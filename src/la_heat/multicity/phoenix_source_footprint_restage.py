"""Refresh Phoenix public source metadata against the canonical city boundary.

The stage lists source coverage only.  It never opens raster assets, target
values, model artifacts, or evaluation outputs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import pandas as pd

from la_heat.multicity import source_footprints as footprints
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import atomic_json, atomic_parquet, canonical_sha256, sha256_file

CITY_ID: Final = "phoenix_az"
ALGORITHM_VERSION: Final = "phoenix-portable-source-footprint-restage"
COMPLETE_STATE: Final = "complete_target_blind_portable_source_footprint"
MANIFEST_PATH: Final = Path(
    "manifests/multicity/cities/phoenix_az/source_footprints/"
    "PORTABLE_SOURCE_FOOTPRINT.json"
)
GEOGRAPHY_PATH: Final = Path(
    "manifests/multicity/cities/phoenix_az/geography/GEOGRAPHY_CONTRACT_V1.json"
)
HISTORICAL_MANIFEST_PATH: Final = Path(
    "manifests/multicity/cities/phoenix_az/source_footprints/SOURCE_FOOTPRINTS.json"
)
ARTIFACT_DIRECTORY: Final = "portable_source_footprint"


class PhoenixSourceFootprintRestageError(ValueError):
    """Raised when the target-blind Phoenix metadata refresh is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PhoenixSourceFootprintRestageError(f"Expected JSON object: {path}")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> None:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise PhoenixSourceFootprintRestageError(f"{label} commit hash changed.")


def _artifact_paths(
    workspace: MulticityWorkspace,
) -> tuple[Path, Path, Path]:
    city = workspace.city(CITY_ID)
    return (
        city.raw / ARTIFACT_DIRECTORY,
        city.processed / ARTIFACT_DIRECTORY,
        workspace.project_root / MANIFEST_PATH,
    )


def _load_canonical_geography(
    project_root: Path,
) -> tuple[dict[str, Any], gpd.GeoDataFrame, tuple[float, float, float, float]]:
    path = project_root / GEOGRAPHY_PATH
    payload = _read_json(path)
    _verify_commit(payload, label="Phoenix geography")
    if (
        payload.get("state") != "complete_target_blind_city_geography_evidence"
        or payload.get("city", {}).get("id") != CITY_ID
        or payload.get("city", {}).get("target_values_status") != "sealed"
        or payload.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or payload.get("access_contract", {}).get("predictor_values_read_or_computed")
        is not False
    ):
        raise PhoenixSourceFootprintRestageError(
            "Phoenix canonical geography or target lock changed."
        )
    boundary_record = payload["output_tables"]["city_boundary"]
    boundary_path = project_root / str(boundary_record["path"])
    if sha256_file(boundary_path) != boundary_record["sha256"]:
        raise PhoenixSourceFootprintRestageError(
            "Phoenix canonical boundary bytes changed."
        )
    boundary = gpd.read_parquet(boundary_path)
    if len(boundary) != 1 or boundary.crs is None:
        raise PhoenixSourceFootprintRestageError(
            "Phoenix canonical boundary must contain one georeferenced feature."
        )
    bbox = tuple(float(value) for value in boundary.to_crs("EPSG:4326").total_bounds)
    return payload, boundary, bbox  # type: ignore[return-value]


def _snapshot_comparison(
    historical: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for family in ("landsat_wrs", "sentinel_mgrs", "daymet_cells", "terrain_windows"):
        old = historical[family]
        new = current[family]
        old_ids = set(old.get("member_ids", []))
        new_ids = set(new.get("member_ids", []))
        comparison[family] = {
            "historical_member_count": int(old["member_count"]),
            "current_member_count": int(new["member_count"]),
            "historical_only_member_ids": sorted(old_ids - new_ids),
            "current_only_member_ids": sorted(new_ids - old_ids),
        }
        if "item_count" in old and "item_count" in new:
            comparison[family]["historical_item_count"] = int(old["item_count"])
            comparison[family]["current_item_count"] = int(new["item_count"])
    return comparison


def _recorded_file(
    project_root: Path, path: Path
) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(project_root.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _verify_record(project_root: Path, record: Mapping[str, Any]) -> Path:
    path = (project_root / str(record["path"])).resolve()
    if (
        not path.is_relative_to(project_root.resolve())
        or not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise PhoenixSourceFootprintRestageError(
            f"Phoenix source-footprint artifact changed: {path}"
        )
    return path


def verify_phoenix_source_footprint_restage(
    config_path: str | Path = "configs/multicity/experiment.toml",
) -> dict[str, Any]:
    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    manifest_path = workspace.project_root / MANIFEST_PATH
    payload = _read_json(manifest_path)
    _verify_commit(payload, label="Phoenix portable source footprint")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or payload.get("city", {}).get("id") != CITY_ID
        or payload.get("city", {}).get("target_values_status") != "sealed"
        or payload.get("access_contract") != footprints.ACCESS_CONTRACT
        or payload.get("decision", {}).get("source_footprint_blocker_closed") is not True
    ):
        raise PhoenixSourceFootprintRestageError(
            "Phoenix portable source-footprint terminal changed."
        )
    for record in payload["raw_files"].values():
        _verify_record(workspace.project_root, record)
    frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {}
    for name, record in payload["output_tables"].items():
        path = _verify_record(workspace.project_root, record)
        frame = (
            gpd.read_parquet(path)
            if name in footprints.OUTPUT_GEOMETRY_TABLES
            else pd.read_parquet(path)
        )
        if len(frame) != record["rows"]:
            raise PhoenixSourceFootprintRestageError(
                f"Phoenix source-footprint row count changed: {name}"
            )
        frames[name] = frame
    replayed = footprints._family_summaries(
        landsat_items=frames["landsat_items"],
        sentinel_items=frames["sentinel_items"],
        optical_units=frames["optical_units"],
        daymet_granules=frames["daymet_granules"],
        daymet_cells=frames["daymet_cells"],
        daymet_window=payload["source_families"]["daymet_cells"]["window"],
        terrain_tiles=frames["terrain_tiles"],
    )
    if replayed != payload["source_families"]:
        raise PhoenixSourceFootprintRestageError(
            "Phoenix source-family summary no longer replays."
        )
    return payload


def stage_phoenix_source_footprint_restage(
    config_path: str | Path = "configs/multicity/experiment.toml",
    *,
    source_config_path: str | Path = footprints.DEFAULT_SOURCE_CONFIG,
    client: footprints._HttpClientLike | None = None,
) -> dict[str, Any]:
    plan = load_multicity_plan(config_path)
    city = footprints._authorize(plan, CITY_ID)
    workspace = MulticityWorkspace.from_plan(plan)
    raw_root, processed_root, manifest_path = _artifact_paths(workspace)
    if manifest_path.is_file():
        return verify_phoenix_source_footprint_restage(plan.path)

    source_path = Path(source_config_path)
    if not source_path.is_absolute():
        source_path = workspace.project_root / source_path
    source_path = source_path.resolve()
    source_config = footprints._read_source_config(source_path, plan)
    geography, city_boundary, bbox = _load_canonical_geography(workspace.project_root)
    analysis_crs = str(source_config["stage"]["analysis_crs"])
    active_client = footprints._retrying_session() if client is None else client

    landsat_config = source_config["landsat"]
    landsat_start = date.fromisoformat(landsat_config["local_start_date"])
    landsat_end = date.fromisoformat(landsat_config["local_end_date"])
    landsat_features, landsat_pages, landsat_query = (
        footprints.fetch_public_stac_metadata(
            active_client,
            api=landsat_config["api"],
            collection=landsat_config["collection"],
            bbox_wgs84=bbox,
            datetime_interval=footprints.local_date_interval_to_utc(
                landsat_start, landsat_end, city.timezone
            ),
            fields=footprints.LANDSAT_FIELDS,
            properties=footprints.LANDSAT_PROPERTIES,
            page_limit=int(landsat_config["page_limit"]),
            query={
                "platform": {"in": list(landsat_config["platforms"])},
                "landsat:collection_category": {
                    "eq": landsat_config["collection_category"]
                },
                "landsat:correction": {"eq": landsat_config["correction"]},
            },
        )
    )
    landsat_items = footprints.build_optical_item_table(
        landsat_features,
        source="landsat_wrs",
        collection=landsat_config["collection"],
        expected_properties=footprints.LANDSAT_PROPERTIES,
        allowed_platforms=landsat_config["platforms"],
        local_start_date=landsat_start,
        local_end_date=landsat_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    sentinel_config = source_config["sentinel"]
    sentinel_start = date.fromisoformat(sentinel_config["local_start_date"])
    sentinel_end = date.fromisoformat(sentinel_config["local_end_date"])
    sentinel_features, sentinel_pages, sentinel_query = (
        footprints.fetch_public_stac_metadata(
            active_client,
            api=sentinel_config["api"],
            collection=sentinel_config["collection"],
            bbox_wgs84=bbox,
            datetime_interval=footprints.local_date_interval_to_utc(
                sentinel_start, sentinel_end, city.timezone
            ),
            fields=footprints.SENTINEL_FIELDS,
            properties=footprints.SENTINEL_PROPERTIES,
            page_limit=int(sentinel_config["page_limit"]),
        )
    )
    sentinel_items = footprints.build_optical_item_table(
        sentinel_features,
        source="sentinel_mgrs",
        collection=sentinel_config["collection"],
        expected_properties=footprints.SENTINEL_PROPERTIES,
        allowed_platforms=sentinel_config["platforms"],
        local_start_date=sentinel_start,
        local_end_date=sentinel_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )
    optical_units = footprints.build_optical_unit_table(
        (landsat_items, sentinel_items),
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    daymet_config = source_config["daymet"]
    daymet_granules, daymet_raw, daymet_query = (
        footprints.fetch_daymet_granule_metadata(
            active_client,
            endpoint=daymet_config["cmr_granules_url"],
            collection_concept_id=daymet_config["collection_concept_id"],
            year=int(daymet_config["year"]),
            variables=daymet_config["variables"],
            bbox_wgs84=bbox,
        )
    )
    daymet_window = footprints.derive_daymet_index_window(
        bbox, halo_cells=int(daymet_config["window_halo_cells"])
    )
    daymet_cells = footprints.build_daymet_cell_table(
        daymet_window, city_boundary=city_boundary
    )

    terrain_config = source_config["terrain"]
    terrain_tiles = footprints.derive_srtm_tiles(
        city_boundary,
        analysis_crs=analysis_crs,
        halo_m=float(terrain_config["slope_halo_m"]),
        base_url=terrain_config["base_url"],
        filename_suffix=terrain_config["filename_suffix"],
    )
    terrain_tiles, terrain_probes = footprints.probe_terrain_heads(
        active_client, terrain_tiles
    )

    raw_paths: list[Path] = []
    for source, pages in (("landsat", landsat_pages), ("sentinel", sentinel_pages)):
        for number, page in enumerate(pages, start=1):
            path = raw_root / source / f"stac_page_{number:03d}.json"
            atomic_json(page, path)
            raw_paths.append(path)
    daymet_path = raw_root / "daymet" / "cmr_granules_2025.json"
    atomic_json(daymet_raw, daymet_path)
    raw_paths.append(daymet_path)
    for tile_id, probe in sorted(terrain_probes.items()):
        path = raw_root / "terrain" / f"{tile_id}_head.json"
        atomic_json(probe, path)
        raw_paths.append(path)

    frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {
        "landsat_items": landsat_items,
        "sentinel_items": sentinel_items,
        "optical_units": optical_units,
        "daymet_granules": daymet_granules,
        "daymet_cells": daymet_cells,
        "terrain_tiles": terrain_tiles,
    }
    output_paths = {
        name: processed_root / filename
        for name, filename in footprints.OUTPUT_FILENAMES.items()
    }
    for name, frame in frames.items():
        atomic_parquet(frame, output_paths[name])
    committed = {
        name: (
            gpd.read_parquet(output_paths[name])
            if name in footprints.OUTPUT_GEOMETRY_TABLES
            else pd.read_parquet(output_paths[name])
        )
        for name in frames
    }
    families = footprints._family_summaries(
        landsat_items=committed["landsat_items"],
        sentinel_items=committed["sentinel_items"],
        optical_units=committed["optical_units"],
        daymet_granules=committed["daymet_granules"],
        daymet_cells=committed["daymet_cells"],
        daymet_window=daymet_window,
        terrain_tiles=committed["terrain_tiles"],
    )
    output_tables = {
        name: footprints._table_record(
            workspace.project_root,
            output_paths[name],
            committed[name],
            geometry=name in footprints.OUTPUT_GEOMETRY_TABLES,
        )
        for name in committed
    }
    historical = _read_json(workspace.project_root / HISTORICAL_MANIFEST_PATH)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "city": {
            "id": city.id,
            "name": city.name,
            "role": city.role,
            "target_values_status": city.target_values_status,
            "timezone": city.timezone,
        },
        "purpose": "refresh_public_metadata_for_canonical_four_city_support",
        "geography_input": {
            **_recorded_file(workspace.project_root, workspace.project_root / GEOGRAPHY_PATH),
            "manifest_commit_sha256": geography["commit_sha256"],
            "city_boundary": dict(geography["output_tables"]["city_boundary"]),
            "bbox_wgs84": list(bbox),
        },
        "source_config": _recorded_file(workspace.project_root, source_path),
        "selection_contract": {
            "analysis_crs": analysis_crs,
            "spatial_rule": "strictly_positive_city_intersection_area",
            "windows_derived_from_canonical_city_boundary": True,
            "no_external_target_dates_selected": True,
        },
        "queries": {
            "landsat": landsat_query,
            "sentinel": sentinel_query,
            "daymet": daymet_query,
            "terrain": {
                "method": "HEAD",
                "object_count": len(terrain_tiles),
                "payload_bytes_read": 0,
            },
        },
        "source_families": families,
        "comparison_to_historical_snapshot": {
            "historical_manifest": _recorded_file(
                workspace.project_root,
                workspace.project_root / HISTORICAL_MANIFEST_PATH,
            ),
            "families": _snapshot_comparison(
                historical["source_families"], families
            ),
        },
        "access_contract": dict(footprints.ACCESS_CONTRACT),
        "raw_files": {
            path.resolve().relative_to(workspace.project_root).as_posix():
            _recorded_file(workspace.project_root, path)
            for path in sorted(raw_paths)
        },
        "output_tables": output_tables,
        "decision": {
            "canonical_geography_used": True,
            "source_footprint_blocker_closed": True,
            "predictor_build_performed": False,
            "external_targets_remain_sealed": True,
            "next_gate": "portable_predictor_contract_decision",
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, manifest_path)
    return verify_phoenix_source_footprint_restage(plan.path)
