"""Stage-specific canonical configuration identities."""

from __future__ import annotations

from typing import Any

from la_heat.config import ResearchConfig
from la_heat.provenance import canonical_sha256


def _select(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    missing = set(keys) - set(source)
    if missing:
        raise ValueError(f"Configuration section lacks locked keys: {sorted(missing)}")
    return {key: source[key] for key in keys}


def inventory_config_payload(config: ResearchConfig) -> dict[str, Any]:
    raw = config.raw
    return {
        "study": _select(
            raw["study"],
            (
                "timezone",
                "crs_analysis",
                "bbox_wgs84",
                "warm_season_months",
                "start_date",
                "development_end_date",
                "final_test_year",
                "minimum_independent_valid_dates",
            ),
        ),
        "boundaries": _select(raw["boundaries"], ("la_city_geojson",)),
        "landsat": _select(
            raw["landsat"],
            (
                "stac_api",
                "collection",
                "sensors",
                "pilot_scene_cloud_cover_max_percent",
                "maximum_overpass_span_minutes",
                "minimum_city_union_coverage_fraction",
            ),
        ),
    }


def inventory_config_sha256(config: ResearchConfig) -> str:
    return canonical_sha256(inventory_config_payload(config))


def target_config_payload(config: ResearchConfig) -> dict[str, Any]:
    raw = config.raw
    return {
        "study": _select(
            raw["study"],
            (
                "crs_analysis",
                "final_test_year",
                "minimum_independent_valid_dates",
            ),
        ),
        "boundaries": _select(
            raw["boundaries"],
            (
                "detailed_tract_arcgis_layer",
                "detailed_tract_expected_count",
                "state_fips",
                "county_fips",
                "minimum_city_area_fraction",
                "exclude_special_use_tracts",
            ),
        ),
        "landsat": _select(
            raw["landsat"],
            (
                "excluded_qa_pixel_bits",
                "lst_scale_kelvin",
                "lst_offset_kelvin",
                "kelvin_to_celsius",
                "minimum_valid_pixel_fraction",
                "minimum_valid_pixels_per_tract",
                "minimum_tract_footprint_fraction",
                "minimum_st_dn",
                "maximum_st_dn",
                "maximum_st_uncertainty_kelvin",
                "apply_st_uncertainty_threshold",
                "minimum_cloud_distance_km",
                "exclude_terrain_occlusion",
                "target_grid_crs",
                "target_grid_resolution_m",
                "target_grid_anchor_x_m",
                "target_grid_anchor_y_m",
                "minimum_date_tract_retention_fraction",
                "minimum_city_union_coverage_fraction",
            ),
        ),
        "static_land_mask": raw["static_land_mask"],
        "validation": _select(
            raw["validation"],
            (
                "spatial_block_size_km",
                "hotspot_quantile",
                "minimum_relative_endpoint_tract_fraction",
                "maximum_relative_endpoint_quartile_retention_gap",
                "minimum_relative_joint_cell_tracts",
                "minimum_relative_joint_cell_retention_fraction",
            ),
        ),
    }


def target_config_sha256(config: ResearchConfig) -> str:
    return canonical_sha256(target_config_payload(config))


def static_feature_config_payload(config: ResearchConfig) -> dict[str, Any]:
    """Return only configuration fields that determine the static feature stage."""

    raw = config.raw
    return {
        "study": _select(
            raw["study"],
            ("crs_analysis", "final_test_year", "unlock_final_test"),
        ),
        "landsat_grid": _select(
            raw["landsat"],
            (
                "target_grid_crs",
                "target_grid_resolution_m",
                "target_grid_anchor_x_m",
                "target_grid_anchor_y_m",
            ),
        ),
        "static_land_mask": raw["static_land_mask"],
        "static_features": raw["static_features"],
    }


def static_feature_config_sha256(config: ResearchConfig) -> str:
    return canonical_sha256(static_feature_config_payload(config))


def daymet_grid_config_payload(config: ResearchConfig) -> dict[str, Any]:
    """Return configuration fields that determine the Daymet grid stage."""

    raw = config.raw
    return {
        "study": _select(
            raw["study"],
            (
                "bbox_wgs84",
                "start_date",
                "development_end_date",
                "final_test_year",
                "unlock_final_test",
                "primary_dynamic_data_latest_offset_days",
            ),
        ),
        "landsat_grid": _select(
            raw["landsat"],
            (
                "target_grid_crs",
                "target_grid_resolution_m",
                "target_grid_anchor_x_m",
                "target_grid_anchor_y_m",
            ),
        ),
        "static_land_mask": raw["static_land_mask"],
        "weather_features": raw["weather_features"],
    }


def daymet_grid_config_sha256(config: ResearchConfig) -> str:
    return canonical_sha256(daymet_grid_config_payload(config))
