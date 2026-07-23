from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

import la_heat.model_run_compile as compile_module
from la_heat.model_run_compile import (
    FOLD_METRICS_FILENAME,
    OOF_PREDICTIONS_FILENAME,
    PER_DATE_METRICS_FILENAME,
    SUMMARY_METRICS_FILENAME,
    ModelRunCompileError,
    OuterFragmentRecord,
    compile_model_run_outputs,
)
from la_heat.model_run_context import ModelRunContext
from la_heat.model_selection import (
    MODEL_IDS,
    CandidateScore,
    CandidateSelection,
    load_model_selection_config,
)
from la_heat.model_task_engine import (
    OUTER_PREDICTION_COLUMNS,
    OuterFitTask,
    build_task_plan,
)
from la_heat.provenance import canonical_sha256, parquet_file_record
from la_heat.validation_splits import (
    FAMILIES,
    assign_fold_roles,
    build_validation_split_tables,
)

ROOT = Path(__file__).resolve().parents[1]
SELECTION_CONFIG = ROOT / "configs" / "model_selection.toml"
EXECUTION_RUN_ID = "f" * 64
TASK_PLAN_SHA256 = "1" * 64


def _synthetic_context() -> ModelRunContext:
    tracts = gpd.GeoDataFrame(
        {
            "GEOID": ["a", "b"],
            "spatial_block": ["x+0000_y+0000", "x+0001_y+0000"],
        },
        geometry=[box(100, 100, 200, 200), box(6000, 100, 6100, 200)],
        crs="EPSG:3310",
    )
    block_by_geoid = dict(zip(tracts["GEOID"], tracts["spatial_block"], strict=True))
    rows = pd.DataFrame(
        [
            {
                "tract_geoid": geoid,
                "target_date": pd.Timestamp(year, 7, 1),
                "spatial_block": block_by_geoid[geoid],
            }
            for year in range(2020, 2025)
            for geoid in ("a", "b")
        ]
    )
    split = build_validation_split_tables(
        rows,
        tracts,
        development_years=tuple(range(2020, 2025)),
        final_test_year=2025,
        analysis_crs="EPSG:3310",
        block_size_km=5.0,
        joint_buffer_m=1000.0,
    )
    keys = split.row_groups.loc[:, ["tract_geoid", "target_date"]].copy()
    target = pd.Series(
        np.arange(len(keys), dtype=float) + 25.0,
        name="target_lst_c",
    )
    dataset = keys.copy()
    dataset["target_lst_c"] = target
    dataset["x"] = np.arange(len(keys), dtype=float)
    registry = pd.DataFrame(
        {
            "feature_name": ["tract_geoid", "target_date", "x"],
            "role": ["key", "key", "model"],
        }
    )
    return ModelRunContext(
        run_id="a" * 64,
        model_dataset_commit_sha256="b" * 64,
        split_promotion_commit_sha256="c" * 64,
        model_selection_commit_sha256="d" * 64,
        runtime_fingerprint_sha256="e" * 64,
        dataset=dataset,
        registry=registry,
        row_groups=split.row_groups,
        fold_definitions=split.fold_definitions,
        spatial_buffer_geoids=split.spatial_buffer_geoids,
        features=dataset.loc[:, ["x"]],
        target=target,
        keys=keys,
        audit_only=pd.DataFrame(index=keys.index),
        model_selection=load_model_selection_config(SELECTION_CONFIG),
    )


def _selection(context: ModelRunContext, task: OuterFitTask) -> CandidateSelection:
    candidates = context.model_selection.candidates_for(task.model_id)
    validation_years = tuple(
        year
        for year in context.model_selection.development_years
        if task.family == "spatial" or year != task.held_out_year
    )
    validation_date_count = len(validation_years)
    ranking = tuple(
        CandidateScore(
            candidate_id=candidate.candidate_id,
            mean_date_mae_c=float(index + 1),
            independent_validation_date_count=validation_date_count,
            complexity_rank=candidate.complexity_rank,
        )
        for index, candidate in enumerate(candidates)
    )
    return CandidateSelection(
        model_id=task.model_id,
        selected_candidate=candidates[0],
        ranking=ranking,
        tied_candidate_ids=(candidates[0].candidate_id,),
        validation_years=validation_years,
        independent_validation_date_count=validation_date_count,
    )


def _buffer_lookup(context: ModelRunContext) -> dict[str, frozenset[str]]:
    return {
        str(block): frozenset(group["tract_geoid"].astype(str))
        for block, group in context.spatial_buffer_geoids.groupby("held_out_block")
    }


def _write_fragments(
    context: ModelRunContext,
    directory: Path,
) -> list[OuterFragmentRecord]:
    directory.mkdir()
    plan = build_task_plan(context.fold_definitions, context.model_selection)
    buffers = _buffer_lookup(context)
    records: list[OuterFragmentRecord] = []
    model_offsets = {model_id: index / 10 for index, model_id in enumerate(MODEL_IDS)}
    for task in plan.outer_tasks:
        roles = assign_fold_roles(
            context.row_groups,
            family=task.family,
            held_out_year=task.held_out_year,
            held_out_block=task.held_out_block,
            buffered_geoids=(
                buffers[task.held_out_block]
                if task.family == "joint" and task.held_out_block is not None
                else frozenset()
            ),
        )
        selected = _selection(context, task)
        result = context.row_groups.loc[
            roles.eq("test"),
            ["tract_geoid", "target_date", "spatial_block"],
        ].copy()
        result["family"] = task.family
        result["fold_id"] = task.fold_id
        result["model_id"] = task.model_id
        result["candidate_id"] = selected.selected_candidate.candidate_id
        truth = context.target.loc[roles.eq("test")].to_numpy(dtype=float)
        result["y_true"] = truth
        result["y_pred"] = truth + model_offsets[task.model_id]
        result = result.loc[:, list(OUTER_PREDICTION_COLUMNS)].reset_index(drop=True)
        path = directory / f"{task.task_id}.parquet"
        result.to_parquet(path, index=False)
        file_record = parquet_file_record(path, result)
        records.append(
            OuterFragmentRecord(
                task=task,
                selection=selected,
                path=path,
                sha256=file_record["sha256"],
                bytes=file_record["bytes"],
                rows=file_record["rows"],
                schema_sha256=file_record["schema_sha256"],
            )
        )
    return records


def test_compiles_exact_synthetic_oof_and_metric_cardinalities(tmp_path: Path) -> None:
    context = _synthetic_context()
    fragments = _write_fragments(context, tmp_path / "fragments")
    output = tmp_path / "compiled"

    payload = compile_model_run_outputs(
        context,
        fragments,
        execution_run_id=EXECUTION_RUN_ID,
        task_plan_sha256=TASK_PLAN_SHA256,
        output_directory=output,
    )

    oof = pd.read_parquet(output / OOF_PREDICTIONS_FILENAME)
    summary = pd.read_csv(output / SUMMARY_METRICS_FILENAME)
    per_date = pd.read_csv(output / PER_DATE_METRICS_FILENAME)
    folds = pd.read_csv(output / FOLD_METRICS_FILENAME)
    assert len(oof) == len(context.keys) * len(FAMILIES) * len(MODEL_IDS) == 150
    assert len(summary) == len(FAMILIES) * len(MODEL_IDS) == 15
    assert len(per_date) == 5 * len(FAMILIES) * len(MODEL_IDS) == 75
    assert len(folds) == len(context.fold_definitions) * len(MODEL_IDS) == 85
    assert not oof.duplicated(
        ["family", "model_id", "tract_geoid", "target_date"]
    ).any()
    assert payload["state"] == "complete"
    assert payload["run_id"] == EXECUTION_RUN_ID
    assert payload["context_run_id"] == context.run_id
    assert payload["task_plan_sha256"] == TASK_PLAN_SHA256
    marker = json.loads(
        (output / "model_run_compile_provenance.json").read_text(encoding="utf-8")
    )
    commit = marker.pop("commit_sha256")
    assert canonical_sha256(marker) == commit


def test_bad_fragment_byte_lock_fails_before_reads_or_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _synthetic_context()
    fragments = _write_fragments(context, tmp_path / "fragments")
    fragments[0] = replace(fragments[0], sha256="0" * 64)
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("Bad locks must fail before fragment Parquet reads.")

    monkeypatch.setattr(compile_module.pd, "read_parquet", forbidden_read)
    output = tmp_path / "compiled"
    with pytest.raises(ModelRunCompileError, match="byte lock"):
        compile_model_run_outputs(
            context,
            fragments,
            execution_run_id=EXECUTION_RUN_ID,
            task_plan_sha256=TASK_PLAN_SHA256,
            output_directory=output,
        )
    assert reads == []
    assert not output.exists()


@pytest.mark.parametrize("corruption", ["truth", "keys", "candidate"])
def test_scientific_fragment_drift_fails_before_publication(
    tmp_path: Path,
    corruption: str,
) -> None:
    context = _synthetic_context()
    fragments = _write_fragments(context, tmp_path / "fragments")
    record = fragments[0]
    frame = pd.read_parquet(record.path)
    if corruption == "truth":
        frame.loc[0, "y_true"] += 1.0
    elif corruption == "keys":
        frame.loc[0, "tract_geoid"] = "not-a-tract"
    else:
        frame.loc[:, "candidate_id"] = "wrong-candidate"
    frame.to_parquet(record.path, index=False)
    file_record = parquet_file_record(Path(record.path), frame)
    fragments[0] = replace(
        record,
        sha256=file_record["sha256"],
        bytes=file_record["bytes"],
        rows=file_record["rows"],
        schema_sha256=file_record["schema_sha256"],
    )
    output = tmp_path / "compiled"

    with pytest.raises(ModelRunCompileError):
        compile_model_run_outputs(
            context,
            fragments,
            execution_run_id=EXECUTION_RUN_ID,
            task_plan_sha256=TASK_PLAN_SHA256,
            output_directory=output,
        )
    assert not output.exists()


def test_production_cardinalities_are_frozen_by_generic_formula() -> None:
    assert 63_403 * len(FAMILIES) * len(MODEL_IDS) == 951_045
    assert 65 * len(FAMILIES) * len(MODEL_IDS) == 975
    assert 431 * len(MODEL_IDS) == 2_155


def test_run_relative_fragments_compile_after_directory_relocation(
    tmp_path: Path,
) -> None:
    context = _synthetic_context()
    source_run = tmp_path / "source_run"
    source_run.mkdir()
    absolute_records = _write_fragments(
        context,
        source_run / "outer_fragments",
    )
    relative_records = [
        replace(
            record,
            path=Path(record.path).relative_to(source_run).as_posix(),
            path_base="run_directory",
        )
        for record in absolute_records
    ]
    relocated_run = tmp_path / "returned" / "run"
    shutil.copytree(source_run, relocated_run)
    output = tmp_path / "compiled"

    payload = compile_model_run_outputs(
        context,
        relative_records,
        execution_run_id=EXECUTION_RUN_ID,
        task_plan_sha256=TASK_PLAN_SHA256,
        output_directory=output,
        fragment_root=relocated_run,
    )

    assert {row["path_base"] for row in payload["input_fragments"]} == {
        "run_directory"
    }
    assert all(
        row["path"].startswith("outer_fragments/")
        for row in payload["input_fragments"]
    )
    assert all(
        record["path_base"] == "output_directory"
        and record["path"] == filename
        for filename, record in payload["output_files"].items()
    )


def test_run_relative_fragment_cannot_escape_root(tmp_path: Path) -> None:
    context = _synthetic_context()
    fragments = _write_fragments(context, tmp_path / "fragments")
    fragments[0] = replace(
        fragments[0],
        path="../outside.parquet",
        path_base="run_directory",
    )

    with pytest.raises(ModelRunCompileError, match="unsafe"):
        compile_model_run_outputs(
            context,
            fragments,
            execution_run_id=EXECUTION_RUN_ID,
            task_plan_sha256=TASK_PLAN_SHA256,
            output_directory=tmp_path / "compiled",
            fragment_root=tmp_path / "run",
        )
