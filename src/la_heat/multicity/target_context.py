"""Target-blind four-city support adapter for future Landsat aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd

from la_heat.grid import FixedGrid
from la_heat.multicity.portable_predictor_components import (
    CITY_IDS,
    PortableCitySupport,
    load_city_support,
)
from la_heat.multicity.spatial_blocks import (
    COMBINED_OUTPUT,
    OUTPUT_COLUMNS,
    build_multicity_spatial_blocks,
)
from la_heat.multicity.spatial_blocks import (
    MANIFEST_PATH as SPATIAL_BLOCK_MANIFEST,
)
from la_heat.provenance import atomic_json, canonical_sha256

ALGORITHM_VERSION: Final = "multicity-target-context-v1"
MANIFEST_PATH: Final = Path("manifests/multicity/targets/TARGET_CONTEXTS.json")
RESERVED_COLUMNS: Final = {
    "GEOID",
    "spatial_block",
    "local_spatial_block",
    "longitude_quartile",
    "latitude_quartile",
}


class TargetContextError(RuntimeError):
    """Raised when target aggregation support cannot be joined exactly."""


@dataclass(frozen=True, slots=True)
class TargetCityContext:
    city_id: str
    grid: FixedGrid
    zones: np.ndarray
    eligible_land: np.ndarray
    tracts: gpd.GeoDataFrame
    locks: dict[str, str]


def attach_frozen_spatial_blocks(
    city_id: str,
    tracts: gpd.GeoDataFrame,
    blocks: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Add the frozen evaluation metadata without reading target values."""

    if city_id not in CITY_IDS or "tract_geoid" not in tracts:
        raise TargetContextError("Canonical city tracts are required.")
    if reserved := RESERVED_COLUMNS.intersection(tracts.columns):
        raise TargetContextError(
            f"Tracts already contain unfrozen target metadata: {sorted(reserved)}"
        )
    if tuple(blocks.columns) != OUTPUT_COLUMNS:
        raise TargetContextError("Frozen spatial-block table schema changed.")
    city_blocks = blocks.loc[blocks["city_id"].astype(str).eq(city_id)].copy()
    if city_blocks.empty or city_blocks["tract_geoid"].duplicated().any():
        raise TargetContextError(f"Frozen spatial blocks are invalid for {city_id}.")
    source_geoids = tracts["tract_geoid"].astype(str)
    if set(source_geoids) != set(city_blocks["tract_geoid"].astype(str)):
        raise TargetContextError(f"Spatial-block GEOIDs do not match {city_id} geography.")
    metadata_columns = [column for column in OUTPUT_COLUMNS if column != "city_id"]
    joined = tracts.merge(
        city_blocks.loc[:, metadata_columns],
        on="tract_geoid",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    joined["GEOID"] = joined["tract_geoid"].astype("string")
    if not joined["tract_geoid"].astype(str).equals(source_geoids.reset_index(drop=True)):
        raise TargetContextError("Spatial-block join changed canonical tract order.")
    if joined[list(RESERVED_COLUMNS)].isna().any(axis=None):
        raise TargetContextError("Spatial-block join created missing target metadata.")
    return gpd.GeoDataFrame(joined, geometry=tracts.geometry.name, crs=tracts.crs)


def _context(
    support: PortableCitySupport,
    blocks: pd.DataFrame,
    spatial_manifest: dict[str, Any],
) -> TargetCityContext:
    tracts = attach_frozen_spatial_blocks(support.city_id, support.tracts, blocks)
    if tuple(tracts["GEOID"].astype(str)) != support.tract_geoids:
        raise TargetContextError("Target tract order no longer matches the zone raster.")
    if tracts.crs is None or tracts.crs.to_epsg() != 5070:
        raise TargetContextError("Target tracts must remain in canonical EPSG:5070.")
    return TargetCityContext(
        city_id=support.city_id,
        grid=support.grid,
        zones=support.zones,
        eligible_land=support.eligible_land,
        tracts=tracts,
        locks={
            "geography_commit_sha256": str(support.geography_manifest["commit_sha256"]),
            "worldcover_commit_sha256": str(support.worldcover_manifest["commit_sha256"]),
            "spatial_blocks_commit_sha256": str(spatial_manifest["commit_sha256"]),
            "spatial_blocks_semantic_sha256": str(
                spatial_manifest["cities"][support.city_id]["semantic_sha256"]
            ),
            "target_grid_definition_sha256": support.grid.sha256,
        },
    )


def load_target_city_context(
    project_root: str | Path,
    city_id: str,
) -> TargetCityContext:
    """Authenticate and load one target-blind city aggregation context."""

    root = Path(project_root).resolve()
    spatial_manifest = build_multicity_spatial_blocks(root, check_only=True)
    blocks = pd.read_parquet(root / COMBINED_OUTPUT)
    return _context(load_city_support(root, city_id), blocks, spatial_manifest)


def stage_multicity_target_contexts(
    project_root: str | Path,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Freeze context identities only; never open a Landsat raster or target table."""

    root = Path(project_root).resolve()
    spatial_manifest = build_multicity_spatial_blocks(root, check_only=True)
    blocks = pd.read_parquet(root / COMBINED_OUTPUT)
    cities: dict[str, dict[str, Any]] = {}
    for city_id in CITY_IDS:
        context = _context(load_city_support(root, city_id), blocks, spatial_manifest)
        cities[city_id] = {
            "tract_count": len(context.tracts),
            "spatial_block_count": int(context.tracts["spatial_block"].nunique()),
            "grid": {
                "crs": context.grid.crs,
                "width": context.grid.width,
                "height": context.grid.height,
                "resolution_m": context.grid.resolution_m,
                "sha256": context.grid.sha256,
            },
            "locks": context.locks,
        }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_target_contexts",
        "spatial_block_manifest": SPATIAL_BLOCK_MANIFEST.as_posix(),
        "spatial_block_commit_sha256": spatial_manifest["commit_sha256"],
        "cities": cities,
        "access_contract": {
            "public_geography_and_support_read": True,
            "landsat_asset_hrefs_read": False,
            "landsat_thermal_or_target_qa_values_read": False,
            "target_tables_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "freeze_target_transaction_after_predictor_readiness",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    path = root / MANIFEST_PATH
    if check_only:
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TargetContextError(f"Cannot read target-context manifest: {path}") from error
        if observed != payload:
            raise TargetContextError("Target-context manifest no longer reproduces exactly.")
        return observed
    atomic_json(payload, path)
    return payload
