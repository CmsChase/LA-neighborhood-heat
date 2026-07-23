"""Build frozen static and calendar predictors for the blind 2025 key universe."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.calendar_features import CALENDAR_MODEL_FEATURE_NAMES, build_calendar_features
from la_heat.final_test_inventory import (
    FINAL_TEST_YEAR,
    KEY_UNIVERSE_FILENAME,
    SUMMARY_FILENAME,
    authenticate_formal_model_lock,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-predictor-base-v1-static-calendar"
OUTPUT_FILENAME: Final = "predictor_base.parquet"
PROVENANCE_FILENAME: Final = "PREDICTOR_BASE.json"
KEY_COLUMNS: Final = ("tract_geoid", "target_date")
AUDIT_COLUMNS: Final = (
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)
FORBIDDEN_COLUMNS: Final = {
    "target_lst_c",
    "lst_anomaly_c",
    "relative_hotspot_top20",
    "valid_pixel_count",
    "target_available",
    "date_usable",
    "y_true",
    "y_pred",
}


class FinalTestPredictorBaseError(RuntimeError):
    """Raised when blind base predictors fail a frozen input contract."""


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestPredictorBaseError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestPredictorBaseError(f"{label} changed or is not an object.")
    return payload


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalTestPredictorBaseError(f"{label} canonical commit failed.")
    return recorded


def _locked_file(path: Path, record: object, *, label: str) -> None:
    if not isinstance(record, dict):
        raise FinalTestPredictorBaseError(f"{label} file lock is invalid.")
    if (
        not path.is_file()
        or sha256_file(path) != record.get("sha256")
        or path.stat().st_size != record.get("bytes")
    ):
        raise FinalTestPredictorBaseError(f"{label} byte lock failed.")


def _validate_inventory(
    inventory: dict[str, Any],
    *,
    inventory_directory: Path,
    formal_lock: dict[str, Any],
    formal_lock_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    commit = _verify_commit(inventory, label="Final-test Landsat inventory")
    if (
        inventory.get("state") != "target_blind_inventory_frozen"
        or inventory.get("final_test_year") != FINAL_TEST_YEAR
        or inventory.get("target_blind") is not True
        or inventory.get("target_assets_opened") is not False
        or inventory.get("target_or_qa_values_read") is not False
        or inventory.get("labels_created") is not False
        or inventory.get("one_time_evaluation_consumed") is not False
        or inventory.get("formal_model_lock", {}).get("commit_sha256")
        != formal_lock.get("commit_sha256")
        or inventory.get("formal_model_lock", {}).get("sha256")
        != formal_lock_sha256
    ):
        raise FinalTestPredictorBaseError("Final-test inventory is not a blind frozen lock.")
    record = inventory.get("output_files", {}).get(KEY_UNIVERSE_FILENAME)
    key_path = inventory_directory / KEY_UNIVERSE_FILENAME
    _locked_file(key_path, record, label="Target-blind key universe")
    return key_path, {"commit_sha256": commit, "record": record}


def _validate_static_provenance(
    provenance: dict[str, Any],
    *,
    static_path: Path,
    inventory: dict[str, Any],
) -> str:
    commit = _verify_commit(provenance, label="Static feature provenance")
    record = provenance.get("output_files", {}).get(static_path.name)
    _locked_file(static_path, record, label="Static feature table")
    support = provenance.get("target_support_locks", {})
    frozen = inventory.get("frozen_support", {})
    if (
        provenance.get("state") != "complete"
        or provenance.get("promoted_outputs_valid") is not True
        or provenance.get("row_count") != 1096
        or provenance.get("unique_geoid_count") != 1096
        or provenance.get("contains_date_column") is not False
        or support.get("tract_manifest_sha256")
        != frozen.get("primary_tract_commit_sha256")
        or support.get("tract_manifest_file_sha256")
        != frozen.get("primary_tract_sha256")
    ):
        raise FinalTestPredictorBaseError("Frozen static support does not match 2025 keys.")
    return commit


def build_predictor_base_frame(
    keys: pd.DataFrame,
    static: pd.DataFrame,
    *,
    model_feature_names: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Create exact 2025 static/calendar rows without opening any target table."""

    expected_keys = [*KEY_COLUMNS, *AUDIT_COLUMNS]
    if keys.columns.tolist() != expected_keys:
        raise FinalTestPredictorBaseError("Blind key-universe schema drifted.")
    working = keys.copy()
    working["tract_geoid"] = working["tract_geoid"].astype("string")
    working["target_date"] = pd.to_datetime(working["target_date"], errors="raise")
    if (
        working.empty
        or working["target_date"].dt.tz is not None
        or not working["target_date"].dt.normalize().equals(working["target_date"])
        or not working["target_date"].dt.year.eq(FINAL_TEST_YEAR).all()
        or working.duplicated(list(KEY_COLUMNS)).any()
    ):
        raise FinalTestPredictorBaseError("Blind keys must be unique 2025 civil dates.")
    if set(working.columns) & FORBIDDEN_COLUMNS:
        raise FinalTestPredictorBaseError("Blind key universe contains target-derived fields.")

    static_working = static.copy()
    if "tract_geoid" not in static_working or static_working["tract_geoid"].duplicated().any():
        raise FinalTestPredictorBaseError("Static table requires one row per tract.")
    static_working["tract_geoid"] = static_working["tract_geoid"].astype("string")
    if set(static_working["tract_geoid"]) != set(working["tract_geoid"]):
        raise FinalTestPredictorBaseError("Static and final-test tract universes differ.")

    calendar_names = list(CALENDAR_MODEL_FEATURE_NAMES)
    static_names = [name for name in model_feature_names if name in static_working.columns]
    if len(static_names) != 18 or not set(calendar_names).issubset(model_feature_names):
        raise FinalTestPredictorBaseError("Formal M2 static/calendar feature lock drifted.")
    calendar = build_calendar_features(
        working.loc[:, list(KEY_COLUMNS)],
        final_test_year=FINAL_TEST_YEAR,
        unlock_final_test=True,
    )
    result = working.merge(
        static_working[["tract_geoid", *static_names]],
        on="tract_geoid",
        how="left",
        sort=False,
        validate="many_to_one",
    ).merge(
        calendar,
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    )
    ordered = [*KEY_COLUMNS, *AUDIT_COLUMNS, *static_names, *calendar_names]
    result = result.loc[:, ordered].sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    numeric = result[[*static_names, *calendar_names]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise FinalTestPredictorBaseError("Static/calendar predictors are not complete.")
    return result, [*static_names, *calendar_names]


def _authenticate_existing(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path, label="Predictor-base provenance")
    _verify_commit(payload, label="Predictor-base provenance")
    record = payload.get("output_files", {}).get(OUTPUT_FILENAME)
    output = Path(str(record.get("path", ""))) if isinstance(record, dict) else Path()
    _locked_file(output, record, label="Predictor-base output")
    return payload


def build_final_test_predictor_base_artifacts(
    *,
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    inventory_directory: str | Path = "manifests/final_test_2025/landsat_inventory",
    static_path: str | Path = "data/processed/static_features/static_features.parquet",
    static_provenance_path: str | Path = (
        "data/processed/static_features/static_features_provenance.json"
    ),
    output_directory: str | Path = "data/interim/final_test_2025/predictor_base",
    provenance_path: str | Path = (
        "manifests/final_test_2025/predictor_base/PREDICTOR_BASE.json"
    ),
) -> dict[str, Any]:
    """Authenticate and freeze local static/calendar final-test predictors."""

    root = Path(__file__).resolve().parents[2]

    def resolve(value: str | Path) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (root / path).resolve()

    marker = resolve(provenance_path)
    existing = _authenticate_existing(marker)
    if existing is not None:
        return existing
    output = resolve(output_directory)
    output_path = output / OUTPUT_FILENAME
    if output_path.exists():
        raise FinalTestPredictorBaseError("Uncommitted predictor-base output already exists.")

    formal, formal_sha256 = authenticate_formal_model_lock(resolve(formal_lock_path))
    inventory_dir = resolve(inventory_directory)
    inventory_path = inventory_dir / SUMMARY_FILENAME
    inventory = _read_json(inventory_path, label="Final-test Landsat inventory")
    key_path, inventory_lock = _validate_inventory(
        inventory,
        inventory_directory=inventory_dir,
        formal_lock=formal,
        formal_lock_sha256=formal_sha256,
    )
    static_file = resolve(static_path)
    static_provenance_file = resolve(static_provenance_path)
    static_provenance = _read_json(
        static_provenance_file, label="Static feature provenance"
    )
    static_commit = _validate_static_provenance(
        static_provenance,
        static_path=static_file,
        inventory=inventory,
    )
    keys = pd.read_parquet(key_path)
    static = pd.read_parquet(static_file)
    if sha256_file(key_path) != inventory_lock["record"]["sha256"]:
        raise FinalTestPredictorBaseError("Key universe changed while being read.")
    static_record = static_provenance["output_files"][static_file.name]
    if sha256_file(static_file) != static_record["sha256"]:
        raise FinalTestPredictorBaseError("Static table changed while being read.")

    model_features = formal["models"]["M2"]["feature_names"]
    result, feature_names = build_predictor_base_frame(
        keys,
        static,
        model_feature_names=list(model_features),
    )
    atomic_parquet(result, output_path)
    pipeline_sha256, pipeline = code_runtime_fingerprint(
        project_root=root,
        relative_paths=(
            "scripts/build_final_test_predictor_base.py",
            "src/la_heat/calendar_features.py",
            "src/la_heat/final_test_inventory.py",
            "src/la_heat/final_test_predictor_base.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=ALGORITHM_VERSION,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "final_test_year": FINAL_TEST_YEAR,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "row_count": len(result),
        "date_count": int(result["target_date"].nunique()),
        "tract_count": int(result["tract_geoid"].nunique()),
        "feature_names": feature_names,
        "feature_count": len(feature_names),
        "semantic_table_sha256": canonical_frame_sha256(
            result, sort_by=["target_date", "tract_geoid"]
        ),
        "formal_model_lock": {
            "path": str(resolve(formal_lock_path)),
            "sha256": formal_sha256,
            "commit_sha256": formal["commit_sha256"],
        },
        "inputs": {
            "landsat_inventory": {
                "path": str(inventory_path),
                "sha256": sha256_file(inventory_path),
                "commit_sha256": inventory_lock["commit_sha256"],
            },
            "key_universe": {"path": str(key_path), **inventory_lock["record"]},
            "static_features": {
                "path": str(static_file),
                **static_record,
            },
            "static_provenance": {
                "path": str(static_provenance_file),
                "sha256": sha256_file(static_provenance_file),
                "commit_sha256": static_commit,
            },
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline,
        "output_files": {
            OUTPUT_FILENAME: {"path": str(output_path), **parquet_file_record(output_path, result)}
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload

