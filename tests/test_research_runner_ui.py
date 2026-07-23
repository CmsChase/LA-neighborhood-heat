from __future__ import annotations

import json
import os
from pathlib import Path

from la_heat.research_runner_ui import (
    RunnerController,
    RunnerPaths,
    estimate_remaining_seconds,
    read_control,
    write_control,
)


def _paths(tmp_path: Path) -> RunnerPaths:
    paths = RunnerPaths.from_root(tmp_path)
    paths.strict_output.mkdir(parents=True)
    return paths


def test_control_intent_is_atomic_and_persistent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert read_control(paths)["desired_state"] == "running"
    write_control(paths, "paused")
    assert read_control(paths)["desired_state"] == "paused"
    assert not paths.control.with_suffix(paths.control.suffix + ".partial").exists()


def test_eta_uses_recent_completion_intervals(tmp_path: Path) -> None:
    summaries = []
    for index in range(4):
        path = tmp_path / f"{index}.json"
        path.write_text("{}", encoding="utf-8")
        timestamp = 1_700_000_000 + index * 60
        path.touch()
        path.chmod(0o644)
        os.utime(path, (timestamp, timestamp))
        summaries.append(path)
    assert estimate_remaining_seconds(summaries, 10) == 360.0


def test_pause_and_resume_never_launch_duplicates(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    processes = [{"ProcessId": 10}, {"ProcessId": 11}]
    stopped: list[list[dict[str, int]]] = []
    launched: list[Path] = []

    def discover() -> list[dict[str, int]]:
        return list(processes)

    def stop(rows):
        stopped.append(list(rows))
        processes.clear()

    def launch(current: RunnerPaths) -> int:
        launched.append(current.root)
        processes.append({"ProcessId": 99})
        return 99

    controller = RunnerController(paths, discover=discover, stop=stop, launch=launch)
    controller.ensure_running()
    assert not launched
    controller.pause()
    assert stopped and not processes
    assert read_control(paths)["desired_state"] == "paused"
    controller.ensure_running()
    assert not launched
    controller.resume()
    assert launched == [tmp_path.resolve()]
    status = controller.status()
    assert status["strict"]["running"] is True
    json.dumps(status)
