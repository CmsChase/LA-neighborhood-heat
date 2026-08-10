"""Resumable orchestration for the four-city non-Sentinel predictor components.

The worker writes only public, target-blind predictor products.  It deliberately
stops in a visible waiting state when the three external-city Daymet subsets are
missing and no in-memory Earthdata credential was supplied by the local UI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.daymet_grid import DaymetAuthenticationError, load_earthdata_bearer_token
from la_heat.provenance import atomic_json, atomic_parquet, parquet_file_record, sha256_file

ALGORITHM_VERSION: Final = "portable-predictor-components-v1"
RUNTIME_RELATIVE: Final = Path("data/interim/multicity/portable_predictors/runtime")
COMPONENTS_RELATIVE: Final = Path("data/processed/multicity/portable_predictors/components")
INVENTORY_RELATIVE: Final = Path(
    "data/processed/multicity/portable_predictors/inventory/predictor_keys.parquet"
)
CONTRACT_RELATIVE: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
EXTERNAL_CITY_IDS: Final = ("phoenix_az", "houston_tx", "chicago_il")
DAYMET_VARIABLES: Final = ("dayl", "prcp", "srad", "tmax", "tmin", "vp")
DISTANCE_CHUNK_CELLS: Final = 100_000
WAITING_EXIT_CODE: Final = 75
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)


class PortablePredictorBuildError(RuntimeError):
    """Raised when the target-blind component build cannot continue."""


class EngineAlreadyRunningError(PortablePredictorBuildError):
    """Raised when another component worker owns the engine lock."""


@dataclass(frozen=True, slots=True)
class WorkUnit:
    task_id: str
    phase: str
    label: str
    city_id: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_message(value: object, *, limit: int = 500) -> str:
    text = _URL_QUERY.sub(r"\1?[query removed]", str(value))
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [removed]", text)
    return text[:limit]


class EngineLock:
    """Small OS-released single-instance lock for the exact worker."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: Any = None

    def __enter__(self) -> EngineLock:
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
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise EngineAlreadyRunningError(
                "Portable predictor worker is already running."
            ) from exc
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


def build_work_plan(project_root: Path) -> tuple[WorkUnit, ...]:
    """Derive the durable work count from the frozen four-city support."""

    units: list[WorkUnit] = []
    for city_id in CITY_IDS:
        units.append(WorkUnit(f"calendar:{city_id}", "calendar", "生成日历特征", city_id))
    for city_id in CITY_IDS:
        units.append(
            WorkUnit(f"static_base:{city_id}", "static_base", "对齐土地覆盖与地形", city_id)
        )
    units.append(
        WorkUnit(
            "daymet_compile:los_angeles_ca",
            "daymet_compile",
            "编译 Los Angeles 历史天气",
            "los_angeles_ca",
        )
    )
    support_root = project_root / (
        "data/processed/multicity/missing_support_calibration_evidence_v1/worldcover"
    )
    for city_id in CITY_IDS:
        table = pd.read_parquet(
            support_root / city_id / "tract_eligible_support.parquet",
            columns=["eligible_cell_count"],
        )
        chunk_count = math.ceil(int(table["eligible_cell_count"].sum()) / DISTANCE_CHUNK_CELLS)
        for chunk_index in range(chunk_count):
            units.append(
                WorkUnit(
                    f"gshhg:{city_id}:{chunk_index}",
                    "water_distance",
                    f"计算水体距离 {chunk_index + 1}/{chunk_count}",
                    city_id,
                )
            )
    for city_id in CITY_IDS:
        units.append(
            WorkUnit(f"static_finalize:{city_id}", "static_finalize", "聚合静态特征", city_id)
        )
    for city_id in EXTERNAL_CITY_IDS:
        for variable in DAYMET_VARIABLES:
            units.append(
                WorkUnit(
                    f"daymet_download:{city_id}:{variable}",
                    "daymet_download",
                    f"下载 Daymet {variable}",
                    city_id,
                )
            )
    for city_id in EXTERNAL_CITY_IDS:
        units.append(
            WorkUnit(f"daymet_compile:{city_id}", "daymet_compile", "编译天气特征", city_id)
        )
    units.append(WorkUnit("merge", "merge", "合并 41 个非卫星特征"))
    return tuple(units)


class StatusTracker:
    """Atomically persist progress while keeping durable completed task IDs."""

    def __init__(self, runtime: Path, plan: Sequence[WorkUnit]) -> None:
        self.runtime = runtime
        self.status_path = runtime / "status.json"
        self.completed_path = runtime / "completed_tasks.json"
        self.plan = tuple(plan)
        self.by_id = {unit.task_id: unit for unit in plan}
        self.completed, self.durations = self._read_completed()
        self.events: list[dict[str, str]] = self._read_events()
        self.current_started: float | None = None
        self.current_task: WorkUnit | None = None
        self.runtime.mkdir(parents=True, exist_ok=True)

    def _read_completed(self) -> tuple[set[str], dict[str, float]]:
        try:
            payload = json.loads(self.completed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set(), {}
        completed = {
            str(value)
            for value in payload.get("completed", [])
            if str(value) in self.by_id
        }
        durations = {
            str(key): float(value)
            for key, value in payload.get("durations_seconds", {}).items()
            if str(key) in self.by_id
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        }
        return completed, durations

    def _read_events(self) -> list[dict[str, str]]:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        events = payload.get("events", []) if isinstance(payload, dict) else []
        return [event for event in events if isinstance(event, dict)][-30:]

    def _save_completed(self) -> None:
        atomic_json(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "completed": sorted(self.completed),
                "durations_seconds": self.durations,
                "updated_at_utc": _utc_now(),
            },
            self.completed_path,
        )

    def event(self, message: object) -> None:
        self.events.append({"at": _utc_now(), "message": _safe_message(message)})
        self.events = self.events[-30:]

    def eta_seconds(self) -> float | None:
        samples = [value for value in self.durations.values() if value > 0]
        if not samples:
            return None
        return statistics.median(samples[-20:]) * (len(self.plan) - len(self.completed))

    def write(
        self,
        state: str,
        *,
        phase: str | None = None,
        current: Mapping[str, object] | None = None,
        error: Mapping[str, object] | str | None = None,
    ) -> None:
        completed = len(self.completed)
        total = len(self.plan)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": state,
            "phase": phase,
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "running": 1 if state == "running" else 0,
            "failed": 1 if state == "failed" else 0,
            "progress_fraction": completed / total if total else 1.0,
            "eta_seconds": self.eta_seconds(),
            "current": dict(current or {}),
            "events": self.events,
            "error": (
                None
                if error is None
                else {
                    str(key): (
                        value if isinstance(value, bool) else _safe_message(value)
                    )
                    for key, value in error.items()
                }
                if isinstance(error, Mapping)
                else _safe_message(error)
            ),
            "updated_at_utc": _utc_now(),
            "external_targets_read": False,
            "model_fit_performed": False,
        }
        atomic_json(payload, self.status_path)

    def start(self, task_id: str) -> None:
        unit = self.by_id[task_id]
        self.current_task = unit
        self.current_started = time.monotonic()
        self.event(f"开始：{unit.label} ({unit.city_id or '四城'})")
        self.write("running", phase=unit.phase, current=asdict(unit))

    def finish(self, task_id: str, *, announce: bool = True) -> None:
        if task_id in self.completed:
            return
        self.completed.add(task_id)
        if self.current_task is not None and self.current_task.task_id == task_id:
            started = self.current_started
            if started is not None:
                self.durations[task_id] = max(0.0, time.monotonic() - started)
            if announce:
                self.event(f"完成：{self.current_task.label}")
            self.current_task = None
            self.current_started = None
        self._save_completed()
        self.write("running")


def _pause_requested(runtime: Path) -> bool:
    return (runtime / "PAUSE_REQUESTED").is_file()


def _component_path(project_root: Path, city_id: str, name: str) -> Path:
    return project_root / COMPONENTS_RELATIVE / city_id / name


def _task_complete(tracker: StatusTracker, task_id: str) -> bool:
    return task_id in tracker.completed


def _run_simple_task(
    tracker: StatusTracker,
    runtime: Path,
    task_id: str,
    function: Any,
    *args: object,
) -> bool:
    if _task_complete(tracker, task_id):
        return True
    if _pause_requested(runtime):
        return False
    tracker.start(task_id)
    function(*args)
    tracker.finish(task_id)
    return True


def _merge_components(project_root: Path) -> dict[str, Any]:
    keys = pd.read_parquet(project_root / INVENTORY_RELATIVE)
    contract = json.loads((project_root / CONTRACT_RELATIVE).read_text(encoding="utf-8"))
    feature_order = [
        str(value)
        for value in contract["feature_registry"]["feature_order"]
        if not str(value).startswith("sentinel_")
    ]
    if len(feature_order) != 41:
        raise PortablePredictorBuildError("The non-Sentinel feature contract is not 41 columns.")

    city_frames: list[pd.DataFrame] = []
    for city_id in CITY_IDS:
        city_keys = keys.loc[keys["city_id"].eq(city_id)].copy()
        static = pd.read_parquet(_component_path(project_root, city_id, "static_features.parquet"))
        calendar = pd.read_parquet(
            _component_path(project_root, city_id, "calendar_features.parquet")
        )
        daymet = pd.read_parquet(_component_path(project_root, city_id, "daymet_features.parquet"))
        for frame in (static, calendar, daymet):
            if "city_id" in frame:
                frame.drop(columns="city_id", inplace=True)
        merged = city_keys.merge(static, on="tract_geoid", how="left", validate="many_to_one")
        merged = merged.merge(
            calendar,
            on=["tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
        merged = merged.merge(
            daymet,
            on=["tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
        missing_columns = sorted(set(feature_order) - set(merged.columns))
        if missing_columns:
            raise PortablePredictorBuildError(
                f"{city_id} component table lacks frozen features: {missing_columns}"
            )
        city_frames.append(merged)

    combined = pd.concat(city_frames, ignore_index=True)
    combined = combined.sort_values(
        ["city_id", "target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    if len(combined) != len(keys) or combined.duplicated(
        ["city_id", "tract_geoid", "target_date"]
    ).any():
        raise PortablePredictorBuildError("Component merge changed the frozen key universe.")
    numeric = combined.loc[:, feature_order].to_numpy(dtype=float)
    if np.isinf(numeric).any():
        raise PortablePredictorBuildError("Combined public predictors contain infinite values.")
    output = project_root / COMPONENTS_RELATIVE / "predictors_static_calendar_daymet.parquet"
    atomic_parquet(combined, output)
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_static_calendar_daymet_components",
        "generated_at_utc": _utc_now(),
        "city_count": 4,
        "row_count": len(combined),
        "feature_count": len(feature_order),
        "feature_order": feature_order,
        "output": {
            "path": output.relative_to(project_root).as_posix(),
            **parquet_file_record(output, combined),
        },
        "inventory_sha256": sha256_file(project_root / INVENTORY_RELATIVE),
        "contract_sha256": sha256_file(project_root / CONTRACT_RELATIVE),
        "access_contract": {
            "public_predictor_values_read": True,
            "external_target_or_qa_values_read": False,
            "landsat_thermal_values_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "build_resumable_sentinel_predictors",
    }
    atomic_json(payload, project_root / COMPONENTS_RELATIVE / "COMPONENTS_COMPLETE.json")
    return payload


def run_portable_predictor_build(project_root: str | Path) -> int:
    """Run all available work and return 75 only when a credential is needed."""

    root = Path(project_root).resolve()
    runtime = root / RUNTIME_RELATIVE
    runtime.mkdir(parents=True, exist_ok=True)
    plan = build_work_plan(root)
    tracker = StatusTracker(runtime, plan)

    from la_heat.multicity.portable_predictor_components import (
        build_calendar_component,
        build_gshhg_distance_component,
        build_static_base_component,
        finalize_static_component,
    )
    from la_heat.multicity.portable_predictor_daymet import (
        build_external_download_tasks,
        compile_city_daymet,
        completed_external_download_tasks,
        download_missing_external_subsets,
    )

    def pause() -> bool:
        return _pause_requested(runtime)

    try:
        tracker.event("四城 predictor 构建已开始或继续。")
        tracker.write("running", phase="startup")
        for city_id in CITY_IDS:
            if not _run_simple_task(
                tracker,
                runtime,
                f"calendar:{city_id}",
                build_calendar_component,
                root,
                city_id,
            ):
                tracker.event("已在安全检查点暂停。")
                tracker.write("paused")
                return 0
        for city_id in CITY_IDS:
            if not _run_simple_task(
                tracker,
                runtime,
                f"static_base:{city_id}",
                build_static_base_component,
                root,
                city_id,
            ):
                tracker.event("已在安全检查点暂停。")
                tracker.write("paused")
                return 0

        if not _run_simple_task(
            tracker,
            runtime,
            "daymet_compile:los_angeles_ca",
            compile_city_daymet,
            root,
            "los_angeles_ca",
        ):
            tracker.write("paused")
            return 0

        for city_id in CITY_IDS:
            city_units = [
                unit for unit in plan if unit.phase == "water_distance" and unit.city_id == city_id
            ]
            if all(unit.task_id in tracker.completed for unit in city_units):
                continue
            first_pending = next(
                unit for unit in city_units if unit.task_id not in tracker.completed
            )
            tracker.start(first_pending.task_id)

            def distance_progress(
                event: Mapping[str, object], active_city_id: str = city_id
            ) -> None:
                zero_based_index = int(event["chunk_index"]) - 1
                task_id = f"gshhg:{active_city_id}:{zero_based_index}"
                if task_id in tracker.by_id:
                    tracker.finish(task_id, announce=False)
                tracker.event(
                    event.get("message", f"{active_city_id} 水体距离分块已保存")
                )
                tracker.write(
                    "running",
                    phase="water_distance",
                    current={"city_id": active_city_id, **dict(event)},
                )

            result = build_gshhg_distance_component(
                root,
                city_id,
                progress_callback=distance_progress,
                pause_callback=pause,
            )
            complete = (
                result.get("state") == "complete"
                if isinstance(result, Mapping)
                else True
            )
            if complete:
                for unit in city_units:
                    tracker.finish(unit.task_id, announce=False)
                tracker.event(f"{city_id} 水体距离完成。")
            if pause() or not complete:
                tracker.event("已在水体距离分块边界暂停。")
                tracker.write("paused")
                return 0

        for city_id in CITY_IDS:
            if not _run_simple_task(
                tracker,
                runtime,
                f"static_finalize:{city_id}",
                finalize_static_component,
                root,
                city_id,
            ):
                tracker.write("paused")
                return 0

        completed_downloads = {
            (task.city_id, task.variable)
            for task in completed_external_download_tasks(root)
        }
        for city_id, variable in completed_downloads:
            tracker.finish(f"daymet_download:{city_id}:{variable}", announce=False)
        missing_downloads = [
            task
            for task in build_external_download_tasks(root)
            if (task.city_id, task.variable) not in completed_downloads
        ]
        if missing_downloads:
            try:
                credential = load_earthdata_bearer_token()
            except DaymetAuthenticationError:
                tracker.event(
                    "静态、日历和 LA 天气已保存；请输入 Earthdata token 下载三城天气。"
                )
                tracker.write(
                    "waiting_for_earthdata_token",
                    phase="daymet_download",
                    current={"remaining_downloads": len(missing_downloads)},
                )
                return WAITING_EXIT_CODE

            def download_progress(event: Mapping[str, object]) -> None:
                raw_city = event.get("city_id")
                raw_variable = event.get("variable")
                if (
                    raw_city is not None
                    and raw_variable is not None
                    and event.get("task_complete") is True
                ):
                    city_id = str(raw_city)
                    variable = str(raw_variable)
                    tracker.finish(
                        f"daymet_download:{city_id}:{variable}", announce=False
                    )
                tracker.event(event.get("message", "Daymet 下载进度已更新"))
                tracker.write(
                    "running",
                    phase="daymet_download",
                    current=dict(event),
                )

            try:
                result = download_missing_external_subsets(
                    root,
                    credential,
                    progress_callback=download_progress,
                    pause_callback=pause,
                )
            except DaymetAuthenticationError:
                tracker.event("Earthdata token 被拒绝；请在本机页面输入新的 token。")
                tracker.write("waiting_for_earthdata_token", phase="daymet_download")
                return WAITING_EXIT_CODE
            if pause() or (
                isinstance(result, Mapping) and result.get("state") != "complete"
            ):
                tracker.event("已在 Daymet 文件边界暂停。")
                tracker.write("paused")
                return 0

        for city_id in EXTERNAL_CITY_IDS:
            if not _run_simple_task(
                tracker,
                runtime,
                f"daymet_compile:{city_id}",
                compile_city_daymet,
                root,
                city_id,
            ):
                tracker.write("paused")
                return 0
        if not _run_simple_task(tracker, runtime, "merge", _merge_components, root):
            tracker.write("paused")
            return 0
        tracker.event("41 个静态、日历与天气 predictors 已全部完成。")
        tracker.write("complete", phase="complete")
        return 0
    except Exception as exc:
        retryable = isinstance(exc, (OSError, TimeoutError)) or type(exc).__module__.startswith(
            "requests"
        )
        tracker.event(f"任务失败：{type(exc).__name__}")
        tracker.write(
            "failed",
            phase=None if tracker.current_task is None else tracker.current_task.phase,
            current={} if tracker.current_task is None else asdict(tracker.current_task),
            error={
                "error_type": type(exc).__name__,
                "message": _safe_message(exc),
                "retryable": retryable,
            },
        )
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    arguments = parser.parse_args(argv)
    root = (
        arguments.project_root.resolve()
        if arguments.project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    runtime = root / RUNTIME_RELATIVE
    try:
        with EngineLock(runtime / "engine.lock"):
            return run_portable_predictor_build(root)
    except EngineAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
