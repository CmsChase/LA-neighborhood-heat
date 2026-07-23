"""Create a fail-closed MODEL_LOCK staging record without generating a formal lock."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.final_model import (
    FINAL_MODEL_ALGORITHM_VERSION,
    FINAL_MODEL_SCHEMA_VERSION,
    FinalModelError,
    authenticate_final_build_provenance,
    load_final_model_config,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

MODEL_LOCK_STAGING_SCHEMA_VERSION: Final = 1
MODEL_LOCK_STAGING_ALGORITHM_VERSION: Final = "model-lock-staging-v1"
FORMAL_MODEL_LOCK_FILENAME: Final = "MODEL_LOCK.json"
_ROBUSTNESS_ALGORITHMS: Final = {
    "feature_ablation_analysis_provenance.json": "feature-ablation-analysis-v1",
    "stqa2_sensitivity_provenance.json": "stqa2-pixel-label-sensitivity-v2",
    "robustness_reconciliation_provenance.json": (
        "development-robustness-reconciliation-v1"
    ),
}
_RECONCILIATION_SCOPE: Final = (
    "locked_2020_2024_development_robustness_reconciliation"
)
_RECONCILIATION_SOURCE_ALGORITHMS: Final = {
    "initial_results": "initial-model-result-analysis-v1",
    "endpoint": "model-endpoint-sensor-diagnostics-v1",
    "qa": "model-qa-diagnostics-v1",
    "residual_spatial": "residual-spatial-diagnostics-v1",
    "diagnostic_figures": "model-diagnostic-figures-v1",
    "feature_ablation": "feature-ablation-analysis-v1",
    "stqa2_sensitivity": "stqa2-pixel-label-sensitivity-v2",
}


class ModelLockStagingError(ValueError):
    """Raised when a staging record cannot be created safely."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelLockStagingError(f"Cannot read valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ModelLockStagingError(f"JSON input must be an object: {path}")
    return payload


def _verify_commit(payload: dict[str, Any], *, name: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if (
        not isinstance(recorded, str)
        or len(recorded) != 64
        or canonical_sha256(working) != recorded
    ):
        raise ModelLockStagingError(f"{name} commit is invalid.")
    return recorded


def _recursive_true(payload: object, names: set[str]) -> bool:
    if isinstance(payload, dict):
        return any(
            (key in names and value is True) or _recursive_true(value, names)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(_recursive_true(value, names) for value in payload)
    return False


def _sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ModelLockStagingError(f"{name} is not a lowercase SHA-256 digest.")
    return value


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelLockStagingError(f"{name} must be an object.")
    return value


def _source_lineage(
    source_name: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    authentication = _mapping(
        payload.get("input_authentication"),
        name=f"{source_name} input authentication",
    )
    if source_name in {"initial_results", "endpoint"}:
        compile_value = payload.get("compile_provenance_commit_sha256")
        oof_value = authentication.get("oof_predictions_sha256")
    elif source_name in {"qa", "residual_spatial", "diagnostic_figures"}:
        compile_value = authentication.get("compile_provenance_commit_sha256")
        oof_value = authentication.get("oof_predictions_sha256")
    elif source_name == "feature_ablation":
        compile_value = authentication.get("canonical_model_compile_commit_sha256")
        oof_value = authentication.get("canonical_all_feature_oof_sha256")
    elif source_name == "stqa2_sensitivity":
        compile_value = authentication.get("model_compile_provenance_commit_sha256")
        oof_value = authentication.get("model_oof_predictions_sha256")
    else:  # pragma: no cover - exact source-set validation makes this unreachable.
        raise ModelLockStagingError(f"Unknown reconciliation source: {source_name}.")
    return (
        _sha256(compile_value, name=f"{source_name} compile commitment"),
        _sha256(oof_value, name=f"{source_name} OOF hash"),
    )


def _verify_reconciliation_chain(payload: dict[str, Any]) -> tuple[str, ...]:
    """Re-authenticate the seven upstream provenance files committed by reconciliation."""

    if (
        payload.get("analysis_scope") != _RECONCILIATION_SCOPE
        or payload.get("ready_for_development_robustness_interpretation") is not True
    ):
        raise ModelLockStagingError("Robustness reconciliation scope is not ready.")
    authentication = _mapping(
        payload.get("input_authentication"),
        name="reconciliation input authentication",
    )
    shared_compile = _sha256(
        authentication.get("shared_model_compile_commit_sha256"),
        name="shared model compile commitment",
    )
    shared_oof = _sha256(
        authentication.get("shared_oof_predictions_sha256"),
        name="shared OOF hash",
    )
    sources = _mapping(authentication.get("sources"), name="reconciliation sources")
    if set(sources) != set(_RECONCILIATION_SOURCE_ALGORITHMS):
        raise ModelLockStagingError("Reconciliation source set is not exact.")

    authenticated_names: list[str] = []
    for source_name, algorithm_version in _RECONCILIATION_SOURCE_ALGORITHMS.items():
        record = _mapping(sources[source_name], name=f"{source_name} source record")
        path_value = record.get("provenance_path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ModelLockStagingError(f"{source_name} provenance path is invalid.")
        source_path = Path(path_value)
        if not source_path.is_absolute():
            raise ModelLockStagingError(
                f"{source_name} reconciliation provenance path must be absolute."
            )
        source_path = source_path.resolve()
        expected_file_sha = _sha256(
            record.get("provenance_file_sha256"),
            name=f"{source_name} provenance file hash",
        )
        expected_commit = _sha256(
            record.get("provenance_commit_sha256"),
            name=f"{source_name} provenance commit",
        )
        output_count = record.get("authenticated_output_count")
        if not isinstance(output_count, int) or isinstance(output_count, bool) or output_count < 1:
            raise ModelLockStagingError(
                f"{source_name} authenticated output count is invalid."
            )
        if not source_path.is_file() or sha256_file(source_path) != expected_file_sha:
            raise ModelLockStagingError(
                f"{source_name} provenance no longer matches reconciliation."
            )
        source_payload = _read_json(source_path)
        observed_commit = _verify_commit(source_payload, name=source_path.name)
        if (
            observed_commit != expected_commit
            or source_payload.get("algorithm_version") != algorithm_version
            or source_payload.get("state") != "complete"
            or source_payload.get("final_test_year") != 2025
            or source_payload.get("final_test_locked") is not True
            or source_payload.get("contains_final_test_year") is not False
            or _recursive_true(
                source_payload,
                {"final_test_unlocked", "unlock_final_test", "final_test_values_read"},
            )
        ):
            raise ModelLockStagingError(
                f"{source_name} is not an authenticated locked development source."
            )
        compile_commit, oof_hash = _source_lineage(source_name, source_payload)
        if compile_commit != shared_compile or oof_hash != shared_oof:
            raise ModelLockStagingError(
                f"{source_name} does not share the reconciled model lineage."
            )
        authenticated_names.append(source_name)
    return tuple(authenticated_names)


def _git_state(project_root: Path) -> dict[str, Any]:
    head_result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    head = head_result.stdout.strip().lower()
    valid_head = (
        head_result.returncode == 0
        and len(head) == 40
        and all(character in "0123456789abcdef" for character in head)
    )
    if not valid_head:
        return {
            "head_present": False,
            "training_code_git_commit": None,
            "working_tree_clean": False,
            "status_entry_count": None,
        }
    status_result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status_result.returncode != 0:
        raise ModelLockStagingError("Cannot inspect the Git working tree.")
    entries = [line for line in status_result.stdout.splitlines() if line.strip()]
    return {
        "head_present": True,
        "training_code_git_commit": head,
        "working_tree_clean": not entries,
        "status_entry_count": len(entries),
    }


def _robustness_record(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {
            "path": path.as_posix(),
            "exists": False,
            "authenticated": False,
        }, "missing"
    try:
        payload = _read_json(path)
        commit = _verify_commit(payload, name=path.name)
        expected_algorithm = _ROBUSTNESS_ALGORITHMS.get(path.name)
        if expected_algorithm is None:
            raise ModelLockStagingError(
                f"Unsupported robustness provenance filename: {path.name}."
            )
        recursively_authenticated_sources: tuple[str, ...] = ()
        if path.name == "robustness_reconciliation_provenance.json":
            recursively_authenticated_sources = _verify_reconciliation_chain(payload)
        valid = (
            payload.get("state") == "complete"
            and payload.get("algorithm_version") == expected_algorithm
            and payload.get("final_test_year") == 2025
            and payload.get("final_test_locked") is True
            and payload.get("contains_final_test_year") is False
            and not _recursive_true(
                payload,
                {"final_test_unlocked", "unlock_final_test", "final_test_values_read"},
            )
        )
    except ModelLockStagingError as error:
        return {
            "path": path.as_posix(),
            "exists": True,
            "authenticated": False,
            "error_type": type(error).__name__,
        }, "invalid"
    return {
        "path": path.as_posix(),
        "exists": True,
        "authenticated": valid,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "commit_sha256": commit,
        "algorithm_version": payload.get("algorithm_version"),
        "state": payload.get("state"),
        "contains_final_test_year": payload.get("contains_final_test_year"),
        "recursively_authenticated_sources": list(recursively_authenticated_sources),
    }, None if valid else "not_complete_or_locked"


def stage_model_lock(
    build_provenance_path: str | Path,
    *,
    config_path: str | Path = "configs/final_model.toml",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write only ``MODEL_LOCK_STAGING.json`` and enumerate every blocker."""

    config = load_final_model_config(config_path)
    output = (
        config.model_lock_staging_path
        if output_path is None
        else Path(output_path).resolve()
    )
    if output.name.casefold() == FORMAL_MODEL_LOCK_FILENAME.casefold():
        raise PermissionError("This command is forbidden from generating MODEL_LOCK.json.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # A failed restaging attempt must never leave an older marker looking current.
    output.unlink(missing_ok=True)
    output.with_suffix(output.suffix + ".partial").unlink(missing_ok=True)

    try:
        build = authenticate_final_build_provenance(
            build_provenance_path,
            load_models=True,
        )
    except FinalModelError as error:
        raise ModelLockStagingError(str(error)) from error
    config_record = build.get("analysis_config")
    if (
        not isinstance(config_record, dict)
        or config_record.get("semantic_sha256") != config.semantic_sha256
        or config_record.get("file_sha256") != sha256_file(config.path)
        or build.get("hotspot_contract") != config.hotspot_contract
        or build.get("planned_figures") != list(config.planned_figures)
    ):
        raise ModelLockStagingError("Final build disagrees with the current frozen lock config.")

    blockers: list[str] = []
    git = _git_state(_project_root())
    if not git["head_present"]:
        blockers.append("git_head_missing")
    elif not git["working_tree_clean"]:
        blockers.append("git_working_tree_not_clean")

    robustness: list[dict[str, Any]] = []
    for path in config.required_robustness_provenance:
        record, reason = _robustness_record(path)
        robustness.append(record)
        if reason is not None:
            blockers.append(f"robustness:{path.name}:{reason}")

    input_locks = build.get("input_locks")
    if not isinstance(input_locks, dict):
        raise ModelLockStagingError("Final build lacks authenticated input locks.")
    required_lock_names = {
        "model_dataset_provenance",
        "model_table",
        "feature_registry",
        "feature_registry_semantic_sha256",
        "split_promotion",
        "row_groups",
        "fold_definitions",
        "spatial_buffer_geoids",
        "model_selection_freeze",
        "model_selection_config",
        "context_commits",
    }
    missing_locks = sorted(required_lock_names - set(input_locks))
    if missing_locks:
        blockers.append("input_locks_incomplete:" + ",".join(missing_locks))

    models = build["models"]
    payload: dict[str, Any] = {
        "schema_version": MODEL_LOCK_STAGING_SCHEMA_VERSION,
        "algorithm_version": MODEL_LOCK_STAGING_ALGORITHM_VERSION,
        "state": "blocked" if blockers else "eligible_for_later_formal_promotion",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "formal_model_lock_written": False,
        "formal_model_lock_generation_allowed_by_this_command": False,
        "ready_for_formal_model_lock": not blockers,
        "blockers": blockers,
        "git": git,
        "development_build": {
            "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
            "schema_version": FINAL_MODEL_SCHEMA_VERSION,
            "path": Path(build_provenance_path).resolve().as_posix(),
            "sha256": sha256_file(Path(build_provenance_path).resolve()),
            "commit_sha256": build["commit_sha256"],
            "run_id": build["run_id"],
            "final_test_values_read": build["final_test_values_read"],
        },
        "configuration": dict(config_record),
        "input_locks": input_locks,
        "model_dataset_commit_sha256": build["model_dataset_commit_sha256"],
        "split_promotion_commit_sha256": build["split_promotion_commit_sha256"],
        "model_selection_commit_sha256": build["model_selection_commit_sha256"],
        "selection_config_sha256": build["selection_config_sha256"],
        "models": {
            model_id: {
                "artifact_path": str(record["path"]),
                "fitted_pipeline_sha256": record["sha256"],
                "fitted_pipeline_bytes": record["bytes"],
                "selected_candidate_id": record["selected_candidate_id"],
                "selected_parameters": record["selected_parameters"],
                "random_state": record["random_state"],
                "feature_names": record["feature_names"],
                "feature_count": record["feature_count"],
                "training_row_count": record["training_row_count"],
                "training_date_count": record["training_date_count"],
                "training_spatial_block_count": record[
                    "training_spatial_block_count"
                ],
                "training_keys_sha256": record["training_keys_sha256"],
            }
            for model_id, record in models.items()
        },
        "primary_metric": "equal_date_weighted_mae_c",
        "hotspot_rule": dict(config.hotspot_contract),
        "planned_figures": list(config.planned_figures),
        "robustness_provenance": robustness,
        "final_test_year": 2025,
        "final_test_locked": True,
        "final_test_unlocked": False,
        "final_test_used": False,
        "contains_final_test_year": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, output)
    return payload
