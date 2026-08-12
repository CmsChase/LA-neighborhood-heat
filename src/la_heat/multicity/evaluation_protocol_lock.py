"""Append-only pre-fit protocol/model lock for the four-city experiment."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import canonical_sha256, sha256_file

CONFIG_PATH: Final = Path("configs/multicity/evaluation_protocol_lock_v1.toml")
LOCK_PATH: Final = Path("manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json")


class EvaluationProtocolLockError(RuntimeError):
    """Raised when a protocol input drifts or an append-only lock is invalid."""


_JSON_INPUTS: Final = {
    "predictor_contract": "complete_portable_predictor_contract_locked",
    "return_receipt": "complete_verified_portable_sentinel_return",
    "predictor_complete": "complete_target_blind_46_feature_predictors",
    "readiness": "ready_for_protocol_lock_not_model_fit",
    "spatial_blocks": "complete_target_blind_spatial_blocks",
    "target_contexts": "complete_target_blind_target_contexts",
    "target_plan": "prepared_target_blind_builder_not_authorized",
}


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationProtocolLockError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise EvaluationProtocolLockError(f"{label} must be a JSON object.")
    return value


def _authenticate_commit(payload: dict[str, Any], label: str) -> str:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise EvaluationProtocolLockError(f"{label} commit is invalid.")
    return recorded


def _fingerprint(root: Path, relative_path: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise EvaluationProtocolLockError(f"Locked input is unavailable: {relative_path}")
    return {
        "path": Path(relative_path).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip().lower()
    return value if len(value) == 40 else None


def _load_config(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = root / CONFIG_PATH
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise EvaluationProtocolLockError("Protocol lock configuration is invalid.") from error
    if not isinstance(config, dict):
        raise EvaluationProtocolLockError("Protocol lock configuration must be a table.")
    return config, _fingerprint(root, CONFIG_PATH.as_posix())


def _validate_semantics(config: dict[str, Any]) -> None:
    identity = config.get("identity", {})
    cohorts = config.get("cohorts", {})
    evaluation = config.get("evaluation", {})
    permissions = config.get("permissions", {})
    outputs = config.get("outputs", {})
    if identity.get("experiment_id") != "la_to_three_city_zero_shot_v1":
        raise EvaluationProtocolLockError("Experiment identity changed.")
    if cohorts.get("training_years") != [2020, 2021, 2022, 2023]:
        raise EvaluationProtocolLockError("Training years changed.")
    if cohorts.get("calibration_year") != 2024 or cohorts.get("external_year") != 2025:
        raise EvaluationProtocolLockError("Calibration or external year changed.")
    if cohorts.get("external_city_ids") != ["phoenix_az", "houston_tx", "chicago_il"]:
        raise EvaluationProtocolLockError("External city cohort changed.")
    if (
        evaluation.get("bootstrap_iterations") != 10_000
        or evaluation.get("bootstrap_seed") != 20_260_728
    ):
        raise EvaluationProtocolLockError("Bootstrap contract changed.")
    if evaluation.get("minimum_relative_mae_improvement") != 0.10:
        raise EvaluationProtocolLockError("Primary success threshold changed.")
    if any(value is not False for value in permissions.values()):
        raise EvaluationProtocolLockError("A prohibited execution permission is enabled.")
    expected_columns = [
        "city_id", "tract_geoid", "target_date", "b1_prediction_c",
        "m2_prediction_c", "m2_lower_c", "m2_upper_c",
        "m2_interval_width_c", "m2_abstain", "m2_accepted",
    ]
    if outputs.get("prediction_columns") != expected_columns:
        raise EvaluationProtocolLockError("Prediction output schema changed.")


def _validate_inputs(
    root: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    inputs_config = config.get("inputs")
    if not isinstance(inputs_config, dict):
        raise EvaluationProtocolLockError("Input lock configuration is missing.")
    payloads: dict[str, Any] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    for name, expected_state in _JSON_INPUTS.items():
        relative = str(inputs_config.get(f"{name}_path", ""))
        expected_commit = str(inputs_config.get(f"{name}_commit_sha256", ""))
        payload = _read_json(root / relative, name)
        commit = _authenticate_commit(payload, name)
        if payload.get("state") != expected_state or commit != expected_commit:
            raise EvaluationProtocolLockError(f"{name} identity or state changed.")
        payloads[name] = payload
        fingerprints[name] = {
            **_fingerprint(root, relative),
            "commit_sha256": commit,
        }

    table_relative = str(inputs_config.get("predictor_table_path", ""))
    table_fingerprint = _fingerprint(root, table_relative)
    if table_fingerprint["sha256"] != inputs_config.get("predictor_table_sha256"):
        raise EvaluationProtocolLockError("Predictor table identity changed.")
    fingerprints["predictor_table"] = table_fingerprint
    historical = str(inputs_config.get("historical_experiment_draft_path", ""))
    fingerprints["historical_experiment_draft_non_authoritative"] = _fingerprint(root, historical)

    readiness = payloads["readiness"]
    complete = payloads["predictor_complete"]
    receipt = payloads["return_receipt"]
    plan = payloads["target_plan"]
    if (
        readiness.get("row_count") != 136_941
        or readiness.get("feature_count") != 46
        or readiness.get("predictor_sha256") != table_fingerprint["sha256"]
        or readiness.get("split_row_counts") != {
            "los_angeles_2020_2023_training": 73_432,
            "los_angeles_2024_calibration": 25_208,
            "external_cities_2025_prediction": 38_301,
        }
        or readiness.get("external_target_values_read") is not False
        or readiness.get("model_fit_performed") is not False
    ):
        raise EvaluationProtocolLockError(
            "Predictor readiness no longer matches the frozen cohort."
        )
    output = complete.get("output", {})
    if (
        complete.get("row_count") != 136_941
        or complete.get("feature_count") != 46
        or output.get("path") != table_relative
        or output.get("sha256") != table_fingerprint["sha256"]
        or output.get("bytes") != table_fingerprint["bytes"]
    ):
        raise EvaluationProtocolLockError("Predictor completion record is inconsistent.")
    if receipt.get("completed_work_units") != 516:
        raise EvaluationProtocolLockError("Portable Sentinel return is not 516/516.")
    if receipt.get("access_contract") != {
        "external_target_or_qa_values_read": False,
        "model_fit_or_prediction_performed": False,
    }:
        raise EvaluationProtocolLockError("Portable return access boundary changed.")
    authorization = plan.get("authorization", {})
    access = plan.get("access_contract", {})
    if any(value is not False for value in authorization.values()) or any(
        value is not False for value in access.values()
    ):
        raise EvaluationProtocolLockError("Target plan is no longer sealed.")
    return payloads, fingerprints


def _validate_model_contract(contract: dict[str, Any]) -> dict[str, Any]:
    registry = contract.get("feature_registry", {})
    model = contract.get("model_contract", {})
    b1 = model.get("b1_transfer", {})
    m2 = model.get("m2_transfer", {})
    uncertainty = model.get("uncertainty", {})
    if (
        registry.get("feature_count") != 46
        or len(registry.get("feature_order", [])) != 46
        or len(model.get("b1_feature_order", [])) != 23
        or model.get("m2_feature_order") != registry.get("feature_order")
        or b1.get("alpha") != 10.0
        or b1.get("solver") != "lsqr"
        or m2.get("random_state") != 20_260_719
        or m2.get("max_iter") != 300
        or uncertainty.get("nominal_coverage") != 0.9
        or uncertainty.get("abstention_width_quantile") != 0.8
    ):
        raise EvaluationProtocolLockError("Frozen predictor/model contract changed.")
    return {
        "feature_registry_semantic_sha256": registry.get("semantic_sha256"),
        "b1_feature_order": model["b1_feature_order"],
        "m2_feature_order": model["m2_feature_order"],
        "b1_transfer": b1,
        "m2_transfer": m2,
        "uncertainty": uncertainty,
        "training_row_weights": model.get("training_row_weights"),
        "dynamic_imputation": model.get("dynamic_imputation"),
    }


def _historical_anchor(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    anchor = config.get("historical_anchor", {})
    result: dict[str, Any] = {"phase1_is_context_only": True}
    for name in ("phase1_model_lock", "phase1_evaluation"):
        relative = str(anchor.get(f"{name}_path", ""))
        payload = _read_json(root / relative, name)
        commit = _authenticate_commit(payload, name)
        if commit != anchor.get(f"{name}_commit_sha256"):
            raise EvaluationProtocolLockError(f"{name} historical identity changed.")
        result[name] = {**_fingerprint(root, relative), "commit_sha256": commit}
    claim_relative = str(anchor.get("phase1_claim_path", ""))
    claim = _read_json(root / claim_relative, "phase1_claim")
    _authenticate_commit(claim, "phase1_claim")
    if claim.get("claim_id") != anchor.get("phase1_claim_id"):
        raise EvaluationProtocolLockError("Phase-I historical claim changed.")
    result["phase1_claim"] = {
        **_fingerprint(root, claim_relative),
        "claim_id": claim["claim_id"],
        "commit_sha256": claim["commit_sha256"],
    }
    return result


def build_protocol_model_lock(project_root: str | Path) -> dict[str, Any]:
    """Build the deterministic lock payload without reading target values or fitting."""

    root = Path(project_root).resolve()
    config, config_fingerprint = _load_config(root)
    _validate_semantics(config)
    payloads, input_fingerprints = _validate_inputs(root, config)
    model_contract = _validate_model_contract(payloads["predictor_contract"])
    code_paths = config.get("code", {}).get("paths", [])
    if not isinstance(code_paths, list) or not code_paths:
        raise EvaluationProtocolLockError("Code fingerprint list is missing.")
    code_fingerprints = {
        str(path): _fingerprint(root, str(path)) for path in code_paths
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "multicity-protocol-model-lock-v1",
        "state": "locked_before_source_targets_and_real_fit",
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "protocol_locked": True,
        "model_spec_locked": True,
        "fitted_model_artifacts_locked": False,
        "research_question": config["identity"]["research_question"],
        "cohorts": config["cohorts"],
        "target_contract": config["target"],
        "model_contract": model_contract,
        "evaluation_contract": config["evaluation"],
        "uncertainty_contract": config["uncertainty"],
        "prediction_output_contract": config["outputs"],
        "permissions": config["permissions"],
        "input_fingerprints": input_fingerprints,
        "historical_anchor": _historical_anchor(root, config),
        "code_identity": {
            "git_head_informational": _git_head(root),
            "files": code_fingerprints,
            "protocol_config": config_fingerprint,
            "future_external_evaluator_implementation_must_be_bound_before_external_claim": True,
        },
        "access_audit": {
            "predictor_values_read_by_this_lock": False,
            "landsat_asset_hrefs_read_by_this_lock": False,
            "landsat_thermal_or_target_qa_values_read_by_this_lock": False,
            "target_tables_read_by_this_lock": False,
            "model_fit_or_prediction_performed_by_this_lock": False,
            "values_opened_marker_created_by_this_lock": False,
        },
        "next_safe_stage": "explicitly_authorize_la_2020_2024_source_target_lane",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _write_exclusive(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise EvaluationProtocolLockError(
            f"Append-only lock already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def authenticate_protocol_model_lock(
    project_root: str | Path,
    lock_path: str | Path = LOCK_PATH,
) -> dict[str, Any]:
    """Re-authenticate the append-only lock and every bound file identity."""

    root = Path(project_root).resolve()
    path = Path(lock_path)
    path = path if path.is_absolute() else root / path
    payload = _read_json(path, "protocol/model lock")
    _authenticate_commit(payload, "protocol/model lock")
    if (
        payload.get("state") != "locked_before_source_targets_and_real_fit"
        or payload.get("protocol_locked") is not True
        or payload.get("model_spec_locked") is not True
        or payload.get("fitted_model_artifacts_locked") is not False
        or any(value is not False for value in payload.get("permissions", {}).values())
    ):
        raise EvaluationProtocolLockError("Protocol/model lock permissions are invalid.")
    for group in ("input_fingerprints",):
        for record in payload.get(group, {}).values():
            current = _fingerprint(root, str(record["path"]))
            if current["bytes"] != record["bytes"] or current["sha256"] != record["sha256"]:
                raise EvaluationProtocolLockError(f"Locked input drifted: {record['path']}")
    for record in payload.get("historical_anchor", {}).values():
        if not isinstance(record, dict) or "path" not in record:
            continue
        current = _fingerprint(root, str(record["path"]))
        if current["bytes"] != record["bytes"] or current["sha256"] != record["sha256"]:
            raise EvaluationProtocolLockError(f"Historical anchor drifted: {record['path']}")
    for record in payload.get("code_identity", {}).get("files", {}).values():
        current = _fingerprint(root, str(record["path"]))
        if current["bytes"] != record["bytes"] or current["sha256"] != record["sha256"]:
            raise EvaluationProtocolLockError(f"Locked code drifted: {record['path']}")
    return payload


def create_protocol_model_lock(
    project_root: str | Path,
    lock_path: str | Path = LOCK_PATH,
) -> dict[str, Any]:
    """Create the formal lock once and immediately re-authenticate it."""

    root = Path(project_root).resolve()
    path = Path(lock_path)
    path = path if path.is_absolute() else root / path
    payload = build_protocol_model_lock(root)
    _write_exclusive(payload, path)
    return authenticate_protocol_model_lock(root, path)
