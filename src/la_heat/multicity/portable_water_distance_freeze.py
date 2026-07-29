"""Authenticate the target-blind portable water-distance freeze decision.

This stage records a deferred decision.  It reads only planning configuration,
three prerequisite manifests, and the fixed GSHHG archive bytes for hashing.
It does not open an archive member, geometry, eligible-land support, target,
predictor, model, prediction, or final-evaluation output.
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
ALGORITHM_VERSION: Final = "portable-water-distance-freeze-decision-v1"
COMPLETE_STATE: Final = "decision_complete_freeze_deferred"
OUTCOME: Final = "deferred_pending_gshhg_l3_hierarchy_contract"
EXPECTED_PLAN_SEMANTIC_SHA256: Final = (
    "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
)
DEFAULT_CONFIG: Final = Path(
    "configs/multicity/portable_water_distance_freeze_decision_v1.toml"
)
DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION.json"
)
EXPECTED_SELECTED_L2_IDS: Final = [180507, 180515, 180517]
EXPECTED_PROVISIONAL_NAMES: Final = [
    "gshhg_ocean_great_lakes_shoreline_distance_mean_km",
    "gshhg_ocean_great_lakes_shoreline_distance_p10_km",
]
EXPECTED_PREREQUISITES: Final = {
    "water_review": {
        "path": (
            "manifests/multicity/reviews/portable_water_distance/"
            "WATER_DISTANCE_REVIEW.json"
        ),
        "expected_file_sha256": (
            "56aeb8ced370f4648b5256875223302ee189d3d5fa452fbc346e4d8dd80b7e56"
        ),
        "expected_commit_sha256": (
            "1c3b1738b7625a74f446b2c61e8efc84255dbe99fe745a2fa101d4decfcda6a5"
        ),
        "expected_state": "review_complete_source_not_frozen",
        "expected_outcome": (
            "conditional_census_benchmark_pending_global_geometry_pilot"
        ),
    },
    "gshhg_v1_failure": {
        "path": (
            "manifests/multicity/reviews/portable_water_distance/"
            "GSHHG_GEOMETRY_PILOT_V1_FAILURE.json"
        ),
        "expected_file_sha256": (
            "1df7191211a59eaf3144c77646a60bfe7238458417f29cb1c2991af92a4270d0"
        ),
        "expected_commit_sha256": (
            "e5d8c85780752bd7f8be46c20c1a34e7f66db7d15ce438b81f8010705861f03c"
        ),
        "expected_state": "geometry_pilot_v1_failed_before_distance",
    },
    "gshhg_v2_pilot": {
        "path": (
            "manifests/multicity/reviews/portable_water_distance/"
            "GSHHG_GEOMETRY_PILOT.json"
        ),
        "expected_file_sha256": (
            "71d68e35a67d82d5e8d7746cc9732d9cd1b8d880ed126e1c2af46cc72615bad1"
        ),
        "expected_commit_sha256": (
            "e14cbd4763489fbacdec3ac45348226e2ae677073aa592aabf9bc0e3d8256735"
        ),
        "expected_state": "geometry_pilot_complete_source_not_frozen",
    },
}
EXPECTED_CURRENT_CONTRACT: Final = (
    "GSHHG 2.3.7 full-resolution L1 exteriors plus L2 source IDs 180507, "
    "180515, and 180517 exteriors; L3 island shores excluded"
)
EXPECTED_DECISION_REASON: Final = (
    "The four fixed-point pilot did not test land on Great Lakes islands, so "
    "excluding L3 cannot yet support the natural-language ocean-or-Great-Lakes "
    "shoreline claim."
)
EXPECTED_CANDIDATE_ALGORITHM: Final = (
    "L1 ocean exteriors plus the three selected L2 connected-water exteriors "
    "plus every directly parented L3 island exterior"
)
EXPECTED_APPLICABILITY: Final = (
    "historical cartographic covariate for the four predeclared study cities, "
    "conditional on their later authenticated and frozen eligible-land supports"
)
EXPECTED_COMPLIANCE_BASELINE: Final = (
    "LGPL-3.0-only common denominator pending legal review"
)
EXPECTED_ACCESS_CONTRACT: Final = {
    "decision_program_network_requests": 0,
    "official_documentation_reviewed_separately": True,
    "prerequisite_manifest_bytes_read": True,
    "gshhg_archive_bytes_authenticated": True,
    "gshhg_archive_members_opened_by_decision_program": False,
    "gshhg_l3_geometry_opened": False,
    "eligible_land_grid_opened": False,
    "distance_feature_surface_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "external_lst_values_read": False,
    "external_target_files_opened": False,
    "final_evaluation_outputs_opened": False,
}
EXPECTED_LOCKS: Final = {
    "source_lock_created": False,
    "algorithm_lock_created": False,
    "feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
}
CODE_PATHS: Final = (
    "configs/multicity/experiment.toml",
    "configs/multicity/portable_water_distance_freeze_decision_v1.toml",
    "configs/multicity/gshhg_geometry_pilot_v1.toml",
    "configs/multicity/gshhg_geometry_pilot_v2.toml",
    "configs/multicity/water_distance_review_v1.toml",
    "scripts/audit_multicity_portable_water_distance_freeze.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/gshhg_geometry_pilot.py",
    "src/la_heat/multicity/portable_water_distance_freeze.py",
    "src/la_heat/multicity/water_distance_review.py",
    "src/la_heat/provenance.py",
)


class PortableWaterDistanceFreezeError(ValueError):
    """Raised when the deferred freeze decision cannot authenticate."""


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


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    observed = set(value)
    if observed != expected:
        raise PortableWaterDistanceFreezeError(
            f"{label} keys changed; missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}."
        )


def _require_table(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortableWaterDistanceFreezeError(f"{label} must be a table.")
    return value


def _committed_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableWaterDistanceFreezeError(
            f"Cannot read prerequisite manifest: {path}"
        ) from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Prerequisite changed while being read: {path}")
    if not isinstance(payload, dict):
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite manifest must be an object: {path}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite internal commit is invalid: {path}"
        )
    return payload, before


def _read_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise PortableWaterDistanceFreezeError("Decision config must be a table.")
    _require_exact_keys(
        payload,
        {
            "decision",
            "prerequisites",
            "source_candidate",
            "license_record",
            "scientific_decision",
            "next_gate",
            "locks",
            "access_contract",
        },
        label="decision config",
    )

    decision = _require_table(payload["decision"], label="decision")
    expected_decision = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "decision_id": "portable_water_distance_freeze_decision_v1",
        "decision_date": "2026-07-29",
        "scope": "target-blind source-and-algorithm freeze decision",
        "state": COMPLETE_STATE,
        "outcome": OUTCOME,
        "expected_plan_semantic_sha256": EXPECTED_PLAN_SEMANTIC_SHA256,
    }
    if not _strict_equal(decision, expected_decision):
        raise PortableWaterDistanceFreezeError(
            "Decision identity or deferred outcome changed."
        )

    prerequisites = _require_table(payload["prerequisites"], label="prerequisites")
    _require_exact_keys(
        prerequisites,
        {"water_review", "gshhg_v1_failure", "gshhg_v2_pilot"},
        label="prerequisites",
    )
    for name, record in prerequisites.items():
        _require_table(record, label=f"prerequisites.{name}")
    if not _strict_equal(prerequisites, EXPECTED_PREREQUISITES):
        raise PortableWaterDistanceFreezeError(
            "Prerequisite paths, hashes, commits, or states changed."
        )

    source = _require_table(payload["source_candidate"], label="source_candidate")
    _require_exact_keys(
        source,
        {
            "id",
            "dataset",
            "version",
            "dataset_release_date",
            "tag_commit",
            "github_release_published_at",
            "path",
            "expected_bytes",
            "expected_sha256",
            "published_md5",
            "release_url",
            "ncei_accession_url",
            "ncei_accession",
            "ncei_metadata_revision",
            "ncei_decommissioned",
            "ncei_further_updates_expected",
            "hierarchy_reference",
            "readme_reference",
            "status",
            "applicability_if_later_frozen",
            "nationwide_or_all_city_claim_allowed",
            "modern_realtime_or_30m_truth_claim_allowed",
            "future_city_requires_new_applicability_audit",
        },
        label="source_candidate",
    )
    expected_source_values = {
        "id": "gshhg_2_3_7_full_shapefile",
        "dataset": "Global Self-consistent Hierarchical High-resolution Geography",
        "version": "2.3.7",
        "dataset_release_date": "2017-06-15",
        "tag_commit": "5f0e530165b34904128575a18de0d57a450b2227",
        "github_release_published_at": "2020-02-15T18:28:51Z",
        "path": "data/raw/multicity/water_distance/gshhg-shp-2.3.7.zip",
        "expected_bytes": 149157845,
        "expected_sha256": (
            "8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41"
        ),
        "published_md5": "cb82015f8533f9611b4adba2c404ba44",
        "release_url": (
            "https://github.com/GenericMappingTools/gshhg-gmt/releases/tag/2.3.7"
        ),
        "ncei_accession_url": (
            "https://www.ncei.noaa.gov/archive/archive-management-system/OAS/"
            "bin/prd/jquery/accession/details/304143"
        ),
        "ncei_accession": "0304143",
        "ncei_metadata_revision": 7,
        "ncei_decommissioned": "2025-05",
        "ncei_further_updates_expected": False,
        "hierarchy_reference": (
            "https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/"
            "2.3.7/SHAPEFILES.TXT"
        ),
        "readme_reference": (
            "https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/"
            "2.3.7/README.md"
        ),
        "status": "candidate_retained_source_not_frozen",
        "applicability_if_later_frozen": EXPECTED_APPLICABILITY,
        "nationwide_or_all_city_claim_allowed": False,
        "modern_realtime_or_30m_truth_claim_allowed": False,
        "future_city_requires_new_applicability_audit": True,
    }
    for key, expected in expected_source_values.items():
        if not _strict_equal(source.get(key), expected):
            raise PortableWaterDistanceFreezeError(
                f"source_candidate.{key} changed."
            )
    license_record = _require_table(
        payload["license_record"], label="license_record"
    )
    _require_exact_keys(
        license_record,
        {
            "archive_license_member",
            "archive_license_member_sha256",
            "archive_copying_member",
            "archive_copying_member_sha256",
            "archive_notice_expression",
            "historical_readme_statement",
            "github_repository_ui_label",
            "compliance_baseline_for_future_redistribution",
            "gshhg_name_advertising_without_permission_allowed",
            "copyright_and_permission_notice_must_be_preserved",
            "as_is_no_suitability_or_support_notice_recorded",
            "archive_includes_full_gplv3_text",
            "future_redistribution_requires_official_gplv3_copy",
            "project_policy_authorizes_archive_or_modified_geometry_redistribution",
            "legal_review_completed",
            "license_reference",
        },
        label="license_record",
    )
    expected_license_controls = {
        "archive_license_member": "LICENSE.TXT",
        "archive_license_member_sha256": (
            "dc53a4833bf9121360a5286917683f041439327353ffe0231206c6b746005b81"
        ),
        "archive_copying_member": "COPYING.LESSERv3",
        "archive_copying_member_sha256": (
            "ea7d049c7705dc13afc202dd18e1827f3484f8212fd3fa7b82fc4a0c363432c9"
        ),
        "archive_notice_expression": "LGPL-3.0-or-later",
        "historical_readme_statement": "version 3 or any earlier version",
        "github_repository_ui_label": "LGPL-3.0",
        "compliance_baseline_for_future_redistribution": (
            EXPECTED_COMPLIANCE_BASELINE
        ),
        "gshhg_name_advertising_without_permission_allowed": False,
        "copyright_and_permission_notice_must_be_preserved": True,
        "as_is_no_suitability_or_support_notice_recorded": True,
        "archive_includes_full_gplv3_text": False,
        "future_redistribution_requires_official_gplv3_copy": True,
        "project_policy_authorizes_archive_or_modified_geometry_redistribution": (
            False
        ),
        "legal_review_completed": False,
        "license_reference": "https://www.gnu.org/licenses/lgpl-3.0.html",
    }
    for key, expected in expected_license_controls.items():
        if not _strict_equal(license_record.get(key), expected):
            raise PortableWaterDistanceFreezeError(
                f"license_record.{key} changed."
            )

    science = _require_table(
        payload["scientific_decision"], label="scientific_decision"
    )
    _require_exact_keys(
        science,
        {
            "current_contract",
            "current_contract_immediate_freeze",
            "reason",
            "l3_hierarchy_gap_resolved",
            "four_city_eligible_land_support_opened",
            "broad_feature_names_frozen",
            "phase1_feature_alias_allowed",
            "phase1_outputs_immutable",
            "census_fallback_silent_substitution_allowed",
            "candidate_source_rejected",
        },
        label="scientific_decision",
    )
    expected_science_controls = {
        "current_contract": EXPECTED_CURRENT_CONTRACT,
        "current_contract_immediate_freeze": "rejected",
        "reason": EXPECTED_DECISION_REASON,
        "l3_hierarchy_gap_resolved": False,
        "four_city_eligible_land_support_opened": False,
        "broad_feature_names_frozen": False,
        "phase1_feature_alias_allowed": False,
        "phase1_outputs_immutable": True,
        "census_fallback_silent_substitution_allowed": False,
        "candidate_source_rejected": False,
    }
    for key, expected in expected_science_controls.items():
        if not _strict_equal(science.get(key), expected):
            raise PortableWaterDistanceFreezeError(
                f"scientific_decision.{key} changed."
            )

    gate = _require_table(payload["next_gate"], label="next_gate")
    _require_exact_keys(
        gate,
        {
            "stage_id",
            "preregistration_must_be_committed_before_l3_geometry_read",
            "source_only",
            "selected_l2_source_ids",
            "candidate_algorithm",
            "include_direct_l3_descendants",
            "exclude_l4_pond_shores",
            "selection_may_use_city_target_or_distance",
            "require_l3_member_hash_schema_crs_and_semantic_audit",
            "require_direct_parent_id_audit",
            "require_real_l3_component_probe",
            "require_numerical_gate_replay",
            "require_zero_target_or_qa_access",
            "require_zero_eligible_land_grid_access",
            "require_zero_feature_surface_or_tract_aggregation",
            "require_zero_predictor_model_prediction_or_final_evaluation_access",
            "audit_pass_automatically_freezes_source_or_algorithm",
            "next_decision_after_pass",
            "provisional_feature_names",
        },
        label="next_gate",
    )
    expected_gate_controls = {
        "stage_id": "preregister_target_blind_gshhg_l3_hierarchy_audit",
        "preregistration_must_be_committed_before_l3_geometry_read": True,
        "source_only": True,
        "selected_l2_source_ids": EXPECTED_SELECTED_L2_IDS,
        "candidate_algorithm": EXPECTED_CANDIDATE_ALGORITHM,
        "include_direct_l3_descendants": True,
        "exclude_l4_pond_shores": True,
        "selection_may_use_city_target_or_distance": False,
        "require_l3_member_hash_schema_crs_and_semantic_audit": True,
        "require_direct_parent_id_audit": True,
        "require_real_l3_component_probe": True,
        "require_numerical_gate_replay": True,
        "require_zero_target_or_qa_access": True,
        "require_zero_eligible_land_grid_access": True,
        "require_zero_feature_surface_or_tract_aggregation": True,
        "require_zero_predictor_model_prediction_or_final_evaluation_access": (
            True
        ),
        "audit_pass_automatically_freezes_source_or_algorithm": False,
        "next_decision_after_pass": (
            "separate_portable_water_distance_source_and_algorithm_freeze_decision"
        ),
        "provisional_feature_names": EXPECTED_PROVISIONAL_NAMES,
    }
    for key, expected in expected_gate_controls.items():
        if not _strict_equal(gate.get(key), expected):
            raise PortableWaterDistanceFreezeError(f"next_gate.{key} changed.")

    if not _strict_equal(payload["locks"], EXPECTED_LOCKS):
        raise PortableWaterDistanceFreezeError(
            "Every decision-stage lock must remain closed."
        )
    if not _strict_equal(payload["access_contract"], EXPECTED_ACCESS_CONTRACT):
        raise PortableWaterDistanceFreezeError(
            "The no-target/no-predictor access contract changed."
        )
    return config_path, payload


def _require_plan_locks_closed(plan_raw: dict[str, Any]) -> None:
    locks = plan_raw["locks"]
    expected_false = (
        "protocol_locked",
        "external_targets_unlocked",
        "external_target_values_read",
        "external_prediction_commit_exists",
        "allow_predictor_construction",
        "allow_model_fitting",
        "allow_external_target_access",
    )
    for name in expected_false:
        if locks[name] is not False:
            raise PortableWaterDistanceFreezeError(
                f"Experiment lock {name} must remain false."
            )
    predictors = plan_raw["predictors"]
    if predictors["portable_water_distance_source_frozen"] is not False:
        raise PortableWaterDistanceFreezeError(
            "Experiment source must remain unfrozen after a deferred decision."
        )
    if plan_raw["sources"]["portable_water_distance_source"] != "NOT_YET_FROZEN":
        raise PortableWaterDistanceFreezeError(
            "Experiment source identity cannot change during a deferred decision."
        )


def _authenticate_prerequisite(
    project_root: Path,
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = project_root / str(settings["path"])
    payload, file_sha = _committed_json(path)
    if file_sha != settings["expected_file_sha256"]:
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite file hash changed: {settings['path']}"
        )
    if payload.get("commit_sha256") != settings["expected_commit_sha256"]:
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite commit changed: {settings['path']}"
        )
    if payload.get("state") != settings["expected_state"]:
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite state changed: {settings['path']}"
        )
    if "expected_outcome" in settings and (
        payload.get("review_outcome") != settings["expected_outcome"]
    ):
        raise PortableWaterDistanceFreezeError(
            f"Prerequisite outcome changed: {settings['path']}"
        )
    return payload, {
        "path": settings["path"],
        "file_sha256": file_sha,
        "bytes": path.stat().st_size,
        "commit_sha256": payload["commit_sha256"],
        "state": payload["state"],
    }


def _validate_pilot_closure_gap(pilot: dict[str, Any]) -> None:
    decision = _require_table(pilot.get("decision"), label="pilot.decision")
    expected_decision = {
        "source_frozen": False,
        "algorithm_frozen": False,
        "gshhg_pilot_passed_all_v2_gates": True,
        "next_safe_stage": (
            "portable_water_distance_source_and_algorithm_freeze_decision"
        ),
    }
    if not _strict_equal(decision, expected_decision):
        raise PortableWaterDistanceFreezeError(
            "GSHHG pilot decision no longer has the expected non-frozen state."
        )
    layers = _require_table(pilot.get("source_layers"), label="pilot.source_layers")
    identity = _require_table(
        layers.get("great_lakes_identity"),
        label="pilot.source_layers.great_lakes_identity",
    )
    if identity.get("l3_island_shores_included") is not False:
        raise PortableWaterDistanceFreezeError(
            "The deferred decision requires the unresolved L3 exclusion."
        )
    observed_ids = [
        int(record["source_id"])
        for record in identity.get("source_polygons", [])
        if isinstance(record, dict) and "source_id" in record
    ]
    if observed_ids != EXPECTED_SELECTED_L2_IDS:
        raise PortableWaterDistanceFreezeError(
            "The three selected Great Lakes L2 source IDs changed."
        )
    gates = _require_table(pilot.get("numerical_gates"), label="pilot.numerical_gates")
    for name in (
        "strtree_bruteforce_all_passed",
        "radius_expansion_all_passed",
        "source_order_all_passed",
        "projected_geodesic_all_passed",
    ):
        if gates.get(name) is not True:
            raise PortableWaterDistanceFreezeError(
                f"GSHHG prerequisite gate did not pass: {name}"
            )
    for name in ("line_chunk_invariance", "worker_and_query_chunk_invariance"):
        record = _require_table(gates.get(name), label=f"pilot.numerical_gates.{name}")
        if record.get("all_runs_invariant") is not True:
            raise PortableWaterDistanceFreezeError(
                f"GSHHG prerequisite invariance gate did not pass: {name}"
            )


def _build_payload(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    project_root = config_path.parents[2]
    plan = load_multicity_plan(project_root / "configs/multicity/experiment.toml")
    _require_plan_locks_closed(plan.raw)
    if plan.semantic_sha256 != EXPECTED_PLAN_SEMANTIC_SHA256:
        raise PortableWaterDistanceFreezeError(
            "Experiment semantic identity changed after the prerequisite evidence."
        )

    prerequisite_records: dict[str, Any] = {}
    prerequisite_payloads: dict[str, dict[str, Any]] = {}
    for name, settings in config["prerequisites"].items():
        prerequisite_payloads[name], prerequisite_records[name] = (
            _authenticate_prerequisite(project_root, settings)
        )
    for name in ("water_review", "gshhg_v2_pilot"):
        prerequisite = prerequisite_payloads[name]
        if (
            prerequisite.get("experiment_id") != plan.experiment_id
            or prerequisite.get("plan_semantic_sha256") != plan.semantic_sha256
        ):
            raise PortableWaterDistanceFreezeError(
                f"Prerequisite {name} belongs to a different experiment plan."
            )
    _validate_pilot_closure_gap(prerequisite_payloads["gshhg_v2_pilot"])

    source = config["source_candidate"]
    archive_path = project_root / source["path"]
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if archive_path.stat().st_size != source["expected_bytes"]:
        raise PortableWaterDistanceFreezeError("GSHHG archive byte count changed.")
    archive_sha = sha256_file(archive_path)
    if archive_sha != source["expected_sha256"]:
        raise PortableWaterDistanceFreezeError("GSHHG archive hash changed.")

    pilot_source = prerequisite_payloads["gshhg_v2_pilot"].get("source_archive")
    if not isinstance(pilot_source, dict):
        raise PortableWaterDistanceFreezeError(
            "GSHHG pilot source archive record is missing."
        )
    for key, expected in (
        ("version", source["version"]),
        ("release_date", source["dataset_release_date"]),
        ("bytes", source["expected_bytes"]),
        ("sha256", source["expected_sha256"]),
        ("published_md5", source["published_md5"]),
    ):
        if not _strict_equal(pilot_source.get(key), expected):
            raise PortableWaterDistanceFreezeError(
                f"GSHHG pilot source_archive.{key} changed."
            )
    required_members = pilot_source.get("required_member_sha256")
    if not isinstance(required_members, dict):
        raise PortableWaterDistanceFreezeError(
            "GSHHG pilot required-member hashes are missing."
        )
    license_record = config["license_record"]
    for member_key, hash_key in (
        ("archive_license_member", "archive_license_member_sha256"),
        ("archive_copying_member", "archive_copying_member_sha256"),
    ):
        if required_members.get(license_record[member_key]) != license_record[hash_key]:
            raise PortableWaterDistanceFreezeError(
                f"Recorded license member hash changed: {license_record[member_key]}"
            )

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
        "decision_id": config["decision"]["decision_id"],
        "decision_date": config["decision"]["decision_date"],
        "decision_scope": config["decision"]["scope"],
        "outcome": OUTCOME,
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "prerequisites": prerequisite_records,
        "source_candidate": {
            **source,
            "observed_bytes": archive_path.stat().st_size,
            "observed_sha256": archive_sha,
        },
        "license_record": license_record,
        "scientific_decision": config["scientific_decision"],
        "next_gate": config["next_gate"],
        "locks": config["locks"],
        "access_contract": config["access_contract"],
        "decision_config": {
            "path": config_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(config_path),
            "bytes": config_path.stat().st_size,
        },
        "code_runtime": code_payload,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def audit_portable_water_distance_freeze_decision(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_path: str | Path = DEFAULT_MANIFEST,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the append-only deferred-decision manifest."""

    resolved_config, config = _read_config(config_path)
    project_root = resolved_config.parents[2]
    payload = _build_payload(resolved_config, config)
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = project_root / destination

    if destination.exists():
        committed, _ = _committed_json(destination)
        if not _strict_equal(committed, payload):
            raise PortableWaterDistanceFreezeError(
                "Decision manifest already exists with different bytes."
            )
        return committed
    if not write:
        raise FileNotFoundError(destination)
    atomic_json(payload, destination)
    return payload
