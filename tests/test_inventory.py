from datetime import UTC, datetime

from pystac import Asset, Item
from shapely.geometry import box

from la_heat.inventory import SceneRecord, group_physical_overpasses, scene_is_eligible


def _item(*, platform: str, correction: str = "L2SP", category: str = "T1") -> Item:
    geometry = box(-118.5, 34.0, -118.4, 34.1)
    item = Item(
        id=f"{platform}-{correction}-{category}",
        geometry=geometry.__geo_interface__,
        bbox=list(geometry.bounds),
        datetime=datetime(2024, 7, 1, 18, tzinfo=UTC),
        properties={
            "platform": platform,
            "landsat:correction": correction,
            "landsat:collection_category": category,
        },
    )
    for asset in ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat"):
        item.add_asset(asset, Asset(href=f"https://example.test/{asset}.tif"))
    return item


def test_scene_discovery_enforces_sensor_product_and_assets() -> None:
    allowed = {"landsat-8", "landsat-9"}
    assert scene_is_eligible(_item(platform="landsat-8"), allowed_platforms=allowed)
    assert not scene_is_eligible(_item(platform="landsat-7"), allowed_platforms=allowed)
    assert not scene_is_eligible(
        _item(platform="landsat-8", correction="L2SR"), allowed_platforms=allowed
    )
    assert not scene_is_eligible(
        _item(platform="landsat-8", category="T2"), allowed_platforms=allowed
    )
    missing_asset = _item(platform="landsat-9")
    missing_asset.assets.pop("qa")
    assert not scene_is_eligible(missing_asset, allowed_platforms=allowed)


def _scene(item_id: str, minute: int, row: str) -> SceneRecord:
    geometry = box(-118.7, 33.7, -118.1, 34.4)
    return SceneRecord(
        item_id=item_id,
        platform="landsat-9",
        acquired_utc=datetime(2024, 7, 1, 18, minute, tzinfo=UTC),
        local_date="2024-07-01",
        wrs_path="041",
        wrs_row=row,
        cloud_cover_percent=50.0,
        city_coverage_fraction=1.0,
        geometry_wgs84=geometry,
        asset_hrefs={},
    )


def test_adjacent_rows_group_but_distinct_overpasses_are_flagged() -> None:
    scenes = [
        _scene("row36", 0, "036"),
        _scene("row37", 5, "037"),
        _scene("later", 40, "036"),
    ]
    groups = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=box(-118.6, 33.8, -118.2, 34.3),
        analysis_crs="EPSG:3310",
        maximum_time_gap_minutes=15,
    )
    assert len(groups) == 2
    assert groups[0].scene_ids == ("row36", "row37")
    assert all(group.ambiguous_local_date for group in groups)


def test_time_cluster_uses_full_span_not_chained_adjacent_gaps() -> None:
    scenes = [
        _scene("minute00", 0, "036"),
        _scene("minute10", 10, "037"),
        _scene("minute20", 20, "038"),
    ]
    groups = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=box(-118.6, 33.8, -118.2, 34.3),
        analysis_crs="EPSG:3310",
        maximum_time_gap_minutes=15,
    )
    assert [group.scene_ids for group in groups] == [
        ("minute00", "minute10"),
        ("minute20",),
    ]


def test_time_near_but_nonadjacent_wrs_rows_do_not_merge() -> None:
    scenes = [
        _scene("row36", 0, "036"),
        _scene("row39", 1, "039"),
    ]
    groups = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=box(-118.6, 33.8, -118.2, 34.3),
        analysis_crs="EPSG:3310",
        maximum_time_gap_minutes=15,
    )
    assert [group.scene_ids for group in groups] == [("row36",), ("row39",)]
    assert all(group.ambiguous_local_date for group in groups)
