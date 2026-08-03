"""Four-city Census-2020 geography and compatibility evidence for V12."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from rasterio.features import rasterize

from la_heat.grid import build_fixed_grid
from la_heat.multicity import geography as _geography
from la_heat.multicity import source_footprints as _footprints
from la_heat.multicity.config import CitySpec, load_multicity_plan
from la_heat.multicity.missing_support_calibration_evidence_v1 import (
    CITY_IDS,
    GEOGRAPHY_GLOBAL_PATH,
    EvidenceConfig,
    MissingSupportCalibrationEvidenceV1Error,
    _city_geography_path,
    atomic_bytes_no_clobber,
    checkpoint_record,
    file_record,
    read_json_with_commit,
    write_manifest_no_clobber,
)
from la_heat.multicity.portable_predictor_source_evidence_v1 import _StrictClient
from la_heat.provenance import (
    atomic_parquet,
    canonical_sha256,
    geometry_semantic_sha256,
    parquet_file_record,
)

ALGORITHM_VERSION: Final = "four-city-census-geography-contract-v1"
COMPLETE_STATE: Final = "complete_target_blind_four_city_geography_evidence"


class _GeographyClient(_StrictClient):
    """Apply the V12 response-byte budget on top of the frozen URL client."""

    def __init__(self, session: Any, *, config: EvidenceConfig) -> None:
        super().__init__(
            session,
            allowed=_allowed_urls(config),
            maximum_requests=int(config.raw["geography"]["limits"]["maximum_requests"]),
        )
        limits = config.raw["geography"]["limits"]
        self.maximum_single_response_bytes = int(
            limits["maximum_single_response_bytes"]
        )
        self.maximum_total_response_bytes = int(
            limits["maximum_total_response_bytes"]
        )
        self.response_bytes = 0

    def _request(self, method: str, url: str, **kwargs: object) -> Any:
        response = super()._request(method, url, **kwargs)
        content = bytes(response.content)
        if len(content) > self.maximum_single_response_bytes:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "Census response exceeded the single-response byte limit."
            )
        self.response_bytes += len(content)
        if self.response_bytes > self.maximum_total_response_bytes:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "Census responses exceeded the total byte limit."
            )
        return response


def _city(plan: Any, city_id: str) -> CitySpec:
    city = next((candidate for candidate in plan.cities if candidate.id == city_id), None)
    if city is None:
        raise MissingSupportCalibrationEvidenceV1Error(f"Unknown city: {city_id}")
    if city_id == "los_angeles_ca":
        if (
            city.role != "source_anchor"
            or city.target_values_status != "known_phase1_anchor"
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                "Los Angeles source-city role changed."
            )
    elif city.role != "external_confirmation" or city.target_values_status != "sealed":
        raise MissingSupportCalibrationEvidenceV1Error(
            f"External geography city is not sealed: {city_id}"
        )
    return city


def _relative(config: EvidenceConfig, path: Path) -> str:
    return path.resolve().relative_to(config.project_root).as_posix()


def _parquet_record(
    config: EvidenceConfig, path: Path, frame: gpd.GeoDataFrame
) -> dict[str, Any]:
    record = parquet_file_record(path, frame)
    record["path"] = _relative(config, path)
    record["geometry_semantic_sha256"] = geometry_semantic_sha256(frame)
    record["full_row_semantic_sha256"] = _full_frame_semantic_sha256(frame)
    return record


def _full_frame_semantic_sha256(frame: gpd.GeoDataFrame) -> str:
    if frame.empty or frame.crs is None:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Geography frame is empty or lacks a CRS."
        )
    geometry_name = frame.geometry.name
    rows: list[str] = []
    for _, row in frame.iterrows():
        geometry = shapely.normalize(shapely.make_valid(row[geometry_name]))
        attributes = {
            column: row[column]
            for column in frame.columns
            if column != geometry_name
        }
        rows.append(
            canonical_sha256(
                {
                    "attributes": attributes,
                    "geometry_wkb_hex": shapely.to_wkb(
                        geometry,
                        hex=True,
                        output_dimension=2,
                        byte_order=1,
                        include_srid=False,
                    ),
                }
            )
        )
    return canonical_sha256(
        {
            "crs": frame.crs.to_string(),
            "columns": list(frame.columns),
            "row_semantic_sha256": sorted(rows),
        }
    )


def _write_geoparquet_no_clobber(path: Path, frame: gpd.GeoDataFrame) -> None:
    if path.exists():
        if not path.is_file():
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Geography output is not a file: {path}"
            )
        observed = gpd.read_parquet(path)
        if _full_frame_semantic_sha256(observed) != _full_frame_semantic_sha256(
            frame
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Existing geography output differs: {path}"
            )
        return
    atomic_parquet(frame, path)


def _normalized_geometry_map_sha256(
    frame: gpd.GeoDataFrame, *, geoid_column: str = "tract_geoid"
) -> str:
    if geoid_column not in frame or frame.crs is None:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Geography semantic mapping lacks GEOID or CRS."
        )
    normalized = frame.to_crs("EPSG:4326").sort_values(geoid_column)
    rows = []
    for row in normalized.itertuples(index=False):
        geoid = str(getattr(row, geoid_column))
        geometry = shapely.normalize(shapely.make_valid(row.geometry))
        rows.append(
            {
                "tract_geoid": geoid,
                "normalized_wkb_hex": shapely.to_wkb(
                    geometry,
                    hex=True,
                    output_dimension=2,
                    byte_order=1,
                    include_srid=False,
                ),
            }
        )
    return canonical_sha256(rows)


def _frame_identity(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    return {
        "rows": len(frame),
        "crs": frame.crs.to_string() if frame.crs is not None else None,
        "geometry_semantic_sha256": geometry_semantic_sha256(frame),
        "full_row_semantic_sha256": _full_frame_semantic_sha256(frame),
        "geoid_geometry_mapping_sha256": (
            _normalized_geometry_map_sha256(frame)
            if "tract_geoid" in frame
            else None
        ),
    }


def _source_paths(config: EvidenceConfig, city_id: str) -> dict[str, Path]:
    root = config.project_path(
        str(config.raw["outputs"]["processed_stage_directory"])
    ) / "geography" / city_id
    return {
        "city_boundary": root / "city_boundary.parquet",
        "tract_candidates": root / "tract_candidates.parquet",
        "primary_tracts": root / "primary_tracts.parquet",
    }


def _raw_root(config: EvidenceConfig, city_id: str) -> Path:
    return config.project_path(str(config.raw["outputs"]["raw_stage_directory"])) / (
        f"geography/{city_id}"
    )


def _official_candidate(config: EvidenceConfig, *, role: str) -> Any:
    section = config.raw["geography"]
    return _geography.LayerCandidate(
        label=f"census_tigerweb_2020_{role}_v12",
        url=str(section[f"{role}_layer"]),
        provider="U.S. Census Bureau",
        source_status="authoritative_contract_evidence",
    )


def _allowed_urls(config: EvidenceConfig) -> dict[str, set[tuple[str, str]]]:
    geography = config.raw["geography"]
    allowed: set[tuple[str, str]] = set()
    for role in ("place", "tract"):
        value = str(geography[f"{role}_layer"])
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Configured Census layer URL is unsafe."
            )
        for suffix in ("", "/query"):
            allowed.add(((parsed.hostname or "").lower(), parsed.path.rstrip("/") + suffix))
    return {"get": allowed, "post": set(), "head": set()}


def _stage_same_adapter_city(
    config: EvidenceConfig,
    *,
    city: CitySpec,
    strict_client: _GeographyClient,
) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, Any]]:
    place_candidate = _official_candidate(config, role="place")
    place = _geography._download_place(strict_client, place_candidate, city)
    boundary = _geography.standardize_place(place.frame, city)
    bbox = tuple(float(value) for value in boundary.total_bounds)
    tract_candidate = _official_candidate(config, role="tract")
    tracts = _geography._download_tracts(
        strict_client, tract_candidate, city, bbox
    )
    standardized = _geography.standardize_tracts(tracts.frame, city)
    candidates, primary = _geography.select_city_tracts(
        boundary,
        standardized,
        city_id=city.id,
        analysis_crs=str(config.raw["geography"]["analysis_crs"]),
        minimum_place_area_fraction=float(
            config.raw["geography"][
                "minimum_original_tract_area_inside_place_fraction"
            ]
        ),
        exclude_special_use_tracts=True,
    )
    raw_records: dict[str, Any] = {}
    for role, acquisition in (("place", place), ("tract", tracts)):
        for name, content in acquisition.raw_files.items():
            destination = _raw_root(config, city.id) / role / name
            atomic_bytes_no_clobber(content, destination)
            raw_records[_relative(config, destination)] = file_record(
                config, destination
            )
    frames = {
        "city_boundary": boundary,
        "tract_candidates": candidates,
        "primary_tracts": primary,
    }
    for name, frame in frames.items():
        _write_geoparquet_no_clobber(_source_paths(config, city.id)[name], frame)
    source = {
        "mode": "fresh_tigerweb_same_adapter",
        "place": {
            "label": place.candidate.label,
            "url": place.candidate.url,
            "provider": place.candidate.provider,
        },
        "tract": {
            "label": tracts.candidate.label,
            "url": tracts.candidate.url,
            "provider": tracts.candidate.provider,
        },
        "raw_files": raw_records,
    }
    return frames, source


def _raw_json(
    config: EvidenceConfig,
    record: Mapping[str, Any],
    *,
    label: str,
) -> tuple[bytes, dict[str, Any]]:
    path = config.project_path(str(record["path"]))
    expected = {key: record[key] for key in ("path", "bytes", "sha256")}
    if file_record(config, path) != expected:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Saved TIGERweb raw response changed: {label}"
        )
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Saved TIGERweb response is not JSON: {label}"
        ) from exc
    if not isinstance(payload, dict) or "error" in payload:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Saved TIGERweb response is invalid: {label}"
        )
    return raw, payload


def _load_existing_city(
    config: EvidenceConfig, *, city_id: str
) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, Any]]:
    plan = load_multicity_plan(
        config.project_path(str(config.raw["stage"]["experiment_config"]))
    )
    city = _city(plan, city_id)
    manifest_path = config.project_path(
        str(
            config.raw["geography"]["legacy_bindings"][
                f"{city_id.split('_')[0]}_manifest"
            ]
        )
    )
    manifest = read_json_with_commit(
        manifest_path, label=f"{city_id} historical geography"
    )
    if (
        manifest.get("algorithm_version")
        != "portable-predictor-source-evidence-v1-geography"
        or manifest.get("state") != "complete_target_blind_public_geography"
        or manifest.get("city", {}).get("id") != city_id
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Historical geography contract changed for {city_id}."
        )
    raw_files = manifest.get("raw_files")
    if not isinstance(raw_files, Mapping):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Historical geography raw set is invalid for {city_id}."
        )
    required = {
        "place/layer_metadata.json",
        "place/query_count.json",
        "place/features.geojson",
        "tract/layer_metadata.json",
        "tract/query_count.json",
    }
    tract_pages = sorted(
        key
        for key in raw_files
        if re.fullmatch(r"tract/features_\d{4}\.geojson", str(key))
    )
    if not required.issubset(raw_files) or not tract_pages:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Historical geography lacks a complete raw replay set: {city_id}."
        )
    _, place_metadata = _raw_json(
        config,
        raw_files["place/layer_metadata.json"],
        label=f"{city_id} place metadata",
    )
    _, tract_metadata = _raw_json(
        config,
        raw_files["tract/layer_metadata.json"],
        label=f"{city_id} tract metadata",
    )
    _geography._validate_layer_metadata(place_metadata, role="place")
    _geography._validate_layer_metadata(tract_metadata, role="tract")
    _, place_count = _raw_json(
        config,
        raw_files["place/query_count.json"],
        label=f"{city_id} place count",
    )
    _, tract_count = _raw_json(
        config,
        raw_files["tract/query_count.json"],
        label=f"{city_id} tract count",
    )
    _, place_payload = _raw_json(
        config,
        raw_files["place/features.geojson"],
        label=f"{city_id} place features",
    )
    place = _geography._frame_from_geojson(
        place_payload, label=f"{city_id} saved place"
    )
    tract_frames: list[gpd.GeoDataFrame] = []
    for page in tract_pages:
        _, page_payload = _raw_json(
            config,
            raw_files[page],
            label=f"{city_id} {page}",
        )
        tract_frames.append(
            _geography._frame_from_geojson(
                page_payload, label=f"{city_id} saved tract page"
            )
        )
    tracts = gpd.GeoDataFrame(
        pd.concat(tract_frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    if int(place_count.get("count", -1)) != len(place) or int(
        tract_count.get("count", -1)
    ) != len(tracts):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Historical geography count responses disagree for {city_id}."
        )
    boundary = _geography.standardize_place(place, city)
    standardized = _geography.standardize_tracts(tracts, city)
    candidates, primary = _geography.select_city_tracts(
        boundary,
        standardized,
        city_id=city_id,
        analysis_crs=str(config.raw["geography"]["analysis_crs"]),
        minimum_place_area_fraction=float(
            config.raw["geography"][
                "minimum_original_tract_area_inside_place_fraction"
            ]
        ),
        exclude_special_use_tracts=True,
    )
    frames = {
        "city_boundary": boundary,
        "tract_candidates": candidates,
        "primary_tracts": primary,
    }
    if set(manifest.get("output_tables", {})) != set(frames):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Existing geography table set changed for {city_id}."
        )
    for name, frame in frames.items():
        historical_record = manifest["output_tables"][name]
        historical_path = config.project_path(str(historical_record["path"]))
        if file_record(config, historical_path) != {
            key: historical_record[key] for key in ("path", "bytes", "sha256")
        }:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Historical geography output changed: {city_id}/{name}."
            )
        historical = gpd.read_parquet(historical_path)
        if (
            geometry_semantic_sha256(historical)
            != historical_record["geometry_semantic_sha256"]
            or _full_frame_semantic_sha256(historical)
            != _full_frame_semantic_sha256(frame)
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Raw geography replay changed current semantics: {city_id}/{name}."
            )
        _write_geoparquet_no_clobber(_source_paths(config, city_id)[name], frame)
    return frames, {
        "mode": "offline_replay_of_authenticated_tigerweb_snapshot",
        "historical_manifest": {
            **file_record(config, manifest_path),
            "commit_sha256": manifest["commit_sha256"],
        },
        "raw_response_count": len(raw_files),
        "raw_response_records": dict(raw_files),
        "current_adapter_replay_exact_to_historical_outputs": True,
    }


def _geoid_column(frame: pd.DataFrame) -> str:
    for column in ("tract_geoid", "GEOID", "geoid"):
        if column in frame:
            return column
    raise MissingSupportCalibrationEvidenceV1Error("No tract GEOID column found.")


def _legacy_primary(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    geoid_column = _geoid_column(frame)
    result = frame.copy()
    if "primary_included" in result:
        result = result.loc[result["primary_included"].astype(bool)].copy()
    elif "primary_exclusion_reason" in result:
        result = result.loc[result["primary_exclusion_reason"] == "included"].copy()
    if result.empty:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Los Angeles legacy geography contains no primary tracts."
        )
    result["tract_geoid"] = result[geoid_column].astype(str).str.zfill(11)
    return result.sort_values("tract_geoid").reset_index(drop=True)


def _rasterize_geoids(
    frame: gpd.GeoDataFrame,
    *,
    grid: Any,
    geoid_order: tuple[str, ...],
) -> np.ndarray:
    indexed = frame.copy()
    indexed["tract_geoid"] = indexed["tract_geoid"].astype(str).str.zfill(11)
    lookup = dict(zip(geoid_order, range(1, len(geoid_order) + 1), strict=True))
    projected = indexed.to_crs(grid.crs)
    shapes = [
        (geometry, lookup[geoid])
        for geoid, geometry in zip(
            projected["tract_geoid"], projected.geometry, strict=True
        )
        if geoid in lookup and geometry is not None and not geometry.is_empty
    ]
    return rasterize(
        shapes,
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )


def _la_compatibility(
    config: EvidenceConfig,
    *,
    frames: Mapping[str, gpd.GeoDataFrame],
) -> dict[str, Any]:
    bindings = config.raw["geography"]["legacy_bindings"]
    legacy_boundary_path = config.project_path(
        str(bindings["los_angeles_boundary"]), allow_pilot=True
    )
    legacy_tract_path = config.project_path(
        str(bindings["los_angeles_tract_universe"]), allow_pilot=True
    )
    legacy_boundary = gpd.read_file(legacy_boundary_path)
    legacy_universe = gpd.read_parquet(legacy_tract_path)
    legacy_primary = _legacy_primary(legacy_universe)
    new_primary = frames["primary_tracts"].sort_values("tract_geoid").reset_index(
        drop=True
    )
    legacy_set = set(legacy_primary["tract_geoid"])
    new_set = set(new_primary["tract_geoid"].astype(str))
    geoid_order = tuple(sorted(legacy_set | new_set))
    combined_boundary = gpd.GeoDataFrame(
        {"name": ["combined"]},
        geometry=[
            shapely.union_all(
                [
                    legacy_boundary.to_crs("EPSG:32611").geometry.union_all(),
                    frames["city_boundary"]
                    .to_crs("EPSG:32611")
                    .geometry.union_all(),
                ]
            )
        ],
        crs="EPSG:32611",
    )
    grid = build_fixed_grid(
        combined_boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    legacy_zones = _rasterize_geoids(
        legacy_primary, grid=grid, geoid_order=geoid_order
    )
    new_zones = _rasterize_geoids(new_primary, grid=grid, geoid_order=geoid_order)
    shared = sorted(legacy_set & new_set)
    legacy_index = legacy_primary.to_crs("EPSG:32611").set_index("tract_geoid")
    new_index = new_primary.to_crs("EPSG:32611").assign(
        tract_geoid=new_primary["tract_geoid"].astype(str)
    ).set_index("tract_geoid")
    comparisons: list[dict[str, Any]] = []
    for geoid in shared:
        old_projected = legacy_index.at[geoid, "geometry"]
        new_projected = new_index.at[geoid, "geometry"]
        union_area = float(old_projected.union(new_projected).area)
        intersection_area = float(old_projected.intersection(new_projected).area)
        comparisons.append(
            {
                "tract_geoid": geoid,
                "intersection_over_union": (
                    intersection_area / union_area if union_area > 0 else 0.0
                ),
                "symmetric_difference_fraction_of_union": (
                    1.0 - intersection_area / union_area if union_area > 0 else 1.0
                ),
            }
        )
    ious = np.asarray(
        [row["intersection_over_union"] for row in comparisons], dtype=float
    )
    return {
        "mode": "same_adapter_rebuild_plus_phase1_compatibility_audit",
        "legacy_inputs": {
            "city_boundary": file_record(config, legacy_boundary_path),
            "tract_universe": file_record(config, legacy_tract_path),
        },
        "legacy_primary_count": len(legacy_primary),
        "new_primary_count": len(new_primary),
        "exact_primary_geoid_set": legacy_set == new_set,
        "legacy_only_geoids": sorted(legacy_set - new_set),
        "new_only_geoids": sorted(new_set - legacy_set),
        "shared_geoid_count": len(shared),
        "minimum_tract_geometry_iou": float(ious.min()) if len(ious) else None,
        "median_tract_geometry_iou": float(np.median(ious)) if len(ious) else None,
        "grid": {
            "crs": grid.crs,
            "shape": list(grid.shape),
            "sha256": grid.sha256,
        },
        "legacy_zone_sha256": hashlib.sha256(
            np.asarray(legacy_zones, dtype="<i4", order="C").tobytes(order="C")
        ).hexdigest(),
        "new_zone_sha256": hashlib.sha256(
            np.asarray(new_zones, dtype="<i4", order="C").tobytes(order="C")
        ).hexdigest(),
        "zone_cell_assignment_exact": bool(np.array_equal(legacy_zones, new_zones)),
        "zone_disagreement_cell_count": int(np.count_nonzero(legacy_zones != new_zones)),
        "parity_is_observed_evidence_not_assumed_success": True,
        "requires_v3_interpretation": True,
    }


def _positive_ids(
    frame: gpd.GeoDataFrame, *, boundary: gpd.GeoDataFrame
) -> tuple[str, ...]:
    if frame.crs is None:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Source-footprint table lacks a CRS."
        )
    id_column = next(
        (
            name
            for name in (
                "item_id",
                "unit_id",
                "daymet_cell_id",
                "cell_id",
                "tile_id",
                "granule_id",
            )
            if name in frame
        ),
        None,
    )
    if id_column is None:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Source-footprint table lacks an identity column."
        )
    projected = frame.to_crs("EPSG:5070")
    city = boundary.to_crs("EPSG:5070").geometry.union_all()
    selected = projected.geometry.intersection(city).area.to_numpy(dtype=float) > 0
    return tuple(sorted(frame.loc[selected, id_column].astype(str).tolist()))


def _phoenix_compatibility(
    config: EvidenceConfig,
    *,
    frames: Mapping[str, gpd.GeoDataFrame],
) -> dict[str, Any]:
    experiment = config.project_path(str(config.raw["stage"]["experiment_config"]))
    old = _footprints.verify_city_source_footprints(experiment, "phoenix_az")
    old_boundary_path = config.project_path(
        str(old["geography_input"]["city_boundary"]["path"])
    )
    old_boundary = gpd.read_parquet(old_boundary_path)
    new_boundary = frames["city_boundary"]
    table_ids: dict[str, Any] = {}
    compatible = True
    for name in (
        "landsat_items",
        "sentinel_items",
        "optical_units",
        "daymet_cells",
        "terrain_tiles",
    ):
        record = old["output_tables"][name]
        table = gpd.read_parquet(config.project_path(str(record["path"])))
        old_ids = _positive_ids(table, boundary=old_boundary)
        new_ids = _positive_ids(table, boundary=new_boundary)
        same = old_ids == new_ids
        compatible = compatible and same
        table_ids[name] = {
            "old_count": len(old_ids),
            "new_count": len(new_ids),
            "exact_identity_set": same,
            "old_only": sorted(set(old_ids) - set(new_ids)),
            "new_only_within_saved_query": sorted(set(new_ids) - set(old_ids)),
        }
    old_bounds = old_boundary.to_crs("EPSG:4326").total_bounds
    new_bounds = new_boundary.to_crs("EPSG:4326").total_bounds
    new_bbox_within_old_query_bbox = bool(
        new_bounds[0] >= old_bounds[0]
        and new_bounds[1] >= old_bounds[1]
        and new_bounds[2] <= old_bounds[2]
        and new_bounds[3] <= old_bounds[3]
    )
    compatible = compatible and new_bbox_within_old_query_bbox
    return {
        "mode": "same_adapter_rebuild_plus_source_footprint_compatibility_audit",
        "historical_manifest_commit_sha256": old["commit_sha256"],
        "new_bbox_within_historical_query_bbox": new_bbox_within_old_query_bbox,
        "saved_metadata_replay": table_ids,
        "historical_source_footprint_compatible": compatible,
        "noncompatibility_requires_source_footprint_restage_before_v3": not compatible,
    }


def _city_manifest(
    config: EvidenceConfig,
    *,
    city: CitySpec,
    frames: Mapping[str, gpd.GeoDataFrame],
    source: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    plan_record: Mapping[str, Any],
) -> dict[str, Any]:
    paths = _source_paths(config, city.id)
    tables = {
        name: _parquet_record(config, paths[name], frame)
        for name, frame in frames.items()
    }
    candidates = frames["tract_candidates"]
    primary = frames["primary_tracts"]
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_city_geography_evidence",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "city": {
            "id": city.id,
            "name": city.name,
            "census_place_geoid": city.census_place_geoid,
            "role": city.role,
            "target_values_status": city.target_values_status,
        },
        "plan_authorization": dict(plan_record),
        "selection_contract": {
            "vintage": 2020,
            "analysis_crs": "EPSG:5070",
            "minimum_original_tract_area_inside_place_fraction": 0.5,
            "exclude_special_use_tract_prefix": "98",
            "repair_order": (
                "make_each_geometry_valid_before_place_dissolve_or_tract_intersection"
            ),
            "target_or_predictor_performance_used": False,
        },
        "source": dict(source),
        "counts": {
            "candidate_tracts": len(candidates),
            "positive_overlap_tracts": int(
                (candidates["place_overlap_area_m2"] > 0).sum()
            ),
            "threshold_tracts": int((candidates["place_area_fraction"] >= 0.5).sum()),
            "special_use_threshold_tracts": int(
                (
                    (candidates["place_area_fraction"] >= 0.5)
                    & candidates["special_use_tract"].astype(bool)
                ).sum()
            ),
            "primary_tracts": len(primary),
        },
        "table_identities": {
            name: _frame_identity(frame) for name, frame in frames.items()
        },
        "output_tables": tables,
        "compatibility": dict(compatibility),
        "access_contract": {
            "public_census_geometry_read": city.id in {"los_angeles_ca", "phoenix_az"},
            "saved_tigerweb_geometry_replayed": city.id in {"houston_tx", "chicago_il"},
            "phase1_pilot_geography_read_for_compatibility": city.id == "los_angeles_ca",
            "external_target_or_qa_values_read": False,
            "landsat_thermal_values_read": False,
            "predictor_values_read_or_computed": False,
            "model_fit_or_prediction_performed": False,
            "final_evaluation_outputs_opened": False,
        },
    }
    return payload


def _verify_city_manifest(config: EvidenceConfig, city_id: str) -> dict[str, Any]:
    path = config.project_path(_city_geography_path(city_id))
    payload = read_json_with_commit(path, label=f"{city_id} geography V1")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != "complete_target_blind_city_geography_evidence"
        or payload.get("city", {}).get("id") != city_id
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"City geography manifest changed: {city_id}"
        )
    source = payload.get("source", {})
    raw_records = source.get("raw_files", source.get("raw_response_records"))
    if not isinstance(raw_records, Mapping) or not raw_records:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"City geography raw provenance is incomplete: {city_id}"
        )
    for record in raw_records.values():
        raw_path = config.project_path(str(record["path"]))
        if file_record(config, raw_path) != {
            key: record[key] for key in ("path", "bytes", "sha256")
        }:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"City geography raw response changed: {city_id}"
            )
    for name, record in payload["output_tables"].items():
        frame = gpd.read_parquet(config.project_path(str(record["path"])))
        if _parquet_record(config, config.project_path(str(record["path"])), frame) != record:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"City geography table changed: {city_id}/{name}"
            )
    return payload


def _verify_global(config: EvidenceConfig) -> dict[str, Any]:
    path = config.project_path(GEOGRAPHY_GLOBAL_PATH)
    payload = read_json_with_commit(path, label="four-city geography terminal")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or set(payload.get("cities", {})) != set(CITY_IDS)
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Four-city geography terminal changed."
        )
    for city_id in CITY_IDS:
        city = _verify_city_manifest(config, city_id)
        if payload["cities"][city_id]["commit_sha256"] != city["commit_sha256"]:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Four-city terminal lost {city_id}."
            )
    return payload


def stage_four_city_geography_contract_v1(
    config: EvidenceConfig,
    *,
    plan_record: Mapping[str, Any],
    session: Any | None = None,
) -> dict[str, Any]:
    """Stage or authenticate the four target-blind geography records."""

    global_path = config.project_path(GEOGRAPHY_GLOBAL_PATH)
    if global_path.is_file():
        return _verify_global(config)
    existing_city_paths = {
        city_id: config.project_path(_city_geography_path(city_id))
        for city_id in CITY_IDS
    }
    plan = load_multicity_plan(
        config.project_path(str(config.raw["stage"]["experiment_config"]))
    )
    active_session = requests.Session() if session is None else session
    strict_client = _GeographyClient(active_session, config=config)
    payloads: dict[str, dict[str, Any]] = {}
    for city_id in CITY_IDS:
        if existing_city_paths[city_id].is_file():
            payloads[city_id] = _verify_city_manifest(config, city_id)
            continue
        city = _city(plan, city_id)
        if city_id in {"los_angeles_ca", "phoenix_az"}:
            frames, source = _stage_same_adapter_city(
                config, city=city, strict_client=strict_client
            )
        else:
            frames, source = _load_existing_city(config, city_id=city_id)
        if city_id == "los_angeles_ca":
            compatibility = _la_compatibility(config, frames=frames)
        elif city_id == "phoenix_az":
            compatibility = _phoenix_compatibility(config, frames=frames)
        else:
            compatibility = {
                "mode": "exact_authenticated_offline_replay",
                "existing_manifest_identity_replayed": True,
            }
        payload = _city_manifest(
            config,
            city=city,
            frames=frames,
            source=source,
            compatibility=compatibility,
            plan_record=plan_record,
        )
        write_manifest_no_clobber(payload, existing_city_paths[city_id])
        payloads[city_id] = _verify_city_manifest(config, city_id)
    global_payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "plan_authorization": dict(plan_record),
        "cities": {
            city_id: checkpoint_record(config, existing_city_paths[city_id])
            for city_id in CITY_IDS
        },
        "fresh_tigerweb_request_count": strict_client.request_count,
        "fresh_tigerweb_response_bytes": strict_client.response_bytes,
        "same_adapter_city_ids": ["los_angeles_ca", "phoenix_az"],
        "offline_replayed_city_ids": ["houston_tx", "chicago_il"],
        "compatibility_evidence_may_record_nonparity": True,
        "geography_contract_formally_locked": False,
        "predictor_build_authorized": False,
        "external_target_or_qa_values_read": False,
        "next_gate": "worldcover_eligible_support_evidence_v1",
    }
    write_manifest_no_clobber(global_payload, global_path)
    return _verify_global(config)
