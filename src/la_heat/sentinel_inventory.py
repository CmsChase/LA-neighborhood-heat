"""Target-blind Sentinel-2 L2A inventory and temporal-membership contracts.

The module deliberately contains no target values and applies no item-level cloud-cover
cutoff.  It freezes one deterministic processing cohort per physical acquisition before
lagged optical features are constructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import mapping as geometry_mapping
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from la_heat.provenance import atomic_csv, atomic_json, atomic_text, canonical_sha256, sha256_file

SENTINEL_COLLECTION = "sentinel-2-l2a"
SENTINEL_TIMEZONE = ZoneInfo("America/Los_Angeles")
FINAL_TEST_YEAR = 2025
WINDOW_START_DAYS = 60
WINDOW_END_DAYS = 1
INVENTORY_SCHEMA_VERSION = "1"
INVENTORY_ALGORITHM_VERSION = "sentinel-inventory-v1-cohort-coverage-first"

SELECTED_ACQUISITIONS_FILENAME = "selected_acquisitions.csv"
SELECTED_ITEMS_FILENAME = "selected_items.csv"
TARGET_WINDOW_MEMBERSHIP_FILENAME = "target_window_membership.csv"
INVENTORY_SUMMARY_FILENAME = "inventory_summary.json"

REQUIRED_SENTINEL_ASSETS = (
    "B02",
    "B03",
    "B04",
    "B08",
    "B8A",
    "B11",
    "B12",
    "SCL",
    "product-metadata",
)

_DATATAKE_BASELINE_SUFFIX = re.compile(r"_N\d+(?:\.\d+)?$", re.IGNORECASE)
_PROCESSING_BASELINE = re.compile(r"^N?(\d+)\.(\d+)$", re.IGNORECASE)


class _SearchResult(Protocol):
    def items(self) -> Iterable[Any]: ...


class SentinelSearchClient(Protocol):
    def search(self, **kwargs: object) -> _SearchResult: ...


@dataclass(frozen=True, order=True)
class PhysicalAcquisitionKey:
    """Identity shared by adjacent MGRS tiles and reprocessed product versions."""

    platform: str
    acquired_utc: datetime
    relative_orbit: str
    normalized_datatake_id: str

    @property
    def semantic_id(self) -> str:
        return "|".join(
            (
                self.platform,
                _utc_isoformat(self.acquired_utc),
                f"R{self.relative_orbit}",
                self.normalized_datatake_id,
            )
        )


@dataclass(frozen=True)
class SentinelItemRecord:
    """Frozen, unsigned metadata required to select and reproduce one L2A item."""

    item_id: str
    platform: str
    acquired_utc: datetime
    relative_orbit: str
    datatake_id: str
    mgrs_tile: str
    processing_baseline: str
    generation_time: datetime
    geometry_wgs84: BaseGeometry
    asset_hrefs: tuple[tuple[str, str], ...]
    cloud_cover_percent: float | None = None


@dataclass(frozen=True)
class CohortSelection:
    """One coherent processing-baseline cohort for one physical acquisition."""

    acquisition_key: PhysicalAcquisitionKey
    processing_baseline: str
    union_aoi_coverage_fraction: float
    generation_time: datetime
    item_ids: tuple[str, ...]
    items: tuple[SentinelItemRecord, ...]


@dataclass(frozen=True, order=True)
class TargetWindowMembership:
    """Auditable inclusion of one physical acquisition in one target-date window."""

    target_date: date
    acquisition_key: PhysicalAcquisitionKey
    acquisition_local_date: date
    lag_days: int


@dataclass(frozen=True, order=True)
class LocalDateQueryInterval:
    """One merged union interval for target-relative local acquisition dates."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("A Sentinel query interval cannot end before it starts.")

    @property
    def utc_datetime_interval(self) -> str:
        """Return a broad UTC interval whose exact membership is checked later."""

        start_local = datetime.combine(
            self.start_date, time.min, tzinfo=SENTINEL_TIMEZONE
        )
        # STAC datetime intervals are closed.  Query through the following local
        # midnight and then enforce the exact local-date bounds after parsing.
        broad_end_local = datetime.combine(
            self.end_date + timedelta(days=1), time.min, tzinfo=SENTINEL_TIMEZONE
        )
        start_utc = _utc_isoformat(start_local.astimezone(UTC))
        broad_end_utc = _utc_isoformat(broad_end_local.astimezone(UTC))
        return f"{start_utc}/{broad_end_utc}"


@dataclass(frozen=True)
class _CohortCandidate:
    baseline_key: tuple[int, int]
    processing_baseline: str
    coverage_fraction: float
    generation_time: datetime
    item_ids: tuple[str, ...]
    items: tuple[SentinelItemRecord, ...]


def _aware_datetime(value: datetime | str, *, field: str, require_utc: bool) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid ISO datetime: {value!r}") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"{field} must be an aware datetime or ISO datetime string.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware.")
    if require_utc and parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be expressed in UTC.")
    return parsed.astimezone(UTC)


def _utc_isoformat(value: datetime) -> str:
    return _aware_datetime(value, field="datetime", require_utc=True).isoformat().replace(
        "+00:00", "Z"
    )


def _civil_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise TypeError("A target date must be a civil date, not a datetime.")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid target date: {value!r}") from exc
    raise TypeError("A target date must be a date or ISO YYYY-MM-DD string.")


def utc_datetime_to_la_date(value: datetime | str) -> date:
    """Convert an explicitly UTC acquisition timestamp to its Los Angeles civil date."""

    acquired_utc = _aware_datetime(value, field="acquisition datetime", require_utc=True)
    return acquired_utc.astimezone(SENTINEL_TIMEZONE).date()


def canonical_asset_url(url: str) -> str:
    """Remove ephemeral SAS query parameters and fragments from an asset identity."""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path:
        raise ValueError(f"Asset URL is not a canonical HTTP(S) location: {url!r}")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def normalize_datatake_id(datatake_id: str) -> str:
    """Remove a reprocessing-baseline suffix from a Sentinel datatake identifier."""

    cleaned = datatake_id.strip().upper()
    if not cleaned:
        raise ValueError("Sentinel datatake ID cannot be empty.")
    normalized = _DATATAKE_BASELINE_SUFFIX.sub("", cleaned)
    if not normalized:
        raise ValueError(f"Invalid Sentinel datatake ID: {datatake_id!r}")
    return normalized


def processing_baseline_key(value: str) -> tuple[int, int]:
    """Parse a Sentinel processing baseline for numeric, not lexical, ordering."""

    match = _PROCESSING_BASELINE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Invalid Sentinel processing baseline: {value!r}")
    return int(match.group(1)), int(match.group(2))


def canonical_processing_baseline(value: str) -> str:
    major, minor = processing_baseline_key(value)
    return f"{major:02d}.{minor:02d}"


def _relative_orbit(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("Sentinel relative orbit cannot be empty.")
    try:
        return str(int(text))
    except ValueError as exc:
        raise ValueError(f"Invalid Sentinel relative orbit: {value!r}") from exc


def physical_acquisition_key(item: SentinelItemRecord) -> PhysicalAcquisitionKey:
    """Build the tile- and processing-version-independent acquisition key."""

    return PhysicalAcquisitionKey(
        platform=item.platform.strip().lower(),
        acquired_utc=_aware_datetime(
            item.acquired_utc, field="acquisition datetime", require_utc=True
        ),
        relative_orbit=_relative_orbit(item.relative_orbit),
        normalized_datatake_id=normalize_datatake_id(item.datatake_id),
    )


def sentinel_record_from_item(item: Any) -> SentinelItemRecord:
    """Parse one pystac-like Item without consulting target data or cloud thresholds."""

    properties = item.properties
    missing_properties = [
        field
        for field in (
            "platform",
            "sat:relative_orbit",
            "s2:datatake_id",
            "s2:mgrs_tile",
            "s2:processing_baseline",
            "s2:generation_time",
        )
        if properties.get(field) in {None, ""}
    ]
    if missing_properties:
        raise ValueError(f"Sentinel item {item.id} lacks properties: {missing_properties}")
    missing_assets = [asset for asset in REQUIRED_SENTINEL_ASSETS if asset not in item.assets]
    if missing_assets:
        raise ValueError(f"Sentinel item {item.id} lacks assets: {missing_assets}")
    if item.geometry is None:
        raise ValueError(f"Sentinel item {item.id} has no geometry.")
    geometry_wgs84 = shape(item.geometry)
    if geometry_wgs84.is_empty or not geometry_wgs84.is_valid:
        raise ValueError(f"Sentinel item {item.id} has invalid geometry.")

    acquired = item.datetime if item.datetime is not None else properties.get("datetime")
    if acquired is None:
        raise ValueError(f"Sentinel item {item.id} has no acquisition datetime.")
    cloud_value = properties.get("eo:cloud_cover")
    cloud_cover = None if cloud_value is None else float(cloud_value)
    if cloud_cover is not None and (
        not math.isfinite(cloud_cover) or not 0.0 <= cloud_cover <= 100.0
    ):
        raise ValueError(f"Sentinel item {item.id} has invalid cloud-cover metadata.")

    return SentinelItemRecord(
        item_id=str(item.id),
        platform=str(properties["platform"]),
        acquired_utc=_aware_datetime(
            acquired, field="acquisition datetime", require_utc=True
        ),
        relative_orbit=_relative_orbit(properties["sat:relative_orbit"]),
        datatake_id=str(properties["s2:datatake_id"]),
        mgrs_tile=str(properties["s2:mgrs_tile"]).strip().upper(),
        processing_baseline=canonical_processing_baseline(
            str(properties["s2:processing_baseline"])
        ),
        generation_time=_aware_datetime(
            properties["s2:generation_time"],
            field="generation time",
            require_utc=True,
        ),
        geometry_wgs84=geometry_wgs84,
        asset_hrefs=tuple(
            sorted(
                (asset, canonical_asset_url(item.assets[asset].href))
                for asset in REQUIRED_SENTINEL_ASSETS
            )
        ),
        cloud_cover_percent=cloud_cover,
    )


def _latest_item_per_tile(items: Iterable[SentinelItemRecord]) -> tuple[SentinelItemRecord, ...]:
    by_tile: dict[str, list[SentinelItemRecord]] = {}
    for item in items:
        tile = item.mgrs_tile.strip().upper()
        if not tile:
            raise ValueError(f"Sentinel item {item.item_id} has no MGRS tile.")
        by_tile.setdefault(tile, []).append(item)

    selected: list[SentinelItemRecord] = []
    for tile in sorted(by_tile):
        versions = by_tile[tile]
        newest_generation = max(
            _aware_datetime(
                item.generation_time, field="generation time", require_utc=True
            )
            for item in versions
        )
        newest = [
            item
            for item in versions
            if _aware_datetime(
                item.generation_time, field="generation time", require_utc=True
            )
            == newest_generation
        ]
        selected.append(min(newest, key=lambda item: item.item_id))
    return tuple(sorted(selected, key=lambda item: (item.mgrs_tile, item.item_id)))


def _union_aoi_coverage_fraction(
    item_geometries_wgs84: Iterable[BaseGeometry],
    *,
    aoi_geometry_wgs84: BaseGeometry,
    analysis_crs: str,
) -> float:
    geometries = list(item_geometries_wgs84)
    if not geometries:
        raise ValueError("A processing cohort must contain at least one geometry.")
    if aoi_geometry_wgs84.is_empty or not aoi_geometry_wgs84.is_valid:
        raise ValueError("AOI geometry must be non-empty and valid.")
    if any(geometry.is_empty or not geometry.is_valid for geometry in geometries):
        raise ValueError("Sentinel cohort contains an empty or invalid geometry.")

    projected = gpd.GeoSeries(
        [aoi_geometry_wgs84, *geometries], crs="EPSG:4326"
    ).to_crs(analysis_crs)
    aoi = projected.iloc[0]
    if aoi.area <= 0:
        raise ValueError("Projected AOI has zero area.")
    cohort_union = shapely.union_all(list(projected.iloc[1:]))
    coverage = float(cohort_union.intersection(aoi).area / aoi.area)
    return min(1.0, max(0.0, coverage))


def select_reprocessing_cohort(
    items: Iterable[SentinelItemRecord],
    *,
    aoi_geometry_wgs84: BaseGeometry,
    analysis_crs: str = "EPSG:3310",
) -> CohortSelection:
    """Select coverage, baseline, generation, then lexical-ID deterministically.

    Coverage and metadata are target-blind.  Within each baseline, only the latest
    generation for each MGRS tile is eligible; exact ties use the smallest item ID.
    """

    records = list(items)
    if not records:
        raise ValueError("Cannot select a Sentinel cohort from no items.")
    keys = {physical_acquisition_key(item) for item in records}
    if len(keys) != 1:
        raise ValueError("Cohort selection received multiple physical acquisitions.")
    acquisition_key = next(iter(keys))

    by_baseline: dict[tuple[int, int], list[SentinelItemRecord]] = {}
    for item in records:
        by_baseline.setdefault(processing_baseline_key(item.processing_baseline), []).append(
            item
        )

    candidates: list[_CohortCandidate] = []
    for baseline_key, baseline_items in by_baseline.items():
        selected = _latest_item_per_tile(baseline_items)
        generation_time = max(
            _aware_datetime(
                item.generation_time, field="generation time", require_utc=True
            )
            for item in selected
        )
        item_ids = tuple(sorted(item.item_id for item in selected))
        candidates.append(
            _CohortCandidate(
                baseline_key=baseline_key,
                processing_baseline=f"{baseline_key[0]:02d}.{baseline_key[1]:02d}",
                coverage_fraction=_union_aoi_coverage_fraction(
                    (item.geometry_wgs84 for item in selected),
                    aoi_geometry_wgs84=aoi_geometry_wgs84,
                    analysis_crs=analysis_crs,
                ),
                generation_time=generation_time,
                item_ids=item_ids,
                items=selected,
            )
        )

    maximum_coverage = max(candidate.coverage_fraction for candidate in candidates)
    finalists = [
        candidate for candidate in candidates if candidate.coverage_fraction == maximum_coverage
    ]
    maximum_baseline = max(candidate.baseline_key for candidate in finalists)
    finalists = [candidate for candidate in finalists if candidate.baseline_key == maximum_baseline]
    latest_generation = max(candidate.generation_time for candidate in finalists)
    finalists = [
        candidate for candidate in finalists if candidate.generation_time == latest_generation
    ]
    chosen = min(finalists, key=lambda candidate: candidate.item_ids)
    return CohortSelection(
        acquisition_key=acquisition_key,
        processing_baseline=chosen.processing_baseline,
        union_aoi_coverage_fraction=chosen.coverage_fraction,
        generation_time=chosen.generation_time,
        item_ids=chosen.item_ids,
        items=chosen.items,
    )


def select_all_reprocessing_cohorts(
    items: Iterable[SentinelItemRecord],
    *,
    aoi_geometry_wgs84: BaseGeometry,
    analysis_crs: str = "EPSG:3310",
) -> tuple[CohortSelection, ...]:
    """Group all items by physical acquisition and select one cohort per group."""

    grouped: dict[PhysicalAcquisitionKey, list[SentinelItemRecord]] = {}
    for item in items:
        grouped.setdefault(physical_acquisition_key(item), []).append(item)
    return tuple(
        select_reprocessing_cohort(
            grouped[key],
            aoi_geometry_wgs84=aoi_geometry_wgs84,
            analysis_crs=analysis_crs,
        )
        for key in sorted(grouped)
    )


def validate_final_test_lock(
    target_dates: Iterable[date | str],
    *,
    unlock_final_test: bool,
    final_test_year: int = FINAL_TEST_YEAR,
) -> tuple[date, ...]:
    """Reject calendar-year final-test targets until the explicit lock is opened."""

    normalized = tuple(sorted({_civil_date(value) for value in target_dates}))
    locked = [value for value in normalized if value.year >= final_test_year]
    if locked and not unlock_final_test:
        raise PermissionError(
            f"Final-test year {final_test_year} and later are locked; "
            f"found {len(locked)} target dates."
        )
    return normalized


def is_acquisition_in_target_window(
    acquired_utc: datetime | str,
    target_date: date | str,
) -> bool:
    """Return whether acquisition local date is exactly within d-60 through d-1."""

    target = _civil_date(target_date)
    acquired_local = utc_datetime_to_la_date(acquired_utc)
    lag_days = (target - acquired_local).days
    return WINDOW_END_DAYS <= lag_days <= WINDOW_START_DAYS


def build_target_window_membership(
    target_dates: Iterable[date | str],
    acquisitions: Iterable[PhysicalAcquisitionKey | CohortSelection],
    *,
    unlock_final_test: bool,
    final_test_year: int = FINAL_TEST_YEAR,
) -> tuple[TargetWindowMembership, ...]:
    """Build deterministic d-60:d-1 membership for unique physical acquisitions."""

    normalized_targets = validate_final_test_lock(
        target_dates,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )
    unique_acquisitions: dict[PhysicalAcquisitionKey, None] = {}
    for acquisition in acquisitions:
        key = (
            acquisition.acquisition_key
            if isinstance(acquisition, CohortSelection)
            else acquisition
        )
        if not isinstance(key, PhysicalAcquisitionKey):
            raise TypeError("Acquisitions must be physical keys or cohort selections.")
        _aware_datetime(key.acquired_utc, field="acquisition datetime", require_utc=True)
        unique_acquisitions[key] = None

    memberships: list[TargetWindowMembership] = []
    for target in normalized_targets:
        for key in sorted(unique_acquisitions):
            local_date = utc_datetime_to_la_date(key.acquired_utc)
            lag_days = (target - local_date).days
            if WINDOW_END_DAYS <= lag_days <= WINDOW_START_DAYS:
                memberships.append(
                    TargetWindowMembership(
                        target_date=target,
                        acquisition_key=key,
                        acquisition_local_date=local_date,
                        lag_days=lag_days,
                    )
                )
    return tuple(memberships)


def query_sentinel_items(
    client: SentinelSearchClient,
    *,
    intersects: BaseGeometry | Mapping[str, object],
    datetime_interval: str,
    global_cloud_cover_max: float | None = None,
) -> tuple[Any, ...]:
    """Query every intersecting L2A item without a global cloud-cover cutoff.

    ``client`` is injectable so inventory tests never need network access.  Passing a
    cloud threshold is an error rather than a convenience because locally clear LA
    pixels may occur in a globally cloudy MGRS item.
    """

    if global_cloud_cover_max is not None:
        raise ValueError("Global Sentinel-2 cloud-cover cutoffs are prohibited.")
    if not datetime_interval.strip():
        raise ValueError("Sentinel query datetime interval cannot be empty.")
    if isinstance(intersects, BaseGeometry):
        if intersects.is_empty or not intersects.is_valid:
            raise ValueError("Sentinel query geometry must be non-empty and valid.")
        intersects_payload: Mapping[str, object] = geometry_mapping(intersects)
    else:
        intersects_payload = dict(intersects)
    search = client.search(
        collections=[SENTINEL_COLLECTION],
        intersects=intersects_payload,
        datetime=datetime_interval,
    )
    return tuple(search.items())


def _geometry_wkb_hex(geometry: BaseGeometry) -> str:
    normalized = shapely.normalize(geometry)
    return str(
        shapely.to_wkb(
            normalized,
            hex=True,
            output_dimension=2,
            byte_order=1,
            include_srid=False,
        )
    )


def _item_semantic_payload(item: SentinelItemRecord) -> dict[str, object]:
    cloud_cover = (
        None
        if item.cloud_cover_percent is None
        else format(float(item.cloud_cover_percent), ".12g")
    )
    return {
        "item_id": item.item_id,
        "platform": item.platform.strip().lower(),
        "acquired_utc": _utc_isoformat(item.acquired_utc),
        "relative_orbit": _relative_orbit(item.relative_orbit),
        "datatake_id": item.datatake_id.strip().upper(),
        "normalized_datatake_id": normalize_datatake_id(item.datatake_id),
        "mgrs_tile": item.mgrs_tile.strip().upper(),
        "processing_baseline": canonical_processing_baseline(item.processing_baseline),
        "generation_time": _utc_isoformat(item.generation_time),
        "geometry_wkb_hex": _geometry_wkb_hex(item.geometry_wgs84),
        "asset_hrefs": [
            [asset, canonical_asset_url(href)]
            for asset, href in sorted(item.asset_hrefs)
        ],
        "cloud_cover_percent_audit_only": cloud_cover,
    }


def sentinel_inventory_semantic_sha256(selections: Iterable[CohortSelection]) -> str:
    """Hash selected scientific identity independent of row and signed-URL order."""

    selection_list = list(selections)
    ids = [selection.acquisition_key.semantic_id for selection in selection_list]
    if len(ids) != len(set(ids)):
        raise ValueError("Sentinel inventory contains duplicate physical acquisitions.")
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "algorithm_version": INVENTORY_ALGORITHM_VERSION,
        "selections": [
            {
                "physical_acquisition_id": selection.acquisition_key.semantic_id,
                "processing_baseline": canonical_processing_baseline(
                    selection.processing_baseline
                ),
                "union_aoi_coverage_fraction": format(
                    float(selection.union_aoi_coverage_fraction), ".12f"
                ),
                "generation_time": _utc_isoformat(selection.generation_time),
                "item_ids": list(sorted(selection.item_ids)),
                "items": [
                    _item_semantic_payload(item)
                    for item in sorted(
                        selection.items, key=lambda item: (item.mgrs_tile, item.item_id)
                    )
                ],
            }
            for selection in sorted(
                selection_list, key=lambda selection: selection.acquisition_key
            )
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_local_date_query_intervals(
    target_dates: Iterable[date | str],
    *,
    unlock_final_test: bool,
    final_test_year: int = FINAL_TEST_YEAR,
) -> tuple[LocalDateQueryInterval, ...]:
    """Merge the union of every target's exact d-60:d-1 local-date window."""

    targets = validate_final_test_lock(
        target_dates,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )
    if not targets:
        raise ValueError("At least one target date is required for Sentinel inventory.")
    windows = sorted(
        (
            target - timedelta(days=WINDOW_START_DAYS),
            target - timedelta(days=WINDOW_END_DAYS),
        )
        for target in targets
    )
    merged: list[LocalDateQueryInterval] = []
    for start_date, end_date in windows:
        if not merged or start_date > merged[-1].end_date + timedelta(days=1):
            merged.append(LocalDateQueryInterval(start_date, end_date))
            continue
        previous = merged[-1]
        merged[-1] = LocalDateQueryInterval(
            previous.start_date,
            max(previous.end_date, end_date),
        )
    return tuple(merged)


def _unsigned_href(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        return value
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )


def _canonical_stac_value(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(child_key): _canonical_stac_value(child_value, key=str(child_key))
            for child_key, child_value in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_stac_value(child) for child in value]
    if isinstance(value, datetime):
        return _utc_isoformat(
            _aware_datetime(value, field="STAC datetime", require_utc=False)
        )
    if isinstance(value, date):
        return value.isoformat()
    if key == "href" and isinstance(value, str):
        return _unsigned_href(value)
    return value


def canonical_stac_item_snapshot(item: Any) -> dict[str, Any]:
    """Return a deterministic, unsigned STAC Item snapshot suitable for freezing."""

    if isinstance(item, Mapping):
        raw = dict(item)
    else:
        to_dict = getattr(item, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("A STAC item must be a mapping or expose to_dict().")
        raw = to_dict()
    if not isinstance(raw, Mapping):
        raise TypeError("STAC item to_dict() must return a mapping.")
    snapshot = _canonical_stac_value(raw)
    item_id = snapshot.get("id")
    if item_id is None or not str(item_id).strip():
        raise ValueError("A STAC snapshot must contain a non-empty item ID.")
    # Reject non-standard JSON values now rather than after any artifact is written.
    json.dumps(snapshot, sort_keys=True, allow_nan=False)
    return snapshot


def _snapshot_text(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(
        snapshot,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _snapshot_filename(item_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("._") or "item"
    identity_suffix = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:120]}-{identity_suffix}.json"


def _selected_acquisitions_frame(
    selections: Iterable[CohortSelection],
) -> pd.DataFrame:
    columns = [
        "physical_acquisition_id",
        "platform",
        "acquired_utc",
        "acquisition_local_date",
        "relative_orbit",
        "normalized_datatake_id",
        "processing_baseline",
        "generation_time",
        "union_city_coverage_fraction",
        "item_count",
        "item_ids",
        "mgrs_tiles",
    ]
    rows: list[dict[str, object]] = []
    for selection in sorted(selections, key=lambda value: value.acquisition_key):
        key = selection.acquisition_key
        rows.append(
            {
                "physical_acquisition_id": key.semantic_id,
                "platform": key.platform,
                "acquired_utc": _utc_isoformat(key.acquired_utc),
                "acquisition_local_date": utc_datetime_to_la_date(
                    key.acquired_utc
                ).isoformat(),
                "relative_orbit": key.relative_orbit,
                "normalized_datatake_id": key.normalized_datatake_id,
                "processing_baseline": canonical_processing_baseline(
                    selection.processing_baseline
                ),
                "generation_time": _utc_isoformat(selection.generation_time),
                "union_city_coverage_fraction": selection.union_aoi_coverage_fraction,
                "item_count": len(selection.items),
                "item_ids": "|".join(sorted(selection.item_ids)),
                "mgrs_tiles": "|".join(
                    sorted({item.mgrs_tile for item in selection.items})
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _asset_column(asset: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", asset.lower()).strip("_")
    return f"asset_{normalized}_href"


def _selected_items_frame(
    selections: Iterable[CohortSelection],
    *,
    snapshot_records: Mapping[str, Mapping[str, object]],
) -> pd.DataFrame:
    asset_columns = [_asset_column(asset) for asset in REQUIRED_SENTINEL_ASSETS]
    columns = [
        "physical_acquisition_id",
        "item_id",
        "platform",
        "acquired_utc",
        "acquisition_local_date",
        "relative_orbit",
        "datatake_id",
        "normalized_datatake_id",
        "mgrs_tile",
        "processing_baseline",
        "generation_time",
        "cloud_cover_percent_audit_only",
        "geometry_wkb_hex",
        "snapshot_filename",
        "snapshot_sha256",
        *asset_columns,
    ]
    rows: list[dict[str, object]] = []
    for selection in sorted(selections, key=lambda value: value.acquisition_key):
        key = selection.acquisition_key
        for item in sorted(selection.items, key=lambda value: (value.mgrs_tile, value.item_id)):
            if item.item_id not in snapshot_records:
                raise ValueError(f"No frozen STAC snapshot for selected item {item.item_id}.")
            snapshot = snapshot_records[item.item_id]
            assets = dict(item.asset_hrefs)
            row: dict[str, object] = {
                "physical_acquisition_id": key.semantic_id,
                "item_id": item.item_id,
                "platform": item.platform.strip().lower(),
                "acquired_utc": _utc_isoformat(item.acquired_utc),
                "acquisition_local_date": utc_datetime_to_la_date(
                    item.acquired_utc
                ).isoformat(),
                "relative_orbit": _relative_orbit(item.relative_orbit),
                "datatake_id": item.datatake_id.strip().upper(),
                "normalized_datatake_id": normalize_datatake_id(item.datatake_id),
                "mgrs_tile": item.mgrs_tile.strip().upper(),
                "processing_baseline": canonical_processing_baseline(
                    item.processing_baseline
                ),
                "generation_time": _utc_isoformat(item.generation_time),
                "cloud_cover_percent_audit_only": item.cloud_cover_percent,
                "geometry_wkb_hex": _geometry_wkb_hex(item.geometry_wgs84),
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
    return pd.DataFrame(rows, columns=columns)


def _target_window_membership_frame(
    memberships: Iterable[TargetWindowMembership],
) -> pd.DataFrame:
    columns = [
        "target_date",
        "physical_acquisition_id",
        "acquisition_local_date",
        "lag_days",
    ]
    rows = [
        {
            "target_date": membership.target_date.isoformat(),
            "physical_acquisition_id": membership.acquisition_key.semantic_id,
            "acquisition_local_date": membership.acquisition_local_date.isoformat(),
            "lag_days": membership.lag_days,
        }
        for membership in sorted(
            memberships,
            key=lambda value: (value.target_date, value.acquisition_key),
        )
    ]
    return pd.DataFrame(rows, columns=columns)


def _manifest_truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"Invalid primary_eligible value: {value!r}")


def _read_primary_target_dates(
    manifest_path: Path,
    *,
    unlock_final_test: bool,
    final_test_year: int,
) -> tuple[date, ...]:
    manifest = pd.read_csv(manifest_path)
    if manifest.empty or "local_date" not in manifest.columns:
        raise ValueError("Primary overpass manifest must contain local_date rows.")
    if "primary_eligible" in manifest.columns:
        eligibility = manifest["primary_eligible"].map(_manifest_truth)
        if not bool(eligibility.all()):
            raise ValueError("Primary overpass manifest contains ineligible rows.")
    target_dates = tuple(_civil_date(str(value)) for value in manifest["local_date"])
    if len(target_dates) != len(set(target_dates)):
        raise ValueError("Primary overpass manifest contains duplicate local dates.")
    return validate_final_test_lock(
        target_dates,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )


def _read_city_aoi(city_boundary_path: Path) -> tuple[BaseGeometry, str]:
    city = gpd.read_file(city_boundary_path)
    if city.empty or city.crs is None:
        raise ValueError("Frozen city boundary must be non-empty and georeferenced.")
    city_wgs84 = city.to_crs("EPSG:4326")
    aoi = shapely.union_all(list(city_wgs84.geometry))
    if aoi.is_empty or not aoi.is_valid:
        raise ValueError("Frozen city boundary has an empty or invalid union geometry.")
    geometry_sha256 = canonical_sha256(
        {"crs": "EPSG:4326", "geometry_wkb_hex": _geometry_wkb_hex(aoi)}
    )
    return aoi, geometry_sha256


def _csv_file_record(path: Path, frame: pd.DataFrame) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def build_sentinel_inventory_artifacts(
    *,
    city_boundary_path: Path,
    primary_overpass_manifest_path: Path,
    output_directory: Path,
    raw_stac_directory: Path,
    client: SentinelSearchClient,
    unlock_final_test: bool = False,
    final_test_year: int = FINAL_TEST_YEAR,
    query_time_utc: datetime | None = None,
    analysis_crs: str = "EPSG:3310",
) -> dict[str, object]:
    """Query, select, serialize, and atomically commit the Sentinel inventory.

    ``inventory_summary.json`` is the commit marker.  It is removed before any
    validation or network operation and replaced only after every recorded artifact
    has been written and hashed successfully.
    """

    city_boundary_path = Path(city_boundary_path)
    primary_overpass_manifest_path = Path(primary_overpass_manifest_path)
    output_directory = Path(output_directory)
    raw_stac_directory = Path(raw_stac_directory)
    summary_path = output_directory / INVENTORY_SUMMARY_FILENAME
    summary_path.unlink(missing_ok=True)

    if not city_boundary_path.is_file():
        raise FileNotFoundError(f"Frozen city boundary not found: {city_boundary_path}")
    if not primary_overpass_manifest_path.is_file():
        raise FileNotFoundError(
            f"Frozen primary overpass manifest not found: {primary_overpass_manifest_path}"
        )
    queried_at = (
        datetime.now(UTC)
        if query_time_utc is None
        else _aware_datetime(query_time_utc, field="query time", require_utc=True)
    )
    target_dates = _read_primary_target_dates(
        primary_overpass_manifest_path,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )
    query_intervals = build_local_date_query_intervals(
        target_dates,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )
    aoi, city_geometry_sha256 = _read_city_aoi(city_boundary_path)

    item_objects: dict[str, Any] = {}
    snapshots: dict[str, dict[str, object]] = {}
    query_response_item_count = 0
    for interval in query_intervals:
        queried = query_sentinel_items(
            client,
            intersects=aoi,
            datetime_interval=interval.utc_datetime_interval,
        )
        query_response_item_count += len(queried)
        for item in queried:
            snapshot = canonical_stac_item_snapshot(item)
            item_id = str(snapshot["id"])
            object_item_id = str(getattr(item, "id", item_id))
            if object_item_id != item_id:
                raise ValueError(
                    f"STAC item ID disagrees with its snapshot: {object_item_id} != {item_id}"
                )
            snapshot_text = _snapshot_text(snapshot)
            snapshot_sha256 = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
            existing = snapshots.get(item_id)
            if existing is not None:
                if existing["sha256"] != snapshot_sha256:
                    raise ValueError(
                        f"Conflicting normalized STAC snapshots share item ID {item_id}."
                    )
                continue
            snapshots[item_id] = {
                "item_id": item_id,
                "filename": _snapshot_filename(item_id),
                "sha256": snapshot_sha256,
                "bytes": len(snapshot_text.encode("utf-8")),
                "text": snapshot_text,
            }
            item_objects[item_id] = item

    records = tuple(
        sentinel_record_from_item(item_objects[item_id]) for item_id in sorted(item_objects)
    )
    candidate_selections = select_all_reprocessing_cohorts(
        records,
        aoi_geometry_wgs84=aoi,
        analysis_crs=analysis_crs,
    )
    memberships = build_target_window_membership(
        target_dates,
        candidate_selections,
        unlock_final_test=unlock_final_test,
        final_test_year=final_test_year,
    )
    retained_keys = {membership.acquisition_key for membership in memberships}
    selections = tuple(
        selection
        for selection in candidate_selections
        if selection.acquisition_key in retained_keys
    )
    if not selections:
        raise ValueError("No Sentinel physical acquisition belongs to a target window.")

    acquisitions_frame = _selected_acquisitions_frame(selections)
    items_frame = _selected_items_frame(
        selections,
        snapshot_records=snapshots,
    )
    membership_frame = _target_window_membership_frame(memberships)

    for snapshot_record in sorted(snapshots.values(), key=lambda value: str(value["item_id"])):
        snapshot_path = raw_stac_directory / str(snapshot_record["filename"])
        if snapshot_path.is_file():
            if sha256_file(snapshot_path) != snapshot_record["sha256"]:
                raise ValueError(
                    "Refusing to overwrite a different frozen STAC snapshot for "
                    f"item {snapshot_record['item_id']}."
                )
        else:
            atomic_text(str(snapshot_record["text"]), snapshot_path)
        if sha256_file(snapshot_path) != snapshot_record["sha256"]:
            raise RuntimeError(f"Frozen STAC snapshot hash mismatch: {snapshot_path}")

    acquisition_path = output_directory / SELECTED_ACQUISITIONS_FILENAME
    item_path = output_directory / SELECTED_ITEMS_FILENAME
    membership_path = output_directory / TARGET_WINDOW_MEMBERSHIP_FILENAME
    atomic_csv(acquisitions_frame, acquisition_path)
    atomic_csv(items_frame, item_path)
    atomic_csv(membership_frame, membership_path)

    snapshot_summary_records = [
        {
            "item_id": record["item_id"],
            "filename": record["filename"],
            "sha256": record["sha256"],
            "bytes": record["bytes"],
        }
        for record in sorted(snapshots.values(), key=lambda value: str(value["item_id"]))
    ]
    summary: dict[str, object] = {
        "state": "complete",
        "artifacts_valid": True,
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "algorithm_version": INVENTORY_ALGORITHM_VERSION,
        "queried_at_utc": _utc_isoformat(queried_at),
        "collection": SENTINEL_COLLECTION,
        "global_scene_cloud_cover_filter": None,
        "local_timezone": str(SENTINEL_TIMEZONE),
        "window_start_days_before_target": WINDOW_START_DAYS,
        "window_end_days_before_target": WINDOW_END_DAYS,
        "final_test_year": final_test_year,
        "unlock_final_test": unlock_final_test,
        "analysis_crs": analysis_crs,
        "inputs": {
            "city_boundary": {
                "path": city_boundary_path.as_posix(),
                "sha256": sha256_file(city_boundary_path),
                "geometry_semantic_sha256": city_geometry_sha256,
            },
            "primary_overpass_manifest": {
                "path": primary_overpass_manifest_path.as_posix(),
                "sha256": sha256_file(primary_overpass_manifest_path),
            },
        },
        "target_dates": {
            "count": len(target_dates),
            "minimum": target_dates[0].isoformat(),
            "maximum": target_dates[-1].isoformat(),
        },
        "query_intervals": [
            {
                "local_start_date": interval.start_date.isoformat(),
                "local_end_date": interval.end_date.isoformat(),
                "utc_datetime_interval_broad": interval.utc_datetime_interval,
            }
            for interval in query_intervals
        ],
        "counts": {
            "query_response_items_including_interval_duplicates": query_response_item_count,
            "unique_stac_items": len(snapshots),
            "candidate_physical_acquisitions": len(candidate_selections),
            "selected_physical_acquisitions": len(selections),
            "selected_items": len(items_frame),
            "target_window_memberships": len(membership_frame),
        },
        "sentinel_inventory_semantic_sha256": sentinel_inventory_semantic_sha256(
            selections
        ),
        "output_files": {
            SELECTED_ACQUISITIONS_FILENAME: _csv_file_record(
                acquisition_path, acquisitions_frame
            ),
            SELECTED_ITEMS_FILENAME: _csv_file_record(item_path, items_frame),
            TARGET_WINDOW_MEMBERSHIP_FILENAME: _csv_file_record(
                membership_path, membership_frame
            ),
        },
        "raw_stac_snapshots": {
            "directory": raw_stac_directory.as_posix(),
            "count": len(snapshot_summary_records),
            "set_sha256": canonical_sha256(snapshot_summary_records),
            "files": snapshot_summary_records,
        },
    }
    atomic_json(summary, summary_path)
    return summary
