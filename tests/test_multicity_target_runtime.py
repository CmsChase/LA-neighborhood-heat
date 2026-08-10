from __future__ import annotations

from pathlib import Path

import pytest

from la_heat.model_run_queue import ModelRunQueue, TaskPlanDriftError
from la_heat.multicity.target_runtime import (
    TargetRuntimeError,
    target_run_id,
    target_runtime_status,
    task_specs_from_target_plan,
)
from la_heat.multicity.target_transaction import (
    EXTERNAL_LANE,
    PREPARED_STATE,
    SOURCE_LANE,
)


def _plan() -> dict[str, object]:
    return {
        "state": PREPARED_STATE,
        "commit_sha256": "a" * 64,
        "work_plan": {
            "total_unit_count": 5,
            "units": [
                {
                    "unit_id": "target:los_angeles_ca:a",
                    "kind": "overpass_target",
                    "lane": SOURCE_LANE,
                    "city_id": "los_angeles_ca",
                    "ordinal": 1,
                },
                {
                    "unit_id": "target:phoenix_az:b",
                    "kind": "overpass_target",
                    "lane": EXTERNAL_LANE,
                    "city_id": "phoenix_az",
                    "ordinal": 2,
                },
                {
                    "unit_id": "compile:los_angeles_ca",
                    "kind": "city_compile",
                    "lane": SOURCE_LANE,
                    "city_id": "los_angeles_ca",
                    "ordinal": 3,
                },
                {
                    "unit_id": "compile:phoenix_az",
                    "kind": "city_compile",
                    "lane": EXTERNAL_LANE,
                    "city_id": "phoenix_az",
                    "ordinal": 4,
                },
                {
                    "unit_id": "merge:four_city_targets",
                    "kind": "final_merge",
                    "city_ids": ["los_angeles_ca", "phoenix_az"],
                    "ordinal": 5,
                },
            ],
        },
    }


def test_translates_frozen_plan_to_lane_specific_queue_kinds() -> None:
    specs = task_specs_from_target_plan(_plan())

    assert [spec.kind for spec in specs] == [
        "source_overpass",
        "external_overpass",
        "source_compile",
        "external_compile",
        "final_merge",
    ]
    assert target_run_id(_plan()) == "multicity-targets-v1-aaaaaaaaaaaaaaaa"


def test_runtime_starts_paused_and_preserves_city_counts(tmp_path: Path) -> None:
    plan = _plan()
    specs = task_specs_from_target_plan(plan)
    queue = ModelRunQueue(tmp_path / "target.sqlite")
    run_id = target_run_id(plan)
    assert queue.initialize_run(run_id, specs, desired_state="paused") is True

    status = target_runtime_status(queue, run_id)

    assert status["state"] == "paused_not_authorized"
    assert status["counts"] == {
        "pending": 5,
        "running": 0,
        "complete": 0,
        "quarantined": 0,
        "total": 5,
    }
    assert status["cities"]["los_angeles_ca"]["total"] == 2
    assert status["target_or_qa_values_read"] is False


def test_existing_queue_rejects_plan_drift(tmp_path: Path) -> None:
    plan = _plan()
    queue = ModelRunQueue(tmp_path / "target.sqlite")
    run_id = target_run_id(plan)
    queue.initialize_run(run_id, task_specs_from_target_plan(plan), desired_state="paused")
    plan["work_plan"]["units"][0]["city_id"] = "changed"  # type: ignore[index]

    with pytest.raises(TaskPlanDriftError):
        queue.initialize_run(
            run_id,
            task_specs_from_target_plan(plan),
            desired_state="paused",
        )


def test_rejects_reordered_plan() -> None:
    plan = _plan()
    plan["work_plan"]["units"][0]["ordinal"] = 2  # type: ignore[index]

    with pytest.raises(TargetRuntimeError, match="order changed"):
        task_specs_from_target_plan(plan)
