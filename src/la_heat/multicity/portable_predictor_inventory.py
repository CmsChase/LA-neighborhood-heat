"""Freeze the target-blind city, tract, and Landsat-date predictor keys."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import pandas as pd

from la_heat.inventory import SceneRecord, group_physical_overpasses
from la_heat.multicity import source_footprints as footprints
from la_heat.multicity.config import CitySpec, load_multicity_plan
from la_heat.multicity.phoenix_source_footprint_restage import (
    verify_phoenix_source_footprint_restage,
)
from la_heat.multicity.portable_predictor_contract import (
    OUTPUT_PATH as CONTRACT_PATH,
)
from la_heat.multicity.portable_predictor_contract import (
    verify_portable_predictor_contract,
)
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "portable-predictor-inventory"
COMPLETE_STATE: Final = "complete_target_blind_portable_predictor_inventory"
DEFAULT_CONFIG: Final = Path("configs/multicity/portable_predictor_build.toml")
EXTERNAL_CITY_IDS: Final = ("phoenix_az", "houston_tx", "chicago_il")


class PortablePredictorInventoryError(ValueError):
    """Raised when the target-blind predictor key inventory is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortablePredictorInventoryError(f"Expected JSON object: {path}")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> None:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise PortablePredictorInventoryError(f"{label} commit changed.")


def _load_config(project_root: Path, path: str | Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = project_root / config_path
    config_path = config_path.resolve()
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    if (
        config.get("stage", {}).get("algorithm_version") != ALGORITHM_VERSION
        or config.get("stage", {}).get("analysis_crs") != "EPSG:5070"
        or float(
            config.get("landsat_dates", {}).get(
                "minimum_city_union_coverage_fraction", -1
            )
        )
        != 0.98
    ):
        raise PortablePredictorInventoryError("Portable inventory config changed.")
    return config, config_path


def _resolve(project_root: Path, value: str) -> Path:
    path = (project_root / value).resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise PortablePredictorInventoryError(f"Configured path escapes project: {value}")
    return path


def _file_record(project_root: Path, path: Path) -> dict[str, Any]:
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
        raise PortablePredictorInventoryError(f"Inventory artifact changed: {path}")
    return path


def _geography(
    project_root: Path, city_id: str
) -> tuple[dict[str, Any], gpd.GeoDataFrame, gpd.GeoDataFrame, Path]:
    path = project_root / (
        f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
    )
    payload = _read_json(path)
    _verify_commit(payload, label=f"{city_id} geography")
    if (
        payload.get("state") != "complete_target_blind_city_geography_evidence"
        or payload.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
    ):
        raise PortablePredictorInventoryError(f"Invalid geography for {city_id}.")
    boundary_record = payload["output_tables"]["city_boundary"]
    tract_record = payload["output_tables"]["primary_tracts"]
    boundary_path = _verify_record(project_root, boundary_record)
    tract_path = _verify_record(project_root, tract_record)
    boundary = gpd.read_parquet(boundary_path)
    tracts = gpd.read_parquet(tract_path)
    if boundary.crs is None or tracts.crs is None or tracts.empty:
        raise PortablePredictorInventoryError(f"Empty geography for {city_id}.")
    return payload, boundary, tracts, path


def _source_manifest(project_root: Path, city_id: str) -> tuple[dict[str, Any], Path]:
    if city_id == "phoenix_az":
        payload = verify_phoenix_source_footprint_restage()
        path = project_root / (
            "manifests/multicity/cities/phoenix_az/source_footprints/"
            "PORTABLE_SOURCE_FOOTPRINT.json"
        )
    else:
        path = project_root / (
            f"manifests/multicity/cities/{city_id}/source_footprints/"
            "SOURCE_FOOTPRINTS.json"
        )
        payload = _read_json(path)
        _verify_commit(payload, label=f"{city_id} source footprint")
        if payload.get("state") != footprints.COMPLETE_STATE:
            raise PortablePredictorInventoryError(
                f"Invalid source footprint for {city_id}."
            )
    if (
        payload.get("city", {}).get("target_values_status") != "sealed"
        or payload.get("access_contract", {}).get("external_lst_values_read") is not False
        or payload.get("access_contract", {}).get("predictor_construction_performed")
        is not False
    ):
        raise PortablePredictorInventoryError(
            f"Source-footprint access changed for {city_id}."
        )
    return payload, path


def _external_landsat_items(
    project_root: Path, city_id: str
) -> tuple[gpd.GeoDataFrame, dict[str, Any], Path]:
    payload, manifest_path = _source_manifest(project_root, city_id)
    record = payload["output_tables"]["landsat_items"]
    path = _verify_record(project_root, record)
    items = gpd.read_parquet(path)
    if items.crs is None or items.empty:
        raise PortablePredictorInventoryError(f"No Landsat metadata for {city_id}.")
    return items, payload, manifest_path


def _query_los_angeles_items(
    *,
    plan: Any,
    city: CitySpec,
    boundary: gpd.GeoDataFrame,
    config: Mapping[str, Any],
    raw_root: Path,
    client: footprints._HttpClientLike,
) -> tuple[gpd.GeoDataFrame, list[Path], list[dict[str, Any]]]:
    dates = config["landsat_dates"]
    bbox = tuple(float(value) for value in boundary.to_crs("EPSG:4326").total_bounds)
    tables: list[gpd.GeoDataFrame] = []
    raw_paths: list[Path] = []
    queries: list[dict[str, Any]] = []
    for year in range(int(dates["los_angeles_start_year"]), int(dates["los_angeles_end_year"]) + 1):
        start = date(year, int(dates["warm_season_start_month"]), 1)
        end = date(year, int(dates["warm_season_end_month"]), 31)
        features, pages, query = footprints.fetch_public_stac_metadata(
            client,
            api=str(plan.raw["sources"]["landsat_stac_api"]),
            collection=str(plan.raw["target"]["landsat_collection"]),
            bbox_wgs84=bbox,
            datetime_interval=footprints.local_date_interval_to_utc(
                start, end, city.timezone
            ),
            fields=footprints.LANDSAT_FIELDS,
            properties=footprints.LANDSAT_PROPERTIES,
            page_limit=int(dates["page_limit"]),
            query={
                "platform": {"in": list(plan.raw["target"]["sensors"])},
                "landsat:collection_category": {"eq": "T1"},
                "landsat:correction": {"eq": "L2SP"},
            },
        )
        tables.append(
            footprints.build_optical_item_table(
                features,
                source="landsat_wrs",
                collection=str(plan.raw["target"]["landsat_collection"]),
                expected_properties=footprints.LANDSAT_PROPERTIES,
                allowed_platforms=tuple(plan.raw["target"]["sensors"]),
                local_start_date=start,
                local_end_date=end,
                timezone=city.timezone,
                city_boundary=boundary,
                analysis_crs=str(config["stage"]["analysis_crs"]),
            )
        )
        queries.append({"year": year, **query})
        for page_number, page in enumerate(pages, start=1):
            path = raw_root / str(year) / f"stac_page_{page_number:03d}.json"
            atomic_json(page, path)
            raw_paths.append(path)
    items = gpd.GeoDataFrame(
        pd.concat(tables, ignore_index=True),
        geometry="geometry",
        crs=tables[0].crs,
    ).sort_values("item_id", kind="stable").reset_index(drop=True)
    if items.empty or items["item_id"].duplicated().any():
        raise PortablePredictorInventoryError(
            "Los Angeles Landsat metadata is empty or duplicated."
        )
    return items, raw_paths, queries


def _overpasses(
    items: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    *,
    analysis_crs: str,
    maximum_gap_minutes: int,
    minimum_coverage: float,
) -> pd.DataFrame:
    wgs84 = items.to_crs("EPSG:4326")
    scenes = [
        SceneRecord(
            item_id=str(row.item_id),
            platform=str(row.platform),
            acquired_utc=datetime.fromisoformat(
                str(row.acquired_utc).replace("Z", "+00:00")
            ),
            local_date=str(row.acquisition_local_date),
            wrs_path=str(row.wrs_path),
            wrs_row=str(row.wrs_row),
            cloud_cover_percent=float("nan"),
            city_coverage_fraction=float(row.city_overlap_fraction),
            geometry_wgs84=row.geometry,
            asset_hrefs={},
        )
        for row in wgs84.itertuples(index=False)
    ]
    records = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=boundary.to_crs("EPSG:4326").geometry.union_all(),
        analysis_crs=analysis_crs,
        maximum_time_gap_minutes=maximum_gap_minutes,
    )
    rows: list[dict[str, Any]] = []
    for record in records:
        row = asdict(record)
        row["scene_ids"] = "|".join(record.scene_ids)
        row["wrs_path_rows"] = "|".join(record.wrs_path_rows)
        row["primary_eligible"] = bool(
            record.union_city_coverage_fraction >= minimum_coverage
            and not record.ambiguous_local_date
        )
        row["source_lock_sha256"] = canonical_sha256(row)
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(
        ["local_date", "overpass_id"], kind="stable"
    ).reset_index(drop=True)
    primary = frame.loc[frame["primary_eligible"]]
    if primary.empty or primary["local_date"].duplicated().any():
        raise PortablePredictorInventoryError(
            "No unique full-city primary Landsat dates were found."
        )
    return frame


def _key_universe(
    city_id: str, tracts: gpd.GeoDataFrame, overpasses: pd.DataFrame
) -> pd.DataFrame:
    geoid_column = "GEOID" if "GEOID" in tracts else "tract_geoid"
    geoids = tracts[geoid_column].astype("string").sort_values().drop_duplicates()
    primary = overpasses.loc[overpasses["primary_eligible"]].copy()
    left = primary[["local_date", "overpass_id", "platform"]]
    left = left.rename(columns={"local_date": "target_date"})
    left["target_date"] = pd.to_datetime(left["target_date"], errors="raise")
    left["_join"] = 1
    right = pd.DataFrame({"tract_geoid": geoids, "_join": 1})
    keys = left.merge(right, on="_join", validate="many_to_many").drop(columns="_join")
    keys.insert(0, "city_id", city_id)
    keys = keys[
        ["city_id", "tract_geoid", "target_date", "overpass_id", "platform"]
    ].sort_values(["target_date", "tract_geoid"], kind="stable").reset_index(drop=True)
    expected = len(primary) * len(geoids)
    if len(keys) != expected or keys.duplicated(
        ["city_id", "tract_geoid", "target_date"]
    ).any():
        raise PortablePredictorInventoryError(f"Invalid predictor keys for {city_id}.")
    return keys


def _table_record(project_root: Path, path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    record = parquet_file_record(path, frame)
    record["path"] = path.resolve().relative_to(project_root.resolve()).as_posix()
    candidates = (
        "city_id",
        "target_date",
        "tract_geoid",
        "item_id",
        "overpass_id",
    )
    record["frame_semantic_sha256"] = canonical_frame_sha256(
        frame,
        sort_by=[column for column in candidates if column in frame],
    )
    return record


def verify_portable_predictor_inventory(
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    plan = load_multicity_plan("configs/multicity/experiment.toml")
    workspace = MulticityWorkspace.from_plan(plan)
    config, _ = _load_config(workspace.project_root, config_path)
    manifest_path = _resolve(workspace.project_root, config["outputs"]["manifest"])
    payload = _read_json(manifest_path)
    _verify_commit(payload, label="Portable predictor inventory")
    if (
        payload.get("state") != COMPLETE_STATE
        or payload.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or payload.get("access_contract", {}).get("landsat_asset_hrefs_read") is not False
        or payload.get("decision", {}).get("predictor_keys_frozen") is not True
    ):
        raise PortablePredictorInventoryError("Portable predictor inventory changed.")
    for record in payload["raw_files"].values():
        _verify_record(workspace.project_root, record)
    for record in payload["output_tables"].values():
        path = _verify_record(workspace.project_root, record)
        frame = (
            gpd.read_parquet(path)
            if "geometry_semantic_sha256" in record
            else pd.read_parquet(path)
        )
        if len(frame) != record["rows"]:
            raise PortablePredictorInventoryError(f"Inventory row count changed: {path}")
    return payload


def build_portable_predictor_inventory(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    client: footprints._HttpClientLike | None = None,
) -> dict[str, Any]:
    plan = load_multicity_plan("configs/multicity/experiment.toml")
    workspace = MulticityWorkspace.from_plan(plan)
    contract = verify_portable_predictor_contract(plan.path)
    if contract["decision"]["predictor_build_authorized"] is not True:
        raise PortablePredictorInventoryError("Predictor build is not authorized.")
    config, config_file = _load_config(workspace.project_root, config_path)
    outputs = config["outputs"]
    raw_root = _resolve(workspace.project_root, outputs["raw_directory"])
    processed_root = _resolve(workspace.project_root, outputs["processed_directory"])
    manifest_path = _resolve(workspace.project_root, outputs["manifest"])
    if manifest_path.is_file():
        return verify_portable_predictor_inventory(config_file)

    active_client = footprints._retrying_session() if client is None else client
    date_rules = config["landsat_dates"]
    city_payloads: dict[str, Any] = {}
    raw_paths: list[Path] = []
    output_tables: dict[str, dict[str, Any]] = {}
    all_keys: list[pd.DataFrame] = []
    evidence: dict[str, dict[str, Any]] = {
        CONTRACT_PATH.as_posix(): _file_record(
            workspace.project_root, workspace.project_root / CONTRACT_PATH
        ),
        config_file.relative_to(workspace.project_root).as_posix(): _file_record(
            workspace.project_root, config_file
        ),
    }
    for city in plan.cities:
        geography, boundary, tracts, geography_path = _geography(
            workspace.project_root, city.id
        )
        evidence[geography_path.relative_to(workspace.project_root).as_posix()] = (
            _file_record(workspace.project_root, geography_path)
        )
        if city.id == "los_angeles_ca":
            items, city_raw_paths, queries = _query_los_angeles_items(
                plan=plan,
                city=city,
                boundary=boundary,
                config=config,
                raw_root=raw_root / city.id / "landsat",
                client=active_client,
            )
            raw_paths.extend(city_raw_paths)
            source_manifest_commit = None
        else:
            items, source_manifest, source_path = _external_landsat_items(
                workspace.project_root, city.id
            )
            evidence[source_path.relative_to(workspace.project_root).as_posix()] = (
                _file_record(workspace.project_root, source_path)
            )
            queries = [source_manifest["queries"]["landsat"]]
            source_manifest_commit = source_manifest["commit_sha256"]
        overpasses = _overpasses(
            items,
            boundary,
            analysis_crs=str(config["stage"]["analysis_crs"]),
            maximum_gap_minutes=int(date_rules["maximum_overpass_span_minutes"]),
            minimum_coverage=float(date_rules["minimum_city_union_coverage_fraction"]),
        )
        keys = _key_universe(city.id, tracts, overpasses)
        all_keys.append(keys)
        city_directory = processed_root / city.id
        item_path = city_directory / "landsat_metadata_items.parquet"
        overpass_path = city_directory / "landsat_overpasses.parquet"
        key_path = city_directory / "predictor_keys.parquet"
        atomic_parquet(items, item_path)
        atomic_parquet(overpasses, overpass_path)
        atomic_parquet(keys, key_path)
        item_record = footprints._table_record(
            workspace.project_root, item_path, gpd.read_parquet(item_path), geometry=True
        )
        overpass_record = _table_record(
            workspace.project_root, overpass_path, pd.read_parquet(overpass_path)
        )
        key_record = _table_record(
            workspace.project_root, key_path, pd.read_parquet(key_path)
        )
        output_tables[f"{city.id}/landsat_items"] = item_record
        output_tables[f"{city.id}/overpasses"] = overpass_record
        output_tables[f"{city.id}/keys"] = key_record
        primary = overpasses.loc[overpasses["primary_eligible"]]
        city_payloads[city.id] = {
            "role": city.role,
            "target_values_status": city.target_values_status,
            "geography_commit_sha256": geography["commit_sha256"],
            "source_manifest_commit_sha256": source_manifest_commit,
            "landsat_query_records": queries,
            "landsat_item_count": len(items),
            "physical_overpass_count": len(overpasses),
            "primary_date_count": len(primary),
            "tract_count": int(keys["tract_geoid"].nunique()),
            "predictor_key_count": len(keys),
            "date_range": [
                str(primary["local_date"].min()),
                str(primary["local_date"].max()),
            ],
        }
    combined = pd.concat(all_keys, ignore_index=True).sort_values(
        ["city_id", "target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    combined_path = processed_root / "predictor_keys.parquet"
    atomic_parquet(combined, combined_path)
    output_tables["combined_predictor_keys"] = _table_record(
        workspace.project_root, combined_path, pd.read_parquet(combined_path)
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "contract_commit_sha256": contract["commit_sha256"],
        "date_selection_contract": {
            "source": "public_landsat_stac_metadata_only",
            "maximum_overpass_span_minutes": int(
                date_rules["maximum_overpass_span_minutes"]
            ),
            "minimum_city_union_coverage_fraction": float(
                date_rules["minimum_city_union_coverage_fraction"]
            ),
            "ambiguous_same_local_date_excluded": True,
            "global_scene_cloud_filter": False,
        },
        "cities": city_payloads,
        "evidence": evidence,
        "raw_files": {
            path.relative_to(workspace.project_root).as_posix(): _file_record(
                workspace.project_root, path
            )
            for path in sorted(raw_paths)
        },
        "output_tables": output_tables,
        "combined": {
            "city_count": int(combined["city_id"].nunique()),
            "date_count": int(combined[["city_id", "target_date"]].drop_duplicates().shape[0]),
            "tract_count": int(combined[["city_id", "tract_geoid"]].drop_duplicates().shape[0]),
            "key_count": len(combined),
        },
        "access_contract": {
            "public_landsat_metadata_read": True,
            "landsat_assets_excluded_from_network_queries": True,
            "landsat_asset_hrefs_read": False,
            "landsat_thermal_or_target_qa_values_read": False,
            "external_target_or_qa_values_read": False,
            "predictor_values_computed": False,
            "model_fit_or_prediction_performed": False,
            "evaluation_outputs_opened": False,
        },
        "decision": {
            "predictor_keys_frozen": True,
            "external_targets_remain_sealed": True,
            "next_safe_stage": "build_static_calendar_and_daymet_predictors",
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, manifest_path)
    return verify_portable_predictor_inventory(config_file)
