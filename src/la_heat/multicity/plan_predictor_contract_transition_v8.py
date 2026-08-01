"""Consume the water-distance V2 terminal and authorize one contract freeze.

This tracked-only transition reads exact Git blobs and JSON manifests.  It
copies the V2 source and algorithm locks into canonical planning, closes the
consumed decision permission, and opens only the target-blind predictor-source
and calibration-contract freeze.  It does not open source archives, geometry,
eligible supports, predictors, models, targets, or results.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.plan_freeze_transition_v7 import (
    authenticate_historical_v7_payload,
)
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    CODE_PATHS as CONTRACT_DECISION_RUNTIME_PATHS,
)
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    expected_plan_authorization_scope,
)
from la_heat.multicity.portable_water_distance_freeze_v2 import (
    CODE_PATHS as V2_RUNTIME_PATHS,
)

SCHEMA_VERSION: Final = 8
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v8"
PLANNING_STAGE: Final = (
    "portable_water_distance_source_and_algorithm_frozen_"
    "predictor_contract_freeze_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "freeze_exact_portable_predictor_source_and_calibration_contract"
)
EXPERIMENT_ID: Final = "la_to_three_city_zero_shot_v1"
EXPERIMENT_SEMANTIC_SHA256: Final = (
    "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
)

PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
V7_PUBLICATION_COMMIT: Final = "252c01d015110336c65bb602d4c5b608708fb092"
V7_FILE_SHA256: Final = (
    "88c153b7c1da9f2f159ac550fd3156a4ffe3fd1f56c269c057288d938a2047f3"
)
V7_INTERNAL_COMMIT_SHA256: Final = (
    "4f6ed97b64d3a1601da6af83779ec96bef87c77de72d5294475ac029f666110f"
)
V7_BYTES: Final = 20_809

V2_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
V2_PUBLICATION_COMMIT: Final = "91a31fd9e1793bbfa9c9f751459fc73d0e0bbb4c"
V2_IMPLEMENTATION_COMMIT: Final = "eefb531e99d95e8dd7069e821ff941abd68de622"
V2_FILE_SHA256: Final = (
    "a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b"
)
V2_INTERNAL_COMMIT_SHA256: Final = (
    "2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51"
)
V2_BYTES: Final = 18_541
V2_STATE: Final = (
    "decision_complete_source_and_algorithm_frozen_predictor_closed"
)
V2_OUTCOME: Final = (
    "freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm"
)
V2_SOURCE_LOCK_SEMANTIC_SHA256: Final = (
    "ccca84d532691c00eac73512ceeb3a72a211e7e26bbe967ab684b3305e8f9bcb"
)
V2_ALGORITHM_LOCK_SEMANTIC_SHA256: Final = (
    "36ac6cd9b0864bedcd169aa1ceedded042d0aefd9f2d1ecebfffd65f1f059b63"
)
V2_LOCKS_SEMANTIC_SHA256: Final = (
    "aefbdf128de49948034a790ce6ea4246124d5f83b93be5168271a3b62301873c"
)
V2_NEXT_GATE_SEMANTIC_SHA256: Final = (
    "385236f7539c8a88a44ead8e3d270af0654d65d6755cd7126636d730fb7f7ffe"
)

V8_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_predictor_contract_transition_v8.py"
)
V8_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_predictor_contract_freeze.py"
)
TRANSITION_CODE_PATHS: Final = tuple(
    CONTRACT_DECISION_RUNTIME_PATHS
)

V7_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": True,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": False,
    "portable_predictor_source_and_calibration_contract_freeze": True,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
V7_LOCKS: Final = {
    "protocol_locked": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
    "portable_water_distance_source_locked": False,
    "portable_water_distance_algorithm_locked": False,
    "portable_water_distance_feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
}
LOCKS: Final = {
    **V7_LOCKS,
    "portable_water_distance_source_locked": True,
    "portable_water_distance_algorithm_locked": True,
}
BLOCKERS_BEFORE_PREDICTOR_BUILD: Final = (
    "freeze_exact_portable_predictor_source_and_calibration_contract",
    "promote_protocol_from_draft_with_separate_lock",
)
EXPECTED_TRANSITION_DIFF_PATHS: Final = (
    "authorized_now.portable_predictor_source_and_calibration_contract_freeze",
    "authorized_now.portable_predictor_source_freeze",
    "locks.portable_water_distance_algorithm_locked",
    "locks.portable_water_distance_source_locked",
)

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_configuration_files_read": True,
    "tracked_code_files_hashed": True,
    "tracked_json_manifests_read": True,
    "local_git_metadata_and_historical_blobs_read": True,
    "untracked_path_names_checked_by_git_status": True,
    "untracked_file_contents_opened": False,
    "ignored_path_names_requested_from_git": False,
    "archive_geometry_data_or_result_bytes_opened": False,
    "gshhg_archive_bytes_opened": False,
    "gshhg_archive_members_opened": False,
    "geometry_opened": False,
    "eligible_land_grid_opened": False,
    "distance_values_computed": False,
    "distance_feature_surface_computed": False,
    "tract_aggregation_performed": False,
    "predictor_values_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "external_lst_values_read": False,
    "external_target_files_opened": False,
    "final_evaluation_outputs_opened": False,
}

class MulticityPlanPredictorContractTransitionV8Error(ValueError):
    """Raised when the narrow v8 transition cannot be authenticated."""


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(  # type: ignore[arg-type]
            _strict_equal(actual[key], expected[key])  # type: ignore[index]
            for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(  # type: ignore[arg-type]
            _strict_equal(left, right)
            for left, right in zip(actual, expected, strict=True)  # type: ignore[arg-type]
        )
    return bool(actual == expected)


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"{label} must be an object."
        )
    return value


def _json_object_from_bytes(payload_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Authenticated JSON internal commit is invalid: {label}"
        )
    return payload


def _run_git(
    project_root: Path,
    *arguments: str,
    binary: bool = False,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode not in accepted_returncodes:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Git authentication failed for {' '.join(arguments)}: "
            f"{stderr.strip()}"
        )
    return completed.stdout


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def _require_exact_commit_delta(
    project_root: Path,
    *,
    parent: str,
    commit: str,
    expected_status: str,
    expected_path: str,
) -> None:
    raw = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        parent,
        commit,
        binary=True,
    )
    assert isinstance(raw, bytes)
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication delta is not valid NUL-delimited Git output."
        )
    fields = fields[:-1]
    try:
        decoded = tuple(field.decode("utf-8") for field in fields)
    except UnicodeDecodeError as exc:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication delta contains a non-UTF-8 path."
        ) from exc
    if decoded != (expected_status, expected_path):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication commit must modify only canonical "
            "PLAN_READINESS.json."
        )


def _git_regular_blob(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> tuple[bytes, str, str]:
    tree_output = _run_git(
        project_root,
        "ls-tree",
        commit,
        "--",
        relative_path,
    )
    assert isinstance(tree_output, str)
    parts = tree_output.strip().split(maxsplit=3)
    if (
        len(parts) != 4
        or parts[0] not in {"100644", "100755"}
        or parts[1] != "blob"
        or parts[3] != relative_path
    ):
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Required input is not one exact regular Git blob: {relative_path}"
        )
    payload = _run_git(
        project_root,
        "show",
        f"{commit}:{relative_path}",
        binary=True,
    )
    assert isinstance(payload, bytes)
    return payload, parts[2], parts[0]


def _historical_json(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> tuple[dict[str, Any], bytes]:
    payload_bytes, _, _ = _git_regular_blob(
        project_root,
        commit=commit,
        relative_path=relative_path,
    )
    return (
        _json_object_from_bytes(
            payload_bytes,
            label=f"{commit}:{relative_path}",
        ),
        payload_bytes,
    )


def _read_current_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Authenticated input changed while read: {path}"
        )
    return _json_object_from_bytes(before, label=str(path)), before


def _recursive_diff_paths(
    left: object,
    right: object,
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    if type(left) is not type(right):
        return (prefix,)
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):  # type: ignore[arg-type]
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(
                    _recursive_diff_paths(left[key], right[key], prefix=path)
                )
        return tuple(paths)
    if isinstance(left, list):
        paths = []
        for index in range(max(len(left), len(right))):
            path = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(path)
            else:
                paths.extend(
                    _recursive_diff_paths(left[index], right[index], prefix=path)
                )
        return tuple(paths)
    return () if left == right else (prefix,)


def _validate_v7_predecessor(
    payload: Mapping[str, Any],
    payload_bytes: bytes,
) -> None:
    if len(payload_bytes) != V7_BYTES or _sha256_bytes(payload_bytes) != V7_FILE_SHA256:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The canonical v7 predecessor bytes changed."
        )
    expected_identity = {
        "schema_version": 7,
        "algorithm_version": "multicity-planning-readiness-v7",
        "state": "planning_ready",
        "planning_stage": (
            "gshhg_l3_hierarchy_audit_complete_freeze_decision_authorized"
        ),
        "experiment_id": EXPERIMENT_ID,
        "config_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "next_safe_stage": (
            "separate_portable_water_distance_source_and_algorithm_freeze_decision"
        ),
        "commit_sha256": V7_INTERNAL_COMMIT_SHA256,
    }
    for key, expected in expected_identity.items():
        if not _strict_equal(payload.get(key), expected):
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"The canonical v7 predecessor field changed: {key}"
            )
    if not _strict_equal(payload.get("authorized_now"), V7_AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The canonical v7 authorization boundary changed."
        )
    if not _strict_equal(payload.get("locks"), V7_LOCKS):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The canonical v7 lock boundary changed."
        )


def _validate_v2_terminal(
    payload: Mapping[str, Any],
    payload_bytes: bytes,
) -> None:
    if (
        len(payload_bytes) != V2_BYTES
        or _sha256_bytes(payload_bytes) != V2_FILE_SHA256
    ):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The canonical V2 decision terminal bytes changed."
        )
    expected_identity = {
        "schema_version": 2,
        "algorithm_version": "portable-water-distance-freeze-decision-v2",
        "state": V2_STATE,
        "outcome": V2_OUTCOME,
        "experiment_id": EXPERIMENT_ID,
        "experiment_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
    }
    for key, expected in expected_identity.items():
        if not _strict_equal(payload.get(key), expected):
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"The canonical V2 decision field changed: {key}"
            )
    planning = _require_mapping(
        payload.get("planning_authorization"),
        label="V2 planning authorization",
    )
    expected_planning = {
        "path": PLAN_PATH,
        "bytes": V7_BYTES,
        "file_sha256": V7_FILE_SHA256,
        "commit_sha256": V7_INTERNAL_COMMIT_SHA256,
        "publication_git_commit": V7_PUBLICATION_COMMIT,
        "state": "planning_ready",
        "planning_stage": (
            "gshhg_l3_hierarchy_audit_complete_freeze_decision_authorized"
        ),
        "authorized_now": deepcopy(V7_AUTHORIZED_NOW),
    }
    if not _strict_equal(planning, expected_planning):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 decision is not bound to the exact canonical v7 plan."
        )
    expected_locks = {
        "source_lock_created": True,
        "algorithm_lock_created": True,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }
    if not _strict_equal(payload.get("locks"), expected_locks):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 decision lock boundary changed."
        )
    next_gate = _require_mapping(payload.get("next_gate"), label="V2 next gate")
    expected_gate = {
        "stage_id": "publish_tracked_only_plan_v8_after_water_distance_freeze",
        "v8_transition_required": True,
        "v8_must_authenticate_and_consume_this_exact_terminal": True,
        "v8_must_set_canonical_plan_source_and_algorithm_locks_true": True,
        "v8_must_close_portable_predictor_source_freeze_permission": True,
        "v8_may_authorize_only_predictor_source_and_calibration_contract_freeze": (
            True
        ),
        "subsequent_safe_stage_after_v8": NEXT_SAFE_STAGE,
        "source_and_point_distance_algorithm_are_frozen_in_this_decision_terminal": (
            True
        ),
        "canonical_plan_source_and_algorithm_locks_remain_false_until_v8": True,
        "tract_aggregation_and_feature_names_remain_unfrozen": True,
        "predictor_construction_requires_new_tracked_only_transition": True,
        "model_target_and_protocol_authorization_remain_closed": True,
    }
    if not _strict_equal(next_gate, expected_gate):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 decision does not require the exact narrow v8 gate."
        )
    semantic_bindings = {
        "source_lock": V2_SOURCE_LOCK_SEMANTIC_SHA256,
        "algorithm_lock": V2_ALGORITHM_LOCK_SEMANTIC_SHA256,
        "locks": V2_LOCKS_SEMANTIC_SHA256,
        "next_gate": V2_NEXT_GATE_SEMANTIC_SHA256,
    }
    for key, expected_sha in semantic_bindings.items():
        if _canonical_sha256(payload.get(key)) != expected_sha:
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"The V2 {key} semantic identity changed."
            )


def _require_v2_terminal_history(
    project_root: Path,
    *,
    terminal_raw: bytes,
    current_head: str,
) -> None:
    additions = _run_git(
        project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        V2_TERMINAL_PATH,
    )
    assert isinstance(additions, str)
    if [line for line in additions.splitlines() if line] != [
        V2_PUBLICATION_COMMIT
    ]:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 terminal must have one unique append-only publication."
        )
    ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        V2_PUBLICATION_COMMIT,
    )
    assert isinstance(ancestry, str)
    if ancestry.split() != [V2_PUBLICATION_COMMIT, V7_PUBLICATION_COMMIT]:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 terminal is not the direct child of canonical v7."
        )
    published, _, _ = _git_regular_blob(
        project_root,
        commit=V2_PUBLICATION_COMMIT,
        relative_path=V2_TERMINAL_PATH,
    )
    if published != terminal_raw:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 publication bytes changed."
        )
    if not _is_ancestor(project_root, V2_PUBLICATION_COMMIT, current_head):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 terminal publication is not an ancestor of HEAD."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V2_PUBLICATION_COMMIT}..{current_head}",
        "--",
        V2_TERMINAL_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The append-only V2 terminal changed after publication."
        )


def _code_records_at_commit(
    project_root: Path,
    *,
    commit: str,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for relative_path in TRANSITION_CODE_PATHS:
        payload, blob_oid, mode = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=relative_path,
        )
        records[relative_path] = {
            "sha256": _sha256_bytes(payload),
            "bytes": len(payload),
            "git_blob_oid": blob_oid,
            "git_mode": mode,
        }
    return records


def _validate_frozen_v2_runtime(
    code_files: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> None:
    runtime = _require_mapping(terminal.get("code_runtime"), label="V2 runtime")
    runtime_files = _require_mapping(
        runtime.get("files"),
        label="V2 runtime files",
    )
    if set(runtime_files) != set(V2_RUNTIME_PATHS):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The V2 terminal runtime path set changed."
        )
    for relative_path, expected_sha in runtime_files.items():
        record = _require_mapping(
            code_files.get(relative_path),
            label=f"v8 code record {relative_path}",
        )
        if record.get("sha256") != expected_sha:
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"A frozen V2 runtime changed before v8: {relative_path}"
            )


def _require_frozen_v2_runtime_history(
    project_root: Path,
    *,
    terminal: Mapping[str, Any],
    current_head: str,
) -> None:
    """Reject a committed V2 runtime modification even if later restored."""

    runtime = _require_mapping(terminal.get("code_runtime"), label="V2 runtime")
    runtime_files = _require_mapping(
        runtime.get("files"),
        label="V2 runtime files",
    )
    for relative_path in V2_RUNTIME_PATHS:
        history = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{V2_IMPLEMENTATION_COMMIT}..{current_head}",
            "--",
            relative_path,
        )
        assert isinstance(history, str)
        if history.strip():
            raise MulticityPlanPredictorContractTransitionV8Error(
                "A frozen V2 runtime was modified after its implementation "
                f"commit: {relative_path}"
            )
        implementation_raw, _, _ = _git_regular_blob(
            project_root,
            commit=V2_IMPLEMENTATION_COMMIT,
            relative_path=relative_path,
        )
        if _sha256_bytes(implementation_raw) != runtime_files.get(relative_path):
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"The V2 runtime identity changed: {relative_path}"
            )


def _validate_config_files_at_commit(
    project_root: Path,
    *,
    commit: str,
    config_files: object,
) -> None:
    records = _require_mapping(config_files, label="v7 config files")
    for relative_path, raw_record in records.items():
        if not isinstance(relative_path, str):
            raise MulticityPlanPredictorContractTransitionV8Error(
                "A v7 config path is not a string."
            )
        record = _require_mapping(raw_record, label=f"config {relative_path}")
        payload, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=relative_path,
        )
        if not _strict_equal(
            record,
            {"sha256": _sha256_bytes(payload), "bytes": len(payload)},
        ):
            raise MulticityPlanPredictorContractTransitionV8Error(
                f"Experiment config changed before v8: {relative_path}"
            )


def _validate_scope_bindings(
    scope: Mapping[str, Any],
    code_files: Mapping[str, Any],
) -> None:
    expected_scope = expected_plan_authorization_scope()
    if not _strict_equal(scope, expected_scope):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract authorization scope changed."
        )
    runtime_paths = scope.get("decision_runtime_paths")
    if not _strict_equal(runtime_paths, list(TRANSITION_CODE_PATHS)):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract runtime path set changed."
        )
    if any(
        any(character in path for character in "*?[]")
        for path in TRANSITION_CODE_PATHS
    ):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract runtime scope may not use wildcards."
        )
    for relative_path in TRANSITION_CODE_PATHS:
        _require_mapping(
            code_files.get(relative_path),
            label=f"transition code record {relative_path}",
        )
    config_path = scope.get("decision_config_path")
    config_sha = scope.get("decision_config_sha256")
    if not isinstance(config_path, str) or not isinstance(config_sha, str):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract config identity is incomplete."
        )
    config_record = _require_mapping(
        code_files.get(config_path),
        label="predictor-contract config record",
    )
    if config_record.get("sha256") != config_sha:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract config does not match its authorized hash."
        )
    output_path = scope.get("decision_output_path")
    absent_paths = scope.get("required_absent_paths")
    if (
        not isinstance(output_path, str)
        or not isinstance(absent_paths, list)
        or any(not isinstance(path, str) for path in absent_paths)
        or any(
            any(character in path for character in "*?[]")
            for path in (output_path, *absent_paths)
        )
    ):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The predictor-contract tracked read/output scope is not exact."
        )


def _validate_transition_boundary(
    predecessor: Mapping[str, Any],
    successor: Mapping[str, Any],
) -> None:
    if not _strict_equal(predecessor.get("authorized_now"), V7_AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v7 authorization boundary is not canonical."
        )
    if not _strict_equal(predecessor.get("locks"), V7_LOCKS):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v7 lock boundary is not canonical."
        )
    if not _strict_equal(successor.get("authorized_now"), AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition opened a wrong permission."
        )
    if not _strict_equal(successor.get("locks"), LOCKS):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition created a wrong lock."
        )
    changes = _recursive_diff_paths(
        {
            "authorized_now": predecessor["authorized_now"],
            "locks": predecessor["locks"],
        },
        {
            "authorized_now": successor["authorized_now"],
            "locks": successor["locks"],
        },
    )
    if changes != EXPECTED_TRANSITION_DIFF_PATHS:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition changed fields outside the exact permission boundary."
        )


def _build_v8_payload(
    predecessor: Mapping[str, Any],
    *,
    predecessor_bytes: int,
    terminal: Mapping[str, Any],
    terminal_bytes: int,
    precondition_commit: str,
    code_files: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact v8 payload from authenticated tracked evidence."""

    if re.fullmatch(r"[0-9a-f]{40}", precondition_commit) is None:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 precondition Git commit is invalid."
        )
    _validate_frozen_v2_runtime(code_files, terminal)
    authorization_scope = expected_plan_authorization_scope()
    _validate_scope_bindings(authorization_scope, code_files)
    old_scope = deepcopy(
        _require_mapping(
            predecessor.get("freeze_decision_authorization_scope"),
            label="v7 freeze-decision authorization scope",
        )
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "planning_ready",
        "planning_stage": PLANNING_STAGE,
        "experiment_id": EXPERIMENT_ID,
        "config_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "config_files": deepcopy(predecessor["config_files"]),
        "code_files": deepcopy(dict(code_files)),
        "transition": {
            "id": (
                "consume_water_distance_v2_and_authorize_"
                "predictor_source_calibration_contract_freeze"
            ),
            "mode": "tracked_manifests_and_local_git_only",
            "predecessor_plan_readiness": {
                "path": PLAN_PATH,
                "source_git_commit": V7_PUBLICATION_COMMIT,
                "file_sha256": V7_FILE_SHA256,
                "bytes": predecessor_bytes,
                "commit_sha256": predecessor["commit_sha256"],
                "state": predecessor["state"],
                "planning_stage": predecessor["planning_stage"],
                "next_safe_stage": predecessor["next_safe_stage"],
            },
            "water_distance_freeze_terminal": {
                "path": V2_TERMINAL_PATH,
                "source_git_commit": V2_PUBLICATION_COMMIT,
                "file_sha256": V2_FILE_SHA256,
                "bytes": terminal_bytes,
                "commit_sha256": terminal["commit_sha256"],
                "state": terminal["state"],
                "outcome": terminal["outcome"],
                "next_safe_stage": terminal["next_gate"]["stage_id"],
            },
            "writer_precondition": {
                "branch": "main",
                "git_head": precondition_commit,
                "origin_main": precondition_commit,
                "worktree_clean": True,
                "head_equals_local_origin_main": True,
                "all_transition_inputs_regular_git_tracked_blobs": True,
            },
            "authorization_effective_only_when": {
                "this_exact_plan_readiness_is_git_tracked": True,
                "branch_is_main": True,
                "worktree_is_clean": True,
                "head_equals_local_origin_main": True,
                "v8_check_only_passes_before_contract_freeze": True,
            },
        },
        "transition_access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
        "cities": deepcopy(predecessor["cities"]),
        "locks": deepcopy(LOCKS),
        "authorized_now": deepcopy(AUTHORIZED_NOW),
        "consumed_geometry_audit_authorization": deepcopy(
            predecessor["consumed_geometry_audit_authorization"]
        ),
        "consumed_portable_water_distance_freeze_authorization": {
            "status": "consumed_and_closed",
            "predecessor_schema_version": 7,
            "predecessor_scope": old_scope,
            "completion_manifest_path": V2_TERMINAL_PATH,
            "completion_manifest_file_sha256": V2_FILE_SHA256,
            "completion_manifest_commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "completion_manifest_publication_git_commit": V2_PUBLICATION_COMMIT,
            "source_lock_now_canonical": True,
            "algorithm_lock_now_canonical": True,
            "decision_permission_now": False,
            "archive_member_geometry_predictor_model_target_or_result_reread": (
                False
            ),
        },
        "portable_water_distance_contract_lock": {
            "terminal_path": V2_TERMINAL_PATH,
            "terminal_publication_git_commit": V2_PUBLICATION_COMMIT,
            "terminal_file_sha256": V2_FILE_SHA256,
            "terminal_commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "source_lock_semantic_sha256": V2_SOURCE_LOCK_SEMANTIC_SHA256,
            "algorithm_lock_semantic_sha256": (
                V2_ALGORITHM_LOCK_SEMANTIC_SHA256
            ),
            "decision_locks_semantic_sha256": V2_LOCKS_SEMANTIC_SHA256,
            "next_gate_semantic_sha256": V2_NEXT_GATE_SEMANTIC_SHA256,
            "source_locked": True,
            "point_distance_algorithm_locked": True,
            "tract_aggregation_frozen": False,
            "feature_names_frozen": False,
            "predictor_construction_authorized": False,
        },
        "predictor_contract_freeze_authorization_scope": deepcopy(
            authorization_scope
        ),
        "workspace": deepcopy(predecessor["workspace"]),
        "gshhg_geometry_pilot": deepcopy(predecessor["gshhg_geometry_pilot"]),
        "portable_water_distance_freeze_decision": deepcopy(
            predecessor["portable_water_distance_freeze_decision"]
        ),
        "gshhg_l3_hierarchy_audit_preregistration": deepcopy(
            predecessor["gshhg_l3_hierarchy_audit_preregistration"]
        ),
        "gshhg_l3_hierarchy_audit": deepcopy(
            predecessor["gshhg_l3_hierarchy_audit"]
        ),
        "portable_water_distance_freeze_decision_v2": {
            "path": V2_TERMINAL_PATH,
            "file_sha256": V2_FILE_SHA256,
            "bytes": terminal_bytes,
            "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "publication_git_commit": V2_PUBLICATION_COMMIT,
            "state": V2_STATE,
            "outcome": V2_OUTCOME,
            "source_lock_created": True,
            "algorithm_lock_created": True,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "authentication_mode": (
                "exact_append_only_terminal_and_historical_git_lineage"
            ),
            "archive_member_geometry_predictor_model_target_or_result_reopened": (
                False
            ),
        },
        "blockers_before_predictor_build": list(
            BLOCKERS_BEFORE_PREDICTOR_BUILD
        ),
        "next_safe_stage": NEXT_SAFE_STAGE,
    }
    _validate_transition_boundary(predecessor, payload)
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_exact_v8_payload(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    recorded = observed.get("commit_sha256")
    body = {key: value for key, value in observed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 planning internal commit is invalid."
        )
    if not _strict_equal(observed, expected):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 planning record differs from the complete reconstructed payload."
        )


def _git_preflight(
    project_root: Path,
    *,
    required_paths: tuple[str, ...],
    expected_head: str | None = None,
) -> str:
    branch = _run_git(project_root, "branch", "--show-current")
    head = _run_git(project_root, "rev-parse", "HEAD")
    origin_main = _run_git(project_root, "rev-parse", "origin/main")
    status = _run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    assert isinstance(branch, str)
    assert isinstance(head, str)
    assert isinstance(origin_main, str)
    assert isinstance(status, str)
    head = head.strip()
    if branch.strip() != "main":
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition requires branch main."
        )
    if head != origin_main.strip():
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition requires HEAD to equal local origin/main."
        )
    if expected_head is not None and head != expected_head:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "HEAD changed between v8 transition gates."
        )
    if status:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition requires a completely clean working tree."
        )
    for ancestor in (V7_PUBLICATION_COMMIT, V2_PUBLICATION_COMMIT):
        if not _is_ancestor(project_root, ancestor, head):
            raise MulticityPlanPredictorContractTransitionV8Error(
                "Canonical v7 or V2 evidence is not an ancestor of HEAD."
            )
    for relative_path in required_paths:
        _, blob_oid, _ = _git_regular_blob(
            project_root,
            commit=head,
            relative_path=relative_path,
        )
        worktree_oid = _run_git(
            project_root,
            "hash-object",
            f"--path={relative_path}",
            "--",
            relative_path,
        )
        assert isinstance(worktree_oid, str)
        if worktree_oid.strip() != blob_oid:
            raise MulticityPlanPredictorContractTransitionV8Error(
                "A required input differs from HEAD, including through an index "
                f"visibility flag: {relative_path}"
            )
    return head


def _require_v8_plan_history(
    project_root: Path,
    *,
    publication_commit: str,
    precondition_commit: str,
    published_raw: bytes,
    current_head: str,
) -> None:
    ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        publication_commit,
    )
    assert isinstance(ancestry, str)
    if ancestry.split() != [publication_commit, precondition_commit]:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication is not the direct child of its precondition."
        )
    _require_exact_commit_delta(
        project_root,
        parent=precondition_commit,
        commit=publication_commit,
        expected_status="M",
        expected_path=PLAN_PATH,
    )
    history = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V7_PUBLICATION_COMMIT}..{publication_commit}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(history, str)
    if [line for line in history.splitlines() if line] != [publication_commit]:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "PLAN_READINESS changed outside the one v7-to-v8 publication."
        )
    if not _is_ancestor(project_root, publication_commit, current_head):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication is not an ancestor of current HEAD."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{publication_commit}..{current_head}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise MulticityPlanPredictorContractTransitionV8Error(
            "PLAN_READINESS changed after the unique v8 publication."
        )
    current_raw, _, _ = _git_regular_blob(
        project_root,
        commit=current_head,
        relative_path=PLAN_PATH,
    )
    if current_raw != published_raw:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "Current PLAN_READINESS differs from the v8 publication."
        )


def _require_publication_code_files(
    project_root: Path,
    *,
    publication_commit: str,
    expected_code_files: Mapping[str, Any],
) -> None:
    observed = _code_records_at_commit(
        project_root,
        commit=publication_commit,
    )
    if not _strict_equal(observed, expected_code_files):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 publication changed a frozen transition code blob."
        )


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _locate_v8_publication_commit(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    current_head: str,
) -> str:
    transition = _require_mapping(payload.get("transition"), label="v8 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v8 writer precondition",
    )
    precondition = writer.get("git_head")
    if not isinstance(precondition, str):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 writer precondition commit is missing."
        )
    log = _run_git(
        project_root,
        "log",
        "--format=%H",
        current_head,
        "--",
        PLAN_PATH,
    )
    assert isinstance(log, str)
    expected_bytes = _expected_json_bytes(payload)
    candidates: list[str] = []
    for commit in (line for line in log.splitlines() if line):
        ancestry = _run_git(
            project_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            commit,
        )
        assert isinstance(ancestry, str)
        if ancestry.split() != [commit, precondition]:
            continue
        published, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=PLAN_PATH,
        )
        if published == expected_bytes:
            candidates.append(commit)
    if len(candidates) != 1:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The exact v8 transition must have one unique direct Git publication."
        )
    return candidates[0]


def authenticate_historical_v8_payload(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str | None = None,
    current_head: str | None = None,
) -> dict[str, Any]:
    """Reconstruct v8 from exact historical v7 and V2 publication blobs."""

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v8 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v8 writer precondition",
    )
    precondition = writer.get("git_head")
    if (
        not isinstance(precondition, str)
        or re.fullmatch(r"[0-9a-f]{40}", precondition) is None
    ):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 precondition Git commit is invalid."
        )
    if not _is_ancestor(root, V2_PUBLICATION_COMMIT, precondition):
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 precondition does not descend from the canonical V2 terminal."
        )
    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V7_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v7_predecessor(predecessor, predecessor_raw)
    precondition_plan, _, _ = _git_regular_blob(
        root,
        commit=precondition,
        relative_path=PLAN_PATH,
    )
    if precondition_plan != predecessor_raw:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "PLAN_READINESS at the v8 precondition is not the exact v7 predecessor."
        )
    try:
        authenticate_historical_v7_payload(
            root,
            predecessor,
            publication_commit=V7_PUBLICATION_COMMIT,
            current_head=precondition,
        )
    except Exception as exc:
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The historical v7 predecessor failed canonical authentication."
        ) from exc
    terminal, terminal_raw = _historical_json(
        root,
        commit=V2_PUBLICATION_COMMIT,
        relative_path=V2_TERMINAL_PATH,
    )
    _validate_v2_terminal(terminal, terminal_raw)
    _require_v2_terminal_history(
        root,
        terminal_raw=terminal_raw,
        current_head=precondition,
    )
    _require_frozen_v2_runtime_history(
        root,
        terminal=terminal,
        current_head=precondition,
    )
    _validate_config_files_at_commit(
        root,
        commit=precondition,
        config_files=predecessor["config_files"],
    )
    code_files = _code_records_at_commit(root, commit=precondition)
    expected = _build_v8_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        terminal=terminal,
        terminal_bytes=len(terminal_raw),
        precondition_commit=precondition,
        code_files=code_files,
    )
    _validate_exact_v8_payload(payload, expected)

    if publication_commit is not None:
        if re.fullmatch(r"[0-9a-f]{40}", publication_commit) is None:
            raise MulticityPlanPredictorContractTransitionV8Error(
                "The v8 publication Git commit is invalid."
            )
        published_raw, _, _ = _git_regular_blob(
            root,
            commit=publication_commit,
            relative_path=PLAN_PATH,
        )
        published = _json_object_from_bytes(
            published_raw,
            label=f"{publication_commit}:{PLAN_PATH}",
        )
        if not _strict_equal(published, payload):
            raise MulticityPlanPredictorContractTransitionV8Error(
                "The supplied v8 payload differs from its publication Git blob."
            )
        _require_publication_code_files(
            root,
            publication_commit=publication_commit,
            expected_code_files=code_files,
        )
        _require_v8_plan_history(
            root,
            publication_commit=publication_commit,
            precondition_commit=precondition,
            published_raw=published_raw,
            current_head=(publication_commit if current_head is None else current_head),
        )
        _require_v2_terminal_history(
            root,
            terminal_raw=terminal_raw,
            current_head=(publication_commit if current_head is None else current_head),
        )
    return deepcopy(dict(payload))


def _publish_or_authenticate(
    payload: Mapping[str, Any],
    *,
    destination: Path,
    predecessor_bytes: bytes,
    write: bool,
) -> None:
    expected_bytes = _expected_json_bytes(payload)
    current = destination.read_bytes()
    if write and current == predecessor_bytes:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.write_bytes(expected_bytes)
        temporary.replace(destination)
        return
    if current != expected_bytes:
        action = "replace" if write else "authenticate"
        raise MulticityPlanPredictorContractTransitionV8Error(
            f"Refusing to {action} a PLAN_READINESS that is neither the exact "
            "v7 predecessor nor the byte-identical reconstructed v8."
        )


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def authorize_multicity_predictor_contract_freeze(
    *,
    project_root: str | Path | None = None,
    output_path: str | Path = PLAN_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the narrow tracked-only v8 transition."""

    root = (
        _default_project_root()
        if project_root is None
        else Path(project_root).resolve()
    )
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    if destination != (root / PLAN_PATH).resolve():
        raise MulticityPlanPredictorContractTransitionV8Error(
            "The v8 transition may only replace canonical PLAN_READINESS.json."
        )

    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V7_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v7_predecessor(predecessor, predecessor_raw)
    terminal, terminal_raw = _historical_json(
        root,
        commit=V2_PUBLICATION_COMMIT,
        relative_path=V2_TERMINAL_PATH,
    )
    _validate_v2_terminal(terminal, terminal_raw)
    config_paths = tuple(
        _require_mapping(predecessor.get("config_files"), label="v7 config files")
    )
    required_paths = tuple(
        dict.fromkeys(
            (
                *config_paths,
                *TRANSITION_CODE_PATHS,
                PLAN_PATH,
                V2_TERMINAL_PATH,
            )
        )
    )
    precondition = _git_preflight(root, required_paths=required_paths)
    current, current_raw = _read_current_json(destination)

    if _sha256_bytes(current_raw) == V7_FILE_SHA256:
        if not write:
            raise MulticityPlanPredictorContractTransitionV8Error(
                "PLAN_READINESS is still v7; the v8 transition has not been written."
            )
        _validate_v7_predecessor(current, current_raw)
        if current_raw != predecessor_raw:
            raise MulticityPlanPredictorContractTransitionV8Error(
                "Current v7 bytes differ from the canonical historical blob."
            )
        authenticate_historical_v7_payload(
            root,
            predecessor,
            publication_commit=V7_PUBLICATION_COMMIT,
            current_head=precondition,
        )
        _require_v2_terminal_history(
            root,
            terminal_raw=terminal_raw,
            current_head=precondition,
        )
        _require_frozen_v2_runtime_history(
            root,
            terminal=terminal,
            current_head=precondition,
        )
        current_terminal, current_terminal_raw = _read_current_json(
            root / V2_TERMINAL_PATH
        )
        _validate_v2_terminal(current_terminal, current_terminal_raw)
        if current_terminal_raw != terminal_raw:
            raise MulticityPlanPredictorContractTransitionV8Error(
                "Current V2 terminal differs from its canonical publication."
            )
        _validate_config_files_at_commit(
            root,
            commit=precondition,
            config_files=predecessor["config_files"],
        )
        code_files = _code_records_at_commit(root, commit=precondition)
        payload = _build_v8_payload(
            predecessor,
            predecessor_bytes=len(predecessor_raw),
            terminal=terminal,
            terminal_bytes=len(terminal_raw),
            precondition_commit=precondition,
            code_files=code_files,
        )
        _git_preflight(
            root,
            required_paths=required_paths,
            expected_head=precondition,
        )
        _publish_or_authenticate(
            payload,
            destination=destination,
            predecessor_bytes=predecessor_raw,
            write=True,
        )
        return payload

    publication_commit = _locate_v8_publication_commit(
        root,
        current,
        current_head=precondition,
    )
    authenticated = authenticate_historical_v8_payload(
        root,
        current,
        publication_commit=publication_commit,
        current_head=precondition,
    )
    _git_preflight(
        root,
        required_paths=required_paths,
        expected_head=precondition,
    )
    _publish_or_authenticate(
        authenticated,
        destination=destination,
        predecessor_bytes=predecessor_raw,
        write=write,
    )
    return authenticated
