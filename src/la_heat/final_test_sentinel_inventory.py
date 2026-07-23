"""Freeze an isolated, target-blind Sentinel-2 inventory for the 2025 test.

This module is an authorization and provenance layer around the already tested
``sentinel_inventory`` implementation.  It reads only frozen metadata, verifies
that every target date is exactly in 2025, and never opens an optical asset,
target table, fitted model, or model score.
"""

from __future__ import annotations

import json
import math
import re
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import pandas as pd

import la_heat.sentinel_inventory as _sentinel_inventory
from la_heat.final_test_inventory import (
    FINAL_TEST_YEAR,
    KEY_UNIVERSE_FILENAME,
    PRIMARY_FILENAME,
    authenticate_formal_model_lock,
)
from la_heat.final_test_inventory import (
    SUMMARY_FILENAME as LANDSAT_SUMMARY_FILENAME,
)
from la_heat.provenance import (
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_inventory import (
    INVENTORY_SUMMARY_FILENAME,
    SELECTED_ACQUISITIONS_FILENAME,
    SELECTED_ITEMS_FILENAME,
    TARGET_WINDOW_MEMBERSHIP_FILENAME,
    SentinelSearchClient,
    build_sentinel_inventory_artifacts,
    canonical_asset_url,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = (
    "final-test-sentinel-inventory-v3-datatake-physical-time"
)
PROVENANCE_FILENAME: Final = "FINAL_TEST_SENTINEL_INVENTORY.json"
DEFAULT_STAC_API: Final = "https://earth-search.aws.element84.com/v1"
STAC_PROVIDER: Final = "Element 84 Earth Search"
EXPECTED_TRACT_COUNT: Final = 1_096
LANDSAT_INVENTORY_SUFFIX: Final = Path("manifests/final_test_2025/landsat_inventory")
OUTPUT_SUFFIX: Final = Path("manifests/final_test_2025/sentinel_inventory")
RAW_STAC_SUFFIX: Final = Path("data/raw/final_test_2025/sentinel/stac_items")
KEY_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)
BASE_OUTPUT_FILENAMES: Final = (
    SELECTED_ACQUISITIONS_FILENAME,
    SELECTED_ITEMS_FILENAME,
    TARGET_WINDOW_MEMBERSHIP_FILENAME,
)
PIPELINE_FILES: Final = (
    "scripts/build_final_test_sentinel_inventory.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/final_test_sentinel_inventory.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_inventory.py",
)
EARTH_SEARCH_ASSET_ALIASES: Final = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B08": "nir",
    "B8A": "nir08",
    "B11": "swir16",
    "B12": "swir22",
    "SCL": "scl",
    "product-metadata": "product_metadata",
}
EARTH_SEARCH_PROPERTY_DERIVATIONS: Final = {
    "s2:mgrs_tile": "grid:code formatted as MGRS-<tile>",
    "sat:relative_orbit": "_Rddd_ token in s2:product_uri",
    "physical_acquisition_datetime": (
        "shared sensing timestamp in s2:datatake_id and s2:product_uri"
    ),
}
EARTH_SEARCH_SUPPORTED_PLATFORMS: Final = ("sentinel-2a", "sentinel-2b")
CALIBRATION_CONTRACT_PROPERTY: Final = "la_heat:cog_calibration_contract"
CALIBRATION_CONTRACT_ID: Final = "earth-search-raster-bands-scale-offset-v1"
CALIBRATION_FORMULA: Final = "reflectance = DN * scale + offset"
CALIBRATION_SOURCES: Final = (
    "https://github.com/Element84/earth-search#gainoffset-in-items-after-jan-25-2022",
    "https://github.com/stac-extensions/raster#scale-and-offset-uses-and-examples",
)
_REFLECTANCE_ASSET_ALIASES: Final = {
    key: value
    for key, value in EARTH_SEARCH_ASSET_ALIASES.items()
    if key not in {"SCL", "product-metadata"}
}
_MGRS_CODE = re.compile(r"^MGRS-(?P<tile>\d{2}[A-Z]{3})$", re.IGNORECASE)
_RELATIVE_ORBIT = re.compile(r"(?:^|_)R(?P<orbit>\d{3})(?:_|$)")
_DATATAKE_ID = re.compile(
    r"^GS2(?P<satellite>[AB])_(?P<sensing>\d{8}T\d{6})_\d+_N\d+\.\d+$"
)
_PRODUCT_URI = re.compile(
    r"^S2(?P<satellite>[AB])_MSIL2A_(?P<sensing>\d{8}T\d{6})_"
    r"N\d{4}_R\d{3}_T\d{2}[A-Z]{3}_\d{8}T\d{6}(?:\.SAFE)?$"
)
_PRODUCT_METADATA_S3_PATH = re.compile(
    r"^/tiles/(?P<zone>\d{2})/(?P<band>[A-Z])/(?P<square>[A-Z]{2})/"
    r"(?P<year>\d{4})/(?P<month>\d{1,2})/(?P<day>\d{1,2})/\d+/"
    r"product_metadata\.xml$"
)
_CANONICALIZER_LOCK = Lock()


class FinalTestSentinelInventoryError(RuntimeError):
    """Raised when blind 2025 Sentinel inventory provenance cannot be proven."""


@dataclass(frozen=True, slots=True)
class _AuthenticatedInputs:
    landsat_summary_path: Path
    landsat_summary_sha256: str
    landsat_commit_sha256: str
    primary_path: Path
    primary_sha256: str
    key_path: Path
    key_sha256: str
    city_path: Path
    city_sha256: str
    city_geometry_sha256: str
    target_dates: tuple[str, ...]
    key_count: int
    tract_count: int


@dataclass(frozen=True, slots=True)
class _AdaptedEarthSearchItem:
    original: Any
    physical_datetime: datetime
    properties: dict[str, Any]
    assets: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.original.id)

    @property
    def geometry(self) -> Any:
        return self.original.geometry

    @property
    def datetime(self) -> datetime | None:
        return self.physical_datetime

    def to_dict(self) -> dict[str, Any]:
        payload = deepcopy(self.original.to_dict())
        payload["properties"] = deepcopy(self.properties)
        payload["assets"] = {name: deepcopy(asset.to_dict()) for name, asset in self.assets.items()}
        return payload


class _AdaptedSearchResult:
    def __init__(self, result: Any) -> None:
        self._result = result

    def items(self) -> tuple[_AdaptedEarthSearchItem, ...]:
        return tuple(adapt_earth_search_item(item) for item in self._result.items())


class _EarthSearchClientAdapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def search(self, **kwargs: object) -> _AdaptedSearchResult:
        if "query" in kwargs:
            raise FinalTestSentinelInventoryError("Unexpected pre-existing STAC query.")
        kwargs["query"] = {"platform": {"in": list(EARTH_SEARCH_SUPPORTED_PLATFORMS)}}
        return _AdaptedSearchResult(self._client.search(**kwargs))


def _canonical_product_metadata_s3_uri(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "s3"
        or parsed.netloc.lower() != "sentinel-s2-l2a"
        or parsed.query
        or parsed.fragment
        or _PRODUCT_METADATA_S3_PATH.fullmatch(parsed.path) is None
    ):
        raise FinalTestSentinelInventoryError(
            "Earth Search product metadata is not the expected requester-pays S3 URI."
        )
    return urlunsplit(("s3", "sentinel-s2-l2a", parsed.path, "", ""))


def _inventory_asset_url(value: str) -> str:
    try:
        return canonical_asset_url(value)
    except ValueError:
        return _canonical_product_metadata_s3_uri(value)


@contextmanager
def _allow_audited_product_metadata_s3() -> Any:
    """Narrowly allow the official requester-pays XML URI in the frozen builder."""

    with _CANONICALIZER_LOCK:
        previous = _sentinel_inventory.canonical_asset_url
        _sentinel_inventory.canonical_asset_url = _inventory_asset_url
        try:
            yield
        finally:
            _sentinel_inventory.canonical_asset_url = previous


def _cog_calibration_contract(item: Any, *, mgrs_tile: str) -> dict[str, Any]:
    boa_offset_applied = item.properties.get("earthsearch:boa_offset_applied")
    if not isinstance(boa_offset_applied, bool):
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} lacks a boolean boa_offset_applied audit flag."
        )
    product_asset = item.assets["product_metadata"]
    product_uri = _canonical_product_metadata_s3_uri(str(product_asset.href))
    product_path = _PRODUCT_METADATA_S3_PATH.fullmatch(urlsplit(product_uri).path)
    assert product_path is not None
    path_tile = "".join(
        (product_path.group("zone"), product_path.group("band"), product_path.group("square"))
    )
    acquired = item.datetime
    if (
        path_tile != mgrs_tile
        or acquired is None
        or (
            int(product_path.group("year")),
            int(product_path.group("month")),
            int(product_path.group("day")),
        )
        != (acquired.year, acquired.month, acquired.day)
    ):
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} product-metadata S3 lineage conflicts."
        )

    bands: dict[str, dict[str, Any]] = {}
    for canonical, source in _REFLECTANCE_ASSET_ALIASES.items():
        asset = item.assets[source]
        payload = asset.to_dict()
        raster_bands = payload.get("raster:bands")
        if not isinstance(raster_bands, list) or len(raster_bands) != 1:
            raise FinalTestSentinelInventoryError(
                f"Earth Search item {item.id} {source} lacks one raster:bands record."
            )
        raster_band = raster_bands[0]
        if not isinstance(raster_band, dict):
            raise FinalTestSentinelInventoryError(
                f"Earth Search item {item.id} {source} raster metadata is invalid."
            )
        scale = raster_band.get("scale")
        offset = raster_band.get("offset")
        if (
            isinstance(scale, bool)
            or isinstance(offset, bool)
            or not isinstance(scale, (int, float))
            or not isinstance(offset, (int, float))
            or not math.isfinite(float(scale))
            or not math.isfinite(float(offset))
            or float(scale) <= 0
        ):
            raise FinalTestSentinelInventoryError(
                f"Earth Search item {item.id} {source} has invalid scale/offset."
            )
        href = canonical_asset_url(str(asset.href))
        bands[canonical] = {
            "source_asset": source,
            "href": href,
            "scale": float(scale),
            "offset": float(offset),
        }
    return {
        "id": CALIBRATION_CONTRACT_ID,
        # Element 84 documents raster:bands scale/offset as the authoritative
        # decoding rule.  The item-level flag is retained for audit only: real
        # 2025 items can legitimately contain either boolean value.
        "earthsearch:boa_offset_applied": boa_offset_applied,
        "formula": CALIBRATION_FORMULA,
        "offset_application_order": "after_scale",
        "product_metadata_uri": product_uri,
        "product_metadata_use": "audit_lineage_only_requester_pays_s3",
        "bands": bands,
        "official_sources": list(CALIBRATION_SOURCES),
    }


def _physical_acquisition_datetime(
    item: Any,
    *,
    platform: str,
    product_uri: str,
) -> tuple[datetime, datetime]:
    """Return one shared datatake time plus the tile-specific audit time."""

    datatake_id = str(item.properties.get("s2:datatake_id", "")).strip()
    datatake_match = _DATATAKE_ID.fullmatch(datatake_id)
    product_match = _PRODUCT_URI.fullmatch(product_uri)
    expected_satellite = {"sentinel-2a": "A", "sentinel-2b": "B"}.get(platform)
    if (
        datatake_match is None
        or product_match is None
        or expected_satellite is None
        or datatake_match.group("satellite") != expected_satellite
        or product_match.group("satellite") != expected_satellite
        or datatake_match.group("sensing") != product_match.group("sensing")
    ):
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} has conflicting datatake sensing metadata."
        )
    physical = datetime.strptime(
        datatake_match.group("sensing"), "%Y%m%dT%H%M%S"
    ).replace(tzinfo=UTC)
    tile_datetime = item.datetime
    if (
        tile_datetime is None
        or tile_datetime.tzinfo is None
        or tile_datetime.utcoffset() is None
    ):
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} lacks an aware tile datetime."
        )
    tile_utc = tile_datetime.astimezone(UTC)
    if abs((tile_utc - physical).total_seconds()) > 30 * 60:
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} tile time conflicts with its datatake."
        )
    return physical, tile_utc


def adapt_earth_search_item(item: Any) -> _AdaptedEarthSearchItem:
    """Map Earth Search metadata names to the frozen Sentinel parser contract."""

    properties = dict(item.properties)
    grid_code = str(properties.get("grid:code", "")).strip()
    grid_match = _MGRS_CODE.fullmatch(grid_code)
    if grid_match is None:
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} has an invalid grid:code."
        )
    mgrs_tile = grid_match.group("tile").upper()
    existing_tile = properties.get("s2:mgrs_tile")
    if existing_tile not in {None, ""} and str(existing_tile).strip().upper() != mgrs_tile:
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} has conflicting MGRS metadata."
        )

    product_uri = str(properties.get("s2:product_uri", "")).strip()
    orbit_match = _RELATIVE_ORBIT.search(product_uri)
    if orbit_match is None:
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} lacks an _Rddd_ product-URI token."
        )
    relative_orbit = orbit_match.group("orbit")
    existing_orbit = properties.get("sat:relative_orbit")
    if existing_orbit not in {None, ""}:
        try:
            normalized_existing = f"{int(existing_orbit):03d}"
        except (TypeError, ValueError) as error:
            raise FinalTestSentinelInventoryError(
                f"Earth Search item {item.id} has invalid relative-orbit metadata."
            ) from error
        if normalized_existing != relative_orbit:
            raise FinalTestSentinelInventoryError(
                f"Earth Search item {item.id} has conflicting orbit metadata."
            )

    missing_assets = [
        source for source in EARTH_SEARCH_ASSET_ALIASES.values() if source not in item.assets
    ]
    if missing_assets:
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} lacks assets: {missing_assets}"
        )
    platform = str(properties.get("platform", "")).strip().lower()
    if platform not in set(EARTH_SEARCH_SUPPORTED_PLATFORMS):
        raise FinalTestSentinelInventoryError(
            f"Earth Search item {item.id} has unsupported platform {platform!r}."
        )
    properties["platform"] = platform
    properties["s2:mgrs_tile"] = mgrs_tile
    properties["sat:relative_orbit"] = relative_orbit
    physical_datetime, tile_datetime = _physical_acquisition_datetime(
        item,
        platform=platform,
        product_uri=product_uri,
    )
    properties["la_heat:physical_acquisition_utc"] = physical_datetime.isoformat().replace(
        "+00:00", "Z"
    )
    properties["la_heat:tile_datetime_utc"] = tile_datetime.isoformat().replace(
        "+00:00", "Z"
    )
    properties[CALIBRATION_CONTRACT_PROPERTY] = _cog_calibration_contract(item, mgrs_tile=mgrs_tile)
    assets = {
        canonical: item.assets[source] for canonical, source in EARTH_SEARCH_ASSET_ALIASES.items()
    }
    return _AdaptedEarthSearchItem(item, physical_datetime, properties, assets)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _require_suffix(path: Path, suffix: Path, *, label: str) -> None:
    actual = tuple(part.casefold() for part in path.resolve().parts)
    expected = tuple(part.casefold() for part in suffix.parts)
    if len(actual) < len(expected) or actual[-len(expected) :] != expected:
        raise FinalTestSentinelInventoryError(
            f"{label} must end in the isolated path {suffix.as_posix()}."
        )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestSentinelInventoryError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestSentinelInventoryError(f"{label} changed or is not an object.")
    return payload, before


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestSentinelInventoryError(f"{label} canonical commit is invalid.")
    return recorded


def _locked_file(path: Path, record: object, *, label: str) -> str:
    if not isinstance(record, dict):
        raise FinalTestSentinelInventoryError(f"{label} file lock is missing.")
    recorded_path = record.get("path")
    if not isinstance(recorded_path, str) or Path(recorded_path).resolve() != path.resolve():
        raise FinalTestSentinelInventoryError(f"{label} path lock changed.")
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise FinalTestSentinelInventoryError(f"{label} byte lock failed.")
    return str(record["sha256"])


def _civil_2025(values: pd.Series, *, label: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError) as error:
        raise FinalTestSentinelInventoryError(f"{label} must contain ISO civil dates.") from error
    if (
        parsed.empty
        or parsed.isna().any()
        or parsed.dt.tz is not None
        or not parsed.dt.normalize().equals(parsed)
        or not parsed.dt.year.eq(FINAL_TEST_YEAR).all()
    ):
        raise FinalTestSentinelInventoryError(
            f"{label} must contain only civil dates in exactly 2025."
        )
    return parsed


def _authenticate_landsat_inventory(
    directory: Path,
    *,
    formal_lock: dict[str, Any],
    formal_lock_sha256: str,
) -> _AuthenticatedInputs:
    summary_path = directory / LANDSAT_SUMMARY_FILENAME
    summary, summary_sha = _read_json(summary_path, label="2025 Landsat inventory")
    commit = _verify_commit(summary, label="2025 Landsat inventory")
    formal_record = summary.get("formal_model_lock")
    if (
        summary.get("state") != "target_blind_inventory_frozen"
        or summary.get("final_test_year") != FINAL_TEST_YEAR
        or summary.get("target_blind") is not True
        or summary.get("target_assets_opened") is not False
        or summary.get("target_or_qa_values_read") is not False
        or summary.get("labels_created") is not False
        or summary.get("models_loaded") is not False
        or summary.get("model_scores_read") is not False
        or summary.get("one_time_evaluation_consumed") is not False
        or not isinstance(formal_record, dict)
        or formal_record.get("sha256") != formal_lock_sha256
        or formal_record.get("commit_sha256") != formal_lock.get("commit_sha256")
    ):
        raise FinalTestSentinelInventoryError(
            "Landsat inventory is not the untouched, target-blind 2025 lock."
        )

    outputs = summary.get("output_files")
    if not isinstance(outputs, dict):
        raise FinalTestSentinelInventoryError("Landsat inventory output locks are missing.")
    primary_path = directory / PRIMARY_FILENAME
    key_path = directory / KEY_UNIVERSE_FILENAME
    primary_sha = _locked_file(
        primary_path, outputs.get(PRIMARY_FILENAME), label="Primary overpass manifest"
    )
    key_sha = _locked_file(
        key_path, outputs.get(KEY_UNIVERSE_FILENAME), label="Target-blind key universe"
    )

    primary_before = sha256_file(primary_path)
    primary = pd.read_csv(primary_path)
    if sha256_file(primary_path) != primary_before:
        raise FinalTestSentinelInventoryError("Primary overpass manifest changed while being read.")
    required_primary = {"overpass_id", "local_date", "primary_eligible"}
    if primary.empty or required_primary - set(primary):
        raise FinalTestSentinelInventoryError("Primary overpass manifest schema drifted.")
    dates = _civil_2025(primary["local_date"], label="Primary overpass manifest")
    eligibility = primary["primary_eligible"].map(
        lambda value: isinstance(value, (bool, np.bool_)) and bool(value)
    )
    if (
        not eligibility.all()
        or dates.duplicated().any()
        or primary["overpass_id"].astype(str).duplicated().any()
    ):
        raise FinalTestSentinelInventoryError(
            "Primary overpass manifest is not a unique, fully eligible 2025 cohort."
        )

    key_before = sha256_file(key_path)
    keys = pd.read_parquet(key_path)
    if sha256_file(key_path) != key_before:
        raise FinalTestSentinelInventoryError("Key universe changed while being read.")
    if tuple(keys.columns) != KEY_COLUMNS:
        raise FinalTestSentinelInventoryError("Target-blind key-universe schema drifted.")
    key_dates = _civil_2025(keys["target_date"], label="Target-blind key universe")
    keys = keys.copy()
    keys["target_date"] = key_dates
    keys["tract_geoid"] = keys["tract_geoid"].astype(str)
    unique_dates = tuple(sorted(date.strftime("%Y-%m-%d") for date in key_dates.unique()))
    primary_dates = tuple(sorted(date.strftime("%Y-%m-%d") for date in dates.unique()))
    tract_count = int(keys["tract_geoid"].nunique())
    if (
        unique_dates != primary_dates
        or tract_count != EXPECTED_TRACT_COUNT
        or keys.duplicated(["tract_geoid", "target_date"]).any()
        or len(keys) != tract_count * len(unique_dates)
        or keys.groupby("target_date", observed=True)["tract_geoid"].nunique().ne(tract_count).any()
        or summary.get("tract_count") != tract_count
        or summary.get("key_count") != len(keys)
        or summary.get("primary_overpass_count") != len(primary_dates)
    ):
        raise FinalTestSentinelInventoryError(
            "Target-blind keys are not the exact 1,096-tract x 2025-date grid."
        )
    recorded_key_semantic = summary.get("semantic_hashes", {}).get("key_universe")
    if (
        canonical_frame_sha256(keys, sort_by=["target_date", "tract_geoid"])
        != recorded_key_semantic
    ):
        raise FinalTestSentinelInventoryError("Key-universe semantic lock failed.")

    support = summary.get("frozen_support")
    if not isinstance(support, dict):
        raise FinalTestSentinelInventoryError("Frozen spatial support lock is missing.")
    city_value = support.get("city_boundary_path")
    if not isinstance(city_value, str):
        raise FinalTestSentinelInventoryError("Frozen city-boundary path is invalid.")
    city_path = Path(city_value).resolve()
    city_sha = str(support.get("city_boundary_sha256", ""))
    city_geometry_sha = str(support.get("city_boundary_geometry_sha256", ""))
    if (
        not city_path.is_file()
        or sha256_file(city_path) != city_sha
        or len(city_geometry_sha) != 64
        or support.get("tract_count") != EXPECTED_TRACT_COUNT
    ):
        raise FinalTestSentinelInventoryError("Frozen city/support lock failed.")

    return _AuthenticatedInputs(
        landsat_summary_path=summary_path,
        landsat_summary_sha256=summary_sha,
        landsat_commit_sha256=commit,
        primary_path=primary_path,
        primary_sha256=primary_sha,
        key_path=key_path,
        key_sha256=key_sha,
        city_path=city_path,
        city_sha256=city_sha,
        city_geometry_sha256=city_geometry_sha,
        target_dates=primary_dates,
        key_count=len(keys),
        tract_count=tract_count,
    )


def _authenticate_base_inventory(
    output: Path,
    raw_stac: Path,
    authenticated: _AuthenticatedInputs,
) -> tuple[dict[str, Any], str]:
    summary_path = output / INVENTORY_SUMMARY_FILENAME
    summary, summary_sha = _read_json(summary_path, label="Sentinel inventory")
    inputs = summary.get("inputs")
    outputs = summary.get("output_files")
    target_dates = summary.get("target_dates")
    snapshots = summary.get("raw_stac_snapshots")
    if (
        summary.get("state") != "complete"
        or summary.get("artifacts_valid") is not True
        or summary.get("final_test_year") != FINAL_TEST_YEAR
        or summary.get("unlock_final_test") is not True
        or summary.get("global_scene_cloud_cover_filter") is not None
        or summary.get("window_start_days_before_target") != 60
        or summary.get("window_end_days_before_target") != 1
        or not isinstance(inputs, dict)
        or not isinstance(outputs, dict)
        or not isinstance(target_dates, dict)
        or target_dates.get("count") != len(authenticated.target_dates)
        or target_dates.get("minimum") != min(authenticated.target_dates)
        or target_dates.get("maximum") != max(authenticated.target_dates)
        or not isinstance(snapshots, dict)
        or Path(str(snapshots.get("directory", ""))).resolve() != raw_stac
    ):
        raise FinalTestSentinelInventoryError("Sentinel base inventory lock is invalid.")
    primary_input = inputs.get("primary_overpass_manifest")
    city_input = inputs.get("city_boundary")
    if (
        not isinstance(primary_input, dict)
        or Path(str(primary_input.get("path", ""))).resolve() != authenticated.primary_path
        or primary_input.get("sha256") != authenticated.primary_sha256
        or not isinstance(city_input, dict)
        or Path(str(city_input.get("path", ""))).resolve() != authenticated.city_path
        or city_input.get("sha256") != authenticated.city_sha256
    ):
        raise FinalTestSentinelInventoryError(
            "Sentinel inventory does not bind the authenticated Landsat inputs."
        )
    for filename in BASE_OUTPUT_FILENAMES:
        _locked_file(output / filename, outputs.get(filename), label=filename)

    membership_path = output / TARGET_WINDOW_MEMBERSHIP_FILENAME
    before = sha256_file(membership_path)
    membership = pd.read_csv(membership_path)
    if sha256_file(membership_path) != before:
        raise FinalTestSentinelInventoryError("Sentinel membership changed while read.")
    member_dates = _civil_2025(membership["target_date"], label="Sentinel target-window membership")
    acquisition_dates = pd.to_datetime(
        membership["acquisition_local_date"], format="%Y-%m-%d", errors="raise"
    )
    lag = pd.to_numeric(membership["lag_days"], errors="raise")
    if (
        membership.duplicated(["target_date", "physical_acquisition_id"]).any()
        or not set(member_dates.dt.strftime("%Y-%m-%d")).issubset(authenticated.target_dates)
        or not ((member_dates - acquisition_dates).dt.days == lag).all()
        or not lag.between(1, 60).all()
    ):
        raise FinalTestSentinelInventoryError(
            "Sentinel membership violates exact 2025 d-60 through d-1 lineage."
        )
    return summary, summary_sha


def _authenticate_provenance(
    path: Path,
    *,
    formal_lock: dict[str, Any],
    formal_lock_sha256: str,
    authenticated: _AuthenticatedInputs,
    base_summary_sha256: str,
    output: Path,
    raw_stac: Path,
) -> dict[str, Any]:
    payload, _ = _read_json(path, label="Final-test Sentinel provenance")
    _verify_commit(payload, label="Final-test Sentinel provenance")
    if (
        payload.get("state") != "target_blind_inventory_frozen"
        or payload.get("final_test_year") != FINAL_TEST_YEAR
        or payload.get("exact_final_test_year") is not True
        or payload.get("target_blind") is not True
        or payload.get("target_or_qa_tables_read") != []
        or payload.get("target_or_qa_values_read") is not False
        or payload.get("target_assets_opened") is not False
        or payload.get("fitted_models_loaded") is not False
        or payload.get("model_scores_read") is not False
        or payload.get("one_time_evaluation_consumed") is not False
        or payload.get("stac_provider") != STAC_PROVIDER
        or payload.get("earth_search_adapter", {}).get("asset_aliases")
        != EARTH_SEARCH_ASSET_ALIASES
        or payload.get("earth_search_adapter", {}).get("property_derivations")
        != EARTH_SEARCH_PROPERTY_DERIVATIONS
        or payload.get("formal_model_lock", {}).get("sha256") != formal_lock_sha256
        or payload.get("formal_model_lock", {}).get("commit_sha256")
        != formal_lock.get("commit_sha256")
        or payload.get("landsat_inventory", {}).get("sha256")
        != authenticated.landsat_summary_sha256
        or payload.get("landsat_inventory", {}).get("commit_sha256")
        != authenticated.landsat_commit_sha256
        or payload.get("sentinel_inventory", {}).get("sha256") != base_summary_sha256
        or Path(str(payload.get("output_directory", ""))).resolve() != output
        or Path(str(payload.get("raw_stac_directory", ""))).resolve() != raw_stac
    ):
        raise FinalTestSentinelInventoryError(
            "Existing final-test Sentinel provenance does not match frozen inputs."
        )
    return payload


def build_final_test_sentinel_inventory_artifacts(
    *,
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    landsat_inventory_directory: str | Path = ("manifests/final_test_2025/landsat_inventory"),
    output_directory: str | Path = "manifests/final_test_2025/sentinel_inventory",
    raw_stac_directory: str | Path = ("data/raw/final_test_2025/sentinel/stac_items"),
    stac_api: str = DEFAULT_STAC_API,
    client: SentinelSearchClient | None = None,
    query_time_utc: datetime | None = None,
) -> dict[str, Any]:
    """Authenticate frozen metadata, then build the isolated 2025 inventory."""

    root = _project_root()
    formal_path = _resolve(root, formal_lock_path)
    landsat_directory = _resolve(root, landsat_inventory_directory)
    output = _resolve(root, output_directory)
    raw_stac = _resolve(root, raw_stac_directory)
    _require_suffix(
        landsat_directory, LANDSAT_INVENTORY_SUFFIX, label="Landsat inventory directory"
    )
    _require_suffix(output, OUTPUT_SUFFIX, label="Sentinel output directory")
    _require_suffix(raw_stac, RAW_STAC_SUFFIX, label="Sentinel raw STAC directory")

    formal_lock, formal_sha = authenticate_formal_model_lock(formal_path)
    authenticated = _authenticate_landsat_inventory(
        landsat_directory,
        formal_lock=formal_lock,
        formal_lock_sha256=formal_sha,
    )
    marker = output / PROVENANCE_FILENAME
    base_marker = output / INVENTORY_SUMMARY_FILENAME
    if marker.exists():
        _, base_sha = _authenticate_base_inventory(output, raw_stac, authenticated)
        return _authenticate_provenance(
            marker,
            formal_lock=formal_lock,
            formal_lock_sha256=formal_sha,
            authenticated=authenticated,
            base_summary_sha256=base_sha,
            output=output,
            raw_stac=raw_stac,
        )

    if base_marker.exists():
        base_summary, base_sha = _authenticate_base_inventory(output, raw_stac, authenticated)
    else:
        if output.exists() and any(output.iterdir()):
            raise FinalTestSentinelInventoryError(
                "Partial Sentinel output exists without an authenticated commit marker."
            )
        if client is None:
            from pystac_client import Client

            client = Client.open(stac_api)
        with _allow_audited_product_metadata_s3():
            base_summary = build_sentinel_inventory_artifacts(
                city_boundary_path=authenticated.city_path,
                primary_overpass_manifest_path=authenticated.primary_path,
                output_directory=output,
                raw_stac_directory=raw_stac,
                client=_EarthSearchClientAdapter(client),
                unlock_final_test=True,
                final_test_year=FINAL_TEST_YEAR,
                query_time_utc=query_time_utc,
            )
        base_summary, base_sha = _authenticate_base_inventory(output, raw_stac, authenticated)

    pipeline_sha, pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "target_blind_inventory_frozen",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "final_test_year": FINAL_TEST_YEAR,
        "exact_final_test_year": True,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_or_qa_values_read": False,
        "target_assets_opened": False,
        "fitted_models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "global_scene_cloud_cover_filter": None,
        "window_start_days_before_target": 60,
        "window_end_days_before_target": 1,
        "stac_provider": STAC_PROVIDER,
        "stac_api": stac_api,
        "stac_collection": "sentinel-2-l2a",
        "earth_search_adapter": {
            "asset_aliases": EARTH_SEARCH_ASSET_ALIASES,
            "property_derivations": EARTH_SEARCH_PROPERTY_DERIVATIONS,
            "supported_platforms": list(EARTH_SEARCH_SUPPORTED_PLATFORMS),
            "calibration_contract": {
                "id": CALIBRATION_CONTRACT_ID,
                "formula": CALIBRATION_FORMULA,
                "offset_application_order": "after_scale",
                "product_metadata_policy": (
                    "preserve official requester-pays s3 URI; never synthesize HTTPS"
                ),
                "official_sources": list(CALIBRATION_SOURCES),
            },
        },
        "target_date_count": len(authenticated.target_dates),
        "tract_count": authenticated.tract_count,
        "key_count": authenticated.key_count,
        "selected_physical_acquisition_count": base_summary["counts"][
            "selected_physical_acquisitions"
        ],
        "selected_item_count": base_summary["counts"]["selected_items"],
        "target_window_membership_count": base_summary["counts"]["target_window_memberships"],
        "output_directory": str(output),
        "raw_stac_directory": str(raw_stac),
        "formal_model_lock": {
            "path": str(formal_path),
            "sha256": formal_sha,
            "commit_sha256": formal_lock["commit_sha256"],
        },
        "landsat_inventory": {
            "path": str(authenticated.landsat_summary_path),
            "sha256": authenticated.landsat_summary_sha256,
            "commit_sha256": authenticated.landsat_commit_sha256,
            "primary_overpass_manifest": {
                "path": str(authenticated.primary_path),
                "sha256": authenticated.primary_sha256,
            },
            "target_blind_key_universe": {
                "path": str(authenticated.key_path),
                "sha256": authenticated.key_sha256,
            },
        },
        "sentinel_inventory": {
            "path": str(base_marker),
            "sha256": base_sha,
            "semantic_sha256": base_summary["sentinel_inventory_semantic_sha256"],
            "output_files": base_summary["output_files"],
            "raw_stac_snapshots": base_summary["raw_stac_snapshots"],
        },
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload
