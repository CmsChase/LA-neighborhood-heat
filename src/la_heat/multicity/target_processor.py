"""Claim-bound processing primitives for frozen multicity Landsat targets."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import quote

import numpy as np
import requests

from la_heat.aligned_landsat import (
    REQUIRED_ASSETS,
    AlignedScene,
    read_aligned_scene_from_hrefs,
)
from la_heat.config import ResearchConfig
from la_heat.grid import FixedGrid
from la_heat.mosaic import mosaic_aligned_scenes
from la_heat.multicity.target_authorization import ValuesAccessGate
from la_heat.provenance import canonical_sha256
from la_heat.target_aggregation import TargetAggregationResult, aggregate_target_mosaic

ALGORITHM_VERSION: Final = "multicity-target-processor-v1"
LANDSAT_COLLECTION: Final = "landsat-c2-l2"
DEFAULT_STAC_API: Final = "https://planetarycomputer.microsoft.com/api/stac/v1"


class TargetProcessorError(RuntimeError):
    """Raised when one authorized target unit cannot be processed exactly."""


class SceneHydrator(Protocol):
    def __call__(self, scene_id: str) -> Mapping[str, str]: ...


class SceneReader(Protocol):
    def __call__(
        self,
        *,
        scene_id: str,
        asset_hrefs: dict[str, str],
        grid: FixedGrid,
        config: ResearchConfig,
    ) -> AlignedScene: ...


def multicity_target_config_payload(config: ResearchConfig) -> dict[str, Any]:
    """Select only cross-city QA and aggregation rules, excluding LA geometry."""

    landsat_keys = (
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
        "minimum_date_tract_retention_fraction",
        "minimum_city_union_coverage_fraction",
    )
    validation_keys = (
        "hotspot_quantile",
        "minimum_relative_endpoint_tract_fraction",
        "maximum_relative_endpoint_quartile_retention_gap",
        "minimum_relative_joint_cell_tracts",
        "minimum_relative_joint_cell_retention_fraction",
    )
    landsat = config.raw["landsat"]
    validation = config.raw["validation"]
    missing = (set(landsat_keys) - set(landsat)) | (
        set(validation_keys) - set(validation)
    )
    if missing:
        raise TargetProcessorError(f"Target configuration lacks keys: {sorted(missing)}")
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "required_assets": list(REQUIRED_ASSETS),
        "landsat": {key: landsat[key] for key in landsat_keys},
        "validation": {key: validation[key] for key in validation_keys},
        "static_land_mask": config.raw["static_land_mask"],
    }


def multicity_target_config_sha256(config: ResearchConfig) -> str:
    return canonical_sha256(multicity_target_config_payload(config))


@dataclass(frozen=True, slots=True)
class PlanetaryComputerSceneHydrator:
    """Fetch canonical asset metadata for one already frozen item ID."""

    stac_api: str = DEFAULT_STAC_API
    collection: str = LANDSAT_COLLECTION
    timeout_seconds: float = 60.0
    get: Callable[..., requests.Response] = requests.get

    def __call__(self, scene_id: str) -> Mapping[str, str]:
        if not scene_id or quote(scene_id, safe="") != scene_id:
            raise TargetProcessorError("Frozen Landsat scene ID is invalid.")
        endpoint = (
            f"{self.stac_api.rstrip('/')}/collections/{self.collection}/items/{scene_id}"
        )
        response = self.get(endpoint, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if payload.get("id") != scene_id or payload.get("collection") != self.collection:
            raise TargetProcessorError("STAC returned a different Landsat item identity.")
        assets = payload.get("assets")
        if not isinstance(assets, dict):
            raise TargetProcessorError(f"Landsat scene {scene_id} has no asset mapping.")
        hrefs: dict[str, str] = {}
        for asset in REQUIRED_ASSETS:
            record = assets.get(asset)
            href = record.get("href") if isinstance(record, dict) else None
            if not isinstance(href, str) or not href.startswith("https://"):
                raise TargetProcessorError(
                    f"Landsat scene {scene_id} lacks canonical asset {asset}."
                )
            hrefs[asset] = href
        return hrefs


def read_authorized_scenes(
    scene_ids: Sequence[str],
    *,
    gate: ValuesAccessGate,
    hydrator: SceneHydrator,
    reader: SceneReader,
    grid: FixedGrid,
    config: ResearchConfig,
) -> tuple[AlignedScene, ...]:
    """Open the claim before href hydration and keep hrefs only in memory."""

    if not scene_ids or len(scene_ids) != len(set(scene_ids)):
        raise TargetProcessorError("Overpass scene identities are empty or duplicated.")
    aligned: list[AlignedScene] = []
    for scene_id in scene_ids:
        gate.before_first_value_access()
        observed_hrefs = dict(hydrator(scene_id))
        if set(observed_hrefs) != set(REQUIRED_ASSETS):
            raise TargetProcessorError(
                f"Hydrated scene {scene_id} changed the exact asset contract."
            )
        scene = reader(
            scene_id=scene_id,
            asset_hrefs=observed_hrefs,
            grid=grid,
            config=config,
        )
        if scene.scene_id != scene_id:
            raise TargetProcessorError("Scene reader returned a different item identity.")
        aligned.append(scene)
    return tuple(aligned)


def aggregate_authorized_overpass(
    *,
    scene_ids: Sequence[str],
    gate: ValuesAccessGate,
    hydrator: SceneHydrator,
    reader: SceneReader = read_aligned_scene_from_hrefs,
    context: Any,
    config: ResearchConfig,
    target_date: str,
    overpass_id: str,
    platform: str,
    union_city_coverage_fraction: float,
    target_config_sha256: str,
    tract_manifest_sha256: str,
) -> TargetAggregationResult:
    """Read, mosaic, and aggregate one frozen overpass after authorization."""

    if context.grid.sha256 == "" or context.zones.shape != context.grid.shape:
        raise TargetProcessorError("Target context grid changed.")
    if context.eligible_land.shape != context.grid.shape:
        raise TargetProcessorError("Target eligible-land mask changed.")
    aligned = read_authorized_scenes(
        scene_ids,
        gate=gate,
        hydrator=hydrator,
        reader=reader,
        grid=context.grid,
        config=config,
    )
    mosaic = mosaic_aligned_scenes(
        scene_ids=[scene.scene_id for scene in aligned],
        st_values=np.stack([scene.lst_c for scene in aligned]),
        qa_valid=np.stack([scene.valid for scene in aligned]),
        st_qa=np.stack([scene.st_uncertainty_k for scene in aligned]),
        cdist=np.stack([scene.cloud_distance_km for scene in aligned]),
        footprint=np.stack([scene.footprint for scene in aligned]),
    )
    grid_identity = (
        f"{context.grid.sha256}|"
        f"zones={hashlib.sha256(context.zones.tobytes()).hexdigest()}|"
        f"land={hashlib.sha256(np.packbits(context.eligible_land.ravel()).tobytes()).hexdigest()}"
    )
    return aggregate_target_mosaic(
        tracts=context.tracts,
        zone_raster=context.zones,
        static_land_mask=context.eligible_land,
        mosaic=mosaic,
        target_date=target_date,
        overpass_id=overpass_id,
        platform=platform,
        scene_ids=tuple(scene_ids),
        union_city_coverage_fraction=union_city_coverage_fraction,
        grid_identity=grid_identity,
        config_sha256=target_config_sha256,
        tract_manifest_sha256=tract_manifest_sha256,
        config=config,
    )
