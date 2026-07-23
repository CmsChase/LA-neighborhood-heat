import copy
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from la_heat.config import ResearchConfig, load_config
from la_heat.mosaic import mosaic_aligned_scenes
from la_heat.target_aggregation import aggregate_target_mosaic

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def test_fixed_grid_mosaic_aggregates_once_per_pixel_and_retains_lineage() -> None:
    loaded = load_config(CONFIG)
    raw = copy.deepcopy(loaded.raw)
    raw["landsat"]["minimum_valid_pixels_per_tract"] = 2
    raw["validation"]["minimum_relative_joint_cell_tracts"] = 1
    config = ResearchConfig(raw=raw, path=loaded.path)
    tracts = gpd.GeoDataFrame(
        {
            "GEOID": ["a", "b"],
            "spatial_block": ["x", "y"],
            "latitude_quartile": [0, 1],
            "longitude_quartile": [0, 1],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:32611",
    )
    zones = np.array([[1, 1, 2, 2], [1, 1, 2, 2]], dtype=np.int16)
    valid = np.ones((1, 2, 4), dtype=bool)
    valid[0, 0, 2] = False
    mosaic = mosaic_aligned_scenes(
        scene_ids=["scene-a"],
        st_values=np.array([[[30.0, 32.0, 40.0, 42.0], [34.0, 36.0, 44.0, 46.0]]]),
        qa_valid=valid,
        st_qa=np.full((1, 2, 4), 2.0),
        cdist=np.full((1, 2, 4), 5.0),
        footprint=np.ones((1, 2, 4), dtype=bool),
    )
    aggregated = aggregate_target_mosaic(
        tracts=tracts,
        zone_raster=zones,
        static_land_mask=np.ones((2, 4), dtype=bool),
        mosaic=mosaic,
        target_date="2024-07-01",
        overpass_id="test-overpass",
        platform="landsat-9",
        scene_ids=("scene-a",),
        union_city_coverage_fraction=1.0,
        grid_identity="test-grid",
        config_sha256="config",
        tract_manifest_sha256="tracts",
        config=config,
    )
    targets = aggregated.tract_date_qa.set_index("tract_geoid")
    assert targets.loc["a", "target_lst_c"] == 33.0
    assert targets.loc["b", "target_lst_c"] == 44.0
    assert targets["target_available"].all()
    assert aggregated.scene_contributions["selected_valid_pixel_count"].sum() == 7
    assert aggregated.summary["date_usable"]
    assert aggregated.summary["relative_hotspot_count"] == 1
