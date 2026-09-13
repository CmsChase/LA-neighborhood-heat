"""One-time frozen four-city M3 blind evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import external_evaluation as engine
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_prediction_v2 as predictions
from la_heat.multicity import m3_blind_spatial_blocks_v1 as blocks
from la_heat.multicity import m3_blind_target_durable_resume_v1 as durable
from la_heat.multicity import m3_blind_target_v1 as targets
from la_heat.multicity.m3_development import PREDICTION_COLUMNS
from la_heat.multicity.m3_development_protocol_lock import (
    LOCK_PATH,
    authenticate_m3_development_protocol_lock,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-evaluation-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_EVALUATION_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/M3_BLIND_EVALUATION_COMPLETE.json"
)
OUTPUT_ROOT: Final = Path("data/processed/multicity/m3_blind_evaluation_v1")
PREDICTION_COMMIT: Final = "295ccca0ea0239cf6eb5633b736a7565abbac3cc47992bae6bcbc9ba4d6f74ac"
TARGET_COMMIT: Final = "55839671f2b28e5c725c5d9601e7d0e2d6ae8a916d7b9880c94e65b2fd99e901"
DURABLE_COMMIT: Final = "ac313aa87ef68b4d2a7ec8803cf904f5881ed8017b552199342c63117983d31b"
BLOCKS_COMMIT: Final = "7e9e3025161a5d5688a439a491d0283aad20363441023a3af5abf6f79bb9fdfa"
PROTOCOL_COMMIT: Final = "dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8"
BOOTSTRAP_ITERATIONS: Final = 10_000
BOOTSTRAP_SEED: Final = 20_260_816
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_evaluation_v1.py",
    "src/la_heat/multicity/external_evaluation.py",
    "scripts/authorize_m3_blind_evaluation_v1.py",
    "scripts/run_m3_blind_evaluation_v1.py",
)
ENGINE_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "target_date",
    "b1_prediction_c",
    "m2_prediction_c",
    "m2_lower_c",
    "m2_upper_c",
    "m2_interval_width_c",
    "m2_abstain",
    "m2_accepted",
)


class M3BlindEvaluationError(RuntimeError):
    """Raised when the frozen blind evaluation cannot authenticate."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindEvaluationError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindEvaluationError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "commit_sha256": canonical_sha256(payload)}


def _manifest_record(root: Path, path: Path, commit: str) -> dict[str, Any]:
    absolute = helpers._inside(root, path)
    payload = _read(absolute)
    if payload.get("commit_sha256") != commit:
        raise M3BlindEvaluationError(f"Completion anchor changed: {path}")
    return {
        "path": path.as_posix(),
        "bytes": absolute.stat().st_size,
        "sha256": sha256_file(absolute),
        "commit_sha256": commit,
    }


def _evaluation_contract(protocol: dict[str, Any]) -> dict[str, Any]:
    evaluation = protocol["development_contract"]["evaluation"]
    expected = {
        "primary_comparison": "M3_vs_B1",
        "primary_metric": (
            "one_minus_equal_city_equal_date_mae_m3_divided_by_equal_city_equal_date_mae_b1"
        ),
        "minimum_relative_mae_improvement": 0.1,
        "require_crossed_bootstrap_ci_lower_above_zero": True,
        "require_no_blind_test_city_point_degradation": True,
        "minimum_total_usable_city_dates": 40,
        "minimum_usable_dates_per_blind_test_city": 8,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "bootstrap_method": "city_stratified_crossed_complete_date_x_5km_spatial_block",
        "bootstrap_seed": BOOTSTRAP_SEED,
        "confidence_level": 0.95,
    }
    if any(evaluation.get(key) != value for key, value in expected.items()):
        raise M3BlindEvaluationError("Frozen evaluation contract changed.")
    return evaluation


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build authorization from committed metadata without computing a metric."""

    root = Path(project_root).resolve()
    protocol = authenticate_m3_development_protocol_lock(root)
    if protocol.get("commit_sha256") != PROTOCOL_COMMIT:
        raise M3BlindEvaluationError("Protocol commit changed.")
    evaluation = _evaluation_contract(protocol)
    manifests = {
        "prediction": _manifest_record(root, predictions.COMPLETION_PATH, PREDICTION_COMMIT),
        "target": _manifest_record(root, targets.COMPLETION_PATH, TARGET_COMMIT),
        "durable_resume": _manifest_record(root, durable.COMPLETION_PATH, DURABLE_COMMIT),
        "spatial_blocks": _manifest_record(root, blocks.COMPLETION_PATH, BLOCKS_COMMIT),
        "protocol": _manifest_record(root, LOCK_PATH, PROTOCOL_COMMIT),
    }
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_authorized",
            "claim": "one_indivisible_four_city_frozen_blind_evaluation",
            "city_ids": list(helpers.BLIND_CITY_IDS),
            "manifests": manifests,
            "prediction_columns": list(PREDICTION_COLUMNS),
            "evaluation_contract": evaluation,
            "code": code,
            "permissions": {
                "read_authenticated_prediction_target_and_spatial_block_values": True,
                "compute_frozen_primary_secondary_reliability_and_bootstrap_metrics": True,
                "write_append_only_evaluation_outputs_and_completion": True,
                "fit_retrain_recalibrate_retune_or_select": False,
                "change_city_date_qa_support_gate_metric_seed_or_threshold": False,
                "network_or_href_reads": False,
            },
            "next_safe_stage": "run_one_time_frozen_four_city_evaluation",
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
        raise M3BlindEvaluationError("Evaluation authorization drifted.")
    return observed


def _inputs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prediction_completion = predictions.authenticate_completion(root)
    target_completion = targets.authenticate_completion(root)
    block_completion = blocks.authenticate_completion(root)
    prediction_frames = [
        pd.read_parquet(helpers._inside(root, item["path"]))
        for item in prediction_completion["city_outputs"]
    ]
    target_frames = [
        pd.read_parquet(helpers._inside(root, item["output"]["path"]))
        for item in target_completion["cities"]
    ]
    block_frame = pd.read_parquet(helpers._inside(root, block_completion["output"]["path"]))
    return (
        pd.concat(prediction_frames, ignore_index=True),
        pd.concat(target_frames, ignore_index=True),
        block_frame,
    )


def _engine_protocol(evaluation: dict[str, Any]) -> dict[str, Any]:
    reliability = evaluation["reliability_gates"]
    return {
        "evaluation_contract": {
            "primary_metric": "one_minus_external_equal_city_equal_date_mae_m2_divided_by_b1",
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_method": evaluation["bootstrap_method"],
            "confidence_level": evaluation["confidence_level"],
            "minimum_relative_mae_improvement": evaluation["minimum_relative_mae_improvement"],
            "require_ci_lower_above_zero": evaluation[
                "require_crossed_bootstrap_ci_lower_above_zero"
            ],
            "require_no_external_city_point_degradation": evaluation[
                "require_no_blind_test_city_point_degradation"
            ],
            "minimum_total_city_dates": evaluation["minimum_total_usable_city_dates"],
            "minimum_dates_per_external_city": evaluation[
                "minimum_usable_dates_per_blind_test_city"
            ],
            "overall_coverage_lower": reliability["overall_coverage_lower"],
            "overall_coverage_upper": reliability["overall_coverage_upper"],
            "per_city_coverage_lower": reliability["minimum_per_city_coverage"],
            "minimum_retention": reliability["minimum_per_city_retention"],
            "accepted_mae_improvement": reliability[
                "minimum_accepted_set_mae_improvement_vs_all_predictions"
            ],
            "hotspot_fraction": 0.2,
            "hotspot_tie_break": "score_desc_tract_geoid_asc",
            "secondary_metrics": list(engine.SECONDARY_METRICS),
        },
        "prediction_output_contract": {
            "prediction_columns": list(ENGINE_COLUMNS),
            "planned_figure_ids": list(engine.PLANNED_FIGURE_IDS),
            "all_reports_require_row_date_block_counts": True,
        },
    }


def _adapt_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(
        columns={
            "m3_prediction_c": "m2_prediction_c",
            "m3_lower_c": "m2_lower_c",
            "m3_upper_c": "m2_upper_c",
            "m3_interval_width_c": "m2_interval_width_c",
            "m3_abstain": "m2_abstain",
            "m3_accepted": "m2_accepted",
        }
    ).loc[:, ENGINE_COLUMNS]


def _rename_m3(value: Any) -> Any:
    if isinstance(value, dict):
        return {key.replace("m2", "m3"): _rename_m3(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rename_m3(item) for item in value]
    return value


def _legacy_summary(prediction: pd.DataFrame, scored: pd.DataFrame) -> dict[str, Any]:
    keys = ["city_id", "tract_geoid", "target_date"]
    legacy = prediction.loc[:, [*keys, "m2_legacy_prediction_c"]]
    joined = scored.loc[:, [*keys, "target_lst_c"]].merge(
        legacy, on=keys, how="left", validate="one_to_one"
    )
    joined["absolute_error_c"] = (joined["m2_legacy_prediction_c"] - joined["target_lst_c"]).abs()
    dates = (
        joined.groupby(["city_id", "target_date"], observed=True)["absolute_error_c"]
        .mean()
        .reset_index()
    )
    city = dates.groupby("city_id", observed=True)["absolute_error_c"].mean()
    return {
        "comparison": "M2_legacy_vs_B1_secondary_only",
        "equal_city_equal_date_mae_c": float(city.mean()),
        "per_city_equal_date_mae_c": {str(key): float(value) for key, value in city.items()},
        "cannot_rescue_failed_primary_gate": True,
    }


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    destination = helpers._inside(root, COMPLETION_PATH)
    if destination.exists():
        return authenticate_completion(root)
    prediction, target, block = _inputs(root)
    old = (engine.EXTERNAL_CITY_IDS, engine.BOOTSTRAP_SEED, engine.PREDICTION_COLUMNS)
    engine.EXTERNAL_CITY_IDS = helpers.BLIND_CITY_IDS
    engine.BOOTSTRAP_SEED = BOOTSTRAP_SEED
    engine.PREDICTION_COLUMNS = ENGINE_COLUMNS
    try:
        result = engine.evaluate_external_frames(
            _adapt_predictions(prediction),
            target,
            block,
            _engine_protocol(permit["evaluation_contract"]),
        )
    finally:
        engine.EXTERNAL_CITY_IDS, engine.BOOTSTRAP_SEED, engine.PREDICTION_COLUMNS = old
    tables = {
        "scored_rows.parquet": result.scored_rows,
        "date_metrics.parquet": result.date_metrics,
        "city_metrics.parquet": result.city_metrics,
        "risk_coverage.parquet": result.risk_coverage,
    }
    outputs: dict[str, Any] = {}
    for name, frame in tables.items():
        renamed = frame.rename(columns=lambda column: column.replace("m2", "m3"))
        path = helpers._inside(root, OUTPUT_ROOT / name)
        helpers._write_parquet_exclusive(renamed, path)
        record = helpers._record(root, path, rows=len(renamed))
        record["semantic_sha256"] = canonical_frame_sha256(renamed)
        outputs[name] = record
    summary = _rename_m3(result.summary)
    summary.update(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "city_ids": list(helpers.BLIND_CITY_IDS),
            "legacy_secondary": _legacy_summary(prediction, result.scored_rows),
            "models_refit_recalibrated_or_retuned": False,
            "four_city_cohort_evaluated_as_one_claim": True,
        }
    )
    bootstrap = _rename_m3(result.bootstrap)
    summary_path = helpers._inside(root, OUTPUT_ROOT / "summary.json")
    bootstrap_path = helpers._inside(root, OUTPUT_ROOT / "crossed_bootstrap.json")
    helpers._write_json_exclusive(summary, summary_path)
    helpers._write_json_exclusive(bootstrap, bootstrap_path)
    outputs["summary.json"] = helpers._record(root, summary_path)
    outputs["crossed_bootstrap.json"] = helpers._record(root, bootstrap_path)
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_evaluation_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "prediction_completion_commit_sha256": PREDICTION_COMMIT,
            "target_completion_commit_sha256": TARGET_COMMIT,
            "outputs": outputs,
            "conclusion": {
                "state": summary["state"],
                "point_prediction_success": summary["point_prediction_gates"]["success"],
                "reliability_success": summary["reliability"]["success"],
            },
            "audit": {
                "network_or_href_reads": 0,
                "fit_retrain_recalibrate_retune_or_select": False,
                "target_completion_authenticated_before_scoring": True,
                "frozen_bootstrap_iterations": BOOTSTRAP_ITERATIONS,
                "frozen_bootstrap_seed": BOOTSTRAP_SEED,
            },
            "next_safe_stage": "publish_exact_blind_result_without_rescue_analysis",
        }
    )
    helpers._write_json_exclusive(completion, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("prediction_completion_commit_sha256") != PREDICTION_COMMIT
        or payload.get("target_completion_commit_sha256") != TARGET_COMMIT
        or payload.get("audit", {}).get("target_completion_authenticated_before_scoring")
        is not True
    ):
        raise M3BlindEvaluationError("Evaluation completion changed.")
    for name, record in payload["outputs"].items():
        path = helpers._inside(root, record["path"])
        if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
            raise M3BlindEvaluationError(f"Evaluation output drifted: {name}")
        if name.endswith(".parquet"):
            frame = pd.read_parquet(path)
            if (
                len(frame) != record["rows"]
                or canonical_frame_sha256(frame) != record["semantic_sha256"]
            ):
                raise M3BlindEvaluationError(f"Evaluation table drifted: {name}")
    return payload
