from __future__ import annotations

from types import SimpleNamespace

import shapely

from la_heat.multicity.m3_source_predictor_sentinel_geometry_filter_v1 import (
    filter_exact_aoi_items,
)


def test_filter_keeps_only_valid_exact_aoi_intersections() -> None:
    exact = shapely.box(0, 0, 1, 1)
    inside = SimpleNamespace(geometry=shapely.geometry.mapping(shapely.box(0.2, 0.2, 0.4, 0.4)))
    outside = SimpleNamespace(geometry=shapely.geometry.mapping(shapely.box(2, 2, 3, 3)))
    invalid = SimpleNamespace(geometry={"type": "Polygon", "coordinates": []})
    assert filter_exact_aoi_items((inside, outside, invalid), exact) == (inside,)
