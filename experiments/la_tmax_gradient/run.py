"""One fixed LA d-1 Tmax-gradient candidate under strict-forward validation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("experiment.toml")
PRIOR_RUN_PATH = ROOT / "experiments" / "la_local_accuracy" / "run.py"
PRIOR_CONFIG_PATH = PRIOR_RUN_PATH.with_name("experiment.toml")
COMPARATOR = "relative_current_23"
CANDIDATE = "relative_current_23_plus_dminus1_tmax_gradient"
TMAX = "daymet_tmax_c_mean_prev_1d"
GRADIENT = "daymet_tmax_c_mean_prev_1d_city_gradient"


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


def add_complete_support_gradient(
    scored: pd.DataFrame,
    universe: pd.DataFrame,
    keys: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Center target-free d-1 Tmax over every fixed prediction tract per date."""
    groups = ["city_id", "target_date"]
    support = universe.loc[:, [*keys, TMAX]].copy()
    if support.duplicated(keys).any() or support[TMAX].isna().any():
        raise ValueError("Fixed prediction support Tmax is incomplete or duplicated")
    counts = support.groupby(groups, observed=True).size()
    if counts.nunique() != 1 or int(counts.iloc[0]) != 1096:
        raise ValueError("Gradient must use the fixed 1096-tract LA support")
    support[GRADIENT] = support[TMAX] - support.groupby(groups, observed=True)[
        TMAX
    ].transform("median")
    medians = support.groupby(groups, observed=True)[GRADIENT].median()
    if not np.allclose(medians.to_numpy(dtype=float), 0.0, atol=1e-12):
        raise ValueError("Complete-support Tmax gradient is not date-centered")
    gradient = support.loc[:, [*keys, GRADIENT]]
    enriched_universe = universe.merge(
        gradient, on=keys, how="left", validate="one_to_one"
    )
    enriched_scored = scored.merge(
        gradient, on=keys, how="left", validate="one_to_one"
    )
    if enriched_scored[GRADIENT].isna().any():
        raise ValueError("Scored rows are not a subset of the fixed prediction support")
    metadata = {
        "definition": (
            "tract d-1 Tmax minus same-date median over all 1096 fixed LA "
            "prediction tracts"
        ),
        "prediction_dates": int(counts.size),
        "tracts_per_date": int(counts.iloc[0]),
        "source_missing_values": int(support[TMAX].isna().sum()),
        "gradient_missing_values": int(support[GRADIENT].isna().sum()),
        "maximum_absolute_date_median": float(medians.abs().max()),
        "scored_subset_used_for_centering": False,
        "target_or_qa_used_for_centering": False,
        "is_spatial_derivative": False,
        "imputation_applied": False,
    }
    return enriched_scored, enriched_universe, metadata


def audit_daymet_timing(path: Path, universe: pd.DataFrame, keys: list[str]) -> dict:
    audit = pd.read_parquet(path)
    audit["target_date"] = pd.to_datetime(audit.target_date, errors="raise")
    target = audit.target_date
    start = pd.to_datetime(audit.daymet_source_start_date_prev_1d, errors="raise")
    end = pd.to_datetime(audit.daymet_source_end_date_prev_1d, errors="raise")
    expected = target - pd.Timedelta(days=1)
    audit_keys = audit.assign(target_date=target.dt.strftime("%Y-%m-%d")).loc[
        :, ["tract_geoid", "target_date"]
    ]
    universe_keys = universe.assign(
        target_date=pd.to_datetime(universe.target_date).dt.strftime("%Y-%m-%d")
    ).loc[:, ["tract_geoid", "target_date"]]
    key_match = (
        len(audit_keys) == len(universe_keys)
        and not audit_keys.duplicated().any()
        and not universe_keys.duplicated().any()
        and audit_keys.merge(
            universe_keys, how="outer", indicator=True, validate="one_to_one"
        )._merge.eq("both").all()
    )
    checks = {
        "audit_key_matches_fixed_prediction_universe": bool(key_match),
        "source_start_equals_target_minus_one_day": bool(start.eq(expected).all()),
        "source_end_equals_target_minus_one_day": bool(end.eq(expected).all()),
        "one_day_expected": bool(
            audit.daymet_source_days_expected_prev_1d.eq(1).all()
        ),
        "one_day_complete": bool(
            audit.daymet_source_days_complete_prev_1d.eq(1).all()
        ),
        "all_primary_windows_complete": bool(
            audit.daymet_all_primary_windows_complete.all()
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Daymet d-1 timing audit failed: {checks}")
    publication_columns = [
        column
        for column in audit
        if "publish" in column.lower() or "release" in column.lower()
    ]
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": digest(path),
        "rows": len(audit),
        "dates": int(audit.target_date.nunique()),
        "checks": checks,
        "publication_timestamp_columns": publication_columns,
        "publication_before_target_proven": False,
        "allowed_claim": "historical hindcast reconstruction only",
    }


def fit_candidate(prior, base_model, training: pd.DataFrame, target: pd.Series):
    features = [*prior.ANOMALY_FEATURES, GRADIENT]
    if len(prior.ANOMALY_FEATURES) != 23 or len(features) != 24:
        raise ValueError("The fixed 23-to-24 feature contract changed")
    if training[GRADIENT].isna().any() or np.isinf(training[GRADIENT]).any():
        raise ValueError("The added complete-support gradient is not finite")
    model = clone(base_model)
    preprocess = model.named_steps["preprocess"]
    dynamic_features = [
        feature
        for feature in prior.ANOMALY_FEATURES
        if feature not in prior.STATIC_FEATURES
    ]
    updated = []
    for name, transformer, columns in preprocess.transformers:
        columns = list(columns)
        if name == "complete":
            if columns != list(prior.STATIC_FEATURES):
                raise ValueError("Current complete-feature branch changed")
            columns.append(GRADIENT)
        elif name == "dynamic" and columns != dynamic_features:
            raise ValueError("Current train-fold dynamic imputation branch changed")
        updated.append((name, transformer, columns))
    preprocess.transformers = updated
    weights = prior.city_date_row_weights(training)
    anomaly = prior.center(training, target)
    model.fit(training.loc[:, features], anomaly, model__sample_weight=weights)
    return model


def add_candidate_predictions(
    prior,
    base_rows: pd.DataFrame,
    base_models: dict,
    candidate_model,
    held_universe: pd.DataFrame,
    prior_config: dict,
) -> pd.DataFrame:
    features = [*prior.ANOMALY_FEATURES, GRADIENT]
    complete = prior.predict_models(base_models, held_universe, prior_config)
    candidate_support = prior.center(
        held_universe,
        candidate_model.predict(held_universe.loc[:, features]),
    )
    level = (
        complete[f"{COMPARATOR}_absolute"].to_numpy(dtype=float)
        - complete[f"{COMPARATOR}_relative_support"].to_numpy(dtype=float)
    )
    candidate = complete.loc[:, prior.KEYS].copy()
    candidate[f"{CANDIDATE}_relative_support"] = candidate_support
    candidate[f"{CANDIDATE}_absolute"] = level + candidate_support
    result = base_rows.merge(candidate, on=prior.KEYS, validate="one_to_one")
    result[f"{CANDIDATE}_relative_scored"] = prior.center(
        result, result[f"{CANDIDATE}_relative_support"]
    )
    result[f"{CANDIDATE}_error"] = np.abs(
        result[f"{CANDIDATE}_relative_scored"] - result.observed_relative
    )
    result[f"{CANDIDATE}_support_error"] = np.abs(
        result[f"{CANDIDATE}_relative_support"] - result.observed_relative
    )
    result[f"{CANDIDATE}_absolute_error"] = np.abs(
        result[f"{CANDIDATE}_absolute"] - result.observed
    )
    return result


def promotion_checks(metrics: pd.DataFrame, bootstrap: dict, config: dict) -> tuple[dict, float]:
    overall = metrics.loc[metrics.held_year.eq("all")].set_index("model")
    improvement = 1.0 - float(overall.loc[CANDIDATE, "relative_mae_c"]) / float(
        overall.loc[COMPARATOR, "relative_mae_c"]
    )
    yearly = metrics.loc[metrics.held_year.ne("all")].pivot(
        index="held_year", columns="model", values="relative_mae_c"
    )
    contract = config["promotion"]
    checks = {
        "minimum_relative_mae_improvement": improvement
        >= float(contract["minimum_relative_mae_improvement"]),
        "maximum_test_year_mae_degradation": float(
            (yearly[CANDIDATE] - yearly[COMPARATOR]).max()
        )
        <= float(contract["maximum_test_year_mae_degradation_c"]),
        "support_centered_mae_not_degraded": float(
            overall.loc[CANDIDATE, "support_centered_relative_mae_c"]
            - overall.loc[COMPARATOR, "support_centered_relative_mae_c"]
        )
        <= float(contract["maximum_support_centered_mae_degradation_c"]),
        "absolute_mae_not_degraded": float(
            overall.loc[CANDIDATE, "absolute_mae_c"]
            - overall.loc[COMPARATOR, "absolute_mae_c"]
        )
        <= float(contract["maximum_absolute_mae_degradation_c"]),
        "hotspot_recall_not_degraded": float(
            overall.loc[COMPARATOR, "hotspot_recall"]
            - overall.loc[CANDIDATE, "hotspot_recall"]
        )
        <= float(contract["maximum_hotspot_recall_degradation"]),
        "positive_crossed_bootstrap_lower_bound": bootstrap["lower_c"] > 0,
    }
    return checks, improvement


def main() -> None:
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    with PRIOR_CONFIG_PATH.open("rb") as handle:
        prior_config = tomllib.load(handle)
    if config["experiment"]["candidate_id"] != CANDIDATE:
        raise ValueError("Single-candidate contract changed")
    prior = load_module("la_tmax_gradient_prior", PRIOR_RUN_PATH)
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)

    def status(state: str, **details) -> None:
        write_json(
            output / "status.json",
            {"state": state, "updated_at": datetime.now(UTC).isoformat(), **details},
        )
        print(state, json.dumps(details), flush=True)

    status("loading")
    scored, target, universe, observed, input_records = prior.load_inputs(prior_config)
    scored, universe, gradient_metadata = add_complete_support_gradient(
        scored, universe, list(prior.KEYS)
    )
    daymet_path = ROOT / config["inputs"]["daymet_audit"]
    timing = audit_daymet_timing(daymet_path, universe, list(prior.KEYS))
    plan = prior.forward_plan(
        list(config["experiment"]["allowed_years"]),
        list(config["experiment"]["outer_test_years"]),
    )
    provenance = {
        "inputs": input_records,
        "gradient": gradient_metadata,
        "timing": timing,
        "config": config,
        "code": {
            str(path.relative_to(ROOT)): digest(path)
            for path in [Path(__file__), CONFIG_PATH, PRIOR_RUN_PATH]
        },
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

    frames = []
    with threadpool_limits(limits=int(config["experiment"]["threads"])):
        for index, fold in enumerate(plan, start=1):
            status("outer_test", fold=index, total=len(plan), **fold)
            train_mask = prior.year_mask(scored, fold["outer_train_years"])
            training = scored.loc[train_mask].reset_index(drop=True)
            training_target = target.loc[train_mask].reset_index(drop=True)
            base_rows, base_models = prior.fit_predict_year(
                scored,
                target,
                universe,
                observed,
                fold["outer_train_years"],
                fold["outer_test_year"],
                prior_config,
            )
            candidate_model = fit_candidate(
                prior, base_models["current"], training, training_target
            )
            held_universe = prior.held_rows(universe, fold["outer_test_year"])
            rows = add_candidate_predictions(
                prior,
                base_rows,
                base_models,
                candidate_model,
                held_universe,
                prior_config,
            )
            rows["held_year"] = int(fold["outer_test_year"])
            frames.append(rows)

        oof = pd.concat(frames, ignore_index=True)
        oof.to_parquet(output / "forward_oof.parquet", index=False)
        metrics = [
            prior.model_metrics(
                subset, name, float(config["experiment"]["hotspot_fraction"])
            )
            | {"held_year": label}
            for label, subset in [("all", oof), *list(oof.groupby("held_year"))]
            for name in [COMPARATOR, CANDIDATE]
        ]
        metrics_table = pd.DataFrame(metrics)
        metrics_table.to_csv(output / "metrics.csv", index=False)
        bootstrap = prior.crossed_bootstrap(
            oof,
            COMPARATOR,
            CANDIDATE,
            int(config["promotion"]["bootstrap_iterations"]),
            int(config["experiment"]["seed"]),
        )
        checks, improvement = promotion_checks(metrics_table, bootstrap, config)
        passed = all(checks.values())

        model_metadata = None
        if passed:
            status("fitting_passed_development_candidate")
            full_base = prior.fit_models(scored, target, prior_config)
            full_candidate = fit_candidate(prior, full_base["current"], scored, target)
            model_path = output / "development_candidate.joblib"
            joblib.dump(
                {
                    "model": full_candidate,
                    "features": [*prior.ANOMALY_FEATURES, GRADIENT],
                    "signature": signature,
                    "role": config["experiment"]["role"],
                },
                model_path,
            )
            reloaded = joblib.load(model_path)
            sample = universe.iloc[:1000]
            original = full_candidate.predict(
                sample.loc[:, [*prior.ANOMALY_FEATURES, GRADIENT]]
            )
            restored = reloaded["model"].predict(
                sample.loc[:, reloaded["features"]]
            )
            if not np.array_equal(original, restored):
                raise ValueError("Reloaded candidate predictions changed")
            model_metadata = {
                "path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": digest(model_path),
                "reload_exact": True,
            }

    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment"]["id"],
        "role": config["experiment"]["role"],
        "completed_at": datetime.now(UTC).isoformat(),
        "signature": signature,
        "adaptive_feature_selection_from_same_outer_years": True,
        "independent_confirmation": False,
        "no_la_2025_or_external_city_targets_used": True,
        "no_data_acquisition_or_qa_change": True,
        "forward_plan": plan,
        "gradient": gradient_metadata,
        "time_availability": timing,
        "relative_improvement": improvement,
        "bootstrap": bootstrap,
        "promotion_checks": checks,
        "promotion_passed": passed,
        "decision": "development_candidate_saved" if passed else "route_stopped",
        "current_model_remains_default": not passed,
        "metrics": metrics,
        "model_artifact": model_metadata,
    }
    write_json(output / "summary.json", summary)
    status(
        "complete",
        promotion_passed=passed,
        relative_improvement=improvement,
        decision=summary["decision"],
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
                "updated_at": datetime.now(UTC).isoformat(),
                "error": repr(error),
            },
        )
        raise
