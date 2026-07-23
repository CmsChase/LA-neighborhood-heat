from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.model_selection import load_model_selection_config
from la_heat.model_task_engine import (
    INNER_DATE_SCORE_COLUMNS,
    OUTER_PREDICTION_COLUMNS,
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
from la_heat.validation_splits import assign_fold_roles

ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / "configs" / "model_selection.toml"
YEARS = tuple(range(2020, 2025))


def _registry_row(name, family, *, static, start=None, end=None):
    return {
        "feature_name": name,
        "family": family,
        "role": "model",
        "units": "unitless",
        "source": "synthetic legal source",
        "static": static,
        "available_by": "2019-01-01" if static else "historical archive",
        "source_start_offset_days": start,
        "source_end_offset_days": end,
    }


def _registry() -> pd.DataFrame:
    rows = [
        {
            "feature_name": "tract_geoid",
            "family": "key",
            "role": "key",
            "units": "identifier",
            "source": "tract manifest",
            "static": True,
            "available_by": "2019-01-01",
            "source_start_offset_days": None,
            "source_end_offset_days": None,
        },
        {
            "feature_name": "target_date",
            "family": "key",
            "role": "key",
            "units": "date",
            "source": "join key",
            "static": False,
            "available_by": "prediction origin",
            "source_start_offset_days": None,
            "source_end_offset_days": None,
        },
    ]
    rows.extend(
        _registry_row(f"land_feature_{index:02d}", "land_use", static=True)
        for index in range(9)
    )
    rows.extend(
        _registry_row(f"geo_feature_{index:02d}", "geography", static=True)
        for index in range(9)
    )
    rows.extend(calendar_feature_registry_rows().to_dict("records"))
    rows.extend(
        _registry_row(
            f"weather_feature_{index:02d}",
            "weather",
            static=False,
            start=-7,
            end=-1,
        )
        for index in range(21)
    )
    rows.extend(
        _registry_row(
            f"satellite_feature_{index:02d}",
            "satellite",
            static=False,
            start=-60,
            end=-1,
        )
        for index in range(5)
    )
    rows.append(
        {
            **_registry_row("coverage_audit", "audit", static=False),
            "role": "audit_only",
        }
    )
    return pd.DataFrame(rows)


def _synthetic_inputs():
    records = []
    block_by_geoid = {"a0": "A", "a1": "A", "b0": "B", "b1": "B"}
    for offset, year in enumerate(YEARS):
        for month in (5 + offset % 3, 8 + offset % 2):
            for geoid, block in block_by_geoid.items():
                records.append(
                    {
                        "tract_geoid": geoid,
                        "target_date": pd.Timestamp(year=year, month=month, day=15),
                        "spatial_block": block,
                    }
                )
    groups = pd.DataFrame.from_records(records).sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    groups["year"] = groups["target_date"].dt.year.astype("int16")

    registry = _registry()
    names = registry.loc[registry["role"].eq("model"), "feature_name"].tolist()
    rng = np.random.default_rng(20260720)
    model_frame = pd.DataFrame(rng.normal(size=(len(groups), len(names))), columns=names)
    phase = 2 * np.pi * (
        groups["target_date"].dt.dayofyear.to_numpy() - 1
    ) / np.where(groups["target_date"].dt.is_leap_year, 366, 365)
    model_frame["calendar_doy_sin"] = np.sin(phase)
    model_frame["calendar_doy_cos"] = np.cos(phase)
    geoid_number = groups["tract_geoid"].map({"a0": 0.0, "a1": 1.0, "b0": 2.0, "b1": 3.0})
    for index in range(9):
        model_frame[f"land_feature_{index:02d}"] = geoid_number + index
        model_frame[f"geo_feature_{index:02d}"] = 0.5 * geoid_number + index
    model_frame.insert(0, "target_date", groups["target_date"])
    model_frame.insert(0, "tract_geoid", groups["tract_geoid"])
    target = pd.Series(
        31.0
        + 2.5 * np.sin(phase)
        + 0.7 * model_frame["weather_feature_00"].to_numpy()
        + 0.2 * geoid_number.to_numpy(),
        index=groups.index,
        name="target_lst_c",
    )

    buffers = pd.DataFrame(
        {
            "held_out_block": ["A", "A"],
            "tract_geoid": ["a0", "a1"],
        }
    )
    roles = assign_fold_roles(
        groups,
        family="joint",
        held_out_year=2024,
        held_out_block="A",
        buffered_geoids=frozenset({"a0", "a1"}),
    )
    train = groups.loc[roles.eq("train")]
    test = groups.loc[roles.eq("test")]
    purged = groups.loc[roles.eq("purged")]
    fold = pd.DataFrame(
        [
            {
                "family": "joint",
                "fold_index": 0,
                "fold_id": "joint_year_2024__block_A",
                "held_out_year": 2024,
                "held_out_block": "A",
                "train_row_count": len(train),
                "test_row_count": len(test),
                "purged_row_count": len(purged),
                "train_date_count": train["target_date"].nunique(),
                "test_date_count": test["target_date"].nunique(),
                "inner_cv_fold_count": train["year"].nunique(),
            }
        ]
    )
    return groups, model_frame, target, registry, buffers, fold


@pytest.fixture(scope="module")
def selection_config():
    return load_model_selection_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def synthetic_bundle(selection_config):
    groups, frame, target, registry, buffers, folds = _synthetic_inputs()
    plan = build_task_plan(folds, selection_config)
    return groups, frame, target, registry, buffers, plan


@pytest.fixture(scope="module")
def b1_inner_results(selection_config, synthetic_bundle):
    groups, frame, target, registry, buffers, plan = synthetic_bundle
    tasks = [task for task in plan.inner_tasks if task.model_id == "B1"]
    return tuple(
        run_inner_fit(
            task,
            row_groups=groups,
            model_frame=frame,
            target=target,
            registry=registry,
            model_selection_config=selection_config,
            spatial_buffer_geoids=buffers,
        )
        for task in tasks
    )


def test_frozen_production_plan_has_predeclared_fit_counts(selection_config) -> None:
    folds = pd.read_csv(ROOT / "manifests" / "validation_splits" / "fold_definitions.csv")

    plan = build_task_plan(folds, selection_config)

    assert len(plan.inner_tasks) == 55_645
    assert len(plan.outer_tasks) == 2_155
    assert len({task.task_id for task in plan.inner_tasks}) == 55_645
    assert len({task.task_id for task in plan.outer_tasks}) == 2_155


def test_plan_and_task_payloads_are_deterministic_and_json_safe(
    selection_config, synthetic_bundle
) -> None:
    *_, original = synthetic_bundle
    _, _, _, _, _, folds = _synthetic_inputs()
    shuffled = build_task_plan(folds.sample(frac=1, random_state=9), selection_config)

    assert [task.task_id for task in original.inner_tasks] == [
        task.task_id for task in shuffled.inner_tasks
    ]
    assert [task.task_id for task in original.outer_tasks] == [
        task.task_id for task in shuffled.outer_tasks
    ]
    restored = TaskPlan.from_dict(original.to_dict())
    assert restored == original
    assert InnerFitTask.from_dict(original.inner_tasks[0].to_dict()) == original.inner_tasks[0]
    assert OuterFitTask.from_dict(original.outer_tasks[0].to_dict()) == original.outer_tasks[0]


def test_inner_fit_never_reads_outer_test_or_purged_values(
    selection_config, synthetic_bundle
) -> None:
    groups, frame, target, registry, buffers, plan = synthetic_bundle
    task = next(
        item
        for item in plan.inner_tasks
        if item.model_id == "B1" and item.validation_year == 2020
    )
    original = run_inner_fit(
        task,
        row_groups=groups,
        model_frame=frame,
        target=target,
        registry=registry,
        model_selection_config=selection_config,
        spatial_buffer_geoids=buffers,
    )
    outer_roles = assign_fold_roles(
        groups,
        family="joint",
        held_out_year=2024,
        held_out_block="A",
        buffered_geoids=frozenset({"a0", "a1"}),
    )
    excluded = ~outer_roles.eq("train")
    changed_frame = frame.copy()
    model_columns = registry.loc[registry["role"].eq("model"), "feature_name"]
    changed_frame = changed_frame.astype(
        {column: "object" for column in model_columns}
    )
    changed_frame.loc[excluded, model_columns] = "must-not-be-read"
    changed_target = target.astype(object)
    changed_target.loc[excluded] = "must-not-be-read"

    changed = run_inner_fit(
        task.to_dict(),
        row_groups=groups,
        model_frame=changed_frame,
        target=changed_target,
        registry=registry,
        model_selection_config=selection_config,
        spatial_buffer_geoids=buffers,
    )

    pd.testing.assert_frame_equal(original.date_scores, changed.date_scores)
    assert original.audit == changed.audit
    assert original.audit.outer_test_row_count > 0
    assert original.audit.outer_purged_row_count > 0
    assert original.audit.outer_excluded_feature_values_read is False
    assert original.audit.outer_excluded_target_values_read is False
    restored = InnerFitResult.from_dict(original.to_dict())
    pd.testing.assert_frame_equal(restored.date_scores, original.date_scores)
    assert restored.audit == original.audit


def test_exact_candidate_selection_and_outer_refit(
    selection_config, synthetic_bundle, b1_inner_results
) -> None:
    groups, frame, target, registry, buffers, plan = synthetic_bundle
    selection = select_outer_candidate(
        [result.to_dict() for result in b1_inner_results], selection_config
    )
    outer_task = next(task for task in plan.outer_tasks if task.model_id == "B1")
    predictions = run_outer_fit(
        outer_task,
        selection,
        row_groups=groups,
        model_frame=frame,
        target=target,
        registry=registry,
        model_selection_config=selection_config,
        spatial_buffer_geoids=buffers,
    )

    assert tuple(predictions.columns) == OUTER_PREDICTION_COLUMNS
    assert len(predictions) == outer_task.expected_outer_test_row_count
    assert predictions["family"].unique().tolist() == ["joint"]
    assert predictions["fold_id"].unique().tolist() == [outer_task.fold_id]
    assert predictions["model_id"].unique().tolist() == ["B1"]
    assert predictions["candidate_id"].unique().tolist() == [
        selection.selected_candidate.candidate_id
    ]
    assert predictions["target_date"].dt.year.unique().tolist() == [2024]
    assert np.isfinite(predictions[["y_true", "y_pred"]]).all(axis=None)

    purged = assign_fold_roles(
        groups,
        family="joint",
        held_out_year=2024,
        held_out_block="A",
        buffered_geoids=frozenset({"a0", "a1"}),
    ).eq("purged")
    changed_frame = frame.copy()
    changed_frame["weather_feature_00"] = changed_frame[
        "weather_feature_00"
    ].astype(object)
    changed_frame.loc[purged, "weather_feature_00"] = "must-not-be-read"
    changed_target = target.astype(object)
    changed_target.loc[purged] = "must-not-be-read"
    repeated = run_outer_fit(
        outer_task.to_dict(),
        selection.selected_candidate,
        row_groups=groups,
        model_frame=changed_frame,
        target=changed_target,
        registry=registry,
        model_selection_config=selection_config,
        spatial_buffer_geoids=buffers,
    )
    np.testing.assert_allclose(repeated["y_pred"], predictions["y_pred"])


def test_missing_inner_result_and_coverage_or_key_drift_fail_closed(
    selection_config, synthetic_bundle, b1_inner_results
) -> None:
    groups, frame, target, registry, buffers, plan = synthetic_bundle
    with pytest.raises(ModelTaskAuditError, match="candidate × validation-year"):
        select_outer_candidate(b1_inner_results[:-1], selection_config)

    task_payload = next(task for task in plan.inner_tasks if task.model_id == "B1").to_dict()
    task_payload["expected_outer_test_row_count"] += 1
    bad_coverage_task = InnerFitTask.from_dict(task_payload)
    with pytest.raises(ModelTaskAuditError, match="role counts"):
        run_inner_fit(
            bad_coverage_task,
            row_groups=groups,
            model_frame=frame,
            target=target,
            registry=registry,
            model_selection_config=selection_config,
            spatial_buffer_geoids=buffers,
        )

    bad_keys = frame.copy()
    bad_keys.loc[0, "tract_geoid"] = "wrong"
    with pytest.raises(ModelTaskAuditError, match="keys disagree"):
        run_inner_fit(
            plan.inner_tasks[0],
            row_groups=groups,
            model_frame=bad_keys,
            target=target,
            registry=registry,
            model_selection_config=selection_config,
            spatial_buffer_geoids=buffers,
        )


def test_locked_2025_is_rejected_before_any_fit(selection_config, synthetic_bundle) -> None:
    groups, frame, target, registry, buffers, plan = synthetic_bundle
    changed = groups.copy()
    changed.loc[0, "target_date"] = pd.Timestamp("2025-06-01")
    changed.loc[0, "year"] = 2025
    changed_frame = frame.copy()
    changed_frame.loc[0, "target_date"] = pd.Timestamp("2025-06-01")

    with pytest.raises(PermissionError, match="2025"):
        run_inner_fit(
            plan.inner_tasks[0],
            row_groups=changed,
            model_frame=changed_frame,
            target=target,
            registry=registry,
            model_selection_config=selection_config,
            spatial_buffer_geoids=buffers,
        )


def test_inner_score_column_contract_is_explicit() -> None:
    assert INNER_DATE_SCORE_COLUMNS == ("candidate_id", "target_date", "date_mae_c")
