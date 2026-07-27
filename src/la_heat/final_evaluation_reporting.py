"""Deterministic reporting for the one-time 2025 final evaluation.

This module receives an already joined surface containing target/QA fields and
blind B1/M2 predictions.  It never fits, tunes, selects, or loads a model.  The
pure :func:`build_final_evaluation_reporting` function validates the joined
surface and calculates every report table without filesystem effects.
:func:`generate_final_evaluation_reports` then writes those tables and three
predeclared diagnostic figures atomically into a caller-owned staging directory.

Absolute-LST metrics use only rows for which both ``date_usable`` and
``target_available`` are true.  Relative/hotspot diagnostics additionally
require the frozen spatial-representativeness gate.  Crossed bootstrap draws
whole dates and whole spatial blocks; tract-date rows are never resampled
independently.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from pandas.api.types import is_bool_dtype, is_numeric_dtype

from la_heat.metrics import evaluate_absolute_lst_predictions
from la_heat.model_endpoint_diagnostics import (
    continuous_average_precision,
    exact_top_k_mask,
)
from la_heat.model_result_analysis import (
    BOOTSTRAP_METHOD,
    BOOTSTRAP_SAMPLING_UNIT,
    aggregate_paired_date_block_errors,
    crossed_date_spatial_block_bootstrap,
)
from la_heat.provenance import atomic_csv, atomic_json

REPORTING_ALGORITHM_VERSION: Final = "final-evaluation-reporting-v1"
SENTINEL_COMPLETE: Final = "sentinel_complete"
SENTINEL_ALL_MISSING: Final = "sentinel_all_five_missing"

MODEL_METRICS_FILENAME: Final = "model_metrics.csv"
PER_DATE_METRICS_FILENAME: Final = "per_date_metrics.csv"
PAIRED_ERRORS_FILENAME: Final = "paired_date_block_errors.csv"
BOOTSTRAP_FILENAME: Final = "crossed_bootstrap.json"
GATES_FILENAME: Final = "protocol_gates.csv"
HOTSPOT_PER_DATE_FILENAME: Final = "hotspot_per_date.csv"
HOTSPOT_SUMMARY_FILENAME: Final = "hotspot_summary.csv"
SENSOR_PER_DATE_FILENAME: Final = "sensor_per_date_metrics.csv"
SENSOR_SUMMARY_FILENAME: Final = "sensor_summary.csv"
SENTINEL_STRATUM_FILENAME: Final = "sentinel_stratum_summary.csv"
QA_MISSINGNESS_FILENAME: Final = "qa_missingness_summary.csv"
TRACT_MAP_SUMMARY_FILENAME: Final = "tract_choropleth_summary.csv"
MAP_FIGURE_FILENAME: Final = "observed_predicted_residual_maps.pdf"
DATE_FIGURE_FILENAME: Final = "per_date_error_and_rank.png"
HOTSPOT_FIGURE_FILENAME: Final = "hotspot_precision_recall.png"

REPORT_TABLE_FILENAMES: Final = (
    MODEL_METRICS_FILENAME,
    PER_DATE_METRICS_FILENAME,
    PAIRED_ERRORS_FILENAME,
    BOOTSTRAP_FILENAME,
    GATES_FILENAME,
    HOTSPOT_PER_DATE_FILENAME,
    HOTSPOT_SUMMARY_FILENAME,
    SENSOR_PER_DATE_FILENAME,
    SENSOR_SUMMARY_FILENAME,
    SENTINEL_STRATUM_FILENAME,
    QA_MISSINGNESS_FILENAME,
    TRACT_MAP_SUMMARY_FILENAME,
)
REPORT_FIGURE_FILENAMES: Final = (
    MAP_FIGURE_FILENAME,
    DATE_FIGURE_FILENAME,
    HOTSPOT_FIGURE_FILENAME,
)
REPORT_OUTPUT_FILENAMES: Final = REPORT_TABLE_FILENAMES + REPORT_FIGURE_FILENAMES

_MODEL_METRIC_COLUMNS: Final = (
    "model_id",
    "model_role",
    "equal_date_weighted_mae_c",
    "pooled_rmse_c",
    "pooled_oos_r2",
    "pooled_mean_signed_error_c",
    "equal_date_weighted_mean_signed_error_c",
    "equal_date_weighted_within_date_anomaly_mae_c",
    "median_per_date_spearman",
    "tract_date_row_count",
    "independent_date_count",
    "independent_spatial_block_count",
    "spearman_defined_date_count",
    "spearman_undefined_date_count",
)
_PER_DATE_COLUMNS: Final = (
    "model_id",
    "model_role",
    "target_date",
    "tract_date_row_count",
    "independent_spatial_block_count",
    "mae_c",
    "rmse_c",
    "mean_signed_error_c",
    "within_date_anomaly_mae_c",
    "spearman_rho",
    "spearman_defined",
)
_HOTSPOT_PER_DATE_COLUMNS: Final = (
    "model_id",
    "model_role",
    "target_date",
    "sensor",
    "tract_date_row_count",
    "independent_spatial_block_count",
    "exact_top_k",
    "observed_positive_count",
    "predicted_positive_count",
    "true_positive_count",
    "false_positive_count",
    "false_negative_count",
    "average_precision",
    "precision_at_k",
    "recall_at_k",
    "false_negative_rate",
)
_HOTSPOT_SUMMARY_COLUMNS: Final = (
    "model_id",
    "model_role",
    "tract_date_row_count",
    "independent_date_count",
    "independent_spatial_block_count",
    "mean_per_date_average_precision",
    "mean_per_date_precision_at_k",
    "mean_per_date_recall_at_k",
    "mean_per_date_false_negative_rate",
)
_QA_INPUT_COLUMNS: Final = (
    "source_scene_count",
    "source_scene_ids",
    "rasterized_pixel_count",
    "footprint_pixel_count",
    "eligible_pixel_count_static",
    "valid_pixel_count",
    "footprint_fraction",
    "valid_fraction",
    "median_st_uncertainty_k",
    "p90_st_uncertainty_k",
    "median_cloud_distance_km",
    "tract_exclusion_reason",
    "date_exclusion_reason",
    "union_city_coverage_fraction",
    "retained_tract_count",
    "retained_tract_fraction",
    "minimum_eligible_joint_cell_retention_fraction",
)
_EVALUATION_QA_COLUMNS: Final = (
    "source_scene_count",
    "source_scene_ids",
    "rasterized_pixel_count",
    "footprint_pixel_count",
    "eligible_pixel_count_static",
    "valid_pixel_count",
    "footprint_fraction",
    "valid_fraction",
    "median_st_uncertainty_k",
    "p90_st_uncertainty_k",
    "median_cloud_distance_km",
    "tract_exclusion_reason",
    "date_exclusion_reason",
)
_EVALUATION_ROW_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "spatial_block",
    "sensor",
    "sentinel_available",
    "target_available",
    "date_usable",
    "relative_endpoint_coverage_pass",
    "relative_hotspot_top20",
    "y_true",
    "y_pred_b1",
    "y_pred_m2",
    *_QA_INPUT_COLUMNS,
    "sentinel_stratum",
    "b1_error_c",
    "m2_error_c",
    "b1_absolute_error_c",
    "m2_absolute_error_c",
)
EVALUATION_ROW_COLUMNS: Final = _EVALUATION_ROW_COLUMNS
_PAIRED_ERROR_COLUMNS: Final = (
    "baseline_model_id",
    "primary_model_id",
    "target_date",
    "spatial_block",
    "row_count",
    "baseline_absolute_error_sum_c",
    "target_absolute_error_sum_c",
    "baseline_cell_mae_c",
    "target_cell_mae_c",
    "paired_absolute_mae_improvement_c",
)
_PROTOCOL_GATE_COLUMNS: Final = (
    "gate_id",
    "observed_value",
    "threshold",
    "comparison",
    "passed",
    "required_for_protocol_success",
    "interpretation",
    "overall_protocol_success_gate_pass",
)
_SENSOR_PER_DATE_COLUMNS: Final = (
    "model_id",
    "model_role",
    "target_date",
    "sensor",
    "tract_date_row_count",
    "independent_spatial_block_count",
    "mae_c",
    "rmse_c",
    "mean_signed_error_c",
    "within_date_anomaly_mae_c",
    "spearman_rho",
    "spearman_defined",
)
_STRATUM_SUMMARY_BASE_COLUMNS: Final = (
    "model_id",
    "model_role",
    "tract_date_row_count",
    "independent_date_count",
    "independent_spatial_block_count",
    "equal_date_weighted_mae_c",
    "pooled_rmse_c",
    "pooled_mean_signed_error_c",
    "median_per_date_spearman",
    "spearman_defined_date_count",
    "spearman_undefined_date_count",
)
_SENSOR_SUMMARY_COLUMNS: Final = (
    "model_id",
    "model_role",
    "sensor",
    *_STRATUM_SUMMARY_BASE_COLUMNS[2:],
)
_SENTINEL_STRATUM_COLUMNS: Final = (
    "model_id",
    "model_role",
    "sentinel_stratum",
    *_STRATUM_SUMMARY_BASE_COLUMNS[2:],
)
_QA_MISSINGNESS_COLUMNS: Final = (
    "summary_level",
    "target_date",
    "sensor",
    "inventory_key_count",
    "independent_date_count",
    "independent_spatial_block_count",
    "target_available_count",
    "target_unavailable_count",
    "target_availability_fraction",
    "eligible_pixel_count_date_tract_sum",
    "valid_pixel_count_date_tract_sum",
    "valid_pixel_fraction_of_static_eligible_date_tract_sum",
    "median_tract_valid_pixel_count",
    "p10_tract_valid_pixel_count",
    "mean_tract_valid_fraction",
    "median_tract_valid_fraction",
    "static_eligible_pixel_count_unique_tract_sum",
    "rasterized_pixel_count_date_tract_sum",
    "footprint_pixel_count_date_tract_sum",
    "rasterized_pixel_count_unique_tract_sum",
    "footprint_pixel_fraction_of_rasterized_date_tract_sum",
    "mean_tract_footprint_fraction",
    "median_tract_st_uncertainty_k",
    "p90_tract_st_uncertainty_k",
    "median_tract_cloud_distance_km",
    "date_usable",
    "evaluation_cohort_count",
    "evaluation_excluded_count",
    "usable_date_count",
    "excluded_date_count",
    "relative_gate_date_count",
    "evaluation_independent_date_count",
    "relative_endpoint_coverage_pass",
    "relative_hotspot_labeled_count",
    "sentinel_available_count",
    "sentinel_all_five_missing_count",
    "sentinel_availability_fraction",
    "evaluation_sentinel_available_count",
    "evaluation_sentinel_all_five_missing_count",
    "tract_qa_failure_count",
    "date_qa_failure_key_count",
    "tract_exclusion_counts_json",
    "date_exclusion_counts_json",
    "date_exclusion_reason",
    "union_city_coverage_fraction",
    "retained_tract_count",
    "retained_tract_fraction",
    "minimum_eligible_joint_cell_retention_fraction",
    "qa_rules_json",
)
TRACT_MAP_SUMMARY_COLUMNS: Final = (
    "tract_geoid",
    "spatial_block",
    "evaluated_date_count",
    "evaluated_date_fraction",
    "observed_lst_c",
    "b1_predicted_lst_c",
    "m2_predicted_lst_c",
    "b1_residual_c",
    "m2_residual_c",
    "b1_mean_absolute_error_c",
    "m2_mean_absolute_error_c",
)
REPORT_TABLE_COLUMN_CONTRACTS: Final = MappingProxyType(
    {
        MODEL_METRICS_FILENAME: _MODEL_METRIC_COLUMNS,
        PER_DATE_METRICS_FILENAME: _PER_DATE_COLUMNS,
        PAIRED_ERRORS_FILENAME: _PAIRED_ERROR_COLUMNS,
        GATES_FILENAME: _PROTOCOL_GATE_COLUMNS,
        HOTSPOT_PER_DATE_FILENAME: _HOTSPOT_PER_DATE_COLUMNS,
        HOTSPOT_SUMMARY_FILENAME: _HOTSPOT_SUMMARY_COLUMNS,
        SENSOR_PER_DATE_FILENAME: _SENSOR_PER_DATE_COLUMNS,
        SENSOR_SUMMARY_FILENAME: _SENSOR_SUMMARY_COLUMNS,
        SENTINEL_STRATUM_FILENAME: _SENTINEL_STRATUM_COLUMNS,
        QA_MISSINGNESS_FILENAME: _QA_MISSINGNESS_COLUMNS,
        TRACT_MAP_SUMMARY_FILENAME: TRACT_MAP_SUMMARY_COLUMNS,
    }
)


class FinalEvaluationReportingError(ValueError):
    """Raised when final reporting inputs or settings violate the frozen contract."""


@dataclass(frozen=True)
class FinalEvaluationReportingSettings:
    """Frozen settings and input-column names for final reporting."""

    final_test_year: int = 2025
    baseline_model_id: str = "B1"
    primary_model_id: str = "M2"
    primary_metric: str = "equal_date_weighted_mae_c"
    evaluation_cohort: str = "all_date_usable_and_target_available_rows"
    minimum_usable_date_count_for_metrics: int = 1
    bootstrap_method: str = BOOTSTRAP_METHOD
    bootstrap_sampling_unit: str = BOOTSTRAP_SAMPLING_UNIT
    bootstrap_seed: int = 20_260_722
    bootstrap_replicates: int = 5_000
    confidence_level: float = 0.95
    minimum_relative_mae_improvement_fraction: float = 0.10
    minimum_median_per_date_spearman: float = 0.50
    uncertainty_relative_ci_lower_must_exceed: float = 0.0
    hotspot_positive_fraction: float = 0.20
    minimum_tract_footprint_fraction: float = 0.90
    minimum_valid_pixel_fraction: float = 0.60
    minimum_valid_pixels_per_tract: int = 20
    minimum_city_union_coverage_fraction: float = 0.98
    minimum_date_tract_retention_fraction: float = 0.50
    excluded_qa_pixel_bits: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 7)
    minimum_cloud_distance_km: float = 1.0
    apply_st_uncertainty_threshold: bool = False
    maximum_st_uncertainty_kelvin: float = 2.0
    exclude_terrain_occlusion: bool = True
    tract_column: str = "tract_geoid"
    date_column: str = "target_date"
    block_column: str = "spatial_block"
    sensor_column: str = "sensor"
    sentinel_available_column: str = "sentinel_available"
    target_available_column: str = "target_available"
    date_usable_column: str = "date_usable"
    relative_gate_column: str = "relative_endpoint_coverage_pass"
    hotspot_label_column: str = "relative_hotspot_top20"
    y_true_column: str = "y_true"
    baseline_prediction_column: str = "y_pred_b1"
    primary_prediction_column: str = "y_pred_m2"


@dataclass(frozen=True)
class FinalEvaluationReportTables:
    """All deterministic in-memory outputs from the pure reporting calculation."""

    evaluation_rows: pd.DataFrame
    model_metrics: pd.DataFrame
    per_date_metrics: pd.DataFrame
    paired_date_block_errors: pd.DataFrame
    crossed_bootstrap: Mapping[str, Any]
    protocol_gates: pd.DataFrame
    hotspot_per_date: pd.DataFrame
    hotspot_summary: pd.DataFrame
    sensor_per_date_metrics: pd.DataFrame
    sensor_summary: pd.DataFrame
    sentinel_stratum_summary: pd.DataFrame
    qa_missingness_summary: pd.DataFrame


@dataclass(frozen=True)
class FinalEvaluationReportArtifacts:
    """Calculated reports and the exact files atomically written to staging."""

    tables: FinalEvaluationReportTables
    tract_map_frame: gpd.GeoDataFrame
    output_paths: Mapping[str, Path]


def _validate_settings(settings: FinalEvaluationReportingSettings) -> None:
    if not isinstance(settings, FinalEvaluationReportingSettings):
        raise TypeError("settings must be FinalEvaluationReportingSettings.")
    integer_fields = {
        "final_test_year": settings.final_test_year,
        "minimum_usable_date_count_for_metrics": (
            settings.minimum_usable_date_count_for_metrics
        ),
        "bootstrap_seed": settings.bootstrap_seed,
        "bootstrap_replicates": settings.bootstrap_replicates,
        "minimum_valid_pixels_per_tract": settings.minimum_valid_pixels_per_tract,
    }
    for name, value in integer_fields.items():
        minimum = 0 if name == "bootstrap_seed" else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise FinalEvaluationReportingError(
                f"{name} must be an integer >= {minimum}."
            )
    numeric_fields = {
        "confidence_level": settings.confidence_level,
        "minimum_relative_mae_improvement_fraction": (
            settings.minimum_relative_mae_improvement_fraction
        ),
        "minimum_median_per_date_spearman": (
            settings.minimum_median_per_date_spearman
        ),
        "uncertainty_relative_ci_lower_must_exceed": (
            settings.uncertainty_relative_ci_lower_must_exceed
        ),
        "hotspot_positive_fraction": settings.hotspot_positive_fraction,
        "minimum_tract_footprint_fraction": (
            settings.minimum_tract_footprint_fraction
        ),
        "minimum_valid_pixel_fraction": settings.minimum_valid_pixel_fraction,
        "minimum_city_union_coverage_fraction": (
            settings.minimum_city_union_coverage_fraction
        ),
        "minimum_date_tract_retention_fraction": (
            settings.minimum_date_tract_retention_fraction
        ),
        "minimum_cloud_distance_km": settings.minimum_cloud_distance_km,
        "maximum_st_uncertainty_kelvin": (
            settings.maximum_st_uncertainty_kelvin
        ),
    }
    for name, value in numeric_fields.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise FinalEvaluationReportingError(f"{name} must be finite numeric.")
    if not 0.0 < settings.confidence_level < 1.0:
        raise FinalEvaluationReportingError(
            "confidence_level must lie strictly between zero and one."
        )
    if not 0.0 < settings.hotspot_positive_fraction < 1.0:
        raise FinalEvaluationReportingError(
            "hotspot_positive_fraction must lie strictly between zero and one."
        )
    fraction_fields = {
        "minimum_tract_footprint_fraction": (
            settings.minimum_tract_footprint_fraction
        ),
        "minimum_valid_pixel_fraction": settings.minimum_valid_pixel_fraction,
        "minimum_city_union_coverage_fraction": (
            settings.minimum_city_union_coverage_fraction
        ),
        "minimum_date_tract_retention_fraction": (
            settings.minimum_date_tract_retention_fraction
        ),
    }
    for name, value in fraction_fields.items():
        if not 0.0 <= float(value) <= 1.0:
            raise FinalEvaluationReportingError(
                f"{name} must lie between zero and one."
            )
    if settings.minimum_cloud_distance_km < 0.0:
        raise FinalEvaluationReportingError(
            "minimum_cloud_distance_km cannot be negative."
        )
    if settings.maximum_st_uncertainty_kelvin <= 0.0:
        raise FinalEvaluationReportingError(
            "maximum_st_uncertainty_kelvin must be positive."
        )
    if (
        not isinstance(settings.excluded_qa_pixel_bits, tuple)
        or not settings.excluded_qa_pixel_bits
        or len(set(settings.excluded_qa_pixel_bits))
        != len(settings.excluded_qa_pixel_bits)
        or any(
            isinstance(bit, bool) or not isinstance(bit, int) or bit < 0
            for bit in settings.excluded_qa_pixel_bits
        )
    ):
        raise FinalEvaluationReportingError(
            "excluded_qa_pixel_bits must be a unique non-empty tuple of non-negative integers."
        )
    for name, value in (
        ("apply_st_uncertainty_threshold", settings.apply_st_uncertainty_threshold),
        ("exclude_terrain_occlusion", settings.exclude_terrain_occlusion),
    ):
        if not isinstance(value, bool):
            raise FinalEvaluationReportingError(f"{name} must be boolean.")
    if settings.minimum_relative_mae_improvement_fraction < 0.0:
        raise FinalEvaluationReportingError(
            "minimum_relative_mae_improvement_fraction cannot be negative."
        )
    if not -1.0 <= settings.minimum_median_per_date_spearman <= 1.0:
        raise FinalEvaluationReportingError(
            "minimum_median_per_date_spearman must lie between -1 and 1."
        )
    if settings.bootstrap_method != BOOTSTRAP_METHOD:
        raise FinalEvaluationReportingError("Only crossed date-spatial-block bootstrap is legal.")
    if settings.bootstrap_sampling_unit != BOOTSTRAP_SAMPLING_UNIT:
        raise FinalEvaluationReportingError("Only complete-cluster sampling is legal.")
    if settings.primary_metric != "equal_date_weighted_mae_c":
        raise FinalEvaluationReportingError("The frozen primary metric cannot be changed.")
    if settings.evaluation_cohort != "all_date_usable_and_target_available_rows":
        raise FinalEvaluationReportingError("The frozen evaluation cohort cannot be changed.")
    if settings.baseline_model_id == settings.primary_model_id:
        raise FinalEvaluationReportingError("Baseline and primary model IDs must differ.")
    input_columns = (
        settings.tract_column,
        settings.date_column,
        settings.block_column,
        settings.sensor_column,
        settings.sentinel_available_column,
        settings.target_available_column,
        settings.date_usable_column,
        settings.relative_gate_column,
        settings.hotspot_label_column,
        settings.y_true_column,
        settings.baseline_prediction_column,
        settings.primary_prediction_column,
    )
    string_fields = (
        settings.baseline_model_id,
        settings.primary_model_id,
        *input_columns,
    )
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in string_fields
    ):
        raise FinalEvaluationReportingError(
            "Model IDs and input-column names must be normalized non-empty strings."
        )
    if len(set(input_columns)) != len(input_columns):
        raise FinalEvaluationReportingError("Every final-reporting input column must be distinct.")


def _civil_dates(values: pd.Series, *, name: str) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            raise FinalEvaluationReportingError(
                f"{name} at row {position} is numeric, not a civil date."
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise FinalEvaluationReportingError(
                f"{name} at row {position} is not parseable."
            ) from error
        if pd.isna(timestamp):
            raise FinalEvaluationReportingError(f"{name} at row {position} is missing.")
        if timestamp.tzinfo is not None or timestamp != timestamp.normalize():
            raise FinalEvaluationReportingError(
                f"{name} must contain timezone-naive civil-midnight dates."
            )
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[us]")


def _normalized_strings(values: pd.Series, *, name: str) -> pd.Series:
    valid = values.map(
        lambda value: isinstance(value, str)
        and bool(value)
        and value == value.strip()
    )
    if values.isna().any() or not valid.all():
        raise FinalEvaluationReportingError(
            f"{name} must contain normalized non-empty strings."
        )
    return values.astype("string")


def _strict_boolean(values: pd.Series, *, name: str) -> pd.Series:
    valid = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    if values.isna().any() or not valid.all():
        raise FinalEvaluationReportingError(
            f"{name} must contain only non-missing booleans."
        )
    return values.astype(bool)


def _finite_numeric(values: pd.Series, *, name: str) -> pd.Series:
    if is_bool_dtype(values.dtype) or not is_numeric_dtype(values.dtype):
        raise FinalEvaluationReportingError(f"{name} must have a numeric non-boolean dtype.")
    numeric = values.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(numeric).all():
        raise FinalEvaluationReportingError(f"{name} must contain only finite values.")
    return pd.Series(numeric, index=values.index, dtype=float)


def _prepare_joined_rows(
    rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a pandas DataFrame.")
    if rows.empty:
        raise FinalEvaluationReportingError("Joined final-evaluation rows are empty.")
    if rows.columns.duplicated().any():
        duplicates = rows.columns[rows.columns.duplicated()].tolist()
        raise FinalEvaluationReportingError(
            f"Joined rows have duplicate columns: {duplicates}."
        )
    required = {
        settings.tract_column,
        settings.date_column,
        settings.block_column,
        settings.sensor_column,
        settings.sentinel_available_column,
        settings.target_available_column,
        settings.date_usable_column,
        settings.relative_gate_column,
        settings.hotspot_label_column,
        settings.y_true_column,
        settings.baseline_prediction_column,
        settings.primary_prediction_column,
        *_QA_INPUT_COLUMNS,
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise FinalEvaluationReportingError(
            f"Joined rows are missing required columns: {missing}."
        )

    result = rows.copy()
    result[settings.tract_column] = _normalized_strings(
        result[settings.tract_column], name=settings.tract_column
    )
    if not result[settings.tract_column].str.fullmatch(r"\d{11}").all():
        raise FinalEvaluationReportingError(
            f"{settings.tract_column} must contain exact 11-digit Census GEOIDs."
        )
    result[settings.block_column] = _normalized_strings(
        result[settings.block_column], name=settings.block_column
    )
    result[settings.sensor_column] = _normalized_strings(
        result[settings.sensor_column], name=settings.sensor_column
    )
    result[settings.date_column] = _civil_dates(
        result[settings.date_column], name=settings.date_column
    )
    if not result[settings.date_column].dt.year.eq(settings.final_test_year).all():
        raise FinalEvaluationReportingError(
            f"Joined rows must contain only calendar year {settings.final_test_year}."
        )
    if result.duplicated(
        [settings.tract_column, settings.date_column], keep=False
    ).any():
        raise FinalEvaluationReportingError("Joined rows contain duplicate tract-date keys.")
    block_counts = result.groupby(settings.tract_column, observed=True)[
        settings.block_column
    ].nunique()
    if block_counts.ne(1).any():
        raise FinalEvaluationReportingError(
            "Each tract must retain one invariant spatial-block identity."
        )

    for column in (
        settings.sentinel_available_column,
        settings.target_available_column,
        settings.date_usable_column,
        settings.relative_gate_column,
    ):
        result[column] = _strict_boolean(result[column], name=column)

    for column in (
        settings.date_usable_column,
        settings.relative_gate_column,
        settings.sensor_column,
        "source_scene_count",
        "source_scene_ids",
        "union_city_coverage_fraction",
        "retained_tract_count",
        "retained_tract_fraction",
        "minimum_eligible_joint_cell_retention_fraction",
    ):
        per_date = result.groupby(settings.date_column, observed=True)[column].nunique(
            dropna=False
        )
        if per_date.ne(1).any():
            raise FinalEvaluationReportingError(
                f"{column} must be invariant within each physical overpass date."
            )
    reason_counts = result.groupby(settings.date_column, observed=True)[
        "date_exclusion_reason"
    ].nunique(dropna=False)
    if reason_counts.ne(1).any():
        raise FinalEvaluationReportingError(
            "date_exclusion_reason must be invariant within each physical overpass date."
        )

    for column in ("source_scene_ids", "tract_exclusion_reason", "date_exclusion_reason"):
        if result[column].isna().any() or not result[column].map(
            lambda value: isinstance(value, str)
        ).all():
            raise FinalEvaluationReportingError(
                f"{column} must contain only non-missing strings."
            )
    integer_qa_columns = (
        "source_scene_count",
        "rasterized_pixel_count",
        "footprint_pixel_count",
        "eligible_pixel_count_static",
        "valid_pixel_count",
        "retained_tract_count",
    )
    for column in integer_qa_columns:
        if is_bool_dtype(result[column].dtype) or not is_numeric_dtype(
            result[column].dtype
        ):
            raise FinalEvaluationReportingError(
                f"{column} must be numeric non-boolean."
            )
        values = result[column].to_numpy(dtype=float, na_value=np.nan)
        if (
            not np.isfinite(values).all()
            or (values < 0.0).any()
            or not np.equal(values, np.floor(values)).all()
        ):
            raise FinalEvaluationReportingError(
                f"{column} must contain non-negative integer counts."
            )
    for column in (
        "footprint_fraction",
        "valid_fraction",
        "union_city_coverage_fraction",
        "retained_tract_fraction",
        "minimum_eligible_joint_cell_retention_fraction",
    ):
        if is_bool_dtype(result[column].dtype) or not is_numeric_dtype(
            result[column].dtype
        ):
            raise FinalEvaluationReportingError(
                f"{column} must be numeric non-boolean."
            )
        values = result[column].to_numpy(dtype=float, na_value=np.nan)
        finite = values[np.isfinite(values)]
        if np.isinf(values).any() or (finite < 0.0).any() or (finite > 1.0).any():
            raise FinalEvaluationReportingError(
                f"{column} must contain fractions between zero and one."
            )
    for column in (
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
        "median_cloud_distance_km",
    ):
        if is_bool_dtype(result[column].dtype) or not is_numeric_dtype(
            result[column].dtype
        ):
            raise FinalEvaluationReportingError(
                f"{column} must be numeric non-boolean."
            )
        values = result[column].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any() or (values[np.isfinite(values)] < 0.0).any():
            raise FinalEvaluationReportingError(
                f"{column} must contain only non-negative finite values or missing."
            )

    for column in (
        settings.baseline_prediction_column,
        settings.primary_prediction_column,
    ):
        result[column] = _finite_numeric(result[column], name=column)

    truth = result[settings.y_true_column]
    if is_bool_dtype(truth.dtype) or not is_numeric_dtype(truth.dtype):
        raise FinalEvaluationReportingError(
            f"{settings.y_true_column} must have a numeric non-boolean dtype."
        )
    truth_values = truth.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(truth_values).any():
        raise FinalEvaluationReportingError(
            f"{settings.y_true_column} cannot contain infinite values."
        )
    target_available = result[settings.target_available_column]
    if not np.isfinite(truth_values[target_available.to_numpy()]).all():
        raise FinalEvaluationReportingError(
            "Every target_available row must contain a finite y_true."
        )
    if not np.isnan(truth_values[~target_available.to_numpy()]).all():
        raise FinalEvaluationReportingError(
            "Every target-unavailable row must keep y_true missing."
        )
    result[settings.y_true_column] = truth_values

    raw_labels = result[settings.hotspot_label_column]
    valid_labels = raw_labels.map(
        lambda value: isinstance(value, (bool, np.bool_))
        or value is None
        or value is pd.NA
        or (
            isinstance(value, (float, np.floating))
            and bool(np.isnan(value))
        )
    )
    if not valid_labels.all():
        raise FinalEvaluationReportingError(
            "Relative-hotspot labels must contain only booleans or missing values."
        )
    labels = raw_labels.astype("boolean")
    if labels.loc[~target_available].notna().any():
        raise FinalEvaluationReportingError(
            "Target-unavailable rows cannot carry relative-hotspot labels."
        )
    for _, date_rows in result.groupby(settings.date_column, sort=True):
        gate = bool(date_rows[settings.relative_gate_column].iloc[0])
        available = date_rows[settings.target_available_column]
        date_labels = labels.loc[date_rows.index]
        if not gate:
            if date_labels.notna().any():
                raise FinalEvaluationReportingError(
                    "Relative-hotspot labels exist on a date that failed the coverage gate."
                )
            continue
        if not date_labels.loc[available].notna().all():
            raise FinalEvaluationReportingError(
                "A coverage-gated available target lacks its frozen hotspot label."
            )
        available_rows = date_rows.loc[available].copy()
        expected = exact_top_k_mask(
            available_rows,
            score_column=settings.y_true_column,
            positive_fraction=settings.hotspot_positive_fraction,
        )
        observed = date_labels.loc[available_rows.index].astype(bool)
        if not observed.equals(expected):
            raise FinalEvaluationReportingError(
                "Frozen hotspot labels are not exact target-top-k with GEOID tie-break."
            )
    result[settings.hotspot_label_column] = labels
    return result.sort_values(
        [settings.date_column, settings.tract_column], kind="stable"
    ).reset_index(drop=True)


def _canonical_evaluation_rows(
    prepared: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> pd.DataFrame:
    cohort = prepared.loc[
        prepared[settings.date_usable_column]
        & prepared[settings.target_available_column]
    ].copy()
    date_count = int(cohort[settings.date_column].nunique())
    if date_count < settings.minimum_usable_date_count_for_metrics:
        raise FinalEvaluationReportingError(
            "The frozen evaluation cohort has fewer usable dates than the predeclared minimum."
        )
    rename = {
        settings.tract_column: "tract_geoid",
        settings.date_column: "target_date",
        settings.block_column: "spatial_block",
        settings.sensor_column: "sensor",
        settings.sentinel_available_column: "sentinel_available",
        settings.target_available_column: "target_available",
        settings.date_usable_column: "date_usable",
        settings.relative_gate_column: "relative_endpoint_coverage_pass",
        settings.hotspot_label_column: "relative_hotspot_top20",
        settings.y_true_column: "y_true",
        settings.baseline_prediction_column: "y_pred_b1",
        settings.primary_prediction_column: "y_pred_m2",
    }
    columns = [*rename, *_QA_INPUT_COLUMNS]
    result = cohort.loc[:, columns].rename(columns=rename)
    result["sentinel_stratum"] = np.where(
        result["sentinel_available"],
        SENTINEL_COMPLETE,
        SENTINEL_ALL_MISSING,
    )
    result["b1_error_c"] = result["y_pred_b1"] - result["y_true"]
    result["m2_error_c"] = result["y_pred_m2"] - result["y_true"]
    result["b1_absolute_error_c"] = result["b1_error_c"].abs()
    result["m2_absolute_error_c"] = result["m2_error_c"].abs()
    return result.loc[:, list(_EVALUATION_ROW_COLUMNS)].reset_index(drop=True)


def _model_evaluations(
    evaluation_rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    date_rows: list[dict[str, Any]] = []
    models = (
        (
            settings.baseline_model_id,
            "legal_baseline",
            "y_pred_b1",
        ),
        (
            settings.primary_model_id,
            "primary_frozen_model",
            "y_pred_m2",
        ),
    )
    sensor_by_date = evaluation_rows.groupby("target_date", sort=True)["sensor"].first()
    for model_id, role, prediction_column in models:
        surface = evaluation_rows.rename(columns={prediction_column: "y_pred"})
        evaluated = evaluate_absolute_lst_predictions(
            surface,
            final_test_year=settings.final_test_year,
            unlock_final_test=True,
        )
        summary = asdict(evaluated.summary)
        summaries.append(
            {
                "model_id": model_id,
                "model_role": role,
                "equal_date_weighted_mae_c": summary[
                    "primary_equal_date_weighted_mae_c"
                ],
                "pooled_rmse_c": summary["pooled_rmse_c"],
                "pooled_oos_r2": summary["pooled_oos_r2"],
                "pooled_mean_signed_error_c": summary[
                    "pooled_mean_signed_error_c"
                ],
                "equal_date_weighted_mean_signed_error_c": summary[
                    "equal_date_weighted_mean_signed_error_c"
                ],
                "equal_date_weighted_within_date_anomaly_mae_c": summary[
                    "equal_date_weighted_within_date_anomaly_mae_c"
                ],
                "median_per_date_spearman": summary["median_per_date_spearman"],
                "tract_date_row_count": summary["row_count"],
                "independent_date_count": summary["independent_date_count"],
                "independent_spatial_block_count": summary[
                    "independent_spatial_block_count"
                ],
                "spearman_defined_date_count": summary[
                    "spearman_defined_date_count"
                ],
                "spearman_undefined_date_count": summary[
                    "spearman_undefined_date_count"
                ],
            }
        )
        for record in evaluated.per_date.to_dict("records"):
            date = pd.Timestamp(record["target_date"])
            group = evaluation_rows.loc[evaluation_rows["target_date"].eq(date)]
            errors = group[prediction_column].to_numpy() - group["y_true"].to_numpy()
            date_rows.append(
                {
                    "model_id": model_id,
                    "model_role": role,
                    "target_date": date,
                    "tract_date_row_count": int(record["row_count"]),
                    "independent_spatial_block_count": int(
                        record["spatial_block_count"]
                    ),
                    "mae_c": float(record["mae_c"]),
                    "rmse_c": float(np.sqrt(np.mean(np.square(errors)))),
                    "mean_signed_error_c": float(record["mean_signed_error_c"]),
                    "within_date_anomaly_mae_c": float(
                        record["within_date_anomaly_mae_c"]
                    ),
                    "spearman_rho": float(record["spearman_rho"]),
                    "spearman_defined": bool(record["spearman_defined"]),
                    "_sensor": str(sensor_by_date.loc[date]),
                }
            )
    model_metrics = pd.DataFrame(summaries, columns=_MODEL_METRIC_COLUMNS)
    per_date = pd.DataFrame(date_rows)
    per_date = per_date.sort_values(
        ["target_date", "model_id"], kind="stable"
    ).reset_index(drop=True)
    return model_metrics, per_date


def _paired_bootstrap(
    evaluation_rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    surfaces: list[pd.DataFrame] = []
    for model_id, prediction_column in (
        (settings.baseline_model_id, "y_pred_b1"),
        (settings.primary_model_id, "y_pred_m2"),
    ):
        surface = evaluation_rows.loc[
            :, ["tract_geoid", "target_date", "spatial_block", "y_true", prediction_column]
        ].rename(columns={prediction_column: "y_pred"})
        surface["family"] = "final_test_2025"
        surface["model_id"] = model_id
        surfaces.append(surface)
    long = pd.concat(surfaces, ignore_index=True)
    try:
        cells = aggregate_paired_date_block_errors(
            long,
            family="final_test_2025",
            target_model_id=settings.primary_model_id,
            baseline_model_id=settings.baseline_model_id,
        )
        bootstrap = crossed_date_spatial_block_bootstrap(
            cells,
            seed=settings.bootstrap_seed,
            replicates=settings.bootstrap_replicates,
            confidence_level=settings.confidence_level,
            method=settings.bootstrap_method,
            sampling_unit=settings.bootstrap_sampling_unit,
            probability_threshold_fraction=(
                settings.minimum_relative_mae_improvement_fraction
            ),
        )
    except ValueError as error:
        raise FinalEvaluationReportingError(
            "Paired date-block aggregation or crossed bootstrap failed."
        ) from error
    required_finite = (
        "baseline_point_mae_c",
        "target_model_point_mae_c",
        "absolute_mae_improvement_c",
        "relative_mae_improvement_fraction",
        "relative_mae_improvement_ci_lower_fraction",
        "relative_mae_improvement_ci_upper_fraction",
    )
    if (
        float(bootstrap["baseline_point_mae_c"]) <= 0.0
        or not all(math.isfinite(float(bootstrap[name])) for name in required_finite)
    ):
        raise FinalEvaluationReportingError(
            "Crossed bootstrap produced an undefined relative-improvement estimand."
        )
    cells.insert(0, "baseline_model_id", settings.baseline_model_id)
    cells.insert(1, "primary_model_id", settings.primary_model_id)
    cells = cells.loc[:, list(_PAIRED_ERROR_COLUMNS)]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": REPORTING_ALGORITHM_VERSION,
        "state": "complete",
        "final_test_year": settings.final_test_year,
        "evaluation_cohort": settings.evaluation_cohort,
        "baseline_model_id": settings.baseline_model_id,
        "primary_model_id": settings.primary_model_id,
        **bootstrap,
    }
    return cells, payload


def _reconcile_bootstrap_point_estimates(
    model_metrics: pd.DataFrame,
    bootstrap: Mapping[str, Any],
    settings: FinalEvaluationReportingSettings,
) -> None:
    metrics = model_metrics.set_index("model_id")
    comparisons = (
        (
            settings.baseline_model_id,
            "baseline_point_mae_c",
        ),
        (
            settings.primary_model_id,
            "target_model_point_mae_c",
        ),
    )
    for model_id, bootstrap_field in comparisons:
        if model_id not in metrics.index:
            raise FinalEvaluationReportingError(
                f"Model summary is missing the frozen model {model_id}."
            )
        metric_value = float(
            metrics.loc[model_id, "equal_date_weighted_mae_c"]
        )
        bootstrap_value = float(bootstrap[bootstrap_field])
        if not np.isclose(
            metric_value,
            bootstrap_value,
            rtol=1e-12,
            atol=1e-12,
        ):
            raise FinalEvaluationReportingError(
                "Crossed-bootstrap point MAE does not reproduce the primary "
                f"model metric for {model_id}."
            )
    expected_rows = int(model_metrics["tract_date_row_count"].iloc[0])
    expected_dates = int(model_metrics["independent_date_count"].iloc[0])
    expected_blocks = int(
        model_metrics["independent_spatial_block_count"].iloc[0]
    )
    if (
        int(bootstrap["tract_date_row_count"]) != expected_rows
        or int(bootstrap["independent_date_count"]) != expected_dates
        or int(bootstrap["independent_spatial_block_count"]) != expected_blocks
    ):
        raise FinalEvaluationReportingError(
            "Crossed-bootstrap independent-unit counts do not reproduce model metrics."
        )
    if not isinstance(bootstrap, dict):
        raise FinalEvaluationReportingError(
            "Crossed-bootstrap payload must remain mutable until reconciliation."
        )
    bootstrap["point_estimate_reconciliation"] = {
        "primary_metric": settings.primary_metric,
        "baseline_model_id": settings.baseline_model_id,
        "baseline_model_metric_mae_c": float(
            metrics.loc[
                settings.baseline_model_id,
                "equal_date_weighted_mae_c",
            ]
        ),
        "baseline_bootstrap_point_mae_c": float(
            bootstrap["baseline_point_mae_c"]
        ),
        "primary_model_id": settings.primary_model_id,
        "primary_model_metric_mae_c": float(
            metrics.loc[
                settings.primary_model_id,
                "equal_date_weighted_mae_c",
            ]
        ),
        "primary_bootstrap_point_mae_c": float(
            bootstrap["target_model_point_mae_c"]
        ),
        "relative_tolerance": 1e-12,
        "absolute_tolerance": 1e-12,
        "point_estimates_reconciled": True,
    }


def _protocol_gates(
    model_metrics: pd.DataFrame,
    bootstrap: Mapping[str, Any],
    settings: FinalEvaluationReportingSettings,
) -> pd.DataFrame:
    target = model_metrics.loc[
        model_metrics["model_id"].eq(settings.primary_model_id)
    ]
    if len(target) != 1:
        raise FinalEvaluationReportingError("Primary-model metric summary is not unique.")
    spearman_value = target["median_per_date_spearman"].iloc[0]
    spearman = float(spearman_value) if pd.notna(spearman_value) else float("nan")
    point_relative = float(bootstrap["relative_mae_improvement_fraction"])
    ci_lower = float(bootstrap["relative_mae_improvement_ci_lower_fraction"])
    spearman_pass = bool(
        math.isfinite(spearman)
        and spearman >= settings.minimum_median_per_date_spearman
    )
    point_pass = point_relative >= settings.minimum_relative_mae_improvement_fraction
    uncertainty_pass = ci_lower > settings.uncertainty_relative_ci_lower_must_exceed
    ten_percent_ci_pass = (
        ci_lower > settings.minimum_relative_mae_improvement_fraction
    )
    overall = bool(spearman_pass and point_pass and uncertainty_pass)
    records = [
        {
            "gate_id": "median_per_date_spearman",
            "observed_value": spearman,
            "threshold": settings.minimum_median_per_date_spearman,
            "comparison": ">=",
            "passed": spearman_pass,
            "required_for_protocol_success": True,
            "interpretation": (
                "Primary-model median per-date Spearman reaches the frozen threshold."
            ),
        },
        {
            "gate_id": "point_relative_mae_improvement",
            "observed_value": point_relative,
            "threshold": settings.minimum_relative_mae_improvement_fraction,
            "comparison": ">=",
            "passed": bool(point_pass),
            "required_for_protocol_success": True,
            "interpretation": (
                "Primary-model equal-date MAE improvement over B1 reaches the frozen threshold."
            ),
        },
        {
            "gate_id": "uncertainty_supports_positive_improvement",
            "observed_value": ci_lower,
            "threshold": settings.uncertainty_relative_ci_lower_must_exceed,
            "comparison": ">",
            "passed": bool(uncertainty_pass),
            "required_for_protocol_success": True,
            "interpretation": (
                "Crossed-bootstrap relative-improvement CI lower bound exceeds the frozen floor."
            ),
        },
        {
            "gate_id": "uncertainty_supports_full_threshold_improvement",
            "observed_value": ci_lower,
            "threshold": settings.minimum_relative_mae_improvement_fraction,
            "comparison": ">",
            "passed": bool(ten_percent_ci_pass),
            "required_for_protocol_success": False,
            "interpretation": (
                "Descriptive stronger check; it is not substituted for the protocol gates."
            ),
        },
    ]
    result = pd.DataFrame(records, columns=_PROTOCOL_GATE_COLUMNS[:-1])
    result["overall_protocol_success_gate_pass"] = overall
    return result.loc[:, list(_PROTOCOL_GATE_COLUMNS)]


def _hotspot_diagnostics(
    evaluation_rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    gated = evaluation_rows.loc[
        evaluation_rows["relative_endpoint_coverage_pass"]
    ].copy()
    rows: list[dict[str, Any]] = []
    models = (
        (settings.baseline_model_id, "legal_baseline", "y_pred_b1"),
        (settings.primary_model_id, "primary_frozen_model", "y_pred_m2"),
    )
    for target_date, group in gated.groupby("target_date", sort=True):
        labels = group["relative_hotspot_top20"].astype(bool)
        expected_k = math.ceil(settings.hotspot_positive_fraction * len(group))
        if int(labels.sum()) != expected_k:
            raise FinalEvaluationReportingError(
                "Coverage-gated hotspot labels no longer contain the frozen exact top-k."
            )
        for model_id, role, prediction_column in models:
            predicted = exact_top_k_mask(
                group,
                score_column=prediction_column,
                positive_fraction=settings.hotspot_positive_fraction,
            )
            true_positive = int((predicted & labels).sum())
            false_positive = int((predicted & ~labels).sum())
            false_negative = int((~predicted & labels).sum())
            rows.append(
                {
                    "model_id": model_id,
                    "model_role": role,
                    "target_date": pd.Timestamp(target_date),
                    "sensor": str(group["sensor"].iloc[0]),
                    "tract_date_row_count": int(len(group)),
                    "independent_spatial_block_count": int(
                        group["spatial_block"].nunique()
                    ),
                    "exact_top_k": expected_k,
                    "observed_positive_count": int(labels.sum()),
                    "predicted_positive_count": int(predicted.sum()),
                    "true_positive_count": true_positive,
                    "false_positive_count": false_positive,
                    "false_negative_count": false_negative,
                    "average_precision": continuous_average_precision(
                        labels.to_numpy(),
                        group[prediction_column].to_numpy(dtype=float),
                    ),
                    "precision_at_k": true_positive / expected_k,
                    "recall_at_k": true_positive / expected_k,
                    "false_negative_rate": false_negative / expected_k,
                }
            )
    if rows:
        per_date = pd.DataFrame(rows, columns=_HOTSPOT_PER_DATE_COLUMNS)
        per_date = per_date.sort_values(
            ["target_date", "model_id"], kind="stable"
        ).reset_index(drop=True)
    else:
        per_date = pd.DataFrame(columns=_HOTSPOT_PER_DATE_COLUMNS)

    summaries: list[dict[str, Any]] = []
    for model_id, role, _ in models:
        metrics = per_date.loc[per_date["model_id"].eq(model_id)]
        source = gated
        summaries.append(
            {
                "model_id": model_id,
                "model_role": role,
                "tract_date_row_count": int(len(source)),
                "independent_date_count": int(source["target_date"].nunique()),
                "independent_spatial_block_count": int(
                    source["spatial_block"].nunique()
                ),
                "mean_per_date_average_precision": (
                    float(metrics["average_precision"].mean())
                    if not metrics.empty
                    else np.nan
                ),
                "mean_per_date_precision_at_k": (
                    float(metrics["precision_at_k"].mean())
                    if not metrics.empty
                    else np.nan
                ),
                "mean_per_date_recall_at_k": (
                    float(metrics["recall_at_k"].mean())
                    if not metrics.empty
                    else np.nan
                ),
                "mean_per_date_false_negative_rate": (
                    float(metrics["false_negative_rate"].mean())
                    if not metrics.empty
                    else np.nan
                ),
            }
        )
    return (
        per_date,
        pd.DataFrame(summaries, columns=_HOTSPOT_SUMMARY_COLUMNS),
    )


def _sensor_diagnostics(
    evaluation_rows: pd.DataFrame,
    per_date_metrics: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sensor_per_date = per_date_metrics.rename(columns={"_sensor": "sensor"}).copy()
    sensor_per_date = sensor_per_date.loc[
        :, [*_PER_DATE_COLUMNS, "sensor"]
    ]
    sensor_per_date = sensor_per_date.loc[
        :,
        [
            "model_id",
            "model_role",
            "target_date",
            "sensor",
            *_PER_DATE_COLUMNS[3:],
        ],
    ]
    summaries: list[dict[str, Any]] = []
    model_columns = (
        (settings.baseline_model_id, "legal_baseline", "y_pred_b1"),
        (settings.primary_model_id, "primary_frozen_model", "y_pred_m2"),
    )
    for model_id, role, prediction_column in model_columns:
        for sensor, source in evaluation_rows.groupby("sensor", sort=True):
            surface = source.rename(columns={prediction_column: "y_pred"})
            evaluated = evaluate_absolute_lst_predictions(
                surface,
                final_test_year=settings.final_test_year,
                unlock_final_test=True,
            )
            metric = asdict(evaluated.summary)
            summaries.append(
                {
                    "model_id": model_id,
                    "model_role": role,
                    "sensor": str(sensor),
                    "tract_date_row_count": int(metric["row_count"]),
                    "independent_date_count": int(metric["independent_date_count"]),
                    "independent_spatial_block_count": int(
                        metric["independent_spatial_block_count"]
                    ),
                    "equal_date_weighted_mae_c": float(
                        metric["primary_equal_date_weighted_mae_c"]
                    ),
                    "pooled_rmse_c": float(metric["pooled_rmse_c"]),
                    "pooled_mean_signed_error_c": float(
                        metric["pooled_mean_signed_error_c"]
                    ),
                    "median_per_date_spearman": metric[
                        "median_per_date_spearman"
                    ],
                    "spearman_defined_date_count": int(
                        metric["spearman_defined_date_count"]
                    ),
                    "spearman_undefined_date_count": int(
                        metric["spearman_undefined_date_count"]
                    ),
                }
            )
    sensor_per_date = sensor_per_date.loc[:, list(_SENSOR_PER_DATE_COLUMNS)]
    summary = pd.DataFrame(
        summaries,
        columns=_SENSOR_SUMMARY_COLUMNS,
    ).sort_values(
        ["sensor", "model_id"], kind="stable"
    ).reset_index(drop=True)
    return sensor_per_date, summary


def _sentinel_diagnostics(
    evaluation_rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    model_columns = (
        (settings.baseline_model_id, "legal_baseline", "y_pred_b1"),
        (settings.primary_model_id, "primary_frozen_model", "y_pred_m2"),
    )
    for model_id, role, prediction_column in model_columns:
        for stratum in (SENTINEL_COMPLETE, SENTINEL_ALL_MISSING):
            source = evaluation_rows.loc[
                evaluation_rows["sentinel_stratum"].eq(stratum)
            ]
            if source.empty:
                rows.append(
                    {
                        "model_id": model_id,
                        "model_role": role,
                        "sentinel_stratum": stratum,
                        "tract_date_row_count": 0,
                        "independent_date_count": 0,
                        "independent_spatial_block_count": 0,
                        "equal_date_weighted_mae_c": np.nan,
                        "pooled_rmse_c": np.nan,
                        "pooled_mean_signed_error_c": np.nan,
                        "median_per_date_spearman": np.nan,
                        "spearman_defined_date_count": 0,
                        "spearman_undefined_date_count": 0,
                    }
                )
                continue
            surface = source.rename(columns={prediction_column: "y_pred"})
            evaluated = evaluate_absolute_lst_predictions(
                surface,
                final_test_year=settings.final_test_year,
                unlock_final_test=True,
            )
            metric = asdict(evaluated.summary)
            rows.append(
                {
                    "model_id": model_id,
                    "model_role": role,
                    "sentinel_stratum": stratum,
                    "tract_date_row_count": int(metric["row_count"]),
                    "independent_date_count": int(metric["independent_date_count"]),
                    "independent_spatial_block_count": int(
                        metric["independent_spatial_block_count"]
                    ),
                    "equal_date_weighted_mae_c": float(
                        metric["primary_equal_date_weighted_mae_c"]
                    ),
                    "pooled_rmse_c": float(metric["pooled_rmse_c"]),
                    "pooled_mean_signed_error_c": float(
                        metric["pooled_mean_signed_error_c"]
                    ),
                    "median_per_date_spearman": metric[
                        "median_per_date_spearman"
                    ],
                    "spearman_defined_date_count": int(
                        metric["spearman_defined_date_count"]
                    ),
                    "spearman_undefined_date_count": int(
                        metric["spearman_undefined_date_count"]
                    ),
                }
            )
    return pd.DataFrame(rows, columns=_SENTINEL_STRATUM_COLUMNS)


def _reason_counts_json(values: pd.Series | None) -> str:
    if values is None:
        return json.dumps({"not_supplied": 0}, sort_keys=True, separators=(",", ":"))
    normalized = values.fillna("<missing>").astype(str)
    counts = {
        key: int(value)
        for key, value in sorted(normalized.value_counts(dropna=False).items())
    }
    return json.dumps(counts, sort_keys=True, separators=(",", ":"))


def _qa_missingness_summary(
    prepared: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    qa_rules = json.dumps(
        {
            "excluded_qa_pixel_bits": list(settings.excluded_qa_pixel_bits),
            "exclude_terrain_occlusion": settings.exclude_terrain_occlusion,
            "minimum_cloud_distance_km": settings.minimum_cloud_distance_km,
            "minimum_tract_footprint_fraction": (
                settings.minimum_tract_footprint_fraction
            ),
            "minimum_valid_pixel_fraction": (
                settings.minimum_valid_pixel_fraction
            ),
            "minimum_valid_pixels_per_tract": (
                settings.minimum_valid_pixels_per_tract
            ),
            "minimum_city_union_coverage_fraction": (
                settings.minimum_city_union_coverage_fraction
            ),
            "minimum_date_tract_retention_fraction": (
                settings.minimum_date_tract_retention_fraction
            ),
            "st_qa_primary_filter_applied": (
                settings.apply_st_uncertainty_threshold
            ),
            "st_qa_sensitivity_threshold_kelvin": (
                settings.maximum_st_uncertainty_kelvin
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    def finite_stat(
        group: pd.DataFrame,
        column: str,
        statistic: str,
    ) -> float:
        values = group[column].to_numpy(dtype=float, na_value=np.nan)
        finite = values[np.isfinite(values)]
        if not len(finite):
            return float("nan")
        if statistic == "mean":
            return float(np.mean(finite))
        if statistic == "median":
            return float(np.median(finite))
        if statistic == "p10":
            return float(np.quantile(finite, 0.10))
        if statistic == "p90":
            return float(np.quantile(finite, 0.90))
        raise AssertionError(statistic)

    def record_for(group: pd.DataFrame, *, level: str) -> dict[str, Any]:
        available = group[settings.target_available_column]
        usable = group[settings.date_usable_column]
        relative_gate = group[settings.relative_gate_column]
        sentinel = group[settings.sentinel_available_column]
        cohort = available & usable
        labels = group[settings.hotspot_label_column]
        eligible_pixels = group["eligible_pixel_count_static"].to_numpy(
            dtype=float
        )
        valid_pixels = group["valid_pixel_count"].to_numpy(dtype=float)
        rasterized_pixels = group["rasterized_pixel_count"].to_numpy(dtype=float)
        footprint_pixels = group["footprint_pixel_count"].to_numpy(dtype=float)
        tract_failure = group["tract_exclusion_reason"].ne("")
        date_failure = group["date_exclusion_reason"].ne("")
        date_level = group.drop_duplicates(settings.date_column)
        static_level = group.drop_duplicates(settings.tract_column)
        return {
            "summary_level": level,
            "target_date": (
                pd.Timestamp(group[settings.date_column].iloc[0])
                if level == "date"
                else pd.NaT
            ),
            "sensor": (
                str(group[settings.sensor_column].iloc[0]) if level == "date" else "ALL"
            ),
            "inventory_key_count": int(len(group)),
            "independent_date_count": int(group[settings.date_column].nunique()),
            "independent_spatial_block_count": int(
                group[settings.block_column].nunique()
            ),
            "target_available_count": int(available.sum()),
            "target_unavailable_count": int((~available).sum()),
            "target_availability_fraction": float(available.mean()),
            "eligible_pixel_count_date_tract_sum": int(eligible_pixels.sum()),
            "valid_pixel_count_date_tract_sum": int(valid_pixels.sum()),
            "valid_pixel_fraction_of_static_eligible_date_tract_sum": (
                float(valid_pixels.sum() / eligible_pixels.sum())
                if eligible_pixels.sum() > 0
                else float("nan")
            ),
            "median_tract_valid_pixel_count": finite_stat(
                group, "valid_pixel_count", "median"
            ),
            "p10_tract_valid_pixel_count": finite_stat(
                group, "valid_pixel_count", "p10"
            ),
            "mean_tract_valid_fraction": finite_stat(
                group, "valid_fraction", "mean"
            ),
            "median_tract_valid_fraction": finite_stat(
                group, "valid_fraction", "median"
            ),
            "static_eligible_pixel_count_unique_tract_sum": int(
                static_level["eligible_pixel_count_static"].sum()
            ),
            "rasterized_pixel_count_date_tract_sum": int(
                rasterized_pixels.sum()
            ),
            "footprint_pixel_count_date_tract_sum": int(
                footprint_pixels.sum()
            ),
            "rasterized_pixel_count_unique_tract_sum": int(
                static_level["rasterized_pixel_count"].sum()
            ),
            "footprint_pixel_fraction_of_rasterized_date_tract_sum": (
                float(footprint_pixels.sum() / rasterized_pixels.sum())
                if rasterized_pixels.sum() > 0
                else float("nan")
            ),
            "mean_tract_footprint_fraction": finite_stat(
                group, "footprint_fraction", "mean"
            ),
            "median_tract_st_uncertainty_k": finite_stat(
                group, "median_st_uncertainty_k", "median"
            ),
            "p90_tract_st_uncertainty_k": finite_stat(
                group, "p90_st_uncertainty_k", "p90"
            ),
            "median_tract_cloud_distance_km": finite_stat(
                group, "median_cloud_distance_km", "median"
            ),
            "date_usable": (
                bool(usable.iloc[0]) if level == "date" else pd.NA
            ),
            "evaluation_cohort_count": int(cohort.sum()),
            "evaluation_excluded_count": int((~cohort).sum()),
            "usable_date_count": int(
                date_level[settings.date_usable_column].sum()
            ),
            "excluded_date_count": int(
                (~date_level[settings.date_usable_column]).sum()
            ),
            "relative_gate_date_count": int(
                date_level[settings.relative_gate_column].sum()
            ),
            "evaluation_independent_date_count": int(
                group.loc[cohort, settings.date_column].nunique()
            ),
            "relative_endpoint_coverage_pass": (
                bool(relative_gate.iloc[0]) if level == "date" else pd.NA
            ),
            "relative_hotspot_labeled_count": int(labels.notna().sum()),
            "sentinel_available_count": int(sentinel.sum()),
            "sentinel_all_five_missing_count": int((~sentinel).sum()),
            "sentinel_availability_fraction": float(sentinel.mean()),
            "evaluation_sentinel_available_count": int(
                (sentinel & cohort).sum()
            ),
            "evaluation_sentinel_all_five_missing_count": int(
                ((~sentinel) & cohort).sum()
            ),
            "tract_qa_failure_count": int(tract_failure.sum()),
            "date_qa_failure_key_count": int(date_failure.sum()),
            "tract_exclusion_counts_json": _reason_counts_json(
                group["tract_exclusion_reason"]
            ),
            "date_exclusion_counts_json": _reason_counts_json(
                group["date_exclusion_reason"]
            ),
            "date_exclusion_reason": (
                str(group["date_exclusion_reason"].iloc[0])
                if level == "date"
                else "ALL"
            ),
            "union_city_coverage_fraction": (
                float(group["union_city_coverage_fraction"].iloc[0])
                if level == "date"
                else float("nan")
            ),
            "retained_tract_count": (
                int(group["retained_tract_count"].iloc[0])
                if level == "date"
                else int(available.sum())
            ),
            "retained_tract_fraction": (
                float(group["retained_tract_fraction"].iloc[0])
                if level == "date"
                else float(available.mean())
            ),
            "minimum_eligible_joint_cell_retention_fraction": (
                float(
                    group[
                        "minimum_eligible_joint_cell_retention_fraction"
                    ].iloc[0]
                )
                if level == "date"
                else float("nan")
            ),
            "qa_rules_json": qa_rules,
        }

    records.append(record_for(prepared, level="overall"))
    for _, group in prepared.groupby(settings.date_column, sort=True):
        records.append(record_for(group, level="date"))
    return pd.DataFrame(records, columns=_QA_MISSINGNESS_COLUMNS)


def build_final_evaluation_reporting(
    rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
) -> FinalEvaluationReportTables:
    """Purely validate and calculate all frozen final-evaluation reports."""

    _validate_settings(settings)
    prepared = _prepare_joined_rows(rows, settings)
    evaluation_rows = _canonical_evaluation_rows(prepared, settings)
    model_metrics, per_date_internal = _model_evaluations(
        evaluation_rows, settings
    )
    paired, bootstrap = _paired_bootstrap(evaluation_rows, settings)
    _reconcile_bootstrap_point_estimates(model_metrics, bootstrap, settings)
    gates = _protocol_gates(model_metrics, bootstrap, settings)
    hotspot_per_date, hotspot_summary = _hotspot_diagnostics(
        evaluation_rows, settings
    )
    sensor_per_date, sensor_summary = _sensor_diagnostics(
        evaluation_rows, per_date_internal, settings
    )
    per_date = per_date_internal.loc[:, list(_PER_DATE_COLUMNS)].copy()
    sentinel = _sentinel_diagnostics(evaluation_rows, settings)
    qa = _qa_missingness_summary(prepared, settings)
    return FinalEvaluationReportTables(
        evaluation_rows=evaluation_rows,
        model_metrics=model_metrics,
        per_date_metrics=per_date,
        paired_date_block_errors=paired,
        crossed_bootstrap=MappingProxyType(bootstrap),
        protocol_gates=gates,
        hotspot_per_date=hotspot_per_date,
        hotspot_summary=hotspot_summary,
        sensor_per_date_metrics=sensor_per_date,
        sensor_summary=sensor_summary,
        sentinel_stratum_summary=sentinel,
        qa_missingness_summary=qa,
    )


def _atomic_png(figure: Figure, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".partial")
    figure.savefig(
        temporary,
        format="png",
        dpi=180,
        bbox_inches="tight",
        metadata={"Software": REPORTING_ALGORITHM_VERSION},
    )
    temporary.replace(destination)


def _prepare_tract_map_frame(
    rows: pd.DataFrame,
    tract_geometries: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Aggregate all usable dates to authenticated tract geometries."""

    if not isinstance(tract_geometries, gpd.GeoDataFrame):
        raise FinalEvaluationReportingError(
            "tract_geometries must be an authenticated GeoDataFrame."
        )
    if tract_geometries.empty or tract_geometries.crs is None:
        raise FinalEvaluationReportingError(
            "tract_geometries must be non-empty and georeferenced."
        )
    geometry_column = tract_geometries.geometry.name
    if (
        "GEOID" not in tract_geometries
        or "spatial_block" not in tract_geometries
        or tract_geometries.geometry.isna().any()
    ):
        raise FinalEvaluationReportingError(
            "tract_geometries must contain GEOID, spatial_block, and geometry fields."
        )
    geography = tract_geometries.loc[
        :, ["GEOID", "spatial_block", geometry_column]
    ].copy()
    geography["tract_geoid"] = geography["GEOID"].astype("string")
    if (
        geography["tract_geoid"].isna().any()
        or not geography["tract_geoid"].str.fullmatch(r"\d{11}").all()
        or geography["tract_geoid"].duplicated().any()
    ):
        raise FinalEvaluationReportingError(
            "Authenticated tract geometries contain invalid or duplicate GEOIDs."
        )
    geography["spatial_block"] = _normalized_strings(
        geography["spatial_block"],
        name="tract_geometries.spatial_block",
    )
    row_geoids = set(rows["tract_geoid"].astype(str))
    geometry_geoids = set(geography["tract_geoid"].astype(str))
    if not row_geoids.issubset(geometry_geoids):
        raise FinalEvaluationReportingError(
            "Evaluation rows contain a tract outside the authenticated geography."
        )
    means = (
        rows.groupby("tract_geoid", observed=True, sort=True)
        .agg(
            evaluated_date_count=("target_date", "nunique"),
            observed_lst_c=("y_true", "mean"),
            b1_predicted_lst_c=("y_pred_b1", "mean"),
            m2_predicted_lst_c=("y_pred_m2", "mean"),
            b1_residual_c=("b1_error_c", "mean"),
            m2_residual_c=("m2_error_c", "mean"),
            b1_mean_absolute_error_c=("b1_absolute_error_c", "mean"),
            m2_mean_absolute_error_c=("m2_absolute_error_c", "mean"),
        )
        .reset_index()
    )
    usable_date_count = int(rows["target_date"].nunique())
    means["evaluated_date_fraction"] = (
        means["evaluated_date_count"] / usable_date_count
    )
    result = geography.drop(columns="GEOID").merge(
        means,
        on="tract_geoid",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    result["evaluated_date_count"] = (
        result["evaluated_date_count"].fillna(0).astype(int)
    )
    result["evaluated_date_fraction"] = result[
        "evaluated_date_fraction"
    ].fillna(0.0)
    result = result.loc[
        :, [*TRACT_MAP_SUMMARY_COLUMNS, geometry_column]
    ]
    return (
        gpd.GeoDataFrame(result, geometry=geometry_column, crs=tract_geometries.crs)
        .sort_values("tract_geoid", kind="stable")
        .reset_index(drop=True)
    )


def _write_tract_maps(
    map_frame: gpd.GeoDataFrame,
    destination: Path,
) -> None:
    temperature_columns = (
        "observed_lst_c",
        "b1_predicted_lst_c",
        "m2_predicted_lst_c",
    )
    residual_columns = ("b1_residual_c", "m2_residual_c")
    temperature = map_frame.loc[:, list(temperature_columns)].to_numpy(dtype=float)
    finite_temperature = temperature[np.isfinite(temperature)]
    residual = np.abs(
        map_frame.loc[:, list(residual_columns)].to_numpy(dtype=float)
    )
    finite_residual = residual[np.isfinite(residual)]
    if not len(finite_temperature) or not len(finite_residual):
        raise FinalEvaluationReportingError(
            "Tract maps require finite temperature and residual values."
        )
    temp_limits = (
        float(np.quantile(finite_temperature, 0.01)),
        float(np.quantile(finite_temperature, 0.99)),
    )
    if np.isclose(*temp_limits, rtol=0.0, atol=1e-12):
        temp_limits = (temp_limits[0] - 1e-9, temp_limits[1] + 1e-9)
    residual_limit = max(float(np.quantile(finite_residual, 0.99)), 1e-9)
    panels = (
        ("observed_lst_c", "Observed QA-filtered LST", "inferno", temp_limits),
        ("b1_predicted_lst_c", "B1 predicted LST", "inferno", temp_limits),
        ("m2_predicted_lst_c", "M2 predicted LST", "inferno", temp_limits),
        (
            "b1_residual_c",
            "B1 residual (prediction - observed)",
            "RdBu_r",
            (-residual_limit, residual_limit),
        ),
        (
            "m2_residual_c",
            "M2 residual (prediction - observed)",
            "RdBu_r",
            (-residual_limit, residual_limit),
        ),
        (
            "evaluated_date_fraction",
            "Fraction of usable dates with an available tract target",
            "viridis",
            (0.0, 1.0),
        ),
    )
    temporary = destination.with_suffix(destination.suffix + ".partial")
    fixed_pdf_timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    metadata = {
        "Title": "Observed, predicted, and residual tract maps",
        "Author": REPORTING_ALGORITHM_VERSION,
        "CreationDate": fixed_pdf_timestamp,
        "ModDate": fixed_pdf_timestamp,
    }
    with PdfPages(temporary, metadata=metadata) as pdf:
        for column, title, cmap, limits in panels:
            figure = Figure(figsize=(10, 9))
            axis = figure.subplots()
            map_frame.plot(
                column=column,
                ax=axis,
                cmap=cmap,
                vmin=limits[0],
                vmax=limits[1],
                legend=True,
                linewidth=0.08,
                edgecolor="#666666",
                missing_kwds={"color": "#D9D9D9"},
            )
            axis.set_title(
                f"{title}\nAll frozen usable dates; matched available dates per tract"
            )
            axis.set_axis_off()
            figure.tight_layout()
            pdf.savefig(figure)
            figure.clear()
    temporary.replace(destination)


def _write_per_date_figure(
    per_date: pd.DataFrame,
    destination: Path,
    settings: FinalEvaluationReportingSettings,
) -> None:
    figure = Figure(figsize=(12, 7))
    mae_axis = figure.subplots()
    rank_axis = mae_axis.twinx()
    colors = {
        settings.baseline_model_id: "#5B6770",
        settings.primary_model_id: "#D55E00",
    }
    for model_id in (settings.baseline_model_id, settings.primary_model_id):
        model = per_date.loc[per_date["model_id"].eq(model_id)].sort_values(
            "target_date"
        )
        mae_axis.plot(
            model["target_date"],
            model["mae_c"],
            marker="o",
            linewidth=1.8,
            color=colors[model_id],
            label=f"{model_id} MAE",
        )
        rank_axis.plot(
            model["target_date"],
            model["spearman_rho"],
            marker="s",
            linestyle="--",
            linewidth=1.2,
            alpha=0.75,
            color=colors[model_id],
            label=f"{model_id} Spearman",
        )
    mae_axis.set_title("Per-date absolute error and neighborhood rank performance")
    mae_axis.set_xlabel("Physical overpass date")
    mae_axis.set_ylabel("MAE (°C)")
    rank_axis.set_ylabel("Spearman ρ")
    rank_axis.set_ylim(-1.05, 1.05)
    mae_axis.grid(axis="y", alpha=0.25)
    handles1, labels1 = mae_axis.get_legend_handles_labels()
    handles2, labels2 = rank_axis.get_legend_handles_labels()
    mae_axis.legend(handles1 + handles2, labels1 + labels2, ncol=2, loc="best")
    figure.autofmt_xdate()
    figure.tight_layout()
    _atomic_png(figure, destination)
    figure.clear()


def _write_hotspot_figure(
    per_date: pd.DataFrame,
    destination: Path,
    settings: FinalEvaluationReportingSettings,
) -> None:
    figure = Figure(figsize=(12, 7))
    average_precision_axis, precision_axis = figure.subplots(2, 1, sharex=True)
    colors = {
        settings.baseline_model_id: "#5B6770",
        settings.primary_model_id: "#D55E00",
    }
    if per_date.empty:
        for axis in (average_precision_axis, precision_axis):
            axis.text(
                0.5,
                0.5,
                "No usable date passed the frozen relative-endpoint coverage gate",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_xticks([])
            axis.set_yticks([])
    else:
        for model_id in (settings.baseline_model_id, settings.primary_model_id):
            model = per_date.loc[per_date["model_id"].eq(model_id)].sort_values(
                "target_date"
            )
            average_precision_axis.plot(
                model["target_date"],
                model["average_precision"],
                marker="o",
                color=colors[model_id],
                label=f"{model_id} average precision",
            )
            precision_axis.plot(
                model["target_date"],
                model["precision_at_k"],
                marker="o",
                color=colors[model_id],
                label=f"{model_id} precision@k",
            )
            precision_axis.plot(
                model["target_date"],
                model["recall_at_k"],
                linestyle="--",
                color=colors[model_id],
                label=f"{model_id} recall@k",
            )
        for axis in (average_precision_axis, precision_axis):
            axis.set_ylim(-0.02, 1.02)
            axis.grid(axis="y", alpha=0.25)
            axis.legend(loc="best", ncol=2)
        average_precision_axis.set_ylabel("Average precision")
        precision_axis.set_ylabel("Exact top-k rate")
        precision_axis.set_xlabel("Coverage-gated physical overpass date")
        figure.autofmt_xdate()
    figure.suptitle(
        "Frozen exact-top-20% neighborhood hotspot diagnostics\n"
        "Continuous prediction scores; GEOID tie-break"
    )
    figure.tight_layout()
    _atomic_png(figure, destination)
    figure.clear()


def generate_final_evaluation_reports(
    rows: pd.DataFrame,
    settings: FinalEvaluationReportingSettings,
    staging_directory: str | Path,
    *,
    tract_geometries: gpd.GeoDataFrame,
) -> FinalEvaluationReportArtifacts:
    """Calculate and atomically write the exact reporting outputs to staging."""

    tables = build_final_evaluation_reporting(rows, settings)
    map_frame = _prepare_tract_map_frame(
        tables.evaluation_rows,
        tract_geometries,
    )
    staging = Path(staging_directory).resolve()
    if staging.exists() and not staging.is_dir():
        raise FinalEvaluationReportingError("staging_directory exists but is not a directory.")
    staging.mkdir(parents=True, exist_ok=True)
    frames = {
        MODEL_METRICS_FILENAME: tables.model_metrics,
        PER_DATE_METRICS_FILENAME: tables.per_date_metrics,
        PAIRED_ERRORS_FILENAME: tables.paired_date_block_errors,
        GATES_FILENAME: tables.protocol_gates,
        HOTSPOT_PER_DATE_FILENAME: tables.hotspot_per_date,
        HOTSPOT_SUMMARY_FILENAME: tables.hotspot_summary,
        SENSOR_PER_DATE_FILENAME: tables.sensor_per_date_metrics,
        SENSOR_SUMMARY_FILENAME: tables.sensor_summary,
        SENTINEL_STRATUM_FILENAME: tables.sentinel_stratum_summary,
        QA_MISSINGNESS_FILENAME: tables.qa_missingness_summary,
        TRACT_MAP_SUMMARY_FILENAME: pd.DataFrame(
            map_frame.drop(columns=map_frame.geometry.name)
        ),
    }
    paths = {name: staging / name for name in REPORT_OUTPUT_FILENAMES}
    for filename, frame in frames.items():
        expected_columns = REPORT_TABLE_COLUMN_CONTRACTS[filename]
        if frame.columns.tolist() != list(expected_columns):
            raise FinalEvaluationReportingError(
                f"Report table column contract drifted: {filename}"
            )
        atomic_csv(frame, paths[filename])
    atomic_json(dict(tables.crossed_bootstrap), paths[BOOTSTRAP_FILENAME])
    _write_tract_maps(map_frame, paths[MAP_FIGURE_FILENAME])
    _write_per_date_figure(
        tables.per_date_metrics,
        paths[DATE_FIGURE_FILENAME],
        settings,
    )
    _write_hotspot_figure(
        tables.hotspot_per_date,
        paths[HOTSPOT_FIGURE_FILENAME],
        settings,
    )
    return FinalEvaluationReportArtifacts(
        tables=tables,
        tract_map_frame=map_frame,
        output_paths=MappingProxyType(paths),
    )
