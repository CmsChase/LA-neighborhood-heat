"""Validate and promote frozen Sentinel-2 predictors without reading target values.

The acquisition builder writes cache-safe interim outputs and a mutable progress
file.  This stage is the immutable hand-off to Phase 2: it revalidates the exact
target-blind key universe, registry fragment, coverage rules, temporal lineage,
and fixed eligible-land denominator before writing canonical predictor/audit
tables.  The provenance JSON is the commit marker and is always written last.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import ResearchConfig, load_config
from la_heat.feature_registry import validate_feature_registry
from la_heat.phase2_registry import (
    DEVELOPMENT_START,
    PHASE2_REGISTRY_PROVENANCE_FILENAME,
    sentinel_feature_registry_rows,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_feature_builder import load_sentinel_stage_config
from la_heat.sentinel_features import INDEX_COLUMNS

PROMOTION_SCHEMA_VERSION: Final = 1
PROMOTION_ALGORITHM_VERSION: Final = "sentinel-feature-promotion-v1"
EXPECTED_SENTINEL_SCIENTIFIC_SHA256: Final = (
    "68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c"
)
EXPECTED_COMPILE_ADAPTER_VERSION: Final = "sentinel-target-sharded-compile-v1"

DEFAULT_SOURCE_DIRECTORY: Final = Path("data/interim/sentinel_features")
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/processed/sentinel_features")
DEFAULT_FEATURE_UNIVERSE_PATH: Final = Path(
    "data/interim/features/feature_key_universe/feature_key_universe.parquet"
)
DEFAULT_REGISTRY_PATH: Final = Path(
    "manifests/phase2_registry/combined_feature_registry_draft.csv"
)
DEFAULT_INVENTORY_DIRECTORY: Final = Path("manifests/sentinel_inventory")
DEFAULT_RESEARCH_CONFIG_PATH: Final = Path("configs/research.toml")
DEFAULT_SENTINEL_CONFIG_PATH: Final = Path("configs/sentinel_features.toml")

PROMOTED_FEATURE_FILENAME: Final = "sentinel_features.parquet"
PROMOTED_AUDIT_FILENAME: Final = "sentinel_feature_audit.parquet"
COVERAGE_BY_DATE_FILENAME: Final = "coverage_by_date.parquet"
COVERAGE_BY_TRACT_FILENAME: Final = "coverage_by_tract.parquet"
PROMOTION_PROVENANCE_FILENAME: Final = "sentinel_features_provenance.json"

SOURCE_FEATURE_COLUMNS: Final = ("target_date", "tract_geoid", *INDEX_COLUMNS)
PROMOTED_FEATURE_COLUMNS: Final = ("tract_geoid", "target_date", *INDEX_COLUMNS)
AUDIT_VALUE_COLUMNS: Final = (
    "window_membership_count",
    "qualifying_acquisition_count",
    "minimum_lag_days",
    "maximum_lag_days",
    "median_acquisition_coverage",
    "newest_source_end_date",
    "oldest_source_end_date",
    "sentinel_feature_available",
)
SOURCE_AUDIT_COLUMNS: Final = ("target_date", "tract_geoid", *AUDIT_VALUE_COLUMNS)
PROMOTED_AUDIT_COLUMNS: Final = ("tract_geoid", "target_date", *AUDIT_VALUE_COLUMNS)
MEMBERSHIP_COLUMNS: Final = (
    "target_date",
    "physical_acquisition_id",
    "acquisition_local_date",
    "lag_days",
)
LINEAGE_REQUIRED_COLUMNS: Final = (
    *MEMBERSHIP_COLUMNS,
    "tract_geoid",
    "eligible_pixel_count_static",
    "acquisition_coverage_fraction",
    *INDEX_COLUMNS,
    "eligible_pixel_identity_sha256_audit_only",
    "included_in_composite",
    "source_end_date",
    "source_age_days_audit_only",
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GEOID_PATTERN = re.compile(r"^06037[0-9]{6}$")
_FORBIDDEN_SOURCE_COLUMNS = frozenset(
    {
        "target_lst_c",
        "target_available",
        "date_usable",
        "lst_anomaly_c",
        "relative_hotspot_top20",
    }
)


class SentinelFeaturePromotionError(ValueError):
    """Raised when a Sentinel artifact cannot be proven safe for promotion."""


def _stable_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SentinelFeaturePromotionError(f"Cannot read {label}: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"{label} changed while it was being read: {path}")
    if not isinstance(payload, dict):
        raise SentinelFeaturePromotionError(f"{label} must contain a JSON object.")
    return payload, before


def _committed_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    payload, file_sha256 = _stable_json(path, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise SentinelFeaturePromotionError(f"{label} has an invalid commit hash.")
    return payload, file_sha256


def _stable_parquet(path: Path, *, label: str) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        frame = pd.read_parquet(path)
    except (OSError, TypeError, ValueError) as exc:
        raise SentinelFeaturePromotionError(f"Cannot read {label}: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"{label} changed while it was being read: {path}")
    if frame.columns.duplicated().any():
        raise SentinelFeaturePromotionError(f"{label} contains duplicate columns.")
    return frame, before


def _stable_csv(path: Path, *, label: str) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        frame = pd.read_csv(path)
    except (OSError, TypeError, ValueError) as exc:
        raise SentinelFeaturePromotionError(f"Cannot read {label}: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"{label} changed while it was being read: {path}")
    if frame.columns.duplicated().any():
        raise SentinelFeaturePromotionError(f"{label} contains duplicate columns.")
    return frame, before


def _require_parquet_record(
    path: Path,
    frame: pd.DataFrame,
    recorded: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(recorded, dict):
        raise SentinelFeaturePromotionError(f"{label} lacks a source output record.")
    actual = parquet_file_record(path, frame)
    for field in ("sha256", "bytes", "rows", "schema_sha256"):
        if recorded.get(field) != actual[field]:
            raise SentinelFeaturePromotionError(
                f"{label} disagrees with its recorded {field}."
            )
    return actual


def _parse_civil_midnights(values: pd.Series, *, field: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, errors="raise")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SentinelFeaturePromotionError(f"{field} contains invalid dates.") from exc
    if parsed.isna().any():
        raise SentinelFeaturePromotionError(f"{field} contains missing dates.")
    try:
        timezone = parsed.dt.tz
        normalized = parsed.dt.normalize()
    except AttributeError as exc:
        raise SentinelFeaturePromotionError(
            f"{field} must use one timezone-naive date representation."
        ) from exc
    if timezone is not None or not parsed.equals(normalized):
        raise SentinelFeaturePromotionError(
            f"{field} must contain timezone-naive civil midnights."
        )
    try:
        return parsed.astype("datetime64[ns]")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SentinelFeaturePromotionError(f"{field} is outside the supported range.") from exc


def _normalize_geoids(values: pd.Series, *, field: str) -> pd.Series:
    valid = values.map(
        lambda value: isinstance(value, str) and bool(_GEOID_PATTERN.fullmatch(value))
    )
    if not valid.all():
        raise SentinelFeaturePromotionError(
            f"{field} must contain complete 11-digit Los Angeles County GEOIDs."
        )
    return values.astype("string")


def _normalize_strings(values: pd.Series, *, field: str) -> pd.Series:
    valid = values.map(lambda value: isinstance(value, str) and bool(value.strip()))
    if not valid.all():
        raise SentinelFeaturePromotionError(f"{field} must contain non-empty strings.")
    return values.astype("string")


def _normalize_integers(values: pd.Series, *, field: str) -> pd.Series:
    try:
        numeric = pd.to_numeric(values, errors="raise")
    except (TypeError, ValueError) as exc:
        raise SentinelFeaturePromotionError(f"{field} must be numeric integers.") from exc
    array = numeric.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(array).all() or not np.equal(array, np.floor(array)).all():
        raise SentinelFeaturePromotionError(f"{field} must contain finite integers.")
    return numeric.astype("int64")


def _normalize_floats(
    values: pd.Series,
    *,
    field: str,
    allow_missing: bool,
) -> pd.Series:
    try:
        numeric = pd.to_numeric(values, errors="raise").astype("float64")
    except (TypeError, ValueError) as exc:
        raise SentinelFeaturePromotionError(f"{field} must be numeric.") from exc
    array = numeric.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(array).any() or (not allow_missing and np.isnan(array).any()):
        qualifier = "finite or missing" if allow_missing else "finite"
        raise SentinelFeaturePromotionError(f"{field} must contain only {qualifier} values.")
    return numeric


def _normalize_booleans(values: pd.Series, *, field: str) -> pd.Series:
    valid = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    if not valid.all():
        raise SentinelFeaturePromotionError(f"{field} must contain only booleans.")
    return values.astype(bool)


def _enforce_development_lock(
    dates: pd.Series,
    *,
    research: ResearchConfig,
    field: str,
) -> None:
    locked = dates.dt.year.ge(research.final_test_year)
    if locked.any():
        raise PermissionError(
            f"{field} contains {int(locked.sum())} rows from locked year "
            f"{research.final_test_year} or later."
        )


def _normalize_universe(
    frame: pd.DataFrame,
    *,
    research: ResearchConfig,
) -> pd.DataFrame:
    if tuple(frame.columns) != ("tract_geoid", "target_date"):
        raise SentinelFeaturePromotionError(
            "Feature universe must contain exactly tract_geoid and target_date in order."
        )
    result = frame.copy()
    result["tract_geoid"] = _normalize_geoids(
        result["tract_geoid"], field="Feature universe tract_geoid"
    )
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Feature universe target_date"
    )
    _enforce_development_lock(
        result["target_date"], research=research, field="Feature universe"
    )
    if result.empty or result.duplicated(["tract_geoid", "target_date"]).any():
        raise SentinelFeaturePromotionError(
            "Feature universe must be non-empty with unique tract-date keys."
        )
    if len(result) != result["tract_geoid"].nunique() * result["target_date"].nunique():
        raise SentinelFeaturePromotionError("Feature universe is not a complete date × tract grid.")
    return result.sort_values(["target_date", "tract_geoid"], kind="stable").reset_index(
        drop=True
    )


def _normalize_features(
    frame: pd.DataFrame,
    *,
    research: ResearchConfig,
) -> pd.DataFrame:
    if tuple(frame.columns) != SOURCE_FEATURE_COLUMNS:
        raise SentinelFeaturePromotionError(
            "Sentinel feature source schema or column order changed."
        )
    if set(frame.columns) & _FORBIDDEN_SOURCE_COLUMNS:
        raise SentinelFeaturePromotionError("Sentinel features contain target-derived columns.")
    result = frame.copy()
    result["tract_geoid"] = _normalize_geoids(
        result["tract_geoid"], field="Sentinel feature tract_geoid"
    )
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Sentinel feature target_date"
    )
    _enforce_development_lock(
        result["target_date"], research=research, field="Sentinel features"
    )
    for column in INDEX_COLUMNS:
        result[column] = _normalize_floats(
            result[column], field=f"Sentinel feature {column}", allow_missing=True
        )
    if result.duplicated(["tract_geoid", "target_date"]).any():
        raise SentinelFeaturePromotionError("Sentinel features contain duplicate keys.")
    missing_count = result[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not missing_count.isin([0, len(INDEX_COLUMNS)]).all():
        raise SentinelFeaturePromotionError(
            "Sentinel predictors must be all present or all missing within a tract-date row."
        )
    return result.loc[:, PROMOTED_FEATURE_COLUMNS].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def _normalize_audit(
    frame: pd.DataFrame,
    *,
    research: ResearchConfig,
) -> pd.DataFrame:
    if tuple(frame.columns) != SOURCE_AUDIT_COLUMNS:
        raise SentinelFeaturePromotionError("Sentinel audit source schema or order changed.")
    if set(frame.columns) & _FORBIDDEN_SOURCE_COLUMNS:
        raise SentinelFeaturePromotionError("Sentinel audit contains target-derived columns.")
    result = frame.copy()
    result["tract_geoid"] = _normalize_geoids(
        result["tract_geoid"], field="Sentinel audit tract_geoid"
    )
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Sentinel audit target_date"
    )
    _enforce_development_lock(result["target_date"], research=research, field="Sentinel audit")
    for column in (
        "window_membership_count",
        "qualifying_acquisition_count",
        "minimum_lag_days",
        "maximum_lag_days",
    ):
        result[column] = _normalize_integers(result[column], field=f"Sentinel audit {column}")
    result["median_acquisition_coverage"] = _normalize_floats(
        result["median_acquisition_coverage"],
        field="Sentinel audit median_acquisition_coverage",
        allow_missing=False,
    )
    for column in ("newest_source_end_date", "oldest_source_end_date"):
        result[column] = _parse_civil_midnights(
            result[column], field=f"Sentinel audit {column}"
        )
    result["sentinel_feature_available"] = _normalize_booleans(
        result["sentinel_feature_available"],
        field="Sentinel audit sentinel_feature_available",
    )
    if result.duplicated(["tract_geoid", "target_date"]).any():
        raise SentinelFeaturePromotionError("Sentinel audit contains duplicate keys.")
    if (result["window_membership_count"] < 1).any():
        raise SentinelFeaturePromotionError("Every target window must contain a membership.")
    if (
        (result["qualifying_acquisition_count"] < 0).any()
        or (
            result["qualifying_acquisition_count"]
            > result["window_membership_count"]
        ).any()
    ):
        raise SentinelFeaturePromotionError("Sentinel audit acquisition counts are invalid.")
    if not result["median_acquisition_coverage"].between(0.0, 1.0).all():
        raise SentinelFeaturePromotionError("Sentinel audit coverage must be in [0, 1].")
    invalid_lag = (
        ~result["minimum_lag_days"].between(1, 60)
        | ~result["maximum_lag_days"].between(1, 60)
        | (result["minimum_lag_days"] > result["maximum_lag_days"])
    )
    if invalid_lag.any():
        raise SentinelFeaturePromotionError("Sentinel audit lags must remain within d-60:d-1.")
    if (
        (result["newest_source_end_date"] >= result["target_date"]).any()
        or (result["oldest_source_end_date"] > result["newest_source_end_date"]).any()
    ):
        raise SentinelFeaturePromotionError("Sentinel audit contains target-day/future sources.")
    return result.loc[:, PROMOTED_AUDIT_COLUMNS].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def _normalize_membership(
    frame: pd.DataFrame,
    *,
    research: ResearchConfig,
) -> pd.DataFrame:
    if tuple(frame.columns) != MEMBERSHIP_COLUMNS:
        raise SentinelFeaturePromotionError("Sentinel membership schema or order changed.")
    result = frame.copy()
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Sentinel membership target_date"
    )
    result["acquisition_local_date"] = _parse_civil_midnights(
        result["acquisition_local_date"],
        field="Sentinel membership acquisition_local_date",
    )
    result["physical_acquisition_id"] = _normalize_strings(
        result["physical_acquisition_id"],
        field="Sentinel membership physical_acquisition_id",
    )
    result["lag_days"] = _normalize_integers(
        result["lag_days"], field="Sentinel membership lag_days"
    )
    _enforce_development_lock(
        result["target_date"], research=research, field="Sentinel membership"
    )
    computed = (result["target_date"] - result["acquisition_local_date"]).dt.days
    if (
        result.duplicated(["target_date", "physical_acquisition_id"]).any()
        or not computed.equals(result["lag_days"])
        or not computed.between(1, 60).all()
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel membership violates unique exact d-60:d-1 windows."
        )
    return result.sort_values(
        ["target_date", "physical_acquisition_id"], kind="stable"
    ).reset_index(drop=True)


def _normalize_lineage(
    frame: pd.DataFrame,
    *,
    research: ResearchConfig,
    minimum_coverage: float,
) -> pd.DataFrame:
    missing = sorted(set(LINEAGE_REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise SentinelFeaturePromotionError(
            f"Sentinel lineage lacks required columns: {missing}"
        )
    forbidden = sorted(set(frame.columns) & _FORBIDDEN_SOURCE_COLUMNS)
    if forbidden:
        raise SentinelFeaturePromotionError(
            f"Sentinel lineage contains target-derived columns: {forbidden}"
        )
    result = frame.loc[:, LINEAGE_REQUIRED_COLUMNS].copy()
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Sentinel lineage target_date"
    )
    result["acquisition_local_date"] = _parse_civil_midnights(
        result["acquisition_local_date"],
        field="Sentinel lineage acquisition_local_date",
    )
    result["source_end_date"] = _parse_civil_midnights(
        result["source_end_date"], field="Sentinel lineage source_end_date"
    )
    result["tract_geoid"] = _normalize_geoids(
        result["tract_geoid"], field="Sentinel lineage tract_geoid"
    )
    result["physical_acquisition_id"] = _normalize_strings(
        result["physical_acquisition_id"],
        field="Sentinel lineage physical_acquisition_id",
    )
    for column in (
        "lag_days",
        "eligible_pixel_count_static",
        "source_age_days_audit_only",
    ):
        result[column] = _normalize_integers(
            result[column], field=f"Sentinel lineage {column}"
        )
    result["acquisition_coverage_fraction"] = _normalize_floats(
        result["acquisition_coverage_fraction"],
        field="Sentinel lineage acquisition_coverage_fraction",
        allow_missing=False,
    )
    for column in INDEX_COLUMNS:
        result[column] = _normalize_floats(
            result[column], field=f"Sentinel lineage {column}", allow_missing=True
        )
    result["eligible_pixel_identity_sha256_audit_only"] = _normalize_strings(
        result["eligible_pixel_identity_sha256_audit_only"],
        field="Sentinel lineage eligible_pixel_identity_sha256_audit_only",
    )
    result["included_in_composite"] = _normalize_booleans(
        result["included_in_composite"],
        field="Sentinel lineage included_in_composite",
    )
    _enforce_development_lock(
        result["target_date"], research=research, field="Sentinel lineage"
    )

    natural_key = ["target_date", "tract_geoid", "physical_acquisition_id"]
    if result.duplicated(natural_key).any():
        raise SentinelFeaturePromotionError("Sentinel lineage contains duplicate natural keys.")
    computed_lag = (result["target_date"] - result["acquisition_local_date"]).dt.days
    if (
        not computed_lag.equals(result["lag_days"])
        or not computed_lag.equals(result["source_age_days_audit_only"])
        or not result["source_end_date"].equals(result["acquisition_local_date"])
        or not computed_lag.between(1, 60).all()
    ):
        raise SentinelFeaturePromotionError("Sentinel lineage violates exact d-60:d-1 timing.")
    if not result["acquisition_coverage_fraction"].between(0.0, 1.0).all():
        raise SentinelFeaturePromotionError("Sentinel lineage coverage must be in [0, 1].")
    index_missing = result[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not index_missing.isin([0, len(INDEX_COLUMNS)]).all():
        raise SentinelFeaturePromotionError(
            "Sentinel lineage index values must use one joint-valid mask."
        )
    expected_inclusion = (
        result["acquisition_coverage_fraction"].ge(minimum_coverage)
        & index_missing.eq(0)
    )
    if not result["included_in_composite"].equals(expected_inclusion):
        raise SentinelFeaturePromotionError(
            "Sentinel lineage inclusion disagrees with the frozen coverage gate."
        )
    if (result["eligible_pixel_count_static"] <= 0).any():
        raise SentinelFeaturePromotionError("Sentinel fixed denominators must be positive.")
    valid_identity = result["eligible_pixel_identity_sha256_audit_only"].str.fullmatch(
        _SHA256_PATTERN
    )
    if not valid_identity.all():
        raise SentinelFeaturePromotionError("Sentinel denominator identity hashes are invalid.")
    denominator_groups = result.groupby("tract_geoid", observed=True, sort=False).agg(
        count_values=("eligible_pixel_count_static", "nunique"),
        identity_values=("eligible_pixel_identity_sha256_audit_only", "nunique"),
    )
    if not denominator_groups.eq(1).all(axis=None):
        raise SentinelFeaturePromotionError(
            "Sentinel static eligible-land denominator changed across acquisitions or dates."
        )
    return result


def _assert_exact_key_support(
    observed: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    label: str,
) -> None:
    keys = ["tract_geoid", "target_date"]
    compared = universe[keys].merge(
        observed[keys],
        on=keys,
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not compared["_merge"].eq("both").all():
        counts = compared["_merge"].value_counts().to_dict()
        raise SentinelFeaturePromotionError(
            f"{label} does not exactly match the frozen key universe: {counts}"
        )


def _assert_lineage_contract(
    *,
    lineage: pd.DataFrame,
    membership: pd.DataFrame,
    universe: pd.DataFrame,
    features: pd.DataFrame,
    audit: pd.DataFrame,
    minimum_acquisitions: int,
) -> pd.DataFrame:
    keys = ["tract_geoid", "target_date"]
    lineage_keys = lineage[keys].drop_duplicates()
    _assert_exact_key_support(lineage_keys, universe, label="Sentinel lineage")

    observed_membership = lineage.loc[:, MEMBERSHIP_COLUMNS].drop_duplicates()
    expected_membership = membership.loc[:, MEMBERSHIP_COLUMNS]
    compared = expected_membership.merge(
        observed_membership,
        on=list(MEMBERSHIP_COLUMNS),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not compared["_merge"].eq("both").all():
        raise SentinelFeaturePromotionError(
            "Sentinel lineage does not exactly match frozen target-window membership."
        )
    tract_count = universe["tract_geoid"].nunique()
    membership_sizes = lineage.groupby(
        ["target_date", "physical_acquisition_id"], observed=True, sort=False
    ).size()
    if not membership_sizes.eq(tract_count).all():
        raise SentinelFeaturePromotionError(
            "A Sentinel target-window membership lacks the complete tract universe."
        )

    group_keys = ["tract_geoid", "target_date"]
    computed_audit = (
        lineage.groupby(group_keys, observed=True, sort=False)
        .agg(
            window_membership_count=("physical_acquisition_id", "size"),
            qualifying_acquisition_count=("included_in_composite", "sum"),
            minimum_lag_days=("lag_days", "min"),
            maximum_lag_days=("lag_days", "max"),
            median_acquisition_coverage=("acquisition_coverage_fraction", "median"),
            newest_source_end_date=("source_end_date", "max"),
            oldest_source_end_date=("source_end_date", "min"),
        )
        .reset_index()
    )
    computed_audit["sentinel_feature_available"] = computed_audit[
        "qualifying_acquisition_count"
    ].ge(minimum_acquisitions)
    expected_audit = computed_audit.loc[:, PROMOTED_AUDIT_COLUMNS].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            audit,
            expected_audit,
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as exc:
        raise SentinelFeaturePromotionError(
            "Sentinel audit does not reproduce from frozen lineage."
        ) from exc

    included = lineage.loc[lineage["included_in_composite"]]
    medians = (
        included.groupby(group_keys, observed=True, sort=False)[list(INDEX_COLUMNS)]
        .median()
        .reset_index()
    )
    computed_features = universe.merge(
        medians, on=group_keys, how="left", validate="one_to_one", sort=False
    )
    availability = audit.set_index(group_keys)["sentinel_feature_available"]
    computed_key = pd.MultiIndex.from_frame(computed_features[group_keys])
    unavailable = ~availability.reindex(computed_key).to_numpy(dtype=bool)
    computed_features.loc[unavailable, list(INDEX_COLUMNS)] = np.nan
    computed_features = computed_features.loc[:, PROMOTED_FEATURE_COLUMNS].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            features,
            computed_features,
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as exc:
        raise SentinelFeaturePromotionError(
            "Sentinel feature medians do not reproduce from frozen lineage."
        ) from exc

    feature_missing = features[list(INDEX_COLUMNS)].isna().all(axis=1)
    if not feature_missing.equals(~audit["sentinel_feature_available"]):
        raise SentinelFeaturePromotionError(
            "Sentinel all-feature missingness disagrees with audited availability."
        )
    return computed_audit


def _coverage_report(audit: pd.DataFrame, *, group_column: str) -> pd.DataFrame:
    report = (
        audit.groupby(group_column, observed=True, sort=True)
        .agg(
            tract_date_row_count=("sentinel_feature_available", "size"),
            feature_available_row_count=("sentinel_feature_available", "sum"),
            window_membership_count_min=("window_membership_count", "min"),
            window_membership_count_median=("window_membership_count", "median"),
            window_membership_count_max=("window_membership_count", "max"),
            qualifying_acquisition_count_min=("qualifying_acquisition_count", "min"),
            qualifying_acquisition_count_median=(
                "qualifying_acquisition_count",
                "median",
            ),
            qualifying_acquisition_count_max=("qualifying_acquisition_count", "max"),
            median_acquisition_coverage_median=(
                "median_acquisition_coverage",
                "median",
            ),
        )
        .reset_index()
    )
    report["feature_missing_row_count"] = (
        report["tract_date_row_count"] - report["feature_available_row_count"]
    )
    report["feature_available_fraction"] = (
        report["feature_available_row_count"] / report["tract_date_row_count"]
    )
    order = (
        group_column,
        "tract_date_row_count",
        "feature_available_row_count",
        "feature_missing_row_count",
        "feature_available_fraction",
        "window_membership_count_min",
        "window_membership_count_median",
        "window_membership_count_max",
        "qualifying_acquisition_count_min",
        "qualifying_acquisition_count_median",
        "qualifying_acquisition_count_max",
        "median_acquisition_coverage_median",
    )
    return report.loc[:, order]


def _validate_feature_universe_provenance(
    marker: dict[str, Any],
    *,
    path: Path,
    frame: pd.DataFrame,
    research: ResearchConfig,
) -> str:
    if marker.get("target_blind") is not True or marker.get("target_tables_read") != []:
        raise SentinelFeaturePromotionError(
            "Feature universe provenance is not explicitly target-blind."
        )
    if int(marker.get("final_test_year", -1)) != research.final_test_year:
        raise SentinelFeaturePromotionError(
            "Feature universe provenance disagrees with the final-test year."
        )
    record = marker.get("output_files", {}).get(path.name)
    _require_parquet_record(path, frame, record, label="Feature universe")
    semantic = canonical_frame_sha256(
        frame, sort_by=["target_date", "tract_geoid"], columns=["tract_geoid", "target_date"]
    )
    if marker.get("semantic_key_sha256") != semantic:
        raise SentinelFeaturePromotionError(
            "Feature universe semantic key hash disagrees with its provenance."
        )
    return semantic


def _validate_registry(
    registry: pd.DataFrame,
    marker: dict[str, Any],
    *,
    path: Path,
    file_sha256: str,
    development_start: str,
) -> str:
    validate_feature_registry(registry, development_start=development_start)
    if marker.get("registry_contract_valid") is not True:
        raise SentinelFeaturePromotionError("Phase 2 registry provenance is not valid.")
    record = marker.get("output_files", {}).get(path.name)
    if not isinstance(record, dict) or any(
        (
            record.get("sha256") != file_sha256,
            record.get("bytes") != path.stat().st_size,
            record.get("rows") != len(registry),
        )
    ):
        raise SentinelFeaturePromotionError(
            "Phase 2 registry disagrees with its committed provenance."
        )
    expected = sentinel_feature_registry_rows().reset_index(drop=True)
    observed = registry.loc[registry["family"].eq("satellite")].reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(observed, expected, check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise SentinelFeaturePromotionError(
            "Phase 2 registry Sentinel fragment changed from the frozen contract."
        ) from exc
    ordered_semantic = canonical_sha256(registry.to_dict("records"))
    if marker.get("ordered_registry_semantic_sha256") != ordered_semantic:
        raise SentinelFeaturePromotionError(
            "Phase 2 registry ordered semantic hash is invalid."
        )
    return ordered_semantic


def _research_dependency_payload(research: ResearchConfig) -> dict[str, Any]:
    return {
        "study": {
            "final_test_year": research.final_test_year,
            "unlock_final_test": research.final_test_unlocked,
        },
        "static_land_mask": research.raw["static_land_mask"],
    }


def _verify_sources_unchanged(source_hashes: dict[Path, str]) -> None:
    changed = [
        str(path)
        for path, expected in source_hashes.items()
        if sha256_file(path) != expected
    ]
    if changed:
        raise RuntimeError(f"Promotion inputs changed during validation: {changed}")


def promote_sentinel_features(
    *,
    source_directory: str | Path = DEFAULT_SOURCE_DIRECTORY,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    feature_universe_path: str | Path = DEFAULT_FEATURE_UNIVERSE_PATH,
    feature_universe_provenance_path: str | Path | None = None,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    registry_provenance_path: str | Path | None = None,
    inventory_directory: str | Path = DEFAULT_INVENTORY_DIRECTORY,
    research_config_path: str | Path = DEFAULT_RESEARCH_CONFIG_PATH,
    sentinel_config_path: str | Path = DEFAULT_SENTINEL_CONFIG_PATH,
) -> dict[str, Any]:
    """Promote exact Sentinel predictors after a target-blind, fail-closed audit."""

    source = Path(source_directory).resolve()
    output = Path(output_directory).resolve()
    universe_path = Path(feature_universe_path).resolve()
    universe_marker_path = (
        Path(feature_universe_provenance_path).resolve()
        if feature_universe_provenance_path is not None
        else universe_path.with_name("feature_key_universe_provenance.json")
    )
    registry_file = Path(registry_path).resolve()
    registry_marker_path = (
        Path(registry_provenance_path).resolve()
        if registry_provenance_path is not None
        else registry_file.with_name(PHASE2_REGISTRY_PROVENANCE_FILENAME)
    )
    inventory = Path(inventory_directory).resolve()
    research_path = Path(research_config_path).resolve()
    sentinel_path = Path(sentinel_config_path).resolve()

    output.mkdir(parents=True, exist_ok=True)
    marker_path = output / PROMOTION_PROVENANCE_FILENAME
    marker_path.unlink(missing_ok=True)

    source_feature_path = source / PROMOTED_FEATURE_FILENAME
    source_audit_path = source / PROMOTED_AUDIT_FILENAME
    source_lineage_path = source / "sentinel_lineage.parquet"
    progress_path = source / "build_progress.json"
    fingerprint_path = source / "pipeline_fingerprint.json"
    inventory_summary_path = inventory / "inventory_summary.json"
    membership_path = inventory / "target_window_membership.csv"

    progress, progress_sha256 = _stable_json(progress_path, label="Sentinel build progress")
    fingerprint, fingerprint_file_sha256 = _stable_json(
        fingerprint_path, label="Sentinel scientific fingerprint"
    )
    inventory_summary, inventory_summary_sha256 = _stable_json(
        inventory_summary_path, label="Sentinel inventory summary"
    )
    universe_marker, universe_marker_sha256 = _committed_json(
        universe_marker_path, label="Feature universe provenance"
    )
    registry_marker, registry_marker_sha256 = _committed_json(
        registry_marker_path, label="Phase 2 registry provenance"
    )

    research_file_sha256 = sha256_file(research_path)
    research = load_config(research_path)
    if sha256_file(research_path) != research_file_sha256:
        raise RuntimeError("Research configuration changed while it was being read.")
    if research.final_test_unlocked:
        raise PermissionError(
            "Sentinel development promotion requires unlock_final_test=false; "
            "2025 must remain locked."
        )
    sentinel_file_sha256 = sha256_file(sentinel_path)
    sentinel_config = load_sentinel_stage_config(sentinel_path)
    if sha256_file(sentinel_path) != sentinel_file_sha256:
        raise RuntimeError("Sentinel configuration changed while it was being read.")

    if (
        progress.get("state") != "complete"
        or progress.get("promoted_outputs_valid") is not True
        or progress.get("build_complete") is not True
        or progress.get("completed_physical_acquisition_count")
        != progress.get("expected_physical_acquisition_count")
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel source build is not complete with valid aggregate outputs."
        )
    if progress.get("compile_adapter_version_audit_only") != EXPECTED_COMPILE_ADAPTER_VERSION:
        raise SentinelFeaturePromotionError("Unexpected Sentinel compile adapter version.")
    if progress.get("sentinel_feature_pipeline_sha256") != (
        EXPECTED_SENTINEL_SCIENTIFIC_SHA256
    ):
        raise SentinelFeaturePromotionError("Unexpected Sentinel scientific processor SHA.")
    if canonical_sha256(fingerprint) != EXPECTED_SENTINEL_SCIENTIFIC_SHA256:
        raise SentinelFeaturePromotionError(
            "Sentinel scientific fingerprint does not reproduce its locked SHA."
        )
    if progress.get("sentinel_feature_pipeline_fingerprint_file_sha256") != (
        fingerprint_file_sha256
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel scientific fingerprint file hash changed."
        )
    if (
        progress.get("sentinel_stage_config_sha256") != sentinel_config.sha256
        or progress.get("sentinel_stage_config_payload") != sentinel_config.raw
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel source build disagrees with the frozen stage configuration."
        )
    research_dependency = _research_dependency_payload(research)
    if (
        progress.get("research_config_file_sha256_audit_only") != research_file_sha256
        or progress.get("sentinel_research_dependency_payload") != research_dependency
        or progress.get("sentinel_research_dependency_sha256")
        != canonical_sha256(research_dependency)
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel source build disagrees with current locked research dependencies."
        )

    if (
        inventory_summary.get("state") != "complete"
        or inventory_summary.get("artifacts_valid") is not True
        or int(inventory_summary.get("final_test_year", -1)) != research.final_test_year
        or bool(inventory_summary.get("unlock_final_test", True))
        or inventory_summary.get("global_scene_cloud_cover_filter") is not None
    ):
        raise SentinelFeaturePromotionError("Sentinel inventory is not a locked development input.")
    if (
        progress.get("sentinel_inventory_summary_sha256_audit_only")
        != inventory_summary_sha256
        or progress.get("sentinel_inventory_semantic_sha256")
        != inventory_summary.get("sentinel_inventory_semantic_sha256")
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel inventory summary disagrees with the source build."
        )

    source_features_raw, source_feature_sha256 = _stable_parquet(
        source_feature_path, label="Sentinel features"
    )
    source_audit_raw, source_audit_sha256 = _stable_parquet(
        source_audit_path, label="Sentinel feature audit"
    )
    source_lineage_raw, source_lineage_sha256 = _stable_parquet(
        source_lineage_path, label="Sentinel lineage"
    )
    universe_raw, universe_sha256 = _stable_parquet(
        universe_path, label="Feature key universe"
    )
    registry, registry_sha256 = _stable_csv(registry_file, label="Phase 2 registry")
    membership_raw, membership_sha256 = _stable_csv(
        membership_path, label="Sentinel target-window membership"
    )

    aggregate = progress.get("aggregate_outputs", {})
    source_records = {
        PROMOTED_FEATURE_FILENAME: _require_parquet_record(
            source_feature_path,
            source_features_raw,
            aggregate.get(PROMOTED_FEATURE_FILENAME),
            label="Sentinel features",
        ),
        PROMOTED_AUDIT_FILENAME: _require_parquet_record(
            source_audit_path,
            source_audit_raw,
            aggregate.get(PROMOTED_AUDIT_FILENAME),
            label="Sentinel feature audit",
        ),
        "sentinel_lineage.parquet": _require_parquet_record(
            source_lineage_path,
            source_lineage_raw,
            aggregate.get("sentinel_lineage.parquet"),
            label="Sentinel lineage",
        ),
    }
    membership_record = inventory_summary.get("output_files", {}).get(membership_path.name)
    if not isinstance(membership_record, dict) or any(
        (
            membership_record.get("sha256") != membership_sha256,
            membership_record.get("bytes") != membership_path.stat().st_size,
            membership_record.get("rows") != len(membership_raw),
            progress.get("sentinel_target_window_membership_csv_sha256")
            != membership_sha256,
        )
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel membership disagrees with inventory/source provenance."
        )

    universe = _normalize_universe(universe_raw, research=research)
    universe_semantic_sha256 = _validate_feature_universe_provenance(
        universe_marker,
        path=universe_path,
        frame=universe_raw,
        research=research,
    )
    ordered_registry_sha256 = _validate_registry(
        registry,
        registry_marker,
        path=registry_file,
        file_sha256=registry_sha256,
        development_start=str(research.raw["study"].get("start_date", DEVELOPMENT_START)),
    )
    features = _normalize_features(source_features_raw, research=research)
    audit = _normalize_audit(source_audit_raw, research=research)
    membership = _normalize_membership(membership_raw, research=research)
    lineage = _normalize_lineage(
        source_lineage_raw,
        research=research,
        minimum_coverage=sentinel_config.minimum_coverage,
    )
    _assert_exact_key_support(features, universe, label="Sentinel features")
    _assert_exact_key_support(audit, universe, label="Sentinel audit")
    if set(membership["target_date"]) != set(universe["target_date"]):
        raise SentinelFeaturePromotionError(
            "Sentinel memberships do not cover every frozen target date."
        )
    _assert_lineage_contract(
        lineage=lineage,
        membership=membership,
        universe=universe,
        features=features,
        audit=audit,
        minimum_acquisitions=sentinel_config.minimum_acquisitions,
    )

    if not audit["sentinel_feature_available"].equals(
        audit["qualifying_acquisition_count"].ge(sentinel_config.minimum_acquisitions)
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel availability disagrees with the frozen acquisition minimum."
        )
    if (
        int(progress.get("feature_row_count", -1)) != len(features)
        or int(progress.get("feature_available_row_count", -1))
        != int(audit["sentinel_feature_available"].sum())
        or int(progress.get("target_date_count", -1))
        != universe["target_date"].nunique()
        or int(progress.get("tract_count", -1)) != universe["tract_geoid"].nunique()
        or int(progress.get("lineage_row_count", -1)) != len(lineage)
    ):
        raise SentinelFeaturePromotionError(
            "Sentinel source progress summary disagrees with validated outputs."
        )

    coverage_by_date = _coverage_report(audit, group_column="target_date")
    coverage_by_tract = _coverage_report(audit, group_column="tract_geoid")
    denominator = (
        lineage.groupby("tract_geoid", observed=True, sort=True)
        .agg(
            eligible_pixel_count_static=("eligible_pixel_count_static", "first"),
            eligible_pixel_identity_sha256=(
                "eligible_pixel_identity_sha256_audit_only",
                "first",
            ),
        )
        .reset_index()
    )

    source_hashes = {
        progress_path: progress_sha256,
        fingerprint_path: fingerprint_file_sha256,
        inventory_summary_path: inventory_summary_sha256,
        membership_path: membership_sha256,
        source_feature_path: source_feature_sha256,
        source_audit_path: source_audit_sha256,
        source_lineage_path: source_lineage_sha256,
        universe_path: universe_sha256,
        universe_marker_path: universe_marker_sha256,
        registry_file: registry_sha256,
        registry_marker_path: registry_marker_sha256,
        research_path: research_file_sha256,
        sentinel_path: sentinel_file_sha256,
    }
    _verify_sources_unchanged(source_hashes)

    output_frames = {
        PROMOTED_FEATURE_FILENAME: features,
        PROMOTED_AUDIT_FILENAME: audit,
        COVERAGE_BY_DATE_FILENAME: coverage_by_date,
        COVERAGE_BY_TRACT_FILENAME: coverage_by_tract,
    }
    for filename, frame in output_frames.items():
        atomic_parquet(frame, output / filename)
        frozen = pd.read_parquet(output / filename)
        try:
            pd.testing.assert_frame_equal(frozen, frame, check_dtype=True, check_exact=True)
        except AssertionError as exc:
            raise RuntimeError(f"Promoted output failed round-trip validation: {filename}") from exc

    _verify_sources_unchanged(source_hashes)
    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/promote_sentinel_features.py",
            "src/la_heat/config.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/phase2_registry.py",
            "src/la_heat/provenance.py",
            "src/la_heat/sentinel_feature_builder.py",
            "src/la_heat/sentinel_feature_stage.py",
            "src/la_heat/sentinel_features.py",
        ),
        algorithm_version=PROMOTION_ALGORITHM_VERSION,
    )
    available_count = int(audit["sentinel_feature_available"].sum())
    missing_count = len(audit) - available_count
    payload: dict[str, Any] = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "algorithm_version": PROMOTION_ALGORITHM_VERSION,
        "state": "complete",
        "promoted_outputs_valid": True,
        "phase2_complete": False,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "final_test_year": research.final_test_year,
        "final_test_unlocked": research.final_test_unlocked,
        "row_count": len(features),
        "date_count": int(universe["target_date"].nunique()),
        "tract_count": int(universe["tract_geoid"].nunique()),
        "feature_names": list(INDEX_COLUMNS),
        "feature_available_row_count": available_count,
        "feature_missing_row_count": missing_count,
        "feature_available_fraction": available_count / len(audit),
        "lineage_row_count": len(lineage),
        "fixed_denominator_invariant": True,
        "output_directory": str(output),
        "semantic_key_sha256": universe_semantic_sha256,
        "semantic_feature_table_sha256": canonical_frame_sha256(
            features, sort_by=["target_date", "tract_geoid"]
        ),
        "semantic_audit_table_sha256": canonical_frame_sha256(
            audit, sort_by=["target_date", "tract_geoid"]
        ),
        "fixed_denominator_semantic_sha256": canonical_frame_sha256(
            denominator, sort_by=["tract_geoid"]
        ),
        "scientific_processor_sha256": EXPECTED_SENTINEL_SCIENTIFIC_SHA256,
        "compile_adapter_version": EXPECTED_COMPILE_ADAPTER_VERSION,
        "sentinel_stage_config_semantic_sha256": sentinel_config.sha256,
        "registry_ordered_semantic_sha256": ordered_registry_sha256,
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "coverage_contract": {
            "minimum_acquisition_coverage_fraction": sentinel_config.minimum_coverage,
            "minimum_physical_acquisitions": sentinel_config.minimum_acquisitions,
            "missingness_rule": "all five model predictors present or all five missing",
            "imputation_performed": False,
            "rows_removed_for_sentinel_missingness": 0,
            "minimum_date_available_fraction": float(
                coverage_by_date["feature_available_fraction"].min()
            ),
            "minimum_tract_available_fraction": float(
                coverage_by_tract["feature_available_fraction"].min()
            ),
            "dates_with_any_missing": int(
                coverage_by_date["feature_missing_row_count"].gt(0).sum()
            ),
            "tracts_with_any_missing": int(
                coverage_by_tract["feature_missing_row_count"].gt(0).sum()
            ),
        },
        "temporal_contract": {
            "window": "local civil dates d-60 through d-1",
            "minimum_source_age_days": int(lineage["source_age_days_audit_only"].min()),
            "maximum_source_age_days": int(lineage["source_age_days_audit_only"].max()),
            "target_day_or_future_rows": 0,
        },
        "inputs": {
            "build_progress": {
                "path": str(progress_path),
                "sha256": progress_sha256,
                "state": progress["state"],
            },
            "scientific_fingerprint": {
                "path": str(fingerprint_path),
                "sha256": fingerprint_file_sha256,
                "semantic_sha256": EXPECTED_SENTINEL_SCIENTIFIC_SHA256,
            },
            "source_outputs": {
                filename: {
                    "path": str(source / filename),
                    **record,
                }
                for filename, record in source_records.items()
            },
            "inventory_summary": {
                "path": str(inventory_summary_path),
                "sha256": inventory_summary_sha256,
                "semantic_sha256": inventory_summary[
                    "sentinel_inventory_semantic_sha256"
                ],
            },
            "target_window_membership": {
                "path": str(membership_path),
                "sha256": membership_sha256,
                "rows": len(membership),
            },
            "feature_key_universe": {
                "path": str(universe_path),
                "sha256": universe_sha256,
                "provenance_path": str(universe_marker_path),
                "provenance_sha256": universe_marker_sha256,
                "provenance_commit_sha256": universe_marker["commit_sha256"],
                "semantic_key_sha256": universe_semantic_sha256,
            },
            "phase2_registry": {
                "path": str(registry_file),
                "sha256": registry_sha256,
                "provenance_path": str(registry_marker_path),
                "provenance_sha256": registry_marker_sha256,
                "provenance_commit_sha256": registry_marker["commit_sha256"],
                "ordered_semantic_sha256": ordered_registry_sha256,
            },
            "research_config": {
                "path": str(research_path),
                "sha256": research_file_sha256,
                "selected_dependency_sha256": canonical_sha256(research_dependency),
            },
            "sentinel_stage_config": {
                "path": str(sentinel_path),
                "sha256": sentinel_file_sha256,
                "semantic_sha256": sentinel_config.sha256,
            },
        },
        "output_files": {
            filename: {
                "path": str(output / filename),
                **parquet_file_record(output / filename, frame),
            }
            for filename, frame in output_frames.items()
        },
        "remaining_gate": (
            "Sentinel predictors are independently promoted; Phase 2 remains incomplete "
            "until Daymet values and the full registry-driven feature table are promoted."
        ),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    return payload
