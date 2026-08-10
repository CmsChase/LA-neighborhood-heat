"""Claim-bound authorization gate for future multicity target value access."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.target_transaction import (
    EXTERNAL_LANE,
    SOURCE_CITY_ID,
    SOURCE_LANE,
)
from la_heat.provenance import canonical_sha256, sha256_file

AUTHORIZED_STATE: Final = "target_execution_authorized"
VALUES_OPENED_STATE: Final = "target_values_opened"


class TargetAuthorizationError(RuntimeError):
    """Raised before any href or target value can be accessed."""


@dataclass(frozen=True, slots=True)
class TargetExecutionAuthorization:
    path: Path
    file_sha256: str
    commit_sha256: str
    lane: str
    city_ids: tuple[str, ...]
    claim_id: str
    plan_commit_sha256: str
    target_config_sha256: str
    values_opened_marker: Path
    external_prediction_commit_sha256: str | None


def _committed(payload: dict[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and canonical_sha256(unsigned) == recorded


def _sha256(value: object, *, label: str) -> str:
    text = str(value)
    if len(text) != 64:
        raise TargetAuthorizationError(f"{label} must be one SHA-256 value.")
    try:
        int(text, 16)
    except ValueError as error:
        raise TargetAuthorizationError(f"{label} must be hexadecimal.") from error
    return text.lower()


def _inside(root: Path, value: object, *, label: str) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise TargetAuthorizationError(f"{label} must stay inside the project.")
    return resolved


def authenticate_target_execution_authorization(
    project_root: str | Path,
    authorization_path: str | Path,
    *,
    expected_lane: str,
    expected_plan_commit_sha256: str,
) -> TargetExecutionAuthorization:
    """Authenticate a later protocol-issued permit without opening target data."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetAuthorizationError("Target execution authorization is unavailable.") from error
    if not isinstance(payload, dict) or not _committed(payload):
        raise TargetAuthorizationError("Target execution authorization is not committed.")
    if payload.get("state") != AUTHORIZED_STATE or payload.get("lane") != expected_lane:
        raise TargetAuthorizationError("Target execution lane is not authorized.")
    if payload.get("asset_href_hydration_authorized") is not True:
        raise TargetAuthorizationError("Landsat asset href hydration is not authorized.")
    if payload.get("target_values_open_authorized") is not True:
        raise TargetAuthorizationError("Target/QA value access is not authorized.")
    claim_id = payload.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.strip() or len(claim_id) > 256:
        raise TargetAuthorizationError("Authorization requires one bounded claim ID.")
    plan_commit = _sha256(payload.get("plan_commit_sha256"), label="Plan commit")
    if plan_commit != expected_plan_commit_sha256:
        raise TargetAuthorizationError("Authorization targets a different build plan.")
    target_config = _sha256(
        payload.get("target_config_sha256"), label="Target configuration"
    )
    city_ids = tuple(payload.get("city_ids", ()))
    external_prediction: str | None = None
    if expected_lane == SOURCE_LANE:
        if city_ids != (SOURCE_CITY_ID,):
            raise TargetAuthorizationError("Source authorization must contain only LA.")
        if payload.get("external_prediction_commit_sha256") is not None:
            raise TargetAuthorizationError("Source authorization cannot bind external predictions.")
    elif expected_lane == EXTERNAL_LANE:
        if city_ids != tuple(EXTERNAL_CITY_IDS):
            raise TargetAuthorizationError(
                "External authorization must contain the complete three-city cohort."
            )
        if payload.get("single_global_claim") is not True:
            raise TargetAuthorizationError("External targets require one global claim.")
        external_prediction = _sha256(
            payload.get("external_prediction_commit_sha256"),
            label="External prediction commit",
        )
    else:
        raise TargetAuthorizationError(f"Unknown target lane: {expected_lane}")
    marker = _inside(
        root,
        payload.get("values_opened_marker"),
        label="VALUES_OPENED marker",
    )
    return TargetExecutionAuthorization(
        path=path,
        file_sha256=sha256_file(path),
        commit_sha256=str(payload["commit_sha256"]),
        lane=expected_lane,
        city_ids=city_ids,
        claim_id=claim_id,
        plan_commit_sha256=plan_commit,
        target_config_sha256=target_config,
        values_opened_marker=marker,
        external_prediction_commit_sha256=external_prediction,
    )


def _marker_payload(authorization: TargetExecutionAuthorization) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "state": VALUES_OPENED_STATE,
        "lane": authorization.lane,
        "city_ids": list(authorization.city_ids),
        "claim_id": authorization.claim_id,
        "plan_commit_sha256": authorization.plan_commit_sha256,
        "target_config_sha256": authorization.target_config_sha256,
        "authorization_commit_sha256": authorization.commit_sha256,
        "authorization_file_sha256": authorization.file_sha256,
        "external_prediction_commit_sha256": (
            authorization.external_prediction_commit_sha256
        ),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def open_or_authenticate_values_marker(
    authorization: TargetExecutionAuthorization,
) -> dict[str, Any]:
    """Create the one-time marker, or prove this is a same-claim resume."""

    path = authorization.values_opened_marker
    expected = _marker_payload(authorization)
    if path.exists():
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TargetAuthorizationError("VALUES_OPENED marker is unreadable.") from error
        if observed != expected:
            raise TargetAuthorizationError("VALUES_OPENED belongs to another claim or lock.")
        return observed
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(expected, indent=2, ensure_ascii=False).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return open_or_authenticate_values_marker(authorization)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return expected


@dataclass(slots=True)
class ValuesAccessGate:
    """Open the marker exactly before the first href, cache, or raster access."""

    authorization: TargetExecutionAuthorization
    opened: bool = False

    def before_first_value_access(self) -> None:
        if self.opened:
            return
        open_or_authenticate_values_marker(self.authorization)
        self.opened = True
