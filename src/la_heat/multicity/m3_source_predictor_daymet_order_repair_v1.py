"""Append-only repair for the M3 source-predictor Daymet variable order.

The parent worker passed the right six variables to the frozen CMR parser in
the wrong order.  This module leaves every parent file and the existing queue
untouched.  It changes only the argument order at the metadata-fetch boundary,
then writes the legacy parent order so the existing task plan and downstream
assembly remain byte-contract compatible.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    _file_record,
    _read_committed,
    _with_commit,
    _write_exclusive,
    authenticate_m3_source_predictor_extension_authorization,
    authenticate_source_predictor_acquisition_completion,
    authenticate_source_predictors_46_completion,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import (
    OFFLINE_KINDS,
    OFFLINE_PHASE,
    ONLINE_KINDS,
    ONLINE_PHASE,
    PHASES,
    predictor_task_plan_sha256,
    source_predictor_run_id,
    task_specs_from_predictor_authorization,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    DAYMET_VARIABLES as PARENT_DAYMET_VARIABLES,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    M3SourcePredictorCompatibilityError,
    M3SourcePredictorCredentialRequiredError,
    M3SourcePredictorWorkerError,
    PredictorWorkerOptions,
    SafeExistingBuilderAdapter,
    _execute_unlocked,
    _write_or_authenticate,
    exclusive_predictor_worker,
)
from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-daymet-order-repair-v1"
PARENT_AUTHORIZATION_COMMIT_SHA256: Final = (
    "22f6f417faea2aaeb7f0c04d182ae45616f3cacf416ea904e58ed2c699987019"
)
RUN_ID: Final = "m3-source-predictor-extension-v1-22f6f417faea2aae"
PARENT_CANONICAL_TASK_PLAN_SHA256: Final = (
    "6133d008f93849033c658725d771a79221cc7c24473757d0d90a0881a5699a12"
)
SQLITE_TASK_PLAN_SHA256: Final = (
    "d21c5c74ca562d29e71375350029a4e44c17088497494203bc8d1844a1cec712"
)
INITIAL_QUEUE_SNAPSHOT_SHA256: Final = (
    "f0d9b9c98f7c7dbb42140d7c0da5f2b4db1fa8b9b1693db8e30a9f7eaadc5c75"
)
INCIDENT_QUEUE_RECORD: Final = {
    "path": "data/interim/multicity/m3_source_predictor_extension_v1/runtime/tasks.sqlite",
    "bytes": 77_824,
    "sha256": "4e680af1c7b9a0a77930c311ac79ba9d839857c9e13741adf2463db743946cdf",
}
INCIDENT_STATUS_RECORD: Final = {
    "path": "data/interim/multicity/m3_source_predictor_extension_v1/runtime/status.json",
    "bytes": 2_383,
    "sha256": "7b978b79b4a5d7bd98bc3396dd6bcd3f69bb2a337021ef1f74a3c0c61fdb2352",
}
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_PREDICTOR_DAYMET_ORDER_REPAIR_V1_AUTHORIZATION.json"
)
ACQUISITION_REPAIR_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_predictor_extension_v1/"
    "SOURCE_PREDICTOR_DAYMET_ORDER_REPAIR_ACQUISITION_COMPLETE.json"
)
REPAIR_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_predictor_extension_v1/"
    "SOURCE_PREDICTOR_DAYMET_ORDER_REPAIR_COMPLETE.json"
)
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_predictor_daymet_order_repair_v1.py",
    "scripts/authorize_m3_source_predictor_daymet_order_repair_v1.py",
    "scripts/run_m3_source_predictor_daymet_order_repair_v1.py",
)
EXPECTED_INITIAL_COMPLETE_IDS: Final = (
    "freeze-key-universe",
    "static-houston_tx",
    "static-chicago_il",
)
EXPECTED_INITIAL_COUNTS: Final = {
    "pending": 82,
    "running": 0,
    "complete": 3,
    "quarantined": 0,
    "total": 85,
}


class M3SourcePredictorDaymetOrderRepairError(RuntimeError):
    """Raised when the narrow repair leaves its append-only authorization."""


class M3SourcePredictorDaymetOrderRepairPermitError(
    M3SourcePredictorCredentialRequiredError
):
    """Retryable, pause-immediately failure of the append-only repair gate."""


def _project_root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise M3SourcePredictorDaymetOrderRepairError(f"Project root is missing: {root}")
    return root


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourcePredictorDaymetOrderRepairError(f"{label} escaped the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _is_committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _read_repair_authorization(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourcePredictorDaymetOrderRepairError(
            f"Cannot read repair authorization: {path}"
        ) from error
    if not isinstance(payload, dict) or not _is_committed(payload):
        raise M3SourcePredictorDaymetOrderRepairError("Repair authorization is invalid.")
    return payload


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    pure = PurePosixPath(str(record.get("path", "")))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourcePredictorDaymetOrderRepairError(f"{label} path is unsafe.")
    path = (root / Path(*pure.parts)).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourcePredictorDaymetOrderRepairError(f"{label} file changed.")
    return path


def _expected_task_plan(parent: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Rebuild both parent and SQLite plan identities from all 85 task specs."""

    specs = task_specs_from_predictor_authorization(parent)
    plan: list[dict[str, Any]] = []
    sqlite_rows: list[dict[str, str]] = []
    for plan_index, spec in enumerate(specs):
        payload_json = json.dumps(
            spec.payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        plan.append(
            {
                "plan_index": plan_index,
                "task_id": spec.task_id,
                "kind": spec.kind,
                "payload": spec.payload,
            }
        )
        sqlite_rows.append(
            {
                "task_id": spec.task_id,
                "kind": spec.kind,
                "payload_json": payload_json,
            }
        )
    encoded = json.dumps(
        sqlite_rows,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sqlite_plan_sha256 = hashlib.sha256(encoded).hexdigest()
    if (
        len(plan) != 85
        or predictor_task_plan_sha256(parent) != PARENT_CANONICAL_TASK_PLAN_SHA256
        or sqlite_plan_sha256 != SQLITE_TASK_PLAN_SHA256
    ):
        raise M3SourcePredictorDaymetOrderRepairError(
            "The parent-derived predictor task plan changed."
        )
    return plan, sqlite_plan_sha256


def _queue_snapshot(database: Path, parent: Mapping[str, Any]) -> dict[str, Any]:
    if not database.is_file():
        raise M3SourcePredictorDaymetOrderRepairError("Parent queue database is missing.")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        run = connection.execute(
            "SELECT run_id, task_plan_sha256, desired_state, schema_version "
            "FROM model_runs WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        tasks = connection.execute(
            "SELECT task_id, kind, payload_json, status, attempt, available_at, "
            "lease_owner, lease_expires_at, claim_generation, result_json, error_type, "
            "plan_index, updated_at FROM model_run_tasks WHERE run_id = ? "
            "ORDER BY plan_index",
            (RUN_ID,),
        ).fetchall()
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    if run is None or quick != "ok" or len(tasks) != 85:
        raise M3SourcePredictorDaymetOrderRepairError("Parent queue is not healthy.")
    expected_plan, expected_sqlite_sha256 = _expected_task_plan(parent)
    normalized: list[dict[str, Any]] = []
    for expected, row in zip(expected_plan, tasks, strict=True):
        item = dict(row)
        item["payload"] = json.loads(str(item.pop("payload_json")))
        raw_result = item.pop("result_json")
        item["result"] = None if raw_result is None else json.loads(str(raw_result))
        if any(item[key] != expected[key] for key in ("plan_index", "task_id", "kind", "payload")):
            raise M3SourcePredictorDaymetOrderRepairError(
                "The SQLite task plan differs from the parent-derived 85-task plan."
            )
        normalized.append(item)
    if any(
        (
            row["status"] == "running"
            and (
                not isinstance(row["lease_owner"], str)
                or not row["lease_owner"]
                or not isinstance(row["lease_expires_at"], (int, float))
                or not math.isfinite(float(row["lease_expires_at"]))
                or int(row["claim_generation"]) <= 0
            )
        )
        or (
            row["status"] != "running"
            and (row["lease_owner"] is not None or row["lease_expires_at"] is not None)
        )
        for row in normalized
    ):
        raise M3SourcePredictorDaymetOrderRepairError("Parent queue lease invariants changed.")
    counts = Counter(str(row["status"]) for row in normalized)
    active_leases = sum(
        row["lease_owner"] is not None or row["lease_expires_at"] is not None
        for row in normalized
    )
    complete = [
        {
            "task_id": row["task_id"],
            "kind": row["kind"],
            "plan_index": row["plan_index"],
            "attempt": row["attempt"],
            "claim_generation": row["claim_generation"],
            "result": row["result"],
        }
        for row in normalized
        if row["status"] == "complete"
    ]
    metadata = [
        {
            "task_id": row["task_id"],
            "plan_index": row["plan_index"],
            "status": row["status"],
            "attempt": row["attempt"],
            "claim_generation": row["claim_generation"],
            "error_type": row["error_type"],
        }
        for row in normalized
        if row["kind"] == "acquire_daymet_metadata"
    ]
    running = [
        {
            "task_id": row["task_id"],
            "kind": row["kind"],
            "plan_index": row["plan_index"],
            "lease_expires_at": row["lease_expires_at"],
            "lease_owner": row["lease_owner"],
            "claim_generation": row["claim_generation"],
        }
        for row in normalized
        if row["status"] == "running"
    ]
    return {
        "run_id": str(run["run_id"]),
        "task_plan_sha256": str(run["task_plan_sha256"]),
        "parent_canonical_task_plan_sha256": predictor_task_plan_sha256(parent),
        "derived_sqlite_task_plan_sha256": expected_sqlite_sha256,
        "task_plan": expected_plan,
        "task_states": [
            {
                "plan_index": row["plan_index"],
                "task_id": row["task_id"],
                "kind": row["kind"],
                "status": row["status"],
                "attempt": row["attempt"],
                "claim_generation": row["claim_generation"],
                "error_type": row["error_type"],
                "result": row["result"],
            }
            for row in normalized
        ],
        "desired_state": str(run["desired_state"]),
        "schema_version": int(run["schema_version"]),
        "counts": {
            "pending": counts["pending"],
            "running": counts["running"],
            "complete": counts["complete"],
            "quarantined": counts["quarantined"],
            "total": len(normalized),
        },
        "active_lease_count": active_leases,
        "completed_tasks": complete,
        "daymet_metadata_tasks": metadata,
        "running_tasks": running,
    }


def _validate_initial_snapshot(snapshot: Mapping[str, Any]) -> None:
    complete_ids = tuple(row["task_id"] for row in snapshot["completed_tasks"])
    metadata = snapshot["daymet_metadata_tasks"]
    if (
        snapshot.get("run_id") != RUN_ID
        or snapshot.get("schema_version") != 1
        or snapshot.get("task_plan_sha256") != SQLITE_TASK_PLAN_SHA256
        or snapshot.get("derived_sqlite_task_plan_sha256") != SQLITE_TASK_PLAN_SHA256
        or snapshot.get("parent_canonical_task_plan_sha256")
        != PARENT_CANONICAL_TASK_PLAN_SHA256
        or snapshot.get("desired_state") != "paused"
        or snapshot.get("counts") != EXPECTED_INITIAL_COUNTS
        or snapshot.get("active_lease_count") != 0
        or complete_ids != EXPECTED_INITIAL_COMPLETE_IDS
        or len(metadata) != 10
        or Counter(int(row.get("attempt", -1)) for row in metadata)
        != Counter({5: 8, 4: 2})
        or any(
            row.get("status") != "pending"
            or row.get("error_type") != "SourceFootprintError"
            or row.get("claim_generation") != row.get("attempt")
            for row in metadata
        )
        or canonical_sha256(snapshot) != INITIAL_QUEUE_SNAPSHOT_SHA256
    ):
        raise M3SourcePredictorDaymetOrderRepairError(
            "The paused Daymet-order incident snapshot changed."
        )


def _validate_safe_progress(
    snapshot: Mapping[str, Any],
    authorization: Mapping[str, Any],
    *,
    require_paused: bool,
) -> None:
    initial = authorization["incident"]["queue_snapshot"]
    initial_results = {
        row["task_id"]: row for row in initial["completed_tasks"]
    }
    current_results = {
        row["task_id"]: row for row in snapshot["completed_tasks"]
    }
    counts = snapshot["counts"]
    task_states = snapshot.get("task_states")
    initial_states = initial.get("task_states")
    if (
        snapshot.get("run_id") != RUN_ID
        or snapshot.get("schema_version") != 1
        or snapshot.get("task_plan_sha256") != SQLITE_TASK_PLAN_SHA256
        or snapshot.get("derived_sqlite_task_plan_sha256") != SQLITE_TASK_PLAN_SHA256
        or snapshot.get("parent_canonical_task_plan_sha256")
        != PARENT_CANONICAL_TASK_PLAN_SHA256
        or snapshot.get("task_plan") != initial.get("task_plan")
        or not isinstance(task_states, list)
        or not isinstance(initial_states, list)
        or len(task_states) != 85
        or len(initial_states) != 85
        or counts.get("total") != 85
        or counts.get("quarantined") != 0
        or counts.get("complete", 0) < 3
        or sum(int(counts[key]) for key in ("pending", "running", "complete", "quarantined"))
        != 85
        or any(current_results.get(task_id) != row for task_id, row in initial_results.items())
        or (require_paused and snapshot.get("desired_state") != "paused")
        or (require_paused and counts.get("running") != 0)
        or (require_paused and snapshot.get("active_lease_count") != 0)
        or (not require_paused and counts.get("running", 0) > 1)
        or (not require_paused and snapshot.get("active_lease_count", 0) > 1)
        or snapshot.get("active_lease_count") != counts.get("running")
        or len(snapshot.get("running_tasks", ())) != counts.get("running")
    ):
        raise M3SourcePredictorDaymetOrderRepairError("Repair queue progress is unsafe.")
    initial_by_id = {row["task_id"]: row for row in initial_states}
    current_by_id = {row["task_id"]: row for row in task_states}
    if set(initial_by_id) != set(current_by_id) or any(
        not isinstance(row.get("attempt"), int)
        or isinstance(row.get("attempt"), bool)
        or not isinstance(row.get("claim_generation"), int)
        or isinstance(row.get("claim_generation"), bool)
        or row["attempt"] < initial_by_id[task_id]["attempt"]
        or row["claim_generation"] < initial_by_id[task_id]["claim_generation"]
        or row["attempt"] != row["claim_generation"]
        for task_id, row in current_by_id.items()
    ):
        raise M3SourcePredictorDaymetOrderRepairError(
            "Repair task attempts or fencing generations regressed."
        )
    kinds = (*ONLINE_KINDS, *OFFLINE_KINDS)
    active_seen = False
    for kind in kinds:
        rows = [row for row in task_states if row.get("kind") == kind]
        if not rows:
            raise M3SourcePredictorDaymetOrderRepairError("Repair task kind disappeared.")
        all_complete = all(row.get("status") == "complete" for row in rows)
        if not active_seen and all_complete:
            if any(row.get("result") is None or row.get("error_type") is not None for row in rows):
                raise M3SourcePredictorDaymetOrderRepairError(
                    "A completed repair task lost its durable result."
                )
            continue
        if not active_seen:
            active_seen = True
            if any(
                row.get("status") not in {"pending", "running", "complete"}
                or (row.get("status") == "complete" and row.get("result") is None)
                or (row.get("status") == "complete" and row.get("error_type") is not None)
                or (row.get("status") != "complete" and row.get("result") is not None)
                for row in rows
            ):
                raise M3SourcePredictorDaymetOrderRepairError(
                    "The active repair task kind has invalid state."
                )
            continue
        if any(
            row.get("status") != "pending"
            or row.get("attempt") != 0
            or row.get("claim_generation") != 0
            or row.get("error_type") is not None
            or row.get("result") is not None
            for row in rows
        ):
            raise M3SourcePredictorDaymetOrderRepairError(
                "A future repair task kind changed before its phase boundary."
            )


def _code_records(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root, root / path) for path in CODE_PATHS]


def _repair_contract() -> dict[str, Any]:
    return {
        "fetch_argument_order": list(DEFAULT_DAYMET_VARIABLES),
        "persisted_granule_order": list(PARENT_DAYMET_VARIABLES),
        "override_only_acquire_daymet_metadata": True,
        "same_database_run_id_and_task_plan": True,
        "initialize_rebuild_reset_rewrite_or_unquarantine_allowed": False,
        "maximum_active_tasks": 1,
        "download_workers": 1,
        "compute_workers": 1,
    }


def _permissions() -> dict[str, bool]:
    return {
        "continue_parent_source_predictor_scope": True,
        "read_or_write_blind_city_asset_predictor_qa_or_target": False,
        "read_landsat_thermal_qa_or_any_target_value": False,
        "fit_select_predict_score_or_choose_model_or_qa": False,
        "change_city_year_key_feature_source_host_or_window": False,
        "modify_parent_authorization_or_hash_bound_files": False,
        "rebuild_reset_rewrite_or_delete_existing_queue_or_cache": False,
    }


def _authorization_access_audit() -> dict[str, Any]:
    return {
        "network_or_href_reads": 0,
        "predictor_qa_or_target_values_read": False,
        "blind_test_city_accessed": False,
        "queue_or_cache_modified": False,
        "model_fit_select_predict_or_score_performed": False,
    }


def build_m3_source_predictor_daymet_order_repair_authorization(
    project_root: str | Path,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Build the pre-network repair permit from metadata and queue state only."""

    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root, config_path)
    parent = authenticate_m3_source_predictor_extension_authorization(
        root, settings.authorization, settings.config_path
    )
    if parent.get("commit_sha256") != PARENT_AUTHORIZATION_COMMIT_SHA256:
        raise M3SourcePredictorDaymetOrderRepairError("Parent authorization changed.")
    snapshot = _queue_snapshot(settings.database, parent)
    _validate_initial_snapshot(snapshot)
    acquisition_files = [
        path
        for path in settings.acquisition_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ] if settings.acquisition_root.exists() else []
    if acquisition_files:
        raise M3SourcePredictorDaymetOrderRepairError(
            "The incident is no longer a zero-acquisition-file snapshot."
        )
    status_payload = json.loads(settings.status.read_text(encoding="utf-8"))
    if not isinstance(status_payload, Mapping):
        raise M3SourcePredictorDaymetOrderRepairError("Parent status is invalid.")
    status_plan = status_payload.get("task_plan_sha256")
    if status_plan != PARENT_CANONICAL_TASK_PLAN_SHA256:
        raise M3SourcePredictorDaymetOrderRepairError("Parent status plan evidence changed.")
    queue_record = _file_record(root, settings.database)
    status_record = _file_record(root, settings.status)
    if queue_record != INCIDENT_QUEUE_RECORD or status_record != INCIDENT_STATUS_RECORD:
        raise M3SourcePredictorDaymetOrderRepairError("Incident file evidence changed.")
    code = _code_records(root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_source_predictor_daymet_order_repair_authorized",
        "parent_authorization": _file_record(
            root,
            settings.authorization,
            commit_sha256=str(parent["commit_sha256"]),
        ),
        "incident": {
            "classification": "correct_six_daymet_variables_passed_in_wrong_order",
            "parent_worker_order": list(PARENT_DAYMET_VARIABLES),
            "cmr_parser_required_order": list(DEFAULT_DAYMET_VARIABLES),
            "same_variable_set": set(PARENT_DAYMET_VARIABLES)
            == set(DEFAULT_DAYMET_VARIABLES),
            "queue_database": queue_record,
            "status": status_record,
            "status_reported_task_plan_sha256": status_plan,
            "status_plan_is_stale_relative_to_sqlite_internal_hash": True,
            "status_task_plan_semantics": (
                "parent_canonical_plan_digest_not_sqlite_model_runs_task_plan_sha256"
            ),
            "authoritative_sqlite_task_plan_sha256": SQLITE_TASK_PLAN_SHA256,
            "queue_snapshot": snapshot,
            "acquisition_file_count": 0,
        },
        "repair_contract": _repair_contract(),
        "permissions": _permissions(),
        "code_identity": {
            "files": code,
            "set_sha256": canonical_sha256(code),
        },
        "authorization_access_audit": _authorization_access_audit(),
        "next_safe_stage": "resume_same_predictor_queue_with_daymet_order_repair_runner",
    }
    return _with_commit(payload)


def create_m3_source_predictor_daymet_order_repair_authorization(
    project_root: str | Path,
    output_path: str | Path = AUTHORIZATION_PATH,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root, config_path)
    destination = _inside(root, output_path, label="Repair authorization")
    if destination != (root / AUTHORIZATION_PATH).resolve():
        raise M3SourcePredictorDaymetOrderRepairError(
            "Repair authorization destination changed."
        )
    with exclusive_predictor_worker(settings.worker_lock):
        payload = build_m3_source_predictor_daymet_order_repair_authorization(
            root, config_path=config_path
        )
        _write_exclusive(payload, destination)
        return load_m3_source_predictor_daymet_order_repair_runtime_permit(
            root,
            destination,
            config_path=config_path,
            require_paused=True,
        )


def authenticate_m3_source_predictor_daymet_order_repair_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    return load_m3_source_predictor_daymet_order_repair_runtime_permit(
        project_root,
        authorization_path,
        config_path=config_path,
        require_paused=True,
    )


def _validate_static_authorization(
    root: Path,
    settings: Any,
    authorization: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> None:
    incident = authorization.get("incident")
    records = authorization.get("code_identity", {}).get("files")
    parent_record = authorization.get("parent_authorization")
    if (
        authorization.get("schema_version") != SCHEMA_VERSION
        or authorization.get("algorithm_version") != ALGORITHM_VERSION
        or authorization.get("state")
        != "m3_source_predictor_daymet_order_repair_authorized"
        or parent.get("commit_sha256") != PARENT_AUTHORIZATION_COMMIT_SHA256
        or not isinstance(parent_record, Mapping)
        or parent_record.get("commit_sha256") != PARENT_AUTHORIZATION_COMMIT_SHA256
        or not isinstance(incident, Mapping)
        or incident.get("classification")
        != "correct_six_daymet_variables_passed_in_wrong_order"
        or incident.get("parent_worker_order") != list(PARENT_DAYMET_VARIABLES)
        or incident.get("cmr_parser_required_order") != list(DEFAULT_DAYMET_VARIABLES)
        or incident.get("same_variable_set") is not True
        or incident.get("acquisition_file_count") != 0
        or incident.get("status_reported_task_plan_sha256")
        != PARENT_CANONICAL_TASK_PLAN_SHA256
        or incident.get("status_plan_is_stale_relative_to_sqlite_internal_hash") is not True
        or incident.get("status_task_plan_semantics")
        != "parent_canonical_plan_digest_not_sqlite_model_runs_task_plan_sha256"
        or incident.get("authoritative_sqlite_task_plan_sha256")
        != SQLITE_TASK_PLAN_SHA256
        or authorization.get("repair_contract") != _repair_contract()
        or authorization.get("permissions") != _permissions()
        or authorization.get("authorization_access_audit") != _authorization_access_audit()
        or authorization.get("next_safe_stage")
        != "resume_same_predictor_queue_with_daymet_order_repair_runner"
        or not isinstance(records, list)
        or records != _code_records(root)
        or authorization.get("code_identity", {}).get("set_sha256")
        != canonical_sha256(records)
    ):
        raise M3SourcePredictorDaymetOrderRepairError("Repair authorization scope drifted.")
    if _record_path(root, parent_record, label="Parent authorization") != settings.authorization:
        raise M3SourcePredictorDaymetOrderRepairError("Parent authorization path changed.")
    initial = incident.get("queue_snapshot")
    if not isinstance(initial, Mapping):
        raise M3SourcePredictorDaymetOrderRepairError("Incident queue snapshot changed.")
    _validate_initial_snapshot(initial)
    for label, expected_path, exact_record in (
        ("Incident queue", settings.database, INCIDENT_QUEUE_RECORD),
        ("Incident status", settings.status, INCIDENT_STATUS_RECORD),
    ):
        record = incident.get("queue_database" if label.endswith("queue") else "status")
        if not isinstance(record, Mapping):
            raise M3SourcePredictorDaymetOrderRepairError(f"{label} record changed.")
        pure = PurePosixPath(str(record.get("path", "")))
        if (
            pure != PurePosixPath(_relative(root, expected_path))
            or dict(record) != exact_record
            or not isinstance(record.get("bytes"), int)
            or not _is_sha256(record.get("sha256"))
        ):
            raise M3SourcePredictorDaymetOrderRepairError(f"{label} evidence changed.")


def load_m3_source_predictor_daymet_order_repair_runtime_permit(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    require_paused: bool,
) -> dict[str, Any]:
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root, config_path)
    path = _inside(root, authorization_path, label="Repair authorization")
    if path != (root / AUTHORIZATION_PATH).resolve():
        raise M3SourcePredictorDaymetOrderRepairError("Repair authorization path changed.")
    authorization = _read_repair_authorization(path)
    parent = authenticate_m3_source_predictor_extension_authorization(
        root, settings.authorization, settings.config_path
    )
    if parent.get("commit_sha256") != PARENT_AUTHORIZATION_COMMIT_SHA256:
        raise M3SourcePredictorDaymetOrderRepairError("Parent permit drifted.")
    _validate_static_authorization(root, settings, authorization, parent)
    snapshot = _queue_snapshot(settings.database, parent)
    _validate_safe_progress(snapshot, authorization, require_paused=require_paused)
    return authorization


def _validate_daymet_repair_marker(
    payload: Mapping[str, Any],
    *,
    city_id: str,
    year: int,
    repair_commit_sha256: str,
    expected_query_sha256: str,
) -> None:
    granules = payload.get("granules")
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != "m3-source-predictor-extension-v1"
        or payload.get("state") != "daymet_granules_complete"
        or payload.get("authorization_commit_sha256")
        != PARENT_AUTHORIZATION_COMMIT_SHA256
        or payload.get("daymet_order_repair_authorization_commit_sha256")
        != repair_commit_sha256
        or payload.get("cmr_fetch_variable_order") != list(DEFAULT_DAYMET_VARIABLES)
        or payload.get("city_id") != city_id
        or payload.get("year") != year
        or not isinstance(granules, list)
        or len(granules) != len(PARENT_DAYMET_VARIABLES)
        or tuple(row.get("variable") for row in granules if isinstance(row, Mapping))
        != tuple(PARENT_DAYMET_VARIABLES)
        or any(
            not isinstance(row, Mapping)
            or set(row)
            != {"concept_id", "title", "variable", "year", "size_mb", "updated_at"}
            or not str(row.get("concept_id", ""))
            or not str(row.get("title", ""))
            or row.get("year") != year
            or not isinstance(row.get("size_mb"), (int, float))
            or isinstance(row.get("size_mb"), bool)
            or not math.isfinite(float(row["size_mb"]))
            or float(row["size_mb"]) <= 0
            or (
                row.get("updated_at") is not None
                and not isinstance(row.get("updated_at"), str)
            )
            for row in granules
        )
        or payload.get("query_sha256") != expected_query_sha256
        or payload.get("official_cmr_http_status") != 200
        or payload.get("urls_or_credentials_persisted") is not False
        or payload.get("target_or_landsat_values_read") is not False
    ):
        raise M3SourcePredictorDaymetOrderRepairError("Daymet repair marker drifted.")


def _expected_daymet_query_sha256(
    footprint: Mapping[str, Any],
    *,
    year: int,
) -> str:
    """Rebuild the frozen public CMR params without any network or value read."""

    from la_heat.daymet_grid import DAYMET_CMR_COLLECTION_ID
    from la_heat.multicity.source_footprints import _daymet_query_params

    geography = footprint.get("geography_input")
    bbox = geography.get("bbox_wgs84") if isinstance(geography, Mapping) else None
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise M3SourcePredictorDaymetOrderRepairError("Daymet footprint bbox changed.")
    params = _daymet_query_params(
        collection_concept_id=DAYMET_CMR_COLLECTION_ID,
        year=year,
        bbox_wgs84=bbox,
    )
    return canonical_sha256(params)


class DaymetOrderRepairAdapter(SafeExistingBuilderAdapter):
    """Parent adapter with only the CMR metadata argument order repaired."""

    def __init__(
        self,
        settings: Any,
        permit: Mapping[str, Any],
        phase: str,
        repair_authorization_path: Path = AUTHORIZATION_PATH,
    ) -> None:
        self.repair_authorization_path = repair_authorization_path
        self.repair = load_m3_source_predictor_daymet_order_repair_runtime_permit(
            settings.root,
            repair_authorization_path,
            config_path=settings.config_path,
            require_paused=False,
        )
        super().__init__(settings, permit, phase)

    def _repair_gate(self) -> dict[str, Any]:
        try:
            current = load_m3_source_predictor_daymet_order_repair_runtime_permit(
                self.settings.root,
                self.repair_authorization_path,
                config_path=self.settings.config_path,
                require_paused=False,
            )
        except Exception as error:
            if isinstance(error, M3SourcePredictorDaymetOrderRepairPermitError):
                raise
            raise M3SourcePredictorDaymetOrderRepairPermitError(
                "Daymet-order repair permit authentication failed."
            ) from error
        if current != self.repair:
            raise M3SourcePredictorDaymetOrderRepairPermitError(
                "Daymet-order repair permit changed after adapter construction."
            )
        return current

    def acquire_daymet_metadata(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._repair_gate()
        if self.phase != ONLINE_PHASE:
            raise M3SourcePredictorWorkerError("Daymet metadata requires online phase.")
        city_id = str(payload.get("city_id", ""))
        year = int(payload.get("year", -1))
        if city_id not in ("houston_tx", "chicago_il") or year not in range(2020, 2025):
            raise M3SourcePredictorWorkerError("Daymet metadata task changed.")
        destination = (
            self.settings.acquisition_root / "daymet" / city_id / str(year) / "GRANULES.json"
        )
        footprint = self._source_footprint(city_id)
        expected_query_sha256 = _expected_daymet_query_sha256(footprint, year=year)
        if destination.is_file():
            existing = _read_committed(destination, label="Daymet granules")
            try:
                _validate_daymet_repair_marker(
                    existing,
                    city_id=city_id,
                    year=year,
                    repair_commit_sha256=self.repair["commit_sha256"],
                    expected_query_sha256=expected_query_sha256,
                )
            except M3SourcePredictorDaymetOrderRepairError as error:
                raise M3SourcePredictorCompatibilityError(
                    "Daymet repair marker drifted."
                ) from error
            return {
                "state": existing["state"],
                "files": [_file_record(self.settings.root, destination)],
            }

        import requests

        from la_heat.daymet_grid import DAYMET_CMR_COLLECTION_ID, DAYMET_CMR_GRANULES_URL
        from la_heat.multicity.source_footprints import fetch_daymet_granule_metadata

        bbox = footprint["geography_input"]["bbox_wgs84"]
        with requests.Session() as session:
            frame, _raw, query = fetch_daymet_granule_metadata(
                session,
                endpoint=DAYMET_CMR_GRANULES_URL,
                collection_concept_id=DAYMET_CMR_COLLECTION_ID,
                year=year,
                variables=DEFAULT_DAYMET_VARIABLES,
                bbox_wgs84=bbox,
            )
        rows = [
            {
                "concept_id": str(row.concept_id),
                "title": str(row.title),
                "variable": str(row.variable),
                "year": int(row.year),
                "size_mb": float(row.size_mb),
                "updated_at": None if row.updated_at is None else str(row.updated_at),
            }
            for row in frame.itertuples(index=False)
        ]
        by_variable = {row["variable"]: row for row in rows}
        if set(by_variable) != set(PARENT_DAYMET_VARIABLES):
            raise M3SourcePredictorCompatibilityError("Daymet repair granule set changed.")
        ordered = [by_variable[variable] for variable in PARENT_DAYMET_VARIABLES]
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": "m3-source-predictor-extension-v1",
                "state": "daymet_granules_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "daymet_order_repair_authorization_commit_sha256": self.repair[
                    "commit_sha256"
                ],
                "cmr_fetch_variable_order": list(DEFAULT_DAYMET_VARIABLES),
                "city_id": city_id,
                "year": year,
                "granules": ordered,
                "query_sha256": query["query_sha256"],
                "official_cmr_http_status": query["http_status"],
                "urls_or_credentials_persisted": False,
                "target_or_landsat_values_read": False,
            }
        )
        _validate_daymet_repair_marker(
            marker,
            city_id=city_id,
            year=year,
            repair_commit_sha256=self.repair["commit_sha256"],
            expected_query_sha256=expected_query_sha256,
        )
        _write_or_authenticate(marker, destination)
        return {"state": marker["state"], "files": [_file_record(self.settings.root, destination)]}

    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._repair_gate()
        return super().execute(kind, payload)


def _repair_metadata_markers(root: Path, authorization: Mapping[str, Any]) -> list[dict[str, Any]]:
    settings = load_predictor_extension_settings(root)
    parent = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    snapshot = _queue_snapshot(settings.database, parent)
    completed = {row["task_id"]: row for row in snapshot["completed_tasks"]}
    parent_adapter = SafeExistingBuilderAdapter(settings, parent, ONLINE_PHASE)
    markers: list[dict[str, Any]] = []
    for city_id in ("houston_tx", "chicago_il"):
        footprint = parent_adapter._source_footprint(city_id)
        for year in range(2020, 2025):
            path = settings.acquisition_root / "daymet" / city_id / str(year) / "GRANULES.json"
            payload = _read_committed(path, label=f"{city_id} {year} Daymet repair marker")
            _validate_daymet_repair_marker(
                payload,
                city_id=city_id,
                year=year,
                repair_commit_sha256=str(authorization["commit_sha256"]),
                expected_query_sha256=_expected_daymet_query_sha256(
                    footprint, year=year
                ),
            )
            file_record = _file_record(root, path)
            task_id = f"daymet-metadata-{city_id}-{year}"
            task = completed.get(task_id)
            if not isinstance(task, Mapping) or task.get("result") != {
                "state": "daymet_granules_complete",
                "files": [file_record],
            }:
                raise M3SourcePredictorDaymetOrderRepairError(
                    "Daymet metadata task result does not bind its repair marker."
                )
            markers.append(
                {
                    "city_id": city_id,
                    "year": year,
                    **file_record,
                    "commit_sha256": str(payload["commit_sha256"]),
                }
            )
    return markers


def _write_or_check(payload: Mapping[str, Any], path: Path) -> dict[str, Any]:
    expected = dict(payload)
    if path.is_file():
        observed = _read_repair_authorization(path)
        if observed != expected:
            raise M3SourcePredictorDaymetOrderRepairError("Repair completion drifted.")
        return observed
    _write_exclusive(expected, path)
    return expected


def _validate_online_terminal(
    snapshot: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> tuple[str, ...]:
    specs = task_specs_from_predictor_authorization(parent)
    online_ids = tuple(spec.task_id for spec in specs if spec.kind in ONLINE_KINDS)
    offline_ids = tuple(spec.task_id for spec in specs if spec.kind in OFFLINE_KINDS)
    states = {row["task_id"]: row for row in snapshot["task_states"]}
    expected_counts = {
        "pending": len(offline_ids),
        "running": 0,
        "complete": len(online_ids),
        "quarantined": 0,
        "total": len(specs),
    }
    if (
        len(online_ids) != 78
        or len(offline_ids) != 7
        or snapshot.get("counts") != expected_counts
        or snapshot.get("desired_state") != "paused"
        or snapshot.get("active_lease_count") != 0
        or any(states[task_id]["status"] != "complete" for task_id in online_ids)
        or any(
            states[task_id]["status"] != "pending"
            or states[task_id]["attempt"] != 0
            or states[task_id]["claim_generation"] != 0
            or states[task_id]["error_type"] is not None
            or states[task_id]["result"] is not None
            for task_id in offline_ids
        )
    ):
        raise M3SourcePredictorDaymetOrderRepairError(
            "Online acquisition did not stop at its exact parent-derived phase boundary."
        )
    return online_ids


def _build_daymet_order_repair_acquisition_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root)
    authorization = load_m3_source_predictor_daymet_order_repair_runtime_permit(
        root, authorization_path, require_paused=False
    )
    parent_runtime = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    online_snapshot = _queue_snapshot(settings.database, parent_runtime)
    online_ids = _validate_online_terminal(online_snapshot, parent_runtime)
    expected_online_counts = {
        "pending": 7,
        "running": 0,
        "complete": 78,
        "quarantined": 0,
        "total": 85,
    }
    parent = authenticate_source_predictor_acquisition_completion(
        root,
        authorization_path=settings.authorization,
        config_path=settings.config_path,
    )
    parent_runtime = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    current_snapshot = _queue_snapshot(settings.database, parent_runtime)
    finalize_matches = [
        row
        for row in current_snapshot["completed_tasks"]
        if row["task_id"] == "acquisition-complete"
    ]
    if len(finalize_matches) != 1 or finalize_matches[0]["result"] != {
        "state": parent["state"],
        "commit_sha256": parent["commit_sha256"],
    }:
        raise M3SourcePredictorDaymetOrderRepairError(
            "The durable acquisition finalizer result changed."
        )
    markers = _repair_metadata_markers(root, authorization)
    finalize = next(
        row
        for row in online_snapshot["completed_tasks"]
        if row["task_id"] == "acquisition-complete"
    )
    if finalize["result"] != {
        "state": parent["state"],
        "commit_sha256": parent["commit_sha256"],
    }:
        raise M3SourcePredictorDaymetOrderRepairError(
            "The finalize_acquisition task does not bind the parent completion."
        )
    payload = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_daymet_order_repair_acquisition_complete",
            "repair_authorization_commit_sha256": authorization["commit_sha256"],
            "parent_acquisition_completion_commit_sha256": parent["commit_sha256"],
            "parent_acquisition_completion": _file_record(
                root,
                settings.acquisition_completion,
                commit_sha256=str(parent["commit_sha256"]),
            ),
            "online_terminal_counts": expected_online_counts,
            "online_task_ids_sha256": canonical_sha256(online_ids),
            "daymet_metadata_markers": markers,
            "audit": {
                "network_or_href_reads_during_authentication": 0,
                "blind_test_city_accessed": False,
                "target_or_landsat_values_read": False,
                "model_fit_select_predict_or_score_performed": False,
            },
            "next_safe_stage": "offline_assembly_with_same_daymet_order_repair_runner",
        }
    )
    return payload


def _create_daymet_order_repair_acquisition_completion_locked(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    payload = _build_daymet_order_repair_acquisition_completion(root, authorization_path)
    path = _inside(root, ACQUISITION_REPAIR_COMPLETION_PATH, label="Repair acquisition")
    return _write_or_check(payload, path)


def create_daymet_order_repair_acquisition_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root)
    with exclusive_predictor_worker(settings.worker_lock):
        return _create_daymet_order_repair_acquisition_completion_locked(
            root, authorization_path
        )


def authenticate_daymet_order_repair_acquisition_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    expected_path = _inside(root, ACQUISITION_REPAIR_COMPLETION_PATH, label="Repair acquisition")
    observed = _read_repair_authorization(expected_path)
    authorization = load_m3_source_predictor_daymet_order_repair_runtime_permit(
        root, authorization_path, require_paused=False
    )
    settings = load_predictor_extension_settings(root)
    parent = authenticate_source_predictor_acquisition_completion(
        root,
        authorization_path=settings.authorization,
        config_path=settings.config_path,
    )
    parent_runtime = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    current_snapshot = _queue_snapshot(settings.database, parent_runtime)
    finalize_matches = [
        row
        for row in current_snapshot["completed_tasks"]
        if row["task_id"] == "acquisition-complete"
    ]
    if len(finalize_matches) != 1 or finalize_matches[0]["result"] != {
        "state": parent["state"],
        "commit_sha256": parent["commit_sha256"],
    }:
        raise M3SourcePredictorDaymetOrderRepairError(
            "The durable acquisition finalizer result changed."
        )
    expected = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_daymet_order_repair_acquisition_complete",
            "repair_authorization_commit_sha256": authorization["commit_sha256"],
            "parent_acquisition_completion_commit_sha256": parent["commit_sha256"],
            "parent_acquisition_completion": _file_record(
                root,
                settings.acquisition_completion,
                commit_sha256=str(parent["commit_sha256"]),
            ),
            "online_terminal_counts": {
                "pending": 7,
                "running": 0,
                "complete": 78,
                "quarantined": 0,
                "total": 85,
            },
            "online_task_ids_sha256": canonical_sha256(
                tuple(
                    spec.task_id
                    for spec in task_specs_from_predictor_authorization(
                        load_m3_source_predictor_extension_runtime_permit(
                            root, settings.authorization, settings.config_path
                        )
                    )
                    if spec.kind in ONLINE_KINDS
                )
            ),
            "daymet_metadata_markers": _repair_metadata_markers(root, authorization),
            "audit": {
                "network_or_href_reads_during_authentication": 0,
                "blind_test_city_accessed": False,
                "target_or_landsat_values_read": False,
                "model_fit_select_predict_or_score_performed": False,
            },
            "next_safe_stage": "offline_assembly_with_same_daymet_order_repair_runner",
        }
    )
    if observed != expected:
        raise M3SourcePredictorDaymetOrderRepairError("Repair acquisition drifted.")
    return observed


def _build_daymet_order_repair_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    authorization = load_m3_source_predictor_daymet_order_repair_runtime_permit(
        root, authorization_path, require_paused=True
    )
    settings = load_predictor_extension_settings(root)
    parent_runtime = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    snapshot = _queue_snapshot(settings.database, parent_runtime)
    if snapshot["counts"] != {
        "pending": 0,
        "running": 0,
        "complete": 85,
        "quarantined": 0,
        "total": 85,
    }:
        raise M3SourcePredictorDaymetOrderRepairError("Predictor queue is not terminal.")
    acquisition = authenticate_daymet_order_repair_acquisition_completion(
        root, authorization_path
    )
    parent = authenticate_source_predictors_46_completion(root)
    parent_audit = parent.get("audit")
    expected_parent_audit = {
        "offline_network_requests": 0,
        "offline_href_reads": 0,
        "blind_test_city_accessed": False,
        "target_or_landsat_values_read": False,
        "model_fit_select_predict_or_score_performed": False,
        "old_predictor_or_runtime_mutated": False,
    }
    if parent_audit != expected_parent_audit:
        raise M3SourcePredictorDaymetOrderRepairError("Parent offline audit changed.")
    payload = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_daymet_order_repair_complete",
            "repair_authorization_commit_sha256": authorization["commit_sha256"],
            "repair_acquisition_completion_commit_sha256": acquisition["commit_sha256"],
            "parent_predictor_completion_commit_sha256": parent["commit_sha256"],
            "parent_predictor_completion": _file_record(
                root,
                settings.predictor_completion,
                commit_sha256=str(parent["commit_sha256"]),
            ),
            "terminal_queue_snapshot_sha256": canonical_sha256(snapshot),
            "audit": {
                "offline_network_requests": 0,
                "offline_href_reads": 0,
                "blind_test_city_accessed": False,
                "target_or_landsat_values_read": False,
                "model_fit_select_predict_or_score_performed": False,
                "old_predictor_or_runtime_mutated": False,
            },
            "next_safe_stage": "bind_both_parent_and_repair_completions_before_joint_authorization",
        }
    )
    return payload


def _create_daymet_order_repair_completion_locked(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    payload = _build_daymet_order_repair_completion(root, authorization_path)
    completion_path = _inside(root, REPAIR_COMPLETION_PATH, label="Repair completion")
    return _write_or_check(payload, completion_path)


def create_daymet_order_repair_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root)
    with exclusive_predictor_worker(settings.worker_lock):
        return _create_daymet_order_repair_completion_locked(root, authorization_path)


def authenticate_daymet_order_repair_completion(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = _project_root(project_root)
    observed = _read_repair_authorization(
        _inside(root, REPAIR_COMPLETION_PATH, label="Repair completion")
    )
    expected = _build_daymet_order_repair_completion(root, authorization_path)
    if observed != expected:
        raise M3SourcePredictorDaymetOrderRepairError("Repair completion drifted.")
    return observed


def _recover_stale_running_state_after_lock(
    settings: Any,
    snapshot: Mapping[str, Any],
) -> ModelRunQueue:
    """Pause only a lock-orphaned run with no live lease; never rewrite a task."""

    running = snapshot.get("running_tasks")
    if not isinstance(running, list):
        raise M3SourcePredictorDaymetOrderRepairError("Running-task snapshot changed.")
    now = time.time()
    if any(
        not isinstance(row, Mapping)
        or not isinstance(row.get("lease_owner"), str)
        or not row["lease_owner"]
        or not isinstance(row.get("claim_generation"), int)
        or isinstance(row.get("claim_generation"), bool)
        or int(row["claim_generation"]) <= 0
        or not isinstance(row.get("lease_expires_at"), (int, float))
        or isinstance(row.get("lease_expires_at"), bool)
        or not math.isfinite(float(row["lease_expires_at"]))
        or float(row["lease_expires_at"]) > now
        for row in running
    ):
        raise M3SourcePredictorDaymetOrderRepairError(
            "A live predictor task lease remains after acquiring the worker lock."
        )
    queue = ModelRunQueue(settings.database)
    if snapshot.get("desired_state") == "running" or running:
        queue.set_desired_state(RUN_ID, "paused")
    elif snapshot.get("desired_state") != "paused":
        raise M3SourcePredictorDaymetOrderRepairError("Predictor desired state changed.")
    return queue


def execute_daymet_order_repair_worker(
    project_root: str | Path,
    *,
    phase: str,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")
    root = _project_root(project_root)
    settings = load_predictor_extension_settings(root, config_path)
    with exclusive_predictor_worker(settings.worker_lock):
        repair = load_m3_source_predictor_daymet_order_repair_runtime_permit(
            root, authorization_path, config_path=settings.config_path, require_paused=False
        )
        parent = load_m3_source_predictor_extension_runtime_permit(
            root, settings.authorization, settings.config_path
        )
        if source_predictor_run_id(parent) != RUN_ID:
            raise M3SourcePredictorDaymetOrderRepairError("Parent run ID changed.")
        snapshot = _queue_snapshot(settings.database, parent)
        queue = _recover_stale_running_state_after_lock(settings, snapshot)
        if phase == OFFLINE_PHASE:
            authenticate_daymet_order_repair_acquisition_completion(root, authorization_path)
        adapter = DaymetOrderRepairAdapter(
            settings,
            parent,
            phase,
            _inside(root, authorization_path, label="Repair authorization"),
        )
        queue.set_desired_state(RUN_ID, "running")
        try:
            result = _execute_unlocked(
                settings=settings,
                permit=parent,
                options=PredictorWorkerOptions(phase=phase),
                adapter=adapter,
            )
        except BaseException:
            queue.set_desired_state(RUN_ID, "paused")
            raise
        if phase == ONLINE_PHASE and result.get("state") == "paused":
            if result.get("counts_by_kind", {}).get("finalize_acquisition", {}).get(
                "complete"
            ) == 1:
                _create_daymet_order_repair_acquisition_completion_locked(
                    root, authorization_path
                )
        if phase == OFFLINE_PHASE and result.get("state") == "paused":
            if result.get("counts", {}).get("complete") == 85:
                _create_daymet_order_repair_completion_locked(root, authorization_path)
        return {
            **result,
            "daymet_order_repair_authorization_commit_sha256": repair["commit_sha256"],
        }
