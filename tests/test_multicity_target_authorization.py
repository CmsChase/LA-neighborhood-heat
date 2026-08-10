from __future__ import annotations

import json
from pathlib import Path

import pytest

from la_heat.multicity.target_authorization import (
    AUTHORIZED_STATE,
    TargetAuthorizationError,
    ValuesAccessGate,
    authenticate_target_execution_authorization,
)
from la_heat.multicity.target_transaction import EXTERNAL_LANE, SOURCE_LANE
from la_heat.provenance import canonical_sha256


def _write_authorization(root: Path, *, lane: str) -> tuple[Path, str]:
    plan_commit = "a" * 64
    external = lane == EXTERNAL_LANE
    payload = {
        "schema_version": 1,
        "state": AUTHORIZED_STATE,
        "lane": lane,
        "city_ids": (
            ["phoenix_az", "houston_tx", "chicago_il"]
            if external
            else ["los_angeles_ca"]
        ),
        "claim_id": "one-claim",
        "plan_commit_sha256": plan_commit,
        "target_config_sha256": "b" * 64,
        "asset_href_hydration_authorized": True,
        "target_values_open_authorized": True,
        "single_global_claim": external,
        "external_prediction_commit_sha256": "c" * 64 if external else None,
        "values_opened_marker": f"data/interim/{lane}/VALUES_OPENED.json",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    path = root / "authorization.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path, plan_commit


@pytest.mark.parametrize("lane", [SOURCE_LANE, EXTERNAL_LANE])
def test_gate_creates_and_reauthenticates_same_claim_marker(
    tmp_path: Path,
    lane: str,
) -> None:
    path, plan_commit = _write_authorization(tmp_path, lane=lane)
    authorization = authenticate_target_execution_authorization(
        tmp_path,
        path,
        expected_lane=lane,
        expected_plan_commit_sha256=plan_commit,
    )
    gate = ValuesAccessGate(authorization)

    assert authorization.values_opened_marker.exists() is False
    gate.before_first_value_access()
    assert authorization.values_opened_marker.exists() is True
    ValuesAccessGate(authorization).before_first_value_access()


def test_external_authorization_rejects_partial_city_claim(tmp_path: Path) -> None:
    path, plan_commit = _write_authorization(tmp_path, lane=EXTERNAL_LANE)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["city_ids"] = ["phoenix_az"]
    payload.pop("commit_sha256")
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TargetAuthorizationError, match="complete three-city"):
        authenticate_target_execution_authorization(
            tmp_path,
            path,
            expected_lane=EXTERNAL_LANE,
            expected_plan_commit_sha256=plan_commit,
        )


def test_source_authorization_rejects_external_prediction_binding(tmp_path: Path) -> None:
    path, plan_commit = _write_authorization(tmp_path, lane=SOURCE_LANE)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_prediction_commit_sha256"] = "c" * 64
    payload.pop("commit_sha256")
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TargetAuthorizationError, match="cannot bind"):
        authenticate_target_execution_authorization(
            tmp_path,
            path,
            expected_lane=SOURCE_LANE,
            expected_plan_commit_sha256=plan_commit,
        )
