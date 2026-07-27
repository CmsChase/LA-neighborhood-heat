"""Fail-closed preflight and one-time authorization for the locked 2025 test."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.final_model import FinalModelError, authenticate_final_build_provenance
from la_heat.final_test_state_lock import (
    DEFAULT_FINAL_TEST_STATE_LOCK_PATH,
    FinalTestStateLock,
)
from la_heat.formal_model_lock import (
    FORMAL_MODEL_LOCK_ALGORITHM_VERSION,
    FORMAL_MODEL_LOCK_SCHEMA_VERSION,
)
from la_heat.provenance import (
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

AUTHORIZATION_SCHEMA_VERSION: Final = 1
AUTHORIZATION_ALGORITHM_VERSION: Final = "one-time-final-test-authorization-v1"
DEFAULT_MODEL_LOCK_PATH: Final = Path("manifests/model_lock/MODEL_LOCK.json")
DEFAULT_AUTHORIZATION_PATH: Final = Path(
    "manifests/final_test_2025/AUTHORIZATION.json"
)
DEFAULT_EVALUATION_READINESS_PATH: Final = Path(
    "manifests/final_test_2025/evaluation/EVALUATION_READINESS.json"
)
EVALUATION_READINESS_SCHEMA_VERSION: Final = 1
EVALUATION_READINESS_ALGORITHM_VERSION: Final = "final-evaluation-readiness-v1"
FORMAL_LOCK_STATE: Final = "frozen_for_one_time_2025_evaluation"
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_GIT_OID: Final = re.compile(r"[0-9a-f]{40,64}")
_MODEL_IDS: Final = ("B1", "M2")
_EXTRA_EVALUATION_RUNTIME_PACKAGES: Final = (
    "joblib",
    "matplotlib",
    "scikit-learn",
    "scipy",
)
_EVALUATION_PIPELINE_FILES: Final = (
    "pyproject.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/config.py",
    "src/la_heat/final_evaluation_protocol.py",
    "src/la_heat/final_evaluation_reporting.py",
    "src/la_heat/final_evaluation_targets.py",
    "src/la_heat/final_model.py",
    "src/la_heat/final_test_authorization.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/final_test_predictor_assembler.py",
    "src/la_heat/final_test_state_lock.py",
    "src/la_heat/formal_model_lock.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/inventory.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/metrics.py",
    "src/la_heat/model_endpoint_diagnostics.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/target_builder.py",
    "src/la_heat/targets.py",
    "scripts/prepare_final_evaluation.py",
    "scripts/execute_locked_final_evaluation.py",
)
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


def _git_state(
    root: Path,
    *,
    allowed_untracked_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    head = _git(root, "rev-parse", "--verify", "HEAD").stdout.strip().lower()
    if _GIT_OID.fullmatch(head) is None:
        raise FinalTestAuthorizationError("A valid Git HEAD is required for authorization.")
    status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
    entries = [line for line in status.splitlines() if line.strip()]
    disallowed = []
    allowed = []
    for entry in entries:
        path = entry[3:].replace("\\", "/") if len(entry) >= 4 else ""
        if entry.startswith("?? ") and path in allowed_untracked_paths:
            allowed.append(entry)
        else:
            disallowed.append(entry)
    if disallowed:
        raise FinalTestAuthorizationError(
            "Authorization requires a completely clean Git working tree."
        )
    return {
        "head": head,
        "working_tree_clean": True,
        "status_entry_count": len(entries),
        "allowed_untracked_state_entry_count": len(allowed),
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


def _authenticate_evaluation_readiness(
    root: Path,
    path: Path,
    *,
    head: str,
    evaluator_module_record: dict[str, Any],
    evaluator_config_record: dict[str, Any],
    formal_model_lock_record: dict[str, Any],
    formal_model_lock: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _read_json(path, label="final-evaluation readiness")
    commit = _verify_commit(payload, label="final-evaluation readiness")
    request = payload.get("request")
    if (
        payload.get("schema_version") != EVALUATION_READINESS_SCHEMA_VERSION
        or payload.get("algorithm_version") != EVALUATION_READINESS_ALGORITHM_VERSION
        or payload.get("state") != "ready_target_blind"
        or payload.get("target_blind") is not True
        or payload.get("values_read") is not False
        or payload.get("authorized") is not False
        or payload.get("code_git_commit") != head
        or not isinstance(request, dict)
        or payload.get("request_sha256") != canonical_sha256(request)
        or payload.get("evaluator_module") != evaluator_module_record
        or payload.get("evaluator_config") != evaluator_config_record
        or payload.get("formal_model_lock") != formal_model_lock_record
    ):
        raise FinalTestAuthorizationError(
            "Final-evaluation readiness is not the exact target-blind contract "
            "for the current committed evaluator."
        )
    _validate_readiness_request_contract(
        root,
        head=head,
        config_path=root / evaluator_config_record["path"],
        request=request,
        formal_model_lock_record=formal_model_lock_record,
        formal_model_lock=formal_model_lock,
    )
    return payload, {
        "path": path.relative_to(root).as_posix(),
        "file_sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "commit_sha256": commit,
        "request_sha256": payload["request_sha256"],
    }


def _validate_readiness_request_contract(
    root: Path,
    *,
    head: str,
    config_path: Path,
    request: dict[str, Any],
    formal_model_lock_record: dict[str, Any],
    formal_model_lock: dict[str, Any],
) -> None:
    """Recompute the security-critical readiness contract before approval."""

    try:
        with config_path.open("rb") as handle:
            configuration = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise FinalTestAuthorizationError(
            "Cannot parse the committed final-evaluation configuration."
        ) from error
    if (
        configuration.get("schema_version") != 1
        or configuration.get("algorithm_version")
        != "one-time-final-evaluation-v1"
        or configuration.get("state")
        != "frozen_before_2025_target_values"
        or request.get("configuration_semantic_sha256")
        != canonical_sha256(configuration)
        or request.get("code_git_commit") != head
        or request.get("formal_model_lock") != formal_model_lock_record
        or request.get("models") != formal_model_lock.get("models")
    ):
        raise FinalTestAuthorizationError(
            "Readiness request identity/model commitments are invalid."
        )
    for section in (
        "locks",
        "analysis",
        "bootstrap",
        "success_gates",
        "hotspot",
        "publication",
    ):
        if request.get(section) != configuration.get(section):
            raise FinalTestAuthorizationError(
                f"Readiness request {section} differs from committed configuration."
            )
    publication = configuration.get("publication")
    if (
        not isinstance(publication, dict)
        or request.get("exact_output_files")
        != publication.get("exact_output_files")
    ):
        raise FinalTestAuthorizationError(
            "Readiness output contract differs from committed configuration."
        )
    raw_paths = configuration.get("paths")
    if not isinstance(raw_paths, dict):
        raise FinalTestAuthorizationError("Readiness configuration paths are invalid.")
    expected_paths: dict[str, str] = {}
    for name, value in raw_paths.items():
        if not isinstance(value, str):
            raise FinalTestAuthorizationError(
                f"Readiness path {name} is not a string."
            )
        resolved = (root / value).resolve()
        try:
            expected_paths[name] = resolved.relative_to(root).as_posix()
        except ValueError as error:
            raise FinalTestAuthorizationError(
                f"Readiness path {name} escapes the project root."
            ) from error
    if request.get("paths") != expected_paths:
        raise FinalTestAuthorizationError(
            "Readiness resolved paths differ from committed configuration."
        )

    code_files = request.get("code_files")
    if not isinstance(code_files, dict) or set(code_files) != set(
        _EVALUATION_PIPELINE_FILES
    ):
        raise FinalTestAuthorizationError(
            "Readiness code-file set is incomplete or expanded."
        )
    for relative in _EVALUATION_PIPELINE_FILES:
        current = _committed_file_record(
            root,
            root / relative,
            head=head,
            label=f"Evaluation code {relative}",
        )
        if code_files.get(relative) != current:
            raise FinalTestAuthorizationError(
                f"Readiness code commitment drifted for {relative}."
            )
    pipeline = request.get("pipeline")
    current_pipeline_sha256, current_pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=_EVALUATION_PIPELINE_FILES,
        algorithm_version="one-time-final-evaluation-v1",
    )
    current_extra_packages: dict[str, str] = {}
    for name in _EXTRA_EVALUATION_RUNTIME_PACKAGES:
        try:
            current_extra_packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            current_extra_packages[name] = "MISSING"
    current_extended_runtime = {
        "python": platform.python_version(),
        "packages": current_extra_packages,
    }
    if (
        not isinstance(pipeline, dict)
        or request.get("pipeline_sha256") != canonical_sha256(pipeline)
        or request.get("pipeline_sha256") != current_pipeline_sha256
        or pipeline != current_pipeline
        or request.get("extended_runtime") != current_extended_runtime
        or pipeline.get("algorithm_version") != "one-time-final-evaluation-v1"
        or pipeline.get("files")
        != {
            relative: code_files[relative]["sha256"]
            for relative in sorted(_EVALUATION_PIPELINE_FILES)
        }
    ):
        raise FinalTestAuthorizationError(
            "Readiness code/runtime fingerprint is invalid."
        )

    locks = configuration["locks"]
    predictors = request.get("predictors")
    inventory = request.get("landsat_inventory")
    inventory_locks = (
        inventory.get("locks") if isinstance(inventory, dict) else None
    )
    analysis = configuration["analysis"]
    if (
        not isinstance(predictors, dict)
        or predictors.get("provenance_file_sha256")
        != locks.get("predictor_provenance_file_sha256")
        or predictors.get("provenance_commit_sha256")
        != locks.get("predictor_provenance_commit_sha256")
        or predictors.get("file_sha256")
        != locks.get("predictor_file_sha256")
        or predictors.get("schema_sha256")
        != locks.get("predictor_schema_sha256")
        or predictors.get("semantic_sha256")
        != locks.get("predictor_semantic_sha256")
        or predictors.get("key_semantic_sha256")
        != locks.get("predictor_key_semantic_sha256")
        or predictors.get("row_count") != analysis.get("expected_key_count")
        or predictors.get("feature_count")
        != analysis.get("expected_model_feature_count")
        or predictors.get("target_blind") is not True
        or predictors.get("contains_target_or_qa_values") is not False
        or not isinstance(inventory, dict)
        or inventory.get("inventory_file_sha256")
        != locks.get("landsat_inventory_file_sha256")
        or inventory.get("inventory_commit_sha256")
        != locks.get("landsat_inventory_commit_sha256")
        or not isinstance(inventory_locks, dict)
        or inventory_locks.get("key_universe_semantic_sha256")
        != locks.get("landsat_key_semantic_sha256")
        or inventory.get("shared_predictor_key_semantic_sha256")
        != predictors.get("key_semantic_sha256")
        or inventory.get("scene_count")
        != analysis.get("expected_inventory_scene_count")
        or inventory.get("physical_overpass_count")
        != analysis.get("expected_inventory_overpass_count")
        or inventory.get("tract_count") != analysis.get("expected_tract_count")
        or inventory.get("key_count") != analysis.get("expected_key_count")
        or inventory.get("target_blind") is not True
        or inventory.get("target_assets_opened") is not False
        or inventory.get("target_or_qa_values_read") is not False
    ):
        raise FinalTestAuthorizationError(
            "Readiness predictor/inventory commitments are invalid."
        )

    research = request.get("locked_research_config")
    if not isinstance(research, dict):
        raise FinalTestAuthorizationError(
            "Readiness locked research-config record is invalid."
        )
    current_research = _committed_file_record(
        root,
        root / "configs/research.toml",
        head=head,
        label="Locked research configuration",
    )
    expected_research = {
        **current_research,
        "target_config_semantic_sha256": locks.get(
            "target_config_semantic_sha256"
        ),
        "unlock_final_test": False,
    }
    if (
        research != expected_research
        or current_research.get("sha256")
        != locks.get("locked_research_config_file_sha256")
    ):
        raise FinalTestAuthorizationError(
            "Readiness locked research configuration is invalid."
        )


def _preflight_final_test_2025_locked(
    *,
    evaluator_module: str | Path,
    evaluator_config: str | Path,
    model_lock_path: str | Path = DEFAULT_MODEL_LOCK_PATH,
    readiness_path: str | Path = DEFAULT_EVALUATION_READINESS_PATH,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Validate readiness while the caller owns the shared final-test lock."""

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
    readiness = _require_exact_path(
        root,
        readiness_path,
        DEFAULT_EVALUATION_READINESS_PATH,
        label="Final-evaluation readiness",
        require_file=True,
    )
    readiness_relative = readiness.relative_to(root).as_posix()
    git = _git_state(
        root,
        allowed_untracked_paths=frozenset({readiness_relative}),
    )
    head = git["head"]
    lock_git_record = _committed_file_record(
        root,
        lock_path,
        head=head,
        label="Formal model lock",
    )
    formal_model_lock, lock_commit = _authenticate_formal_model_lock(
        root,
        lock_path,
    )
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
    formal_model_lock_record = {
        **{
            key: value
            for key, value in lock_git_record.items()
            if key != "sha256"
        },
        "file_sha256": lock_file_sha256,
        "commit_sha256": lock_commit,
    }
    _, readiness_record = _authenticate_evaluation_readiness(
        root,
        readiness,
        head=head,
        evaluator_module_record=evaluator_module_record,
        evaluator_config_record=evaluator_config_record,
        formal_model_lock_record=formal_model_lock_record,
        formal_model_lock=formal_model_lock,
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
        "git_status_entry_count": git["status_entry_count"],
        "allowed_untracked_state_entry_count": git[
            "allowed_untracked_state_entry_count"
        ],
        "formal_model_lock": formal_model_lock_record,
        "evaluator_module": evaluator_module_record,
        "evaluator_config": evaluator_config_record,
        "evaluation_readiness": readiness_record,
    }


def preflight_final_test_2025(
    *,
    evaluator_module: str | Path,
    evaluator_config: str | Path,
    model_lock_path: str | Path = DEFAULT_MODEL_LOCK_PATH,
    readiness_path: str | Path = DEFAULT_EVALUATION_READINESS_PATH,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Validate readiness without reading or authorizing 2025 values."""

    root = _project_root().resolve()
    with FinalTestStateLock(root / DEFAULT_FINAL_TEST_STATE_LOCK_PATH):
        return _preflight_final_test_2025_locked(
            evaluator_module=evaluator_module,
            evaluator_config=evaluator_config,
            model_lock_path=model_lock_path,
            readiness_path=readiness_path,
            authorization_path=authorization_path,
        )


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
    readiness_path: str | Path = DEFAULT_EVALUATION_READINESS_PATH,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION_PATH,
    approve_one_time_2025: bool = False,
) -> dict[str, Any]:
    """Create the immutable authorization marker after explicit one-time approval."""

    if approve_one_time_2025 is not True:
        raise PermissionError(
            "2025 final-test authorization requires explicit --approve-one-time-2025."
        )
    root = _project_root().resolve()
    with FinalTestStateLock(root / DEFAULT_FINAL_TEST_STATE_LOCK_PATH):
        preflight = _preflight_final_test_2025_locked(
            evaluator_module=evaluator_module,
            evaluator_config=evaluator_config,
            model_lock_path=model_lock_path,
            readiness_path=readiness_path,
            authorization_path=authorization_path,
        )
        output = _require_exact_path(
            root,
            authorization_path,
            DEFAULT_AUTHORIZATION_PATH,
            label="Authorization output",
            require_file=False,
        )

        # Recheck the clean HEAD and every committed authorization input
        # directly before exclusive publication. The shared lock remains owned
        # from the initial absence check through the atomic create.
        readiness_relative = preflight["evaluation_readiness"]["path"]
        git = _git_state(
            root,
            allowed_untracked_paths=frozenset({readiness_relative}),
        )
        if git["head"] != preflight["evaluator_code_git_commit"]:
            raise FinalTestAuthorizationError(
                "Git HEAD changed during authorization preflight."
            )
        for key in (
            "formal_model_lock",
            "evaluator_module",
            "evaluator_config",
            "evaluation_readiness",
        ):
            record = preflight[key]
            path, _ = _resolve_project_path(root, record["path"], label=key)
            expected_sha = record.get("file_sha256", record.get("sha256"))
            if sha256_file(path) != expected_sha:
                raise FinalTestAuthorizationError(
                    f"{key} changed during authorization preflight."
                )
        _authorization_absent(output)

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
