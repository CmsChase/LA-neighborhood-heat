"""Append-only repair that publishes the exact frozen 21-column M3 schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity import m3_blind_prediction_v1 as v1
from la_heat.multicity.m3_development import (
    B1_FEATURES,
    KEY_COLUMNS,
    M2_FEATURES,
    M3_CANDIDATES,
    PREDICTION_COLUMNS,
    build_b1_estimator,
    build_m2_legacy_estimator,
    city_date_row_weights,
    fit_m3_candidate,
    predict_m3,
    validate_prediction_output,
)
from la_heat.multicity.m3_source_joint_nested_loso_v1 import (
    load_authorized_source_inputs,
    prepare_qa_dataset,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-prediction-v2-exact-schema-repair"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_PREDICTION_V2_REPAIR_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_prediction_v2/M3_BLIND_PREDICTIONS_COMMITTED.json"
)
OUTPUT_ROOT: Final = Path("data/processed/multicity/m3_blind_prediction_v2")
PROTOCOL_PATH: Final = Path("manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json")
SOURCE_AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_JOINT_NESTED_LOSO_V1_AUTHORIZATION.json"
)
V1_COMPLETION_COMMIT: Final = "4d90bca2ae6aa28233a05e3e0419b165e8c11844843bb93489b4c4d74837f20b"
PROTOCOL_COMMIT: Final = "dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8"
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_prediction_v1.py",
    "src/la_heat/multicity/m3_blind_prediction_v2.py",
    "scripts/authorize_m3_blind_prediction_v2.py",
    "scripts/run_m3_blind_prediction_v2.py",
)


class M3BlindPredictionV2Error(RuntimeError):
    """Raised when the exact-schema repair contract is violated."""


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read_committed(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictionV2Error(f"Cannot read committed JSON: {path}") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictionV2Error("Committed JSON is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindPredictionV2Error(f"Invalid commit: {path}")
    return payload


def _code_record(root: Path, relative: str) -> dict[str, Any]:
    path = v1._inside(root, relative)
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build metadata-only repair permission before any source value re-read."""

    root = Path(project_root).resolve()
    protocol = _read_committed(v1._inside(root, PROTOCOL_PATH))
    v1_completion = _read_committed(v1._inside(root, v1.COMPLETION_PATH))
    source_authorization = _read_committed(v1._inside(root, SOURCE_AUTHORIZATION_PATH))
    if (
        protocol.get("commit_sha256") != PROTOCOL_COMMIT
        or protocol.get("development_contract", {})
        .get("outputs", {})
        .get("prediction_column_count")
        != 21
        or tuple(
            protocol.get("development_contract", {})
            .get("outputs", {})
            .get("prediction_columns", ())
        )
        != PREDICTION_COLUMNS
        or v1_completion.get("commit_sha256") != V1_COMPLETION_COMMIT
        or v1_completion.get("audit", {}).get("prediction_before_target_boundary_preserved")
        is not True
        or v1_completion.get("audit", {}).get("blind_landsat_thermal_qa_or_target_values_read")
        is not False
        or source_authorization.get("state") != "m3_source_joint_nested_loso_v1_authorized"
    ):
        raise M3BlindPredictionV2Error("Repair anchors changed.")
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_prediction_v2_repair_authorized",
        "incident": {
            "v1_completion_commit_sha256": V1_COMPLETION_COMMIT,
            "observed_column_count": 14,
            "required_column_count": 21,
            "disposition": "retain_v1_as_superseded_audit_record",
        },
        "protocol_commit_sha256": PROTOCOL_COMMIT,
        "source_authorization_commit_sha256": source_authorization["commit_sha256"],
        "code": [_code_record(root, relative) for relative in CODE_PATHS],
        "permissions": {
            "reread_authorized_source_predictors_and_selected_4k_targets": True,
            "fit_frozen_b1_m2_and_four_member_m3_diagnostic_ensemble": True,
            "reuse_v1_frozen_m3_point_predictions_and_uq_correction": True,
            "write_exact_21_column_predictions": True,
            "change_selected_qa_model_uq_or_risk": False,
            "read_blind_landsat_hrefs_thermal_qa_or_targets": False,
            "network_or_href_reads": False,
            "score_or_evaluate_blind_targets": False,
        },
        "output_contract": {
            "columns": list(PREDICTION_COLUMNS),
            "row_count": sum(v1.EXPECTED_ROWS.values()),
            "city_ids": list(v1.BLIND_CITY_IDS),
            "prediction_before_target_boundary": True,
        },
        "next_safe_stage": "run_v2_exact_schema_repair_before_target_authorization",
    }
    return _committed(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_authorization(root)
    v1._write_json_exclusive(payload, v1._inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(v1._inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindPredictionV2Error("V2 repair authorization drifted.")
    return observed


def _fit_diagnostic_models(project_root: Path) -> tuple[Any, Any, list[Any]]:
    predictors, targets = load_authorized_source_inputs(project_root)
    prepared = prepare_qa_dataset(predictors, targets["4k"], qa_id="4k")
    weights = city_date_row_weights(prepared.frame)
    b1 = build_b1_estimator()
    b1.fit(
        prepared.frame.loc[:, B1_FEATURES],
        prepared.target,
        model__sample_weight=weights,
    )
    m2 = build_m2_legacy_estimator()
    m2.fit(
        prepared.frame.loc[:, M2_FEATURES],
        prepared.target,
        model__sample_weight=weights,
    )
    ensemble = [
        fit_m3_candidate(prepared.frame, prepared.target, candidate) for candidate in M3_CANDIDATES
    ]
    return b1, m2, ensemble


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    if v1._inside(root, COMPLETION_PATH).exists():
        return authenticate_completion(root)
    v1_completion = v1.authenticate_completion(root)
    b1, m2, ensemble = _fit_diagnostic_models(root)
    records = []
    for v1_record, city_id in zip(v1_completion["city_outputs"], v1.BLIND_CITY_IDS, strict=True):
        base = pd.read_parquet(v1._inside(root, v1_record["path"]))
        predictor_record = next(
            row for row in permit["output_contract"]["city_ids"] if row == city_id
        )
        if predictor_record != city_id:
            raise M3BlindPredictionV2Error("City order changed.")
        blind_completion = _read_committed(v1._inside(root, v1.BLIND_PREDICTOR_COMPLETION_PATH))
        source = next(
            row
            for row in blind_completion["city_outputs"]
            if Path(row["path"]).parts[-2] == city_id
        )
        predictors = pd.read_parquet(v1._inside(root, source["path"]))
        context = next(row for row in blind_completion["city_context"] if row["city_id"] == city_id)
        predictors["city_centroid_latitude_deg"] = context["city_centroid_latitude_deg"]
        result = base.loc[:, KEY_COLUMNS].copy()
        result["b1_prediction_c"] = b1.predict(predictors.loc[:, B1_FEATURES])
        result["m2_legacy_prediction_c"] = m2.predict(predictors.loc[:, M2_FEATURES])
        result = result.merge(
            base.loc[:, [column for column in v1.OUTPUT_COLUMNS if column not in KEY_COLUMNS]],
            left_index=True,
            right_index=True,
            validate="one_to_one",
        )
        members = np.vstack(
            [predict_m3(model, predictors)["m3_prediction_c"].to_numpy(float) for model in ensemble]
        )
        result["m3_ensemble_point_sd_c"] = np.std(members, axis=0, ddof=0)
        result["uq_density_ratio_raw"] = 1.0
        result["uq_density_ratio_clipped"] = 1.0
        result["uq_weight_clip_hit"] = False
        result["m3_predicted_absolute_error_c"] = 0.0
        result["m3_risk_percentile_within_city_date"] = 0.5
        result = validate_prediction_output(result.loc[:, PREDICTION_COLUMNS])
        destination = v1._inside(root, OUTPUT_ROOT / city_id / "predictions.parquet")
        v1._write_parquet_exclusive(result, destination)
        record = v1._record(root, destination, rows=len(result))
        record["city_id"] = city_id
        record["semantic_sha256"] = canonical_frame_sha256(result, sort_by=list(KEY_COLUMNS))
        records.append(record)
        print(f"BLIND_PREDICTION_V2_COMPLETE {city_id} {len(records)}/4", flush=True)
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictions_v2_committed",
            "authorization_commit_sha256": permit["commit_sha256"],
            "supersedes_v1_completion_commit_sha256": V1_COMPLETION_COMMIT,
            "protocol_commit_sha256": PROTOCOL_COMMIT,
            "city_count": 4,
            "row_count": sum(v1.EXPECTED_ROWS.values()),
            "output_columns": list(PREDICTION_COLUMNS),
            "selected_qa_id": "4k",
            "selected_m3_candidate_id": "level_ridge_alpha_10__anomaly_hgb_leaves_31",
            "uq_method": "unweighted_cross_conformal",
            "risk_method": "none_accept_all",
            "none_accept_all_numeric_sentinels": {
                "m3_predicted_absolute_error_c": 0.0,
                "m3_risk_percentile_within_city_date": 0.5,
            },
            "city_outputs": records,
            "audit": {
                "source_only_diagnostic_refit": True,
                "blind_target_score_or_evaluation": False,
                "network_requests": 0,
                "href_reads": 0,
                "blind_landsat_thermal_qa_or_target_values_read": False,
                "prediction_before_target_boundary_preserved": True,
            },
            "next_safe_stage": "authorize_blind_target_access_and_evaluation_separately",
        }
    )
    v1._write_json_exclusive(completion, v1._inside(root, COMPLETION_PATH))
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    payload = _read_committed(v1._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or tuple(payload.get("output_columns", ())) != PREDICTION_COLUMNS
        or payload.get("row_count") != sum(v1.EXPECTED_ROWS.values())
        or payload.get("audit", {}).get("prediction_before_target_boundary_preserved") is not True
    ):
        raise M3BlindPredictionV2Error("V2 completion changed.")
    for record, city_id in zip(payload["city_outputs"], v1.BLIND_CITY_IDS, strict=True):
        path = v1._inside(root, record["path"])
        frame = validate_prediction_output(pd.read_parquet(path))
        if (
            not v1._matches(root, record, OUTPUT_ROOT / city_id / "predictions.parquet")
            or len(frame) != v1.EXPECTED_ROWS[city_id]
            or canonical_frame_sha256(frame, sort_by=list(KEY_COLUMNS))
            != record.get("semantic_sha256")
        ):
            raise M3BlindPredictionV2Error(f"{city_id} V2 output drifted.")
    return payload
