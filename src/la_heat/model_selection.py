"""Pre-score hyperparameter freeze and deterministic nested-CV selection contract.

This module does not train models or read targets.  It validates the frozen
candidate set and, later, reduces already-computed per-date inner-OOF MAE values
according to the predeclared rule.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

MODEL_IDS = ("B0", "B1", "B2", "M1", "M2")
MODEL_SELECTION_SCHEMA_VERSION = 1
MODEL_SELECTION_ALGORITHM_VERSION = "model-selection-v1"
MODEL_SELECTION_STATE = "frozen_pre_score"
FROZEN_CONFIG_SEMANTIC_SHA256 = (
    "98f0429f3f2daa6f61f2bf260ff284f7fe08cc52487ee6f11abcab05b98fcec0"
)
MODEL_SELECTION_FREEZE_FILENAME = "model_selection_freeze.json"
DEFAULT_MODEL_SELECTION_OUTPUT_DIRECTORY = Path("manifests/model_selection")
SCORE_COLUMNS = ("candidate_id", "target_date", "date_mae_c")
EXPECTED_CANDIDATE_COUNTS = {"B0": 1, "B1": 5, "B2": 5, "M1": 12, "M2": 8}
PARAMETER_ORDER = {
    "B0": (),
    "B1": ("ridge_alpha",),
    "B2": ("ridge_alpha",),
    "M1": ("elastic_alpha", "elastic_l1_ratio"),
    "M2": (
        "hgb_learning_rate",
        "hgb_max_iter",
        "hgb_max_leaf_nodes",
        "hgb_min_samples_leaf",
        "hgb_l2_regularization",
    ),
}
EXPECTED_INNER_CV = {
    "strategy": "leave_one_remaining_calendar_year_out",
    "scope": "outer_train_only",
    "preprocessing_fit_scope": "inner_train_only",
    "refit_scope": "complete_outer_train_only_after_selection",
}
EXPECTED_SELECTION = {
    "primary_metric": "equal_date_weighted_mae_c",
    "score_input_unit": "one_mae_per_independent_validation_date",
    "aggregation": "mean_across_stitched_inner_oof_dates",
    "rule": "minimum_stitched_date_macro_mae",
    "candidate_coverage": "exact_same_validation_dates",
    "tie_breakers": ["complexity_rank", "candidate_id"],
}
_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ModelSelectionAuditError(ValueError):
    """Raised when the frozen candidate or selection contract is violated."""


@dataclass(frozen=True)
class HyperparameterCandidate:
    """One legal, predeclared set of keyword arguments for a model factory."""

    model_id: str
    candidate_id: str
    complexity_rank: int
    parameters: tuple[tuple[str, int | float], ...]

    def factory_parameters(self) -> dict[str, int | float]:
        """Return a fresh dictionary suitable for ``make_model_spec``."""

        return dict(self.parameters)


@dataclass(frozen=True)
class ModelSelectionConfig:
    """Validated, semantically hashed pre-score model-selection settings."""

    path: Path
    semantic_sha256: str
    development_years: tuple[int, ...]
    final_test_year: int
    unlock_final_test: bool
    random_state: int
    tie_absolute_tolerance_c: float
    tie_relative_tolerance: float
    candidates: tuple[HyperparameterCandidate, ...]

    def candidates_for(self, model_id: str) -> tuple[HyperparameterCandidate, ...]:
        if model_id not in MODEL_IDS:
            raise ModelSelectionAuditError(f"Unknown model_id {model_id!r}.")
        return tuple(candidate for candidate in self.candidates if candidate.model_id == model_id)

    def candidate(self, model_id: str, candidate_id: str) -> HyperparameterCandidate:
        matches = [
            candidate
            for candidate in self.candidates_for(model_id)
            if candidate.candidate_id == candidate_id
        ]
        if len(matches) != 1:
            raise ModelSelectionAuditError(
                f"Unknown candidate_id {candidate_id!r} for model {model_id}."
            )
        return matches[0]

    def factory_kwargs(self, model_id: str, candidate_id: str) -> dict[str, int | float]:
        """Return frozen candidate parameters plus the shared deterministic seed."""

        kwargs = self.candidate(model_id, candidate_id).factory_parameters()
        kwargs["random_state"] = self.random_state
        return kwargs


@dataclass(frozen=True)
class CandidateScore:
    """Auditable date-macro score summary for one candidate."""

    candidate_id: str
    mean_date_mae_c: float
    independent_validation_date_count: int
    complexity_rank: int


@dataclass(frozen=True)
class CandidateSelection:
    """Deterministic result of the frozen minimum-MAE and tie-breaking rule."""

    model_id: str
    selected_candidate: HyperparameterCandidate
    ranking: tuple[CandidateScore, ...]
    tied_candidate_ids: tuple[str, ...]
    validation_years: tuple[int, ...]
    independent_validation_date_count: int
    selection_rule: str = "minimum_stitched_date_macro_mae"


def build_model_selection_freeze_manifest(
    config_path: str | Path = "configs/model_selection.toml",
    output_directory: str | Path = DEFAULT_MODEL_SELECTION_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Commit the pre-score configuration as a target- and score-blind artifact."""

    config = load_model_selection_config(config_path)
    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "configs/model_selection.toml",
            "scripts/audit_model_selection_freeze.py",
            "src/la_heat/model_selection.py",
            "src/la_heat/modeling.py",
            "src/la_heat/provenance.py",
            "src/la_heat/validation_splits.py",
        ),
        algorithm_version=MODEL_SELECTION_ALGORITHM_VERSION,
    )
    candidate_counts = {
        model_id: len(config.candidates_for(model_id)) for model_id in MODEL_IDS
    }
    candidates = [
        {
            "model_id": candidate.model_id,
            "candidate_id": candidate.candidate_id,
            "complexity_rank": candidate.complexity_rank,
            "parameters": candidate.factory_parameters(),
        }
        for candidate in config.candidates
    ]
    payload: dict[str, Any] = {
        "schema_version": MODEL_SELECTION_SCHEMA_VERSION,
        "algorithm_version": MODEL_SELECTION_ALGORITHM_VERSION,
        "state": MODEL_SELECTION_STATE,
        "frozen_before_scores": True,
        "target_tables_read": [],
        "score_tables_read": [],
        "models_fitted": False,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": config.unlock_final_test,
        "development_years": list(config.development_years),
        "random_state": config.random_state,
        "config": {
            "path": str(config.path),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "candidate_counts": candidate_counts,
        "candidate_count_total": len(config.candidates),
        "candidates": candidates,
        "inner_cv": EXPECTED_INNER_CV,
        "selection": {
            **EXPECTED_SELECTION,
            "tie_absolute_tolerance_c": config.tie_absolute_tolerance_c,
            "tie_relative_tolerance": config.tie_relative_tolerance,
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "scientific_contract": {
            "outer_split_unit": "grouped whole date, spatial block, or joint cell",
            "inner_split_unit": "whole calendar year inside outer training only",
            "preprocessing_scope": "inner training fold only",
            "selection_unit": "independent validation date",
            "selection_metric": "equal-date-weighted MAE in degrees C",
            "locked_final_test_used": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(payload, output / MODEL_SELECTION_FREEZE_FILENAME)
    return payload


def _exact_keys(payload: dict[str, Any], expected: set[str], *, name: str) -> None:
    observed = set(payload)
    if observed != expected:
        raise ModelSelectionAuditError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(observed)}."
        )


def _integer(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelSelectionAuditError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ModelSelectionAuditError(f"{name} must be in [{minimum}, {maximum}].")
    return value


def _finite_number(
    value: object,
    *,
    name: str,
    minimum: float,
    maximum: float,
    include_minimum: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelSelectionAuditError(f"{name} must be numeric.")
    number = float(value)
    minimum_ok = number >= minimum if include_minimum else number > minimum
    if not np.isfinite(number) or not minimum_ok or number > maximum:
        bracket = "[" if include_minimum else "("
        raise ModelSelectionAuditError(
            f"{name} must be finite and in {bracket}{minimum}, {maximum}]."
        )
    return number


def _validated_parameters(
    model_id: str, parameters: object
) -> tuple[tuple[str, int | float], ...]:
    if not isinstance(parameters, dict):
        raise ModelSelectionAuditError(f"{model_id} candidate parameters must be a table.")
    expected = PARAMETER_ORDER[model_id]
    _exact_keys(parameters, set(expected), name=f"{model_id} candidate parameters")
    normalized: dict[str, int | float] = {}
    if model_id in {"B1", "B2"}:
        normalized["ridge_alpha"] = _finite_number(
            parameters["ridge_alpha"],
            name="ridge_alpha",
            minimum=0.0,
            maximum=1_000_000.0,
            include_minimum=False,
        )
    elif model_id == "M1":
        normalized["elastic_alpha"] = _finite_number(
            parameters["elastic_alpha"],
            name="elastic_alpha",
            minimum=0.0,
            maximum=1_000.0,
            include_minimum=False,
        )
        normalized["elastic_l1_ratio"] = _finite_number(
            parameters["elastic_l1_ratio"],
            name="elastic_l1_ratio",
            minimum=0.0,
            maximum=1.0,
        )
    elif model_id == "M2":
        normalized["hgb_learning_rate"] = _finite_number(
            parameters["hgb_learning_rate"],
            name="hgb_learning_rate",
            minimum=0.0,
            maximum=1.0,
            include_minimum=False,
        )
        normalized["hgb_max_iter"] = _integer(
            parameters["hgb_max_iter"], name="hgb_max_iter", minimum=1, maximum=10_000
        )
        normalized["hgb_max_leaf_nodes"] = _integer(
            parameters["hgb_max_leaf_nodes"],
            name="hgb_max_leaf_nodes",
            minimum=2,
            maximum=255,
        )
        normalized["hgb_min_samples_leaf"] = _integer(
            parameters["hgb_min_samples_leaf"],
            name="hgb_min_samples_leaf",
            minimum=1,
            maximum=1_000_000,
        )
        normalized["hgb_l2_regularization"] = _finite_number(
            parameters["hgb_l2_regularization"],
            name="hgb_l2_regularization",
            minimum=0.0,
            maximum=1_000_000.0,
        )
    return tuple((name, normalized[name]) for name in expected)


def _validated_candidates(models: object) -> tuple[HyperparameterCandidate, ...]:
    if not isinstance(models, dict):
        raise ModelSelectionAuditError("models must be a table.")
    if tuple(models) != MODEL_IDS:
        raise ModelSelectionAuditError(f"Model tables must appear exactly in order {MODEL_IDS}.")
    candidates: list[HyperparameterCandidate] = []
    seen_ids: set[str] = set()
    for model_id in MODEL_IDS:
        model = models[model_id]
        if not isinstance(model, dict):
            raise ModelSelectionAuditError(f"models.{model_id} must be a table.")
        _exact_keys(model, {"factory_model_id", "candidates"}, name=f"models.{model_id}")
        if model["factory_model_id"] != model_id:
            raise ModelSelectionAuditError(f"models.{model_id}.factory_model_id must be exact.")
        raw_candidates = model["candidates"]
        expected_count = EXPECTED_CANDIDATE_COUNTS[model_id]
        if not isinstance(raw_candidates, list) or len(raw_candidates) != expected_count:
            raise ModelSelectionAuditError(
                f"{model_id} must contain exactly {EXPECTED_CANDIDATE_COUNTS[model_id]} candidates."
            )
        model_ranks: list[int] = []
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                raise ModelSelectionAuditError(f"{model_id} candidates must be tables.")
            _exact_keys(
                raw_candidate,
                {"candidate_id", "complexity_rank", "parameters"},
                name=f"{model_id} candidate",
            )
            candidate_id = raw_candidate["candidate_id"]
            if (
                not isinstance(candidate_id, str)
                or not _CANDIDATE_ID.fullmatch(candidate_id)
                or not candidate_id.startswith(f"{model_id}-")
            ):
                raise ModelSelectionAuditError(
                    f"{model_id} candidate_id must be normalized and model-prefixed."
                )
            if candidate_id in seen_ids:
                raise ModelSelectionAuditError(f"Duplicate candidate_id {candidate_id!r}.")
            rank = _integer(
                raw_candidate["complexity_rank"],
                name=f"{candidate_id}.complexity_rank",
                minimum=0,
                maximum=len(raw_candidates) - 1,
            )
            candidates.append(
                HyperparameterCandidate(
                    model_id=model_id,
                    candidate_id=candidate_id,
                    complexity_rank=rank,
                    parameters=_validated_parameters(model_id, raw_candidate["parameters"]),
                )
            )
            seen_ids.add(candidate_id)
            model_ranks.append(rank)
        if sorted(model_ranks) != list(range(len(raw_candidates))):
            raise ModelSelectionAuditError(
                f"{model_id} complexity ranks must be unique and contiguous from zero."
            )
    return tuple(candidates)


def load_model_selection_config(path: str | Path) -> ModelSelectionConfig:
    """Load the only legal pre-score candidate grid and fail on semantic drift."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _exact_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "state",
            "development_years",
            "final_test_year",
            "unlock_final_test",
            "random_state",
            "inner_cv",
            "selection",
            "models",
        },
        name="model-selection configuration",
    )
    if raw["schema_version"] != MODEL_SELECTION_SCHEMA_VERSION:
        raise ModelSelectionAuditError("Unsupported model-selection schema version.")
    if raw["algorithm_version"] != MODEL_SELECTION_ALGORITHM_VERSION:
        raise ModelSelectionAuditError("Unsupported model-selection algorithm version.")
    if raw["state"] != MODEL_SELECTION_STATE:
        raise ModelSelectionAuditError(f"state must remain {MODEL_SELECTION_STATE!r}.")
    development_years = tuple(raw["development_years"])
    if development_years != tuple(range(2020, 2025)):
        raise ModelSelectionAuditError("Development years must be exactly 2020 through 2024.")
    if raw["final_test_year"] != 2025 or raw["unlock_final_test"] is not False:
        raise PermissionError("Calendar year 2025 must remain locked during model selection.")
    if raw["random_state"] != 20260719:
        raise ModelSelectionAuditError("random_state must remain frozen at 20260719.")
    if raw["inner_cv"] != EXPECTED_INNER_CV:
        raise ModelSelectionAuditError("Inner CV must remain whole-year, nested, and fold-local.")
    selection = raw["selection"]
    if not isinstance(selection, dict):
        raise ModelSelectionAuditError("selection must be a table.")
    expected_selection_keys = set(EXPECTED_SELECTION) | {
        "tie_absolute_tolerance_c",
        "tie_relative_tolerance",
    }
    _exact_keys(selection, expected_selection_keys, name="selection")
    for key, expected in EXPECTED_SELECTION.items():
        if selection[key] != expected:
            raise ModelSelectionAuditError(f"selection.{key} must remain {expected!r}.")
    absolute_tolerance = _finite_number(
        selection["tie_absolute_tolerance_c"],
        name="tie_absolute_tolerance_c",
        minimum=0.0,
        maximum=1.0e-9,
    )
    relative_tolerance = _finite_number(
        selection["tie_relative_tolerance"],
        name="tie_relative_tolerance",
        minimum=0.0,
        maximum=0.0,
    )
    candidates = _validated_candidates(raw["models"])
    semantic_sha256 = canonical_sha256(raw)
    if semantic_sha256 != FROZEN_CONFIG_SEMANTIC_SHA256:
        raise ModelSelectionAuditError(
            "Frozen model-selection configuration semantic SHA-256 changed."
        )
    return ModelSelectionConfig(
        path=config_path,
        semantic_sha256=semantic_sha256,
        development_years=development_years,
        final_test_year=2025,
        unlock_final_test=False,
        random_state=20260719,
        tie_absolute_tolerance_c=absolute_tolerance,
        tie_relative_tolerance=relative_tolerance,
        candidates=candidates,
    )


def _civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            raise ModelSelectionAuditError(
                f"target_date at row {position} is numeric, not a civil date."
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ModelSelectionAuditError(
                f"target_date at row {position} is not parseable."
            ) from exc
        if pd.isna(timestamp):
            raise ModelSelectionAuditError(f"target_date at row {position} is missing.")
        if timestamp.tzinfo is not None or timestamp != timestamp.normalize():
            raise ModelSelectionAuditError(
                "Inner validation dates must be timezone-naive civil midnights."
            )
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _validation_dates(
    values: object, config: ModelSelectionConfig
) -> tuple[tuple[pd.Timestamp, ...], tuple[int, ...]]:
    if isinstance(values, (str, bytes)):
        raise ModelSelectionAuditError(
            "expected_validation_dates must be a non-empty date collection."
        )
    try:
        raw_dates = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ModelSelectionAuditError(
            "expected_validation_dates must be a non-empty date collection."
        ) from exc
    if not raw_dates:
        raise ModelSelectionAuditError(
            "expected_validation_dates must be a non-empty date collection."
        )
    parsed = _civil_midnights(pd.Series(raw_dates, dtype="object"))
    if parsed.duplicated().any():
        raise ModelSelectionAuditError(
            "Expected validation dates must be unique."
        )
    dates = tuple(sorted(pd.Timestamp(value) for value in parsed))
    years = tuple(sorted({date.year for date in dates}))
    if any(year >= config.final_test_year for year in years):
        raise PermissionError("Expected validation dates must exclude locked 2025 and later.")
    if not set(years).issubset(config.development_years):
        raise ModelSelectionAuditError(
            "Expected validation dates must fall within the development years."
        )
    return dates, years


def select_candidate(
    config: ModelSelectionConfig,
    model_id: str,
    date_scores: pd.DataFrame,
    *,
    expected_validation_dates: object,
) -> CandidateSelection:
    """Select from per-date inner-OOF MAE without row- or fold-size weighting.

    ``date_scores`` must contain exactly one MAE for every candidate and every
    caller-declared independent validation date.  Requiring the exact date set
    prevents every candidate from silently omitting the same date.  The mean
    therefore gives every physical acquisition date equal weight.
    """

    if not isinstance(config, ModelSelectionConfig):
        raise TypeError("config must be a validated ModelSelectionConfig.")
    if (
        config.semantic_sha256 != FROZEN_CONFIG_SEMANTIC_SHA256
        or config.final_test_year != 2025
        or config.unlock_final_test
    ):
        raise PermissionError("The frozen pre-score configuration and 2025 lock are required.")
    candidates = config.candidates_for(model_id)
    expected_dates, years = _validation_dates(expected_validation_dates, config)
    if not isinstance(date_scores, pd.DataFrame):
        raise TypeError("date_scores must be a pandas DataFrame.")
    if tuple(date_scores.columns) != SCORE_COLUMNS or date_scores.columns.duplicated().any():
        raise ModelSelectionAuditError(
            f"date_scores columns must be exactly {SCORE_COLUMNS} in order."
        )
    if date_scores.empty:
        raise ModelSelectionAuditError("date_scores must not be empty.")
    scores = date_scores.copy()
    scores["target_date"] = _civil_midnights(scores["target_date"])
    observed_years = tuple(
        sorted(int(year) for year in scores["target_date"].dt.year.unique())
    )
    if any(year >= config.final_test_year for year in observed_years):
        raise PermissionError("Locked 2025 or later appeared in inner-validation scores.")
    if not set(observed_years).issubset(config.development_years):
        raise ModelSelectionAuditError(
            "Inner-validation scores must remain within the development years."
        )
    if scores.duplicated(["candidate_id", "target_date"]).any():
        raise ModelSelectionAuditError("Duplicate candidate-date score rows are forbidden.")
    if not scores["candidate_id"].map(lambda value: isinstance(value, str)).all():
        raise ModelSelectionAuditError("candidate_id score values must be strings.")
    expected_ids = {candidate.candidate_id for candidate in candidates}
    observed_ids = set(scores["candidate_id"])
    if observed_ids != expected_ids:
        raise ModelSelectionAuditError(
            "Inner-validation scores must cover every and only frozen candidate."
        )
    if scores["date_mae_c"].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ModelSelectionAuditError("date_mae_c must be numeric, not boolean.")
    try:
        numeric_scores = pd.to_numeric(scores["date_mae_c"], errors="raise").to_numpy(
            dtype=float
        )
    except (TypeError, ValueError) as exc:
        raise ModelSelectionAuditError("date_mae_c must be numeric.") from exc
    if not np.isfinite(numeric_scores).all() or (numeric_scores < 0).any():
        raise ModelSelectionAuditError("date_mae_c must be finite and nonnegative.")
    scores["date_mae_c"] = numeric_scores

    date_sets = {
        candidate_id: tuple(
            sorted(scores.loc[scores["candidate_id"].eq(candidate_id), "target_date"])
        )
        for candidate_id in expected_ids
    }
    if any(dates != expected_dates for dates in date_sets.values()):
        raise ModelSelectionAuditError(
            "Every candidate must cover the exact caller-declared validation dates."
        )

    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    summaries = []
    for candidate_id in sorted(expected_ids):
        candidate_scores = scores.loc[scores["candidate_id"].eq(candidate_id), "date_mae_c"]
        summaries.append(
            CandidateScore(
                candidate_id=candidate_id,
                mean_date_mae_c=float(candidate_scores.mean()),
                independent_validation_date_count=len(candidate_scores),
                complexity_rank=by_id[candidate_id].complexity_rank,
            )
        )
    best_score = min(summary.mean_date_mae_c for summary in summaries)
    threshold = (
        best_score
        + config.tie_absolute_tolerance_c
        + config.tie_relative_tolerance * abs(best_score)
    )
    tied = tuple(
        sorted(
            (summary for summary in summaries if summary.mean_date_mae_c <= threshold),
            key=lambda summary: (summary.complexity_rank, summary.candidate_id),
        )
    )
    selected_id = tied[0].candidate_id
    ranking = tuple(
        sorted(
            summaries,
            key=lambda summary: (
                summary.mean_date_mae_c,
                summary.complexity_rank,
                summary.candidate_id,
            ),
        )
    )
    return CandidateSelection(
        model_id=model_id,
        selected_candidate=by_id[selected_id],
        ranking=ranking,
        tied_candidate_ids=tuple(summary.candidate_id for summary in tied),
        validation_years=years,
        independent_validation_date_count=len(expected_dates),
    )
