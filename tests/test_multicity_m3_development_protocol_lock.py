from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from la_heat.multicity.m3_development_protocol_lock import (
    CONFIG_PATH,
    CORE_PATH,
    HISTORICAL_STATES,
    MODULE_PATH,
    POSTHOC_QA_PATH,
    SCRIPT_PATH,
    M3DevelopmentProtocolLockError,
    authenticate_m3_development_protocol_lock,
    build_m3_development_protocol_lock,
    create_m3_development_protocol_lock,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_lock_inputs(destination: Path) -> None:
    config_text = (PROJECT_ROOT / CONFIG_PATH).read_text(encoding="utf-8")
    # Collect the historical paths without importing a second TOML parser in tests.
    import tomllib

    config = tomllib.loads(config_text)
    paths = [CONFIG_PATH, CORE_PATH, MODULE_PATH, SCRIPT_PATH, POSTHOC_QA_PATH]
    paths.extend(Path(config["anchors"]["historical"][name]) for name in HISTORICAL_STATES)
    paths.append(Path(config["anchors"]["feasibility"]["path"]))
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)


def test_build_is_value_blind_and_freezes_candidates_not_winner() -> None:
    payload = build_m3_development_protocol_lock(PROJECT_ROOT)
    assert payload["state"] == "locked_before_new_source_analysis"
    assert payload["protocol_locked"] is True
    assert payload["candidate_space_locked"] is True
    assert payload["model_spec_locked"] is False
    assert payload["selected_model_winner_locked"] is False
    assert all(value is False for value in payload["permissions"].values())
    assert payload["access_audit"] == {
        "predictor_tables_read_by_this_lock": False,
        "source_target_or_qa_values_read_by_this_lock": False,
        "blind_test_landsat_asset_hrefs_read_by_this_lock": False,
        "blind_test_thermal_or_target_qa_values_read_by_this_lock": False,
        "blind_test_target_tables_read_by_this_lock": False,
        "model_fit_prediction_or_scoring_performed_by_this_lock": False,
        "values_opened_marker_created_by_this_lock": False,
    }
    assert payload["next_safe_stage"] == (
        "separately_authorize_source_only_qa_rebuild_and_nested_loso"
    )


def test_create_is_append_only_and_check_rebuilds_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_lock_inputs(tmp_path)
    import la_heat.multicity.m3_development_protocol_lock as lock_module

    feasibility = json_load(tmp_path / lock_module.CONFIG_PATH, tmp_path)
    monkeypatch.setattr(lock_module, "authenticate_feasibility_audit", lambda _root: feasibility)
    created = create_m3_development_protocol_lock(tmp_path)
    assert authenticate_m3_development_protocol_lock(tmp_path) == created
    with pytest.raises(M3DevelopmentProtocolLockError, match="already exists"):
        create_m3_development_protocol_lock(tmp_path)


def test_authentication_rejects_locked_code_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_lock_inputs(tmp_path)
    import la_heat.multicity.m3_development_protocol_lock as lock_module

    feasibility = json_load(tmp_path / lock_module.CONFIG_PATH, tmp_path)
    monkeypatch.setattr(lock_module, "authenticate_feasibility_audit", lambda _root: feasibility)
    create_m3_development_protocol_lock(tmp_path)
    with (tmp_path / CORE_PATH).open("a", encoding="utf-8") as handle:
        handle.write("\n# drift\n")
    with pytest.raises(M3DevelopmentProtocolLockError, match="no longer reproduces"):
        authenticate_m3_development_protocol_lock(tmp_path)


def test_config_cannot_grant_execution_permission(tmp_path: Path) -> None:
    _copy_lock_inputs(tmp_path)
    path = tmp_path / CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        "fit_models = false", "fit_models = true", 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(M3DevelopmentProtocolLockError, match="permission"):
        build_m3_development_protocol_lock(tmp_path)


def test_config_seed_tamper_fails_closed(tmp_path: Path) -> None:
    _copy_lock_inputs(tmp_path)
    path = tmp_path / CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        "model = 20260813", "model = 20260812", 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(M3DevelopmentProtocolLockError, match="seeds"):
        build_m3_development_protocol_lock(tmp_path)


def test_config_qa_candidate_tamper_fails_closed(tmp_path: Path) -> None:
    _copy_lock_inputs(tmp_path)
    path = tmp_path / CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        'id = "3k"', 'id = "2k"', 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(M3DevelopmentProtocolLockError, match="ST_QA candidate"):
        build_m3_development_protocol_lock(tmp_path)


def json_load(config_path: Path, root: Path) -> dict[str, object]:
    import json
    import tomllib

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    path = root / config["anchors"]["feasibility"]["path"]
    return json.loads(path.read_text(encoding="utf-8"))


def test_lock_commit_is_canonical() -> None:
    payload = build_m3_development_protocol_lock(PROJECT_ROOT)
    unsigned = dict(payload)
    commit = unsigned.pop("commit_sha256")
    from la_heat.provenance import canonical_sha256

    assert commit == canonical_sha256(unsigned)
