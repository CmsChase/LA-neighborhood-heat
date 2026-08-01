"""Authorize only the evidence-only portable water-distance freeze decision.

The transition closes the consumed GSHHG L3 geometry-read permission and opens
one tracked-evidence decision permission.  It reads exact tracked JSON records
and local Git blobs only.  It does not import a geometry auditor or open source
archives, geometry, eligible-land support, predictors, models, targets, or
results.
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

from la_heat.multicity.portable_water_distance_freeze_v2 import (
    CODE_PATHS as V2_DECISION_RUNTIME_PATHS,
)
from la_heat.multicity.portable_water_distance_freeze_v2 import (
    expected_plan_authorization_scope,
)

SCHEMA_VERSION: Final = 7
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v7"
PLANNING_STAGE: Final = (
    "gshhg_l3_hierarchy_audit_complete_freeze_decision_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "separate_portable_water_distance_source_and_algorithm_freeze_decision"
)
EXPERIMENT_ID: Final = "la_to_three_city_zero_shot_v1"
EXPERIMENT_SEMANTIC_SHA256: Final = (
    "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
)

PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
V6_PUBLICATION_COMMIT: Final = "6d48d5a6def99c8f9e9fe03997850046c693f538"
V6_FILE_SHA256: Final = (
    "9a8f8b93ccfa89bf43354cb09d6d92fee1b436eb5edbd227b75d794dd49cac6c"
)
V6_INTERNAL_COMMIT_SHA256: Final = (
    "1789d828f212e0cd65f87c9427eb4a7fbd1697cc7170ebb98a80806659afbc86"
)
V6_BYTES: Final = 13_327

L3_SUCCESS_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT.json"
)
L3_SUCCESS_PUBLICATION_COMMIT: Final = (
    "0afb1f9868378f12e8fe8b66f5772fde6685ed1f"
)
L3_SUCCESS_FILE_SHA256: Final = (
    "9b206f449d71f23ff0f13d0adca436a2d433140560fef92646d48a7e5c522070"
)
L3_SUCCESS_INTERNAL_COMMIT_SHA256: Final = (
    "9b7f6c814bda4e97120a6768b88feae37ee73044883b2ec8cad10db8d4af0f0b"
)
L3_SUCCESS_BYTES: Final = 109_139
L3_SUCCESS_STATE: Final = (
    "gshhg_l3_hierarchy_audit_v2_complete_source_not_frozen"
)

V2_CONFIG_PATH: Final = (
    "configs/multicity/portable_water_distance_freeze_decision_v2.toml"
)
V2_MODULE_PATH: Final = (
    "src/la_heat/multicity/portable_water_distance_freeze_v2.py"
)
V2_SCRIPT_PATH: Final = (
    "scripts/audit_multicity_portable_water_distance_freeze_v2.py"
)
V7_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_freeze_transition_v7.py"
)
V7_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_water_distance_freeze.py"
)
TRANSITION_CODE_PATHS: Final = tuple(V2_DECISION_RUNTIME_PATHS)

V6_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": True,
    "portable_predictor_source_freeze": False,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
AUTHORIZED_NOW: Final = {
    **V6_AUTHORIZED_NOW,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": True,
}
LOCKS: Final = {
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
BLOCKERS_BEFORE_PREDICTOR_BUILD: Final = (
    "freeze_portable_water_distance_source_and_algorithm",
    "freeze_exact_portable_predictor_source_and_calibration_contract",
    "promote_protocol_from_draft_with_separate_lock",
)
EXPECTED_AUTHORIZATION_DIFF_PATHS: Final = (
    "authorized_now.portable_predictor_source_freeze",
    "authorized_now.target_blind_gshhg_l3_hierarchy_geometry_read",
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
    "gshhg_l1_geometry_opened": False,
    "gshhg_l2_geometry_opened": False,
    "gshhg_l3_geometry_opened": False,
    "gshhg_l4_geometry_opened": False,
    "other_public_source_geometry_opened": False,
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


class MulticityPlanFreezeTransitionV7Error(ValueError):
    """Raised when the narrow v7 authorization cannot be authenticated."""


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
        raise MulticityPlanFreezeTransitionV7Error(f"{label} must be an object.")
    return value


def _json_object_from_bytes(payload_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanFreezeTransitionV7Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanFreezeTransitionV7Error(
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
        raise MulticityPlanFreezeTransitionV7Error(
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
        raise MulticityPlanFreezeTransitionV7Error(
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


def _validate_v6_predecessor(
    payload: Mapping[str, Any],
    payload_bytes: bytes,
) -> None:
    if len(payload_bytes) != V6_BYTES or _sha256_bytes(payload_bytes) != V6_FILE_SHA256:
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical v6 predecessor bytes changed."
        )
    expected_identity = {
        "schema_version": 6,
        "algorithm_version": "multicity-planning-readiness-v6",
        "state": "planning_ready",
        "planning_stage": (
            "gshhg_l3_hierarchy_audit_preregistered_geometry_authorized_unopened"
        ),
        "experiment_id": EXPERIMENT_ID,
        "config_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "next_safe_stage": "target_blind_gshhg_l3_hierarchy_geometry_audit",
        "commit_sha256": V6_INTERNAL_COMMIT_SHA256,
    }
    for key, expected in expected_identity.items():
        if not _strict_equal(payload.get(key), expected):
            raise MulticityPlanFreezeTransitionV7Error(
                f"The canonical v6 predecessor field changed: {key}"
            )
    if not _strict_equal(payload.get("authorized_now"), V6_AUTHORIZED_NOW):
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical v6 authorization boundary changed."
        )
    if not _strict_equal(payload.get("locks"), LOCKS):
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical v6 experiment locks changed."
        )


def _validate_l3_success(
    payload: Mapping[str, Any],
    payload_bytes: bytes,
) -> None:
    if (
        len(payload_bytes) != L3_SUCCESS_BYTES
        or _sha256_bytes(payload_bytes) != L3_SUCCESS_FILE_SHA256
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical L3 success bytes changed."
        )
    expected_identity = {
        "schema_version": 1,
        "algorithm_version": "gshhg-l3-hierarchy-audit-v2",
        "state": L3_SUCCESS_STATE,
        "commit_sha256": L3_SUCCESS_INTERNAL_COMMIT_SHA256,
    }
    for key, expected in expected_identity.items():
        if not _strict_equal(payload.get(key), expected):
            raise MulticityPlanFreezeTransitionV7Error(
                f"The canonical L3 success field changed: {key}"
            )
    decision = _require_mapping(payload.get("decision"), label="L3 decision")
    if not _strict_equal(
        decision,
        {
            "audit_passed": True,
            "source_frozen": False,
            "algorithm_frozen": False,
            "predictor_build_authorized": False,
            "next_safe_stage": NEXT_SAFE_STAGE,
        },
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical L3 decision boundary changed."
        )
    locks = _require_mapping(payload.get("locks"), label="L3 locks")
    if any(value is not False for value in locks.values()):
        raise MulticityPlanFreezeTransitionV7Error(
            "The canonical L3 audit unexpectedly created a lock."
        )


def _require_exact_precondition_plan(
    project_root: Path,
    *,
    precondition_commit: str,
    predecessor_raw: bytes,
) -> None:
    observed, _, _ = _git_regular_blob(
        project_root,
        commit=precondition_commit,
        relative_path=PLAN_PATH,
    )
    if observed != predecessor_raw:
        raise MulticityPlanFreezeTransitionV7Error(
            "PLAN_READINESS at the v7 precondition is not the exact v6 predecessor."
        )


def _require_v7_plan_history(
    project_root: Path,
    *,
    publication_commit: str,
    published_raw: bytes,
    current_head: str | None,
) -> None:
    plan_history = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V6_PUBLICATION_COMMIT}..{publication_commit}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(plan_history, str)
    if [line for line in plan_history.splitlines() if line] != [
        publication_commit
    ]:
        raise MulticityPlanFreezeTransitionV7Error(
            "PLAN_READINESS changed outside the one v6-to-v7 publication."
        )
    if current_head is None:
        return
    if re.fullmatch(r"[0-9a-f]{40}", current_head) is None:
        raise MulticityPlanFreezeTransitionV7Error(
            "The current v7 authentication HEAD is invalid."
        )
    if not _is_ancestor(project_root, publication_commit, current_head):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 publication is not an ancestor of current HEAD."
        )
    later_plan_history = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{publication_commit}..{current_head}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(later_plan_history, str)
    if later_plan_history.strip():
        raise MulticityPlanFreezeTransitionV7Error(
            "PLAN_READINESS changed after the unique v7 publication."
        )
    current_plan_raw, _, _ = _git_regular_blob(
        project_root,
        commit=current_head,
        relative_path=PLAN_PATH,
    )
    if current_plan_raw != published_raw:
        raise MulticityPlanFreezeTransitionV7Error(
            "Current PLAN_READINESS differs from the v7 publication."
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
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 publication changed a frozen transition code blob."
        )


def _validate_config_files_at_commit(
    project_root: Path,
    *,
    commit: str,
    config_files: object,
) -> None:
    records = _require_mapping(config_files, label="v6 config_files")
    for relative_path, raw_record in records.items():
        if not isinstance(relative_path, str):
            raise MulticityPlanFreezeTransitionV7Error(
                "A v6 config path is not a string."
            )
        record = _require_mapping(raw_record, label=f"config record {relative_path}")
        payload, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=relative_path,
        )
        expected = {
            "sha256": _sha256_bytes(payload),
            "bytes": len(payload),
        }
        if not _strict_equal(record, expected):
            raise MulticityPlanFreezeTransitionV7Error(
                f"Experiment config changed at the v7 precondition: {relative_path}"
            )


def _validate_scope_bindings(
    scope: Mapping[str, Any],
    code_files: Mapping[str, Any],
) -> None:
    expected_scope = expected_plan_authorization_scope()
    if not _strict_equal(scope, expected_scope):
        raise MulticityPlanFreezeTransitionV7Error(
            "The V2 authorization scope differs from the exact tracked read set."
        )
    if not _strict_equal(
        scope.get("decision_runtime_paths"),
        list(TRANSITION_CODE_PATHS),
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The V2 authorization scope runtime paths changed."
        )
    for relative_path in TRANSITION_CODE_PATHS:
        _require_mapping(
            code_files.get(relative_path),
            label=f"transition code record {relative_path}",
        )
    tracked_read_set = _require_mapping(
        scope.get("tracked_read_set"),
        label="V2 tracked read set",
    )
    success_record = _require_mapping(
        tracked_read_set.get("l3_v2_success"),
        label="V2 L3 success scope record",
    )
    if not _strict_equal(
        success_record,
        expected_scope["tracked_read_set"]["l3_v2_success"],
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The V2 authorization scope changed the L3 success identity."
        )
    config_record = _require_mapping(
        code_files.get(V2_CONFIG_PATH),
        label="V2 config code record",
    )
    if not _strict_equal(
        config_record.get("sha256"),
        scope.get("decision_config_sha256"),
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The V2 config scope does not match its tracked precondition blob."
        )


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
        keys = sorted(set(left) | set(right))  # type: ignore[arg-type]
        for key in keys:
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
        length = max(len(left), len(right))
        for index in range(length):
            path = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(path)
            else:
                paths.extend(
                    _recursive_diff_paths(
                        left[index],
                        right[index],
                        prefix=path,
                    )
                )
        return tuple(paths)
    return () if left == right else (prefix,)


def _validate_authorization_boundary(
    predecessor: Mapping[str, Any],
    successor: Mapping[str, Any],
) -> None:
    if not _strict_equal(predecessor.get("authorized_now"), V6_AUTHORIZED_NOW):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v6 authorization boundary is not canonical."
        )
    if not _strict_equal(successor.get("authorized_now"), AUTHORIZED_NOW):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 authorization boundary opened a wrong permission."
        )
    changes = _recursive_diff_paths(
        {"authorized_now": predecessor["authorized_now"]},
        {"authorized_now": successor["authorized_now"]},
    )
    if changes != EXPECTED_AUTHORIZATION_DIFF_PATHS:
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition must change exactly two authorization leaves."
        )
    if not _strict_equal(successor.get("locks"), LOCKS):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition changed an experiment lock."
        )


def _build_v7_payload(
    predecessor: Mapping[str, Any],
    *,
    predecessor_bytes: int,
    l3_success: Mapping[str, Any],
    l3_success_bytes: int,
    precondition_commit: str,
    code_files: Mapping[str, Any],
    authorization_scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one exact v7 payload from already authenticated evidence."""

    if re.fullmatch(r"[0-9a-f]{40}", precondition_commit) is None:
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 precondition Git commit is invalid."
        )
    _validate_scope_bindings(authorization_scope, code_files)
    geometry_scope = deepcopy(
        _require_mapping(
            predecessor.get("geometry_audit_authorization_scope"),
            label="v6 geometry authorization scope",
        )
    )
    l3_decision = _require_mapping(l3_success.get("decision"), label="L3 decision")
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
                "close_consumed_l3_geometry_and_authorize_"
                "portable_water_distance_freeze_decision_v2"
            ),
            "mode": "tracked_manifests_and_local_git_only",
            "predecessor_plan_readiness": {
                "path": PLAN_PATH,
                "source_git_commit": V6_PUBLICATION_COMMIT,
                "file_sha256": V6_FILE_SHA256,
                "bytes": predecessor_bytes,
                "commit_sha256": predecessor["commit_sha256"],
                "state": predecessor["state"],
                "planning_stage": predecessor["planning_stage"],
                "next_safe_stage": predecessor["next_safe_stage"],
            },
            "completed_l3_hierarchy_audit": {
                "path": L3_SUCCESS_PATH,
                "source_git_commit": L3_SUCCESS_PUBLICATION_COMMIT,
                "file_sha256": L3_SUCCESS_FILE_SHA256,
                "bytes": l3_success_bytes,
                "commit_sha256": l3_success["commit_sha256"],
                "state": l3_success["state"],
                "next_safe_stage": l3_decision["next_safe_stage"],
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
                "v7_check_only_passes_before_the_freeze_decision": True,
            },
        },
        "transition_access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
        "cities": deepcopy(predecessor["cities"]),
        "locks": deepcopy(LOCKS),
        "authorized_now": deepcopy(AUTHORIZED_NOW),
        "consumed_geometry_audit_authorization": {
            "status": "consumed_and_closed",
            "predecessor_schema_version": 6,
            "predecessor_scope": geometry_scope,
            "completion_manifest_path": L3_SUCCESS_PATH,
            "completion_manifest_file_sha256": L3_SUCCESS_FILE_SHA256,
            "completion_manifest_commit_sha256": (
                L3_SUCCESS_INTERNAL_COMMIT_SHA256
            ),
            "geometry_read_permission_now": False,
            "archive_member_or_geometry_reread_by_transition": False,
        },
        "freeze_decision_authorization_scope": deepcopy(dict(authorization_scope)),
        "workspace": deepcopy(predecessor["workspace"]),
        "gshhg_geometry_pilot": deepcopy(predecessor["gshhg_geometry_pilot"]),
        "portable_water_distance_freeze_decision": deepcopy(
            predecessor["portable_water_distance_freeze_decision"]
        ),
        "gshhg_l3_hierarchy_audit_preregistration": deepcopy(
            predecessor["gshhg_l3_hierarchy_audit_preregistration"]
        ),
        "gshhg_l3_hierarchy_audit": {
            "path": L3_SUCCESS_PATH,
            "file_sha256": L3_SUCCESS_FILE_SHA256,
            "bytes": l3_success_bytes,
            "commit_sha256": L3_SUCCESS_INTERNAL_COMMIT_SHA256,
            "publication_git_commit": L3_SUCCESS_PUBLICATION_COMMIT,
            "state": L3_SUCCESS_STATE,
            "audit_passed": True,
            "source_frozen": False,
            "algorithm_frozen": False,
            "predictor_build_authorized": False,
            "authentication_mode": (
                "exact_current_and_historical_tracked_manifest_bytes"
            ),
            "archive_member_or_geometry_reopened": False,
        },
        "blockers_before_predictor_build": list(
            BLOCKERS_BEFORE_PREDICTOR_BUILD
        ),
        "next_safe_stage": NEXT_SAFE_STAGE,
    }
    _validate_authorization_boundary(predecessor, payload)
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_exact_v7_payload(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    recorded = observed.get("commit_sha256")
    body = {key: value for key, value in observed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 planning internal commit is invalid."
        )
    if not _strict_equal(observed, expected):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 planning record differs from the complete reconstructed payload."
        )


def authenticate_historical_v7_payload(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str | None = None,
    current_head: str | None = None,
) -> dict[str, Any]:
    """Reconstruct and authenticate v7 without reading current PLAN_READINESS.

    The caller supplies bytes already parsed from either the current or a
    historical plan.  Every other input is reloaded from an exact Git blob.
    This helper is intentionally safe for a later V2 decision authenticator.
    """

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v7 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v7 writer precondition",
    )
    precondition_commit = writer.get("git_head")
    if (
        not isinstance(precondition_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", precondition_commit) is None
    ):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 precondition Git commit is invalid."
        )
    for ancestor in (V6_PUBLICATION_COMMIT, L3_SUCCESS_PUBLICATION_COMMIT):
        if not _is_ancestor(root, ancestor, precondition_commit):
            raise MulticityPlanFreezeTransitionV7Error(
                "The v7 precondition does not descend from canonical evidence."
            )

    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V6_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v6_predecessor(predecessor, predecessor_raw)
    _require_exact_precondition_plan(
        root,
        precondition_commit=precondition_commit,
        predecessor_raw=predecessor_raw,
    )
    l3_success, l3_raw = _historical_json(
        root,
        commit=L3_SUCCESS_PUBLICATION_COMMIT,
        relative_path=L3_SUCCESS_PATH,
    )
    _validate_l3_success(l3_success, l3_raw)
    l3_at_precondition, _, _ = _git_regular_blob(
        root,
        commit=precondition_commit,
        relative_path=L3_SUCCESS_PATH,
    )
    if l3_at_precondition != l3_raw:
        raise MulticityPlanFreezeTransitionV7Error(
            "The L3 success differs at the v7 precondition commit."
        )
    _validate_config_files_at_commit(
        root,
        commit=precondition_commit,
        config_files=predecessor["config_files"],
    )
    code_files = _code_records_at_commit(root, commit=precondition_commit)
    scope = expected_plan_authorization_scope()
    expected = _build_v7_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        l3_success=l3_success,
        l3_success_bytes=len(l3_raw),
        precondition_commit=precondition_commit,
        code_files=code_files,
        authorization_scope=scope,
    )
    _validate_exact_v7_payload(payload, expected)

    if publication_commit is not None:
        if re.fullmatch(r"[0-9a-f]{40}", publication_commit) is None:
            raise MulticityPlanFreezeTransitionV7Error(
                "The v7 publication Git commit is invalid."
            )
        ancestry = _run_git(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            publication_commit,
        )
        assert isinstance(ancestry, str)
        ancestry_parts = ancestry.split()
        if len(ancestry_parts) != 2 or ancestry_parts[1] != precondition_commit:
            raise MulticityPlanFreezeTransitionV7Error(
                "The v7 publication is not the direct child of its precondition."
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
            raise MulticityPlanFreezeTransitionV7Error(
                "The supplied v7 payload differs from its publication Git blob."
            )
        _require_publication_code_files(
            root,
            publication_commit=publication_commit,
            expected_code_files=code_files,
        )
        _require_v7_plan_history(
            root,
            publication_commit=publication_commit,
            published_raw=published_raw,
            current_head=current_head,
        )
    return deepcopy(dict(payload))


def _read_current_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        before = path.read_bytes()
        after = path.read_bytes()
    except OSError as exc:
        raise MulticityPlanFreezeTransitionV7Error(
            f"Cannot read authenticated JSON: {path}"
        ) from exc
    if before != after:
        raise RuntimeError(f"Authenticated input changed while read: {path}")
    return _json_object_from_bytes(before, label=str(path)), before


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
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition requires branch main."
        )
    if head != origin_main.strip():
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition requires HEAD to equal local origin/main."
        )
    if expected_head is not None and head != expected_head:
        raise MulticityPlanFreezeTransitionV7Error(
            "HEAD changed between v7 transition gates."
        )
    if status:
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition requires a completely clean working tree."
        )
    for ancestor in (V6_PUBLICATION_COMMIT, L3_SUCCESS_PUBLICATION_COMMIT):
        if not _is_ancestor(project_root, ancestor, head):
            raise MulticityPlanFreezeTransitionV7Error(
                "Canonical v6 or L3 evidence is not an ancestor of HEAD."
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
            raise MulticityPlanFreezeTransitionV7Error(
                "A required input differs from HEAD, including through an index "
                f"visibility flag: {relative_path}"
            )
    return head


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _locate_v7_publication_commit(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    current_head: str,
) -> str:
    """Locate the unique commit that replaced v6 with these exact v7 bytes."""

    transition = _require_mapping(payload.get("transition"), label="v7 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v7 writer precondition",
    )
    precondition = writer.get("git_head")
    if not isinstance(precondition, str):
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 writer precondition commit is missing."
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
        parts = ancestry.split()
        if len(parts) != 2 or parts[1] != precondition:
            continue
        published, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=PLAN_PATH,
        )
        if published == expected_bytes:
            candidates.append(commit)
    if len(candidates) != 1:
        raise MulticityPlanFreezeTransitionV7Error(
            "The exact v7 transition must have one unique direct Git publication."
        )
    return candidates[0]


def _publish_or_authenticate(
    payload: Mapping[str, Any],
    *,
    destination: Path,
    predecessor_bytes: bytes,
    write: bool,
) -> None:
    expected_bytes = _expected_json_bytes(payload)
    if not destination.is_file():
        raise FileNotFoundError(destination)
    current = destination.read_bytes()
    if write and current == predecessor_bytes:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.write_bytes(expected_bytes)
        temporary.replace(destination)
        return
    if current != expected_bytes:
        action = "replace" if write else "authenticate"
        raise MulticityPlanFreezeTransitionV7Error(
            f"Refusing to {action} a PLAN_READINESS that is neither the exact "
            "v6 predecessor nor the byte-identical reconstructed v7."
        )


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def authorize_multicity_water_distance_freeze(
    *,
    project_root: str | Path | None = None,
    output_path: str | Path = PLAN_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the narrow tracked-only v7 transition."""

    root = (
        _default_project_root()
        if project_root is None
        else Path(project_root).resolve()
    )
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    expected_destination = (root / PLAN_PATH).resolve()
    if destination != expected_destination:
        raise MulticityPlanFreezeTransitionV7Error(
            "The v7 transition may only replace canonical PLAN_READINESS.json."
        )

    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V6_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v6_predecessor(predecessor, predecessor_raw)
    config_paths = tuple(
        _require_mapping(
            predecessor.get("config_files"),
            label="v6 config_files",
        )
    )
    required_paths = tuple(
        dict.fromkeys(
            (
                *config_paths,
                *TRANSITION_CODE_PATHS,
                PLAN_PATH,
                L3_SUCCESS_PATH,
            )
        )
    )
    precondition_head = _git_preflight(
        root,
        required_paths=required_paths,
    )
    current, current_raw = _read_current_json(destination)

    if _sha256_bytes(current_raw) == V6_FILE_SHA256:
        if not write:
            raise MulticityPlanFreezeTransitionV7Error(
                "PLAN_READINESS is still v6; the v7 transition has not been written."
            )
        _validate_v6_predecessor(current, current_raw)
        if current_raw != predecessor_raw:
            raise MulticityPlanFreezeTransitionV7Error(
                "Current v6 bytes differ from the canonical historical blob."
            )
        l3_success, l3_raw = _historical_json(
            root,
            commit=L3_SUCCESS_PUBLICATION_COMMIT,
            relative_path=L3_SUCCESS_PATH,
        )
        _validate_l3_success(l3_success, l3_raw)
        current_l3, current_l3_raw = _read_current_json(root / L3_SUCCESS_PATH)
        _validate_l3_success(current_l3, current_l3_raw)
        if current_l3_raw != l3_raw:
            raise MulticityPlanFreezeTransitionV7Error(
                "Current L3 success differs from its canonical publication."
            )
        _validate_config_files_at_commit(
            root,
            commit=precondition_head,
            config_files=predecessor["config_files"],
        )
        code_files = _code_records_at_commit(
            root,
            commit=precondition_head,
        )
        payload = _build_v7_payload(
            predecessor,
            predecessor_bytes=len(predecessor_raw),
            l3_success=l3_success,
            l3_success_bytes=len(l3_raw),
            precondition_commit=precondition_head,
            code_files=code_files,
            authorization_scope=expected_plan_authorization_scope(),
        )
        _git_preflight(
            root,
            required_paths=required_paths,
            expected_head=precondition_head,
        )
        _publish_or_authenticate(
            payload,
            destination=destination,
            predecessor_bytes=predecessor_raw,
            write=True,
        )
        return payload

    publication_commit = _locate_v7_publication_commit(
        root,
        current,
        current_head=precondition_head,
    )
    authenticated = authenticate_historical_v7_payload(
        root,
        current,
        publication_commit=publication_commit,
        current_head=precondition_head,
    )
    _validate_config_files_at_commit(
        root,
        commit=precondition_head,
        config_files=predecessor["config_files"],
    )
    current_code_files = _code_records_at_commit(
        root,
        commit=precondition_head,
    )
    if not _strict_equal(current.get("code_files"), current_code_files):
        raise MulticityPlanFreezeTransitionV7Error(
            "Current transition code differs from the v7 frozen code records."
        )
    canonical_l3_raw, _, _ = _git_regular_blob(
        root,
        commit=L3_SUCCESS_PUBLICATION_COMMIT,
        relative_path=L3_SUCCESS_PATH,
    )
    current_l3_raw, _, _ = _git_regular_blob(
        root,
        commit=precondition_head,
        relative_path=L3_SUCCESS_PATH,
    )
    if current_l3_raw != canonical_l3_raw:
        raise MulticityPlanFreezeTransitionV7Error(
            "Current L3 success differs from its canonical publication."
        )
    _git_preflight(
        root,
        required_paths=required_paths,
        expected_head=precondition_head,
    )
    _publish_or_authenticate(
        authenticated,
        destination=destination,
        predecessor_bytes=predecessor_raw,
        write=write,
    )
    return authenticated
