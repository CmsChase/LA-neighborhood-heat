"""Authorized runtime repair for cross-UTM Sentinel assets and transient warps."""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import rasterio

from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    ACQUISITION_REPAIR_COMPLETION_PATH,
    REPAIR_COMPLETION_PATH,
    authenticate_daymet_order_repair_acquisition_completion,
    authenticate_daymet_order_repair_completion,
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
    OFFLINE_PHASE,
    ONLINE_PHASE,
    PHASES,
    source_predictor_run_id,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    exclusive_predictor_worker,
)
from la_heat.multicity.m3_source_predictor_sentinel_geometry_filter_v1 import (
    authenticate_completion as authenticate_geometry_filter_completion,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-sentinel-cross-utm-repair-v1"
DAYMET_AUTHORIZATION_COMMIT_SHA256: Final = (
    "366f94697d13048d00fa121b69983b45fc96d9247c90f460d591209cdad25a76"
)
GEOMETRY_FILTER_COMPLETION_COMMIT_SHA256: Final = (
    "5f0d68ffdf984fa95a182ae7be445640c64bc02273284b720abd495784626e35"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_PREDICTOR_SENTINEL_CROSS_UTM_REPAIR_V1_AUTHORIZATION.json"
)
ACQUISITION_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_PREDICTOR_SENTINEL_CROSS_UTM_REPAIR_V1_ACQUISITION_COMPLETE.json"
)
FINAL_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_PREDICTOR_SENTINEL_CROSS_UTM_REPAIR_V1_COMPLETE.json"
)
CODE_PATHS: Final = (
    Path("src/la_heat/multicity/m3_source_predictor_sentinel_cross_utm_repair_v1.py"),
    Path("scripts/run_m3_source_predictor_sentinel_cross_utm_repair_v1.py"),
)


class SentinelCrossUTMRepairError(RuntimeError):
    """Raised when the narrow runtime repair boundary changes."""


def validate_repaired_native_grid(
    source: rasterio.DatasetReader,
    *,
    grid: Any,
    categorical: bool,
    original_validator: Any,
    tolerance: float = 1e-8,
) -> None:
    """Retain strict validation, except phase checks across UTM 14/15."""

    expected_crs = rasterio.crs.CRS.from_string(grid.crs)
    if source.crs == expected_crs:
        original_validator(source, grid=grid, categorical=categorical, tolerance=tolerance)
        return
    if source.crs is None or source.crs.to_epsg() != 32614 or expected_crs.to_epsg() != 32615:
        raise ValueError(f"Unauthorized Sentinel CRS transform: {source.crs} -> {expected_crs}.")
    transform = source.transform
    if (
        not math.isclose(transform.b, 0.0, abs_tol=tolerance)
        or not math.isclose(transform.d, 0.0, abs_tol=tolerance)
        or transform.a <= 0
        or transform.e >= 0
        or not math.isclose(transform.a, -transform.e, abs_tol=tolerance)
    ):
        raise ValueError("Cross-UTM Sentinel asset is not north-up with square pixels.")
    resolution = float(transform.a)
    allowed = (20.0,) if categorical else (10.0, 20.0)
    if not any(math.isclose(resolution, value, abs_tol=tolerance) for value in allowed):
        raise ValueError(f"Unsupported cross-UTM Sentinel resolution: {resolution:g} m.")


def _root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _code_records(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root, root / path) for path in CODE_PATHS]


def _queue_snapshot(root: Path, parent: Mapping[str, Any]) -> dict[str, Any]:
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
                "SELECT status, COUNT(*) FROM model_run_tasks WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
        )
        rows = connection.execute(
            "SELECT task_id,status,attempt,claim_generation,error_type,lease_owner,"
            "lease_expires_at FROM model_run_tasks WHERE run_id=? AND kind=? ORDER BY task_id",
            (run_id, "acquire_sentinel_cache"),
        ).fetchall()
        leases = connection.execute(
            "SELECT COUNT(*) FROM model_run_tasks WHERE run_id=? AND "
            "(lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL)",
            (run_id,),
        ).fetchone()[0]
    if run is None or len(rows) != 2:
        raise SentinelCrossUTMRepairError("Predictor queue identity changed.")
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
            for row in rows
        ],
    }


def _validate_initial(snapshot: Mapping[str, Any]) -> None:
    tasks = snapshot.get("sentinel_cache_tasks")
    expected = [
        ("sentinel-cache-chicago_il", "WarpOperationError"),
        ("sentinel-cache-houston_tx", "ValueError"),
    ]
    if (
        snapshot.get("desired_state") != "paused"
        or snapshot.get("counts") != {"complete": 75, "pending": 10, "running": 0, "quarantined": 0}
        or snapshot.get("active_lease_count") != 0
        or not isinstance(tasks, list)
        or len(tasks) != 2
    ):
        raise SentinelCrossUTMRepairError("Sentinel repair queue snapshot changed.")
    for task, (task_id, error_type) in zip(tasks, expected, strict=True):
        if (
            task.get("task_id") != task_id
            or task.get("status") != "pending"
            or task.get("attempt") != 10
            or task.get("claim_generation") != 10
            or task.get("error_type") != error_type
            or task.get("lease_owner") is not None
            or task.get("lease_expires_at") is not None
        ):
            raise SentinelCrossUTMRepairError("Sentinel task incident changed.")


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
    geometry = authenticate_geometry_filter_completion(root)
    if (
        daymet.get("commit_sha256") != DAYMET_AUTHORIZATION_COMMIT_SHA256
        or geometry.get("commit_sha256") != GEOMETRY_FILTER_COMPLETION_COMMIT_SHA256
    ):
        raise SentinelCrossUTMRepairError("Repair lineage changed.")
    snapshot = _queue_snapshot(root, parent)
    _validate_initial(snapshot)
    code = _code_records(root)
    return _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_source_predictor_sentinel_cross_utm_repair_authorized",
            "daymet_order_repair_authorization_commit_sha256": daymet["commit_sha256"],
            "geometry_filter_completion_commit_sha256": geometry["commit_sha256"],
            "incident": {
                "queue_snapshot": snapshot,
                "houston_cause": "utm14_assets_rejected_by_utm15_native_crs_equality",
                "houston_cross_utm_item_count": 443,
                "chicago_cause": "transient_remote_gdal_warp_operation_error",
            },
            "repair_contract": {
                "allowed_crs_transform": "EPSG:32614_to_EPSG:32615_only",
                "cross_crs_phase_check_applicable": False,
                "native_resolution_and_north_up_checks_retained": True,
                "warp_operation_max_attempts_per_asset": 3,
                "resampling_or_scientific_parameters_changed": False,
                "queue_rebuild_reset_or_rewrite_allowed": False,
            },
            "permissions": {
                "source_sentinel_value_read": True,
                "blind_city_access": False,
                "target_or_landsat_value_read": False,
                "model_fit_select_predict_or_score": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "resume_same_predictor_queue_with_cross_utm_runner",
        }
    )


def _load_static(root: Path) -> dict[str, Any]:
    authorization = _read_committed(root / AUTHORIZATION_PATH, label=AUTHORIZATION_PATH.name)
    code = _code_records(root)
    if (
        authorization.get("schema_version") != SCHEMA_VERSION
        or authorization.get("algorithm_version") != ALGORITHM_VERSION
        or authorization.get("state") != "m3_source_predictor_sentinel_cross_utm_repair_authorized"
        or authorization.get("daymet_order_repair_authorization_commit_sha256")
        != DAYMET_AUTHORIZATION_COMMIT_SHA256
        or authorization.get("geometry_filter_completion_commit_sha256")
        != GEOMETRY_FILTER_COMPLETION_COMMIT_SHA256
        or authorization.get("code_identity")
        != {"files": code, "set_sha256": canonical_sha256(code)}
    ):
        raise SentinelCrossUTMRepairError("Cross-UTM repair authorization drifted.")
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
        raise SentinelCrossUTMRepairError("Cross-UTM repair authorization mismatch.")
    return observed


def _write_phase_completion(
    root: Path,
    *,
    phase: str,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    if phase == ONLINE_PHASE:
        parent = authenticate_daymet_order_repair_acquisition_completion(root)
        path = ACQUISITION_COMPLETION_PATH
        parent_path = ACQUISITION_REPAIR_COMPLETION_PATH
        state = "source_predictor_sentinel_cross_utm_acquisition_complete"
    else:
        parent = authenticate_daymet_order_repair_completion(root)
        path = FINAL_COMPLETION_PATH
        parent_path = REPAIR_COMPLETION_PATH
        state = "source_predictor_sentinel_cross_utm_repair_complete"
    payload = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": state,
            "authorization_commit_sha256": authorization["commit_sha256"],
            "parent_completion_commit_sha256": parent["commit_sha256"],
            "parent_completion": _file_record(
                root,
                root / parent_path,
                commit_sha256=parent["commit_sha256"],
            ),
            "queue_rebuilt_reset_or_rewritten": False,
            "blind_city_accessed": False,
            "target_or_landsat_values_read": False,
            "next_safe_stage": (
                "offline_assembly" if phase == ONLINE_PHASE else "source_joint_authorization"
            ),
        }
    )
    _write_exclusive(payload, root / path)
    return payload


def execute_repaired_worker(project_root: str | Path, *, phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")
    root = _root(project_root)
    authorization = authenticate_authorization(root)

    import la_heat.sentinel_feature_builder as sentinel_builder

    original_validator = sentinel_builder._validate_native_asset_grid
    original_reader = sentinel_builder._read_asset_to_optical_grid

    def repaired_validator(
        source: Any, *, grid: Any, categorical: bool, tolerance: float = 1e-8
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

    sentinel_builder._validate_native_asset_grid = repaired_validator
    sentinel_builder._read_asset_to_optical_grid = retry_reader
    try:
        result = execute_daymet_order_repair_worker(root, phase=phase)
    finally:
        sentinel_builder._validate_native_asset_grid = original_validator
        sentinel_builder._read_asset_to_optical_grid = original_reader
    if result.get("state") == "paused":
        if (
            phase == ONLINE_PHASE
            and result.get("counts_by_kind", {}).get("finalize_acquisition", {}).get("complete")
            == 1
        ):
            _write_phase_completion(root, phase=phase, authorization=authorization)
        elif phase == OFFLINE_PHASE and result.get("counts", {}).get("complete") == 85:
            _write_phase_completion(root, phase=phase, authorization=authorization)
    return {
        **result,
        "sentinel_cross_utm_repair_authorization_commit_sha256": authorization["commit_sha256"],
    }
