"""Target-blind Landsat inventory for the isolated 2025 final test.

This stage may inspect public STAC metadata only.  It deliberately does not open
any Landsat asset, target raster, target QA table, fitted model, or model score.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import shape

from la_heat.config import ResearchConfig, load_config
from la_heat.inventory import (
    REQUIRED_ASSETS,
    SceneRecord,
    group_physical_overpasses,
    scene_is_eligible,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)

FINAL_TEST_YEAR: Final = 2025
FINAL_TEST_START: Final = date(2025, 5, 1)
FINAL_TEST_END: Final = date(2025, 10, 31)
SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-landsat-inventory-v1-target-blind"
SUMMARY_FILENAME: Final = "LANDSAT_INVENTORY.json"
SCENE_FILENAME: Final = "scene_inventory.csv"
OVERPASS_FILENAME: Final = "overpass_inventory.csv"
PRIMARY_FILENAME: Final = "primary_overpass_manifest.csv"
KEY_UNIVERSE_FILENAME: Final = "target_blind_key_universe.parquet"
PIPELINE_FILES: Final = (
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/inventory.py",
    "src/la_heat/provenance.py",
    "scripts/build_final_test_inventory.py",
)


class FinalTestInventoryError(RuntimeError):
    """Raised when the target-blind final-test inventory fails closed."""


class _SearchResult(Protocol):
    def items(self) -> Any: ...


class _StacClient(Protocol):
    def search(self, **kwargs: object) -> _SearchResult: ...


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestInventoryError(f"Cannot read JSON input: {path}") from error
    if sha256_file(path) != before:
        raise FinalTestInventoryError(f"JSON input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise FinalTestInventoryError(f"JSON input must be an object: {path}")
    return payload


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestInventoryError(f"{label} has an invalid canonical commit hash.")
    return recorded


def authenticate_formal_model_lock(path: str | Path) -> tuple[dict[str, Any], str]:
    """Authenticate the formal lock without granting label or score access."""

    lock_path = Path(path).resolve()
    payload = _read_json(lock_path)
    _verify_commit(payload, label="Formal model lock")
    if (
        payload.get("state") != "frozen_for_one_time_2025_evaluation"
        or payload.get("formal_model_lock_written") is not True
        or payload.get("final_test_year") != FINAL_TEST_YEAR
        or payload.get("final_test_locked") is not True
        or payload.get("final_test_unlocked") is not False
        or payload.get("final_test_used") is not False
        or payload.get("final_test_values_read") is not False
        or payload.get("contains_final_test_year") is not False
        or payload.get("one_time_final_evaluation_authorized") is not False
        or set(payload.get("models", {})) != {"B1", "M2"}
    ):
        raise FinalTestInventoryError(
            "Formal model lock is not the untouched locked-2025 specification."
        )
    return payload, sha256_file(lock_path)


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path:
        raise FinalTestInventoryError(f"Invalid STAC asset URL: {url!r}")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def discover_final_test_scenes(
    client: _StacClient,
    *,
    config: ResearchConfig,
    city_boundary: gpd.GeoDataFrame,
    start_date: date = FINAL_TEST_START,
    end_date: date = FINAL_TEST_END,
) -> tuple[list[SceneRecord], list[Any]]:
    """Discover all eligible 2025 Tier-1 L2SP scenes from metadata only."""

    if start_date.year != FINAL_TEST_YEAR or end_date.year != FINAL_TEST_YEAR:
        raise FinalTestInventoryError("Final-test inventory dates must remain in 2025.")
    if end_date < start_date:
        raise FinalTestInventoryError("Final-test end date precedes its start date.")
    landsat = config.raw["landsat"]
    study = config.raw["study"]
    search = client.search(
        collections=[landsat["collection"]],
        bbox=study["bbox_wgs84"],
        datetime=f"{start_date.isoformat()}/{end_date.isoformat()}",
        query={
            "landsat:collection_category": {"eq": "T1"},
            "landsat:correction": {"eq": "L2SP"},
        },
    )
    allowed_platforms = set(landsat["sensors"])
    warm_months = set(study["warm_season_months"])
    timezone = ZoneInfo(study["timezone"])
    city = city_boundary.to_crs(study["crs_analysis"]).geometry.union_all()

    by_id: dict[str, SceneRecord] = {}
    for item in search.items():
        if not scene_is_eligible(item, allowed_platforms=allowed_platforms):
            continue
        acquired = item.datetime
        if acquired is None or acquired.tzinfo is None or acquired.utcoffset() is None:
            raise FinalTestInventoryError(f"STAC item {item.id} lacks an aware datetime.")
        acquired = acquired.astimezone(UTC)
        local_date = acquired.astimezone(timezone).date()
        if not start_date <= local_date <= end_date or local_date.month not in warm_months:
            continue
        geometry_wgs84 = shape(item.geometry)
        if geometry_wgs84.is_empty or not geometry_wgs84.is_valid:
            raise FinalTestInventoryError(f"STAC item {item.id} has invalid geometry.")
        projected = gpd.GeoSeries([geometry_wgs84], crs="EPSG:4326").to_crs(
            study["crs_analysis"]
        ).iloc[0]
        coverage = float(projected.intersection(city).area / city.area)
        if coverage <= 0:
            continue
        cloud = item.properties.get("eo:cloud_cover", np.nan)
        record = SceneRecord(
            item_id=str(item.id),
            platform=str(item.properties["platform"]),
            acquired_utc=acquired,
            local_date=local_date.isoformat(),
            wrs_path=str(item.properties.get("landsat:wrs_path", "")),
            wrs_row=str(item.properties.get("landsat:wrs_row", "")),
            cloud_cover_percent=float(cloud),
            city_coverage_fraction=coverage,
            geometry_wgs84=geometry_wgs84,
            asset_hrefs={
                asset: _canonical_url(item.assets[asset].href)
                for asset in REQUIRED_ASSETS
            },
        )
        previous = by_id.get(record.item_id)
        if previous is not None and previous != record:
            raise FinalTestInventoryError(
                f"Conflicting duplicate STAC item: {record.item_id}"
            )
        by_id[record.item_id] = record

    scenes = sorted(by_id.values(), key=lambda row: (row.acquired_utc, row.item_id))
    overpasses = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=city_boundary.geometry.union_all(),
        analysis_crs=study["crs_analysis"],
        maximum_time_gap_minutes=int(landsat["maximum_overpass_span_minutes"]),
    )
    return scenes, overpasses


def _scene_frame(scenes: list[SceneRecord]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scene in scenes:
        row = asdict(scene)
        row.pop("geometry_wgs84")
        assets = row.pop("asset_hrefs")
        row.update({f"{name}_href": href for name, href in assets.items()})
        row["acquired_utc"] = scene.acquired_utc.isoformat()
        rows.append(row)
    if not rows:
        raise FinalTestInventoryError("No eligible intersecting 2025 Landsat scenes found.")
    return pd.DataFrame(rows).sort_values("item_id").reset_index(drop=True)


def _overpass_frame(
    overpasses: list[Any],
    *,
    scene_frame: pd.DataFrame,
    city_geometry_sha256: str,
    minimum_city_coverage: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scene_lookup = scene_frame.set_index("item_id", drop=False)
    for overpass in overpasses:
        row = asdict(overpass)
        row["scene_ids"] = "|".join(overpass.scene_ids)
        row["wrs_path_rows"] = "|".join(overpass.wrs_path_rows)
        frozen_scenes = scene_lookup.loc[list(overpass.scene_ids)].sort_index()
        row["source_lock_sha256"] = canonical_sha256(
            {
                "schema_version": SCHEMA_VERSION,
                "overpass_id": overpass.overpass_id,
                "platform": overpass.platform,
                "local_date": overpass.local_date,
                "acquired_utc_min": overpass.acquired_utc_min,
                "acquired_utc_max": overpass.acquired_utc_max,
                "scene_ids": list(overpass.scene_ids),
                "wrs_path_rows": list(overpass.wrs_path_rows),
                "union_city_coverage_fraction": (
                    overpass.union_city_coverage_fraction
                ),
                "city_boundary_geometry_sha256": city_geometry_sha256,
                "scenes": frozen_scenes.to_dict("records"),
            }
        )
        rows.append(row)
    if not rows:
        raise FinalTestInventoryError("No physical 2025 Landsat overpasses were found.")
    frame = pd.DataFrame(rows).sort_values(
        ["local_date", "overpass_id"]
    ).reset_index(drop=True)
    frame["primary_eligible"] = (
        frame["union_city_coverage_fraction"].ge(minimum_city_coverage)
        & ~frame["ambiguous_local_date"]
    )
    return frame


def _tract_manifest_commit(tracts: gpd.GeoDataFrame) -> str:
    columns = [
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
    if set(columns) - set(tracts.columns):
        raise FinalTestInventoryError("Frozen primary tract manifest schema drifted.")
    return canonical_frame_sha256(tracts, sort_by=["GEOID"], columns=columns)


def build_target_blind_key_universe(
    primary_tracts: gpd.GeoDataFrame,
    primary_overpasses: pd.DataFrame,
) -> pd.DataFrame:
    """Cross frozen tracts with metadata-selected dates, without reading labels."""

    tracts = primary_tracts.copy()
    if "primary_included" in tracts and not tracts["primary_included"].all():
        raise FinalTestInventoryError("Primary tract manifest contains excluded tracts.")
    if tracts["GEOID"].astype("string").duplicated().any():
        raise FinalTestInventoryError("Primary tract manifest has duplicate GEOIDs.")
    dates = pd.to_datetime(primary_overpasses["local_date"], errors="raise")
    if dates.duplicated().any() or not dates.dt.year.eq(FINAL_TEST_YEAR).all():
        raise FinalTestInventoryError("Primary overpass dates are not unique 2025 dates.")
    tract_fields = [
        "GEOID",
        "spatial_block",
        "latitude_quartile",
        "longitude_quartile",
    ]
    left = primary_overpasses[["local_date", "overpass_id", "platform"]].copy()
    left["target_date"] = pd.to_datetime(left.pop("local_date"), errors="raise")
    left["_join"] = 1
    right = tracts[tract_fields].copy()
    right["tract_geoid"] = right.pop("GEOID").astype("string")
    right["_join"] = 1
    result = left.merge(right, on="_join", how="inner", validate="many_to_many").drop(
        columns="_join"
    )
    result = result[
        [
            "tract_geoid",
            "target_date",
            "overpass_id",
            "platform",
            "spatial_block",
            "latitude_quartile",
            "longitude_quartile",
        ]
    ].sort_values(["target_date", "tract_geoid"], kind="stable")
    result = result.reset_index(drop=True)
    expected = len(primary_overpasses) * len(primary_tracts)
    if len(result) != expected or result.duplicated(
        ["tract_geoid", "target_date"]
    ).any():
        raise FinalTestInventoryError("Target-blind key universe is not a full grid.")
    return result


def _existing_summary(output: Path) -> dict[str, Any] | None:
    summary_path = output / SUMMARY_FILENAME
    if not summary_path.exists():
        return None
    payload = _read_json(summary_path)
    _verify_commit(payload, label="Final-test Landsat inventory")
    outputs = payload.get("output_files")
    if not isinstance(outputs, dict):
        raise FinalTestInventoryError("Existing final-test inventory lacks file locks.")
    for record in outputs.values():
        path = Path(str(record.get("path", ""))).resolve()
        if (
            not path.is_file()
            or sha256_file(path) != record.get("sha256")
            or path.stat().st_size != record.get("bytes")
        ):
            raise FinalTestInventoryError("Existing final-test inventory file drifted.")
    return payload


def build_final_test_inventory_artifacts(
    *,
    config_path: str | Path = "configs/research.toml",
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    development_inventory_directory: str | Path = "manifests/target_inventory",
    primary_tract_path: str | Path = (
        "data/interim/targets/primary_tract_manifest.parquet"
    ),
    target_progress_path: str | Path = "data/interim/targets/build_progress.json",
    output_directory: str | Path = "manifests/final_test_2025/landsat_inventory",
    client: _StacClient | None = None,
) -> dict[str, Any]:
    """Freeze target-blind 2025 metadata and its tract-date key universe."""

    root = Path(__file__).resolve().parents[2]

    def resolve(value: str | Path) -> Path:
        candidate = Path(value)
        return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    output = resolve(output_directory)
    existing = _existing_summary(output)
    if existing is not None:
        return existing
    if output.exists() and any(output.iterdir()):
        raise FinalTestInventoryError(
            "Partial final-test inventory exists without a valid commit marker."
        )

    formal_lock, formal_lock_sha256 = authenticate_formal_model_lock(
        resolve(formal_lock_path)
    )
    config = load_config(resolve(config_path))
    if config.final_test_year != FINAL_TEST_YEAR or config.final_test_unlocked:
        raise FinalTestInventoryError(
            "Target-blind inventory requires the development config to remain locked."
        )
    development = resolve(development_inventory_directory)
    development_summary_path = development / "inventory_summary.json"
    development_summary = _read_json(development_summary_path)
    city_path = development / "city_boundary.geojson"
    if sha256_file(city_path) != development_summary.get("city_boundary_file_sha256"):
        raise FinalTestInventoryError("Frozen city boundary byte lock failed.")
    city = gpd.read_file(city_path)
    city_hash = geometry_semantic_sha256(city)
    if city_hash != development_summary.get("city_boundary_geometry_sha256"):
        raise FinalTestInventoryError("Frozen city boundary geometry lock failed.")

    tracts_path = resolve(primary_tract_path)
    tracts_sha256 = sha256_file(tracts_path)
    primary_tracts = gpd.read_parquet(tracts_path)
    progress_path = resolve(target_progress_path)
    development_progress = _read_json(progress_path)
    tract_commit = _tract_manifest_commit(primary_tracts)
    if tract_commit != development_progress.get("tract_manifest_sha256"):
        raise FinalTestInventoryError("Frozen primary tract semantic lock failed.")

    if client is None:
        from pystac_client import Client

        client = Client.open(config.raw["landsat"]["stac_api"])
    scenes, overpasses = discover_final_test_scenes(
        client,
        config=config,
        city_boundary=city,
    )
    scenes_table = _scene_frame(scenes)
    overpasses_table = _overpass_frame(
        overpasses,
        scene_frame=scenes_table,
        city_geometry_sha256=city_hash,
        minimum_city_coverage=float(
            config.raw["landsat"]["minimum_city_union_coverage_fraction"]
        ),
    )
    primary = overpasses_table.loc[overpasses_table["primary_eligible"]].copy()
    if primary.empty or primary["local_date"].duplicated().any():
        raise FinalTestInventoryError(
            "No unique full-city 2025 primary overpass cohort could be frozen."
        )
    keys = build_target_blind_key_universe(primary_tracts, primary)

    scene_path = output / SCENE_FILENAME
    overpass_path = output / OVERPASS_FILENAME
    primary_path = output / PRIMARY_FILENAME
    keys_path = output / KEY_UNIVERSE_FILENAME
    atomic_csv(scenes_table, scene_path)
    atomic_csv(overpasses_table, overpass_path)
    atomic_csv(primary, primary_path)
    atomic_parquet(keys, keys_path)
    pipeline_sha256, pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    outputs = {
        SCENE_FILENAME: {
            "path": str(scene_path),
            "sha256": sha256_file(scene_path),
            "bytes": scene_path.stat().st_size,
            "rows": len(scenes_table),
        },
        OVERPASS_FILENAME: {
            "path": str(overpass_path),
            "sha256": sha256_file(overpass_path),
            "bytes": overpass_path.stat().st_size,
            "rows": len(overpasses_table),
        },
        PRIMARY_FILENAME: {
            "path": str(primary_path),
            "sha256": sha256_file(primary_path),
            "bytes": primary_path.stat().st_size,
            "rows": len(primary),
        },
        KEY_UNIVERSE_FILENAME: {
            "path": str(keys_path),
            **parquet_file_record(keys_path, keys),
        },
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "target_blind_inventory_frozen",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "final_test_year": FINAL_TEST_YEAR,
        "date_range": [FINAL_TEST_START.isoformat(), FINAL_TEST_END.isoformat()],
        "target_blind": True,
        "target_assets_opened": False,
        "target_or_qa_values_read": False,
        "labels_created": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "global_scene_cloud_cover_filter": False,
        "scene_count": len(scenes_table),
        "physical_overpass_count": len(overpasses_table),
        "primary_overpass_count": len(primary),
        "tract_count": int(keys["tract_geoid"].nunique()),
        "key_count": len(keys),
        "formal_model_lock": {
            "path": str(resolve(formal_lock_path)),
            "sha256": formal_lock_sha256,
            "commit_sha256": formal_lock["commit_sha256"],
        },
        "frozen_support": {
            "city_boundary_path": str(city_path),
            "city_boundary_sha256": sha256_file(city_path),
            "city_boundary_geometry_sha256": city_hash,
            "primary_tract_path": str(tracts_path),
            "primary_tract_sha256": tracts_sha256,
            "primary_tract_commit_sha256": tract_commit,
            "tract_count": len(primary_tracts),
        },
        "source_records": {
            "development_inventory_summary": {
                "path": str(development_summary_path),
                "sha256": sha256_file(development_summary_path),
            },
            "development_target_progress": {
                "path": str(progress_path),
                "sha256": sha256_file(progress_path),
            },
            "research_config": {
                "path": str(config.path),
                "sha256": sha256_file(config.path),
            },
        },
        "semantic_hashes": {
            "scenes": canonical_frame_sha256(scenes_table, sort_by=["item_id"]),
            "overpasses": canonical_frame_sha256(
                overpasses_table, sort_by=["local_date", "overpass_id"]
            ),
            "primary_overpasses": canonical_frame_sha256(
                primary, sort_by=["local_date", "overpass_id"]
            ),
            "key_universe": canonical_frame_sha256(
                keys, sort_by=["target_date", "tract_geoid"]
            ),
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline,
        "output_files": outputs,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, output / SUMMARY_FILENAME)
    return payload
