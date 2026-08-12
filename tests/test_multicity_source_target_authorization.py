from __future__ import annotations

import json
from pathlib import Path

import pytest

from la_heat.multicity.source_target_authorization import (
    SourceTargetAuthorizationError,
    authenticate_source_target_authorization,
    build_source_target_authorization,
    create_source_target_authorization,
)
from la_heat.multicity.target_authorization import (
    authenticate_target_execution_authorization,
)
from la_heat.multicity.target_transaction import SOURCE_LANE

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_build_is_la_only_and_does_not_create_values_opened(tmp_path: Path) -> None:
    marker = PROJECT_ROOT / ".tmp" / tmp_path.name / "VALUES_OPENED.json"
    payload = build_source_target_authorization(PROJECT_ROOT, values_opened_path=marker)

    assert payload["lane"] == SOURCE_LANE
    assert payload["city_ids"] == ["los_angeles_ca"]
    assert payload["years"] == [2020, 2021, 2022, 2023, 2024]
    assert payload["permissions"] == {
        "source_target_build_authorized": True,
        "external_target_build_authorized": False,
        "model_fit_authorized": False,
        "model_score_authorized": False,
        "external_targets_unlocked": False,
    }
    assert marker.exists() is False


def test_create_is_append_only_checkable_and_engine_compatible(tmp_path: Path) -> None:
    destination = tmp_path / "SOURCE_TARGET_AUTHORIZATION.json"
    canonical_marker = (
        PROJECT_ROOT
        / "data/interim/multicity/targets/values_opened/"
        "los_angeles_2020_2024_source/VALUES_OPENED.json"
    )
    marker_before = (
        canonical_marker.read_bytes() if canonical_marker.exists() else None
    )
    created = create_source_target_authorization(PROJECT_ROOT, destination)
    checked = authenticate_source_target_authorization(PROJECT_ROOT, destination)
    engine_auth = authenticate_target_execution_authorization(
        PROJECT_ROOT,
        destination,
        expected_lane=SOURCE_LANE,
        expected_plan_commit_sha256=created["plan_commit_sha256"],
    )

    assert checked == created
    assert engine_auth.city_ids == ("los_angeles_ca",)
    marker_after = (
        canonical_marker.read_bytes() if canonical_marker.exists() else None
    )
    assert marker_after == marker_before
    with pytest.raises(SourceTargetAuthorizationError, match="already exists"):
        create_source_target_authorization(PROJECT_ROOT, destination)


def test_authentication_rejects_tampering(tmp_path: Path) -> None:
    destination = tmp_path / "SOURCE_TARGET_AUTHORIZATION.json"
    create_source_target_authorization(PROJECT_ROOT, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["permissions"]["external_targets_unlocked"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceTargetAuthorizationError, match="commit is invalid"):
        authenticate_source_target_authorization(PROJECT_ROOT, destination)


def test_build_rejects_protocol_lock_drift(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json"
    lock = tmp_path / "PROTOCOL_MODEL_LOCK.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["next_safe_stage"] = "something_else"
    payload.pop("commit_sha256")
    from la_heat.provenance import canonical_sha256

    payload["commit_sha256"] = canonical_sha256(payload)
    lock.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceTargetAuthorizationError, match="transition"):
        build_source_target_authorization(PROJECT_ROOT, protocol_lock_path=lock)
