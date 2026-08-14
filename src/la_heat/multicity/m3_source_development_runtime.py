"""Durable runtime plan for low-load M3 source data preparation.

The runtime is deliberately inert until an append-only source-acquisition
amendment, an exact expanded inventory, and an execution authorization exist.
It never creates those permissions itself.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.provenance import canonical_sha256

ALGORITHM_VERSION: Final = "m3-source-development-runtime-v1"
DEFAULT_CONFIG: Final = Path("configs/multicity/m3_source_development_runner.toml")
REQUIRED_ASSETS: Final = ("lwir11", "qa_pixel", "qa", "cdist", "qa_radsat")
QA_CANDIDATES: Final = ("none", "3k", "4k", "6k")
SOURCE_CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
BLIND_CITY_IDS: Final = (
    "seattle_wa",
    "denver_co",
    "atlanta_ga",
    "miami_fl",
)


class M3SourceRuntimeError(RuntimeError):
    """Raised when the resumable source-development plan drifts."""


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    root: Path
    config_path: Path
    protocol_lock: Path
    amendment: Path
    inventory: Path
    authorization: Path
    database: Path
    control: Path
    status: Path
    log: Path
    cache_root: Path
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
        raise M3SourceRuntimeError(f"{label} path is missing.")
    raw = Path(value)
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise M3SourceRuntimeError(f"{label} must stay inside the project.")
    return resolved


def load_runner_settings(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> RunnerSettings:
    root = Path(project_root).resolve()
    config = _inside(root, str(config_path), label="Runner config")
    with config.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != 1 or raw.get("algorithm_version") != ALGORITHM_VERSION:
        raise M3SourceRuntimeError("M3 source runner configuration changed.")
    inputs = raw.get("inputs")
    runtime = raw.get("runtime")
    office = raw.get("office_mode")
    limits = raw.get("limits")
    if not all(isinstance(value, Mapping) for value in (inputs, runtime, office, limits)):
        raise M3SourceRuntimeError("M3 source runner sections are incomplete.")
    assert isinstance(inputs, Mapping)
    assert isinstance(runtime, Mapping)
    assert isinstance(office, Mapping)
    assert isinstance(limits, Mapping)
    download_workers = int(office.get("download_workers", 0))
    compute_workers = int(office.get("compute_workers", 0))
    window_size = int(office.get("raster_window_size", 0))
    if (
        tuple(limits.get("allowed_download_workers", ())) != (1, 2)
        or download_workers not in (1, 2)
        or compute_workers != limits.get("compute_workers_fixed")
        or compute_workers != 1
        or window_size != limits.get("raster_window_size_fixed")
        or window_size != 512
        or limits.get("signed_urls_may_be_persisted") is not False
        or limits.get("credentials_may_be_persisted") is not False
        or limits.get("offline_phase_network_requests_allowed") is not False
        or limits.get("blind_test_city_assets_allowed") is not False
    ):
        raise M3SourceRuntimeError("Office-mode safety limits changed.")
    return RunnerSettings(
        root=root,
        config_path=config,
        protocol_lock=_inside(root, inputs.get("protocol_lock"), label="Protocol lock"),
        amendment=_inside(root, inputs.get("source_acquisition_amendment"), label="Amendment"),
        inventory=_inside(root, inputs.get("expanded_source_inventory"), label="Inventory"),
        authorization=_inside(root, inputs.get("execution_authorization"), label="Authorization"),
        database=_inside(root, runtime.get("database"), label="Runtime database"),
        control=_inside(root, runtime.get("control"), label="Runtime control"),
        status=_inside(root, runtime.get("status"), label="Runtime status"),
        log=_inside(root, runtime.get("log"), label="Runtime log"),
        cache_root=_inside(root, runtime.get("cache_root"), label="Cache root"),
        qa_output_root=_inside(root, runtime.get("qa_output_root"), label="QA output root"),
        completion_root=_inside(root, runtime.get("completion_root"), label="Completion root"),
        download_workers=download_workers,
        compute_workers=compute_workers,
        window_size=window_size,
        network_timeout_seconds=int(office.get("network_timeout_seconds", 0)),
        network_recheck_seconds=int(office.get("network_recheck_seconds", 0)),
        lease_seconds=int(office.get("lease_seconds", 0)),
        heartbeat_seconds=int(office.get("heartbeat_seconds", 0)),
        retry_base_seconds=int(office.get("retry_base_seconds", 0)),
        retry_max_seconds=int(office.get("retry_max_seconds", 0)),
    )


def _committed_json(path: Path, *, state: str, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise M3SourceRuntimeError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceRuntimeError(f"{label} is unreadable.") from error
    if not isinstance(payload, dict) or payload.get("state") != state:
        raise M3SourceRuntimeError(f"{label} state changed.")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise M3SourceRuntimeError(f"{label} commit is invalid.")
    return payload


def runtime_readiness(project_root: str | Path) -> dict[str, Any]:
    """Report the first unmet gate without opening an inventory or target table."""

    settings = load_runner_settings(project_root)
    protocol = authenticate_m3_development_protocol_lock(settings.root)
    base = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "protocol_commit_sha256": protocol["commit_sha256"],
        "download_workers": settings.download_workers,
        "compute_workers": settings.compute_workers,
        "raster_window_size": settings.window_size,
        "blind_test_targets_sealed": True,
    }
    if not settings.amendment.is_file():
        return {**base, "state": "waiting_for_source_acquisition_amendment"}
    amendment = authenticate_m3_source_acquisition_amendment(
        settings.root,
        settings.amendment,
    )
    if (
        amendment.get("input_anchors", {})
        .get("m3_protocol_lock", {})
        .get("commit_sha256")
        != protocol["commit_sha256"]
    ):
        raise M3SourceRuntimeError("Source-acquisition amendment is detached from M3 lock.")
    base["amendment_commit_sha256"] = amendment["commit_sha256"]
    if not settings.inventory.is_file():
        return {**base, "state": "waiting_for_expanded_source_inventory"}
    inventory = authenticate_expanded_inventory(settings, amendment)
    base["inventory_commit_sha256"] = inventory["commit_sha256"]
    if not settings.authorization.is_file():
        return {**base, "state": "waiting_for_source_qa_execution_authorization"}
    # Local import avoids a module cycle: the authorizer itself binds this
    # runtime implementation as part of its immutable code identity.
    from la_heat.multicity.m3_source_qa_authorization import (
        authenticate_m3_source_qa_authorization,
    )

    authorization = authenticate_m3_source_qa_authorization(
        settings.root,
        settings.authorization,
    )
    for key, expected in (
        ("m3_protocol_lock_commit_sha256", protocol["commit_sha256"]),
        ("source_acquisition_amendment_commit_sha256", amendment["commit_sha256"]),
        ("expanded_source_inventory_commit_sha256", inventory["commit_sha256"]),
    ):
        if authorization.get(key) != expected:
            raise M3SourceRuntimeError(f"Authorization binding changed: {key}")
    if authorization.get("blind_test_target_access_authorized") is not False:
        raise M3SourceRuntimeError("Authorization unexpectedly opens blind-test targets.")
    if (
        authorization.get("model_fit_or_selection_authorized") is not False
        or authorization.get("predictor_build_or_read_authorized") is not False
    ):
        raise M3SourceRuntimeError("Authorization exceeded source cache and QA rebuild.")
    return {
        **base,
        "state": "ready_paused",
        "authorization_commit_sha256": authorization["commit_sha256"],
    }


def _overpasses(inventory: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = inventory.get("overpasses")
    if not isinstance(raw, list) or not raw:
        raise M3SourceRuntimeError("Expanded inventory has no overpasses.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping):
            raise M3SourceRuntimeError("Expanded inventory overpass is invalid.")
        row = dict(value)
        city_id = row.get("city_id")
        overpass_id = row.get("overpass_id")
        scene_ids = row.get("scene_ids")
        if (
            city_id not in SOURCE_CITY_IDS
            or city_id in BLIND_CITY_IDS
            or not isinstance(overpass_id, str)
            or not overpass_id
            or not isinstance(scene_ids, list)
            or not scene_ids
            or any(not isinstance(item, str) or not item for item in scene_ids)
            or len(scene_ids) != len(set(scene_ids))
        ):
            raise M3SourceRuntimeError("Expanded inventory crossed its source-city contract.")
        unit_key = f"{city_id}|{overpass_id}"
        if unit_key in seen:
            raise M3SourceRuntimeError("Expanded inventory duplicated an overpass.")
        seen.add(unit_key)
        rows.append(row)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row["city_id"]),
                str(row["target_date"]),
                str(row["overpass_id"]),
            ),
        )
    )


def authenticate_expanded_inventory(
    settings: RunnerSettings,
    amendment: Mapping[str, Any],
) -> dict[str, Any]:
    inventory = _committed_json(
        settings.inventory,
        state="expanded_source_inventory_complete",
        label="Expanded source inventory",
    )
    if inventory.get("source_acquisition_amendment_commit_sha256") != amendment.get(
        "commit_sha256"
    ):
        raise M3SourceRuntimeError("Expanded inventory is detached from its amendment.")
    if tuple(inventory.get("source_city_ids", ())) != SOURCE_CITY_IDS:
        raise M3SourceRuntimeError("Expanded inventory source-city order changed.")
    if inventory.get("blind_test_asset_or_value_accessed") is not False:
        raise M3SourceRuntimeError("Expanded inventory reports blind-test access.")
    overpasses = _overpasses(inventory)
    declared = inventory.get("overpass_count")
    if declared != len(overpasses):
        raise M3SourceRuntimeError("Expanded inventory overpass count changed.")
    return inventory


def task_specs_from_inventory(inventory: Mapping[str, Any]) -> tuple[TaskSpec, ...]:
    """Build one immutable queue plan from an authenticated inventory."""

    overpasses = _overpasses(inventory)
    scene_city: dict[tuple[str, str], dict[str, Any]] = {}
    for row in overpasses:
        for scene_id in row["scene_ids"]:
            key = (str(row["city_id"]), str(scene_id))
            scene_city.setdefault(
                key,
                {
                    "city_id": key[0],
                    "scene_id": key[1],
                    "grid_sha256": row.get("grid_sha256"),
                    "target_context_commit_sha256": row.get("target_context_commit_sha256"),
                },
            )
    specs: list[TaskSpec] = []
    for (city_id, scene_id), scene in sorted(scene_city.items()):
        token = canonical_sha256([city_id, scene_id])[:20]
        for asset in REQUIRED_ASSETS:
            specs.append(
                TaskSpec(
                    task_id=f"asset-{token}-{asset}",
                    kind="download_asset",
                    payload={**scene, "asset": asset},
                )
            )
        specs.append(
            TaskSpec(
                task_id=f"scene-{token}",
                kind="finalize_scene",
                payload=scene,
            )
        )
    specs.append(
        TaskSpec(
            task_id="download-cache-complete",
            kind="finalize_download",
            payload={"expected_scene_count": len(scene_city)},
        )
    )
    for row in overpasses:
        token = canonical_sha256([row["city_id"], row["overpass_id"]])[:20]
        specs.append(
            TaskSpec(
                task_id=f"qa-{token}",
                kind="qa_overpass",
                payload={**row, "qa_candidate_ids": list(QA_CANDIDATES)},
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
                "expected_overpass_count": len(overpasses),
            },
        )
    )
    return tuple(specs)


def source_run_id(inventory: Mapping[str, Any]) -> str:
    commit = inventory.get("commit_sha256")
    if not isinstance(commit, str) or len(commit) != 64:
        raise M3SourceRuntimeError("Expanded inventory commit is invalid.")
    return f"m3-source-development-v1-{commit[:16]}"


def initialize_source_runtime(project_root: str | Path) -> dict[str, Any]:
    settings = load_runner_settings(project_root)
    readiness = runtime_readiness(settings.root)
    if readiness["state"] != "ready_paused":
        return readiness
    amendment = authenticate_m3_source_acquisition_amendment(
        settings.root,
        settings.amendment,
    )
    inventory = authenticate_expanded_inventory(settings, amendment)
    specs = task_specs_from_inventory(inventory)
    queue = ModelRunQueue(settings.database)
    run_id = source_run_id(inventory)
    queue.initialize_run(run_id, specs, desired_state="paused")
    return runtime_status(queue, run_id, settings=settings)


def runtime_status(
    queue: ModelRunQueue,
    run_id: str,
    *,
    settings: RunnerSettings,
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
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": state,
        "run_id": run_id,
        "desired_state": desired,
        "counts": counts,
        "counts_by_kind": by_kind,
        "active_task_ids": [task.task_id for task in running],
        "download_workers": settings.download_workers,
        "compute_workers": 1,
        "raster_window_size": 512,
        "blind_test_targets_sealed": True,
    }
