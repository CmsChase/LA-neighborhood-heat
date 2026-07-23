"""Aggregate a fixed-grid Landsat quality mosaic to tract-date targets."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from la_heat.config import ResearchConfig
from la_heat.landsat import zonal_mask_identity_hashes
from la_heat.mosaic import MosaicResult
from la_heat.targets import assign_relative_endpoints


@dataclass(frozen=True, slots=True)
class TargetAggregationResult:
    tract_date_qa: pd.DataFrame
    scene_contributions: pd.DataFrame
    summary: dict[str, object]


def aggregate_target_mosaic(
    *,
    tracts: gpd.GeoDataFrame,
    zone_raster: np.ndarray,
    static_land_mask: np.ndarray,
    mosaic: MosaicResult,
    target_date: str,
    overpass_id: str,
    platform: str,
    scene_ids: tuple[str, ...],
    union_city_coverage_fraction: float,
    grid_identity: str,
    config_sha256: str,
    tract_manifest_sha256: str,
    config: ResearchConfig,
) -> TargetAggregationResult:
    """Build auditable absolute and gated relative targets after scene mosaicking."""

    shape = zone_raster.shape
    aligned_arrays = (
        static_land_mask,
        mosaic.selected_st_value,
        mosaic.selected_valid,
        mosaic.selected_st_qa,
        mosaic.selected_cdist,
        mosaic.footprint,
    )
    if any(np.asarray(array).shape != shape for array in aligned_arrays):
        raise ValueError("Zone, land, and mosaic arrays must share the fixed grid.")
    if len(tracts) == 0:
        raise ValueError("At least one tract is required for target aggregation.")
    zone_count = len(tracts)
    present_zones = np.unique(zone_raster[zone_raster > 0])
    if not np.array_equal(present_zones, np.arange(1, zone_count + 1)):
        raise ValueError("Zone raster must contain every tract ID exactly in 1..N.")

    landsat = config.raw["landsat"]
    validation = config.raw["validation"]
    rasterized = zone_raster > 0
    eligible = rasterized & static_land_mask
    footprint = rasterized & mosaic.footprint
    valid = eligible & mosaic.selected_valid

    def counts(mask: np.ndarray) -> np.ndarray:
        return np.bincount(zone_raster[mask], minlength=zone_count + 1)[1:]

    rasterized_counts = counts(rasterized)
    footprint_counts = counts(footprint)
    eligible_counts = counts(eligible)
    valid_counts = counts(valid)
    eligible_identity_hashes = zonal_mask_identity_hashes(
        zone_raster,
        eligible,
        zone_count=zone_count,
        grid_identity=grid_identity,
    )

    valid_frame = pd.DataFrame(
        {
            "zone": zone_raster[valid],
            "lst_c": mosaic.selected_st_value[valid],
            "uncertainty_k": mosaic.selected_st_qa[valid],
            "cloud_distance_km": mosaic.selected_cdist[valid],
            "scene_id": mosaic.selected_scene_id[valid],
        }
    )
    grouped = valid_frame.groupby("zone", sort=True)
    statistics = grouped.agg(
        target_lst_c=("lst_c", "median"),
        mean_lst_c=("lst_c", "mean"),
        std_lst_c=("lst_c", "std"),
        median_st_uncertainty_k=("uncertainty_k", "median"),
        median_cloud_distance_km=("cloud_distance_km", "median"),
    )
    statistics["p10_lst_c"] = grouped["lst_c"].quantile(0.10)
    statistics["p90_lst_c"] = grouped["lst_c"].quantile(0.90)
    statistics["p90_st_uncertainty_k"] = grouped["uncertainty_k"].quantile(0.90)

    result = pd.DataFrame(
        {
            "tract_geoid": tracts["GEOID"].to_numpy(),
            "spatial_block": tracts["spatial_block"].to_numpy(),
            "latitude_quartile": tracts["latitude_quartile"].to_numpy(),
            "longitude_quartile": tracts["longitude_quartile"].to_numpy(),
            "rasterized_pixel_count": rasterized_counts,
            "footprint_pixel_count": footprint_counts,
            "eligible_pixel_count_static": eligible_counts,
            "eligible_pixel_identity_sha256": eligible_identity_hashes,
            "valid_pixel_count": valid_counts,
        }
    )
    result["footprint_fraction"] = np.divide(
        footprint_counts,
        rasterized_counts,
        out=np.full(zone_count, np.nan, dtype=float),
        where=rasterized_counts > 0,
    )
    result["valid_fraction"] = np.divide(
        valid_counts,
        eligible_counts,
        out=np.full(zone_count, np.nan, dtype=float),
        where=eligible_counts > 0,
    )
    result["zone"] = np.arange(1, zone_count + 1)
    result = result.join(statistics, on="zone").drop(columns="zone")

    retained = (
        (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_fraction"] >= landsat["minimum_valid_pixel_fraction"])
        & (result["valid_pixel_count"] >= landsat["minimum_valid_pixels_per_tract"])
    )
    result["tract_exclusion_reason"] = ""
    result.loc[result["eligible_pixel_count_static"] == 0, "tract_exclusion_reason"] = (
        "no_static_eligible_land"
    )
    result.loc[
        (result["eligible_pixel_count_static"] > 0)
        & (result["footprint_fraction"] < landsat["minimum_tract_footprint_fraction"]),
        "tract_exclusion_reason",
    ] = "insufficient_scene_footprint"
    result.loc[
        (result["eligible_pixel_count_static"] > 0)
        & (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_pixel_count"] < landsat["minimum_valid_pixels_per_tract"]),
        "tract_exclusion_reason",
    ] = "insufficient_valid_pixels"
    result.loc[
        (result["valid_pixel_count"] >= landsat["minimum_valid_pixels_per_tract"])
        & (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_fraction"] < landsat["minimum_valid_pixel_fraction"]),
        "tract_exclusion_reason",
    ] = "insufficient_valid_fraction"

    target_columns = [
        "target_lst_c",
        "mean_lst_c",
        "std_lst_c",
        "p10_lst_c",
        "p90_lst_c",
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
        "median_cloud_distance_km",
    ]
    result.loc[~retained, target_columns] = np.nan
    result["target_available"] = retained
    result, relative_summary = assign_relative_endpoints(
        result,
        hotspot_fraction=1.0 - float(validation["hotspot_quantile"]),
        minimum_tract_fraction=validation["minimum_relative_endpoint_tract_fraction"],
        maximum_quartile_retention_gap=validation[
            "maximum_relative_endpoint_quartile_retention_gap"
        ],
        minimum_joint_cell_tracts=validation["minimum_relative_joint_cell_tracts"],
        minimum_joint_cell_retention_fraction=validation[
            "minimum_relative_joint_cell_retention_fraction"
        ],
    )
    minimum_city_coverage = landsat["minimum_city_union_coverage_fraction"]
    date_usable = bool(
        union_city_coverage_fraction >= minimum_city_coverage
        and retained.mean() >= landsat["minimum_date_tract_retention_fraction"]
    )
    date_exclusion_reason = ""
    if union_city_coverage_fraction < minimum_city_coverage:
        date_exclusion_reason = "insufficient_union_city_footprint"
    elif retained.mean() < landsat["minimum_date_tract_retention_fraction"]:
        date_exclusion_reason = "insufficient_date_tract_retention"

    result.insert(1, "target_date", target_date)
    result.insert(2, "overpass_id", overpass_id)
    result.insert(3, "platform", platform)
    result.insert(4, "source_scene_count", len(scene_ids))
    result.insert(5, "source_scene_ids", "|".join(scene_ids))
    result["date_usable"] = date_usable
    result["date_exclusion_reason"] = date_exclusion_reason
    result["config_sha256"] = config_sha256
    result["tract_manifest_sha256"] = tract_manifest_sha256
    result["grid_sha256"] = hashlib.sha256(grid_identity.encode()).hexdigest()

    contributions = (
        valid_frame.groupby(["zone", "scene_id"], sort=True)
        .size()
        .rename("selected_valid_pixel_count")
        .reset_index()
    )
    zone_to_geoid = pd.Series(tracts["GEOID"].to_numpy(), index=np.arange(1, zone_count + 1))
    contributions["tract_geoid"] = contributions["zone"].map(zone_to_geoid)
    contributions.insert(1, "target_date", target_date)
    contributions.insert(2, "overpass_id", overpass_id)
    contributions = contributions.drop(columns="zone")

    retained_values = result.loc[retained, "target_lst_c"]
    summary = {
        "target_date": target_date,
        "overpass_id": overpass_id,
        "platform": platform,
        "scene_ids": list(scene_ids),
        "scene_count": len(scene_ids),
        "union_city_coverage_fraction": union_city_coverage_fraction,
        "tract_count": zone_count,
        "retained_tract_count": int(retained.sum()),
        "retained_tract_fraction": float(retained.mean()),
        "date_usable": date_usable,
        "date_exclusion_reason": date_exclusion_reason,
        "relative_endpoint_coverage_pass": relative_summary.coverage_pass,
        "minimum_eligible_joint_cell_retention_fraction": (
            relative_summary.minimum_eligible_joint_cell_retention_fraction
        ),
        "relative_hotspot_count": relative_summary.hotspot_count,
        "median_target_lst_c": float(retained_values.median()),
        "p05_target_lst_c": float(retained_values.quantile(0.05)),
        "p95_target_lst_c": float(retained_values.quantile(0.95)),
        "grid_sha256": hashlib.sha256(grid_identity.encode()).hexdigest(),
        "zone_raster_sha256": hashlib.sha256(zone_raster.tobytes()).hexdigest(),
        "eligible_mask_sha256": hashlib.sha256(
            np.packbits(eligible.ravel()).tobytes()
        ).hexdigest(),
        "config_sha256": config_sha256,
        "tract_manifest_sha256": tract_manifest_sha256,
    }
    return TargetAggregationResult(result, contributions, summary)
