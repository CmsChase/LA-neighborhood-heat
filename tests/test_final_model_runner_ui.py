from __future__ import annotations

import json
import os
from pathlib import Path

from la_heat.final_model_runner_ui import (
    MAX_STALLED_RESTARTS,
    TOTAL_TUNING_TASKS,
    FinalModelController,
    ProcessDiscovery,
    current_run,
    is_exact_target_process,
    model_lock_stage_status,
    read_control,
    write_control,
)


def _prepared(tmp_path: Path, fragment_count: int = 0) -> Path:
    run = tmp_path / "data/interim/final_model_staging/runs/run1"
    fragments = run / "tuning_fragments"
    fragments.mkdir(parents=True)
    (run / "final_model_run_manifest.json").write_text(
        json.dumps({"state": "prepared_development_only", "run_id": "run1"}),
        encoding="utf-8",
    )
    for index in range(fragment_count):
        path = fragments / f"{index}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_700_000_000 + index * 60,) * 2)
    return run


def test_default_is_paused_and_prepared_plan_is_visible(tmp_path: Path) -> None:
    _prepared(tmp_path, fragment_count=3)
    launched = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True),
        launch=lambda root: launched.append(root) or 9,
    )

    status = controller.status()
    controller.ensure_running()

    assert read_control(tmp_path)["desired_state"] == "paused"
    assert status["session_armed"] is False
    assert status["prepared"] is True
    assert status["tuning_completed"] == 3
    assert status["tuning_total"] == TOTAL_TUNING_TASKS
    assert current_run(tmp_path)[1]["run_id"] == "run1"
    assert launched == []


def test_stale_running_control_cannot_arm_a_new_session(tmp_path: Path) -> None:
    _prepared(tmp_path)
    write_control(tmp_path, "running")
    launched = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True),
        launch=lambda root: launched.append(root) or 9,
    )

    controller.ensure_running()

    assert launched == []
    assert controller.status()["session_armed"] is False


def test_begin_session_forces_pause_and_stops_exact_discovery(tmp_path: Path) -> None:
    _prepared(tmp_path)
    write_control(tmp_path, "running")
    processes = ({"ProcessId": 7},)
    stopped = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True, processes=processes),
        stop=lambda root, rows: stopped.extend((root, tuple(rows))),
    )

    controller.begin_session()

    assert read_control(tmp_path)["desired_state"] == "paused"
    assert stopped[-1] == processes
    assert controller.status()["session_armed"] is False


def test_resume_does_not_duplicate_an_existing_process(tmp_path: Path) -> None:
    _prepared(tmp_path)
    processes = [{"ProcessId": 7}]
    launched = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True, processes=tuple(processes)),
        launch=lambda root: launched.append(root) or 9,
    )

    controller.resume()

    assert read_control(tmp_path)["desired_state"] == "running"
    assert launched == []


def test_pause_preserves_fragments_and_stops_only_discovered_family(tmp_path: Path) -> None:
    run = _prepared(tmp_path, fragment_count=2)
    processes = [{"ProcessId": 7}, {"ProcessId": 8}]
    stopped = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True, processes=tuple(processes)),
        stop=lambda _root, rows: stopped.extend(rows),
    )

    controller.pause()

    assert len(stopped) == 2
    assert len(list((run / "tuning_fragments").glob("*.json"))) == 2
    assert read_control(tmp_path)["desired_state"] == "paused"


def test_discovery_failure_is_fail_closed(tmp_path: Path) -> None:
    _prepared(tmp_path)
    launched = []
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=False, error="timeout"),
        launch=lambda root: launched.append(root) or 9,
    )

    controller.resume()

    assert launched == []
    assert "未启动" in str(controller.last_error)


def test_completed_run_reports_unambiguous_eta(tmp_path: Path) -> None:
    run = _prepared(tmp_path, fragment_count=TOTAL_TUNING_TASKS)
    (run / "final_model_build_provenance.json").write_text("{}", encoding="utf-8")
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True),
    )

    status = controller.status()

    assert status["complete"] is True
    assert status["eta"] == "已完成"


def test_status_exposes_completed_model_lock_audit(tmp_path: Path) -> None:
    destination = tmp_path / "manifests/model_lock/MODEL_LOCK_STAGING.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(
        json.dumps(
            {
                "state": "blocked",
                "ready_for_formal_model_lock": False,
                "formal_model_lock_written": False,
                "blockers": ["git_head_missing"],
                "commit_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    status = model_lock_stage_status(tmp_path)

    assert status["exists"] is True
    assert status["state"] == "blocked"
    assert status["blockers"] == ["git_head_missing"]


def test_exact_process_match_requires_project_python_script_and_config(tmp_path: Path) -> None:
    python = tmp_path / ".venv/Scripts/python.exe"
    script = tmp_path / "scripts/build_final_models.py"
    config = tmp_path / "configs/final_model.toml"
    matching = {
        "ProcessId": 17,
        "CreationDate": "20260722120000.000000+480",
        "ExecutablePath": str(python),
        "CommandLine": f'"{python}" "{script}" --config "{config}"',
    }

    assert is_exact_target_process(matching, tmp_path)
    assert not is_exact_target_process(
        {
            **matching,
            "CommandLine": matching["CommandLine"].replace(
                str(script), str(tmp_path / "other/build_final_models.py")
            ),
        },
        tmp_path,
    )
    assert not is_exact_target_process(
        {**matching, "ExecutablePath": str(tmp_path / "other/python.exe")},
        tmp_path,
    )
    assert not is_exact_target_process(
        {**matching, "CommandLine": matching["CommandLine"] + " --prepare-only"},
        tmp_path,
    )


def test_launch_failures_back_off_and_eventually_pause(tmp_path: Path, monkeypatch) -> None:
    _prepared(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("la_heat.final_model_runner_ui.time.monotonic", lambda: clock[0])
    controller = FinalModelController(
        tmp_path,
        discover=lambda _root: ProcessDiscovery(ok=True),
        launch=lambda _root: (_ for _ in ()).throw(OSError("no launch")),
    )

    controller.resume()
    for _ in range(MAX_STALLED_RESTARTS - 1):
        clock[0] += 1_000.0
        controller.ensure_running()

    assert read_control(tmp_path)["desired_state"] == "paused"
    assert controller.status()["session_armed"] is False
    assert "无进度" in str(controller.last_error)
