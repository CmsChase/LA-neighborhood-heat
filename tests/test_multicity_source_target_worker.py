from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.source_target_worker import (
    SourceWorkerSettings,
    execute_source_queue,
    publish_source_completion,
)
from la_heat.multicity.target_authorization import TargetExecutionAuthorization
from la_heat.provenance import canonical_sha256, sha256_file

RUN_ID = "source-worker-test"


def _tasks() -> list[TaskSpec]:
    tasks = [
        TaskSpec(
            task_id=f"source-overpass-{index:02d}",
            kind="source_overpass",
            payload={"kind": "overpass_target", "unit_id": f"source-{index:02d}"},
        )
        for index in range(90)
    ]
    tasks.extend(
        TaskSpec(
            task_id=f"external-overpass-{index:02d}",
            kind="external_overpass",
            payload={"kind": "overpass_target", "unit_id": f"external-{index:02d}"},
        )
        for index in range(64)
    )
    tasks.append(
        TaskSpec(
            task_id="source-compile",
            kind="source_compile",
            payload={"kind": "city_compile", "unit_id": "compile:los_angeles_ca"},
        )
    )
    tasks.extend(
        TaskSpec(
            task_id=f"external-compile-{index}",
            kind="external_compile",
            payload={"kind": "city_compile", "unit_id": f"external-compile-{index}"},
        )
        for index in range(3)
    )
    tasks.append(
        TaskSpec(
            task_id="final-merge",
            kind="final_merge",
            payload={"kind": "final_merge", "unit_id": "final-merge"},
        )
    )
    return tasks


def _queue(tmp_path: Path, *, desired: str = "running") -> ModelRunQueue:
    queue = ModelRunQueue(tmp_path / "target.sqlite")
    queue.initialize_run(RUN_ID, _tasks(), desired_state=desired)
    return queue


class _FakeEngine:
    def __init__(
        self,
        events: list[str],
        lock: threading.Lock,
        failed_once: set[str],
    ) -> None:
        self.events = events
        self.lock = lock
        self.failed_once = failed_once

    def execute(self, payload: dict[str, object]) -> dict[str, str]:
        unit_id = str(payload["unit_id"])
        if unit_id == "source-00" and unit_id not in self.failed_once:
            with self.lock:
                if unit_id not in self.failed_once:
                    self.failed_once.add(unit_id)
                    raise RuntimeError(
                        "https://secret.example/?token=BEARER_SHOULD_NOT_BE_RECORDED"
                    )
        if unit_id == "source-01":
            time.sleep(0.04)
        with self.lock:
            self.events.append(str(payload["kind"]))
        return {"commit_sha256": hashlib.sha256(unit_id.encode()).hexdigest()}


def _settings(workers: int) -> SourceWorkerSettings:
    return SourceWorkerSettings(
        workers=workers,
        lease_seconds=5.0,
        heartbeat_interval_seconds=0.02,
        retry_base_seconds=0.001,
        retry_max_seconds=0.004,
        poll_seconds=0.001,
    )


def test_worker_retries_source_tasks_compiles_last_and_never_claims_external(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    events: list[str] = []
    lock = threading.Lock()
    failed_once: set[str] = set()
    factory_count = 0

    def factory() -> _FakeEngine:
        nonlocal factory_count
        with lock:
            factory_count += 1
        return _FakeEngine(events, lock, failed_once)

    status_path = tmp_path / "status.json"
    status = execute_source_queue(
        database_path=queue.path,
        run_id=RUN_ID,
        status_path=status_path,
        engine_factory=factory,
        settings=_settings(3),
    )

    tasks = queue.list_tasks(RUN_ID)
    source = [task for task in tasks if task.kind.startswith("source_")]
    external = [task for task in tasks if not task.kind.startswith("source_")]
    assert all(task.status == "complete" for task in source)
    assert all(task.status == "pending" and task.attempt == 0 for task in external)
    assert events[-1] == "city_compile"
    assert events[:-1].count("overpass_target") == 90
    assert queue.list_tasks(RUN_ID)[0].attempt == 2
    assert factory_count == 3
    assert status["state"] == "complete"
    assert status["source_counts"]["total"]["complete"] == 91
    assert status["external_counts"]["pending"] == 68
    assert status["retry_count"] == 1
    assert status["eta_seconds"] == 0.0
    encoded_status = status_path.read_text(encoding="utf-8")
    assert "secret.example" not in encoded_status
    assert "BEARER_SHOULD_NOT_BE_RECORDED" not in encoded_status


def test_paused_queue_exits_without_constructing_an_engine(tmp_path: Path) -> None:
    queue = _queue(tmp_path, desired="paused")

    def forbidden_factory() -> _FakeEngine:
        raise AssertionError("paused worker must not authenticate or construct an engine")

    status = execute_source_queue(
        database_path=queue.path,
        run_id=RUN_ID,
        status_path=tmp_path / "status.json",
        engine_factory=forbidden_factory,
        settings=_settings(2),
    )

    assert status["state"] == "paused"
    assert status["eta_seconds"] is None
    assert queue.counts_by_kind(RUN_ID)["source_overpass"]["pending"] == 90


def _write_committed(path: Path, payload: dict[str, object]) -> dict[str, object]:
    committed = dict(payload)
    committed["commit_sha256"] = canonical_sha256(committed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(committed), encoding="utf-8")
    return committed


def test_source_completion_is_append_only_and_binds_unopened_external_queue(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    execute_source_queue(
        database_path=queue.path,
        run_id=RUN_ID,
        status_path=tmp_path / "status.json",
        engine_factory=lambda: _FakeEngine([], threading.Lock(), {"source-00"}),
        settings=_settings(1),
    )

    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text("{}", encoding="utf-8")
    marker_path = tmp_path / "VALUES_OPENED.json"
    marker = _write_committed(
        marker_path,
        {"authorization_commit_sha256": "a" * 64},
    )
    authorization = TargetExecutionAuthorization(
        path=authorization_path,
        file_sha256=sha256_file(authorization_path),
        commit_sha256="a" * 64,
        lane="los_angeles_2020_2024_source",
        city_ids=("los_angeles_ca",),
        claim_id="source-claim",
        plan_commit_sha256="b" * 64,
        target_config_sha256="c" * 64,
        values_opened_marker=marker_path,
        external_prediction_commit_sha256=None,
    )
    cache_root = tmp_path / "claims" / "source"
    city_directory = cache_root / "cities" / "los_angeles_ca"
    target_file = city_directory / "targets.parquet"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(b"test-target-output")
    city_commit = _write_committed(
        city_directory / "CITY_TARGETS_COMPLETE.json",
        {
            "state": "complete",
            "output_files": {
                "targets.parquet": {
                    "bytes": target_file.stat().st_size,
                    "sha256": sha256_file(target_file),
                }
            },
        },
    )
    engine = SimpleNamespace(cache_root=cache_root, authorization=authorization)

    first = publish_source_completion(tmp_path, queue, RUN_ID, engine)
    second = publish_source_completion(tmp_path, queue, RUN_ID, engine)

    assert first == second
    assert first["commit_sha256"] == canonical_sha256(
        {key: value for key, value in first.items() if key not in {"path", "commit_sha256"}}
    )
    assert first["values_opened_marker"]["commit_sha256"] == marker["commit_sha256"]
    assert first["city_target_commit"]["commit_sha256"] == city_commit["commit_sha256"]
    assert first["external_cohort"] == {
        "task_count": 68,
        "tasks_claimed": False,
        "target_values_read": False,
    }
