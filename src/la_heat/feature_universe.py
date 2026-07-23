"""Build the target-blind tract-date key universe for Phase 2 predictors.

The universe comes only from frozen acquisition eligibility metadata and the fixed
primary tract manifest.  It deliberately does not inspect Landsat LST, target QA,
target availability, or any target-derived table.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

FEATURE_UNIVERSE_SCHEMA_VERSION = 1
FEATURE_UNIVERSE_ALGORITHM_VERSION = "feature-key-universe-target-blind-v1"
FEATURE_UNIVERSE_STATUS = "target_blind_draft"
FEATURE_UNIVERSE_FILENAME = "feature_key_universe.parquet"
FEATURE_UNIVERSE_PROVENANCE_FILENAME = "feature_key_universe_provenance.json"
DEFAULT_OUTPUT_DIRECTORY = Path("data/interim/features/feature_key_universe")

OVERPASS_INPUT_COLUMNS = (
    "overpass_id",
    "local_date",
    "primary_eligible",
    "source_lock_sha256",
)
TRACT_INPUT_COLUMNS = (
    "GEOID",
    "primary_included",
    "tract_manifest_sha256",
)
KEY_COLUMNS = ("tract_geoid", "target_date")

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GEOID_PATTERN = re.compile(r"^06037[0-9]{6}$")
_TARGET_LIKE_TOKENS = (
    "lst",
    "temperature",
    "target_",
    "target-",
    "hotspot",
    "anomaly",
    "valid_pixel",
    "date_usable",
    "target_available",
)


class FeatureUniverseError(ValueError):
    """Raised when a target-blind feature-universe invariant cannot be proven."""


def _require_positive_count(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if value < 1:
        raise ValueError(f"{name} must be positive.")


def _read_stable_overpass_manifest(path: Path) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Primary overpass manifest does not exist: {path}")
    before = sha256_file(path)
    try:
        frame = pd.read_csv(
            path,
            usecols=list(OVERPASS_INPUT_COLUMNS),
            dtype={
                "overpass_id": "string",
                "local_date": "string",
                "source_lock_sha256": "string",
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FeatureUniverseError(
            "Primary overpass manifest lacks a required target-blind metadata column."
        ) from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError("Primary overpass manifest changed while it was being read.")
    return frame, before


def _read_stable_tract_manifest(path: Path) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Primary tract manifest does not exist: {path}")
    before = sha256_file(path)
    try:
        frame = pd.read_parquet(path, columns=list(TRACT_INPUT_COLUMNS))
    except (KeyError, TypeError, ValueError) as exc:
        raise FeatureUniverseError(
            "Primary tract manifest lacks a required target-blind metadata column."
        ) from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError("Primary tract manifest changed while it was being read.")
    return frame, before


def _require_boolean_column(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column]
    valid = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    if not valid.all():
        examples = values.loc[~valid].head(5).tolist()
        raise FeatureUniverseError(
            f"{column} must contain only non-null booleans; examples: {examples}"
        )
    return values.astype(bool)


def _require_sha256_column(
    frame: pd.DataFrame,
    column: str,
    *,
    require_single_value: bool,
) -> str | None:
    values = frame[column]
    valid = values.map(
        lambda value: isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None
    )
    if not valid.all():
        examples = values.loc[~valid].head(5).tolist()
        raise FeatureUniverseError(
            f"{column} must contain canonical lowercase SHA-256 values; examples: {examples}"
        )
    unique = sorted(values.unique().tolist())
    if require_single_value and len(unique) != 1:
        raise FeatureUniverseError(
            f"{column} is inconsistent across the frozen tract manifest."
        )
    return unique[0] if require_single_value else None


def _parse_civil_midnights(values: pd.Series, *, column: str) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for value in values.tolist():
        if value is None or value is pd.NaT or (
            not isinstance(value, (list, tuple, dict)) and pd.isna(value)
        ):
            raise FeatureUniverseError(f"{column} contains a missing date.")
        if isinstance(value, (bool, int, float, np.number)):
            raise FeatureUniverseError(f"{column} contains a non-calendar value: {value!r}")
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise FeatureUniverseError(
                f"{column} contains an invalid calendar date: {value!r}"
            ) from exc
        if pd.isna(timestamp):
            raise FeatureUniverseError(f"{column} contains a missing date.")
        if timestamp.tzinfo is not None:
            raise FeatureUniverseError(f"{column} must be timezone-naive civil dates.")
        if timestamp != timestamp.normalize():
            raise FeatureUniverseError(f"{column} must contain civil midnights only.")
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _validate_overpasses(
    frame: pd.DataFrame,
    *,
    final_test_year: int,
    expected_date_count: int,
) -> tuple[list[pd.Timestamp], pd.DataFrame]:
    if frame.empty:
        raise FeatureUniverseError("Primary overpass manifest is empty.")
    eligible = _require_boolean_column(frame, "primary_eligible")
    _require_sha256_column(frame, "source_lock_sha256", require_single_value=False)

    overpass_ids = frame["overpass_id"]
    valid_ids = overpass_ids.map(
        lambda value: isinstance(value, str) and bool(value) and value == value.strip()
    )
    if not valid_ids.all():
        raise FeatureUniverseError("overpass_id values must be non-empty canonical strings.")
    if overpass_ids.duplicated().any():
        raise FeatureUniverseError("Primary overpass manifest contains duplicate overpass IDs.")
    if frame["source_lock_sha256"].duplicated().any():
        raise FeatureUniverseError(
            "Distinct overpasses must not share a source_lock_sha256 value."
        )

    parsed_dates = _parse_civil_midnights(frame["local_date"], column="local_date")
    if parsed_dates.duplicated().any():
        raise FeatureUniverseError("Primary overpass manifest contains duplicate local dates.")
    if (parsed_dates.dt.year >= final_test_year).any():
        raise PermissionError(
            f"Primary overpass manifest contains locked dates from {final_test_year} or later."
        )

    eligible_dates = sorted(parsed_dates.loc[eligible].tolist())
    if len(eligible_dates) != expected_date_count:
        raise FeatureUniverseError(
            "Eligible target-date count does not match the frozen expectation: "
            f"expected {expected_date_count}, found {len(eligible_dates)}."
        )

    metadata = frame.loc[:, OVERPASS_INPUT_COLUMNS].copy()
    metadata["local_date"] = parsed_dates
    return eligible_dates, metadata


def _validate_tracts(
    frame: pd.DataFrame,
    *,
    expected_tract_count: int,
) -> tuple[list[str], str, pd.DataFrame]:
    if frame.empty:
        raise FeatureUniverseError("Primary tract manifest is empty.")
    included = _require_boolean_column(frame, "primary_included")
    tract_manifest_sha256 = _require_sha256_column(
        frame,
        "tract_manifest_sha256",
        require_single_value=True,
    )
    assert tract_manifest_sha256 is not None

    geoids = frame["GEOID"]
    valid_geoids = geoids.map(
        lambda value: isinstance(value, str) and _GEOID_PATTERN.fullmatch(value) is not None
    )
    if not valid_geoids.all():
        examples = geoids.loc[~valid_geoids].head(5).tolist()
        raise FeatureUniverseError(
            "GEOID values must be canonical 11-digit Los Angeles County tract IDs; "
            f"examples: {examples}"
        )
    if geoids.duplicated().any():
        raise FeatureUniverseError("Primary tract manifest contains duplicate GEOIDs.")

    primary_geoids = sorted(geoids.loc[included].tolist())
    if len(primary_geoids) != expected_tract_count:
        raise FeatureUniverseError(
            "Included tract count does not match the frozen expectation: "
            f"expected {expected_tract_count}, found {len(primary_geoids)}."
        )

    metadata = frame.loc[:, TRACT_INPUT_COLUMNS].copy()
    return primary_geoids, tract_manifest_sha256, metadata


def validate_feature_key_universe(
    frame: pd.DataFrame,
    *,
    final_test_year: int = 2025,
) -> None:
    """Fail closed unless ``frame`` is a pure, unique, unlocked key table."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Feature key universe must be a pandas DataFrame.")
    columns = list(frame.columns)
    unexpected = [column for column in columns if column not in KEY_COLUMNS]
    target_like = [
        column
        for column in unexpected
        if any(token in str(column).lower() for token in _TARGET_LIKE_TOKENS)
    ]
    if target_like:
        raise FeatureUniverseError(
            f"Target-like columns are forbidden from the feature key universe: {target_like}"
        )
    if columns != list(KEY_COLUMNS):
        raise FeatureUniverseError(
            "Feature key universe must contain exactly tract_geoid,target_date in that order."
        )
    if frame.empty:
        raise FeatureUniverseError("Feature key universe is empty.")
    if frame.duplicated(list(KEY_COLUMNS)).any():
        raise FeatureUniverseError("Feature key universe contains duplicate tract-date keys.")

    parsed_dates = _parse_civil_midnights(frame["target_date"], column="target_date")
    if (parsed_dates.dt.year >= final_test_year).any():
        raise PermissionError(
            f"Feature key universe contains locked dates from {final_test_year} or later."
        )
    valid_geoids = frame["tract_geoid"].map(
        lambda value: isinstance(value, str) and _GEOID_PATTERN.fullmatch(value) is not None
    )
    if not valid_geoids.all():
        raise FeatureUniverseError("Feature key universe contains an invalid tract GEOID.")


def construct_feature_key_universe(
    overpass_manifest: pd.DataFrame,
    tract_manifest: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    expected_date_count: int = 90,
    expected_tract_count: int = 1096,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Construct a deterministic Cartesian key grid from target-blind metadata."""

    if isinstance(final_test_year, bool) or not isinstance(final_test_year, int):
        raise TypeError("final_test_year must be an integer calendar year.")
    if final_test_year < 1:
        raise ValueError("final_test_year must be positive.")
    _require_positive_count(expected_date_count, name="expected_date_count")
    _require_positive_count(expected_tract_count, name="expected_tract_count")

    missing_overpass = sorted(set(OVERPASS_INPUT_COLUMNS) - set(overpass_manifest.columns))
    if missing_overpass:
        raise FeatureUniverseError(
            f"Primary overpass manifest is missing columns: {missing_overpass}"
        )
    missing_tract = sorted(set(TRACT_INPUT_COLUMNS) - set(tract_manifest.columns))
    if missing_tract:
        raise FeatureUniverseError(
            f"Primary tract manifest is missing columns: {missing_tract}"
        )

    dates, overpass_metadata = _validate_overpasses(
        overpass_manifest.loc[:, OVERPASS_INPUT_COLUMNS].copy(),
        final_test_year=final_test_year,
        expected_date_count=expected_date_count,
    )
    geoids, tract_manifest_sha256, tract_metadata = _validate_tracts(
        tract_manifest.loc[:, TRACT_INPUT_COLUMNS].copy(),
        expected_tract_count=expected_tract_count,
    )

    product = pd.MultiIndex.from_product(
        [dates, geoids],
        names=["target_date", "tract_geoid"],
    ).to_frame(index=False)
    universe = product.loc[:, ["tract_geoid", "target_date"]].copy()
    universe["tract_geoid"] = universe["tract_geoid"].astype("string")
    universe["target_date"] = pd.to_datetime(universe["target_date"])
    validate_feature_key_universe(universe, final_test_year=final_test_year)

    expected_rows = expected_date_count * expected_tract_count
    if len(universe) != expected_rows:
        raise FeatureUniverseError(
            f"Cartesian key count mismatch: expected {expected_rows}, found {len(universe)}."
        )
    if universe.groupby("target_date", observed=True)["tract_geoid"].nunique().ne(
        expected_tract_count
    ).any():
        raise FeatureUniverseError("At least one target date is missing a primary tract.")
    if universe.groupby("tract_geoid", observed=True)["target_date"].nunique().ne(
        expected_date_count
    ).any():
        raise FeatureUniverseError("At least one primary tract is missing a target date.")

    audit = {
        "eligible_date_count": len(dates),
        "primary_tract_count": len(geoids),
        "key_count": len(universe),
        "years": sorted({date.year for date in dates}),
        "tract_manifest_sha256": tract_manifest_sha256,
        "source_lock_set_sha256": canonical_sha256(
            sorted(overpass_metadata.loc[:, "source_lock_sha256"].tolist())
        ),
        "overpass_metadata_semantic_sha256": canonical_frame_sha256(
            overpass_metadata,
            sort_by=["local_date", "overpass_id"],
            columns=list(OVERPASS_INPUT_COLUMNS),
        ),
        "tract_metadata_semantic_sha256": canonical_frame_sha256(
            tract_metadata,
            sort_by=["GEOID"],
            columns=list(TRACT_INPUT_COLUMNS),
        ),
        "semantic_key_sha256": canonical_frame_sha256(
            universe,
            sort_by=["target_date", "tract_geoid"],
            columns=list(KEY_COLUMNS),
        ),
    }
    return universe, audit


def build_feature_key_universe(
    overpass_manifest_path: str | Path = (
        "manifests/target_inventory/primary_overpass_manifest.csv"
    ),
    tract_manifest_path: str | Path = (
        "data/interim/targets/primary_tract_manifest.parquet"
    ),
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    *,
    final_test_year: int = 2025,
    expected_date_count: int = 90,
    expected_tract_count: int = 1096,
) -> dict[str, Any]:
    """Build and commit the target-blind feature key universe.

    The provenance file is the commit marker.  Any previous marker is removed only
    after all inputs and the in-memory output have passed validation, and the new
    marker is written after the Parquet artifact has been atomically promoted.
    """

    overpass_path = Path(overpass_manifest_path)
    tract_path = Path(tract_manifest_path)
    output = Path(output_directory)
    overpasses, overpass_file_sha256 = _read_stable_overpass_manifest(overpass_path)
    tracts, tract_file_sha256 = _read_stable_tract_manifest(tract_path)
    universe, audit = construct_feature_key_universe(
        overpasses,
        tracts,
        final_test_year=final_test_year,
        expected_date_count=expected_date_count,
        expected_tract_count=expected_tract_count,
    )

    feature_path = output / FEATURE_UNIVERSE_FILENAME
    provenance_path = output / FEATURE_UNIVERSE_PROVENANCE_FILENAME
    output.mkdir(parents=True, exist_ok=True)
    provenance_path.unlink(missing_ok=True)
    atomic_parquet(universe, feature_path)

    frozen = pd.read_parquet(feature_path)
    validate_feature_key_universe(frozen, final_test_year=final_test_year)
    pd.testing.assert_frame_equal(frozen, universe, check_dtype=True)

    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_feature_universe.py",
            "src/la_heat/feature_universe.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=FEATURE_UNIVERSE_ALGORITHM_VERSION,
    )

    payload: dict[str, Any] = {
        "schema_version": FEATURE_UNIVERSE_SCHEMA_VERSION,
        "algorithm_version": FEATURE_UNIVERSE_ALGORITHM_VERSION,
        "status": FEATURE_UNIVERSE_STATUS,
        "phase2_promoted": False,
        "target_blind": True,
        "target_tables_read": [],
        "output_directory": str(output.resolve()),
        "final_test_year": final_test_year,
        "eligible_date_count": audit["eligible_date_count"],
        "primary_tract_count": audit["primary_tract_count"],
        "key_count": audit["key_count"],
        "years": audit["years"],
        "semantic_key_sha256": audit["semantic_key_sha256"],
        "tract_manifest_sha256": audit["tract_manifest_sha256"],
        "source_lock_set_sha256": audit["source_lock_set_sha256"],
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "inputs": {
            "primary_overpass_manifest": {
                "path": str(overpass_path.resolve()),
                "sha256": overpass_file_sha256,
                "bytes": overpass_path.stat().st_size,
                "semantic_sha256": audit["overpass_metadata_semantic_sha256"],
                "columns_read": list(OVERPASS_INPUT_COLUMNS),
            },
            "primary_tract_manifest": {
                "path": str(tract_path.resolve()),
                "sha256": tract_file_sha256,
                "bytes": tract_path.stat().st_size,
                "semantic_sha256": audit["tract_metadata_semantic_sha256"],
                "columns_read": list(TRACT_INPUT_COLUMNS),
            },
        },
        "output_files": {
            FEATURE_UNIVERSE_FILENAME: parquet_file_record(feature_path, frozen),
        },
        "scientific_contract": {
            "keys": list(KEY_COLUMNS),
            "construction": "eligible_overpass_dates_x_primary_tract_geoids",
            "target_date_representation": "timezone-naive civil midnight",
            "target_or_qa_values_used": False,
            "locked_years_included": False,
            "promotion_note": (
                "Draft Phase 2 key support only; predictor completion and registry "
                "validation are still required before Phase 2 promotion."
            ),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, provenance_path)
    return payload
