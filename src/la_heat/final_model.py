"""Frozen full-development tuning, fitting, and model-artifact authentication.

This module is development-only.  It refuses calendar year 2025, tunes the
predeclared B1 and M2 candidates by leave-one-development-year-out validation,
then fits one pipeline per model on all authenticated 2020--2024 rows.  It does
not create ``MODEL_LOCK.json`` and contains no final-test evaluator.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
import pandas as pd

from la_heat.model_run_context import (
    DEFAULT_FOLD_DEFINITIONS_PATH,
    DEFAULT_MODEL_PROVENANCE_PATH,
    DEFAULT_MODEL_SELECTION_CONFIG_PATH,
    DEFAULT_MODEL_SELECTION_FREEZE_PATH,
    DEFAULT_MODEL_TABLE_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_ROW_GROUPS_PATH,
    DEFAULT_SPATIAL_BUFFERS_PATH,
    DEFAULT_SPLIT_PROMOTION_PATH,
    ModelRunContext,
    load_model_run_context,
)
from la_heat.model_runtime import modeling_runtime_fingerprint
from la_heat.model_selection import (
    FROZEN_CONFIG_SEMANTIC_SHA256,
    SCORE_COLUMNS,
    CandidateSelection,
    ModelSelectionConfig,
    select_candidate,
)
from la_heat.modeling import fit_fold_model, make_model_spec, predict_fold_model
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

FINAL_MODEL_SCHEMA_VERSION: Final = 1
FINAL_MODEL_ALGORITHM_VERSION: Final = "full-development-final-model-v1"
FINAL_MODEL_CONFIG_STATE: Final = "frozen_development_only"
DEFAULT_FINAL_MODEL_CONFIG: Final = Path("configs/final_model.toml")

RUN_MANIFEST_FILENAME: Final = "final_model_run_manifest.json"
TUNING_SCORES_FILENAME: Final = "full_development_tuning_date_scores.parquet"
SELECTIONS_FILENAME: Final = "full_development_selections.json"
BUILD_PROVENANCE_FILENAME: Final = "final_model_build_provenance.json"
LATEST_BUILD_FILENAME: Final = "latest_build.json"

_MODEL_IDS: Final = ("B1", "M2")
_EXPECTED_CANDIDATE_COUNTS: Final = {"B1": 5, "M2": 8}
_BUNDLE_KEYS: Final = {
    "schema_version",
    "algorithm_version",
    "model_id",
    "candidate_id",
    "candidate_parameters",
    "random_state",
    "feature_names",
    "training_row_count",
    "training_date_count",
    "training_spatial_block_count",
    "training_keys_sha256",
    "pipeline",
}
_RUNTIME_PATHS: Final = (
    "configs/final_model.toml",
    "scripts/build_final_models.py",
    "src/la_heat/final_model.py",
    "src/la_heat/feature_registry.py",
    "src/la_heat/model_run_context.py",
    "src/la_heat/model_runtime.py",
    "src/la_heat/model_selection.py",
    "src/la_heat/modeling.py",
    "src/la_heat/provenance.py",
    "src/la_heat/training_contract.py",
)


class FinalModelError(ValueError):
    """Raised when the full-development model contract is violated."""


@dataclass(frozen=True)
class FinalModelConfig:
    """Validated development-only final-model settings."""

    path: Path
    semantic_sha256: str
    output_root: Path
    model_lock_staging_path: Path
    development_years: tuple[int, ...]
    final_test_year: int
    model_ids: tuple[str, ...]
    baseline_model_id: str
    primary_model_id: str
    expected_tract_date_rows: int
    expected_independent_dates: int
    expected_independent_spatial_blocks: int
    expected_model_feature_count: int
    required_robustness_provenance: tuple[Path, ...]
    planned_figures: tuple[str, ...]
    hotspot_contract: dict[str, Any]


@dataclass(frozen=True)
class FinalTuningTask:
    """One frozen candidate by held-out development-year fit."""

    run_id: str
    selection_config_sha256: str
    model_id: str
    candidate_id: str
    validation_year: int

    @property
    def task_id(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "selection_config_sha256": self.selection_config_sha256,
            "model_id": self.model_id,
            "candidate_id": self.candidate_id,
            "validation_year": self.validation_year,
        }


@dataclass(frozen=True)
class PreparedFinalModelBuild:
    """Authenticated context and immutable build plan."""

    context: ModelRunContext
    config: FinalModelConfig
    run_id: str
    runtime_sha256: str
    runtime_fingerprint: dict[str, Any]
    run_directory: Path
    fragments_directory: Path
    tasks: tuple[FinalTuningTask, ...]
    manifest: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(payload: Mapping[str, Any], expected: set[str], *, name: str) -> None:
    if set(payload) != expected:
        raise FinalModelError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(payload)}."
        )


def _positive_integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FinalModelError(f"{name} must be a positive integer.")
    return value


def _resolved_path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise FinalModelError(f"{name} must be a non-empty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def _normalized_string_list(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise FinalModelError(f"{name} must be a non-empty array.")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise FinalModelError(f"{name} entries must be normalized non-empty strings.")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise FinalModelError(f"{name} entries must be unique.")
    return result


def load_final_model_config(
    path: str | Path = DEFAULT_FINAL_MODEL_CONFIG,
) -> FinalModelConfig:
    """Load the frozen development-only build and lock-staging contract."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _exact_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "state",
            "paths",
            "analysis",
            "selection",
            "hotspot",
            "model_lock",
        },
        name="final-model configuration",
    )
    if (
        raw["schema_version"] != FINAL_MODEL_SCHEMA_VERSION
        or raw["algorithm_version"] != FINAL_MODEL_ALGORITHM_VERSION
        or raw["state"] != FINAL_MODEL_CONFIG_STATE
    ):
        raise FinalModelError("Final-model configuration identity drifted.")
    paths = raw["paths"]
    analysis = raw["analysis"]
    selection = raw["selection"]
    hotspot = raw["hotspot"]
    lock = raw["model_lock"]
    sections = (paths, analysis, selection, hotspot, lock)
    if not all(isinstance(section, dict) for section in sections):
        raise FinalModelError("Final-model configuration sections must be TOML tables.")
    _exact_keys(paths, {"output_root", "model_lock_staging"}, name="paths")
    _exact_keys(
        analysis,
        {
            "development_years",
            "final_test_year",
            "final_test_locked",
            "model_ids",
            "baseline_model_id",
            "primary_model_id",
            "expected_tract_date_rows",
            "expected_independent_dates",
            "expected_independent_spatial_blocks",
            "expected_model_feature_count",
        },
        name="analysis",
    )
    years = tuple(analysis["development_years"])
    if years != tuple(range(2020, 2025)):
        raise FinalModelError("Development years must remain exactly 2020--2024.")
    if analysis["final_test_year"] != 2025 or analysis["final_test_locked"] is not True:
        raise PermissionError("Calendar year 2025 must remain locked.")
    models = _normalized_string_list(analysis["model_ids"], name="analysis.model_ids")
    if models != _MODEL_IDS or analysis["baseline_model_id"] != "B1" or analysis[
        "primary_model_id"
    ] != "M2":
        raise FinalModelError("Final build must freeze B1 as baseline and M2 as primary.")
    expected_counts = (
        _positive_integer(analysis["expected_tract_date_rows"], name="expected rows"),
        _positive_integer(analysis["expected_independent_dates"], name="expected dates"),
        _positive_integer(
            analysis["expected_independent_spatial_blocks"], name="expected blocks"
        ),
        _positive_integer(
            analysis["expected_model_feature_count"], name="expected model features"
        ),
    )
    if expected_counts != (63_403, 65, 71, 46):
        raise FinalModelError("Frozen full-development cardinalities drifted.")
    expected_selection = {
        "strategy": "leave_one_development_year_out",
        "score_input_unit": "one_mae_per_independent_validation_date",
        "primary_metric": "equal_date_weighted_mae_c",
        "rule": "minimum_stitched_date_macro_mae",
        "candidate_coverage": "exact_same_validation_dates",
        "tie_absolute_tolerance_c": 1.0e-12,
        "tie_relative_tolerance": 0.0,
        "tie_breakers": ["complexity_rank", "candidate_id"],
    }
    if selection != expected_selection:
        raise FinalModelError("Full-development selection rule drifted.")
    expected_hotspot = {
        "gate": "relative_endpoint_coverage_pass",
        "label": "relative_hotspot_top20",
        "positive_fraction": 0.20,
        "rank_order": "score_desc_geoid_asc",
        "exact_top_k": True,
        "average_precision_input": "continuous_y_pred",
    }
    if hotspot != expected_hotspot:
        raise FinalModelError("Frozen hotspot rule drifted.")
    _exact_keys(
        lock,
        {
            "formal_lock_generation_allowed",
            "required_robustness_provenance",
            "planned_figures",
        },
        name="model_lock",
    )
    if lock["formal_lock_generation_allowed"] is not False:
        raise PermissionError("This infrastructure may stage, but never generate, MODEL_LOCK.json.")
    robustness_values = _normalized_string_list(
        lock["required_robustness_provenance"],
        name="model_lock.required_robustness_provenance",
    )
    figures = _normalized_string_list(lock["planned_figures"], name="planned_figures")
    return FinalModelConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        output_root=_resolved_path(paths["output_root"], name="paths.output_root"),
        model_lock_staging_path=_resolved_path(
            paths["model_lock_staging"], name="paths.model_lock_staging"
        ),
        development_years=years,
        final_test_year=2025,
        model_ids=models,
        baseline_model_id="B1",
        primary_model_id="M2",
        expected_tract_date_rows=expected_counts[0],
        expected_independent_dates=expected_counts[1],
        expected_independent_spatial_blocks=expected_counts[2],
        expected_model_feature_count=expected_counts[3],
        required_robustness_provenance=tuple(
            _resolved_path(value, name="robustness provenance")
            for value in robustness_values
        ),
        planned_figures=figures,
        hotspot_contract=dict(hotspot),
    )


def _civil_dates(values: pd.Series, *, name: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, format="mixed", errors="raise")
    except (TypeError, ValueError) as error:
        raise FinalModelError(f"{name} contains invalid dates.") from error
    if parsed.isna().any() or parsed.dt.tz is not None or not parsed.dt.normalize().equals(parsed):
        raise FinalModelError(f"{name} must contain timezone-naive civil midnights.")
    return parsed.astype("datetime64[us]")


def _validate_context(context: ModelRunContext, config: FinalModelConfig) -> pd.DataFrame:
    if not isinstance(context, ModelRunContext):
        raise TypeError("context must be an authenticated ModelRunContext.")
    selection = context.model_selection
    if (
        not isinstance(selection, ModelSelectionConfig)
        or selection.semantic_sha256 != FROZEN_CONFIG_SEMANTIC_SHA256
        or selection.final_test_year != config.final_test_year
        or selection.unlock_final_test
        or selection.development_years != config.development_years
        or selection.tie_absolute_tolerance_c != 1.0e-12
        or selection.tie_relative_tolerance != 0.0
    ):
        raise PermissionError("The frozen development selection contract is required.")
    for model_id, expected in _EXPECTED_CANDIDATE_COUNTS.items():
        if len(selection.candidates_for(model_id)) != expected:
            raise FinalModelError(f"Frozen {model_id} candidate coverage drifted.")
    if not (
        context.row_groups.index.equals(context.features.index)
        and context.row_groups.index.equals(context.target.index)
        and context.row_groups.index.equals(context.keys.index)
    ):
        raise FinalModelError("Context rows, features, targets, and keys must align exactly.")
    required = {"tract_geoid", "target_date", "spatial_block", "year"}
    missing = sorted(required - set(context.row_groups.columns))
    if missing:
        raise FinalModelError(f"row_groups is missing columns: {missing}")
    groups = context.row_groups.loc[
        :, ["tract_geoid", "target_date", "spatial_block", "year"]
    ].copy()
    groups["target_date"] = _civil_dates(groups["target_date"], name="row_groups.target_date")
    keys_dates = _civil_dates(context.keys["target_date"], name="keys.target_date")
    if not keys_dates.equals(groups["target_date"]):
        raise FinalModelError("Context keys and row groups disagree on target_date.")
    if not context.keys["tract_geoid"].astype(str).equals(groups["tract_geoid"].astype(str)):
        raise FinalModelError("Context keys and row groups disagree on tract_geoid.")
    if groups.duplicated(["tract_geoid", "target_date"]).any():
        raise FinalModelError("Full-development rows contain duplicate tract-date keys.")
    for column in ("tract_geoid", "spatial_block"):
        valid = groups[column].map(
            lambda value: isinstance(value, str) and bool(value) and value == value.strip()
        )
        if not valid.all():
            raise FinalModelError(f"{column} must contain normalized non-empty strings.")
    try:
        years = pd.to_numeric(groups["year"], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise FinalModelError("row_groups.year must be numeric.") from error
    if not np.isfinite(years).all() or not np.equal(years, np.floor(years)).all():
        raise FinalModelError("row_groups.year must be finite and integral.")
    groups["year"] = years.astype(np.int16)
    if not np.array_equal(groups["year"].to_numpy(), groups["target_date"].dt.year.to_numpy()):
        raise FinalModelError("row_groups.year disagrees with target_date.")
    observed_years = tuple(sorted(int(value) for value in groups["year"].unique()))
    if observed_years != config.development_years or (
        groups["year"] >= config.final_test_year
    ).any():
        raise PermissionError("Full-development fitting must contain only 2020--2024.")
    try:
        target = pd.to_numeric(context.target, errors="raise").to_numpy(
            dtype=float, na_value=np.nan
        )
    except (TypeError, ValueError) as error:
        raise FinalModelError("Full-development target must be numeric.") from error
    if not np.isfinite(target).all():
        raise FinalModelError("Full-development target must be finite and complete.")
    observed = (
        len(groups),
        int(groups["target_date"].nunique()),
        int(groups["spatial_block"].nunique()),
        int((context.registry["role"] == "model").sum()),
    )
    expected = (
        config.expected_tract_date_rows,
        config.expected_independent_dates,
        config.expected_independent_spatial_blocks,
        config.expected_model_feature_count,
    )
    if observed != expected:
        raise FinalModelError(f"Full-development cardinalities drifted: {observed} != {expected}.")
    return groups


def _clean_partials(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.rglob("*.partial"):
        if path.is_file():
            path.unlink()


def _verify_json_commit(payload: Mapping[str, Any], *, name: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if (
        not isinstance(recorded, str)
        or len(recorded) != 64
        or canonical_sha256(working) != recorded
    ):
        raise FinalModelError(f"{name} commit is invalid.")
    return recorded


def _read_json(path: Path, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalModelError(f"Cannot read valid {name}: {path}") from error
    if not isinstance(payload, dict):
        raise FinalModelError(f"{name} must be a JSON object.")
    return payload


def _manifest_payload(
    context: ModelRunContext,
    config: FinalModelConfig,
    *,
    run_id: str,
    runtime_sha256: str,
    runtime_fingerprint: Mapping[str, Any],
    task_count: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "state": "prepared_development_only",
        "run_id": run_id,
        "context_run_id": context.run_id,
        "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
        "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
        "model_selection_commit_sha256": context.model_selection_commit_sha256,
        "selection_config_sha256": context.model_selection.semantic_sha256,
        "analysis_config_semantic_sha256": config.semantic_sha256,
        "runtime_sha256": runtime_sha256,
        "runtime_fingerprint": dict(runtime_fingerprint),
        "development_years": list(config.development_years),
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "model_ids": list(config.model_ids),
        "tuning_task_count": task_count,
        "scientific_contract": {
            "strategy": "leave_one_development_year_out",
            "preprocessing_fit_scope": "training_years_only",
            "selection_metric": "equal_date_weighted_mae_c",
            "random_row_split": False,
            "final_refit_scope": "all_2020_2024_rows_only",
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def prepare_final_model_build(
    context: ModelRunContext,
    config: FinalModelConfig,
) -> PreparedFinalModelBuild:
    """Authenticate context and freeze the exact 65-task B1/M2 tuning plan."""

    _validate_context(context, config)
    runtime_sha256, runtime_fingerprint = modeling_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=_RUNTIME_PATHS,
        algorithm_version=FINAL_MODEL_ALGORITHM_VERSION,
    )
    run_id = canonical_sha256(
        {
            "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
            "context_run_id": context.run_id,
            "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
            "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
            "model_selection_commit_sha256": context.model_selection_commit_sha256,
            "selection_config_sha256": context.model_selection.semantic_sha256,
            "analysis_config_semantic_sha256": config.semantic_sha256,
            "runtime_sha256": runtime_sha256,
        }
    )
    tasks = tuple(
        FinalTuningTask(
            run_id=run_id,
            selection_config_sha256=context.model_selection.semantic_sha256,
            model_id=model_id,
            candidate_id=candidate.candidate_id,
            validation_year=year,
        )
        for model_id in config.model_ids
        for candidate in context.model_selection.candidates_for(model_id)
        for year in config.development_years
    )
    expected_tasks = sum(
        _EXPECTED_CANDIDATE_COUNTS[model] * len(config.development_years)
        for model in config.model_ids
    )
    if len(tasks) != expected_tasks or len({task.task_id for task in tasks}) != len(tasks):
        raise FinalModelError("Full-development tuning task plan is incomplete or duplicated.")
    run_directory = config.output_root / "runs" / run_id
    fragments_directory = run_directory / "tuning_fragments"
    fragments_directory.mkdir(parents=True, exist_ok=True)
    _clean_partials(run_directory)
    manifest = _manifest_payload(
        context,
        config,
        run_id=run_id,
        runtime_sha256=runtime_sha256,
        runtime_fingerprint=runtime_fingerprint,
        task_count=len(tasks),
    )
    manifest_path = run_directory / RUN_MANIFEST_FILENAME
    if manifest_path.exists():
        observed = _read_json(manifest_path, name="final-model run manifest")
        _verify_json_commit(observed, name="Final-model run manifest")
        if observed != manifest:
            raise FinalModelError("Existing final-model run manifest drifted.")
    else:
        atomic_json(manifest, manifest_path)
    return PreparedFinalModelBuild(
        context=context,
        config=config,
        run_id=run_id,
        runtime_sha256=runtime_sha256,
        runtime_fingerprint=runtime_fingerprint,
        run_directory=run_directory,
        fragments_directory=fragments_directory,
        tasks=tasks,
        manifest=manifest,
    )


def _task_result(
    prepared: PreparedFinalModelBuild,
    task: FinalTuningTask,
    groups: pd.DataFrame,
) -> dict[str, Any]:
    context = prepared.context
    validation_mask = groups["year"].eq(task.validation_year)
    training_mask = ~validation_mask
    if not training_mask.any() or not validation_mask.any():
        raise FinalModelError("A leave-one-year-out fold has an empty role.")
    candidate = context.model_selection.candidate(task.model_id, task.candidate_id)
    spec = make_model_spec(
        context.registry,
        task.model_id,
        **context.model_selection.factory_kwargs(task.model_id, task.candidate_id),
    )
    training_keys = context.keys.loc[training_mask]
    validation_keys = context.keys.loc[validation_mask]
    fitted = fit_fold_model(
        spec,
        context.features.loc[training_mask],
        context.target.loc[training_mask],
        training_keys,
    )
    predicted = predict_fold_model(fitted, context.features.loc[validation_mask])
    truth = pd.to_numeric(context.target.loc[validation_mask], errors="raise").to_numpy(
        dtype=float
    )
    scored = validation_keys.copy()
    scored["absolute_error_c"] = np.abs(predicted - truth)
    scores = (
        scored.groupby("target_date", sort=True, as_index=False)["absolute_error_c"]
        .mean()
        .rename(columns={"absolute_error_c": "date_mae_c"})
    )
    expected_dates = int(groups.loc[validation_mask, "target_date"].nunique())
    if len(scores) != expected_dates:
        raise FinalModelError("LOYO score output omitted an independent validation date.")
    score_records = [
        {
            "candidate_id": task.candidate_id,
            "target_date": pd.Timestamp(row.target_date).date().isoformat(),
            "date_mae_c": float(row.date_mae_c),
        }
        for row in scores.itertuples(index=False)
    ]
    payload: dict[str, Any] = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "kind": "full_development_loyo_fit",
        "task_id": task.task_id,
        "task": task.to_dict(),
        "candidate_parameters": candidate.factory_parameters(),
        "date_scores": score_records,
        "audit": {
            "training_row_count": int(training_mask.sum()),
            "validation_row_count": int(validation_mask.sum()),
            "training_date_count": int(groups.loc[training_mask, "target_date"].nunique()),
            "validation_date_count": expected_dates,
            "training_spatial_block_count": int(
                groups.loc[training_mask, "spatial_block"].nunique()
            ),
            "validation_spatial_block_count": int(
                groups.loc[validation_mask, "spatial_block"].nunique()
            ),
            "training_years": sorted(
                int(year) for year in groups.loc[training_mask, "year"].unique()
            ),
            "validation_year": task.validation_year,
            "model_feature_count": len(spec.feature_names),
            "training_keys_sha256": canonical_frame_sha256(
                training_keys, sort_by=["target_date", "tract_geoid"]
            ),
            "validation_keys_sha256": canonical_frame_sha256(
                validation_keys, sort_by=["target_date", "tract_geoid"]
            ),
            "final_test_values_read": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _validated_fragment(
    path: Path,
    *,
    prepared: PreparedFinalModelBuild,
    task: FinalTuningTask,
    groups: pd.DataFrame,
) -> dict[str, Any]:
    payload = _read_json(path, name="LOYO tuning fragment")
    _verify_json_commit(payload, name="LOYO tuning fragment")
    if (
        payload.get("schema_version") != FINAL_MODEL_SCHEMA_VERSION
        or payload.get("algorithm_version") != FINAL_MODEL_ALGORITHM_VERSION
        or payload.get("kind") != "full_development_loyo_fit"
        or payload.get("task_id") != task.task_id
        or payload.get("task") != task.to_dict()
        or not isinstance(payload.get("date_scores"), list)
        or not isinstance(payload.get("audit"), dict)
        or payload["audit"].get("final_test_values_read") is not False
    ):
        raise FinalModelError("LOYO tuning fragment identity or audit drifted.")
    scores = pd.DataFrame(payload["date_scores"], columns=list(SCORE_COLUMNS))
    expected_dates = int(
        groups.loc[groups["year"].eq(task.validation_year), "target_date"].nunique()
    )
    if len(scores) != expected_dates:
        raise FinalModelError("LOYO fragment validation-date cardinality drifted.")
    if not scores["candidate_id"].eq(task.candidate_id).all():
        raise FinalModelError("LOYO fragment candidate identity drifted.")
    dates = _civil_dates(scores["target_date"], name="fragment.target_date")
    if dates.dt.year.ne(task.validation_year).any() or dates.duplicated().any():
        raise FinalModelError("LOYO fragment dates are invalid or duplicated.")
    values = pd.to_numeric(scores["date_mae_c"], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise FinalModelError("LOYO fragment MAE values must be finite and nonnegative.")
    return payload


def _run_or_load_task(
    prepared: PreparedFinalModelBuild,
    task: FinalTuningTask,
    groups: pd.DataFrame,
) -> dict[str, Any]:
    path = prepared.fragments_directory / f"{task.task_id}.json"
    path.with_suffix(path.suffix + ".partial").unlink(missing_ok=True)
    if path.exists():
        return _validated_fragment(path, prepared=prepared, task=task, groups=groups)
    payload = _task_result(prepared, task, groups)
    atomic_json(payload, path)
    return _validated_fragment(path, prepared=prepared, task=task, groups=groups)


def _selection_payload(selection: CandidateSelection) -> dict[str, Any]:
    return {
        "model_id": selection.model_id,
        "selected_candidate_id": selection.selected_candidate.candidate_id,
        "selected_complexity_rank": selection.selected_candidate.complexity_rank,
        "selected_parameters": selection.selected_candidate.factory_parameters(),
        "ranking": [
            {
                "candidate_id": item.candidate_id,
                "mean_date_mae_c": item.mean_date_mae_c,
                "independent_validation_date_count": item.independent_validation_date_count,
                "complexity_rank": item.complexity_rank,
            }
            for item in selection.ranking
        ],
        "tied_candidate_ids": list(selection.tied_candidate_ids),
        "validation_years": list(selection.validation_years),
        "independent_validation_date_count": selection.independent_validation_date_count,
        "selection_rule": selection.selection_rule,
    }


def atomic_dump_model_bundle(bundle: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    """Atomically serialize one fitted bundle and return its exact byte lock."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_model_bundle(dict(bundle))
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    try:
        joblib.dump(dict(bundle), temporary, compress=3)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": destination.name,
        "path_base": "run_directory",
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
    }


def _validate_model_bundle(bundle: object) -> dict[str, Any]:
    if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_KEYS:
        raise FinalModelError("Serialized model bundle has an invalid schema.")
    if (
        bundle["schema_version"] != FINAL_MODEL_SCHEMA_VERSION
        or bundle["algorithm_version"] != FINAL_MODEL_ALGORITHM_VERSION
        or bundle["model_id"] not in _MODEL_IDS
        or not isinstance(bundle["candidate_id"], str)
        or not isinstance(bundle["candidate_parameters"], dict)
        or not isinstance(bundle["random_state"], int)
        or isinstance(bundle["random_state"], bool)
        or not callable(getattr(bundle["pipeline"], "predict", None))
    ):
        raise FinalModelError("Serialized model bundle identity drifted.")
    features = bundle["feature_names"]
    if (
        not isinstance(features, list)
        or not features
        or len(features) != len(set(features))
        or any(not isinstance(value, str) or not value for value in features)
    ):
        raise FinalModelError("Serialized model feature order is invalid.")
    for key in (
        "training_row_count",
        "training_date_count",
        "training_spatial_block_count",
    ):
        _positive_integer(bundle[key], name=f"bundle.{key}")
    key_hash = bundle["training_keys_sha256"]
    if not isinstance(key_hash, str) or len(key_hash) != 64:
        raise FinalModelError("Serialized model training-key hash is invalid.")
    return bundle


def load_hashed_model_bundle(
    path: str | Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    expected_model_id: str | None = None,
    expected_candidate_id: str | None = None,
) -> dict[str, Any]:
    """Verify an exact artifact lock before invoking the unsafe joblib loader."""

    source = Path(path)
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise FinalModelError("Expected model SHA-256 is invalid.")
    if (
        isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 1
    ):
        raise FinalModelError("Expected model byte count is invalid.")
    if not source.is_file() or source.stat().st_size != expected_bytes:
        raise FinalModelError("Model artifact byte lock failed before deserialization.")
    if sha256_file(source) != expected_sha256:
        raise FinalModelError("Model artifact SHA-256 failed before deserialization.")
    bundle = _validate_model_bundle(joblib.load(source))
    if expected_model_id is not None and bundle["model_id"] != expected_model_id:
        raise FinalModelError("Loaded model id disagrees with its authenticated metadata.")
    if expected_candidate_id is not None and bundle["candidate_id"] != expected_candidate_id:
        raise FinalModelError("Loaded candidate id disagrees with its authenticated metadata.")
    return bundle


def predict_model_bundle(bundle: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """Predict with an authenticated fitted bundle; this function never fits."""

    checked = _validate_model_bundle(dict(bundle))
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
        raise FinalModelError("Prediction features must be a DataFrame with unique columns.")
    names = list(checked["feature_names"])
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise FinalModelError(f"Prediction features are missing frozen columns: {missing}")
    predictions = np.asarray(checked["pipeline"].predict(frame.loc[:, names]), dtype=float)
    if (
        predictions.ndim != 1
        or len(predictions) != len(frame)
        or not np.isfinite(predictions).all()
    ):
        raise FinalModelError("Authenticated model produced invalid predictions.")
    return predictions


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "run_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def collect_default_input_locks(context: ModelRunContext) -> dict[str, Any]:
    """Record exact default-path inputs already authenticated by the context loader."""

    root = _project_root()
    paths = {
        "model_dataset_provenance": root / DEFAULT_MODEL_PROVENANCE_PATH,
        "model_table": root / DEFAULT_MODEL_TABLE_PATH,
        "feature_registry": root / DEFAULT_REGISTRY_PATH,
        "split_promotion": root / DEFAULT_SPLIT_PROMOTION_PATH,
        "row_groups": root / DEFAULT_ROW_GROUPS_PATH,
        "fold_definitions": root / DEFAULT_FOLD_DEFINITIONS_PATH,
        "spatial_buffer_geoids": root / DEFAULT_SPATIAL_BUFFERS_PATH,
        "model_selection_freeze": root / DEFAULT_MODEL_SELECTION_FREEZE_PATH,
        "model_selection_config": root / DEFAULT_MODEL_SELECTION_CONFIG_PATH,
    }
    records: dict[str, Any] = {}
    for name, path in paths.items():
        resolved = path.resolve()
        if not resolved.is_file():
            raise FinalModelError(f"Authenticated default input is missing: {resolved}")
        record: dict[str, Any] = {
            "path": resolved.as_posix(),
            "sha256": sha256_file(resolved),
            "bytes": resolved.stat().st_size,
        }
        if resolved.suffix == ".json":
            payload = _read_json(resolved, name=name)
            record["commit_sha256"] = _verify_json_commit(payload, name=name)
        records[name] = record
    records["context_commits"] = {
        "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
        "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
        "model_selection_commit_sha256": context.model_selection_commit_sha256,
    }
    records["feature_registry_semantic_sha256"] = canonical_frame_sha256(
        context.registry, sort_by=["feature_name"]
    )
    return records


def authenticate_final_build_provenance(
    path: str | Path,
    *,
    load_models: bool = False,
) -> dict[str, Any]:
    """Authenticate a completed development build and optionally deserialize its models."""

    provenance_path = Path(path).resolve()
    payload = _read_json(provenance_path, name="final-model build provenance")
    _verify_json_commit(payload, name="Final-model build provenance")
    if (
        payload.get("schema_version") != FINAL_MODEL_SCHEMA_VERSION
        or payload.get("algorithm_version") != FINAL_MODEL_ALGORITHM_VERSION
        or payload.get("state") != "complete_development_only"
        or payload.get("ready_for_model_lock_staging") is not True
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_locked") is not True
        or payload.get("contains_final_test_year") is not False
        or payload.get("final_test_values_read") is not False
        or payload.get("model_ids") != list(_MODEL_IDS)
        or not isinstance(payload.get("output_files"), dict)
        or not isinstance(payload.get("models"), dict)
        or set(payload["models"]) != set(_MODEL_IDS)
        or not isinstance(payload.get("selections"), dict)
        or set(payload["selections"]) != set(_MODEL_IDS)
    ):
        raise FinalModelError("Final-model build provenance is not complete and locked.")
    directory = provenance_path.parent
    for name, record in payload["output_files"].items():
        if (
            not isinstance(record, dict)
            or record.get("path") != name
            or Path(name).name != name
        ):
            raise FinalModelError("Final-model output record is invalid.")
        output = directory / name
        if (
            not output.is_file()
            or output.stat().st_size != record.get("bytes")
            or sha256_file(output) != record.get("sha256")
        ):
            raise FinalModelError(f"Final-model output byte lock failed: {name}")
    for model_id in _MODEL_IDS:
        record = payload["models"].get(model_id)
        if not isinstance(record, dict):
            raise FinalModelError(f"Final-model provenance lacks {model_id}.")
        relative_artifact = str(record.get("path"))
        if Path(relative_artifact).name != relative_artifact:
            raise FinalModelError(f"{model_id} artifact path is not run-directory local.")
        selection = payload["selections"].get(model_id)
        if (
            not isinstance(selection, dict)
            or selection.get("selected_candidate_id")
            != record.get("selected_candidate_id")
        ):
            raise FinalModelError(f"{model_id} selection and artifact metadata disagree.")
        artifact = directory / relative_artifact
        if (
            not artifact.is_file()
            or artifact.stat().st_size != record.get("bytes")
            or sha256_file(artifact) != record.get("sha256")
        ):
            raise FinalModelError(f"{model_id} artifact byte lock failed.")
        if load_models:
            bundle = load_hashed_model_bundle(
                artifact,
                expected_sha256=str(record["sha256"]),
                expected_bytes=int(record["bytes"]),
                expected_model_id=model_id,
                expected_candidate_id=str(record["selected_candidate_id"]),
            )
            expected_bundle_metadata = {
                "candidate_parameters": record.get("selected_parameters"),
                "random_state": record.get("random_state"),
                "feature_names": record.get("feature_names"),
                "training_row_count": record.get("training_row_count"),
                "training_date_count": record.get("training_date_count"),
                "training_spatial_block_count": record.get(
                    "training_spatial_block_count"
                ),
                "training_keys_sha256": record.get("training_keys_sha256"),
            }
            if any(
                bundle.get(key) != value
                for key, value in expected_bundle_metadata.items()
            ):
                raise FinalModelError(
                    f"{model_id} serialized bundle disagrees with provenance metadata."
                )
    return payload


def run_final_model_build(
    context: ModelRunContext,
    config: FinalModelConfig,
    *,
    input_locks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all frozen LOYO fits and serialize B1/M2 full-development pipelines."""

    prepared = prepare_final_model_build(context, config)
    groups = _validate_context(context, config)
    provenance_path = prepared.run_directory / BUILD_PROVENANCE_FILENAME
    if provenance_path.exists():
        try:
            return authenticate_final_build_provenance(provenance_path, load_models=True)
        except FinalModelError:
            provenance_path.unlink(missing_ok=True)
    latest_path = config.output_root / LATEST_BUILD_FILENAME
    latest_path.unlink(missing_ok=True)
    latest_path.with_suffix(latest_path.suffix + ".partial").unlink(missing_ok=True)

    fragments = [
        _run_or_load_task(prepared, task, groups)
        for task in prepared.tasks
    ]
    all_dates = sorted(pd.Timestamp(value) for value in groups["target_date"].unique())
    selections: dict[str, CandidateSelection] = {}
    score_frames: list[pd.DataFrame] = []
    for model_id in config.model_ids:
        model_fragments = [item for item in fragments if item["task"]["model_id"] == model_id]
        expected_fragment_count = (
            _EXPECTED_CANDIDATE_COUNTS[model_id] * len(config.development_years)
        )
        if len(model_fragments) != expected_fragment_count:
            raise FinalModelError(f"{model_id} LOYO fragment coverage is incomplete.")
        scores = pd.concat(
            [
                pd.DataFrame(item["date_scores"], columns=list(SCORE_COLUMNS))
                for item in model_fragments
            ],
            ignore_index=True,
        )
        scores["target_date"] = _civil_dates(scores["target_date"], name="tuning.target_date")
        selection = select_candidate(
            context.model_selection,
            model_id,
            scores.loc[:, list(SCORE_COLUMNS)],
            expected_validation_dates=all_dates,
        )
        selections[model_id] = selection
        scored = scores.copy()
        scored.insert(0, "model_id", model_id)
        score_frames.append(scored)
    all_scores = pd.concat(score_frames, ignore_index=True).sort_values(
        ["model_id", "candidate_id", "target_date"], kind="stable"
    ).reset_index(drop=True)
    expected_score_rows = sum(
        _EXPECTED_CANDIDATE_COUNTS[model] * config.expected_independent_dates
        for model in config.model_ids
    )
    if len(all_scores) != expected_score_rows or (
        all_scores["target_date"].dt.year >= config.final_test_year
    ).any():
        raise FinalModelError("Compiled tuning score coverage is incomplete or contains 2025.")
    scores_path = prepared.run_directory / TUNING_SCORES_FILENAME
    atomic_parquet(all_scores, scores_path)
    selection_payload: dict[str, Any] = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "state": "selected_development_only",
        "run_id": prepared.run_id,
        "selection_config_sha256": context.model_selection.semantic_sha256,
        "development_years": list(config.development_years),
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "score_row_count": len(all_scores),
        "selections": {
            model_id: _selection_payload(selection)
            for model_id, selection in selections.items()
        },
    }
    selection_payload["commit_sha256"] = canonical_sha256(selection_payload)
    selections_path = prepared.run_directory / SELECTIONS_FILENAME
    atomic_json(selection_payload, selections_path)

    training_keys_hash = canonical_frame_sha256(
        context.keys, sort_by=["target_date", "tract_geoid"]
    )
    model_records: dict[str, Any] = {}
    for model_id in config.model_ids:
        selection = selections[model_id]
        candidate = selection.selected_candidate
        spec = make_model_spec(
            context.registry,
            model_id,
            **context.model_selection.factory_kwargs(model_id, candidate.candidate_id),
        )
        fitted = fit_fold_model(spec, context.features, context.target, context.keys)
        if (
            fitted.training_row_count != config.expected_tract_date_rows
            or fitted.training_date_count != config.expected_independent_dates
        ):
            raise FinalModelError("Final fitted training cardinalities drifted.")
        bundle: dict[str, Any] = {
            "schema_version": FINAL_MODEL_SCHEMA_VERSION,
            "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
            "model_id": model_id,
            "candidate_id": candidate.candidate_id,
            "candidate_parameters": candidate.factory_parameters(),
            "random_state": context.model_selection.random_state,
            "feature_names": list(spec.feature_names),
            "training_row_count": config.expected_tract_date_rows,
            "training_date_count": config.expected_independent_dates,
            "training_spatial_block_count": config.expected_independent_spatial_blocks,
            "training_keys_sha256": training_keys_hash,
            "pipeline": fitted.pipeline,
        }
        artifact_path = prepared.run_directory / f"{model_id}_full_development.joblib"
        artifact = atomic_dump_model_bundle(bundle, artifact_path)
        loaded = load_hashed_model_bundle(
            artifact_path,
            expected_sha256=str(artifact["sha256"]),
            expected_bytes=int(artifact["bytes"]),
            expected_model_id=model_id,
            expected_candidate_id=candidate.candidate_id,
        )
        before = predict_fold_model(fitted, context.features)
        after = predict_model_bundle(loaded, context.features)
        if not np.array_equal(before, after):
            raise FinalModelError("Reloaded fitted pipeline predictions changed.")
        prediction_fingerprint = context.keys.copy()
        prediction_fingerprint["y_pred"] = after
        model_records[model_id] = {
            **artifact,
            "selected_candidate_id": candidate.candidate_id,
            "selected_complexity_rank": candidate.complexity_rank,
            "selected_parameters": candidate.factory_parameters(),
            "random_state": context.model_selection.random_state,
            "feature_names": list(spec.feature_names),
            "feature_count": len(spec.feature_names),
            "training_row_count": config.expected_tract_date_rows,
            "training_date_count": config.expected_independent_dates,
            "training_spatial_block_count": config.expected_independent_spatial_blocks,
            "training_keys_sha256": training_keys_hash,
            "reloaded_training_prediction_sha256": canonical_frame_sha256(
                prediction_fingerprint, sort_by=["target_date", "tract_geoid"]
            ),
        }

    score_record = {
        "path": scores_path.name,
        "path_base": "run_directory",
        **parquet_file_record(scores_path, all_scores),
    }
    selection_record = _file_record(selections_path)
    output_files = {
        scores_path.name: score_record,
        selections_path.name: selection_record,
    }
    provenance: dict[str, Any] = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "state": "complete_development_only",
        "ready_for_model_lock_staging": True,
        "ready_for_formal_model_lock": False,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_id": prepared.run_id,
        "context_run_id": context.run_id,
        "development_years": list(config.development_years),
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "final_test_values_read": False,
        "model_ids": list(config.model_ids),
        "baseline_model_id": config.baseline_model_id,
        "primary_model_id": config.primary_model_id,
        "tract_date_row_count": config.expected_tract_date_rows,
        "independent_date_count": config.expected_independent_dates,
        "independent_spatial_block_count": config.expected_independent_spatial_blocks,
        "tuning_fit_count": len(prepared.tasks),
        "tuning_score_row_count": len(all_scores),
        "model_dataset_commit_sha256": context.model_dataset_commit_sha256,
        "split_promotion_commit_sha256": context.split_promotion_commit_sha256,
        "model_selection_commit_sha256": context.model_selection_commit_sha256,
        "selection_config_sha256": context.model_selection.semantic_sha256,
        "analysis_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_locks": dict(input_locks or {
            "context_run_id": context.run_id,
            "feature_registry_semantic_sha256": canonical_frame_sha256(
                context.registry, sort_by=["feature_name"]
            ),
        }),
        "selection_contract": {
            "strategy": "leave_one_development_year_out",
            "primary_metric": "equal_date_weighted_mae_c",
            "exact_candidate_date_coverage": True,
            "tie_absolute_tolerance_c": 1.0e-12,
            "tie_relative_tolerance": 0.0,
            "tie_breakers": ["complexity_rank", "candidate_id"],
        },
        "selections": {
            model_id: _selection_payload(selection)
            for model_id, selection in selections.items()
        },
        "models": model_records,
        "hotspot_contract": dict(config.hotspot_contract),
        "planned_figures": list(config.planned_figures),
        "runtime_sha256": prepared.runtime_sha256,
        "runtime_fingerprint": prepared.runtime_fingerprint,
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, provenance_path)
    authenticated = authenticate_final_build_provenance(provenance_path, load_models=True)
    latest: dict[str, Any] = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "state": "complete_development_only",
        "run_id": prepared.run_id,
        "provenance_path": provenance_path.as_posix(),
        "provenance_sha256": sha256_file(provenance_path),
        "provenance_commit_sha256": authenticated["commit_sha256"],
        "final_test_locked": True,
    }
    latest["commit_sha256"] = canonical_sha256(latest)
    atomic_json(latest, latest_path)
    return authenticated


def build_final_models(
    config_path: str | Path = DEFAULT_FINAL_MODEL_CONFIG,
    *,
    prepare_only: bool = False,
) -> dict[str, Any]:
    """Production entry point using the fail-closed default development context."""

    config = load_final_model_config(config_path)
    context = load_model_run_context()
    prepared = prepare_final_model_build(context, config)
    if prepare_only:
        return prepared.manifest
    return run_final_model_build(
        context,
        config,
        input_locks=collect_default_input_locks(context),
    )
