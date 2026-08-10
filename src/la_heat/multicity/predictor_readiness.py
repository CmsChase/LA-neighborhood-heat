"""Target-blind readiness audit for the completed four-city predictor table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity.portable_sentinel_build import FINAL_COMPLETE, FINAL_OUTPUT
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

CONTRACT_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
REPORT_PATH: Final = Path(
    "data/processed/multicity/portable_predictors/readiness/"
    "TRAINING_PREFLIGHT.json"
)
EXPECTED_CITY_ROWS: Final = {
    "los_angeles_ca": 98_640,
    "phoenix_az": 8_250,
    "houston_tx": 13_671,
    "chicago_il": 16_380,
}
EXPECTED_SPLIT_ROWS: Final = {
    "los_angeles_2020_2023_training": 73_432,
    "los_angeles_2024_calibration": 25_208,
    "external_cities_2025_prediction": 38_301,
}


class PredictorReadinessError(RuntimeError):
    """Raised when the target-blind predictor table violates its contract."""


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PredictorReadinessError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict):
        raise PredictorReadinessError(f"{label} must be a JSON object.")
    return payload


def _authenticate_commit(payload: dict[str, Any], *, label: str) -> str:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise PredictorReadinessError(f"{label} commit is invalid.")
    return recorded


def validate_predictor_frame(
    frame: pd.DataFrame,
    *,
    key_columns: list[str],
    feature_order: list[str],
) -> dict[str, object]:
    """Validate schema and missingness without reading targets or fitting a model."""

    if len(key_columns) != len(set(key_columns)) or len(feature_order) != len(
        set(feature_order)
    ):
        raise PredictorReadinessError("Predictor contract contains duplicate columns.")
    required = [*key_columns, *feature_order]
    missing_columns = [column for column in required if column not in frame]
    if missing_columns:
        raise PredictorReadinessError(
            f"Predictor table lacks frozen columns: {missing_columns}"
        )
    if frame.empty or frame.duplicated(key_columns).any():
        raise PredictorReadinessError("Predictor keys are empty or duplicated.")
    if frame[key_columns].isna().any(axis=None):
        raise PredictorReadinessError("Predictor keys contain missing values.")
    try:
        numeric = frame[feature_order].to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise PredictorReadinessError("Frozen predictors must be numeric.") from error
    if np.isinf(numeric).any():
        raise PredictorReadinessError("Frozen predictors contain infinite values.")

    sentinel = [name for name in feature_order if name.startswith("sentinel_")]
    if sentinel:
        sentinel_missing = frame[sentinel].isna().sum(axis=1)
        if not sentinel_missing.isin([0, len(sentinel)]).all():
            raise PredictorReadinessError(
                "Sentinel predictors must be either all present or all missing per row."
            )
        sentinel_available = int((sentinel_missing == 0).sum())
    else:
        sentinel_available = len(frame)

    static_calendar = [
        name
        for name in feature_order
        if not name.startswith(("daymet_", "sentinel_"))
    ]
    if static_calendar and frame[static_calendar].isna().any(axis=None):
        raise PredictorReadinessError("Static/calendar predictors contain missing values.")
    return {
        "row_count": len(frame),
        "feature_count": len(feature_order),
        "metadata_columns": [
            name for name in frame.columns if name not in {*key_columns, *feature_order}
        ],
        "sentinel_available_row_count": sentinel_available,
        "sentinel_missing_row_count": len(frame) - sentinel_available,
    }


def _split_counts(frame: pd.DataFrame) -> dict[str, int]:
    city = frame["city_id"].astype(str)
    dates = pd.to_datetime(frame["target_date"], errors="raise")
    la = city.eq("los_angeles_ca")
    external = city.ne("los_angeles_ca")
    return {
        "los_angeles_2020_2023_training": int((la & dates.dt.year.between(2020, 2023)).sum()),
        "los_angeles_2024_calibration": int((la & dates.dt.year.eq(2024)).sum()),
        "external_cities_2025_prediction": int((external & dates.dt.year.eq(2025)).sum()),
    }


def audit_multicity_predictor_readiness(
    project_root: str | Path,
    *,
    write_report: bool = False,
) -> dict[str, Any]:
    """Return waiting or ready state without opening any Landsat target values."""

    root = Path(project_root).resolve()
    contract = _read_json(root / CONTRACT_PATH, label="portable predictor contract")
    contract_commit = _authenticate_commit(contract, label="portable predictor contract")
    if not (root / FINAL_OUTPUT).is_file() or not (root / FINAL_COMPLETE).is_file():
        return {
            "schema_version": 1,
            "state": "waiting_for_completed_sentinel_predictors",
            "contract_commit_sha256": contract_commit,
            "expected_predictor_path": FINAL_OUTPUT.as_posix(),
            "external_target_values_read": False,
            "model_fit_performed": False,
        }

    complete = _read_json(root / FINAL_COMPLETE, label="46-feature completion record")
    complete_commit = _authenticate_commit(complete, label="46-feature completion")
    output = complete.get("output")
    if (
        complete.get("state") != "complete_target_blind_46_feature_predictors"
        or complete.get("feature_count") != 46
        or complete.get("row_count") != sum(EXPECTED_CITY_ROWS.values())
        or not isinstance(output, dict)
        or output.get("path") != FINAL_OUTPUT.as_posix()
        or output.get("bytes") != (root / FINAL_OUTPUT).stat().st_size
        or output.get("sha256") != sha256_file(root / FINAL_OUTPUT)
    ):
        raise PredictorReadinessError("46-feature completion record is inconsistent.")

    registry = contract.get("feature_registry")
    model_contract = contract.get("model_contract")
    if not isinstance(registry, dict) or not isinstance(model_contract, dict):
        raise PredictorReadinessError("Predictor/model contract is incomplete.")
    key_columns = [str(value) for value in registry.get("key_columns", [])]
    feature_order = [str(value) for value in registry.get("feature_order", [])]
    if (
        key_columns != ["city_id", "tract_geoid", "target_date"]
        or len(feature_order) != 46
        or model_contract.get("m2_feature_order") != feature_order
        or complete.get("feature_order") != feature_order
    ):
        raise PredictorReadinessError("Frozen key or feature order changed.")

    frame = pd.read_parquet(root / FINAL_OUTPUT)
    audit = validate_predictor_frame(
        frame,
        key_columns=key_columns,
        feature_order=feature_order,
    )
    observed_city_rows = {
        str(city_id): int(count)
        for city_id, count in frame.groupby("city_id", observed=True).size().items()
    }
    if observed_city_rows != EXPECTED_CITY_ROWS:
        raise PredictorReadinessError(
            f"Four-city row counts changed: {observed_city_rows}"
        )
    observed_split_rows = _split_counts(frame)
    if observed_split_rows != EXPECTED_SPLIT_ROWS:
        raise PredictorReadinessError(
            f"Frozen train/calibration/prediction splits changed: {observed_split_rows}"
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "state": "ready_for_protocol_lock_not_model_fit",
        "contract_commit_sha256": contract_commit,
        "predictor_complete_commit_sha256": complete_commit,
        "predictor_sha256": str(output["sha256"]),
        **audit,
        "city_row_counts": observed_city_rows,
        "split_row_counts": observed_split_rows,
        "external_target_values_read": False,
        "model_fit_performed": False,
        "next_safe_stage": "lock_multicity_evaluation_protocol",
    }
    report["commit_sha256"] = canonical_sha256(report)
    if write_report:
        atomic_json(report, root / REPORT_PATH)
    return report
