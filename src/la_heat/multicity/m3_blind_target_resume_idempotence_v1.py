"""Append-only exact-match resume repair for completed blind city tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_target_joint_cell_repair_v1 as joint_repair
from la_heat.multicity import m3_blind_target_v1 as parent
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-target-resume-idempotence-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_TARGET_RESUME_IDEMPOTENCE_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_TARGET_RESUME_IDEMPOTENCE_COMPLETE.json"
)
JOINT_REPAIR_AUTHORIZATION_COMMIT: Final = (
    "2dc5797de4f542ac9de7bc19c764fe693fec66951b9f924ae0a78e9fa89e259b"
)
SEATTLE_OUTPUT: Final = parent.OUTPUT_ROOT / "cities/seattle_wa/targets_4k.parquet"
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_target_resume_idempotence_v1.py",
    "scripts/authorize_m3_blind_target_resume_idempotence_v1.py",
    "scripts/run_m3_blind_target_resume_idempotence_v1.py",
)


class M3BlindTargetResumeIdempotenceError(RuntimeError):
    """Raised when exact-match resume evidence changes."""


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindTargetResumeIdempotenceError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    joint = joint_repair.authenticate_authorization(root)
    output = helpers._inside(root, SEATTLE_OUTPUT)
    frame = pd.read_parquet(output)
    if joint["commit_sha256"] != JOINT_REPAIR_AUTHORIZATION_COMMIT:
        raise M3BlindTargetResumeIdempotenceError("Joint repair anchor changed.")
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_resume_idempotence_authorized",
            "joint_repair_authorization_commit_sha256": JOINT_REPAIR_AUTHORIZATION_COMMIT,
            "incident": {
                "exception_type": "M3BlindPredictionError",
                "exception_message": f"Append-only artifact exists: {output}",
                "existing_seattle_output": {
                    **helpers._record(root, output, rows=len(frame)),
                    "semantic_sha256": canonical_frame_sha256(
                        frame, sort_by=["city_id", "target_date", "tract_geoid"]
                    ),
                },
            },
            "code": code,
            "permissions": {
                "resume_same_combined_claim": True,
                "skip_existing_city_table_only_on_exact_semantic_match": True,
                "overwrite_delete_or_change_existing_artifact": False,
                "change_threshold_city_date_qa_prediction_or_model": False,
            },
            "next_safe_stage": "resume_joint_cell_repair_with_exact_match_city_skip",
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
        raise M3BlindTargetResumeIdempotenceError("Resume authorization drifted.")
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    original = helpers._write_parquet_exclusive

    def exact_match_or_write(frame: pd.DataFrame, destination: Path) -> None:
        if destination.exists():
            observed = pd.read_parquet(destination)
            sort_by = ["city_id", "target_date", "tract_geoid"]
            if canonical_frame_sha256(observed, sort_by=sort_by) != canonical_frame_sha256(
                frame, sort_by=sort_by
            ):
                raise M3BlindTargetResumeIdempotenceError(
                    f"Existing artifact differs: {destination}"
                )
            return
        original(frame, destination)

    helpers._write_parquet_exclusive = exact_match_or_write
    try:
        joint_completion = joint_repair.run(root)
    finally:
        helpers._write_parquet_exclusive = original
    payload = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_resume_idempotence_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "joint_repair_completion_commit_sha256": joint_completion["commit_sha256"],
            "next_safe_stage": "authenticate_all_target_completions_before_evaluation",
        }
    )
    destination = helpers._inside(root, COMPLETION_PATH)
    if not destination.exists():
        helpers._write_json_exclusive(payload, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    joint = joint_repair.authenticate_completion(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("joint_repair_completion_commit_sha256") != joint["commit_sha256"]
    ):
        raise M3BlindTargetResumeIdempotenceError("Resume completion changed.")
    return payload
