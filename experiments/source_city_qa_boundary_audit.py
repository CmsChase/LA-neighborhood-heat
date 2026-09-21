"""Audit official 2020 Census geometry before the fixed two-city QA-only pilot.

No raster asset or thermal target is requested by this script. An unavailable
official source is a failed prerequisite, never an implicit mirror approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from rasterio.features import rasterize

from la_heat.grid import build_fixed_grid
from la_heat.multicity import geography
from la_heat.multicity.config import CitySpec
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "exports" / "SOURCE_CITY_METADATA_SCREEN" / "summary.json"
OUTPUT = ROOT / "exports" / "SOURCE_CITY_QA_PILOT" / "boundary_audit.json"
OFFICIAL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer"
)
SPECS = (
    ("colorado_springs_co", "Colorado Springs", "08", "0816000", "America/Denver", "EPSG:32613"),
    ("charlotte_nc", "Charlotte", "37", "3712000", "America/New_York", "EPSG:32617"),
)
FROZEN_MIRROR = ROOT / "exports/SOURCE_CITY_QA_PILOT/mirror_exploratory/colorado_springs_co"


def fetch_frames(city: CitySpec, place_url: str, tract_url: str, *, official: bool):
    session = geography._retrying_session()
    label = "official TIGERweb" if official else "2020 Census pilot mirror"
    place_candidate = geography.LayerCandidate(label + " place", place_url, label, label)
    tract_candidate = geography.LayerCandidate(label + " tract", tract_url, label, label)
    place_raw = geography._download_place(session, place_candidate, city)
    place = geography.standardize_place(place_raw.frame, city)
    tracts_raw = geography._download_tracts(
        session, tract_candidate, city, tuple(float(x) for x in place.total_bounds)
    )
    tracts = geography.standardize_tracts(tracts_raw.frame, city)
    candidates, primary = geography.select_city_tracts(
        place,
        tracts,
        city_id=city.id,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )
    return (
        place,
        candidates,
        primary,
        {
            "place_url": place_url,
            "tract_url": tract_url,
            "place_layer_name": place_raw.metadata.get("name"),
            "tract_layer_name": tracts_raw.metadata.get("name"),
            "place_layer_description": str(place_raw.metadata.get("description", ""))[:200],
            "tract_layer_description": str(tracts_raw.metadata.get("description", ""))[:200],
        },
    )


def _zone_map(primary: gpd.GeoDataFrame, grid, geoids):
    projected = primary.to_crs(grid.crs)
    lookup = {geoid: index + 1 for index, geoid in enumerate(geoids)}
    return rasterize(
        [
            (geom, lookup[geoid])
            for geoid, geom in zip(projected["tract_geoid"], projected.geometry, strict=True)
        ],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )


def compare_geometry(official, mirror, *, crs: str):
    """Pixel-scale acceptance: <= half-pixel boundary drift and exact 30m zones."""
    official_place, _, official_primary, _ = official
    mirror_place, _, mirror_primary, _ = mirror
    a = official_place.to_crs(crs).geometry.union_all()
    b = mirror_place.to_crs(crs).geometry.union_all()
    official_ids = set(official_primary["tract_geoid"].astype(str))
    mirror_ids = set(mirror_primary["tract_geoid"].astype(str))
    combined = gpd.GeoDataFrame(
        geometry=[shapely.union_all([a, b])],
        crs=crs,
    )
    grid = build_fixed_grid(
        combined,
        target_crs=crs,
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    geoid_order = tuple(sorted(official_ids | mirror_ids))
    official_zones = _zone_map(official_primary, grid, geoid_order)
    mirror_zones = _zone_map(mirror_primary, grid, geoid_order)
    disagreement = int(np.count_nonzero(official_zones != mirror_zones))
    official_only_cells = int(np.count_nonzero((official_zones != 0) & (mirror_zones == 0)))
    mirror_only_cells = int(np.count_nonzero((official_zones == 0) & (mirror_zones != 0)))
    reassigned_cells = int(np.count_nonzero(
        (official_zones != 0) & (mirror_zones != 0) & (official_zones != mirror_zones)
    ))
    boundary_distance_m = float(a.hausdorff_distance(b))
    return {
        "analysis_crs": crs,
        "place_hausdorff_m": boundary_distance_m,
        "place_symmetric_difference_m2": float(a.symmetric_difference(b).area),
        "official_tract_count": len(official_ids),
        "mirror_tract_count": len(mirror_ids),
        "official_only_geoids": sorted(official_ids - mirror_ids),
        "mirror_only_geoids": sorted(mirror_ids - official_ids),
        "common_30m_grid_sha256": grid.sha256,
        "zone_disagreement_30m_cells": disagreement,
        "official_only_30m_cells": official_only_cells,
        "mirror_only_30m_cells": mirror_only_cells,
        "reassigned_between_tracts_30m_cells": reassigned_cells,
        "official_zone_sha256": hashlib.sha256(official_zones.tobytes()).hexdigest(),
        "mirror_zone_sha256": hashlib.sha256(mirror_zones.tobytes()).hexdigest(),
        "passes_pixel_scale_gate": (
            boundary_distance_m <= 15.0 and official_ids == mirror_ids and disagreement == 0
        ),
    }


def _tiger_frame(path: Path, *, role: str) -> gpd.GeoDataFrame:
    """Read a user-supplied original TIGER ZIP without touching a network endpoint."""
    expected = f"tl_2020_08_{role}.zip"
    if path.name.lower() != expected or not path.is_file():
        raise ValueError(f"Expected the local 2020 Colorado TIGER file {expected}.")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        stem = expected[:-4]
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            if not any(name.lower().endswith(stem + suffix) for name in names):
                raise ValueError(f"{expected} lacks {suffix}.")
        if archive.testzip() is not None:
            raise ValueError(f"{expected} failed ZIP integrity checking.")
    frame = gpd.read_file(path)
    if frame.empty or frame.crs is None or frame.geometry.isna().any():
        raise ValueError(f"{expected} has no usable georeferenced geometry.")
    return frame


def _tiger_to_existing_fields(frame: gpd.GeoDataFrame, *, role: str) -> gpd.GeoDataFrame:
    """Map original TIGER field names to the existing geometry contract."""
    result = frame.rename(columns={
        "STATEFP": "STATE", "PLACEFP": "PLACE", "COUNTYFP": "COUNTY",
        "TRACTCE": "TRACT", "ALAND": "AREALAND",
    }).copy()
    required = {"GEOID", "STATE", "AREALAND", "NAME"}
    required |= {"PLACE", "FUNCSTAT"} if role == "place" else {"COUNTY", "TRACT"}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(f"Original TIGER {role} lacks required fields: {sorted(missing)}")
    result["OBJECTID"] = range(1, len(result) + 1)
    result["BASENAME"] = result["NAME"] if role == "place" else result["TRACT"]
    if role == "tract" and "FUNCSTAT" not in result:
        result["FUNCSTAT"] = "S"
    return result


def offline_colorado(place_zip: Path, tract_zip: Path) -> dict:
    """Compare supplied TIGER geometry with the frozen local mirror, read-only."""
    city_id, name, state, geoid, timezone, crs = SPECS[0]
    city = CitySpec(city_id, name, state, geoid, timezone, crs,
                    "qa_pilot_boundary_only", "sealed",
                    Path("experiments/source_city_qa_boundary_audit.py"))
    info = json.loads(INPUT.read_text(encoding="utf-8"))["cities"][city_id]
    raw_place = _tiger_to_existing_fields(_tiger_frame(place_zip, role="place"), role="place")
    raw_tract = _tiger_to_existing_fields(_tiger_frame(tract_zip, role="tract"), role="tract")
    official_place_rows = raw_place[raw_place["GEOID"].astype(str) == geoid]
    if len(official_place_rows) != 1 or set(raw_tract["STATE"].astype(str)) != {state}:
        raise ValueError("Official ZIP place GEOID or tract state is inconsistent with Colorado.")
    official_place = geography.standardize_place(official_place_rows, city)
    official_tracts = geography.standardize_tracts(raw_tract, city)
    _, official_primary = geography.select_city_tracts(
        official_place, official_tracts, city_id=city_id, analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5, exclude_special_use_tracts=True,
    )
    mirror_place = geography.standardize_place(
        gpd.read_file(FROZEN_MIRROR / "mirror_place/features.geojson"), city,
    )
    mirror_tracts = geography.standardize_tracts(
        gpd.read_file(FROZEN_MIRROR / "mirror_tract/features_0000.geojson"), city,
    )
    _, mirror_primary = geography.select_city_tracts(
        mirror_place, mirror_tracts, city_id=city_id, analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5, exclude_special_use_tracts=True,
    )
    mirror_hash = canonical_sha256(
        shapely.to_wkb(shapely.normalize(mirror_place.geometry.iloc[0]), hex=True)
    )
    mirror_ids_match = sorted(mirror_primary["tract_geoid"]) == info["selected_tract_geoids"]
    if mirror_hash != info["place_geometry_sha256"] or not mirror_ids_match:
        raise ValueError(
            "Local mirror geometry or selected tract IDs drifted from the frozen inventory."
        )
    comparison = compare_geometry(
        (official_place, None, official_primary, {}),
        (mirror_place, None, mirror_primary, {}), crs=crs,
    )
    status = (
        "geometry_pass_pending_official_source_review"
        if comparison["passes_pixel_scale_gate"]
        else "geometry_difference_formal_support_blocked"
    )
    return {
        "status": status,
        "official_zip_sha256": {
            "place": hashlib.sha256(place_zip.read_bytes()).hexdigest(),
            "tract": hashlib.sha256(tract_zip.read_bytes()).hexdigest(),
        },
        "official_source_provenance_must_be_reviewed_separately": True,
        "frozen_metadata_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "comparison": comparison,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-colorado-place-zip", type=Path)
    parser.add_argument("--offline-colorado-tract-zip", type=Path)
    args = parser.parse_args()
    if args.offline_colorado_place_zip or args.offline_colorado_tract_zip:
        if not (args.offline_colorado_place_zip and args.offline_colorado_tract_zip):
            parser.error("Both local Colorado TIGER ZIPs are required.")
        print(json.dumps(offline_colorado(args.offline_colorado_place_zip,
                                          args.offline_colorado_tract_zip),
                         ensure_ascii=False, indent=2))
        return
    metadata = json.loads(INPUT.read_text(encoding="utf-8"))
    output = {
        "checked_utc": datetime.now(UTC).isoformat(),
        "metadata_input_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "geometry_gate_predefined": (
            "Same 2020 Census GEOID and candidate rule; project to city UTM; "
            "place Hausdorff <=15m (half a 30m target pixel), exact selected tract "
            "GEOID set, zero changed 30m center-assigned tract-zone cells on one "
            "common 30m grid anchored at 15m; otherwise block QA access. "
            "The pixel rule is strict even when area or WKB differs."
        ),
        "cities": {},
        "qa_pixels_read": False,
        "thermal_pixels_read": False,
    }
    for city_id, name, state, geoid, timezone, crs in SPECS:
        city = CitySpec(
            city_id,
            name,
            state,
            geoid,
            timezone,
            crs,
            "qa_pilot_boundary_only",
            "sealed",
            Path("experiments/source_city_qa_boundary_audit.py"),
        )
        info = metadata["cities"][city_id]
        result = {
            "place_geoid": geoid,
            "official_place_url": f"{OFFICIAL}/26",
            "official_tract_url": f"{OFFICIAL}/6",
            "mirror_place_url": info["place_source"],
            "mirror_tract_url": info["tract_source"],
            "preselected_overpasses": info["qa_pilot_design_not_authorized"]["date_scene_ids"],
            "requested_product_versions": ["ESA WorldCover 2020 v100", "Landsat C2 L2 T1 L2SP"],
            "qa_asset_keys_if_boundary_passes": ["qa_pixel", "qa_radsat", "qa", "cdist"],
            "status": "boundary_unverified_qa_forbidden",
            "qa_date_status_counts": {"passed": 0, "failed_scientific_qa": 0, "unassessable": 10},
            "science_raster_bytes_downloaded": 0,
        }
        try:
            mirror = fetch_frames(city, info["place_source"], info["tract_source"], official=False)
            mirror_place, _, mirror_primary, mirror_source = mirror
            mirror_hash = canonical_sha256(
                shapely.to_wkb(shapely.normalize(mirror_place.geometry.iloc[0]), hex=True)
            )
            result["mirror"] = {
                "source": mirror_source,
                "place_geometry_sha256": mirror_hash,
                "selected_tract_count": len(mirror_primary),
                "selected_ids_match_frozen_input": (
                    sorted(mirror_primary["tract_geoid"].astype(str))
                    == info["selected_tract_geoids"]
                ),
            }
            if (
                mirror_hash != info["place_geometry_sha256"]
                or not result["mirror"]["selected_ids_match_frozen_input"]
            ):
                result["status"] = "mirror_drift_qa_forbidden"
                output["cities"][city_id] = result
                continue
            official = fetch_frames(city, f"{OFFICIAL}/26", f"{OFFICIAL}/6", official=True)
            result["official"] = official[3]
            result["comparison"] = compare_geometry(official, mirror, crs=crs)
            result["status"] = (
                "boundary_verified_qa_still_requires_active_read_permit"
                if result["comparison"]["passes_pixel_scale_gate"]
                else "geometry_disagreement_qa_forbidden"
            )
        except geography.LayerUnavailableError as exc:
            result["official_access_error"] = {
                "type": type(exc).__name__,
                "detail": str(exc)[:300],
            }
        output["cities"][city_id] = result
        print(city_id, result["status"], flush=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Evidence:", OUTPUT)


if __name__ == "__main__":
    main()
