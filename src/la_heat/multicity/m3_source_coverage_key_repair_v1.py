"""Append-only authorization for the M3 coverage-key compatibility repair.

The authenticated v2 logical cache returns the coverage mask under the legacy
key ``source_coverage`` while ``aligned_landsat`` requires
``_source_coverage``.  This module authorizes only an in-memory key rename
after the existing loader has authenticated every physical TIFF.  It never
opens a raster, rewrites a cache, rebuilds a queue, or broadens the source-only
QA scope.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.aligned_landsat import COVERAGE_KEY, REQUIRED_ASSETS
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    EXPECTED_TASK_COUNT,
    load_runner_settings_v2,
)
from la_heat.multicity.m3_source_integrity_v2 import (
    authenticate_logical_global_cache,
    authenticate_m3_source_integrity_v2_authorization,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-coverage-key-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_COVERAGE_KEY_REPAIR_V1_AUTHORIZATION.json"
)

LEGACY_COVERAGE_KEY: Final = "source_coverage"
V2_AUTHORIZATION_COMMIT_SHA256: Final = (
    "8fcfbcd308bec8e218252389a39b746e7785f076f03e47f3298e3d9f13fc0677"
)
LOGICAL_CACHE_COMPLETION_COMMIT_SHA256: Final = (
    "4834d19192e1cb3c61013577bb50d61f5376e3d0ba8b8088f8442611cdf474c7"
)
LOGICAL_GLOBAL_CACHE_COMMIT_SHA256: Final = (
    "948a87a94ab462fdafdb7a6d68d7e060d5b98e6ff00b7edc06ef17202a8cb6aa"
)
RUN_ID: Final = "m3-source-integrity-v2-8fcfbcd308bec8e2"
TASK_PLAN_SHA256: Final = "f7d8f092db682d5345586269ba5c56ae7125fabf854bcbc1994aae6af644dd79"
FIRST_QA_TASK_ID: Final = "qa-b7293fc5e647cc0bc489"
FIRST_QA_PLAN_INDEX: Final = 524
EXPECTED_INITIAL_COUNTS: Final = {
    "pending": 322,
    "running": 0,
    "complete": 524,
    "quarantined": 0,
    "total": 846,
}
EXPECTED_INITIAL_BY_KIND: Final = {
    "compile_qa_city": {
        "pending": 4,
        "running": 0,
        "complete": 0,
        "quarantined": 0,
        "total": 4,
    },
    "finalize_logical_cache": {
        "pending": 0,
        "running": 0,
        "complete": 1,
        "quarantined": 0,
        "total": 1,
    },
    "finalize_qa_candidates": {
        "pending": 1,
        "running": 0,
        "complete": 0,
        "quarantined": 0,
        "total": 1,
    },
    "finalize_retained_scene": {
        "pending": 0,
        "running": 0,
        "complete": 523,
        "quarantined": 0,
        "total": 523,
    },
    "qa_overpass": {
        "pending": 317,
        "running": 0,
        "complete": 0,
        "quarantined": 0,
        "total": 317,
    },
}

STATUS_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/status.json"
)
QA_WORKER_LOG_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/qa-worker.log"
)
QA_WORKER_ERROR_LOG_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/qa-worker.err.log"
)

CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_coverage_key_repair_v1.py",
    "src/la_heat/multicity/m3_source_development_engine_coverage_key_repair_v1.py",
    "scripts/authorize_m3_source_coverage_key_repair_v1.py",
    "scripts/run_m3_source_coverage_key_repair_v1.py",
)


class M3SourceCoverageKeyRepairError(RuntimeError):
    """Raised when the narrow compatibility repair leaves its authorization."""


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _is_committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceCoverageKeyRepairError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourceCoverageKeyRepairError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict) or not _is_committed(payload):
        raise M3SourceCoverageKeyRepairError(f"{label} commit is invalid.")
    return payload


def _file_record(
    root: Path,
    path: Path,
    *,
    commit_sha256: str | None = None,
) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.is_symlink():
        raise M3SourceCoverageKeyRepairError(f"Bound file is invalid: {path}")
    result: dict[str, Any] = {
        "path": _relative(root, resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if commit_sha256 is not None:
        result["commit_sha256"] = commit_sha256
    return result


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    pure = PurePosixPath(str(record.get("path", "")))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourceCoverageKeyRepairError(f"{label} path is unsafe.")
    path = (root / Path(*pure.parts)).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourceCoverageKeyRepairError(f"{label} file record changed.")
    return path


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceCoverageKeyRepairError(
            f"Append-only repair authorization already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _queue_snapshot(database: Path) -> dict[str, Any]:
    if not database.is_file():
        raise M3SourceCoverageKeyRepairError("V2 queue database is missing.")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        run = connection.execute(
            "SELECT run_id, task_plan_sha256, desired_state, schema_version "
            "FROM model_runs WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        status_rows = connection.execute(
            "SELECT status, COUNT(*) AS n FROM model_run_tasks WHERE run_id = ? GROUP BY status",
            (RUN_ID,),
        ).fetchall()
        kind_rows = connection.execute(
            "SELECT kind, status, COUNT(*) AS n FROM model_run_tasks "
            "WHERE run_id = ? GROUP BY kind, status ORDER BY kind, status",
            (RUN_ID,),
        ).fetchall()
        active = connection.execute(
            "SELECT COUNT(*) FROM model_run_tasks WHERE run_id = ? "
            "AND (status = 'running' OR lease_owner IS NOT NULL "
            "OR lease_expires_at IS NOT NULL)",
            (RUN_ID,),
        ).fetchone()
        first = connection.execute(
            "SELECT task_id, kind, payload_json, status, attempt, available_at, "
            "lease_owner, lease_expires_at, claim_generation, result_json, "
            "error_type, plan_index, updated_at FROM model_run_tasks "
            "WHERE run_id = ? AND kind = 'qa_overpass' ORDER BY plan_index LIMIT 1",
            (RUN_ID,),
        ).fetchone()
    finally:
        connection.close()
    if run is None or active is None or first is None:
        raise M3SourceCoverageKeyRepairError("V2 queue snapshot is incomplete.")
    by_status = {str(row["status"]): int(row["n"]) for row in status_rows}
    by_kind: dict[str, dict[str, int]] = {}
    for row in kind_rows:
        kind = str(row["kind"])
        by_kind.setdefault(
            kind,
            {key: 0 for key in ("pending", "running", "complete", "quarantined")},
        )[str(row["status"])] = int(row["n"])
    for counts in by_kind.values():
        counts["total"] = sum(counts.values())
    first_payload = json.loads(str(first["payload_json"]))
    return {
        "run_id": str(run["run_id"]),
        "schema_version": int(run["schema_version"]),
        "task_plan_sha256": str(run["task_plan_sha256"]),
        "desired_state": str(run["desired_state"]),
        "counts": {
            **{
                key: by_status.get(key, 0)
                for key in ("pending", "running", "complete", "quarantined")
            },
            "total": sum(by_status.values()),
        },
        "counts_by_kind": by_kind,
        "active_lease_count": int(active[0]),
        "first_qa_task": {
            "task_id": str(first["task_id"]),
            "kind": str(first["kind"]),
            "payload": first_payload,
            "payload_sha256": canonical_sha256(first_payload),
            "status": str(first["status"]),
            "attempt": int(first["attempt"]),
            "available_at": float(first["available_at"]),
            "lease_owner": first["lease_owner"],
            "lease_expires_at": first["lease_expires_at"],
            "claim_generation": int(first["claim_generation"]),
            "result_json": first["result_json"],
            "error_type": first["error_type"],
            "plan_index": int(first["plan_index"]),
            "updated_at": float(first["updated_at"]),
        },
    }


def _validate_initial_snapshot(snapshot: Mapping[str, Any]) -> None:
    first = snapshot.get("first_qa_task")
    if (
        snapshot.get("run_id") != RUN_ID
        or snapshot.get("schema_version") != 1
        or snapshot.get("task_plan_sha256") != TASK_PLAN_SHA256
        or snapshot.get("desired_state") != "paused"
        or snapshot.get("counts") != EXPECTED_INITIAL_COUNTS
        or snapshot.get("counts_by_kind") != EXPECTED_INITIAL_BY_KIND
        or snapshot.get("active_lease_count") != 0
        or not isinstance(first, Mapping)
        or first.get("task_id") != FIRST_QA_TASK_ID
        or first.get("kind") != "qa_overpass"
        or first.get("status") != "pending"
        or first.get("attempt") != 3
        or first.get("claim_generation") != 3
        or first.get("error_type") != "ValueError"
        or first.get("result_json") is not None
        or first.get("lease_owner") is not None
        or first.get("lease_expires_at") is not None
        or first.get("plan_index") != FIRST_QA_PLAN_INDEX
        or first.get("payload", {}).get("ordinal") != 1
        or first.get("payload", {}).get("city_id") != "los_angeles_ca"
        or first.get("payload", {}).get("overpass_id") != "landsat-8_20200516T182745Z"
    ):
        raise M3SourceCoverageKeyRepairError(
            "V2 queue left the exact paused coverage-key incident snapshot."
        )


def _validate_safe_progress(snapshot: Mapping[str, Any], *, terminal: bool) -> None:
    counts = snapshot.get("counts")
    kinds = snapshot.get("counts_by_kind")
    if (
        snapshot.get("run_id") != RUN_ID
        or snapshot.get("schema_version") != 1
        or snapshot.get("task_plan_sha256") != TASK_PLAN_SHA256
        or snapshot.get("desired_state") not in {"running", "paused"}
        or not isinstance(counts, Mapping)
        or not isinstance(kinds, Mapping)
        or counts.get("total") != EXPECTED_TASK_COUNT
        or counts.get("quarantined") != 0
        or int(counts.get("running", -1)) not in {0, 1}
        or snapshot.get("active_lease_count") != counts.get("running")
        or kinds.get("finalize_retained_scene", {}).get("complete") != 523
        or kinds.get("finalize_logical_cache", {}).get("complete") != 1
        or set(kinds) != set(EXPECTED_INITIAL_BY_KIND)
        or any(
            kinds[kind].get("total") != expected["total"]
            for kind, expected in EXPECTED_INITIAL_BY_KIND.items()
        )
        or kinds.get("finalize_retained_scene")
        != EXPECTED_INITIAL_BY_KIND["finalize_retained_scene"]
        or kinds.get("finalize_logical_cache") != EXPECTED_INITIAL_BY_KIND["finalize_logical_cache"]
        or any(
            counts.get(status) != sum(int(value.get(status, 0)) for value in kinds.values())
            for status in ("pending", "running", "complete", "quarantined")
        )
    ):
        raise M3SourceCoverageKeyRepairError("V2 queue left safe repair progression.")
    qa = kinds["qa_overpass"]
    cities = kinds["compile_qa_city"]
    final = kinds["finalize_qa_candidates"]
    if (
        (int(cities.get("complete", 0)) + int(cities.get("running", 0)) > 0)
        and qa.get("complete") != 317
    ) or (
        (int(final.get("complete", 0)) + int(final.get("running", 0)) > 0)
        and cities.get("complete") != 4
    ):
        raise M3SourceCoverageKeyRepairError("V2 repair phase ordering changed.")
    if terminal and (
        snapshot.get("desired_state") != "paused"
        or counts
        != {
            "pending": 0,
            "running": 0,
            "complete": EXPECTED_TASK_COUNT,
            "quarantined": 0,
            "total": EXPECTED_TASK_COUNT,
        }
    ):
        raise M3SourceCoverageKeyRepairError("V2 repair queue is not terminal.")


def _bug_evidence(root: Path) -> dict[str, Any]:
    producer_path = root / "src/la_heat/multicity/m3_source_integrity_v2.py"
    consumer_path = root / "src/la_heat/aligned_landsat.py"
    qa_path = root / "src/la_heat/multicity/m3_source_offline_qa.py"
    producer_lines = producer_path.read_text(encoding="utf-8").splitlines()
    consumer_lines = consumer_path.read_text(encoding="utf-8").splitlines()
    qa_lines = qa_path.read_text(encoding="utf-8").splitlines()

    def unique_line(lines: list[str], needle: str, *, label: str) -> int:
        matches = [index for index, line in enumerate(lines, start=1) if needle in line]
        if len(matches) != 1:
            raise M3SourceCoverageKeyRepairError(f"{label} code evidence changed.")
        return matches[0]

    producer_line = unique_line(
        producer_lines,
        'arrays["source_coverage"] = coverage',
        label="Legacy coverage producer",
    )
    consumer_key_line = unique_line(
        consumer_lines,
        'COVERAGE_KEY = "_source_coverage"',
        label="Coverage consumer key",
    )
    consumer_error_line = unique_line(
        consumer_lines,
        'raise ValueError(f"Aligned scene lacks assets: {sorted(missing)}")',
        label="Coverage consumer ValueError",
    )
    qa_decode_line = unique_line(
        qa_lines,
        "scene = decode_aligned_scene_arrays(",
        label="Offline QA decode call",
    )
    if COVERAGE_KEY != "_source_coverage" or LEGACY_COVERAGE_KEY == COVERAGE_KEY:
        raise M3SourceCoverageKeyRepairError("Coverage-key mismatch evidence changed.")
    return {
        "classification": "in_memory_mapping_key_compatibility_failure",
        "producer_key": LEGACY_COVERAGE_KEY,
        "consumer_key": COVERAGE_KEY,
        "expected_exception_type": "ValueError",
        "expected_exception_message": ("Aligned scene lacks assets: ['_source_coverage']"),
        "producer_assignment": {
            **_file_record(root, producer_path),
            "line": producer_line,
            "statement": 'arrays["source_coverage"] = coverage',
        },
        "consumer_contract": {
            **_file_record(root, consumer_path),
            "coverage_key_line": consumer_key_line,
            "value_error_line": consumer_error_line,
        },
        "offline_qa_call_path": {
            **_file_record(root, qa_path),
            "decode_call_line": qa_decode_line,
        },
        "tiff_bytes_or_values_read_to_build_evidence": False,
    }


def _validate_incident_status(payload: Mapping[str, Any]) -> None:
    if (
        payload.get("state") != "paused"
        or payload.get("run_id") != RUN_ID
        or payload.get("desired_state") != "paused"
        or payload.get("counts") != EXPECTED_INITIAL_COUNTS
        or payload.get("counts_by_kind") != EXPECTED_INITIAL_BY_KIND
        or payload.get("active_phase") != "offline_qa_rebuild"
        or payload.get("phase") != "qa_overpass"
        or payload.get("phase_complete") != 0
        or payload.get("phase_total") != 322
        or payload.get("retry_count") != 32
        or payload.get("last_error_type") != "ValueError"
        or payload.get("network_allowed") is not False
        or payload.get("href_reads_allowed") is not False
        or payload.get("network_request_count") != 0
    ):
        raise M3SourceCoverageKeyRepairError("Coverage-key incident status changed.")


def build_m3_source_coverage_key_repair_authorization(
    project_root: str | Path,
) -> dict[str, Any]:
    """Build the repair permit without opening any Landsat or QA raster value."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    v2_authorization = authenticate_m3_source_integrity_v2_authorization(
        root, settings.authorization
    )
    if v2_authorization.get("commit_sha256") != V2_AUTHORIZATION_COMMIT_SHA256:
        raise M3SourceCoverageKeyRepairError("V2 authorization commit changed.")
    logical_global = authenticate_logical_global_cache(root, v2_authorization)
    if logical_global.get("commit_sha256") != LOGICAL_GLOBAL_CACHE_COMMIT_SHA256:
        raise M3SourceCoverageKeyRepairError("Logical global cache commit changed.")
    logical_completion_path = _inside(
        root,
        str(v2_authorization["source_landsat_cache_completion"]),
        label="Logical cache completion",
    )
    logical_completion = _read_committed(logical_completion_path, label="Logical cache completion")
    if (
        logical_completion.get("commit_sha256") != LOGICAL_CACHE_COMPLETION_COMMIT_SHA256
        or logical_completion.get("logical_global_cache_commit_sha256")
        != logical_global["commit_sha256"]
    ):
        raise M3SourceCoverageKeyRepairError("Logical cache completion changed.")
    snapshot = _queue_snapshot(settings.database)
    _validate_initial_snapshot(snapshot)
    status_path = root / STATUS_RELATIVE_PATH
    qa_log_path = root / QA_WORKER_LOG_RELATIVE_PATH
    error_log_path = root / QA_WORKER_ERROR_LOG_RELATIVE_PATH
    status = json.loads(status_path.read_text(encoding="utf-8"))
    qa_log = json.loads(qa_log_path.read_text(encoding="utf-8"))
    if not isinstance(status, dict) or not isinstance(qa_log, dict) or status != qa_log:
        raise M3SourceCoverageKeyRepairError("Incident status/log evidence differs.")
    _validate_incident_status(status)
    if not error_log_path.is_file() or error_log_path.stat().st_size != 0:
        raise M3SourceCoverageKeyRepairError("QA stderr incident evidence changed.")
    code_identity = {relative: _file_record(root, root / relative) for relative in CODE_PATHS}
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_source_coverage_key_repair_authorized",
        "repair_scope": "rename_one_authenticated_in_memory_mapping_key_only",
        "parent_v2_authorization_commit_sha256": v2_authorization["commit_sha256"],
        "logical_cache_completion_commit_sha256": logical_completion["commit_sha256"],
        "logical_global_cache_commit_sha256": logical_global["commit_sha256"],
        "v2_run_id": RUN_ID,
        "v2_task_plan_sha256": TASK_PLAN_SHA256,
        "initial_paused_queue_snapshot": snapshot,
        "incident_evidence": {
            "status": status,
            "status_file": _file_record(root, status_path),
            "qa_worker_log": _file_record(root, qa_log_path),
            "qa_worker_stderr": _file_record(root, error_log_path),
            "first_qa_task": snapshot["first_qa_task"],
            "coverage_key_mismatch": _bug_evidence(root),
        },
        "inputs": {
            "parent_v2_authorization": _file_record(
                root,
                settings.authorization,
                commit_sha256=v2_authorization["commit_sha256"],
            ),
            "logical_cache_completion": _file_record(
                root,
                logical_completion_path,
                commit_sha256=logical_completion["commit_sha256"],
            ),
            "logical_global_cache_commit": _file_record(
                root,
                settings.logical_cache_root / "LOGICAL_GLOBAL_CACHE_COMMIT.json",
                commit_sha256=logical_global["commit_sha256"],
            ),
            "incident_queue_database": _file_record(root, settings.database),
        },
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": list(BLIND_CITY_IDS),
        "required_landsat_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(QA_CANDIDATES),
        "adapter_contract": {
            "call_existing_authenticated_load_retained_scene_arrays_first": True,
            "required_input_keys": [*REQUIRED_ASSETS, LEGACY_COVERAGE_KEY],
            "required_output_keys": [*REQUIRED_ASSETS, COVERAGE_KEY],
            "remove_key": LEGACY_COVERAGE_KEY,
            "insert_key": COVERAGE_KEY,
            "coverage_array_object_copied_or_modified": False,
            "any_raster_array_value_inspected_or_changed_by_adapter": False,
        },
        "permissions": {
            "resume_existing_v2_queue_without_rebuild_or_reset": True,
            "execute_only_existing_317_qa_four_city_compile_and_final_tasks": True,
            "rename_legacy_coverage_key_in_memory_after_authenticated_loader": True,
            "write_existing_v2_qa_output_and_completion_paths": True,
            "modify_old_v2_authorization_or_logical_cache_commits": False,
            "modify_physical_cache_or_completed_logical_tasks": False,
            "network_or_href_reads": False,
            "blind_city_asset_predictor_qa_or_target_access": False,
            "predictor_read_or_build": False,
            "fit_select_predict_or_score": False,
            "change_year_city_candidate_or_support_gate": False,
        },
        "runtime_contract": {
            "compute_workers": 1,
            "download_workers": 0,
            "network_requests_allowed": False,
            "href_reads_allowed": False,
            "existing_run_id": RUN_ID,
            "existing_task_plan_sha256": TASK_PLAN_SHA256,
            "initial_completed_tasks_preserved": 524,
            "initial_pending_tasks_preserved": 322,
            "queue_rebuild_reset_or_task_rewrite_allowed": False,
        },
        "code_identity": code_identity,
        "access_audit": {
            "authorization_read_landsat_or_qa_raster_values": False,
            "authorization_read_predictor_or_target_values": False,
            "authorization_accessed_blind_city_data": False,
            "authorization_modified_queue_cache_or_existing_manifest": False,
            "authorization_fit_selected_predicted_or_scored": False,
        },
        "next_safe_stage": "start_repair_wrapper_for_existing_offline_qa_queue",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_m3_source_coverage_key_repair_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    destination = _inside(root, authorization_path, label="Repair authorization")
    if destination.is_relative_to(settings.cache_root) or destination.is_relative_to(
        settings.database.parent
    ):
        raise M3SourceCoverageKeyRepairError(
            "Repair authorization cannot be written into an old runtime or cache."
        )
    payload = build_m3_source_coverage_key_repair_authorization(root)
    _write_exclusive(payload, destination)
    return authenticate_m3_source_coverage_key_repair_authorization(root, destination)


def authenticate_m3_source_coverage_key_repair_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Repair authorization")
    observed = _read_committed(path, label="Coverage-key repair authorization")
    expected = build_m3_source_coverage_key_repair_authorization(root)
    if observed != expected:
        raise M3SourceCoverageKeyRepairError("Coverage-key repair authorization drifted.")
    return observed


def load_m3_source_coverage_key_repair_runtime_permit(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    *,
    require_terminal_queue: bool,
) -> dict[str, Any]:
    """Authenticate immutable repair inputs after the incident queue progresses."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    path = _inside(root, authorization_path, label="Repair authorization")
    permit = _read_committed(path, label="Coverage-key repair authorization")
    if (
        permit.get("state") != "m3_source_coverage_key_repair_authorized"
        or permit.get("parent_v2_authorization_commit_sha256") != V2_AUTHORIZATION_COMMIT_SHA256
        or permit.get("logical_cache_completion_commit_sha256")
        != LOGICAL_CACHE_COMPLETION_COMMIT_SHA256
        or permit.get("logical_global_cache_commit_sha256") != LOGICAL_GLOBAL_CACHE_COMMIT_SHA256
        or permit.get("v2_run_id") != RUN_ID
        or permit.get("v2_task_plan_sha256") != TASK_PLAN_SHA256
    ):
        raise M3SourceCoverageKeyRepairError("Repair permit bindings changed.")
    expected_adapter = {
        "call_existing_authenticated_load_retained_scene_arrays_first": True,
        "required_input_keys": [*REQUIRED_ASSETS, LEGACY_COVERAGE_KEY],
        "required_output_keys": [*REQUIRED_ASSETS, COVERAGE_KEY],
        "remove_key": LEGACY_COVERAGE_KEY,
        "insert_key": COVERAGE_KEY,
        "coverage_array_object_copied_or_modified": False,
        "any_raster_array_value_inspected_or_changed_by_adapter": False,
    }
    expected_permissions = {
        "resume_existing_v2_queue_without_rebuild_or_reset": True,
        "execute_only_existing_317_qa_four_city_compile_and_final_tasks": True,
        "rename_legacy_coverage_key_in_memory_after_authenticated_loader": True,
        "write_existing_v2_qa_output_and_completion_paths": True,
        "modify_old_v2_authorization_or_logical_cache_commits": False,
        "modify_physical_cache_or_completed_logical_tasks": False,
        "network_or_href_reads": False,
        "blind_city_asset_predictor_qa_or_target_access": False,
        "predictor_read_or_build": False,
        "fit_select_predict_or_score": False,
        "change_year_city_candidate_or_support_gate": False,
    }
    expected_runtime = {
        "compute_workers": 1,
        "download_workers": 0,
        "network_requests_allowed": False,
        "href_reads_allowed": False,
        "existing_run_id": RUN_ID,
        "existing_task_plan_sha256": TASK_PLAN_SHA256,
        "initial_completed_tasks_preserved": 524,
        "initial_pending_tasks_preserved": 322,
        "queue_rebuild_reset_or_task_rewrite_allowed": False,
    }
    expected_access_audit = {
        "authorization_read_landsat_or_qa_raster_values": False,
        "authorization_read_predictor_or_target_values": False,
        "authorization_accessed_blind_city_data": False,
        "authorization_modified_queue_cache_or_existing_manifest": False,
        "authorization_fit_selected_predicted_or_scored": False,
    }
    initial_snapshot = permit.get("initial_paused_queue_snapshot")
    incident = permit.get("incident_evidence")
    unsigned_claim = dict(permit)
    unsigned_claim.pop("commit_sha256", None)
    claim_id = unsigned_claim.pop("claim_id", None)
    if (
        permit.get("repair_scope") != "rename_one_authenticated_in_memory_mapping_key_only"
        or permit.get("source_city_ids") != list(SOURCE_CITY_IDS)
        or permit.get("blind_test_city_ids") != list(BLIND_CITY_IDS)
        or permit.get("required_landsat_assets") != list(REQUIRED_ASSETS)
        or permit.get("qa_candidate_ids") != list(QA_CANDIDATES)
        or permit.get("adapter_contract") != expected_adapter
        or permit.get("permissions") != expected_permissions
        or permit.get("runtime_contract") != expected_runtime
        or permit.get("access_audit") != expected_access_audit
        or claim_id != canonical_sha256(unsigned_claim)
        or not isinstance(initial_snapshot, Mapping)
        or not isinstance(incident, Mapping)
        or incident.get("first_qa_task") != initial_snapshot.get("first_qa_task")
        or incident.get("coverage_key_mismatch") != _bug_evidence(root)
    ):
        raise M3SourceCoverageKeyRepairError("Repair permit scope changed.")
    _validate_initial_snapshot(initial_snapshot)
    status = incident.get("status")
    if not isinstance(status, Mapping):
        raise M3SourceCoverageKeyRepairError("Repair incident status is missing.")
    _validate_incident_status(status)
    v2_authorization = authenticate_m3_source_integrity_v2_authorization(
        root, settings.authorization
    )
    logical_global = authenticate_logical_global_cache(root, v2_authorization)
    if (
        v2_authorization.get("commit_sha256") != V2_AUTHORIZATION_COMMIT_SHA256
        or logical_global.get("commit_sha256") != LOGICAL_GLOBAL_CACHE_COMMIT_SHA256
    ):
        raise M3SourceCoverageKeyRepairError("Repair immutable parent changed.")
    code_identity = permit.get("code_identity")
    if not isinstance(code_identity, Mapping) or set(code_identity) != set(CODE_PATHS):
        raise M3SourceCoverageKeyRepairError("Repair code identity set changed.")
    for relative, record in code_identity.items():
        if (
            not isinstance(record, Mapping)
            or _relative(root, _record_path(root, record, label=f"Repair code {relative}"))
            != relative
        ):
            raise M3SourceCoverageKeyRepairError("Repair code identity changed.")
    for key in (
        "parent_v2_authorization",
        "logical_cache_completion",
        "logical_global_cache_commit",
    ):
        record = permit.get("inputs", {}).get(key)
        if not isinstance(record, Mapping):
            raise M3SourceCoverageKeyRepairError(f"Repair input {key} is missing.")
        _record_path(root, record, label=f"Repair input {key}")
    inputs = permit["inputs"]
    if (
        inputs["parent_v2_authorization"].get("commit_sha256") != V2_AUTHORIZATION_COMMIT_SHA256
        or inputs["logical_cache_completion"].get("commit_sha256")
        != LOGICAL_CACHE_COMPLETION_COMMIT_SHA256
        or inputs["logical_global_cache_commit"].get("commit_sha256")
        != LOGICAL_GLOBAL_CACHE_COMMIT_SHA256
    ):
        raise M3SourceCoverageKeyRepairError("Repair input commit binding changed.")
    snapshot = _queue_snapshot(settings.database)
    _validate_safe_progress(snapshot, terminal=require_terminal_queue)
    return permit


def authenticate_m3_source_coverage_key_repair_value_gate(
    project_root: str | Path,
    expected_authorization: Mapping[str, Any],
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> None:
    """Cheap per-task repair gate after the strict pre-start authentication."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    observed = _read_committed(
        _inside(root, authorization_path, label="Repair authorization"),
        label="Coverage-key repair authorization",
    )
    if observed != dict(expected_authorization):
        raise M3SourceCoverageKeyRepairError("Coverage-key repair permit changed.")
    _validate_safe_progress(_queue_snapshot(settings.database), terminal=False)
