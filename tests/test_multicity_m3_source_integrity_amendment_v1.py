from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import m3_source_integrity_amendment_v1 as integrity
from la_heat.multicity.m3_source_development_runtime import RunnerSettings
from la_heat.provenance import canonical_sha256


def _settings(root: Path) -> RunnerSettings:
    paths = {
        "config_path": root / "runner.toml",
        "protocol_lock": root / "protocol.json",
        "amendment": root / "acquisition.json",
        "inventory": root / "inventory.json",
        "authorization": root / "source_qa_authorization.json",
    }
    for path in paths.values():
        path.write_text("fixture\n", encoding="utf-8")
    cache = root / "cache"
    cache.mkdir()
    (cache / integrity.PLAN_FILENAME).write_text("physical plan\n", encoding="utf-8")
    return RunnerSettings(
        root=root,
        **paths,
        database=root / "tasks.sqlite",
        control=root / "control.json",
        status=root / "status.json",
        log=root / "worker.log",
        cache_root=cache,
        qa_output_root=root / "qa",
        completion_root=root / "completion",
        download_workers=2,
        compute_workers=1,
        window_size=512,
        network_timeout_seconds=20,
        network_recheck_seconds=20,
        lease_seconds=900,
        heartbeat_seconds=30,
        retry_base_seconds=5,
        retry_max_seconds=300,
    )


def _row(
    city_id: str,
    city_index: int,
    scene_count: int,
    ordinal: int,
) -> dict[str, Any]:
    target_date = str(date(2020, 1, 1) + timedelta(days=city_index))
    overpass_id = f"fixture-{city_id}-{city_index:03d}"
    scenes = [f"SCENE_{city_id}_{city_index:03d}_{number}" for number in range(scene_count)]
    if city_id == integrity.HOUSTON_CITY and ordinal == 133:
        target_date = "2022-05-14"
        overpass_id = integrity.HOUSTON_OVERPASS
        scenes = [integrity.HOUSTON_RETAINED_SCENE, integrity.HOUSTON_UNAVAILABLE_SCENE]
    if city_id == integrity.CHICAGO_CITY and ordinal == 244:
        target_date = "2022-07-27"
        overpass_id = integrity.CHICAGO_OVERPASS
        scenes = [integrity.CHICAGO_UNAVAILABLE_SCENE]
    row: dict[str, Any] = {
        "city_id": city_id,
        "target_date": target_date,
        "overpass_id": overpass_id,
        "platform": "landsat-9",
        "scene_ids": scenes,
        "wrs_path_rows": [f"001/{number:03d}" for number in range(len(scenes))],
        "acquired_utc_min": f"{target_date}T16:00:00+00:00",
        "acquired_utc_max": f"{target_date}T16:00:10+00:00",
        "union_city_coverage_fraction": 1.0,
        "source_lock_sha256": canonical_sha256([overpass_id, scenes]),
        "grid_sha256": canonical_sha256([city_id, "grid"]),
        "target_context_commit_sha256": canonical_sha256([city_id, "context"]),
        "inventory_mode": "fixture",
    }
    row["relationship_sha256"] = canonical_sha256(row)
    row["ordinal"] = ordinal
    return row


def _inventory() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ordinal = 0
    layout = (
        ("los_angeles_ca", 90, lambda index: 2 if index < 87 else 1),
        ("phoenix_az", 22, lambda _index: 2),
        ("houston_tx", 102, lambda index: 2 if index < 98 else 1),
        ("chicago_il", 104, lambda _index: 1),
    )
    for city_id, count, scene_count in layout:
        for index in range(count):
            ordinal += 1
            rows.append(_row(city_id, index, scene_count(index), ordinal))
    return {
        "commit_sha256": "c" * 64,
        "source_city_ids": list(integrity.SOURCE_CITY_IDS),
        "overpass_count": 318,
        "overpasses": rows,
    }


def _wire_fixture(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    settings = _settings(root)
    protocol = {"commit_sha256": "a" * 64}
    acquisition = {"commit_sha256": "b" * 64}
    inventory = _inventory()
    incident = {
        "commit_sha256": "d" * 64,
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "scene_plan_commit_sha256": "e" * 64,
        "runtime_task_plan": {"task_plan_sha256": "f" * 64},
        "affected_assets": [
            {
                "city_id": city_id,
                "scene_id": scene_id,
                "asset": asset,
                "cache_absence": {
                    "content_commit_present": False,
                    "output_present": False,
                },
                "bad_blob_safe_fingerprint": {
                    "classification": "html_error_payload_not_tiff",
                    "first_four_bytes_hex": "3c21444f",
                },
            }
            for city_id, scene_id, asset in integrity.EXPECTED_INCIDENT_ASSETS
        ],
    }
    physical_plan = {"commit_sha256": "e" * 64}
    incident_file = root / "incident.json"
    incident_file.write_text("fixture incident\n", encoding="utf-8")
    parents = (
        settings,
        protocol,
        acquisition,
        inventory,
        incident,
        physical_plan,
        incident_file,
    )
    monkeypatch.setattr(
        integrity,
        "_authenticate_parents",
        lambda _root, _incident_path: parents,
    )
    monkeypatch.setattr(
        integrity,
        "_houston_coverage_evidence",
        lambda _root, _inventory: {
            "retained_scene_id": integrity.HOUSTON_RETAINED_SCENE,
            "overpass_id": integrity.HOUSTON_OVERPASS,
            "platform": "landsat-9",
            "target_date": "2022-05-14",
            "wrs_path_rows": ["025/039"],
            "acquired_utc_min": "2022-05-14T16:50:14+00:00",
            "acquired_utc_max": "2022-05-14T16:50:14+00:00",
            "union_city_coverage_fraction": 0.9999,
            "minimum_required_fraction": 0.98,
            "passes_gate": True,
            "logical_source_lock_sha256": "9" * 64,
            "feature_geometry_sha256": "8" * 64,
            "assets_excluded_raw_metadata_page": {"sha256": "7" * 64},
            "public_geography_manifest": {"commit_sha256": "6" * 64},
            "public_city_boundary": {"sha256": "5" * 64},
            "analysis_crs": "EPSG:5070",
            "grouping_implementation": "fixture",
        },
    )
    monkeypatch.setattr(integrity, "CODE_PATHS", ("integrity_code.py",))
    (root / "integrity_code.py").write_text("fixture code\n", encoding="utf-8")


def test_create_check_overlay_counts_and_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _wire_fixture(monkeypatch, root)
    amendment_path = root / "amendment.json"
    overlay_path = root / "overlay.json"

    amendment = integrity.create_source_integrity_availability_amendment(
        root,
        amendment_path,
        incident_path=root / "incident.json",
        overlay_path=overlay_path,
    )
    assert amendment["required_logical_totals"] == {
        "overpasses": 317,
        "city_dates": 317,
        "scene_references": 523,
    }
    assert amendment["integrity_availability_rule"][
        "trigger_may_use_qa_target_predictor_or_model_outcomes"
    ] is False
    assert amendment["physical_cache_contract"][
        "scene_plan_and_cache_remain_immutable"
    ] is True
    assert all(
        value is False
        for key, value in amendment["permissions"].items()
        if any(word in key for word in ("blind", "qa_values", "target_values", "predictor", "fit"))
    )
    with pytest.raises(integrity.M3SourceIntegrityAmendmentError, match="already exists"):
        integrity.create_source_integrity_availability_amendment(
            root,
            amendment_path,
            incident_path=root / "incident.json",
            overlay_path=overlay_path,
        )

    overlay = integrity.create_source_integrity_logical_overlay(
        root,
        overlay_path,
        amendment_path=amendment_path,
    )
    assert overlay["logical_totals"] == {
        "overpasses": 317,
        "city_dates": 317,
        "scene_references": 523,
        "unique_scenes": 523,
    }
    assert [row["ordinal"] for row in overlay["logical_overpasses"]] == list(
        range(1, 318)
    )
    houston = next(
        row
        for row in overlay["logical_overpasses"]
        if row["overpass_id"] == integrity.HOUSTON_OVERPASS
    )
    assert houston["scene_ids"] == [integrity.HOUSTON_RETAINED_SCENE]
    assert not any(
        row["overpass_id"] == integrity.CHICAGO_OVERPASS
        for row in overlay["logical_overpasses"]
    )
    assert overlay["old_inventory_scene_plan_cache_or_queue_modified"] is False
    with pytest.raises(integrity.M3SourceIntegrityAmendmentError, match="already exists"):
        integrity.create_source_integrity_logical_overlay(
            root,
            overlay_path,
            amendment_path=amendment_path,
        )


def test_tampered_amendment_and_overlay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _wire_fixture(monkeypatch, root)
    amendment_path = root / "amendment.json"
    overlay_path = root / "overlay.json"
    integrity.create_source_integrity_availability_amendment(
        root,
        amendment_path,
        incident_path=root / "incident.json",
        overlay_path=overlay_path,
    )
    integrity.create_source_integrity_logical_overlay(
        root,
        overlay_path,
        amendment_path=amendment_path,
    )

    original_overlay = overlay_path.read_bytes()
    tampered_overlay = json.loads(original_overlay)
    tampered_overlay["logical_totals"]["overpasses"] = 316
    overlay_path.write_text(json.dumps(tampered_overlay), encoding="utf-8")
    with pytest.raises(integrity.M3SourceIntegrityAmendmentError, match="commit"):
        integrity.authenticate_source_integrity_logical_overlay(
            root,
            overlay_path,
            amendment_path=amendment_path,
        )
    overlay_path.write_bytes(original_overlay)

    original_amendment = amendment_path.read_bytes()
    tampered_amendment = json.loads(original_amendment)
    tampered_amendment["required_logical_totals"]["city_dates"] = 316
    amendment_path.write_text(json.dumps(tampered_amendment), encoding="utf-8")
    with pytest.raises(integrity.M3SourceIntegrityAmendmentError, match="commit"):
        integrity.authenticate_source_integrity_availability_amendment(
            root, amendment_path
        )
