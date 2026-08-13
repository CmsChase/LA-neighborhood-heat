"""Target-blind feasibility audit for the proposed M3 unseen-city cohort.

The audit may read Census geometry, ESA WorldCover land-cover classes, and a
strict allow-list of Landsat STAC metadata.  It never requests a Landsat asset
object, asset URL, thermal band, QA band, target table, model, or metric.
"""

from __future__ import annotations

import json
import tomllib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.warp import transform_bounds
from shapely.geometry import box

from la_heat.grid import build_fixed_grid
from la_heat.inventory import SceneRecord, group_physical_overpasses
from la_heat.multicity import geography as census_geography
from la_heat.multicity.config import CitySpec
from la_heat.multicity.missing_support_calibration_evidence_v1 import (
    EvidenceConfig,
    read_json_with_commit,
    write_manifest_no_clobber,
)
from la_heat.multicity.source_footprints import (
    LANDSAT_FIELDS,
    LANDSAT_PROPERTIES,
    _retrying_session,
    build_optical_item_table,
    fetch_public_stac_metadata,
    local_date_interval_to_utc,
)
from la_heat.multicity.worldcover_eligible_support_evidence_v1 import (
    WORLD_COVER_CLASSES,
    _BoundedClient,
    _mosaic_to_grid,
    _search_items,
    _support_table,
    _validate_items,
    _zones,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "next-experiment-metadata-feasibility-v1"
COMPLETE_STATE: Final = "metadata_only_feasibility_complete"
DEFAULT_CONFIG: Final = Path("configs/multicity/next_experiment_feasibility.toml")
PRIMARY_TIER: Final = "primary"
REPLACEMENT_TIER: Final = "replacement"

ACCESS_CONTRACT: Final = {
    "public_census_geometry_read": True,
    "worldcover_static_support_values_read": True,
    "worldcover_stac_item_assets_read": True,
    "worldcover_unsigned_map_asset_hrefs_read": True,
    "worldcover_unsigned_map_asset_hrefs_persisted_in_ignored_raw_evidence": True,
    "worldcover_map_asset_http_requests_allowed_on_cache_miss": True,
    "worldcover_sas_token_requests_allowed_on_azure_cache_miss": True,
    "worldcover_signed_map_asset_urls_persisted": False,
    "landsat_stac_metadata_read": True,
    "landsat_stac_fields_excluded_assets": True,
    "landsat_stac_item_assets_returned": False,
    "new_candidate_landsat_asset_hrefs_read": False,
    "new_candidate_landsat_asset_sign_calls": 0,
    "new_candidate_landsat_asset_http_requests": 0,
    "new_candidate_thermal_values_read": False,
    "new_candidate_target_qa_values_read": False,
    "new_candidate_target_tables_read": False,
    "new_candidate_values_opened_marker_created": False,
    "predictor_construction_performed": False,
    "model_fit_or_prediction_performed": False,
    "evaluation_metrics_computed": False,
    "target_authorization_created": False,
}

CODE_PATHS: Final = (
    "configs/multicity/next_experiment_feasibility.toml",
    "docs/NEXT_EXPERIMENT_PREREGISTRATION_DRAFT.md",
    "scripts/audit_next_experiment_city_feasibility.py",
    "src/la_heat/grid.py",
    "src/la_heat/inventory.py",
    "src/la_heat/multicity/geography.py",
    "src/la_heat/multicity/next_experiment_feasibility.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py",
    "src/la_heat/provenance.py",
)


class FeasibilityAuditError(RuntimeError):
    """Raised when the target-blind audit cannot be run or authenticated."""


@dataclass(frozen=True, slots=True)
class CandidateCity:
    id: str
    name: str
    state_fips: str
    census_place_geoid: str
    timezone: str
    target_grid_crs: str
    tier: str
    replacement_order: int | None

    def as_city_spec(self, config_path: Path) -> CitySpec:
        return CitySpec(
            id=self.id,
            name=self.name,
            state_fips=self.state_fips,
            census_place_geoid=self.census_place_geoid,
            timezone=self.timezone,
            target_grid_crs=self.target_grid_crs,
            role="external_confirmation",
            target_values_status="sealed",
            config_path=config_path,
        )


@dataclass(frozen=True, slots=True)
class FeasibilityConfig:
    project_root: Path
    path: Path
    raw: dict[str, Any]
    cities: tuple[CandidateCity, ...]

    def project_path(self, value: str) -> Path:
        candidate = (self.project_root / value).resolve()
        if not candidate.is_relative_to(self.project_root):
            raise FeasibilityAuditError(f"Configured path escapes project root: {value}")
        return candidate

    @property
    def primary(self) -> tuple[CandidateCity, ...]:
        return tuple(city for city in self.cities if city.tier == PRIMARY_TIER)

    @property
    def replacements(self) -> tuple[CandidateCity, ...]:
        return tuple(
            sorted(
                (city for city in self.cities if city.tier == REPLACEMENT_TIER),
                key=lambda city: int(city.replacement_order or 0),
            )
        )


def _required_sections(raw: Mapping[str, Any]) -> None:
    expected = {"experiment", "window", "census", "worldcover", "landsat", "paths", "cities"}
    if set(raw) != expected:
        raise FeasibilityAuditError("Feasibility configuration sections changed.")


def load_feasibility_config(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> FeasibilityConfig:
    root = Path(project_root).resolve()
    path = Path(config_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    _required_sections(raw)
    experiment = raw["experiment"]
    if (
        experiment.get("id") != "multicity_m3_level_anomaly_v1"
        or experiment.get("audit_class") != "target_blind_label_free_city_feasibility"
        or experiment.get("target_blind") is not True
        or experiment.get("label_free") is not True
        or experiment.get("target_access_authorized") is not False
    ):
        raise FeasibilityAuditError("Experiment identity or target-blind boundary changed.")
    window = raw["window"]
    if window != {"start_date": "2025-03-01", "end_date": "2025-11-30"}:
        raise FeasibilityAuditError("The complete 2025 March-November window changed.")
    landsat = raw["landsat"]
    expected_landsat = {
        "collection": "landsat-c2-l2",
        "platforms": ["landsat-8", "landsat-9"],
        "collection_category": "T1",
        "processing_level": "L2SP",
        "metadata_only": True,
        "physical_time_tolerance_minutes": 15,
        "minimum_union_coverage_fraction": 0.98,
        "minimum_unique_physical_dates": 16,
    }
    if any(landsat.get(key) != value for key, value in expected_landsat.items()):
        raise FeasibilityAuditError("Landsat metadata feasibility contract changed.")
    census = raw["census"]
    if (
        float(census.get("minimum_place_area_fraction", -1)) != 0.5
        or census.get("exclude_special_use_tracts") is not True
        or census.get("special_use_tract_prefix") != "98"
    ):
        raise FeasibilityAuditError("Census tract-selection contract changed.")
    worldcover = raw["worldcover"]
    if (
        worldcover.get("collection") != "esa-worldcover"
        or int(worldcover.get("product_year", -1)) != 2020
        or worldcover.get("product_version") != "v100"
        or worldcover.get("asset") != "map"
        or list(worldcover.get("exclude_classes", [])) != [0, 80]
    ):
        raise FeasibilityAuditError("WorldCover support contract changed.")

    records = raw["cities"]
    if not isinstance(records, list):
        raise FeasibilityAuditError("Candidate cities must be an ordered array.")
    cities = tuple(
        CandidateCity(
            id=str(value["id"]),
            name=str(value["name"]),
            state_fips=str(value["state_fips"]),
            census_place_geoid=str(value["census_place_geoid"]),
            timezone=str(value["timezone"]),
            target_grid_crs=str(value["target_grid_crs"]),
            tier=str(value["tier"]),
            replacement_order=(
                None if value.get("replacement_order") is None else int(value["replacement_order"])
            ),
        )
        for value in records
    )
    primary_ids = [city.id for city in cities if city.tier == PRIMARY_TIER]
    replacement_ids = [city.id for city in cities if city.tier == REPLACEMENT_TIER]
    if primary_ids != ["seattle_wa", "denver_co", "atlanta_ga", "miami_fl"]:
        raise FeasibilityAuditError("Primary candidate order changed.")
    if replacement_ids != ["dallas_tx", "minneapolis_mn", "portland_or", "baltimore_md"]:
        raise FeasibilityAuditError("Replacement candidate order changed.")
    if [city.replacement_order for city in cities if city.tier == REPLACEMENT_TIER] != [1, 2, 3, 4]:
        raise FeasibilityAuditError("Replacement priority changed.")
    if len({city.id for city in cities}) != 8:
        raise FeasibilityAuditError("Candidate city identities are not unique.")
    return FeasibilityConfig(root, path, raw, cities)


def _relative(config: FeasibilityConfig, path: Path) -> str:
    return path.resolve().relative_to(config.project_root).as_posix()


def _file_record(config: FeasibilityConfig, path: Path) -> dict[str, Any]:
    return {
        "path": _relative(config, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _table_record(
    config: FeasibilityConfig,
    path: Path,
    frame: pd.DataFrame,
    *,
    geometry: bool = False,
) -> dict[str, Any]:
    record = {**_file_record(config, path), **parquet_file_record(path, frame)}
    if geometry:
        record["geometry_semantic_sha256"] = geometry_semantic_sha256(frame)
    else:
        record["semantic_sha256"] = canonical_frame_sha256(frame, sort_by=list(frame.columns[:1]))
    return record


def _write_bytes(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    temporary.write_bytes(content)
    temporary.replace(destination)


def _write_status(
    config: FeasibilityConfig,
    *,
    state: str,
    current_city_id: str | None,
    current_step: str,
    completed: Sequence[str],
    selected: Sequence[str],
    error: BaseException | None = None,
) -> None:
    status_path = config.project_path(str(config.raw["paths"]["status"]))
    payload = {
        "state": state,
        "current_city_id": current_city_id,
        "current_step": current_step,
        "completed_city_count": len(completed),
        "completed_city_ids": list(completed),
        "selected_city_count": len(selected),
        "selected_city_ids": list(selected),
        "last_error_type": None if error is None else type(error).__name__,
        "last_error": None if error is None else str(error)[:500],
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(payload, status_path)


def _code_identity(config: FeasibilityConfig) -> dict[str, dict[str, Any]]:
    return {
        path: {
            "bytes": (config.project_root / path).stat().st_size,
            "sha256": sha256_file(config.project_root / path),
        }
        for path in CODE_PATHS
    }


def _census_candidates(config: FeasibilityConfig, *, role: str) -> tuple[Any, ...]:
    census = config.raw["census"]
    return (
        census_geography.LayerCandidate(
            label=f"census_tigerweb_{role}",
            url=str(census[f"{role}_layer_url"]),
            provider="U.S. Census Bureau",
            source_status="authoritative_primary",
        ),
        census_geography.LayerCandidate(
            label=f"esri_census2020_{role}_mirror",
            url=str(census[f"{role}_mirror_layer_url"]),
            provider="Esri Demographics",
            source_status="public_mirror_same_census_vintage",
            item_id=str(census[f"{role}_mirror_item"]),
        ),
    )


def _stage_census(
    config: FeasibilityConfig,
    city: CandidateCity,
    session: requests.Session,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, Any]]:
    city_spec = city.as_city_spec(config.path)
    unavailable: set[str] = set()
    place = census_geography._acquire_with_fallback(
        _census_candidates(config, role="place"),
        unavailable_origins=unavailable,
        downloader=lambda candidate: census_geography._download_place(
            session, candidate, city_spec
        ),
    )
    boundary = census_geography.standardize_place(place.frame, city_spec)
    bbox = tuple(float(value) for value in boundary.total_bounds)
    tract = census_geography._acquire_with_fallback(
        _census_candidates(config, role="tract"),
        unavailable_origins=unavailable,
        downloader=lambda candidate: census_geography._download_tracts(
            session, candidate, city_spec, bbox
        ),
    )
    standardized = census_geography.standardize_tracts(tract.frame, city_spec)
    candidates, primary = census_geography.select_city_tracts(
        boundary,
        standardized,
        city_id=city.id,
        analysis_crs=str(config.raw["experiment"]["analysis_crs"]),
        minimum_place_area_fraction=float(
            config.raw["census"]["minimum_place_area_fraction"]
        ),
        exclude_special_use_tracts=True,
    )
    root = config.project_path(str(config.raw["paths"]["cache_root"])) / city.id
    raw_records: dict[str, Any] = {}
    for role, acquisition in (("place", place), ("tract", tract)):
        for name, content in acquisition.raw_files.items():
            path = root / "census" / "raw" / role / name
            _write_bytes(content, path)
            raw_records[_relative(config, path)] = _file_record(config, path)
    outputs = {
        "city_boundary": root / "census" / "city_boundary.parquet",
        "tract_candidates": root / "census" / "tract_candidates.parquet",
        "primary_tracts": root / "census" / "primary_tracts.parquet",
    }
    for name, frame in (
        ("city_boundary", boundary),
        ("tract_candidates", candidates),
        ("primary_tracts", primary),
    ):
        atomic_parquet(frame, outputs[name])
    summary = {
        "passed": True,
        "place_source": place.candidate.label,
        "tract_source": tract.candidate.label,
        "bbox_wgs84": list(bbox),
        "tract_candidate_count": len(candidates),
        "primary_tract_count": len(primary),
        "primary_geoid_unique": not primary["tract_geoid"].duplicated().any(),
        "primary_geometry_valid": bool(
            (~primary.geometry.is_empty & primary.geometry.is_valid).all()
        ),
        "outputs": {
            name: _table_record(config, path, frame, geometry=True)
            for (name, frame), path in zip(
                (
                    ("city_boundary", boundary),
                    ("tract_candidates", candidates),
                    ("primary_tracts", primary),
                ),
                outputs.values(),
                strict=True,
            )
        },
        "raw_responses": raw_records,
    }
    return boundary, primary, summary


def _stage_landsat_metadata(
    config: FeasibilityConfig,
    city: CandidateCity,
    boundary: gpd.GeoDataFrame,
    session: requests.Session,
) -> tuple[dict[str, Any], list[str]]:
    landsat = config.raw["landsat"]
    start = date.fromisoformat(str(config.raw["window"]["start_date"]))
    end = date.fromisoformat(str(config.raw["window"]["end_date"]))
    interval = local_date_interval_to_utc(start, end, city.timezone)
    query = {
        "landsat:collection_category": {"eq": str(landsat["collection_category"])},
        "landsat:correction": {"eq": str(landsat["processing_level"])},
    }
    features, pages, request_summary = fetch_public_stac_metadata(
        session,
        api=str(landsat["stac_api"]),
        collection=str(landsat["collection"]),
        bbox_wgs84=[float(value) for value in boundary.total_bounds],
        datetime_interval=interval,
        fields=LANDSAT_FIELDS,
        properties=LANDSAT_PROPERTIES,
        page_limit=int(landsat["page_limit"]),
        query=query,
    )
    items = build_optical_item_table(
        features,
        source="landsat_wrs",
        collection=str(landsat["collection"]),
        expected_properties=LANDSAT_PROPERTIES,
        allowed_platforms=tuple(str(value) for value in landsat["platforms"]),
        local_start_date=start,
        local_end_date=end,
        timezone=city.timezone,
        city_boundary=boundary,
        analysis_crs=str(config.raw["experiment"]["analysis_crs"]),
    )
    scenes = [
        SceneRecord(
            item_id=str(row.item_id),
            platform=str(row.platform),
            acquired_utc=datetime.fromisoformat(str(row.acquired_utc).replace("Z", "+00:00")),
            local_date=str(row.acquisition_local_date),
            wrs_path=str(row.wrs_path),
            wrs_row=str(row.wrs_row),
            cloud_cover_percent=float("nan"),
            city_coverage_fraction=float(row.city_overlap_fraction),
            geometry_wgs84=row.geometry,
            asset_hrefs={},
        )
        for row in items.itertuples(index=False)
    ]
    records = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=boundary.to_crs("EPSG:4326").geometry.union_all(),
        analysis_crs=str(config.raw["experiment"]["analysis_crs"]),
        maximum_time_gap_minutes=int(landsat["physical_time_tolerance_minutes"]),
    )
    overpasses = pd.DataFrame(asdict(record) for record in records)
    if not overpasses.empty:
        overpasses["scene_ids"] = overpasses["scene_ids"].map("|".join)
        overpasses["wrs_path_rows"] = overpasses["wrs_path_rows"].map("|".join)
    overpasses, dates, passes = eligible_physical_overpasses(
        overpasses,
        minimum_union_coverage_fraction=float(
            landsat["minimum_union_coverage_fraction"]
        ),
        minimum_unique_physical_dates=int(landsat["minimum_unique_physical_dates"]),
    )
    root = config.project_path(str(config.raw["paths"]["cache_root"])) / city.id
    raw_records: dict[str, Any] = {}
    for index, page in enumerate(pages, start=1):
        path = root / "landsat" / "raw" / f"stac_page_{index:03d}.json"
        _write_bytes(json.dumps(page, indent=2).encode("utf-8"), path)
        raw_records[_relative(config, path)] = _file_record(config, path)
    item_path = root / "landsat" / "landsat_metadata_items.parquet"
    overpass_path = root / "landsat" / "physical_overpasses.parquet"
    atomic_parquet(items, item_path)
    atomic_parquet(overpasses, overpass_path)
    minimum = int(landsat["minimum_unique_physical_dates"])
    summary = {
        "passed": passes,
        "failure_codes": [] if passes else ["insufficient_landsat_physical_dates"],
        "query": request_summary,
        "metadata_item_count": len(items),
        "physical_overpass_count": len(overpasses),
        "eligible_unique_physical_date_count": len(dates),
        "eligible_unique_physical_dates": dates,
        "minimum_required": minimum,
        "minimum_union_coverage_fraction": float(
            landsat["minimum_union_coverage_fraction"]
        ),
        "ambiguous_overpass_count": (
            0 if overpasses.empty else int(overpasses["ambiguous_local_date"].sum())
        ),
        "platform_item_counts": {
            platform: int(count)
            for platform, count in items["platform"].value_counts().sort_index().items()
        },
        "outputs": {
            "metadata_items": _table_record(config, item_path, items, geometry=True),
            "physical_overpasses": _table_record(config, overpass_path, overpasses),
        },
        "raw_responses": raw_records,
        "access_contract": {
            "stac_fields_excluded_assets": True,
            "stac_item_assets_returned": False,
            "landsat_asset_hrefs_read": False,
            "landsat_target_or_qa_values_read": False,
        },
    }
    return summary, dates


def eligible_physical_overpasses(
    overpasses: pd.DataFrame,
    *,
    minimum_union_coverage_fraction: float,
    minimum_unique_physical_dates: int,
) -> tuple[pd.DataFrame, list[str], bool]:
    """Apply the frozen coverage, ambiguity, and independent-date gate."""

    if not 0 < minimum_union_coverage_fraction <= 1:
        raise FeasibilityAuditError("Landsat coverage threshold must be in (0, 1].")
    if minimum_unique_physical_dates <= 0:
        raise FeasibilityAuditError("Landsat physical-date threshold must be positive.")
    result = overpasses.copy()
    required = {"union_city_coverage_fraction", "ambiguous_local_date", "local_date"}
    if result.empty:
        for column in required | {"primary_eligible"}:
            if column not in result:
                result[column] = pd.Series(dtype="object")
        return result, [], False
    if not required.issubset(result):
        raise FeasibilityAuditError("Physical-overpass table lacks gate columns.")
    result["primary_eligible"] = (
        pd.to_numeric(result["union_city_coverage_fraction"], errors="raise").ge(
            minimum_union_coverage_fraction
        )
        & ~result["ambiguous_local_date"].astype(bool)
    )
    eligible = result.loc[result["primary_eligible"]].copy()
    dates = sorted(eligible["local_date"].astype(str).unique())
    return result, dates, len(dates) >= minimum_unique_physical_dates


def worldcover_eligible_counts(
    zones: np.ndarray,
    classes: np.ndarray,
    *,
    tract_count: int,
) -> np.ndarray:
    """Count fixed 30 m non-water WorldCover cells for every tract zone."""

    if zones.shape != classes.shape or tract_count <= 0:
        raise FeasibilityAuditError("WorldCover grid or tract count is invalid.")
    observed_classes = {int(value) for value in np.unique(classes)}
    if not observed_classes.issubset(WORLD_COVER_CLASSES):
        unexpected = sorted(observed_classes.difference(WORLD_COVER_CLASSES))
        raise FeasibilityAuditError(
            f"WorldCover mosaic contains unexpected classes: {unexpected}"
        )
    eligible = (zones > 0) & (classes != 0) & (classes != 80)
    return np.bincount(zones[eligible], minlength=tract_count + 1)[1 : tract_count + 1]


def _worldcover_evidence_config(config: FeasibilityConfig) -> EvidenceConfig:
    cache_root = str(config.raw["paths"]["cache_root"])
    raw = {
        "outputs": {"raw_stage_directory": cache_root},
        "worldcover": {
            "provider": config.raw["worldcover"]["provider"],
            "stac_api": config.raw["worldcover"]["stac_api"],
            "stac_collection": config.raw["worldcover"]["collection"],
            "limits": {
                "maximum_unique_assets": 8,
                "maximum_requests": 64,
                "maximum_single_asset_bytes": 2_147_483_648,
                "maximum_total_asset_bytes": 12_884_901_888,
                "allowed_hosts": [
                    "planetarycomputer.microsoft.com",
                    "esa-worldcover.s3.eu-central-1.amazonaws.com",
                    "ai4edataeuwest.blob.core.windows.net",
                ],
                "allowed_stac_path": "/api/stac/v1/search",
                "allowed_sas_path_prefix": "/api/sas/v1/token/",
                "allowed_asset_path_prefix_by_host": {
                    "esa-worldcover.s3.eu-central-1.amazonaws.com": "/v100/2020/map/",
                    "ai4edataeuwest.blob.core.windows.net": "/esa-worldcover/v100/2020/map/",
                },
            },
        },
    }
    return EvidenceConfig(path=config.path, project_root=config.project_root, raw=raw)


def _stage_worldcover(
    config: FeasibilityConfig,
    city: CandidateCity,
    boundary: gpd.GeoDataFrame,
    primary: gpd.GeoDataFrame,
    session: requests.Session,
) -> dict[str, Any]:
    evidence = _worldcover_evidence_config(config)
    client = _BoundedClient(session, evidence)
    features, query = _search_items(
        evidence, client=client, city_id=city.id, boundary=boundary
    )
    items = _validate_items(evidence, features=features, boundary=boundary)
    cache = config.project_path(str(config.raw["paths"]["cache_root"])) / "worldcover_cache"
    asset_paths: list[Path] = []
    item_records: list[dict[str, Any]] = []
    for item in items:
        download = client.download(str(item["unsigned_asset_url"]), cache)
        schema = _validate_worldcover_asset(download, boundary=boundary)
        path = Path(download["path"])
        asset_paths.append(path)
        item_records.append(
            {
                "item_id": item["item_id"],
                "tile_id": item["tile_id"],
                "asset": _file_record(config, path),
                "schema": schema,
            }
        )
    grid = build_fixed_grid(
        boundary,
        target_crs=city.target_grid_crs,
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    classes = _mosaic_to_grid(asset_paths, boundary=boundary, grid=grid)
    zones = _zones(primary, grid)
    eligible = (zones > 0) & (classes != 0) & (classes != 80)
    counts = worldcover_eligible_counts(zones, classes, tract_count=len(primary))
    if len(counts) != len(primary):
        raise FeasibilityAuditError("WorldCover tract-zone identity changed.")
    zero_geoids = [
        str(geoid)
        for geoid, count in zip(primary["tract_geoid"], counts, strict=True)
        if int(count) <= 0
    ]
    if zero_geoids:
        return {
            "passed": False,
            "failure_codes": ["tract_without_worldcover_nonwater_support"],
            "zero_eligible_tract_count": len(zero_geoids),
            "zero_eligible_tract_geoids": zero_geoids,
            "item_count": len(items),
            "items": item_records,
            "query": query,
        }
    support, identities = _support_table(
        city_id=city.id,
        geoids=tuple(primary["tract_geoid"].astype(str)),
        zones=zones,
        classes=classes,
        eligible=eligible,
        grid=grid,
    )
    support_path = (
        config.project_path(str(config.raw["paths"]["cache_root"]))
        / city.id
        / "worldcover"
        / "tract_eligible_support.parquet"
    )
    atomic_parquet(support, support_path)
    return {
        "passed": True,
        "failure_codes": [],
        "item_count": len(items),
        "items": item_records,
        "query": query,
        "grid": {
            "crs": grid.crs,
            "shape": list(grid.shape),
            "resolution_m": grid.resolution_m,
            "sha256": grid.sha256,
        },
        "tract_count": len(support),
        "total_eligible_cell_count": int(support["eligible_cell_count"].sum()),
        "all_tracts_positive_zone_and_eligible": True,
        "denominator_invariant_across_dates": True,
        "identities": identities,
        "output": _table_record(config, support_path, support),
        "access_contract": {
            "worldcover_static_class_values_read": True,
            "worldcover_stac_item_assets_read": True,
            "worldcover_unsigned_map_asset_hrefs_read": True,
            "worldcover_signed_map_asset_urls_persisted": False,
            "landsat_target_or_qa_values_read": False,
        },
    }


def _validate_worldcover_asset(
    record: Mapping[str, Any], *, boundary: gpd.GeoDataFrame
) -> dict[str, Any]:
    """Validate raster identity without scanning a full 3-degree global tile."""

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
            or source.nodata not in {None, 0.0}
        ):
            raise FeasibilityAuditError("WorldCover raster schema changed.")
        footprint = box(
            *transform_bounds(
                source.crs,
                "EPSG:4326",
                *source.bounds,
                densify_pts=21,
            )
        )
        city = boundary.to_crs("EPSG:4326").geometry.union_all()
        if footprint.intersection(city).area <= 0:
            raise FeasibilityAuditError("WorldCover raster does not intersect the city.")
        return {
            "crs": source.crs.to_string(),
            "shape": [source.height, source.width],
            "resolution": [float(source.res[0]), float(source.res[1])],
            "dtype": source.dtypes[0],
            "nodata": source.nodata,
            "city_window_only_validation": True,
        }


def _checkpoint_path(config: FeasibilityConfig, city_id: str) -> Path:
    manifest = config.project_path(str(config.raw["paths"]["manifest"]))
    return manifest.parent / "cities" / city_id / "FEASIBILITY.json"


def _output_records(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    census = result.get("census", {})
    landsat = result.get("landsat", {})
    worldcover = result.get("worldcover", {})
    records.extend(census.get("outputs", {}).values())
    records.extend(census.get("raw_responses", {}).values())
    records.extend(landsat.get("outputs", {}).values())
    records.extend(landsat.get("raw_responses", {}).values())
    output = worldcover.get("output")
    if isinstance(output, Mapping):
        records.append(output)
    records.extend(
        item["asset"]
        for item in worldcover.get("items", [])
        if isinstance(item, Mapping) and isinstance(item.get("asset"), Mapping)
    )
    return records


def _authenticate_city_checkpoint(
    config: FeasibilityConfig, city_id: str
) -> dict[str, Any]:
    path = _checkpoint_path(config, city_id)
    payload = read_json_with_commit(path, label=f"{city_id} feasibility checkpoint")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("city", {}).get("id") != city_id
        or payload.get("config_sha256") != sha256_file(config.path)
        or payload.get("access_contract") != ACCESS_CONTRACT
        or payload.get("code_identity") != _code_identity(config)
    ):
        raise FeasibilityAuditError(f"Feasibility checkpoint contract changed: {city_id}")
    for record in _output_records(payload):
        file_path = config.project_path(str(record["path"]))
        if (
            not file_path.is_file()
            or file_path.stat().st_size != int(record["bytes"])
            or sha256_file(file_path) != record["sha256"]
        ):
            raise FeasibilityAuditError(f"Feasibility evidence changed: {record['path']}")
    return payload


def _audit_city(
    config: FeasibilityConfig,
    city: CandidateCity,
    session: requests.Session,
    *,
    completed: Sequence[str],
    selected: Sequence[str],
) -> dict[str, Any]:
    checkpoint = _checkpoint_path(config, city.id)
    if checkpoint.is_file():
        return _authenticate_city_checkpoint(config, city.id)
    _write_status(
        config,
        state="running",
        current_city_id=city.id,
        current_step="census",
        completed=completed,
        selected=selected,
    )
    boundary, primary, census = _stage_census(config, city, session)
    _write_status(
        config,
        state="running",
        current_city_id=city.id,
        current_step="landsat_metadata",
        completed=completed,
        selected=selected,
    )
    landsat, _ = _stage_landsat_metadata(config, city, boundary, session)
    failure_codes = list(landsat["failure_codes"])
    worldcover: dict[str, Any]
    if landsat["passed"]:
        _write_status(
            config,
            state="running",
            current_city_id=city.id,
            current_step="worldcover_static_support",
            completed=completed,
            selected=selected,
        )
        worldcover = _stage_worldcover(config, city, boundary, primary, session)
        failure_codes.extend(worldcover["failure_codes"])
    else:
        worldcover = {
            "passed": False,
            "not_run_reason": "landsat_candidate_gate_failed",
            "failure_codes": [],
        }
    passes = bool(census["passed"] and landsat["passed"] and worldcover["passed"])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_city_feasibility_audit",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": str(config.raw["experiment"]["id"]),
        "config_sha256": sha256_file(config.path),
        "city": asdict(city),
        "passes": passes,
        "failure_codes": failure_codes,
        "census": census,
        "landsat": landsat,
        "worldcover": worldcover,
        "access_contract": ACCESS_CONTRACT,
        "code_identity": _code_identity(config),
    }
    write_manifest_no_clobber(payload, checkpoint)
    return _authenticate_city_checkpoint(config, city.id)


def _manifest_payload(
    config: FeasibilityConfig,
    results: Sequence[Mapping[str, Any]],
    selected: Sequence[str],
) -> dict[str, Any]:
    primary_ids = [city.id for city in config.primary]
    replacement_ids = [city.id for city in config.replacements]
    selected_ids = list(selected)
    if len(selected_ids) < 4:
        decision = "insufficient_feasible_cities"
        next_stage = "stop_and_review_city_feasibility"
    elif selected_ids == primary_ids:
        decision = "primary_set_feasible"
        next_stage = "lock_final_unseen_city_set_and_protocol"
    else:
        decision = "replacement_set_selected"
        next_stage = "lock_final_unseen_city_set_and_protocol"
    city_records = {
        str(result["city"]["id"]): {
            "checkpoint": {
                **_file_record(config, _checkpoint_path(config, str(result["city"]["id"]))),
                "commit_sha256": result["commit_sha256"],
            },
            "tier": result["city"]["tier"],
            "passes": bool(result["passes"]),
            "failure_codes": list(result["failure_codes"]),
            "primary_tract_count": int(result["census"]["primary_tract_count"]),
            "eligible_unique_physical_date_count": int(
                result["landsat"]["eligible_unique_physical_date_count"]
            ),
            "worldcover_all_tracts_supported": bool(
                result["worldcover"].get("all_tracts_positive_zone_and_eligible", False)
            ),
        }
        for result in results
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": str(config.raw["experiment"]["id"]),
        "design_authorization": {
            "scope": "user_confirmed_metadata_feasibility_only",
            "design_draft": _file_record(
                config, config.project_root / "docs/NEXT_EXPERIMENT_PREREGISTRATION_DRAFT.md"
            ),
            "configuration": _file_record(config, config.path),
        },
        "audit_contract": {
            "year": 2025,
            "local_start_date": str(config.raw["window"]["start_date"]),
            "local_end_date": str(config.raw["window"]["end_date"]),
            "minimum_place_fraction": 0.5,
            "exclude_tract_prefix": "98",
            "worldcover_year": 2020,
            "worldcover_version": "v100",
            "excluded_classes": [0, 80],
            "maximum_overpass_span_minutes": 15,
            "minimum_city_union_coverage_fraction": 0.98,
            "minimum_candidate_physical_acquisitions": 16,
        },
        "primary_city_order": primary_ids,
        "replacement_city_order": replacement_ids,
        "cities": city_records,
        "selection": {
            "decision": decision,
            "selected_city_ids": selected_ids,
            "replacements_used": [
                city_id for city_id in selected_ids if city_id in replacement_ids
            ],
            "all_selected_cities_passed": len(selected_ids) == 4,
        },
        "access_contract": ACCESS_CONTRACT,
        "code_identity": _code_identity(config),
        "next_safe_stage": next_stage,
    }


def authenticate_feasibility_audit(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    config = load_feasibility_config(project_root, config_path)
    manifest_path = config.project_path(str(config.raw["paths"]["manifest"]))
    payload = read_json_with_commit(manifest_path, label="next-experiment feasibility audit")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or payload.get("access_contract") != ACCESS_CONTRACT
        or payload.get("code_identity") != _code_identity(config)
        or payload.get("design_authorization", {}).get("configuration")
        != _file_record(config, config.path)
    ):
        raise FeasibilityAuditError("Final feasibility manifest contract changed.")
    city_records = payload.get("cities")
    if not isinstance(city_records, Mapping) or not city_records:
        raise FeasibilityAuditError("Final feasibility manifest has no city results.")
    expected_results: list[dict[str, Any]] = []
    expected_selected: list[str] = []
    expected_audited_ids: list[str] = []
    for city in config.primary:
        result = _authenticate_city_checkpoint(config, city.id)
        expected_results.append(result)
        expected_audited_ids.append(city.id)
        if result["passes"]:
            expected_selected.append(city.id)
    if len(expected_selected) < 4:
        for city in config.replacements:
            result = _authenticate_city_checkpoint(config, city.id)
            expected_results.append(result)
            expected_audited_ids.append(city.id)
            if result["passes"]:
                expected_selected.append(city.id)
            if len(expected_selected) == 4:
                break
    if list(city_records) != expected_audited_ids:
        raise FeasibilityAuditError("Audited city order or replacement stopping rule changed.")

    expected = _manifest_payload(config, expected_results, expected_selected)
    observed_without_timestamp = {
        key: value
        for key, value in payload.items()
        if key not in {"generated_at_utc", "commit_sha256"}
    }
    expected_without_timestamp = {
        key: value for key, value in expected.items() if key != "generated_at_utc"
    }
    if observed_without_timestamp != expected_without_timestamp:
        raise FeasibilityAuditError(
            "Final feasibility manifest is not the deterministic summary of its checkpoints."
        )
    generated_at = payload.get("generated_at_utc")
    try:
        timestamp = datetime.fromisoformat(str(generated_at))
    except ValueError as exc:
        raise FeasibilityAuditError("Final feasibility timestamp is invalid.") from exc
    if timestamp.tzinfo is None:
        raise FeasibilityAuditError("Final feasibility timestamp lacks a timezone.")
    return payload


def run_feasibility_audit(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Run or authenticate the resumable target-blind city feasibility audit."""

    config = load_feasibility_config(project_root, config_path)
    manifest_path = config.project_path(str(config.raw["paths"]["manifest"]))
    if check_only or manifest_path.is_file():
        return authenticate_feasibility_audit(config.project_root, config.path)
    completed: list[str] = []
    selected: list[str] = []
    results: list[dict[str, Any]] = []
    session = _retrying_session()
    session.headers.update(
        {"User-Agent": "la-neighborhood-heat/0.1 student feasibility audit"}
    )
    try:
        for city in config.primary:
            result = _audit_city(
                config, city, session, completed=completed, selected=selected
            )
            results.append(result)
            completed.append(city.id)
            if result["passes"]:
                selected.append(city.id)
        if len(selected) < 4:
            for city in config.replacements:
                result = _audit_city(
                    config, city, session, completed=completed, selected=selected
                )
                results.append(result)
                completed.append(city.id)
                if result["passes"]:
                    selected.append(city.id)
                if len(selected) == 4:
                    break
        _write_status(
            config,
            state="publishing",
            current_city_id=None,
            current_step="deciding",
            completed=completed,
            selected=selected,
        )
        write_manifest_no_clobber(
            _manifest_payload(config, results, selected), manifest_path
        )
        payload = authenticate_feasibility_audit(config.project_root, config.path)
        _write_status(
            config,
            state="complete",
            current_city_id=None,
            current_step="complete",
            completed=completed,
            selected=selected,
        )
        return payload
    except BaseException as error:
        _write_status(
            config,
            state="failed_runtime",
            current_city_id=(completed[-1] if completed else None),
            current_step="failed_runtime",
            completed=completed,
            selected=selected,
            error=error,
        )
        raise
    finally:
        session.close()
