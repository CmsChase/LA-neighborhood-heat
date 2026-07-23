"""Machine-enforceable scientific integrity checks."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

FORBIDDEN_PRIMARY_FEATURE_TOKENS = (
    "lwir",
    "thermal",
    "st_b10",
    "surface_temperature",
    "land_surface_temperature",
    "lst_",
    "target_",
    "hotspot_label",
    "st_uncertainty",
    "tract_id",
    "geoid",
)


def validate_primary_feature_names(feature_names: Iterable[str]) -> None:
    """Reject direct target, target-derived, thermal, and identifier predictors."""

    rejected = sorted(
        name
        for name in feature_names
        if any(token in name.lower() for token in FORBIDDEN_PRIMARY_FEATURE_TOKENS)
    )
    if rejected:
        raise ValueError(f"Forbidden primary-model features: {rejected}")


def validate_lag_windows(
    frame: pd.DataFrame,
    *,
    target_date: str = "target_date",
    feature_window_end: str = "feature_window_end",
) -> None:
    """Require every satellite feature window to end before its target date."""

    target = pd.to_datetime(frame[target_date], utc=True)
    window_end = pd.to_datetime(frame[feature_window_end], utc=True)
    invalid = window_end >= target
    if invalid.any():
        examples = frame.loc[invalid, [target_date, feature_window_end]].head(5)
        raise ValueError(f"Satellite temporal leakage detected:\n{examples.to_string(index=False)}")


def validate_disjoint_groups(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    group_columns: Iterable[str],
) -> None:
    """Require train/test group values to be disjoint for every declared group."""

    overlaps: dict[str, list[object]] = {}
    for column in group_columns:
        shared = set(train[column].dropna().unique()) & set(test[column].dropna().unique())
        if shared:
            overlaps[column] = sorted(shared, key=str)[:10]
    if overlaps:
        raise ValueError(f"Grouped split leakage detected: {overlaps}")


def validate_no_final_year_rows(
    frame: pd.DataFrame,
    *,
    final_year: int,
    date_column: str = "target_date",
) -> None:
    """Reject the final-test year and any later rows in development operations."""

    years = pd.to_datetime(frame[date_column], utc=True).dt.year
    count = int((years >= final_year).sum())
    if count:
        raise PermissionError(
            f"Found {count} locked final-test rows from {final_year} or later."
        )


def validate_unique_primary_key(
    frame: pd.DataFrame,
    *,
    key_columns: tuple[str, str] = ("tract_geoid", "target_date"),
) -> None:
    """Require exactly one row per declared tract-date observation."""

    duplicate = frame.duplicated(list(key_columns), keep=False)
    if duplicate.any():
        examples = frame.loc[duplicate, list(key_columns)].head(5)
        raise ValueError(f"Duplicate primary keys detected:\n{examples.to_string(index=False)}")


def validate_target_qa_contract(
    frame: pd.DataFrame,
    *,
    minimum_footprint_fraction: float,
    minimum_valid_fraction: float,
    minimum_valid_pixels: int,
) -> None:
    """Require target availability to match the predeclared QA gate exactly."""

    passes_qa = (
        (frame["footprint_fraction"] >= minimum_footprint_fraction)
        & (frame["valid_fraction"] >= minimum_valid_fraction)
        & (frame["valid_pixel_count"] >= minimum_valid_pixels)
    )
    has_target = frame["target_lst_c"].notna()
    mismatch = passes_qa != has_target
    if mismatch.any():
        columns = [
            "tract_geoid",
            "target_date",
            "footprint_fraction",
            "valid_fraction",
            "valid_pixel_count",
            "target_lst_c",
        ]
        examples = frame.loc[mismatch, columns].head(5)
        raise ValueError(f"Target QA contract violated:\n{examples.to_string(index=False)}")


def validate_static_eligible_denominator(
    frame: pd.DataFrame,
    *,
    geoid_column: str = "tract_geoid",
    count_column: str = "eligible_pixel_count_static",
    identity_column: str = "eligible_pixel_identity_sha256",
) -> None:
    """Require the target-coverage denominator to be invariant across dates."""

    variation = frame.groupby(geoid_column, sort=False)[
        [count_column, identity_column]
    ].nunique(dropna=False)
    invalid_geoids = variation.index[(variation != 1).any(axis=1)].tolist()
    if invalid_geoids:
        raise ValueError(
            "Static eligible-pixel denominator changed across dates for GEOIDs: "
            f"{invalid_geoids[:10]}"
        )
