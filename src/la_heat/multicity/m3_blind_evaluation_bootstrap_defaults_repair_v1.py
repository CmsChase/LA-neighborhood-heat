"""Append-only repair for legacy bootstrap defaults captured at import time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import external_evaluation as engine
from la_heat.multicity import m3_blind_evaluation_contract_repair_v1 as contract_repair
from la_heat.multicity import m3_blind_evaluation_v1 as parent
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-evaluation-bootstrap-defaults-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_EVALUATION_BOOTSTRAP_DEFAULTS_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_EVALUATION_BOOTSTRAP_DEFAULTS_REPAIR_COMPLETE.json"
)
CONTRACT_REPAIR_AUTHORIZATION_COMMIT: Final = (
    "2b775eda05dabfa90e4182ee3a279f35a0630d19dd9847357f563a6d9a50a9d7"
)
INCIDENT: Final = {
    "exception_type": "ExternalEvaluationError",
    "exception_message": "Frozen bootstrap parameters cannot be changed",
    "row_date_city_metrics_computed_in_memory": True,
    "bootstrap_computed": False,
    "evaluation_outputs_written": 0,
    "cause": "legacy_function_default_seed_captured_as_20260728_at_import_time",
}
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_evaluation_bootstrap_defaults_repair_v1.py",
    "scripts/authorize_m3_blind_evaluation_bootstrap_defaults_repair_v1.py",
    "scripts/run_m3_blind_evaluation_bootstrap_defaults_repair_v1.py",
)


class M3BlindEvaluationBootstrapDefaultsRepairError(RuntimeError):
    """Raised when the exact bootstrap-default repair changes."""


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindEvaluationBootstrapDefaultsRepairError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _outputs_exist(root: Path) -> bool:
    names = (
        "scored_rows.parquet",
        "date_metrics.parquet",
        "city_metrics.parquet",
        "risk_coverage.parquet",
        "summary.json",
        "crossed_bootstrap.json",
    )
    return any(helpers._inside(root, parent.OUTPUT_ROOT / name).exists() for name in names)


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    prior = contract_repair.authenticate_authorization(root)
    if (
        prior["commit_sha256"] != CONTRACT_REPAIR_AUTHORIZATION_COMMIT
        or helpers._inside(root, parent.COMPLETION_PATH).exists()
        or _outputs_exist(root)
    ):
        raise M3BlindEvaluationBootstrapDefaultsRepairError(
            "Bootstrap repair incident snapshot changed."
        )
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_bootstrap_defaults_repair_authorized",
            "contract_repair_authorization_commit_sha256": (CONTRACT_REPAIR_AUTHORIZATION_COMMIT),
            "incident": INCIDENT,
            "exact_bootstrap_arguments": {
                "iterations": 10_000,
                "seed": 20_260_816,
                "confidence_level": 0.95,
            },
            "code": code,
            "permissions": {
                "explicitly_forward_frozen_bootstrap_arguments": True,
                "reuse_identical_crossed_bootstrap_algorithm": True,
                "change_bootstrap_method_seed_iterations_confidence_or_gate": False,
                "fit_retrain_recalibrate_retune_or_select": False,
            },
            "next_safe_stage": "rerun_frozen_evaluation_with_explicit_bootstrap_arguments",
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
        raise M3BlindEvaluationBootstrapDefaultsRepairError(
            "Bootstrap repair authorization drifted."
        )
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    original_validate = engine._validate_lock
    original_bootstrap = engine.city_stratified_crossed_bootstrap

    def explicit_bootstrap(rows: Any) -> dict[str, Any]:
        return original_bootstrap(
            rows,
            iterations=10_000,
            seed=20_260_816,
            confidence_level=0.95,
        )

    engine._validate_lock = contract_repair._validate_m3_engine_contract
    engine.city_stratified_crossed_bootstrap = explicit_bootstrap
    try:
        evaluation = parent.run(root)
    finally:
        engine._validate_lock = original_validate
        engine.city_stratified_crossed_bootstrap = original_bootstrap
    payload = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_bootstrap_defaults_repair_complete",
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
        raise M3BlindEvaluationBootstrapDefaultsRepairError("Bootstrap repair completion changed.")
    return payload
