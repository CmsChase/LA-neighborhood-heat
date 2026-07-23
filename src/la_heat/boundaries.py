"""Boundary acquisition and deterministic Los Angeles tract selection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import requests
import shapely
from pystac_client import Client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "la-neighborhood-heat/0.1 (student research; reproducible data pilot)"


@dataclass(frozen=True)
class DownloadRecord:
    path: Path
    sha256: str
    bytes_downloaded: int
    source_href: str


def _retrying_session() -> requests.Session:
    retries = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_city_boundary(url: str) -> gpd.GeoDataFrame:
    """Fetch and dissolve the official City of Los Angeles GeoJSON boundary."""

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    boundary = gpd.GeoDataFrame.from_features(payload["features"], crs="EPSG:4326")
    if boundary.empty:
        raise ValueError("The City of Los Angeles boundary response contained no features.")
    union = shapely.make_valid(boundary.geometry.union_all())
    return gpd.GeoDataFrame({"city": ["Los Angeles"]}, geometry=[union], crs="EPSG:4326")


def _census_signed_https_url(
    *,
    stac_api: str,
    collection_id: str,
    item_id: str,
) -> tuple[str, str]:
    """Resolve a Planetary Computer ABFS table asset to a temporary signed HTTPS URL."""

    catalog = Client.open(stac_api)
    item = catalog.get_collection(collection_id).get_item(item_id)
    if item is None:
        raise ValueError(f"Census STAC item not found: {item_id}")
    pc.sign_inplace(item)
    asset = item.assets["data"]
    href = asset.href
    if not href.startswith("abfs://"):
        raise ValueError(f"Unexpected Census table href: {href}")

    storage = asset.extra_fields.get("table:storage_options", {})
    account = storage.get("account_name")
    credential = storage.get("credential")
    if not account or not credential:
        raise ValueError("Signed Census table asset lacks account or temporary credential.")

    container_and_path = href.removeprefix("abfs://")
    container, relative_path = container_and_path.split("/", 1)
    signed = (
        f"https://{account}.blob.core.windows.net/{container}/{relative_path}?{credential}"
    )
    return signed, href


def download_census_tract_table(
    *,
    stac_api: str,
    collection_id: str,
    item_id: str,
    destination: Path,
) -> DownloadRecord:
    """Download and cache the Census cartographic tract GeoParquet with a checksum."""

    signed_url, source_href = _census_signed_https_url(
        stac_api=stac_api, collection_id=collection_id, item_id=item_id
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return DownloadRecord(destination, digest, destination.stat().st_size, source_href)

    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    byte_count = 0
    with requests.get(
        signed_url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=(30, 300),
    ) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
    temporary.replace(destination)
    return DownloadRecord(destination, digest.hexdigest(), byte_count, source_href)


def download_detailed_la_county_tracts(
    *,
    layer_url: str,
    destination: Path,
    state_fips: str,
    county_fips: str,
    expected_feature_count: int,
) -> DownloadRecord:
    """Download detailed 2020 TIGER tracts from California DWR's public mirror.

    The state-government layer preserves the original U.S. Census TIGER fields.
    ArcGIS pagination is explicit and ordered, so a silent first-page truncation
    cannot create an incomplete tract universe.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        tracts = pd.read_parquet(destination, columns=["GEOID"])
        if len(tracts) != expected_feature_count or tracts["GEOID"].duplicated().any():
            raise ValueError("Cached detailed Census tract file failed its count/ID audit.")
        return DownloadRecord(destination, digest, destination.stat().st_size, layer_url)

    session = _retrying_session()
    metadata_response = session.get(layer_url, params={"f": "json"}, timeout=60)
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    if metadata.get("geometryType") != "esriGeometryPolygon":
        raise ValueError("Detailed Census layer is not a polygon feature layer.")
    page_size = min(int(metadata.get("maxRecordCount", 2000)), 2000)

    query_url = f"{layer_url.rstrip('/')}/query"
    where = f"STATEFP20='{state_fips}' AND COUNTYFP20='{county_fips}'"
    count_response = session.get(
        query_url,
        params={"where": where, "returnCountOnly": "true", "f": "json"},
        timeout=60,
    )
    count_response.raise_for_status()
    server_count = int(count_response.json()["count"])
    if server_count != expected_feature_count:
        raise ValueError(
            f"Detailed tract layer count changed: expected {expected_feature_count}, "
            f"server reports {server_count}."
        )

    frames: list[gpd.GeoDataFrame] = []
    for offset in range(0, server_count, page_size):
        response = session.get(
            query_url,
            params={
                "where": where,
                "outFields": (
                    "OBJECTID,STATEFP20,COUNTYFP20,TRACTCE20,GEOID20,NAME20,"
                    "NAMELSAD20,MTFCC20,FUNCSTAT20,ALAND20,AWATER20"
                ),
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "geojson",
            },
            timeout=(30, 180),
        )
        response.raise_for_status()
        payload = response.json()
        page = gpd.GeoDataFrame.from_features(payload["features"], crs="EPSG:4326")
        if page.empty:
            raise ValueError(f"ArcGIS tract pagination returned an empty page at {offset}.")
        frames.append(page)

    tracts = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )
    if len(tracts) != server_count:
        raise ValueError(
            f"ArcGIS pagination returned {len(tracts)} of {server_count} tract features."
        )
    tracts = tracts.rename(
        columns={
            "STATEFP20": "STATEFP",
            "COUNTYFP20": "COUNTYFP",
            "TRACTCE20": "TRACTCE",
            "GEOID20": "GEOID",
            "NAME20": "NAME",
            "NAMELSAD20": "NAMELSAD",
            "MTFCC20": "MTFCC",
            "FUNCSTAT20": "FUNCSTAT",
            "ALAND20": "ALAND",
            "AWATER20": "AWATER",
        }
    )
    tracts["STATEFP"] = _normalize_code(tracts["STATEFP"], 2)
    tracts["COUNTYFP"] = _normalize_code(tracts["COUNTYFP"], 3)
    tracts["TRACTCE"] = _normalize_code(tracts["TRACTCE"], 6)
    tracts["GEOID"] = _normalize_code(tracts["GEOID"], 11)
    expected_geoid = tracts["STATEFP"] + tracts["COUNTYFP"] + tracts["TRACTCE"]
    if not tracts["GEOID"].equals(expected_geoid):
        raise ValueError("Detailed TIGER GEOID fields are internally inconsistent.")
    if tracts["OBJECTID"].duplicated().any() or tracts["GEOID"].duplicated().any():
        raise ValueError("Detailed tract layer contains duplicate OBJECTID or GEOID values.")
    tracts = tracts.sort_values("GEOID").reset_index(drop=True)
    tracts.to_parquet(destination, index=False)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return DownloadRecord(destination, digest, destination.stat().st_size, layer_url)


def _normalize_code(series, width: int):
    def convert(value: object) -> str:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return str(value).strip().zfill(width)

    return series.map(convert)


def load_city_tracts(
    census_path: Path,
    city_boundary: gpd.GeoDataFrame,
    *,
    analysis_crs: str,
    state_fips: str,
    county_fips: str,
    minimum_city_area_fraction: float,
    exclude_special_use_tracts: bool = False,
) -> gpd.GeoDataFrame:
    """Select LA County tracts, clip to the city, and freeze geometry hashes.

    Inclusion depends only on official geometry: at least the configured fraction of
    the original tract area must lie inside the City boundary. No temperature or
    demographic field participates in selection.
    """

    tracts = gpd.read_parquet(census_path)
    tracts["STATEFP"] = _normalize_code(tracts["STATEFP"], 2)
    tracts["COUNTYFP"] = _normalize_code(tracts["COUNTYFP"], 3)
    tracts["GEOID"] = _normalize_code(tracts["GEOID"], 11)
    optional_fields = [
        field
        for field in ("TRACTCE", "NAME", "NAMELSAD", "MTFCC", "FUNCSTAT", "ALAND", "AWATER")
        if field in tracts.columns
    ]
    tracts = tracts.loc[
        (tracts["STATEFP"] == state_fips) & (tracts["COUNTYFP"] == county_fips),
        ["GEOID", *optional_fields, "geometry"],
    ].copy()
    if tracts.empty:
        raise ValueError("No Los Angeles County tracts were found in the Census table.")

    tracts["geometry"] = tracts.geometry.map(shapely.make_valid)
    tracts = tracts.loc[~tracts.geometry.is_empty & tracts.geometry.notna()].copy()
    tracts = tracts.to_crs(analysis_crs)
    city = city_boundary.to_crs(analysis_crs).geometry.union_all()

    original_area = tracts.geometry.area
    intersection = tracts.geometry.intersection(city)
    city_fraction = intersection.area / original_area
    keep = city_fraction >= minimum_city_area_fraction
    selected = tracts.loc[keep].copy()
    selected["city_area_fraction"] = city_fraction.loc[keep].to_numpy()
    if {"ALAND", "AWATER"}.issubset(selected.columns):
        total_census_area = selected["ALAND"] + selected["AWATER"]
        selected["census_land_fraction"] = np.divide(
            selected["ALAND"],
            total_census_area,
            out=np.full(len(selected), np.nan, dtype=float),
            where=total_census_area > 0,
        )
    if "TRACTCE" in selected.columns:
        selected["special_use_tract"] = selected["TRACTCE"].str.startswith("98")
    selected["primary_included"] = True
    selected["primary_exclusion_reason"] = ""
    if exclude_special_use_tracts and "special_use_tract" in selected.columns:
        selected.loc[selected["special_use_tract"], "primary_included"] = False
        selected.loc[
            selected["special_use_tract"], "primary_exclusion_reason"
        ] = "census_special_use_98xxxx"
    selected["geometry"] = intersection.loc[selected.index].to_numpy()
    selected = selected.sort_values("GEOID").reset_index(drop=True)
    selected["geometry_sha256"] = [
        hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()
        for geometry in selected.geometry
    ]
    if selected["GEOID"].duplicated().any():
        raise ValueError("Duplicate GEOIDs after tract selection.")
    return selected


def assign_spatial_blocks(
    tracts: gpd.GeoDataFrame,
    *,
    block_size_km: float,
) -> gpd.GeoDataFrame:
    """Assign target-independent square blocks from tract centroids."""

    if tracts.crs is None or tracts.crs.is_geographic:
        raise ValueError("Spatial blocks require a projected tract CRS.")
    block_size_m = float(block_size_km) * 1000.0
    if block_size_m <= 0:
        raise ValueError("Spatial block size must be positive.")
    result = tracts.copy()
    centroids = result.geometry.centroid
    block_x = np.floor(centroids.x.to_numpy() / block_size_m).astype(int)
    block_y = np.floor(centroids.y.to_numpy() / block_size_m).astype(int)
    result["spatial_block"] = [
        f"x{x:+05d}_y{y:+05d}" for x, y in zip(block_x, block_y, strict=True)
    ]
    geoid = result["GEOID"].astype(str).to_numpy()

    def deterministic_quartile(values: np.ndarray) -> np.ndarray:
        order = np.lexsort((geoid, values))
        quartiles = np.empty(len(values), dtype=int)
        quartiles[order] = np.minimum(3, np.arange(len(values)) * 4 // len(values))
        return quartiles

    result["longitude_quartile"] = deterministic_quartile(centroids.x.to_numpy())
    result["latitude_quartile"] = deterministic_quartile(centroids.y.to_numpy())
    return result
