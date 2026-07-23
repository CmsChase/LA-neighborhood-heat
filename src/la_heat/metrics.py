"""Audited metrics for out-of-sample absolute-LST predictions.

The primary error metric gives each physical overpass date equal weight.  Metrics
whose names begin with ``pooled_`` intentionally pool tract-date rows and are
reported only as complementary diagnostics.  Signed error always means
``prediction - observation``.

This module evaluates already-created predictions.  It does not select rows,
derive thresholds from targets, or inspect model features.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

PREDICTION_COLUMNS = (
    "tract_geoid",
    "target_date",
    "spatial_block",
    "y_true",
    "y_pred",
)


class MetricAuditError(ValueError):
    """Raised when prediction rows cannot support an audited evaluation."""


@dataclass(frozen=True)
class AbsoluteLSTMetricSummary:
    """Scalar absolute-LST metrics and their independent-unit counts.

    ``pooled_oos_r2`` is ``None`` when all observed temperatures are identical.
    ``median_per_date_spearman`` is ``None`` when no date has at least two rows
    and nonconstant observed and predicted values.
    """

    primary_equal_date_weighted_mae_c: float
    pooled_rmse_c: float
    pooled_oos_r2: float | None
    pooled_mean_signed_error_c: float
    equal_date_weighted_mean_signed_error_c: float
    equal_date_weighted_within_date_anomaly_mae_c: float
    median_per_date_spearman: float | None
    row_count: int
    independent_date_count: int
    independent_spatial_block_count: int
    spearman_defined_date_count: int
    spearman_undefined_date_count: int


@dataclass(frozen=True)
class AbsoluteLSTEvaluation:
    """Summary metrics plus one deterministic diagnostic row per overpass date."""

    summary: AbsoluteLSTMetricSummary
    per_date: pd.DataFrame


def _civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            raise MetricAuditError(
                f"target_date at row {position} is numeric, not a civil date."
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise MetricAuditError(
                f"target_date at row {position} is not parseable."
            ) from error
        if pd.isna(timestamp):
            raise MetricAuditError(f"target_date at row {position} is missing.")
        if timestamp.tzinfo is not None:
            raise MetricAuditError("target_date must contain timezone-naive civil dates.")
        if timestamp != timestamp.normalize():
            raise MetricAuditError("target_date must contain civil-midnight timestamps.")
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[us]")


def _validate_normalized_strings(frame: pd.DataFrame, column: str) -> None:
    valid = frame[column].map(
        lambda value: isinstance(value, str)
        and bool(value)
        and value == value.strip()
    )
    if frame[column].isna().any() or not valid.all():
        raise MetricAuditError(
            f"{column} must contain non-empty, whitespace-normalized strings."
        )


def prepare_absolute_lst_predictions(
    frame: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> pd.DataFrame:
    """Validate and canonicalize rows without silently filtering observations.

    The returned frame contains only :data:`PREDICTION_COLUMNS` and is sorted by
    date and tract.  Extra input columns are ignored so they cannot affect metric
    values.  Every required row must have finite numeric truth and prediction.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Prediction input must be a pandas DataFrame.")
    if frame.columns.duplicated().any():
        duplicates = frame.columns[frame.columns.duplicated()].tolist()
        raise MetricAuditError(f"Prediction input has duplicate columns: {duplicates}")
    missing = sorted(set(PREDICTION_COLUMNS) - set(frame.columns))
    if missing:
        raise MetricAuditError(f"Prediction input is missing columns: {missing}")
    if frame.empty:
        raise MetricAuditError("Prediction input must contain at least one row.")
    if not isinstance(final_test_year, int) or isinstance(final_test_year, bool):
        raise TypeError("final_test_year must be an integer calendar year.")
    if not isinstance(unlock_final_test, bool):
        raise TypeError("unlock_final_test must be boolean.")

    result = frame.loc[:, list(PREDICTION_COLUMNS)].copy()
    _validate_normalized_strings(result, "tract_geoid")
    _validate_normalized_strings(result, "spatial_block")
    result["target_date"] = _civil_midnights(result["target_date"])

    duplicate_keys = result.duplicated(["tract_geoid", "target_date"], keep=False)
    if duplicate_keys.any():
        examples = result.loc[
            duplicate_keys, ["tract_geoid", "target_date"]
        ].head(5)
        raise MetricAuditError(
            "Prediction input has duplicate tract-date keys:\n"
            f"{examples.to_string(index=False)}"
        )

    if not unlock_final_test:
        locked = result["target_date"].dt.year >= final_test_year
        if locked.any():
            raise PermissionError(
                f"Prediction input contains {int(locked.sum())} locked rows from "
                f"{final_test_year} or later."
            )

    block_counts = result.groupby("tract_geoid", sort=False)["spatial_block"].nunique()
    inconsistent = block_counts[block_counts.ne(1)]
    if not inconsistent.empty:
        raise MetricAuditError(
            "Each tract_geoid must map to exactly one spatial_block; inconsistent "
            f"tracts include {inconsistent.index[:5].tolist()}."
        )

    for column in ("y_true", "y_pred"):
        if is_bool_dtype(result[column].dtype) or not is_numeric_dtype(
            result[column].dtype
        ):
            raise MetricAuditError(f"{column} must have a numeric, non-boolean dtype.")
        values = result[column].to_numpy(dtype=float, na_value=np.nan)
        if not np.isfinite(values).all():
            raise MetricAuditError(f"{column} must contain only finite values.")
        result[column] = values

    return result.sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def _spearman_or_none(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    if (
        y_true.size < 2
        or np.unique(y_true).size < 2
        or np.unique(y_pred).size < 2
    ):
        return None
    true_ranks = pd.Series(y_true).rank(method="average").to_numpy(dtype=float)
    pred_ranks = pd.Series(y_pred).rank(method="average").to_numpy(dtype=float)
    coefficient = float(np.corrcoef(true_ranks, pred_ranks)[0, 1])
    return coefficient if np.isfinite(coefficient) else None


def evaluate_absolute_lst_predictions(
    frame: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> AbsoluteLSTEvaluation:
    """Evaluate unique out-of-sample tract-date absolute-LST predictions.

    The primary MAE, signed-error macro diagnostic, and anomaly MAE are means of
    their per-date values, so a date with many valid tracts cannot dominate.  RMSE
    and R-squared are explicitly pooled complements.  Within-date anomalies center
    observations and predictions separately on each date before taking errors.
    """

    rows = prepare_absolute_lst_predictions(
        frame,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )

    date_records: list[dict[str, object]] = []
    for target_date, group in rows.groupby("target_date", sort=True):
        y_true = group["y_true"].to_numpy(dtype=float)
        y_pred = group["y_pred"].to_numpy(dtype=float)
        signed_error = y_pred - y_true
        true_anomaly = y_true - y_true.mean()
        pred_anomaly = y_pred - y_pred.mean()
        spearman = _spearman_or_none(y_true, y_pred)
        date_records.append(
            {
                "target_date": target_date,
                "row_count": int(len(group)),
                "spatial_block_count": int(group["spatial_block"].nunique()),
                "mae_c": float(np.mean(np.abs(signed_error))),
                "mean_signed_error_c": float(np.mean(signed_error)),
                "within_date_anomaly_mae_c": float(
                    np.mean(np.abs(pred_anomaly - true_anomaly))
                ),
                "spearman_rho": np.nan if spearman is None else spearman,
                "spearman_defined": spearman is not None,
            }
        )

    per_date = pd.DataFrame.from_records(date_records).astype(
        {
            "row_count": "int64",
            "spatial_block_count": "int64",
            "spearman_defined": "bool",
        }
    )
    all_true = rows["y_true"].to_numpy(dtype=float)
    all_pred = rows["y_pred"].to_numpy(dtype=float)
    all_signed_error = all_pred - all_true
    total_sum_squares = float(np.sum(np.square(all_true - all_true.mean())))
    pooled_r2 = (
        None
        if total_sum_squares == 0.0
        else 1.0 - float(np.sum(np.square(all_signed_error))) / total_sum_squares
    )
    defined_spearman = per_date.loc[per_date["spearman_defined"], "spearman_rho"]
    median_spearman = (
        None if defined_spearman.empty else float(defined_spearman.median())
    )

    summary = AbsoluteLSTMetricSummary(
        primary_equal_date_weighted_mae_c=float(per_date["mae_c"].mean()),
        pooled_rmse_c=float(np.sqrt(np.mean(np.square(all_signed_error)))),
        pooled_oos_r2=pooled_r2,
        pooled_mean_signed_error_c=float(np.mean(all_signed_error)),
        equal_date_weighted_mean_signed_error_c=float(
            per_date["mean_signed_error_c"].mean()
        ),
        equal_date_weighted_within_date_anomaly_mae_c=float(
            per_date["within_date_anomaly_mae_c"].mean()
        ),
        median_per_date_spearman=median_spearman,
        row_count=int(len(rows)),
        independent_date_count=int(rows["target_date"].nunique()),
        independent_spatial_block_count=int(rows["spatial_block"].nunique()),
        spearman_defined_date_count=int(per_date["spearman_defined"].sum()),
        spearman_undefined_date_count=int((~per_date["spearman_defined"]).sum()),
    )
    return AbsoluteLSTEvaluation(summary=summary, per_date=per_date)
