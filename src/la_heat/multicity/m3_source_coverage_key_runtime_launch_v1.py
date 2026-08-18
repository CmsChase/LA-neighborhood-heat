"""Append-only launch amendment for the M3 coverage-key repair.

The first repair canary stopped before any raster read because the inherited
``prepare_worker_v2`` path performed an idempotent SQLite write transaction.
That transaction changed only the database file hash bound as incident
evidence; the complete paused queue snapshot remained identical.  This module
authorizes a launch path which authenticates the existing repair runtime permit
without calling ``prepare_worker_v2`` or ``initialize_source_runtime_v2`` and
treats both database hashes as evidence, never as a mutable runtime input.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    AUTHORIZATION_PATH as PARENT_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    RUN_ID,
    TASK_PLAN_SHA256,
    _file_record,
    _inside,
    _is_committed,
    _queue_snapshot,
    _read_committed,
    _relative,
    _validate_safe_progress,
    _with_commit,
    _write_exclusive,
    authenticate_m3_source_coverage_key_repair_value_gate,
    load_m3_source_coverage_key_repair_runtime_permit,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    EXPECTED_TASK_COUNT,
    load_runner_settings_v2,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-coverage-key-runtime-launch-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_COVERAGE_KEY_RUNTIME_LAUNCH_V1_AUTHORIZATION.json"
)

PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256: Final = (
    "3496e3c96483f2fdd78aa6d9368d0896eb05bfca083d71f2b724df9abcbc3145"
)
INCIDENT_DATABASE_SHA256: Final = "869b3b2ab50afa44d1c6fcdba6a083566b5ad2f7cc2626e5b492ced28adfc85b"
POST_PREPARE_DATABASE_SHA256: Final = (
    "72dee20b87e5c94c43a8e6efe382b48cfcc6aa55048b4bc0da90d7612617d6d8"
)
DATABASE_BYTES: Final = 815104
DATABASE_RELATIVE_PATH: Final = (
    "data/interim/multicity/m3_source_development_v2/runtime/tasks.sqlite"
)
CANARY_STDERR_BYTES: Final = 1880
CANARY_STDERR_SHA256: Final = "735b3839bed6b02763f8f440ab92f8f786b616e0f3553d8910fdca1baeb93e2b"

CANARY_STDOUT_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/qa-repair-worker.log"
)
CANARY_STDERR_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/qa-repair-worker.err.log"
)

CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_coverage_key_runtime_launch_v1.py",
    "src/la_heat/multicity/m3_source_development_engine_coverage_key_runtime_launch_v1.py",
    "scripts/authorize_m3_source_coverage_key_runtime_launch_v1.py",
    "scripts/run_m3_source_coverage_key_runtime_launch_v1.py",
)

CAUSE_CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_development_worker_v2.py",
    "src/la_heat/multicity/m3_source_development_runtime_v2.py",
    "src/la_heat/model_run_queue.py",
)


class M3SourceCoverageKeyRuntimeLaunchError(RuntimeError):
    """Raised when the launch amendment leaves its narrow scope."""


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    pure = PurePosixPath(str(record.get("path", "")))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourceCoverageKeyRuntimeLaunchError(f"{label} path is unsafe.")
    path = (root / Path(*pure.parts)).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError(f"{label} file record changed.")
    return path


def _unique_line(path: Path, needle: str, *, label: str) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines, start=1) if needle in line]
    if len(matches) != 1:
        raise M3SourceCoverageKeyRuntimeLaunchError(f"{label} evidence changed.")
    return matches[0]


def _transaction_cause_evidence(root: Path) -> dict[str, Any]:
    worker = root / CAUSE_CODE_PATHS[0]
    runtime = root / CAUSE_CODE_PATHS[1]
    queue = root / CAUSE_CODE_PATHS[2]
    return {
        "classification": "idempotent_queue_initialization_changed_sqlite_bytes_only",
        "prepare_worker_call": {
            **_file_record(root, worker),
            "line": _unique_line(
                worker,
                "initialized = initialize_source_runtime_v2(settings.root)",
                label="Prepare-worker initialization",
            ),
        },
        "runtime_queue_open": {
            **_file_record(root, runtime),
            "line": _unique_line(
                runtime,
                "queue = ModelRunQueue(settings.database)",
                label="Runtime queue construction",
            ),
        },
        "runtime_idempotent_initialize": {
            **_file_record(root, runtime),
            "line": _unique_line(
                runtime,
                'queue.initialize_run(run_id, specs, desired_state="paused")',
                label="Runtime idempotent initialization",
            ),
        },
        "queue_wal_schema_initialization": {
            **_file_record(root, queue),
            "line": _unique_line(
                queue,
                'connection.execute("PRAGMA journal_mode = WAL")',
                label="Queue WAL initialization",
            ),
        },
        "semantic_task_or_run_row_change_observed": False,
    }


def _permissions() -> dict[str, bool]:
    return {
        "authenticate_parent_repair_via_nonterminal_runtime_permit": True,
        "skip_prepare_worker_v2_and_initialize_source_runtime_v2": True,
        "reuse_existing_queue_without_rebuild_reset_or_task_rewrite": True,
        "modify_task_plan_or_rebuild_reset_rewrite_queue": False,
        "execute_only_existing_317_qa_four_city_compile_and_final_tasks": True,
        "bind_launch_amendment_to_new_base_lock_and_final_completion": True,
        "treat_old_and_new_database_hashes_as_incident_evidence_only": True,
        "modify_parent_repair_authorization_or_hash_bound_code": False,
        "modify_completed_logical_tasks_or_physical_cache": False,
        "network_or_href_reads": False,
        "blind_city_asset_predictor_qa_or_target_access": False,
        "predictor_read_or_build": False,
        "fit_select_predict_or_score": False,
        "change_year_city_candidate_or_support_gate": False,
    }


def _runtime_contract() -> dict[str, Any]:
    return {
        "compute_workers": 1,
        "download_workers": 0,
        "network_requests_allowed": False,
        "href_reads_allowed": False,
        "existing_run_id": RUN_ID,
        "existing_task_plan_sha256": TASK_PLAN_SHA256,
        "expected_task_count": EXPECTED_TASK_COUNT,
        "worker_lock_acquired_before_runtime_permit_factory_and_running_state": True,
        "prepare_worker_v2_or_initialize_source_runtime_v2_allowed": False,
        "model_run_queue_schema_open_after_all_permits_authenticated": True,
        "task_plan_rebuild_reset_or_rewrite_allowed": False,
        "pre_running_factory_failure_leaves_queue_unchanged": True,
        "post_running_state_exception_restores_paused_state": True,
    }


def _access_audit() -> dict[str, bool]:
    return {
        "authorization_read_landsat_or_qa_raster_values": False,
        "authorization_read_predictor_or_target_values": False,
        "authorization_accessed_blind_city_data": False,
        "authorization_modified_queue_cache_or_existing_manifest": False,
        "authorization_fit_selected_predicted_or_scored": False,
    }


def _load_parent_runtime_permit(root: Path, *, require_terminal_queue: bool) -> dict[str, Any]:
    parent = load_m3_source_coverage_key_repair_runtime_permit(
        root,
        PARENT_AUTHORIZATION_PATH,
        require_terminal_queue=require_terminal_queue,
    )
    if parent.get("commit_sha256") != PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256:
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "Parent coverage-key repair authorization changed."
        )
    return parent


def build_m3_source_coverage_key_runtime_launch_authorization(
    project_root: str | Path,
) -> dict[str, Any]:
    """Build the amendment without reading any raster or target value."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    parent = _load_parent_runtime_permit(root, require_terminal_queue=False)
    snapshot = _queue_snapshot(settings.database)
    if snapshot != parent.get("initial_paused_queue_snapshot"):
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "Post-canary queue differs from the exact authorized semantic snapshot."
        )
    _validate_safe_progress(snapshot, terminal=False)
    old_database = parent.get("inputs", {}).get("incident_queue_database")
    current_database = _file_record(root, settings.database)
    if (
        not isinstance(old_database, Mapping)
        or old_database.get("sha256") != INCIDENT_DATABASE_SHA256
        or old_database.get("bytes") != DATABASE_BYTES
        or current_database.get("sha256") != POST_PREPARE_DATABASE_SHA256
        or current_database.get("bytes") != DATABASE_BYTES
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "The exact idempotent SQLite hash transition changed."
        )
    stdout_path = root / CANARY_STDOUT_PATH
    stderr_path = root / CANARY_STDERR_PATH
    stderr = stderr_path.read_text(encoding="utf-8")
    if (
        not stdout_path.is_file()
        or stdout_path.stat().st_size != 0
        or "Coverage-key repair authorization drifted." not in stderr
        or "RasterioIOError" in stderr
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "The pre-raster launch canary evidence changed."
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_source_coverage_key_runtime_launch_authorized",
        "launch_scope": "bypass_idempotent_prepare_and_use_existing_runtime_permit",
        "parent_repair_authorization_commit_sha256": parent["commit_sha256"],
        "v2_run_id": RUN_ID,
        "v2_task_plan_sha256": TASK_PLAN_SHA256,
        "paused_semantic_queue_snapshot": snapshot,
        "incident_evidence": {
            "database_hash_transition_is_evidence_only": True,
            "before_prepare_database_record": dict(old_database),
            "after_prepare_database_record": current_database,
            "database_size_unchanged": True,
            "semantic_snapshot_exactly_unchanged": True,
            "transaction_cause": _transaction_cause_evidence(root),
            "failed_canary_stdout": _file_record(root, stdout_path),
            "failed_canary_stderr": _file_record(root, stderr_path),
            "failed_before_raster_value_access": True,
        },
        "inputs": {
            "parent_repair_authorization": _file_record(
                root,
                root / PARENT_AUTHORIZATION_PATH,
                commit_sha256=parent["commit_sha256"],
            ),
        },
        "permissions": _permissions(),
        "runtime_contract": _runtime_contract(),
        "code_identity": {relative: _file_record(root, root / relative) for relative in CODE_PATHS},
        "cause_code_identity": {
            relative: _file_record(root, root / relative) for relative in CAUSE_CODE_PATHS
        },
        "access_audit": _access_audit(),
        "next_safe_stage": (
            "launch_existing_offline_qa_queue_without_prepare_worker_or_runtime_initialize"
        ),
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def create_m3_source_coverage_key_runtime_launch_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    destination = _inside(root, authorization_path, label="Launch authorization")
    if destination.is_relative_to(settings.cache_root) or destination.is_relative_to(
        settings.database.parent
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "Launch authorization cannot be written into an old runtime or cache."
        )
    payload = build_m3_source_coverage_key_runtime_launch_authorization(root)
    _write_exclusive(payload, destination)
    return authenticate_m3_source_coverage_key_runtime_launch_authorization(root, destination)


def authenticate_m3_source_coverage_key_runtime_launch_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Launch authorization")
    observed = _read_committed(path, label="Coverage-key runtime launch authorization")
    expected = build_m3_source_coverage_key_runtime_launch_authorization(root)
    if observed != expected:
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "Coverage-key runtime launch authorization drifted."
        )
    return observed


def _authenticate_launch_payload(
    root: Path,
    permit: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> None:
    unsigned = dict(permit)
    unsigned.pop("commit_sha256", None)
    claim_id = unsigned.pop("claim_id", None)
    incident = permit.get("incident_evidence")
    before = (
        incident.get("before_prepare_database_record") if isinstance(incident, Mapping) else None
    )
    after = incident.get("after_prepare_database_record") if isinstance(incident, Mapping) else None
    transaction_cause = incident.get("transaction_cause") if isinstance(incident, Mapping) else None
    canary_stdout = incident.get("failed_canary_stdout") if isinstance(incident, Mapping) else None
    canary_stderr = incident.get("failed_canary_stderr") if isinstance(incident, Mapping) else None
    parent_database = parent.get("inputs", {}).get("incident_queue_database")
    if (
        not _is_committed(permit)
        or permit.get("schema_version") != SCHEMA_VERSION
        or permit.get("algorithm_version") != ALGORITHM_VERSION
        or permit.get("state") != "m3_source_coverage_key_runtime_launch_authorized"
        or permit.get("launch_scope") != "bypass_idempotent_prepare_and_use_existing_runtime_permit"
        or permit.get("parent_repair_authorization_commit_sha256")
        != PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256
        or parent.get("commit_sha256") != PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256
        or permit.get("v2_run_id") != RUN_ID
        or permit.get("v2_task_plan_sha256") != TASK_PLAN_SHA256
        or permit.get("paused_semantic_queue_snapshot")
        != parent.get("initial_paused_queue_snapshot")
        or permit.get("permissions") != _permissions()
        or permit.get("runtime_contract") != _runtime_contract()
        or permit.get("access_audit") != _access_audit()
        or claim_id != canonical_sha256(unsigned)
        or not isinstance(incident, Mapping)
        or incident.get("database_hash_transition_is_evidence_only") is not True
        or incident.get("database_size_unchanged") is not True
        or incident.get("semantic_snapshot_exactly_unchanged") is not True
        or incident.get("failed_before_raster_value_access") is not True
        or not isinstance(before, Mapping)
        or before != parent_database
        or before.get("path") != DATABASE_RELATIVE_PATH
        or before.get("sha256") != INCIDENT_DATABASE_SHA256
        or before.get("bytes") != DATABASE_BYTES
        or not isinstance(after, Mapping)
        or after.get("path") != DATABASE_RELATIVE_PATH
        or after.get("sha256") != POST_PREPARE_DATABASE_SHA256
        or after.get("bytes") != DATABASE_BYTES
        or transaction_cause != _transaction_cause_evidence(root)
        or canary_stdout
        != {
            "path": CANARY_STDOUT_PATH.as_posix(),
            "bytes": 0,
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }
        or canary_stderr
        != {
            "path": CANARY_STDERR_PATH.as_posix(),
            "bytes": CANARY_STDERR_BYTES,
            "sha256": CANARY_STDERR_SHA256,
        }
        or set(permit.get("inputs", {})) != {"parent_repair_authorization"}
        or permit.get("next_safe_stage")
        != ("launch_existing_offline_qa_queue_without_prepare_worker_or_runtime_initialize")
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError(
            "Coverage-key runtime launch permit scope changed."
        )
    code_identity = permit.get("code_identity")
    cause_identity = permit.get("cause_code_identity")
    if not isinstance(code_identity, Mapping) or set(code_identity) != set(CODE_PATHS):
        raise M3SourceCoverageKeyRuntimeLaunchError("Launch code identity set changed.")
    if not isinstance(cause_identity, Mapping) or set(cause_identity) != set(CAUSE_CODE_PATHS):
        raise M3SourceCoverageKeyRuntimeLaunchError("Cause code identity set changed.")
    for label, records in (("Launch code", code_identity), ("Cause code", cause_identity)):
        for relative, record in records.items():
            if (
                not isinstance(record, Mapping)
                or _relative(root, _record_path(root, record, label=f"{label} {relative}"))
                != relative
            ):
                raise M3SourceCoverageKeyRuntimeLaunchError(f"{label} identity changed.")
    inputs = permit.get("inputs")
    parent_record = (
        inputs.get("parent_repair_authorization") if isinstance(inputs, Mapping) else None
    )
    if (
        not isinstance(parent_record, Mapping)
        or parent_record.get("commit_sha256") != PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256
        or _record_path(root, parent_record, label="Parent repair authorization")
        != (root / PARENT_AUTHORIZATION_PATH).resolve()
    ):
        raise M3SourceCoverageKeyRuntimeLaunchError("Parent repair authorization input changed.")


def authenticate_m3_source_coverage_key_runtime_launch_bundle(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    *,
    require_terminal_queue: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate both append-only permits while allowing queue progress."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    parent = _load_parent_runtime_permit(root, require_terminal_queue=require_terminal_queue)
    permit = _read_committed(
        _inside(root, authorization_path, label="Launch authorization"),
        label="Coverage-key runtime launch authorization",
    )
    _authenticate_launch_payload(root, permit, parent)
    _validate_safe_progress(_queue_snapshot(settings.database), terminal=require_terminal_queue)
    return permit, parent


def authenticate_m3_source_coverage_key_runtime_launch_value_gate(
    project_root: str | Path,
    expected_authorization: Mapping[str, Any],
    parent_authorization: Mapping[str, Any],
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> None:
    """Cheap per-value gate; neither historical database hash is re-read."""

    root = Path(project_root).resolve()
    observed = _read_committed(
        _inside(root, authorization_path, label="Launch authorization"),
        label="Coverage-key runtime launch authorization",
    )
    if observed != dict(expected_authorization):
        raise M3SourceCoverageKeyRuntimeLaunchError("Coverage-key runtime launch permit changed.")
    authenticate_m3_source_coverage_key_repair_value_gate(
        root,
        parent_authorization,
        PARENT_AUTHORIZATION_PATH,
    )


__all__ = [
    "AUTHORIZATION_PATH",
    "PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256",
    "authenticate_m3_source_coverage_key_runtime_launch_authorization",
    "authenticate_m3_source_coverage_key_runtime_launch_bundle",
    "authenticate_m3_source_coverage_key_runtime_launch_value_gate",
    "build_m3_source_coverage_key_runtime_launch_authorization",
    "create_m3_source_coverage_key_runtime_launch_authorization",
]
