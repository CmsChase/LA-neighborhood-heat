"""Append-only repair for city-local static/Sentinel manifest path semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_daymet_compilation_v1 as parent
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-daymet-path-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_DAYMET_PATH_REPAIR_V1_AUTHORIZATION.json"
)
EXPECTED_PARENT_AUTHORIZATION_COMMIT: Final = (
    "378bfea1bd3bca1f9c9cf24ef23f243f01b1eb38c5b3630353b6f1f34e660484"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_daymet_path_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_path_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_compilation_v1.py",
)


class M3BlindDaymetPathRepairError(RuntimeError):
    """Raised when the narrow path-semantics repair contract drifts."""


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindDaymetPathRepairError(f"Missing repair input: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_auth = parent._read_committed(root / parent.AUTHORIZATION_PATH)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_AUTHORIZATION_COMMIT:
        raise M3BlindDaymetPathRepairError("Parent compilation authorization changed.")
    code = [_record(root, path) for path in CODE_PATHS]
    payload = {
        key: value
        for key, value in parent_auth.items()
        if key
        not in {
            "algorithm_version",
            "state",
            "code_identity",
            "run_id",
            "claim_id",
            "commit_sha256",
        }
    }
    payload.update(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_daymet_city_local_path_repair_authorized",
            "parent_compilation_authorization": {
                **_record(root, parent.AUTHORIZATION_PATH),
                "commit_sha256": parent_auth["commit_sha256"],
            },
            "incident": {
                "error_type": "M3BlindDaymetCompilationError",
                "message": "Authenticated input file changed.",
                "failed_city": "seattle_wa",
                "completed_city_outputs": 0,
                "cause": "upstream parquet records omit path and are city-directory-relative",
            },
            "repair_contract": {
                "resolve_only_static_and_sentinel_output_names_from_locked_city_directories": True,
                "retain_original_bytes_and_sha256_validation": True,
                "change_scientific_values_schema_keys_or_features": False,
                "network_or_href_reads": False,
                "read_landsat_qa_or_target_values": False,
                "fit_predict_score_or_evaluate": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "resume_original_offline_compilation_through_repair_runner",
        }
    )
    payload["run_id"] = f"m3-blind-predictors-46-path-repair-{canonical_sha256(payload)[:16]}"
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if parent._read_committed(path) != expected:
            raise M3BlindDaymetPathRepairError("Append-only repair authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = parent._read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindDaymetPathRepairError("Path repair authorization drifted.")
    return observed


def _with_repair_auth(project_root: str | Path, operation: str) -> dict[str, Any]:
    original = parent.authenticate_authorization
    parent.authenticate_authorization = authenticate_authorization
    try:
        if operation == "run":
            return parent.run(project_root)
        return parent.authenticate_completion(project_root)
    finally:
        parent.authenticate_authorization = original


def run(project_root: str | Path) -> dict[str, Any]:
    authenticate_authorization(project_root)
    return _with_repair_auth(project_root, "run")


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    authenticate_authorization(project_root)
    return _with_repair_auth(project_root, "check")
