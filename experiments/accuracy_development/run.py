"""Bounded, resumable source-only relative-accuracy experiment."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
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
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    B1_FEATURES,
    KEY_COLUMNS,
    M2_FEATURES,
    M3_CANDIDATES,
    build_b1_estimator,
    build_m2_legacy_estimator,
    build_m3_estimators,
    city_date_row_weights,
)

KEYS = list(KEY_COLUMNS)
GROUPS = ["city_id", "target_date"]
CANDIDATES = ["relative_static", "B1", "relative_context", "relative_blend"]


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def center(frame, values):
    table = frame[GROUPS].reset_index(drop=True).copy()
    table["value"] = np.asarray(values, dtype=float)
    return (table.value - table.groupby(GROUPS).value.transform("median")).to_numpy()


def fit_models(frame, target, config):
    """Only training labels are accepted; estimator inputs use fixed allowlists."""
    weights = city_date_row_weights(frame)
    anomaly = center(frame, target)
    models = {
        "B1": build_b1_estimator(),
        "relative_static": build_m3_estimators(M3_CANDIDATES[-1])[1],
        "relative_context": build_m2_legacy_estimator(),
    }
    features = {
        "B1": B1_FEATURES,
        "relative_static": ANOMALY_FEATURES,
        "relative_context": M2_FEATURES,
    }
    for name, model in models.items():
        if name != "B1":
            parameters = {f"model__{key}": value for key, value in config["models"].items()}
            # Preserve the established relative-model seed in both HGB branches.
            parameters["model__random_state"] = models["relative_static"].get_params()[
                "model__random_state"
            ]
            model.set_params(**parameters)
        model.fit(
            frame.loc[:, list(features[name])],
            target if name == "B1" else anomaly,
            model__sample_weight=weights,
        )
    return models


def predict_models(models, universe):
    """Call with complete city-date predictor support, before joining held targets."""
    result = universe[KEYS].copy()
    b1 = models["B1"].predict(universe.loc[:, list(B1_FEATURES)])
    b1_relative = center(universe, b1)
    level = b1 - b1_relative
    static = center(
        universe, models["relative_static"].predict(universe.loc[:, list(ANOMALY_FEATURES)])
    )
    context = center(
        universe, models["relative_context"].predict(universe.loc[:, list(M2_FEATURES)])
    )
    relative = {
        "B1": b1_relative,
        "relative_static": static,
        "relative_context": context,
        "relative_blend": center(universe, (static + context) / 2),
    }
    for name, values in relative.items():
        result[name] = level + values
        result[name + "_relative"] = values
    if not np.isfinite(result.drop(columns=KEYS).to_numpy()).all():
        raise ValueError("Nonfinite predictions")
    return result


def score_rows(prediction, observed):
    result = observed.merge(prediction, on=KEYS, validate="one_to_one", how="left")
    if result[CANDIDATES].isna().any().any():
        raise ValueError("Missing held predictions")
    truth = center(result, result.observed)
    result["zero_error"] = np.abs(truth)
    for name in CANDIDATES:
        relative = center(result, result[name])
        result[name + "_error"] = np.abs(relative - truth)
        result[name + "_support_error"] = np.abs(result[name + "_relative"] - truth)
        result[name + "_absolute_error"] = np.abs(result[name] - result.observed)
    return result


def macro(frame, column):
    return float(frame.groupby(GROUPS)[column].mean().groupby("city_id").mean().mean())


def choose(inner_frames):
    rows = pd.concat(inner_frames, ignore_index=True)
    scores = {name: macro(rows, name + "_error") for name in CANDIDATES}
    return min(CANDIDATES, key=lambda name: scores[name]), scores


def split_plan(cities):
    return [
        (tuple(train), held)
        for size in (2, 3)
        for train in itertools.combinations(cities, size)
        for held in cities
        if held not in train
    ]


def crossed_bootstrap(rows, iterations, seed):
    """Paired dates x blocks within fixed cities, preserving equal-date scoring."""
    rng = np.random.default_rng(seed)
    city_replicates = []
    for _, city in rows.groupby("city_id", sort=True):
        city = city.copy()
        city["gain"] = city.relative_static_error - city.selected_error
        aggregate = city.groupby(["target_date", "spatial_block"]).gain.agg(["sum", "count"])
        sums = aggregate["sum"].unstack(fill_value=0).to_numpy()
        counts = aggregate["count"].unstack(fill_value=0).to_numpy()
        ndate, nblock = counts.shape
        values = []
        for _ in range(iterations):
            block_weights = rng.multinomial(nblock, np.full(nblock, 1 / nblock))
            denominator = counts @ block_weights
            per_date = np.divide(
                sums @ block_weights, denominator, out=np.full(ndate, np.nan), where=denominator > 0
            )
            sampled = per_date[rng.integers(ndate, size=ndate)]
            if not np.isfinite(sampled).any():
                raise ValueError("Bootstrap replicate has no represented observations")
            values.append(float(np.nanmean(sampled)))
        city_replicates.append(values)
    replicates = np.mean(city_replicates, axis=0)
    return {
        "lower_c": float(np.quantile(replicates, 0.025)),
        "upper_c": float(np.quantile(replicates, 0.975)),
        "scope": "fixed four cities; independent whole-date and 5km block resampling",
    }


def load_inputs(config):
    spec = importlib.util.spec_from_file_location("source_2x2", ROOT / "experiments/m3_2x2/run.py")
    source = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = source
    spec.loader.exec_module(source)
    auth = json.loads(
        (
            ROOT / "manifests/multicity/next_experiment/"
            "M3_SOURCE_JOINT_NESTED_LOSO_V1_AUTHORIZATION.json"
        ).read_text()
    )
    records = auth["source_predictor_tables"] + [
        row for row in auth["source_qa_target_tables"] if row["qa_id"] == "4k"
    ]
    block_manifest = json.loads(
        (ROOT / "manifests/multicity/evaluation/SPATIAL_BLOCKS.json").read_text()
    )
    records.append(block_manifest["output"])
    for record in records:
        if digest(ROOT / record["path"]) != record["sha256"]:
            raise ValueError(f"Changed authorized input: {record['path']}")
    data, _ = source.load_source_data(ROOT, source.load_config())
    allowed = set(config["experiment"]["source_cities"])
    for frame in (data.prediction_universe, data.scored_frame):
        if set(frame.city_id) != allowed or frame.duplicated(KEYS).any():
            raise ValueError("Source city/key boundary failed")
        if ((frame.city_id == "los_angeles_ca") & frame.target_date.str.startswith("2025")).any():
            raise ValueError("LA 2025 is frozen")
    observed = data.scored_frame[KEYS].copy()
    observed["observed"] = data.target.to_numpy()
    blocks = pd.read_parquet(ROOT / block_manifest["output"]["path"])
    blocks["tract_geoid"] = blocks.tract_geoid.astype(str).str.zfill(11)
    observed = observed.merge(
        blocks[["city_id", "tract_geoid", "spatial_block"]],
        on=["city_id", "tract_geoid"],
        validate="many_to_one",
    )
    if len(observed) != len(data.target) or observed.spatial_block.isna().any():
        raise ValueError("Incomplete block assignments")
    if not np.isfinite(observed.observed).all():
        raise ValueError("Nonfinite source targets")
    return data, observed, records


def main():
    config_path = Path(__file__).with_name("experiment.toml")
    config = tomllib.loads(config_path.read_text())
    if config["experiment"]["candidate_ids"] != CANDIDATES:
        raise ValueError("Candidate contract mismatch")
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)

    def status(state, **details):
        write_json(
            output / "status.json",
            {"state": state, "updated_utc": datetime.now(UTC).isoformat(), **details},
        )
        print(state, json.dumps(details), flush=True)

    status("loading")
    data, observed, records = load_inputs(config)
    code_paths = [
        Path(__file__),
        config_path,
        ROOT / "experiments/m3_2x2/run.py",
        ROOT / "experiments/m3_2x2/experiment.toml",
        ROOT / "src/la_heat/modeling.py",
        ROOT / "src/la_heat/multicity/m3_development.py",
    ]
    provenance = {
        "inputs": records,
        "config": config,
        "code": {str(path.relative_to(ROOT)): digest(path) for path in code_paths},
        "versions": {
            "python": platform.python_version(),
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    signature = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    write_json(output / "provenance.json", {"signature": signature, **provenance})
    cities = sorted(config["experiment"]["source_cities"])
    split_results = {}
    with threadpool_limits(limits=config["experiment"]["threads"]):
        for index, (train, held) in enumerate(split_plan(cities)):
            name = "__".join(train) + "--" + held
            path = output / "checkpoints" / (name + ".parquet")
            metadata = path.with_suffix(".json")
            universe = data.prediction_universe.loc[data.prediction_universe.city_id == held]
            cached = False
            if path.exists() and metadata.exists():
                record = json.loads(metadata.read_text())
                cached = record["signature"] == signature and record["sha256"] == digest(path)
            status("running", split=index + 1, total=16, train=train, held=held, cached=cached)
            if cached:
                prediction = pd.read_parquet(path)
            else:
                mask = data.scored_frame.city_id.isin(train)
                models = fit_models(
                    data.scored_frame.loc[mask].reset_index(drop=True),
                    data.target.loc[mask].reset_index(drop=True),
                    config,
                )
                prediction = predict_models(models, universe)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".parquet.tmp")
                prediction.to_parquet(temporary, index=False)
                temporary.replace(path)
                write_json(metadata, {"signature": signature, "sha256": digest(path)})
            pd.testing.assert_frame_equal(
                prediction[KEYS].reset_index(drop=True), universe[KEYS].reset_index(drop=True)
            )
            split_results[(train, held)] = score_rows(
                prediction, observed.loc[observed.city_id == held]
            )
        selections, outer = [], []
        for held in cities:
            train = tuple(city for city in cities if city != held)
            inner = [
                split_results[(tuple(city for city in train if city != inner_held), inner_held)]
                for inner_held in train
            ]
            selected, scores = choose(inner)
            rows = split_results[(train, held)].copy()
            for suffix in ("", "_relative", "_error", "_support_error", "_absolute_error"):
                rows["selected" + suffix] = rows[selected + suffix]
            rows["selected_candidate"] = selected
            outer.append(rows)
            selections.append({"held_city": held, "selected": selected, "inner_scores": scores})
        rows = pd.concat(outer, ignore_index=True)
        rows.to_parquet(output / "nested_oof.parquet", index=False)
        metrics = []
        for city, subset in [("equal_city", rows), *list(rows.groupby("city_id"))]:
            for name in [*CANDIDATES, "selected"]:
                metrics.append(
                    {
                        "city": city,
                        "model": name,
                        "rows": len(subset),
                        "dates": len(subset[GROUPS].drop_duplicates()),
                        "blocks": subset.spatial_block.nunique(),
                        "anomaly_mae_c": macro(subset, name + "_error"),
                        "support_sensitivity_mae_c": macro(subset, name + "_support_error"),
                        "absolute_mae_c": macro(subset, name + "_absolute_error"),
                        "zero_anomaly_mae_c": macro(subset, "zero_error"),
                    }
                )
        table = pd.DataFrame(metrics)
        table.to_csv(output / "metrics.csv", index=False)
        bootstrap = crossed_bootstrap(
            rows, config["promotion"]["bootstrap_iterations"], config["experiment"]["seed"]
        )
        gain = 1 - macro(rows, "selected_error") / macro(rows, "relative_static_error")
        city_gain = {
            city: macro(group, "relative_static_error") - macro(group, "selected_error")
            for city, group in rows.groupby("city_id")
        }
        gate = (
            gain >= config["promotion"]["minimum_relative_mae_improvement"]
            and min(city_gain.values()) >= -config["promotion"]["maximum_city_mae_degradation_c"]
            and bootstrap["lower_c"] > 0
        )
        winner, scores = choose(outer)
        summary = {
            "role": config["experiment"]["role"],
            "signature": signature,
            "outer_selections": selections,
            "relative_improvement": gain,
            "city_gain_c": city_gain,
            "bootstrap": bootstrap,
            "promotion_passed": gate,
            "final_development_candidate": winner,
            "selection_scores_not_confirmation": scores,
            "metrics": metrics,
        }
        write_json(output / "summary.json", summary)
        status("fitting_final_development_artifact", candidate=winner, promotion_passed=gate)
        final_models = fit_models(data.scored_frame, data.target, config)
        bundle = {
            "models": final_models,
            "candidate": winner,
            "signature": signature,
            "promotion_passed": gate,
            "role": "posthoc_research_only",
            "prediction_requires_complete_city_date_universe": True,
        }
        model_path = output / "development_model.joblib"
        joblib.dump(bundle, model_path)
        check = data.prediction_universe.iloc[:1000]
        pd.testing.assert_frame_equal(
            predict_models(final_models, check),
            predict_models(joblib.load(model_path)["models"], check),
        )
        write_json(
            output / "model_metadata.json",
            {key: value for key, value in bundle.items() if key != "models"}
            | {"sha256": digest(model_path)},
        )
        status("complete", candidate=winner, promotion_passed=gate, relative_improvement=gain)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        config = tomllib.loads(Path(__file__).with_name("experiment.toml").read_text())
        write_json(
            ROOT / config["outputs"]["directory"] / "status.json",
            {"state": "failed", "error": repr(error), "updated_utc": datetime.now(UTC).isoformat()},
        )
        raise
