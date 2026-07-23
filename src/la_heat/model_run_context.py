"""Fail-closed loader for the frozen development-model run context.

The loader is deliberately the only bridge between committed Phase 2 artifacts
and model fitting.  It authenticates every controlling manifest before reading
Parquet data, rechecks the grouped OOF contract, and returns all model arrays in
the exact row order frozen by ``row_groups.parquet``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.model_dataset import (
    PRIMARY_KEYS,
    TARGET_COLUMN,
    extract_registered_model_data,
)
from la_heat.model_runtime import modeling_runtime_fingerprint
from la_heat.model_selection import (
    EXPECTED_CANDIDATE_COUNTS,
    MODEL_IDS,
    MODEL_SELECTION_STATE,
    ModelSelectionConfig,
    load_model_selection_config,
)
from la_heat.portable_relocation import (
    PortableRelocation,
    PortableRelocationError,
    load_portable_relocation,
)
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.validation_splits import FAMILIES, validate_oof_coverage

MODEL_RUN_CONTEXT_ALGORITHM_VERSION: Final = "model-run-context-v2"
EXPECTED_PRODUCTION_FOLD_COUNT: Final = 431

DEFAULT_MODEL_DIRECTORY: Final = Path("data/processed/model_dataset")
DEFAULT_MODEL_PROVENANCE_PATH: Final = (
    DEFAULT_MODEL_DIRECTORY / "model_dataset_provenance.json"
)
DEFAULT_MODEL_TABLE_PATH: Final = (
    DEFAULT_MODEL_DIRECTORY / "development_model_table.parquet"
)
DEFAULT_REGISTRY_PATH: Final = DEFAULT_MODEL_DIRECTORY / "feature_registry.csv"
DEFAULT_SPLIT_DIRECTORY: Final = Path("manifests/validation_splits")
DEFAULT_SPLIT_PROMOTION_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "split_promotion.json"
DEFAULT_ROW_GROUPS_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "row_groups.parquet"
DEFAULT_FOLD_DEFINITIONS_PATH: Final = (
    DEFAULT_SPLIT_DIRECTORY / "fold_definitions.csv"
)
DEFAULT_SPATIAL_BUFFERS_PATH: Final = (
    DEFAULT_SPLIT_DIRECTORY / "spatial_buffer_geoids.parquet"
)
DEFAULT_MODEL_SELECTION_FREEZE_PATH: Final = Path(
    "manifests/model_selection/model_selection_freeze.json"
)
DEFAULT_MODEL_SELECTION_CONFIG_PATH: Final = Path("configs/model_selection.toml")


class ModelRunContextError(ValueError):
    """Raised when any committed model-run input fails closed."""


@dataclass(frozen=True)
class ModelRunContext:
    """Authenticated, row-aligned inputs for grouped model evaluation."""

    run_id: str
    model_dataset_commit_sha256: str
    split_promotion_commit_sha256: str
    model_selection_commit_sha256: str
    runtime_fingerprint_sha256: str
    dataset: pd.DataFrame
    registry: pd.DataFrame
    row_groups: pd.DataFrame
    fold_definitions: pd.DataFrame
    spatial_buffer_geoids: pd.DataFrame
    features: pd.DataFrame
    target: pd.Series
    keys: pd.DataFrame
    audit_only: pd.DataFrame
    model_selection: ModelSelectionConfig
    portable_relocation_commit_sha256: str | None = None


def _resolve(project_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelRunContextError(f"Cannot read committed JSON input {path}.") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"JSON input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise ModelRunContextError(f"Committed JSON input must be an object: {path}")
    return payload, before


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise ModelRunContextError(f"{label} has an invalid canonical commit hash.")
    return recorded


def _record_for_path(
    record: object,
    *,
    requested_path: Path,
    original_path: Path | None = None,
    label: str,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ModelRunContextError(f"{label} lacks a file lock.")
    try:
        recorded_path = Path(str(record["path"])).resolve()
        expected_sha256 = str(record["sha256"])
        expected_bytes = int(record["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRunContextError(f"{label} has an incomplete file lock.") from error
    expected_recorded_path = requested_path if original_path is None else original_path
    if recorded_path != expected_recorded_path:
        raise ModelRunContextError(f"{label} path lock failed.")
    if (
        not requested_path.is_file()
        or requested_path.stat().st_size != expected_bytes
        or sha256_file(requested_path) != expected_sha256
    ):
        raise ModelRunContextError(f"{label} byte lock failed.")
    return dict(record)


def _validate_model_provenance(
    payload: dict[str, Any],
    *,
    model_path: Path,
    registry_path: Path,
    original_model_path: Path | None = None,
    original_registry_path: Path | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    commit = _verify_commit(payload, label="Model dataset provenance")
    if (
        payload.get("state") != "complete"
        or payload.get("ready_for_modeling") is not True
        or payload.get("phase2_feature_commit_verified") is not True
        or payload.get("model_scores_read") is not False
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_unlocked") is not False
        or payload.get("contains_final_test_year") is not False
    ):
        raise ModelRunContextError(
            "Model dataset is not a complete, score-free, locked-2025 input."
        )
    outputs = payload.get("output_files")
    if not isinstance(outputs, dict):
        raise ModelRunContextError("Model provenance lacks output file locks.")
    table_record = _record_for_path(
        outputs.get(model_path.name),
        requested_path=model_path,
        original_path=original_model_path,
        label="Model table",
    )
    registry_record = _record_for_path(
        outputs.get(registry_path.name),
        requested_path=registry_path,
        original_path=original_registry_path,
        label="Feature registry",
    )
    return commit, table_record, registry_record


def _one_oof_per_row(payload: object, *, expected_fold_counts: dict[str, int]) -> bool:
    if not isinstance(payload, dict) or set(payload) != set(FAMILIES):
        return False
    for family in FAMILIES:
        row = payload.get(family)
        if not isinstance(row, dict):
            return False
        if row != {
            "fold_count": expected_fold_counts[family],
            "minimum_test_assignments_per_row": 1,
            "maximum_test_assignments_per_row": 1,
        }:
            return False
    return True


def _validate_split_promotion(
    payload: dict[str, Any],
    *,
    model_commit: str,
    model_provenance_path: Path,
    model_provenance_file_sha256: str,
    model_path: Path,
    model_record: dict[str, Any],
    row_groups_path: Path,
    fold_definitions_path: Path,
    buffers_path: Path,
    expected_fold_count_total: int,
    original_model_provenance_path: Path | None = None,
    original_model_path: Path | None = None,
    original_row_groups_path: Path | None = None,
    original_fold_definitions_path: Path | None = None,
    original_buffers_path: Path | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    commit = _verify_commit(payload, label="Validation split promotion")
    fold_counts = payload.get("fold_counts")
    if not isinstance(fold_counts, dict):
        raise ModelRunContextError("Split promotion lacks fold counts.")
    try:
        normalized_counts = {family: int(fold_counts[family]) for family in FAMILIES}
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRunContextError("Split promotion fold counts are invalid.") from error
    if (
        payload.get("state") != "promoted"
        or payload.get("phase_complete") is not True
        or payload.get("ready_for_model_evaluation") is not True
        or payload.get("target_values_read") is not False
        or payload.get("predictor_values_read") is not False
        or payload.get("model_scores_read") is not False
        or payload.get("columns_read_from_model_dataset") != list(PRIMARY_KEYS)
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_locked") is not True
        or payload.get("contains_final_test_year") is not False
        or int(payload.get("fold_count_total", -1)) != expected_fold_count_total
        or sum(normalized_counts.values()) != expected_fold_count_total
        or not _one_oof_per_row(
            payload.get("oof_coverage_audit"),
            expected_fold_counts=normalized_counts,
        )
    ):
        raise ModelRunContextError(
            "Validation splits are not the promoted, score-free, one-OOF-per-row contract."
        )

    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ModelRunContextError("Split promotion lacks input locks.")
    model_provenance_lock = inputs.get("model_dataset_provenance")
    if not isinstance(model_provenance_lock, dict):
        raise ModelRunContextError("Split promotion lacks the model-provenance lock.")
    try:
        locked_model_commit = str(model_provenance_lock["commit_sha256"])
        locked_model_provenance_sha = str(model_provenance_lock["sha256"])
        locked_model_provenance_path = Path(
            str(model_provenance_lock["path"])
        ).resolve()
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRunContextError(
            "Split promotion has an incomplete model-provenance lock."
        ) from error
    expected_model_provenance_path = (
        model_provenance_path
        if original_model_provenance_path is None
        else original_model_provenance_path
    )
    if (
        locked_model_commit != model_commit
        or locked_model_provenance_sha != model_provenance_file_sha256
        or locked_model_provenance_path != expected_model_provenance_path
    ):
        raise ModelRunContextError(
            "Split promotion does not commit to this model-dataset provenance."
        )
    split_model_record = _record_for_path(
        inputs.get("model_dataset"),
        requested_path=model_path,
        original_path=original_model_path,
        label="Split-promotion model table",
    )
    for key in ("sha256", "bytes", "rows", "schema_sha256"):
        if split_model_record.get(key) != model_record.get(key):
            raise ModelRunContextError(
                "Split promotion and model provenance disagree on the model table."
            )

    frozen_outputs = inputs.get("frozen_split_outputs")
    if not isinstance(frozen_outputs, dict):
        raise ModelRunContextError("Split promotion lacks frozen split output locks.")
    requested = {
        row_groups_path.name: (row_groups_path, original_row_groups_path),
        fold_definitions_path.name: (
            fold_definitions_path,
            original_fold_definitions_path,
        ),
        buffers_path.name: (buffers_path, original_buffers_path),
    }
    records = {
        filename: _record_for_path(
            frozen_outputs.get(filename),
            requested_path=path,
            original_path=original_path,
            label=f"Frozen split output {filename}",
        )
        for filename, (path, original_path) in requested.items()
    }
    return commit, records


def _expected_candidates(config: ModelSelectionConfig) -> list[dict[str, Any]]:
    return [
        {
            "model_id": candidate.model_id,
            "candidate_id": candidate.candidate_id,
            "complexity_rank": candidate.complexity_rank,
            "parameters": candidate.factory_parameters(),
        }
        for candidate in config.candidates
    ]


def _validate_model_selection_freeze(
    payload: dict[str, Any],
    *,
    config_path: Path,
    original_config_path: Path | None = None,
) -> tuple[str, ModelSelectionConfig]:
    commit = _verify_commit(payload, label="Model-selection freeze")
    if (
        payload.get("state") != MODEL_SELECTION_STATE
        or payload.get("frozen_before_scores") is not True
        or payload.get("target_tables_read") != []
        or payload.get("score_tables_read") != []
        or payload.get("models_fitted") is not False
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_unlocked") is not False
        or payload.get("candidate_count_total") != 31
        or payload.get("candidate_counts") != EXPECTED_CANDIDATE_COUNTS
        or not isinstance(payload.get("candidates"), list)
        or len(payload["candidates"]) != 31
    ):
        raise ModelRunContextError(
            "Model selection is not the frozen, score-blind 31-candidate contract."
        )
    config_record = payload.get("config")
    if not isinstance(config_record, dict):
        raise ModelRunContextError("Model-selection freeze lacks its config lock.")
    try:
        recorded_path = Path(str(config_record["path"])).resolve()
        recorded_file_sha256 = str(config_record["file_sha256"])
        recorded_semantic_sha256 = str(config_record["semantic_sha256"])
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRunContextError("Model-selection config lock is incomplete.") from error
    expected_recorded_path = (
        config_path if original_config_path is None else original_config_path
    )
    if (
        recorded_path != expected_recorded_path
        or not config_path.is_file()
        or sha256_file(config_path) != recorded_file_sha256
    ):
        raise ModelRunContextError("Model-selection config byte lock failed.")
    config = load_model_selection_config(config_path)
    if (
        config.semantic_sha256 != recorded_semantic_sha256
        or list(config.development_years) != payload.get("development_years")
        or config.random_state != payload.get("random_state")
        or payload["candidates"] != _expected_candidates(config)
        or {model_id: len(config.candidates_for(model_id)) for model_id in MODEL_IDS}
        != EXPECTED_CANDIDATE_COUNTS
    ):
        raise ModelRunContextError(
            "Model-selection freeze disagrees with the byte-locked semantic config."
        )
    return commit, config


def _verify_parquet_record(
    path: Path,
    frame: pd.DataFrame,
    record: dict[str, Any],
    *,
    label: str,
) -> None:
    actual = parquet_file_record(path, frame)
    for key in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual.get(key) != record.get(key):
            raise ModelRunContextError(f"{label} {key} failed verification.")


def _verify_csv_record(
    path: Path,
    frame: pd.DataFrame,
    record: dict[str, Any],
    *,
    label: str,
) -> None:
    if int(record.get("rows", -1)) != len(frame):
        raise ModelRunContextError(f"{label} row count failed verification.")
    if sha256_file(path) != record.get("sha256") or path.stat().st_size != int(
        record.get("bytes", -1)
    ):
        raise ModelRunContextError(f"{label} byte lock changed while being read.")


def _civil_keys(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    missing = sorted(set(PRIMARY_KEYS) - set(frame.columns))
    if missing:
        raise ModelRunContextError(f"{label} is missing keys: {missing}")
    result = frame.loc[:, list(PRIMARY_KEYS)].copy()
    result["tract_geoid"] = result["tract_geoid"].astype("string")
    try:
        result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise ModelRunContextError(f"{label} has invalid target dates.") from error
    if result.isna().any(axis=None) or result.duplicated(list(PRIMARY_KEYS)).any():
        raise ModelRunContextError(f"{label} has missing or duplicate keys.")
    if result["target_date"].dt.tz is not None or not result[
        "target_date"
    ].dt.normalize().equals(result["target_date"]):
        raise ModelRunContextError(f"{label} dates must be naive civil midnights.")
    if result["target_date"].dt.year.ge(2025).any():
        raise PermissionError(f"{label} contains locked 2025+ rows.")
    return result


def _align_dataset_to_row_groups(
    model_table: pd.DataFrame,
    row_groups: pd.DataFrame,
) -> pd.DataFrame:
    model = model_table.copy()
    model_keys = _civil_keys(model, label="Model table")
    model.loc[:, "tract_geoid"] = model_keys["tract_geoid"]
    model.loc[:, "target_date"] = model_keys["target_date"]
    row_keys = _civil_keys(row_groups, label="Row groups")
    comparison = row_keys.merge(
        model_keys,
        on=list(PRIMARY_KEYS),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not comparison["_merge"].eq("both").all():
        missing = int(comparison["_merge"].eq("left_only").sum())
        extra = int(comparison["_merge"].eq("right_only").sum())
        raise ModelRunContextError(
            f"Model/row-group key mismatch: missing_from_model={missing}, "
            f"extra_in_model={extra}."
        )
    aligned = row_keys.merge(
        model,
        on=list(PRIMARY_KEYS),
        how="left",
        sort=False,
        validate="one_to_one",
    )
    try:
        pd.testing.assert_frame_equal(
            aligned.loc[:, list(PRIMARY_KEYS)],
            row_keys,
            check_dtype=False,
        )
    except AssertionError as error:
        raise AssertionError(
            "Model alignment changed frozen row-group key order."
        ) from error
    aligned["tract_geoid"] = row_keys["tract_geoid"].array
    aligned["target_date"] = row_keys["target_date"].array
    return aligned


def load_model_run_context(
    *,
    model_provenance_path: str | Path = DEFAULT_MODEL_PROVENANCE_PATH,
    model_table_path: str | Path = DEFAULT_MODEL_TABLE_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    split_promotion_path: str | Path = DEFAULT_SPLIT_PROMOTION_PATH,
    row_groups_path: str | Path = DEFAULT_ROW_GROUPS_PATH,
    fold_definitions_path: str | Path = DEFAULT_FOLD_DEFINITIONS_PATH,
    spatial_buffers_path: str | Path = DEFAULT_SPATIAL_BUFFERS_PATH,
    model_selection_freeze_path: str | Path = DEFAULT_MODEL_SELECTION_FREEZE_PATH,
    model_selection_config_path: str | Path = DEFAULT_MODEL_SELECTION_CONFIG_PATH,
    expected_fold_count_total: int = EXPECTED_PRODUCTION_FOLD_COUNT,
    portable_manifest_path: str | Path | None = None,
    portable_root: str | Path | None = None,
) -> ModelRunContext:
    """Authenticate and load all frozen inputs required for model evaluation.

    Relocation is opt-in and requires both ``portable_manifest_path`` and
    ``portable_root``.  Without both, the original absolute path locks retain
    their historical fail-closed behavior.
    """

    if (portable_manifest_path is None) != (portable_root is None):
        raise ModelRunContextError(
            "Portable loading requires both portable_manifest_path and portable_root."
        )
    relocation: PortableRelocation | None = None
    if portable_manifest_path is None:
        project_root = Path(__file__).resolve().parents[2]
    else:
        assert portable_root is not None
        project_root = Path(portable_root).resolve()
        try:
            relocation = load_portable_relocation(
                portable_manifest_path,
                project_root,
            )
        except PortableRelocationError as error:
            raise ModelRunContextError(str(error)) from error
    paths = {
        "model_provenance": _resolve(project_root, model_provenance_path),
        "model_table": _resolve(project_root, model_table_path),
        "registry": _resolve(project_root, registry_path),
        "split_promotion": _resolve(project_root, split_promotion_path),
        "row_groups": _resolve(project_root, row_groups_path),
        "folds": _resolve(project_root, fold_definitions_path),
        "buffers": _resolve(project_root, spatial_buffers_path),
        "selection_freeze": _resolve(project_root, model_selection_freeze_path),
        "selection_config": _resolve(project_root, model_selection_config_path),
    }
    if relocation is not None:
        for logical_name, requested_path in paths.items():
            portable_path = relocation.entry(logical_name).portable_path
            if requested_path != portable_path:
                raise ModelRunContextError(
                    f"Portable {logical_name} request disagrees with relocation manifest."
                )

    def original(logical_name: str) -> Path | None:
        return (
            None
            if relocation is None
            else relocation.entry(logical_name).original_path
        )

    # Authenticate every controlling manifest before the first Parquet read.
    model_provenance, model_provenance_file_sha = _read_json_object(
        paths["model_provenance"]
    )
    split_promotion, _ = _read_json_object(paths["split_promotion"])
    selection_freeze, _ = _read_json_object(paths["selection_freeze"])
    model_commit, model_record, registry_record = _validate_model_provenance(
        model_provenance,
        model_path=paths["model_table"],
        registry_path=paths["registry"],
        original_model_path=original("model_table"),
        original_registry_path=original("registry"),
    )
    split_commit, split_records = _validate_split_promotion(
        split_promotion,
        model_commit=model_commit,
        model_provenance_path=paths["model_provenance"],
        model_provenance_file_sha256=model_provenance_file_sha,
        model_path=paths["model_table"],
        model_record=model_record,
        row_groups_path=paths["row_groups"],
        fold_definitions_path=paths["folds"],
        buffers_path=paths["buffers"],
        expected_fold_count_total=expected_fold_count_total,
        original_model_provenance_path=original("model_provenance"),
        original_model_path=original("model_table"),
        original_row_groups_path=original("row_groups"),
        original_fold_definitions_path=original("folds"),
        original_buffers_path=original("buffers"),
    )
    selection_commit, selection_config = _validate_model_selection_freeze(
        selection_freeze,
        config_path=paths["selection_config"],
        original_config_path=original("selection_config"),
    )

    model_table = pd.read_parquet(paths["model_table"])
    registry = pd.read_csv(paths["registry"])
    row_groups = pd.read_parquet(paths["row_groups"])
    folds = pd.read_csv(paths["folds"])
    buffers = pd.read_parquet(paths["buffers"])
    _verify_parquet_record(
        paths["model_table"], model_table, model_record, label="Model table"
    )
    _verify_csv_record(
        paths["registry"], registry, registry_record, label="Feature registry"
    )
    _verify_parquet_record(
        paths["row_groups"],
        row_groups,
        split_records[paths["row_groups"].name],
        label="Row groups",
    )
    _verify_csv_record(
        paths["folds"],
        folds,
        split_records[paths["folds"].name],
        label="Fold definitions",
    )
    _verify_parquet_record(
        paths["buffers"],
        buffers,
        split_records[paths["buffers"].name],
        label="Spatial buffers",
    )

    normalized_row_keys = _civil_keys(row_groups, label="Row groups")
    row_groups = row_groups.copy()
    row_groups["tract_geoid"] = normalized_row_keys["tract_geoid"].array
    row_groups["target_date"] = normalized_row_keys["target_date"].array

    if "held_out_year" not in folds.columns:
        raise ModelRunContextError("Fold definitions lack held_out_year.")
    try:
        folds["held_out_year"] = folds["held_out_year"].astype("Int64")
    except (TypeError, ValueError) as error:
        raise ModelRunContextError("held_out_year cannot be represented as Int64.") from error
    if len(folds) != expected_fold_count_total:
        raise ModelRunContextError("Fold-definition row count is not frozen.")
    observed_fold_counts = {
        family: int(folds["family"].eq(family).sum()) for family in FAMILIES
    }
    if observed_fold_counts != split_promotion["fold_counts"]:
        raise ModelRunContextError("Fold definitions disagree with promoted fold counts.")
    recomputed_oof = validate_oof_coverage(row_groups, folds, buffers)
    if recomputed_oof != split_promotion["oof_coverage_audit"]:
        raise ModelRunContextError("Recomputed OOF coverage disagrees with promotion.")

    aligned = _align_dataset_to_row_groups(model_table, row_groups)
    if len(aligned) != int(model_provenance.get("row_count", -1)) or len(
        aligned
    ) != int(split_promotion.get("row_count", -1)):
        raise ModelRunContextError("Aligned model row count disagrees with provenance.")
    if aligned.shape[1] != int(model_provenance.get("column_count", -1)):
        raise ModelRunContextError("Aligned model column count disagrees with provenance.")
    if canonical_frame_sha256(
        aligned, sort_by=list(PRIMARY_KEYS)
    ) != model_provenance.get("semantic_model_table_sha256"):
        raise ModelRunContextError("Aligned model-table semantic hash failed.")
    if canonical_frame_sha256(
        registry, sort_by=["feature_name"]
    ) != model_provenance.get("registry_semantic_sha256"):
        raise ModelRunContextError("Feature-registry semantic hash failed.")
    expected_columns = [
        *PRIMARY_KEYS,
        TARGET_COLUMN,
        *registry.loc[~registry["role"].eq("key"), "feature_name"].tolist(),
    ]
    if aligned.columns.tolist() != expected_columns:
        raise ModelRunContextError("Model table schema/order disagrees with the registry.")
    features, target, keys, audit_only = extract_registered_model_data(aligned, registry)
    if features.shape[1] != int(model_provenance.get("model_feature_count", -1)):
        raise ModelRunContextError("Registered model-feature count disagrees with provenance.")
    if audit_only.shape[1] != int(
        model_provenance.get("audit_only_feature_count", -1)
    ):
        raise ModelRunContextError("Audit-only feature count disagrees with provenance.")
    row_keys = _civil_keys(row_groups, label="Row groups")
    if not keys.equals(row_keys):
        raise AssertionError("Extracted model keys do not follow frozen row-group order.")
    if canonical_frame_sha256(
        keys, sort_by=["target_date", "tract_geoid"]
    ) != split_promotion.get("semantic_model_key_sha256"):
        raise ModelRunContextError("Model keys disagree with the promoted semantic lock.")

    runtime_sha, _ = modeling_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "src/la_heat/model_dataset.py",
            "src/la_heat/model_runtime.py",
            "src/la_heat/model_run_context.py",
            "src/la_heat/model_selection.py",
            "src/la_heat/portable_relocation.py",
            "src/la_heat/provenance.py",
            "src/la_heat/validation_splits.py",
        ),
        algorithm_version=MODEL_RUN_CONTEXT_ALGORITHM_VERSION,
    )
    run_id = canonical_sha256(
        {
            "algorithm_version": MODEL_RUN_CONTEXT_ALGORITHM_VERSION,
            "model_dataset_commit_sha256": model_commit,
            "split_promotion_commit_sha256": split_commit,
            "model_selection_commit_sha256": selection_commit,
            "runtime_fingerprint_sha256": runtime_sha,
        }
    )
    return ModelRunContext(
        run_id=run_id,
        model_dataset_commit_sha256=model_commit,
        split_promotion_commit_sha256=split_commit,
        model_selection_commit_sha256=selection_commit,
        runtime_fingerprint_sha256=runtime_sha,
        dataset=aligned,
        registry=registry,
        row_groups=row_groups,
        fold_definitions=folds,
        spatial_buffer_geoids=buffers,
        features=features,
        target=target,
        keys=keys,
        audit_only=audit_only,
        model_selection=selection_config,
        portable_relocation_commit_sha256=(
            None if relocation is None else relocation.commit_sha256
        ),
    )
