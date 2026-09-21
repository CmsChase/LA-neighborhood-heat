"""Colorado six-date, mirror-boundary, nonthermal predictor feasibility trial.

The only target-side operation is a final join on existing label keys. This
script never opens target temperatures, models, or evaluation-city assets.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
import time as clock
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely

from experiments.source_city_qa_mirror_pilot import _grid_and_zones
from la_heat.calendar_features import build_calendar_features
from la_heat.multicity import geography
from la_heat.multicity import portable_predictor_components as components
from la_heat.multicity import portable_predictor_source_evidence_v1 as source_evidence
from la_heat.multicity.config import CitySpec
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    CALENDAR_FEATURES,
    DAYMET_FEATURES,
    FEATURE_NAMES,
    SENTINEL_FEATURES,
    STATIC_FEATURES,
)
from la_heat.multicity.source_footprints import OPEN_TOPOGRAPHY_SRTM_BASE_URL
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    atomic_text,
    canonical_sha256,
    sha256_file,
)
from la_heat.static_features import build_static_support

ROOT = Path(__file__).resolve().parents[1]
CITY = "colorado_springs_co"
DATES = (
    "2020-10-31",
    "2021-05-27",
    "2021-10-18",
    "2022-10-29",
    "2023-10-24",
    "2024-05-03",
)
SUPPORT_DIR = ROOT / "exports/SOURCE_CITY_QA_PILOT/mirror_exploratory" / CITY
OUTPUT = ROOT / "exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror" / CITY
LABEL_DIR = ROOT / "exports/SOURCE_CITY_TARGET_AVAILABILITY/exploratory_mirror" / CITY / "dates"
ACTIVE = ROOT / "manifests/multicity/ACTIVE_STAGE.json"
OFFICIAL_SUPPORT_DIR = ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020" / CITY
OFFICIAL_OUTPUT = ROOT / "exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020" / CITY
OFFICIAL_TARGET_DIR = ROOT / "exports/SOURCE_CITY_TARGET_AVAILABILITY/official_2020" / CITY
MIRROR_OUTPUT = OUTPUT
SUPPORT_SHA = "06b64ebd0f7088dac8223687349f0f5056b97f2b9a193995522a90e37b6aafcc"
GRID_SHA = "c08ee85e1873c23b1289ea18475c5eeac92621aa1da1ca766fcc35b4391bdae7"
MARKER = "基于冻结镜像边界的探索性开发预测变量，官方等价性待核验"
OFFICIAL_SENTINEL_RETRYABLE_ERRORS = frozenset(
    {
        "ReadTimeout",
        "ConnectionError",
        "ProxyError",
        "RasterioIOError",
        "WarpOperationError",
        "TimeoutError",
    }
)
OFFICIAL_SENTINEL_MAX_ATTEMPTS_PER_ACQUISITION = 6
OFFICIAL_SENTINEL_RETRY_DELAYS_SECONDS = (5, 15, 30, 60, 120)
SPEC = CitySpec(
    CITY,
    "Colorado Springs",
    "08",
    "0816000",
    "America/Denver",
    "EPSG:32613",
    "predictor_trial_only",
    "sealed",
    Path(__file__),
)


def _preflight(*, local_daymet: bool = False) -> None:
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_exploratory_predictor_trial"]
    if local_daymet:
        local = scope.get("local_daymet_entrypoint", {})
        problems = []
        allowed_states = {
            "colorado_six_date_predictor_trial_paused_pending_local_earthdata_token_and_sentinel_offset_resolution",
            "paused_colorado_six_date_exploratory_predictor_trial_only",
        }
        if stage["state"] not in allowed_states:
            problems.append(f"ACTIVE_STAGE.state is not an approved paused state: {stage['state']}")
        if not local.get("approved_for_private_interactive_run"):
            problems.append("local_daymet_entrypoint approval is closed")
        inventory_path = OUTPUT / "daymet_inventory.json"
        if not inventory_path.is_file():
            problems.append("frozen Daymet inventory is missing")
        elif local.get("daymet_inventory_sha256") != sha256_file(inventory_path):
            problems.append("frozen Daymet inventory SHA-256 differs from authorization")
        forbidden = (
            "read_public_predictor_sources",
            "build_predictor",
            "read_source_targets",
            "fit_model",
            "score_model",
            "read_external_targets",
            "read_new_candidate_targets",
        )
        enabled = [name for name in forbidden if stage["permissions"][name]]
        if enabled:
            problems.append("other stage permissions remain open: " + ", ".join(enabled))
        if scope["allowed_city_id"] != CITY:
            problems.append("allowed city differs from frozen Colorado scope")
        if tuple(scope["allowed_target_dates"]) != DATES:
            problems.append("allowed dates differ from the six frozen dates")
        if scope["full_predictor_key_count"] != 660:
            problems.append("allowed full predictor key count differs from 660")
        support_path = SUPPORT_DIR / "fixed_support.npz"
        if not support_path.is_file():
            problems.append("frozen fixed-land support is missing")
        elif sha256_file(support_path) != SUPPORT_SHA:
            problems.append("frozen fixed-land support SHA-256 differs from contract")
        if problems:
            raise RuntimeError("Frozen local Daymet-only preflight failed: " + "; ".join(problems))
        return
    if (
        stage["state"] != "running_colorado_six_date_exploratory_predictor_trial_only"
        or not stage["permissions"]["read_public_predictor_sources"]
        or not stage["permissions"]["build_predictor"]
        or stage["permissions"]["fit_model"]
        or stage["permissions"]["score_model"]
        or stage["permissions"]["read_new_candidate_targets"]
        or scope["allowed_city_id"] != CITY
        or tuple(scope["allowed_target_dates"]) != DATES
        or scope["full_predictor_key_count"] != 660
        or sha256_file(SUPPORT_DIR / "fixed_support.npz") != SUPPORT_SHA
    ):
        raise RuntimeError("Colorado six-date predictor permission or support drifted")


def support(*, local_daymet: bool = False) -> components.PortableCitySupport:
    _preflight(local_daymet=local_daymet)
    record = json.loads((SUPPORT_DIR / "support.json").read_text(encoding="utf-8"))
    place = geography.standardize_place(
        gpd.read_file(SUPPORT_DIR / "mirror_place/features.geojson"), SPEC
    )
    tracts = geography.standardize_tracts(
        gpd.read_file(SUPPORT_DIR / "mirror_tract/features_0000.geojson"), SPEC
    )
    _, primary = geography.select_city_tracts(
        place,
        tracts,
        city_id=CITY,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    geoids = tuple(primary.tract_geoid.astype(str))
    if geoids != tuple(record["tract_geoids"]) or len(geoids) != 110:
        raise RuntimeError("Frozen tract keys changed")
    grid, zones, _ = _grid_and_zones(place, primary, "EPSG:32613")
    if grid.sha256 != GRID_SHA:
        raise RuntimeError("Frozen Colorado grid changed")
    with np.load(SUPPORT_DIR / "fixed_support.npz") as cached:
        if not np.array_equal(zones, cached["zones"]):
            raise RuntimeError("Frozen tract raster changed")
        eligible = cached["eligible"].copy()
    fixed = build_static_support(zones, eligible, geoids=geoids, grid_identity=SUPPORT_SHA)
    return components.PortableCitySupport(
        city_id=CITY,
        grid=grid,
        zones=zones,
        eligible_land=eligible,
        tract_geoids=geoids,
        tracts=primary,
        static_support=fixed,
        worldcover_manifest={"commit_sha256": SUPPORT_SHA},
        geography_manifest={"commit_sha256": record["mirror"]["place_geometry_sha256"]},
    )


def keys(city_support: components.PortableCitySupport) -> pd.DataFrame:
    return (
        pd.MultiIndex.from_product(
            [city_support.tract_geoids, pd.to_datetime(DATES)],
            names=["tract_geoid", "target_date"],
        )
        .to_frame(index=False)
        .assign(city_id=CITY)[["city_id", "tract_geoid", "target_date"]]
    )


def _download(url: str, destination: Path, *, params=None, maximum_bytes: int) -> dict:
    _preflight()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        partial = destination.with_suffix(destination.suffix + ".partial")
        try:
            with requests.get(url, params=params, stream=True, timeout=(15, 90)) as response:
                response.raise_for_status()
                count = 0
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            count += len(chunk)
                            if count > maximum_bytes:
                                raise RuntimeError("Source exceeds frozen trial byte cap")
                            stream.write(chunk)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
    return {
        "path": destination.relative_to(ROOT).as_posix(),
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "url_without_query": url,
    }


def run_static() -> dict:
    """Reuse the existing fixed 18-feature static algorithm on mirror support."""
    s = support()
    raw = OUTPUT / "raw_static"
    bounds = source_evidence._aligned_nlcd_bounds(
        s.tracts.to_crs("EPSG:4326"), resolution=30, edge_offset=15, halo_pixels=2
    )
    source_records = []
    products = (
        ("land_cover", "mrlc_download__NLCD_2016_Land_Cover_L48"),
        ("impervious", "mrlc_download__NLCD_2016_Impervious_L48"),
    )
    for product, coverage in products:
        path = raw / f"nlcd_2016_{product}.tif"
        record = _download(
            "https://www.mrlc.gov/geoserver/ows",
            path,
            params=source_evidence._nlcd_query(coverage, bounds),
            maximum_bytes=128 * 1024 * 1024,
        )
        source_evidence._inspect_nlcd(path, product=product, bounds=bounds)
        record["coverage_id"] = coverage
        source_records.append(record)
    from la_heat.multicity.source_footprints import derive_srtm_tiles

    city_boundary = gpd.GeoDataFrame(
        geometry=[s.tracts.to_crs("EPSG:4326").union_all()], crs="EPSG:4326"
    )
    terrain_table = derive_srtm_tiles(
        city_boundary,
        analysis_crs="EPSG:32613",
        halo_m=30,
        base_url=OPEN_TOPOGRAPHY_SRTM_BASE_URL,
        filename_suffix=".tif",
    )
    tile_ids = tuple(sorted(terrain_table["tile_id"].astype(str)))
    terrain = []
    for tile_id in tile_ids:
        path = raw / "terrain" / f"{tile_id}.tif"
        record = _download(
            f"{OPEN_TOPOGRAPHY_SRTM_BASE_URL}/{tile_id}.tif",
            path,
            maximum_bytes=64 * 1024 * 1024,
        )
        source_evidence._inspect_srtm(path, tile_id=tile_id)
        source_records.append(record)
        terrain.append(path)
    if len(terrain) not in (1, 2, 3, 4):
        raise RuntimeError("Unexpected Colorado SRTM tile count")
    components.CITY_IDS = (CITY,)
    components.COMPONENT_ROOT = OUTPUT / "components"
    components.RUNTIME_ROOT = OUTPUT / "runtime_components"
    components.load_city_support = lambda _root, city_id: s if city_id == CITY else None
    components._static_source_paths = lambda _root, city_id: components.StaticSourcePaths(
        raw / "nlcd_2016_land_cover.tif",
        raw / "nlcd_2016_impervious.tif",
        tuple(terrain),
        tuple(source_records),
    )
    components.build_static_base_component(ROOT, CITY)
    components.build_gshhg_distance_component(ROOT, CITY)
    result = components.finalize_static_component(ROOT, CITY)
    atomic_json(
        {
            "marker": MARKER,
            "static_result": result,
            "source_records": source_records,
            "srtm_tile_ids": list(tile_ids),
        },
        OUTPUT / "static_trial.json",
    )
    return result


def run_sentinel_inventory() -> dict:
    """Freeze only physical acquisitions intersecting the six d-60:d-1 windows."""
    _preflight()
    import pystac_client

    from la_heat.multicity.portable_sentinel_inventory import (
        _membership_frame,
        _selected_acquisitions_frame,
        _selected_items_frame,
        _snapshot_filename,
        _snapshot_text,
        build_city_window_membership,
    )
    from la_heat.sentinel_inventory import (
        SENTINEL_COLLECTION,
        canonical_stac_item_snapshot,
        query_sentinel_items,
        select_all_reprocessing_cohorts,
        sentinel_record_from_item,
    )

    directory = OUTPUT / "sentinel_inventory"
    marker = directory / "INVENTORY.json"
    if marker.is_file():
        observed = json.loads(marker.read_text(encoding="utf-8"))
        if observed.get("dates") != list(DATES) or observed.get("marker") != MARKER:
            raise RuntimeError("Sentinel inventory drifted")
        return observed
    place = gpd.read_file(SUPPORT_DIR / "mirror_place/features.geojson")
    aoi = shapely.union_all(place.geometry.to_numpy())
    zone = ZoneInfo("America/Denver")
    dates = tuple(date.fromisoformat(value) for value in DATES)
    windows = sorted((d - timedelta(days=60), d - timedelta(days=1)) for d in dates)
    merged: list[tuple[date, date]] = []
    for start, stop in windows:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, stop))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
    client = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    items = {}
    snapshots = {}
    query_windows = []
    for start, stop in merged:
        start_utc = datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC)
        end_utc = datetime.combine(stop + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        interval = (
            start_utc.isoformat().replace("+00:00", "Z")
            + "/"
            + end_utc.isoformat().replace("+00:00", "Z")
        )
        found = query_sentinel_items(
            client,
            intersects=aoi,
            datetime_interval=interval,
            collection=SENTINEL_COLLECTION,
        )
        query_windows.append(
            {
                "first_local_date": start.isoformat(),
                "last_local_date": stop.isoformat(),
                "items": len(found),
            }
        )
        for item in found:
            snapshot = canonical_stac_item_snapshot(item)
            item_id = str(snapshot["id"])
            text = _snapshot_text(snapshot)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if item_id in snapshots and snapshots[item_id]["sha256"] != digest:
                raise RuntimeError("Sentinel item ID has conflicting snapshots")
            snapshots[item_id] = {
                "filename": _snapshot_filename(item_id),
                "sha256": digest,
                "text": text,
            }
            items[item_id] = item
    records = tuple(sentinel_record_from_item(items[item_id]) for item_id in sorted(items))
    candidates = select_all_reprocessing_cohorts(
        records,
        aoi_geometry_wgs84=aoi,
        analysis_crs="EPSG:32613",
    )
    memberships = build_city_window_membership(dates, candidates, timezone="America/Denver")
    retained = {member.acquisition_key for member in memberships}
    selected = tuple(row for row in candidates if row.acquisition_key in retained)
    if not selected:
        raise RuntimeError("No Sentinel acquisition in fixed windows")
    selected_ids = {item.item_id for row in selected for item in row.items}
    chosen_snapshots = {key: snapshots[key] for key in sorted(selected_ids)}
    acquisitions = _selected_acquisitions_frame(CITY, "America/Denver", selected)
    selected_items = _selected_items_frame(CITY, "America/Denver", selected, chosen_snapshots)
    membership = _membership_frame(CITY, memberships)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, frame in (
        ("selected_acquisitions.csv", acquisitions),
        ("selected_items.csv", selected_items),
        ("target_window_membership.csv", membership),
    ):
        frame.to_csv(directory / filename, index=False)
    for snapshot in chosen_snapshots.values():
        atomic_text(snapshot["text"], directory / "stac" / snapshot["filename"])
    summary = {
        "marker": MARKER,
        "city_id": CITY,
        "dates": list(DATES),
        "window_days_before_target": [60, 1],
        "query_windows": query_windows,
        "selected_physical_acquisitions": len(acquisitions),
        "selected_items": len(selected_items),
        "memberships": len(membership),
        "assets": ["B02", "B03", "B04", "B08", "B8A", "B11", "B12", "SCL"],
        "thermal_target_or_qa_read": False,
        "official_geometry_equivalence_verified": False,
        "files_sha256": {
            name: sha256_file(directory / name)
            for name in (
                "selected_acquisitions.csv",
                "selected_items.csv",
                "target_window_membership.csv",
            )
        },
    }
    atomic_json(summary, marker)
    return summary


def run_daymet_inventory() -> dict:
    """Freeze only the six dates' V4R1 granules and Colorado local grid subset."""
    _preflight()
    from la_heat.daymet_grid import DAYMET_CMR_COLLECTION_ID, DAYMET_CMR_GRANULES_URL
    from la_heat.multicity.source_footprints import (
        derive_daymet_index_window,
        fetch_daymet_granule_metadata,
    )

    marker = OUTPUT / "daymet_inventory.json"
    if marker.is_file():
        observed = json.loads(marker.read_text(encoding="utf-8"))
        if observed.get("dates") != list(DATES):
            raise RuntimeError("Daymet inventory date drift")
        return observed
    bbox = gpd.read_file(SUPPORT_DIR / "mirror_place/features.geojson").total_bounds.tolist()
    window = derive_daymet_index_window(bbox, halo_cells=1)
    years = (2020, 2021, 2022, 2023, 2024)
    variables = ("tmax", "tmin", "prcp", "srad", "vp", "dayl")
    granules = []
    queries = []
    with requests.Session() as session:
        for year in years:
            frame, _, query = fetch_daymet_granule_metadata(
                session,
                endpoint=DAYMET_CMR_GRANULES_URL,
                collection_concept_id=DAYMET_CMR_COLLECTION_ID,
                year=year,
                variables=variables,
                bbox_wgs84=bbox,
            )
            queries.append(query)
            for row in frame.itertuples(index=False):
                granules.append(
                    {
                        "year": year,
                        "variable": str(row.variable),
                        "title": str(row.title),
                        "concept_id": str(row.concept_id),
                        "updated_at": None if row.updated_at is None else str(row.updated_at),
                        "full_granule_size_mb_metadata_only": float(row.size_mb),
                    }
                )
    if len(granules) != 30:
        raise RuntimeError("Expected 5 years x 6 Daymet granules")
    result = {
        "marker": MARKER,
        "city_id": CITY,
        "dates": list(DATES),
        "product": "Daymet Daily North America V4R1 / ORNL DAAC 2129",
        "granule_count": len(granules),
        "granules": granules,
        "subset_window": window,
        "query_records": queries,
        "values_downloaded": False,
        "official_geometry_equivalence_verified": False,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    atomic_json(result, marker)
    return result


def _safe_daymet_probe_location(final_url: str, requested_url: str) -> tuple[str, str]:
    """Show only a host and a non-sensitive path shape, never URL parameters."""

    try:
        final = urlsplit(final_url)
        requested = urlsplit(requested_url)
        host = final.hostname or "<unknown-host>"
        if host != "earthdata.nasa.gov" and not host.endswith(".earthdata.nasa.gov"):
            host = "<non-Earthdata-host>"
        if final.hostname == requested.hostname and final.path == requested.path:
            return host, "/collections/<frozen>/granules/<frozen>.dap.nc4"
        safe_segments = {"oauth", "authorize", "login", "collections", "granules", "data"}
        parts = [
            part if part.lower() in safe_segments else "<redacted>"
            for part in final.path.strip("/").split("/")[:3]
            if part
        ]
        return host, "/" + "/".join(parts)
    except ValueError:
        return "<invalid-host>", "/<redacted>"


def _daymet_probe_diagnostic(response, requested_url: str) -> tuple[dict, bool]:
    """Consume only one bounded subset response and report no body or credentials."""

    from la_heat.daymet_grid import netcdf_signature_kind

    headers = getattr(response, "headers", {}) or {}
    media_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if media_type not in {
        "text/html",
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
        "application/x-netcdf",
        "application/netcdf",
        "application/octet-stream",
        "application/x-hdf5",
        "application/vnd.opendap.dap4.data",
    }:
        media_type = "<unspecified-or-other>"
    encoding = str(headers.get("Content-Encoding", "")).strip().lower()
    if encoding not in {"", "identity", "gzip", "deflate", "br"}:
        encoding = "<other>"
    declared = str(headers.get("Content-Length", ""))
    declared_length = int(declared) if declared.isdecimal() else None
    final_host, final_path = _safe_daymet_probe_location(
        str(getattr(response, "url", requested_url)), requested_url
    )
    history = getattr(response, "history", ())

    def auth_scheme(request) -> str:
        if request is None:
            return "unknown"
        value = str(getattr(request, "headers", {}).get("Authorization", ""))
        if not value:
            return "none"
        scheme = value.split(" ", 1)[0].lower()
        return scheme if scheme in {"bearer", "basic"} else "other"

    final_request = getattr(response, "request", None)
    first_request = getattr(history[0], "request", None) if history else final_request
    prefix = bytearray()
    actual_bytes = 0
    complete = True
    stream_error = None
    maximum_probe_bytes = 16_000_000
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            actual_bytes += len(chunk)
            if len(prefix) < 8200:
                prefix.extend(chunk[: 8200 - len(prefix)])
            if actual_bytes > maximum_probe_bytes:
                complete = False
                break
    except requests.RequestException as error:
        complete = False
        stream_error = type(error).__name__

    beginning = bytes(prefix).lstrip().lower()
    kind = netcdf_signature_kind(bytes(prefix))
    looks_html = media_type == "text/html" or beginning.startswith(
        (
            b"<!doctype html",
            b"<html",
            b"<head",
            b"<body",
        )
    )
    if looks_html:
        login_markers = (b"login", b"sign in", b"earthdata", b"oauth", b"urs.")
        payload_kind = (
            "login_page"
            if any(marker in beginning for marker in login_markers)
            or final_host == "urs.earthdata.nasa.gov"
            else "html_response"
        )
    elif media_type.endswith("/json") or beginning.startswith((b"{", b"[")):
        payload_kind = "json_response_or_error"
    elif media_type.endswith("/xml") or beginning.startswith(b"<?xml"):
        payload_kind = "xml_response_or_error"
    elif beginning.startswith(b"\x1f\x8b"):
        payload_kind = "gzip_payload_not_decoded"
    elif kind is not None:
        payload_kind = kind
    elif not actual_bytes:
        payload_kind = "empty_response"
    else:
        payload_kind = "unrecognized_response_format"
    status = int(response.status_code)
    content_range_present = "Content-Range" in headers
    length_mismatch = (
        complete
        and declared_length is not None
        and encoding in {"", "identity"}
        and actual_bytes != declared_length
    )
    valid = (
        status == 200
        and complete
        and stream_error is None
        and not content_range_present
        and not length_mismatch
        and kind is not None
        and payload_kind == kind
    )
    diagnostic = {
        "http_status": status,
        "content_type": media_type,
        "content_encoding": encoding or "identity_or_unspecified",
        "declared_wire_length_bytes": declared_length,
        "actual_decoded_bytes_read": actual_bytes,
        "response_body_complete": complete,
        "redirected": bool(history),
        "redirect_count": len(history),
        "final_host": final_host,
        "final_path_redacted": final_path,
        "initial_auth_scheme": auth_scheme(first_request),
        "final_auth_scheme": auth_scheme(final_request),
        "content_range_present_without_range_request": content_range_present,
        "uncompressed_length_mismatch": length_mismatch,
        "stream_error_type": stream_error,
        "payload_kind": payload_kind,
        "netcdf_accepted": valid,
    }
    return diagnostic, valid


def probe_daymet_access(*, credential=None, local_daymet: bool = False) -> bool:
    """One credentialed frozen-subset probe; save no body or credential."""
    _preflight(local_daymet=local_daymet)
    from la_heat.daymet_grid import (
        DAYMET_CMR_COLLECTION_ID,
        DaymetGranule,
        build_daymet_direct_subset_url,
        load_earthdata_bearer_token,
    )

    inventory = json.loads((OUTPUT / "daymet_inventory.json").read_text(encoding="utf-8"))
    if inventory["dates"] != list(DATES) or inventory["granule_count"] != 30:
        raise RuntimeError("Frozen Daymet inventory changed")
    row = next(
        item
        for item in inventory["granules"]
        if item["year"] == 2020 and item["variable"] == "dayl"
    )
    title = str(row["title"])
    granule = DaymetGranule(
        concept_id=str(row["concept_id"]),
        title=title,
        variable="dayl",
        year=2020,
        size_mb=float(row["full_granule_size_mb_metadata_only"]),
        https_url="https://data.ornldaac.earthdata.nasa.gov/Daymet_Daily_V4R1/data/",
        opendap_url=(
            "https://opendap.earthdata.nasa.gov/collections/"
            f"{DAYMET_CMR_COLLECTION_ID}/granules/{title}"
        ),
        updated_at=row.get("updated_at"),
    )
    window = inventory["subset_window"]
    url = build_daymet_direct_subset_url(
        granule,
        y_indices=tuple(window["y_indices_inclusive"]),
        x_indices=tuple(window["x_indices_inclusive"]),
    )
    if credential is None:
        credential = load_earthdata_bearer_token()
    try:
        with requests.get(
            url,
            headers={"Authorization": f"Bearer {credential.value}"},
            stream=True,
            timeout=(15, 90),
        ) as response:
            diagnostic, valid = _daymet_probe_diagnostic(response, url)
    except requests.RequestException as error:
        print(
            "DAYMET_PROBE_DIAGNOSTIC "
            + json.dumps(
                {
                    "network_error_type": type(error).__name__,
                    "netcdf_accepted": False,
                },
                sort_keys=True,
            )
        )
        return False
    print("DAYMET_PROBE_DIAGNOSTIC " + json.dumps(diagnostic, sort_keys=True))
    if valid:
        print("DAYMET_ACCESS_NETCDF_OK")
    return valid


def run_daymet_local() -> dict:
    """Prompt once, probe, then resume only 30 frozen subsets and 660×21 features."""
    from la_heat.daymet_feature_stage import compile_daymet_feature_tables
    from la_heat.daymet_grid import (
        DAYMET_CMR_COLLECTION_ID,
        DaymetGranule,
        authenticated_netcdf_download,
        build_daymet_direct_subset_url,
        inspect_daymet_netcdf,
        prompt_earthdata_bearer_token,
        validate_daymet_direct_subset_spec,
    )
    from la_heat.multicity.source_footprints import derive_daymet_index_window

    _preflight(local_daymet=True)
    inventory = json.loads((OUTPUT / "daymet_inventory.json").read_text(encoding="utf-8"))
    rows = inventory["granules"]
    identities = {(int(row["year"]), str(row["variable"])) for row in rows}
    expected = {
        (year, var)
        for year in range(2020, 2025)
        for var in ("dayl", "prcp", "srad", "tmax", "tmin", "vp")
    }
    if inventory["dates"] != list(DATES) or len(rows) != 30 or identities != expected:
        raise RuntimeError("Frozen Daymet year-variable inventory changed")
    bbox = gpd.read_file(SUPPORT_DIR / "mirror_place/features.geojson").total_bounds.tolist()
    window = inventory["subset_window"]
    if derive_daymet_index_window(bbox, halo_cells=1) != window:
        raise RuntimeError("Frozen Daymet subset window changed")
    city_support = support(local_daymet=True)
    key_frame = keys(city_support)
    credential = prompt_earthdata_bearer_token()
    try:
        if not probe_daymet_access(credential=credential, local_daymet=True):
            raise RuntimeError("Daymet probe failed; no subset download started")
        records = []
        for row in sorted(rows, key=lambda item: (int(item["year"]), str(item["variable"]))):
            _preflight(local_daymet=True)
            year, variable = int(row["year"]), str(row["variable"])
            title = str(row["title"])
            granule = DaymetGranule(
                concept_id=str(row["concept_id"]),
                title=title,
                variable=variable,
                year=year,
                size_mb=float(row["full_granule_size_mb_metadata_only"]),
                https_url="https://data.ornldaac.earthdata.nasa.gov/Daymet_Daily_V4R1/data/",
                opendap_url=(
                    "https://opendap.earthdata.nasa.gov/collections/"
                    f"{DAYMET_CMR_COLLECTION_ID}/granules/{title}"
                ),
                updated_at=row.get("updated_at"),
            )
            url = build_daymet_direct_subset_url(
                granule,
                y_indices=tuple(window["y_indices_inclusive"]),
                x_indices=tuple(window["x_indices_inclusive"]),
            )
            destination = OUTPUT / "daymet_subsets" / str(year) / f"{variable}.nc"
            if not destination.is_file():
                authenticated_netcdf_download(
                    url,
                    destination,
                    credential=credential,
                    maximum_bytes=100_000_000,
                )
            spec = inspect_daymet_netcdf(
                destination,
                variable=variable,
                year=year,
                final_test_year=2025,
            )
            validate_daymet_direct_subset_spec(
                spec,
                y_indices=tuple(window["y_indices_inclusive"]),
                x_indices=tuple(window["x_indices_inclusive"]),
                bbox_wgs84=bbox,
            )
            records.append({"path": destination, "year": year, "variable": variable})
        _preflight(local_daymet=True)
        compiled = compile_daymet_feature_tables(
            pd.DataFrame(records),
            key_frame[["tract_geoid", "target_date"]],
            zone_raster=city_support.zones,
            eligible_land_mask=city_support.eligible_land,
            tract_geoids=city_support.tract_geoids,
            target_transform=city_support.grid.transform,
            target_crs=city_support.grid.crs,
            final_test_year=2025,
        )
        features = compiled.features.assign(city_id=CITY)
        features = features[["city_id", "tract_geoid", "target_date", *DAYMET_FEATURES]]
        if len(features) != 660 or features[list(DAYMET_FEATURES)].isna().any().any():
            raise RuntimeError("Frozen Daymet feature coverage is incomplete")
        output = OUTPUT / "components" / CITY / "daymet_features.parquet"
        atomic_parquet(features, output)
        result = {
            "marker": MARKER,
            "city_id": CITY,
            "dates": list(DATES),
            "subset_count": len(records),
            "key_count": len(features),
            "feature_count": len(DAYMET_FEATURES),
            "daymet_inventory_sha256": sha256_file(OUTPUT / "daymet_inventory.json"),
            "output_sha256": sha256_file(output),
            "official_geometry_equivalence_verified": False,
            "model_fit_or_score_performed": False,
        }
        atomic_json(result, OUTPUT / "daymet_build.json")
        return result
    finally:
        # The short-lived Python process exits immediately after this mode.
        # No token is passed to a child, environment variable, file, or log.
        del credential


def _sentinel_context(s: components.PortableCitySupport):
    from la_heat.multicity import portable_sentinel_build as build

    directory = OUTPUT / "sentinel_inventory"
    inventory = json.loads((directory / "INVENTORY.json").read_text(encoding="utf-8"))
    if inventory.get("dates") != list(DATES):
        raise RuntimeError("Frozen Sentinel dates changed")
    for name, digest in inventory["files_sha256"].items():
        if sha256_file(directory / name) != digest:
            raise RuntimeError("Frozen Sentinel inventory file changed")
    acquisitions = pd.read_csv(
        directory / "selected_acquisitions.csv", dtype={"processing_baseline": "string"}
    )
    items = pd.read_csv(directory / "selected_items.csv", dtype={"processing_baseline": "string"})
    membership = pd.read_csv(directory / "target_window_membership.csv")
    frozen = build.FrozenSentinelInputs(
        acquisitions=acquisitions,
        items=items,
        membership=membership,
        summary={"local_timezone": "America/Denver"},
        locks={"inventory_files_sha256": canonical_sha256(inventory["files_sha256"])},
    )
    stage = build._stage_for_city(ROOT, CITY, "America/Denver")
    spatial = build._fixed_spatial_support(s, target_dates=DATES)
    runtime = OUTPUT / "sentinel_runtime"
    compiled = OUTPUT / "sentinel_compiled"
    metadata = OUTPUT / "sentinel_product_metadata"
    for path in (runtime, compiled, metadata):
        path.mkdir(parents=True, exist_ok=True)
    context = build.CityBuildContext(
        city_id=CITY,
        inventory=frozen,
        support=s,
        spatial=spatial,
        stage=stage,
        base_lock={
            "trial_contract_sha256": canonical_sha256([CITY, DATES, SUPPORT_SHA]),
            "stage_sha256": stage.sha256,
            **frozen.locks,
            **spatial.locks,
        },
        runtime_directory=runtime,
        output_directory=compiled,
        metadata_directory=metadata,
    )
    return build, context


def run_sentinel_values(*, maximum_new_acquisitions: int | None = None) -> dict:
    """Resume the 144-acquisition optical build on the existing trial support."""
    scope = json.loads(ACTIVE.read_text(encoding="utf-8"))[
        "source_city_exploratory_predictor_trial"
    ]
    if scope.get("no_sentinel_batch_execution"):
        raise RuntimeError("Only the two frozen Sentinel cost samples are authorized")
    s = support()
    build, context = _sentinel_context(s)
    completed = 0
    new = 0
    total = len(context.inventory.acquisitions)
    for row in context.inventory.acquisitions.itertuples(index=False):
        _preflight()
        if build.acquisition_cache_is_current(ROOT, context, row):
            completed += 1
            continue
        if maximum_new_acquisitions is not None and new >= maximum_new_acquisitions:
            break
        build._process_one(ROOT, context, row, download_threads=2, force=False)
        if not build.acquisition_cache_is_current(ROOT, context, row):
            raise RuntimeError("Sentinel acquisition did not create an authenticated cache")
        completed += 1
        new += 1
        atomic_json(
            {
                "marker": MARKER,
                "complete": completed,
                "total": total,
                "last_physical_acquisition_id": str(row.physical_acquisition_id),
                "thermal_target_or_qa_read": False,
            },
            OUTPUT / "sentinel_runtime/status.json",
        )
        if new == 1 or new % 10 == 0:
            print(f"COLORADO_SENTINEL {completed}/{total}", flush=True)
    if completed == total:
        result = build.compile_city(ROOT, context)
        return {"complete": completed, "total": total, "compiled": result}
    return {"complete": completed, "total": total, "compiled": None}


def _isolated_sentinel_dates(preflight: dict, membership: pd.DataFrame) -> tuple[str, ...]:
    """Use frozen product evidence to isolate dates, never omit an item within a date."""
    if preflight.get("unique_product_count") != 562 or set(preflight["by_target_date"]) != set(
        DATES
    ):
        raise RuntimeError("Frozen Sentinel product preflight coverage changed")
    blocked = [
        row
        for row in preflight["products"]
        if row["category"]
        not in {
            "pre04_no_new_offset_required",
            "conversion_complete",
        }
    ]
    if len(blocked) != 1 or blocked[0]["category"] != "required_offset_missing":
        raise RuntimeError("Sentinel calibration blocker set changed")
    if (
        blocked[0]["target_dates"] != ["2021-10-18"]
        or len(blocked[0]["physical_acquisitions"]) != 1
    ):
        raise RuntimeError("Sentinel blocker no longer belongs to one frozen date")
    if set(
        membership.loc[
            membership.physical_acquisition_id == blocked[0]["physical_acquisitions"][0],
            "target_date",
        ]
    ) != {"2021-10-18"}:
        raise RuntimeError("Frozen acquisition membership disagrees with product preflight")
    safe = tuple(day for day in DATES if day != "2021-10-18")
    if any(
        preflight["by_target_date"][day]["required_offset_missing"]
        or preflight["by_target_date"][day]["read_or_identity_failure"]
        for day in safe
    ):
        raise RuntimeError("A supposedly isolated date has unverified calibration")
    if len(membership) != 144 or membership.physical_acquisition_id.nunique() != 144:
        raise RuntimeError("Frozen physical acquisition membership changed")
    if any(len(membership.loc[membership.target_date == day]) != 24 for day in DATES):
        raise RuntimeError("Frozen 24-acquisition-per-date membership changed")
    return safe


def _process_with_bounded_remote_retry(process, physical_id: str) -> None:
    """Retry one incomplete remote COG read; never replace a failed observation."""
    from rasterio.errors import RasterioIOError, WarpOperationError

    remote_errors = (RasterioIOError, WarpOperationError)
    try:
        process(2)
    except remote_errors:
        print(f"COLORADO_SENTINEL_REMOTE_READ_RETRY {physical_id}", flush=True)
        clock.sleep(2)
        try:
            process(1)
        except remote_errors as error:
            raise RuntimeError(
                f"Remote COG read failed twice for {physical_id}: {type(error).__name__}"
            ) from None


def run_sentinel_isolated_five_dates() -> dict:
    """Resume only five calibrated dates with original full-context cache locks."""
    _preflight()
    scope = json.loads(ACTIVE.read_text(encoding="utf-8"))[
        "source_city_exploratory_predictor_trial"
    ]
    if (
        scope.get("no_sentinel_batch_execution")
        or scope.get("state") != "sentinel_five_date_isolated_execution_only"
    ):
        raise RuntimeError("Five-date isolated Sentinel execution is not approved")
    preflight_path = OUTPUT / "sentinel_metadata_preflight.json"
    if sha256_file(preflight_path) != scope["sentinel_metadata_preflight_sha256"]:
        raise RuntimeError("Frozen Sentinel preflight SHA-256 changed")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    s = support()
    build, context = _sentinel_context(s)
    membership = context.inventory.membership
    safe_dates = _isolated_sentinel_dates(preflight, membership)
    safe_ids = set(
        membership.loc[
            membership.target_date.astype(str).isin(safe_dates), "physical_acquisition_id"
        ].astype(str)
    )
    rows = [
        row
        for row in context.inventory.acquisitions.itertuples(index=False)
        if str(row.physical_acquisition_id) in safe_ids
    ]
    if len(rows) != 120:
        raise RuntimeError("Five-date acquisition count changed")
    started = clock.monotonic()
    completed = sum(build.acquisition_cache_is_current(ROOT, context, row) for row in rows)
    failures = 0

    def publish(state: str, last_id: str | None = None, error_type: str | None = None) -> None:
        atomic_json(
            {
                "marker": MARKER,
                "state": state,
                "target_dates": list(safe_dates),
                "blocked_target_date": "2021-10-18",
                "complete": completed,
                "total": len(rows),
                "failed": failures,
                "elapsed_seconds": round(clock.monotonic() - started, 1),
                "last_physical_acquisition_id": last_id,
                "last_error_type": error_type,
                "thermal_target_or_qa_read": False,
            },
            OUTPUT / "sentinel_runtime/five_date_status.json",
        )

    publish("running")
    pending = [row for row in rows if not build.acquisition_cache_is_current(ROOT, context, row)]

    def process(row):
        _preflight()
        physical_id = str(row.physical_acquisition_id)
        if not build.acquisition_cache_is_current(ROOT, context, row):
            _process_with_bounded_remote_retry(
                lambda threads: build._process_one(
                    ROOT, context, row, download_threads=threads, force=False
                ),
                physical_id,
            )
        if not build.acquisition_cache_is_current(ROOT, context, row):
            raise RuntimeError("Acquisition did not create an authenticated cache")
        return physical_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(process, row): str(row.physical_acquisition_id) for row in pending}
        for future in as_completed(futures):
            physical_id = futures[future]
            try:
                future.result()
            except Exception as error:
                failures += 1
                publish("failed", physical_id, type(error).__name__)
                for other in futures:
                    other.cancel()
                raise
            completed += 1
            publish("running", physical_id)
            print(
                f"COLORADO_SENTINEL_FIVE_DATE {completed}/{len(rows)} failed={failures} "
                f"elapsed_s={round(clock.monotonic() - started)}",
                flush=True,
            )
    if completed != len(rows):
        raise RuntimeError("Five-date cache count did not reach 120")

    from la_heat.sentinel_feature_builder import _acquisition_cache_directory
    from la_heat.sentinel_features import INDEX_COLUMNS, build_previous_60_day_composites

    frames = [
        pd.read_parquet(
            _acquisition_cache_directory(
                context.runtime_directory, str(row.physical_acquisition_id)
            )
            / "acquisition_tract.parquet"
        )
        for row in rows
    ]
    composites = build_previous_60_day_composites(
        pd.concat(frames, ignore_index=True),
        membership.loc[membership.target_date.astype(str).isin(safe_dates)].copy(),
        target_dates=safe_dates,
        tract_geoids=s.tract_geoids,
        minimum_acquisition_coverage=context.stage.minimum_coverage,
        minimum_acquisitions=context.stage.minimum_acquisitions,
        final_test_year=2025,
        unlock_final_test=False,
    )
    output = OUTPUT / "sentinel_compiled_five_dates"
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, frame in (
        ("sentinel_features.parquet", composites.features),
        ("sentinel_feature_audit.parquet", composites.audit),
        ("sentinel_lineage.parquet", composites.lineage),
    ):
        value = frame.copy()
        if "city_id" in value:
            if set(value["city_id"].astype(str)) != {CITY}:
                raise RuntimeError("Five-date Sentinel lineage has another city")
        else:
            value.insert(0, "city_id", CITY)
        path = output / name
        atomic_parquet(value, path)
        records[name] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "rows": len(value),
        }
    features = composites.features
    if len(features) != 550 or features.duplicated(["tract_geoid", "target_date"]).any():
        raise RuntimeError("Five-date composite lacks full 110-tract prediction keys")
    missing = features[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not missing.isin([0, len(INDEX_COLUMNS)]).all():
        raise RuntimeError("Five-date composite has partial Sentinel feature missingness")
    result = {
        "marker": MARKER,
        "state": "complete_exploratory_five_dates_only",
        "target_dates": list(safe_dates),
        "blocked_target_date": "2021-10-18",
        "frozen_preflight_sha256": sha256_file(preflight_path),
        "frozen_inventory_sha256": sha256_file(OUTPUT / "sentinel_inventory/INVENTORY.json"),
        "physical_acquisitions": len(rows),
        "feature_rows": len(features),
        "available_feature_rows": int((missing == 0).sum()),
        "missing_feature_rows": int((missing == len(INDEX_COLUMNS)).sum()),
        "outputs": records,
        "official_geometry_equivalence_verified": False,
        "model_or_target_values_read": False,
    }
    atomic_json(result, output / "FIVE_DATE_COMPLETE.json")
    publish("complete")
    return result


def _classify_sentinel_product_xml(snapshot: dict, xml_content: bytes) -> dict:
    """Validate exact product identity before accepting any optical calibration."""
    from la_heat.sentinel_features import parse_boa_calibration

    props = snapshot["properties"]
    product_uri = str(props["s2:product_uri"])
    baseline = str(props["s2:processing_baseline"])
    root = ET.fromstring(xml_content)

    def only(name: str) -> str:
        values = {
            (element.text or "").strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == name
        }
        if len(values) != 1 or not next(iter(values)):
            raise ValueError(f"Product XML lacks a unique {name}")
        return next(iter(values))

    xml_uri = only("PRODUCT_URI")
    xml_baseline = only("PROCESSING_BASELINE")
    xml_platform = only("SPACECRAFT_NAME")
    xml_start = only("PRODUCT_START_TIME")
    xml_generation = only("GENERATION_TIME")
    if (
        xml_uri != product_uri
        or xml_baseline != baseline
        or xml_platform.casefold() != str(props["platform"]).casefold()
        or xml_start[:19] != str(props["datetime"])[:19]
        or xml_generation[:19] != str(props["s2:generation_time"])[:19]
        or str(props["s2:mgrs_tile"]) not in xml_uri
    ):
        raise ValueError("Product XML identity disagrees with the frozen STAC item")
    try:
        calibration = parse_boa_calibration(xml_content, processing_baseline=baseline)
    except ValueError as error:
        if "requires BOA offsets for every band" in str(error):
            return {"category": "required_offset_missing", "reason": str(error)}
        raise
    category = (
        "pre04_no_new_offset_required"
        if tuple(int(part) for part in baseline.split(".")) < (4, 0)
        else "conversion_complete"
    )
    return {
        "category": category,
        "calibration_sha256": calibration.sha256,
        "quantification_value": calibration.quantification_value,
        "offsets": dict(calibration.offsets),
    }


def run_sentinel_metadata_preflight() -> dict:
    """Audit all frozen products' XML only; never open a scientific raster."""
    from la_heat.sentinel_feature_builder import _read_product_metadata

    _preflight()
    scope = json.loads(ACTIVE.read_text(encoding="utf-8"))[
        "source_city_exploratory_predictor_trial"
    ]
    if not scope.get("sentinel_metadata_preflight_approved") or not scope.get(
        "no_sentinel_batch_execution"
    ):
        raise RuntimeError("Metadata-only preflight scope is not active")
    directory = OUTPUT / "sentinel_inventory"
    selected = pd.read_csv(directory / "selected_items.csv", dtype=str)
    members = pd.read_csv(directory / "target_window_membership.csv", dtype=str)
    if len(selected) != 562 or set(members.target_date) != set(DATES):
        raise RuntimeError("Frozen Sentinel item/membership inventory drifted")
    dates_by_acquisition = (
        members.groupby("physical_acquisition_id")["target_date"]
        .agg(lambda values: sorted(set(values)))
        .to_dict()
    )
    products: dict[str, dict] = {}
    for row in selected.itertuples(index=False):
        snapshot_path = directory / "stac" / str(row.snapshot_filename)
        if sha256_file(snapshot_path) != str(row.snapshot_sha256):
            raise RuntimeError("Frozen STAC snapshot bytes drifted")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        product_uri = str(snapshot["properties"]["s2:product_uri"])
        href = str(snapshot["assets"]["product-metadata"]["href"])
        if href != str(row.asset_product_metadata_href):
            raise RuntimeError("Frozen product metadata asset changed")
        entry = products.setdefault(
            product_uri,
            {
                "item_id": str(row.item_id),
                "snapshot": snapshot,
                "metadata_href": href,
                "acquisitions": set(),
                "dates": set(),
            },
        )
        if entry["metadata_href"] != href or entry["item_id"] != str(row.item_id):
            raise RuntimeError("Product URI has conflicting frozen items")
        acquisition = str(row.physical_acquisition_id)
        entry["acquisitions"].add(acquisition)
        entry["dates"].update(dates_by_acquisition[acquisition])

    def inspect(product_uri: str, entry: dict) -> dict:
        _preflight()
        try:
            with requests.Session() as session:
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry

                session.mount(
                    "https://",
                    HTTPAdapter(
                        max_retries=Retry(
                            total=2,
                            connect=2,
                            read=2,
                            status=2,
                            backoff_factor=0.5,
                            status_forcelist=(429, 500, 502, 503, 504),
                        )
                    ),
                )
                content, digest, path = _read_product_metadata(
                    item_id=entry["item_id"],
                    unsigned_url=entry["metadata_href"],
                    raw_metadata_directory=OUTPUT / "sentinel_product_metadata",
                    session=session,
                )
            result = _classify_sentinel_product_xml(entry["snapshot"], content)
            result.update({"xml_sha256": digest, "xml_path": str(path.relative_to(ROOT))})
        except (requests.RequestException, OSError, ValueError, ET.ParseError) as error:
            result = {"category": "read_or_identity_failure", "reason": type(error).__name__}
        return {
            "product_uri": product_uri,
            "item_id": entry["item_id"],
            "processing_baseline": entry["snapshot"]["properties"]["s2:processing_baseline"],
            "physical_acquisitions": sorted(entry["acquisitions"]),
            "target_dates": sorted(entry["dates"]),
            **result,
        }

    inspected = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(inspect, uri, entry): uri for uri, entry in products.items()}
        for future in as_completed(futures):
            inspected.append(future.result())
            if len(inspected) % 50 == 0:
                print(f"COLORADO_SENTINEL_METADATA {len(inspected)}/{len(products)}", flush=True)
    inspected.sort(key=lambda value: value["product_uri"])
    counts = dict(Counter(row["category"] for row in inspected))
    affected = {
        target_date: {
            category: sum(
                target_date in row["target_dates"] and row["category"] == category
                for row in inspected
            )
            for category in (
                "conversion_complete",
                "pre04_no_new_offset_required",
                "required_offset_missing",
                "read_or_identity_failure",
            )
        }
        for target_date in DATES
    }
    summary = {
        "marker": MARKER,
        "city_id": CITY,
        "dates": list(DATES),
        "frozen_item_count": len(selected),
        "unique_product_count": len(products),
        "counts": counts,
        "by_target_date": affected,
        "all_products_calibrated": not counts.get("required_offset_missing", 0)
        and not counts.get("read_or_identity_failure", 0),
        "scientific_rasters_read": False,
        "products": inspected,
    }
    atomic_json(summary, OUTPUT / "sentinel_metadata_preflight.json")
    return summary


def _gdal_network_statistics_library():
    """Use GDAL's own VSI counters without retaining signed file URLs."""
    import rasterio

    library_dir = Path(rasterio.__file__).parent.parent / "rasterio.libs"
    candidates = list(library_dir.glob("gdal-*.dll"))
    library_name = str(candidates[0]) if len(candidates) == 1 else ctypes.util.find_library("gdal")
    if not library_name:
        raise RuntimeError("GDAL network statistics library unavailable")
    library = ctypes.CDLL(library_name)
    library.VSINetworkStatsReset.restype = None
    library.VSINetworkStatsGetAsSerializedJSON.argtypes = [ctypes.c_void_p]
    library.VSINetworkStatsGetAsSerializedJSON.restype = ctypes.c_void_p
    library.VSIFree.argtypes = [ctypes.c_void_p]
    return library


def _gdal_network_method_totals(library) -> dict:
    pointer = library.VSINetworkStatsGetAsSerializedJSON(None)
    if not pointer:
        raise RuntimeError("GDAL network statistics unavailable")
    try:
        raw = json.loads(ctypes.string_at(pointer).decode("utf-8"))
    finally:
        library.VSIFree(pointer)
    # The full GDAL object includes signed URLs under files: never persist it.
    return {
        method: {
            key: int(values.get(key, 0))
            for key in ("count", "downloaded_bytes", "uploaded_bytes")
            if key in values
        }
        for method, values in raw.get("methods", {}).items()
    }


def _cache_bytes(*directories: Path) -> int:
    return sum(
        path.stat().st_size
        for directory in directories
        for path in directory.rglob("*")
        if path.is_file()
    )


def run_sentinel_cost_samples() -> dict:
    """Measure exactly two preselected physical acquisitions, then stop."""
    from la_heat.multicity import portable_sentinel_build as build

    s = support()
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_exploratory_predictor_trial"]
    sample_ids = tuple(scope.get("sentinel_cost_sample_physical_acquisition_ids", ()))
    if (
        not scope.get("no_sentinel_batch_execution")
        or len(sample_ids) != 2
        or len(set(sample_ids)) != 2
    ):
        raise RuntimeError("Two frozen Sentinel cost samples required")
    _, context = _sentinel_context(s)
    acquisition_rows = {
        str(row.physical_acquisition_id): row
        for row in context.inventory.acquisitions.itertuples(index=False)
    }
    if any(physical_id not in acquisition_rows for physical_id in sample_ids):
        raise RuntimeError("Cost sample is outside frozen Sentinel inventory")
    if [int(acquisition_rows[physical_id].item_count) for physical_id in sample_ids] != [1, 4]:
        raise RuntimeError("Frozen one-item/four-item cost sample changed")
    results_path = OUTPUT / "sentinel_cost_samples.json"
    results = (
        json.loads(results_path.read_text(encoding="utf-8"))["samples"]
        if results_path.is_file()
        else []
    )
    if [row["physical_acquisition_id"] for row in results] != list(sample_ids[: len(results)]):
        raise RuntimeError("Existing cost sample record changed")
    original_request = requests.sessions.Session.request
    for physical_id in sample_ids:
        _preflight()
        row = acquisition_rows[physical_id]
        if any(record["physical_acquisition_id"] == physical_id for record in results):
            recorded = next(
                record for record in results if record["physical_acquisition_id"] == physical_id
            )
            if recorded["authenticated_cache"] and not build.acquisition_cache_is_current(
                ROOT, context, row
            ):
                raise RuntimeError("Recorded cost sample cache is no longer authenticated")
            continue
        if build.acquisition_cache_is_current(ROOT, context, row):
            raise RuntimeError(
                "Cost sample already cached; refuse to misreport cache hit as network cost"
            )
        request_totals = {"count": 0, "downloaded_bytes": 0, "status_counts": {}}

        def counted_request(session, method, url, *args, totals=request_totals, **kwargs):
            response = original_request(session, method, url, *args, **kwargs)
            totals["count"] += 1
            totals["downloaded_bytes"] += len(response.content)
            status = str(response.status_code)
            totals["status_counts"][status] = totals["status_counts"].get(status, 0) + 1
            return response

        before = _cache_bytes(context.runtime_directory, context.metadata_directory)
        old_setting = os.environ.get("CPL_VSIL_NETWORK_STATS_ENABLED")
        os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = "YES"
        library = _gdal_network_statistics_library()
        library.VSINetworkStatsReset()
        started = clock.monotonic()
        failure = None
        try:
            with patch.object(requests.sessions.Session, "request", counted_request):
                build._process_one(ROOT, context, row, download_threads=2, force=False)
        except ValueError as error:
            if "requires BOA offsets for every band" not in str(error):
                raise
            failure = "baseline_04_metadata_missing_boa_offsets"
        finally:
            methods = _gdal_network_method_totals(library)
            if old_setting is None:
                os.environ.pop("CPL_VSIL_NETWORK_STATS_ENABLED", None)
            else:
                os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = old_setting
        authenticated = build.acquisition_cache_is_current(ROOT, context, row)
        if not authenticated and failure is None:
            raise RuntimeError("Sentinel cost sample did not create an authenticated cache")
        result = {
            "physical_acquisition_id": physical_id,
            "item_count": int(row.item_count),
            "union_city_coverage_fraction": float(row.union_city_coverage_fraction),
            "elapsed_seconds": round(clock.monotonic() - started, 3),
            "gdal_http_methods": methods,
            "python_http": request_totals,
            "persistent_cache_bytes_added": (
                _cache_bytes(context.runtime_directory, context.metadata_directory) - before
            ),
            "authenticated_cache": authenticated,
            "technical_failure": failure,
            "configured_gdal_http_retry_count": int(os.environ.get("GDAL_HTTP_MAX_RETRY", "0")),
        }
        results.append(result)
        atomic_json({"marker": MARKER, "samples": results}, results_path)
        print(f"COLORADO_SENTINEL_COST_SAMPLE {len(results)}/2", flush=True)
    completed_count = sum(
        build.acquisition_cache_is_current(ROOT, context, row) for row in acquisition_rows.values()
    )
    atomic_json(
        {
            "marker": MARKER,
            "complete": completed_count,
            "total": len(acquisition_rows),
            "thermal_target_or_qa_read": False,
        },
        OUTPUT / "sentinel_runtime/status.json",
    )
    remaining = [
        row
        for row in acquisition_rows.values()
        if not build.acquisition_cache_is_current(ROOT, context, row)
    ]
    remaining_frame = pd.DataFrame(
        [
            {
                "physical_acquisition_id": str(row.physical_acquisition_id),
                "acquisition_local_date": str(row.acquisition_local_date),
                "item_count": int(row.item_count),
                "processing_baseline": str(row.processing_baseline),
                "status": "pending_calibration_review"
                if str(row.physical_acquisition_id) == sample_ids[0]
                else "pending_not_run",
            }
            for row in remaining
        ]
    )
    atomic_csv(remaining_frame, OUTPUT / "sentinel_remaining_acquisitions.csv")
    item_frame = context.inventory.items
    asset_columns = [
        name
        for name in item_frame.columns
        if name.startswith("asset_")
        and name.endswith("_href")
        and name != "asset_product_metadata_href"
    ]
    asset_urls = item_frame[asset_columns].to_numpy().ravel().tolist()
    membership = context.inventory.membership
    successful = [record for record in results if record["authenticated_cache"]]
    audit = {
        "marker": MARKER,
        "samples": results,
        "complete_acquisitions": completed_count,
        "total_acquisitions": len(acquisition_rows),
        "remaining_acquisitions": len(remaining),
        "remaining_item_tiles": sum(int(row.item_count) for row in remaining),
        "remaining_by_baseline": {
            baseline: int(count)
            for baseline, count in remaining_frame.groupby("processing_baseline").size().items()
        },
        "unique_selected_item_ids": int(item_frame.item_id.nunique()),
        "selected_item_rows": len(item_frame),
        "unique_selected_asset_urls": len(set(asset_urls)),
        "selected_asset_rows": len(asset_urls),
        "target_membership_rows": len(membership),
        "unique_membership_acquisitions": int(membership.physical_acquisition_id.nunique()),
        "successful_cost_sample_count": len(successful),
        "calibration_blocked_sample_count": len(results) - len(successful),
        "all_remaining_network_cost_estimate_conditional": (
            {
                "method": "one successful four-item sample, per-item linear scenario; "
                "not a confidence interval or authorization to run",
                "gdal_downloaded_bytes_point": round(
                    successful[0]["gdal_http_methods"]["GET"]["downloaded_bytes"]
                    / successful[0]["item_count"]
                    * sum(int(row.item_count) for row in remaining)
                ),
                "elapsed_seconds_point": round(
                    successful[0]["elapsed_seconds"]
                    / successful[0]["item_count"]
                    * sum(int(row.item_count) for row in remaining)
                ),
                "sensitivity_multipliers_not_empirical_interval": [0.5, 2.0],
                "invalid_until_missing_boa_offsets_resolved": True,
            }
            if len(successful) == 1 and successful[0]["item_count"] == 4
            else None
        ),
        "remaining_list": "sentinel_remaining_acquisitions.csv",
        "scientific_status": "exploratory_mirror_only_no_complete_six_date_sentinel_features",
    }
    atomic_json(audit, OUTPUT / "sentinel_cost_audit.json")
    return audit


def existing_label_keys(label_dir: Path = LABEL_DIR) -> pd.DataFrame:
    """Read only keys/availability; never deserialize saved temperature values."""
    rows = []
    for target_date in DATES:
        path = label_dir / f"{target_date}_development_labels.csv"
        frame = pd.read_csv(
            path,
            usecols=["tract_geoid", "target_available_exploratory"],
            dtype={"tract_geoid": str},
        )
        if len(frame) != 110 or frame.tract_geoid.duplicated().any():
            raise RuntimeError("Existing exploratory label keys changed")
        frame["target_date"] = pd.Timestamp(target_date)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True)


def run_audit() -> dict:
    """Publish full keys and honest missingness, with no model or target values."""
    s = support()
    key_frame = keys(s)
    if (
        len(key_frame) != 660
        or key_frame.duplicated(["city_id", "tract_geoid", "target_date"]).any()
    ):
        raise RuntimeError("Full Colorado predictor key universe changed")
    static_path = OUTPUT / "components" / CITY / "static_features.parquet"
    static = pd.read_parquet(static_path, columns=["tract_geoid", *STATIC_FEATURES])
    static["tract_geoid"] = static.tract_geoid.astype(str)
    if len(static) != 110 or static.tract_geoid.duplicated().any():
        raise RuntimeError("Static component is not the complete tract universe")
    calendar = build_calendar_features(
        key_frame[["tract_geoid", "target_date"]].copy(),
        final_test_year=2025,
        unlock_final_test=False,
    )
    table = key_frame.merge(static, on="tract_geoid", how="left", validate="many_to_one")
    table = table.merge(
        calendar, on=["tract_geoid", "target_date"], how="left", validate="one_to_one"
    )
    sentinel_path = OUTPUT / "sentinel_compiled_five_dates/sentinel_features.parquet"
    sentinel_completion = OUTPUT / "sentinel_compiled_five_dates/FIVE_DATE_COMPLETE.json"
    if sentinel_path.is_file():
        if not sentinel_completion.is_file():
            raise RuntimeError("Five-date Sentinel table lacks completion record")
        completion = json.loads(sentinel_completion.read_text(encoding="utf-8"))
        record = completion["outputs"]["sentinel_features.parquet"]
        if completion["state"] != "complete_exploratory_five_dates_only" or (
            sha256_file(sentinel_path) != record["sha256"]
            or sentinel_path.stat().st_size != record["bytes"]
        ):
            raise RuntimeError("Five-date Sentinel completion does not match table")
    if sentinel_path.is_file():
        sentinel = pd.read_parquet(
            sentinel_path, columns=["city_id", "tract_geoid", "target_date", *SENTINEL_FEATURES]
        )
        sentinel["tract_geoid"] = sentinel.tract_geoid.astype(str)
        sentinel["target_date"] = pd.to_datetime(sentinel.target_date)
        if set(sentinel.target_date.dt.strftime("%Y-%m-%d")) != set(completion["target_dates"]):
            raise RuntimeError("Five-date Sentinel output dates disagree with completion")
        table = table.merge(
            sentinel,
            on=["city_id", "tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
    daymet_path = OUTPUT / "components" / CITY / "daymet_features.parquet"
    daymet_completion_path = OUTPUT / "daymet_build.json"
    daymet_completion = (
        json.loads(daymet_completion_path.read_text(encoding="utf-8"))
        if daymet_completion_path.is_file()
        else None
    )
    if daymet_path.is_file():
        if daymet_completion is None or daymet_completion["output_sha256"] != sha256_file(
            daymet_path
        ):
            raise RuntimeError("Daymet table lacks its matching frozen completion record")
        daymet = pd.read_parquet(
            daymet_path, columns=["city_id", "tract_geoid", "target_date", *DAYMET_FEATURES]
        )
        daymet["tract_geoid"] = daymet.tract_geoid.astype(str)
        daymet["target_date"] = pd.to_datetime(daymet.target_date)
        table = table.merge(
            daymet,
            on=["city_id", "tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
    for name in (*DAYMET_FEATURES, *SENTINEL_FEATURES):
        if name not in table:
            table[name] = np.nan
    table = (
        table[["city_id", "tract_geoid", "target_date", *FEATURE_NAMES]]
        .sort_values(["target_date", "tract_geoid"], kind="stable")
        .reset_index(drop=True)
    )
    label_keys = existing_label_keys()
    label_flags = label_keys.rename(
        columns={"target_available_exploratory": "existing_exploratory_label_available"}
    )
    joined = table[["city_id", "tract_geoid", "target_date"]].merge(
        label_flags,
        on=["tract_geoid", "target_date"],
        how="left",
        validate="one_to_one",
    )
    if joined.existing_exploratory_label_available.isna().any():
        raise RuntimeError("Some full predictor keys lack an existing label-key record")
    label_count = int(joined.existing_exploratory_label_available.sum())
    if label_count != 587:
        raise RuntimeError("Existing exploratory label count changed")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    partial_path = OUTPUT / "partial_46_schema_predictor_trial.parquet"
    atomic_parquet(table, partial_path)
    join_path = OUTPUT / "label_key_join_only.parquet"
    atomic_parquet(joined, join_path)
    missing_by_date = {
        str(date.date()): {name: int(group[name].isna().sum()) for name in FEATURE_NAMES}
        for date, group in table.groupby("target_date", sort=True)
    }
    inventory = json.loads(
        (OUTPUT / "sentinel_inventory/INVENTORY.json").read_text(encoding="utf-8")
    )
    daymet_inventory = json.loads((OUTPUT / "daymet_inventory.json").read_text(encoding="utf-8"))
    acquisitions = pd.read_csv(OUTPUT / "sentinel_inventory/selected_acquisitions.csv")
    memberships = pd.read_csv(OUTPUT / "sentinel_inventory/target_window_membership.csv")
    acquisitions["generation_time"] = pd.to_datetime(
        acquisitions["generation_time"], utc=True, format="mixed"
    )
    membership_times = memberships.merge(
        acquisitions[["physical_acquisition_id", "generation_time"]],
        on="physical_acquisition_id",
        validate="many_to_one",
    )
    membership_times["target_date"] = pd.to_datetime(membership_times["target_date"], utc=True)
    if not membership_times["lag_days"].between(1, 60).all():
        raise RuntimeError("Sentinel membership violates d-60:d-1")
    generated_after_date = (
        membership_times["generation_time"].dt.date > membership_times["target_date"].dt.date
    )
    sentinel_build, sentinel_context = _sentinel_context(s)
    authenticated_acquisitions = sum(
        sentinel_build.acquisition_cache_is_current(ROOT, sentinel_context, row)
        for row in sentinel_context.inventory.acquisitions.itertuples(index=False)
    )
    preflight_path = OUTPUT / "sentinel_metadata_preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    with (ROOT / "configs/multicity/m3_development_protocol_v1.toml").open("rb") as stream:
        protocol = tomllib.load(stream)
    static_record = json.loads((OUTPUT / "static_trial.json").read_text(encoding="utf-8"))
    input_records = {
        "support": {
            "path": str((SUPPORT_DIR / "fixed_support.npz").relative_to(ROOT)),
            "sha256": SUPPORT_SHA,
        },
        "static_trial": {
            "path": str((OUTPUT / "static_trial.json").relative_to(ROOT)),
            "sha256": sha256_file(OUTPUT / "static_trial.json"),
        },
        "sentinel_inventory": {
            "path": str((OUTPUT / "sentinel_inventory/INVENTORY.json").relative_to(ROOT)),
            "sha256": sha256_file(OUTPUT / "sentinel_inventory/INVENTORY.json"),
        },
        "sentinel_metadata_preflight": {
            "path": str(preflight_path.relative_to(ROOT)),
            "sha256": sha256_file(preflight_path),
        },
    }
    if daymet_completion is not None:
        input_records["daymet_completion"] = {
            "path": str(daymet_completion_path.relative_to(ROOT)),
            "sha256": sha256_file(daymet_completion_path),
        }
    if sentinel_completion.is_file():
        input_records["sentinel_five_date_completion"] = {
            "path": str(sentinel_completion.relative_to(ROOT)),
            "sha256": sha256_file(sentinel_completion),
        }
    missing_rows = {name: int(table[name].isna().sum()) for name in FEATURE_NAMES}
    input_complete = all(count == 0 for count in missing_rows.values())
    date_status = {
        day: (
            "calibration_blocked_not_computed"
            if day == "2021-10-18"
            else "calculated_with_observation_missingness"
            if any(missing_by_date[day][name] for name in SENTINEL_FEATURES)
            else "calculated_complete"
        )
        if sentinel_completion.is_file()
        else "not_computed"
        for day in DATES
    }
    summary = {
        "marker": MARKER,
        "state": (
            "complete_exploratory_predictor_input_official_boundary_pending"
            if input_complete
            else "partial_trial_one_date_sentinel_calibration_blocked"
        ),
        "city_id": CITY,
        "dates": list(DATES),
        "tract_count": 110,
        "full_predictor_rows": len(table),
        "feature_count": len(FEATURE_NAMES),
        "feature_order": list(FEATURE_NAMES),
        "b1_feature_names": list(protocol["features"]["old_23_dynamic"]["names"]),
        "m3_feature_names": list(FEATURE_NAMES),
        "static_feature_count_available": len(STATIC_FEATURES),
        "calendar_feature_count_available": len(CALENDAR_FEATURES),
        "daymet_feature_count_available": int(
            sum(table[name].notna().all() for name in DAYMET_FEATURES)
        ),
        "sentinel_feature_count_available": int(
            sum(table[name].notna().all() for name in SENTINEL_FEATURES)
        ),
        "missing_rows_by_feature": missing_rows,
        "missing_rows_by_date_and_feature": missing_by_date,
        "sentinel_date_status": date_status,
        "existing_exploratory_label_keys_joined": label_count,
        "existing_label_count_by_date": {
            str(date.date()): int(group.existing_exploratory_label_available.sum())
            for date, group in joined.groupby("target_date", sort=True)
        },
        "sentinel_selected_physical_acquisitions": inventory["selected_physical_acquisitions"],
        "sentinel_selected_items": inventory["selected_items"],
        "sentinel_band_window_reads_if_fully_run": inventory["selected_items"]
        * len(inventory["assets"]),
        "sentinel_authenticated_acquisitions_cached": authenticated_acquisitions,
        "sentinel_acquisitions_remaining": len(sentinel_context.inventory.acquisitions)
        - authenticated_acquisitions,
        "sentinel_metadata_preflight_counts": preflight["counts"],
        "sentinel_all_products_calibrated": preflight["all_products_calibrated"],
        "sentinel_memberships_by_date": {
            str(day): int(count) for day, count in memberships.groupby("target_date").size().items()
        },
        "sentinel_generation_date_after_target_count": int(generated_after_date.sum()),
        "sentinel_generation_date_after_target_by_date": {
            str(day): int(generated_after_date.loc[group.index].sum())
            for day, group in membership_times.groupby("target_date", sort=True)
        },
        "sentinel_observation_lag_days_min_max": [
            int(memberships["lag_days"].min()),
            int(memberships["lag_days"].max()),
        ],
        "daymet_granule_count": daymet_inventory["granule_count"],
        "daymet_subset_y_indices_inclusive": daymet_inventory["subset_window"][
            "y_indices_inclusive"
        ],
        "daymet_subset_x_indices_inclusive": daymet_inventory["subset_window"][
            "x_indices_inclusive"
        ],
        "daymet_granule_updated_at_unique": sorted(
            {str(row["updated_at"]) for row in daymet_inventory["granules"]}
        ),
        "realtime_availability_claim": False,
        "daymet_access": (
            "authenticated frozen 30-subset local build complete"
            if daymet_completion is not None
            else "local authenticated build pending"
        ),
        "daymet_completion_sha256": (
            sha256_file(daymet_completion_path) if daymet_completion is not None else None
        ),
        "daymet_inventory_path": str((OUTPUT / "daymet_inventory.json").relative_to(ROOT)),
        "daymet_inventory_sha256": sha256_file(OUTPUT / "daymet_inventory.json"),
        "official_geometry_equivalence_verified": False,
        "formal_training_source_accepted": False,
        "scientific_predictor_table_complete": input_complete,
        "model_fit_predict_or_score": False,
        "target_temperature_values_deserialized": False,
        "input_records": input_records,
        "outputs": {
            "partial_predictor_trial": {
                "path": str(partial_path.relative_to(ROOT)),
                "sha256": sha256_file(partial_path),
            },
            "label_key_join_only": {
                "path": str(join_path.relative_to(ROOT)),
                "sha256": sha256_file(join_path),
            },
        },
        "static_source_records": static_record["source_records"],
    }
    atomic_json(summary, OUTPUT / "summary.json")
    return summary


def _official_preflight(*, batch: bool = False) -> tuple[dict, dict, dict]:
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_colorado_formal_build_plan"]
    expected_state = (
        "running_colorado_official_sentinel_batch_only"
        if batch
        else "running_colorado_official_predictor_preparation_only"
    )
    if stage["state"] != expected_state or (
        batch and not scope.get("sentinel_full_batch_approved", False)
    ):
        raise RuntimeError("Official predictor preparation stage is closed")
    permissions = stage["permissions"]
    if not all(
        permissions[name]
        for name in (
            "read_public_predictor_sources",
            "build_predictor",
            "read_new_candidate_public_metadata",
        )
    ):
        raise RuntimeError("Official predictor preparation permissions are incomplete")
    if any(
        permissions[name]
        for name in (
            "read_new_candidate_targets",
            "fit_model",
            "score_model",
            "read_external_targets",
        )
    ):
        raise RuntimeError("A forbidden target/model permission is open")
    paths = {
        "catalog": OFFICIAL_SUPPORT_DIR / "official_catalog.json",
        "support": OFFICIAL_SUPPORT_DIR / "support.json",
        "budget": OFFICIAL_OUTPUT / "budget.json",
        "target": OFFICIAL_TARGET_DIR / "summary.json",
    }
    for key, path in paths.items():
        expected = scope.get(
            {
                "catalog": "official_2020_catalog_sha256",
                "support": "official_fixed_support_sha256",
                "budget": "post_target_nonthermal_budget_sha256",
                "target": "official_target_summary_sha256",
            }[key]
        )
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"Frozen official {key} file or hash drifted")
    fixed = OFFICIAL_SUPPORT_DIR / "fixed_support.npz"
    support_record = json.loads(paths["support"].read_text(encoding="utf-8"))
    if sha256_file(fixed) != support_record["fixed_support_npz_sha256"]:
        raise RuntimeError("Frozen official fixed support drifted")
    return (
        stage,
        scope,
        {key: json.loads(path.read_text(encoding="utf-8")) for key, path in paths.items()},
    )


def official_support(*, batch: bool = False) -> components.PortableCitySupport:
    _, _, records = _official_preflight(batch=batch)
    from experiments.source_city_qa_boundary_audit import _tiger_frame, _tiger_to_existing_fields

    catalog, frozen = records["catalog"], records["support"]
    folder = ROOT / "data/raw/source_city_official_boundaries/2020/colorado"
    place_zip, tract_zip = folder / "tl_2020_08_place.zip", folder / "tl_2020_08_tract.zip"
    if (
        sha256_file(place_zip) != frozen["official_place_zip_sha256"]
        or sha256_file(tract_zip) != frozen["official_tract_zip_sha256"]
    ):
        raise RuntimeError("Official TIGER ZIP identity drifted")
    raw_place = _tiger_to_existing_fields(_tiger_frame(place_zip, role="place"), role="place")
    raw_tract = _tiger_to_existing_fields(_tiger_frame(tract_zip, role="tract"), role="tract")
    place = geography.standardize_place(
        raw_place.loc[raw_place["GEOID"].astype(str) == "0816000"], SPEC
    )
    tracts = geography.standardize_tracts(raw_tract, SPEC)
    _, primary = geography.select_city_tracts(
        place,
        tracts,
        city_id=CITY,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    geoids = tuple(primary.tract_geoid.astype(str))
    if list(geoids) != frozen["tract_geoids"] or list(geoids) != catalog["official_tract_geoids"]:
        raise RuntimeError("Official 110-tract selection drifted")
    grid, zones, _ = _grid_and_zones(place, primary, "EPSG:32613")
    if grid.sha256 != frozen["grid_sha256"]:
        raise RuntimeError("Official 30-m grid drifted")
    with np.load(OFFICIAL_SUPPORT_DIR / "fixed_support.npz") as cached:
        if not np.array_equal(zones, cached["zones"]):
            raise RuntimeError("Official zone assignment drifted")
        eligible = cached["eligible"].copy()
    if int(eligible.sum()) != frozen["fixed_eligible_land_cell_count"]:
        raise RuntimeError("Official fixed eligible land drifted")
    fixed = build_static_support(
        zones,
        eligible,
        geoids=geoids,
        grid_identity=sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz"),
    )
    return components.PortableCitySupport(
        CITY,
        grid,
        zones,
        eligible,
        geoids,
        primary,
        fixed,
        {"commit_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz")},
        {"commit_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "official_catalog.json")},
    )


def _official_dates(*, batch: bool = False) -> tuple[str, ...]:
    _, _, records = _official_preflight(batch=batch)
    dates = tuple(
        row["local_date"]
        for row in records["target"]["date_results"]
        if row["status"] == "target_usable_official"
    )
    if len(dates) != 47 or list(dates) != records["budget"]["retained_target_dates"]:
        raise RuntimeError("Official target-usable date list drifted")
    return dates


def run_official_keys() -> dict:
    s = official_support()
    dates = _official_dates()
    frame = pd.MultiIndex.from_product(
        [s.tract_geoids, pd.to_datetime(dates)], names=["tract_geoid", "target_date"]
    ).to_frame(index=False)
    frame.insert(0, "city_id", CITY)
    if len(frame) != 5170 or frame.duplicated(["tract_geoid", "target_date"]).any():
        raise RuntimeError("Official 5170-key universe invalid")
    label_keys = set()
    per_date = {}
    for day in dates:
        path = OFFICIAL_TARGET_DIR / "dates" / f"{day}_source_development_labels.csv"
        rows = pd.read_csv(
            path,
            usecols=["tract_geoid", "target_available_source_development"],
            dtype={"tract_geoid": str},
        )
        observed = set(
            rows.loc[rows.target_available_source_development.astype(bool), "tract_geoid"]
        )
        if not observed.issubset(set(s.tract_geoids)):
            raise RuntimeError(f"Existing label key outside official support: {day}")
        label_keys.update((geoid, day) for geoid in observed)
        per_date[day] = len(observed)
    if len(label_keys) != 4235:
        raise RuntimeError("Existing source-development label key count drifted")
    OFFICIAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OFFICIAL_OUTPUT / "full_keys.parquet"
    atomic_parquet(frame, path)
    result = {
        "state": "official_5170_keys_frozen",
        "tracts": 110,
        "dates": 47,
        "full_keys": 5170,
        "existing_label_keys_joined_without_temperature_values": len(label_keys),
        "label_keys_by_date": per_date,
        "target_values_read": False,
        "official_support_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz"),
        "target_summary_sha256": sha256_file(OFFICIAL_TARGET_DIR / "summary.json"),
        "full_keys_sha256": sha256_file(path),
        "target_dates": list(dates),
    }
    atomic_json(result, OFFICIAL_OUTPUT / "full_keys.json")
    return result


def run_official_static() -> dict:
    s = official_support()
    source_record = json.loads((MIRROR_OUTPUT / "static_trial.json").read_text(encoding="utf-8"))
    sources = source_record["source_records"]
    for record in sources:
        path = ROOT / record["path"]
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"Cached raw static source drifted: {record['path']}")
    # Raw rasters are reused; every tract aggregation is recomputed on the official grid.
    raw = MIRROR_OUTPUT / "raw_static"
    components.CITY_IDS = (CITY,)
    components.COMPONENT_ROOT = OFFICIAL_OUTPUT / "components"
    components.RUNTIME_ROOT = OFFICIAL_OUTPUT / "runtime_components"
    components.load_city_support = lambda _root, city_id: s if city_id == CITY else None
    components._static_source_paths = lambda _root, city_id: components.StaticSourcePaths(
        raw / "nlcd_2016_land_cover.tif",
        raw / "nlcd_2016_impervious.tif",
        tuple(raw / "terrain" / f"{tile_id}.tif" for tile_id in source_record["srtm_tile_ids"]),
        tuple(sources),
    )
    components.build_static_base_component(ROOT, CITY)
    components.build_gshhg_distance_component(ROOT, CITY)
    result = components.finalize_static_component(ROOT, CITY)
    if result["row_count"] != 110 or result["model_feature_count"] != 18:
        raise RuntimeError("Official static feature shape invalid")
    atomic_json(
        {
            "state": "official_static_18_complete",
            "official_support_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz"),
            "cached_raw_source_sha256": {Path(row["path"]).name: row["sha256"] for row in sources},
            "result": result,
            "mirror_derived_aggregates_reused": False,
            "feature_sha256": sha256_file(
                OFFICIAL_OUTPUT / "components" / CITY / "static_features.parquet"
            ),
        },
        OFFICIAL_OUTPUT / "static_build.json",
    )
    return {"state": "official_static_18_complete"}


def run_official_daymet() -> dict:
    from la_heat.daymet_feature_stage import compile_daymet_feature_tables
    from la_heat.daymet_grid import inspect_daymet_netcdf, validate_daymet_direct_subset_spec

    s = official_support()
    full = pd.read_parquet(OFFICIAL_OUTPUT / "full_keys.parquet")
    inventory = json.loads((MIRROR_OUTPUT / "daymet_inventory.json").read_text(encoding="utf-8"))
    window = inventory["subset_window"]
    place_zip = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_place.zip"
    place = gpd.read_file(place_zip)
    official_bounds = (
        place.loc[place.GEOID.astype(str) == "0816000"].to_crs(window["grid_crs"]).total_bounds
    )
    bbox = window["projected_bbox_m"]
    if not (
        bbox[0] <= official_bounds[0]
        and bbox[1] <= official_bounds[1]
        and bbox[2] >= official_bounds[2]
        and bbox[3] >= official_bounds[3]
    ):
        raise RuntimeError("Raw Daymet subset does not cover official place")
    records = []
    for row in inventory["granules"]:
        year, variable = int(row["year"]), row["variable"]
        path = MIRROR_OUTPUT / "daymet_subsets" / str(year) / f"{variable}.nc"
        spec = inspect_daymet_netcdf(path, variable=variable, year=year, final_test_year=2025)
        validate_daymet_direct_subset_spec(
            spec,
            y_indices=tuple(window["y_indices_inclusive"]),
            x_indices=tuple(window["x_indices_inclusive"]),
            bbox_wgs84=place.loc[place.GEOID.astype(str) == "0816000"].total_bounds.tolist(),
        )
        records.append({"path": path, "year": year, "variable": variable})
    if len(records) != 30:
        raise RuntimeError("Expected exactly 30 cached Daymet subsets")
    compiled = compile_daymet_feature_tables(
        pd.DataFrame(records),
        full[["tract_geoid", "target_date"]],
        zone_raster=s.zones,
        eligible_land_mask=s.eligible_land,
        tract_geoids=s.tract_geoids,
        target_transform=s.grid.transform,
        target_crs=s.grid.crs,
        final_test_year=2025,
    )
    frame = compiled.features.assign(city_id=CITY)[
        ["city_id", "tract_geoid", "target_date", *DAYMET_FEATURES]
    ]
    if len(frame) != 5170 or frame.duplicated(["tract_geoid", "target_date"]).any():
        raise RuntimeError("Official Daymet key coverage invalid")
    path = OFFICIAL_OUTPUT / "components" / CITY / "daymet_features.parquet"
    atomic_parquet(frame, path)
    missing = {name: int(frame[name].isna().sum()) for name in DAYMET_FEATURES}
    result = {
        "state": "official_daymet_21_complete",
        "rows": len(frame),
        "feature_count": len(DAYMET_FEATURES),
        "missing_by_feature": missing,
        "source_inventory_sha256": sha256_file(MIRROR_OUTPUT / "daymet_inventory.json"),
        "source_subsets": {
            f"{row['year']}/{row['variable']}": sha256_file(row["path"]) for row in records
        },
        "feature_sha256": sha256_file(path),
        "official_support_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz"),
        "mirror_derived_aggregates_reused": False,
        "target_values_read": False,
    }
    atomic_json(result, OFFICIAL_OUTPUT / "daymet_build.json")
    return result


def run_official_non_sentinel() -> dict:
    _official_preflight()
    keys_frame = pd.read_parquet(OFFICIAL_OUTPUT / "full_keys.parquet")
    static = pd.read_parquet(OFFICIAL_OUTPUT / "components" / CITY / "static_features.parquet")
    daymet = pd.read_parquet(OFFICIAL_OUTPUT / "components" / CITY / "daymet_features.parquet")
    calendar = build_calendar_features(
        keys_frame[["tract_geoid", "target_date"]], final_test_year=2025
    )
    frame = keys_frame.merge(
        static[["tract_geoid", *STATIC_FEATURES]], on="tract_geoid", validate="many_to_one"
    )
    frame = frame.merge(
        calendar[["tract_geoid", "target_date", *CALENDAR_FEATURES]],
        on=["tract_geoid", "target_date"],
        validate="one_to_one",
    )
    frame = frame.merge(
        daymet[["tract_geoid", "target_date", *DAYMET_FEATURES]],
        on=["tract_geoid", "target_date"],
        validate="one_to_one",
    )
    frame = frame[
        [
            "city_id",
            "tract_geoid",
            "target_date",
            *STATIC_FEATURES,
            *CALENDAR_FEATURES,
            *DAYMET_FEATURES,
        ]
    ]
    if (
        len(frame) != 5170
        or len(STATIC_FEATURES) + len(CALENDAR_FEATURES) + len(DAYMET_FEATURES) != 41
    ):
        raise RuntimeError("Official 41-feature table shape invalid")
    path = OFFICIAL_OUTPUT / "official_non_sentinel_41.parquet"
    atomic_parquet(frame, path)
    missing_by_date = {
        str(day.date()): {
            name: int(group[name].isna().sum())
            for name in (*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES)
        }
        for day, group in frame.groupby("target_date", sort=True)
    }
    complete = [
        day
        for day, counts in missing_by_date.items()
        if all(value == 0 for value in counts.values())
    ]
    result = {
        "state": "official_non_sentinel_41_complete",
        "rows": len(frame),
        "features": 41,
        "feature_names": [*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES],
        "complete_dates": complete,
        "missing_by_date": missing_by_date,
        "inputs_sha256": {
            "keys": sha256_file(OFFICIAL_OUTPUT / "full_keys.parquet"),
            "static": sha256_file(
                OFFICIAL_OUTPUT / "components" / CITY / "static_features.parquet"
            ),
            "daymet": sha256_file(
                OFFICIAL_OUTPUT / "components" / CITY / "daymet_features.parquet"
            ),
        },
        "output_sha256": sha256_file(path),
        "target_values_read": False,
        "model_fit_or_score": False,
    }
    atomic_json(result, OFFICIAL_OUTPUT / "non_sentinel_41.json")
    return result


def run_official_sentinel_inventory() -> dict:
    """Freeze exactly the budgeted 416/1643 official-support optical inventory."""
    import pystac_client

    from la_heat.multicity.portable_sentinel_inventory import (
        _membership_frame,
        _selected_acquisitions_frame,
        _selected_items_frame,
        _snapshot_filename,
        _snapshot_text,
        build_city_window_membership,
    )
    from la_heat.sentinel_inventory import (
        SENTINEL_COLLECTION,
        canonical_stac_item_snapshot,
        query_sentinel_items,
        select_all_reprocessing_cohorts,
        sentinel_record_from_item,
    )

    _, _, records = _official_preflight()
    dates = tuple(date.fromisoformat(day) for day in _official_dates())
    budget = records["budget"]
    directory = OFFICIAL_OUTPUT / "sentinel_inventory"
    marker = directory / "INVENTORY.json"
    if marker.exists():
        result = json.loads(marker.read_text(encoding="utf-8"))
        if result["selected_items"] != 1643 or result["selected_physical_acquisitions"] != 416:
            raise RuntimeError("Frozen official Sentinel inventory size drifted")
        for filename, digest in result["files_sha256"].items():
            if sha256_file(directory / filename) != digest:
                raise RuntimeError(f"Frozen official Sentinel inventory {filename} drifted")
        return result
    place_zip = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_place.zip"
    frame = gpd.read_file(place_zip)
    aoi = frame.loc[frame.GEOID.astype(str) == "0816000"].to_crs("EPSG:4326").geometry.iloc[0]
    zone = ZoneInfo("America/Denver")
    client = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    snapshots, items = {}, {}
    for query in budget["merged_sentinel_d_minus_60_to_minus_1_windows"]:
        start, stop = (
            date.fromisoformat(query["first_local_date"]),
            date.fromisoformat(query["last_local_date"]),
        )
        interval = (
            datetime.combine(start, time.min, tzinfo=zone)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        interval += "/" + datetime.combine(
            stop + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC).isoformat().replace("+00:00", "Z")
        for item in query_sentinel_items(
            client, intersects=aoi, datetime_interval=interval, collection=SENTINEL_COLLECTION
        ):
            snapshot = canonical_stac_item_snapshot(item)
            item_id = str(snapshot["id"])
            body = _snapshot_text(snapshot)
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if item_id in snapshots and snapshots[item_id]["sha256"] != digest:
                raise RuntimeError("Official Sentinel STAC item changed between query windows")
            items[item_id] = item
            snapshots[item_id] = {
                "filename": _snapshot_filename(item_id),
                "sha256": digest,
                "text": body,
            }
    candidates = select_all_reprocessing_cohorts(
        tuple(sentinel_record_from_item(items[key]) for key in sorted(items)),
        aoi_geometry_wgs84=aoi,
        analysis_crs="EPSG:32613",
    )
    members = build_city_window_membership(dates, candidates, timezone="America/Denver")
    selected_ids = {row.acquisition_key for row in members}
    selected = tuple(row for row in candidates if row.acquisition_key in selected_ids)
    chosen_ids = {item.item_id for row in selected for item in row.items}
    if (
        sorted(chosen_ids) != budget["unique_sentinel_item_ids"]
        or sorted(key.semantic_id for key in selected_ids)
        != budget["selected_physical_acquisition_keys"]
    ):
        raise RuntimeError("Fresh official Sentinel query differs from frozen post-target budget")
    chosen = {key: snapshots[key] for key in sorted(chosen_ids)}
    acquisitions = _selected_acquisitions_frame(CITY, "America/Denver", selected)
    selected_items = _selected_items_frame(CITY, "America/Denver", selected, chosen)
    membership = _membership_frame(CITY, members)
    if (
        len(acquisitions) != 416
        or len(selected_items) != 1643
        or set(membership.target_date) != set(_official_dates())
    ):
        raise RuntimeError("Official Sentinel inventory count/dependency drifted")
    directory.mkdir(parents=True, exist_ok=True)
    for filename, table in (
        ("selected_acquisitions.csv", acquisitions),
        ("selected_items.csv", selected_items),
        ("target_window_membership.csv", membership),
    ):
        atomic_csv(table, directory / filename)
    for item in chosen.values():
        atomic_text(item["text"], directory / "stac" / item["filename"])
    result = {
        "state": "official_sentinel_inventory_frozen",
        "dates": list(_official_dates()),
        "selected_physical_acquisitions": len(acquisitions),
        "selected_items": len(selected_items),
        "target_window_memberships": len(membership),
        "window_days_before_target": [60, 1],
        "budget_sha256": sha256_file(OFFICIAL_OUTPUT / "budget.json"),
        "files_sha256": {
            filename: sha256_file(directory / filename)
            for filename in (
                "selected_acquisitions.csv",
                "selected_items.csv",
                "target_window_membership.csv",
            )
        },
        "scientific_rasters_read": False,
        "target_values_read": False,
    }
    atomic_json(result, marker)
    return result


def run_official_sentinel_metadata() -> dict:
    from la_heat.sentinel_feature_builder import _read_product_metadata

    _official_preflight()
    directory = OFFICIAL_OUTPUT / "sentinel_inventory"
    inventory = json.loads((directory / "INVENTORY.json").read_text(encoding="utf-8"))
    selected = pd.read_csv(directory / "selected_items.csv", dtype=str)
    members = pd.read_csv(directory / "target_window_membership.csv", dtype=str)
    if len(selected) != 1643 or len(members) != inventory["target_window_memberships"]:
        raise RuntimeError("Official Sentinel inventory drifted")
    dates_by_acquisition = (
        members.groupby("physical_acquisition_id")["target_date"]
        .agg(lambda values: sorted(set(values)))
        .to_dict()
    )
    products = {}
    for row in selected.itertuples(index=False):
        path = directory / "stac" / str(row.snapshot_filename)
        if sha256_file(path) != str(row.snapshot_sha256):
            raise RuntimeError("Official STAC snapshot changed")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        uri = str(snapshot["properties"]["s2:product_uri"])
        href = str(snapshot["assets"]["product-metadata"]["href"])
        if href != str(row.asset_product_metadata_href):
            raise RuntimeError("Official product metadata href changed")
        entry = products.setdefault(
            uri,
            {
                "item_id": str(row.item_id),
                "snapshot": snapshot,
                "metadata_href": href,
                "acquisitions": set(),
                "dates": set(),
            },
        )
        if entry["item_id"] != str(row.item_id) or entry["metadata_href"] != href:
            raise RuntimeError("Conflicting Sentinel product identity")
        acquisition = str(row.physical_acquisition_id)
        entry["acquisitions"].add(acquisition)
        entry["dates"].update(dates_by_acquisition[acquisition])
    result_path = OFFICIAL_OUTPUT / "sentinel_metadata_preflight.json"
    previous = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.exists()
        else {"products": []}
    )
    inspected = {row["product_uri"]: row for row in previous["products"]}
    mirror_dir = MIRROR_OUTPUT / "sentinel_product_metadata"
    official_dir = OFFICIAL_OUTPUT / "sentinel_product_metadata"
    official_dir.mkdir(parents=True, exist_ok=True)

    def inspect(uri: str, entry: dict) -> dict:
        _official_preflight()
        try:
            from la_heat.sentinel_feature_builder import _safe_item_filename

            old = mirror_dir / _safe_item_filename(entry["item_id"])
            if old.is_file():
                content, digest, path = old.read_bytes(), sha256_file(old), old
            else:
                with requests.Session() as session:
                    from requests.adapters import HTTPAdapter
                    from urllib3.util.retry import Retry

                    session.mount(
                        "https://",
                        HTTPAdapter(
                            max_retries=Retry(
                                total=2,
                                backoff_factor=0.5,
                                status_forcelist=(429, 500, 502, 503, 504),
                            )
                        ),
                    )
                    content, digest, path = _read_product_metadata(
                        item_id=entry["item_id"],
                        unsigned_url=entry["metadata_href"],
                        raw_metadata_directory=official_dir,
                        session=session,
                    )
            outcome = _classify_sentinel_product_xml(entry["snapshot"], content)
            outcome.update({"xml_sha256": digest, "xml_path": str(path.relative_to(ROOT))})
        except (requests.RequestException, OSError, ValueError, ET.ParseError) as error:
            outcome = {"category": "read_or_identity_failure", "reason": type(error).__name__}
        return {
            "product_uri": uri,
            "item_id": entry["item_id"],
            "physical_acquisitions": sorted(entry["acquisitions"]),
            "target_dates": sorted(entry["dates"]),
            **outcome,
        }

    todo = [(uri, entry) for uri, entry in products.items() if uri not in inspected]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(inspect, uri, entry): uri for uri, entry in todo}
        for future in as_completed(futures):
            row = future.result()
            inspected[row["product_uri"]] = row
            if len(inspected) % 50 == 0 or len(inspected) == len(products):
                print(
                    f"COLORADO_OFFICIAL_SENTINEL_METADATA {len(inspected)}/{len(products)}",
                    flush=True,
                )
                atomic_json(
                    {
                        "state": "running_resumable",
                        "products": sorted(
                            inspected.values(), key=lambda value: value["product_uri"]
                        ),
                    },
                    result_path,
                )
    rows = sorted(inspected.values(), key=lambda value: value["product_uri"])
    counts = dict(Counter(row["category"] for row in rows))
    by_date = _official_metadata_impact(rows, _official_dates())
    result = {
        "state": "official_all_sentinel_metadata_audited",
        "inventory_sha256": sha256_file(directory / "INVENTORY.json"),
        "unique_product_count": len(rows),
        "counts": counts,
        "by_target_date": by_date,
        "calibrated_dates": [day for day, row in by_date.items() if not row["blocked_products"]],
        "blocked_dates": [day for day, row in by_date.items() if row["blocked_products"]],
        "products": rows,
        "scientific_rasters_read": False,
        "target_values_read": False,
    }
    atomic_json(result, result_path)
    return result


def _official_metadata_impact(products: list[dict], dates: tuple[str, ...]) -> dict:
    blocked_categories = {"required_offset_missing", "read_or_identity_failure"}
    return {
        day: {
            "product_count": sum(day in row["target_dates"] for row in products),
            "blocked_products": [
                row["product_uri"]
                for row in products
                if day in row["target_dates"] and row["category"] in blocked_categories
            ],
        }
        for day in dates
    }


def _official_sentinel_context(*, batch: bool = False):
    from la_heat.multicity import portable_sentinel_build as build

    _official_preflight(batch=batch)
    directory = OFFICIAL_OUTPUT / "sentinel_inventory"
    inventory = json.loads((directory / "INVENTORY.json").read_text(encoding="utf-8"))
    for filename, digest in inventory["files_sha256"].items():
        if sha256_file(directory / filename) != digest:
            raise RuntimeError(f"Official Sentinel {filename} changed")
    acquisitions = pd.read_csv(
        directory / "selected_acquisitions.csv", dtype={"processing_baseline": "string"}
    )
    items = pd.read_csv(directory / "selected_items.csv", dtype={"processing_baseline": "string"})
    membership = pd.read_csv(directory / "target_window_membership.csv")
    frozen = build.FrozenSentinelInputs(
        acquisitions,
        items,
        membership,
        {"local_timezone": "America/Denver"},
        {"inventory_files_sha256": canonical_sha256(inventory["files_sha256"])},
    )
    s = official_support(batch=batch)
    spatial = build._fixed_spatial_support(s, target_dates=_official_dates(batch=batch))
    stage = build._stage_for_city(ROOT, CITY, "America/Denver")
    context = build.CityBuildContext(
        CITY,
        frozen,
        s,
        spatial,
        stage,
        {
            "official_support_sha256": sha256_file(OFFICIAL_SUPPORT_DIR / "fixed_support.npz"),
            "stage_sha256": stage.sha256,
            **frozen.locks,
            **spatial.locks,
        },
        OFFICIAL_OUTPUT / "sentinel_runtime",
        OFFICIAL_OUTPUT / "sentinel_compiled",
        OFFICIAL_OUTPUT / "sentinel_product_metadata",
    )
    for path in (context.runtime_directory, context.output_directory, context.metadata_directory):
        path.mkdir(parents=True, exist_ok=True)
    return build, context


def _windows_peak_working_set_bytes() -> int:
    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(MemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise RuntimeError("Could not measure Windows process working set")
    return int(counters.PeakWorkingSetSize)


def run_official_sentinel_samples() -> dict:
    """Two pre-frozen metadata-cleared cost samples, not a batch build."""
    import threading

    metadata = json.loads(
        (OFFICIAL_OUTPUT / "sentinel_metadata_preflight.json").read_text(encoding="utf-8")
    )
    if metadata["state"] != "official_all_sentinel_metadata_audited":
        raise RuntimeError("Full Sentinel calibration audit must finish before image samples")
    build, context = _official_sentinel_context()
    acquisitions = context.inventory.acquisitions
    members = context.inventory.membership
    samples_path = OFFICIAL_OUTPUT / "sentinel_cost_sample_selection.json"
    if not samples_path.is_file():
        safe_ids = set(members.physical_acquisition_id.astype(str))
        blocked_ids = {
            a
            for row in metadata["products"]
            if row["category"] in ("required_offset_missing", "read_or_identity_failure")
            for a in row["physical_acquisitions"]
        }
        options = acquisitions.loc[
            acquisitions.physical_acquisition_id.astype(str).isin(safe_ids - blocked_ids)
        ].copy()
        choices = []
        for item_count in (1, 4):
            tier = options.loc[options.item_count.astype(int) == item_count].sort_values(
                ["union_city_coverage_fraction", "physical_acquisition_id"]
            )
            if len(tier):
                row = tier.iloc[len(tier) // 2]
                choices.append(
                    {
                        "physical_acquisition_id": str(row.physical_acquisition_id),
                        "item_count": int(row.item_count),
                        "union_city_coverage_fraction": float(row.union_city_coverage_fraction),
                    }
                )
        if len(choices) != 2:
            raise RuntimeError("Cannot preselect one-item and four-item calibrated cost samples")
        atomic_json(
            {
                "state": "frozen_before_image_read",
                "selection_basis": (
                    "middle city coverage within one-item and four-item tiers; "
                    "no target values or model errors"
                ),
                "metadata_sha256": sha256_file(
                    OFFICIAL_OUTPUT / "sentinel_metadata_preflight.json"
                ),
                "samples": choices,
            },
            samples_path,
        )
    selection = json.loads(samples_path.read_text(encoding="utf-8"))
    if selection["metadata_sha256"] != sha256_file(
        OFFICIAL_OUTPUT / "sentinel_metadata_preflight.json"
    ):
        raise RuntimeError("Sample calibration audit changed")
    results_path = OFFICIAL_OUTPUT / "sentinel_cost_samples.json"
    results = (
        json.loads(results_path.read_text(encoding="utf-8"))["samples"]
        if results_path.is_file()
        else []
    )
    rows = {str(row.physical_acquisition_id): row for row in acquisitions.itertuples(index=False)}
    for sample in selection["samples"]:
        _official_preflight()
        acquisition_id = sample["physical_acquisition_id"]
        row = rows[acquisition_id]
        if any(record["physical_acquisition_id"] == acquisition_id for record in results):
            continue
        if build.acquisition_cache_is_current(ROOT, context, row):
            results.append(
                {
                    "physical_acquisition_id": acquisition_id,
                    "cache_hit": True,
                    "measured_network_bytes": 0,
                    "elapsed_seconds": 0,
                    "authenticated_cache": True,
                }
            )
            atomic_json({"state": "bounded_samples", "samples": results}, results_path)
            continue
        stop = threading.Event()
        temp_peak = [0]

        def monitor(stop_event=stop, peak_values=temp_peak):
            while not stop_event.wait(0.2):
                peak_values[0] = max(
                    peak_values[0],
                    sum(
                        path.stat().st_size
                        for path in OFFICIAL_OUTPUT.rglob("*")
                        if path.is_file() and path.suffix in (".tmp", ".partial")
                    ),
                )

        before = _cache_bytes(context.runtime_directory, context.metadata_directory)
        old_setting = os.environ.get("CPL_VSIL_NETWORK_STATS_ENABLED")
        os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = "YES"
        lib = _gdal_network_statistics_library()
        lib.VSINetworkStatsReset()
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        started = clock.monotonic()
        failure = None
        try:
            build._process_one(ROOT, context, row, download_threads=2, force=False)
        except Exception as error:
            failure = type(error).__name__
        finally:
            elapsed = clock.monotonic() - started
            stop.set()
            thread.join(timeout=2)
            methods = _gdal_network_method_totals(lib)
            if old_setting is None:
                os.environ.pop("CPL_VSIL_NETWORK_STATS_ENABLED", None)
            else:
                os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = old_setting
        authenticated = build.acquisition_cache_is_current(ROOT, context, row)
        result = {
            "physical_acquisition_id": acquisition_id,
            "item_count": int(row.item_count),
            "union_city_coverage_fraction": float(row.union_city_coverage_fraction),
            "elapsed_seconds": round(elapsed, 3),
            "peak_process_rss_bytes": _windows_peak_working_set_bytes(),
            "temporary_file_peak_bytes_observed": int(temp_peak[0]),
            "gdal_http_methods": methods,
            "persistent_cache_bytes_added": _cache_bytes(
                context.runtime_directory, context.metadata_directory
            )
            - before,
            "authenticated_cache": authenticated,
            "failure_type": failure,
        }
        results.append(result)
        atomic_json({"state": "bounded_samples", "samples": results}, results_path)
        print(f"COLORADO_OFFICIAL_SENTINEL_SAMPLE {len(results)}/2", flush=True)
        if failure or not authenticated:
            break
    return {"state": "bounded_samples_finished", "samples": results}


def run_official_batch_plan() -> dict:
    """Prepare a resumable future plan; deliberately do not process an acquisition."""
    import shutil

    metadata = json.loads(
        (OFFICIAL_OUTPUT / "sentinel_metadata_preflight.json").read_text(encoding="utf-8")
    )
    build, context = _official_sentinel_context()
    blocked_dates = set(metadata["blocked_dates"])
    blocked_acquisitions = {
        acquisition
        for product in metadata["products"]
        if product["category"] in ("required_offset_missing", "read_or_identity_failure")
        for acquisition in product["physical_acquisitions"]
    }
    rows = []
    for row in context.inventory.acquisitions.itertuples(index=False):
        acquisition_id = str(row.physical_acquisition_id)
        dependencies = sorted(
            set(
                context.inventory.membership.loc[
                    context.inventory.membership.physical_acquisition_id.astype(str)
                    == acquisition_id,
                    "target_date",
                ].astype(str)
            )
        )
        rows.append(
            {
                "physical_acquisition_id": acquisition_id,
                "item_count": int(row.item_count),
                "target_dates": dependencies,
                "needed_for_calibrated_date": any(day not in blocked_dates for day in dependencies),
                "authenticated_official_cache": bool(
                    build.acquisition_cache_is_current(ROOT, context, row)
                ),
                "calibration_blocked": acquisition_id in blocked_acquisitions,
            }
        )
    sample_path = OFFICIAL_OUTPUT / "sentinel_cost_samples.json"
    samples = (
        json.loads(sample_path.read_text(encoding="utf-8"))["samples"]
        if sample_path.exists()
        else []
    )
    measured = {
        int(row["item_count"]): row
        for row in samples
        if row.get("authenticated_cache") and not row.get("cache_hit")
    }
    pending = [
        row
        for row in rows
        if row["needed_for_calibrated_date"]
        and not row["authenticated_official_cache"]
        and not row["calibration_blocked"]
    ]
    by_item_count = dict(Counter(row["item_count"] for row in pending))
    # Interpolate per acquisition between the frozen 1-item and 4-item samples.
    estimated = None
    if 1 in measured and 4 in measured:

        def network_bytes(sample):
            return sum(
                int(method.get("downloaded_bytes", 0))
                for method in sample["gdal_http_methods"].values()
            )

        def interpolated(field, count):
            low, high = field(measured[1]), field(measured[4])
            return max(0, low + (high - low) * (count - 1) / 3)

        center_network = sum(interpolated(network_bytes, row["item_count"]) for row in pending)
        center_seconds = sum(
            interpolated(lambda sample: sample["elapsed_seconds"], row["item_count"])
            for row in pending
        )
        center_disk = sum(
            interpolated(
                lambda sample: max(0, sample["persistent_cache_bytes_added"]), row["item_count"]
            )
            for row in pending
        )
        estimated = {
            "method": (
                "item-count-stratified interpolation of 1/4-item samples; "
                "0.5–2x engineering scenarios, not confidence interval"
            ),
            "pending_item_count_tiers": by_item_count,
            "network_get_body_gb_scenario": [
                round(center_network * factor / 1e9, 2) for factor in (0.5, 2)
            ],
            "wall_hours_scenario": [
                round(center_seconds * factor / 3600, 2) for factor in (0.5, 2)
            ],
            "persistent_cache_gb_scenario": [
                round(center_disk * factor / 1e9, 2) for factor in (0.5, 2)
            ],
            "sample_peak_process_memory_gb": round(
                max(sample["peak_process_rss_bytes"] for sample in measured.values()) / 1e9, 2
            ),
            "sample_observed_temporary_file_peak_gb": round(
                max(sample["temporary_file_peak_bytes_observed"] for sample in measured.values())
                / 1e9,
                3,
            ),
        }
    result = {
        "state": "prepared_not_executed",
        "full_batch_authorized": False,
        "entrypoint_after_separate_authorization": (
            ".\\.venv\\Scripts\\python.exe -m experiments.source_city_predictor_trial "
            "official_sentinel_batch"
        ),
        "batch_entry_current_behavior": (
            "implemented but intentionally refuses until a separate execution "
            "authorization reopens its narrow stage flag"
        ),
        "resource_stop_conditions": [
            "calibration mismatch",
            "source identity drift",
            "disk free below 30 GiB",
            "network body materially exceeds stratified estimate",
        ],
        "calibrated_dates": metadata["calibrated_dates"],
        "blocked_dates": metadata["blocked_dates"],
        "sample_based_remaining_estimate": estimated,
        "current_disk_free_gb": round(shutil.disk_usage(OFFICIAL_OUTPUT).free / 1e9, 2),
        "acquisitions": rows,
        "scientific_rasters_read_by_this_mode": False,
    }
    atomic_json(result, OFFICIAL_OUTPUT / "sentinel_batch_plan.json")
    return result


def run_official_preparation_summary() -> dict:
    _official_preflight()
    names = {
        "keys": "full_keys.json",
        "static": "static_build.json",
        "daymet": "daymet_build.json",
        "non_sentinel": "non_sentinel_41.json",
        "inventory": "sentinel_inventory/INVENTORY.json",
        "metadata": "sentinel_metadata_preflight.json",
        "samples": "sentinel_cost_samples.json",
        "batch_plan": "sentinel_batch_plan.json",
    }
    records = {
        key: json.loads((OFFICIAL_OUTPUT / name).read_text(encoding="utf-8"))
        for key, name in names.items()
    }
    if (
        records["keys"]["full_keys"] != 5170
        or records["keys"]["existing_label_keys_joined_without_temperature_values"] != 4235
        or len(records["non_sentinel"]["complete_dates"]) != 47
        or records["metadata"]["unique_product_count"] != 1643
        or len(records["metadata"]["calibrated_dates"]) + len(records["metadata"]["blocked_dates"])
        != 47
    ):
        raise RuntimeError("Official preparation evidence is incomplete or inconsistent")
    plan = records["batch_plan"]
    pending = sum(
        row["needed_for_calibrated_date"]
        and not row["authenticated_official_cache"]
        and not row["calibration_blocked"]
        for row in plan["acquisitions"]
    )
    result = {
        "state": "official_predictor_preparation_complete_no_full_sentinel_batch",
        "city_id": CITY,
        "official_2020_geometry": True,
        "mirror_equivalence_gate_passed": False,
        "full_prediction_keys": 5170,
        "existing_development_label_keys_joined_only": 4235,
        "non_sentinel_feature_count": 41,
        "non_sentinel_complete_dates": 47,
        "sentinel_physical_acquisitions": 416,
        "sentinel_products": 1643,
        "sentinel_metadata_categories": records["metadata"]["counts"],
        "sentinel_calibrated_dates": records["metadata"]["calibrated_dates"],
        "sentinel_calibration_blocked_dates": records["metadata"]["blocked_dates"],
        "sentinel_calibration_blocked_products": [
            row["product_uri"]
            for row in records["metadata"]["products"]
            if row["category"] in ("required_offset_missing", "read_or_identity_failure")
        ],
        "remaining_clean_date_acquisitions": pending,
        "resource_estimate": plan["sample_based_remaining_estimate"],
        "disk_free_gb_at_plan": plan["current_disk_free_gb"],
        "input_evidence_sha256": {
            key: sha256_file(OFFICIAL_OUTPUT / name) for key, name in names.items()
        },
        "new_target_values_read": False,
        "model_fit_or_score": False,
        "full_sentinel_batch_executed": False,
        "full_training_table_accepted": False,
    }
    atomic_json(result, OFFICIAL_OUTPUT / "preparation_summary.json")
    return result


def run_official_sentinel_batch() -> dict:
    """Resume only authenticated nonblocked acquisitions after separate approval."""
    import shutil
    import threading

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_colorado_formal_build_plan"]
    if stage["state"] != "running_colorado_official_sentinel_batch_only" or not scope.get(
        "sentinel_full_batch_approved", False
    ):
        raise RuntimeError("Full Sentinel batch is NOT authorized in the current stage")
    plan_path = OFFICIAL_OUTPUT / "sentinel_batch_plan.json"
    if scope.get("official_sentinel_batch_plan_sha256") != sha256_file(plan_path):
        raise RuntimeError("Frozen official Sentinel batch plan hash drifted")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["state"] != "prepared_not_executed":
        raise RuntimeError("Frozen Sentinel batch plan is unavailable")
    progress_path = OFFICIAL_OUTPUT / "sentinel_batch_progress.json"
    progress = (
        json.loads(progress_path.read_text(encoding="utf-8"))
        if progress_path.is_file()
        else {
            "state": "running",
            "batch_plan_sha256": sha256_file(plan_path),
            "new_network_bytes": 0,
            "elapsed_seconds": 0.0,
            "attempts": [],
            "peak_process_rss_bytes": 0,
            "peak_temporary_bytes_observed": 0,
            "minimum_disk_free_bytes_observed": shutil.disk_usage(OFFICIAL_OUTPUT).free,
        }
    )
    if progress["batch_plan_sha256"] != sha256_file(plan_path):
        raise RuntimeError("Sentinel batch checkpoint belongs to a different plan")
    disk_floor = 30 * 1024**3
    network_ceiling = 80_000_000_000
    wall_ceiling = 24 * 3600

    def check_limits() -> None:
        if progress["new_network_bytes"] >= network_ceiling:
            raise RuntimeError("Sentinel batch stopped: 80 GB network response limit")
        if progress["elapsed_seconds"] >= wall_ceiling:
            raise RuntimeError("Sentinel batch stopped: 24 h cumulative wall limit")
        if shutil.disk_usage(OFFICIAL_OUTPUT).free < disk_floor:
            raise RuntimeError("Sentinel batch stopped: less than 30 GiB disk free")

    check_limits()
    build, context = _official_sentinel_context(batch=True)
    sample_rows = json.loads(
        (OFFICIAL_OUTPUT / "sentinel_cost_samples.json").read_text(encoding="utf-8")
    )["samples"]
    measured_bytes = {
        int(sample["item_count"]): sum(
            int(method.get("downloaded_bytes", 0))
            for method in sample["gdal_http_methods"].values()
        )
        for sample in sample_rows
        if sample.get("authenticated_cache") and not sample.get("cache_hit")
    }
    if set(measured_bytes) != {1, 4}:
        raise RuntimeError("Frozen one/four-item resource samples are unavailable")
    rows = {
        str(row.physical_acquisition_id): row
        for row in context.inventory.acquisitions.itertuples(index=False)
    }
    eligible = [
        item for item in plan["acquisitions"]
        if item["needed_for_calibrated_date"] and not item["calibration_blocked"]
    ]
    completed = 0
    for item in plan["acquisitions"]:
        if item["calibration_blocked"] or not item["needed_for_calibrated_date"]:
            continue
        _official_preflight(batch=True)
        row = rows[item["physical_acquisition_id"]]
        if build.acquisition_cache_is_current(ROOT, context, row):
            completed += 1
            continue
        prior_failures = sum(
            attempt["physical_acquisition_id"] == item["physical_acquisition_id"]
            and not attempt["authenticated_cache"]
            for attempt in progress["attempts"]
        )
        for retry in range(prior_failures, OFFICIAL_SENTINEL_MAX_ATTEMPTS_PER_ACQUISITION):
            check_limits()
            stop_monitor = threading.Event()
            monitored = {"temp_bytes": 0, "disk_free": shutil.disk_usage(OFFICIAL_OUTPUT).free}

            def monitor(stop_event=stop_monitor, measured=monitored) -> None:
                temp_root = Path(os.environ.get("TEMP", ""))
                while not stop_event.wait(0.5):
                    measured["disk_free"] = min(
                        measured["disk_free"], shutil.disk_usage(OFFICIAL_OUTPUT).free
                    )
                    if temp_root.is_dir():
                        measured["temp_bytes"] = max(
                            measured["temp_bytes"],
                            sum(p.stat().st_size for p in temp_root.rglob("*") if p.is_file()),
                        )

            old_setting = os.environ.get("CPL_VSIL_NETWORK_STATS_ENABLED")
            os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = "YES"
            library = _gdal_network_statistics_library()
            library.VSINetworkStatsReset()
            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            started = clock.monotonic()
            failure = None
            try:
                build._process_one(
                    ROOT,
                    context,
                    row,
                    download_threads=2 if retry == 0 else 1,
                    force=False,
                )
            except Exception as error:
                failure = type(error).__name__
            finally:
                elapsed = clock.monotonic() - started
                stop_monitor.set()
                thread.join(timeout=2)
                network_methods = _gdal_network_method_totals(library)
                if old_setting is None:
                    os.environ.pop("CPL_VSIL_NETWORK_STATS_ENABLED", None)
                else:
                    os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"] = old_setting
            observed_bytes = sum(
                int(method.get("downloaded_bytes", 0)) for method in network_methods.values()
            )
            progress["new_network_bytes"] += observed_bytes
            progress["elapsed_seconds"] += elapsed
            progress["peak_process_rss_bytes"] = max(
                progress["peak_process_rss_bytes"], _windows_peak_working_set_bytes()
            )
            progress["peak_temporary_bytes_observed"] = max(
                progress["peak_temporary_bytes_observed"], monitored["temp_bytes"]
            )
            progress["minimum_disk_free_bytes_observed"] = min(
                progress["minimum_disk_free_bytes_observed"], monitored["disk_free"]
            )
            authenticated = build.acquisition_cache_is_current(ROOT, context, row)
            progress["attempts"].append({
                "physical_acquisition_id": item["physical_acquisition_id"],
                "retry": retry,
                "elapsed_seconds": round(elapsed, 3),
                "new_network_bytes": observed_bytes,
                "failure_type": failure,
                "authenticated_cache": authenticated,
            })
            atomic_json(progress, progress_path)
            print(
                f"COLORADO_OFFICIAL_SENTINEL_BATCH {completed}/{len(eligible)} "
                f"network_gb={progress['new_network_bytes']/1e9:.2f} "
                f"elapsed_h={progress['elapsed_seconds']/3600:.2f} "
                f"failure={failure or 'none'}",
                flush=True,
            )
            check_limits()
            if authenticated:
                break
            if (
                failure not in OFFICIAL_SENTINEL_RETRYABLE_ERRORS
                or retry + 1 >= OFFICIAL_SENTINEL_MAX_ATTEMPTS_PER_ACQUISITION
            ):
                raise RuntimeError(
                    f"Official Sentinel batch stopped at {item['physical_acquisition_id']}: "
                    f"{failure or 'unauthenticated_cache'}"
                ) from None
            delay = OFFICIAL_SENTINEL_RETRY_DELAYS_SECONDS[retry]
            print(
                f"COLORADO_OFFICIAL_SENTINEL_AUTO_RECOVER retry={retry + 1}/"
                f"{OFFICIAL_SENTINEL_MAX_ATTEMPTS_PER_ACQUISITION - 1} "
                f"wait_s={delay} failure={failure}",
                flush=True,
            )
            clock.sleep(delay)
        else:
            raise RuntimeError(
                f"Official Sentinel batch stopped at {item['physical_acquisition_id']}: "
                "bounded retry already exhausted"
            )
        if not build.acquisition_cache_is_current(ROOT, context, row):
            raise RuntimeError("Sentinel acquisition cache did not authenticate")
        expected_bytes = (
            measured_bytes[1]
            + (measured_bytes[4] - measured_bytes[1]) * (int(row.item_count) - 1) / 3
        )
        if observed_bytes > 4 * max(expected_bytes, 1):
            raise RuntimeError("Sentinel batch stopped: network body exceeded 4x sample tier")
        completed += 1
        print(
            f"COLORADO_OFFICIAL_SENTINEL_BATCH {completed}/{len(eligible)}", flush=True
        )
    from la_heat.sentinel_feature_builder import _acquisition_cache_directory
    from la_heat.sentinel_features import INDEX_COLUMNS, build_previous_60_day_composites

    safe_dates = tuple(plan["calibrated_dates"])
    needed = [
        rows[item["physical_acquisition_id"]]
        for item in plan["acquisitions"]
        if item["needed_for_calibrated_date"] and not item["calibration_blocked"]
    ]
    if any(not build.acquisition_cache_is_current(ROOT, context, row) for row in needed):
        raise RuntimeError("A required official Sentinel acquisition is not authenticated")
    frames = [
        pd.read_parquet(
            _acquisition_cache_directory(
                context.runtime_directory, str(row.physical_acquisition_id)
            )
            / "acquisition_tract.parquet"
        )
        for row in needed
    ]
    members = context.inventory.membership.loc[
        context.inventory.membership.target_date.astype(str).isin(safe_dates)
    ].copy()
    composites = build_previous_60_day_composites(
        pd.concat(frames, ignore_index=True),
        members,
        target_dates=safe_dates,
        tract_geoids=context.support.tract_geoids,
        minimum_acquisition_coverage=context.stage.minimum_coverage,
        minimum_acquisitions=context.stage.minimum_acquisitions,
        final_test_year=2025,
        unlock_final_test=False,
    )
    if (
        len(composites.features) != 110 * len(safe_dates)
        or composites.features.duplicated(["tract_geoid", "target_date"]).any()
    ):
        raise RuntimeError("Official Sentinel composite lacks full prediction keys")
    missing = composites.features[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not missing.isin([0, len(INDEX_COLUMNS)]).all():
        raise RuntimeError("Official Sentinel composite has partial feature missingness")
    outputs = {}
    for filename, frame in (
        ("sentinel_features.parquet", composites.features),
        ("sentinel_feature_audit.parquet", composites.audit),
        ("sentinel_lineage.parquet", composites.lineage),
    ):
        value = frame.copy()
        if "city_id" not in value:
            value.insert(0, "city_id", CITY)
        elif set(value.city_id.astype(str)) != {CITY}:
            raise RuntimeError("Official Sentinel output contains another city")
        destination = context.output_directory / filename
        atomic_parquet(value, destination)
        outputs[filename] = {"sha256": sha256_file(destination), "rows": len(value)}
    completion = {
        "state": "official_46_calibration_cleared_sentinel_complete",
        "target_dates": list(safe_dates),
        "calibration_blocked_dates": plan["blocked_dates"],
        "physical_acquisitions": len(needed),
        "feature_rows": len(composites.features),
        "available_feature_rows": int((missing == 0).sum()),
        "missing_observation_rows": int((missing == len(INDEX_COLUMNS)).sum()),
        "batch_plan_sha256": sha256_file(plan_path),
        "outputs": outputs,
        "target_values_read": False,
        "model_fit_or_score": False,
    }
    atomic_json(completion, context.output_directory / "OFFICIAL_SENTINEL_COMPLETE.json")
    return completion


def run_official_predictor_acceptance() -> dict:
    """Accept the frozen 46-date table while retaining the blocked date's keys."""
    completion_path = (
        OFFICIAL_OUTPUT / "sentinel_compiled" / "OFFICIAL_SENTINEL_COMPLETE.json"
    )
    sentinel_path = OFFICIAL_OUTPUT / "sentinel_compiled" / "sentinel_features.parquet"
    non_sentinel_path = OFFICIAL_OUTPUT / "official_non_sentinel_41.parquet"
    key_record_path = OFFICIAL_OUTPUT / "full_keys.json"
    key_path = OFFICIAL_OUTPUT / "full_keys.parquet"
    progress_path = OFFICIAL_OUTPUT / "sentinel_batch_progress.json"
    required = (
        completion_path,
        sentinel_path,
        non_sentinel_path,
        key_record_path,
        key_path,
        progress_path,
    )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Official predictor acceptance lacks inputs: {missing}")

    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    key_record = json.loads(key_record_path.read_text(encoding="utf-8"))
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    blocked_dates = tuple(completion["calibration_blocked_dates"])
    if blocked_dates != ("2021-10-18",):
        raise RuntimeError("Official calibration-blocked date set drifted")
    clear_dates = tuple(completion["target_dates"])
    if len(clear_dates) != 46 or set(clear_dates) & set(blocked_dates):
        raise RuntimeError("Official Sentinel completion date partition is invalid")
    if completion["outputs"]["sentinel_features.parquet"]["sha256"] != sha256_file(
        sentinel_path
    ):
        raise RuntimeError("Official Sentinel feature table hash drifted")

    keys_frame = pd.read_parquet(key_path)
    non_sentinel = pd.read_parquet(non_sentinel_path)
    sentinel = pd.read_parquet(sentinel_path)
    for frame in (keys_frame, non_sentinel, sentinel):
        frame["tract_geoid"] = frame["tract_geoid"].astype(str)
        frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.normalize()
    key_columns = ["city_id", "tract_geoid", "target_date"]
    if (
        len(keys_frame) != 5170
        or keys_frame.duplicated(key_columns).any()
        or keys_frame.target_date.nunique() != 47
        or keys_frame.tract_geoid.nunique() != 110
    ):
        raise RuntimeError("Official full prediction key universe failed acceptance")
    if (
        len(non_sentinel) != 5170
        or non_sentinel.duplicated(key_columns).any()
        or any(
            non_sentinel[name].isna().any()
            for name in (*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES)
        )
    ):
        raise RuntimeError("Official 41-feature table failed acceptance")
    if (
        len(sentinel) != 5060
        or sentinel.duplicated(key_columns).any()
        or sentinel.target_date.nunique() != 46
        or sentinel.tract_geoid.nunique() != 110
        or set(sentinel.target_date.dt.strftime("%Y-%m-%d")) != set(clear_dates)
    ):
        raise RuntimeError("Official Sentinel table failed key acceptance")
    sentinel_missing = sentinel[list(SENTINEL_FEATURES)].isna().sum(axis=1)
    if (sentinel_missing != 0).any():
        raise RuntimeError("Official clear-date Sentinel rows contain missing features")

    table = non_sentinel.merge(
        sentinel[key_columns + list(SENTINEL_FEATURES)],
        on=key_columns,
        how="left",
        validate="one_to_one",
    )
    table["input_status"] = np.where(
        table.target_date.dt.strftime("%Y-%m-%d").isin(blocked_dates),
        "calibration_blocked",
        "accepted_46_features",
    )
    clear_mask = table.input_status == "accepted_46_features"
    blocked_mask = table.input_status == "calibration_blocked"
    if (
        clear_mask.sum() != 5060
        or blocked_mask.sum() != 110
        or table.loc[clear_mask, list(FEATURE_NAMES)].isna().any(axis=None)
        or not table.loc[blocked_mask, list(SENTINEL_FEATURES)].isna().all(axis=None)
        or table.loc[blocked_mask, [*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES]]
        .isna()
        .any(axis=None)
    ):
        raise RuntimeError("Official 47-date status partition failed feature acceptance")
    finite_clear = np.isfinite(
        table.loc[clear_mask, list(FEATURE_NAMES)].to_numpy(dtype=float)
    ).all()
    if not finite_clear:
        raise RuntimeError("Official clear-date predictor table contains nonfinite values")

    output_path = OFFICIAL_OUTPUT / "official_predictor_inputs_46_with_blocked_date.parquet"
    atomic_parquet(
        table[[*key_columns, "input_status", *FEATURE_NAMES]], output_path
    )
    blocked_label_count = int(key_record["label_keys_by_date"][blocked_dates[0]])
    clear_label_count = int(
        key_record["existing_label_keys_joined_without_temperature_values"]
        - blocked_label_count
    )
    progress["state"] = "complete"
    progress["accepted_feature_rows"] = 5060
    progress["calibration_blocked_rows"] = 110
    atomic_json(progress, progress_path)
    result = {
        "state": "official_colorado_predictor_inputs_accepted_46_dates_one_date_blocked",
        "full_prediction_rows": 5170,
        "accepted_dates": 46,
        "accepted_rows": 5060,
        "accepted_feature_count": 46,
        "accepted_rows_with_any_missing_or_nonfinite_feature": 0,
        "calibration_blocked_dates": list(blocked_dates),
        "calibration_blocked_rows": 110,
        "existing_development_label_keys_clear_dates": clear_label_count,
        "existing_development_label_keys_blocked_date": blocked_label_count,
        "existing_development_label_keys_total": int(
            key_record["existing_label_keys_joined_without_temperature_values"]
        ),
        "sentinel_physical_acquisitions": completion["physical_acquisitions"],
        "sentinel_normal_observation_missing_rows": completion[
            "missing_observation_rows"
        ],
        "resource_actual": {
            "network_response_bytes": int(progress["new_network_bytes"]),
            "acquisition_elapsed_seconds": float(progress["elapsed_seconds"]),
            "peak_process_rss_bytes": int(progress["peak_process_rss_bytes"]),
            "peak_temporary_bytes_observed_in_monitored_directory": int(
                progress["peak_temporary_bytes_observed"]
            ),
            "minimum_disk_free_bytes_observed": int(
                progress["minimum_disk_free_bytes_observed"]
            ),
            "attempt_count": len(progress["attempts"]),
            "authenticated_attempt_count": sum(
                bool(row["authenticated_cache"]) for row in progress["attempts"]
            ),
        },
        "inputs_sha256": {
            "full_keys": sha256_file(key_path),
            "non_sentinel_41": sha256_file(non_sentinel_path),
            "sentinel_completion": sha256_file(completion_path),
            "sentinel_features": sha256_file(sentinel_path),
            "batch_plan": completion["batch_plan_sha256"],
        },
        "output": {
            "path": str(output_path.relative_to(ROOT)),
            "sha256": sha256_file(output_path),
            "rows": len(table),
        },
        "target_values_read": False,
        "model_fit_or_score": False,
        "ready_for_separately_authorized_fixed_model_experiment": True,
        "readiness_scope": "46 accepted dates only; 2021-10-18 remains calibration-blocked",
    }
    acceptance_path = OFFICIAL_OUTPUT / "OFFICIAL_PREDICTOR_INPUT_ACCEPTANCE.json"
    atomic_json(result, acceptance_path)

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_colorado_formal_build_plan"]
    stage["revision"] = "2026-09-colorado-official-predictor-inputs-accepted"
    stage["state"] = "complete_colorado_official_predictor_inputs_46_dates"
    stage["next_safe_stage"] = "separate_fixed_model_experiment_contract_if_authorized"
    stage["summary"] = (
        "Colorado official predictor input acceptance is complete for 46 calibration-cleared "
        "dates (5,060 rows, 46 features); 2021-10-18 remains calibration-blocked with "
        "110 keys retained. No target values, model fit, or scoring were performed."
    )
    for permission in stage["permissions"]:
        stage["permissions"][permission] = False
    scope["official_46_feature_predictor_build_complete"] = True
    scope["official_46_feature_predictor_accepted_dates"] = 46
    scope["official_46_feature_predictor_accepted_rows"] = 5060
    scope["official_46_feature_predictor_acceptance"] = str(
        acceptance_path.relative_to(ROOT)
    )
    scope["official_46_feature_predictor_acceptance_sha256"] = sha256_file(
        acceptance_path
    )
    scope["official_sentinel_clean_date_acquisitions_remaining"] = 0
    scope["sentinel_resume_pre_authorized"] = False
    scope["temporary_permissions_closed"] = True
    scope["model_fit_or_scoring_authorized"] = False
    atomic_json(stage, ACTIVE)
    return result


def _windows_pid_is_running(pid: int) -> bool:
    """Return whether a recorded Windows worker PID is still alive."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def run_official_sentinel_resume() -> dict:
    """Open the preauthorized batch gate, resume its checkpoint, then close the gate."""
    import shutil

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage["source_city_colorado_formal_build_plan"]
    worker_path = OFFICIAL_OUTPUT / "sentinel_batch_worker.json"
    if stage["state"] == "running_colorado_official_sentinel_batch_only":
        worker = (
            json.loads(worker_path.read_text(encoding="utf-8"))
            if worker_path.is_file()
            else {}
        )
        worker_pid = int(worker.get("pid", 0))
        if worker_pid and _windows_pid_is_running(worker_pid):
            raise RuntimeError(f"Official Sentinel worker is already active (PID {worker_pid})")
        for name in (
            "read_public_predictor_sources",
            "build_predictor",
            "read_new_candidate_public_metadata",
        ):
            stage["permissions"][name] = False
        stage["state"] = "paused_colorado_official_sentinel_batch"
        stage["revision"] = "2026-09-colorado-official-sentinel-orphan-auto-recovered"
        scope["sentinel_full_batch_approved"] = False
        scope["official_predictor_preparation_scope"]["full_sentinel_image_batch_allowed"] = False
        scope["temporary_permissions_closed"] = True
        atomic_json(stage, ACTIVE)
        print("COLORADO_OFFICIAL_SENTINEL_ORPHANED_RUN_RECOVERED", flush=True)
    if stage["state"] != "paused_colorado_official_sentinel_batch" or not scope.get(
        "sentinel_resume_pre_authorized", False
    ):
        raise RuntimeError("Official Sentinel resume is not preauthorized from this stage")
    plan_path = OFFICIAL_OUTPUT / "sentinel_batch_plan.json"
    plan_sha = sha256_file(plan_path)
    if scope["official_sentinel_batch_plan_sha256"] != plan_sha:
        raise RuntimeError("Frozen official Sentinel batch plan drifted")
    checkpoint = OFFICIAL_OUTPUT / "sentinel_batch_progress.json"
    if checkpoint.is_file():
        progress = json.loads(checkpoint.read_text(encoding="utf-8"))
        if progress["batch_plan_sha256"] != plan_sha:
            raise RuntimeError("Sentinel checkpoint belongs to another plan")
        if progress["new_network_bytes"] >= 80_000_000_000:
            raise RuntimeError("Sentinel network cap already reached")
        if progress["elapsed_seconds"] >= 24 * 3600:
            raise RuntimeError("Sentinel wall-time cap already reached")
    if shutil.disk_usage(OFFICIAL_OUTPUT).free < 30 * 1024**3:
        raise RuntimeError("Less than 30 GiB free on the output disk")
    temp_root = ROOT / "data/runtime/colorado_official_sentinel/tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    for name in (
        "read_public_predictor_sources",
        "build_predictor",
        "read_new_candidate_public_metadata",
    ):
        stage["permissions"][name] = True
    stage["state"] = "running_colorado_official_sentinel_batch_only"
    stage["revision"] = "2026-09-colorado-official-sentinel-batch-resumed"
    scope["sentinel_full_batch_approved"] = True
    scope["official_predictor_preparation_scope"]["full_sentinel_image_batch_allowed"] = True
    scope["temporary_permissions_closed"] = False
    atomic_json(stage, ACTIVE)
    atomic_json(
        {
            "pid": os.getpid(),
            "batch_plan_sha256": plan_sha,
            "started_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        worker_path,
    )
    print("COLORADO_OFFICIAL_SENTINEL_RESUMING_FROM_CHECKPOINT", flush=True)
    try:
        return run_official_sentinel_batch()
    except RuntimeError as error:
        current = json.loads(ACTIVE.read_text(encoding="utf-8"))
        if current["state"] != "paused_colorado_official_sentinel_batch":
            raise
        if str(error) != "Official predictor preparation stage is closed":
            raise
        print("COLORADO_OFFICIAL_SENTINEL_PAUSED_AT_ACQUISITION_BOUNDARY", flush=True)
        return {"state": "paused_at_acquisition_boundary"}
    finally:
        current = json.loads(ACTIVE.read_text(encoding="utf-8"))
        if current["state"] == "running_colorado_official_sentinel_batch_only":
            completion = OFFICIAL_OUTPUT / "sentinel_compiled/OFFICIAL_SENTINEL_COMPLETE.json"
            current["state"] = (
                "complete_colorado_official_sentinel_batch_pending_acceptance"
                if completion.is_file()
                else "paused_colorado_official_sentinel_batch"
            )
            current["revision"] = "2026-09-colorado-official-sentinel-batch-gate-closed"
            for name in (
                "read_public_predictor_sources",
                "build_predictor",
                "read_new_candidate_public_metadata",
            ):
                current["permissions"][name] = False
            current_scope = current["source_city_colorado_formal_build_plan"]
            current_scope["sentinel_full_batch_approved"] = False
            current_scope["official_predictor_preparation_scope"][
                "full_sentinel_image_batch_allowed"
            ] = False
            current_scope["temporary_permissions_closed"] = True
            atomic_json(current, ACTIVE)
        worker_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "static",
            "keys",
            "daymet_inventory",
            "sentinel_inventory",
            "sentinel",
            "sentinel_five_dates",
            "sentinel_cost",
            "sentinel_metadata_preflight",
            "daymet_access_probe",
            "daymet_local",
            "audit",
            "official_budget",
            "official_keys",
            "official_static",
            "official_daymet",
            "official_non_sentinel",
            "official_sentinel_inventory",
            "official_sentinel_metadata",
            "official_sentinel_samples",
            "official_batch_plan",
            "official_preparation_summary",
            "official_sentinel_batch",
            "official_sentinel_resume",
            "official_predictor_acceptance",
        ),
    )
    parser.add_argument("--maximum-new-acquisitions", type=int)
    args = parser.parse_args()
    if args.mode == "official_budget":
        result = run_official_budget()
        print("COLORADO_OFFICIAL_NONTHERMAL_BUDGET", result["unique_sentinel_items"])
        return
    if args.mode == "official_sentinel_resume":
        result = run_official_sentinel_resume()
        print(result["state"], flush=True)
        return
    if args.mode == "official_predictor_acceptance":
        result = run_official_predictor_acceptance()
        print("COLORADO_OFFICIAL_PREDICTOR_INPUT_ACCEPTED", result["accepted_rows"])
        return
    if args.mode.startswith("official_"):
        if args.mode == "official_sentinel_batch":
            result = run_official_sentinel_batch()
            print("COLORADO OFFICIAL_SENTINEL_BATCH", result["state"], flush=True)
            return
        actions = {
            "official_keys": run_official_keys,
            "official_static": run_official_static,
            "official_daymet": run_official_daymet,
            "official_non_sentinel": run_official_non_sentinel,
            "official_sentinel_inventory": run_official_sentinel_inventory,
            "official_sentinel_metadata": run_official_sentinel_metadata,
            "official_sentinel_samples": run_official_sentinel_samples,
            "official_batch_plan": run_official_batch_plan,
            "official_preparation_summary": run_official_preparation_summary,
        }
        result = actions[args.mode]()
        print("COLORADO", args.mode.upper(), result.get("state", "complete"), flush=True)
        return
    if args.mode == "daymet_access_probe":
        if not probe_daymet_access():
            raise SystemExit(1)
        return
    if args.mode == "daymet_local":
        result = run_daymet_local()
        print("COLORADO_DAYMET_21_COMPLETE", result["key_count"])
        return
    s = support()
    if args.mode == "keys":
        OUTPUT.mkdir(parents=True, exist_ok=True)
        atomic_parquet(keys(s), OUTPUT / "full_keys.parquet")
        print("COLORADO_FULL_KEYS_660")
    elif args.mode == "static":
        run_static()
        print("COLORADO_STATIC_18_COMPLETE")
    elif args.mode == "sentinel_inventory":
        result = run_sentinel_inventory()
        print("COLORADO_SENTINEL_INVENTORY", result["selected_physical_acquisitions"])
    elif args.mode == "daymet_inventory":
        result = run_daymet_inventory()
        print("COLORADO_DAYMET_INVENTORY", result["granule_count"])
    elif args.mode == "sentinel":
        result = run_sentinel_values(maximum_new_acquisitions=args.maximum_new_acquisitions)
        print(
            "COLORADO_SENTINEL_RESULT",
            result["complete"],
            result["total"],
            bool(result["compiled"]),
        )
    elif args.mode == "sentinel_five_dates":
        result = run_sentinel_isolated_five_dates()
        print("COLORADO_SENTINEL_FIVE_DATES_COMPLETE", result["feature_rows"])
    elif args.mode == "sentinel_cost":
        result = run_sentinel_cost_samples()
        print("COLORADO_SENTINEL_COST_COMPLETE", len(result["samples"]))
    elif args.mode == "sentinel_metadata_preflight":
        result = run_sentinel_metadata_preflight()
        print("COLORADO_SENTINEL_METADATA_COMPLETE", result["counts"])
    elif args.mode == "audit":
        result = run_audit()
        print(
            "COLORADO_PREDICTOR_TRIAL_AUDIT",
            result["full_predictor_rows"],
            result["existing_exploratory_label_keys_joined"],
        )


def run_official_budget() -> dict:
    """Estimate only the unique, post-screening official predictor asset scope."""
    import pystac_client
    import rasterio

    from la_heat.multicity.portable_sentinel_inventory import build_city_window_membership
    from la_heat.sentinel_inventory import (
        SENTINEL_COLLECTION,
        query_sentinel_items,
        select_all_reprocessing_cohorts,
        sentinel_record_from_item,
    )

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    if stage["state"] != "running_colorado_official_support_catalog_qa_target_only":
        raise RuntimeError("Official Colorado budget stage is not active.")
    if not stage["permissions"]["read_new_candidate_public_metadata"]:
        raise RuntimeError("Official predictor metadata permission is closed.")
    qa_dir = ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co"
    target_dir = ROOT / "exports/SOURCE_CITY_TARGET_AVAILABILITY/official_2020/colorado_springs_co"
    qa_summary = json.loads((qa_dir / "qa_summary.json").read_text(encoding="utf-8"))
    target_summary_path = target_dir / "summary.json"
    target_summary = json.loads(target_summary_path.read_text(encoding="utf-8"))
    if (
        qa_summary["state"] != "official_all_candidate_qa_finished"
        or target_summary["state"] != "official_qa_passing_thermal_screen_finished"
        or target_summary["qa_summary_sha256"] != sha256_file(qa_dir / "qa_summary.json")
    ):
        raise RuntimeError("QA and target screen must finish before asset budgeting.")
    dates = tuple(
        date.fromisoformat(row["local_date"])
        for row in target_summary["date_results"]
        if row["status"] == "target_usable_official"
    )
    if not dates:
        raise RuntimeError("No target-usable official dates; no Sentinel budget is relevant.")
    catalog = json.loads((qa_dir / "official_catalog.json").read_text(encoding="utf-8"))
    place_zip = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_place.zip"
    if sha256_file(place_zip) != catalog["official_place_zip_sha256"]:
        raise RuntimeError("Official place ZIP changed after catalog freeze.")
    original = gpd.read_file(place_zip)
    place = original.loc[original["GEOID"].astype(str) == "0816000"]
    if len(place) != 1 or place.crs is None:
        raise RuntimeError("Official Colorado place identity changed.")
    aoi = shapely.union_all(place.to_crs("EPSG:4326").geometry.to_numpy())
    zone = ZoneInfo("America/Denver")
    windows = sorted((d - timedelta(days=60), d - timedelta(days=1)) for d in dates)
    merged: list[tuple[date, date]] = []
    for start, stop in windows:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, stop))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
    client = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    items = {}
    query_windows = []
    for start, stop in merged:
        start_utc = datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC)
        end_utc = datetime.combine(stop + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        interval = (
            start_utc.isoformat().replace("+00:00", "Z")
            + "/"
            + end_utc.isoformat().replace("+00:00", "Z")
        )
        found = query_sentinel_items(
            client,
            intersects=aoi,
            datetime_interval=interval,
            collection=SENTINEL_COLLECTION,
        )
        query_windows.append(
            {
                "first_local_date": start.isoformat(),
                "last_local_date": stop.isoformat(),
                "catalog_items": len(found),
            }
        )
        for item in found:
            items[item.id] = item
    records = tuple(sentinel_record_from_item(items[key]) for key in sorted(items))
    candidates = select_all_reprocessing_cohorts(
        records,
        aoi_geometry_wgs84=aoi,
        analysis_crs="EPSG:32613",
    )
    members = build_city_window_membership(dates, candidates, timezone="America/Denver")
    selected_ids = {row.acquisition_key for row in members}
    selected = [row for row in candidates if row.acquisition_key in selected_ids]
    item_ids = {item.item_id for row in selected for item in row.items}
    if not selected or not item_ids:
        raise RuntimeError("Official retained-date Sentinel inventory is empty.")
    sample_item_bytes = 113082421 / 4  # One measured four-tile acquisition; not a guarantee.
    sample_item_seconds = 82.446 / 4
    center_bytes = len(item_ids) * sample_item_bytes
    center_hours = len(item_ids) * sample_item_seconds / 3600
    mirror_cache = (
        ROOT / "exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co"
    )
    support = json.loads((qa_dir / "support.json").read_text(encoding="utf-8"))
    tract_zip = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_tract.zip"
    if sha256_file(tract_zip) != catalog["official_tract_zip_sha256"]:
        raise RuntimeError("Official tract ZIP changed after catalog freeze.")
    tract = gpd.read_file(tract_zip)
    tract = tract.loc[tract["GEOID"].astype(str).isin(support["tract_geoids"])]
    if len(tract) != 110:
        raise RuntimeError("Official 110-tract support changed.")
    place_5070 = place.to_crs("EPSG:5070").geometry.iloc[0]
    official_clipped_bounds = (
        tract.to_crs("EPSG:5070").geometry.intersection(place_5070).total_bounds
    )
    nlcd_coverage = {}
    for name in ("land_cover", "impervious"):
        path = mirror_cache / "raw_static" / f"nlcd_2016_{name}.tif"
        with rasterio.open(path) as source:
            bounds = source.bounds
            covered = (
                bounds.left <= official_clipped_bounds[0]
                and bounds.bottom <= official_clipped_bounds[1]
                and bounds.right >= official_clipped_bounds[2]
                and bounds.top >= official_clipped_bounds[3]
            )
        nlcd_coverage[name] = {
            "full_clipped_support_covered": bool(covered),
            "source_sha256": sha256_file(path),
        }
    daymet_inventory = json.loads((mirror_cache / "daymet_inventory.json").read_text())
    official_daymet_bounds = place.to_crs(
        daymet_inventory["subset_window"]["grid_crs"]
    ).total_bounds
    cached_daymet_bounds = daymet_inventory["subset_window"]["projected_bbox_m"]
    daymet_footprint_covered = (
        cached_daymet_bounds[0] <= official_daymet_bounds[0]
        and cached_daymet_bounds[1] <= official_daymet_bounds[1]
        and cached_daymet_bounds[2] >= official_daymet_bounds[2]
        and cached_daymet_bounds[3] >= official_daymet_bounds[3]
    )

    def cached_bytes(name: str) -> int:
        return sum(
            path.stat().st_size for path in (mirror_cache / name).rglob("*") if path.is_file()
        )

    required_bands = ("B02", "B03", "B04", "B08", "B8A", "B11", "B12", "SCL")
    known_blocked_product = "S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE"
    known_blocked_item = "S2A_MSIL2A_20211005T174211_R098_T13SED_20220512T201134"
    output = {
        "state": "post_target_official_nonthermal_scope_budget_only_no_image_reads",
        "qa_summary_sha256": sha256_file(qa_dir / "qa_summary.json"),
        "target_summary_sha256": sha256_file(target_summary_path),
        "official_place_zip_sha256": sha256_file(place_zip),
        "retained_target_dates": [d.isoformat() for d in dates],
        "merged_sentinel_d_minus_60_to_minus_1_windows": query_windows,
        "unique_sentinel_physical_acquisitions": len(selected),
        "unique_sentinel_items": len(item_ids),
        "unique_required_band_asset_count": len(required_bands) * len(item_ids),
        "unique_required_product_metadata_asset_count": len(item_ids),
        "unique_band_plus_product_metadata_asset_count": (len(required_bands) + 1) * len(item_ids),
        "required_band_keys": list(required_bands),
        "unique_sentinel_item_ids": sorted(item_ids),
        "selected_physical_acquisition_keys": sorted(key.semantic_id for key in selected_ids),
        "target_window_memberships": len(members),
        "known_boa_offset_blocked_product": known_blocked_product,
        "known_boa_offset_blocked_product_selected": any(
            item_id.startswith(known_blocked_item) for item_id in item_ids
        ),
        "remaining_item_calibration_metadata_not_yet_audited": True,
        "old_mirror_derived_sentinel_acquisition_cache_reusable": False,
        "reason": (
            "Official tract geometry and spatial aggregation changed; "
            "item metadata can be reused only after identity checks."
        ),
        "estimated_additional_sentinel_get_body_gb_scenario_0_5_to_2x": [
            round(center_bytes * factor / 1e9, 2) for factor in (0.5, 2.0)
        ],
        "estimated_sentinel_wall_hours_scenario_0_5_to_2x": [
            round(center_hours * factor, 2) for factor in (0.5, 2.0)
        ],
        "estimate_basis": (
            "82.446 seconds and 113082421 GET response bytes for one 4-item "
            "acquisition; extrapolated by unique item, not target date; "
            "range is engineering scenario, not confidence interval"
        ),
        "persistent_sentinel_metadata_cache_mb_scenario": [
            round(len(item_ids) * factor, 1) for factor in (0.05, 0.2)
        ],
        "existing_mirror_raw_cache_bytes_not_yet_officially_reused": {
            "static": cached_bytes("raw_static"),
            "daymet_subsets": cached_bytes("daymet_subsets"),
            "sentinel_inventory": cached_bytes("sentinel_inventory"),
        },
        "raw_static_footprint_check": nlcd_coverage,
        "raw_daymet_footprint_check": {
            "cached_subset_contains_official_place_bounds": bool(daymet_footprint_covered),
            "inventory_sha256": sha256_file(mirror_cache / "daymet_inventory.json"),
        },
        "disk_budget_limit": (
            "Metadata/derived-output scenario excludes temporary COG reads and any "
            "raw subset that fails official-footprint validation; budget must be revised "
            "before high-cost acquisition. Old mirror tract aggregates are not reusable."
        ),
        "static_and_daymet_raw_cache_note": (
            "Revalidate and reuse raw NLCD/SRTM/GSHHG and 30 annual Daymet subsets "
            "if official support lies inside footprints; recompute all tract "
            "aggregations; additional source transfer conditional on coverage checks."
        ),
        "high_cost_sentinel_values_read": False,
        "model_fit_or_scoring_performed": False,
    }
    destination = (
        ROOT / "exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020/colorado_springs_co/budget.json"
    )
    atomic_json(output, destination)
    return output


if __name__ == "__main__":
    main()
