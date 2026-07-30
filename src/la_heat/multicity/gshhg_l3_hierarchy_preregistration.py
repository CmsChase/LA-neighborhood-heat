"""Create the target-blind GSHHG L3 hierarchy-audit preregistration.

This module is intentionally tracked-input-only.  It authenticates the
experiment configuration and two already committed JSON records, then writes
the fixed audit contract.  It must not open the GSHHG ZIP, any ZIP member,
geometry, eligible-land support, target, predictor, model, prediction, or
result artifact.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.config import load_multicity_plan
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "gshhg-l3-hierarchy-audit-preregistration-v1"
COMPLETE_STATE: Final = "gshhg_l3_hierarchy_audit_preregistered_geometry_unopened"
EXPECTED_CONFIG_SHA256: Final = (
    "6fcac13640a8914543d7d057e19cd18ec7ddea74a8d3f50406f4c5dd81e2c1cd"
)
EXPECTED_EXPERIMENT_ID: Final = "la_to_three_city_zero_shot_v1"
EXPECTED_EXPERIMENT_SEMANTIC_SHA256: Final = (
    "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
)
EXPECTED_SELECTED_L2_IDS: Final = [180507, 180515, 180517]
EXPECTED_L3_MEMBERS: Final = [
    "GSHHS_shp/f/GSHHS_f_L3.dbf",
    "GSHHS_shp/f/GSHHS_f_L3.prj",
    "GSHHS_shp/f/GSHHS_f_L3.shp",
    "GSHHS_shp/f/GSHHS_f_L3.shx",
]
DEFAULT_CONFIG: Final = Path(
    "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml"
)
DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
)
CODE_PATHS: Final = (
    "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml",
    "scripts/preregister_multicity_gshhg_l3_hierarchy_audit.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/gshhg_l3_hierarchy_preregistration.py",
    "src/la_heat/provenance.py",
)
EXPECTED_PLAN_AUTHORIZATION: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": True,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": False,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
EXPECTED_CLOSED_LOCKS: Final = {
    "source_lock_created": False,
    "algorithm_lock_created": False,
    "feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
}
EXPECTED_DECISION_TOP_LEVEL_LOCKS: Final = {
    key: EXPECTED_CLOSED_LOCKS[key]
    for key in (
        "source_lock_created",
        "algorithm_lock_created",
        "feature_names_frozen",
        "predictor_build_authorized",
        "protocol_lock_created",
    )
}


class GshhgL3HierarchyPreregistrationError(ValueError):
    """Raised when the preregistration cannot authenticate without data access."""


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(  # type: ignore[arg-type]
            _strict_equal(actual[key], expected[key])  # type: ignore[index]
            for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(  # type: ignore[arg-type]
            _strict_equal(left, right)
            for left, right in zip(actual, expected, strict=True)  # type: ignore[arg-type]
        )
    return bool(actual == expected)


def _require_table(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GshhgL3HierarchyPreregistrationError(f"{label} must be a table.")
    return value


def _read_committed_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GshhgL3HierarchyPreregistrationError(
            f"Cannot read committed JSON: {path}"
        ) from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Committed input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise GshhgL3HierarchyPreregistrationError(
            f"Committed JSON must be an object: {path}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise GshhgL3HierarchyPreregistrationError(
            f"Committed JSON has an invalid internal commit: {path}"
        )
    return payload, before


def _read_config(path: str | Path) -> tuple[Path, Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    observed_sha = sha256_file(config_path)
    if observed_sha != EXPECTED_CONFIG_SHA256:
        raise GshhgL3HierarchyPreregistrationError(
            "L3 preregistration config bytes changed."
        )
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise GshhgL3HierarchyPreregistrationError(
            "L3 preregistration config must be a table."
        )
    project_root = config_path.parents[2]
    _validate_config_controls(payload)
    return project_root, config_path, payload


def _validate_config_controls(config: dict[str, Any]) -> None:
    expected_sections = {
        "preregistration",
        "prerequisites",
        "source",
        "license_record",
        "unchanged_v2_contract",
        "hierarchy_contract",
        "structural_audit",
        "probe_rule",
        "numerical_audit",
        "diagnostic_points",
        "phase_order_and_failure",
        "outputs",
        "locks",
        "access_contract",
        "next_gate",
    }
    if set(config) != expected_sections:
        raise GshhgL3HierarchyPreregistrationError(
            "L3 preregistration config sections changed."
        )
    preregistration = _require_table(
        config["preregistration"], label="preregistration"
    )
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "preregistration_id": "target_blind_gshhg_l3_hierarchy_audit_v1",
        "preregistration_date": "2026-07-30",
        "scope": "target-blind source-only GSHHG L3 hierarchy audit",
        "state": COMPLETE_STATE,
        "expected_experiment_id": EXPECTED_EXPERIMENT_ID,
        "expected_experiment_semantic_sha256": (
            EXPECTED_EXPERIMENT_SEMANTIC_SHA256
        ),
        "base_repository_commit": "9209ec244319f14be7c2bcb8b56c38bee12256e0",
    }
    if not _strict_equal(preregistration, expected_identity):
        raise GshhgL3HierarchyPreregistrationError(
            "L3 preregistration identity changed."
        )

    source = _require_table(config["source"], label="source")
    if source.get("required_l3_members") != EXPECTED_L3_MEMBERS:
        raise GshhgL3HierarchyPreregistrationError(
            "The exact L3 member paths changed."
        )
    if source.get("member_paths_are_preregistered_not_observed") is not True:
        raise GshhgL3HierarchyPreregistrationError(
            "L3 member paths must remain explicitly unobserved."
        )
    if source.get("member_bytes_hashes_crcs_are_unknown_until_audit") is not True:
        raise GshhgL3HierarchyPreregistrationError(
            "L3 member hashes and CRCs cannot be claimed before the audit."
        )

    unchanged = _require_table(
        config["unchanged_v2_contract"], label="unchanged_v2_contract"
    )
    if unchanged.get("selected_l2_source_ids") != EXPECTED_SELECTED_L2_IDS:
        raise GshhgL3HierarchyPreregistrationError(
            "The three selected L2 source IDs changed."
        )
    hierarchy = _require_table(config["hierarchy_contract"], label="hierarchy")
    if hierarchy.get("selected_l2_source_ids") != EXPECTED_SELECTED_L2_IDS:
        raise GshhgL3HierarchyPreregistrationError(
            "The L3 direct-parent rule changed."
        )
    required_true = (
        "include_every_direct_l3_descendant",
        "selected_l3_exteriors_only",
        "selected_l3_interior_rings_excluded",
        "sibling_id_is_audit_only_not_selection",
    )
    if any(hierarchy.get(key) is not True for key in required_true):
        raise GshhgL3HierarchyPreregistrationError(
            "The exterior-only direct-descendant rule changed."
        )
    prohibited_selection = (
        "selection_may_use_city",
        "selection_may_use_bbox",
        "selection_may_use_name",
        "hierarchy_inclusion_selection_may_use_area",
        "selection_may_use_distance",
        "selection_may_use_eligible_support",
        "selection_may_use_target_or_qa",
        "selection_may_use_model_prediction_or_result",
        "l4_members_may_be_opened",
        "l4_geometry_included",
    )
    if any(hierarchy.get(key) is not False for key in prohibited_selection):
        raise GshhgL3HierarchyPreregistrationError(
            "A prohibited L3 selection or L4 access route was enabled."
        )

    structure = _require_table(config["structural_audit"], label="structural_audit")
    if structure.get("geometry_repair_allowed") is not False:
        raise GshhgL3HierarchyPreregistrationError(
            "Unpreregistered geometry repair cannot be enabled."
        )
    if structure.get("unexpected_structure_fails_before_probe_or_distance") is not True:
        raise GshhgL3HierarchyPreregistrationError(
            "Structural failures must stop before every distance."
        )
    probe = _require_table(config["probe_rule"], label="probe_rule")
    if probe.get("probe_reselection_after_distance_allowed") is not False:
        raise GshhgL3HierarchyPreregistrationError(
            "Distance-informed probe reselection cannot be enabled."
        )
    numerical = _require_table(config["numerical_audit"], label="numerical_audit")
    if numerical.get("search_radii_km") != [64, 128, 256, 512, 1024, 2048]:
        raise GshhgL3HierarchyPreregistrationError("Radius gates changed.")
    if numerical.get("line_chunk_vertex_counts") != [256, 1024, 4096]:
        raise GshhgL3HierarchyPreregistrationError("Line-chunk gates changed.")
    if numerical.get("query_chunk_sizes") != [1, 2, 4]:
        raise GshhgL3HierarchyPreregistrationError("Query-chunk gates changed.")
    if numerical.get("worker_counts") != [1, 2, 4]:
        raise GshhgL3HierarchyPreregistrationError("Worker gates changed.")

    if not _strict_equal(config["locks"], EXPECTED_CLOSED_LOCKS):
        raise GshhgL3HierarchyPreregistrationError(
            "Every continuation lock must remain closed."
        )
    access = _require_table(config["access_contract"], label="access_contract")
    forbidden_true = (
        "gshhg_archive_bytes_opened_by_preregistration_program",
        "gshhg_archive_members_opened_by_preregistration_program",
        "gshhg_l3_member_opened",
        "gshhg_l3_geometry_opened",
        "gshhg_l4_member_opened",
        "other_public_source_geometry_opened",
        "eligible_land_grid_opened",
        "distance_values_computed",
        "distance_feature_surface_computed",
        "tract_aggregation_performed",
        "predictor_values_computed",
        "predictor_construction_performed",
        "model_fit_performed",
        "model_predictions_computed",
        "landsat_thermal_values_read",
        "landsat_target_qa_values_read",
        "external_lst_values_read",
        "external_target_files_opened",
        "final_evaluation_outputs_opened",
    )
    if any(access.get(key) is not False for key in forbidden_true):
        raise GshhgL3HierarchyPreregistrationError(
            "The preregistration access boundary changed."
        )
    if access.get("preregistration_program_network_requests") != 0:
        raise GshhgL3HierarchyPreregistrationError(
            "The preregistration program must make zero network requests."
        )


def _require_plan_locks_closed(plan_raw: dict[str, Any]) -> None:
    locks = plan_raw["locks"]
    for key in (
        "protocol_locked",
        "external_targets_unlocked",
        "external_target_values_read",
        "external_prediction_commit_exists",
        "allow_predictor_construction",
        "allow_model_fitting",
        "allow_external_target_access",
    ):
        if locks.get(key) is not False:
            raise GshhgL3HierarchyPreregistrationError(
                f"Experiment lock {key} must remain false."
            )
    if plan_raw["predictors"].get("portable_water_distance_source_frozen") is not False:
        raise GshhgL3HierarchyPreregistrationError(
            "Portable water-distance source must remain unfrozen."
        )
    if plan_raw["sources"].get("portable_water_distance_source") != "NOT_YET_FROZEN":
        raise GshhgL3HierarchyPreregistrationError(
            "Portable water-distance source identity must remain unset."
        )


def _authenticate_plan_readiness(
    project_root: Path,
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = project_root / settings["path"]
    payload, file_sha = _read_committed_json(path)
    for key, expected in (
        ("file_sha256", settings["expected_file_sha256"]),
        ("commit_sha256", settings["expected_commit_sha256"]),
        ("state", settings["expected_state"]),
        ("planning_stage", settings["expected_planning_stage"]),
        ("next_safe_stage", settings["expected_next_safe_stage"]),
    ):
        actual = file_sha if key == "file_sha256" else payload.get(key)
        if not _strict_equal(actual, expected):
            raise GshhgL3HierarchyPreregistrationError(
                f"Prerequisite plan readiness {key} changed."
            )
    if not _strict_equal(payload.get("authorized_now"), EXPECTED_PLAN_AUTHORIZATION):
        raise GshhgL3HierarchyPreregistrationError(
            "Prerequisite plan authorization changed."
        )
    if settings["preregistration_authorized"] is not True:
        raise GshhgL3HierarchyPreregistrationError(
            "The preregistration is not authorized by configuration."
        )
    if settings["l3_geometry_read_authorized"] is not False:
        raise GshhgL3HierarchyPreregistrationError(
            "L3 geometry must still be unauthorized during preregistration."
        )
    return payload, {
        "path": settings["path"],
        "file_sha256": file_sha,
        "bytes": path.stat().st_size,
        "commit_sha256": payload["commit_sha256"],
        "state": payload["state"],
        "planning_stage": payload["planning_stage"],
        "next_safe_stage": payload["next_safe_stage"],
        "authorized_now": payload["authorized_now"],
    }


def _authenticate_freeze_decision(
    project_root: Path,
    settings: dict[str, Any],
    *,
    expected_experiment_id: str,
    expected_plan_sha: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = project_root / settings["path"]
    payload, file_sha = _read_committed_json(path)
    for key, expected in (
        ("file_sha256", settings["expected_file_sha256"]),
        ("commit_sha256", settings["expected_commit_sha256"]),
        ("state", settings["expected_state"]),
        ("outcome", settings["expected_outcome"]),
        ("experiment_id", expected_experiment_id),
        ("plan_semantic_sha256", expected_plan_sha),
    ):
        actual = file_sha if key == "file_sha256" else payload.get(key)
        if not _strict_equal(actual, expected):
            raise GshhgL3HierarchyPreregistrationError(
                f"Deferred freeze decision {key} changed."
            )
    for key, expected in EXPECTED_DECISION_TOP_LEVEL_LOCKS.items():
        if not _strict_equal(payload.get(key), expected):
            raise GshhgL3HierarchyPreregistrationError(
                f"Deferred freeze decision {key} changed."
            )
    if not _strict_equal(payload.get("locks"), EXPECTED_CLOSED_LOCKS):
        raise GshhgL3HierarchyPreregistrationError(
            "Deferred freeze-decision locks changed."
        )

    v2 = _require_table(
        _require_table(payload.get("prerequisites"), label="decision.prerequisites").get(
            "gshhg_v2_pilot"
        ),
        label="decision.prerequisites.gshhg_v2_pilot",
    )
    if (
        v2.get("file_sha256") != settings["expected_gshhg_v2_file_sha256"]
        or v2.get("commit_sha256")
        != settings["expected_gshhg_v2_commit_sha256"]
    ):
        raise GshhgL3HierarchyPreregistrationError(
            "The completed V2 pilot identity changed."
        )
    next_gate = _require_table(payload.get("next_gate"), label="decision.next_gate")
    if (
        next_gate.get("stage_id")
        != "preregister_target_blind_gshhg_l3_hierarchy_audit"
        or next_gate.get("selected_l2_source_ids") != EXPECTED_SELECTED_L2_IDS
        or next_gate.get("include_direct_l3_descendants") is not True
        or next_gate.get("exclude_l4_pond_shores") is not True
        or next_gate.get("selection_may_use_city_target_or_distance") is not False
    ):
        raise GshhgL3HierarchyPreregistrationError(
            "The deferred decision L3 gate changed."
        )
    access = _require_table(
        payload.get("access_contract"), label="decision.access_contract"
    )
    if (
        access.get("gshhg_archive_members_opened_by_decision_program") is not False
        or access.get("gshhg_l3_geometry_opened") is not False
        or access.get("eligible_land_grid_opened") is not False
    ):
        raise GshhgL3HierarchyPreregistrationError(
            "The deferred decision no-access record changed."
        )
    return payload, {
        "path": settings["path"],
        "file_sha256": file_sha,
        "bytes": path.stat().st_size,
        "commit_sha256": payload["commit_sha256"],
        "state": payload["state"],
        "outcome": payload["outcome"],
        "gshhg_v2_pilot": v2,
    }


def _validate_source_identity(
    config_source: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    source = _require_table(
        decision.get("source_candidate"), label="decision.source_candidate"
    )
    expected_pairs = {
        "id": config_source["source_id"],
        "dataset": config_source["dataset"],
        "version": config_source["version"],
        "dataset_release_date": config_source["dataset_release_date"],
        "tag_commit": config_source["tag_commit"],
        "path": config_source["archive_path"],
        "expected_bytes": config_source["expected_archive_bytes"],
        "observed_bytes": config_source["expected_archive_bytes"],
        "expected_sha256": config_source["expected_archive_sha256"],
        "observed_sha256": config_source["expected_archive_sha256"],
        "published_md5": config_source["published_archive_md5"],
        "release_url": config_source["release_url"],
        "hierarchy_reference": config_source["hierarchy_reference"],
        "readme_reference": config_source["readme_reference"],
    }
    for key, expected in expected_pairs.items():
        if not _strict_equal(source.get(key), expected):
            raise GshhgL3HierarchyPreregistrationError(
                f"Deferred source identity changed at {key}."
            )


def _build_payload(
    project_root: Path,
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    plan = load_multicity_plan(project_root / "configs/multicity/experiment.toml")
    _require_plan_locks_closed(plan.raw)
    if (
        plan.experiment_id != EXPECTED_EXPERIMENT_ID
        or plan.semantic_sha256 != EXPECTED_EXPERIMENT_SEMANTIC_SHA256
    ):
        raise GshhgL3HierarchyPreregistrationError(
            "Experiment identity changed before L3 preregistration."
        )

    prerequisites = _require_table(config["prerequisites"], label="prerequisites")
    plan_payload, plan_record = _authenticate_plan_readiness(
        project_root,
        _require_table(
            prerequisites.get("plan_readiness"),
            label="prerequisites.plan_readiness",
        ),
    )
    decision_payload, decision_record = _authenticate_freeze_decision(
        project_root,
        _require_table(
            prerequisites.get("freeze_decision"),
            label="prerequisites.freeze_decision",
        ),
        expected_experiment_id=plan.experiment_id,
        expected_plan_sha=plan.semantic_sha256,
    )
    planning_decision = _require_table(
        plan_payload.get("portable_water_distance_freeze_decision"),
        label="plan.portable_water_distance_freeze_decision",
    )
    if (
        planning_decision.get("commit_sha256")
        != decision_payload.get("commit_sha256")
        or planning_decision.get("file_sha256") != decision_record["file_sha256"]
    ):
        raise GshhgL3HierarchyPreregistrationError(
            "Plan readiness and deferred decision identities disagree."
        )
    _validate_source_identity(config["source"], decision_payload)

    code_sha, code_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_payload["relative_paths"] = list(CODE_PATHS)
    code_payload["sha256"] = code_sha

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "preregistration_id": config["preregistration"]["preregistration_id"],
        "preregistration_date": config["preregistration"]["preregistration_date"],
        "scope": config["preregistration"]["scope"],
        "base_repository_commit": config["preregistration"][
            "base_repository_commit"
        ],
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "prerequisites": {
            "plan_readiness": plan_record,
            "freeze_decision": decision_record,
        },
        "source_identity_inherited_without_archive_access": config["source"],
        "license_record": config["license_record"],
        "unchanged_v2_contract": config["unchanged_v2_contract"],
        "hierarchy_contract": config["hierarchy_contract"],
        "structural_audit": config["structural_audit"],
        "probe_rule": config["probe_rule"],
        "numerical_audit": {
            **config["numerical_audit"],
            "diagnostic_points": config["diagnostic_points"],
        },
        "phase_order_and_failure": config["phase_order_and_failure"],
        "outputs": config["outputs"],
        "locks": config["locks"],
        "access_contract": config["access_contract"],
        "next_gate": config["next_gate"],
        "preregistration_config": {
            "path": config_path.relative_to(project_root).as_posix(),
            "bytes": config_path.stat().st_size,
            "sha256": sha256_file(config_path),
        },
        "experiment_config_files": plan.file_records,
        "code_runtime": code_payload,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def preregister_gshhg_l3_hierarchy_audit(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_path: str | Path = DEFAULT_MANIFEST,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the append-only tracked-input preregistration."""

    project_root, resolved_config, config = _read_config(config_path)
    payload = _build_payload(project_root, resolved_config, config)
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = project_root / destination

    if destination.exists():
        committed, _ = _read_committed_json(destination)
        if not _strict_equal(committed, payload):
            raise GshhgL3HierarchyPreregistrationError(
                "L3 preregistration manifest already exists with different bytes."
            )
        return committed
    if not write:
        raise FileNotFoundError(destination)
    atomic_json(payload, destination)
    return payload
