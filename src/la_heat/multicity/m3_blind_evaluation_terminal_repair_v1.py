"""Append-only terminal certification and four-city summary correction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_evaluation_v1 as evaluation
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-evaluation-terminal-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_EVALUATION_TERMINAL_REPAIR_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_EVALUATION_TERMINAL_COMPLETE.json"
)
PARENT_COMPLETION_COMMIT: Final = "6574750d633b580afd9fedb4b06cf99cd78c414956450a92bb3ea9e99113559f"
PUBLISH_REPAIR_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/"
    "M3_BLIND_EVALUATION_PUBLISH_REPAIR_COMPLETE.json"
)
PUBLISH_REPAIR_COMPLETION_COMMIT: Final = (
    "30930187801558964ef52e665a5aab3c1c5876bd965d1e0f8946aeb2d789cad0"
)
SUMMARY_V2_PATH: Final = evaluation.OUTPUT_ROOT / "summary_v2.json"
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_evaluation_terminal_repair_v1.py",
    "scripts/authorize_m3_blind_evaluation_terminal_repair_v1.py",
    "scripts/run_m3_blind_evaluation_terminal_repair_v1.py",
)
SORT_KEYS: Final = {
    "scored_rows.parquet": ["city_id", "target_date", "tract_geoid"],
    "date_metrics.parquet": ["city_id", "target_date"],
    "city_metrics.parquet": ["city_id"],
    "risk_coverage.parquet": ["cohort_id", "coverage_fraction"],
}


class M3BlindEvaluationTerminalRepairError(RuntimeError):
    """Raised when terminal evaluation evidence changes."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindEvaluationTerminalRepairError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if "commit_sha256" in payload and canonical_sha256(body) != payload["commit_sha256"]:
        raise M3BlindEvaluationTerminalRepairError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _verify_parent(root: Path) -> dict[str, Any]:
    parent = _read(helpers._inside(root, evaluation.COMPLETION_PATH))
    publish = _read(helpers._inside(root, PUBLISH_REPAIR_COMPLETION_PATH))
    if (
        parent.get("commit_sha256") != PARENT_COMPLETION_COMMIT
        or publish.get("commit_sha256") != PUBLISH_REPAIR_COMPLETION_COMMIT
        or publish.get("parent_evaluation_completion_commit_sha256") != PARENT_COMPLETION_COMMIT
    ):
        raise M3BlindEvaluationTerminalRepairError("Terminal completion anchors changed.")
    for name, record in parent["outputs"].items():
        path = helpers._inside(root, record["path"])
        if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
            raise M3BlindEvaluationTerminalRepairError(f"Output bytes changed: {name}")
        if name in SORT_KEYS:
            frame = pd.read_parquet(path)
            if (
                len(frame) != record["rows"]
                or canonical_frame_sha256(frame, sort_by=SORT_KEYS[name])
                != record["semantic_sha256"]
            ):
                raise M3BlindEvaluationTerminalRepairError(f"Output semantics changed: {name}")
    return parent


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent = _verify_parent(root)
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_terminal_repair_authorized",
            "parent_evaluation_completion_commit_sha256": PARENT_COMPLETION_COMMIT,
            "publish_repair_completion_commit_sha256": PUBLISH_REPAIR_COMPLETION_COMMIT,
            "parent_conclusion": parent["conclusion"],
            "correction": {
                "remove_legacy_external_models_field": True,
                "remove_incorrect_three_city_compatibility_field": True,
                "retain_all_numeric_metrics_unchanged": True,
                "write_path": SUMMARY_V2_PATH.as_posix(),
            },
            "code": code,
            "permissions": {
                "authenticate_existing_evaluation_outputs": True,
                "write_corrected_summary_and_terminal_completion": True,
                "recompute_or_change_any_metric_bootstrap_gate_or_conclusion": False,
                "overwrite_or_delete_existing_output": False,
            },
            "next_safe_stage": "write_corrected_summary_and_terminal_certification",
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_authorization(root)
    helpers._write_json_exclusive(payload, helpers._inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(helpers._inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindEvaluationTerminalRepairError("Terminal authorization drifted.")
    return observed


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    destination = helpers._inside(root, COMPLETION_PATH)
    if destination.exists():
        return authenticate_completion(root)
    parent = _verify_parent(root)
    summary_record = parent["outputs"]["summary.json"]
    summary = _read(helpers._inside(root, summary_record["path"]))
    corrected = dict(summary)
    corrected.pop("external_models_refit_or_recalibrated", None)
    corrected.pop("three_city_cohort_evaluated_as_one_claim", None)
    corrected["models_refit_recalibrated_or_retuned"] = False
    corrected["four_city_cohort_evaluated_as_one_claim"] = True
    corrected["supersedes_summary_sha256"] = summary_record["sha256"]
    summary_v2 = helpers._inside(root, SUMMARY_V2_PATH)
    helpers._write_json_exclusive(corrected, summary_v2)
    corrected_record = helpers._record(root, summary_v2)
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_terminal_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_evaluation_completion_commit_sha256": PARENT_COMPLETION_COMMIT,
            "publish_repair_completion_commit_sha256": PUBLISH_REPAIR_COMPLETION_COMMIT,
            "corrected_summary": corrected_record,
            "conclusion": parent["conclusion"],
            "audit": {
                "all_parent_output_bytes_and_semantics_authenticated": True,
                "numeric_metrics_bootstrap_gates_and_conclusion_changed": False,
                "network_or_href_reads": 0,
                "fit_retrain_recalibrate_retune_or_select": False,
            },
            "next_safe_stage": "commit_and_publish_terminal_blind_result",
        }
    )
    helpers._write_json_exclusive(completion, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    parent = _verify_parent(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    record = payload.get("corrected_summary", {})
    path = helpers._inside(root, record.get("path", ""))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("parent_evaluation_completion_commit_sha256") != parent["commit_sha256"]
        or path != helpers._inside(root, SUMMARY_V2_PATH)
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3BlindEvaluationTerminalRepairError("Terminal completion changed.")
    corrected = _read(path)
    if (
        "three_city_cohort_evaluated_as_one_claim" in corrected
        or "external_models_refit_or_recalibrated" in corrected
        or corrected.get("four_city_cohort_evaluated_as_one_claim") is not True
    ):
        raise M3BlindEvaluationTerminalRepairError("Corrected summary is invalid.")
    return payload
