from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.plan_audit import (
    MulticityPlanAuditError,
    _continuation_planning_state,
)
from la_heat.multicity.portable_water_distance_freeze import (
    COMPLETE_STATE,
    EXPECTED_ACCESS_CONTRACT,
    PortableWaterDistanceFreezeError,
    _read_config,
    _require_plan_locks_closed,
    _validate_pilot_closure_gap,
    audit_portable_water_distance_freeze_decision,
)

ROOT = Path(__file__).parents[1]
CONFIG = (
    ROOT
    / "configs"
    / "multicity"
    / "portable_water_distance_freeze_decision_v1.toml"
)
PLAN = ROOT / "configs" / "multicity" / "experiment.toml"
PILOT = (
    ROOT
    / "manifests"
    / "multicity"
    / "reviews"
    / "portable_water_distance"
    / "GSHHG_GEOMETRY_PILOT.json"
)


def test_deferred_decision_generates_and_reauthenticates(tmp_path: Path) -> None:
    destination = tmp_path / "WATER_DISTANCE_FREEZE_DECISION.json"

    payload = audit_portable_water_distance_freeze_decision(
        CONFIG,
        output_path=destination,
    )

    assert payload["state"] == COMPLETE_STATE
    assert payload["outcome"] == "deferred_pending_gshhg_l3_hierarchy_contract"
    assert payload["source_lock_created"] is False
    assert payload["algorithm_lock_created"] is False
    assert payload["feature_names_frozen"] is False
    assert payload["predictor_build_authorized"] is False
    assert payload["access_contract"] == EXPECTED_ACCESS_CONTRACT
    assert payload["next_gate"]["stage_id"] == (
        "preregister_target_blind_gshhg_l3_hierarchy_audit"
    )

    verified = audit_portable_water_distance_freeze_decision(
        CONFIG,
        output_path=destination,
        write=False,
    )
    assert verified["commit_sha256"] == payload["commit_sha256"]


def test_decision_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    destination = tmp_path / "WATER_DISTANCE_FREEZE_DECISION.json"
    audit_portable_water_distance_freeze_decision(
        CONFIG,
        output_path=destination,
    )
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["source_lock_created"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="internal commit",
    ):
        audit_portable_water_distance_freeze_decision(
            CONFIG,
            output_path=destination,
            write=False,
        )


def test_decision_refuses_to_overwrite_different_valid_manifest(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "WATER_DISTANCE_FREEZE_DECISION.json"
    payload = audit_portable_water_distance_freeze_decision(
        CONFIG,
        output_path=destination,
    )
    changed = deepcopy(payload)
    changed["decision_scope"] = "changed"
    body = {key: value for key, value in changed.items() if key != "commit_sha256"}
    from la_heat.provenance import canonical_sha256

    changed["commit_sha256"] = canonical_sha256(body)
    destination.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="already exists with different bytes",
    ):
        audit_portable_water_distance_freeze_decision(
            CONFIG,
            output_path=destination,
        )


def test_config_rejects_claim_that_l3_gap_is_resolved(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    assert raw.count("l3_hierarchy_gap_resolved = false") == 1
    changed = raw.replace(
        "l3_hierarchy_gap_resolved = false",
        "l3_hierarchy_gap_resolved = true",
    )
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="l3_hierarchy_gap_resolved",
    ):
        _read_config(path)


def test_config_rejects_boolean_integer_type_confusion(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    assert raw.count("source_lock_created = false") == 1
    changed = raw.replace("source_lock_created = false", "source_lock_created = 0")
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="lock",
    ):
        _read_config(path)


def test_config_rejects_prerequisite_hash_substitution(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    original = (
        "56aeb8ced370f4648b5256875223302ee189d3d5fa452fbc346e4d8dd80b7e56"
    )
    assert raw.count(original) == 1
    changed = raw.replace(original, "0" * 64)
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="Prerequisite paths, hashes",
    ):
        _read_config(path)


def test_config_rejects_algorithm_semantic_change(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    original = (
        "L1 ocean exteriors plus the three selected L2 connected-water "
        "exteriors plus every directly parented L3 island exterior"
    )
    assert raw.count(original) == 1
    changed = raw.replace(original, "L1 only")
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="candidate_algorithm",
    ):
        _read_config(path)


def test_config_rejects_plan_semantic_substitution(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    original = (
        "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
    )
    assert raw.count(original) == 1
    changed = raw.replace(original, "0" * 64)
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="identity or deferred outcome",
    ):
        _read_config(path)


def test_config_rejects_official_reference_substitution(tmp_path: Path) -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    original = "https://www.gnu.org/licenses/lgpl-3.0.html"
    assert raw.count(original) == 1
    changed = raw.replace(original, "https://example.com/license")
    path = tmp_path / "decision.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="license_record.license_reference",
    ):
        _read_config(path)


def test_deferred_decision_rejects_open_experiment_lock() -> None:
    plan = load_multicity_plan(PLAN)
    raw = deepcopy(plan.raw)
    raw["locks"]["allow_predictor_construction"] = True

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="allow_predictor_construction",
    ):
        _require_plan_locks_closed(raw)


def test_deferred_decision_rejects_failed_prerequisite_gate() -> None:
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    pilot["numerical_gates"]["strtree_bruteforce_all_passed"] = False

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="strtree_bruteforce_all_passed",
    ):
        _validate_pilot_closure_gap(pilot)


def test_deferred_decision_requires_l3_to_remain_unresolved() -> None:
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    pilot["source_layers"]["great_lakes_identity"][
        "l3_island_shores_included"
    ] = True

    with pytest.raises(
        PortableWaterDistanceFreezeError,
        match="unresolved L3 exclusion",
    ):
        _validate_pilot_closure_gap(pilot)


def test_plan_advances_only_to_l3_preregistration_after_deferred_decision() -> None:
    stage, blockers, next_stage, source_geometry_authorized = (
        _continuation_planning_state(
            phoenix_geography={"state": "complete"},
            phoenix_source_footprints={"state": "complete"},
            water_distance_review={
                "state": "review_complete_source_not_frozen"
            },
            gshhg_geometry_pilot={
                "state": "geometry_pilot_complete_source_not_frozen"
            },
            water_distance_freeze_decision={
                "state": COMPLETE_STATE,
                "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
                "source_lock_created": False,
                "algorithm_lock_created": False,
                "feature_names_frozen": False,
                "predictor_build_authorized": False,
                "protocol_lock_created": False,
                "next_gate": {
                    "stage_id": (
                        "preregister_target_blind_gshhg_l3_hierarchy_audit"
                    )
                },
            },
        )
    )

    assert stage == (
        "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit"
    )
    assert blockers[0] == (
        "resolve_and_audit_gshhg_l3_lake_island_shoreline_contract"
    )
    assert next_stage == "preregister_target_blind_gshhg_l3_hierarchy_audit"
    assert source_geometry_authorized is False


def test_plan_rejects_decision_that_claims_source_freeze() -> None:
    with pytest.raises(MulticityPlanAuditError, match="source_lock_created"):
        _continuation_planning_state(
            phoenix_geography={"state": "complete"},
            phoenix_source_footprints={"state": "complete"},
            water_distance_review={
                "state": "review_complete_source_not_frozen"
            },
            gshhg_geometry_pilot={
                "state": "geometry_pilot_complete_source_not_frozen"
            },
            water_distance_freeze_decision={
                "state": COMPLETE_STATE,
                "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
                "source_lock_created": True,
                "algorithm_lock_created": False,
                "feature_names_frozen": False,
                "predictor_build_authorized": False,
                "protocol_lock_created": False,
                "next_gate": {
                    "stage_id": (
                        "preregister_target_blind_gshhg_l3_hierarchy_audit"
                    )
                },
            },
        )


def test_plan_rejects_decision_without_source_review() -> None:
    with pytest.raises(MulticityPlanAuditError, match="without the source review"):
        _continuation_planning_state(
            phoenix_geography={"state": "complete"},
            phoenix_source_footprints={"state": "complete"},
            water_distance_review=None,
            gshhg_geometry_pilot={
                "state": "geometry_pilot_complete_source_not_frozen"
            },
            water_distance_freeze_decision={},
        )


def test_plan_rejects_decision_with_wrong_pilot_state() -> None:
    with pytest.raises(MulticityPlanAuditError, match="wrong GSHHG pilot state"):
        _continuation_planning_state(
            phoenix_geography={"state": "complete"},
            phoenix_source_footprints={"state": "complete"},
            water_distance_review={
                "state": "review_complete_source_not_frozen"
            },
            gshhg_geometry_pilot={"state": "source_frozen"},
            water_distance_freeze_decision={},
        )
