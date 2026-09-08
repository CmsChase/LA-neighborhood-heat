"""Append-only scope authorization for blind-city Sentinel/static acquisition.

This authorization is deliberately value-free.  It freezes the exact inputs,
sources, outputs, and safety boundary for the next runtime.  A separate launch
authorization must bind the reviewed executable runner before any raster or
network access occurs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    AUTHORIZATION_PATH as PARENT_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    BLIND_CITY_IDS,
    authenticate_m3_blind_predictor_parent_authorization,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file
from la_heat.sentinel_features import INDEX_COLUMNS, REFLECTANCE_BANDS

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-static-acquisition-auth-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ACQUISITION_V1_AUTHORIZATION.json"
)
SUPPORT_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SUPPORT_COMPLETE.json"
)
METADATA_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_METADATA_COMPLETE.json"
)
SENTINEL_INVENTORY_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_INVENTORY_COMPLETE.json"
)
WATER_FREEZE_PATH: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
PREDICTOR_CONTRACT_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
METADATA_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/inventory"
)
SENTINEL_INVENTORY_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/sentinel_inventory"
)
OUTPUT_ROOT: Final = Path(
    "data/raw/multicity/m3_blind_predictor_build_v1/sentinel_static_acquisition_v1"
)
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/m3_blind_predictor_build_v1/"
    "sentinel_static_acquisition_v1/runtime"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ACQUISITION_COMPLETE.json"
)

EXPECTED_PARENT_COMMIT: Final = (
    "1a704fca3848471dfba16c28bf2dd2e282343af6ac2aa24e3cbbd2ef44d790f8"
)
EXPECTED_SUPPORT_COMMIT: Final = (
    "aa0c35e626e84ab1e8a17e04b8c5da374c3fcd655a601c90b2e8256806d74dbd"
)
EXPECTED_METADATA_COMMIT: Final = (
    "31dcc3f639ccd8a4af040be20dd5ced243cadf7a913b53639a9e7311b7966201"
)
EXPECTED_SENTINEL_INVENTORY_COMMIT: Final = (
    "7052a02df4da25661ea29cb9b5862bd71921ce1e57017a73db87e8f9ca4b10d7"
)
EXPECTED_WATER_FREEZE_COMMIT: Final = (
    "2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51"
)
EXPECTED_CITY_METADATA_COMMITS: Final = {
    "seattle_wa": "37f3ddd17234363f599efc4fe80bde20cf447a1da7f29ba1b2b5ae08fe8510bb",
    "denver_co": "32f828da1a2442d10cc28d326d83d83761ffc27610f792e11e734b78c2ca44eb",
    "atlanta_ga": "7192ed698e3de98da403cb304dc35f5bf70c5891234a320213b8d4aca4190a1e",
    "miami_fl": "ec6f1312a2a768bd6b4a35290d29ceac415a03bc7500cd46105191bd8a195565",
}
EXPECTED_SENTINEL_COUNTS: Final = {
    "seattle_wa": 150,
    "denver_co": 157,
    "atlanta_ga": 157,
    "miami_fl": 75,
}
EXPECTED_SRTM_TILES: Final = {
    "seattle_wa": ("N47W123",),
    "denver_co": ("N39W106", "N39W105"),
    "atlanta_ga": ("N33W085",),
    "miami_fl": ("N25W081",),
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
CODE_PATHS: Final = (
    "scripts/authorize_m3_blind_predictor_sentinel_static_acquisition_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_build_authorization_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_inventory_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_static_acquisition_authorization_v1.py",
    "src/la_heat/multicity/portable_predictor_components.py",
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py",
    "src/la_heat/multicity/portable_sentinel_build.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
)


class M3BlindPredictorSentinelStaticAuthorizationError(RuntimeError):
    """Raised when the frozen Sentinel/static acquisition scope drifts."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorSentinelStaticAuthorizationError(
            f"{label} escapes the project root."
        )
    return path


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictorSentinelStaticAuthorizationError(
            f"Cannot read {label}."
        ) from error
    if not isinstance(payload, dict):
        raise M3BlindPredictorSentinelStaticAuthorizationError(f"{label} is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorSentinelStaticAuthorizationError(
            f"{label} commit is invalid."
        )
    return payload


def _record(root: Path, path: Path, *, commit: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if commit is not None:
        record["commit_sha256"] = commit
    return record


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read_committed(path, label=path.name) != dict(payload):
            raise M3BlindPredictorSentinelStaticAuthorizationError(
                f"Append-only artifact drifted: {path}"
            )
        return
    atomic_json(dict(payload), path)


def _authenticated_input(
    root: Path, path: Path, *, expected_commit: str, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    absolute = _inside(root, path, label=label)
    payload = _read_committed(absolute, label=label)
    if payload.get("commit_sha256") != expected_commit:
        raise M3BlindPredictorSentinelStaticAuthorizationError(f"{label} changed.")
    return payload, _record(root, absolute, commit=expected_commit)


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build the scope permit using JSON/code metadata only."""

    root = Path(project_root).resolve()
    parent = authenticate_m3_blind_predictor_parent_authorization(
        root, PARENT_AUTHORIZATION_PATH
    )
    if parent.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindPredictorSentinelStaticAuthorizationError("Parent commit changed.")
    support, support_record = _authenticated_input(
        root,
        SUPPORT_COMPLETION_PATH,
        expected_commit=EXPECTED_SUPPORT_COMMIT,
        label="blind support completion",
    )
    metadata, metadata_record = _authenticated_input(
        root,
        METADATA_COMPLETION_PATH,
        expected_commit=EXPECTED_METADATA_COMMIT,
        label="blind metadata completion",
    )
    sentinel, sentinel_record = _authenticated_input(
        root,
        SENTINEL_INVENTORY_COMPLETION_PATH,
        expected_commit=EXPECTED_SENTINEL_INVENTORY_COMMIT,
        label="blind Sentinel inventory completion",
    )
    if (
        tuple(support.get("blind_city_ids", ())) != BLIND_CITY_IDS
        or metadata.get("predictor_key_count") != 23_667
        or metadata.get("target_date_count") != 143
        or [row.get("city_id") for row in sentinel.get("city_completions", ())]
        != list(BLIND_CITY_IDS)
    ):
        raise M3BlindPredictorSentinelStaticAuthorizationError("Frozen city scope changed.")

    cities: list[dict[str, Any]] = []
    sentinel_rows = {row["city_id"]: row for row in sentinel["city_completions"]}
    metadata_rows = {row["city_id"]: row for row in metadata["city_completions"]}
    for city_id in BLIND_CITY_IDS:
        marker_path = _inside(
            root, METADATA_ROOT / city_id / "METADATA_COMPLETE.json", label=city_id
        )
        marker = _read_committed(marker_path, label=f"{city_id} metadata marker")
        expected_commit = EXPECTED_CITY_METADATA_COMMITS[city_id]
        if (
            marker.get("commit_sha256") != expected_commit
            or metadata_rows[city_id].get("commit_sha256") != expected_commit
            or marker.get("city_id") != city_id
            or len(marker.get("bbox_wgs84", ())) != 4
            or sentinel_rows[city_id].get("selected_physical_acquisitions")
            != EXPECTED_SENTINEL_COUNTS[city_id]
        ):
            raise M3BlindPredictorSentinelStaticAuthorizationError(
                f"{city_id} frozen metadata changed."
            )
        cities.append(
            {
                "city_id": city_id,
                "metadata": _record(root, marker_path, commit=expected_commit),
                "bbox_wgs84": marker["bbox_wgs84"],
                "sentinel_physical_acquisitions": EXPECTED_SENTINEL_COUNTS[city_id],
                "srtm_tile_ids": list(EXPECTED_SRTM_TILES[city_id]),
            }
        )

    water, water_record = _authenticated_input(
        root,
        WATER_FREEZE_PATH,
        expected_commit=EXPECTED_WATER_FREEZE_COMMIT,
        label="water-distance freeze",
    )
    contract = _read_committed(
        _inside(root, PREDICTOR_CONTRACT_PATH, label="predictor contract"),
        label="predictor contract",
    )
    if (
        water.get("source_lock", {}).get("archive_sha256")
        != "8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41"
        or tuple(contract.get("feature_registry", {}).get("feature_order", ()))
        != tuple(parent["predictor_contract"]["feature_names"])
    ):
        raise M3BlindPredictorSentinelStaticAuthorizationError(
            "Frozen predictor source contract changed."
        )

    code = [_record(root, _inside(root, path, label="code")) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_static_acquisition_scope_authorized",
        "parent_authorization": _record(
            root,
            _inside(root, PARENT_AUTHORIZATION_PATH, label="parent"),
            commit=EXPECTED_PARENT_COMMIT,
        ),
        "prerequisites": {
            "support_completion": support_record,
            "metadata_completion": metadata_record,
            "sentinel_inventory_completion": sentinel_record,
            "water_distance_freeze": water_record,
        },
        "blind_city_ids": list(BLIND_CITY_IDS),
        "city_scope": cities,
        "key_universe": {
            "target_date_count": 143,
            "tract_date_row_count": 23_667,
            "sha256": parent["key_universe"]["universe_sha256"],
        },
        "predictor_scope": {
            "static_feature_count": len(STATIC_FEATURES),
            "static_feature_names": list(STATIC_FEATURES),
            "sentinel_feature_count": len(INDEX_COLUMNS),
            "sentinel_feature_names": list(INDEX_COLUMNS),
            "sentinel_reflectance_bands": list(REFLECTANCE_BANDS),
            "sentinel_qa_asset": "SCL",
            "sentinel_product_metadata_asset": "product-metadata",
            "lag_window_days_before_target_inclusive": [60, 1],
        },
        "source_contract": {
            "sentinel": {
                "provider": "Microsoft Planetary Computer",
                "collection": "sentinel-2-l2a",
                "allowed_hosts": [
                    "planetarycomputer.microsoft.com",
                    "*.blob.core.windows.net",
                ],
                "selected_physical_acquisition_count": sum(
                    EXPECTED_SENTINEL_COUNTS.values()
                ),
                "only_inventory_bound_asset_hrefs": True,
            },
            "nlcd": {
                "endpoint": "https://www.mrlc.gov/geoserver/ows",
                "coverage_ids": [
                    "mrlc_download__NLCD_2016_Land_Cover_L48",
                    "mrlc_download__NLCD_2016_Impervious_L48",
                ],
                "native_crs": "EPSG:5070",
                "native_resolution_m": 30,
                "source_halo_pixels": 2,
            },
            "terrain": {
                "url_prefix": (
                    "https://opentopography.s3.sdsc.edu/raster/"
                    "SRTM_GL1/SRTM_GL1_srtm/"
                ),
                "dataset": "NASA SRTM Global 1 arc second V003",
                "only_frozen_tile_ids": True,
            },
            "water_distance": {
                "archive_path": water["source_lock"]["archive_path"],
                "archive_bytes": water["source_lock"]["archive_bytes"],
                "archive_sha256": water["source_lock"]["archive_sha256"],
                "network_allowed": False,
            },
        },
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "write_scope": {
            "output_root": OUTPUT_ROOT.as_posix(),
            "runtime_root": RUNTIME_ROOT.as_posix(),
            "completion": COMPLETION_PATH.as_posix(),
            "append_only_completions": True,
            "partial_files_must_not_be_treated_as_complete": True,
        },
        "limits": {
            "maximum_compute_workers": 1,
            "maximum_download_workers": 2,
            "maximum_active_acquisitions": 2,
            "maximum_single_static_download_bytes": 268_435_456,
            "maximum_total_static_download_bytes": 1_073_741_824,
        },
        "permissions": {
            "sentinel_and_static_scope_frozen": True,
            "sentinel_and_static_value_or_network_read_now": False,
            "runtime_launch_child_authorization_required_before_first_value_or_network_read": True,
            "daymet_value_or_network_read": False,
            "landsat_asset_href_thermal_qa_or_target_access": False,
            "model_fit_predict_score_or_evaluate": False,
            "prediction_before_target_boundary_preserved": True,
        },
        "authorization_audit": {
            "sentinel_or_static_raster_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "daymet_landsat_qa_or_target_values_read": False,
            "model_fit_predict_score_or_evaluate": False,
        },
        "required_runtime_launch_contract": {
            "bind_exact_executable_and_config_hashes": True,
            "authenticate_this_authorization_before_every_value_or_network_read": True,
            "resume_without_queue_rebuild_or_cache_delete": True,
            "no_signed_urls_or_credentials_persisted": True,
            "offline_assembly_requires_separate_phase_authentication": True,
        },
        "next_safe_stage": (
            "implement_review_and_authorize_exact_resumable_sentinel_static_runtime_launch"
        ),
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, AUTHORIZATION_PATH, label="authorization")
    payload = build_authorization(root)
    _write_exclusive(payload, destination)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(
        _inside(root, AUTHORIZATION_PATH, label="authorization"),
        label="Sentinel/static acquisition authorization",
    )
    if observed != build_authorization(root):
        raise M3BlindPredictorSentinelStaticAuthorizationError(
            "Sentinel/static acquisition authorization drifted."
        )
    return observed
