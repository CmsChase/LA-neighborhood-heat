from __future__ import annotations

import re
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from la_heat.sentinel_dashboard import (
    AcquisitionJob,
    CooperativeAcquisitionRunner,
    DashboardProcessLock,
    LazyDashboardRunner,
    TransientSpatialSupportError,
    create_server,
)
from la_heat.sentinel_feature_builder import _pipeline_fingerprint


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for dashboard state.")


def _jobs(count: int) -> list[AcquisitionJob]:
    return [
        AcquisitionJob(f"physical-{index}", object(), pd.DataFrame({"item_id": [index]}))
        for index in range(count)
    ]


def _patch_lazy_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, count: int = 2
) -> None:
    inventory_directory = tmp_path / "inventory"
    output_directory = tmp_path / "output"
    inventory_directory.mkdir()
    output_directory.mkdir()
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_sentinel_stage_config",
        lambda _path: SimpleNamespace(
            raw={
                "paths": {
                    "inventory_directory": str(inventory_directory),
                    "output_directory": str(output_directory),
                }
            }
        ),
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.pd.read_csv",
        lambda _path: pd.DataFrame(
            {"physical_acquisition_id": [f"physical-{index}" for index in range(count)]}
        ),
    )


class _FakeDelegate:
    def __init__(self, *, completed: int = 1, total: int = 2) -> None:
        self.start_calls = 0
        self.pause_calls = 0
        self.shutdown_calls = 0
        self.completed = completed
        self.total = total
        self.state = "idle"
        self.error: dict[str, object] | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "pause_requested": False,
            "workers": 1,
            "completed": self.completed,
            "total": self.total,
            "pending": self.total - self.completed,
            "active": [],
            "progress_fraction": self.completed / self.total,
            "mean_seconds_per_acquisition": None,
            "eta_seconds": None,
            "started_at_utc": None,
            "error": self.error,
            "last_failure": None,
            "retry_attempts_total": 0,
            "retrying_count": 0,
            "retrying": [],
            "quarantined_count": 0,
            "events": [],
            "last_checkpoint_state": None,
            "completed_ids_sha256": "fake",
        }

    def start_or_resume(self) -> dict[str, object]:
        self.start_calls += 1
        self.state = "running"
        return self.snapshot()

    def request_pause(self) -> dict[str, object]:
        self.pause_calls += 1
        self.state = "paused"
        return self.snapshot()

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.state = "paused"

    def fail(self, *, retryable: bool) -> None:
        self.error = {
            "physical_acquisition_id": "delegate",
            "type": "ConnectionError" if retryable else "ValueError",
            "retryable": retryable,
        }
        self.state = "error"


def test_safe_pause_waits_for_active_jobs_and_starts_no_new_job() -> None:
    jobs = _jobs(4)
    started: list[str] = []
    started_lock = threading.Lock()
    two_started = threading.Event()
    release = threading.Event()
    checkpoint_calls = 0

    def worker(job: AcquisitionJob) -> dict[str, object]:
        with started_lock:
            started.append(job.physical_id)
            if len(started) == 2:
                two_started.set()
        assert release.wait(timeout=5)
        return {"state": "complete"}

    def checkpoint() -> dict[str, object]:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 1:
            return {"state": "partial_ready", "promoted_outputs_valid": False}
        return {"state": "complete", "promoted_outputs_valid": True}

    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=[job.physical_id for job in jobs],
        initial_completed_ids=[],
        worker=worker,
        checkpoint=checkpoint,
        workers=2,
    )
    runner.start_or_resume()
    assert two_started.wait(timeout=5)
    runner.request_pause()
    release.set()
    assert runner.wait_for_final_state(timeout=5) == "paused"
    assert set(started) == {"physical-0", "physical-1"}
    assert runner.snapshot()["completed"] == 2
    assert checkpoint_calls == 1

    runner.start_or_resume()
    _wait_until(lambda: runner.snapshot()["state"] == "complete")
    assert set(started) == {job.physical_id for job in jobs}
    assert runner.snapshot()["completed"] == 4
    assert checkpoint_calls == 2


def test_transient_job_retries_while_peer_and_fresh_job_continue() -> None:
    jobs = _jobs(3)
    attempts: Counter[str] = Counter()
    attempts_lock = threading.Lock()
    peer_started = threading.Event()
    release_peer = threading.Event()
    fresh_started = threading.Event()
    checkpoint_calls = 0

    def worker(job: AcquisitionJob) -> dict[str, object]:
        with attempts_lock:
            attempts[job.physical_id] += 1
            attempt = attempts[job.physical_id]
        if job.physical_id == "physical-0" and attempt == 1:
            assert peer_started.wait(timeout=5)
            raise requests.ConnectionError(
                "https://example.test/asset?sig=secret"
            )
        if job.physical_id == "physical-1":
            peer_started.set()
            assert release_peer.wait(timeout=5)
        if job.physical_id == "physical-2":
            fresh_started.set()
        return {"state": "complete"}

    def checkpoint() -> dict[str, object]:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        return {"state": "complete", "promoted_outputs_valid": True}

    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=[job.physical_id for job in jobs],
        initial_completed_ids=[],
        worker=worker,
        checkpoint=checkpoint,
        workers=2,
        retry_delays=(0.2,),
    )
    runner.start_or_resume()
    assert fresh_started.wait(timeout=5)
    during_retry = runner.snapshot()
    assert during_retry["state"] == "running"
    assert during_retry["pause_requested"] is False
    assert "physical-1" in {
        item["physical_acquisition_id"] for item in during_retry["active"]
    }
    assert during_retry["last_failure"] == {
        "physical_acquisition_id": "physical-0",
        "type": "ConnectionError",
        "retryable": True,
        "failed_attempts": 1,
    }
    assert "secret" not in str(during_retry)

    release_peer.set()
    assert runner.wait_for_final_state(timeout=5) == "complete"
    complete = runner.snapshot()
    assert attempts == Counter(
        {"physical-0": 2, "physical-1": 1, "physical-2": 1}
    )
    assert complete["completed"] == 3
    assert complete["retry_attempts_total"] == 1
    assert checkpoint_calls == 1
    assert "secret" not in str(complete)
    runner.shutdown()


def test_retry_exhaustion_fails_closed_after_other_jobs_finish() -> None:
    jobs = _jobs(3)
    attempts: Counter[str] = Counter()
    checkpoint_calls = 0

    def worker(job: AcquisitionJob) -> dict[str, object]:
        attempts[job.physical_id] += 1
        if job.physical_id == "physical-0":
            raise requests.ConnectionError(
                "https://example.test/asset?sig=exhausted-secret"
            )
        return {"state": "complete"}

    def checkpoint() -> dict[str, object]:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        return {"state": "complete", "promoted_outputs_valid": True}

    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=[job.physical_id for job in jobs],
        initial_completed_ids=[],
        worker=worker,
        checkpoint=checkpoint,
        workers=2,
        retry_delays=(0.01,),
    )
    runner.start_or_resume()
    assert runner.wait_for_final_state(timeout=5) == "error"
    snapshot = runner.snapshot()
    assert attempts == Counter(
        {"physical-0": 2, "physical-1": 1, "physical-2": 1}
    )
    assert snapshot["completed"] == 2
    assert snapshot["pending"] == 1
    assert snapshot["active"] == []
    assert snapshot["quarantined_count"] == 1
    assert snapshot["error"] == {
        "physical_acquisition_id": "physical-0",
        "type": "ConnectionError",
        "retryable": True,
        "failed_attempts": 2,
        "quarantined_count": 1,
    }
    assert checkpoint_calls == 0
    assert "exhausted-secret" not in str(snapshot)
    runner.shutdown()


def test_non_transient_failure_fails_closed_after_other_jobs_finish() -> None:
    jobs = _jobs(3)
    attempts: Counter[str] = Counter()
    checkpoint_calls = 0

    def worker(job: AcquisitionJob) -> dict[str, object]:
        attempts[job.physical_id] += 1
        if job.physical_id == "physical-0":
            raise ValueError("https://example.test/asset?sig=permanent-secret")
        return {"state": "complete"}

    def checkpoint() -> dict[str, object]:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        return {"state": "complete", "promoted_outputs_valid": True}

    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=[job.physical_id for job in jobs],
        initial_completed_ids=[],
        worker=worker,
        checkpoint=checkpoint,
        workers=2,
        retry_delays=(0.01,),
    )
    runner.start_or_resume()
    assert runner.wait_for_final_state(timeout=5) == "error"
    snapshot = runner.snapshot()
    assert attempts == Counter(
        {"physical-0": 1, "physical-1": 1, "physical-2": 1}
    )
    assert snapshot["completed"] == 2
    assert snapshot["pending"] == 1
    assert snapshot["quarantined_count"] == 1
    assert snapshot["error"] == {
        "physical_acquisition_id": "physical-0",
        "type": "ValueError",
        "retryable": False,
        "failed_attempts": 1,
        "quarantined_count": 1,
    }
    assert checkpoint_calls == 0
    assert "permanent-secret" not in str(snapshot)
    runner.shutdown()


def test_pause_delays_scheduled_retry_until_resume() -> None:
    jobs = _jobs(1)
    attempts = 0
    first_failure = threading.Event()
    checkpoint_calls = 0

    def worker(_job: AcquisitionJob) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_failure.set()
            raise requests.ConnectionError("temporary")
        return {"state": "complete"}

    def checkpoint() -> dict[str, object]:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        return {
            "state": "partial_ready" if checkpoint_calls == 1 else "complete",
            "promoted_outputs_valid": checkpoint_calls > 1,
        }

    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=[job.physical_id for job in jobs],
        initial_completed_ids=[],
        worker=worker,
        checkpoint=checkpoint,
        retry_delays=(0.1,),
    )
    runner.start_or_resume()
    assert first_failure.wait(timeout=5)
    runner.request_pause()
    assert runner.wait_for_final_state(timeout=5) == "paused"
    time.sleep(0.15)
    paused = runner.snapshot()
    assert attempts == 1
    assert paused["retrying_count"] == 1
    assert checkpoint_calls == 1

    runner.start_or_resume()
    assert runner.wait_for_final_state(timeout=5) == "complete"
    assert attempts == 2
    assert checkpoint_calls == 2
    runner.shutdown()


def test_progress_summary_failure_does_not_abort_atomic_jobs() -> None:
    jobs = _jobs(1)
    runner = CooperativeAcquisitionRunner(
        jobs=jobs,
        all_ids=["physical-0"],
        initial_completed_ids=[],
        worker=lambda _job: {"state": "complete"},
        checkpoint=lambda: {"state": "complete", "promoted_outputs_valid": True},
        progress_hook=lambda _snapshot: (_ for _ in ()).throw(OSError("audit only")),
    )
    runner.start_or_resume()
    assert runner.wait_for_final_state(timeout=5) == "complete"
    assert runner.snapshot()["completed"] == 1
    assert "进度摘要写入失败" in str(runner.snapshot()["events"])
    runner.shutdown()


def test_runner_bounds_and_inventory_partition_fail_closed() -> None:
    jobs = _jobs(1)
    with pytest.raises(ValueError, match="workers"):
        CooperativeAcquisitionRunner(
            jobs=jobs,
            all_ids=["physical-0"],
            initial_completed_ids=[],
            worker=lambda _job: {},
            checkpoint=lambda: {},
            workers=3,
        )
    with pytest.raises(ValueError, match="overlap"):
        CooperativeAcquisitionRunner(
            jobs=jobs,
            all_ids=["physical-0"],
            initial_completed_ids=["physical-0"],
            worker=lambda _job: {},
            checkpoint=lambda: {},
        )


@pytest.mark.parametrize(
    "retry_delays",
    [
        (0.0,),
        (-0.1,),
        (float("inf"),),
        (float("nan"),),
    ],
)
def test_runner_rejects_invalid_retry_delays(
    retry_delays: tuple[float, ...],
) -> None:
    jobs = _jobs(1)
    with pytest.raises(ValueError, match="finite and positive"):
        CooperativeAcquisitionRunner(
            jobs=jobs,
            all_ids=["physical-0"],
            initial_completed_ids=[],
            worker=lambda _job: {},
            checkpoint=lambda: {},
            retry_delays=retry_delays,
        )


def test_lazy_runner_queues_and_cancels_start_during_remote_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0
    attempted = threading.Event()

    def unavailable(*_args: object, **_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        attempted.set()
        raise TransientSpatialSupportError("signed-url-must-not-appear")

    _patch_lazy_inventory(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context", unavailable
    )

    runner = LazyDashboardRunner(
        research_config_path="configs/research.toml",
        stage_config_path="configs/sentinel_features.toml",
        workers=1,
        retry_seconds=0.05,
    )
    runner.begin_initialization()
    assert attempted.wait(timeout=5)
    queued = runner.start_or_resume()
    assert queued["state"] == "initializing"
    assert queued["start_queued"] is True
    assert queued["verification_pending"] is True
    assert "signed-url-must-not-appear" not in str(queued)

    cancelled = runner.request_pause()
    assert cancelled["state"] == "initializing"
    assert cancelled["start_queued"] is False
    assert attempts >= 1
    runner.shutdown()


def test_lazy_runner_hands_queued_start_to_validated_delegate_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    context = SimpleNamespace(current_ids=("physical-0",))
    delegate = _FakeDelegate()

    def load_context(*_args: object, **_kwargs: object) -> object:
        entered.set()
        assert release.wait(timeout=5)
        return context

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context", load_context
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner",
        lambda _context, *, workers: delegate,
    )
    runner = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    runner.begin_initialization()
    assert entered.wait(timeout=5)
    assert runner.start_or_resume()["start_queued"] is True
    release.set()
    _wait_until(lambda: runner.snapshot()["state"] == "running")
    snapshot = runner.snapshot()
    assert delegate.start_calls == 1
    assert snapshot["completed"] == 1
    assert snapshot["verification_pending"] is False
    assert snapshot["start_queued"] is False
    runner.shutdown()
    assert delegate.shutdown_calls == 1


def test_lazy_runner_cancelled_queue_stays_idle_after_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    delegate = _FakeDelegate()

    def load_context(*_args: object, **_kwargs: object) -> object:
        entered.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(current_ids=("physical-0",))

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context", load_context
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner",
        lambda _context, *, workers: delegate,
    )
    runner = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    runner.begin_initialization()
    assert entered.wait(timeout=5)
    runner.start_or_resume()
    runner.request_pause()
    release.set()
    _wait_until(lambda: runner.snapshot()["state"] == "idle")
    assert delegate.start_calls == 0
    runner.shutdown()


def test_lazy_runner_sanitizes_permanent_error_and_can_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    should_fail = True
    delegate = _FakeDelegate()

    def load_context(*_args: object, **_kwargs: object) -> object:
        if should_fail:
            raise ValueError("https://example.test/asset?sig=never-expose")
        return SimpleNamespace(current_ids=("physical-0",))

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context", load_context
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner",
        lambda _context, *, workers: delegate,
    )
    runner = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    runner.begin_initialization()
    _wait_until(lambda: runner.snapshot()["state"] == "error")
    failed = runner.snapshot()
    assert failed["verification_pending"] is True
    assert failed["error"] == {
        "physical_acquisition_id": "initialization",
        "type": "ValueError",
        "retryable": False,
    }
    assert "never-expose" not in str(failed)

    should_fail = False
    runner.start_or_resume()
    _wait_until(lambda: runner.snapshot()["state"] == "running")
    assert delegate.start_calls == 1
    runner.shutdown()


def test_lazy_runner_rebuilds_retryable_delegate_and_starts_automatically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    load_calls = 0
    contexts = [
        SimpleNamespace(current_ids=()),
        SimpleNamespace(current_ids=("physical-1",)),
    ]
    first = _FakeDelegate(completed=0)
    second = _FakeDelegate(completed=1)
    delegates = [first, second]
    build_calls = 0

    def load_context(*_args: object, **_kwargs: object) -> object:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            entered.set()
            assert release.wait(timeout=5)
        return contexts[min(load_calls - 1, len(contexts) - 1)]

    def build_runner(_context: object, *, workers: int) -> _FakeDelegate:
        nonlocal build_calls
        assert workers == 1
        delegate = delegates[build_calls]
        build_calls += 1
        return delegate

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context", load_context
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner", build_runner
    )
    runner = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
        retry_seconds=0.02,
        supervisor_poll_seconds=0.01,
    )
    runner.begin_initialization()
    assert entered.wait(timeout=5)
    assert runner.start_or_resume()["start_queued"] is True
    release.set()
    _wait_until(lambda: first.start_calls == 1)

    first.fail(retryable=True)
    _wait_until(lambda: second.start_calls == 1)
    snapshot = runner.snapshot()
    assert load_calls == 2
    assert build_calls == 2
    assert first.shutdown_calls == 1
    assert second.start_calls == 1
    assert snapshot["state"] == "running"
    assert snapshot["completed"] == 1
    assert snapshot["auto_restart_enabled"] is True
    assert snapshot["automatic_restart_count"] == 1
    runner.shutdown()


def test_pause_during_restart_prevents_delegate_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    shutdown_started = threading.Event()
    release_shutdown = threading.Event()

    class BlockingShutdownDelegate(_FakeDelegate):
        def shutdown(self) -> None:
            self.shutdown_calls += 1
            shutdown_started.set()
            assert release_shutdown.wait(timeout=5)
            self.state = "paused"

    first = BlockingShutdownDelegate()
    second = _FakeDelegate()
    delegates = [first, second]
    build_calls = 0

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context",
        lambda *_args, **_kwargs: SimpleNamespace(current_ids=("physical-0",)),
    )

    def build_runner(_context: object, *, workers: int) -> _FakeDelegate:
        nonlocal build_calls
        assert workers == 1
        delegate = delegates[build_calls]
        build_calls += 1
        return delegate

    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner", build_runner
    )
    runner = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
        retry_seconds=0.02,
        supervisor_poll_seconds=0.01,
    )
    runner.begin_initialization()
    _wait_until(lambda: runner.snapshot()["state"] == "idle")
    runner.start_or_resume()
    _wait_until(lambda: first.start_calls == 1)

    first.fail(retryable=True)
    assert shutdown_started.wait(timeout=5)
    paused = runner.request_pause()
    assert paused["auto_restart_enabled"] is False
    assert paused["start_queued"] is False
    release_shutdown.set()
    time.sleep(0.08)
    assert build_calls == 1
    assert second.start_calls == 0
    runner.shutdown()


def test_persisted_control_intent_resumes_running_but_not_paused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lazy_inventory(monkeypatch, tmp_path)
    delegate = _FakeDelegate()
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.load_dashboard_context",
        lambda *_args, **_kwargs: SimpleNamespace(current_ids=("physical-0",)),
    )
    monkeypatch.setattr(
        "la_heat.sentinel_dashboard.build_dashboard_runner",
        lambda _context, *, workers: delegate,
    )

    first_process = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    queued = first_process.start_or_resume()
    assert queued["start_queued"] is True
    first_process.shutdown()

    restarted_process = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    assert restarted_process.snapshot()["start_queued"] is True
    restarted_process.begin_initialization()
    _wait_until(lambda: delegate.start_calls == 1)
    restarted_process.request_pause()
    restarted_process.shutdown()

    paused_restart = LazyDashboardRunner(
        research_config_path="research.toml",
        stage_config_path="sentinel.toml",
        workers=1,
    )
    paused_snapshot = paused_restart.snapshot()
    assert paused_snapshot["start_queued"] is False
    assert paused_snapshot["auto_restart_enabled"] is False
    paused_restart.shutdown()


def test_dashboard_process_lock_is_exclusive_and_os_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "dashboard.lock"
    with DashboardProcessLock(lock_path):
        with pytest.raises(RuntimeError, match="already holds"):
            with DashboardProcessLock(lock_path):
                pass
    with DashboardProcessLock(lock_path):
        assert lock_path.exists()


def test_local_http_controls_require_same_origin_token(tmp_path: Path) -> None:
    class FakeRunner:
        def snapshot(self) -> dict[str, object]:
            return {"state": "idle", "completed": 18, "total": 226}

        def start_or_resume(self) -> dict[str, object]:
            return {"state": "running", "completed": 18, "total": 226}

        def request_pause(self) -> dict[str, object]:
            return {"state": "pausing", "completed": 18, "total": 226}

    page = tmp_path / "index.html"
    page.write_text('<script>const token="__CONTROL_TOKEN__";</script>', encoding="utf-8")
    server = create_server(
        host="127.0.0.1",
        port=0,
        runner=FakeRunner(),  # type: ignore[arg-type]
        page_path=page,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        html = requests.get(base, timeout=5).text
        token = re.search(r'const token="([^"]+)"', html).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", timeout=5).status_code == 403
        response = requests.post(
            f"{base}/api/start",
            headers={"X-ISEF-Control": token},
            json={},
            timeout=5,
        )
        assert response.status_code == 200
        assert response.json()["state"] == "running"
        pause = requests.post(
            f"{base}/api/pause",
            headers={"X-ISEF-Control": token},
            json={},
            timeout=5,
        )
        assert pause.status_code == 200
        assert pause.json()["state"] == "pausing"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_files_do_not_change_the_scientific_pipeline_sha() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pipeline_sha, _ = _pipeline_fingerprint(project_root)
    assert pipeline_sha == "de4f0e61a9717617a0f70b892f69b0f34022e68f7f04f26b7541cb1137c16797"
