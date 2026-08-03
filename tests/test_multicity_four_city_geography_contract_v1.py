from __future__ import annotations

import ast
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from la_heat.grid import build_fixed_grid
from la_heat.multicity import four_city_geography_contract_v1 as geography

ROOT = Path(__file__).resolve().parents[1]


def _tracts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"tract_geoid": ["00000000001", "00000000002"]},
        geometry=[box(0, 0, 60, 60), box(60, 0, 120, 60)],
        crs="EPSG:32611",
    )


def test_geoid_geometry_mapping_hash_binds_geoid_not_row_order() -> None:
    frame = _tracts()
    first = geography._normalized_geometry_map_sha256(frame)
    reordered = frame.iloc[::-1].reset_index(drop=True)
    assert geography._normalized_geometry_map_sha256(reordered) == first

    exchanged = frame.copy()
    exchanged["tract_geoid"] = exchanged["tract_geoid"].iloc[::-1].to_numpy()
    assert geography._normalized_geometry_map_sha256(exchanged) != first


def test_rasterized_geoid_assignment_uses_fixed_sorted_identity() -> None:
    frame = _tracts()
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(0, 0, 120, 60)], crs=frame.crs
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    geoids = tuple(frame["tract_geoid"])
    zones = geography._rasterize_geoids(frame, grid=grid, geoid_order=geoids)
    replay = geography._rasterize_geoids(
        frame.iloc[::-1], grid=grid, geoid_order=geoids
    )

    assert np.array_equal(zones, replay)
    assert set(np.unique(zones[zones > 0])) == {1, 2}


def test_source_footprint_replay_recognizes_daymet_identity_column() -> None:
    frame = gpd.GeoDataFrame(
        {"daymet_cell_id": ["inside", "outside"]},
        geometry=[box(0, 0, 1, 1), box(3, 3, 4, 4)],
        crs="EPSG:4326",
    )
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(-0.5, -0.5, 1.5, 1.5)], crs="EPSG:4326"
    )
    assert geography._positive_ids(frame, boundary=boundary) == ("inside",)


def test_full_geography_hash_binds_attributes_to_geometry() -> None:
    frame = _tracts()
    original = geography._full_frame_semantic_sha256(frame)
    changed = frame.copy()
    changed.loc[0, "tract_geoid"] = "99999999999"
    assert geography._full_frame_semantic_sha256(changed) != original


def test_geography_program_imports_no_target_model_or_final_reader() -> None:
    source = (
        ROOT / "src/la_heat/multicity/four_city_geography_contract_v1.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(
            (
                "la_heat.final_",
                "la_heat.model",
                "la_heat.target",
                "la_heat.feature_ablation",
            )
        )
        for name in imported_modules
    )
