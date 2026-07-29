"""Metadata-only source-footprint discovery for the cross-city pilot.

This module deliberately does not call a raster reader, asset signer, model,
or feature builder.  STAC searches request a strict field allow-list and
exclude ``assets``; Daymet access stops at public CMR granule metadata; terrain
objects are probed with HEAD only.
"""

from __future__ import annotations

import calendar
import json
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import geopandas as gpd
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from urllib3.util.retry import Retry

from la_heat.daymet_grid import (
    DAYMET_CMR_COLLECTION_ID,
    DAYMET_CMR_GRANULES_URL,
    DAYMET_DOI,
    DAYMET_FULL_GRID_SHAPE,
    DAYMET_FULL_GRID_TRANSFORM,
    DAYMET_GRID_CRS,
)
from la_heat.multicity.config import CitySpec, MulticityPlan, load_multicity_plan
from la_heat.multicity.geography import verify_city_geography
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.static_sources import (
    OPEN_TOPOGRAPHY_DATASET_DOI,
    OPEN_TOPOGRAPHY_SRTM_BASE_URL,
)
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "multicity-source-footprints-v1"
COMPLETE_STATE: Final = "complete_metadata_only_source_not_protocol_locked"
STAGE_NAME: Final = "target_blind_source_footprint_discovery"
DEFAULT_SOURCE_CONFIG: Final = Path("configs/multicity/source_footprints_v1.toml")
MANIFEST_FILENAME: Final = "SOURCE_FOOTPRINTS.json"
LANDSAT_COLLECTION: Final = "landsat-c2-l2"
SENTINEL_COLLECTION: Final = "sentinel-2-l2a"
STAC_TIMEOUT: Final = (30.0, 120.0)
CMR_TIMEOUT: Final = (30.0, 120.0)
HEAD_TIMEOUT: Final = (30.0, 120.0)
MAX_STAC_PAGES: Final = 100

LANDSAT_PROPERTIES: Final = (
    "datetime",
    "platform",
    "landsat:wrs_path",
    "landsat:wrs_row",
    "landsat:collection_category",
    "landsat:correction",
)
SENTINEL_PROPERTIES: Final = (
    "datetime",
    "platform",
    "s2:mgrs_tile",
)
LANDSAT_FIELDS: Final = (
    "id",
    "collection",
    "geometry",
    "bbox",
    *(f"properties.{value}" for value in LANDSAT_PROPERTIES),
)
SENTINEL_FIELDS: Final = (
    "id",
    "collection",
    "geometry",
    "bbox",
    *(f"properties.{value}" for value in SENTINEL_PROPERTIES),
)
OUTPUT_FILENAMES: Final = {
    "landsat_items": "landsat_metadata_items.parquet",
    "sentinel_items": "sentinel_metadata_items.parquet",
    "optical_units": "optical_source_units.parquet",
    "daymet_granules": "daymet_granules.parquet",
    "daymet_cells": "daymet_intersecting_cells.parquet",
    "terrain_tiles": "terrain_tiles.parquet",
}
OUTPUT_GEOMETRY_TABLES: Final = frozenset(
    {
        "landsat_items",
        "sentinel_items",
        "optical_units",
        "daymet_cells",
        "terrain_tiles",
    }
)
ACCESS_CONTRACT: Final = {
    "metadata_responses_only": True,
    "stac_fields_excluded_assets": True,
    "stac_asset_objects_returned": False,
    "stac_asset_hrefs_read": False,
    "asset_sign_calls": 0,
    "landsat_asset_http_requests": 0,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "sentinel_asset_http_requests": 0,
    "sentinel_band_values_read": False,
    "daymet_data_download_requests": 0,
    "daymet_values_read": False,
    "terrain_get_requests": 0,
    "terrain_values_read": False,
    "raster_payload_bytes_read": 0,
    "external_lst_values_read": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
}
CODE_PATHS: Final = (
    "configs/multicity/cities/phoenix_az.toml",
    "configs/multicity/experiment.toml",
    "configs/multicity/source_footprints_v1.toml",
    "scripts/stage_multicity_source_footprints.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/geography.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/provenance.py",
    "src/la_heat/static_sources.py",
)

_DAYMET_TITLE = re.compile(
    r"^Daymet_Daily_V4R1\.daymet_v4_daily_na_"
    r"(?P<variable>dayl|prcp|srad|swe|tmax|tmin|vp)_(?P<year>\d{4})\.nc$"
)
_CMR_CONCEPT = re.compile(r"^G\d+-ORNL_CLOUD$")
_WRS_CODE = re.compile(r"^\d{1,3}$")
_MGRS_CODE = re.compile(r"^\d{2}[A-Z]{3}$")


class SourceFootprintError(ValueError):
    """Raised when the metadata-only footprint contract is violated."""


class _ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...

    def close(self) -> None: ...


class _HttpClientLike(Protocol):
    def post(self, url: str, **kwargs: object) -> _ResponseLike: ...

    def get(self, url: str, **kwargs: object) -> _ResponseLike: ...

    def head(self, url: str, **kwargs: object) -> _ResponseLike: ...


def _retrying_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "la-heat-source-footprints/1.0"})
    return session


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        raise SourceFootprintError(
            f"{label} keys changed; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}."
        )


def _strict_equal(actual: object, expected: object) -> bool:
    """Compare JSON-like values without treating booleans as integers."""

    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            return False
        return all(_strict_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_equal(observed, wanted)
            for observed, wanted in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _read_source_config(path: Path, plan: MulticityPlan) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    _require_exact_keys(
        payload,
        {"stage", "landsat", "sentinel", "daymet", "terrain"},
        label="source-footprint config sections",
    )
    stage = payload["stage"]
    landsat = payload["landsat"]
    sentinel = payload["sentinel"]
    daymet = payload["daymet"]
    terrain = payload["terrain"]
    _require_exact_keys(
        stage,
        {
            "schema_version",
            "algorithm_version",
            "city_id",
            "status",
            "analysis_crs",
            "confirmation_year",
            "warm_season_months",
        },
        label="source-footprint stage",
    )
    _require_exact_keys(
        landsat,
        {
            "provider",
            "api",
            "collection",
            "local_start_date",
            "local_end_date",
            "platforms",
            "collection_category",
            "correction",
            "page_limit",
            "license_note",
        },
        label="Landsat source contract",
    )
    _require_exact_keys(
        sentinel,
        {
            "provider",
            "api",
            "collection",
            "local_start_date",
            "local_end_date",
            "platforms",
            "page_limit",
            "license_note",
        },
        label="Sentinel source contract",
    )
    _require_exact_keys(
        daymet,
        {
            "provider",
            "doi",
            "collection_concept_id",
            "cmr_granules_url",
            "year",
            "variables",
            "source_start_date",
            "source_end_date",
            "window_halo_cells",
            "license_note",
        },
        label="Daymet source contract",
    )
    _require_exact_keys(
        terrain,
        {
            "provider",
            "dataset",
            "opentopo_id",
            "dataset_doi",
            "base_url",
            "filename_suffix",
            "nominal_resolution_arc_seconds",
            "nominal_shape",
            "vertical_datum",
            "slope_halo_m",
            "probe_method",
            "license_note",
        },
        label="terrain source contract",
    )

    experiment = plan.raw["experiment"]
    year = int(plan.raw["design"]["external_confirmation_year"])
    months = [int(value) for value in experiment["warm_season_months"]]
    warm_start = date(year, min(months), 1)
    warm_end = date(year, max(months), calendar.monthrange(year, max(months))[1])
    expected = {
        "stage": {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "city_id": "phoenix_az",
            "status": "pilot_metadata_only_not_protocol_lock",
            "analysis_crs": experiment["analysis_crs"],
            "confirmation_year": year,
            "warm_season_months": months,
        },
        "landsat": {
            "provider": "USGS via Microsoft Planetary Computer",
            "api": plan.raw["sources"]["landsat_stac_api"],
            "collection": plan.raw["target"]["landsat_collection"],
            "local_start_date": warm_start.isoformat(),
            "local_end_date": warm_end.isoformat(),
            "platforms": plan.raw["target"]["sensors"],
            "collection_category": "T1",
            "correction": "L2SP",
            "page_limit": 100,
            "license_note": "U.S. government public data",
        },
        "sentinel": {
            "provider": "ESA Copernicus via Microsoft Planetary Computer",
            "api": plan.raw["sources"]["sentinel_stac_api"],
            "collection": SENTINEL_COLLECTION,
            "local_start_date": (warm_start - timedelta(days=60)).isoformat(),
            "local_end_date": (warm_end - timedelta(days=1)).isoformat(),
            "platforms": ["sentinel-2a", "sentinel-2b", "sentinel-2c"],
            "page_limit": 100,
            "license_note": "ESA Copernicus Sentinel data terms",
        },
        "daymet": {
            "provider": "NASA ORNL DAAC",
            "doi": DAYMET_DOI,
            "collection_concept_id": DAYMET_CMR_COLLECTION_ID,
            "cmr_granules_url": DAYMET_CMR_GRANULES_URL,
            "year": year,
            "variables": list(DEFAULT_DAYMET_VARIABLES),
            "source_start_date": (warm_start - timedelta(days=7)).isoformat(),
            "source_end_date": (warm_end - timedelta(days=1)).isoformat(),
            "window_halo_cells": 1,
            "license_note": "NASA Earthdata terms",
        },
        "terrain": {
            "provider": "NASA/USGS via OpenTopography",
            "dataset": "NASA SRTM Global 1 arc second V003",
            "opentopo_id": "OTSRTM.082015.4326.1",
            "dataset_doi": OPEN_TOPOGRAPHY_DATASET_DOI.removeprefix(
                "https://doi.org/"
            ),
            "base_url": OPEN_TOPOGRAPHY_SRTM_BASE_URL,
            "filename_suffix": ".tif",
            "nominal_resolution_arc_seconds": 1,
            "nominal_shape": [3601, 3601],
            "vertical_datum": "EGM96 orthometric height",
            "slope_halo_m": 30,
            "probe_method": "HEAD",
            "license_note": "Cite NASA LP DAAC and OpenTopography",
        },
    }
    for section, values in expected.items():
        observed = {key: payload[section].get(key) for key in values}
        if not _strict_equal(observed, values):
            raise SourceFootprintError(
                f"{section} metadata-discovery contract changed: {observed!r}."
            )
    return payload


def _locks(plan: MulticityPlan) -> dict[str, bool]:
    raw = plan.raw["locks"]
    return {
        "protocol_locked": bool(raw["protocol_locked"]),
        "external_targets_unlocked": bool(raw["external_targets_unlocked"]),
        "external_target_values_read": bool(raw["external_target_values_read"]),
        "external_prediction_commit_exists": bool(
            raw["external_prediction_commit_exists"]
        ),
        "allow_predictor_construction": bool(raw["allow_predictor_construction"]),
        "allow_model_fitting": bool(raw["allow_model_fitting"]),
        "allow_external_target_access": bool(raw["allow_external_target_access"]),
    }


def _authorize(plan: MulticityPlan, city_id: str) -> CitySpec:
    locks = _locks(plan)
    if any(locks.values()):
        raise SourceFootprintError("Metadata discovery requires every computation lock closed.")
    raw = plan.raw["locks"]
    if (
        raw["allow_boundary_metadata_staging"] is not True
        or city_id not in raw["authorized_metadata_city_ids"]
    ):
        raise SourceFootprintError(f"Metadata discovery is not authorized for {city_id}.")
    city = next((value for value in plan.cities if value.id == city_id), None)
    if city is None:
        raise SourceFootprintError(f"Unknown city id: {city_id}")
    if city.role != "external_confirmation" or city.target_values_status != "sealed":
        raise SourceFootprintError("Source footprints require one sealed external city.")
    return city


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceFootprintError("Datetime must be timezone-aware.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def local_date_interval_to_utc(
    start_date: date,
    end_date: date,
    timezone: str,
) -> str:
    """Return a broad closed UTC interval for exact local-date filtering."""

    if end_date < start_date:
        raise SourceFootprintError("Local metadata interval ends before it starts.")
    zone = ZoneInfo(timezone)
    start = datetime.combine(start_date, time.min, tzinfo=zone)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return f"{_utc_text(start)}/{_utc_text(end)}"


def _normalized_platform(value: object) -> str:
    raw = str(value).strip().lower().replace("_", "-")
    aliases = {
        "landsat8": "landsat-8",
        "landsat9": "landsat-9",
        "sentinel-2-a": "sentinel-2a",
        "sentinel-2-b": "sentinel-2b",
        "sentinel-2-c": "sentinel-2c",
    }
    return aliases.get(raw, raw)


def _parse_stac_datetime(value: object, *, item_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceFootprintError(
            f"STAC item {item_id} has an invalid datetime."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceFootprintError(f"STAC item {item_id} datetime is not aware.")
    return parsed.astimezone(UTC)


def _stac_endpoint(api: str) -> str:
    parsed = urlparse(api)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "planetarycomputer.microsoft.com"
        or parsed.query
        or parsed.fragment
    ):
        raise SourceFootprintError(f"Unexpected public STAC API: {api!r}")
    return api.rstrip("/") + "/search"


def _validate_stac_page(
    payload: object,
    *,
    collection: str,
    properties: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not isinstance(payload, Mapping) or payload.get("type") != "FeatureCollection":
        raise SourceFootprintError("STAC search response is not a FeatureCollection.")
    features = payload.get("features")
    links = payload.get("links")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise SourceFootprintError("STAC search response lacks a features array.")
    if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
        raise SourceFootprintError("STAC search response lacks a links array.")
    normalized: list[dict[str, Any]] = []
    required_top = {"id", "collection", "geometry", "bbox", "properties"}
    for raw in features:
        if not isinstance(raw, Mapping):
            raise SourceFootprintError("STAC returned a non-object feature.")
        if "assets" in raw or "links" in raw:
            raise SourceFootprintError("STAC metadata response exposed item assets or links.")
        if set(raw) != required_top:
            raise SourceFootprintError(
                f"STAC item fields changed: {sorted(set(raw))!r}."
            )
        if raw.get("collection") != collection:
            raise SourceFootprintError("STAC response crossed the requested collection.")
        item_properties = raw.get("properties")
        if not isinstance(item_properties, Mapping) or set(item_properties) != set(
            properties
        ):
            raise SourceFootprintError("STAC item property allow-list changed.")
        normalized.append(dict(raw))
    next_links = [
        value
        for value in links
        if isinstance(value, Mapping) and value.get("rel") == "next"
    ]
    if len(next_links) > 1:
        raise SourceFootprintError("STAC response returned multiple next links.")
    return normalized, None if not next_links else dict(next_links[0])


def fetch_public_stac_metadata(
    client: _HttpClientLike,
    *,
    api: str,
    collection: str,
    bbox_wgs84: Sequence[float],
    datetime_interval: str,
    fields: Sequence[str],
    properties: Sequence[str],
    page_limit: int,
    query: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Exhaust a public STAC search whose item assets are excluded server-side."""

    endpoint = _stac_endpoint(api)
    body: dict[str, Any] = {
        "collections": [collection],
        "bbox": [float(value) for value in bbox_wgs84],
        "datetime": datetime_interval,
        "limit": int(page_limit),
        "fields": {
            "include": list(fields),
            "exclude": ["assets", "links"],
        },
    }
    if query is not None:
        body["query"] = dict(query)
    pages: list[dict[str, Any]] = []
    items_by_id: dict[str, dict[str, Any]] = {}
    response_count = 0
    duplicate_count = 0
    seen_next: set[str] = set()
    active_body = body
    for _ in range(MAX_STAC_PAGES):
        response = client.post(
            endpoint,
            json=active_body,
            timeout=STAC_TIMEOUT,
        )
        try:
            response.raise_for_status()
            payload = response.json()
        finally:
            response.close()
        items, next_link = _validate_stac_page(
            payload,
            collection=collection,
            properties=properties,
        )
        page_payload = dict(payload)
        pages.append(page_payload)
        response_count += len(items)
        for item in items:
            item_id = str(item["id"])
            if not item_id:
                raise SourceFootprintError("STAC item ID is empty.")
            previous = items_by_id.get(item_id)
            if previous is not None:
                duplicate_count += 1
                if canonical_sha256(previous) != canonical_sha256(item):
                    raise SourceFootprintError(
                        f"Conflicting STAC metadata share item ID {item_id}."
                    )
                continue
            items_by_id[item_id] = item
        if next_link is None:
            break
        if (
            next_link.get("method") != "POST"
            or next_link.get("href") != endpoint
            or not isinstance(next_link.get("body"), Mapping)
        ):
            raise SourceFootprintError("STAC pagination left the frozen POST contract.")
        next_body = dict(next_link["body"])
        for key in ("collections", "bbox", "datetime", "limit", "fields"):
            if next_body.get(key) != body.get(key):
                raise SourceFootprintError(f"STAC next page changed query field {key}.")
        if body.get("query") != next_body.get("query"):
            raise SourceFootprintError("STAC next page changed the metadata query.")
        next_sha = canonical_sha256(next_body)
        if next_sha in seen_next:
            raise SourceFootprintError("STAC pagination token repeated.")
        seen_next.add(next_sha)
        active_body = next_body
    else:
        raise SourceFootprintError("STAC metadata pagination exceeded its page limit.")
    summary = {
        "endpoint": endpoint,
        "query": body,
        "query_sha256": canonical_sha256(body),
        "page_count": len(pages),
        "query_response_items": response_count,
        "unique_items": len(items_by_id),
        "duplicate_items": duplicate_count,
        "pagination_exhausted": True,
        "assets_excluded": True,
    }
    return (
        [items_by_id[key] for key in sorted(items_by_id)],
        pages,
        summary,
    )


def build_optical_item_table(
    features: Sequence[Mapping[str, Any]],
    *,
    source: str,
    collection: str,
    expected_properties: Sequence[str],
    allowed_platforms: Sequence[str],
    local_start_date: date,
    local_end_date: date,
    timezone: str,
    city_boundary: gpd.GeoDataFrame,
    analysis_crs: str,
) -> gpd.GeoDataFrame:
    """Parse allow-listed STAC metadata and retain positive city intersections."""

    if source not in {"landsat_wrs", "sentinel_mgrs"}:
        raise SourceFootprintError(f"Unknown optical source: {source}")
    zone = ZoneInfo(timezone)
    allowed = {_normalized_platform(value) for value in allowed_platforms}
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    for item in features:
        item_id = str(item.get("id", ""))
        properties = item.get("properties")
        if (
            not item_id
            or item.get("collection") != collection
            or not isinstance(properties, Mapping)
            or set(properties) != set(expected_properties)
        ):
            raise SourceFootprintError("STAC item disagrees with its allow-list.")
        platform = _normalized_platform(properties["platform"])
        if platform not in allowed:
            raise SourceFootprintError(
                f"STAC item {item_id} uses unexpected platform {platform}."
            )
        acquired = _parse_stac_datetime(properties["datetime"], item_id=item_id)
        local_date = acquired.astimezone(zone).date()
        if not local_start_date <= local_date <= local_end_date:
            continue
        raw_geometry = item.get("geometry")
        if not isinstance(raw_geometry, Mapping):
            raise SourceFootprintError(f"STAC item {item_id} has no geometry object.")
        geometry = shape(raw_geometry)
        if geometry.is_empty or not geometry.is_valid:
            raise SourceFootprintError(f"STAC item {item_id} geometry is invalid.")

        if source == "landsat_wrs":
            if (
                properties["landsat:collection_category"] != "T1"
                or properties["landsat:correction"] != "L2SP"
            ):
                raise SourceFootprintError("Landsat metadata crossed T1/L2SP.")
            path_value = str(properties["landsat:wrs_path"]).strip()
            row_value = str(properties["landsat:wrs_row"]).strip()
            if not _WRS_CODE.fullmatch(path_value) or not _WRS_CODE.fullmatch(
                row_value
            ):
                raise SourceFootprintError(f"Invalid WRS code in {item_id}.")
            wrs_path = f"{int(path_value):03d}"
            wrs_row = f"{int(row_value):03d}"
            if not 1 <= int(wrs_path) <= 233 or not 1 <= int(wrs_row) <= 248:
                raise SourceFootprintError(f"WRS code is out of range in {item_id}.")
            unit_id = f"WRS2-{wrs_path}{wrs_row}"
            source_fields = {
                "wrs_path": wrs_path,
                "wrs_row": wrs_row,
                "mgrs_tile": "",
            }
        else:
            mgrs = str(properties["s2:mgrs_tile"]).strip().upper()
            if not _MGRS_CODE.fullmatch(mgrs):
                raise SourceFootprintError(f"Invalid MGRS tile in {item_id}.")
            unit_id = f"MGRS-{mgrs}"
            source_fields = {
                "wrs_path": "",
                "wrs_row": "",
                "mgrs_tile": mgrs,
            }
        rows.append(
            {
                "source": source,
                "collection": collection,
                "item_id": item_id,
                "platform": platform,
                "acquired_utc": _utc_text(acquired),
                "acquisition_local_date": local_date.isoformat(),
                "unit_id": unit_id,
                **source_fields,
                "geometry_sha256": canonical_sha256(
                    shapely.to_wkb(
                        shapely.normalize(geometry),
                        hex=True,
                        output_dimension=2,
                        byte_order=1,
                        include_srid=False,
                    )
                ),
            }
        )
        geometries.append(geometry)
    if not rows:
        raise SourceFootprintError(f"No {source} metadata survived exact dates.")
    frame = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    projected = frame.to_crs(analysis_crs)
    city = city_boundary.to_crs(analysis_crs).geometry.union_all()
    city_area = float(city.area)
    overlap = projected.geometry.intersection(city).area
    frame["city_overlap_area_m2"] = overlap.to_numpy(dtype=float)
    frame["city_overlap_fraction"] = (
        frame["city_overlap_area_m2"] / city_area
    ).clip(0.0, 1.0)
    frame = frame.loc[frame["city_overlap_area_m2"] > 0].copy()
    if frame.empty:
        raise SourceFootprintError(f"No {source} metadata intersects the city.")
    return frame.sort_values(
        ["acquired_utc", "unit_id", "item_id"], kind="stable"
    ).reset_index(drop=True)


def build_optical_unit_table(
    frames: Sequence[gpd.GeoDataFrame],
    *,
    city_boundary: gpd.GeoDataFrame,
    analysis_crs: str,
) -> gpd.GeoDataFrame:
    """Union repeated scene footprints into stable WRS/MGRS source units."""

    combined = pd.concat(frames, ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    city = city_boundary.to_crs(analysis_crs).geometry.union_all()
    city_area = float(city.area)
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    for (source, collection, unit_id), group in combined.groupby(
        ["source", "collection", "unit_id"], sort=True
    ):
        geometry = shapely.union_all(group.geometry.to_numpy())
        projected = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
            analysis_crs
        ).iloc[0]
        overlap_area = float(projected.intersection(city).area)
        if overlap_area <= 0:
            raise SourceFootprintError(f"Source unit {unit_id} lost city overlap.")
        rows.append(
            {
                "source": source,
                "collection": collection,
                "unit_id": unit_id,
                "item_count": len(group),
                "platforms": "|".join(sorted(group["platform"].unique())),
                "first_acquired_utc": str(group["acquired_utc"].min()),
                "last_acquired_utc": str(group["acquired_utc"].max()),
                "city_overlap_area_m2": overlap_area,
                "city_coverage_fraction": min(1.0, overlap_area / city_area),
                "item_id_set_sha256": canonical_sha256(
                    sorted(group["item_id"].astype(str).tolist())
                ),
            }
        )
        geometries.append(geometry)
    return gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326").sort_values(
        ["source", "unit_id"], kind="stable"
    ).reset_index(drop=True)


def _official_cmr_link(
    entry: Mapping[str, Any],
    *,
    relation_suffix: str,
) -> str:
    raw_links = entry.get("links")
    if not isinstance(raw_links, Sequence) or isinstance(raw_links, (str, bytes)):
        raise SourceFootprintError("Daymet CMR granule lacks a links array.")
    matches = [
        str(value.get("href"))
        for value in raw_links
        if isinstance(value, Mapping)
        and value.get("inherited") is not True
        and str(value.get("rel", "")).endswith(relation_suffix)
        and value.get("href")
    ]
    if len(matches) != 1:
        raise SourceFootprintError(
            f"Daymet granule needs one {relation_suffix} link; found {len(matches)}."
        )
    return matches[0]


def _validate_daymet_link(url: str, *, data_link: bool) -> str:
    parsed = urlparse(url)
    expected_host = (
        "data.ornldaac.earthdata.nasa.gov"
        if data_link
        else "opendap.earthdata.nasa.gov"
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.query
        or parsed.fragment
    ):
        raise SourceFootprintError(f"Unexpected Daymet metadata link: {url!r}")
    if data_link and "/Daymet_Daily_V4R1/data/" not in parsed.path:
        raise SourceFootprintError("Daymet HTTPS link is outside V4 R1.")
    if not data_link and (
        f"/collections/{DAYMET_CMR_COLLECTION_ID}/granules/" not in parsed.path
    ):
        raise SourceFootprintError("Daymet OPeNDAP link is outside the frozen collection.")
    return url


def _daymet_query_params(
    *,
    collection_concept_id: str,
    year: int,
    bbox_wgs84: Sequence[float],
) -> dict[str, Any]:
    return {
        "collection_concept_id": collection_concept_id,
        "temporal": f"{year}-01-01T00:00:00Z,{year}-12-31T23:59:59Z",
        "bounding_box": ",".join(
            format(float(value), ".10f") for value in bbox_wgs84
        ),
        "page_size": 2000,
    }


def _parse_daymet_granule_payload(
    raw_payload: object,
    *,
    year: int,
    variables: Sequence[str],
) -> tuple[pd.DataFrame, int]:
    """Parse the allow-listed annual Daymet identities from a CMR response."""

    normalized_variables = tuple(str(value).lower() for value in variables)
    if normalized_variables != tuple(DEFAULT_DAYMET_VARIABLES):
        raise SourceFootprintError("Daymet variable contract changed.")
    if not isinstance(raw_payload, Mapping):
        raise SourceFootprintError("CMR granule response must be an object.")
    feed = raw_payload.get("feed")
    entries = feed.get("entry") if isinstance(feed, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise SourceFootprintError("CMR granule response lacks feed.entry.")

    requested = {(value, year) for value in normalized_variables}
    discovered: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise SourceFootprintError("CMR feed contains a non-object granule.")
        title = str(raw.get("title", ""))
        match = _DAYMET_TITLE.fullmatch(title)
        if match is None:
            continue
        variable = match.group("variable")
        granule_year = int(match.group("year"))
        key = (variable, granule_year)
        if key not in requested:
            continue
        if key in discovered:
            raise SourceFootprintError(f"Duplicate Daymet granule metadata: {key}.")
        concept_id = str(raw.get("id", ""))
        if _CMR_CONCEPT.fullmatch(concept_id) is None:
            raise SourceFootprintError(f"Invalid Daymet granule concept: {concept_id}.")
        try:
            size_mb = float(raw["granule_size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceFootprintError(
                f"Daymet granule {title} lacks a valid size."
            ) from exc
        if not math.isfinite(size_mb) or size_mb <= 0:
            raise SourceFootprintError(f"Daymet granule {title} size is invalid.")
        discovered[key] = {
            "concept_id": concept_id,
            "title": title,
            "variable": variable,
            "year": granule_year,
            "size_mb": size_mb,
            "updated_at": (
                None if raw.get("updated") is None else str(raw.get("updated"))
            ),
            "https_url": _validate_daymet_link(
                _official_cmr_link(raw, relation_suffix="/data#"),
                data_link=True,
            ),
            "opendap_url": _validate_daymet_link(
                _official_cmr_link(raw, relation_suffix="/service#"),
                data_link=False,
            ),
        }
    if set(discovered) != requested:
        raise SourceFootprintError(
            "Daymet granule set is incomplete; "
            f"missing={sorted(requested - set(discovered))}, "
            f"unexpected={sorted(set(discovered) - requested)}."
        )
    frame = pd.DataFrame(
        [discovered[key] for key in sorted(discovered, key=lambda value: value[0])]
    )
    return frame, len(entries)


def fetch_daymet_granule_metadata(
    client: _HttpClientLike,
    *,
    endpoint: str,
    collection_concept_id: str,
    year: int,
    variables: Sequence[str],
    bbox_wgs84: Sequence[float],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Read exact annual Daymet granule identities from public CMR metadata."""

    if endpoint != DAYMET_CMR_GRANULES_URL:
        raise SourceFootprintError("Daymet metadata endpoint changed.")
    if collection_concept_id != DAYMET_CMR_COLLECTION_ID:
        raise SourceFootprintError("Daymet collection concept changed.")
    params = _daymet_query_params(
        collection_concept_id=collection_concept_id,
        year=year,
        bbox_wgs84=bbox_wgs84,
    )
    response = client.get(endpoint, params=params, timeout=CMR_TIMEOUT)
    try:
        response.raise_for_status()
        raw_payload = response.json()
        headers = {
            key: str(value)
            for key, value in response.headers.items()
            if key.lower() in {"cmr-hits", "cmr-request-id", "content-type"}
        }
        status_code = int(response.status_code)
    finally:
        response.close()
    frame, entry_count = _parse_daymet_granule_payload(
        raw_payload,
        year=year,
        variables=variables,
    )
    query_record = {
        "endpoint": endpoint,
        "params": params,
        "query_sha256": canonical_sha256(params),
        "http_status": status_code,
        "response_headers": headers,
        "returned_feed_entries": entry_count,
        "selected_granules": len(frame),
    }
    return frame, dict(raw_payload), query_record


def derive_daymet_index_window(
    bbox_wgs84: Sequence[float],
    *,
    halo_cells: int,
) -> dict[str, Any]:
    """Derive a conservative inclusive Daymet window from the frozen city bbox."""

    if len(bbox_wgs84) != 4 or halo_cells < 0:
        raise SourceFootprintError("Invalid Daymet bbox or halo.")
    west, south, east, north = (float(value) for value in bbox_wgs84)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise SourceFootprintError("Daymet bbox is invalid.")
    from rasterio.warp import transform_bounds

    projected = transform_bounds(
        "EPSG:4326",
        DAYMET_GRID_CRS,
        west,
        south,
        east,
        north,
        densify_pts=41,
    )
    transform = DAYMET_FULL_GRID_TRANSFORM
    x_start = math.floor((projected[0] - transform.c) / transform.a)
    x_stop = math.ceil((projected[2] - transform.c) / transform.a) - 1
    y_start = math.floor((transform.f - projected[3]) / abs(transform.e))
    y_stop = math.ceil((transform.f - projected[1]) / abs(transform.e)) - 1
    minimal = (y_start, y_stop, x_start, x_stop)
    y_start -= halo_cells
    y_stop += halo_cells
    x_start -= halo_cells
    x_stop += halo_cells
    height, width = DAYMET_FULL_GRID_SHAPE
    if not (
        0 <= y_start <= y_stop < height
        and 0 <= x_start <= x_stop < width
    ):
        raise SourceFootprintError("Derived Daymet window is outside the full grid.")
    return {
        "full_grid_shape": list(DAYMET_FULL_GRID_SHAPE),
        "full_grid_transform": list(DAYMET_FULL_GRID_TRANSFORM)[:6],
        "grid_crs": DAYMET_GRID_CRS,
        "resolution_m": 1000,
        "projected_bbox_m": [round(float(value), 3) for value in projected],
        "minimal_y_indices_inclusive": [minimal[0], minimal[1]],
        "minimal_x_indices_inclusive": [minimal[2], minimal[3]],
        "halo_cells": halo_cells,
        "y_indices_inclusive": [y_start, y_stop],
        "x_indices_inclusive": [x_start, x_stop],
        "window_shape": [y_stop - y_start + 1, x_stop - x_start + 1],
        "window_cell_count": (y_stop - y_start + 1)
        * (x_stop - x_start + 1),
        "candidate_grid_cells_only": True,
    }


def build_daymet_cell_table(
    window: Mapping[str, Any],
    *,
    city_boundary: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """List only Daymet cells with positive city-polygon intersection."""

    y_start, y_stop = (int(value) for value in window["y_indices_inclusive"])
    x_start, x_stop = (int(value) for value in window["x_indices_inclusive"])
    city = city_boundary.to_crs(DAYMET_GRID_CRS).geometry.union_all()
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    transform = DAYMET_FULL_GRID_TRANSFORM
    for row_index in range(y_start, y_stop + 1):
        top = transform.f + row_index * transform.e
        bottom = top + transform.e
        for col_index in range(x_start, x_stop + 1):
            left = transform.c + col_index * transform.a
            right = left + transform.a
            geometry = box(left, bottom, right, top)
            overlap_area = float(geometry.intersection(city).area)
            if overlap_area <= 0:
                continue
            center_x = left + transform.a / 2
            center_y = top + transform.e / 2
            rows.append(
                {
                    "source": "daymet_cells",
                    "daymet_row": row_index,
                    "daymet_col": col_index,
                    "daymet_x_m": center_x,
                    "daymet_y_m": center_y,
                    "daymet_cell_id": f"x{center_x:.3f}_y{center_y:.3f}",
                    "city_overlap_area_m2": overlap_area,
                    "cell_area_m2": abs(transform.a * transform.e),
                    "city_overlap_fraction_of_cell": overlap_area
                    / abs(transform.a * transform.e),
                }
            )
            geometries.append(geometry)
    if not rows:
        raise SourceFootprintError("No Daymet cell positively intersects the city.")
    return gpd.GeoDataFrame(
        rows,
        geometry=geometries,
        crs=DAYMET_GRID_CRS,
    ).sort_values(["daymet_row", "daymet_col"], kind="stable").reset_index(drop=True)


def _srtm_tile_id(south: int, west: int) -> str:
    latitude = f"N{south:02d}" if south >= 0 else f"S{abs(south):02d}"
    longitude = f"E{west:03d}" if west >= 0 else f"W{abs(west):03d}"
    return latitude + longitude


def derive_srtm_tiles(
    city_boundary: gpd.GeoDataFrame,
    *,
    analysis_crs: str,
    halo_m: float,
    base_url: str,
    filename_suffix: str,
) -> gpd.GeoDataFrame:
    """Derive one-degree SRTM tile identities from a buffered city boundary."""

    if halo_m < 0 or base_url != OPEN_TOPOGRAPHY_SRTM_BASE_URL:
        raise SourceFootprintError("Invalid terrain footprint contract.")
    projected = city_boundary.to_crs(analysis_crs)
    buffered = gpd.GeoSeries(
        [projected.geometry.union_all().buffer(halo_m)],
        crs=analysis_crs,
    ).to_crs("EPSG:4326")
    west_bound, south_bound, east_bound, north_bound = buffered.total_bounds
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    for south in range(math.floor(south_bound), math.ceil(north_bound)):
        for west in range(math.floor(west_bound), math.ceil(east_bound)):
            geometry = box(west, south, west + 1, south + 1)
            overlap = geometry.intersection(buffered.iloc[0])
            if overlap.is_empty or overlap.area <= 0:
                continue
            tile_id = _srtm_tile_id(south, west)
            filename = tile_id + filename_suffix
            rows.append(
                {
                    "source": "terrain_windows",
                    "tile_id": tile_id,
                    "filename": filename,
                    "south": south,
                    "west": west,
                    "north": south + 1,
                    "east": west + 1,
                    "url": f"{base_url}/{filename}",
                    "probe_method": "HEAD",
                    "payload_bytes_read": 0,
                }
            )
            geometries.append(geometry)
    if not rows:
        raise SourceFootprintError("No SRTM tile intersects the buffered city.")
    return gpd.GeoDataFrame(
        rows,
        geometry=geometries,
        crs="EPSG:4326",
    ).sort_values("tile_id", kind="stable").reset_index(drop=True)


def _apply_recorded_terrain_probes(
    tiles: gpd.GeoDataFrame,
    probes: Mapping[str, Mapping[str, Any]],
) -> gpd.GeoDataFrame:
    """Validate HEAD-only probe records and bind them to derived terrain tiles."""

    expected_ids = set(tiles["tile_id"].astype(str))
    if set(probes) != expected_ids:
        raise SourceFootprintError("Terrain HEAD probe set changed.")
    output = tiles.copy()
    expected_keys = {
        "tile_id",
        "request_method",
        "requested_url",
        "final_url",
        "http_status",
        "content_length",
        "content_type",
        "last_modified",
        "etag",
        "payload_bytes_read",
        "content_sha256",
        "raster_schema_verified",
    }
    for index, row in output.iterrows():
        tile_id = str(row["tile_id"])
        record = probes[tile_id]
        _require_exact_keys(record, expected_keys, label=f"terrain probe {tile_id}")
        final = urlparse(str(record["final_url"]))
        if (
            record["tile_id"] != tile_id
            or record["request_method"] != "HEAD"
            or record["requested_url"] != str(row["url"])
            or final.scheme != "https"
            or final.hostname != "opentopography.s3.sdsc.edu"
            or type(record["http_status"]) is not int
            or record["http_status"] != 200
            or type(record["content_length"]) is not int
            or record["content_length"] <= 0
            or record["content_type"] not in {"image/tiff", "image/geotiff"}
            or (
                record["last_modified"] is not None
                and not isinstance(record["last_modified"], str)
            )
            or (
                record["etag"] is not None
                and not isinstance(record["etag"], str)
            )
            or type(record["payload_bytes_read"]) is not int
            or record["payload_bytes_read"] != 0
            or record["content_sha256"] is not None
            or record["raster_schema_verified"] is not False
        ):
            raise SourceFootprintError(
                f"Terrain HEAD probe violates metadata-only access: {tile_id}."
            )
        output.loc[index, "http_status"] = record["http_status"]
        output.loc[index, "content_length"] = record["content_length"]
        output.loc[index, "content_type"] = record["content_type"]
        output.loc[index, "last_modified"] = record["last_modified"]
        output.loc[index, "content_sha256"] = None
        output.loc[index, "raster_schema_verified"] = False
    output["raster_schema_verified"] = output["raster_schema_verified"].astype(bool)
    return output


def probe_terrain_heads(
    client: _HttpClientLike,
    tiles: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, dict[str, dict[str, Any]]]:
    """Verify public terrain object existence without issuing any GET request."""

    probes: dict[str, dict[str, Any]] = {}
    for _, row in tiles.iterrows():
        response = client.head(
            str(row["url"]),
            allow_redirects=True,
            stream=True,
            timeout=HEAD_TIMEOUT,
        )
        try:
            response.raise_for_status()
            final = urlparse(str(response.url))
            if (
                response.status_code != 200
                or final.scheme != "https"
                or final.hostname != "opentopography.s3.sdsc.edu"
            ):
                raise SourceFootprintError(
                    f"Terrain HEAD left the public OpenTopography object: {row['tile_id']}."
                )
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            try:
                content_length = int(headers["content-length"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceFootprintError(
                    f"Terrain HEAD lacks content length: {row['tile_id']}."
                ) from exc
            content_type = headers.get("content-type", "").split(";")[0].strip().lower()
            if content_length <= 0 or content_type not in {
                "image/tiff",
                "image/geotiff",
            }:
                raise SourceFootprintError(
                    f"Terrain HEAD metadata is invalid: {row['tile_id']}."
                )
            record = {
                "tile_id": str(row["tile_id"]),
                "request_method": "HEAD",
                "requested_url": str(row["url"]),
                "final_url": str(response.url),
                "http_status": int(response.status_code),
                "content_length": content_length,
                "content_type": content_type,
                "last_modified": headers.get("last-modified"),
                "etag": headers.get("etag"),
                "payload_bytes_read": 0,
                "content_sha256": None,
                "raster_schema_verified": False,
            }
        finally:
            response.close()
        probes[str(row["tile_id"])] = record
    return _apply_recorded_terrain_probes(tiles, probes), probes


def _relative(project_root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(project_root):
        raise SourceFootprintError(f"Artifact escapes the project root: {path}")
    return resolved.relative_to(project_root).as_posix()


def _file_records(project_root: Path, paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    return {
        _relative(project_root, path): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(paths)
    }


def _table_record(
    project_root: Path,
    path: Path,
    frame: pd.DataFrame | gpd.GeoDataFrame,
    *,
    geometry: bool,
) -> dict[str, Any]:
    record = parquet_file_record(path, frame)
    record["path"] = _relative(project_root, path)
    if geometry:
        if not isinstance(frame, gpd.GeoDataFrame):
            raise TypeError("A geometry table must be a GeoDataFrame.")
        record["geometry_semantic_sha256"] = geometry_semantic_sha256(frame)
    return record


def _non_geometry_semantic(
    frame: pd.DataFrame | gpd.GeoDataFrame,
    *,
    sort_by: list[str],
) -> str:
    selected = pd.DataFrame(frame.drop(columns=["geometry"], errors="ignore"))
    return canonical_frame_sha256(selected, sort_by=sort_by)


def _frame_replay_sha256(
    frame: pd.DataFrame | gpd.GeoDataFrame,
    *,
    sort_by: list[str],
) -> str:
    """Hash row-associated geometry and values for deterministic replay checks."""

    selected = pd.DataFrame(frame.drop(columns=["geometry"], errors="ignore")).copy()
    crs: dict[str, Any] | None = None
    if isinstance(frame, gpd.GeoDataFrame):
        if frame.crs is None:
            raise SourceFootprintError("Replayed geometry table has no CRS.")
        crs = frame.crs.to_json_dict()
        selected["__geometry_wkb"] = [
            shapely.to_wkb(
                shapely.normalize(geometry),
                hex=True,
                output_dimension=2,
                byte_order=1,
                include_srid=False,
            )
            for geometry in frame.geometry
        ]
    schema = [(column, str(dtype)) for column, dtype in selected.dtypes.items()]
    return canonical_sha256(
        {
            "crs": crs,
            "columns": list(selected.columns),
            "schema": schema,
            "rows_sha256": canonical_frame_sha256(selected, sort_by=sort_by),
        }
    )


def _require_replayed_frame(
    observed: pd.DataFrame | gpd.GeoDataFrame,
    replayed: pd.DataFrame | gpd.GeoDataFrame,
    *,
    sort_by: list[str],
    label: str,
) -> None:
    if _frame_replay_sha256(observed, sort_by=sort_by) != _frame_replay_sha256(
        replayed,
        sort_by=sort_by,
    ):
        raise SourceFootprintError(f"{label} output does not replay from raw metadata.")


def _family_summaries(
    *,
    landsat_items: gpd.GeoDataFrame,
    sentinel_items: gpd.GeoDataFrame,
    optical_units: gpd.GeoDataFrame,
    daymet_granules: pd.DataFrame,
    daymet_cells: gpd.GeoDataFrame,
    daymet_window: Mapping[str, Any],
    terrain_tiles: gpd.GeoDataFrame,
) -> dict[str, dict[str, Any]]:
    landsat_units = optical_units.loc[optical_units["source"] == "landsat_wrs"]
    sentinel_units = optical_units.loc[optical_units["source"] == "sentinel_mgrs"]
    return {
        "landsat_wrs": {
            "member_count": len(landsat_units),
            "member_ids": sorted(landsat_units["unit_id"].astype(str).tolist()),
            "item_count": len(landsat_items),
            "platforms": sorted(landsat_items["platform"].unique().tolist()),
            "item_metadata_semantic_sha256": _non_geometry_semantic(
                landsat_items,
                sort_by=["item_id"],
            ),
            "unit_semantic_sha256": _non_geometry_semantic(
                landsat_units,
                sort_by=["unit_id"],
            ),
        },
        "sentinel_mgrs": {
            "member_count": len(sentinel_units),
            "member_ids": sorted(sentinel_units["unit_id"].astype(str).tolist()),
            "item_count": len(sentinel_items),
            "platforms": sorted(sentinel_items["platform"].unique().tolist()),
            "item_metadata_semantic_sha256": _non_geometry_semantic(
                sentinel_items,
                sort_by=["item_id"],
            ),
            "unit_semantic_sha256": _non_geometry_semantic(
                sentinel_units,
                sort_by=["unit_id"],
            ),
        },
        "daymet_cells": {
            "member_count": len(daymet_cells),
            "member_id_set_sha256": canonical_sha256(
                sorted(daymet_cells["daymet_cell_id"].astype(str).tolist())
            ),
            "granule_count": len(daymet_granules),
            "granule_concept_ids": sorted(
                daymet_granules["concept_id"].astype(str).tolist()
            ),
            "granule_semantic_sha256": canonical_frame_sha256(
                daymet_granules,
                sort_by=["variable"],
            ),
            "cell_semantic_sha256": _non_geometry_semantic(
                daymet_cells,
                sort_by=["daymet_row", "daymet_col"],
            ),
            "window": dict(daymet_window),
        },
        "terrain_windows": {
            "member_count": len(terrain_tiles),
            "member_ids": sorted(terrain_tiles["tile_id"].astype(str).tolist()),
            "tile_semantic_sha256": _non_geometry_semantic(
                terrain_tiles,
                sort_by=["tile_id"],
            ),
            "all_objects_head_verified": bool(
                terrain_tiles["http_status"].eq(200).all()
            ),
            "content_sha256_frozen": False,
            "raster_schema_verified": False,
        },
    }


def _manifest_paths(
    workspace: MulticityWorkspace,
    city_id: str,
) -> tuple[Path, Path, Path]:
    city_workspace = workspace.city(city_id)
    return (
        city_workspace.raw / "source_footprints",
        city_workspace.processed / "source_footprints",
        city_workspace.manifests / "source_footprints" / MANIFEST_FILENAME,
    )


def _assert_empty_destination(raw_root: Path, processed_root: Path) -> None:
    for path in (raw_root, processed_root):
        if path.exists() and any(path.rglob("*")):
            raise SourceFootprintError(
                f"Uncommitted source-footprint artifacts already exist: {path}"
            )


def stage_city_source_footprints(
    config_path: str | Path,
    city_id: str,
    *,
    source_config_path: str | Path = DEFAULT_SOURCE_CONFIG,
    client: _HttpClientLike | None = None,
    query_time_utc: datetime | None = None,
) -> dict[str, Any]:
    """Discover and commit Phoenix source coverage without opening data assets."""

    plan = load_multicity_plan(config_path)
    city = _authorize(plan, city_id)
    workspace = MulticityWorkspace.from_plan(plan)
    source_path = Path(source_config_path)
    if not source_path.is_absolute():
        source_path = workspace.project_root / source_path
    source_path = source_path.resolve()
    source_config = _read_source_config(source_path, plan)
    if source_config["stage"]["city_id"] != city_id:
        raise SourceFootprintError("Source-footprint config city changed.")

    geography = verify_city_geography(plan.path, city_id)
    raw_root, processed_root, manifest_path = _manifest_paths(workspace, city_id)
    if manifest_path.is_file():
        return verify_city_source_footprints(
            plan.path,
            city_id,
            source_config_path=source_path,
        )
    _assert_empty_destination(raw_root, processed_root)

    boundary_record = geography["output_tables"]["city_boundary"]
    boundary_path = workspace.project_root / boundary_record["path"]
    city_boundary = gpd.read_parquet(boundary_path)
    bbox = tuple(float(value) for value in geography["geography"]["bbox_wgs84"])
    analysis_crs = str(source_config["stage"]["analysis_crs"])
    active_client = _retrying_session() if client is None else client
    queried_at = query_time_utc or datetime.now(UTC)
    if queried_at.tzinfo is None or queried_at.utcoffset() is None:
        raise SourceFootprintError("Query time must be timezone-aware.")

    landsat_config = source_config["landsat"]
    landsat_start = date.fromisoformat(landsat_config["local_start_date"])
    landsat_end = date.fromisoformat(landsat_config["local_end_date"])
    landsat_interval = local_date_interval_to_utc(
        landsat_start,
        landsat_end,
        city.timezone,
    )
    landsat_features, landsat_pages, landsat_query = fetch_public_stac_metadata(
        active_client,
        api=landsat_config["api"],
        collection=landsat_config["collection"],
        bbox_wgs84=bbox,
        datetime_interval=landsat_interval,
        fields=LANDSAT_FIELDS,
        properties=LANDSAT_PROPERTIES,
        page_limit=int(landsat_config["page_limit"]),
        query={
            "platform": {"in": list(landsat_config["platforms"])},
            "landsat:collection_category": {
                "eq": landsat_config["collection_category"]
            },
            "landsat:correction": {"eq": landsat_config["correction"]},
        },
    )
    landsat_items = build_optical_item_table(
        landsat_features,
        source="landsat_wrs",
        collection=landsat_config["collection"],
        expected_properties=LANDSAT_PROPERTIES,
        allowed_platforms=landsat_config["platforms"],
        local_start_date=landsat_start,
        local_end_date=landsat_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    sentinel_config = source_config["sentinel"]
    sentinel_start = date.fromisoformat(sentinel_config["local_start_date"])
    sentinel_end = date.fromisoformat(sentinel_config["local_end_date"])
    sentinel_interval = local_date_interval_to_utc(
        sentinel_start,
        sentinel_end,
        city.timezone,
    )
    sentinel_features, sentinel_pages, sentinel_query = fetch_public_stac_metadata(
        active_client,
        api=sentinel_config["api"],
        collection=sentinel_config["collection"],
        bbox_wgs84=bbox,
        datetime_interval=sentinel_interval,
        fields=SENTINEL_FIELDS,
        properties=SENTINEL_PROPERTIES,
        page_limit=int(sentinel_config["page_limit"]),
    )
    sentinel_items = build_optical_item_table(
        sentinel_features,
        source="sentinel_mgrs",
        collection=sentinel_config["collection"],
        expected_properties=SENTINEL_PROPERTIES,
        allowed_platforms=sentinel_config["platforms"],
        local_start_date=sentinel_start,
        local_end_date=sentinel_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )
    optical_units = build_optical_unit_table(
        (landsat_items, sentinel_items),
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    daymet_config = source_config["daymet"]
    daymet_granules, daymet_raw, daymet_query = fetch_daymet_granule_metadata(
        active_client,
        endpoint=daymet_config["cmr_granules_url"],
        collection_concept_id=daymet_config["collection_concept_id"],
        year=int(daymet_config["year"]),
        variables=daymet_config["variables"],
        bbox_wgs84=bbox,
    )
    daymet_window = derive_daymet_index_window(
        bbox,
        halo_cells=int(daymet_config["window_halo_cells"]),
    )
    daymet_cells = build_daymet_cell_table(
        daymet_window,
        city_boundary=city_boundary,
    )

    terrain_config = source_config["terrain"]
    terrain_tiles = derive_srtm_tiles(
        city_boundary,
        analysis_crs=analysis_crs,
        halo_m=float(terrain_config["slope_halo_m"]),
        base_url=terrain_config["base_url"],
        filename_suffix=terrain_config["filename_suffix"],
    )
    terrain_tiles, terrain_probes = probe_terrain_heads(
        active_client,
        terrain_tiles,
    )

    raw_paths: list[Path] = []
    for source, pages in (
        ("landsat", landsat_pages),
        ("sentinel", sentinel_pages),
    ):
        for number, page in enumerate(pages, start=1):
            path = raw_root / source / f"stac_page_{number:03d}.json"
            atomic_json(page, path)
            raw_paths.append(path)
    daymet_raw_path = raw_root / "daymet" / "cmr_granules_2025.json"
    atomic_json(daymet_raw, daymet_raw_path)
    raw_paths.append(daymet_raw_path)
    for tile_id, record in sorted(terrain_probes.items()):
        path = raw_root / "terrain" / f"{tile_id}_head.json"
        atomic_json(record, path)
        raw_paths.append(path)

    output_frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {
        "landsat_items": landsat_items,
        "sentinel_items": sentinel_items,
        "optical_units": optical_units,
        "daymet_granules": daymet_granules,
        "daymet_cells": daymet_cells,
        "terrain_tiles": terrain_tiles,
    }
    output_paths = {
        name: processed_root / filename
        for name, filename in OUTPUT_FILENAMES.items()
    }
    for name, frame in output_frames.items():
        atomic_parquet(frame, output_paths[name])

    raw_records = _file_records(workspace.project_root, raw_paths)
    output_records = _file_records(
        workspace.project_root,
        list(output_paths.values()),
    )
    committed_frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {
        name: (
            gpd.read_parquet(output_paths[name])
            if name in OUTPUT_GEOMETRY_TABLES
            else pd.read_parquet(output_paths[name])
        )
        for name in output_frames
    }
    output_tables = {
        name: _table_record(
            workspace.project_root,
            output_paths[name],
            committed_frames[name],
            geometry=name in OUTPUT_GEOMETRY_TABLES,
        )
        for name in output_frames
    }
    families = _family_summaries(
        landsat_items=committed_frames["landsat_items"],
        sentinel_items=committed_frames["sentinel_items"],
        optical_units=committed_frames["optical_units"],
        daymet_granules=committed_frames["daymet_granules"],
        daymet_cells=committed_frames["daymet_cells"],
        daymet_window=daymet_window,
        terrain_tiles=committed_frames["terrain_tiles"],
    )
    code_sha, code_runtime = code_runtime_fingerprint(
        project_root=workspace.project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_runtime["relative_paths"] = list(CODE_PATHS)
    code_runtime["sha256"] = code_sha

    geography_path = (
        workspace.city(city_id).manifests / "geography" / "GEOGRAPHY.json"
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "stage": STAGE_NAME,
        "generated_at_utc": _utc_text(queried_at),
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "city": {
            "id": city.id,
            "name": city.name,
            "role": city.role,
            "target_values_status": city.target_values_status,
            "timezone": city.timezone,
        },
        "source_lock_status": "pilot_snapshot_not_protocol_lock",
        "lock_snapshot": _locks(plan),
        "geography_input": {
            "manifest_path": _relative(workspace.project_root, geography_path),
            "manifest_file_sha256": sha256_file(geography_path),
            "manifest_commit_sha256": geography["commit_sha256"],
            "city_boundary": dict(boundary_record),
            "primary_tracts": dict(geography["output_tables"]["primary_tracts"]),
            "bbox_wgs84": list(bbox),
        },
        "source_config": {
            "path": _relative(workspace.project_root, source_path),
            "sha256": sha256_file(source_path),
            "bytes": source_path.stat().st_size,
            "semantic_sha256": canonical_sha256(source_config),
            "status": source_config["stage"]["status"],
        },
        "selection_contract": {
            "analysis_crs": analysis_crs,
            "boundary_role": "authenticated_city_polygon",
            "spatial_rule": "strictly_positive_city_intersection_area",
            "windows_derived_from_boundary_not_los_angeles_constants": True,
            "cloud_cover_cutoff": None,
            "landsat_local_date_interval": [
                landsat_start.isoformat(),
                landsat_end.isoformat(),
            ],
            "sentinel_all_possible_lag_window_local_dates": [
                sentinel_start.isoformat(),
                sentinel_end.isoformat(),
            ],
            "no_external_target_dates_selected": True,
            "daymet_candidate_cells_are_not_final_contributing_cells": True,
            "terrain_head_does_not_freeze_content_bytes": True,
        },
        "queries": {
            "landsat": landsat_query,
            "sentinel": sentinel_query,
            "daymet": daymet_query,
            "terrain": {
                "method": "HEAD",
                "object_count": len(terrain_tiles),
                "payload_bytes_read": 0,
            },
        },
        "source_families": families,
        "access_contract": dict(ACCESS_CONTRACT),
        "raw_files": raw_records,
        "raw_file_set_sha256": canonical_sha256(raw_records),
        "outputs": output_records,
        "output_file_set_sha256": canonical_sha256(output_records),
        "output_tables": output_tables,
        "code_runtime": code_runtime,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, manifest_path)
    return verify_city_source_footprints(
        plan.path,
        city_id,
        source_config_path=source_path,
    )


def _verify_recorded_files(
    project_root: Path,
    records: Mapping[str, Mapping[str, Any]],
    *,
    required_root: Path,
) -> set[Path]:
    verified: set[Path] = set()
    for relative, record in records.items():
        if not isinstance(relative, str) or not isinstance(record, Mapping):
            raise SourceFootprintError("Source-footprint file record is invalid.")
        _require_exact_keys(
            record,
            {"sha256", "bytes"},
            label=f"source-footprint file record {relative}",
        )
        path = (project_root / relative).resolve()
        if (
            not path.is_relative_to(required_root.resolve())
            or not path.is_file()
            or not isinstance(record["sha256"], str)
            or sha256_file(path) != record.get("sha256")
            or type(record["bytes"]) is not int
            or path.stat().st_size != record["bytes"]
        ):
            raise SourceFootprintError(
                f"Source-footprint artifact failed its byte lock: {relative}"
            )
        verified.add(path)
    actual = {path.resolve() for path in required_root.rglob("*") if path.is_file()}
    if actual != verified:
        raise SourceFootprintError(
            "Source-footprint directory contains missing or undeclared files."
        )
    return verified


def _require_exact_record_paths(
    records: Mapping[str, Mapping[str, Any]],
    expected_paths: set[str],
    *,
    label: str,
) -> None:
    if set(records) != expected_paths:
        raise SourceFootprintError(f"{label} file set changed.")


def _expected_manifest_keys() -> set[str]:
    return {
        "schema_version",
        "algorithm_version",
        "state",
        "stage",
        "generated_at_utc",
        "experiment_id",
        "plan_semantic_sha256",
        "city",
        "source_lock_status",
        "lock_snapshot",
        "geography_input",
        "source_config",
        "selection_contract",
        "queries",
        "source_families",
        "access_contract",
        "raw_files",
        "raw_file_set_sha256",
        "outputs",
        "output_file_set_sha256",
        "output_tables",
        "code_runtime",
        "commit_sha256",
    }


def _replay_stac_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    collection: str,
    properties: Sequence[str],
    query_record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replay a frozen STAC POST chain and return its unique allow-listed items."""

    expected_query_keys = {
        "endpoint",
        "query",
        "query_sha256",
        "page_count",
        "query_response_items",
        "unique_items",
        "duplicate_items",
        "pagination_exhausted",
        "assets_excluded",
    }
    _require_exact_keys(
        query_record,
        expected_query_keys,
        label=f"{collection} STAC query record",
    )
    endpoint = query_record["endpoint"]
    body = query_record["query"]
    if not isinstance(endpoint, str) or not isinstance(body, Mapping):
        raise SourceFootprintError("STAC query endpoint or body is invalid.")
    if (
        type(query_record["page_count"]) is not int
        or query_record["page_count"] < 1
        or len(pages) != query_record["page_count"]
        or type(query_record["query_response_items"]) is not int
        or type(query_record["unique_items"]) is not int
        or type(query_record["duplicate_items"]) is not int
        or query_record["pagination_exhausted"] is not True
        or query_record["assets_excluded"] is not True
    ):
        raise SourceFootprintError("STAC query counters or flags changed.")

    items_by_id: dict[str, dict[str, Any]] = {}
    response_count = 0
    duplicate_count = 0
    seen_next: set[str] = set()
    for page_number, payload in enumerate(pages, start=1):
        if set(payload) != {"type", "links", "features", "numberReturned"}:
            raise SourceFootprintError("STAC raw page fields changed.")
        items, next_link = _validate_stac_page(
            payload,
            collection=collection,
            properties=properties,
        )
        if (
            type(payload["numberReturned"]) is not int
            or payload["numberReturned"] != len(items)
        ):
            raise SourceFootprintError("STAC numberReturned disagrees with features.")
        response_count += len(items)
        for item in items:
            item_id = str(item["id"])
            if not item_id:
                raise SourceFootprintError("STAC item ID is empty.")
            previous = items_by_id.get(item_id)
            if previous is not None:
                duplicate_count += 1
                if canonical_sha256(previous) != canonical_sha256(item):
                    raise SourceFootprintError(
                        f"Conflicting STAC metadata share item ID {item_id}."
                    )
                continue
            items_by_id[item_id] = item

        final_page = page_number == len(pages)
        if final_page:
            if next_link is not None:
                raise SourceFootprintError("Final STAC raw page still has a next link.")
            continue
        if (
            next_link is None
            or next_link.get("method") != "POST"
            or next_link.get("href") != endpoint
            or not isinstance(next_link.get("body"), Mapping)
        ):
            raise SourceFootprintError("STAC raw pagination chain is incomplete.")
        next_body = dict(next_link["body"])
        if set(next_body) != set(body) | {"token"}:
            raise SourceFootprintError("STAC next body fields changed.")
        for key, value in body.items():
            if not _strict_equal(next_body.get(key), value):
                raise SourceFootprintError(
                    f"STAC next page changed query field {key}."
                )
        token = next_body.get("token")
        if not isinstance(token, str) or not token:
            raise SourceFootprintError("STAC next page lacks a token.")
        next_sha = canonical_sha256(next_body)
        if next_sha in seen_next:
            raise SourceFootprintError("STAC pagination token repeated.")
        seen_next.add(next_sha)

    if (
        query_record["query_response_items"] != response_count
        or query_record["unique_items"] != len(items_by_id)
        or query_record["duplicate_items"] != duplicate_count
    ):
        raise SourceFootprintError("STAC raw replay counters changed.")
    return [items_by_id[key] for key in sorted(items_by_id)]


def _verify_stac_raw_pages(
    project_root: Path,
    records: Mapping[str, Mapping[str, Any]],
    *,
    source: str,
    collection: str,
    properties: Sequence[str],
    query_record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matches = sorted(
        relative
        for relative in records
        if f"/source_footprints/{source}/stac_page_" in relative
    )
    expected_paths = [
        (
            f"data/raw/multicity/phoenix_az/source_footprints/{source}/"
            f"stac_page_{number:03d}.json"
        )
        for number in range(1, int(query_record.get("page_count", 0)) + 1)
    ]
    if matches != expected_paths:
        raise SourceFootprintError(f"{source} raw STAC page count changed.")
    pages: list[dict[str, Any]] = []
    for relative in matches:
        payload = json.loads((project_root / relative).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SourceFootprintError(f"{source} raw STAC page is not an object.")
        pages.append(payload)
    return _replay_stac_pages(
        pages,
        collection=collection,
        properties=properties,
        query_record=query_record,
    )


def _verify_query_contract(
    payload: Mapping[str, Any],
    *,
    source_config: Mapping[str, Any],
    city: CitySpec,
    bbox: Sequence[float],
    expected_terrain_count: int,
) -> None:
    _require_exact_keys(
        payload,
        {"landsat", "sentinel", "daymet", "terrain"},
        label="source query record",
    )
    for source, fields in (
        ("landsat", LANDSAT_FIELDS),
        ("sentinel", SENTINEL_FIELDS),
    ):
        query_record = payload[source]
        if not isinstance(query_record, Mapping):
            raise SourceFootprintError(f"{source} STAC query record is invalid.")
        configured = source_config[source]
        expected_interval = local_date_interval_to_utc(
            date.fromisoformat(configured["local_start_date"]),
            date.fromisoformat(configured["local_end_date"]),
            city.timezone,
        )
        body = query_record.get("query")
        if not isinstance(body, Mapping):
            raise SourceFootprintError(f"{source} STAC query record is invalid.")
        expected_base = {
            "collections": [configured["collection"]],
            "bbox": [float(value) for value in bbox],
            "datetime": expected_interval,
            "limit": int(configured["page_limit"]),
            "fields": {
                "include": list(fields),
                "exclude": ["assets", "links"],
            },
        }
        if source == "landsat":
            expected_base["query"] = {
                "platform": {"in": list(configured["platforms"])},
                "landsat:collection_category": {
                    "eq": configured["collection_category"]
                },
                "landsat:correction": {"eq": configured["correction"]},
            }
        if (
            query_record.get("endpoint") != _stac_endpoint(configured["api"])
            or not _strict_equal(dict(body), expected_base)
            or query_record.get("query_sha256") != canonical_sha256(expected_base)
            or query_record.get("assets_excluded") is not True
            or query_record.get("pagination_exhausted") is not True
            or type(query_record.get("page_count")) is not int
            or query_record["page_count"] < 1
        ):
            raise SourceFootprintError(f"{source} STAC query contract changed.")

    daymet = payload["daymet"]
    if not isinstance(daymet, Mapping):
        raise SourceFootprintError("Daymet query record is invalid.")
    _require_exact_keys(
        daymet,
        {
            "endpoint",
            "params",
            "query_sha256",
            "http_status",
            "response_headers",
            "returned_feed_entries",
            "selected_granules",
        },
        label="Daymet query record",
    )
    configured_daymet = source_config["daymet"]
    expected_params = _daymet_query_params(
        collection_concept_id=configured_daymet["collection_concept_id"],
        year=int(configured_daymet["year"]),
        bbox_wgs84=bbox,
    )
    headers = daymet["response_headers"]
    if not isinstance(headers, Mapping):
        raise SourceFootprintError("Daymet response headers are invalid.")
    normalized_headers = {str(key).lower(): value for key, value in headers.items()}
    if (
        daymet["endpoint"] != configured_daymet["cmr_granules_url"]
        or not _strict_equal(daymet["params"], expected_params)
        or daymet["query_sha256"] != canonical_sha256(expected_params)
        or type(daymet["http_status"]) is not int
        or daymet["http_status"] != 200
        or not set(normalized_headers)
        <= {"cmr-hits", "cmr-request-id", "content-type"}
        or not normalized_headers
        or any(not isinstance(value, str) for value in normalized_headers.values())
        or type(daymet["returned_feed_entries"]) is not int
        or daymet["returned_feed_entries"] < len(configured_daymet["variables"])
        or type(daymet["selected_granules"]) is not int
        or daymet["selected_granules"] != len(configured_daymet["variables"])
    ):
        raise SourceFootprintError("Daymet query contract changed.")
    if "cmr-hits" in normalized_headers:
        try:
            cmr_hits = int(normalized_headers["cmr-hits"])
        except ValueError as exc:
            raise SourceFootprintError("Daymet CMR-Hits header is invalid.") from exc
        if cmr_hits != daymet["returned_feed_entries"]:
            raise SourceFootprintError("Daymet CMR-Hits disagrees with the response.")

    terrain = payload.get("terrain")
    if not _strict_equal(
        terrain,
        {
            "method": "HEAD",
            "object_count": expected_terrain_count,
            "payload_bytes_read": 0,
        },
    ):
        raise SourceFootprintError("Terrain query used a method other than HEAD.")


def verify_city_source_footprints(
    config_path: str | Path,
    city_id: str,
    *,
    source_config_path: str | Path = DEFAULT_SOURCE_CONFIG,
) -> dict[str, Any]:
    """Authenticate a complete metadata-only source-footprint snapshot."""

    plan = load_multicity_plan(config_path)
    city = _authorize(plan, city_id)
    workspace = MulticityWorkspace.from_plan(plan)
    source_path = Path(source_config_path)
    if not source_path.is_absolute():
        source_path = workspace.project_root / source_path
    source_path = source_path.resolve()
    source_config = _read_source_config(source_path, plan)
    geography = verify_city_geography(plan.path, city_id)
    raw_root, processed_root, manifest_path = _manifest_paths(workspace, city_id)
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFootprintError("Cannot read source-footprint manifest.") from exc
    if not isinstance(payload, dict):
        raise SourceFootprintError("Source-footprint manifest must be an object.")
    _require_exact_keys(
        payload,
        _expected_manifest_keys(),
        label="source-footprint manifest",
    )
    recorded_commit = payload["commit_sha256"]
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded_commit, str) or recorded_commit != canonical_sha256(body):
        raise SourceFootprintError("Source-footprint manifest commit is invalid.")
    source_families = payload["source_families"]
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
        or payload["algorithm_version"] != ALGORITHM_VERSION
        or payload["state"] != COMPLETE_STATE
        or payload["stage"] != STAGE_NAME
        or payload["experiment_id"] != plan.experiment_id
        or payload["plan_semantic_sha256"] != plan.semantic_sha256
        or payload["source_lock_status"] != "pilot_snapshot_not_protocol_lock"
        or not _strict_equal(payload["lock_snapshot"], _locks(plan))
        or not _strict_equal(payload["access_contract"], ACCESS_CONTRACT)
        or not isinstance(source_families, Mapping)
        or set(source_families)
        != {"landsat_wrs", "sentinel_mgrs", "daymet_cells", "terrain_windows"}
    ):
        raise SourceFootprintError("Source-footprint manifest contract changed.")
    expected_city = {
        "id": city.id,
        "name": city.name,
        "role": city.role,
        "target_values_status": city.target_values_status,
        "timezone": city.timezone,
    }
    if not _strict_equal(payload["city"], expected_city):
        raise SourceFootprintError("Source-footprint city identity changed.")

    geography_path = (
        workspace.city(city_id).manifests / "geography" / "GEOGRAPHY.json"
    )
    expected_geography = {
        "manifest_path": _relative(workspace.project_root, geography_path),
        "manifest_file_sha256": sha256_file(geography_path),
        "manifest_commit_sha256": geography["commit_sha256"],
        "city_boundary": geography["output_tables"]["city_boundary"],
        "primary_tracts": geography["output_tables"]["primary_tracts"],
        "bbox_wgs84": geography["geography"]["bbox_wgs84"],
    }
    if not _strict_equal(payload["geography_input"], expected_geography):
        raise SourceFootprintError("Source-footprint geography binding changed.")
    expected_source_config = {
        "path": _relative(workspace.project_root, source_path),
        "sha256": sha256_file(source_path),
        "bytes": source_path.stat().st_size,
        "semantic_sha256": canonical_sha256(source_config),
        "status": source_config["stage"]["status"],
    }
    if not _strict_equal(payload["source_config"], expected_source_config):
        raise SourceFootprintError("Source-footprint source config changed.")
    expected_selection = {
        "analysis_crs": source_config["stage"]["analysis_crs"],
        "boundary_role": "authenticated_city_polygon",
        "spatial_rule": "strictly_positive_city_intersection_area",
        "windows_derived_from_boundary_not_los_angeles_constants": True,
        "cloud_cover_cutoff": None,
        "landsat_local_date_interval": [
            source_config["landsat"]["local_start_date"],
            source_config["landsat"]["local_end_date"],
        ],
        "sentinel_all_possible_lag_window_local_dates": [
            source_config["sentinel"]["local_start_date"],
            source_config["sentinel"]["local_end_date"],
        ],
        "no_external_target_dates_selected": True,
        "daymet_candidate_cells_are_not_final_contributing_cells": True,
        "terrain_head_does_not_freeze_content_bytes": True,
    }
    if not _strict_equal(payload["selection_contract"], expected_selection):
        raise SourceFootprintError("Source-footprint selection contract changed.")

    boundary_path = workspace.project_root / expected_geography["city_boundary"]["path"]
    city_boundary = gpd.read_parquet(boundary_path)
    terrain_config = source_config["terrain"]
    replayed_terrain_base = derive_srtm_tiles(
        city_boundary,
        analysis_crs=source_config["stage"]["analysis_crs"],
        halo_m=float(terrain_config["slope_halo_m"]),
        base_url=terrain_config["base_url"],
        filename_suffix=terrain_config["filename_suffix"],
    )
    _verify_query_contract(
        payload["queries"],
        source_config=source_config,
        city=city,
        bbox=geography["geography"]["bbox_wgs84"],
        expected_terrain_count=len(replayed_terrain_base),
    )

    raw_records = payload["raw_files"]
    output_records = payload["outputs"]
    if not isinstance(raw_records, Mapping) or not isinstance(
        output_records,
        Mapping,
    ):
        raise SourceFootprintError("Source-footprint file records must be objects.")
    expected_output_paths = {
        _relative(workspace.project_root, processed_root / filename)
        for filename in OUTPUT_FILENAMES.values()
    }
    _require_exact_record_paths(
        output_records,
        expected_output_paths,
        label="source-footprint processed",
    )
    if (
        payload["raw_file_set_sha256"] != canonical_sha256(raw_records)
        or payload["output_file_set_sha256"] != canonical_sha256(output_records)
    ):
        raise SourceFootprintError("Source-footprint file-set commit changed.")
    _verify_recorded_files(
        workspace.project_root,
        raw_records,
        required_root=raw_root,
    )
    _verify_recorded_files(
        workspace.project_root,
        output_records,
        required_root=processed_root,
    )
    landsat_features = _verify_stac_raw_pages(
        workspace.project_root,
        raw_records,
        source="landsat",
        collection=LANDSAT_COLLECTION,
        properties=LANDSAT_PROPERTIES,
        query_record=payload["queries"]["landsat"],
    )
    sentinel_features = _verify_stac_raw_pages(
        workspace.project_root,
        raw_records,
        source="sentinel",
        collection=SENTINEL_COLLECTION,
        properties=SENTINEL_PROPERTIES,
        query_record=payload["queries"]["sentinel"],
    )

    daymet_relative = (
        "data/raw/multicity/phoenix_az/source_footprints/"
        "daymet/cmr_granules_2025.json"
    )
    if daymet_relative not in raw_records:
        raise SourceFootprintError("Frozen Daymet raw response is missing.")
    daymet_raw = json.loads(
        (workspace.project_root / daymet_relative).read_text(encoding="utf-8")
    )
    replayed_daymet_granules, daymet_entry_count = _parse_daymet_granule_payload(
        daymet_raw,
        year=int(source_config["daymet"]["year"]),
        variables=source_config["daymet"]["variables"],
    )
    daymet_query = payload["queries"]["daymet"]
    if (
        daymet_query["returned_feed_entries"] != daymet_entry_count
        or daymet_query["selected_granules"] != len(replayed_daymet_granules)
    ):
        raise SourceFootprintError("Daymet raw response disagrees with query counters.")

    expected_probe_paths = {
        (
            "data/raw/multicity/phoenix_az/source_footprints/terrain/"
            f"{tile_id}_head.json"
        )
        for tile_id in replayed_terrain_base["tile_id"].astype(str)
    }
    terrain_probe_paths = {
        relative
        for relative in raw_records
        if "/source_footprints/terrain/" in relative
    }
    if terrain_probe_paths != expected_probe_paths:
        raise SourceFootprintError("Frozen terrain HEAD file set changed.")
    terrain_probes: dict[str, dict[str, Any]] = {}
    for relative in sorted(terrain_probe_paths):
        probe = json.loads(
            (workspace.project_root / relative).read_text(encoding="utf-8")
        )
        if not isinstance(probe, dict):
            raise SourceFootprintError("Terrain raw probe must be an object.")
        tile_id = Path(relative).name.removesuffix("_head.json")
        terrain_probes[tile_id] = probe
    replayed_terrain = _apply_recorded_terrain_probes(
        replayed_terrain_base,
        terrain_probes,
    )
    expected_raw_paths = {
        daymet_relative,
        *expected_probe_paths,
        *(
            "data/raw/multicity/phoenix_az/source_footprints/landsat/"
            f"stac_page_{number:03d}.json"
            for number in range(1, payload["queries"]["landsat"]["page_count"] + 1)
        ),
        *(
            "data/raw/multicity/phoenix_az/source_footprints/sentinel/"
            f"stac_page_{number:03d}.json"
            for number in range(1, payload["queries"]["sentinel"]["page_count"] + 1)
        ),
    }
    if set(raw_records) != expected_raw_paths:
        raise SourceFootprintError("Source-footprint raw file set changed.")

    if set(payload["output_tables"]) != set(OUTPUT_FILENAMES):
        raise SourceFootprintError("Source-footprint output table set changed.")
    frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {}
    for name, filename in OUTPUT_FILENAMES.items():
        expected_path = processed_root / filename
        record = payload["output_tables"][name]
        if record.get("path") != _relative(workspace.project_root, expected_path):
            raise SourceFootprintError(f"{name} output path changed.")
        frame: pd.DataFrame | gpd.GeoDataFrame
        if name in OUTPUT_GEOMETRY_TABLES:
            frame = gpd.read_parquet(expected_path)
        else:
            frame = pd.read_parquet(expected_path)
        expected_record = _table_record(
            workspace.project_root,
            expected_path,
            frame,
            geometry=name in OUTPUT_GEOMETRY_TABLES,
        )
        if not _strict_equal(record, expected_record):
            raise SourceFootprintError(f"{name} table provenance changed.")
        frames[name] = frame

    landsat_config = source_config["landsat"]
    replayed_landsat = build_optical_item_table(
        landsat_features,
        source="landsat_wrs",
        collection=landsat_config["collection"],
        expected_properties=LANDSAT_PROPERTIES,
        allowed_platforms=landsat_config["platforms"],
        local_start_date=date.fromisoformat(landsat_config["local_start_date"]),
        local_end_date=date.fromisoformat(landsat_config["local_end_date"]),
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=source_config["stage"]["analysis_crs"],
    )
    sentinel_config = source_config["sentinel"]
    replayed_sentinel = build_optical_item_table(
        sentinel_features,
        source="sentinel_mgrs",
        collection=sentinel_config["collection"],
        expected_properties=SENTINEL_PROPERTIES,
        allowed_platforms=sentinel_config["platforms"],
        local_start_date=date.fromisoformat(sentinel_config["local_start_date"]),
        local_end_date=date.fromisoformat(sentinel_config["local_end_date"]),
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=source_config["stage"]["analysis_crs"],
    )
    replayed_optical_units = build_optical_unit_table(
        (replayed_landsat, replayed_sentinel),
        city_boundary=city_boundary,
        analysis_crs=source_config["stage"]["analysis_crs"],
    )
    daymet_window = derive_daymet_index_window(
        geography["geography"]["bbox_wgs84"],
        halo_cells=int(source_config["daymet"]["window_halo_cells"]),
    )
    replayed_daymet_cells = build_daymet_cell_table(
        daymet_window,
        city_boundary=city_boundary,
    )
    for name, replayed, sort_by in (
        ("landsat_items", replayed_landsat, ["item_id"]),
        ("sentinel_items", replayed_sentinel, ["item_id"]),
        ("optical_units", replayed_optical_units, ["source", "unit_id"]),
        ("daymet_granules", replayed_daymet_granules, ["variable"]),
        ("daymet_cells", replayed_daymet_cells, ["daymet_row", "daymet_col"]),
        ("terrain_tiles", replayed_terrain, ["tile_id"]),
    ):
        _require_replayed_frame(
            frames[name],
            replayed,
            sort_by=sort_by,
            label=name,
        )

    expected_families = _family_summaries(
        landsat_items=frames["landsat_items"],
        sentinel_items=frames["sentinel_items"],
        optical_units=frames["optical_units"],
        daymet_granules=frames["daymet_granules"],
        daymet_cells=frames["daymet_cells"],
        daymet_window=daymet_window,
        terrain_tiles=frames["terrain_tiles"],
    )
    if not _strict_equal(payload["source_families"], expected_families):
        raise SourceFootprintError("Source-family semantic summary changed.")

    code_sha, expected_code_runtime = code_runtime_fingerprint(
        project_root=workspace.project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    expected_code_runtime["relative_paths"] = list(CODE_PATHS)
    expected_code_runtime["sha256"] = code_sha
    if not _strict_equal(payload["code_runtime"], expected_code_runtime):
        raise SourceFootprintError("Source-footprint code/runtime fingerprint changed.")
    return payload
