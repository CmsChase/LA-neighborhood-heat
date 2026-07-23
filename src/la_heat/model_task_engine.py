"""Pure, deterministic execution engine for nested grouped model evaluation.

The module deliberately contains no persistence, queue, or user-interface code.
Task payloads are JSON-safe descriptions of one scientific fit.  Executors
reconstruct split roles from the frozen fold formula and only then slice model
features and targets, so outer-test and purged values cannot enter inner fitting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from la_heat.model_selection import (
    MODEL_IDS,
    CandidateSelection,
    HyperparameterCandidate,
    ModelSelectionConfig,
    select_candidate,
)
from la_heat.modeling import fit_fold_model, make_model_spec, predict_fold_model
from la_heat.provenance import canonical_frame_sha256, canonical_sha256
from la_heat.validation_splits import FAMILIES, assign_fold_roles, build_inner_cv_roles

TASK_SCHEMA_VERSION = 1
INNER_RESULT_SCHEMA_VERSION = 1
INNER_DATE_SCORE_COLUMNS = ("candidate_id", "target_date", "date_mae_c")
OUTER_PREDICTION_COLUMNS = (
    "tract_geoid",
    "target_date",
    "spatial_block",
    "family",
    "fold_id",
    "model_id",
    "candidate_id",
    "y_true",
    "y_pred",
)
_FAMILY_ORDER = {family: position for position, family in enumerate(FAMILIES)}
_REQUIRED_FOLD_COLUMNS = frozenset(
    {
        "family",
        "fold_index",
        "fold_id",
        "held_out_year",
        "held_out_block",
        "train_row_count",
        "test_row_count",
        "purged_row_count",
        "train_date_count",
        "test_date_count",
        "inner_cv_fold_count",
    }
)


class ModelTaskAuditError(ValueError):
    """Raised when a task, result, or model input violates the nested-CV contract."""


def _normalized_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ModelTaskAuditError(f"{name} must be a non-empty normalized string.")
    return value


def _integer(value: object, *, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ModelTaskAuditError(f"{name} must be an integer.")
    if isinstance(value, (int, np.integer)):
        result = int(value)
    elif (
        isinstance(value, (float, np.floating)) and np.isfinite(value) and float(value).is_integer()
    ):
        result = int(value)
    else:
        raise ModelTaskAuditError(f"{name} must be an integer.")
    if result < minimum:
        raise ModelTaskAuditError(f"{name} must be at least {minimum}.")
    return result


def _optional_year(value: object, *, name: str) -> int | None:
    if value is None or pd.isna(value):
        return None
    return _integer(value, name=name, minimum=1900)


def _optional_text(value: object, *, name: str) -> str | None:
    if value is None or pd.isna(value):
        return None
    return _normalized_text(value, name=name)


def _exact_mapping_keys(payload: Mapping[str, Any], expected: set[str], *, name: str) -> None:
    observed = set(payload)
    if observed != expected:
        raise ModelTaskAuditError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(observed)}."
        )


def _task_id(kind: str, identity: Mapping[str, object]) -> str:
    return f"{kind}-{canonical_sha256({'kind': kind, **identity})}"


def _validate_fold_identity(
    *,
    family: str,
    fold_index: int,
    fold_id: str,
    held_out_year: int | None,
    held_out_block: str | None,
) -> None:
    if family not in FAMILIES:
        raise ModelTaskAuditError(f"Unknown split family {family!r}.")
    _integer(fold_index, name="fold_index")
    _normalized_text(fold_id, name="fold_id")
    if family == "temporal" and (held_out_year is None or held_out_block is not None):
        raise ModelTaskAuditError("Temporal tasks require only held_out_year.")
    if family == "spatial" and (held_out_year is not None or held_out_block is None):
        raise ModelTaskAuditError("Spatial tasks require only held_out_block.")
    if family == "joint" and (held_out_year is None or held_out_block is None):
        raise ModelTaskAuditError("Joint tasks require held_out_year and held_out_block.")


@dataclass(frozen=True)
class InnerFitTask:
    """JSON-safe description of one candidate × inner-validation-year fit."""

    selection_config_sha256: str
    family: str
    fold_index: int
    fold_id: str
    held_out_year: int | None
    held_out_block: str | None
    model_id: str
    candidate_id: str
    validation_year: int
    expected_outer_train_row_count: int
    expected_outer_test_row_count: int
    expected_outer_purged_row_count: int
    expected_outer_train_date_count: int
    expected_outer_test_date_count: int
    expected_inner_fold_count: int
    task_id: str = field(init=False)

    def __post_init__(self) -> None:
        _normalized_text(self.selection_config_sha256, name="selection_config_sha256")
        _validate_fold_identity(
            family=self.family,
            fold_index=self.fold_index,
            fold_id=self.fold_id,
            held_out_year=self.held_out_year,
            held_out_block=self.held_out_block,
        )
        if self.model_id not in MODEL_IDS:
            raise ModelTaskAuditError(f"Unknown model_id {self.model_id!r}.")
        _normalized_text(self.candidate_id, name="candidate_id")
        _integer(self.validation_year, name="validation_year", minimum=1900)
        for name in (
            "expected_outer_train_row_count",
            "expected_outer_test_row_count",
            "expected_outer_train_date_count",
            "expected_outer_test_date_count",
            "expected_inner_fold_count",
        ):
            _integer(getattr(self, name), name=name, minimum=1)
        _integer(
            self.expected_outer_purged_row_count,
            name="expected_outer_purged_row_count",
        )
        identity = {
            "selection_config_sha256": self.selection_config_sha256,
            "family": self.family,
            "fold_id": self.fold_id,
            "model_id": self.model_id,
            "candidate_id": self.candidate_id,
            "validation_year": self.validation_year,
        }
        object.__setattr__(self, "task_id", _task_id("inner", identity))

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-safe payload, including its verified task id."""

        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_kind": "inner",
            "task_id": self.task_id,
            "selection_config_sha256": self.selection_config_sha256,
            "family": self.family,
            "fold_index": self.fold_index,
            "fold_id": self.fold_id,
            "held_out_year": self.held_out_year,
            "held_out_block": self.held_out_block,
            "model_id": self.model_id,
            "candidate_id": self.candidate_id,
            "validation_year": self.validation_year,
            "expected_outer_train_row_count": self.expected_outer_train_row_count,
            "expected_outer_test_row_count": self.expected_outer_test_row_count,
            "expected_outer_purged_row_count": self.expected_outer_purged_row_count,
            "expected_outer_train_date_count": self.expected_outer_train_date_count,
            "expected_outer_test_date_count": self.expected_outer_test_date_count,
            "expected_inner_fold_count": self.expected_inner_fold_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InnerFitTask:
        """Rehydrate a payload and reject altered or stale task ids."""

        expected = {
            "schema_version",
            "task_kind",
            "task_id",
            "selection_config_sha256",
            "family",
            "fold_index",
            "fold_id",
            "held_out_year",
            "held_out_block",
            "model_id",
            "candidate_id",
            "validation_year",
            "expected_outer_train_row_count",
            "expected_outer_test_row_count",
            "expected_outer_purged_row_count",
            "expected_outer_train_date_count",
            "expected_outer_test_date_count",
            "expected_inner_fold_count",
        }
        _exact_mapping_keys(payload, expected, name="inner task payload")
        if payload["schema_version"] != TASK_SCHEMA_VERSION or payload["task_kind"] != "inner":
            raise ModelTaskAuditError("Unsupported inner task payload schema or kind.")
        task = cls(
            selection_config_sha256=_normalized_text(
                payload["selection_config_sha256"], name="selection_config_sha256"
            ),
            family=_normalized_text(payload["family"], name="family"),
            fold_index=_integer(payload["fold_index"], name="fold_index"),
            fold_id=_normalized_text(payload["fold_id"], name="fold_id"),
            held_out_year=_optional_year(payload["held_out_year"], name="held_out_year"),
            held_out_block=_optional_text(payload["held_out_block"], name="held_out_block"),
            model_id=_normalized_text(payload["model_id"], name="model_id"),
            candidate_id=_normalized_text(payload["candidate_id"], name="candidate_id"),
            validation_year=_integer(
                payload["validation_year"], name="validation_year", minimum=1900
            ),
            expected_outer_train_row_count=_integer(
                payload["expected_outer_train_row_count"],
                name="expected_outer_train_row_count",
                minimum=1,
            ),
            expected_outer_test_row_count=_integer(
                payload["expected_outer_test_row_count"],
                name="expected_outer_test_row_count",
                minimum=1,
            ),
            expected_outer_purged_row_count=_integer(
                payload["expected_outer_purged_row_count"],
                name="expected_outer_purged_row_count",
            ),
            expected_outer_train_date_count=_integer(
                payload["expected_outer_train_date_count"],
                name="expected_outer_train_date_count",
                minimum=1,
            ),
            expected_outer_test_date_count=_integer(
                payload["expected_outer_test_date_count"],
                name="expected_outer_test_date_count",
                minimum=1,
            ),
            expected_inner_fold_count=_integer(
                payload["expected_inner_fold_count"],
                name="expected_inner_fold_count",
                minimum=1,
            ),
        )
        if payload["task_id"] != task.task_id:
            raise ModelTaskAuditError("Inner task_id does not match its scientific identity.")
        return task


@dataclass(frozen=True)
class OuterFitTask:
    """JSON-safe description of one selected-candidate outer refit."""

    selection_config_sha256: str
    family: str
    fold_index: int
    fold_id: str
    held_out_year: int | None
    held_out_block: str | None
    model_id: str
    expected_outer_train_row_count: int
    expected_outer_test_row_count: int
    expected_outer_purged_row_count: int
    expected_outer_train_date_count: int
    expected_outer_test_date_count: int
    expected_inner_fold_count: int
    task_id: str = field(init=False)

    def __post_init__(self) -> None:
        _normalized_text(self.selection_config_sha256, name="selection_config_sha256")
        _validate_fold_identity(
            family=self.family,
            fold_index=self.fold_index,
            fold_id=self.fold_id,
            held_out_year=self.held_out_year,
            held_out_block=self.held_out_block,
        )
        if self.model_id not in MODEL_IDS:
            raise ModelTaskAuditError(f"Unknown model_id {self.model_id!r}.")
        for name in (
            "expected_outer_train_row_count",
            "expected_outer_test_row_count",
            "expected_outer_train_date_count",
            "expected_outer_test_date_count",
            "expected_inner_fold_count",
        ):
            _integer(getattr(self, name), name=name, minimum=1)
        _integer(
            self.expected_outer_purged_row_count,
            name="expected_outer_purged_row_count",
        )
        identity = {
            "selection_config_sha256": self.selection_config_sha256,
            "family": self.family,
            "fold_id": self.fold_id,
            "model_id": self.model_id,
        }
        object.__setattr__(self, "task_id", _task_id("outer", identity))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_kind": "outer",
            "task_id": self.task_id,
            "selection_config_sha256": self.selection_config_sha256,
            "family": self.family,
            "fold_index": self.fold_index,
            "fold_id": self.fold_id,
            "held_out_year": self.held_out_year,
            "held_out_block": self.held_out_block,
            "model_id": self.model_id,
            "expected_outer_train_row_count": self.expected_outer_train_row_count,
            "expected_outer_test_row_count": self.expected_outer_test_row_count,
            "expected_outer_purged_row_count": self.expected_outer_purged_row_count,
            "expected_outer_train_date_count": self.expected_outer_train_date_count,
            "expected_outer_test_date_count": self.expected_outer_test_date_count,
            "expected_inner_fold_count": self.expected_inner_fold_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OuterFitTask:
        expected = {
            "schema_version",
            "task_kind",
            "task_id",
            "selection_config_sha256",
            "family",
            "fold_index",
            "fold_id",
            "held_out_year",
            "held_out_block",
            "model_id",
            "expected_outer_train_row_count",
            "expected_outer_test_row_count",
            "expected_outer_purged_row_count",
            "expected_outer_train_date_count",
            "expected_outer_test_date_count",
            "expected_inner_fold_count",
        }
        _exact_mapping_keys(payload, expected, name="outer task payload")
        if payload["schema_version"] != TASK_SCHEMA_VERSION or payload["task_kind"] != "outer":
            raise ModelTaskAuditError("Unsupported outer task payload schema or kind.")
        task = cls(
            selection_config_sha256=_normalized_text(
                payload["selection_config_sha256"], name="selection_config_sha256"
            ),
            family=_normalized_text(payload["family"], name="family"),
            fold_index=_integer(payload["fold_index"], name="fold_index"),
            fold_id=_normalized_text(payload["fold_id"], name="fold_id"),
            held_out_year=_optional_year(payload["held_out_year"], name="held_out_year"),
            held_out_block=_optional_text(payload["held_out_block"], name="held_out_block"),
            model_id=_normalized_text(payload["model_id"], name="model_id"),
            expected_outer_train_row_count=_integer(
                payload["expected_outer_train_row_count"],
                name="expected_outer_train_row_count",
                minimum=1,
            ),
            expected_outer_test_row_count=_integer(
                payload["expected_outer_test_row_count"],
                name="expected_outer_test_row_count",
                minimum=1,
            ),
            expected_outer_purged_row_count=_integer(
                payload["expected_outer_purged_row_count"],
                name="expected_outer_purged_row_count",
            ),
            expected_outer_train_date_count=_integer(
                payload["expected_outer_train_date_count"],
                name="expected_outer_train_date_count",
                minimum=1,
            ),
            expected_outer_test_date_count=_integer(
                payload["expected_outer_test_date_count"],
                name="expected_outer_test_date_count",
                minimum=1,
            ),
            expected_inner_fold_count=_integer(
                payload["expected_inner_fold_count"],
                name="expected_inner_fold_count",
                minimum=1,
            ),
        )
        if payload["task_id"] != task.task_id:
            raise ModelTaskAuditError("Outer task_id does not match its scientific identity.")
        return task


@dataclass(frozen=True)
class TaskPlan:
    """Deterministically ordered inner and outer task payloads."""

    selection_config_sha256: str
    inner_tasks: tuple[InnerFitTask, ...]
    outer_tasks: tuple[OuterFitTask, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "selection_config_sha256": self.selection_config_sha256,
            "inner_task_count": len(self.inner_tasks),
            "outer_task_count": len(self.outer_tasks),
            "inner_tasks": [task.to_dict() for task in self.inner_tasks],
            "outer_tasks": [task.to_dict() for task in self.outer_tasks],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskPlan:
        expected = {
            "schema_version",
            "selection_config_sha256",
            "inner_task_count",
            "outer_task_count",
            "inner_tasks",
            "outer_tasks",
        }
        _exact_mapping_keys(payload, expected, name="task plan")
        if payload["schema_version"] != TASK_SCHEMA_VERSION:
            raise ModelTaskAuditError("Unsupported task-plan schema version.")
        if not isinstance(payload["inner_tasks"], list) or not isinstance(
            payload["outer_tasks"], list
        ):
            raise ModelTaskAuditError("Task-plan task collections must be JSON arrays.")
        inner = tuple(InnerFitTask.from_dict(item) for item in payload["inner_tasks"])
        outer = tuple(OuterFitTask.from_dict(item) for item in payload["outer_tasks"])
        if payload["inner_task_count"] != len(inner) or payload["outer_task_count"] != len(outer):
            raise ModelTaskAuditError("Task-plan recorded counts do not match its payloads.")
        config_sha = _normalized_text(
            payload["selection_config_sha256"], name="selection_config_sha256"
        )
        if any(task.selection_config_sha256 != config_sha for task in (*inner, *outer)):
            raise ModelTaskAuditError("Task-plan payloads disagree on selection config hash.")
        return cls(config_sha, inner, outer)


def _validate_selection_config(config: ModelSelectionConfig) -> None:
    if not isinstance(config, ModelSelectionConfig):
        raise TypeError("model_selection_config must be a validated ModelSelectionConfig.")
    if config.final_test_year != 2025 or config.unlock_final_test:
        raise PermissionError("Calendar year 2025 must remain locked for nested evaluation.")
    if (
        not config.development_years
        or tuple(sorted(set(config.development_years))) != config.development_years
        or any(year >= config.final_test_year for year in config.development_years)
    ):
        raise ModelTaskAuditError("Development years must be unique, ordered, and before 2025.")
    if not config.semantic_sha256:
        raise ModelTaskAuditError("Model-selection configuration hash is missing.")
    candidate_ids = [candidate.candidate_id for candidate in config.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ModelTaskAuditError("Model-selection candidate ids must be globally unique.")
    if any(not config.candidates_for(model_id) for model_id in MODEL_IDS):
        raise ModelTaskAuditError("Every model must have at least one candidate.")


def _fold_validation_years(
    family: str, held_out_year: int | None, config: ModelSelectionConfig
) -> tuple[int, ...]:
    if family == "spatial":
        return config.development_years
    if held_out_year not in config.development_years:
        raise ModelTaskAuditError("Held-out year is outside the development-year contract.")
    return tuple(year for year in config.development_years if year != held_out_year)


def _fold_values(record: Any, config: ModelSelectionConfig) -> dict[str, object]:
    family = _normalized_text(record.family, name="family")
    fold_index = _integer(record.fold_index, name="fold_index")
    fold_id = _normalized_text(record.fold_id, name="fold_id")
    held_out_year = _optional_year(record.held_out_year, name="held_out_year")
    held_out_block = _optional_text(record.held_out_block, name="held_out_block")
    _validate_fold_identity(
        family=family,
        fold_index=fold_index,
        fold_id=fold_id,
        held_out_year=held_out_year,
        held_out_block=held_out_block,
    )
    validation_years = _fold_validation_years(family, held_out_year, config)
    inner_count = _integer(record.inner_cv_fold_count, name="inner_cv_fold_count", minimum=1)
    if inner_count != len(validation_years):
        raise ModelTaskAuditError(
            f"Fold {fold_id} records {inner_count} inner folds but requires "
            f"{len(validation_years)}."
        )
    values: dict[str, object] = {
        "family": family,
        "fold_index": fold_index,
        "fold_id": fold_id,
        "held_out_year": held_out_year,
        "held_out_block": held_out_block,
        "expected_outer_train_row_count": _integer(
            record.train_row_count, name="train_row_count", minimum=1
        ),
        "expected_outer_test_row_count": _integer(
            record.test_row_count, name="test_row_count", minimum=1
        ),
        "expected_outer_purged_row_count": _integer(
            record.purged_row_count, name="purged_row_count"
        ),
        "expected_outer_train_date_count": _integer(
            record.train_date_count, name="train_date_count", minimum=1
        ),
        "expected_outer_test_date_count": _integer(
            record.test_date_count, name="test_date_count", minimum=1
        ),
        "expected_inner_fold_count": inner_count,
        "validation_years": validation_years,
    }
    if family != "joint" and values["expected_outer_purged_row_count"] != 0:
        raise ModelTaskAuditError(f"Non-joint fold {fold_id} cannot record purged rows.")
    return values


def build_task_plan(
    fold_definitions: pd.DataFrame,
    model_selection_config: ModelSelectionConfig,
) -> TaskPlan:
    """Build all deterministic inner fits and selected-candidate outer refits.

    With the frozen LA fold table and candidate grid this yields exactly 55,645
    inner fits and 2,155 outer fits.  Smaller synthetic fold tables naturally
    produce the corresponding Cartesian subset using the same rules.
    """

    _validate_selection_config(model_selection_config)
    if not isinstance(fold_definitions, pd.DataFrame):
        raise TypeError("fold_definitions must be a pandas DataFrame.")
    if fold_definitions.empty or fold_definitions.columns.duplicated().any():
        raise ModelTaskAuditError("Fold definitions must be non-empty with unique columns.")
    missing = _REQUIRED_FOLD_COLUMNS - set(fold_definitions.columns)
    if missing:
        raise ModelTaskAuditError(f"Fold definitions are missing columns: {sorted(missing)}")

    folds = [
        _fold_values(record, model_selection_config) for record in fold_definitions.itertuples()
    ]
    fold_ids = [str(fold["fold_id"]) for fold in folds]
    fold_positions = [(str(fold["family"]), int(fold["fold_index"])) for fold in folds]
    if len(fold_ids) != len(set(fold_ids)) or len(fold_positions) != len(set(fold_positions)):
        raise ModelTaskAuditError("Fold ids and family/fold-index pairs must be unique.")
    folds.sort(
        key=lambda fold: (
            _FAMILY_ORDER[str(fold["family"])],
            int(fold["fold_index"]),
            str(fold["fold_id"]),
        )
    )

    inner_tasks: list[InnerFitTask] = []
    outer_tasks: list[OuterFitTask] = []
    common_names = (
        "family",
        "fold_index",
        "fold_id",
        "held_out_year",
        "held_out_block",
        "expected_outer_train_row_count",
        "expected_outer_test_row_count",
        "expected_outer_purged_row_count",
        "expected_outer_train_date_count",
        "expected_outer_test_date_count",
        "expected_inner_fold_count",
    )
    for fold in folds:
        common = {name: fold[name] for name in common_names}
        for model_id in MODEL_IDS:
            outer_tasks.append(
                OuterFitTask(
                    selection_config_sha256=model_selection_config.semantic_sha256,
                    model_id=model_id,
                    **common,  # type: ignore[arg-type]
                )
            )
            for candidate in model_selection_config.candidates_for(model_id):
                for validation_year in fold["validation_years"]:  # type: ignore[union-attr]
                    inner_tasks.append(
                        InnerFitTask(
                            selection_config_sha256=model_selection_config.semantic_sha256,
                            model_id=model_id,
                            candidate_id=candidate.candidate_id,
                            validation_year=int(validation_year),
                            **common,  # type: ignore[arg-type]
                        )
                    )
    if len({task.task_id for task in inner_tasks}) != len(inner_tasks):
        raise AssertionError("Inner task ids are not globally unique.")
    if len({task.task_id for task in outer_tasks}) != len(outer_tasks):
        raise AssertionError("Outer task ids are not globally unique.")
    return TaskPlan(
        selection_config_sha256=model_selection_config.semantic_sha256,
        inner_tasks=tuple(inner_tasks),
        outer_tasks=tuple(outer_tasks),
    )


def _civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            raise ModelTaskAuditError(f"target_date at row {position} is numeric.")
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ModelTaskAuditError(f"target_date at row {position} is not parseable.") from exc
        if pd.isna(timestamp) or timestamp.tzinfo is not None or timestamp != timestamp.normalize():
            raise ModelTaskAuditError("target_date values must be timezone-naive civil midnights.")
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _validate_model_inputs(
    *,
    row_groups: pd.DataFrame,
    model_frame: pd.DataFrame,
    target: pd.Series,
    config: ModelSelectionConfig,
) -> pd.DataFrame:
    """Validate alignment and target-blind metadata without reading target values."""

    _validate_selection_config(config)
    if not isinstance(row_groups, pd.DataFrame) or not isinstance(model_frame, pd.DataFrame):
        raise TypeError("row_groups and model_frame must be pandas DataFrames.")
    if not isinstance(target, pd.Series):
        raise TypeError("target must be a pandas Series.")
    required = {"tract_geoid", "target_date", "spatial_block", "year"}
    missing = required - set(row_groups.columns)
    if missing:
        raise ModelTaskAuditError(f"row_groups is missing columns: {sorted(missing)}")
    if row_groups.empty or row_groups.columns.duplicated().any():
        raise ModelTaskAuditError("row_groups must be non-empty with unique columns.")
    if model_frame.columns.duplicated().any():
        raise ModelTaskAuditError("model_frame contains duplicate columns.")
    if not row_groups.index.is_unique:
        raise ModelTaskAuditError("row_groups index must be unique.")
    if not row_groups.index.equals(model_frame.index) or not row_groups.index.equals(target.index):
        raise ModelTaskAuditError("row_groups, model_frame, and target indexes must align exactly.")

    groups = row_groups.loc[:, ["tract_geoid", "target_date", "spatial_block", "year"]].copy()
    for column in ("tract_geoid", "spatial_block"):
        valid = groups[column].map(
            lambda value: isinstance(value, str) and bool(value) and value == value.strip()
        )
        if not valid.all():
            raise ModelTaskAuditError(f"{column} values must be normalized strings.")
    groups["target_date"] = _civil_midnights(groups["target_date"])
    if groups.duplicated(["tract_geoid", "target_date"]).any():
        raise ModelTaskAuditError("row_groups contains duplicate tract-date keys.")
    try:
        years = pd.to_numeric(groups["year"], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ModelTaskAuditError("row_groups.year must be integral.") from exc
    if not np.isfinite(years).all() or not np.equal(years, np.floor(years)).all():
        raise ModelTaskAuditError("row_groups.year must be finite and integral.")
    groups["year"] = years.astype(np.int16)
    if not np.array_equal(groups["year"].to_numpy(), groups["target_date"].dt.year.to_numpy()):
        raise ModelTaskAuditError("row_groups.year disagrees with target_date.")
    if (groups["year"] >= config.final_test_year).any():
        raise PermissionError("Locked calendar year 2025 or later appeared in model rows.")
    observed_years = tuple(sorted(int(year) for year in groups["year"].unique()))
    if observed_years != config.development_years:
        raise ModelTaskAuditError(
            f"Model rows must cover development years {config.development_years}; "
            f"got {observed_years}."
        )
    block_counts = groups.groupby("tract_geoid", sort=False)["spatial_block"].nunique()
    if block_counts.ne(1).any():
        raise ModelTaskAuditError("Each tract_geoid must map to exactly one spatial block.")

    present_keys = {"tract_geoid", "target_date"} & set(model_frame.columns)
    if present_keys and present_keys != {"tract_geoid", "target_date"}:
        raise ModelTaskAuditError("model_frame must contain both keys or neither key.")
    if present_keys:
        model_geoids = model_frame["tract_geoid"]
        if not model_geoids.equals(groups["tract_geoid"]):
            raise ModelTaskAuditError("model_frame tract_geoid keys disagree with row_groups.")
        model_dates = _civil_midnights(model_frame["target_date"])
        if not model_dates.equals(groups["target_date"]):
            raise ModelTaskAuditError("model_frame target_date keys disagree with row_groups.")
    if "spatial_block" in model_frame and not model_frame["spatial_block"].equals(
        groups["spatial_block"]
    ):
        raise ModelTaskAuditError("model_frame spatial_block values disagree with row_groups.")
    return groups


def _coerce_inner_task(task: InnerFitTask | Mapping[str, Any]) -> InnerFitTask:
    return task if isinstance(task, InnerFitTask) else InnerFitTask.from_dict(task)


def _coerce_outer_task(task: OuterFitTask | Mapping[str, Any]) -> OuterFitTask:
    return task if isinstance(task, OuterFitTask) else OuterFitTask.from_dict(task)


def _task_config_guard(task: InnerFitTask | OuterFitTask, config: ModelSelectionConfig) -> None:
    if task.selection_config_sha256 != config.semantic_sha256:
        raise ModelTaskAuditError("Task was generated from a different selection config.")
    if task.held_out_year is not None and task.held_out_year >= config.final_test_year:
        raise PermissionError("Task attempts to hold out locked 2025 or later.")


def _buffered_geoids(
    task: InnerFitTask | OuterFitTask,
    spatial_buffer_geoids: pd.DataFrame | Mapping[str, Iterable[str]] | None,
) -> frozenset[str]:
    if task.family != "joint":
        return frozenset()
    if spatial_buffer_geoids is None:
        raise ModelTaskAuditError("Joint task requires the frozen spatial-buffer table.")
    if isinstance(spatial_buffer_geoids, pd.DataFrame):
        required = {"held_out_block", "tract_geoid"}
        missing = required - set(spatial_buffer_geoids.columns)
        if missing or spatial_buffer_geoids.columns.duplicated().any():
            raise ModelTaskAuditError(f"Spatial-buffer table is missing columns: {sorted(missing)}")
        if spatial_buffer_geoids.duplicated(["held_out_block", "tract_geoid"]).any():
            raise ModelTaskAuditError("Spatial-buffer table contains duplicate block/GEOID rows.")
        values = spatial_buffer_geoids.loc[
            spatial_buffer_geoids["held_out_block"].eq(task.held_out_block), "tract_geoid"
        ].tolist()
    elif isinstance(spatial_buffer_geoids, Mapping):
        values = list(spatial_buffer_geoids.get(str(task.held_out_block), ()))
    else:
        raise TypeError("spatial_buffer_geoids must be a DataFrame, mapping, or None.")
    if not values or any(
        not isinstance(value, str) or not value or value != value.strip() for value in values
    ):
        raise ModelTaskAuditError("Joint task requires normalized GEOIDs in a non-empty buffer.")
    if len(values) != len(set(values)):
        raise ModelTaskAuditError("Joint task buffer contains duplicate GEOIDs.")
    return frozenset(values)


def _outer_roles(
    task: InnerFitTask | OuterFitTask,
    groups: pd.DataFrame,
    spatial_buffer_geoids: pd.DataFrame | Mapping[str, Iterable[str]] | None,
) -> pd.Series:
    roles = assign_fold_roles(
        groups,
        family=task.family,
        held_out_year=task.held_out_year,
        held_out_block=task.held_out_block,
        buffered_geoids=_buffered_geoids(task, spatial_buffer_geoids),
    )
    observed_counts = roles.value_counts().to_dict()
    expected_counts = {
        "train": task.expected_outer_train_row_count,
        "test": task.expected_outer_test_row_count,
        "purged": task.expected_outer_purged_row_count,
    }
    if any(int(observed_counts.get(role, 0)) != count for role, count in expected_counts.items()):
        raise ModelTaskAuditError(
            f"Outer role counts disagree with task payload: observed={observed_counts}, "
            f"expected={expected_counts}."
        )
    train_dates = int(groups.loc[roles.eq("train"), "target_date"].nunique())
    test_dates = int(groups.loc[roles.eq("test"), "target_date"].nunique())
    if (
        train_dates != task.expected_outer_train_date_count
        or test_dates != task.expected_outer_test_date_count
    ):
        raise ModelTaskAuditError("Outer independent-date coverage disagrees with task payload.")
    return roles


def _keys(groups: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    return groups.loc[mask, ["tract_geoid", "target_date"]].copy()


def _finite_target(target: pd.Series, mask: pd.Series, *, role: str) -> pd.Series:
    selected = target.loc[mask]
    try:
        numeric = pd.to_numeric(selected, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ModelTaskAuditError(f"{role} target values must be numeric.") from exc
    values = numeric.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(values).all():
        raise ModelTaskAuditError(f"{role} target values must be finite and complete.")
    return pd.Series(values, index=selected.index, name=target.name, dtype=float)


@dataclass(frozen=True)
class InnerFitAudit:
    """JSON-safe evidence about the exact rows inspected by one inner fit."""

    task_id: str
    outer_train_row_count: int
    outer_test_row_count: int
    outer_purged_row_count: int
    inner_train_row_count: int
    inner_validation_row_count: int
    outer_excluded_row_count: int
    inner_train_date_count: int
    inner_validation_date_count: int
    inner_train_years: tuple[int, ...]
    validation_year: int
    inner_train_spatial_block_count: int
    inner_validation_spatial_block_count: int
    model_feature_count: int
    training_keys_sha256: str
    validation_keys_sha256: str
    outer_excluded_feature_values_read: bool = False
    outer_excluded_target_values_read: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = vars(self).copy()
        payload["inner_train_years"] = list(self.inner_train_years)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InnerFitAudit:
        expected = set(cls.__dataclass_fields__)
        _exact_mapping_keys(payload, expected, name="inner fit audit")
        if (
            payload["outer_excluded_feature_values_read"] is not False
            or payload["outer_excluded_target_values_read"] is not False
        ):
            raise ModelTaskAuditError("Inner audit reports access to outer-excluded values.")
        years = payload["inner_train_years"]
        if not isinstance(years, list):
            raise ModelTaskAuditError("inner_train_years must be a JSON array.")
        return cls(
            task_id=_normalized_text(payload["task_id"], name="task_id"),
            outer_train_row_count=_integer(
                payload["outer_train_row_count"], name="outer_train_row_count", minimum=1
            ),
            outer_test_row_count=_integer(
                payload["outer_test_row_count"], name="outer_test_row_count", minimum=1
            ),
            outer_purged_row_count=_integer(
                payload["outer_purged_row_count"], name="outer_purged_row_count"
            ),
            inner_train_row_count=_integer(
                payload["inner_train_row_count"], name="inner_train_row_count", minimum=1
            ),
            inner_validation_row_count=_integer(
                payload["inner_validation_row_count"],
                name="inner_validation_row_count",
                minimum=1,
            ),
            outer_excluded_row_count=_integer(
                payload["outer_excluded_row_count"], name="outer_excluded_row_count", minimum=1
            ),
            inner_train_date_count=_integer(
                payload["inner_train_date_count"], name="inner_train_date_count", minimum=1
            ),
            inner_validation_date_count=_integer(
                payload["inner_validation_date_count"],
                name="inner_validation_date_count",
                minimum=1,
            ),
            inner_train_years=tuple(
                _integer(year, name="inner_train_year", minimum=1900) for year in years
            ),
            validation_year=_integer(
                payload["validation_year"], name="validation_year", minimum=1900
            ),
            inner_train_spatial_block_count=_integer(
                payload["inner_train_spatial_block_count"],
                name="inner_train_spatial_block_count",
                minimum=1,
            ),
            inner_validation_spatial_block_count=_integer(
                payload["inner_validation_spatial_block_count"],
                name="inner_validation_spatial_block_count",
                minimum=1,
            ),
            model_feature_count=_integer(
                payload["model_feature_count"], name="model_feature_count", minimum=1
            ),
            training_keys_sha256=_normalized_text(
                payload["training_keys_sha256"], name="training_keys_sha256"
            ),
            validation_keys_sha256=_normalized_text(
                payload["validation_keys_sha256"], name="validation_keys_sha256"
            ),
            outer_excluded_feature_values_read=False,
            outer_excluded_target_values_read=False,
        )


def _validated_date_scores(task: InnerFitTask, scores: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(scores, pd.DataFrame):
        raise TypeError("date_scores must be a pandas DataFrame.")
    if tuple(scores.columns) != INNER_DATE_SCORE_COLUMNS or scores.columns.duplicated().any():
        raise ModelTaskAuditError(
            f"date_scores columns must be exactly {INNER_DATE_SCORE_COLUMNS}."
        )
    if scores.empty or not scores["candidate_id"].eq(task.candidate_id).all():
        raise ModelTaskAuditError("Inner scores must be non-empty and match task candidate_id.")
    result = scores.copy()
    result["target_date"] = _civil_midnights(result["target_date"])
    if result["target_date"].dt.year.ne(task.validation_year).any():
        raise ModelTaskAuditError("Inner score date falls outside its validation year.")
    if result["target_date"].duplicated().any():
        raise ModelTaskAuditError("Inner result contains duplicate validation dates.")
    try:
        values = pd.to_numeric(result["date_mae_c"], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ModelTaskAuditError("date_mae_c must be numeric.") from exc
    if not np.isfinite(values).all() or (values < 0).any():
        raise ModelTaskAuditError("date_mae_c must be finite and nonnegative.")
    result["date_mae_c"] = values
    return result.sort_values("target_date", kind="stable").reset_index(drop=True)


@dataclass(frozen=True)
class InnerFitResult:
    """Per-date validation MAE and audit record for one inner task."""

    task: InnerFitTask
    date_scores: pd.DataFrame
    audit: InnerFitAudit

    def __post_init__(self) -> None:
        if self.audit.task_id != self.task.task_id:
            raise ModelTaskAuditError("Inner result task and audit ids disagree.")
        object.__setattr__(self, "date_scores", _validated_date_scores(self.task, self.date_scores))

    def to_dict(self) -> dict[str, object]:
        records = []
        for record in self.date_scores.to_dict("records"):
            records.append(
                {
                    "candidate_id": str(record["candidate_id"]),
                    "target_date": pd.Timestamp(record["target_date"]).strftime("%Y-%m-%d"),
                    "date_mae_c": float(record["date_mae_c"]),
                }
            )
        return {
            "schema_version": INNER_RESULT_SCHEMA_VERSION,
            "task": self.task.to_dict(),
            "date_scores": records,
            "audit": self.audit.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InnerFitResult:
        _exact_mapping_keys(
            payload,
            {"schema_version", "task", "date_scores", "audit"},
            name="inner fit result",
        )
        if payload["schema_version"] != INNER_RESULT_SCHEMA_VERSION:
            raise ModelTaskAuditError("Unsupported inner-result schema version.")
        if not isinstance(payload["task"], Mapping) or not isinstance(payload["audit"], Mapping):
            raise ModelTaskAuditError("Inner-result task and audit must be JSON objects.")
        if not isinstance(payload["date_scores"], list):
            raise ModelTaskAuditError("Inner-result date_scores must be a JSON array.")
        return cls(
            task=InnerFitTask.from_dict(payload["task"]),
            date_scores=pd.DataFrame.from_records(
                payload["date_scores"], columns=list(INNER_DATE_SCORE_COLUMNS)
            ),
            audit=InnerFitAudit.from_dict(payload["audit"]),
        )


def run_inner_fit(
    task: InnerFitTask | Mapping[str, Any],
    *,
    row_groups: pd.DataFrame,
    model_frame: pd.DataFrame,
    target: pd.Series,
    registry: pd.DataFrame,
    model_selection_config: ModelSelectionConfig,
    spatial_buffer_geoids: pd.DataFrame | Mapping[str, Iterable[str]] | None = None,
) -> InnerFitResult:
    """Fit one inner task and return one tract-macro MAE per validation date."""

    inner_task = _coerce_inner_task(task)
    groups = _validate_model_inputs(
        row_groups=row_groups,
        model_frame=model_frame,
        target=target,
        config=model_selection_config,
    )
    _task_config_guard(inner_task, model_selection_config)
    candidate = model_selection_config.candidate(inner_task.model_id, inner_task.candidate_id)
    expected_years = _fold_validation_years(
        inner_task.family, inner_task.held_out_year, model_selection_config
    )
    if inner_task.validation_year not in expected_years:
        raise ModelTaskAuditError("Task validation year is not legal inside this outer fold.")

    outer_roles = _outer_roles(inner_task, groups, spatial_buffer_geoids)
    inner_roles = build_inner_cv_roles(groups, outer_roles)
    if (
        len(inner_roles) != inner_task.expected_inner_fold_count
        or tuple(inner_roles) != expected_years
    ):
        raise ModelTaskAuditError("Reconstructed inner-year coverage disagrees with task payload.")
    roles = inner_roles[inner_task.validation_year]
    train_mask = roles.eq("train")
    validation_mask = roles.eq("validation")
    outer_excluded_mask = roles.eq("outer_excluded")
    if not (
        train_mask.any()
        and validation_mask.any()
        and int(train_mask.sum() + validation_mask.sum() + outer_excluded_mask.sum()) == len(groups)
    ):
        raise ModelTaskAuditError("Inner roles are empty or non-exhaustive.")
    if not outer_excluded_mask.equals(~outer_roles.eq("train")):
        raise ModelTaskAuditError("Outer test/purged rows were not fully excluded from inner CV.")

    train_keys = _keys(groups, train_mask)
    validation_keys = _keys(groups, validation_mask)
    train_target = _finite_target(target, train_mask, role="Inner-training")
    validation_target = _finite_target(target, validation_mask, role="Inner-validation")
    spec = make_model_spec(
        registry,
        inner_task.model_id,
        **model_selection_config.factory_kwargs(inner_task.model_id, candidate.candidate_id),
    )
    fitted = fit_fold_model(
        spec,
        model_frame.loc[train_mask],
        train_target,
        train_keys,
    )
    predictions = predict_fold_model(fitted, model_frame.loc[validation_mask])
    scored = validation_keys.copy()
    scored["absolute_error_c"] = np.abs(predictions - validation_target.to_numpy(dtype=float))
    date_scores = (
        scored.groupby("target_date", sort=True, as_index=False)["absolute_error_c"]
        .mean()
        .rename(columns={"absolute_error_c": "date_mae_c"})
    )
    date_scores.insert(0, "candidate_id", candidate.candidate_id)
    date_scores = date_scores.loc[:, list(INNER_DATE_SCORE_COLUMNS)]
    expected_validation_dates = int(groups.loc[validation_mask, "target_date"].nunique())
    if len(date_scores) != expected_validation_dates:
        raise AssertionError("Inner date-score coverage is incomplete.")

    audit = InnerFitAudit(
        task_id=inner_task.task_id,
        outer_train_row_count=int(outer_roles.eq("train").sum()),
        outer_test_row_count=int(outer_roles.eq("test").sum()),
        outer_purged_row_count=int(outer_roles.eq("purged").sum()),
        inner_train_row_count=int(train_mask.sum()),
        inner_validation_row_count=int(validation_mask.sum()),
        outer_excluded_row_count=int(outer_excluded_mask.sum()),
        inner_train_date_count=int(train_keys["target_date"].nunique()),
        inner_validation_date_count=expected_validation_dates,
        inner_train_years=tuple(
            sorted(int(year) for year in groups.loc[train_mask, "year"].unique())
        ),
        validation_year=inner_task.validation_year,
        inner_train_spatial_block_count=int(groups.loc[train_mask, "spatial_block"].nunique()),
        inner_validation_spatial_block_count=int(
            groups.loc[validation_mask, "spatial_block"].nunique()
        ),
        model_feature_count=len(spec.feature_names),
        training_keys_sha256=canonical_frame_sha256(
            train_keys,
            sort_by=["target_date", "tract_geoid"],
        ),
        validation_keys_sha256=canonical_frame_sha256(
            validation_keys,
            sort_by=["target_date", "tract_geoid"],
        ),
    )
    if fitted.training_row_count != audit.inner_train_row_count:
        raise AssertionError("Fitted model training-row audit is inconsistent.")
    return InnerFitResult(task=inner_task, date_scores=date_scores, audit=audit)


def select_outer_candidate(
    inner_results: Iterable[InnerFitResult | Mapping[str, Any]],
    model_selection_config: ModelSelectionConfig,
) -> CandidateSelection:
    """Stitch exact candidate-date inner scores for one outer fold and select."""

    _validate_selection_config(model_selection_config)
    results = tuple(
        result if isinstance(result, InnerFitResult) else InnerFitResult.from_dict(result)
        for result in inner_results
    )
    if not results:
        raise ModelTaskAuditError("At least one inner result is required for selection.")
    first = results[0].task
    identity = (
        first.selection_config_sha256,
        first.family,
        first.fold_id,
        first.held_out_year,
        first.held_out_block,
        first.model_id,
    )
    if any(
        (
            result.task.selection_config_sha256,
            result.task.family,
            result.task.fold_id,
            result.task.held_out_year,
            result.task.held_out_block,
            result.task.model_id,
        )
        != identity
        for result in results
    ):
        raise ModelTaskAuditError("Candidate selection may combine only one outer fold/model.")
    _task_config_guard(first, model_selection_config)
    expected_years = _fold_validation_years(
        first.family, first.held_out_year, model_selection_config
    )
    expected_candidates = model_selection_config.candidates_for(first.model_id)
    expected_pairs = {
        (candidate.candidate_id, year)
        for candidate in expected_candidates
        for year in expected_years
    }
    observed_pairs = {(result.task.candidate_id, result.task.validation_year) for result in results}
    if len(observed_pairs) != len(results) or observed_pairs != expected_pairs:
        raise ModelTaskAuditError(
            "Inner results must cover every candidate × validation-year exactly once."
        )
    frames = []
    for result in results:
        scores = _validated_date_scores(result.task, result.date_scores)
        if len(scores) != result.audit.inner_validation_date_count:
            raise ModelTaskAuditError("Inner score dates disagree with recorded audit coverage.")
        frames.append(scores)
    stitched = pd.concat(frames, ignore_index=True).sort_values(
        ["candidate_id", "target_date"], kind="stable"
    )
    if stitched.duplicated(["candidate_id", "target_date"]).any():
        raise ModelTaskAuditError("Stitched inner results duplicate a candidate/date score.")
    first_candidate_id = expected_candidates[0].candidate_id
    expected_dates = stitched.loc[
        stitched["candidate_id"].eq(first_candidate_id), "target_date"
    ].tolist()
    return select_candidate(
        model_selection_config,
        first.model_id,
        stitched.loc[:, list(INNER_DATE_SCORE_COLUMNS)].reset_index(drop=True),
        expected_validation_dates=expected_dates,
    )


def _selected_candidate(
    selected: HyperparameterCandidate | CandidateSelection,
) -> HyperparameterCandidate:
    if isinstance(selected, CandidateSelection):
        return selected.selected_candidate
    if isinstance(selected, HyperparameterCandidate):
        return selected
    raise TypeError("selected_candidate must be a candidate or CandidateSelection.")


def run_outer_fit(
    task: OuterFitTask | Mapping[str, Any],
    selected_candidate: HyperparameterCandidate | CandidateSelection,
    *,
    row_groups: pd.DataFrame,
    model_frame: pd.DataFrame,
    target: pd.Series,
    registry: pd.DataFrame,
    model_selection_config: ModelSelectionConfig,
    spatial_buffer_geoids: pd.DataFrame | Mapping[str, Iterable[str]] | None = None,
    feature_families: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Refit a selected candidate on complete outer train and predict only test."""

    outer_task = _coerce_outer_task(task)
    groups = _validate_model_inputs(
        row_groups=row_groups,
        model_frame=model_frame,
        target=target,
        config=model_selection_config,
    )
    _task_config_guard(outer_task, model_selection_config)
    candidate = _selected_candidate(selected_candidate)
    frozen_candidate = model_selection_config.candidate(outer_task.model_id, candidate.candidate_id)
    if candidate != frozen_candidate or candidate.model_id != outer_task.model_id:
        raise ModelTaskAuditError("Selected candidate is not the exact frozen task candidate.")

    outer_roles = _outer_roles(outer_task, groups, spatial_buffer_geoids)
    inner_roles = build_inner_cv_roles(groups, outer_roles)
    expected_years = _fold_validation_years(
        outer_task.family, outer_task.held_out_year, model_selection_config
    )
    if (
        len(inner_roles) != outer_task.expected_inner_fold_count
        or tuple(inner_roles) != expected_years
    ):
        raise ModelTaskAuditError("Outer task inner-year coverage disagrees with its payload.")
    train_mask = outer_roles.eq("train")
    test_mask = outer_roles.eq("test")
    if not train_mask.any() or not test_mask.any() or (train_mask & test_mask).any():
        raise ModelTaskAuditError("Outer train/test roles must be non-empty and disjoint.")
    train_keys = _keys(groups, train_mask)
    train_target = _finite_target(target, train_mask, role="Outer-training")
    test_target = _finite_target(target, test_mask, role="Outer-test")
    spec = make_model_spec(
        registry,
        outer_task.model_id,
        feature_families=feature_families,
        **model_selection_config.factory_kwargs(outer_task.model_id, candidate.candidate_id),
    )
    fitted = fit_fold_model(
        spec,
        model_frame.loc[train_mask],
        train_target,
        train_keys,
    )
    predictions = predict_fold_model(fitted, model_frame.loc[test_mask])

    result = groups.loc[test_mask, ["tract_geoid", "target_date", "spatial_block"]].copy()
    result["family"] = outer_task.family
    result["fold_id"] = outer_task.fold_id
    result["model_id"] = outer_task.model_id
    result["candidate_id"] = candidate.candidate_id
    result["y_true"] = test_target.to_numpy(dtype=float)
    result["y_pred"] = predictions
    result = (
        result.loc[:, list(OUTER_PREDICTION_COLUMNS)]
        .sort_values(["target_date", "tract_geoid"], kind="stable")
        .reset_index(drop=True)
    )
    if len(result) != outer_task.expected_outer_test_row_count:
        raise AssertionError("Outer prediction row coverage is incomplete.")
    if result.duplicated(["tract_geoid", "target_date"]).any():
        raise AssertionError("Outer predictions contain duplicate tract-date keys.")
    if (result["target_date"].dt.year >= model_selection_config.final_test_year).any():
        raise PermissionError("Outer predictions unexpectedly include locked 2025 or later.")
    expected_test_keys = _keys(groups, test_mask)
    observed_test_keys = result.loc[:, ["tract_geoid", "target_date"]]
    if canonical_frame_sha256(
        expected_test_keys, sort_by=["target_date", "tract_geoid"]
    ) != canonical_frame_sha256(observed_test_keys, sort_by=["target_date", "tract_geoid"]):
        raise AssertionError("Outer predictions do not cover the exact test keys.")
    if not np.isfinite(result[["y_true", "y_pred"]].to_numpy(dtype=float)).all():
        raise AssertionError("Outer predictions or labels are non-finite.")
    return result
