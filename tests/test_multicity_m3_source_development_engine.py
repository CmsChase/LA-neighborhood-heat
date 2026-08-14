from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin

from la_heat.aligned_landsat import REQUIRED_ASSETS
from la_heat.config import load_config
from la_heat.grid import FixedGrid
from la_heat.multicity.m3_source_development_engine import (
    CITY_COMMIT,
    FINAL_COMMIT,
    OVERPASS_COMMIT,
    M3SourceDevelopmentEngine,
    M3SourceDevelopmentError,
    build_cache_plan_from_inventory,
)
from la_heat.multicity.m3_source_development_runtime import (
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
    RunnerSettings,
)
from la_heat.multicity.m3_source_development_worker import OFFLINE_PHASE, ONLINE_PHASE
from la_heat.provenance import canonical_sha256
from la_heat.target_aggregation import TargetAggregationResult


def _sha(character: str) -> str:
    return character * 64


def _grid(offset: int) -> FixedGrid:
    left = float(offset * 90)
    return FixedGrid(
        crs="EPSG:5070",
        resolution_m=30.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=left,
        bottom=0.0,
        right=left + 60.0,
        top=60.0,
        width=2,
        height=2,
        transform=from_origin(left, 60.0, 30.0, 30.0),
    )


def _contexts() -> dict[str, Any]:
    return {
        city_id: SimpleNamespace(
            city_id=city_id,
            grid=_grid(index),
            locks={"city": canonical_sha256(city_id)},
            zones=np.ones((2, 2), dtype=np.int16),
            eligible_land=np.ones((2, 2), dtype=bool),
            tracts=None,
        )
        for index, city_id in enumerate(SOURCE_CITY_IDS)
    }


def _components() -> tuple[dict[str, Any], ...]:
    contexts = _contexts()
    rows = []
    for index, city_id in enumerate(SOURCE_CITY_IDS, start=1):
        rows.append(
            {
                "city_id": city_id,
                "overpass_id": f"overpass-{index}",
                "target_date": f"2025-07-{index:02d}",
                "platform": "landsat-9",
                "scene_ids": [f"LC09_SCENE_{index}"],
                "union_city_coverage_fraction": 0.99,
                "grid_sha256": contexts[city_id].grid.sha256,
                "target_context_commit_sha256": canonical_sha256(
                    contexts[city_id].locks
                ),
            }
        )
    protocol = {"commit_sha256": _sha("a")}
    amendment = {"commit_sha256": _sha("b")}
    inventory = {
        "state": "expanded_source_inventory_complete",
        "commit_sha256": _sha("c"),
        "source_city_ids": list(SOURCE_CITY_IDS),
        "overpass_count": len(rows),
        "overpasses": rows,
    }
    authorization = {
        "state": "source_qa_two_phase_execution_authorized",
        "claim_id": _sha("e"),
        "commit_sha256": _sha("d"),
        "m3_protocol_lock_commit_sha256": protocol["commit_sha256"],
        "source_acquisition_amendment_commit_sha256": amendment["commit_sha256"],
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": [
            "seattle_wa",
            "denver_co",
            "atlanta_ga",
            "miami_fl",
        ],
        "required_landsat_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(QA_CANDIDATES),
        "expected_overpass_count": 4,
        "expected_unique_city_scene_count": 4,
        "runtime_contract": {
            "download_workers_allowed": [1, 2],
            "compute_workers": 1,
            "raster_window_size": 512,
            "raster_window_size_is_hard_streaming_limit": False,
            "offline_execution_granularity": "one_complete_physical_overpass",
            "signed_urls_credentials_or_cookies_may_be_persisted": False,
            "retry_and_resume_from_content_commits": True,
        },
        "online_predownload_permissions": {
            "hydrate_frozen_source_scene_asset_hrefs": True,
            "read_exact_five_source_landsat_assets": True,
            "write_verified_local_aligned_cache": True,
            "aggregate_targets_or_apply_qa_candidates": False,
            "read_blind_test_city_assets_or_values": False,
        },
        "offline_qa_permissions": {
            "requires_authenticated_global_cache": True,
            "network_or_href_hydration_allowed": False,
            "read_verified_local_source_cache": True,
            "rebuild_none_3k_4k_6k_candidates": True,
            "fit_select_predict_or_score": False,
        },
        "blind_test_target_access_authorized": False,
        "model_fit_or_selection_authorized": False,
        "predictor_build_or_read_authorized": False,
        "source_cache_access_started_marker": (
            "data/interim/multicity/m3_source_development/"
            "SOURCE_CACHE_ACCESS_STARTED.json"
        ),
        "source_landsat_cache_completion": (
            "manifests/multicity/next_experiment/source_development/"
            "SOURCE_LANDSAT_CACHE_COMPLETE.json"
        ),
        "source_qa_candidates_completion": (
            "manifests/multicity/next_experiment/source_development/"
            "SOURCE_QA_CANDIDATES_COMPLETE.json"
        ),
    }
    return protocol, amendment, inventory, authorization, contexts


def _settings(root: Path) -> RunnerSettings:
    runtime = root / "data/interim/multicity/m3_source_development/runtime"
    completion = root / "manifests/multicity/next_experiment/source_development"
    return RunnerSettings(
        root=root,
        config_path=root / "runner.toml",
        protocol_lock=root / "protocol.json",
        amendment=root / "amendment.json",
        inventory=root / "inventory.json",
        authorization=root / "authorization.json",
        database=runtime / "tasks.sqlite",
        control=runtime / "control.json",
        status=runtime / "status.json",
        log=runtime / "worker.log",
        cache_root=root / "data/interim/multicity/m3_source_development/cache",
        qa_output_root=root / "data/interim/multicity/m3_source_development/qa",
        completion_root=completion,
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


def _online_engine(tmp_path: Path, hydrator: Any) -> M3SourceDevelopmentEngine:
    protocol, amendment, inventory, authorization, contexts = _components()
    return M3SourceDevelopmentEngine.from_authenticated_components(
        settings=_settings(tmp_path),
        phase=ONLINE_PHASE,
        protocol=protocol,
        amendment=amendment,
        inventory=inventory,
        authorization=authorization,
        config=load_config(Path("configs/research.toml")),
        contexts=contexts,
        hydrator=hydrator,
        signer=lambda href: f"{href}?token=memory-only",
    )


def _offline_copy(
    online: M3SourceDevelopmentEngine,
    reconstructor: Any,
) -> M3SourceDevelopmentEngine:
    return M3SourceDevelopmentEngine(
        settings=online.settings,
        phase=OFFLINE_PHASE,
        protocol=online.protocol,
        amendment=online.amendment,
        inventory=online.inventory,
        authorization=online.authorization,
        plan=online.plan,
        config=online.config,
        contexts=online.contexts,
        hydrator=None,
        before_value_access=lambda: None,
        reconstructor=reconstructor,
        global_cache_commit={"commit_sha256": _sha("f")},
    )


def test_cache_plan_binds_four_source_grids_and_rejects_blind_city() -> None:
    protocol, amendment, inventory, authorization, contexts = _components()
    plan = build_cache_plan_from_inventory(
        inventory,
        contexts=contexts,
        bindings={
            "protocol": protocol["commit_sha256"],
            "amendment": amendment["commit_sha256"],
            "authorization": authorization["commit_sha256"],
        },
    )

    assert plan["scene_count"] == 4
    assert plan["content_task_count"] == 20
    assert set(plan["grids"]) == set(SOURCE_CITY_IDS)
    assert "http" not in json.dumps(plan).lower()

    changed = dict(inventory)
    changed["overpasses"] = [
        {**inventory["overpasses"][0], "city_id": "seattle_wa"},
        *inventory["overpasses"][1:],
    ]
    with pytest.raises(M3SourceDevelopmentError, match="non-source city"):
        build_cache_plan_from_inventory(
            changed,
            contexts=contexts,
            bindings={"authorization": authorization["commit_sha256"]},
        )


def test_online_asset_task_creates_exclusive_marker_and_keeps_hrefs_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydration_calls: list[str] = []

    def hydrate(scene_id: str) -> dict[str, str]:
        hydration_calls.append(scene_id)
        return {
            asset: f"https://example.test/{scene_id}/{asset}.tif"
            for asset in REQUIRED_ASSETS
        }

    engine = _online_engine(tmp_path, hydrate)
    cache_calls: list[tuple[str, str]] = []

    def fake_cache(
        cache_root: Path,
        plan: dict[str, Any],
        scene_id: str,
        asset: str,
        href: str,
        *,
        before_value_access: Any,
        signer: Any,
    ) -> dict[str, Any]:
        del cache_root, plan
        before_value_access()
        cache_calls.append((href, signer(href)))
        return {"commit_sha256": canonical_sha256([scene_id, asset])}

    monkeypatch.setattr(
        "la_heat.multicity.m3_source_development_engine.cache_asset_from_href",
        fake_cache,
    )
    row = engine.inventory["overpasses"][0]
    base = {
        "city_id": row["city_id"],
        "scene_id": row["scene_ids"][0],
        "grid_sha256": row["grid_sha256"],
        "target_context_commit_sha256": row["target_context_commit_sha256"],
    }
    first = engine.execute("download_asset", {**base, "asset": "lwir11"})
    engine.execute("download_asset", {**base, "asset": "qa_pixel"})

    marker = (
        tmp_path
        / engine.authorization["source_cache_access_started_marker"]
    )
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["state"] == "source_cache_access_started"
    assert hydration_calls == [row["scene_ids"][0]]
    assert all(value[0] == "IN_MEMORY_AUTHORIZED_HREF" for value in cache_calls)
    assert all(value[1].startswith("https://") for value in cache_calls)
    assert "example.test" not in marker.read_text(encoding="utf-8")
    assert "example.test" not in json.dumps(first)


def _synthetic_reconstructor(call_log: list[str]) -> Any:
    def reconstruct(**kwargs: Any) -> dict[str, TargetAggregationResult]:
        call_log.append(str(kwargs["overpass_id"]))
        arrays = kwargs["loader"](kwargs["city_id"], kwargs["scene_ids"][0])
        assert set(arrays) == {"local"}
        results: dict[str, TargetAggregationResult] = {}
        for candidate_id in QA_CANDIDATES:
            targets = pd.DataFrame(
                {
                    "tract_geoid": ["00000000001"],
                    "target_date": [kwargs["target_date"]],
                    "overpass_id": [kwargs["overpass_id"]],
                    "target_available": [True],
                    "date_usable": [True],
                }
            )
            contributions = pd.DataFrame(
                {
                    "tract_geoid": ["00000000001"],
                    "target_date": [kwargs["target_date"]],
                    "overpass_id": [kwargs["overpass_id"]],
                    "scene_id": [kwargs["scene_ids"][0]],
                    "selected_valid_pixel_count": [4],
                }
            )
            summary = {
                "target_date": kwargs["target_date"],
                "overpass_id": kwargs["overpass_id"],
                "date_usable": True,
                "retained_tract_count": 1,
            }
            results[candidate_id] = TargetAggregationResult(
                targets,
                contributions,
                summary,
            )
        return results

    return reconstruct


def test_offline_overpass_compile_and_final_stop_before_model_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    online = _online_engine(
        tmp_path,
        lambda scene_id: {
            asset: f"https://never-used.test/{scene_id}/{asset}"
            for asset in REQUIRED_ASSETS
        },
    )
    reconstruct_calls: list[str] = []
    engine = _offline_copy(online, _synthetic_reconstructor(reconstruct_calls))
    local_reads: list[str] = []

    def fake_local_loader(*args: Any, **kwargs: Any) -> dict[str, np.ndarray]:
        del kwargs
        local_reads.append(str(args[2]))
        return {"local": np.ones((2, 2), dtype=np.uint16)}

    monkeypatch.setattr(
        "la_heat.multicity.m3_source_development_engine.load_local_scene_arrays",
        fake_local_loader,
    )

    for row in engine.inventory["overpasses"]:
        payload = {**row, "qa_candidate_ids": list(QA_CANDIDATES)}
        built = engine.execute("qa_overpass", payload)
        resumed = engine.execute("qa_overpass", payload)
        assert built["cache"] == "built"
        assert resumed["cache"] == "hit"
        overpass_path = (
            engine.settings.qa_output_root
            / "by_overpass"
            / row["city_id"]
            / row["overpass_id"]
            / OVERPASS_COMMIT
        )
        assert overpass_path.is_file()
        assert "http" not in overpass_path.read_text(encoding="utf-8").lower()

    assert len(reconstruct_calls) == 4
    assert len(local_reads) == 4
    for city_id in SOURCE_CITY_IDS:
        result = engine.execute(
            "compile_qa_city",
            {"city_id": city_id, "qa_candidate_ids": list(QA_CANDIDATES)},
        )
        assert result["state"] == "city_qa_candidates_complete"
        assert (
            engine.settings.qa_output_root / "cities" / city_id / CITY_COMMIT
        ).is_file()

    complete = engine.execute(
        "finalize_qa_candidates",
        {
            "source_city_ids": list(SOURCE_CITY_IDS),
            "qa_candidate_ids": list(QA_CANDIDATES),
            "expected_overpass_count": 4,
        },
    )
    assert complete["state"] == "source_qa_candidates_complete"
    assert complete["support_gate"]["passed"] is False
    assert complete["model_fit_performed"] is False
    assert complete["nested_loso_performed"] is False
    assert complete["model_or_st_qa_selected"] is False
    assert complete["blind_test_asset_or_target_accessed"] is False
    assert (engine.settings.completion_root / FINAL_COMMIT).is_file()


def test_phase_dispatch_rejects_cross_phase_tasks(tmp_path: Path) -> None:
    online = _online_engine(
        tmp_path,
        lambda scene_id: {
            asset: f"https://example.test/{scene_id}/{asset}"
            for asset in REQUIRED_ASSETS
        },
    )
    offline = _offline_copy(online, _synthetic_reconstructor([]))

    with pytest.raises(M3SourceDevelopmentError, match="forbidden"):
        online.execute("qa_overpass", {})
    with pytest.raises(M3SourceDevelopmentError, match="forbidden"):
        offline.execute("download_asset", {})


def test_offline_factory_rejects_a_hydrator_before_any_cache_read(tmp_path: Path) -> None:
    protocol, amendment, inventory, authorization, contexts = _components()
    with pytest.raises(M3SourceDevelopmentError, match="may not receive"):
        M3SourceDevelopmentEngine.from_authenticated_components(
            settings=_settings(tmp_path),
            phase=OFFLINE_PHASE,
            protocol=protocol,
            amendment=amendment,
            inventory=inventory,
            authorization=authorization,
            config=load_config(Path("configs/research.toml")),
            contexts=contexts,
            hydrator=lambda scene_id: {},
        )
