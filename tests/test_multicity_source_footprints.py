from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import shapely
from shapely.geometry import mapping

from la_heat.multicity import source_footprints as footprints
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.source_footprints import (
    ACCESS_CONTRACT,
    LANDSAT_COLLECTION,
    LANDSAT_PROPERTIES,
    SENTINEL_COLLECTION,
    SENTINEL_FIELDS,
    SENTINEL_PROPERTIES,
    SourceFootprintError,
    _read_source_config,
    build_daymet_cell_table,
    build_optical_item_table,
    build_optical_unit_table,
    derive_daymet_index_window,
    derive_srtm_tiles,
    fetch_public_stac_metadata,
    local_date_interval_to_utc,
    probe_terrain_heads,
)
from la_heat.static_sources import OPEN_TOPOGRAPHY_SRTM_BASE_URL
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

ROOT = Path(__file__).parents[1]
PLAN_CONFIG = ROOT / "configs" / "multicity" / "experiment.toml"
SOURCE_CONFIG = ROOT / "configs" / "multicity" / "source_footprints_v1.toml"
STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
STAC_SEARCH = f"{STAC_API}/search"
PHOENIX_BBOX = (-112.3240760724, 33.2902650923, -111.9255320035, 33.9183961768)


class _FakeResponse:
    def __init__(
        self,
        payload: object | None = None,
        *,
        url: str = STAC_SEARCH,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.url = url
        self.status_code = status_code
        self.headers = {} if headers is None else dict(headers)
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"fake HTTP status {self.status_code}")

    def json(self) -> object:
        return self._payload

    def close(self) -> None:
        self.closed = True


class _PostOnlyClient:
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = list(payloads)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, dict(kwargs)))
        if not self._payloads:
            raise AssertionError("unexpected extra POST")
        return _FakeResponse(self._payloads.pop(0), url=url)

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        raise AssertionError(f"unexpected GET {url} {kwargs}")

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        raise AssertionError(f"unexpected HEAD {url} {kwargs}")


class _HeadOnlyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        raise AssertionError(f"unexpected POST {url} {kwargs}")

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        raise AssertionError(f"unexpected GET {url} {kwargs}")

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, dict(kwargs)))
        return _FakeResponse(
            url=url,
            headers={
                "Content-Length": "123456",
                "Content-Type": "image/tiff; charset=binary",
                "ETag": '"fake-etag"',
            },
        )


def _boundary(geometry: Any, *, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"city_id": ["phoenix_az"]},
        geometry=[geometry],
        crs=crs,
    )


def _sentinel_feature(
    item_id: str,
    geometry: Any,
    *,
    acquired: str = "2025-06-15T18:00:00Z",
) -> dict[str, object]:
    return {
        "id": item_id,
        "collection": SENTINEL_COLLECTION,
        "geometry": mapping(geometry),
        "bbox": list(geometry.bounds),
        "properties": {
            "datetime": acquired,
            "platform": "sentinel-2a",
            "s2:mgrs_tile": "12SVC",
        },
    }


def _landsat_feature(item_id: str, geometry: Any) -> dict[str, object]:
    return {
        "id": item_id,
        "collection": LANDSAT_COLLECTION,
        "geometry": mapping(geometry),
        "bbox": list(geometry.bounds),
        "properties": {
            "datetime": "2025-06-15T18:00:00Z",
            "platform": "landsat-9",
            "landsat:wrs_path": "37",
            "landsat:wrs_row": "37",
            "landsat:collection_category": "T1",
            "landsat:correction": "L2SP",
        },
    }


def _stac_body(*, token: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "collections": [SENTINEL_COLLECTION],
        "bbox": list(PHOENIX_BBOX),
        "datetime": "2025-03-02T07:00:00Z/2025-10-31T07:00:00Z",
        "limit": 100,
        "fields": {
            "include": list(SENTINEL_FIELDS),
            "exclude": ["assets", "links"],
        },
        "query": {"platform": {"in": ["sentinel-2a"]}},
    }
    if token is not None:
        body["token"] = token
    return body


def _fetch_sentinel(client: _PostOnlyClient):
    return fetch_public_stac_metadata(
        client,
        api=STAC_API,
        collection=SENTINEL_COLLECTION,
        bbox_wgs84=PHOENIX_BBOX,
        datetime_interval="2025-03-02T07:00:00Z/2025-10-31T07:00:00Z",
        fields=SENTINEL_FIELDS,
        properties=SENTINEL_PROPERTIES,
        page_limit=100,
        query={"platform": {"in": ["sentinel-2a"]}},
    )


def _daymet_entry(variable: str, sequence: int) -> tuple[dict[str, object], dict[str, object]]:
    concept_id = f"G{9000000000 + sequence}-ORNL_CLOUD"
    title = f"Daymet_Daily_V4R1.daymet_v4_daily_na_{variable}_2025.nc"
    data_url = (
        "https://data.ornldaac.earthdata.nasa.gov/"
        f"protected/daymet/Daymet_Daily_V4R1/data/{title}"
    )
    opendap_url = (
        "https://opendap.earthdata.nasa.gov/collections/"
        f"C2532426483-ORNL_CLOUD/granules/{concept_id}"
    )
    updated_at = f"2025-01-{sequence + 1:02d}T00:00:00Z"
    size_mb = 100.0 + sequence
    raw = {
        "id": concept_id,
        "title": title,
        "granule_size": str(size_mb),
        "updated": updated_at,
        "links": [
            {
                "rel": "http://esipfed.org/ns/fedsearch/1.1/data#",
                "href": data_url,
            },
            {
                "rel": "http://esipfed.org/ns/fedsearch/1.1/service#",
                "href": opendap_url,
            },
        ],
    }
    parsed = {
        "concept_id": concept_id,
        "title": title,
        "variable": variable,
        "year": 2025,
        "size_mb": size_mb,
        "updated_at": updated_at,
        "https_url": data_url,
        "opendap_url": opendap_url,
    }
    return raw, parsed


def _daymet_payload_and_frame() -> tuple[dict[str, object], pd.DataFrame]:
    entries: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    for sequence, variable in enumerate(DEFAULT_DAYMET_VARIABLES, start=1):
        raw, parsed = _daymet_entry(variable, sequence)
        entries.append(raw)
        rows.append(parsed)
    entries.append(
        {
            "id": "G9999999999-ORNL_CLOUD",
            "title": "unrelated_collection_metadata.txt",
        }
    )
    expected = pd.DataFrame(rows).sort_values("variable", kind="stable").reset_index(
        drop=True
    )
    return {"feed": {"entry": entries}}, expected


def test_config_and_local_dates_use_the_exact_phoenix_utc_interval() -> None:
    plan = load_multicity_plan(PLAN_CONFIG)
    source_config = _read_source_config(SOURCE_CONFIG, plan)

    assert local_date_interval_to_utc(
        date(2025, 5, 1),
        date(2025, 10, 31),
        "America/Phoenix",
    ) == "2025-05-01T07:00:00Z/2025-11-01T07:00:00Z"
    assert source_config["landsat"]["local_start_date"] == "2025-05-01"
    assert source_config["landsat"]["local_end_date"] == "2025-10-31"
    assert source_config["sentinel"]["local_start_date"] == "2025-03-02"
    assert source_config["sentinel"]["local_end_date"] == "2025-10-30"
    assert source_config["terrain"]["probe_method"] == "HEAD"


def test_stac_uses_allow_list_post_pagination_and_deduplicates_exact_items() -> None:
    first = _sentinel_feature("S2-first", shapely.box(-112.2, 33.4, -112.0, 33.6))
    second = _sentinel_feature("S2-second", shapely.box(-112.1, 33.5, -111.9, 33.7))
    next_body = _stac_body(token="page-2")
    client = _PostOnlyClient(
        [
            {
                "type": "FeatureCollection",
                "numberReturned": 1,
                "features": [first],
                "links": [
                    {
                        "rel": "next",
                        "method": "POST",
                        "href": STAC_SEARCH,
                        "body": next_body,
                    }
                ],
            },
            {
                "type": "FeatureCollection",
                "numberReturned": 2,
                "features": [first, second],
                "links": [],
            },
        ]
    )

    items, pages, summary = _fetch_sentinel(client)

    assert [item["id"] for item in items] == ["S2-first", "S2-second"]
    assert len(pages) == 2
    assert summary["query_response_items"] == 3
    assert summary["unique_items"] == 2
    assert summary["duplicate_items"] == 1
    assert summary["pagination_exhausted"] is True
    assert summary["assets_excluded"] is True
    assert [url for url, _ in client.calls] == [STAC_SEARCH, STAC_SEARCH]
    assert client.calls[0][1] == {
        "json": _stac_body(),
        "timeout": (30.0, 120.0),
    }
    assert client.calls[1][1] == {
        "json": next_body,
        "timeout": (30.0, 120.0),
    }


def test_stac_rejects_conflicting_duplicate_metadata_and_returned_assets() -> None:
    first = _sentinel_feature("S2-duplicate", shapely.box(-112.2, 33.4, -112.0, 33.6))
    conflict = _sentinel_feature(
        "S2-duplicate",
        shapely.box(-112.1, 33.4, -111.9, 33.6),
    )
    first_page = {
        "type": "FeatureCollection",
        "features": [first],
        "links": [
            {
                "rel": "next",
                "method": "POST",
                "href": STAC_SEARCH,
                "body": _stac_body(token="page-2"),
            }
        ],
    }

    with pytest.raises(SourceFootprintError, match="Conflicting STAC metadata"):
        _fetch_sentinel(
            _PostOnlyClient(
                [
                    first_page,
                    {
                        "type": "FeatureCollection",
                        "features": [conflict],
                        "links": [],
                    },
                ]
            )
        )

    item_with_assets = dict(first)
    item_with_assets["assets"] = {}
    with pytest.raises(SourceFootprintError, match="exposed item assets or links"):
        _fetch_sentinel(
            _PostOnlyClient(
                [
                    {
                        "type": "FeatureCollection",
                        "features": [item_with_assets],
                        "links": [],
                    }
                ]
            )
        )


def test_stac_raw_replay_checks_chain_counts_deduplication_and_item_ids() -> None:
    first = _sentinel_feature("S2-first", shapely.box(-112.2, 33.4, -112.0, 33.6))
    second = _sentinel_feature("S2-second", shapely.box(-112.1, 33.5, -111.9, 33.7))
    client = _PostOnlyClient(
        [
            {
                "type": "FeatureCollection",
                "numberReturned": 1,
                "features": [first],
                "links": [
                    {
                        "rel": "next",
                        "method": "POST",
                        "href": STAC_SEARCH,
                        "body": _stac_body(token="page-2"),
                    }
                ],
            },
            {
                "type": "FeatureCollection",
                "numberReturned": 2,
                "features": [first, second],
                "links": [],
            },
        ]
    )
    output_items, pages, query_record = _fetch_sentinel(client)

    replayed = footprints._replay_stac_pages(
        pages,
        collection=SENTINEL_COLLECTION,
        properties=SENTINEL_PROPERTIES,
        query_record=query_record,
    )

    assert [item["id"] for item in replayed] == [
        item["id"] for item in output_items
    ]
    replayed_table = build_optical_item_table(
        replayed,
        source="sentinel_mgrs",
        collection=SENTINEL_COLLECTION,
        expected_properties=SENTINEL_PROPERTIES,
        allowed_platforms=["sentinel-2a", "sentinel-2b", "sentinel-2c"],
        local_start_date=date(2025, 3, 2),
        local_end_date=date(2025, 10, 30),
        timezone="America/Phoenix",
        city_boundary=_boundary(shapely.box(*PHOENIX_BBOX)),
        analysis_crs="EPSG:5070",
    )
    footprints._require_replayed_frame(
        replayed_table,
        replayed_table.copy(),
        sort_by=["item_id"],
        label="sentinel_items",
    )
    corrupted_output = replayed_table.copy()
    corrupted_output.loc[0, "platform"] = "sentinel-2b"
    with pytest.raises(SourceFootprintError, match="does not replay"):
        footprints._require_replayed_frame(
            corrupted_output,
            replayed_table,
            sort_by=["item_id"],
            label="sentinel_items",
        )

    broken_chain = deepcopy(pages)
    broken_chain[0]["links"] = []
    with pytest.raises(SourceFootprintError):
        footprints._replay_stac_pages(
            broken_chain,
            collection=SENTINEL_COLLECTION,
            properties=SENTINEL_PROPERTIES,
            query_record=query_record,
        )

    wrong_count = dict(query_record)
    wrong_count["duplicate_items"] = 0
    with pytest.raises(SourceFootprintError):
        footprints._replay_stac_pages(
            pages,
            collection=SENTINEL_COLLECTION,
            properties=SENTINEL_PROPERTIES,
            query_record=wrong_count,
        )

    conflicting_duplicate = deepcopy(pages)
    conflicting_duplicate[1]["features"][0] = _sentinel_feature(
        "S2-first",
        shapely.box(-112.0, 33.4, -111.8, 33.6),
    )
    with pytest.raises(SourceFootprintError):
        footprints._replay_stac_pages(
            conflicting_duplicate,
            collection=SENTINEL_COLLECTION,
            properties=SENTINEL_PROPERTIES,
            query_record=query_record,
        )


def test_wrs_and_mgrs_footprints_require_positive_area_not_boundary_touch() -> None:
    city = _boundary(shapely.box(-112.2, 33.4, -112.0, 33.6))
    positive = shapely.box(-112.25, 33.45, -112.1, 33.55)
    touching = shapely.box(-112.0, 33.45, -111.9, 33.55)

    landsat = build_optical_item_table(
        [
            _landsat_feature("LC09-positive", positive),
            _landsat_feature("LC09-touching", touching),
        ],
        source="landsat_wrs",
        collection=LANDSAT_COLLECTION,
        expected_properties=LANDSAT_PROPERTIES,
        allowed_platforms=["landsat-8", "landsat-9"],
        local_start_date=date(2025, 5, 1),
        local_end_date=date(2025, 10, 31),
        timezone="America/Phoenix",
        city_boundary=city,
        analysis_crs="EPSG:5070",
    )
    sentinel = build_optical_item_table(
        [
            _sentinel_feature("S2-positive", positive),
            _sentinel_feature("S2-touching", touching),
        ],
        source="sentinel_mgrs",
        collection=SENTINEL_COLLECTION,
        expected_properties=SENTINEL_PROPERTIES,
        allowed_platforms=["sentinel-2a", "sentinel-2b", "sentinel-2c"],
        local_start_date=date(2025, 3, 2),
        local_end_date=date(2025, 10, 30),
        timezone="America/Phoenix",
        city_boundary=city,
        analysis_crs="EPSG:5070",
    )

    assert landsat[["item_id", "unit_id"]].to_records(index=False).tolist() == [
        ("LC09-positive", "WRS2-037037")
    ]
    assert sentinel[["item_id", "unit_id"]].to_records(index=False).tolist() == [
        ("S2-positive", "MGRS-12SVC")
    ]
    assert landsat["city_overlap_area_m2"].gt(0).all()
    assert sentinel["city_overlap_area_m2"].gt(0).all()

    units = build_optical_unit_table(
        [landsat, sentinel],
        city_boundary=city,
        analysis_crs="EPSG:5070",
    )
    assert units["unit_id"].tolist() == ["WRS2-037037", "MGRS-12SVC"]
    assert units["city_overlap_area_m2"].gt(0).all()


def test_phoenix_bbox_daymet_window_and_cells_are_minimal_and_positive() -> None:
    window = derive_daymet_index_window(PHOENIX_BBOX, halo_cells=1)

    assert window["minimal_y_indices_inclusive"] == [5815, 5887]
    assert window["minimal_x_indices_inclusive"] == [3454, 3499]
    assert window["y_indices_inclusive"] == [5814, 5888]
    assert window["x_indices_inclusive"] == [3453, 3500]
    assert window["window_shape"] == [75, 48]
    assert window["window_cell_count"] == 3600
    assert window["candidate_grid_cells_only"] is True

    cells = build_daymet_cell_table(
        window,
        city_boundary=_boundary(shapely.box(*PHOENIX_BBOX)),
    )
    assert cells["city_overlap_area_m2"].gt(0).all()
    assert cells["daymet_row"].between(5815, 5887).all()
    assert cells["daymet_col"].between(3454, 3499).all()
    assert (cells["city_overlap_fraction_of_cell"] > 0).all()
    assert (cells["city_overlap_fraction_of_cell"] <= 1).all()


def test_daymet_raw_payload_parser_reconstructs_the_exact_granule_table() -> None:
    raw_payload, expected = _daymet_payload_and_frame()

    actual, entry_count = footprints._parse_daymet_granule_payload(
        raw_payload,
        year=2025,
        variables=DEFAULT_DAYMET_VARIABLES,
    )

    assert entry_count == 7
    pd.testing.assert_frame_equal(actual, expected)
    footprints._require_replayed_frame(
        expected,
        actual,
        sort_by=["variable"],
        label="daymet_granules",
    )
    corrupted_output = expected.copy()
    corrupted_output.loc[0, "concept_id"] = "G1234567890-ORNL_CLOUD"
    with pytest.raises(SourceFootprintError, match="does not replay"):
        footprints._require_replayed_frame(
            corrupted_output,
            actual,
            sort_by=["variable"],
            label="daymet_granules",
        )

    missing = deepcopy(raw_payload)
    missing["feed"]["entry"] = missing["feed"]["entry"][:-2]
    with pytest.raises(SourceFootprintError):
        footprints._parse_daymet_granule_payload(
            missing,
            year=2025,
            variables=DEFAULT_DAYMET_VARIABLES,
        )

    duplicate = deepcopy(raw_payload)
    duplicate["feed"]["entry"].append(deepcopy(duplicate["feed"]["entry"][0]))
    with pytest.raises(SourceFootprintError):
        footprints._parse_daymet_granule_payload(
            duplicate,
            year=2025,
            variables=DEFAULT_DAYMET_VARIABLES,
        )


def test_srtm_tiles_are_exact_and_terrain_probes_use_head_only() -> None:
    tiles = derive_srtm_tiles(
        _boundary(shapely.box(*PHOENIX_BBOX)),
        analysis_crs="EPSG:5070",
        halo_m=30,
        base_url=OPEN_TOPOGRAPHY_SRTM_BASE_URL,
        filename_suffix=".tif",
    )

    assert tiles["tile_id"].tolist() == ["N33W112", "N33W113"]
    assert tiles["filename"].tolist() == ["N33W112.tif", "N33W113.tif"]
    assert tiles["probe_method"].eq("HEAD").all()
    assert tiles["payload_bytes_read"].eq(0).all()

    client = _HeadOnlyClient()
    probed, records = probe_terrain_heads(client, tiles)

    assert [url for url, _ in client.calls] == tiles["url"].tolist()
    assert all(
        kwargs
        == {
            "allow_redirects": True,
            "stream": True,
            "timeout": (30.0, 120.0),
        }
        for _, kwargs in client.calls
    )
    assert probed["http_status"].eq(200).all()
    assert set(records) == {"N33W112", "N33W113"}
    for record in records.values():
        assert record["request_method"] == "HEAD"
        assert type(record["payload_bytes_read"]) is int
        assert record["payload_bytes_read"] == 0
        assert record["content_sha256"] is None
        assert record["raster_schema_verified"] is False


def test_recorded_terrain_probes_require_exact_types_and_tile_set() -> None:
    tiles = derive_srtm_tiles(
        _boundary(shapely.box(*PHOENIX_BBOX)),
        analysis_crs="EPSG:5070",
        halo_m=30,
        base_url=OPEN_TOPOGRAPHY_SRTM_BASE_URL,
        filename_suffix=".tif",
    )
    _, records = probe_terrain_heads(_HeadOnlyClient(), tiles)

    applied = footprints._apply_recorded_terrain_probes(tiles, records)

    assert applied["tile_id"].tolist() == ["N33W112", "N33W113"]
    assert applied["http_status"].eq(200).all()
    assert applied["content_length"].eq(123456).all()
    assert applied["content_type"].eq("image/tiff").all()

    for key, replacement in (
        ("payload_bytes_read", False),
        ("http_status", True),
        ("content_length", True),
        ("raster_schema_verified", 0),
    ):
        wrong_type = deepcopy(records)
        wrong_type["N33W112"][key] = replacement
        with pytest.raises(SourceFootprintError):
            footprints._apply_recorded_terrain_probes(tiles, wrong_type)

    missing = deepcopy(records)
    del missing["N33W113"]
    with pytest.raises(SourceFootprintError):
        footprints._apply_recorded_terrain_probes(tiles, missing)

    extra = deepcopy(records)
    extra["N34W112"] = deepcopy(extra["N33W112"])
    extra["N34W112"]["tile_id"] = "N34W112"
    with pytest.raises(SourceFootprintError):
        footprints._apply_recorded_terrain_probes(tiles, extra)


def test_strict_equal_rejects_bool_integer_interchange_at_any_depth() -> None:
    assert footprints._strict_equal(False, False)
    assert footprints._strict_equal(0, 0)
    assert not footprints._strict_equal(False, 0)
    assert not footprints._strict_equal(0, False)
    assert not footprints._strict_equal(True, 1)
    assert not footprints._strict_equal(
        {"contract": {"values_read": False, "request_count": 0}},
        {"contract": {"values_read": 0, "request_count": False}},
    )


def test_processed_record_paths_must_be_exactly_the_six_standard_outputs() -> None:
    expected = {
        f"data/processed/multicity/phoenix_az/source_footprints/{filename}"
        for filename in footprints.OUTPUT_FILENAMES.values()
    }
    records = {path: {"sha256": "0" * 64, "bytes": 1} for path in expected}

    footprints._require_exact_record_paths(
        records,
        expected,
        label="source-footprint processed",
    )
    records[
        "data/processed/multicity/phoenix_az/source_footprints/extra_target.csv"
    ] = {"sha256": "1" * 64, "bytes": 1}
    with pytest.raises(SourceFootprintError, match="processed file set changed"):
        footprints._require_exact_record_paths(
            records,
            expected,
            label="source-footprint processed",
        )


def test_access_contract_preserves_exact_boolean_and_zero_types() -> None:
    true_keys = {
        "metadata_responses_only",
        "stac_fields_excluded_assets",
    }
    zero_keys = {
        "asset_sign_calls",
        "landsat_asset_http_requests",
        "sentinel_asset_http_requests",
        "daymet_data_download_requests",
        "terrain_get_requests",
        "raster_payload_bytes_read",
    }
    false_keys = {
        "stac_asset_objects_returned",
        "stac_asset_hrefs_read",
        "landsat_thermal_values_read",
        "landsat_target_qa_values_read",
        "sentinel_band_values_read",
        "daymet_values_read",
        "terrain_values_read",
        "external_lst_values_read",
        "predictor_construction_performed",
        "model_fit_performed",
        "model_predictions_computed",
    }

    assert set(ACCESS_CONTRACT) == true_keys | zero_keys | false_keys
    assert all(ACCESS_CONTRACT[key] is True for key in true_keys)
    assert all(ACCESS_CONTRACT[key] is False for key in false_keys)
    assert all(
        type(ACCESS_CONTRACT[key]) is int and ACCESS_CONTRACT[key] == 0
        for key in zero_keys
    )
