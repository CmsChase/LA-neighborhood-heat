"""Audit and compile grouped outer-fold predictions into final OOF artifacts."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.metrics import evaluate_absolute_lst_predictions
from la_heat.model_run_context import ModelRunContext
from la_heat.model_runtime import modeling_runtime_fingerprint
from la_heat.model_selection import (
    MODEL_IDS,
    CandidateSelection,
    HyperparameterCandidate,
)
from la_heat.model_task_engine import (
    OUTER_PREDICTION_COLUMNS,
    OuterFitTask,
    build_task_plan,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.validation_splits import FAMILIES, assign_fold_roles, validate_oof_coverage

MODEL_RUN_COMPILE_SCHEMA_VERSION: Final = 2
MODEL_RUN_COMPILE_ALGORITHM_VERSION: Final = "grouped-model-oof-compile-v2"

OOF_PREDICTIONS_FILENAME: Final = "oof_predictions.parquet"
SUMMARY_METRICS_FILENAME: Final = "summary_metrics.csv"
PER_DATE_METRICS_FILENAME: Final = "per_date_metrics.csv"
FOLD_METRICS_FILENAME: Final = "fold_metrics.csv"
COMPILE_PROVENANCE_FILENAME: Final = "model_run_compile_provenance.json"
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/processed/model_evaluation")

SUMMARY_METRIC_COLUMNS: Final = (
    "family",
    "model_id",
    "row_count",
    "independent_date_count",
    "independent_spatial_block_count",
    "primary_equal_date_weighted_mae_c",
    "pooled_rmse_c",
    "pooled_oos_r2",
    "pooled_mean_signed_error_c",
    "equal_date_weighted_mean_signed_error_c",
    "equal_date_weighted_within_date_anomaly_mae_c",
    "median_per_date_spearman",
    "spearman_defined_date_count",
    "spearman_undefined_date_count",
)
PER_DATE_METRIC_COLUMNS: Final = (
    "family",
    "model_id",
    "target_date",
    "row_count",
    "spatial_block_count",
    "mae_c",
    "mean_signed_error_c",
    "within_date_anomaly_mae_c",
    "spearman_rho",
    "spearman_defined",
)
FOLD_METRIC_COLUMNS: Final = (
    "family",
    "fold_id",
    "model_id",
    "candidate_id",
    "row_count",
    "independent_date_count",
    "mae_c",
    "rmse_c",
    "mean_error_c",
)


class ModelRunCompileError(ValueError):
    """Raised when an outer-result collection violates its frozen contract."""


@dataclass(frozen=True)
class OuterFragmentRecord:
    """One byte-locked Parquet result for a frozen outer task and selection."""

    task: OuterFitTask
    selection: CandidateSelection
    path: str | Path
    sha256: str
    bytes: int
    rows: int
    schema_sha256: str
    path_base: str = "absolute"


def _resolved_fragment_path(
    record: OuterFragmentRecord,
    *,
    fragment_root: Path | None,
) -> Path:
    raw = Path(record.path)
    if record.path_base == "absolute":
        if not raw.is_absolute():
            raise ModelRunCompileError(
                "An absolute outer fragment record must contain an absolute path."
            )
        return raw.resolve()
    if record.path_base != "run_directory":
        raise ModelRunCompileError("Outer fragment path_base is invalid.")
    if fragment_root is None:
        raise ModelRunCompileError(
            "Run-relative outer fragments require an explicit fragment_root."
        )
    if raw.is_absolute() or ".." in raw.parts:
        raise ModelRunCompileError("Run-relative outer fragment path is unsafe.")
    root = fragment_root.resolve()
    resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ModelRunCompileError(
            "Run-relative outer fragment escaped fragment_root."
        ) from error
    return resolved


def _hex_sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ModelRunCompileError(f"{label} must be a lowercase SHA-256 digest.")
    return value


def _context_keys_and_truth(
    context: ModelRunContext,
) -> tuple[pd.DataFrame, pd.Series]:
    if not isinstance(context, ModelRunContext):
        raise TypeError("context must be an authenticated ModelRunContext.")
    for label, value in (
        ("run_id", context.run_id),
        ("model dataset commit", context.model_dataset_commit_sha256),
        ("split promotion commit", context.split_promotion_commit_sha256),
        ("model selection commit", context.model_selection_commit_sha256),
        ("runtime fingerprint", context.runtime_fingerprint_sha256),
    ):
        _hex_sha256(value, label=label)
    if context.model_selection.final_test_year != 2025 or (
        context.model_selection.unlock_final_test
    ):
        raise PermissionError("Final-test year 2025 must remain locked during compilation.")
    if len(context.keys) != len(context.target) or len(context.keys) != len(
        context.row_groups
    ):
        raise ModelRunCompileError("Context keys, target, and row groups are misaligned.")
    keys = context.keys.loc[:, ["tract_geoid", "target_date"]].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    try:
        keys["target_date"] = pd.to_datetime(keys["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise ModelRunCompileError("Context target dates are invalid.") from error
    if (
        keys.isna().any(axis=None)
        or keys.duplicated(["tract_geoid", "target_date"]).any()
        or keys["target_date"].dt.tz is not None
        or not keys["target_date"].dt.normalize().equals(keys["target_date"])
    ):
        raise ModelRunCompileError("Context keys must be unique naive civil dates.")
    if keys["target_date"].dt.year.ge(2025).any():
        raise PermissionError("Context contains locked 2025+ rows.")
    row_keys = context.row_groups.loc[:, ["tract_geoid", "target_date"]].copy()
    row_keys["tract_geoid"] = row_keys["tract_geoid"].astype("string")
    row_keys["target_date"] = pd.to_datetime(row_keys["target_date"], errors="raise")
    try:
        pd.testing.assert_frame_equal(keys, row_keys, check_dtype=False)
    except AssertionError as error:
        raise ModelRunCompileError(
            "Context keys do not follow the frozen row-group order."
        ) from error
    truth = pd.to_numeric(context.target, errors="raise").reset_index(drop=True)
    if not np.isfinite(truth.to_numpy(dtype=float)).all():
        raise ModelRunCompileError("Context target values must be finite.")
    return keys.reset_index(drop=True), truth


def _selection_candidate(
    context: ModelRunContext,
    task: OuterFitTask,
    selection: CandidateSelection,
) -> HyperparameterCandidate:
    if not isinstance(selection, CandidateSelection):
        raise TypeError("Each fragment selection must be a CandidateSelection.")
    if selection.model_id != task.model_id:
        raise ModelRunCompileError("Selection model_id disagrees with its outer task.")
    candidate = context.model_selection.candidate(
        task.model_id, selection.selected_candidate.candidate_id
    )
    if candidate != selection.selected_candidate:
        raise ModelRunCompileError("Selection is not an exact frozen candidate.")
    expected_candidate_ids = {
        item.candidate_id
        for item in context.model_selection.candidates_for(task.model_id)
    }
    ranking_ids = [item.candidate_id for item in selection.ranking]
    score_by_id = {
        item.candidate_id: item.mean_date_mae_c for item in selection.ranking
    }
    best_score = min(score_by_id.values(), default=math.inf)
    tie_threshold = (
        best_score
        + context.model_selection.tie_absolute_tolerance_c
        + context.model_selection.tie_relative_tolerance * abs(best_score)
    )
    expected_tied = tuple(
        candidate_id
        for candidate_id in sorted(
            (
                candidate_id
                for candidate_id, score in score_by_id.items()
                if score <= tie_threshold
            ),
            key=lambda candidate_id: (
                context.model_selection.candidate(
                    task.model_id, candidate_id
                ).complexity_rank,
                candidate_id,
            ),
        )
    )
    if (
        len(ranking_ids) != len(set(ranking_ids))
        or set(ranking_ids) != expected_candidate_ids
        or not ranking_ids
        or tuple(
            sorted(
                selection.ranking,
                key=lambda item: (
                    item.mean_date_mae_c,
                    item.complexity_rank,
                    item.candidate_id,
                ),
            )
        )
        != selection.ranking
        or selection.tied_candidate_ids != expected_tied
        or not expected_tied
        or candidate.candidate_id != expected_tied[0]
        or selection.selection_rule != "minimum_stitched_date_macro_mae"
        or selection.independent_validation_date_count <= 0
        or any(
            item.independent_validation_date_count
            != selection.independent_validation_date_count
            or not math.isfinite(item.mean_date_mae_c)
            or item.mean_date_mae_c < 0
            or item.complexity_rank
            != context.model_selection.candidate(
                task.model_id, item.candidate_id
            ).complexity_rank
            for item in selection.ranking
        )
    ):
        raise ModelRunCompileError("Candidate selection ranking is incomplete or invalid.")
    expected_years = tuple(
        year
        for year in context.model_selection.development_years
        if task.family == "spatial" or year != task.held_out_year
    )
    if selection.validation_years != expected_years:
        raise ModelRunCompileError("Selection validation years disagree with the outer fold.")
    return candidate


def _input_locks(
    context: ModelRunContext,
    fragments: Iterable[OuterFragmentRecord],
    *,
    fragment_root: Path | None,
) -> tuple[list[OuterFragmentRecord], dict[str, OuterFitTask]]:
    plan = build_task_plan(context.fold_definitions, context.model_selection)
    expected = {task.task_id: task for task in plan.outer_tasks}
    records = list(fragments)
    if len(records) != len(expected):
        raise ModelRunCompileError(
            f"Expected {len(expected)} outer fragments; received {len(records)}."
        )
    observed_ids: list[str] = []
    observed_paths: set[Path] = set()
    for record in records:
        if not isinstance(record, OuterFragmentRecord):
            raise TypeError("fragments must contain only OuterFragmentRecord values.")
        if not isinstance(record.task, OuterFitTask):
            raise TypeError("OuterFragmentRecord.task must be an OuterFitTask.")
        expected_task = expected.get(record.task.task_id)
        if expected_task is None or record.task != expected_task:
            raise ModelRunCompileError("Outer fragment task identity is not in the frozen plan.")
        _selection_candidate(context, record.task, record.selection)
        path = _resolved_fragment_path(record, fragment_root=fragment_root)
        if path in observed_paths:
            raise ModelRunCompileError("Outer fragment paths must be unique.")
        observed_paths.add(path)
        if record.rows != record.task.expected_outer_test_row_count:
            raise ModelRunCompileError("Fragment row lock disagrees with its outer task.")
        _hex_sha256(record.sha256, label="fragment SHA-256")
        _hex_sha256(record.schema_sha256, label="fragment schema SHA-256")
        if (
            record.bytes <= 0
            or not path.is_file()
            or path.stat().st_size != record.bytes
            or sha256_file(path) != record.sha256
        ):
            raise ModelRunCompileError(f"Outer fragment byte lock failed: {path}")
        observed_ids.append(record.task.task_id)
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != set(expected):
        raise ModelRunCompileError("Outer fragment task coverage is not exact.")
    return records, expected


def _expected_test_frames(
    context: ModelRunContext,
) -> dict[str, pd.DataFrame]:
    row_groups = context.row_groups.reset_index(drop=True).copy()
    required = {"tract_geoid", "target_date", "spatial_block", "year"}
    if not required.issubset(row_groups.columns):
        raise ModelRunCompileError("Row groups lack required grouped-split columns.")
    audit = validate_oof_coverage(
        row_groups,
        context.fold_definitions,
        context.spatial_buffer_geoids,
    )
    if any(
        result["minimum_test_assignments_per_row"] != 1
        or result["maximum_test_assignments_per_row"] != 1
        for result in audit.values()
    ):
        raise ModelRunCompileError("Grouped folds do not provide exact OOF coverage.")
    buffers = {
        str(block): frozenset(group["tract_geoid"].astype(str))
        for block, group in context.spatial_buffer_geoids.groupby(
            "held_out_block", sort=True
        )
    }
    result: dict[str, pd.DataFrame] = {}
    for fold in context.fold_definitions.itertuples(index=False):
        year = None if pd.isna(fold.held_out_year) else int(fold.held_out_year)
        block = None if pd.isna(fold.held_out_block) else str(fold.held_out_block)
        buffered = (
            buffers.get(block, frozenset())
            if str(fold.family) == "joint" and block is not None
            else frozenset()
        )
        roles = assign_fold_roles(
            row_groups,
            family=str(fold.family),
            held_out_year=year,
            held_out_block=block,
            buffered_geoids=buffered,
        )
        expected = row_groups.loc[
            roles.eq("test"),
            ["tract_geoid", "target_date", "spatial_block"],
        ].copy()
        expected["tract_geoid"] = expected["tract_geoid"].astype("string")
        expected["target_date"] = pd.to_datetime(
            expected["target_date"], errors="raise"
        )
        result[str(fold.fold_id)] = expected.reset_index(drop=True)
    return result


def _validated_fragment(
    record: OuterFragmentRecord,
    expected_test: pd.DataFrame,
    truth_lookup: pd.Series,
    *,
    fragment_root: Path | None,
) -> pd.DataFrame:
    path = _resolved_fragment_path(record, fragment_root=fragment_root)
    frame = pd.read_parquet(path)
    actual_record = parquet_file_record(path, frame)
    for key in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual_record[key] != getattr(record, key):
            raise ModelRunCompileError(f"Fragment {record.task.task_id} {key} drifted.")
    if tuple(frame.columns) != OUTER_PREDICTION_COLUMNS:
        raise ModelRunCompileError("Outer fragment prediction schema/order is invalid.")
    working = frame.copy()
    working["tract_geoid"] = working["tract_geoid"].astype("string")
    try:
        working["target_date"] = pd.to_datetime(
            working["target_date"], errors="raise"
        )
    except (TypeError, ValueError) as error:
        raise ModelRunCompileError("Outer fragment has invalid target dates.") from error
    if (
        working[["tract_geoid", "target_date"]].isna().any(axis=None)
        or working.duplicated(["tract_geoid", "target_date"]).any()
        or working["target_date"].dt.tz is not None
        or not working["target_date"].dt.normalize().equals(working["target_date"])
        or working["target_date"].dt.year.ge(2025).any()
    ):
        raise ModelRunCompileError("Outer fragment keys violate the development contract.")
    task = record.task
    candidate_id = record.selection.selected_candidate.candidate_id
    identities = {
        "family": task.family,
        "fold_id": task.fold_id,
        "model_id": task.model_id,
        "candidate_id": candidate_id,
    }
    if any(
        working[column].nunique(dropna=False) != 1
        or working[column].iloc[0] != expected
        for column, expected in identities.items()
    ):
        raise ModelRunCompileError("Outer fragment identity disagrees with task/selection.")
    numeric = working[["y_true", "y_pred"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ModelRunCompileError("Outer fragment contains non-finite truth/predictions.")
    working.loc[:, ["y_true", "y_pred"]] = numeric
    joined = expected_test.merge(
        working,
        on=["tract_geoid", "target_date"],
        how="outer",
        suffixes=("_expected", ""),
        indicator=True,
        sort=False,
        validate="one_to_one",
    )
    if not joined["_merge"].eq("both").all() or len(joined) != len(expected_test):
        raise ModelRunCompileError("Outer fragment does not cover its exact fold-test keys.")
    if not joined["spatial_block_expected"].astype(str).equals(
        joined["spatial_block"].astype(str)
    ):
        raise ModelRunCompileError("Outer fragment spatial blocks disagree with row groups.")
    key_index = pd.MultiIndex.from_frame(
        joined[["tract_geoid", "target_date"]],
        names=["tract_geoid", "target_date"],
    )
    expected_truth = truth_lookup.loc[key_index].to_numpy(dtype=float)
    observed_truth = joined["y_true"].to_numpy(dtype=float)
    if not np.array_equal(expected_truth, observed_truth):
        raise ModelRunCompileError("Outer fragment y_true disagrees with context target.")
    return joined.loc[:, list(OUTER_PREDICTION_COLUMNS)].reset_index(drop=True)


def _errors(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    error = frame["y_pred"].to_numpy(dtype=float) - frame["y_true"].to_numpy(
        dtype=float
    )
    return error, np.abs(error)


def _audited_metrics(oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use the project's audited absolute-LST evaluator for every OOF surface."""

    summary_rows: list[dict[str, Any]] = []
    per_date_frames: list[pd.DataFrame] = []
    for (family, model_id), group in oof.groupby(
        ["family", "model_id"], sort=False, observed=True
    ):
        evaluation = evaluate_absolute_lst_predictions(
            group.loc[
                :,
                [
                    "tract_geoid",
                    "target_date",
                    "spatial_block",
                    "y_true",
                    "y_pred",
                ],
            ],
            final_test_year=2025,
            unlock_final_test=False,
        )
        summary = evaluation.summary
        summary_rows.append(
            {
                "family": family,
                "model_id": model_id,
                **vars(summary),
            }
        )
        per_date = evaluation.per_date.copy()
        per_date.insert(0, "model_id", model_id)
        per_date.insert(0, "family", family)
        per_date_frames.append(per_date)
    return (
        pd.DataFrame(summary_rows, columns=list(SUMMARY_METRIC_COLUMNS)),
        pd.concat(per_date_frames, ignore_index=True).loc[
            :, list(PER_DATE_METRIC_COLUMNS)
        ],
    )


def _fold_metrics(oof: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    grouped = oof.groupby(
        ["family", "fold_id", "model_id"], sort=False, observed=True
    )
    for (family, fold_id, model_id), group in grouped:
        candidate_ids = group["candidate_id"].unique()
        if len(candidate_ids) != 1:
            raise ModelRunCompileError("A fold/model contains multiple selected candidates.")
        error, absolute = _errors(group)
        rows.append(
            {
                "family": family,
                "fold_id": fold_id,
                "model_id": model_id,
                "candidate_id": candidate_ids[0],
                "row_count": len(group),
                "independent_date_count": int(group["target_date"].nunique()),
                "mae_c": float(absolute.mean()),
                "rmse_c": float(np.sqrt(np.mean(np.square(error)))),
                "mean_error_c": float(error.mean()),
            }
        )
    return pd.DataFrame(rows, columns=list(FOLD_METRIC_COLUMNS))


def _ordered(frame: pd.DataFrame, *, keys: list[str]) -> pd.DataFrame:
    family_order = {family: index for index, family in enumerate(FAMILIES)}
    model_order = {model_id: index for index, model_id in enumerate(MODEL_IDS)}
    result = frame.copy()
    result["__family_order"] = result["family"].map(family_order)
    result["__model_order"] = result["model_id"].map(model_order)
    result = result.sort_values(
        ["__family_order", "__model_order", *keys], kind="stable"
    ).drop(columns=["__family_order", "__model_order"])
    return result.reset_index(drop=True)


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
    }


def compile_model_run_outputs(
    context: ModelRunContext,
    fragments: Iterable[OuterFragmentRecord],
    *,
    execution_run_id: str,
    task_plan_sha256: str,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    fragment_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate every outer fragment, then atomically publish complete OOF outputs."""

    execution_run_id = _hex_sha256(execution_run_id, label="execution run ID")
    task_plan_sha256 = _hex_sha256(task_plan_sha256, label="task-plan SHA-256")
    keys, truth = _context_keys_and_truth(context)
    resolved_fragment_root = (
        None if fragment_root is None else Path(fragment_root).resolve()
    )
    records, expected_tasks = _input_locks(
        context,
        fragments,
        fragment_root=resolved_fragment_root,
    )
    expected_tests = _expected_test_frames(context)
    truth_lookup = pd.Series(
        truth.to_numpy(dtype=float),
        index=pd.MultiIndex.from_frame(keys),
        name="target_lst_c",
    )
    pieces = [
        _validated_fragment(
            record,
            expected_tests[record.task.fold_id],
            truth_lookup,
            fragment_root=resolved_fragment_root,
        )
        for record in records
    ]
    oof = pd.concat(pieces, ignore_index=True)
    expected_oof_rows = len(keys) * len(FAMILIES) * len(MODEL_IDS)
    if len(oof) != expected_oof_rows:
        raise ModelRunCompileError(
            f"OOF row count must be {expected_oof_rows}; found {len(oof)}."
        )
    identity_keys = ["family", "model_id", "tract_geoid", "target_date"]
    if oof.duplicated(identity_keys).any():
        raise ModelRunCompileError("OOF predictions duplicate a family/model/key.")
    expected_key_hash = canonical_frame_sha256(
        keys, sort_by=["target_date", "tract_geoid"]
    )
    for family in FAMILIES:
        for model_id in MODEL_IDS:
            group_keys = oof.loc[
                oof["family"].eq(family) & oof["model_id"].eq(model_id),
                ["tract_geoid", "target_date"],
            ]
            if len(group_keys) != len(keys) or canonical_frame_sha256(
                group_keys, sort_by=["target_date", "tract_geoid"]
            ) != expected_key_hash:
                raise ModelRunCompileError(
                    f"OOF key coverage is incomplete for {family}/{model_id}."
                )

    oof = _ordered(
        oof,
        keys=["target_date", "tract_geoid", "fold_id"],
    ).loc[:, list(OUTER_PREDICTION_COLUMNS)]
    raw_summary, raw_per_date = _audited_metrics(oof)
    per_date = _ordered(raw_per_date, keys=["target_date"]).loc[
        :, list(PER_DATE_METRIC_COLUMNS)
    ]
    summary = _ordered(raw_summary, keys=[]).loc[
        :, list(SUMMARY_METRIC_COLUMNS)
    ]
    folds = _ordered(_fold_metrics(oof), keys=["fold_id"]).loc[
        :, list(FOLD_METRIC_COLUMNS)
    ]
    expected_summary_rows = len(FAMILIES) * len(MODEL_IDS)
    expected_date_rows = (
        int(keys["target_date"].nunique()) * len(FAMILIES) * len(MODEL_IDS)
    )
    expected_fold_rows = len(context.fold_definitions) * len(MODEL_IDS)
    if (
        len(summary) != expected_summary_rows
        or len(per_date) != expected_date_rows
        or len(folds) != expected_fold_rows
        or len(expected_tasks) != expected_fold_rows
    ):
        raise ModelRunCompileError("Compiled metric cardinalities are incomplete.")
    metric_arrays = (
        summary[
            [
                "primary_equal_date_weighted_mae_c",
                "pooled_rmse_c",
                "pooled_mean_signed_error_c",
                "equal_date_weighted_mean_signed_error_c",
                "equal_date_weighted_within_date_anomaly_mae_c",
            ]
        ]
        .to_numpy(dtype=float)
        .ravel(),
        per_date[
            ["mae_c", "mean_signed_error_c", "within_date_anomaly_mae_c"]
        ]
        .to_numpy(dtype=float)
        .ravel(),
        folds[["mae_c", "rmse_c", "mean_error_c"]]
        .to_numpy(dtype=float)
        .ravel(),
    )
    if not all(np.isfinite(values).all() for values in metric_arrays):
        raise ModelRunCompileError("Compiled metrics contain non-finite values.")

    project_root = Path(__file__).resolve().parents[2]
    runtime_sha, runtime_payload = modeling_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "src/la_heat/model_run_compile.py",
            "src/la_heat/model_run_context.py",
            "src/la_heat/model_runtime.py",
            "src/la_heat/metrics.py",
            "src/la_heat/model_selection.py",
            "src/la_heat/model_task_engine.py",
            "src/la_heat/provenance.py",
            "src/la_heat/validation_splits.py",
        ),
        algorithm_version=MODEL_RUN_COMPILE_ALGORITHM_VERSION,
    )
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "oof": output / OOF_PREDICTIONS_FILENAME,
        "summary": output / SUMMARY_METRICS_FILENAME,
        "per_date": output / PER_DATE_METRICS_FILENAME,
        "fold": output / FOLD_METRICS_FILENAME,
        "provenance": output / COMPILE_PROVENANCE_FILENAME,
    }
    paths["provenance"].unlink(missing_ok=True)
    atomic_parquet(oof, paths["oof"])
    atomic_csv(summary, paths["summary"])
    atomic_csv(per_date, paths["per_date"])
    atomic_csv(folds, paths["fold"])
    output_records = {
        OOF_PREDICTIONS_FILENAME: {
            "path": paths["oof"].name,
            "path_base": "output_directory",
            **parquet_file_record(paths["oof"], oof),
        },
        SUMMARY_METRICS_FILENAME: _csv_record(paths["summary"], summary),
        PER_DATE_METRICS_FILENAME: _csv_record(paths["per_date"], per_date),
        FOLD_METRICS_FILENAME: _csv_record(paths["fold"], folds),
    }
    payload: dict[str, Any] = {
        "schema_version": MODEL_RUN_COMPILE_SCHEMA_VERSION,
        "algorithm_version": MODEL_RUN_COMPILE_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_reporting": True,
        "compiled_at_utc": datetime.now(UTC).isoformat(),
        "run_id": execution_run_id,
        "context_run_id": context.run_id,
        "task_plan_sha256": task_plan_sha256,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
        "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
        "model_selection_commit_sha256": context.model_selection_commit_sha256,
        "context_runtime_fingerprint_sha256": context.runtime_fingerprint_sha256,
        "compile_runtime_fingerprint_sha256": runtime_sha,
        "compile_runtime_fingerprint": runtime_payload,
        "context_row_count": len(keys),
        "independent_date_count": int(keys["target_date"].nunique()),
        "family_count": len(FAMILIES),
        "model_count": len(MODEL_IDS),
        "outer_fragment_count": len(records),
        "oof_prediction_row_count": len(oof),
        "summary_metric_row_count": len(summary),
        "per_date_metric_row_count": len(per_date),
        "fold_metric_row_count": len(folds),
        "oof_contract": {
            "unique_by": identity_keys,
            "assignments_per_key_family_model": 1,
            "target_equality": "bit_exact_to_authenticated_context",
            "date_aggregation": "equal_weight_per_independent_date",
        },
        "input_fragments": [
            {
                "task_id": record.task.task_id,
                "family": record.task.family,
                "fold_id": record.task.fold_id,
                "model_id": record.task.model_id,
                "candidate_id": record.selection.selected_candidate.candidate_id,
                "path": (
                    str(_resolved_fragment_path(record, fragment_root=None))
                    if record.path_base == "absolute"
                    else Path(record.path).as_posix()
                ),
                "path_base": record.path_base,
                "sha256": record.sha256,
                "bytes": record.bytes,
                "rows": record.rows,
                "schema_sha256": record.schema_sha256,
            }
            for record in sorted(records, key=lambda item: item.task.task_id)
        ],
        "output_files": output_records,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, paths["provenance"])
    return payload
