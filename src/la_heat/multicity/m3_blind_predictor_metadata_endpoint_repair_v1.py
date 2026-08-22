"""Append-only endpoint-name repair for blind predictor metadata v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_metadata_v1 as parent
from la_heat.multicity import source_footprints
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-metadata-endpoint-repair-v1"
STAC_API: Final = "https://planetarycomputer.microsoft.com/api/stac/v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_METADATA_ENDPOINT_REPAIR_V1_AUTHORIZATION.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_metadata_endpoint_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_metadata_endpoint_repair_v1.py",
)


class M3BlindPredictorMetadataEndpointRepairError(RuntimeError):
    """Raised when the append-only repair identity changes."""


def _record(root: Path, value: str | Path) -> dict[str, Any]:
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindPredictorMetadataEndpointRepairError("Repair code path changed.")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorMetadataEndpointRepairError("Repair commit changed.")
    return payload


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    original = parent.authenticate_runtime_authorization(root)
    code = [_record(root, path) for path in CODE_PATHS]
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_metadata_endpoint_repair_authorized",
            "parent_runtime_authorization_commit_sha256": original["commit_sha256"],
            "incident": {
                "error_type": "AttributeError",
                "failed_before_first_network_request": True,
                "predictor_or_target_values_read": False,
                "deterministic_key_table_may_exist": True,
            },
            "repair": {
                "operation": "inject_missing_source_footprints_endpoint_constant_only",
                "name": "PLANETARY_COMPUTER_STAC_API",
                "value": STAC_API,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "permissions": {
                "resume_original_metadata_runner": True,
                "change_city_date_key_or_source_contract": False,
                "read_predictor_landsat_qa_or_target_values": False,
            },
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if _read(path) != expected:
            raise M3BlindPredictorMetadataEndpointRepairError("Repair auth drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(root / AUTHORIZATION_PATH)
    expected = build_authorization(root)
    if observed != expected:
        raise M3BlindPredictorMetadataEndpointRepairError("Repair auth drifted.")
    return observed


def run_repaired_metadata(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    setattr(source_footprints, "PLANETARY_COMPUTER_STAC_API", STAC_API)
    return parent.run_metadata_bootstrap(root)
