import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from la_heat.multicity import m3_source_coverage_key_runtime_launch_v1 as launch
from la_heat.multicity import (
    m3_source_development_engine_coverage_key_repair_v1 as repair_engine,
)
from la_heat.multicity import (
    m3_source_development_engine_coverage_key_runtime_launch_v1 as launch_engine,
)
from la_heat.multicity import m3_source_development_engine_v2 as engine_v2


def test_transaction_cause_evidence_binds_prepare_and_idempotent_initialize() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = launch._transaction_cause_evidence(root)

    assert evidence["classification"] == (
        "idempotent_queue_initialization_changed_sqlite_bytes_only"
    )
    assert evidence["prepare_worker_call"]["line"] > 0
    assert evidence["runtime_queue_open"]["line"] > 0
    assert evidence["runtime_idempotent_initialize"]["line"] > 0
    assert evidence["queue_wal_schema_initialization"]["line"] > 0
    assert evidence["semantic_task_or_run_row_change_observed"] is False


def test_launch_engine_authenticates_both_permits_before_parent_contexts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    launch_permit = {"commit_sha256": "a" * 64}
    parent_permit = {
        "commit_sha256": "b" * 64,
        "adapter_contract": {},
    }

    def authenticate_bundle(*args: object, **kwargs: object) -> tuple[dict, dict]:
        del args, kwargs
        events.append("runtime_permits")
        return launch_permit, parent_permit

    def parent_create(
        cls: type[launch_engine.M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1],
        project_root: Path,
        **kwargs: object,
    ) -> launch_engine.M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1:
        del kwargs
        events.append("parent_contexts")
        engine = object.__new__(cls)
        engine.settings = SimpleNamespace(root=project_root)
        engine.before_value_access = lambda: None
        return engine

    monkeypatch.setattr(
        launch_engine,
        "authenticate_m3_source_coverage_key_runtime_launch_bundle",
        authenticate_bundle,
    )
    monkeypatch.setattr(
        engine_v2.M3SourceDevelopmentEngineV2,
        "create",
        classmethod(parent_create),
    )
    observed = launch_engine.M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1.create(tmp_path)

    assert observed.coverage_key_repair_authorization is parent_permit
    assert observed.coverage_key_runtime_launch_authorization is launch_permit
    assert events == ["runtime_permits", "parent_contexts"]


def test_launch_base_lock_and_completion_bind_launch_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object.__new__(launch_engine.M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1)
    engine.coverage_key_runtime_launch_authorization = {"commit_sha256": "a" * 64}
    monkeypatch.setattr(
        repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1,
        "_base_lock",
        lambda self, city_id: {"city_id": city_id, "parent_repair": True},
    )
    lock = engine._base_lock("los_angeles_ca")
    assert lock["coverage_key_runtime_launch_authorization_commit_sha256"] == ("a" * 64)

    parent = engine_v2.engine_v1._with_commit(
        {
            "state": "source_qa_candidates_complete",
            "coverage_key_repair_authorization_commit_sha256": "b" * 64,
        }
    )
    monkeypatch.setattr(
        repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1,
        "_build_qa_completion",
        lambda self: parent,
    )
    completion = engine._build_qa_completion()
    assert completion["coverage_key_runtime_launch_authorization_commit_sha256"] == "a" * 64
    assert completion["prepare_worker_v2_or_initialize_source_runtime_v2_performed"] is False
    assert completion["model_run_queue_schema_open_after_all_permits_authenticated"] is True
    assert completion["task_plan_rebuilt_reset_or_rewritten"] is False
    assert completion["database_hash_transition_used_as_runtime_input"] is False
    assert engine_v2.engine_v1._is_committed(completion)


def test_launch_runner_locks_before_factory_and_does_not_open_queue_on_factory_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextmanager
    def exclusive(path: Path) -> Any:
        assert path == (tmp_path / "control.json").with_suffix(".worker.lock")
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    class Queue:
        def __init__(self, path: Path) -> None:
            assert path == tmp_path / "tasks.sqlite"
            events.append("queue_open")

        def set_desired_state(self, run_id: str, state: str) -> None:
            assert run_id == launch.RUN_ID
            events.append(f"state_{state}")

    def fail_factory() -> Any:
        events.append("runtime_permit_factory")
        raise RuntimeError("synthetic permit failure")

    monkeypatch.setattr(launch_engine, "_exclusive_worker", exclusive)
    monkeypatch.setattr(launch_engine, "ModelRunQueue", Queue)
    settings = SimpleNamespace(
        control=tmp_path / "control.json",
        database=tmp_path / "tasks.sqlite",
    )
    with pytest.raises(RuntimeError, match="synthetic permit failure"):
        launch_engine.execute_coverage_key_runtime_launch_queue_locked(
            settings=settings,
            run_id=launch.RUN_ID,
            options=launch_engine.WorkerOptionsV2(phase=engine_v2.QA_PHASE),
            executor_factory=fail_factory,
        )

    assert events == [
        "lock_enter",
        "runtime_permit_factory",
        "lock_exit",
    ]


def test_launch_runner_opens_queue_after_factory_and_pauses_loop_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextmanager
    def exclusive(path: Path) -> Any:
        assert path == (tmp_path / "control.json").with_suffix(".worker.lock")
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    class Queue:
        def __init__(self, path: Path) -> None:
            assert path == tmp_path / "tasks.sqlite"
            events.append("queue_open")

        def set_desired_state(self, run_id: str, state: str) -> None:
            assert run_id == launch.RUN_ID
            events.append(f"state_{state}")

    def fail_loop(**kwargs: object) -> dict[str, object]:
        del kwargs
        events.append("unlocked_loop")
        raise RuntimeError("synthetic loop failure")

    monkeypatch.setattr(launch_engine, "_exclusive_worker", exclusive)
    monkeypatch.setattr(launch_engine, "ModelRunQueue", Queue)
    monkeypatch.setattr(launch_engine, "_execute_phase_queue_unlocked_v2", fail_loop)
    settings = SimpleNamespace(
        control=tmp_path / "control.json",
        database=tmp_path / "tasks.sqlite",
    )
    with pytest.raises(RuntimeError, match="synthetic loop failure"):
        launch_engine.execute_coverage_key_runtime_launch_queue_locked(
            settings=settings,
            run_id=launch.RUN_ID,
            options=launch_engine.WorkerOptionsV2(phase=engine_v2.QA_PHASE),
            executor_factory=lambda: events.append("runtime_permit_factory") or object(),
        )

    assert events == [
        "lock_enter",
        "runtime_permit_factory",
        "queue_open",
        "state_running",
        "unlocked_loop",
        "state_paused",
        "lock_exit",
    ]


def test_runtime_bundle_uses_safe_semantic_progress_not_database_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = {"commit_sha256": launch.PARENT_REPAIR_AUTHORIZATION_COMMIT_SHA256}
    permit = {"commit_sha256": "a" * 64}
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        launch,
        "load_runner_settings_v2",
        lambda root: SimpleNamespace(database=tmp_path / "dynamically-changed.sqlite"),
    )
    monkeypatch.setattr(
        launch,
        "_load_parent_runtime_permit",
        lambda root, require_terminal_queue: parent,
    )
    monkeypatch.setattr(launch, "_inside", lambda root, path, label: tmp_path / "a.json")
    monkeypatch.setattr(launch, "_read_committed", lambda path, label: permit)
    monkeypatch.setattr(
        launch,
        "_authenticate_launch_payload",
        lambda root, observed, authenticated_parent: events.append(
            ("payload", (observed, authenticated_parent))
        ),
    )
    monkeypatch.setattr(
        launch,
        "_queue_snapshot",
        lambda path: {"semantic": "progress", "database_sha256": "unread"},
    )
    monkeypatch.setattr(
        launch,
        "_validate_safe_progress",
        lambda snapshot, terminal: events.append(("progress", (snapshot, terminal))),
    )

    observed = launch.authenticate_m3_source_coverage_key_runtime_launch_bundle(
        tmp_path,
        require_terminal_queue=False,
    )

    assert observed == (permit, parent)
    assert events == [
        ("payload", (permit, parent)),
        (
            "progress",
            (
                {"semantic": "progress", "database_sha256": "unread"},
                False,
            ),
        ),
    ]


def test_light_gate_delegates_parent_gate_without_database_file_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permit = {"commit_sha256": "a" * 64}
    parent = {"commit_sha256": "b" * 64}
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(launch, "_inside", lambda root, path, label: tmp_path / "a.json")
    monkeypatch.setattr(launch, "_read_committed", lambda path, label: permit)
    monkeypatch.setattr(
        launch,
        "authenticate_m3_source_coverage_key_repair_value_gate",
        lambda *args: calls.append(args),
    )

    launch.authenticate_m3_source_coverage_key_runtime_launch_value_gate(
        tmp_path,
        permit,
        parent,
    )

    assert len(calls) == 1
    assert calls[0][1] is parent


def test_new_runner_has_no_prepare_or_initialize_path() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts/run_m3_source_coverage_key_runtime_launch_v1.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(runner)
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert "prepare_worker_v2" not in referenced_names
    assert "initialize_source_runtime_v2" not in referenced_names
    assert "execute_coverage_key_runtime_launch_queue_locked" in runner
    assert "M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1.create" in runner
