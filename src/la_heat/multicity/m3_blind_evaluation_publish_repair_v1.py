"""Append-only publish repair for frozen M3 blind evaluation outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import external_evaluation as engine
from la_heat.multicity import m3_blind_evaluation_bootstrap_defaults_repair_v1 as bootstrap_repair
from la_heat.multicity import m3_blind_evaluation_contract_repair_v1 as contract_repair
from la_heat.multicity import m3_blind_evaluation_v1 as parent
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-evaluation-publish-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_EVALUATION_PUBLISH_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_EVALUATION_PUBLISH_REPAIR_COMPLETE.json"
)
BOOTSTRAP_REPAIR_AUTHORIZATION_COMMIT: Final = (
    "86e9417068d53ece66f48d63e04b59433e3acdb6fd4ce6e53b94f6b72e0ad713"
)
PARTIAL_OUTPUT: Final = parent.OUTPUT_ROOT / "scored_rows.parquet"
OTHER_OUTPUTS: Final = (
    "date_metrics.parquet",
    "city_metrics.parquet",
    "risk_coverage.parquet",
    "summary.json",
    "crossed_bootstrap.json",
)
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_evaluation_publish_repair_v1.py",
    "scripts/authorize_m3_blind_evaluation_publish_repair_v1.py",
    "scripts/run_m3_blind_evaluation_publish_repair_v1.py",
)


class M3BlindEvaluationPublishRepairError(RuntimeError):
    """Raised when the exact evaluation publish repair changes."""


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindEvaluationPublishRepairError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _sort_columns(frame: pd.DataFrame) -> list[str]:
    candidates = (
        ("city_id", "target_date", "tract_geoid"),
        ("city_id", "target_date"),
        ("city_id",),
        ("cohort_id", "coverage_fraction"),
    )
    for keys in candidates:
        if all(key in frame.columns for key in keys):
            return list(keys)
    raise M3BlindEvaluationPublishRepairError("No frozen semantic sort key applies.")


def _semantic(frame: pd.DataFrame, *, sort_by: Any = None) -> str:
    return canonical_frame_sha256(
        frame, sort_by=list(sort_by) if sort_by is not None else _sort_columns(frame)
    )


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    prior = _read(helpers._inside(root, bootstrap_repair.AUTHORIZATION_PATH))
    partial = helpers._inside(root, PARTIAL_OUTPUT)
    if (
        prior["commit_sha256"] != BOOTSTRAP_REPAIR_AUTHORIZATION_COMMIT
        or not partial.is_file()
        or helpers._inside(root, parent.COMPLETION_PATH).exists()
        or any(helpers._inside(root, parent.OUTPUT_ROOT / name).exists() for name in OTHER_OUTPUTS)
    ):
        raise M3BlindEvaluationPublishRepairError("Publish incident snapshot changed.")
    frame = pd.read_parquet(partial)
    partial_record = helpers._record(root, partial, rows=len(frame))
    partial_record["semantic_sha256"] = _semantic(frame)
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_publish_repair_authorized",
            "bootstrap_repair_authorization_commit_sha256": (BOOTSTRAP_REPAIR_AUTHORIZATION_COMMIT),
            "incident": {
                "exception_type": "TypeError",
                "exception_message": (
                    "canonical_frame_sha256() missing 1 required keyword-only argument: sort_by"
                ),
                "metrics_and_bootstrap_computed_in_memory": True,
                "partial_output": partial_record,
                "other_evaluation_outputs_written": 0,
            },
            "semantic_sort_keys": {
                "scored_rows": ["city_id", "target_date", "tract_geoid"],
                "date_metrics": ["city_id", "target_date"],
                "city_metrics": ["city_id"],
                "risk_coverage": ["cohort_id", "coverage_fraction"],
            },
            "code": code,
            "permissions": {
                "reuse_partial_scored_rows_only_on_exact_semantic_match": True,
                "write_remaining_append_only_outputs": True,
                "supply_frozen_semantic_sort_keys": True,
                "overwrite_delete_or_change_existing_output": False,
                "change_metric_bootstrap_gate_prediction_target_or_model": False,
            },
            "next_safe_stage": "rerun_and_publish_identical_frozen_evaluation",
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
        raise M3BlindEvaluationPublishRepairError("Publish repair authorization drifted.")
    return observed


def _patches() -> tuple[Any, Any, Any, Any]:
    return (
        engine._validate_lock,
        engine.city_stratified_crossed_bootstrap,
        helpers._write_parquet_exclusive,
        parent.canonical_frame_sha256,
    )


def _install_patches(original_bootstrap: Any, original_write: Any) -> None:
    def explicit_bootstrap(rows: Any) -> dict[str, Any]:
        return original_bootstrap(rows, iterations=10_000, seed=20_260_816, confidence_level=0.95)

    def exact_match_or_write(frame: pd.DataFrame, destination: Path) -> None:
        if destination.exists():
            observed = pd.read_parquet(destination)
            if _semantic(observed) != _semantic(frame):
                raise M3BlindEvaluationPublishRepairError(
                    f"Existing evaluation output differs: {destination}"
                )
            return
        original_write(frame, destination)

    engine._validate_lock = contract_repair._validate_m3_engine_contract
    engine.city_stratified_crossed_bootstrap = explicit_bootstrap
    helpers._write_parquet_exclusive = exact_match_or_write
    parent.canonical_frame_sha256 = _semantic


def _restore_patches(originals: tuple[Any, Any, Any, Any]) -> None:
    (
        engine._validate_lock,
        engine.city_stratified_crossed_bootstrap,
        helpers._write_parquet_exclusive,
        parent.canonical_frame_sha256,
    ) = originals


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    originals = _patches()
    _install_patches(originals[1], originals[2])
    try:
        evaluation = parent.run(root)
    finally:
        _restore_patches(originals)
    payload = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_publish_repair_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_evaluation_completion_commit_sha256": evaluation["commit_sha256"],
            "next_safe_stage": "publish_exact_blind_result",
        }
    )
    destination = helpers._inside(root, COMPLETION_PATH)
    if not destination.exists():
        helpers._write_json_exclusive(payload, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    originals = _patches()
    parent.canonical_frame_sha256 = _semantic
    try:
        evaluation = parent.authenticate_completion(root)
    finally:
        parent.canonical_frame_sha256 = originals[3]
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("parent_evaluation_completion_commit_sha256") != evaluation["commit_sha256"]
    ):
        raise M3BlindEvaluationPublishRepairError("Publish repair completion changed.")
    return payload
