from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from la_heat.multicity.spatial_blocks import OUTPUT_COLUMNS
from la_heat.multicity.target_context import (
    TargetContextError,
    attach_frozen_spatial_blocks,
)


def _tracts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"tract_geoid": ["b", "a"], "name": ["B", "A"]},
        geometry=[box(5_000, 0, 6_000, 1_000), box(0, 0, 1_000, 1_000)],
        crs="EPSG:5070",
    )


def _blocks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city_id": ["chicago_il", "chicago_il"],
            "tract_geoid": ["a", "b"],
            "spatial_block": [
                "chicago_il__x+0000_y+0000",
                "chicago_il__x+0001_y+0000",
            ],
            "local_spatial_block": ["x+0000_y+0000", "x+0001_y+0000"],
            "longitude_quartile": [0, 3],
            "latitude_quartile": [0, 3],
        },
        columns=OUTPUT_COLUMNS,
    )


def test_target_context_join_preserves_zone_order_and_adds_required_metadata() -> None:
    result = attach_frozen_spatial_blocks("chicago_il", _tracts(), _blocks())

    assert result["GEOID"].tolist() == ["b", "a"]
    assert result["spatial_block"].tolist() == [
        "chicago_il__x+0001_y+0000",
        "chicago_il__x+0000_y+0000",
    ]
    assert result.geometry.equals(_tracts().geometry)


def test_target_context_rejects_missing_block_mapping() -> None:
    with pytest.raises(TargetContextError, match="GEOIDs do not match"):
        attach_frozen_spatial_blocks("chicago_il", _tracts(), _blocks().iloc[:1])


def test_target_context_rejects_preexisting_unfrozen_block() -> None:
    tracts = _tracts().assign(spatial_block="legacy")

    with pytest.raises(TargetContextError, match="already contain"):
        attach_frozen_spatial_blocks("chicago_il", tracts, _blocks())
