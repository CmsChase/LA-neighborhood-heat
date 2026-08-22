"""Parent authorization for the target-blind M3 predictor build.

This module is intentionally metadata-only.  It freezes the exact blind-city
key universe and predictor contract, but it does not authorize a runner to
open predictor values.  A later child runtime authorization must bind reviewed
code hashes before the first public predictor value or network read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    CITY_CENTROID_ALGORITHM,
    FEATURE_NAMES,
    REQUIRED_COLUMNS,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-build-parent-authorization-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_PREDICTOR_BUILD_V1_PARENT_AUTHORIZATION.json"
)
PROTOCOL_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json"
)
FEASIBILITY_PATH: Final = Path(
    "manifests/multicity/next_experiment/METADATA_FEASIBILITY_AUDIT.json"
)
SOURCE_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_joint_nested_loso_v1/"
    "SOURCE_NESTED_LOSO_COMPLETE.json"
)
BLIND_CITY_IDS: Final = (
    "seattle_wa",
    "denver_co",
    "atlanta_ga",
    "miami_fl",
)
EXPECTED_CITY_COUNTS: Final = {
    "seattle_wa": {"tract_count": 177, "target_date_count": 54, "row_count": 9_558},
    "denver_co": {"tract_count": 175, "target_date_count": 31, "row_count": 5_425},
    "atlanta_ga": {"tract_count": 173, "target_date_count": 28, "row_count": 4_844},
    "miami_fl": {"tract_count": 128, "target_date_count": 30, "row_count": 3_840},
}
EXPECTED_PROTOCOL_COMMIT: Final = (
    "dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8"
)
EXPECTED_FEASIBILITY_COMMIT: Final = (
    "450ecd604000fcec7f3958e9a15013c74f69c52fddd656e9786c703c62838922"
)
EXPECTED_SOURCE_COMPLETION_COMMIT: Final = (
    "207d45f8fdc7237f6347ed69b1c67733df353a3331e622707e93c4b3f21c34d3"
)


class M3BlindPredictorAuthorizationError(RuntimeError):
    """Raised when the frozen parent authorization contract drifts."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorAuthorizationError(f"{label} escapes the project root.")
    return path


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    committed = dict(payload)
    committed["commit_sha256"] = canonical_sha256(payload)
    return committed


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictorAuthorizationError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictorAuthorizationError(f"{label} is not an object.")
    commit = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(commit, str) or canonical_sha256(body) != commit:
        raise M3BlindPredictorAuthorizationError(f"{label} commit is invalid.")
    return payload


def _file_record(root: Path, path: Path, *, commit_sha256: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if commit_sha256 is not None:
        record["commit_sha256"] = commit_sha256
    return record


def _city_universe(root: Path, feasibility: Mapping[str, Any]) -> list[dict[str, Any]]:
    cities = feasibility.get("cities")
    if not isinstance(cities, Mapping):
        raise M3BlindPredictorAuthorizationError("Feasibility city mapping changed.")
    universe: list[dict[str, Any]] = []
    for city_id in BLIND_CITY_IDS:
        summary = cities.get(city_id)
        if not isinstance(summary, Mapping) or summary.get("passes") is not True:
            raise M3BlindPredictorAuthorizationError(f"{city_id} feasibility changed.")
        checkpoint_record = summary.get("checkpoint")
        if not isinstance(checkpoint_record, Mapping):
            raise M3BlindPredictorAuthorizationError(f"{city_id} checkpoint changed.")
        checkpoint_path = _inside(root, str(checkpoint_record.get("path", "")), label=city_id)
        checkpoint = _read_committed(checkpoint_path, label=f"{city_id} feasibility checkpoint")
        expected = EXPECTED_CITY_COUNTS[city_id]
        dates = checkpoint.get("landsat", {}).get("eligible_unique_physical_dates")
        census = checkpoint.get("census", {})
        landsat = checkpoint.get("landsat", {})
        worldcover = checkpoint.get("worldcover", {})
        if (
            checkpoint.get("commit_sha256") != checkpoint_record.get("commit_sha256")
            or checkpoint_path.stat().st_size != checkpoint_record.get("bytes")
            or sha256_file(checkpoint_path) != checkpoint_record.get("sha256")
            or checkpoint.get("city", {}).get("id") != city_id
            or census.get("primary_tract_count") != expected["tract_count"]
            or landsat.get("eligible_unique_physical_date_count") != expected["target_date_count"]
            or not isinstance(dates, list)
            or len(dates) != expected["target_date_count"]
            or len(set(map(str, dates))) != len(dates)
            or worldcover.get("all_tracts_positive_zone_and_eligible") is not True
            or landsat.get("access_contract", {}).get("landsat_asset_hrefs_read") is not False
            or landsat.get("access_contract", {}).get("landsat_target_or_qa_values_read")
            is not False
        ):
            raise M3BlindPredictorAuthorizationError(f"{city_id} frozen universe changed.")
        universe.append(
            {
                "city_id": city_id,
                **expected,
                "target_dates": list(map(str, dates)),
                "checkpoint": _file_record(
                    root, checkpoint_path, commit_sha256=str(checkpoint["commit_sha256"])
                ),
                "primary_tracts": dict(census["outputs"]["primary_tracts"]),
                "worldcover_support": dict(worldcover["output"]),
            }
        )
    return universe


def build_m3_blind_predictor_parent_authorization(
    project_root: str | Path,
) -> dict[str, Any]:
    """Build the immutable metadata-only parent permit."""

    root = Path(project_root).resolve()
    protocol_path = _inside(root, PROTOCOL_PATH, label="M3 protocol")
    feasibility_path = _inside(root, FEASIBILITY_PATH, label="feasibility audit")
    completion_path = _inside(root, SOURCE_COMPLETION_PATH, label="source completion")
    protocol = _read_committed(protocol_path, label="M3 protocol")
    feasibility = _read_committed(feasibility_path, label="feasibility audit")
    completion = _read_committed(completion_path, label="source nested LOSO completion")
    if (
        protocol.get("commit_sha256") != EXPECTED_PROTOCOL_COMMIT
        or feasibility.get("commit_sha256") != EXPECTED_FEASIBILITY_COMMIT
        or completion.get("commit_sha256") != EXPECTED_SOURCE_COMPLETION_COMMIT
        or tuple(feasibility.get("selection", {}).get("selected_city_ids", ()))
        != BLIND_CITY_IDS
        or tuple(completion.get("blind_test_city_ids", ())) != BLIND_CITY_IDS
        or completion.get("source_only_selection_complete") is not True
        or completion.get("support_and_tie_break_frozen") is not True
        or completion.get("audit", {}).get("blind_test_city_accessed") is not False
        or completion.get("audit", {}).get("blind_predictor_accessed") is not False
    ):
        raise M3BlindPredictorAuthorizationError("Parent scientific anchors changed.")
    universe = _city_universe(root, feasibility)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_build_parent_authorized",
        "inputs": {
            "m3_protocol_lock": _file_record(
                root, protocol_path, commit_sha256=str(protocol["commit_sha256"])
            ),
            "metadata_feasibility_audit": _file_record(
                root, feasibility_path, commit_sha256=str(feasibility["commit_sha256"])
            ),
            "source_nested_loso_completion": _file_record(
                root, completion_path, commit_sha256=str(completion["commit_sha256"])
            ),
        },
        "blind_city_ids": list(BLIND_CITY_IDS),
        "key_universe": {
            "cities": universe,
            "city_count": len(universe),
            "target_date_count": sum(row["target_date_count"] for row in universe),
            "tract_date_row_count": sum(row["row_count"] for row in universe),
            "universe_sha256": canonical_sha256(universe),
        },
        "frozen_source_selection": {
            "joint_candidate_id": completion["selected_joint_candidate_id"],
            "qa_id": completion["selected_qa_id"],
            "m3_candidate_id": completion["selected_m3_candidate_id"],
            "uq_method": completion["selected_uq_method"],
            "risk_method": completion["selected_risk_method"],
            "retuning_after_this_parent": False,
        },
        "predictor_contract": {
            "feature_count": len(FEATURE_NAMES),
            "feature_names": list(FEATURE_NAMES),
            "required_columns": list(REQUIRED_COLUMNS),
            "city_context_feature": "city_centroid_latitude_deg",
            "city_context_algorithm": CITY_CENTROID_ALGORITHM,
            "lagged_sentinel_window_days": 60,
            "satellite_window_ends_before_target_date": True,
        },
        "required_runtime_contract": {
            "append_only_child_runtime_authorization_required": True,
            "child_must_bind_exact_code_and_config_hashes": True,
            "child_must_authenticate_parent_before_each_value_or_network_access": True,
            "resumable_online_acquisition_then_offline_assembly": True,
            "online_acquisition_may_use_public_predictor_sources_only": True,
            "offline_network_and_href_reads": 0,
            "completion_must_authenticate_four_city_key_schema_and_semantics": True,
            "prediction_stage_requires_a_later_independent_authorization": True,
        },
        "permissions": {
            "implement_and_review_blind_predictor_runner_without_value_access": True,
            "read_blind_predictor_values_under_this_parent_alone": False,
            "perform_network_or_href_reads_under_this_parent_alone": False,
            "read_landsat_asset_hrefs_thermal_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
            "change_city_date_key_feature_support_or_selected_model": False,
            "open_blind_target_or_qa_values": False,
        },
        "authorization_access_audit": {
            "blind_predictor_parquet_opened_or_statted": 0,
            "blind_landsat_asset_href_reads": 0,
            "blind_thermal_qa_or_target_values_read": False,
            "network_requests": 0,
            "model_fit_predict_score_or_evaluate": False,
        },
        "next_safe_stage": (
            "implement_review_and_bind_child_runtime_authorization_before_first_"
            "blind_predictor_value_or_network_read"
        ),
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def create_m3_blind_predictor_parent_authorization(
    project_root: str | Path,
    output_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, output_path, label="parent authorization")
    expected_destination = _inside(root, AUTHORIZATION_PATH, label="parent authorization")
    if destination != expected_destination:
        raise M3BlindPredictorAuthorizationError("Parent authorization path changed.")
    if destination.exists():
        raise M3BlindPredictorAuthorizationError("Parent authorization is append-only.")
    payload = build_m3_blind_predictor_parent_authorization(root)
    atomic_json(payload, destination)
    return authenticate_m3_blind_predictor_parent_authorization(root, destination)


def authenticate_m3_blind_predictor_parent_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="parent authorization")
    if path != _inside(root, AUTHORIZATION_PATH, label="parent authorization"):
        raise M3BlindPredictorAuthorizationError("Parent authorization path changed.")
    observed = _read_committed(path, label="blind predictor parent authorization")
    expected = build_m3_blind_predictor_parent_authorization(root)
    if observed != expected:
        raise M3BlindPredictorAuthorizationError("Blind predictor parent authorization drifted.")
    return observed
