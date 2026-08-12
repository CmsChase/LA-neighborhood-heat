"""Append-only authorization for the indivisible three-city external cohort."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import load_config
from la_heat.multicity.evaluation_protocol_lock import (
    LOCK_PATH as PROTOCOL_LOCK_PATH,
)
from la_heat.multicity.evaluation_protocol_lock import authenticate_protocol_model_lock
from la_heat.multicity.model_fit_authorization import (
    authenticate_model_fit_authorization,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.target_authorization import (
    AUTHORIZED_STATE,
    TargetAuthorizationError,
    authenticate_target_execution_authorization,
)
from la_heat.multicity.target_processor import multicity_target_config_sha256
from la_heat.multicity.target_transaction import (
    EXTERNAL_LANE,
    PREPARED_STATE,
)
from la_heat.multicity.target_transaction import (
    MANIFEST_PATH as TARGET_PLAN_PATH,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

AUTHORIZATION_PATH: Final = Path("manifests/multicity/targets/EXTERNAL_TARGET_AUTHORIZATION.json")
PREDICTION_COMMIT_PATH: Final = Path(
    "manifests/multicity/evaluation/EXTERNAL_PREDICTIONS_COMMITTED.json"
)
SOURCE_COMPLETION_PATH: Final = Path("manifests/multicity/targets/LA_SOURCE_TARGETS_COMPLETE.json")
TARGET_CONFIG_PATH: Final = Path("configs/research.toml")
VALUES_OPENED_PATH: Final = Path(
    "data/interim/multicity/targets/values_opened/"
    "three_city_2025_combined_external/VALUES_OPENED.json"
)
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
EVALUATOR_FILES: Final = (
    "src/la_heat/multicity/external_target_authorization.py",
    "src/la_heat/multicity/external_target_worker.py",
    "src/la_heat/multicity/external_evaluation.py",
)
EXPECTED_EXTERNAL_ROWS: Final = 38_301
EXPECTED_EXTERNAL_CITY_DATES: Final = 64


class ExternalTargetAuthorizationError(RuntimeError):
    """Raised before any external-target authorization can be issued."""


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ExternalTargetAuthorizationError(f"{label} must stay inside the project")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExternalTargetAuthorizationError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise ExternalTargetAuthorizationError(f"{label} is not a JSON object")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise ExternalTargetAuthorizationError(f"{label} commit is invalid")
    return payload


def _record(root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "commit_sha256": payload["commit_sha256"],
    }


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ExternalTargetAuthorizationError(f"Bound implementation is missing: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _matches_file_record(path: Path, record: object) -> bool:
    return (
        isinstance(record, dict)
        and path.is_file()
        and path.stat().st_size == record.get("bytes")
        and sha256_file(path) == record.get("sha256")
    )


def _prediction_semantic(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for column in ("city_id", "tract_geoid", "target_date"):
        normalized[column] = normalized[column].astype("string")
    return canonical_frame_sha256(
        normalized,
        sort_by=["city_id", "target_date", "tract_geoid"],
        columns=list(PREDICTION_COLUMNS),
    )


def _authenticate_model_fit_chain(
    root: Path,
    commit_path: Path,
    commit: dict[str, Any],
    protocol: dict[str, Any],
) -> None:
    """Trace the publication back to its authenticated fit and calibration."""

    authorization_path = _inside(
        root,
        str(commit.get("model_fit_authorization_path", "")),
        label="Model-fit authorization",
    )
    authorization = authenticate_model_fit_authorization(root, authorization_path)
    completion_path = _inside(
        root,
        str(commit.get("model_fit_completion_path", "")),
        label="Model-fit completion",
    )
    expected_completion_path = _inside(
        root,
        str(authorization.get("output_contract", {}).get("completion_manifest", "")),
        label="Authorized model-fit completion",
    )
    completion = _read_committed(completion_path, label="Model-fit completion")
    publication_record = completion.get("external_prediction_commit", {})
    outputs = completion.get("outputs", {})
    fit_record = outputs.get("fit_audit", {}) if isinstance(outputs, dict) else {}
    prediction_record = outputs.get("external_predictions", {}) if isinstance(outputs, dict) else {}
    model_record = outputs.get("model_artifact", {}) if isinstance(outputs, dict) else {}
    fit_path = _inside(root, str(fit_record.get("path", "")), label="Fit audit")
    prediction_path = _inside(
        root, str(prediction_record.get("path", "")), label="Completed predictions"
    )
    model_path = _inside(root, str(model_record.get("path", "")), label="Model artifact")
    if (
        completion_path != expected_completion_path
        or completion.get("state") != "model_fit_complete_external_predictions_committed"
        or completion.get("authorization_commit_sha256") != authorization.get("commit_sha256")
        or completion.get("claim_id") != authorization.get("claim_id")
        or commit.get("authorization_commit_sha256") != authorization.get("commit_sha256")
        or commit.get("model_fit_claim_id") != authorization.get("claim_id")
        or commit.get("protocol_lock_commit_sha256") != protocol.get("commit_sha256")
        or completion.get("protocol_model_lock_commit_sha256") != protocol.get("commit_sha256")
        or completion.get("external_prediction_commit_sha256") != commit.get("commit_sha256")
        or publication_record.get("path") != _relative(root, commit_path)
        or publication_record.get("commit_sha256") != commit.get("commit_sha256")
        or not _matches_file_record(commit_path, publication_record)
        or not _matches_file_record(fit_path, fit_record)
        or not _matches_file_record(prediction_path, prediction_record)
        or not _matches_file_record(model_path, model_record)
        or prediction_record.get("path") != commit.get("output", {}).get("path")
        or prediction_record.get("sha256") != commit.get("output", {}).get("sha256")
        or completion.get("access_audit")
        != {
            "la_source_target_values_read": True,
            "external_target_values_read": False,
            "external_target_or_qa_files_read": [],
            "external_prediction_created_before_external_target_claim": True,
            "model_selection_or_retuning_performed": False,
        }
    ):
        raise ExternalTargetAuthorizationError("Model-fit completion chain changed")
    fit_audit = _read_committed(fit_path, label="Fit audit")
    calibration = fit_audit.get("calibration", {})
    if (
        fit_audit.get("commit_sha256") != commit.get("model_fit_commit_sha256")
        or fit_record.get("commit_sha256") != fit_audit.get("commit_sha256")
        or fit_audit.get("authorization_commit_sha256") != authorization.get("commit_sha256")
        or calibration.get("commit_sha256") != commit.get("calibration_commit_sha256")
        or fit_audit.get("access_audit")
        != {
            "la_source_target_table_read": True,
            "external_target_or_qa_table_read": False,
            "external_target_asset_href_read": False,
            "external_target_values_read": False,
            "model_selection_or_retuning_performed": False,
        }
    ):
        raise ExternalTargetAuthorizationError("Model-fit or calibration chain changed")


def authenticate_external_prediction_commit(
    project_root: str | Path,
    prediction_commit_path: str | Path = PREDICTION_COMMIT_PATH,
    *,
    protocol: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Authenticate target-blind predictions and their exact frozen schema."""

    root = Path(project_root).resolve()
    commit_path = _inside(root, prediction_commit_path, label="Prediction commit path")
    commit = _read_committed(commit_path, label="External prediction commit")
    lock = protocol or authenticate_protocol_model_lock(root, root / PROTOCOL_LOCK_PATH)
    output = commit.get("output")
    if not isinstance(output, dict):
        raise ExternalTargetAuthorizationError("Prediction commit lacks one output")
    output_path = _inside(root, output.get("path", ""), label="Prediction output")
    if (
        commit.get("state") != "external_predictions_committed_before_target_access"
        or commit.get("protocol_lock_commit_sha256") != lock.get("commit_sha256")
        or commit.get("external_city_ids") != list(EXTERNAL_CITY_IDS)
        or commit.get("external_year") != 2025
        or commit.get("row_count") != EXPECTED_EXTERNAL_ROWS
        or commit.get("city_date_count") != EXPECTED_EXTERNAL_CITY_DATES
        or commit.get("prediction_columns") != list(PREDICTION_COLUMNS)
        or commit.get("external_target_or_qa_values_read") is not False
        or not _is_sha256(commit.get("model_fit_commit_sha256"))
        or not _is_sha256(commit.get("calibration_commit_sha256"))
        or not output_path.is_file()
        or output.get("bytes") != output_path.stat().st_size
        or output.get("sha256") != sha256_file(output_path)
        or commit.get("access_audit")
        != {
            "external_target_or_qa_files_read": [],
            "external_target_or_qa_values_read": False,
            "external_target_claim_existed_at_publication": False,
        }
    ):
        raise ExternalTargetAuthorizationError("External prediction commit contract failed")
    _authenticate_model_fit_chain(root, commit_path, commit, lock)
    try:
        frame = pd.read_parquet(output_path)
    except Exception as error:  # noqa: BLE001 - normalize file-reader failures
        raise ExternalTargetAuthorizationError("External predictions cannot be read") from error
    if tuple(frame.columns) != PREDICTION_COLUMNS or len(frame) != EXPECTED_EXTERNAL_ROWS:
        raise ExternalTargetAuthorizationError("External prediction schema or row count changed")
    keys = ["city_id", "tract_geoid", "target_date"]
    if (
        frame.duplicated(keys).any()
        or tuple(sorted(frame["city_id"].astype(str).unique())) != tuple(sorted(EXTERNAL_CITY_IDS))
        or pd.to_datetime(frame["target_date"], errors="raise").dt.year.ne(2025).any()
        or frame.groupby("city_id", observed=True)["target_date"].nunique().sum()
        != EXPECTED_EXTERNAL_CITY_DATES
    ):
        raise ExternalTargetAuthorizationError("External prediction cohort changed")
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
        raise ExternalTargetAuthorizationError("External prediction values violate the lock")
    return commit, frame


def build_external_target_authorization(
    project_root: str | Path,
    *,
    prediction_commit_path: str | Path = PREDICTION_COMMIT_PATH,
    source_completion_path: str | Path = SOURCE_COMPLETION_PATH,
    protocol_lock_path: str | Path = PROTOCOL_LOCK_PATH,
    target_plan_path: str | Path = TARGET_PLAN_PATH,
    target_config_path: str | Path = TARGET_CONFIG_PATH,
    values_opened_path: str | Path = VALUES_OPENED_PATH,
    require_unopened: bool = True,
) -> dict[str, Any]:
    """Build one deterministic three-city permit without opening target values."""

    root = Path(project_root).resolve()
    lock_path = _inside(root, protocol_lock_path, label="Protocol lock")
    plan_path = _inside(root, target_plan_path, label="Target plan")
    config_path = _inside(root, target_config_path, label="Target config")
    source_path = _inside(root, source_completion_path, label="Source completion")
    marker_path = _inside(root, values_opened_path, label="VALUES_OPENED marker")
    protocol = authenticate_protocol_model_lock(root, lock_path)
    prediction, _frame = authenticate_external_prediction_commit(
        root, prediction_commit_path, protocol=protocol
    )
    prediction_path = _inside(root, prediction_commit_path, label="Prediction commit")
    plan = _read_committed(plan_path, label="Target plan")
    lane = plan.get("cohort_lanes", {}).get(EXTERNAL_LANE, {})
    if (
        plan.get("state") != PREPARED_STATE
        or any(value is not False for value in plan.get("authorization", {}).values())
        or any(value is not False for value in plan.get("access_contract", {}).values())
        or lane.get("city_ids") != list(EXTERNAL_CITY_IDS)
        or lane.get("years") != [2025]
        or lane.get("single_append_only_claim_required") is not True
        or lane.get("per_city_claims_forbidden") is not True
        or lane.get("overpasses") != 64
        or lane.get("keys") != EXPECTED_EXTERNAL_ROWS
    ):
        raise ExternalTargetAuthorizationError("Frozen external cohort changed")
    source = _read_committed(source_path, label="LA source completion")
    external_audit = source.get("external_cohort", {})
    if (
        source.get("state") != "la_source_targets_complete"
        or source.get("plan_commit_sha256") != plan.get("commit_sha256")
        or external_audit.get("task_count") != 68
        or external_audit.get("tasks_claimed") is not False
        or external_audit.get("target_values_read") is not False
    ):
        raise ExternalTargetAuthorizationError(
            "LA completion did not leave external targets sealed"
        )
    if require_unopened and marker_path.exists():
        raise ExternalTargetAuthorizationError("External VALUES_OPENED already exists")
    config_sha = multicity_target_config_sha256(load_config(config_path))
    protocol_config = (
        protocol.get("code_identity", {}).get("files", {}).get(TARGET_CONFIG_PATH.as_posix(), {})
    )
    if protocol_config.get("sha256") != sha256_file(config_path):
        raise ExternalTargetAuthorizationError("Target config drifted from protocol lock")
    contract = protocol.get("evaluation_contract", {})
    output_contract = protocol.get("prediction_output_contract", {})
    if (
        contract.get("bootstrap_iterations") != 10_000
        or contract.get("bootstrap_seed") != 20260728
        or output_contract.get("prediction_columns") != list(PREDICTION_COLUMNS)
    ):
        raise ExternalTargetAuthorizationError("Frozen evaluator contract changed")
    code = {relative: _file_record(root, root / relative) for relative in EVALUATOR_FILES}
    request: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "multicity-external-target-authorization-v1",
        "state": AUTHORIZED_STATE,
        "lane": EXTERNAL_LANE,
        "city_ids": list(EXTERNAL_CITY_IDS),
        "years": [2025],
        "purpose": "one_time_three_city_zero_shot_confirmation",
        "single_global_claim": True,
        "per_city_or_partial_claims_forbidden": True,
        "expected_overpass_count": 64,
        "expected_city_compile_count": 3,
        "expected_target_key_count": EXPECTED_EXTERNAL_ROWS,
        "protocol_lock": _record(root, lock_path, protocol),
        "source_completion": _record(root, source_path, source),
        "external_prediction_commit": _record(root, prediction_path, prediction),
        "external_prediction_commit_sha256": prediction["commit_sha256"],
        "plan_commit_sha256": plan["commit_sha256"],
        "target_plan": _record(root, plan_path, plan),
        "target_config_path": _relative(root, config_path),
        "target_config_file_sha256": sha256_file(config_path),
        "target_config_sha256": config_sha,
        "asset_href_hydration_authorized": True,
        "target_values_open_authorized": True,
        "values_opened_marker": _relative(root, marker_path),
        "evaluator_binding": {
            "files": code,
            "primary_metric": contract["primary_metric"],
            "bootstrap_iterations": 10_000,
            "bootstrap_method": contract["bootstrap_method"],
            "bootstrap_seed": 20260728,
            "secondary_metrics": contract["secondary_metrics"],
            "prediction_columns": list(PREDICTION_COLUMNS),
        },
        "permissions": {
            "external_target_build_authorized": True,
            "external_targets_unlocked": True,
            "external_target_scoring_before_all_three_city_compiles": False,
            "external_model_refit_or_recalibration": False,
            "external_claim_reissue": False,
        },
        "access_audit": {
            "external_prediction_values_read": True,
            "landsat_asset_hrefs_read_by_authorization": False,
            "landsat_thermal_or_target_qa_values_read_by_authorization": False,
            "external_target_tables_read_by_authorization": False,
            "values_opened_marker_created_by_authorization": False,
        },
        "next_safe_stage": "run_resumable_combined_three_city_external_target_build",
    }
    request["claim_id"] = canonical_sha256(request)
    request["commit_sha256"] = canonical_sha256(request)
    return request


def _write_exclusive(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise ExternalTargetAuthorizationError(
            f"Append-only external authorization already exists: {path}"
        ) from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def authenticate_external_target_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Reproduce the permit; marker existence is legal only for same-claim resume."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="External authorization")
    observed = _read_committed(path, label="External authorization")
    expected = build_external_target_authorization(
        root,
        prediction_commit_path=observed.get("external_prediction_commit", {}).get("path", ""),
        source_completion_path=observed.get("source_completion", {}).get("path", ""),
        protocol_lock_path=observed.get("protocol_lock", {}).get("path", ""),
        target_plan_path=observed.get("target_plan", {}).get("path", ""),
        target_config_path=observed.get("target_config_path", ""),
        values_opened_path=observed.get("values_opened_marker", ""),
        require_unopened=False,
    )
    if observed != expected:
        raise ExternalTargetAuthorizationError("External authorization no longer reproduces")
    try:
        authenticate_target_execution_authorization(
            root,
            path,
            expected_lane=EXTERNAL_LANE,
            expected_plan_commit_sha256=str(observed["plan_commit_sha256"]),
        )
    except TargetAuthorizationError as error:
        raise ExternalTargetAuthorizationError(
            "External authorization is incompatible with the target engine"
        ) from error
    return observed


def create_external_target_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    **paths: Any,
) -> dict[str, Any]:
    """Issue the sole external claim.  This function is not called automatically."""

    root = Path(project_root).resolve()
    output = _inside(root, authorization_path, label="External authorization")
    payload = build_external_target_authorization(root, **paths)
    _write_exclusive(payload, output)
    return authenticate_external_target_authorization(root, output)
