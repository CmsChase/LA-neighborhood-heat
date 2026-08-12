"""Frozen production evaluation for the three-city external confirmation."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity.evaluation_protocol_lock import (
    LOCK_PATH as PROTOCOL_LOCK_PATH,
)
from la_heat.multicity.evaluation_protocol_lock import authenticate_protocol_model_lock
from la_heat.multicity.external_target_authorization import (
    AUTHORIZATION_PATH,
    PREDICTION_COLUMNS,
    authenticate_external_prediction_commit,
    authenticate_external_target_authorization,
)
from la_heat.multicity.external_target_worker import EXTERNAL_COMPLETION
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.spatial_blocks import COMBINED_OUTPUT as SPATIAL_BLOCKS_PATH
from la_heat.multicity.spatial_blocks import MANIFEST_PATH as SPATIAL_BLOCKS_MANIFEST_PATH
from la_heat.multicity.spatial_blocks import OUTPUT_COLUMNS as SPATIAL_BLOCK_COLUMNS
from la_heat.multicity.target_authorization import (
    authenticate_target_execution_authorization,
    open_or_authenticate_values_marker,
)
from la_heat.multicity.target_transaction import EXTERNAL_LANE
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "multicity-external-evaluation-v1"
BOOTSTRAP_ITERATIONS: Final = 10_000
BOOTSTRAP_SEED: Final = 20260728
CONFIDENCE_LEVEL: Final = 0.95
HOTSPOT_FRACTION: Final = 0.20
OUTPUT_DIRECTORY: Final = Path("data/processed/multicity/external_evaluation")
COMPLETION_FILENAME: Final = "EXTERNAL_EVALUATION_COMPLETE.json"
TARGET_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "target_date",
    "target_lst_c",
    "target_available",
    "date_usable",
)
OUTPUT_COLUMNS_BY_FILE: Final = {
    "scored_rows.parquet": (
        *PREDICTION_COLUMNS,
        "target_lst_c",
        "target_available",
        "date_usable",
        "spatial_block",
        "b1_error_c",
        "m2_error_c",
        "b1_absolute_error_c",
        "m2_absolute_error_c",
        "m2_interval_covered",
        "m2_wis90_c",
    ),
    "date_metrics.parquet": (
        "city_id",
        "target_date",
        "date_count",
        "row_count",
        "spatial_block_count",
        "b1_mae_c",
        "b1_rmse_c",
        "b1_signed_error_c",
        "b1_anomaly_mae_c",
        "b1_spearman",
        "b1_hotspot_average_precision",
        "b1_hotspot_precision",
        "b1_hotspot_recall",
        "b1_hotspot_false_negative_rate",
        "m2_mae_c",
        "m2_rmse_c",
        "m2_signed_error_c",
        "m2_anomaly_mae_c",
        "m2_spearman",
        "m2_hotspot_average_precision",
        "m2_hotspot_precision",
        "m2_hotspot_recall",
        "m2_hotspot_false_negative_rate",
        "m2_interval_coverage",
        "m2_mean_interval_width_c",
        "m2_wis90_c",
        "m2_retention_fraction",
    ),
    "city_metrics.parquet": (
        "city_id",
        "date_count",
        "row_count",
        "spatial_block_count",
        "b1_equal_date_mae_c",
        "m2_equal_date_mae_c",
        "m2_minus_b1_equal_date_mae_c",
        "m2_interval_coverage",
        "m2_mean_interval_width_c",
        "m2_wis90_c",
        "m2_retention_fraction",
        "m2_all_prediction_mae_c",
        "m2_accepted_prediction_mae_c",
        "m2_accepted_mae_improvement_fraction",
        "median_per_date_m2_spearman",
    ),
    "risk_coverage.parquet": (
        "cohort_id",
        "coverage_fraction",
        "retained_rows",
        "retained_city_date_count",
        "retained_spatial_block_count",
        "m2_mae_c",
        "maximum_interval_width_c",
    ),
}
SUMMARY_KEYS: Final = (
    "schema_version",
    "algorithm_version",
    "state",
    "city_ids",
    "usable_row_count",
    "usable_city_date_count",
    "usable_dates_by_city",
    "spatial_block_count",
    "primary",
    "point_prediction_gates",
    "reliability",
    "external_models_refit_or_recalibrated",
    "prediction_commit_preceded_target_access",
    "three_city_cohort_evaluated_as_one_claim",
)
BOOTSTRAP_KEYS: Final = (
    "bootstrap_method",
    "bootstrap_iterations",
    "bootstrap_seed",
    "confidence_level",
    "cities_resampled_separately",
    "cities_equal_weight",
    "complete_dates_resampled",
    "complete_spatial_blocks_resampled",
    "random_rows_sampled",
    "city_units",
    "relative_mae_improvement_mean",
    "relative_mae_improvement_ci_lower",
    "relative_mae_improvement_ci_upper",
    "probability_improvement_gt_zero",
    "probability_improvement_at_least_10_percent",
)
SECONDARY_METRICS: Final = (
    "per_city_equal_date_mae",
    "rmse",
    "signed_error",
    "anomaly_mae",
    "per_date_spearman",
    "top20_hotspot_metrics",
    "coverage",
    "interval_width",
    "wis",
    "risk_coverage",
)
PLANNED_FIGURE_IDS: Final = (
    "external_city_mae",
    "predicted_vs_observed",
    "error_by_city_date",
    "interval_calibration",
    "risk_coverage",
    "spatial_error_maps",
)


class ExternalEvaluationError(RuntimeError):
    """Raised when the one-time external evaluation cannot reproduce its lock."""


@dataclass(frozen=True, slots=True)
class ExternalEvaluationResult:
    scored_rows: pd.DataFrame
    date_metrics: pd.DataFrame
    city_metrics: pd.DataFrame
    risk_coverage: pd.DataFrame
    bootstrap: dict[str, Any]
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _AuthenticatedEvaluationInputs:
    authorization: dict[str, Any]
    protocol: dict[str, Any]
    external_completion: dict[str, Any]
    prediction_commit: dict[str, Any]
    predictions: pd.DataFrame
    spatial_manifest: dict[str, Any]
    spatial_blocks: pd.DataFrame
    bindings: dict[str, Any]


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExternalEvaluationError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise ExternalEvaluationError(f"{label} must be a JSON object")
    unsigned = dict(payload)
    recorded = unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise ExternalEvaluationError(f"{label} commit is invalid")
    return payload


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ExternalEvaluationError(f"{label} must stay inside the project")
    return path


def authenticate_spatial_blocks(
    project_root: str | Path,
    protocol: Mapping[str, Any],
    *,
    manifest_path: str | Path = SPATIAL_BLOCKS_MANIFEST_PATH,
    table_path: str | Path = SPATIAL_BLOCKS_PATH,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Authenticate the geometry-only block manifest and its exact parquet."""

    root = Path(project_root).resolve()
    manifest_file = _inside(root, manifest_path, label="Spatial-block manifest")
    table_file = _inside(root, table_path, label="Spatial-block table")
    manifest = _read_committed(manifest_file, label="Spatial-block manifest")
    frozen = protocol.get("input_fingerprints", {}).get("spatial_blocks", {})
    output = manifest.get("output")
    if (
        not isinstance(frozen, dict)
        or frozen.get("path") != manifest_file.relative_to(root).as_posix()
        or frozen.get("bytes") != manifest_file.stat().st_size
        or frozen.get("sha256") != sha256_file(manifest_file)
        or frozen.get("commit_sha256") != manifest.get("commit_sha256")
        or manifest.get("state") != "complete_target_blind_spatial_blocks"
        or manifest.get("block_size_km") != 5.0
        or manifest.get("spatial_crs") != "EPSG:5070"
        or not isinstance(output, dict)
        or output.get("path") != table_file.relative_to(root).as_posix()
        or not table_file.is_file()
    ):
        raise ExternalEvaluationError("Spatial-block manifest drifted from protocol lock")
    try:
        frame = pd.read_parquet(table_file)
    except Exception as error:  # noqa: BLE001 - normalize reader failures
        raise ExternalEvaluationError("Spatial-block table cannot be read") from error
    record = parquet_file_record(table_file, frame)
    if (
        tuple(frame.columns) != SPATIAL_BLOCK_COLUMNS
        or any(output.get(key) != value for key, value in record.items())
        or output.get("semantic_sha256")
        != canonical_frame_sha256(
            frame,
            sort_by=["city_id", "tract_geoid"],
            columns=list(SPATIAL_BLOCK_COLUMNS),
        )
    ):
        raise ExternalEvaluationError("Spatial-block parquet failed manifest authentication")
    return manifest, frame


def _validate_lock(protocol: Mapping[str, Any]) -> None:
    contract = protocol.get("evaluation_contract", {})
    output = protocol.get("prediction_output_contract", {})
    if (
        contract.get("primary_metric")
        != "one_minus_external_equal_city_equal_date_mae_m2_divided_by_b1"
        or contract.get("bootstrap_iterations") != BOOTSTRAP_ITERATIONS
        or contract.get("bootstrap_seed") != BOOTSTRAP_SEED
        or contract.get("bootstrap_method")
        != "city_stratified_crossed_complete_date_x_5km_spatial_block"
        or float(contract.get("confidence_level", -1)) != CONFIDENCE_LEVEL
        or float(contract.get("minimum_relative_mae_improvement", -1)) != 0.10
        or contract.get("require_ci_lower_above_zero") is not True
        or contract.get("require_no_external_city_point_degradation") is not True
        or contract.get("minimum_total_city_dates") != 30
        or contract.get("minimum_dates_per_external_city") != 8
        or float(contract.get("overall_coverage_lower", -1)) != 0.85
        or float(contract.get("overall_coverage_upper", -1)) != 0.95
        or float(contract.get("per_city_coverage_lower", -1)) != 0.80
        or float(contract.get("minimum_retention", -1)) != 0.60
        or float(contract.get("accepted_mae_improvement", -1)) != 0.10
        or float(contract.get("hotspot_fraction", -1)) != HOTSPOT_FRACTION
        or contract.get("hotspot_tie_break") != "score_desc_tract_geoid_asc"
        or contract.get("secondary_metrics") != list(SECONDARY_METRICS)
        or output.get("prediction_columns") != list(PREDICTION_COLUMNS)
        or output.get("planned_figure_ids") != list(PLANNED_FIGURE_IDS)
        or output.get("all_reports_require_row_date_block_counts") is not True
    ):
        raise ExternalEvaluationError("Frozen external evaluator contract changed")


def _normalize_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["city_id"] = result["city_id"].astype("string")
    result["tract_geoid"] = result["tract_geoid"].astype("string")
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise").dt.strftime(
        "%Y-%m-%d"
    )
    return result


def _ranked_hotspot_metrics(
    geoids: pd.Series,
    actual: np.ndarray,
    predicted: np.ndarray,
) -> tuple[float, float, float, float]:
    count = len(actual)
    k = max(1, math.ceil(HOTSPOT_FRACTION * count))
    actual_order = np.lexsort((geoids.astype(str).to_numpy(), -actual))
    predicted_order = np.lexsort((geoids.astype(str).to_numpy(), -predicted))
    actual_hot = set(actual_order[:k].tolist())
    predicted_hot = set(predicted_order[:k].tolist())
    true_positive = len(actual_hot & predicted_hot)
    precision = true_positive / k
    recall = true_positive / k
    positives_seen = 0
    precision_sum = 0.0
    for rank, position in enumerate(predicted_order, start=1):
        if int(position) in actual_hot:
            positives_seen += 1
            precision_sum += positives_seen / rank
    average_precision = precision_sum / k
    return average_precision, precision, recall, 1.0 - recall


def _prepare_scored_rows(
    predictions: pd.DataFrame,
    targets: pd.DataFrame,
    spatial_blocks: pd.DataFrame,
) -> pd.DataFrame:
    if tuple(predictions.columns) != PREDICTION_COLUMNS:
        raise ExternalEvaluationError("Prediction schema changed")
    missing_target = set(TARGET_COLUMNS) - set(targets.columns)
    if missing_target:
        raise ExternalEvaluationError(f"Target table lacks columns: {sorted(missing_target)}")
    keys = ["city_id", "tract_geoid", "target_date"]
    prediction = _normalize_keys(predictions)
    target = _normalize_keys(targets.loc[:, TARGET_COLUMNS])
    if prediction.duplicated(keys).any() or target.duplicated(keys).any():
        raise ExternalEvaluationError("Prediction or target keys are duplicated")
    date_usable_counts = target.groupby(
        ["city_id", "target_date"], observed=True
    )["date_usable"].nunique()
    if date_usable_counts.gt(1).any():
        raise ExternalEvaluationError("date_usable must be constant within each city-date")
    if set(prediction["city_id"].astype(str)) != set(EXTERNAL_CITY_IDS):
        raise ExternalEvaluationError("External prediction city cohort changed")
    if not prediction.loc[:, keys].sort_values(keys).reset_index(drop=True).equals(
        target.loc[:, keys].sort_values(keys).reset_index(drop=True)
    ):
        raise ExternalEvaluationError("Committed prediction and target key universes differ")
    block = spatial_blocks.loc[:, ["city_id", "tract_geoid", "spatial_block"]].copy()
    block["city_id"] = block["city_id"].astype("string")
    block["tract_geoid"] = block["tract_geoid"].astype("string")
    block = block.loc[block["city_id"].isin(EXTERNAL_CITY_IDS)]
    if block.duplicated(["city_id", "tract_geoid"]).any():
        raise ExternalEvaluationError("Frozen spatial-block mapping is duplicated")
    result = prediction.merge(target, on=keys, how="inner", validate="one_to_one")
    result = result.merge(
        block,
        on=["city_id", "tract_geoid"],
        how="left",
        validate="many_to_one",
        suffixes=("", "_frozen"),
    )
    if result["spatial_block"].isna().any():
        raise ExternalEvaluationError("A target tract lacks a frozen 5 km block")
    if "spatial_block_frozen" in result:
        if not result["spatial_block"].astype(str).eq(
            result["spatial_block_frozen"].astype(str)
        ).all():
            raise ExternalEvaluationError("Target spatial block drifted from frozen geometry")
        result = result.drop(columns="spatial_block_frozen")
    usable = result["target_available"].astype(bool) & result["date_usable"].astype(bool)
    result = result.loc[usable].copy()
    numeric = [
        "target_lst_c",
        "b1_prediction_c",
        "m2_prediction_c",
        "m2_lower_c",
        "m2_upper_c",
        "m2_interval_width_c",
    ]
    result.loc[:, numeric] = result.loc[:, numeric].apply(
        pd.to_numeric, errors="raise"
    ).astype("float64")
    result["spatial_block"] = result["spatial_block"].astype("string")
    if result.empty or not np.isfinite(result[numeric].to_numpy(dtype=float)).all():
        raise ExternalEvaluationError("Usable external target values are empty or nonfinite")
    result["b1_error_c"] = result["b1_prediction_c"] - result["target_lst_c"]
    result["m2_error_c"] = result["m2_prediction_c"] - result["target_lst_c"]
    result["b1_absolute_error_c"] = result["b1_error_c"].abs()
    result["m2_absolute_error_c"] = result["m2_error_c"].abs()
    result["m2_interval_covered"] = result["target_lst_c"].between(
        result["m2_lower_c"], result["m2_upper_c"], inclusive="both"
    )
    alpha = 0.10
    under = (result["m2_lower_c"] - result["target_lst_c"]).clip(lower=0)
    over = (result["target_lst_c"] - result["m2_upper_c"]).clip(lower=0)
    interval_score = result["m2_interval_width_c"] + 2.0 / alpha * (under + over)
    absolute_error = (result["m2_prediction_c"] - result["target_lst_c"]).abs()
    # Standard WIS with one central 90% interval and the M2 point prediction
    # as its median: (0.5*AE + alpha/2*IS_alpha) / (1 + 0.5).
    result["m2_wis90_c"] = (
        0.5 * absolute_error + alpha / 2.0 * interval_score
    ) / 1.5
    return result.sort_values(keys, kind="stable").reset_index(drop=True)


def _date_metrics(rows: pd.DataFrame) -> pd.DataFrame:
    results: list[dict[str, Any]] = []
    for (city_id, target_date), frame in rows.groupby(
        ["city_id", "target_date"], observed=True, sort=True
    ):
        actual = frame["target_lst_c"].to_numpy(dtype=float)
        row: dict[str, Any] = {
            "city_id": str(city_id),
            "target_date": str(target_date),
            "date_count": 1,
            "row_count": len(frame),
            "spatial_block_count": int(frame["spatial_block"].nunique()),
        }
        actual_anomaly = actual - np.median(actual)
        for prefix in ("b1", "m2"):
            error = frame[f"{prefix}_error_c"].to_numpy(dtype=float)
            predicted = frame[f"{prefix}_prediction_c"].to_numpy(dtype=float)
            predicted_anomaly = predicted - np.median(predicted)
            ap, precision, recall, fnr = _ranked_hotspot_metrics(
                frame["tract_geoid"], actual, predicted
            )
            row.update(
                {
                    f"{prefix}_mae_c": float(np.mean(np.abs(error))),
                    f"{prefix}_rmse_c": float(np.sqrt(np.mean(np.square(error)))),
                    f"{prefix}_signed_error_c": float(np.mean(error)),
                    f"{prefix}_anomaly_mae_c": float(
                        np.mean(np.abs(predicted_anomaly - actual_anomaly))
                    ),
                    f"{prefix}_spearman": float(
                        pd.Series(predicted).corr(pd.Series(actual), method="spearman")
                    ),
                    f"{prefix}_hotspot_average_precision": ap,
                    f"{prefix}_hotspot_precision": precision,
                    f"{prefix}_hotspot_recall": recall,
                    f"{prefix}_hotspot_false_negative_rate": fnr,
                }
            )
        row.update(
            {
                "m2_interval_coverage": float(frame["m2_interval_covered"].mean()),
                "m2_mean_interval_width_c": float(frame["m2_interval_width_c"].mean()),
                "m2_wis90_c": float(frame["m2_wis90_c"].mean()),
                "m2_retention_fraction": float(frame["m2_accepted"].astype(bool).mean()),
            }
        )
        results.append(row)
    return pd.DataFrame(results).sort_values(
        ["city_id", "target_date"], kind="stable"
    ).reset_index(drop=True)


def _city_metrics(dates: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    results: list[dict[str, Any]] = []
    for city_id in EXTERNAL_CITY_IDS:
        date_frame = dates.loc[dates["city_id"].eq(city_id)]
        row_frame = rows.loc[rows["city_id"].eq(city_id)]
        accepted = row_frame["m2_accepted"].astype(bool)
        all_mae = float(row_frame["m2_absolute_error_c"].mean())
        accepted_mae = (
            float(row_frame.loc[accepted, "m2_absolute_error_c"].mean())
            if accepted.any()
            else math.nan
        )
        results.append(
            {
                "city_id": city_id,
                "date_count": len(date_frame),
                "row_count": len(row_frame),
                "spatial_block_count": int(row_frame["spatial_block"].nunique()),
                "b1_equal_date_mae_c": float(date_frame["b1_mae_c"].mean()),
                "m2_equal_date_mae_c": float(date_frame["m2_mae_c"].mean()),
                "m2_minus_b1_equal_date_mae_c": float(
                    date_frame["m2_mae_c"].mean() - date_frame["b1_mae_c"].mean()
                ),
                "m2_interval_coverage": float(row_frame["m2_interval_covered"].mean()),
                "m2_mean_interval_width_c": float(
                    row_frame["m2_interval_width_c"].mean()
                ),
                "m2_wis90_c": float(row_frame["m2_wis90_c"].mean()),
                "m2_retention_fraction": float(accepted.mean()),
                "m2_all_prediction_mae_c": all_mae,
                "m2_accepted_prediction_mae_c": accepted_mae,
                "m2_accepted_mae_improvement_fraction": (
                    1.0 - accepted_mae / all_mae
                    if math.isfinite(accepted_mae) and all_mae > 0
                    else math.nan
                ),
                "median_per_date_m2_spearman": float(
                    date_frame["m2_spearman"].median()
                ),
            }
        )
    return pd.DataFrame(results)


def _city_crossed_draws(
    rows: pd.DataFrame,
    *,
    rng: np.random.Generator,
    replicates: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    cells = (
        rows.groupby(["target_date", "spatial_block"], observed=True, sort=True)
        .agg(
            row_count=("tract_geoid", "size"),
            b1_absolute_error_sum_c=("b1_absolute_error_c", "sum"),
            m2_absolute_error_sum_c=("m2_absolute_error_c", "sum"),
        )
        .reset_index()
    )
    dates = pd.Index(sorted(cells["target_date"].unique()))
    blocks = pd.Index(sorted(cells["spatial_block"].astype(str).unique()))
    date_index = {value: index for index, value in enumerate(dates)}
    block_index = {value: index for index, value in enumerate(blocks)}
    shape = (len(dates), len(blocks))
    counts = np.zeros(shape)
    b1_sum = np.zeros(shape)
    m2_sum = np.zeros(shape)
    for row in cells.itertuples(index=False):
        i = date_index[row.target_date]
        j = block_index[str(row.spatial_block)]
        counts[i, j] = float(row.row_count)
        b1_sum[i, j] = float(row.b1_absolute_error_sum_c)
        m2_sum[i, j] = float(row.m2_absolute_error_sum_c)
    date_weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=replicates
    ).astype(float)
    block_weights = rng.multinomial(
        len(blocks), np.full(len(blocks), 1.0 / len(blocks)), size=replicates
    ).astype(float)
    sampled_counts = np.einsum("db,rb->rd", counts, block_weights, optimize=True)
    valid = sampled_counts > 0
    weights = date_weights * valid
    denominator = weights.sum(axis=1)
    if np.any(denominator <= 0):
        raise ExternalEvaluationError("A bootstrap replicate contains no observations")

    def estimate(sums: np.ndarray) -> np.ndarray:
        sampled = np.einsum("db,rb->rd", sums, block_weights, optimize=True)
        date_mae = np.divide(
            sampled, sampled_counts, out=np.zeros_like(sampled), where=valid
        )
        return (date_mae * weights).sum(axis=1) / denominator

    return estimate(b1_sum), estimate(m2_sum), {
        "date_count": len(dates),
        "spatial_block_count": len(blocks),
        "date_block_cell_count": len(cells),
        "row_count": len(rows),
    }


def city_stratified_crossed_bootstrap(
    rows: pd.DataFrame,
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    confidence_level: float = CONFIDENCE_LEVEL,
) -> dict[str, Any]:
    """Resample complete dates and 5 km blocks independently within each city."""

    if (
        iterations != BOOTSTRAP_ITERATIONS
        or seed != BOOTSTRAP_SEED
        or confidence_level != CONFIDENCE_LEVEL
    ):
        raise ExternalEvaluationError("Frozen bootstrap parameters cannot be changed")
    rng = np.random.default_rng(seed)
    b1_draws: list[np.ndarray] = []
    m2_draws: list[np.ndarray] = []
    units: dict[str, dict[str, int]] = {}
    for city_id in EXTERNAL_CITY_IDS:
        city = rows.loc[rows["city_id"].eq(city_id)]
        if city.empty:
            raise ExternalEvaluationError("Every external city must enter bootstrap")
        b1, m2, counts = _city_crossed_draws(city, rng=rng, replicates=iterations)
        b1_draws.append(b1)
        m2_draws.append(m2)
        units[city_id] = counts
    b1 = np.mean(np.stack(b1_draws), axis=0)
    m2 = np.mean(np.stack(m2_draws), axis=0)
    if np.any(b1 <= 0):
        raise ExternalEvaluationError("Bootstrap B1 MAE must be positive")
    relative = 1.0 - m2 / b1
    alpha = (1.0 - confidence_level) / 2.0
    ci = np.quantile(relative, [alpha, 1.0 - alpha], method="linear")
    return {
        "bootstrap_method": "city_stratified_crossed_complete_date_x_5km_spatial_block",
        "bootstrap_iterations": iterations,
        "bootstrap_seed": seed,
        "confidence_level": confidence_level,
        "cities_resampled_separately": True,
        "cities_equal_weight": True,
        "complete_dates_resampled": True,
        "complete_spatial_blocks_resampled": True,
        "random_rows_sampled": False,
        "city_units": units,
        "relative_mae_improvement_mean": float(relative.mean()),
        "relative_mae_improvement_ci_lower": float(ci[0]),
        "relative_mae_improvement_ci_upper": float(ci[1]),
        "probability_improvement_gt_zero": float(np.mean(relative > 0)),
        "probability_improvement_at_least_10_percent": float(np.mean(relative >= 0.10)),
    }


def _risk_coverage(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    cohorts = [("all_external", rows)] + [
        (city, rows.loc[rows["city_id"].eq(city)]) for city in EXTERNAL_CITY_IDS
    ]
    for cohort_id, frame in cohorts:
        ordered = frame.sort_values(
            ["m2_interval_width_c", "city_id", "target_date", "tract_geoid"],
            kind="stable",
        )
        for coverage in np.linspace(0.1, 1.0, 10):
            retained = max(1, math.ceil(float(coverage) * len(ordered)))
            selected = ordered.iloc[:retained]
            records.append(
                {
                    "cohort_id": cohort_id,
                    "coverage_fraction": retained / len(ordered),
                    "retained_rows": retained,
                    "retained_city_date_count": int(
                        selected.loc[:, ["city_id", "target_date"]]
                        .drop_duplicates()
                        .shape[0]
                    ),
                    "retained_spatial_block_count": int(
                        selected["spatial_block"].nunique()
                    ),
                    "m2_mae_c": float(selected["m2_absolute_error_c"].mean()),
                    "maximum_interval_width_c": float(
                        selected["m2_interval_width_c"].max()
                    ),
                }
            )
    return pd.DataFrame(records)


def _validate_report_counts(result: ExternalEvaluationResult) -> None:
    """Enforce row/date/block counts on every aggregate report."""

    rows = result.scored_rows
    dates = result.date_metrics
    cities = result.city_metrics
    if (
        not result.date_metrics["date_count"].eq(1).all()
        or not {"row_count", "spatial_block_count"}.issubset(result.date_metrics)
        or not {"row_count", "date_count", "spatial_block_count"}.issubset(
            result.city_metrics
        )
        or not {
            "retained_rows",
            "retained_city_date_count",
            "retained_spatial_block_count",
        }.issubset(result.risk_coverage)
        or result.risk_coverage[
            [
                "retained_rows",
                "retained_city_date_count",
                "retained_spatial_block_count",
            ]
        ]
        .le(0)
        .any()
        .any()
        or not {
            "usable_row_count",
            "usable_city_date_count",
            "spatial_block_count",
        }.issubset(result.summary)
        or int(result.summary["usable_row_count"]) != len(rows)
        or int(result.summary["usable_city_date_count"]) != len(dates)
        or int(result.summary["spatial_block_count"])
        != int(rows["spatial_block"].nunique())
        or int(dates["row_count"].sum()) != len(rows)
        or int(cities["row_count"].sum()) != len(rows)
        or int(cities["date_count"].sum()) != len(dates)
        or int(cities["spatial_block_count"].sum())
        != int(rows["spatial_block"].nunique())
    ):
        raise ExternalEvaluationError("A report omitted frozen row/date/block counts")
    units = result.bootstrap.get("city_units", {})
    expected_unit_keys = {
        "date_count",
        "spatial_block_count",
        "date_block_cell_count",
        "row_count",
    }
    if set(units) != set(EXTERNAL_CITY_IDS):
        raise ExternalEvaluationError("Bootstrap unit counts are incomplete")
    for city in EXTERNAL_CITY_IDS:
        city_rows = rows.loc[rows["city_id"].eq(city)]
        expected = {
            "date_count": int(city_rows["target_date"].nunique()),
            "spatial_block_count": int(city_rows["spatial_block"].nunique()),
            "date_block_cell_count": int(
                city_rows.loc[:, ["target_date", "spatial_block"]]
                .drop_duplicates()
                .shape[0]
            ),
            "row_count": len(city_rows),
        }
        if set(units[city]) != expected_unit_keys or units[city] != expected:
            raise ExternalEvaluationError("Bootstrap unit counts are incomplete")


def evaluate_external_frames(
    predictions: pd.DataFrame,
    targets: pd.DataFrame,
    spatial_blocks: pd.DataFrame,
    protocol: Mapping[str, Any],
) -> ExternalEvaluationResult:
    """Join only committed predictions to all three completed city targets."""

    _validate_lock(protocol)
    rows = _prepare_scored_rows(predictions, targets, spatial_blocks)
    dates = _date_metrics(rows)
    cities = _city_metrics(dates, rows)
    bootstrap = city_stratified_crossed_bootstrap(rows)
    b1_mae = float(cities["b1_equal_date_mae_c"].mean())
    m2_mae = float(cities["m2_equal_date_mae_c"].mean())
    relative = 1.0 - m2_mae / b1_mae
    contract = protocol["evaluation_contract"]
    date_counts = cities.set_index("city_id")["date_count"].to_dict()
    sample_gate = sum(date_counts.values()) >= int(contract["minimum_total_city_dates"]) and all(
        int(date_counts[city]) >= int(contract["minimum_dates_per_external_city"])
        for city in EXTERNAL_CITY_IDS
    )
    no_city_degradation = bool(cities["m2_minus_b1_equal_date_mae_c"].le(0).all())
    point_success = bool(
        sample_gate
        and relative >= float(contract["minimum_relative_mae_improvement"])
        and bootstrap["relative_mae_improvement_ci_lower"] > 0
        and no_city_degradation
    )
    overall_coverage = float(rows["m2_interval_covered"].mean())
    all_m2_mae = float(rows["m2_absolute_error_c"].mean())
    accepted = rows["m2_accepted"].astype(bool)
    accepted_mae = (
        float(rows.loc[accepted, "m2_absolute_error_c"].mean())
        if accepted.any()
        else math.nan
    )
    accepted_improvement = (
        1.0 - accepted_mae / all_m2_mae
        if math.isfinite(accepted_mae) and all_m2_mae > 0
        else math.nan
    )
    reliability_success = bool(
        float(contract["overall_coverage_lower"])
        <= overall_coverage
        <= float(contract["overall_coverage_upper"])
        and cities["m2_interval_coverage"].ge(
            float(contract["per_city_coverage_lower"])
        ).all()
        and cities["m2_retention_fraction"].ge(float(contract["minimum_retention"])).all()
        and accepted_improvement >= float(contract["accepted_mae_improvement"])
    )
    summary = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete" if sample_gate else "inconclusive_sample_size",
        "city_ids": list(EXTERNAL_CITY_IDS),
        "usable_row_count": len(rows),
        "usable_city_date_count": int(len(dates)),
        "usable_dates_by_city": {city: int(date_counts[city]) for city in EXTERNAL_CITY_IDS},
        "spatial_block_count": int(rows["spatial_block"].nunique()),
        "primary": {
            "b1_equal_city_equal_date_mae_c": b1_mae,
            "m2_equal_city_equal_date_mae_c": m2_mae,
            "relative_mae_improvement_fraction": relative,
            "bootstrap_ci_lower": bootstrap["relative_mae_improvement_ci_lower"],
            "bootstrap_ci_upper": bootstrap["relative_mae_improvement_ci_upper"],
        },
        "point_prediction_gates": {
            "sample_size": sample_gate,
            "relative_improvement_at_least_10_percent": relative >= 0.10,
            "bootstrap_ci_lower_above_zero": bootstrap[
                "relative_mae_improvement_ci_lower"
            ]
            > 0,
            "no_city_point_degradation": no_city_degradation,
            "success": point_success,
        },
        "reliability": {
            "overall_interval_coverage": overall_coverage,
            "overall_retention_fraction": float(accepted.mean()),
            "all_prediction_mae_c": all_m2_mae,
            "accepted_prediction_mae_c": accepted_mae,
            "accepted_mae_improvement_fraction": accepted_improvement,
            "success": reliability_success,
        },
        "external_models_refit_or_recalibrated": False,
        "prediction_commit_preceded_target_access": True,
        "three_city_cohort_evaluated_as_one_claim": True,
    }
    result = ExternalEvaluationResult(
        scored_rows=rows,
        date_metrics=dates,
        city_metrics=cities,
        risk_coverage=_risk_coverage(rows),
        bootstrap=bootstrap,
        summary=summary,
    )
    _validate_report_counts(result)
    return result


def _authenticate_evaluation_inputs(
    root: Path,
    *,
    authorization_path: str | Path,
    external_completion_path: str | Path,
    protocol_lock_path: str | Path,
    spatial_blocks_manifest_path: str | Path,
    spatial_blocks_path: str | Path,
) -> _AuthenticatedEvaluationInputs:
    authorization = authenticate_external_target_authorization(root, authorization_path)
    protocol = authenticate_protocol_model_lock(root, protocol_lock_path)
    _validate_lock(protocol)
    completion_path = _inside(root, external_completion_path, label="External completion")
    completion = _read_committed(completion_path, label="External completion")
    if (
        completion.get("state") != "three_city_external_targets_complete"
        or completion.get("city_ids") != list(EXTERNAL_CITY_IDS)
        or completion.get("authorization", {}).get("commit_sha256")
        != authorization["commit_sha256"]
        or completion.get("external_prediction_commit_sha256")
        != authorization["external_prediction_commit_sha256"]
        or completion.get("external_work_units", {}).get("overpass") != 64
        or completion.get("external_work_units", {}).get("city_compile") != 3
        or completion.get("external_work_units", {}).get("total") != 67
        or set(completion.get("city_targets", {})) != set(EXTERNAL_CITY_IDS)
        or completion.get("final_merge")
        != {"claimed": False, "attempt": 0, "status": "pending"}
    ):
        raise ExternalEvaluationError("External completion does not match the sole claim")
    engine_authorization = authenticate_target_execution_authorization(
        root,
        authorization_path,
        expected_lane=EXTERNAL_LANE,
        expected_plan_commit_sha256=authorization["plan_commit_sha256"],
    )
    if not engine_authorization.values_opened_marker.is_file():
        raise ExternalEvaluationError("External worker never opened the same-claim marker")
    marker = open_or_authenticate_values_marker(engine_authorization)
    prediction_commit, predictions = authenticate_external_prediction_commit(
        root,
        authorization["external_prediction_commit"]["path"],
        protocol=protocol,
    )
    spatial_manifest, spatial_blocks = authenticate_spatial_blocks(
        root,
        protocol,
        manifest_path=spatial_blocks_manifest_path,
        table_path=spatial_blocks_path,
    )
    manifest_file = _inside(
        root, spatial_blocks_manifest_path, label="Spatial-block manifest"
    )
    spatial_output = spatial_manifest["output"]
    bindings = {
        "protocol_lock_commit_sha256": protocol["commit_sha256"],
        "authorization_commit_sha256": authorization["commit_sha256"],
        "external_prediction_commit_sha256": prediction_commit["commit_sha256"],
        "external_target_completion_commit_sha256": completion["commit_sha256"],
        "values_opened_commit_sha256": marker["commit_sha256"],
        "spatial_blocks_manifest_commit_sha256": spatial_manifest["commit_sha256"],
        "spatial_blocks_manifest_sha256": sha256_file(manifest_file),
        "spatial_blocks_sha256": spatial_output["sha256"],
        "spatial_blocks_semantic_sha256": spatial_output["semantic_sha256"],
    }
    return _AuthenticatedEvaluationInputs(
        authorization=authorization,
        protocol=protocol,
        external_completion=completion,
        prediction_commit=prediction_commit,
        predictions=predictions,
        spatial_manifest=spatial_manifest,
        spatial_blocks=spatial_blocks,
        bindings=bindings,
    )


def _verified_target_tables(
    root: Path, completion: Mapping[str, Any]
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    city_targets = completion.get("city_targets", {})
    if set(city_targets) != set(EXTERNAL_CITY_IDS):
        raise ExternalEvaluationError("All three city compiles are required before scoring")
    for city_id in EXTERNAL_CITY_IDS:
        record = city_targets[city_id]
        if not isinstance(record, dict):
            raise ExternalEvaluationError("City target record is invalid")
        directory = _inside(root, record.get("directory", ""), label="City target directory")
        target_record = record.get("output_files", {}).get("targets.parquet")
        path = directory / "targets.parquet"
        if (
            not isinstance(target_record, dict)
            or not path.is_file()
            or target_record.get("bytes") != path.stat().st_size
            or target_record.get("sha256") != sha256_file(path)
        ):
            raise ExternalEvaluationError("City target table failed authentication")
        frames.append(pd.read_parquet(path))
    return pd.concat(frames, ignore_index=True)


def publish_external_evaluation(
    project_root: str | Path,
    result: ExternalEvaluationResult,
    *,
    input_bindings: Mapping[str, Any],
    output_directory: str | Path = OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Publish outputs atomically; an existing final directory is immutable."""

    root = Path(project_root).resolve()
    _validate_report_counts(result)
    output = _inside(root, output_directory, label="Evaluation output")
    if output.exists():
        raise ExternalEvaluationError("External evaluation output is append-only")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    tables = {
        "scored_rows.parquet": result.scored_rows,
        "date_metrics.parquet": result.date_metrics,
        "city_metrics.parquet": result.city_metrics,
        "risk_coverage.parquet": result.risk_coverage,
    }
    records: dict[str, Any] = {}
    for name, frame in tables.items():
        if tuple(frame.columns) != OUTPUT_COLUMNS_BY_FILE[name]:
            raise ExternalEvaluationError(f"Frozen output schema changed for {name}")
        path = temporary / name
        atomic_parquet(frame, path)
        records[name] = parquet_file_record(path, frame)
    for name, payload in (
        ("bootstrap.json", result.bootstrap),
        ("summary.json", result.summary),
    ):
        path = temporary / name
        atomic_json(dict(payload), path)
        records[name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    completion: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "external_evaluation_complete",
        "input_bindings": dict(input_bindings),
        "evaluation_contract": {
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "confidence_level": CONFIDENCE_LEVEL,
            "prediction_columns": list(PREDICTION_COLUMNS),
            "city_ids": list(EXTERNAL_CITY_IDS),
            "cities_equal_weight": True,
            "dates_equal_weight_within_city": True,
            "random_row_bootstrap": False,
            "output_columns": {
                name: list(columns)
                for name, columns in OUTPUT_COLUMNS_BY_FILE.items()
            },
            "summary_keys": list(SUMMARY_KEYS),
            "bootstrap_keys": list(BOOTSTRAP_KEYS),
        },
        "output_files": records,
        "summary_commit_sha256": canonical_sha256(result.summary),
        "bootstrap_commit_sha256": canonical_sha256(result.bootstrap),
    }
    completion["commit_sha256"] = canonical_sha256(completion)
    atomic_json(completion, temporary / COMPLETION_FILENAME)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.rename(temporary, output)
    return completion


def authenticate_external_evaluation_completion(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    external_completion_path: str | Path = EXTERNAL_COMPLETION,
    protocol_lock_path: str | Path = PROTOCOL_LOCK_PATH,
    spatial_blocks_manifest_path: str | Path = SPATIAL_BLOCKS_MANIFEST_PATH,
    spatial_blocks_path: str | Path = SPATIAL_BLOCKS_PATH,
    output_directory: str | Path = OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Independently authenticate every published metric artifact and input."""

    root = Path(project_root).resolve()
    inputs = _authenticate_evaluation_inputs(
        root,
        authorization_path=authorization_path,
        external_completion_path=external_completion_path,
        protocol_lock_path=protocol_lock_path,
        spatial_blocks_manifest_path=spatial_blocks_manifest_path,
        spatial_blocks_path=spatial_blocks_path,
    )
    output = _inside(root, output_directory, label="Evaluation output")
    completion = _read_committed(
        output / COMPLETION_FILENAME, label="External evaluation completion"
    )
    contract = completion.get("evaluation_contract", {})
    expected_contract = {
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "confidence_level": CONFIDENCE_LEVEL,
        "prediction_columns": list(PREDICTION_COLUMNS),
        "city_ids": list(EXTERNAL_CITY_IDS),
        "cities_equal_weight": True,
        "dates_equal_weight_within_city": True,
        "random_row_bootstrap": False,
        "output_columns": {
            name: list(columns) for name, columns in OUTPUT_COLUMNS_BY_FILE.items()
        },
        "summary_keys": list(SUMMARY_KEYS),
        "bootstrap_keys": list(BOOTSTRAP_KEYS),
    }
    records = completion.get("output_files")
    expected_files = {
        *OUTPUT_COLUMNS_BY_FILE,
        "bootstrap.json",
        "summary.json",
    }
    if (
        completion.get("algorithm_version") != ALGORITHM_VERSION
        or completion.get("state") != "external_evaluation_complete"
        or completion.get("input_bindings") != inputs.bindings
        or contract != expected_contract
        or not isinstance(records, dict)
        or set(records) != expected_files
    ):
        raise ExternalEvaluationError("Evaluation completion contract or inputs changed")
    tables: dict[str, pd.DataFrame] = {}
    for name, expected_columns in OUTPUT_COLUMNS_BY_FILE.items():
        path = output / name
        record = records[name]
        if not isinstance(record, dict) or not path.is_file():
            raise ExternalEvaluationError(f"Published output is missing: {name}")
        try:
            frame = pd.read_parquet(path)
        except Exception as error:  # noqa: BLE001 - normalize reader failures
            raise ExternalEvaluationError(f"Published output cannot be read: {name}") from error
        if (
            tuple(frame.columns) != expected_columns
            or record != parquet_file_record(path, frame)
        ):
            raise ExternalEvaluationError(f"Published parquet failed authentication: {name}")
        tables[name] = frame

    json_payloads: dict[str, dict[str, Any]] = {}
    for name in ("bootstrap.json", "summary.json"):
        path = output / name
        record = records[name]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ExternalEvaluationError(f"Published JSON cannot be read: {name}") from error
        if (
            not isinstance(payload, dict)
            or not isinstance(record, dict)
            or record
            != {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        ):
            raise ExternalEvaluationError(f"Published JSON failed authentication: {name}")
        json_payloads[name] = payload
    summary = json_payloads["summary.json"]
    bootstrap = json_payloads["bootstrap.json"]
    date_metrics = tables["date_metrics.parquet"]
    city_metrics = tables["city_metrics.parquet"]
    risk_coverage = tables["risk_coverage.parquet"]
    authenticated_result = ExternalEvaluationResult(
        scored_rows=tables["scored_rows.parquet"],
        date_metrics=date_metrics,
        city_metrics=city_metrics,
        risk_coverage=risk_coverage,
        bootstrap=bootstrap,
        summary=summary,
    )
    _validate_report_counts(authenticated_result)
    if (
        tuple(summary) != SUMMARY_KEYS
        or tuple(bootstrap) != BOOTSTRAP_KEYS
        or completion.get("summary_commit_sha256") != canonical_sha256(summary)
        or completion.get("bootstrap_commit_sha256") != canonical_sha256(bootstrap)
        or summary.get("city_ids") != list(EXTERNAL_CITY_IDS)
        or summary.get("external_models_refit_or_recalibrated") is not False
        or summary.get("prediction_commit_preceded_target_access") is not True
        or summary.get("three_city_cohort_evaluated_as_one_claim") is not True
        or summary.get("usable_row_count") != len(tables["scored_rows.parquet"])
        or summary.get("usable_city_date_count") != len(tables["date_metrics.parquet"])
        or not date_metrics["date_count"].eq(1).all()
        or date_metrics[["row_count", "spatial_block_count"]].le(0).any().any()
        or city_metrics[["row_count", "date_count", "spatial_block_count"]]
        .le(0)
        .any()
        .any()
        or risk_coverage[
            [
                "retained_rows",
                "retained_city_date_count",
                "retained_spatial_block_count",
            ]
        ]
        .le(0)
        .any()
        .any()
        or set(city_metrics["city_id"].astype(str)) != set(EXTERNAL_CITY_IDS)
        or bootstrap.get("bootstrap_iterations") != BOOTSTRAP_ITERATIONS
        or bootstrap.get("bootstrap_seed") != BOOTSTRAP_SEED
        or bootstrap.get("random_rows_sampled") is not False
        or set(bootstrap.get("city_units", {})) != set(EXTERNAL_CITY_IDS)
    ):
        raise ExternalEvaluationError("Published metrics do not reproduce frozen schema")
    return completion


def run_and_publish_external_evaluation(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    external_completion_path: str | Path = EXTERNAL_COMPLETION,
    protocol_lock_path: str | Path = PROTOCOL_LOCK_PATH,
    spatial_blocks_manifest_path: str | Path = SPATIAL_BLOCKS_MANIFEST_PATH,
    spatial_blocks_path: str | Path = SPATIAL_BLOCKS_PATH,
    output_directory: str | Path = OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Authenticate the completed claim, then and only then read target tables."""

    root = Path(project_root).resolve()
    inputs = _authenticate_evaluation_inputs(
        root,
        authorization_path=authorization_path,
        external_completion_path=external_completion_path,
        protocol_lock_path=protocol_lock_path,
        spatial_blocks_manifest_path=spatial_blocks_manifest_path,
        spatial_blocks_path=spatial_blocks_path,
    )
    targets = _verified_target_tables(root, inputs.external_completion)
    result = evaluate_external_frames(
        inputs.predictions,
        targets,
        inputs.spatial_blocks,
        inputs.protocol,
    )
    published = publish_external_evaluation(
        root,
        result,
        input_bindings=inputs.bindings,
        output_directory=output_directory,
    )
    authenticated = authenticate_external_evaluation_completion(
        root,
        authorization_path=authorization_path,
        external_completion_path=external_completion_path,
        protocol_lock_path=protocol_lock_path,
        spatial_blocks_manifest_path=spatial_blocks_manifest_path,
        spatial_blocks_path=spatial_blocks_path,
        output_directory=output_directory,
    )
    if authenticated != published:
        raise ExternalEvaluationError("Published evaluation did not authenticate")
    return published
