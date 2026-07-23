"""Static, scene-independent eligible-land masks."""

from __future__ import annotations

import numpy as np
import planetary_computer as pc
import rasterio
from pystac import Item
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from la_heat.config import ResearchConfig


def land_classes_to_mask(
    classes: np.ndarray,
    *,
    nodata_class: int,
    water_classes: list[int],
) -> np.ndarray:
    """Convert fixed land-cover classes to the eligible-land denominator mask."""

    values = np.asarray(classes)
    return (values != nodata_class) & ~np.isin(values, water_classes)


def get_static_land_item(config: ResearchConfig) -> Item:
    land = config.raw["static_land_mask"]
    study = config.raw["study"]
    client = Client.open(land["stac_api"])
    items = list(
        client.search(
            collections=[land["collection"]],
            bbox=study["bbox_wgs84"],
            datetime=f"{land['year']}-01-01/{land['year']}-12-31",
        ).items()
    )
    if len(items) != 1:
        raise ValueError(f"Expected one static land-cover tile, found {len(items)}.")
    if land["asset"] not in items[0].assets:
        raise ValueError(f"Static land-cover item lacks asset {land['asset']!r}.")
    return items[0]


def read_static_land_mask(
    item: Item,
    *,
    output_shape: tuple[int, int],
    output_transform: rasterio.Affine,
    output_crs: object,
    config: ResearchConfig,
) -> np.ndarray:
    """Read and nearest-neighbor align the frozen land mask to a target grid."""

    land = config.raw["static_land_mask"]
    if land["categorical_resampling"] != "mode":
        raise ValueError("Static categorical land-mask resampling must be 'mode'.")
    signed = pc.sign(item)
    environment = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif",
    }
    with rasterio.Env(**environment):
        with rasterio.open(signed.assets[land["asset"]].href) as source:
            with WarpedVRT(
                source,
                crs=output_crs,
                transform=output_transform,
                height=output_shape[0],
                width=output_shape[1],
                resampling=Resampling.mode,
                nodata=land["nodata_class"],
            ) as warped:
                classes = warped.read(1)
    return land_classes_to_mask(
        classes,
        nodata_class=land["nodata_class"],
        water_classes=land["water_classes"],
    )
