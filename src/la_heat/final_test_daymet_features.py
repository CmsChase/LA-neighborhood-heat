"""Compile target-blind Daymet predictors for the frozen 2025 test keys.

This stage is intentionally predictor-only.  It authenticates the formal model
lock, the blind Landsat key universe, the isolated final-test Daymet download
commit, and the exact fixed Daymet-cell weights used during development.  It
never opens a Landsat target/QA table, a fitted model, or a score.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.daymet_feature_stage import _build_feature_audit
from la_heat.daymet_grid import (
    DAYMET_DIRECT_DAP4_ROUTE,
    DAYMET_DOI_URL,
    DaymetGranule,
    DaymetNetCDFSpec,
    aggregate_daymet_cells_to_tract_daily,
    build_daymet_direct_subset_url,
    build_lagged_tract_daymet_features,
    read_daymet_netcdf_cells,
    validate_daymet_direct_subset_spec,
    validate_daymet_netcdf_grid_specs,
    validate_fixed_cell_weights,
)
from la_heat.final_test_daymet_grid import (
    GRANULE_INVENTORY_FILENAME,
    SUBSET_DOWNLOADS_FILENAME,
    WEATHER_REQUIREMENTS_FILENAME,
    FinalTestDaymetRequirements,
    derive_final_test_daymet_requirements,
    inspect_exact_final_test_daymet_netcdf,
)
from la_heat.final_test_daymet_grid import (
    PROVENANCE_FILENAME as GRID_PROVENANCE_FILENAME,
)
from la_heat.final_test_inventory import (
    FINAL_TEST_YEAR,
    KEY_UNIVERSE_FILENAME,
    authenticate_formal_model_lock,
)
from la_heat.final_test_inventory import (
    SUMMARY_FILENAME as LANDSAT_PROVENANCE_FILENAME,
)
from la_heat.phase2_registry import (
    DAYMET_FEATURE_COUNT,
    LOCKED_DAYMET_VARIABLES,
    daymet_feature_registry_rows,
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

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-daymet-features-v1-frozen-weights"
OUTPUT_FILENAME: Final = "daymet_features.parquet"
AUDIT_FILENAME: Final = "daymet_feature_audit.parquet"
PROVENANCE_FILENAME: Final = "DAYMET_FEATURES.json"
INTERNAL_PROVENANCE_FILENAME: Final = "DAYMET_FEATURES.json"
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/interim/final_test_2025/daymet_features")
DEFAULT_PROVENANCE_PATH: Final = Path(
    "manifests/final_test_2025/daymet_features/DAYMET_FEATURES.json"
)
EXPECTED_ROW_COUNT: Final = 25_208
EXPECTED_DATE_COUNT: Final = 23
EXPECTED_TRACT_COUNT: Final = 1_096
WINDOWS: Final = (1, 3, 7)
KEY_COLUMNS: Final = ("tract_geoid", "target_date")
FULL_KEY_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)
_PROCESSING_EXCLUSIVE_UPPER_YEAR: Final = FINAL_TEST_YEAR + 1
_DOWNLOAD_COLUMNS: Final = frozenset(
    {
        "concept_id",
        "variable",
        "year",
        "access_route",
        "subset_y_start",
        "subset_y_stop",
        "subset_x_start",
        "subset_x_stop",
        "path",
        "bytes",
        "sha256",
        "source_url",
        "retrieved_on",
        "credential_source",
    }
)
_FORBIDDEN_KEY_COLUMNS: Final = frozenset(
    {
        "lst",
        "thermal",
        "target_available",
        "target_usable",
        "valid_pixel_count",
        "relative_hotspot_top20",
        "y_true",
        "y_pred",
    }
)
_PIPELINE_FILES: Final = (
    "scripts/build_final_test_daymet_features.py",
    "src/la_heat/daymet_feature_stage.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/final_test_daymet_features.py",
    "src/la_heat/final_test_daymet_grid.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/phase2_registry.py",
    "src/la_heat/provenance.py",
    "src/la_heat/weather_daymet.py",
)


class FinalTestDaymetFeatureError(RuntimeError):
    """Raised when the blind final-test Daymet feature contract cannot be proved."""


@dataclass(frozen=True, slots=True)
class FinalTestDaymetCompilation:
    """In-memory final-test Daymet feature and audit products."""

    features: pd.DataFrame
    audit: pd.DataFrame
    tract_daily: pd.DataFrame


Inspector = Callable[..., DaymetNetCDFSpec]
CellReader = Callable[..., pd.DataFrame]
MarkerWriter = Callable[[dict[str, Any], Path], None]


def _is_semantic_string_series(series: pd.Series) -> bool:
    """Return whether a series contains only strings and missing values."""

    if isinstance(series.dtype, pd.StringDtype):
        return True
    if not pd.api.types.is_object_dtype(series.dtype):
        return False
    present = series.dropna()
    return bool(not present.empty and present.map(lambda value: isinstance(value, str)).all())


def _assert_parquet_round_trip(
    frozen: pd.DataFrame,
    source: pd.DataFrame,
    *,
    label: str,
) -> None:
    """Require an exact round trip except for equivalent pandas string dtypes."""

    checked_frozen = frozen.copy()
    checked_source = source.copy()
    if checked_frozen.columns.equals(checked_source.columns):
        canonical_string_dtype = pd.StringDtype(storage="python")
        for column in checked_source.columns:
            if _is_semantic_string_series(
                checked_source[column]
            ) and _is_semantic_string_series(checked_frozen[column]):
                checked_source[column] = checked_source[column].astype(
                    canonical_string_dtype
                )
                checked_frozen[column] = checked_frozen[column].astype(
                    canonical_string_dtype
                )
    try:
        pd.testing.assert_frame_equal(
            checked_frozen,
            checked_source,
            check_dtype=True,
            check_exact=True,
        )
    except AssertionError as error:
        raise FinalTestDaymetFeatureError(
            f"{label} Parquet round trip changed its schema, dtype, or values: {error}"
        ) from error


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestDaymetFeatureError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestDaymetFeatureError(f"{label} changed or is not a JSON object.")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestDaymetFeatureError(f"{label} canonical commit failed.")
    return recorded


def _record_path(record: Mapping[str, Any], *, root: Path, label: str) -> Path:
    value = record.get("path")
    if not isinstance(value, str) or not value:
        raise FinalTestDaymetFeatureError(f"{label} lacks a locked path.")
    return _resolve(root, value)


def _verify_file(
    path: Path,
    record: Mapping[str, Any] | object,
    *,
    label: str,
    require_path: bool = False,
) -> None:
    if not isinstance(record, Mapping):
        raise FinalTestDaymetFeatureError(f"{label} file lock is invalid.")
    if require_path and Path(str(record.get("path", ""))).resolve() != path.resolve():
        raise FinalTestDaymetFeatureError(f"{label} path lock failed.")
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    if sha256_file(path) != record.get("sha256"):
        raise FinalTestDaymetFeatureError(f"{label} SHA-256 lock failed.")
    if "bytes" in record and path.stat().st_size != record.get("bytes"):
        raise FinalTestDaymetFeatureError(f"{label} byte-size lock failed.")


def _read_linked_json(
    record: Mapping[str, Any] | object,
    *,
    root: Path,
    label: str,
) -> tuple[Path, dict[str, Any], str]:
    if not isinstance(record, Mapping):
        raise FinalTestDaymetFeatureError(f"{label} link is invalid.")
    path = _record_path(record, root=root, label=label)
    _verify_file(path, record, label=label)
    payload = _read_json(path, label=label)
    commit = _verify_commit(payload, label=label)
    if "commit_sha256" in record and record.get("commit_sha256") != commit:
        raise FinalTestDaymetFeatureError(f"{label} commit link failed.")
    return path, payload, commit


def _validate_blind_inventory(
    inventory: dict[str, Any],
    *,
    formal_lock: dict[str, Any],
    formal_sha256: str,
    inventory_directory: Path,
) -> tuple[pd.DataFrame, Path, Mapping[str, Any], str]:
    commit = _verify_commit(inventory, label="Final-test Landsat inventory")
    formal_record = inventory.get("formal_model_lock")
    if (
        inventory.get("state") != "target_blind_inventory_frozen"
        or inventory.get("final_test_year") != FINAL_TEST_YEAR
        or inventory.get("target_blind") is not True
        or inventory.get("target_assets_opened") is not False
        or inventory.get("target_or_qa_values_read") is not False
        or inventory.get("labels_created") is not False
        or inventory.get("models_loaded") is not False
        or inventory.get("model_scores_read") is not False
        or inventory.get("one_time_evaluation_consumed") is not False
        or not isinstance(formal_record, Mapping)
        or formal_record.get("sha256") != formal_sha256
        or formal_record.get("commit_sha256") != formal_lock.get("commit_sha256")
    ):
        raise FinalTestDaymetFeatureError(
            "Landsat key inventory is not the untouched blind 2025 commitment."
        )
    outputs = inventory.get("output_files")
    if not isinstance(outputs, Mapping):
        raise FinalTestDaymetFeatureError("Landsat inventory lacks output locks.")
    key_record = outputs.get(KEY_UNIVERSE_FILENAME)
    key_path = inventory_directory / KEY_UNIVERSE_FILENAME
    _verify_file(key_path, key_record, label="Final-test key universe", require_path=True)
    before = sha256_file(key_path)
    keys = pd.read_parquet(key_path)
    if sha256_file(key_path) != before:
        raise FinalTestDaymetFeatureError("Final-test key universe changed while read.")
    _validate_final_keys(keys)
    semantic = canonical_frame_sha256(keys, sort_by=["target_date", "tract_geoid"])
    if (
        len(keys) != inventory.get("key_count")
        or keys["target_date"].nunique() != inventory.get("primary_overpass_count")
        or keys["tract_geoid"].nunique() != inventory.get("tract_count")
        or semantic != inventory.get("semantic_hashes", {}).get("key_universe")
    ):
        raise FinalTestDaymetFeatureError(
            "Final-test key universe disagrees with its inventory commitment."
        )
    assert isinstance(key_record, Mapping)
    return keys, key_path, key_record, commit


def _validate_final_keys(keys: pd.DataFrame, *, production_shape: bool = False) -> None:
    if keys.columns.tolist() != list(FULL_KEY_COLUMNS):
        raise FinalTestDaymetFeatureError("Final-test Daymet key schema drifted.")
    forbidden = {
        column
        for column in keys
        if column != "target_date"
        and any(token in column.casefold() for token in _FORBIDDEN_KEY_COLUMNS)
    }
    if forbidden:
        raise FinalTestDaymetFeatureError(
            f"Target/thermal columns are forbidden in Daymet keys: {sorted(forbidden)}"
        )
    checked = keys.copy()
    checked["tract_geoid"] = checked["tract_geoid"].astype("string")
    dates = pd.to_datetime(checked["target_date"], errors="raise")
    string_columns = ("tract_geoid", "overpass_id", "platform", "spatial_block")
    if any(checked[column].isna().any() for column in string_columns):
        raise FinalTestDaymetFeatureError("Final-test key metadata contains missing strings.")
    if any(not checked[column].astype(str).str.strip().ne("").all() for column in string_columns):
        raise FinalTestDaymetFeatureError("Final-test key metadata contains blank strings.")
    if (
        checked.empty
        or dates.dt.tz is not None
        or not dates.dt.normalize().equals(dates)
        or not dates.dt.year.eq(FINAL_TEST_YEAR).all()
        or checked.duplicated(list(KEY_COLUMNS)).any()
    ):
        raise FinalTestDaymetFeatureError(
            "Daymet keys must be unique timezone-naive civil dates in exact 2025."
        )
    checked["target_date"] = dates
    tract_sets = checked.groupby("target_date", sort=True)["tract_geoid"].agg(frozenset)
    if tract_sets.empty or tract_sets.nunique() != 1:
        raise FinalTestDaymetFeatureError(
            "Every final-test date must use the same frozen tract support."
        )
    if not checked.groupby("target_date")["overpass_id"].nunique().eq(1).all():
        raise FinalTestDaymetFeatureError("Each final-test date must map to one overpass.")
    if not checked.groupby("tract_geoid")["spatial_block"].nunique().eq(1).all():
        raise FinalTestDaymetFeatureError("Each tract must map to one frozen spatial block.")
    expected = len(tract_sets.iloc[0]) * len(tract_sets)
    if len(checked) != expected:
        raise FinalTestDaymetFeatureError(
            "Final-test Daymet keys are not a complete tract-by-date product."
        )
    if production_shape and (
        len(checked) != EXPECTED_ROW_COUNT
        or len(tract_sets) != EXPECTED_DATE_COUNT
        or len(tract_sets.iloc[0]) != EXPECTED_TRACT_COUNT
    ):
        raise FinalTestDaymetFeatureError("Production Daymet keys must remain 25,208 = 1,096 x 23.")


def verify_locked_subset_files(
    downloads: pd.DataFrame,
    *,
    raw_subset_directory: Path,
    variables: Sequence[str] = LOCKED_DAYMET_VARIABLES,
    source_years: Sequence[int] = (FINAL_TEST_YEAR,),
) -> list[dict[str, Any]]:
    """Verify the exact variable-year files and return immutable file records."""

    if set(downloads.columns) != _DOWNLOAD_COLUMNS:
        raise FinalTestDaymetFeatureError("Final-test Daymet download schema drifted.")
    expected = {(str(variable), int(year)) for year in source_years for variable in variables}
    observed = {(str(row.variable), int(row.year)) for row in downloads.itertuples(index=False)}
    if (
        downloads.duplicated(["variable", "year"]).any()
        or observed != expected
        or len(downloads) != len(expected)
    ):
        raise FinalTestDaymetFeatureError(
            "Final-test Daymet downloads are not the exact six required 2025 subsets."
        )
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in downloads.sort_values(["year", "variable"], kind="stable").itertuples(index=False):
        variable = str(row.variable)
        year = int(row.year)
        path = Path(str(row.path)).resolve()
        expected_name = f"daymet_v4r1_daily_na_{variable}_{year}_la_subset.nc"
        if path.parent != raw_subset_directory.resolve() or path.name != expected_name:
            raise FinalTestDaymetFeatureError(
                "Final-test Daymet subset points outside its isolated raw directory."
            )
        if not path.is_file():
            missing.append(str(path))
            continue
        if path.stat().st_size != int(row.bytes):
            raise FinalTestDaymetFeatureError(f"Daymet subset byte-size lock failed: {path}")
        digest = sha256_file(path)
        if digest != str(row.sha256):
            raise FinalTestDaymetFeatureError(f"Daymet subset SHA-256 lock failed: {path}")
        records.append(
            {
                "path": str(path),
                "variable": variable,
                "year": year,
                "bytes": int(row.bytes),
                "sha256": digest,
            }
        )
    if missing:
        raise FileNotFoundError("Missing frozen 2025 Daymet subset file(s): " + ", ".join(missing))
    return records


def _validate_grid_and_subsets(
    grid: dict[str, Any],
    *,
    grid_path: Path,
    manifest_directory: Path,
    formal_path: Path,
    formal_lock: dict[str, Any],
    formal_sha256: str,
    inventory_path: Path,
    inventory_sha256: str,
    inventory_commit: str,
    key_path: Path,
    key_record: Mapping[str, Any],
    keys: pd.DataFrame,
    inspector: Inspector,
) -> tuple[pd.DataFrame, tuple[DaymetNetCDFSpec, ...], dict[str, Any]]:
    commit = _verify_commit(grid, label="Final-test Daymet grid")
    completed = int(grid.get("completed_subset_count", 0))
    expected_count = int(grid.get("expected_subset_count", 6))
    if grid.get("state") != "subsets_complete":
        raise FileNotFoundError(
            "Final-test Daymet subsets are not complete: "
            f"{completed}/{expected_count}; run the isolated downloader first."
        )
    formal_record = grid.get("formal_model_lock")
    landsat_record = grid.get("landsat_inventory")
    grid_key = grid.get("key_universe")
    if (
        grid.get("final_test_year") != FINAL_TEST_YEAR
        or grid.get("final_test_unlocked") is not False
        or grid.get("target_blind") is not True
        or grid.get("target_or_qa_tables_read") != []
        or grid.get("target_values_read") is not False
        or grid.get("models_loaded") is not False
        or grid.get("model_scores_read") is not False
        or grid.get("one_time_evaluation_consumed") is not False
        or grid.get("dynamic_observed_predictors_end_by") != "target_day_minus_1"
        or set(grid.get("variables", [])) != set(LOCKED_DAYMET_VARIABLES)
        or grid.get("source_years") != [FINAL_TEST_YEAR]
        or completed != expected_count
        or expected_count != len(LOCKED_DAYMET_VARIABLES)
        or not isinstance(formal_record, Mapping)
        or Path(str(formal_record.get("path", ""))).resolve() != formal_path.resolve()
        or formal_record.get("sha256") != formal_sha256
        or formal_record.get("commit_sha256") != formal_lock.get("commit_sha256")
        or not isinstance(landsat_record, Mapping)
        or Path(str(landsat_record.get("path", ""))).resolve() != inventory_path.resolve()
        or landsat_record.get("sha256") != inventory_sha256
        or landsat_record.get("commit_sha256") != inventory_commit
        or not isinstance(grid_key, Mapping)
        or Path(str(grid_key.get("path", ""))).resolve() != key_path.resolve()
        or grid_key.get("sha256") != key_record.get("sha256")
        or grid_key.get("bytes") != key_record.get("bytes")
    ):
        raise FinalTestDaymetFeatureError(
            "Final-test Daymet grid is not bound to the frozen blind inputs."
        )
    outputs = grid.get("output_files")
    if not isinstance(outputs, Mapping):
        raise FinalTestDaymetFeatureError("Final-test Daymet grid lacks output locks.")
    locked_frames: dict[str, pd.DataFrame] = {}
    locked_manifest_files: list[dict[str, Any]] = []
    for name in (
        GRANULE_INVENTORY_FILENAME,
        WEATHER_REQUIREMENTS_FILENAME,
        SUBSET_DOWNLOADS_FILENAME,
    ):
        path = manifest_directory / name
        record = outputs.get(name)
        _verify_file(path, record, label=f"Daymet {name}", require_path=True)
        before = sha256_file(path)
        locked_frames[name] = pd.read_csv(path)
        if sha256_file(path) != before:
            raise FinalTestDaymetFeatureError(f"Daymet {name} changed while read.")
        assert isinstance(record, Mapping)
        locked_manifest_files.append(
            {
                "path": str(path),
                "sha256": str(record["sha256"]),
                "bytes": int(record["bytes"]),
            }
        )

    requirements, membership = derive_final_test_daymet_requirements(keys)
    frozen_membership = locked_frames[WEATHER_REQUIREMENTS_FILENAME]
    if (
        canonical_frame_sha256(frozen_membership, sort_by=["target_date", "lag_days"])
        != requirements.membership_semantic_sha256
        or canonical_frame_sha256(membership, sort_by=["target_date", "lag_days"])
        != requirements.membership_semantic_sha256
        or grid.get("weather_requirement_semantic_sha256")
        != requirements.membership_semantic_sha256
    ):
        raise FinalTestDaymetFeatureError("Frozen d-7 through d-1 requirements drifted.")

    granules = locked_frames[GRANULE_INVENTORY_FILENAME]
    downloads = locked_frames[SUBSET_DOWNLOADS_FILENAME]
    if canonical_frame_sha256(downloads, sort_by=["year", "variable"]) != grid.get(
        "download_manifest_semantic_sha256"
    ):
        raise FinalTestDaymetFeatureError("Daymet subset manifest semantic lock failed.")
    raw_directory = Path(str(grid.get("raw_subset_directory", ""))).resolve()
    file_records = verify_locked_subset_files(
        downloads,
        raw_subset_directory=raw_directory,
        source_years=requirements.source_years,
    )
    required_granule_columns = {
        "concept_id",
        "title",
        "variable",
        "year",
        "size_mb",
        "https_url",
        "opendap_url",
        "updated_at",
    }
    if (
        not required_granule_columns.issubset(granules.columns)
        or granules.duplicated(["variable", "year"]).any()
    ):
        raise FinalTestDaymetFeatureError("Final-test Daymet granule inventory drifted.")
    joined = downloads.merge(
        granules.loc[:, sorted(required_granule_columns)],
        on=["concept_id", "variable", "year"],
        how="left",
        validate="one_to_one",
    )
    if len(joined) != expected_count or joined["opendap_url"].isna().any():
        raise FinalTestDaymetFeatureError(
            "Daymet subset manifest does not match its granule inventory."
        )
    y_indices = tuple(int(value) for value in grid["direct_subset_y_indices"])
    x_indices = tuple(int(value) for value in grid["direct_subset_x_indices"])
    bbox = tuple(float(value) for value in grid["bbox_wgs84"])
    specs: list[DaymetNetCDFSpec] = []
    records: list[dict[str, object]] = []
    by_file = {(item["variable"], item["year"]): item for item in file_records}
    for row in joined.sort_values(["year", "variable"], kind="stable").itertuples(index=False):
        granule = DaymetGranule(
            concept_id=str(row.concept_id),
            title=str(row.title),
            variable=str(row.variable),
            year=int(row.year),
            size_mb=float(row.size_mb),
            https_url=str(row.https_url),
            opendap_url=str(row.opendap_url),
            updated_at=None if pd.isna(row.updated_at) else str(row.updated_at),
        )
        expected_url = build_daymet_direct_subset_url(
            granule, y_indices=y_indices, x_indices=x_indices
        )
        if (
            row.access_route != DAYMET_DIRECT_DAP4_ROUTE
            or str(row.source_url) != expected_url
            or int(row.subset_y_start) != y_indices[0]
            or int(row.subset_y_stop) != y_indices[1]
            or int(row.subset_x_start) != x_indices[0]
            or int(row.subset_x_stop) != x_indices[1]
        ):
            raise FinalTestDaymetFeatureError("Daymet constrained-source lock failed.")
        file_record = by_file[(str(row.variable), int(row.year))]
        path = Path(str(file_record["path"]))
        spec = inspector(
            path,
            variable=str(row.variable),
            year=int(row.year),
            requirements=requirements,
        )
        specs.append(
            validate_daymet_direct_subset_spec(
                spec,
                y_indices=y_indices,
                x_indices=x_indices,
                bbox_wgs84=bbox,
            )
        )
        records.append({"path": path, "variable": str(row.variable), "year": int(row.year)})
    reference = validate_daymet_netcdf_grid_specs(specs)
    if list(reference.shape) != grid.get("subset_grid_shape"):
        raise FinalTestDaymetFeatureError("Daymet subset grid shape lock failed.")
    return (
        pd.DataFrame(records),
        tuple(specs),
        {
            "path": str(grid_path),
            "sha256": sha256_file(grid_path),
            "commit_sha256": commit,
            "subset_manifest_path": str(manifest_directory / SUBSET_DOWNLOADS_FILENAME),
            "subset_manifest_sha256": sha256_file(manifest_directory / SUBSET_DOWNLOADS_FILENAME),
            "locked_manifest_files": locked_manifest_files,
            "subset_files": file_records,
            "requirements": {
                "source_start": requirements.weather_dates[0].isoformat(),
                "source_end": requirements.weather_dates[-1].isoformat(),
                "source_years": list(requirements.source_years),
                "semantic_sha256": requirements.membership_semantic_sha256,
            },
        },
    )


def _authenticate_frozen_weights(
    formal_lock: dict[str, Any],
    *,
    inventory: dict[str, Any],
    root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    input_locks = formal_lock.get("input_locks")
    if not isinstance(input_locks, Mapping):
        raise FinalTestDaymetFeatureError("Formal model lock lacks input links.")
    model_path, model_provenance, model_commit = _read_linked_json(
        input_locks.get("model_dataset_provenance"),
        root=root,
        label="Locked model-dataset provenance",
    )
    phase2_path, phase2, phase2_commit = _read_linked_json(
        model_provenance.get("inputs", {}).get("phase2_provenance"),
        root=root,
        label="Locked phase-2 provenance",
    )
    readiness_path, readiness, readiness_commit = _read_linked_json(
        phase2.get("inputs", {}).get("phase2_readiness"),
        root=root,
        label="Locked phase-2 readiness",
    )
    if (
        phase2.get("state") != "complete"
        or phase2.get("target_blind") is not True
        or phase2.get("target_or_qa_tables_read") != []
        or phase2.get("target_values_read") is not False
        or phase2.get("contains_final_test_year") is not False
        or readiness.get("state") != "ready_for_feature_assembly"
        or readiness.get("target_blind") is not True
        or readiness.get("target_or_qa_tables_read") != []
        or readiness.get("target_values_read") is not False
        or readiness.get("contains_final_test_year") is not False
    ):
        raise FinalTestDaymetFeatureError(
            "Development feature provenance is not a blind pre-2025 commitment."
        )
    readiness_inputs = readiness.get("inputs")
    if not isinstance(readiness_inputs, Mapping):
        raise FinalTestDaymetFeatureError("Phase-2 readiness lacks fixed-input locks.")
    weights_record = readiness_inputs.get("daymet_fixed_cell_weights")
    if not isinstance(weights_record, Mapping):
        raise FinalTestDaymetFeatureError("Phase-2 readiness lacks Daymet weights.")
    weights_path = _record_path(weights_record, root=root, label="Fixed Daymet weights")
    _verify_file(weights_path, weights_record, label="Fixed Daymet weights")
    daymet_path, daymet_provenance, daymet_commit = _read_linked_json(
        readiness_inputs.get("daymet_features_provenance"),
        root=root,
        label="Development Daymet feature provenance",
    )
    daymet_output = daymet_provenance.get("output_files", {}).get(weights_path.name)
    support = daymet_provenance.get("target_support_locks")
    frozen_support = inventory.get("frozen_support")
    if (
        daymet_provenance.get("status") != "complete"
        or daymet_provenance.get("target_blind") is not True
        or daymet_provenance.get("target_or_qa_tables_read") != []
        or daymet_provenance.get("final_test_unlocked") is not False
        or not isinstance(daymet_output, Mapping)
        or daymet_output.get("sha256") != weights_record.get("sha256")
        or daymet_output.get("rows") != weights_record.get("rows")
        or not isinstance(support, Mapping)
        or not isinstance(frozen_support, Mapping)
        or support.get("tract_manifest_sha256") != frozen_support.get("primary_tract_commit_sha256")
        or support.get("tract_manifest_file_sha256") != frozen_support.get("primary_tract_sha256")
    ):
        raise FinalTestDaymetFeatureError(
            "Development Daymet weights do not match the frozen 2025 tract support."
        )
    before = sha256_file(weights_path)
    weights = validate_fixed_cell_weights(pd.read_parquet(weights_path))
    if sha256_file(weights_path) != before or len(weights) != weights_record.get("rows"):
        raise FinalTestDaymetFeatureError("Fixed Daymet weights changed while read.")
    locks = {
        "formal_chain": {
            "model_dataset_provenance": {
                "path": str(model_path),
                "sha256": sha256_file(model_path),
                "commit_sha256": model_commit,
            },
            "phase2_provenance": {
                "path": str(phase2_path),
                "sha256": sha256_file(phase2_path),
                "commit_sha256": phase2_commit,
            },
            "phase2_readiness": {
                "path": str(readiness_path),
                "sha256": sha256_file(readiness_path),
                "commit_sha256": readiness_commit,
            },
        },
        "development_daymet_provenance": {
            "path": str(daymet_path),
            "sha256": sha256_file(daymet_path),
            "commit_sha256": daymet_commit,
        },
        "fixed_cell_weights": {
            "path": str(weights_path),
            "sha256": before,
            "bytes": weights_path.stat().st_size,
            "rows": len(weights),
        },
        "target_support_locks": dict(support),
    }
    return weights, locks


def _validate_specs(
    specs: Sequence[DaymetNetCDFSpec],
    records: pd.DataFrame,
    requirements: FinalTestDaymetRequirements,
) -> dict[tuple[int, str], DaymetNetCDFSpec]:
    expected = {(int(row.year), str(row.variable)) for row in records.itertuples(index=False)}
    observed: dict[tuple[int, str], DaymetNetCDFSpec] = {}
    for spec in specs:
        key = (int(spec.year), str(spec.variable))
        if key in observed:
            raise FinalTestDaymetFeatureError(f"Duplicate Daymet grid spec: {key}")
        observed[key] = spec
    if set(observed) != expected or {year for year, _ in observed} != set(
        requirements.source_years
    ):
        raise FinalTestDaymetFeatureError("Daymet grid specs do not match exact subsets.")
    validate_daymet_netcdf_grid_specs(tuple(observed[key] for key in sorted(observed)))
    return observed


def compile_final_test_daymet_feature_tables(
    subset_records: pd.DataFrame,
    key_universe: pd.DataFrame,
    fixed_weights: pd.DataFrame,
    *,
    specs: Sequence[DaymetNetCDFSpec],
    cell_reader: CellReader = read_daymet_netcdf_cells,
) -> FinalTestDaymetCompilation:
    """Compile exact 2025 d-1/d-3/d-7 features with frozen development weights."""

    _validate_final_keys(key_universe)
    requirements, _ = derive_final_test_daymet_requirements(key_universe)
    required_record_columns = {"path", "variable", "year"}
    if set(subset_records.columns) != required_record_columns:
        raise FinalTestDaymetFeatureError("Daymet subset-record schema drifted.")
    records = subset_records.copy()
    records["variable"] = records["variable"].astype(str)
    records["year"] = pd.to_numeric(records["year"], errors="raise").astype(int)
    expected_pairs = {
        (year, variable)
        for year in requirements.source_years
        for variable in LOCKED_DAYMET_VARIABLES
    }
    observed_pairs = {(int(row.year), str(row.variable)) for row in records.itertuples(index=False)}
    if records.duplicated(["year", "variable"]).any() or observed_pairs != expected_pairs:
        raise FinalTestDaymetFeatureError(
            "Daymet subset records do not exactly cover required variable-years."
        )
    checked_weights = validate_fixed_cell_weights(fixed_weights)
    key_geoids = set(key_universe["tract_geoid"].astype(str))
    if set(checked_weights["tract_geoid"].astype(str)) != key_geoids:
        raise FinalTestDaymetFeatureError(
            "Frozen Daymet weights and final-test tract universes differ."
        )
    inspected = _validate_specs(specs, records, requirements)
    cells = checked_weights.loc[:, ["daymet_cell_id", "daymet_row", "daymet_col"]].drop_duplicates(
        "daymet_cell_id"
    )
    target_dates = tuple(pd.Timestamp(value) for value in requirements.target_dates)
    source_start = min(target_dates) - pd.Timedelta(days=max(WINDOWS))
    source_end = max(target_dates) - pd.Timedelta(days=1)

    annual: list[pd.DataFrame] = []
    for year, group in records.groupby("year", sort=True):
        decoded: pd.DataFrame | None = None
        for row in group.sort_values("variable", kind="stable").itertuples(index=False):
            spec = inspected[(int(year), str(row.variable))]
            variable_frame = cell_reader(spec, cells=cells)
            value_columns = [
                column for column in variable_frame if column not in {"daymet_cell_id", "date"}
            ]
            if len(value_columns) != 1:
                raise FinalTestDaymetFeatureError(
                    f"Decoded Daymet {row.variable} must contain one value column."
                )
            if decoded is None:
                decoded = variable_frame.copy()
            else:
                before = len(decoded)
                decoded = decoded.merge(
                    variable_frame,
                    on=["daymet_cell_id", "date"],
                    how="inner",
                    sort=False,
                    validate="one_to_one",
                )
                if len(decoded) != before or len(decoded) != len(variable_frame):
                    raise FinalTestDaymetFeatureError(
                        f"Daymet variables disagree on cell-date keys for {year}."
                    )
        assert decoded is not None
        decoded["date"] = pd.to_datetime(decoded["date"], errors="raise")
        decoded = decoded.loc[
            decoded["date"].between(source_start, source_end, inclusive="both")
        ].reset_index(drop=True)
        if decoded.empty:
            raise FinalTestDaymetFeatureError(
                "Daymet subsets contain no required d-7 through d-1 source dates."
            )
        annual.append(
            aggregate_daymet_cells_to_tract_daily(
                decoded,
                checked_weights,
                final_test_year=_PROCESSING_EXCLUSIVE_UPPER_YEAR,
            )
        )
    tract_daily = pd.concat(annual, ignore_index=True).sort_values(
        ["tract_geoid", "date"], kind="stable"
    )
    if tract_daily.duplicated(["tract_geoid", "date"]).any():
        raise FinalTestDaymetFeatureError("Compiled Daymet tract-day keys duplicate.")
    lagged = build_lagged_tract_daymet_features(
        tract_daily,
        target_dates=target_dates,
        windows=WINDOWS,
        final_test_year=_PROCESSING_EXCLUSIVE_UPPER_YEAR,
    )
    feature_names = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))
    if len(feature_names) != DAYMET_FEATURE_COUNT or not set(feature_names).issubset(
        lagged.columns
    ):
        raise FinalTestDaymetFeatureError("Daymet compiler violated the 21-feature lock.")
    keys = key_universe.loc[:, list(KEY_COLUMNS)].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    keys["target_date"] = pd.to_datetime(keys["target_date"]).astype("datetime64[us]")
    features = keys.merge(
        lagged.loc[:, [*KEY_COLUMNS, *feature_names]],
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    )
    features = features.sort_values(["target_date", "tract_geoid"], kind="stable").reset_index(
        drop=True
    )
    audit = _build_feature_audit(
        lagged,
        tract_daily,
        checked_weights,
        target_dates=target_dates,
        windows=WINDOWS,
    )
    audit = keys.merge(
        audit,
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    ).sort_values(["target_date", "tract_geoid"], kind="stable")
    audit = audit.reset_index(drop=True)
    numeric = features.loc[:, feature_names].to_numpy(dtype=float)
    if np.isinf(numeric).any():
        raise FinalTestDaymetFeatureError("Final-test Daymet features contain infinity.")
    if len(features) != len(keys) or len(audit) != len(keys):
        raise FinalTestDaymetFeatureError("Daymet compilation changed the frozen key count.")
    output_key_hash = canonical_frame_sha256(
        features.loc[:, list(KEY_COLUMNS)], sort_by=["target_date", "tract_geoid"]
    )
    input_key_hash = canonical_frame_sha256(keys, sort_by=["target_date", "tract_geoid"])
    if output_key_hash != input_key_hash:
        raise FinalTestDaymetFeatureError("Daymet compilation changed frozen keys.")
    target_dates_series = pd.to_datetime(audit["target_date"])
    for window in WINDOWS:
        suffix = f"prev_{window}d"
        if (
            not pd.to_datetime(audit[f"daymet_source_end_date_{suffix}"])
            .eq(target_dates_series - pd.Timedelta(days=1))
            .all()
        ):
            raise FinalTestDaymetFeatureError("Daymet source window reaches target day.")
        if (
            not pd.to_datetime(audit[f"daymet_source_start_date_{suffix}"])
            .eq(target_dates_series - pd.Timedelta(days=window))
            .all()
        ):
            raise FinalTestDaymetFeatureError("Daymet source window start drifted.")
    return FinalTestDaymetCompilation(features, audit, tract_daily.reset_index(drop=True))


def _locked_daymet_feature_names(formal_lock: Mapping[str, Any]) -> tuple[str, ...]:
    expected = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))
    models = formal_lock.get("models")
    if not isinstance(models, Mapping) or set(models) != {"B1", "M2"}:
        raise FinalTestDaymetFeatureError("Formal lock lacks exact B1/M2 models.")
    for model_id in ("B1", "M2"):
        record = models[model_id]
        names = record.get("feature_names") if isinstance(record, Mapping) else None
        if not isinstance(names, list):
            raise FinalTestDaymetFeatureError(f"{model_id} feature lock is invalid.")
        daymet_names = tuple(name for name in names if str(name).startswith("daymet_"))
        if daymet_names != expected:
            raise FinalTestDaymetFeatureError(
                f"{model_id} Daymet feature order disagrees with the 21-feature registry."
            )
    return expected


def _verify_inputs_unchanged(records: Sequence[Mapping[str, Any]]) -> None:
    if not records:
        raise FinalTestDaymetFeatureError("Immutable input snapshot cannot be empty.")
    observed: set[Path] = set()
    for record in records:
        path = Path(str(record.get("path", ""))).resolve()
        if path in observed:
            raise FinalTestDaymetFeatureError(f"Duplicate immutable input lock: {path}")
        observed.add(path)
        _verify_file(path, record, label=f"Frozen input {path}")


def _callable_identity(value: Callable[..., object]) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise FinalTestDaymetFeatureError("Runtime adapter lacks a stable identity.")
    return f"{module}.{qualname}"


def _request_payload(
    *,
    formal_path: Path,
    inventory_directory: Path,
    manifest_directory: Path,
    output: Path,
    marker: Path,
    inspector: Inspector,
    cell_reader: CellReader,
) -> dict[str, Any]:
    """Canonicalize every CLI-selectable or derived publication path/parameter."""

    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "final_test_year": FINAL_TEST_YEAR,
        "window_days": list(WINDOWS),
        "formal_model_lock_path": str(formal_path),
        "landsat_inventory_directory": str(inventory_directory),
        "landsat_inventory_path": str(inventory_directory / LANDSAT_PROVENANCE_FILENAME),
        "key_universe_path": str(inventory_directory / KEY_UNIVERSE_FILENAME),
        "daymet_manifest_directory": str(manifest_directory),
        "daymet_grid_path": str(manifest_directory / GRID_PROVENANCE_FILENAME),
        "daymet_granule_inventory_path": str(manifest_directory / GRANULE_INVENTORY_FILENAME),
        "daymet_weather_requirements_path": str(manifest_directory / WEATHER_REQUIREMENTS_FILENAME),
        "daymet_subset_manifest_path": str(manifest_directory / SUBSET_DOWNLOADS_FILENAME),
        "output_directory": str(output),
        "feature_output_path": str(output / OUTPUT_FILENAME),
        "audit_output_path": str(output / AUDIT_FILENAME),
        "internal_provenance_path": str(output / INTERNAL_PROVENANCE_FILENAME),
        "external_provenance_path": str(marker),
        "inspector": _callable_identity(inspector),
        "cell_reader": _callable_identity(cell_reader),
    }


def _current_pipeline(root: Path) -> tuple[str, dict[str, Any]]:
    return code_runtime_fingerprint(
        project_root=root,
        relative_paths=_PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )


def _verify_publish_snapshot(
    records: Sequence[Mapping[str, Any]],
    *,
    pipeline_sha256: str,
    pipeline_fingerprint: Mapping[str, Any],
    root: Path,
) -> None:
    """Recheck every byte lock and the executable pipeline immediately before use."""

    _verify_inputs_unchanged(records)
    current_sha256, current_fingerprint = _current_pipeline(root)
    if current_sha256 != pipeline_sha256 or dict(current_fingerprint) != dict(pipeline_fingerprint):
        raise FinalTestDaymetFeatureError(
            "Daymet feature pipeline changed after the publication snapshot."
        )


def _authenticate_existing(
    path: Path,
    *,
    expected_request: Mapping[str, Any],
    root: Path,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path, label="Final-test Daymet feature provenance")
    _verify_commit(payload, label="Final-test Daymet feature provenance")
    request = payload.get("request")
    if (
        payload.get("state") != "complete_target_blind"
        or payload.get("final_test_year") != FINAL_TEST_YEAR
        or payload.get("target_blind") is not True
        or payload.get("target_or_qa_tables_read") != []
        or payload.get("target_values_read") is not False
        or payload.get("models_loaded") is not False
        or payload.get("model_scores_read") is not False
        or payload.get("one_time_evaluation_consumed") is not False
        or payload.get("publication_protocol") != "staged_directory_atomic_replace_v1"
        or not isinstance(request, Mapping)
        or dict(request) != dict(expected_request)
        or payload.get("request_sha256") != canonical_sha256(expected_request)
    ):
        raise FinalTestDaymetFeatureError(
            "Existing Daymet feature commit is unsafe or belongs to another request."
        )
    outputs = payload.get("output_files")
    expected_outputs = {
        OUTPUT_FILENAME: Path(str(expected_request["feature_output_path"])),
        AUDIT_FILENAME: Path(str(expected_request["audit_output_path"])),
    }
    if not isinstance(outputs, Mapping) or set(outputs) != set(expected_outputs):
        raise FinalTestDaymetFeatureError("Existing Daymet feature commit lacks outputs.")
    for name, expected_path in expected_outputs.items():
        record = outputs.get(name)
        if not isinstance(record, Mapping):
            raise FinalTestDaymetFeatureError(f"Existing Daymet output {name} is unlocked.")
        if Path(str(record.get("path", ""))).resolve() != expected_path.resolve():
            raise FinalTestDaymetFeatureError(
                f"Existing Daymet output {name} belongs to another request."
            )
        _verify_file(
            expected_path,
            record,
            label=f"Existing Daymet output {name}",
            require_path=True,
        )
    immutable_inputs = payload.get("immutable_input_files")
    if not isinstance(immutable_inputs, list) or not all(
        isinstance(record, Mapping) for record in immutable_inputs
    ):
        raise FinalTestDaymetFeatureError("Existing Daymet input locks are invalid.")
    pipeline_sha256 = payload.get("pipeline_sha256")
    pipeline_fingerprint = payload.get("pipeline_fingerprint")
    if not isinstance(pipeline_sha256, str) or not isinstance(pipeline_fingerprint, Mapping):
        raise FinalTestDaymetFeatureError("Existing Daymet pipeline lock is invalid.")
    _verify_publish_snapshot(
        immutable_inputs,
        pipeline_sha256=pipeline_sha256,
        pipeline_fingerprint=pipeline_fingerprint,
        root=root,
    )
    internal_path = Path(str(expected_request["internal_provenance_path"]))
    if path.resolve() != internal_path.resolve():
        internal_payload = _read_json(internal_path, label="Internal Daymet recovery provenance")
        if internal_payload != payload:
            raise FinalTestDaymetFeatureError(
                "External and internal Daymet provenance commits disagree."
            )
    return payload


def _publish_staged_output(
    staging: Path,
    output: Path,
    payload: dict[str, Any],
    marker: Path,
    *,
    marker_writer: MarkerWriter = atomic_json,
) -> None:
    """Atomically publish the complete directory, then mirror its recovery marker."""

    if (
        not staging.is_dir()
        or staging.parent.resolve() != output.parent.resolve()
        or not staging.name.startswith(f".{output.name}.staging-")
        or not (staging / INTERNAL_PROVENANCE_FILENAME).is_file()
    ):
        raise FinalTestDaymetFeatureError("Daymet staging directory is not owned or complete.")
    internal = _read_json(
        staging / INTERNAL_PROVENANCE_FILENAME,
        label="Staged Daymet recovery provenance",
    )
    _verify_commit(internal, label="Staged Daymet recovery provenance")
    if internal != payload:
        raise FinalTestDaymetFeatureError(
            "Staged Daymet recovery provenance changed before publication."
        )
    request = payload.get("request")
    outputs = payload.get("output_files")
    if (
        not isinstance(request, Mapping)
        or Path(str(request.get("output_directory", ""))).resolve() != output.resolve()
        or not isinstance(outputs, Mapping)
        or set(outputs) != {OUTPUT_FILENAME, AUDIT_FILENAME}
    ):
        raise FinalTestDaymetFeatureError("Staged Daymet publication bundle is invalid.")
    for name in (OUTPUT_FILENAME, AUDIT_FILENAME):
        record = outputs[name]
        if (
            not isinstance(record, Mapping)
            or Path(str(record.get("path", ""))).resolve() != (output / name).resolve()
        ):
            raise FinalTestDaymetFeatureError(f"Staged Daymet output path lock failed: {name}")
        _verify_file(
            staging / name,
            record,
            label=f"Staged Daymet output {name}",
        )
    if output.exists():
        raise FinalTestDaymetFeatureError("Final Daymet output path already exists.")
    staging.replace(output)
    marker_writer(payload, marker)


def _recover_published_output(
    output: Path,
    marker: Path,
    *,
    expected_request: Mapping[str, Any],
    root: Path,
    marker_writer: MarkerWriter = atomic_json,
) -> dict[str, Any]:
    """Recover a complete atomic directory published just before marker failure."""

    if not output.is_dir():
        raise FinalTestDaymetFeatureError(
            "Uncommitted final-test Daymet output exists and is not recoverable."
        )
    internal = output / INTERNAL_PROVENANCE_FILENAME
    payload = _authenticate_existing(internal, expected_request=expected_request, root=root)
    if payload is None:
        raise FinalTestDaymetFeatureError(
            "Uncommitted final-test Daymet output lacks a recovery provenance."
        )
    marker_writer(payload, marker)
    recovered = _authenticate_existing(marker, expected_request=expected_request, root=root)
    if recovered is None:
        raise AssertionError("Recovered Daymet provenance was not published.")
    return recovered


def build_final_test_daymet_feature_artifacts(
    *,
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    landsat_inventory_directory: str | Path = ("manifests/final_test_2025/landsat_inventory"),
    daymet_manifest_directory: str | Path = ("manifests/final_test_2025/daymet_grid"),
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    provenance_path: str | Path = DEFAULT_PROVENANCE_PATH,
    inspector: Inspector = inspect_exact_final_test_daymet_netcdf,
    cell_reader: CellReader = read_daymet_netcdf_cells,
) -> dict[str, Any]:
    """Authenticate, compile, and atomically freeze final-test Daymet predictors."""

    root = _project_root()
    pipeline_sha256, pipeline = _current_pipeline(root)
    formal_path = _resolve(root, formal_lock_path)
    inventory_directory = _resolve(root, landsat_inventory_directory)
    manifest_directory = _resolve(root, daymet_manifest_directory)
    output = _resolve(root, output_directory)
    marker = _resolve(root, provenance_path)
    request = _request_payload(
        formal_path=formal_path,
        inventory_directory=inventory_directory,
        manifest_directory=manifest_directory,
        output=output,
        marker=marker,
        inspector=inspector,
        cell_reader=cell_reader,
    )
    existing = _authenticate_existing(marker, expected_request=request, root=root)
    if existing is not None:
        return existing
    if output.exists():
        return _recover_published_output(
            output,
            marker,
            expected_request=request,
            root=root,
        )
    feature_path = output / OUTPUT_FILENAME
    audit_path = output / AUDIT_FILENAME

    formal, formal_sha256 = authenticate_formal_model_lock(formal_path)
    feature_names = _locked_daymet_feature_names(formal)
    inventory_path = inventory_directory / LANDSAT_PROVENANCE_FILENAME
    inventory_sha256 = sha256_file(inventory_path)
    inventory = _read_json(inventory_path, label="Final-test Landsat inventory")
    if sha256_file(inventory_path) != inventory_sha256:
        raise FinalTestDaymetFeatureError(
            "Final-test Landsat inventory changed during authentication."
        )
    keys, key_path, key_record, inventory_commit = _validate_blind_inventory(
        inventory,
        formal_lock=formal,
        formal_sha256=formal_sha256,
        inventory_directory=inventory_directory,
    )
    _validate_final_keys(keys, production_shape=True)
    grid_path = manifest_directory / GRID_PROVENANCE_FILENAME
    grid_sha256 = sha256_file(grid_path)
    grid = _read_json(grid_path, label="Final-test Daymet grid")
    if sha256_file(grid_path) != grid_sha256:
        raise FinalTestDaymetFeatureError("Final-test Daymet grid changed during authentication.")
    subset_records, specs, grid_locks = _validate_grid_and_subsets(
        grid,
        grid_path=grid_path,
        manifest_directory=manifest_directory,
        formal_path=formal_path,
        formal_lock=formal,
        formal_sha256=formal_sha256,
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha256,
        inventory_commit=inventory_commit,
        key_path=key_path,
        key_record=key_record,
        keys=keys,
        inspector=inspector,
    )
    if sha256_file(inventory_path) != inventory_sha256 or sha256_file(grid_path) != grid_sha256:
        raise FinalTestDaymetFeatureError(
            "Final-test inventory/grid changed during Daymet subset authentication."
        )
    grid_locks["sha256"] = grid_sha256
    weights, weight_locks = _authenticate_frozen_weights(formal, inventory=inventory, root=root)
    compilation = compile_final_test_daymet_feature_tables(
        subset_records,
        keys,
        weights,
        specs=specs,
        cell_reader=cell_reader,
    )
    subset_file_records = list(grid_locks["subset_files"])
    if set(compilation.features.columns) != {*KEY_COLUMNS, *feature_names}:
        raise FinalTestDaymetFeatureError("Final Daymet output schema is not 2 keys + 21.")

    immutable_inputs: list[dict[str, Any]] = [
        {
            "path": str(formal_path),
            "sha256": formal_sha256,
            "bytes": formal_path.stat().st_size,
        },
        {
            "path": str(inventory_path),
            "sha256": inventory_sha256,
            "bytes": inventory_path.stat().st_size,
        },
        {
            "path": str(key_path),
            "sha256": str(key_record["sha256"]),
            "bytes": int(key_record["bytes"]),
        },
        {
            "path": str(grid_path),
            "sha256": grid_sha256,
            "bytes": grid_path.stat().st_size,
        },
        *[dict(record) for record in grid_locks["locked_manifest_files"]],
        *[dict(record) for record in weight_locks["formal_chain"].values()],
        dict(weight_locks["development_daymet_provenance"]),
        dict(weight_locks["fixed_cell_weights"]),
        *subset_file_records,
    ]
    _verify_publish_snapshot(
        immutable_inputs,
        pipeline_sha256=pipeline_sha256,
        pipeline_fingerprint=pipeline,
        root=root,
    )
    missingness = {name: int(compilation.features[name].isna().sum()) for name in feature_names}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.staging-",
            dir=output.parent,
        )
    )
    try:
        staged_feature_path = staging / OUTPUT_FILENAME
        staged_audit_path = staging / AUDIT_FILENAME
        atomic_parquet(compilation.features, staged_feature_path)
        atomic_parquet(compilation.audit, staged_audit_path)
        frozen_features = pd.read_parquet(staged_feature_path)
        frozen_audit = pd.read_parquet(staged_audit_path)
        _assert_parquet_round_trip(
            frozen_features,
            compilation.features,
            label="Final-test Daymet feature table",
        )
        _assert_parquet_round_trip(
            frozen_audit,
            compilation.audit,
            label="Final-test Daymet audit table",
        )
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "complete_target_blind",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "final_test_year": FINAL_TEST_YEAR,
            "final_test_unlocked": False,
            "target_blind": True,
            "target_or_qa_tables_read": [],
            "target_or_qa_value_columns_read": [],
            "target_values_read": False,
            "models_loaded": False,
            "model_scores_read": False,
            "one_time_evaluation_consumed": False,
            "publication_protocol": "staged_directory_atomic_replace_v1",
            "request": request,
            "request_sha256": canonical_sha256(request),
            "row_count": len(compilation.features),
            "date_count": int(compilation.features["target_date"].nunique()),
            "tract_count": int(compilation.features["tract_geoid"].nunique()),
            "feature_count": len(feature_names),
            "feature_names": list(feature_names),
            "window_days": list(WINDOWS),
            "source_window_definition": "complete civil days d-n through d-1",
            "source_end_offset_days": -1,
            "target_day_observations_included": False,
            "annual_container_contains_unused_dates": True,
            "source": "Daymet V4 R1 daily gridded weather",
            "dataset_doi": DAYMET_DOI_URL,
            "date_specific_weight_renormalization": False,
            "static_eligible_land_denominator_invariant": True,
            "srad_energy_computed_cell_first": True,
            "complete_feature_rows": int(
                compilation.audit["daymet_all_primary_windows_complete"].sum()
            ),
            "incomplete_feature_rows": int(
                (~compilation.audit["daymet_all_primary_windows_complete"]).sum()
            ),
            "missing_count_by_feature": missingness,
            "semantic_key_sha256": canonical_frame_sha256(
                compilation.features.loc[:, list(KEY_COLUMNS)],
                sort_by=["target_date", "tract_geoid"],
            ),
            "semantic_feature_table_sha256": canonical_frame_sha256(
                compilation.features, sort_by=["target_date", "tract_geoid"]
            ),
            "semantic_audit_table_sha256": canonical_frame_sha256(
                compilation.audit, sort_by=["target_date", "tract_geoid"]
            ),
            "formal_model_lock": {
                "path": str(formal_path),
                "sha256": formal_sha256,
                "commit_sha256": formal["commit_sha256"],
            },
            "landsat_inventory": {
                "path": str(inventory_path),
                "sha256": inventory_sha256,
                "commit_sha256": inventory_commit,
            },
            "key_universe": {
                "path": str(key_path),
                "sha256": key_record["sha256"],
                "bytes": key_record["bytes"],
            },
            "daymet_grid": grid_locks,
            "development_fixed_weights": weight_locks,
            "immutable_input_files": immutable_inputs,
            "pipeline_sha256": pipeline_sha256,
            "pipeline_fingerprint": pipeline,
            "output_files": {
                OUTPUT_FILENAME: {
                    "path": str(feature_path),
                    **parquet_file_record(staged_feature_path, frozen_features),
                },
                AUDIT_FILENAME: {
                    "path": str(audit_path),
                    **parquet_file_record(staged_audit_path, frozen_audit),
                },
            },
            "remaining_gate": (
                "Join the frozen static/calendar, Daymet, and lagged Sentinel "
                "predictor families before any final-test target values are opened."
            ),
        }
        payload["commit_sha256"] = canonical_sha256(payload)
        internal_marker = staging / INTERNAL_PROVENANCE_FILENAME
        atomic_json(payload, internal_marker)
        if _read_json(internal_marker, label="Staged Daymet recovery provenance") != payload:
            raise FinalTestDaymetFeatureError(
                "Staged Daymet recovery provenance did not round-trip."
            )
        _verify_publish_snapshot(
            immutable_inputs,
            pipeline_sha256=pipeline_sha256,
            pipeline_fingerprint=pipeline,
            root=root,
        )
        _publish_staged_output(staging, output, payload, marker)
        published = _authenticate_existing(marker, expected_request=request, root=root)
        if published is None:
            raise AssertionError("Final Daymet provenance was not published.")
        return published
    finally:
        if staging.exists():
            if staging.parent.resolve() != output.parent.resolve() or not staging.name.startswith(
                f".{output.name}.staging-"
            ):
                raise FinalTestDaymetFeatureError(
                    "Refusing to clean an unowned Daymet staging directory."
                )
            shutil.rmtree(staging)
