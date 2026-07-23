"""Target-blind deterministic calendar features known at prediction origin."""

from __future__ import annotations

import numpy as np
import pandas as pd

from la_heat.feature_registry import (
    CALENDAR_FEATURE_AVAILABLE_BY,
    CALENDAR_FEATURE_FAMILY,
    CALENDAR_FEATURE_SOURCE,
    CALENDAR_FEATURE_UNITS,
    CALENDAR_MODEL_FEATURE_NAMES,
)

CALENDAR_KEY_COLUMNS = ("tract_geoid", "target_date")
CALENDAR_FEATURE_REGISTRY_COLUMNS = (
    "feature_name",
    "family",
    "role",
    "units",
    "source",
    "static",
    "available_by",
    "source_start_offset_days",
    "source_end_offset_days",
)


class CalendarFeatureError(ValueError):
    """Raised when calendar features cannot be generated without ambiguity."""


def calendar_feature_registry_rows() -> pd.DataFrame:
    """Return the exact registry fragment for the deterministic sin/cos pair."""

    rows = [
        {
            "feature_name": feature_name,
            "family": CALENDAR_FEATURE_FAMILY,
            "role": "model",
            "units": CALENDAR_FEATURE_UNITS,
            "source": CALENDAR_FEATURE_SOURCE,
            "static": False,
            "available_by": CALENDAR_FEATURE_AVAILABLE_BY,
            "source_start_offset_days": np.nan,
            "source_end_offset_days": np.nan,
        }
        for feature_name in CALENDAR_MODEL_FEATURE_NAMES
    ]
    return pd.DataFrame(rows, columns=CALENDAR_FEATURE_REGISTRY_COLUMNS)


def _parse_civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for value in values.tolist():
        if value is None or value is pd.NaT or (
            not isinstance(value, (list, tuple, dict)) and pd.isna(value)
        ):
            raise CalendarFeatureError("target_date contains a missing date.")
        if isinstance(value, (bool, int, float, np.number)):
            raise CalendarFeatureError(
                f"target_date contains a non-calendar value: {value!r}"
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CalendarFeatureError(
                f"target_date contains an invalid calendar date: {value!r}"
            ) from exc
        if pd.isna(timestamp):
            raise CalendarFeatureError("target_date contains a missing date.")
        if timestamp.tzinfo is not None:
            raise CalendarFeatureError(
                "target_date must contain timezone-naive civil midnights."
            )
        if timestamp != timestamp.normalize():
            raise CalendarFeatureError(
                "target_date must contain timezone-naive civil midnights."
            )
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _validate_final_test_year(final_test_year: int) -> None:
    if isinstance(final_test_year, bool) or not isinstance(final_test_year, int):
        raise TypeError("final_test_year must be an integer.")


def build_calendar_features(
    keys: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> pd.DataFrame:
    """Build one deterministic feature row for every supplied tract-date key.

    Only the two keys are accepted as input, so target values, QA fields, observed
    predictors, and tract identifiers cannot be transformed into model features.
    Dates from the final-test year or later are rejected unless the caller passes
    the same explicit unlock decision used by the final evaluator.
    """

    _validate_final_test_year(final_test_year)
    if not isinstance(unlock_final_test, bool):
        raise TypeError("unlock_final_test must be boolean.")
    if not isinstance(keys, pd.DataFrame):
        raise TypeError("Calendar feature keys must be a pandas DataFrame.")
    if keys.columns.duplicated().any():
        raise CalendarFeatureError("Calendar feature keys contain duplicate columns.")
    if tuple(keys.columns) != CALENDAR_KEY_COLUMNS:
        raise CalendarFeatureError(
            "Calendar feature input must contain exactly tract_geoid,target_date "
            "in that order."
        )
    if keys.empty:
        raise CalendarFeatureError("Calendar feature keys must not be empty.")

    geoids = keys["tract_geoid"]
    valid_geoids = geoids.map(
        lambda value: isinstance(value, str) and bool(value) and value == value.strip()
    )
    if not valid_geoids.all():
        examples = geoids.loc[~valid_geoids].head(5).tolist()
        raise CalendarFeatureError(
            "tract_geoid must contain non-empty canonical strings; "
            f"examples: {examples}"
        )

    dates = _parse_civil_midnights(keys["target_date"])
    locked = dates.dt.year.ge(final_test_year)
    if locked.any() and not unlock_final_test:
        raise PermissionError(
            f"Calendar feature keys contain {int(locked.sum())} locked rows from "
            f"{final_test_year} or later."
        )

    normalized = pd.DataFrame(
        {
            "tract_geoid": geoids.astype("string"),
            "target_date": dates,
        }
    )
    duplicate_keys = normalized.duplicated(list(CALENDAR_KEY_COLUMNS), keep=False)
    if duplicate_keys.any():
        examples = normalized.loc[duplicate_keys, list(CALENDAR_KEY_COLUMNS)].head(5)
        raise CalendarFeatureError(
            "Calendar feature input contains duplicate tract-date keys:\n"
            f"{examples.to_string(index=False)}"
        )

    normalized = normalized.sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    day_index = normalized["target_date"].dt.dayofyear.to_numpy(dtype=float) - 1.0
    year_length = np.where(normalized["target_date"].dt.is_leap_year, 366.0, 365.0)
    phase = 2.0 * np.pi * day_index / year_length
    normalized["calendar_doy_sin"] = np.sin(phase)
    normalized["calendar_doy_cos"] = np.cos(phase)

    feature_values = normalized.loc[:, list(CALENDAR_MODEL_FEATURE_NAMES)].to_numpy()
    if not np.isfinite(feature_values).all():
        raise AssertionError("Calendar feature generation produced non-finite values.")
    return normalized.loc[:, [*CALENDAR_KEY_COLUMNS, *CALENDAR_MODEL_FEATURE_NAMES]]
