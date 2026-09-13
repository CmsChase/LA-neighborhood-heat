from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from la_heat.multicity.m3_blind_spatial_blocks_v1 import _city_blocks


def test_city_blocks_are_prefixed_and_target_independent() -> None:
    tracts = gpd.GeoDataFrame(
        {"tract_geoid": ["b", "a"]},
        geometry=[box(5_100, 100, 5_200, 200), box(100, 100, 200, 200)],
        crs="EPSG:5070",
    )
    result = _city_blocks("seattle_wa", tracts)
    assert result["tract_geoid"].tolist() == ["a", "b"]
    assert result["spatial_block"].str.startswith("seattle_wa__").all()
    assert "geometry" not in result
