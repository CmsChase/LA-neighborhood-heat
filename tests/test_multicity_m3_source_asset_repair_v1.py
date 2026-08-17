from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import m3_source_asset_repair_v1 as repair
from la_heat.multicity.m3_source_development_runtime import RunnerSettings
from la_heat.provenance import canonical_sha256


def _settings(root: Path) -> RunnerSettings:
    cache = root / "cache"
    paths = {
        "config_path": root / "runner.toml",
        "protocol_lock": root / "protocol.json",
        "amendment": root / "amendment.json",
        "inventory": root / "inventory.json",
        "authorization": root / "old_authorization.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    cache.mkdir(parents=True)
    (cache / repair.PLAN_FILENAME).write_text("fixture plan\n", encoding="utf-8")
    return RunnerSettings(
        root=root,
        **paths,
        database=root / "runtime.sqlite3",
        control=root / "control.json",
        status=root / "status.json",
        log=root / "worker.log",
        cache_root=cache,
        qa_output_root=root / "qa",
        completion_root=root / "completions",
        download_workers=2,
        compute_workers=1,
        window_size=512,
        network_timeout_seconds=30,
        network_recheck_seconds=30,
        lease_seconds=60,
        heartbeat_seconds=10,
        retry_base_seconds=1,
        retry_max_seconds=60,
    )


def _wire_frozen_fixture(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    specs: tuple[repair.RepairAsset, ...],
) -> tuple[RunnerSettings, dict[str, Any], list[dict[str, Any]]]:
    settings = _settings(root)
    commits = [character * 64 for character in "abcde"]
    protocol = {"commit_sha256": commits[0]}
    amendment = {"commit_sha256": commits[1]}
    inventory = {"commit_sha256": commits[2]}
    old_authorization = {"commit_sha256": commits[3]}
    plan = {
        "commit_sha256": commits[4],
        "required_assets": ["lwir11", "qa_radsat"],
        "scenes": [
            {"city_id": spec.city_id, "scene_id": spec.scene_id}
            for spec in (specs[0], specs[2])
        ],
        "grids": {
            "chicago_il": {"sha256": "1" * 64},
            "houston_tx": {"sha256": "2" * 64},
        },
    }
    queue = {
        "run_id": "m3-source-development-v1-fixture",
        "schema_version": 1,
        "task_plan_sha256": "3" * 64,
        "task_count": 3,
    }
    plan_rows = [
        (
            repair._task_id(spec),
            "download_asset",
            json.dumps(
                {
                    "asset": spec.asset,
                    "city_id": spec.city_id,
                    "scene_id": spec.scene_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        for spec in specs
    ]
    snapshots = []
    for index, (task_id, _kind, payload_json) in enumerate(plan_rows, start=1):
        updated = 1_700_000_000.0 + index
        snapshots.append(
            {
                "task_id": task_id,
                "kind": "download_asset",
                "payload_json_sha256": hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest(),
                "status": "pending",
                "attempt": 40 + index,
                "claim_generation": 40 + index,
                "error_type": "RasterioIOError",
                "updated_at_epoch_seconds": updated,
                "updated_at_utc": datetime.fromtimestamp(updated, tz=UTC).isoformat(),
                "lease_owner": None,
                "lease_expires_at": None,
            }
        )
    foundations = (
        settings,
        protocol,
        amendment,
        inventory,
        old_authorization,
        plan,
    )
    monkeypatch.setattr(repair, "REPAIR_ASSETS", specs)
    monkeypatch.setattr(repair, "CODE_PATHS", ("repair_code.py",))
    (root / "repair_code.py").write_text("fixture code\n", encoding="utf-8")
    monkeypatch.setattr(repair, "_authenticated_foundations", lambda _root: foundations)
    monkeypatch.setattr(
        repair,
        "_authenticated_components",
        lambda _root, *, require_paused: (*foundations, queue),
    )
    monkeypatch.setattr(
        repair,
        "_expected_task_plan",
        lambda _inventory: (plan_rows, queue["task_plan_sha256"]),
    )
    monkeypatch.setattr(
        repair,
        "_queue_snapshot",
        lambda _settings, _inventory, *, require_paused, incident_task_ids=(): (
            queue,
            snapshots if incident_task_ids else [],
        ),
    )
    monkeypatch.setattr(
        repair,
        "_queue_binding",
        lambda _settings, _inventory, *, require_paused: queue,
    )
    monkeypatch.setattr(repair, "load_runner_settings", lambda _root: settings)
    monkeypatch.setattr(repair, "load_scene_plan", lambda _cache_root: plan)
    return settings, plan, snapshots


def test_incident_authorization_and_local_repair_are_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source_root = tmp_path / "official"
    contents: dict[tuple[str, str], bytes] = {}
    specs = []
    for index, spec in enumerate(repair.REPAIR_ASSETS, start=1):
        content = b"II*\x00" + f"synthetic-source-{index}".encode()
        contents[(spec.scene_id, spec.asset)] = content
        path = source_root / spec.usgs_product_id / spec.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        specs.append(
            replace(
                spec,
                official_md5=hashlib.md5(content, usedforsecurity=False).hexdigest(),
            )
        )
    frozen_specs = tuple(specs)
    settings, plan, snapshots = _wire_frozen_fixture(
        monkeypatch,
        root,
        frozen_specs,
    )
    incident_path = root / "incident.json"
    authorization_path = root / "authorization.json"
    completion_path = root / "completion.json"

    incident = repair.create_source_asset_repair_incident(root, incident_path)
    assert incident["runtime_task_plan"]["task_plan_sha256"] == "3" * 64
    assert [
        row["task_snapshot"]["attempt"] for row in incident["affected_assets"]
    ] == [row["attempt"] for row in snapshots]
    assert all(
        row["cache_absence"]["content_commit_present"] is False
        and row["bad_blob_safe_fingerprint"]["first_four_bytes_hex"] == "3c21444f"
        for row in incident["affected_assets"]
    )
    serialized_incident = json.dumps(incident).lower()
    assert "https://" not in serialized_incident
    assert "sig=" not in serialized_incident
    with pytest.raises(repair.M3SourceAssetRepairError, match="already exists"):
        repair.create_source_asset_repair_incident(root, incident_path)

    authorization = repair.create_source_asset_repair_authorization(
        root,
        authorization_path,
        incident_path=incident_path,
        completion_path=completion_path,
    )
    assert (
        authorization["source_asset_repair_incident_commit_sha256"]
        == incident["commit_sha256"]
    )
    assert authorization["repair_asset_set_sha256"] == canonical_sha256(
        authorization["repair_assets"]
    )

    local_cache_calls: list[tuple[str, str, Path]] = []
    commits = {
        (spec.scene_id, spec.asset): {
            "scene_id": spec.scene_id,
            "asset": spec.asset,
            "commit_sha256": canonical_sha256([spec.scene_id, spec.asset, "cached"]),
        }
        for spec in frozen_specs
    }

    def fake_cache_asset(
        _cache_root: Path,
        observed_plan: dict[str, Any],
        scene_id: str,
        asset: str,
        href: str,
        *,
        before_value_access: Any,
        signer: Any,
    ) -> dict[str, Any]:
        assert observed_plan is plan
        before_value_access()
        if href != "AUTHORIZED_REPAIR_CONTENT_ALREADY_CACHED":
            path = Path(signer(href))
            assert path.read_bytes() == contents[(scene_id, asset)]
            local_cache_calls.append((scene_id, asset, path))
        return commits[(scene_id, asset)]

    monkeypatch.setattr(repair, "cache_asset_from_href", fake_cache_asset)
    third = source_root / frozen_specs[2].usgs_product_id / frozen_specs[2].filename
    third.write_bytes(b"II*\x00wrong")
    with pytest.raises(repair.M3SourceAssetRepairError, match="MD5 mismatch"):
        repair.run_source_asset_repair(
            root,
            source_mode="official_original_directory",
            source_directory=source_root,
            authorization_path=authorization_path,
            completion_path=completion_path,
        )
    assert local_cache_calls == []
    assert not completion_path.exists()

    third.write_bytes(contents[(frozen_specs[2].scene_id, frozen_specs[2].asset)])
    completion = repair.run_source_asset_repair(
        root,
        source_mode="official_original_directory",
        source_directory=source_root,
        authorization_path=authorization_path,
        completion_path=completion_path,
    )
    assert len(local_cache_calls) == 3
    assert completion["repaired_asset_count"] == 3
    assert (
        completion["source_asset_repair_incident_commit_sha256"]
        == incident["commit_sha256"]
    )
    persisted = completion_path.read_text(encoding="utf-8").lower()
    assert str(source_root).lower() not in persisted
    assert "https://" not in persisted
    assert repair.authenticate_source_asset_repair_completion(
        root,
        completion_path,
        authorization_path=authorization_path,
    ) == completion
    assert settings.database.exists() is False


def test_signed_pc_href_may_add_query_but_not_change_identity() -> None:
    canonical = (
        "https://landsateuwest.blob.core.windows.net/landsat-c2/"
        "PRODUCT/PRODUCT_QA_RADSAT.TIF"
    )
    repair._validate_signed_pc_href(canonical, canonical + "?sig=secret")
    with pytest.raises(repair.M3SourceAssetRepairError, match="changed"):
        repair._validate_signed_pc_href(
            canonical,
            "https://example.invalid/landsat-c2/PRODUCT/PRODUCT_QA_RADSAT.TIF?sig=x",
        )
