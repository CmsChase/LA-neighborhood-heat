from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from rasterio import Affine
from shapely.geometry import LineString

from la_heat.grid import FixedGrid
from la_heat.static_features import (
    StaticArrays,
    StaticFeatureCoverageError,
    StaticSupport,
    aggregate_static_features,
    build_static_support,
    coast_distance_on_support,
    horn_slope_degrees,
    static_feature_registry,
)


def _fixture() -> tuple[StaticArrays, StaticSupport]:
    zones = np.array([[1, 1, 2, 2], [1, 1, 2, 2]], dtype=np.int16)
    eligible = np.ones_like(zones, dtype=bool)
    support = build_static_support(
        zones,
        eligible,
        geoids=["06037000100", "06037000200"],
        grid_identity="test-grid",
    )
    arrays = StaticArrays(
        land_cover=np.array([[11, 21, 41, 41], [21, 21, 41, 52]], dtype=np.int16),
        land_cover_valid=np.ones_like(eligible),
        impervious_fraction=np.array(
            [[0.0, 0.2, 0.6, 1.0], [0.5, 0.8, 0.0, 0.4]], dtype=float
        ),
        impervious_valid=np.ones_like(eligible),
        elevation_m=np.array([[10, 20, 30, 40], [30, 40, 50, 60]], dtype=float),
        elevation_valid=np.ones_like(eligible),
        slope_degrees=np.array([[1, 2, 3, 4], [3, 4, 5, 6]], dtype=float),
        slope_valid=np.ones_like(eligible),
        coast_distance_km=np.array([[1, 2, 3, 4], [2, 3, 4, 5]], dtype=float),
        coast_distance_valid=np.ones_like(eligible),
    )
    return arrays, support


def test_fixed_denominator_aggregation_and_zero_impervious_is_valid() -> None:
    arrays, support = _fixture()
    features, audit = aggregate_static_features(
        arrays=arrays,
        support=support,
        land_groups={"open_water": [11], "developed_open": [21], "forest": [41]},
        minimum_coverage_fraction=0.98,
        std_ddof=0,
        quantile_method="linear",
    )
    first = features.set_index("tract_geoid").loc["06037000100"]
    assert first["nlcd_open_water_fraction"] == 0.25
    assert first["nlcd_developed_open_fraction"] == 0.75
    assert first["impervious_mean_fraction"] == pytest.approx(0.375)
    assert first["impervious_at_least_50_fraction"] == 0.5
    assert first["elevation_std_m"] == pytest.approx(np.std([10, 20, 30, 40]))
    assert (audit.filter(like="coverage_fraction") == 1.0).all().all()
    assert audit["eligible_pixel_count_static"].tolist() == [4, 4]


def test_coverage_gate_fails_instead_of_renormalizing() -> None:
    arrays, support = _fixture()
    bad_valid = arrays.impervious_valid.copy()
    bad_valid[0, 0] = False
    broken = replace(arrays, impervious_valid=bad_valid)
    with pytest.raises(StaticFeatureCoverageError, match="nlcd_impervious=0.7500"):
        aggregate_static_features(
            arrays=broken,
            support=support,
            land_groups={"open_water": [11], "developed_open": [21], "forest": [41]},
            minimum_coverage_fraction=0.98,
            std_ddof=0,
            quantile_method="linear",
        )


def test_horn_slope_matches_planar_surface_and_propagates_nodata() -> None:
    x = np.arange(5, dtype=float) * 30.0
    elevation = np.tile(x, (5, 1))
    slope = horn_slope_degrees(elevation, pixel_width_m=30.0, pixel_height_m=30.0)
    assert slope.shape == (3, 3)
    assert np.allclose(slope, 45.0)

    elevation[0, 0] = np.nan
    slope = horn_slope_degrees(elevation, pixel_width_m=30.0, pixel_height_m=30.0)
    assert np.isnan(slope[0, 0])
    assert np.isfinite(slope[-1, -1])


def test_coast_distance_uses_eligible_cell_centers() -> None:
    grid = FixedGrid(
        crs="EPSG:32611",
        resolution_m=1000.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=0.0,
        bottom=0.0,
        right=2000.0,
        top=2000.0,
        width=2,
        height=2,
        transform=Affine(1000.0, 0.0, 0.0, 0.0, -1000.0, 2000.0),
    )
    coast = gpd.GeoDataFrame(
        {"MTFCC": ["L4150"]},
        geometry=[LineString([(0, -1000), (0, 3000)])],
        crs=grid.crs,
    )
    distance = coast_distance_on_support(
        coast=coast,
        grid=grid,
        eligible_land=np.ones((2, 2), dtype=bool),
        search_buffer_km=10.0,
        chunk_size=2,
    )
    assert np.allclose(distance[:, 0], 0.5)
    assert np.allclose(distance[:, 1], 1.5)


def test_static_registry_contains_only_key_and_legal_model_roles() -> None:
    arrays, support = _fixture()
    features, _ = aggregate_static_features(
        arrays=arrays,
        support=support,
        land_groups={"open_water": [11], "developed_open": [21], "forest": [41]},
        minimum_coverage_fraction=0.98,
        std_ddof=0,
        quantile_method="linear",
    )
    existing_land = [
        "nlcd_open_water_fraction",
        "nlcd_developed_open_fraction",
        "nlcd_forest_fraction",
    ]
    features[existing_land] = features[existing_land] * 0.8
    features["nlcd_developed_medium_fraction"] = 0.2
    registry = static_feature_registry(features)
    assert set(registry["role"]) == {"key", "model", "audit_only"}
    assert registry.loc[registry["role"] == "model", "static"].all()
    assert registry.loc[
        registry["feature_name"] == "nlcd_developed_medium_fraction", "role"
    ].item() == "audit_only"
    registered_table_columns = set(registry["feature_name"]) - {"target_date"}
    assert registered_table_columns == set(features.columns)
    land_model_names = registry.loc[
        registry["role"].eq("model")
        & registry["feature_name"].str.startswith("nlcd_")
        & registry["feature_name"].str.endswith("_fraction"),
        "feature_name",
    ]
    assert not np.allclose(features[land_model_names].sum(axis=1), 1.0)
    assert not registry["feature_name"].str.contains("target_lst", regex=False).any()
    assert isinstance(registry, pd.DataFrame)


def test_static_feature_cli_help_has_no_data_side_effect(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "build_static_features.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout
