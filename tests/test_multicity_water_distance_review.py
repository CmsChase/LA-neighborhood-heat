from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.plan_audit import _continuation_planning_state
from la_heat.multicity.water_distance_review import (
    COMPLETE_STATE,
    EXPECTED_ACCESS_CONTRACT,
    WaterDistanceReviewError,
    _read_review_config,
    _require_closed_locks,
    audit_water_distance_review,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "configs" / "multicity" / "water_distance_review_v1.toml"
PLAN = ROOT / "configs" / "multicity" / "experiment.toml"


def test_review_authenticates_existing_source_without_unlocking_computation(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "WATER_DISTANCE_REVIEW.json"

    payload = audit_water_distance_review(CONFIG, output_path=destination)

    assert payload["state"] == COMPLETE_STATE
    assert payload["review_outcome"] == (
        "conditional_census_benchmark_pending_global_geometry_pilot"
    )
    assert payload["source_lock_created"] is False
    assert payload["algorithm_lock_created"] is False
    assert payload["predictor_build_authorized"] is False
    assert payload["access_contract"] == EXPECTED_ACCESS_CONTRACT
    assert payload["source_audit"]["row_count"] == 4248
    assert payload["source_audit"]["name_counts"]["Great Lakes"] == 377
    assert payload["source_audit"]["distance_values_computed"] is False
    assert payload["next_geometry_pilot"]["target_or_qa_access_allowed"] is False

    verified = audit_water_distance_review(
        CONFIG,
        output_path=destination,
        write=False,
    )
    assert verified["commit_sha256"] == payload["commit_sha256"]


def test_review_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    destination = tmp_path / "WATER_DISTANCE_REVIEW.json"
    audit_water_distance_review(CONFIG, output_path=destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["source_lock_created"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WaterDistanceReviewError, match="internal commit"):
        audit_water_distance_review(
            CONFIG,
            output_path=destination,
            write=False,
        )


def test_review_config_rejects_silent_source_resolution(tmp_path: Path) -> None:
    payload = CONFIG.read_text(encoding="utf-8")
    assert payload.count("source_scope_is_resolved = false") == 1
    changed = payload.replace(
        "source_scope_is_resolved = false",
        "source_scope_is_resolved = true",
    )
    path = tmp_path / "water_distance_review.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(WaterDistanceReviewError, match="silently resolve"):
        _read_review_config(path)


def test_review_config_rejects_weakened_geometry_pilot_gate(
    tmp_path: Path,
) -> None:
    payload = CONFIG.read_text(encoding="utf-8")
    assert payload.count("require_strtree_bruteforce_parity = true") == 1
    changed = payload.replace(
        "require_strtree_bruteforce_parity = true",
        "require_strtree_bruteforce_parity = false",
    )
    path = tmp_path / "water_distance_review.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(WaterDistanceReviewError, match="geometry-pilot gate"):
        _read_review_config(path)


def test_review_rejects_any_early_predictor_unlock() -> None:
    plan = load_multicity_plan(PLAN)
    raw = deepcopy(plan.raw)
    raw["locks"]["allow_predictor_construction"] = True

    with pytest.raises(WaterDistanceReviewError, match="every computation"):
        _require_closed_locks(raw)


def test_review_keeps_global_and_census_fallback_names_distinct() -> None:
    _, config = _read_review_config(CONFIG)
    algorithm = config["algorithm_recommendation"]

    assert algorithm["proposed_global_feature_names"] == [
        "ocean_great_lakes_distance_mean_km",
        "ocean_great_lakes_distance_p10_km",
    ]
    assert algorithm["proposed_census_fallback_feature_names"] == [
        "us_census_qualifying_shoreline_distance_mean_km",
        "us_census_qualifying_shoreline_distance_p10_km",
    ]
    assert algorithm["forbid_phase1_alias"] is True


def test_planning_authorizes_source_geometry_only_after_verified_review() -> None:
    before_review = _continuation_planning_state(
        phoenix_geography={"state": "complete"},
        phoenix_source_footprints={"state": "complete"},
        water_distance_review=None,
    )
    after_review = _continuation_planning_state(
        phoenix_geography={"state": "complete"},
        phoenix_source_footprints={"state": "complete"},
        water_distance_review={"state": COMPLETE_STATE},
    )

    assert before_review[2] == "review_portable_water_distance_source_and_algorithm"
    assert before_review[3] is False
    assert after_review[2] == "target_blind_gshhg_geometry_comparison"
    assert after_review[3] is True
