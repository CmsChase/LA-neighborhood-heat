"""Code-bound launch permit for the full four-city offline assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_canary_counter_repair_v1 as canary
from la_heat.multicity import m3_blind_predictor_static_tile_scope_repair_v1 as runner
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-full-offline-launch-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_FULL_OFFLINE_LAUNCH_V1_AUTHORIZATION.json"
)
EXPECTED_CANARY_COMMIT: Final = (
    "f57d39827b9abc7d36126aeec4285fce6996f017cbf3f5786017b32b3be9694e"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_full_offline_launch_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_full_offline_launch_v1.py",
)


class M3BlindFullOfflineLaunchError(RuntimeError):
    """Raised when the full offline launch contract drifts."""


def _read_committed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindFullOfflineLaunchError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindFullOfflineLaunchError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    corrected = _read_committed(root / canary.COMPLETION_PATH)
    if corrected.get("commit_sha256") != EXPECTED_CANARY_COMMIT:
        raise M3BlindFullOfflineLaunchError("Corrected canary completion changed.")
    runner_auth = runner.authenticate_authorization(root)
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_full_four_city_offline_assembly_authorized",
        "canary_completion": {
            **_record(root, canary.COMPLETION_PATH),
            "commit_sha256": corrected["commit_sha256"],
        },
        "runner_authorization": {
            **_record(root, runner.AUTHORIZATION_PATH),
            "commit_sha256": runner_auth["commit_sha256"],
        },
        "blind_city_ids": ["seattle_wa", "denver_co", "atlanta_ga", "miami_fl"],
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "permissions": {
            "resume_full_four_city_sentinel_static_offline_assembly": True,
            "reuse_authenticated_canary_outputs": True,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "runtime_contract": {
            "same_runtime_and_output_roots": True,
            "durable_city_and_gshhg_chunk_boundaries": True,
            "network_and_href_read_count": 0,
            "blind_targets_sealed": True,
        },
        "authorization_audit": {
            "predictor_value_files_opened_or_statted": 0,
            "network_or_href_reads": 0,
        },
        "next_safe_stage": "run_full_four_city_offline_assembly",
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
            raise M3BlindFullOfflineLaunchError("Append-only launch authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindFullOfflineLaunchError("Full offline launch authorization drifted.")
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    result = runner.run(root, canary=False)
    authenticate_authorization(root)
    return result
