from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from la_heat.multicity.spatial_blocks import (
    MulticitySpatialBlockError,
    assign_city_spatial_blocks,
)


def _tracts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"tract_geoid": ["b", "a", "c"]},
        geometry=[
            box(5_100, 100, 5_200, 200),
            box(100, 100, 200, 200),
            box(100, 5_100, 200, 5_200),
        ],
        crs="EPSG:5070",
    )


def test_city_blocks_are_deterministic_unique_and_target_blind() -> None:
    first = assign_city_spatial_blocks("chicago_il", _tracts())
    second = assign_city_spatial_blocks("chicago_il", _tracts().iloc[::-1])

    pd.testing.assert_frame_equal(first, second)
    assert first["tract_geoid"].tolist() == ["a", "b", "c"]
    assert first["spatial_block"].tolist() == [
        "chicago_il__x+0000_y+0000",
        "chicago_il__x+0001_y+0000",
        "chicago_il__x+0000_y+0001",
    ]
    assert "geometry" not in first


def test_city_prefix_prevents_equal_local_ids_from_colliding() -> None:
    chicago = assign_city_spatial_blocks("chicago_il", _tracts())
    houston = assign_city_spatial_blocks("houston_tx", _tracts())

    assert set(chicago["local_spatial_block"]) == set(houston["local_spatial_block"])
    assert set(chicago["spatial_block"]).isdisjoint(houston["spatial_block"])


def test_extra_values_are_ignored_and_negative_coordinates_use_floor() -> None:
    tracts = gpd.GeoDataFrame(
        {"tract_geoid": ["negative"], "target_lst_c": [999.0]},
        geometry=[box(-5_200, -200, -5_100, -100)],
        crs="EPSG:5070",
    )

    result = assign_city_spatial_blocks("chicago_il", tracts)

    assert result.loc[0, "local_spatial_block"] == "x-0002_y-0001"
    assert "target_lst_c" not in result


def test_geographic_crs_is_rejected() -> None:
    tracts = _tracts().to_crs("EPSG:4326")

    with pytest.raises(MulticitySpatialBlockError, match="EPSG:5070"):
        assign_city_spatial_blocks("chicago_il", tracts)


def test_other_projected_crs_is_rejected() -> None:
    tracts = _tracts().to_crs("EPSG:26916")

    with pytest.raises(MulticitySpatialBlockError, match="EPSG:5070"):
        assign_city_spatial_blocks("chicago_il", tracts)


def test_non_five_km_block_size_is_rejected() -> None:
    with pytest.raises(MulticitySpatialBlockError, match="exactly 5 km"):
        assign_city_spatial_blocks("chicago_il", _tracts(), block_size_km=10.0)
