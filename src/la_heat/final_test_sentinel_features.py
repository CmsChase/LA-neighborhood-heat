"""Build isolated, target-blind Sentinel-2 predictors for the 2025 test.

The builder authenticates the frozen model and inventory chain, reads only
public Earth Search optical COGs, and decodes every reflectance band with the
frozen STAC ``raster:bands`` rule ``DN * scale + offset``.  The requester-pays
Sentinel product XML is retained as lineage only and is never opened.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT

from la_heat.config import ResearchConfig, load_config
from la_heat.final_test_inventory import (
    FINAL_TEST_YEAR,
    authenticate_formal_model_lock,
)
from la_heat.final_test_inventory import (
    SUMMARY_FILENAME as LANDSAT_SUMMARY_FILENAME,
)
from la_heat.final_test_sentinel_inventory import (
    CALIBRATION_CONTRACT_ID,
    CALIBRATION_CONTRACT_PROPERTY,
    CALIBRATION_FORMULA,
    _authenticate_base_inventory,
    _authenticate_landsat_inventory,
    _authenticate_provenance,
)
from la_heat.final_test_sentinel_inventory import (
    PROVENANCE_FILENAME as SENTINEL_PROVENANCE_FILENAME,
)
from la_heat.grid import build_fixed_grid
from la_heat.landmask import land_classes_to_mask
from la_heat.landsat import zonal_mask_identity_hashes
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_compile_adapter import compile_outputs_from_current_caches
from la_heat.sentinel_feature_builder import (
    FixedSpatialSupport,
    FrozenSentinelInputs,
    SentinelStageConfig,
    _acquisition_cache_directory,
    _acquisition_cache_is_current,
    _expected_acquisition_lock,
    _fixed_grid_from_lock,
    _raster_environment,
    _validate_native_asset_grid,
    load_sentinel_stage_config,
)
from la_heat.sentinel_features import (
    REFLECTANCE_BANDS,
    AlignedSentinelTile,
    aggregate_acquisition_to_tracts,
    clear_land_mask,
    compute_optical_indices,
    mosaic_aligned_tiles,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-sentinel-features-v1-earth-search-cog"
RUNNER_VERSION: Final = "final-test-sentinel-runner-v1"
OUTPUT_SUFFIX: Final = Path("data/interim/final_test_2025/sentinel")
INVENTORY_SUFFIX: Final = Path("manifests/final_test_2025/sentinel_inventory")
RAW_STAC_SUFFIX: Final = Path("data/raw/final_test_2025/sentinel/stac_items")
STATUS_FILENAME: Final = "status.json"
PAUSE_FILENAME: Final = "PAUSE_REQUESTED"
WORLD_COVER_URL: Final = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v100/2020/map/"
    "ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
)
WORLD_COVER_FILENAME: Final = "ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
WORLD_COVER_EXPECTED_BYTES: Final = 104_207_525
EARTH_SEARCH_COG_HOST: Final = "sentinel-cogs.s3.us-west-2.amazonaws.com"
EXPECTED_TRACT_COUNT: Final = 1_096
EXPECTED_ACQUISITION_COUNT: Final = 34
EXPECTED_ITEM_COUNT: Final = 67
PIPELINE_FILES: Final = (
    "pyproject.toml",
    "scripts/build_final_test_sentinel_features.py",
    "src/la_heat/config.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/final_test_sentinel_features.py",
    "src/la_heat/final_test_sentinel_inventory.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_compile_adapter.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/sentinel_inventory.py",
)


class FinalTestSentinelFeatureError(RuntimeError):
    """Raised when a frozen input or target-blind feature invariant fails."""


class FinalTestSentinelEngineAlreadyRunningError(RuntimeError):
    """Raised when another builder owns the isolated final-test output."""


class FinalTestSentinelEngineLock:
    """OS-released single-instance lock owned by the builder, not the UI."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> FinalTestSentinelEngineLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise FinalTestSentinelEngineAlreadyRunningError(
                "The final Sentinel feature engine is already running."
            ) from error
        self._stream = stream
        return self

    def __exit__(self, *_args: object) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


@dataclass(frozen=True, slots=True)
class CogCalibration:
    """Per-item, frozen Earth Search COG calibration metadata."""

    item_id: str
    snapshot_path: Path
    snapshot_sha256: str
    band_scale_offsets: Mapping[str, tuple[float, float]]
    calibration_sha256: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSentinelInputs:
    """Fully authenticated inventory plus its frozen COG contracts."""

    inventory: FrozenSentinelInputs
    predictor_research: ResearchConfig
    formal_lock: dict[str, Any]
    formal_lock_sha256: str
    final_inventory_provenance: dict[str, Any]
    contracts: Mapping[str, CogCalibration]
    city_path: Path
    locks: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BuildInputs:
    """All target-blind inputs required by the acquisition runner."""

    authenticated: AuthenticatedSentinelInputs
    spatial: FixedSpatialSupport
    stage: SentinelStageConfig
    base_lock: dict[str, str]
    output_directory: Path
    runner_sha256: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _require_suffix(path: Path, suffix: Path, *, label: str) -> None:
    actual = tuple(part.casefold() for part in path.resolve().parts)
    expected = tuple(part.casefold() for part in suffix.parts)
    if len(actual) < len(expected) or actual[-len(expected) :] != expected:
        raise FinalTestSentinelFeatureError(
            f"{label} must end in the isolated path {suffix.as_posix()}."
        )


def _read_json_stable(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestSentinelFeatureError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestSentinelFeatureError(f"{label} changed or is not a JSON object.")
    return payload, before


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestSentinelFeatureError(f"{label} canonical commit is invalid.")
    return recorded


def _verify_file_record(path: Path, record: object, *, label: str) -> str:
    if not isinstance(record, dict):
        raise FinalTestSentinelFeatureError(f"{label} lock record is missing.")
    recorded_path = record.get("path")
    if recorded_path is not None and Path(str(recorded_path)).resolve() != path.resolve():
        raise FinalTestSentinelFeatureError(f"{label} path lock changed.")
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise FinalTestSentinelFeatureError(f"{label} byte lock failed.")
    return str(record["sha256"])


def _predictor_research_config(path: Path) -> tuple[ResearchConfig, str]:
    """Grant in-memory predictor access without changing the locked config file."""

    source = load_config(path)
    if source.final_test_year != FINAL_TEST_YEAR or source.final_test_unlocked:
        raise FinalTestSentinelFeatureError(
            "The source research config must keep the 2025 final test locked."
        )
    raw = copy.deepcopy(source.raw)
    raw["study"]["unlock_final_test"] = True
    return ResearchConfig(raw=raw, path=source.path), sha256_file(source.path)


def validate_exact_lag_membership(
    membership: pd.DataFrame,
    *,
    acquisition_dates: Mapping[str, str] | None = None,
    target_dates: Sequence[str] | None = None,
) -> None:
    """Fail closed unless every membership is exactly a local d-60..d-1 row."""

    required = {
        "target_date",
        "physical_acquisition_id",
        "acquisition_local_date",
        "lag_days",
    }
    if membership.empty or required - set(membership):
        raise FinalTestSentinelFeatureError("Sentinel membership schema is incomplete.")
    target = pd.to_datetime(
        membership["target_date"], format="%Y-%m-%d", errors="raise"
    )
    acquired = pd.to_datetime(
        membership["acquisition_local_date"], format="%Y-%m-%d", errors="raise"
    )
    lag = pd.to_numeric(membership["lag_days"], errors="raise")
    if (
        not target.dt.year.eq(FINAL_TEST_YEAR).all()
        or membership.duplicated(["target_date", "physical_acquisition_id"]).any()
        or not ((target - acquired).dt.days == lag).all()
        or not lag.between(1, 60).all()
    ):
        raise FinalTestSentinelFeatureError(
            "Sentinel membership violates exact local d-60 through d-1 lineage."
        )
    if (acquisition_dates is None) != (target_dates is None):
        raise ValueError(
            "acquisition_dates and target_dates must be supplied together."
        )
    if acquisition_dates is None or target_dates is None:
        return

    normalized_acquisitions = {
        str(identifier): pd.Timestamp(value).normalize()
        for identifier, value in acquisition_dates.items()
    }
    normalized_targets = tuple(
        sorted({pd.Timestamp(value).normalize() for value in target_dates})
    )
    if (
        not normalized_acquisitions
        or not normalized_targets
        or any(value.year != FINAL_TEST_YEAR for value in normalized_targets)
        or set(target.dt.normalize().tolist()) != set(normalized_targets)
    ):
        raise FinalTestSentinelFeatureError(
            "Sentinel membership does not cover the frozen target dates."
        )
    expected = {
        (
            target_date.strftime("%Y-%m-%d"),
            identifier,
            acquisition_date.strftime("%Y-%m-%d"),
            int((target_date - acquisition_date).days),
        )
        for target_date in normalized_targets
        for identifier, acquisition_date in normalized_acquisitions.items()
        if 1 <= int((target_date - acquisition_date).days) <= 60
    }
    observed = {
        (
            target_date.strftime("%Y-%m-%d"),
            str(identifier),
            acquisition_date.strftime("%Y-%m-%d"),
            int(lag_days),
        )
        for target_date, identifier, acquisition_date, lag_days in zip(
            target,
            membership["physical_acquisition_id"],
            acquired,
            lag,
            strict=True,
        )
    }
    if observed != expected or len(observed) != len(membership):
        raise FinalTestSentinelFeatureError(
            "Sentinel membership is not the exact frozen target/acquisition crosswalk."
        )


def _canonical_public_cog_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != EARTH_SEARCH_COG_HOST
        or not parsed.path.lower().endswith(".tif")
        or parsed.query
        or parsed.fragment
    ):
        raise FinalTestSentinelFeatureError(
            "Sentinel optical inputs must be public frozen Earth Search HTTPS COGs."
        )
    return urlunsplit(("https", EARTH_SEARCH_COG_HOST, parsed.path, "", ""))


def _extract_cog_calibration(
    item_row: Any,
    *,
    snapshot_path: Path,
    expected_snapshot_sha256: str,
) -> CogCalibration:
    payload, observed_sha = _read_json_stable(
        snapshot_path, label=f"frozen STAC snapshot {item_row.item_id}"
    )
    if observed_sha != expected_snapshot_sha256 or payload.get("id") != str(item_row.item_id):
        raise FinalTestSentinelFeatureError("Frozen STAC snapshot identity/hash changed.")
    properties = payload.get("properties")
    assets = payload.get("assets")
    if not isinstance(properties, dict) or not isinstance(assets, dict):
        raise FinalTestSentinelFeatureError("Frozen STAC snapshot schema changed.")
    contract = properties.get(CALIBRATION_CONTRACT_PROPERTY)
    if (
        not isinstance(contract, dict)
        or contract.get("id") != CALIBRATION_CONTRACT_ID
        or contract.get("formula") != CALIBRATION_FORMULA
        or contract.get("offset_application_order") != "after_scale"
        or contract.get("product_metadata_use")
        != "audit_lineage_only_requester_pays_s3"
    ):
        raise FinalTestSentinelFeatureError("Frozen Earth Search calibration contract changed.")
    product_uri = str(contract.get("product_metadata_uri", ""))
    if (
        not product_uri.startswith("s3://sentinel-s2-l2a/")
        or product_uri != str(item_row.asset_product_metadata_href)
    ):
        raise FinalTestSentinelFeatureError("Requester-pays XML lineage URI changed.")
    contract_bands = contract.get("bands")
    if not isinstance(contract_bands, dict) or set(contract_bands) != set(
        REFLECTANCE_BANDS
    ):
        raise FinalTestSentinelFeatureError("COG calibration bands are incomplete.")

    scale_offsets: dict[str, tuple[float, float]] = {}
    semantic_bands: dict[str, dict[str, float]] = {}
    for band in REFLECTANCE_BANDS:
        band_contract = contract_bands.get(band)
        asset = assets.get(band)
        if not isinstance(band_contract, dict) or not isinstance(asset, dict):
            raise FinalTestSentinelFeatureError(f"Frozen {band} metadata is incomplete.")
        raster_bands = asset.get("raster:bands")
        if not isinstance(raster_bands, list) or len(raster_bands) != 1:
            raise FinalTestSentinelFeatureError(f"Frozen {band} raster metadata changed.")
        raster_band = raster_bands[0]
        if not isinstance(raster_band, dict):
            raise FinalTestSentinelFeatureError(f"Frozen {band} raster metadata is invalid.")
        scale = band_contract.get("scale")
        offset = band_contract.get("offset")
        if (
            isinstance(scale, bool)
            or isinstance(offset, bool)
            or not isinstance(scale, (int, float))
            or not isinstance(offset, (int, float))
            or not math.isfinite(float(scale))
            or not math.isfinite(float(offset))
            or float(scale) <= 0
            or float(raster_band.get("scale", math.nan)) != float(scale)
            or float(raster_band.get("offset", math.nan)) != float(offset)
        ):
            raise FinalTestSentinelFeatureError(f"Frozen {band} scale/offset changed.")
        selected_href = _canonical_public_cog_url(
            str(getattr(item_row, f"asset_{band.lower()}_href"))
        )
        if (
            _canonical_public_cog_url(str(asset.get("href", ""))) != selected_href
            or _canonical_public_cog_url(str(band_contract.get("href", "")))
            != selected_href
        ):
            raise FinalTestSentinelFeatureError(f"Frozen {band} COG URL changed.")
        scale_offsets[band] = (float(scale), float(offset))
        semantic_bands[band] = {"scale": float(scale), "offset": float(offset)}

    scl_asset = assets.get("SCL")
    if (
        not isinstance(scl_asset, dict)
        or _canonical_public_cog_url(str(scl_asset.get("href", "")))
        != _canonical_public_cog_url(str(item_row.asset_scl_href))
    ):
        raise FinalTestSentinelFeatureError("Frozen SCL COG URL changed.")
    calibration_sha = canonical_sha256(
        {"id": CALIBRATION_CONTRACT_ID, "bands": semantic_bands}
    )
    return CogCalibration(
        item_id=str(item_row.item_id),
        snapshot_path=snapshot_path.resolve(),
        snapshot_sha256=observed_sha,
        band_scale_offsets=scale_offsets,
        calibration_sha256=calibration_sha,
    )


def _authenticate_snapshot_files(
    records: object,
    *,
    raw_directory: Path,
) -> dict[str, Path]:
    if not isinstance(records, list) or not records:
        raise FinalTestSentinelFeatureError("Frozen raw STAC snapshot records are missing.")
    if canonical_sha256(records) == "":
        raise AssertionError("Unreachable canonical hash state.")
    by_item: dict[str, Path] = {}
    filenames: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise FinalTestSentinelFeatureError("Raw STAC snapshot record is invalid.")
        item_id = str(record.get("item_id", ""))
        filename = str(record.get("filename", ""))
        path = (raw_directory / filename).resolve()
        if (
            not item_id
            or not filename
            or path.parent != raw_directory.resolve()
            or item_id in by_item
            or filename in filenames
            or not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise FinalTestSentinelFeatureError("Raw STAC snapshot byte/set lock failed.")
        by_item[item_id] = path
        filenames.add(filename)
    return by_item


def authenticate_final_sentinel_inputs(
    *,
    project_root: Path,
    research_config_path: Path,
    formal_lock_path: Path,
    landsat_inventory_directory: Path,
    sentinel_inventory_directory: Path,
    raw_stac_directory: Path,
) -> AuthenticatedSentinelInputs:
    """Authenticate every formal, Landsat, Sentinel, and snapshot byte lock."""

    _require_suffix(
        sentinel_inventory_directory, INVENTORY_SUFFIX, label="Sentinel inventory"
    )
    _require_suffix(raw_stac_directory, RAW_STAC_SUFFIX, label="Raw STAC directory")
    formal_lock, formal_sha = authenticate_formal_model_lock(formal_lock_path)
    predictor_research, research_file_sha = _predictor_research_config(
        research_config_path
    )

    landsat_path = landsat_inventory_directory / LANDSAT_SUMMARY_FILENAME
    landsat, landsat_sha = _read_json_stable(
        landsat_path, label="final-test Landsat inventory"
    )
    _verify_commit(landsat, label="final-test Landsat inventory")
    for filename, record in landsat.get("output_files", {}).items():
        _verify_file_record(
            landsat_inventory_directory / filename,
            record,
            label=f"Landsat {filename}",
        )
    landsat_auth = _authenticate_landsat_inventory(
        landsat_inventory_directory,
        formal_lock=formal_lock,
        formal_lock_sha256=formal_sha,
    )

    base, base_sha = _authenticate_base_inventory(
        sentinel_inventory_directory, raw_stac_directory, landsat_auth
    )
    provenance_path = sentinel_inventory_directory / SENTINEL_PROVENANCE_FILENAME
    provenance = _authenticate_provenance(
        provenance_path,
        formal_lock=formal_lock,
        formal_lock_sha256=formal_sha,
        authenticated=landsat_auth,
        base_summary_sha256=base_sha,
        output=sentinel_inventory_directory,
        raw_stac=raw_stac_directory,
    )
    if (
        provenance.get("landsat_inventory", {}).get("sha256") != landsat_sha
        or provenance.get("sentinel_inventory", {}).get("sha256") != base_sha
    ):
        raise FinalTestSentinelFeatureError("Final inventory provenance chain changed.")
    snapshots = base.get("raw_stac_snapshots")
    if not isinstance(snapshots, dict):
        raise FinalTestSentinelFeatureError("Raw STAC snapshot set lock is missing.")
    records = snapshots.get("files")
    if (
        not isinstance(records, list)
        or snapshots.get("count") != len(records)
        or snapshots.get("set_sha256") != canonical_sha256(records)
        or Path(str(snapshots.get("directory", ""))).resolve()
        != raw_stac_directory.resolve()
    ):
        raise FinalTestSentinelFeatureError("Raw STAC snapshot set hash changed.")
    snapshot_paths = _authenticate_snapshot_files(
        records, raw_directory=raw_stac_directory
    )

    inventory = FrozenSentinelInputs(
        acquisitions=pd.read_csv(
            sentinel_inventory_directory / "selected_acquisitions.csv",
            dtype={"processing_baseline": "string"},
        ),
        items=pd.read_csv(
            sentinel_inventory_directory / "selected_items.csv",
            dtype={"processing_baseline": "string"},
        ),
        membership=pd.read_csv(
            sentinel_inventory_directory / "target_window_membership.csv"
        ),
        summary=base,
        locks={
            "sentinel_inventory_summary_sha256": base_sha,
            "sentinel_inventory_semantic_sha256": str(
                base["sentinel_inventory_semantic_sha256"]
            ),
            **{
                f"sentinel_{name.replace('.', '_')}_sha256": str(record["sha256"])
                for name, record in base["output_files"].items()
            },
        },
    )
    acquisition_dates = dict(
        zip(
            inventory.acquisitions["physical_acquisition_id"].astype(str),
            inventory.acquisitions["acquisition_local_date"].astype(str),
            strict=True,
        )
    )
    validate_exact_lag_membership(
        inventory.membership,
        acquisition_dates=acquisition_dates,
        target_dates=landsat_auth.target_dates,
    )
    if (
        len(inventory.acquisitions) != EXPECTED_ACQUISITION_COUNT
        or len(inventory.items) != EXPECTED_ITEM_COUNT
        or
        inventory.acquisitions["physical_acquisition_id"].duplicated().any()
        or inventory.items["item_id"].duplicated().any()
        or set(inventory.items["physical_acquisition_id"])
        != set(inventory.acquisitions["physical_acquisition_id"])
    ):
        raise FinalTestSentinelFeatureError("Frozen Sentinel acquisition cohort changed.")

    contracts: dict[str, CogCalibration] = {}
    for item in inventory.items.itertuples(index=False):
        item_id = str(item.item_id)
        if (
            item_id not in snapshot_paths
            or str(item.snapshot_sha256)
            != next(
                str(record["sha256"])
                for record in records
                if str(record["item_id"]) == item_id
            )
        ):
            raise FinalTestSentinelFeatureError("Selected item snapshot lineage changed.")
        contracts[item_id] = _extract_cog_calibration(
            item,
            snapshot_path=snapshot_paths[item_id],
            expected_snapshot_sha256=str(item.snapshot_sha256),
        )
    if set(contracts) != set(inventory.items["item_id"].astype(str)):
        raise FinalTestSentinelFeatureError("Not every selected item has a COG contract.")

    locks = {
        "formal_model_lock_sha256": formal_sha,
        "formal_model_lock_commit_sha256": str(formal_lock["commit_sha256"]),
        "landsat_inventory_sha256": landsat_sha,
        "landsat_inventory_commit_sha256": str(landsat["commit_sha256"]),
        "final_sentinel_inventory_provenance_sha256": sha256_file(provenance_path),
        "final_sentinel_inventory_commit_sha256": str(provenance["commit_sha256"]),
        "raw_stac_snapshot_set_sha256": str(snapshots["set_sha256"]),
        "raw_stac_snapshot_calibration_set_sha256": canonical_sha256(
            {
                item_id: contract.calibration_sha256
                for item_id, contract in sorted(contracts.items())
            }
        ),
        "research_config_file_sha256_audit_only": research_file_sha,
        **inventory.locks,
    }
    return AuthenticatedSentinelInputs(
        inventory=inventory,
        predictor_research=predictor_research,
        formal_lock=formal_lock,
        formal_lock_sha256=formal_sha,
        final_inventory_provenance=provenance,
        contracts=contracts,
        city_path=landsat_auth.city_path,
        locks=locks,
    )


_CONTENT_RANGE = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$")


def _download_worldcover(
    path: Path,
    *,
    maximum_attempts: int = 5,
    retry_backoff_seconds: float = 2.0,
) -> None:
    """Download the frozen tile atomically with verified HTTP Range resume."""

    if maximum_attempts < 1 or retry_backoff_seconds < 0:
        raise ValueError("WorldCover retry settings are invalid.")
    parsed = urlsplit(WORLD_COVER_URL)
    if parsed.scheme != "https" or parsed.netloc != "esa-worldcover.s3.eu-central-1.amazonaws.com":
        raise FinalTestSentinelFeatureError("Frozen WorldCover source URL changed.")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists() and partial.stat().st_size > WORLD_COVER_EXPECTED_BYTES:
        partial.unlink(missing_ok=True)
    if partial.exists() and partial.stat().st_size == WORLD_COVER_EXPECTED_BYTES:
        partial.replace(path)
        return

    last_error: requests.RequestException | None = None
    for attempt in range(1, maximum_attempts + 1):
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        try:
            with requests.get(
                WORLD_COVER_URL,
                stream=True,
                timeout=(20, 180),
                allow_redirects=False,
                headers=headers,
            ) as response:
                response.raise_for_status()
                mode = "wb"
                expected_response_bytes = WORLD_COVER_EXPECTED_BYTES
                if existing and response.status_code == 206:
                    match = _CONTENT_RANGE.fullmatch(
                        str(response.headers.get("Content-Range", ""))
                    )
                    if (
                        match is None
                        or int(match.group("start")) != existing
                        or int(match.group("total")) != WORLD_COVER_EXPECTED_BYTES
                    ):
                        raise FinalTestSentinelFeatureError(
                            "WorldCover Range response does not resume the frozen file."
                        )
                    mode = "ab"
                    expected_response_bytes = WORLD_COVER_EXPECTED_BYTES - existing
                elif response.status_code != 200:
                    raise FinalTestSentinelFeatureError(
                        "WorldCover server returned an unexpected HTTP response."
                    )
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != expected_response_bytes:
                    raise FinalTestSentinelFeatureError(
                        "Frozen WorldCover response byte length changed."
                    )
                with partial.open(mode) as handle:
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        if not block:
                            continue
                        handle.write(block)
                        if handle.tell() > WORLD_COVER_EXPECTED_BYTES:
                            raise FinalTestSentinelFeatureError(
                                "WorldCover stream exceeded its frozen byte length."
                            )
                    handle.flush()
                    os.fsync(handle.fileno())
            if partial.stat().st_size == WORLD_COVER_EXPECTED_BYTES:
                partial.replace(path)
                return
            raise requests.ConnectionError("WorldCover response ended early.")
        except requests.RequestException as error:
            last_error = error
            if attempt == maximum_attempts:
                break
            time.sleep(retry_backoff_seconds * (2 ** (attempt - 1)))
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    raise FinalTestSentinelFeatureError(
        "WorldCover download remained incomplete after bounded retries."
    ) from last_error


def _read_worldcover_mask(path: Path, *, target_grid: Any) -> np.ndarray:
    with rasterio.Env(**_raster_environment()):
        with rasterio.open(path) as source:
            with WarpedVRT(
                source,
                crs=target_grid.crs,
                transform=target_grid.transform,
                height=target_grid.height,
                width=target_grid.width,
                resampling=Resampling.mode,
                nodata=0,
            ) as warped:
                classes = warped.read(1)
    return land_classes_to_mask(classes, nodata_class=0, water_classes=[80])


def validate_fixed_support_arrays(
    *,
    zones: np.ndarray,
    eligible_land: np.ndarray,
    tract_geoids: Sequence[str],
    audit: pd.DataFrame,
    grid_identity: str,
    expected_zone_sha256: str,
    expected_land_sha256: str,
) -> tuple[dict[str, int], dict[str, str]]:
    """Verify global and per-tract fixed-support hashes without target tables."""

    zone_values = np.asarray(zones)
    eligible = np.asarray(eligible_land, dtype=bool)
    geoids = tuple(str(value) for value in tract_geoids)
    if zone_values.shape != eligible.shape:
        raise FinalTestSentinelFeatureError("Zone and eligible-land rasters disagree.")
    if hashlib.sha256(zone_values.tobytes()).hexdigest() != expected_zone_sha256:
        raise FinalTestSentinelFeatureError("Frozen tract zone raster hash changed.")
    if (
        hashlib.sha256(np.packbits(eligible.ravel()).tobytes()).hexdigest()
        != expected_land_sha256
    ):
        raise FinalTestSentinelFeatureError("Frozen WorldCover eligible-land hash changed.")
    if set(np.unique(zone_values[zone_values > 0])) != set(range(1, len(geoids) + 1)):
        raise FinalTestSentinelFeatureError("Zone raster does not contain every tract.")
    required = {
        "tract_geoid",
        "eligible_pixel_count_static",
        "eligible_pixel_identity_sha256",
    }
    if audit.empty or required - set(audit):
        raise FinalTestSentinelFeatureError("Static feature audit support schema changed.")
    support = audit.loc[:, list(required)].copy()
    support["tract_geoid"] = support["tract_geoid"].astype(str)
    if support["tract_geoid"].duplicated().any() or set(support["tract_geoid"]) != set(
        geoids
    ):
        raise FinalTestSentinelFeatureError("Static feature audit tract universe changed.")
    selected = (zone_values > 0) & eligible
    observed_counts = np.bincount(
        zone_values[selected], minlength=len(geoids) + 1
    )[1:]
    observed_identities = zonal_mask_identity_hashes(
        zone_values,
        selected,
        zone_count=len(geoids),
        grid_identity=grid_identity,
    )
    indexed = support.set_index("tract_geoid")
    expected_counts = np.array(
        [int(indexed.at[geoid, "eligible_pixel_count_static"]) for geoid in geoids]
    )
    expected_identities = [
        str(indexed.at[geoid, "eligible_pixel_identity_sha256"]) for geoid in geoids
    ]
    if not np.array_equal(observed_counts, expected_counts):
        raise FinalTestSentinelFeatureError(
            "WorldCover eligible-land denominators changed."
        )
    if observed_identities != expected_identities:
        raise FinalTestSentinelFeatureError(
            "WorldCover eligible-land pixel identities changed."
        )
    return (
        dict(zip(geoids, observed_counts.astype(int), strict=True)),
        dict(zip(geoids, observed_identities, strict=True)),
    )


def authenticate_fixed_spatial_support(
    *,
    project_root: Path,
    authenticated: AuthenticatedSentinelInputs,
    stage: SentinelStageConfig,
    worldcover_path: Path,
) -> FixedSpatialSupport:
    """Rebuild support from frozen static artifacts, never development target/QA."""

    provenance_path = (
        project_root
        / "data/processed/static_features/static_features_provenance.json"
    )
    provenance, provenance_sha = _read_json_stable(
        provenance_path, label="static feature provenance"
    )
    provenance_commit = _verify_commit(provenance, label="static feature provenance")
    if (
        provenance.get("state") != "complete"
        or provenance.get("promoted_outputs_valid") is not True
        or provenance.get("contains_date_column") is not False
        or provenance.get("contains_2025_rows") is not False
        or provenance.get("unique_geoid_count") != EXPECTED_TRACT_COUNT
    ):
        raise FinalTestSentinelFeatureError(
            "Static feature provenance is not the frozen target-blind support."
        )
    output_directory = Path(str(provenance.get("output_directory", ""))).resolve()
    for filename, record in provenance.get("output_files", {}).items():
        _verify_file_record(
            output_directory / filename, record, label=f"Static {filename}"
        )
    audit_path = output_directory / "static_feature_audit.parquet"
    audit_before = sha256_file(audit_path)
    audit = pd.read_parquet(audit_path)
    if (
        sha256_file(audit_path) != audit_before
        or canonical_frame_sha256(audit, sort_by=["tract_geoid"])
        != provenance.get("semantic_audit_table_sha256")
    ):
        raise FinalTestSentinelFeatureError("Static support audit semantic lock failed.")

    grid_lock_path = project_root / "data/interim/targets/fixed_grid_lock.json"
    grid_lock, grid_lock_sha = _read_json_stable(
        grid_lock_path, label="target fixed-grid lock"
    )
    target_locks = provenance.get("target_support_locks")
    if not isinstance(target_locks, dict):
        raise FinalTestSentinelFeatureError("Static target-support locks are missing.")
    comparisons = {
        "target_grid_identity_sha256": "target_grid_identity_sha256",
        "grid_definition_sha256": "grid_definition_sha256",
        "zone_raster_sha256": "zone_raster_sha256",
        "static_land_mask_sha256": "static_land_mask_sha256",
    }
    if any(
        grid_lock.get(grid_key) != target_locks.get(static_key)
        for grid_key, static_key in comparisons.items()
    ):
        raise FinalTestSentinelFeatureError("Static and target grid locks disagree.")
    target_grid = _fixed_grid_from_lock(grid_lock)

    tract_path = project_root / "data/interim/targets/primary_tract_manifest.parquet"
    if sha256_file(tract_path) != target_locks.get("tract_manifest_file_sha256"):
        raise FinalTestSentinelFeatureError("Primary tract manifest byte lock failed.")
    tracts = gpd.read_parquet(tract_path).reset_index(drop=True)
    if (
        tracts.crs is None
        or "GEOID" not in tracts
        or len(tracts) != EXPECTED_TRACT_COUNT
        or tracts["GEOID"].astype(str).duplicated().any()
        or not tracts["primary_included"].all()
        or set(tracts["tract_manifest_sha256"].astype(str))
        != {str(target_locks["tract_manifest_sha256"])}
    ):
        raise FinalTestSentinelFeatureError("Primary tract universe lock failed.")
    tract_geoids = tuple(tracts["GEOID"].astype(str))
    projected = tracts.to_crs(target_grid.crs)
    zones = rasterize(
        ((geometry, index + 1) for index, geometry in enumerate(projected.geometry)),
        out_shape=target_grid.shape,
        transform=target_grid.transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )

    if not worldcover_path.is_file():
        _download_worldcover(worldcover_path)
    eligible_land = _read_worldcover_mask(worldcover_path, target_grid=target_grid)
    eligible_counts, identities = validate_fixed_support_arrays(
        zones=zones,
        eligible_land=eligible_land,
        tract_geoids=tract_geoids,
        audit=audit,
        grid_identity=str(grid_lock["target_grid_identity_sha256"]),
        expected_zone_sha256=str(grid_lock["zone_raster_sha256"]),
        expected_land_sha256=str(grid_lock["static_land_mask_sha256"]),
    )

    city = gpd.read_file(authenticated.city_path)
    grid_config = stage.raw["grid"]
    optical_grid = build_fixed_grid(
        city,
        target_crs=str(grid_config["crs"]),
        resolution_m=float(grid_config["resolution_m"]),
        anchor_x_m=float(grid_config["edge_anchor_x_m"]),
        anchor_y_m=float(grid_config["edge_anchor_y_m"]),
    )
    target_dates = tuple(
        sorted(authenticated.inventory.membership["target_date"].astype(str).unique())
    )
    locks = {
        "static_feature_provenance_sha256": provenance_sha,
        "static_feature_provenance_commit_sha256": provenance_commit,
        "static_feature_audit_sha256": audit_before,
        "target_grid_lock_sha256": grid_lock_sha,
        "target_grid_identity_sha256": str(
            grid_lock["target_grid_identity_sha256"]
        ),
        "target_grid_definition_sha256": target_grid.sha256,
        "zone_raster_sha256": str(grid_lock["zone_raster_sha256"]),
        "static_land_mask_sha256": str(grid_lock["static_land_mask_sha256"]),
        "tract_manifest_file_sha256": sha256_file(tract_path),
        "tract_manifest_semantic_sha256": str(
            target_locks["tract_manifest_sha256"]
        ),
        "worldcover_source_file_sha256_audit_only": sha256_file(worldcover_path),
        "optical_grid_definition_sha256": optical_grid.sha256,
    }
    return FixedSpatialSupport(
        target_grid=target_grid,
        optical_grid=optical_grid,
        zones=zones,
        eligible_land=eligible_land,
        tracts=tracts,
        tract_geoids=tract_geoids,
        eligible_counts=eligible_counts,
        eligible_identity_sha256s=identities,
        target_dates=target_dates,
        locks=locks,
    )


def decode_cog_reflectance(
    digital_number: np.ndarray,
    *,
    scale: float,
    offset: float,
    nodata_dn: int = 0,
    saturated_dn: int = 65535,
) -> np.ndarray:
    """Decode an Earth Search COG as ``DN * scale + offset`` after averaging."""

    if (
        not math.isfinite(scale)
        or scale <= 0
        or not math.isfinite(offset)
    ):
        raise ValueError("COG scale and offset must be finite; scale must be positive.")
    values = np.asarray(digital_number)
    if values.ndim != 2:
        raise ValueError("A Sentinel COG band must be two-dimensional.")
    numeric = values.astype(np.float32, copy=False)
    valid = (
        np.isfinite(numeric)
        & (numeric != float(nodata_dn))
        & (numeric != float(saturated_dn))
    )
    output = np.full(values.shape, np.nan, dtype=np.float32)
    output[valid] = (
        numeric[valid] * np.float32(scale) + np.float32(offset)
    )
    return output


def _read_public_asset_to_optical_grid(
    unsigned_url: str,
    *,
    grid: Any,
    categorical: bool,
    saturated_dn: int = 65535,
) -> np.ndarray:
    """Read a frozen public COG directly; no Planetary Computer signing/query."""

    from rasterio.warp import reproject

    url = _canonical_public_cog_url(unsigned_url)
    if categorical:
        destination = np.zeros(grid.shape, dtype=np.uint8)
        resampling = Resampling.nearest
        dst_nodata: int | float = 0
    else:
        destination = np.full(grid.shape, np.nan, dtype=np.float32)
        saturation_max = np.zeros(grid.shape, dtype=np.float32)
        resampling = Resampling.average
        dst_nodata = np.nan
    with rasterio.Env(**_raster_environment()):
        with rasterio.open(url) as source:
            _validate_native_asset_grid(source, grid=grid, categorical=categorical)
            source_nodata = source.nodata if source.nodata is not None else 0
            reproject(
                source=rasterio.band(source, 1),
                destination=destination,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source_nodata,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=dst_nodata,
                resampling=resampling,
                init_dest_nodata=True,
            )
            if not categorical:
                reproject(
                    source=rasterio.band(source, 1),
                    destination=saturation_max,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source_nodata,
                    dst_transform=grid.transform,
                    dst_crs=grid.crs,
                    dst_nodata=0,
                    resampling=Resampling.max,
                    init_dest_nodata=True,
                )
                destination[saturation_max == float(saturated_dn)] = np.nan
    return destination


def _process_acquisition(
    acquisition_row: Any,
    *,
    item_rows: pd.DataFrame,
    contracts: Mapping[str, CogCalibration],
    spatial: FixedSpatialSupport,
    stage: SentinelStageConfig,
    base_lock: dict[str, str],
    output_directory: Path,
    force: bool,
) -> dict[str, Any]:
    physical_id = str(acquisition_row.physical_acquisition_id)
    directory = _acquisition_cache_directory(output_directory, physical_id)
    expected_lock = _expected_acquisition_lock(
        base_lock=base_lock, physical_id=physical_id, item_rows=item_rows
    )
    if not force and _acquisition_cache_is_current(
        directory, expected_lock=expected_lock
    ):
        return json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "summary.json"
    summary_path.unlink(missing_ok=True)

    qa = stage.raw["qa"]
    aligned_tiles: list[AlignedSentinelTile] = []
    snapshot_records: list[dict[str, Any]] = []
    for item in item_rows.sort_values(["mgrs_tile", "item_id"]).itertuples(index=False):
        item_id = str(item.item_id)
        calibration = contracts[item_id]
        scl = _read_public_asset_to_optical_grid(
            str(item.asset_scl_href), grid=spatial.optical_grid, categorical=True
        )
        reflectance: dict[str, np.ndarray] = {}
        for band in REFLECTANCE_BANDS:
            dn = _read_public_asset_to_optical_grid(
                str(getattr(item, f"asset_{band.lower()}_href")),
                grid=spatial.optical_grid,
                categorical=False,
                saturated_dn=int(qa["saturated_dn"]),
            )
            scale, offset = calibration.band_scale_offsets[band]
            reflectance[band] = decode_cog_reflectance(
                dn,
                scale=scale,
                offset=offset,
                nodata_dn=int(qa["nodata_dn"]),
                saturated_dn=int(qa["saturated_dn"]),
            )
        aligned_tiles.append(
            AlignedSentinelTile(
                item_id=item_id,
                mgrs_tile=str(item.mgrs_tile),
                scl=scl,
                reflectance=reflectance,
                calibration_sha256=calibration.calibration_sha256,
            )
        )
        # Compatibility keys let the tested compile adapter validate a local
        # immutable metadata record.  This path is the frozen STAC JSON, not XML.
        snapshot_records.append(
            {
                "item_id": item_id,
                "metadata_kind": "frozen_stac_snapshot",
                "product_metadata_path": str(calibration.snapshot_path),
                "product_metadata_sha256": calibration.snapshot_sha256,
                "requester_pays_product_xml_opened": False,
                "calibration_sha256": calibration.calibration_sha256,
            }
        )

    mosaic = mosaic_aligned_tiles(aligned_tiles)
    if len(set(mosaic.calibration_sha256s)) != 1:
        raise FinalTestSentinelFeatureError(
            "Adjacent tiles disagree on Earth Search COG calibration."
        )
    base_valid = clear_land_mask(
        mosaic.scl,
        mosaic.reflectance,
        accepted_scl_classes=qa["accepted_scl_classes"],
    )
    indices = compute_optical_indices(
        mosaic.reflectance,
        denominator_epsilon=float(qa["index_denominator_epsilon"]),
        albedo_coefficients=stage.albedo_coefficients,
    )
    joint_valid = base_valid.copy()
    for values in indices.values():
        joint_valid &= np.isfinite(values)
    aggregated = aggregate_acquisition_to_tracts(
        physical_acquisition_id=physical_id,
        acquisition_local_date=str(acquisition_row.acquisition_local_date),
        platform=str(acquisition_row.platform),
        processing_baseline=str(acquisition_row.processing_baseline),
        indices=indices,
        base_valid_20m=base_valid,
        optical_grid=spatial.optical_grid,
        target_grid=spatial.target_grid,
        zone_raster_30m=spatial.zones,
        eligible_land_30m=spatial.eligible_land,
        tract_geoids=spatial.tract_geoids,
        expected_eligible_counts=spatial.eligible_counts,
        minimum_acquisition_coverage=stage.minimum_coverage,
    )
    aggregated["source_item_ids_audit_only"] = "|".join(mosaic.item_ids)
    aggregated["source_mgrs_tiles_audit_only"] = "|".join(mosaic.mgrs_tiles)
    aggregated["product_generation_time_audit_only"] = str(
        acquisition_row.generation_time
    )
    aggregated["tile_cloud_cover_percent_audit_only"] = json.dumps(
        [
            None if pd.isna(value) else float(value)
            for value in item_rows.sort_values(["mgrs_tile", "item_id"])[
                "cloud_cover_percent_audit_only"
            ]
        ]
    )
    aggregated["union_city_coverage_fraction_audit_only"] = float(
        acquisition_row.union_city_coverage_fraction
    )
    aggregated["calibration_sha256_audit_only"] = mosaic.calibration_sha256s[0]
    aggregated["optical_grid_sha256_audit_only"] = spatial.optical_grid.sha256
    aggregated["static_land_mask_sha256_audit_only"] = spatial.locks[
        "static_land_mask_sha256"
    ]
    aggregated["eligible_pixel_identity_sha256_audit_only"] = aggregated[
        "tract_geoid"
    ].map(spatial.eligible_identity_sha256s)
    output_path = directory / "acquisition_tract.parquet"
    atomic_parquet(aggregated, output_path)
    scl_counts = np.bincount(mosaic.scl.ravel(), minlength=12)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "cache_lock": expected_lock,
        "physical_acquisition_id": physical_id,
        "acquisition_local_date": str(acquisition_row.acquisition_local_date),
        "platform": str(acquisition_row.platform),
        "processing_baseline": str(acquisition_row.processing_baseline),
        "item_ids": list(mosaic.item_ids),
        "mgrs_tiles": list(mosaic.mgrs_tiles),
        "owned_optical_grid_pixels": list(mosaic.owned_pixel_counts),
        "calibration_sha256s": list(mosaic.calibration_sha256s),
        "product_metadata": snapshot_records,
        "requester_pays_product_xml_opened": False,
        "cog_decode_formula": CALIBRATION_FORMULA,
        "global_scene_cloud_cover_filter": None,
        "accepted_scl_classes": list(qa["accepted_scl_classes"]),
        "scl_class_pixel_counts_audit_only": {
            str(index): int(value) for index, value in enumerate(scl_counts) if value
        },
        "clear_land_pixel_count": int(base_valid.sum()),
        "joint_five_index_valid_pixel_count": int(joint_valid.sum()),
        "tract_count": len(aggregated),
        "qualifying_tract_count": int(
            aggregated["acquisition_qualifies_coverage"].sum()
        ),
        "output_file": parquet_file_record(output_path, aggregated),
    }
    atomic_json(summary, summary_path)
    return summary


class _StatusTracker:
    def __init__(
        self,
        path: Path,
        *,
        total: int,
        completed_ids: Sequence[str],
        workers: int,
    ) -> None:
        self.path = path
        self.lock = Lock()
        self.started = time.monotonic()
        self.durations: list[float] = []
        self.task_started: dict[str, float] = {}
        self.data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "state": "starting",
            "total": int(total),
            "completed": len(completed_ids),
            "running": 0,
            "failed": 0,
            "current": [],
            "completed_ids": list(completed_ids),
            "failures": [],
            "log_tail": [],
            "eta_seconds": None,
            "workers": workers,
            "pause_marker": str(path.parent / PAUSE_FILENAME),
        }
        self._write()

    def _eta(self) -> int | None:
        if not self.durations:
            return None
        remaining = max(
            0,
            int(self.data["total"])
            - int(self.data["completed"])
            - int(self.data["failed"]),
        )
        return int(
            math.ceil(
                remaining * (sum(self.durations) / len(self.durations))
                / max(1, int(self.data["workers"]))
            )
        )

    def _write(self) -> None:
        self.data["updated_at_utc"] = datetime.now(UTC).isoformat()
        self.data["eta_seconds"] = self._eta()
        atomic_json(self.data, self.path)

    def log(self, message: str) -> None:
        with self.lock:
            lines = list(self.data["log_tail"])
            lines.append(
                f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}"
            )
            self.data["log_tail"] = lines[-40:]
            self._write()

    def set_state(self, state: str) -> None:
        with self.lock:
            self.data["state"] = state
            self._write()

    def started_task(self, physical_id: str, attempt: int) -> None:
        with self.lock:
            self.task_started[physical_id] = time.monotonic()
            current = set(self.data["current"])
            current.add(physical_id)
            self.data["current"] = sorted(current)
            self.data["running"] = len(current)
            self.data["state"] = "running"
            lines = list(self.data["log_tail"])
            lines.append(
                f"{datetime.now(UTC).isoformat(timespec='seconds')} "
                f"start {physical_id} attempt {attempt}"
            )
            self.data["log_tail"] = lines[-40:]
            self._write()

    def finished_task(self, physical_id: str) -> None:
        with self.lock:
            started = self.task_started.pop(physical_id, time.monotonic())
            self.durations.append(max(0.0, time.monotonic() - started))
            current = set(self.data["current"])
            current.discard(physical_id)
            self.data["current"] = sorted(current)
            self.data["running"] = len(current)
            completed = set(self.data["completed_ids"])
            completed.add(physical_id)
            self.data["completed_ids"] = sorted(completed)
            self.data["completed"] = len(completed)
            lines = list(self.data["log_tail"])
            lines.append(
                f"{datetime.now(UTC).isoformat(timespec='seconds')} "
                f"complete {physical_id}"
            )
            self.data["log_tail"] = lines[-40:]
            self._write()

    def retry_task(self, physical_id: str, attempt: int, error: BaseException) -> None:
        with self.lock:
            self.task_started.pop(physical_id, None)
            current = set(self.data["current"])
            current.discard(physical_id)
            self.data["current"] = sorted(current)
            self.data["running"] = len(current)
            lines = list(self.data["log_tail"])
            lines.append(
                f"{datetime.now(UTC).isoformat(timespec='seconds')} retry "
                f"{physical_id} after attempt {attempt}: "
                f"{type(error).__name__}: {error}"
            )
            self.data["log_tail"] = lines[-40:]
            self._write()

    def failed_task(
        self, physical_id: str, attempt: int, error: BaseException
    ) -> None:
        with self.lock:
            started = self.task_started.pop(physical_id, time.monotonic())
            self.durations.append(max(0.0, time.monotonic() - started))
            current = set(self.data["current"])
            current.discard(physical_id)
            self.data["current"] = sorted(current)
            self.data["running"] = len(current)
            failures = list(self.data["failures"])
            failures.append(
                {
                    "physical_acquisition_id": physical_id,
                    "attempts": attempt,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            self.data["failures"] = failures
            self.data["failed"] = len(failures)
            lines = list(self.data["log_tail"])
            lines.append(
                f"{datetime.now(UTC).isoformat(timespec='seconds')} failed "
                f"{physical_id}: {type(error).__name__}: {error}"
            )
            self.data["log_tail"] = lines[-40:]
            self._write()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.data)


def execute_acquisition_queue(
    rows: Sequence[Any],
    *,
    physical_id: Callable[[Any], str],
    cache_is_current: Callable[[Any], bool],
    process: Callable[[Any], Any],
    status_path: Path,
    pause_marker: Path,
    workers: int,
    max_attempts: int,
) -> dict[str, Any]:
    """Run a resumable queue; pause is honored only at acquisition boundaries."""

    if workers not in {6, 8}:
        raise ValueError("Workers must be either 6 or 8.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive.")
    ordered = list(rows)
    ids = [physical_id(row) for row in ordered]
    if len(ids) != len(set(ids)):
        raise FinalTestSentinelFeatureError("Acquisition queue contains duplicates.")
    completed_ids = [
        physical_id(row) for row in ordered if cache_is_current(row)
    ]
    pending = deque(
        row for row in ordered if physical_id(row) not in set(completed_ids)
    )
    tracker = _StatusTracker(
        status_path,
        total=len(ordered),
        completed_ids=completed_ids,
        workers=workers,
    )
    tracker.log(f"resume found {len(completed_ids)} current acquisition caches")
    attempts: dict[str, int] = {}
    paused = pause_marker.exists()
    futures: dict[Future[Any], Any] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sentinel") as pool:
        while pending or futures:
            if pause_marker.exists():
                paused = True
            while pending and len(futures) < workers and not paused:
                row = pending.popleft()
                identifier = physical_id(row)
                attempts[identifier] = attempts.get(identifier, 0) + 1
                tracker.started_task(identifier, attempts[identifier])
                futures[pool.submit(process, row)] = row
            if not futures:
                break
            done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in done:
                row = futures.pop(future)
                identifier = physical_id(row)
                try:
                    future.result()
                except Exception as error:
                    if attempts[identifier] < max_attempts and not paused:
                        tracker.retry_task(
                            identifier, attempts[identifier], error
                        )
                        pending.append(row)
                    elif paused:
                        tracker.retry_task(
                            identifier, attempts[identifier], error
                        )
                        pending.appendleft(row)
                    else:
                        tracker.failed_task(
                            identifier, attempts[identifier], error
                        )
                else:
                    tracker.finished_task(identifier)
    snapshot = tracker.snapshot()
    if paused:
        tracker.set_state("paused")
    elif snapshot["failed"]:
        tracker.set_state("incomplete_with_failures")
    else:
        tracker.set_state("ready_to_compile")
    return tracker.snapshot()


def _prepare_build_inputs(
    *,
    research_config_path: str | Path,
    stage_config_path: str | Path,
    formal_lock_path: str | Path,
    landsat_inventory_directory: str | Path,
    sentinel_inventory_directory: str | Path,
    raw_stac_directory: str | Path,
    output_directory: str | Path,
) -> BuildInputs:
    root = _project_root()
    output = _resolve(root, output_directory)
    _require_suffix(output, OUTPUT_SUFFIX, label="Sentinel feature output")
    output.mkdir(parents=True, exist_ok=True)
    authenticated = authenticate_final_sentinel_inputs(
        project_root=root,
        research_config_path=_resolve(root, research_config_path),
        formal_lock_path=_resolve(root, formal_lock_path),
        landsat_inventory_directory=_resolve(root, landsat_inventory_directory),
        sentinel_inventory_directory=_resolve(root, sentinel_inventory_directory),
        raw_stac_directory=_resolve(root, raw_stac_directory),
    )
    stage = load_sentinel_stage_config(_resolve(root, stage_config_path))
    worldcover_path = (
        root / "data/raw/final_test_2025/static" / WORLD_COVER_FILENAME
    )
    spatial = authenticate_fixed_spatial_support(
        project_root=root,
        authenticated=authenticated,
        stage=stage,
        worldcover_path=worldcover_path,
    )
    pipeline_sha, pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    fingerprint_path = output / "pipeline_fingerprint.json"
    atomic_json(pipeline, fingerprint_path)
    runner_path = root / "scripts/build_final_test_sentinel_features.py"
    runner_sha = sha256_file(runner_path)
    base_lock = {
        "final_test_sentinel_feature_pipeline_sha256": pipeline_sha,
        "final_test_sentinel_feature_pipeline_fingerprint_sha256": sha256_file(
            fingerprint_path
        ),
        "sentinel_stage_config_sha256": stage.sha256,
        "target_blind_predictor_access": "2025_predictors_only_no_labels",
        "requester_pays_product_xml_opened": "false",
        **authenticated.locks,
        **spatial.locks,
    }
    return BuildInputs(
        authenticated=authenticated,
        spatial=spatial,
        stage=stage,
        base_lock=base_lock,
        output_directory=output,
        runner_sha256=runner_sha,
    )


def _build_final_test_sentinel_features_locked(
    *,
    research_config_path: str | Path = "configs/research.toml",
    stage_config_path: str | Path = "configs/sentinel_features.toml",
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    landsat_inventory_directory: str | Path = (
        "manifests/final_test_2025/landsat_inventory"
    ),
    sentinel_inventory_directory: str | Path = (
        "manifests/final_test_2025/sentinel_inventory"
    ),
    raw_stac_directory: str | Path = (
        "data/raw/final_test_2025/sentinel/stac_items"
    ),
    output_directory: str | Path = "data/interim/final_test_2025/sentinel",
    workers: int = 6,
    max_attempts: int = 3,
    force: bool = False,
    compile_only: bool = False,
) -> dict[str, Any]:
    """Build/resume all frozen acquisitions and compile exact d-60..d-1 features."""

    root = _project_root()
    preliminary_output = _resolve(root, output_directory)
    _require_suffix(
        preliminary_output, OUTPUT_SUFFIX, label="Sentinel feature output"
    )
    preliminary_output.mkdir(parents=True, exist_ok=True)
    preliminary_status_path = preliminary_output / STATUS_FILENAME
    preliminary_status: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "state": "preparing_inputs",
        "total": EXPECTED_ACQUISITION_COUNT,
        "completed": 0,
        "running": 0,
        "failed": 0,
        "current": ["authenticate_inputs_and_static_support"],
        "completed_ids": [],
        "failures": [],
        "log_tail": ["Authenticating frozen inputs and fixed spatial support."],
        "eta_seconds": None,
        "workers": workers,
        "pause_marker": str(preliminary_output / PAUSE_FILENAME),
    }
    atomic_json(preliminary_status, preliminary_status_path)
    try:
        inputs = _prepare_build_inputs(
            research_config_path=research_config_path,
            stage_config_path=stage_config_path,
            formal_lock_path=formal_lock_path,
            landsat_inventory_directory=landsat_inventory_directory,
            sentinel_inventory_directory=sentinel_inventory_directory,
            raw_stac_directory=raw_stac_directory,
            output_directory=output_directory,
        )
    except Exception as error:
        preliminary_status["state"] = "failed_preparing_inputs"
        preliminary_status["failed"] = 1
        preliminary_status["running"] = 0
        preliminary_status["current"] = []
        preliminary_status["failures"] = [
            {
                "physical_acquisition_id": None,
                "attempts": 1,
                "error_type": type(error).__name__,
                "message": str(error),
            }
        ]
        preliminary_status["log_tail"].append(
            f"Input preparation failed: {type(error).__name__}: {error}"
        )
        preliminary_status["updated_at_utc"] = datetime.now(UTC).isoformat()
        atomic_json(preliminary_status, preliminary_status_path)
        raise
    inventory = inputs.authenticated.inventory
    status_path = inputs.output_directory / STATUS_FILENAME
    pause_marker = inputs.output_directory / PAUSE_FILENAME

    def item_rows_for(row: Any) -> pd.DataFrame:
        return inventory.items.loc[
            inventory.items["physical_acquisition_id"]
            == str(row.physical_acquisition_id)
        ]

    def cache_current(row: Any) -> bool:
        if force:
            return False
        identifier = str(row.physical_acquisition_id)
        rows = item_rows_for(row)
        expected = _expected_acquisition_lock(
            base_lock=inputs.base_lock,
            physical_id=identifier,
            item_rows=rows,
        )
        return _acquisition_cache_is_current(
            _acquisition_cache_directory(inputs.output_directory, identifier),
            expected_lock=expected,
        )

    def process(row: Any) -> dict[str, Any]:
        return _process_acquisition(
            row,
            item_rows=item_rows_for(row),
            contracts=inputs.authenticated.contracts,
            spatial=inputs.spatial,
            stage=inputs.stage,
            base_lock=inputs.base_lock,
            output_directory=inputs.output_directory,
            force=force,
        )

    rows = list(inventory.acquisitions.itertuples(index=False))
    if compile_only:
        tracker = _StatusTracker(
            status_path,
            total=len(rows),
            completed_ids=[
                str(row.physical_acquisition_id)
                for row in rows
                if cache_current(row)
            ],
            workers=workers,
        )
        tracker.set_state("compiling")
        queue_status = tracker.snapshot()
    else:
        queue_status = execute_acquisition_queue(
            rows,
            physical_id=lambda row: str(row.physical_acquisition_id),
            cache_is_current=cache_current,
            process=process,
            status_path=status_path,
            pause_marker=pause_marker,
            workers=workers,
            max_attempts=max_attempts,
        )

    compile_result = compile_outputs_from_current_caches(
        inventory=inventory,
        spatial=inputs.spatial,
        stage=inputs.stage,
        research=inputs.authenticated.predictor_research,
        base_lock=inputs.base_lock,
        output_directory=inputs.output_directory,
        runner_sha256=inputs.runner_sha256,
        runner_version=RUNNER_VERSION,
    )
    status, _ = _read_json_stable(status_path, label="Sentinel status")
    if queue_status["state"] == "paused":
        final_state = "paused"
    elif queue_status["failed"]:
        final_state = "incomplete_with_failures"
    elif compile_result.get("state") == "complete":
        final_state = "complete"
    else:
        final_state = str(compile_result.get("state", "incomplete"))
    status["state"] = final_state
    status["compile_state"] = compile_result.get("state")
    status["promoted_outputs_valid"] = bool(
        compile_result.get("promoted_outputs_valid")
    )
    status["completed"] = int(
        compile_result.get(
            "completed_physical_acquisition_count", status["completed"]
        )
    )
    status["eta_seconds"] = 0 if final_state == "complete" else status["eta_seconds"]
    atomic_json(status, status_path)
    return {
        "state": final_state,
        "status_path": str(status_path),
        "pause_marker": str(pause_marker),
        "status": status,
        "compile": compile_result,
        "target_or_qa_values_read": False,
        "requester_pays_product_xml_opened": False,
    }


def build_final_test_sentinel_features(
    *,
    research_config_path: str | Path = "configs/research.toml",
    stage_config_path: str | Path = "configs/sentinel_features.toml",
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    landsat_inventory_directory: str | Path = (
        "manifests/final_test_2025/landsat_inventory"
    ),
    sentinel_inventory_directory: str | Path = (
        "manifests/final_test_2025/sentinel_inventory"
    ),
    raw_stac_directory: str | Path = (
        "data/raw/final_test_2025/sentinel/stac_items"
    ),
    output_directory: str | Path = "data/interim/final_test_2025/sentinel",
    workers: int = 6,
    max_attempts: int = 3,
    force: bool = False,
    compile_only: bool = False,
) -> dict[str, Any]:
    """Run exactly one resumable builder against the isolated output directory."""

    root = _project_root()
    output = _resolve(root, output_directory)
    _require_suffix(output, OUTPUT_SUFFIX, label="Sentinel feature output")
    with FinalTestSentinelEngineLock(output / "engine.lock"):
        return _build_final_test_sentinel_features_locked(
            research_config_path=research_config_path,
            stage_config_path=stage_config_path,
            formal_lock_path=formal_lock_path,
            landsat_inventory_directory=landsat_inventory_directory,
            sentinel_inventory_directory=sentinel_inventory_directory,
            raw_stac_directory=raw_stac_directory,
            output_directory=output_directory,
            workers=workers,
            max_attempts=max_attempts,
            force=force,
            compile_only=compile_only,
        )
