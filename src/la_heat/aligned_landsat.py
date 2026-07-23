"""Read and decode Landsat assets on the frozen target grid."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import planetary_computer as pc
import rasterio
from pystac import Item
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from shapely.geometry import box

from la_heat.config import ResearchConfig
from la_heat.grid import FixedGrid
from la_heat.landsat import (
    landsat_st_dn_to_celsius,
    physically_plausible_lst_mask,
    qa_pixel_clear_land_mask,
)

REQUIRED_ASSETS = ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat")
COVERAGE_KEY = "_source_coverage"


@dataclass(frozen=True, slots=True)
class AlignedScene:
    scene_id: str
    lst_c: np.ndarray
    valid: np.ndarray
    st_uncertainty_k: np.ndarray
    cloud_distance_km: np.ndarray
    footprint: np.ndarray


def decode_aligned_scene_arrays(
    *,
    scene_id: str,
    arrays: dict[str, np.ndarray],
    config: ResearchConfig,
) -> AlignedScene:
    """Decode already-nearest-aligned source arrays using the locked target QA."""

    missing = set((*REQUIRED_ASSETS, COVERAGE_KEY)) - set(arrays)
    if missing:
        raise ValueError(f"Aligned scene lacks assets: {sorted(missing)}")
    shape = arrays["lwir11"].shape
    if len(shape) != 2 or any(array.shape != shape for array in arrays.values()):
        raise ValueError("All aligned Landsat arrays must share one 2D shape.")

    landsat = config.raw["landsat"]
    st_dn = arrays["lwir11"]
    qa_pixel = arrays["qa_pixel"]
    st_qa_raw = arrays["qa"]
    cdist_raw = arrays["cdist"]
    qa_radsat = arrays["qa_radsat"].astype(np.uint16)
    source_coverage = arrays[COVERAGE_KEY].astype(bool)
    fill = (qa_pixel.astype(np.uint16) & 1) != 0
    footprint = source_coverage & ~fill
    lst_c = landsat_st_dn_to_celsius(
        st_dn,
        scale_kelvin=float(landsat["lst_scale_kelvin"]),
        offset_kelvin=float(landsat["lst_offset_kelvin"]),
    )
    uncertainty_k = st_qa_raw.astype(np.float64) * 0.01
    cloud_distance_km = cdist_raw.astype(np.float64) * 0.01
    lst_c = lst_c.astype(float, copy=True)
    lst_c[~footprint] = np.nan

    clear_land = qa_pixel_clear_land_mask(
        qa_pixel, excluded_bits=tuple(landsat["excluded_qa_pixel_bits"])
    )
    valid_dn = (st_dn >= landsat["minimum_st_dn"]) & (
        st_dn <= landsat["maximum_st_dn"]
    )
    terrain_visible = np.ones(shape, dtype=bool)
    if landsat["exclude_terrain_occlusion"]:
        terrain_visible = (qa_radsat & (1 << 11)) == 0
    valid = (
        footprint
        & clear_land
        & valid_dn
        & (st_qa_raw != -9999)
        & (cdist_raw != -9999)
        & (cloud_distance_km >= landsat["minimum_cloud_distance_km"])
        & terrain_visible
        & physically_plausible_lst_mask(lst_c)
    )
    if landsat["apply_st_uncertainty_threshold"]:
        valid &= uncertainty_k <= landsat["maximum_st_uncertainty_kelvin"]

    uncertainty_k = uncertainty_k.astype(float, copy=True)
    cloud_distance_km = cloud_distance_km.astype(float, copy=True)
    uncertainty_k[~footprint] = np.nan
    cloud_distance_km[~footprint] = np.nan
    return AlignedScene(
        scene_id=scene_id,
        lst_c=lst_c,
        valid=valid,
        st_uncertainty_k=uncertainty_k,
        cloud_distance_km=cloud_distance_km,
        footprint=footprint,
    )


def _read_asset_to_grid(
    href: str,
    *,
    grid: FixedGrid,
    fallback_nodata: int,
) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(href) as source:
        if source.crs is None or source.crs != rasterio.crs.CRS.from_string(grid.crs):
            raise ValueError(
                f"Source CRS {source.crs} is not the locked target CRS {grid.crs}."
            )
        transform = source.transform
        tolerance = 1e-6
        if (
            abs(transform.b) > tolerance
            or abs(transform.d) > tolerance
            or not np.isclose(transform.a, grid.resolution_m, atol=tolerance)
            or not np.isclose(-transform.e, grid.resolution_m, atol=tolerance)
        ):
            raise ValueError("Landsat source is not a north-up locked-resolution grid.")
        x_phase = (transform.c - grid.left) / grid.resolution_m
        y_phase = (grid.top - transform.f) / grid.resolution_m
        if not (
            np.isclose(x_phase, round(x_phase), atol=tolerance)
            and np.isclose(y_phase, round(y_phase), atol=tolerance)
        ):
            raise ValueError(
                "Landsat source pixel edges are not aligned to the locked target grid."
            )
        source_coverage = rasterize(
            ((box(*source.bounds), 1),),
            out_shape=grid.shape,
            transform=grid.transform,
            fill=0,
            all_touched=False,
            dtype="uint8",
        ).astype(bool)
        source_nodata = source.nodata
        destination_nodata = source_nodata
        if destination_nodata is None:
            destination_nodata = fallback_nodata
        with WarpedVRT(
            source,
            crs=grid.crs,
            transform=grid.transform,
            height=grid.height,
            width=grid.width,
            resampling=Resampling.nearest,
            src_nodata=source_nodata,
            nodata=destination_nodata,
        ) as warped:
            result = warped.read(1)
    if result.shape != grid.shape:
        raise ValueError(f"Aligned asset shape {result.shape} does not match {grid.shape}.")
    return result, source_coverage


def read_aligned_scene_from_hrefs(
    *,
    scene_id: str,
    asset_hrefs: dict[str, str],
    grid: FixedGrid,
    config: ResearchConfig,
) -> AlignedScene:
    """Read one scene exclusively from a frozen canonical asset manifest."""

    missing = set(REQUIRED_ASSETS) - set(asset_hrefs)
    if missing:
        raise ValueError(f"Frozen scene {scene_id} lacks assets: {sorted(missing)}")
    fallback_nodata = {
        "lwir11": 0,
        "qa_pixel": 0,
        "qa": -9999,
        "cdist": -9999,
        "qa_radsat": 0,
    }
    environment = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif",
    }
    arrays: dict[str, np.ndarray] = {}
    reference_coverage: np.ndarray | None = None
    with rasterio.Env(**environment):
        for asset in REQUIRED_ASSETS:
            signed_href = pc.sign(asset_hrefs[asset])
            array, coverage = _read_asset_to_grid(
                signed_href,
                grid=grid,
                fallback_nodata=fallback_nodata[asset],
            )
            arrays[asset] = array
            if reference_coverage is None:
                reference_coverage = coverage
            elif not np.array_equal(reference_coverage, coverage):
                raise ValueError(
                    f"Frozen scene {scene_id} assets do not share one raster footprint."
                )
    if reference_coverage is None:
        raise ValueError(f"Frozen scene {scene_id} produced no coverage mask.")
    arrays[COVERAGE_KEY] = reference_coverage
    return decode_aligned_scene_arrays(scene_id=scene_id, arrays=arrays, config=config)


def read_aligned_scene(
    item: Item,
    *,
    grid: FixedGrid,
    config: ResearchConfig,
) -> AlignedScene:
    """Nearest-align all categorical/DN assets and decode one remote scene."""

    if not all(asset in item.assets for asset in REQUIRED_ASSETS):
        raise ValueError(f"Scene {item.id} is missing a required asset.")
    return read_aligned_scene_from_hrefs(
        scene_id=item.id,
        asset_hrefs={asset: item.assets[asset].href for asset in REQUIRED_ASSETS},
        grid=grid,
        config=config,
    )
