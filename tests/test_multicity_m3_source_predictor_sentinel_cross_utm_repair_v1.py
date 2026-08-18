from __future__ import annotations

from types import SimpleNamespace

import affine
import rasterio

from la_heat.multicity.m3_source_predictor_sentinel_cross_utm_repair_v1 import (
    validate_repaired_native_grid,
)


def test_cross_utm_14_to_15_is_narrowly_allowed() -> None:
    source = SimpleNamespace(
        crs=rasterio.crs.CRS.from_epsg(32614),
        transform=affine.Affine(20, 0, 699960, 0, -20, 3300000),
    )
    grid = SimpleNamespace(crs="EPSG:32615")
    validate_repaired_native_grid(
        source,
        grid=grid,
        categorical=True,
        original_validator=lambda *args, **kwargs: None,
    )


def test_unrelated_crs_is_rejected() -> None:
    source = SimpleNamespace(
        crs=rasterio.crs.CRS.from_epsg(32613),
        transform=affine.Affine(20, 0, 0, 0, -20, 0),
    )
    grid = SimpleNamespace(crs="EPSG:32615")
    try:
        validate_repaired_native_grid(
            source,
            grid=grid,
            categorical=True,
            original_validator=lambda *args, **kwargs: None,
        )
    except ValueError as error:
        assert "Unauthorized Sentinel CRS transform" in str(error)
    else:
        raise AssertionError("unrelated CRS was accepted")
