from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from la_heat.model_run_queue import (
    LeaseLostError,
    ModelRunQueue,
    TaskPlanDriftError,
    TaskSpec,
)


def _tasks(count: int = 3) -> list[TaskSpec]:
    return [
        TaskSpec(
            task_id=f"task-{index:02d}",
            kind="grouped_fold",
            payload={"fold": index, "features": ["a", "b"]},
        )
        for index in range(count)
    ]


def test_initialize_is_idempotent_but_exact_plan_drift_fails(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = ModelRunQueue(database)

    assert queue.initialize_run("run-a", _tasks(), now=100.0)
    assert not queue.initialize_run("run-a", _tasks(), now=101.0)
    assert queue.counts("run-a") == {
        "pending": 3,
        "running": 0,
        "complete": 0,
        "quarantined": 0,
        "total": 3,
    }

    changed = _tasks()
    changed[1] = TaskSpec("task-01", "grouped_fold", {"fold": 999})
    with pytest.raises(TaskPlanDriftError, match="plan drift"):
        queue.initialize_run("run-a", changed, now=102.0)
    with pytest.raises(TaskPlanDriftError, match="plan drift"):
        queue.initialize_run("run-a", list(reversed(_tasks())), now=102.0)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(model_run_tasks)").fetchall()
        }
    assert {
        "run_id",
        "task_id",
        "kind",
        "payload_json",
        "status",
        "attempt",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "claim_generation",
        "result_json",
        "error_type",
    } <= columns


def test_claim_kind_filter_keeps_outer_tasks_blocked_until_inner_phase(
    tmp_path: Path,
) -> None:
    queue = ModelRunQueue(tmp_path / "queue.sqlite3")
    tasks = [
        TaskSpec("inner-1", "inner_fit", {"index": 1}),
        TaskSpec("outer-1", "outer_refit", {"index": 1}),
    ]
    queue.initialize_run("run-a", tasks, now=100.0)

    inner = queue.claim_next(
        "run-a",
        owner="worker-a",
        lease_seconds=10,
        kinds=("inner_fit",),
        now=100.0,
    )
    assert inner is not None and inner.task_id == "inner-1"
    assert (
        queue.claim_next(
            "run-a",
            owner="worker-b",
            lease_seconds=10,
            kinds=("inner_fit",),
            now=100.0,
        )
        is None
    )
    outer = queue.claim_next(
        "run-a",
        owner="worker-b",
        lease_seconds=10,
        kinds=("outer_refit",),
        now=100.0,
    )
    assert outer is not None and outer.task_id == "outer-1"
    assert queue.counts_by_kind("run-a") == {
        "inner_fit": {
            "pending": 0,
            "running": 1,
            "complete": 0,
            "quarantined": 0,
            "total": 1,
        },
        "outer_refit": {
            "pending": 0,
            "running": 1,
            "complete": 0,
            "quarantined": 0,
            "total": 1,
        },
    }
    with pytest.raises(ValueError, match="must not be empty"):
        queue.claim_next(
            "run-a",
            owner="worker-c",
            lease_seconds=10,
            kinds=(),
            now=100.0,
        )


def test_begin_immediate_claims_are_unique_across_workers(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = ModelRunQueue(database)
    queue.initialize_run("run", _tasks(12), now=100.0)

    def claim(index: int) -> str | None:
        worker_queue = ModelRunQueue(database)
        task = worker_queue.claim_next(
            "run",
            owner=f"worker-{index}",
            lease_seconds=60.0,
            now=101.0,
        )
        return None if task is None else task.task_id

    with ThreadPoolExecutor(max_workers=12) as executor:
        task_ids = list(executor.map(claim, range(12)))

    assert None not in task_ids
    assert len(set(task_ids)) == 12
    assert queue.counts("run")["running"] == 12


def test_expired_lease_is_reclaimed_and_stale_owner_cannot_complete(
    tmp_path: Path,
) -> None:
    queue = ModelRunQueue(tmp_path / "queue.sqlite3")
    queue.initialize_run("run", _tasks(1), now=100.0)

    first = queue.claim_next("run", owner="worker-a", lease_seconds=10, now=100.0)
    assert first is not None
    assert first.attempt == 1
    assert first.claim_generation == 1

    assert queue.claim_next("run", owner="worker-b", lease_seconds=10, now=109.9) is None
    reclaimed = queue.claim_next("run", owner="worker-b", lease_seconds=10, now=110.0)
    assert reclaimed is not None
    assert reclaimed.task_id == first.task_id
    assert reclaimed.attempt == 2
    assert reclaimed.claim_generation == 2

    with pytest.raises(LeaseLostError, match="no longer current"):
        queue.complete(
            "run",
            first.task_id,
            owner="worker-a",
            generation=first.claim_generation,
            result={"score": -1},
            now=111.0,
        )
    queue.complete(
        "run",
        reclaimed.task_id,
        owner="worker-b",
        generation=reclaimed.claim_generation,
        result={"score": 0.8},
        now=111.0,
    )
    record = queue.list_tasks("run")[0]
    assert record.status == "complete"
    assert record.result == {"score": 0.8}


def test_pause_prevents_claim_until_run_is_resumed(tmp_path: Path) -> None:
    queue = ModelRunQueue(tmp_path / "queue.sqlite3")
    queue.initialize_run("run", _tasks(1), now=100.0)

    queue.set_desired_state("run", "paused", now=101.0)
    assert queue.get_desired_state("run") == "paused"
    assert queue.claim_next("run", owner="worker", lease_seconds=20, now=101.0) is None
    assert queue.counts("run")["pending"] == 1

    queue.set_desired_state("run", "running", now=102.0)
    claimed = queue.claim_next("run", owner="worker", lease_seconds=20, now=102.0)
    assert claimed is not None
    assert claimed.task_id == "task-00"


def test_heartbeat_retry_backoff_quarantine_and_listing(tmp_path: Path) -> None:
    queue = ModelRunQueue(tmp_path / "queue.sqlite3")
    queue.initialize_run(
        "run",
        _tasks(2),
        now=datetime.fromtimestamp(100, tz=UTC),
    )

    first = queue.claim_next("run", owner="worker", lease_seconds=5, now=100.0)
    assert first is not None
    assert queue.heartbeat(
        "run",
        first.task_id,
        owner="worker",
        generation=first.claim_generation,
        lease_seconds=20,
        now=102.0,
    ) == 122.0
    assert queue.retry(
        "run",
        first.task_id,
        owner="worker",
        generation=first.claim_generation,
        error_type="TransientError",
        base_delay_seconds=5,
        now=103.0,
    ) == 108.0

    # The other plan entry is claimable while task-00 is in backoff.
    second = queue.claim_next("run", owner="worker", lease_seconds=5, now=107.0)
    assert second is not None
    assert second.task_id == "task-01"
    queue.quarantine(
        "run",
        second.task_id,
        owner="worker",
        generation=second.claim_generation,
        error_type="PermanentError",
        result={"message": "bad grid"},
        now=107.0,
    )

    retried = queue.claim_next("run", owner="worker", lease_seconds=5, now=108.0)
    assert retried is not None
    assert retried.task_id == "task-00"
    assert retried.attempt == 2
    assert queue.retry(
        "run",
        retried.task_id,
        owner="worker",
        generation=retried.claim_generation,
        error_type="TransientAgain",
        base_delay_seconds=5,
        max_delay_seconds=100,
        now=109.0,
    ) == 119.0

    pending = queue.list_tasks("run", statuses=["pending"])
    quarantined = queue.list_tasks("run", statuses=["quarantined"])
    assert [task.task_id for task in pending] == ["task-00"]
    assert pending[0].error_type == "TransientAgain"
    assert quarantined[0].result == {"message": "bad grid"}
    assert queue.counts("run") == {
        "pending": 1,
        "running": 0,
        "complete": 0,
        "quarantined": 1,
        "total": 2,
    }
