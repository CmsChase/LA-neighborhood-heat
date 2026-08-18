"""Append-only integrity overlay for the M3 source cache.

Version 1 downloaded 525 source scenes into one immutable physical cache plan,
but two upstream scenes can no longer provide all five official assets.  This
module implements the separately authorized option-A response: exclude exactly
those two scenes, retain every other physical content commit in place, and
write only lightweight logical commits under a new root.

The authorization builder reads committed metadata and public, assets-excluded
STAC geometry only.  It never opens a TIFF.  TIFF access is confined to
``authenticate_retained_scene`` and is therefore available only after the
append-only authorization authenticates.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

import numpy as np

from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_asset_cache import (
    CONTENT_COMMIT_SUFFIX,
    COVERAGE_COMMIT_FILENAME,
    PLAN_FILENAME,
    REQUIRED_ASSETS,
    _authenticate_content,
    _authenticate_coverage,
    _grid_from_record,
    authenticate_plan,
)
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
    authenticate_expanded_inventory,
    load_runner_settings,
)
from la_heat.multicity.m3_source_integrity_amendment_v1 import (
    AMENDMENT_PATH as INTEGRITY_AMENDMENT_PATH,
)
from la_heat.multicity.m3_source_integrity_amendment_v1 import (
    OVERLAY_PATH as SOURCE_LOGICAL_OVERLAY_PATH,
)
from la_heat.multicity.m3_source_integrity_amendment_v1 import (
    authenticate_source_integrity_logical_overlay,
)
from la_heat.multicity.m3_source_qa_authorization import (
    authenticate_m3_source_qa_authorization,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 2
ALGORITHM_VERSION: Final = "m3-source-integrity-overlay-v2"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_INTEGRITY_V2_AUTHORIZATION.json"
)
LOGICAL_CACHE_ROOT: Final = Path(
    "data/interim/multicity/m3_source_development_v2/logical_cache"
)
QA_OUTPUT_ROOT: Final = Path(
    "data/interim/multicity/m3_source_development_v2/qa_candidates"
)
RUNTIME_DATABASE_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/tasks.sqlite"
)
RUNTIME_CONTROL_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/control.json"
)
RUNTIME_STATUS_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/status.json"
)
RUNTIME_LOG_PATH: Final = Path(
    "data/interim/multicity/m3_source_development_v2/runtime/worker.log"
)
LOGICAL_CACHE_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_LANDSAT_LOGICAL_CACHE_COMPLETE.json"
)
QA_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_QA_CANDIDATES_COMPLETE.json"
)
LOGICAL_PLAN_FILENAME: Final = "LOGICAL_OVERLAY_PLAN.json"
LOGICAL_SCENE_COMMIT_FILENAME: Final = "LOGICAL_SCENE_COMMIT.json"
LOGICAL_GLOBAL_COMMIT_FILENAME: Final = "LOGICAL_GLOBAL_CACHE_COMMIT.json"

OLD_RUN_ID: Final = "m3-source-development-v1-0d407dbbfb2e3e9f"
OLD_TASK_PLAN_SHA256: Final = (
    "2cbf37305abd20a8b1cb4ee6e364c4793380bed2cf9aa2dc9f2cf19a2972c3cf"
)
INTEGRITY_AMENDMENT_COMMIT_SHA256: Final = (
    "43f72b3ce67f27aa9fa68ba9f49a287beb9ef555cf073ad9d8f8255d2c21f697"
)
SOURCE_LOGICAL_OVERLAY_COMMIT_SHA256: Final = (
    "1051593e5f87541dc21fdfc99df327605b429adb00de0dd851efa9340b44a673"
)
OLD_TASK_COUNT: Final = 3474
OLD_COMPLETE_COUNT: Final = 2622
OLD_PENDING_COUNT: Final = 852

CHICAGO_EXCLUDED_SCENE: Final = "LC08_L2SP_023031_20220727_02_T1"
CHICAGO_EXCLUDED_OVERPASS: Final = "landsat-8_20220727T163528Z"
HOUSTON_EXCLUDED_SCENE: Final = "LC09_L2SP_025040_20220514_02_T1"
HOUSTON_RETAINED_SCENE: Final = "LC09_L2SP_025039_20220514_02_T1"
HOUSTON_MODIFIED_OVERPASS: Final = "landsat-9_20220514T165014Z"
EXPECTED_OVERPASS_COUNT: Final = 317
EXPECTED_SCENE_COUNT: Final = 523
EXPECTED_CONTENT_COUNT: Final = 2615

CODE_PATHS: Final = (
    "configs/multicity/m3_source_development_runner_v2.toml",
    "configs/research.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/model_run_queue.py",
    "src/la_heat/multicity/m3_source_asset_cache.py",
    "src/la_heat/multicity/m3_source_integrity_amendment_v1.py",
    "src/la_heat/multicity/m3_source_offline_qa.py",
    "src/la_heat/multicity/m3_source_development_runtime.py",
    "src/la_heat/multicity/m3_source_development_engine.py",
    "src/la_heat/multicity/m3_source_development_worker.py",
    "src/la_heat/multicity/m3_source_integrity_v2.py",
    "src/la_heat/multicity/m3_source_development_runtime_v2.py",
    "src/la_heat/multicity/m3_source_development_engine_v2.py",
    "src/la_heat/multicity/m3_source_development_worker_v2.py",
    "scripts/authorize_m3_source_integrity_v2.py",
    "scripts/run_m3_source_development_worker_v2.py",
)


class M3SourceIntegrityV2Error(RuntimeError):
    """Raised when the integrity overlay or its physical bindings drift."""


ValueAccessGate = Callable[[], None]


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _is_committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise M3SourceIntegrityV2Error(f"{label} must stay inside the project.")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _overlaps(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


def _require_new_write_target(
    path: Path,
    *,
    physical_cache_root: Path,
    old_runtime_root: Path,
    label: str,
) -> None:
    if _overlaps(path, physical_cache_root) or _overlaps(path, old_runtime_root):
        raise M3SourceIntegrityV2Error(
            f"{label} must be isolated from the old cache and runtime."
        )


def _read_committed_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceIntegrityV2Error(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict) or not _is_committed(value):
        raise M3SourceIntegrityV2Error(f"{label} commit is invalid: {path}")
    return value


def _file_record(
    root: Path,
    path: Path,
    *,
    commit_sha256: str | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise M3SourceIntegrityV2Error(f"Bound file is missing: {path}")
    result: dict[str, Any] = {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if commit_sha256 is not None:
        result["commit_sha256"] = commit_sha256
    return result


def _safe_local_record_path(root: Path, value: object) -> Path:
    text = str(value)
    pure = PurePosixPath(text)
    if (
        "://" in text
        or "\\" in text
        or pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
    ):
        raise M3SourceIntegrityV2Error("Physical commit contains a non-local path.")
    path = (root / Path(*pure.parts)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise M3SourceIntegrityV2Error("Physical commit path escapes its cache root.")
    return path


def _metadata_only_output_check(cache_root: Path, commit: Mapping[str, Any]) -> None:
    record = commit.get("output_file")
    if not isinstance(record, Mapping):
        raise M3SourceIntegrityV2Error("Physical commit lacks its output record.")
    path = _safe_local_record_path(cache_root, record.get("path"))
    if not path.is_file() or path.stat().st_size != record.get("bytes"):
        raise M3SourceIntegrityV2Error("Physical raster is absent or has a changed size.")


def _physical_scene_reference(
    cache_root: Path,
    plan: Mapping[str, Any],
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate commit JSON and file metadata without opening TIFF values."""

    city_id = str(scene["city_id"])
    scene_id = str(scene["scene_id"])
    grid_sha256 = str(plan["grids"][city_id]["sha256"])
    directory = cache_root / "cities" / city_id / "scenes" / scene_id
    coverage = _read_committed_json(
        directory / COVERAGE_COMMIT_FILENAME,
        label=f"coverage {scene_id}",
    )
    if (
        coverage.get("plan_commit_sha256") != plan["commit_sha256"]
        or coverage.get("scene_id") != scene_id
        or coverage.get("grid_sha256") != grid_sha256
    ):
        raise M3SourceIntegrityV2Error("Coverage is detached from the physical plan.")
    _metadata_only_output_check(cache_root, coverage)
    contents: dict[str, str] = {}
    for asset in REQUIRED_ASSETS:
        commit = _read_committed_json(
            directory / "assets" / f"{asset}{CONTENT_COMMIT_SUFFIX}",
            label=f"{scene_id} {asset}",
        )
        if (
            commit.get("plan_commit_sha256") != plan["commit_sha256"]
            or commit.get("scene_id") != scene_id
            or commit.get("asset") != asset
            or commit.get("grid_sha256") != grid_sha256
            or commit.get("coverage_commit_sha256") != coverage["commit_sha256"]
            or commit.get("remote_href_or_signed_url_persisted") is not False
        ):
            raise M3SourceIntegrityV2Error("Content is detached from its physical scene.")
        _metadata_only_output_check(cache_root, commit)
        contents[asset] = str(commit["commit_sha256"])
    return {
        "city_id": city_id,
        "scene_id": scene_id,
        "physical_scene_record": dict(scene),
        "grid_sha256": grid_sha256,
        "coverage_commit_sha256": str(coverage["commit_sha256"]),
        "content_commit_sha256s": contents,
    }


def _old_queue_snapshot(database: Path) -> dict[str, Any]:
    if not database.is_file():
        raise M3SourceIntegrityV2Error("Original queue database is missing.")
    uri = f"file:{database.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        run = connection.execute(
            "SELECT run_id, task_plan_sha256, desired_state, schema_version "
            "FROM model_runs WHERE run_id = ?",
            (OLD_RUN_ID,),
        ).fetchone()
        counts = connection.execute(
            "SELECT status, COUNT(*) AS n FROM model_run_tasks "
            "WHERE run_id = ? GROUP BY status",
            (OLD_RUN_ID,),
        ).fetchall()
        active = connection.execute(
            "SELECT COUNT(*) FROM model_run_tasks WHERE run_id = ? "
            "AND (status = 'running' OR lease_owner IS NOT NULL "
            "OR lease_expires_at IS NOT NULL)",
            (OLD_RUN_ID,),
        ).fetchone()
    finally:
        connection.close()
    if run is None or active is None:
        raise M3SourceIntegrityV2Error("Original queue run is unavailable.")
    by_status = {str(row["status"]): int(row["n"]) for row in counts}
    result = {
        "run_id": str(run["run_id"]),
        "schema_version": int(run["schema_version"]),
        "task_plan_sha256": str(run["task_plan_sha256"]),
        "task_count": sum(by_status.values()),
        "desired_state": str(run["desired_state"]),
        "status_counts": {
            key: by_status.get(key, 0)
            for key in ("pending", "running", "complete", "quarantined")
        },
        "active_lease_count": int(active[0]),
    }
    if result != {
        "run_id": OLD_RUN_ID,
        "schema_version": 1,
        "task_plan_sha256": OLD_TASK_PLAN_SHA256,
        "task_count": OLD_TASK_COUNT,
        "desired_state": "paused",
        "status_counts": {
            "pending": OLD_PENDING_COUNT,
            "running": 0,
            "complete": OLD_COMPLETE_COUNT,
            "quarantined": 0,
        },
        "active_lease_count": 0,
    }:
        raise M3SourceIntegrityV2Error("Original queue left its frozen zero-lease snapshot.")
    return result


def build_logical_overlay(
    project_root: str | Path,
    inventory: Mapping[str, Any],
    physical_plan: Mapping[str, Any],
    source_overlay: Mapping[str, Any],
    *,
    physical_cache_root: str | Path,
) -> dict[str, Any]:
    """Bind the formal 317-overpass overlay to old physical commit JSON."""

    root = Path(project_root).resolve()
    cache_root = _inside(root, physical_cache_root, label="Physical cache root")
    plan = authenticate_plan(physical_plan)
    if (
        not _is_committed(source_overlay)
        or source_overlay.get("state") != "source_integrity_logical_overlay_complete"
        or source_overlay.get("parent_expanded_source_inventory_commit_sha256")
        != inventory.get("commit_sha256")
        or source_overlay.get("physical_scene_plan_commit_sha256")
        != plan.get("commit_sha256")
        or source_overlay.get("logical_totals")
        != {
            "overpasses": EXPECTED_OVERPASS_COUNT,
            "city_dates": EXPECTED_OVERPASS_COUNT,
            "scene_references": EXPECTED_SCENE_COUNT,
            "unique_scenes": EXPECTED_SCENE_COUNT,
        }
        or source_overlay.get("excluded_scene_ids")
        != [CHICAGO_EXCLUDED_SCENE, HOUSTON_EXCLUDED_SCENE]
        or source_overlay.get("excluded_overpass_ids")
        != [CHICAGO_EXCLUDED_OVERPASS]
        or source_overlay.get("retained_modified_overpass_ids")
        != [HOUSTON_MODIFIED_OVERPASS]
        or source_overlay.get("old_inventory_scene_plan_cache_or_queue_modified")
        is not False
        or source_overlay.get("existing_content_commits_rewritten_copied_or_rebound")
        is not False
    ):
        raise M3SourceIntegrityV2Error("Formal source integrity overlay changed.")
    raw_overpasses = source_overlay.get("logical_overpasses")
    if not isinstance(raw_overpasses, list):
        raise M3SourceIntegrityV2Error("Formal source integrity overlay has no rows.")
    overpasses = [dict(row) for row in raw_overpasses if isinstance(row, Mapping)]
    if len(overpasses) != len(raw_overpasses):
        raise M3SourceIntegrityV2Error("Formal logical overpass row is invalid.")
    retained_ids = {
        str(scene_id)
        for row in overpasses
        for scene_id in row.get("scene_ids", [])
    }
    base_scenes = plan.get("scenes")
    if not isinstance(base_scenes, list):
        raise M3SourceIntegrityV2Error("Physical plan lacks scenes.")
    expected_ids = {
        str(scene["scene_id"])
        for scene in base_scenes
        if str(scene["scene_id"])
        not in {CHICAGO_EXCLUDED_SCENE, HOUSTON_EXCLUDED_SCENE}
    }
    if (
        len(overpasses) != EXPECTED_OVERPASS_COUNT
        or retained_ids != expected_ids
        or len(retained_ids) != EXPECTED_SCENE_COUNT
    ):
        raise M3SourceIntegrityV2Error("Logical overlay counts or scene membership changed.")
    references = [
        _physical_scene_reference(cache_root, plan, scene)
        for scene in base_scenes
        if str(scene["scene_id"]) in retained_ids
    ]
    if len(references) * len(REQUIRED_ASSETS) != EXPECTED_CONTENT_COUNT:
        raise M3SourceIntegrityV2Error("Retained physical content count changed.")
    content_hashes = [
        reference["content_commit_sha256s"][asset]
        for reference in references
        for asset in REQUIRED_ASSETS
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_integrity_execution_overlay_bound",
        "option": "A_exclude_exact_unrecoverable_scenes_without_replacement",
        "source_integrity_logical_overlay_commit_sha256": source_overlay[
            "commit_sha256"
        ],
        "base_expanded_inventory_commit_sha256": inventory["commit_sha256"],
        "physical_scene_plan_commit_sha256": plan["commit_sha256"],
        "physical_cache_root": _relative(root, cache_root),
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": list(BLIND_CITY_IDS),
        "required_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(QA_CANDIDATES),
        "excluded_scene_ids": list(source_overlay["excluded_scene_ids"]),
        "excluded_overpass_ids": list(source_overlay["excluded_overpass_ids"]),
        "retained_modified_overpass_ids": list(
            source_overlay["retained_modified_overpass_ids"]
        ),
        "overpass_count": len(overpasses),
        "scene_count": len(references),
        "content_count": len(content_hashes),
        "overpasses": overpasses,
        "retained_scenes": references,
        "retained_content_commit_set_sha256": canonical_sha256(content_hashes),
        "support_gate_changed": False,
        "years_or_cities_changed": False,
        "physical_cache_files_copied_or_modified": False,
        "blind_test_asset_predictor_qa_or_target_accessed": False,
    }
    return _with_commit(payload)


def _write_once(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        observed = _read_committed_json(destination, label=destination.name)
        if observed != dict(payload):
            raise M3SourceIntegrityV2Error(f"Append-only output differs: {destination}")
        return
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        observed = _read_committed_json(destination, label=destination.name)
        if observed != dict(payload):
            raise M3SourceIntegrityV2Error(
                f"Append-only output differs: {destination}"
            ) from error
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def build_m3_source_integrity_v2_authorization(
    project_root: str | Path,
    *,
    logical_cache_root: str | Path = LOGICAL_CACHE_ROOT,
    qa_output_root: str | Path = QA_OUTPUT_ROOT,
    runtime_database_path: str | Path = RUNTIME_DATABASE_PATH,
    runtime_control_path: str | Path = RUNTIME_CONTROL_PATH,
    runtime_status_path: str | Path = RUNTIME_STATUS_PATH,
    runtime_log_path: str | Path = RUNTIME_LOG_PATH,
    logical_cache_completion_path: str | Path = LOGICAL_CACHE_COMPLETION_PATH,
    qa_completion_path: str | Path = QA_COMPLETION_PATH,
) -> dict[str, Any]:
    """Build option-A authorization without opening any raster value."""

    root = Path(project_root).resolve()
    settings = load_runner_settings(root)
    logical_root = _inside(root, logical_cache_root, label="Logical cache root")
    qa_root = _inside(root, qa_output_root, label="QA output root")
    runtime_database = _inside(root, runtime_database_path, label="V2 runtime database")
    runtime_control = _inside(root, runtime_control_path, label="V2 runtime control")
    runtime_worker_lock = runtime_control.with_suffix(".worker.lock")
    runtime_status = _inside(root, runtime_status_path, label="V2 runtime status")
    runtime_log = _inside(root, runtime_log_path, label="V2 runtime log")
    cache_completion = _inside(
        root, logical_cache_completion_path, label="Logical cache completion"
    )
    qa_completion = _inside(root, qa_completion_path, label="QA completion")
    write_targets = {
        "logical cache root": logical_root,
        "QA output root": qa_root,
        "V2 runtime database": runtime_database,
        "V2 runtime control": runtime_control,
        "V2 runtime worker lock": runtime_worker_lock,
        "V2 runtime status": runtime_status,
        "V2 runtime log": runtime_log,
        "logical cache completion": cache_completion,
        "QA completion": qa_completion,
    }
    for label, target in write_targets.items():
        _require_new_write_target(
            target,
            physical_cache_root=settings.cache_root,
            old_runtime_root=settings.database.parent,
            label=label,
        )
    disjoint = tuple(write_targets.values())
    if any(
        _overlaps(left, right)
        for index, left in enumerate(disjoint)
        for right in disjoint[index + 1 :]
    ):
        raise M3SourceIntegrityV2Error("V2 write targets overlap each other.")
    protocol = authenticate_m3_development_protocol_lock(root, settings.protocol_lock)
    amendment = authenticate_m3_source_acquisition_amendment(root, settings.amendment)
    inventory = authenticate_expanded_inventory(settings, amendment)
    qa_authorization = authenticate_m3_source_qa_authorization(
        root, settings.authorization
    )
    source_overlay = authenticate_source_integrity_logical_overlay(
        root,
        SOURCE_LOGICAL_OVERLAY_PATH,
        amendment_path=INTEGRITY_AMENDMENT_PATH,
    )
    integrity_amendment = _read_committed_json(
        root / INTEGRITY_AMENDMENT_PATH,
        label="Source integrity availability amendment",
    )
    if (
        integrity_amendment.get("state")
        != "source_asset_integrity_availability_amendment_locked"
        or integrity_amendment.get("commit_sha256")
        != INTEGRITY_AMENDMENT_COMMIT_SHA256
        or source_overlay.get("source_integrity_amendment_commit_sha256")
        != integrity_amendment["commit_sha256"]
        or source_overlay.get("commit_sha256")
        != SOURCE_LOGICAL_OVERLAY_COMMIT_SHA256
    ):
        raise M3SourceIntegrityV2Error("Formal integrity manifest commits changed.")
    plan_path = settings.cache_root / PLAN_FILENAME
    physical_plan = authenticate_plan(
        json.loads(plan_path.read_text(encoding="utf-8"))
    )
    queue = _old_queue_snapshot(settings.database)
    overlay = build_logical_overlay(
        root,
        inventory,
        physical_plan,
        source_overlay,
        physical_cache_root=settings.cache_root,
    )
    code_identity = {
        path: _file_record(root, root / path) for path in CODE_PATHS
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_integrity_overlay_v2_authorized",
        "option": "A_exclude_exact_unrecoverable_scenes_without_replacement",
        "m3_protocol_lock_commit_sha256": protocol["commit_sha256"],
        "source_acquisition_amendment_commit_sha256": amendment["commit_sha256"],
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "source_qa_execution_authorization_commit_sha256": qa_authorization[
            "commit_sha256"
        ],
        "source_integrity_availability_amendment_commit_sha256": (
            integrity_amendment["commit_sha256"]
        ),
        "source_integrity_logical_overlay_commit_sha256": source_overlay[
            "commit_sha256"
        ],
        "physical_scene_plan_commit_sha256": physical_plan["commit_sha256"],
        "logical_overlay": overlay,
        "original_queue_snapshot": queue,
        "inputs": {
            "m3_protocol_lock": _file_record(
                root, settings.protocol_lock, commit_sha256=protocol["commit_sha256"]
            ),
            "source_acquisition_amendment": _file_record(
                root, settings.amendment, commit_sha256=amendment["commit_sha256"]
            ),
            "expanded_source_inventory": _file_record(
                root, settings.inventory, commit_sha256=inventory["commit_sha256"]
            ),
            "source_qa_execution_authorization": _file_record(
                root,
                settings.authorization,
                commit_sha256=qa_authorization["commit_sha256"],
            ),
            "source_integrity_availability_amendment": _file_record(
                root,
                root / INTEGRITY_AMENDMENT_PATH,
                commit_sha256=integrity_amendment["commit_sha256"],
            ),
            "source_integrity_logical_overlay": _file_record(
                root,
                root / SOURCE_LOGICAL_OVERLAY_PATH,
                commit_sha256=source_overlay["commit_sha256"],
            ),
            "physical_scene_plan": _file_record(
                root, plan_path, commit_sha256=physical_plan["commit_sha256"]
            ),
        },
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": list(BLIND_CITY_IDS),
        "required_landsat_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(QA_CANDIDATES),
        "expected_overpass_count": EXPECTED_OVERPASS_COUNT,
        "expected_retained_scene_count": EXPECTED_SCENE_COUNT,
        "expected_reused_content_commit_count": EXPECTED_CONTENT_COUNT,
        "support_gate": {
            "minimum_usable_dates_per_city": 8,
            "minimum_total_usable_city_dates": 30,
            "changed": False,
        },
        "permissions": {
            "authenticate_formal_integrity_amendment_and_logical_overlay": True,
            "revalidate_bound_assets_excluded_metadata_and_public_geography": True,
            "authenticate_exact_523_retained_source_scenes": True,
            "read_exact_2615_existing_physical_content_files": True,
            "write_logical_scene_and_global_commits_under_new_root": True,
            "run_317_overpasses_times_four_frozen_qa_candidates_offline": True,
            "compile_four_source_cities_and_finalize_qa_candidates": True,
            "network_or_href_hydration": False,
            "copy_delete_replace_or_modify_old_cache": False,
            "mutate_resume_or_rebuild_old_queue": False,
            "read_blind_city_assets_predictors_qa_or_targets": False,
            "read_or_build_predictors": False,
            "fit_select_predict_or_score": False,
            "change_year_city_candidate_or_support_gate": False,
        },
        "runtime_contract": {
            "compute_workers": 1,
            "download_workers": 0,
            "network_requests_allowed": False,
            "href_reads_allowed": False,
            "physical_cache_is_read_only": True,
            "logical_overlay_is_append_only": True,
            "old_queue_is_read_only_and_must_remain_paused": True,
            "independent_sqlite_status_output_required": True,
            "phase_order": ["logical_cache_finalize", "offline_qa_rebuild"],
        },
        "physical_cache_root": _relative(root, settings.cache_root),
        "original_queue_database": _relative(root, settings.database),
        "logical_cache_root": _relative(root, logical_root),
        "qa_output_root": _relative(root, qa_root),
        "runtime_database": _relative(root, runtime_database),
        "runtime_control": _relative(root, runtime_control),
        "runtime_worker_lock": _relative(root, runtime_worker_lock),
        "runtime_status": _relative(root, runtime_status),
        "runtime_log": _relative(root, runtime_log),
        "source_landsat_cache_completion": _relative(root, cache_completion),
        "source_qa_candidates_completion": _relative(root, qa_completion),
        "code_identity": code_identity,
        "access_audit": {
            "authorization_read_assets_excluded_source_metadata": True,
            "authorization_read_public_source_geography": True,
            "authorization_read_landsat_raster_thermal_or_qa_values": False,
            "authorization_modified_old_cache_or_queue": False,
            "authorization_read_predictor_or_target_values": False,
            "authorization_accessed_blind_city_data": False,
            "authorization_fit_selected_predicted_or_scored": False,
        },
        "next_safe_stage": "initialize_independent_v2_run_then_finalize_logical_cache",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_m3_source_integrity_v2_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Integrity v2 authorization")
    settings = load_runner_settings(root)
    _require_new_write_target(
        path,
        physical_cache_root=settings.cache_root,
        old_runtime_root=settings.database.parent,
        label="Integrity v2 authorization",
    )
    payload = build_m3_source_integrity_v2_authorization(root)
    if path.exists():
        raise M3SourceIntegrityV2Error(f"Append-only authorization exists: {path}")
    _write_once(payload, path)
    return authenticate_m3_source_integrity_v2_authorization(root, path)


def authenticate_m3_source_integrity_v2_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Integrity v2 authorization")
    settings = load_runner_settings(root)
    _require_new_write_target(
        path,
        physical_cache_root=settings.cache_root,
        old_runtime_root=settings.database.parent,
        label="Integrity v2 authorization",
    )
    observed = _read_committed_json(path, label="M3 source integrity v2 authorization")
    expected = build_m3_source_integrity_v2_authorization(
        root,
        logical_cache_root=str(observed.get("logical_cache_root", "")),
        qa_output_root=str(observed.get("qa_output_root", "")),
        runtime_database_path=str(observed.get("runtime_database", "")),
        runtime_control_path=str(observed.get("runtime_control", "")),
        runtime_status_path=str(observed.get("runtime_status", "")),
        runtime_log_path=str(observed.get("runtime_log", "")),
        logical_cache_completion_path=str(
            observed.get("source_landsat_cache_completion", "")
        ),
        qa_completion_path=str(observed.get("source_qa_candidates_completion", "")),
    )
    if observed != expected:
        raise M3SourceIntegrityV2Error("M3 source integrity v2 authorization drifted.")
    return observed


def authenticate_m3_source_integrity_v2_value_gate(
    project_root: str | Path,
    expected_authorization: Mapping[str, Any],
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> None:
    """Cheap per-task gate after one full authorization authentication.

    The immutable authorization JSON is checked in full, and the original v1
    queue is queried read-only to prove that it remains at the exact paused,
    zero-lease snapshot.  This deliberately does not rebuild the 523-scene
    metadata overlay; each physical TIFF is still authenticated separately by
    its coverage/content commit before its values are returned.
    """

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Integrity v2 authorization")
    observed = _read_committed_json(path, label="M3 source integrity v2 authorization")
    if observed != dict(expected_authorization):
        raise M3SourceIntegrityV2Error("Integrity v2 value-access permit changed.")
    database = _inside(
        root,
        str(observed.get("original_queue_database", "")),
        label="Original queue database",
    )
    if _old_queue_snapshot(database) != observed.get("original_queue_snapshot"):
        raise M3SourceIntegrityV2Error("Original queue snapshot changed before value access.")


def write_logical_overlay_plan(
    project_root: str | Path,
    authorization: Mapping[str, Any],
) -> Path:
    root = Path(project_root).resolve()
    logical_root = _inside(
        root, str(authorization["logical_cache_root"]), label="Logical cache root"
    )
    physical_root = _inside(
        root, str(authorization["physical_cache_root"]), label="Physical cache root"
    )
    if logical_root == physical_root or logical_root.is_relative_to(physical_root):
        raise M3SourceIntegrityV2Error("Logical plan cannot be written under old cache.")
    overlay = authorization.get("logical_overlay")
    if not isinstance(overlay, Mapping) or not _is_committed(overlay):
        raise M3SourceIntegrityV2Error("Authorization lacks a committed logical overlay.")
    path = logical_root / LOGICAL_PLAN_FILENAME
    _write_once(overlay, path)
    return path


def _scene_reference(
    overlay: Mapping[str, Any], city_id: str, scene_id: str
) -> dict[str, Any]:
    matches = [
        dict(value)
        for value in overlay.get("retained_scenes", [])
        if isinstance(value, Mapping)
        and value.get("city_id") == city_id
        and value.get("scene_id") == scene_id
    ]
    if len(matches) != 1:
        raise M3SourceIntegrityV2Error("Unknown or duplicated logical scene.")
    return matches[0]


def _authenticate_physical_scene_values(
    cache_root: Path,
    physical_plan: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    plan = authenticate_plan(physical_plan)
    scene = reference.get("physical_scene_record")
    if not isinstance(scene, Mapping):
        raise M3SourceIntegrityV2Error("Logical scene lacks its physical record.")
    city_id = str(reference["city_id"])
    scene_id = str(reference["scene_id"])
    if scene.get("city_id") != city_id or scene.get("scene_id") != scene_id:
        raise M3SourceIntegrityV2Error("Logical scene identity changed.")
    grid = _grid_from_record(plan["grids"][city_id])
    directory = cache_root / "cities" / city_id / "scenes" / scene_id
    coverage_commit, coverage = _authenticate_coverage(
        cache_root,
        directory,
        plan_commit_sha256=str(plan["commit_sha256"]),
        scene_id=scene_id,
        grid=grid,
    )
    if coverage_commit["commit_sha256"] != reference["coverage_commit_sha256"]:
        raise M3SourceIntegrityV2Error("Physical coverage commit changed.")
    arrays: dict[str, np.ndarray] = {}
    observed_contents: dict[str, str] = {}
    for asset in REQUIRED_ASSETS:
        commit, array = _authenticate_content(
            cache_root,
            directory,
            plan_commit_sha256=str(plan["commit_sha256"]),
            scene_id=scene_id,
            asset=asset,
            grid=grid,
        )
        if commit.get("coverage_commit_sha256") != coverage_commit["commit_sha256"]:
            raise M3SourceIntegrityV2Error("Physical content coverage binding changed.")
        observed_contents[asset] = str(commit["commit_sha256"])
        arrays[asset] = array
    if observed_contents != reference["content_commit_sha256s"]:
        raise M3SourceIntegrityV2Error("Physical content commit set changed.")
    arrays["source_coverage"] = coverage
    return {
        "coverage_commit_sha256": coverage_commit["commit_sha256"],
        "content_commit_sha256s": observed_contents,
    }, arrays


def _logical_scene_path(logical_root: Path, city_id: str, scene_id: str) -> Path:
    return (
        logical_root
        / "cities"
        / city_id
        / "scenes"
        / scene_id
        / LOGICAL_SCENE_COMMIT_FILENAME
    )


def authenticate_retained_scene(
    project_root: str | Path,
    authorization: Mapping[str, Any],
    physical_plan: Mapping[str, Any],
    city_id: str,
    scene_id: str,
    *,
    before_value_access: ValueAccessGate,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Authenticate one retained physical scene and write no physical file."""

    before_value_access()
    root = Path(project_root).resolve()
    overlay = authorization["logical_overlay"]
    reference = _scene_reference(overlay, city_id, scene_id)
    physical_root = _inside(
        root, str(authorization["physical_cache_root"]), label="Physical cache root"
    )
    physical, arrays = _authenticate_physical_scene_values(
        physical_root, physical_plan, reference
    )
    logical = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "logical_retained_scene_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "logical_overlay_commit_sha256": overlay["commit_sha256"],
            "physical_scene_plan_commit_sha256": physical_plan["commit_sha256"],
            "city_id": city_id,
            "scene_id": scene_id,
            "grid_sha256": reference["grid_sha256"],
            "coverage_commit_sha256": physical["coverage_commit_sha256"],
            "content_commit_sha256s": physical["content_commit_sha256s"],
            "physical_raster_files_copied_or_modified": False,
            "network_or_href_read": False,
            "blind_test_city_accessed": False,
        }
    )
    return logical, arrays


def finalize_retained_scene(
    project_root: str | Path,
    authorization: Mapping[str, Any],
    physical_plan: Mapping[str, Any],
    city_id: str,
    scene_id: str,
    *,
    before_value_access: ValueAccessGate,
) -> dict[str, Any]:
    logical, _arrays = authenticate_retained_scene(
        project_root,
        authorization,
        physical_plan,
        city_id,
        scene_id,
        before_value_access=before_value_access,
    )
    root = Path(project_root).resolve()
    logical_root = _inside(
        root, str(authorization["logical_cache_root"]), label="Logical cache root"
    )
    _write_once(logical, _logical_scene_path(logical_root, city_id, scene_id))
    return logical


def _authenticate_logical_scene_commit(
    path: Path,
    authorization: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    commit = _read_committed_json(path, label="Logical scene")
    if (
        commit.get("state") != "logical_retained_scene_complete"
        or commit.get("authorization_commit_sha256") != authorization["commit_sha256"]
        or commit.get("logical_overlay_commit_sha256")
        != authorization["logical_overlay"]["commit_sha256"]
        or commit.get("physical_scene_plan_commit_sha256")
        != authorization["physical_scene_plan_commit_sha256"]
        or commit.get("city_id") != reference["city_id"]
        or commit.get("scene_id") != reference["scene_id"]
        or commit.get("grid_sha256") != reference["grid_sha256"]
        or commit.get("coverage_commit_sha256")
        != reference["coverage_commit_sha256"]
        or commit.get("content_commit_sha256s")
        != reference["content_commit_sha256s"]
        or commit.get("physical_raster_files_copied_or_modified") is not False
        or commit.get("network_or_href_read") is not False
        or commit.get("blind_test_city_accessed") is not False
    ):
        raise M3SourceIntegrityV2Error("Logical scene commit binding changed.")
    return commit


def finalize_logical_global_cache(
    project_root: str | Path,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    logical_root = _inside(
        root, str(authorization["logical_cache_root"]), label="Logical cache root"
    )
    overlay = authorization["logical_overlay"]
    scene_commits: list[dict[str, str]] = []
    for reference in overlay["retained_scenes"]:
        commit = _authenticate_logical_scene_commit(
            _logical_scene_path(
                logical_root, str(reference["city_id"]), str(reference["scene_id"])
            ),
            authorization,
            reference,
        )
        scene_commits.append(
            {
                "city_id": str(reference["city_id"]),
                "scene_id": str(reference["scene_id"]),
                "commit_sha256": str(commit["commit_sha256"]),
            }
        )
    payload = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "logical_source_cache_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "logical_overlay_commit_sha256": overlay["commit_sha256"],
            "physical_scene_plan_commit_sha256": authorization[
                "physical_scene_plan_commit_sha256"
            ],
            "scene_count": len(scene_commits),
            "content_count": overlay["content_count"],
            "scene_commits": scene_commits,
            "physical_raster_files_copied_or_modified": False,
            "network_or_href_read": False,
            "local_only": True,
        }
    )
    _write_once(payload, logical_root / LOGICAL_GLOBAL_COMMIT_FILENAME)
    completion = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_landsat_logical_cache_complete",
            "claim_id": authorization["claim_id"],
            "authorization_commit_sha256": authorization["commit_sha256"],
            "logical_overlay_commit_sha256": overlay["commit_sha256"],
            "physical_scene_plan_commit_sha256": authorization[
                "physical_scene_plan_commit_sha256"
            ],
            "logical_global_cache_commit_sha256": payload["commit_sha256"],
            "overpass_count": overlay["overpass_count"],
            "scene_count": payload["scene_count"],
            "content_count": payload["content_count"],
            "physical_raster_files_copied_or_modified": False,
            "old_queue_or_cache_mutated": False,
            "network_or_href_read": False,
            "blind_test_city_accessed": False,
            "model_fit_or_selection_performed": False,
        }
    )
    completion_path = _inside(
        root,
        str(authorization["source_landsat_cache_completion"]),
        label="Logical cache completion",
    )
    _write_once(completion, completion_path)
    return payload


def authenticate_logical_global_cache(
    project_root: str | Path,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    logical_root = _inside(
        root, str(authorization["logical_cache_root"]), label="Logical cache root"
    )
    overlay = authorization["logical_overlay"]
    plan = _read_committed_json(
        logical_root / LOGICAL_PLAN_FILENAME, label="Logical overlay plan"
    )
    if plan != overlay:
        raise M3SourceIntegrityV2Error("Stored logical overlay plan changed.")
    global_commit = _read_committed_json(
        logical_root / LOGICAL_GLOBAL_COMMIT_FILENAME,
        label="Logical global cache",
    )
    expected_scene_commits: list[dict[str, str]] = []
    for reference in overlay["retained_scenes"]:
        commit = _authenticate_logical_scene_commit(
            _logical_scene_path(
                logical_root, str(reference["city_id"]), str(reference["scene_id"])
            ),
            authorization,
            reference,
        )
        expected_scene_commits.append(
            {
                "city_id": str(reference["city_id"]),
                "scene_id": str(reference["scene_id"]),
                "commit_sha256": str(commit["commit_sha256"]),
            }
        )
    if (
        global_commit.get("state") != "logical_source_cache_complete"
        or global_commit.get("authorization_commit_sha256")
        != authorization["commit_sha256"]
        or global_commit.get("logical_overlay_commit_sha256") != overlay["commit_sha256"]
        or global_commit.get("physical_scene_plan_commit_sha256")
        != authorization["physical_scene_plan_commit_sha256"]
        or global_commit.get("scene_count") != EXPECTED_SCENE_COUNT
        or global_commit.get("content_count") != EXPECTED_CONTENT_COUNT
        or global_commit.get("scene_commits") != expected_scene_commits
        or global_commit.get("physical_raster_files_copied_or_modified") is not False
        or global_commit.get("network_or_href_read") is not False
        or global_commit.get("local_only") is not True
    ):
        raise M3SourceIntegrityV2Error("Logical global cache binding changed.")
    completion = _read_committed_json(
        _inside(
            root,
            str(authorization["source_landsat_cache_completion"]),
            label="Logical cache completion",
        ),
        label="Logical cache completion",
    )
    if (
        completion.get("state") != "source_landsat_logical_cache_complete"
        or completion.get("authorization_commit_sha256")
        != authorization["commit_sha256"]
        or completion.get("logical_global_cache_commit_sha256")
        != global_commit["commit_sha256"]
        or completion.get("old_queue_or_cache_mutated") is not False
    ):
        raise M3SourceIntegrityV2Error("Logical cache completion binding changed.")
    return global_commit


def load_retained_scene_arrays(
    project_root: str | Path,
    authorization: Mapping[str, Any],
    physical_plan: Mapping[str, Any],
    city_id: str,
    scene_id: str,
    *,
    before_value_access: ValueAccessGate,
) -> dict[str, np.ndarray]:
    logical, arrays = authenticate_retained_scene(
        project_root,
        authorization,
        physical_plan,
        city_id,
        scene_id,
        before_value_access=before_value_access,
    )
    root = Path(project_root).resolve()
    logical_root = _inside(
        root, str(authorization["logical_cache_root"]), label="Logical cache root"
    )
    reference = _scene_reference(authorization["logical_overlay"], city_id, scene_id)
    observed = _authenticate_logical_scene_commit(
        _logical_scene_path(logical_root, city_id, scene_id),
        authorization,
        reference,
    )
    if observed != logical:
        raise M3SourceIntegrityV2Error("Logical scene differs from physical authentication.")
    return arrays
