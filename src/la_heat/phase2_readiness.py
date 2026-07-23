"""Target-blind readiness audit for the four Phase 2 predictor families.

This stage deliberately stops before joining Landsat labels.  It proves that the
frozen key universe, registry, static features, calendar features, and Sentinel-2
features agree exactly, then reports Daymet as a blocker until a separately
provenanced Daymet feature table exists.  A blocked audit is a successful audit,
not a promoted Phase 2 dataset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.daymet_feature_stage import (
    DAYMET_PRIMARY_WINDOWS,
    DAYMET_WINDOW_AUDIT_FIELDS,
)
from la_heat.feature_registry import validate_feature_registry
from la_heat.feature_universe import KEY_COLUMNS, validate_feature_key_universe
from la_heat.phase2_registry import (
    EXPECTED_MODEL_ROWS,
    EXPECTED_TOTAL_ROWS,
    PHASE2_REGISTRY_FILENAME,
    PHASE2_REGISTRY_PROVENANCE_FILENAME,
    construct_phase2_registry,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_features import INDEX_COLUMNS

READINESS_SCHEMA_VERSION: Final = 1
READINESS_ALGORITHM_VERSION: Final = "phase2-target-blind-readiness-v1"
READINESS_FILENAME: Final = "phase2_readiness.json"
FAMILY_SUMMARY_FILENAME: Final = "feature_family_readiness.csv"

DEFAULT_OUTPUT_DIRECTORY: Final = Path("manifests/phase2_readiness")
DEFAULT_KEY_PATH: Final = Path(
    "data/interim/features/feature_key_universe/feature_key_universe.parquet"
)
DEFAULT_KEY_PROVENANCE_PATH: Final = DEFAULT_KEY_PATH.with_name(
    "feature_key_universe_provenance.json"
)
DEFAULT_REGISTRY_PATH: Final = Path("manifests/phase2_registry") / PHASE2_REGISTRY_FILENAME
DEFAULT_REGISTRY_PROVENANCE_PATH: Final = (
    Path("manifests/phase2_registry") / PHASE2_REGISTRY_PROVENANCE_FILENAME
)
DEFAULT_STATIC_PATH: Final = Path("data/processed/static_features/static_features.parquet")
DEFAULT_STATIC_AUDIT_PATH: Final = DEFAULT_STATIC_PATH.with_name(
    "static_feature_audit.parquet"
)
DEFAULT_STATIC_REGISTRY_PATH: Final = DEFAULT_STATIC_PATH.with_name(
    "static_feature_registry.csv"
)
DEFAULT_STATIC_PROVENANCE_PATH: Final = DEFAULT_STATIC_PATH.with_name(
    "static_features_provenance.json"
)
DEFAULT_CALENDAR_PATH: Final = Path(
    "data/interim/features/calendar/calendar_features.parquet"
)
DEFAULT_CALENDAR_PROVENANCE_PATH: Final = DEFAULT_CALENDAR_PATH.with_name(
    "calendar_features_provenance.json"
)
DEFAULT_SENTINEL_DIRECTORY: Final = Path("data/processed/sentinel_features")
DEFAULT_SENTINEL_LINEAGE_PATH: Final = Path(
    "data/interim/sentinel_features/sentinel_lineage.parquet"
)
DEFAULT_DAYMET_INVENTORY_PATH: Final = Path("manifests/daymet_grid/granule_inventory.csv")
DEFAULT_DAYMET_SUMMARY_PATH: Final = Path("manifests/daymet_grid/inventory_summary.json")
DEFAULT_DAYMET_SUBSET_MANIFEST_PATH: Final = Path("manifests/daymet_grid/subset_downloads.csv")
DEFAULT_DAYMET_FEATURE_PATH: Final = Path(
    "data/interim/features/daymet/daymet_features.parquet"
)
DEFAULT_DAYMET_PROVENANCE_PATH: Final = DEFAULT_DAYMET_FEATURE_PATH.with_name(
    "daymet_features_provenance.json"
)

CALENDAR_COLUMNS: Final[tuple[str, ...]] = ("calendar_doy_sin", "calendar_doy_cos")
SENTINEL_AUDIT_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "window_membership_count",
    "qualifying_acquisition_count",
    "minimum_lag_days",
    "maximum_lag_days",
    "median_acquisition_coverage",
    "newest_source_end_date",
    "oldest_source_end_date",
    "sentinel_feature_available",
)
SENTINEL_LINEAGE_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "physical_acquisition_id",
    "acquisition_local_date",
    "lag_days",
    "source_end_date",
    "source_age_days_audit_only",
    "eligible_pixel_count_static",
    "eligible_pixel_identity_sha256_audit_only",
    "included_in_composite",
)


class Phase2ReadinessError(ValueError):
    """Raised when a present Phase 2 input fails its target-blind audit."""


def _load_json(path: Path, *, require_commit: bool) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase2ReadinessError(f"Cannot read JSON provenance {path}.") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise Phase2ReadinessError(f"JSON provenance must be an object: {path}")
    if require_commit:
        recorded = payload.get("commit_sha256")
        without_commit = {key: value for key, value in payload.items() if key != "commit_sha256"}
        if not isinstance(recorded, str) or canonical_sha256(without_commit) != recorded:
            raise Phase2ReadinessError(f"Invalid provenance commit hash: {path}")
    return payload, before


def _read_parquet(path: Path) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    frame = pd.read_parquet(path)
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Input changed while being read: {path}")
    return frame, before


def _read_csv(path: Path, **kwargs: Any) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    frame = pd.read_csv(path, **kwargs)
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Input changed while being read: {path}")
    return frame, before


def _parse_keys(
    frame: pd.DataFrame,
    *,
    label: str,
    final_test_year: int,
    require_unique: bool = True,
) -> pd.DataFrame:
    missing = sorted(set(KEY_COLUMNS) - set(frame.columns))
    if missing:
        raise Phase2ReadinessError(f"{label} is missing key columns: {missing}")
    keys = frame.loc[:, list(KEY_COLUMNS)].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    try:
        keys["target_date"] = pd.to_datetime(keys["target_date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise Phase2ReadinessError(f"{label} has invalid target dates.") from exc
    if keys.isna().any(axis=None):
        raise Phase2ReadinessError(f"{label} has missing tract-date keys.")
    if keys["target_date"].dt.tz is not None:
        raise Phase2ReadinessError(f"{label} target dates must be timezone-naive.")
    if not keys["target_date"].dt.normalize().equals(keys["target_date"]):
        raise Phase2ReadinessError(f"{label} target dates must be civil midnights.")
    if keys["target_date"].dt.year.ge(final_test_year).any():
        raise PermissionError(f"{label} contains locked {final_test_year}+ rows.")
    if require_unique and keys.duplicated(list(KEY_COLUMNS)).any():
        raise Phase2ReadinessError(f"{label} has duplicate tract-date keys.")
    return keys


def _assert_exact_key_coverage(
    frame: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    label: str,
    final_test_year: int,
) -> pd.DataFrame:
    observed = _parse_keys(frame, label=label, final_test_year=final_test_year)
    expected = _parse_keys(
        universe,
        label="feature key universe",
        final_test_year=final_test_year,
    )
    comparison = expected.merge(
        observed,
        on=list(KEY_COLUMNS),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    missing = int(comparison["_merge"].eq("left_only").sum())
    extra = int(comparison["_merge"].eq("right_only").sum())
    if missing or extra:
        raise Phase2ReadinessError(
            f"{label} key coverage mismatch: missing={missing}, extra={extra}."
        )
    return observed


def _require_exact_columns(
    frame: pd.DataFrame,
    *,
    expected: set[str],
    label: str,
) -> None:
    observed = set(frame.columns)
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing or extra:
        raise Phase2ReadinessError(
            f"{label} schema mismatch: missing={missing}, extra={extra}."
        )
    if frame.columns.duplicated().any():
        raise Phase2ReadinessError(f"{label} contains duplicate columns.")


def _require_numeric(
    frame: pd.DataFrame,
    columns: list[str] | tuple[str, ...],
    *,
    label: str,
    allow_missing: bool,
) -> np.ndarray:
    try:
        numeric = frame.loc[:, list(columns)].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as exc:
        raise Phase2ReadinessError(f"{label} features must be numeric.") from exc
    values = numeric.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any():
        raise Phase2ReadinessError(f"{label} features contain infinite values.")
    if not allow_missing and np.isnan(values).any():
        raise Phase2ReadinessError(f"{label} features contain missing values.")
    return values


def validate_ready_feature_families(
    *,
    key_universe: pd.DataFrame,
    registry: pd.DataFrame,
    static_features: pd.DataFrame,
    static_audit: pd.DataFrame,
    calendar_features: pd.DataFrame,
    sentinel_features: pd.DataFrame,
    sentinel_audit: pd.DataFrame,
    sentinel_lineage: pd.DataFrame,
    final_test_year: int = 2025,
) -> list[dict[str, Any]]:
    """Validate every currently complete predictor family without labels."""

    validate_feature_key_universe(key_universe, final_test_year=final_test_year)
    universe = key_universe.copy()
    universe["target_date"] = pd.to_datetime(universe["target_date"])
    date_count = int(universe["target_date"].nunique())
    tract_count = int(universe["tract_geoid"].nunique())
    if len(universe) != date_count * tract_count:
        raise Phase2ReadinessError("Feature key universe is not a complete date × tract grid.")

    validate_feature_registry(registry, development_start="2020-05-01")
    if len(registry) != EXPECTED_TOTAL_ROWS:
        raise Phase2ReadinessError(
            f"Phase 2 registry must contain {EXPECTED_TOTAL_ROWS} rows."
        )
    role_counts = registry["role"].value_counts().to_dict()
    if role_counts != {"model": EXPECTED_MODEL_ROWS, "key": 2, "audit_only": 1}:
        raise Phase2ReadinessError(f"Phase 2 registry role counts changed: {role_counts}")

    declared_static = registry.loc[
        registry["static"].astype(bool) & ~registry["role"].eq("key"), "feature_name"
    ].tolist()
    declared_static_model = registry.loc[
        registry["static"].astype(bool) & registry["role"].eq("model"), "feature_name"
    ].tolist()
    _require_exact_columns(
        static_features,
        expected={"tract_geoid", *declared_static},
        label="static feature table",
    )
    if static_features["tract_geoid"].isna().any() or static_features[
        "tract_geoid"
    ].duplicated().any():
        raise Phase2ReadinessError("Static feature table must have one row per tract.")
    expected_tracts = set(universe["tract_geoid"].astype(str))
    observed_tracts = set(static_features["tract_geoid"].astype(str))
    if observed_tracts != expected_tracts:
        raise Phase2ReadinessError("Static feature tract universe does not match key universe.")
    _require_numeric(
        static_features,
        declared_static,
        label="static",
        allow_missing=False,
    )

    static_audit_required = {
        "tract_geoid",
        "eligible_pixel_count_static",
        "eligible_pixel_identity_sha256",
    }
    missing_static_audit = sorted(static_audit_required - set(static_audit.columns))
    if missing_static_audit:
        raise Phase2ReadinessError(
            f"Static audit is missing columns: {missing_static_audit}"
        )
    if static_audit["tract_geoid"].duplicated().any():
        raise Phase2ReadinessError("Static audit has duplicate tracts.")
    if set(static_audit["tract_geoid"].astype(str)) != expected_tracts:
        raise Phase2ReadinessError("Static audit tract universe does not match key universe.")
    static_denominator = static_audit.loc[
        :,
        [
            "tract_geoid",
            "eligible_pixel_count_static",
            "eligible_pixel_identity_sha256",
        ],
    ].copy()
    if (pd.to_numeric(static_denominator["eligible_pixel_count_static"]) <= 0).any():
        raise Phase2ReadinessError("Static eligible-land denominators must be positive.")
    if static_denominator["eligible_pixel_identity_sha256"].isna().any():
        raise Phase2ReadinessError("Static eligible-land identities may not be missing.")

    _require_exact_columns(
        calendar_features,
        expected={*KEY_COLUMNS, *CALENDAR_COLUMNS},
        label="calendar feature table",
    )
    _assert_exact_key_coverage(
        calendar_features,
        universe,
        label="calendar feature table",
        final_test_year=final_test_year,
    )
    _require_numeric(
        calendar_features,
        CALENDAR_COLUMNS,
        label="calendar",
        allow_missing=False,
    )

    _require_exact_columns(
        sentinel_features,
        expected={*KEY_COLUMNS, *INDEX_COLUMNS},
        label="Sentinel feature table",
    )
    _assert_exact_key_coverage(
        sentinel_features,
        universe,
        label="Sentinel feature table",
        final_test_year=final_test_year,
    )
    sentinel_values = _require_numeric(
        sentinel_features,
        INDEX_COLUMNS,
        label="Sentinel",
        allow_missing=True,
    )

    sentinel_audit_expected = {*KEY_COLUMNS, *SENTINEL_AUDIT_REQUIRED_COLUMNS}
    _require_exact_columns(
        sentinel_audit,
        expected=sentinel_audit_expected,
        label="Sentinel audit table",
    )
    _assert_exact_key_coverage(
        sentinel_audit,
        universe,
        label="Sentinel audit table",
        final_test_year=final_test_year,
    )
    availability = sentinel_audit["sentinel_feature_available"]
    if availability.isna().any() or not availability.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise Phase2ReadinessError("Sentinel availability must contain booleans only.")
    memberships = pd.to_numeric(
        sentinel_audit["window_membership_count"], errors="raise"
    )
    qualifying = pd.to_numeric(
        sentinel_audit["qualifying_acquisition_count"], errors="raise"
    )
    if (memberships < 1).any() or (qualifying < 0).any() or (qualifying > memberships).any():
        raise Phase2ReadinessError("Sentinel acquisition counts are invalid.")
    if not availability.to_numpy(dtype=bool).tolist() == qualifying.ge(3).tolist():
        raise Phase2ReadinessError(
            "Sentinel availability must mean at least three qualifying acquisitions."
        )
    median_coverage = pd.to_numeric(
        sentinel_audit["median_acquisition_coverage"], errors="raise"
    )
    if not median_coverage.between(0, 1, inclusive="both").all():
        raise Phase2ReadinessError("Sentinel median coverage must be within [0, 1].")
    audit_target_dates = pd.to_datetime(sentinel_audit["target_date"], errors="raise")
    audit_newest = pd.to_datetime(
        sentinel_audit["newest_source_end_date"], errors="raise"
    )
    audit_oldest = pd.to_datetime(
        sentinel_audit["oldest_source_end_date"], errors="raise"
    )
    minimum_lag = pd.to_numeric(sentinel_audit["minimum_lag_days"], errors="raise")
    maximum_lag = pd.to_numeric(sentinel_audit["maximum_lag_days"], errors="raise")
    if (audit_newest >= audit_target_dates).any() or (audit_oldest >= audit_target_dates).any():
        raise Phase2ReadinessError("Sentinel audit contains target-day or future sources.")
    if not minimum_lag.equals((audit_target_dates - audit_newest).dt.days):
        raise Phase2ReadinessError("Sentinel minimum lag disagrees with newest source date.")
    if not maximum_lag.equals((audit_target_dates - audit_oldest).dt.days):
        raise Phase2ReadinessError("Sentinel maximum lag disagrees with oldest source date.")
    if not minimum_lag.between(1, 60, inclusive="both").all() or not maximum_lag.between(
        1, 60, inclusive="both"
    ).all():
        raise Phase2ReadinessError("Sentinel audit lags must remain inside d-60 through d-1.")
    feature_keyed = sentinel_features.copy()
    feature_keyed["target_date"] = pd.to_datetime(feature_keyed["target_date"])
    audit_keyed = sentinel_audit.copy()
    audit_keyed["target_date"] = pd.to_datetime(audit_keyed["target_date"])
    aligned = audit_keyed.loc[:, [*KEY_COLUMNS, "sentinel_feature_available"]].merge(
        feature_keyed,
        on=list(KEY_COLUMNS),
        validate="one_to_one",
    )
    aligned_values = aligned.loc[:, INDEX_COLUMNS].to_numpy(dtype=float, na_value=np.nan)
    available = aligned["sentinel_feature_available"].to_numpy(dtype=bool)
    if not np.isfinite(aligned_values[available]).all():
        raise Phase2ReadinessError(
            "Every available Sentinel row must have all five finite features."
        )
    if not np.isnan(aligned_values[~available]).all():
        raise Phase2ReadinessError(
            "Every unavailable Sentinel row must have all five features missing."
        )
    if np.any(np.isfinite(sentinel_values).sum(axis=1) == 0) != bool((~available).any()):
        raise AssertionError("Sentinel missing-row audit is internally inconsistent.")

    missing_lineage = sorted(
        {*KEY_COLUMNS, *SENTINEL_LINEAGE_REQUIRED_COLUMNS} - set(sentinel_lineage.columns)
    )
    if missing_lineage:
        raise Phase2ReadinessError(
            f"Sentinel lineage is missing columns: {missing_lineage}"
        )
    lineage_keys = _parse_keys(
        sentinel_lineage,
        label="Sentinel lineage",
        final_test_year=final_test_year,
        require_unique=False,
    )
    universe_membership = lineage_keys.merge(
        universe,
        on=list(KEY_COLUMNS),
        how="left",
        indicator=True,
        validate="many_to_one",
    )
    if universe_membership["_merge"].ne("both").any():
        raise Phase2ReadinessError("Sentinel lineage contains keys outside the universe.")
    lineage_duplicate_columns = [*KEY_COLUMNS, "physical_acquisition_id"]
    if sentinel_lineage.duplicated(lineage_duplicate_columns).any():
        raise Phase2ReadinessError("Sentinel lineage has duplicate acquisition memberships.")
    target_dates = pd.to_datetime(sentinel_lineage["target_date"], errors="raise")
    source_dates = pd.to_datetime(sentinel_lineage["source_end_date"], errors="raise")
    acquisition_dates = pd.to_datetime(
        sentinel_lineage["acquisition_local_date"], errors="raise"
    )
    lag_days = pd.to_numeric(sentinel_lineage["lag_days"], errors="raise")
    source_age = pd.to_numeric(
        sentinel_lineage["source_age_days_audit_only"], errors="raise"
    )
    calculated_lag = (target_dates - source_dates).dt.days
    if not source_dates.equals(acquisition_dates):
        raise Phase2ReadinessError("Sentinel source and acquisition civil dates disagree.")
    if (source_dates >= target_dates).any():
        raise Phase2ReadinessError("Sentinel lineage contains target-day or future sources.")
    if not lag_days.equals(calculated_lag) or not source_age.equals(calculated_lag):
        raise Phase2ReadinessError("Sentinel lineage lag-day fields disagree with dates.")
    if not lag_days.between(1, 60, inclusive="both").all():
        raise Phase2ReadinessError("Sentinel lineage must remain inside d-60 through d-1.")
    included = sentinel_lineage["included_in_composite"]
    if included.isna().any() or not included.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise Phase2ReadinessError("Sentinel lineage inclusion flags must be booleans.")
    lineage_included = (
        sentinel_lineage.assign(
            target_date=target_dates,
            included_in_composite=included.astype(int),
        )
        .groupby(list(KEY_COLUMNS), observed=True, as_index=False)[
            "included_in_composite"
        ]
        .sum()
    )
    audit_counts = audit_keyed.loc[
        :, [*KEY_COLUMNS, "qualifying_acquisition_count"]
    ].copy()
    count_comparison = audit_counts.merge(
        lineage_included,
        on=list(KEY_COLUMNS),
        validate="one_to_one",
    )
    if len(count_comparison) != len(audit_counts) or not pd.to_numeric(
        count_comparison["qualifying_acquisition_count"], errors="raise"
    ).equals(count_comparison["included_in_composite"]):
        raise Phase2ReadinessError(
            "Sentinel audit qualifying counts disagree with lineage inclusion."
        )

    lineage_denominator = sentinel_lineage.loc[
        :,
        [
            "tract_geoid",
            "eligible_pixel_count_static",
            "eligible_pixel_identity_sha256_audit_only",
        ],
    ].copy()
    denominator_counts = lineage_denominator.groupby("tract_geoid", observed=True).agg(
        count_nunique=("eligible_pixel_count_static", "nunique"),
        identity_nunique=("eligible_pixel_identity_sha256_audit_only", "nunique"),
    )
    if denominator_counts.ne(1).any(axis=None):
        raise Phase2ReadinessError(
            "Sentinel lineage changes a tract's static eligible-land denominator."
        )
    expected_denominator = static_denominator.rename(
        columns={
            "eligible_pixel_identity_sha256": (
                "eligible_pixel_identity_sha256_audit_only"
            )
        }
    )
    observed_denominator = lineage_denominator.drop_duplicates("tract_geoid")
    denominator_join = expected_denominator.merge(
        observed_denominator,
        on="tract_geoid",
        suffixes=("_static", "_sentinel"),
        validate="one_to_one",
    )
    if len(denominator_join) != len(expected_denominator):
        raise Phase2ReadinessError("Sentinel lineage omits a static tract denominator.")
    if not denominator_join["eligible_pixel_count_static_static"].equals(
        denominator_join["eligible_pixel_count_static_sentinel"]
    ):
        raise Phase2ReadinessError("Sentinel pixel counts disagree with static support.")
    if not denominator_join[
        "eligible_pixel_identity_sha256_audit_only_static"
    ].equals(
        denominator_join["eligible_pixel_identity_sha256_audit_only_sentinel"]
    ):
        raise Phase2ReadinessError("Sentinel pixel identities disagree with static support.")

    available_count = int(available.sum())
    unavailable_count = int((~available).sum())
    return [
        {
            "family": "key_universe",
            "status": "complete",
            "row_count": len(universe),
            "feature_count": 0,
            "available_row_count": len(universe),
            "missing_row_count": 0,
            "notes": f"{date_count} dates x {tract_count} tracts; target-blind",
        },
        {
            "family": "registry",
            "status": "complete",
            "row_count": len(registry),
            "feature_count": EXPECTED_MODEL_ROWS,
            "available_row_count": len(registry),
            "missing_row_count": 0,
            "notes": "metadata contract only; no scores or labels read",
        },
        {
            "family": "static",
            "status": "complete",
            "row_count": len(static_features),
            "feature_count": len(declared_static_model),
            "available_row_count": len(static_features),
            "missing_row_count": 0,
            "notes": (
                "18 model features plus 1 audit-only reference; fixed eligible-land "
                "denominator verified"
            ),
        },
        {
            "family": "calendar",
            "status": "complete",
            "row_count": len(calendar_features),
            "feature_count": len(CALENDAR_COLUMNS),
            "available_row_count": len(calendar_features),
            "missing_row_count": 0,
            "notes": "known at prediction origin",
        },
        {
            "family": "sentinel",
            "status": "complete",
            "row_count": len(sentinel_features),
            "feature_count": len(INDEX_COLUMNS),
            "available_row_count": available_count,
            "missing_row_count": unavailable_count,
            "notes": "d-60 through d-1; unavailable rows are explicit all-null rows",
        },
    ]


def _validate_upstream_record(
    payload: dict[str, Any],
    *,
    section: str,
    filename: str,
    file_sha256: str,
    rows: int,
    label: str,
) -> None:
    try:
        record = payload[section][filename]
    except (KeyError, TypeError) as exc:
        raise Phase2ReadinessError(f"{label} provenance lacks {filename}.") from exc
    if record.get("sha256") != file_sha256 or int(record.get("rows", -1)) != rows:
        raise Phase2ReadinessError(f"{label} disagrees with its recorded output hash/rows.")


def _daymet_window_audit_column(field: str, window_days: int) -> str:
    return f"daymet_{field}_prev_{window_days}d"


def _daymet_civil_dates(values: pd.Series, *, label: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, errors="raise")
    except (TypeError, ValueError) as exc:
        raise Phase2ReadinessError(f"{label} contains invalid dates.") from exc
    if parsed.isna().any():
        raise Phase2ReadinessError(f"{label} contains missing dates.")
    if parsed.dt.tz is not None or not parsed.dt.normalize().equals(parsed):
        raise Phase2ReadinessError(
            f"{label} must contain timezone-naive civil midnights."
        )
    return parsed


def _daymet_integer_audit(
    values: pd.Series,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> pd.Series:
    if values.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise Phase2ReadinessError(f"{label} must contain integers, not booleans.")
    try:
        numeric = pd.to_numeric(values, errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise Phase2ReadinessError(f"{label} must contain integers.") from exc
    if (
        not np.isfinite(numeric).all()
        or not np.equal(numeric, np.floor(numeric)).all()
        or (numeric < minimum).any()
        or (numeric > maximum).any()
    ):
        raise Phase2ReadinessError(
            f"{label} must contain integers within [{minimum}, {maximum}]."
        )
    return pd.Series(numeric.astype(np.int64), index=values.index)


def _audit_daymet_state(
    *,
    inventory_path: Path,
    summary_path: Path,
    subset_manifest_path: Path,
    feature_path: Path,
    provenance_path: Path,
    universe: pd.DataFrame,
    registry: pd.DataFrame,
    static_audit: pd.DataFrame,
    final_test_year: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory, inventory_sha256 = _read_csv(
        inventory_path,
        dtype={"variable": "string"},
    )
    summary, summary_sha256 = _load_json(summary_path, require_commit=False)
    required = {"variable", "year"}
    if not required.issubset(inventory.columns):
        raise Phase2ReadinessError("Daymet inventory lacks variable/year columns.")
    inventory["year"] = pd.to_numeric(inventory["year"], errors="raise").astype(int)
    if inventory.duplicated(["year", "variable"]).any():
        raise Phase2ReadinessError("Daymet inventory has duplicate variable-year granules.")
    expected_years = set(range(2020, final_test_year))
    expected_variables = {"tmax", "tmin", "prcp", "srad", "vp", "dayl"}
    expected_pairs = {
        (year, variable) for year in expected_years for variable in expected_variables
    }
    observed_pairs = set(zip(inventory["year"], inventory["variable"], strict=True))
    if observed_pairs != expected_pairs:
        raise Phase2ReadinessError("Daymet inventory is not the frozen 2020-2024 x 6 set.")
    if summary.get("contains_final_test_year") is not False:
        raise PermissionError("Daymet inventory summary does not prove the 2025 lock.")
    if summary.get("inventory_file_sha256") != inventory_sha256:
        raise Phase2ReadinessError("Daymet inventory hash disagrees with its summary.")
    if int(summary.get("granule_count", -1)) != len(inventory):
        raise Phase2ReadinessError("Daymet granule count disagrees with its summary.")
    semantic = canonical_frame_sha256(inventory, sort_by=["year", "variable"])
    if summary.get("inventory_semantic_sha256") != semantic:
        raise Phase2ReadinessError("Daymet inventory semantic hash disagrees with summary.")

    input_records: dict[str, Any] = {
        "daymet_inventory": {
            "path": str(inventory_path.resolve()),
            "sha256": inventory_sha256,
            "rows": len(inventory),
            "semantic_sha256": semantic,
        },
        "daymet_inventory_summary": {
            "path": str(summary_path.resolve()),
            "sha256": summary_sha256,
            "state": summary.get("state"),
        },
    }
    weather_names = registry.loc[
        registry["family"].eq("weather") & registry["role"].eq("model"),
        "feature_name",
    ].tolist()
    if not feature_path.is_file():
        subset_state = "missing"
        if subset_manifest_path.is_file():
            subsets, subset_sha256 = _read_csv(subset_manifest_path)
            input_records["daymet_subset_manifest"] = {
                "path": str(subset_manifest_path.resolve()),
                "sha256": subset_sha256,
                "rows": len(subsets),
            }
            subset_state = "present_uncompiled"
        status = (
            "blocked_missing_authenticated_subsets"
            if subset_state == "missing"
            else "blocked_missing_daymet_feature_build"
        )
        return (
            {
                "family": "daymet",
                "status": status,
                "row_count": 0,
                "feature_count": len(weather_names),
                "available_row_count": 0,
                "missing_row_count": len(universe),
                "notes": "official inventory complete; no feature values were fabricated",
            },
            input_records,
        )

    daymet, daymet_sha256 = _read_parquet(feature_path)
    provenance, provenance_sha256 = _load_json(provenance_path, require_commit=True)
    audit_path = feature_path.with_name("daymet_feature_audit.parquet")
    weights_path = feature_path.with_name("daymet_fixed_cell_weights.parquet")
    daymet_audit, daymet_audit_sha256 = _read_parquet(audit_path)
    daymet_weights, daymet_weights_sha256 = _read_parquet(weights_path)
    _require_exact_columns(
        daymet,
        expected={*KEY_COLUMNS, *weather_names},
        label="Daymet feature table",
    )
    _assert_exact_key_coverage(
        daymet,
        universe,
        label="Daymet feature table",
        final_test_year=final_test_year,
    )
    values = _require_numeric(
        daymet,
        weather_names,
        label="Daymet",
        allow_missing=True,
    )
    _validate_upstream_record(
        provenance,
        section="output_files",
        filename=feature_path.name,
        file_sha256=daymet_sha256,
        rows=len(daymet),
        label="Daymet",
    )
    _validate_upstream_record(
        provenance,
        section="output_files",
        filename=audit_path.name,
        file_sha256=daymet_audit_sha256,
        rows=len(daymet_audit),
        label="Daymet audit",
    )
    _validate_upstream_record(
        provenance,
        section="output_files",
        filename=weights_path.name,
        file_sha256=daymet_weights_sha256,
        rows=len(daymet_weights),
        label="Daymet weights",
    )
    if provenance.get("target_blind") is not True:
        raise Phase2ReadinessError("Daymet provenance must declare target_blind=true.")
    if provenance.get("target_or_qa_tables_read") not in ([], None):
        raise Phase2ReadinessError("Daymet stage may not read target or QA tables.")
    if provenance.get("source_end_offset_days") != -1:
        raise Phase2ReadinessError("Daymet features must end at target day -1.")
    if provenance.get("window_days") != list(DAYMET_PRIMARY_WINDOWS):
        raise Phase2ReadinessError("Daymet windows must remain exactly 1, 3, and 7 days.")
    if (
        provenance.get("static_eligible_land_denominator_invariant") is not True
        or provenance.get("date_specific_weight_renormalization") is not False
        or provenance.get("srad_energy_computed_cell_first") is not True
    ):
        raise Phase2ReadinessError("Daymet fixed-support scientific contract changed.")
    required_audit = {
        *KEY_COLUMNS,
        "daymet_source_start_date",
        "daymet_source_end_date",
        "daymet_source_days_expected",
        "daymet_source_days_complete",
        "daymet_grid_cells_expected",
        "daymet_grid_cells_present_min",
        "daymet_static_eligible_area_m2",
        "daymet_all_primary_windows_complete",
        *{
            _daymet_window_audit_column(field, window_days)
            for window_days in DAYMET_PRIMARY_WINDOWS
            for field in DAYMET_WINDOW_AUDIT_FIELDS
        },
    }
    if not required_audit.issubset(daymet_audit.columns):
        raise Phase2ReadinessError("Daymet audit lacks fixed-support lineage columns.")
    _assert_exact_key_coverage(
        daymet_audit,
        universe,
        label="Daymet audit table",
        final_test_year=final_test_year,
    )
    availability = daymet_audit["daymet_all_primary_windows_complete"]
    if availability.isna().any() or not availability.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise Phase2ReadinessError("Daymet availability audit must contain booleans.")
    feature_keyed = daymet.copy()
    feature_keyed["tract_geoid"] = feature_keyed["tract_geoid"].astype("string")
    feature_keyed["target_date"] = _daymet_civil_dates(
        feature_keyed["target_date"], label="Daymet feature target_date"
    )
    audit_keyed = daymet_audit.copy()
    audit_keyed["tract_geoid"] = audit_keyed["tract_geoid"].astype("string")
    audit_keyed["target_date"] = _daymet_civil_dates(
        audit_keyed["target_date"], label="Daymet audit target_date"
    )
    aligned = audit_keyed.loc[:, sorted(required_audit)].merge(
        feature_keyed,
        on=list(KEY_COLUMNS),
        how="inner",
        validate="one_to_one",
    )
    audit_target = aligned["target_date"]
    window_complete: list[np.ndarray] = []
    window_complete_days: list[np.ndarray] = []
    for window_days in DAYMET_PRIMARY_WINDOWS:
        suffix = f"prev_{window_days}d"
        start_column = _daymet_window_audit_column(
            "source_start_date", window_days
        )
        end_column = _daymet_window_audit_column("source_end_date", window_days)
        expected_column = _daymet_window_audit_column(
            "source_days_expected", window_days
        )
        complete_column = _daymet_window_audit_column(
            "source_days_complete", window_days
        )
        source_start = _daymet_civil_dates(
            aligned[start_column], label=f"Daymet {window_days}-day source start"
        )
        source_end = _daymet_civil_dates(
            aligned[end_column], label=f"Daymet {window_days}-day source end"
        )
        if not source_start.equals(audit_target - pd.Timedelta(days=window_days)):
            raise Phase2ReadinessError(
                f"Daymet {window_days}-day audit does not start exactly at d-{window_days}."
            )
        if not source_end.equals(audit_target - pd.Timedelta(days=1)):
            raise Phase2ReadinessError(
                f"Daymet {window_days}-day audit does not end exactly at d-1."
            )
        expected_days = _daymet_integer_audit(
            aligned[expected_column],
            label=f"Daymet {window_days}-day expected-day audit",
            minimum=window_days,
            maximum=window_days,
        )
        complete_days = _daymet_integer_audit(
            aligned[complete_column],
            label=f"Daymet {window_days}-day complete-day audit",
            minimum=0,
            maximum=window_days,
        )
        if not expected_days.eq(window_days).all():
            raise AssertionError("Exact Daymet expected-day bounds were not enforced.")
        complete = complete_days.eq(window_days).to_numpy(dtype=bool)
        window_complete_days.append(complete_days.to_numpy(dtype=np.int64))
        window_features = [name for name in weather_names if name.endswith(suffix)]
        if len(window_features) != 7:
            raise Phase2ReadinessError(
                f"Daymet {window_days}-day registry must contain exactly 7 features."
            )
        finite = np.isfinite(
            aligned.loc[:, window_features].to_numpy(dtype=float, na_value=np.nan)
        ).all(axis=1)
        if not np.array_equal(finite, complete):
            raise Phase2ReadinessError(
                f"Daymet {window_days}-day feature finiteness disagrees with "
                "its complete-day audit."
            )
        window_complete.append(complete)

    for index in range(1, len(DAYMET_PRIMARY_WINDOWS)):
        added_days = DAYMET_PRIMARY_WINDOWS[index] - DAYMET_PRIMARY_WINDOWS[index - 1]
        count_change = window_complete_days[index] - window_complete_days[index - 1]
        if (count_change < 0).any() or (count_change > added_days).any():
            raise Phase2ReadinessError(
                "Daymet complete-day counts are inconsistent across nested 1/3/7-day "
                "windows."
            )

    maximum_window = max(DAYMET_PRIMARY_WINDOWS)
    maximum_suffix = f"prev_{maximum_window}d"
    legacy_aliases = {
        "daymet_source_start_date": f"daymet_source_start_date_{maximum_suffix}",
        "daymet_source_end_date": f"daymet_source_end_date_{maximum_suffix}",
        "daymet_source_days_expected": (
            f"daymet_source_days_expected_{maximum_suffix}"
        ),
        "daymet_source_days_complete": (
            f"daymet_source_days_complete_{maximum_suffix}"
        ),
    }
    for alias, canonical in legacy_aliases.items():
        if alias.endswith("_date"):
            alias_values = _daymet_civil_dates(
                aligned[alias], label=f"Daymet legacy audit field {alias}"
            )
            canonical_values = _daymet_civil_dates(
                aligned[canonical], label=f"Daymet audit field {canonical}"
            )
        else:
            alias_values = _daymet_integer_audit(
                aligned[alias],
                label=f"Daymet legacy audit field {alias}",
                minimum=0,
                maximum=maximum_window,
            )
            canonical_values = _daymet_integer_audit(
                aligned[canonical],
                label=f"Daymet audit field {canonical}",
                minimum=0,
                maximum=maximum_window,
            )
        if not alias_values.equals(canonical_values):
            raise Phase2ReadinessError(
                f"Daymet legacy 7-day audit alias {alias} disagrees with {canonical}."
            )

    aligned_availability = aligned[
        "daymet_all_primary_windows_complete"
    ].to_numpy(dtype=bool)
    audited_availability = np.logical_and.reduce(window_complete)
    all_feature_finite = np.isfinite(
        aligned.loc[:, weather_names].to_numpy(dtype=float, na_value=np.nan)
    ).all(axis=1)
    if (
        len(weather_names) != 21
        or not np.array_equal(aligned_availability, audited_availability)
        or not np.array_equal(aligned_availability, all_feature_finite)
    ):
        raise Phase2ReadinessError(
            "Daymet 21-feature finiteness, window completeness, and availability "
            "must agree exactly."
        )
    required_weights = {
        "tract_geoid",
        "daymet_cell_id",
        "eligible_pixel_count",
        "static_denominator_m2",
        "weight",
    }
    if not required_weights.issubset(daymet_weights.columns):
        raise Phase2ReadinessError("Daymet weights lack fixed-denominator columns.")
    if daymet_weights.duplicated(["tract_geoid", "daymet_cell_id"]).any():
        raise Phase2ReadinessError("Daymet weights contain duplicate tract-cell rows.")
    weight_sums = daymet_weights.groupby("tract_geoid", observed=True)["weight"].sum()
    if not np.allclose(weight_sums.to_numpy(dtype=float), 1.0, rtol=0, atol=1e-12):
        raise Phase2ReadinessError("Daymet fixed weights do not sum to one per tract.")
    daymet_counts = (
        daymet_weights.groupby("tract_geoid", observed=True)["eligible_pixel_count"]
        .sum()
        .sort_index()
    )
    static_counts = (
        static_audit.set_index("tract_geoid")["eligible_pixel_count_static"].sort_index()
    )
    daymet_counts.index = daymet_counts.index.astype(str)
    static_counts.index = static_counts.index.astype(str)
    if not daymet_counts.equals(static_counts):
        raise Phase2ReadinessError(
            "Daymet weights disagree with the frozen static eligible-land denominator."
        )
    input_records["daymet_features"] = {
        "path": str(feature_path.resolve()),
        "sha256": daymet_sha256,
        "rows": len(daymet),
    }
    input_records["daymet_features_provenance"] = {
        "path": str(provenance_path.resolve()),
        "sha256": provenance_sha256,
        "commit_sha256": provenance.get("commit_sha256"),
    }
    input_records["daymet_feature_audit"] = {
        "path": str(audit_path.resolve()),
        "sha256": daymet_audit_sha256,
        "rows": len(daymet_audit),
    }
    input_records["daymet_fixed_cell_weights"] = {
        "path": str(weights_path.resolve()),
        "sha256": daymet_weights_sha256,
        "rows": len(daymet_weights),
    }
    complete_rows = np.isfinite(values).all(axis=1)
    return (
        {
            "family": "daymet",
            "status": "complete",
            "row_count": len(daymet),
            "feature_count": len(weather_names),
            "available_row_count": int(complete_rows.sum()),
            "missing_row_count": int((~complete_rows).sum()),
            "notes": (
                "complete civil-day windows d-n through d-1; any missing dynamic "
                "values remain explicit for fold-local imputation"
            ),
        },
        input_records,
    )


def audit_phase2_readiness(
    *,
    key_path: str | Path = DEFAULT_KEY_PATH,
    key_provenance_path: str | Path = DEFAULT_KEY_PROVENANCE_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    registry_provenance_path: str | Path = DEFAULT_REGISTRY_PROVENANCE_PATH,
    static_path: str | Path = DEFAULT_STATIC_PATH,
    static_audit_path: str | Path = DEFAULT_STATIC_AUDIT_PATH,
    static_registry_path: str | Path = DEFAULT_STATIC_REGISTRY_PATH,
    static_provenance_path: str | Path = DEFAULT_STATIC_PROVENANCE_PATH,
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
    calendar_provenance_path: str | Path = DEFAULT_CALENDAR_PROVENANCE_PATH,
    sentinel_directory: str | Path = DEFAULT_SENTINEL_DIRECTORY,
    sentinel_lineage_path: str | Path = DEFAULT_SENTINEL_LINEAGE_PATH,
    daymet_inventory_path: str | Path = DEFAULT_DAYMET_INVENTORY_PATH,
    daymet_summary_path: str | Path = DEFAULT_DAYMET_SUMMARY_PATH,
    daymet_subset_manifest_path: str | Path = DEFAULT_DAYMET_SUBSET_MANIFEST_PATH,
    daymet_feature_path: str | Path = DEFAULT_DAYMET_FEATURE_PATH,
    daymet_provenance_path: str | Path = DEFAULT_DAYMET_PROVENANCE_PATH,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> dict[str, Any]:
    """Run and atomically record the target-blind Phase 2 readiness audit."""

    if unlock_final_test:
        raise PermissionError("Readiness audit refuses to unlock the final test set.")
    key_path = Path(key_path)
    key_provenance_path = Path(key_provenance_path)
    registry_path = Path(registry_path)
    registry_provenance_path = Path(registry_provenance_path)
    static_path = Path(static_path)
    static_audit_path = Path(static_audit_path)
    static_registry_path = Path(static_registry_path)
    static_provenance_path = Path(static_provenance_path)
    calendar_path = Path(calendar_path)
    calendar_provenance_path = Path(calendar_provenance_path)
    sentinel_directory = Path(sentinel_directory)

    universe, universe_sha256 = _read_parquet(key_path)
    key_provenance, key_provenance_sha256 = _load_json(
        key_provenance_path, require_commit=True
    )
    registry, registry_sha256 = _read_csv(registry_path)
    registry_provenance, registry_provenance_sha256 = _load_json(
        registry_provenance_path, require_commit=True
    )
    static, static_sha256 = _read_parquet(static_path)
    static_audit, static_audit_sha256 = _read_parquet(static_audit_path)
    static_registry, static_registry_sha256 = _read_csv(static_registry_path)
    static_provenance, static_provenance_sha256 = _load_json(
        static_provenance_path, require_commit=True
    )
    calendar, calendar_sha256 = _read_parquet(calendar_path)
    calendar_provenance, calendar_provenance_sha256 = _load_json(
        calendar_provenance_path, require_commit=True
    )
    sentinel_features_path = sentinel_directory / "sentinel_features.parquet"
    sentinel_audit_path = sentinel_directory / "sentinel_feature_audit.parquet"
    sentinel_coverage_date_path = sentinel_directory / "coverage_by_date.parquet"
    sentinel_coverage_tract_path = sentinel_directory / "coverage_by_tract.parquet"
    sentinel_provenance_path = sentinel_directory / "sentinel_features_provenance.json"
    sentinel_lineage_path = Path(sentinel_lineage_path)
    sentinel, sentinel_sha256 = _read_parquet(sentinel_features_path)
    sentinel_audit, sentinel_audit_sha256 = _read_parquet(sentinel_audit_path)
    sentinel_lineage, sentinel_lineage_sha256 = _read_parquet(sentinel_lineage_path)
    sentinel_coverage_date, sentinel_coverage_date_sha256 = _read_parquet(
        sentinel_coverage_date_path
    )
    sentinel_coverage_tract, sentinel_coverage_tract_sha256 = _read_parquet(
        sentinel_coverage_tract_path
    )
    sentinel_provenance, sentinel_provenance_sha256 = _load_json(
        sentinel_provenance_path, require_commit=True
    )

    expected_registry = construct_phase2_registry(static_registry)
    pd.testing.assert_frame_equal(registry, expected_registry, check_dtype=True)
    family_rows = validate_ready_feature_families(
        key_universe=universe,
        registry=registry,
        static_features=static,
        static_audit=static_audit,
        calendar_features=calendar,
        sentinel_features=sentinel,
        sentinel_audit=sentinel_audit,
        sentinel_lineage=sentinel_lineage,
        final_test_year=final_test_year,
    )

    _validate_upstream_record(
        key_provenance,
        section="output_files",
        filename=key_path.name,
        file_sha256=universe_sha256,
        rows=len(universe),
        label="key universe",
    )
    _validate_upstream_record(
        registry_provenance,
        section="output_files",
        filename=registry_path.name,
        file_sha256=registry_sha256,
        rows=len(registry),
        label="registry",
    )
    _validate_upstream_record(
        static_provenance,
        section="output_files",
        filename=static_path.name,
        file_sha256=static_sha256,
        rows=len(static),
        label="static feature",
    )
    _validate_upstream_record(
        static_provenance,
        section="output_files",
        filename=static_audit_path.name,
        file_sha256=static_audit_sha256,
        rows=len(static_audit),
        label="static audit",
    )
    _validate_upstream_record(
        calendar_provenance,
        section="output_files",
        filename=calendar_path.name,
        file_sha256=calendar_sha256,
        rows=len(calendar),
        label="calendar feature",
    )
    for path, file_sha256, rows in (
        (sentinel_features_path, sentinel_sha256, len(sentinel)),
        (sentinel_audit_path, sentinel_audit_sha256, len(sentinel_audit)),
        (
            sentinel_coverage_date_path,
            sentinel_coverage_date_sha256,
            len(sentinel_coverage_date),
        ),
        (
            sentinel_coverage_tract_path,
            sentinel_coverage_tract_sha256,
            len(sentinel_coverage_tract),
        ),
    ):
        _validate_upstream_record(
            sentinel_provenance,
            section="output_files",
            filename=path.name,
            file_sha256=file_sha256,
            rows=rows,
            label="promoted Sentinel",
        )
    try:
        lineage_record = sentinel_provenance["inputs"]["source_outputs"][
            sentinel_lineage_path.name
        ]
    except (KeyError, TypeError) as exc:
        raise Phase2ReadinessError(
            "Promoted Sentinel provenance lacks its source lineage record."
        ) from exc
    if lineage_record.get("sha256") != sentinel_lineage_sha256 or int(
        lineage_record.get("rows", -1)
    ) != len(sentinel_lineage):
        raise Phase2ReadinessError(
            "Promoted Sentinel provenance disagrees with source lineage hash/rows."
        )
    if sentinel_provenance.get("state") != "complete" or sentinel_provenance.get(
        "promoted_outputs_valid"
    ) is not True:
        raise Phase2ReadinessError("Sentinel promotion is not complete and valid.")
    if sentinel_provenance.get("target_blind") is not True or sentinel_provenance.get(
        "target_or_qa_tables_read"
    ) != []:
        raise Phase2ReadinessError("Sentinel promotion is not target-blind.")
    if sentinel_provenance.get("scientific_processor_sha256") != (
        "68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c"
    ):
        raise Phase2ReadinessError("Sentinel scientific processor hash changed.")
    if len(sentinel_coverage_date) != int(universe["target_date"].nunique()):
        raise Phase2ReadinessError("Sentinel date-coverage summary is incomplete.")
    if len(sentinel_coverage_tract) != int(universe["tract_geoid"].nunique()):
        raise Phase2ReadinessError("Sentinel tract-coverage summary is incomplete.")

    daymet_row, daymet_records = _audit_daymet_state(
        inventory_path=Path(daymet_inventory_path),
        summary_path=Path(daymet_summary_path),
        subset_manifest_path=Path(daymet_subset_manifest_path),
        feature_path=Path(daymet_feature_path),
        provenance_path=Path(daymet_provenance_path),
        universe=universe,
        registry=registry,
        static_audit=static_audit,
        final_test_year=final_test_year,
    )
    family_rows.append(daymet_row)
    families = pd.DataFrame(family_rows)
    blocked = families["status"].str.startswith("blocked_")
    blockers = families.loc[blocked, "status"].tolist()
    overall_status = (
        "blocked_missing_daymet_values" if blockers else "ready_for_feature_assembly"
    )

    inputs: dict[str, Any] = {
        "feature_key_universe": {
            "path": str(key_path.resolve()),
            "sha256": universe_sha256,
            "rows": len(universe),
            "provenance_sha256": key_provenance_sha256,
            "provenance_commit_sha256": key_provenance["commit_sha256"],
        },
        "phase2_registry": {
            "path": str(registry_path.resolve()),
            "sha256": registry_sha256,
            "rows": len(registry),
            "provenance_sha256": registry_provenance_sha256,
            "provenance_commit_sha256": registry_provenance["commit_sha256"],
        },
        "static_features": {
            "path": str(static_path.resolve()),
            "sha256": static_sha256,
            "rows": len(static),
            "audit_sha256": static_audit_sha256,
            "registry_sha256": static_registry_sha256,
            "provenance_sha256": static_provenance_sha256,
            "provenance_commit_sha256": static_provenance["commit_sha256"],
        },
        "calendar_features": {
            "path": str(calendar_path.resolve()),
            "sha256": calendar_sha256,
            "rows": len(calendar),
            "provenance_sha256": calendar_provenance_sha256,
            "provenance_commit_sha256": calendar_provenance["commit_sha256"],
        },
        "sentinel_features": {
            "path": str(sentinel_features_path.resolve()),
            "sha256": sentinel_sha256,
            "rows": len(sentinel),
            "audit_sha256": sentinel_audit_sha256,
            "lineage_sha256": sentinel_lineage_sha256,
            "coverage_by_date_sha256": sentinel_coverage_date_sha256,
            "coverage_by_tract_sha256": sentinel_coverage_tract_sha256,
            "provenance_sha256": sentinel_provenance_sha256,
            "provenance_commit_sha256": sentinel_provenance["commit_sha256"],
            "scientific_pipeline_sha256": sentinel_provenance.get(
                "scientific_processor_sha256"
            ),
        },
        **daymet_records,
    }

    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/audit_phase2_readiness.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/feature_universe.py",
            "src/la_heat/phase2_readiness.py",
            "src/la_heat/phase2_registry.py",
            "src/la_heat/provenance.py",
            "src/la_heat/sentinel_feature_stage.py",
            "src/la_heat/sentinel_features.py",
            "src/la_heat/daymet_feature_stage.py",
        ),
        algorithm_version=READINESS_ALGORITHM_VERSION,
    )
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    marker_path = output / READINESS_FILENAME
    summary_path = output / FAMILY_SUMMARY_FILENAME
    marker_path.unlink(missing_ok=True)
    atomic_csv(families, summary_path)
    payload: dict[str, Any] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "algorithm_version": READINESS_ALGORITHM_VERSION,
        "state": overall_status,
        "audit_completed": True,
        "phase2_complete": False,
        "ready_for_feature_assembly": not blockers,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "model_scores_read": False,
        "final_test_year": final_test_year,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "blockers": blockers,
        "key_count": len(universe),
        "date_count": int(pd.to_datetime(universe["target_date"]).nunique()),
        "tract_count": int(universe["tract_geoid"].nunique()),
        "registry_model_feature_count": EXPECTED_MODEL_ROWS,
        "family_status": family_rows,
        "inputs": inputs,
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "output_files": {
            FAMILY_SUMMARY_FILENAME: {
                "path": str(summary_path.resolve()),
                "sha256": sha256_file(summary_path),
                "rows": len(families),
            }
        },
        "scientific_contract": {
            "prediction_type": "historical hindcast",
            "prediction_origin": "00:00 Los Angeles civil time on target date",
            "dynamic_observed_predictors_end_by": "target day -1",
            "sentinel_window": "d-60 through d-1",
            "daymet_windows": "d-n through d-1",
            "locked_final_test_used": False,
            "promotion_note": (
                "This readiness audit cannot itself promote Phase 2 or authorize "
                "target access; a separate feature-table build and audit are required."
            ),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    return payload
