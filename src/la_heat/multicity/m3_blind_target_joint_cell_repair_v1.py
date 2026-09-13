"""Append-only repair for blind cities with no eligible relative joint cell."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_target_v1 as parent
from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.targets import RelativeEndpointSummary

ALGORITHM_VERSION: Final = "m3-blind-target-joint-cell-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_TARGET_JOINT_CELL_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_TARGET_JOINT_CELL_REPAIR_COMPLETE.json"
)
PARENT_AUTHORIZATION_COMMIT: Final = (
    "87794fd56b68711f3fe5499f130aae9ef61fcfeb98beffba238cc522a4299601"
)
INCIDENT: Final = {
    "exception_type": "ValueError",
    "exception_message": "No joint geographic cell meets the minimum tract count.",
    "completed_overpasses": {"seattle_wa": 54, "denver_co": 0, "atlanta_ga": 0, "miami_fl": 0},
}
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_target_joint_cell_repair_v1.py",
    "scripts/authorize_m3_blind_target_joint_cell_repair_v1.py",
    "scripts/run_m3_blind_target_joint_cell_repair_v1.py",
)


class M3BlindTargetJointCellRepairError(RuntimeError):
    """Raised when the narrowly scoped target repair changes."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindTargetJointCellRepairError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindTargetJointCellRepairError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _observed_counts(root: Path) -> dict[str, int]:
    result = {}
    for city_id in helpers.BLIND_CITY_IDS:
        directory = helpers._inside(root, parent.OUTPUT_ROOT / "overpasses" / city_id)
        result[city_id] = len(list(directory.glob("*.json"))) if directory.exists() else 0
    return result


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_permit = parent.authenticate_authorization(root)
    marker = _read(helpers._inside(root, parent.VALUES_OPENED_PATH))
    if (
        parent_permit["commit_sha256"] != PARENT_AUTHORIZATION_COMMIT
        or marker.get("authorization_commit_sha256") != PARENT_AUTHORIZATION_COMMIT
        or _observed_counts(root) != INCIDENT["completed_overpasses"]
    ):
        raise M3BlindTargetJointCellRepairError("Repair incident snapshot changed.")
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_joint_cell_repair_authorized",
            "parent_authorization_commit_sha256": PARENT_AUTHORIZATION_COMMIT,
            "values_opened_commit_sha256": marker["commit_sha256"],
            "incident": INCIDENT,
            "code": code,
            "repair_semantics": {
                "exact_exception_only": INCIDENT["exception_message"],
                "coverage_pass": False,
                "relative_anomaly_and_hotspot_withheld": True,
                "absolute_target_preserved": True,
                "minimum_joint_cell_tracts_changed": False,
            },
            "permissions": {
                "resume_same_combined_four_city_claim": True,
                "reuse_completed_seattle_overpasses": True,
                "convert_exact_empty_eligible_joint_cell_exception_to_gate_failure": True,
                "change_threshold_city_date_qa_prediction_or_model": False,
                "score_or_evaluate_before_target_completion_authentication": False,
            },
            "next_safe_stage": "resume_parent_target_runner_with_exact_exception_repair",
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
        raise M3BlindTargetJointCellRepairError("Repair authorization drifted.")
    return observed


def safe_assign_relative_endpoints(
    frame: pd.DataFrame,
    *,
    hotspot_fraction: float,
    minimum_tract_fraction: float,
    maximum_quartile_retention_gap: float,
    minimum_joint_cell_tracts: int,
    minimum_joint_cell_retention_fraction: float,
) -> tuple[pd.DataFrame, RelativeEndpointSummary]:
    """Preserve the gate and withhold relative endpoints if no cell is eligible."""

    from la_heat.targets import assign_relative_endpoints as original

    try:
        return original(
            frame,
            hotspot_fraction=hotspot_fraction,
            minimum_tract_fraction=minimum_tract_fraction,
            maximum_quartile_retention_gap=maximum_quartile_retention_gap,
            minimum_joint_cell_tracts=minimum_joint_cell_tracts,
            minimum_joint_cell_retention_fraction=minimum_joint_cell_retention_fraction,
        )
    except ValueError as error:
        if str(error) != INCIDENT["exception_message"]:
            raise
    result = frame.copy()
    retained = result["target_lst_c"].notna()
    block_rates = retained.groupby(result["spatial_block"], sort=True).mean()
    latitude_rates = retained.groupby(result["latitude_quartile"], sort=True).mean()
    longitude_rates = retained.groupby(result["longitude_quartile"], sort=True).mean()
    result["lst_anomaly_c"] = pd.Series(float("nan"), index=result.index, dtype=float)
    result["relative_hotspot_top20"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
    return result, RelativeEndpointSummary(
        coverage_pass=False,
        retained_tract_count=int(retained.sum()),
        retained_tract_fraction=float(retained.mean()),
        minimum_block_retention_fraction=float(block_rates.min()),
        latitude_quartile_retention_gap=float(latitude_rates.max() - latitude_rates.min()),
        longitude_quartile_retention_gap=float(longitude_rates.max() - longitude_rates.min()),
        minimum_eligible_joint_cell_retention_fraction=0.0,
        hotspot_count=0,
        hotspot_threshold_c=None,
    )


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    import la_heat.target_aggregation as aggregation

    original = aggregation.assign_relative_endpoints
    aggregation.assign_relative_endpoints = safe_assign_relative_endpoints
    try:
        completion = parent.run(root)
    finally:
        aggregation.assign_relative_endpoints = original
    repair_completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_joint_cell_repair_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_target_completion_commit_sha256": completion["commit_sha256"],
            "repair_semantics": permit["repair_semantics"],
            "next_safe_stage": "authenticate_parent_and_repair_completions_before_evaluation",
        }
    )
    destination = helpers._inside(root, COMPLETION_PATH)
    if not destination.exists():
        helpers._write_json_exclusive(repair_completion, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    parent_completion = parent.authenticate_completion(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("parent_target_completion_commit_sha256")
        != parent_completion["commit_sha256"]
        or payload.get("repair_semantics") != permit["repair_semantics"]
    ):
        raise M3BlindTargetJointCellRepairError("Repair completion changed.")
    return payload
