"""Durable monotonic resume authorization for the combined blind target claim."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_target_joint_cell_repair_v1 as joint_repair
from la_heat.multicity import m3_blind_target_v1 as parent
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-target-durable-resume-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_TARGET_DURABLE_RESUME_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_TARGET_DURABLE_RESUME_COMPLETE.json"
)
PARENT_AUTHORIZATION_COMMIT: Final = (
    "87794fd56b68711f3fe5499f130aae9ef61fcfeb98beffba238cc522a4299601"
)
JOINT_REPAIR_AUTHORIZATION_COMMIT: Final = (
    "2dc5797de4f542ac9de7bc19c764fe693fec66951b9f924ae0a78e9fa89e259b"
)
IDEMPOTENCE_AUTHORIZATION_COMMIT: Final = (
    "8419a7b5ce301cc7426f71d665fcacf19055607a0c76dfcea3ed711a6602fe30"
)
MINIMUM_PROGRESS: Final = {
    "seattle_wa": 54,
    "denver_co": 15,
    "atlanta_ga": 0,
    "miami_fl": 0,
}
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_target_durable_resume_v1.py",
    "scripts/authorize_m3_blind_target_durable_resume_v1.py",
    "scripts/run_m3_blind_target_durable_resume_v1.py",
)


class M3BlindTargetDurableResumeError(RuntimeError):
    """Raised when durable resume provenance or monotonicity changes."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindTargetDurableResumeError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindTargetDurableResumeError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _counts(root: Path) -> dict[str, int]:
    result = {}
    for city_id in helpers.BLIND_CITY_IDS:
        directory = helpers._inside(root, parent.OUTPUT_ROOT / "overpasses" / city_id)
        result[city_id] = len(list(directory.glob("*.json"))) if directory.exists() else 0
    return result


def _anchor(root: Path, path: Path, expected: str) -> None:
    if _read(helpers._inside(root, path)).get("commit_sha256") != expected:
        raise M3BlindTargetDurableResumeError(f"Authorization anchor changed: {path}")


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    _anchor(root, parent.AUTHORIZATION_PATH, PARENT_AUTHORIZATION_COMMIT)
    _anchor(root, joint_repair.AUTHORIZATION_PATH, JOINT_REPAIR_AUTHORIZATION_COMMIT)
    _anchor(
        root,
        Path(
            "manifests/multicity/next_experiment/"
            "M3_BLIND_TARGET_RESUME_IDEMPOTENCE_V1_AUTHORIZATION.json"
        ),
        IDEMPOTENCE_AUTHORIZATION_COMMIT,
    )
    observed = _counts(root)
    if any(observed[city] < count for city, count in MINIMUM_PROGRESS.items()):
        raise M3BlindTargetDurableResumeError("Completed progress regressed.")
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_durable_resume_authorized",
            "parent_authorization_commit_sha256": PARENT_AUTHORIZATION_COMMIT,
            "joint_repair_authorization_commit_sha256": JOINT_REPAIR_AUTHORIZATION_COMMIT,
            "idempotence_authorization_commit_sha256": IDEMPOTENCE_AUTHORIZATION_COMMIT,
            "minimum_completed_overpasses": MINIMUM_PROGRESS,
            "code": code,
            "permissions": {
                "resume_same_combined_claim_with_monotonic_progress": True,
                "retry_uncommitted_transient_network_failure": True,
                "withhold_relative_endpoints_when_no_joint_cell_is_eligible": True,
                "skip_existing_parquet_only_on_exact_semantic_match": True,
                "overwrite_delete_or_regress_progress": False,
                "change_threshold_city_date_qa_prediction_or_model": False,
            },
            "next_safe_stage": "resume_until_parent_target_completion",
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
        raise M3BlindTargetDurableResumeError("Durable resume authorization drifted.")
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    import la_heat.target_aggregation as aggregation

    original_relative = aggregation.assign_relative_endpoints
    original_write = helpers._write_parquet_exclusive

    def exact_match_or_write(frame: pd.DataFrame, destination: Path) -> None:
        if destination.exists():
            observed = pd.read_parquet(destination)
            sort_by = ["city_id", "target_date", "tract_geoid"]
            if canonical_frame_sha256(observed, sort_by=sort_by) != canonical_frame_sha256(
                frame, sort_by=sort_by
            ):
                raise M3BlindTargetDurableResumeError(f"Existing artifact differs: {destination}")
            return
        original_write(frame, destination)

    aggregation.assign_relative_endpoints = joint_repair.safe_assign_relative_endpoints
    helpers._write_parquet_exclusive = exact_match_or_write
    try:
        target_completion = parent.run(root)
    finally:
        aggregation.assign_relative_endpoints = original_relative
        helpers._write_parquet_exclusive = original_write
    payload = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_durable_resume_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_target_completion_commit_sha256": target_completion["commit_sha256"],
            "final_completed_overpasses": parent.EXPECTED_DATES,
            "next_safe_stage": "authenticate_target_completion_then_authorize_frozen_evaluation",
        }
    )
    destination = helpers._inside(root, COMPLETION_PATH)
    if not destination.exists():
        helpers._write_json_exclusive(payload, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    target = parent.authenticate_completion(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("parent_target_completion_commit_sha256") != target["commit_sha256"]
        or _counts(root) != parent.EXPECTED_DATES
    ):
        raise M3BlindTargetDurableResumeError("Durable resume completion changed.")
    return payload
