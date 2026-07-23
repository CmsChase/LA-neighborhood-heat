import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pystac import Asset, Item
from shapely.geometry import box

from la_heat.final_test_sentinel_inventory import (
    CALIBRATION_CONTRACT_ID,
    CALIBRATION_CONTRACT_PROPERTY,
    EARTH_SEARCH_ASSET_ALIASES,
    FinalTestSentinelInventoryError,
    _allow_audited_product_metadata_s3,
    adapt_earth_search_item,
    build_final_test_sentinel_inventory_artifacts,
)
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    geometry_semantic_sha256,
    sha256_file,
)
from la_heat.sentinel_inventory import (
    physical_acquisition_key,
    sentinel_record_from_item,
)

AOI = box(-118.6, 34.0, -118.4, 34.2)


class _Search:
    def __init__(self, items: tuple[Item, ...]) -> None:
        self._items = items

    def items(self) -> tuple[Item, ...]:
        return self._items


class _Client:
    def __init__(self, items: tuple[Item, ...]) -> None:
        self._items = items
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> _Search:
        self.calls.append(kwargs)
        return _Search(self._items)


def _write_committed_json(path: Path, payload: dict[str, object]) -> dict[str, object]:
    committed = dict(payload)
    committed["commit_sha256"] = canonical_sha256(committed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(committed, indent=2), encoding="utf-8")
    return committed


def _formal_lock(path: Path) -> dict[str, object]:
    return _write_committed_json(
        path,
        {
            "state": "frozen_for_one_time_2025_evaluation",
            "formal_model_lock_written": True,
            "final_test_year": 2025,
            "final_test_locked": True,
            "final_test_unlocked": False,
            "final_test_used": False,
            "final_test_values_read": False,
            "contains_final_test_year": False,
            "one_time_final_evaluation_authorized": False,
            "models": {"B1": {}, "M2": {}},
        },
    )


def _earth_search_item() -> Item:
    item = Item(
        id="S2A_11SLT_20250829_0_L2A",
        geometry=AOI.__geo_interface__,
        bbox=list(AOI.bounds),
        datetime=datetime(2025, 8, 29, 18, 30, tzinfo=UTC),
        properties={
            "platform": "sentinel-2a",
            "grid:code": "MGRS-11SLT",
            "s2:product_uri": ("S2A_MSIL2A_20250829T183000_N0511_R027_T11SLT_20250829T220000"),
            "s2:datatake_id": "GS2A_20250829T183000_000001_N05.11",
            "s2:processing_baseline": "05.11",
            "s2:generation_time": "2025-08-29T22:00:00Z",
            "eo:cloud_cover": 100.0,
            "earthsearch:boa_offset_applied": True,
        },
    )
    for source in EARTH_SEARCH_ASSET_ALIASES.values():
        if source == "product_metadata":
            item.add_asset(
                source,
                Asset(href=("s3://sentinel-s2-l2a/tiles/11/S/LT/2025/8/29/0/product_metadata.xml")),
            )
            continue
        extra_fields = {}
        if source != "scl":
            extra_fields["raster:bands"] = [
                {"scale": 0.0001, "offset": -0.1, "data_type": "uint16"}
            ]
        item.add_asset(
            source,
            Asset(
                href=(
                    f"HTTPS://EXAMPLE.TEST/earth-search/{source}.tif?signature=temporary#fragment"
                ),
                extra_fields=extra_fields,
            ),
        )
    return item


def _source_inventory(tmp_path: Path, *, year: int = 2025) -> dict[str, Path]:
    formal_path = tmp_path / "manifests/model_lock/MODEL_LOCK.json"
    formal = _formal_lock(formal_path)
    directory = tmp_path / "manifests/final_test_2025/landsat_inventory"
    directory.mkdir(parents=True)
    city_path = tmp_path / "manifests/target_inventory/city_boundary.geojson"
    city_path.parent.mkdir(parents=True)
    city = gpd.GeoDataFrame({"name": ["Los Angeles"]}, geometry=[AOI], crs="EPSG:4326")
    city.to_file(city_path, driver="GeoJSON")

    primary_path = directory / "primary_overpass_manifest.csv"
    primary = pd.DataFrame(
        {
            "overpass_id": ["final-overpass"],
            "local_date": [f"{year}-08-30"],
            "primary_eligible": [True],
        }
    )
    primary.to_csv(primary_path, index=False)
    geoids = [f"06037{index:06d}" for index in range(1_096)]
    key_path = directory / "target_blind_key_universe.parquet"
    keys = pd.DataFrame(
        {
            "tract_geoid": pd.Series(geoids, dtype="string"),
            "target_date": pd.to_datetime([f"{year}-08-30"] * len(geoids)),
            "overpass_id": ["final-overpass"] * len(geoids),
            "platform": ["landsat-9"] * len(geoids),
            "spatial_block": [f"block-{index % 71:02d}" for index in range(len(geoids))],
            "latitude_quartile": [index % 4 + 1 for index in range(len(geoids))],
            "longitude_quartile": [index % 4 + 1 for index in range(len(geoids))],
        }
    )
    keys.to_parquet(key_path, index=False)
    formal_sha = sha256_file(formal_path)
    summary = {
        "schema_version": 1,
        "algorithm_version": "test-final-landsat-inventory",
        "state": "target_blind_inventory_frozen",
        "final_test_year": 2025,
        "target_blind": True,
        "target_assets_opened": False,
        "target_or_qa_values_read": False,
        "labels_created": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "global_scene_cloud_cover_filter": False,
        "primary_overpass_count": 1,
        "tract_count": 1_096,
        "key_count": len(keys),
        "formal_model_lock": {
            "path": str(formal_path),
            "sha256": formal_sha,
            "commit_sha256": formal["commit_sha256"],
        },
        "frozen_support": {
            "city_boundary_path": str(city_path),
            "city_boundary_sha256": sha256_file(city_path),
            "city_boundary_geometry_sha256": geometry_semantic_sha256(city),
            "tract_count": 1_096,
        },
        "semantic_hashes": {
            "key_universe": canonical_frame_sha256(keys, sort_by=["target_date", "tract_geoid"])
        },
        "output_files": {
            primary_path.name: {
                "path": str(primary_path),
                "sha256": sha256_file(primary_path),
                "bytes": primary_path.stat().st_size,
                "rows": len(primary),
            },
            key_path.name: {
                "path": str(key_path),
                "sha256": sha256_file(key_path),
                "bytes": key_path.stat().st_size,
                "rows": len(keys),
            },
        },
    }
    landsat_path = directory / "LANDSAT_INVENTORY.json"
    _write_committed_json(landsat_path, summary)
    return {
        "formal": formal_path,
        "landsat": directory,
        "primary": primary_path,
        "output": tmp_path / "manifests/final_test_2025/sentinel_inventory",
        "raw": tmp_path / "data/raw/final_test_2025/sentinel/stac_items",
    }


def _build(paths: dict[str, Path], client: _Client) -> dict[str, object]:
    return build_final_test_sentinel_inventory_artifacts(
        formal_lock_path=paths["formal"],
        landsat_inventory_directory=paths["landsat"],
        output_directory=paths["output"],
        raw_stac_directory=paths["raw"],
        client=client,
        query_time_utc=datetime(2025, 11, 1, tzinfo=UTC),
    )


def test_earth_search_adapter_maps_assets_tile_and_orbit() -> None:
    adapted = adapt_earth_search_item(_earth_search_item())
    assert set(adapted.assets) == set(EARTH_SEARCH_ASSET_ALIASES)
    assert adapted.properties["s2:mgrs_tile"] == "11SLT"
    assert adapted.properties["sat:relative_orbit"] == "027"
    assert adapted.properties["platform"] == "sentinel-2a"
    contract = adapted.properties[CALIBRATION_CONTRACT_PROPERTY]
    assert contract["id"] == CALIBRATION_CONTRACT_ID
    assert contract["formula"] == "reflectance = DN * scale + offset"
    assert contract["bands"]["B02"]["offset"] == -0.1
    assert adapted.assets["product-metadata"].href.startswith("s3://sentinel-s2-l2a/")


def test_adjacent_tiles_share_one_datatake_physical_acquisition() -> None:
    south = _earth_search_item()
    north = _earth_search_item()
    north.id = "S2A_11SLU_20250829_0_L2A"
    north.datetime = datetime(2025, 8, 29, 18, 30, 14, tzinfo=UTC)
    north.properties["grid:code"] = "MGRS-11SLU"
    north.properties["s2:product_uri"] = str(
        north.properties["s2:product_uri"]
    ).replace("T11SLT", "T11SLU")
    north.assets["product_metadata"].href = str(
        north.assets["product_metadata"].href
    ).replace("/S/LT/", "/S/LU/")

    south_adapted = adapt_earth_search_item(south)
    north_adapted = adapt_earth_search_item(north)
    expected = datetime(2025, 8, 29, 18, 30, tzinfo=UTC)
    assert south_adapted.datetime == expected
    assert north_adapted.datetime == expected
    assert north_adapted.properties["la_heat:tile_datetime_utc"] == (
        "2025-08-29T18:30:14Z"
    )
    with _allow_audited_product_metadata_s3():
        assert physical_acquisition_key(
            sentinel_record_from_item(south_adapted)
        ) == physical_acquisition_key(sentinel_record_from_item(north_adapted))


def test_build_is_target_blind_exact_2025_and_isolated(tmp_path: Path) -> None:
    paths = _source_inventory(tmp_path)
    client = _Client((_earth_search_item(),))
    payload = _build(paths, client)

    assert len(client.calls) == 1
    assert client.calls[0]["collections"] == ["sentinel-2-l2a"]
    assert client.calls[0]["query"] == {"platform": {"in": ["sentinel-2a", "sentinel-2b"]}}
    assert payload["stac_provider"] == "Element 84 Earth Search"
    assert payload["earth_search_adapter"]["asset_aliases"] == (EARTH_SEARCH_ASSET_ALIASES)
    assert payload["target_or_qa_tables_read"] == []
    assert payload["target_or_qa_values_read"] is False
    assert payload["target_assets_opened"] is False
    assert payload["fitted_models_loaded"] is False
    assert payload["model_scores_read"] is False
    assert payload["exact_final_test_year"] is True

    membership = pd.read_csv(paths["output"] / "target_window_membership.csv")
    assert membership["target_date"].tolist() == ["2025-08-30"]
    assert membership["lag_days"].tolist() == [1]
    base = json.loads((paths["output"] / "inventory_summary.json").read_text(encoding="utf-8"))
    assert base["unlock_final_test"] is True
    assert Path(base["raw_stac_snapshots"]["directory"]).resolve() == paths["raw"].resolve()
    assert list(paths["raw"].glob("*.json"))
    selected = pd.read_csv(paths["output"] / "selected_items.csv")
    assert selected.loc[0, "asset_product_metadata_href"].startswith("s3://sentinel-s2-l2a/")


def test_adapter_uses_raster_contract_when_item_offset_flag_is_false() -> None:
    item = _earth_search_item()
    item.properties["earthsearch:boa_offset_applied"] = False
    adapted = adapt_earth_search_item(item)
    contract = adapted.properties[CALIBRATION_CONTRACT_PROPERTY]
    assert contract["earthsearch:boa_offset_applied"] is False
    assert contract["bands"]["B02"]["scale"] == 0.0001
    assert contract["bands"]["B02"]["offset"] == -0.1


def test_adapter_fails_closed_without_boolean_offset_audit_flag() -> None:
    item = _earth_search_item()
    item.properties.pop("earthsearch:boa_offset_applied")
    with pytest.raises(FinalTestSentinelInventoryError, match="boolean boa_offset_applied"):
        adapt_earth_search_item(item)


def test_adapter_never_fabricates_product_metadata_https() -> None:
    item = _earth_search_item()
    original = item.assets["product_metadata"].href
    adapted = adapt_earth_search_item(item)
    assert adapted.assets["product-metadata"].href == original


def test_non_2025_source_is_rejected_before_stac_query(tmp_path: Path) -> None:
    paths = _source_inventory(tmp_path, year=2026)
    client = _Client((_earth_search_item(),))
    with pytest.raises(FinalTestSentinelInventoryError, match="exactly 2025"):
        _build(paths, client)
    assert client.calls == []
    assert not paths["output"].exists()


def test_tampered_primary_lock_is_rejected_before_stac_query(tmp_path: Path) -> None:
    paths = _source_inventory(tmp_path)
    paths["primary"].write_text("changed", encoding="utf-8")
    client = _Client((_earth_search_item(),))
    with pytest.raises(FinalTestSentinelInventoryError, match="byte lock"):
        _build(paths, client)
    assert client.calls == []


def test_invalid_formal_lock_is_rejected_before_stac_query(tmp_path: Path) -> None:
    paths = _source_inventory(tmp_path)
    formal = json.loads(paths["formal"].read_text(encoding="utf-8"))
    formal.pop("commit_sha256")
    formal["final_test_unlocked"] = True
    _write_committed_json(paths["formal"], formal)
    client = _Client((_earth_search_item(),))
    with pytest.raises(Exception, match="untouched locked-2025"):
        _build(paths, client)
    assert client.calls == []


def test_committed_result_is_idempotent_without_new_query(tmp_path: Path) -> None:
    paths = _source_inventory(tmp_path)
    first_client = _Client((_earth_search_item(),))
    first = _build(paths, first_client)
    second_client = _Client(())
    second = _build(paths, second_client)
    assert first["commit_sha256"] == second["commit_sha256"]
    assert second_client.calls == []
