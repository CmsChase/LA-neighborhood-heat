"""Narrow, append-only repair path for three malformed M3 source assets.

The original M3 protocol, source-QA authorization, scene plan, and SQLite task
plan remain immutable.  This module can authorize exactly three already-frozen
source assets whose Planetary Computer blobs were confirmed to contain HTML
instead of GeoTIFF bytes.  Execution is permitted only after that independent
authorization exists and the original queue is paused with no active leases.

No URL, signed query string, credential, source-directory path, model fit, or
blind-test-city reference is written to either repair manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import urlsplit

import planetary_computer as pc
import requests

from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_asset_cache import (
    PLAN_FILENAME,
    cache_asset_from_href,
    load_scene_plan,
)
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    SOURCE_CITY_IDS,
    RunnerSettings,
    authenticate_expanded_inventory,
    load_runner_settings,
    source_run_id,
    task_specs_from_inventory,
)
from la_heat.multicity.m3_source_qa_authorization import (
    authenticate_m3_source_qa_authorization,
)
from la_heat.multicity.target_processor import PlanetaryComputerSceneHydrator
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-asset-repair-v1"
INCIDENT_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development/"
    "M3_SOURCE_ASSET_REPAIR_V1_INCIDENT.json"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_ASSET_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development/"
    "M3_SOURCE_ASSET_REPAIR_V1_COMPLETE.json"
)
SOURCE_MODES: Final = (
    "planetary_computer_restored",
    "official_original_directory",
)
SourceMode = Literal[
    "planetary_computer_restored",
    "official_original_directory",
]

CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_asset_repair_v1.py",
    "scripts/repair_m3_source_assets_v1.py",
    "src/la_heat/multicity/m3_source_asset_cache.py",
    "src/la_heat/multicity/m3_source_development_runtime.py",
    "src/la_heat/multicity/target_processor.py",
    "src/la_heat/aligned_landsat.py",
)

_QUEUE_OBSERVATION_KEYS: Final = (
    "observed_desired_state",
    "observed_running_task_count",
    "observed_active_lease_count",
)

_TIFF_MAGICS: Final = (
    b"II*\x00",
    b"MM\x00*",
    b"II+\x00",
    b"MM\x00+",
)
_PC_BLOB_HOST: Final = "landsateuwest.blob.core.windows.net"
_BAD_BLOB_MAGIC_HEX: Final = "3c21444f"
_BAD_BLOB_CLASSIFICATION: Final = "html_error_payload_not_tiff"


class M3SourceAssetRepairError(RuntimeError):
    """Raised when the versioned repair leaves its exact source-only contract."""


@dataclass(frozen=True, slots=True)
class RepairAsset:
    """One immutable official file identity allowed by the repair."""

    city_id: str
    scene_id: str
    asset: str
    usgs_product_id: str
    filename: str
    official_md5: str
    bad_blob_bytes: int
    bad_blob_md5: str
    bad_blob_sha256: str


REPAIR_ASSETS: Final = (
    RepairAsset(
        city_id="chicago_il",
        scene_id="LC08_L2SP_023031_20220727_02_T1",
        asset="lwir11",
        usgs_product_id="LC08_L2SP_023031_20220727_20220801_02_T1",
        filename="LC08_L2SP_023031_20220727_20220801_02_T1_ST_B10.TIF",
        official_md5="fc0971b593dd08d9f279c5fe9b9e94b3",
        bad_blob_bytes=12_351,
        bad_blob_md5="026f674f002b9f986882187962eb21cb",
        bad_blob_sha256="fa44d1717e6a4e9a4b814f3d5e84c02e261762f43ffa54f43ec3e2095b30a354",
    ),
    RepairAsset(
        city_id="chicago_il",
        scene_id="LC08_L2SP_023031_20220727_02_T1",
        asset="qa_radsat",
        usgs_product_id="LC08_L2SP_023031_20220727_20220801_02_T1",
        filename="LC08_L2SP_023031_20220727_20220801_02_T1_QA_RADSAT.TIF",
        official_md5="3f56604c33f7f4b2fc4c48dc855adf2e",
        bad_blob_bytes=12_351,
        bad_blob_md5="026f674f002b9f986882187962eb21cb",
        bad_blob_sha256="fa44d1717e6a4e9a4b814f3d5e84c02e261762f43ffa54f43ec3e2095b30a354",
    ),
    RepairAsset(
        city_id="houston_tx",
        scene_id="LC09_L2SP_025040_20220514_02_T1",
        asset="qa_radsat",
        usgs_product_id="LC09_L2SP_025040_20220514_20220516_02_T1",
        filename="LC09_L2SP_025040_20220514_20220516_02_T1_QA_RADSAT.TIF",
        official_md5="77aa03d424ce15e47eddb96276e1e401",
        bad_blob_bytes=12_351,
        bad_blob_md5="4d711b5ab112bc8ee744f42e2d726e43",
        bad_blob_sha256="aec44631077762c51f42df01a8b54036b7f4338c6f45d9359e93c0f821fc5274",
    ),
)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceAssetRepairError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _require_digest(value: object, *, length: int, label: str) -> str:
    text = str(value)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise M3SourceAssetRepairError(f"{label} is not a lowercase hexadecimal digest.")
    return text


def _committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _read_committed_json(path: Path, *, state: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourceAssetRepairError(f"Cannot read {label}: {path}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != state
        or not _committed(payload)
    ):
        raise M3SourceAssetRepairError(f"{label} state or commit is invalid.")
    return payload


def _file_record(root: Path, path: Path, *, commit: object | None = None) -> dict[str, Any]:
    resolved = _inside(root, path, label="Bound input")
    if not resolved.is_file() or resolved.is_symlink():
        raise M3SourceAssetRepairError(f"Bound input is missing or is a symlink: {resolved}")
    result: dict[str, Any] = {
        "path": _relative(root, resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if commit is not None:
        result["commit_sha256"] = _require_digest(commit, length=64, label="Input commit")
    return result


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceAssetRepairError(
            f"Append-only repair manifest already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _json_text(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise M3SourceAssetRepairError("Task plan contains non-canonical JSON data.") from error


def _expected_task_plan(inventory: Mapping[str, Any]) -> tuple[list[tuple[str, str, str]], str]:
    rows = [
        (spec.task_id, spec.kind, _json_text(spec.payload))
        for spec in task_specs_from_inventory(inventory)
    ]
    serializable = [
        {"task_id": task_id, "kind": kind, "payload_json": payload_json}
        for task_id, kind, payload_json in rows
    ]
    digest = hashlib.sha256(_json_text(serializable).encode("utf-8")).hexdigest()
    return rows, digest


def _queue_snapshot(
    settings: RunnerSettings,
    inventory: Mapping[str, Any],
    *,
    require_paused: bool,
    incident_task_ids: Sequence[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Authenticate the immutable plan through a read-only SQLite snapshot."""

    if not settings.database.is_file():
        raise M3SourceAssetRepairError("The bound source-development SQLite database is missing.")
    run_id = source_run_id(inventory)
    expected_rows, expected_sha256 = _expected_task_plan(inventory)
    uri = settings.database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        run = connection.execute(
            """
            SELECT task_plan_sha256, desired_state, schema_version
            FROM model_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        observed_rows = [
            (str(row["task_id"]), str(row["kind"]), str(row["payload_json"]))
            for row in connection.execute(
                """
                SELECT task_id, kind, payload_json
                FROM model_run_tasks
                WHERE run_id = ?
                ORDER BY plan_index
                """,
                (run_id,),
            )
        ]
        running = int(
            connection.execute(
                "SELECT COUNT(*) FROM model_run_tasks WHERE run_id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()[0]
        )
        active_leases = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM model_run_tasks
                WHERE run_id = ?
                  AND (lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL)
                """,
                (run_id,),
            ).fetchone()[0]
        )
        incident_rows: list[sqlite3.Row] = []
        for task_id in incident_task_ids:
            row = connection.execute(
                """
                SELECT task_id, kind, payload_json, status, attempt,
                       claim_generation, error_type, updated_at,
                       lease_owner, lease_expires_at
                FROM model_run_tasks
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, task_id),
            ).fetchone()
            if row is None:
                raise M3SourceAssetRepairError(
                    f"Incident task is missing from the frozen queue: {task_id}"
                )
            incident_rows.append(row)
    except sqlite3.Error as error:
        raise M3SourceAssetRepairError("Cannot authenticate the source task plan.") from error
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
    if run is None:
        raise M3SourceAssetRepairError(f"The frozen source run is missing: {run_id}")
    if (
        schema_version != 1
        or int(run["schema_version"]) != 1
        or str(run["task_plan_sha256"]) != expected_sha256
        or observed_rows != expected_rows
    ):
        raise M3SourceAssetRepairError("The frozen SQLite task plan drifted.")
    if require_paused and (
        str(run["desired_state"]) != "paused"
        or running != 0
        or active_leases != 0
    ):
        raise M3SourceAssetRepairError(
            "Repair requires the original queue to be paused with no running task or lease."
        )
    binding = {
        "run_id": run_id,
        "schema_version": schema_version,
        "task_plan_sha256": expected_sha256,
        "task_count": len(expected_rows),
        "observed_desired_state": str(run["desired_state"]),
        "observed_running_task_count": running,
        "observed_active_lease_count": active_leases,
    }
    snapshots: list[dict[str, Any]] = []
    for row in incident_rows:
        updated_at = float(row["updated_at"])
        if not math.isfinite(updated_at):
            raise M3SourceAssetRepairError("Incident task has an invalid updated_at value.")
        snapshots.append(
            {
                "task_id": str(row["task_id"]),
                "kind": str(row["kind"]),
                "payload_json_sha256": hashlib.sha256(
                    str(row["payload_json"]).encode("utf-8")
                ).hexdigest(),
                "status": str(row["status"]),
                "attempt": int(row["attempt"]),
                "claim_generation": int(row["claim_generation"]),
                "error_type": row["error_type"],
                "updated_at_epoch_seconds": updated_at,
                "updated_at_utc": datetime.fromtimestamp(updated_at, tz=UTC).isoformat(),
                "lease_owner": row["lease_owner"],
                "lease_expires_at": row["lease_expires_at"],
            }
        )
    return binding, snapshots


def _queue_binding(
    settings: RunnerSettings,
    inventory: Mapping[str, Any],
    *,
    require_paused: bool,
) -> dict[str, Any]:
    observed, snapshots = _queue_snapshot(
        settings,
        inventory,
        require_paused=require_paused,
    )
    if snapshots:
        raise AssertionError("Queue binding unexpectedly captured incident tasks.")
    return {
        key: value for key, value in observed.items() if key not in _QUEUE_OBSERVATION_KEYS
    }


def _task_id(spec: RepairAsset) -> str:
    token = canonical_sha256([spec.city_id, spec.scene_id])[:20]
    return f"asset-{token}-{spec.asset}"


def _repair_records(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    scenes = plan.get("scenes")
    grids = plan.get("grids")
    required_assets = plan.get("required_assets")
    if not isinstance(scenes, list) or not isinstance(grids, Mapping):
        raise M3SourceAssetRepairError("The authenticated scene plan is incomplete.")
    if not isinstance(required_assets, list):
        raise M3SourceAssetRepairError("The scene plan has no required-asset contract.")
    scene_lookup = {
        str(row.get("scene_id")): row for row in scenes if isinstance(row, Mapping)
    }
    records: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for spec in REPAIR_ASSETS:
        identity = (spec.scene_id, spec.asset)
        if identity in identities:
            raise M3SourceAssetRepairError("Repair asset identities are duplicated.")
        identities.add(identity)
        if spec.city_id not in SOURCE_CITY_IDS or spec.city_id in BLIND_CITY_IDS:
            raise M3SourceAssetRepairError("Repair authorization crossed into a blind city.")
        _require_digest(spec.official_md5, length=32, label="Official MD5")
        scene = scene_lookup.get(spec.scene_id)
        grid = grids.get(spec.city_id)
        if (
            not isinstance(scene, Mapping)
            or scene.get("city_id") != spec.city_id
            or spec.asset not in required_assets
            or not isinstance(grid, Mapping)
        ):
            raise M3SourceAssetRepairError("A repair asset is detached from the scene plan.")
        grid_sha256 = _require_digest(
            grid.get("sha256"), length=64, label="Repair grid commit"
        )
        if not spec.filename.startswith(spec.usgs_product_id + "_"):
            raise M3SourceAssetRepairError("Official filename is detached from its product ID.")
        records.append(
            {
                "city_id": spec.city_id,
                "scene_id": spec.scene_id,
                "asset": spec.asset,
                "task_id": _task_id(spec),
                "grid_sha256": grid_sha256,
                "usgs_product_id": spec.usgs_product_id,
                "official_filename": spec.filename,
                "official_md5": spec.official_md5,
            }
        )
    if len(records) != 3:
        raise M3SourceAssetRepairError("Repair authorization must contain exactly three assets.")
    return records


def _authenticated_foundations(
    project_root: str | Path,
) -> tuple[
    RunnerSettings,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    root = Path(project_root).resolve()
    settings = load_runner_settings(root)
    protocol = authenticate_m3_development_protocol_lock(root, settings.protocol_lock)
    amendment = authenticate_m3_source_acquisition_amendment(root, settings.amendment)
    inventory = authenticate_expanded_inventory(settings, amendment)
    authorization = authenticate_m3_source_qa_authorization(root, settings.authorization)
    plan = load_scene_plan(settings.cache_root)
    expected_bindings = {
        "m3_protocol_lock": protocol.get("commit_sha256"),
        "source_acquisition_amendment": amendment.get("commit_sha256"),
        "expanded_source_inventory": inventory.get("commit_sha256"),
        "source_qa_execution_authorization": authorization.get("commit_sha256"),
    }
    if (
        authorization.get("state") != "source_qa_two_phase_execution_authorized"
        or authorization.get("blind_test_target_access_authorized") is not False
        or plan.get("bindings") != expected_bindings
    ):
        raise M3SourceAssetRepairError("The repair inputs are detached from the old authorization.")
    return settings, protocol, amendment, inventory, authorization, plan


def _authenticated_components(
    project_root: str | Path,
    *,
    require_paused: bool,
) -> tuple[
    RunnerSettings,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    (
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
    ) = _authenticated_foundations(project_root)
    queue = _queue_binding(settings, inventory, require_paused=require_paused)
    return settings, protocol, amendment, inventory, authorization, plan, queue


def _incident_input_records(
    root: Path,
    settings: RunnerSettings,
    protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    inventory: Mapping[str, Any],
    authorization: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "m3_protocol_lock": _file_record(
            root, settings.protocol_lock, commit=protocol["commit_sha256"]
        ),
        "source_acquisition_amendment": _file_record(
            root, settings.amendment, commit=amendment["commit_sha256"]
        ),
        "expanded_source_inventory": _file_record(
            root, settings.inventory, commit=inventory["commit_sha256"]
        ),
        "source_qa_execution_authorization": _file_record(
            root, settings.authorization, commit=authorization["commit_sha256"]
        ),
        "source_cache_scene_plan": _file_record(
            root,
            settings.cache_root / PLAN_FILENAME,
            commit=plan["commit_sha256"],
        ),
    }


def _cache_absence_record(
    root: Path,
    settings: RunnerSettings,
    spec: RepairAsset,
    *,
    verify_absent: bool,
) -> dict[str, Any]:
    asset_root = (
        settings.cache_root
        / "cities"
        / spec.city_id
        / "scenes"
        / spec.scene_id
        / "assets"
    )
    output = asset_root / f"{spec.asset}.tif"
    commit = asset_root / f"{spec.asset}.CONTENT_COMMIT.json"
    parts = tuple(asset_root.glob(f".{spec.asset}.tif.*.part")) if asset_root.is_dir() else ()
    if verify_absent and (
        output.exists()
        or output.is_symlink()
        or commit.exists()
        or commit.is_symlink()
        or parts
    ):
        raise M3SourceAssetRepairError(
            f"Incident cache absence changed for {spec.scene_id}/{spec.asset}."
        )
    return {
        "output_path": _relative(root, output),
        "output_present": False,
        "content_commit_path": _relative(root, commit),
        "content_commit_present": False,
        "temporary_part_count": 0,
    }


def _bad_blob_fingerprint(spec: RepairAsset) -> dict[str, Any]:
    return {
        "classification": _BAD_BLOB_CLASSIFICATION,
        "bytes": spec.bad_blob_bytes,
        "md5": _require_digest(spec.bad_blob_md5, length=32, label="Bad-blob MD5"),
        "sha256": _require_digest(
            spec.bad_blob_sha256,
            length=64,
            label="Bad-blob SHA-256",
        ),
        "first_four_bytes_hex": _BAD_BLOB_MAGIC_HEX,
        "observation_source": "prior_read_only_upstream_blob_diagnostic",
    }


def _expected_payload_sha256s(inventory: Mapping[str, Any]) -> dict[str, str]:
    return {
        task_id: hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        for task_id, kind, payload_json in _expected_task_plan(inventory)[0]
        if kind == "download_asset"
    }


def _incident_payload(
    root: Path,
    settings: RunnerSettings,
    protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    inventory: Mapping[str, Any],
    authorization: Mapping[str, Any],
    plan: Mapping[str, Any],
    queue: Mapping[str, Any],
    task_snapshots: Sequence[Mapping[str, Any]],
    *,
    verify_cache_absent: bool,
) -> dict[str, Any]:
    records = _repair_records(plan)
    expected_payloads = _expected_payload_sha256s(inventory)
    snapshots = {str(row.get("task_id")): dict(row) for row in task_snapshots}
    affected: list[dict[str, Any]] = []
    for spec, record in zip(REPAIR_ASSETS, records, strict=True):
        task_id = _task_id(spec)
        snapshot = snapshots.get(task_id)
        if snapshot is None or set(snapshot) != {
            "task_id",
            "kind",
            "payload_json_sha256",
            "status",
            "attempt",
            "claim_generation",
            "error_type",
            "updated_at_epoch_seconds",
            "updated_at_utc",
            "lease_owner",
            "lease_expires_at",
        }:
            raise M3SourceAssetRepairError("Incident task snapshot is incomplete.")
        timestamp = snapshot["updated_at_epoch_seconds"]
        if (
            snapshot["task_id"] != task_id
            or snapshot["kind"] != "download_asset"
            or snapshot["payload_json_sha256"] != expected_payloads.get(task_id)
            or snapshot["status"] != "pending"
            or type(snapshot["attempt"]) is not int
            or snapshot["attempt"] < 1
            or type(snapshot["claim_generation"]) is not int
            or snapshot["claim_generation"] < 1
            or snapshot["error_type"] != "RasterioIOError"
            or type(timestamp) not in (int, float)
            or not math.isfinite(float(timestamp))
            or float(timestamp) <= 0
            or snapshot["updated_at_utc"]
            != datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()
            or snapshot["lease_owner"] is not None
            or snapshot["lease_expires_at"] is not None
        ):
            raise M3SourceAssetRepairError("Incident task snapshot is not the frozen failure.")
        affected.append(
            {
                "city_id": record["city_id"],
                "scene_id": record["scene_id"],
                "asset": record["asset"],
                "task_id": task_id,
                "task_snapshot": snapshot,
                "cache_absence": _cache_absence_record(
                    root,
                    settings,
                    spec,
                    verify_absent=verify_cache_absent,
                ),
                "bad_blob_safe_fingerprint": _bad_blob_fingerprint(spec),
            }
        )
    if set(snapshots) != {row["task_id"] for row in records}:
        raise M3SourceAssetRepairError("Incident contains an unauthorized task snapshot.")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_asset_repair_incident_frozen",
        "m3_protocol_lock_commit_sha256": protocol["commit_sha256"],
        "source_acquisition_amendment_commit_sha256": amendment["commit_sha256"],
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "source_qa_execution_authorization_commit_sha256": authorization[
            "commit_sha256"
        ],
        "scene_plan_commit_sha256": plan["commit_sha256"],
        "runtime_task_plan": {
            **queue,
            "incident_task_ids": [row["task_id"] for row in records],
        },
        "inputs": _incident_input_records(
            root,
            settings,
            protocol,
            amendment,
            inventory,
            authorization,
            plan,
        ),
        "affected_asset_count": len(affected),
        "affected_assets": affected,
        "affected_asset_set_sha256": canonical_sha256(affected),
        "remote_locator_or_response_payload_persisted": False,
        "alternative_asset_bytes_or_values_read_by_incident_builder": False,
        "cache_or_queue_modified_by_incident_builder": False,
        "blind_test_city_accessed": False,
        "next_safe_stage": "create_independent_source_asset_repair_authorization",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def build_source_asset_repair_incident(project_root: str | Path) -> dict[str, Any]:
    """Freeze the three failed task rows without reading replacement asset values."""

    root = Path(project_root).resolve()
    (
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
    ) = _authenticated_foundations(root)
    records = _repair_records(plan)
    queue, snapshots = _queue_snapshot(
        settings,
        inventory,
        require_paused=True,
        incident_task_ids=[row["task_id"] for row in records],
    )
    return _incident_payload(
        root,
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
        queue,
        snapshots,
        verify_cache_absent=True,
    )


def create_source_asset_repair_incident(
    project_root: str | Path,
    incident_path: str | Path = INCIDENT_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, incident_path, label="Repair incident")
    payload = build_source_asset_repair_incident(root)
    _write_exclusive(payload, destination)
    return authenticate_source_asset_repair_incident(root, destination)


def authenticate_source_asset_repair_incident(
    project_root: str | Path,
    incident_path: str | Path = INCIDENT_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, incident_path, label="Repair incident")
    observed = _read_committed_json(
        path,
        state="source_asset_repair_incident_frozen",
        label="Source-asset repair incident",
    )
    (
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
    ) = _authenticated_foundations(root)
    queue = _queue_binding(settings, inventory, require_paused=False)
    observed_runtime = observed.get("runtime_task_plan")
    if not isinstance(observed_runtime, Mapping):
        raise M3SourceAssetRepairError("Repair incident has no runtime observation.")
    frozen_observation = {
        key: observed_runtime.get(key) for key in _QUEUE_OBSERVATION_KEYS
    }
    if frozen_observation != {
        "observed_desired_state": "paused",
        "observed_running_task_count": 0,
        "observed_active_lease_count": 0,
    }:
        raise M3SourceAssetRepairError("Repair incident was not frozen at a zero-lease pause.")
    queue.update(frozen_observation)
    raw_assets = observed.get("affected_assets")
    if not isinstance(raw_assets, list):
        raise M3SourceAssetRepairError("Repair incident has no affected assets.")
    snapshots = [
        row.get("task_snapshot")
        for row in raw_assets
        if isinstance(row, Mapping) and isinstance(row.get("task_snapshot"), Mapping)
    ]
    expected = _incident_payload(
        root,
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
        queue,
        snapshots,
        verify_cache_absent=False,
    )
    if observed != expected:
        raise M3SourceAssetRepairError("Source-asset repair incident drifted.")
    return observed


def _build_authorization(
    project_root: str | Path,
    *,
    incident_path: str | Path,
    completion_path: str | Path,
    require_paused: bool,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    incident_file = _inside(root, incident_path, label="Repair incident")
    incident = authenticate_source_asset_repair_incident(root, incident_file)
    (
        settings,
        protocol,
        amendment,
        inventory,
        authorization,
        plan,
        queue,
    ) = _authenticated_components(root, require_paused=require_paused)
    completion = _inside(root, completion_path, label="Repair completion")
    records = _repair_records(plan)
    task_ids = {row["task_id"] for row in records}
    expected_tasks, _ = _expected_task_plan(inventory)
    observed_tasks = {task_id for task_id, kind, _ in expected_tasks if kind == "download_asset"}
    if not task_ids.issubset(observed_tasks):
        raise M3SourceAssetRepairError("A repair asset is detached from the frozen task plan.")
    if (
        incident.get("scene_plan_commit_sha256") != plan.get("commit_sha256")
        or incident.get("runtime_task_plan", {}).get("task_plan_sha256")
        != queue.get("task_plan_sha256")
        or incident.get("runtime_task_plan", {}).get("incident_task_ids")
        != [row["task_id"] for row in records]
    ):
        raise M3SourceAssetRepairError("Repair incident is detached from the authorization.")
    code_identity = {
        relative: _file_record(root, root / relative) for relative in CODE_PATHS
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_asset_repair_authorized",
        "source_asset_repair_incident_commit_sha256": incident["commit_sha256"],
        "m3_protocol_lock_commit_sha256": protocol["commit_sha256"],
        "source_acquisition_amendment_commit_sha256": amendment["commit_sha256"],
        "expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "source_qa_execution_authorization_commit_sha256": authorization["commit_sha256"],
        "scene_plan_commit_sha256": plan["commit_sha256"],
        "runtime_task_plan": {
            **queue,
            "repair_task_ids": [row["task_id"] for row in records],
        },
        "inputs": {
            "source_asset_repair_incident": _file_record(
                root,
                incident_file,
                commit=incident["commit_sha256"],
            ),
            "m3_protocol_lock": _file_record(
                root, settings.protocol_lock, commit=protocol["commit_sha256"]
            ),
            "source_acquisition_amendment": _file_record(
                root, settings.amendment, commit=amendment["commit_sha256"]
            ),
            "expanded_source_inventory": _file_record(
                root, settings.inventory, commit=inventory["commit_sha256"]
            ),
            "source_qa_execution_authorization": _file_record(
                root, settings.authorization, commit=authorization["commit_sha256"]
            ),
            "source_cache_scene_plan": _file_record(
                root,
                settings.cache_root / PLAN_FILENAME,
                commit=plan["commit_sha256"],
            ),
        },
        "repair_assets": records,
        "repair_asset_set_sha256": canonical_sha256(records),
        "allowed_source_modes": list(SOURCE_MODES),
        "permissions": {
            "read_exact_three_official_source_asset_files": True,
            "accept_planetary_computer_only_after_original_md5_is_restored": True,
            "accept_user_supplied_official_original_product_directories": True,
            "write_exact_three_existing_cache_content_commits": True,
            "create_append_only_repair_completion": True,
            "mutate_or_rebuild_sqlite_task_plan": False,
            "delete_or_replace_existing_valid_cache": False,
            "change_year_city_scene_asset_or_support_gate": False,
            "read_predictors_targets_or_blind_city_assets": False,
            "fit_select_predict_or_score": False,
        },
        "source_asset_repair_incident_path": _relative(root, incident_file),
        "repair_completion_path": _relative(root, completion),
        "signed_urls_tokens_credentials_or_source_paths_may_be_persisted": False,
        "blind_test_asset_predictor_qa_or_target_access_authorized": False,
        "access_audit": {
            "authorization_read_alternative_asset_bytes_or_values": False,
            "authorization_hydrated_an_asset_href": False,
            "authorization_modified_cache_or_queue": False,
            "blind_test_city_accessed": False,
        },
        "code_identity": code_identity,
        "next_safe_stage": "run_exact_source_asset_repair_while_original_queue_is_paused",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def build_source_asset_repair_authorization(
    project_root: str | Path,
    *,
    incident_path: str | Path = INCIDENT_PATH,
    completion_path: str | Path = COMPLETION_PATH,
) -> dict[str, Any]:
    """Build a value-blind authorization preview while the old queue is paused."""

    return _build_authorization(
        project_root,
        incident_path=incident_path,
        completion_path=completion_path,
        require_paused=True,
    )


def create_source_asset_repair_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    *,
    incident_path: str | Path = INCIDENT_PATH,
    completion_path: str | Path = COMPLETION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, authorization_path, label="Repair authorization")
    payload = build_source_asset_repair_authorization(
        root,
        incident_path=incident_path,
        completion_path=completion_path,
    )
    _write_exclusive(payload, destination)
    return authenticate_source_asset_repair_authorization(root, destination)


def authenticate_source_asset_repair_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Repair authorization")
    observed = _read_committed_json(
        path,
        state="source_asset_repair_authorized",
        label="Source-asset repair authorization",
    )
    expected = _build_authorization(
        root,
        incident_path=str(observed.get("source_asset_repair_incident_path", "")),
        completion_path=str(observed.get("repair_completion_path", "")),
        require_paused=False,
    )
    if observed != expected:
        raise M3SourceAssetRepairError("Source-asset repair authorization drifted.")
    return observed


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _official_path(source_root: Path, spec: RepairAsset) -> Path:
    if not source_root.is_dir() or source_root.is_symlink():
        raise M3SourceAssetRepairError("Official source root is missing or is a symlink.")
    product = source_root / spec.usgs_product_id
    path = product / spec.filename
    if (
        product.name != spec.usgs_product_id
        or path.name != spec.filename
        or not product.is_dir()
        or product.is_symlink()
        or not path.is_file()
        or path.is_symlink()
    ):
        raise M3SourceAssetRepairError(
            f"Official product/filename identity is missing for {spec.scene_id}/{spec.asset}."
        )
    return path


def _validate_official_file(source_root: Path, spec: RepairAsset) -> tuple[Path, dict[str, Any]]:
    path = _official_path(source_root, spec)
    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic not in _TIFF_MAGICS:
        raise M3SourceAssetRepairError(
            f"Official source is not a TIFF for {spec.scene_id}/{spec.asset}."
        )
    observed_md5 = _md5_file(path)
    if observed_md5 != spec.official_md5:
        raise M3SourceAssetRepairError(
            f"Official MD5 mismatch for {spec.scene_id}/{spec.asset}."
        )
    return path, {
        "bytes": path.stat().st_size,
        "official_md5": observed_md5,
        "sha256": sha256_file(path),
        "tiff_magic_hex": magic.hex(),
    }


def _validate_pc_href(href: str, spec: RepairAsset) -> None:
    parsed = urlsplit(href)
    parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _PC_BLOB_HOST
        or parsed.query
        or len(parts) < 2
        or parts[-2:] != (spec.usgs_product_id, spec.filename)
    ):
        raise M3SourceAssetRepairError(
            f"Planetary Computer identity changed for {spec.scene_id}/{spec.asset}."
        )


def _validate_signed_pc_href(canonical_href: str, signed_href: str) -> None:
    canonical = urlsplit(canonical_href)
    signed = urlsplit(signed_href)
    if (
        signed.scheme != canonical.scheme
        or signed.hostname != canonical.hostname
        or signed.port != canonical.port
        or signed.path != canonical.path
        or signed.username is not None
        or signed.password is not None
        or signed.fragment
    ):
        raise M3SourceAssetRepairError(
            "Planetary Computer signer changed the authorized asset identity."
        )


@contextmanager
def _repair_temporary_directory(
    settings: RunnerSettings,
    *,
    prefix: str,
) -> Iterator[Path]:
    temporary_parent = settings.cache_root / ".repair_tmp"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    if temporary_parent.is_symlink():
        raise M3SourceAssetRepairError("Repair temporary root must not be a symlink.")
    with tempfile.TemporaryDirectory(
        prefix=prefix,
        dir=temporary_parent,
    ) as temporary:
        yield Path(temporary)


@contextmanager
def _download_restored_pc_sources(
    settings: RunnerSettings,
    *,
    hydrator: Callable[[str], Mapping[str, str]],
    signer: Callable[[str], str],
    get: Callable[..., Any],
) -> Iterator[Path]:
    with _repair_temporary_directory(
        settings,
        prefix="m3-source-repair-v1-",
    ) as source_root:
        hrefs_by_scene: dict[str, Mapping[str, str]] = {}
        for spec in REPAIR_ASSETS:
            hrefs = hrefs_by_scene.get(spec.scene_id)
            if hrefs is None:
                hrefs = dict(hydrator(spec.scene_id))
                hrefs_by_scene[spec.scene_id] = hrefs
            canonical_href = hrefs.get(spec.asset)
            if not isinstance(canonical_href, str):
                raise M3SourceAssetRepairError(
                    f"Planetary Computer lacks {spec.scene_id}/{spec.asset}."
                )
            _validate_pc_href(canonical_href, spec)
            signed_href = signer(canonical_href)
            if not isinstance(signed_href, str) or not signed_href:
                raise M3SourceAssetRepairError("Planetary Computer signer returned no href.")
            _validate_signed_pc_href(canonical_href, signed_href)
            product = source_root / spec.usgs_product_id
            product.mkdir(parents=True, exist_ok=True)
            destination = product / spec.filename
            response = get(
                signed_href,
                stream=True,
                timeout=settings.network_timeout_seconds,
                allow_redirects=False,
            )
            try:
                if int(response.status_code) != 200:
                    raise M3SourceAssetRepairError(
                        f"Planetary Computer did not return an asset for "
                        f"{spec.scene_id}/{spec.asset}."
                    )
                with destination.open("xb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                response.close()
        yield source_root


def _source_mode(value: object) -> SourceMode:
    text = str(value)
    if text not in SOURCE_MODES:
        raise M3SourceAssetRepairError(f"Unknown repair source mode: {text!r}")
    return cast(SourceMode, text)


def _assert_live_binding(
    settings: RunnerSettings,
    inventory: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> None:
    queue = _queue_binding(settings, inventory, require_paused=True)
    if queue != {
        key: value
        for key, value in authorization["runtime_task_plan"].items()
        if key != "repair_task_ids"
    }:
        raise M3SourceAssetRepairError("Live task-plan binding differs from the repair permit.")


def _cached_content_commits(
    settings: RunnerSettings,
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []

    def forbidden_signer(_value: str) -> str:
        raise M3SourceAssetRepairError("An authorized repair content commit is missing.")

    for spec in REPAIR_ASSETS:
        commit = cache_asset_from_href(
            settings.cache_root,
            plan,
            spec.scene_id,
            spec.asset,
            "AUTHORIZED_REPAIR_CONTENT_ALREADY_CACHED",
            before_value_access=lambda: None,
            signer=forbidden_signer,
        )
        if commit.get("scene_id") != spec.scene_id or commit.get("asset") != spec.asset:
            raise M3SourceAssetRepairError("A repaired content commit changed identity.")
        commits.append(dict(commit))
    return commits


def _completion_payload(
    authorization: Mapping[str, Any],
    *,
    source_mode: SourceMode,
    content_commits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(content_commits) != len(REPAIR_ASSETS):
        raise M3SourceAssetRepairError("Repair completion requires exactly three commits.")
    records: list[dict[str, Any]] = []
    for spec, content in zip(REPAIR_ASSETS, content_commits, strict=True):
        records.append(
            {
                "city_id": spec.city_id,
                "scene_id": spec.scene_id,
                "asset": spec.asset,
                "task_id": _task_id(spec),
                "usgs_product_id": spec.usgs_product_id,
                "official_filename": spec.filename,
                "official_md5": spec.official_md5,
                "cache_content_commit_sha256": _require_digest(
                    content.get("commit_sha256"),
                    length=64,
                    label="Cache content commit",
                ),
                "reference_alignment_commit_sha256": _require_digest(
                    content.get("commit_sha256"),
                    length=64,
                    label="Reference alignment commit",
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_asset_repair_complete",
        "repair_authorization_commit_sha256": authorization["commit_sha256"],
        "source_asset_repair_incident_commit_sha256": authorization[
            "source_asset_repair_incident_commit_sha256"
        ],
        "m3_protocol_lock_commit_sha256": authorization[
            "m3_protocol_lock_commit_sha256"
        ],
        "source_qa_execution_authorization_commit_sha256": authorization[
            "source_qa_execution_authorization_commit_sha256"
        ],
        "scene_plan_commit_sha256": authorization["scene_plan_commit_sha256"],
        "task_plan_sha256": authorization["runtime_task_plan"]["task_plan_sha256"],
        "source_mode": source_mode,
        "repaired_asset_count": len(records),
        "repaired_assets": records,
        "each_live_cache_commit_matched_exact_md5_reference_alignment": True,
        "queue_or_task_plan_mutated": False,
        "unrelated_valid_cache_deleted_or_replaced": False,
        "signed_url_token_credential_or_source_path_persisted": False,
        "blind_test_asset_predictor_qa_or_target_accessed": False,
        "model_fit_select_predict_or_score_performed": False,
        "next_safe_stage": "resume_original_online_predownload_queue_from_content_commits",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def authenticate_source_asset_repair_completion(
    project_root: str | Path,
    completion_path: str | Path = COMPLETION_PATH,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authorization = authenticate_source_asset_repair_authorization(root, authorization_path)
    expected_path = _inside(
        root,
        str(authorization["repair_completion_path"]),
        label="Authorized repair completion",
    )
    requested = _inside(root, completion_path, label="Repair completion")
    if requested != expected_path:
        raise M3SourceAssetRepairError("Repair completion path differs from the authorization.")
    observed = _read_committed_json(
        requested,
        state="source_asset_repair_complete",
        label="Source-asset repair completion",
    )
    mode = _source_mode(observed.get("source_mode"))
    settings = load_runner_settings(root)
    plan = load_scene_plan(settings.cache_root)
    commits = _cached_content_commits(settings, plan)
    expected = _completion_payload(authorization, source_mode=mode, content_commits=commits)
    if observed != expected:
        raise M3SourceAssetRepairError("Source-asset repair completion drifted.")
    return observed


def run_source_asset_repair(
    project_root: str | Path,
    *,
    source_mode: SourceMode,
    source_directory: str | Path | None = None,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    completion_path: str | Path = COMPLETION_PATH,
    hydrator: Callable[[str], Mapping[str, str]] | None = None,
    signer: Callable[[str], str] = pc.sign,
    get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Repair exactly three cache contents after authenticating the separate permit."""

    root = Path(project_root).resolve()
    mode = _source_mode(source_mode)
    authorization = authenticate_source_asset_repair_authorization(root, authorization_path)
    authorized_completion = _inside(
        root,
        str(authorization["repair_completion_path"]),
        label="Authorized repair completion",
    )
    requested_completion = _inside(root, completion_path, label="Repair completion")
    if requested_completion != authorized_completion:
        raise M3SourceAssetRepairError("Repair completion path differs from the authorization.")
    if requested_completion.exists():
        observed = authenticate_source_asset_repair_completion(
            root,
            requested_completion,
            authorization_path=authorization_path,
        )
        if observed.get("source_mode") != mode:
            raise M3SourceAssetRepairError("Completed repair used a different source mode.")
        return observed

    (
        settings,
        _protocol,
        _amendment,
        inventory,
        _old_authorization,
        plan,
        queue,
    ) = _authenticated_components(root, require_paused=True)
    if queue["task_plan_sha256"] != authorization["runtime_task_plan"]["task_plan_sha256"]:
        raise M3SourceAssetRepairError("Repair task plan changed after authorization.")

    if mode == "official_original_directory":
        if source_directory is None:
            raise M3SourceAssetRepairError("Official source directory is required.")
        source_context: Any = nullcontext(Path(source_directory).expanduser().resolve())
    else:
        if source_directory is not None:
            raise M3SourceAssetRepairError("PC-restored mode does not accept a source directory.")
        if hydrator is None:
            hydrator = PlanetaryComputerSceneHydrator(
                timeout_seconds=settings.network_timeout_seconds
            )
        source_context = _download_restored_pc_sources(
            settings,
            hydrator=hydrator,
            signer=signer,
            get=get,
        )

    with source_context as source_root, _repair_temporary_directory(
        settings,
        prefix="m3-source-repair-reference-v1-",
    ) as reference_root:
        validated = {
            (spec.scene_id, spec.asset): _validate_official_file(source_root, spec)
            for spec in REPAIR_ASSETS
        }

        def gate() -> None:
            current = authenticate_source_asset_repair_authorization(root, authorization_path)
            if current.get("commit_sha256") != authorization.get("commit_sha256"):
                raise M3SourceAssetRepairError("Repair authorization changed during execution.")
            _assert_live_binding(settings, inventory, authorization)

        reference_commits: dict[tuple[str, str], dict[str, Any]] = {}
        for spec in REPAIR_ASSETS:
            source_path, input_record = validated[(spec.scene_id, spec.asset)]
            gate()
            reference = cache_asset_from_href(
                reference_root,
                plan,
                spec.scene_id,
                spec.asset,
                str(source_path),
                before_value_access=gate,
                signer=lambda value: value,
            )
            _path_after, input_after = _validate_official_file(source_root, spec)
            if input_after != input_record:
                raise M3SourceAssetRepairError(
                    f"Official input changed during reference alignment for "
                    f"{spec.scene_id}/{spec.asset}."
                )
            reference_commits[(spec.scene_id, spec.asset)] = dict(reference)

        content_commits: list[dict[str, Any]] = []
        for spec in REPAIR_ASSETS:
            source_path, input_record = validated[(spec.scene_id, spec.asset)]
            gate()
            commit = cache_asset_from_href(
                settings.cache_root,
                plan,
                spec.scene_id,
                spec.asset,
                str(source_path),
                before_value_access=gate,
                signer=lambda value: value,
            )
            _path_after, input_after = _validate_official_file(source_root, spec)
            reference = reference_commits[(spec.scene_id, spec.asset)]
            if input_after != input_record or dict(commit) != reference:
                raise M3SourceAssetRepairError(
                    f"Live cache does not match the exact-MD5 reference alignment for "
                    f"{spec.scene_id}/{spec.asset}."
                )
            content_commits.append(dict(commit))

    _assert_live_binding(settings, inventory, authorization)
    payload = _completion_payload(
        authorization,
        source_mode=mode,
        content_commits=content_commits,
    )
    _write_exclusive(payload, requested_completion)
    return authenticate_source_asset_repair_completion(
        root,
        requested_completion,
        authorization_path=authorization_path,
    )
