from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from la_heat.multicity.m3_source_acquisition_amendment import (
    ACCESS_AUDIT,
    AMENDMENT_PATH,
    CONFIG_PATH,
    MODULE_PATH,
    PERMISSIONS,
    SCRIPT_PATH,
    M3SourceAcquisitionAmendmentError,
    authenticate_m3_source_acquisition_amendment,
    build_m3_source_acquisition_amendment,
    create_m3_source_acquisition_amendment,
)
from la_heat.provenance import canonical_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def preview() -> dict[str, object]:
    return build_m3_source_acquisition_amendment(PROJECT_ROOT)


def _copy_inputs(destination: Path) -> dict[str, object]:
    config = tomllib.loads((PROJECT_ROOT / CONFIG_PATH).read_text(encoding="utf-8"))
    paths = [CONFIG_PATH, MODULE_PATH, SCRIPT_PATH]
    paths.extend(Path(anchor["path"]) for anchor in config["anchors"].values())
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    lock_path = destination / config["anchors"]["m3_protocol_lock"]["path"]
    return json.loads(lock_path.read_text(encoding="utf-8"))


def _patch_protocol_auth(
    monkeypatch: pytest.MonkeyPatch,
    protocol: dict[str, object],
) -> None:
    import la_heat.multicity.m3_source_acquisition_amendment as module

    monkeypatch.setattr(
        module,
        "authenticate_m3_development_protocol_lock",
        lambda _root, _path: protocol,
    )


def test_preflight_fails_fast_before_current_inventory_prefetch(
    preview: dict[str, object],
) -> None:
    preflight = preview["historical_preflight"]
    assert isinstance(preflight, dict)
    assert preflight["current_city_support"] == [
        {
            "city_id": "phoenix_az",
            "planned_date_count": 22,
            "none_usable_date_count": 21,
        },
        {
            "city_id": "houston_tx",
            "planned_date_count": 21,
            "none_usable_date_count": 4,
        },
        {
            "city_id": "chicago_il",
            "planned_date_count": 21,
            "none_usable_date_count": 3,
        },
    ]
    assert preflight["failing_city_ids"] == ["houston_tx", "chicago_il"]
    assert preflight["monotonicity_proof"]["candidate_subset_of_none"] == {
        "3k": True,
        "4k": True,
        "6k": True,
    }
    assert preflight["any_current_joint_configuration_can_pass"] is False
    assert preflight["decision"] == "current_inventory_ineligible_expand_before_prefetch"
    assert preflight["current_inventory_prefetch_authorized"] is False


def test_amendment_freezes_fixed_nonadaptive_source_expansion(
    preview: dict[str, object],
) -> None:
    contract = preview["amendment_contract"]
    assert isinstance(contract, dict)
    assert contract["retained_inventory"] == {
        "los_angeles_ca": {
            "mode": "retain_authenticated_existing_inventory_exactly",
            "start_date": "2020-05-01",
            "end_date": "2024-10-31",
            "expected_existing_overpass_count": 90,
            "expected_existing_scene_count": 177,
        },
        "phoenix_az": {
            "mode": "retain_authenticated_existing_inventory_exactly",
            "start_date": "2025-05-01",
            "end_date": "2025-10-31",
            "expected_existing_overpass_count": 22,
            "expected_existing_scene_count": 44,
        },
    }
    assert contract["expanded_inventory"] == {
        city_id: {
            "mode": "replace_existing_2025_slice_with_complete_fixed_window_query",
            "start_date": "2020-05-01",
            "end_date": "2025-10-31",
            "include_all_qualifying_overpasses": True,
        }
        for city_id in ("houston_tx", "chicago_il")
    }
    stop = contract["stop_rule"]
    assert stop["adaptive_year_extension_after_failure"] is False
    assert stop["required_none_usable_dates_per_source_city"] == 8


def test_amendment_grants_no_execution_or_value_permission(
    preview: dict[str, object],
) -> None:
    assert preview["execution_authorized"] is False
    assert preview["permissions"] == PERMISSIONS
    assert all(value is False for value in PERMISSIONS.values())
    assert preview["access_audit"] == ACCESS_AUDIT
    assert ACCESS_AUDIT["authenticated_committed_historical_json_read"] is True
    assert all(
        value is False
        for key, value in ACCESS_AUDIT.items()
        if key != "authenticated_committed_historical_json_read"
    )


def test_create_is_append_only_and_authentication_rebuilds_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = _copy_inputs(tmp_path)
    _patch_protocol_auth(monkeypatch, protocol)
    created = create_m3_source_acquisition_amendment(tmp_path)
    assert authenticate_m3_source_acquisition_amendment(tmp_path) == created
    with pytest.raises(M3SourceAcquisitionAmendmentError, match="already exists"):
        create_m3_source_acquisition_amendment(tmp_path)


def test_config_cannot_expand_adaptively(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        "adaptive_year_extension_after_failure = false",
        "adaptive_year_extension_after_failure = true",
        1,
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(M3SourceAcquisitionAmendmentError, match="stop rule"):
        build_m3_source_acquisition_amendment(tmp_path)


def test_config_cannot_grant_network_permission(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        "query_public_metadata = false",
        "query_public_metadata = true",
        1,
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(M3SourceAcquisitionAmendmentError, match="permission"):
        build_m3_source_acquisition_amendment(tmp_path)


def test_historical_support_file_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = _copy_inputs(tmp_path)
    _patch_protocol_auth(monkeypatch, protocol)
    config = tomllib.loads((tmp_path / CONFIG_PATH).read_text(encoding="utf-8"))
    path = tmp_path / config["anchors"]["historical_support"]["path"]
    text = path.read_text(encoding="utf-8").replace(
        '"usable_date_count": 4', '"usable_date_count": 8', 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(
        M3SourceAcquisitionAmendmentError,
        match="commit is invalid|file changed",
    ):
        build_m3_source_acquisition_amendment(tmp_path)


def test_preview_commit_is_canonical_and_matches_formal_manifest_when_present(
    preview: dict[str, object],
) -> None:
    unsigned = dict(preview)
    commit = unsigned.pop("commit_sha256")
    assert commit == canonical_sha256(unsigned)
    formal = PROJECT_ROOT / AMENDMENT_PATH
    if formal.exists():
        assert json.loads(formal.read_text(encoding="utf-8")) == preview
