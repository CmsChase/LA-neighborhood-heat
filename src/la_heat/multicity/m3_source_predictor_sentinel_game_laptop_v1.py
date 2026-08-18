"""Four-asset game-laptop runner for the existing predictor queue."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import rasterio

from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    ACQUISITION_REPAIR_COMPLETION_PATH,
    authenticate_daymet_order_repair_acquisition_completion,
    execute_daymet_order_repair_worker,
    load_m3_source_predictor_daymet_order_repair_runtime_permit,
)
from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    AUTHORIZATION_PATH as DAYMET_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    _file_record,
    _read_committed,
    _with_commit,
    _write_exclusive,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import (
    ONLINE_PHASE,
    PHASES,
    source_predictor_run_id,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    exclusive_predictor_worker,
)
from la_heat.multicity.m3_source_predictor_sentinel_cross_utm_repair_v1 import (
    _load_static as load_cross_utm_static_authorization,
)
from la_heat.multicity.m3_source_predictor_sentinel_cross_utm_repair_v1 import (
    validate_repaired_native_grid,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-sentinel-game-laptop-v1"
CROSS_UTM_AUTHORIZATION_COMMIT_SHA256: Final = (
    "ca54e26d6a358ea5abd673a7ec3bf7bf8726d0a67b67a7a1e572447c2889844b"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_PREDICTOR_SENTINEL_GAME_LAPTOP_V1_AUTHORIZATION.json"
)
ACQUISITION_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_PREDICTOR_SENTINEL_GAME_LAPTOP_V1_ACQUISITION_COMPLETE.json"
)
CODE_PATHS: Final = (
    Path("src/la_heat/multicity/m3_source_predictor_sentinel_game_laptop_v1.py"),
    Path("scripts/run_m3_source_predictor_sentinel_game_laptop_v1.py"),
    Path("START_M3_PREDICTOR_GAME_LAPTOP.cmd"),
)
DOWNLOAD_THREADS: Final = 4


class SentinelGameLaptopError(RuntimeError):
    """Raised when the migration/runtime contract changes."""


def _root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _code_records(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root, root / path) for path in CODE_PATHS]


def _snapshot(root: Path, parent: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    uri = settings.database.resolve().as_uri() + "?mode=ro"
    run_id = source_predictor_run_id(parent)
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        run = connection.execute(
            "SELECT desired_state, task_plan_sha256 FROM model_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        counts = dict(
            connection.execute(
                "SELECT status,COUNT(*) FROM model_run_tasks WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
        )
        tasks = connection.execute(
            "SELECT task_id,status,attempt,claim_generation,error_type,lease_owner,"
            "lease_expires_at FROM model_run_tasks WHERE run_id=? AND kind=? ORDER BY task_id",
            (run_id, "acquire_sentinel_cache"),
        ).fetchall()
        leases = connection.execute(
            "SELECT COUNT(*) FROM model_run_tasks WHERE run_id=? AND "
            "(lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL)",
            (run_id,),
        ).fetchone()[0]
    if run is None or len(tasks) != 2:
        raise SentinelGameLaptopError("Predictor queue identity changed.")
    return {
        "run_id": run_id,
        "desired_state": run[0],
        "sqlite_task_plan_sha256": run[1],
        "counts": {
            key: int(counts.get(key, 0))
            for key in ("complete", "pending", "running", "quarantined")
        },
        "active_lease_count": int(leases),
        "sentinel_cache_tasks": [
            {
                "task_id": row[0],
                "status": row[1],
                "attempt": int(row[2]),
                "claim_generation": int(row[3]),
                "error_type": row[4],
                "lease_owner": row[5],
                "lease_expires_at": row[6],
            }
            for row in tasks
        ],
    }


def _validate_migration_snapshot(snapshot: Mapping[str, Any]) -> None:
    tasks = snapshot.get("sentinel_cache_tasks")
    expected = [
        ("sentinel-cache-chicago_il", 10, "WarpOperationError"),
        ("sentinel-cache-houston_tx", 11, "InterruptedForMigration"),
    ]
    if (
        snapshot.get("desired_state") != "paused"
        or snapshot.get("counts") != {"complete": 75, "pending": 10, "running": 0, "quarantined": 0}
        or snapshot.get("active_lease_count") != 0
        or not isinstance(tasks, list)
        or len(tasks) != 2
    ):
        raise SentinelGameLaptopError("Migration queue is not paused at a safe boundary.")
    for task, (task_id, attempt, error_type) in zip(tasks, expected, strict=True):
        if (
            task.get("task_id") != task_id
            or task.get("status") != "pending"
            or task.get("attempt") != attempt
            or task.get("claim_generation") != attempt
            or task.get("error_type") != error_type
            or task.get("lease_owner") is not None
            or task.get("lease_expires_at") is not None
        ):
            raise SentinelGameLaptopError("Migration task evidence changed.")


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    parent = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    daymet = load_m3_source_predictor_daymet_order_repair_runtime_permit(
        root,
        DAYMET_AUTHORIZATION_PATH,
        config_path=settings.config_path,
        require_paused=True,
    )
    cross_utm = load_cross_utm_static_authorization(root)
    if cross_utm.get("commit_sha256") != CROSS_UTM_AUTHORIZATION_COMMIT_SHA256:
        raise SentinelGameLaptopError("Cross-UTM authorization changed.")
    snapshot = _snapshot(root, parent)
    _validate_migration_snapshot(snapshot)
    code = _code_records(root)
    return _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_source_predictor_sentinel_game_laptop_authorized",
            "parent_authorization_commit_sha256": parent["commit_sha256"],
            "daymet_order_repair_authorization_commit_sha256": daymet["commit_sha256"],
            "cross_utm_authorization_commit_sha256": cross_utm["commit_sha256"],
            "migration_snapshot": snapshot,
            "runtime_contract": {
                "download_threads_per_acquisition": DOWNLOAD_THREADS,
                "active_acquisitions": 1,
                "compute_workers": 1,
                "native_compute_threads": 1,
                "same_sqlite_run_and_task_plan": True,
                "queue_rebuild_reset_or_rewrite_allowed": False,
                "old_machine_concurrent_execution_allowed": False,
            },
            "permissions": {
                "source_sentinel_value_read": True,
                "blind_city_access": False,
                "target_or_landsat_value_read": False,
                "model_fit_select_predict_or_score": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "copy_verified_bundle_then_run_start_cmd_on_one_machine",
        }
    )


def _load_static(root: Path) -> dict[str, Any]:
    authorization = _read_committed(root / AUTHORIZATION_PATH, label=AUTHORIZATION_PATH.name)
    code = _code_records(root)
    if (
        authorization.get("schema_version") != SCHEMA_VERSION
        or authorization.get("algorithm_version") != ALGORITHM_VERSION
        or authorization.get("state") != "m3_source_predictor_sentinel_game_laptop_authorized"
        or authorization.get("cross_utm_authorization_commit_sha256")
        != CROSS_UTM_AUTHORIZATION_COMMIT_SHA256
        or authorization.get("runtime_contract", {}).get("download_threads_per_acquisition")
        != DOWNLOAD_THREADS
        or authorization.get("code_identity")
        != {"files": code, "set_sha256": canonical_sha256(code)}
    ):
        raise SentinelGameLaptopError("Game-laptop authorization drifted.")
    return authorization


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    with exclusive_predictor_worker(settings.worker_lock):
        payload = build_authorization(root)
        _write_exclusive(payload, root / AUTHORIZATION_PATH)
        return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    observed = _load_static(root)
    expected = build_authorization(root)
    if observed != expected:
        raise SentinelGameLaptopError("Game-laptop authorization mismatch.")
    return observed


def execute_game_laptop_worker(project_root: str | Path, *, phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")
    root = _root(project_root)
    authorization = authenticate_authorization(root)

    import la_heat.multicity.portable_sentinel_build as portable_build
    import la_heat.sentinel_feature_builder as sentinel_builder

    original_process_one = portable_build._process_one
    original_validator = sentinel_builder._validate_native_asset_grid
    original_reader = sentinel_builder._read_asset_to_optical_grid

    def repaired_validator(
        source: Any,
        *,
        grid: Any,
        categorical: bool,
        tolerance: float = 1e-8,
    ) -> None:
        validate_repaired_native_grid(
            source,
            grid=grid,
            categorical=categorical,
            original_validator=original_validator,
            tolerance=tolerance,
        )

    def retry_reader(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(1, 4):
            try:
                return original_reader(*args, **kwargs)
            except rasterio.errors.WarpOperationError:
                if attempt == 3:
                    raise
                time.sleep(float(attempt))
        raise AssertionError("unreachable")

    def four_thread_process_one(
        root_path: Path,
        context: Any,
        row: Any,
        *,
        download_threads: int,
        force: bool,
    ) -> dict[str, Any]:
        if download_threads != 1:
            raise SentinelGameLaptopError("Parent download-thread contract changed.")
        return original_process_one(
            root_path,
            context,
            row,
            download_threads=DOWNLOAD_THREADS,
            force=force,
        )

    portable_build._process_one = four_thread_process_one
    sentinel_builder._validate_native_asset_grid = repaired_validator
    sentinel_builder._read_asset_to_optical_grid = retry_reader
    try:
        result = execute_daymet_order_repair_worker(root, phase=phase)
    finally:
        portable_build._process_one = original_process_one
        sentinel_builder._validate_native_asset_grid = original_validator
        sentinel_builder._read_asset_to_optical_grid = original_reader
    if (
        phase == ONLINE_PHASE
        and result.get("state") == "paused"
        and result.get("counts_by_kind", {}).get("finalize_acquisition", {}).get("complete") == 1
    ):
        parent_completion = authenticate_daymet_order_repair_acquisition_completion(root)
        completion = _with_commit(
            {
                "schema_version": SCHEMA_VERSION,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_predictor_sentinel_game_laptop_acquisition_complete",
                "authorization_commit_sha256": authorization["commit_sha256"],
                "parent_completion_commit_sha256": parent_completion["commit_sha256"],
                "parent_completion": _file_record(
                    root,
                    root / ACQUISITION_REPAIR_COMPLETION_PATH,
                    commit_sha256=parent_completion["commit_sha256"],
                ),
                "download_threads_per_acquisition": DOWNLOAD_THREADS,
                "active_acquisitions": 1,
                "blind_city_accessed": False,
                "target_or_landsat_values_read": False,
                "next_safe_stage": "offline_assembly",
            }
        )
        _write_exclusive(completion, root / ACQUISITION_COMPLETION_PATH)
    return {
        **result,
        "game_laptop_authorization_commit_sha256": authorization["commit_sha256"],
        "download_threads_per_acquisition": DOWNLOAD_THREADS,
    }
