"""Read-only planning audit for the cross-city continuation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.config import MulticityPlan, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

PLAN_AUDIT_SCHEMA_VERSION: Final = 1
PLAN_AUDIT_ALGORITHM_VERSION: Final = "multicity-planning-readiness-v1"
PLAN_AUDIT_CODE_PATHS: Final = (
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/multicity/plan_audit.py",
    "scripts/audit_multicity_plan.py",
)


class MulticityPlanAuditError(ValueError):
    """Raised when Phase I identity or continuation locks do not authenticate."""


def _committed_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MulticityPlanAuditError(f"Cannot read authenticated JSON: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise MulticityPlanAuditError(f"Authenticated JSON must be an object: {path}")
    recorded = payload.get("commit_sha256")
    without_commit = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(without_commit) != recorded:
        raise MulticityPlanAuditError(f"Invalid internal commit hash: {path}")
    return payload, before


def _phase1_anchor(
    plan: MulticityPlan,
    *,
    verify_evidence_zip: bool,
) -> dict[str, Any]:
    root = plan.path.parents[2]
    expected = plan.raw["phase1_anchor"]
    model_lock_path = root / "manifests/model_lock/MODEL_LOCK.json"
    completion_path = (
        root / "manifests/final_test_2025/evaluation/EVALUATION_COMPLETE.json"
    )
    evidence_path = (
        root / "manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json"
    )
    model_lock, model_lock_file_sha = _committed_json(model_lock_path)
    completion, completion_file_sha = _committed_json(completion_path)
    evidence, evidence_file_sha = _committed_json(evidence_path)

    if model_lock.get("commit_sha256") != expected["model_lock_commit_sha256"]:
        raise MulticityPlanAuditError("Phase I model-lock identity changed.")
    if completion.get("state") != "complete_one_time_final_evaluation":
        raise MulticityPlanAuditError("Phase I evaluation is not complete.")
    if completion.get("claim_id") != expected["claim_id"]:
        raise MulticityPlanAuditError("Phase I completion claim changed.")
    if completion.get("commit_sha256") != expected["evaluation_completion_commit_sha256"]:
        raise MulticityPlanAuditError("Phase I completion commit changed.")
    if completion.get("output_commit_sha256") != expected["evaluation_output_commit_sha256"]:
        raise MulticityPlanAuditError("Phase I output commit changed.")
    if evidence.get("state") != "verified_read_only_evidence_export":
        raise MulticityPlanAuditError("Phase I evidence export is not verified.")
    if evidence.get("claim_id") != expected["claim_id"]:
        raise MulticityPlanAuditError("Phase I evidence claim changed.")
    if evidence.get("zip_sha256") != expected["evidence_zip_sha256"]:
        raise MulticityPlanAuditError("Phase I evidence ZIP identity changed.")

    cache_commits = sorted(
        (
            root
            / "data/interim/final_test_2025/evaluation/target_cache/targets/by_overpass"
        ).glob("*/CACHE_COMMIT.json")
    )
    if len(cache_commits) != 23:
        raise MulticityPlanAuditError(
            f"Phase I target cache must contain 23 commits, found {len(cache_commits)}."
        )
    output_dir = root / "data/processed/final_test_2025/final_evaluation"
    output_files = sorted(path for path in output_dir.iterdir() if path.is_file())
    if len(output_files) != 21:
        raise MulticityPlanAuditError(
            f"Phase I final output must contain 21 files, found {len(output_files)}."
        )
    staging = root / "data/processed/final_test_2025/.final_evaluation.staging"
    if staging.exists():
        raise MulticityPlanAuditError("Phase I final-evaluation staging unexpectedly exists.")

    zip_path = root / "exports/FINAL_EVALUATION_EVIDENCE.zip"
    zip_record: dict[str, Any] = {
        "path": zip_path.relative_to(root).as_posix(),
        "verified_bytes": False,
        "expected_sha256": expected["evidence_zip_sha256"],
    }
    if verify_evidence_zip:
        if not zip_path.is_file():
            raise FileNotFoundError(zip_path)
        actual_zip_sha = sha256_file(zip_path)
        if actual_zip_sha != expected["evidence_zip_sha256"]:
            raise MulticityPlanAuditError("Phase I evidence ZIP bytes changed.")
        zip_record.update(
            {
                "verified_bytes": True,
                "sha256": actual_zip_sha,
                "bytes": zip_path.stat().st_size,
            }
        )

    return {
        "claim_id": expected["claim_id"],
        "model_lock": {
            "path": model_lock_path.relative_to(root).as_posix(),
            "file_sha256": model_lock_file_sha,
            "commit_sha256": model_lock["commit_sha256"],
        },
        "completion": {
            "path": completion_path.relative_to(root).as_posix(),
            "file_sha256": completion_file_sha,
            "commit_sha256": completion["commit_sha256"],
            "output_commit_sha256": completion["output_commit_sha256"],
        },
        "evidence_export": {
            "path": evidence_path.relative_to(root).as_posix(),
            "file_sha256": evidence_file_sha,
            "commit_sha256": evidence["commit_sha256"],
            "zip": zip_record,
        },
        "cache_commit_count": len(cache_commits),
        "final_output_file_count": len(output_files),
        "staging_absent": True,
    }


def audit_multicity_plan(
    config_path: str | Path,
    *,
    output_path: str | Path | None = None,
    verify_evidence_zip: bool = True,
    write: bool = True,
) -> dict[str, Any]:
    """Authenticate Phase I and prove that only metadata staging is authorized."""

    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    anchor = _phase1_anchor(plan, verify_evidence_zip=verify_evidence_zip)
    locks = plan.raw["locks"]
    if locks["external_targets_unlocked"] or locks["external_target_values_read"]:
        raise MulticityPlanAuditError("External target lock is not closed.")
    if any(city.target_values_status != "sealed" for city in plan.external_cities):
        raise MulticityPlanAuditError("Every external city must remain sealed.")

    payload: dict[str, Any] = {
        "schema_version": PLAN_AUDIT_SCHEMA_VERSION,
        "algorithm_version": PLAN_AUDIT_ALGORITHM_VERSION,
        "state": "planning_ready",
        "experiment_id": plan.experiment_id,
        "config_semantic_sha256": plan.semantic_sha256,
        "config_files": plan.file_records,
        "code_files": {
            relative: {
                "sha256": sha256_file(plan.path.parents[2] / relative),
                "bytes": (plan.path.parents[2] / relative).stat().st_size,
            }
            for relative in PLAN_AUDIT_CODE_PATHS
        },
        "phase1_anchor": anchor,
        "cities": [
            {
                "id": city.id,
                "name": city.name,
                "role": city.role,
                "census_place_geoid": city.census_place_geoid,
                "target_values_status": city.target_values_status,
            }
            for city in plan.cities
        ],
        "locks": {
            "protocol_locked": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "authorized_now": {
            "boundary_and_public_metadata_staging": True,
            "portable_predictor_source_freeze": False,
            "predictor_construction": False,
            "model_fitting": False,
            "external_target_or_qa_value_access": False,
            "one_time_external_evaluation": False,
            "operational_forecast_claim": False,
        },
        "workspace": {
            "raw_root": workspace.raw_root.relative_to(workspace.project_root).as_posix(),
            "interim_root": workspace.interim_root.relative_to(
                workspace.project_root
            ).as_posix(),
            "processed_root": workspace.processed_root.relative_to(
                workspace.project_root
            ).as_posix(),
            "manifest_root": workspace.manifest_root.relative_to(
                workspace.project_root
            ).as_posix(),
            "report_root": workspace.report_root.relative_to(
                workspace.project_root
            ).as_posix(),
            "export_root": workspace.export_root.relative_to(
                workspace.project_root
            ).as_posix(),
        },
        "blockers_before_predictor_build": [
            "freeze_portable_water_distance_source_and_algorithm",
            "implement_and_test_generic_census_place_tract_adapter",
            "complete_phoenix_metadata_only_pilot",
            "promote_protocol_from_draft_with_separate_lock",
        ],
        "next_safe_stage": "phoenix_boundary_and_metadata_only_pilot",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    if write:
        destination = (
            Path(output_path)
            if output_path is not None
            else workspace.manifest_root / "PLAN_READINESS.json"
        )
        atomic_json(payload, destination)
    return payload
