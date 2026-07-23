"""Resumable coordinator for the frozen grouped development-model evaluation.

The scientific fit functions live in :mod:`la_heat.model_task_engine`.  This
module adds only durable orchestration: an immutable SQLite task plan, strict
inner-before-outer phase gating, process workers, leases/heartbeats, automatic
retry, safe pause/resume, and auditable prediction fragments.
"""

from __future__ import annotations

import json
import math
import os
import socket
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.execution_ownership import assert_grouped_execution_authorized
from la_heat.model_run_compile import (
    DEFAULT_OUTPUT_DIRECTORY as DEFAULT_COMPILE_OUTPUT_DIRECTORY,
)
from la_heat.model_run_compile import (
    OuterFragmentRecord,
    compile_model_run_outputs,
)
from la_heat.model_run_context import ModelRunContext, load_model_run_context
from la_heat.model_run_queue import (
    LeaseLostError,
    ModelRunQueue,
    TaskRecord,
    TaskSpec,
)
from la_heat.model_runtime import modeling_runtime_fingerprint
from la_heat.model_selection import (
    CandidateScore,
    CandidateSelection,
    ModelSelectionAuditError,
)
from la_heat.model_task_engine import (
    InnerFitResult,
    InnerFitTask,
    ModelTaskAuditError,
    OuterFitTask,
    TaskPlan,
    build_task_plan,
    run_inner_fit,
    run_outer_fit,
    select_outer_candidate,
)
from la_heat.modeling import ModelingContractError
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
)

GROUPED_MODEL_RUN_ALGORITHM_VERSION: Final = "grouped-model-run-v2"
RESULT_SCHEMA_VERSION: Final = 2
EXPECTED_INNER_TASK_COUNT: Final = 55_645
EXPECTED_OUTER_TASK_COUNT: Final = 2_155
DEFAULT_QUEUE_PATH: Final = Path("data/interim/model_runs/model_tasks.sqlite3")
DEFAULT_RUNS_ROOT: Final = Path("data/interim/model_runs/runs")
DEFAULT_STATUS_PATH: Final = Path("data/interim/model_runs/status.json")
PORTABLE_RELOCATION_ENV: Final = "LA_HEAT_PORTABLE_RELOCATION"

_RUNTIME_PATHS: Final = (
    "configs/model_selection.toml",
    "scripts/run_grouped_models.py",
    "src/la_heat/execution_ownership.py",
    "src/la_heat/grouped_model_run.py",
    "src/la_heat/metrics.py",
    "src/la_heat/model_run_compile.py",
    "src/la_heat/model_run_context.py",
    "src/la_heat/model_run_queue.py",
    "src/la_heat/model_runtime.py",
    "src/la_heat/model_selection.py",
    "src/la_heat/model_task_engine.py",
    "src/la_heat/modeling.py",
    "src/la_heat/portable_relocation.py",
    "src/la_heat/provenance.py",
    "src/la_heat/validation_splits.py",
)


class GroupedModelRunError(RuntimeError):
    """Raised when durable orchestration or output compilation fails closed."""


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Code- and plan-bound identity for one immutable evaluation run."""

    run_id: str
    context_run_id: str
    task_plan_sha256: str
    runtime_fingerprint_sha256: str
    runtime_fingerprint: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunPaths:
    """All mutable and immutable paths owned by a single coordinator run."""

    project_root: Path
    queue_path: Path
    runs_root: Path
    run_directory: Path
    fragments_directory: Path
    status_path: Path
    run_manifest_path: Path
    selections_path: Path


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Authenticated inputs, deterministic task plan, and persistent queue."""

    context: ModelRunContext
    task_plan: TaskPlan
    identity: ExecutionIdentity
    paths: RunPaths
    queue: ModelRunQueue


@dataclass(frozen=True, slots=True)
class ActiveClaim:
    """One in-flight future and the fenced queue claim it represents."""

    record: TaskRecord
    owner: str
    started_monotonic: float
    submitted_at_utc: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _context_loader_kwargs(project_root: Path) -> dict[str, Path]:
    """Resolve the explicit portable manifest inherited by all worker processes."""

    value = os.environ.get(PORTABLE_RELOCATION_ENV)
    if value is None:
        return {}
    if not value.strip():
        raise GroupedModelRunError(f"{PORTABLE_RELOCATION_ENV} cannot be empty.")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    manifest = candidate.resolve()
    try:
        manifest.relative_to(project_root)
    except ValueError as error:
        raise GroupedModelRunError(
            f"{PORTABLE_RELOCATION_ENV} must resolve inside the project root."
        ) from error
    if not manifest.is_file():
        raise GroupedModelRunError(
            f"{PORTABLE_RELOCATION_ENV} does not resolve to a file."
        )
    return {
        "portable_manifest_path": manifest,
        "portable_root": project_root,
    }


def _positive_integer(value: int, *, name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be no greater than {maximum}.")
    return value


def _positive_seconds(value: float, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def build_execution_identity(
    context: ModelRunContext,
    task_plan: TaskPlan,
    *,
    project_root: str | Path | None = None,
    runtime_paths: Sequence[str] = _RUNTIME_PATHS,
) -> ExecutionIdentity:
    """Bind a run to upstream commits, the exact task plan, code, and runtime."""

    if not isinstance(context, ModelRunContext):
        raise TypeError("context must be an authenticated ModelRunContext.")
    if not isinstance(task_plan, TaskPlan):
        raise TypeError("task_plan must be a validated TaskPlan.")
    root = _project_root() if project_root is None else Path(project_root).resolve()
    task_plan_sha256 = canonical_sha256(task_plan.to_dict())
    runtime_sha256, runtime_fingerprint = modeling_runtime_fingerprint(
        project_root=root,
        relative_paths=tuple(runtime_paths),
        algorithm_version=GROUPED_MODEL_RUN_ALGORITHM_VERSION,
    )
    run_id = canonical_sha256(
        {
            "algorithm_version": GROUPED_MODEL_RUN_ALGORITHM_VERSION,
            "context_run_id": context.run_id,
            "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
            "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
            "model_selection_commit_sha256": context.model_selection_commit_sha256,
            "task_plan_sha256": task_plan_sha256,
            "runtime_fingerprint_sha256": runtime_sha256,
        }
    )
    return ExecutionIdentity(
        run_id=run_id,
        context_run_id=context.run_id,
        task_plan_sha256=task_plan_sha256,
        runtime_fingerprint_sha256=runtime_sha256,
        runtime_fingerprint=runtime_fingerprint,
    )


def queue_task_specs(task_plan: TaskPlan) -> tuple[TaskSpec, ...]:
    """Convert a scientific plan into an immutable, phase-labelled queue plan."""

    if not isinstance(task_plan, TaskPlan):
        raise TypeError("task_plan must be a TaskPlan.")
    specs = tuple(
        TaskSpec(task_id=task.task_id, kind="inner_fit", payload=task.to_dict())
        for task in task_plan.inner_tasks
    ) + tuple(
        TaskSpec(task_id=task.task_id, kind="outer_refit", payload=task.to_dict())
        for task in task_plan.outer_tasks
    )
    if len({spec.task_id for spec in specs}) != len(specs):
        raise AssertionError("The combined task plan contains duplicate task IDs.")
    return specs


def _run_paths(
    identity: ExecutionIdentity,
    *,
    project_root: Path,
    queue_path: str | Path,
    runs_root: str | Path,
    status_path: str | Path,
) -> RunPaths:
    queue = _resolve(project_root, queue_path)
    root = _resolve(project_root, runs_root)
    run_directory = root / identity.run_id
    return RunPaths(
        project_root=project_root,
        queue_path=queue,
        runs_root=root,
        run_directory=run_directory,
        fragments_directory=run_directory / "outer_fragments",
        status_path=_resolve(project_root, status_path),
        run_manifest_path=run_directory / "run_manifest.json",
        selections_path=run_directory / "outer_selections.json",
    )


def _run_manifest(
    context: ModelRunContext,
    task_plan: TaskPlan,
    identity: ExecutionIdentity,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": GROUPED_MODEL_RUN_ALGORITHM_VERSION,
        "run_id": identity.run_id,
        "context_run_id": identity.context_run_id,
        "task_plan_sha256": identity.task_plan_sha256,
        "runtime_fingerprint_sha256": identity.runtime_fingerprint_sha256,
        "runtime_fingerprint": identity.runtime_fingerprint,
        "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
        "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
        "model_selection_commit_sha256": context.model_selection_commit_sha256,
        "portable_relocation_commit_sha256": (
            context.portable_relocation_commit_sha256
        ),
        "selection_config_sha256": context.model_selection.semantic_sha256,
        "inner_task_count": len(task_plan.inner_tasks),
        "outer_task_count": len(task_plan.outer_tasks),
        "total_task_count": len(task_plan.inner_tasks) + len(task_plan.outer_tasks),
        "final_test_year": context.model_selection.final_test_year,
        "final_test_unlocked": context.model_selection.unlock_final_test,
        "scientific_contract": {
            "inner_before_outer": True,
            "outer_selection_unit": "split_family_x_outer_fold_x_model",
            "selection_metric": "equal_date_weighted_mae_c",
            "preprocessing_scope": "fit_only_within_each_training_fold",
            "random_row_split": False,
            "locked_final_test_used": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _validate_existing_manifest(path: Path, expected: Mapping[str, Any]) -> None:
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GroupedModelRunError("Existing grouped-run manifest is unreadable.") from error
    if not isinstance(observed, dict):
        raise GroupedModelRunError("Existing grouped-run manifest is not a JSON object.")
    recorded = observed.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(observed) != recorded:
        raise GroupedModelRunError("Existing grouped-run manifest commit is invalid.")
    comparable = dict(expected)
    comparable.pop("commit_sha256", None)
    if observed != comparable:
        raise GroupedModelRunError("Existing grouped-run manifest drifted from this run.")


def prepare_grouped_run(
    *,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    initial_desired_state: str = "running",
    enforce_production_counts: bool = True,
) -> PreparedRun:
    """Authenticate frozen inputs and idempotently initialize the exact task plan."""

    root = _project_root()
    assert_grouped_execution_authorized(root)
    context = load_model_run_context(**_context_loader_kwargs(root))
    task_plan = build_task_plan(context.fold_definitions, context.model_selection)
    if enforce_production_counts and (
        len(task_plan.inner_tasks) != EXPECTED_INNER_TASK_COUNT
        or len(task_plan.outer_tasks) != EXPECTED_OUTER_TASK_COUNT
    ):
        raise GroupedModelRunError("Production grouped task cardinality is not frozen.")
    identity = build_execution_identity(context, task_plan, project_root=root)
    paths = _run_paths(
        identity,
        project_root=root,
        queue_path=queue_path,
        runs_root=runs_root,
        status_path=status_path,
    )
    paths.run_directory.mkdir(parents=True, exist_ok=True)
    paths.fragments_directory.mkdir(parents=True, exist_ok=True)
    manifest = _run_manifest(context, task_plan, identity)
    if paths.run_manifest_path.exists():
        _validate_existing_manifest(paths.run_manifest_path, manifest)
    else:
        atomic_json(manifest, paths.run_manifest_path)
    queue = ModelRunQueue(paths.queue_path)
    queue.initialize_run(
        identity.run_id,
        queue_task_specs(task_plan),
        desired_state=initial_desired_state,
    )
    return PreparedRun(
        context=context,
        task_plan=task_plan,
        identity=identity,
        paths=paths,
        queue=queue,
    )


def determine_phase(counts_by_kind: Mapping[str, Mapping[str, int]]) -> str:
    """Return the only legal next phase from queue counts.

    Quarantined tasks do not stop unrelated work in the same phase.  They block
    promotion only after all retryable work in that phase has drained.
    """

    expected_kinds = {"inner_fit", "outer_refit"}
    if set(counts_by_kind) != expected_kinds:
        raise GroupedModelRunError("Queue kinds disagree with the grouped task plan.")
    inner = counts_by_kind["inner_fit"]
    outer = counts_by_kind["outer_refit"]
    for label, counts in (("inner", inner), ("outer", outer)):
        required = {"pending", "running", "complete", "quarantined", "total"}
        if set(counts) != required:
            raise GroupedModelRunError(f"{label} queue counts have an invalid schema.")
        if sum(int(counts[name]) for name in required - {"total"}) != int(
            counts["total"]
        ):
            raise GroupedModelRunError(f"{label} queue counts do not add to total.")
    if int(inner["pending"]) + int(inner["running"]) > 0:
        return "inner_cv"
    if int(inner["quarantined"]) > 0:
        return "blocked_inner"
    if int(inner["complete"]) != int(inner["total"]):
        raise GroupedModelRunError("Inner phase reached an impossible terminal state.")
    if int(outer["pending"]) + int(outer["running"]) > 0:
        return "outer_refit"
    if int(outer["quarantined"]) > 0:
        return "blocked_outer"
    if int(outer["complete"]) != int(outer["total"]):
        raise GroupedModelRunError("Outer phase reached an impossible terminal state.")
    return "ready_to_compile"


def _selection_to_dict(selection: CandidateSelection) -> dict[str, Any]:
    candidate = selection.selected_candidate
    return {
        "model_id": selection.model_id,
        "selected_candidate_id": candidate.candidate_id,
        "selected_complexity_rank": candidate.complexity_rank,
        "selected_parameters": candidate.factory_parameters(),
        "ranking": [
            {
                "candidate_id": score.candidate_id,
                "mean_date_mae_c": score.mean_date_mae_c,
                "independent_validation_date_count": score.independent_validation_date_count,
                "complexity_rank": score.complexity_rank,
            }
            for score in selection.ranking
        ],
        "tied_candidate_ids": list(selection.tied_candidate_ids),
        "validation_years": list(selection.validation_years),
        "independent_validation_date_count": (
            selection.independent_validation_date_count
        ),
        "selection_rule": selection.selection_rule,
    }


def _selection_key(task: InnerFitTask | OuterFitTask) -> tuple[str, str, str]:
    return task.family, task.fold_id, task.model_id


def _validate_selection_manifest(
    payload: Mapping[str, Any],
    *,
    prepared: PreparedRun,
) -> dict[str, str]:
    working = dict(payload)
    recorded_commit = working.pop("commit_sha256", None)
    if not isinstance(recorded_commit, str) or canonical_sha256(working) != recorded_commit:
        raise GroupedModelRunError("Outer-selection manifest commit is invalid.")
    if (
        working.get("schema_version") != 1
        or working.get("run_id") != prepared.identity.run_id
        or working.get("task_plan_sha256") != prepared.identity.task_plan_sha256
        or working.get("selection_config_sha256")
        != prepared.context.model_selection.semantic_sha256
        or working.get("inner_result_count") != len(prepared.task_plan.inner_tasks)
        or working.get("selection_count") != len(prepared.task_plan.outer_tasks)
        or not isinstance(working.get("selections"), list)
    ):
        raise GroupedModelRunError("Outer-selection manifest identity/counts are invalid.")
    expected = {task.task_id: task for task in prepared.task_plan.outer_tasks}
    result: dict[str, str] = {}
    for entry in working["selections"]:
        if not isinstance(entry, dict):
            raise GroupedModelRunError("Outer-selection entry is not an object.")
        task_id = entry.get("outer_task_id")
        candidate_id = entry.get("selected_candidate_id")
        if task_id not in expected or not isinstance(candidate_id, str):
            raise GroupedModelRunError("Outer-selection task/candidate identity is invalid.")
        task = expected[task_id]
        if (
            entry.get("family") != task.family
            or entry.get("fold_id") != task.fold_id
            or entry.get("model_id") != task.model_id
        ):
            raise GroupedModelRunError("Outer-selection fold identity drifted.")
        prepared.context.model_selection.candidate(task.model_id, candidate_id)
        if task_id in result:
            raise GroupedModelRunError("Outer-selection task is duplicated.")
        result[task_id] = candidate_id
    if set(result) != set(expected):
        raise GroupedModelRunError("Outer selections do not cover the exact outer task plan.")
    return result


def materialize_outer_selections(prepared: PreparedRun) -> dict[str, str]:
    """Create or authenticate all fold-local selections after inner completion."""

    path = prepared.paths.selections_path
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GroupedModelRunError("Outer-selection manifest is unreadable.") from error
        if not isinstance(payload, dict):
            raise GroupedModelRunError("Outer-selection manifest must be an object.")
        return _validate_selection_manifest(payload, prepared=prepared)

    by_task_id = {task.task_id: task for task in prepared.task_plan.inner_tasks}
    grouped: dict[tuple[str, str, str], list[InnerFitResult]] = defaultdict(list)
    completed = prepared.queue.list_tasks(
        prepared.identity.run_id, statuses=("complete",)
    )
    inner_records = [record for record in completed if record.kind == "inner_fit"]
    if len(inner_records) != len(by_task_id):
        raise GroupedModelRunError("Cannot select candidates before every inner task completes.")
    for record in inner_records:
        expected_task = by_task_id.get(record.task_id)
        if expected_task is None or record.payload != expected_task.to_dict():
            raise GroupedModelRunError("Completed inner task payload drifted from its plan.")
        if not isinstance(record.result, dict) or set(record.result) != {
            "schema_version",
            "kind",
            "model_id",
            "duration_seconds",
            "inner_result",
        }:
            raise GroupedModelRunError("Completed inner task result has an invalid schema.")
        if (
            record.result["schema_version"] != RESULT_SCHEMA_VERSION
            or record.result["kind"] != "inner_fit"
            or record.result["model_id"] != expected_task.model_id
            or not isinstance(record.result["inner_result"], dict)
        ):
            raise GroupedModelRunError("Completed inner task result identity is invalid.")
        result = InnerFitResult.from_dict(record.result["inner_result"])
        if result.task != expected_task or result.task.task_id != record.task_id:
            raise GroupedModelRunError("Inner result task does not match its queue record.")
        grouped[_selection_key(expected_task)].append(result)

    entries: list[dict[str, Any]] = []
    for outer_task in prepared.task_plan.outer_tasks:
        selection = select_outer_candidate(
            grouped[_selection_key(outer_task)], prepared.context.model_selection
        )
        selection_payload = _selection_to_dict(selection)
        entries.append(
            {
                "outer_task_id": outer_task.task_id,
                "family": outer_task.family,
                "fold_id": outer_task.fold_id,
                "model_id": outer_task.model_id,
                **selection_payload,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": GROUPED_MODEL_RUN_ALGORITHM_VERSION,
        "run_id": prepared.identity.run_id,
        "task_plan_sha256": prepared.identity.task_plan_sha256,
        "selection_config_sha256": prepared.context.model_selection.semantic_sha256,
        "inner_result_count": len(inner_records),
        "selection_count": len(entries),
        "selections": entries,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, path)
    return _validate_selection_manifest(payload, prepared=prepared)


def _read_selection_payload(prepared: PreparedRun) -> dict[str, Any]:
    try:
        payload = json.loads(prepared.paths.selections_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GroupedModelRunError("Outer-selection manifest is unreadable.") from error
    if not isinstance(payload, dict):
        raise GroupedModelRunError("Outer-selection manifest must be an object.")
    _validate_selection_manifest(payload, prepared=prepared)
    return payload


def _selection_objects(prepared: PreparedRun) -> dict[str, CandidateSelection]:
    """Rehydrate the committed selection evidence for final fragment auditing."""

    payload = _read_selection_payload(prepared)
    outer_by_id = {task.task_id: task for task in prepared.task_plan.outer_tasks}
    result: dict[str, CandidateSelection] = {}
    for entry in payload["selections"]:
        task = outer_by_id[entry["outer_task_id"]]
        ranking_payload = entry.get("ranking")
        tied_payload = entry.get("tied_candidate_ids")
        years_payload = entry.get("validation_years")
        if (
            not isinstance(ranking_payload, list)
            or not isinstance(tied_payload, list)
            or not isinstance(years_payload, list)
        ):
            raise GroupedModelRunError("Selection ranking/tie/year evidence is invalid.")
        try:
            selected_candidate = prepared.context.model_selection.candidate(
                task.model_id, str(entry["selected_candidate_id"])
            )
            if (
                int(entry["selected_complexity_rank"])
                != selected_candidate.complexity_rank
                or entry["selected_parameters"]
                != selected_candidate.factory_parameters()
            ):
                raise GroupedModelRunError(
                    "Selected-candidate parameters drifted from the frozen grid."
                )
            ranking = tuple(
                CandidateScore(
                    candidate_id=str(item["candidate_id"]),
                    mean_date_mae_c=float(item["mean_date_mae_c"]),
                    independent_validation_date_count=int(
                        item["independent_validation_date_count"]
                    ),
                    complexity_rank=int(item["complexity_rank"]),
                )
                for item in ranking_payload
            )
            selection = CandidateSelection(
                model_id=task.model_id,
                selected_candidate=selected_candidate,
                ranking=ranking,
                tied_candidate_ids=tuple(str(value) for value in tied_payload),
                validation_years=tuple(int(value) for value in years_payload),
                independent_validation_date_count=int(
                    entry["independent_validation_date_count"]
                ),
                selection_rule=str(entry["selection_rule"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GroupedModelRunError(
                "Selection evidence cannot be rehydrated exactly."
            ) from error
        result[task.task_id] = selection
    if set(result) != set(outer_by_id):
        raise GroupedModelRunError("Selection objects do not cover every outer task.")
    return result


def outer_fragment_records(prepared: PreparedRun) -> tuple[OuterFragmentRecord, ...]:
    """Authenticate queue result metadata before the compiler reads any fragment."""

    expected = {task.task_id: task for task in prepared.task_plan.outer_tasks}
    selections = _selection_objects(prepared)
    completed = prepared.queue.list_tasks(
        prepared.identity.run_id, statuses=("complete",)
    )
    records = [record for record in completed if record.kind == "outer_refit"]
    if len(records) != len(expected):
        raise GroupedModelRunError("Final compilation requires every outer task result.")
    fragments: list[OuterFragmentRecord] = []
    for record in records:
        task = expected.get(record.task_id)
        if task is None or record.payload != task.to_dict():
            raise GroupedModelRunError("Completed outer task payload drifted from its plan.")
        result = record.result
        required_result_keys = {
            "schema_version",
            "kind",
            "model_id",
            "selected_candidate_id",
            "duration_seconds",
            "fragment",
        }
        if not isinstance(result, dict) or set(result) != required_result_keys:
            raise GroupedModelRunError("Completed outer result has an invalid schema.")
        selection = selections[task.task_id]
        if (
            result["schema_version"] != RESULT_SCHEMA_VERSION
            or result["kind"] != "outer_refit"
            or result["model_id"] != task.model_id
            or result["selected_candidate_id"]
            != selection.selected_candidate.candidate_id
            or not isinstance(result["fragment"], dict)
        ):
            raise GroupedModelRunError("Completed outer result identity is invalid.")
        fragment = result["fragment"]
        required_fragment_keys = {
            "path",
            "path_base",
            "sha256",
            "bytes",
            "rows",
            "schema_sha256",
            "semantic_sha256",
        }
        if set(fragment) != required_fragment_keys:
            raise GroupedModelRunError("Outer fragment record has an invalid schema.")
        expected_relative_path = (
            Path("outer_fragments") / f"{task.task_id}.parquet"
        ).as_posix()
        if (
            fragment["path_base"] != "run_directory"
            or fragment["path"] != expected_relative_path
        ):
            raise GroupedModelRunError(
                "Outer fragment path is not the frozen run-relative destination."
            )
        try:
            fragments.append(
                OuterFragmentRecord(
                    task=task,
                    selection=selection,
                    path=str(fragment["path"]),
                    sha256=str(fragment["sha256"]),
                    bytes=int(fragment["bytes"]),
                    rows=int(fragment["rows"]),
                    schema_sha256=str(fragment["schema_sha256"]),
                    path_base=str(fragment["path_base"]),
                )
            )
        except (TypeError, ValueError) as error:
            raise GroupedModelRunError("Outer fragment record types are invalid.") from error
    return tuple(fragments)


def compile_grouped_run(
    prepared: PreparedRun,
    *,
    output_directory: str | Path = DEFAULT_COMPILE_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Compile exact OOF predictions and audited metrics after all fits complete."""

    if determine_phase(prepared.queue.counts_by_kind(prepared.identity.run_id)) != (
        "ready_to_compile"
    ):
        raise GroupedModelRunError("Compilation is forbidden before every fit completes.")
    fragments = outer_fragment_records(prepared)
    return compile_model_run_outputs(
        prepared.context,
        fragments,
        execution_run_id=prepared.identity.run_id,
        task_plan_sha256=prepared.identity.task_plan_sha256,
        output_directory=_resolve(prepared.paths.project_root, output_directory),
        fragment_root=prepared.paths.run_directory,
    )


class StatusReporter:
    """Throttled atomic status writer used by both the dashboard and CLI."""

    def __init__(self, path: Path, *, run_id: str, workers: int) -> None:
        self.path = path
        self.run_id = run_id
        self.workers = workers
        self.events: list[dict[str, str]] = []
        self.duration_stats: dict[str, dict[str, float | int]] = {}
        self._last_write = 0.0
        if path.is_file():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            if isinstance(previous, dict) and previous.get("run_id") == run_id:
                if isinstance(previous.get("events"), list):
                    self.events = [
                        event
                        for event in previous["events"][-40:]
                        if isinstance(event, dict)
                        and isinstance(event.get("at"), str)
                        and isinstance(event.get("message"), str)
                    ]
                if isinstance(previous.get("duration_stats"), dict):
                    self.duration_stats = previous["duration_stats"]

    def event(self, message: str) -> None:
        self.events.append({"at": _utc_now(), "message": str(message)[:300]})
        self.events = self.events[-40:]

    def record_duration(self, *, kind: str, model_id: str, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            return
        key = f"{kind}:{model_id}"
        observed = self.duration_stats.get(key, {"count": 0, "total_seconds": 0.0})
        observed["count"] = int(observed.get("count", 0)) + 1
        observed["total_seconds"] = float(observed.get("total_seconds", 0.0)) + seconds
        observed["mean_seconds"] = float(observed["total_seconds"]) / int(
            observed["count"]
        )
        self.duration_stats[key] = observed

    def _eta_seconds(self, counts_by_kind: Mapping[str, Mapping[str, int]]) -> float | None:
        estimates = []
        for kind in ("inner_fit", "outer_refit"):
            remaining = int(counts_by_kind[kind]["pending"]) + int(
                counts_by_kind[kind]["running"]
            )
            means = [
                float(value["mean_seconds"])
                for key, value in self.duration_stats.items()
                if key.startswith(f"{kind}:") and "mean_seconds" in value
            ]
            if remaining and means:
                estimates.append(remaining * sum(means) / len(means))
            elif remaining:
                return None
        return sum(estimates) / self.workers if estimates else 0.0

    def write(
        self,
        *,
        state: str,
        phase: str,
        desired_state: str,
        counts: Mapping[str, int],
        counts_by_kind: Mapping[str, Mapping[str, int]],
        active: Iterable[ActiveClaim],
        error: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < 1.0:
            return
        active_rows = []
        for claim in active:
            payload = claim.record.payload
            active_rows.append(
                {
                    "task_id": claim.record.task_id,
                    "kind": claim.record.kind,
                    "model_id": payload.get("model_id") if isinstance(payload, dict) else None,
                    "worker": claim.owner,
                    "attempt": claim.record.attempt,
                    "elapsed_seconds": max(0.0, now - claim.started_monotonic),
                }
            )
        payload = {
            "schema_version": 1,
            "updated_at_utc": _utc_now(),
            "run_id": self.run_id,
            "state": state,
            "phase": phase,
            "desired_state": desired_state,
            "total": int(counts["total"]),
            "completed": int(counts["complete"]),
            "active": int(counts["running"]),
            "pending": int(counts["pending"]),
            "quarantined": int(counts["quarantined"]),
            "workers": self.workers,
            "eta_seconds": self._eta_seconds(counts_by_kind),
            "counts": dict(counts),
            "counts_by_kind": {
                kind: dict(kind_counts) for kind, kind_counts in counts_by_kind.items()
            },
            "active_tasks": active_rows,
            "duration_stats": self.duration_stats,
            "events": self.events,
            "error": None if error is None else dict(error),
        }
        atomic_json(payload, self.path)
        self._last_write = now


_WORKER_CONTEXT: ModelRunContext | None = None
_WORKER_RUN_ID: str | None = None
_THREAD_LIMITER: Any = None


def _limit_worker_threads() -> None:
    global _THREAD_LIMITER
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    try:
        from threadpoolctl import threadpool_limits

        _THREAD_LIMITER = threadpool_limits(limits=1)
    except ImportError:
        _THREAD_LIMITER = None


def _initialize_worker(expected_context_run_id: str) -> None:
    global _WORKER_CONTEXT, _WORKER_RUN_ID
    _limit_worker_threads()
    root = _project_root()
    context = load_model_run_context(**_context_loader_kwargs(root))
    if context.run_id != expected_context_run_id:
        raise GroupedModelRunError("Worker context identity differs from coordinator.")
    _WORKER_CONTEXT = context
    _WORKER_RUN_ID = expected_context_run_id


def _worker_context() -> ModelRunContext:
    if _WORKER_CONTEXT is None or _WORKER_RUN_ID is None:
        raise GroupedModelRunError("Worker was not initialized with a frozen context.")
    return _WORKER_CONTEXT


def _execute_worker_task(
    kind: str,
    payload: Mapping[str, Any],
    selected_candidate_id: str | None,
    fragments_directory: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    context = _worker_context()
    if kind == "inner_fit":
        task = InnerFitTask.from_dict(payload)
        result = run_inner_fit(
            task,
            row_groups=context.row_groups,
            model_frame=context.features,
            target=context.target,
            registry=context.registry,
            model_selection_config=context.model_selection,
            spatial_buffer_geoids=context.spatial_buffer_geoids,
        )
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "kind": kind,
            "model_id": task.model_id,
            "duration_seconds": time.perf_counter() - started,
            "inner_result": result.to_dict(),
        }
    if kind != "outer_refit":
        raise GroupedModelRunError(f"Unknown worker task kind {kind!r}.")
    task = OuterFitTask.from_dict(payload)
    if not isinstance(selected_candidate_id, str):
        raise GroupedModelRunError("Outer task lacks its frozen selected candidate.")
    selected = context.model_selection.candidate(task.model_id, selected_candidate_id)
    predictions = run_outer_fit(
        task,
        selected,
        row_groups=context.row_groups,
        model_frame=context.features,
        target=context.target,
        registry=context.registry,
        model_selection_config=context.model_selection,
        spatial_buffer_geoids=context.spatial_buffer_geoids,
    )
    destination = Path(fragments_directory).resolve() / f"{task.task_id}.parquet"
    atomic_parquet(predictions, destination)
    record = parquet_file_record(destination, predictions)
    record.update(
        {
            "path": destination.relative_to(destination.parent.parent).as_posix(),
            "path_base": "run_directory",
            "semantic_sha256": canonical_frame_sha256(
                predictions,
                sort_by=["target_date", "tract_geoid"],
            ),
        }
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "kind": kind,
        "model_id": task.model_id,
        "selected_candidate_id": selected_candidate_id,
        "duration_seconds": time.perf_counter() - started,
        "fragment": record,
    }


def _is_scientific_failure(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            AssertionError,
            ModelSelectionAuditError,
            ModelTaskAuditError,
            ModelingContractError,
            PermissionError,
            TypeError,
            ValueError,
        ),
    )


def _safe_error_record(error: BaseException, *, permanent: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "error_type": type(error).__name__,
        "error_module": type(error).__module__,
        "permanent": permanent,
        "message_omitted": True,
        "at_utc": _utc_now(),
    }


def _new_executor(workers: int, context_run_id: str) -> ProcessPoolExecutor:
    _limit_worker_threads()
    return ProcessPoolExecutor(
        max_workers=workers,
        initializer=_initialize_worker,
        initargs=(context_run_id,),
    )


def _record_result_duration(reporter: StatusReporter, result: Mapping[str, Any]) -> None:
    try:
        duration = float(result["duration_seconds"])
        kind = str(result["kind"])
        model_id = str(result["model_id"])
    except (KeyError, TypeError, ValueError):
        return
    reporter.record_duration(kind=kind, model_id=model_id, seconds=duration)


def _finish_future(
    future: Future[dict[str, Any]],
    claim: ActiveClaim,
    *,
    prepared: PreparedRun,
    reporter: StatusReporter,
    max_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> bool:
    """Persist one future outcome; return True when the process pool broke."""

    queue = prepared.queue
    run_id = prepared.identity.run_id
    try:
        result = future.result()
    except BaseException as error:  # noqa: BLE001 - classification is explicit below
        permanent = _is_scientific_failure(error)
        error_type = type(error).__name__
        evidence = _safe_error_record(error, permanent=permanent)
        try:
            if permanent or claim.record.attempt >= max_attempts:
                queue.quarantine(
                    run_id,
                    claim.record.task_id,
                    owner=claim.owner,
                    generation=claim.record.claim_generation,
                    error_type=error_type,
                    result=evidence,
                )
                reporter.event(
                    f"Task quarantined after attempt {claim.record.attempt}: {error_type}."
                )
            else:
                queue.retry(
                    run_id,
                    claim.record.task_id,
                    owner=claim.owner,
                    generation=claim.record.claim_generation,
                    error_type=error_type,
                    base_delay_seconds=retry_base_seconds,
                    max_delay_seconds=retry_max_seconds,
                )
                reporter.event(
                    f"Transient task failure will retry automatically: {error_type}."
                )
        except LeaseLostError:
            reporter.event("A stale worker result was fenced and ignored.")
        return type(error).__name__ == "BrokenProcessPool"
    if not isinstance(result, dict):
        raise GroupedModelRunError("Worker returned a non-object result.")
    try:
        queue.complete(
            run_id,
            claim.record.task_id,
            owner=claim.owner,
            generation=claim.record.claim_generation,
            result=result,
        )
    except LeaseLostError:
        reporter.event("A completed stale worker was fenced and ignored.")
        return False
    _record_result_duration(reporter, result)
    return False


def _heartbeat_active(
    prepared: PreparedRun,
    active: Iterable[ActiveClaim],
    *,
    lease_seconds: float,
    reporter: StatusReporter,
) -> None:
    for claim in active:
        try:
            prepared.queue.heartbeat(
                prepared.identity.run_id,
                claim.record.task_id,
                owner=claim.owner,
                generation=claim.record.claim_generation,
                lease_seconds=lease_seconds,
            )
        except LeaseLostError:
            reporter.event("An active task lease was already reclaimed; fencing remains active.")


def run_grouped_coordinator(
    *,
    workers: int = 4,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    resume: bool = False,
    prepare_only: bool = False,
    max_tasks: int | None = None,
    lease_seconds: float = 600.0,
    heartbeat_seconds: float = 30.0,
    max_attempts: int = 3,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 300.0,
) -> dict[str, Any]:
    """Run or resume the nested grouped evaluation until paused or terminal."""

    workers = _positive_integer(workers, name="workers", maximum=8)
    max_attempts = _positive_integer(max_attempts, name="max_attempts", maximum=20)
    lease_seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    heartbeat_seconds = _positive_seconds(heartbeat_seconds, name="heartbeat_seconds")
    retry_base_seconds = _positive_seconds(
        retry_base_seconds, name="retry_base_seconds"
    )
    retry_max_seconds = _positive_seconds(retry_max_seconds, name="retry_max_seconds")
    if heartbeat_seconds >= lease_seconds / 2:
        raise ValueError("heartbeat_seconds must be below half the task lease.")
    if retry_max_seconds < retry_base_seconds:
        raise ValueError("retry_max_seconds cannot be below retry_base_seconds.")
    if max_tasks is not None:
        max_tasks = _positive_integer(max_tasks, name="max_tasks")

    prepared = prepare_grouped_run(
        queue_path=queue_path,
        runs_root=runs_root,
        status_path=status_path,
    )
    queue = prepared.queue
    run_id = prepared.identity.run_id
    if resume:
        queue.set_desired_state(run_id, "running")
    reporter = StatusReporter(prepared.paths.status_path, run_id=run_id, workers=workers)
    reporter.event("Grouped model coordinator initialized.")
    counts = queue.counts(run_id)
    by_kind = queue.counts_by_kind(run_id)
    desired = queue.get_desired_state(run_id)
    if prepare_only:
        reporter.write(
            state="ready" if desired == "running" else "paused",
            phase=determine_phase(by_kind),
            desired_state=desired,
            counts=counts,
            counts_by_kind=by_kind,
            active=(),
            force=True,
        )
        return json.loads(prepared.paths.status_path.read_text(encoding="utf-8"))

    executor: ProcessPoolExecutor | None = None
    active: dict[Future[dict[str, Any]], ActiveClaim] = {}
    selection_by_outer_task: dict[str, str] | None = None
    owner_prefix = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    handled_in_invocation = 0
    last_heartbeat = time.monotonic()
    terminal_state = "paused"
    terminal_phase = determine_phase(by_kind)
    terminal_error: dict[str, Any] | None = None

    try:
        while True:
            desired = queue.get_desired_state(run_id)
            counts = queue.counts(run_id)
            by_kind = queue.counts_by_kind(run_id)
            phase = determine_phase(by_kind)
            now = time.monotonic()
            if active and now - last_heartbeat >= heartbeat_seconds:
                _heartbeat_active(
                    prepared,
                    active.values(),
                    lease_seconds=lease_seconds,
                    reporter=reporter,
                )
                last_heartbeat = now

            done = {future for future in active if future.done()}
            if not done and active:
                done, _ = wait(tuple(active), timeout=0.25, return_when=FIRST_COMPLETED)
            pool_broken = False
            for future in done:
                claim = active.pop(future)
                pool_broken |= _finish_future(
                    future,
                    claim,
                    prepared=prepared,
                    reporter=reporter,
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                    retry_max_seconds=retry_max_seconds,
                )
                handled_in_invocation += 1

            if pool_broken and executor is not None:
                reporter.event("Worker pool failed; coordinator is recreating it automatically.")
                for future, claim in list(active.items()):
                    future.cancel()
                    try:
                        queue.retry(
                            run_id,
                            claim.record.task_id,
                            owner=claim.owner,
                            generation=claim.record.claim_generation,
                            error_type="BrokenProcessPool",
                            base_delay_seconds=retry_base_seconds,
                            max_delay_seconds=retry_max_seconds,
                        )
                    except LeaseLostError:
                        pass
                    active.pop(future, None)
                executor.shutdown(wait=False, cancel_futures=True)
                executor = None

            if max_tasks is not None and handled_in_invocation >= max_tasks:
                queue.set_desired_state(run_id, "paused")
                desired = "paused"
                reporter.event("Invocation task limit reached; checkpoint paused safely.")

            counts = queue.counts(run_id)
            by_kind = queue.counts_by_kind(run_id)
            phase = determine_phase(by_kind)
            visible_state = "pausing" if desired == "paused" and active else "running"
            reporter.write(
                state=visible_state,
                phase=phase,
                desired_state=desired,
                counts=counts,
                counts_by_kind=by_kind,
                active=active.values(),
            )

            if desired == "paused":
                if active:
                    continue
                terminal_state = "paused"
                terminal_phase = phase
                break
            if phase.startswith("blocked_"):
                if active:
                    continue
                terminal_state = "blocked"
                terminal_phase = phase
                reporter.event("All retryable work drained; quarantined tasks block promotion.")
                break
            if phase == "ready_to_compile":
                if active:
                    continue
                reporter.event("All model fits completed; compiling exact OOF outputs.")
                reporter.write(
                    state="compiling",
                    phase="compile",
                    desired_state=desired,
                    counts=counts,
                    counts_by_kind=by_kind,
                    active=(),
                    force=True,
                )
                compile_grouped_run(prepared)
                terminal_state = "complete"
                terminal_phase = "complete"
                reporter.event("OOF predictions and audited metrics compiled successfully.")
                break

            if phase == "outer_refit" and selection_by_outer_task is None:
                if active:
                    continue
                reporter.event("Inner CV complete; freezing fold-local candidate selections.")
                selection_by_outer_task = materialize_outer_selections(prepared)

            if executor is None:
                executor = _new_executor(workers, prepared.context.run_id)
            kind = "inner_fit" if phase == "inner_cv" else "outer_refit"
            while len(active) < workers and desired == "running":
                owner = f"{owner_prefix}-{len(active) + 1}"
                record = queue.claim_next(
                    run_id,
                    owner=owner,
                    lease_seconds=lease_seconds,
                    kinds=(kind,),
                )
                if record is None:
                    break
                selected_id = (
                    None
                    if kind == "inner_fit"
                    else (selection_by_outer_task or {}).get(record.task_id)
                )
                try:
                    future = executor.submit(
                        _execute_worker_task,
                        record.kind,
                        record.payload,
                        selected_id,
                        str(prepared.paths.fragments_directory),
                    )
                except BaseException as error:  # noqa: BLE001 - retry is fenced
                    queue.retry(
                        run_id,
                        record.task_id,
                        owner=owner,
                        generation=record.claim_generation,
                        error_type=type(error).__name__,
                        base_delay_seconds=retry_base_seconds,
                        max_delay_seconds=retry_max_seconds,
                    )
                    reporter.event("Worker submission failed; task will retry automatically.")
                    executor.shutdown(wait=False, cancel_futures=True)
                    executor = None
                    break
                active[future] = ActiveClaim(
                    record=record,
                    owner=owner,
                    started_monotonic=time.monotonic(),
                    submitted_at_utc=_utc_now(),
                )
            if not active:
                time.sleep(0.25)
    except KeyboardInterrupt:
        terminal_state = "restarting"
        terminal_phase = determine_phase(queue.counts_by_kind(run_id))
        reporter.event("Coordinator interrupted; durable leases/checkpoints remain available.")
        raise
    except BaseException as error:  # noqa: BLE001 - status records type only
        terminal_state = "restarting"
        terminal_phase = "coordinator_error"
        terminal_error = _safe_error_record(error, permanent=False)
        reporter.event(f"Coordinator exited unexpectedly: {type(error).__name__}.")
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        counts = queue.counts(run_id)
        by_kind = queue.counts_by_kind(run_id)
        desired = queue.get_desired_state(run_id)
        reporter.write(
            state=terminal_state,
            phase=terminal_phase,
            desired_state=desired,
            counts=counts,
            counts_by_kind=by_kind,
            active=(),
            error=terminal_error,
            force=True,
        )
    return json.loads(prepared.paths.status_path.read_text(encoding="utf-8"))


def calibrate_grouped_runtime(
    *,
    output_path: str | Path = Path("data/interim/model_runs/calibration.json"),
) -> dict[str, Any]:
    """Time one conservative inner fit per model without mutating the task queue."""

    _limit_worker_threads()
    root = _project_root()
    context = load_model_run_context(**_context_loader_kwargs(root))
    plan = build_task_plan(context.fold_definitions, context.model_selection)
    samples: list[InnerFitTask] = []
    for model_id in ("B0", "B1", "B2", "M1", "M2"):
        eligible = [task for task in plan.inner_tasks if task.model_id == model_id]
        # Largest outer train and highest declared candidate rank form a conservative sample.
        samples.append(
            max(
                eligible,
                key=lambda task: (
                    task.expected_outer_train_row_count,
                    context.model_selection.candidate(
                        task.model_id, task.candidate_id
                    ).complexity_rank,
                    -task.validation_year,
                ),
            )
        )
    records = []
    counts_by_model = {
        model_id: sum(task.model_id == model_id for task in plan.inner_tasks)
        for model_id in ("B0", "B1", "B2", "M1", "M2")
    }
    for task in samples:
        started = time.perf_counter()
        result = run_inner_fit(
            task,
            row_groups=context.row_groups,
            model_frame=context.features,
            target=context.target,
            registry=context.registry,
            model_selection_config=context.model_selection,
            spatial_buffer_geoids=context.spatial_buffer_geoids,
        )
        seconds = time.perf_counter() - started
        records.append(
            {
                "model_id": task.model_id,
                "candidate_id": task.candidate_id,
                "task_id": task.task_id,
                "duration_seconds": seconds,
                "inner_validation_date_count": result.audit.inner_validation_date_count,
                "inner_training_row_count": result.audit.inner_train_row_count,
                "planned_inner_task_count": counts_by_model[task.model_id],
                "single_worker_projected_inner_seconds": (
                    seconds * counts_by_model[task.model_id]
                ),
            }
        )
    projected = sum(float(record["single_worker_projected_inner_seconds"]) for record in records)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": GROUPED_MODEL_RUN_ALGORITHM_VERSION,
        "calibrated_at_utc": _utc_now(),
        "context_run_id": context.run_id,
        "sample_count": len(records),
        "samples": records,
        "projected_inner_seconds_one_worker": projected,
        "projection_is_rough": True,
        "queue_mutated": False,
        "final_test_year_used": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    destination = _resolve(_project_root(), output_path)
    atomic_json(payload, destination)
    return payload


def status_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a concise JSON-safe CLI summary from a full dashboard status."""

    return {
        "run_id": payload.get("run_id"),
        "state": payload.get("state"),
        "phase": payload.get("phase"),
        "desired_state": payload.get("desired_state"),
        "completed": payload.get("completed"),
        "total": payload.get("total"),
        "active": payload.get("active"),
        "quarantined": payload.get("quarantined"),
        "eta_seconds": payload.get("eta_seconds"),
    }
