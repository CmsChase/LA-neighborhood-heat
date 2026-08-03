"""Audit the target-blind four-city portable predictor contract candidate.

This decision reads only tracked TOML/JSON/code blobs and local Git history.
It never imports a raster, vector, predictor, model, target, or result reader.
It binds candidate source, calibration, aggregation, timing, feature, and
missingness rules for the next evidence stage, but defers the formal contract
freeze. Predictor construction, model fitting, protocol promotion,
predictions, and external targets remain closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

CONFIG_PATH: Final = "configs/multicity/portable_predictor_contract_freeze_v2.toml"
CONFIG_BYTES: Final = 17_681
CONFIG_SHA256: Final = (
    "0992eff4fdcf45005dcb1c8d237dc28a4205966527572e06be4e875cae0ee2f4"
)
PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
OUTPUT_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V2.json"
)
MODULE_PATH: Final = (
    "src/la_heat/multicity/portable_predictor_contract_freeze_v2.py"
)
SCRIPT_PATH: Final = "scripts/audit_multicity_portable_predictor_contract_freeze_v2.py"
PLAN_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_predictor_contract_transition_v11.py"
)
PLAN_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_predictor_contract_freeze_v2.py"
)
PROVENANCE_PATH: Final = "src/la_heat/provenance.py"

SCHEMA_VERSION: Final = 2
ALGORITHM_VERSION: Final = "portable-predictor-contract-freeze-v2"
STATE: Final = (
    "decision_complete_candidate_rules_frozen_contract_deferred_predictor_closed"
)
OUTCOME: Final = "defer_for_geography_worldcover_support_and_sentinel_calibration_evidence"
EXPERIMENT_ID: Final = "la_to_three_city_zero_shot_v1"
NEXT_SAFE_STAGE: Final = (
    "publish_tracked_only_plan_v12_for_missing_support_and_calibration_evidence"
)

V10_PUBLICATION_COMMIT: Final = "975c155d625de4c9912b9cbf1b5ec710e945bc07"
SOURCE_PUBLICATION_COMMIT: Final = "d41cc58078b23b3b4e7295c38c16d86ff02f5974"
SOURCE_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
)
V1_PUBLICATION_COMMIT: Final = "47a626f6fc0a6577148cc731bb00d21f5387f20a"
V1_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.json"
)
WATER_PUBLICATION_COMMIT: Final = "91a31fd9e1793bbfa9c9f751459fc73d0e0bbb4c"
WATER_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
MODEL_LOCK_PUBLICATION_COMMIT: Final = "8e6a0e7546fb54d9b8fa976b35ad3a4084f6ceb8"
MODEL_LOCK_PATH: Final = "manifests/model_lock/MODEL_LOCK.json"

EXPERIMENT_PATH: Final = "configs/multicity/experiment.toml"
RESEARCH_PATH: Final = "configs/research.toml"
SENTINEL_CONFIG_PATH: Final = "configs/sentinel_features.toml"
CITY_CONFIG_PATHS: Final = (
    "configs/multicity/cities/los_angeles_ca.toml",
    "configs/multicity/cities/phoenix_az.toml",
    "configs/multicity/cities/houston_tx.toml",
    "configs/multicity/cities/chicago_il.toml",
)

CODE_PATHS: Final = tuple(
    dict.fromkeys(
        (
            CONFIG_PATH,
            EXPERIMENT_PATH,
            RESEARCH_PATH,
            SENTINEL_CONFIG_PATH,
            *CITY_CONFIG_PATHS,
            PLAN_MODULE_PATH,
            PLAN_SCRIPT_PATH,
            MODULE_PATH,
            SCRIPT_PATH,
            PROVENANCE_PATH,
        )
    )
)

SOURCE_OUTPUT_RECORDS: Final = {
    "manifests/multicity/cities/houston_tx/geography/GEOGRAPHY.json": {
        "bytes": 5_523,
        "file_sha256": "f89fd246854baa670ecc7a32c20672922be5327e1b89abce8caa07a9ccb14906",
        "commit_sha256": "1c00bb64c1eaa6ce83fe44b377fd40235dfe4108ce8535839cb94a7caf43711f",
        "state": "complete_target_blind_public_geography",
    },
    "manifests/multicity/cities/chicago_il/geography/GEOGRAPHY.json": {
        "bytes": 5_520,
        "file_sha256": "6fadf3355dda0ca70a5ef0f6201f84d38f8df29a588bdddc6fac076238e50b25",
        "commit_sha256": "10a2f41134becd5e45697fa0e95c43e58665155c39a8ec02391c7003e0b01c15",
        "state": "complete_target_blind_public_geography",
    },
    "manifests/multicity/cities/houston_tx/source_footprints/SOURCE_FOOTPRINTS.json": {
        "bytes": 14_379,
        "file_sha256": "af2dd226caf4df1dd14a6ed583ab331c9d791736c47ee35c3fde0332d1172798",
        "commit_sha256": "c1a47ad3ad5cfadb6e0f83ca05e913154383b181e0b2213ee86598597e59f27c",
        "state": "complete_metadata_only_source_not_protocol_locked",
    },
    "manifests/multicity/cities/chicago_il/source_footprints/SOURCE_FOOTPRINTS.json": {
        "bytes": 12_740,
        "file_sha256": "d99e8e320c4c1bb3703cdda0bf98ded183369f41cc0feaee8a568ebfcffdb55c",
        "commit_sha256": "1227a9f9030905c31ab420cdd72b0794984dcfc461acc8e0ab075c8ebac9fb82",
        "state": "complete_metadata_only_source_not_protocol_locked",
    },
    (
        "manifests/multicity/cities/phoenix_az/source_evidence/"
        "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
    ): {
        "bytes": 12_730,
        "file_sha256": "9de8b68e68808d237e24273f1e6e940af5c08d86f3b839ba2102696c796442bc",
        "commit_sha256": "6ee4ddb67bbaa2df545ad13b25f29607647ef84dfff69897ddc2e34ab0f95ae8",
        "state": "complete_city_static_source_evidence",
    },
    (
        "manifests/multicity/cities/houston_tx/source_evidence/"
        "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
    ): {
        "bytes": 12_685,
        "file_sha256": "4a824184d5c75732905bf028836557975d7f3b877cd98d4e37c4d0369164c58d",
        "commit_sha256": "6d0333fa50d93d6bf69a3d5f2a5619ac53d0ae7a4a5526cc4c5a670bdf11e538",
        "state": "complete_city_static_source_evidence",
    },
    (
        "manifests/multicity/cities/chicago_il/source_evidence/"
        "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
    ): {
        "bytes": 12_704,
        "file_sha256": "902e38cee6ddd433b2c17cc8fbf7bcf8aa8ce5dfd140f9a733ae61a6e192a4ad",
        "commit_sha256": "0a16c855648182d72d25cecfbf22da8dcb60aa9cd5f7f4774ee6a7a7941875e8",
        "state": "complete_city_static_source_evidence",
    },
    SOURCE_TERMINAL_PATH: {
        "bytes": 23_042,
        "file_sha256": "31efb7464abf8b44d4f27ebf632299d3d5bcfe121940d429c6abeff040ae64ba",
        "commit_sha256": "961764d4d512cd0e706f743f7fc2a2a99858d7f8618f4e87129e8490089c14f1",
        "state": "complete_target_blind_portable_predictor_source_evidence",
    },
}

PHOENIX_FOOTPRINT_RECORD: Final = {
    "path": "manifests/multicity/cities/phoenix_az/source_footprints/SOURCE_FOOTPRINTS.json",
    "bytes": 18_861,
    "file_sha256": "76a667f559a98bf6281d7f8af71fab18c2fac8d3701ea91813eef8ff8ef479df",
    "commit_sha256": "a6f287daa22c41d893519dc751848c426b819dcf4ae00d33012fbf11c6073ed7",
    "state": "complete_metadata_only_source_not_protocol_locked",
    "publication_git_commit": "44ecdcd2e48c68fa1c67a7ff0c2d1a54d5d3a785",
}

STATIC_FEATURES: Final = (
    "nlcd_open_water_fraction",
    "nlcd_developed_open_fraction",
    "nlcd_developed_low_fraction",
    "nlcd_developed_high_fraction",
    "nlcd_barren_fraction",
    "nlcd_forest_fraction",
    "nlcd_shrub_grass_fraction",
    "nlcd_agriculture_fraction",
    "nlcd_wetland_fraction",
    "impervious_mean_fraction",
    "impervious_p90_fraction",
    "impervious_at_least_50_fraction",
    "elevation_mean_m",
    "elevation_std_m",
    "slope_mean_degrees",
    "slope_p90_degrees",
    "gshhg_ocean_great_lakes_shore_distance_mean_km",
    "gshhg_ocean_great_lakes_shore_distance_p10_km",
)
CALENDAR_FEATURES: Final = ("calendar_doy_sin", "calendar_doy_cos")
DAYMET_FEATURES: Final = tuple(
    f"daymet_{stem}_{stat}_prev_{window}d"
    for window in (1, 3, 7)
    for stem, stat in (
        ("dayl_s", "mean"),
        ("prcp_mm", "sum"),
        ("srad_w_m2", "mean"),
        ("tmax_c", "mean"),
        ("tmin_c", "mean"),
        ("vp_pa", "mean"),
        ("srad_energy_mj_m2", "sum"),
    )
)
SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
B1_FEATURES: Final = (*CALENDAR_FEATURES, *DAYMET_FEATURES)
M2_FEATURES: Final = (*STATIC_FEATURES, *B1_FEATURES, *SENTINEL_FEATURES)
PHASE1_M2_FEATURES: Final = tuple(
    "pacific_coast_distance_mean_km"
    if value == "gshhg_ocean_great_lakes_shore_distance_mean_km"
    else "pacific_coast_distance_p10_km"
    if value == "gshhg_ocean_great_lakes_shore_distance_p10_km"
    else value
    for value in M2_FEATURES
)

V1_BLOCKERS: Final = (
    "houston_source_footprint_manifest_absent",
    "chicago_source_footprint_manifest_absent",
    "phoenix_nlcd_source_family_absent",
    "phoenix_terrain_content_and_schema_unfrozen",
)
UNRESOLVED_BLOCKERS: Final = (
    "four_city_geography_contract_and_los_angeles_parity_evidence_absent",
    "four_city_worldcover_item_mosaic_and_eligible_support_evidence_absent",
    "external_city_sentinel_asset_calibration_smoke_evidence_absent",
)


class PortablePredictorContractFreezeV2Error(ValueError):
    """Raised when the V2 decision cannot be proven target-blind and exact."""


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortablePredictorContractFreezeV2Error(f"{label} must be an object.")
    return value


def _run_git(
    project_root: Path,
    *arguments: str,
    binary: bool = False,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode not in accepted_returncodes:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise PortablePredictorContractFreezeV2Error(
            f"Git authentication failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def _git_regular_blob(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> tuple[bytes, str, str]:
    listing = _run_git(project_root, "ls-tree", commit, "--", relative_path)
    assert isinstance(listing, str)
    fields = listing.rstrip("\n").split(maxsplit=3)
    if (
        len(fields) != 4
        or fields[0] not in {"100644", "100755"}
        or fields[1] != "blob"
        or fields[3] != relative_path
    ):
        raise PortablePredictorContractFreezeV2Error(
            f"Expected one regular historical Git blob: {commit}:{relative_path}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", binary=True)
    assert isinstance(raw, bytes)
    return raw, fields[2], fields[0]


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortablePredictorContractFreezeV2Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise PortablePredictorContractFreezeV2Error(
            f"Authenticated JSON internal commit is invalid: {label}"
        )
    return payload


def _historical_json(
    project_root: Path,
    *,
    commit: str,
    path: str,
    expected_bytes: int,
    expected_sha256: str,
    expected_commit_sha256: str,
    expected_state: str,
) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = _git_regular_blob(project_root, commit=commit, relative_path=path)
    if len(raw) != expected_bytes or _sha256_bytes(raw) != expected_sha256:
        raise PortablePredictorContractFreezeV2Error(
            f"Historical prerequisite bytes changed: {commit}:{path}"
        )
    payload = _json_from_bytes(raw, label=f"{commit}:{path}")
    if (
        payload.get("commit_sha256") != expected_commit_sha256
        or payload.get("state") != expected_state
    ):
        raise PortablePredictorContractFreezeV2Error(
            f"Historical prerequisite identity changed: {commit}:{path}"
        )
    return payload, raw


def _read_config(path: str | Path = CONFIG_PATH) -> tuple[dict[str, Any], bytes]:
    config_path = Path(path)
    raw = config_path.read_bytes()
    if len(raw) != CONFIG_BYTES or _sha256_bytes(raw) != CONFIG_SHA256:
        raise PortablePredictorContractFreezeV2Error("The exact V2 config bytes changed.")
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PortablePredictorContractFreezeV2Error("The V2 config is not valid TOML.") from exc
    _validate_config(payload)
    return payload, raw


def _validate_config(config: Mapping[str, Any]) -> None:
    decision = _require_mapping(config.get("decision"), label="decision")
    if decision != {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "decision_id": "portable_predictor_contract_freeze_v2",
        "decision_date": "2026-08-03",
        "scope": (
            "target-blind four-city portable predictor source calibration aggregation "
            "timing feature and missingness contract freeze"
        ),
        "state": STATE,
        "outcome": OUTCOME,
        "experiment_id": EXPERIMENT_ID,
    }:
        raise PortablePredictorContractFreezeV2Error("V2 decision identity changed.")
    registry = _require_mapping(config.get("feature_registry"), label="feature_registry")
    observed = (
        tuple(_require_mapping(registry.get("static"), label="static registry")["names"]),
        tuple(_require_mapping(registry.get("calendar"), label="calendar registry")["names"]),
        tuple(_require_mapping(registry.get("daymet"), label="daymet registry")["names"]),
        tuple(_require_mapping(registry.get("sentinel"), label="sentinel registry")["names"]),
    )
    if observed != (STATIC_FEATURES, CALENDAR_FEATURES, DAYMET_FEATURES, SENTINEL_FEATURES):
        raise PortablePredictorContractFreezeV2Error("The exact V2 feature order changed.")
    names = (*observed[0], *observed[1], *observed[2], *observed[3])
    if (
        len(names) != 46
        or len(set(names)) != 46
        or registry.get("total_model_feature_count") != 46
        or registry.get("complete_feature_count") != 20
        or registry.get("dynamic_imputed_feature_count") != 26
        or registry.get("b1_feature_count") != 23
        or registry.get("m2_feature_count") != 46
    ):
        raise PortablePredictorContractFreezeV2Error("V2 feature counts changed.")
    forbidden_tokens = ("pacific_coast", "city_id", "tract_geoid", "latitude", "longitude")
    if any(token in name for name in names for token in forbidden_tokens):
        raise PortablePredictorContractFreezeV2Error("A prohibited predictor entered V2.")
    sentinel = _require_mapping(config.get("sentinel_contract"), label="sentinel_contract")
    if (
        sentinel.get("provider") != "ESA Copernicus via Microsoft Planetary Computer"
        or sentinel.get("stac_collection") != "sentinel-2-l2a"
        or "EarthSearch" not in str(sentinel.get("prohibited_provider_collection"))
        or sentinel.get("boa_offset_applied_exactly_once") is not True
        or sentinel.get("global_scene_cloud_filter") is not False
        or sentinel.get("window_start_days_before_target") != 60
        or sentinel.get("window_end_days_before_target") != 1
    ):
        raise PortablePredictorContractFreezeV2Error("Sentinel portability lock changed.")
    support = _require_mapping(config.get("eligible_support"), label="eligible_support")
    if (
        support.get("dataset") != "ESA WorldCover 10 m 2020 v100"
        or support.get("excluded_water_classes") != [80]
        or support.get("denominator_invariant_across_dates") is not True
        or support.get("same_support_required_for_static_daymet_sentinel_and_landsat_target")
        is not True
    ):
        raise PortablePredictorContractFreezeV2Error("Eligible-support lock changed.")
    daymet = _require_mapping(config.get("daymet_contract"), label="daymet_contract")
    if (
        daymet.get("windows_days") != [1, 3, 7]
        or daymet.get("latest_source_offset_days") != -1
        or daymet.get("missing_source_days_are_never_filled") is not True
    ):
        raise PortablePredictorContractFreezeV2Error("Daymet timing lock changed.")
    water = _require_mapping(
        _require_mapping(config.get("static_contract"), label="static_contract").get(
            "water_distance"
        ),
        label="water_distance",
    )
    if (
        water.get("source_and_point_algorithm_locked") is not True
        or water.get("phase1_pacific_feature_alias_allowed") is not False
        or water.get("aggregation_statistics") != ["mean", "p10"]
    ):
        raise PortablePredictorContractFreezeV2Error(
            "Candidate water-distance rules changed."
        )
    unresolved = _require_mapping(
        config.get("unresolved_evidence"), label="unresolved_evidence"
    )
    if unresolved != {
        "required_blockers": list(UNRESOLVED_BLOCKERS),
        "all_blockers_must_remain_target_blind": True,
        "worldcover_evidence_must_bind_exact_item_assets_and_30m_support_hashes": True,
        "sentinel_smoke_must_read_no_landsat_thermal_or_target_qa_value": True,
        "sentinel_smoke_must_test_real_product_metadata_and_small_native_dn_windows": True,
        "geography_review_must_not_use_target_or_predictor_performance": True,
    }:
        raise PortablePredictorContractFreezeV2Error(
            "The exact unresolved V2 blocker contract changed."
        )
    locks = _require_mapping(config.get("locks"), label="locks")
    if locks != {
        "portable_water_distance_source_locked": True,
        "portable_water_distance_algorithm_locked": True,
        "portable_predictor_source_and_calibration_contract_locked": False,
        "portable_feature_names_frozen": False,
        "portable_water_distance_feature_names_frozen": False,
        "candidate_46_feature_registry_bound_for_evidence_stage": True,
        "predictor_build_authorized": False,
        "model_fit_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }:
        raise PortablePredictorContractFreezeV2Error("V2 locks changed.")
    next_gate = _require_mapping(config.get("next_gate"), label="next_gate")
    if next_gate != {
        "stage_id": NEXT_SAFE_STAGE,
        "v2_terminal_is_append_only_and_may_not_be_overwritten": True,
        "separate_tracked_only_transition_required": True,
        (
            "v12_may_authorize_only_target_blind_geography_worldcover_"
            "and_sentinel_calibration_evidence"
        ): True,
        "v12_may_not_authorize_predictor_construction_model_fit_or_target_access": True,
        "source_city_labels_may_not_be_opened_by_predictor_builder": True,
        "external_target_and_qa_values_remain_sealed": True,
        "successful_evidence_requires_a_separate_v3_contract_decision": True,
        (
            "predictor_build_model_fit_protocol_promotion_prediction_commit_"
            "and_evaluation_require_later_gates"
        ): True,
    }:
        raise PortablePredictorContractFreezeV2Error("The V12 next gate changed.")
    access = _require_mapping(config.get("access_contract"), label="access_contract")
    if access.get("network_requests") != 0 or any(
        access.get(key) is not False
        for key in (
            "untracked_file_contents_opened",
            "ignored_paths_requested",
            "source_raster_or_archive_payload_opened",
            "geometry_opened",
            "eligible_land_grid_opened",
            "predictor_values_opened_or_computed",
            "predictor_construction_performed",
            "model_fit_performed",
            "model_predictions_computed",
            "external_target_or_qa_values_read",
            "landsat_thermal_values_read",
            "final_evaluation_outputs_opened",
        )
    ):
        raise PortablePredictorContractFreezeV2Error("V2 access boundary changed.")


def expected_plan_authorization_scope() -> dict[str, Any]:
    return {
        "decision_id": "portable_predictor_contract_freeze_v2",
        "decision_runtime_paths": list(CODE_PATHS),
        "tracked_read_set": {
            "v10_plan": {
                "path": PLAN_PATH,
                "bytes": 45_470,
                "file_sha256": "bdd2a1c75d397116f2c9e9f00e7e673dc83581e5b3b707b9bf5318e815cb3c28",
                "commit_sha256": "61359cbec70f2f4f549ed177abdca8b06b3459e3bb21d3f3d46e75d99001ec26",
                "publication_git_commit": V10_PUBLICATION_COMMIT,
            },
            "source_evidence_v1": {
                "path": SOURCE_TERMINAL_PATH,
                "bytes": 23_042,
                "file_sha256": SOURCE_OUTPUT_RECORDS[SOURCE_TERMINAL_PATH]["file_sha256"],
                "commit_sha256": SOURCE_OUTPUT_RECORDS[SOURCE_TERMINAL_PATH]["commit_sha256"],
                "publication_git_commit": SOURCE_PUBLICATION_COMMIT,
            },
            "contract_v1": {
                "path": V1_TERMINAL_PATH,
                "bytes": 12_934,
                "file_sha256": "794e85c2ea5ad76b84c5e6e7be0999bc5939ab85d9dc7df773406f9802fe6127",
                "commit_sha256": "75b368d7f71c7af5af10317f996595f629e4dacdbbf62b3dc79c3ac0c5eb3e3d",
                "publication_git_commit": V1_PUBLICATION_COMMIT,
            },
            "water_distance_v2": {
                "path": WATER_TERMINAL_PATH,
                "bytes": 18_541,
                "file_sha256": "a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b",
                "commit_sha256": "2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51",
                "publication_git_commit": WATER_PUBLICATION_COMMIT,
            },
            "phase1_model_lock": {
                "path": MODEL_LOCK_PATH,
                "bytes": 12_162,
                "file_sha256": "bf77762bbd1838be2b67e8461c5f99aad1c2ebf36b4f3b53b25dac1801a81245",
                "commit_sha256": "584ccfcb6a32a5a9c380e6e029f5205b91b21684ca6655f240eb72d49e76115b",
                "publication_git_commit": MODEL_LOCK_PUBLICATION_COMMIT,
                "read_scope": "feature_names_and_fixed_model_settings_only",
            },
        },
        "source_output_records": deepcopy(SOURCE_OUTPUT_RECORDS),
        "configuration": {
            "path": CONFIG_PATH,
            "bytes": CONFIG_BYTES,
            "sha256": CONFIG_SHA256,
        },
        "network_requests": 0,
        "tracked_files_and_historical_git_blobs_only": True,
        "untracked_file_contents_allowed": False,
        "source_payload_or_geometry_allowed": False,
        "predictor_or_model_value_allowed": False,
        "external_target_or_qa_value_allowed": False,
        "final_evaluation_output_allowed": False,
        "decision_output_path": OUTPUT_PATH,
        "append_only_output": True,
        "predictor_construction_allowed": False,
        "model_fitting_allowed": False,
        "protocol_promotion_allowed": False,
    }


def _source_evidence_inputs(project_root: Path) -> dict[str, Any]:
    terminal_record = SOURCE_OUTPUT_RECORDS[SOURCE_TERMINAL_PATH]
    terminal, _ = _historical_json(
        project_root,
        commit=SOURCE_PUBLICATION_COMMIT,
        path=SOURCE_TERMINAL_PATH,
        expected_bytes=int(terminal_record["bytes"]),
        expected_sha256=str(terminal_record["file_sha256"]),
        expected_commit_sha256=str(terminal_record["commit_sha256"]),
        expected_state=str(terminal_record["state"]),
    )
    expected_paths = list(SOURCE_OUTPUT_RECORDS)
    if terminal.get("tracked_output_paths") != expected_paths:
        raise PortablePredictorContractFreezeV2Error(
            "Source-evidence terminal output set changed."
        )
    checkpoints = _require_mapping(
        terminal.get("tracked_checkpoints"), label="source checkpoints"
    )
    if set(checkpoints) != set(expected_paths[:-1]):
        raise PortablePredictorContractFreezeV2Error(
            "Source-evidence checkpoint set changed."
        )
    city_records: dict[str, Any] = {}
    for path, record in SOURCE_OUTPUT_RECORDS.items():
        payload, _ = _historical_json(
            project_root,
            commit=SOURCE_PUBLICATION_COMMIT,
            path=path,
            expected_bytes=int(record["bytes"]),
            expected_sha256=str(record["file_sha256"]),
            expected_commit_sha256=str(record["commit_sha256"]),
            expected_state=str(record["state"]),
        )
        if path != SOURCE_TERMINAL_PATH:
            checkpoint = _require_mapping(checkpoints.get(path), label=path)
            expected_checkpoint = {
                "path": path,
                "bytes": record["bytes"],
                "sha256": record["file_sha256"],
                "commit_sha256": record["commit_sha256"],
                "state": record["state"],
            }
            if checkpoint != expected_checkpoint:
                raise PortablePredictorContractFreezeV2Error(
                    f"Source checkpoint identity changed: {path}"
                )
        if "/source_evidence/" in path:
            city_id = str(payload.get("city_id"))
            families = _require_mapping(payload.get("source_families"), label=path)
            nlcd = _require_mapping(
                families.get("nlcd_land_cover_and_imperviousness"), label=f"{city_id} NLCD"
            )
            terrain = _require_mapping(
                families.get("terrain_windows"), label=f"{city_id} terrain"
            )
            if (
                nlcd.get("evidence_complete_for_v2_contract_review") is not True
                or terrain.get("evidence_complete_for_v2_contract_review") is not True
                or terrain.get("content_sha256_frozen") is not True
                or terrain.get("raster_schema_verified") is not True
                or payload.get("target_values_status") != "sealed"
            ):
                raise PortablePredictorContractFreezeV2Error(
                    f"Static source evidence is incomplete for {city_id}."
                )
            city_records[city_id] = {
                "path": path,
                "file_sha256": record["file_sha256"],
                "commit_sha256": record["commit_sha256"],
                "nlcd_source_count": len(nlcd.get("sources", [])),
                "terrain_source_count": len(terrain.get("sources", [])),
                "candidate_rules": payload.get("candidate_downstream_rules_not_executed"),
            }
    if set(city_records) != {"phoenix_az", "houston_tx", "chicago_il"}:
        raise PortablePredictorContractFreezeV2Error("Three city supplements are required.")
    if terminal.get("access_contract") != {
        "external_target_or_qa_values_read": False,
        "landsat_thermal_values_read": False,
        "landsat_target_qa_values_read": False,
        "external_lst_values_read": False,
        "predictor_construction_performed": False,
        "model_fit_performed": False,
        "model_predictions_computed": False,
        "final_evaluation_outputs_opened": False,
        "only_public_source_metadata_and_static_predictor_sources_opened": True,
    }:
        raise PortablePredictorContractFreezeV2Error(
            "Source-evidence target-blind access record changed."
        )
    return {
        "terminal": {
            "path": SOURCE_TERMINAL_PATH,
            "publication_git_commit": SOURCE_PUBLICATION_COMMIT,
            "bytes": terminal_record["bytes"],
            "file_sha256": terminal_record["file_sha256"],
            "commit_sha256": terminal_record["commit_sha256"],
            "state": terminal_record["state"],
        },
        "cities": city_records,
    }


def _validate_historical_prerequisites(project_root: Path) -> dict[str, Any]:
    v1, _ = _historical_json(
        project_root,
        commit=V1_PUBLICATION_COMMIT,
        path=V1_TERMINAL_PATH,
        expected_bytes=12_934,
        expected_sha256="794e85c2ea5ad76b84c5e6e7be0999bc5939ab85d9dc7df773406f9802fe6127",
        expected_commit_sha256="75b368d7f71c7af5af10317f996595f629e4dacdbbf62b3dc79c3ac0c5eb3e3d",
        expected_state="decision_complete_contract_freeze_deferred_predictor_closed",
    )
    gaps = _require_mapping(v1.get("evidence_gaps"), label="V1 evidence gaps")
    if tuple(gaps.get("observed_blockers", [])) != V1_BLOCKERS:
        raise PortablePredictorContractFreezeV2Error("Historical V1 blockers changed.")
    water, _ = _historical_json(
        project_root,
        commit=WATER_PUBLICATION_COMMIT,
        path=WATER_TERMINAL_PATH,
        expected_bytes=18_541,
        expected_sha256="a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b",
        expected_commit_sha256="2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51",
        expected_state="decision_complete_source_and_algorithm_frozen_predictor_closed",
    )
    water_locks = _require_mapping(water.get("locks"), label="water locks")
    algorithm = _require_mapping(water.get("algorithm_lock"), label="water algorithm")
    if (
        water_locks.get("source_lock_created") is not True
        or water_locks.get("algorithm_lock_created") is not True
        or algorithm.get("tract_aggregation_frozen") is not False
        or algorithm.get("feature_names_frozen") is not False
        or algorithm.get("predictor_construction_authorized") is not False
        or algorithm.get("phase1_pacific_feature_alias_allowed") is not False
    ):
        raise PortablePredictorContractFreezeV2Error("Water-distance lock changed.")
    model_lock, _ = _historical_json(
        project_root,
        commit=MODEL_LOCK_PUBLICATION_COMMIT,
        path=MODEL_LOCK_PATH,
        expected_bytes=12_162,
        expected_sha256="bf77762bbd1838be2b67e8461c5f99aad1c2ebf36b4f3b53b25dac1801a81245",
        expected_commit_sha256="584ccfcb6a32a5a9c380e6e029f5205b91b21684ca6655f240eb72d49e76115b",
        expected_state="frozen_for_one_time_2025_evaluation",
    )
    models = _require_mapping(model_lock.get("models"), label="Phase I models")
    b1 = _require_mapping(models.get("B1"), label="Phase I B1")
    m2 = _require_mapping(models.get("M2"), label="Phase I M2")
    if tuple(b1.get("feature_names", [])) != B1_FEATURES:
        raise PortablePredictorContractFreezeV2Error("Phase I B1 feature order changed.")
    if tuple(m2.get("feature_names", [])) != PHASE1_M2_FEATURES:
        raise PortablePredictorContractFreezeV2Error("Phase I M2 feature order changed.")
    return {
        "contract_v1": {
            "publication_git_commit": V1_PUBLICATION_COMMIT,
            "observed_blockers": list(V1_BLOCKERS),
            "blockers_now_filled_by_source_evidence": True,
        },
        "water_distance_v2": {
            "publication_git_commit": WATER_PUBLICATION_COMMIT,
            "source_lock_commit_sha256": _canonical_sha256(water["source_lock"]),
            "algorithm_lock_commit_sha256": _canonical_sha256(water["algorithm_lock"]),
            "source_and_point_algorithm_remain_exact": True,
        },
        "phase1_model_lock": {
            "publication_git_commit": MODEL_LOCK_PUBLICATION_COMMIT,
            "commit_sha256": model_lock["commit_sha256"],
            "b1_feature_order_reused_exactly": True,
            "m2_feature_order_changed_only_at_two_water_distance_names": True,
        },
    }


def _feature_record(name: str, config: Mapping[str, Any]) -> dict[str, Any]:
    if name in STATIC_FEATURES:
        static = True
        timing = "static"
        missingness = "must_be_finite_no_imputation"
        if name.startswith("nlcd_") or name.startswith("impervious_"):
            family = "land_use"
            source = "NLCD 2016 original release"
            units = "fraction"
        elif name.startswith("elevation_"):
            family = "geography"
            source = "SRTM GL1 v3"
            units = "metre"
        elif name.startswith("slope_"):
            family = "geography"
            source = "SRTM GL1 v3 derived Horn slope"
            units = "degree"
        else:
            family = "geography"
            source = "GSHHG 2.3.7 locked ocean/Great-Lakes linework"
            units = "km"
    elif name in CALENDAR_FEATURES:
        static = False
        timing = "known_at_00_00_local_target_date"
        missingness = "must_be_finite_no_imputation"
        family = "calendar"
        source = "civil target date"
        units = "unitless"
    elif name in DAYMET_FEATURES:
        static = False
        timing = "complete_calendar_window_ending_d_minus_1"
        missingness = "train_only_median_if_eligible_row_has_missing_dynamic_value"
        family = "weather"
        source = "Daymet V4 R1"
        if "dayl_s" in name:
            units = "second_per_day"
        elif "prcp_mm" in name:
            units = "mm"
        elif "srad_w_m2" in name:
            units = "W_m-2"
        elif "tmax_c" in name or "tmin_c" in name:
            units = "degree_C"
        elif "vp_pa" in name:
            units = "Pa"
        else:
            units = "MJ_m-2"
    elif name in SENTINEL_FEATURES:
        static = False
        timing = "physical_acquisitions_d_minus_60_through_d_minus_1"
        missingness = "all_five_or_none_then_train_only_median"
        family = "satellite"
        source = "Sentinel-2 L2A via Microsoft Planetary Computer"
        units = "unitless"
    else:
        raise PortablePredictorContractFreezeV2Error(f"Unknown feature: {name}")
    return {
        "feature_name": name,
        "family": family,
        "source": source,
        "units": units,
        "static": static,
        "timing": timing,
        "missingness": missingness,
        "b1_transfer": name in B1_FEATURES,
        "m2_transfer": True,
    }


def _code_records_at_commit(project_root: Path, *, commit: str) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for relative in CODE_PATHS:
        raw, oid, mode = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=relative,
        )
        records[relative] = {
            "sha256": _sha256_bytes(raw),
            "bytes": len(raw),
            "git_blob_oid": oid,
            "git_mode": mode,
        }
    return records


def _git_preflight(
    project_root: Path,
    *,
    required_paths: Sequence[str],
    expected_head: str | None = None,
) -> str:
    branch = _run_git(project_root, "branch", "--show-current")
    head = _run_git(project_root, "rev-parse", "HEAD")
    origin = _run_git(project_root, "rev-parse", "origin/main")
    status = _run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(branch, str) and isinstance(head, str) and isinstance(origin, str)
    assert isinstance(status, bytes)
    normalized_head = head.strip()
    if branch.strip() != "main" or normalized_head != origin.strip() or status:
        raise PortablePredictorContractFreezeV2Error(
            "V2 requires clean synchronized main."
        )
    if expected_head is not None and normalized_head != expected_head:
        raise PortablePredictorContractFreezeV2Error("HEAD changed during V2.")
    for ancestor in (
        V10_PUBLICATION_COMMIT,
        SOURCE_PUBLICATION_COMMIT,
        V1_PUBLICATION_COMMIT,
        WATER_PUBLICATION_COMMIT,
        MODEL_LOCK_PUBLICATION_COMMIT,
    ):
        if not _is_ancestor(project_root, ancestor, normalized_head):
            raise PortablePredictorContractFreezeV2Error(
                f"Required historical evidence is not an ancestor: {ancestor}"
            )
    for relative in dict.fromkeys(required_paths):
        _, oid, _ = _git_regular_blob(
            project_root,
            commit=normalized_head,
            relative_path=relative,
        )
        worktree_oid = _run_git(
            project_root,
            "hash-object",
            f"--path={relative}",
            "--",
            relative,
        )
        assert isinstance(worktree_oid, str)
        if worktree_oid.strip() != oid:
            raise PortablePredictorContractFreezeV2Error(
                f"Required path differs from HEAD: {relative}"
            )
    return normalized_head


def _authenticate_plan(project_root: Path, *, head: str) -> tuple[dict[str, Any], str]:
    from la_heat.multicity.plan_predictor_contract_transition_v11 import (
        _locate_v11_publication_commit,
        authenticate_historical_v11_payload,
    )

    raw = (project_root / PLAN_PATH).read_bytes()
    payload = _json_from_bytes(raw, label="canonical planning v11")
    publication = _locate_v11_publication_commit(
        project_root,
        payload,
        current_head=head,
    )
    authenticated = authenticate_historical_v11_payload(
        project_root,
        payload,
        publication_commit=publication,
        current_head=head,
    )
    if authenticated.get(
        "portable_predictor_contract_freeze_v2_authorization_scope"
    ) != expected_plan_authorization_scope():
        raise PortablePredictorContractFreezeV2Error("V11 authorization scope changed.")
    return authenticated, publication


def _build_payload(
    *,
    config: Mapping[str, Any],
    config_raw: bytes,
    plan: Mapping[str, Any],
    plan_publication: str,
    precondition_head: str,
    source_evidence: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    code_files: Mapping[str, Any],
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    registry = [_feature_record(name, config) for name in M2_FEATURES]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": STATE,
        "outcome": OUTCOME,
        "generated_at_utc": generated_at_utc
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "experiment_id": EXPERIMENT_ID,
        "decision_scope": config["decision"]["scope"],
        "plan_authorization": {
            "path": PLAN_PATH,
            "publication_git_commit": plan_publication,
            "schema_version": plan["schema_version"],
            "algorithm_version": plan["algorithm_version"],
            "commit_sha256": plan["commit_sha256"],
            "scope": expected_plan_authorization_scope(),
        },
        "writer_precondition": {
            "branch": "main",
            "git_head": precondition_head,
            "origin_main": precondition_head,
            "worktree_clean": True,
            "head_equals_local_origin_main": True,
        },
        "config": {
            "path": CONFIG_PATH,
            "bytes": len(config_raw),
            "sha256": _sha256_bytes(config_raw),
            "semantic_sha256": _canonical_sha256(config),
        },
        "prerequisites": deepcopy(dict(prerequisites)),
        "source_evidence": deepcopy(dict(source_evidence)),
        "unresolved_evidence": deepcopy(config["unresolved_evidence"]),
        "contract": {
            "eligible_support": deepcopy(config["eligible_support"]),
            "static": deepcopy(config["static_contract"]),
            "calendar": deepcopy(config["calendar_contract"]),
            "daymet": deepcopy(config["daymet_contract"]),
            "sentinel": deepcopy(config["sentinel_contract"]),
            "missingness": deepcopy(config["missingness_contract"]),
        },
        "feature_registry": {
            "key_columns": list(config["feature_registry"]["key_columns"]),
            "feature_count": len(registry),
            "features": registry,
            "feature_order": list(M2_FEATURES),
            "semantic_sha256": _canonical_sha256(registry),
            "audit_only_features": list(
                config["feature_registry"]["audit_only_features"]
            ),
            "candidate_rules_bound_for_evidence_stage": True,
            "formal_feature_names_frozen": False,
            "phase1_water_name_replacements": {
                "pacific_coast_distance_mean_km": (
                    "gshhg_ocean_great_lakes_shore_distance_mean_km"
                ),
                "pacific_coast_distance_p10_km": (
                    "gshhg_ocean_great_lakes_shore_distance_p10_km"
                ),
            },
            "phase1_pacific_water_names_are_historical_and_disabled": True,
        },
        "model_contract": {
            **deepcopy(config["model_contract"]),
            "b1_feature_order": list(B1_FEATURES),
            "m2_feature_order": list(M2_FEATURES),
            "uncertainty": deepcopy(config["uncertainty_contract"]),
        },
        "decision": {
            "contract_freeze_passed": False,
            "all_four_v1_evidence_gaps_closed": True,
            "new_v2_blockers_observed": list(UNRESOLVED_BLOCKERS),
            "candidate_rules_and_registry_bound_for_evidence_stage": True,
            "portable_predictor_contract_locked": False,
            "portable_feature_names_frozen": False,
            "portable_water_distance_feature_names_frozen": False,
            "predictor_build_recommended_for_separate_authorization": False,
            "predictor_build_authorized_now": False,
            "model_fit_authorized_now": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "next_safe_stage": config["next_gate"]["stage_id"],
        },
        "locks": deepcopy(config["locks"]),
        "access_contract": deepcopy(config["access_contract"]),
        "next_gate": deepcopy(config["next_gate"]),
        "code_runtime": {
            "algorithm_version": ALGORITHM_VERSION,
            "relative_paths": list(CODE_PATHS),
            "files": deepcopy(dict(code_files)),
            "sha256": _canonical_sha256(code_files),
        },
    }
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _validate_terminal(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    config_raw: bytes,
) -> None:
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != STATE
        or payload.get("outcome") != OUTCOME
        or payload.get("experiment_id") != EXPERIMENT_ID
        or payload.get("commit_sha256") != _canonical_sha256(body)
    ):
        raise PortablePredictorContractFreezeV2Error("V2 terminal identity changed.")
    if payload.get("config") != {
        "path": CONFIG_PATH,
        "bytes": len(config_raw),
        "sha256": _sha256_bytes(config_raw),
        "semantic_sha256": _canonical_sha256(config),
    }:
        raise PortablePredictorContractFreezeV2Error("V2 config binding changed.")
    registry = _require_mapping(payload.get("feature_registry"), label="terminal registry")
    if (
        registry.get("feature_count") != 46
        or tuple(registry.get("feature_order", [])) != M2_FEATURES
        or len(registry.get("features", [])) != 46
        or registry.get("candidate_rules_bound_for_evidence_stage") is not True
        or registry.get("formal_feature_names_frozen") is not False
        or registry.get("phase1_pacific_water_names_are_historical_and_disabled")
        is not True
        or registry.get("phase1_water_name_replacements")
        != {
            "pacific_coast_distance_mean_km": (
                "gshhg_ocean_great_lakes_shore_distance_mean_km"
            ),
            "pacific_coast_distance_p10_km": (
                "gshhg_ocean_great_lakes_shore_distance_p10_km"
            ),
        }
    ):
        raise PortablePredictorContractFreezeV2Error("V2 terminal feature registry changed.")
    if payload.get("unresolved_evidence") != config["unresolved_evidence"]:
        raise PortablePredictorContractFreezeV2Error(
            "V2 terminal unresolved evidence changed."
        )
    if payload.get("locks") != config["locks"]:
        raise PortablePredictorContractFreezeV2Error("V2 terminal locks changed.")
    if payload.get("access_contract") != config["access_contract"]:
        raise PortablePredictorContractFreezeV2Error("V2 terminal access record changed.")
    decision = _require_mapping(payload.get("decision"), label="terminal decision")
    if decision != {
        "contract_freeze_passed": False,
        "all_four_v1_evidence_gaps_closed": True,
        "new_v2_blockers_observed": list(UNRESOLVED_BLOCKERS),
        "candidate_rules_and_registry_bound_for_evidence_stage": True,
        "portable_predictor_contract_locked": False,
        "portable_feature_names_frozen": False,
        "portable_water_distance_feature_names_frozen": False,
        "predictor_build_recommended_for_separate_authorization": False,
        "predictor_build_authorized_now": False,
        "model_fit_authorized_now": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "next_safe_stage": NEXT_SAFE_STAGE,
    }:
        raise PortablePredictorContractFreezeV2Error("V2 decision boundary changed.")


def _validate_terminal_reconstruction(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    config_raw: bytes,
    plan: Mapping[str, Any],
    plan_publication: str,
    source_evidence: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    code_files: Mapping[str, Any],
) -> None:
    generated_at_utc = payload.get("generated_at_utc")
    if not isinstance(generated_at_utc, str):
        raise PortablePredictorContractFreezeV2Error(
            "Published V2 generation time is invalid."
        )
    expected = _build_payload(
        config=config,
        config_raw=config_raw,
        plan=plan,
        plan_publication=plan_publication,
        precondition_head=plan_publication,
        source_evidence=source_evidence,
        prerequisites=prerequisites,
        code_files=code_files,
        generated_at_utc=generated_at_utc,
    )
    if not _strict_equal(payload, expected):
        raise PortablePredictorContractFreezeV2Error(
            "Published V2 differs from its full authenticated reconstruction."
        )


def _require_terminal_generation_precondition(
    *,
    head: str,
    plan_publication: str,
    output_exists: bool,
    check_only: bool,
) -> None:
    if output_exists:
        return
    if check_only:
        raise PortablePredictorContractFreezeV2Error(
            "V2 terminal is absent; run without --check-only only after v11 publication."
        )
    if head != plan_publication:
        raise PortablePredictorContractFreezeV2Error(
            "V2 terminal generation requires HEAD to equal the exact planning-v11 "
            "publication so its publication can be the direct child."
        )


def _publication_commit(project_root: Path, *, plan_publication: str, head: str) -> str:
    additions = _run_git(
        project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        OUTPUT_PATH,
    )
    assert isinstance(additions, str)
    candidates = [line for line in additions.splitlines() if line]
    if len(candidates) != 1:
        raise PortablePredictorContractFreezeV2Error(
            "V2 terminal must have one unique Git addition."
        )
    publication = candidates[0]
    ancestry = _run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    assert isinstance(ancestry, str)
    if ancestry.split() != [publication, plan_publication]:
        raise PortablePredictorContractFreezeV2Error(
            "V2 terminal publication must be the direct child of planning v11."
        )
    delta = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        plan_publication,
        publication,
        binary=True,
    )
    assert isinstance(delta, bytes)
    if delta != b"A\0" + OUTPUT_PATH.encode("utf-8") + b"\0":
        raise PortablePredictorContractFreezeV2Error(
            "V2 publication must add only its terminal."
        )
    if not _is_ancestor(project_root, publication, head):
        raise PortablePredictorContractFreezeV2Error("V2 publication is not on current HEAD.")
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{publication}..{head}",
        "--",
        OUTPUT_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise PortablePredictorContractFreezeV2Error(
            "Append-only V2 terminal changed after publication."
        )
    return publication


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists():
        raise PortablePredictorContractFreezeV2Error(
            "Refusing to overwrite an existing V2 temporary file."
        )
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise PortablePredictorContractFreezeV2Error(
                "Refusing to overwrite an existing append-only V2 terminal."
            ) from exc
        except OSError as exc:
            raise PortablePredictorContractFreezeV2Error(
                "Could not publish the append-only V2 terminal atomically."
            ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def audit_portable_predictor_contract_freeze_v2(
    config_path: str | Path = CONFIG_PATH,
    *,
    output_path: str | Path = OUTPUT_PATH,
    check_only: bool = False,
) -> dict[str, Any]:
    """Create or authenticate the append-only target-blind V2 decision."""

    root = Path(__file__).resolve().parents[3]
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    if destination != (root / OUTPUT_PATH).resolve():
        raise PortablePredictorContractFreezeV2Error(
            "V2 may write only its canonical terminal."
        )
    supplied_config = Path(config_path)
    if not supplied_config.is_absolute():
        supplied_config = root / supplied_config
    if supplied_config.resolve() != (root / CONFIG_PATH).resolve():
        raise PortablePredictorContractFreezeV2Error("V2 requires its exact config path.")
    config, config_raw = _read_config(supplied_config)
    required_paths = (
        *CODE_PATHS,
        PLAN_PATH,
        SOURCE_TERMINAL_PATH,
        V1_TERMINAL_PATH,
        WATER_TERMINAL_PATH,
        MODEL_LOCK_PATH,
    )
    head = _git_preflight(root, required_paths=required_paths)
    plan, plan_publication = _authenticate_plan(root, head=head)
    _require_terminal_generation_precondition(
        head=head,
        plan_publication=plan_publication,
        output_exists=destination.exists(),
        check_only=check_only,
    )
    source_evidence = _source_evidence_inputs(root)
    prerequisites = _validate_historical_prerequisites(root)
    code_files = _code_records_at_commit(root, commit=head)

    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise PortablePredictorContractFreezeV2Error(
                "V2 terminal must be one regular file."
            )
        tracked = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"HEAD:{OUTPUT_PATH}"],
            check=False,
            capture_output=True,
        )
        if tracked.returncode != 0:
            raise PortablePredictorContractFreezeV2Error(
                "An untracked V2 terminal already exists; preserve it for audit."
            )
        raw = destination.read_bytes()
        payload = _json_from_bytes(raw, label="portable predictor contract V2")
        _validate_terminal(payload, config=config, config_raw=config_raw)
        publication = _publication_commit(
            root,
            plan_publication=plan_publication,
            head=head,
        )
        published_raw, _, _ = _git_regular_blob(
            root,
            commit=publication,
            relative_path=OUTPUT_PATH,
        )
        if published_raw != raw:
            raise PortablePredictorContractFreezeV2Error(
                "Published V2 bytes differ from current bytes."
            )
        _validate_terminal_reconstruction(
            payload,
            config=config,
            config_raw=config_raw,
            plan=plan,
            plan_publication=plan_publication,
            source_evidence=source_evidence,
            prerequisites=prerequisites,
            code_files=code_files,
        )
        result = deepcopy(payload)
        result["publication_status"] = "authenticated_git_publication"
        result["publication_git_commit"] = publication
        return result

    payload = _build_payload(
        config=config,
        config_raw=config_raw,
        plan=plan,
        plan_publication=plan_publication,
        precondition_head=head,
        source_evidence=source_evidence,
        prerequisites=prerequisites,
        code_files=code_files,
    )
    _validate_terminal(payload, config=config, config_raw=config_raw)
    _git_preflight(root, required_paths=required_paths, expected_head=head)
    _atomic_write(destination, _expected_json_bytes(payload))
    result = deepcopy(payload)
    result["publication_status"] = "awaiting_git_publication"
    return result
