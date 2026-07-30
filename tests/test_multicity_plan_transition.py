from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.plan_audit as plan_audit
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.plan_audit import (
    AUTHORIZED_NOW,
    BLOCKERS_BEFORE_PREDICTOR_BUILD,
    CANONICAL_FREEZE_DECISION_PATH,
    CANONICAL_GSHHG_PILOT_PATH,
    CANONICAL_PREDECESSOR_PLAN_PATH,
    CANONICAL_PREREGISTRATION_FILE_SHA256,
    CANONICAL_PREREGISTRATION_GIT_COMMIT,
    CANONICAL_PREREGISTRATION_PATH,
    FALSE_PREREGISTRATION_ACCESS_FIELDS,
    PERMITTED_GSHHG_MEMBER_PATHS,
    PREDECESSOR_AUTHORIZED_NOW,
    MulticityPlanAuditError,
    _build_transition_payload,
    _committed_json,
    _continuation_planning_state,
    _git_preflight,
    _historical_committed_json,
    _publish_or_authenticate,
    _validate_predecessor_plan,
    _validate_preregistration_contract,
    _validate_tracked_pilot_and_decision,
)
from la_heat.multicity.workspace import MulticityWorkspace

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "configs" / "multicity" / "experiment.toml"
MODULE = ROOT / "src" / "la_heat" / "multicity" / "plan_audit.py"


def _transition_inputs() -> tuple[
    object,
    MulticityWorkspace,
    dict[str, object],
    int,
    dict[str, object],
    str,
    dict[str, object],
    str,
    dict[str, object],
    str,
]:
    plan = load_multicity_plan(CONFIG)
    workspace = MulticityWorkspace.from_plan(plan)
    predecessor, predecessor_sha256, predecessor_bytes = (
        _historical_committed_json(
            ROOT,
            git_commit=CANONICAL_PREREGISTRATION_GIT_COMMIT,
            relative_path=CANONICAL_PREDECESSOR_PLAN_PATH,
        )
    )
    _validate_predecessor_plan(
        predecessor,
        file_sha256=predecessor_sha256,
    )
    preregistration, preregistration_sha256 = _committed_json(
        ROOT / CANONICAL_PREREGISTRATION_PATH
    )
    pilot, pilot_sha256 = _committed_json(ROOT / CANONICAL_GSHHG_PILOT_PATH)
    decision, decision_sha256 = _committed_json(
        ROOT / CANONICAL_FREEZE_DECISION_PATH
    )
    return (
        plan,
        workspace,
        predecessor,
        predecessor_bytes,
        preregistration,
        preregistration_sha256,
        pilot,
        pilot_sha256,
        decision,
        decision_sha256,
    )


def _transition_payload() -> dict[str, object]:
    (
        plan,
        workspace,
        predecessor,
        predecessor_bytes,
        preregistration,
        preregistration_sha256,
        pilot,
        pilot_sha256,
        decision,
        decision_sha256,
    ) = _transition_inputs()
    return _build_transition_payload(
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


def test_transition_authenticates_exact_preregistration_and_tracked_manifests() -> None:
    (
        plan,
        _workspace,
        _predecessor,
        _predecessor_bytes,
        preregistration,
        preregistration_sha256,
        pilot,
        pilot_sha256,
        decision,
        decision_sha256,
    ) = _transition_inputs()

    _validate_preregistration_contract(
        preregistration,
        file_sha256=preregistration_sha256,
        plan=plan,
        project_root=ROOT,
    )
    _validate_tracked_pilot_and_decision(
        pilot=pilot,
        pilot_file_sha256=pilot_sha256,
        decision=decision,
        decision_file_sha256=decision_sha256,
    )

    assert preregistration_sha256 == CANONICAL_PREREGISTRATION_FILE_SHA256


def test_transition_rejects_resigned_preregistration_with_changed_access() -> None:
    (
        plan,
        _workspace,
        _predecessor,
        _predecessor_bytes,
        preregistration,
        _preregistration_sha256,
        _pilot,
        _pilot_sha256,
        _decision,
        _decision_sha256,
    ) = _transition_inputs()
    changed = deepcopy(preregistration)
    changed["access_contract"]["gshhg_l3_member_opened"] = True

    with pytest.raises(
        MulticityPlanAuditError,
        match="access boundary changed",
    ):
        _validate_preregistration_contract(
            changed,
            file_sha256=CANONICAL_PREREGISTRATION_FILE_SHA256,
            plan=plan,
            project_root=ROOT,
        )


def test_transition_advances_only_to_preregistered_l3_geometry_audit() -> None:
    stage, blockers, next_stage, generic_review = _continuation_planning_state(
        phoenix_geography={"state": "pilot_complete_source_not_protocol_locked"},
        phoenix_source_footprints={
            "state": "complete_metadata_only_source_not_protocol_locked"
        },
        water_distance_review={"state": "review_complete_source_not_frozen"},
        gshhg_geometry_pilot={
            "state": "geometry_pilot_complete_source_not_frozen"
        },
        water_distance_freeze_decision={
            "state": "decision_complete_freeze_deferred",
            "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "next_gate": {
                "stage_id": "preregister_target_blind_gshhg_l3_hierarchy_audit"
            },
        },
        gshhg_l3_preregistration={
            "state": "gshhg_l3_hierarchy_audit_preregistered_geometry_unopened"
        },
    )

    assert stage == (
        "gshhg_l3_hierarchy_audit_preregistered_geometry_authorized_unopened"
    )
    assert blockers == list(BLOCKERS_BEFORE_PREDICTOR_BUILD)
    assert next_stage == "target_blind_gshhg_l3_hierarchy_geometry_audit"
    assert generic_review is False


def test_transition_opens_only_the_narrow_geometry_permission() -> None:
    payload = _transition_payload()

    assert payload["authorized_now"] == AUTHORIZED_NOW
    changed = {
        key
        for key in AUTHORIZED_NOW
        if AUTHORIZED_NOW[key] != PREDECESSOR_AUTHORIZED_NOW[key]
    }
    assert changed == {
        "target_blind_gshhg_l3_hierarchy_preregistration",
        "target_blind_gshhg_l3_hierarchy_geometry_read",
    }
    assert AUTHORIZED_NOW["target_blind_gshhg_l3_hierarchy_preregistration"] is False
    assert AUTHORIZED_NOW["target_blind_gshhg_l3_hierarchy_geometry_read"] is True
    assert payload["geometry_audit_authorization_scope"][
        "eligible_land_support_read"
    ] is False
    assert payload["geometry_audit_authorization_scope"][
        "predictor_model_prediction_target_or_result_access"
    ] is False
    scope = payload["geometry_audit_authorization_scope"]
    assert scope["network_request_or_download_allowed"] is False
    assert scope["permitted_member_paths"] == list(PERMITTED_GSHHG_MEMBER_PATHS)
    assert scope["all_other_archive_members_allowed"] is False
    assert scope["geometry_export_or_redistribution"] is False
    assert set(scope["permitted_output_paths"]) == {
        "success_manifest",
        "failure_manifest",
        "ignored_diagnostic_table",
    }


def test_transition_access_ledger_records_no_archive_geometry_or_result_read() -> None:
    payload = _transition_payload()
    access = payload["transition_access_contract"]

    assert access["network_requests"] == 0
    assert access["untracked_path_names_checked_by_git_status"] is True
    assert access["untracked_file_contents_opened"] is False
    assert access["ignored_path_names_requested_from_git"] is False
    assert access["archive_geometry_data_or_result_bytes_opened"] is False
    for key, value in access.items():
        if key.endswith(("_opened", "_computed", "_performed")):
            assert value is False, key
    preregistration = payload["gshhg_l3_hierarchy_audit_preregistration"]
    for field in FALSE_PREREGISTRATION_ACCESS_FIELDS:
        assert preregistration["access_contract"][field] is False


def test_transition_module_has_no_legacy_deep_auditor_or_path_enumeration() -> None:
    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "verify_city_geography",
        "verify_city_source_footprints",
        "audit_water_distance_review",
        "audit_gshhg_geometry_pilot",
        "audit_portable_water_distance_freeze_decision",
        ".glob(",
        ".rglob(",
        ".iterdir(",
        "ZipFile",
        "read_parquet",
        "read_file",
    ):
        assert forbidden not in source


def test_transition_writer_replaces_only_exact_predecessor_and_is_idempotent(
    tmp_path: Path,
) -> None:
    payload = _transition_payload()
    predecessor_bytes = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            f"{CANONICAL_PREREGISTRATION_GIT_COMMIT}:{CANONICAL_PREDECESSOR_PLAN_PATH}",
        ],
        check=True,
        capture_output=True,
    ).stdout
    destination = tmp_path / "PLAN_READINESS.json"
    destination.write_bytes(predecessor_bytes)

    _publish_or_authenticate(payload, destination=destination, write=True)
    first = destination.read_bytes()
    _publish_or_authenticate(payload, destination=destination, write=True)

    assert destination.read_bytes() == first
    _publish_or_authenticate(payload, destination=destination, write=False)

    destination.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    with pytest.raises(MulticityPlanAuditError):
        _publish_or_authenticate(payload, destination=destination, write=True)


def test_git_preflight_requires_clean_synced_regular_tracked_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "audit@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Audit Test"],
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "freeze"],
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-ref",
            "refs/remotes/origin/main",
            head,
        ],
        check=True,
    )
    monkeypatch.setattr(
        plan_audit,
        "CANONICAL_PREREGISTRATION_GIT_COMMIT",
        head,
    )

    assert _git_preflight(tmp_path, required_paths=("tracked.txt",)) == head
    with pytest.raises(MulticityPlanAuditError, match="HEAD changed"):
        _git_preflight(
            tmp_path,
            required_paths=("tracked.txt",),
            expected_head="0" * 40,
        )

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(MulticityPlanAuditError, match="clean working tree"):
        _git_preflight(tmp_path, required_paths=("tracked.txt",))

    tracked.write_text("frozen\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-index",
            "--skip-worktree",
            "tracked.txt",
        ],
        check=True,
    )
    tracked.write_text("hidden dirty\n", encoding="utf-8")
    with pytest.raises(
        MulticityPlanAuditError,
        match="index visibility flag",
    ):
        _git_preflight(tmp_path, required_paths=("tracked.txt",))
