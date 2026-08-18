"""Independent resumable task plan for the M3 source predictor extension."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    EXTENSION_CITY_IDS,
    EXTENSION_YEARS,
    SOURCE_CITY_IDS,
    PredictorExtensionSettings,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.provenance import canonical_sha256
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

ALGORITHM_VERSION: Final = "m3-source-predictor-extension-runtime-v1"
ONLINE_PHASE: Final = "online_acquisition"
OFFLINE_PHASE: Final = "offline_assembly"
PHASES: Final = (ONLINE_PHASE, OFFLINE_PHASE)

ONLINE_KINDS: Final = (
    "freeze_key_universe",
    "authenticate_static_reuse",
    "acquire_daymet_metadata",
    "acquire_daymet_subset",
    "build_sentinel_inventory",
    "acquire_sentinel_cache",
    "finalize_acquisition",
)
OFFLINE_KINDS: Final = (
    "build_extension_city",
    "compile_source_city",
    "finalize_predictors",
)
EXPECTED_TASK_COUNT: Final = 85


class M3SourcePredictorRuntimeError(RuntimeError):
    """Raised when predictor runtime state leaves its immutable plan."""


def source_predictor_run_id(permit: Mapping[str, Any]) -> str:
    commit = str(permit.get("commit_sha256", ""))
    if len(commit) != 64:
        raise M3SourcePredictorRuntimeError("Predictor authorization commit is invalid.")
    return f"m3-source-predictor-extension-v1-{commit[:16]}"


def _extension_dates(permit: Mapping[str, Any], city_id: str) -> tuple[str, ...]:
    rows = permit.get("key_universe", {}).get("extension_cities")
    if not isinstance(rows, list):
        raise M3SourcePredictorRuntimeError("Extension key universe changed.")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("city_id") == city_id]
    if len(matches) != 1:
        raise M3SourcePredictorRuntimeError("Extension city key universe changed.")
    dates = tuple(str(value) for value in matches[0].get("target_dates", ()))
    if not dates:
        raise M3SourcePredictorRuntimeError("Extension target dates are empty.")
    return dates


def task_specs_from_predictor_authorization(
    permit: Mapping[str, Any],
) -> tuple[TaskSpec, ...]:
    """Return the exact ordered plan; payloads contain identities, never URLs."""

    key_sha = str(permit.get("key_universe", {}).get("key_universe_sha256", ""))
    if len(key_sha) != 64:
        raise M3SourcePredictorRuntimeError("Key universe commit changed.")
    specs: list[TaskSpec] = [
        TaskSpec(
            task_id="freeze-key-universe",
            kind="freeze_key_universe",
            payload={"key_universe_sha256": key_sha},
        )
    ]
    for city_id in EXTENSION_CITY_IDS:
        specs.append(
            TaskSpec(
                task_id=f"static-{city_id}",
                kind="authenticate_static_reuse",
                payload={"city_id": city_id},
            )
        )
    for city_id in EXTENSION_CITY_IDS:
        for year in EXTENSION_YEARS:
            specs.append(
                TaskSpec(
                    task_id=f"daymet-metadata-{city_id}-{year}",
                    kind="acquire_daymet_metadata",
                    payload={"city_id": city_id, "year": year},
                )
            )
    for city_id in EXTENSION_CITY_IDS:
        for year in EXTENSION_YEARS:
            for variable in DEFAULT_DAYMET_VARIABLES:
                specs.append(
                    TaskSpec(
                        task_id=f"daymet-subset-{city_id}-{year}-{variable}",
                        kind="acquire_daymet_subset",
                        payload={
                            "city_id": city_id,
                            "year": year,
                            "variable": variable,
                        },
                    )
                )
    for city_id in EXTENSION_CITY_IDS:
        dates_sha = canonical_sha256(_extension_dates(permit, city_id))
        specs.append(
            TaskSpec(
                task_id=f"sentinel-inventory-{city_id}",
                kind="build_sentinel_inventory",
                payload={"city_id": city_id, "target_dates_sha256": dates_sha},
            )
        )
    for city_id in EXTENSION_CITY_IDS:
        specs.append(
            TaskSpec(
                task_id=f"sentinel-cache-{city_id}",
                kind="acquire_sentinel_cache",
                payload={"city_id": city_id},
            )
        )
    specs.append(
        TaskSpec(
            task_id="acquisition-complete",
            kind="finalize_acquisition",
            payload={"extension_city_ids": list(EXTENSION_CITY_IDS)},
        )
    )
    for city_id in EXTENSION_CITY_IDS:
        specs.append(
            TaskSpec(
                task_id=f"build-extension-{city_id}",
                kind="build_extension_city",
                payload={"city_id": city_id},
            )
        )
    for city_id in SOURCE_CITY_IDS:
        specs.append(
            TaskSpec(
                task_id=f"compile-source-{city_id}",
                kind="compile_source_city",
                payload={"city_id": city_id},
            )
        )
    specs.append(
        TaskSpec(
            task_id="source-predictors-46-complete",
            kind="finalize_predictors",
            payload={"source_city_ids": list(SOURCE_CITY_IDS), "feature_count": 46},
        )
    )
    if len(specs) != EXPECTED_TASK_COUNT or len({spec.task_id for spec in specs}) != len(specs):
        raise AssertionError("Predictor extension task plan count changed.")
    return tuple(specs)


def predictor_task_plan_sha256(permit: Mapping[str, Any]) -> str:
    specs = task_specs_from_predictor_authorization(permit)
    return canonical_sha256(
        [{"task_id": spec.task_id, "kind": spec.kind, "payload": spec.payload} for spec in specs]
    )


def initialize_source_predictor_runtime(
    project_root: str | Path,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate the formal permit before creating the independent queue."""

    settings = load_predictor_extension_settings(project_root, config_path)
    permit = load_m3_source_predictor_extension_runtime_permit(
        settings.root, settings.authorization, settings.config_path
    )
    specs = task_specs_from_predictor_authorization(permit)
    queue = ModelRunQueue(settings.database)
    run_id = source_predictor_run_id(permit)
    queue.initialize_run(run_id, specs, desired_state="paused")
    return source_predictor_runtime_status(queue, run_id, settings=settings)


def _counts_for_kind(queue: ModelRunQueue, run_id: str, kind: str) -> dict[str, int]:
    return dict(
        queue.counts_by_kind(run_id).get(
            kind,
            {"pending": 0, "running": 0, "complete": 0, "quarantined": 0, "total": 0},
        )
    )


def active_predictor_kind(queue: ModelRunQueue, run_id: str, phase: str) -> str:
    if phase not in PHASES:
        raise M3SourcePredictorRuntimeError("Unknown predictor phase.")
    if phase == OFFLINE_PHASE:
        acquisition = _counts_for_kind(queue, run_id, "finalize_acquisition")
        if acquisition["complete"] != 1:
            raise M3SourcePredictorRuntimeError(
                "Offline assembly is sealed until acquisition completion is durable."
            )
    kinds = ONLINE_KINDS if phase == ONLINE_PHASE else OFFLINE_KINDS
    for kind in kinds:
        counts = _counts_for_kind(queue, run_id, kind)
        if counts["quarantined"]:
            raise M3SourcePredictorRuntimeError(f"{kind} contains quarantined work.")
        if counts["complete"] < counts["total"]:
            return kind
    return "complete"


def source_predictor_runtime_status(
    queue: ModelRunQueue,
    run_id: str,
    *,
    settings: PredictorExtensionSettings,
    phase: str | None = None,
) -> dict[str, Any]:
    counts = queue.counts(run_id)
    desired = queue.get_desired_state(run_id)
    running = queue.list_tasks(run_id, statuses=("running",))
    if counts["quarantined"]:
        state = "failed"
    elif _counts_for_kind(queue, run_id, "finalize_predictors")["complete"] == 1:
        state = "source_predictors_46_complete_waiting_for_nested_loso_authorization"
    elif desired == "paused" and running:
        state = "pausing"
    elif desired == "paused":
        state = "paused"
    else:
        state = "running"
    active = (
        None
        if phase is None
        else "failed"
        if counts["quarantined"]
        else active_predictor_kind(queue, run_id, phase)
    )
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": state,
        "run_id": run_id,
        "task_plan_sha256": predictor_task_plan_sha256(
            load_m3_source_predictor_extension_runtime_permit(
                settings.root, settings.authorization, settings.config_path
            )
        ),
        "desired_state": desired,
        "counts": counts,
        "counts_by_kind": queue.counts_by_kind(run_id),
        "active_task_ids": [task.task_id for task in running],
        "active_phase": phase,
        "active_kind": active,
        "compute_workers": 1,
        "download_workers": 1 if phase == ONLINE_PHASE else 0,
        "maximum_active_tasks": 1,
        "network_allowed": phase == ONLINE_PHASE,
        "href_reads_allowed": phase == ONLINE_PHASE,
        "blind_test_targets_sealed": True,
        "model_fit_select_predict_or_score_performed": False,
    }
