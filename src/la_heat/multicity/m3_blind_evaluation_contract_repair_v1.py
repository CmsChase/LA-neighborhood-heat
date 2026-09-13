"""Append-only contract-validator repair for the frozen M3 blind evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import external_evaluation as engine
from la_heat.multicity import m3_blind_evaluation_v1 as parent
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-evaluation-contract-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_EVALUATION_CONTRACT_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_EVALUATION_CONTRACT_REPAIR_COMPLETE.json"
)
PARENT_AUTHORIZATION_COMMIT: Final = (
    "3ce0eca0de3367c0a64dac9df3f2bb68aa78f8ed2e12e5dea2c352b9b5ca8616"
)
INCIDENT: Final = {
    "exception_type": "ExternalEvaluationError",
    "exception_message": "Frozen external evaluator contract changed",
    "metrics_computed": False,
    "evaluation_outputs_written": 0,
    "cause": "legacy_three_city_validator_hard_codes_minimum_total_city_dates_30",
}
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_evaluation_contract_repair_v1.py",
    "scripts/authorize_m3_blind_evaluation_contract_repair_v1.py",
    "scripts/run_m3_blind_evaluation_contract_repair_v1.py",
)


class M3BlindEvaluationContractRepairError(RuntimeError):
    """Raised when the narrow evaluation contract repair changes."""


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindEvaluationContractRepairError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_permit = parent.authenticate_authorization(root)
    evaluation_outputs = (
        "scored_rows.parquet",
        "date_metrics.parquet",
        "city_metrics.parquet",
        "risk_coverage.parquet",
        "summary.json",
        "crossed_bootstrap.json",
    )
    if (
        parent_permit["commit_sha256"] != PARENT_AUTHORIZATION_COMMIT
        or helpers._inside(root, parent.COMPLETION_PATH).exists()
        or any(
            helpers._inside(root, parent.OUTPUT_ROOT / name).exists() for name in evaluation_outputs
        )
    ):
        raise M3BlindEvaluationContractRepairError("Repair incident snapshot changed.")
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_contract_repair_authorized",
            "parent_authorization_commit_sha256": PARENT_AUTHORIZATION_COMMIT,
            "incident": INCIDENT,
            "code": code,
            "exact_contract": {
                "minimum_total_usable_city_dates": 40,
                "minimum_usable_dates_per_blind_test_city": 8,
                "bootstrap_iterations": 10_000,
                "bootstrap_seed": 20_260_816,
                "minimum_relative_mae_improvement": 0.10,
                "require_ci_lower_above_zero": True,
                "require_no_city_point_degradation": True,
            },
            "permissions": {
                "replace_legacy_contract_shape_validation_only": True,
                "run_parent_frozen_evaluation": True,
                "change_metric_gate_seed_prediction_target_or_scoring_logic": False,
                "fit_retrain_recalibrate_retune_or_select": False,
            },
            "next_safe_stage": "run_parent_evaluation_with_exact_m3_contract_validator",
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_authorization(root)
    helpers._write_json_exclusive(payload, helpers._inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(helpers._inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindEvaluationContractRepairError("Repair authorization drifted.")
    return observed


def _validate_m3_engine_contract(protocol: dict[str, Any]) -> None:
    contract = protocol.get("evaluation_contract", {})
    output = protocol.get("prediction_output_contract", {})
    expected = {
        "primary_metric": "one_minus_external_equal_city_equal_date_mae_m2_divided_by_b1",
        "bootstrap_iterations": 10_000,
        "bootstrap_seed": 20_260_816,
        "bootstrap_method": "city_stratified_crossed_complete_date_x_5km_spatial_block",
        "confidence_level": 0.95,
        "minimum_relative_mae_improvement": 0.10,
        "require_ci_lower_above_zero": True,
        "require_no_external_city_point_degradation": True,
        "minimum_total_city_dates": 40,
        "minimum_dates_per_external_city": 8,
        "overall_coverage_lower": 0.85,
        "overall_coverage_upper": 0.95,
        "per_city_coverage_lower": 0.80,
        "minimum_retention": 0.60,
        "accepted_mae_improvement": 0.10,
        "hotspot_fraction": 0.20,
        "hotspot_tie_break": "score_desc_tract_geoid_asc",
        "secondary_metrics": list(engine.SECONDARY_METRICS),
    }
    if contract != expected or output != {
        "prediction_columns": list(parent.ENGINE_COLUMNS),
        "planned_figure_ids": list(engine.PLANNED_FIGURE_IDS),
        "all_reports_require_row_date_block_counts": True,
    }:
        raise M3BlindEvaluationContractRepairError("Translated M3 contract changed.")


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    original = engine._validate_lock
    engine._validate_lock = _validate_m3_engine_contract
    try:
        evaluation = parent.run(root)
    finally:
        engine._validate_lock = original
    payload = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_contract_repair_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_evaluation_completion_commit_sha256": evaluation["commit_sha256"],
            "next_safe_stage": "authenticate_and_publish_exact_blind_result",
        }
    )
    destination = helpers._inside(root, COMPLETION_PATH)
    if not destination.exists():
        helpers._write_json_exclusive(payload, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    evaluation = parent.authenticate_completion(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("parent_evaluation_completion_commit_sha256") != evaluation["commit_sha256"]
    ):
        raise M3BlindEvaluationContractRepairError("Repair completion changed.")
    return payload
