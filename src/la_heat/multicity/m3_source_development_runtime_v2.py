"""Independent durable task plan for the M3 integrity-overlay continuation."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.m3_source_development_runtime import (
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
)
from la_heat.multicity.m3_source_integrity_v2 import (
    EXPECTED_OVERPASS_COUNT,
    EXPECTED_SCENE_COUNT,
    authenticate_m3_source_integrity_v2_authorization,
    write_logical_overlay_plan,
)
from la_heat.provenance import canonical_sha256

ALGORITHM_VERSION: Final = "m3-source-development-runtime-v2"
DEFAULT_CONFIG: Final = Path(
    "configs/multicity/m3_source_development_runner_v2.toml"
)
EXPECTED_TASK_COUNT: Final = 846


class M3SourceRuntimeV2Error(RuntimeError):
    """Raised when the independent v2 runtime leaves its frozen contract."""


@dataclass(frozen=True, slots=True)
class RunnerSettingsV2:
    root: Path
    config_path: Path
    protocol_lock: Path
    amendment: Path
    inventory: Path
    authorization: Path
    original_database: Path
    database: Path
    control: Path
    status: Path
    log: Path
    cache_root: Path
    logical_cache_root: Path
    qa_output_root: Path
    completion_root: Path
    download_workers: int
    compute_workers: int
    window_size: int
    network_timeout_seconds: int
    network_recheck_seconds: int
    lease_seconds: int
    heartbeat_seconds: int
    retry_base_seconds: int
    retry_max_seconds: int


def _inside(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise M3SourceRuntimeV2Error(f"{label} path is missing.")
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceRuntimeV2Error(f"{label} must stay inside the project.")
    return path


def _overlaps(left: Path, right: Path) -> bool:
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


def load_runner_settings_v2(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> RunnerSettingsV2:
    root = Path(project_root).resolve()
    config = _inside(root, str(config_path), label="Runner v2 config")
    with config.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != 2 or raw.get("algorithm_version") != ALGORITHM_VERSION:
        raise M3SourceRuntimeV2Error("M3 source v2 runner configuration changed.")
    inputs = raw.get("inputs")
    runtime = raw.get("runtime")
    office = raw.get("office_mode")
    limits = raw.get("limits")
    if not all(isinstance(value, Mapping) for value in (inputs, runtime, office, limits)):
        raise M3SourceRuntimeV2Error("M3 source v2 runner sections are incomplete.")
    assert isinstance(inputs, Mapping)
    assert isinstance(runtime, Mapping)
    assert isinstance(office, Mapping)
    assert isinstance(limits, Mapping)
    compute_workers = int(office.get("compute_workers", 0))
    window_size = int(office.get("raster_window_size", 0))
    if (
        int(office.get("download_workers", -1)) != 0
        or compute_workers != 1
        or window_size != 512
        or limits.get("network_requests_allowed") is not False
        or limits.get("href_reads_allowed") is not False
        or limits.get("physical_cache_mutation_allowed") is not False
        or limits.get("original_queue_mutation_allowed") is not False
        or limits.get("blind_test_city_access_allowed") is not False
        or limits.get("predictor_read_or_build_allowed") is not False
        or limits.get("fit_select_predict_or_score_allowed") is not False
    ):
        raise M3SourceRuntimeV2Error("M3 source v2 safety limits changed.")
    original_database = _inside(
        root, runtime.get("original_database"), label="Original database"
    )
    database = _inside(root, runtime.get("database"), label="V2 database")
    physical = _inside(root, runtime.get("physical_cache_root"), label="Physical cache")
    logical = _inside(root, runtime.get("logical_cache_root"), label="Logical cache")
    authorization = _inside(
        root,
        inputs.get("integrity_execution_authorization"),
        label="Integrity v2 authorization",
    )
    control = _inside(root, runtime.get("control"), label="V2 control")
    status = _inside(root, runtime.get("status"), label="V2 status")
    log = _inside(root, runtime.get("log"), label="V2 log")
    qa_output = _inside(root, runtime.get("qa_output_root"), label="V2 QA root")
    completion = _inside(
        root, runtime.get("completion_root"), label="V2 completion root"
    )
    old_runtime = original_database.parent
    write_targets = {
        "V2 database": database,
        "V2 control": control,
        "V2 worker lock": control.with_suffix(".worker.lock"),
        "V2 status": status,
        "V2 log": log,
        "V2 logical cache": logical,
        "V2 QA output": qa_output,
        "V2 completion": completion,
    }
    for label, target in write_targets.items():
        if _overlaps(target, physical) or _overlaps(target, old_runtime):
            raise M3SourceRuntimeV2Error(
                f"{label} must be isolated from the old cache and runtime."
            )
    disjoint = tuple(write_targets.values())
    if any(
        _overlaps(left, right)
        for index, left in enumerate(disjoint)
        for right in disjoint[index + 1 :]
    ):
        raise M3SourceRuntimeV2Error("V2 write targets overlap each other.")
    if _overlaps(authorization, physical) or _overlaps(authorization, old_runtime):
        raise M3SourceRuntimeV2Error(
            "V2 authorization must be isolated from the old cache and runtime."
        )
    return RunnerSettingsV2(
        root=root,
        config_path=config,
        protocol_lock=_inside(root, inputs.get("protocol_lock"), label="Protocol lock"),
        amendment=_inside(
            root, inputs.get("source_acquisition_amendment"), label="Amendment"
        ),
        inventory=_inside(
            root, inputs.get("expanded_source_inventory"), label="Inventory"
        ),
        authorization=authorization,
        original_database=original_database,
        database=database,
        control=control,
        status=status,
        log=log,
        cache_root=physical,
        logical_cache_root=logical,
        qa_output_root=qa_output,
        completion_root=completion,
        download_workers=0,
        compute_workers=compute_workers,
        window_size=window_size,
        network_timeout_seconds=0,
        network_recheck_seconds=0,
        lease_seconds=int(office.get("lease_seconds", 0)),
        heartbeat_seconds=int(office.get("heartbeat_seconds", 0)),
        retry_base_seconds=int(office.get("retry_base_seconds", 0)),
        retry_max_seconds=int(office.get("retry_max_seconds", 0)),
    )


def runtime_readiness_v2(project_root: str | Path) -> dict[str, Any]:
    settings = load_runner_settings_v2(project_root)
    base = {
        "schema_version": 2,
        "algorithm_version": ALGORITHM_VERSION,
        "compute_workers": 1,
        "download_workers": 0,
        "network_allowed": False,
        "physical_cache_read_only": True,
        "original_queue_read_only": True,
        "blind_test_targets_sealed": True,
    }
    if not settings.authorization.is_file():
        return {**base, "state": "waiting_for_source_integrity_v2_authorization"}
    authorization = authenticate_m3_source_integrity_v2_authorization(
        settings.root, settings.authorization
    )
    if (
        (settings.root / authorization["physical_cache_root"]).resolve()
        != settings.cache_root
        or (settings.root / authorization["original_queue_database"]).resolve()
        != settings.original_database
        or (settings.root / authorization["logical_cache_root"]).resolve()
        != settings.logical_cache_root
        or (settings.root / authorization["qa_output_root"]).resolve()
        != settings.qa_output_root
        or (settings.root / authorization["runtime_database"]).resolve()
        != settings.database
        or (settings.root / authorization["runtime_control"]).resolve()
        != settings.control
        or (settings.root / authorization["runtime_worker_lock"]).resolve()
        != settings.control.with_suffix(".worker.lock")
        or (settings.root / authorization["runtime_status"]).resolve()
        != settings.status
        or (settings.root / authorization["runtime_log"]).resolve() != settings.log
        or (
            settings.root / authorization["source_landsat_cache_completion"]
        ).resolve().parent
        != settings.completion_root
        or (
            settings.root / authorization["source_qa_candidates_completion"]
        ).resolve().parent
        != settings.completion_root
        or authorization.get("expected_overpass_count") != EXPECTED_OVERPASS_COUNT
        or authorization.get("expected_retained_scene_count") != EXPECTED_SCENE_COUNT
    ):
        raise M3SourceRuntimeV2Error("V2 authorization paths or counts changed.")
    return {
        **base,
        "state": "ready_paused",
        "authorization_commit_sha256": authorization["commit_sha256"],
        "logical_overlay_commit_sha256": authorization["logical_overlay"][
            "commit_sha256"
        ],
    }


def task_specs_from_integrity_authorization(
    authorization: Mapping[str, Any],
) -> tuple[TaskSpec, ...]:
    overlay = authorization.get("logical_overlay")
    if not isinstance(overlay, Mapping):
        raise M3SourceRuntimeV2Error("Authorization lacks its logical overlay.")
    scenes = overlay.get("retained_scenes")
    overpasses = overlay.get("overpasses")
    if (
        not isinstance(scenes, list)
        or len(scenes) != EXPECTED_SCENE_COUNT
        or not isinstance(overpasses, list)
        or len(overpasses) != EXPECTED_OVERPASS_COUNT
    ):
        raise M3SourceRuntimeV2Error("Logical overlay task counts changed.")
    specs: list[TaskSpec] = []
    for reference in scenes:
        if not isinstance(reference, Mapping):
            raise M3SourceRuntimeV2Error("Logical scene reference is invalid.")
        city_id = str(reference["city_id"])
        scene_id = str(reference["scene_id"])
        token = canonical_sha256([city_id, scene_id])[:20]
        specs.append(
            TaskSpec(
                task_id=f"logical-scene-{token}",
                kind="finalize_retained_scene",
                payload={"city_id": city_id, "scene_id": scene_id},
            )
        )
    specs.append(
        TaskSpec(
            task_id="logical-cache-complete",
            kind="finalize_logical_cache",
            payload={
                "expected_scene_count": EXPECTED_SCENE_COUNT,
                "expected_content_count": EXPECTED_SCENE_COUNT * 5,
            },
        )
    )
    for row in overpasses:
        if not isinstance(row, Mapping):
            raise M3SourceRuntimeV2Error("Logical overpass is invalid.")
        token = canonical_sha256([row["city_id"], row["overpass_id"]])[:20]
        specs.append(
            TaskSpec(
                task_id=f"qa-{token}",
                kind="qa_overpass",
                payload={**dict(row), "qa_candidate_ids": list(QA_CANDIDATES)},
            )
        )
    for city_id in SOURCE_CITY_IDS:
        specs.append(
            TaskSpec(
                task_id=f"qa-city-{city_id}",
                kind="compile_qa_city",
                payload={"city_id": city_id, "qa_candidate_ids": list(QA_CANDIDATES)},
            )
        )
    specs.append(
        TaskSpec(
            task_id="qa-candidates-complete",
            kind="finalize_qa_candidates",
            payload={
                "source_city_ids": list(SOURCE_CITY_IDS),
                "qa_candidate_ids": list(QA_CANDIDATES),
                "expected_overpass_count": EXPECTED_OVERPASS_COUNT,
            },
        )
    )
    if len(specs) != EXPECTED_TASK_COUNT:
        raise M3SourceRuntimeV2Error("V2 task count changed.")
    return tuple(specs)


def source_run_id_v2(authorization: Mapping[str, Any]) -> str:
    commit = str(authorization.get("commit_sha256", ""))
    if len(commit) != 64:
        raise M3SourceRuntimeV2Error("V2 authorization commit is invalid.")
    return f"m3-source-integrity-v2-{commit[:16]}"


def initialize_source_runtime_v2(project_root: str | Path) -> dict[str, Any]:
    settings = load_runner_settings_v2(project_root)
    readiness = runtime_readiness_v2(settings.root)
    if readiness["state"] != "ready_paused":
        return readiness
    authorization = authenticate_m3_source_integrity_v2_authorization(
        settings.root, settings.authorization
    )
    write_logical_overlay_plan(settings.root, authorization)
    specs = task_specs_from_integrity_authorization(authorization)
    queue = ModelRunQueue(settings.database)
    run_id = source_run_id_v2(authorization)
    queue.initialize_run(run_id, specs, desired_state="paused")
    return runtime_status_v2(queue, run_id, settings=settings)


def runtime_status_v2(
    queue: ModelRunQueue,
    run_id: str,
    *,
    settings: RunnerSettingsV2,
) -> dict[str, Any]:
    counts = queue.counts(run_id)
    by_kind = queue.counts_by_kind(run_id)
    desired = queue.get_desired_state(run_id)
    running = queue.list_tasks(run_id, statuses=("running",))
    if counts["quarantined"]:
        state = "failed"
    elif by_kind.get("finalize_qa_candidates", {}).get("complete") == 1:
        state = "qa_candidates_complete_waiting_for_loso_authorization"
    elif desired == "paused" and running:
        state = "pausing"
    elif desired == "paused":
        state = "paused"
    else:
        state = "running"
    return {
        "schema_version": 2,
        "algorithm_version": ALGORITHM_VERSION,
        "state": state,
        "run_id": run_id,
        "desired_state": desired,
        "counts": counts,
        "counts_by_kind": by_kind,
        "active_task_ids": [task.task_id for task in running],
        "download_workers": 0,
        "compute_workers": 1,
        "raster_window_size": 512,
        "network_allowed": False,
        "href_reads_allowed": False,
        "physical_cache_read_only": True,
        "original_queue_read_only": True,
        "blind_test_targets_sealed": True,
    }
