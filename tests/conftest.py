"""Separate public code tests from opt-in, workstation evidence audits.

The explicit inventory below is not a failure filter. These tests authenticate
real, ignored research outputs; their assertions still run unchanged when the
operator requests --run-local-evidence. Synthetic guardrail tests stay enabled.
"""

from __future__ import annotations

import pytest

# Module-wide entries contain only tests requiring the same local evidence.
LOCAL_EVIDENCE_MODULES = {
    "test_multicity_evaluation_protocol_lock.py": "completed portable predictor products",
    "test_multicity_model_fit_authorization.py": "completed portable predictor products",
    "test_multicity_source_target_authorization.py": "completed portable predictor products",
    "test_multicity_m3_development_protocol_lock.py": "historical evaluation and QA evidence",
    "test_multicity_m3_source_metadata_inventory_v1.py": "previous local overpass inventories",
}

# Function-level entries keep each module's synthetic tests in public CI.
LOCAL_EVIDENCE_TESTS = {
    "test_multicity_external_evaluation.py": {
        "test_spatial_blocks_authenticate_against_real_protocol_lock",
    },
    "test_multicity_m3_blind_predictor_daymet_acquisition_v1.py": {
        "test_daymet_authorization_is_narrow",
    },
    "test_multicity_m3_blind_predictor_metadata_endpoint_repair_v1.py": {
        "test_endpoint_repair_is_narrow_and_parent_bound",
    },
    "test_multicity_m3_blind_predictor_metadata_geometry_repair_v2.py": {
        "test_geometry_repair_is_hash_only_and_parent_bound",
    },
    "test_multicity_m3_blind_predictor_metadata_v1.py": {
        "test_metadata_authorization_is_value_blind",
    },
    "test_multicity_m3_blind_predictor_sentinel_inventory_v1.py": {
        "test_sentinel_inventory_authorization_is_raster_blind",
    },
    "test_multicity_m3_development_protocol_lock.py": {
        "test_build_is_value_blind_and_freezes_candidates_not_winner",
        "test_lock_commit_is_canonical",
    },
    "test_multicity_m3_source_joint_nested_loso_v1.py": {
        "test_readiness_authenticates_metadata_without_opening_or_statting_any_parquet",
        "test_source_and_runner_keep_value_reads_behind_formal_authentication",
    },
    "test_multicity_m3_source_acquisition_amendment.py": {
        "test_preflight_fails_fast_before_current_inventory_prefetch",
        "test_amendment_freezes_fixed_nonadaptive_source_expansion",
        "test_amendment_grants_no_execution_or_value_permission",
        "test_preview_commit_is_canonical_and_matches_formal_manifest_when_present",
    },
    "test_multicity_m3_source_metadata_inventory_v1.py": {
        "test_preview_is_exact_metadata_only_authorization",
        "test_formal_outputs_are_committed_when_present",
    },
    "test_multicity_m3_source_predictor_extension_v1.py": {
        "test_live_authorization_preview_is_metadata_only_and_exact",
    },
    "test_multicity_portable_water_distance_freeze.py": {
        "test_deferred_decision_generates_and_reauthenticates",
        "test_decision_manifest_tampering_fails_closed",
        "test_decision_refuses_to_overwrite_different_valid_manifest",
    },
    "test_multicity_water_distance_review.py": {
        "test_review_authenticates_existing_source_without_unlocking_computation",
        "test_review_manifest_tampering_fails_closed",
    },
    "test_phase2_registry.py": {
        "test_combined_registry_has_exact_order_counts_and_contract",
        "test_sentinel_names_units_offsets_and_source_are_exact",
        "test_model_registry_has_no_thermal_target_identifier_or_dynamic_audit_fields",
        "test_static_row_shuffle_is_canonicalized",
        "test_tampered_static_registry_fails_closed",
        "test_build_writes_output_then_valid_commit_marker",
        "test_failed_marker_write_leaves_no_false_commit",
    },
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-local-evidence",
        action="store_true",
        help="Also run research evidence audits; requires existing local data and permissions.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "local_evidence: authenticates ignored workstation research products"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        module = item.path.name
        name = getattr(item, "originalname", item.name)
        if module in LOCAL_EVIDENCE_MODULES or name in LOCAL_EVIDENCE_TESTS.get(module, set()):
            item.add_marker(pytest.mark.local_evidence)
        if item.get_closest_marker("local_evidence") and not config.getoption(
            "--run-local-evidence"
        ):
            item.add_marker(pytest.mark.skip(
                reason="Local research evidence audit: opt in with --run-local-evidence."
            ))
