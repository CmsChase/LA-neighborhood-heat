"""Ephemeral bearer adapter for the authorized blind-city Daymet acquisition."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from la_heat.multicity import m3_blind_predictor_daymet_acquisition_v1 as parent
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-daymet-bearer-runtime-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_DAYMET_BEARER_RUNTIME_V1_AUTHORIZATION.json"
)
EXPECTED_PARENT_COMMIT: Final = (
    "bbf7c4e1b24639d4ad4ff857c04492d296b51486cfbe0651142ac073c166c6ae"
)
TOKEN_ENV: Final = "M3_EARTHDATA_EPHEMERAL_TOKEN"
ALLOWED_HOST: Final = "opendap.earthdata.nasa.gov"
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_daymet_bearer_runtime_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_bearer_runtime_v1.py",
)
JWT_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class M3BlindDaymetBearerRuntimeError(RuntimeError):
    """Raised when ephemeral credential use escapes the authorized contract."""


def _read_committed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindDaymetBearerRuntimeError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindDaymetBearerRuntimeError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build a code-bound permit without reading the credential."""

    root = Path(project_root).resolve()
    parent_auth = parent.authenticate_authorization(root)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindDaymetBearerRuntimeError("Parent Daymet authorization changed.")
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_daymet_ephemeral_bearer_runtime_authorized",
        "parent_authorization": {
            **_record(root, parent.AUTHORIZATION_PATH),
            "commit_sha256": parent_auth["commit_sha256"],
        },
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "credential_contract": {
            "environment_variable": TOKEN_ENV,
            "read_only_at_request_time": True,
            "persisted_to_file_log_manifest_or_output": False,
            "printed_or_returned": False,
            "removed_from_process_environment_after_run": True,
        },
        "network_contract": {
            "scheme": "https",
            "host": ALLOWED_HOST,
            "authorization_header_only": True,
            "redirects_allowed": False,
        },
        "permissions": {
            "resume_parent_authorized_24_daymet_tasks": True,
            "read_sentinel_static_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "authorization_audit": {
            "credential_read": False,
            "network_requests": 0,
            "credential_persisted_or_printed": False,
        },
        "next_safe_stage": "run_daymet_acquisition_with_ephemeral_environment_token",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if _read_committed(path) != expected:
            raise M3BlindDaymetBearerRuntimeError("Append-only authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindDaymetBearerRuntimeError("Bearer runtime authorization drifted.")
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    token = os.environ.get(TOKEN_ENV, "").strip().replace("\\_", "_").rstrip(",")
    if not JWT_PATTERN.fullmatch(token):
        raise M3BlindDaymetBearerRuntimeError("Missing or malformed ephemeral token.")
    original_get = parent.requests.get

    def authorized_get(url: str, *args: Any, **kwargs: Any) -> Any:
        authenticate_authorization(root)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            raise M3BlindDaymetBearerRuntimeError("Daymet request escaped allowlist.")
        headers = dict(kwargs.pop("headers", {}))
        if any(key.lower() == "authorization" for key in headers):
            raise M3BlindDaymetBearerRuntimeError("Unexpected authorization header.")
        headers["Authorization"] = f"Bearer {token}"
        kwargs["headers"] = headers
        kwargs["allow_redirects"] = False
        response = original_get(url, *args, **kwargs)
        if response.is_redirect or response.is_permanent_redirect:
            response.close()
            raise M3BlindDaymetBearerRuntimeError("Redirect refused by credential contract.")
        return response

    parent.requests.get = authorized_get
    try:
        result = parent.run_acquisition(root)
    finally:
        parent.requests.get = original_get
        os.environ.pop(TOKEN_ENV, None)
        token = ""
    return result
