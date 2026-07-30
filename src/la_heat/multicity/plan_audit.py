"""Tracked-manifest-only planning audit for the cross-city continuation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.config import MulticityPlan, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import canonical_sha256, sha256_file

PLAN_AUDIT_SCHEMA_VERSION: Final = 6
PLAN_AUDIT_ALGORITHM_VERSION: Final = "multicity-planning-readiness-v6"
PLAN_AUDIT_CODE_PATHS: Final = (
    "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml",
    "scripts/audit_multicity_plan.py",
    "scripts/preregister_multicity_gshhg_l3_hierarchy_audit.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/gshhg_l3_hierarchy_preregistration.py",
    "src/la_heat/multicity/plan_audit.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/provenance.py",
)

CANONICAL_PREREGISTRATION_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
)
CANONICAL_PREREGISTRATION_FILE_SHA256: Final = (
    "ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f"
)
CANONICAL_PREREGISTRATION_COMMIT_SHA256: Final = (
    "7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7"
)
CANONICAL_PREREGISTRATION_GIT_COMMIT: Final = (
    "997e86d9ab06d22c04faad6fe714eacde53c9921"
)
CANONICAL_PREDECESSOR_PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
CANONICAL_PREDECESSOR_PLAN_FILE_SHA256: Final = (
    "1411d6a2ab0cfe3d3c13713194818a901face864172ed5ebde5e8e946a3e5a01"
)
CANONICAL_PREDECESSOR_PLAN_COMMIT_SHA256: Final = (
    "ebe371cdb8e9dc39c086fc394ce33d4d113abc44d83e2289bef6c74988021001"
)
CANONICAL_GSHHG_PILOT_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_GEOMETRY_PILOT.json"
)
CANONICAL_GSHHG_PILOT_FILE_SHA256: Final = (
    "71d68e35a67d82d5e8d7746cc9732d9cd1b8d880ed126e1c2af46cc72615bad1"
)
CANONICAL_GSHHG_PILOT_COMMIT_SHA256: Final = (
    "e14cbd4763489fbacdec3ac45348226e2ae677073aa592aabf9bc0e3d8256735"
)
CANONICAL_FREEZE_DECISION_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION.json"
)
CANONICAL_FREEZE_DECISION_FILE_SHA256: Final = (
    "226788498dfd8c9eb0aa004d60667dfa712926d8bf443fd710e17a7f5f8d5805"
)
CANONICAL_FREEZE_DECISION_COMMIT_SHA256: Final = (
    "00e8ed677035f8f8315b7171fa8c969ca6c50c14b0114eff9e5024bb1c7b99b5"
)

PREDECESSOR_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": True,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": False,
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
    "target_blind_gshhg_l3_hierarchy_geometry_read": True,
    "portable_predictor_source_freeze": False,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
BLOCKERS_BEFORE_PREDICTOR_BUILD: Final = (
    "resolve_and_audit_gshhg_l3_lake_island_shoreline_contract",
    "freeze_portable_water_distance_source_and_algorithm",
    "freeze_exact_portable_predictor_source_and_calibration_contract",
    "promote_protocol_from_draft_with_separate_lock",
)
FALSE_LOCK_FIELDS: Final = (
    "source_lock_created",
    "algorithm_lock_created",
    "feature_names_frozen",
    "predictor_build_authorized",
    "protocol_lock_created",
)
FALSE_PREREGISTRATION_ACCESS_FIELDS: Final = (
    "gshhg_archive_bytes_opened_by_preregistration_program",
    "gshhg_archive_members_opened_by_preregistration_program",
    "gshhg_l3_member_opened",
    "gshhg_l3_geometry_opened",
    "gshhg_l4_member_opened",
    "other_public_source_geometry_opened",
    "eligible_land_grid_opened",
    "distance_values_computed",
    "distance_feature_surface_computed",
    "tract_aggregation_performed",
    "predictor_values_computed",
    "predictor_construction_performed",
    "model_fit_performed",
    "model_predictions_computed",
    "landsat_thermal_values_read",
    "landsat_target_qa_values_read",
    "external_lst_values_read",
    "external_target_files_opened",
    "final_evaluation_outputs_opened",
)
PERMITTED_GSHHG_MEMBER_PATHS: Final = (
    "GSHHS_shp/f/GSHHS_f_L1.dbf",
    "GSHHS_shp/f/GSHHS_f_L1.prj",
    "GSHHS_shp/f/GSHHS_f_L1.shp",
    "GSHHS_shp/f/GSHHS_f_L1.shx",
    "GSHHS_shp/f/GSHHS_f_L2.dbf",
    "GSHHS_shp/f/GSHHS_f_L2.prj",
    "GSHHS_shp/f/GSHHS_f_L2.shp",
    "GSHHS_shp/f/GSHHS_f_L2.shx",
    "GSHHS_shp/f/GSHHS_f_L3.dbf",
    "GSHHS_shp/f/GSHHS_f_L3.prj",
    "GSHHS_shp/f/GSHHS_f_L3.shp",
    "GSHHS_shp/f/GSHHS_f_L3.shx",
)


class MulticityPlanAuditError(ValueError):
    """Raised when tracked planning evidence or continuation locks do not match."""


def _json_object_from_bytes(payload_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanAuditError(f"Cannot parse authenticated JSON: {label}") from exc
    if not isinstance(payload, dict):
        raise MulticityPlanAuditError(
            f"Authenticated JSON must be an object: {label}"
        )
    recorded = payload.get("commit_sha256")
    without_commit = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(without_commit) != recorded:
        raise MulticityPlanAuditError(f"Invalid internal commit hash: {label}")
    return payload


def _committed_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload_bytes = path.read_bytes()
    except OSError as exc:
        raise MulticityPlanAuditError(f"Cannot read authenticated JSON: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Input changed while being read: {path}")
    payload = _json_object_from_bytes(payload_bytes, label=str(path))
    return payload, before


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
        raise MulticityPlanAuditError(
            f"Git preflight failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _historical_committed_json(
    project_root: Path,
    *,
    git_commit: str,
    relative_path: str,
) -> tuple[dict[str, Any], str, int]:
    payload_bytes = _run_git(
        project_root,
        "show",
        f"{git_commit}:{relative_path}",
        binary=True,
    )
    assert isinstance(payload_bytes, bytes)
    payload = _json_object_from_bytes(
        payload_bytes,
        label=f"{git_commit}:{relative_path}",
    )
    return payload, hashlib.sha256(payload_bytes).hexdigest(), len(payload_bytes)


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
    if branch.strip() != "main":
        raise MulticityPlanAuditError("Planning transition requires branch main.")
    if head.strip() != origin_main.strip():
        raise MulticityPlanAuditError(
            "Planning transition requires HEAD to equal local origin/main."
        )
    if expected_head is not None and head.strip() != expected_head:
        raise MulticityPlanAuditError(
            "HEAD changed between planning-transition preflight gates."
        )
    if status:
        raise MulticityPlanAuditError(
            "Planning transition requires a completely clean working tree."
        )

    merge_base_status = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            CANONICAL_PREREGISTRATION_GIT_COMMIT,
            "HEAD",
        ],
        check=False,
        capture_output=True,
    ).returncode
    if merge_base_status != 0:
        raise MulticityPlanAuditError(
            "Canonical preregistration commit is not an ancestor of HEAD."
        )

    for relative_path in required_paths:
        _run_git(
            project_root,
            "ls-files",
            "--error-unmatch",
            "--",
            relative_path,
        )
        tree_record = _run_git(
            project_root,
            "ls-tree",
            "HEAD",
            "--",
            relative_path,
        )
        assert isinstance(tree_record, str)
        parts = tree_record.strip().split(maxsplit=3)
        if len(parts) != 4 or parts[0] not in {"100644", "100755"} or parts[1] != "blob":
            raise MulticityPlanAuditError(
                f"Required planning input is not a regular tracked blob: {relative_path}"
            )
        worktree_blob = _run_git(
            project_root,
            "hash-object",
            f"--path={relative_path}",
            "--",
            relative_path,
        )
        assert isinstance(worktree_blob, str)
        if worktree_blob.strip() != parts[2]:
            raise MulticityPlanAuditError(
                "Required planning input bytes differ from HEAD, including through "
                f"an index visibility flag: {relative_path}"
            )
    return head.strip()


def _require_exact_fields(
    payload: dict[str, Any],
    expected: dict[str, Any],
    *,
    label: str,
) -> None:
    for key, value in expected.items():
        observed = payload.get(key)
        if type(observed) is not type(value) or observed != value:
            raise MulticityPlanAuditError(f"{label} field changed: {key}")


def _validate_predecessor_plan(
    payload: dict[str, Any],
    *,
    file_sha256: str,
) -> None:
    if file_sha256 != CANONICAL_PREDECESSOR_PLAN_FILE_SHA256:
        raise MulticityPlanAuditError("Predecessor PLAN_READINESS bytes changed.")
    _require_exact_fields(
        payload,
        {
            "schema_version": 5,
            "algorithm_version": "multicity-planning-readiness-v5",
            "state": "planning_ready",
            "planning_stage": (
                "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit"
            ),
            "experiment_id": "la_to_three_city_zero_shot_v1",
            "config_semantic_sha256": (
                "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
            ),
            "next_safe_stage": "preregister_target_blind_gshhg_l3_hierarchy_audit",
            "commit_sha256": CANONICAL_PREDECESSOR_PLAN_COMMIT_SHA256,
        },
        label="Predecessor PLAN_READINESS",
    )
    if payload.get("authorized_now") != PREDECESSOR_AUTHORIZED_NOW:
        raise MulticityPlanAuditError(
            "Predecessor PLAN_READINESS authorization boundary changed."
        )


def _validate_preregistration_contract(
    payload: dict[str, Any],
    *,
    file_sha256: str,
    plan: MulticityPlan,
    project_root: Path,
) -> None:
    if file_sha256 != CANONICAL_PREREGISTRATION_FILE_SHA256:
        raise MulticityPlanAuditError("Canonical L3 preregistration bytes changed.")
    _require_exact_fields(
        payload,
        {
            "schema_version": 1,
            "algorithm_version": "gshhg-l3-hierarchy-audit-preregistration-v1",
            "state": "gshhg_l3_hierarchy_audit_preregistered_geometry_unopened",
            "preregistration_id": "target_blind_gshhg_l3_hierarchy_audit_v1",
            "base_repository_commit": "9209ec244319f14be7c2bcb8b56c38bee12256e0",
            "experiment_id": plan.experiment_id,
            "plan_semantic_sha256": plan.semantic_sha256,
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "commit_sha256": CANONICAL_PREREGISTRATION_COMMIT_SHA256,
        },
        label="L3 preregistration",
    )
    locks = payload.get("locks")
    if not isinstance(locks, dict):
        raise MulticityPlanAuditError("L3 preregistration locks are missing.")
    expected_locks = {
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }
    if locks != expected_locks:
        raise MulticityPlanAuditError("L3 preregistration locks changed.")

    access = payload.get("access_contract")
    if not isinstance(access, dict):
        raise MulticityPlanAuditError("L3 preregistration access contract is missing.")
    if access.get("preregistration_program_network_requests") != 0:
        raise MulticityPlanAuditError("L3 preregistration unexpectedly used the network.")
    for field in FALSE_PREREGISTRATION_ACCESS_FIELDS:
        if access.get(field) is not False:
            raise MulticityPlanAuditError(
                f"L3 preregistration access boundary changed: {field}"
            )

    next_gate = payload.get("next_gate")
    if not isinstance(next_gate, dict):
        raise MulticityPlanAuditError("L3 preregistration next gate is missing.")
    _require_exact_fields(
        next_gate,
        {
            "stage_id": (
                "authenticate_committed_l3_preregistration_and_authorize_geometry_audit"
            ),
            "preregistration_config_code_manifest_must_be_in_clean_head": True,
            "head_must_equal_origin_main": True,
            "tracked_only_transition_may_open_gshhg_archive_or_member": False,
            (
                "tracked_only_transition_may_open_geometry_support_target_model_or_result"
            ): False,
            "geometry_audit_stage_after_transition": (
                "target_blind_gshhg_l3_hierarchy_geometry_audit"
            ),
            "passing_geometry_audit_automatically_freezes_source_or_algorithm": False,
            "next_decision_after_passing_geometry_audit": (
                "separate_portable_water_distance_source_and_algorithm_freeze_decision"
            ),
            "eligible_support_predictor_model_target_or_protocol_authorized": False,
        },
        label="L3 preregistration next gate",
    )

    prerequisites = payload.get("prerequisites")
    if not isinstance(prerequisites, dict):
        raise MulticityPlanAuditError("L3 preregistration prerequisites are missing.")
    expected_plan = {
        "path": CANONICAL_PREDECESSOR_PLAN_PATH,
        "file_sha256": CANONICAL_PREDECESSOR_PLAN_FILE_SHA256,
        "commit_sha256": CANONICAL_PREDECESSOR_PLAN_COMMIT_SHA256,
        "state": "planning_ready",
        "planning_stage": (
            "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit"
        ),
        "next_safe_stage": "preregister_target_blind_gshhg_l3_hierarchy_audit",
    }
    plan_record = prerequisites.get("plan_readiness")
    if not isinstance(plan_record, dict):
        raise MulticityPlanAuditError("Predecessor plan record is missing.")
    for key, value in expected_plan.items():
        if plan_record.get(key) != value:
            raise MulticityPlanAuditError(
                f"Predecessor plan record changed: {key}"
            )
    if plan_record.get("authorized_now") != PREDECESSOR_AUTHORIZED_NOW:
        raise MulticityPlanAuditError(
            "Predecessor plan authorization record changed."
        )

    decision_record = prerequisites.get("freeze_decision")
    if not isinstance(decision_record, dict):
        raise MulticityPlanAuditError("Deferred freeze-decision record is missing.")
    _require_exact_fields(
        decision_record,
        {
            "path": CANONICAL_FREEZE_DECISION_PATH,
            "file_sha256": CANONICAL_FREEZE_DECISION_FILE_SHA256,
            "commit_sha256": CANONICAL_FREEZE_DECISION_COMMIT_SHA256,
            "state": "decision_complete_freeze_deferred",
            "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
        },
        label="Deferred freeze-decision prerequisite",
    )
    pilot_record = decision_record.get("gshhg_v2_pilot")
    if not isinstance(pilot_record, dict):
        raise MulticityPlanAuditError("GSHHG v2 pilot prerequisite is missing.")
    _require_exact_fields(
        pilot_record,
        {
            "path": CANONICAL_GSHHG_PILOT_PATH,
            "file_sha256": CANONICAL_GSHHG_PILOT_FILE_SHA256,
            "commit_sha256": CANONICAL_GSHHG_PILOT_COMMIT_SHA256,
            "state": "geometry_pilot_complete_source_not_frozen",
        },
        label="GSHHG v2 pilot prerequisite",
    )

    config_record = payload.get("preregistration_config")
    if not isinstance(config_record, dict):
        raise MulticityPlanAuditError("Preregistration config record is missing.")
    config_path = config_record.get("path")
    if not isinstance(config_path, str):
        raise MulticityPlanAuditError("Preregistration config path is invalid.")
    if sha256_file(project_root / config_path) != config_record.get("sha256"):
        raise MulticityPlanAuditError("Preregistration config bytes changed.")

    if payload.get("experiment_config_files") != plan.file_records:
        raise MulticityPlanAuditError(
            "Preregistration experiment configuration records changed."
        )
    code_runtime = payload.get("code_runtime")
    if not isinstance(code_runtime, dict) or not isinstance(
        code_runtime.get("files"), dict
    ):
        raise MulticityPlanAuditError("Preregistration code records are missing.")
    for relative_path, expected_sha256 in code_runtime["files"].items():
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_sha256, str)
            or sha256_file(project_root / relative_path) != expected_sha256
        ):
            raise MulticityPlanAuditError(
                f"Preregistration code bytes changed: {relative_path}"
            )


def _validate_tracked_pilot_and_decision(
    *,
    pilot: dict[str, Any],
    pilot_file_sha256: str,
    decision: dict[str, Any],
    decision_file_sha256: str,
) -> None:
    if pilot_file_sha256 != CANONICAL_GSHHG_PILOT_FILE_SHA256:
        raise MulticityPlanAuditError("Tracked GSHHG pilot bytes changed.")
    _require_exact_fields(
        pilot,
        {
            "state": "geometry_pilot_complete_source_not_frozen",
            "commit_sha256": CANONICAL_GSHHG_PILOT_COMMIT_SHA256,
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "predictor_build_authorized": False,
        },
        label="Tracked GSHHG pilot",
    )
    pilot_access = pilot.get("access_contract")
    if not isinstance(pilot_access, dict):
        raise MulticityPlanAuditError("Tracked GSHHG pilot access record is missing.")
    for field in (
        "eligible_land_grid_opened",
        "distance_feature_surface_computed",
        "tract_aggregation_performed",
        "predictor_values_computed",
        "predictor_construction_performed",
        "model_fit_performed",
        "model_predictions_computed",
        "landsat_thermal_values_read",
        "landsat_target_qa_values_read",
        "external_lst_values_read",
        "external_target_files_opened",
        "final_evaluation_outputs_opened",
    ):
        if pilot_access.get(field) is not False:
            raise MulticityPlanAuditError(
                f"Tracked GSHHG pilot access state changed: {field}"
            )

    if decision_file_sha256 != CANONICAL_FREEZE_DECISION_FILE_SHA256:
        raise MulticityPlanAuditError("Tracked freeze-decision bytes changed.")
    _require_exact_fields(
        decision,
        {
            "state": "decision_complete_freeze_deferred",
            "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
            "commit_sha256": CANONICAL_FREEZE_DECISION_COMMIT_SHA256,
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
        },
        label="Tracked freeze decision",
    )


def _continuation_planning_state(
    *,
    phoenix_geography: dict[str, Any] | None,
    phoenix_source_footprints: dict[str, Any] | None,
    water_distance_review: dict[str, Any] | None,
    gshhg_geometry_pilot: dict[str, Any] | None,
    water_distance_freeze_decision: dict[str, Any] | None = None,
    gshhg_l3_preregistration: dict[str, Any] | None = None,
) -> tuple[str, list[str], str, bool]:
    """Return the exact planning stage, blockers, next action, and review grant."""

    if phoenix_geography is None:
        return (
            "awaiting_phoenix_geography",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "implement_and_test_generic_census_place_tract_adapter",
                "complete_phoenix_metadata_only_pilot",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "phoenix_boundary_and_metadata_only_pilot",
            False,
        )
    if phoenix_source_footprints is None:
        return (
            "ready_for_phoenix_source_footprints",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "complete_phoenix_target_blind_source_footprint_discovery",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "phoenix_target_blind_source_footprint_discovery",
            False,
        )
    if water_distance_freeze_decision is not None:
        if water_distance_review is None:
            raise MulticityPlanAuditError(
                "Water-distance freeze decision exists without the source review."
            )
        if water_distance_review.get("state") != "review_complete_source_not_frozen":
            raise MulticityPlanAuditError(
                "Water-distance freeze decision has the wrong source-review state."
            )
        if gshhg_geometry_pilot is None:
            raise MulticityPlanAuditError(
                "Water-distance freeze decision exists without the GSHHG pilot."
            )
        if (
            gshhg_geometry_pilot.get("state")
            != "geometry_pilot_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "Water-distance freeze decision has the wrong GSHHG pilot state."
            )
    if water_distance_review is None:
        return (
            "phoenix_source_footprints_complete_metadata_only",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "freeze_exact_portable_predictor_source_and_calibration_contract",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "review_portable_water_distance_source_and_algorithm",
            False,
        )
    if water_distance_freeze_decision is not None:
        expected_decision = {
            "state": "decision_complete_freeze_deferred",
            "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
        }
        for key, expected in expected_decision.items():
            if (
                type(water_distance_freeze_decision.get(key)) is not type(expected)
                or water_distance_freeze_decision.get(key) != expected
            ):
                raise MulticityPlanAuditError(
                    "Water-distance decision may only record the authenticated "
                    f"deferred state; {key} changed."
                )
        next_gate = water_distance_freeze_decision.get("next_gate")
        if not isinstance(next_gate, dict) or next_gate.get("stage_id") != (
            "preregister_target_blind_gshhg_l3_hierarchy_audit"
        ):
            raise MulticityPlanAuditError("Water-distance decision next gate changed.")
        if gshhg_l3_preregistration is not None:
            if gshhg_l3_preregistration.get("state") != (
                "gshhg_l3_hierarchy_audit_preregistered_geometry_unopened"
            ):
                raise MulticityPlanAuditError(
                    "L3 hierarchy preregistration state changed."
                )
            return (
                "gshhg_l3_hierarchy_audit_preregistered_geometry_authorized_unopened",
                list(BLOCKERS_BEFORE_PREDICTOR_BUILD),
                "target_blind_gshhg_l3_hierarchy_geometry_audit",
                False,
            )
        return (
            "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit",
            list(BLOCKERS_BEFORE_PREDICTOR_BUILD),
            "preregister_target_blind_gshhg_l3_hierarchy_audit",
            False,
        )
    if gshhg_geometry_pilot is not None:
        if (
            gshhg_geometry_pilot.get("state")
            != "geometry_pilot_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "GSHHG geometry pilot state is not the expected non-frozen completion."
            )
        return (
            "gshhg_geometry_pilot_complete_source_not_frozen",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "freeze_exact_portable_predictor_source_and_calibration_contract",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "portable_water_distance_source_and_algorithm_freeze_decision",
            False,
        )
    return (
        "portable_water_distance_review_complete_source_not_frozen",
        [
            "complete_target_blind_gshhg_geometry_comparison",
            "freeze_portable_water_distance_source_and_algorithm",
            "freeze_exact_portable_predictor_source_and_calibration_contract",
            "promote_protocol_from_draft_with_separate_lock",
        ],
        "target_blind_gshhg_geometry_comparison",
        True,
    )


def _build_transition_payload(
    plan: MulticityPlan,
    *,
    workspace: MulticityWorkspace,
    predecessor: dict[str, Any],
    predecessor_bytes: int,
    preregistration: dict[str, Any],
    preregistration_file_sha256: str,
    gshhg_pilot: dict[str, Any],
    gshhg_pilot_file_sha256: str,
    freeze_decision: dict[str, Any],
    freeze_decision_file_sha256: str,
) -> dict[str, Any]:
    project_root = workspace.project_root
    (
        planning_stage,
        blockers,
        next_safe_stage,
        source_geometry_review_authorized,
    ) = _continuation_planning_state(
        phoenix_geography={"state": "pilot_complete_source_not_protocol_locked"},
        phoenix_source_footprints={
            "state": "complete_metadata_only_source_not_protocol_locked"
        },
        water_distance_review={"state": "review_complete_source_not_frozen"},
        gshhg_geometry_pilot=gshhg_pilot,
        water_distance_freeze_decision=freeze_decision,
        gshhg_l3_preregistration=preregistration,
    )
    if source_geometry_review_authorized:
        raise MulticityPlanAuditError(
            "Generic source-geometry review must remain closed."
        )

    prereg_access = preregistration["access_contract"]
    source_identity = preregistration[
        "source_identity_inherited_without_archive_access"
    ]
    outputs = preregistration["outputs"]
    pilot_member_hashes = gshhg_pilot["source_archive"]["required_member_sha256"]
    if any(
        path not in pilot_member_hashes for path in PERMITTED_GSHHG_MEMBER_PATHS[:8]
    ):
        raise MulticityPlanAuditError(
            "Tracked pilot does not bind every permitted L1/L2 member."
        )
    if tuple(source_identity["required_l3_members"]) != (
        PERMITTED_GSHHG_MEMBER_PATHS[8:]
    ):
        raise MulticityPlanAuditError(
            "Preregistered L3 member allowlist changed."
        )
    payload: dict[str, Any] = {
        "schema_version": PLAN_AUDIT_SCHEMA_VERSION,
        "algorithm_version": PLAN_AUDIT_ALGORITHM_VERSION,
        "state": "planning_ready",
        "planning_stage": planning_stage,
        "experiment_id": plan.experiment_id,
        "config_semantic_sha256": plan.semantic_sha256,
        "config_files": plan.file_records,
        "code_files": {
            relative: {
                "sha256": sha256_file(project_root / relative),
                "bytes": (project_root / relative).stat().st_size,
            }
            for relative in PLAN_AUDIT_CODE_PATHS
        },
        "transition": {
            "id": "authenticate_preregistered_gshhg_l3_audit_v1",
            "mode": "tracked_manifests_and_local_git_only",
            "canonical_preregistration_git_commit": (
                CANONICAL_PREREGISTRATION_GIT_COMMIT
            ),
            "predecessor_plan_readiness": {
                "path": CANONICAL_PREDECESSOR_PLAN_PATH,
                "source_git_commit": CANONICAL_PREREGISTRATION_GIT_COMMIT,
                "file_sha256": CANONICAL_PREDECESSOR_PLAN_FILE_SHA256,
                "bytes": predecessor_bytes,
                "commit_sha256": predecessor["commit_sha256"],
                "state": predecessor["state"],
                "planning_stage": predecessor["planning_stage"],
                "next_safe_stage": predecessor["next_safe_stage"],
            },
            "writer_precondition": {
                "branch": "main",
                "worktree_clean": True,
                "head_equals_local_origin_main": True,
                "all_transition_inputs_regular_git_tracked_blobs": True,
            },
            "authorization_effective_only_when": {
                "this_exact_plan_readiness_is_git_tracked": True,
                "branch_is_main": True,
                "worktree_is_clean": True,
                "head_equals_local_origin_main": True,
                "v6_check_only_passes_before_first_archive_or_member_read": True,
            },
        },
        "transition_access_contract": {
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
        },
        "cities": [
            {
                "id": city.id,
                "name": city.name,
                "role": city.role,
                "census_place_geoid": city.census_place_geoid,
                "target_values_status": city.target_values_status,
            }
            for city in plan.cities
        ],
        "locks": {
            "protocol_locked": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
            "portable_water_distance_source_locked": False,
            "portable_water_distance_algorithm_locked": False,
            "portable_water_distance_feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
        },
        "authorized_now": dict(AUTHORIZED_NOW),
        "geometry_audit_authorization_scope": {
            "stage_id": "target_blind_gshhg_l3_hierarchy_geometry_audit",
            "preregistration_id": preregistration["preregistration_id"],
            "exact_source_identity_only": True,
            "network_request_or_download_allowed": False,
            "local_archive_path": source_identity["archive_path"],
            "local_archive_sha256": source_identity["expected_archive_sha256"],
            "local_archive_bytes": source_identity["expected_archive_bytes"],
            "permitted_member_paths": list(PERMITTED_GSHHG_MEMBER_PATHS),
            "all_other_archive_members_allowed": False,
            "permitted_operations": [
                "authenticate_exact_local_gshhg_archive",
                "open_only_preregistered_l1_l2_l3_members",
                "audit_direct_parent_l3_hierarchy",
                "construct_source_only_exterior_linework",
                "compute_preregistered_real_l3_probes",
                "replay_four_existing_gshhg_points_without_census",
                "write_one_append_only_success_or_failure_manifest",
                "write_ignored_preregistered_diagnostic_table",
            ],
            "permitted_output_paths": {
                "success_manifest": outputs["success_manifest"],
                "failure_manifest": outputs["v1_failure_manifest"],
                "ignored_diagnostic_table": outputs["diagnostic_table"],
            },
            "gshhg_l4_member_or_geometry_read": False,
            "other_resolution_or_wdbii_member_read": False,
            "generic_source_geometry_review": False,
            "census_or_other_public_geometry_read": False,
            "eligible_land_support_read": False,
            "distance_surface_or_tract_aggregation": False,
            "predictor_model_prediction_target_or_result_access": False,
            "source_or_algorithm_freeze_created_by_audit": False,
            "feature_names_frozen_by_audit": False,
            "geometry_export_or_redistribution": False,
            "next_decision_after_passing_audit": (
                "separate_portable_water_distance_source_and_algorithm_freeze_decision"
            ),
        },
        "workspace": {
            "raw_root": workspace.raw_root.relative_to(project_root).as_posix(),
            "interim_root": workspace.interim_root.relative_to(
                project_root
            ).as_posix(),
            "processed_root": workspace.processed_root.relative_to(
                project_root
            ).as_posix(),
            "manifest_root": workspace.manifest_root.relative_to(
                project_root
            ).as_posix(),
            "report_root": workspace.report_root.relative_to(project_root).as_posix(),
            "export_root": workspace.export_root.relative_to(project_root).as_posix(),
        },
        "gshhg_geometry_pilot": {
            "path": CANONICAL_GSHHG_PILOT_PATH,
            "file_sha256": gshhg_pilot_file_sha256,
            "commit_sha256": gshhg_pilot["commit_sha256"],
            "state": gshhg_pilot["state"],
            "authentication_mode": "exact_tracked_manifest_only",
            "archive_or_member_reopened": False,
        },
        "portable_water_distance_freeze_decision": {
            "path": CANONICAL_FREEZE_DECISION_PATH,
            "file_sha256": freeze_decision_file_sha256,
            "commit_sha256": freeze_decision["commit_sha256"],
            "state": freeze_decision["state"],
            "outcome": freeze_decision["outcome"],
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "authentication_mode": "exact_tracked_manifest_only",
            "archive_or_member_reopened": False,
        },
        "gshhg_l3_hierarchy_audit_preregistration": {
            "path": CANONICAL_PREREGISTRATION_PATH,
            "file_sha256": preregistration_file_sha256,
            "commit_sha256": preregistration["commit_sha256"],
            "state": preregistration["state"],
            "preregistration_id": preregistration["preregistration_id"],
            "access_contract": {
                "network_requests": prereg_access[
                    "preregistration_program_network_requests"
                ],
                **{
                    field: prereg_access[field]
                    for field in FALSE_PREREGISTRATION_ACCESS_FIELDS
                },
            },
            "next_gate": preregistration["next_gate"],
        },
        "blockers_before_predictor_build": blockers,
        "next_safe_stage": next_safe_stage,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _expected_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2).encode("utf-8")


def _write_exact_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_bytes(_expected_json_bytes(payload))
    temporary.replace(destination)


def _publish_or_authenticate(
    payload: dict[str, Any],
    *,
    destination: Path,
    write: bool,
) -> None:
    expected_bytes = _expected_json_bytes(payload)
    if write:
        if not destination.is_file():
            raise MulticityPlanAuditError(
                "Canonical predecessor PLAN_READINESS is missing."
            )
        current, current_sha256 = _committed_json(destination)
        if current_sha256 == CANONICAL_PREDECESSOR_PLAN_FILE_SHA256:
            _validate_predecessor_plan(current, file_sha256=current_sha256)
            _write_exact_json(payload, destination)
        elif destination.read_bytes() != expected_bytes:
            raise MulticityPlanAuditError(
                "Refusing to replace a PLAN_READINESS that is neither the exact "
                "v5 predecessor nor the byte-identical v6 transition."
            )
    else:
        if not destination.is_file() or destination.read_bytes() != expected_bytes:
            raise MulticityPlanAuditError(
                f"Readiness record is stale or changed: {destination}"
            )
        committed, _ = _committed_json(destination)
        if committed != payload:
            raise MulticityPlanAuditError(
                f"Readiness record is semantically stale: {destination}"
            )


def audit_multicity_plan(
    config_path: str | Path,
    *,
    output_path: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Authorize only the preregistered L3 audit from tracked evidence."""

    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    project_root = workspace.project_root
    destination = (
        workspace.manifest_root / "PLAN_READINESS.json"
        if output_path is None
        else Path(output_path)
    )
    if not destination.is_absolute():
        destination = project_root / destination
    destination = destination.absolute()
    expected_destination = (
        project_root / CANONICAL_PREDECESSOR_PLAN_PATH
    ).absolute()
    if destination != expected_destination:
        raise MulticityPlanAuditError(
            "The tracked transition may only write canonical PLAN_READINESS.json."
        )

    required_paths = tuple(
        dict.fromkeys(
            (
                *(path.relative_to(project_root).as_posix() for path in plan.source_files),
                *PLAN_AUDIT_CODE_PATHS,
                CANONICAL_PREDECESSOR_PLAN_PATH,
                CANONICAL_GSHHG_PILOT_PATH,
                CANONICAL_FREEZE_DECISION_PATH,
                CANONICAL_PREREGISTRATION_PATH,
            )
        )
    )
    preflight_head = _git_preflight(
        project_root,
        required_paths=required_paths,
    )

    predecessor, predecessor_sha256, predecessor_bytes = _historical_committed_json(
        project_root,
        git_commit=CANONICAL_PREREGISTRATION_GIT_COMMIT,
        relative_path=CANONICAL_PREDECESSOR_PLAN_PATH,
    )
    _validate_predecessor_plan(predecessor, file_sha256=predecessor_sha256)

    preregistration_path = project_root / CANONICAL_PREREGISTRATION_PATH
    preregistration, preregistration_sha256 = _committed_json(
        preregistration_path
    )
    _validate_preregistration_contract(
        preregistration,
        file_sha256=preregistration_sha256,
        plan=plan,
        project_root=project_root,
    )

    historical_preregistration, historical_preregistration_sha256, _ = (
        _historical_committed_json(
            project_root,
            git_commit=CANONICAL_PREREGISTRATION_GIT_COMMIT,
            relative_path=CANONICAL_PREREGISTRATION_PATH,
        )
    )
    if (
        historical_preregistration_sha256
        != CANONICAL_PREREGISTRATION_FILE_SHA256
        or historical_preregistration != preregistration
    ):
        raise MulticityPlanAuditError(
            "Current preregistration differs from its canonical Git commit."
        )

    pilot, pilot_sha256 = _committed_json(
        project_root / CANONICAL_GSHHG_PILOT_PATH
    )
    decision, decision_sha256 = _committed_json(
        project_root / CANONICAL_FREEZE_DECISION_PATH
    )
    _validate_tracked_pilot_and_decision(
        pilot=pilot,
        pilot_file_sha256=pilot_sha256,
        decision=decision,
        decision_file_sha256=decision_sha256,
    )

    payload = _build_transition_payload(
        plan,
        workspace=workspace,
        predecessor=predecessor,
        predecessor_bytes=predecessor_bytes,
        preregistration=preregistration,
        preregistration_file_sha256=preregistration_sha256,
        gshhg_pilot=pilot,
        gshhg_pilot_file_sha256=pilot_sha256,
        freeze_decision=decision,
        freeze_decision_file_sha256=decision_sha256,
    )
    _git_preflight(
        project_root,
        required_paths=required_paths,
        expected_head=preflight_head,
    )
    _publish_or_authenticate(
        payload,
        destination=destination,
        write=write,
    )
    return payload
