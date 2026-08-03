"""Planning V15: authorize native WorldCover mosaic alignment and resume."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import (
    plan_worldcover_bbox_query_hotfix_transition_v14 as v14,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 15
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v15"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_worldcover_native_mosaic_"
    "alignment_hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "stage_target_blind_missing_support_and_calibration_evidence_v1"
)
PLAN_PATH: Final = v14.PLAN_PATH
IMPLEMENTATION_BASE_COMMIT: Final = "c8d38718510f0ddb34c881bdb5e27d663c1c88c2"
V14_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V14_IMPLEMENTATION_COMMIT: Final = "50826b681517615846bfce8513e395f774e40821"
V14_BYTES: Final = 61_826
V14_FILE_SHA256: Final = (
    "a6d7692ad89451671a955ef49424934542f654fef0550dc10d5b751628399bff"
)
V14_INTERNAL_COMMIT_SHA256: Final = (
    "eb99f8abfbe99e3caae2379af894fa8a99b5abb0446dc7a3e1c480d2c1bb9364"
)
OLD_CONFIG_SHA256: Final = (
    "b9faa332a8f946ba4d07721c19c3eb95dc9e1307171fc3876f18c43c2076d85a"
)

CONFIG_PATH: Final = v14.CONFIG_PATH
EXECUTOR_MODULE_PATH: Final = v14.EXECUTOR_MODULE_PATH
WORLDCOVER_MODULE_PATH: Final = v14.WORLDCOVER_MODULE_PATH
SENTINEL_MODULE_PATH: Final = v14.SENTINEL_MODULE_PATH
WORLDCOVER_TEST_PATH: Final = v14.WORLDCOVER_TEST_PATH
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_worldcover_mosaic_alignment_hotfix_transition_v15.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_worldcover_mosaic_alignment_hotfix_v15.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_worldcover_mosaic_alignment_hotfix_transition_v15.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("M", CONFIG_PATH),
        ("M", EXECUTOR_MODULE_PATH),
        ("M", SENTINEL_MODULE_PATH),
        ("M", WORLDCOVER_MODULE_PATH),
        ("M", WORLDCOVER_TEST_PATH),
        ("A", TRANSITION_MODULE_PATH),
        ("A", AUTHORIZATION_SCRIPT_PATH),
        ("A", TRANSITION_TEST_PATH),
    }
)

RESUME_CHECKPOINTS: Final = v14.RESUME_CHECKPOINTS
RESUME_CHECKPOINT_PATHS: Final = v14.RESUME_CHECKPOINT_PATHS

AUTHORIZED_FIX: Final = {
    "worldcover_native_mosaic_bounds_before": "clipped_not_native_pixel_aligned",
    "worldcover_native_mosaic_bounds_after": "target_aligned_pixels",
    "failing_city": "houston_tx",
    "adjacent_native_tile_seam_latitude_degrees": 30.0,
    "adjacent_native_tile_count": 2,
    "pre_fix_forward_reverse_30m_difference_count": 130,
    "post_fix_forward_reverse_30m_difference_count": 0,
    "native_mosaic_before_reprojection_preserved": True,
    "resampling_mode_changed": False,
    "class_definition_changed": False,
    "completed_worldcover_city_checkpoints_preserved": [
        "los_angeles_ca",
        "phoenix_az",
    ],
    "completed_asset_cache_objects_preserved": 4,
    "collection_year_version_or_asset_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
    "conflicting_next_plan_version_replaced": "v15",
    "successful_evidence_next_plan_version": "v16",
}

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_code_configuration_and_git_blobs_read": True,
    "exact_target_blind_checkpoint_manifests_read": True,
    "raw_source_or_raster_payloads_opened_by_transition": False,
    "target_blind_static_raster_diagnosis_completed_before_transition": True,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(ValueError):
    """Raised when the exact V15 resume transition fails authentication."""


def _implementation_delta(project_root: Path, implementation: str) -> None:
    parents = str(
        v14._run_git(
            project_root, "rev-list", "--parents", "-n", "1", implementation
        )
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 implementation is not the V14 publication's direct child."
        )
    raw = v14._run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        IMPLEMENTATION_BASE_COMMIT,
        implementation,
        binary=True,
    )
    assert isinstance(raw, bytes)
    if v14._parse_delta(raw) != EXPECTED_IMPLEMENTATION_DELTA:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 implementation changed a path outside its exact allowlist."
        )


def _executor_contract(
    project_root: Path, *, implementation: str
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, bool]]:
    _implementation_delta(project_root, implementation)
    for relative in (CONFIG_PATH, EXECUTOR_MODULE_PATH):
        _, oid, _ = v14._git_blob(
            project_root, commit=implementation, relative_path=relative
        )
        worktree_oid = str(
            v14._run_git(
                project_root,
                "hash-object",
                f"--path={relative}",
                "--",
                relative,
            )
        ).strip()
        if worktree_oid != oid:
            raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
                f"V15 executor input differs from HEAD: {relative}"
            )
    module = importlib.import_module(
        "la_heat.multicity.missing_support_calibration_evidence_v1"
    )
    code_paths = tuple(module.CODE_PATHS)
    scope = deepcopy(module.expected_plan_authorization_scope())
    authorized = deepcopy(module.expected_authorized_now())
    if scope.get("configuration") != {
        "path": CONFIG_PATH,
        "sha256": module.CONFIG_SHA256,
    }:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 executor permission map changed."
        )
    return code_paths, scope, authorized


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and transition files frozen at V15 implementation."""

    return tuple(
        dict.fromkeys(
            (
                *executor_paths,
                v14.v13.TRANSITION_MODULE_PATH,
                v14.TRANSITION_MODULE_PATH,
                TRANSITION_MODULE_PATH,
                AUTHORIZATION_SCRIPT_PATH,
            )
        )
    )


def _historical_v14(project_root: Path) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = v14._git_blob(
        project_root,
        commit=V14_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V14_BYTES or v14._sha(raw) != V14_FILE_SHA256:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Historical planning V14 bytes changed."
        )
    payload = v14._json_bytes(raw, label="historical planning V14")
    scope = payload.get(
        "missing_support_calibration_evidence_v1_authorization_scope", {}
    )
    if (
        payload.get("schema_version") != 14
        or payload.get("algorithm_version") != "multicity-planning-readiness-v14"
        or payload.get("commit_sha256") != V14_INTERNAL_COMMIT_SHA256
        or scope.get("configuration")
        != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
        or payload.get("transition", {}).get("authorized_fix") != v14.AUTHORIZED_FIX
    ):
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Historical planning V14 contract changed."
        )
    return payload, raw


def _build_payload(
    predecessor: Mapping[str, Any],
    *,
    implementation: str,
    code_files: Mapping[str, Any],
    transition_code_files: Mapping[str, Any],
    authorization_scope: Mapping[str, Any],
    authorized_now: Mapping[str, bool],
) -> dict[str, Any]:
    payload = deepcopy(dict(predecessor))
    previous_transition = payload.pop("transition")
    payload.pop("commit_sha256")
    payload["schema_version"] = SCHEMA_VERSION
    payload["algorithm_version"] = ALGORITHM_VERSION
    payload["planning_stage"] = PLANNING_STAGE
    payload["next_safe_stage"] = NEXT_SAFE_STAGE
    payload["code_files"] = deepcopy(dict(code_files))
    payload["authorized_now"] = deepcopy(dict(authorized_now))
    payload[
        "missing_support_calibration_evidence_v1_authorization_scope"
    ] = deepcopy(dict(authorization_scope))
    payload["transition"] = {
        "id": "align_worldcover_native_mosaic_bounds_to_native_pixels_and_resume",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v14": {
            "path": PLAN_PATH,
            "publication_git_commit": V14_PUBLICATION_COMMIT,
            "implementation_git_commit": V14_IMPLEMENTATION_COMMIT,
            "bytes": V14_BYTES,
            "file_sha256": V14_FILE_SHA256,
            "commit_sha256": V14_INTERNAL_COMMIT_SHA256,
            "authorized_fix_sha256": canonical_sha256(v14.AUTHORIZED_FIX),
        },
        "consumed_v14_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_overall_tasks": 1,
            "completed_worldcover_city_checkpoints": 2,
            "failing_task": "four_city_worldcover_eligible_support",
            "failing_city": "houston_tx",
            "exception_type": "MissingSupportCalibrationEvidenceV1Error",
            "failing_gate": "WorldCover mosaic order changed target classes",
            "external_target_or_qa_values_read": False,
        },
        "authorized_fix": deepcopy(AUTHORIZED_FIX),
        "resume_checkpoints": deepcopy(list(RESUME_CHECKPOINTS)),
        "implementation": {
            "base_git_commit": IMPLEMENTATION_BASE_COMMIT,
            "implementation_git_commit": implementation,
            "delta": [
                {"status": status, "path": path}
                for status, path in sorted(EXPECTED_IMPLEMENTATION_DELTA)
            ],
        },
        "transition_code_files": deepcopy(dict(transition_code_files)),
        "writer_precondition": {
            "branch": "main",
            "git_head": implementation,
            "origin_main_equal": True,
            "allowed_untracked_paths": list(RESUME_CHECKPOINT_PATHS),
        },
        "access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
    }
    if payload["locks"] != predecessor["locks"]:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "V15 changed a scientific permission."
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _publication_commit(
    project_root: Path, *, implementation: str, head: str
) -> str:
    changes = str(
        v14._run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation}..{head}",
            "--",
            PLAN_PATH,
        )
    ).splitlines()
    if len(changes) != 1:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Planning V15 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        v14._run_git(
            project_root, "rev-list", "--parents", "-n", "1", publication
        )
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Planning V15 is not the hotfix implementation's direct child."
        )
    raw = v14._run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        implementation,
        publication,
        binary=True,
    )
    assert isinstance(raw, bytes)
    if v14._parse_delta(raw) != frozenset({("M", PLAN_PATH)}):
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Planning V15 publication changed more than PLAN_READINESS.json."
        )
    return publication


def authorize_multicity_worldcover_mosaic_alignment_hotfix_v15(
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate planning V15 and its seven resume checkpoints."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v14(root)
    current_raw = plan_path.read_bytes()
    writing_from_v14 = current_raw == predecessor_raw
    head = v14._preflight(root, allow_clean=not writing_from_v14)

    if writing_from_v14:
        if not write:
            raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
                "PLAN_READINESS is still V14."
            )
        implementation = head
        code_paths, scope, authorized = _executor_contract(
            root, implementation=implementation
        )
        code_files = v14._code_records(
            root, commit=implementation, paths=code_paths
        )
        transition_files = v14._code_records(
            root,
            commit=implementation,
            paths=transition_code_paths(code_paths),
        )
        payload = _build_payload(
            predecessor,
            implementation=implementation,
            code_files=code_files,
            transition_code_files=transition_files,
            authorization_scope=scope,
            authorized_now=authorized,
        )
        if plan_path.read_bytes() != predecessor_raw:
            raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
                "Planning changed before the V15 write boundary."
            )
        v14._atomic_replace(v14._expected_bytes(payload), plan_path)
        return payload

    observed = v14._json_bytes(current_raw, label="canonical planning V15")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get(
        "implementation_git_commit"
    )
    if not isinstance(implementation, str):
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Planning V15 implementation identity is missing."
        )
    code_paths, scope, authorized = _executor_contract(
        root, implementation=implementation
    )
    code_files = v14._code_records(root, commit=implementation, paths=code_paths)
    transition_paths = transition_code_paths(code_paths)
    transition_files = v14._code_records(
        root, commit=implementation, paths=transition_paths
    )
    expected = _build_payload(
        predecessor,
        implementation=implementation,
        code_files=code_files,
        transition_code_files=transition_files,
        authorization_scope=scope,
        authorized_now=authorized,
    )
    if current_raw != v14._expected_bytes(expected) or observed != expected:
        raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
            "Canonical planning V15 bytes changed."
        )
    _publication_commit(root, implementation=implementation, head=head)
    for relative in transition_paths:
        history = str(
            v14._run_git(
                root,
                "log",
                "--format=%H",
                f"{implementation}..{head}",
                "--",
                relative,
            )
        )
        if history.strip():
            raise MulticityPlanWorldCoverMosaicAlignmentHotfixV15Error(
                f"V15-authorized runtime changed after implementation: {relative}"
            )
    return observed
