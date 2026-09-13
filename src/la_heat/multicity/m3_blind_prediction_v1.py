"""Authorized target-blind prediction with the frozen source-selected M3 model."""

from __future__ import annotations

import json
import os
import pickle
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity.m3_development import (
    KEY_COLUMNS,
    M2_FEATURES,
    predict_m3,
    u0_cross_conformal_correction,
    validate_prediction_feature_frame,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-prediction-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_PREDICTION_V1_AUTHORIZATION.json"
)
SOURCE_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_joint_nested_loso_v1/"
    "SOURCE_NESTED_LOSO_COMPLETE.json"
)
BLIND_PREDICTOR_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTORS_46_COMPLETE.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_prediction_v1/M3_BLIND_PREDICTIONS_COMMITTED.json"
)
OUTPUT_ROOT: Final = Path("data/processed/multicity/m3_blind_prediction_v1")
MODEL_PATH: Final = Path(
    "data/processed/multicity/m3_source_joint_nested_loso_v1/joint/selected_source_model.pkl"
)
OOF_PATH: Final = Path(
    "data/processed/multicity/m3_source_joint_nested_loso_v1/joint/outer_oof_predictions.parquet"
)
SOURCE_COMPLETION_COMMIT: Final = "207d45f8fdc7237f6347ed69b1c67733df353a3331e622707e93c4b3f21c34d3"
BLIND_PREDICTOR_COMPLETION_COMMIT: Final = (
    "efd100881992273fd17101b6687e84a9a24cb00d52726eec489ba9b2331b3661"
)
BLIND_CITY_IDS: Final = ("seattle_wa", "denver_co", "atlanta_ga", "miami_fl")
EXPECTED_ROWS: Final = {
    "seattle_wa": 9_558,
    "denver_co": 5_425,
    "atlanta_ga": 4_844,
    "miami_fl": 3_840,
}
OUTPUT_COLUMNS: Final = (
    *KEY_COLUMNS,
    "m3_level_prediction_c",
    "m3_anomaly_prediction_c",
    "m3_prediction_c",
    "m3_conformal_correction_c",
    "m3_lower_c",
    "m3_upper_c",
    "m3_interval_width_c",
    "uq_method",
    "risk_method",
    "m3_abstain",
    "m3_accepted",
)
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_prediction_v1.py",
    "scripts/authorize_m3_blind_prediction_v1.py",
    "scripts/run_m3_blind_prediction_v1.py",
)


class M3BlindPredictionError(RuntimeError):
    """Raised when the immutable blind-prediction contract is violated."""


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictionError("Path escapes the project root.")
    return path


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read_committed(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictionError(f"Cannot read committed JSON: {path}") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictionError("Committed JSON is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindPredictionError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, path: Path, *, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        result["rows"] = rows
    return result


def _write_json_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise M3BlindPredictionError(f"Append-only artifact exists: {destination}")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_parquet_exclusive(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise M3BlindPredictionError(f"Append-only artifact exists: {destination}")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        frame.to_parquet(temporary, index=False)
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_anchors(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _read_committed(_inside(root, SOURCE_COMPLETION_PATH))
    blind = _read_committed(_inside(root, BLIND_PREDICTOR_COMPLETION_PATH))
    if (
        source.get("commit_sha256") != SOURCE_COMPLETION_COMMIT
        or blind.get("commit_sha256") != BLIND_PREDICTOR_COMPLETION_COMMIT
        or source.get("selected_uq_method") != "unweighted_cross_conformal"
        or source.get("selected_risk_method") != "none_accept_all"
        or tuple(source.get("blind_test_city_ids", ())) != BLIND_CITY_IDS
        or tuple(row.get("city_id") for row in blind.get("city_context", ())) != BLIND_CITY_IDS
        or blind.get("row_count") != sum(EXPECTED_ROWS.values())
        or blind.get("feature_names") != list(M2_FEATURES)
    ):
        raise M3BlindPredictionError("Frozen scientific anchors changed.")
    return source, blind


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build the metadata-only authorization without opening predictor/model values."""

    root = Path(project_root).resolve()
    source, blind = _manifest_anchors(root)
    code = [_record(root, _inside(root, path)) for path in CODE_PATHS]
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_prediction_authorized",
        "source_nested_loso_completion_commit_sha256": source["commit_sha256"],
        "blind_predictors_completion_commit_sha256": blind["commit_sha256"],
        "selected_joint_candidate_id": source["selected_joint_candidate_id"],
        "selected_qa_id": source["selected_qa_id"],
        "selected_m3_candidate_id": source["selected_m3_candidate_id"],
        "selected_uq_method": "unweighted_cross_conformal",
        "selected_risk_method": "none_accept_all",
        "model_record": next(
            row
            for row in _read_committed(
                _inside(
                    root,
                    "data/processed/multicity/m3_source_joint_nested_loso_v1/"
                    "JOINT_NESTED_LOSO_STAGE_COMPLETE.json",
                )
            )["artifacts"]
            if row["role"] == "selected_source_model"
        ),
        "oof_record": next(
            row
            for row in _read_committed(
                _inside(
                    root,
                    "data/processed/multicity/m3_source_joint_nested_loso_v1/"
                    "JOINT_NESTED_LOSO_STAGE_COMPLETE.json",
                )
            )["artifacts"]
            if row["role"] == "outer_oof_predictions"
        ),
        "blind_predictor_records": blind["city_outputs"],
        "city_context": blind["city_context"],
        "code": code,
        "permissions": {
            "read_frozen_source_model_and_oof_residuals": True,
            "read_completed_blind_predictor_values": True,
            "write_target_blind_predictions": True,
            "fit_retrain_retune_or_select": False,
            "read_blind_landsat_hrefs_thermal_qa_or_targets": False,
            "network_or_href_reads": False,
            "score_or_evaluate_against_blind_targets": False,
        },
        "output_contract": {
            "city_ids": list(BLIND_CITY_IDS),
            "row_count": sum(EXPECTED_ROWS.values()),
            "columns": list(OUTPUT_COLUMNS),
            "prediction_before_target_boundary": True,
        },
        "next_safe_stage": "run_offline_blind_prediction_before_any_target_access",
    }
    return _committed(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, AUTHORIZATION_PATH)
    payload = build_authorization(root)
    _write_json_exclusive(payload, destination)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(_inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindPredictionError("Blind-prediction authorization drifted.")
    return observed


def _matches(root: Path, record: Mapping[str, Any], expected: Path) -> bool:
    path = _inside(root, str(record.get("path", "")))
    return (
        path == _inside(root, expected)
        and path.is_file()
        and path.stat().st_size == record.get("bytes")
        and sha256_file(path) == record.get("sha256")
    )


def _validate_output(frame: pd.DataFrame, city_id: str) -> pd.DataFrame:
    if tuple(frame.columns) != OUTPUT_COLUMNS or len(frame) != EXPECTED_ROWS[city_id]:
        raise M3BlindPredictionError(f"{city_id} prediction shape changed.")
    result = frame.copy()
    if (
        result["city_id"].astype(str).ne(city_id).any()
        or result.duplicated(list(KEY_COLUMNS)).any()
        or result["uq_method"].ne("unweighted_cross_conformal").any()
        or result["risk_method"].ne("none_accept_all").any()
        or result["m3_abstain"].any()
        or not result["m3_accepted"].all()
    ):
        raise M3BlindPredictionError(f"{city_id} prediction semantics changed.")
    numeric = result.loc[
        :,
        [
            "m3_level_prediction_c",
            "m3_anomaly_prediction_c",
            "m3_prediction_c",
            "m3_conformal_correction_c",
            "m3_lower_c",
            "m3_upper_c",
            "m3_interval_width_c",
        ],
    ].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy()).all():
        raise M3BlindPredictionError(f"{city_id} prediction contains non-finite values.")
    if not (
        np.allclose(
            numeric["m3_prediction_c"],
            numeric["m3_level_prediction_c"] + numeric["m3_anomaly_prediction_c"],
        )
        and np.allclose(
            numeric["m3_lower_c"],
            numeric["m3_prediction_c"] - numeric["m3_conformal_correction_c"],
        )
        and np.allclose(
            numeric["m3_upper_c"],
            numeric["m3_prediction_c"] + numeric["m3_conformal_correction_c"],
        )
        and np.allclose(
            numeric["m3_interval_width_c"],
            2 * numeric["m3_conformal_correction_c"],
        )
    ):
        raise M3BlindPredictionError(f"{city_id} prediction arithmetic changed.")
    return result


def run(project_root: str | Path) -> dict[str, Any]:
    """Create and authenticate immutable predictions without target or network access."""

    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    if _inside(root, COMPLETION_PATH).exists():
        return authenticate_completion(root)
    if not _matches(root, permit["model_record"], MODEL_PATH) or not _matches(
        root, permit["oof_record"], OOF_PATH
    ):
        raise M3BlindPredictionError("Frozen source artifact drifted.")
    for record, city_id in zip(permit["blind_predictor_records"], BLIND_CITY_IDS, strict=True):
        expected = Path(
            f"data/processed/multicity/m3_blind_predictor_build_v1/"
            f"predictors_46_v1/{city_id}/predictors_46.parquet"
        )
        if not _matches(root, record, expected) or record.get("rows") != EXPECTED_ROWS[city_id]:
            raise M3BlindPredictionError(f"{city_id} frozen predictor artifact drifted.")
    with _inside(root, MODEL_PATH).open("rb") as handle:
        model = pickle.load(handle)  # noqa: S301 -- hash-bound trusted local artifact
    oof = pd.read_parquet(_inside(root, OOF_PATH))
    required_oof = {*KEY_COLUMNS, "observed_lst_c", "m3_prediction_c"}
    if not required_oof <= set(oof.columns) or len(oof) != 96_904:
        raise M3BlindPredictionError("Frozen OOF calibration table changed.")
    correction = u0_cross_conformal_correction(
        np.abs(oof["observed_lst_c"].to_numpy(float) - oof["m3_prediction_c"].to_numpy(float)),
        oof.loc[:, KEY_COLUMNS],
    )
    contexts = {row["city_id"]: row["city_centroid_latitude_deg"] for row in permit["city_context"]}
    outputs = []
    for record, city_id in zip(permit["blind_predictor_records"], BLIND_CITY_IDS, strict=True):
        predictors = pd.read_parquet(_inside(root, record["path"]))
        predictors = validate_prediction_feature_frame(predictors, required_features=M2_FEATURES)
        predictors["city_centroid_latitude_deg"] = float(contexts[city_id])
        predicted = predict_m3(model, predictors)
        predicted["m3_conformal_correction_c"] = correction
        predicted["m3_lower_c"] = predicted["m3_prediction_c"] - correction
        predicted["m3_upper_c"] = predicted["m3_prediction_c"] + correction
        predicted["m3_interval_width_c"] = 2.0 * correction
        predicted["uq_method"] = "unweighted_cross_conformal"
        predicted["risk_method"] = "none_accept_all"
        predicted["m3_abstain"] = False
        predicted["m3_accepted"] = True
        predicted = _validate_output(predicted.loc[:, OUTPUT_COLUMNS], city_id)
        destination = _inside(root, OUTPUT_ROOT / city_id / "predictions.parquet")
        _write_parquet_exclusive(predicted, destination)
        output = _record(root, destination, rows=len(predicted))
        output["city_id"] = city_id
        output["semantic_sha256"] = canonical_frame_sha256(predicted, sort_by=list(KEY_COLUMNS))
        outputs.append(output)
        print(f"BLIND_PREDICTION_COMPLETE {city_id} {len(outputs)}/4", flush=True)
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictions_committed",
            "authorization_commit_sha256": permit["commit_sha256"],
            "source_nested_loso_completion_commit_sha256": SOURCE_COMPLETION_COMMIT,
            "blind_predictors_completion_commit_sha256": BLIND_PREDICTOR_COMPLETION_COMMIT,
            "city_count": 4,
            "row_count": sum(EXPECTED_ROWS.values()),
            "output_columns": list(OUTPUT_COLUMNS),
            "selected_joint_candidate_id": permit["selected_joint_candidate_id"],
            "uq_method": "unweighted_cross_conformal",
            "conformal_probability": 0.90,
            "conformal_correction_c": correction,
            "risk_method": "none_accept_all",
            "city_outputs": outputs,
            "audit": {
                "network_requests": 0,
                "href_reads": 0,
                "model_fit_retrain_retune_or_select": False,
                "blind_landsat_thermal_qa_or_target_values_read": False,
                "prediction_before_target_boundary_preserved": True,
            },
            "next_safe_stage": "authorize_blind_target_access_and_evaluation_separately",
        }
    )
    _write_json_exclusive(completion, _inside(root, COMPLETION_PATH))
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    completion = _read_committed(_inside(root, COMPLETION_PATH))
    if (
        completion.get("authorization_commit_sha256") != permit["commit_sha256"]
        or completion.get("state") != "m3_blind_predictions_committed"
        or completion.get("row_count") != sum(EXPECTED_ROWS.values())
        or completion.get("audit", {}).get("prediction_before_target_boundary_preserved")
        is not True
        or completion.get("audit", {}).get("blind_landsat_thermal_qa_or_target_values_read")
        is not False
    ):
        raise M3BlindPredictionError("Blind-prediction completion changed.")
    for record, city_id in zip(completion["city_outputs"], BLIND_CITY_IDS, strict=True):
        path = _inside(root, record["path"])
        frame = _validate_output(pd.read_parquet(path), city_id)
        if not _matches(
            root, record, OUTPUT_ROOT / city_id / "predictions.parquet"
        ) or canonical_frame_sha256(frame, sort_by=list(KEY_COLUMNS)) != record.get(
            "semantic_sha256"
        ):
            raise M3BlindPredictionError(f"{city_id} committed prediction drifted.")
    return completion
