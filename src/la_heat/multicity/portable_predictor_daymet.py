"""Resumable Daymet inputs and features for the frozen four-city predictors.

This module only opens public predictor inputs.  In particular, it never opens
Landsat thermal bands, target QA, or external-city evaluation artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import rasterio

from la_heat.daymet_feature_stage import compile_daymet_feature_tables
from la_heat.daymet_grid import (
    DaymetGranule,
    DaymetGridAuditError,
    EarthdataBearerToken,
    authenticated_netcdf_download,
    build_daymet_direct_subset_url,
    inspect_daymet_netcdf,
    validate_daymet_direct_subset_spec,
)
from la_heat.provenance import atomic_parquet, canonical_sha256, sha256_file
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
EXTERNAL_CITY_IDS: Final = CITY_IDS[1:]
LOS_ANGELES_YEARS: Final = tuple(range(2020, 2025))
EXTERNAL_DAYMET_YEAR: Final = 2025
FINAL_TEST_YEAR_SENTINEL: Final = 2026

RAW_EXTERNAL_ROOT: Final = Path("data/raw/multicity/portable_predictors/daymet")
COMPONENT_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/components"
)
INVENTORY_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/inventory"
)
LOS_ANGELES_SUBSET_ROOT: Final = Path("data/raw/daymet/v4r1/subsets")

FEATURE_FILENAME: Final = "daymet_features.parquet"
AUDIT_FILENAME: Final = "daymet_feature_audit.parquet"
WEIGHTS_FILENAME: Final = "daymet_fixed_cell_weights.parquet"
TRACT_DAILY_FILENAME: Final = "daymet_tract_daily.parquet"

_SOURCE_FOOTPRINT_MANIFESTS: Final = {
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

ProgressCallback = Callable[[dict[str, object]], None]
PauseCallback = Callable[[], bool]


class PortablePredictorDaymetError(ValueError):
    """Raised when portable Daymet inputs fail a target-blind audit."""


@dataclass(frozen=True, slots=True)
class PortableDaymetDownloadTask:
    """One frozen external-city Daymet variable subset download."""

    city_id: str
    variable: str
    year: int
    granule: DaymetGranule
    y_indices: tuple[int, int]
    x_indices: tuple[int, int]
    bbox_wgs84: tuple[float, float, float, float]
    source_url: str
    destination: Path

    @property
    def task_id(self) -> str:
        return f"{self.city_id}:{self.year}:{self.variable}"

    def as_status_record(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "city_id": self.city_id,
            "variable": self.variable,
            "year": self.year,
            "destination": self.destination.as_posix(),
        }


@dataclass(frozen=True, slots=True)
class _SupportView:
    zones: np.ndarray
    eligible_land: np.ndarray
    tract_geoids: tuple[str, ...]
    transform: rasterio.Affine
    crs: object


def _root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project root does not exist: {root}")
    return root


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PortablePredictorDaymetError(f"Unreadable Daymet manifest: {path}") from error
    if not isinstance(payload, dict):
        raise PortablePredictorDaymetError(f"Daymet manifest is not an object: {path}")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> None:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise PortablePredictorDaymetError(f"{label} commit changed.")


def _resolve_record(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = (root / str(record.get("path", ""))).resolve()
    if not path.is_relative_to(root):
        raise PortablePredictorDaymetError(f"{label} path escapes the project root.")
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise PortablePredictorDaymetError(f"{label} file lock changed: {path}")
    return path


def _pair(value: object, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise PortablePredictorDaymetError(f"{label} must contain two integer indices.")
    start, stop = (int(item) for item in value)
    if start < 0 or stop < start:
        raise PortablePredictorDaymetError(f"{label} is not an inclusive index window.")
    return start, stop


def _bbox(value: object, *, label: str) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 4
    ):
        raise PortablePredictorDaymetError(f"{label} must contain four coordinates.")
    west, south, east, north = (float(item) for item in value)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise PortablePredictorDaymetError(f"{label} has invalid WGS84 bounds.")
    return west, south, east, north


def _source_footprint(
    root: Path, city_id: str
) -> tuple[dict[str, Any], pd.DataFrame, tuple[int, int], tuple[int, int], tuple[float, ...]]:
    path = root / _SOURCE_FOOTPRINT_MANIFESTS[city_id]
    payload = _read_json(path)
    _verify_commit(payload, label=f"{city_id} source-footprint")
    access = payload.get("access_contract", {})
    if (
        payload.get("city", {}).get("id") != city_id
        or payload.get("city", {}).get("target_values_status") != "sealed"
        or access.get("landsat_thermal_values_read") is not False
        or access.get("landsat_target_qa_values_read") is not False
        or access.get("external_lst_values_read") is not False
    ):
        raise PortablePredictorDaymetError(
            f"{city_id} source-footprint no longer preserves sealed targets."
        )
    try:
        record = payload["output_tables"]["daymet_granules"]
        window = payload["source_families"]["daymet_cells"]["window"]
        bbox = payload["geography_input"]["bbox_wgs84"]
    except (KeyError, TypeError) as error:
        raise PortablePredictorDaymetError(
            f"{city_id} source-footprint lacks frozen Daymet inputs."
        ) from error
    granule_path = _resolve_record(
        root, record, label=f"{city_id} Daymet granule inventory"
    )
    granules = pd.read_parquet(granule_path)
    required = {
        "concept_id",
        "title",
        "variable",
        "year",
        "size_mb",
        "updated_at",
        "https_url",
        "opendap_url",
    }
    if missing := required.difference(granules.columns):
        raise PortablePredictorDaymetError(
            f"{city_id} Daymet inventory lacks columns: {sorted(missing)}"
        )
    y_indices = _pair(window.get("y_indices_inclusive"), label="Daymet y window")
    x_indices = _pair(window.get("x_indices_inclusive"), label="Daymet x window")
    return payload, granules, y_indices, x_indices, _bbox(
        bbox, label=f"{city_id} bbox"
    )


def _granule_from_row(row: Any) -> DaymetGranule:
    updated = row.updated_at
    return DaymetGranule(
        concept_id=str(row.concept_id),
        title=str(row.title),
        variable=str(row.variable),
        year=int(row.year),
        size_mb=float(row.size_mb),
        https_url=str(row.https_url),
        opendap_url=str(row.opendap_url),
        updated_at=None if pd.isna(updated) else str(updated),
    )


def build_external_download_tasks(
    project_root: str | Path,
) -> tuple[PortableDaymetDownloadTask, ...]:
    """Build the exact 18 frozen 2025 external-city Daymet download tasks."""

    root = _root(project_root)
    tasks: list[PortableDaymetDownloadTask] = []
    for city_id in EXTERNAL_CITY_IDS:
        _, frame, y_indices, x_indices, bbox = _source_footprint(root, city_id)
        checked = frame.copy()
        checked["variable"] = checked["variable"].astype(str).str.lower()
        checked["year"] = pd.to_numeric(checked["year"], errors="raise").astype(int)
        expected = {
            (EXTERNAL_DAYMET_YEAR, variable) for variable in DEFAULT_DAYMET_VARIABLES
        }
        observed = set(zip(checked["year"], checked["variable"], strict=True))
        if len(checked) != len(expected) or observed != expected:
            raise PortablePredictorDaymetError(
                f"{city_id} must have exactly six frozen 2025 Daymet granules."
            )
        by_variable = {str(row.variable): row for row in checked.itertuples(index=False)}
        for variable in DEFAULT_DAYMET_VARIABLES:
            granule = _granule_from_row(by_variable[variable])
            destination = (
                root
                / RAW_EXTERNAL_ROOT
                / city_id
                / (
                    f"daymet_v4r1_daily_na_{variable}_{EXTERNAL_DAYMET_YEAR}_"
                    f"{city_id}_subset.nc"
                )
            )
            tasks.append(
                PortableDaymetDownloadTask(
                    city_id=city_id,
                    variable=variable,
                    year=EXTERNAL_DAYMET_YEAR,
                    granule=granule,
                    y_indices=y_indices,
                    x_indices=x_indices,
                    bbox_wgs84=bbox,
                    source_url=build_daymet_direct_subset_url(
                        granule, y_indices=y_indices, x_indices=x_indices
                    ),
                    destination=destination,
                )
            )
    if len(tasks) != 18 or len({task.task_id for task in tasks}) != 18:
        raise AssertionError("External Daymet task construction did not produce 18 tasks.")
    return tuple(tasks)


# Descriptive alias used by runner code and notebooks.
build_external_daymet_download_tasks = build_external_download_tasks


def _validate_downloaded_task(task: PortableDaymetDownloadTask, path: Path) -> None:
    spec = inspect_daymet_netcdf(
        path,
        variable=task.variable,
        year=task.year,
        final_test_year=FINAL_TEST_YEAR_SENTINEL,
    )
    validate_daymet_direct_subset_spec(
        spec,
        y_indices=task.y_indices,
        x_indices=task.x_indices,
        bbox_wgs84=task.bbox_wgs84,
    )


def completed_external_download_tasks(
    project_root: str | Path,
) -> tuple[PortableDaymetDownloadTask, ...]:
    """Discover byte-present and scientifically valid external subset tasks."""

    completed: list[PortableDaymetDownloadTask] = []
    for task in build_external_download_tasks(project_root):
        if not task.destination.is_file():
            continue
        try:
            _validate_downloaded_task(task, task.destination)
        except (OSError, DaymetGridAuditError, ValueError) as error:
            raise PortablePredictorDaymetError(
                f"Existing external Daymet subset is invalid: {task.task_id}"
            ) from error
        completed.append(task)
    return tuple(completed)


def missing_external_download_tasks(
    project_root: str | Path,
) -> tuple[PortableDaymetDownloadTask, ...]:
    """Return frozen external tasks that do not yet have a valid local subset."""

    tasks = build_external_download_tasks(project_root)
    completed_ids = {
        task.task_id for task in completed_external_download_tasks(project_root)
    }
    return tuple(task for task in tasks if task.task_id not in completed_ids)


def external_download_status(project_root: str | Path) -> dict[str, object]:
    """Return a credential-free, UI-safe external Daymet readiness snapshot."""

    tasks = build_external_download_tasks(project_root)
    completed = completed_external_download_tasks(project_root)
    completed_ids = {task.task_id for task in completed}
    missing = tuple(task for task in tasks if task.task_id not in completed_ids)
    return {
        "state": "complete" if not missing else "incomplete",
        "completed": len(completed),
        "total": len(tasks),
        "remaining": len(missing),
        "credential_required": bool(missing),
        "completed_tasks": [task.as_status_record() for task in completed],
        "missing_tasks": [task.as_status_record() for task in missing],
    }


# Short status aliases kept intentionally explicit for the UI orchestrator.
discover_completed_tasks = completed_external_download_tasks
daymet_download_gap = external_download_status


def _notify(
    callback: ProgressCallback | None,
    *,
    task: PortableDaymetDownloadTask | None,
    completed: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    callback(
        {
            "city_id": None if task is None else task.city_id,
            "variable": None if task is None else task.variable,
            "completed": completed,
            "total": total,
            "message": message,
        }
    )


def _download_result(
    *,
    state: str,
    tasks: Sequence[PortableDaymetDownloadTask],
    completed_ids: set[str],
    downloaded: int,
    message: str,
) -> dict[str, object]:
    missing = [task for task in tasks if task.task_id not in completed_ids]
    return {
        "state": state,
        "paused": state == "incomplete",
        "completed": len(completed_ids),
        "total": len(tasks),
        "remaining": len(missing),
        "downloaded": downloaded,
        "message": message,
        "credential_required": state == "credential_required",
        "missing_tasks": [task.as_status_record() for task in missing],
    }


def download_missing_external_subsets(
    project_root: str | Path,
    credential: EarthdataBearerToken | None = None,
    progress_callback: ProgressCallback | None = None,
    pause_callback: PauseCallback | None = None,
) -> dict[str, object]:
    """Resume external Daymet downloads, pausing only at atomic file boundaries."""

    tasks = build_external_download_tasks(project_root)
    completed_ids = {
        task.task_id for task in completed_external_download_tasks(project_root)
    }
    total = len(tasks)
    _notify(
        progress_callback,
        task=None,
        completed=len(completed_ids),
        total=total,
        message="Daymet 下载状态已检查",
    )
    if len(completed_ids) == total:
        return _download_result(
            state="complete",
            tasks=tasks,
            completed_ids=completed_ids,
            downloaded=0,
            message="全部外部城市 Daymet 子集已完成",
        )
    if credential is None:
        _notify(
            progress_callback,
            task=None,
            completed=len(completed_ids),
            total=total,
            message="缺少 Earthdata token，等待安全输入",
        )
        return _download_result(
            state="credential_required",
            tasks=tasks,
            completed_ids=completed_ids,
            downloaded=0,
            message="缺少 Earthdata token",
        )
    if not isinstance(credential, EarthdataBearerToken):
        raise TypeError("credential must be an in-memory EarthdataBearerToken.")

    downloaded = 0
    for task in tasks:
        if task.task_id in completed_ids:
            continue
        if pause_callback is not None and bool(pause_callback()):
            return _download_result(
                state="incomplete",
                tasks=tasks,
                completed_ids=completed_ids,
                downloaded=downloaded,
                message="已在文件边界暂停",
            )
        _notify(
            progress_callback,
            task=task,
            completed=len(completed_ids),
            total=total,
            message=f"正在下载 {task.city_id} {task.variable}",
        )
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        validating = task.destination.with_suffix(task.destination.suffix + ".validating")
        validating.unlink(missing_ok=True)
        try:
            authenticated_netcdf_download(
                task.source_url,
                validating,
                credential=credential,
            )
            _validate_downloaded_task(task, validating)
            validating.replace(task.destination)
        except Exception:
            validating.unlink(missing_ok=True)
            raise
        completed_ids.add(task.task_id)
        downloaded += 1
        _notify(
            progress_callback,
            task=task,
            completed=len(completed_ids),
            total=total,
            message=f"已完成 {task.city_id} {task.variable}",
        )
    return _download_result(
        state="complete",
        tasks=tasks,
        completed_ids=completed_ids,
        downloaded=downloaded,
        message="全部外部城市 Daymet 子集已完成",
    )


def _subset_records(
    root: Path, city_id: str
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    if city_id == "los_angeles_ca":
        for year in LOS_ANGELES_YEARS:
            for variable in DEFAULT_DAYMET_VARIABLES:
                path = root / LOS_ANGELES_SUBSET_ROOT / (
                    f"daymet_v4r1_daily_na_{variable}_{year}_la_subset.nc"
                )
                if not path.is_file():
                    raise FileNotFoundError(f"Los Angeles Daymet subset is missing: {path}")
                records.append({"path": path, "variable": variable, "year": year})
    else:
        tasks = tuple(
            task
            for task in build_external_download_tasks(root)
            if task.city_id == city_id
        )
        for task in tasks:
            if not task.destination.is_file():
                raise FileNotFoundError(
                    f"External Daymet subset is missing: {task.destination}"
                )
            _validate_downloaded_task(task, task.destination)
            records.append(
                {
                    "path": task.destination,
                    "variable": task.variable,
                    "year": task.year,
                }
            )
    return pd.DataFrame(records, columns=["path", "variable", "year"])


def _fallback_city_support(root: Path, city_id: str) -> _SupportView:
    manifest_path = root / (
        f"manifests/multicity/cities/{city_id}/eligible_support/"
        "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
    )
    payload = _read_json(manifest_path)
    _verify_commit(payload, label=f"{city_id} eligible support")
    if (
        payload.get("city_id") != city_id
        or payload.get("state") != "complete_target_blind_city_worldcover_support"
        or payload.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
    ):
        raise PortablePredictorDaymetError(f"Invalid canonical support for {city_id}.")
    outputs = payload.get("outputs", {})
    zones_path = _resolve_record(
        root, outputs["tract_zones_30m"], label=f"{city_id} tract zones"
    )
    mask_path = _resolve_record(
        root, outputs["eligible_mask_30m"], label=f"{city_id} eligible mask"
    )
    support_path = _resolve_record(
        root, outputs["tract_support"], label=f"{city_id} tract support"
    )
    with rasterio.open(zones_path) as source:
        zones = source.read(1)
        transform = source.transform
        crs = source.crs
    with rasterio.open(mask_path) as source:
        if source.shape != zones.shape or source.transform != transform or source.crs != crs:
            raise PortablePredictorDaymetError(
                f"{city_id} canonical support rasters disagree."
            )
        eligible = source.read(1).astype(bool)
    support = pd.read_parquet(support_path)
    tract_geoids = tuple(sorted(support["tract_geoid"].astype(str)))
    if (
        len(tract_geoids) != int(payload["support"]["tract_count"])
        or int(zones.max(initial=0)) != len(tract_geoids)
        or np.any(eligible & (zones <= 0))
    ):
        raise PortablePredictorDaymetError(f"{city_id} canonical support is inconsistent.")
    return _SupportView(zones, eligible, tract_geoids, transform, crs)


def _load_city_support(root: Path, city_id: str) -> _SupportView:
    try:
        from la_heat.multicity.portable_predictor_components import load_city_support
    except ImportError:
        return _fallback_city_support(root, city_id)
    support = load_city_support(root, city_id)
    grid = support.grid
    return _SupportView(
        zones=np.asarray(support.zones),
        eligible_land=np.asarray(support.eligible_land, dtype=bool),
        tract_geoids=tuple(str(value) for value in support.tract_geoids),
        transform=grid.transform,
        crs=grid.crs,
    )


def compile_city_daymet(project_root: str | Path, city_id: str) -> dict[str, object]:
    """Compile and atomically publish one city's canonical Daymet components."""

    root = _root(project_root)
    if city_id not in CITY_IDS:
        raise ValueError(f"Unknown portable predictor city: {city_id}")
    key_path = root / INVENTORY_ROOT / city_id / "predictor_keys.parquet"
    if not key_path.is_file():
        raise FileNotFoundError(f"Portable predictor keys are missing: {key_path}")
    try:
        raw_keys = pd.read_parquet(key_path)
        keys = raw_keys.loc[:, ["tract_geoid", "target_date"]].copy()
    except (KeyError, OSError, ValueError) as error:
        raise PortablePredictorDaymetError(
            f"Unreadable portable predictor keys for {city_id}."
        ) from error
    if (
        "city_id" not in raw_keys
        or not raw_keys["city_id"].astype(str).eq(city_id).all()
    ):
        raise PortablePredictorDaymetError(
            f"Portable predictor keys have the wrong city for {city_id}."
        )

    support = _load_city_support(root, city_id)
    compilation = compile_daymet_feature_tables(
        _subset_records(root, city_id),
        keys,
        zone_raster=support.zones,
        eligible_land_mask=support.eligible_land,
        tract_geoids=support.tract_geoids,
        target_transform=support.transform,
        target_crs=support.crs,
        windows=(1, 3, 7),
        final_test_year=FINAL_TEST_YEAR_SENTINEL,
    )
    output = root / COMPONENT_ROOT / city_id
    paths = {
        "features": output / FEATURE_FILENAME,
        "audit": output / AUDIT_FILENAME,
        "weights": output / WEIGHTS_FILENAME,
        "tract_daily": output / TRACT_DAILY_FILENAME,
    }
    atomic_parquet(compilation.features, paths["features"])
    atomic_parquet(compilation.audit, paths["audit"])
    atomic_parquet(compilation.weights, paths["weights"])
    atomic_parquet(compilation.tract_daily, paths["tract_daily"])
    return {
        "state": "complete",
        "city_id": city_id,
        "row_count": len(compilation.features),
        "paths": paths,
    }
