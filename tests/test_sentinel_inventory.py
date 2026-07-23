import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from pystac import Asset, Item
from shapely.geometry import box

from la_heat.provenance import sha256_file
from la_heat.sentinel_inventory import (
    INVENTORY_SUMMARY_FILENAME,
    REQUIRED_SENTINEL_ASSETS,
    SELECTED_ACQUISITIONS_FILENAME,
    SELECTED_ITEMS_FILENAME,
    TARGET_WINDOW_MEMBERSHIP_FILENAME,
    CohortSelection,
    PhysicalAcquisitionKey,
    SentinelItemRecord,
    build_local_date_query_intervals,
    build_sentinel_inventory_artifacts,
    build_target_window_membership,
    canonical_processing_baseline,
    canonical_stac_item_snapshot,
    normalize_datatake_id,
    physical_acquisition_key,
    processing_baseline_key,
    query_sentinel_items,
    select_all_reprocessing_cohorts,
    select_reprocessing_cohort,
    sentinel_inventory_semantic_sha256,
    sentinel_record_from_item,
    utc_datetime_to_la_date,
    validate_final_test_lock,
)

AOI = box(-118.6, 34.0, -118.4, 34.2)


def _record(
    item_id: str,
    *,
    baseline: str = "05.10",
    tile: str = "11SLU",
    geometry=AOI,
    acquired: datetime = datetime(2024, 7, 6, 18, 29, tzinfo=UTC),
    generation: datetime = datetime(2024, 7, 7, 2, 30, tzinfo=UTC),
    datatake_stem: str = "GS2A_20240706T182921_047214",
) -> SentinelItemRecord:
    return SentinelItemRecord(
        item_id=item_id,
        platform="Sentinel-2A",
        acquired_utc=acquired,
        relative_orbit="027",
        datatake_id=f"{datatake_stem}_N{baseline}",
        mgrs_tile=tile,
        processing_baseline=baseline,
        generation_time=generation,
        geometry_wgs84=geometry,
        asset_hrefs=(("B02", f"https://example.test/{item_id}/B02.tif"),),
        cloud_cover_percent=73.0,
    )


def _key(acquired: datetime) -> PhysicalAcquisitionKey:
    return PhysicalAcquisitionKey(
        platform="sentinel-2a",
        acquired_utc=acquired,
        relative_orbit="27",
        normalized_datatake_id=f"ACQ-{acquired.date().isoformat()}",
    )


def test_utc_datetime_maps_to_los_angeles_civil_date_across_seasons() -> None:
    assert utc_datetime_to_la_date("2024-07-01T06:59:00Z") == date(2024, 6, 30)
    assert utc_datetime_to_la_date("2024-12-01T07:59:00Z") == date(2024, 11, 30)
    assert utc_datetime_to_la_date("2024-12-01T08:00:00Z") == date(2024, 12, 1)

    with pytest.raises(ValueError, match="timezone-aware"):
        utc_datetime_to_la_date(datetime(2024, 7, 1, 18, 0))
    with pytest.raises(ValueError, match="expressed in UTC"):
        utc_datetime_to_la_date(
            datetime(2024, 7, 1, 19, 0, tzinfo=timezone(timedelta(hours=1)))
        )


def test_normalized_datatake_and_physical_key_ignore_tile_and_reprocessing() -> None:
    older = _record("old-tile", baseline="04.00", tile="11SLT")
    newer = _record("new-tile", baseline="05.10", tile="11SLU")

    assert normalize_datatake_id(older.datatake_id) == "GS2A_20240706T182921_047214"
    assert physical_acquisition_key(older) == physical_acquisition_key(newer)
    assert physical_acquisition_key(older).relative_orbit == "27"
    assert processing_baseline_key("05.10") > processing_baseline_key("05.09")
    assert canonical_processing_baseline("N5.9") == "05.09"


def test_membership_is_exactly_d_minus_60_through_d_minus_1() -> None:
    target = date(2024, 8, 30)
    acquisitions = [
        _key(datetime(2024, 6, 30, 18, tzinfo=UTC)),  # d-61
        _key(datetime(2024, 7, 1, 18, tzinfo=UTC)),  # d-60
        _key(datetime(2024, 8, 29, 18, tzinfo=UTC)),  # d-1
        _key(datetime(2024, 8, 30, 18, tzinfo=UTC)),  # d
        _key(datetime(2024, 8, 31, 18, tzinfo=UTC)),  # future
    ]
    memberships = build_target_window_membership(
        [target],
        [*acquisitions, acquisitions[1]],
        unlock_final_test=False,
    )

    assert [membership.lag_days for membership in memberships] == [60, 1]
    assert [membership.acquisition_local_date for membership in memberships] == [
        date(2024, 7, 1),
        date(2024, 8, 29),
    ]


def test_final_test_year_lock_is_explicit_and_membership_enforces_it() -> None:
    with pytest.raises(PermissionError, match="2025"):
        validate_final_test_lock(["2024-08-01", "2025-08-01"], unlock_final_test=False)
    assert validate_final_test_lock(["2025-08-01"], unlock_final_test=True) == (
        date(2025, 8, 1),
    )
    with pytest.raises(PermissionError, match="2025"):
        build_target_window_membership(
            ["2025-08-01"],
            [_key(datetime(2025, 7, 31, 18, tzinfo=UTC))],
            unlock_final_test=False,
        )
    with pytest.raises(PermissionError, match="2025"):
        validate_final_test_lock(["2026-08-01"], unlock_final_test=False)


def test_cohort_selection_prefers_union_coverage_before_newer_baseline() -> None:
    west = box(-118.6, 34.0, -118.5, 34.2)
    east = box(-118.5, 34.0, -118.4, 34.2)
    items = [
        _record("old-west", baseline="04.00", tile="11SLT", geometry=west),
        _record("old-east", baseline="04.00", tile="11SLU", geometry=east),
        _record("new-west-only", baseline="05.10", tile="11SLT", geometry=west),
    ]

    selected = select_reprocessing_cohort(items, aoi_geometry_wgs84=AOI)

    assert selected.processing_baseline == "04.00"
    assert selected.item_ids == ("old-east", "old-west")
    assert selected.union_aoi_coverage_fraction == pytest.approx(1.0, abs=2e-4)


def test_equal_coverage_prefers_highest_numeric_processing_baseline() -> None:
    selected = select_reprocessing_cohort(
        [
            _record("baseline-5-9", baseline="05.09"),
            _record("baseline-5-10", baseline="05.10"),
        ],
        aoi_geometry_wgs84=AOI,
    )

    assert selected.processing_baseline == "05.10"
    assert selected.item_ids == ("baseline-5-10",)


def test_same_baseline_tile_uses_latest_generation_then_smallest_item_id() -> None:
    old = datetime(2024, 7, 7, 1, tzinfo=UTC)
    new = datetime(2024, 7, 7, 3, tzinfo=UTC)
    selected = select_reprocessing_cohort(
        [
            _record("z-old", generation=old),
            _record("b-new", generation=new),
            _record("a-new", generation=new),
        ],
        aoi_geometry_wgs84=AOI,
    )

    assert selected.item_ids == ("a-new",)
    assert selected.generation_time == new


def test_all_cohorts_group_and_sort_distinct_physical_acquisitions() -> None:
    later = datetime(2024, 7, 11, 18, 29, tzinfo=UTC)
    records = [
        _record("later", acquired=later, datatake_stem="GS2A_LATER"),
        _record("first-north", tile="11SLU"),
        _record("first-south", tile="11SLT"),
    ]

    selected = select_all_reprocessing_cohorts(records, aoi_geometry_wgs84=AOI)

    assert len(selected) == 2
    assert selected[0].item_ids == ("first-north", "first-south")
    assert selected[1].item_ids == ("later",)


def _pystac_item(*, cloud_cover: float = 99.0) -> Item:
    item = Item(
        id="S2A_TEST_ITEM",
        geometry=AOI.__geo_interface__,
        bbox=list(AOI.bounds),
        datetime=datetime(2024, 7, 6, 18, 29, tzinfo=UTC),
        properties={
            "platform": "Sentinel-2A",
            "sat:relative_orbit": 27,
            "s2:datatake_id": "GS2A_20240706T182921_047214_N05.10",
            "s2:mgrs_tile": "11SLU",
            "s2:processing_baseline": "05.10",
            "s2:generation_time": "2024-07-07T02:36:21Z",
            "eo:cloud_cover": cloud_cover,
        },
    )
    for asset in REQUIRED_SENTINEL_ASSETS:
        suffix = "xml" if asset == "product-metadata" else "tif"
        item.add_asset(
            asset,
            Asset(
                href=(
                    f"HTTPS://EXAMPLE.TEST/data/{asset}.{suffix}"
                    "?se=tomorrow&sig=ephemeral#fragment"
                )
            ),
        )
    return item


def test_item_parser_records_cloud_only_as_audit_and_canonicalizes_assets() -> None:
    record = sentinel_record_from_item(_pystac_item(cloud_cover=99.9))

    assert record.cloud_cover_percent == pytest.approx(99.9)
    assert record.processing_baseline == "05.10"
    assert all("?" not in href and "#" not in href for _, href in record.asset_hrefs)
    assert dict(record.asset_hrefs)["B02"] == "https://example.test/data/B02.tif"

    incomplete = _pystac_item()
    incomplete.assets.pop("SCL")
    with pytest.raises(ValueError, match="SCL"):
        sentinel_record_from_item(incomplete)


class _FakeSearch:
    def __init__(self, values: tuple[object, ...]) -> None:
        self.values = values

    def items(self):
        return iter(self.values)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> _FakeSearch:
        self.calls.append(kwargs)
        return _FakeSearch(("item-a", "item-b"))


def test_query_is_injectable_and_never_adds_global_cloud_filter() -> None:
    client = _FakeClient()
    result = query_sentinel_items(
        client,
        intersects=AOI,
        datetime_interval="2024-05-01T00:00:00Z/2024-10-31T23:59:59Z",
    )

    assert result == ("item-a", "item-b")
    assert client.calls[0]["collections"] == ["sentinel-2-l2a"]
    assert "query" not in client.calls[0]
    with pytest.raises(ValueError, match="prohibited"):
        query_sentinel_items(
            client,
            intersects=AOI,
            datetime_interval="2024-05-01/2024-10-31",
            global_cloud_cover_max=80.0,
        )
    assert len(client.calls) == 1


def test_semantic_hash_is_canonical_and_changes_with_selected_identity() -> None:
    selected = select_reprocessing_cohort(
        [
            _record("north", tile="11SLU"),
            _record("south", tile="11SLT"),
        ],
        aoi_geometry_wgs84=AOI,
    )
    reordered = replace(
        selected,
        item_ids=tuple(reversed(selected.item_ids)),
        items=tuple(reversed(selected.items)),
    )
    assert sentinel_inventory_semantic_sha256([selected]) == (
        sentinel_inventory_semantic_sha256([reordered])
    )

    signed_first = replace(
        selected.items[0],
        asset_hrefs=tuple(
            (asset, f"{href}?se=tomorrow&sig=ephemeral")
            for asset, href in selected.items[0].asset_hrefs
        ),
    )
    signed = replace(selected, items=(signed_first, *selected.items[1:]))
    assert sentinel_inventory_semantic_sha256([selected]) == (
        sentinel_inventory_semantic_sha256([signed])
    )

    changed_item = replace(
        selected.items[0],
        asset_hrefs=(("B02", "https://example.test/revised/B02.tif"),),
    )
    changed = CohortSelection(
        acquisition_key=selected.acquisition_key,
        processing_baseline=selected.processing_baseline,
        union_aoi_coverage_fraction=selected.union_aoi_coverage_fraction,
        generation_time=selected.generation_time,
        item_ids=selected.item_ids,
        items=(changed_item, *selected.items[1:]),
    )
    assert sentinel_inventory_semantic_sha256([selected]) != (
        sentinel_inventory_semantic_sha256([changed])
    )

    with pytest.raises(ValueError, match="duplicate physical acquisitions"):
        sentinel_inventory_semantic_sha256([selected, selected])


def test_local_query_union_merges_windows_and_converts_dst_boundaries() -> None:
    intervals = build_local_date_query_intervals(
        ["2024-03-15", "2024-03-20", "2024-08-30"],
        unlock_final_test=False,
    )

    assert [(value.start_date, value.end_date) for value in intervals] == [
        (date(2024, 1, 15), date(2024, 3, 19)),
        (date(2024, 7, 1), date(2024, 8, 29)),
    ]
    assert intervals[0].utc_datetime_interval == (
        "2024-01-15T08:00:00Z/2024-03-20T07:00:00Z"
    )
    assert intervals[1].utc_datetime_interval == (
        "2024-07-01T07:00:00Z/2024-08-30T07:00:00Z"
    )


def _inventory_item(
    item_id: str,
    *,
    acquired: datetime,
    datatake_stem: str,
    baseline: str = "05.10",
    generation: datetime = datetime(2024, 8, 31, 1, tzinfo=UTC),
    token: str = "first",
) -> Item:
    item = Item(
        id=item_id,
        geometry=AOI.__geo_interface__,
        bbox=list(AOI.bounds),
        datetime=acquired,
        properties={
            "platform": "Sentinel-2A",
            "sat:relative_orbit": 27,
            "s2:datatake_id": f"{datatake_stem}_N{baseline}",
            "s2:mgrs_tile": "11SLU",
            "s2:processing_baseline": baseline,
            "s2:generation_time": generation.isoformat().replace("+00:00", "Z"),
            "eo:cloud_cover": 100.0,
        },
    )
    for asset in REQUIRED_SENTINEL_ASSETS:
        suffix = "xml" if asset == "product-metadata" else "tif"
        item.add_asset(
            asset,
            Asset(
                href=(
                    f"HTTPS://EXAMPLE.TEST/sentinel/{item_id}/{asset}.{suffix}"
                    f"?st=now&sig={token}#temporary"
                )
            ),
        )
    return item


class _InventoryClient:
    def __init__(self, items: tuple[Item, ...], *, fail: bool = False) -> None:
        self.items = items
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> _FakeSearch:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated network failure")
        return _FakeSearch(self.items)


def _inventory_inputs(tmp_path, *, target_date: str = "2024-08-30"):
    city_path = tmp_path / "city.geojson"
    gpd.GeoDataFrame({"name": ["Los Angeles"]}, geometry=[AOI], crs="EPSG:4326").to_file(
        city_path,
        driver="GeoJSON",
    )
    manifest_path = tmp_path / "primary_overpass_manifest.csv"
    pd.DataFrame(
        {
            "overpass_id": ["test-overpass"],
            "local_date": [target_date],
            "primary_eligible": [True],
        }
    ).to_csv(manifest_path, index=False)
    return city_path, manifest_path


def _inventory_query_items() -> tuple[Item, ...]:
    d_minus_60_old = _inventory_item(
        "S2A_D60_OLD",
        acquired=datetime(2024, 7, 1, 18, 30, tzinfo=UTC),
        datatake_stem="GS2A_20240701T183000_000001",
        baseline="04.00",
        generation=datetime(2024, 7, 2, 1, tzinfo=UTC),
    )
    d_minus_60_new = _inventory_item(
        "S2A_D60_NEW",
        acquired=datetime(2024, 7, 1, 18, 30, tzinfo=UTC),
        datatake_stem="GS2A_20240701T183000_000001",
        baseline="05.10",
        generation=datetime(2024, 7, 3, 1, tzinfo=UTC),
        token="new-first",
    )
    d_minus_60_new_resigned = _inventory_item(
        "S2A_D60_NEW",
        acquired=datetime(2024, 7, 1, 18, 30, tzinfo=UTC),
        datatake_stem="GS2A_20240701T183000_000001",
        baseline="05.10",
        generation=datetime(2024, 7, 3, 1, tzinfo=UTC),
        token="new-second",
    )
    d_minus_1 = _inventory_item(
        "S2A_D1",
        acquired=datetime(2024, 8, 29, 18, 30, tzinfo=UTC),
        datatake_stem="GS2A_20240829T183000_000002",
    )
    broad_endpoint_d0 = _inventory_item(
        "S2A_D0_BROAD_ENDPOINT",
        acquired=datetime(2024, 8, 30, 7, 0, tzinfo=UTC),
        datatake_stem="GS2A_20240830T070000_000003",
    )
    return (
        broad_endpoint_d0,
        d_minus_60_new_resigned,
        d_minus_1,
        d_minus_60_old,
        d_minus_60_new,
    )


def test_canonical_snapshot_strips_sas_without_changing_identity() -> None:
    first, second = _inventory_query_items()[1], _inventory_query_items()[-1]
    first_snapshot = canonical_stac_item_snapshot(first)
    second_snapshot = canonical_stac_item_snapshot(second)

    assert first_snapshot == second_snapshot
    serialized = json.dumps(first_snapshot)
    assert "sig=" not in serialized
    assert "?" not in serialized
    assert "#temporary" not in serialized


def test_artifact_build_serializes_exact_membership_hashes_and_snapshots(tmp_path) -> None:
    city_path, manifest_path = _inventory_inputs(tmp_path)
    output_directory = tmp_path / "manifests" / "sentinel_inventory"
    raw_directory = tmp_path / "data" / "raw" / "sentinel" / "stac_items"
    client = _InventoryClient(_inventory_query_items())

    summary = build_sentinel_inventory_artifacts(
        city_boundary_path=city_path,
        primary_overpass_manifest_path=manifest_path,
        output_directory=output_directory,
        raw_stac_directory=raw_directory,
        client=client,
        query_time_utc=datetime(2024, 9, 1, tzinfo=UTC),
    )

    assert len(client.calls) == 1
    assert "query" not in client.calls[0]
    assert client.calls[0]["datetime"] == (
        "2024-07-01T07:00:00Z/2024-08-30T07:00:00Z"
    )
    assert summary["state"] == "complete"
    assert summary["artifacts_valid"] is True
    assert summary["counts"] == {
        "query_response_items_including_interval_duplicates": 5,
        "unique_stac_items": 4,
        "candidate_physical_acquisitions": 3,
        "selected_physical_acquisitions": 2,
        "selected_items": 2,
        "target_window_memberships": 2,
    }

    acquisitions = pd.read_csv(output_directory / SELECTED_ACQUISITIONS_FILENAME)
    selected_items = pd.read_csv(output_directory / SELECTED_ITEMS_FILENAME)
    membership = pd.read_csv(output_directory / TARGET_WINDOW_MEMBERSHIP_FILENAME)
    assert acquisitions["item_ids"].tolist() == ["S2A_D60_NEW", "S2A_D1"]
    assert selected_items["item_id"].tolist() == ["S2A_D60_NEW", "S2A_D1"]
    assert membership["lag_days"].tolist() == [60, 1]
    assert membership["target_date"].tolist() == ["2024-08-30", "2024-08-30"]
    assert not membership["target_date"].str.startswith("2025").any()

    selected_text = (output_directory / SELECTED_ITEMS_FILENAME).read_text(
        encoding="utf-8"
    )
    assert "sig=" not in selected_text
    assert "?" not in selected_text
    snapshot_files = sorted(raw_directory.glob("*.json"))
    assert len(snapshot_files) == 4
    assert any("D60_OLD" in path.name for path in snapshot_files)
    assert any("D0_BROAD_ENDPOINT" in path.name for path in snapshot_files)
    assert all("sig=" not in path.read_text(encoding="utf-8") for path in snapshot_files)

    for filename in (
        SELECTED_ACQUISITIONS_FILENAME,
        SELECTED_ITEMS_FILENAME,
        TARGET_WINDOW_MEMBERSHIP_FILENAME,
    ):
        record = summary["output_files"][filename]
        assert record["sha256"] == sha256_file(output_directory / filename)
        assert record["rows"] == len(pd.read_csv(output_directory / filename))
    on_disk_summary = json.loads(
        (output_directory / INVENTORY_SUMMARY_FILENAME).read_text(encoding="utf-8")
    )
    assert on_disk_summary == summary


def test_artifact_semantic_and_snapshot_hashes_ignore_order_and_sas(tmp_path) -> None:
    city_path, manifest_path = _inventory_inputs(tmp_path)
    items = _inventory_query_items()
    fixed_time = datetime(2024, 9, 1, tzinfo=UTC)

    first = build_sentinel_inventory_artifacts(
        city_boundary_path=city_path,
        primary_overpass_manifest_path=manifest_path,
        output_directory=tmp_path / "first_manifest",
        raw_stac_directory=tmp_path / "first_raw",
        client=_InventoryClient(items),
        query_time_utc=fixed_time,
    )
    reordered_and_resigned = tuple(
        _inventory_item(
            item.id,
            acquired=item.datetime,
            datatake_stem=str(item.properties["s2:datatake_id"]).rsplit("_N", 1)[0],
            baseline=str(item.properties["s2:processing_baseline"]),
            generation=datetime.fromisoformat(
                str(item.properties["s2:generation_time"]).replace("Z", "+00:00")
            ),
            token="entirely-different-sas",
        )
        for item in reversed(items)
    )
    second = build_sentinel_inventory_artifacts(
        city_boundary_path=city_path,
        primary_overpass_manifest_path=manifest_path,
        output_directory=tmp_path / "second_manifest",
        raw_stac_directory=tmp_path / "second_raw",
        client=_InventoryClient(reordered_and_resigned),
        query_time_utc=fixed_time,
    )

    assert first["sentinel_inventory_semantic_sha256"] == (
        second["sentinel_inventory_semantic_sha256"]
    )
    assert first["raw_stac_snapshots"]["set_sha256"] == (
        second["raw_stac_snapshots"]["set_sha256"]
    )
    for filename in (
        SELECTED_ACQUISITIONS_FILENAME,
        SELECTED_ITEMS_FILENAME,
        TARGET_WINDOW_MEMBERSHIP_FILENAME,
    ):
        assert first["output_files"][filename]["sha256"] == (
            second["output_files"][filename]["sha256"]
        )


def test_summary_commit_marker_is_removed_before_network_failure(tmp_path) -> None:
    city_path, manifest_path = _inventory_inputs(tmp_path)
    output_directory = tmp_path / "sentinel_inventory"
    output_directory.mkdir()
    summary_path = output_directory / INVENTORY_SUMMARY_FILENAME
    summary_path.write_text('{"state":"stale"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated network failure"):
        build_sentinel_inventory_artifacts(
            city_boundary_path=city_path,
            primary_overpass_manifest_path=manifest_path,
            output_directory=output_directory,
            raw_stac_directory=tmp_path / "raw",
            client=_InventoryClient((), fail=True),
        )

    assert not summary_path.exists()


def test_existing_raw_snapshot_is_never_overwritten(tmp_path) -> None:
    city_path, manifest_path = _inventory_inputs(tmp_path)
    output_directory = tmp_path / "sentinel_inventory"
    raw_directory = tmp_path / "raw"
    build_sentinel_inventory_artifacts(
        city_boundary_path=city_path,
        primary_overpass_manifest_path=manifest_path,
        output_directory=output_directory,
        raw_stac_directory=raw_directory,
        client=_InventoryClient(_inventory_query_items()),
    )
    existing_snapshot = sorted(raw_directory.glob("*.json"))[0]
    existing_snapshot.write_text("preserve-this-original", encoding="utf-8")

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        build_sentinel_inventory_artifacts(
            city_boundary_path=city_path,
            primary_overpass_manifest_path=manifest_path,
            output_directory=output_directory,
            raw_stac_directory=raw_directory,
            client=_InventoryClient(_inventory_query_items()),
        )

    assert existing_snapshot.read_text(encoding="utf-8") == "preserve-this-original"
    assert not (output_directory / INVENTORY_SUMMARY_FILENAME).exists()


def test_locked_2025_manifest_never_queries_or_commits(tmp_path) -> None:
    city_path, manifest_path = _inventory_inputs(tmp_path, target_date="2025-08-30")
    output_directory = tmp_path / "sentinel_inventory"
    output_directory.mkdir()
    summary_path = output_directory / INVENTORY_SUMMARY_FILENAME
    summary_path.write_text('{"state":"stale"}', encoding="utf-8")
    client = _InventoryClient(())

    with pytest.raises(PermissionError, match="2025"):
        build_sentinel_inventory_artifacts(
            city_boundary_path=city_path,
            primary_overpass_manifest_path=manifest_path,
            output_directory=output_directory,
            raw_stac_directory=tmp_path / "raw",
            client=client,
            unlock_final_test=False,
        )

    assert client.calls == []
    assert not summary_path.exists()
