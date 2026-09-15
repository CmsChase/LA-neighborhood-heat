"""Strict-forward Los Angeles relative-temperature accuracy development."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import platform
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from la_heat.modeling import CompleteFeatureValidator  # noqa: E402
from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    B1_FEATURES,
    KEY_COLUMNS,
    M3_CANDIDATES,
    STATIC_FEATURES,
    build_b1_estimator,
    build_m3_estimators,
    city_date_row_weights,
)

CONFIG_PATH = Path(__file__).with_name("experiment.toml")
PRIOR_RUN_PATH = ROOT / "experiments" / "accuracy_development" / "run.py"
PRIOR_CONFIG_PATH = PRIOR_RUN_PATH.with_name("experiment.toml")
KEYS = list(KEY_COLUMNS)
GROUPS = ["city_id", "target_date"]
CANDIDATES = ["relative_current_23", "relative_stable_18", "relative_stable_blend"]
BASELINES = ["zero_anomaly", "historical_tract_median", "B1"]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def center(frame: pd.DataFrame, values) -> np.ndarray:
    table = frame[GROUPS].reset_index(drop=True).copy()
    table["value"] = np.asarray(values, dtype=float)
    return (table.value - table.groupby(GROUPS).value.transform("median")).to_numpy()


def year_mask(frame: pd.DataFrame, years: list[int] | tuple[int, ...]) -> pd.Series:
    return pd.to_datetime(frame.target_date, errors="raise").dt.year.isin(years)


def forward_plan(available_years: list[int], outer_years: list[int]) -> list[dict]:
    plan = []
    for outer_year in outer_years:
        inner_year = outer_year - 1
        inner_train = [year for year in available_years if year < inner_year]
        outer_train = [year for year in available_years if year < outer_year]
        if not inner_train or inner_year not in available_years or not outer_train:
            raise ValueError(f"Incomplete forward split for {outer_year}")
        plan.append(
            {
                "outer_test_year": outer_year,
                "inner_validation_year": inner_year,
                "inner_train_years": inner_train,
                "outer_train_years": outer_train,
            }
        )
    return plan


def build_stable_estimator(config: dict) -> Pipeline:
    model = config["models"]
    return Pipeline(
        [
            ("validate", CompleteFeatureValidator()),
            (
                "model",
                HistGradientBoostingRegressor(
                    loss=model["loss"],
                    max_iter=int(model["max_iter"]),
                    learning_rate=float(model["learning_rate"]),
                    max_leaf_nodes=int(model["max_leaf_nodes"]),
                    min_samples_leaf=int(model["min_samples_leaf"]),
                    l2_regularization=float(model["l2_regularization"]),
                    early_stopping=bool(model["early_stopping"]),
                    random_state=int(config["experiment"]["seed"]),
                ),
            ),
        ]
    )


def fit_models(frame: pd.DataFrame, target: pd.Series, config: dict) -> dict:
    """Fit only on the supplied years; targets never enter predictor matrices."""
    weights = city_date_row_weights(frame)
    anomaly = center(frame, target)
    current = build_m3_estimators(M3_CANDIDATES[-1])[1]
    parameters = {
        f"model__{name}": value
        for name, value in config["models"].items()
        if name != "stable_blend_current_weight"
    }
    parameters["model__random_state"] = int(config["experiment"]["seed"])
    current.set_params(**parameters)
    stable = build_stable_estimator(config)
    b1 = build_b1_estimator()
    current.fit(
        frame.loc[:, list(ANOMALY_FEATURES)], anomaly, model__sample_weight=weights
    )
    stable.fit(
        frame.loc[:, list(STATIC_FEATURES)], anomaly, model__sample_weight=weights
    )
    b1.fit(frame.loc[:, list(B1_FEATURES)], target, model__sample_weight=weights)
    history = (
        pd.DataFrame({"tract_geoid": frame.tract_geoid.astype(str), "anomaly": anomaly})
        .groupby("tract_geoid", observed=True)
        .anomaly.median()
    )
    return {"current": current, "stable": stable, "B1": b1, "history": history}


def predict_models(models: dict, universe: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Predict complete held-year support before joining any held target."""
    result = universe[KEYS].reset_index(drop=True).copy()
    b1_absolute = models["B1"].predict(universe.loc[:, list(B1_FEATURES)])
    b1_relative = center(universe, b1_absolute)
    level = np.asarray(b1_absolute) - b1_relative
    current = center(
        universe,
        models["current"].predict(universe.loc[:, list(ANOMALY_FEATURES)]),
    )
    stable = center(
        universe,
        models["stable"].predict(universe.loc[:, list(STATIC_FEATURES)]),
    )
    weight = float(config["models"]["stable_blend_current_weight"])
    blend = center(universe, weight * current + (1.0 - weight) * stable)
    history = universe.tract_geoid.astype(str).map(models["history"]).fillna(0.0)
    history = center(universe, history)
    relative = {
        "relative_current_23": current,
        "relative_stable_18": stable,
        "relative_stable_blend": blend,
        "zero_anomaly": np.zeros(len(universe), dtype=float),
        "historical_tract_median": history,
        "B1": b1_relative,
    }
    for name, values in relative.items():
        result[f"{name}_relative_support"] = values
        result[f"{name}_absolute"] = (
            b1_absolute if name == "B1" else level + np.asarray(values)
        )
    numeric = result.drop(columns=KEYS).to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("Nonfinite held predictions")
    return result


def score_rows(prediction: pd.DataFrame, observed: pd.DataFrame) -> pd.DataFrame:
    result = observed.merge(prediction, on=KEYS, how="left", validate="one_to_one")
    if result.drop(columns=[*KEYS, "observed", "spatial_block"]).isna().any().any():
        raise ValueError("Missing held prediction")
    result["observed_relative"] = center(result, result.observed)
    result["zero_error"] = np.abs(result.observed_relative)
    for name in [*CANDIDATES, *BASELINES]:
        support = result[f"{name}_relative_support"].to_numpy(dtype=float)
        scored = center(result, support)
        result[f"{name}_relative_scored"] = scored
        result[f"{name}_error"] = np.abs(scored - result.observed_relative)
        result[f"{name}_support_error"] = np.abs(support - result.observed_relative)
        result[f"{name}_absolute_error"] = np.abs(
            result[f"{name}_absolute"] - result.observed
        )
    return result


def mean_by_date(frame: pd.DataFrame, column: str) -> float:
    return float(frame.groupby("target_date", observed=True)[column].mean().mean())


def choose(rows: pd.DataFrame) -> tuple[str, dict[str, float]]:
    scores = {name: mean_by_date(rows, f"{name}_error") for name in CANDIDATES}
    return min(CANDIDATES, key=lambda name: scores[name]), scores


def ranked_hotspot(geoids, observed, predicted, fraction: float) -> tuple[float, float]:
    geoids = np.asarray(geoids, dtype=str)
    observed = np.asarray(observed, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    count = max(1, math.ceil(fraction * len(observed)))
    truth_order = np.lexsort((geoids, -observed))
    predicted_order = np.lexsort((geoids, -predicted))
    truth = set(truth_order[:count].tolist())
    hits = np.asarray([index in truth for index in predicted_order], dtype=bool)
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    average_precision = float(precision[hits].sum() / count)
    recall = float(len(truth.intersection(predicted_order[:count])) / count)
    return average_precision, recall


def model_metrics(rows: pd.DataFrame, name: str, hotspot_fraction: float) -> dict:
    dates = []
    for _, frame in rows.groupby("target_date", sort=True, observed=True):
        prediction = frame[f"{name}_relative_support"].to_numpy(dtype=float)
        observed = frame.observed_relative.to_numpy(dtype=float)
        average_precision, recall = ranked_hotspot(
            frame.tract_geoid, observed, prediction, hotspot_fraction
        )
        spearman = pd.Series(observed).corr(pd.Series(prediction), method="spearman")
        dates.append(
            {
                "relative_mae_c": float(frame[f"{name}_error"].mean()),
                "support_centered_relative_mae_c": float(
                    frame[f"{name}_support_error"].mean()
                ),
                "absolute_mae_c": float(frame[f"{name}_absolute_error"].mean()),
                "hotspot_average_precision": average_precision,
                "hotspot_recall": recall,
                "spearman": None if not np.isfinite(spearman) else float(spearman),
            }
        )
    table = pd.DataFrame(dates)
    finite_spearman = table.spearman.dropna()
    return {
        "model": name,
        "rows": int(len(rows)),
        "dates": int(rows.target_date.nunique()),
        "blocks": int(rows.spatial_block.nunique()),
        "relative_mae_c": float(table.relative_mae_c.mean()),
        "support_centered_relative_mae_c": float(
            table.support_centered_relative_mae_c.mean()
        ),
        "absolute_mae_c": float(table.absolute_mae_c.mean()),
        "hotspot_average_precision": float(table.hotspot_average_precision.mean()),
        "hotspot_recall": float(table.hotspot_recall.mean()),
        "median_date_spearman": (
            None if finite_spearman.empty else float(finite_spearman.median())
        ),
    }


def crossed_bootstrap(
    rows: pd.DataFrame, comparator: str, selected: str, iterations: int, seed: int
) -> dict:
    rng = np.random.default_rng(seed)
    work = rows.copy()
    work["gain"] = work[f"{comparator}_error"] - work[f"{selected}_error"]
    aggregate = work.groupby(["target_date", "spatial_block"]).gain.agg(["sum", "count"])
    sums = aggregate["sum"].unstack(fill_value=0).to_numpy()
    counts = aggregate["count"].unstack(fill_value=0).to_numpy()
    ndate, nblock = counts.shape
    replicates = []
    for _ in range(iterations):
        block_weights = rng.multinomial(nblock, np.full(nblock, 1 / nblock))
        denominator = counts @ block_weights
        per_date = np.divide(
            sums @ block_weights,
            denominator,
            out=np.full(ndate, np.nan),
            where=denominator > 0,
        )
        sample = per_date[rng.integers(ndate, size=ndate)]
        replicates.append(float(np.nanmean(sample)))
    return {
        "lower_c": float(np.quantile(replicates, 0.025)),
        "upper_c": float(np.quantile(replicates, 0.975)),
        "scope": "Los Angeles outer test dates x fixed 5 km spatial blocks",
    }


def descriptive_stability(observed: pd.DataFrame) -> dict:
    work = observed.copy()
    work["anomaly"] = center(work, work.observed)
    pivot = work.pivot(index="target_date", columns="tract_geoid", values="anomaly")
    correlations = []
    for left in range(len(pivot)):
        for right in range(left + 1, len(pivot)):
            pair = pivot.iloc[[left, right]].dropna(axis=1)
            if pair.shape[1] >= 20:
                value = pair.iloc[0].corr(pair.iloc[1], method="spearman")
                if np.isfinite(value):
                    correlations.append(float(value))
    tract_median = work.groupby("tract_geoid", observed=True).anomaly.median()
    mapped = work.tract_geoid.map(tract_median)
    return {
        "role": "descriptive_all_2020_2024_not_validation",
        "date_pair_count": len(correlations),
        "median_pairwise_date_spearman": float(np.median(correlations)),
        "target_anomaly_variance_c2": float(np.var(work.anomaly)),
        "stable_tract_median_variance_c2": float(np.var(mapped)),
        "residual_after_stable_tract_median_mae_c": float(
            np.mean(np.abs(work.anomaly - mapped))
        ),
    }


def load_inputs(config: dict):
    prior = load_module("prior_accuracy_inputs", PRIOR_RUN_PATH)
    with PRIOR_CONFIG_PATH.open("rb") as handle:
        prior_config = tomllib.load(handle)
    data, observed, records = prior.load_inputs(prior_config)
    city = config["experiment"]["city_id"]
    scored_mask = data.scored_frame.city_id.eq(city)
    universe_mask = data.prediction_universe.city_id.eq(city)
    scored = data.scored_frame.loc[scored_mask].reset_index(drop=True)
    target = data.target.loc[scored_mask].reset_index(drop=True)
    universe = data.prediction_universe.loc[universe_mask].reset_index(drop=True)
    observed = observed.loc[observed.city_id.eq(city)].reset_index(drop=True)
    years = sorted(pd.to_datetime(scored.target_date).dt.year.unique().tolist())
    if years != list(config["experiment"]["allowed_years"]):
        raise ValueError(f"LA year boundary changed: {years}")
    if any(pd.to_datetime(universe.target_date).dt.year > max(years)):
        raise ValueError("Future or LA-2025 predictor rows entered the experiment")
    if set(scored.city_id) != {city} or set(universe.city_id) != {city}:
        raise ValueError("Non-LA city entered local experiment")
    return scored, target, universe, observed, records


def held_rows(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    return frame.loc[year_mask(frame, [year])].reset_index(drop=True)


def fit_predict_year(
    scored: pd.DataFrame,
    target: pd.Series,
    universe: pd.DataFrame,
    observed: pd.DataFrame,
    train_years: list[int],
    held_year: int,
    config: dict,
) -> tuple[pd.DataFrame, dict]:
    train_mask = year_mask(scored, train_years)
    training = scored.loc[train_mask].reset_index(drop=True)
    training_target = target.loc[train_mask].reset_index(drop=True)
    if training.empty or max(train_years) >= held_year:
        raise ValueError("Forward training boundary failed")
    models = fit_models(training, training_target, config)
    prediction = predict_models(models, held_rows(universe, held_year), config)
    scored_rows = score_rows(prediction, held_rows(observed, held_year))
    return scored_rows, models


def main() -> None:
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    if config["experiment"]["candidate_ids"] != CANDIDATES:
        raise ValueError("Candidate contract changed")
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)

    def status(state: str, **details) -> None:
        write_json(
            output / "status.json",
            {"state": state, "updated_utc": datetime.now(UTC).isoformat(), **details},
        )
        print(state, json.dumps(details), flush=True)

    status("loading")
    scored, target, universe, observed, records = load_inputs(config)
    plan = forward_plan(
        list(config["experiment"]["allowed_years"]),
        list(config["experiment"]["outer_test_years"]),
    )
    code_paths = [
        Path(__file__),
        CONFIG_PATH,
        PRIOR_RUN_PATH,
        ROOT / "src/la_heat/modeling.py",
        ROOT / "src/la_heat/multicity/m3_development.py",
    ]
    provenance = {
        "inputs": records,
        "config": config,
        "code": {str(path.relative_to(ROOT)): digest(path) for path in code_paths},
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    signature = hashlib.sha256(
        json.dumps(provenance, sort_keys=True).encode("utf-8")
    ).hexdigest()
    write_json(output / "provenance.json", {"signature": signature, **provenance})

    outer_frames = []
    selections = []
    with threadpool_limits(limits=int(config["experiment"]["threads"])):
        for index, fold in enumerate(plan, start=1):
            status("inner_validation", fold=index, total=len(plan), **fold)
            inner, _ = fit_predict_year(
                scored,
                target,
                universe,
                observed,
                fold["inner_train_years"],
                fold["inner_validation_year"],
                config,
            )
            selected, scores = choose(inner)
            status("outer_test", fold=index, selected=selected, **fold)
            outer, _ = fit_predict_year(
                scored,
                target,
                universe,
                observed,
                fold["outer_train_years"],
                fold["outer_test_year"],
                config,
            )
            for suffix in (
                "_relative_support",
                "_absolute",
                "_relative_scored",
                "_error",
                "_support_error",
                "_absolute_error",
            ):
                outer["selected" + suffix] = outer[selected + suffix]
            outer["selected_candidate"] = selected
            outer["held_year"] = int(fold["outer_test_year"])
            outer_frames.append(outer)
            selections.append({**fold, "selected": selected, "inner_scores": scores})

        rows = pd.concat(outer_frames, ignore_index=True)
        rows.to_parquet(output / "forward_oof.parquet", index=False)
        names = [*CANDIDATES, *BASELINES, "selected"]
        metrics = [
            model_metrics(subset, name, float(config["experiment"]["hotspot_fraction"]))
            | {"held_year": label}
            for label, subset in [("all", rows), *list(rows.groupby("held_year"))]
            for name in names
        ]
        metrics_table = pd.DataFrame(metrics)
        metrics_table.to_csv(output / "metrics.csv", index=False)
        aggregate = metrics_table.loc[metrics_table.held_year.eq("all")].set_index("model")
        comparator = config["experiment"]["primary_comparator"]
        improvement = 1.0 - float(aggregate.loc["selected", "relative_mae_c"]) / float(
            aggregate.loc[comparator, "relative_mae_c"]
        )
        bootstrap = crossed_bootstrap(
            rows,
            comparator,
            "selected",
            int(config["promotion"]["bootstrap_iterations"]),
            int(config["experiment"]["seed"]),
        )
        per_year = metrics_table.loc[metrics_table.held_year.ne("all")].pivot(
            index="held_year", columns="model", values="relative_mae_c"
        )
        checks = {
            "minimum_relative_mae_improvement": improvement
            >= float(config["promotion"]["minimum_relative_mae_improvement"]),
            "maximum_test_year_mae_degradation": float(
                (per_year["selected"] - per_year[comparator]).max()
            )
            <= float(config["promotion"]["maximum_test_year_mae_degradation_c"]),
            "support_centered_mae_not_degraded": float(
                aggregate.loc["selected", "support_centered_relative_mae_c"]
                - aggregate.loc[comparator, "support_centered_relative_mae_c"]
            )
            <= float(config["promotion"]["maximum_support_centered_mae_degradation_c"]),
            "absolute_mae_not_degraded": float(
                aggregate.loc["selected", "absolute_mae_c"]
                - aggregate.loc[comparator, "absolute_mae_c"]
            )
            <= float(config["promotion"]["maximum_absolute_mae_degradation_c"]),
            "hotspot_recall_not_degraded": float(
                aggregate.loc[comparator, "hotspot_recall"]
                - aggregate.loc["selected", "hotspot_recall"]
            )
            <= float(config["promotion"]["maximum_hotspot_recall_degradation"]),
            "positive_bootstrap_lower_bound": bootstrap["lower_c"] > 0,
        }

        final_validation_year = max(config["experiment"]["allowed_years"])
        final_train_years = [
            year
            for year in config["experiment"]["allowed_years"]
            if year < final_validation_year
        ]
        final_validation, _ = fit_predict_year(
            scored,
            target,
            universe,
            observed,
            final_train_years,
            final_validation_year,
            config,
        )
        final_candidate, final_scores = choose(final_validation)
        status("fitting_full_history_artifact", candidate=final_candidate)
        final_models = fit_models(scored, target, config)
        bundle = {
            "models": final_models,
            "candidate": final_candidate,
            "signature": signature,
            "promotion_passed": all(checks.values()),
            "role": config["experiment"]["role"],
            "absolute_level": "B1 complete-support city-date median",
        }
        model_path = output / "development_model.joblib"
        joblib.dump(bundle, model_path)
        reloaded = joblib.load(model_path)
        sample = universe.loc[year_mask(universe, [final_validation_year])].iloc[:1000]
        pd.testing.assert_frame_equal(
            predict_models(final_models, sample, config),
            predict_models(reloaded["models"], sample, config),
        )

    support_gap = {
        name: float(
            aggregate.loc[name, "support_centered_relative_mae_c"]
            - aggregate.loc[name, "relative_mae_c"]
        )
        for name in names
    }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment"]["id"],
        "role": config["experiment"]["role"],
        "completed_at": datetime.now(UTC).isoformat(),
        "signature": signature,
        "city_id": config["experiment"]["city_id"],
        "no_la_2025_or_external_city_targets_used": True,
        "forward_plan": plan,
        "outer_selections": selections,
        "relative_improvement": improvement,
        "bootstrap": bootstrap,
        "promotion_checks": checks,
        "promotion_passed": all(checks.values()),
        "support_centering_mae_gap_c": support_gap,
        "stability_diagnostic": descriptive_stability(observed),
        "training_safe_history_baseline_gain_vs_zero_c": float(
            aggregate.loc["zero_anomaly", "relative_mae_c"]
            - aggregate.loc["historical_tract_median", "relative_mae_c"]
        ),
        "final_development_candidate": final_candidate,
        "final_candidate_validation_scores_not_outer_evidence": final_scores,
        "metrics": metrics,
        "model_sha256": digest(model_path),
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "model_metadata.json",
        {key: value for key, value in bundle.items() if key != "models"}
        | {"sha256": digest(model_path)},
    )
    status(
        "complete",
        promotion_passed=summary["promotion_passed"],
        relative_improvement=improvement,
        candidate=final_candidate,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        with CONFIG_PATH.open("rb") as handle:
            failed_config = tomllib.load(handle)
        write_json(
            ROOT / failed_config["outputs"]["directory"] / "status.json",
            {
                "state": "failed",
                "updated_utc": datetime.now(UTC).isoformat(),
                "error": repr(error),
            },
        )
        raise
