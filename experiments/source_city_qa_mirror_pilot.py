"""Fixed two-city QA-only pilot on provisional 2020 Census mirror geometry.

Exploratory support only: no Landsat thermal asset or target value is opened.
The original official-geometry equivalence gate remains unfinished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import planetary_computer as pc
import rasterio
import requests
import shapely
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as transform_points

from la_heat.aligned_landsat import _read_asset_to_grid
from la_heat.grid import build_fixed_grid
from la_heat.landsat import qa_pixel_clear_land_mask
from la_heat.multicity import geography
from la_heat.multicity.config import CitySpec
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "exports/SOURCE_CITY_METADATA_SCREEN/summary.json"
BOUNDARY_AUDIT = ROOT / "exports/SOURCE_CITY_QA_PILOT/boundary_audit.json"
OUTPUT = ROOT / "exports/SOURCE_CITY_QA_PILOT/mirror_exploratory"
ACTIVE = ROOT / "manifests/multicity/ACTIVE_STAGE.json"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections"
METADATA_SHA256 = "18a5d5b899ab43a1da45b9658a780dd0490c5d7fd30c0223a911a38fb2ce0ef9"
ASSETS = ("qa_pixel", "qa_radsat", "qa", "cdist")
SPECS = (
    ("colorado_springs_co", "Colorado Springs", "08", "0816000", "America/Denver", "EPSG:32613"),
    ("charlotte_nc", "Charlotte", "37", "3712000", "America/New_York", "EPSG:32617"),
)
DISCLAIMER = "基于冻结镜像边界的探索性结果，官方等价性待核验"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _active_scope() -> None:
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    permission = stage["permissions"]
    if (
        stage["stage_id"] != "source_city_qa_pilot"
        or stage["state"] != "running_exploratory_mirror_qa_only"
    ):
        raise RuntimeError("Exploratory mirror QA stage is not active.")
    if not all(
        permission[key]
        for key in (
            "read_new_candidate_public_metadata",
            "read_new_candidate_worldcover_static_support",
            "read_new_candidate_landsat_asset_hrefs",
        )
    ) or any(
        permission[key]
        for key in (
            "read_new_candidate_targets",
            "read_external_targets",
            "fit_model",
            "score_model",
        )
    ):
        raise RuntimeError("Exploratory QA read permissions are not exactly scoped.")


def _snapshot_raw(raw: geography.LayerAcquisition, directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, content in raw.raw_files.items():
        destination = directory / name
        digest = hashlib.sha256(content).hexdigest()
        if destination.exists() and _sha(destination) != digest:
            raise RuntimeError(f"Frozen mirror response changed: {destination}")
        if not destination.exists():
            destination.write_bytes(content)
        hashes[name] = digest
    return hashes


def _mirror_geometry(city: CitySpec, info: dict, directory: Path):
    session = geography._retrying_session()
    place_source = geography.LayerCandidate(
        "frozen mirror place", info["place_source"], "Esri mirror", "2020 mirror"
    )
    tract_source = geography.LayerCandidate(
        "frozen mirror tract", info["tract_source"], "Esri mirror", "2020 mirror"
    )
    place_raw = geography._download_place(session, place_source, city)
    raw_place = place_raw.frame
    raw_place_hashes = _snapshot_raw(place_raw, directory / "mirror_place")
    if (
        raw_place.crs is None
        or len(raw_place) != 1
        or raw_place.geometry.isna().any()
        or not raw_place.geometry.is_valid.all()
    ):
        _write_json(
            directory / "mirror_geometry_preflight.json",
            {
                "city_id": city.id,
                "raw_place_response_sha256": raw_place_hashes,
                "raw_place_feature_count": len(raw_place),
                "raw_place_crs": str(raw_place.crs),
                "raw_place_validity_reasons": [
                    shapely.is_valid_reason(geom) for geom in raw_place.geometry
                ],
                "status": "raw_mirror_geometry_invalid_no_qa_read",
            },
        )
        raise RuntimeError("Mirror place geometry is missing, duplicated or invalid.")
    place = geography.standardize_place(raw_place, city)
    tracts_raw = geography._download_tracts(
        session, tract_source, city, tuple(float(v) for v in place.total_bounds)
    )
    raw_tracts = tracts_raw.frame
    if (
        raw_tracts.crs is None
        or raw_tracts.geometry.isna().any()
        or not raw_tracts.geometry.is_valid.all()
        or raw_tracts["GEOID"].isna().any()
        or raw_tracts["GEOID"].astype(str).duplicated().any()
    ):
        raise RuntimeError("Mirror tract geometry or GEOID is missing, duplicated or invalid.")
    tracts = geography.standardize_tracts(raw_tracts, city)
    _, primary = geography.select_city_tracts(
        place,
        tracts,
        city_id=city.id,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    if (
        primary.geometry.isna().any()
        or primary.geometry.is_empty.any()
        or not primary.geometry.is_valid.all()
        or primary["tract_geoid"].astype(str).duplicated().any()
    ):
        raise RuntimeError("Selected mirror tract geometry is unusable for rasterization.")
    geoids = primary["tract_geoid"].astype(str).tolist()
    place_hash = canonical_sha256(
        shapely.to_wkb(shapely.normalize(place.geometry.iloc[0]), hex=True)
    )
    if place_hash != info["place_geometry_sha256"] or geoids != info["selected_tract_geoids"]:
        raise RuntimeError("Frozen mirror place or tract identity changed.")
    tract_hashes = _snapshot_raw(tracts_raw, directory / "mirror_tract")
    record = {
        "source_place_url": info["place_source"],
        "source_tract_url": info["tract_source"],
        "vintage_evidence": (
            "Esri 2020 Census Redistricting layer identity; "
            "not independently verified against Census geometry"
        ),
        "source_crs": str(raw_place.crs),
        "source_tract_crs": str(raw_tracts.crs),
        "place_geometry_sha256": place_hash,
        "tract_count": len(geoids),
        "tract_geoids_sha256": canonical_sha256(geoids),
        "raw_place_response_sha256": raw_place_hashes,
        "raw_tract_response_sha256": tract_hashes,
        "geometry_valid_and_unique": True,
        "official_equivalence_verified": False,
    }
    _write_json(directory / "mirror_geometry.json", record)
    return place, primary, record


def _stac_item(session: requests.Session, collection: str, item_id: str) -> dict:
    response = session.get(f"{STAC}/{collection}/items/{item_id}", timeout=(10, 30))
    response.raise_for_status()
    item = response.json()
    if item.get("id") != item_id or item.get("collection") != collection:
        raise RuntimeError("STAC item identity changed.")
    return item


def _grid_and_zones(place: gpd.GeoDataFrame, primary: gpd.GeoDataFrame, crs: str):
    grid = build_fixed_grid(
        place, target_crs=crs, resolution_m=30.0, anchor_x_m=15.0, anchor_y_m=15.0
    )
    projected = primary.to_crs(crs)
    zones = rasterize(
        [(geom, index + 1) for index, geom in enumerate(projected.geometry)],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    if set(np.unique(zones)) != set(range(len(primary) + 1)):
        raise RuntimeError("Mirror zone raster omitted or changed a selected tract.")
    place_mask = rasterize(
        [(place.to_crs(crs).geometry.iloc[0], 1)],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    return grid, zones, place_mask


def _worldcover(session: requests.Session, item_ids: list[str], grid):
    classes = np.zeros(grid.shape, dtype=np.uint8)
    items = []
    seam_conflicts = 0
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MAX_RETRY": "1",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    for item_id in item_ids:
        if not item_id.startswith("ESA_WorldCover_10m_2020_v100_"):
            raise RuntimeError("WorldCover item version changed.")
        item = _stac_item(session, "esa-worldcover", item_id)
        if "map" not in item.get("assets", {}):
            raise RuntimeError("Frozen WorldCover map asset is missing.")
        signed = pc.sign(item["assets"]["map"]["href"])
        with rasterio.Env(**env), rasterio.open(signed) as source:
            if source.crs is None or source.count != 1:
                raise RuntimeError("WorldCover source has no CRS or is not one band.")
            with WarpedVRT(
                source,
                crs=grid.crs,
                transform=grid.transform,
                height=grid.height,
                width=grid.width,
                resampling=Resampling.mode,
                src_nodata=0,
                nodata=0,
            ) as vrt:
                part = vrt.read(1)
        conflict = (classes != 0) & (part != 0) & (classes != part)
        if conflict.any():
            if len(items) != 1:
                raise RuntimeError("WorldCover conflict spans more than one prior tile.")
            prior = items[0]["bbox"]
            current = item["bbox"]
            if not (
                prior[2] == current[2]
                and prior[0] == current[0]
                and (prior[3] == current[1] or current[3] == prior[1])
            ):
                raise RuntimeError("WorldCover conflict is not an adjacent-tile seam.")
            rows, cols = np.nonzero(conflict)
            xs, ys = grid.transform * (cols + 0.5, rows + 0.5)
            lons, lats = transform_points(grid.crs, "EPSG:4326", xs.tolist(), ys.tolist())
            previous_owns = np.array(
                [
                    prior[0] <= x < prior[2] and prior[1] <= y < prior[3]
                    for x, y in zip(lons, lats, strict=True)
                ]
            )
            current_owns = np.array(
                [
                    current[0] <= x < current[2] and current[1] <= y < current[3]
                    for x, y in zip(lons, lats, strict=True)
                ]
            )
            if not np.all(previous_owns ^ current_owns):
                raise RuntimeError("WorldCover seam pixel center has ambiguous tile ownership.")
            classes[rows[current_owns], cols[current_owns]] = part[
                rows[current_owns], cols[current_owns]
            ]
            seam_conflicts += len(rows)
        classes[(classes == 0) & (part != 0)] = part[(classes == 0) & (part != 0)]
        items.append(
            {"id": item_id, "asset_key": "map", "source_crs": str(source.crs), "bbox": item["bbox"]}
        )
    return classes, items, seam_conflicts


def _count(zones: np.ndarray, mask: np.ndarray, n: int) -> np.ndarray:
    return np.bincount(zones[mask], minlength=n + 1)[1:]


def _read_scene_qa(session: requests.Session, scene_id: str, grid):
    item = _stac_item(session, "landsat-c2-l2", scene_id)
    properties = item.get("properties", {})
    if (
        properties.get("landsat:collection_category") != "T1"
        or properties.get("landsat:correction") != "L2SP"
    ):
        raise RuntimeError("Frozen Landsat scene product identity changed.")
    if not set(ASSETS).issubset(item.get("assets", {})):
        raise RuntimeError("Frozen QA asset is missing.")
    arrays = {}
    coverage = None
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MAX_RETRY": "1",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    with rasterio.Env(**env):
        for key in ASSETS:
            href = pc.sign(item["assets"][key]["href"])
            array, observed = _read_asset_to_grid(
                href,
                grid=grid,
                fallback_nodata=-9999 if key in {"qa", "cdist"} else 0,
            )
            if coverage is not None and not np.array_equal(coverage, observed):
                raise RuntimeError("Four QA assets do not share one source footprint.")
            coverage = observed
            arrays[key] = array
    return arrays, coverage


def qa_stages(arrays: dict[str, np.ndarray], coverage: np.ndarray) -> dict[str, np.ndarray]:
    """The fixed QA4K-only upper bound, without ST DN or physical-value masks."""
    qa_pixel = arrays["qa_pixel"]
    footprint = coverage & ((qa_pixel.astype(np.uint16) & 1) == 0)
    clear = footprint & qa_pixel_clear_land_mask(qa_pixel, excluded_bits=(0, 1, 2, 3, 4, 5, 7))
    terrain = clear & ((arrays["qa_radsat"].astype(np.uint16) & (1 << 11)) == 0)
    nonfill = terrain & (arrays["qa"] != -9999) & (arrays["cdist"] != -9999)
    cloud_distance = nonfill & (arrays["cdist"] >= 100)
    qa4k = cloud_distance & (arrays["qa"] <= 400)
    return {
        "observed_footprint": footprint,
        "clear_qa_pixel": clear,
        "terrain_visible": terrain,
        "qa_cdist_nonfill": nonfill,
        "cloud_distance_ge_1km": cloud_distance,
        "st_qa_le_4k": qa4k,
    }


def summarize_date(
    *,
    city_id: str,
    local_date: str,
    scene_ids: list[str],
    stage_masks: dict[str, np.ndarray],
    zones: np.ndarray,
    eligible: np.ndarray,
    place_mask: np.ndarray,
    static_counts: np.ndarray,
    zone_counts: np.ndarray,
    grid_sha256: str,
) -> dict:
    n = len(static_counts)
    observed = stage_masks["observed_footprint"]
    footprint_count = _count(zones, observed & (zones > 0), n)
    footprint_fraction = np.divide(
        footprint_count, zone_counts, out=np.zeros(n, dtype=float), where=zone_counts > 0
    )
    support = footprint_fraction >= 0.90
    per_stage = {}
    per_tract = []
    stage_counts = {}
    for name, mask in stage_masks.items():
        count = _count(zones, mask & eligible, n)
        fraction = np.divide(
            count, static_counts, out=np.zeros(n, dtype=float), where=static_counts > 0
        )
        retained = support & (static_counts > 0) & (count >= 20) & (fraction >= 0.60)
        per_stage[name] = {
            "eligible_pixels_retained": int(count.sum()),
            "tracts_meeting_fixed_support": int(retained.sum()),
            "tract_retention_fraction": float(retained.mean()),
        }
        stage_counts[name] = count
    final = per_stage["st_qa_le_4k"]["tracts_meeting_fixed_support"]
    for index in range(n):
        per_tract.append(
            {
                "tract_index": index + 1,
                "eligible_land_pixels_static": int(static_counts[index]),
                "zone_pixels_static": int(zone_counts[index]),
                "observed_footprint_pixels": int(footprint_count[index]),
                "footprint_fraction": float(footprint_fraction[index]),
                "qa4k_pixels": int(stage_counts["st_qa_le_4k"][index]),
                "qa4k_fraction_of_fixed_eligible": float(
                    stage_counts["st_qa_le_4k"][index] / static_counts[index]
                )
                if static_counts[index]
                else None,
            }
        )
    city_coverage = float((observed & place_mask).sum() / place_mask.sum())
    provisional_support = city_coverage >= 0.98 and final / n >= 0.50
    return {
        "city_id": city_id,
        "local_date": local_date,
        "scene_ids": scene_ids,
        "scene_count": len(scene_ids),
        "status": "provisional_qa_support" if provisional_support else "qa_support_insufficient",
        "disclaimer": DISCLAIMER,
        "official_geometry_equivalence_verified": False,
        "thermal_value_or_st_dn_read": False,
        "grid_sha256": grid_sha256,
        "city_union_observed_coverage_fraction": city_coverage,
        "tract_count": n,
        "static_zero_denominator_tracts": int((static_counts == 0).sum()),
        "tracts_meeting_footprint_ge_0_90": int(support.sum()),
        "stage_retention": per_stage,
        "final_qa4k_tract_count": final,
        "final_qa4k_tract_fraction": float(final / n),
        "per_tract": per_tract,
    }


def _city(city_tuple: tuple, metadata: dict, audit: dict) -> dict:
    city_id, name, state, geoid, timezone, crs = city_tuple
    city = CitySpec(
        city_id,
        name,
        state,
        geoid,
        timezone,
        crs,
        "exploratory_mirror_qa_only",
        "sealed",
        Path(__file__),
    )
    info = metadata["cities"][city_id]
    if [entry["local_date"] for entry in audit["cities"][city_id]["preselected_overpasses"]] != [
        entry["local_date"] for entry in info["qa_pilot_design_not_authorized"]["date_scene_ids"]
    ]:
        raise RuntimeError("Frozen pilot dates changed.")
    directory = OUTPUT / city_id
    place, primary, mirror = _mirror_geometry(city, info, directory)
    grid, zones, place_mask = _grid_and_zones(place, primary, crs)
    session = requests.Session()
    classes, worldcover_items, seam_conflicts = _worldcover(
        session, info["worldcover_2020_catalog"]["item_ids"], grid
    )
    if np.any((zones > 0) & (classes == 0)):
        raise RuntimeError("WorldCover leaves unknown class cells inside frozen tract support.")
    eligible = (zones > 0) & (classes != 0) & (classes != 80)
    n = len(primary)
    zone_counts = _count(zones, zones > 0, n)
    static_counts = _count(zones, eligible, n)
    if np.any(zone_counts == 0) or np.any(static_counts == 0):
        raise RuntimeError("A frozen mirror tract has zero zone or eligible-land denominator.")
    directory.mkdir(parents=True, exist_ok=True)
    support_path = directory / "fixed_support.npz"
    if support_path.exists():
        with np.load(support_path) as previous:
            if not np.array_equal(previous["zones"], zones) or not np.array_equal(
                previous["eligible"], eligible
            ):
                raise RuntimeError("Existing provisional fixed support changed.")
    else:
        np.savez_compressed(support_path, zones=zones, eligible=eligible, classes=classes)
    support = {
        "city_id": city_id,
        "disclaimer": DISCLAIMER,
        "mirror": mirror,
        "worldcover_items": worldcover_items,
        "worldcover_adjacent_tile_seam_conflict_cells": seam_conflicts,
        "worldcover_seam_rule": (
            "For conflicting 30 m mode-resampled cells only, select the adjacent "
            "2020 tile containing the cell center; no QA result was inspected."
        ),
        "worldcover_support_file": str(support_path.relative_to(ROOT)).replace("\\", "/"),
        "worldcover_support_file_sha256": _sha(support_path),
        "grid_sha256": grid.sha256,
        "grid_crs": grid.crs,
        "grid_shape": grid.shape,
        "grid_anchor_m": [15, 15],
        "tract_geoids": primary["tract_geoid"].astype(str).tolist(),
        "fixed_eligible_land_count_by_tract": static_counts.tolist(),
        "fixed_zone_count_by_tract": zone_counts.tolist(),
        "eligible_land_cell_count": int(eligible.sum()),
        "denominator_date_invariant": True,
        "official_geometry_equivalence_verified": False,
    }
    _write_json(directory / "support.json", support)
    summaries = []
    for selected in info["qa_pilot_design_not_authorized"]["date_scene_ids"]:
        local_date, scene_ids = selected["local_date"], selected["scene_ids"]
        path = directory / "dates" / f"{local_date}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("scene_ids") != scene_ids or existing.get("grid_sha256") != grid.sha256:
                raise RuntimeError("Existing date result differs from frozen input.")
            summaries.append(existing)
            print(city_id, local_date, existing["status"], "cached", flush=True)
            continue
        try:
            layers = {
                key: np.zeros(grid.shape, dtype=bool)
                for key in (
                    "observed_footprint",
                    "clear_qa_pixel",
                    "terrain_visible",
                    "qa_cdist_nonfill",
                    "cloud_distance_ge_1km",
                    "st_qa_le_4k",
                )
            }
            for scene_id in scene_ids:
                arrays, coverage = _read_scene_qa(session, scene_id, grid)
                for key, stage in qa_stages(arrays, coverage).items():
                    layers[key] |= stage
            result = summarize_date(
                city_id=city_id,
                local_date=local_date,
                scene_ids=scene_ids,
                stage_masks=layers,
                zones=zones,
                eligible=eligible,
                place_mask=place_mask,
                static_counts=static_counts,
                zone_counts=zone_counts,
                grid_sha256=grid.sha256,
            )
            for row, tract_geoid in zip(result["per_tract"], support["tract_geoids"], strict=True):
                row["tract_geoid"] = tract_geoid
        except (
            requests.RequestException,
            rasterio.errors.RasterioError,
            ValueError,
            RuntimeError,
            OSError,
        ) as exc:
            result = {
                "city_id": city_id,
                "local_date": local_date,
                "scene_ids": scene_ids,
                "grid_sha256": grid.sha256,
                "status": "technical_read_failure",
                "error_type": type(exc).__name__,
                "disclaimer": DISCLAIMER,
                "official_geometry_equivalence_verified": False,
                "thermal_value_or_st_dn_read": False,
            }
        _write_json(path, result)
        summaries.append(result)
        print(city_id, local_date, result["status"], flush=True)
    counts = {
        status: sum(row["status"] == status for row in summaries)
        for status in (
            "provisional_qa_support",
            "qa_support_insufficient",
            "technical_read_failure",
        )
    }
    city_result = {
        "city_id": city_id,
        "disclaimer": DISCLAIMER,
        "support_record": str((directory / "support.json").relative_to(ROOT)).replace("\\", "/"),
        "support_record_sha256": _sha(directory / "support.json"),
        "date_result_counts": counts,
        "date_results": [
            {"local_date": row["local_date"], "status": row["status"]} for row in summaries
        ],
        "five_year_exploratory_gate": (
            counts["provisional_qa_support"] >= 6
            and all(
                any(
                    row["local_date"].startswith(str(y))
                    and row["status"] == "provisional_qa_support"
                    for row in summaries
                )
                for y in range(2020, 2025)
            )
        ),
        "official_geometry_equivalence_verified": False,
        "thermal_target_available_verified": False,
    }
    _write_json(directory / "city_summary.json", city_result)
    return city_result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=[row[0] for row in SPECS])
    parser.add_argument("--official-colorado-prepare", action="store_true")
    parser.add_argument("--official-colorado-support", action="store_true")
    parser.add_argument("--official-colorado-qa", action="store_true")
    parser.add_argument("--official-colorado-qa-retry-technical", action="store_true")
    args = parser.parse_args()
    if args.official_colorado_prepare:
        _prepare_official_colorado()
        return
    if args.official_colorado_support:
        _build_official_colorado_support()
        return
    if args.official_colorado_qa:
        _run_official_colorado_qa(retry_technical=False)
        return
    if args.official_colorado_qa_retry_technical:
        _run_official_colorado_qa(retry_technical=True)
        return
    _active_scope()
    if _sha(METADATA) != METADATA_SHA256:
        raise RuntimeError("Frozen metadata summary changed.")
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    audit = json.loads(BOUNDARY_AUDIT.read_text(encoding="utf-8"))
    output = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "disclaimer": DISCLAIMER,
        "contract_amendment": "docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md#9",
        "metadata_sha256": METADATA_SHA256,
        "prior_boundary_audit_sha256": _sha(BOUNDARY_AUDIT),
        "official_geometry_equivalence_verified": False,
        "thermal_or_target_values_read": False,
        "cities": {},
    }
    for spec in SPECS:
        if args.city and spec[0] != args.city:
            continue
        try:
            output["cities"][spec[0]] = _city(spec, metadata, audit)
        except (
            requests.RequestException,
            rasterio.errors.RasterioError,
            ValueError,
            RuntimeError,
            OSError,
        ) as exc:
            output["cities"][spec[0]] = {
                "city_id": spec[0],
                "status": "city_technical_or_geometry_blocker",
                "error_type": type(exc).__name__,
                "error_note": (
                    str(exc)
                    if type(exc) is RuntimeError
                    else "Remote read or raster failure; no scientific QA failure inferred."
                ),
                "official_geometry_equivalence_verified": False,
            }
            print(spec[0], "city_technical_or_geometry_blocker", type(exc).__name__, flush=True)
        _write_json(OUTPUT / "summary.json", output)
    print("Summary:", OUTPUT / "summary.json")


def _prepare_official_colorado() -> None:
    """Freeze official support geometry and Landsat catalog before any thermal access."""
    from experiments.source_city_metadata_screen import catalog_query
    from experiments.source_city_qa_boundary_audit import (
        _tiger_frame,
        _tiger_to_existing_fields,
        compare_geometry,
    )
    from la_heat.inventory import SceneRecord, group_physical_overpasses

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    if stage["state"] != "running_colorado_official_support_catalog_qa_target_only":
        raise RuntimeError("Official Colorado support/catalog stage is not active.")
    if not stage["permissions"]["read_new_candidate_public_metadata"]:
        raise RuntimeError("Official Colorado metadata permission is closed.")
    if _sha(METADATA) != METADATA_SHA256:
        raise RuntimeError("Frozen mirror metadata summary changed.")
    source_dir = ROOT / "data/raw/source_city_official_boundaries/2020/colorado"
    place_zip = source_dir / "tl_2020_08_place.zip"
    tract_zip = source_dir / "tl_2020_08_tract.zip"
    expected = {
        place_zip: "3f5f0c917a4005c8c4fa081610db03f10ac3a83af98654849b7ea6936ae0694b",
        tract_zip: "111978fb25abed1db139680abe21c08b61a8d6fdc4339f79b6e0b1866adbb3d7",
    }
    if any(_sha(path) != digest for path, digest in expected.items()):
        raise RuntimeError("User-supplied official Colorado ZIP bytes changed.")
    city = CitySpec(*SPECS[0], "official_2020_support_catalog_only", "sealed", Path(__file__))
    place_rows = _tiger_to_existing_fields(_tiger_frame(place_zip, role="place"), role="place")
    tract_rows = _tiger_to_existing_fields(_tiger_frame(tract_zip, role="tract"), role="tract")
    selected_place = place_rows[place_rows["GEOID"].astype(str) == city.census_place_geoid]
    if len(selected_place) != 1:
        raise RuntimeError("Original TIGER ZIP does not contain one Colorado Springs place.")
    place = geography.standardize_place(selected_place, city)
    tracts = geography.standardize_tracts(tract_rows, city)
    _, primary = geography.select_city_tracts(
        place, tracts, city_id=city.id, analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5, exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))["cities"][city.id]
    mirror_dir = OUTPUT / city.id
    mirror_place = geography.standardize_place(
        gpd.read_file(mirror_dir / "mirror_place/features.geojson"), city,
    )
    mirror_tracts = geography.standardize_tracts(
        gpd.read_file(mirror_dir / "mirror_tract/features_0000.geojson"), city,
    )
    _, mirror_primary = geography.select_city_tracts(
        mirror_place, mirror_tracts, city_id=city.id, analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5, exclude_special_use_tracts=True,
    )
    mirror_primary = mirror_primary.sort_values("tract_geoid").reset_index(drop=True)
    comparison = compare_geometry(
        (place, None, primary, {}), (mirror_place, None, mirror_primary, {}),
        crs=city.target_grid_crs,
    )
    if comparison["passes_pixel_scale_gate"] or comparison["zone_disagreement_30m_cells"] != 500:
        raise RuntimeError("Official/mirror difference no longer matches the approved incident.")
    if primary["tract_geoid"].tolist() != metadata["selected_tract_geoids"]:
        raise RuntimeError("Official selected tract GEOIDs changed from the audited 110.")
    grid, zones, place_mask = _grid_and_zones(place, primary, city.target_grid_crs)
    common = gpd.GeoDataFrame(
        geometry=[shapely.union_all([
            place.to_crs(city.target_grid_crs).geometry.iloc[0],
            mirror_place.to_crs(city.target_grid_crs).geometry.iloc[0],
        ])], crs=city.target_grid_crs,
    )
    comparison_grid = build_fixed_grid(
        common, target_crs=city.target_grid_crs, resolution_m=30,
        anchor_x_m=15, anchor_y_m=15,
    )
    geoids = primary["tract_geoid"].tolist()
    code = {geoid: i + 1 for i, geoid in enumerate(geoids)}
    def zones_on_common(frame):
        projected = frame.to_crs(city.target_grid_crs)
        return rasterize(
            [
                (geom, code[geoid])
                for geoid, geom in zip(
                    projected["tract_geoid"], projected.geometry, strict=True
                )
            ],
            out_shape=comparison_grid.shape, transform=comparison_grid.transform,
            fill=0, all_touched=False, dtype="int32",
        )
    a, b = zones_on_common(primary), zones_on_common(mirror_primary)
    rows, cols = np.nonzero(a != b)
    if len(rows) != 500:
        raise RuntimeError("Spatial difference raster disagrees with frozen audit.")
    xs, ys = comparison_grid.transform * (cols + 0.5, rows + 0.5)
    directory = ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co"
    directory.mkdir(parents=True, exist_ok=True)
    difference = directory / "zone_difference_cells.csv"
    lines = ["row,col,x_utm13_m,y_utm13_m,official_geoid,mirror_geoid"]
    for row, col, x, y in zip(rows, cols, xs, ys, strict=True):
        official_id = geoids[a[row, col] - 1] if a[row, col] else ""
        mirror_id = geoids[b[row, col] - 1] if b[row, col] else ""
        lines.append(f"{row},{col},{x:.3f},{y:.3f},{official_id},{mirror_id}")
    new_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    if difference.exists() and difference.read_bytes() != new_bytes:
        raise RuntimeError("Existing official difference-cell record changed.")
    if not difference.exists():
        difference.write_bytes(new_bytes)
    client = requests.Session()
    candidates = []
    year_counts = {}
    for year in range(2020, 2025):
        landsat, query = catalog_query(client, city, place.to_crs("EPSG:4326"), year)
        scenes = [
            SceneRecord(
                item_id=str(r.item_id), platform=str(r.platform),
                acquired_utc=datetime.fromisoformat(str(r.acquired_utc).replace("Z", "+00:00")),
                local_date=str(r.acquisition_local_date), wrs_path=str(r.wrs_path),
                wrs_row=str(r.wrs_row), cloud_cover_percent=float("nan"),
                city_coverage_fraction=float(r.city_overlap_fraction), geometry_wgs84=r.geometry,
                asset_hrefs={},
            ) for r in landsat.to_crs("EPSG:4326").itertuples(index=False)
        ]
        grouped = group_physical_overpasses(
            scenes, city_geometry_wgs84=place.geometry.union_all(),
            analysis_crs="EPSG:5070", maximum_time_gap_minutes=15,
        )
        selected = [item for item in grouped if item.union_city_coverage_fraction >= 0.98
                    and not item.ambiguous_local_date]
        for item in selected:
            candidates.append({"local_date": item.local_date, "scene_ids": list(item.scene_ids)})
        year_counts[str(year)] = {
            "raw_catalog_items": query["query_response_items"],
            "intersecting_scenes": len(landsat), "candidate_overpasses": len(selected),
        }
        print("official catalog", year, len(selected), flush=True)
    old = {
        (date, tuple(scenes)) for year in metadata["years"].values()
        for date, scenes in zip(year["candidate_dates"], year["candidate_scene_ids"], strict=True)
    }
    fresh = {(item["local_date"], tuple(item["scene_ids"])) for item in candidates}
    manifest = {
        "state": "official_support_catalog_frozen_before_new_target_reads",
        "official_place_zip_sha256": expected[place_zip],
        "official_tract_zip_sha256": expected[tract_zip],
        "mirror_equivalence_gate_passed": False,
        "comparison": comparison,
        "difference_cells_csv_sha256": _sha(difference),
        "official_tract_geoids": geoids,
        "official_grid_sha256": grid.sha256,
        "official_grid_shape": grid.shape,
        "official_place_cell_count": int(place_mask.sum()),
        "candidate_rules_unchanged": True,
        "years": year_counts,
        "candidate_overpasses": candidates,
        "new_vs_mirror": [dict(local_date=d, scene_ids=list(s)) for d, s in sorted(fresh - old)],
        "removed_vs_mirror": [
            dict(local_date=d, scene_ids=list(s)) for d, s in sorted(old - fresh)
        ],
        "no_thermal_or_qa_pixels_read": True,
    }
    path = directory / "official_catalog.json"
    if path.exists() and (
        json.loads(path.read_text(encoding="utf-8"))["candidate_overpasses"] != candidates
    ):
        raise RuntimeError("Frozen official candidate list changed.")
    _write_json(path, manifest)
    print("Official catalog:", path, "candidates", len(candidates), flush=True)


def _build_official_colorado_support() -> None:
    """Recompute land support from the user-supplied original TIGER geometry."""
    from experiments.source_city_qa_boundary_audit import _tiger_frame, _tiger_to_existing_fields

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    if stage["state"] != "running_colorado_official_support_catalog_qa_target_only":
        raise RuntimeError("Official Colorado support stage is not active.")
    if not stage["permissions"]["read_new_candidate_worldcover_static_support"]:
        raise RuntimeError("Official Colorado WorldCover permission is closed.")
    city = CitySpec(*SPECS[0], "official_2020_support_only", "sealed", Path(__file__))
    source_dir = ROOT / "data/raw/source_city_official_boundaries/2020/colorado"
    zip_place = source_dir / "tl_2020_08_place.zip"
    zip_tract = source_dir / "tl_2020_08_tract.zip"
    catalog_path = (
        ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co"
        / "official_catalog.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog["official_place_zip_sha256"] != _sha(zip_place):
        raise RuntimeError("Official place ZIP differs from frozen catalog.")
    if catalog["official_tract_zip_sha256"] != _sha(zip_tract):
        raise RuntimeError("Official tract ZIP differs from frozen catalog.")
    place_rows = _tiger_to_existing_fields(_tiger_frame(zip_place, role="place"), role="place")
    tract_rows = _tiger_to_existing_fields(_tiger_frame(zip_tract, role="tract"), role="tract")
    place = geography.standardize_place(
        place_rows[place_rows["GEOID"].astype(str) == city.census_place_geoid], city
    )
    tracts = geography.standardize_tracts(tract_rows, city)
    _, primary = geography.select_city_tracts(
        place, tracts, city_id=city.id, analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5, exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    if primary["tract_geoid"].tolist() != catalog["official_tract_geoids"]:
        raise RuntimeError("Official tract list differs from frozen catalog.")
    grid, zones, place_mask = _grid_and_zones(place, primary, city.target_grid_crs)
    if grid.sha256 != catalog["official_grid_sha256"]:
        raise RuntimeError("Official grid differs from frozen catalog.")
    item_ids = json.loads(METADATA.read_text(encoding="utf-8"))["cities"][city.id][
        "worldcover_2020_catalog"
    ]["item_ids"]
    classes, worldcover_items, seam_conflicts = _worldcover(requests.Session(), item_ids, grid)
    if np.any((zones > 0) & (classes == 0)):
        raise RuntimeError("WorldCover has unknown classes in official tract zones.")
    eligible = (zones > 0) & (classes != 80)
    zone_counts = _count(zones, zones > 0, len(primary))
    static_counts = _count(zones, eligible, len(primary))
    if np.any(zone_counts == 0) or np.any(static_counts == 0):
        raise RuntimeError("Official tract has zero zone or eligible-land denominator.")
    directory = catalog_path.parent
    support_path = directory / "fixed_support.npz"
    if support_path.exists():
        with np.load(support_path) as previous:
            if not np.array_equal(previous["zones"], zones) or not np.array_equal(
                previous["eligible"], eligible
            ):
                raise RuntimeError("Frozen official support changed.")
    else:
        np.savez_compressed(support_path, zones=zones, eligible=eligible, classes=classes)
    old_support = json.loads((OUTPUT / city.id / "support.json").read_text(encoding="utf-8"))
    old_counts = np.asarray(old_support["fixed_eligible_land_count_by_tract"], dtype=int)
    deltas = static_counts - old_counts
    record = {
        "state": "official_2020_fixed_support_complete",
        "mirror_equivalence_gate_passed": False,
        "official_catalog_sha256": _sha(catalog_path),
        "official_place_zip_sha256": _sha(zip_place),
        "official_tract_zip_sha256": _sha(zip_tract),
        "grid_sha256": grid.sha256,
        "grid_shape": grid.shape,
        "grid_crs": grid.crs,
        "grid_anchor_m": [15, 15],
        "worldcover_items": worldcover_items,
        "worldcover_seam_conflict_cells": seam_conflicts,
        "worldcover_seam_rule": old_support["worldcover_seam_rule"],
        "tract_geoids": primary["tract_geoid"].tolist(),
        "fixed_zone_count_by_tract": zone_counts.tolist(),
        "fixed_eligible_land_count_by_tract": static_counts.tolist(),
        "fixed_eligible_land_cell_count": int(eligible.sum()),
        "zero_land_denominator_tracts": 0,
        "denominator_date_invariant": True,
        "mirror_eligible_land_cell_count": old_support["eligible_land_cell_count"],
        "tracts_with_changed_land_denominator": int(np.count_nonzero(deltas)),
        "land_denominator_deltas_by_tract": dict(zip(
            primary["tract_geoid"].tolist(), deltas.tolist(), strict=True
        )),
        "fixed_support_npz_sha256": _sha(support_path),
        "all_mirror_derived_qa_target_and_predictors_require_revalidation": True,
    }
    _write_json(directory / "support.json", record)
    print("Official support:", int(eligible.sum()), "eligible cells;",
          int(np.count_nonzero(deltas)), "tract denominators changed", flush=True)


def _run_official_colorado_qa(*, retry_technical: bool) -> None:
    """Resume all frozen official candidate dates; cache QA arrays for target stage."""
    from experiments.source_city_qa_boundary_audit import _tiger_frame, _tiger_to_existing_fields

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    permission = stage["permissions"]
    if stage["state"] != "running_colorado_official_support_catalog_qa_target_only":
        raise RuntimeError("Official Colorado QA stage is not active.")
    if not all(permission[key] for key in (
        "read_new_candidate_public_metadata",
        "read_new_candidate_worldcover_static_support",
        "read_new_candidate_landsat_asset_hrefs",
    )) or any(permission[key] for key in ("fit_model", "score_model", "read_external_targets")):
        raise RuntimeError("Official Colorado QA permissions are not exactly scoped.")
    directory = ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co"
    catalog_path, support_path = directory / "official_catalog.json", directory / "support.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    support = json.loads(support_path.read_text(encoding="utf-8"))
    if support["official_catalog_sha256"] != _sha(catalog_path):
        raise RuntimeError("Official catalog changed after support construction.")
    city = CitySpec(*SPECS[0], "official_2020_qa_only", "sealed", Path(__file__))
    zip_place = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_place.zip"
    if _sha(zip_place) != catalog["official_place_zip_sha256"]:
        raise RuntimeError("Official place ZIP changed.")
    raw = _tiger_to_existing_fields(_tiger_frame(zip_place, role="place"), role="place")
    place = geography.standardize_place(
        raw[raw["GEOID"].astype(str) == city.census_place_geoid], city
    )
    grid = build_fixed_grid(
        place, target_crs=city.target_grid_crs, resolution_m=30.0,
        anchor_x_m=15.0, anchor_y_m=15.0,
    )
    if grid.sha256 != support["grid_sha256"]:
        raise RuntimeError("Official grid changed.")
    place_mask = rasterize(
        [(place.to_crs(grid.crs).geometry.iloc[0], 1)],
        out_shape=grid.shape, transform=grid.transform,
        fill=0, all_touched=False, dtype="uint8",
    ).astype(bool)
    npz = directory / "fixed_support.npz"
    if _sha(npz) != support["fixed_support_npz_sha256"]:
        raise RuntimeError("Official support cache changed.")
    with np.load(npz) as fixed:
        zones, eligible = fixed["zones"].copy(), fixed["eligible"].copy()
    static_counts = np.asarray(support["fixed_eligible_land_count_by_tract"], dtype=int)
    zone_counts = np.asarray(support["fixed_zone_count_by_tract"], dtype=int)
    session = requests.Session()
    cache_dir = directory / "qa_scene_cache"
    cache_dir.mkdir(exist_ok=True)
    results = []
    candidates = catalog["candidate_overpasses"]
    if len({item["local_date"] for item in candidates}) != len(candidates):
        raise RuntimeError("Official catalog contains duplicate local dates.")
    for index, item in enumerate(candidates, 1):
        date, scene_ids = item["local_date"], item["scene_ids"]
        result_path = directory / "dates" / f"{date}.json"
        prior_error = None
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result["scene_ids"] != scene_ids or result["grid_sha256"] != grid.sha256:
                raise RuntimeError(f"Existing official QA result changed: {date}")
            if retry_technical and result["status"] == "technical_unassessable" and not result.get(
                "bounded_retry_performed", False
            ):
                prior_error = {
                    "error_type": result["error_type"],
                    "error_note": result["error_note"],
                }
            else:
                results.append(result)
                print(
                    f"official QA {index}/{len(candidates)} {date} {result['status']} cached",
                    flush=True,
                )
                continue
        try:
            layers = {key: np.zeros(grid.shape, dtype=bool) for key in (
                "observed_footprint", "clear_qa_pixel", "terrain_visible",
                "qa_cdist_nonfill", "cloud_distance_ge_1km", "st_qa_le_4k",
            )}
            for scene_id in scene_ids:
                cache_path = cache_dir / f"{scene_id}.npz"
                if cache_path.exists():
                    with np.load(cache_path) as cached:
                        if (str(cached["scene_id"]) != scene_id or
                                str(cached["grid_sha256"]) != grid.sha256):
                            raise RuntimeError("Cached QA scene identity or grid changed.")
                        arrays = {key: cached[key].copy() for key in ASSETS}
                        coverage = cached["coverage"].copy()
                else:
                    arrays, coverage = _read_scene_qa(session, scene_id, grid)
                    np.savez_compressed(
                        cache_path, scene_id=scene_id, grid_sha256=grid.sha256,
                        coverage=coverage, **arrays,
                    )
                if any(array.shape != grid.shape for array in (*arrays.values(), coverage)):
                    raise RuntimeError("QA cache has a different grid shape.")
                for key, mask in qa_stages(arrays, coverage).items():
                    layers[key] |= mask
            result = summarize_date(
                city_id=city.id, local_date=date, scene_ids=scene_ids,
                stage_masks=layers, zones=zones, eligible=eligible,
                place_mask=place_mask, static_counts=static_counts,
                zone_counts=zone_counts, grid_sha256=grid.sha256,
            )
            result["status"] = (
                "qa_support_passed_official" if result["status"] == "provisional_qa_support"
                else "qa_insufficient_official"
            )
            result["support_source"] = "original_2020_colorado_tiger_rebuilt"
            result["mirror_equivalence_gate_passed"] = False
            result["official_catalog_sha256"] = _sha(catalog_path)
            result["official_support_sha256"] = _sha(support_path)
            result["qa_scene_cache"] = {
                scene_id: _sha(cache_dir / f"{scene_id}.npz") for scene_id in scene_ids
            }
            for row, geoid in zip(result["per_tract"], support["tract_geoids"], strict=True):
                row["tract_geoid"] = geoid
            result.pop("disclaimer", None)
            result.pop("official_geometry_equivalence_verified", None)
        except (requests.RequestException, rasterio.errors.RasterioError, ValueError,
                RuntimeError, OSError) as exc:
            result = {
                "city_id": city.id, "local_date": date, "scene_ids": scene_ids,
                "grid_sha256": grid.sha256, "status": "technical_unassessable",
                "error_type": type(exc).__name__, "error_note": str(exc)[:300],
                "thermal_value_or_st_dn_read": False,
            }
        if prior_error is not None:
            result["bounded_retry_performed"] = True
            result["prior_technical_error"] = prior_error
        _write_json(result_path, result)
        results.append(result)
        print(f"official QA {index}/{len(candidates)} {date} {result['status']}", flush=True)
    counts = {
        status: sum(item["status"] == status for item in results)
        for status in (
            "qa_support_passed_official", "qa_insufficient_official", "technical_unassessable"
        )
    }
    summary = {
        "state": "official_all_candidate_qa_finished",
        "official_catalog_sha256": _sha(catalog_path),
        "official_support_sha256": _sha(support_path),
        "candidate_count": len(candidates), "counts": counts,
        "date_results": [
            {"local_date": item["local_date"], "status": item["status"]} for item in results
        ],
        "thermal_values_read": False,
    }
    _write_json(directory / "qa_summary.json", summary)


if __name__ == "__main__":
    main()
