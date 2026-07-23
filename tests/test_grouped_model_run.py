from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.grouped_model_run as grouped_run
from la_heat.grouped_model_run import (
    ExecutionIdentity,
    GroupedModelRunError,
    StatusReporter,
    build_execution_identity,
    determine_phase,
    queue_task_specs,
)
from la_heat.model_run_context import ModelRunContext
from la_heat.model_task_engine import InnerFitTask, OuterFitTask, TaskPlan


def _inner_task() -> InnerFitTask:
    return InnerFitTask(
        selection_config_sha256="selection",
        family="temporal",
        fold_index=0,
        fold_id="temporal-2020",
        held_out_year=2020,
        held_out_block=None,
        model_id="B0",
        candidate_id="B0-fixed",
        validation_year=2021,
        expected_outer_train_row_count=8,
        expected_outer_test_row_count=2,
        expected_outer_purged_row_count=0,
        expected_outer_train_date_count=4,
        expected_outer_test_date_count=1,
        expected_inner_fold_count=4,
    )


def _outer_task() -> OuterFitTask:
    return OuterFitTask(
        selection_config_sha256="selection",
        family="temporal",
        fold_index=0,
        fold_id="temporal-2020",
        held_out_year=2020,
        held_out_block=None,
        model_id="B0",
        expected_outer_train_row_count=8,
        expected_outer_test_row_count=2,
        expected_outer_purged_row_count=0,
        expected_outer_train_date_count=4,
        expected_outer_test_date_count=1,
        expected_inner_fold_count=4,
    )


def _counts(
    *,
    inner: tuple[int, int, int, int],
    outer: tuple[int, int, int, int],
) -> dict[str, dict[str, int]]:
    def record(values: tuple[int, int, int, int]) -> dict[str, int]:
        pending, running, complete, quarantined = values
        return {
            "pending": pending,
            "running": running,
            "complete": complete,
            "quarantined": quarantined,
            "total": sum(values),
        }

    return {"inner_fit": record(inner), "outer_refit": record(outer)}


def test_queue_specs_are_inner_first_and_exact() -> None:
    plan = TaskPlan("selection", (_inner_task(),), (_outer_task(),))
    specs = queue_task_specs(plan)

    assert [spec.kind for spec in specs] == ["inner_fit", "outer_refit"]
    assert [spec.task_id for spec in specs] == [
        plan.inner_tasks[0].task_id,
        plan.outer_tasks[0].task_id,
    ]
    assert specs[0].payload == plan.inner_tasks[0].to_dict()
    assert specs[1].payload == plan.outer_tasks[0].to_dict()


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (_counts(inner=(1, 0, 0, 0), outer=(1, 0, 0, 0)), "inner_cv"),
        (_counts(inner=(0, 1, 0, 0), outer=(1, 0, 0, 0)), "inner_cv"),
        (_counts(inner=(0, 0, 1, 1), outer=(1, 0, 0, 0)), "blocked_inner"),
        (_counts(inner=(0, 0, 2, 0), outer=(1, 0, 0, 0)), "outer_refit"),
        (_counts(inner=(0, 0, 2, 0), outer=(0, 1, 0, 0)), "outer_refit"),
        (_counts(inner=(0, 0, 2, 0), outer=(0, 0, 1, 1)), "blocked_outer"),
        (_counts(inner=(0, 0, 2, 0), outer=(0, 0, 2, 0)), "ready_to_compile"),
    ],
)
def test_determine_phase_never_opens_outer_while_inner_is_live(
    counts: dict[str, dict[str, int]], expected: str
) -> None:
    assert determine_phase(counts) == expected


def test_determine_phase_rejects_corrupt_counts() -> None:
    counts = _counts(inner=(1, 0, 0, 0), outer=(1, 0, 0, 0))
    counts["inner_fit"]["total"] = 9
    with pytest.raises(GroupedModelRunError, match="do not add"):
        determine_phase(counts)


def test_execution_identity_changes_with_runtime_or_plan(tmp_path: Path) -> None:
    code = tmp_path / "runner.py"
    code.write_text("version = 1\n", encoding="utf-8")
    empty = pd.DataFrame()
    context = ModelRunContext(
        run_id="context",
        model_dataset_commit_sha256="model",
        split_promotion_commit_sha256="splits",
        model_selection_commit_sha256="selection-commit",
        runtime_fingerprint_sha256="context-runtime",
        dataset=empty,
        registry=empty,
        row_groups=empty,
        fold_definitions=empty,
        spatial_buffer_geoids=empty,
        features=empty,
        target=pd.Series(dtype=float),
        keys=empty,
        audit_only=empty,
        model_selection=None,  # type: ignore[arg-type]
    )
    first_plan = TaskPlan("selection", (_inner_task(),), (_outer_task(),))
    first = build_execution_identity(
        context,
        first_plan,
        project_root=tmp_path,
        runtime_paths=("runner.py",),
    )
    code.write_text("version = 2\n", encoding="utf-8")
    second = build_execution_identity(
        context,
        first_plan,
        project_root=tmp_path,
        runtime_paths=("runner.py",),
    )

    assert isinstance(first, ExecutionIdentity)
    assert first.task_plan_sha256 == second.task_plan_sha256
    assert first.runtime_fingerprint_sha256 != second.runtime_fingerprint_sha256
    assert first.run_id != second.run_id


def test_status_reporter_writes_dashboard_contract(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    reporter = StatusReporter(path, run_id="run", workers=2)
    reporter.event("initialized")
    reporter.record_duration(kind="inner_fit", model_id="B0", seconds=2.0)
    counts_by_kind = _counts(inner=(2, 0, 1, 0), outer=(2, 0, 0, 0))
    counts = {
        "pending": 4,
        "running": 0,
        "complete": 1,
        "quarantined": 0,
        "total": 5,
    }
    reporter.write(
        state="running",
        phase="inner_cv",
        desired_state="running",
        counts=counts,
        counts_by_kind=counts_by_kind,
        active=(),
        force=True,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run"
    assert payload["completed"] == 1
    assert payload["total"] == 5
    assert payload["workers"] == 2
    assert payload["phase"] == "inner_cv"
    assert payload["events"][-1]["message"] == "initialized"


def test_portable_environment_manifest_must_be_inside_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    manifest = root / "portable_relocation.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LA_HEAT_PORTABLE_RELOCATION", manifest.name)

    assert grouped_run._context_loader_kwargs(root) == {
        "portable_manifest_path": manifest.resolve(),
        "portable_root": root.resolve(),
    }

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LA_HEAT_PORTABLE_RELOCATION", str(outside))
    with pytest.raises(GroupedModelRunError, match="inside the project root"):
        grouped_run._context_loader_kwargs(root)
