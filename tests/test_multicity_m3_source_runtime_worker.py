from __future__ import annotations

from pathlib import Path

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.m3_source_development_runtime import (
    SOURCE_CITY_IDS,
    RunnerSettings,
    task_specs_from_inventory,
)
from la_heat.multicity.m3_source_development_worker import (
    OFFLINE_PHASE,
    ONLINE_PHASE,
    WorkerOptions,
    active_kind,
    execute_phase_queue,
)


def _inventory() -> dict[str, object]:
    overpasses: list[dict[str, object]] = []
    for index, city_id in enumerate(SOURCE_CITY_IDS):
        overpasses.append(
            {
                "city_id": city_id,
                "overpass_id": f"overpass-{index}",
                "target_date": f"2025-0{index + 5}-01",
                "scene_ids": [f"scene-{index}"],
                "platform": "landsat-9",
                "grid_sha256": f"grid-{index}",
                "target_context_commit_sha256": f"context-{index}",
            }
        )
    return {
        "state": "expanded_source_inventory_complete",
        "source_city_ids": list(SOURCE_CITY_IDS),
        "overpass_count": len(overpasses),
        "overpasses": overpasses,
    }


def _settings(tmp_path: Path) -> RunnerSettings:
    return RunnerSettings(
        root=tmp_path,
        config_path=tmp_path / "runner.toml",
        protocol_lock=tmp_path / "protocol.json",
        amendment=tmp_path / "amendment.json",
        inventory=tmp_path / "inventory.json",
        authorization=tmp_path / "authorization.json",
        database=tmp_path / "runtime" / "tasks.sqlite",
        control=tmp_path / "runtime" / "control.json",
        status=tmp_path / "runtime" / "status.json",
        log=tmp_path / "runtime" / "worker.log",
        cache_root=tmp_path / "cache",
        qa_output_root=tmp_path / "qa",
        completion_root=tmp_path / "completion",
        download_workers=2,
        compute_workers=1,
        window_size=512,
        network_timeout_seconds=20,
        network_recheck_seconds=20,
        lease_seconds=30,
        heartbeat_seconds=1,
        retry_base_seconds=0.01,
        retry_max_seconds=0.02,
    )


def test_task_plan_separates_download_and_offline_phases() -> None:
    specs = task_specs_from_inventory(_inventory())
    by_kind: dict[str, int] = {}
    for spec in specs:
        by_kind[spec.kind] = by_kind.get(spec.kind, 0) + 1
    assert by_kind == {
        "download_asset": 20,
        "finalize_scene": 4,
        "finalize_download": 1,
        "qa_overpass": 4,
        "compile_qa_city": 4,
        "finalize_qa_candidates": 1,
    }
    assert all("seattle" not in str(spec.payload) for spec in specs)


class _Executor:
    def execute(self, kind: str, payload: object) -> dict[str, object]:
        return {"kind": kind, "completed": True, "payload_seen": payload is not None}


def test_online_worker_stops_before_offline_tasks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    queue = ModelRunQueue(settings.database)
    run_id = "m3-source-test"
    queue.initialize_run(run_id, task_specs_from_inventory(_inventory()), desired_state="running")

    result = execute_phase_queue(
        settings=settings,
        run_id=run_id,
        options=WorkerOptions(
            phase=ONLINE_PHASE,
            download_workers=2,
            compute_workers=1,
            window_size=512,
            poll_seconds=0.01,
        ),
        executor_factory=_Executor,
    )

    assert result["phase"] == "complete"
    assert queue.counts_by_kind(run_id)["finalize_download"]["complete"] == 1
    assert queue.counts_by_kind(run_id)["qa_overpass"]["complete"] == 0
    assert queue.get_desired_state(run_id) == "paused"


def test_offline_worker_requires_complete_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    queue = ModelRunQueue(settings.database)
    run_id = "m3-source-test"
    queue.initialize_run(run_id, task_specs_from_inventory(_inventory()), desired_state="paused")

    try:
        active_kind(queue, run_id, OFFLINE_PHASE)
    except Exception as error:
        assert "cache is complete" in str(error)
    else:
        raise AssertionError("Offline work opened before the cache was complete")
