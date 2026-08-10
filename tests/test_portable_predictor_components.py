from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import shapely
from rasterio import Affine

from la_heat.grid import FixedGrid
from la_heat.multicity import portable_predictor_components as components
from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.static_features import build_static_support


def _commit(payload: dict[str, object], path: Path) -> dict[str, object]:
    payload["commit_sha256"] = canonical_sha256(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _file_record(root: Path, path: Path, *, rows: int | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        record["rows"] = rows
    return record


def _write_raster(path: Path, array: np.ndarray, grid: FixedGrid) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=grid.height,
        width=grid.width,
        count=1,
        dtype=array.dtype,
        crs=grid.crs,
        transform=grid.transform,
        nodata=0,
    ) as target:
        target.write(array, 1)


def _small_grid(*, crs: str = "EPSG:32612") -> FixedGrid:
    return FixedGrid(
        crs=crs,
        resolution_m=30.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=0.0,
        bottom=0.0,
        right=60.0,
        top=60.0,
        width=2,
        height=2,
        transform=Affine(30.0, 0.0, 0.0, 0.0, -30.0, 60.0),
    )


def _write_support_fixture(root: Path) -> None:
    city_id = "phoenix_az"
    grid = _small_grid()
    tracts = gpd.GeoDataFrame(
        {
            "city_id": [city_id, city_id],
            "tract_geoid": ["04000000001", "04000000002"],
            "geometry": [shapely.box(0, 0, 30, 60), shapely.box(30, 0, 60, 60)],
        },
        crs=grid.crs,
    )
    geography_path = root / "data/geography/primary_tracts.parquet"
    geography_path.parent.mkdir(parents=True, exist_ok=True)
    tracts.to_parquet(geography_path, index=False)
    zones = np.array([[1, 2], [1, 2]], dtype=np.int32)
    eligible = np.ones((2, 2), dtype=np.uint8)
    zone_path = root / "data/support/tract_zones_30m.tif"
    eligible_path = root / "data/support/eligible_mask_30m.tif"
    _write_raster(zone_path, zones, grid)
    _write_raster(eligible_path, eligible, grid)
    support_table = pd.DataFrame(
        {
            "city_id": [city_id, city_id],
            "tract_geoid": ["04000000001", "04000000002"],
            "eligible_cell_count": [2, 2],
            "city_support_identity_sha256": ["support-id", "support-id"],
        }
    )
    support_path = root / "data/support/tract_eligible_support.parquet"
    support_table.to_parquet(support_path, index=False)
    geography = {
        "schema_version": 1,
        "state": "complete_target_blind_city_geography_evidence",
        "output_tables": {
            "primary_tracts": _file_record(root, geography_path, rows=2),
        },
        "access_contract": {"external_target_or_qa_values_read": False},
    }
    _commit(
        geography,
        root
        / "manifests/multicity/cities/phoenix_az/geography/"
        "GEOGRAPHY_CONTRACT_V1.json",
    )
    worldcover = {
        "schema_version": 1,
        "state": "complete_target_blind_worldcover_eligible_support",
        "city_id": city_id,
        "grid": {
            "crs": grid.crs,
            "resolution_m": grid.resolution_m,
            "anchor_x_m": grid.anchor_x_m,
            "anchor_y_m": grid.anchor_y_m,
            "bounds": [grid.left, grid.bottom, grid.right, grid.top],
            "shape": [grid.height, grid.width],
            "transform": list(grid.transform),
            "sha256": grid.sha256,
        },
        "outputs": {
            "tract_zones_30m": _file_record(root, zone_path),
            "eligible_mask_30m": _file_record(root, eligible_path),
            "tract_support": _file_record(root, support_path, rows=2),
        },
        "access_contract": {"external_target_or_qa_values_read": False},
    }
    _commit(
        worldcover,
        root
        / "manifests/multicity/cities/phoenix_az/eligible_support/"
        "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json",
    )


def _write_inventory_fixture(root: Path) -> None:
    keys = pd.DataFrame(
        {
            "city_id": ["phoenix_az"] * 4,
            "tract_geoid": [
                "04000000001",
                "04000000002",
                "04000000001",
                "04000000002",
            ],
            "target_date": pd.to_datetime(
                ["2025-05-01", "2025-05-01", "2025-06-01", "2025-06-01"]
            ),
        }
    )
    path = root / "data/inventory/phoenix_keys.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    keys.to_parquet(path, index=False)
    inventory = {
        "schema_version": 1,
        "state": "complete_target_blind_portable_predictor_inventory",
        "decision": {"predictor_keys_frozen": True},
        "output_tables": {
            "phoenix_az/keys": _file_record(root, path, rows=4),
        },
    }
    _commit(inventory, root / components.INVENTORY_MANIFEST)


def _fake_support() -> components.PortableCitySupport:
    grid = FixedGrid(
        crs="EPSG:3857",
        resolution_m=30.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=0.0,
        bottom=0.0,
        right=150.0,
        top=30.0,
        width=5,
        height=1,
        transform=Affine(30.0, 0.0, 0.0, 0.0, -30.0, 30.0),
    )
    zones = np.array([[1, 1, 1, 2, 2]], dtype=np.int32)
    eligible = np.ones_like(zones, dtype=bool)
    geoids = ("04000000001", "04000000002")
    static_support = build_static_support(
        zones,
        eligible,
        geoids=geoids,
        grid_identity="test-support",
    )
    tracts = gpd.GeoDataFrame(
        {
            "tract_geoid": list(geoids),
            "geometry": [shapely.box(0, 0, 90, 30), shapely.box(90, 0, 150, 30)],
        },
        crs=grid.crs,
    )
    return components.PortableCitySupport(
        city_id="phoenix_az",
        grid=grid,
        zones=zones,
        eligible_land=eligible,
        tract_geoids=geoids,
        tracts=tracts,
        static_support=static_support,
        worldcover_manifest={"commit_sha256": "worldcover"},
        geography_manifest={"commit_sha256": "geography"},
    )


STATIC_FEATURE_NAMES = [
    "nlcd_open_water_fraction",
    "nlcd_developed_open_fraction",
    "nlcd_developed_low_fraction",
    "nlcd_developed_high_fraction",
    "nlcd_barren_fraction",
    "nlcd_forest_fraction",
    "nlcd_shrub_grass_fraction",
    "nlcd_agriculture_fraction",
    "nlcd_wetland_fraction",
    "impervious_mean_fraction",
    "impervious_p90_fraction",
    "impervious_at_least_50_fraction",
    "elevation_mean_m",
    "elevation_std_m",
    "slope_mean_degrees",
    "slope_p90_degrees",
    "gshhg_ocean_great_lakes_shore_distance_mean_km",
    "gshhg_ocean_great_lakes_shore_distance_p10_km",
]


def _fake_contract() -> dict[str, object]:
    groups = {
        "open_water": [11],
        "developed_open": [21],
        "developed_low": [22],
        "developed_medium": [23],
        "developed_high": [24],
        "barren": [31],
        "forest": [41, 42, 43],
        "shrub_grass": [52, 71],
        "agriculture": [81, 82],
        "wetland": [90, 95],
    }
    return {
        "commit_sha256": "contract",
        "contract": {
            "static": {
                "minimum_valid_coverage_fraction": 0.98,
                "quantile_method": "linear",
                "continuous_std_ddof": 0,
                "nlcd": {
                    "land_cover_nodata": 0,
                    "impervious_nodata": 127,
                    "impervious_valid_minimum": 0,
                    "impervious_valid_maximum": 100,
                    "impervious_scale_divisor": 100.0,
                    "groups": groups,
                },
                "terrain": {
                    "slope_source_halo_cells": 1,
                    "native_nodata": -32768,
                    "slope_algorithm": "Horn 3x3",
                    "slope_pixel_size_m": 30.0,
                },
            }
        },
        "feature_registry": {
            "features": [
                {"feature_name": name, "static": True} for name in STATIC_FEATURE_NAMES
            ]
        },
    }


def test_load_city_support_authenticates_sorted_canonical_inputs(tmp_path: Path) -> None:
    _write_support_fixture(tmp_path)

    support = components.load_city_support(tmp_path, "phoenix_az")

    assert support.tract_geoids == ("04000000001", "04000000002")
    assert support.grid.shape == (2, 2)
    assert support.eligible_land.dtype == np.bool_
    assert np.array_equal(support.eligible_mask, support.eligible_land)
    assert support.transform == support.grid.transform
    assert support.crs == "EPSG:32612"


def test_calendar_component_builds_authorized_2025_rows_atomically(tmp_path: Path) -> None:
    _write_support_fixture(tmp_path)
    _write_inventory_fixture(tmp_path)

    output = components.build_calendar_component(tmp_path, "phoenix_az")
    result = pd.read_parquet(output)

    assert output == (
        tmp_path
        / "data/processed/multicity/portable_predictors/components/phoenix_az/"
        "calendar_features.parquet"
    )
    assert list(result.columns) == [
        "city_id",
        "tract_geoid",
        "target_date",
        "calendar_doy_sin",
        "calendar_doy_cos",
    ]
    assert len(result) == 4
    assert np.isfinite(result[["calendar_doy_sin", "calendar_doy_cos"]]).all().all()


def test_gshhg_chunks_pause_only_after_atomic_publish_and_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support = _fake_support()
    linework = gpd.GeoDataFrame(
        {"geometry": [shapely.LineString([(0.0, -0.01), (0.0, 0.01)])]},
        crs="EPSG:4326",
    )
    freeze = {"algorithm_lock": {"search_radii_km": [64, 128, 2048]}}
    monkeypatch.setattr(components, "load_city_support", lambda *_: support)
    monkeypatch.setattr(components, "_load_contract", lambda *_: _fake_contract())
    monkeypatch.setattr(
        components,
        "_load_frozen_gshhg_linework",
        lambda *_: (linework, freeze),
    )
    updates: list[dict[str, object]] = []
    pause_calls = 0

    def pause() -> bool:
        nonlocal pause_calls
        pause_calls += 1
        return True

    first = components.build_gshhg_distance_component(
        tmp_path,
        "phoenix_az",
        progress_callback=updates.append,
        pause_callback=pause,
        chunk_size=2,
    )

    assert first["state"] == "incomplete"
    assert first["completed_chunk_indices"] == [1]
    assert pause_calls == 1
    assert set(updates[0]) == {"city_id", "chunk_index", "chunk_count", "message"}
    first_chunk = tmp_path / first["chunks"]["1"]["path"]
    assert first_chunk.is_file()
    first_sha = sha256_file(first_chunk)

    second = components.build_gshhg_distance_component(
        tmp_path,
        "phoenix_az",
        progress_callback=updates.append,
        pause_callback=lambda: False,
        chunk_size=2,
    )

    assert second["state"] == "complete"
    assert second["completed_chunk_indices"] == [1, 2, 3]
    assert sha256_file(first_chunk) == first_sha
    chunks = [pd.read_parquet(tmp_path / second["chunks"][str(i)]["path"]) for i in (1, 2, 3)]
    combined = pd.concat(chunks, ignore_index=True)
    assert combined["flat_index"].tolist() == [0, 1, 2, 3, 4]
    assert np.isfinite(combined["distance_km"]).all()


def test_static_base_and_finalize_emit_exact_frozen_18_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support = _fake_support()
    contract = _fake_contract()
    monkeypatch.setattr(components, "load_city_support", lambda *_: support)
    monkeypatch.setattr(components, "_load_contract", lambda *_: contract)
    monkeypatch.setattr(
        components,
        "_static_source_paths",
        lambda *_: components.StaticSourcePaths(
            Path("land.tif"),
            Path("impervious.tif"),
            (Path("terrain-a.tif"), Path("terrain-b.tif")),
            ({"path": "synthetic", "bytes": 1, "sha256": "x"},),
        ),
    )

    def fake_reproject(path: Path, **_: object) -> np.ndarray:
        if path.name == "land.tif":
            return np.array([[11, 23, 22, 41, 95]], dtype=np.float32)
        return np.array([[0, 25, 50, 75, 100]], dtype=np.float32)

    monkeypatch.setattr(components, "_reproject_values", fake_reproject)
    monkeypatch.setattr(
        components,
        "_reproject_mosaic",
        lambda *_, **__: np.array(
            [
                [0, 1, 2, 3, 4, 5, 6],
                [1, 2, 3, 4, 5, 6, 7],
                [2, 3, 4, 5, 6, 7, 8],
            ],
            dtype=np.float32,
        ),
    )
    base = components.build_static_base_component(tmp_path, "phoenix_az")
    assert base["state"] == "complete"

    linework = gpd.GeoDataFrame(
        {"geometry": [shapely.LineString([(0.0, -0.01), (0.0, 0.01)])]},
        crs="EPSG:4326",
    )
    monkeypatch.setattr(
        components,
        "_load_frozen_gshhg_linework",
        lambda *_: (linework, {"algorithm_lock": {"search_radii_km": [64, 2048]}}),
    )
    distance = components.build_gshhg_distance_component(
        tmp_path,
        "phoenix_az",
        chunk_size=2,
    )
    assert distance["state"] == "complete"

    result = components.finalize_static_component(tmp_path, "phoenix_az")
    model = pd.read_parquet(
        tmp_path
        / "data/processed/multicity/portable_predictors/components/phoenix_az/"
        "static_features.parquet"
    )
    audit = pd.read_parquet(
        tmp_path
        / "data/processed/multicity/portable_predictors/components/phoenix_az/"
        "static_feature_audit.parquet"
    )

    assert result["model_feature_count"] == 18
    assert list(model.columns) == ["city_id", "tract_geoid", *STATIC_FEATURE_NAMES]
    assert "nlcd_developed_medium_fraction" in audit
    assert "nlcd_remainder_fraction" in audit
    assert audit["nlcd_remainder_fraction"].eq(0.0).all()

