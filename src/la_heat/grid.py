"""Fixed target grid construction and hashing."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

import geopandas as gpd
from rasterio import Affine
from rasterio.transform import from_origin


@dataclass(frozen=True, slots=True)
class FixedGrid:
    crs: str
    resolution_m: float
    anchor_x_m: float
    anchor_y_m: float
    left: float
    bottom: float
    right: float
    top: float
    width: int
    height: int
    transform: Affine

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        payload["transform"] = tuple(self.transform)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def build_fixed_grid(
    boundary: gpd.GeoDataFrame,
    *,
    target_crs: str,
    resolution_m: float,
    anchor_x_m: float = 0.0,
    anchor_y_m: float = 0.0,
) -> FixedGrid:
    """Create an outward-snapped grid with a prespecified raster-edge phase."""

    if boundary.empty or boundary.crs is None:
        raise ValueError("A non-empty georeferenced boundary is required.")
    if not all(math.isfinite(value) for value in (resolution_m, anchor_x_m, anchor_y_m)):
        raise ValueError("Grid resolution and anchors must be finite.")
    if resolution_m <= 0:
        raise ValueError("Grid resolution must be positive.")
    if not (0 <= anchor_x_m < resolution_m and 0 <= anchor_y_m < resolution_m):
        raise ValueError("Grid anchors must be in [0, resolution).")
    projected = boundary.to_crs(target_crs)
    min_x, min_y, max_x, max_y = (float(value) for value in projected.total_bounds)
    left = anchor_x_m + math.floor((min_x - anchor_x_m) / resolution_m) * resolution_m
    bottom = (
        anchor_y_m
        + math.floor((min_y - anchor_y_m) / resolution_m) * resolution_m
    )
    right = anchor_x_m + math.ceil((max_x - anchor_x_m) / resolution_m) * resolution_m
    top = anchor_y_m + math.ceil((max_y - anchor_y_m) / resolution_m) * resolution_m
    width = int(round((right - left) / resolution_m))
    height = int(round((top - bottom) / resolution_m))
    if width <= 0 or height <= 0:
        raise ValueError("Boundary produced an empty target grid.")
    return FixedGrid(
        crs=target_crs,
        resolution_m=float(resolution_m),
        anchor_x_m=float(anchor_x_m),
        anchor_y_m=float(anchor_y_m),
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        width=width,
        height=height,
        transform=from_origin(left, top, resolution_m, resolution_m),
    )
