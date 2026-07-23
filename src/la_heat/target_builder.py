"""Build fixed-grid, multi-scene 2020–2024 Landsat tract targets."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize

from la_heat.aligned_landsat import REQUIRED_ASSETS, read_aligned_scene_from_hrefs
from la_heat.boundaries import (
    assign_spatial_blocks,
    download_detailed_la_county_tracts,
    load_city_tracts,
)
from la_heat.config import ResearchConfig, load_config
from la_heat.grid import FixedGrid, build_fixed_grid
from la_heat.guardrails import (
    validate_no_final_year_rows,
    validate_static_eligible_denominator,
    validate_target_qa_contract,
    validate_unique_primary_key,
)
from la_heat.inventory import (
    INVENTORY_SCHEMA_VERSION,
    read_overpass_inventory,
    read_scene_inventory,
)
from la_heat.landmask import get_static_land_item, read_static_land_mask
from la_heat.mosaic import mosaic_aligned_scenes
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import (
    inventory_config_sha256,
    target_config_payload,
    target_config_sha256,
)
from la_heat.target_aggregation import aggregate_target_mosaic

TARGET_PIPELINE_ALGORITHM_VERSION = "target-v2-gridphase-footprint-cachelock"
DEFAULT_TARGET_OUTPUT_DIRECTORY = Path("data/interim/targets")
TARGET_PIPELINE_FILES = (
    "pyproject.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/boundaries.py",
    "src/la_heat/config.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/inventory.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/target_builder.py",
    "src/la_heat/targets.py",
)


@dataclass(frozen=True, slots=True)
class FrozenInventory:
    city: gpd.GeoDataFrame
    primary_overpasses: pd.DataFrame
    scenes: pd.DataFrame
    locks: dict[str, str]


def _config_hash(config: ResearchConfig) -> str:
    return target_config_sha256(config)


def _canonical_tract_manifest_hash(tracts: gpd.GeoDataFrame) -> str:
    fields = [
        "GEOID",
        "geometry_sha256",
        "city_area_fraction",
        "census_land_fraction",
        "special_use_tract",
        "primary_included",
        "primary_exclusion_reason",
        "spatial_block",
        "longitude_quartile",
        "latitude_quartile",
    ]
    missing = set(fields) - set(tracts.columns)
    if missing:
        raise ValueError(f"Primary tract manifest lacks semantic fields: {sorted(missing)}")
    return canonical_frame_sha256(tracts, sort_by=["GEOID"], columns=fields)


def _prepare_primary_tracts(
    config: ResearchConfig,
    city: gpd.GeoDataFrame,
    output_directory: Path,
) -> tuple[gpd.GeoDataFrame, str]:
    boundaries = config.raw["boundaries"]
    study = config.raw["study"]
    download = download_detailed_la_county_tracts(
        layer_url=boundaries["detailed_tract_arcgis_layer"],
        destination=Path("data/raw/census/la_county_2020_tiger_detailed.parquet"),
        state_fips=boundaries["state_fips"],
        county_fips=boundaries["county_fips"],
        expected_feature_count=boundaries["detailed_tract_expected_count"],
    )
    universe = load_city_tracts(
        download.path,
        city,
        analysis_crs=study["crs_analysis"],
        state_fips=boundaries["state_fips"],
        county_fips=boundaries["county_fips"],
        minimum_city_area_fraction=boundaries["minimum_city_area_fraction"],
        exclude_special_use_tracts=boundaries["exclude_special_use_tracts"],
    )
    primary = universe.loc[universe["primary_included"]].copy()
    primary = assign_spatial_blocks(
        primary,
        block_size_km=config.raw["validation"]["spatial_block_size_km"],
    )
    manifest_hash = _canonical_tract_manifest_hash(primary)
    universe["tract_manifest_sha256"] = manifest_hash
    primary["tract_manifest_sha256"] = manifest_hash
    atomic_parquet(universe, output_directory / "tract_universe_manifest.parquet")
    atomic_parquet(primary, output_directory / "primary_tract_manifest.parquet")
    return primary, manifest_hash


def _load_frozen_inventory(config: ResearchConfig) -> FrozenInventory:
    directory = Path("manifests/target_inventory")
    paths = {
        "summary": directory / "inventory_summary.json",
        "city": directory / "city_boundary.geojson",
        "scenes": directory / "scene_inventory.csv",
        "primary": directory / "primary_overpass_manifest.csv",
        "overpasses": directory / "overpass_inventory.csv",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Frozen inventory is incomplete. Run `python -m la_heat.inventory` "
            f"explicitly before target construction. Missing: {missing}"
        )
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    if summary.get("inventory_schema_version") != INVENTORY_SCHEMA_VERSION:
        raise ValueError("Frozen inventory schema is not the current locked version.")
    if summary.get("inventory_config_sha256") != inventory_config_sha256(config):
        raise ValueError(
            "Frozen inventory config hash differs from the current config. "
            "Refresh inventory explicitly, review its diff, then rebuild targets."
        )
    byte_locks = {
        "city": "city_boundary_file_sha256",
        "scenes": "scene_inventory_file_sha256",
        "overpasses": "overpass_inventory_file_sha256",
        "primary": "primary_overpass_manifest_sha256",
    }
    for path_key, summary_key in byte_locks.items():
        if sha256_file(paths[path_key]) != summary.get(summary_key):
            raise ValueError(f"Frozen inventory file failed its byte lock: {path_key}")

    city = gpd.read_file(paths["city"])
    city_geometry_hash = geometry_semantic_sha256(city)
    if city_geometry_hash != summary.get("city_boundary_geometry_sha256"):
        raise ValueError("Frozen City boundary failed its semantic geometry lock.")
    scenes = read_scene_inventory(paths["scenes"])
    if canonical_frame_sha256(scenes, sort_by=["item_id"]) != summary.get(
        "scene_inventory_semantic_sha256"
    ):
        raise ValueError("Frozen scene inventory failed its semantic lock.")
    manifest = read_overpass_inventory(paths["primary"])
    if canonical_frame_sha256(
        manifest, sort_by=["local_date", "overpass_id"]
    ) != summary.get("primary_overpass_manifest_semantic_sha256"):
        raise ValueError("Frozen primary overpass manifest failed its semantic lock.")
    if len(manifest) != summary["full_city_coverage_unambiguous_overpass_count"]:
        raise ValueError("Frozen primary overpass row count failed its lock.")
    if not manifest["primary_eligible"].all():
        raise ValueError("Frozen primary overpass manifest contains an ineligible row.")
    if manifest["local_date"].duplicated().any():
        raise ValueError("Frozen primary overpass manifest contains duplicate local dates.")
    if scenes["item_id"].duplicated().any():
        raise ValueError("Frozen scene inventory contains duplicate scene IDs.")
    locked_scene_ids = set(scenes["item_id"])
    referenced_scene_ids = {
        scene_id
        for scene_ids in manifest["scene_ids"]
        for scene_id in str(scene_ids).split("|")
    }
    if not referenced_scene_ids.issubset(locked_scene_ids):
        raise ValueError("Primary overpass manifest references an unlocked scene.")
    locks = {
        "city_boundary_geometry_sha256": city_geometry_hash,
        "scene_inventory_semantic_sha256": summary[
            "scene_inventory_semantic_sha256"
        ],
        "primary_overpass_manifest_sha256": summary[
            "primary_overpass_manifest_sha256"
        ],
        "primary_overpass_manifest_semantic_sha256": summary[
            "primary_overpass_manifest_semantic_sha256"
        ],
        "inventory_pipeline_sha256": summary["inventory_pipeline_sha256"],
    }
    return FrozenInventory(city, manifest, scenes, locks)


def _fixed_grid_and_zones(
    config: ResearchConfig,
    city: gpd.GeoDataFrame,
    tracts: gpd.GeoDataFrame,
) -> tuple[FixedGrid, np.ndarray, np.ndarray, str]:
    landsat = config.raw["landsat"]
    grid = build_fixed_grid(
        city,
        target_crs=landsat["target_grid_crs"],
        resolution_m=landsat["target_grid_resolution_m"],
        anchor_x_m=landsat["target_grid_anchor_x_m"],
        anchor_y_m=landsat["target_grid_anchor_y_m"],
    )
    projected = tracts.to_crs(grid.crs).reset_index(drop=True)
    zones = rasterize(
        ((geometry, index + 1) for index, geometry in enumerate(projected.geometry)),
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    present = np.unique(zones[zones > 0])
    if not np.array_equal(present, np.arange(1, len(projected) + 1)):
        raise ValueError("At least one primary tract has no pixel center on the fixed grid.")
    land_item = get_static_land_item(config)
    static_land = read_static_land_mask(
        land_item,
        output_shape=grid.shape,
        output_transform=grid.transform,
        output_crs=grid.crs,
        config=config,
    )
    identity = (
        f"{grid.sha256}|zone={hashlib.sha256(zones.tobytes()).hexdigest()}|"
        f"land={hashlib.sha256(np.packbits(static_land.ravel()).tobytes()).hexdigest()}"
    )
    return grid, zones, static_land, identity


def _pipeline_fingerprint() -> tuple[str, dict[str, Any]]:
    project_root = Path(__file__).resolve().parents[2]
    return code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=TARGET_PIPELINE_FILES,
        algorithm_version=TARGET_PIPELINE_ALGORITHM_VERSION,
    )


def _expected_cache_lock(
    base_lock: dict[str, str], row: Any
) -> dict[str, str]:
    return {
        **base_lock,
        "overpass_id": str(row.overpass_id),
        "overpass_source_sha256": str(row.source_lock_sha256),
    }


def _cache_is_current(cache_directory: Path, expected_lock: dict[str, str]) -> bool:
    summary_path = cache_directory / "summary.json"
    if not summary_path.exists():
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if summary.get("cache_lock") != expected_lock:
        return False
    output_files = summary.get("output_files")
    required = {"tract_date_qa.parquet", "scene_contributions.parquet"}
    if not isinstance(output_files, dict) or set(output_files) != required:
        return False
    for filename, record in output_files.items():
        path = cache_directory / filename
        if (
            not path.exists()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            return False
    return True


def _process_overpass(
    row: Any,
    *,
    scenes: pd.DataFrame,
    grid: FixedGrid,
    zones: np.ndarray,
    static_land: np.ndarray,
    grid_identity: str,
    tracts: gpd.GeoDataFrame,
    config: ResearchConfig,
    base_cache_lock: dict[str, str],
    output_directory: Path,
    force: bool,
) -> dict[str, object]:
    overpass_id = str(row.overpass_id)
    cache_directory = output_directory / "by_overpass" / overpass_id
    expected_lock = _expected_cache_lock(base_cache_lock, row)
    if not force and _cache_is_current(cache_directory, expected_lock):
        print(f"[target] cache hit {overpass_id}", flush=True)
        return json.loads((cache_directory / "summary.json").read_text(encoding="utf-8"))

    # Summary is the commit marker. Removing it first makes a mid-write crash
    # fail closed; partial or mixed parquet files cannot become cache hits.
    summary_path = cache_directory / "summary.json"
    summary_path.unlink(missing_ok=True)
    scene_ids = tuple(str(row.scene_ids).split("|"))
    scene_lookup = scenes.set_index("item_id", drop=False)
    aligned = []
    for scene_id in scene_ids:
        if scene_id not in scene_lookup.index:
            raise ValueError(f"Locked overpass references unknown scene: {scene_id}")
        scene = scene_lookup.loc[scene_id]
        asset_hrefs = {
            asset: str(scene[f"{asset}_href"]) for asset in REQUIRED_ASSETS
        }
        print(f"[target] aligning frozen {scene_id}", flush=True)
        aligned.append(
            read_aligned_scene_from_hrefs(
                scene_id=scene_id,
                asset_hrefs=asset_hrefs,
                grid=grid,
                config=config,
            )
        )
    mosaic = mosaic_aligned_scenes(
        scene_ids=[scene.scene_id for scene in aligned],
        st_values=np.stack([scene.lst_c for scene in aligned]),
        qa_valid=np.stack([scene.valid for scene in aligned]),
        st_qa=np.stack([scene.st_uncertainty_k for scene in aligned]),
        cdist=np.stack([scene.cloud_distance_km for scene in aligned]),
        footprint=np.stack([scene.footprint for scene in aligned]),
    )
    aggregated = aggregate_target_mosaic(
        tracts=tracts,
        zone_raster=zones,
        static_land_mask=static_land,
        mosaic=mosaic,
        target_date=str(row.local_date),
        overpass_id=overpass_id,
        platform=str(row.platform),
        scene_ids=scene_ids,
        union_city_coverage_fraction=float(row.union_city_coverage_fraction),
        grid_identity=grid_identity,
        config_sha256=base_cache_lock["target_config_sha256"],
        tract_manifest_sha256=base_cache_lock["tract_manifest_sha256"],
        config=config,
    )
    summary = dict(aggregated.summary)
    summary["primary_overpass_manifest_sha256"] = base_cache_lock[
        "primary_overpass_manifest_sha256"
    ]
    summary["primary_overpass_manifest_semantic_sha256"] = base_cache_lock[
        "primary_overpass_manifest_semantic_sha256"
    ]
    summary["cache_lock"] = expected_lock
    target_path = cache_directory / "tract_date_qa.parquet"
    contribution_path = cache_directory / "scene_contributions.parquet"
    atomic_parquet(aggregated.tract_date_qa, target_path)
    atomic_parquet(aggregated.scene_contributions, contribution_path)
    summary["output_files"] = {
        target_path.name: parquet_file_record(target_path, aggregated.tract_date_qa),
        contribution_path.name: parquet_file_record(
            contribution_path, aggregated.scene_contributions
        ),
    }
    atomic_json(summary, summary_path)
    return summary


def _unlink_generated(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _begin_build_transaction(
    output_directory: Path,
    *,
    target_config_sha256_value: str,
    research_config_file_sha256: str,
) -> None:
    """Withdraw every promoted artifact before any new locks or remote reads."""

    output_directory.mkdir(parents=True, exist_ok=True)
    atomic_json(
        {
            "state": "preparing",
            "promoted_outputs_valid": False,
            "target_config_sha256": target_config_sha256_value,
            "research_config_file_sha256": research_config_file_sha256,
        },
        output_directory / "build_progress.json",
    )
    _unlink_generated(
        [
            output_directory / "development_target_qa.parquet",
            output_directory / "date_summary.parquet",
            output_directory / "scene_contributions.parquet",
            output_directory / "development_targets_model_ready.parquet",
            output_directory / "development_target_qa_partial.parquet",
            output_directory / "date_summary_partial.parquet",
            output_directory / "scene_contributions_partial.parquet",
        ]
    )


def _compile_completed_outputs(
    *,
    manifest: pd.DataFrame,
    output_directory: Path,
    config: ResearchConfig,
    base_cache_lock: dict[str, str],
) -> dict[str, object]:
    target_frames: list[pd.DataFrame] = []
    contribution_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, object]] = []
    for row in manifest.itertuples(index=False):
        directory = output_directory / "by_overpass" / str(row.overpass_id)
        if not _cache_is_current(directory, _expected_cache_lock(base_cache_lock, row)):
            continue
        target_frames.append(pd.read_parquet(directory / "tract_date_qa.parquet"))
        contribution_frames.append(
            pd.read_parquet(directory / "scene_contributions.parquet")
        )
        cached_summary = json.loads(
            (directory / "summary.json").read_text(encoding="utf-8")
        )
        summaries.append(
            {
                key: value
                for key, value in cached_summary.items()
                if key not in {"cache_lock", "output_files"}
            }
        )

    progress: dict[str, object] = {
        **base_cache_lock,
        "expected_overpass_count": int(len(manifest)),
        "completed_overpass_count": int(len(target_frames)),
        "build_complete": len(target_frames) == len(manifest),
        "partial_outputs_only": len(target_frames) != len(manifest),
        "promoted_outputs_valid": False,
    }
    promoted_paths = [
        output_directory / "development_target_qa.parquet",
        output_directory / "date_summary.parquet",
        output_directory / "scene_contributions.parquet",
        output_directory / "development_targets_model_ready.parquet",
    ]
    partial_paths = [
        output_directory / "development_target_qa_partial.parquet",
        output_directory / "date_summary_partial.parquet",
        output_directory / "scene_contributions_partial.parquet",
    ]
    if not target_frames:
        _unlink_generated([*promoted_paths, *partial_paths])
        progress["state"] = "no_current_caches"
        atomic_json(progress, output_directory / "build_progress.json")
        return progress

    all_targets = pd.concat(target_frames, ignore_index=True)
    validate_unique_primary_key(all_targets)
    validate_no_final_year_rows(all_targets, final_year=config.final_test_year)
    validate_static_eligible_denominator(all_targets)
    landsat = config.raw["landsat"]
    validate_target_qa_contract(
        all_targets,
        minimum_footprint_fraction=landsat["minimum_tract_footprint_fraction"],
        minimum_valid_fraction=landsat["minimum_valid_pixel_fraction"],
        minimum_valid_pixels=landsat["minimum_valid_pixels_per_tract"],
    )
    all_contributions = pd.concat(contribution_frames, ignore_index=True)
    date_summary = pd.DataFrame(summaries).sort_values("target_date")
    usable_dates = int(date_summary["date_usable"].sum())
    minimum_usable = config.raw["study"]["minimum_independent_valid_dates"]
    gate_pass = usable_dates >= minimum_usable
    progress["usable_overpass_count"] = usable_dates
    progress["minimum_required_usable_overpasses"] = minimum_usable
    progress["usable_date_gate_pass"] = bool(gate_pass)

    if progress["build_complete"]:
        _unlink_generated(partial_paths)
        full_targets_path = promoted_paths[0]
        date_summary_path = promoted_paths[1]
        contributions_path = promoted_paths[2]
        atomic_parquet(all_targets, full_targets_path)
        atomic_parquet(date_summary, date_summary_path)
        atomic_parquet(all_contributions, contributions_path)
        progress["aggregate_outputs"] = {
            full_targets_path.name: parquet_file_record(full_targets_path, all_targets),
            date_summary_path.name: parquet_file_record(date_summary_path, date_summary),
            contributions_path.name: parquet_file_record(
                contributions_path, all_contributions
            ),
        }
        if gate_pass:
            model_ready = all_targets.loc[
                all_targets["date_usable"] & all_targets["target_available"]
            ]
            model_ready_path = promoted_paths[3]
            atomic_parquet(model_ready, model_ready_path)
            progress["aggregate_outputs"][model_ready_path.name] = (
                parquet_file_record(model_ready_path, model_ready)
            )
            progress["state"] = "model_ready"
            progress["promoted_outputs_valid"] = True
        else:
            promoted_paths[3].unlink(missing_ok=True)
            progress["state"] = "complete_gate_failed"
    else:
        _unlink_generated(promoted_paths)
        atomic_parquet(all_targets, partial_paths[0])
        atomic_parquet(date_summary, partial_paths[1])
        atomic_parquet(all_contributions, partial_paths[2])
        progress["partial_outputs"] = {
            partial_paths[0].name: parquet_file_record(partial_paths[0], all_targets),
            partial_paths[1].name: parquet_file_record(partial_paths[1], date_summary),
            partial_paths[2].name: parquet_file_record(
                partial_paths[2], all_contributions
            ),
        }
        progress["state"] = "partial_ready"
    atomic_json(progress, output_directory / "build_progress.json")
    return progress


def run_target_build(
    config_path: str | Path,
    *,
    limit: int | None = None,
    overpass_id: str | None = None,
    force: bool = False,
    output_directory: str | Path = DEFAULT_TARGET_OUTPUT_DIRECTORY,
) -> dict[str, object]:
    config = load_config(config_path)
    config_sha256 = _config_hash(config)
    config_file_sha256 = sha256_file(config.path)
    output_directory = Path(output_directory)
    _begin_build_transaction(
        output_directory,
        target_config_sha256_value=config_sha256,
        research_config_file_sha256=config_file_sha256,
    )
    frozen = _load_frozen_inventory(config)
    tracts, tract_manifest_sha256 = _prepare_primary_tracts(
        config, frozen.city, output_directory
    )
    grid, zones, static_land, grid_identity = _fixed_grid_and_zones(
        config, frozen.city, tracts
    )
    grid_sha256 = hashlib.sha256(grid_identity.encode()).hexdigest()
    pipeline_sha256, pipeline_payload = _pipeline_fingerprint()
    base_cache_lock = {
        "target_pipeline_sha256": pipeline_sha256,
        "target_config_sha256": config_sha256,
        "research_config_file_sha256": config_file_sha256,
        "tract_manifest_sha256": tract_manifest_sha256,
        "grid_sha256": grid_sha256,
        **frozen.locks,
    }
    atomic_json(
        {
            **base_cache_lock,
            "state": "building",
            "promoted_outputs_valid": False,
            "expected_overpass_count": len(frozen.primary_overpasses),
        },
        output_directory / "build_progress.json",
    )
    grid_payload = {
        "crs": grid.crs,
        "resolution_m": grid.resolution_m,
        "edge_anchor_x_m": grid.anchor_x_m,
        "edge_anchor_y_m": grid.anchor_y_m,
        "bounds": [grid.left, grid.bottom, grid.right, grid.top],
        "width": grid.width,
        "height": grid.height,
        "grid_definition_sha256": grid.sha256,
        "target_grid_identity_sha256": grid_sha256,
        "zone_raster_sha256": hashlib.sha256(zones.tobytes()).hexdigest(),
        "static_land_mask_sha256": hashlib.sha256(
            np.packbits(static_land.ravel()).tobytes()
        ).hexdigest(),
        "categorical_land_resampling": config.raw["static_land_mask"][
            "categorical_resampling"
        ],
        "coverage_method": "source_extent_pixel_centers_and_not_qa_fill",
        "target_pipeline_sha256": pipeline_sha256,
        "target_pipeline_fingerprint": pipeline_payload,
        "target_config_sha256": config_sha256,
        "target_config_payload": target_config_payload(config),
        "research_config_file_sha256": config_file_sha256,
        **frozen.locks,
    }
    atomic_json(grid_payload, output_directory / "fixed_grid_lock.json")

    selected = frozen.primary_overpasses
    if overpass_id is not None:
        selected = selected.loc[selected["overpass_id"] == overpass_id]
        if selected.empty:
            raise ValueError(f"Overpass is not in the primary manifest: {overpass_id}")
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive.")
        selected = selected.head(limit)

    for row in selected.itertuples(index=False):
        _process_overpass(
            row,
            scenes=frozen.scenes,
            grid=grid,
            zones=zones,
            static_land=static_land,
            grid_identity=grid_identity,
            tracts=tracts,
            config=config,
            base_cache_lock=base_cache_lock,
            output_directory=output_directory,
            force=force,
        )
    progress = _compile_completed_outputs(
        manifest=frozen.primary_overpasses,
        output_directory=output_directory,
        config=config,
        base_cache_lock=base_cache_lock,
    )
    print(json.dumps(progress, indent=2))
    return progress


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    parser.add_argument(
        "--output-directory",
        default=str(DEFAULT_TARGET_OUTPUT_DIRECTORY),
        help=(
            "Target-build output directory. Use a separate directory for every "
            "sensitivity analysis; the default preserves the canonical primary build."
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overpass-id")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    run_target_build(
        arguments.config,
        limit=arguments.limit,
        overpass_id=arguments.overpass_id,
        force=arguments.force,
        output_directory=arguments.output_directory,
    )


if __name__ == "__main__":
    main()
