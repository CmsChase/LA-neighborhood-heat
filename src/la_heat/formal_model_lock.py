"""One-way promotion of an authenticated staging record to ``MODEL_LOCK.json``."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.final_model import (
    authenticate_final_build_provenance,
    load_final_model_config,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

FORMAL_MODEL_LOCK_SCHEMA_VERSION: Final = 1
FORMAL_MODEL_LOCK_ALGORITHM_VERSION: Final = "formal-model-lock-v1"
FORMAL_MODEL_LOCK_FILENAME: Final = "MODEL_LOCK.json"
STAGING_FILENAME: Final = "MODEL_LOCK_STAGING.json"
_FINAL_ACCESS_FLAGS: Final = {
    "contains_final_test_year",
    "final_test_unlocked",
    "final_test_used",
    "final_test_values_read",
    "unlock_final_test",
}


class FormalModelLockError(ValueError):
    """Raised when formal model-lock promotion cannot be proven safe."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FormalModelLockError(f"Cannot read valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise FormalModelLockError(f"JSON input must be an object: {path}")
    return payload


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FormalModelLockError(f"{label} canonical commit is invalid.")
    return recorded


def _valid_git_sha(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise FormalModelLockError(
            f"Git command failed: git {' '.join(arguments)}"
        )
    return result


def _git_state(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "--verify", "HEAD").stdout.strip().lower()
    if not _valid_git_sha(head):
        raise FormalModelLockError("A valid Git HEAD is required for formal promotion.")
    status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
    entries = [line for line in status.splitlines() if line.strip()]
    return {
        "head": head,
        "working_tree_clean": not entries,
        "status_entry_count": len(entries),
    }


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(
            root,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
        ).returncode
        == 0
    )


def _git_changed_paths(root: Path, ancestor: str, descendant: str) -> tuple[str, ...]:
    result = _git(root, "diff", "--name-only", f"{ancestor}..{descendant}", "--")
    return tuple(
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    )


def _relative_path(path: Path, root: Path, *, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise FormalModelLockError(f"{label} must remain inside the project root.") from error


def _recursive_final_access(payload: object) -> bool:
    if isinstance(payload, dict):
        return any(
            (key in _FINAL_ACCESS_FLAGS and value is True)
            or _recursive_final_access(value)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(_recursive_final_access(value) for value in payload)
    return False


def _authenticate_robustness(records: object) -> list[dict[str, Any]]:
    if not isinstance(records, list) or len(records) != 3:
        raise FormalModelLockError("The exact three robustness records are required.")
    authenticated: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("authenticated") is not True:
            raise FormalModelLockError("A robustness record is not authenticated.")
        path_value = record.get("path")
        if not isinstance(path_value, str):
            raise FormalModelLockError("A robustness path is invalid.")
        path = Path(path_value).resolve()
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise FormalModelLockError("A robustness provenance file changed after staging.")
        payload = _read_json(path)
        if _verify_commit(payload, label=path.name) != record.get("commit_sha256"):
            raise FormalModelLockError("A robustness provenance commit changed after staging.")
        authenticated.append(dict(record))
    return authenticated


def _authenticate_models(
    staging_models: object,
    build_models: object,
) -> dict[str, dict[str, Any]]:
    if not isinstance(staging_models, dict) or set(staging_models) != {"B1", "M2"}:
        raise FormalModelLockError("Formal lock requires exact B1 and M2 staging records.")
    if not isinstance(build_models, dict) or set(build_models) != {"B1", "M2"}:
        raise FormalModelLockError("Authenticated build does not contain exact B1 and M2 models.")
    comparison = {
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
    result: dict[str, dict[str, Any]] = {}
    for model_id in ("B1", "M2"):
        staged = staging_models[model_id]
        built = build_models[model_id]
        if not isinstance(staged, dict) or not isinstance(built, dict):
            raise FormalModelLockError(f"{model_id} model record is invalid.")
        if any(staged.get(left) != built.get(right) for left, right in comparison.items()):
            raise FormalModelLockError(f"{model_id} model changed after staging.")
        result[model_id] = dict(staged)
    return result


def promote_formal_model_lock(
    staging_path: str | Path = "manifests/model_lock/MODEL_LOCK_STAGING.json",
    *,
    output_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    approve_formal_lock: bool = False,
) -> dict[str, Any]:
    """Create the immutable formal lock without granting final-test access."""

    if approve_formal_lock is not True:
        raise PermissionError("Formal promotion requires explicit --approve-formal-lock.")
    root = _project_root().resolve()
    staging_file = Path(staging_path).resolve()
    output = Path(output_path).resolve()
    if staging_file.name != STAGING_FILENAME:
        raise FormalModelLockError(f"Staging input must be named {STAGING_FILENAME}.")
    if output.name != FORMAL_MODEL_LOCK_FILENAME:
        raise FormalModelLockError(f"Formal output must be named {FORMAL_MODEL_LOCK_FILENAME}.")
    staging_relative = _relative_path(staging_file, root, label="Staging input")
    _relative_path(output, root, label="Formal output")
    if output.exists():
        raise FileExistsError("MODEL_LOCK.json already exists and will never be overwritten.")

    staging = _read_json(staging_file)
    staging_commit = _verify_commit(staging, label=STAGING_FILENAME)
    if (
        staging.get("state") != "eligible_for_later_formal_promotion"
        or staging.get("ready_for_formal_model_lock") is not True
        or staging.get("blockers") != []
        or staging.get("formal_model_lock_written") is not False
        or staging.get("final_test_year") != 2025
        or staging.get("final_test_locked") is not True
        or staging.get("final_test_unlocked") is not False
        or staging.get("final_test_used") is not False
        or staging.get("contains_final_test_year") is not False
        or _recursive_final_access(staging)
    ):
        raise FormalModelLockError("Staging is not an eligible locked-2025 record.")

    staged_git = staging.get("git")
    if not isinstance(staged_git, dict):
        raise FormalModelLockError("Staging Git record is invalid.")
    training_commit = staged_git.get("training_code_git_commit")
    if (
        not _valid_git_sha(training_commit)
        or staged_git.get("working_tree_clean") is not True
        or staged_git.get("status_entry_count") != 0
    ):
        raise FormalModelLockError("Staging was not generated from a clean Git commit.")
    current_git = _git_state(root)
    if not current_git["working_tree_clean"]:
        raise FormalModelLockError("Formal promotion requires a clean working tree.")
    current_head = current_git["head"]
    if not _git_is_ancestor(root, training_commit, current_head):
        raise FormalModelLockError("The staged training commit is not an ancestor of HEAD.")
    changed_paths = _git_changed_paths(root, training_commit, current_head)
    if changed_paths != (staging_relative,):
        raise FormalModelLockError(
            "Only the committed staging record may differ after its clean source commit."
        )

    development = staging.get("development_build")
    if not isinstance(development, dict) or not isinstance(development.get("path"), str):
        raise FormalModelLockError("Staged development-build record is invalid.")
    build_path = Path(development["path"]).resolve()
    if not build_path.is_file() or sha256_file(build_path) != development.get("sha256"):
        raise FormalModelLockError("Final-model build provenance changed after staging.")
    build = authenticate_final_build_provenance(build_path, load_models=True)
    if (
        build.get("commit_sha256") != development.get("commit_sha256")
        or build.get("run_id") != development.get("run_id")
        or build.get("final_test_values_read") is not False
        or _recursive_final_access(build)
    ):
        raise FormalModelLockError("Authenticated final build violates the staged lock.")

    configuration = staging.get("configuration")
    if not isinstance(configuration, dict) or not isinstance(configuration.get("path"), str):
        raise FormalModelLockError("Staged configuration record is invalid.")
    config = load_final_model_config(configuration["path"])
    if (
        sha256_file(config.path) != configuration.get("file_sha256")
        or config.semantic_sha256 != configuration.get("semantic_sha256")
        or build.get("analysis_config") != configuration
    ):
        raise FormalModelLockError("Frozen final-model configuration changed after staging.")
    if staging.get("input_locks") != build.get("input_locks"):
        raise FormalModelLockError("Authenticated input locks changed after staging.")

    models = _authenticate_models(staging.get("models"), build.get("models"))
    robustness = _authenticate_robustness(staging.get("robustness_provenance"))
    payload: dict[str, Any] = {
        "schema_version": FORMAL_MODEL_LOCK_SCHEMA_VERSION,
        "algorithm_version": FORMAL_MODEL_LOCK_ALGORITHM_VERSION,
        "state": "frozen_for_one_time_2025_evaluation",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "formal_model_lock_written": True,
        "staging_record": {
            "path": staging_relative,
            "sha256": sha256_file(staging_file),
            "commit_sha256": staging_commit,
        },
        "git": {
            "training_code_git_commit": training_commit,
            "staging_record_git_commit": current_head,
            "working_tree_clean_before_lock": True,
            "changed_paths_after_training_commit": list(changed_paths),
        },
        "development_build": dict(development),
        "configuration": dict(configuration),
        "input_locks": dict(staging["input_locks"]),
        "model_dataset_commit_sha256": staging["model_dataset_commit_sha256"],
        "split_promotion_commit_sha256": staging["split_promotion_commit_sha256"],
        "model_selection_commit_sha256": staging["model_selection_commit_sha256"],
        "selection_config_sha256": staging["selection_config_sha256"],
        "models": models,
        "primary_metric": staging["primary_metric"],
        "hotspot_rule": dict(staging["hotspot_rule"]),
        "planned_figures": list(staging["planned_figures"]),
        "robustness_provenance": robustness,
        "final_test_year": 2025,
        "final_test_locked": True,
        "final_test_unlocked": False,
        "final_test_used": False,
        "final_test_values_read": False,
        "contains_final_test_year": False,
        "one_time_final_evaluation_authorized": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, output)
    return payload
