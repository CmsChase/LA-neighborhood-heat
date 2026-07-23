from datetime import UTC, date, datetime

import geopandas as gpd
import pandas as pd
import pytest
from pystac import Asset, Item
from shapely.geometry import box

from la_heat.config import load_config
from la_heat.final_test_inventory import (
    FinalTestInventoryError,
    build_target_blind_key_universe,
    discover_final_test_scenes,
)


class _Results:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def items(self) -> list[Item]:
        return self._items


class _Client:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.kwargs: dict[str, object] | None = None

    def search(self, **kwargs: object) -> _Results:
        self.kwargs = kwargs
        return _Results(self.items)


def _item(item_id: str, *, when: datetime, cloud: float = 100.0) -> Item:
    geometry = box(-118.8, 33.6, -118.0, 34.5)
    item = Item(
        id=item_id,
        geometry=geometry.__geo_interface__,
        bbox=list(geometry.bounds),
        datetime=when,
        properties={
            "platform": "landsat-9",
            "landsat:collection_category": "T1",
            "landsat:correction": "L2SP",
            "landsat:wrs_path": "041",
            "landsat:wrs_row": "036",
            "eo:cloud_cover": cloud,
        },
    )
    for asset in ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat"):
        item.add_asset(
            asset,
            Asset(href=f"https://example.test/{item_id}/{asset}.tif?token=secret"),
        )
    return item


def test_discovery_is_exact_2025_metadata_without_cloud_cutoff() -> None:
    config = load_config("configs/research.toml")
    city = gpd.GeoDataFrame(
        {"name": ["LA"]}, geometry=[box(-118.7, 33.7, -118.15, 34.34)], crs="EPSG:4326"
    )
    client = _Client(
        [
            _item("inside", when=datetime(2025, 7, 1, 18, tzinfo=UTC)),
            _item("outside", when=datetime(2024, 7, 1, 18, tzinfo=UTC)),
        ]
    )
    scenes, overpasses = discover_final_test_scenes(
        client, config=config, city_boundary=city
    )
    assert [row.item_id for row in scenes] == ["inside"]
    assert scenes[0].cloud_cover_percent == 100.0
    assert "?" not in scenes[0].asset_hrefs["lwir11"]
    assert len(overpasses) == 1
    assert client.kwargs is not None
    assert client.kwargs["datetime"] == "2025-05-01/2025-10-31"
    assert "eo:cloud_cover" not in str(client.kwargs["query"])


def test_discovery_rejects_nonfinal_date_contract() -> None:
    config = load_config("configs/research.toml")
    city = gpd.GeoDataFrame(
        geometry=[box(-118.7, 33.7, -118.15, 34.34)], crs="EPSG:4326"
    )
    with pytest.raises(FinalTestInventoryError, match="must remain in 2025"):
        discover_final_test_scenes(
            _Client([]),
            config=config,
            city_boundary=city,
            start_date=date(2024, 5, 1),
        )


def test_target_blind_key_universe_is_complete_and_unique() -> None:
    tracts = gpd.GeoDataFrame(
        {
            "GEOID": ["06037000001", "06037000002"],
            "primary_included": [True, True],
            "spatial_block": ["a", "b"],
            "latitude_quartile": [1, 2],
            "longitude_quartile": [3, 4],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:3310",
    )
    overpasses = pd.DataFrame(
        {
            "local_date": ["2025-06-01", "2025-06-17"],
            "overpass_id": ["one", "two"],
            "platform": ["landsat-9", "landsat-8"],
        }
    )
    result = build_target_blind_key_universe(tracts, overpasses)
    assert len(result) == 4
    assert result["target_date"].dt.year.eq(2025).all()
    assert not result.duplicated(["tract_geoid", "target_date"]).any()
    assert result["spatial_block"].nunique() == 2
