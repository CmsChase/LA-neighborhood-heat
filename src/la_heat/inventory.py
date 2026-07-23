"""Build a complete, target-blind Landsat development-scene inventory."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pystac import Item
from pystac_client import Client

from la_heat.boundaries import fetch_city_boundary
from la_heat.config import ResearchConfig, load_config
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_text,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    sha256_file,
)
from la_heat.stage_config import inventory_config_payload, inventory_config_sha256

REQUIRED_ASSETS = ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat")
INVENTORY_SCHEMA_VERSION = "3"
INVENTORY_ALGORITHM_VERSION = "inventory-v3-versioned-stage-config"
INVENTORY_PIPELINE_FILES = (
    "pyproject.toml",
    "src/la_heat/boundaries.py",
    "src/la_heat/config.py",
    "src/la_heat/inventory.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
)
SCENE_CSV_DTYPES = {
    "item_id": str,
    "platform": str,
    "acquired_utc": str,
    "local_date": str,
    "wrs_path": str,
    "wrs_row": str,
    **{f"{asset}_href": str for asset in REQUIRED_ASSETS},
}
OVERPASS_CSV_DTYPES = {
    "overpass_id": str,
    "platform": str,
    "local_date": str,
    "acquired_utc_min": str,
    "acquired_utc_max": str,
    "scene_ids": str,
    "wrs_path_rows": str,
    "source_lock_sha256": str,
}


def read_scene_inventory(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=SCENE_CSV_DTYPES)


def read_overpass_inventory(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=OVERPASS_CSV_DTYPES)


@dataclass(frozen=True)
class SceneRecord:
    item_id: str
    platform: str
    acquired_utc: datetime
    local_date: str
    wrs_path: str
    wrs_row: str
    cloud_cover_percent: float
    city_coverage_fraction: float
    geometry_wgs84: object
    asset_hrefs: dict[str, str]


@dataclass(frozen=True)
class OverpassRecord:
    overpass_id: str
    platform: str
    local_date: str
    acquired_utc_min: str
    acquired_utc_max: str
    scene_ids: tuple[str, ...]
    wrs_path_rows: tuple[str, ...]
    scene_count: int
    union_city_coverage_fraction: float
    ambiguous_local_date: bool


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def scene_is_eligible(
    item: Item,
    *,
    allowed_platforms: set[str],
    required_assets: tuple[str, ...] = REQUIRED_ASSETS,
) -> bool:
    """Apply product and sensor rules without consulting target values."""

    properties = item.properties
    return bool(
        properties.get("platform") in allowed_platforms
        and properties.get("landsat:collection_category") == "T1"
        and properties.get("landsat:correction") == "L2SP"
        and all(asset in item.assets for asset in required_assets)
    )


def group_physical_overpasses(
    scenes: list[SceneRecord],
    *,
    city_geometry_wgs84: object,
    analysis_crs: str,
    maximum_time_gap_minutes: int,
) -> list[OverpassRecord]:
    """Group adjacent path/row scenes while failing closed on same-date ambiguity."""

    if maximum_time_gap_minutes <= 0:
        raise ValueError("Maximum overpass time gap must be positive.")
    def adjacent(first: SceneRecord, second: SceneRecord) -> bool:
        try:
            path_gap = abs(int(first.wrs_path) - int(second.wrs_path))
            row_gap = abs(int(first.wrs_row) - int(second.wrs_row))
        except ValueError:
            return False
        return path_gap <= 1 and row_gap <= 1

    grouped: list[list[SceneRecord]] = []
    for platform_date, same_date in pd.DataFrame(
        {
            "platform": [scene.platform for scene in scenes],
            "local_date": [scene.local_date for scene in scenes],
            "scene": scenes,
        }
    ).groupby(["platform", "local_date"], sort=True):
        del platform_date
        ordered = sorted(same_date["scene"].tolist(), key=lambda scene: scene.acquired_utc)
        current: list[SceneRecord] = []
        for scene in ordered:
            outside_time_span = bool(
                current
                and scene.acquired_utc - current[0].acquired_utc
                > timedelta(minutes=maximum_time_gap_minutes)
            )
            disconnected_wrs = bool(
                current and not any(adjacent(scene, member) for member in current)
            )
            if current and (outside_time_span or disconnected_wrs):
                grouped.append(current)
                current = []
            current.append(scene)
        if current:
            grouped.append(current)

    local_date_counts: dict[str, int] = {}
    for group in grouped:
        local_date_counts[group[0].local_date] = (
            local_date_counts.get(group[0].local_date, 0) + 1
        )

    city = gpd.GeoSeries([city_geometry_wgs84], crs="EPSG:4326").to_crs(
        analysis_crs
    ).iloc[0]
    records: list[OverpassRecord] = []
    for group in grouped:
        group = sorted(group, key=lambda scene: scene.item_id)
        union_wgs84 = shapely.union_all(
            np.array([scene.geometry_wgs84 for scene in group], dtype=object)
        )
        union_projected = gpd.GeoSeries([union_wgs84], crs="EPSG:4326").to_crs(
            analysis_crs
        ).iloc[0]
        union_coverage = float(union_projected.intersection(city).area / city.area)
        acquired_min = min(scene.acquired_utc for scene in group)
        acquired_max = max(scene.acquired_utc for scene in group)
        platform = group[0].platform
        records.append(
            OverpassRecord(
                overpass_id=f"{platform}_{acquired_min.strftime('%Y%m%dT%H%M%SZ')}",
                platform=platform,
                local_date=group[0].local_date,
                acquired_utc_min=acquired_min.isoformat(),
                acquired_utc_max=acquired_max.isoformat(),
                scene_ids=tuple(scene.item_id for scene in group),
                wrs_path_rows=tuple(
                    sorted({f"{scene.wrs_path}/{scene.wrs_row}" for scene in group})
                ),
                scene_count=len(group),
                union_city_coverage_fraction=union_coverage,
                ambiguous_local_date=local_date_counts[group[0].local_date] > 1,
            )
        )
    return sorted(records, key=lambda record: (record.local_date, record.overpass_id))


def discover_development_inventory(
    config: ResearchConfig,
    city_boundary: gpd.GeoDataFrame,
) -> tuple[list[SceneRecord], list[OverpassRecord]]:
    landsat = config.raw["landsat"]
    study = config.raw["study"]
    client = Client.open(landsat["stac_api"])
    search = client.search(
        collections=[landsat["collection"]],
        bbox=study["bbox_wgs84"],
        datetime=f"{study['start_date']}/{study['development_end_date']}",
        query={
            "landsat:collection_category": {"eq": "T1"},
            "landsat:correction": {"eq": "L2SP"},
        },
    )
    allowed_platforms = set(landsat["sensors"])
    warm_months = set(study["warm_season_months"])
    timezone = ZoneInfo(study["timezone"])
    city = city_boundary.to_crs(study["crs_analysis"]).geometry.union_all()

    records_by_id: dict[str, SceneRecord] = {}
    for item in search.items():
        if not scene_is_eligible(item, allowed_platforms=allowed_platforms):
            continue
        local_date = item.datetime.astimezone(timezone).date()
        if local_date.month not in warm_months:
            continue
        geometry_wgs84 = shapely.geometry.shape(item.geometry)
        projected = gpd.GeoSeries([geometry_wgs84], crs="EPSG:4326").to_crs(
            study["crs_analysis"]
        ).iloc[0]
        coverage = float(projected.intersection(city).area / city.area)
        if coverage <= 0:
            continue
        record = SceneRecord(
            item_id=item.id,
            platform=str(item.properties["platform"]),
            acquired_utc=item.datetime,
            local_date=local_date.isoformat(),
            wrs_path=str(item.properties.get("landsat:wrs_path", "")),
            wrs_row=str(item.properties.get("landsat:wrs_row", "")),
            cloud_cover_percent=float(item.properties.get("eo:cloud_cover", np.nan)),
            city_coverage_fraction=coverage,
            geometry_wgs84=geometry_wgs84,
            asset_hrefs={
                asset: _canonical_url(item.assets[asset].href) for asset in REQUIRED_ASSETS
            },
        )
        previous = records_by_id.get(item.id)
        if previous is not None and previous != record:
            raise ValueError(f"Conflicting duplicate STAC item: {item.id}")
        records_by_id[item.id] = record

    scenes = sorted(records_by_id.values(), key=lambda scene: (scene.acquired_utc, scene.item_id))
    overpasses = group_physical_overpasses(
        scenes,
        city_geometry_wgs84=city_boundary.geometry.union_all(),
        analysis_crs=study["crs_analysis"],
        maximum_time_gap_minutes=landsat["maximum_overpass_span_minutes"],
    )
    return scenes, overpasses


def _config_hash(config: ResearchConfig) -> str:
    return inventory_config_sha256(config)


def run_inventory(config_path: str | Path) -> dict[str, object]:
    config = load_config(config_path)
    city = fetch_city_boundary(config.raw["boundaries"]["la_city_geojson"])
    scenes, overpasses = discover_development_inventory(config, city)

    output = Path("manifests/target_inventory")
    output.mkdir(parents=True, exist_ok=True)
    boundary_path = output / "city_boundary.geojson"
    atomic_text(city.to_json(), boundary_path)
    boundary_semantic_sha256 = geometry_semantic_sha256(city)
    scene_rows: list[dict[str, object]] = []
    for scene in scenes:
        row = asdict(scene)
        row.pop("geometry_wgs84")
        assets = row.pop("asset_hrefs")
        row.update({f"{key}_href": value for key, value in assets.items()})
        row["acquired_utc"] = scene.acquired_utc.isoformat()
        scene_rows.append(row)
    scene_frame = pd.DataFrame(scene_rows).sort_values("item_id").reset_index(drop=True)
    scene_path = output / "scene_inventory.csv"
    atomic_csv(scene_frame, scene_path)
    frozen_scene_frame = read_scene_inventory(scene_path)
    scene_semantic_sha256 = canonical_frame_sha256(
        frozen_scene_frame,
        sort_by=["item_id"],
    )

    overpass_rows: list[dict[str, object]] = []
    for overpass in overpasses:
        row = asdict(overpass)
        row["scene_ids"] = "|".join(overpass.scene_ids)
        row["wrs_path_rows"] = "|".join(overpass.wrs_path_rows)
        overpass_rows.append(row)
    overpass_frame = pd.DataFrame(overpass_rows).sort_values(
        ["local_date", "overpass_id"]
    ).reset_index(drop=True)
    overpass_frame["primary_eligible"] = (
        (
            overpass_frame["union_city_coverage_fraction"]
            >= config.raw["landsat"]["minimum_city_union_coverage_fraction"]
        )
        & ~overpass_frame["ambiguous_local_date"]
    )
    scene_lookup = scene_frame.set_index("item_id", drop=False)
    source_locks: list[str] = []
    for row in overpass_frame.itertuples(index=False):
        scene_ids = str(row.scene_ids).split("|")
        if any(scene_id not in scene_lookup.index for scene_id in scene_ids):
            raise ValueError(f"Overpass {row.overpass_id} references an unknown scene.")
        frozen_scenes = scene_lookup.loc[scene_ids].sort_index()
        source_locks.append(
            canonical_sha256(
                {
                    "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
                    "overpass_id": row.overpass_id,
                    "platform": row.platform,
                    "local_date": row.local_date,
                    "acquired_utc_min": row.acquired_utc_min,
                    "acquired_utc_max": row.acquired_utc_max,
                    "scene_ids": scene_ids,
                    "wrs_path_rows": str(row.wrs_path_rows).split("|"),
                    "union_city_coverage_fraction": row.union_city_coverage_fraction,
                    "city_boundary_geometry_sha256": boundary_semantic_sha256,
                    "scenes": frozen_scenes.to_dict("records"),
                }
            )
        )
    overpass_frame["source_lock_sha256"] = source_locks
    overpass_path = output / "overpass_inventory.csv"
    atomic_csv(overpass_frame, overpass_path)
    frozen_overpass_frame = read_overpass_inventory(overpass_path)
    overpass_semantic_sha256 = canonical_frame_sha256(
        frozen_overpass_frame,
        sort_by=["local_date", "overpass_id"],
    )

    primary_manifest = overpass_frame.loc[overpass_frame["primary_eligible"]].copy()
    primary_manifest_path = output / "primary_overpass_manifest.csv"
    atomic_csv(primary_manifest, primary_manifest_path)
    frozen_primary_manifest = read_overpass_inventory(primary_manifest_path)
    primary_manifest_sha256 = sha256_file(primary_manifest_path)
    primary_manifest_semantic_sha256 = canonical_frame_sha256(
        frozen_primary_manifest,
        sort_by=["local_date", "overpass_id"],
    )

    low_cloud_sensitivity_dates = int(
        scene_frame.loc[
            scene_frame["cloud_cover_percent"]
            < config.raw["landsat"]["pilot_scene_cloud_cover_max_percent"],
            "local_date",
        ].nunique()
    )
    project_root = Path(__file__).resolve().parents[2]
    inventory_pipeline_sha256, inventory_pipeline_payload = (
        code_runtime_fingerprint(
            project_root=project_root,
            relative_paths=INVENTORY_PIPELINE_FILES,
            algorithm_version=INVENTORY_ALGORITHM_VERSION,
        )
    )
    summary = {
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "inventory_config_sha256": _config_hash(config),
        "inventory_config_payload": inventory_config_payload(config),
        "research_config_file_sha256": sha256_file(config.path),
        "inventory_pipeline_sha256": inventory_pipeline_sha256,
        "inventory_pipeline_fingerprint": inventory_pipeline_payload,
        "city_boundary_file_sha256": sha256_file(boundary_path),
        "city_boundary_geometry_sha256": boundary_semantic_sha256,
        "scene_inventory_file_sha256": sha256_file(scene_path),
        "scene_inventory_semantic_sha256": scene_semantic_sha256,
        "overpass_inventory_file_sha256": sha256_file(overpass_path),
        "overpass_inventory_semantic_sha256": overpass_semantic_sha256,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": config.final_test_unlocked,
        "scene_count": int(len(scene_frame)),
        "physical_overpass_count": int(len(overpass_frame)),
        "full_city_coverage_unambiguous_overpass_count": int(len(primary_manifest)),
        "primary_overpass_manifest_sha256": primary_manifest_sha256,
        "primary_overpass_manifest_semantic_sha256": (
            primary_manifest_semantic_sha256
        ),
        "ambiguous_local_date_count": int(
            overpass_frame.loc[overpass_frame["ambiguous_local_date"], "local_date"].nunique()
        ),
        "multi_scene_overpass_count": int((overpass_frame["scene_count"] > 1).sum()),
        "low_scene_cloud_sensitivity_date_count": low_cloud_sensitivity_dates,
        "minimum_required_post_qa_dates": config.raw["study"][
            "minimum_independent_valid_dates"
        ],
        "warning": (
            "This is a target-blind metadata inventory. The independent usable-date "
            "gate is evaluated only after local pixel and tract QA."
        ),
    }
    atomic_json(summary, output / "inventory_summary.json")
    report_path = Path("reports/tables/generated/development_overpass_inventory.csv")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_csv(primary_manifest, report_path)
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    arguments = parser.parse_args()
    run_inventory(arguments.config)


if __name__ == "__main__":
    main()
