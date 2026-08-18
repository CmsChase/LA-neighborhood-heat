"""Append-only repair for Houston Sentinel STAC request-size failure.

Discovery uses the authenticated city AOI envelope; scientific cohort selection
continues to use the exact authenticated city AOI in the parent builder.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    AUTHORIZATION_PATH as DAYMET_REPAIR_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    DaymetOrderRepairAdapter,
    load_m3_source_predictor_daymet_order_repair_runtime_permit,
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
    source_predictor_run_id,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    exclusive_predictor_worker,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-sentinel-bbox-repair-v1"
PARENT_AUTHORIZATION_COMMIT_SHA256: Final = (
    "22f6f417faea2aaeb7f0c04d182ae45616f3cacf416ea904e58ed2c699987019"
)
DAYMET_REPAIR_AUTHORIZATION_COMMIT_SHA256: Final = (
    "366f94697d13048d00fa121b69983b45fc96d9247c90f460d591209cdad25a76"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_PREDICTOR_SENTINEL_BBOX_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_PREDICTOR_SENTINEL_BBOX_REPAIR_V1_COMPLETE.json"
)
HOUSTON_TASK_ID: Final = "sentinel-inventory-houston_tx"
CODE_PATHS: Final = (
    Path("src/la_heat/multicity/m3_source_predictor_sentinel_bbox_repair_v1.py"),
    Path("scripts/run_m3_source_predictor_sentinel_bbox_repair_v1.py"),
)


class SentinelBBoxRepairError(RuntimeError):
    """Raised when the narrow repair boundary or lineage changes."""


def _root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _committed(path: Path) -> dict[str, Any]:
    payload = _read_committed(path, label=path.name)
    return payload


def _queue_snapshot(root: Path, parent: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    uri = settings.database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        run_id = source_predictor_run_id(parent)
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
        task = connection.execute(
            "SELECT status, attempt, claim_generation, error_type, lease_owner, "
            "lease_expires_at FROM model_run_tasks WHERE run_id=? AND task_id=?",
            (run_id, HOUSTON_TASK_ID),
        ).fetchone()
        leases = connection.execute(
            "SELECT COUNT(*) FROM model_run_tasks WHERE run_id=? AND "
            "(lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL)",
            (run_id,),
        ).fetchone()[0]
    if run is None or task is None:
        raise SentinelBBoxRepairError("Predictor queue identity changed.")
    return {
        "run_id": run_id,
        "desired_state": run[0],
        "sqlite_task_plan_sha256": run[1],
        "counts": {
            key: int(counts.get(key, 0))
            for key in ("complete", "pending", "running", "quarantined")
        },
        "active_lease_count": int(leases),
        "houston_inventory_task": {
            "task_id": HOUSTON_TASK_ID,
            "status": task[0],
            "attempt": int(task[1]),
            "claim_generation": int(task[2]),
            "error_type": task[3],
            "lease_owner": task[4],
            "lease_expires_at": task[5],
        },
    }


def _validate_initial_snapshot(snapshot: Mapping[str, Any]) -> None:
    task = snapshot.get("houston_inventory_task")
    if (
        snapshot.get("desired_state") != "paused"
        or snapshot.get("counts") != {"complete": 74, "pending": 11, "running": 0, "quarantined": 0}
        or snapshot.get("active_lease_count") != 0
        or not isinstance(task, Mapping)
        or task.get("task_id") != HOUSTON_TASK_ID
        or task.get("status") != "pending"
        or task.get("attempt") != 7
        or task.get("claim_generation") != 7
        or task.get("error_type") != "APIError"
        or task.get("lease_owner") is not None
        or task.get("lease_expires_at") is not None
    ):
        raise SentinelBBoxRepairError("Houston 413 incident queue snapshot changed.")


def _code_records(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root, root / path) for path in CODE_PATHS]


def build_authorization(
    project_root: str | Path,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    root = _root(project_root)
    settings = load_predictor_extension_settings(root, config_path)
    parent = load_m3_source_predictor_extension_runtime_permit(
        root, settings.authorization, settings.config_path
    )
    repair = load_m3_source_predictor_daymet_order_repair_runtime_permit(
        root,
        DAYMET_REPAIR_AUTHORIZATION_PATH,
        config_path=settings.config_path,
        require_paused=True,
    )
    if (
        parent.get("commit_sha256") != PARENT_AUTHORIZATION_COMMIT_SHA256
        or repair.get("commit_sha256") != DAYMET_REPAIR_AUTHORIZATION_COMMIT_SHA256
    ):
        raise SentinelBBoxRepairError("Parent authorization lineage changed.")
    snapshot = _queue_snapshot(root, parent)
    _validate_initial_snapshot(snapshot)
    marker = settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json"
    if marker.exists():
        raise SentinelBBoxRepairError("Houston Sentinel inventory already exists.")
    code = _code_records(root)
    return _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_source_predictor_sentinel_bbox_repair_authorized",
            "parent_authorization_commit_sha256": parent["commit_sha256"],
            "daymet_order_repair_authorization_commit_sha256": repair["commit_sha256"],
            "incident": {
                "classification": "planetary_computer_stac_request_geometry_too_large",
                "city_id": "houston_tx",
                "http_status": 413,
                "error_type": "APIError",
                "queue_snapshot": snapshot,
            },
            "repair_contract": {
                "discovery_query_geometry": "exact_authenticated_aoi_envelope",
                "scientific_selection_geometry": "exact_authenticated_aoi",
                "target_dates_or_features_changed": False,
                "queue_rebuild_reset_or_rewrite_allowed": False,
            },
            "permissions": {
                "houston_source_sentinel_metadata_read": True,
                "blind_city_access": False,
                "target_or_landsat_value_read": False,
                "model_fit_select_predict_or_score": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "build_houston_inventory_once_then_resume_same_queue",
        }
    )


def _load_static_authorization(root: Path) -> dict[str, Any]:
    path = (root / AUTHORIZATION_PATH).resolve()
    authorization = _committed(path)
    code = _code_records(root)
    if (
        authorization.get("schema_version") != SCHEMA_VERSION
        or authorization.get("algorithm_version") != ALGORITHM_VERSION
        or authorization.get("state") != "m3_source_predictor_sentinel_bbox_repair_authorized"
        or authorization.get("parent_authorization_commit_sha256")
        != PARENT_AUTHORIZATION_COMMIT_SHA256
        or authorization.get("daymet_order_repair_authorization_commit_sha256")
        != DAYMET_REPAIR_AUTHORIZATION_COMMIT_SHA256
        or authorization.get("code_identity")
        != {"files": code, "set_sha256": canonical_sha256(code)}
    ):
        raise SentinelBBoxRepairError("Sentinel bbox repair authorization drifted.")
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
    observed = _load_static_authorization(root)
    expected = build_authorization(root)
    if observed != expected:
        raise SentinelBBoxRepairError("Sentinel bbox repair authorization mismatch.")
    return observed


def _completion_payload(
    root: Path,
    authorization: Mapping[str, Any],
    marker: Mapping[str, Any],
    marker_path: Path,
) -> dict[str, Any]:
    return _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_sentinel_bbox_repair_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "city_id": "houston_tx",
            "sentinel_inventory_commit_sha256": marker["commit_sha256"],
            "sentinel_inventory": _file_record(
                root, marker_path, commit_sha256=marker["commit_sha256"]
            ),
            "discovery_query_geometry": "exact_authenticated_aoi_envelope",
            "scientific_selection_geometry": "exact_authenticated_aoi",
            "queue_mutated": False,
            "blind_city_accessed": False,
            "target_or_landsat_values_read": False,
            "model_fit_select_predict_or_score_performed": False,
            "next_safe_stage": "resume_same_daymet_repair_runner_online_acquisition",
        }
    )


def execute_repair(project_root: str | Path) -> dict[str, Any]:
    """Build and authenticate only the missing Houston inventory."""

    root = _root(project_root)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    marker_path = settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json"
    with exclusive_predictor_worker(settings.worker_lock):
        authorization = authenticate_authorization(root)
        parent = load_m3_source_predictor_extension_runtime_permit(
            root, settings.authorization, settings.config_path
        )
        adapter = DaymetOrderRepairAdapter(settings, parent, ONLINE_PHASE)
        if marker_path.exists():
            raise SentinelBBoxRepairError("Houston Sentinel inventory unexpectedly exists.")

        import la_heat.multicity.m3_source_predictor_extension_worker_v1 as worker_module
        import la_heat.sentinel_inventory as sentinel_module

        original_query = sentinel_module.query_sentinel_items
        original_write = worker_module._write_exclusive

        def bbox_query(
            client: Any,
            *,
            intersects: Any,
            datetime_interval: str,
            global_cloud_cover_max: float | None = None,
            collection: str,
        ) -> tuple[Any, ...]:
            return original_query(
                client,
                intersects=intersects.envelope,
                datetime_interval=datetime_interval,
                global_cloud_cover_max=global_cloud_cover_max,
                collection=collection,
            )

        def lineage_write(payload: Mapping[str, Any], destination: Path) -> None:
            if destination.resolve() == marker_path.resolve():
                unsigned = dict(payload)
                unsigned.pop("commit_sha256", None)
                unsigned.update(
                    {
                        "sentinel_bbox_repair_authorization_commit_sha256": authorization[
                            "commit_sha256"
                        ],
                        "stac_discovery_query_geometry": "exact_authenticated_aoi_envelope",
                        "local_selection_geometry": "exact_authenticated_aoi",
                    }
                )
                payload = _with_commit(unsigned)
            original_write(payload, destination)

        sentinel_module.query_sentinel_items = bbox_query
        worker_module._write_exclusive = lineage_write
        try:
            adapter._build_sentinel_inventory("houston_tx", marker_path)
        finally:
            sentinel_module.query_sentinel_items = original_query
            worker_module._write_exclusive = original_write

        marker, *_ = adapter._authenticate_sentinel_inventory_for_cache("houston_tx", marker_path)
        if (
            marker.get("sentinel_bbox_repair_authorization_commit_sha256")
            != authorization["commit_sha256"]
            or marker.get("stac_discovery_query_geometry") != "exact_authenticated_aoi_envelope"
            or marker.get("local_selection_geometry") != "exact_authenticated_aoi"
        ):
            raise SentinelBBoxRepairError("Houston inventory repair lineage is absent.")
        completion = _completion_payload(root, authorization, marker, marker_path)
        _write_exclusive(completion, root / COMPLETION_PATH)
        return completion


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    authorization = _load_static_authorization(root)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    marker_path = settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json"
    marker = _committed(marker_path)
    observed = _committed(root / COMPLETION_PATH)
    expected = _completion_payload(root, authorization, marker, marker_path)
    if observed != expected:
        raise SentinelBBoxRepairError("Sentinel bbox repair completion mismatch.")
    return observed
