from __future__ import annotations

import json
from pathlib import Path

import pytest

import la_heat.multicity.evaluation_protocol_lock as lock_module
from la_heat.multicity.evaluation_protocol_lock import (
    EvaluationProtocolLockError,
    authenticate_protocol_model_lock,
    build_protocol_model_lock,
    create_protocol_model_lock,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_build_freezes_protocol_but_keeps_all_execution_permissions_closed() -> None:
    payload = build_protocol_model_lock(PROJECT_ROOT)

    assert payload["protocol_locked"] is True
    assert payload["model_spec_locked"] is True
    assert payload["fitted_model_artifacts_locked"] is False
    assert all(value is False for value in payload["permissions"].values())
    assert payload["cohorts"]["training_rows"] == 73_432
    assert payload["cohorts"]["calibration_rows"] == 25_208
    assert payload["cohorts"]["external_rows"] == 38_301
    assert len(payload["model_contract"]["b1_feature_order"]) == 23
    assert len(payload["model_contract"]["m2_feature_order"]) == 46
    assert payload["evaluation_contract"]["bootstrap_iterations"] == 10_000
    assert payload["access_audit"]["target_tables_read_by_this_lock"] is False


def test_create_is_append_only_and_check_only_reauthenticates(tmp_path: Path) -> None:
    destination = tmp_path / "PROTOCOL_MODEL_LOCK.json"
    created = create_protocol_model_lock(PROJECT_ROOT, destination)
    checked = authenticate_protocol_model_lock(PROJECT_ROOT, destination)

    assert checked == created
    with pytest.raises(EvaluationProtocolLockError, match="already exists"):
        create_protocol_model_lock(PROJECT_ROOT, destination)


def test_authentication_rejects_tampered_lock(tmp_path: Path) -> None:
    destination = tmp_path / "PROTOCOL_MODEL_LOCK.json"
    create_protocol_model_lock(PROJECT_ROOT, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["permissions"]["model_fit_authorized"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationProtocolLockError, match="commit is invalid"):
        authenticate_protocol_model_lock(PROJECT_ROOT, destination)


def test_authentication_rejects_bound_file_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "PROTOCOL_MODEL_LOCK.json"
    create_protocol_model_lock(PROJECT_ROOT, destination)
    real_sha = lock_module.sha256_file

    def changed_sha(path: Path) -> str:
        value = real_sha(path)
        if path.name == "TRAINING_PREFLIGHT.json":
            return "0" * 64
        return value

    monkeypatch.setattr(lock_module, "sha256_file", changed_sha)
    with pytest.raises(EvaluationProtocolLockError, match="drifted"):
        authenticate_protocol_model_lock(PROJECT_ROOT, destination)
