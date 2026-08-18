from __future__ import annotations

from pathlib import Path

import shapely

from la_heat.multicity.m3_source_predictor_sentinel_bbox_repair_v1 import CODE_PATHS


def test_repair_code_paths_are_versioned_and_narrow() -> None:
    assert CODE_PATHS == (
        Path("src/la_heat/multicity/m3_source_predictor_sentinel_bbox_repair_v1.py"),
        Path("scripts/run_m3_source_predictor_sentinel_bbox_repair_v1.py"),
    )


def test_envelope_contains_exact_geometry() -> None:
    geometry = shapely.Polygon([(0, 0), (2, 0), (1, 1), (0, 0)])
    assert geometry.envelope.covers(geometry)
