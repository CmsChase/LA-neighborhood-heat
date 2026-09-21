"""Six-scene Colorado exploratory ST availability; local-only Charlotte geometry diagnosis.

The official-boundary gate remains open. Output labels are development material,
not accepted source training data or an independent confirmation cohort.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import rasterio
import requests
import shapely
from rasterio.features import rasterize

from experiments.source_city_qa_mirror_pilot import (
    OUTPUT as QA_OUTPUT,
)
from experiments.source_city_qa_mirror_pilot import (
    _grid_and_zones,
    _read_scene_qa,
    _stac_item,
    _write_json,
    qa_stages,
)
from la_heat.aligned_landsat import COVERAGE_KEY, _read_asset_to_grid, decode_aligned_scene_arrays
from la_heat.config import ResearchConfig, load_config
from la_heat.landsat import physically_plausible_lst_mask
from la_heat.mosaic import mosaic_aligned_scenes
from la_heat.multicity import geography
from la_heat.multicity.config import CitySpec
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "manifests/multicity/ACTIVE_STAGE.json"
METADATA = ROOT / "exports/SOURCE_CITY_METADATA_SCREEN/summary.json"
QA_SUMMARY = QA_OUTPUT / "summary.json"
QA_CITY = QA_OUTPUT / "colorado_springs_co"
OUTPUT = ROOT / "exports/SOURCE_CITY_TARGET_AVAILABILITY/exploratory_mirror"
PROTOCOL = ROOT / "configs/multicity/m3_development_protocol_v1.toml"
CONFIG = ROOT / "configs/research.toml"
CITY = CitySpec(
    "colorado_springs_co",
    "Colorado Springs",
    "08",
    "0816000",
    "America/Denver",
    "EPSG:32613",
    "exploratory_target_availability_only",
    "sealed",
    Path(__file__),
)
CHARLOTTE = CitySpec(
    "charlotte_nc",
    "Charlotte",
    "37",
    "3712000",
    "America/New_York",
    "EPSG:32617",
    "local_geometry_diagnosis_only",
    "sealed",
    Path(__file__),
)
DATES = {
    "2020-10-31": "LC08_L2SP_033033_20201031_02_T1",
    "2021-05-27": "LC08_L2SP_033033_20210527_02_T1",
    "2021-10-18": "LC08_L2SP_033033_20211018_02_T1",
    "2022-10-29": "LC09_L2SP_033033_20221029_02_T1",
    "2023-10-24": "LC08_L2SP_033033_20231024_02_T1",
    "2024-05-03": "LC08_L2SP_033033_20240503_02_T1",
}
QA_SHA = "7c1919f3d190f1f171456c6bb670da2b8a76cf6abe4753a2e162e1ea5280b3ce"
SUPPORT_SHA = "06b64ebd0f7088dac8223687349f0f5056b97f2b9a193995522a90e37b6aafcc"
GRID_SHA = "c08ee85e1873c23b1289ea18475c5eeac92621aa1da1ca766fcc35b4391bdae7"
PROTOCOL_SHA = "65087095142e86d4b5ee4f190ac14951f18679506738253571df160200cbb821"
CONFIG_SHA = "a2d4f7300d8a264c77c3ddc15a730546945a2d7f6ed253ff2f49e352102c60b9"
MARKER = "探索性开发目标，基于镜像边界，官方几何核验待完成"


class ExploratoryTargetError(RuntimeError):
    """A frozen-input, product, or target-support mismatch."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preflight() -> tuple[dict, dict]:
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    permissions = stage["permissions"]
    if stage["state"] != "running_colorado_six_scene_exploratory_target_availability_only":
        raise ExploratoryTargetError("The exact exploratory target stage is not active.")
    if not (
        permissions["read_new_candidate_targets"]
        and permissions["read_new_candidate_landsat_asset_hrefs"]
        and permissions["read_new_candidate_public_metadata"]
    ) or any(
        permissions[key]
        for key in ("fit_model", "score_model", "read_external_targets", "read_source_targets")
    ):
        raise ExploratoryTargetError("Target read permissions are not exactly scoped.")
    if stage["source_city_exploratory_target_availability"]["allowed_qa_supported_dates"] != list(
        DATES
    ):
        raise ExploratoryTargetError("Frozen six-date authorization changed.")
    for path, expected in (
        (QA_SUMMARY, QA_SHA),
        (QA_CITY / "fixed_support.npz", SUPPORT_SHA),
        (PROTOCOL, PROTOCOL_SHA),
        (CONFIG, CONFIG_SHA),
    ):
        if _sha(path) != expected:
            raise ExploratoryTargetError(f"Frozen input changed: {path.name}.")
    summary = json.loads(QA_SUMMARY.read_text(encoding="utf-8"))
    prior = summary["cities"][CITY.id]
    if prior["date_result_counts"]["provisional_qa_support"] != 6:
        raise ExploratoryTargetError("QA support selection changed.")
    for date, scene_id in DATES.items():
        row = json.loads((QA_CITY / "dates" / f"{date}.json").read_text(encoding="utf-8"))
        if (
            row["status"] != "provisional_qa_support"
            or row["scene_ids"] != [scene_id]
            or row["grid_sha256"] != GRID_SHA
        ):
            raise ExploratoryTargetError(f"Frozen QA date changed: {date}.")
    return stage, json.loads(METADATA.read_text(encoding="utf-8"))


def _cached_colorado_geometry(metadata: dict):
    directory = QA_CITY
    raw_place = gpd.read_file(directory / "mirror_place/features.geojson")
    raw_tracts = gpd.read_file(directory / "mirror_tract/features_0000.geojson")
    if not raw_place.geometry.is_valid.all() or not raw_tracts.geometry.is_valid.all():
        raise ExploratoryTargetError("Cached Colorado raw geometry is no longer valid.")
    place = geography.standardize_place(raw_place, CITY)
    tracts = geography.standardize_tracts(raw_tracts, CITY)
    _, primary = geography.select_city_tracts(
        place,
        tracts,
        city_id=CITY.id,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )
    primary = primary.sort_values("tract_geoid").reset_index(drop=True)
    info = metadata["cities"][CITY.id]
    place_hash = canonical_sha256(
        shapely.to_wkb(shapely.normalize(place.geometry.iloc[0]), hex=True)
    )
    if (
        place_hash != info["place_geometry_sha256"]
        or primary["tract_geoid"].tolist() != info["selected_tract_geoids"]
    ):
        raise ExploratoryTargetError("Cached Colorado mirror identity changed.")
    grid, zones, place_mask = _grid_and_zones(place, primary, CITY.target_grid_crs)
    if grid.sha256 != GRID_SHA:
        raise ExploratoryTargetError("Frozen target grid changed.")
    with np.load(QA_CITY / "fixed_support.npz") as support:
        if not np.array_equal(support["zones"], zones):
            raise ExploratoryTargetError("Frozen tract zone raster changed.")
        eligible = support["eligible"].copy()
    support_record = json.loads((QA_CITY / "support.json").read_text(encoding="utf-8"))
    if (
        support_record["grid_sha256"] != GRID_SHA
        or support_record["tract_geoids"] != primary["tract_geoid"].tolist()
    ):
        raise ExploratoryTargetError("Fixed-support identity changed.")
    return grid, zones, eligible, place_mask, primary, support_record


def _target_config() -> ResearchConfig:
    base = load_config(CONFIG)
    raw = copy.deepcopy(base.raw)
    raw["landsat"]["apply_st_uncertainty_threshold"] = True
    raw["landsat"]["maximum_st_uncertainty_kelvin"] = 4.0
    return ResearchConfig(raw=raw, path=CONFIG)


def _check_thermal_metadata(item: dict, allowed_scene_ids=None) -> dict:
    if allowed_scene_ids is None:
        allowed_scene_ids = DATES.values()
    if item.get("id") not in allowed_scene_ids:
        raise ExploratoryTargetError("Thermal item is outside the frozen six scenes.")
    if item.get("properties", {}).get("landsat:correction") != "L2SP":
        raise ExploratoryTargetError("Thermal item is not L2SP.")
    asset = item.get("assets", {}).get("lwir11")
    if not asset:
        raise ExploratoryTargetError("Frozen scene lacks the ST_B10/lwir11 asset.")
    bands = asset.get("raster:bands", [])
    if len(bands) != 1:
        raise ExploratoryTargetError("Thermal scale metadata is not single-band.")
    band = bands[0]
    if not (
        band.get("unit") == "kelvin"
        and band.get("scale") == 0.00341802
        and band.get("offset") == 149.0
        and band.get("nodata") == 0
        and band.get("data_type") == "uint16"
        and band.get("spatial_resolution") == 30
    ):
        raise ExploratoryTargetError(
            "Thermal scale, unit, nodata, or resolution differs from contract."
        )
    return {
        "asset_key": "lwir11",
        "band_name": "ST_B10",
        "unit": "kelvin",
        "scale": band["scale"],
        "offset": band["offset"],
        "nodata": band["nodata"],
        "data_type": band["data_type"],
        "spatial_resolution_m": band["spatial_resolution"],
    }


def summarize_target_support(
    *,
    zones: np.ndarray,
    eligible: np.ndarray,
    qa_mask: np.ndarray,
    valid: np.ndarray,
    st_c: np.ndarray,
    static_counts: np.ndarray,
    footprint_fraction: np.ndarray,
) -> tuple[list[dict], dict]:
    """Use the unchanged static denominator for each exploratory tract label."""
    count = len(static_counts)
    qa_count = np.bincount(zones[qa_mask & eligible], minlength=count + 1)[1:]
    valid = valid & eligible
    target_count = np.bincount(zones[valid], minlength=count + 1)[1:]
    if np.any(target_count > qa_count):
        raise ExploratoryTargetError("Thermal validity added pixels outside QA support.")
    medians = (
        pd.DataFrame({"zone": zones[valid], "lst_c": st_c[valid]}).groupby("zone")["lst_c"].median()
    )
    rows = []
    for index in range(count):
        retained = bool(
            footprint_fraction[index] >= 0.90
            and static_counts[index] > 0
            and target_count[index] >= 20
            and target_count[index] / static_counts[index] >= 0.60
        )
        median = float(medians.loc[index + 1]) if retained else None
        if retained and not np.isfinite(median):
            raise ExploratoryTargetError("Retained tract target median is not finite.")
        rows.append(
            {
                "tract_index": index + 1,
                "eligible_land_pixels_static": int(static_counts[index]),
                "qa4k_pixels": int(qa_count[index]),
                "thermal_valid_pixels": int(target_count[index]),
                "thermal_valid_fraction_fixed_denominator": float(
                    target_count[index] / static_counts[index]
                )
                if static_counts[index]
                else None,
                "target_available_exploratory": retained,
                "target_lst_c_development_only": median,
            }
        )
    return rows, {
        "qa4k_eligible_pixels": int(qa_count.sum()),
        "thermal_valid_eligible_pixels": int(target_count.sum()),
        "thermal_invalid_among_qa_pixels": int(qa_count.sum() - target_count.sum()),
        "thermal_invalid_fraction_among_qa_pixels": float(1 - target_count.sum() / qa_count.sum())
        if qa_count.sum()
        else None,
        "exploratory_label_count": sum(row["target_available_exploratory"] for row in rows),
    }


def _colorado_date(
    date: str,
    scene_id: str,
    session: requests.Session,
    context: tuple,
    config: ResearchConfig,
) -> dict:
    grid, zones, eligible, place_mask, primary, support = context
    old = json.loads((QA_CITY / "dates" / f"{date}.json").read_text(encoding="utf-8"))
    arrays, coverage = _read_scene_qa(session, scene_id, grid)
    qa = qa_stages(arrays, coverage)["st_qa_le_4k"]
    qa_counts = np.bincount(zones[qa & eligible], minlength=len(primary) + 1)[1:]
    old_qa_counts = np.array([row["qa4k_pixels"] for row in old["per_tract"]])
    if not np.array_equal(qa_counts, old_qa_counts):
        raise ExploratoryTargetError("Reread QA pixels differ from frozen pilot counts.")
    footprint = qa_stages(arrays, coverage)["observed_footprint"]
    footprint_counts = np.bincount(zones[footprint & (zones > 0)], minlength=len(primary) + 1)[1:]
    if not np.array_equal(
        footprint_counts, [row["observed_footprint_pixels"] for row in old["per_tract"]]
    ):
        raise ExploratoryTargetError("Reread footprint differs from frozen pilot.")
    item = _stac_item(session, "landsat-c2-l2", scene_id)
    scale = _check_thermal_metadata(item)
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MAX_RETRY": "1",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    with rasterio.Env(**env):
        dn, thermal_coverage = _read_asset_to_grid(
            pc.sign(item["assets"]["lwir11"]["href"]), grid=grid, fallback_nodata=0
        )
    if not np.array_equal(coverage, thermal_coverage):
        raise ExploratoryTargetError("ST_B10 and frozen QA do not share raster support.")
    scene = decode_aligned_scene_arrays(
        scene_id=scene_id,
        arrays={**arrays, "lwir11": dn, COVERAGE_KEY: coverage},
        config=config,
    )
    if np.any(scene.valid & ~qa):
        raise ExploratoryTargetError("Decoded thermal mask exceeds frozen QA4K support.")
    static_counts = np.asarray(support["fixed_eligible_land_count_by_tract"], dtype=int)
    footprint_fraction = np.asarray([row["footprint_fraction"] for row in old["per_tract"]])
    rows, counts = summarize_target_support(
        zones=zones,
        eligible=eligible,
        qa_mask=qa,
        valid=scene.valid,
        st_c=scene.lst_c,
        static_counts=static_counts,
        footprint_fraction=footprint_fraction,
    )
    for row, geoid in zip(rows, support["tract_geoids"], strict=True):
        row["tract_geoid"] = geoid
    valid_dn = (dn >= 293) & (dn <= 61440)
    physically_plausible = physically_plausible_lst_mask(scene.lst_c)
    qa_eligible = qa & eligible
    counts["thermal_dn_outside_contract_among_qa"] = int((qa_eligible & ~valid_dn).sum())
    counts["thermal_physical_invalid_after_dn_among_qa"] = int(
        (qa_eligible & valid_dn & ~physically_plausible).sum()
    )
    counts["city_union_coverage_fraction"] = float(
        (footprint & place_mask).sum() / place_mask.sum()
    )
    counts["qa4k_prior_label_upper_bound"] = old["final_qa4k_tract_count"]
    counts["date_usable_exploratory"] = bool(
        counts["city_union_coverage_fraction"] >= 0.98
        and counts["exploratory_label_count"] / len(primary) >= 0.50
    )
    directory = OUTPUT / CITY.id / "dates"
    directory.mkdir(parents=True, exist_ok=True)
    labels_path = directory / f"{date}_development_labels.csv"
    pd.DataFrame(rows).to_csv(labels_path, index=False)
    result = {
        "city_id": CITY.id,
        "local_date": date,
        "scene_ids": [scene_id],
        "status": "exploratory_target_usable"
        if counts["date_usable_exploratory"]
        else "exploratory_target_support_insufficient",
        "marker": MARKER,
        "official_geometry_equivalence_verified": False,
        "formally_accepted_training_data": False,
        "independent_confirmation_data": False,
        "qa_date_sha256": _sha(QA_CITY / "dates" / f"{date}.json"),
        "grid_sha256": GRID_SHA,
        "fixed_support_sha256": SUPPORT_SHA,
        "st_asset_scale": scale,
        "counts": counts,
        "labels_file": str(labels_path.relative_to(ROOT)).replace("\\", "/"),
        "labels_file_sha256": _sha(labels_path),
    }
    _write_json(directory / f"{date}.json", result)
    return result


def _charlotte_geometry_diagnosis(metadata: dict) -> dict:
    source = QA_OUTPUT / "charlotte_nc/mirror_place/features.geojson"
    raw = gpd.read_file(source)
    if len(raw) != 1 or raw.crs is None:
        raise ExploratoryTargetError("Cached Charlotte place identity is incomplete.")
    geometry = raw.geometry.iloc[0]
    make_valid = shapely.make_valid(geometry)
    buffer_zero = geometry.buffer(0)
    if not make_valid.is_valid or not buffer_zero.is_valid:
        raise ExploratoryTargetError("Standard repair copy remains invalid.")
    a = gpd.GeoSeries([make_valid], crs=raw.crs).to_crs(CHARLOTTE.target_grid_crs).iloc[0]
    b = gpd.GeoSeries([buffer_zero], crs=raw.crs).to_crs(CHARLOTTE.target_grid_crs).iloc[0]
    place = geography.standardize_place(raw, CHARLOTTE)
    grid, _, place_mask = _grid_and_zones_for_place_only(place, CHARLOTTE.target_grid_crs)
    alternate_mask = rasterize(
        [(b, 1)],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    repaired_hash = canonical_sha256(
        shapely.to_wkb(shapely.normalize(place.geometry.iloc[0]), hex=True)
    )
    result = {
        "city_id": CHARLOTTE.id,
        "scope": "existing_local_mirror_geometry_only",
        "raw_file_sha256": _sha(source),
        "source_crs": str(raw.crs),
        "raw_valid": bool(geometry.is_valid),
        "invalid_reason": shapely.is_valid_reason(geometry),
        "standard_make_valid_area_m2": float(a.area),
        "alternate_buffer_zero_area_m2": float(b.area),
        "repair_symdiff_area_m2": float(a.symmetric_difference(b).area),
        "repair_symdiff_fraction_of_make_valid_area": float(
            a.symmetric_difference(b).area / a.area
        ),
        "place_grid_sha256": grid.sha256,
        "repair_choice_changed_30m_place_cells": int(np.count_nonzero(place_mask ^ alternate_mask)),
        "frozen_metadata_place_hash_matches_make_valid": repaired_hash
        == metadata["cities"][CHARLOTTE.id]["place_geometry_sha256"],
        "frozen_selected_tract_count": len(
            metadata["cities"][CHARLOTTE.id]["selected_tract_geoids"]
        ),
        "tract_membership_change_assessable_locally": False,
        "tract_membership_note": (
            "The prior screen retained selected GEOIDs but no Charlotte tract geometry; "
            "local-only diagnosis cannot recompute membership or fixed land support."
        ),
        "official_geometry_equivalence_verified": False,
        "worldcover_qa_or_thermal_read": False,
    }
    _write_json(OUTPUT / CHARLOTTE.id / "geometry_diagnosis.json", result)
    return result


def _grid_and_zones_for_place_only(place: gpd.GeoDataFrame, crs: str):
    from la_heat.grid import build_fixed_grid

    grid = build_fixed_grid(
        place,
        target_crs=crs,
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    mask = rasterize(
        [(place.to_crs(crs).geometry.iloc[0], 1)],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    return grid, None, mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-colorado", action="store_true")
    parser.add_argument("--official-colorado-retry-technical", action="store_true")
    args = parser.parse_args()
    if args.official_colorado:
        _run_official_colorado_targets(retry_technical=False)
        return
    if args.official_colorado_retry_technical:
        _run_official_colorado_targets(retry_technical=True)
        return
    _, metadata = _preflight()
    context = _cached_colorado_geometry(metadata)
    config = _target_config()
    session = requests.Session()
    results = []
    for date, scene_id in DATES.items():
        path = OUTPUT / CITY.id / "dates" / f"{date}.json"
        if path.exists():
            result = json.loads(path.read_text(encoding="utf-8"))
            if (
                result.get("scene_ids") != [scene_id]
                or result.get("fixed_support_sha256") != SUPPORT_SHA
            ):
                raise ExploratoryTargetError("Existing exploratory date result changed.")
        else:
            try:
                result = _colorado_date(date, scene_id, session, context, config)
            except (
                requests.RequestException,
                rasterio.errors.RasterioError,
                OSError,
                ValueError,
                ExploratoryTargetError,
            ) as exc:
                result = {
                    "city_id": CITY.id,
                    "local_date": date,
                    "scene_ids": [scene_id],
                    "status": "technical_target_unassessable",
                    "error_type": type(exc).__name__,
                    "error_note": str(exc)
                    if type(exc) is ExploratoryTargetError
                    else "Remote raster/metadata failure; no scientific support failure inferred.",
                    "marker": MARKER,
                    "official_geometry_equivalence_verified": False,
                    "fixed_support_sha256": SUPPORT_SHA,
                }
                _write_json(path, result)
        results.append(result)
        print(CITY.id, date, result["status"], flush=True)
    charlotte = _charlotte_geometry_diagnosis(metadata)
    summary = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "marker": MARKER,
        "contract_amendment": "docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md#11",
        "qa_summary_sha256": QA_SHA,
        "fixed_support_sha256": SUPPORT_SHA,
        "protocol_sha256": PROTOCOL_SHA,
        "research_config_sha256": CONFIG_SHA,
        "official_geometry_equivalence_verified": False,
        "formally_accepted_training_data": False,
        "independent_confirmation_data": False,
        "model_fit_prediction_or_scoring_performed": False,
        "colorado_springs_co": {
            "selected_scenes": DATES,
            "exploratory_target_usable_count": sum(
                row["status"] == "exploratory_target_usable" for row in results
            ),
            "exploratory_target_insufficient_count": sum(
                row["status"] == "exploratory_target_support_insufficient" for row in results
            ),
            "technical_unassessable_count": sum(
                row["status"] == "technical_target_unassessable" for row in results
            ),
            "dates": [
                {
                    "local_date": row["local_date"],
                    "status": row["status"],
                    "counts": row.get("counts"),
                }
                for row in results
            ],
        },
        "charlotte_nc": charlotte,
    }
    _write_json(OUTPUT / "summary.json", summary)
    print("Summary:", OUTPUT / "summary.json", flush=True)


def _run_official_colorado_targets(*, retry_technical: bool) -> None:
    """Read thermal only for all officially supported QA dates, resumably."""
    from experiments.source_city_qa_boundary_audit import _tiger_frame, _tiger_to_existing_fields
    from la_heat.grid import build_fixed_grid

    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    permission = stage["permissions"]
    if stage["state"] != "running_colorado_official_support_catalog_qa_target_only":
        raise ExploratoryTargetError("Official Colorado target stage is not active.")
    if not all(permission[key] for key in (
        "read_new_candidate_public_metadata", "read_new_candidate_landsat_asset_hrefs",
        "read_new_candidate_targets",
    )) or any(permission[key] for key in (
        "fit_model", "score_model", "read_external_targets", "read_source_targets"
    )):
        raise ExploratoryTargetError("Official target permissions are not exactly scoped.")
    qa_dir = ROOT / "exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co"
    catalog_path, support_path, qa_path = (
        qa_dir / "official_catalog.json", qa_dir / "support.json", qa_dir / "qa_summary.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    support = json.loads(support_path.read_text(encoding="utf-8"))
    qa_summary = json.loads(qa_path.read_text(encoding="utf-8"))
    if qa_summary["candidate_count"] != len(catalog["candidate_overpasses"]):
        raise ExploratoryTargetError("Official QA did not cover the full frozen catalog.")
    if qa_summary["official_catalog_sha256"] != _sha(catalog_path):
        raise ExploratoryTargetError("Official catalog changed after QA.")
    if qa_summary["official_support_sha256"] != _sha(support_path):
        raise ExploratoryTargetError("Official support changed after QA.")
    place_zip = ROOT / "data/raw/source_city_official_boundaries/2020/colorado/tl_2020_08_place.zip"
    if _sha(place_zip) != catalog["official_place_zip_sha256"]:
        raise ExploratoryTargetError("Official place ZIP changed.")
    raw = _tiger_to_existing_fields(_tiger_frame(place_zip, role="place"), role="place")
    place = geography.standardize_place(
        raw[raw["GEOID"].astype(str) == CITY.census_place_geoid], CITY
    )
    grid = build_fixed_grid(
        place, target_crs=CITY.target_grid_crs, resolution_m=30.0,
        anchor_x_m=15.0, anchor_y_m=15.0,
    )
    if grid.sha256 != support["grid_sha256"]:
        raise ExploratoryTargetError("Official grid changed.")
    fixed_path = qa_dir / "fixed_support.npz"
    if _sha(fixed_path) != support["fixed_support_npz_sha256"]:
        raise ExploratoryTargetError("Official fixed land support changed.")
    with np.load(fixed_path) as fixed:
        zones, eligible = fixed["zones"].copy(), fixed["eligible"].copy()
    config = _target_config()
    if _sha(PROTOCOL) != PROTOCOL_SHA or _sha(CONFIG) != CONFIG_SHA:
        raise ExploratoryTargetError("Locked target protocol or research config changed.")
    session = requests.Session()
    target_dir = ROOT / "exports/SOURCE_CITY_TARGET_AVAILABILITY/official_2020/colorado_springs_co"
    target_dir.mkdir(parents=True, exist_ok=True)
    allowed = {scene for item in catalog["candidate_overpasses"] for scene in item["scene_ids"]}
    results = []
    for index, item in enumerate(catalog["candidate_overpasses"], 1):
        date, scene_ids = item["local_date"], item["scene_ids"]
        qa_result = json.loads((qa_dir / "dates" / f"{date}.json").read_text(encoding="utf-8"))
        if qa_result["scene_ids"] != scene_ids:
            raise ExploratoryTargetError(f"Frozen QA scene list changed: {date}")
        if qa_result["status"] != "qa_support_passed_official":
            continue
        result_path = target_dir / "dates" / f"{date}.json"
        prior_error = None
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if (result["scene_ids"] != scene_ids or
                    result["qa_date_sha256"] != _sha(qa_dir / "dates" / f"{date}.json")):
                raise ExploratoryTargetError(f"Existing official target result changed: {date}")
            if (retry_technical and result["status"] == "technical_target_unassessable"
                    and not result.get("bounded_retry_performed", False)):
                prior_error = {
                    "error_type": result["error_type"], "error_note": result["error_note"],
                }
            else:
                results.append(result)
                print(f"official target {index}/{len(catalog['candidate_overpasses'])} "
                      f"{date} {result['status']} cached", flush=True)
                continue
        try:
            aligned = []
            qa_union = np.zeros(grid.shape, dtype=bool)
            for scene_id in scene_ids:
                cache_path = qa_dir / "qa_scene_cache" / f"{scene_id}.npz"
                if _sha(cache_path) != qa_result["qa_scene_cache"][scene_id]:
                    raise ExploratoryTargetError("Official QA scene cache changed.")
                with np.load(cache_path) as cache:
                    arrays = {
                        key: cache[key].copy()
                        for key in ("qa_pixel", "qa_radsat", "qa", "cdist")
                    }
                    coverage = cache["coverage"].copy()
                qa_union |= qa_stages(arrays, coverage)["st_qa_le_4k"]
                scene_item = _stac_item(session, "landsat-c2-l2", scene_id)
                _check_thermal_metadata(scene_item, allowed)
                env = {
                    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
                    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MAX_RETRY": "1",
                    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
                }
                with rasterio.Env(**env):
                    dn, thermal_coverage = _read_asset_to_grid(
                        pc.sign(scene_item["assets"]["lwir11"]["href"]),
                        grid=grid, fallback_nodata=0,
                    )
                if not np.array_equal(coverage, thermal_coverage):
                    raise ExploratoryTargetError("Official QA and thermal footprints differ.")
                scene = decode_aligned_scene_arrays(
                    scene_id=scene_id,
                    arrays={**arrays, "lwir11": dn, COVERAGE_KEY: coverage},
                    config=config,
                )
                if np.any(scene.valid & ~qa_union):
                    raise ExploratoryTargetError("Thermal validity exceeds QA support.")
                aligned.append(scene)
            mosaic = mosaic_aligned_scenes(
                scene_ids=scene_ids,
                st_values=np.stack([scene.lst_c for scene in aligned]),
                qa_valid=np.stack([scene.valid for scene in aligned]),
                st_qa=np.stack([scene.st_uncertainty_k for scene in aligned]),
                cdist=np.stack([scene.cloud_distance_km for scene in aligned]),
                footprint=np.stack([scene.footprint for scene in aligned]),
            )
            if np.any(mosaic.selected_valid & ~qa_union):
                raise ExploratoryTargetError("Mosaic thermal validity exceeds QA support.")
            static_counts = np.asarray(support["fixed_eligible_land_count_by_tract"], dtype=int)
            footprint_fraction = np.asarray(
                [row["footprint_fraction"] for row in qa_result["per_tract"]], dtype=float
            )
            rows, counts = summarize_target_support(
                zones=zones, eligible=eligible, qa_mask=qa_union,
                valid=mosaic.selected_valid, st_c=mosaic.selected_st_value,
                static_counts=static_counts, footprint_fraction=footprint_fraction,
            )
            for row, geoid in zip(rows, support["tract_geoids"], strict=True):
                row["tract_geoid"] = geoid
                row["target_available_source_development"] = row.pop(
                    "target_available_exploratory"
                )
                row["target_lst_c_source_development"] = row.pop(
                    "target_lst_c_development_only"
                )
            place_coverage = qa_result["city_union_observed_coverage_fraction"]
            counts["target_label_count"] = counts.pop("exploratory_label_count")
            counts["date_usable"] = (
                place_coverage >= 0.98 and counts["target_label_count"] / len(rows) >= 0.50
            )
            labels_path = target_dir / "dates" / f"{date}_source_development_labels.csv"
            labels_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(labels_path, index=False)
            result = {
                "city_id": CITY.id, "local_date": date, "scene_ids": scene_ids,
                "status": "target_usable_official" if counts["date_usable"]
                          else "target_support_insufficient_official",
                "source_role": "development_only_not_independent_confirmation",
                "official_geometry_source": "original_2020_tiger_rebuilt_not_mirror_equivalent",
                "qa_date_sha256": _sha(qa_dir / "dates" / f"{date}.json"),
                "fixed_support_sha256": _sha(support_path),
                "grid_sha256": grid.sha256,
                "counts": counts,
                "labels_file": str(labels_path.relative_to(ROOT)).replace("\\", "/"),
                "labels_file_sha256": _sha(labels_path),
            }
        except (requests.RequestException, rasterio.errors.RasterioError, OSError,
                ValueError, ExploratoryTargetError, RuntimeError) as exc:
            result = {
                "city_id": CITY.id, "local_date": date, "scene_ids": scene_ids,
                "status": "technical_target_unassessable",
                "error_type": type(exc).__name__, "error_note": str(exc)[:300],
                "qa_date_sha256": _sha(qa_dir / "dates" / f"{date}.json"),
                "fixed_support_sha256": _sha(support_path),
            }
        if prior_error is not None:
            result["bounded_retry_performed"] = True
            result["prior_technical_error"] = prior_error
        _write_json(result_path, result)
        results.append(result)
        print(f"official target {index}/{len(catalog['candidate_overpasses'])} "
              f"{date} {result['status']}", flush=True)
    _write_json(target_dir / "summary.json", {
        "state": "official_qa_passing_thermal_screen_finished",
        "candidate_count": len(catalog["candidate_overpasses"]),
        "qa_pass_count": qa_summary["counts"]["qa_support_passed_official"],
        "qa_summary_sha256": _sha(qa_path),
        "official_support_sha256": _sha(support_path),
        "target_usable_count": sum(row["status"] == "target_usable_official" for row in results),
        "target_insufficient_count": sum(
            row["status"] == "target_support_insufficient_official" for row in results
        ),
        "technical_unassessable_count": sum(
            row["status"] == "technical_target_unassessable" for row in results
        ),
        "date_results": [
            {"local_date": row["local_date"], "status": row["status"],
             "label_count": row.get("counts", {}).get("target_label_count", 0)}
            for row in results
        ],
        "model_fit_or_scoring_performed": False,
    })


if __name__ == "__main__":
    main()
