"""Authenticate the durable canary chunk and correct its provenance counter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_predictor_sentinel_static_assembly_v1 as assembly
from la_heat.multicity import m3_blind_predictor_static_tile_scope_repair_v1 as parent
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-canary-counter-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_CANARY_COUNTER_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ASSEMBLY_CANARY_COMPLETE.json"
)
OLD_CANARY_PATH: Final = assembly.CANARY_PATH
PROGRESS_PATH: Final = (
    assembly.RUNTIME_ROOT
    / "components/miami_fl/static/gshhg_distance_progress.json"
)
EXPECTED_PARENT_COMMIT: Final = (
    "fb87086fda0b1be1c44e3cd7ea51d9754b70b249372e06f87eb272836254d426"
)
EXPECTED_OLD_CANARY_COMMIT: Final = (
    "5dbd2524b6f00f71d40d97ff143343e986b363ccab09275316a6eb2ebc685f59"
)
CODE_PATHS: Final = (
    "scripts/repair_m3_blind_predictor_canary_counter_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_canary_counter_repair_v1.py",
)


class M3BlindCanaryCounterRepairError(RuntimeError):
    """Raised when the provenance-only counter repair cannot be authenticated."""


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindCanaryCounterRepairError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict):
        raise M3BlindCanaryCounterRepairError(f"{label} must be an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindCanaryCounterRepairError(f"{label} commit is invalid.")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindCanaryCounterRepairError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Bind the incident and repair code without opening the Parquet chunk."""

    root = Path(project_root).resolve()
    parent_auth = parent.authenticate_authorization(root)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindCanaryCounterRepairError("Parent repair authorization changed.")
    old = _read_committed(root / OLD_CANARY_PATH, label="old canary")
    progress = _read_committed(root / PROGRESS_PATH, label="distance progress")
    if (
        old.get("commit_sha256") != EXPECTED_OLD_CANARY_COMMIT
        or old.get("gshhg_completed_chunk_count") != 0
        or progress.get("city_id") != "miami_fl"
        or progress.get("chunk_count") != 1
        or progress.get("completed_chunk_indices") != [1]
    ):
        raise M3BlindCanaryCounterRepairError("Counter incident evidence changed.")
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_canary_counter_repair_authorized",
        "parent_authorization": {
            **_record(root, parent.AUTHORIZATION_PATH),
            "commit_sha256": parent_auth["commit_sha256"],
        },
        "incident_evidence": {
            "old_canary": {**_record(root, OLD_CANARY_PATH), "commit_sha256": old["commit_sha256"]},
            "distance_progress": {
                **_record(root, PROGRESS_PATH),
                "commit_sha256": progress["commit_sha256"],
            },
            "wrong_field": "completed_chunks",
            "canonical_field": "completed_chunk_indices",
            "recorded_count": 0,
            "expected_count_after_file_authentication": 1,
        },
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "permissions": {
            "read_only_recorded_gshhg_chunk": True,
            "write_new_append_only_corrected_canary_completion": True,
            "modify_or_recompute_existing_outputs": False,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
            "start_full_assembly": False,
        },
        "authorization_audit": {
            "parquet_chunks_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "blind_targets_sealed": True,
        },
        "completion": COMPLETION_PATH.as_posix(),
        "next_safe_stage": "authenticate_existing_chunk_and_write_corrected_canary_completion",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if _read_committed(path, label="counter repair authorization") != expected:
            raise M3BlindCanaryCounterRepairError("Append-only authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH, label="counter repair authorization")
    if observed != build_authorization(root):
        raise M3BlindCanaryCounterRepairError("Counter repair authorization drifted.")
    return observed


def repair(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    progress = _read_committed(root / PROGRESS_PATH, label="distance progress")
    record = progress["chunks"]["1"]
    chunk_path = (root / str(record["path"])).resolve()
    authenticate_authorization(root)
    if (
        not chunk_path.is_relative_to(root)
        or not chunk_path.is_file()
        or chunk_path.stat().st_size != record.get("bytes")
        or sha256_file(chunk_path) != record.get("sha256")
    ):
        raise M3BlindCanaryCounterRepairError("Durable GSHHG chunk changed.")
    authenticate_authorization(root)
    frame = pd.read_parquet(chunk_path)
    if (
        list(frame.columns) != ["flat_index", "tract_geoid", "distance_km"]
        or len(frame) != record.get("rows")
        or frame["flat_index"].duplicated().any()
        or not frame["distance_km"].notna().all()
    ):
        raise M3BlindCanaryCounterRepairError("Durable GSHHG chunk schema changed.")
    old = _read_committed(root / OLD_CANARY_PATH, label="old canary")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_sentinel_static_offline_assembly_canary_complete",
        "authorization_commit_sha256": permit["commit_sha256"],
        "supersedes_canary_commit_sha256": old["commit_sha256"],
        "city_id": "miami_fl",
        "sentinel_city_compile": True,
        "static_base": True,
        "gshhg_completed_chunk_count": 1,
        "gshhg_chunk_count": 1,
        "gshhg_chunk": {**record, "schema_columns": list(frame.columns)},
        "network_request_count": 0,
        "href_read_count": 0,
        "blind_targets_sealed": True,
        "daymet_landsat_qa_or_target_access": False,
        "model_fit_predict_score_or_evaluate": False,
        "existing_outputs_modified_or_recomputed": False,
        "full_assembly_started": False,
        "next_safe_stage": "authorize_launch_of_full_four_city_offline_assembly",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    path = root / COMPLETION_PATH
    if path.exists():
        if _read_committed(path, label="corrected canary completion") != payload:
            raise M3BlindCanaryCounterRepairError("Corrected completion drifted.")
    else:
        atomic_json(payload, path)
    return payload
