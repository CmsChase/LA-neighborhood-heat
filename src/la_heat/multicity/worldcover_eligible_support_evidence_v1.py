"""Exact WorldCover item, mosaic, and 30 m eligible-support evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
import shapely
from rasterio.features import rasterize
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject, transform_bounds
from shapely.geometry import box, shape

from la_heat.grid import FixedGrid, build_fixed_grid
from la_heat.multicity.missing_support_calibration_evidence_v1 import (
    CITY_IDS,
    WORLDCOVER_GLOBAL_PATH,
    EvidenceConfig,
    MissingSupportCalibrationEvidenceV1Error,
    _city_geography_path,
    _city_worldcover_path,
    assert_no_secrets,
    canonical_unsigned_url,
    checkpoint_record,
    file_record,
    read_json_with_commit,
    write_manifest_no_clobber,
)
from la_heat.provenance import (
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    sha256_file,
)

ALGORITHM_VERSION: Final = "four-city-worldcover-eligible-support-v1"
COMPLETE_STATE: Final = "complete_target_blind_four_city_worldcover_support"
ITEM_ID = re.compile(
    r"^(?:ESA_)?WorldCover_10m_2020_v100_(?P<tile>[NS]\d{2}[EW]\d{3})(?:_Map)?$",
    re.IGNORECASE,
)
FILENAME = re.compile(
    r"^ESA_WorldCover_10m_2020_v100_(?P<tile>[NS]\d{2}[EW]\d{3})_Map\.tif$",
    re.IGNORECASE,
)
WORLD_COVER_CLASSES: Final = frozenset(
    {0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100}
)


class _BoundedClient:
    def __init__(self, session: Any, config: EvidenceConfig) -> None:
        self.session = session
        self.config = config
        limits = config.raw["worldcover"]["limits"]
        self.allowed_hosts = {str(value).lower() for value in limits["allowed_hosts"]}
        self.maximum_requests = int(limits["maximum_requests"])
        self.maximum_single = int(limits["maximum_single_asset_bytes"])
        self.maximum_total = int(limits["maximum_total_asset_bytes"])
        self.request_count = 0
        self.downloaded_bytes = 0
        self._sas_tokens: dict[tuple[str, str], str] = {}

    def _authorize(self, method: str, url: str, *, asset: bool = False) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        limits = self.config.raw["worldcover"]["limits"]
        if parsed.scheme != "https" or host not in self.allowed_hosts or parsed.fragment:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover {method} URL is outside the allowlist."
            )
        path = parsed.path
        if host == "planetarycomputer.microsoft.com":
            allowed = path == limits["allowed_stac_path"] or path.startswith(
                limits["allowed_sas_path_prefix"]
            )
        else:
            prefix_by_host = limits["allowed_asset_path_prefix_by_host"]
            allowed = (
                asset
                and host in prefix_by_host
                and path.startswith(str(prefix_by_host[host]))
            )
        if not allowed:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover {method} path is outside the allowlist."
            )
        self.request_count += 1
        if self.request_count > self.maximum_requests:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover request limit exceeded."
            )

    def post_json(self, url: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
        self._authorize("POST", url)
        response = self.session.post(
            url,
            json=dict(payload),
            timeout=(20, 120),
            allow_redirects=False,
        )
        if 300 <= int(response.status_code) < 400:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover STAC redirect is prohibited."
            )
        response.raise_for_status()
        content = bytes(response.content)
        if len(content) > 64 * 1024 * 1024:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover STAC response exceeded 64 MiB."
            )
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover STAC response is not JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover STAC response is not an object."
            )
        assert_no_secrets(parsed, label="WorldCover STAC response")
        return parsed, content

    def _sas_url(self, unsigned_url: str) -> str:
        parsed = urlsplit(unsigned_url)
        if parsed.hostname != "ai4edataeuwest.blob.core.windows.net":
            return unsigned_url
        parts = parsed.path.lstrip("/").split("/", 1)
        if len(parts) != 2:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover Azure asset lacks a container path."
            )
        account = parsed.hostname.split(".", 1)[0]
        container = parts[0]
        key = account, container
        if key not in self._sas_tokens:
            url = (
                "https://planetarycomputer.microsoft.com/api/sas/v1/token/"
                f"{account}/{container}"
            )
            self._authorize("GET", url)
            response = self.session.get(
                url, timeout=(20, 60), allow_redirects=False
            )
            if 300 <= int(response.status_code) < 400:
                response.close()
                raise MissingSupportCalibrationEvidenceV1Error(
                    "WorldCover SAS redirect is prohibited."
                )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise MissingSupportCalibrationEvidenceV1Error(
                    "WorldCover SAS endpoint did not return a token."
                )
            self._sas_tokens[key] = token.lstrip("?")
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                self._sas_tokens[key],
                "",
            )
        )

    def download(self, unsigned_url: str, destination_root: Path) -> dict[str, Any]:
        canonical = canonical_unsigned_url(unsigned_url)
        url_identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record_path = destination_root / "url_records" / f"{url_identity}.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("unsigned_url") != canonical:
                raise MissingSupportCalibrationEvidenceV1Error(
                    "WorldCover cache URL identity changed."
                )
            cached = destination_root / "assets_by_sha256" / f"{record['sha256']}.tif"
            if (
                not cached.is_file()
                or cached.stat().st_size != int(record["bytes"])
                or sha256_file(cached) != record["sha256"]
            ):
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Authenticated WorldCover cache is missing or corrupted."
                )
            return {**record, "path": cached}

        signed = self._sas_url(canonical)
        self._authorize("GET", canonical, asset=True)
        response = self.session.get(
            signed,
            stream=True,
            timeout=(20, 240),
            allow_redirects=False,
        )
        if 300 <= int(response.status_code) < 400:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover asset redirect is prohibited."
            )
        response.raise_for_status()
        length_value = response.headers.get("Content-Length")
        if length_value is None:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover asset lacks Content-Length."
            )
        length = int(length_value)
        if length <= 0 or length > self.maximum_single:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover asset size exceeds the preregistration."
            )
        if self.downloaded_bytes + length > self.maximum_total:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover total download limit exceeded."
            )
        assets_root = destination_root / "assets_by_sha256"
        assets_root.mkdir(parents=True, exist_ok=True)
        temporary = assets_root / f".{url_identity}.{uuid.uuid4().hex}.partial"
        digest = hashlib.sha256()
        observed = 0
        try:
            with temporary.open("xb") as handle:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    observed += len(block)
                    if observed > length:
                        raise MissingSupportCalibrationEvidenceV1Error(
                            "WorldCover response exceeded Content-Length."
                        )
                    digest.update(block)
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            response.close()
        if observed != length:
            temporary.unlink(missing_ok=True)
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover response ended before Content-Length."
            )
        sha = digest.hexdigest()
        destination = assets_root / f"{sha}.tif"
        try:
            if destination.exists():
                if destination.stat().st_size != length or sha256_file(destination) != sha:
                    raise MissingSupportCalibrationEvidenceV1Error(
                        "Content-addressed WorldCover cache collision."
                    )
            else:
                os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        record = {
            "unsigned_url": canonical,
            "bytes": length,
            "sha256": sha,
            "etag": str(response.headers.get("ETag", "")),
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(record, indent=2).encode("utf-8")
        temp_record = record_path.with_name(
            f".{record_path.name}.{uuid.uuid4().hex}.partial"
        )
        temp_record.write_bytes(raw)
        try:
            os.link(temp_record, record_path)
        finally:
            temp_record.unlink(missing_ok=True)
        self.downloaded_bytes += length
        return {**record, "path": destination}


def _grid_record(grid: FixedGrid) -> dict[str, Any]:
    return {
        "crs": grid.crs,
        "resolution_m": grid.resolution_m,
        "anchor_x_m": grid.anchor_x_m,
        "anchor_y_m": grid.anchor_y_m,
        "bounds": [grid.left, grid.bottom, grid.right, grid.top],
        "shape": [grid.height, grid.width],
        "transform": list(grid.transform),
        "sha256": grid.sha256,
    }


def _array_sha(array: np.ndarray, *, dtype: str) -> str:
    canonical = np.asarray(array, dtype=dtype, order="C")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def _mask_sha(mask: np.ndarray) -> str:
    packed = np.packbits(np.asarray(mask, dtype=np.uint8).ravel(order="C"), bitorder="big")
    return hashlib.sha256(packed.tobytes()).hexdigest()


def _manifest_frames(
    config: EvidenceConfig, city_id: str
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, Any]]:
    manifest_path = config.project_path(_city_geography_path(city_id))
    manifest = read_json_with_commit(manifest_path, label=f"{city_id} geography")
    tables = manifest["output_tables"]
    boundary = gpd.read_parquet(config.project_path(str(tables["city_boundary"]["path"])))
    primary = gpd.read_parquet(config.project_path(str(tables["primary_tracts"]["path"])))
    if primary.empty or primary["tract_geoid"].duplicated().any():
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Invalid primary geography for WorldCover: {city_id}"
        )
    return boundary, primary.sort_values("tract_geoid").reset_index(drop=True), manifest


def _target_crs(config: EvidenceConfig, city_id: str) -> str:
    city_path = config.project_path(f"configs/multicity/cities/{city_id}.toml")
    import tomllib

    with city_path.open("rb") as handle:
        raw = tomllib.load(handle)
    crs = str(raw["city"]["target_grid_crs"])
    if crs not in {"EPSG:32611", "EPSG:32612", "EPSG:32615", "EPSG:32616"}:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Unexpected target CRS for {city_id}: {crs}"
        )
    return crs


def _search_items(
    config: EvidenceConfig,
    *,
    client: _BoundedClient,
    city_id: str,
    boundary: gpd.GeoDataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    geometry = shapely.make_valid(boundary.to_crs("EPSG:4326").geometry.union_all())
    query = {
        "collections": [config.raw["worldcover"]["stac_collection"]],
        "datetime": "2020-01-01T00:00:00Z/2020-12-31T23:59:59Z",
        "bbox": [float(value) for value in geometry.bounds],
        "limit": 100,
    }
    url = str(config.raw["worldcover"]["stac_api"]).rstrip("/") + "/search"
    payload, raw = client.post_json(url, query)
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover STAC returned no features for {city_id}."
        )
    next_links = [
        link
        for link in payload.get("links", [])
        if isinstance(link, dict) and link.get("rel") == "next"
    ]
    if next_links:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Unexpected WorldCover STAC pagination; fixed limit should cover the city."
        )
    raw_path = config.project_path(
        str(config.raw["outputs"]["raw_stage_directory"])
    ) / f"worldcover/{city_id}/stac_search.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_path.exists() and raw_path.read_bytes() != raw:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover STAC snapshot changed for {city_id}."
        )
    if not raw_path.exists():
        temp = raw_path.with_name(f".{raw_path.name}.{uuid.uuid4().hex}.partial")
        temp.write_bytes(raw)
        try:
            os.link(temp, raw_path)
        finally:
            temp.unlink(missing_ok=True)
    return [dict(feature) for feature in features], {
        "query_sha256": canonical_sha256(query),
        "raw_response": file_record(config, raw_path),
    }


def _validate_items(
    config: EvidenceConfig,
    *,
    features: Sequence[Mapping[str, Any]],
    boundary: gpd.GeoDataFrame,
) -> list[dict[str, Any]]:
    city_geometry = shapely.make_valid(
        boundary.to_crs("EPSG:4326").geometry.union_all()
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feature in features:
        item_id = str(feature.get("id", ""))
        match = ITEM_ID.fullmatch(item_id)
        if match is None:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Unexpected WorldCover item ID: {item_id}"
            )
        if item_id in seen or feature.get("collection") != "esa-worldcover":
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover item identity or collection changed."
            )
        seen.add(item_id)
        assets = feature.get("assets")
        if not isinstance(assets, Mapping) or "map" not in assets:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover item {item_id} does not expose map."
            )
        href = canonical_unsigned_url(str(assets["map"].get("href", "")))
        filename_match = FILENAME.fullmatch(Path(urlsplit(href).path).name)
        if filename_match is None or filename_match.group("tile").upper() != match.group(
            "tile"
        ).upper():
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover item/asset tile identity changed: {item_id}"
            )
        geometry_value = feature.get("geometry")
        if not isinstance(geometry_value, Mapping):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover item {item_id} lacks geometry."
            )
        item_geometry = shape(geometry_value)
        if (
            item_geometry.is_empty
            or not item_geometry.is_valid
            or item_geometry.intersection(city_geometry).area <= 0
        ):
            continue
        selected.append(
            {
                "item_id": item_id,
                "tile_id": match.group("tile").upper(),
                "unsigned_asset_url": href,
                "stac_asset_keys": sorted(str(key) for key in assets),
                "stac_geometry_sha256": canonical_sha256(
                    shapely.to_wkb(
                        shapely.normalize(item_geometry),
                        hex=True,
                        output_dimension=2,
                        byte_order=1,
                    )
                ),
            }
        )
    if not selected:
        raise MissingSupportCalibrationEvidenceV1Error(
            "No positive-intersection WorldCover item survived."
        )
    return sorted(selected, key=lambda row: row["item_id"])


def _validate_asset(
    record: Mapping[str, Any], *, boundary: gpd.GeoDataFrame
) -> dict[str, Any]:
    path = Path(record["path"])
    with rasterio.open(path) as source:
        if (
            source.count != 1
            or source.dtypes != ("uint8",)
            or source.crs is None
            or source.transform.b != 0
            or source.transform.d != 0
            or source.transform.a <= 0
            or source.transform.e >= 0
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover raster schema changed."
            )
        if source.nodata not in {None, 0.0}:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover nodata is not class 0."
            )
        footprint_wgs84 = box(
            *transform_bounds(source.crs, "EPSG:4326", *source.bounds, densify_pts=21)
        )
        city = boundary.to_crs("EPSG:4326").geometry.union_all()
        if footprint_wgs84.intersection(city).area <= 0:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Downloaded WorldCover raster has no positive city intersection."
            )
        domain_values: set[int] = set()
        for _, window in source.block_windows(1):
            domain_values.update(
                int(value)
                for value in np.unique(source.read(1, window=window, masked=False))
            )
        domain = sorted(domain_values)
        if not set(domain).issubset(WORLD_COVER_CLASSES):
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover source contains an unknown class."
            )
        return {
            "crs": source.crs.to_string(),
            "shape": [source.height, source.width],
            "transform": list(source.transform),
            "resolution": [float(source.res[0]), float(source.res[1])],
            "dtype": source.dtypes[0],
            "nodata": source.nodata,
            "class_domain": domain,
            "raster_footprint_wgs84_sha256": canonical_sha256(
                shapely.to_wkb(
                    shapely.normalize(footprint_wgs84),
                    hex=True,
                    output_dimension=2,
                    byte_order=1,
                )
            ),
        }


def _mosaic_to_grid(
    paths: Sequence[Path], *, boundary: gpd.GeoDataFrame, grid: FixedGrid
) -> np.ndarray:
    datasets = [rasterio.open(path) for path in paths]
    try:
        crs_set = {source.crs for source in datasets}
        resolution_set = {
            (round(float(source.res[0]), 15), round(float(source.res[1]), 15))
            for source in datasets
        }
        if len(crs_set) != 1 or len(resolution_set) != 1:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover assets do not share one native grid family."
            )
        source_crs = datasets[0].crs
        native_bounds = transform_bounds(
            "EPSG:4326",
            source_crs,
            *boundary.to_crs("EPSG:4326").total_bounds,
            densify_pts=21,
        )
        native, native_transform = merge(
            datasets,
            bounds=native_bounds,
            res=datasets[0].res,
            nodata=0,
            dtype="uint8",
            method="first",
            target_aligned_pixels=True,
        )
        if native.shape[0] != 1:
            raise MissingSupportCalibrationEvidenceV1Error(
                "WorldCover native mosaic is not one band."
            )
        destination = np.zeros(grid.shape, dtype=np.uint8)
        with rasterio.Env(GDAL_NUM_THREADS="1"):
            reproject(
                source=native[0],
                destination=destination,
                src_transform=native_transform,
                src_crs=source_crs,
                src_nodata=0,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=0,
                resampling=Resampling.mode,
                num_threads=1,
            )
        return destination
    finally:
        for source in datasets:
            source.close()


def _zones(primary: gpd.GeoDataFrame, grid: FixedGrid) -> np.ndarray:
    sorted_primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    projected = sorted_primary.to_crs(grid.crs)
    values = rasterize(
        [
            (geometry, index + 1)
            for index, geometry in enumerate(projected.geometry)
        ],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    expected = set(range(1, len(sorted_primary) + 1))
    observed = set(int(value) for value in np.unique(values[values > 0]))
    if observed != expected:
        raise MissingSupportCalibrationEvidenceV1Error(
            "WorldCover zone raster omitted a primary tract."
        )
    return values


def _support_table(
    *,
    city_id: str,
    geoids: Sequence[str],
    zones: np.ndarray,
    classes: np.ndarray,
    eligible: np.ndarray,
    grid: FixedGrid,
) -> tuple[pd.DataFrame, dict[str, str]]:
    zone_sha = _array_sha(zones, dtype="<i4")
    class_sha = _array_sha(classes, dtype="u1")
    eligible_sha = _mask_sha(eligible)
    flat = np.arange(zones.size, dtype="<u8").reshape(zones.shape)
    rows: list[dict[str, Any]] = []
    for zone_value, geoid in enumerate(geoids, start=1):
        zone = zones == zone_value
        valid = zone & (classes != 0)
        water = valid & (classes == 80)
        selected = zone & eligible
        selected_indices = np.asarray(flat[selected], dtype="<u8")
        prefix = canonical_sha256(
            {
                "algorithm_version": ALGORITHM_VERSION,
                "city_id": city_id,
                "tract_geoid": str(geoid),
                "grid_sha256": grid.sha256,
                "zone_sha256": zone_sha,
                "eligible_mask_sha256": eligible_sha,
            }
        ).encode("ascii")
        identity = hashlib.sha256(prefix + selected_indices.tobytes()).hexdigest()
        rows.append(
            {
                "city_id": city_id,
                "tract_geoid": str(geoid),
                "tract_zone_cell_count": int(zone.sum()),
                "worldcover_spatially_covered_cell_count": int(valid.sum()),
                "worldcover_nodata_cell_count": int((zone & (classes == 0)).sum()),
                "worldcover_valid_cell_count": int(valid.sum()),
                "worldcover_permanent_water_cell_count": int(water.sum()),
                "eligible_cell_count": int(selected.sum()),
                "eligible_cell_identity_sha256": identity,
            }
        )
    frame = pd.DataFrame(rows)
    if (
        (frame["tract_zone_cell_count"] <= 0).any()
        or (frame["eligible_cell_count"] <= 0).any()
        or not (
            frame["tract_zone_cell_count"]
            == frame["worldcover_valid_cell_count"]
            + frame["worldcover_nodata_cell_count"]
        ).all()
        or not (
            frame["worldcover_valid_cell_count"]
            == frame["eligible_cell_count"]
            + frame["worldcover_permanent_water_cell_count"]
        ).all()
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "WorldCover per-tract support identities failed conservation gates."
        )
    city_identity = canonical_frame_sha256(
        frame, sort_by=["city_id", "tract_geoid"]
    )
    frame["city_support_identity_sha256"] = city_identity
    return frame, {
        "zone_raster_sha256": zone_sha,
        "worldcover_class_raster_sha256": class_sha,
        "eligible_mask_sha256": eligible_sha,
        "city_support_identity_sha256": city_identity,
    }


def _write_raster_no_clobber(
    path: Path,
    array: np.ndarray,
    *,
    grid: FixedGrid,
    dtype: str,
    nodata: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expected_sha = _array_sha(array, dtype=dtype)
    if path.exists():
        with rasterio.open(path) as source:
            if (
                source.count != 1
                or source.shape != grid.shape
                or source.crs is None
                or source.crs.to_string() != grid.crs
                or source.transform != grid.transform
                or source.dtypes != (dtype,)
                or source.nodata != float(nodata)
            ):
                raise MissingSupportCalibrationEvidenceV1Error(
                    f"Existing WorldCover raster grid differs: {path}"
                )
            observed = source.read(1)
        if _array_sha(observed, dtype=dtype) != expected_sha:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Existing WorldCover raster differs: {path}"
            )
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial.tif")
    try:
        with rasterio.open(
            temporary,
            "w",
            driver="GTiff",
            height=grid.height,
            width=grid.width,
            count=1,
            crs=grid.crs,
            transform=grid.transform,
            dtype=dtype,
            nodata=nodata,
            compress="DEFLATE",
            predictor=1,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        ) as destination:
            destination.write(np.asarray(array, dtype=dtype), 1)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_table_no_clobber(path: Path, frame: pd.DataFrame) -> None:
    if path.exists():
        observed = pd.read_parquet(path)
        if canonical_frame_sha256(observed, sort_by=["city_id", "tract_geoid"]) != (
            canonical_frame_sha256(frame, sort_by=["city_id", "tract_geoid"])
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Existing WorldCover support table differs: {path}"
            )
        return
    atomic_parquet(frame, path)


def _city_outputs(config: EvidenceConfig, city_id: str) -> dict[str, Path]:
    root = config.project_path(
        str(config.raw["outputs"]["processed_stage_directory"])
    ) / "worldcover" / city_id
    return {
        "tract_zones_30m": root / "tract_zones_30m.tif",
        "worldcover_classes_30m": root / "worldcover_classes_30m.tif",
        "eligible_mask_30m": root / "eligible_mask_30m.tif",
        "tract_support": root / "tract_eligible_support.parquet",
    }


def _stage_city(
    config: EvidenceConfig,
    *,
    city_id: str,
    client: _BoundedClient,
    plan_record: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = config.project_path(_city_worldcover_path(city_id))
    if manifest_path.is_file():
        return _verify_city(config, city_id)
    boundary, primary, geography = _manifest_frames(config, city_id)
    grid = build_fixed_grid(
        boundary,
        target_crs=_target_crs(config, city_id),
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    features, query = _search_items(
        config, client=client, city_id=city_id, boundary=boundary
    )
    items = _validate_items(config, features=features, boundary=boundary)
    if len(items) > int(config.raw["worldcover"]["limits"]["maximum_unique_assets"]):
        raise MissingSupportCalibrationEvidenceV1Error(
            "WorldCover city item count exceeded the preregistration."
        )
    cache_root = config.project_path(
        str(config.raw["worldcover"]["limits"]["asset_cache"])
    ).parent
    asset_paths: list[Path] = []
    item_records: list[dict[str, Any]] = []
    for item in items:
        download = client.download(item["unsigned_asset_url"], cache_root)
        schema = _validate_asset(download, boundary=boundary)
        asset_paths.append(Path(download["path"]))
        item_records.append(
            {
                **item,
                "asset": {
                    "bytes": download["bytes"],
                    "sha256": download["sha256"],
                    "etag": download["etag"],
                    "cache_path": _relative_path(config, Path(download["path"])),
                },
                "schema": schema,
            }
        )
    ascending = _mosaic_to_grid(asset_paths, boundary=boundary, grid=grid)
    descending = _mosaic_to_grid(
        list(reversed(asset_paths)), boundary=boundary, grid=grid
    )
    if not np.array_equal(ascending, descending):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover mosaic order changed target classes for {city_id}."
        )
    zones = _zones(primary, grid)
    eligible = (zones > 0) & (ascending != 0) & (ascending != 80)
    geoids = tuple(primary["tract_geoid"].astype(str).tolist())
    support, identities = _support_table(
        city_id=city_id,
        geoids=geoids,
        zones=zones,
        classes=ascending,
        eligible=eligible,
        grid=grid,
    )
    outputs = _city_outputs(config, city_id)
    _write_raster_no_clobber(
        outputs["tract_zones_30m"], zones, grid=grid, dtype="int32", nodata=0
    )
    _write_raster_no_clobber(
        outputs["worldcover_classes_30m"],
        ascending,
        grid=grid,
        dtype="uint8",
        nodata=0,
    )
    _write_raster_no_clobber(
        outputs["eligible_mask_30m"],
        eligible.astype(np.uint8),
        grid=grid,
        dtype="uint8",
        nodata=0,
    )
    _write_table_no_clobber(outputs["tract_support"], support)
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_city_worldcover_support",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "city_id": city_id,
        "plan_authorization": dict(plan_record),
        "geography": {
            "path": _city_geography_path(city_id),
            "commit_sha256": geography["commit_sha256"],
            "primary_tract_count": len(primary),
        },
        "source": {
            "provider": config.raw["worldcover"]["provider"],
            "collection": "esa-worldcover",
            "year": 2020,
            "version": "v100",
            "asset": "map",
            "query": query,
            "items": item_records,
        },
        "grid": _grid_record(grid),
        "mosaic": {
            "native_mosaic_before_reprojection": True,
            "item_order": [item["item_id"] for item in item_records],
            "reverse_order_parity": True,
            "resampling": "mode",
            "class_domain": sorted(int(value) for value in np.unique(ascending)),
            "class_raster_sha256": identities["worldcover_class_raster_sha256"],
        },
        "support": {
            **identities,
            "tract_count": len(support),
            "total_zone_cells": int(support["tract_zone_cell_count"].sum()),
            "total_eligible_cells": int(support["eligible_cell_count"].sum()),
            "all_tracts_positive_zone_and_eligible": True,
            "denominator_invariant_across_dates": True,
        },
        "outputs": {name: file_record(config, path) for name, path in outputs.items()},
        "access_contract": {
            "worldcover_static_class_values_read": True,
            "external_target_or_qa_values_read": False,
            "landsat_thermal_values_read": False,
            "sentinel_values_read": False,
            "predictor_values_computed": False,
            "model_fit_or_prediction_performed": False,
            "final_evaluation_outputs_opened": False,
        },
    }
    write_manifest_no_clobber(payload, manifest_path)
    return _verify_city(config, city_id)


def _relative_path(config: EvidenceConfig, path: Path) -> str:
    return path.resolve().relative_to(config.project_root).as_posix()


def _verify_city(config: EvidenceConfig, city_id: str) -> dict[str, Any]:
    path = config.project_path(_city_worldcover_path(city_id))
    payload = read_json_with_commit(path, label=f"{city_id} WorldCover support")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != "complete_target_blind_city_worldcover_support"
        or payload.get("city_id") != city_id
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover city manifest changed: {city_id}"
        )
    query_record = payload["source"]["query"]["raw_response"]
    query_path = config.project_path(str(query_record["path"]))
    if file_record(config, query_path) != query_record:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover STAC response changed: {city_id}"
        )
    for item in payload["source"]["items"]:
        asset = item["asset"]
        asset_path = config.project_path(str(asset["cache_path"]))
        if (
            not asset_path.is_file()
            or asset_path.stat().st_size != int(asset["bytes"])
            or sha256_file(asset_path) != asset["sha256"]
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover raw asset changed: {city_id}/{item['item_id']}"
            )
    outputs = payload.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(_city_outputs(config, city_id)):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover output set changed: {city_id}"
        )
    for record in outputs.values():
        file_path = config.project_path(str(record["path"]))
        if file_record(config, file_path) != record:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover output hash changed: {city_id}"
            )
    table = pd.read_parquet(
        config.project_path(str(outputs["tract_support"]["path"]))
    )
    if (
        len(table) != int(payload["support"]["tract_count"])
        or (table["eligible_cell_count"] <= 0).any()
        or table["city_support_identity_sha256"].nunique() != 1
        or table["city_support_identity_sha256"].iloc[0]
        != payload["support"]["city_support_identity_sha256"]
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"WorldCover support table changed: {city_id}"
        )
    return payload


def _verify_global(config: EvidenceConfig) -> dict[str, Any]:
    path = config.project_path(WORLDCOVER_GLOBAL_PATH)
    payload = read_json_with_commit(path, label="four-city WorldCover terminal")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or set(payload.get("cities", {})) != set(CITY_IDS)
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Four-city WorldCover terminal changed."
        )
    for city_id in CITY_IDS:
        city = _verify_city(config, city_id)
        if payload["cities"][city_id]["commit_sha256"] != city["commit_sha256"]:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"WorldCover terminal lost {city_id}."
            )
    return payload


def stage_worldcover_eligible_support_evidence_v1(
    config: EvidenceConfig,
    *,
    plan_record: Mapping[str, Any],
    session: Any | None = None,
) -> dict[str, Any]:
    """Stage or authenticate exact four-city WorldCover support evidence."""

    global_path = config.project_path(WORLDCOVER_GLOBAL_PATH)
    if global_path.is_file():
        return _verify_global(config)
    active_session = requests.Session() if session is None else session
    client = _BoundedClient(active_session, config)
    city_payloads = {
        city_id: _stage_city(
            config, city_id=city_id, client=client, plan_record=plan_record
        )
        for city_id in CITY_IDS
    }
    unique_assets: dict[str, dict[str, Any]] = {}
    for payload in city_payloads.values():
        for item in payload["source"]["items"]:
            unique_assets[item["asset"]["sha256"]] = item["asset"]
    if len(unique_assets) > int(
        config.raw["worldcover"]["limits"]["maximum_unique_assets"]
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Four-city WorldCover unique asset count exceeded the preregistration."
        )
    global_payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "plan_authorization": dict(plan_record),
        "cities": {
            city_id: checkpoint_record(
                config, config.project_path(_city_worldcover_path(city_id))
            )
            for city_id in CITY_IDS
        },
        "unique_asset_count": len(unique_assets),
        "unique_assets": {
            sha: {key: value for key, value in record.items() if key != "cache_path"}
            for sha, record in sorted(unique_assets.items())
        },
        "network_audit": {
            "request_count": client.request_count,
            "downloaded_asset_bytes": client.downloaded_bytes,
            "redirects_followed": 0,
            "signed_urls_persisted": False,
        },
        "all_four_city_supports_complete": True,
        "support_is_predictor_values": False,
        "predictor_build_authorized": False,
        "external_target_or_qa_values_read": False,
        "next_gate": "external_city_sentinel_calibration_smoke_v1",
    }
    write_manifest_no_clobber(global_payload, global_path)
    return _verify_global(config)
