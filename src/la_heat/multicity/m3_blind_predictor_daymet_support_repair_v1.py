"""Authorize preserved all-feature Daymet gaps for frozen-fold imputation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_daymet_compilation_v1 as parent
from la_heat.multicity import m3_blind_predictor_daymet_path_repair_v1 as path_repair
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-daymet-support-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_DAYMET_SUPPORT_REPAIR_V1_AUTHORIZATION.json"
)
EXPECTED_PATH_REPAIR_COMMIT: Final = (
    "aed45c08882a122eda3fb55d724ea6092a6d84b9fb04d426520a7c3e1b5ed3da"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_daymet_support_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_support_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_compilation_v1.py",
)


class M3BlindDaymetSupportRepairError(RuntimeError):
    """Raised when the Daymet support repair contract drifts."""


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindDaymetSupportRepairError(f"Missing support repair input: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    previous = parent._read_committed(root / path_repair.AUTHORIZATION_PATH)
    if previous.get("commit_sha256") != EXPECTED_PATH_REPAIR_COMMIT:
        raise M3BlindDaymetSupportRepairError("Path repair authorization changed.")
    code = [_record(root, path) for path in CODE_PATHS]
    excluded = {
        "algorithm_version",
        "state",
        "code_identity",
        "run_id",
        "claim_id",
        "commit_sha256",
    }
    payload = {key: value for key, value in previous.items() if key not in excluded}
    payload.update(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_daymet_support_repair_authorized",
            "parent_path_repair_authorization": {
                **_record(root, path_repair.AUTHORIZATION_PATH),
                "commit_sha256": previous["commit_sha256"],
            },
            "support_incident": {
                "failed_city": "seattle_wa",
                "row_count": 9_558,
                "daymet_missing_row_count": 108,
                "daymet_missing_cell_count": 2_268,
                "daymet_feature_count": 21,
                "inferred_missing_tract_count": 2,
                "other_semantic_gates_passed": True,
                "completed_city_outputs": 0,
            },
            "repair_contract": {
                "allow_only_rowwise_all_21_or_zero_daymet_missingness": True,
                "forbid_infinite_daymet_values": True,
                "require_static_and_calendar_finite": True,
                "preserve_missing_values_without_fill_or_row_drop": True,
                "defer_to_frozen_source_fit_fold_median_imputer_with_indicator": True,
                "retain_row_key_column_and_sentinel_semantic_gates": True,
                "change_feature_values_keys_dates_or_model": False,
                "network_or_href_reads": False,
                "read_landsat_qa_or_target_values": False,
                "fit_predict_score_or_evaluate": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "resume_offline_compilation_through_support_repair_runner",
        }
    )
    payload["run_id"] = f"m3-blind-predictors-46-support-repair-{canonical_sha256(payload)[:16]}"
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if parent._read_committed(path) != expected:
            raise M3BlindDaymetSupportRepairError("Append-only support authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = parent._read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindDaymetSupportRepairError("Support repair authorization drifted.")
    return observed


def _operate(project_root: str | Path, *, run_now: bool) -> dict[str, Any]:
    original = parent.authenticate_authorization
    parent.authenticate_authorization = authenticate_authorization
    try:
        return parent.run(project_root) if run_now else parent.authenticate_completion(project_root)
    finally:
        parent.authenticate_authorization = original


def run(project_root: str | Path) -> dict[str, Any]:
    authenticate_authorization(project_root)
    return _operate(project_root, run_now=True)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    authenticate_authorization(project_root)
    return _operate(project_root, run_now=False)
