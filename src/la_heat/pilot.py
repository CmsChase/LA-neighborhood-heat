"""Run the target-data feasibility pilot using remote Landsat COG windows."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import planetary_computer as pc
import rasterio
import shapely
from pystac import Item
from pystac_client import Client
from rasterio.features import rasterize
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from la_heat.boundaries import (
    assign_spatial_blocks,
    download_detailed_la_county_tracts,
    fetch_city_boundary,
    load_city_tracts,
)
from la_heat.config import ResearchConfig, load_config
from la_heat.guardrails import (
    validate_no_final_year_rows,
    validate_static_eligible_denominator,
    validate_target_qa_contract,
    validate_unique_primary_key,
)
from la_heat.landmask import get_static_land_item, read_static_land_mask
from la_heat.landsat import (
    landsat_st_dn_to_celsius,
    physically_plausible_lst_mask,
    qa_pixel_clear_land_mask,
    zonal_mask_identity_hashes,
)
from la_heat.targets import assign_relative_endpoints

REQUIRED_ASSETS = ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat")


@dataclass(frozen=True)
class SceneCandidate:
    item: Item
    local_date: date
    city_coverage_fraction: float
    cloud_cover_percent: float


def _without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _config_hash(config: ResearchConfig) -> str:
    return hashlib.sha256(config.path.read_bytes()).hexdigest()


def _scene_candidates(
    config: ResearchConfig,
    city_boundary: gpd.GeoDataFrame,
    *,
    start_date: str,
    end_date: str,
    maximum_scene_cloud_cover_percent: float | None,
) -> list[SceneCandidate]:
    landsat = config.raw["landsat"]
    study = config.raw["study"]
    client = Client.open(landsat["stac_api"])
    query: dict[str, dict[str, object]] = {
        "landsat:collection_category": {"eq": "T1"},
        "landsat:correction": {"eq": "L2SP"},
    }
    if (
        maximum_scene_cloud_cover_percent is not None
        and maximum_scene_cloud_cover_percent < 100
    ):
        query["eo:cloud_cover"] = {"lt": maximum_scene_cloud_cover_percent}
    search = client.search(
        collections=[landsat["collection"]],
        bbox=study["bbox_wgs84"],
        datetime=f"{start_date}/{end_date}",
        query=query,
    )

    analysis_crs = study["crs_analysis"]
    city = city_boundary.to_crs(analysis_crs).geometry.union_all()
    timezone = ZoneInfo(study["timezone"])
    warm_months = set(study["warm_season_months"])
    allowed_sensors = set(landsat["sensors"])
    candidates: list[SceneCandidate] = []
    for item in search.items():
        if item.properties.get("platform") not in allowed_sensors:
            continue
        if not all(asset in item.assets for asset in REQUIRED_ASSETS):
            continue
        local_date = item.datetime.astimezone(timezone).date()
        if local_date.month not in warm_months:
            continue
        scene = gpd.GeoSeries([shapely.geometry.shape(item.geometry)], crs="EPSG:4326")
        scene_projected = scene.to_crs(analysis_crs).iloc[0]
        coverage = scene_projected.intersection(city).area / city.area
        if coverage < 0.98:
            continue
        candidates.append(
            SceneCandidate(
                item=item,
                local_date=local_date,
                city_coverage_fraction=float(coverage),
                cloud_cover_percent=float(item.properties.get("eo:cloud_cover", np.nan)),
            )
        )

    # Keep only the best single scene for each local date.
    best_by_date: dict[date, SceneCandidate] = {}
    for candidate in candidates:
        previous = best_by_date.get(candidate.local_date)
        score = (-candidate.city_coverage_fraction, candidate.cloud_cover_percent)
        if previous is None:
            best_by_date[candidate.local_date] = candidate
            continue
        previous_score = (-previous.city_coverage_fraction, previous.cloud_cover_percent)
        if score < previous_score:
            best_by_date[candidate.local_date] = candidate
    return sorted(best_by_date.values(), key=lambda candidate: candidate.local_date)


def _select_evenly_spaced(
    candidates: list[SceneCandidate], number_of_dates: int
) -> list[SceneCandidate]:
    if len(candidates) < number_of_dates:
        raise ValueError(
            f"Only {len(candidates)} full-city candidate dates; requested {number_of_dates}."
        )
    indices = np.rint(np.linspace(0, len(candidates) - 1, number_of_dates)).astype(int)
    if len(set(indices.tolist())) != number_of_dates:
        raise RuntimeError("Even scene selection produced duplicate indices.")
    return [candidates[index] for index in indices]


def _read_scene_arrays(
    item: Item,
    city_bounds_wgs84: tuple[float, float, float, float],
) -> tuple[dict[str, np.ndarray], rasterio.Affine, object]:
    signed = pc.sign(item)
    arrays: dict[str, np.ndarray] = {}
    reference_transform = None
    reference_source_transform = None
    reference_crs = None
    reference_window = None
    reference_shape = None

    environment = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif",
    }
    with rasterio.Env(**environment):
        for asset_key in REQUIRED_ASSETS:
            with rasterio.open(signed.assets[asset_key].href) as source:
                if reference_window is None:
                    projected_bounds = transform_bounds(
                        "EPSG:4326", source.crs, *city_bounds_wgs84, densify_pts=21
                    )
                    proposed = from_bounds(*projected_bounds, transform=source.transform)
                    full = Window(0, 0, source.width, source.height)
                    reference_window = proposed.intersection(full).round_offsets().round_lengths()
                    reference_transform = source.window_transform(reference_window)
                    reference_source_transform = source.transform
                    reference_crs = source.crs
                    reference_shape = (
                        int(reference_window.height),
                        int(reference_window.width),
                    )
                else:
                    if (
                        source.crs != reference_crs
                        or source.transform != reference_source_transform
                    ):
                        raise ValueError(f"Asset grid mismatch in {item.id}: {asset_key}")
                array = source.read(1, window=reference_window)
                if array.shape != reference_shape:
                    raise ValueError(f"Unexpected shape for {item.id}/{asset_key}: {array.shape}")
                arrays[asset_key] = array

    assert reference_transform is not None and reference_crs is not None
    return arrays, reference_transform, reference_crs


def _aggregate_scene(
    candidate: SceneCandidate,
    tracts: gpd.GeoDataFrame,
    city_bounds_wgs84: tuple[float, float, float, float],
    config: ResearchConfig,
    static_land_item: Item,
    static_land_cache: dict[tuple[object, ...], np.ndarray],
) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame, pd.DataFrame]:
    arrays, transform, raster_crs = _read_scene_arrays(candidate.item, city_bounds_wgs84)
    projected_tracts = tracts.to_crs(raster_crs).reset_index(drop=True)
    zone_raster = rasterize(
        ((geometry, index + 1) for index, geometry in enumerate(projected_tracts.geometry)),
        out_shape=arrays["lwir11"].shape,
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    land_cache_key: tuple[object, ...] = (
        str(raster_crs),
        *tuple(transform),
        *arrays["lwir11"].shape,
    )
    if land_cache_key not in static_land_cache:
        static_land_cache[land_cache_key] = read_static_land_mask(
            static_land_item,
            output_shape=arrays["lwir11"].shape,
            output_transform=transform,
            output_crs=raster_crs,
            config=config,
        )
    static_land = static_land_cache[land_cache_key]

    landsat = config.raw["landsat"]
    qa_pixel = arrays["qa_pixel"]
    st_dn = arrays["lwir11"]
    uncertainty_k = arrays["qa"].astype(np.float64) * 0.01
    cloud_distance_km = arrays["cdist"].astype(np.float64) * 0.01
    qa_radsat = arrays["qa_radsat"].astype(np.uint16)
    lst_c = landsat_st_dn_to_celsius(
        st_dn,
        scale_kelvin=float(landsat["lst_scale_kelvin"]),
        offset_kelvin=float(landsat["lst_offset_kelvin"]),
    )

    rasterized = zone_raster > 0
    is_fill = (qa_pixel.astype(np.uint16) & (1 << 0)) != 0
    scene_footprint = rasterized & ~is_fill
    eligible = rasterized & static_land
    eligible_in_footprint = eligible & ~is_fill
    clear_land = qa_pixel_clear_land_mask(
        qa_pixel, excluded_bits=tuple(landsat["excluded_qa_pixel_bits"])
    )
    valid_dn = (st_dn >= landsat["minimum_st_dn"]) & (
        st_dn <= landsat["maximum_st_dn"]
    )
    valid_uncertainty_value = arrays["qa"] != -9999
    valid_cloud_distance_value = arrays["cdist"] != -9999
    terrain_visible = np.ones_like(eligible, dtype=bool)
    if landsat["exclude_terrain_occlusion"]:
        terrain_visible = (qa_radsat & (1 << 11)) == 0
    common_valid = (
        eligible_in_footprint
        & clear_land
        & valid_dn
        & valid_uncertainty_value
        & valid_cloud_distance_value
        & terrain_visible
        & physically_plausible_lst_mask(lst_c)
    )
    if landsat["apply_st_uncertainty_threshold"]:
        valid_uncertainty = (
            uncertainty_k <= landsat["maximum_st_uncertainty_kelvin"]
        )
    else:
        valid_uncertainty = np.ones_like(common_valid, dtype=bool)
    valid_cloud_distance = (
        cloud_distance_km >= landsat["minimum_cloud_distance_km"]
    )
    valid = common_valid & valid_uncertainty & valid_cloud_distance

    zone_count = len(projected_tracts)
    rasterized_counts = np.bincount(
        zone_raster[rasterized], minlength=zone_count + 1
    )[1:]
    footprint_counts = np.bincount(
        zone_raster[scene_footprint], minlength=zone_count + 1
    )[1:]
    total_counts = np.bincount(zone_raster[eligible], minlength=zone_count + 1)[1:]
    valid_counts = np.bincount(zone_raster[valid], minlength=zone_count + 1)[1:]
    grid_identity = (
        f"{raster_crs}|{tuple(transform)}|{zone_raster.shape[0]}x{zone_raster.shape[1]}"
    )
    eligible_identity_hashes = zonal_mask_identity_hashes(
        zone_raster,
        eligible,
        zone_count=zone_count,
        grid_identity=grid_identity,
    )

    valid_frame = pd.DataFrame(
        {
            "zone": zone_raster[valid],
            "lst_c": lst_c[valid],
            "uncertainty_k": uncertainty_k[valid],
            "cloud_distance_km": cloud_distance_km[valid],
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
    statistics["p90_st_uncertainty_k"] = grouped["uncertainty_k"].quantile(0.90)
    statistics["p10_lst_c"] = grouped["lst_c"].quantile(0.10)
    statistics["p90_lst_c"] = grouped["lst_c"].quantile(0.90)

    result = pd.DataFrame(
        {
            "tract_geoid": projected_tracts["GEOID"].to_numpy(),
            "spatial_block": projected_tracts["spatial_block"].to_numpy(),
            "latitude_quartile": projected_tracts["latitude_quartile"].to_numpy(),
            "longitude_quartile": projected_tracts["longitude_quartile"].to_numpy(),
            "rasterized_pixel_count": rasterized_counts,
            "footprint_pixel_count": footprint_counts,
            "eligible_pixel_count_static": total_counts,
            "eligible_pixel_identity_sha256": eligible_identity_hashes,
            "total_pixel_count": total_counts,
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
        total_counts,
        out=np.full(zone_count, np.nan, dtype=float),
        where=total_counts > 0,
    )
    result["zone"] = np.arange(1, zone_count + 1)
    result = result.join(statistics, on="zone").drop(columns="zone")

    retain = (
        (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_fraction"] >= landsat["minimum_valid_pixel_fraction"])
        & (result["valid_pixel_count"] >= landsat["minimum_valid_pixels_per_tract"])
    )
    result["exclusion_reason"] = ""
    result.loc[result["total_pixel_count"] == 0, "exclusion_reason"] = "no_eligible_pixels"
    result.loc[
        (result["total_pixel_count"] > 0)
        & (result["footprint_fraction"] < landsat["minimum_tract_footprint_fraction"]),
        "exclusion_reason",
    ] = "insufficient_scene_footprint"
    result.loc[
        (result["total_pixel_count"] > 0)
        & (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_pixel_count"] < landsat["minimum_valid_pixels_per_tract"]),
        "exclusion_reason",
    ] = "insufficient_valid_pixels"
    result.loc[
        (result["valid_pixel_count"] >= landsat["minimum_valid_pixels_per_tract"])
        & (result["footprint_fraction"] >= landsat["minimum_tract_footprint_fraction"])
        & (result["valid_fraction"] < landsat["minimum_valid_pixel_fraction"]),
        "exclusion_reason",
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
    result.loc[~retain, target_columns] = np.nan
    validation = config.raw["validation"]
    result, relative_summary = assign_relative_endpoints(
        result,
        hotspot_fraction=1.0 - float(validation["hotspot_quantile"]),
        minimum_tract_fraction=validation[
            "minimum_relative_endpoint_tract_fraction"
        ],
        maximum_quartile_retention_gap=validation[
            "maximum_relative_endpoint_quartile_retention_gap"
        ],
        minimum_joint_cell_tracts=validation["minimum_relative_joint_cell_tracts"],
        minimum_joint_cell_retention_fraction=validation[
            "minimum_relative_joint_cell_retention_fraction"
        ],
    )

    result.insert(1, "target_date", candidate.local_date.isoformat())
    result.insert(2, "scene_id", candidate.item.id)
    result.insert(3, "sensor", candidate.item.properties.get("platform"))
    result.insert(4, "scene_cloud_cover_percent", candidate.cloud_cover_percent)
    result.insert(5, "scene_city_coverage_fraction", candidate.city_coverage_fraction)

    retained_values = result.loc[retain, "target_lst_c"]
    summary: dict[str, object] = {
        "target_date": candidate.local_date.isoformat(),
        "scene_id": candidate.item.id,
        "sensor": candidate.item.properties.get("platform"),
        "scene_cloud_cover_percent": candidate.cloud_cover_percent,
        "scene_city_coverage_fraction": candidate.city_coverage_fraction,
        "tract_count": int(len(result)),
        "retained_tract_count": int(retain.sum()),
        "retained_tract_fraction": float(retain.mean()),
        "median_footprint_fraction": float(result["footprint_fraction"].median()),
        "median_valid_fraction": float(result["valid_fraction"].median()),
        "median_target_lst_c": float(retained_values.median()),
        "p05_target_lst_c": float(retained_values.quantile(0.05)),
        "p95_target_lst_c": float(retained_values.quantile(0.95)),
        "relative_endpoint_coverage_pass": relative_summary.coverage_pass,
        "minimum_spatial_block_retention_fraction": (
            relative_summary.minimum_block_retention_fraction
        ),
        "latitude_quartile_retention_gap": (
            relative_summary.latitude_quartile_retention_gap
        ),
        "longitude_quartile_retention_gap": (
            relative_summary.longitude_quartile_retention_gap
        ),
        "minimum_eligible_joint_cell_retention_fraction": (
            relative_summary.minimum_eligible_joint_cell_retention_fraction
        ),
        "relative_hotspot_count": relative_summary.hotspot_count,
        "relative_hotspot_threshold_c": relative_summary.hotspot_threshold_c,
        "median_label_st_uncertainty_k": float(
            result.loc[retain, "median_st_uncertainty_k"].median()
        ),
        "p90_label_st_uncertainty_k": float(
            result.loc[retain, "p90_st_uncertainty_k"].median()
        ),
        "grid_identity_sha256": hashlib.sha256(grid_identity.encode()).hexdigest(),
        "zone_raster_sha256": hashlib.sha256(zone_raster.tobytes()).hexdigest(),
        "eligible_mask_sha256": hashlib.sha256(
            np.packbits(eligible.ravel()).tobytes()
        ).hexdigest(),
    }

    waterfall_masks = [
        ("tract_rasterized", rasterized),
        ("static_eligible_land", eligible),
        ("static_land_inside_scene", eligible_in_footprint),
        ("qa_clear_land", eligible_in_footprint & clear_land),
        ("valid_st_dn", eligible_in_footprint & clear_land & valid_dn),
        (
            "st_qa_present",
            eligible_in_footprint & clear_land & valid_dn & valid_uncertainty_value,
        ),
    ]
    cumulative = waterfall_masks[-1][1]
    if landsat["apply_st_uncertainty_threshold"]:
        cumulative = cumulative & valid_uncertainty
        waterfall_masks.append(("primary_st_qa_threshold", cumulative))
    cumulative = cumulative & valid_cloud_distance_value
    waterfall_masks.append(("cloud_distance_present", cumulative))
    cumulative = cumulative & valid_cloud_distance
    waterfall_masks.append(("primary_cloud_distance", cumulative))
    cumulative = cumulative & terrain_visible
    waterfall_masks.append(("terrain_visible", cumulative))
    cumulative = cumulative & physically_plausible_lst_mask(lst_c)
    waterfall_masks.append(("physically_plausible_lst", cumulative))

    waterfall_rows: list[dict[str, object]] = []
    previous_count: int | None = None
    rasterized_count = int(rasterized.sum())
    for order, (stage, stage_mask) in enumerate(waterfall_masks):
        pixel_count = int(stage_mask.sum())
        waterfall_rows.append(
            {
                "target_date": candidate.local_date.isoformat(),
                "scene_id": candidate.item.id,
                "stage_order": order,
                "stage": stage,
                "pixel_count": pixel_count,
                "fraction_of_rasterized": pixel_count / rasterized_count,
                "fraction_of_previous": (
                    1.0 if previous_count is None else pixel_count / previous_count
                ),
            }
        )
        previous_count = pixel_count
    waterfall = pd.DataFrame(waterfall_rows)

    sensitivity = _qa_sensitivity_table(
        candidate=candidate,
        projected_tracts=projected_tracts,
        zone_raster=zone_raster,
        common_valid=common_valid,
        eligible=eligible,
        uncertainty_k=uncertainty_k,
        cloud_distance_km=cloud_distance_km,
        footprint_counts=footprint_counts,
        rasterized_counts=rasterized_counts,
        config=config,
    )
    return result, summary, waterfall, sensitivity


def _qa_sensitivity_table(
    *,
    candidate: SceneCandidate,
    projected_tracts: gpd.GeoDataFrame,
    zone_raster: np.ndarray,
    common_valid: np.ndarray,
    eligible: np.ndarray,
    uncertainty_k: np.ndarray,
    cloud_distance_km: np.ndarray,
    footprint_counts: np.ndarray,
    rasterized_counts: np.ndarray,
    config: ResearchConfig,
) -> pd.DataFrame:
    """Summarize QA retention without using target values or model performance."""

    landsat = config.raw["landsat"]
    sensitivity_config = config.raw["pilot"]["qa_sensitivity"]
    thresholds: list[float | None] = [
        float(value)
        for value in sensitivity_config["st_uncertainty_thresholds_kelvin"]
    ]
    if sensitivity_config["include_no_st_uncertainty_threshold"]:
        thresholds.insert(0, None)
    cloud_distances = [
        float(value) for value in sensitivity_config["cloud_distance_thresholds_km"]
    ]

    zone_count = len(projected_tracts)
    eligible_counts = np.bincount(
        zone_raster[eligible], minlength=zone_count + 1
    )[1:]
    footprint_fraction = np.divide(
        footprint_counts,
        rasterized_counts,
        out=np.full(zone_count, np.nan, dtype=float),
        where=rasterized_counts > 0,
    )
    latitude_quartile = projected_tracts["latitude_quartile"].to_numpy()
    longitude_quartile = projected_tracts["longitude_quartile"].to_numpy()

    rows: list[dict[str, object]] = []
    for uncertainty_threshold in thresholds:
        uncertainty_mask = np.ones_like(common_valid, dtype=bool)
        if uncertainty_threshold is not None:
            uncertainty_mask = uncertainty_k <= uncertainty_threshold
        for cloud_distance_threshold in cloud_distances:
            valid = (
                common_valid
                & uncertainty_mask
                & (cloud_distance_km >= cloud_distance_threshold)
            )
            valid_counts = np.bincount(
                zone_raster[valid], minlength=zone_count + 1
            )[1:]
            valid_fraction = np.divide(
                valid_counts,
                eligible_counts,
                out=np.full(zone_count, np.nan, dtype=float),
                where=eligible_counts > 0,
            )
            retained = (
                (footprint_fraction >= landsat["minimum_tract_footprint_fraction"])
                & (valid_fraction >= landsat["minimum_valid_pixel_fraction"])
                & (valid_counts >= landsat["minimum_valid_pixels_per_tract"])
            )

            latitude_rates = pd.Series(retained).groupby(latitude_quartile).mean()
            longitude_rates = pd.Series(retained).groupby(longitude_quartile).mean()
            included_uncertainty = uncertainty_k[valid]
            rows.append(
                {
                    "target_date": candidate.local_date.isoformat(),
                    "scene_id": candidate.item.id,
                    "st_uncertainty_threshold_k": (
                        "none"
                        if uncertainty_threshold is None
                        else f"{uncertainty_threshold:g}"
                    ),
                    "cloud_distance_threshold_km": cloud_distance_threshold,
                    "valid_pixel_fraction_city": float(valid.sum() / eligible.sum()),
                    "median_tract_valid_fraction": float(
                        np.nanmedian(valid_fraction)
                    ),
                    "retained_tract_count": int(retained.sum()),
                    "retained_tract_fraction": float(retained.mean()),
                    "latitude_quartile_retention_gap": float(
                        latitude_rates.max() - latitude_rates.min()
                    ),
                    "longitude_quartile_retention_gap": float(
                        longitude_rates.max() - longitude_rates.min()
                    ),
                    "median_included_st_uncertainty_k": float(
                        np.median(included_uncertainty)
                    ),
                    "p90_included_st_uncertainty_k": float(
                        np.quantile(included_uncertainty, 0.90)
                    ),
                }
            )
    return pd.DataFrame(rows)


def _plot_pilot(
    tracts: gpd.GeoDataFrame,
    targets: pd.DataFrame,
    destination: Path,
) -> None:
    dates = sorted(targets["target_date"].unique())
    valid_values = targets["target_lst_c"].dropna()
    vmin, vmax = valid_values.quantile([0.02, 0.98])
    figure, axes = plt.subplots(2, len(dates), figsize=(5 * len(dates), 9), squeeze=False)
    for column, target_date in enumerate(dates):
        date_data = targets.loc[targets["target_date"] == target_date]
        mapped = tracts.merge(date_data, left_on="GEOID", right_on="tract_geoid", how="left")
        mapped.plot(
            column="target_lst_c",
            ax=axes[0, column],
            cmap="inferno",
            vmin=float(vmin),
            vmax=float(vmax),
            legend=True,
            missing_kwds={"color": "lightgray", "label": "Excluded"},
        )
        mapped.boundary.plot(ax=axes[0, column], color="white", linewidth=0.08, alpha=0.4)
        axes[0, column].set_title(f"LST (°C) — {target_date}")

        mapped.plot(
            column="valid_fraction",
            ax=axes[1, column],
            cmap="viridis",
            vmin=0,
            vmax=1,
            legend=True,
            missing_kwds={"color": "lightgray"},
        )
        mapped.boundary.plot(ax=axes[1, column], color="white", linewidth=0.08, alpha=0.4)
        axes[1, column].set_title(f"Valid-pixel fraction — {target_date}")
        for axis in (axes[0, column], axes[1, column]):
            axis.set_axis_off()
    figure.suptitle("Los Angeles Landsat target-data pilot", fontsize=16)
    figure.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=180, bbox_inches="tight")
    plt.close(figure)


def run_pilot(config_path: str | Path) -> dict[str, object]:
    config = load_config(config_path)
    study = config.raw["study"]
    boundaries = config.raw["boundaries"]
    landsat = config.raw["landsat"]
    pilot = config.raw["pilot"]
    output_directory = Path(pilot["output_directory"])
    output_directory.mkdir(parents=True, exist_ok=True)

    pilot_start = pd.Timestamp(pilot["start_date"])
    pilot_end = pd.Timestamp(pilot["end_date"])
    if pilot_start.year <= config.final_test_year <= pilot_end.year:
        config.require_final_test_access()

    print("[pilot] fetching official City of Los Angeles boundary", flush=True)
    city = fetch_city_boundary(boundaries["la_city_geojson"])
    city.to_file(output_directory / "la_city_boundary.geojson", driver="GeoJSON")

    print("[pilot] acquiring detailed 2020 Census tract geometry", flush=True)
    census_download = download_detailed_la_county_tracts(
        layer_url=boundaries["detailed_tract_arcgis_layer"],
        destination=Path("data/raw/census/la_county_2020_tiger_detailed.parquet"),
        state_fips=boundaries["state_fips"],
        county_fips=boundaries["county_fips"],
        expected_feature_count=boundaries["detailed_tract_expected_count"],
    )
    tract_universe = load_city_tracts(
        census_download.path,
        city,
        analysis_crs=study["crs_analysis"],
        state_fips=boundaries["state_fips"],
        county_fips=boundaries["county_fips"],
        minimum_city_area_fraction=boundaries["minimum_city_area_fraction"],
        exclude_special_use_tracts=boundaries["exclude_special_use_tracts"],
    )
    tract_universe.to_parquet(
        output_directory / "tract_universe_manifest.parquet", index=False
    )
    tract_universe.drop(columns="geometry").to_csv(
        output_directory / "tract_universe_manifest.csv", index=False
    )
    tracts = tract_universe.loc[tract_universe["primary_included"]].copy()
    tracts = assign_spatial_blocks(
        tracts, block_size_km=config.raw["validation"]["spatial_block_size_km"]
    )
    tracts.to_parquet(output_directory / "tract_manifest.parquet", index=False)
    tracts.drop(columns="geometry").to_csv(output_directory / "tract_manifest.csv", index=False)
    print(f"[pilot] selected {len(tracts)} fixed City tracts", flush=True)

    print("[pilot] auditing development-period Landsat metadata", flush=True)
    development_candidates = _scene_candidates(
        config,
        city,
        start_date=study["start_date"],
        end_date=study["development_end_date"],
        maximum_scene_cloud_cover_percent=landsat["scene_cloud_cover_max_percent"],
    )
    pilot_candidates = _scene_candidates(
        config,
        city,
        start_date=pilot["start_date"],
        end_date=pilot["end_date"],
        maximum_scene_cloud_cover_percent=landsat[
            "pilot_scene_cloud_cover_max_percent"
        ],
    )
    selected = _select_evenly_spaced(pilot_candidates, int(pilot["number_of_dates"]))
    print(
        "[pilot] selected scenes: " + ", ".join(candidate.item.id for candidate in selected),
        flush=True,
    )

    city_bounds_wgs84 = tuple(float(value) for value in city.total_bounds)
    target_frames: list[pd.DataFrame] = []
    scene_summaries: list[dict[str, object]] = []
    scene_manifest: list[dict[str, object]] = []
    waterfall_frames: list[pd.DataFrame] = []
    sensitivity_frames: list[pd.DataFrame] = []
    static_land_item = get_static_land_item(config)
    static_land_cache: dict[tuple[object, ...], np.ndarray] = {}
    for candidate in selected:
        print(f"[pilot] aggregating remote COG windows for {candidate.item.id}", flush=True)
        targets, summary, waterfall, sensitivity = _aggregate_scene(
            candidate,
            tracts,
            city_bounds_wgs84,
            config,
            static_land_item,
            static_land_cache,
        )
        target_frames.append(targets)
        scene_summaries.append(summary)
        waterfall_frames.append(waterfall)
        sensitivity_frames.append(sensitivity)
        scene_manifest.append(
            {
                "target_date": candidate.local_date.isoformat(),
                "scene_id": candidate.item.id,
                "sensor": candidate.item.properties.get("platform"),
                "cloud_cover_percent": candidate.cloud_cover_percent,
                "city_coverage_fraction": candidate.city_coverage_fraction,
                **{
                    f"{asset_key}_href": _without_query(candidate.item.assets[asset_key].href)
                    for asset_key in REQUIRED_ASSETS
                },
            }
        )

    all_targets = pd.concat(target_frames, ignore_index=True)
    validate_unique_primary_key(all_targets)
    validate_no_final_year_rows(all_targets, final_year=config.final_test_year)
    validate_static_eligible_denominator(all_targets)
    validate_target_qa_contract(
        all_targets,
        minimum_footprint_fraction=landsat["minimum_tract_footprint_fraction"],
        minimum_valid_fraction=landsat["minimum_valid_pixel_fraction"],
        minimum_valid_pixels=landsat["minimum_valid_pixels_per_tract"],
    )
    all_targets.to_csv(output_directory / "tract_date_targets.csv", index=False)
    pd.DataFrame(scene_manifest).to_csv(output_directory / "scene_manifest.csv", index=False)
    scene_summary_frame = pd.DataFrame(scene_summaries)
    scene_summary_frame.to_csv(output_directory / "scene_summary.csv", index=False)
    waterfall_frame = pd.concat(waterfall_frames, ignore_index=True)
    waterfall_frame.to_csv(output_directory / "qa_mask_waterfall.csv", index=False)
    sensitivity_frame = pd.concat(sensitivity_frames, ignore_index=True)
    sensitivity_frame.to_csv(output_directory / "qa_sensitivity.csv", index=False)

    figure_path = Path("reports/figures/generated/pilot_lst_and_coverage.png")
    _plot_pilot(tracts, all_targets, figure_path)
    table_path = Path("reports/tables/generated/pilot_scene_summary.csv")
    table_path.parent.mkdir(parents=True, exist_ok=True)
    scene_summary_frame.to_csv(table_path, index=False)
    waterfall_frame.to_csv(
        Path("reports/tables/generated/pilot_qa_mask_waterfall.csv"), index=False
    )
    sensitivity_frame.to_csv(
        Path("reports/tables/generated/pilot_qa_sensitivity.csv"), index=False
    )

    plausible = bool(
        scene_summary_frame["median_target_lst_c"].between(5, 65).all()
        and (scene_summary_frame["p05_target_lst_c"] > -10).all()
        and (scene_summary_frame["p95_target_lst_c"] < 80).all()
    )
    coverage_pass = bool((scene_summary_frame["retained_tract_fraction"] >= 0.50).all())
    date_count_pass = len(development_candidates) >= study["minimum_independent_valid_dates"]
    summary = {
        "pilot_status": "PASS" if plausible and coverage_pass and date_count_pass else "REVIEW",
        "config_path": str(config.path),
        "config_sha256": _config_hash(config),
        "final_test_year": config.final_test_year,
        "final_test_unlocked": config.final_test_unlocked,
        "pilot_dates": [candidate.local_date.isoformat() for candidate in selected],
        "pilot_scene_ids": [candidate.item.id for candidate in selected],
        "development_metadata_candidate_date_count": len(development_candidates),
        "minimum_required_independent_dates": study["minimum_independent_valid_dates"],
        "tract_count": int(len(tracts)),
        "tract_universe_count": int(len(tract_universe)),
        "primary_excluded_tract_count": int(
            (~tract_universe["primary_included"]).sum()
        ),
        "plausible_temperature_distributions": plausible,
        "coverage_gate_pass": coverage_pass,
        "metadata_date_count_gate_pass": date_count_pass,
        "qa_sensitivity_rows": int(len(sensitivity_frame)),
        "census_download": {
            "source_href": census_download.source_href,
            "local_path": str(census_download.path),
            "sha256": census_download.sha256,
            "bytes": census_download.bytes_downloaded,
        },
        "static_land_mask": {
            "collection": config.raw["static_land_mask"]["collection"],
            "item_id": static_land_item.id,
            "year": config.raw["static_land_mask"]["year"],
            "water_classes": config.raw["static_land_mask"]["water_classes"],
        },
        "scene_summaries": scene_summaries,
        "limitations": [
            "Landsat thermal information is natively coarser than the delivered 30 m grid.",
            "Clear-sky daytime surface temperature is not air temperature or human heat risk.",
        ],
    }
    (output_directory / "pilot_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    arguments = parser.parse_args()
    run_pilot(arguments.config)


if __name__ == "__main__":
    main()
