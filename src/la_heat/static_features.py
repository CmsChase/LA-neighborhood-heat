"""Build target-blind, one-row-per-GEOID static predictor features.

This stage consumes only frozen tract geometry/grid/support locks plus public
static source rasters and coastline geometry.  It never opens a target-value
table.  Every numerator is evaluated on the exact WorldCover-derived eligible
land support used by target construction, and every source must cover at least
the configured fraction of that fixed denominator for every tract.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.merge import merge
from rasterio.warp import reproject

from la_heat.config import ResearchConfig, load_config
from la_heat.feature_registry import validate_feature_registry
from la_heat.grid import FixedGrid
from la_heat.landmask import get_static_land_item, read_static_land_mask
from la_heat.landsat import zonal_mask_identity_hashes
from la_heat.nlcd_sources import (
    NLCD_2016_IMPERVIOUS,
    NLCD_2016_LAND_COVER,
    NLCD_SOURCE_COMMIT_MARKER,
    validate_nlcd_subset,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import (
    static_feature_config_payload,
    static_feature_config_sha256,
    target_config_sha256,
)
from la_heat.static_sources import (
    STATIC_SOURCE_SPECS,
    STATIC_SOURCES_COMMIT_MARKER,
    validate_source_file,
)

STATIC_FEATURE_SCHEMA_VERSION = 1
STATIC_FEATURE_ALGORITHM_VERSION = "static-features-fixed-support-v1"
STATIC_FEATURE_COMMIT_MARKER = "static_features_provenance.json"
NLCD_REFERENCE_CATEGORY = "nlcd_developed_medium_fraction"
STATIC_FEATURE_FILES = (
    "pyproject.toml",
    "configs/research.toml",
    "src/la_heat/config.py",
    "src/la_heat/feature_registry.py",
    "src/la_heat/grid.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/nlcd_sources.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
    "src/la_heat/static_features.py",
    "src/la_heat/static_sources.py",
)


class StaticFeatureAuditError(ValueError):
    """Raised when a static feature invariant cannot be proven."""


class StaticFeatureCoverageError(StaticFeatureAuditError):
    """Raised when any source misses the predeclared per-tract coverage gate."""


@dataclass(frozen=True, slots=True)
class StaticSupport:
    """Compact group index for the exact fixed eligible-land cell support."""

    geoids: tuple[str, ...]
    sorted_flat_indices: np.ndarray
    counts: np.ndarray
    offsets: np.ndarray
    identity_sha256: tuple[str, ...]
    shape: tuple[int, int]

    @property
    def zone_count(self) -> int:
        return len(self.geoids)


@dataclass(frozen=True, slots=True)
class TargetSupport:
    tracts: gpd.GeoDataFrame
    grid: FixedGrid
    zones: np.ndarray
    eligible_land: np.ndarray
    support: StaticSupport
    locks: dict[str, object]


@dataclass(frozen=True, slots=True)
class StaticArrays:
    land_cover: np.ndarray
    land_cover_valid: np.ndarray
    impervious_fraction: np.ndarray
    impervious_valid: np.ndarray
    elevation_m: np.ndarray
    elevation_valid: np.ndarray
    slope_degrees: np.ndarray
    slope_valid: np.ndarray
    coast_distance_km: np.ndarray
    coast_distance_valid: np.ndarray


def build_static_support(
    zones: np.ndarray,
    eligible_land: np.ndarray,
    *,
    geoids: list[str] | tuple[str, ...],
    grid_identity: str,
) -> StaticSupport:
    """Index eligible cells once and preserve each tract's exact denominator."""

    zone_values = np.asarray(zones)
    eligible = np.asarray(eligible_land)
    normalized_geoids = tuple(str(value) for value in geoids)
    if zone_values.ndim != 2 or eligible.shape != zone_values.shape:
        raise ValueError("Zone and eligible-land arrays must share one 2-D shape.")
    if eligible.dtype != np.bool_:
        raise TypeError("Eligible-land support must be boolean.")
    if not normalized_geoids or len(set(normalized_geoids)) != len(normalized_geoids):
        raise ValueError("GEOIDs must be non-empty and unique.")
    zone_count = len(normalized_geoids)
    present = np.unique(zone_values[zone_values > 0])
    if not np.array_equal(present, np.arange(1, zone_count + 1)):
        raise ValueError("Zone raster must contain every zone ID exactly in 1..N.")
    selected = eligible & (zone_values > 0)
    flat_indices = np.flatnonzero(selected).astype("<i8", copy=False)
    selected_zones = zone_values.ravel()[flat_indices]
    order = np.argsort(selected_zones, kind="stable")
    sorted_indices = flat_indices[order]
    counts = np.bincount(selected_zones, minlength=zone_count + 1)[1:].astype(np.int64)
    if np.any(counts <= 0):
        missing = [normalized_geoids[index] for index in np.flatnonzero(counts <= 0)[:10]]
        raise StaticFeatureAuditError(f"Tracts lack fixed eligible-land cells: {missing}")
    offsets = np.concatenate((np.array([0], dtype=np.int64), np.cumsum(counts)))
    identities = zonal_mask_identity_hashes(
        zone_values,
        selected,
        zone_count=zone_count,
        grid_identity=grid_identity,
    )
    return StaticSupport(
        geoids=normalized_geoids,
        sorted_flat_indices=sorted_indices,
        counts=counts,
        offsets=offsets,
        identity_sha256=tuple(identities),
        shape=zone_values.shape,
    )


def horn_slope_degrees(
    padded_elevation_m: np.ndarray,
    *,
    pixel_width_m: float,
    pixel_height_m: float,
) -> np.ndarray:
    """Return Horn 3x3 slope for the unpadded interior, preserving NoData."""

    elevation = np.asarray(padded_elevation_m, dtype=np.float64)
    if elevation.ndim != 2 or min(elevation.shape) < 3:
        raise ValueError("Horn slope requires a 2-D raster at least 3x3.")
    if not math.isfinite(pixel_width_m) or not math.isfinite(pixel_height_m):
        raise ValueError("Slope pixel dimensions must be finite.")
    if pixel_width_m <= 0 or pixel_height_m <= 0:
        raise ValueError("Slope pixel dimensions must be positive.")
    z1 = elevation[:-2, :-2]
    z2 = elevation[:-2, 1:-1]
    z3 = elevation[:-2, 2:]
    z4 = elevation[1:-1, :-2]
    z5 = elevation[1:-1, 1:-1]
    z6 = elevation[1:-1, 2:]
    z7 = elevation[2:, :-2]
    z8 = elevation[2:, 1:-1]
    z9 = elevation[2:, 2:]
    valid = np.logical_and.reduce(
        [np.isfinite(item) for item in (z1, z2, z3, z4, z5, z6, z7, z8, z9)]
    )
    dz_dx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * pixel_width_m)
    dz_dy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * pixel_height_m)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    slope[~valid] = np.nan
    return slope


def _counts_on_support(mask: np.ndarray, support: StaticSupport) -> np.ndarray:
    selected = np.asarray(mask, dtype=bool).ravel()[support.sorted_flat_indices]
    cumulative = np.concatenate((np.array([0], dtype=np.int64), np.cumsum(selected)))
    return cumulative[support.offsets[1:]] - cumulative[support.offsets[:-1]]


def _group_statistic(
    values: np.ndarray,
    valid: np.ndarray,
    support: StaticSupport,
    function: Callable[[np.ndarray], float],
) -> np.ndarray:
    flat_values = np.asarray(values).ravel()[support.sorted_flat_indices]
    flat_valid = np.asarray(valid, dtype=bool).ravel()[support.sorted_flat_indices]
    output = np.full(support.zone_count, np.nan, dtype=np.float64)
    for index, (start, end) in enumerate(
        zip(support.offsets[:-1], support.offsets[1:], strict=True)
    ):
        selected = flat_values[start:end][flat_valid[start:end]]
        if selected.size:
            output[index] = function(selected.astype(np.float64, copy=False))
    return output


def _quantile(values: np.ndarray, quantile: float, method: str) -> float:
    return float(np.quantile(values, quantile, method=method))


def aggregate_static_features(
    *,
    arrays: StaticArrays,
    support: StaticSupport,
    land_groups: dict[str, list[int]],
    minimum_coverage_fraction: float,
    std_ddof: int,
    quantile_method: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate aligned arrays without ever replacing the fixed denominator."""

    if not 0 < minimum_coverage_fraction <= 1:
        raise ValueError("Minimum static source coverage must be in (0, 1].")
    if std_ddof < 0:
        raise ValueError("Standard-deviation ddof must be nonnegative.")
    all_arrays = (
        arrays.land_cover,
        arrays.land_cover_valid,
        arrays.impervious_fraction,
        arrays.impervious_valid,
        arrays.elevation_m,
        arrays.elevation_valid,
        arrays.slope_degrees,
        arrays.slope_valid,
        arrays.coast_distance_km,
        arrays.coast_distance_valid,
    )
    if any(np.asarray(array).shape != support.shape for array in all_arrays):
        raise ValueError("Every static array must share the fixed support grid shape.")
    validity = {
        "nlcd_land_cover": arrays.land_cover_valid,
        "nlcd_impervious": arrays.impervious_valid,
        "srtm_elevation": arrays.elevation_valid,
        "srtm_slope": arrays.slope_valid,
        "census_coast_distance": arrays.coast_distance_valid,
    }
    audit = pd.DataFrame(
        {
            "tract_geoid": support.geoids,
            "eligible_pixel_count_static": support.counts,
            "eligible_pixel_identity_sha256": support.identity_sha256,
        }
    )
    failed: list[tuple[str, str, float]] = []
    for source_name, valid_mask in validity.items():
        counts = _counts_on_support(valid_mask, support)
        fractions = counts / support.counts
        audit[f"{source_name}_valid_pixel_count"] = counts
        audit[f"{source_name}_coverage_fraction"] = fractions
        for index in np.flatnonzero(fractions < minimum_coverage_fraction)[:10]:
            failed.append((support.geoids[index], source_name, float(fractions[index])))
    if failed:
        detail = ", ".join(
            f"{geoid}/{source}={fraction:.4f}" for geoid, source, fraction in failed[:10]
        )
        raise StaticFeatureCoverageError(
            f"Static source coverage is below {minimum_coverage_fraction:.3f}: {detail}"
        )

    features = pd.DataFrame({"tract_geoid": support.geoids})
    covered_classes: set[int] = set()
    land = np.asarray(arrays.land_cover)
    land_valid = np.asarray(arrays.land_cover_valid, dtype=bool)
    for group_name, classes in land_groups.items():
        normalized = {int(value) for value in classes}
        if not normalized or covered_classes.intersection(normalized):
            raise StaticFeatureAuditError(
                "NLCD land-cover groups must be non-empty and mutually exclusive."
            )
        covered_classes.update(normalized)
        numerator = _counts_on_support(land_valid & np.isin(land, list(normalized)), support)
        features[f"nlcd_{group_name}_fraction"] = numerator / support.counts
    remainder = land_valid & ~np.isin(land, list(covered_classes))
    audit["nlcd_remainder_pixel_count"] = _counts_on_support(remainder, support)
    audit["nlcd_remainder_fraction"] = (
        audit["nlcd_remainder_pixel_count"].to_numpy() / support.counts
    )

    impervious = np.asarray(arrays.impervious_fraction, dtype=np.float64)
    features["impervious_mean_fraction"] = _group_statistic(
        impervious, arrays.impervious_valid, support, np.mean
    )
    features["impervious_p90_fraction"] = _group_statistic(
        impervious,
        arrays.impervious_valid,
        support,
        lambda values: _quantile(values, 0.90, quantile_method),
    )
    features["impervious_at_least_50_fraction"] = (
        _counts_on_support(arrays.impervious_valid & (impervious >= 0.50), support)
        / support.counts
    )

    elevation = np.asarray(arrays.elevation_m, dtype=np.float64)
    features["elevation_mean_m"] = _group_statistic(
        elevation, arrays.elevation_valid, support, np.mean
    )
    features["elevation_std_m"] = _group_statistic(
        elevation,
        arrays.elevation_valid,
        support,
        lambda values: float(np.std(values, ddof=std_ddof)),
    )
    slope = np.asarray(arrays.slope_degrees, dtype=np.float64)
    features["slope_mean_degrees"] = _group_statistic(
        slope, arrays.slope_valid, support, np.mean
    )
    features["slope_p90_degrees"] = _group_statistic(
        slope,
        arrays.slope_valid,
        support,
        lambda values: _quantile(values, 0.90, quantile_method),
    )
    coast = np.asarray(arrays.coast_distance_km, dtype=np.float64)
    features["pacific_coast_distance_mean_km"] = _group_statistic(
        coast, arrays.coast_distance_valid, support, np.mean
    )
    features["pacific_coast_distance_p10_km"] = _group_statistic(
        coast,
        arrays.coast_distance_valid,
        support,
        lambda values: _quantile(values, 0.10, quantile_method),
    )
    if features["tract_geoid"].duplicated().any() or features.isna().any().any():
        raise StaticFeatureAuditError("Static model table has duplicate keys or missing values.")
    return features, audit


def _load_json_commit(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"Required commit marker is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("state") != "complete":
        raise StaticFeatureAuditError(f"Commit marker is not complete: {path}")
    recorded = payload.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(payload) != recorded:
        raise StaticFeatureAuditError(f"Commit marker hash is invalid: {path}")
    payload["commit_sha256"] = recorded
    return payload


def _fixed_grid_from_lock(payload: dict[str, object]) -> FixedGrid:
    bounds = payload.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise StaticFeatureAuditError("Fixed-grid lock has invalid bounds.")
    left, bottom, right, top = (float(value) for value in bounds)
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
        transform=Affine(resolution, 0.0, left, 0.0, -resolution, top),
    )
    if grid.sha256 != payload.get("grid_definition_sha256"):
        raise StaticFeatureAuditError("Reconstructed fixed grid failed its definition hash.")
    return grid


def load_target_support(config: ResearchConfig, target_directory: Path) -> TargetSupport:
    """Rebuild and verify target support without opening any target-value table."""

    grid_lock_path = target_directory / "fixed_grid_lock.json"
    progress_path = target_directory / "build_progress.json"
    manifest_path = target_directory / "primary_tract_manifest.parquet"
    if not grid_lock_path.is_file() or not progress_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Frozen target grid, progress, and tract manifest are required.")
    grid_lock = json.loads(grid_lock_path.read_text(encoding="utf-8"))
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("state") != "model_ready" or not progress.get("promoted_outputs_valid"):
        raise StaticFeatureAuditError("Target support is not from a complete promoted build.")
    expected_target_config = target_config_sha256(config)
    if grid_lock.get("target_config_sha256") != expected_target_config:
        raise StaticFeatureAuditError(
            "Target grid lock disagrees with current target configuration."
        )
    if progress.get("target_config_sha256") != expected_target_config:
        raise StaticFeatureAuditError(
            "Target progress disagrees with current target configuration."
        )
    grid = _fixed_grid_from_lock(grid_lock)
    tracts = gpd.read_parquet(manifest_path)
    required = {"GEOID", "geometry", "tract_manifest_sha256", "primary_included"}
    missing = required - set(tracts.columns)
    if missing or tracts.empty or tracts.crs is None:
        raise StaticFeatureAuditError(
            f"Primary tract manifest is invalid; missing={sorted(missing)}"
        )
    tracts = tracts.reset_index(drop=True)
    geoids = tracts["GEOID"].astype(str)
    if geoids.duplicated().any() or not tracts["primary_included"].all():
        raise StaticFeatureAuditError("Primary tract manifest has duplicate/excluded GEOIDs.")
    manifest_hashes = tracts["tract_manifest_sha256"].astype(str).unique()
    if len(manifest_hashes) != 1 or manifest_hashes[0] != progress.get(
        "tract_manifest_sha256"
    ):
        raise StaticFeatureAuditError("Primary tract manifest failed its semantic lock.")
    projected = tracts.to_crs(grid.crs)
    zones = rasterize(
        ((geometry, index + 1) for index, geometry in enumerate(projected.geometry)),
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    zone_hash = hashlib.sha256(zones.tobytes()).hexdigest()
    if zone_hash != grid_lock.get("zone_raster_sha256"):
        raise StaticFeatureAuditError("Rebuilt tract zone raster failed its target-stage lock.")
    worldcover_item = get_static_land_item(config)
    eligible_land = read_static_land_mask(
        worldcover_item,
        output_shape=grid.shape,
        output_transform=grid.transform,
        output_crs=grid.crs,
        config=config,
    )
    land_hash = hashlib.sha256(np.packbits(eligible_land.ravel()).tobytes()).hexdigest()
    if land_hash != grid_lock.get("static_land_mask_sha256"):
        raise StaticFeatureAuditError("Rebuilt WorldCover eligible-land mask failed its lock.")
    grid_identity = f"{grid.sha256}|zone={zone_hash}|land={land_hash}"
    identity_hash = hashlib.sha256(grid_identity.encode()).hexdigest()
    if identity_hash != grid_lock.get("target_grid_identity_sha256"):
        raise StaticFeatureAuditError("Combined fixed-support identity failed its lock.")
    support = build_static_support(
        zones,
        eligible_land,
        geoids=geoids.tolist(),
        grid_identity=grid_identity,
    )
    locks = {
        "target_grid_identity_sha256": identity_hash,
        "grid_definition_sha256": grid.sha256,
        "zone_raster_sha256": zone_hash,
        "static_land_mask_sha256": land_hash,
        "tract_manifest_sha256": manifest_hashes[0],
        "tract_manifest_file_sha256": sha256_file(manifest_path),
        "target_config_sha256": expected_target_config,
        "worldcover_collection": config.raw["static_land_mask"]["collection"],
        "worldcover_item_id": worldcover_item.id,
        "worldcover_asset": config.raw["static_land_mask"]["asset"],
    }
    return TargetSupport(tracts, grid, zones, eligible_land, support, locks)


def _padded_grid(grid: FixedGrid, padding: int) -> tuple[tuple[int, int], Affine]:
    if padding < 0:
        raise ValueError("Grid padding cannot be negative.")
    shape = (grid.height + 2 * padding, grid.width + 2 * padding)
    transform = grid.transform * Affine.translation(-padding, -padding)
    return shape, transform


def _reproject_values(
    path: Path,
    *,
    grid: FixedGrid,
    padding: int,
    valid_source: Callable[[np.ndarray], np.ndarray],
    resampling: Resampling,
) -> np.ndarray:
    destination_shape, destination_transform = _padded_grid(grid, padding)
    destination = np.full(destination_shape, np.nan, dtype=np.float32)
    with rasterio.open(path) as source:
        raw = source.read(1)
        valid = np.asarray(valid_source(raw), dtype=bool)
        if valid.shape != raw.shape:
            raise StaticFeatureAuditError(f"Source validity mask shape mismatch: {path}")
        values = np.where(valid, raw, np.nan).astype(np.float32)
        reproject(
            source=values,
            destination=destination,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=np.nan,
            dst_transform=destination_transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=resampling,
            init_dest_nodata=True,
            num_threads=2,
        )
    return destination


def _reproject_mosaic(
    paths: list[Path],
    *,
    grid: FixedGrid,
    padding: int,
    nodata_value: float,
    resampling: Resampling,
) -> np.ndarray:
    """Merge same-grid source tiles before reprojection to avoid seam extrapolation."""

    destination_shape, destination_transform = _padded_grid(grid, padding)
    destination = np.full(destination_shape, np.nan, dtype=np.float32)
    with ExitStack() as stack:
        sources = [stack.enter_context(rasterio.open(path)) for path in paths]
        if not sources or any(source.crs != sources[0].crs for source in sources):
            raise StaticFeatureAuditError("SRTM mosaic sources require one shared CRS.")
        merged, source_transform = merge(
            sources,
            nodata=nodata_value,
            dtype="float32",
            method="first",
        )
        values = merged[0]
        values[values == nodata_value] = np.nan
        reproject(
            source=values,
            destination=destination,
            src_transform=source_transform,
            src_crs=sources[0].crs,
            src_nodata=np.nan,
            dst_transform=destination_transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=resampling,
            init_dest_nodata=True,
            num_threads=2,
        )
    return destination


def coast_distance_on_support(
    *,
    coast: gpd.GeoDataFrame,
    grid: FixedGrid,
    eligible_land: np.ndarray,
    search_buffer_km: float,
    chunk_size: int = 100_000,
) -> np.ndarray:
    """Calculate exact nearest-line distances only for fixed eligible cells."""

    if coast.empty or coast.crs is None:
        raise StaticFeatureAuditError("A non-empty georeferenced coastline is required.")
    if search_buffer_km <= 0 or chunk_size <= 0:
        raise ValueError("Coast search buffer and chunk size must be positive.")
    projected = coast.to_crs(grid.crs)
    projected = projected.loc[
        projected.geometry.notna() & ~projected.geometry.is_empty
    ].copy()
    buffer_m = search_buffer_km * 1000.0
    search_box = shapely.box(
        grid.left - buffer_m,
        grid.bottom - buffer_m,
        grid.right + buffer_m,
        grid.top + buffer_m,
    )
    projected = projected.loc[projected.geometry.intersects(search_box)]
    if projected.empty:
        raise StaticFeatureAuditError("No coastline geometry intersects the search window.")
    tree = shapely.STRtree(projected.geometry.to_numpy())
    selected_indices = np.flatnonzero(np.asarray(eligible_land, dtype=bool).ravel())
    output = np.full(grid.shape[0] * grid.shape[1], np.nan, dtype=np.float32)
    for start in range(0, selected_indices.size, chunk_size):
        flat = selected_indices[start : start + chunk_size]
        rows = flat // grid.width
        columns = flat % grid.width
        x = grid.transform.c + (columns + 0.5) * grid.transform.a
        y = grid.transform.f + (rows + 0.5) * grid.transform.e
        points = shapely.points(x, y)
        indices, distances = tree.query_nearest(
            points, all_matches=False, return_distance=True
        )
        query_indices = indices[0] if indices.ndim == 2 else np.arange(flat.size)
        local = np.full(flat.size, np.nan, dtype=np.float64)
        local[query_indices] = distances
        output[flat] = (local / 1000.0).astype(np.float32)
    result = output.reshape(grid.shape)
    selected_distances = result.ravel()[selected_indices]
    if not np.isfinite(selected_distances).all():
        raise StaticFeatureAuditError("Nearest-coast query omitted eligible cells.")
    if float(selected_distances.max()) >= search_buffer_km:
        raise StaticFeatureAuditError(
            "Coast search buffer is not wider than every retained nearest distance."
        )
    return result


def _validate_raw_source_commits(raw_directory: Path) -> dict[str, object]:
    static_commit = _load_json_commit(raw_directory / STATIC_SOURCES_COMMIT_MARKER)
    nlcd_commit = _load_json_commit(raw_directory / NLCD_SOURCE_COMMIT_MARKER)
    static_records = static_commit.get("sources")
    nlcd_records = nlcd_commit.get("sources")
    if not isinstance(static_records, dict) or not isinstance(nlcd_records, dict):
        raise StaticFeatureAuditError("Raw source commit lacks source records.")
    for spec in STATIC_SOURCE_SPECS:
        path = raw_directory / spec.filename
        validate_source_file(spec, path)
        record = static_records.get(spec.source_id)
        if not isinstance(record, dict) or record.get("sha256") != sha256_file(path):
            raise StaticFeatureAuditError(f"Static source record mismatch: {spec.source_id}")
    for spec in (NLCD_2016_LAND_COVER, NLCD_2016_IMPERVIOUS):
        path = raw_directory / spec.filename
        validate_nlcd_subset(path, spec)
        record = nlcd_records.get(spec.source_id)
        validation = record.get("validation") if isinstance(record, dict) else None
        if not isinstance(validation, dict) or validation.get("sha256") != sha256_file(path):
            raise StaticFeatureAuditError(f"NLCD source record mismatch: {spec.source_id}")
    return {
        "static_sources_commit_sha256": static_commit["commit_sha256"],
        "nlcd_sources_commit_sha256": nlcd_commit["commit_sha256"],
    }


def _read_static_arrays(
    *,
    config: ResearchConfig,
    target: TargetSupport,
    raw_directory: Path,
) -> StaticArrays:
    settings = config.raw["static_features"]
    categorical = Resampling[settings["categorical_resampling"]]
    continuous = Resampling[settings["continuous_resampling"]]
    land = _reproject_values(
        raw_directory / settings["nlcd_land_cover_filename"],
        grid=target.grid,
        padding=0,
        valid_source=lambda values: values != settings["nlcd_land_cover_nodata"],
        resampling=categorical,
    )
    land_valid = np.isfinite(land)
    land_cover = np.where(land_valid, np.rint(land), 0).astype(np.int16)
    impervious = _reproject_values(
        raw_directory / settings["nlcd_impervious_filename"],
        grid=target.grid,
        padding=0,
        valid_source=lambda values: values != settings["nlcd_impervious_nodata"],
        resampling=continuous,
    )
    impervious_valid = np.isfinite(impervious) & (impervious >= 0) & (impervious <= 100)
    impervious_fraction = np.where(impervious_valid, impervious / 100.0, np.nan)
    padding = int(settings["slope_edge_padding_pixels"])
    if settings["slope_operator"] != "horn_3x3" or padding != 1:
        raise StaticFeatureAuditError("Static slope requires horn_3x3 and one-cell padding.")
    elevation_padded = _reproject_mosaic(
        [raw_directory / filename for filename in settings["srtm_filenames"]],
        grid=target.grid,
        padding=padding,
        nodata_value=float(settings["srtm_nodata"]),
        resampling=continuous,
    )
    elevation = elevation_padded[padding:-padding, padding:-padding]
    slope = horn_slope_degrees(
        elevation_padded,
        pixel_width_m=target.grid.resolution_m,
        pixel_height_m=target.grid.resolution_m,
    )
    coast_path = raw_directory / settings["coastline_filename"]
    coast = gpd.read_file(f"zip://{coast_path.resolve()}!{settings['coastline_layer']}")
    if "MTFCC" not in coast.columns:
        raise StaticFeatureAuditError("Census coastline lacks MTFCC.")
    coast = coast.loc[coast["MTFCC"].astype(str) == settings["coastline_mtfcc"]]
    coast_distance = coast_distance_on_support(
        coast=coast,
        grid=target.grid,
        eligible_land=target.eligible_land & (target.zones > 0),
        search_buffer_km=float(settings["coastline_search_buffer_km"]),
    )
    return StaticArrays(
        land_cover=land_cover,
        land_cover_valid=land_valid,
        impervious_fraction=impervious_fraction,
        impervious_valid=impervious_valid,
        elevation_m=elevation,
        elevation_valid=np.isfinite(elevation),
        slope_degrees=slope,
        slope_valid=np.isfinite(slope),
        coast_distance_km=coast_distance,
        coast_distance_valid=np.isfinite(coast_distance),
    )


def static_feature_registry(features: pd.DataFrame) -> pd.DataFrame:
    """Create and validate the registry fragment for static model columns."""

    metadata: dict[str, tuple[str, str, str, str]] = {}
    for column in features.columns:
        if column.startswith("nlcd_"):
            metadata[column] = (
                "land_use",
                "fraction",
                "NLCD 2016 original land cover",
                "2019-04-30",
            )
        elif column.startswith("impervious_"):
            metadata[column] = (
                "land_use",
                "fraction",
                "NLCD 2016 original imperviousness",
                "2019-04-30",
            )
        elif column.startswith("elevation_"):
            metadata[column] = (
                "geography",
                "metres",
                "NASA SRTMGL1 v003",
                "2015-08-01",
            )
        elif column.startswith("slope_"):
            metadata[column] = (
                "geography",
                "degrees",
                "NASA SRTMGL1 v003 derived Horn slope",
                "2015-08-01",
            )
        elif column.startswith("pacific_coast_"):
            metadata[column] = (
                "geography",
                "kilometres",
                "Census TIGER/Line 2019 U.S. Coastline",
                "2019-08-09",
            )
    rows: list[dict[str, object]] = [
        {
            "feature_name": "tract_geoid",
            "family": "key",
            "role": "key",
            "units": "identifier",
            "source": "Census 2020 tract manifest",
            "static": True,
            "available_by": "2019-08-09",
            "source_start_offset_days": np.nan,
            "source_end_offset_days": np.nan,
        },
        {
            "feature_name": "target_date",
            "family": "key",
            "role": "key",
            "units": "date",
            "source": "Target-date join key only",
            "static": False,
            "available_by": "target date",
            "source_start_offset_days": np.nan,
            "source_end_offset_days": np.nan,
        },
    ]
    for column in features.columns:
        if column == "tract_geoid":
            continue
        family, units, source, available_by = metadata[column]
        role = "audit_only" if column == NLCD_REFERENCE_CATEGORY else "model"
        rows.append(
            {
                "feature_name": column,
                "family": family,
                "role": role,
                "units": units,
                "source": source,
                "static": True,
                "available_by": available_by,
                "source_start_offset_days": np.nan,
                "source_end_offset_days": np.nan,
            }
        )
    registry = pd.DataFrame(rows)
    validate_feature_registry(registry, development_start="2020-05-01")
    return registry


def _pipeline_fingerprint() -> tuple[str, dict[str, Any]]:
    project_root = Path(__file__).resolve().parents[2]
    return code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=STATIC_FEATURE_FILES,
        algorithm_version=STATIC_FEATURE_ALGORITHM_VERSION,
    )


def build_static_feature_table(config_path: str | Path) -> dict[str, object]:
    """Run the complete target-blind static feature stage and commit outputs."""

    config = load_config(config_path)
    settings = config.raw["static_features"]
    output_directory = Path(settings["output_directory"])
    output_directory.mkdir(parents=True, exist_ok=True)
    marker = output_directory / STATIC_FEATURE_COMMIT_MARKER
    marker.unlink(missing_ok=True)
    raw_directory = Path(settings["raw_static_directory"])
    source_locks = _validate_raw_source_commits(raw_directory)
    target = load_target_support(config, Path(settings["target_support_directory"]))
    arrays = _read_static_arrays(config=config, target=target, raw_directory=raw_directory)
    features, audit = aggregate_static_features(
        arrays=arrays,
        support=target.support,
        land_groups={
            str(name): [int(value) for value in values]
            for name, values in settings["nlcd_land_cover_groups"].items()
        },
        minimum_coverage_fraction=float(settings["minimum_source_coverage_fraction"]),
        std_ddof=int(settings["continuous_std_ddof"]),
        quantile_method=str(settings["quantile_method"]),
    )
    registry = static_feature_registry(features)
    registered_table_columns = set(registry["feature_name"]) - {"target_date"}
    if registered_table_columns != set(features.columns):
        raise StaticFeatureAuditError(
            "Static feature registry must cover every feature-table column exactly."
        )
    feature_path = output_directory / "static_features.parquet"
    audit_path = output_directory / "static_feature_audit.parquet"
    registry_path = output_directory / "static_feature_registry.csv"
    atomic_parquet(features, feature_path)
    atomic_parquet(audit, audit_path)
    atomic_csv(registry, registry_path)
    pipeline_sha256, pipeline_payload = _pipeline_fingerprint()
    coverage_columns = [column for column in audit if column.endswith("_coverage_fraction")]
    payload: dict[str, object] = {
        "schema_version": STATIC_FEATURE_SCHEMA_VERSION,
        "algorithm_version": STATIC_FEATURE_ALGORITHM_VERSION,
        "state": "complete",
        "promoted_outputs_valid": True,
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "output_directory": str(output_directory.resolve()),
        "row_count": len(features),
        "unique_geoid_count": int(features["tract_geoid"].nunique()),
        "model_feature_count": int(registry["role"].eq("model").sum()),
        "audit_only_feature_count": int(registry["role"].eq("audit_only").sum()),
        "contains_date_column": False,
        "contains_2025_rows": False,
        "minimum_source_coverage_fraction": float(
            settings["minimum_source_coverage_fraction"]
        ),
        "minimum_observed_coverage_by_source": {
            column.removesuffix("_coverage_fraction"): float(audit[column].min())
            for column in coverage_columns
        },
        "eligible_pixel_count_total": int(target.support.counts.sum()),
        "eligible_count_min": int(target.support.counts.min()),
        "eligible_count_max": int(target.support.counts.max()),
        "target_support_locks": target.locks,
        "raw_source_locks": source_locks,
        "static_feature_config_sha256": static_feature_config_sha256(config),
        "static_feature_config_payload": static_feature_config_payload(config),
        "research_config_file_sha256": sha256_file(config.path),
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "semantic_feature_table_sha256": canonical_frame_sha256(
            features, sort_by=["tract_geoid"]
        ),
        "semantic_audit_table_sha256": canonical_frame_sha256(
            audit, sort_by=["tract_geoid"]
        ),
        "feature_registry_semantic_sha256": canonical_frame_sha256(
            registry, sort_by=["feature_name"]
        ),
        "output_files": {
            feature_path.name: parquet_file_record(feature_path, features),
            audit_path.name: parquet_file_record(audit_path, audit),
            registry_path.name: {
                "sha256": sha256_file(registry_path),
                "bytes": registry_path.stat().st_size,
                "rows": len(registry),
            },
        },
        "scientific_rules": {
            "support": "fixed WorldCover-derived eligible-land target-grid cells",
            "land_cover_fraction_denominator": "all fixed eligible-land cells",
            "impervious_threshold_fraction_denominator": "all fixed eligible-land cells",
            "continuous_statistics": "valid fixed-support cells after 98% tract gate",
            "nlcd_land_cover_nodata": int(settings["nlcd_land_cover_nodata"]),
            "nlcd_impervious_nodata": int(settings["nlcd_impervious_nodata"]),
            "srtm_nodata": int(settings["srtm_nodata"]),
            "slope": "Horn 3x3 on one-cell-padded aligned elevation",
            "std_ddof": int(settings["continuous_std_ddof"]),
            "quantile_method": str(settings["quantile_method"]),
            "nlcd_reference_category": NLCD_REFERENCE_CATEGORY,
            "nlcd_reference_category_role": "audit_only",
            "nlcd_reference_category_reason": (
                "Target-blind static-source diagnostic found all ten exhaustive "
                "land-cover fractions summed to one for every tract. Developed medium "
                "was nonzero for every tract and had the largest median fraction, so it "
                "is retained for audit but excluded from the model to remove exact "
                "intercept collinearity."
            ),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload
