from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from la_heat.aligned_landsat import COVERAGE_KEY, REQUIRED_ASSETS
from la_heat.grid import FixedGrid
from la_heat.multicity.m3_source_asset_cache import (
    M3SourceAssetCacheError,
    authenticate_global_cache,
    authenticate_plan,
    build_scene_plan,
    cache_asset_from_href,
    cache_scene_from_hrefs,
    finalize_global_cache,
    finalize_scene_cache,
    load_local_scene_arrays,
    resolve_local_cache_path,
    write_scene_plan,
)


def _grid() -> FixedGrid:
    return FixedGrid(
        crs="EPSG:32610",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
        left=15.0,
        bottom=15.0,
        right=75.0,
        top=75.0,
        width=2,
        height=2,
        transform=from_origin(15.0, 75.0, 30.0, 30.0),
    )


def _scene(scene_id: str = "LC09_TEST_SCENE") -> dict[str, Any]:
    return {
        "city_id": "seattle_wa",
        "scene_id": scene_id,
        "overpass_id": f"landsat-9_{scene_id}",
        "target_date": "2025-07-01",
        "platform": "landsat-9",
        "acquired_utc": "2025-07-01T19:00:00Z",
    }


def _plan(*scenes: dict[str, Any]) -> dict[str, Any]:
    return build_scene_plan(
        list(scenes) if scenes else [_scene()],
        grids={"seattle_wa": _grid()},
        bindings={
            "source_selection_commit_sha256": "a" * 64,
            "authorization_commit_sha256": "b" * 64,
        },
    )


def _write_source(path: Path, values: np.ndarray, *, nodata: int) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype=values.dtype,
        crs="EPSG:32610",
        transform=_grid().transform,
        nodata=nodata,
    ) as target:
        target.write(values, 1)


def _source_hrefs(directory: Path) -> tuple[dict[str, str], dict[str, np.ndarray]]:
    directory.mkdir(parents=True)
    arrays = {
        "lwir11": np.array([[44177, 44200], [44300, 44400]], dtype=np.uint16),
        "qa_pixel": np.array([[64, 64], [64, 72]], dtype=np.uint16),
        "qa": np.array([[210, 220], [230, 240]], dtype=np.int16),
        "cdist": np.array([[200, 300], [400, 500]], dtype=np.int16),
        "qa_radsat": np.zeros((2, 2), dtype=np.uint16),
    }
    nodata = {
        "lwir11": 0,
        "qa_pixel": 0,
        "qa": -9999,
        "cdist": -9999,
        "qa_radsat": 0,
    }
    hrefs: dict[str, str] = {}
    for asset in REQUIRED_ASSETS:
        path = directory / f"{asset}.tif"
        _write_source(path, arrays[asset], nodata=nodata[asset])
        hrefs[asset] = str(path)
    return hrefs, arrays


def test_plan_is_deterministic_metadata_only_and_exact() -> None:
    first = _plan()
    second = _plan()

    assert first == second
    assert authenticate_plan(first) == first
    assert first["required_assets"] == list(REQUIRED_ASSETS)
    assert first["content_task_count"] == 5
    assert first["remote_hrefs_signed_urls_tokens_or_cookies_persisted"] is False

    changed = dict(first)
    changed["required_assets"] = ["lwir11"]
    with pytest.raises(M3SourceAssetCacheError, match="commit is invalid"):
        authenticate_plan(changed)


def test_cache_resume_local_load_and_global_authentication(tmp_path: Path) -> None:
    source_hrefs, expected = _source_hrefs(tmp_path / "remote-source")
    cache_root = tmp_path / "portable-cache"
    plan = _plan()
    write_scene_plan(cache_root, plan)
    gate_events: list[str] = []
    sign_events: list[str] = []

    def gate() -> None:
        gate_events.append("gate")

    def signer(value: str) -> str:
        sign_events.append(value)
        return f"{value}?sig=memory-only-secret"

    # Local rasterio paths cannot carry a query string, so emulate an in-memory
    # signer while returning the original readable path.
    def readable_signer(value: str) -> str:
        signer(value)
        return value

    stale = (
        cache_root
        / "cities/seattle_wa/scenes/LC09_TEST_SCENE/assets/.qa.tif.dead.part"
    )
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"incomplete")
    scene_commit = cache_scene_from_hrefs(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        source_hrefs,
        before_value_access=gate,
        signer=readable_signer,
    )

    assert scene_commit["state"] == "complete"
    assert len(sign_events) == len(REQUIRED_ASSETS)
    assert not stale.exists()
    arrays = load_local_scene_arrays(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        before_value_access=gate,
    )
    assert set(arrays) == {*REQUIRED_ASSETS, COVERAGE_KEY}
    for asset in REQUIRED_ASSETS:
        np.testing.assert_array_equal(arrays[asset], expected[asset])
    assert arrays[COVERAGE_KEY].dtype == np.bool_
    assert arrays[COVERAGE_KEY].all()

    def forbidden_signer(_value: str) -> str:
        raise AssertionError("A completed scene must not reopen any href.")

    resumed = cache_scene_from_hrefs(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        source_hrefs,
        before_value_access=gate,
        signer=forbidden_signer,
    )
    assert resumed == scene_commit

    final = finalize_global_cache(cache_root, plan, before_value_access=gate)
    assert final["scene_count"] == 1
    assert final["content_count"] == 5
    assert (
        authenticate_global_cache(cache_root, plan, before_value_access=gate) == final
    )
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in cache_root.rglob("*.json")
    )
    assert "memory-only-secret" not in persisted
    assert str(tmp_path / "remote-source") not in persisted
    assert len(gate_events) >= 5


def test_two_scene_workers_write_independent_atomic_commits(tmp_path: Path) -> None:
    hrefs, _ = _source_hrefs(tmp_path / "sources")
    plan = _plan(_scene("SCENE_A"), _scene("SCENE_B"))
    cache_root = tmp_path / "cache"
    write_scene_plan(cache_root, plan)

    def run(scene_id: str) -> str:
        result = cache_scene_from_hrefs(
            cache_root,
            plan,
            scene_id,
            hrefs,
            before_value_access=lambda: None,
        )
        return str(result["commit_sha256"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        commits = list(pool.map(run, ("SCENE_A", "SCENE_B")))

    assert len(set(commits)) == 2
    assert not list(cache_root.rglob("*.part"))
    global_commit = finalize_global_cache(
        cache_root,
        plan,
        before_value_access=lambda: None,
    )
    assert global_commit["scene_count"] == 2


def test_asset_tasks_and_scene_finalizer_are_separate_resume_boundaries(
    tmp_path: Path,
) -> None:
    hrefs, _ = _source_hrefs(tmp_path / "sources")
    plan = _plan()
    cache_root = tmp_path / "cache"
    write_scene_plan(cache_root, plan)

    first = cache_asset_from_href(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        "lwir11",
        hrefs["lwir11"],
        before_value_access=lambda: None,
    )
    resumed = cache_asset_from_href(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        "lwir11",
        "https://must-not-be-opened.test/asset.tif?sig=secret",
        before_value_access=lambda: None,
        signer=lambda _value: (_ for _ in ()).throw(
            AssertionError("committed content must not be signed again")
        ),
    )
    assert resumed == first
    with pytest.raises(M3SourceAssetCacheError, match="Cannot read qa_pixel"):
        finalize_scene_cache(
            cache_root,
            plan,
            "LC09_TEST_SCENE",
            before_value_access=lambda: None,
        )

    for asset in REQUIRED_ASSETS[1:]:
        cache_asset_from_href(
            cache_root,
            plan,
            "LC09_TEST_SCENE",
            asset,
            hrefs[asset],
            before_value_access=lambda: None,
        )
    scene = finalize_scene_cache(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        before_value_access=lambda: None,
    )
    assert len(scene["content_commit_sha256s"]) == 5


def test_global_authentication_detects_tampered_content(tmp_path: Path) -> None:
    hrefs, _ = _source_hrefs(tmp_path / "sources")
    plan = _plan()
    cache_root = tmp_path / "cache"
    write_scene_plan(cache_root, plan)
    cache_scene_from_hrefs(
        cache_root,
        plan,
        "LC09_TEST_SCENE",
        hrefs,
        before_value_access=lambda: None,
    )
    finalize_global_cache(cache_root, plan, before_value_access=lambda: None)
    target = (
        cache_root
        / "cities/seattle_wa/scenes/LC09_TEST_SCENE/assets/lwir11.tif"
    )
    with target.open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(M3SourceAssetCacheError, match="failed its file lock"):
        authenticate_global_cache(
            cache_root,
            plan,
            before_value_access=lambda: None,
        )


@pytest.mark.parametrize(
    "value",
    (
        "https://example.test/cache.tif",
        "http://example.test/cache.tif",
        "file:///tmp/cache.tif",
        "../outside.tif",
        "C:/outside.tif",
        "cities\\scene.tif",
    ),
)
def test_local_resolver_rejects_urls_and_escaping_paths(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(M3SourceAssetCacheError):
        resolve_local_cache_path(tmp_path, value)


def test_plan_file_is_immutable_on_resume(tmp_path: Path) -> None:
    plan = _plan()
    path = write_scene_plan(tmp_path, plan)
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["scene_count"] = 2
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(M3SourceAssetCacheError, match="commit is invalid"):
        write_scene_plan(tmp_path, plan)
