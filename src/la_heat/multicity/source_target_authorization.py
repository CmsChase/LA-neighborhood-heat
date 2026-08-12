"""Issue or authenticate the append-only Los Angeles source-target permit."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from la_heat.config import load_config
from la_heat.multicity.evaluation_protocol_lock import (
    LOCK_PATH as PROTOCOL_LOCK_PATH,
)
from la_heat.multicity.evaluation_protocol_lock import (
    authenticate_protocol_model_lock,
)
from la_heat.multicity.target_authorization import (
    AUTHORIZED_STATE,
    TargetAuthorizationError,
    authenticate_target_execution_authorization,
)
from la_heat.multicity.target_processor import multicity_target_config_sha256
from la_heat.multicity.target_transaction import (
    MANIFEST_PATH as TARGET_PLAN_PATH,
)
from la_heat.multicity.target_transaction import (
    SOURCE_CITY_ID,
    SOURCE_LANE,
)
from la_heat.provenance import canonical_sha256, sha256_file

AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/targets/SOURCE_TARGET_AUTHORIZATION.json"
)
TARGET_CONFIG_PATH: Final = Path("configs/research.toml")
VALUES_OPENED_PATH: Final = Path(
    "data/interim/multicity/targets/values_opened/"
    "los_angeles_2020_2024_source/VALUES_OPENED.json"
)
AUTHORIZED_YEARS: Final = (2020, 2021, 2022, 2023, 2024)


class SourceTargetAuthorizationError(RuntimeError):
    """Raised when the narrow source-target permit cannot be issued or verified."""


def _read_committed_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceTargetAuthorizationError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict):
        raise SourceTargetAuthorizationError(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise SourceTargetAuthorizationError(f"{label} commit is invalid.")
    return payload


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise SourceTargetAuthorizationError(f"{label} must stay inside the project.")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _input_record(root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "commit_sha256": payload["commit_sha256"],
    }


def build_source_target_authorization(
    project_root: str | Path,
    *,
    protocol_lock_path: str | Path = PROTOCOL_LOCK_PATH,
    target_plan_path: str | Path = TARGET_PLAN_PATH,
    target_config_path: str | Path = TARGET_CONFIG_PATH,
    values_opened_path: str | Path = VALUES_OPENED_PATH,
) -> dict[str, Any]:
    """Build the deterministic LA-only permit without opening target values."""

    root = Path(project_root).resolve()
    lock_path = _inside(root, protocol_lock_path, label="Protocol lock path")
    plan_path = _inside(root, target_plan_path, label="Target plan path")
    config_path = _inside(root, target_config_path, label="Target config path")
    marker_path = _inside(root, values_opened_path, label="VALUES_OPENED path")

    protocol = authenticate_protocol_model_lock(root, lock_path)
    if protocol.get("next_safe_stage") != "explicitly_authorize_la_2020_2024_source_target_lane":
        raise SourceTargetAuthorizationError("Protocol lock does not authorize this transition.")
    if protocol.get("permissions", {}).get("source_target_build_authorized") is not False:
        raise SourceTargetAuthorizationError("Protocol lock source permission is not sealed.")
    if any(
        protocol.get("permissions", {}).get(key) is not False
        for key in (
            "external_target_build_authorized",
            "model_fit_authorized",
            "model_score_authorized",
            "external_targets_unlocked",
            "external_target_values_read",
            "external_prediction_commit_exists",
        )
    ):
        raise SourceTargetAuthorizationError("Protocol lock opened a forbidden permission.")

    plan = _read_committed_json(plan_path, label="Target build plan")
    if plan.get("state") != "prepared_target_blind_builder_not_authorized":
        raise SourceTargetAuthorizationError("Target build plan is not sealed and prepared.")
    lane = plan.get("cohort_lanes", {}).get(SOURCE_LANE, {})
    if (
        lane.get("city_ids") != [SOURCE_CITY_ID]
        or lane.get("years") != list(AUTHORIZED_YEARS)
        or lane.get("overpasses") != 90
        or lane.get("scenes") != 177
        or lane.get("keys") != 98_640
    ):
        raise SourceTargetAuthorizationError("LA source cohort changed.")
    if any(value is not False for value in plan.get("authorization", {}).values()):
        raise SourceTargetAuthorizationError("Target build plan is already authorized.")
    if any(value is not False for value in plan.get("access_contract", {}).values()):
        raise SourceTargetAuthorizationError("Target build plan records prior value access.")

    target_config_sha256 = multicity_target_config_sha256(load_config(config_path))
    protocol_target_config = (
        protocol.get("code_identity", {})
        .get("files", {})
        .get(TARGET_CONFIG_PATH.as_posix(), {})
    )
    if (
        _relative(root, config_path) != TARGET_CONFIG_PATH.as_posix()
        or protocol_target_config.get("sha256") != sha256_file(config_path)
    ):
        raise SourceTargetAuthorizationError("Research target configuration drifted from the lock.")

    request: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "multicity-source-target-authorization-v1",
        "state": AUTHORIZED_STATE,
        "lane": SOURCE_LANE,
        "city_ids": [SOURCE_CITY_ID],
        "years": list(AUTHORIZED_YEARS),
        "purpose": "los_angeles_training_and_calibration_labels",
        "expected_overpass_count": 90,
        "expected_scene_count": 177,
        "expected_target_key_count": 98_640,
        "protocol_lock": _input_record(root, lock_path, protocol),
        "plan_commit_sha256": plan["commit_sha256"],
        "target_plan": _input_record(root, plan_path, plan),
        "target_config_path": _relative(root, config_path),
        "target_config_file_sha256": sha256_file(config_path),
        "target_config_sha256": target_config_sha256,
        "asset_href_hydration_authorized": True,
        "target_values_open_authorized": True,
        "single_global_claim": False,
        "external_prediction_commit_sha256": None,
        "values_opened_marker": _relative(root, marker_path),
        "permissions": {
            "source_target_build_authorized": True,
            "external_target_build_authorized": False,
            "model_fit_authorized": False,
            "model_score_authorized": False,
            "external_targets_unlocked": False,
        },
        "access_audit": {
            "landsat_asset_hrefs_read_by_authorization": False,
            "landsat_thermal_or_target_qa_values_read_by_authorization": False,
            "target_tables_read_by_authorization": False,
            "model_fit_or_prediction_performed_by_authorization": False,
            "values_opened_marker_created_by_authorization": False,
        },
        "next_safe_stage": "run_resumable_la_2020_2024_source_target_build",
    }
    request["claim_id"] = canonical_sha256(request)
    request["commit_sha256"] = canonical_sha256(request)
    return request


def _write_exclusive(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise SourceTargetAuthorizationError(
            f"Append-only authorization already exists: {path}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def authenticate_source_target_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Authenticate the source permit and its bound identities without opening values."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization path")
    observed = _read_committed_json(path, label="Source target authorization")
    expected = build_source_target_authorization(
        root,
        protocol_lock_path=observed.get("protocol_lock", {}).get("path", ""),
        target_plan_path=observed.get("target_plan", {}).get("path", ""),
        target_config_path=observed.get("target_config_path", ""),
        values_opened_path=observed.get("values_opened_marker", ""),
    )
    if observed != expected:
        raise SourceTargetAuthorizationError("Source target authorization no longer reproduces.")
    try:
        authenticate_target_execution_authorization(
            root,
            path,
            expected_lane=SOURCE_LANE,
            expected_plan_commit_sha256=str(observed["plan_commit_sha256"]),
        )
    except TargetAuthorizationError as error:
        raise SourceTargetAuthorizationError(
            "Source authorization is incompatible with the target engine."
        ) from error
    return observed


def create_source_target_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Issue the append-only LA permit and immediately authenticate it."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization path")
    payload = build_source_target_authorization(root)
    _write_exclusive(payload, path)
    return authenticate_source_target_authorization(root, path)
