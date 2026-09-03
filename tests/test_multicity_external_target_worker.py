from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

import pytest

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.external_target_worker import (
    EXTERNAL_KINDS,
    ExternalTargetWorkerError,
    ExternalWorkerSettings,
    execute_external_queue,
)
from la_heat.multicity.target_runtime import task_specs_from_target_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeEngine:
    def __init__(self, seen: list[dict[str, object]], lock: Lock) -> None:
        self.seen = seen
        self.lock = lock

    def execute(self, payload: dict[str, object]) -> dict[str, object]:
        with self.lock:
            self.seen.append(payload)
        return {"commit_sha256": "e" * 64}


def _queue_with_completed_source(
    project_root: Path, database: Path, *, complete_source: bool = True
) -> tuple[ModelRunQueue, str]:
    # Queue behavior uses the tracked plan as a fixture, not local raster evidence.
    plan = json.loads((project_root / "manifests/multicity/targets/TARGET_BUILD_PLAN.json")
                      .read_text(encoding="utf-8"))
    queue = ModelRunQueue(database)
    run_id = "external-worker-test"
    queue.initialize_run(
        run_id,
        task_specs_from_target_plan(plan),
        desired_state="running",
    )
    if complete_source:
        for index in range(91):
            task = queue.claim_next(
                run_id,
                owner="source-test",
                lease_seconds=30,
                kinds={"source_overpass", "source_compile"},
            )
            assert task is not None
            queue.complete(
                run_id,
                task.task_id,
                owner="source-test",
                generation=task.claim_generation,
                result={"commit_sha256": f"{index:064x}"},
            )
    return queue, run_id


def test_external_worker_claims_64_plus_3_and_never_final_merge(
    tmp_path: Path,
) -> None:
    database = tmp_path / "target.sqlite"
    status = tmp_path / "status.json"
    queue, run_id = _queue_with_completed_source(PROJECT_ROOT, database)
    seen: list[dict[str, object]] = []
    lock = Lock()

    result = execute_external_queue(
        database_path=database,
        run_id=run_id,
        status_path=status,
        engine_factory=lambda: _FakeEngine(seen, lock),
        settings=ExternalWorkerSettings(
            workers=2,
            lease_seconds=30,
            heartbeat_interval_seconds=10,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
            poll_seconds=0.001,
        ),
        completion_publisher=lambda _queue, _run: {"path": "complete.json"},
    )

    tasks = queue.list_tasks(run_id)
    external = [task for task in tasks if task.kind in EXTERNAL_KINDS]
    final = [task for task in tasks if task.kind == "final_merge"]
    assert len(seen) == 67
    assert {payload["lane"] for payload in seen} == {
        "three_city_2025_combined_external"
    }
    assert {payload["kind"] for payload in seen} == {
        "overpass_target",
        "city_compile",
    }
    assert len(external) == 67 and all(task.status == "complete" for task in external)
    assert len(final) == 1
    assert final[0].status == "pending"
    assert final[0].attempt == 0
    assert final[0].result is None
    assert result["state"] == "complete"
    assert result["completion_manifest"] == "complete.json"


def test_external_worker_refuses_to_start_before_all_source_tasks(
    tmp_path: Path,
) -> None:
    database = tmp_path / "target.sqlite"
    _queue, run_id = _queue_with_completed_source(
        PROJECT_ROOT, database, complete_source=False
    )

    with pytest.raises(ExternalTargetWorkerError, match="91 LA source"):
        execute_external_queue(
            database_path=database,
            run_id=run_id,
            status_path=tmp_path / "status.json",
            engine_factory=lambda: _FakeEngine([], Lock()),
        )
