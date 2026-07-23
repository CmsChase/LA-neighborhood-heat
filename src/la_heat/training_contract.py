"""Training-only date balancing and the legal B0 date-mean design."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from la_heat.feature_registry import CALENDAR_MODEL_FEATURE_NAMES

TRAINING_KEY_COLUMNS = ("tract_geoid", "target_date")


class TrainingContractError(ValueError):
    """Raised when a fold-local training design cannot be proven legal."""


@dataclass(frozen=True)
class B0DateMeanTraining:
    """One equal-weight response and calendar row per independent training date."""

    features: pd.DataFrame
    target: pd.Series
    dates: pd.Series
    tract_row_counts: pd.Series


def _civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            raise TrainingContractError(
                f"target_date at row {position} is numeric, not a civil date."
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TrainingContractError(
                f"target_date at row {position} is not parseable."
            ) from exc
        if pd.isna(timestamp):
            raise TrainingContractError(f"target_date at row {position} is missing.")
        if timestamp.tzinfo is not None or timestamp != timestamp.normalize():
            raise TrainingContractError(
                "Training target_date values must be timezone-naive civil midnights."
            )
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _validated_training_keys(
    keys: pd.DataFrame,
    *,
    final_test_year: int,
    unlock_final_test: bool,
) -> pd.DataFrame:
    if not isinstance(keys, pd.DataFrame):
        raise TypeError("Training keys must be a pandas DataFrame.")
    if keys.columns.duplicated().any() or tuple(keys.columns) != TRAINING_KEY_COLUMNS:
        raise TrainingContractError(
            "Training keys must contain exactly tract_geoid,target_date in that order."
        )
    if keys.empty:
        raise TrainingContractError("Training keys must not be empty.")
    if not isinstance(final_test_year, int) or isinstance(final_test_year, bool):
        raise TypeError("final_test_year must be an integer.")
    if not isinstance(unlock_final_test, bool):
        raise TypeError("unlock_final_test must be boolean.")
    valid_geoids = keys["tract_geoid"].map(
        lambda value: isinstance(value, str) and bool(value) and value == value.strip()
    )
    if not valid_geoids.all():
        raise TrainingContractError("Training tract_geoid values must be normalized strings.")
    result = keys.copy()
    result["target_date"] = _civil_midnights(result["target_date"])
    if result.duplicated(list(TRAINING_KEY_COLUMNS)).any():
        raise TrainingContractError("Training keys contain duplicate tract-date rows.")
    locked = result["target_date"].dt.year.ge(final_test_year)
    if locked.any() and not unlock_final_test:
        raise PermissionError(
            f"Training keys contain {int(locked.sum())} locked rows from "
            f"{final_test_year} or later."
        )
    return result


def date_balanced_sample_weights(
    keys: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> pd.Series:
    """Return fold-local weights with equal total mass for each overpass date.

    For ``N`` rows across ``D`` dates, row ``i`` on date ``d`` receives
    ``N / (D * n_d)``. The mean weight is one, and the sum within every date is
    exactly ``N / D`` up to floating-point precision.
    """

    validated = _validated_training_keys(
        keys,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )
    counts = validated.groupby("target_date")["tract_geoid"].transform("size")
    date_count = validated["target_date"].nunique()
    weights = len(validated) / (date_count * counts.to_numpy(dtype=float))
    if not np.isfinite(weights).all() or np.any(weights <= 0):
        raise AssertionError("Date-balanced training weights are not finite and positive.")
    return pd.Series(weights, index=keys.index, name="date_balanced_sample_weight")


def prepare_b0_date_mean_training(
    features: pd.DataFrame,
    target: pd.Series,
    keys: pd.DataFrame,
    *,
    final_test_year: int = 2025,
) -> B0DateMeanTraining:
    """Aggregate an outer/inner training fold to the legal equal-date B0 design.

    This function must receive training rows only. It computes target means inside
    that fold and returns one row per training date. Date means are responses,
    never model features or reusable climatology columns.
    """

    validated_keys = _validated_training_keys(
        keys,
        final_test_year=final_test_year,
        unlock_final_test=False,
    )
    if not isinstance(features, pd.DataFrame):
        raise TypeError("B0 features must be a pandas DataFrame.")
    if tuple(features.columns) != CALENDAR_MODEL_FEATURE_NAMES:
        raise TrainingContractError(
            "B0 requires exactly calendar_doy_sin,calendar_doy_cos in registry order."
        )
    if not isinstance(target, pd.Series):
        raise TypeError("B0 target must be a pandas Series.")
    if not features.index.equals(keys.index) or not target.index.equals(keys.index):
        raise TrainingContractError("B0 features, target, and keys must align by index.")
    numeric_features = features.apply(pd.to_numeric, errors="raise")
    feature_values = numeric_features.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(feature_values).all():
        raise TrainingContractError("B0 calendar features must be complete and finite.")
    numeric_target = pd.to_numeric(target, errors="raise")
    target_values = numeric_target.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(target_values).all():
        raise TrainingContractError("B0 training target must be complete and finite.")

    working = pd.concat(
        [
            validated_keys[["target_date"]],
            numeric_features,
            pd.Series(target_values, index=keys.index, name="target_lst_c"),
        ],
        axis=1,
    )
    grouped = working.groupby("target_date", sort=True)
    calendar_variation = grouped[list(CALENDAR_MODEL_FEATURE_NAMES)].nunique(dropna=False)
    if calendar_variation.ne(1).any(axis=None):
        raise TrainingContractError(
            "B0 calendar features must be identical for every tract on a date."
        )
    per_date = grouped.agg(
        calendar_doy_sin=("calendar_doy_sin", "first"),
        calendar_doy_cos=("calendar_doy_cos", "first"),
        target_lst_c=("target_lst_c", "mean"),
        tract_row_count=("target_lst_c", "size"),
    ).reset_index()
    design = np.column_stack(
        [
            np.ones(len(per_date), dtype=float),
            per_date.loc[:, list(CALENDAR_MODEL_FEATURE_NAMES)].to_numpy(dtype=float),
        ]
    )
    if np.linalg.matrix_rank(design) != 3:
        raise TrainingContractError(
            "B0 training dates do not give a full-rank intercept/sin/cos design."
        )
    return B0DateMeanTraining(
        features=per_date.loc[:, list(CALENDAR_MODEL_FEATURE_NAMES)].copy(),
        target=per_date["target_lst_c"].copy(),
        dates=per_date["target_date"].copy(),
        tract_row_counts=per_date["tract_row_count"].astype(int).copy(),
    )
