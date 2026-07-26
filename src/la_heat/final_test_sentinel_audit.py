"""Target-blind, read-only post-run audit for final-test Sentinel predictors.

The audit authenticates the completed Collection 1 build, reconstructs the
published audit/features from source lineage, verifies invariant static
support, and compares the calibration-sensitive output against frozen positive
and negative controls.  It never accepts a Landsat target, QA, fitted-model,
prediction, residual, score, or metric path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.final_test_sentinel_features import (
    ALGORITHM_VERSION as SENTINEL_ALGORITHM_VERSION,
)
from la_heat.final_test_sentinel_features import (
    EXPECTED_ACQUISITION_COUNT,
    EXPECTED_ITEM_COUNT,
    WORLD_COVER_FILENAME,
    authenticate_final_sentinel_inputs,
    authenticate_fixed_spatial_support,
)
from la_heat.final_test_sentinel_features import (
    PIPELINE_FILES as SENTINEL_PIPELINE_FILES,
)
from la_heat.final_test_sentinel_inventory import (
    CALIBRATION_ENCODING,
    CALIBRATION_FORMULA,
    PROHIBITED_LEGACY_COLLECTION,
    PROVIDER_PARITY_EVIDENCE_SHA256,
    STAC_COLLECTION,
)
from la_heat.final_test_state_lock import (
    DEFAULT_FINAL_TEST_STATE_LOCK_PATH,
    FinalTestStateLock,
)
from la_heat.provenance import (
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_feature_builder import (
    _acquisition_cache_directory,
    _acquisition_cache_is_current,
    _expected_acquisition_lock,
    _research_dependency_payload,
    load_sentinel_stage_config,
)
from la_heat.sentinel_feature_stage import (
    _assert_exact_key_support,
    _assert_lineage_contract,
    _normalize_audit,
    _normalize_features,
    _normalize_lineage,
    _normalize_membership,
    _normalize_universe,
)
from la_heat.sentinel_features import INDEX_COLUMNS

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-sentinel-postrun-audit-v1-target-blind"

EXPECTED_TRACT_COUNT: Final = 1_096
EXPECTED_TARGET_DATE_COUNT: Final = 23
EXPECTED_FEATURE_ROW_COUNT: Final = 25_208
EXPECTED_MEMBERSHIP_COUNT: Final = 207
EXPECTED_ACQUISITION_TRACT_ROW_COUNT: Final = (
    EXPECTED_ACQUISITION_COUNT * EXPECTED_TRACT_COUNT
)
EXPECTED_LINEAGE_ROW_COUNT: Final = EXPECTED_MEMBERSHIP_COUNT * EXPECTED_TRACT_COUNT
EXPECTED_LEGACY_ACQUISITION_COUNT: Final = 34

DEFAULT_SENTINEL_OUTPUT_DIRECTORY: Final = Path(
    "data/interim/final_test_2025/sentinel"
)
DEFAULT_SENTINEL_INVENTORY_DIRECTORY: Final = Path(
    "manifests/final_test_2025/sentinel_inventory"
)
DEFAULT_RAW_STAC_DIRECTORY: Final = Path(
    "data/raw/final_test_2025/sentinel/stac_items"
)
DEFAULT_LANDSAT_INVENTORY_DIRECTORY: Final = Path(
    "manifests/final_test_2025/landsat_inventory"
)
DEFAULT_FORMAL_LOCK_PATH: Final = Path("manifests/model_lock/MODEL_LOCK.json")
DEFAULT_RESEARCH_CONFIG_PATH: Final = Path("configs/research.toml")
DEFAULT_SENTINEL_CONFIG_PATH: Final = Path("configs/sentinel_features.toml")
DEFAULT_PREDICTOR_BASE_PATH: Final = Path(
    "data/interim/final_test_2025/predictor_base/predictor_base.parquet"
)
DEFAULT_PREDICTOR_BASE_PROVENANCE_PATH: Final = Path(
    "manifests/final_test_2025/predictor_base/PREDICTOR_BASE.json"
)
DEFAULT_STATIC_AUDIT_PATH: Final = Path(
    "data/processed/static_features/static_feature_audit.parquet"
)
DEFAULT_STATIC_PROVENANCE_PATH: Final = Path(
    "data/processed/static_features/static_features_provenance.json"
)
DEFAULT_FIXED_GRID_LOCK_PATH: Final = Path(
    "data/interim/targets/fixed_grid_lock.json"
)
DEFAULT_TRACT_MANIFEST_PATH: Final = Path(
    "data/interim/targets/primary_tract_manifest.parquet"
)
DEFAULT_CITY_BOUNDARY_PATH: Final = Path(
    "manifests/target_inventory/city_boundary.geojson"
)
DEFAULT_DEVELOPMENT_CONTROL_DIRECTORY: Final = Path(
    "data/interim/sentinel_features"
)
DEFAULT_LEGACY_CONTROL_DIRECTORY: Final = Path(
    "data/interim/superseded/sentinel_features_double_offset_20260723"
)
DEFAULT_AUDIT_PATH: Final = Path(
    "manifests/final_test_2025/sentinel_features/SENTINEL_FEATURE_AUDIT.json"
)
AUTHORIZATION_PATH: Final = Path("manifests/final_test_2025/AUTHORIZATION.json")

AGGREGATE_FILENAMES: Final = (
    "acquisition_tract.parquet",
    "sentinel_features.parquet",
    "sentinel_feature_audit.parquet",
    "sentinel_lineage.parquet",
)
LANDSAT_OUTPUT_FILENAMES: Final = (
    "scene_inventory.csv",
    "overpass_inventory.csv",
    "primary_overpass_manifest.csv",
    "target_blind_key_universe.parquet",
)
SENTINEL_INVENTORY_FILENAMES: Final = (
    "selected_acquisitions.csv",
    "selected_items.csv",
    "target_window_membership.csv",
)
STATIC_OUTPUT_FILENAMES: Final = (
    "static_features.parquet",
    "static_feature_audit.parquet",
    "static_feature_registry.csv",
)
EXPECTED_AGGREGATE_ROWS: Final = {
    "acquisition_tract.parquet": EXPECTED_ACQUISITION_TRACT_ROW_COUNT,
    "sentinel_features.parquet": EXPECTED_FEATURE_ROW_COUNT,
    "sentinel_feature_audit.parquet": EXPECTED_FEATURE_ROW_COUNT,
    "sentinel_lineage.parquet": EXPECTED_LINEAGE_ROW_COUNT,
}
CALIBRATION_DIAGNOSTIC_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_albedo_proxy_lag60",
)
NORMALIZED_DIFFERENCE_DIAGNOSTICS: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_ndwi_lag60",
)
ALBEDO_FEATURE: Final = "sentinel_albedo_proxy_lag60"
EXPECTED_ALBEDO_SHIFT_FROM_LEGACY: Final = 0.1

# These controls are frozen audit evidence.  Their exact Parquet bytes are
# intentionally locked so a changed control cannot silently redefine the
# calibration classifier.
CONTROL_SHA256: Final = {
    "development_acquisition_tract.parquet": (
        "f1eebb7346a36b034924b1870fd73df6631dfabd9cb868b6e277243bca95e54b"
    ),
    "development_sentinel_features.parquet": (
        "1114f61188f55258e4dae95c23cbd02d79bd0b60969e1e2d595b13ad2c9c8154"
    ),
    "legacy_acquisition_tract.parquet": (
        "10dd7a0b5df785d68a92aee06c5eaedfc704ab74db313525adeb762e5cf13683"
    ),
    "legacy_sentinel_features.parquet": (
        "261f68cecd4e614e37ef1cdc270931766a236f513709fee83e0d15471f67f551"
    ),
}

AUDIT_PIPELINE_FILES: Final = (
    "scripts/audit_final_test_sentinel_features.py",
    "src/la_heat/final_test_sentinel_audit.py",
    "src/la_heat/final_test_state_lock.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_stage.py",
)

_FORBIDDEN_ARTIFACT_TOKENS: Final = (
    "target_lst",
    "lst_c",
    "target_available",
    "date_usable",
    "hotspot",
    "label",
    "prediction",
    "residual",
    "model_score",
    "metric",
)


class FinalTestSentinelAuditError(RuntimeError):
    """Raised when a completed Sentinel build cannot be authenticated as safe."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _read_json_stable(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestSentinelAuditError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestSentinelAuditError(f"{label} changed or is not a JSON object.")
    return payload, before


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestSentinelAuditError(f"{label} canonical commit is invalid.")
    return recorded


def _read_parquet_stable(
    path: Path,
    *,
    label: str,
    columns: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        frame = pd.read_parquet(path, columns=None if columns is None else list(columns))
    except Exception as error:
        raise FinalTestSentinelAuditError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before:
        raise FinalTestSentinelAuditError(f"{label} changed while it was read.")
    return frame, before


def _schema_sha256(frame: pd.DataFrame) -> str:
    return canonical_sha256(
        [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
    )


def _verify_parquet_record(
    path: Path,
    frame: pd.DataFrame,
    observed_sha256: str,
    record: object,
    *,
    label: str,
) -> None:
    if not isinstance(record, Mapping):
        raise FinalTestSentinelAuditError(f"{label} output record is missing.")
    recorded_path = record.get("path")
    if recorded_path is not None and Path(str(recorded_path)).resolve() != path.resolve():
        raise FinalTestSentinelAuditError(f"{label} recorded path changed.")
    if (
        record.get("sha256") != observed_sha256
        or record.get("bytes") != path.stat().st_size
        or record.get("rows") != len(frame)
        or record.get("schema_sha256") != _schema_sha256(frame)
    ):
        raise FinalTestSentinelAuditError(f"{label} byte/row/schema lock failed.")


def _snapshot_record(root: Path, path: Path, sha256: str | None = None) -> dict[str, Any]:
    observed = sha256_file(path) if sha256 is None else sha256
    return {
        "path": _relative_path(root, path),
        "sha256": observed,
        "bytes": path.stat().st_size,
    }


def _verify_snapshots(root: Path, records: Sequence[Mapping[str, Any]]) -> None:
    for record in records:
        path = _resolve(root, str(record["path"]))
        if (
            not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise FinalTestSentinelAuditError(
                f"Audit input changed during the read: {record.get('path')}."
            )


def _authorization_absent(path: Path) -> None:
    if os.path.lexists(path):
        raise FinalTestSentinelAuditError(
            "Final-test authorization exists; target-blind Sentinel audit is closed."
        )


def _verify_exact_raw_stac_set(
    raw_directory: Path,
    records: object,
    *,
    expected_count: int = EXPECTED_ITEM_COUNT,
) -> None:
    if not isinstance(records, list) or len(records) != expected_count:
        raise FinalTestSentinelAuditError(
            f"Sentinel raw STAC declaration must contain exactly {expected_count} files."
        )
    filenames = [
        str(record.get("filename", ""))
        for record in records
        if isinstance(record, Mapping)
    ]
    if (
        len(filenames) != expected_count
        or len(set(filenames)) != expected_count
        or any(not name.lower().endswith(".json") for name in filenames)
    ):
        raise FinalTestSentinelAuditError("Sentinel raw STAC filenames are invalid.")
    actual = {
        path.name
        for path in raw_directory.iterdir()
        if path.is_file() and path.suffix.casefold() == ".json"
    }
    if actual != set(filenames):
        raise FinalTestSentinelAuditError(
            "Sentinel raw STAC directory contains a missing or extra JSON file."
        )


def _expected_cache_directories(
    output_directory: Path,
    acquisitions: pd.DataFrame,
) -> set[Path]:
    return {
        _acquisition_cache_directory(output_directory, str(physical_id)).resolve()
        for physical_id in acquisitions["physical_acquisition_id"]
    }


def _verify_exact_cache_directory_set(
    output_directory: Path,
    expected_directories: set[Path],
) -> None:
    by_acquisition = output_directory / "by_acquisition"
    if not by_acquisition.is_dir():
        raise FinalTestSentinelAuditError(
            "Sentinel acquisition cache directory is missing."
        )
    actual = {
        path.resolve() for path in by_acquisition.iterdir() if path.is_dir()
    }
    if actual != expected_directories:
        raise FinalTestSentinelAuditError(
            "Sentinel cache directory set contains a missing or extra acquisition."
        )


def _capture_upstream_snapshots(
    *,
    root: Path,
    formal_path: Path,
    research_path: Path,
    sentinel_config_path: Path,
    landsat_directory: Path,
    sentinel_inventory_directory: Path,
    raw_stac_directory: Path,
    worldcover_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Capture every upstream byte before semantic authentication."""

    snapshots = [
        _snapshot_record(root, formal_path),
        _snapshot_record(root, research_path),
        _snapshot_record(root, sentinel_config_path),
    ]

    landsat_path = landsat_directory / "LANDSAT_INVENTORY.json"
    landsat, landsat_sha = _read_json_stable(
        landsat_path, label="Landsat final-test inventory provenance"
    )
    if set(landsat.get("output_files", {})) != set(LANDSAT_OUTPUT_FILENAMES):
        raise FinalTestSentinelAuditError("Landsat inventory output set changed.")
    snapshots.append(_snapshot_record(root, landsat_path, landsat_sha))
    snapshots.extend(
        _snapshot_record(root, landsat_directory / name)
        for name in LANDSAT_OUTPUT_FILENAMES
    )
    frozen_support = landsat.get("frozen_support")
    if not isinstance(frozen_support, Mapping):
        raise FinalTestSentinelAuditError("Landsat frozen support lock is missing.")
    city_path = _resolve(root, str(frozen_support.get("city_boundary_path", "")))
    if city_path != (root / DEFAULT_CITY_BOUNDARY_PATH).resolve():
        raise FinalTestSentinelAuditError("Landsat city-boundary path changed.")
    snapshots.append(_snapshot_record(root, city_path))

    sentinel_summary_path = sentinel_inventory_directory / "inventory_summary.json"
    sentinel_provenance_path = (
        sentinel_inventory_directory / "FINAL_TEST_SENTINEL_INVENTORY.json"
    )
    sentinel_summary, sentinel_summary_sha = _read_json_stable(
        sentinel_summary_path, label="Sentinel inventory summary"
    )
    sentinel_provenance, sentinel_provenance_sha = _read_json_stable(
        sentinel_provenance_path, label="Sentinel inventory provenance"
    )
    if (
        set(sentinel_summary.get("output_files", {}))
        != set(SENTINEL_INVENTORY_FILENAMES)
        or sentinel_provenance.get("state")
        != "target_blind_inventory_frozen"
    ):
        raise FinalTestSentinelAuditError("Sentinel inventory output set changed.")
    snapshots.extend(
        [
            _snapshot_record(root, sentinel_summary_path, sentinel_summary_sha),
            _snapshot_record(
                root, sentinel_provenance_path, sentinel_provenance_sha
            ),
            *[
                _snapshot_record(root, sentinel_inventory_directory / name)
                for name in SENTINEL_INVENTORY_FILENAMES
            ],
        ]
    )
    raw_snapshot_lock = sentinel_summary.get("raw_stac_snapshots")
    if not isinstance(raw_snapshot_lock, Mapping):
        raise FinalTestSentinelAuditError("Sentinel raw STAC lock is missing.")
    raw_records = raw_snapshot_lock.get("files")
    _verify_exact_raw_stac_set(raw_stac_directory, raw_records)
    assert isinstance(raw_records, list)
    for record in raw_records:
        if not isinstance(record, Mapping):
            raise FinalTestSentinelAuditError("Sentinel raw STAC record is invalid.")
        path = raw_stac_directory / str(record["filename"])
        observed = sha256_file(path)
        if (
            observed != record.get("sha256")
            or path.stat().st_size != record.get("bytes")
        ):
            raise FinalTestSentinelAuditError(
                f"Sentinel raw STAC byte lock failed: {path.name}."
            )
        snapshots.append(_snapshot_record(root, path, observed))

    static_provenance_path = root / DEFAULT_STATIC_PROVENANCE_PATH
    static_provenance, static_provenance_sha = _read_json_stable(
        static_provenance_path, label="Static-feature provenance"
    )
    if set(static_provenance.get("output_files", {})) != set(
        STATIC_OUTPUT_FILENAMES
    ):
        raise FinalTestSentinelAuditError("Static predictor output set changed.")
    static_output_directory = _resolve(
        root, str(static_provenance.get("output_directory", ""))
    )
    if static_output_directory != (root / "data/processed/static_features").resolve():
        raise FinalTestSentinelAuditError("Static predictor output path changed.")
    snapshots.append(
        _snapshot_record(root, static_provenance_path, static_provenance_sha)
    )
    snapshots.extend(
        _snapshot_record(root, static_output_directory / name)
        for name in STATIC_OUTPUT_FILENAMES
    )
    snapshots.extend(
        [
            _snapshot_record(root, root / DEFAULT_FIXED_GRID_LOCK_PATH),
            _snapshot_record(root, root / DEFAULT_TRACT_MANIFEST_PATH),
            _snapshot_record(root, worldcover_path),
        ]
    )
    return snapshots, [dict(record) for record in raw_records]


def validate_completion_contract(
    status: Mapping[str, Any],
    progress: Mapping[str, Any],
    pipeline_fingerprint: Mapping[str, Any],
    *,
    pipeline_file_sha256: str,
    expected_pipeline_sha256: str,
) -> dict[str, Any]:
    """Validate the hard completion contract before any aggregate is trusted."""

    if (
        status.get("state") != "complete"
        or status.get("algorithm_version") != SENTINEL_ALGORITHM_VERSION
        or status.get("total") != EXPECTED_ACQUISITION_COUNT
        or status.get("completed") != EXPECTED_ACQUISITION_COUNT
        or status.get("running") != 0
        or status.get("failed") != 0
        or status.get("current") != []
        or status.get("failures") not in ([], None)
        or status.get("compile_state") != "complete"
        or status.get("promoted_outputs_valid") is not True
    ):
        raise FinalTestSentinelAuditError("Sentinel status is not a clean 36/36 completion.")
    if (
        progress.get("state") != "complete"
        or progress.get("promoted_outputs_valid") is not True
        or progress.get("build_complete") is not True
        or progress.get("expected_physical_acquisition_count")
        != EXPECTED_ACQUISITION_COUNT
        or progress.get("completed_physical_acquisition_count")
        != EXPECTED_ACQUISITION_COUNT
        or progress.get("feature_row_count") != EXPECTED_FEATURE_ROW_COUNT
        or progress.get("target_date_count") != EXPECTED_TARGET_DATE_COUNT
        or progress.get("tract_count") != EXPECTED_TRACT_COUNT
        or progress.get("lineage_row_count") != EXPECTED_LINEAGE_ROW_COUNT
        or not 0
        <= int(progress.get("feature_available_row_count", -1))
        <= EXPECTED_FEATURE_ROW_COUNT
        or progress.get("target_blind_predictor_access")
        != "2025_predictors_only_no_labels"
        or progress.get("requester_pays_product_xml_opened") != "false"
        or progress.get("public_product_xml_opened") != "false"
        or progress.get("sentinel_source_collection") != STAC_COLLECTION
        or progress.get("sentinel_raw_dn_encoding") != CALIBRATION_ENCODING
        or progress.get("sentinel_prohibited_legacy_collection")
        != PROHIBITED_LEGACY_COLLECTION
        or progress.get("sentinel_provider_parity_evidence_sha256")
        != PROVIDER_PARITY_EVIDENCE_SHA256
    ):
        raise FinalTestSentinelAuditError(
            "Sentinel build progress is incomplete, non-C1, or not target-blind."
        )
    if (
        pipeline_fingerprint.get("algorithm_version") != SENTINEL_ALGORITHM_VERSION
        or canonical_sha256(pipeline_fingerprint) != expected_pipeline_sha256
        or progress.get("final_test_sentinel_feature_pipeline_sha256")
        != expected_pipeline_sha256
        or progress.get(
            "final_test_sentinel_feature_pipeline_fingerprint_sha256"
        )
        != pipeline_file_sha256
    ):
        raise FinalTestSentinelAuditError(
            "Sentinel pipeline fingerprint does not authenticate current code/runtime."
        )
    aggregates = progress.get("aggregate_outputs")
    if not isinstance(aggregates, Mapping) or set(aggregates) != set(
        AGGREGATE_FILENAMES
    ):
        raise FinalTestSentinelAuditError("Sentinel aggregate output set changed.")
    return {
        "status_complete": True,
        "progress_complete": True,
        "algorithm_version": SENTINEL_ALGORITHM_VERSION,
        "completed_physical_acquisition_count": EXPECTED_ACQUISITION_COUNT,
        "feature_available_row_count": int(
            progress["feature_available_row_count"]
        ),
    }


def _assert_no_forbidden_columns(frame: pd.DataFrame, *, label: str) -> None:
    offending = sorted(
        column
        for column in frame.columns
        if any(token in str(column).casefold() for token in _FORBIDDEN_ARTIFACT_TOKENS)
    )
    if offending:
        raise FinalTestSentinelAuditError(
            f"{label} contains forbidden target/model columns: {offending}."
        )


def _all_or_none_missing(frame: pd.DataFrame, *, label: str) -> pd.Series:
    missing = frame[list(INDEX_COLUMNS)].isna()
    all_present = ~missing.any(axis=1)
    all_missing = missing.all(axis=1)
    if not np.logical_or(all_present, all_missing).all():
        raise FinalTestSentinelAuditError(
            f"{label} has partial five-feature missingness."
        )
    return pd.Series(all_missing, index=frame.index)


def validate_acquisition_table(
    acquisition: pd.DataFrame,
    *,
    inventory_acquisitions: pd.DataFrame,
    inventory_items: pd.DataFrame,
    tract_geoids: Sequence[str],
    static_audit: pd.DataFrame,
    minimum_coverage: float,
) -> dict[str, Any]:
    """Validate acquisition summaries and invariant static support."""

    required = {
        "tract_geoid",
        "physical_acquisition_id",
        "acquisition_local_date",
        "platform",
        "processing_baseline",
        "eligible_pixel_count_static",
        "valid_area_equivalent_pixels",
        "acquisition_coverage_fraction",
        "acquisition_qualifies_coverage",
        "source_item_ids_audit_only",
        "source_mgrs_tiles_audit_only",
        "calibration_sha256_audit_only",
        "optical_grid_sha256_audit_only",
        "static_land_mask_sha256_audit_only",
        "eligible_pixel_identity_sha256_audit_only",
        *INDEX_COLUMNS,
    }
    missing = sorted(required - set(acquisition))
    if missing:
        raise FinalTestSentinelAuditError(
            f"Acquisition table lacks required columns: {missing}."
        )
    _assert_no_forbidden_columns(acquisition, label="Acquisition table")
    if (
        len(acquisition) != len(inventory_acquisitions) * len(tract_geoids)
        or acquisition.duplicated(["physical_acquisition_id", "tract_geoid"]).any()
        or acquisition["physical_acquisition_id"].nunique()
        != len(inventory_acquisitions)
        or acquisition["tract_geoid"].nunique() != len(tract_geoids)
    ):
        raise FinalTestSentinelAuditError(
            "Acquisition table does not contain one complete tract grid per acquisition."
        )
    expected_tracts = set(map(str, tract_geoids))
    group_tracts = acquisition.groupby(
        "physical_acquisition_id", observed=True
    )["tract_geoid"].agg(lambda values: set(values.astype(str)))
    if not all(value == expected_tracts for value in group_tracts):
        raise FinalTestSentinelAuditError(
            "At least one physical acquisition lacks the exact tract universe."
        )

    inventory_columns = [
        "physical_acquisition_id",
        "acquisition_local_date",
        "platform",
        "processing_baseline",
    ]
    observed_inventory = (
        acquisition[inventory_columns]
        .drop_duplicates()
        .sort_values("physical_acquisition_id", kind="stable")
        .reset_index(drop=True)
        .astype(str)
    )
    expected_inventory = (
        inventory_acquisitions[inventory_columns]
        .sort_values("physical_acquisition_id", kind="stable")
        .reset_index(drop=True)
        .astype(str)
    )
    try:
        pd.testing.assert_frame_equal(
            observed_inventory, expected_inventory, check_dtype=False, check_exact=True
        )
    except AssertionError as error:
        raise FinalTestSentinelAuditError(
            "Acquisition identity/date/platform/baseline changed from inventory."
        ) from error

    expected_items = (
        inventory_items.sort_values(
            ["physical_acquisition_id", "mgrs_tile", "item_id"], kind="stable"
        )
        .groupby("physical_acquisition_id", observed=True)
        .agg(
            source_item_ids_audit_only=("item_id", lambda x: "|".join(map(str, x))),
            source_mgrs_tiles_audit_only=("mgrs_tile", lambda x: "|".join(map(str, x))),
        )
        .reset_index()
    )
    observed_items = (
        acquisition[
            [
                "physical_acquisition_id",
                "source_item_ids_audit_only",
                "source_mgrs_tiles_audit_only",
            ]
        ]
        .drop_duplicates()
        .sort_values("physical_acquisition_id", kind="stable")
        .reset_index(drop=True)
    )
    expected_items = expected_items.sort_values(
        "physical_acquisition_id", kind="stable"
    ).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            observed_items, expected_items, check_dtype=False, check_exact=True
        )
    except AssertionError as error:
        raise FinalTestSentinelAuditError(
            "Acquisition item/tile lineage changed from inventory."
        ) from error
    if (
        inventory_items.groupby("physical_acquisition_id", observed=True).size().ne(2).any()
        or set(inventory_items["mgrs_tile"].astype(str)) != {"11SLT", "11SLU"}
    ):
        raise FinalTestSentinelAuditError(
            "Every C1 acquisition must remain a two-tile 11SLT + 11SLU mosaic."
        )

    coverage = pd.to_numeric(
        acquisition["acquisition_coverage_fraction"], errors="coerce"
    )
    eligible = pd.to_numeric(
        acquisition["eligible_pixel_count_static"], errors="coerce"
    )
    valid_area = pd.to_numeric(
        acquisition["valid_area_equivalent_pixels"], errors="coerce"
    )
    if (
        coverage.isna().any()
        or not coverage.between(0.0, 1.0).all()
        or eligible.isna().any()
        or not (eligible > 0).all()
        or valid_area.isna().any()
        or not np.array_equal(
            (valid_area / eligible).to_numpy(), coverage.to_numpy()
        )
    ):
        raise FinalTestSentinelAuditError(
            "Acquisition coverage/eligible-area arithmetic is invalid."
        )
    qualifies = acquisition["acquisition_qualifies_coverage"].astype(bool)
    expected_qualifies = coverage.ge(minimum_coverage)
    if not qualifies.equals(expected_qualifies):
        raise FinalTestSentinelAuditError(
            "Acquisition coverage qualification disagrees with the frozen gate."
        )
    all_missing = _all_or_none_missing(acquisition, label="Acquisition table")
    if not all_missing.equals(~qualifies):
        raise FinalTestSentinelAuditError(
            "Acquisition feature missingness disagrees with coverage qualification."
        )
    numeric = acquisition[list(INDEX_COLUMNS)].to_numpy(dtype=float)
    if np.isinf(numeric).any():
        raise FinalTestSentinelAuditError("Acquisition features contain infinity.")

    static_required = {
        "tract_geoid",
        "eligible_pixel_count_static",
        "eligible_pixel_identity_sha256",
    }
    if static_required - set(static_audit):
        raise FinalTestSentinelAuditError("Static support audit schema is incomplete.")
    support = (
        acquisition.groupby("tract_geoid", observed=True, sort=True)
        .agg(
            count_nunique=("eligible_pixel_count_static", "nunique"),
            identity_nunique=(
                "eligible_pixel_identity_sha256_audit_only",
                "nunique",
            ),
            eligible_pixel_count_static=("eligible_pixel_count_static", "first"),
            eligible_pixel_identity_sha256=(
                "eligible_pixel_identity_sha256_audit_only",
                "first",
            ),
        )
        .reset_index()
    )
    if not support[["count_nunique", "identity_nunique"]].eq(1).all(axis=None):
        raise FinalTestSentinelAuditError(
            "Static eligible-land denominator or pixel identity changed by acquisition."
        )
    expected_support = (
        static_audit[list(static_required)]
        .sort_values("tract_geoid", kind="stable")
        .reset_index(drop=True)
    )
    observed_support = (
        support[list(static_required)]
        .sort_values("tract_geoid", kind="stable")
        .reset_index(drop=True)
    )
    try:
        pd.testing.assert_frame_equal(
            observed_support,
            expected_support,
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as error:
        raise FinalTestSentinelAuditError(
            "Acquisition fixed support disagrees with static-feature provenance."
        ) from error

    return {
        "row_count": len(acquisition),
        "physical_acquisition_count": acquisition[
            "physical_acquisition_id"
        ].nunique(),
        "tract_count": acquisition["tract_geoid"].nunique(),
        "qualifying_row_count": int(qualifies.sum()),
        "fixed_denominator_invariant": True,
        "two_tile_mosaic_invariant": True,
        "coverage_minimum": float(coverage.min()),
        "coverage_median": float(coverage.median()),
        "coverage_maximum": float(coverage.max()),
    }


def validate_semantic_outputs(
    *,
    features: pd.DataFrame,
    audit: pd.DataFrame,
    lineage: pd.DataFrame,
    acquisition: pd.DataFrame,
    membership: pd.DataFrame,
    predictor_base_keys: pd.DataFrame,
    static_audit: pd.DataFrame,
    inventory_acquisitions: pd.DataFrame,
    inventory_items: pd.DataFrame,
    research: Any,
    minimum_coverage: float,
    minimum_acquisitions: int,
    expected_tract_count: int = EXPECTED_TRACT_COUNT,
    expected_target_date_count: int = EXPECTED_TARGET_DATE_COUNT,
    expected_acquisition_count: int = EXPECTED_ACQUISITION_COUNT,
    expected_membership_count: int = EXPECTED_MEMBERSHIP_COUNT,
) -> dict[str, Any]:
    """Reconstruct the final audit/features and verify all semantic contracts."""

    for label, frame in (
        ("Sentinel features", features),
        ("Sentinel audit", audit),
        ("Sentinel lineage", lineage),
    ):
        _assert_no_forbidden_columns(frame, label=label)

    target_dates = pd.to_datetime(
        predictor_base_keys["target_date"], format="%Y-%m-%d", errors="raise"
    )
    if (
        target_dates.isna().any()
        or not target_dates.dt.year.eq(int(research.final_test_year)).all()
    ):
        raise FinalTestSentinelAuditError(
            "Predictor-base keys are not exclusively from the frozen final-test year."
        )
    # The shared promotion normalizers intentionally reject the configured
    # final-test year because they were written for development promotion.
    # This audit first proves the exact final-test year above, then presents
    # 2025 as the last admissible year solely to reuse their schema/type checks.
    normalization_research = SimpleNamespace(
        final_test_year=int(research.final_test_year) + 1
    )
    universe = _normalize_universe(
        predictor_base_keys[["tract_geoid", "target_date"]],
        research=normalization_research,
    )
    normalized_features = _normalize_features(
        features, research=normalization_research
    )
    normalized_audit = _normalize_audit(
        audit, research=normalization_research
    )
    normalized_membership = _normalize_membership(
        membership, research=normalization_research
    )
    normalized_lineage = _normalize_lineage(
        lineage,
        research=normalization_research,
        minimum_coverage=minimum_coverage,
    )
    _assert_exact_key_support(
        normalized_features, universe, label="Final-test Sentinel features"
    )
    _assert_exact_key_support(
        normalized_audit, universe, label="Final-test Sentinel audit"
    )
    _assert_lineage_contract(
        lineage=normalized_lineage,
        membership=normalized_membership,
        universe=universe,
        features=normalized_features,
        audit=normalized_audit,
        minimum_acquisitions=minimum_acquisitions,
    )
    if (
        universe["tract_geoid"].nunique() != expected_tract_count
        or universe["target_date"].nunique() != expected_target_date_count
        or len(universe) != expected_tract_count * expected_target_date_count
        or len(inventory_acquisitions) != expected_acquisition_count
        or len(inventory_items) != expected_acquisition_count * 2
        or len(normalized_membership) != expected_membership_count
        or len(normalized_lineage) != expected_membership_count * expected_tract_count
    ):
        raise FinalTestSentinelAuditError(
            "Final Sentinel cardinalities changed from the frozen contracts."
        )

    acquisition_metrics = validate_acquisition_table(
        acquisition,
        inventory_acquisitions=inventory_acquisitions,
        inventory_items=inventory_items,
        tract_geoids=universe["tract_geoid"].drop_duplicates().astype(str),
        static_audit=static_audit,
        minimum_coverage=minimum_coverage,
    )
    if acquisition_metrics["physical_acquisition_count"] != expected_acquisition_count:
        raise FinalTestSentinelAuditError("Acquisition count changed.")

    comparison_columns = [
        "physical_acquisition_id",
        "acquisition_local_date",
        "tract_geoid",
        "eligible_pixel_count_static",
        "acquisition_coverage_fraction",
        *INDEX_COLUMNS,
        "eligible_pixel_identity_sha256_audit_only",
    ]
    observed_lineage = normalized_lineage[comparison_columns].copy()
    expected_acquisition = acquisition[comparison_columns].copy()
    observed_lineage["acquisition_local_date"] = pd.to_datetime(
        observed_lineage["acquisition_local_date"]
    ).dt.strftime("%Y-%m-%d")
    expected_acquisition["acquisition_local_date"] = pd.to_datetime(
        expected_acquisition["acquisition_local_date"]
    ).dt.strftime("%Y-%m-%d")
    compared = observed_lineage.merge(
        expected_acquisition,
        on=["physical_acquisition_id", "acquisition_local_date", "tract_geoid"],
        how="left",
        validate="many_to_one",
        suffixes=("_lineage", "_acquisition"),
        indicator=True,
    )
    if not compared["_merge"].eq("both").all():
        raise FinalTestSentinelAuditError(
            "Lineage contains a row absent from acquisition summaries."
        )
    for column in (
        "eligible_pixel_count_static",
        "acquisition_coverage_fraction",
        *INDEX_COLUMNS,
        "eligible_pixel_identity_sha256_audit_only",
    ):
        left = compared[f"{column}_lineage"]
        right = compared[f"{column}_acquisition"]
        if pd.api.types.is_numeric_dtype(left):
            equal = np.equal(left.to_numpy(), right.to_numpy()) | (
                left.isna().to_numpy() & right.isna().to_numpy()
            )
            if not equal.all():
                raise FinalTestSentinelAuditError(
                    f"Lineage {column} changed from acquisition summaries."
                )
        elif not np.array_equal(
            left.astype(str).to_numpy(), right.astype(str).to_numpy()
        ):
            raise FinalTestSentinelAuditError(
                f"Lineage {column} changed from acquisition summaries."
            )

    feature_missing = normalized_features[list(INDEX_COLUMNS)].isna().all(axis=1)
    available = normalized_audit["sentinel_feature_available"].astype(bool)
    if not feature_missing.equals(~available):
        raise FinalTestSentinelAuditError(
            "Final feature missingness disagrees with reconstructed availability."
        )
    if np.isinf(
        normalized_features[list(INDEX_COLUMNS)].to_numpy(dtype=float)
    ).any():
        raise FinalTestSentinelAuditError("Final Sentinel features contain infinity.")

    temporal_lag = normalized_lineage["source_age_days_audit_only"]
    availability_by_date = (
        normalized_audit.groupby("target_date", observed=True)[
            "sentinel_feature_available"
        ]
        .mean()
        .sort_index()
    )
    return {
        "feature_row_count": len(normalized_features),
        "audit_row_count": len(normalized_audit),
        "lineage_row_count": len(normalized_lineage),
        "membership_row_count": len(normalized_membership),
        "target_date_count": universe["target_date"].nunique(),
        "tract_count": universe["tract_geoid"].nunique(),
        "feature_available_row_count": int(available.sum()),
        "feature_missing_row_count": int((~available).sum()),
        "all_or_none_feature_missingness": True,
        "minimum_source_age_days": int(temporal_lag.min()),
        "maximum_source_age_days": int(temporal_lag.max()),
        "target_day_or_future_source_count": 0,
        "minimum_date_available_fraction": float(availability_by_date.min()),
        "median_date_available_fraction": float(availability_by_date.median()),
        "maximum_date_available_fraction": float(availability_by_date.max()),
        "semantic_feature_table_sha256": canonical_frame_sha256(
            normalized_features,
            sort_by=["target_date", "tract_geoid"],
        ),
        "semantic_audit_table_sha256": canonical_frame_sha256(
            normalized_audit,
            sort_by=["target_date", "tract_geoid"],
        ),
        "semantic_lineage_core_sha256": canonical_frame_sha256(
            normalized_lineage,
            sort_by=["target_date", "tract_geoid", "physical_acquisition_id"],
        ),
        "acquisition": acquisition_metrics,
    }


def _feature_median(frame: pd.DataFrame, feature: str, *, label: str) -> float:
    values = pd.to_numeric(frame[feature], errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        raise FinalTestSentinelAuditError(f"{label} has no finite {feature} values.")
    return float(finite.median())


def _date_median_envelope(frame: pd.DataFrame, feature: str) -> dict[str, Any]:
    values = frame[["target_date", feature]].copy()
    values[feature] = pd.to_numeric(values[feature], errors="coerce")
    medians = values.groupby("target_date", observed=True)[feature].median().dropna()
    if medians.empty:
        raise FinalTestSentinelAuditError(
            f"Cannot compute a target-date median envelope for {feature}."
        )
    return {
        "date_count": len(medians),
        "minimum": float(medians.min()),
        "median": float(medians.median()),
        "maximum": float(medians.max()),
    }


def _outside_unit_rate(frame: pd.DataFrame, feature: str) -> dict[str, Any]:
    values = pd.to_numeric(frame[feature], errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        raise FinalTestSentinelAuditError(f"No finite values for {feature}.")
    count = int(((finite < -1.0) | (finite > 1.0)).sum())
    return {
        "finite_count": len(finite),
        "outside_minus1_plus1_count": count,
        "outside_minus1_plus1_fraction": float(count / len(finite)),
    }


def classify_calibration(
    *,
    final_features: pd.DataFrame,
    development_features: pd.DataFrame,
    legacy_features: pd.DataFrame,
    final_acquisition: pd.DataFrame,
    legacy_acquisition: pd.DataFrame,
    expected_final_acquisition_count: int = EXPECTED_ACQUISITION_COUNT,
    expected_legacy_acquisition_count: int = EXPECTED_LEGACY_ACQUISITION_COUNT,
    expected_tract_count: int = EXPECTED_TRACT_COUNT,
) -> dict[str, Any]:
    """Classify C1 calibration against frozen valid and double-offset controls.

    Three predeclared, strongly discriminating medians (NDVI, NDWI, and albedo)
    must each be strictly closer to the development-valid median than to the
    legacy double-offset median.  NDVI/NDWI out-of-unit fractions must also be
    closer to the valid control.  Finally, on common acquisition-date/tract
    pairs, the albedo difference from the legacy run must fit the +0.1
    correction hypothesis better than the repeated-offset (0.0) hypothesis.
    """

    for label, frame in (
        ("final features", final_features),
        ("development control", development_features),
        ("legacy control", legacy_features),
    ):
        missing = (set(INDEX_COLUMNS) | {"target_date"}) - set(frame)
        if missing:
            raise FinalTestSentinelAuditError(
                f"{label} lacks calibration columns: {sorted(missing)}."
            )
        _all_or_none_missing(frame, label=label)
        if np.isinf(frame[list(INDEX_COLUMNS)].to_numpy(dtype=float)).any():
            raise FinalTestSentinelAuditError(f"{label} contains infinity.")

    median_criteria: dict[str, Any] = {}
    envelopes: dict[str, Any] = {}
    for feature in INDEX_COLUMNS:
        final_median = _feature_median(final_features, feature, label="Final features")
        development_median = _feature_median(
            development_features, feature, label="Development control"
        )
        legacy_median = _feature_median(
            legacy_features, feature, label="Legacy control"
        )
        distance_to_development = abs(final_median - development_median)
        distance_to_legacy = abs(final_median - legacy_median)
        diagnostic = feature in CALIBRATION_DIAGNOSTIC_FEATURES
        median_criteria[feature] = {
            "used_for_classification": diagnostic,
            "final_median": final_median,
            "development_valid_median": development_median,
            "legacy_double_offset_median": legacy_median,
            "absolute_distance_to_development_valid": distance_to_development,
            "absolute_distance_to_legacy_double_offset": distance_to_legacy,
            "closer_to_development_valid": distance_to_development
            < distance_to_legacy,
        }
        envelopes[feature] = {
            "final": _date_median_envelope(final_features, feature),
            "development_valid": _date_median_envelope(
                development_features, feature
            ),
            "legacy_double_offset": _date_median_envelope(
                legacy_features, feature
            ),
        }

    range_criteria: dict[str, Any] = {}
    for feature in NORMALIZED_DIFFERENCE_DIAGNOSTICS:
        final_rate = _outside_unit_rate(final_features, feature)
        development_rate = _outside_unit_rate(development_features, feature)
        legacy_rate = _outside_unit_rate(legacy_features, feature)
        final_fraction = final_rate["outside_minus1_plus1_fraction"]
        valid_fraction = development_rate["outside_minus1_plus1_fraction"]
        bad_fraction = legacy_rate["outside_minus1_plus1_fraction"]
        range_criteria[feature] = {
            "final": final_rate,
            "development_valid": development_rate,
            "legacy_double_offset": legacy_rate,
            "closer_to_development_valid": abs(final_fraction - valid_fraction)
            < abs(final_fraction - bad_fraction),
        }

    join_keys = ["acquisition_local_date", "tract_geoid"]
    if (
        final_acquisition.duplicated(join_keys).any()
        or legacy_acquisition.duplicated(join_keys).any()
        or final_acquisition["acquisition_local_date"].nunique()
        != expected_final_acquisition_count
        or legacy_acquisition["acquisition_local_date"].nunique()
        != expected_legacy_acquisition_count
    ):
        raise FinalTestSentinelAuditError(
            "Calibration acquisition controls do not have one acquisition per local date."
        )
    final_dates = set(final_acquisition["acquisition_local_date"].astype(str))
    legacy_dates = set(legacy_acquisition["acquisition_local_date"].astype(str))
    if not legacy_dates.issubset(final_dates) or len(legacy_dates) != (
        expected_legacy_acquisition_count
    ):
        raise FinalTestSentinelAuditError(
            "Legacy calibration dates are not the expected subset of C1 dates."
        )
    joined = final_acquisition[join_keys + [ALBEDO_FEATURE]].merge(
        legacy_acquisition[join_keys + [ALBEDO_FEATURE]],
        on=join_keys,
        how="inner",
        validate="one_to_one",
        suffixes=("_c1", "_legacy"),
    )
    expected_common_rows = expected_legacy_acquisition_count * expected_tract_count
    if len(joined) != expected_common_rows:
        raise FinalTestSentinelAuditError(
            "Common C1/legacy acquisition-date × tract support changed."
        )
    final_albedo = pd.to_numeric(
        joined[f"{ALBEDO_FEATURE}_c1"], errors="coerce"
    )
    legacy_albedo = pd.to_numeric(
        joined[f"{ALBEDO_FEATURE}_legacy"], errors="coerce"
    )
    paired = final_albedo.notna() & legacy_albedo.notna()
    if not paired.any():
        raise FinalTestSentinelAuditError(
            "No finite common albedo pairs exist for the offset diagnostic."
        )
    delta = (final_albedo.loc[paired] - legacy_albedo.loc[paired]).to_numpy()
    correct_shift_error = float(
        np.median(np.abs(delta - EXPECTED_ALBEDO_SHIFT_FROM_LEGACY))
    )
    repeated_offset_error = float(np.median(np.abs(delta)))
    shift_criterion = {
        "common_acquisition_date_count": len(legacy_dates),
        "common_date_tract_row_count": len(joined),
        "finite_pair_count": int(paired.sum()),
        "expected_corrected_shift": EXPECTED_ALBEDO_SHIFT_FROM_LEGACY,
        "observed_median_shift": float(np.median(delta)),
        "median_absolute_error_to_corrected_shift": correct_shift_error,
        "median_absolute_error_to_repeated_offset": repeated_offset_error,
        "supports_corrected_c1": correct_shift_error < repeated_offset_error,
    }

    median_pass = all(
        median_criteria[feature]["closer_to_development_valid"]
        for feature in CALIBRATION_DIAGNOSTIC_FEATURES
    )
    range_pass = all(
        result["closer_to_development_valid"]
        for result in range_criteria.values()
    )
    passed = bool(
        median_pass and range_pass and shift_criterion["supports_corrected_c1"]
    )
    return {
        "classification": (
            "c1_calibration_consistent"
            if passed
            else "legacy_double_offset_or_ambiguous"
        ),
        "passed": passed,
        "decision_rule": {
            "diagnostic_medians": (
                "NDVI, NDWI, and albedo final medians must each be strictly "
                "closer to the frozen development-valid median than to the "
                "frozen legacy double-offset median."
            ),
            "normalized_difference_ranges": (
                "NDVI and NDWI fractions outside [-1, 1] must each be strictly "
                "closer to the development-valid control fraction."
            ),
            "paired_albedo_shift": (
                "On all common acquisition-date/tract pairs, median absolute "
                "error to the +0.1 corrected-shift hypothesis must be lower "
                "than error to the 0.0 repeated-offset hypothesis."
            ),
            "mixed_or_tied_evidence": "fail_closed",
        },
        "diagnostic_medians": median_criteria,
        "target_date_median_envelopes": envelopes,
        "normalized_difference_range_diagnostics": range_criteria,
        "paired_albedo_shift_diagnostic": shift_criterion,
    }


def _authenticate_caches(
    *,
    root: Path,
    output_directory: Path,
    inventory: Any,
    contracts: Mapping[str, Any],
    base_lock: dict[str, str],
    acquisition: pd.DataFrame,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_acquisition = output_directory / "by_acquisition"
    if not by_acquisition.is_dir():
        raise FinalTestSentinelAuditError("Sentinel acquisition cache directory is missing.")
    expected_directories = {
        _acquisition_cache_directory(
            output_directory, str(row.physical_acquisition_id)
        ).resolve()
        for row in inventory.acquisitions.itertuples(index=False)
    }
    actual_directories = {
        path.resolve() for path in by_acquisition.iterdir() if path.is_dir()
    }
    if actual_directories != expected_directories:
        raise FinalTestSentinelAuditError(
            "Sentinel cache directory set contains missing or stale acquisitions."
        )

    snapshots: list[dict[str, Any]] = []
    cache_records: list[dict[str, Any]] = []
    for row in inventory.acquisitions.itertuples(index=False):
        physical_id = str(row.physical_acquisition_id)
        item_rows = inventory.items.loc[
            inventory.items["physical_acquisition_id"].astype(str) == physical_id
        ]
        directory = _acquisition_cache_directory(output_directory, physical_id)
        expected_lock = _expected_acquisition_lock(
            base_lock=base_lock,
            physical_id=physical_id,
            item_rows=item_rows,
        )
        if not _acquisition_cache_is_current(directory, expected_lock=expected_lock):
            raise FinalTestSentinelAuditError(
                f"Sentinel cache is not current: {physical_id}."
            )
        summary_path = directory / "summary.json"
        cache_path = directory / "acquisition_tract.parquet"
        summary, summary_sha = _read_json_stable(
            summary_path, label=f"Sentinel cache summary {physical_id}"
        )
        cache, cache_sha = _read_parquet_stable(
            cache_path, label=f"Sentinel cache table {physical_id}"
        )
        _verify_parquet_record(
            cache_path,
            cache,
            cache_sha,
            summary.get("output_file"),
            label=f"Sentinel cache {physical_id}",
        )
        sorted_items = item_rows.sort_values(["mgrs_tile", "item_id"], kind="stable")
        item_ids = list(sorted_items["item_id"].astype(str))
        mgrs_tiles = list(sorted_items["mgrs_tile"].astype(str))
        expected_calibration = [
            contracts[item_id].calibration_sha256 for item_id in item_ids
        ]
        if (
            summary.get("schema_version") != 1
            or summary.get("algorithm_version") != SENTINEL_ALGORITHM_VERSION
            or summary.get("state") != "complete"
            or summary.get("cache_lock") != expected_lock
            or summary.get("physical_acquisition_id") != physical_id
            or summary.get("source_collection") != STAC_COLLECTION
            or summary.get("raw_dn_encoding") != CALIBRATION_ENCODING
            or summary.get("prohibited_legacy_collection")
            != PROHIBITED_LEGACY_COLLECTION
            or summary.get("provider_parity_evidence_sha256")
            != PROVIDER_PARITY_EVIDENCE_SHA256
            or summary.get("cog_decode_formula") != CALIBRATION_FORMULA
            or summary.get("requester_pays_product_xml_opened") is not False
            or summary.get("public_product_xml_opened") is not False
            or summary.get("item_ids") != item_ids
            or summary.get("mgrs_tiles") != mgrs_tiles
            or summary.get("calibration_sha256s") != expected_calibration
            or summary.get("accepted_scl_classes") != [4, 5]
            or summary.get("tract_count") != EXPECTED_TRACT_COUNT
        ):
            raise FinalTestSentinelAuditError(
                f"Sentinel cache lineage/calibration contract failed: {physical_id}."
            )
        expected_slice = (
            acquisition.loc[
                acquisition["physical_acquisition_id"].astype(str) == physical_id
            ]
            .sort_values("tract_geoid", kind="stable")
            .reset_index(drop=True)
        )
        observed_slice = cache.sort_values(
            "tract_geoid", kind="stable"
        ).reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                observed_slice,
                expected_slice,
                check_dtype=True,
                check_exact=True,
            )
        except AssertionError as error:
            raise FinalTestSentinelAuditError(
                f"Aggregate acquisition slice changed from cache: {physical_id}."
            ) from error
        if summary.get("qualifying_tract_count") != int(
            cache["acquisition_qualifies_coverage"].sum()
        ):
            raise FinalTestSentinelAuditError(
                f"Cache qualifying count changed: {physical_id}."
            )
        snapshots.extend(
            [
                _snapshot_record(root, summary_path, summary_sha),
                _snapshot_record(root, cache_path, cache_sha),
            ]
        )
        cache_records.append(
            {
                "physical_acquisition_id": physical_id,
                "summary_sha256": summary_sha,
                "output_sha256": cache_sha,
            }
        )
    return {
        "cache_count": len(cache_records),
        "all_current": True,
        "cache_set_sha256": canonical_sha256(cache_records),
    }, snapshots


def _publish_report(
    report: dict[str, Any],
    *,
    root: Path,
    output_path: Path,
) -> dict[str, Any]:
    expected_parent = root / "manifests/final_test_2025/sentinel_features"
    expected_name = DEFAULT_AUDIT_PATH.name
    if (
        output_path.resolve().parent != expected_parent.resolve()
        or output_path.name != expected_name
    ):
        raise FinalTestSentinelAuditError(
            "Audit output must be the isolated canonical Sentinel audit JSON."
        )
    committed = dict(report)
    committed["commit_sha256"] = canonical_sha256(committed)
    if output_path.exists():
        existing, _ = _read_json_stable(output_path, label="Existing Sentinel audit")
        _verify_commit(existing, label="Existing Sentinel audit")
        if existing != committed:
            raise FinalTestSentinelAuditError(
                "An existing Sentinel audit belongs to different immutable inputs."
            )
        return existing
    atomic_json(committed, output_path)
    frozen, _ = _read_json_stable(output_path, label="Published Sentinel audit")
    _verify_commit(frozen, label="Published Sentinel audit")
    if frozen != committed:
        raise FinalTestSentinelAuditError("Published Sentinel audit changed on readback.")
    return frozen


def _audit_final_test_sentinel_features_locked(
    *,
    sentinel_output_directory: str | Path = DEFAULT_SENTINEL_OUTPUT_DIRECTORY,
    sentinel_inventory_directory: str | Path = DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    raw_stac_directory: str | Path = DEFAULT_RAW_STAC_DIRECTORY,
    landsat_inventory_directory: str | Path = DEFAULT_LANDSAT_INVENTORY_DIRECTORY,
    formal_lock_path: str | Path = DEFAULT_FORMAL_LOCK_PATH,
    research_config_path: str | Path = DEFAULT_RESEARCH_CONFIG_PATH,
    sentinel_config_path: str | Path = DEFAULT_SENTINEL_CONFIG_PATH,
    predictor_base_path: str | Path = DEFAULT_PREDICTOR_BASE_PATH,
    predictor_base_provenance_path: str | Path = (
        DEFAULT_PREDICTOR_BASE_PROVENANCE_PATH
    ),
    static_audit_path: str | Path = DEFAULT_STATIC_AUDIT_PATH,
    development_control_directory: str | Path = (
        DEFAULT_DEVELOPMENT_CONTROL_DIRECTORY
    ),
    legacy_control_directory: str | Path = DEFAULT_LEGACY_CONTROL_DIRECTORY,
    output_path: str | Path = DEFAULT_AUDIT_PATH,
) -> dict[str, Any]:
    """Audit and publish while the caller owns the shared final-test lock."""

    root = _project_root().resolve()
    output_directory = _resolve(root, sentinel_output_directory)
    inventory_directory = _resolve(root, sentinel_inventory_directory)
    raw_directory = _resolve(root, raw_stac_directory)
    landsat_directory = _resolve(root, landsat_inventory_directory)
    formal_path = _resolve(root, formal_lock_path)
    research_path = _resolve(root, research_config_path)
    sentinel_config = _resolve(root, sentinel_config_path)
    base_path = _resolve(root, predictor_base_path)
    base_provenance_path = _resolve(root, predictor_base_provenance_path)
    static_path = _resolve(root, static_audit_path)
    development_directory = _resolve(root, development_control_directory)
    legacy_directory = _resolve(root, legacy_control_directory)
    audit_path = _resolve(root, output_path)

    authorization_path = (root / AUTHORIZATION_PATH).resolve()
    _authorization_absent(authorization_path)
    worldcover_path = (
        root / "data/raw/final_test_2025/static" / WORLD_COVER_FILENAME
    )
    if not worldcover_path.is_file():
        raise FinalTestSentinelAuditError(
            "Frozen WorldCover predictor support is missing; audit never downloads data."
        )

    upstream_snapshots, raw_stac_records = _capture_upstream_snapshots(
        root=root,
        formal_path=formal_path,
        research_path=research_path,
        sentinel_config_path=sentinel_config,
        landsat_directory=landsat_directory,
        sentinel_inventory_directory=inventory_directory,
        raw_stac_directory=raw_directory,
        worldcover_path=worldcover_path,
    )

    status_path = output_directory / "status.json"
    progress_path = output_directory / "build_progress.json"
    pipeline_path = output_directory / "pipeline_fingerprint.json"
    status, status_sha = _read_json_stable(status_path, label="Sentinel status")
    progress, progress_sha = _read_json_stable(
        progress_path, label="Sentinel build progress"
    )
    pipeline, pipeline_file_sha = _read_json_stable(
        pipeline_path, label="Sentinel pipeline fingerprint"
    )
    expected_pipeline_sha, expected_pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=SENTINEL_PIPELINE_FILES,
        algorithm_version=SENTINEL_ALGORITHM_VERSION,
    )
    if pipeline != expected_pipeline:
        raise FinalTestSentinelAuditError(
            "Sentinel pipeline fingerprint payload changed from current code/runtime."
        )
    completion = validate_completion_contract(
        status,
        progress,
        pipeline,
        pipeline_file_sha256=pipeline_file_sha,
        expected_pipeline_sha256=expected_pipeline_sha,
    )

    authenticated = authenticate_final_sentinel_inputs(
        project_root=root,
        research_config_path=research_path,
        formal_lock_path=formal_path,
        landsat_inventory_directory=landsat_directory,
        sentinel_inventory_directory=inventory_directory,
        raw_stac_directory=raw_directory,
    )
    stage = load_sentinel_stage_config(sentinel_config)
    spatial = authenticate_fixed_spatial_support(
        project_root=root,
        authenticated=authenticated,
        stage=stage,
        worldcover_path=worldcover_path,
    )
    base_lock = {
        "final_test_sentinel_feature_pipeline_sha256": expected_pipeline_sha,
        "final_test_sentinel_feature_pipeline_fingerprint_sha256": (
            pipeline_file_sha
        ),
        "sentinel_stage_config_sha256": stage.sha256,
        "target_blind_predictor_access": "2025_predictors_only_no_labels",
        "requester_pays_product_xml_opened": "false",
        "public_product_xml_opened": "false",
        "sentinel_source_collection": STAC_COLLECTION,
        "sentinel_raw_dn_encoding": CALIBRATION_ENCODING,
        "sentinel_prohibited_legacy_collection": PROHIBITED_LEGACY_COLLECTION,
        "sentinel_provider_parity_evidence_sha256": (
            PROVIDER_PARITY_EVIDENCE_SHA256
        ),
        **authenticated.locks,
        **spatial.locks,
    }
    mismatched_locks = sorted(
        key for key, value in base_lock.items() if progress.get(key) != value
    )
    if (
        mismatched_locks
        or progress.get("sentinel_stage_config_payload") != stage.raw
        or progress.get("sentinel_stage_config_sha256")
        != canonical_sha256(stage.raw)
        or progress.get("sentinel_research_dependency_payload")
        != _research_dependency_payload(authenticated.predictor_research)
    ):
        raise FinalTestSentinelAuditError(
            f"Sentinel request/input locks drifted: {mismatched_locks}."
        )

    aggregate_frames: dict[str, pd.DataFrame] = {}
    snapshots: list[dict[str, Any]] = [
        *upstream_snapshots,
        _snapshot_record(root, status_path, status_sha),
        _snapshot_record(root, progress_path, progress_sha),
        _snapshot_record(root, pipeline_path, pipeline_file_sha),
    ]
    aggregate_records = progress["aggregate_outputs"]
    for filename in AGGREGATE_FILENAMES:
        path = output_directory / filename
        frame, observed_sha = _read_parquet_stable(
            path, label=f"Sentinel aggregate {filename}"
        )
        _verify_parquet_record(
            path,
            frame,
            observed_sha,
            aggregate_records[filename],
            label=f"Sentinel aggregate {filename}",
        )
        if len(frame) != EXPECTED_AGGREGATE_ROWS[filename]:
            raise FinalTestSentinelAuditError(
                f"Sentinel aggregate row count changed: {filename}."
            )
        aggregate_frames[filename] = frame
        snapshots.append(_snapshot_record(root, path, observed_sha))

    base_provenance, base_provenance_sha = _read_json_stable(
        base_provenance_path, label="Predictor-base provenance"
    )
    _verify_commit(base_provenance, label="Predictor-base provenance")
    if (
        base_provenance.get("state") != "complete_target_blind"
        or base_provenance.get("target_blind") is not True
        or base_provenance.get("target_values_read") is not False
        or base_provenance.get("models_loaded") is not False
        or base_provenance.get("model_scores_read") is not False
    ):
        raise FinalTestSentinelAuditError("Predictor-base provenance is not target-blind.")
    base_keys, base_sha = _read_parquet_stable(
        base_path,
        label="Predictor-base keys",
        columns=["tract_geoid", "target_date"],
    )
    base_record = base_provenance.get("output_files", {}).get(base_path.name)
    if (
        not isinstance(base_record, Mapping)
        or base_record.get("sha256") != base_sha
        or base_record.get("bytes") != base_path.stat().st_size
        or base_record.get("rows") != EXPECTED_FEATURE_ROW_COUNT
    ):
        raise FinalTestSentinelAuditError("Predictor-base key source byte lock failed.")
    snapshots.extend(
        [
            _snapshot_record(root, base_provenance_path, base_provenance_sha),
            _snapshot_record(root, base_path, base_sha),
        ]
    )

    static_audit, static_sha = _read_parquet_stable(
        static_path, label="Static eligible-land audit"
    )
    if static_sha != progress.get("static_feature_audit_sha256"):
        raise FinalTestSentinelAuditError("Static support audit hash changed.")
    snapshots.append(_snapshot_record(root, static_path, static_sha))

    semantic = validate_semantic_outputs(
        features=aggregate_frames["sentinel_features.parquet"],
        audit=aggregate_frames["sentinel_feature_audit.parquet"],
        lineage=aggregate_frames["sentinel_lineage.parquet"],
        acquisition=aggregate_frames["acquisition_tract.parquet"],
        membership=authenticated.inventory.membership,
        predictor_base_keys=base_keys,
        static_audit=static_audit,
        inventory_acquisitions=authenticated.inventory.acquisitions,
        inventory_items=authenticated.inventory.items,
        research=authenticated.predictor_research,
        minimum_coverage=stage.minimum_coverage,
        minimum_acquisitions=stage.minimum_acquisitions,
    )
    if semantic["feature_available_row_count"] != progress.get(
        "feature_available_row_count"
    ):
        raise FinalTestSentinelAuditError(
            "Reconstructed Sentinel availability disagrees with build progress."
        )

    cache_audit, cache_snapshots = _authenticate_caches(
        root=root,
        output_directory=output_directory,
        inventory=authenticated.inventory,
        contracts=authenticated.contracts,
        base_lock=base_lock,
        acquisition=aggregate_frames["acquisition_tract.parquet"],
    )
    snapshots.extend(cache_snapshots)

    development_feature_path = development_directory / "sentinel_features.parquet"
    development_acquisition_path = development_directory / "acquisition_tract.parquet"
    legacy_feature_path = legacy_directory / "sentinel_features.parquet"
    legacy_acquisition_path = legacy_directory / "acquisition_tract.parquet"
    control_paths = {
        "development_sentinel_features.parquet": development_feature_path,
        "development_acquisition_tract.parquet": development_acquisition_path,
        "legacy_sentinel_features.parquet": legacy_feature_path,
        "legacy_acquisition_tract.parquet": legacy_acquisition_path,
    }
    control_frames: dict[str, pd.DataFrame] = {}
    classification_inputs: dict[str, Any] = {}
    for key, path in control_paths.items():
        frame, observed_sha = _read_parquet_stable(
            path, label=f"Calibration control {key}"
        )
        if observed_sha != CONTROL_SHA256[key]:
            raise FinalTestSentinelAuditError(
                f"Frozen calibration control hash changed: {key}."
            )
        control_frames[key] = frame
        record = _snapshot_record(root, path, observed_sha)
        classification_inputs[key] = record
        snapshots.append(record)

    classification = classify_calibration(
        final_features=aggregate_frames["sentinel_features.parquet"],
        development_features=control_frames[
            "development_sentinel_features.parquet"
        ],
        legacy_features=control_frames["legacy_sentinel_features.parquet"],
        final_acquisition=aggregate_frames["acquisition_tract.parquet"],
        legacy_acquisition=control_frames["legacy_acquisition_tract.parquet"],
    )
    if not classification["passed"]:
        raise FinalTestSentinelAuditError(
            "C1 calibration is indistinguishable from or closer to the legacy "
            "double-offset control."
        )

    audit_pipeline_sha, audit_pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=AUDIT_PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    unique_snapshots = {
        (record["path"], record["sha256"]): dict(record) for record in snapshots
    }
    ordered_snapshots = sorted(
        unique_snapshots.values(), key=lambda record: str(record["path"])
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "passed",
        "safe_for_final_predictor_assembly": True,
        "target_blind": True,
        "target_or_qa_values_read": False,
        "target_or_qa_paths_opened": [],
        "fitted_models_loaded": False,
        "predictions_scores_or_metrics_read": False,
        "authorization_file_present": False,
        "sentinel_algorithm_version": SENTINEL_ALGORITHM_VERSION,
        "source_collection": STAC_COLLECTION,
        "raw_dn_encoding": CALIBRATION_ENCODING,
        "prohibited_legacy_collection": PROHIBITED_LEGACY_COLLECTION,
        "provider_parity_evidence_sha256": PROVIDER_PARITY_EVIDENCE_SHA256,
        "completion_contract": completion,
        "semantic_contract": semantic,
        "cache_contract": cache_audit,
        "calibration_classification": classification,
        "classification_input_files": classification_inputs,
        "authenticated_input_files": ordered_snapshots,
        "authenticated_input_file_set_sha256": canonical_sha256(
            ordered_snapshots
        ),
        "upstream_locks": {
            "formal_model_lock_sha256": authenticated.formal_lock_sha256,
            "formal_model_lock_commit_sha256": authenticated.formal_lock[
                "commit_sha256"
            ],
            "sentinel_inventory_provenance_sha256": authenticated.locks[
                "final_sentinel_inventory_provenance_sha256"
            ],
            "sentinel_inventory_commit_sha256": authenticated.locks[
                "final_sentinel_inventory_commit_sha256"
            ],
            "sentinel_inventory_semantic_sha256": authenticated.locks[
                "sentinel_inventory_semantic_sha256"
            ],
            "raw_stac_snapshot_set_sha256": authenticated.locks[
                "raw_stac_snapshot_set_sha256"
            ],
            "static_feature_audit_sha256": spatial.locks[
                "static_feature_audit_sha256"
            ],
            "target_grid_identity_sha256": spatial.locks[
                "target_grid_identity_sha256"
            ],
        },
        "audit_pipeline_sha256": audit_pipeline_sha,
        "audit_pipeline_fingerprint": audit_pipeline,
    }
    _verify_snapshots(root, ordered_snapshots)
    _verify_exact_raw_stac_set(raw_directory, raw_stac_records)
    _verify_exact_cache_directory_set(
        output_directory,
        _expected_cache_directories(
            output_directory,
            authenticated.inventory.acquisitions,
        ),
    )
    _authorization_absent(authorization_path)
    return _publish_report(report, root=root, output_path=audit_path)


def audit_final_test_sentinel_features(
    *,
    sentinel_output_directory: str | Path = DEFAULT_SENTINEL_OUTPUT_DIRECTORY,
    sentinel_inventory_directory: str | Path = DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    raw_stac_directory: str | Path = DEFAULT_RAW_STAC_DIRECTORY,
    landsat_inventory_directory: str | Path = DEFAULT_LANDSAT_INVENTORY_DIRECTORY,
    formal_lock_path: str | Path = DEFAULT_FORMAL_LOCK_PATH,
    research_config_path: str | Path = DEFAULT_RESEARCH_CONFIG_PATH,
    sentinel_config_path: str | Path = DEFAULT_SENTINEL_CONFIG_PATH,
    predictor_base_path: str | Path = DEFAULT_PREDICTOR_BASE_PATH,
    predictor_base_provenance_path: str | Path = (
        DEFAULT_PREDICTOR_BASE_PROVENANCE_PATH
    ),
    static_audit_path: str | Path = DEFAULT_STATIC_AUDIT_PATH,
    development_control_directory: str | Path = (
        DEFAULT_DEVELOPMENT_CONTROL_DIRECTORY
    ),
    legacy_control_directory: str | Path = DEFAULT_LEGACY_CONTROL_DIRECTORY,
    output_path: str | Path = DEFAULT_AUDIT_PATH,
) -> dict[str, Any]:
    """Authenticate a completed C1 run and atomically publish one audit JSON."""

    root = _project_root().resolve()
    with FinalTestStateLock(root / DEFAULT_FINAL_TEST_STATE_LOCK_PATH):
        return _audit_final_test_sentinel_features_locked(
            sentinel_output_directory=sentinel_output_directory,
            sentinel_inventory_directory=sentinel_inventory_directory,
            raw_stac_directory=raw_stac_directory,
            landsat_inventory_directory=landsat_inventory_directory,
            formal_lock_path=formal_lock_path,
            research_config_path=research_config_path,
            sentinel_config_path=sentinel_config_path,
            predictor_base_path=predictor_base_path,
            predictor_base_provenance_path=predictor_base_provenance_path,
            static_audit_path=static_audit_path,
            development_control_directory=development_control_directory,
            legacy_control_directory=legacy_control_directory,
            output_path=output_path,
        )
