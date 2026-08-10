"""Resumable four-city Sentinel-2 predictor build for the portable runner.

The inventory, target dates, tract support, and scientific transformation are
already frozen.  This module only reads public Sentinel-2 optical assets,
caches one physical acquisition at a time, compiles the five lagged optical
features, and merges them with the completed 41-feature predictor component.
No Landsat thermal target, target QA value, model, or prediction is opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import box

from la_heat.grid import build_fixed_grid
from la_heat.multicity.portable_predictor_components import (
    CITY_IDS,
    PortableCitySupport,
    load_city_support,
)
from la_heat.multicity.portable_sentinel_inventory import (
    authenticate_portable_sentinel_inventory,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_feature_builder import (
    ALGORITHM_VERSION,
    FixedSpatialSupport,
    FrozenSentinelInputs,
    SentinelStageConfig,
    _acquisition_cache_directory,
    _acquisition_cache_is_current,
    _expected_acquisition_lock,
    _process_acquisition,
)
from la_heat.sentinel_features import INDEX_COLUMNS, build_previous_60_day_composites

RUNNER_VERSION: Final = "portable-four-city-sentinel-v1"
PROCESS_ORDER: Final = (
    "chicago_il",
    "phoenix_az",
    "houston_tx",
    "los_angeles_ca",
)
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/portable_predictors/runtime/sentinel"
)
INVENTORY_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/sentinel_inventory"
)
RAW_METADATA_ROOT: Final = Path(
    "data/raw/multicity/portable_predictors/sentinel_product_metadata"
)
COMPONENT_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/components"
)
SENTINEL_COMPONENT_ROOT: Final = COMPONENT_ROOT / "sentinel"
BASE_COMPONENT: Final = COMPONENT_ROOT / "predictors_static_calendar_daymet.parquet"
BASE_COMPLETE: Final = COMPONENT_ROOT / "COMPONENTS_COMPLETE.json"
FINAL_OUTPUT: Final = COMPONENT_ROOT / "predictors_all_46.parquet"
FINAL_COMPLETE: Final = COMPONENT_ROOT / "PREDICTORS_ALL_46_COMPLETE.json"
STATUS_FILENAME: Final = "status.json"
PAUSE_FILENAME: Final = "PAUSE_REQUESTED"
CITY_COMPLETE_FILENAME: Final = "SENTINEL_COMPLETE.json"
CITY_OUTPUTS: Final = (
    "sentinel_features.parquet",
    "sentinel_feature_audit.parquet",
    "sentinel_lineage.parquet",
)
PIPELINE_FILES: Final = (
    "src/la_heat/grid.py",
    "src/la_heat/multicity/portable_predictor_components.py",
    "src/la_heat/multicity/portable_sentinel_build.py",
    "src/la_heat/multicity/portable_sentinel_inventory.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/sentinel_inventory.py",
)
ALBEDO_COEFFICIENTS: Final = {
    "B02": 0.2266,
    "B03": 0.1236,
    "B04": 0.1573,
    "B08": 0.3417,
    "B11": 0.1170,
    "B12": 0.0338,
}
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)


class PortableSentinelBuildError(RuntimeError):
    """Raised when a portable Sentinel input or output is inconsistent."""


@dataclass(frozen=True, slots=True)
class CityBuildContext:
    city_id: str
    inventory: FrozenSentinelInputs
    support: PortableCitySupport
    spatial: FixedSpatialSupport
    stage: SentinelStageConfig
    base_lock: dict[str, str]
    runtime_directory: Path
    output_directory: Path
    metadata_directory: Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_message(value: object, *, limit: int = 800) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return _URL_QUERY.sub(r"\1?<redacted>", text)[:limit]


def _project_root(value: str | Path | None = None) -> Path:
    root = Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortableSentinelBuildError(f"JSON object required: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and canonical_sha256(unsigned) == recorded


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _stage_for_city(root: Path, city_id: str, timezone: str) -> SentinelStageConfig:
    raw: dict[str, Any] = {
        "schema_version": "1",
        "algorithm_version": ALGORITHM_VERSION,
        "city_id": city_id,
        "grid": {
            "resolution_m": 20.0,
            "edge_anchor_x_m": 0.0,
            "edge_anchor_y_m": 0.0,
            "reflectance_resampling": "average",
            "scl_resampling": "nearest",
            "target_cell_aggregation": (
                "valid_area_average_then_tract_weighted_median"
            ),
            "processing_order": (
                "validate_phase_then_area_average_dn_and_max_saturation_mask_to_20m_"
                "then_decode_boa_then_scl_4_5_gate"
            ),
        },
        "qa": {
            "accepted_scl_classes": [4, 5],
            "nodata_dn": 0,
            "saturated_dn": 65535,
            "minimum_acquisition_coverage_fraction": 0.80,
            "minimum_physical_acquisitions": 3,
            "index_denominator_epsilon": 0.000001,
            "global_scene_cloud_cover_filter": False,
        },
        "window": {
            "start_days_before_target": 60,
            "end_days_before_target": 1,
            "local_timezone": timezone,
        },
        "albedo_proxy": {"intercept": 0.0, **ALBEDO_COEFFICIENTS},
    }
    return SentinelStageConfig(
        raw=raw,
        path=root
        / "manifests/multicity/reviews/portable_predictor_contract/"
        "PORTABLE_PREDICTOR_CONTRACT.json",
    )


def _fixed_spatial_support(
    support: PortableCitySupport,
    *,
    target_dates: tuple[str, ...],
) -> FixedSpatialSupport:
    grid = support.grid
    grid_box = gpd.GeoDataFrame(
        geometry=[box(grid.left, grid.bottom, grid.right, grid.top)],
        crs=grid.crs,
    )
    optical = build_fixed_grid(
        grid_box,
        target_crs=grid.crs,
        resolution_m=20.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
    )
    static = support.static_support
    eligible_counts = {
        geoid: int(count)
        for geoid, count in zip(static.geoids, static.counts, strict=True)
    }
    eligible_identities = {
        geoid: identity
        for geoid, identity in zip(
            static.geoids, static.identity_sha256, strict=True
        )
    }
    land_sha = hashlib.sha256(
        np.packbits(support.eligible_land.ravel()).tobytes()
    ).hexdigest()
    zone_sha = hashlib.sha256(support.zones.tobytes()).hexdigest()
    return FixedSpatialSupport(
        target_grid=grid,
        optical_grid=optical,
        zones=support.zones,
        eligible_land=support.eligible_land,
        tracts=support.tracts,
        tract_geoids=support.tract_geoids,
        eligible_counts=eligible_counts,
        eligible_identity_sha256s=eligible_identities,
        target_dates=target_dates,
        locks={
            "target_grid_definition_sha256": grid.sha256,
            "optical_grid_definition_sha256": optical.sha256,
            "zone_raster_sha256": zone_sha,
            "static_land_mask_sha256": land_sha,
            "geography_commit_sha256": str(
                support.geography_manifest["commit_sha256"]
            ),
            "worldcover_commit_sha256": str(
                support.worldcover_manifest["commit_sha256"]
            ),
        },
    )


def _load_inventory(root: Path, city_id: str) -> tuple[FrozenSentinelInputs, str]:
    complete = authenticate_portable_sentinel_inventory(root, city_id)
    directory = root / INVENTORY_ROOT / city_id
    acquisitions = pd.read_csv(
        directory / "selected_acquisitions.csv",
        dtype={"processing_baseline": "string"},
    )
    items = pd.read_csv(
        directory / "selected_items.csv",
        dtype={"processing_baseline": "string"},
    )
    membership = pd.read_csv(directory / "target_window_membership.csv")
    target_dates = tuple(sorted(membership["target_date"].astype(str).unique()))
    if not target_dates or acquisitions.empty or items.empty or membership.empty:
        raise PortableSentinelBuildError(f"Sentinel inventory is empty for {city_id}.")
    locks = {
        "portable_sentinel_inventory_commit_sha256": str(complete["commit_sha256"]),
        "sentinel_inventory_semantic_sha256": str(
            complete["sentinel_inventory_semantic_sha256"]
        ),
        "sentinel_membership_semantic_sha256": str(
            complete["membership_semantic_sha256"]
        ),
    }
    return (
        FrozenSentinelInputs(
            acquisitions=acquisitions,
            items=items,
            membership=membership,
            summary=complete,
            locks=locks,
        ),
        canonical_sha256(target_dates),
    )


def prepare_contexts(
    project_root: str | Path,
    city_ids: tuple[str, ...] = PROCESS_ORDER,
) -> tuple[dict[str, CityBuildContext], dict[str, Any]]:
    """Authenticate target-blind inputs and create immutable city contexts."""

    root = _project_root(project_root)
    if not city_ids or len(set(city_ids)) != len(city_ids):
        raise ValueError("city_ids must be non-empty and unique.")
    if unknown := set(city_ids) - set(CITY_IDS):
        raise ValueError(f"Unknown cities: {sorted(unknown)}")
    pipeline_sha, pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=RUNNER_VERSION,
    )
    runtime_root = root / RUNTIME_ROOT
    runtime_root.mkdir(parents=True, exist_ok=True)
    atomic_json(pipeline, runtime_root / "pipeline_fingerprint.json")
    contexts: dict[str, CityBuildContext] = {}
    for city_id in city_ids:
        inventory, target_dates_sha = _load_inventory(root, city_id)
        support = load_city_support(root, city_id)
        timezone = str(inventory.summary["local_timezone"])
        stage = _stage_for_city(root, city_id, timezone)
        target_dates = tuple(
            sorted(inventory.membership["target_date"].astype(str).unique())
        )
        spatial = _fixed_spatial_support(support, target_dates=target_dates)
        runtime = runtime_root / city_id
        output = root / SENTINEL_COMPONENT_ROOT / city_id
        metadata = root / RAW_METADATA_ROOT / city_id
        runtime.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        metadata.mkdir(parents=True, exist_ok=True)
        base_lock = {
            "portable_sentinel_pipeline_sha256": pipeline_sha,
            "portable_sentinel_stage_sha256": stage.sha256,
            "target_dates_sha256": target_dates_sha,
            **inventory.locks,
            **spatial.locks,
        }
        contexts[city_id] = CityBuildContext(
            city_id=city_id,
            inventory=inventory,
            support=support,
            spatial=spatial,
            stage=stage,
            base_lock=base_lock,
            runtime_directory=runtime,
            output_directory=output,
            metadata_directory=metadata,
        )
    return contexts, pipeline


def _item_rows(context: CityBuildContext, physical_id: str) -> pd.DataFrame:
    rows = context.inventory.items.loc[
        context.inventory.items["physical_acquisition_id"] == physical_id
    ]
    if rows.empty:
        raise PortableSentinelBuildError(
            f"No selected items for {context.city_id} {physical_id}."
        )
    return rows


def acquisition_cache_is_current(
    project_root: Path,
    context: CityBuildContext,
    row: Any,
) -> bool:
    physical_id = str(row.physical_acquisition_id)
    items = _item_rows(context, physical_id)
    expected = _expected_acquisition_lock(
        base_lock=context.base_lock,
        physical_id=physical_id,
        item_rows=items,
    )
    return _acquisition_cache_is_current(
        _acquisition_cache_directory(context.runtime_directory, physical_id),
        expected_lock=expected,
        metadata_path_root=project_root,
    )


def _verify_output_record(path: Path, record: object) -> bool:
    if not isinstance(record, dict) or not path.is_file():
        return False
    return bool(
        path.stat().st_size == record.get("bytes")
        and sha256_file(path) == record.get("sha256")
    )


def city_output_is_current(context: CityBuildContext) -> bool:
    path = context.output_directory / CITY_COMPLETE_FILENAME
    try:
        payload = _read_json(path)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not _committed(payload) or payload.get("base_lock") != context.base_lock:
        return False
    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        return False
    return all(
        _verify_output_record(context.output_directory / name, outputs.get(name))
        for name in CITY_OUTPUTS
    )


def compile_city(project_root: Path, context: CityBuildContext) -> dict[str, Any]:
    """Compile a city only after every physical acquisition cache is current."""

    frames: list[pd.DataFrame] = []
    for row in context.inventory.acquisitions.itertuples(index=False):
        if not acquisition_cache_is_current(project_root, context, row):
            raise PortableSentinelBuildError(
                f"Cannot compile {context.city_id}; acquisition cache is incomplete."
            )
        cache = _acquisition_cache_directory(
            context.runtime_directory, str(row.physical_acquisition_id)
        )
        frames.append(pd.read_parquet(cache / "acquisition_tract.parquet"))
    acquisition = pd.concat(frames, ignore_index=True)
    if acquisition.duplicated(["tract_geoid", "physical_acquisition_id"]).any():
        raise PortableSentinelBuildError(
            f"Duplicate acquisition/tract keys for {context.city_id}."
        )
    composites = build_previous_60_day_composites(
        acquisition,
        context.inventory.membership,
        target_dates=context.spatial.target_dates,
        tract_geoids=context.spatial.tract_geoids,
        minimum_acquisition_coverage=context.stage.minimum_coverage,
        minimum_acquisitions=context.stage.minimum_acquisitions,
        final_test_year=2025,
        unlock_final_test=True,
    )
    frames_by_name = {
        "sentinel_features.parquet": composites.features,
        "sentinel_feature_audit.parquet": composites.audit,
        "sentinel_lineage.parquet": composites.lineage,
    }
    output_records: dict[str, Any] = {}
    for name, frame in frames_by_name.items():
        output = frame.copy()
        output.insert(0, "city_id", context.city_id)
        path = context.output_directory / name
        atomic_parquet(output, path)
        output_records[name] = parquet_file_record(path, output)
    features = frames_by_name["sentinel_features.parquet"]
    missing_count = features[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not missing_count.isin([0, len(INDEX_COLUMNS)]).all():
        raise PortableSentinelBuildError(
            f"Partial Sentinel feature missingness for {context.city_id}."
        )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": RUNNER_VERSION,
        "state": "complete",
        "generated_at_utc": _utc_now(),
        "city_id": context.city_id,
        "base_lock": context.base_lock,
        "physical_acquisition_count": len(context.inventory.acquisitions),
        "tract_count": len(context.spatial.tract_geoids),
        "target_date_count": len(context.spatial.target_dates),
        "feature_row_count": len(features),
        "available_feature_row_count": int((missing_count == 0).sum()),
        "outputs": output_records,
        "access_contract": {
            "public_sentinel_optical_values_read": True,
            "external_target_or_qa_values_read": False,
            "landsat_thermal_values_read": False,
            "model_fit_or_prediction_performed": False,
        },
    }
    manifest["commit_sha256"] = canonical_sha256(manifest)
    atomic_json(manifest, context.output_directory / CITY_COMPLETE_FILENAME)
    return manifest


def _base_component_manifest(root: Path) -> dict[str, Any]:
    payload = _read_json(root / BASE_COMPLETE)
    record = payload.get("output")
    if (
        payload.get("state")
        != "complete_target_blind_static_calendar_daymet_components"
        or not _verify_output_record(root / BASE_COMPONENT, record)
        or int(payload.get("feature_count", -1)) != 41
    ):
        raise PortableSentinelBuildError("The completed 41-feature component changed.")
    return payload


def final_output_is_current(
    root: Path,
    contexts: dict[str, CityBuildContext],
) -> bool:
    try:
        payload = _read_json(root / FINAL_COMPLETE)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    expected_locks = {
        city_id: context.base_lock for city_id, context in contexts.items()
    }
    return bool(
        _committed(payload)
        and payload.get("city_base_locks") == expected_locks
        and _verify_output_record(root / FINAL_OUTPUT, payload.get("output"))
    )


def merge_all_predictors(
    root: Path,
    contexts: dict[str, CityBuildContext],
) -> dict[str, Any]:
    """Merge the five Sentinel features onto the frozen 41-feature table."""

    if set(contexts) != set(CITY_IDS):
        raise PortableSentinelBuildError("The final merge requires all four cities.")
    base_manifest = _base_component_manifest(root)
    base = pd.read_parquet(root / BASE_COMPONENT)
    keys = ["city_id", "tract_geoid", "target_date"]
    base["city_id"] = base["city_id"].astype(str)
    base["tract_geoid"] = base["tract_geoid"].astype(str)
    base["target_date"] = pd.to_datetime(base["target_date"])
    if base.duplicated(keys).any():
        raise PortableSentinelBuildError("The 41-feature component has duplicate keys.")
    sentinel_frames: list[pd.DataFrame] = []
    city_manifests: dict[str, str] = {}
    for city_id in CITY_IDS:
        context = contexts[city_id]
        if not city_output_is_current(context):
            raise PortableSentinelBuildError(f"Sentinel output is incomplete for {city_id}.")
        manifest = _read_json(context.output_directory / CITY_COMPLETE_FILENAME)
        city_manifests[city_id] = str(manifest["commit_sha256"])
        frame = pd.read_parquet(context.output_directory / "sentinel_features.parquet")
        frame["city_id"] = frame["city_id"].astype(str)
        frame["tract_geoid"] = frame["tract_geoid"].astype(str)
        frame["target_date"] = pd.to_datetime(frame["target_date"])
        sentinel_frames.append(frame[[*keys, *INDEX_COLUMNS]])
    sentinel = pd.concat(sentinel_frames, ignore_index=True)
    if sentinel.duplicated(keys).any() or len(sentinel) != len(base):
        raise PortableSentinelBuildError("Sentinel keys do not match the predictor base.")
    merged = base.merge(sentinel, on=keys, how="left", validate="one_to_one")
    if len(merged) != len(base):
        raise PortableSentinelBuildError("Final predictor merge changed the row count.")
    missing = merged[list(INDEX_COLUMNS)].isna().sum(axis=1)
    if not missing.isin([0, len(INDEX_COLUMNS)]).all():
        raise PortableSentinelBuildError("Final merge created partial Sentinel missingness.")
    output_path = root / FINAL_OUTPUT
    atomic_parquet(merged, output_path)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": RUNNER_VERSION,
        "state": "complete_target_blind_46_feature_predictors",
        "generated_at_utc": _utc_now(),
        "city_count": 4,
        "row_count": len(merged),
        "feature_count": 46,
        "feature_order": [*base_manifest["feature_order"], *INDEX_COLUMNS],
        "sentinel_available_row_count": int((missing == 0).sum()),
        "base_component_sha256": str(base_manifest["output"]["sha256"]),
        "city_complete_commits": city_manifests,
        "city_base_locks": {
            city_id: context.base_lock for city_id, context in contexts.items()
        },
        "output": {
            "path": _relative(root, output_path),
            **parquet_file_record(output_path, merged),
        },
        "access_contract": {
            "public_predictor_values_read": True,
            "external_target_or_qa_values_read": False,
            "landsat_thermal_values_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "fit_frozen_model_and_run_external_city_predictions",
    }
    manifest["commit_sha256"] = canonical_sha256(manifest)
    atomic_json(manifest, root / FINAL_COMPLETE)
    return manifest


class StatusWriter:
    """Small status document consumed by the localhost dashboard."""

    def __init__(
        self,
        path: Path,
        contexts: dict[str, CityBuildContext],
        *,
        download_threads: int,
        acquisition_concurrency: int,
        include_global_merge: bool,
    ) -> None:
        self.path = path
        self.contexts = contexts
        self.started: dict[str, float] = {}
        self.durations: list[float] = []
        city_status = {
            city_id: {
                "total": len(context.inventory.acquisitions) + 1,
                "completed": 0,
                "running": 0,
                "failed": 0,
                "state": "pending",
            }
            for city_id, context in contexts.items()
        }
        total = sum(value["total"] for value in city_status.values())
        if include_global_merge:
            total += 1
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": RUNNER_VERSION,
            "state": "preparing",
            "phase": "准备输入",
            "total": total,
            "completed": 0,
            "pending": total,
            "running": 0,
            "failed": 0,
            "retries": 0,
            "current": [],
            "current_city": None,
            "eta_seconds": None,
            "cities": city_status,
            "events": [],
            "error": None,
            "download_threads": download_threads,
            "acquisition_concurrency": acquisition_concurrency,
            "updated_at_utc": _utc_now(),
        }

    def publish(self) -> None:
        self.data["updated_at_utc"] = _utc_now()
        self.data["pending"] = max(
            0,
            int(self.data["total"])
            - int(self.data["completed"])
            - int(self.data["running"]),
        )
        acquisition_pending = sum(
            max(
                0,
                int(city["total"])
                - int(city["completed"])
                - int(city["running"]),
            )
            for city in self.data["cities"].values()
        )
        if self.durations and acquisition_pending:
            mean = sum(self.durations[-20:]) / len(self.durations[-20:])
            self.data["eta_seconds"] = int(
                math.ceil(mean * acquisition_pending)
            )
        elif not self.data["pending"]:
            self.data["eta_seconds"] = 0
        atomic_json(self.data, self.path)

    def event(self, message: object) -> None:
        events = list(self.data["events"])
        events.append({"at": _utc_now(), "message": _clean_message(message)})
        self.data["events"] = events[-80:]
        self.publish()

    def initialize_from_disk(
        self,
        root: Path,
        *,
        include_global_merge: bool,
    ) -> None:
        completed = 0
        for city_id, context in self.contexts.items():
            current = sum(
                acquisition_cache_is_current(root, context, row)
                for row in context.inventory.acquisitions.itertuples(index=False)
            )
            compiled = int(city_output_is_current(context))
            city = self.data["cities"][city_id]
            city["completed"] = current + compiled
            city["state"] = "complete" if compiled else "ready"
            completed += current + compiled
        if include_global_merge and final_output_is_current(root, self.contexts):
            completed += 1
        self.data["completed"] = completed
        self.publish()

    def task_started(self, city_id: str, task_id: str) -> None:
        self.started[task_id] = time.monotonic()
        current = set(self.data["current"])
        current.add(task_id)
        self.data["current"] = sorted(current)
        self.data["current_city"] = city_id
        self.data["running"] = len(current)
        self.data["cities"][city_id]["running"] += 1
        self.data["cities"][city_id]["state"] = "running"
        self.publish()

    def task_finished(self, city_id: str, task_id: str) -> None:
        started = self.started.pop(task_id, None)
        if started is not None:
            self.durations.append(max(0.0, time.monotonic() - started))
        current = set(self.data["current"])
        current.discard(task_id)
        self.data["current"] = sorted(current)
        self.data["running"] = len(current)
        city = self.data["cities"][city_id]
        city["running"] = max(0, int(city["running"]) - 1)
        city["completed"] += 1
        self.data["completed"] += 1
        self.publish()

    def task_retry(self, city_id: str, task_id: str, error: BaseException) -> None:
        current = set(self.data["current"])
        current.discard(task_id)
        self.data["current"] = sorted(current)
        self.data["running"] = len(current)
        city = self.data["cities"][city_id]
        city["running"] = max(0, int(city["running"]) - 1)
        self.data["retries"] += 1
        self.event(
            f"{city_id} {task_id} 自动重试：{type(error).__name__}: {error}"
        )

    def task_failed(self, city_id: str, task_id: str, error: BaseException) -> None:
        self.task_retry(city_id, task_id, error)
        self.data["failed"] += 1
        self.data["cities"][city_id]["failed"] += 1
        self.data["error"] = {
            "type": type(error).__name__,
            "message": _clean_message(error),
            "task_id": task_id,
            "city_id": city_id,
        }
        self.publish()

    def compiled_city(self, city_id: str) -> None:
        self.data["cities"][city_id]["completed"] += 1
        self.data["cities"][city_id]["state"] = "complete"
        self.data["completed"] += 1
        self.publish()


def _process_one(
    root: Path,
    context: CityBuildContext,
    row: Any,
    *,
    download_threads: int,
    force: bool,
) -> dict[str, Any]:
    physical_id = str(row.physical_acquisition_id)
    with requests.Session() as session:
        return _process_acquisition(
            row,
            item_rows=_item_rows(context, physical_id),
            spatial=context.spatial,
            stage=context.stage,
            base_lock=context.base_lock,
            output_directory=context.runtime_directory,
            raw_metadata_directory=context.metadata_directory,
            session=session,
            force=force,
            metadata_path_root=root,
            download_threads=download_threads,
        )


def _run_city_queue(
    root: Path,
    context: CityBuildContext,
    status: StatusWriter,
    pause_marker: Path,
    *,
    download_threads: int,
    acquisition_concurrency: int,
    max_attempts: int,
    force: bool,
) -> tuple[bool, list[str]]:
    rows = list(context.inventory.acquisitions.itertuples(index=False))
    pending = deque(
        row
        for row in rows
        if force or not acquisition_cache_is_current(root, context, row)
    )
    if not pending:
        return pause_marker.exists(), []
    failures: list[str] = []
    attempts: dict[str, int] = {}
    futures: dict[Future[Any], Any] = {}
    paused = pause_marker.exists()
    with ThreadPoolExecutor(
        max_workers=acquisition_concurrency,
        thread_name_prefix="sentinel-acquisition",
    ) as pool:
        while pending or futures:
            if pause_marker.exists():
                paused = True
            while pending and len(futures) < acquisition_concurrency and not paused:
                row = pending.popleft()
                physical_id = str(row.physical_acquisition_id)
                attempts[physical_id] = attempts.get(physical_id, 0) + 1
                status.task_started(context.city_id, physical_id)
                futures[
                    pool.submit(
                        _process_one,
                        root,
                        context,
                        row,
                        download_threads=download_threads,
                        force=force,
                    )
                ] = row
            if not futures:
                break
            done, _ = wait(tuple(futures), timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                row = futures.pop(future)
                physical_id = str(row.physical_acquisition_id)
                try:
                    future.result()
                except Exception as error:
                    if attempts[physical_id] < max_attempts and not paused:
                        status.task_retry(context.city_id, physical_id, error)
                        time.sleep(min(15.0, float(2 ** attempts[physical_id])))
                        pending.append(row)
                    elif paused:
                        status.task_retry(context.city_id, physical_id, error)
                        pending.appendleft(row)
                    else:
                        status.task_failed(context.city_id, physical_id, error)
                        failures.append(physical_id)
                else:
                    status.task_finished(context.city_id, physical_id)
    return paused, failures


def run_portable_sentinel_build(
    project_root: str | Path,
    *,
    city_ids: tuple[str, ...] = PROCESS_ORDER,
    download_threads: int = 6,
    acquisition_concurrency: int = 1,
    max_attempts: int = 3,
    force: bool = False,
    compile_only: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    """Build/resume all requested cities and publish dashboard status."""

    if download_threads not in {6, 8}:
        raise ValueError("download_threads must be 6 or 8.")
    if acquisition_concurrency not in {1, 2}:
        raise ValueError("acquisition_concurrency must be 1 or 2.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive.")
    root = _project_root(project_root)
    runtime_root = root / RUNTIME_ROOT
    runtime_root.mkdir(parents=True, exist_ok=True)
    status_path = runtime_root / STATUS_FILENAME
    pause_marker = runtime_root / PAUSE_FILENAME
    include_global_merge = set(city_ids) == set(CITY_IDS)
    contexts, _pipeline = prepare_contexts(root, city_ids)
    status = StatusWriter(
        status_path,
        contexts,
        download_threads=download_threads,
        acquisition_concurrency=acquisition_concurrency,
        include_global_merge=include_global_merge,
    )
    status.initialize_from_disk(root, include_global_merge=include_global_merge)
    if check_only:
        complete = all(city_output_is_current(value) for value in contexts.values())
        if include_global_merge:
            complete = complete and final_output_is_current(root, contexts)
        status.data["state"] = "complete" if complete else "ready"
        status.data["phase"] = "已完成" if complete else "输入已验证，可开始"
        status.publish()
        return {"state": status.data["state"], "status": status.data}
    if pause_marker.exists():
        status.data["state"] = "paused"
        status.data["phase"] = "已安全暂停"
        status.publish()
        return {"state": "paused", "status": status.data}

    all_failures: list[str] = []
    for city_id in city_ids:
        context = contexts[city_id]
        status.data["state"] = "running"
        status.data["phase"] = "读取并聚合 Sentinel-2"
        status.data["current_city"] = city_id
        status.event(f"开始或继续 {city_id}。")
        if not compile_only:
            paused, failures = _run_city_queue(
                root,
                context,
                status,
                pause_marker,
                download_threads=download_threads,
                acquisition_concurrency=acquisition_concurrency,
                max_attempts=max_attempts,
                force=force,
            )
            all_failures.extend(failures)
            if paused:
                status.data["state"] = "paused"
                status.data["phase"] = "已在 acquisition 边界安全暂停"
                status.data["current"] = []
                status.data["running"] = 0
                status.publish()
                return {"state": "paused", "status": status.data}
        city_ready = all(
            acquisition_cache_is_current(root, context, row)
            for row in context.inventory.acquisitions.itertuples(index=False)
        )
        if city_ready:
            status.data["phase"] = "编译城市 Sentinel 特征"
            status.publish()
            if force or not city_output_is_current(context):
                compile_city(root, context)
                status.compiled_city(city_id)
            status.event(f"{city_id} Sentinel 特征完成。")
        else:
            status.data["cities"][city_id]["state"] = "incomplete"
            status.event(f"{city_id} 仍有 acquisition 未完成；继续后续城市。")

    if all_failures:
        status.data["state"] = "incomplete_with_failures"
        status.data["phase"] = "部分任务失败，等待自动重启续跑"
        status.data["current"] = []
        status.data["running"] = 0
        status.publish()
        return {
            "state": "incomplete_with_failures",
            "failed_task_ids": all_failures,
            "status": status.data,
        }
    if not all(city_output_is_current(value) for value in contexts.values()):
        status.data["state"] = "incomplete"
        status.data["phase"] = "城市输出尚未全部完成"
        status.publish()
        return {"state": "incomplete", "status": status.data}
    if include_global_merge:
        status.data["phase"] = "合并最终 46 个 predictor features"
        status.publish()
        if force or not final_output_is_current(root, contexts):
            merge_all_predictors(root, contexts)
            status.data["completed"] += 1
        status.event("四城 46-feature predictor table 已完成。")
    status.data["state"] = "complete"
    status.data["phase"] = "已完成"
    status.data["current"] = []
    status.data["current_city"] = None
    status.data["running"] = 0
    status.data["failed"] = 0
    status.data["error"] = None
    status.publish()
    return {"state": "complete", "status": status.data}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build/resume the frozen four-city Sentinel-2 predictors."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument(
        "--city",
        choices=(*CITY_IDS, "all"),
        default="all",
    )
    parser.add_argument("--download-threads", type=int, choices=(6, 8), default=6)
    parser.add_argument(
        "--acquisition-concurrency", type=int, choices=(1, 2), default=1
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    city_ids = PROCESS_ORDER if args.city == "all" else (args.city,)
    try:
        result = run_portable_sentinel_build(
            args.project_root,
            city_ids=city_ids,
            download_threads=args.download_threads,
            acquisition_concurrency=args.acquisition_concurrency,
            max_attempts=args.max_attempts,
            force=args.force,
            compile_only=args.compile_only,
            check_only=args.check_only,
        )
    except Exception as error:
        root = _project_root(args.project_root)
        status_path = root / RUNTIME_ROOT / STATUS_FILENAME
        failure = {
            "schema_version": 1,
            "algorithm_version": RUNNER_VERSION,
            "state": "failed",
            "phase": "engine 失败，等待自动重启",
            "total": 0,
            "completed": 0,
            "pending": 0,
            "running": 0,
            "failed": 1,
            "retries": 0,
            "current": [],
            "current_city": None,
            "eta_seconds": None,
            "cities": {},
            "events": [
                {
                    "at": _utc_now(),
                    "message": _clean_message(
                        f"{type(error).__name__}: {error}"
                    ),
                }
            ],
            "error": {
                "type": type(error).__name__,
                "message": _clean_message(error),
            },
            "updated_at_utc": _utc_now(),
        }
        atomic_json(failure, status_path)
        print(_clean_message(f"{type(error).__name__}: {error}"), file=sys.stderr)
        return 1
    print(json.dumps({"state": result["state"]}, ensure_ascii=False))
    return 0 if result["state"] in {"complete", "paused", "ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
