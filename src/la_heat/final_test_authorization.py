"""Fail-closed preflight and one-time authorization for the locked 2025 test."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.final_model import FinalModelError, authenticate_final_build_provenance
from la_heat.formal_model_lock import (
    FORMAL_MODEL_LOCK_ALGORITHM_VERSION,
    FORMAL_MODEL_LOCK_SCHEMA_VERSION,
)
from la_heat.provenance import canonical_sha256, sha256_file

AUTHORIZATION_SCHEMA_VERSION: Final = 1
AUTHORIZATION_ALGORITHM_VERSION: Final = "one-time-final-test-authorization-v1"
DEFAULT_MODEL_LOCK_PATH: Final = Path("manifests/model_lock/MODEL_LOCK.json")
DEFAULT_AUTHORIZATION_PATH: Final = Path(
    "manifests/final_test_2025/AUTHORIZATION.json"
)
FORMAL_LOCK_STATE: Final = "frozen_for_one_time_2025_evaluation"
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_GIT_OID: Final = re.compile(r"[0-9a-f]{40,64}")
_MODEL_IDS: Final = ("B1", "M2")
_INPUT_FILE_LOCKS: Final = {
    "model_dataset_provenance",
    "model_table",
    "feature_registry",
    "split_promotion",
    "row_groups",
    "fold_definitions",
    "spatial_buffer_geoids",
    "model_selection_freeze",
    "model_selection_config",
}


class FinalTestAuthorizationError(ValueError):
    """Raised when authorization cannot be proven safe without reading 2025 data."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestAuthorizationError(f"Cannot read valid {label}: {path}") from error
    if not isinstance(payload, dict):
        raise FinalTestAuthorizationError(f"{label} must be a JSON object.")
    return payload


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or _SHA256.fullmatch(recorded) is None:
        raise FinalTestAuthorizationError(f"{label} canonical commit is missing or invalid.")
    if canonical_sha256(working) != recorded:
        raise FinalTestAuthorizationError(f"{label} canonical commit does not match.")
    return recorded


def _resolve_project_path(
    root: Path,
    value: str | Path,
    *,
    label: str,
    require_file: bool = True,
) -> tuple[Path, str]:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise FinalTestAuthorizationError(
            f"{label} must remain inside the project root."
        ) from error
    if require_file and not resolved.is_file():
        raise FinalTestAuthorizationError(f"{label} is missing: {relative}")
    return resolved, relative


def _require_exact_path(
    root: Path,
    value: str | Path,
    expected: Path,
    *,
    label: str,
    require_file: bool,
) -> Path:
    resolved, _ = _resolve_project_path(
        root,
        value,
        label=label,
        require_file=require_file,
    )
    expected_resolved = (root / expected).resolve()
    if resolved != expected_resolved:
        raise FinalTestAuthorizationError(
            f"{label} must be exactly {expected.as_posix()}."
        )
    return resolved


def _git(
    root: Path,
    *arguments: str,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=text,
        check=False,
    )
    if result.returncode != 0:
        raise FinalTestAuthorizationError(
            f"Git command failed: git {' '.join(arguments)}"
        )
    return result


def _git_state(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "--verify", "HEAD").stdout.strip().lower()
    if _GIT_OID.fullmatch(head) is None:
        raise FinalTestAuthorizationError("A valid Git HEAD is required for authorization.")
    status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
    entries = [line for line in status.splitlines() if line.strip()]
    if entries:
        raise FinalTestAuthorizationError(
            "Authorization requires a completely clean Git working tree."
        )
    return {
        "head": head,
        "working_tree_clean": True,
        "status_entry_count": 0,
    }


def _committed_file_record(
    root: Path,
    path: Path,
    *,
    head: str,
    label: str,
) -> dict[str, Any]:
    resolved, relative = _resolve_project_path(root, path, label=label)
    committed_oid = _git(root, "rev-parse", "--verify", f"{head}:{relative}").stdout.strip().lower()
    current_oid = _git(root, "hash-object", "--", relative).stdout.strip().lower()
    if (
        _GIT_OID.fullmatch(committed_oid) is None
        or current_oid != committed_oid
    ):
        raise FinalTestAuthorizationError(
            f"{label} is not the exact file committed at current Git HEAD."
        )
    return {
        "path": relative,
        "sha256": sha256_file(resolved),
        "bytes": resolved.stat().st_size,
        "git_blob_oid": committed_oid,
    }


def _locked_file(
    root: Path,
    record: object,
    *,
    label: str,
    sha_key: str = "sha256",
    verify_commit: bool = False,
) -> tuple[Path, dict[str, Any] | None]:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise FinalTestAuthorizationError(f"{label} file lock is invalid.")
    path, _ = _resolve_project_path(root, record["path"], label=label)
    expected_sha = record.get(sha_key)
    if not isinstance(expected_sha, str) or _SHA256.fullmatch(expected_sha) is None:
        raise FinalTestAuthorizationError(f"{label} SHA-256 lock is invalid.")
    if sha256_file(path) != expected_sha:
        raise FinalTestAuthorizationError(f"{label} SHA-256 lock failed.")
    expected_bytes = record.get("bytes")
    if expected_bytes is not None and (
        isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 1
        or path.stat().st_size != expected_bytes
    ):
        raise FinalTestAuthorizationError(f"{label} byte lock failed.")
    if not verify_commit:
        return path, None
    payload = _read_json(path, label=label)
    commit = _verify_commit(payload, label=label)
    if commit != record.get("commit_sha256"):
        raise FinalTestAuthorizationError(f"{label} canonical commit lock failed.")
    return path, payload


def _authenticate_input_locks(root: Path, locks: object) -> None:
    if not isinstance(locks, dict) or not _INPUT_FILE_LOCKS.issubset(locks):
        raise FinalTestAuthorizationError("Formal model-lock input hashes are incomplete.")
    for name in sorted(_INPUT_FILE_LOCKS):
        record = locks[name]
        verify_commit = isinstance(record, dict) and "commit_sha256" in record
        _locked_file(
            root,
            record,
            label=f"input lock {name}",
            verify_commit=verify_commit,
        )
    context = locks.get("context_commits")
    if not isinstance(context, dict) or set(context) != {
        "model_dataset_commit_sha256",
        "split_promotion_commit_sha256",
        "model_selection_commit_sha256",
    }:
        raise FinalTestAuthorizationError("Formal model-lock context commitments are invalid.")
    if any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in context.values()
    ):
        raise FinalTestAuthorizationError("Formal model-lock context hash is invalid.")
    semantic = locks.get("feature_registry_semantic_sha256")
    if not isinstance(semantic, str) or _SHA256.fullmatch(semantic) is None:
        raise FinalTestAuthorizationError("Feature-registry semantic lock is invalid.")


def _authenticate_model_records(formal: object, build: object) -> None:
    if (
        not isinstance(formal, dict)
        or set(formal) != set(_MODEL_IDS)
        or not isinstance(build, dict)
        or set(build) != set(_MODEL_IDS)
    ):
        raise FinalTestAuthorizationError("Formal lock requires exact B1 and M2 models.")
    comparisons = {
        "artifact_path": "path",
        "fitted_pipeline_sha256": "sha256",
        "fitted_pipeline_bytes": "bytes",
        "selected_candidate_id": "selected_candidate_id",
        "selected_parameters": "selected_parameters",
        "random_state": "random_state",
        "feature_names": "feature_names",
        "feature_count": "feature_count",
        "training_row_count": "training_row_count",
        "training_date_count": "training_date_count",
        "training_spatial_block_count": "training_spatial_block_count",
        "training_keys_sha256": "training_keys_sha256",
    }
    for model_id in _MODEL_IDS:
        locked = formal[model_id]
        built = build[model_id]
        if not isinstance(locked, dict) or not isinstance(built, dict):
            raise FinalTestAuthorizationError(f"{model_id} model lock is invalid.")
        if any(locked.get(left) != built.get(right) for left, right in comparisons.items()):
            raise FinalTestAuthorizationError(
                f"{model_id} model no longer matches the authenticated development build."
            )


def _authenticate_formal_model_lock(root: Path, path: Path) -> tuple[dict[str, Any], str]:
    payload = _read_json(path, label="formal model lock")
    commit = _verify_commit(payload, label="formal model lock")
    if (
        payload.get("schema_version") != FORMAL_MODEL_LOCK_SCHEMA_VERSION
        or payload.get("algorithm_version") != FORMAL_MODEL_LOCK_ALGORITHM_VERSION
        or payload.get("state") != FORMAL_LOCK_STATE
        or payload.get("formal_model_lock_written") is not True
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_locked") is not True
        or payload.get("final_test_unlocked") is not False
        or payload.get("final_test_used") is not False
        or payload.get("final_test_values_read") is not False
        or payload.get("contains_final_test_year") is not False
        or payload.get("one_time_final_evaluation_authorized") is not False
    ):
        raise FinalTestAuthorizationError(
            "MODEL_LOCK.json is not the untouched formal one-time 2025 lock."
        )

    _, staging = _locked_file(
        root,
        payload.get("staging_record"),
        label="formal-lock staging record",
        verify_commit=True,
    )
    if staging is None or staging.get("final_test_locked") is not True:
        raise FinalTestAuthorizationError("Staging record is not locked.")

    development_record = payload.get("development_build")
    development_path, _ = _locked_file(
        root,
        development_record,
        label="final-model development build",
        verify_commit=True,
    )
    try:
        development = authenticate_final_build_provenance(
            development_path,
            load_models=False,
        )
    except FinalModelError as error:
        raise FinalTestAuthorizationError(str(error)) from error
    if (
        not isinstance(development_record, dict)
        or development.get("commit_sha256") != development_record.get("commit_sha256")
        or development.get("run_id") != development_record.get("run_id")
        or development.get("final_test_values_read") is not False
    ):
        raise FinalTestAuthorizationError("Development build commitment changed after locking.")

    configuration = payload.get("configuration")
    _locked_file(
        root,
        configuration,
        label="frozen final-model configuration",
        sha_key="file_sha256",
    )
    if development.get("analysis_config") != configuration:
        raise FinalTestAuthorizationError("Frozen configuration disagrees with the build.")
    if development.get("input_locks") != payload.get("input_locks"):
        raise FinalTestAuthorizationError("Formal input locks disagree with the build.")
    _authenticate_input_locks(root, payload.get("input_locks"))
    _authenticate_model_records(payload.get("models"), development.get("models"))

    robustness = payload.get("robustness_provenance")
    if not isinstance(robustness, list) or len(robustness) != 3:
        raise FinalTestAuthorizationError("Exact robustness-provenance locks are required.")
    for index, record in enumerate(robustness):
        if not isinstance(record, dict) or record.get("authenticated") is not True:
            raise FinalTestAuthorizationError("A robustness record is not authenticated.")
        _locked_file(
            root,
            record,
            label=f"robustness provenance {index + 1}",
            verify_commit=True,
        )
    return payload, commit


def _authorization_absent(path: Path) -> None:
    if os.path.lexists(path):
        raise FileExistsError(
            "AUTHORIZATION.json already exists and one-time authorization can never be overwritten."
        )


def preflight_final_test_2025(
    *,
    evaluator_module: str | Path,
    evaluator_config: str | Path,
    model_lock_path: str | Path = DEFAULT_MODEL_LOCK_PATH,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Validate authorization readiness without reading or authorizing 2025 values."""

    root = _project_root().resolve()
    lock_path = _require_exact_path(
        root,
        model_lock_path,
        DEFAULT_MODEL_LOCK_PATH,
        label="Formal model lock",
        require_file=True,
    )
    output = _require_exact_path(
        root,
        authorization_path,
        DEFAULT_AUTHORIZATION_PATH,
        label="Authorization output",
        require_file=False,
    )
    _authorization_absent(output)
    git = _git_state(root)
    head = git["head"]
    lock_git_record = _committed_file_record(
        root,
        lock_path,
        head=head,
        label="Formal model lock",
    )
    _, lock_commit = _authenticate_formal_model_lock(root, lock_path)
    lock_file_sha256 = lock_git_record["sha256"]

    module_path, _ = _resolve_project_path(
        root,
        evaluator_module,
        label="Evaluator module",
    )
    config_path, _ = _resolve_project_path(
        root,
        evaluator_config,
        label="Evaluator configuration",
    )
    if module_path.suffix.casefold() != ".py":
        raise FinalTestAuthorizationError("Evaluator module must be a Python source file.")
    if config_path.suffix.casefold() != ".toml":
        raise FinalTestAuthorizationError("Evaluator configuration must be a TOML file.")
    evaluator_module_record = _committed_file_record(
        root,
        module_path,
        head=head,
        label="Evaluator module",
    )
    evaluator_config_record = _committed_file_record(
        root,
        config_path,
        head=head,
        label="Evaluator configuration",
    )
    return {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "algorithm_version": AUTHORIZATION_ALGORITHM_VERSION,
        "state": "eligible_but_not_authorized",
        "final_test_year": 2025,
        "authorized": False,
        "values_read": False,
        "evaluator_code_git_commit": head,
        "working_tree_clean": True,
        "git_status_entry_count": 0,
        "formal_model_lock": {
            **{
                key: value
                for key, value in lock_git_record.items()
                if key != "sha256"
            },
            "file_sha256": lock_file_sha256,
            "commit_sha256": lock_commit,
        },
        "evaluator_module": evaluator_module_record,
        "evaluator_config": evaluator_config_record,
    }


def _atomic_create_json(payload: dict[str, Any], destination: Path) -> None:
    """Publish a complete JSON file atomically while refusing any existing name."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.partial"
    )
    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    except FileExistsError as error:
        raise FileExistsError(
            "AUTHORIZATION.json already exists and one-time authorization can never be overwritten."
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def authorize_final_test_2025(
    *,
    evaluator_module: str | Path,
    evaluator_config: str | Path,
    model_lock_path: str | Path = DEFAULT_MODEL_LOCK_PATH,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION_PATH,
    approve_one_time_2025: bool = False,
) -> dict[str, Any]:
    """Create the immutable authorization marker after explicit one-time approval."""

    if approve_one_time_2025 is not True:
        raise PermissionError(
            "2025 final-test authorization requires explicit --approve-one-time-2025."
        )
    preflight = preflight_final_test_2025(
        evaluator_module=evaluator_module,
        evaluator_config=evaluator_config,
        model_lock_path=model_lock_path,
        authorization_path=authorization_path,
    )
    root = _project_root().resolve()
    output = _require_exact_path(
        root,
        authorization_path,
        DEFAULT_AUTHORIZATION_PATH,
        label="Authorization output",
        require_file=False,
    )

    # Recheck the clean HEAD and every committed authorization input directly
    # before exclusive publication. No final-test path is accepted or opened.
    git = _git_state(root)
    if git["head"] != preflight["evaluator_code_git_commit"]:
        raise FinalTestAuthorizationError("Git HEAD changed during authorization preflight.")
    for key in ("formal_model_lock", "evaluator_module", "evaluator_config"):
        record = preflight[key]
        path, _ = _resolve_project_path(root, record["path"], label=key)
        expected_sha = record.get("file_sha256", record.get("sha256"))
        if sha256_file(path) != expected_sha:
            raise FinalTestAuthorizationError(f"{key} changed during authorization preflight.")

    payload: dict[str, Any] = {
        **preflight,
        "state": "authorized_for_one_time_2025_evaluation",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "authorized": True,
        "values_read": False,
        "authorization_consumed": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    _atomic_create_json(payload, output)
    return payload
