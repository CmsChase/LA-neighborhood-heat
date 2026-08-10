"""Durable, initially paused runtime for the frozen multicity target plan."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.target_transaction import (
    EXTERNAL_LANE,
    PREPARED_STATE,
    SOURCE_LANE,
    stage_multicity_target_build_plan,
)

DEFAULT_DATABASE: Final = Path(
    "data/interim/multicity/targets/runtime/target_tasks.sqlite"
)
RUN_PREFIX: Final = "multicity-targets-v1"
SOURCE_KINDS: Final = frozenset({"source_overpass", "source_compile"})
EXTERNAL_KINDS: Final = frozenset({"external_overpass", "external_compile"})
FINAL_KINDS: Final = frozenset({"final_merge"})


class TargetRuntimeError(RuntimeError):
    """Raised when the frozen target plan cannot initialize one durable queue."""


def _runtime_kind(unit: dict[str, Any]) -> str:
    kind = str(unit.get("kind", ""))
    lane = unit.get("lane")
    if kind == "overpass_target" and lane == SOURCE_LANE:
        return "source_overpass"
    if kind == "overpass_target" and lane == EXTERNAL_LANE:
        return "external_overpass"
    if kind == "city_compile" and lane == SOURCE_LANE:
        return "source_compile"
    if kind == "city_compile" and lane == EXTERNAL_LANE:
        return "external_compile"
    if kind == "final_merge" and lane is None:
        return "final_merge"
    raise TargetRuntimeError(f"Unknown target work unit: {unit.get('unit_id')!r}")


def task_specs_from_target_plan(plan: dict[str, Any]) -> tuple[TaskSpec, ...]:
    """Translate the authenticated JSON work plan to immutable queue tasks."""

    if plan.get("state") != PREPARED_STATE:
        raise TargetRuntimeError("Target build plan is not in the prepared state.")
    work_plan = plan.get("work_plan")
    if not isinstance(work_plan, dict) or not isinstance(work_plan.get("units"), list):
        raise TargetRuntimeError("Target build plan has no immutable unit list.")
    units = work_plan["units"]
    specs: list[TaskSpec] = []
    for expected_ordinal, raw in enumerate(units, start=1):
        if not isinstance(raw, dict) or raw.get("ordinal") != expected_ordinal:
            raise TargetRuntimeError("Target work-unit order changed.")
        unit = dict(raw)
        task_id = unit.get("unit_id")
        if not isinstance(task_id, str) or not task_id:
            raise TargetRuntimeError("Target work unit lacks a stable unit_id.")
        specs.append(TaskSpec(task_id=task_id, kind=_runtime_kind(unit), payload=unit))
    declared_total = work_plan.get("total_unit_count")
    if declared_total != len(specs) or len({spec.task_id for spec in specs}) != len(specs):
        raise TargetRuntimeError("Target work-unit count or identity changed.")
    return tuple(specs)


def _database_path(root: Path, value: str | Path | None) -> Path:
    path = DEFAULT_DATABASE if value is None else Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise TargetRuntimeError("Target runtime database must stay inside the project.")
    return resolved


def target_run_id(plan: dict[str, Any]) -> str:
    commit = plan.get("commit_sha256")
    if not isinstance(commit, str) or len(commit) != 64:
        raise TargetRuntimeError("Target build-plan commit is invalid.")
    return f"{RUN_PREFIX}-{commit[:16]}"


def target_runtime_status(queue: ModelRunQueue, run_id: str) -> dict[str, Any]:
    """Return a compact dashboard-safe status without opening target values."""

    counts = queue.counts(run_id)
    tasks = queue.list_tasks(run_id)
    cities: dict[str, dict[str, int]] = {}
    for task in tasks:
        city_id = task.payload.get("city_id") if isinstance(task.payload, dict) else None
        if not isinstance(city_id, str):
            continue
        city = cities.setdefault(
            city_id,
            {"pending": 0, "running": 0, "complete": 0, "quarantined": 0, "total": 0},
        )
        city[task.status] += 1
        city["total"] += 1
    desired = queue.get_desired_state(run_id)
    if counts["complete"] == counts["total"]:
        state = "complete"
    elif desired == "paused":
        state = "paused_not_authorized"
    else:
        state = "running"
    return {
        "schema_version": 1,
        "state": state,
        "run_id": run_id,
        "desired_state": desired,
        "counts": counts,
        "counts_by_kind": queue.counts_by_kind(run_id),
        "cities": cities,
        "source_execution_authorized": False,
        "external_execution_authorized": False,
        "target_or_qa_values_read": False,
    }


def initialize_target_runtime(
    project_root: str | Path,
    *,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Authenticate the plan and initialize its queue in the paused state."""

    root = Path(project_root).resolve()
    plan = stage_multicity_target_build_plan(root, check_only=True)
    specs = task_specs_from_target_plan(plan)
    queue = ModelRunQueue(_database_path(root, database_path))
    run_id = target_run_id(plan)
    queue.initialize_run(run_id, specs, desired_state="paused")
    return target_runtime_status(queue, run_id)
