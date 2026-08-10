"""Freeze target-blind Sentinel-2 metadata for the four-city predictor build.

The source-footprint stage already froze exact Planetary Computer item IDs for
the three external cities.  This module hydrates only those IDs, selects one
reprocessing cohort per physical acquisition, and records d-60 through d-1
membership in each city's civil timezone.  It never opens a raster asset.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import requests
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from la_heat.multicity.portable_predictor_components import load_city_support
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_text,
    canonical_frame_sha256,
    canonical_sha256,
    sha256_file,
)
from la_heat.sentinel_inventory import (
    REQUIRED_SENTINEL_ASSETS,
    CohortSelection,
    PhysicalAcquisitionKey,
    SentinelItemRecord,
    canonical_asset_url,
    canonical_processing_baseline,
    canonical_stac_item_snapshot,
    normalize_datatake_id,
    physical_acquisition_key,
    select_all_reprocessing_cohorts,
    sentinel_inventory_semantic_sha256,
    sentinel_record_from_item,
)

CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
STAC_API: Final = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
STAC_COLLECTION: Final = "sentinel-2-l2a"
WINDOW_START_DAYS: Final = 60
WINDOW_END_DAYS: Final = 1
ALGORITHM_VERSION: Final = "portable-sentinel-inventory-v1"
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/sentinel_inventory"
)
RAW_STAC_ROOT: Final = Path(
    "data/raw/multicity/portable_predictors/sentinel_stac"
)
PREDICTOR_KEY_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/inventory"
)
COMPLETE_FILENAME: Final = "INVENTORY_COMPLETE.json"

_SOURCE_MANIFESTS: Final = {
    "phoenix_az": Path(
        "manifests/multicity/cities/phoenix_az/source_footprints/"
        "PORTABLE_SOURCE_FOOTPRINT.json"
    ),
    "houston_tx": Path(
        "manifests/multicity/cities/houston_tx/source_footprints/"
        "SOURCE_FOOTPRINTS.json"
    ),
    "chicago_il": Path(
        "manifests/multicity/cities/chicago_il/source_footprints/"
        "SOURCE_FOOTPRINTS.json"
    ),
}


class PortableSentinelInventoryError(ValueError):
    """Raised when frozen Sentinel metadata cannot be reproduced."""


class ExactItemClient(Protocol):
    """Minimal injectable client used by the metadata-only build."""

    def fetch_exact_items(self, item_ids: Sequence[str]) -> tuple[dict[str, Any], ...]:
        """Return one full STAC feature for every requested ID."""


@dataclass(frozen=True, slots=True)
class CityInventorySpec:
    city_id: str
    timezone: str
    analysis_crs: str
    target_dates: tuple[date, ...]


@dataclass(frozen=True, order=True, slots=True)
class CityWindowMembership:
    target_date: date
    acquisition_key: PhysicalAcquisitionKey
    acquisition_local_date: date
    lag_days: int


class PlanetaryComputerExactItemClient:
    """Small POST client for exact, unsigned STAC metadata lookup."""

    def __init__(
        self,
        endpoint: str = STAC_API,
        *,
        timeout_seconds: float = 60.0,
        attempts: int = 3,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.session = requests.Session()

    def fetch_exact_items(self, item_ids: Sequence[str]) -> tuple[dict[str, Any], ...]:
        requested = tuple(dict.fromkeys(str(value) for value in item_ids))
        if not requested:
            return ()
        payload = {
            "collections": [STAC_COLLECTION],
            "ids": list(requested),
            "limit": len(requested),
        }
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.session.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                features = body.get("features")
                if not isinstance(features, list):
                    raise PortableSentinelInventoryError(
                        "Planetary Computer response has no feature list."
                    )
                by_id = {str(feature.get("id")): feature for feature in features}
                if set(by_id) != set(requested) or len(features) != len(by_id):
                    missing = sorted(set(requested) - set(by_id))
                    extra = sorted(set(by_id) - set(requested))
                    raise PortableSentinelInventoryError(
                        f"Exact STAC lookup disagreed with frozen IDs; "
                        f"missing={missing[:3]}, extra={extra[:3]}."
                    )
                return tuple(by_id[item_id] for item_id in requested)
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    time.sleep(float(attempt))
        raise PortableSentinelInventoryError(
            f"Planetary Computer exact metadata lookup failed: {last_error}"
        ) from last_error


def _project_root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _committed_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortableSentinelInventoryError(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise PortableSentinelInventoryError(f"{label} commit changed: {path}")
    return payload


def _load_city_spec(root: Path, city_id: str) -> CityInventorySpec:
    if city_id not in CITY_IDS:
        raise PortableSentinelInventoryError(f"Unknown portable city: {city_id}")
    config_path = root / f"configs/multicity/cities/{city_id}.toml"
    with config_path.open("rb") as handle:
        city = tomllib.load(handle)["city"]
    if city.get("id") != city_id:
        raise PortableSentinelInventoryError(f"City config ID changed for {city_id}.")
    timezone = str(city["timezone"])
    ZoneInfo(timezone)
    key_path = root / PREDICTOR_KEY_ROOT / city_id / "predictor_keys.parquet"
    keys = pd.read_parquet(key_path, columns=["city_id", "target_date"])
    if keys.empty or set(keys["city_id"].astype(str)) != {city_id}:
        raise PortableSentinelInventoryError(f"Predictor keys changed for {city_id}.")
    targets = tuple(sorted(pd.to_datetime(keys["target_date"]).dt.date.unique()))
    return CityInventorySpec(
        city_id=city_id,
        timezone=timezone,
        analysis_crs=str(city["target_grid_crs"]),
        target_dates=targets,
    )


def acquisition_local_date(value: datetime | str, timezone: str) -> date:
    """Convert an aware acquisition timestamp to one city's civil date."""

    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PortableSentinelInventoryError("Acquisition datetime must be aware.")
    return parsed.astimezone(ZoneInfo(timezone)).date()


def build_city_window_membership(
    target_dates: Iterable[date],
    acquisitions: Iterable[PhysicalAcquisitionKey | CohortSelection],
    *,
    timezone: str,
) -> tuple[CityWindowMembership, ...]:
    """Build deterministic d-60:d-1 membership in a city's timezone."""

    targets = tuple(sorted(set(target_dates)))
    keys = {
        value.acquisition_key if isinstance(value, CohortSelection) else value
        for value in acquisitions
    }
    memberships: list[CityWindowMembership] = []
    for target in targets:
        for key in sorted(keys):
            local_date = acquisition_local_date(key.acquired_utc, timezone)
            lag = (target - local_date).days
            if WINDOW_END_DAYS <= lag <= WINDOW_START_DAYS:
                memberships.append(
                    CityWindowMembership(target, key, local_date, lag)
                )
    return tuple(memberships)


def _in_any_target_window(acquired: date, targets: Sequence[date]) -> bool:
    return any(
        WINDOW_END_DAYS <= (target - acquired).days <= WINDOW_START_DAYS
        for target in targets
    )


def _geometry_sha256(geometry: BaseGeometry) -> str:
    return canonical_sha256(
        shapely.to_wkb(
            shapely.normalize(geometry),
            hex=True,
            output_dimension=2,
            byte_order=1,
            include_srid=False,
        )
    )


def _external_candidate_source(
    root: Path,
    spec: CityInventorySpec,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    manifest_path = root / _SOURCE_MANIFESTS[spec.city_id]
    manifest = _committed_json(manifest_path, label=f"{spec.city_id} source footprint")
    record = manifest["output_tables"]["sentinel_items"]
    table_path = root / str(record["path"])
    if not table_path.is_file() or sha256_file(table_path) != record["sha256"]:
        raise PortableSentinelInventoryError(
            f"Frozen Sentinel source table changed for {spec.city_id}."
        )
    source = gpd.read_parquet(table_path)
    local_dates = pd.to_datetime(source["acquisition_local_date"]).dt.date
    recomputed = source["acquired_utc"].map(
        lambda value: acquisition_local_date(str(value), spec.timezone)
    )
    if not np.array_equal(local_dates.to_numpy(), recomputed.to_numpy()):
        raise PortableSentinelInventoryError(
            f"Frozen Sentinel local dates changed for {spec.city_id}."
        )
    keep = recomputed.map(
        lambda value: _in_any_target_window(value, spec.target_dates)
    )
    source = source.loc[keep].copy().reset_index(drop=True)
    if source.empty or source["item_id"].duplicated().any():
        raise PortableSentinelInventoryError(
            f"No unique Sentinel candidates survived for {spec.city_id}."
        )
    return source, {
        "path": _relative(root, table_path),
        "sha256": sha256_file(table_path),
        "source_manifest": _relative(root, manifest_path),
        "source_manifest_commit_sha256": manifest["commit_sha256"],
    }


def _snapshot_filename(item_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("._") or "item"
    suffix = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:120]}-{suffix}.json"


def _snapshot_text(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(
        snapshot,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _snapshot_record(
    raw_directory: Path,
    feature: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = canonical_stac_item_snapshot(feature)
    item_id = str(snapshot["id"])
    text = _snapshot_text(snapshot)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    filename = _snapshot_filename(item_id)
    path = raw_directory / filename
    if path.is_file():
        if sha256_file(path) != digest:
            raise PortableSentinelInventoryError(
                f"Cached STAC snapshot changed for {item_id}."
            )
    else:
        atomic_text(text, path)
    return dict(snapshot), {
        "item_id": item_id,
        "filename": filename,
        "sha256": digest,
        "bytes": path.stat().st_size,
    }


def _cached_feature(raw_directory: Path, item_id: str) -> dict[str, Any] | None:
    path = raw_directory / _snapshot_filename(item_id)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("id")) != item_id:
        raise PortableSentinelInventoryError(f"Cached STAC ID changed: {path}")
    return payload


def _hydrate_exact_features(
    item_ids: Sequence[str],
    *,
    client: ExactItemClient,
    raw_directory: Path,
    batch_size: int,
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for item_id in sorted(set(item_ids)):
        cached = _cached_feature(raw_directory, item_id)
        if cached is None:
            missing.append(item_id)
        else:
            by_id[item_id] = cached
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        for feature in client.fetch_exact_items(batch):
            snapshot, _ = _snapshot_record(raw_directory, feature)
            by_id[str(snapshot["id"])] = snapshot
    if set(by_id) != set(item_ids):
        raise PortableSentinelInventoryError("Hydrated STAC IDs are incomplete.")
    return tuple(by_id[item_id] for item_id in sorted(by_id))


def _los_angeles_source(
    root: Path,
    spec: CityInventorySpec,
    raw_directory: Path,
) -> tuple[gpd.GeoDataFrame, tuple[dict[str, Any], ...], dict[str, Any]]:
    summary_path = root / "manifests/sentinel_inventory/inventory_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    source_raw = root / "data/raw/sentinel/stac_items"
    features: list[dict[str, Any]] = []
    records = summary["raw_stac_snapshots"]["files"]
    for record in records:
        path = source_raw / str(record["filename"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise PortableSentinelInventoryError("Los Angeles STAC freeze changed.")
        feature = json.loads(path.read_text(encoding="utf-8"))
        snapshot, _ = _snapshot_record(raw_directory, feature)
        features.append(snapshot)
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    for feature in features:
        acquired = str(feature["properties"]["datetime"])
        local_date = acquisition_local_date(acquired, spec.timezone)
        if not _in_any_target_window(local_date, spec.target_dates):
            continue
        geometry = shape(feature["geometry"])
        rows.append(
            {
                "item_id": str(feature["id"]),
                "platform": str(feature["properties"]["platform"]).lower(),
                "acquired_utc": acquired,
                "acquisition_local_date": local_date.isoformat(),
                "mgrs_tile": str(feature["properties"]["s2:mgrs_tile"]).upper(),
                "geometry_sha256": _geometry_sha256(geometry),
            }
        )
        geometries.append(geometry)
    source = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    selected_dates = set(
        pd.read_csv(
            root / "manifests/sentinel_inventory/target_window_membership.csv"
        )["target_date"].astype(str)
    )
    if selected_dates != {value.isoformat() for value in spec.target_dates}:
        raise PortableSentinelInventoryError(
            "Los Angeles target dates disagree with the portable predictor keys."
        )
    return source, tuple(features), {
        "path": _relative(root, summary_path),
        "sha256": sha256_file(summary_path),
        "source_mode": "reused_frozen_los_angeles_inventory",
    }


def eligible_tile_overlap_counts(
    items: gpd.GeoDataFrame,
    *,
    eligible_mask: np.ndarray,
    transform: Any,
    support_crs: str,
) -> dict[str, int]:
    """Count canonical eligible-cell centres inside each unique MGRS tile."""

    rows, columns = np.nonzero(eligible_mask)
    xs = transform.c + (columns.astype(float) + 0.5) * transform.a
    ys = transform.f + (rows.astype(float) + 0.5) * transform.e
    unique = items.sort_values(["mgrs_tile", "item_id"]).drop_duplicates("mgrs_tile")
    projected = unique.to_crs(support_crs)
    return {
        str(tile): int(shapely.contains_xy(geometry, xs, ys).sum())
        for tile, geometry in zip(
            projected["mgrs_tile"].astype(str), projected.geometry, strict=True
        )
    }


def _canonical_city_aoi(root: Path, support: Any) -> BaseGeometry:
    record = support.geography_manifest["output_tables"]["city_boundary"]
    path = root / str(record["path"])
    if not path.is_file() or sha256_file(path) != record["sha256"]:
        raise PortableSentinelInventoryError(
            f"Canonical city boundary changed for {support.city_id}."
        )
    boundary = gpd.read_parquet(path)
    if boundary.empty or boundary.crs is None:
        raise PortableSentinelInventoryError(
            f"Canonical city boundary is invalid for {support.city_id}."
        )
    aoi = boundary.to_crs("EPSG:4326").geometry.union_all()
    if aoi.is_empty or not aoi.is_valid:
        raise PortableSentinelInventoryError(
            f"Canonical city AOI is invalid for {support.city_id}."
        )
    return aoi


def _verify_hydrated_source(
    source: gpd.GeoDataFrame,
    features: Sequence[Mapping[str, Any]],
    spec: CityInventorySpec,
) -> dict[str, SentinelItemRecord]:
    source_by_id = source.set_index("item_id", drop=False)
    records: dict[str, SentinelItemRecord] = {}
    for feature in features:
        item = pystac.Item.from_dict(dict(feature), preserve_dict=False)
        record = sentinel_record_from_item(item)
        row = source_by_id.loc[record.item_id]
        if (
            record.mgrs_tile != str(row["mgrs_tile"]).upper()
            or acquisition_local_date(record.acquired_utc, spec.timezone).isoformat()
            != str(row["acquisition_local_date"])
            or _geometry_sha256(record.geometry_wgs84) != str(row["geometry_sha256"])
        ):
            raise PortableSentinelInventoryError(
                f"Hydrated metadata disagrees with frozen candidate {record.item_id}."
            )
        records[record.item_id] = record
    if set(records) != set(source["item_id"].astype(str)):
        raise PortableSentinelInventoryError("Hydrated candidate set changed.")
    return records


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _asset_column(asset: str) -> str:
    return "asset_" + re.sub(r"[^a-z0-9]+", "_", asset.lower()).strip("_") + "_href"


def _selected_acquisitions_frame(
    city_id: str,
    timezone: str,
    selections: Sequence[CohortSelection],
) -> pd.DataFrame:
    rows = []
    for selection in sorted(selections, key=lambda value: value.acquisition_key):
        key = selection.acquisition_key
        rows.append(
            {
                "city_id": city_id,
                "physical_acquisition_id": key.semantic_id,
                "platform": key.platform,
                "acquired_utc": _utc_text(key.acquired_utc),
                "acquisition_local_date": acquisition_local_date(
                    key.acquired_utc, timezone
                ).isoformat(),
                "relative_orbit": key.relative_orbit,
                "normalized_datatake_id": key.normalized_datatake_id,
                "processing_baseline": canonical_processing_baseline(
                    selection.processing_baseline
                ),
                "generation_time": _utc_text(selection.generation_time),
                "union_city_coverage_fraction": selection.union_aoi_coverage_fraction,
                "item_count": len(selection.items),
                "item_ids": "|".join(sorted(selection.item_ids)),
                "mgrs_tiles": "|".join(
                    sorted({item.mgrs_tile for item in selection.items})
                ),
            }
        )
    return pd.DataFrame(rows)


def _selected_items_frame(
    city_id: str,
    timezone: str,
    selections: Sequence[CohortSelection],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for selection in sorted(selections, key=lambda value: value.acquisition_key):
        key = selection.acquisition_key
        for item in sorted(selection.items, key=lambda value: (value.mgrs_tile, value.item_id)):
            snapshot = snapshots[item.item_id]
            assets = dict(item.asset_hrefs)
            row: dict[str, Any] = {
                "city_id": city_id,
                "physical_acquisition_id": key.semantic_id,
                "item_id": item.item_id,
                "platform": item.platform.lower(),
                "acquired_utc": _utc_text(item.acquired_utc),
                "acquisition_local_date": acquisition_local_date(
                    item.acquired_utc, timezone
                ).isoformat(),
                "relative_orbit": str(item.relative_orbit),
                "datatake_id": item.datatake_id.upper(),
                "normalized_datatake_id": normalize_datatake_id(item.datatake_id),
                "mgrs_tile": item.mgrs_tile,
                "processing_baseline": canonical_processing_baseline(
                    item.processing_baseline
                ),
                "generation_time": _utc_text(item.generation_time),
                "cloud_cover_percent_audit_only": item.cloud_cover_percent,
                "geometry_wkb_hex": str(
                    shapely.to_wkb(
                        shapely.normalize(item.geometry_wgs84),
                        hex=True,
                        output_dimension=2,
                        byte_order=1,
                        include_srid=False,
                    )
                ),
                "snapshot_filename": snapshot["filename"],
                "snapshot_sha256": snapshot["sha256"],
            }
            row.update(
                {
                    _asset_column(asset): canonical_asset_url(assets[asset])
                    for asset in REQUIRED_SENTINEL_ASSETS
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _membership_frame(
    city_id: str,
    memberships: Sequence[CityWindowMembership],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "city_id": city_id,
                "target_date": value.target_date.isoformat(),
                "physical_acquisition_id": value.acquisition_key.semantic_id,
                "acquisition_local_date": value.acquisition_local_date.isoformat(),
                "lag_days": value.lag_days,
            }
            for value in memberships
        ]
    )


def _file_record(root: Path, path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def _candidate_audit_frame(
    source: gpd.GeoDataFrame,
    records: Mapping[str, SentinelItemRecord],
    *,
    city_id: str,
    overlap_counts: Mapping[str, int],
    selected_ids: set[str],
    snapshot_records: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    rows = []
    for source_row in source.sort_values("item_id").itertuples(index=False):
        item_id = str(source_row.item_id)
        record = records[item_id]
        overlap = int(overlap_counts[record.mgrs_tile])
        rows.append(
            {
                "city_id": city_id,
                "item_id": item_id,
                "platform": record.platform.lower(),
                "acquired_utc": _utc_text(record.acquired_utc),
                "acquisition_local_date": str(source_row.acquisition_local_date),
                "mgrs_tile": record.mgrs_tile,
                "physical_acquisition_id": physical_acquisition_key(record).semantic_id,
                "processing_baseline": record.processing_baseline,
                "eligible_cell_overlap": overlap,
                "raster_contributor": overlap > 0,
                "selected_for_processing": item_id in selected_ids,
                "exclusion_reason": "" if overlap > 0 else "zero_eligible_cell_overlap",
                "snapshot_filename": snapshot_records[item_id]["filename"],
                "snapshot_sha256": snapshot_records[item_id]["sha256"],
            }
        )
    return pd.DataFrame(rows)


def authenticate_portable_sentinel_inventory(
    project_root: str | Path,
    city_id: str,
) -> dict[str, Any]:
    """Authenticate one completed metadata inventory without opening rasters."""

    root = _project_root(project_root)
    marker = root / OUTPUT_ROOT / city_id / COMPLETE_FILENAME
    payload = _committed_json(marker, label=f"{city_id} portable Sentinel inventory")
    if payload.get("state") != "complete" or payload.get("city_id") != city_id:
        raise PortableSentinelInventoryError(f"Incomplete Sentinel inventory: {city_id}")
    for record in payload["outputs"].values():
        path = root / str(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise PortableSentinelInventoryError(
                f"Portable Sentinel output changed: {path}"
            )
    return payload


def build_portable_sentinel_inventory(
    project_root: str | Path,
    city_id: str,
    *,
    client: ExactItemClient | None = None,
    batch_size: int = 50,
) -> dict[str, Any]:
    """Build or resume one city's raster-free Sentinel metadata inventory."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    root = _project_root(project_root)
    marker = root / OUTPUT_ROOT / city_id / COMPLETE_FILENAME
    if marker.is_file():
        return authenticate_portable_sentinel_inventory(root, city_id)

    spec = _load_city_spec(root, city_id)
    support = load_city_support(root, city_id)
    if support.crs != spec.analysis_crs:
        raise PortableSentinelInventoryError(f"Canonical CRS changed for {city_id}.")
    raw_directory = root / RAW_STAC_ROOT / city_id
    if city_id == "los_angeles_ca":
        source, features, source_record = _los_angeles_source(
            root, spec, raw_directory
        )
    else:
        source, source_record = _external_candidate_source(root, spec)
        exact_client = client or PlanetaryComputerExactItemClient()
        features = _hydrate_exact_features(
            tuple(source["item_id"].astype(str)),
            client=exact_client,
            raw_directory=raw_directory,
            batch_size=batch_size,
        )

    records = _verify_hydrated_source(source, features, spec)
    overlap_counts = eligible_tile_overlap_counts(
        source,
        eligible_mask=support.eligible_land,
        transform=support.transform,
        support_crs=support.crs,
    )
    contributor_records = [
        record
        for record in records.values()
        if overlap_counts[record.mgrs_tile] > 0
    ]
    if not contributor_records:
        raise PortableSentinelInventoryError(
            f"No Sentinel item overlaps eligible support for {city_id}."
        )
    aoi = _canonical_city_aoi(root, support)
    candidate_selections = select_all_reprocessing_cohorts(
        contributor_records,
        aoi_geometry_wgs84=aoi,
        analysis_crs=support.crs,
    )
    memberships = build_city_window_membership(
        spec.target_dates,
        candidate_selections,
        timezone=spec.timezone,
    )
    retained = {value.acquisition_key for value in memberships}
    selections = tuple(
        value for value in candidate_selections if value.acquisition_key in retained
    )
    selected_ids = {
        item.item_id for selection in selections for item in selection.items
    }

    snapshot_records: dict[str, dict[str, Any]] = {}
    for feature in features:
        _, record = _snapshot_record(raw_directory, feature)
        snapshot_records[str(feature["id"])] = record
    acquisitions = _selected_acquisitions_frame(city_id, spec.timezone, selections)
    items = _selected_items_frame(
        city_id, spec.timezone, selections, snapshot_records
    )
    membership = _membership_frame(city_id, memberships)
    candidate_audit = _candidate_audit_frame(
        source,
        records,
        city_id=city_id,
        overlap_counts=overlap_counts,
        selected_ids=selected_ids,
        snapshot_records=snapshot_records,
    )

    output = root / OUTPUT_ROOT / city_id
    paths = {
        "selected_acquisitions": output / "selected_acquisitions.csv",
        "selected_items": output / "selected_items.csv",
        "target_window_membership": output / "target_window_membership.csv",
        "candidate_items": output / "candidate_items.csv",
    }
    frames = {
        "selected_acquisitions": acquisitions,
        "selected_items": items,
        "target_window_membership": membership,
        "candidate_items": candidate_audit,
    }
    for name, path in paths.items():
        atomic_csv(frames[name], path)

    snapshots = [snapshot_records[item_id] for item_id in sorted(snapshot_records)]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "city_id": city_id,
        "collection": STAC_COLLECTION,
        "provider": "Microsoft Planetary Computer",
        "local_timezone": spec.timezone,
        "window_start_days_before_target": WINDOW_START_DAYS,
        "window_end_days_before_target": WINDOW_END_DAYS,
        "global_scene_cloud_cover_filter": None,
        "source": source_record,
        "target_dates": {
            "count": len(spec.target_dates),
            "minimum": spec.target_dates[0].isoformat(),
            "maximum": spec.target_dates[-1].isoformat(),
        },
        "counts": {
            "frozen_candidate_items": len(source),
            "zero_support_candidate_items": int(
                (~candidate_audit["raster_contributor"]).sum()
            ),
            "selected_physical_acquisitions": len(acquisitions),
            "selected_items": len(items),
            "target_window_memberships": len(membership),
        },
        "eligible_cell_overlap_by_mgrs_tile": {
            key: int(value) for key, value in sorted(overlap_counts.items())
        },
        "sentinel_inventory_semantic_sha256": sentinel_inventory_semantic_sha256(
            selections
        ),
        "membership_semantic_sha256": canonical_frame_sha256(
            membership,
            sort_by=["city_id", "target_date", "physical_acquisition_id"],
        ),
        "outputs": {
            name: _file_record(root, paths[name], frames[name]) for name in paths
        },
        "raw_stac_snapshots": {
            "directory": _relative(root, raw_directory),
            "count": len(snapshots),
            "set_sha256": canonical_sha256(snapshots),
            "files": snapshots,
        },
        "target_or_qa_values_read": False,
        "raster_assets_opened": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload


def build_all_portable_sentinel_inventories(
    project_root: str | Path,
    *,
    client: ExactItemClient | None = None,
    batch_size: int = 50,
) -> dict[str, Any]:
    """Build all four city inventories and return their completion summaries."""

    return {
        city_id: build_portable_sentinel_inventory(
            project_root,
            city_id,
            client=client,
            batch_size=batch_size,
        )
        for city_id in CITY_IDS
    }
