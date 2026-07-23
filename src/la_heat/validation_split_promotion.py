"""Promote the frozen grouped-validation draft against the committed model keys."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.feature_assembly_stage import (
    MODEL_DATASET_FILENAME,
    MODEL_DATASET_PROVENANCE_FILENAME,
)
from la_heat.model_dataset import PRIMARY_KEYS
from la_heat.provenance import (
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.validation_splits import FAMILIES, validate_oof_coverage

SPLIT_PROMOTION_SCHEMA_VERSION: Final = 1
SPLIT_PROMOTION_ALGORITHM_VERSION: Final = "validation-split-promotion-v1"
SPLIT_PROMOTION_FILENAME: Final = "split_promotion.json"

DEFAULT_SPLIT_DIRECTORY: Final = Path("manifests/validation_splits")
DEFAULT_DRAFT_PROVENANCE_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "split_provenance.json"
DEFAULT_ROW_GROUPS_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "row_groups.parquet"
DEFAULT_FOLD_DEFINITIONS_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "fold_definitions.csv"
DEFAULT_BUFFER_PATH: Final = DEFAULT_SPLIT_DIRECTORY / "spatial_buffer_geoids.parquet"
DEFAULT_MODEL_DIRECTORY: Final = Path("data/processed/model_dataset")
DEFAULT_MODEL_PATH: Final = DEFAULT_MODEL_DIRECTORY / MODEL_DATASET_FILENAME
DEFAULT_MODEL_PROVENANCE_PATH: Final = (
    DEFAULT_MODEL_DIRECTORY / MODEL_DATASET_PROVENANCE_FILENAME
)
DEFAULT_OUTPUT_PATH: Final = DEFAULT_SPLIT_DIRECTORY / SPLIT_PROMOTION_FILENAME


class ValidationSplitPromotionError(ValueError):
    """Raised when a draft split or committed model-key input fails closed."""


def _resolve(project_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationSplitPromotionError(f"Cannot read JSON input {path}.") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"JSON input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise ValidationSplitPromotionError(f"JSON input must be an object: {path}")
    return payload, before


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise ValidationSplitPromotionError(f"{label} canonical commit is invalid.")
    return recorded


def _read_locked_parquet(
    path: Path,
    record: dict[str, Any],
    *,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    try:
        expected_sha256 = str(record["sha256"])
        expected_bytes = int(record["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationSplitPromotionError(
            f"Incomplete Parquet lock for {path.name}."
        ) from error
    if (
        not path.is_file()
        or sha256_file(path) != expected_sha256
        or path.stat().st_size != expected_bytes
    ):
        raise ValidationSplitPromotionError(f"Parquet byte lock failed for {path.name}.")
    frame = pd.read_parquet(path, columns=columns)
    if sha256_file(path) != expected_sha256:
        raise RuntimeError(f"Parquet input changed while being read: {path}")
    return frame


def _read_locked_csv(path: Path, record: dict[str, Any]) -> pd.DataFrame:
    try:
        expected_sha256 = str(record["sha256"])
        expected_bytes = int(record["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationSplitPromotionError(
            f"Incomplete CSV lock for {path.name}."
        ) from error
    if (
        not path.is_file()
        or sha256_file(path) != expected_sha256
        or path.stat().st_size != expected_bytes
    ):
        raise ValidationSplitPromotionError(f"CSV byte lock failed for {path.name}.")
    frame = pd.read_csv(path)
    if sha256_file(path) != expected_sha256:
        raise RuntimeError(f"CSV input changed while being read: {path}")
    return frame


def _validate_draft(
    provenance: dict[str, Any],
    *,
    requested_paths: dict[str, Path],
) -> str:
    commit = _verify_commit(provenance, label="Validation split draft")
    if (
        provenance.get("state") != "predeclared_draft"
        or provenance.get("phase_complete") is not False
        or provenance.get("ready_for_model_evaluation") is not False
        or provenance.get("final_test_year") != 2025
        or provenance.get("final_test_locked") is not True
        or provenance.get("development_years") != [2020, 2021, 2022, 2023, 2024]
        or provenance.get("input_column_contract", {}).get(
            "target_or_predictor_values_read"
        )
        is not False
    ):
        raise ValidationSplitPromotionError(
            "Validation split draft is not the locked target-blind predeclaration."
        )
    expected_directory = Path(str(provenance.get("output_directory", ""))).resolve()
    for filename, path in requested_paths.items():
        if path.parent != expected_directory or path.name != filename:
            raise ValidationSplitPromotionError(
                f"Draft output path disagrees with provenance for {filename!r}."
            )
        if filename not in provenance.get("output_files", {}):
            raise ValidationSplitPromotionError(
                f"Draft provenance lacks output record {filename!r}."
            )
    return commit


def _validate_model_provenance(
    provenance: dict[str, Any],
    *,
    model_path: Path,
) -> tuple[str, dict[str, Any]]:
    commit = _verify_commit(provenance, label="Model dataset")
    if (
        provenance.get("state") != "complete"
        or provenance.get("phase2_feature_commit_verified") is not True
        or provenance.get("ready_for_modeling") is not True
        or provenance.get("model_scores_read") is not False
        or provenance.get("final_test_year") != 2025
        or provenance.get("final_test_unlocked") is not False
        or provenance.get("contains_final_test_year") is not False
        or int(provenance.get("model_feature_count", -1)) != 46
        or int(provenance.get("audit_only_feature_count", -1)) != 1
    ):
        raise ValidationSplitPromotionError(
            "Model dataset is not the committed locked development input."
        )
    try:
        record = dict(provenance["output_files"][MODEL_DATASET_FILENAME])
        recorded_path = Path(str(record["path"])).resolve()
    except (KeyError, TypeError) as error:
        raise ValidationSplitPromotionError(
            "Model provenance lacks the development-table output lock."
        ) from error
    if recorded_path != model_path:
        raise ValidationSplitPromotionError("Model dataset path lock failed.")
    return commit, record


def _civil_key_frame(
    frame: pd.DataFrame,
    *,
    label: str,
) -> pd.DataFrame:
    if set(frame.columns) != set(PRIMARY_KEYS):
        raise ValidationSplitPromotionError(f"{label} must contain only the two keys.")
    result = frame.loc[:, list(PRIMARY_KEYS)].copy()
    result["tract_geoid"] = result["tract_geoid"].astype("string")
    try:
        result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise ValidationSplitPromotionError(f"{label} has invalid dates.") from error
    if result.isna().any(axis=None) or result.duplicated(list(PRIMARY_KEYS)).any():
        raise ValidationSplitPromotionError(f"{label} has missing or duplicate keys.")
    if result["target_date"].dt.tz is not None:
        raise ValidationSplitPromotionError(f"{label} dates must be timezone-naive.")
    if not result["target_date"].dt.normalize().equals(result["target_date"]):
        raise ValidationSplitPromotionError(f"{label} dates must be civil midnights.")
    if result["target_date"].dt.year.ge(2025).any():
        raise PermissionError(f"{label} contains locked 2025+ rows.")
    return result


def promote_validation_splits(
    *,
    draft_provenance_path: str | Path = DEFAULT_DRAFT_PROVENANCE_PATH,
    row_groups_path: str | Path = DEFAULT_ROW_GROUPS_PATH,
    fold_definitions_path: str | Path = DEFAULT_FOLD_DEFINITIONS_PATH,
    buffer_path: str | Path = DEFAULT_BUFFER_PATH,
    model_path: str | Path = DEFAULT_MODEL_PATH,
    model_provenance_path: str | Path = DEFAULT_MODEL_PROVENANCE_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Verify frozen fold artifacts against model keys and write a promotion marker."""

    project_root = Path(__file__).resolve().parents[2]
    resolved = {
        "draft_provenance": _resolve(project_root, draft_provenance_path),
        "row_groups": _resolve(project_root, row_groups_path),
        "fold_definitions": _resolve(project_root, fold_definitions_path),
        "buffer": _resolve(project_root, buffer_path),
        "model": _resolve(project_root, model_path),
        "model_provenance": _resolve(project_root, model_provenance_path),
        "output": _resolve(project_root, output_path),
    }
    output = resolved["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    draft, draft_file_sha256 = _read_json(resolved["draft_provenance"])
    draft_paths = {
        "row_groups.parquet": resolved["row_groups"],
        "fold_definitions.csv": resolved["fold_definitions"],
        "spatial_buffer_geoids.parquet": resolved["buffer"],
    }
    draft_commit = _validate_draft(draft, requested_paths=draft_paths)
    output_records = draft["output_files"]
    row_groups = _read_locked_parquet(
        resolved["row_groups"], output_records["row_groups.parquet"]
    )
    folds = _read_locked_csv(
        resolved["fold_definitions"], output_records["fold_definitions.csv"]
    )
    if "held_out_year" not in folds.columns:
        raise ValidationSplitPromotionError(
            "Draft fold definitions lack held_out_year."
        )
    try:
        folds["held_out_year"] = folds["held_out_year"].astype("Int64")
    except (TypeError, ValueError) as error:
        raise ValidationSplitPromotionError(
            "Draft held_out_year values are invalid."
        ) from error
    buffers = _read_locked_parquet(
        resolved["buffer"], output_records["spatial_buffer_geoids.parquet"]
    )
    for path, frame, filename in (
        (resolved["row_groups"], row_groups, "row_groups.parquet"),
        (resolved["buffer"], buffers, "spatial_buffer_geoids.parquet"),
    ):
        actual = parquet_file_record(path, frame)
        expected = output_records[filename]
        for key in ("sha256", "bytes", "rows", "schema_sha256"):
            if actual[key] != expected[key]:
                raise ValidationSplitPromotionError(
                    f"Draft {filename} {key} failed validation."
                )
    semantic = draft.get("semantic_outputs", {})
    if canonical_frame_sha256(
        row_groups, sort_by=["target_date", "tract_geoid"]
    ) != semantic.get("row_groups_sha256"):
        raise ValidationSplitPromotionError("Draft row-group semantic hash failed.")
    if canonical_frame_sha256(
        folds, sort_by=["family", "fold_index"]
    ) != semantic.get("fold_definitions_sha256"):
        raise ValidationSplitPromotionError("Draft fold-definition semantic hash failed.")
    if canonical_frame_sha256(
        buffers, sort_by=["held_out_block", "tract_geoid"]
    ) != semantic.get("spatial_buffer_geoids_sha256"):
        raise ValidationSplitPromotionError("Draft buffer semantic hash failed.")
    oof_audit = validate_oof_coverage(row_groups, folds, buffers)
    if oof_audit != draft.get("oof_coverage_audit"):
        raise ValidationSplitPromotionError("Recomputed OOF audit disagrees with draft.")

    model_provenance, model_provenance_file_sha256 = _read_json(
        resolved["model_provenance"]
    )
    model_commit, model_record = _validate_model_provenance(
        model_provenance, model_path=resolved["model"]
    )
    model_keys_raw = _read_locked_parquet(
        resolved["model"], model_record, columns=list(PRIMARY_KEYS)
    )
    model_keys = _civil_key_frame(model_keys_raw, label="Model dataset keys")
    draft_keys = _civil_key_frame(
        row_groups.loc[:, list(PRIMARY_KEYS)], label="Draft row-group keys"
    )
    comparison = draft_keys.merge(
        model_keys,
        on=list(PRIMARY_KEYS),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    missing = int(comparison["_merge"].eq("left_only").sum())
    extra = int(comparison["_merge"].eq("right_only").sum())
    if missing or extra:
        raise ValidationSplitPromotionError(
            f"Draft/model key mismatch: missing_from_model={missing}, extra_in_model={extra}."
        )
    if len(model_keys) != int(model_provenance.get("row_count", -1)):
        raise ValidationSplitPromotionError("Model key count disagrees with provenance.")

    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/promote_validation_splits.py",
            "src/la_heat/provenance.py",
            "src/la_heat/validation_split_promotion.py",
            "src/la_heat/validation_splits.py",
        ),
        algorithm_version=SPLIT_PROMOTION_ALGORITHM_VERSION,
    )
    fold_counts = {
        family: int(folds["family"].eq(family).sum()) for family in FAMILIES
    }
    dates = pd.to_datetime(model_keys["target_date"], errors="raise")
    payload: dict[str, Any] = {
        "schema_version": SPLIT_PROMOTION_SCHEMA_VERSION,
        "algorithm_version": SPLIT_PROMOTION_ALGORITHM_VERSION,
        "state": "promoted",
        "phase_complete": True,
        "ready_for_model_evaluation": True,
        "promoted_at_utc": datetime.now(UTC).isoformat(),
        "target_values_read": False,
        "predictor_values_read": False,
        "model_scores_read": False,
        "columns_read_from_model_dataset": list(PRIMARY_KEYS),
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "row_count": len(model_keys),
        "independent_date_count": int(dates.nunique()),
        "tract_count": int(model_keys["tract_geoid"].nunique()),
        "spatial_block_count": int(draft.get("spatial_block_count", -1)),
        "fold_counts": fold_counts,
        "fold_count_total": len(folds),
        "oof_coverage_audit": oof_audit,
        "semantic_model_key_sha256": canonical_frame_sha256(
            model_keys, sort_by=["target_date", "tract_geoid"]
        ),
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "inputs": {
            "split_draft_provenance": {
                "path": str(resolved["draft_provenance"]),
                "sha256": draft_file_sha256,
                "commit_sha256": draft_commit,
            },
            "model_dataset_provenance": {
                "path": str(resolved["model_provenance"]),
                "sha256": model_provenance_file_sha256,
                "commit_sha256": model_commit,
            },
            "model_dataset": {
                "path": str(resolved["model"]),
                **model_record,
            },
            "frozen_split_outputs": {
                filename: {
                    "path": str(path),
                    **output_records[filename],
                }
                for filename, path in draft_paths.items()
            },
        },
        "scientific_contract": {
            "temporal_outer_split": "leave one complete calendar year out",
            "spatial_outer_split": "leave one fixed 5 km spatial block out",
            "joint_outer_split": "held-out year by held-out block with 1 km purge",
            "inner_tuning_split": "whole remaining calendar years inside outer train",
            "preprocessing_fit_scope": "inner training rows only during tuning",
            "outer_test_or_purged_rows_used_for_tuning": False,
            "random_row_split_allowed": False,
            "oof_unit": "one prediction per legal row per split family",
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, output)
    return payload
