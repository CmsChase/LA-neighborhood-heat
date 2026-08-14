"""Strictly local reconstruction of the four locked M3 ST_QA candidates."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from la_heat.aligned_landsat import AlignedScene, decode_aligned_scene_arrays
from la_heat.config import ResearchConfig
from la_heat.mosaic import mosaic_aligned_scenes
from la_heat.provenance import canonical_sha256
from la_heat.target_aggregation import TargetAggregationResult, aggregate_target_mosaic

ALGORITHM_VERSION: Final = "m3-source-offline-qa-v1"
QA_THRESHOLDS: Final = {
    "none": None,
    "3k": 3.0,
    "4k": 4.0,
    "6k": 6.0,
}


class M3OfflineQAError(RuntimeError):
    """Raised when offline QA reconstruction would leave its locked contract."""


LocalArrayLoader = Callable[[str, str], Mapping[str, np.ndarray]]


def candidate_config(base: ResearchConfig, candidate_id: str) -> ResearchConfig:
    """Return an in-memory config differing only by the locked ST_QA threshold."""

    if candidate_id not in QA_THRESHOLDS:
        raise M3OfflineQAError(f"Unknown ST_QA candidate: {candidate_id}")
    raw = copy.deepcopy(base.raw)
    threshold = QA_THRESHOLDS[candidate_id]
    raw["landsat"]["apply_st_uncertainty_threshold"] = threshold is not None
    if threshold is not None:
        raw["landsat"]["maximum_st_uncertainty_kelvin"] = threshold
    return ResearchConfig(raw=raw, path=Path(f"offline://{candidate_id}"))


def candidate_target_config_sha256(base: ResearchConfig, candidate_id: str) -> str:
    config = candidate_config(base, candidate_id)
    landsat = config.raw["landsat"]
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "candidate_id": candidate_id,
        "threshold_kelvin": QA_THRESHOLDS[candidate_id],
        "threshold_comparison": "less_than_or_equal",
        "landsat": {
            "excluded_qa_pixel_bits": landsat["excluded_qa_pixel_bits"],
            "lst_scale_kelvin": landsat["lst_scale_kelvin"],
            "lst_offset_kelvin": landsat["lst_offset_kelvin"],
            "minimum_valid_pixel_fraction": landsat["minimum_valid_pixel_fraction"],
            "minimum_valid_pixels_per_tract": landsat["minimum_valid_pixels_per_tract"],
            "minimum_tract_footprint_fraction": landsat["minimum_tract_footprint_fraction"],
            "minimum_st_dn": landsat["minimum_st_dn"],
            "maximum_st_dn": landsat["maximum_st_dn"],
            "minimum_cloud_distance_km": landsat["minimum_cloud_distance_km"],
            "exclude_terrain_occlusion": landsat["exclude_terrain_occlusion"],
            "minimum_date_tract_retention_fraction": landsat[
                "minimum_date_tract_retention_fraction"
            ],
            "minimum_city_union_coverage_fraction": landsat["minimum_city_union_coverage_fraction"],
        },
    }
    return canonical_sha256(payload)


def _grid_identity(context: Any) -> str:
    return (
        f"{context.grid.sha256}|"
        f"zones={hashlib.sha256(context.zones.tobytes()).hexdigest()}|"
        "land="
        f"{hashlib.sha256(np.packbits(context.eligible_land.ravel()).tobytes()).hexdigest()}"
    )


def _aligned_scenes(
    *,
    city_id: str,
    scene_ids: Sequence[str],
    loader: LocalArrayLoader,
    config: ResearchConfig,
    expected_shape: tuple[int, int],
) -> tuple[AlignedScene, ...]:
    if not scene_ids or len(scene_ids) != len(set(scene_ids)):
        raise M3OfflineQAError("Offline overpass scene identities are empty or duplicated.")
    scenes: list[AlignedScene] = []
    for scene_id in scene_ids:
        arrays = dict(loader(city_id, scene_id))
        if any(array.shape != expected_shape for array in arrays.values()):
            raise M3OfflineQAError("Cached Landsat array left the frozen city grid.")
        scene = decode_aligned_scene_arrays(
            scene_id=scene_id,
            arrays=arrays,
            config=config,
        )
        if scene.scene_id != scene_id:
            raise M3OfflineQAError("Cached scene identity changed.")
        scenes.append(scene)
    return tuple(scenes)


def reconstruct_overpass_candidates(
    *,
    city_id: str,
    scene_ids: Sequence[str],
    loader: LocalArrayLoader,
    context: Any,
    base_config: ResearchConfig,
    target_date: str,
    overpass_id: str,
    platform: str,
    union_city_coverage_fraction: float,
    tract_manifest_sha256: str,
) -> dict[str, TargetAggregationResult]:
    """Build all four candidates from verified local arrays with zero networking."""

    if city_id not in {
        "los_angeles_ca",
        "phoenix_az",
        "houston_tx",
        "chicago_il",
    }:
        raise M3OfflineQAError("Offline QA accepts only the four source cities.")
    if (
        context.zones.shape != context.grid.shape
        or context.eligible_land.shape != context.grid.shape
    ):
        raise M3OfflineQAError("Frozen source context shape changed.")
    raw_arrays = {scene_id: dict(loader(city_id, scene_id)) for scene_id in scene_ids}

    def memory_loader(_: str, scene_id: str) -> Mapping[str, np.ndarray]:
        return raw_arrays[scene_id]

    results: dict[str, TargetAggregationResult] = {}
    for candidate_id in QA_THRESHOLDS:
        config = candidate_config(base_config, candidate_id)
        scenes = _aligned_scenes(
            city_id=city_id,
            scene_ids=scene_ids,
            loader=memory_loader,
            config=config,
            expected_shape=context.grid.shape,
        )
        mosaic = mosaic_aligned_scenes(
            scene_ids=[scene.scene_id for scene in scenes],
            st_values=np.stack([scene.lst_c for scene in scenes]),
            qa_valid=np.stack([scene.valid for scene in scenes]),
            st_qa=np.stack([scene.st_uncertainty_k for scene in scenes]),
            cdist=np.stack([scene.cloud_distance_km for scene in scenes]),
            footprint=np.stack([scene.footprint for scene in scenes]),
        )
        results[candidate_id] = aggregate_target_mosaic(
            tracts=context.tracts,
            zone_raster=context.zones,
            static_land_mask=context.eligible_land,
            mosaic=mosaic,
            target_date=target_date,
            overpass_id=overpass_id,
            platform=platform,
            scene_ids=tuple(scene_ids),
            union_city_coverage_fraction=union_city_coverage_fraction,
            grid_identity=_grid_identity(context),
            config_sha256=candidate_target_config_sha256(base_config, candidate_id),
            tract_manifest_sha256=tract_manifest_sha256,
            config=config,
        )
    _validate_monotone_support(results)
    return results


def _validate_monotone_support(
    results: Mapping[str, TargetAggregationResult],
) -> None:
    if tuple(results) != tuple(QA_THRESHOLDS):
        raise M3OfflineQAError("ST_QA candidate output order changed.")
    counts: dict[str, int] = {}
    for candidate_id, result in results.items():
        frame = result.tract_date_qa
        if "target_available" not in frame:
            raise M3OfflineQAError("Candidate output lacks target availability.")
        counts[candidate_id] = int(frame["target_available"].fillna(False).sum())
    if not counts["none"] >= counts["6k"] >= counts["4k"] >= counts["3k"]:
        raise M3OfflineQAError("Stricter ST_QA unexpectedly increased target support.")
