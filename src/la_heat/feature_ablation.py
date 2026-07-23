"""Resumable, fixed-selection feature-family ablation outer refits.

This deliberately does *not* tune or select on outer test folds.  It authenticates
the completed M2 run, inherits its selected candidate for every outer fold, and
refits only the named predictor families with fold-local preprocessing.
"""

from __future__ import annotations

import json
import os
import socket
import time
import tomllib
import uuid
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from la_heat.model_run_context import ModelRunContext, load_model_run_context
from la_heat.model_run_queue import LeaseLostError, ModelRunQueue, TaskSpec
from la_heat.model_task_engine import OuterFitTask, build_task_plan, run_outer_fit
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "feature-ablation-outer-refit-v1"
TASK_SCHEMA_VERSION: Final = 1
RESULT_SCHEMA_VERSION: Final = 1
_ALLOWED_FAMILIES: Final = frozenset({"calendar", "weather", "land_use", "geography", "satellite"})
_FORBIDDEN_FEATURE_NAMES: Final = frozenset(
    {"tract_geoid", "target_date", "longitude", "latitude", "centroid_x", "centroid_y"}
)
_SPLIT_FAMILIES: Final = ("temporal", "spatial", "joint")
_OOF_ROWS_PER_FAMILY: Final = 63_403


class FeatureAblationError(RuntimeError):
    """Raised when an ablation input, task, or artifact violates the contract."""


@dataclass(frozen=True)
class AblationSpec:
    ablation_id: str
    feature_families: frozenset[str]


@dataclass(frozen=True)
class AblationConfig:
    path: Path
    semantic_sha256: str
    source_run_id: str
    source_runs_root: Path
    source_evaluation_directory: Path
    queue_path: Path
    runs_root: Path
    status_path: Path
    output_directory: Path
    final_test_year: int
    unlock_final_test: bool
    model_id: str
    workers_maximum: int
    lease_seconds: float
    heartbeat_seconds: float
    max_attempts: int
    ablations: tuple[AblationSpec, ...]


@dataclass(frozen=True)
class PreparedFeatureAblation:
    context: ModelRunContext
    config: AblationConfig
    run_id: str
    source_manifest_commit: str
    source_selection_commit: str
    task_specs: tuple[TaskSpec, ...]
    run_directory: Path
    fragments_directory: Path
    manifest_path: Path
    queue: ModelRunQueue


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_committed_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FeatureAblationError(f"{label} is unreadable.") from error
    if not isinstance(payload, dict):
        raise FeatureAblationError(f"{label} must be a JSON object.")
    commit = payload.get("commit_sha256")
    working = dict(payload)
    working.pop("commit_sha256", None)
    if not isinstance(commit, str) or canonical_sha256(working) != commit:
        raise FeatureAblationError(f"{label} commit is invalid.")
    return payload, commit


def load_feature_ablation_config(
    path: str | Path = "configs/feature_ablation.toml",
    *,
    project_root: Path | None = None,
) -> AblationConfig:
    root = _root() if project_root is None else project_root.resolve()
    config_path = _resolve(root, str(path))
    try:
        raw_bytes = config_path.read_bytes()
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise FeatureAblationError("Feature-ablation config is unreadable.") from error
    section = raw.get("feature_ablation") if isinstance(raw, dict) else None
    if not isinstance(section, dict) or set(section) != {
        "schema_version",
        "algorithm_version",
        "source_run_id",
        "source_runs_root",
        "source_evaluation_directory",
        "queue_path",
        "runs_root",
        "status_path",
        "output_directory",
        "final_test_year",
        "unlock_final_test",
        "model_id",
        "workers_maximum",
        "lease_seconds",
        "heartbeat_seconds",
        "max_attempts",
        "ablations",
    }:
        raise FeatureAblationError("Feature-ablation config schema is invalid.")
    if section["schema_version"] != 1 or section["algorithm_version"] != ALGORITHM_VERSION:
        raise FeatureAblationError("Feature-ablation algorithm/version is invalid.")
    if section["final_test_year"] != 2025 or section["unlock_final_test"] is not False:
        raise PermissionError("Feature ablation must keep 2025 locked.")
    if section["model_id"] != "M2":
        raise FeatureAblationError("Only fixed-selected M2 ablations are permitted.")
    if not isinstance(section["ablations"], list) or len(section["ablations"]) != 3:
        raise FeatureAblationError("Exactly three predeclared fitted ablations are required.")
    ablations: list[AblationSpec] = []
    for item in section["ablations"]:
        if not isinstance(item, dict) or set(item) != {"ablation_id", "feature_families"}:
            raise FeatureAblationError("Ablation config entry is invalid.")
        identifier, families = item["ablation_id"], item["feature_families"]
        if not isinstance(identifier, str) or not identifier or identifier != identifier.strip():
            raise FeatureAblationError("Ablation identifier is invalid.")
        if not isinstance(families, list) or not families:
            raise FeatureAblationError("Ablation feature families are invalid.")
        family_set = frozenset(str(value) for value in families)
        if len(family_set) != len(families) or not family_set.issubset(_ALLOWED_FAMILIES):
            raise FeatureAblationError("Ablation feature family is invalid.")
        if "calendar" not in family_set:
            raise FeatureAblationError("Every ablation must retain calendar predictors.")
        ablations.append(AblationSpec(identifier, family_set))
    if len({item.ablation_id for item in ablations}) != len(ablations):
        raise FeatureAblationError("Ablation identifiers must be unique.")
    return AblationConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        source_run_id=str(section["source_run_id"]),
        source_runs_root=_resolve(root, str(section["source_runs_root"])),
        source_evaluation_directory=_resolve(root, str(section["source_evaluation_directory"])),
        queue_path=_resolve(root, str(section["queue_path"])),
        runs_root=_resolve(root, str(section["runs_root"])),
        status_path=_resolve(root, str(section["status_path"])),
        output_directory=_resolve(root, str(section["output_directory"])),
        final_test_year=2025,
        unlock_final_test=False,
        model_id="M2",
        workers_maximum=int(section["workers_maximum"]),
        lease_seconds=float(section["lease_seconds"]),
        heartbeat_seconds=float(section["heartbeat_seconds"]),
        max_attempts=int(section["max_attempts"]),
        ablations=tuple(ablations),
    )


def _authenticate_source(
    context: ModelRunContext, config: AblationConfig
) -> tuple[dict[str, str], str, str]:
    run_root = config.source_runs_root / config.source_run_id
    manifest, manifest_commit = _read_committed_json(
        run_root / "run_manifest.json", label="Source run manifest"
    )
    if (
        manifest.get("run_id") != config.source_run_id
        or manifest.get("context_run_id") != context.run_id
        or manifest.get("final_test_year") != 2025
        or manifest.get("final_test_unlocked") is not False
        or manifest.get("outer_task_count") != 2155
    ):
        raise FeatureAblationError("Source run is not the authenticated locked development run.")
    selections, selections_commit = _read_committed_json(
        run_root / "outer_selections.json", label="Source outer selections"
    )
    if (
        selections.get("run_id") != config.source_run_id
        or selections.get("task_plan_sha256") != manifest.get("task_plan_sha256")
        or selections.get("selection_config_sha256") != context.model_selection.semantic_sha256
        or selections.get("selection_count") != 2155
        or not isinstance(selections.get("selections"), list)
    ):
        raise FeatureAblationError("Source outer selections do not authenticate this run.")
    plan = build_task_plan(context.fold_definitions, context.model_selection)
    expected = {task.task_id: task for task in plan.outer_tasks if task.model_id == "M2"}
    selected: dict[str, str] = {}
    for entry in selections["selections"]:
        if not isinstance(entry, dict) or entry.get("model_id") != "M2":
            continue
        task_id = entry.get("outer_task_id")
        candidate_id = entry.get("selected_candidate_id")
        task = expected.get(task_id)
        if task is None or not isinstance(candidate_id, str) or task_id in selected:
            raise FeatureAblationError("Source M2 selection identity is invalid.")
        candidate = context.model_selection.candidate("M2", candidate_id)
        if entry.get("selected_parameters") != candidate.factory_parameters():
            raise FeatureAblationError("Source M2 selected parameters drifted.")
        selected[task_id] = candidate_id
    if set(selected) != set(expected) or len(expected) != 431:
        raise FeatureAblationError("Source M2 selections do not cover exactly 431 outer folds.")
    provenance, provenance_commit = _read_committed_json(
        config.source_evaluation_directory / "model_run_compile_provenance.json",
        label="Source compile provenance",
    )
    if (
        provenance.get("run_id") != config.source_run_id
        or provenance.get("context_run_id") != context.run_id
        or provenance.get("final_test_locked") is not True
        or provenance.get("contains_final_test_year") is not False
        or not (config.source_evaluation_directory / "oof_predictions.parquet").is_file()
    ):
        raise FeatureAblationError("Authenticated all-feature M2 OOF reference is unavailable.")
    return (
        selected,
        manifest_commit,
        canonical_sha256(
            {
                "selections_commit": selections_commit,
                "compile_commit": provenance_commit,
                "oof_sha256": sha256_file(
                    config.source_evaluation_directory / "oof_predictions.parquet"
                ),
            }
        ),
    )


def _task_payload(
    *,
    ablation: AblationSpec,
    outer_task: OuterFitTask,
    selected_candidate_id: str,
    config: AblationConfig,
    source_selection_lock: str,
) -> dict[str, Any]:
    core = {
        "schema_version": TASK_SCHEMA_VERSION,
        "task_kind": "feature_ablation_outer_refit",
        "ablation_id": ablation.ablation_id,
        "feature_families": sorted(ablation.feature_families),
        "source_run_id": config.source_run_id,
        "source_selection_lock": source_selection_lock,
        "selected_candidate_id": selected_candidate_id,
        "outer_task": outer_task.to_dict(),
    }
    return {**core, "task_id": "feature-ablation-" + canonical_sha256(core)}


def prepare_feature_ablation(
    *,
    config_path: str | Path = "configs/feature_ablation.toml",
    initial_desired_state: str = "paused",
) -> PreparedFeatureAblation:
    if initial_desired_state not in {"running", "paused"}:
        raise ValueError("initial_desired_state must be running or paused.")
    config = load_feature_ablation_config(config_path)
    context = load_model_run_context()
    if context.model_selection.unlock_final_test or context.model_selection.final_test_year != 2025:
        raise PermissionError("The model context does not preserve the locked 2025 contract.")
    selected, manifest_commit, source_selection_lock = _authenticate_source(context, config)
    registry_names = set(context.registry.loc[context.registry["role"].eq("model"), "feature_name"])
    if registry_names & _FORBIDDEN_FEATURE_NAMES:
        raise FeatureAblationError(
            "Forbidden identifier or coordinate is registered as a predictor."
        )
    plan = build_task_plan(context.fold_definitions, context.model_selection)
    outer = tuple(task for task in plan.outer_tasks if task.model_id == "M2")
    tasks = tuple(
        TaskSpec(task_id=payload["task_id"], kind="outer_refit", payload=payload)
        for ablation in config.ablations
        for task in outer
        for payload in (
            _task_payload(
                ablation=ablation,
                outer_task=task,
                selected_candidate_id=selected[task.task_id],
                config=config,
                source_selection_lock=source_selection_lock,
            ),
        )
    )
    if len(tasks) != 1293 or len({task.task_id for task in tasks}) != 1293:
        raise AssertionError("Feature-ablation plan must contain exactly 1,293 unique refits.")
    plan_sha = canonical_sha256(
        [{"task_id": task.task_id, "payload": task.payload} for task in tasks]
    )
    run_id = canonical_sha256(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "context_run_id": context.run_id,
            "config": config.semantic_sha256,
            "source_manifest_commit": manifest_commit,
            "source_selection_lock": source_selection_lock,
            "task_plan": plan_sha,
        }
    )
    run_directory = config.runs_root / run_id
    fragments = run_directory / "outer_fragments"
    fragments.mkdir(parents=True, exist_ok=True)
    manifest_path = run_directory / "run_manifest.json"
    manifest = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "run_id": run_id,
        "context_run_id": context.run_id,
        "source_run_id": config.source_run_id,
        "source_run_manifest_commit_sha256": manifest_commit,
        "source_selection_and_all_oof_lock_sha256": source_selection_lock,
        "feature_ablation_config_semantic_sha256": config.semantic_sha256,
        "task_plan_sha256": plan_sha,
        "outer_task_count_per_ablation": 431,
        "fitted_ablation_count": 3,
        "total_task_count": 1293,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "scientific_contract": {
            "fixed_selected_hyperparameters": True,
            "outer_test_used_for_selection_or_tuning": False,
            "preprocessing_fit_scope": "outer_train_only",
            "date_balanced_training_weights": True,
            "random_row_split": False,
            "tract_identifiers_as_predictors": False,
            "raw_coordinates_as_predictors": False,
            "all_feature_reference": "authenticated_M2_OOF",
        },
        "ablations": [
            {"ablation_id": item.ablation_id, "feature_families": sorted(item.feature_families)}
            for item in config.ablations
        ],
    }
    manifest["commit_sha256"] = canonical_sha256(manifest)
    if manifest_path.exists():
        existing, _ = _read_committed_json(
            manifest_path, label="Existing feature-ablation manifest"
        )
        if existing != manifest:
            raise FeatureAblationError("Existing feature-ablation run manifest drifted.")
    else:
        atomic_json(manifest, manifest_path)
    queue = ModelRunQueue(config.queue_path)
    queue.initialize_run(run_id, tasks, desired_state=initial_desired_state)
    return PreparedFeatureAblation(
        context,
        config,
        run_id,
        manifest_commit,
        source_selection_lock,
        tasks,
        run_directory,
        fragments,
        manifest_path,
        queue,
    )


_WORKER_CONTEXT: ModelRunContext | None = None
_WORKER_CONTEXT_ID: str | None = None


def _init_worker(context_id: str) -> None:
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    global _WORKER_CONTEXT, _WORKER_CONTEXT_ID
    context = load_model_run_context()
    if context.run_id != context_id:
        raise FeatureAblationError("Worker context identity drifted.")
    _WORKER_CONTEXT, _WORKER_CONTEXT_ID = context, context_id


def _execute_task(payload: Mapping[str, Any], fragments_directory: str) -> dict[str, Any]:
    started = time.perf_counter()
    if _WORKER_CONTEXT is None or not isinstance(payload, Mapping):
        raise FeatureAblationError("Feature-ablation worker was not initialized.")
    required = {
        "schema_version",
        "task_kind",
        "ablation_id",
        "feature_families",
        "source_run_id",
        "source_selection_lock",
        "selected_candidate_id",
        "outer_task",
        "task_id",
    }
    if (
        set(payload) != required
        or payload["schema_version"] != 1
        or payload["task_kind"] != "feature_ablation_outer_refit"
    ):
        raise FeatureAblationError("Feature-ablation task schema is invalid.")
    task = OuterFitTask.from_dict(payload["outer_task"])
    families = frozenset(payload["feature_families"])
    if (
        task.model_id != "M2"
        or not families.issubset(_ALLOWED_FAMILIES)
        or "calendar" not in families
    ):
        raise FeatureAblationError("Feature-ablation task contract is invalid.")
    selected = _WORKER_CONTEXT.model_selection.candidate(
        "M2", str(payload["selected_candidate_id"])
    )
    predictions = run_outer_fit(
        task,
        selected,
        row_groups=_WORKER_CONTEXT.row_groups,
        model_frame=_WORKER_CONTEXT.features,
        target=_WORKER_CONTEXT.target,
        registry=_WORKER_CONTEXT.registry,
        model_selection_config=_WORKER_CONTEXT.model_selection,
        spatial_buffer_geoids=_WORKER_CONTEXT.spatial_buffer_geoids,
        feature_families=families,
    )
    predictions.insert(3, "ablation_id", str(payload["ablation_id"]))
    destination = (
        Path(fragments_directory) / str(payload["ablation_id"]) / f"{payload['task_id']}.parquet"
    )
    atomic_parquet(predictions, destination)
    record = parquet_file_record(destination, predictions)
    record.update(
        {
            "path": destination.relative_to(Path(fragments_directory).parent).as_posix(),
            "path_base": "run_directory",
            "semantic_sha256": canonical_frame_sha256(
                predictions, sort_by=["target_date", "tract_geoid"]
            ),
        }
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "kind": "outer_refit",
        "ablation_id": payload["ablation_id"],
        "model_id": "M2",
        "selected_candidate_id": selected.candidate_id,
        "duration_seconds": time.perf_counter() - started,
        "fragment": record,
    }


def _write_status(
    prepared: PreparedFeatureAblation, *, workers: int, state: str, event: str | None = None
) -> dict[str, Any]:
    counts = prepared.queue.counts(prepared.run_id)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": prepared.run_id,
        "state": state,
        "desired_state": prepared.queue.get_desired_state(prepared.run_id),
        "workers": workers,
        "counts": counts,
        "total": counts["total"],
        "completed": counts["complete"],
        "active": counts["running"],
        "pending": counts["pending"],
        "quarantined": counts["quarantined"],
        "independent_outer_folds_per_ablation": 431,
        "fitted_ablation_count": 3,
        "all_feature_reference": "authenticated_M2_OOF",
        "final_test_locked": True,
    }
    if event:
        payload["event"] = event
    atomic_json(payload, prepared.config.status_path)
    return payload


def _validate_compiled_ablation_coverage(
    combined: Any,
    *,
    ablation_ids: tuple[str, ...],
    expected_keys_sha256: str,
    expected_rows_per_family: int = _OOF_ROWS_PER_FAMILY,
) -> None:
    """Require one complete OOF face for every ablation and split family."""

    required = {"ablation_id", "family", "tract_geoid", "target_date"}
    if not required.issubset(combined.columns):
        raise FeatureAblationError("Compiled ablation rows lack identity columns.")
    expected_total = len(ablation_ids) * len(_SPLIT_FAMILIES) * expected_rows_per_family
    if len(combined) != expected_total:
        raise FeatureAblationError("Compiled ablation OOF row cardinality is invalid.")
    if set(combined["ablation_id"].unique()) != set(ablation_ids):
        raise FeatureAblationError("Compiled ablation identities are incomplete.")
    if set(combined["family"].unique()) != set(_SPLIT_FAMILIES):
        raise FeatureAblationError("Compiled split-family identities are incomplete.")

    for ablation_id in ablation_ids:
        ablation_rows = combined.loc[combined["ablation_id"].eq(ablation_id)]
        if len(ablation_rows) != len(_SPLIT_FAMILIES) * expected_rows_per_family:
            raise FeatureAblationError("An ablation does not contain three complete OOF faces.")
        for family in _SPLIT_FAMILIES:
            subset = ablation_rows.loc[ablation_rows["family"].eq(family)]
            if (
                len(subset) != expected_rows_per_family
                or subset.duplicated(["tract_geoid", "target_date"]).any()
            ):
                raise FeatureAblationError(
                    "An ablation split family is not exactly one prediction per context row."
                )
            if (
                canonical_frame_sha256(
                    subset.loc[:, ["tract_geoid", "target_date"]],
                    sort_by=["target_date", "tract_geoid"],
                )
                != expected_keys_sha256
            ):
                raise FeatureAblationError(
                    "An ablation split-family key set drifted from the authenticated context."
                )


def compile_feature_ablation(prepared: PreparedFeatureAblation) -> dict[str, Any]:
    """Compile exact fitted-ablation OOF rows after every queue task completes.

    The all-feature comparator is deliberately referenced, rather than refit or
    copied: it remains the authenticated M2 OOF artifact selected before this
    descriptive ablation was planned.
    """

    counts = prepared.queue.counts(prepared.run_id)
    if counts["complete"] != counts["total"] or counts["quarantined"]:
        raise FeatureAblationError("Cannot compile before every ablation refit completes.")
    expected_keys = canonical_frame_sha256(
        prepared.context.keys, sort_by=["target_date", "tract_geoid"]
    )
    frames: list[Any] = []
    fragments: list[dict[str, Any]] = []
    records = prepared.queue.list_tasks(prepared.run_id, statuses=("complete",))
    if len(records) != 1293:
        raise FeatureAblationError("Completed ablation task count is invalid.")
    for record in records:
        result = record.result
        if not isinstance(result, dict) or not isinstance(result.get("fragment"), dict):
            raise FeatureAblationError("Completed ablation result lacks a fragment record.")
        fragment = dict(result["fragment"])
        relative = fragment.get("path")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise FeatureAblationError("Ablation fragment path is unsafe.")
        path = prepared.run_directory / relative
        if not path.is_file():
            raise FeatureAblationError("Ablation fragment is missing.")
        frame = __import__("pandas").read_parquet(path)
        observed = parquet_file_record(path, frame)
        if any(
            observed[key] != fragment.get(key)
            for key in ("sha256", "bytes", "rows", "schema_sha256")
        ):
            raise FeatureAblationError("Ablation fragment byte lock failed.")
        payload = record.payload
        if (
            frame.get("ablation_id") is None
            or frame["ablation_id"].nunique() != 1
            or frame["ablation_id"].iloc[0] != payload["ablation_id"]
            or len(frame) != payload["outer_task"]["expected_outer_test_row_count"]
        ):
            raise FeatureAblationError("Ablation fragment identity or coverage is invalid.")
        if frame["target_date"].dt.year.ge(2025).any():
            raise PermissionError("Ablation fragment contains locked 2025+ rows.")
        frames.append(frame)
        fragments.append(
            {"task_id": record.task_id, "ablation_id": payload["ablation_id"], **fragment}
        )
    pandas = __import__("pandas")
    combined = pandas.concat(frames, ignore_index=True).sort_values(
        ["ablation_id", "family", "target_date", "tract_geoid"], kind="stable"
    )
    ablation_ids = tuple(item.ablation_id for item in prepared.config.ablations)
    _validate_compiled_ablation_coverage(
        combined,
        ablation_ids=ablation_ids,
        expected_keys_sha256=expected_keys,
    )
    output = prepared.config.output_directory / prepared.run_id
    output.mkdir(parents=True, exist_ok=True)
    output_path = output / "feature_ablation_oof_predictions.parquet"
    atomic_parquet(combined, output_path)
    output_record = parquet_file_record(output_path, combined)
    reference = prepared.config.source_evaluation_directory / "oof_predictions.parquet"
    compiler_pipeline_sha256, compiler_pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_root(),
        relative_paths=(
            "scripts/run_feature_ablation.py",
            "src/la_heat/feature_ablation.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=ALGORITHM_VERSION,
    )
    provenance = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "run_id": prepared.run_id,
        "context_run_id": prepared.context.run_id,
        "source_run_id": prepared.config.source_run_id,
        "source_run_manifest_commit_sha256": prepared.source_manifest_commit,
        "source_selection_and_all_oof_lock_sha256": prepared.source_selection_commit,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "fitted_ablation_count": 3,
        "split_families": list(_SPLIT_FAMILIES),
        "split_family_count": len(_SPLIT_FAMILIES),
        "outer_folds_per_ablation": 431,
        "fitted_fragment_count": len(fragments),
        "fitted_oof_rows_per_ablation_family": _OOF_ROWS_PER_FAMILY,
        "fitted_oof_rows_per_ablation": len(_SPLIT_FAMILIES) * _OOF_ROWS_PER_FAMILY,
        "fitted_oof_row_count": len(combined),
        "compiler_pipeline_sha256": compiler_pipeline_sha256,
        "compiler_pipeline_fingerprint": compiler_pipeline_fingerprint,
        "all_feature_reference": {
            "path": str(reference),
            "sha256": sha256_file(reference),
            "model_id": "M2",
            "refit_performed": False,
        },
        "output_files": {output_path.name: output_record},
        "input_fragments": fragments,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / "feature_ablation_compile_provenance.json")
    return provenance


def run_feature_ablation(
    *,
    config_path: str | Path = "configs/feature_ablation.toml",
    workers: int = 4,
    resume: bool = False,
    prepare_only: bool = False,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    prepared = prepare_feature_ablation(config_path=config_path, initial_desired_state="paused")
    if not 1 <= workers <= prepared.config.workers_maximum:
        raise ValueError("workers is outside the frozen safe range.")
    if max_tasks is not None and max_tasks < 1:
        raise ValueError("max_tasks must be positive when set.")
    if resume:
        prepared.queue.set_desired_state(prepared.run_id, "running")
    if prepare_only:
        return _write_status(prepared, workers=workers, state="ready")
    if prepared.queue.get_desired_state(prepared.run_id) != "running":
        return _write_status(prepared, workers=workers, state="paused")
    active: dict[Future[dict[str, Any]], tuple[Any, str]] = {}
    handled = 0
    owner_prefix = f"ablation-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(prepared.context.run_id,)
    ) as executor:
        while True:
            while (
                len(active) < workers
                and prepared.queue.get_desired_state(prepared.run_id) == "running"
                and (max_tasks is None or handled + len(active) < max_tasks)
            ):
                owner = f"{owner_prefix}-{len(active)}"
                claim = prepared.queue.claim_next(
                    prepared.run_id,
                    owner=owner,
                    lease_seconds=prepared.config.lease_seconds,
                    kinds=("outer_refit",),
                )
                if claim is None:
                    break
                active[
                    executor.submit(_execute_task, claim.payload, str(prepared.fragments_directory))
                ] = (claim, owner)
            if not active:
                counts = prepared.queue.counts(prepared.run_id)
                if max_tasks is not None and handled >= max_tasks and counts["pending"]:
                    prepared.queue.set_desired_state(prepared.run_id, "paused")
                    return _write_status(
                        prepared, workers=workers, state="paused", event="max_tasks checkpoint"
                    )
                state = "complete" if counts["complete"] == counts["total"] else "paused"
                if state == "complete":
                    compile_feature_ablation(prepared)
                return _write_status(prepared, workers=workers, state=state)
            done, _ = wait(
                tuple(active),
                timeout=min(10.0, prepared.config.heartbeat_seconds),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                claim, owner = active.pop(future)
                handled += 1
                try:
                    result = future.result()
                    prepared.queue.complete(
                        prepared.run_id,
                        claim.task_id,
                        owner=owner,
                        generation=claim.claim_generation,
                        result=result,
                    )
                except BaseException as error:  # queue preserves retry/quarantine evidence
                    permanent = isinstance(
                        error,
                        (
                            AssertionError,
                            FeatureAblationError,
                            PermissionError,
                            TypeError,
                            ValueError,
                        ),
                    )
                    if permanent or claim.attempt >= prepared.config.max_attempts:
                        prepared.queue.quarantine(
                            prepared.run_id,
                            claim.task_id,
                            owner=owner,
                            generation=claim.claim_generation,
                            error_type=type(error).__name__,
                            result={
                                "schema_version": 1,
                                "error_type": type(error).__name__,
                                "permanent": permanent,
                                "message_omitted": True,
                            },
                        )
                    else:
                        prepared.queue.retry(
                            prepared.run_id,
                            claim.task_id,
                            owner=owner,
                            generation=claim.claim_generation,
                            error_type=type(error).__name__,
                            base_delay_seconds=5.0,
                            max_delay_seconds=300.0,
                        )
            for claim, owner in active.values():
                try:
                    prepared.queue.heartbeat(
                        prepared.run_id,
                        claim.task_id,
                        owner=owner,
                        generation=claim.claim_generation,
                        lease_seconds=prepared.config.lease_seconds,
                    )
                except LeaseLostError:
                    pass
            _write_status(prepared, workers=workers, state="running")
