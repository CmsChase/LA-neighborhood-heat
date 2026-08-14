"""Append-only permit for M3 source cache download and offline QA rebuild.

The permit has two strictly ordered phases.  The online phase may hydrate the
five frozen Landsat assets for the four source cities and write a local cache.
The offline phase may start only after that cache authenticates and may rebuild
the four preregistered ST_QA candidates.  It never authorizes model fitting,
selection, scoring, or any blind-test-city target access.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    QA_CANDIDATES,
    REQUIRED_ASSETS,
    SOURCE_CITY_IDS,
    authenticate_expanded_inventory,
    load_runner_settings,
)
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-source-qa-two-phase-authorization-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_QA_EXECUTION_AUTHORIZATION.json"
)
ACCESS_MARKER_PATH: Final = Path(
    "data/interim/multicity/m3_source_development/"
    "SOURCE_CACHE_ACCESS_STARTED.json"
)
CACHE_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development/"
    "SOURCE_LANDSAT_CACHE_COMPLETE.json"
)
QA_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development/"
    "SOURCE_QA_CANDIDATES_COMPLETE.json"
)

CODE_PATHS: Final = (
    "configs/multicity/m3_source_development_runner.toml",
    "configs/research.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/multicity/m3_source_asset_cache.py",
    "src/la_heat/multicity/m3_source_development_runtime.py",
    "src/la_heat/multicity/m3_source_development_worker.py",
    "src/la_heat/multicity/m3_source_development_engine.py",
    "src/la_heat/multicity/m3_source_offline_qa.py",
    "src/la_heat/multicity/m3_source_qa_authorization.py",
    "scripts/run_m3_source_development_worker.py",
    "scripts/authorize_m3_source_qa_execution.py",
)


class M3SourceQAAuthorizationError(RuntimeError):
    """Raised when the narrow two-phase permit cannot be reproduced."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceQAAuthorizationError(f"{label} must stay inside the project")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_committed_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceQAAuthorizationError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict):
        raise M3SourceQAAuthorizationError(f"{label} must be a JSON object")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise M3SourceQAAuthorizationError(f"{label} commit is invalid")
    return payload


def _file_record(root: Path, path: Path, *, commit: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise M3SourceQAAuthorizationError(f"Bound file is missing: {path}")
    record: dict[str, Any] = {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if commit is not None:
        record["commit_sha256"] = commit
    return record


def _inventory_counts(inventory: Mapping[str, Any]) -> tuple[int, int]:
    overpasses = inventory.get("overpasses")
    if not isinstance(overpasses, list) or not overpasses:
        raise M3SourceQAAuthorizationError("Expanded inventory has no overpasses")
    scene_keys: set[tuple[str, str]] = set()
    for row in overpasses:
        if not isinstance(row, Mapping):
            raise M3SourceQAAuthorizationError("Expanded inventory row is invalid")
        city_id = str(row.get("city_id", ""))
        scene_ids = row.get("scene_ids")
        if city_id not in SOURCE_CITY_IDS or not isinstance(scene_ids, list):
            raise M3SourceQAAuthorizationError("Expanded inventory left the source cohort")
        scene_keys.update((city_id, str(scene_id)) for scene_id in scene_ids)
    return len(overpasses), len(scene_keys)


def build_m3_source_qa_authorization(
    project_root: str | Path,
    *,
    access_marker_path: str | Path = ACCESS_MARKER_PATH,
    cache_completion_path: str | Path = CACHE_COMPLETION_PATH,
    qa_completion_path: str | Path = QA_COMPLETION_PATH,
) -> dict[str, Any]:
    """Build the permit without hydrating an href or opening a raster."""

    root = Path(project_root).resolve()
    settings = load_runner_settings(root)
    protocol = authenticate_m3_development_protocol_lock(root, settings.protocol_lock)
    amendment = authenticate_m3_source_acquisition_amendment(root, settings.amendment)
    inventory = authenticate_expanded_inventory(settings, amendment)
    if tuple(inventory.get("source_city_ids", ())) != SOURCE_CITY_IDS:
        raise M3SourceQAAuthorizationError("Source city order changed")
    if any(city in str(inventory) for city in BLIND_CITY_IDS):
        raise M3SourceQAAuthorizationError("Expanded source inventory contains a blind city")
    overpass_count, scene_count = _inventory_counts(inventory)

    code_identity = {
        relative: _file_record(root, root / relative)
        for relative in CODE_PATHS
    }
    marker = _inside(root, access_marker_path, label="Access marker")
    cache_complete = _inside(root, cache_completion_path, label="Cache completion")
    qa_complete = _inside(root, qa_completion_path, label="QA completion")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_qa_two_phase_execution_authorized",
        "m3_protocol_lock_commit_sha256": protocol["commit_sha256"],
        "source_acquisition_amendment_commit_sha256": amendment["commit_sha256"],
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "inputs": {
            "m3_protocol_lock": _file_record(
                root, settings.protocol_lock, commit=str(protocol["commit_sha256"])
            ),
            "source_acquisition_amendment": _file_record(
                root, settings.amendment, commit=str(amendment["commit_sha256"])
            ),
            "expanded_source_inventory": _file_record(
                root, settings.inventory, commit=str(inventory["commit_sha256"])
            ),
        },
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": list(BLIND_CITY_IDS),
        "expected_overpass_count": overpass_count,
        "expected_unique_city_scene_count": scene_count,
        "required_landsat_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(QA_CANDIDATES),
        "runtime_contract": {
            "download_workers_allowed": [1, 2],
            "compute_workers": 1,
            "raster_window_size": 512,
            "raster_window_size_is_hard_streaming_limit": False,
            "offline_execution_granularity": "one_complete_physical_overpass",
            "signed_urls_credentials_or_cookies_may_be_persisted": False,
            "retry_and_resume_from_content_commits": True,
        },
        "online_predownload_permissions": {
            "hydrate_frozen_source_scene_asset_hrefs": True,
            "read_exact_five_source_landsat_assets": True,
            "write_verified_local_aligned_cache": True,
            "aggregate_targets_or_apply_qa_candidates": False,
            "read_blind_test_city_assets_or_values": False,
        },
        "offline_qa_permissions": {
            "requires_authenticated_global_cache": True,
            "network_or_href_hydration_allowed": False,
            "read_verified_local_source_cache": True,
            "rebuild_none_3k_4k_6k_candidates": True,
            "fit_select_predict_or_score": False,
        },
        "blind_test_target_access_authorized": False,
        "model_fit_or_selection_authorized": False,
        "predictor_build_or_read_authorized": False,
        "source_cache_access_started_marker": _relative(root, marker),
        "source_landsat_cache_completion": _relative(root, cache_complete),
        "source_qa_candidates_completion": _relative(root, qa_complete),
        "code_identity": code_identity,
        "access_audit": {
            "authorization_read_landsat_item_assets_or_hrefs": False,
            "authorization_read_landsat_thermal_or_qa_values": False,
            "authorization_read_target_or_predictor_tables": False,
            "authorization_fit_selected_predicted_or_scored": False,
            "authorization_created_access_marker_or_cache": False,
            "blind_test_city_asset_or_value_accessed": False,
        },
        "next_safe_stage": "run_online_source_cache_then_authenticated_offline_qa_rebuild",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceQAAuthorizationError(
            f"Append-only authorization already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def authenticate_m3_source_qa_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Rebuild the permit exactly without reading source or blind target values."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization")
    observed = _read_committed_json(path, label="M3 source QA authorization")
    expected = build_m3_source_qa_authorization(
        root,
        access_marker_path=str(observed.get("source_cache_access_started_marker", "")),
        cache_completion_path=str(observed.get("source_landsat_cache_completion", "")),
        qa_completion_path=str(observed.get("source_qa_candidates_completion", "")),
    )
    if observed != expected:
        raise M3SourceQAAuthorizationError("M3 source QA authorization drifted")
    return observed


def create_m3_source_qa_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization")
    payload = build_m3_source_qa_authorization(root)
    _write_exclusive(payload, path)
    return authenticate_m3_source_qa_authorization(root, path)
