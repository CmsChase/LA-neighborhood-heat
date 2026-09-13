"""Run the fixed source-only M3 level-mechanism 2 × 2 experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from la_heat.modeling import CompleteFeatureValidator, ObservedDynamicMedianImputer  # noqa: E402
from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    B1_FEATURES,
    CALENDAR_FEATURES,
    KEY_COLUMNS,
    M3_CANDIDATES,
    WEATHER_FEATURES,
    build_b1_estimator,
    build_m3_estimators,
    city_date_level_weights,
    city_date_row_weights,
)

CONFIG_PATH = Path(__file__).with_name("experiment.toml")
MODEL_B1 = "B1"
LEVEL_AGGREGATIONS = {"selected_training_support", "complete_predictor_support"}


@dataclass(frozen=True)
class Variant:
    variant_id: str
    label: str
    include_elevation: bool
    level_aggregation: str


@dataclass(frozen=True)
class SourceData:
    prediction_universe: pd.DataFrame
    scored_frame: pd.DataFrame
    target: pd.Series


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def variants_from_config(config: dict[str, Any]) -> tuple[Variant, ...]:
    variants = tuple(
        Variant(
            str(row["id"]),
            str(row["label"]),
            bool(row["include_elevation"]),
            str(row["level_aggregation"]),
        )
        for row in config["variants"]
    )
    if tuple(row.variant_id for row in variants) != ("A", "B", "C", "D"):
        raise ValueError("The experiment must contain the fixed A/B/C/D variants.")
    if any(row.level_aggregation not in LEVEL_AGGREGATIONS for row in variants):
        raise ValueError("Unknown level aggregation mode.")
    return variants


def _normalize_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["city_id"] = result["city_id"].astype(str)
    result["tract_geoid"] = result["tract_geoid"].astype(str).str.zfill(11)
    result["target_date"] = pd.to_datetime(result["target_date"]).dt.strftime("%Y-%m-%d")
    return result.sort_values(list(KEY_COLUMNS), kind="stable").reset_index(drop=True)


def _city_context(root: Path, config: dict[str, Any]) -> dict[str, float]:
    path = root / config["inputs"]["source_predictor_completion"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row["city_id"]): float(row["city_centroid_latitude_deg"])
        for row in payload["city_context"]
    }


def load_source_data(root: Path, config: dict[str, Any]) -> tuple[SourceData, list[dict[str, Any]]]:
    experiment = config["experiment"]
    inputs = config["inputs"]
    cities = tuple(str(value) for value in experiment["source_cities"])
    excluded = set(str(value) for value in experiment["opened_cities_excluded"])
    if set(cities) & excluded:
        raise ValueError("Opened-city IDs cannot appear in the source experiment.")
    context = _city_context(root, config)
    predictors: list[pd.DataFrame] = []
    targets: list[pd.DataFrame] = []
    input_records: list[dict[str, Any]] = []
    for city_id in cities:
        predictor_path = root / inputs["predictor_template"].format(city_id=city_id)
        target_path = root / inputs["target_template"].format(city_id=city_id)
        predictor = _normalize_keys(pd.read_parquet(predictor_path))
        target = _normalize_keys(pd.read_parquet(target_path))
        predictor["city_centroid_latitude_deg"] = context[city_id]
        predictors.append(predictor)
        targets.append(target)
        for role, path in (("predictors", predictor_path), ("qa_4k_targets", target_path)):
            input_records.append(
                {
                    "city_id": city_id,
                    "role": role,
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path),
                }
            )
    prediction_universe = pd.concat(predictors, ignore_index=True)
    target_table = pd.concat(targets, ignore_index=True)
    merged = prediction_universe.merge(
        target_table.loc[
            :, [*KEY_COLUMNS, "date_usable", "target_available", "target_lst_c"]
        ],
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
    )
    numeric_target = pd.to_numeric(merged["target_lst_c"], errors="coerce")
    keep = (
        merged["date_usable"].fillna(False).astype(bool)
        & merged["target_available"].fillna(False).astype(bool)
        & numeric_target.notna()
    )
    scored = merged.loc[keep, prediction_universe.columns].reset_index(drop=True)
    target = numeric_target.loc[keep].astype(float).reset_index(drop=True)
    return SourceData(prediction_universe, scored, target), input_records


def _level_features(include_elevation: bool) -> tuple[str, ...]:
    optional = ("elevation_mean_m",) if include_elevation else ()
    return (*B1_FEATURES, *optional, "city_centroid_latitude_deg")


def _level_estimator(include_elevation: bool, alpha: float) -> Pipeline:
    complete = [*CALENDAR_FEATURES]
    if include_elevation:
        complete.append("elevation_mean_m")
    complete.append("city_centroid_latitude_deg")
    preprocessor = ColumnTransformer(
        [
            (
                "complete",
                Pipeline(
                    [
                        ("validate", CompleteFeatureValidator()),
                        ("scale", StandardScaler()),
                    ]
                ),
                complete,
            ),
            (
                "dynamic",
                Pipeline(
                    [
                        ("impute", ObservedDynamicMedianImputer()),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(WEATHER_FEATURES),
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
    return Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", Ridge(alpha=alpha, fit_intercept=True, solver="lsqr", tol=0.0001)),
        ]
    )


def build_level_training_table(
    scored_training: pd.DataFrame,
    target: pd.Series,
    complete_training_universe: pd.DataFrame,
    variant: Variant,
) -> pd.DataFrame:
    features = _level_features(variant.include_elevation)
    support = (
        complete_training_universe
        if variant.level_aggregation == "complete_predictor_support"
        else scored_training
    )
    feature_levels = (
        support.loc[:, [*KEY_COLUMNS, *features]]
        .groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg({name: "median" for name in features})
        .reset_index()
    )
    target_levels = scored_training.loc[:, ["city_id", "target_date"]].copy()
    target_levels["observed_lst_c"] = target.to_numpy(dtype=float)
    target_levels = (
        target_levels.groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg(observed_lst_c=("observed_lst_c", "median"))
        .reset_index()
    )
    return feature_levels.merge(
        target_levels,
        on=["city_id", "target_date"],
        how="inner",
        validate="one_to_one",
    )


def _predict_level(model: Pipeline, frame: pd.DataFrame, features: tuple[str, ...]) -> pd.DataFrame:
    level = (
        frame.loc[:, [*KEY_COLUMNS, *features]]
        .groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg({name: "median" for name in features})
        .reset_index()
    )
    level["level_prediction_c"] = model.predict(level.loc[:, features])
    return level.loc[:, ["city_id", "target_date", "level_prediction_c"]]


def _score_predictions(
    prediction_universe: pd.DataFrame,
    scored_held: pd.DataFrame,
    held_target: pd.Series,
    model_id: str,
    prediction: np.ndarray,
    level_prediction: pd.DataFrame,
    anomaly_prediction: np.ndarray,
) -> pd.DataFrame:
    full = prediction_universe.loc[:, KEY_COLUMNS].copy()
    full["prediction_c"] = prediction
    full["anomaly_prediction_c"] = anomaly_prediction
    full = full.merge(
        level_prediction,
        on=["city_id", "target_date"],
        how="left",
        validate="many_to_one",
    )
    observed = scored_held.loc[:, KEY_COLUMNS].copy()
    observed["observed_lst_c"] = held_target.to_numpy(dtype=float)
    scored = full.merge(observed, on=list(KEY_COLUMNS), how="inner", validate="one_to_one")
    scored.insert(0, "model_id", model_id)
    return scored


def run_fold(
    data: SourceData,
    held_city: str,
    variants: tuple[Variant, ...],
    config: dict[str, Any],
) -> pd.DataFrame:
    scored_mask = data.scored_frame["city_id"].ne(held_city)
    universe_mask = data.prediction_universe["city_id"].ne(held_city)
    scored_training = data.scored_frame.loc[scored_mask].reset_index(drop=True)
    training_target = data.target.loc[scored_mask].reset_index(drop=True)
    complete_training = data.prediction_universe.loc[universe_mask].reset_index(drop=True)
    held_universe = data.prediction_universe.loc[~universe_mask].reset_index(drop=True)
    scored_held = data.scored_frame.loc[~scored_mask].reset_index(drop=True)
    held_target = data.target.loc[~scored_mask].reset_index(drop=True)

    weights = city_date_row_weights(scored_training)
    b1 = build_b1_estimator()
    b1.fit(
        scored_training.loc[:, B1_FEATURES],
        training_target,
        model__sample_weight=weights,
    )
    b1_values = np.asarray(b1.predict(held_universe.loc[:, B1_FEATURES]), dtype=float)
    b1_series = pd.Series(b1_values, index=held_universe.index)
    b1_centered = b1_series - b1_series.groupby(
        [held_universe["city_id"], held_universe["target_date"]], observed=True
    ).transform("median")
    b1_level = held_universe.loc[:, ["city_id", "target_date"]].copy()
    b1_level["level_prediction_c"] = b1_values
    b1_level = (
        b1_level.groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg(level_prediction_c=("level_prediction_c", "median"))
        .reset_index()
    )
    predictions = [
        _score_predictions(
            held_universe,
            scored_held,
            held_target,
            MODEL_B1,
            b1_values,
            b1_level,
            b1_centered.to_numpy(dtype=float),
        )
    ]

    candidate = next(
        row
        for row in M3_CANDIDATES
        if row.level_alpha == float(config["experiment"]["level_ridge_alpha"])
        and row.anomaly_max_leaf_nodes
        == int(config["experiment"]["anomaly_max_leaf_nodes"])
    )
    _, anomaly_model = build_m3_estimators(candidate)
    target_level = training_target.groupby(
        [scored_training["city_id"], scored_training["target_date"]], observed=True
    ).transform("median")
    anomaly_model.fit(
        scored_training.loc[:, ANOMALY_FEATURES],
        training_target - target_level,
        model__sample_weight=weights,
    )
    raw_anomaly = pd.Series(
        anomaly_model.predict(held_universe.loc[:, ANOMALY_FEATURES]),
        index=held_universe.index,
    )
    centered_anomaly = raw_anomaly - raw_anomaly.groupby(
        [held_universe["city_id"], held_universe["target_date"]], observed=True
    ).transform("median")

    for variant in variants:
        level_table = build_level_training_table(
            scored_training,
            training_target,
            complete_training,
            variant,
        )
        features = _level_features(variant.include_elevation)
        level_model = _level_estimator(
            variant.include_elevation,
            float(config["experiment"]["level_ridge_alpha"]),
        )
        level_model.fit(
            level_table.loc[:, features],
            level_table["observed_lst_c"],
            model__sample_weight=city_date_level_weights(level_table),
        )
        level_prediction = _predict_level(level_model, held_universe, features)
        level_lookup = level_prediction.set_index(["city_id", "target_date"])[
            "level_prediction_c"
        ]
        held_keys = pd.MultiIndex.from_frame(held_universe.loc[:, ["city_id", "target_date"]])
        level_values = level_lookup.reindex(held_keys).to_numpy(dtype=float)
        prediction = level_values + centered_anomaly.to_numpy(dtype=float)
        predictions.append(
            _score_predictions(
                held_universe,
                scored_held,
                held_target,
                variant.variant_id,
                prediction,
                level_prediction,
                centered_anomaly.to_numpy(dtype=float),
            )
        )
    return pd.concat(predictions, ignore_index=True)


def calculate_metrics(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = predictions.copy()
    frame["signed_error_c"] = frame["prediction_c"] - frame["observed_lst_c"]
    frame["absolute_error_c"] = frame["signed_error_c"].abs()
    frame["observed_anomaly_c"] = frame["observed_lst_c"] - frame.groupby(
        ["model_id", "city_id", "target_date"], observed=True
    )["observed_lst_c"].transform("median")
    frame["anomaly_absolute_error_c"] = (
        frame["anomaly_prediction_c"] - frame["observed_anomaly_c"]
    ).abs()
    rows: list[dict[str, Any]] = []
    for (model_id, city_id, target_date), group in frame.groupby(
        ["model_id", "city_id", "target_date"], observed=True, sort=True
    ):
        correlation = group["prediction_c"].corr(group["observed_lst_c"], method="spearman")
        rows.append(
            {
                "model_id": model_id,
                "city_id": city_id,
                "target_date": target_date,
                "rows": len(group),
                "mae_c": float(group["absolute_error_c"].mean()),
                "signed_bias_c": float(group["signed_error_c"].mean()),
                "level_bias_c": float(
                    group["level_prediction_c"].iloc[0] - group["observed_lst_c"].median()
                ),
                "anomaly_mae_c": float(group["anomaly_absolute_error_c"].mean()),
                "spearman": float(correlation) if math.isfinite(float(correlation)) else math.nan,
            }
        )
    date_metrics = pd.DataFrame(rows)
    city_metrics = (
        date_metrics.groupby(["model_id", "city_id"], observed=True, sort=True)
        .agg(
            dates=("target_date", "nunique"),
            rows=("rows", "sum"),
            mae_c=("mae_c", "mean"),
            signed_bias_c=("signed_bias_c", "mean"),
            level_bias_c=("level_bias_c", "mean"),
            anomaly_mae_c=("anomaly_mae_c", "mean"),
            median_spearman=("spearman", "median"),
        )
        .reset_index()
    )
    overall = (
        city_metrics.groupby("model_id", observed=True, sort=True)
        .agg(
            cities=("city_id", "nunique"),
            city_dates=("dates", "sum"),
            rows=("rows", "sum"),
            mae_c=("mae_c", "mean"),
            signed_bias_c=("signed_bias_c", "mean"),
            level_bias_c=("level_bias_c", "mean"),
            anomaly_mae_c=("anomaly_mae_c", "mean"),
            median_city_spearman=("median_spearman", "median"),
        )
        .reset_index()
    )
    return date_metrics, city_metrics, overall


def summarize(
    config: dict[str, Any],
    variants: tuple[Variant, ...],
    input_records: list[dict[str, Any]],
    city_metrics: pd.DataFrame,
    overall: pd.DataFrame,
    run_signature: str,
) -> dict[str, Any]:
    b1 = overall.set_index("model_id").loc[MODEL_B1]
    by_model = overall.set_index("model_id")
    by_city = city_metrics.set_index(["model_id", "city_id"])
    decisions: list[dict[str, Any]] = []
    minimum_improvement = float(config["decision"]["minimum_relative_mae_improvement"])
    maximum_degradation = float(config["decision"]["maximum_city_mae_degradation_c"])
    require_anomaly = bool(config["decision"]["require_anomaly_mae_not_worse_than_b1"])
    for variant in variants:
        row = by_model.loc[variant.variant_id]
        relative_improvement = (float(b1["mae_c"]) - float(row["mae_c"])) / float(
            b1["mae_c"]
        )
        city_deltas = {
            city_id: float(
                by_city.loc[(variant.variant_id, city_id), "mae_c"]
                - by_city.loc[(MODEL_B1, city_id), "mae_c"]
            )
            for city_id in config["experiment"]["source_cities"]
        }
        decisions.append(
            {
                "variant_id": variant.variant_id,
                "relative_mae_improvement_vs_b1": relative_improvement,
                "city_mae_delta_vs_b1_c": city_deltas,
                "aggregate_improvement_gate": relative_improvement >= minimum_improvement,
                "no_city_degradation_gate": max(city_deltas.values()) <= maximum_degradation,
                "anomaly_not_worse_gate": (
                    float(row["anomaly_mae_c"]) <= float(b1["anomaly_mae_c"])
                    if require_anomaly
                    else True
                ),
            }
        )
        decisions[-1]["qualifies_for_opened_city_stress_test"] = all(
            decisions[-1][key]
            for key in (
                "aggregate_improvement_gate",
                "no_city_degradation_gate",
                "anomaly_not_worse_gate",
            )
        )
    mae = {model_id: float(by_model.loc[model_id, "mae_c"]) for model_id in ("A", "B", "C", "D")}
    contrasts = {
        "consistent_support_main_effect_c": 0.5 * ((mae["B"] - mae["A"]) + (mae["D"] - mae["C"])),
        "remove_elevation_main_effect_c": 0.5 * ((mae["C"] - mae["A"]) + (mae["D"] - mae["B"])),
        "interaction_c": mae["D"] - mae["C"] - mae["B"] + mae["A"],
    }
    original_difference = abs(
        mae["A"] - float(config["experiment"]["expected_original_m3_mae_c"])
    )
    qualified = [
        row["variant_id"]
        for row in decisions
        if row["qualifies_for_opened_city_stress_test"]
    ]
    return {
        "schema_version": 1,
        "experiment": config["experiment"],
        "run_signature": run_signature,
        "completed_at": datetime.now(UTC).isoformat(),
        "network_or_new_city_data_used": False,
        "input_files": input_records,
        "overall_metrics": overall.to_dict("records"),
        "factorial_mae_contrasts": contrasts,
        "original_variant_reproduction": {
            "observed_mae_c": mae["A"],
            "expected_mae_c": float(config["experiment"]["expected_original_m3_mae_c"]),
            "absolute_difference_c": original_difference,
            "passed": original_difference
            <= float(config["experiment"]["reproduction_tolerance_c"]),
        },
        "development_gates": decisions,
        "qualified_variants": qualified,
        "recommended_next_action": (
            "evaluate_qualified_variants_on_previously_opened_cities_as_development_stress_tests"
            if qualified
            else "pause_complex_zero_shot_absolute_temperature_route"
        ),
    }


def run(
    project_root: Path,
    config_path: Path,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    config = load_config(config_path)
    variants = variants_from_config(config)
    output = output_directory or root / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(config_path.read_bytes() + Path(__file__).read_bytes()).hexdigest()
    data, input_records = load_source_data(root, config)
    cities = tuple(str(value) for value in config["experiment"]["source_cities"])
    checkpoint_directory = output / "checkpoints"
    checkpoint_directory.mkdir(parents=True, exist_ok=True)
    completed: list[str] = []
    fold_predictions: list[pd.DataFrame] = []
    _write_json(
        output / "status.json",
        {"state": "running", "run_signature": signature, "completed_folds": completed},
    )
    try:
        for held_city in cities:
            checkpoint = checkpoint_directory / f"{held_city}.parquet"
            if checkpoint.exists():
                frame = pd.read_parquet(checkpoint)
                if set(frame["run_signature"].astype(str)) == {signature}:
                    fold_predictions.append(frame.drop(columns="run_signature"))
                    completed.append(held_city)
                    continue
            print(f"[m3-2x2] fitting held city {held_city}", flush=True)
            frame = run_fold(data, held_city, variants, config)
            stored = frame.copy()
            stored["run_signature"] = signature
            stored.to_parquet(checkpoint, index=False)
            fold_predictions.append(frame)
            completed.append(held_city)
            _write_json(
                output / "status.json",
                {"state": "running", "run_signature": signature, "completed_folds": completed},
            )
        predictions = pd.concat(fold_predictions, ignore_index=True)
        date_metrics, city_metrics, overall = calculate_metrics(predictions)
        summary = summarize(config, variants, input_records, city_metrics, overall, signature)
        predictions.to_parquet(output / "predictions.parquet", index=False)
        date_metrics.to_parquet(output / "date_metrics.parquet", index=False)
        city_metrics.to_parquet(output / "city_metrics.parquet", index=False)
        overall.to_parquet(output / "metrics.parquet", index=False)
        _write_json(output / "summary.json", summary)
        _write_json(
            output / "status.json",
            {
                "state": "complete",
                "run_signature": signature,
                "completed_folds": completed,
                "recommended_next_action": summary["recommended_next_action"],
            },
        )
        return summary
    except Exception as error:
        _write_json(
            output / "status.json",
            {
                "state": "failed",
                "run_signature": signature,
                "completed_folds": completed,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    summary = run(args.project_root, args.config.resolve(), args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
