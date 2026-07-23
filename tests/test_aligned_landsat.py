from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box

from la_heat.aligned_landsat import (
    COVERAGE_KEY,
    _read_asset_to_grid,
    decode_aligned_scene_arrays,
)
from la_heat.config import load_config
from la_heat.grid import build_fixed_grid

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def test_aligned_scene_decoder_applies_cloud_distance_and_qa() -> None:
    config = load_config(CONFIG)
    clear = np.uint16(1 << 6)
    arrays = {
        "lwir11": np.array([[44177, 44177, 44177, 0]], dtype=np.uint16),
        "qa_pixel": np.array(
            [[clear, clear | (1 << 3), clear, 0]], dtype=np.uint16
        ),
        "qa": np.array([[210, 190, 500, -9999]], dtype=np.int16),
        "cdist": np.array([[100, 500, 99, -9999]], dtype=np.int16),
        "qa_radsat": np.zeros((1, 4), dtype=np.uint16),
        COVERAGE_KEY: np.array([[True, True, True, False]]),
    }
    scene = decode_aligned_scene_arrays(
        scene_id="test-scene", arrays=arrays, config=config
    )
    assert scene.footprint.tolist() == [[True, True, True, False]]
    assert scene.valid.tolist() == [[True, False, False, False]]
    assert np.isclose(scene.lst_c[0, 0], 26.84786954)
    assert np.isnan(scene.st_uncertainty_k[0, 3])


def test_footprint_uses_source_coverage_and_fill_not_surface_temperature_dn() -> None:
    config = load_config(CONFIG)
    clear = np.uint16(1 << 6)
    arrays = {
        # Pixel 0 is covered but has no ST retrieval; pixel 2 is a QA fill pixel;
        # pixel 3 has fallback QA=0 but lies outside the source raster.
        "lwir11": np.array([[0, 44177, 44177, 0]], dtype=np.uint16),
        "qa_pixel": np.array([[clear, clear, 1, 0]], dtype=np.uint16),
        "qa": np.array([[200, 200, 200, -9999]], dtype=np.int16),
        "cdist": np.array([[500, 500, 500, -9999]], dtype=np.int16),
        "qa_radsat": np.zeros((1, 4), dtype=np.uint16),
        COVERAGE_KEY: np.array([[True, True, True, False]]),
    }
    scene = decode_aligned_scene_arrays(
        scene_id="test-footprint", arrays=arrays, config=config
    )
    assert scene.footprint.tolist() == [[True, True, False, False]]
    assert scene.valid.tolist() == [[False, True, False, False]]


def test_source_extent_coverage_is_independent_of_zero_pixel_values() -> None:
    boundary = gpd.GeoDataFrame(
        {"name": ["test"]}, geometry=[box(-10, 20, 70, 100)], crs="EPSG:32611"
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    profile = {
        "driver": "GTiff",
        "height": 1,
        "width": 1,
        "count": 1,
        "dtype": "uint16",
        "crs": "EPSG:32611",
        "transform": from_origin(15, 105, 30, 30),
        "nodata": 0,
    }
    with MemoryFile() as memory:
        with memory.open(**profile) as dataset:
            dataset.write(np.zeros((1, 1, 1), dtype=np.uint16))
        array, coverage = _read_asset_to_grid(
            memory.name,
            grid=grid,
            fallback_nodata=0,
        )
    assert not array.any()
    assert coverage.tolist() == [
        [False, True, False],
        [False, False, False],
        [False, False, False],
    ]


def test_source_grid_phase_mismatch_fails_closed() -> None:
    boundary = gpd.GeoDataFrame(
        {"name": ["test"]}, geometry=[box(-10, 20, 70, 100)], crs="EPSG:32611"
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    profile = {
        "driver": "GTiff",
        "height": 1,
        "width": 1,
        "count": 1,
        "dtype": "uint16",
        "crs": "EPSG:32611",
        "transform": from_origin(0, 105, 30, 30),
    }
    with MemoryFile() as memory:
        with memory.open(**profile) as dataset:
            dataset.write(np.ones((1, 1, 1), dtype=np.uint16))
        with pytest.raises(ValueError, match="not aligned"):
            _read_asset_to_grid(memory.name, grid=grid, fallback_nodata=0)
