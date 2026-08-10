"""Production orchestration for lagged Sentinel-2 tract features.

The builder consumes the frozen, target-blind Sentinel inventory.  It never
queries a new cohort and never reads Landsat target values.  Each physical
acquisition is cached independently; the final target-date feature table is
promoted only after every frozen acquisition cache passes its byte and semantic
locks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform
from rasterio.windows import Window

from la_heat.config import ResearchConfig, load_config
from la_heat.grid import FixedGrid, build_fixed_grid
from la_heat.guardrails import validate_static_eligible_denominator
from la_heat.landmask import get_static_land_item, read_static_land_mask
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_features import (
    REFLECTANCE_BANDS,
    AlignedSentinelTile,
    aggregate_acquisition_to_tracts,
    build_previous_60_day_composites,
    clear_land_mask,
    compute_optical_indices,
    decode_boa_reflectance,
    mosaic_aligned_tiles,
    parse_boa_calibration,
)
from la_heat.sentinel_inventory import processing_baseline_key

ALGORITHM_VERSION = "sentinel-optical-v1-physical-mosaic-fixed-support"
PIPELINE_FILES = (
    "pyproject.toml",
    "src/la_heat/config.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/landmask.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/sentinel_inventory.py",
)
REQUIRED_INVENTORY_FILES = (
    "selected_acquisitions.csv",
    "selected_items.csv",
    "target_window_membership.csv",
)
REQUIRED_ITEM_COLUMNS = {
    "physical_acquisition_id",
    "item_id",
    "mgrs_tile",
    "processing_baseline",
    "asset_product_metadata_href",
    "asset_scl_href",
    *(f"asset_{band.lower()}_href" for band in REFLECTANCE_BANDS),
}


@dataclass(frozen=True, slots=True)
class SentinelStageConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.raw)

    @property
    def albedo_coefficients(self) -> dict[str, float]:
        return {
            str(key).upper(): float(value)
            for key, value in self.raw["albedo_proxy"].items()
            if str(key).lower() != "intercept"
        }

    @property
    def minimum_coverage(self) -> float:
        return float(self.raw["qa"]["minimum_acquisition_coverage_fraction"])

    @property
    def minimum_acquisitions(self) -> int:
        return int(self.raw["qa"]["minimum_physical_acquisitions"])


@dataclass(frozen=True, slots=True)
class FrozenSentinelInputs:
    acquisitions: pd.DataFrame
    items: pd.DataFrame
    membership: pd.DataFrame
    summary: dict[str, Any]
    locks: dict[str, str]


@dataclass(frozen=True, slots=True)
class FixedSpatialSupport:
    target_grid: FixedGrid
    optical_grid: FixedGrid
    zones: np.ndarray
    eligible_land: np.ndarray
    tracts: gpd.GeoDataFrame
    tract_geoids: tuple[str, ...]
    eligible_counts: dict[str, int]
    eligible_identity_sha256s: dict[str, str]
    target_dates: tuple[str, ...]
    locks: dict[str, str]


def _require_keys(section: dict[str, Any], required: set[str], *, name: str) -> None:
    missing = required - set(section)
    if missing:
        raise ValueError(f"Sentinel stage config [{name}] lacks keys: {sorted(missing)}")


def load_sentinel_stage_config(path: str | Path) -> SentinelStageConfig:
    """Load and validate the independently hashed Sentinel stage configuration."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _require_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "grid",
            "qa",
            "window",
            "albedo_proxy",
            "paths",
        },
        name="root",
    )
    if str(raw["schema_version"]) != "1" or raw["algorithm_version"] != ALGORITHM_VERSION:
        raise ValueError("Unsupported Sentinel feature schema or algorithm version.")
    _require_keys(
        raw["grid"],
        {
            "crs",
            "resolution_m",
            "edge_anchor_x_m",
            "edge_anchor_y_m",
            "reflectance_resampling",
            "scl_resampling",
            "target_cell_aggregation",
            "processing_order",
        },
        name="grid",
    )
    grid = raw["grid"]
    if float(grid["resolution_m"]) != 20.0:
        raise ValueError("The frozen optical mosaic grid must be 20 m.")
    if grid["reflectance_resampling"] != "average" or grid["scl_resampling"] != "nearest":
        raise ValueError("Sentinel reflectance/SCL resampling rules changed.")
    if grid["target_cell_aggregation"] != (
        "valid_area_average_then_tract_weighted_median"
    ):
        raise ValueError("Unsupported Sentinel target-cell aggregation rule.")
    if grid["processing_order"] != (
        "validate_phase_then_area_average_dn_and_max_saturation_mask_to_20m_then_decode_boa_"
        "then_scl_4_5_gate"
    ):
        raise ValueError("Unsupported Sentinel reflectance/QA processing order.")
    _require_keys(
        raw["qa"],
        {
            "accepted_scl_classes",
            "nodata_dn",
            "saturated_dn",
            "minimum_acquisition_coverage_fraction",
            "minimum_physical_acquisitions",
            "index_denominator_epsilon",
            "global_scene_cloud_cover_filter",
        },
        name="qa",
    )
    qa = raw["qa"]
    if set(qa["accepted_scl_classes"]) != {4, 5}:
        raise ValueError("Primary clear-land classes must be exactly SCL 4 and 5.")
    if bool(qa["global_scene_cloud_cover_filter"]):
        raise ValueError("A global Sentinel scene-cloud filter is prohibited.")
    if float(qa["minimum_acquisition_coverage_fraction"]) != 0.8:
        raise ValueError("The frozen per-acquisition coverage threshold is 0.80.")
    if int(qa["minimum_physical_acquisitions"]) != 3:
        raise ValueError("The frozen composite minimum is three physical acquisitions.")
    if float(qa["index_denominator_epsilon"]) != 1e-6:
        raise ValueError("The frozen Sentinel index denominator epsilon is 1e-6.")
    _require_keys(
        raw["window"],
        {"start_days_before_target", "end_days_before_target", "local_timezone"},
        name="window",
    )
    window = raw["window"]
    if (
        int(window["start_days_before_target"]) != 60
        or int(window["end_days_before_target"]) != 1
        or window["local_timezone"] != "America/Los_Angeles"
    ):
        raise ValueError("Primary Sentinel window must remain local d-60 through d-1.")
    config = SentinelStageConfig(raw=raw, path=config_path)
    expected_albedo = {"B02", "B03", "B04", "B08", "B11", "B12"}
    if set(config.albedo_coefficients) != expected_albedo or not math.isclose(
        sum(config.albedo_coefficients.values()), 1.0, abs_tol=1e-6
    ):
        raise ValueError("Sentinel albedo coefficients are incomplete or do not sum to one.")
    if float(raw["albedo_proxy"].get("intercept", math.nan)) != 0.0:
        raise ValueError("The prespecified Sentinel albedo proxy intercept must be zero.")
    return config


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _research_dependency_payload(research: ResearchConfig) -> dict[str, Any]:
    """Select only research settings that can change Sentinel feature semantics."""

    return {
        "study": {
            "final_test_year": research.final_test_year,
            "unlock_final_test": research.final_test_unlocked,
        },
        "static_land_mask": research.raw["static_land_mask"],
    }


def _load_frozen_sentinel_inventory(
    inventory_directory: Path,
    *,
    research: ResearchConfig,
) -> FrozenSentinelInputs:
    summary_path = inventory_directory / "inventory_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("state") != "complete" or summary.get("artifacts_valid") is not True:
        raise ValueError("Sentinel inventory commit marker is not complete and valid.")
    if summary.get("global_scene_cloud_cover_filter") is not None:
        raise ValueError("Frozen Sentinel inventory unexpectedly used a cloud cutoff.")
    for filename in REQUIRED_INVENTORY_FILES:
        path = inventory_directory / filename
        record = summary.get("output_files", {}).get(filename, {})
        if (
            not path.exists()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise ValueError(f"Frozen Sentinel inventory failed byte lock: {filename}")
    acquisitions = pd.read_csv(
        inventory_directory / "selected_acquisitions.csv",
        dtype={"processing_baseline": "string"},
    )
    items = pd.read_csv(
        inventory_directory / "selected_items.csv",
        dtype={"processing_baseline": "string"},
    )
    membership = pd.read_csv(inventory_directory / "target_window_membership.csv")
    if missing := REQUIRED_ITEM_COLUMNS - set(items):
        raise ValueError(f"Frozen Sentinel items lack columns: {sorted(missing)}")
    if acquisitions["physical_acquisition_id"].duplicated().any():
        raise ValueError("Frozen Sentinel acquisitions contain duplicate identities.")
    if items["item_id"].duplicated().any():
        raise ValueError("Frozen Sentinel selected items contain duplicate item IDs.")
    if membership.duplicated(["target_date", "physical_acquisition_id"]).any():
        raise ValueError("Frozen Sentinel memberships contain duplicate keys.")
    targets = pd.to_datetime(membership["target_date"], format="%Y-%m-%d", errors="raise")
    acquired = pd.to_datetime(
        membership["acquisition_local_date"], format="%Y-%m-%d", errors="raise"
    )
    lag = pd.to_numeric(membership["lag_days"], errors="raise")
    if ((targets - acquired).dt.days != lag).any() or not lag.between(1, 60).all():
        raise ValueError("Frozen Sentinel membership violates exact d-60:d-1.")
    if (targets.dt.year >= research.final_test_year).any() and not research.final_test_unlocked:
        raise PermissionError(f"Final-test year {research.final_test_year} remains locked.")
    acquisition_ids = set(acquisitions["physical_acquisition_id"])
    if set(items["physical_acquisition_id"]) != acquisition_ids:
        raise ValueError("Frozen Sentinel item/acquisition identities disagree.")
    if not set(membership["physical_acquisition_id"]).issubset(acquisition_ids):
        raise ValueError("Frozen Sentinel membership references an unselected acquisition.")
    for acquisition in acquisitions.itertuples(index=False):
        physical_id = str(acquisition.physical_acquisition_id)
        selected = items.loc[items["physical_acquisition_id"] == physical_id]
        expected_item_ids = tuple(sorted(str(acquisition.item_ids).split("|")))
        observed_item_ids = tuple(sorted(selected["item_id"].astype(str)))
        if expected_item_ids != observed_item_ids or len(selected) != int(acquisition.item_count):
            raise ValueError(f"Frozen selected-item cohort disagrees for {physical_id}.")
        if set(selected["processing_baseline"].astype(str)) != {
            str(acquisition.processing_baseline)
        }:
            raise ValueError(f"Frozen processing baseline disagrees for {physical_id}.")
    locks = {
        "sentinel_inventory_summary_sha256_audit_only": sha256_file(summary_path),
        "sentinel_inventory_semantic_sha256": str(
            summary["sentinel_inventory_semantic_sha256"]
        ),
        **{
            f"sentinel_{filename.replace('.', '_')}_sha256": str(
                summary["output_files"][filename]["sha256"]
            )
            for filename in REQUIRED_INVENTORY_FILES
        },
    }
    return FrozenSentinelInputs(acquisitions, items, membership, summary, locks)


def _fixed_grid_from_lock(payload: dict[str, Any]) -> FixedGrid:
    required = {
        "crs",
        "resolution_m",
        "edge_anchor_x_m",
        "edge_anchor_y_m",
        "bounds",
        "width",
        "height",
        "grid_definition_sha256",
    }
    if missing := required - set(payload):
        raise ValueError(f"Target grid lock lacks fields: {sorted(missing)}")
    left, bottom, right, top = (float(value) for value in payload["bounds"])
    resolution = float(payload["resolution_m"])
    grid = FixedGrid(
        crs=str(payload["crs"]),
        resolution_m=resolution,
        anchor_x_m=float(payload["edge_anchor_x_m"]),
        anchor_y_m=float(payload["edge_anchor_y_m"]),
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        width=int(payload["width"]),
        height=int(payload["height"]),
        transform=from_origin(left, top, resolution, resolution),
    )
    if grid.sha256 != payload["grid_definition_sha256"]:
        raise ValueError("Reconstructed target grid failed its definition hash.")
    return grid


def _load_fixed_spatial_support(
    *,
    project_root: Path,
    research: ResearchConfig,
    stage: SentinelStageConfig,
    inventory: FrozenSentinelInputs,
) -> FixedSpatialSupport:
    paths = stage.raw["paths"]
    target_directory = _resolve_project_path(project_root, paths["target_directory"])
    progress_path = target_directory / "build_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if (
        progress.get("state") != "model_ready"
        or progress.get("build_complete") is not True
        or progress.get("promoted_outputs_valid") is not True
    ):
        raise ValueError("Target stage is not a complete locked support source.")
    grid_lock_path = target_directory / "fixed_grid_lock.json"
    grid_lock = json.loads(grid_lock_path.read_text(encoding="utf-8"))
    target_grid = _fixed_grid_from_lock(grid_lock)
    grid_config = stage.raw["grid"]
    if str(grid_config["crs"]) != target_grid.crs:
        raise ValueError("Sentinel optical grid CRS must match the locked target grid CRS.")

    tract_path = target_directory / "primary_tract_manifest.parquet"
    tracts = gpd.read_parquet(tract_path).reset_index(drop=True)
    if tracts.crs is None or "GEOID" not in tracts:
        raise ValueError("Primary tract manifest lacks CRS or GEOID.")
    if not tracts["primary_included"].all() or tracts["GEOID"].duplicated().any():
        raise ValueError("Primary tract manifest is not a unique included universe.")
    tract_geoids = tuple(tracts["GEOID"].astype(str))
    projected = tracts.to_crs(target_grid.crs)
    zones = rasterize(
        ((geometry, index + 1) for index, geometry in enumerate(projected.geometry)),
        out_shape=target_grid.shape,
        transform=target_grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    if hashlib.sha256(zones.tobytes()).hexdigest() != grid_lock["zone_raster_sha256"]:
        raise ValueError("Recreated tract zones failed the target-stage hash lock.")

    land_item = get_static_land_item(research)
    eligible_land = read_static_land_mask(
        land_item,
        output_shape=target_grid.shape,
        output_transform=target_grid.transform,
        output_crs=target_grid.crs,
        config=research,
    )
    land_sha = hashlib.sha256(np.packbits(eligible_land.ravel()).tobytes()).hexdigest()
    if land_sha != grid_lock["static_land_mask_sha256"]:
        raise ValueError("Recreated static eligible-land mask failed its target-stage lock.")

    target_qa_path = target_directory / "development_target_qa.parquet"
    target_locks = progress.get("aggregate_outputs", {}).get(target_qa_path.name, {})
    if sha256_file(target_qa_path) != target_locks.get("sha256"):
        raise ValueError("Target QA table failed its frozen byte lock.")
    # Column projection is intentional: the predictor stage never reads target values.
    denominators = pd.read_parquet(
        target_qa_path,
        columns=[
            "tract_geoid",
            "target_date",
            "eligible_pixel_count_static",
            "eligible_pixel_identity_sha256",
        ],
    )
    validate_static_eligible_denominator(denominators)
    if pd.to_datetime(denominators["target_date"]).dt.year.ge(
        research.final_test_year
    ).any() and not research.final_test_unlocked:
        raise PermissionError(f"Final-test year {research.final_test_year} remains locked.")
    frozen_counts = (
        denominators.groupby("tract_geoid", sort=False)["eligible_pixel_count_static"]
        .first()
        .astype(int)
        .to_dict()
    )
    frozen_identities = (
        denominators.groupby("tract_geoid", sort=False)[
            "eligible_pixel_identity_sha256"
        ]
        .first()
        .astype(str)
        .to_dict()
    )
    if set(frozen_counts) != set(tract_geoids):
        raise ValueError("Frozen target denominators do not match the tract universe.")
    if set(frozen_identities) != set(tract_geoids) or not all(
        len(value) == 64 for value in frozen_identities.values()
    ):
        raise ValueError("Frozen eligible-land identity hashes are incomplete.")
    observed_counts = np.bincount(
        zones[eligible_land], minlength=len(tract_geoids) + 1
    )[1:]
    if not np.array_equal(
        observed_counts,
        np.array([frozen_counts[geoid] for geoid in tract_geoids]),
    ):
        raise ValueError("Static eligible-land counts changed after target construction.")

    city_path = _resolve_project_path(project_root, paths["city_boundary"])
    city = gpd.read_file(city_path)
    optical_grid = build_fixed_grid(
        city,
        target_crs=str(grid_config["crs"]),
        resolution_m=float(grid_config["resolution_m"]),
        anchor_x_m=float(grid_config["edge_anchor_x_m"]),
        anchor_y_m=float(grid_config["edge_anchor_y_m"]),
    )
    target_dates = tuple(sorted(inventory.membership["target_date"].astype(str).unique()))
    locks = {
        "target_progress_sha256_audit_only": sha256_file(progress_path),
        "target_qa_sha256": str(target_locks["sha256"]),
        "target_grid_lock_sha256_audit_only": sha256_file(grid_lock_path),
        "target_grid_identity_sha256": str(grid_lock["target_grid_identity_sha256"]),
        "target_grid_definition_sha256": target_grid.sha256,
        "optical_grid_definition_sha256": optical_grid.sha256,
        "zone_raster_sha256": str(grid_lock["zone_raster_sha256"]),
        "static_land_mask_sha256": land_sha,
        "tract_manifest_file_sha256": sha256_file(tract_path),
        "tract_manifest_semantic_sha256": str(tracts["tract_manifest_sha256"].iloc[0]),
    }
    return FixedSpatialSupport(
        target_grid=target_grid,
        optical_grid=optical_grid,
        zones=zones,
        eligible_land=eligible_land,
        tracts=tracts,
        tract_geoids=tract_geoids,
        eligible_counts=frozen_counts,
        eligible_identity_sha256s=frozen_identities,
        target_dates=target_dates,
        locks=locks,
    )


def _atomic_bytes(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.write_bytes(content)
    partial.replace(destination)


def _safe_item_filename(item_id: str) -> str:
    prefix = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in item_id
    )
    return f"{prefix}-{hashlib.sha256(item_id.encode()).hexdigest()[:12]}.xml"


def _read_product_metadata(
    *,
    item_id: str,
    unsigned_url: str,
    raw_metadata_directory: Path,
    session: requests.Session,
) -> tuple[bytes, str, Path]:
    destination = raw_metadata_directory / _safe_item_filename(item_id)
    if destination.exists():
        content = destination.read_bytes()
        return content, sha256_file(destination), destination
    signed_url = pc.sign_url(unsigned_url)
    response = session.get(signed_url, timeout=(15, 120))
    response.raise_for_status()
    content = response.content
    if not content:
        raise ValueError(f"Sentinel product metadata is empty for {item_id}.")
    _atomic_bytes(content, destination)
    return content, sha256_file(destination), destination


def _raster_environment() -> dict[str, str]:
    return {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif",
    }


def _validate_native_asset_grid(
    source: rasterio.DatasetReader,
    *,
    grid: FixedGrid,
    categorical: bool,
    tolerance: float = 1e-8,
) -> None:
    """Require native 10/20 m UTM phase before any reflectance averaging."""

    expected_crs = rasterio.crs.CRS.from_string(grid.crs)
    if source.crs != expected_crs:
        raise ValueError(
            f"Sentinel asset CRS {source.crs} does not match optical grid {grid.crs}."
        )
    transform_value = source.transform
    if (
        not math.isclose(transform_value.b, 0.0, abs_tol=tolerance)
        or not math.isclose(transform_value.d, 0.0, abs_tol=tolerance)
        or transform_value.a <= 0
        or transform_value.e >= 0
        or not math.isclose(transform_value.a, -transform_value.e, abs_tol=tolerance)
    ):
        raise ValueError("Sentinel asset must be a north-up square-pixel grid.")
    resolution = float(transform_value.a)
    expected_resolutions = {20.0} if categorical else {10.0, 20.0}
    supported_resolution = any(
        math.isclose(resolution, value, abs_tol=tolerance)
        for value in expected_resolutions
    )
    if not supported_resolution:
        raise ValueError(
            f"Sentinel asset has unsupported native resolution {resolution:g} m."
        )
    x_phase = (transform_value.c - grid.left) / resolution
    y_phase = (grid.top - transform_value.f) / resolution
    if not (
        math.isclose(x_phase, round(x_phase), abs_tol=tolerance)
        and math.isclose(y_phase, round(y_phase), abs_tol=tolerance)
    ):
        raise ValueError(
            "Sentinel asset pixel edges are not phase-aligned to the frozen optical grid."
        )


def _read_asset_to_optical_grid(
    unsigned_url: str,
    *,
    grid: FixedGrid,
    categorical: bool,
    saturated_dn: int = 65535,
) -> np.ndarray:
    signed_url = pc.sign_url(unsigned_url)
    if categorical:
        destination = np.zeros(grid.shape, dtype=np.uint8)
        resampling = Resampling.nearest
        dst_nodata: int | float = 0
    else:
        destination = np.full(grid.shape, np.nan, dtype=np.float32)
        saturation_max = np.zeros(grid.shape, dtype=np.float32)
        resampling = Resampling.average
        dst_nodata = np.nan
    with rasterio.Env(**_raster_environment()):
        with rasterio.open(signed_url) as source:
            _validate_native_asset_grid(source, grid=grid, categorical=categorical)
            source_nodata = source.nodata if source.nodata is not None else 0
            reproject(
                source=rasterio.band(source, 1),
                destination=destination,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source_nodata,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=dst_nodata,
                resampling=resampling,
                init_dest_nodata=True,
            )
            if not categorical:
                reproject(
                    source=rasterio.band(source, 1),
                    destination=saturation_max,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source_nodata,
                    dst_transform=grid.transform,
                    dst_crs=grid.crs,
                    dst_nodata=0,
                    resampling=Resampling.max,
                    init_dest_nodata=True,
                )
                destination[saturation_max == float(saturated_dn)] = np.nan
    return destination


def _acquisition_cache_directory(output_directory: Path, physical_id: str) -> Path:
    identity = hashlib.sha256(physical_id.encode()).hexdigest()[:20]
    return output_directory / "by_acquisition" / identity


def _expected_acquisition_lock(
    *,
    base_lock: dict[str, str],
    physical_id: str,
    item_rows: pd.DataFrame,
) -> dict[str, str]:
    item_assets = item_rows.sort_values(["mgrs_tile", "item_id"])[
        ["item_id", "mgrs_tile", "processing_baseline", *sorted(REQUIRED_ITEM_COLUMNS - {
            "physical_acquisition_id", "item_id", "mgrs_tile", "processing_baseline"
        })]
    ]
    return {
        **{
            key: value
            for key, value in base_lock.items()
            if not key.endswith("_audit_only")
        },
        "physical_acquisition_id": physical_id,
        "selected_item_assets_sha256": canonical_sha256(item_assets.to_dict("records")),
    }


def _acquisition_cache_is_current(
    directory: Path,
    *,
    expected_lock: dict[str, str],
    metadata_path_root: Path | None = None,
) -> bool:
    summary_path = directory / "summary.json"
    output_path = directory / "acquisition_tract.parquet"
    if not summary_path.exists() or not output_path.exists():
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    record = summary.get("output_file", {})
    metadata_records = summary.get("product_metadata", [])
    if not isinstance(metadata_records, list) or not metadata_records:
        return False
    for metadata in metadata_records:
        metadata_path = Path(str(metadata.get("product_metadata_path", "")))
        if not metadata_path.is_absolute() and metadata_path_root is not None:
            metadata_path = metadata_path_root / metadata_path
        if (
            not metadata_path.exists()
            or sha256_file(metadata_path) != metadata.get("product_metadata_sha256")
        ):
            return False
    return bool(
        summary.get("state") == "complete"
        and summary.get("cache_lock") == expected_lock
        and record.get("bytes") == output_path.stat().st_size
        and record.get("sha256") == sha256_file(output_path)
        and record.get("rows") == summary.get("tract_count")
    )


def _process_acquisition(
    acquisition_row: Any,
    *,
    item_rows: pd.DataFrame,
    spatial: FixedSpatialSupport,
    stage: SentinelStageConfig,
    base_lock: dict[str, str],
    output_directory: Path,
    raw_metadata_directory: Path,
    session: requests.Session,
    force: bool,
    metadata_path_root: Path | None = None,
    download_threads: int = 1,
) -> dict[str, Any]:
    if download_threads < 1 or download_threads > 8:
        raise ValueError("download_threads must be between 1 and 8.")
    physical_id = str(acquisition_row.physical_acquisition_id)
    directory = _acquisition_cache_directory(output_directory, physical_id)
    expected_lock = _expected_acquisition_lock(
        base_lock=base_lock, physical_id=physical_id, item_rows=item_rows
    )
    if not force and _acquisition_cache_is_current(
        directory,
        expected_lock=expected_lock,
        metadata_path_root=metadata_path_root,
    ):
        print(f"[sentinel] cache hit {physical_id}", flush=True)
        return json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "summary.json"
    summary_path.unlink(missing_ok=True)

    aligned_tiles: list[AlignedSentinelTile] = []
    metadata_records: list[dict[str, Any]] = []
    qa = stage.raw["qa"]
    for item in item_rows.sort_values(["mgrs_tile", "item_id"]).itertuples(index=False):
        print(f"[sentinel] align frozen item {item.item_id}", flush=True)
        metadata, metadata_sha, metadata_path = _read_product_metadata(
            item_id=str(item.item_id),
            unsigned_url=str(item.asset_product_metadata_href),
            raw_metadata_directory=raw_metadata_directory,
            session=session,
        )
        calibration = parse_boa_calibration(
            metadata,
            processing_baseline=str(item.processing_baseline),
        )
        def read_reflectance(
            band: str,
            *,
            current_item: Any = item,
            current_calibration: Any = calibration,
        ) -> np.ndarray:
            print(f"[sentinel] read {current_item.item_id} {band}", flush=True)
            dn = _read_asset_to_optical_grid(
                str(getattr(current_item, f"asset_{band.lower()}_href")),
                grid=spatial.optical_grid,
                categorical=False,
                saturated_dn=int(qa["saturated_dn"]),
            )
            return decode_boa_reflectance(
                dn,
                band=band,
                calibration=current_calibration,
                nodata_dn=int(qa["nodata_dn"]),
                saturated_dn=int(qa["saturated_dn"]),
            )

        if download_threads == 1:
            scl = _read_asset_to_optical_grid(
                str(item.asset_scl_href),
                grid=spatial.optical_grid,
                categorical=True,
            )
            reflectance = {band: read_reflectance(band) for band in REFLECTANCE_BANDS}
        else:
            with ThreadPoolExecutor(
                max_workers=download_threads,
                thread_name_prefix="sentinel-asset",
            ) as pool:
                scl_future = pool.submit(
                    _read_asset_to_optical_grid,
                    str(item.asset_scl_href),
                    grid=spatial.optical_grid,
                    categorical=True,
                )
                band_futures = {
                    band: pool.submit(read_reflectance, band)
                    for band in REFLECTANCE_BANDS
                }
                scl = scl_future.result()
                reflectance = {
                    band: band_futures[band].result() for band in REFLECTANCE_BANDS
                }
        aligned_tiles.append(
            AlignedSentinelTile(
                item_id=str(item.item_id),
                mgrs_tile=str(item.mgrs_tile),
                scl=scl,
                reflectance=reflectance,
                calibration_sha256=calibration.sha256,
            )
        )
        recorded_metadata_path = metadata_path
        if metadata_path_root is not None:
            try:
                recorded_metadata_path = metadata_path.relative_to(metadata_path_root)
            except ValueError:
                pass
        metadata_records.append(
            {
                "item_id": str(item.item_id),
                "processing_baseline": calibration.processing_baseline,
                "calibration_sha256": calibration.sha256,
                "product_metadata_path": recorded_metadata_path.as_posix(),
                "product_metadata_sha256": metadata_sha,
            }
        )

    mosaic = mosaic_aligned_tiles(aligned_tiles)
    if len(set(mosaic.calibration_sha256s)) != 1:
        raise ValueError(
            "Adjacent tiles in one physical acquisition have inconsistent BOA calibration."
        )
    base_valid = clear_land_mask(
        mosaic.scl,
        mosaic.reflectance,
        accepted_scl_classes=qa["accepted_scl_classes"],
    )
    indices = compute_optical_indices(
        mosaic.reflectance,
        denominator_epsilon=float(qa["index_denominator_epsilon"]),
        albedo_coefficients=stage.albedo_coefficients,
    )
    joint_valid = base_valid.copy()
    for values in indices.values():
        joint_valid &= np.isfinite(values)
    joint_valid_pixel_count = int(joint_valid.sum())
    del joint_valid
    mosaic_scl = mosaic.scl
    mosaic_item_ids = mosaic.item_ids
    mosaic_mgrs_tiles = mosaic.mgrs_tiles
    mosaic_owned_pixel_counts = mosaic.owned_pixel_counts
    mosaic_calibration_sha256s = mosaic.calibration_sha256s
    # Acquisition mosaics are large (~8 million cells).  Release tile/band arrays
    # before tract aggregation retains its own 30 m intermediates.
    del aligned_tiles, mosaic
    aggregated = aggregate_acquisition_to_tracts(
        physical_acquisition_id=physical_id,
        acquisition_local_date=str(acquisition_row.acquisition_local_date),
        platform=str(acquisition_row.platform),
        processing_baseline=str(acquisition_row.processing_baseline),
        indices=indices,
        base_valid_20m=base_valid,
        optical_grid=spatial.optical_grid,
        target_grid=spatial.target_grid,
        zone_raster_30m=spatial.zones,
        eligible_land_30m=spatial.eligible_land,
        tract_geoids=spatial.tract_geoids,
        expected_eligible_counts=spatial.eligible_counts,
        minimum_acquisition_coverage=stage.minimum_coverage,
    )
    cloud_values = [
        None if pd.isna(value) else float(value)
        for value in item_rows.sort_values(["mgrs_tile", "item_id"])[
            "cloud_cover_percent_audit_only"
        ]
    ]
    aggregated["source_item_ids_audit_only"] = "|".join(mosaic_item_ids)
    aggregated["source_mgrs_tiles_audit_only"] = "|".join(mosaic_mgrs_tiles)
    aggregated["product_generation_time_audit_only"] = str(
        acquisition_row.generation_time
    )
    aggregated["tile_cloud_cover_percent_audit_only"] = json.dumps(cloud_values)
    aggregated["union_city_coverage_fraction_audit_only"] = float(
        acquisition_row.union_city_coverage_fraction
    )
    aggregated["calibration_sha256_audit_only"] = mosaic_calibration_sha256s[0]
    aggregated["optical_grid_sha256_audit_only"] = spatial.optical_grid.sha256
    aggregated["static_land_mask_sha256_audit_only"] = spatial.locks[
        "static_land_mask_sha256"
    ]
    aggregated["eligible_pixel_identity_sha256_audit_only"] = aggregated[
        "tract_geoid"
    ].map(spatial.eligible_identity_sha256s)
    output_path = directory / "acquisition_tract.parquet"
    atomic_parquet(aggregated, output_path)
    scl_counts = np.bincount(mosaic_scl.ravel(), minlength=12)
    summary: dict[str, Any] = {
        "state": "complete",
        "cache_lock": expected_lock,
        "physical_acquisition_id": physical_id,
        "acquisition_local_date": str(acquisition_row.acquisition_local_date),
        "platform": str(acquisition_row.platform),
        "processing_baseline": str(acquisition_row.processing_baseline),
        "item_ids": list(mosaic_item_ids),
        "mgrs_tiles": list(mosaic_mgrs_tiles),
        "owned_optical_grid_pixels": list(mosaic_owned_pixel_counts),
        "calibration_sha256s": list(mosaic_calibration_sha256s),
        "product_metadata": metadata_records,
        "global_scene_cloud_cover_filter": None,
        "accepted_scl_classes": list(qa["accepted_scl_classes"]),
        "scl_class_pixel_counts_audit_only": {
            str(index): int(value) for index, value in enumerate(scl_counts) if value
        },
        "clear_land_pixel_count": int(base_valid.sum()),
        "joint_five_index_valid_pixel_count": joint_valid_pixel_count,
        "tract_count": len(aggregated),
        "qualifying_tract_count": int(aggregated["acquisition_qualifies_coverage"].sum()),
        "output_file": parquet_file_record(output_path, aggregated),
    }
    atomic_json(summary, summary_path)
    return summary


def _pipeline_fingerprint(project_root: Path) -> tuple[str, dict[str, Any]]:
    return code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )


def _unlink(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _compile_outputs(
    *,
    inventory: FrozenSentinelInputs,
    spatial: FixedSpatialSupport,
    stage: SentinelStageConfig,
    research: ResearchConfig,
    base_lock: dict[str, str],
    output_directory: Path,
) -> dict[str, Any]:
    acquisition_frames: list[pd.DataFrame] = []
    current_ids: list[str] = []
    for row in inventory.acquisitions.itertuples(index=False):
        physical_id = str(row.physical_acquisition_id)
        item_rows = inventory.items.loc[
            inventory.items["physical_acquisition_id"] == physical_id
        ]
        directory = _acquisition_cache_directory(output_directory, physical_id)
        expected = _expected_acquisition_lock(
            base_lock=base_lock, physical_id=physical_id, item_rows=item_rows
        )
        if _acquisition_cache_is_current(directory, expected_lock=expected):
            acquisition_frames.append(pd.read_parquet(directory / "acquisition_tract.parquet"))
            current_ids.append(physical_id)

    expected_count = len(inventory.acquisitions)
    complete = len(acquisition_frames) == expected_count
    promoted = [
        output_directory / "acquisition_tract.parquet",
        output_directory / "sentinel_features.parquet",
        output_directory / "sentinel_feature_audit.parquet",
        output_directory / "sentinel_lineage.parquet",
    ]
    partial = output_directory / "acquisition_tract_partial.parquet"
    progress: dict[str, Any] = {
        **base_lock,
        "sentinel_stage_config_payload": stage.raw,
        "sentinel_research_dependency_payload": _research_dependency_payload(research),
        "state": "building" if not complete else "compiling",
        "promoted_outputs_valid": False,
        "expected_physical_acquisition_count": expected_count,
        "completed_physical_acquisition_count": len(acquisition_frames),
        "build_complete": complete,
        "completed_physical_acquisition_ids_sha256": canonical_sha256(current_ids),
    }
    if not acquisition_frames:
        _unlink([*promoted, partial])
        progress["state"] = "no_current_acquisition_caches"
        atomic_json(progress, output_directory / "build_progress.json")
        return progress
    acquisition_tract = pd.concat(acquisition_frames, ignore_index=True)
    if acquisition_tract.duplicated(
        ["tract_geoid", "physical_acquisition_id"]
    ).any():
        raise ValueError("Compiled acquisition table contains duplicate keys.")
    if not complete:
        _unlink(promoted)
        atomic_parquet(acquisition_tract, partial)
        progress["state"] = "partial_ready"
        progress["partial_output"] = parquet_file_record(partial, acquisition_tract)
        atomic_json(progress, output_directory / "build_progress.json")
        return progress

    partial.unlink(missing_ok=True)
    composites = build_previous_60_day_composites(
        acquisition_tract,
        inventory.membership,
        target_dates=spatial.target_dates,
        tract_geoids=spatial.tract_geoids,
        minimum_acquisition_coverage=stage.minimum_coverage,
        minimum_acquisitions=stage.minimum_acquisitions,
        final_test_year=research.final_test_year,
        unlock_final_test=research.final_test_unlocked,
    )
    atomic_parquet(acquisition_tract, promoted[0])
    atomic_parquet(composites.features, promoted[1])
    atomic_parquet(composites.audit, promoted[2])
    atomic_parquet(composites.lineage, promoted[3])
    progress["aggregate_outputs"] = {
        path.name: parquet_file_record(path, frame)
        for path, frame in zip(
            promoted,
            [acquisition_tract, composites.features, composites.audit, composites.lineage],
            strict=True,
        )
    }
    progress["state"] = "complete"
    progress["promoted_outputs_valid"] = True
    progress["feature_row_count"] = len(composites.features)
    progress["feature_available_row_count"] = int(
        composites.audit["sentinel_feature_available"].sum()
    )
    progress["target_date_count"] = len(spatial.target_dates)
    progress["tract_count"] = len(spatial.tract_geoids)
    progress["lineage_row_count"] = len(composites.lineage)
    atomic_json(progress, output_directory / "build_progress.json")
    return progress


def run_sentinel_feature_build(
    research_config_path: str | Path,
    stage_config_path: str | Path,
    *,
    limit: int | None = None,
    physical_acquisition_id: str | None = None,
    force: bool = False,
    compile_only: bool = False,
) -> dict[str, Any]:
    """Build or resume the complete frozen Sentinel optical feature stage."""

    project_root = Path(__file__).resolve().parents[2]
    research = load_config(research_config_path)
    stage = load_sentinel_stage_config(stage_config_path)
    paths = stage.raw["paths"]
    inventory = _load_frozen_sentinel_inventory(
        _resolve_project_path(project_root, paths["inventory_directory"]),
        research=research,
    )
    spatial = _load_fixed_spatial_support(
        project_root=project_root,
        research=research,
        stage=stage,
        inventory=inventory,
    )
    output_directory = _resolve_project_path(project_root, paths["output_directory"])
    output_directory.mkdir(parents=True, exist_ok=True)
    pipeline_sha, pipeline_payload = _pipeline_fingerprint(project_root)
    fingerprint_path = output_directory / "pipeline_fingerprint.json"
    atomic_json(pipeline_payload, fingerprint_path)
    base_lock = {
        "sentinel_feature_pipeline_sha256": pipeline_sha,
        "sentinel_feature_pipeline_fingerprint_file_sha256": sha256_file(
            fingerprint_path
        ),
        "sentinel_stage_config_sha256": stage.sha256,
        "sentinel_research_dependency_sha256": canonical_sha256(
            _research_dependency_payload(research)
        ),
        "research_config_file_sha256_audit_only": sha256_file(research.path),
        **inventory.locks,
        **spatial.locks,
    }
    atomic_json(
        {
            **base_lock,
            "state": "building",
            "promoted_outputs_valid": False,
            "expected_physical_acquisition_count": len(inventory.acquisitions),
            "sentinel_stage_config_payload": stage.raw,
            "sentinel_research_dependency_payload": _research_dependency_payload(
                research
            ),
            "sentinel_feature_pipeline_fingerprint": pipeline_payload,
        },
        output_directory / "build_progress.json",
    )
    if compile_only:
        return _compile_outputs(
            inventory=inventory,
            spatial=spatial,
            stage=stage,
            research=research,
            base_lock=base_lock,
            output_directory=output_directory,
        )

    selected = inventory.acquisitions
    if physical_acquisition_id is not None:
        selected = selected.loc[
            selected["physical_acquisition_id"] == physical_acquisition_id
        ]
        if selected.empty:
            raise ValueError("Requested physical acquisition is not in the frozen inventory.")
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive.")
        selected = selected.head(limit)
    raw_metadata_directory = project_root / "data/raw/sentinel/product_metadata"
    with requests.Session() as session:
        for row in selected.itertuples(index=False):
            physical_id = str(row.physical_acquisition_id)
            print(f"[sentinel] process physical acquisition {physical_id}", flush=True)
            item_rows = inventory.items.loc[
                inventory.items["physical_acquisition_id"] == physical_id
            ]
            _process_acquisition(
                row,
                item_rows=item_rows,
                spatial=spatial,
                stage=stage,
                base_lock=base_lock,
                output_directory=output_directory,
                raw_metadata_directory=raw_metadata_directory,
                session=session,
                force=force,
            )
    return _compile_outputs(
        inventory=inventory,
        spatial=spatial,
        stage=stage,
        research=research,
        base_lock=base_lock,
        output_directory=output_directory,
    )


def _central_window(source: rasterio.DatasetReader, lon: float, lat: float, size: int) -> Window:
    xs, ys = transform("EPSG:4326", source.crs, [lon], [lat])
    row, column = source.index(xs[0], ys[0])
    row_off = max(0, min(source.height - size, row - size // 2))
    col_off = max(0, min(source.width - size, column - size // 2))
    return Window(col_off=col_off, row_off=row_off, width=size, height=size)


def run_real_cog_smoke(
    research_config_path: str | Path,
    stage_config_path: str | Path,
    *,
    item_id: str | None = None,
    window_size: int = 64,
) -> dict[str, Any]:
    """Read real metadata plus small B04/SCL COG windows; never emit model features."""

    if not 16 <= window_size <= 512:
        raise ValueError("Smoke window size must be between 16 and 512 pixels.")
    project_root = Path(__file__).resolve().parents[2]
    research = load_config(research_config_path)
    stage = load_sentinel_stage_config(stage_config_path)
    paths = stage.raw["paths"]
    inventory_directory = _resolve_project_path(project_root, paths["inventory_directory"])
    inventory = _load_frozen_sentinel_inventory(inventory_directory, research=research)
    candidates = inventory.items.loc[
        inventory.items["processing_baseline"].map(processing_baseline_key) >= (4, 0)
    ]
    if item_id is not None:
        candidates = inventory.items.loc[inventory.items["item_id"] == item_id]
    if candidates.empty:
        raise ValueError("No matching frozen Sentinel item is available for smoke testing.")
    item = candidates.sort_values(["acquisition_local_date", "mgrs_tile"]).iloc[0]
    city = gpd.read_file(_resolve_project_path(project_root, paths["city_boundary"]))
    grid_config = stage.raw["grid"]
    optical_grid = build_fixed_grid(
        city,
        target_crs=str(grid_config["crs"]),
        resolution_m=float(grid_config["resolution_m"]),
        anchor_x_m=float(grid_config["edge_anchor_x_m"]),
        anchor_y_m=float(grid_config["edge_anchor_y_m"]),
    )
    centroid = city.to_crs("EPSG:3310").geometry.union_all().centroid
    centroid_wgs84 = gpd.GeoSeries([centroid], crs="EPSG:3310").to_crs("EPSG:4326").iloc[0]
    raw_directory = project_root / "data/raw/sentinel/product_metadata"
    with requests.Session() as session:
        metadata, metadata_sha, metadata_path = _read_product_metadata(
            item_id=str(item["item_id"]),
            unsigned_url=str(item["asset_product_metadata_href"]),
            raw_metadata_directory=raw_directory,
            session=session,
        )
    calibration = parse_boa_calibration(
        metadata, processing_baseline=str(item["processing_baseline"])
    )
    asset_summaries: dict[str, Any] = {}
    arrays: dict[str, np.ndarray] = {}
    for asset_name, url_column in (("B04", "asset_b04_href"), ("SCL", "asset_scl_href")):
        signed_url = pc.sign_url(str(item[url_column]))
        with rasterio.Env(**_raster_environment()):
            with rasterio.open(signed_url) as source:
                _validate_native_asset_grid(
                    source,
                    grid=optical_grid,
                    categorical=asset_name == "SCL",
                )
                window = _central_window(
                    source, centroid_wgs84.x, centroid_wgs84.y, window_size
                )
                array = source.read(1, window=window)
                arrays[asset_name] = array
                asset_summaries[asset_name] = {
                    "crs": source.crs.to_string(),
                    "shape": [source.height, source.width],
                    "resolution": [float(source.res[0]), float(source.res[1])],
                    "dtype": str(source.dtypes[0]),
                    "nodata": source.nodata,
                    "window": [
                        int(window.col_off),
                        int(window.row_off),
                        int(window.width),
                        int(window.height),
                    ],
                    "window_sha256": hashlib.sha256(array.tobytes()).hexdigest(),
                }
    decoded = decode_boa_reflectance(
        arrays["B04"],
        band="B04",
        calibration=calibration,
        nodata_dn=int(stage.raw["qa"]["nodata_dn"]),
        saturated_dn=int(stage.raw["qa"]["saturated_dn"]),
    )
    finite = decoded[np.isfinite(decoded)]
    summary = {
        "state": "complete",
        "smoke_only_not_model_features": True,
        "run_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "item_id": str(item["item_id"]),
        "physical_acquisition_id": str(item["physical_acquisition_id"]),
        "processing_baseline": calibration.processing_baseline,
        "calibration_sha256": calibration.sha256,
        "metadata_path": metadata_path.as_posix(),
        "metadata_sha256": metadata_sha,
        "window_size": window_size,
        "native_grid_phase_validated": True,
        "optical_grid_definition_sha256": optical_grid.sha256,
        "assets": asset_summaries,
        "decoded_b04_finite_count": int(finite.size),
        "decoded_b04_min": float(finite.min()) if finite.size else None,
        "decoded_b04_max": float(finite.max()) if finite.size else None,
        "scl_4_5_count_in_independent_scl_window": int(
            np.isin(arrays["SCL"], [4, 5]).sum()
        ),
        "global_scene_cloud_cover_filter": None,
        "target_values_read": False,
        "sentinel_inventory_semantic_sha256": inventory.locks[
            "sentinel_inventory_semantic_sha256"
        ],
        "sentinel_stage_config_sha256": stage.sha256,
        "albedo_proxy": {
            "formula": "sum(coefficient_band * BOA_reflectance_band)",
            "coefficients": stage.albedo_coefficients,
            "intercept": 0.0,
            "reference_doi": "10.1109/LGRS.2020.2967085",
        },
    }
    output_directory = _resolve_project_path(project_root, paths["output_directory"])
    atomic_json(summary, output_directory / "real_cog_smoke.json")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-config", default="configs/research.toml")
    parser.add_argument("--stage-config", default="configs/sentinel_features.toml")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--physical-acquisition-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--real-cog-smoke", action="store_true")
    parser.add_argument("--smoke-item-id")
    parser.add_argument("--smoke-window-size", type=int, default=64)
    arguments = parser.parse_args()
    if arguments.real_cog_smoke:
        result = run_real_cog_smoke(
            arguments.research_config,
            arguments.stage_config,
            item_id=arguments.smoke_item_id,
            window_size=arguments.smoke_window_size,
        )
    else:
        result = run_sentinel_feature_build(
            arguments.research_config,
            arguments.stage_config,
            limit=arguments.limit,
            physical_acquisition_id=arguments.physical_acquisition_id,
            force=arguments.force,
            compile_only=arguments.compile_only,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
