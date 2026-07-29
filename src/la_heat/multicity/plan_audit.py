"""Read-only planning audit for the cross-city continuation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.config import MulticityPlan, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

PLAN_AUDIT_SCHEMA_VERSION: Final = 5
PLAN_AUDIT_ALGORITHM_VERSION: Final = "multicity-planning-readiness-v5"
PLAN_AUDIT_CODE_PATHS: Final = (
    "configs/multicity/gshhg_geometry_pilot_v1.toml",
    "configs/multicity/gshhg_geometry_pilot_v2.toml",
    "configs/multicity/portable_water_distance_freeze_decision_v1.toml",
    "configs/multicity/water_distance_review_v1.toml",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/gshhg_geometry_pilot.py",
    "src/la_heat/multicity/portable_water_distance_freeze.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/multicity/plan_audit.py",
    "src/la_heat/multicity/water_distance_review.py",
    "scripts/audit_multicity_plan.py",
    "scripts/audit_multicity_portable_water_distance_freeze.py",
    "scripts/audit_multicity_water_distance_review.py",
    "scripts/stage_multicity_gshhg_geometry_pilot.py",
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


def _continuation_planning_state(
    *,
    phoenix_geography: dict[str, Any] | None,
    phoenix_source_footprints: dict[str, Any] | None,
    water_distance_review: dict[str, Any] | None,
    gshhg_geometry_pilot: dict[str, Any] | None,
    water_distance_freeze_decision: dict[str, Any] | None = None,
) -> tuple[str, list[str], str, bool]:
    """Return the exact planning stage, blockers, next action, and review grant."""

    if phoenix_geography is None:
        return (
            "awaiting_phoenix_geography",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "implement_and_test_generic_census_place_tract_adapter",
                "complete_phoenix_metadata_only_pilot",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "phoenix_boundary_and_metadata_only_pilot",
            False,
        )
    if phoenix_source_footprints is None:
        return (
            "ready_for_phoenix_source_footprints",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "complete_phoenix_target_blind_source_footprint_discovery",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "phoenix_target_blind_source_footprint_discovery",
            False,
        )
    if water_distance_freeze_decision is not None:
        if water_distance_review is None:
            raise MulticityPlanAuditError(
                "Water-distance freeze decision exists without the source review."
            )
        if water_distance_review.get("state") != (
            "review_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "Water-distance freeze decision has the wrong source-review state."
            )
        if gshhg_geometry_pilot is None:
            raise MulticityPlanAuditError(
                "Water-distance freeze decision exists without the GSHHG pilot."
            )
        if gshhg_geometry_pilot.get("state") != (
            "geometry_pilot_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "Water-distance freeze decision has the wrong GSHHG pilot state."
            )
    if water_distance_review is None:
        return (
            "phoenix_source_footprints_complete_metadata_only",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "freeze_exact_portable_predictor_source_and_calibration_contract",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "review_portable_water_distance_source_and_algorithm",
            False,
        )
    if water_distance_freeze_decision is not None:
        expected_decision = {
            "state": "decision_complete_freeze_deferred",
            "outcome": "deferred_pending_gshhg_l3_hierarchy_contract",
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
        }
        for key, expected in expected_decision.items():
            if (
                type(water_distance_freeze_decision.get(key)) is not type(expected)
                or water_distance_freeze_decision.get(key) != expected
            ):
                raise MulticityPlanAuditError(
                    "Water-distance decision may only record the authenticated "
                    f"deferred state; {key} changed."
                )
        next_gate = water_distance_freeze_decision.get("next_gate")
        if not isinstance(next_gate, dict) or next_gate.get("stage_id") != (
            "preregister_target_blind_gshhg_l3_hierarchy_audit"
        ):
            raise MulticityPlanAuditError(
                "Water-distance decision next gate changed."
            )
        return (
            "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit",
            [
                "resolve_and_audit_gshhg_l3_lake_island_shoreline_contract",
                "freeze_portable_water_distance_source_and_algorithm",
                "freeze_exact_portable_predictor_source_and_calibration_contract",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "preregister_target_blind_gshhg_l3_hierarchy_audit",
            False,
        )
    if gshhg_geometry_pilot is not None:
        if gshhg_geometry_pilot.get("state") != (
            "geometry_pilot_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "GSHHG geometry pilot state is not the expected non-frozen completion."
            )
        return (
            "gshhg_geometry_pilot_complete_source_not_frozen",
            [
                "freeze_portable_water_distance_source_and_algorithm",
                "freeze_exact_portable_predictor_source_and_calibration_contract",
                "promote_protocol_from_draft_with_separate_lock",
            ],
            "portable_water_distance_source_and_algorithm_freeze_decision",
            False,
        )
    return (
        "portable_water_distance_review_complete_source_not_frozen",
        [
            "complete_target_blind_gshhg_geometry_comparison",
            "freeze_portable_water_distance_source_and_algorithm",
            "freeze_exact_portable_predictor_source_and_calibration_contract",
            "promote_protocol_from_draft_with_separate_lock",
        ],
        "target_blind_gshhg_geometry_comparison",
        True,
    )


def audit_multicity_plan(
    config_path: str | Path,
    *,
    output_path: str | Path | None = None,
    verify_evidence_zip: bool = True,
    write: bool = True,
) -> dict[str, Any]:
    """Authenticate Phase I and the target-blind continuation planning stages."""

    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    anchor = _phase1_anchor(plan, verify_evidence_zip=verify_evidence_zip)
    locks = plan.raw["locks"]
    if locks["external_targets_unlocked"] or locks["external_target_values_read"]:
        raise MulticityPlanAuditError("External target lock is not closed.")
    if any(city.target_values_status != "sealed" for city in plan.external_cities):
        raise MulticityPlanAuditError("Every external city must remain sealed.")

    phoenix_manifest_path = (
        workspace.city("phoenix_az").manifests / "geography" / "GEOGRAPHY.json"
    )
    phoenix_geography: dict[str, Any] | None = None
    if phoenix_manifest_path.is_file():
        from la_heat.multicity.geography import verify_city_geography

        verified_geography = verify_city_geography(plan.path, "phoenix_az")
        phoenix_geography = {
            "state": verified_geography["state"],
            "path": phoenix_manifest_path.relative_to(
                workspace.project_root
            ).as_posix(),
            "file_sha256": sha256_file(phoenix_manifest_path),
            "commit_sha256": verified_geography["commit_sha256"],
            "primary_tract_count": verified_geography["geography"][
                "primary_tract_count"
            ],
            "target_or_qa_values_read": any(
                verified_geography["target_access"].values()
            ),
        }

    phoenix_source_footprints: dict[str, Any] | None = None
    if phoenix_geography is not None:
        phoenix_workspace = workspace.city("phoenix_az")
        source_manifest_path = (
            phoenix_workspace.manifests
            / "source_footprints"
            / "SOURCE_FOOTPRINTS.json"
        )
        if source_manifest_path.is_file():
            from la_heat.multicity.source_footprints import (
                verify_city_source_footprints,
            )

            verified_sources = verify_city_source_footprints(
                plan.path,
                "phoenix_az",
            )
            families = verified_sources["source_families"]
            phoenix_source_footprints = {
                "state": verified_sources["state"],
                "path": source_manifest_path.relative_to(
                    workspace.project_root
                ).as_posix(),
                "file_sha256": sha256_file(source_manifest_path),
                "commit_sha256": verified_sources["commit_sha256"],
                "source_family_counts": {
                    name: int(record["member_count"])
                    for name, record in families.items()
                },
                "landsat_wrs": families["landsat_wrs"]["member_ids"],
                "sentinel_mgrs": families["sentinel_mgrs"]["member_ids"],
                "terrain_tiles": families["terrain_windows"]["member_ids"],
                "target_or_asset_values_read": False,
            }
        else:
            partial_roots = (
                phoenix_workspace.raw / "source_footprints",
                phoenix_workspace.processed / "source_footprints",
            )
            if any(
                root.exists() and any(path.is_file() for path in root.rglob("*"))
                for root in partial_roots
            ):
                raise MulticityPlanAuditError(
                    "Phoenix source-footprint files exist without a commit manifest."
                )

    water_distance_review: dict[str, Any] | None = None
    water_review_path = (
        workspace.manifest_root
        / "reviews"
        / "portable_water_distance"
        / "WATER_DISTANCE_REVIEW.json"
    )
    if water_review_path.is_file():
        from la_heat.multicity.water_distance_review import (
            audit_water_distance_review,
        )

        verified_review = audit_water_distance_review(
            workspace.project_root
            / "configs"
            / "multicity"
            / "water_distance_review_v1.toml",
            output_path=water_review_path,
            write=False,
        )
        water_distance_review = {
            "state": verified_review["state"],
            "path": water_review_path.relative_to(
                workspace.project_root
            ).as_posix(),
            "file_sha256": sha256_file(water_review_path),
            "commit_sha256": verified_review["commit_sha256"],
            "review_outcome": verified_review["review_outcome"],
            "source_lock_created": verified_review["source_lock_created"],
            "algorithm_lock_created": verified_review["algorithm_lock_created"],
            "predictor_build_authorized": verified_review[
                "predictor_build_authorized"
            ],
            "target_or_qa_values_read": False,
        }

    gshhg_geometry_pilot: dict[str, Any] | None = None
    gshhg_pilot_path = (
        workspace.manifest_root
        / "reviews"
        / "portable_water_distance"
        / "GSHHG_GEOMETRY_PILOT.json"
    )
    if gshhg_pilot_path.is_file():
        if water_distance_review is None:
            raise MulticityPlanAuditError(
                "GSHHG geometry pilot exists without the prerequisite water review."
            )
        from la_heat.multicity.gshhg_geometry_pilot import (
            audit_gshhg_geometry_pilot,
        )

        verified_pilot = audit_gshhg_geometry_pilot(
            workspace.project_root
            / "configs"
            / "multicity"
            / "gshhg_geometry_pilot_v2.toml",
            output_path=gshhg_pilot_path,
            write=False,
        )
        if verified_pilot.get("state") != (
            "geometry_pilot_complete_source_not_frozen"
        ):
            raise MulticityPlanAuditError(
                "GSHHG geometry pilot is not in the expected non-frozen state."
            )
        gshhg_geometry_pilot = {
            "state": verified_pilot["state"],
            "path": gshhg_pilot_path.relative_to(
                workspace.project_root
            ).as_posix(),
            "file_sha256": sha256_file(gshhg_pilot_path),
            "commit_sha256": verified_pilot["commit_sha256"],
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "predictor_build_authorized": False,
            "target_or_qa_values_read": False,
        }

    water_distance_freeze_decision: dict[str, Any] | None = None
    freeze_decision_path = (
        workspace.manifest_root
        / "reviews"
        / "portable_water_distance"
        / "WATER_DISTANCE_FREEZE_DECISION.json"
    )
    if freeze_decision_path.is_file():
        if gshhg_geometry_pilot is None:
            raise MulticityPlanAuditError(
                "Water-distance freeze decision exists without the GSHHG pilot."
            )
        from la_heat.multicity.portable_water_distance_freeze import (
            audit_portable_water_distance_freeze_decision,
        )

        verified_decision = audit_portable_water_distance_freeze_decision(
            workspace.project_root
            / "configs"
            / "multicity"
            / "portable_water_distance_freeze_decision_v1.toml",
            output_path=freeze_decision_path,
            write=False,
        )
        water_distance_freeze_decision = {
            "state": verified_decision["state"],
            "path": freeze_decision_path.relative_to(
                workspace.project_root
            ).as_posix(),
            "file_sha256": sha256_file(freeze_decision_path),
            "commit_sha256": verified_decision["commit_sha256"],
            "outcome": verified_decision["outcome"],
            "source_lock_created": verified_decision["source_lock_created"],
            "algorithm_lock_created": verified_decision["algorithm_lock_created"],
            "feature_names_frozen": verified_decision["feature_names_frozen"],
            "predictor_build_authorized": verified_decision[
                "predictor_build_authorized"
            ],
            "protocol_lock_created": verified_decision["protocol_lock_created"],
            "next_gate": verified_decision["next_gate"],
            "target_or_qa_values_read": False,
        }

    (
        planning_stage,
        blockers,
        next_safe_stage,
        source_geometry_review_authorized,
    ) = _continuation_planning_state(
        phoenix_geography=phoenix_geography,
        phoenix_source_footprints=phoenix_source_footprints,
        water_distance_review=water_distance_review,
        gshhg_geometry_pilot=gshhg_geometry_pilot,
        water_distance_freeze_decision=water_distance_freeze_decision,
    )
    l3_preregistration_authorized = planning_stage == (
        "portable_water_distance_freeze_deferred_pending_l3_hierarchy_audit"
    )

    payload: dict[str, Any] = {
        "schema_version": PLAN_AUDIT_SCHEMA_VERSION,
        "algorithm_version": PLAN_AUDIT_ALGORITHM_VERSION,
        "state": "planning_ready",
        "planning_stage": planning_stage,
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
            "target_blind_source_geometry_review": (
                source_geometry_review_authorized
            ),
            "target_blind_gshhg_l3_hierarchy_preregistration": (
                l3_preregistration_authorized
            ),
            "target_blind_gshhg_l3_hierarchy_geometry_read": False,
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
        "phoenix_geography_pilot": phoenix_geography,
        "phoenix_source_footprint_pilot": phoenix_source_footprints,
        "portable_water_distance_review": water_distance_review,
        "gshhg_geometry_pilot": gshhg_geometry_pilot,
        "portable_water_distance_freeze_decision": (
            water_distance_freeze_decision
        ),
        "blockers_before_predictor_build": blockers,
        "next_safe_stage": next_safe_stage,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    destination = (
        Path(output_path)
        if output_path is not None
        else workspace.manifest_root / "PLAN_READINESS.json"
    )
    if write:
        atomic_json(payload, destination)
    else:
        committed, _ = _committed_json(destination)
        if canonical_sha256(committed) != canonical_sha256(payload):
            raise MulticityPlanAuditError(
                f"Readiness record is stale or changed: {destination}"
            )
    return payload
