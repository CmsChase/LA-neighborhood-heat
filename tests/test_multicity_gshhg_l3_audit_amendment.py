from __future__ import annotations

import copy
import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest

import la_heat.multicity.gshhg_l3_hierarchy_audit as l3_audit

OLD_HASH = "858c762462d6573e3f2ce25356ba1e193c1ca47a1f1ecc327710a49ff1fe014c"
CORRECTED_HASH = (
    "858c762462d6573e3f2ce25356ba1e193c1ca47a1f1ecc327710a49ff1fe014a"
)
L2_HASHES = {
    "180507": "83d5ba2f0065ceb7851aaafe1cb6cb11745a205e09509e211a0309409022b39a",
    "180515": OLD_HASH,
    "180517": "b99eceddc5b9edc2fea390e33e008923a877f3b2fbbff3dafb30aea7ca3d23f7",
}
AMENDMENT_RELATIVE = Path(
    "configs/multicity/gshhg_l3_hierarchy_audit_amendment_v2.toml"
)
BASE_CONFIG_RELATIVE = Path(
    "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml"
)
PREREGISTRATION_RELATIVE = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
)
PILOT_RELATIVE = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_GEOMETRY_PILOT.json"
)
V1_FAILURE_RELATIVE = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_V1_FAILURE.json"
)
SUCCESS_RELATIVE = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT.json"
)
V2_FAILURE_RELATIVE = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_V2_FAILURE.json"
)
TABLE_RELATIVE = Path(
    "data/interim/multicity/water_distance/"
    "gshhg_l3_hierarchy_audit/diagnostic_distances.csv"
)

AMENDMENT_FILE_SHA256 = (
    "c60c2d699e94bca832a78b4959db9a5333b2aa3ae37bfdd72d9c0eb6f37ff127"
)
BASE_CONFIG_SHA256 = (
    "6fcac13640a8914543d7d057e19cd18ec7ddea74a8d3f50406f4c5dd81e2c1cd"
)
PREREGISTRATION_FILE_SHA256 = (
    "ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f"
)
PREREGISTRATION_COMMIT_SHA256 = (
    "7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7"
)
V1_FAILURE_FILE_SHA256 = (
    "b5eb32e3de1702250e36a7eb81b2ea0c78551930a7f92abe5278d21c05a0ea9e"
)
V1_FAILURE_COMMIT_SHA256 = (
    "e5b8e1e242276bcb530990ee070739f84e48177c431e556cfebb4819c92ea067"
)
V1_FAILURE_GIT_COMMIT = "fbf20ed7a601af8e9f77ad768f1267b8a6503a0d"
V1_FAILURE_BLOB_SHA1 = "aca5a2b7231bd1d0ffb660ce0554034c3dd014ba"
V1_RUN_HEAD = "ab51a9506d77b7ac0efcdfb97e494c665cd80e5b"
PILOT_FILE_SHA256 = (
    "71d68e35a67d82d5e8d7746cc9732d9cd1b8d880ed126e1c2af46cc72615bad1"
)
PILOT_COMMIT_SHA256 = (
    "e14cbd4763489fbacdec3ac45348226e2ae677073aa592aabf9bc0e3d8256735"
)


@dataclass
class AmendmentHarness:
    root: Path
    amendment_path: Path
    base_config_path: Path
    amendment: dict[str, Any]
    base_config: dict[str, Any]
    preregistration: dict[str, Any]
    pilot: dict[str, Any]
    v1_failure: dict[str, Any]
    json_reader: Mock


def _base_config() -> dict[str, Any]:
    return {
        "source": {
            "archive_path": (
                "data/raw/multicity/water_distance/gshhg-shp-2.3.7.zip"
            ),
            "expected_archive_bytes": 149_157_845,
            "expected_archive_sha256": (
                "8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41"
            ),
        },
        "unchanged_v2_contract": {
            "amendment_config_path": (
                "configs/multicity/gshhg_geometry_pilot_v2.toml"
            ),
            "amendment_config_sha256": (
                "c0f41f77d0c87a5ca09de81c2ec0ca2ae489633ac3f785648fbeb5f9f67e67ff"
            ),
            "selected_l2_source_ids": [180507, 180515, 180517],
            "selected_l2_normalized_wkb_sha256": dict(L2_HASHES),
            "l1_ocean_exteriors_only": True,
            "selected_l2_exteriors_only": True,
            "polygon_interior_rings_excluded": True,
        },
        "hierarchy_contract": {
            "selected_l2_source_ids": [180507, 180515, 180517],
            "include_every_direct_l3_descendant": True,
            "selected_l3_exteriors_only": True,
            "l4_members_may_be_opened": False,
            "l4_geometry_included": False,
        },
        "probe_rule": {
            "child_selection_order": (
                "source-reported area descending, then canonical child source id"
            ),
            "child_selection_may_use_distance": False,
            "probe_reselection_after_distance_allowed": False,
        },
        "numerical_audit": {
            "search_radii_km": [64, 128, 256, 512, 1024, 2048],
            "line_chunk_vertex_counts": [256, 1024, 4096],
            "query_chunk_sizes": [1, 2, 4],
            "worker_counts": [1, 2, 4],
            "invariance_absolute_tolerance_m": 0.000001,
            "geodesic_absolute_tolerance_m": 100.0,
            "geodesic_relative_tolerance": 0.005,
        },
        "diagnostic_points": [
            {
                "city_id": "synthetic_fixed_point",
                "longitude": -118.2437,
                "latitude": 34.0522,
                "projected_crs": "EPSG:32611",
            }
        ],
        "locks": {
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "access_contract": {
            "gshhg_l4_member_opened": False,
            "eligible_land_grid_opened": False,
            "distance_values_computed": False,
            "predictor_values_computed": False,
            "model_fit_performed": False,
            "external_target_files_opened": False,
        },
        "outputs": {
            "success_manifest": SUCCESS_RELATIVE.as_posix(),
            "v1_failure_manifest": V1_FAILURE_RELATIVE.as_posix(),
            "diagnostic_table": TABLE_RELATIVE.as_posix(),
        },
    }


def _preregistration(base_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": "gshhg_l3_hierarchy_audit_preregistered_geometry_unopened",
        "preregistration_id": "target_blind_gshhg_l3_hierarchy_audit_v1",
        "unchanged_v2_contract": copy.deepcopy(
            base_config["unchanged_v2_contract"]
        ),
        "hierarchy_contract": copy.deepcopy(base_config["hierarchy_contract"]),
        "probe_rule": copy.deepcopy(base_config["probe_rule"]),
        "numerical_audit": copy.deepcopy(base_config["numerical_audit"]),
        "locks": copy.deepcopy(base_config["locks"]),
        "access_contract": copy.deepcopy(base_config["access_contract"]),
        "commit_sha256": PREREGISTRATION_COMMIT_SHA256,
    }


def _pilot() -> dict[str, Any]:
    return {
        "state": "geometry_pilot_complete_source_not_frozen",
        "source_layers": {
            "great_lakes_identity": {
                "source_polygons": [
                    {
                        "source_id": source_id,
                        "normalized_wkb_sha256": (
                            CORRECTED_HASH if source_id == "180515" else sha256
                        ),
                    }
                    for source_id, sha256 in L2_HASHES.items()
                ]
            }
        },
        "commit_sha256": PILOT_COMMIT_SHA256,
    }


def _v1_failure() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "algorithm_version": "gshhg-l3-hierarchy-audit-v1-failure-record",
        "state": "gshhg_l3_hierarchy_audit_v1_failed",
        "phase": "phase_1_structure",
        "gate": "selected_l2_normalized_wkb_sha256",
        "expected": {"source_id": "180515", "sha256": OLD_HASH},
        "observed": {"sha256": CORRECTED_HASH},
        "repository": {
            "head": V1_RUN_HEAD,
            "branch": "main",
            "origin_main": V1_RUN_HEAD,
            "head_equals_origin_main": True,
            "tracked_blob_sha1": {"src/synthetic_executor.py": "3" * 40},
        },
        "phase_evidence": {
            "phase_1_started": True,
            "archive_opened": True,
        },
        "access_contract": {
            "network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_l1_l2_l3_members_may_have_been_opened": True,
            "authorized_member_allowlist": list(l3_audit.AUTHORIZED_MEMBERS),
            "probe_derived": False,
            "distance_values_computed": False,
            "gshhg_l4_member_opened": False,
            "census_layer_opened": False,
            "other_public_source_geometry_opened": False,
            "eligible_land_grid_opened": False,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
            "geometry_exported_or_redistributed": False,
        },
        "locks": {
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "amendment_policy": {
            "failure_record_is_append_only": True,
            "tolerance_probe_or_gate_may_be_relaxed_after_failure": False,
            "separate_committed_and_pushed_amendment_required": True,
        },
        "commit_sha256": V1_FAILURE_COMMIT_SHA256,
    }


def _amendment() -> dict[str, Any]:
    return {
        "amendment": {
            "schema_version": 2,
            "algorithm_version": (
                "gshhg-l3-hierarchy-audit-structural-amendment-v2"
            ),
            "amendment_id": (
                "target_blind_gshhg_l3_hierarchy_audit_structural_amendment_v2"
            ),
            "amendment_date": "2026-07-30",
            "state": (
                "gshhg_l3_hierarchy_audit_v2_"
                "structural_amendment_committed_unopened"
            ),
            "scope": (
                "correct exactly one transcribed source-structure hash after "
                "preserving the authenticated V1 failure and before any probe "
                "or distance"
            ),
            "correction_count": 1,
            "base_preregistration_config": BASE_CONFIG_RELATIVE.as_posix(),
            "base_preregistration_config_sha256": BASE_CONFIG_SHA256,
            "base_preregistration_manifest": PREREGISTRATION_RELATIVE.as_posix(),
            "base_preregistration_manifest_sha256": (
                PREREGISTRATION_FILE_SHA256
            ),
            "base_preregistration_commit_sha256": (
                PREREGISTRATION_COMMIT_SHA256
            ),
            "v1_failure_manifest": V1_FAILURE_RELATIVE.as_posix(),
            "v1_failure_manifest_sha256": V1_FAILURE_FILE_SHA256,
            "v1_failure_commit_sha256": V1_FAILURE_COMMIT_SHA256,
            "v1_failure_git_commit": V1_FAILURE_GIT_COMMIT,
            "v1_failure_tracked_blob_sha1": V1_FAILURE_BLOB_SHA1,
            "v1_run_head": V1_RUN_HEAD,
            "v1_required_state": "gshhg_l3_hierarchy_audit_v1_failed",
            "v1_required_phase": "phase_1_structure",
            "v1_required_gate": "selected_l2_normalized_wkb_sha256",
            "all_other_structure_gates_unchanged": True,
            "all_probe_definitions_unchanged": True,
            "all_numerical_algorithms_and_thresholds_unchanged": True,
            "all_access_locks_unchanged": True,
        },
        "correction": {
            "field_path": (
                "unchanged_v2_contract."
                "selected_l2_normalized_wkb_sha256.180515"
            ),
            "json_pointer": (
                "/unchanged_v2_contract/"
                "selected_l2_normalized_wkb_sha256/180515"
            ),
            "source_id": "180515",
            "preregistered_value": OLD_HASH,
            "corrected_value": CORRECTED_HASH,
            "v1_observed_value": CORRECTED_HASH,
            "correction_reason": (
                "the base preregistration transcribed the final hexadecimal "
                "character as c although the previously authenticated V2 pilot "
                "and the preserved V1 failure both record a"
            ),
            "acceptance_rule": (
                "replace only the exact old value with the exact corrected "
                "value; no tolerance, fallback, reselection, or additional "
                "correction is allowed"
            ),
            "pilot_manifest": PILOT_RELATIVE.as_posix(),
            "pilot_manifest_sha256": PILOT_FILE_SHA256,
            "pilot_commit_sha256": PILOT_COMMIT_SHA256,
            "pilot_record_pointer": (
                "/source_layers/great_lakes_identity/source_polygons/"
                "source_id=180515/normalized_wkb_sha256"
            ),
        },
        "unchanged_contract": {
            "source_archive_or_version_may_change": False,
            "selected_l2_source_ids_may_change": False,
            "direct_parent_all_descendants_rule_may_change": False,
            "l4_or_exterior_only_rule_may_change": False,
            "existing_points_may_change": False,
            "numerical_thresholds_may_change": False,
            "access_locks_may_change": False,
            "tolerance_may_be_relaxed": False,
            "fallback_or_reselection_allowed": False,
            "v1_failure_may_be_deleted_or_rewritten": False,
        },
        "locks": {
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "amendment_access_record": {
            "amendment_program_archive_opened": False,
            "amendment_program_geometry_opened": False,
            "public_source_geometry_was_read_in_preserved_v1_run": True,
            "source_structure_values_were_read_in_preserved_v1_run": True,
            "v1_probe_derived": False,
            "v1_distance_values_computed": False,
            "network_requests": 0,
            "gshhg_l4_member_opened": False,
            "census_layer_opened": False,
            "eligible_land_grid_opened": False,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
        },
        "outputs": {
            "success_manifest": SUCCESS_RELATIVE.as_posix(),
            "v2_failure_manifest": V2_FAILURE_RELATIVE.as_posix(),
            "diagnostic_table": TABLE_RELATIVE.as_posix(),
        },
    }


def _leaf_differences(
    left: object,
    right: object,
    *,
    prefix: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], object, object]]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences: list[tuple[tuple[str, ...], object, object]] = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                differences.append(
                    (
                        (*prefix, str(key)),
                        left.get(key),
                        right.get(key),
                    )
                )
            else:
                differences.extend(
                    _leaf_differences(
                        left[key],
                        right[key],
                        prefix=(*prefix, str(key)),
                    )
                )
        return differences
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [(prefix, left, right)]
        differences = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            differences.extend(
                _leaf_differences(
                    left_item,
                    right_item,
                    prefix=(*prefix, str(index)),
                )
            )
        return differences
    return [] if left == right else [(prefix, left, right)]


@pytest.fixture
def amendment_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AmendmentHarness:
    root = tmp_path.resolve()
    amendment_path = (root / AMENDMENT_RELATIVE).resolve()
    base_config_path = (root / BASE_CONFIG_RELATIVE).resolve()
    amendment_path.parent.mkdir(parents=True, exist_ok=True)
    amendment_path.write_text("# synthetic TOML intercepted by tomllib.load\n")
    base_config_path.write_text("# synthetic base config; parsed mapping is injected\n")
    for relative in (
        PREREGISTRATION_RELATIVE,
        PILOT_RELATIVE,
        V1_FAILURE_RELATIVE,
        Path("configs/multicity/gshhg_geometry_pilot_v2.toml"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    amendment = _amendment()
    base_config = _base_config()
    preregistration = _preregistration(base_config)
    pilot = _pilot()
    v1_failure = _v1_failure()

    pilot_v2_path = (
        root / "configs/multicity/gshhg_geometry_pilot_v2.toml"
    ).resolve()
    pilot_v2 = {
        "great_lakes_connected_water_contract": {
            "source_polygons": [
                {
                    "source_id": "180515",
                    "expected_normalized_wkb_sha256": CORRECTED_HASH,
                }
            ]
        }
    }

    def fake_toml_load(handle: Any) -> dict[str, Any]:
        handle_path = Path(handle.name).resolve()
        if handle_path == amendment_path:
            return copy.deepcopy(amendment)
        if handle_path == pilot_v2_path:
            return copy.deepcopy(pilot_v2)
        raise AssertionError(f"Unexpected synthetic TOML read: {handle_path}")

    monkeypatch.setattr(tomllib, "load", fake_toml_load)
    hash_records = {
        amendment_path: AMENDMENT_FILE_SHA256,
        base_config_path: BASE_CONFIG_SHA256,
        (root / PREREGISTRATION_RELATIVE).resolve(): (
            PREREGISTRATION_FILE_SHA256
        ),
        (root / PILOT_RELATIVE).resolve(): PILOT_FILE_SHA256,
        (root / V1_FAILURE_RELATIVE).resolve(): V1_FAILURE_FILE_SHA256,
        pilot_v2_path: (
            "c0f41f77d0c87a5ca09de81c2ec0ca2ae489633ac3f785648fbeb5f9f67e67ff"
        ),
    }

    def fake_sha256(path: str | Path) -> str:
        resolved = Path(path).resolve()
        if resolved in hash_records:
            return hash_records[resolved]
        if resolved.is_file():
            return hashlib.sha256(resolved.read_bytes()).hexdigest()
        raise AssertionError(f"Unexpected synthetic amendment hash request: {resolved}")

    monkeypatch.setattr(l3_audit, "sha256_file", fake_sha256)

    def fake_json_reader(
        path: Path,
        *,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        resolved = path.resolve()
        if resolved == (root / V1_FAILURE_RELATIVE).resolve():
            return copy.deepcopy(v1_failure), V1_FAILURE_FILE_SHA256
        if resolved == (root / PREREGISTRATION_RELATIVE).resolve():
            return copy.deepcopy(preregistration), PREREGISTRATION_FILE_SHA256
        if resolved == (root / PILOT_RELATIVE).resolve():
            return copy.deepcopy(pilot), PILOT_FILE_SHA256
        raise AssertionError(f"Unexpected synthetic JSON read for {label}: {resolved}")

    json_reader = Mock(side_effect=fake_json_reader)
    monkeypatch.setattr(l3_audit, "_read_json_object", json_reader)
    monkeypatch.setattr(
        l3_audit,
        "_git_readonly",
        lambda _root, *_arguments: (
            l3_audit.EXPECTED_AMENDMENT_BLOB_SHA1
            if str(_arguments[-1]).endswith(l3_audit.AMENDMENT_PATH)
            else (
                V1_FAILURE_BLOB_SHA1
                if str(_arguments[-1]).endswith(
                    V1_FAILURE_RELATIVE.as_posix()
                )
                else "3" * 40
            )
        ),
    )
    monkeypatch.setattr(
        l3_audit,
        "_require_git_ancestor",
        lambda *_args, **_kwargs: None,
    )

    return AmendmentHarness(
        root=root,
        amendment_path=amendment_path,
        base_config_path=base_config_path,
        amendment=amendment,
        base_config=base_config,
        preregistration=preregistration,
        pilot=pilot,
        v1_failure=v1_failure,
        json_reader=json_reader,
    )


def _authenticate(harness: AmendmentHarness) -> Any:
    return l3_audit._authenticate_v2_amendment(
        harness.root,
        base_config_path=harness.base_config_path,
        base_config=harness.base_config,
        preregistration=harness.preregistration,
        pilot=harness.pilot,
    )


def _set_nested(
    mapping: dict[str, Any],
    dotted_path: str,
    replacement: object,
) -> None:
    parts = dotted_path.split(".")
    target = mapping
    for part in parts[:-1]:
        value = target[part]
        assert isinstance(value, dict)
        target = value
    target[parts[-1]] = replacement


def test_exact_single_leaf_c_to_a_amendment_is_accepted(
    amendment_harness: AmendmentHarness,
) -> None:
    authenticated = _authenticate(amendment_harness)

    assert authenticated.path == amendment_harness.amendment_path
    assert authenticated.file_sha256 == AMENDMENT_FILE_SHA256
    assert authenticated.contract["correction"]["source_id"] == "180515"
    assert authenticated.contract["correction"]["preregistered_value"] == OLD_HASH
    assert (
        authenticated.contract["correction"]["corrected_value"]
        == CORRECTED_HASH
    )
    assert (
        authenticated.effective_config["unchanged_v2_contract"][
            "selected_l2_normalized_wkb_sha256"
        ]["180515"]
        == CORRECTED_HASH
    )
    assert authenticated.v1_failure["gate"] == (
        "selected_l2_normalized_wkb_sha256"
    )
    assert authenticated.v1_failure_file_sha256 == V1_FAILURE_FILE_SHA256

    differences = _leaf_differences(
        amendment_harness.base_config,
        authenticated.effective_config,
    )
    assert differences == [
        (
            (
                "unchanged_v2_contract",
                "selected_l2_normalized_wkb_sha256",
                "180515",
            ),
            OLD_HASH,
            CORRECTED_HASH,
        )
    ]
    amendment_harness.json_reader.assert_any_call(
        (amendment_harness.root / V1_FAILURE_RELATIVE).resolve(),
        label="preserved GSHHG L3 V1 failure",
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", 3),
        ("algorithm_version", "different-amendment"),
        ("amendment_id", "different-amendment-id"),
        ("state", "geometry_opened"),
        ("correction_count", 2),
        ("base_preregistration_config", "configs/not-the-base.toml"),
        ("base_preregistration_config_sha256", "0" * 64),
        ("base_preregistration_manifest", "manifests/not-the-prereg.json"),
        ("base_preregistration_manifest_sha256", "0" * 64),
        ("base_preregistration_commit_sha256", "0" * 64),
        ("v1_failure_manifest", "manifests/not-the-failure.json"),
        ("v1_failure_manifest_sha256", "0" * 64),
        ("v1_failure_commit_sha256", "0" * 64),
        ("v1_failure_git_commit", "0" * 40),
        ("v1_failure_tracked_blob_sha1", "0" * 40),
        ("v1_run_head", "0" * 40),
        ("v1_required_state", "different-state"),
        ("v1_required_phase", "phase_2_numerical"),
        ("v1_required_gate", "different-gate"),
        ("all_other_structure_gates_unchanged", False),
        ("all_probe_definitions_unchanged", False),
        ("all_numerical_algorithms_and_thresholds_unchanged", False),
        ("all_access_locks_unchanged", False),
    ],
)
def test_amendment_identity_tampering_is_rejected(
    amendment_harness: AmendmentHarness,
    field: str,
    replacement: object,
) -> None:
    amendment_harness.amendment["amendment"][field] = replacement

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("field_path", "unchanged_v2_contract.selected_l2_source_ids"),
        ("json_pointer", "/numerical_audit/invariance_absolute_tolerance_m"),
        ("source_id", "180517"),
        ("preregistered_value", "0" * 64),
        ("corrected_value", "f" * 64),
        ("v1_observed_value", "f" * 64),
        ("pilot_manifest", "manifests/not-the-pilot.json"),
        ("pilot_manifest_sha256", "0" * 64),
        ("pilot_commit_sha256", "0" * 64),
        ("pilot_record_pointer", "/source_layers/not-the-record"),
    ],
)
def test_exact_correction_or_pilot_identity_tampering_is_rejected(
    amendment_harness: AmendmentHarness,
    field: str,
    replacement: object,
) -> None:
    amendment_harness.amendment["correction"][field] = replacement

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("state", "different-failure-state"),
        ("phase", "phase_2_numerical"),
        ("gate", "different-gate"),
        ("expected.source_id", "180517"),
        ("expected.sha256", "0" * 64),
        ("observed.sha256", "f" * 64),
        ("access_contract.probe_derived", True),
        ("access_contract.distance_values_computed", True),
    ],
)
def test_v1_failure_state_gate_probe_or_distance_tampering_is_rejected(
    amendment_harness: AmendmentHarness,
    field: str,
    replacement: object,
) -> None:
    _set_nested(amendment_harness.v1_failure, field, replacement)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


@pytest.mark.parametrize(
    ("mutation", "replacement"),
    [
        ("preregistration_commit", "0" * 64),
        ("preregistration_old_hash", "f" * 64),
        ("pilot_commit", "0" * 64),
        ("pilot_source_id", "180516"),
        ("pilot_recorded_hash", "f" * 64),
    ],
)
def test_preregistration_or_pilot_evidence_tampering_is_rejected(
    amendment_harness: AmendmentHarness,
    mutation: str,
    replacement: str,
) -> None:
    if mutation == "preregistration_commit":
        amendment_harness.preregistration["commit_sha256"] = replacement
    elif mutation == "preregistration_old_hash":
        amendment_harness.preregistration["unchanged_v2_contract"][
            "selected_l2_normalized_wkb_sha256"
        ]["180515"] = replacement
    elif mutation == "pilot_commit":
        amendment_harness.pilot["commit_sha256"] = replacement
    elif mutation == "pilot_source_id":
        amendment_harness.pilot["source_layers"]["great_lakes_identity"][
            "source_polygons"
        ][1]["source_id"] = replacement
    else:
        amendment_harness.pilot["source_layers"]["great_lakes_identity"][
            "source_polygons"
        ][1]["normalized_wkb_sha256"] = replacement

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        (
            "unchanged_contract",
            "source_archive_or_version_may_change",
            True,
        ),
        ("unchanged_contract", "selected_l2_source_ids_may_change", True),
        ("unchanged_contract", "numerical_thresholds_may_change", True),
        ("unchanged_contract", "access_locks_may_change", True),
        ("unchanged_contract", "tolerance_may_be_relaxed", True),
        ("unchanged_contract", "fallback_or_reselection_allowed", True),
        (
            "unchanged_contract",
            "v1_failure_may_be_deleted_or_rewritten",
            True,
        ),
        ("locks", "source_lock_created", True),
        ("locks", "predictor_build_authorized", True),
        ("locks", "external_targets_unlocked", True),
        ("amendment_access_record", "amendment_program_archive_opened", True),
        ("amendment_access_record", "amendment_program_geometry_opened", True),
        ("amendment_access_record", "v1_probe_derived", True),
        ("amendment_access_record", "v1_distance_values_computed", True),
        ("amendment_access_record", "network_requests", 1),
        ("amendment_access_record", "gshhg_l4_member_opened", True),
        ("amendment_access_record", "eligible_land_grid_opened", True),
        ("amendment_access_record", "predictor_values_computed", True),
        ("amendment_access_record", "model_fit_performed", True),
        ("amendment_access_record", "external_target_files_opened", True),
    ],
)
def test_unchanged_contract_lock_or_access_tampering_is_rejected(
    amendment_harness: AmendmentHarness,
    section: str,
    field: str,
    replacement: object,
) -> None:
    amendment_harness.amendment[section][field] = replacement

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


@pytest.mark.parametrize("location", ["top_level", "correction"])
def test_unknown_amendment_fields_are_rejected(
    amendment_harness: AmendmentHarness,
    location: str,
) -> None:
    if location == "top_level":
        amendment_harness.amendment["unexpected_second_patch"] = {
            "field_path": "numerical_audit.invariance_absolute_tolerance_m",
            "corrected_value": 0.1,
        }
    else:
        amendment_harness.amendment["correction"]["second_field_path"] = (
            "numerical_audit.invariance_absolute_tolerance_m"
        )

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


def test_multi_leaf_effective_contract_change_is_rejected(
    amendment_harness: AmendmentHarness,
) -> None:
    amendment_harness.base_config["numerical_audit"][
        "invariance_absolute_tolerance_m"
    ] = 0.1

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


def test_selected_parent_or_sibling_hash_change_is_rejected(
    amendment_harness: AmendmentHarness,
) -> None:
    amendment_harness.base_config["unchanged_v2_contract"][
        "selected_l2_normalized_wkb_sha256"
    ]["180507"] = "f" * 64

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        _authenticate(amendment_harness)


def test_v1_and_v2_failure_paths_are_distinct_and_append_only(
    amendment_harness: AmendmentHarness,
) -> None:
    assert (
        amendment_harness.base_config["outputs"]["v1_failure_manifest"]
        == V1_FAILURE_RELATIVE.as_posix()
    )
    assert (
        amendment_harness.amendment["outputs"]["v2_failure_manifest"]
        == V2_FAILURE_RELATIVE.as_posix()
    )
    assert V1_FAILURE_RELATIVE != V2_FAILURE_RELATIVE
    assert (
        amendment_harness.amendment["unchanged_contract"][
            "v1_failure_may_be_deleted_or_rewritten"
        ]
        is False
    )


def _v2_amendment_result(
    harness: AmendmentHarness,
) -> l3_audit.V2Amendment:
    effective = copy.deepcopy(harness.base_config)
    effective["unchanged_v2_contract"]["selected_l2_normalized_wkb_sha256"][
        "180515"
    ] = CORRECTED_HASH
    return l3_audit.V2Amendment(
        path=harness.amendment_path,
        file_sha256=AMENDMENT_FILE_SHA256,
        contract=copy.deepcopy(harness.amendment),
        effective_config=effective,
        v1_failure=copy.deepcopy(harness.v1_failure),
        v1_failure_file_sha256=V1_FAILURE_FILE_SHA256,
    )


def _false_terminal_locks() -> dict[str, bool]:
    return {
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }


def _false_terminal_access() -> dict[str, bool]:
    return {
        "gshhg_l4_member_opened": False,
        "census_layer_opened": False,
        "other_public_source_geometry_opened": False,
        "eligible_land_grid_opened": False,
        "distance_feature_surface_computed": False,
        "tract_aggregation_performed": False,
        "predictor_values_computed": False,
        "predictor_construction_performed": False,
        "model_fit_performed": False,
        "model_predictions_computed": False,
        "landsat_thermal_values_read": False,
        "landsat_target_qa_values_read": False,
        "external_target_files_opened": False,
        "final_evaluation_outputs_opened": False,
        "geometry_exported_or_redistributed": False,
    }


def _state_machine_payloads(
    harness: AmendmentHarness,
) -> tuple[dict[str, Any], dict[str, Any]]:
    failure_access: dict[str, Any] = _false_terminal_access()
    failure_access.update(
        {
            "network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_l1_l2_l3_members_may_have_been_opened": True,
            "authorized_member_allowlist": list(l3_audit.AUTHORIZED_MEMBERS),
            "probe_derived": False,
            "distance_values_computed": False,
        }
    )
    failure = {
        "schema_version": l3_audit.SCHEMA_VERSION,
        "algorithm_version": f"{l3_audit.ALGORITHM_VERSION}-failure-record",
        "state": l3_audit.FAILURE_STATE,
        "phase": "phase_1_structure",
        "config": {
            "path": BASE_CONFIG_RELATIVE.as_posix(),
            "sha256": l3_audit.EXPECTED_CONFIG_SHA256,
        },
        "preregistration": {
            "path": l3_audit.PREREGISTRATION_PATH,
            "file_sha256": l3_audit.EXPECTED_PREREGISTRATION_FILE_SHA256,
            "commit_sha256": l3_audit.EXPECTED_PREREGISTRATION_COMMIT_SHA256,
        },
        "repository": {},
        "structural_amendment": {},
        "prior_v1_failure": {},
        "phase_evidence": {"phase_1_started": True},
        "locks": _false_terminal_locks(),
        "access_contract": failure_access,
    }

    table = pd.DataFrame(
        [
            {
                "point_id": "synthetic",
                "point_kind": "fixed_v2_replay",
                "distance_m": 12.5,
                "l1_l2_only_distance_m": 12.5,
            }
        ]
    )
    harness.root.joinpath(TABLE_RELATIVE).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    table_bytes = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
    harness.root.joinpath(TABLE_RELATIVE).write_bytes(table_bytes)
    serialized = pd.read_csv(harness.root / TABLE_RELATIVE)
    runtime_body: dict[str, Any] = {
        "algorithm_version": "synthetic-runtime",
        "files": {},
        "packages": {},
    }
    runtime = {
        **runtime_body,
        "sha256": l3_audit.canonical_sha256(runtime_body),
    }
    success_access: dict[str, Any] = _false_terminal_access()
    success_access.update(
        {
            "audit_program_network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_gshhg_member_count_opened": len(
                l3_audit.AUTHORIZED_MEMBERS
            ),
            "authorized_gshhg_members": list(l3_audit.AUTHORIZED_MEMBERS),
            "unauthorized_gshhg_members_opened": 0,
            "fixed_target_blind_source_geometry_distances_computed": True,
        }
    )
    success = {
        "schema_version": l3_audit.SCHEMA_VERSION,
        "algorithm_version": l3_audit.ALGORITHM_VERSION,
        "state": l3_audit.COMPLETE_STATE,
        "config": {
            "path": BASE_CONFIG_RELATIVE.as_posix(),
            "sha256": l3_audit.EXPECTED_CONFIG_SHA256,
            "all_other_preregistered_structure_probe_and_numerical_gates_unchanged": (
                True
            ),
            "exact_documented_source_identity_correction_count": 1,
        },
        "planning_authorization": {
            "path": l3_audit.PLAN_PATH,
            "file_sha256": l3_audit.EXPECTED_PLAN_FILE_SHA256,
            "commit_sha256": l3_audit.EXPECTED_PLAN_COMMIT_SHA256,
            "authorized_stage": (
                "target_blind_gshhg_l3_hierarchy_geometry_audit"
            ),
        },
        "preregistration": {
            "path": l3_audit.PREREGISTRATION_PATH,
            "file_sha256": l3_audit.EXPECTED_PREREGISTRATION_FILE_SHA256,
            "commit_sha256": l3_audit.EXPECTED_PREREGISTRATION_COMMIT_SHA256,
            "preregistration_id": harness.preregistration[
                "preregistration_id"
            ],
        },
        "repository": {},
        "structural_amendment": {},
        "prior_v1_failure": {},
        "hierarchy_audit": {"all_structural_gates_passed": True},
        "numerical_audit": {"all_numerical_gates_passed": True},
        "locks": _false_terminal_locks(),
        "access_contract": success_access,
        "decision": {
            "audit_passed": True,
            "source_frozen": False,
            "algorithm_frozen": False,
            "predictor_build_authorized": False,
            "next_safe_stage": (
                "separate_portable_water_distance_source_and_algorithm_"
                "freeze_decision"
            ),
        },
        "source_archive": {
            "sha256": harness.base_config["source"]["expected_archive_sha256"],
            "bytes": harness.base_config["source"]["expected_archive_bytes"],
            "authorized_member_count": len(l3_audit.AUTHORIZED_MEMBERS),
            "member_open_log": list(l3_audit.AUTHORIZED_MEMBERS),
            "unauthorized_member_open_count": 0,
            "zipfile_testzip_called": False,
        },
        "diagnostic_table": {
            "path": TABLE_RELATIVE.as_posix(),
            "bytes": len(table_bytes),
            "sha256": hashlib.sha256(table_bytes).hexdigest(),
            "rows": len(serialized),
            "semantic_sha256": l3_audit.canonical_sha256(
                serialized[
                    [
                        "point_id",
                        "point_kind",
                        "distance_m",
                        "l1_l2_only_distance_m",
                    ]
                ].to_dict("records")
            ),
        },
        "code_runtime": runtime,
    }
    return success, failure


def _install_state_machine_stubs(
    harness: AmendmentHarness,
    monkeypatch: pytest.MonkeyPatch,
    *,
    success: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> None:
    monkeypatch.setattr(
        l3_audit,
        "_read_config",
        lambda _path: (
            harness.root,
            harness.base_config_path,
            harness.base_config,
        ),
    )
    monkeypatch.setattr(
        l3_audit,
        "audit_multicity_plan",
        lambda *_args, **_kwargs: {
            "commit_sha256": l3_audit.EXPECTED_PLAN_COMMIT_SHA256,
        },
    )
    original_sha256 = l3_audit.sha256_file

    def state_sha256(path: str | Path) -> str:
        resolved = Path(path).resolve()
        if resolved == harness.base_config_path:
            return l3_audit.EXPECTED_CONFIG_SHA256
        if resolved == (harness.root / l3_audit.PLAN_PATH).resolve():
            return l3_audit.EXPECTED_PLAN_FILE_SHA256
        return original_sha256(resolved)

    monkeypatch.setattr(l3_audit, "sha256_file", state_sha256)
    success_path = (harness.root / SUCCESS_RELATIVE).resolve()
    failure_path = (harness.root / V2_FAILURE_RELATIVE).resolve()
    if success is not None:
        success_path.parent.mkdir(parents=True, exist_ok=True)
        success_path.write_text("{}\n", encoding="utf-8")
    if failure is not None:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text("{}\n", encoding="utf-8")

    def terminal_json_reader(
        path: Path,
        *,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        resolved = path.resolve()
        if resolved == (harness.root / PREREGISTRATION_RELATIVE).resolve():
            return harness.preregistration, PREREGISTRATION_FILE_SHA256
        if resolved == (harness.root / PILOT_RELATIVE).resolve():
            return harness.pilot, PILOT_FILE_SHA256
        if resolved == success_path and success is not None:
            return success, "synthetic-success-sha"
        if resolved == failure_path and failure is not None:
            return failure, "synthetic-failure-sha"
        raise AssertionError(f"Unexpected terminal JSON read for {label}: {resolved}")

    monkeypatch.setattr(l3_audit, "_read_json_object", terminal_json_reader)
    monkeypatch.setattr(
        l3_audit,
        "_authenticate_v2_amendment",
        lambda *_args, **_kwargs: _v2_amendment_result(harness),
    )
    monkeypatch.setattr(
        l3_audit,
        "_authenticate_terminal_repository",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        l3_audit,
        "_authenticate_v2_terminal_lineage",
        lambda *_args, **_kwargs: None,
    )
    if success is not None:
        monkeypatch.setattr(
            l3_audit,
            "_expected_runtime_fingerprint",
            lambda _root: success["code_runtime"],
        )


def test_preserved_v1_failure_plus_v2_success_is_legal(
    amendment_harness: AmendmentHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success, _ = _state_machine_payloads(amendment_harness)
    _install_state_machine_stubs(
        amendment_harness,
        monkeypatch,
        success=success,
        failure=None,
    )

    authenticated = l3_audit.authenticate_l3_audit_terminal(
        amendment_harness.base_config_path
    )

    assert authenticated is success
    assert (amendment_harness.root / V1_FAILURE_RELATIVE).is_file()


def test_preserved_v1_failure_plus_v2_failure_is_legal(
    amendment_harness: AmendmentHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, failure = _state_machine_payloads(amendment_harness)
    (amendment_harness.root / TABLE_RELATIVE).unlink()
    _install_state_machine_stubs(
        amendment_harness,
        monkeypatch,
        success=None,
        failure=failure,
    )

    authenticated = l3_audit.authenticate_l3_audit_terminal(
        amendment_harness.base_config_path
    )

    assert authenticated is failure
    assert (amendment_harness.root / V1_FAILURE_RELATIVE).is_file()


def test_v2_success_and_v2_failure_together_are_rejected(
    amendment_harness: AmendmentHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success, failure = _state_machine_payloads(amendment_harness)
    _install_state_machine_stubs(
        amendment_harness,
        monkeypatch,
        success=success,
        failure=failure,
    )

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="cannot both exist",
    ):
        l3_audit.authenticate_l3_audit_terminal(
            amendment_harness.base_config_path
        )


def test_every_v2_terminal_requires_preserved_v1_failure(
    amendment_harness: AmendmentHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success, _ = _state_machine_payloads(amendment_harness)
    _install_state_machine_stubs(
        amendment_harness,
        monkeypatch,
        success=success,
        failure=None,
    )
    (amendment_harness.root / V1_FAILURE_RELATIVE).unlink()

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="preserved V1 failure is required",
    ):
        l3_audit.authenticate_l3_audit_terminal(
            amendment_harness.base_config_path
        )


def test_amendment_preflight_failure_happens_before_any_archive_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path.resolve()
    config_path = project_root / BASE_CONFIG_RELATIVE
    base_config = _base_config()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("# synthetic base config\n", encoding="utf-8")
    plan_path = project_root / l3_audit.PLAN_PATH
    preregistration = _preregistration(base_config)
    pilot = _pilot()
    git_gate = l3_audit.GitGate(
        head="1" * 40,
        branch="main",
        origin_main="1" * 40,
        tracked_blob_sha1={relative: "2" * 40 for relative in l3_audit.CODE_PATHS},
    )

    monkeypatch.setattr(
        l3_audit,
        "_read_config",
        lambda _path: (project_root, config_path, base_config),
    )

    def fake_sha256(path: str | Path) -> str:
        resolved = Path(path).resolve()
        if resolved == config_path.resolve():
            return l3_audit.EXPECTED_CONFIG_SHA256
        if resolved == plan_path.resolve():
            return l3_audit.EXPECTED_PLAN_FILE_SHA256
        raise AssertionError(f"Unexpected preflight hash request: {resolved}")

    monkeypatch.setattr(l3_audit, "sha256_file", fake_sha256)
    monkeypatch.setattr(
        l3_audit,
        "audit_multicity_plan",
        lambda *_args, **_kwargs: {
            "schema_version": 6,
            "commit_sha256": l3_audit.EXPECTED_PLAN_COMMIT_SHA256,
            "next_safe_stage": (
                "target_blind_gshhg_l3_hierarchy_geometry_audit"
            ),
            "authorized_now": {
                "target_blind_gshhg_l3_hierarchy_geometry_read": True,
            },
        },
    )

    def fake_json_reader(
        path: Path,
        *,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        if path == project_root / l3_audit.PREREGISTRATION_PATH:
            return preregistration, l3_audit.EXPECTED_PREREGISTRATION_FILE_SHA256
        if path == project_root / l3_audit.PILOT_PATH:
            return pilot, PILOT_FILE_SHA256
        raise AssertionError(f"Unexpected preflight JSON read for {label}: {path}")

    monkeypatch.setattr(l3_audit, "_read_json_object", fake_json_reader)
    monkeypatch.setattr(
        l3_audit,
        "_read_exact_configs",
        lambda _path: (
            project_root / "configs/amendment.toml",
            {},
            project_root / "configs/base.toml",
            {},
        ),
    )
    monkeypatch.setattr(
        l3_audit,
        "_required_git_paths",
        lambda _root: l3_audit.CODE_PATHS,
    )
    monkeypatch.setattr(
        l3_audit,
        "_git_blob_records",
        lambda _root, *, required_paths: git_gate,
    )
    amendment_gate = Mock(
        side_effect=l3_audit.GshhgL3HierarchyAuditError(
            "synthetic V2 amendment preflight rejection"
        )
    )
    monkeypatch.setattr(
        l3_audit,
        "_authenticate_v2_amendment",
        amendment_gate,
        raising=False,
    )
    archive_spies = [
        Mock(side_effect=AssertionError("archive access preceded amendment auth"))
        for _ in range(3)
    ]
    monkeypatch.setattr(l3_audit, "run_structural_phase", archive_spies[0])
    monkeypatch.setattr(l3_audit, "_hash_archive", archive_spies[1])
    monkeypatch.setattr(l3_audit.zipfile, "ZipFile", archive_spies[2])

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="synthetic V2 amendment preflight rejection",
    ):
        l3_audit.audit_gshhg_l3_hierarchy(config_path)

    assert amendment_gate.call_count == 1
    assert all(not spy.called for spy in archive_spies)
