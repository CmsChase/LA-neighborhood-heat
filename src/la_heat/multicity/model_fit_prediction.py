"""Authorized LA-only fit and target-blind external prediction publication.

This module is the G5 production orchestrator.  It authenticates the separate
model-fit permit before reading either the completed public predictor table or
the Los Angeles source target.  Phoenix, Houston, and Chicago targets are not
an input and are never discovered or opened here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
import pandas as pd

from la_heat.model_rows import select_absolute_model_rows
from la_heat.multicity.model_fit_authorization import (
    AUTHORIZATION_PATH,
    authenticate_model_fit_authorization,
)
from la_heat.multicity.predictor_readiness import validate_predictor_frame
from la_heat.multicity.transfer_model import (
    CALIBRATION_YEAR,
    EXTERNAL_YEAR,
    KEY_COLUMNS,
    TRAINING_CITY,
    TRAINING_YEARS,
    ConformalCalibration,
    FittedTransferModels,
    build_frozen_transfer_estimators,
    calibrate_frozen_intervals,
    load_frozen_transfer_contract,
    predict_external_cities,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.training_contract import date_balanced_sample_weights

ALGORITHM_VERSION: Final = "multicity-model-fit-prediction-v1"
DEFAULT_STATUS_PATH: Final = Path("data/interim/multicity/models/frozen_transfer/status.json")
MODEL_FILENAME: Final = "fitted_transfer_models.joblib"
FIT_AUDIT_FILENAME: Final = "fit_audit.json"
PREDICTION_FILENAME: Final = "external_predictions_2025.parquet"
COMPLETION_STATE: Final = "model_fit_complete_external_predictions_committed"
PREDICTION_COMMIT_STATE: Final = "external_predictions_committed_before_target_access"
PREDICTION_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "target_date",
    "b1_prediction_c",
    "m2_prediction_c",
    "m2_lower_c",
    "m2_upper_c",
    "m2_interval_width_c",
    "m2_abstain",
    "m2_accepted",
)
STAGES: Final = (
    "preflight",
    "join",
    "fit_b1",
    "fit_m2",
    "fit_q05",
    "fit_q95",
    "calibrate",
    "predict",
    "commit",
    "complete",
)


class ModelFitPredictionError(RuntimeError):
    """Raised when G5 inputs or outputs violate their frozen contracts."""


@dataclass(frozen=True, slots=True)
class PreparedFitData:
    training_predictors: pd.DataFrame
    training_target: pd.Series
    calibration_predictors: pd.DataFrame
    calibration_target: pd.Series
    external_predictors: pd.DataFrame
    selection_audit: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FitResult:
    models: FittedTransferModels
    calibration: ConformalCalibration
    predictions: pd.DataFrame


ProgressCallback = Callable[[str, Mapping[str, Any] | None], None]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ModelFitPredictionError(f"{label} must stay inside the project")
    return path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelFitPredictionError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise ModelFitPredictionError(f"{label} must be a JSON object")
    return payload


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    payload = _read_json(path, label=label)
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise ModelFitPredictionError(f"{label} commit is invalid")
    return payload


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _prediction_semantic(frame: pd.DataFrame) -> str:
    """Match the semantic identity consumed by external-target authorization."""

    normalized = frame.copy()
    for column in KEY_COLUMNS:
        normalized[column] = normalized[column].astype("string")
    return canonical_frame_sha256(
        normalized,
        sort_by=["city_id", "target_date", "tract_geoid"],
        columns=list(PREDICTION_COLUMNS),
    )


def _record_matches(root: Path, record: object) -> Path:
    if not isinstance(record, dict):
        raise ModelFitPredictionError("Output file record is invalid")
    path = _inside(root, str(record.get("path", "")), label="Output path")
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise ModelFitPredictionError("Output file failed its byte lock")
    return path


def _normalize_keys(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    missing = set(KEY_COLUMNS) - set(frame.columns)
    if missing:
        raise ModelFitPredictionError(f"{label} lacks keys: {sorted(missing)}")
    result = frame.copy()
    if result.loc[:, KEY_COLUMNS].isna().any(axis=None):
        raise ModelFitPredictionError(f"{label} contains missing keys")
    result["city_id"] = result["city_id"].astype(str)
    result["tract_geoid"] = result["tract_geoid"].astype(str)
    dates = pd.to_datetime(result["target_date"], errors="raise")
    if dates.isna().any():
        raise ModelFitPredictionError(f"{label} contains missing dates")
    result["target_date"] = dates.dt.strftime("%Y-%m-%d")
    if result.duplicated(list(KEY_COLUMNS)).any():
        raise ModelFitPredictionError(f"{label} contains duplicate keys")
    return result


def _reason_counts(frame: pd.DataFrame, mask: pd.Series, column: str) -> dict[str, int]:
    if column not in frame.columns or not mask.any():
        return {}
    values = frame.loc[mask, column].fillna("").astype(str).replace("", "unspecified")
    counts = values.value_counts(sort=False).sort_index()
    return {str(key): int(value) for key, value in counts.items()}


def prepare_fit_data(
    predictors: pd.DataFrame,
    source_targets: pd.DataFrame,
    contract: dict[str, Any],
    cohorts: Mapping[str, Any],
) -> PreparedFitData:
    """Validate the full LA key join, then apply the frozen QA eligibility gate."""

    feature_order = tuple(contract["feature_registry"]["feature_order"])
    predictors = _normalize_keys(predictors, label="Predictor table")
    validate_predictor_frame(
        predictors,
        key_columns=list(KEY_COLUMNS),
        feature_order=list(feature_order),
    )
    targets = _normalize_keys(source_targets, label="LA source target table")
    if set(targets["city_id"].unique()) != {TRAINING_CITY}:
        raise ModelFitPredictionError("Source target table must contain only Los Angeles")
    la_predictors = predictors.loc[predictors["city_id"].eq(TRAINING_CITY)].copy()
    external = predictors.loc[predictors["city_id"].ne(TRAINING_CITY)].copy()
    expected_la = int(cohorts["training_rows"]) + int(cohorts["calibration_rows"])
    if len(la_predictors) != expected_la or len(targets) != expected_la:
        raise ModelFitPredictionError("LA candidate row count changed")
    if len(external) != int(cohorts["external_rows"]):
        raise ModelFitPredictionError("External predictor row count changed")

    la_keys = (
        la_predictors.loc[:, KEY_COLUMNS]
        .sort_values(list(KEY_COLUMNS), kind="stable")
        .reset_index(drop=True)
    )
    target_keys = (
        targets.loc[:, KEY_COLUMNS]
        .sort_values(list(KEY_COLUMNS), kind="stable")
        .reset_index(drop=True)
    )
    if not la_keys.equals(target_keys):
        raise ModelFitPredictionError("LA predictor and target key universes differ")

    # This repository-wide selector is the normative QA rule.  It validates all
    # target flags and keeps only target_available AND date_usable with finite y.
    model_ready = select_absolute_model_rows(
        targets,
        final_test_year=EXTERNAL_YEAR,
        unlock_final_test=False,
    )
    target_columns = [*KEY_COLUMNS, "target_lst_c"]
    joined = la_predictors.merge(
        model_ready.loc[:, target_columns],
        on=list(KEY_COLUMNS),
        how="inner",
        validate="one_to_one",
    )
    dates = pd.to_datetime(joined["target_date"], errors="raise")
    train_mask = dates.dt.year.isin(TRAINING_YEARS)
    calibration_mask = dates.dt.year.eq(CALIBRATION_YEAR)
    if not (train_mask | calibration_mask).all():
        raise ModelFitPredictionError("LA usable targets contain a year outside 2020-2024")
    predictor_columns = [*KEY_COLUMNS, *feature_order]
    training = joined.loc[train_mask, predictor_columns].reset_index(drop=True)
    calibration = joined.loc[calibration_mask, predictor_columns].reset_index(drop=True)
    training_target = joined.loc[train_mask, "target_lst_c"].reset_index(drop=True)
    calibration_target = joined.loc[calibration_mask, "target_lst_c"].reset_index(drop=True)
    external = external.loc[:, predictor_columns].reset_index(drop=True)

    predictor_dates = pd.to_datetime(la_predictors["target_date"], errors="raise")
    target_available = targets["target_available"].astype(bool)
    date_usable = targets["date_usable"].astype(bool)
    target_present = targets["target_lst_c"].notna()
    eligible = target_available & date_usable & target_present

    def cohort_audit(years: set[int]) -> dict[str, Any]:
        mask = pd.to_datetime(targets["target_date"]).dt.year.isin(years)
        available_mask = mask & target_available
        usable_mask = mask & eligible
        return {
            "candidate_rows": int(mask.sum()),
            "candidate_dates": int(targets.loc[mask, "target_date"].nunique()),
            "usable_rows": int(usable_mask.sum()),
            "usable_dates": int(targets.loc[usable_mask, "target_date"].nunique()),
            "excluded_rows": int(mask.sum() - usable_mask.sum()),
            "target_unavailable_rows": int((mask & ~target_available).sum()),
            "date_unusable_after_target_available_rows": int((available_mask & ~date_usable).sum()),
            "tract_exclusion_reasons": _reason_counts(
                targets, mask & ~target_available, "tract_exclusion_reason"
            ),
            "date_exclusion_reasons": _reason_counts(
                targets, available_mask & ~date_usable, "date_exclusion_reason"
            ),
        }

    external_dates = pd.to_datetime(external["target_date"], errors="raise")
    if (
        set(external["city_id"].unique()) != set(cohorts["external_city_ids"])
        or not external_dates.dt.year.eq(int(cohorts["external_year"])).all()
    ):
        raise ModelFitPredictionError("External predictor cohort changed")
    audit = {
        "qa_selection_rule": (
            "target_available == true AND date_usable == true AND target_lst_c is finite"
        ),
        "full_la_key_join": {
            "predictor_rows": len(la_predictors),
            "target_rows": len(targets),
            "keys_equal": True,
            "candidate_dates": int(predictor_dates.nunique()),
        },
        "training": cohort_audit(set(TRAINING_YEARS)),
        "calibration": cohort_audit({CALIBRATION_YEAR}),
        "external_predictor_only": {
            "rows": len(external),
            "city_dates": int(
                external.loc[:, ["city_id", "target_date"]].drop_duplicates().shape[0]
            ),
            "city_row_counts": {
                str(key): int(value)
                for key, value in external["city_id"].value_counts(sort=False).sort_index().items()
            },
            "target_values_read": False,
        },
    }
    if audit["training"]["candidate_rows"] != int(cohorts["training_rows"]):
        raise ModelFitPredictionError("Training candidate cohort changed")
    if audit["calibration"]["candidate_rows"] != int(cohorts["calibration_rows"]):
        raise ModelFitPredictionError("Calibration candidate cohort changed")
    if training.empty or calibration.empty:
        raise ModelFitPredictionError("QA selection left an empty fit cohort")
    return PreparedFitData(
        training,
        training_target,
        calibration,
        calibration_target,
        external,
        audit,
    )


def fit_and_predict(
    data: PreparedFitData,
    contract: dict[str, Any],
    *,
    progress: ProgressCallback | None = None,
) -> FitResult:
    """Fit the four frozen estimators in named stages, then calibrate and predict."""

    callback = progress or (lambda _stage, _detail=None: None)
    estimators = build_frozen_transfer_estimators(contract)
    weights = date_balanced_sample_weights(
        data.training_predictors.loc[:, ["tract_geoid", "target_date"]]
    ).to_numpy(dtype=float)
    callback("fit_b1", None)
    estimators.b1.fit(
        data.training_predictors.loc[:, estimators.b1_feature_order],
        data.training_target,
        model__sample_weight=weights,
    )
    m2_frame = data.training_predictors.loc[:, estimators.m2_feature_order]
    callback("fit_m2", None)
    estimators.m2.fit(m2_frame, data.training_target, model__sample_weight=weights)
    callback("fit_q05", None)
    estimators.lower.fit(m2_frame, data.training_target, model__sample_weight=weights)
    callback("fit_q95", None)
    estimators.upper.fit(m2_frame, data.training_target, model__sample_weight=weights)
    training_dates = pd.to_datetime(data.training_predictors["target_date"], errors="raise")
    models = FittedTransferModels(
        estimators.b1,
        estimators.m2,
        estimators.lower,
        estimators.upper,
        estimators.b1_feature_order,
        estimators.m2_feature_order,
        len(data.training_predictors),
        int(training_dates.nunique()),
    )
    callback("calibrate", None)
    calibration = calibrate_frozen_intervals(
        models,
        data.calibration_predictors,
        data.calibration_target,
        contract,
    )
    callback("predict", None)
    predictions = predict_external_cities(models, calibration, data.external_predictors)
    if tuple(predictions.columns) != PREDICTION_COLUMNS:
        raise ModelFitPredictionError("External prediction schema changed")
    return FitResult(models, calibration, predictions)


def _atomic_joblib(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    try:
        joblib.dump(dict(payload), partial, compress=3)
        os.replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _exclusive_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise ModelFitPredictionError(
            f"Append-only completion already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _exclusive_json_or_authenticate_same(payload: dict[str, Any], destination: Path) -> None:
    """Publish once, while allowing an exact same-claim crash recovery."""

    try:
        _exclusive_json(payload, destination)
    except ModelFitPredictionError:
        if (
            not destination.is_file()
            or _read_committed(destination, label="Existing append-only publication") != payload
        ):
            raise


def _output_paths(root: Path, authorization: Mapping[str, Any]) -> dict[str, Path]:
    contract = authorization.get("output_contract")
    if not isinstance(contract, dict):
        raise ModelFitPredictionError("Authorization lacks its output contract")
    model_root = _inside(root, str(contract.get("model_root", "")), label="Model root")
    expected = {
        "external_predictions_path": model_root / PREDICTION_FILENAME,
        "model_artifact_path": model_root / MODEL_FILENAME,
        "fit_audit_path": model_root / FIT_AUDIT_FILENAME,
    }
    resolved: dict[str, Path] = {"model_root": model_root}
    for key, default in expected.items():
        value = contract.get(key, default.relative_to(root).as_posix())
        path = _inside(root, str(value), label=key)
        if path != default:
            raise ModelFitPredictionError(f"Authorization changed {key}")
        resolved[key] = path
    completion = _inside(
        root,
        str(contract.get("completion_manifest", "")),
        label="Completion manifest",
    )
    resolved["completion_manifest"] = completion
    prediction_commit = _inside(
        root,
        str(contract.get("external_prediction_commit_manifest", "")),
        label="External prediction commit manifest",
    )
    resolved["external_prediction_commit_manifest"] = prediction_commit
    return resolved


def authenticate_external_prediction_publication(
    project_root: str | Path,
    publication_path: str | Path,
    *,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate the predictor-only commit without discovering target files."""

    root = Path(project_root).resolve()
    publication = _read_committed(
        _inside(root, publication_path, label="Prediction publication"),
        label="External prediction publication",
    )
    cohorts = authorization["cohorts"]
    contract = authorization["output_contract"]
    output = publication.get("output")
    if not isinstance(output, dict):
        raise ModelFitPredictionError("Prediction publication lacks its output")
    output_path = _record_matches(root, output)
    expected_output = _inside(
        root,
        str(contract["external_predictions_path"]),
        label="External prediction output",
    )
    expected_city_dates = int(cohorts["external_city_dates"])
    if (
        output_path != expected_output
        or publication.get("state") != PREDICTION_COMMIT_STATE
        or publication.get("authorization_commit_sha256") != authorization["commit_sha256"]
        or publication.get("model_fit_claim_id") != authorization["claim_id"]
        or publication.get("protocol_lock_commit_sha256")
        != authorization["protocol_model_lock_commit_sha256"]
        or publication.get("external_city_ids") != list(cohorts["external_city_ids"])
        or publication.get("external_year") != int(cohorts["external_year"])
        or publication.get("row_count") != int(cohorts["external_rows"])
        or publication.get("city_date_count") != expected_city_dates
        or publication.get("prediction_columns") != list(PREDICTION_COLUMNS)
        or publication.get("external_target_or_qa_values_read") is not False
        or publication.get("access_audit")
        != {
            "external_target_or_qa_files_read": [],
            "external_target_or_qa_values_read": False,
            "external_target_claim_existed_at_publication": False,
        }
        or not _is_sha256(publication.get("model_fit_commit_sha256"))
        or not _is_sha256(publication.get("calibration_commit_sha256"))
    ):
        raise ModelFitPredictionError("External prediction publication contract changed")
    try:
        frame = pd.read_parquet(output_path)
    except Exception as error:  # noqa: BLE001 - normalize reader failures
        raise ModelFitPredictionError("External predictions cannot be read") from error
    if tuple(frame.columns) != PREDICTION_COLUMNS or len(frame) != int(cohorts["external_rows"]):
        raise ModelFitPredictionError("External prediction schema or row count changed")
    dates = pd.to_datetime(frame["target_date"], errors="raise")
    if (
        frame.duplicated(list(KEY_COLUMNS)).any()
        or set(frame["city_id"].astype(str).unique()) != set(cohorts["external_city_ids"])
        or dates.dt.year.ne(int(cohorts["external_year"])).any()
        or frame.groupby("city_id", observed=True)["target_date"].nunique().sum()
        != expected_city_dates
    ):
        raise ModelFitPredictionError("External prediction cohort changed")
    numeric = frame.loc[
        :,
        [
            "b1_prediction_c",
            "m2_prediction_c",
            "m2_lower_c",
            "m2_upper_c",
            "m2_interval_width_c",
        ],
    ].apply(pd.to_numeric, errors="raise")
    if (
        not np.isfinite(numeric.to_numpy(dtype=float)).all()
        or numeric["m2_lower_c"].gt(numeric["m2_upper_c"]).any()
        or not np.allclose(
            numeric["m2_interval_width_c"],
            numeric["m2_upper_c"] - numeric["m2_lower_c"],
            rtol=0,
            atol=1e-10,
        )
        or not frame["m2_accepted"].astype(bool).eq(~frame["m2_abstain"].astype(bool)).all()
        or output.get("semantic_sha256") != _prediction_semantic(frame)
    ):
        raise ModelFitPredictionError("External prediction values violate the lock")
    return publication


def authenticate_model_fit_completion(
    project_root: str | Path,
    completion_path: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Reauthenticate G5 without loading joblib or opening any target table."""

    root = Path(project_root).resolve()
    authorization = authenticate_model_fit_authorization(root, authorization_path)
    completion = _read_committed(
        _inside(root, completion_path, label="Completion path"),
        label="Model-fit completion",
    )
    if (
        completion.get("state") != COMPLETION_STATE
        or completion.get("authorization_commit_sha256") != authorization.get("commit_sha256")
        or completion.get("claim_id") != authorization.get("claim_id")
        or completion.get("protocol_model_lock_commit_sha256")
        != authorization.get("protocol_model_lock_commit_sha256")
        or completion.get("source_completion_commit_sha256")
        != authorization.get("source_completion_commit_sha256")
        or completion.get("predictor_complete_commit_sha256")
        != authorization.get("predictor_complete_commit_sha256")
        or completion.get("cohorts", {}).get("candidate") != authorization.get("cohorts")
        or completion.get("access_audit")
        != {
            "la_source_target_values_read": True,
            "external_target_values_read": False,
            "external_target_or_qa_files_read": [],
            "external_prediction_created_before_external_target_claim": True,
            "model_selection_or_retuning_performed": False,
        }
    ):
        raise ModelFitPredictionError("Model-fit completion identity changed")
    outputs = completion.get("outputs")
    if not isinstance(outputs, dict):
        raise ModelFitPredictionError("Model-fit completion lacks outputs")
    for name in ("model_artifact", "fit_audit", "external_predictions"):
        _record_matches(root, outputs.get(name))
    audit_path = _record_matches(root, outputs["fit_audit"])
    audit = _read_committed(audit_path, label="Fit audit")
    if (
        audit.get("state") != "complete_la_fit_and_calibration_external_predictor_only"
        or audit.get("authorization_commit_sha256") != authorization["commit_sha256"]
        or audit.get("access_audit")
        != {
            "la_source_target_table_read": True,
            "external_target_or_qa_table_read": False,
            "external_target_asset_href_read": False,
            "external_target_values_read": False,
            "model_selection_or_retuning_performed": False,
        }
        or audit.get("external_prediction", {}).get("target_values_read") is not False
    ):
        raise ModelFitPredictionError("Fit audit authorization changed")
    prediction_record = outputs["external_predictions"]
    if prediction_record.get("rows") != int(
        authorization["cohorts"]["external_rows"]
    ) or prediction_record.get("columns") != list(PREDICTION_COLUMNS):
        raise ModelFitPredictionError("External prediction output contract changed")
    paths = _output_paths(root, authorization)
    publication = authenticate_external_prediction_publication(
        root,
        paths["external_prediction_commit_manifest"],
        authorization=authorization,
    )
    publication_record = completion.get("external_prediction_commit")
    publication_path = _record_matches(root, publication_record)
    recorded_authorization_path = _inside(
        root,
        str(publication.get("model_fit_authorization_path", "")),
        label="Recorded model-fit authorization",
    )
    recorded_completion_path = _inside(
        root,
        str(publication.get("model_fit_completion_path", "")),
        label="Recorded model-fit completion",
    )
    if (
        publication_path != paths["external_prediction_commit_manifest"]
        or recorded_authorization_path
        != _inside(root, authorization_path, label="Authorization path")
        or recorded_completion_path != _inside(root, completion_path, label="Completion path")
        or publication_record.get("commit_sha256") != publication["commit_sha256"]
        or completion.get("external_prediction_commit_sha256") != publication["commit_sha256"]
        or publication["output"].get("sha256") != prediction_record.get("sha256")
        or publication.get("model_fit_commit_sha256") != audit.get("commit_sha256")
        or publication.get("calibration_commit_sha256")
        != audit.get("calibration", {}).get("commit_sha256")
    ):
        raise ModelFitPredictionError("External prediction publication binding changed")
    return completion


def run_model_fit_prediction(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    check_only: bool = False,
) -> dict[str, Any]:
    """Run G5 once or authenticate its existing append-only completion."""

    root = Path(project_root).resolve()
    status = _inside(root, status_path, label="Status path")
    authorization = authenticate_model_fit_authorization(root, authorization_path)
    paths = _output_paths(root, authorization)
    completion_path = paths["completion_manifest"]
    if check_only or completion_path.exists():
        return authenticate_model_fit_completion(
            root,
            completion_path,
            authorization_path=authorization_path,
        )

    status_payload: dict[str, Any] = {
        "schema_version": 1,
        "state": "running",
        "stage": "preflight",
        "stage_index": 1,
        "stage_total": len(STAGES),
        "authorization_commit_sha256": authorization["commit_sha256"],
        "external_target_values_read": False,
        "updated_at_utc": _utc_now(),
        "last_error_type": None,
    }

    def progress(stage: str, detail: Mapping[str, Any] | None = None) -> None:
        if stage not in STAGES:
            raise ModelFitPredictionError(f"Unknown model-fit stage: {stage}")
        status_payload.update(
            {
                "state": "complete" if stage == "complete" else "running",
                "stage": stage,
                "stage_index": STAGES.index(stage) + 1,
                "updated_at_utc": _utc_now(),
            }
        )
        if detail is not None:
            status_payload["detail"] = dict(detail)
        atomic_json(status_payload, status)

    progress("preflight")
    try:
        predictor_record = authorization["predictor_table"]
        predictor_path = _record_matches(root, predictor_record)
        source_manifest_path = _inside(
            root,
            authorization["source_targets_complete"]["path"],
            label="LA source completion",
        )
        source_manifest = _read_committed(source_manifest_path, label="LA source completion")
        city_commit_path = _inside(
            root,
            source_manifest["city_target_commit"]["path"],
            label="LA city target commit",
        )
        target_path = city_commit_path.parent / "targets.parquet"
        expected_target = source_manifest["city_target_commit"]["output_files"]["targets.parquet"]
        if (
            not target_path.is_file()
            or target_path.stat().st_size != expected_target.get("bytes")
            or sha256_file(target_path) != expected_target.get("sha256")
        ):
            raise ModelFitPredictionError("LA target table failed its authorization lock")
        predictors = pd.read_parquet(predictor_path)
        source_targets = pd.read_parquet(target_path)
        progress("join")
        contract = load_frozen_transfer_contract(root)
        prepared = prepare_fit_data(
            predictors,
            source_targets,
            contract,
            authorization["cohorts"],
        )
        result = fit_and_predict(prepared, contract, progress=progress)
        if len(result.predictions) != int(authorization["cohorts"]["external_rows"]):
            raise ModelFitPredictionError("External prediction row count changed")
        progress("commit")
        model_root = paths["model_root"]
        model_root.mkdir(parents=True, exist_ok=True)
        training_key_sha = canonical_frame_sha256(
            prepared.training_predictors,
            sort_by=list(KEY_COLUMNS),
            columns=list(KEY_COLUMNS),
        )
        calibration_key_sha = canonical_frame_sha256(
            prepared.calibration_predictors,
            sort_by=list(KEY_COLUMNS),
            columns=list(KEY_COLUMNS),
        )
        model_bundle = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "authorization_commit_sha256": authorization["commit_sha256"],
            "protocol_model_lock_commit_sha256": authorization["protocol_model_lock_commit_sha256"],
            "source_completion_commit_sha256": authorization["source_completion_commit_sha256"],
            "predictor_complete_commit_sha256": authorization["predictor_complete_commit_sha256"],
            "training_keys_sha256": training_key_sha,
            "calibration_keys_sha256": calibration_key_sha,
            "b1_feature_order": list(result.models.b1_feature_order),
            "m2_feature_order": list(result.models.m2_feature_order),
            "training_row_count": result.models.training_row_count,
            "training_date_count": result.models.training_date_count,
            "calibration": asdict(result.calibration),
            "models": {
                "b1": result.models.b1,
                "m2": result.models.m2,
                "q05": result.models.lower,
                "q95": result.models.upper,
            },
        }
        _atomic_joblib(model_bundle, paths["model_artifact_path"])
        atomic_parquet(result.predictions, paths["external_predictions_path"])
        prediction_record = {
            **_file_record(root, paths["external_predictions_path"]),
            **parquet_file_record(paths["external_predictions_path"], result.predictions),
            "columns": list(result.predictions.columns),
            "semantic_sha256": _prediction_semantic(result.predictions),
            "city_date_count": int(
                result.predictions.loc[:, ["city_id", "target_date"]].drop_duplicates().shape[0]
            ),
            "city_row_counts": {
                str(key): int(value)
                for key, value in result.predictions["city_id"]
                .value_counts(sort=False)
                .sort_index()
                .items()
            },
        }
        if prediction_record["city_date_count"] != int(
            authorization["cohorts"]["external_city_dates"]
        ):
            raise ModelFitPredictionError("External prediction city-date count changed")
        calibration_commit_sha256 = canonical_sha256(
            {
                "authorization_commit_sha256": authorization["commit_sha256"],
                "calibration_keys_sha256": calibration_key_sha,
                "calibration": asdict(result.calibration),
            }
        )
        fit_audit: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "complete_la_fit_and_calibration_external_predictor_only",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "selection": prepared.selection_audit,
            "training": {
                "rows": result.models.training_row_count,
                "dates": result.models.training_date_count,
                "keys_sha256": training_key_sha,
            },
            "calibration": {
                **asdict(result.calibration),
                "keys_sha256": calibration_key_sha,
                "commit_sha256": calibration_commit_sha256,
            },
            "external_prediction": {
                "rows": len(result.predictions),
                "city_dates": prediction_record["city_date_count"],
                "target_values_read": False,
            },
            "access_audit": {
                "la_source_target_table_read": True,
                "external_target_or_qa_table_read": False,
                "external_target_asset_href_read": False,
                "external_target_values_read": False,
                "model_selection_or_retuning_performed": False,
            },
        }
        fit_audit["commit_sha256"] = canonical_sha256(fit_audit)
        atomic_json(fit_audit, paths["fit_audit_path"])
        prediction_publication: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": PREDICTION_COMMIT_STATE,
            "authorization_commit_sha256": authorization["commit_sha256"],
            "model_fit_authorization_path": _inside(
                root, authorization_path, label="Authorization path"
            )
            .relative_to(root)
            .as_posix(),
            "model_fit_claim_id": authorization["claim_id"],
            "model_fit_completion_path": completion_path.relative_to(root).as_posix(),
            "protocol_lock_commit_sha256": authorization["protocol_model_lock_commit_sha256"],
            "model_fit_commit_sha256": fit_audit["commit_sha256"],
            "calibration_commit_sha256": calibration_commit_sha256,
            "external_city_ids": list(authorization["cohorts"]["external_city_ids"]),
            "external_year": int(authorization["cohorts"]["external_year"]),
            "row_count": len(result.predictions),
            "city_date_count": prediction_record["city_date_count"],
            "prediction_columns": list(PREDICTION_COLUMNS),
            "external_target_or_qa_values_read": False,
            "output": {
                "path": prediction_record["path"],
                "bytes": prediction_record["bytes"],
                "sha256": prediction_record["sha256"],
                "semantic_sha256": prediction_record["semantic_sha256"],
            },
            "access_audit": {
                "external_target_or_qa_files_read": [],
                "external_target_or_qa_values_read": False,
                "external_target_claim_existed_at_publication": False,
            },
            "next_safe_stage": "authorize_one_indivisible_three_city_external_target_claim",
        }
        prediction_publication["commit_sha256"] = canonical_sha256(prediction_publication)
        _exclusive_json_or_authenticate_same(
            prediction_publication,
            paths["external_prediction_commit_manifest"],
        )
        authenticated_publication = authenticate_external_prediction_publication(
            root,
            paths["external_prediction_commit_manifest"],
            authorization=authorization,
        )
        completion: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": COMPLETION_STATE,
            "claim_id": authorization["claim_id"],
            "authorization_commit_sha256": authorization["commit_sha256"],
            "protocol_model_lock_commit_sha256": authorization["protocol_model_lock_commit_sha256"],
            "source_completion_commit_sha256": authorization["source_completion_commit_sha256"],
            "predictor_complete_commit_sha256": authorization["predictor_complete_commit_sha256"],
            "cohorts": {
                "candidate": dict(authorization["cohorts"]),
                "usable_la_training_rows": result.models.training_row_count,
                "usable_la_training_dates": result.models.training_date_count,
                "usable_la_calibration_rows": result.calibration.calibration_row_count,
                "usable_la_calibration_dates": result.calibration.calibration_date_count,
                "external_prediction_rows": len(result.predictions),
            },
            "outputs": {
                "model_artifact": _file_record(root, paths["model_artifact_path"]),
                "fit_audit": {
                    **_file_record(root, paths["fit_audit_path"]),
                    "commit_sha256": fit_audit["commit_sha256"],
                },
                "external_predictions": prediction_record,
            },
            "external_prediction_commit": {
                **_file_record(root, paths["external_prediction_commit_manifest"]),
                "commit_sha256": authenticated_publication["commit_sha256"],
            },
            "external_prediction_commit_sha256": authenticated_publication["commit_sha256"],
            "access_audit": {
                "la_source_target_values_read": True,
                "external_target_values_read": False,
                "external_target_or_qa_files_read": [],
                "external_prediction_created_before_external_target_claim": True,
                "model_selection_or_retuning_performed": False,
            },
            "next_safe_stage": ("authorize_one_indivisible_three_city_external_target_claim"),
        }
        completion["commit_sha256"] = canonical_sha256(completion)
        _exclusive_json(completion, completion_path)
        authenticated = authenticate_model_fit_completion(
            root,
            completion_path,
            authorization_path=authorization_path,
        )
        progress(
            "complete",
            {
                "external_prediction_rows": len(result.predictions),
                "external_prediction_commit_sha256": authenticated_publication["commit_sha256"],
                "completion_commit_sha256": authenticated["commit_sha256"],
            },
        )
        return authenticated
    except Exception as error:
        status_payload.update(
            {
                "state": "failed",
                "last_error_type": type(error).__name__,
                "updated_at_utc": _utc_now(),
            }
        )
        atomic_json(status_payload, status)
        raise
