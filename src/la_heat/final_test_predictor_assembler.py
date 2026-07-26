"""Assemble the frozen, target-blind 2025 predictor matrix.

This module only opens authenticated predictor artifacts.  It does not know a
Landsat target, QA, label, fitted-model, or score path.  Publication is an
atomic directory promotion with an internal recovery commit so that a failure
while writing the external manifest cannot make a partial table look final.
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

from la_heat.final_test_inventory import FINAL_TEST_YEAR, authenticate_formal_model_lock
from la_heat.final_test_sentinel_audit import (
    ALGORITHM_VERSION as SENTINEL_AUDIT_ALGORITHM_VERSION,
)
from la_heat.final_test_sentinel_audit import (
    AUDIT_PIPELINE_FILES as SENTINEL_AUDIT_PIPELINE_FILES,
)
from la_heat.final_test_sentinel_audit import (
    AUTHORIZATION_PATH as FINAL_TEST_AUTHORIZATION_PATH,
)
from la_heat.final_test_sentinel_audit import (
    DEFAULT_AUDIT_PATH as DEFAULT_SENTINEL_AUDIT_PATH,
)
from la_heat.final_test_sentinel_audit import (
    SCHEMA_VERSION as SENTINEL_AUDIT_SCHEMA_VERSION,
)
from la_heat.final_test_sentinel_features import (
    ALGORITHM_VERSION as SENTINEL_ALGORITHM_VERSION,
)
from la_heat.final_test_sentinel_features import (
    EXPECTED_ACQUISITION_COUNT as SENTINEL_EXPECTED_ACQUISITION_COUNT,
)
from la_heat.final_test_sentinel_features import (
    PIPELINE_FILES as SENTINEL_PIPELINE_FILES,
)
from la_heat.final_test_sentinel_features import (
    WORLD_COVER_FILENAME,
    authenticate_final_sentinel_inputs,
    authenticate_fixed_spatial_support,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_feature_builder import (
    _research_dependency_payload,
    load_sentinel_stage_config,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-predictor-assembly-v1-target-blind"
OUTPUT_FILENAME: Final = "final_predictors.parquet"
MISSINGNESS_FILENAME: Final = "predictor_missingness.csv"
PROVENANCE_FILENAME: Final = "PREDICTOR_ASSEMBLY.json"
INTERNAL_PROVENANCE_FILENAME: Final = PROVENANCE_FILENAME
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/processed/final_test_2025/predictors")
DEFAULT_PROVENANCE_PATH: Final = Path(
    "manifests/final_test_2025/predictors/PREDICTOR_ASSEMBLY.json"
)
DEFAULT_BASE_PATH: Final = Path(
    "data/interim/final_test_2025/predictor_base/predictor_base.parquet"
)
DEFAULT_BASE_PROVENANCE_PATH: Final = Path(
    "manifests/final_test_2025/predictor_base/PREDICTOR_BASE.json"
)
DEFAULT_DAYMET_PATH: Final = Path(
    "data/interim/final_test_2025/daymet_features/daymet_features.parquet"
)
DEFAULT_DAYMET_PROVENANCE_PATH: Final = Path(
    "manifests/final_test_2025/daymet_features/DAYMET_FEATURES.json"
)
DEFAULT_SENTINEL_PATH: Final = Path(
    "data/interim/final_test_2025/sentinel/sentinel_features.parquet"
)
DEFAULT_SENTINEL_PROGRESS_PATH: Final = Path(
    "data/interim/final_test_2025/sentinel/build_progress.json"
)
DEFAULT_SENTINEL_PIPELINE_PATH: Final = Path(
    "data/interim/final_test_2025/sentinel/pipeline_fingerprint.json"
)
DEFAULT_RESEARCH_CONFIG_PATH: Final = Path("configs/research.toml")
DEFAULT_SENTINEL_STAGE_CONFIG_PATH: Final = Path("configs/sentinel_features.toml")
DEFAULT_SENTINEL_INVENTORY_DIRECTORY: Final = Path(
    "manifests/final_test_2025/sentinel_inventory"
)
DEFAULT_SENTINEL_RAW_STAC_DIRECTORY: Final = Path(
    "data/raw/final_test_2025/sentinel/stac_items"
)

EXPECTED_ROW_COUNT: Final = 25_208
EXPECTED_DATE_COUNT: Final = 23
EXPECTED_TRACT_COUNT: Final = 1_096
EXPECTED_BASE_FEATURE_COUNT: Final = 20
EXPECTED_DAYMET_FEATURE_COUNT: Final = 21
EXPECTED_SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
EXPECTED_MODEL_FEATURE_COUNT: Final = 46
KEY_COLUMNS: Final = ("tract_geoid", "target_date")
SENTINEL_SOURCE_KEY_COLUMNS: Final = ("target_date", "tract_geoid")
BASE_AUDIT_COLUMNS: Final = (
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)
_FORBIDDEN_FEATURE_TOKENS: Final = (
    "geoid",
    "target",
    "label",
    "lst",
    "prediction",
    "residual",
    "score",
)
_SENTINEL_AGGREGATE_FILES: Final = {
    "acquisition_tract.parquet",
    "sentinel_features.parquet",
    "sentinel_feature_audit.parquet",
    "sentinel_lineage.parquet",
}
_BASE_ALGORITHM_VERSION: Final = "final-test-predictor-base-v1-static-calendar"
_BASE_PIPELINE_FILES: Final = (
    "scripts/build_final_test_predictor_base.py",
    "src/la_heat/calendar_features.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/final_test_predictor_base.py",
    "src/la_heat/provenance.py",
)
_DAYMET_ALGORITHM_VERSION: Final = "final-test-daymet-features-v1-frozen-weights"
_DAYMET_PIPELINE_FILES: Final = (
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
_PIPELINE_FILES: Final = tuple(
    dict.fromkeys(
        (
            "configs/research.toml",
            "configs/sentinel_features.toml",
            "scripts/build_final_test_predictors.py",
            *_BASE_PIPELINE_FILES,
            *_DAYMET_PIPELINE_FILES,
            *SENTINEL_AUDIT_PIPELINE_FILES,
            *SENTINEL_PIPELINE_FILES,
            "src/la_heat/final_test_inventory.py",
            "src/la_heat/final_test_predictor_assembler.py",
            "src/la_heat/provenance.py",
        )
    )
)

MarkerWriter = Callable[[dict[str, Any], Path], None]


class FinalTestPredictorAssemblyError(RuntimeError):
    """Raised when a final predictor input or publication fails closed."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _read_json_stable(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestPredictorAssemblyError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestPredictorAssemblyError(f"{label} changed or is not a JSON object.")
    return payload, before


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestPredictorAssemblyError(f"{label} canonical commit failed.")
    return recorded


def _snapshot_record(
    path: Path,
    *,
    expected: Mapping[str, Any] | None = None,
    label: str,
    require_recorded_path: bool = False,
) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    digest = sha256_file(resolved)
    size = resolved.stat().st_size
    if expected is not None:
        recorded_path = expected.get("path")
        if require_recorded_path and (
            not isinstance(recorded_path, str)
            or Path(recorded_path).resolve() != resolved
        ):
            raise FinalTestPredictorAssemblyError(f"{label} path lock failed.")
        if expected.get("sha256") != digest:
            raise FinalTestPredictorAssemblyError(f"{label} SHA-256 lock failed.")
        recorded_bytes = expected.get("bytes")
        if recorded_bytes is not None and recorded_bytes != size:
            raise FinalTestPredictorAssemblyError(f"{label} byte-size lock failed.")
    return {"path": str(resolved), "sha256": digest, "bytes": size}


def _deduplicate_snapshot(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[Path, dict[str, Any]] = {}
    for raw in records:
        path = Path(str(raw.get("path", ""))).resolve()
        if not str(raw.get("sha256", "")) or int(raw.get("bytes", -1)) < 0:
            raise FinalTestPredictorAssemblyError("Immutable input record is incomplete.")
        record = {
            "path": str(path),
            "sha256": str(raw["sha256"]),
            "bytes": int(raw["bytes"]),
        }
        previous = by_path.get(path)
        if previous is not None and previous != record:
            raise FinalTestPredictorAssemblyError(
                f"Conflicting immutable locks for {path}."
            )
        by_path[path] = record
    if not by_path:
        raise FinalTestPredictorAssemblyError("Immutable input snapshot cannot be empty.")
    return [by_path[path] for path in sorted(by_path, key=str)]


def _verify_snapshot(records: Sequence[Mapping[str, Any]]) -> None:
    for record in _deduplicate_snapshot(records):
        _snapshot_record(
            Path(record["path"]),
            expected=record,
            label=f"Immutable input {record['path']}",
            require_recorded_path=True,
        )


def _verify_upstream_pipeline(
    fingerprint: object,
    pipeline_sha256: object,
    *,
    root: Path,
    label: str,
    expected_algorithm_version: str,
    expected_files: Sequence[str],
) -> None:
    if (
        not isinstance(fingerprint, Mapping)
        or not isinstance(pipeline_sha256, str)
        or canonical_sha256(fingerprint) != pipeline_sha256
    ):
        raise FinalTestPredictorAssemblyError(f"{label} pipeline hash failed.")
    files = fingerprint.get("files")
    if (
        fingerprint.get("algorithm_version") != expected_algorithm_version
        or not isinstance(files, Mapping)
        or set(files) != set(expected_files)
    ):
        raise FinalTestPredictorAssemblyError(
            f"{label} pipeline algorithm/dependency closure drifted."
        )
    for relative, expected_sha256 in files.items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise FinalTestPredictorAssemblyError(
                f"{label} pipeline path is not repository-relative."
            )
        path = (root / relative_path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as error:
            raise FinalTestPredictorAssemblyError(
                f"{label} pipeline path escapes the project root."
            ) from error
        if not path.is_file() or sha256_file(path) != expected_sha256:
            raise FinalTestPredictorAssemblyError(
                f"{label} pipeline file changed: {relative}."
            )


def _current_pipeline(root: Path) -> tuple[str, dict[str, Any]]:
    return code_runtime_fingerprint(
        project_root=root,
        relative_paths=_PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )


def _verify_current_pipeline(
    root: Path,
    expected_sha256: str,
    expected_fingerprint: Mapping[str, Any],
) -> None:
    current_sha256, current_fingerprint = _current_pipeline(root)
    if current_sha256 != expected_sha256 or current_fingerprint != dict(
        expected_fingerprint
    ):
        raise FinalTestPredictorAssemblyError(
            "Predictor assembler pipeline changed during execution."
        )


def _model_feature_contract(
    formal_lock: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    models = formal_lock.get("models")
    if not isinstance(models, Mapping) or set(models) != {"B1", "M2"}:
        raise FinalTestPredictorAssemblyError("Formal lock lacks exact B1/M2 models.")
    b1 = models["B1"]
    m2 = models["M2"]
    if not isinstance(b1, Mapping) or not isinstance(m2, Mapping):
        raise FinalTestPredictorAssemblyError("Formal model records are invalid.")
    b1_names = b1.get("feature_names")
    m2_names = m2.get("feature_names")
    if (
        not isinstance(b1_names, list)
        or not all(isinstance(name, str) for name in b1_names)
        or not isinstance(m2_names, list)
        or not all(isinstance(name, str) for name in m2_names)
        or len(b1_names) != 23
        or b1.get("feature_count") != 23
        or len(m2_names) != EXPECTED_MODEL_FEATURE_COUNT
        or m2.get("feature_count") != EXPECTED_MODEL_FEATURE_COUNT
        or len(m2_names) != len(set(m2_names))
    ):
        raise FinalTestPredictorAssemblyError("Frozen model feature counts/order are invalid.")
    forbidden = [
        name
        for name in m2_names
        if any(token in name.casefold() for token in _FORBIDDEN_FEATURE_TOKENS)
    ]
    if forbidden:
        raise FinalTestPredictorAssemblyError(
            f"Frozen predictors contain forbidden target/key fields: {forbidden}."
        )
    base_names = list(m2_names[:EXPECTED_BASE_FEATURE_COUNT])
    daymet_names = list(
        m2_names[
            EXPECTED_BASE_FEATURE_COUNT : EXPECTED_BASE_FEATURE_COUNT
            + EXPECTED_DAYMET_FEATURE_COUNT
        ]
    )
    sentinel_names = list(m2_names[-len(EXPECTED_SENTINEL_FEATURES) :])
    if (
        len(daymet_names) != EXPECTED_DAYMET_FEATURE_COUNT
        or not all(name.startswith("daymet_") for name in daymet_names)
        or tuple(sentinel_names) != EXPECTED_SENTINEL_FEATURES
        or any(
            name.startswith(("daymet_", "sentinel_"))
            for name in base_names
        )
    ):
        raise FinalTestPredictorAssemblyError(
            "Formal M2 base/Daymet/Sentinel family boundaries drifted."
        )
    calendar_names = [name for name in base_names if name.startswith("calendar_")]
    if len(calendar_names) != 2 or b1_names != [*calendar_names, *daymet_names]:
        raise FinalTestPredictorAssemblyError(
            "Formal B1 is not exact calendar plus Daymet in frozen order."
        )
    return base_names, daymet_names, sentinel_names, list(m2_names)


def _require_blind_flags(payload: Mapping[str, Any], *, label: str) -> None:
    if (
        payload.get("state") != "complete_target_blind"
        or payload.get("final_test_year") != FINAL_TEST_YEAR
        or payload.get("target_blind") is not True
        or payload.get("target_or_qa_tables_read") != []
        or payload.get("target_values_read") is not False
        or payload.get("models_loaded") is not False
        or payload.get("model_scores_read") is not False
        or payload.get("one_time_evaluation_consumed") is not False
    ):
        raise FinalTestPredictorAssemblyError(f"{label} is not a blind frozen commit.")


def _require_formal_reference(
    record: object,
    *,
    formal_path: Path,
    formal_sha256: str,
    formal_commit: str,
    label: str,
) -> None:
    if (
        not isinstance(record, Mapping)
        or Path(str(record.get("path", ""))).resolve() != formal_path
        or record.get("sha256") != formal_sha256
        or record.get("commit_sha256") != formal_commit
    ):
        raise FinalTestPredictorAssemblyError(
            f"{label} does not bind the active formal model lock."
        )


def _recorded_output(
    payload: Mapping[str, Any],
    *,
    name: str,
    path: Path,
    label: str,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    outputs = payload.get("output_files")
    record = outputs.get(name) if isinstance(outputs, Mapping) else None
    if not isinstance(record, Mapping):
        raise FinalTestPredictorAssemblyError(f"{label} output record is missing.")
    snapshot = _snapshot_record(
        path,
        expected=record,
        label=label,
        require_recorded_path=True,
    )
    return record, snapshot


def _provenance_dimensions(
    payload: Mapping[str, Any],
    *,
    feature_count: int,
    label: str,
) -> None:
    if (
        payload.get("row_count") != EXPECTED_ROW_COUNT
        or payload.get("date_count") != EXPECTED_DATE_COUNT
        or payload.get("tract_count") != EXPECTED_TRACT_COUNT
        or payload.get("feature_count") != feature_count
    ):
        raise FinalTestPredictorAssemblyError(f"{label} production dimensions drifted.")


def _authenticate_base(
    *,
    path: Path,
    provenance_path: Path,
    formal_path: Path,
    formal_sha256: str,
    formal_commit: str,
    feature_names: Sequence[str],
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    payload, provenance_sha256 = _read_json_stable(
        provenance_path, label="Predictor-base provenance"
    )
    commit = _verify_commit(payload, label="Predictor-base provenance")
    _require_blind_flags(payload, label="Predictor base")
    _provenance_dimensions(
        payload, feature_count=EXPECTED_BASE_FEATURE_COUNT, label="Predictor base"
    )
    if payload.get("feature_names") != list(feature_names):
        raise FinalTestPredictorAssemblyError(
            "Predictor-base features disagree with formal M2."
        )
    _require_formal_reference(
        payload.get("formal_model_lock"),
        formal_path=formal_path,
        formal_sha256=formal_sha256,
        formal_commit=formal_commit,
        label="Predictor base",
    )
    _verify_upstream_pipeline(
        payload.get("pipeline_fingerprint"),
        payload.get("pipeline_sha256"),
        root=root,
        label="Predictor base",
        expected_algorithm_version=_BASE_ALGORITHM_VERSION,
        expected_files=_BASE_PIPELINE_FILES,
    )
    _, output_snapshot = _recorded_output(
        payload,
        name=path.name,
        path=path,
        label="Predictor-base table",
    )
    snapshots = [
        {
            "path": str(provenance_path),
            "sha256": provenance_sha256,
            "bytes": provenance_path.stat().st_size,
        },
        output_snapshot,
    ]
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or not inputs:
        raise FinalTestPredictorAssemblyError("Predictor-base input locks are missing.")
    for name, record in inputs.items():
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(
                f"Predictor-base input {name} is invalid."
            )
        input_path = Path(str(record.get("path", ""))).resolve()
        snapshots.append(
            _snapshot_record(
                input_path,
                expected=record,
                label=f"Predictor-base input {name}",
                require_recorded_path=True,
            )
        )
    return payload, snapshots, commit


def _authenticate_daymet(
    *,
    path: Path,
    provenance_path: Path,
    formal_path: Path,
    formal_sha256: str,
    formal_commit: str,
    feature_names: Sequence[str],
    base_provenance: Mapping[str, Any],
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    payload, provenance_sha256 = _read_json_stable(
        provenance_path, label="Final-test Daymet provenance"
    )
    commit = _verify_commit(payload, label="Final-test Daymet provenance")
    _require_blind_flags(payload, label="Final-test Daymet")
    _provenance_dimensions(
        payload, feature_count=EXPECTED_DAYMET_FEATURE_COUNT, label="Final-test Daymet"
    )
    missing_counts = payload.get("missing_count_by_feature")
    if (
        payload.get("publication_protocol") != "staged_directory_atomic_replace_v1"
        or payload.get("feature_names") != list(feature_names)
        or payload.get("complete_feature_rows") != EXPECTED_ROW_COUNT
        or payload.get("incomplete_feature_rows") != 0
        or payload.get("source_end_offset_days") != -1
        or payload.get("target_day_observations_included") is not False
        or not isinstance(missing_counts, Mapping)
        or set(missing_counts) != set(feature_names)
        or any(value != 0 for value in missing_counts.values())
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Daymet feature completeness/lineage contract failed."
        )
    _require_formal_reference(
        payload.get("formal_model_lock"),
        formal_path=formal_path,
        formal_sha256=formal_sha256,
        formal_commit=formal_commit,
        label="Final-test Daymet",
    )
    request = payload.get("request")
    if (
        not isinstance(request, Mapping)
        or payload.get("request_sha256") != canonical_sha256(request)
        or Path(str(request.get("feature_output_path", ""))).resolve() != path
        or Path(str(request.get("external_provenance_path", ""))).resolve()
        != provenance_path
        or Path(str(request.get("formal_model_lock_path", ""))).resolve()
        != formal_path
        or request.get("final_test_year") != FINAL_TEST_YEAR
        or request.get("window_days") != [1, 3, 7]
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Daymet provenance belongs to another request."
        )
    _verify_upstream_pipeline(
        payload.get("pipeline_fingerprint"),
        payload.get("pipeline_sha256"),
        root=root,
        label="Final-test Daymet",
        expected_algorithm_version=_DAYMET_ALGORITHM_VERSION,
        expected_files=_DAYMET_PIPELINE_FILES,
    )
    _, output_snapshot = _recorded_output(
        payload,
        name=path.name,
        path=path,
        label="Final-test Daymet table",
    )
    snapshots = [
        {
            "path": str(provenance_path),
            "sha256": provenance_sha256,
            "bytes": provenance_path.stat().st_size,
        },
        output_snapshot,
    ]
    outputs = payload.get("output_files")
    assert isinstance(outputs, Mapping)
    for name, record in outputs.items():
        if name == path.name:
            continue
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(f"Daymet output {name} is invalid.")
        snapshots.append(
            _snapshot_record(
                Path(str(record.get("path", ""))).resolve(),
                expected=record,
                label=f"Final-test Daymet output {name}",
                require_recorded_path=True,
            )
        )
    immutable = payload.get("immutable_input_files")
    if not isinstance(immutable, list) or not immutable:
        raise FinalTestPredictorAssemblyError("Daymet immutable inputs are missing.")
    for record in immutable:
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError("Daymet immutable input is invalid.")
        snapshots.append(
            _snapshot_record(
                Path(str(record.get("path", ""))).resolve(),
                expected=record,
                label="Final-test Daymet immutable input",
                require_recorded_path=True,
            )
        )
    internal_path = Path(str(request.get("internal_provenance_path", ""))).resolve()
    internal, internal_sha256 = _read_json_stable(
        internal_path, label="Internal Daymet recovery provenance"
    )
    if internal != payload:
        raise FinalTestPredictorAssemblyError(
            "External and internal Daymet commits disagree."
        )
    snapshots.append(
        {
            "path": str(internal_path),
            "sha256": internal_sha256,
            "bytes": internal_path.stat().st_size,
        }
    )
    base_landsat = base_provenance.get("inputs", {}).get("landsat_inventory", {})
    daymet_landsat = payload.get("landsat_inventory")
    base_key = base_provenance.get("inputs", {}).get("key_universe", {})
    daymet_key = payload.get("key_universe")
    if (
        not isinstance(daymet_landsat, Mapping)
        or not isinstance(daymet_key, Mapping)
        or daymet_landsat.get("sha256") != base_landsat.get("sha256")
        or daymet_landsat.get("commit_sha256")
        != base_landsat.get("commit_sha256")
        or daymet_key.get("sha256") != base_key.get("sha256")
    ):
        raise FinalTestPredictorAssemblyError(
            "Daymet and predictor base do not share one Landsat/key lock."
        )
    return payload, snapshots, commit


@dataclass(frozen=True, slots=True)
class _SentinelChainAudit:
    locks: dict[str, str]
    snapshots: list[dict[str, Any]]
    stage_payload: dict[str, Any]
    research_dependency_payload: dict[str, Any]


def _declared_output_snapshots(
    payload: Mapping[str, Any],
    *,
    directory: Path,
    label: str,
) -> list[dict[str, Any]]:
    outputs = payload.get("output_files")
    if not isinstance(outputs, Mapping) or not outputs:
        raise FinalTestPredictorAssemblyError(f"{label} output locks are missing.")
    snapshots: list[dict[str, Any]] = []
    for name, record in outputs.items():
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(f"{label} output {name} is invalid.")
        recorded = record.get("path")
        path = (
            Path(str(recorded)).resolve()
            if isinstance(recorded, str) and recorded
            else (directory / str(name)).resolve()
        )
        if path != (directory / str(name)).resolve():
            raise FinalTestPredictorAssemblyError(
                f"{label} output {name} path leaves its frozen directory."
            )
        snapshots.append(
            _snapshot_record(
                path,
                expected=record,
                label=f"{label} output {name}",
                require_recorded_path=isinstance(recorded, str),
            )
        )
    return snapshots


def _authenticate_sentinel_chain(
    *,
    root: Path,
    research_config_path: Path,
    stage_config_path: Path,
    formal_path: Path,
    landsat_inventory_directory: Path,
    sentinel_inventory_directory: Path,
    raw_stac_directory: Path,
) -> _SentinelChainAudit:
    """Re-authenticate the complete predictor-only Sentinel request and support."""

    stage = load_sentinel_stage_config(stage_config_path)
    authenticated = authenticate_final_sentinel_inputs(
        project_root=root,
        research_config_path=research_config_path,
        formal_lock_path=formal_path,
        landsat_inventory_directory=landsat_inventory_directory,
        sentinel_inventory_directory=sentinel_inventory_directory,
        raw_stac_directory=raw_stac_directory,
    )
    worldcover_path = (
        root / "data/raw/final_test_2025/static" / WORLD_COVER_FILENAME
    )
    if not worldcover_path.is_file():
        raise FileNotFoundError(
            "Frozen WorldCover support is missing; predictor assembly never downloads inputs."
        )
    spatial = authenticate_fixed_spatial_support(
        project_root=root,
        authenticated=authenticated,
        stage=stage,
        worldcover_path=worldcover_path,
    )
    locks = {**authenticated.locks, **spatial.locks}
    snapshots = [
        _snapshot_record(research_config_path, label="Sentinel research config"),
        _snapshot_record(stage_config_path, label="Sentinel stage config"),
        _snapshot_record(worldcover_path, label="Sentinel WorldCover support"),
        _snapshot_record(authenticated.city_path, label="Sentinel city boundary"),
    ]

    landsat_path = landsat_inventory_directory / "LANDSAT_INVENTORY.json"
    landsat, landsat_sha256 = _read_json_stable(
        landsat_path, label="Sentinel-linked Landsat inventory"
    )
    snapshots.append(
        {
            "path": str(landsat_path),
            "sha256": landsat_sha256,
            "bytes": landsat_path.stat().st_size,
        }
    )
    snapshots.extend(
        _declared_output_snapshots(
            landsat,
            directory=landsat_inventory_directory,
            label="Sentinel-linked Landsat inventory",
        )
    )

    sentinel_provenance_path = (
        sentinel_inventory_directory / "FINAL_TEST_SENTINEL_INVENTORY.json"
    )
    sentinel_provenance, sentinel_provenance_sha256 = _read_json_stable(
        sentinel_provenance_path, label="Final Sentinel inventory provenance"
    )
    if (
        sentinel_provenance_sha256
        != locks.get("final_sentinel_inventory_provenance_sha256")
    ):
        raise FinalTestPredictorAssemblyError(
            "Final Sentinel inventory provenance lock changed."
        )
    snapshots.append(
        {
            "path": str(sentinel_provenance_path),
            "sha256": sentinel_provenance_sha256,
            "bytes": sentinel_provenance_path.stat().st_size,
        }
    )
    summary_path = sentinel_inventory_directory / "inventory_summary.json"
    summary, summary_sha256 = _read_json_stable(
        summary_path, label="Final Sentinel inventory summary"
    )
    if summary_sha256 != locks.get("sentinel_inventory_summary_sha256"):
        raise FinalTestPredictorAssemblyError("Sentinel inventory summary lock changed.")
    snapshots.append(
        {
            "path": str(summary_path),
            "sha256": summary_sha256,
            "bytes": summary_path.stat().st_size,
        }
    )
    snapshots.extend(
        _declared_output_snapshots(
            summary,
            directory=sentinel_inventory_directory,
            label="Final Sentinel inventory",
        )
    )
    for contract in authenticated.contracts.values():
        snapshots.append(
            _snapshot_record(
                contract.snapshot_path,
                expected={
                    "path": str(contract.snapshot_path),
                    "sha256": contract.snapshot_sha256,
                },
                label=f"Sentinel raw snapshot {contract.item_id}",
                require_recorded_path=True,
            )
        )

    static_provenance_path = (
        root / "data/processed/static_features/static_features_provenance.json"
    )
    static_provenance, static_provenance_sha256 = _read_json_stable(
        static_provenance_path, label="Sentinel static support provenance"
    )
    if (
        static_provenance_sha256 != locks.get("static_feature_provenance_sha256")
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel static support provenance lock changed."
        )
    snapshots.append(
        {
            "path": str(static_provenance_path),
            "sha256": static_provenance_sha256,
            "bytes": static_provenance_path.stat().st_size,
        }
    )
    static_directory = Path(
        str(static_provenance.get("output_directory", ""))
    ).resolve()
    snapshots.extend(
        _declared_output_snapshots(
            static_provenance,
            directory=static_directory,
            label="Sentinel static support",
        )
    )
    for path, key, label in (
        (
            root / "data/interim/targets/fixed_grid_lock.json",
            "target_grid_lock_sha256",
            "Sentinel fixed-grid lock",
        ),
        (
            root / "data/interim/targets/primary_tract_manifest.parquet",
            "tract_manifest_file_sha256",
            "Sentinel primary tract manifest",
        ),
        (
            worldcover_path,
            "worldcover_source_file_sha256_audit_only",
            "Sentinel WorldCover support",
        ),
    ):
        snapshots.append(
            _snapshot_record(
                path,
                expected={"path": str(path), "sha256": locks.get(key)},
                label=label,
                require_recorded_path=True,
            )
        )
    return _SentinelChainAudit(
        locks={key: str(value) for key, value in locks.items()},
        snapshots=_deduplicate_snapshot(snapshots),
        stage_payload=dict(stage.raw),
        research_dependency_payload=_research_dependency_payload(
            authenticated.predictor_research
        ),
    )


def _authenticate_sentinel(
    *,
    path: Path,
    progress_path: Path,
    pipeline_path: Path,
    research_config_path: Path,
    stage_config_path: Path,
    formal_path: Path,
    landsat_inventory_directory: Path,
    sentinel_inventory_directory: Path,
    raw_stac_directory: Path,
    formal_sha256: str,
    formal_commit: str,
    feature_names: Sequence[str],
    base_provenance: Mapping[str, Any],
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    progress, progress_sha256 = _read_json_stable(
        progress_path, label="Final-test Sentinel build progress"
    )
    fingerprint, fingerprint_sha256 = _read_json_stable(
        pipeline_path, label="Final-test Sentinel pipeline fingerprint"
    )
    if (
        progress.get("state") != "complete"
        or progress.get("promoted_outputs_valid") is not True
        or progress.get("build_complete") is not True
        or progress.get("target_blind_predictor_access")
        != "2025_predictors_only_no_labels"
        or progress.get("requester_pays_product_xml_opened") != "false"
        or progress.get("expected_physical_acquisition_count")
        != SENTINEL_EXPECTED_ACQUISITION_COUNT
        or progress.get("completed_physical_acquisition_count")
        != SENTINEL_EXPECTED_ACQUISITION_COUNT
        or progress.get("feature_row_count") != EXPECTED_ROW_COUNT
        or progress.get("target_date_count") != EXPECTED_DATE_COUNT
        or progress.get("tract_count") != EXPECTED_TRACT_COUNT
        or not 0
        <= int(progress.get("feature_available_row_count", -1))
        <= EXPECTED_ROW_COUNT
        or progress.get("formal_model_lock_sha256") != formal_sha256
        or progress.get("formal_model_lock_commit_sha256") != formal_commit
        or progress.get("final_test_sentinel_feature_pipeline_fingerprint_sha256")
        != fingerprint_sha256
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Sentinel build is incomplete or not target-blind."
        )
    _verify_upstream_pipeline(
        fingerprint,
        progress.get("final_test_sentinel_feature_pipeline_sha256"),
        root=root,
        label="Final-test Sentinel",
        expected_algorithm_version=SENTINEL_ALGORITHM_VERSION,
        expected_files=SENTINEL_PIPELINE_FILES,
    )
    chain = _authenticate_sentinel_chain(
        root=root,
        research_config_path=research_config_path,
        stage_config_path=stage_config_path,
        formal_path=formal_path,
        landsat_inventory_directory=landsat_inventory_directory,
        sentinel_inventory_directory=sentinel_inventory_directory,
        raw_stac_directory=raw_stac_directory,
    )
    mismatched_chain_locks = sorted(
        key for key, value in chain.locks.items() if progress.get(key) != value
    )
    if (
        mismatched_chain_locks
        or progress.get("sentinel_stage_config_payload") != chain.stage_payload
        or progress.get("sentinel_stage_config_sha256")
        != canonical_sha256(chain.stage_payload)
        or progress.get("sentinel_research_dependency_payload")
        != chain.research_dependency_payload
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel request/spec/input locks drifted: "
            f"{mismatched_chain_locks}."
        )
    aggregates = progress.get("aggregate_outputs")
    if not isinstance(aggregates, Mapping) or set(aggregates) != _SENTINEL_AGGREGATE_FILES:
        raise FinalTestPredictorAssemblyError(
            "Final-test Sentinel aggregate output set drifted."
        )
    snapshots = [
        {
            "path": str(progress_path),
            "sha256": progress_sha256,
            "bytes": progress_path.stat().st_size,
        },
        {
            "path": str(pipeline_path),
            "sha256": fingerprint_sha256,
            "bytes": pipeline_path.stat().st_size,
        },
        *chain.snapshots,
    ]
    for name, record in aggregates.items():
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(
                f"Sentinel aggregate output {name} is invalid."
            )
        aggregate_path = progress_path.parent / name
        snapshots.append(
            _snapshot_record(
                aggregate_path,
                expected=record,
                label=f"Sentinel aggregate output {name}",
            )
        )
    if path != progress_path.parent / "sentinel_features.parquet":
        raise FinalTestPredictorAssemblyError(
            "Sentinel feature path is outside its authenticated build."
        )
    base_landsat = base_provenance.get("inputs", {}).get("landsat_inventory", {})
    if (
        progress.get("landsat_inventory_sha256") != base_landsat.get("sha256")
        or progress.get("landsat_inventory_commit_sha256")
        != base_landsat.get("commit_sha256")
        or tuple(feature_names) != EXPECTED_SENTINEL_FEATURES
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel and predictor base do not share one frozen input contract."
        )
    return progress, snapshots, progress_sha256


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _authenticate_sentinel_postrun_audit(
    *,
    path: Path,
    sentinel_path: Path,
    progress_path: Path,
    pipeline_path: Path,
    progress: Mapping[str, Any],
    progress_sha256: str,
    formal_sha256: str,
    formal_commit: str,
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    """Require the isolated, current, target-blind Sentinel safety audit."""

    canonical_path = _resolve(root, DEFAULT_SENTINEL_AUDIT_PATH)
    if path != canonical_path:
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit path must be the exact canonical production path."
        )
    if (root / FINAL_TEST_AUTHORIZATION_PATH).exists():
        raise FinalTestPredictorAssemblyError(
            "Final-test authorization already exists; predictor-only assembly is closed."
        )

    payload, audit_sha256 = _read_json_stable(
        path, label="Final-test Sentinel post-run audit"
    )
    commit = _verify_commit(payload, label="Final-test Sentinel post-run audit")
    if (
        payload.get("schema_version") != SENTINEL_AUDIT_SCHEMA_VERSION
        or payload.get("algorithm_version") != SENTINEL_AUDIT_ALGORITHM_VERSION
        or payload.get("state") != "passed"
        or payload.get("safe_for_final_predictor_assembly") is not True
        or payload.get("target_blind") is not True
        or payload.get("target_or_qa_values_read") is not False
        or payload.get("target_or_qa_paths_opened") != []
        or payload.get("fitted_models_loaded") is not False
        or payload.get("predictions_scores_or_metrics_read") is not False
        or payload.get("authorization_file_present") is not False
        or payload.get("sentinel_algorithm_version")
        != SENTINEL_ALGORITHM_VERSION
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Sentinel audit is absent, unsafe, or not target-blind."
        )
    _verify_upstream_pipeline(
        payload.get("audit_pipeline_fingerprint"),
        payload.get("audit_pipeline_sha256"),
        root=root,
        label="Final-test Sentinel post-run audit",
        expected_algorithm_version=SENTINEL_AUDIT_ALGORITHM_VERSION,
        expected_files=SENTINEL_AUDIT_PIPELINE_FILES,
    )

    raw_snapshots = payload.get("authenticated_input_files")
    if (
        not isinstance(raw_snapshots, list)
        or not raw_snapshots
        or payload.get("authenticated_input_file_set_sha256")
        != canonical_sha256(raw_snapshots)
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit authenticated-input set is invalid."
        )
    recorded_paths = [
        str(record.get("path", ""))
        for record in raw_snapshots
        if isinstance(record, Mapping)
    ]
    if (
        len(recorded_paths) != len(raw_snapshots)
        or recorded_paths != sorted(recorded_paths)
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit authenticated-input ordering is invalid."
        )

    snapshots_by_path: dict[Path, dict[str, Any]] = {}
    for raw_record in raw_snapshots:
        if (
            not isinstance(raw_record, Mapping)
            or set(raw_record) != {"path", "sha256", "bytes"}
            or not isinstance(raw_record.get("path"), str)
            or not raw_record["path"]
            or not _is_sha256(raw_record.get("sha256"))
            or isinstance(raw_record.get("bytes"), bool)
            or not isinstance(raw_record.get("bytes"), int)
            or int(raw_record["bytes"]) < 0
        ):
            raise FinalTestPredictorAssemblyError(
                "Sentinel audit contains an invalid authenticated-input record."
            )
        input_path = _resolve(root, str(raw_record["path"]))
        if input_path in snapshots_by_path:
            raise FinalTestPredictorAssemblyError(
                "Sentinel audit contains duplicate authenticated-input paths."
            )
        snapshots_by_path[input_path] = _snapshot_record(
            input_path,
            expected=raw_record,
            label=f"Sentinel audit input {raw_record['path']}",
        )

    def require_audited(path_to_require: Path, *, label: str) -> dict[str, Any]:
        record = snapshots_by_path.get(path_to_require.resolve())
        if record is None:
            raise FinalTestPredictorAssemblyError(
                f"Sentinel audit does not authenticate {label}."
            )
        return record

    status_path = progress_path.parent / "status.json"
    require_audited(status_path, label="the canonical completion status")
    audited_progress = require_audited(
        progress_path, label="the canonical build progress"
    )
    audited_pipeline = require_audited(
        pipeline_path, label="the canonical pipeline fingerprint"
    )
    if (
        audited_progress["sha256"] != progress_sha256
        or audited_pipeline["sha256"] != sha256_file(pipeline_path)
    ):
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit no longer binds the active progress/pipeline files."
        )

    aggregates = progress.get("aggregate_outputs")
    if not isinstance(aggregates, Mapping) or set(aggregates) != _SENTINEL_AGGREGATE_FILES:
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit cannot bind an incomplete aggregate output set."
        )
    for name in sorted(_SENTINEL_AGGREGATE_FILES):
        record = aggregates.get(name)
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(
                f"Sentinel aggregate output {name} is invalid."
            )
        aggregate_path = progress_path.parent / name
        audited = require_audited(
            aggregate_path, label=f"Sentinel aggregate output {name}"
        )
        if (
            audited["sha256"] != record.get("sha256")
            or audited["bytes"] != record.get("bytes")
        ):
            raise FinalTestPredictorAssemblyError(
                f"Sentinel audit/aggregate hash mismatch: {name}."
            )
    if sentinel_path != progress_path.parent / "sentinel_features.parquet":
        raise FinalTestPredictorAssemblyError(
            "Sentinel audit feature path is outside the authenticated production build."
        )

    completion = payload.get("completion_contract")
    semantic = payload.get("semantic_contract")
    acquisition = semantic.get("acquisition") if isinstance(semantic, Mapping) else None
    cache = payload.get("cache_contract")
    calibration = payload.get("calibration_classification")
    feature_record = aggregates.get("sentinel_features.parquet")
    if (
        not isinstance(completion, Mapping)
        or completion.get("status_complete") is not True
        or completion.get("progress_complete") is not True
        or completion.get("algorithm_version") != SENTINEL_ALGORITHM_VERSION
        or completion.get("completed_physical_acquisition_count")
        != SENTINEL_EXPECTED_ACQUISITION_COUNT
        or completion.get("feature_available_row_count")
        != progress.get("feature_available_row_count")
        or not isinstance(semantic, Mapping)
        or semantic.get("feature_row_count") != EXPECTED_ROW_COUNT
        or semantic.get("audit_row_count") != EXPECTED_ROW_COUNT
        or semantic.get("target_date_count") != EXPECTED_DATE_COUNT
        or semantic.get("tract_count") != EXPECTED_TRACT_COUNT
        or semantic.get("feature_available_row_count")
        != progress.get("feature_available_row_count")
        or semantic.get("all_or_none_feature_missingness") is not True
        or semantic.get("minimum_source_age_days", 0) < 1
        or semantic.get("maximum_source_age_days", 61) > 60
        or semantic.get("target_day_or_future_source_count") != 0
        or not isinstance(feature_record, Mapping)
        or not _is_sha256(semantic.get("semantic_feature_table_sha256"))
        or not isinstance(acquisition, Mapping)
        or acquisition.get("physical_acquisition_count")
        != SENTINEL_EXPECTED_ACQUISITION_COUNT
        or acquisition.get("tract_count") != EXPECTED_TRACT_COUNT
        or acquisition.get("fixed_denominator_invariant") is not True
        or acquisition.get("two_tile_mosaic_invariant") is not True
        or not isinstance(cache, Mapping)
        or cache.get("cache_count") != SENTINEL_EXPECTED_ACQUISITION_COUNT
        or cache.get("all_current") is not True
        or not isinstance(calibration, Mapping)
        or calibration.get("passed") is not True
        or calibration.get("classification") != "c1_calibration_consistent"
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Sentinel audit safety/semantic contract failed."
        )

    upstream = payload.get("upstream_locks")
    expected_upstream = {
        "formal_model_lock_sha256": formal_sha256,
        "formal_model_lock_commit_sha256": formal_commit,
        "sentinel_inventory_provenance_sha256": progress.get(
            "final_sentinel_inventory_provenance_sha256"
        ),
        "sentinel_inventory_commit_sha256": progress.get(
            "final_sentinel_inventory_commit_sha256"
        ),
        "sentinel_inventory_semantic_sha256": progress.get(
            "sentinel_inventory_semantic_sha256"
        ),
        "raw_stac_snapshot_set_sha256": progress.get(
            "raw_stac_snapshot_set_sha256"
        ),
        "static_feature_audit_sha256": progress.get(
            "static_feature_audit_sha256"
        ),
        "target_grid_identity_sha256": progress.get(
            "target_grid_identity_sha256"
        ),
    }
    if (
        not isinstance(upstream, Mapping)
        or dict(upstream) != expected_upstream
        or any(not _is_sha256(value) for value in expected_upstream.values())
        or payload.get("source_collection")
        != progress.get("sentinel_source_collection")
        or payload.get("raw_dn_encoding")
        != progress.get("sentinel_raw_dn_encoding")
        or payload.get("prohibited_legacy_collection")
        != progress.get("sentinel_prohibited_legacy_collection")
        or payload.get("provider_parity_evidence_sha256")
        != progress.get("sentinel_provider_parity_evidence_sha256")
    ):
        raise FinalTestPredictorAssemblyError(
            "Final-test Sentinel audit upstream locks drifted."
        )

    audit_snapshot = _snapshot_record(
        path, label="Final-test Sentinel post-run audit"
    )
    snapshots = _deduplicate_snapshot(
        [audit_snapshot, *snapshots_by_path.values()]
    )
    _verify_snapshot(snapshots)
    return payload, snapshots, commit, audit_sha256


def _normalize_keys(
    frame: pd.DataFrame,
    *,
    label: str,
    production_shape: bool,
) -> pd.DataFrame:
    keys = frame.loc[:, list(KEY_COLUMNS)].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    try:
        keys["target_date"] = pd.to_datetime(keys["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise FinalTestPredictorAssemblyError(f"{label} dates are invalid.") from error
    if (
        keys.isna().any(axis=None)
        or not keys["tract_geoid"].str.fullmatch(r"\d{11}").all()
        or keys["target_date"].dt.tz is not None
        or not keys["target_date"].dt.normalize().equals(keys["target_date"])
        or not keys["target_date"].dt.year.eq(FINAL_TEST_YEAR).all()
        or keys.duplicated(list(KEY_COLUMNS)).any()
    ):
        raise FinalTestPredictorAssemblyError(
            f"{label} requires unique 11-digit GEOID × 2025 civil-date keys."
        )
    date_count = int(keys["target_date"].nunique())
    tract_count = int(keys["tract_geoid"].nunique())
    if len(keys) != date_count * tract_count:
        raise FinalTestPredictorAssemblyError(
            f"{label} is not a complete date-by-tract grid."
        )
    if production_shape and (
        len(keys) != EXPECTED_ROW_COUNT
        or date_count != EXPECTED_DATE_COUNT
        or tract_count != EXPECTED_TRACT_COUNT
    ):
        raise FinalTestPredictorAssemblyError(f"{label} production dimensions drifted.")
    return keys


def _numeric_frame(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    label: str,
    allow_missing: bool,
) -> pd.DataFrame:
    try:
        numeric = frame.loc[:, list(columns)].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as error:
        raise FinalTestPredictorAssemblyError(
            f"{label} predictors must be numeric."
        ) from error
    values = numeric.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any() or (not allow_missing and np.isnan(values).any()):
        raise FinalTestPredictorAssemblyError(
            f"{label} predictors contain forbidden missing/infinite values."
        )
    return numeric


def _assert_exact_keys(
    expected: pd.DataFrame,
    observed: pd.DataFrame,
    *,
    label: str,
) -> None:
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
        raise FinalTestPredictorAssemblyError(
            f"{label} key mismatch: missing={missing}, extra={extra}."
        )


def assemble_final_predictor_frame(
    base: pd.DataFrame,
    daymet: pd.DataFrame,
    sentinel: pd.DataFrame,
    *,
    base_feature_names: Sequence[str],
    daymet_feature_names: Sequence[str],
    sentinel_feature_names: Sequence[str],
    model_feature_names: Sequence[str],
    production_shape: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Exact one-to-one predictor join with no label or target inputs."""

    base_names = list(base_feature_names)
    daymet_names = list(daymet_feature_names)
    sentinel_names = list(sentinel_feature_names)
    model_names = list(model_feature_names)
    if (
        len(base_names) != EXPECTED_BASE_FEATURE_COUNT
        or len(daymet_names) != EXPECTED_DAYMET_FEATURE_COUNT
        or tuple(sentinel_names) != EXPECTED_SENTINEL_FEATURES
        or [*base_names, *daymet_names, *sentinel_names] != model_names
        or len(model_names) != EXPECTED_MODEL_FEATURE_COUNT
        or len(model_names) != len(set(model_names))
        or any("geoid" in name.casefold() for name in model_names)
    ):
        raise FinalTestPredictorAssemblyError(
            "Requested predictor families do not equal frozen M2."
        )
    expected_base_columns = [
        *KEY_COLUMNS,
        *BASE_AUDIT_COLUMNS,
        *base_names,
    ]
    expected_daymet_columns = [*KEY_COLUMNS, *daymet_names]
    expected_sentinel_columns = [*SENTINEL_SOURCE_KEY_COLUMNS, *sentinel_names]
    for frame, columns, label in (
        (base, expected_base_columns, "Predictor base"),
        (daymet, expected_daymet_columns, "Daymet"),
        (sentinel, expected_sentinel_columns, "Sentinel"),
    ):
        if frame.columns.duplicated().any() or frame.columns.tolist() != columns:
            raise FinalTestPredictorAssemblyError(
                f"{label} schema must be exactly {columns}."
            )

    base_keys = _normalize_keys(
        base, label="Predictor base", production_shape=production_shape
    )
    daymet_keys = _normalize_keys(
        daymet, label="Daymet", production_shape=production_shape
    )
    sentinel_keys = _normalize_keys(
        sentinel, label="Sentinel", production_shape=production_shape
    )
    _assert_exact_keys(base_keys, daymet_keys, label="Daymet")
    _assert_exact_keys(base_keys, sentinel_keys, label="Sentinel")

    base_numeric = _numeric_frame(
        base, base_names, label="Predictor base", allow_missing=False
    )
    daymet_numeric = _numeric_frame(
        daymet, daymet_names, label="Daymet", allow_missing=False
    )
    sentinel_numeric = _numeric_frame(
        sentinel, sentinel_names, label="Sentinel", allow_missing=True
    )
    sentinel_missing = sentinel_numeric.isna().to_numpy()
    all_present = ~sentinel_missing.any(axis=1)
    all_missing = sentinel_missing.all(axis=1)
    if not np.logical_or(all_present, all_missing).all():
        raise FinalTestPredictorAssemblyError(
            "Sentinel rows must have all five predictors present or all five missing."
        )

    def keyed_numeric(
        source_keys: pd.DataFrame,
        numeric: pd.DataFrame,
    ) -> pd.DataFrame:
        result = source_keys.reset_index(drop=True).copy()
        for name in numeric:
            result[name] = numeric[name].reset_index(drop=True)
        return result

    assembled = keyed_numeric(base_keys, base_numeric)
    for working in (
        keyed_numeric(daymet_keys, daymet_numeric),
        keyed_numeric(sentinel_keys, sentinel_numeric),
    ):
        assembled = assembled.merge(
            working,
            on=list(KEY_COLUMNS),
            how="left",
            sort=False,
            validate="one_to_one",
        )
    assembled = assembled.loc[:, [*KEY_COLUMNS, *model_names]].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    )
    assembled = assembled.reset_index(drop=True)
    if (
        assembled.columns.tolist() != [*KEY_COLUMNS, *model_names]
        or assembled.duplicated(list(KEY_COLUMNS)).any()
        or len(assembled) != len(base_keys)
    ):
        raise FinalTestPredictorAssemblyError("Final predictor join changed keys/schema.")
    _numeric_frame(
        assembled, base_names, label="Final base", allow_missing=False
    )
    _numeric_frame(
        assembled, daymet_names, label="Final Daymet", allow_missing=False
    )
    _numeric_frame(
        assembled, sentinel_names, label="Final Sentinel", allow_missing=True
    )

    family_by_name = {
        **{name: "static_calendar" for name in base_names},
        **{name: "weather" for name in daymet_names},
        **{name: "satellite" for name in sentinel_names},
    }
    missingness = pd.DataFrame(
        [
            {
                "feature_name": name,
                "family": family_by_name[name],
                "row_count": len(assembled),
                "non_missing_count": int(assembled[name].notna().sum()),
                "missing_count": int(assembled[name].isna().sum()),
                "missing_fraction": float(assembled[name].isna().mean()),
                "missing_allowed": name in sentinel_names,
            }
            for name in model_names
        ]
    )
    return assembled, missingness


def _read_parquet_stable(
    path: Path,
    *,
    record: Mapping[str, Any],
    label: str,
) -> pd.DataFrame:
    before = sha256_file(path)
    if before != record.get("sha256"):
        raise FinalTestPredictorAssemblyError(f"{label} changed before read.")
    frame = pd.read_parquet(path)
    if sha256_file(path) != before:
        raise FinalTestPredictorAssemblyError(f"{label} changed while being read.")
    return frame


def _read_csv_stable(
    path: Path,
    *,
    record: Mapping[str, Any],
    label: str,
) -> pd.DataFrame:
    before = sha256_file(path)
    if before != record.get("sha256"):
        raise FinalTestPredictorAssemblyError(f"{label} changed before read.")
    frame = pd.read_csv(path)
    if sha256_file(path) != before:
        raise FinalTestPredictorAssemblyError(f"{label} changed while being read.")
    return frame


def _csv_file_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(name, str(dtype)) for name, dtype in frame.dtypes.items()]
        ),
        "semantic_sha256": canonical_frame_sha256(
            frame, sort_by=["feature_name"]
        ),
    }


def _request_payload(
    *,
    formal_path: Path,
    base_path: Path,
    base_provenance_path: Path,
    daymet_path: Path,
    daymet_provenance_path: Path,
    sentinel_path: Path,
    sentinel_progress_path: Path,
    sentinel_pipeline_path: Path,
    sentinel_audit_path: Path,
    research_config_path: Path,
    sentinel_stage_config_path: Path,
    sentinel_inventory_directory: Path,
    sentinel_raw_stac_directory: Path,
    output: Path,
    marker: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "final_test_year": FINAL_TEST_YEAR,
        "formal_model_lock_path": str(formal_path),
        "predictor_base_path": str(base_path),
        "predictor_base_provenance_path": str(base_provenance_path),
        "daymet_feature_path": str(daymet_path),
        "daymet_provenance_path": str(daymet_provenance_path),
        "sentinel_feature_path": str(sentinel_path),
        "sentinel_build_progress_path": str(sentinel_progress_path),
        "sentinel_pipeline_fingerprint_path": str(sentinel_pipeline_path),
        "sentinel_postrun_audit_path": str(sentinel_audit_path),
        "research_config_path": str(research_config_path),
        "sentinel_stage_config_path": str(sentinel_stage_config_path),
        "sentinel_inventory_directory": str(sentinel_inventory_directory),
        "sentinel_raw_stac_directory": str(sentinel_raw_stac_directory),
        "output_directory": str(output),
        "predictor_output_path": str(output / OUTPUT_FILENAME),
        "missingness_output_path": str(output / MISSINGNESS_FILENAME),
        "internal_provenance_path": str(output / INTERNAL_PROVENANCE_FILENAME),
        "external_provenance_path": str(marker),
        "expected_row_count": EXPECTED_ROW_COUNT,
        "expected_model_feature_count": EXPECTED_MODEL_FEATURE_COUNT,
    }


def _validate_published_outputs(
    predictors: pd.DataFrame,
    missingness: pd.DataFrame,
    payload: Mapping[str, Any],
) -> None:
    feature_names = payload.get("feature_names")
    if (
        not isinstance(feature_names, list)
        or predictors.columns.tolist() != [*KEY_COLUMNS, *feature_names]
        or len(feature_names) != EXPECTED_MODEL_FEATURE_COUNT
        or "tract_geoid" in feature_names
    ):
        raise FinalTestPredictorAssemblyError("Published predictor schema drifted.")
    _normalize_keys(predictors, label="Published predictors", production_shape=True)
    if (
        missingness["feature_name"].tolist() != feature_names
        or len(missingness) != EXPECTED_MODEL_FEATURE_COUNT
    ):
        raise FinalTestPredictorAssemblyError("Published missingness report drifted.")
    observed = predictors.loc[:, feature_names].isna().sum()
    reported = missingness.set_index("feature_name")["missing_count"]
    if any(int(observed[name]) != int(reported[name]) for name in feature_names):
        raise FinalTestPredictorAssemblyError(
            "Published missingness counts disagree with predictors."
        )
    base_names = feature_names[:EXPECTED_BASE_FEATURE_COUNT]
    daymet_names = feature_names[
        EXPECTED_BASE_FEATURE_COUNT : EXPECTED_BASE_FEATURE_COUNT
        + EXPECTED_DAYMET_FEATURE_COUNT
    ]
    sentinel_names = feature_names[-len(EXPECTED_SENTINEL_FEATURES) :]
    _numeric_frame(
        predictors,
        [*base_names, *daymet_names],
        label="Published non-Sentinel",
        allow_missing=False,
    )
    sentinel_numeric = _numeric_frame(
        predictors,
        sentinel_names,
        label="Published Sentinel",
        allow_missing=True,
    )
    sentinel_missing = sentinel_numeric.isna().to_numpy()
    if not np.logical_or(
        ~sentinel_missing.any(axis=1), sentinel_missing.all(axis=1)
    ).all():
        raise FinalTestPredictorAssemblyError(
            "Published Sentinel missingness is not all-or-none."
        )


def _authenticate_existing(
    marker: Path,
    *,
    expected_request: Mapping[str, Any],
    root: Path,
) -> dict[str, Any] | None:
    if not marker.exists():
        return None
    payload, marker_sha256 = _read_json_stable(
        marker, label="Final predictor provenance"
    )
    _verify_commit(payload, label="Final predictor provenance")
    _require_blind_flags(payload, label="Final predictors")
    if (
        payload.get("publication_protocol") != "staged_directory_atomic_replace_v1"
        or payload.get("request") != dict(expected_request)
        or payload.get("request_sha256") != canonical_sha256(expected_request)
        or payload.get("feature_count") != EXPECTED_MODEL_FEATURE_COUNT
        or payload.get("row_count") != EXPECTED_ROW_COUNT
        or payload.get("geoid_used_as_feature") is not False
        or payload.get("contains_only_final_test_year") is not True
        or payload.get("contains_target_values") is not False
        or payload.get("contains_target_or_qa_values") is not False
        or payload.get("contains_model_outputs") is not False
    ):
        raise FinalTestPredictorAssemblyError(
            "Existing final predictors belong to another or unsafe request."
        )
    pipeline = payload.get("pipeline_fingerprint")
    pipeline_sha256 = payload.get("pipeline_sha256")
    if not isinstance(pipeline, Mapping) or not isinstance(pipeline_sha256, str):
        raise FinalTestPredictorAssemblyError("Final predictor pipeline lock is invalid.")
    _verify_current_pipeline(root, pipeline_sha256, pipeline)
    immutable = payload.get("immutable_input_files")
    if not isinstance(immutable, list):
        raise FinalTestPredictorAssemblyError("Final predictor input locks are invalid.")
    _verify_snapshot(immutable)
    formal_path = Path(str(expected_request["formal_model_lock_path"]))
    formal, formal_sha256 = authenticate_formal_model_lock(formal_path)
    _, _, _, formal_model_names = _model_feature_contract(formal)
    sentinel_progress_path = Path(
        str(expected_request["sentinel_build_progress_path"])
    )
    sentinel_pipeline_path = Path(
        str(expected_request["sentinel_pipeline_fingerprint_path"])
    )
    sentinel_audit_path = Path(
        str(expected_request["sentinel_postrun_audit_path"])
    )
    sentinel_progress, sentinel_progress_sha256 = _read_json_stable(
        sentinel_progress_path, label="Final-test Sentinel build progress"
    )
    (
        sentinel_audit,
        _,
        sentinel_audit_commit,
        sentinel_audit_sha256,
    ) = _authenticate_sentinel_postrun_audit(
        path=sentinel_audit_path,
        sentinel_path=Path(str(expected_request["sentinel_feature_path"])),
        progress_path=sentinel_progress_path,
        pipeline_path=sentinel_pipeline_path,
        progress=sentinel_progress,
        progress_sha256=sentinel_progress_sha256,
        formal_sha256=formal_sha256,
        formal_commit=str(formal["commit_sha256"]),
        root=root,
    )
    upstream_commits = payload.get("upstream_commits")
    recorded_audit = (
        upstream_commits.get("sentinel_postrun_audit")
        if isinstance(upstream_commits, Mapping)
        else None
    )
    if (
        payload.get("feature_names") != formal_model_names
        or payload.get("formal_model_lock")
        != {
            "path": str(formal_path),
            "sha256": formal_sha256,
            "commit_sha256": formal["commit_sha256"],
        }
        or payload.get("sentinel_postrun_audit_passed") is not True
        or payload.get("sentinel_postrun_audit_commit_sha256")
        != sentinel_audit_commit
        or not isinstance(recorded_audit, Mapping)
        or dict(recorded_audit)
        != {
            "path": str(sentinel_audit_path),
            "sha256": sentinel_audit_sha256,
            "commit_sha256": sentinel_audit_commit,
            "audit_pipeline_sha256": sentinel_audit["audit_pipeline_sha256"],
        }
    ):
        raise FinalTestPredictorAssemblyError(
            "Published predictors disagree with the active model/Sentinel audit locks."
        )
    outputs = payload.get("output_files")
    expected_outputs = {
        OUTPUT_FILENAME: Path(str(expected_request["predictor_output_path"])),
        MISSINGNESS_FILENAME: Path(str(expected_request["missingness_output_path"])),
    }
    if not isinstance(outputs, Mapping) or set(outputs) != set(expected_outputs):
        raise FinalTestPredictorAssemblyError("Final predictor output set drifted.")
    authenticated_outputs: dict[str, Mapping[str, Any]] = {}
    for name, path in expected_outputs.items():
        record = outputs[name]
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(f"Final output {name} is unlocked.")
        _snapshot_record(
            path,
            expected=record,
            label=f"Final output {name}",
            require_recorded_path=True,
        )
        authenticated_outputs[name] = record
    internal_path = Path(str(expected_request["internal_provenance_path"]))
    internal_sha256 = marker_sha256
    if marker.resolve() != internal_path.resolve():
        internal, internal_sha256 = _read_json_stable(
            internal_path, label="Internal final predictor provenance"
        )
        if internal != payload:
            raise FinalTestPredictorAssemblyError(
                "Internal/external final predictor commits disagree."
            )
    predictors = _read_parquet_stable(
        expected_outputs[OUTPUT_FILENAME],
        record=authenticated_outputs[OUTPUT_FILENAME],
        label="Published predictor table",
    )
    missingness = _read_csv_stable(
        expected_outputs[MISSINGNESS_FILENAME],
        record=authenticated_outputs[MISSINGNESS_FILENAME],
        label="Published predictor missingness",
    )
    if (
        canonical_frame_sha256(
            predictors, sort_by=["target_date", "tract_geoid"]
        )
        != payload.get("semantic_predictor_table_sha256")
        or canonical_frame_sha256(missingness, sort_by=["feature_name"])
        != payload.get("semantic_missingness_table_sha256")
    ):
        raise FinalTestPredictorAssemblyError("Final predictor semantic hash failed.")
    _validate_published_outputs(predictors, missingness, payload)
    _verify_snapshot(immutable)
    _verify_current_pipeline(root, pipeline_sha256, pipeline)
    for name, path in expected_outputs.items():
        _snapshot_record(
            path,
            expected=authenticated_outputs[name],
            label=f"Final output {name}",
            require_recorded_path=True,
        )
    final_marker, final_marker_sha256 = _read_json_stable(
        marker, label="Final predictor provenance"
    )
    if final_marker != payload or final_marker_sha256 != marker_sha256:
        raise FinalTestPredictorAssemblyError(
            "Final predictor provenance changed during authentication."
        )
    final_internal, final_internal_sha256 = _read_json_stable(
        internal_path, label="Internal final predictor provenance"
    )
    if final_internal != payload or final_internal_sha256 != internal_sha256:
        raise FinalTestPredictorAssemblyError(
            "Internal predictor provenance changed during authentication."
        )
    return payload


def _publish_staged(
    staging: Path,
    output: Path,
    payload: dict[str, Any],
    marker: Path,
    *,
    marker_writer: MarkerWriter = atomic_json,
) -> None:
    if (
        not staging.is_dir()
        or staging.parent.resolve() != output.parent.resolve()
        or not staging.name.startswith(f".{output.name}.staging-")
    ):
        raise FinalTestPredictorAssemblyError("Predictor staging directory is not owned.")
    internal, _ = _read_json_stable(
        staging / INTERNAL_PROVENANCE_FILENAME,
        label="Staged final predictor provenance",
    )
    _verify_commit(internal, label="Staged final predictor provenance")
    if internal != payload:
        raise FinalTestPredictorAssemblyError(
            "Staged final predictor provenance changed."
        )
    outputs = payload.get("output_files")
    if not isinstance(outputs, Mapping):
        raise FinalTestPredictorAssemblyError("Staged output records are invalid.")
    for name in (OUTPUT_FILENAME, MISSINGNESS_FILENAME):
        record = outputs.get(name)
        if not isinstance(record, Mapping):
            raise FinalTestPredictorAssemblyError(f"Staged output {name} is unlocked.")
        final_path = output / name
        if Path(str(record.get("path", ""))).resolve() != final_path.resolve():
            raise FinalTestPredictorAssemblyError(f"Staged output {name} path drifted.")
        _snapshot_record(
            staging / name,
            expected=record,
            label=f"Staged output {name}",
        )
    if output.exists():
        raise FinalTestPredictorAssemblyError("Final predictor output already exists.")
    staging.replace(output)
    marker_writer(payload, marker)


def _recover_published(
    output: Path,
    marker: Path,
    *,
    request: Mapping[str, Any],
    root: Path,
    marker_writer: MarkerWriter = atomic_json,
) -> dict[str, Any]:
    if not output.is_dir():
        raise FinalTestPredictorAssemblyError(
            "Uncommitted final predictor output is not a recoverable directory."
        )
    internal_path = output / INTERNAL_PROVENANCE_FILENAME
    payload = _authenticate_existing(internal_path, expected_request=request, root=root)
    if payload is None:
        raise FinalTestPredictorAssemblyError(
            "Uncommitted final predictor output lacks recovery provenance."
        )
    marker_writer(payload, marker)
    recovered = _authenticate_existing(marker, expected_request=request, root=root)
    if recovered is None:
        raise AssertionError("Recovered final predictor provenance was not published.")
    return recovered


def build_final_test_predictor_artifacts(
    *,
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    predictor_base_path: str | Path = DEFAULT_BASE_PATH,
    predictor_base_provenance_path: str | Path = DEFAULT_BASE_PROVENANCE_PATH,
    daymet_feature_path: str | Path = DEFAULT_DAYMET_PATH,
    daymet_provenance_path: str | Path = DEFAULT_DAYMET_PROVENANCE_PATH,
    sentinel_feature_path: str | Path = DEFAULT_SENTINEL_PATH,
    sentinel_progress_path: str | Path = DEFAULT_SENTINEL_PROGRESS_PATH,
    sentinel_pipeline_path: str | Path = DEFAULT_SENTINEL_PIPELINE_PATH,
    sentinel_audit_path: str | Path = DEFAULT_SENTINEL_AUDIT_PATH,
    research_config_path: str | Path = DEFAULT_RESEARCH_CONFIG_PATH,
    sentinel_stage_config_path: str | Path = DEFAULT_SENTINEL_STAGE_CONFIG_PATH,
    sentinel_inventory_directory: str | Path = DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    sentinel_raw_stac_directory: str | Path = DEFAULT_SENTINEL_RAW_STAC_DIRECTORY,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    provenance_path: str | Path = DEFAULT_PROVENANCE_PATH,
    marker_writer: MarkerWriter = atomic_json,
) -> dict[str, Any]:
    """Authenticate, join, and atomically freeze all 46 blind M2 predictors."""

    root = _project_root()
    formal_path = _resolve(root, formal_lock_path)
    base_path = _resolve(root, predictor_base_path)
    base_provenance_path = _resolve(root, predictor_base_provenance_path)
    daymet_path = _resolve(root, daymet_feature_path)
    daymet_provenance_path = _resolve(root, daymet_provenance_path)
    sentinel_path = _resolve(root, sentinel_feature_path)
    sentinel_progress_path = _resolve(root, sentinel_progress_path)
    sentinel_pipeline_path = _resolve(root, sentinel_pipeline_path)
    sentinel_audit_path = _resolve(root, sentinel_audit_path)
    research_config_path = _resolve(root, research_config_path)
    sentinel_stage_config_path = _resolve(root, sentinel_stage_config_path)
    sentinel_inventory_directory = _resolve(root, sentinel_inventory_directory)
    sentinel_raw_stac_directory = _resolve(root, sentinel_raw_stac_directory)
    output = _resolve(root, output_directory)
    marker = _resolve(root, provenance_path)
    direct_input_paths = {
        formal_path,
        base_path,
        base_provenance_path,
        daymet_path,
        daymet_provenance_path,
        sentinel_path,
        sentinel_progress_path,
        sentinel_pipeline_path,
        sentinel_audit_path,
        research_config_path,
        sentinel_stage_config_path,
    }
    if marker in direct_input_paths:
        raise FinalTestPredictorAssemblyError(
            "External provenance path collides with an immutable input."
        )
    if any(path == output or output in path.parents for path in direct_input_paths):
        raise FinalTestPredictorAssemblyError(
            "Atomic output directory contains or equals an immutable input."
        )
    if output == sentinel_inventory_directory or output == sentinel_raw_stac_directory:
        raise FinalTestPredictorAssemblyError(
            "Atomic output directory collides with a Sentinel input directory."
        )
    try:
        marker.relative_to(output)
        marker_inside_output = True
    except ValueError:
        marker_inside_output = False
    if marker_inside_output:
        raise FinalTestPredictorAssemblyError(
            "External provenance must be outside the atomic output directory."
        )
    request = _request_payload(
        formal_path=formal_path,
        base_path=base_path,
        base_provenance_path=base_provenance_path,
        daymet_path=daymet_path,
        daymet_provenance_path=daymet_provenance_path,
        sentinel_path=sentinel_path,
        sentinel_progress_path=sentinel_progress_path,
        sentinel_pipeline_path=sentinel_pipeline_path,
        sentinel_audit_path=sentinel_audit_path,
        research_config_path=research_config_path,
        sentinel_stage_config_path=sentinel_stage_config_path,
        sentinel_inventory_directory=sentinel_inventory_directory,
        sentinel_raw_stac_directory=sentinel_raw_stac_directory,
        output=output,
        marker=marker,
    )
    existing = _authenticate_existing(marker, expected_request=request, root=root)
    if existing is not None:
        return existing
    if output.exists():
        return _recover_published(
            output,
            marker,
            request=request,
            root=root,
            marker_writer=marker_writer,
        )

    # Fail before opening any predictor parquet unless every upstream completion
    # marker exists.  No Landsat target/QA/score path is accepted by this API.
    for label, required in (
        ("formal model lock", formal_path),
        ("predictor-base provenance", base_provenance_path),
        ("Daymet provenance", daymet_provenance_path),
        ("Sentinel build progress", sentinel_progress_path),
        ("Sentinel pipeline fingerprint", sentinel_pipeline_path),
        ("Sentinel post-run safety audit", sentinel_audit_path),
    ):
        if not required.is_file():
            raise FileNotFoundError(f"Missing {label}: {required}")

    pipeline_sha256, pipeline = _current_pipeline(root)
    formal, formal_sha256 = authenticate_formal_model_lock(formal_path)
    formal_commit = str(formal["commit_sha256"])
    base_names, daymet_names, sentinel_names, model_names = _model_feature_contract(
        formal
    )
    base_provenance, base_snapshots, base_commit = _authenticate_base(
        path=base_path,
        provenance_path=base_provenance_path,
        formal_path=formal_path,
        formal_sha256=formal_sha256,
        formal_commit=formal_commit,
        feature_names=base_names,
        root=root,
    )
    daymet_provenance, daymet_snapshots, daymet_commit = _authenticate_daymet(
        path=daymet_path,
        provenance_path=daymet_provenance_path,
        formal_path=formal_path,
        formal_sha256=formal_sha256,
        formal_commit=formal_commit,
        feature_names=daymet_names,
        base_provenance=base_provenance,
        root=root,
    )
    sentinel_progress, sentinel_snapshots, sentinel_progress_sha256 = (
        _authenticate_sentinel(
            path=sentinel_path,
            progress_path=sentinel_progress_path,
            pipeline_path=sentinel_pipeline_path,
            research_config_path=research_config_path,
            stage_config_path=sentinel_stage_config_path,
            formal_path=formal_path,
            landsat_inventory_directory=Path(
                str(
                    base_provenance["inputs"]["landsat_inventory"]["path"]
                )
            ).resolve().parent,
            sentinel_inventory_directory=sentinel_inventory_directory,
            raw_stac_directory=sentinel_raw_stac_directory,
            formal_sha256=formal_sha256,
            formal_commit=formal_commit,
            feature_names=sentinel_names,
            base_provenance=base_provenance,
            root=root,
        )
    )
    (
        sentinel_audit,
        sentinel_audit_snapshots,
        sentinel_audit_commit,
        sentinel_audit_sha256,
    ) = _authenticate_sentinel_postrun_audit(
        path=sentinel_audit_path,
        sentinel_path=sentinel_path,
        progress_path=sentinel_progress_path,
        pipeline_path=sentinel_pipeline_path,
        progress=sentinel_progress,
        progress_sha256=sentinel_progress_sha256,
        formal_sha256=formal_sha256,
        formal_commit=formal_commit,
        root=root,
    )
    immutable_inputs = _deduplicate_snapshot(
        [
            _snapshot_record(formal_path, label="Formal model lock"),
            *base_snapshots,
            *daymet_snapshots,
            *sentinel_snapshots,
            *sentinel_audit_snapshots,
        ]
    )
    _verify_snapshot(immutable_inputs)

    base_record = base_provenance["output_files"][base_path.name]
    daymet_record = daymet_provenance["output_files"][daymet_path.name]
    sentinel_record = sentinel_progress["aggregate_outputs"][sentinel_path.name]
    base = _read_parquet_stable(
        base_path, record=base_record, label="Predictor-base table"
    )
    daymet = _read_parquet_stable(
        daymet_path, record=daymet_record, label="Final-test Daymet table"
    )
    sentinel = _read_parquet_stable(
        sentinel_path, record=sentinel_record, label="Final-test Sentinel table"
    )
    if (
        canonical_frame_sha256(base, sort_by=["target_date", "tract_geoid"])
        != base_provenance.get("semantic_table_sha256")
        or canonical_frame_sha256(daymet, sort_by=["target_date", "tract_geoid"])
        != daymet_provenance.get("semantic_feature_table_sha256")
    ):
        raise FinalTestPredictorAssemblyError(
            "Upstream predictor semantic table lock failed."
        )
    predictors, missingness = assemble_final_predictor_frame(
        base,
        daymet,
        sentinel,
        base_feature_names=base_names,
        daymet_feature_names=daymet_names,
        sentinel_feature_names=sentinel_names,
        model_feature_names=model_names,
        production_shape=True,
    )
    sentinel_missing_rows = int(
        predictors.loc[:, sentinel_names].isna().all(axis=1).sum()
    )
    expected_sentinel_missing = (
        EXPECTED_ROW_COUNT - int(sentinel_progress["feature_available_row_count"])
    )
    if sentinel_missing_rows != expected_sentinel_missing:
        raise FinalTestPredictorAssemblyError(
            "Sentinel missingness disagrees with authenticated build progress."
        )
    _verify_snapshot(immutable_inputs)
    _verify_current_pipeline(root, pipeline_sha256, pipeline)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        staged_predictor = staging / OUTPUT_FILENAME
        staged_missingness = staging / MISSINGNESS_FILENAME
        atomic_parquet(predictors, staged_predictor)
        atomic_csv(missingness, staged_missingness)
        frozen_predictors = pd.read_parquet(staged_predictor)
        frozen_missingness = pd.read_csv(staged_missingness)
        _validate_published_outputs(
            frozen_predictors,
            frozen_missingness,
            {"feature_names": model_names},
        )
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "complete_target_blind",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "final_test_year": FINAL_TEST_YEAR,
            "final_test_unlocked": False,
            "contains_only_final_test_year": True,
            "contains_final_test_year": True,
            "target_blind": True,
            "contains_target_values": False,
            "contains_target_or_qa_values": False,
            "contains_model_outputs": False,
            "target_or_qa_tables_read": [],
            "target_or_qa_value_columns_read": [],
            "target_values_read": False,
            "models_loaded": False,
            "model_scores_read": False,
            "one_time_evaluation_consumed": False,
            "publication_protocol": "staged_directory_atomic_replace_v1",
            "request": request,
            "request_sha256": canonical_sha256(request),
            "row_count": len(predictors),
            "date_count": int(predictors["target_date"].nunique()),
            "tract_count": int(predictors["tract_geoid"].nunique()),
            "feature_count": len(model_names),
            "feature_names": model_names,
            "key_columns": list(KEY_COLUMNS),
            "geoid_used_as_feature": False,
            "family_feature_counts": {
                "static_calendar": len(base_names),
                "weather": len(daymet_names),
                "satellite": len(sentinel_names),
            },
            "missing_count_by_feature": {
                row.feature_name: int(row.missing_count)
                for row in missingness.itertuples(index=False)
            },
            "sentinel_available_row_count": int(
                sentinel_progress["feature_available_row_count"]
            ),
            "sentinel_missing_row_count": sentinel_missing_rows,
            "sentinel_all_or_none_missingness": True,
            "sentinel_postrun_audit_passed": (
                sentinel_audit["safe_for_final_predictor_assembly"]
            ),
            "sentinel_postrun_audit_commit_sha256": sentinel_audit_commit,
            "non_sentinel_missing_count": int(
                predictors.loc[:, [*base_names, *daymet_names]].isna().sum().sum()
            ),
            "semantic_key_sha256": canonical_frame_sha256(
                predictors.loc[:, list(KEY_COLUMNS)],
                sort_by=["target_date", "tract_geoid"],
            ),
            "semantic_predictor_table_sha256": canonical_frame_sha256(
                predictors, sort_by=["target_date", "tract_geoid"]
            ),
            "semantic_missingness_table_sha256": canonical_frame_sha256(
                frozen_missingness, sort_by=["feature_name"]
            ),
            "formal_model_lock": {
                "path": str(formal_path),
                "sha256": formal_sha256,
                "commit_sha256": formal_commit,
            },
            "upstream_commits": {
                "predictor_base": {
                    "path": str(base_provenance_path),
                    "sha256": sha256_file(base_provenance_path),
                    "commit_sha256": base_commit,
                },
                "daymet_features": {
                    "path": str(daymet_provenance_path),
                    "sha256": sha256_file(daymet_provenance_path),
                    "commit_sha256": daymet_commit,
                },
                "sentinel_build_progress": {
                    "path": str(sentinel_progress_path),
                    "sha256": sentinel_progress_sha256,
                    "pipeline_sha256": sentinel_progress[
                        "final_test_sentinel_feature_pipeline_sha256"
                    ],
                },
                "sentinel_postrun_audit": {
                    "path": str(sentinel_audit_path),
                    "sha256": sentinel_audit_sha256,
                    "commit_sha256": sentinel_audit_commit,
                    "audit_pipeline_sha256": sentinel_audit[
                        "audit_pipeline_sha256"
                    ],
                },
            },
            "immutable_input_files": immutable_inputs,
            "pipeline_sha256": pipeline_sha256,
            "pipeline_fingerprint": pipeline,
            "output_files": {
                OUTPUT_FILENAME: {
                    "path": str(output / OUTPUT_FILENAME),
                    **parquet_file_record(staged_predictor, frozen_predictors),
                },
                MISSINGNESS_FILENAME: {
                    "path": str(output / MISSINGNESS_FILENAME),
                    **_csv_file_record(staged_missingness, frozen_missingness),
                },
            },
            "remaining_gate": (
                "Keep all Landsat target/QA values closed until the separate one-time "
                "evaluation authorization and consumption protocol is complete."
            ),
        }
        payload["commit_sha256"] = canonical_sha256(payload)
        atomic_json(payload, staging / INTERNAL_PROVENANCE_FILENAME)
        _verify_snapshot(immutable_inputs)
        _verify_current_pipeline(root, pipeline_sha256, pipeline)
        _publish_staged(
            staging,
            output,
            payload,
            marker,
            marker_writer=marker_writer,
        )
        published = _authenticate_existing(marker, expected_request=request, root=root)
        if published is None:
            raise AssertionError("Final predictor provenance was not published.")
        return published
    finally:
        if staging.exists():
            if (
                staging.parent.resolve() != output.parent.resolve()
                or not staging.name.startswith(f".{output.name}.staging-")
            ):
                raise FinalTestPredictorAssemblyError(
                    "Refusing to clean an unowned predictor staging directory."
                )
            shutil.rmtree(staging)
