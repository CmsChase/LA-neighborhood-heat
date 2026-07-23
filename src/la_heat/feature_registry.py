"""Fail-closed validation for the Phase 2 feature registry."""

from __future__ import annotations

import re
from collections.abc import Iterable

import numpy as np
import pandas as pd

REQUIRED_FEATURE_REGISTRY_COLUMNS = frozenset(
    {
        "feature_name",
        "family",
        "role",
        "units",
        "source",
        "static",
        "available_by",
        "source_start_offset_days",
        "source_end_offset_days",
    }
)
VALID_FEATURE_ROLES = frozenset({"model", "audit_only", "key"})
REQUIRED_KEY_FEATURES = frozenset({"tract_geoid", "target_date"})
CALENDAR_MODEL_FEATURE_NAMES = ("calendar_doy_sin", "calendar_doy_cos")
CALENDAR_FEATURE_FAMILY = "calendar"
CALENDAR_FEATURE_UNITS = "unitless"
CALENDAR_FEATURE_SOURCE = "Deterministic target-date calendar known at prediction origin"
CALENDAR_FEATURE_AVAILABLE_BY = "prediction origin"


class FeatureRegistryError(ValueError):
    """Raised when a feature registry cannot be proven safe for modeling."""


_FORBIDDEN_MODEL_NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "tract identifier",
        re.compile(
            r"(?:^|_)(?:tract_?id|tract_?geoid|geoid|census_?tract)(?:_|$)"
        ),
    ),
    (
        "raw coordinate",
        re.compile(
            r"(?:^|_)(?:lat|latitude|lon|long|longitude|lng|easting|northing|"
            r"x|y|x_?coord|y_?coord|coord_?[xy]|utm_?[xy]|centroid_?[xy]|"
            r"centroid_?lat|centroid_?lon)(?:_|$)"
        ),
    ),
    (
        "spatial block or fold",
        re.compile(
            r"(?:^|_)(?:spatial_?(?:block|fold)|block_?id|fold_?id)(?:_|$)"
        ),
    ),
    (
        "coverage or observation count",
        re.compile(
            r"(?:^|_)(?:coverage|footprint_?fraction|valid_?fraction|"
            r"valid_?pixel(?:_?count)?|eligible_?pixel(?:_?count)?|n_?obs|"
            r"obs_?count|observation_?count)(?:_|$)"
        ),
    ),
    (
        "QA field",
        re.compile(
            r"(?:^|_)(?:qa|qa_?pixel|qa_?radsat|quality_?(?:flag|score)|"
            r"mask_?flag|saturation_?flag)(?:_|$)"
        ),
    ),
    (
        "LST, thermal, hotspot, or target-derived field",
        re.compile(
            r"(?:^|_)(?:lst|lwir|tir|thermal|st_?b10|surface_?temp(?:erature)?|"
            r"land_?surface_?temp(?:erature)?|hotspot|target|label|outcome|"
            r"response)(?:_|$)"
        ),
    ),
)


def _normalized_feature_name(value: str) -> str:
    """Return a conservative identifier form used only for validation."""

    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _format_rows(frame: pd.DataFrame, mask: pd.Series, columns: Iterable[str]) -> str:
    return frame.loc[mask, list(columns)].head(5).to_string(index=False)


def _validate_text_columns(registry: pd.DataFrame) -> None:
    for column in ("feature_name", "family", "role", "units", "source", "available_by"):
        valid = registry[column].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        )
        if not valid.all():
            raise FeatureRegistryError(
                f"Feature registry column {column!r} must contain only non-empty strings."
            )


def _validate_keys(registry: pd.DataFrame) -> None:
    roles = registry["role"]
    feature_names = registry["feature_name"]
    present_names = set(feature_names)
    missing_keys = sorted(REQUIRED_KEY_FEATURES - present_names)
    if missing_keys:
        raise FeatureRegistryError(
            f"Feature registry is missing required key features: {missing_keys}"
        )

    required_key_rows = feature_names.isin(REQUIRED_KEY_FEATURES)
    wrong_role = required_key_rows & roles.ne("key")
    if wrong_role.any():
        raise FeatureRegistryError(
            "tract_geoid and target_date may appear only with role='key':\n"
            f"{_format_rows(registry, wrong_role, ('feature_name', 'role'))}"
        )

    unexpected_keys = sorted(
        set(feature_names.loc[roles.eq("key")]) - REQUIRED_KEY_FEATURES
    )
    if unexpected_keys:
        raise FeatureRegistryError(
            "Only tract_geoid and target_date may use role='key': "
            f"{unexpected_keys}"
        )

    expected_static = {"tract_geoid": True, "target_date": False}
    wrong_static = required_key_rows & pd.Series(
        [
            bool(registry.at[index, "static"])
            != expected_static.get(str(registry.at[index, "feature_name"]), False)
            for index in registry.index
        ],
        index=registry.index,
    )
    if wrong_static.any():
        raise FeatureRegistryError(
            "tract_geoid must be static and target_date must be dynamic:\n"
            f"{_format_rows(registry, wrong_static, ('feature_name', 'static'))}"
        )

    offset_columns = ["source_start_offset_days", "source_end_offset_days"]
    key_offsets = registry.loc[required_key_rows, offset_columns]
    if key_offsets.notna().any(axis=None):
        raise FeatureRegistryError("Key features must not declare source-window offsets.")


def _validate_static_model_windows(registry: pd.DataFrame) -> None:
    static_model = registry["role"].eq("model") & registry["static"].astype(bool)
    columns = ["source_start_offset_days", "source_end_offset_days"]
    invalid = static_model & registry[columns].notna().any(axis=1)
    if invalid.any():
        raise FeatureRegistryError(
            "Static model features must not declare source-window offsets:\n"
            f"{_format_rows(registry, invalid, ('feature_name', *columns))}"
        )


def _validate_calendar_exception(
    registry: pd.DataFrame,
    normalized_names: pd.Series,
) -> pd.Series:
    """Return the exact deterministic-calendar rows allowed to omit source windows."""

    normalized_allowed = {
        _normalized_feature_name(name) for name in CALENDAR_MODEL_FEATURE_NAMES
    }
    family_like = registry["family"].map(
        lambda value: value.strip().casefold() == CALENDAR_FEATURE_FAMILY
    )
    name_like = normalized_names.map(
        lambda value: value in normalized_allowed or value.startswith("calendar_")
    )
    source_like = registry["source"].map(
        lambda value: value.strip().casefold() == CALENDAR_FEATURE_SOURCE.casefold()
    )
    candidates = family_like | name_like | source_like
    if not candidates.any():
        return pd.Series(False, index=registry.index, dtype=bool)

    expected_metadata: dict[str, object] = {
        "family": CALENDAR_FEATURE_FAMILY,
        "role": "model",
        "units": CALENDAR_FEATURE_UNITS,
        "source": CALENDAR_FEATURE_SOURCE,
        "static": False,
        "available_by": CALENDAR_FEATURE_AVAILABLE_BY,
    }
    invalid = candidates & ~registry["feature_name"].isin(CALENDAR_MODEL_FEATURE_NAMES)
    for column, expected in expected_metadata.items():
        invalid |= candidates & registry[column].ne(expected)
    offset_columns = ["source_start_offset_days", "source_end_offset_days"]
    invalid |= candidates & registry[offset_columns].notna().any(axis=1)
    if invalid.any():
        detail_columns = (
            "feature_name",
            "family",
            "role",
            "units",
            "source",
            "static",
            "available_by",
            *offset_columns,
        )
        raise FeatureRegistryError(
            "Deterministic calendar metadata must match the frozen calendar "
            "feature contract exactly:\n"
            f"{_format_rows(registry, invalid, detail_columns)}"
        )

    present = set(registry.loc[candidates, "feature_name"])
    expected_names = set(CALENDAR_MODEL_FEATURE_NAMES)
    if present != expected_names:
        raise FeatureRegistryError(
            "Deterministic calendar features must be declared as the complete "
            f"sin/cos pair {list(CALENDAR_MODEL_FEATURE_NAMES)}; found {sorted(present)}."
        )
    return registry["feature_name"].isin(CALENDAR_MODEL_FEATURE_NAMES)


def _validate_dynamic_model_windows(
    registry: pd.DataFrame,
    *,
    calendar_exception: pd.Series,
) -> None:
    dynamic_model = (
        registry["role"].eq("model")
        & ~registry["static"].astype(bool)
        & ~calendar_exception
    )
    if not dynamic_model.any():
        return

    columns = ["source_start_offset_days", "source_end_offset_days"]
    offsets = registry.loc[dynamic_model, columns].apply(pd.to_numeric, errors="coerce")
    offset_values = offsets.to_numpy(dtype=float, na_value=np.nan)
    finite = np.isfinite(offset_values).all(axis=1)
    integral = np.equal(offset_values, np.floor(offset_values)).all(axis=1)
    valid_offsets = finite & integral
    if not valid_offsets.all():
        invalid_index = offsets.index[~valid_offsets]
        invalid = pd.Series(registry.index.isin(invalid_index), index=registry.index)
        examples = _format_rows(registry, invalid, ("feature_name", *columns))
        raise FeatureRegistryError(
            "Dynamic model feature offsets must be finite integer day offsets:\n"
            f"{examples}"
        )

    start = offsets["source_start_offset_days"]
    end = offsets["source_end_offset_days"]
    reversed_window = start > end
    if reversed_window.any():
        invalid = pd.Series(
            registry.index.isin(reversed_window.index[reversed_window]), index=registry.index
        )
        examples = _format_rows(registry, invalid, ("feature_name", *columns))
        raise FeatureRegistryError(
            "Dynamic model feature windows require source_start_offset_days <= "
            "source_end_offset_days:\n"
            f"{examples}"
        )

    leaks = end > -1
    if leaks.any():
        invalid = pd.Series(registry.index.isin(leaks.index[leaks]), index=registry.index)
        examples = _format_rows(registry, invalid, ("feature_name", *columns))
        raise FeatureRegistryError(
            "Dynamic model features must end by target day -1; "
            "source_end_offset_days must be <= -1:\n"
            f"{examples}"
        )


def _validate_static_model_availability(
    registry: pd.DataFrame,
    *,
    development_start: str | pd.Timestamp,
) -> None:
    try:
        start = pd.to_datetime(development_start, errors="raise", utc=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FeatureRegistryError("development_start must be a valid date or timestamp.") from exc
    if not isinstance(start, pd.Timestamp) or pd.isna(start):
        raise FeatureRegistryError("development_start must be a valid date or timestamp.")
    cutoff = pd.Timestamp(year=start.year, month=1, day=1, tz="UTC")

    static_model = registry["role"].eq("model") & registry["static"].astype(bool)
    if not static_model.any():
        return

    available = pd.to_datetime(
        registry.loc[static_model, "available_by"], errors="coerce", utc=True
    )
    invalid = available.isna() | available.ge(cutoff)
    if invalid.any():
        invalid_index = available.index[invalid]
        mask = pd.Series(registry.index.isin(invalid_index), index=registry.index)
        raise FeatureRegistryError(
            "Static model features require a source available_by date strictly before "
            f"the development start year ({cutoff.date().isoformat()}):\n"
            f"{_format_rows(registry, mask, ('feature_name', 'source', 'available_by'))}"
        )


def _validate_model_feature_names(registry: pd.DataFrame, normalized_names: pd.Series) -> None:
    rejected: list[tuple[str, str]] = []
    for index in registry.index[registry["role"].eq("model")]:
        normalized = normalized_names.loc[index]
        reasons = [
            reason
            for reason, pattern in _FORBIDDEN_MODEL_NAME_PATTERNS
            if pattern.search(normalized)
        ]
        if reasons:
            rejected.append((registry.at[index, "feature_name"], "; ".join(reasons)))
    if rejected:
        detail = ", ".join(f"{name!r} ({reason})" for name, reason in rejected[:10])
        raise FeatureRegistryError(f"Forbidden primary-model feature names: {detail}")


def validate_feature_registry(
    registry: pd.DataFrame,
    *,
    development_start: str | pd.Timestamp,
) -> None:
    """Validate registry schema, feature roles, and historical-hindcast timing.

    ``tract_geoid`` and ``target_date`` must be key features. Features with
    ``role='model'`` are subject to the leakage rules; forbidden fields may still
    be retained explicitly as ``audit_only`` metadata.
    """

    if not isinstance(registry, pd.DataFrame):
        raise TypeError("Feature registry validation requires a pandas DataFrame.")
    missing = sorted(REQUIRED_FEATURE_REGISTRY_COLUMNS - set(registry.columns))
    if missing:
        raise FeatureRegistryError(f"Feature registry is missing required columns: {missing}")
    if registry.empty:
        raise FeatureRegistryError("Feature registry must contain at least one row.")

    working = registry.reset_index(drop=True)
    _validate_text_columns(working)

    feature_names = working["feature_name"]
    normalized_names = feature_names.map(_normalized_feature_name)
    noncanonical = feature_names.ne(feature_names.str.strip())
    if noncanonical.any():
        raise FeatureRegistryError("Feature names must not have leading or trailing whitespace.")
    duplicate = normalized_names.duplicated(keep=False)
    if duplicate.any():
        examples = sorted(feature_names.loc[duplicate].tolist(), key=str.casefold)
        raise FeatureRegistryError(
            "Feature names must be unique after case/punctuation normalization: "
            f"{examples[:10]}"
        )

    invalid_roles = ~working["role"].isin(VALID_FEATURE_ROLES)
    if invalid_roles.any():
        values = sorted(working.loc[invalid_roles, "role"].unique())
        raise FeatureRegistryError(
            f"Feature registry has invalid roles {values}; allowed roles are "
            f"{sorted(VALID_FEATURE_ROLES)}."
        )

    valid_static = working["static"].map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if not valid_static.all():
        raise FeatureRegistryError("Feature registry column 'static' must contain only booleans.")

    _validate_keys(working)
    _validate_model_feature_names(working, normalized_names)
    _validate_static_model_windows(working)
    calendar_exception = _validate_calendar_exception(working, normalized_names)
    _validate_dynamic_model_windows(working, calendar_exception=calendar_exception)
    _validate_static_model_availability(working, development_start=development_start)
