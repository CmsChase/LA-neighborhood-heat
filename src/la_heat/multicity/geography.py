"""Target-blind Census place and tract staging for the cross-city study."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from la_heat.multicity.config import CitySpec, MulticityPlan, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION = "multicity-census-geography-v1"
USER_AGENT = "la-neighborhood-heat/0.1 (student research; metadata-only pilot)"
COMMON_PLACE_FIELDS = (
    "OBJECTID,GEOID,STATE,PLACE,BASENAME,NAME,FUNCSTAT,AREALAND"
)
COMMON_TRACT_FIELDS = (
    "OBJECTID,GEOID,STATE,COUNTY,TRACT,BASENAME,NAME,FUNCSTAT,AREALAND"
)


class MulticityGeographyError(ValueError):
    """Raised when the geography stage violates a scientific contract."""


class LayerUnavailableError(RuntimeError):
    """Raised when a configured public layer cannot be reached or queried."""


@dataclass(frozen=True)
class LayerCandidate:
    """One fixed ArcGIS source option for a Census geography layer."""

    label: str
    url: str
    provider: str
    source_status: str
    item_id: str | None = None

    @property
    def origin(self) -> str:
        parsed = urlparse(self.url)
        return f"{parsed.scheme}://{parsed.netloc}"


@dataclass(frozen=True)
class LayerAcquisition:
    """In-memory source response plus the exact bytes to preserve."""

    candidate: LayerCandidate
    frame: gpd.GeoDataFrame
    metadata: dict[str, Any]
    raw_files: dict[str, bytes]
    attempts: tuple[dict[str, Any], ...] = ()


def _retrying_session() -> requests.Session:
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _decode_json(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LayerUnavailableError(f"{label} did not return JSON.") from exc
    if not isinstance(payload, dict):
        raise LayerUnavailableError(f"{label} returned a non-object JSON root.")
    if "error" in payload:
        raise LayerUnavailableError(f"{label} returned ArcGIS error: {payload['error']!r}")
    return payload


def _request_bytes(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    label: str,
    timeout: tuple[int, int] = (15, 120),
) -> tuple[bytes, dict[str, Any]]:
    try:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LayerUnavailableError(f"{label} request failed: {type(exc).__name__}.") from exc
    content = response.content
    return content, _decode_json(content, label=label)


def _validate_layer_metadata(
    metadata: dict[str, Any],
    *,
    role: str,
) -> None:
    if metadata.get("geometryType") != "esriGeometryPolygon":
        raise MulticityGeographyError(f"{role} source is not an ArcGIS polygon layer.")
    fields = {
        str(field.get("name"))
        for field in metadata.get("fields", [])
        if isinstance(field, dict)
    }
    required = (
        set(COMMON_PLACE_FIELDS.split(","))
        if role == "place"
        else set(COMMON_TRACT_FIELDS.split(","))
    )
    missing = sorted(required - fields)
    if missing:
        raise MulticityGeographyError(
            f"{role} source is missing required Census fields: {missing}"
        )
    formats = str(metadata.get("supportedQueryFormats", "")).lower()
    if "geojson" not in formats:
        raise MulticityGeographyError(f"{role} source does not support GeoJSON queries.")
    if int(metadata.get("maxRecordCount", 0)) <= 0:
        raise MulticityGeographyError(f"{role} source has no positive page limit.")


def _item_metadata(
    session: requests.Session,
    candidate: LayerCandidate,
) -> tuple[bytes, dict[str, Any]] | None:
    if candidate.item_id is None:
        return None
    url = f"https://www.arcgis.com/sharing/rest/content/items/{candidate.item_id}"
    content, payload = _request_bytes(
        session,
        url,
        params={"f": "json"},
        label=f"{candidate.label} item metadata",
        timeout=(15, 60),
    )
    if payload.get("id") != candidate.item_id:
        raise MulticityGeographyError("ArcGIS item identity changed.")
    item_url = str(payload.get("url", "")).rstrip("/")
    expected_service_url = candidate.url.rsplit("/", 1)[0].rstrip("/")
    if item_url != expected_service_url:
        raise MulticityGeographyError(
            "ArcGIS item service URL does not match the frozen layer candidate."
        )
    return content, payload


def _frame_from_geojson(payload: dict[str, Any], *, label: str) -> gpd.GeoDataFrame:
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise MulticityGeographyError(f"{label} returned no features.")
    frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if frame.empty or frame.crs is None:
        raise MulticityGeographyError(f"{label} is empty or lacks a CRS.")
    return frame


def _download_place(
    session: requests.Session,
    candidate: LayerCandidate,
    city: CitySpec,
) -> LayerAcquisition:
    raw_files: dict[str, bytes] = {}
    metadata_bytes, metadata = _request_bytes(
        session,
        candidate.url,
        params={"f": "json"},
        label=f"{candidate.label} place metadata",
        timeout=(15, 60),
    )
    _validate_layer_metadata(metadata, role="place")
    raw_files["layer_metadata.json"] = metadata_bytes
    item = _item_metadata(session, candidate)
    if item is not None:
        raw_files["arcgis_item_metadata.json"] = item[0]

    query_url = f"{candidate.url.rstrip('/')}/query"
    where = f"GEOID='{city.census_place_geoid}'"
    count_bytes, count_payload = _request_bytes(
        session,
        query_url,
        params={"where": where, "returnCountOnly": "true", "f": "json"},
        label=f"{candidate.label} place count",
        timeout=(15, 60),
    )
    raw_files["query_count.json"] = count_bytes
    if int(count_payload.get("count", -1)) != 1:
        raise MulticityGeographyError(
            f"Expected exactly one incorporated place for {city.id}."
        )

    feature_bytes, feature_payload = _request_bytes(
        session,
        query_url,
        params={
            "where": where,
            "outFields": COMMON_PLACE_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        },
        label=f"{candidate.label} place feature",
    )
    raw_files["features.geojson"] = feature_bytes
    frame = _frame_from_geojson(feature_payload, label=f"{city.id} place")
    if len(frame) != 1:
        raise MulticityGeographyError(
            f"Place count and feature response disagree for {city.id}."
        )
    return LayerAcquisition(candidate, frame, metadata, raw_files)


def _download_tracts(
    session: requests.Session,
    candidate: LayerCandidate,
    city: CitySpec,
    bbox_wgs84: tuple[float, float, float, float],
) -> LayerAcquisition:
    raw_files: dict[str, bytes] = {}
    metadata_bytes, metadata = _request_bytes(
        session,
        candidate.url,
        params={"f": "json"},
        label=f"{candidate.label} tract metadata",
        timeout=(15, 60),
    )
    _validate_layer_metadata(metadata, role="tract")
    raw_files["layer_metadata.json"] = metadata_bytes
    item = _item_metadata(session, candidate)
    if item is not None:
        raw_files["arcgis_item_metadata.json"] = item[0]

    query_url = f"{candidate.url.rstrip('/')}/query"
    where = f"STATE='{city.state_fips}'"
    envelope = ",".join(f"{value:.10f}" for value in bbox_wgs84)
    spatial_params: dict[str, Any] = {
        "where": where,
        "geometry": envelope,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    count_bytes, count_payload = _request_bytes(
        session,
        query_url,
        params={**spatial_params, "returnCountOnly": "true", "f": "json"},
        label=f"{candidate.label} tract count",
        timeout=(15, 60),
    )
    raw_files["query_count.json"] = count_bytes
    expected_count = int(count_payload.get("count", -1))
    if expected_count <= 0:
        raise MulticityGeographyError(f"No tract candidates were found for {city.id}.")

    page_size = min(int(metadata["maxRecordCount"]), 2000)
    frames: list[gpd.GeoDataFrame] = []
    for page_index, offset in enumerate(range(0, expected_count, page_size)):
        page_bytes, page_payload = _request_bytes(
            session,
            query_url,
            params={
                **spatial_params,
                "outFields": COMMON_TRACT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "geojson",
            },
            label=f"{candidate.label} tract page {page_index}",
        )
        raw_files[f"features_{page_index:04d}.geojson"] = page_bytes
        frames.append(
            _frame_from_geojson(
                page_payload,
                label=f"{city.id} tract page {page_index}",
            )
        )

    frame = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    if len(frame) != expected_count:
        raise MulticityGeographyError(
            f"Tract pagination returned {len(frame)} of {expected_count} features."
        )
    return LayerAcquisition(candidate, frame, metadata, raw_files)


def _attempt_record(
    candidate: LayerCandidate,
    *,
    status: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "label": candidate.label,
        "url": candidate.url,
        "status": status,
    }
    if error is not None:
        record["error_type"] = type(error).__name__
        record["error"] = str(error)[:300]
    return record


def _acquire_with_fallback(
    candidates: tuple[LayerCandidate, ...],
    *,
    unavailable_origins: set[str],
    downloader,
) -> LayerAcquisition:
    attempts: list[dict[str, Any]] = []
    last_error: LayerUnavailableError | None = None
    for candidate in candidates:
        if candidate.origin in unavailable_origins:
            attempts.append(
                _attempt_record(candidate, status="skipped_after_origin_failure")
            )
            continue
        try:
            acquisition = downloader(candidate)
        except LayerUnavailableError as exc:
            unavailable_origins.add(candidate.origin)
            attempts.append(_attempt_record(candidate, status="unavailable", error=exc))
            last_error = exc
            continue
        attempts.append(_attempt_record(candidate, status="selected"))
        return replace(acquisition, attempts=tuple(attempts))
    raise LayerUnavailableError("Every configured public layer was unavailable.") from last_error


def _normalize_code(series: pd.Series, *, width: int, label: str) -> pd.Series:
    normalized = series.astype("string").str.strip().str.zfill(width)
    if normalized.isna().any() or (normalized.str.len() != width).any():
        raise MulticityGeographyError(f"Invalid {label} code.")
    return normalized.astype(str)


def standardize_place(
    frame: gpd.GeoDataFrame,
    city: CitySpec,
) -> gpd.GeoDataFrame:
    """Validate and dissolve one configured incorporated-place feature."""

    required = set(COMMON_PLACE_FIELDS.split(",")) | {"geometry"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MulticityGeographyError(f"Place feature lacks fields: {missing}")
    place = frame.copy()
    place["GEOID"] = _normalize_code(place["GEOID"], width=7, label="place GEOID")
    place["STATE"] = _normalize_code(place["STATE"], width=2, label="state FIPS")
    place["PLACE"] = _normalize_code(place["PLACE"], width=5, label="place FIPS")
    if place["GEOID"].iloc[0] != city.census_place_geoid:
        raise MulticityGeographyError("Returned place GEOID does not match configuration.")
    if place["GEOID"].iloc[0] != place["STATE"].iloc[0] + place["PLACE"].iloc[0]:
        raise MulticityGeographyError("Place GEOID fields are internally inconsistent.")
    if place["STATE"].iloc[0] != city.state_fips:
        raise MulticityGeographyError("Returned place belongs to the wrong state.")
    if str(place["BASENAME"].iloc[0]).strip().casefold() != city.name.casefold():
        raise MulticityGeographyError("Returned place name does not match configuration.")
    if str(place["FUNCSTAT"].iloc[0]).strip() != "A":
        raise MulticityGeographyError("Configured incorporated place is not active.")

    valid_parts = place.geometry.map(shapely.make_valid)
    geometry = shapely.make_valid(valid_parts.union_all())
    if geometry is None or geometry.is_empty or not geometry.is_valid:
        raise MulticityGeographyError("Incorporated-place geometry is invalid.")
    source_land_area = int(str(place["AREALAND"].iloc[0]).strip())
    return gpd.GeoDataFrame(
        {
            "city_id": [city.id],
            "city_name": [city.name],
            "census_place_geoid": [city.census_place_geoid],
            "state_fips": [city.state_fips],
            "source_name": [str(place["NAME"].iloc[0]).strip()],
            "source_funcstat": ["A"],
            "source_land_area_m2": [source_land_area],
        },
        geometry=[geometry],
        crs=place.crs,
    ).to_crs("EPSG:4326")


def standardize_tracts(
    frame: gpd.GeoDataFrame,
    city: CitySpec,
) -> gpd.GeoDataFrame:
    """Validate Census tract identity fields without reading demographic values."""

    required = set(COMMON_TRACT_FIELDS.split(",")) | {"geometry"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MulticityGeographyError(f"Tract features lack fields: {missing}")
    tracts = frame.copy()
    tracts["GEOID"] = _normalize_code(tracts["GEOID"], width=11, label="tract GEOID")
    tracts["STATE"] = _normalize_code(tracts["STATE"], width=2, label="state FIPS")
    tracts["COUNTY"] = _normalize_code(
        tracts["COUNTY"], width=3, label="county FIPS"
    )
    tracts["TRACT"] = _normalize_code(tracts["TRACT"], width=6, label="tract code")
    expected_geoid = tracts["STATE"] + tracts["COUNTY"] + tracts["TRACT"]
    if not tracts["GEOID"].equals(expected_geoid):
        raise MulticityGeographyError("Tract GEOID fields are internally inconsistent.")
    if set(tracts["STATE"]) != {city.state_fips}:
        raise MulticityGeographyError("Tract query returned a feature from another state.")
    if tracts["GEOID"].duplicated().any() or tracts["OBJECTID"].duplicated().any():
        raise MulticityGeographyError("Tract response contains duplicate identifiers.")
    tracts["geometry"] = tracts.geometry.map(shapely.make_valid)
    invalid = (
        tracts.geometry.isna()
        | tracts.geometry.is_empty
        | ~tracts.geometry.is_valid
    )
    if invalid.any():
        raise MulticityGeographyError("Tract response contains unusable geometry.")
    tracts["AREALAND"] = pd.to_numeric(tracts["AREALAND"], errors="raise").astype(
        "int64"
    )
    return tracts.sort_values("GEOID").reset_index(drop=True)


def _geometry_sha256(geometry) -> str:
    return hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()


def select_city_tracts(
    city_boundary: gpd.GeoDataFrame,
    tracts: gpd.GeoDataFrame,
    *,
    city_id: str,
    analysis_crs: str,
    minimum_place_area_fraction: float,
    exclude_special_use_tracts: bool,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Apply the target-independent area and special-use selection rule."""

    if not 0 < minimum_place_area_fraction <= 1:
        raise MulticityGeographyError("Place-area threshold must be in (0, 1].")
    if city_boundary.empty or city_boundary.crs is None:
        raise MulticityGeographyError("A georeferenced city boundary is required.")
    if tracts.empty or tracts.crs is None:
        raise MulticityGeographyError("Georeferenced tract candidates are required.")

    projected = tracts.to_crs(analysis_crs).copy()
    city_geometry = shapely.make_valid(
        city_boundary.to_crs(analysis_crs).geometry.union_all()
    )
    original_area = projected.geometry.area.to_numpy(dtype=float)
    if np.any(~np.isfinite(original_area)) or np.any(original_area <= 0):
        raise MulticityGeographyError("Tract candidates have non-positive area.")
    intersections = projected.geometry.intersection(city_geometry)
    overlap_area = intersections.area.to_numpy(dtype=float)
    fractions = np.divide(overlap_area, original_area)
    if np.any(fractions < -1e-9) or np.any(fractions > 1 + 1e-9):
        raise MulticityGeographyError("Place-overlap fractions exceed numeric tolerance.")
    fractions = np.clip(fractions, 0.0, 1.0)
    meets_threshold = fractions >= minimum_place_area_fraction
    special_use = projected["TRACT"].str.startswith("98").to_numpy()
    primary_included = meets_threshold & (
        ~special_use if exclude_special_use_tracts else True
    )

    reasons = np.full(len(projected), "included", dtype=object)
    reasons[overlap_area <= 0] = "no_place_overlap"
    reasons[(overlap_area > 0) & ~meets_threshold] = "below_place_area_threshold"
    if exclude_special_use_tracts:
        reasons[meets_threshold & special_use] = "census_special_use_98xxxx"

    projected["city_id"] = city_id
    projected["tract_geoid"] = projected["GEOID"]
    projected["state_fips"] = projected["STATE"]
    projected["county_fips"] = projected["COUNTY"]
    projected["tract_code"] = projected["TRACT"]
    projected["tract_name"] = projected["NAME"].astype(str).str.strip()
    projected["tract_basename"] = projected["BASENAME"].astype(str).str.strip()
    projected["source_funcstat"] = projected["FUNCSTAT"].astype(str).str.strip()
    projected["source_land_area_m2"] = projected["AREALAND"].astype("int64")
    projected["original_area_m2"] = original_area
    projected["place_overlap_area_m2"] = overlap_area
    projected["place_area_fraction"] = fractions
    projected["special_use_tract"] = special_use
    projected["primary_included"] = primary_included
    projected["primary_exclusion_reason"] = reasons
    projected["original_geometry_sha256"] = projected.geometry.map(
        _geometry_sha256
    )

    clipped_by_geoid = dict(
        zip(projected["tract_geoid"], intersections, strict=True)
    )
    candidate_columns = [
        "city_id",
        "tract_geoid",
        "state_fips",
        "county_fips",
        "tract_code",
        "tract_name",
        "tract_basename",
        "source_funcstat",
        "source_land_area_m2",
        "original_area_m2",
        "place_overlap_area_m2",
        "place_area_fraction",
        "special_use_tract",
        "primary_included",
        "primary_exclusion_reason",
        "original_geometry_sha256",
        "geometry",
    ]
    candidates = projected[candidate_columns].sort_values("tract_geoid").reset_index(
        drop=True
    )
    primary = candidates.loc[candidates["primary_included"]].copy()
    primary["geometry"] = [
        clipped_by_geoid[geoid] for geoid in primary["tract_geoid"]
    ]
    primary["geometry_sha256"] = primary.geometry.map(_geometry_sha256)
    primary = primary.drop(
        columns=["original_geometry_sha256", "primary_exclusion_reason"]
    ).reset_index(drop=True)
    if primary.empty or primary.geometry.is_empty.any():
        raise MulticityGeographyError("The primary city-tract universe is empty.")
    if primary["tract_geoid"].duplicated().any():
        raise MulticityGeographyError("Primary tract GEOIDs are not unique.")
    return candidates, primary


def _layer_candidates(plan: MulticityPlan, *, role: str) -> tuple[LayerCandidate, ...]:
    sources = plan.raw["sources"]
    return (
        LayerCandidate(
            label=f"census_tigerweb_{role}",
            url=str(sources[f"census_{role}_layer"]),
            provider="U.S. Census Bureau",
            source_status="authoritative_primary",
        ),
        LayerCandidate(
            label=f"esri_demographics_census2020_{role}_pilot_mirror",
            url=str(sources[f"census_{role}_pilot_mirror_layer"]),
            item_id=str(sources[f"census_{role}_pilot_mirror_item"]),
            provider="Esri Demographics",
            source_status="pilot_mirror_not_protocol_frozen",
        ),
    )


def _atomic_bytes(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_bytes(content)
    temporary.replace(destination)


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root).as_posix()


def _source_manifest(acquisition: LayerAcquisition) -> dict[str, Any]:
    return {
        "selected": {
            "label": acquisition.candidate.label,
            "url": acquisition.candidate.url,
            "provider": acquisition.candidate.provider,
            "source_status": acquisition.candidate.source_status,
            "item_id": acquisition.candidate.item_id,
            "layer_name": acquisition.metadata.get("name"),
            "max_record_count": int(acquisition.metadata["maxRecordCount"]),
        },
        "attempts": list(acquisition.attempts),
    }


def _code_paths(project_root: Path, city: CitySpec) -> tuple[str, ...]:
    return (
        "configs/multicity/experiment.toml",
        _relative(project_root, city.config_path),
        "scripts/stage_multicity_geography.py",
        "src/la_heat/multicity/config.py",
        "src/la_heat/multicity/geography.py",
        "src/la_heat/multicity/workspace.py",
        "src/la_heat/provenance.py",
    )


def _verify_file_records(
    project_root: Path,
    records: dict[str, dict[str, Any]],
) -> None:
    for relative, record in records.items():
        path = project_root / relative
        if not path.is_file():
            raise MulticityGeographyError(f"Geography artifact is missing: {relative}")
        if sha256_file(path) != record["sha256"]:
            raise MulticityGeographyError(f"Geography artifact hash changed: {relative}")
        if path.stat().st_size != int(record["bytes"]):
            raise MulticityGeographyError(f"Geography artifact size changed: {relative}")


def verify_city_geography(
    config_path: str | Path,
    city_id: str,
) -> dict[str, Any]:
    """Authenticate a completed metadata-only geography snapshot."""

    plan = load_multicity_plan(config_path)
    city = next((item for item in plan.cities if item.id == city_id), None)
    if city is None:
        raise MulticityGeographyError(f"Unknown city id: {city_id}")
    workspace = MulticityWorkspace.from_plan(plan)
    manifest_path = workspace.city(city_id).manifests / "geography" / "GEOGRAPHY.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_commit = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if recorded_commit != canonical_sha256(body):
        raise MulticityGeographyError("Geography manifest commit is invalid.")
    if payload.get("state") != "pilot_complete_source_not_protocol_locked":
        raise MulticityGeographyError("Geography manifest is not in its complete pilot state.")
    if payload.get("city", {}).get("id") != city_id:
        raise MulticityGeographyError("Geography manifest city identity changed.")
    if payload.get("plan_semantic_sha256") != plan.semantic_sha256:
        raise MulticityGeographyError("Geography manifest no longer matches the plan.")
    target_access = payload.get("target_access", {})
    if any(bool(value) for value in target_access.values()):
        raise MulticityGeographyError("A metadata-only manifest records target access.")

    _verify_file_records(workspace.project_root, payload["raw_files"])
    _verify_file_records(workspace.project_root, payload["outputs"])
    frames = {
        name: gpd.read_parquet(workspace.project_root / record["path"])
        for name, record in payload["output_tables"].items()
    }
    for name, frame in frames.items():
        record = payload["output_tables"][name]
        if len(frame) != int(record["rows"]):
            raise MulticityGeographyError(f"{name} row count changed.")
        if geometry_semantic_sha256(frame) != record["geometry_semantic_sha256"]:
            raise MulticityGeographyError(f"{name} geometry semantics changed.")

    code_hash, _ = code_runtime_fingerprint(
        project_root=workspace.project_root,
        relative_paths=tuple(payload["code_runtime"]["relative_paths"]),
        algorithm_version=payload["code_runtime"]["algorithm_version"],
    )
    if code_hash != payload["code_runtime"]["sha256"]:
        raise MulticityGeographyError("Geography code/runtime fingerprint changed.")
    return payload


def stage_city_geography(
    config_path: str | Path,
    city_id: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Stage one authorized city using no thermal, target-QA, or outcome values."""

    plan = load_multicity_plan(config_path)
    locks = plan.raw["locks"]
    if not locks["allow_boundary_metadata_staging"]:
        raise MulticityGeographyError("Boundary metadata staging is locked.")
    if city_id not in locks["authorized_metadata_city_ids"]:
        raise MulticityGeographyError(
            f"The current draft does not authorize metadata staging for {city_id}."
        )
    if (
        locks["external_targets_unlocked"]
        or locks["external_target_values_read"]
        or locks["allow_external_target_access"]
    ):
        raise MulticityGeographyError("External target locks are not intact.")
    city = next((item for item in plan.cities if item.id == city_id), None)
    if city is None:
        raise MulticityGeographyError(f"Unknown city id: {city_id}")

    workspace = MulticityWorkspace.from_plan(plan)
    city_workspace = workspace.city(city_id)
    manifest_path = city_workspace.manifests / "geography" / "GEOGRAPHY.json"
    if manifest_path.exists():
        return verify_city_geography(config_path, city_id)

    active_session = session or _retrying_session()
    unavailable_origins: set[str] = set()
    place = _acquire_with_fallback(
        _layer_candidates(plan, role="place"),
        unavailable_origins=unavailable_origins,
        downloader=lambda candidate: _download_place(active_session, candidate, city),
    )
    boundary = standardize_place(place.frame, city)
    bbox = tuple(float(value) for value in boundary.total_bounds)
    tracts = _acquire_with_fallback(
        _layer_candidates(plan, role="tract"),
        unavailable_origins=unavailable_origins,
        downloader=lambda candidate: _download_tracts(
            active_session,
            candidate,
            city,
            bbox,
        ),
    )
    tract_frame = standardize_tracts(tracts.frame, city)
    candidates, primary = select_city_tracts(
        boundary,
        tract_frame,
        city_id=city.id,
        analysis_crs=str(plan.raw["experiment"]["analysis_crs"]),
        minimum_place_area_fraction=float(
            plan.raw["target"]["minimum_place_area_fraction"]
        ),
        exclude_special_use_tracts=bool(
            plan.raw["target"]["exclude_special_use_tracts"]
        ),
    )

    raw_root = city_workspace.raw / "geography"
    raw_paths: dict[str, Path] = {}
    for role, acquisition in (("place", place), ("tract", tracts)):
        for name, content in acquisition.raw_files.items():
            destination = raw_root / role / name
            _atomic_bytes(content, destination)
            raw_paths[f"{role}/{name}"] = destination

    processed_root = city_workspace.processed / "geography"
    output_frames = {
        "city_boundary": boundary,
        "tract_candidates": candidates,
        "primary_tracts": primary,
    }
    output_paths = {
        "city_boundary": processed_root / "city_boundary.parquet",
        "tract_candidates": processed_root / "tract_candidates.parquet",
        "primary_tracts": processed_root / "primary_tracts.parquet",
    }
    for name, frame in output_frames.items():
        atomic_parquet(frame, output_paths[name])

    raw_records = {
        _relative(workspace.project_root, path): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(raw_paths.values())
    }
    output_records = {
        _relative(workspace.project_root, path): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in output_paths.values()
    }
    output_tables: dict[str, dict[str, Any]] = {}
    for name, path in output_paths.items():
        frame = output_frames[name]
        record = parquet_file_record(path, frame)
        record["path"] = _relative(workspace.project_root, path)
        record["geometry_semantic_sha256"] = geometry_semantic_sha256(frame)
        output_tables[name] = record

    analysis_boundary = boundary.to_crs(str(plan.raw["experiment"]["analysis_crs"]))
    code_paths = _code_paths(workspace.project_root, city)
    code_hash, code_payload = code_runtime_fingerprint(
        project_root=workspace.project_root,
        relative_paths=code_paths,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_payload["relative_paths"] = list(code_paths)
    code_payload["sha256"] = code_hash
    positive_overlap = candidates["place_overlap_area_m2"] > 0
    meets_threshold = (
        candidates["place_area_fraction"]
        >= float(plan.raw["target"]["minimum_place_area_fraction"])
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "state": "pilot_complete_source_not_protocol_locked",
        "stage": "boundary_and_public_metadata_only",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "city": {
            "id": city.id,
            "name": city.name,
            "state_fips": city.state_fips,
            "census_place_geoid": city.census_place_geoid,
            "role": city.role,
            "target_values_status": city.target_values_status,
        },
        "source_lock_status": "pilot_snapshot_not_protocol_lock",
        "mirror_vertex_contract": plan.raw["sources"][
            "census_pilot_mirror_vertex_contract"
        ],
        "sources": {
            "place": _source_manifest(place),
            "tract": _source_manifest(tracts),
        },
        "selection_contract": {
            "analysis_crs": str(plan.raw["experiment"]["analysis_crs"]),
            "minimum_place_area_fraction": float(
                plan.raw["target"]["minimum_place_area_fraction"]
            ),
            "exclude_special_use_tracts": bool(
                plan.raw["target"]["exclude_special_use_tracts"]
            ),
            "special_use_prefix": "98",
            "uses_target_or_demographic_values": False,
        },
        "geography": {
            "bbox_wgs84": [round(value, 10) for value in bbox],
            "bbox_analysis_m": [
                round(float(value), 3) for value in analysis_boundary.total_bounds
            ],
            "city_area_m2": round(float(analysis_boundary.geometry.area.iloc[0]), 3),
            "source_declared_land_area_m2": int(
                boundary["source_land_area_m2"].iloc[0]
            ),
            "county_fips": sorted(primary["county_fips"].unique().tolist()),
            "tract_candidates_in_bbox": len(candidates),
            "tract_candidates_with_positive_overlap": int(positive_overlap.sum()),
            "tracts_meeting_area_threshold": int(meets_threshold.sum()),
            "special_use_candidates_meeting_threshold": int(
                (meets_threshold & candidates["special_use_tract"]).sum()
            ),
            "primary_tract_count": len(primary),
        },
        "target_access": {
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_lst_values_read": False,
            "model_predictions_computed": False,
            "model_fit_performed": False,
            "predictor_construction_performed": False,
        },
        "raw_files": raw_records,
        "outputs": output_records,
        "output_tables": output_tables,
        "code_runtime": code_payload,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, manifest_path)
    return verify_city_geography(config_path, city_id)
