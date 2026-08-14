"""Value-blind source-support preflight and append-only M3 amendment.

This module reads only configuration and authenticated, already-committed JSON
evidence.  It proves that the existing source inventory cannot pass the locked
support gate, then freezes a metadata-query and stop-rule amendment.  It never
performs a network request or opens a predictor, raster, or target table.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.provenance import canonical_sha256, sha256_file

CONFIG_PATH: Final = Path(
    "configs/multicity/m3_source_acquisition_amendment_v1.toml"
)
MODULE_PATH: Final = Path(
    "src/la_heat/multicity/m3_source_acquisition_amendment.py"
)
SCRIPT_PATH: Final = Path(
    "scripts/audit_m3_source_support_and_stage_amendment.py"
)
AMENDMENT_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_ACQUISITION_AMENDMENT.json"
)

SOURCE_CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
HISTORICAL_CITY_SUPPORT: Final = (
    {
        "city_id": "phoenix_az",
        "planned_date_count": 22,
        "none_usable_date_count": 21,
    },
    {
        "city_id": "houston_tx",
        "planned_date_count": 21,
        "none_usable_date_count": 4,
    },
    {
        "city_id": "chicago_il",
        "planned_date_count": 21,
        "none_usable_date_count": 3,
    },
)
LOCKED_QA_CANDIDATES: Final = (
    ("none", False, None),
    ("3k", True, 3.0),
    ("4k", True, 4.0),
    ("6k", True, 6.0),
)
PERMISSIONS: Final = {
    "query_public_metadata": False,
    "read_landsat_asset_hrefs": False,
    "read_landsat_thermal_or_target_qa_values": False,
    "read_raw_or_processed_target_tables": False,
    "create_values_opened_or_access_started_marker": False,
    "build_or_extend_predictors": False,
    "prefetch_landsat_cache": False,
    "rebuild_source_targets": False,
    "fit_models": False,
    "select_model_or_st_qa": False,
    "predict_or_score": False,
    "read_blind_test_landsat_metadata_assets_or_values": False,
}
ACCESS_AUDIT: Final = {
    "authenticated_committed_historical_json_read": True,
    "network_requests_performed": False,
    "landsat_stac_metadata_read": False,
    "landsat_asset_hrefs_read": False,
    "landsat_thermal_or_target_qa_values_read": False,
    "raw_or_processed_target_tables_read": False,
    "predictors_read_or_built": False,
    "model_fit_selection_prediction_or_scoring_performed": False,
    "values_opened_or_access_started_marker_created": False,
}


class M3SourceAcquisitionAmendmentError(RuntimeError):
    """Raised when the preflight or proposed amendment is not reproducible."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M3SourceAcquisitionAmendmentError(message)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceAcquisitionAmendmentError(
            f"{label} must stay inside the project"
        )
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_record(root: Path, value: str | Path, *, label: str) -> dict[str, Any]:
    path = _inside(root, value, label=label)
    if not path.is_file():
        raise M3SourceAcquisitionAmendmentError(f"{label} is unavailable: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceAcquisitionAmendmentError(
            f"Cannot read {label}: {path}"
        ) from error
    if not isinstance(payload, dict):
        raise M3SourceAcquisitionAmendmentError(f"{label} must be a JSON object")
    return payload


def _authenticate_commit(payload: Mapping[str, Any], *, label: str) -> str:
    commit = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(commit, str) or canonical_sha256(unsigned) != commit:
        raise M3SourceAcquisitionAmendmentError(f"{label} commit is invalid")
    return commit


def _committed_record(
    root: Path,
    anchor: Mapping[str, Any],
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _file_record(root, str(anchor.get("path", "")), label=label)
    payload = _read_json(root / record["path"], label=label)
    commit = _authenticate_commit(payload, label=label)
    _require(record["sha256"] == anchor.get("file_sha256"), f"{label} file changed")
    _require(commit == anchor.get("commit_sha256"), f"{label} commit changed")
    required_state = anchor.get("required_state")
    if required_state is not None:
        _require(payload.get("state") == required_state, f"{label} state changed")
    return payload, {**record, "commit_sha256": commit}


def _load_config(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _file_record(root, CONFIG_PATH, label="Source acquisition amendment config")
    try:
        config = tomllib.loads((root / record["path"]).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise M3SourceAcquisitionAmendmentError(
            "Source acquisition amendment config is invalid"
        ) from error
    if not isinstance(config, dict):
        raise M3SourceAcquisitionAmendmentError("Amendment config must be a table")
    return config, record


def _validate_config(config: Mapping[str, Any]) -> None:
    _require(
        set(config)
        == {
            "amendment",
            "anchors",
            "preflight",
            "query_contract",
            "retained_inventory",
            "expanded_inventory",
            "stop_rule",
            "permissions",
            "access_audit",
            "next_stage",
        },
        "Amendment config sections changed",
    )
    amendment = config.get("amendment", {})
    _require(
        amendment
        == {
            "schema_version": 1,
            "id": "multicity_m3_source_acquisition_amendment_v1",
            "status": "draft_not_locked",
            "parent_experiment_id": "multicity_m3_level_anomaly_development_v1",
            "scope": "source_acquisition_universe_only",
            "reason": (
                "the_authenticated_existing_none_threshold_support_cannot_meet_"
                "the_locked_inner_validation_gate"
            ),
            "model_candidate_space_changed": False,
            "st_qa_candidate_space_changed": False,
            "selection_objective_or_gate_changed": False,
            "uncertainty_or_evaluation_changed": False,
            "blind_test_city_cohort_changed": False,
        },
        "Amendment identity or narrow scope changed",
    )
    preflight = config.get("preflight", {})
    _require(
        preflight.get("locked_st_qa_candidate_ids") == ["none", "3k", "4k", "6k"]
        and preflight.get("finite_thresholds_kelvin") == [3.0, 4.0, 6.0]
        and preflight.get("none_is_unthresholded_maximum_valid_pixel_set") is True
        and preflight.get("finite_threshold_valid_pixel_sets_are_subsets_of_none")
        is True
        and preflight.get("tract_and_date_support_are_monotone_in_valid_pixels")
        is True
        and preflight.get("minimum_usable_dates_per_inner_validation_city") == 8
        and preflight.get("current_inventory_passes_locked_gate") is False
        and preflight.get("failing_city_ids") == ["houston_tx", "chicago_il"]
        and tuple(preflight.get("authenticated_city_support", ()))
        == HISTORICAL_CITY_SUPPORT,
        "Historical source-support preflight changed",
    )
    query = config.get("query_contract", {})
    _require(
        query
        == {
            "metadata_query_not_authorized_by_this_amendment": True,
            "metadata_query_must_be_separately_authorized": True,
            "landsat_collection": "landsat-c2-l2",
            "sensors": ["landsat-8", "landsat-9"],
            "warm_season_months": [5, 6, 7, 8, 9, 10],
            "maximum_physical_overpass_span_minutes": 15,
            "minimum_city_union_coverage_fraction": 0.98,
            "reuse_previous_metadata_fields_and_properties_exactly": True,
            "reuse_previous_physical_overpass_grouping_exactly": True,
            "include_every_qualifying_physical_overpass": True,
            "exclude_assets_from_metadata_query": True,
            "persist_asset_hrefs": False,
            "qa_dependent_date_or_scene_selection": False,
            "date_or_scene_reselection_after_query": False,
        },
        "Source metadata query contract changed",
    )
    retained = config.get("retained_inventory", {})
    _require(
        retained
        == {
            "los_angeles_ca": {
                "mode": "retain_authenticated_existing_inventory_exactly",
                "start_date": "2020-05-01",
                "end_date": "2024-10-31",
                "expected_existing_overpass_count": 90,
                "expected_existing_scene_count": 177,
            },
            "phoenix_az": {
                "mode": "retain_authenticated_existing_inventory_exactly",
                "start_date": "2025-05-01",
                "end_date": "2025-10-31",
                "expected_existing_overpass_count": 22,
                "expected_existing_scene_count": 44,
            },
        },
        "Retained source inventory changed",
    )
    expanded = config.get("expanded_inventory", {})
    expected_expanded = {
        city_id: {
            "mode": "replace_existing_2025_slice_with_complete_fixed_window_query",
            "start_date": "2020-05-01",
            "end_date": "2025-10-31",
            "include_all_qualifying_overpasses": True,
        }
        for city_id in ("houston_tx", "chicago_il")
    }
    _require(expanded == expected_expanded, "Expanded source inventory changed")
    stop = config.get("stop_rule", {})
    _require(
        stop
        == {
            "evaluate_only_after_the_complete_expanded_none_candidate_is_rebuilt": True,
            "required_none_usable_dates_per_source_city": 8,
            "if_any_source_city_fails_state": "source_support_failed",
            "if_all_source_cities_pass_next_stage": (
                "separately_authorize_source_only_cache_prefetch_and_offline_qa_rebuild"
            ),
            "adaptive_year_extension_after_failure": False,
            "city_substitution_after_failure": False,
            "support_threshold_relaxation_after_failure": False,
            "model_or_st_qa_selection_after_failure": False,
        },
        "Source-support stop rule changed",
    )
    _require(config.get("permissions") == PERMISSIONS, "Amendment granted permission")
    _require(config.get("access_audit") == ACCESS_AUDIT, "Access audit changed")
    _require(
        config.get("next_stage", {}).get("safe_action")
        == (
            "independently_review_then_append_only_lock_this_amendment_before_"
            "separate_metadata_query_authorization"
        ),
        "Next safe stage changed",
    )


def _validate_previous_inventory_config(root: Path, anchor: Mapping[str, Any]) -> dict[str, Any]:
    record = _file_record(root, str(anchor.get("path", "")), label="Previous inventory config")
    _require(
        record["sha256"] == anchor.get("file_sha256"),
        "Previous inventory configuration changed",
    )
    try:
        previous = tomllib.loads((root / record["path"]).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise M3SourceAcquisitionAmendmentError(
            "Previous inventory configuration is invalid"
        ) from error
    dates = previous.get("landsat_dates", {})
    _require(
        dates
        == {
            "maximum_overpass_span_minutes": 15,
            "minimum_city_union_coverage_fraction": 0.98,
            "warm_season_start_month": 5,
            "warm_season_end_month": 10,
            "los_angeles_start_year": 2020,
            "los_angeles_end_year": 2024,
            "external_year": 2025,
            "page_limit": 100,
        },
        "Previous inventory rules changed",
    )
    return record


def _historical_preflight(
    root: Path,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    anchors = config.get("anchors", {})
    lock_anchor = anchors.get("m3_protocol_lock", {})
    lock_path = _inside(root, str(lock_anchor.get("path", "")), label="M3 protocol lock")
    lock_record = _file_record(root, lock_path, label="M3 protocol lock")
    _require(
        lock_record["sha256"] == lock_anchor.get("file_sha256"),
        "M3 protocol lock file changed",
    )
    protocol = authenticate_m3_development_protocol_lock(root, lock_path)
    _require(
        protocol.get("state") == lock_anchor.get("required_state")
        and protocol.get("commit_sha256") == lock_anchor.get("commit_sha256"),
        "M3 protocol lock changed",
    )
    _require(
        tuple(protocol.get("cohorts", {}).get("source_city_ids", ()))
        == SOURCE_CITY_IDS,
        "M3 source city cohort changed",
    )
    target_qa = protocol.get("development_contract", {}).get("target_qa", {})
    candidates = tuple(
        (
            row.get("id"),
            row.get("apply_threshold"),
            row.get("maximum_kelvin"),
        )
        for row in target_qa.get("st_qa_candidates", ())
        if isinstance(row, dict)
    )
    _require(candidates == LOCKED_QA_CANDIDATES, "Locked ST_QA candidates changed")
    selection = protocol.get("development_contract", {}).get("selection", {})
    _require(
        selection.get("minimum_usable_dates_per_inner_validation_city") == 8,
        "Locked inner source-support gate changed",
    )

    historical_anchor = anchors.get("historical_support", {})
    historical, historical_record = _committed_record(
        root,
        historical_anchor,
        label="Authenticated historical source support",
    )
    locked_historical = (
        protocol.get("input_anchors", {})
        .get("historical_source_evidence", {})
        .get("posthoc_qa_source_evidence", {})
    )
    _require(
        historical_record == locked_historical,
        "Historical support is not the evidence anchored by the M3 lock",
    )
    _require(
        historical.get("analysis_class") == historical_anchor.get("analysis_class")
        and historical.get("formal_result_unchanged") is True
        and historical.get("model_refit_or_recalibrated") is False,
        "Historical support evidence changed role",
    )
    observed_support = tuple(
        {
            "city_id": row.get("city_id"),
            "planned_date_count": row.get("planned_date_count"),
            "none_usable_date_count": row.get("usable_date_count"),
        }
        for row in historical.get("city_support", ())
        if isinstance(row, dict)
    )
    _require(
        observed_support == HISTORICAL_CITY_SUPPORT,
        "Authenticated current source support changed",
    )

    target_plan, target_plan_record = _committed_record(
        root,
        anchors.get("previous_target_plan", {}),
        label="Previous target plan",
    )
    cities = target_plan.get("cities", {})
    _require(
        target_plan.get("access_contract")
        == {
            "network_access_performed": False,
            "landsat_asset_hrefs_read": False,
            "landsat_thermal_or_target_qa_values_read": False,
            "target_tables_read": False,
            "model_fit_or_prediction_performed": False,
        }
        and cities.get("los_angeles_ca", {}).get("overpass_target_unit_count") == 90
        and cities.get("los_angeles_ca", {}).get("primary_scene_count") == 177
        and cities.get("phoenix_az", {}).get("overpass_target_unit_count") == 22
        and cities.get("phoenix_az", {}).get("primary_scene_count") == 44,
        "Authenticated retained inventory changed",
    )
    inventory, inventory_record = _committed_record(
        root,
        anchors.get("previous_predictor_inventory", {}),
        label="Previous predictor inventory",
    )
    _require(
        inventory.get("state")
        == anchors.get("previous_predictor_inventory", {}).get("required_state"),
        "Previous predictor inventory state changed",
    )
    previous_config_record = _validate_previous_inventory_config(
        root, anchors.get("previous_inventory_config", {})
    )
    inputs = {
        "m3_protocol_lock": {
            **lock_record,
            "commit_sha256": protocol["commit_sha256"],
        },
        "historical_source_support": historical_record,
        "previous_target_plan": target_plan_record,
        "previous_predictor_inventory": inventory_record,
        "previous_inventory_config": previous_config_record,
    }
    preflight = {
        "locked_minimum_usable_dates_per_inner_validation_city": 8,
        "current_city_support": [dict(row) for row in HISTORICAL_CITY_SUPPORT],
        "monotonicity_proof": {
            "none_valid_mask": "base_valid_pixel_mask",
            "finite_candidate_valid_mask": (
                "base_valid_pixel_mask_and_st_qa_kelvin_less_than_or_equal_to_threshold"
            ),
            "candidate_subset_of_none": {"3k": True, "4k": True, "6k": True},
            "tract_available_rule_is_monotone_in_valid_pixels": True,
            "date_usable_rule_is_monotone_in_available_tracts": True,
            "finite_threshold_can_increase_usable_dates": False,
        },
        "maximum_usable_dates_under_any_locked_st_qa_candidate": {
            row["city_id"]: row["none_usable_date_count"]
            for row in HISTORICAL_CITY_SUPPORT
        },
        "locked_joint_configuration_count": 16,
        "any_current_joint_configuration_can_pass": False,
        "failing_city_ids": ["houston_tx", "chicago_il"],
        "decision": "current_inventory_ineligible_expand_before_prefetch",
        "current_inventory_prefetch_authorized": False,
    }
    return preflight, inputs


def build_m3_source_acquisition_amendment(project_root: str | Path) -> dict[str, Any]:
    """Build the proposed amendment without querying metadata or opening values."""

    root = Path(project_root).resolve()
    config, config_record = _load_config(root)
    _validate_config(config)
    preflight, inputs = _historical_preflight(root, config)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "m3-source-acquisition-amendment-v1",
        "state": "source_acquisition_amendment_locked_before_new_metadata_or_value_access",
        "amendment_id": config["amendment"]["id"],
        "parent_experiment_id": config["amendment"]["parent_experiment_id"],
        "protocol_amendment_locked": True,
        "source_acquisition_query_contract_locked": True,
        "source_support_stop_rule_locked": True,
        "execution_authorized": False,
        "model_candidate_or_evaluation_contract_changed": False,
        "historical_preflight": preflight,
        "amendment_contract": {
            "scope": config["amendment"]["scope"],
            "query_contract": config["query_contract"],
            "retained_inventory": config["retained_inventory"],
            "expanded_inventory": config["expanded_inventory"],
            "stop_rule": config["stop_rule"],
        },
        "permissions": config["permissions"],
        "input_anchors": inputs,
        "code_identity": {
            "configuration": config_record,
            "amendment_module": _file_record(root, MODULE_PATH, label="Amendment module"),
            "amendment_script": _file_record(root, SCRIPT_PATH, label="Amendment script"),
        },
        "access_audit": config["access_audit"],
        "next_safe_stage": "separately_authorize_target_blind_source_metadata_inventory_extension",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceAcquisitionAmendmentError(
            f"Append-only source acquisition amendment already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def authenticate_m3_source_acquisition_amendment(
    project_root: str | Path,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    """Rebuild the value-blind amendment and require exact equality."""

    root = Path(project_root).resolve()
    path = _inside(root, amendment_path, label="Source acquisition amendment")
    observed = _read_json(path, label="Source acquisition amendment")
    _authenticate_commit(observed, label="Source acquisition amendment")
    expected = build_m3_source_acquisition_amendment(root)
    if observed != expected:
        raise M3SourceAcquisitionAmendmentError(
            "Source acquisition amendment no longer reproduces exactly"
        )
    return observed


def create_m3_source_acquisition_amendment(
    project_root: str | Path,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    """Create the append-only amendment once, then immediately authenticate it."""

    root = Path(project_root).resolve()
    path = _inside(root, amendment_path, label="Source acquisition amendment")
    payload = build_m3_source_acquisition_amendment(root)
    _write_exclusive(payload, path)
    return authenticate_m3_source_acquisition_amendment(root, path)
