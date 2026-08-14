from __future__ import annotations

from pathlib import Path

import pytest

from la_heat.multicity import m3_source_qa_authorization as authorization
from la_heat.multicity.m3_source_development_runtime import SOURCE_CITY_IDS, RunnerSettings


def _settings(root: Path) -> RunnerSettings:
    return RunnerSettings(
        root=root,
        config_path=root / "configs/multicity/m3_source_development_runner.toml",
        protocol_lock=root / "protocol.json",
        amendment=root / "amendment.json",
        inventory=root / "inventory.json",
        authorization=root / authorization.AUTHORIZATION_PATH,
        database=root / "runtime/tasks.sqlite",
        control=root / "runtime/control.json",
        status=root / "runtime/status.json",
        log=root / "runtime/log.txt",
        cache_root=root / "cache",
        qa_output_root=root / "qa",
        completion_root=root / "completion",
        download_workers=2,
        compute_workers=1,
        window_size=512,
        network_timeout_seconds=20,
        network_recheck_seconds=20,
        lease_seconds=30,
        heartbeat_seconds=1,
        retry_base_seconds=1,
        retry_max_seconds=2,
    )


def _fixture(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    for relative in authorization.CODE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")
    settings = _settings(root)
    for path in (settings.protocol_lock, settings.amendment, settings.inventory):
        path.write_text("{}\n", encoding="utf-8")
    protocol = {"commit_sha256": "a" * 64}
    amendment = {"commit_sha256": "b" * 64}
    overpasses = [
        {
            "city_id": city_id,
            "overpass_id": f"overpass-{index}",
            "target_date": "2025-07-01",
            "scene_ids": [f"scene-{index}"],
        }
        for index, city_id in enumerate(SOURCE_CITY_IDS)
    ]
    inventory: dict[str, object] = {
        "commit_sha256": "c" * 64,
        "source_city_ids": list(SOURCE_CITY_IDS),
        "overpass_count": 4,
        "overpasses": overpasses,
    }
    monkeypatch.setattr(authorization, "load_runner_settings", lambda _: settings)
    monkeypatch.setattr(
        authorization,
        "authenticate_m3_development_protocol_lock",
        lambda *_: protocol,
    )
    monkeypatch.setattr(
        authorization,
        "authenticate_m3_source_acquisition_amendment",
        lambda *_: amendment,
    )
    monkeypatch.setattr(
        authorization,
        "authenticate_expanded_inventory",
        lambda *_: inventory,
    )
    return inventory


def test_authorization_is_two_phase_source_only_and_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch)
    output = tmp_path / authorization.AUTHORIZATION_PATH
    payload = authorization.create_m3_source_qa_authorization(tmp_path, output)

    assert payload["state"] == "source_qa_two_phase_execution_authorized"
    assert payload["source_city_ids"] == list(SOURCE_CITY_IDS)
    assert payload["blind_test_target_access_authorized"] is False
    assert payload["online_predownload_permissions"][
        "read_exact_five_source_landsat_assets"
    ] is True
    assert payload["offline_qa_permissions"]["network_or_href_hydration_allowed"] is False
    assert payload["model_fit_or_selection_authorized"] is False
    assert authorization.authenticate_m3_source_qa_authorization(tmp_path, output) == payload
    with pytest.raises(authorization.M3SourceQAAuthorizationError, match="already exists"):
        authorization.create_m3_source_qa_authorization(tmp_path, output)


def test_authorization_rejects_blind_city_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _fixture(tmp_path, monkeypatch)
    rows = inventory["overpasses"]
    assert isinstance(rows, list)
    rows[0]["city_id"] = "seattle_wa"
    with pytest.raises(authorization.M3SourceQAAuthorizationError):
        authorization.build_m3_source_qa_authorization(tmp_path)
