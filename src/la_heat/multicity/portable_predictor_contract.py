"""Lock the portable four-city predictor contract after target-blind evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.phoenix_source_footprint_restage import (
    MANIFEST_PATH as PHOENIX_RESTAGE_PATH,
)
from la_heat.multicity.phoenix_source_footprint_restage import (
    verify_phoenix_source_footprint_restage,
)
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "portable-predictor-contract"
COMPLETE_STATE: Final = "complete_portable_predictor_contract_locked"
OUTPUT_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
CANDIDATE_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V2.json"
)
EVIDENCE_TERMINAL_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "MISSING_SUPPORT_CALIBRATION_EVIDENCE_V1.json"
)
CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
EXTERNAL_CITY_IDS: Final = ("phoenix_az", "houston_tx", "chicago_il")
AGGREGATE_EVIDENCE: Final = {
    Path(
        "manifests/multicity/reviews/portable_predictor_contract/"
        "FOUR_CITY_GEOGRAPHY_CONTRACT_V1.json"
    ): "complete_target_blind_four_city_geography_evidence",
    Path(
        "manifests/multicity/reviews/portable_predictor_contract/"
        "FOUR_CITY_WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
    ): "complete_target_blind_four_city_worldcover_support",
    Path(
        "manifests/multicity/reviews/portable_predictor_contract/"
        "SENTINEL_CALIBRATION_SMOKE_EVIDENCE_V1.json"
    ): "complete_target_blind_sentinel_calibration_smoke",
}


class PortablePredictorContractError(ValueError):
    """Raised when the final portable contract cannot be locked."""


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortablePredictorContractError(f"Expected JSON object: {path}")
    return payload


def _authenticated_payload(path: Path, *, state: str | None = None) -> dict[str, Any]:
    payload = _read_json(path)
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise PortablePredictorContractError(f"Evidence commit changed: {path}")
    if state is not None and payload.get("state") != state:
        raise PortablePredictorContractError(f"Evidence state changed: {path}")
    return payload


def _record(project_root: Path, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": path.relative_to(project_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "commit_sha256": payload["commit_sha256"],
    }


def _load_evidence(
    project_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    candidate_path = project_root / CANDIDATE_PATH
    candidate = _authenticated_payload(candidate_path)
    if candidate.get("decision", {}).get("all_four_v1_evidence_gaps_closed") is not True:
        raise PortablePredictorContractError("The predecessor evidence gaps are not closed.")

    terminal_path = project_root / EVIDENCE_TERMINAL_PATH
    terminal = _authenticated_payload(
        terminal_path,
        state="complete_target_blind_missing_support_and_calibration_evidence",
    )
    if (
        terminal.get("predictor_build_authorized") is not False
        or terminal.get("external_targets_unlocked") is not False
        or terminal.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or terminal.get("access_contract", {}).get("predictor_construction_performed")
        is not False
    ):
        raise PortablePredictorContractError("The evidence terminal broke target blindness.")

    restage = verify_phoenix_source_footprint_restage()
    loaded: dict[str, dict[str, Any]] = {
        CANDIDATE_PATH.as_posix(): candidate,
        EVIDENCE_TERMINAL_PATH.as_posix(): terminal,
        PHOENIX_RESTAGE_PATH.as_posix(): restage,
    }
    for aggregate_path, state in AGGREGATE_EVIDENCE.items():
        loaded[aggregate_path.as_posix()] = _authenticated_payload(
            project_root / aggregate_path,
            state=state,
        )
    for city_id in CITY_IDS:
        geography_path = Path(
            f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
        )
        geography = _authenticated_payload(
            project_root / geography_path,
            state="complete_target_blind_city_geography_evidence",
        )
        if (
            geography.get("access_contract", {}).get("external_target_or_qa_values_read")
            is not False
            or geography.get("access_contract", {}).get("predictor_values_read_or_computed")
            is not False
        ):
            raise PortablePredictorContractError(f"Geography lock changed for {city_id}.")
        loaded[geography_path.as_posix()] = geography

        support_path = Path(
            f"manifests/multicity/cities/{city_id}/eligible_support/"
            "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        )
        support = _authenticated_payload(
            project_root / support_path,
            state="complete_target_blind_city_worldcover_support",
        )
        if (
            support.get("city_id") != city_id
            or support.get("access_contract", {}).get("external_target_or_qa_values_read")
            is not False
            or support.get("access_contract", {}).get("predictor_values_computed")
            is not False
        ):
            raise PortablePredictorContractError(f"WorldCover lock changed for {city_id}.")
        loaded[support_path.as_posix()] = support

    la = loaded[
        "manifests/multicity/cities/los_angeles_ca/geography/GEOGRAPHY_CONTRACT_V1.json"
    ]
    la_compatibility = la.get("compatibility", {})
    if (
        la_compatibility.get("exact_primary_geoid_set") is not True
        or la_compatibility.get("requires_v3_interpretation") is not True
    ):
        raise PortablePredictorContractError("Los Angeles compatibility evidence changed.")

    for city_id in EXTERNAL_CITY_IDS:
        smoke_path = Path(
            f"manifests/multicity/cities/{city_id}/sentinel_calibration_smoke/"
            "SENTINEL_CALIBRATION_SMOKE_V1.json"
        )
        smoke = _authenticated_payload(
            project_root / smoke_path,
            state="complete_target_blind_city_sentinel_calibration_smoke",
        )
        if (
            smoke.get("city_id") != city_id
            or smoke.get("access_contract", {}).get("external_target_or_lst_values_read")
            is not False
            or smoke.get("access_contract", {}).get("predictor_construction_performed")
            is not False
        ):
            raise PortablePredictorContractError(
                f"Sentinel calibration access changed for {city_id}."
            )
        for probe in smoke["probes"]:
            metadata = probe["product_metadata"]
            encoding = probe["provider_encoding_evidence"]
            if (
                metadata["processing_baseline"] != "05.11"
                or float(metadata["quantification_value"]) != 10000.0
                or set(metadata["band_offsets_dn"].values()) != {-1000.0}
                or encoding["decode_calibration_authority"]
                != "official_product_metadata_xml"
                or encoding["comparison_status"]
                != "provider_stac_raster_calibration_not_published"
            ):
                raise PortablePredictorContractError(
                    f"Sentinel calibration evidence changed for {city_id}."
                )
        loaded[smoke_path.as_posix()] = smoke
    return candidate, terminal, loaded


def _finalize_registry(candidate: Mapping[str, Any]) -> dict[str, Any]:
    registry = deepcopy(candidate["feature_registry"])
    features = registry["features"]
    order = registry["feature_order"]
    b1 = [row["feature_name"] for row in features if row["b1_transfer"]]
    m2 = [row["feature_name"] for row in features if row["m2_transfer"]]
    if (
        len(features) != 46
        or len(order) != 46
        or set(order) != {row["feature_name"] for row in features}
        or len(b1) != 23
        or len(m2) != 46
    ):
        raise PortablePredictorContractError("The 46-feature registry changed.")
    registry["candidate_rules_bound_for_evidence_stage"] = False
    registry["formal_feature_names_frozen"] = True
    registry["worldcover_is_support_not_predictor"] = True
    registry["prohibited_model_inputs"] = ["city_id", "tract_geoid", "raw_coordinates"]
    if registry["semantic_sha256"] != canonical_sha256(features):
        raise PortablePredictorContractError("The feature-record semantic hash changed.")
    return registry


def verify_portable_predictor_contract(
    config_path: str | Path = "configs/multicity/experiment.toml",
) -> dict[str, Any]:
    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    path = workspace.project_root / OUTPUT_PATH
    payload = _authenticated_payload(path, state=COMPLETE_STATE)
    if (
        payload.get("decision", {}).get("portable_predictor_contract_locked") is not True
        or payload.get("decision", {}).get("predictor_build_authorized") is not True
        or payload.get("decision", {}).get("model_fit_authorized") is not False
        or payload.get("decision", {}).get("external_targets_unlocked") is not False
        or payload.get("decision", {}).get("protocol_lock_created") is not False
        or payload.get("model_roles", {}).get("primary") != "m2_transfer"
        or payload.get("model_roles", {}).get("diagnostic_baseline") != "b1_transfer"
        or payload.get("feature_registry", {}).get("feature_count") != 46
        or payload.get("feature_registry", {}).get("formal_feature_names_frozen")
        is not True
    ):
        raise PortablePredictorContractError("Final portable contract changed.")
    for record in payload["evidence"].values():
        evidence_path = workspace.project_root / record["path"]
        if (
            not evidence_path.is_file()
            or evidence_path.stat().st_size != record["bytes"]
            or sha256_file(evidence_path) != record["sha256"]
        ):
            raise PortablePredictorContractError(
                f"Final contract evidence changed: {evidence_path}"
            )
    return payload


def lock_portable_predictor_contract(
    config_path: str | Path = "configs/multicity/experiment.toml",
) -> dict[str, Any]:
    plan = load_multicity_plan(config_path)
    workspace = MulticityWorkspace.from_plan(plan)
    output_path = workspace.project_root / OUTPUT_PATH
    if output_path.is_file():
        return verify_portable_predictor_contract(plan.path)

    candidate, terminal, evidence = _load_evidence(workspace.project_root)
    contract = deepcopy(candidate["contract"])
    registry = _finalize_registry(candidate)
    model_contract = deepcopy(candidate["model_contract"])
    evidence_records = {
        key: _record(workspace.project_root, workspace.project_root / key, value)
        for key, value in sorted(evidence.items())
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "outcome": "portable_predictor_build_authorized_external_targets_sealed",
        "predecessor_evidence_state": terminal["state"],
        "evidence": evidence_records,
        "contract": contract,
        "feature_registry": registry,
        "model_contract": model_contract,
        "model_roles": {
            "primary": "m2_transfer",
            "diagnostic_baseline": "b1_transfer",
            "b1_deployment_candidate": False,
            "reason": (
                "B1 has little neighborhood spatial differentiation and is retained "
                "only as a comparator."
            ),
        },
        "canonical_support": {
            "city_ids": list(CITY_IDS),
            "geography": "new_same_adapter_four_city_census_2020_geography",
            "eligible_land": "new_four_city_worldcover_2020_v100_valid_non_water_support",
            "worldcover_role": "support_mask_only_not_predictor",
            "same_eligible_denominator_for_all_source_families": True,
            "los_angeles": {
                "primary_geoid_count": 1096,
                "primary_geoid_set_matches_phase1": True,
                "legacy_zone_assignment_exact": False,
                "zone_disagreement_cell_count": 6872,
                "minimum_tract_geometry_iou": 0.5129066492363569,
                "decision": "new_canonical_support_wins",
                "rebuild_2020_2024_predictors_and_target_aggregation": True,
                "phase1_zone_mask_pixel_feature_or_aggregated_target_reuse_allowed": False,
            },
            "phoenix": {
                "canonical_boundary_source_footprint_restaged": True,
                "historical_item_count_parity_required": False,
                "external_targets_remain_sealed": True,
            },
        },
        "sentinel_calibration": {
            "authority": "official_product_metadata_xml",
            "formula": "reflectance=(DN+BOA_ADD_OFFSET)/BOA_QUANTIFICATION_VALUE",
            "offset_applied_exactly_once": True,
            "native_cog_identity_dn_storage_required": True,
            "stac_raster_calibration_role": "diagnostic_only_never_decode_authority",
            "stac_values_may_not_be_synthesized_from_xml": True,
            "missing_stac_calibration_is_blocker": False,
            "verified_processing_baseline": "05.11",
            "verified_quantification_value": 10000.0,
            "verified_band_offset_dn": -1000.0,
            "verified_external_native_utm_zone_count": 3,
        },
        "decision": {
            "portable_predictor_contract_locked": True,
            "portable_feature_names_frozen": True,
            "predictor_build_authorized": True,
            "model_fit_authorized": False,
            "model_score_authorized": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
            "protocol_lock_created": False,
            "next_safe_stage": "build_portable_predictors",
        },
        "access_contract": {
            "external_target_or_qa_values_read": False,
            "landsat_thermal_or_target_qa_values_read": False,
            "predictor_construction_performed": False,
            "model_fit_or_prediction_performed": False,
            "final_evaluation_outputs_opened": False,
        },
        "semantic_locks": {
            "contract_sha256": canonical_sha256(contract),
            "feature_records_sha256": registry["semantic_sha256"],
            "model_contract_sha256": canonical_sha256(model_contract),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, output_path)
    return verify_portable_predictor_contract(plan.path)
