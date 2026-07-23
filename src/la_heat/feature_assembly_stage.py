"""Join legal Landsat targets to the promoted target-blind Phase 2 table."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import load_config
from la_heat.feature_registry import validate_feature_registry
from la_heat.model_dataset import (
    PRIMARY_KEYS,
    TARGET_COLUMN,
    assemble_precomputed_development_model_table,
    extract_registered_model_data,
)
from la_heat.phase2_feature_stage import (
    PHASE2_FEATURE_FILENAME,
    PHASE2_PROVENANCE_FILENAME,
    PHASE2_REGISTRY_FILENAME,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import target_config_sha256

MODEL_DATASET_SCHEMA_VERSION: Final = 1
MODEL_DATASET_ALGORITHM_VERSION: Final = "gated-development-model-dataset-v1"
MODEL_DATASET_FILENAME: Final = "development_model_table.parquet"
MODEL_REGISTRY_FILENAME: Final = "feature_registry.csv"
MODEL_DATASET_PROVENANCE_FILENAME: Final = "model_dataset_provenance.json"

DEFAULT_PHASE2_DIRECTORY: Final = Path("data/processed/phase2_features")
DEFAULT_PHASE2_PATH: Final = DEFAULT_PHASE2_DIRECTORY / PHASE2_FEATURE_FILENAME
DEFAULT_PHASE2_PROVENANCE_PATH: Final = (
    DEFAULT_PHASE2_DIRECTORY / PHASE2_PROVENANCE_FILENAME
)
DEFAULT_PHASE2_REGISTRY_PATH: Final = (
    DEFAULT_PHASE2_DIRECTORY / PHASE2_REGISTRY_FILENAME
)
DEFAULT_TARGET_PROGRESS_PATH: Final = Path("data/interim/targets/build_progress.json")
DEFAULT_TARGET_PATH: Final = Path(
    "data/interim/targets/development_targets_model_ready.parquet"
)
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/processed/model_dataset")


class FeatureAssemblyError(ValueError):
    """Raised when a promoted feature or target input fails its frozen lock."""


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
        raise FeatureAssemblyError(f"Cannot read committed JSON input {path}.") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"JSON input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise FeatureAssemblyError(f"Committed JSON input must be an object: {path}")
    return payload, before


def _verify_canonical_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FeatureAssemblyError(f"{label} has an invalid canonical commit hash.")
    return recorded


def _locked_output_record(
    provenance: dict[str, Any],
    *,
    filename: str,
    requested_path: Path,
) -> dict[str, Any]:
    try:
        record = provenance["output_files"][filename]
        recorded_path = Path(str(record["path"])).resolve()
        recorded_sha256 = str(record["sha256"])
        recorded_bytes = int(record["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise FeatureAssemblyError(
            f"Phase 2 provenance lacks a complete output lock for {filename!r}."
        ) from error
    if recorded_path != requested_path:
        raise FeatureAssemblyError(f"Phase 2 output path lock failed for {filename!r}.")
    if (
        not requested_path.is_file()
        or sha256_file(requested_path) != recorded_sha256
        or requested_path.stat().st_size != recorded_bytes
    ):
        raise FeatureAssemblyError(f"Phase 2 output byte lock failed for {filename!r}.")
    return dict(record)


def _read_locked_parquet(path: Path, *, expected_sha256: str) -> pd.DataFrame:
    before = sha256_file(path)
    if before != expected_sha256:
        raise FeatureAssemblyError(f"Parquet input failed its byte lock: {path}")
    frame = pd.read_parquet(path)
    if sha256_file(path) != before:
        raise RuntimeError(f"Parquet input changed while being read: {path}")
    return frame


def _read_locked_csv(path: Path, *, expected_sha256: str) -> pd.DataFrame:
    before = sha256_file(path)
    if before != expected_sha256:
        raise FeatureAssemblyError(f"CSV input failed its byte lock: {path}")
    frame = pd.read_csv(path)
    if sha256_file(path) != before:
        raise RuntimeError(f"CSV input changed while being read: {path}")
    return frame


def _validate_phase2_provenance(
    provenance: dict[str, Any],
    *,
    final_test_year: int,
    final_test_unlocked: bool,
) -> str:
    commit = _verify_canonical_commit(provenance, label="Phase 2 feature provenance")
    authorized = (
        provenance.get("state") == "complete"
        and provenance.get("phase2_complete") is True
        and provenance.get("ready_for_target_join") is True
        and provenance.get("target_blind") is True
        and provenance.get("target_or_qa_tables_read") == []
        and provenance.get("target_values_read") is False
        and provenance.get("model_scores_read") is False
    )
    if not authorized:
        raise FeatureAssemblyError("Promoted Phase 2 features do not authorize target access.")
    if (
        int(provenance.get("final_test_year", -1)) != final_test_year
        or bool(provenance.get("final_test_unlocked")) != final_test_unlocked
        or provenance.get("contains_final_test_year") is not False
    ):
        raise FeatureAssemblyError(
            "Phase 2 provenance disagrees with the locked final-test state."
        )
    if final_test_unlocked:
        raise PermissionError(
            "Development model assembly requires calendar year 2025 to remain locked."
        )
    return commit


def _validate_phase2_artifacts(
    table: pd.DataFrame,
    registry: pd.DataFrame,
    provenance: dict[str, Any],
    *,
    table_path: Path,
    table_record: dict[str, Any],
    development_start: str,
    final_test_year: int,
) -> None:
    validate_feature_registry(registry, development_start=development_start)
    ordered_columns = registry["feature_name"].tolist()
    if table.columns.tolist() != ordered_columns:
        raise FeatureAssemblyError(
            "Promoted Phase 2 table schema or order disagrees with its registry."
        )
    if ordered_columns != provenance.get("ordered_columns"):
        raise FeatureAssemblyError("Phase 2 registry order disagrees with provenance.")
    if len(table) != int(provenance.get("row_count", -1)):
        raise FeatureAssemblyError("Phase 2 table row count disagrees with provenance.")
    if table.shape[1] != int(provenance.get("column_count", -1)):
        raise FeatureAssemblyError("Phase 2 table column count disagrees with provenance.")
    actual_record = parquet_file_record(table_path, table)
    for key in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual_record.get(key) != table_record.get(key):
            raise FeatureAssemblyError(
                f"Phase 2 Parquet {key} disagrees with its provenance."
            )
    expected_registry_semantic = provenance.get("registry_semantic_sha256")
    if canonical_frame_sha256(registry, sort_by=["feature_name"]) != expected_registry_semantic:
        raise FeatureAssemblyError("Phase 2 registry semantic hash failed.")
    expected_table_semantic = provenance.get("semantic_feature_table_sha256")
    if (
        canonical_frame_sha256(
            table,
            sort_by=["target_date", "tract_geoid"],
            columns=ordered_columns,
        )
        != expected_table_semantic
    ):
        raise FeatureAssemblyError("Phase 2 feature-table semantic hash failed.")
    try:
        dates = pd.to_datetime(table["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise FeatureAssemblyError("Phase 2 table has invalid target dates.") from error
    if dates.dt.tz is not None or not dates.dt.normalize().equals(dates):
        raise FeatureAssemblyError("Phase 2 target dates must be naive civil midnights.")
    if dates.dt.year.ge(final_test_year).any():
        raise PermissionError("Phase 2 features contain locked final-test rows.")
    if table[list(PRIMARY_KEYS)].isna().any(axis=None):
        raise FeatureAssemblyError("Phase 2 table has missing keys.")
    if table.duplicated(list(PRIMARY_KEYS)).any():
        raise FeatureAssemblyError("Phase 2 table has duplicate keys.")


def _target_record(
    progress: dict[str, Any],
    *,
    target_path: Path,
) -> dict[str, Any]:
    try:
        record = progress["aggregate_outputs"][target_path.name]
        locked = {
            "sha256": str(record["sha256"]),
            "bytes": int(record["bytes"]),
            "rows": int(record["rows"]),
            "schema_sha256": str(record["schema_sha256"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise FeatureAssemblyError(
            "Target build progress lacks its model-ready file lock."
        ) from error
    if (
        not target_path.is_file()
        or sha256_file(target_path) != locked["sha256"]
        or target_path.stat().st_size != locked["bytes"]
    ):
        raise FeatureAssemblyError("Model-ready target table failed its byte lock.")
    return locked


def build_model_dataset_artifacts(
    config_path: str | Path = "configs/research.toml",
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    *,
    phase2_path: str | Path = DEFAULT_PHASE2_PATH,
    phase2_provenance_path: str | Path = DEFAULT_PHASE2_PROVENANCE_PATH,
    phase2_registry_path: str | Path = DEFAULT_PHASE2_REGISTRY_PATH,
    target_progress_path: str | Path = DEFAULT_TARGET_PROGRESS_PATH,
    target_path: str | Path = DEFAULT_TARGET_PATH,
) -> dict[str, Any]:
    """Build and atomically commit the legal development target-predictor table."""

    project_root = Path(__file__).resolve().parents[2]
    config = load_config(_resolve(project_root, config_path))
    if config.final_test_unlocked:
        raise PermissionError(
            "Development model assembly requires calendar year 2025 to remain locked."
        )
    study = config.raw["study"]
    resolved = {
        "phase2": _resolve(project_root, phase2_path),
        "phase2_provenance": _resolve(project_root, phase2_provenance_path),
        "phase2_registry": _resolve(project_root, phase2_registry_path),
        "target_progress": _resolve(project_root, target_progress_path),
        "target": _resolve(project_root, target_path),
    }

    phase2_provenance, phase2_provenance_sha256 = _read_json_object(
        resolved["phase2_provenance"]
    )
    phase2_commit = _validate_phase2_provenance(
        phase2_provenance,
        final_test_year=config.final_test_year,
        final_test_unlocked=config.final_test_unlocked,
    )
    phase2_table_record = _locked_output_record(
        phase2_provenance,
        filename=PHASE2_FEATURE_FILENAME,
        requested_path=resolved["phase2"],
    )
    phase2_registry_record = _locked_output_record(
        phase2_provenance,
        filename=PHASE2_REGISTRY_FILENAME,
        requested_path=resolved["phase2_registry"],
    )
    phase2_table = _read_locked_parquet(
        resolved["phase2"], expected_sha256=str(phase2_table_record["sha256"])
    )
    registry = _read_locked_csv(
        resolved["phase2_registry"],
        expected_sha256=str(phase2_registry_record["sha256"]),
    )
    _validate_phase2_artifacts(
        phase2_table,
        registry,
        phase2_provenance,
        table_path=resolved["phase2"],
        table_record=phase2_table_record,
        development_start=str(study["start_date"]),
        final_test_year=config.final_test_year,
    )

    target_progress, target_progress_sha256 = _read_json_object(
        resolved["target_progress"]
    )
    if (
        target_progress.get("state") != "model_ready"
        or target_progress.get("build_complete") is not True
        or target_progress.get("promoted_outputs_valid") is not True
        or target_progress.get("partial_outputs_only") is not False
        or target_progress.get("target_config_sha256") != target_config_sha256(config)
        or target_progress.get("completed_overpass_count")
        != target_progress.get("expected_overpass_count")
    ):
        raise FeatureAssemblyError("Target build is not a locked model-ready input.")
    locked_target = _target_record(target_progress, target_path=resolved["target"])
    target = _read_locked_parquet(
        resolved["target"], expected_sha256=locked_target["sha256"]
    )
    actual_target_record = parquet_file_record(resolved["target"], target)
    if any(actual_target_record[key] != locked_target[key] for key in locked_target):
        raise FeatureAssemblyError("Model-ready target table record failed validation.")

    assembled = assemble_precomputed_development_model_table(
        target,
        phase2_table,
        registry,
        development_start=str(study["start_date"]),
        final_test_year=config.final_test_year,
        unlock_final_test=False,
    )
    if len(assembled) != locked_target["rows"]:
        raise FeatureAssemblyError(
            "Legal model-table row count disagrees with the model-ready target lock."
        )
    features, target_values, keys, audit = extract_registered_model_data(
        assembled, registry
    )
    dates = pd.to_datetime(keys["target_date"], errors="raise")
    if dates.dt.year.ge(config.final_test_year).any():
        raise PermissionError("Assembled model data contains locked final-test rows.")
    if not np.isfinite(target_values.to_numpy(dtype=float)).all():
        raise FeatureAssemblyError("Assembled target contains non-finite values.")

    output = _resolve(project_root, output_directory)
    table_path = output / MODEL_DATASET_FILENAME
    registry_snapshot_path = output / MODEL_REGISTRY_FILENAME
    marker_path = output / MODEL_DATASET_PROVENANCE_FILENAME
    output.mkdir(parents=True, exist_ok=True)
    marker_path.unlink(missing_ok=True)
    atomic_parquet(assembled, table_path)
    atomic_csv(registry, registry_snapshot_path)
    frozen = pd.read_parquet(table_path)
    pd.testing.assert_frame_equal(frozen, assembled, check_dtype=True)

    model_missing = {
        column: int(count) for column, count in features.isna().sum().items()
    }
    complete_model_rows = int(features.notna().all(axis=1).sum())
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_model_dataset.py",
            "src/la_heat/feature_assembly_stage.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/model_dataset.py",
            "src/la_heat/model_rows.py",
            "src/la_heat/phase2_feature_stage.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=MODEL_DATASET_ALGORITHM_VERSION,
    )
    payload: dict[str, Any] = {
        "schema_version": MODEL_DATASET_SCHEMA_VERSION,
        "algorithm_version": MODEL_DATASET_ALGORITHM_VERSION,
        "state": "complete",
        "phase2_feature_commit_verified": True,
        "ready_for_modeling": True,
        "assembled_at_utc": datetime.now(UTC).isoformat(),
        "phase2_feature_commit_sha256": phase2_commit,
        "target_values_read": True,
        "target_or_qa_tables_read": [str(resolved["target"])],
        "target_columns_used": [
            "tract_geoid",
            "target_date",
            "target_available",
            "date_usable",
            TARGET_COLUMN,
        ],
        "model_scores_read": False,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "row_count": len(assembled),
        "column_count": assembled.shape[1],
        "independent_date_count": int(dates.nunique()),
        "tract_count": int(keys["tract_geoid"].nunique()),
        "model_feature_count": features.shape[1],
        "audit_only_feature_count": audit.shape[1],
        "complete_model_feature_rows": complete_model_rows,
        "incomplete_model_feature_rows": int(len(features) - complete_model_rows),
        "missing_count_by_model_feature": model_missing,
        "ordered_model_feature_names": list(features.columns),
        "ordered_audit_only_feature_names": list(audit.columns),
        "semantic_model_table_sha256": canonical_frame_sha256(
            assembled, sort_by=list(PRIMARY_KEYS)
        ),
        "registry_semantic_sha256": canonical_frame_sha256(
            registry, sort_by=["feature_name"]
        ),
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "inputs": {
            "phase2_provenance": {
                "path": str(resolved["phase2_provenance"]),
                "sha256": phase2_provenance_sha256,
                "commit_sha256": phase2_commit,
            },
            "phase2_features": phase2_table_record,
            "phase2_registry": phase2_registry_record,
            "target_progress": {
                "path": str(resolved["target_progress"]),
                "sha256": target_progress_sha256,
            },
            "model_ready_target": {
                "path": str(resolved["target"]),
                **locked_target,
            },
        },
        "output_files": {
            MODEL_DATASET_FILENAME: {
                "path": str(table_path),
                **parquet_file_record(table_path, assembled),
            },
            MODEL_REGISTRY_FILENAME: {
                "path": str(registry_snapshot_path),
                "sha256": sha256_file(registry_snapshot_path),
                "bytes": registry_snapshot_path.stat().st_size,
                "rows": len(registry),
            },
        },
        "scientific_contract": {
            "outcome": "QA-filtered daytime Landsat land-surface temperature",
            "outcome_interpretation": "surface-heat hazard proxy",
            "prediction_type": "historical hindcast",
            "prediction_origin": "00:00 Los Angeles civil time on target date",
            "dynamic_observed_predictors_end_by": "target day -1",
            "same_scene_or_thermal_predictors_used": False,
            "fold_local_preprocessing_still_required": True,
            "random_row_split_allowed": False,
            "spatial_and_temporal_grouping_required": True,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    return payload
