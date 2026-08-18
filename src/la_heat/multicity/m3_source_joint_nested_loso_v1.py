"""Authorized source-only joint ST_QA/M3 nested whole-city LOSO.

The metadata functions in this module deliberately stop before opening any
predictor or QA-target parquet.  Source values become readable only after a
separate append-only authorization authenticates.  The pure selection
functions accept already-open in-memory frames so they can be exercised with
synthetic data without granting access to formal source values.

``SOURCE_NESTED_LOSO_COMPLETE`` is intentionally broader than the joint-model
stage: it may be written only after the source-only uncertainty and risk
selection stages also authenticate.  No function in this module permits a
blind-test-city asset, predictor, QA, or target read.
"""

from __future__ import annotations

import json
import math
import os
import pickle
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity.m3_development import (
    KEY_COLUMNS,
    M2_FEATURES,
    M3_CANDIDATES,
    M3Candidate,
    fit_m3_candidate,
    predict_m3,
    select_density_method,
    select_risk_method,
    validate_prediction_feature_frame,
)
from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    authenticate_source_predictors_46_completion_metadata,
)
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-source-joint-nested-loso-v1"
CONFIG_PATH: Final = Path("configs/multicity/m3_source_joint_nested_loso_v1.toml")
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_JOINT_NESTED_LOSO_V1_AUTHORIZATION.json"
)
PROTOCOL_LOCK_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json"
)
QA_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/SOURCE_QA_CANDIDATES_COMPLETE.json"
)
PREDICTOR_COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_predictor_extension_v1/"
    "SOURCE_PREDICTORS_46_COMPLETE.json"
)
VALUES_OPENED_PATH: Final = Path(
    "data/interim/multicity/m3_source_joint_nested_loso_v1/VALUES_OPENED.json"
)
JOINT_STAGE_PATH: Final = Path(
    "data/processed/multicity/m3_source_joint_nested_loso_v1/JOINT_NESTED_LOSO_STAGE_COMPLETE.json"
)
UQ_STAGE_PATH: Final = Path(
    "data/processed/multicity/m3_source_joint_nested_loso_v1/SOURCE_UQ_SELECTION_COMPLETE.json"
)
RISK_STAGE_PATH: Final = Path(
    "data/processed/multicity/m3_source_joint_nested_loso_v1/SOURCE_RISK_SELECTION_COMPLETE.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_joint_nested_loso_v1/"
    "SOURCE_NESTED_LOSO_COMPLETE.json"
)

EXPECTED_PROTOCOL_COMMIT: Final = "dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8"
EXPECTED_QA_COMPLETION_COMMIT: Final = (
    "f3276d7254c71c642c8300edacacd1a3caa7c3e279a7dc820c9de5928cdac1d4"
)
SOURCE_CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
BLIND_TEST_CITY_IDS: Final = (
    "seattle_wa",
    "denver_co",
    "atlanta_ga",
    "miami_fl",
)
QA_IDS: Final = ("none", "3k", "4k", "6k")
QA_LENIENCY_RANK: Final = {"none": 4, "3k": 1, "4k": 2, "6k": 3}
M3_COMPLEXITY_RANK: Final = {
    "level_ridge_alpha_10__anomaly_hgb_leaves_15": 1,
    "level_ridge_alpha_1__anomaly_hgb_leaves_15": 2,
    "level_ridge_alpha_10__anomaly_hgb_leaves_31": 3,
    "level_ridge_alpha_1__anomaly_hgb_leaves_31": 4,
}
MINIMUM_DATES_PER_INNER_CITY: Final = 8
MINIMUM_TOTAL_INNER_CITY_DATES: Final = 30
CONTEXT_FEATURE: Final = "city_centroid_latitude_deg"
CITY_CENTROID_ALGORITHM: Final = (
    "authenticate_census_place_city_boundary;project_to_locked_target_grid_crs;"
    "unary_union;centroid;transform_centroid_to_epsg4326;take_latitude"
)

CODE_PATHS: Final = (
    CONFIG_PATH.as_posix(),
    "src/la_heat/multicity/m3_source_joint_nested_loso_v1.py",
    "scripts/authorize_m3_source_joint_nested_loso_v1.py",
    "scripts/run_m3_source_joint_nested_loso_v1.py",
)


class M3SourceJointLosoError(RuntimeError):
    """Raised when the append-only or nested-LOSO contract is violated."""


@dataclass(frozen=True, slots=True)
class JointConfiguration:
    """One member of the fixed four-QA by four-M3 Cartesian product."""

    joint_candidate_id: str
    qa_id: str
    m3_candidate: M3Candidate
    qa_leniency_rank: int
    m3_complexity_rank: int


@dataclass(frozen=True, slots=True)
class PreparedQADataset:
    """Predictor rows aligned with one QA candidate after the value gate."""

    frame: pd.DataFrame
    target: pd.Series
    prediction_universe: pd.DataFrame
    usable_date_counts: Mapping[str, int]
    total_predictor_rows_on_usable_dates: int


@dataclass(frozen=True, slots=True)
class JointNestedLosoResult:
    """Source-only joint selection, outer pseudo-tests, and final source refit."""

    selected_joint_candidate_id: str
    selected_qa_id: str
    selected_m3_candidate_id: str
    candidate_metrics: pd.DataFrame
    outer_inner_candidate_metrics: pd.DataFrame
    outer_selections: pd.DataFrame
    outer_oof_predictions: pd.DataFrame
    final_fitted_model: Any


def _joint_configurations() -> tuple[JointConfiguration, ...]:
    candidates = {value.candidate_id: value for value in M3_CANDIDATES}
    if set(candidates) != set(M3_COMPLEXITY_RANK):
        raise M3SourceJointLosoError("The frozen M3 candidate universe changed.")
    result = []
    for qa_id in QA_IDS:
        for candidate_id in sorted(candidates):
            result.append(
                JointConfiguration(
                    joint_candidate_id=f"qa_{qa_id}__{candidate_id}",
                    qa_id=qa_id,
                    m3_candidate=candidates[candidate_id],
                    qa_leniency_rank=QA_LENIENCY_RANK[qa_id],
                    m3_complexity_rank=M3_COMPLEXITY_RANK[candidate_id],
                )
            )
    return tuple(result)


JOINT_CONFIGURATIONS: Final = _joint_configurations()


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceJointLosoError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceJointLosoError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict):
        raise M3SourceJointLosoError(f"{label} must be a JSON object.")
    return payload


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    payload = _read_json(path, label=label)
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise M3SourceJointLosoError(f"{label} commit is invalid.")
    return payload


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("commit_sha256", None)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceJointLosoError(f"Append-only output already exists: {destination}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise M3SourceJointLosoError(f"Bound code/config file is missing: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _load_config(root: Path, config_path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    path = _inside(root, config_path, label="Joint LOSO config")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise M3SourceJointLosoError(f"Cannot read joint LOSO config: {path}") from error
    if not isinstance(payload, dict):
        raise M3SourceJointLosoError("Joint LOSO config must be a TOML table.")
    return payload


def _require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise M3SourceJointLosoError(f"{label} must be a mapping.")
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    inputs = _require_mapping(config.get("inputs"), label="inputs")
    outputs = _require_mapping(config.get("outputs"), label="outputs")
    cohorts = _require_mapping(config.get("cohorts"), label="cohorts")
    selection = _require_mapping(config.get("selection"), label="selection")
    tie = _require_mapping(selection.get("tie_break"), label="selection.tie_break")
    uq = _require_mapping(config.get("source_only_uq"), label="source_only_uq")
    risk = _require_mapping(config.get("source_only_risk"), label="source_only_risk")
    completion = _require_mapping(config.get("completion"), label="completion")
    permissions = _require_mapping(config.get("permissions"), label="permissions")
    qa_rows = config.get("qa_candidates")
    m3_rows = config.get("m3_candidates")
    expected_tie = [
        "higher_minimum_usable_dates_across_inner_validation_cities",
        "higher_total_usable_city_dates",
        "higher_overall_tract_date_retention",
        "more_lenient_st_qa_none_then_6k_then_4k_then_3k",
        "lower_m3_complexity_rank",
        "joint_candidate_id_lexicographic_ascending",
    ]
    expected_m3_rows = [
        {
            "candidate_id": "level_ridge_alpha_1__anomaly_hgb_leaves_15",
            "level_alpha": 1.0,
            "anomaly_max_leaf_nodes": 15,
            "complexity_rank": 2,
        },
        {
            "candidate_id": "level_ridge_alpha_1__anomaly_hgb_leaves_31",
            "level_alpha": 1.0,
            "anomaly_max_leaf_nodes": 31,
            "complexity_rank": 4,
        },
        {
            "candidate_id": "level_ridge_alpha_10__anomaly_hgb_leaves_15",
            "level_alpha": 10.0,
            "anomaly_max_leaf_nodes": 15,
            "complexity_rank": 1,
        },
        {
            "candidate_id": "level_ridge_alpha_10__anomaly_hgb_leaves_31",
            "level_alpha": 10.0,
            "anomaly_max_leaf_nodes": 31,
            "complexity_rank": 3,
        },
    ]
    if (
        config.get("schema_version") != 1
        or config.get("algorithm_version") != ALGORITHM_VERSION
        or inputs.get("protocol_lock") != PROTOCOL_LOCK_PATH.as_posix()
        or inputs.get("protocol_lock_commit_sha256") != EXPECTED_PROTOCOL_COMMIT
        or inputs.get("source_qa_completion") != QA_COMPLETION_PATH.as_posix()
        or inputs.get("source_qa_completion_commit_sha256") != EXPECTED_QA_COMPLETION_COMMIT
        or inputs.get("source_qa_city_root")
        != "data/interim/multicity/m3_source_development_v2/qa_candidates/cities"
        or inputs.get("source_predictors_completion") != PREDICTOR_COMPLETION_PATH.as_posix()
        or inputs.get("source_predictors_required_state") != "source_predictors_46_complete"
        or inputs.get("source_predictor_table_columns")
        != "keys_plus_exact_46_without_city_centroid"
        or inputs.get("city_context_feature") != CONTEXT_FEATURE
        or inputs.get("city_context_algorithm") != CITY_CENTROID_ALGORITHM
        or inputs.get("city_context_assembly")
        != "mechanically_repeat_authenticated_city_value_by_city_id_after_authorization"
        or tuple(cohorts.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or tuple(cohorts.get("blind_test_city_ids", ())) != BLIND_TEST_CITY_IDS
        or cohorts.get("outer_holdout_unit") != "one_complete_source_city"
        or cohorts.get("inner_holdout_unit")
        != "one_complete_city_from_the_three_outer_training_cities"
        or outputs
        != {
            "root": "data/processed/multicity/m3_source_joint_nested_loso_v1",
            "values_opened_marker": VALUES_OPENED_PATH.as_posix(),
            "joint_stage": JOINT_STAGE_PATH.as_posix(),
            "source_uq_stage": UQ_STAGE_PATH.as_posix(),
            "source_risk_stage": RISK_STAGE_PATH.as_posix(),
            "completion": COMPLETION_PATH.as_posix(),
        }
        or selection.get("joint_configuration_count") != 16
        or selection.get("outer_fold_count") != 4
        or selection.get("inner_city_count_per_outer_fold") != 3
        or selection.get("minimum_usable_dates_per_inner_validation_city")
        != MINIMUM_DATES_PER_INNER_CITY
        or selection.get("minimum_total_usable_inner_validation_city_dates")
        != MINIMUM_TOTAL_INNER_CITY_DATES
        or selection.get("primary_objective") != "equal_city_equal_date_m3_absolute_lst_mae"
        or selection.get("primary_objective_round_decimal_places") != 12
        or selection.get("overall_retention")
        != "retained_finite_model_rows_divided_by_all_frozen_predictor_rows_on_usable_dates"
        or selection.get("outer_scores_select_final_configuration") is not False
        or selection.get("final_selection")
        != (
            "repeat_whole_city_loso_on_all_four_sources_then_refit_selected_"
            "configuration_on_all_four_sources"
        )
        or selection.get("pre_value_complexity_disambiguation")
        != "fewer_anomaly_leaves_first_then_stronger_ridge_regularization"
        or tie.get("order") != expected_tie
        or not isinstance(qa_rows, list)
        or [(row.get("id"), row.get("leniency_rank")) for row in qa_rows]
        != [(value, QA_LENIENCY_RANK[value]) for value in QA_IDS]
        or m3_rows != expected_m3_rows
        or uq
        != {
            "selection_unit": "outer_held_source_city_as_pseudo_test",
            "source_oof_residuals_only": True,
            "density_weighted_candidate_requires_all_protocol_stability_and_benefit_gates": True,
            "fallback_method": "unweighted_cross_conformal",
            "blind_predictors_allowed": False,
        }
        or risk
        != {
            "selection_unit": "outer_held_source_city_as_pseudo_test",
            "candidate_methods": ["learned_error", "interval_width", "ensemble_sd"],
            "fallback_method": "none_accept_all",
            "blind_predictors_allowed": False,
        }
        or completion
        != {
            "implementation_status": (
                "final_completion_disabled_until_executable_source_uq_and_risk_stages"
            ),
            "joint_selection_stage_required": True,
            "source_uq_selection_stage_required": True,
            "source_risk_selection_stage_required": True,
            "completion_may_not_claim_final_source_selection_before_all_three_stages": True,
        }
        or permissions.get("authorization_may_read_json_metadata_and_code_only") is not True
        or permissions.get("authorization_may_read_predictor_or_qa_parquet") is not False
        or permissions.get("source_predictor_and_qa_values_require_append_only_authorization")
        is not True
        or permissions.get("fit_select_predict_or_score_before_authorization") is not False
        or permissions.get("source_only_fit_after_authorization") is not True
        or permissions.get("blind_city_asset_predictor_qa_or_target_access") is not False
        or permissions.get("network_or_href_reads") is not False
        or permissions.get("change_year_city_candidate_support_or_tie_break") is not False
    ):
        raise M3SourceJointLosoError("Joint LOSO config drifted from the frozen contract.")


def _authenticate_qa_completion(root: Path, path: Path) -> dict[str, Any]:
    payload = _read_committed(path, label="source QA completion")
    audit = _require_mapping(payload.get("offline_audit"), label="QA offline audit")
    if (
        payload.get("commit_sha256") != EXPECTED_QA_COMPLETION_COMMIT
        or payload.get("state") != "source_qa_candidates_complete"
        or payload.get("m3_protocol_lock_commit_sha256") != EXPECTED_PROTOCOL_COMMIT
        or tuple(payload.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or tuple(payload.get("candidate_ids", ())) != QA_IDS
        or _require_mapping(payload.get("support_gate"), label="QA support gate").get("passed")
        is not True
        or audit.get("network_requests_performed") != 0
        or audit.get("href_reads_performed") != 0
        or audit.get("blind_test_city_accessed") is not False
        or audit.get("predictor_values_read_or_built") is not False
        or audit.get("model_fit_selection_prediction_or_scoring_performed") is not False
    ):
        raise M3SourceJointLosoError("Source QA completion is not the frozen safe completion.")
    return payload


def _qa_target_records(
    root: Path,
    qa_completion: Mapping[str, Any],
    city_root: Path,
) -> list[dict[str, Any]]:
    commit_rows = qa_completion.get("city_commits")
    if not isinstance(commit_rows, list) or len(commit_rows) != len(SOURCE_CITY_IDS):
        raise M3SourceJointLosoError("Source QA city completion universe changed.")
    expected_commits = {
        str(row.get("city_id")): str(row.get("commit_sha256")) for row in commit_rows
    }
    if set(expected_commits) != set(SOURCE_CITY_IDS):
        raise M3SourceJointLosoError("Source QA city completion cohort changed.")
    records: list[dict[str, Any]] = []
    for city_id in SOURCE_CITY_IDS:
        completion_path = city_root / city_id / "CITY_QA_CANDIDATES_COMPLETE.json"
        city_payload = _read_committed(completion_path, label=f"{city_id} QA city completion")
        if (
            city_payload.get("commit_sha256") != expected_commits[city_id]
            or tuple(city_payload.get("candidate_ids", ())) != QA_IDS
            or city_payload.get("blind_test_city_accessed") is not False
            or city_payload.get("model_fit_or_selection_performed") is not False
        ):
            raise M3SourceJointLosoError(f"{city_id} QA city completion drifted.")
        outputs = _require_mapping(
            city_payload.get("output_files"), label=f"{city_id} QA output files"
        )
        for qa_id in QA_IDS:
            relative = f"{qa_id}/targets.parquet"
            record = _require_mapping(
                outputs.get(relative), label=f"{city_id} {qa_id} target record"
            )
            required = ("sha256", "bytes", "rows", "schema_sha256", "semantic_sha256")
            if not all(record.get(key) not in (None, "") for key in required):
                raise M3SourceJointLosoError(f"{city_id} {qa_id} target record is incomplete.")
            records.append(
                {
                    "city_id": city_id,
                    "qa_id": qa_id,
                    "path": _relative(root, city_root / city_id / relative),
                    **{key: record[key] for key in required},
                    "city_completion_commit_sha256": expected_commits[city_id],
                }
            )
    return records


def _authenticate_predictor_completion(root: Path, path: Path) -> dict[str, Any]:
    payload = _read_committed(path, label="source predictor completion")
    audit = _require_mapping(payload.get("audit"), label="predictor audit")
    tables = payload.get("city_tables")
    contexts = payload.get("city_context")
    required_columns = [*KEY_COLUMNS, *M2_FEATURES]
    if (
        payload.get("state") != "source_predictors_46_complete"
        or payload.get("source_qa_candidates_completion_commit_sha256")
        != EXPECTED_QA_COMPLETION_COMMIT
        or not isinstance(payload.get("authorization_commit_sha256"), str)
        or len(str(payload.get("authorization_commit_sha256"))) != 64
        or not isinstance(payload.get("acquisition_completion_commit_sha256"), str)
        or len(str(payload.get("acquisition_completion_commit_sha256"))) != 64
        or payload.get("feature_names") != list(M2_FEATURES)
        or payload.get("feature_count") != len(M2_FEATURES)
        or payload.get("context_features") != [CONTEXT_FEATURE]
        or payload.get("required_columns") != required_columns
        or not isinstance(tables, list)
        or [row.get("city_id") for row in tables] != list(SOURCE_CITY_IDS)
        or not isinstance(contexts, list)
        or [row.get("city_id") for row in contexts] != list(SOURCE_CITY_IDS)
        or any(
            tuple(row)
            != (
                "city_id",
                CONTEXT_FEATURE,
                "algorithm",
                "geography_commit_sha256",
                "worldcover_support_commit_sha256",
                "city_boundary_sha256",
                "target_grid_crs",
            )
            or row.get("algorithm") != CITY_CENTROID_ALGORITHM
            or not math.isfinite(float(row.get(CONTEXT_FEATURE, math.nan)))
            or any(
                row.get(key) in (None, "")
                for key in (
                    "geography_commit_sha256",
                    "worldcover_support_commit_sha256",
                    "city_boundary_sha256",
                    "target_grid_crs",
                )
            )
            for row in contexts
        )
        or audit.get("offline_network_requests") != 0
        or audit.get("offline_href_reads") != 0
        or audit.get("blind_test_city_accessed") is not False
        or audit.get("target_or_landsat_values_read") is not False
        or audit.get("model_fit_select_predict_or_score_performed") is not False
    ):
        raise M3SourceJointLosoError("Source predictor completion drifted from its contract.")
    required_record = ("path", "sha256", "bytes", "rows", "schema_sha256", "semantic_sha256")
    if any(not all(row.get(key) not in (None, "") for key in required_record) for row in tables):
        raise M3SourceJointLosoError("A source predictor table record is incomplete.")
    for row in tables:
        city_id = str(row["city_id"])
        expected_path = (
            root
            / "data/processed/multicity/m3_source_predictor_extension_v1"
            / city_id
            / "predictors_46.parquet"
        ).resolve()
        observed_path = _inside(root, str(row["path"]), label=f"{city_id} predictor path")
        if observed_path != expected_path:
            raise M3SourceJointLosoError(f"{city_id} predictor path changed.")
    return payload


def joint_loso_readiness(
    project_root: str | Path,
    *,
    config_path: str | Path = CONFIG_PATH,
) -> dict[str, Any]:
    """Authenticate JSON/code readiness without statting or opening a parquet."""

    root = Path(project_root).resolve()
    config = _load_config(root, config_path)
    _validate_config(config)
    inputs = _require_mapping(config["inputs"], label="inputs")
    protocol_path = _inside(root, str(inputs["protocol_lock"]), label="protocol lock")
    protocol = authenticate_m3_development_protocol_lock(root, protocol_path)
    if protocol.get("commit_sha256") != EXPECTED_PROTOCOL_COMMIT:
        raise M3SourceJointLosoError("M3 protocol lock identity changed.")
    qa_path = _inside(root, str(inputs["source_qa_completion"]), label="QA completion")
    qa_completion = _authenticate_qa_completion(root, qa_path)
    city_root = _inside(root, str(inputs["source_qa_city_root"]), label="QA city root")
    qa_records = _qa_target_records(root, qa_completion, city_root)
    predictor_path = _inside(
        root, str(inputs["source_predictors_completion"]), label="predictor completion"
    )
    if not predictor_path.is_file():
        return {
            "state": "waiting_for_source_predictors_46_complete",
            "ready": False,
            "m3_protocol_lock_commit_sha256": EXPECTED_PROTOCOL_COMMIT,
            "source_qa_candidates_completion_commit_sha256": EXPECTED_QA_COMPLETION_COMMIT,
            "source_predictors_completion": _relative(root, predictor_path),
            "parquet_files_opened_or_statted": 0,
            "model_fit_selection_prediction_or_scoring_performed": False,
            "blind_test_city_accessed": False,
        }
    predictor_completion = authenticate_source_predictors_46_completion_metadata(
        root,
        completion_path=predictor_path,
    )
    if predictor_completion != _authenticate_predictor_completion(root, predictor_path):
        raise M3SourceJointLosoError("Predictor metadata authentication disagreed.")
    return {
        "state": "blocked_pending_executable_source_uq_and_risk_stages",
        "ready": False,
        "metadata_ready": True,
        "m3_protocol_lock_commit_sha256": EXPECTED_PROTOCOL_COMMIT,
        "source_qa_candidates_completion_commit_sha256": EXPECTED_QA_COMPLETION_COMMIT,
        "source_predictors_completion_commit_sha256": predictor_completion["commit_sha256"],
        "source_predictor_tables": [dict(row) for row in predictor_completion["city_tables"]],
        "source_city_context": [dict(row) for row in predictor_completion["city_context"]],
        "source_qa_target_tables": qa_records,
        "parquet_files_opened_or_statted": 0,
        "model_fit_selection_prediction_or_scoring_performed": False,
        "blind_test_city_accessed": False,
    }


def build_m3_source_joint_nested_loso_authorization(
    project_root: str | Path,
    *,
    config_path: str | Path = CONFIG_PATH,
) -> dict[str, Any]:
    """Build the value-access permit from committed metadata only."""

    root = Path(project_root).resolve()
    readiness = joint_loso_readiness(root, config_path=config_path)
    if readiness.get("metadata_ready") is True:
        raise M3SourceJointLosoError(
            "Formal joint LOSO authorization is disabled until executable source-only "
            "UQ and risk stages are code-bound before first value access."
        )
    if readiness.get("ready") is not True:
        raise M3SourceJointLosoError("Source predictor completion is not ready.")
    code_identity = {relative: _file_record(root, root / relative) for relative in CODE_PATHS}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_source_joint_nested_loso_v1_authorized",
        "m3_protocol_lock_commit_sha256": EXPECTED_PROTOCOL_COMMIT,
        "source_qa_candidates_completion_commit_sha256": EXPECTED_QA_COMPLETION_COMMIT,
        "source_predictors_completion_commit_sha256": readiness[
            "source_predictors_completion_commit_sha256"
        ],
        "source_city_ids": list(SOURCE_CITY_IDS),
        "blind_test_city_ids": list(BLIND_TEST_CITY_IDS),
        "source_predictor_tables": readiness["source_predictor_tables"],
        "source_qa_target_tables": readiness["source_qa_target_tables"],
        "source_city_context": readiness["source_city_context"],
        "city_context_contract": {
            "feature": CONTEXT_FEATURE,
            "counted_in_exact_46_predictors": False,
            "algorithm": CITY_CENTROID_ALGORITHM,
            "assembly": "mechanically_repeat_authenticated_city_value_by_city_id",
            "derive_estimate_or_read_from_target_in_joint_stage": False,
        },
        "joint_configuration_ids": [value.joint_candidate_id for value in JOINT_CONFIGURATIONS],
        "selection_contract": {
            "outer_unit": "one_complete_source_city",
            "inner_unit": "one_complete_city_from_three_outer_training_cities",
            "held_city_rows_dates_fit_imputation_or_selection": False,
            "minimum_usable_dates_per_inner_city": MINIMUM_DATES_PER_INNER_CITY,
            "minimum_total_usable_inner_city_dates": MINIMUM_TOTAL_INNER_CITY_DATES,
            "primary_objective": "equal_city_equal_date_m3_absolute_lst_mae",
            "primary_objective_round_decimal_places": 12,
            "tie_break": [
                "higher_minimum_usable_dates_across_inner_validation_cities",
                "higher_total_usable_city_dates",
                "higher_overall_tract_date_retention",
                "more_lenient_st_qa_none_then_6k_then_4k_then_3k",
                "lower_m3_complexity_rank",
                "joint_candidate_id_lexicographic_ascending",
            ],
            "m3_complexity_rank": M3_COMPLEXITY_RANK,
            "complexity_rank_frozen_before_value_access": True,
        },
        "completion_contract": {
            "implementation_status": (
                "final_completion_disabled_until_executable_source_uq_and_risk_stages"
            ),
            "joint_stage_required": True,
            "source_only_uq_stage_required": True,
            "source_only_risk_stage_required": True,
            "outer_held_source_pseudo_tests_only": True,
            "density_fallback": "unweighted_cross_conformal",
            "risk_fallback": "none_accept_all",
            "completion_before_all_three_stages": False,
        },
        "values_opened_marker": VALUES_OPENED_PATH.as_posix(),
        "joint_stage_completion": JOINT_STAGE_PATH.as_posix(),
        "source_uq_stage_completion": UQ_STAGE_PATH.as_posix(),
        "source_risk_stage_completion": RISK_STAGE_PATH.as_posix(),
        "source_nested_loso_completion": COMPLETION_PATH.as_posix(),
        "code_identity": code_identity,
        "permissions": {
            "read_authenticated_source_predictor_and_qa_target_values": True,
            "fit_select_predict_and_score_source_only": True,
            "network_or_href_reads": False,
            "blind_city_asset_predictor_qa_or_target_access": False,
            "modify_input_predictor_or_qa_tables": False,
        },
        "authorization_audit": {
            "predictor_or_qa_parquet_opened_or_statted": 0,
            "model_fit_selection_prediction_or_scoring_performed": False,
            "blind_test_city_accessed": False,
            "network_or_href_reads": 0,
        },
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def authenticate_m3_source_joint_nested_loso_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Rebuild the permit exactly while still remaining metadata-only."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="joint LOSO authorization")
    observed = _read_committed(path, label="joint LOSO authorization")
    expected = build_m3_source_joint_nested_loso_authorization(root)
    if observed != expected:
        raise M3SourceJointLosoError("Joint LOSO authorization drifted.")
    return observed


def create_m3_source_joint_nested_loso_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Append the formal permit once, without opening source values."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="joint LOSO authorization")
    payload = build_m3_source_joint_nested_loso_authorization(root)
    _write_exclusive(payload, path)
    return authenticate_m3_source_joint_nested_loso_authorization(root, path)


def create_values_opened_marker(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Create the first-value-read marker only after exact permit authentication."""

    root = Path(project_root).resolve()
    authorization = authenticate_m3_source_joint_nested_loso_authorization(root, authorization_path)
    marker = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_source_joint_nested_loso_values_opened",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "m3_protocol_lock_commit_sha256": EXPECTED_PROTOCOL_COMMIT,
            "source_qa_candidates_completion_commit_sha256": (EXPECTED_QA_COMPLETION_COMMIT),
            "source_predictors_completion_commit_sha256": authorization[
                "source_predictors_completion_commit_sha256"
            ],
            "source_city_ids": list(SOURCE_CITY_IDS),
            "blind_test_city_accessed": False,
            "network_or_href_reads": 0,
        }
    )
    destination = _inside(root, authorization["values_opened_marker"], label="values marker")
    if destination.is_file():
        observed = _read_committed(destination, label="values marker")
        if observed != marker:
            raise M3SourceJointLosoError("Existing values marker belongs to another permit.")
        return observed
    _write_exclusive(marker, destination)
    return _read_committed(destination, label="values marker")


def _normalize_keys(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    result = frame.copy()
    missing = sorted(set(KEY_COLUMNS) - set(result.columns))
    if missing or result.empty:
        raise M3SourceJointLosoError(f"{label} has missing keys or no rows: {missing}")
    result["city_id"] = result["city_id"].astype(str)
    result["tract_geoid"] = result["tract_geoid"].astype(str)
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise").dt.strftime(
        "%Y-%m-%d"
    )
    if (
        result.loc[:, KEY_COLUMNS].isna().any(axis=None)
        or result.duplicated(list(KEY_COLUMNS)).any()
    ):
        raise M3SourceJointLosoError(f"{label} keys are missing or duplicated.")
    cities = set(result["city_id"])
    if cities & set(BLIND_TEST_CITY_IDS) or not cities <= set(SOURCE_CITY_IDS):
        raise M3SourceJointLosoError(f"{label} left the four-source cohort.")
    return result


def add_authenticated_city_context(
    predictors: pd.DataFrame,
    city_context: Mapping[str, float],
) -> pd.DataFrame:
    """Mechanically repeat the authenticated centroid latitude by source city."""

    frame = _normalize_keys(predictors, label="source predictors")
    if tuple(city_context) != SOURCE_CITY_IDS or any(
        not math.isfinite(float(value)) for value in city_context.values()
    ):
        raise M3SourceJointLosoError("Authenticated city context is incomplete or reordered.")
    if CONTEXT_FEATURE in frame.columns:
        raise M3SourceJointLosoError("Predictor tables may not smuggle in the context feature.")
    frame[CONTEXT_FEATURE] = frame["city_id"].map(city_context).astype(float)
    return frame


def prepare_qa_dataset(
    predictors_with_context: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    qa_id: str,
    required_cities: Sequence[str] = SOURCE_CITY_IDS,
) -> PreparedQADataset:
    """Align one QA table to predictors after the formal value-access gate."""

    if qa_id not in QA_IDS:
        raise M3SourceJointLosoError("QA candidate is outside the frozen four.")
    cities = tuple(required_cities)
    if not cities or not set(cities) <= set(SOURCE_CITY_IDS):
        raise M3SourceJointLosoError("Required cities must be a nonempty source-only subset.")
    predictors = validate_prediction_feature_frame(
        _normalize_keys(predictors_with_context, label="source predictors"),
        required_features=(*M2_FEATURES, CONTEXT_FEATURE),
    )
    predictors = predictors.loc[predictors["city_id"].isin(cities)].reset_index(drop=True)
    if set(predictors["city_id"]) != set(cities):
        raise M3SourceJointLosoError("Predictors do not cover every requested source city.")
    target_frame = _normalize_keys(targets, label=f"{qa_id} target table")
    required = {"date_usable", "target_available", "target_lst_c"}
    missing = sorted(required - set(target_frame.columns))
    if missing:
        raise M3SourceJointLosoError(f"{qa_id} target table lacks columns: {missing}")
    target_frame = target_frame.loc[target_frame["city_id"].isin(cities)].reset_index(drop=True)
    if set(target_frame["city_id"]) != set(cities):
        raise M3SourceJointLosoError("Target table does not cover every requested source city.")
    merged = predictors.merge(
        target_frame.loc[:, [*KEY_COLUMNS, "date_usable", "target_available", "target_lst_c"]],
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if not merged["_merge"].eq("both").all():
        raise M3SourceJointLosoError("Predictor and target key universes differ.")
    date_usable = merged["date_usable"].fillna(False).astype(bool)
    target_available = merged["target_available"].fillna(False).astype(bool)
    numeric_target = pd.to_numeric(merged["target_lst_c"], errors="coerce")
    finite_target = np.isfinite(numeric_target.to_numpy(dtype=float))
    keep = date_usable.to_numpy() & target_available.to_numpy() & finite_target
    usable_rows = int(date_usable.sum())
    # Prediction remains target/QA blind: every frozen predictor key for the
    # requested cities is scored first.  QA/date usability is applied only by
    # the later merge to observed scoring rows.
    prediction_universe = predictors.reset_index(drop=True)
    selected = merged.loc[keep, predictors.columns].reset_index(drop=True)
    y = numeric_target.loc[keep].astype(float).reset_index(drop=True)
    if selected.empty:
        raise M3SourceJointLosoError(f"{qa_id} has no finite model rows.")
    counts = (
        merged.loc[date_usable, ["city_id", "target_date"]]
        .drop_duplicates()
        .groupby("city_id", observed=True)["target_date"]
        .nunique()
        .to_dict()
    )
    return PreparedQADataset(
        selected,
        y,
        prediction_universe,
        {city: int(counts.get(city, 0)) for city in cities},
        usable_rows,
    )


def _equal_city_equal_date_mae(predictions: pd.DataFrame) -> float:
    frame = predictions.copy()
    frame["absolute_error_c"] = (
        pd.to_numeric(frame["m3_prediction_c"], errors="raise")
        - pd.to_numeric(frame["observed_lst_c"], errors="raise")
    ).abs()
    date_scores = frame.groupby(["city_id", "target_date"], observed=True, sort=True)[
        "absolute_error_c"
    ].mean()
    city_scores = date_scores.groupby(level="city_id").mean()
    if city_scores.empty or not np.isfinite(city_scores.to_numpy(dtype=float)).all():
        raise M3SourceJointLosoError("Equal-city/equal-date MAE is invalid.")
    return float(city_scores.mean())


def joint_selection_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the exact primary objective plus six deterministic tie levels."""

    return (
        round(float(row["equal_city_equal_date_mae_c"]), 12),
        -int(row["minimum_usable_dates"]),
        -int(row["total_usable_city_dates"]),
        -float(row["overall_tract_date_retention"]),
        -int(row["qa_leniency_rank"]),
        int(row["m3_complexity_rank"]),
        str(row["joint_candidate_id"]),
    )


def select_joint_configuration(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    eligible = [row for row in rows if row.get("eligible") is True]
    if not eligible:
        raise M3SourceJointLosoError("No joint QA/M3 configuration passed the support gate.")
    return min(eligible, key=joint_selection_key)


def _candidate_loso_metrics(
    prepared: PreparedQADataset,
    cities: Sequence[str],
    configuration: JointConfiguration,
    *,
    fit_func: Callable[[pd.DataFrame, pd.Series, M3Candidate], Any],
    predict_func: Callable[[Any, pd.DataFrame], pd.DataFrame],
) -> tuple[dict[str, Any], pd.DataFrame]:
    counts = {city: int(prepared.usable_date_counts.get(city, 0)) for city in cities}
    minimum_dates = min(counts.values()) if counts else 0
    total_dates = sum(counts.values())
    eligible = (
        minimum_dates >= MINIMUM_DATES_PER_INNER_CITY
        and total_dates >= MINIMUM_TOTAL_INNER_CITY_DATES
    )
    retained = len(prepared.frame)
    denominator = prepared.total_predictor_rows_on_usable_dates
    retention = retained / denominator if denominator else 0.0
    base = {
        "joint_candidate_id": configuration.joint_candidate_id,
        "qa_id": configuration.qa_id,
        "m3_candidate_id": configuration.m3_candidate.candidate_id,
        "qa_leniency_rank": configuration.qa_leniency_rank,
        "m3_complexity_rank": configuration.m3_complexity_rank,
        "minimum_usable_dates": minimum_dates,
        "total_usable_city_dates": total_dates,
        "overall_tract_date_retention": float(retention),
        "eligible": eligible,
    }
    if not eligible:
        return {**base, "equal_city_equal_date_mae_c": math.inf}, pd.DataFrame()
    predictions: list[pd.DataFrame] = []
    for held_city in sorted(cities):
        training = prepared.frame["city_id"].ne(held_city)
        fitted = fit_func(
            prepared.frame.loc[training].reset_index(drop=True),
            prepared.target.loc[training].reset_index(drop=True),
            configuration.m3_candidate,
        )
        held = prepared.prediction_universe.loc[
            prepared.prediction_universe["city_id"].eq(held_city)
        ].reset_index(drop=True)
        predicted = predict_func(fitted, held)
        scored = prepared.frame.loc[~training, KEY_COLUMNS].copy()
        scored["observed_lst_c"] = prepared.target.loc[~training].to_numpy(dtype=float)
        predicted = predicted.merge(
            scored,
            on=list(KEY_COLUMNS),
            how="inner",
            validate="one_to_one",
        )
        if len(predicted) != len(scored):
            raise M3SourceJointLosoError("Held-source predictions lost eligible scoring rows.")
        predicted["held_source_city_id"] = held_city
        predictions.append(predicted)
    combined = pd.concat(predictions, ignore_index=True)
    return {
        **base,
        "equal_city_equal_date_mae_c": _equal_city_equal_date_mae(combined),
    }, combined


def joint_nested_whole_city_loso(
    predictors_with_context: pd.DataFrame,
    targets_by_qa: Mapping[str, pd.DataFrame],
    *,
    fit_func: Callable[[pd.DataFrame, pd.Series, M3Candidate], Any] = fit_m3_candidate,
    predict_func: Callable[[Any, pd.DataFrame], pd.DataFrame] = predict_m3,
) -> JointNestedLosoResult:
    """Run leakage-free joint selection on four source cities in memory."""

    predictors = _normalize_keys(predictors_with_context, label="source predictors")
    if set(predictors["city_id"]) != set(SOURCE_CITY_IDS):
        raise M3SourceJointLosoError("Joint nested LOSO requires exactly four source cities.")
    if tuple(targets_by_qa) != QA_IDS:
        raise M3SourceJointLosoError("Targets must contain the ordered four-QA universe.")
    prepared_all = {
        qa_id: prepare_qa_dataset(predictors, targets_by_qa[qa_id], qa_id=qa_id) for qa_id in QA_IDS
    }
    configuration_by_id = {value.joint_candidate_id: value for value in JOINT_CONFIGURATIONS}
    outer_rows: list[dict[str, Any]] = []
    outer_inner_rows: list[dict[str, Any]] = []
    outer_predictions: list[pd.DataFrame] = []
    for outer_city in SOURCE_CITY_IDS:
        inner_cities = tuple(city for city in SOURCE_CITY_IDS if city != outer_city)
        inner_rows: list[dict[str, Any]] = []
        for configuration in JOINT_CONFIGURATIONS:
            inner_prepared = prepare_qa_dataset(
                predictors,
                targets_by_qa[configuration.qa_id],
                qa_id=configuration.qa_id,
                required_cities=inner_cities,
            )
            row, _ = _candidate_loso_metrics(
                inner_prepared,
                inner_cities,
                configuration,
                fit_func=fit_func,
                predict_func=predict_func,
            )
            inner_rows.append(row)
        outer_inner_rows.extend({"outer_city_id": outer_city, **row} for row in inner_rows)
        selected = select_joint_configuration(inner_rows)
        configuration = configuration_by_id[str(selected["joint_candidate_id"])]
        development = prepared_all[configuration.qa_id]
        train_mask = development.frame["city_id"].ne(outer_city)
        fitted = fit_func(
            development.frame.loc[train_mask].reset_index(drop=True),
            development.target.loc[train_mask].reset_index(drop=True),
            configuration.m3_candidate,
        )
        held = development.prediction_universe.loc[
            development.prediction_universe["city_id"].eq(outer_city)
        ].reset_index(drop=True)
        predicted = predict_func(fitted, held)
        scored = development.frame.loc[~train_mask, KEY_COLUMNS].copy()
        scored["observed_lst_c"] = development.target.loc[~train_mask].to_numpy(dtype=float)
        predicted = predicted.merge(
            scored,
            on=list(KEY_COLUMNS),
            how="inner",
            validate="one_to_one",
        )
        if len(predicted) != len(scored):
            raise M3SourceJointLosoError("Outer predictions lost eligible scoring rows.")
        predicted["outer_city_id"] = outer_city
        predicted["selected_joint_candidate_id"] = configuration.joint_candidate_id
        predicted["selected_qa_id"] = configuration.qa_id
        predicted["selected_m3_candidate_id"] = configuration.m3_candidate.candidate_id
        outer_predictions.append(predicted)
        outer_rows.append(
            {
                "outer_city_id": outer_city,
                **dict(selected),
                "outer_equal_city_equal_date_mae_c": _equal_city_equal_date_mae(predicted),
            }
        )
    final_rows: list[dict[str, Any]] = []
    for configuration in JOINT_CONFIGURATIONS:
        row, _ = _candidate_loso_metrics(
            prepared_all[configuration.qa_id],
            SOURCE_CITY_IDS,
            configuration,
            fit_func=fit_func,
            predict_func=predict_func,
        )
        final_rows.append(row)
    selected = select_joint_configuration(final_rows)
    configuration = configuration_by_id[str(selected["joint_candidate_id"])]
    final_data = prepared_all[configuration.qa_id]
    final_model = fit_func(final_data.frame, final_data.target, configuration.m3_candidate)
    return JointNestedLosoResult(
        configuration.joint_candidate_id,
        configuration.qa_id,
        configuration.m3_candidate.candidate_id,
        pd.DataFrame(final_rows).sort_values("joint_candidate_id").reset_index(drop=True),
        pd.DataFrame(outer_inner_rows)
        .sort_values(["outer_city_id", "joint_candidate_id"], kind="stable")
        .reset_index(drop=True),
        pd.DataFrame(outer_rows).sort_values("outer_city_id").reset_index(drop=True),
        pd.concat(outer_predictions, ignore_index=True)
        .sort_values(list(KEY_COLUMNS), kind="stable")
        .reset_index(drop=True),
        final_model,
    )


def _authenticate_values_marker(
    root: Path,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    marker_path = _inside(root, authorization["values_opened_marker"], label="values marker")
    marker = _read_committed(marker_path, label="values marker")
    if (
        marker.get("state") != "m3_source_joint_nested_loso_values_opened"
        or marker.get("authorization_commit_sha256") != authorization.get("commit_sha256")
        or marker.get("m3_protocol_lock_commit_sha256") != EXPECTED_PROTOCOL_COMMIT
        or marker.get("source_qa_candidates_completion_commit_sha256")
        != EXPECTED_QA_COMPLETION_COMMIT
        or marker.get("source_predictors_completion_commit_sha256")
        != authorization.get("source_predictors_completion_commit_sha256")
        or marker.get("blind_test_city_accessed") is not False
        or marker.get("network_or_href_reads") != 0
    ):
        raise M3SourceJointLosoError("Values marker does not bind the active authorization.")
    return marker


def _authenticate_bound_parquet(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _inside(root, str(record.get("path", "")), label=label)
    if not path.is_file():
        raise M3SourceJointLosoError(f"Bound {label} is missing: {path}")
    if path.stat().st_size != int(record.get("bytes", -1)) or sha256_file(path) != record.get(
        "sha256"
    ):
        raise M3SourceJointLosoError(f"Bound {label} bytes changed.")
    return path


def _schema_sha256(frame: pd.DataFrame) -> str:
    return canonical_sha256([(column, str(dtype)) for column, dtype in frame.dtypes.items()])


def _predictor_semantic_sha256(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["city_id", "target_date", "tract_geoid"], kind="stable")
    return canonical_sha256(ordered.to_dict("records"))


def _qa_semantic_sha256(frame: pd.DataFrame) -> str:
    serializable = json.loads(
        frame.reset_index(drop=True).to_json(
            orient="split",
            date_format="iso",
            date_unit="ns",
            double_precision=15,
            force_ascii=False,
        )
    )
    return canonical_sha256(serializable)


def load_authorized_source_inputs(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Authenticate, then open only the bound four-source predictor/QA tables."""

    root = Path(project_root).resolve()
    authorization = authenticate_m3_source_joint_nested_loso_authorization(root, authorization_path)
    _authenticate_values_marker(root, authorization)
    predictor_frames: list[pd.DataFrame] = []
    expected_predictor_columns = [*KEY_COLUMNS, *M2_FEATURES]
    for record in authorization["source_predictor_tables"]:
        city_id = str(record["city_id"])
        if city_id not in SOURCE_CITY_IDS:
            raise M3SourceJointLosoError("A bound predictor table is outside source cities.")
        path = _authenticate_bound_parquet(root, record, label=f"{city_id} predictor table")
        frame = pd.read_parquet(path)
        if (
            frame.columns.tolist() != expected_predictor_columns
            or _schema_sha256(frame) != record.get("schema_sha256")
            or _predictor_semantic_sha256(frame) != record.get("semantic_sha256")
        ):
            raise M3SourceJointLosoError("A source predictor table is not keys plus exact 46.")
        normalized = _normalize_keys(frame, label=f"{city_id} predictor table")
        if set(normalized["city_id"]) != {city_id} or len(normalized) != int(record["rows"]):
            raise M3SourceJointLosoError("A source predictor table's city or row count changed.")
        predictor_frames.append(normalized)
    predictors = pd.concat(predictor_frames, ignore_index=True)
    context_rows = authorization["source_city_context"]
    context = {str(row["city_id"]): float(row[CONTEXT_FEATURE]) for row in context_rows}
    predictors = add_authenticated_city_context(predictors, context)

    target_frames: dict[str, list[pd.DataFrame]] = {qa_id: [] for qa_id in QA_IDS}
    for record in authorization["source_qa_target_tables"]:
        city_id = str(record["city_id"])
        qa_id = str(record["qa_id"])
        if city_id not in SOURCE_CITY_IDS or qa_id not in QA_IDS:
            raise M3SourceJointLosoError("A bound QA target table left the frozen universe.")
        path = _authenticate_bound_parquet(root, record, label=f"{city_id} {qa_id} QA target table")
        frame = pd.read_parquet(path)
        if _schema_sha256(frame) != record.get("schema_sha256") or _qa_semantic_sha256(
            frame
        ) != record.get("semantic_sha256"):
            raise M3SourceJointLosoError("A QA target table's schema or semantics changed.")
        normalized = _normalize_keys(frame, label=f"{city_id} {qa_id} QA target table")
        if set(normalized["city_id"]) != {city_id} or len(normalized) != int(record["rows"]):
            raise M3SourceJointLosoError("A QA target table's city or row count changed.")
        target_frames[qa_id].append(normalized)
    combined_targets = {
        qa_id: pd.concat(target_frames[qa_id], ignore_index=True) for qa_id in QA_IDS
    }
    if set(predictors["city_id"]) != set(SOURCE_CITY_IDS) or any(
        set(frame["city_id"]) != set(SOURCE_CITY_IDS) for frame in combined_targets.values()
    ):
        raise M3SourceJointLosoError("Authorized inputs do not cover exactly four source cities.")
    return predictors, combined_targets


def _write_bytes_exclusive(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceJointLosoError(
            f"Append-only artifact already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _write_parquet_exclusive(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise M3SourceJointLosoError(f"Append-only artifact already exists: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        frame.to_parquet(temporary, index=False)
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise M3SourceJointLosoError(
                f"Append-only artifact already exists: {destination}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _artifact_record(
    root: Path,
    path: Path,
    *,
    role: str,
    rows: int | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = _file_record(root, path)
    record["role"] = role
    if rows is not None:
        record["rows"] = rows
    return record


def run_authorized_joint_stage(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Run only the joint stage; UQ/risk remain separate required stages."""

    root = Path(project_root).resolve()
    authorization = authenticate_m3_source_joint_nested_loso_authorization(root, authorization_path)
    destination = _inside(
        root, authorization["joint_stage_completion"], label="joint stage completion"
    )
    if destination.is_file():
        return _authenticate_stage(
            root,
            destination,
            state="joint_nested_loso_stage_complete",
            authorization_commit=str(authorization["commit_sha256"]),
        )
    values_marker = create_values_opened_marker(root, authorization_path)
    predictors, targets = load_authorized_source_inputs(root, authorization_path)
    result = joint_nested_whole_city_loso(predictors, targets)
    output_root = _inside(
        root,
        "data/processed/multicity/m3_source_joint_nested_loso_v1/joint",
        label="joint stage output root",
    )
    metrics_path = output_root / "candidate_metrics.parquet"
    inner_metrics_path = output_root / "outer_inner_candidate_metrics.parquet"
    selections_path = output_root / "outer_selections.parquet"
    predictions_path = output_root / "outer_oof_predictions.parquet"
    model_path = output_root / "selected_source_model.pkl"
    model_metadata_path = output_root / "selected_source_model_metadata.json"
    _write_parquet_exclusive(result.candidate_metrics, metrics_path)
    _write_parquet_exclusive(result.outer_inner_candidate_metrics, inner_metrics_path)
    _write_parquet_exclusive(result.outer_selections, selections_path)
    _write_parquet_exclusive(result.outer_oof_predictions, predictions_path)
    _write_bytes_exclusive(
        pickle.dumps(result.final_fitted_model, protocol=pickle.HIGHEST_PROTOCOL), model_path
    )
    model_file = _file_record(root, model_path)
    model_metadata = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "selected_source_model_metadata",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "selected_joint_candidate_id": result.selected_joint_candidate_id,
            "selected_qa_id": result.selected_qa_id,
            "selected_m3_candidate_id": result.selected_m3_candidate_id,
            "feature_names": list(M2_FEATURES),
            "context_features": [CONTEXT_FEATURE],
            "source_city_ids": list(SOURCE_CITY_IDS),
            "model_file": model_file,
            "blind_test_city_accessed": False,
        }
    )
    _write_exclusive(model_metadata, model_metadata_path)
    artifacts = [
        _artifact_record(
            root,
            metrics_path,
            role="final_candidate_metrics",
            rows=len(result.candidate_metrics),
        ),
        _artifact_record(
            root,
            inner_metrics_path,
            role="outer_inner_candidate_metrics",
            rows=len(result.outer_inner_candidate_metrics),
        ),
        _artifact_record(
            root,
            selections_path,
            role="outer_selections",
            rows=len(result.outer_selections),
        ),
        _artifact_record(
            root,
            predictions_path,
            role="outer_oof_predictions",
            rows=len(result.outer_oof_predictions),
        ),
        _artifact_record(root, model_path, role="selected_source_model"),
        _artifact_record(
            root,
            model_metadata_path,
            role="selected_source_model_metadata",
        ),
    ]
    stage = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "joint_nested_loso_stage_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "values_opened_commit_sha256": values_marker["commit_sha256"],
            "m3_protocol_lock_commit_sha256": EXPECTED_PROTOCOL_COMMIT,
            "source_qa_candidates_completion_commit_sha256": (EXPECTED_QA_COMPLETION_COMMIT),
            "source_predictors_completion_commit_sha256": authorization[
                "source_predictors_completion_commit_sha256"
            ],
            "source_city_ids": list(SOURCE_CITY_IDS),
            "configuration_count": len(JOINT_CONFIGURATIONS),
            "outer_fold_count": len(SOURCE_CITY_IDS),
            "support_gate": {
                "minimum_usable_dates_per_inner_validation_city": (MINIMUM_DATES_PER_INNER_CITY),
                "minimum_total_usable_inner_validation_city_dates": (
                    MINIMUM_TOTAL_INNER_CITY_DATES
                ),
            },
            "selected_joint_candidate_id": result.selected_joint_candidate_id,
            "selected_qa_id": result.selected_qa_id,
            "selected_m3_candidate_id": result.selected_m3_candidate_id,
            "final_refit_source_city_ids": list(SOURCE_CITY_IDS),
            "artifacts": artifacts,
            "source_uq_selection_complete": False,
            "source_risk_selection_complete": False,
            "final_source_selection_complete": False,
            "audit": {
                "authorization_authenticated_before_first_value_read": True,
                "blind_test_city_accessed": False,
                "blind_predictor_accessed": False,
                "network_or_href_reads": 0,
            },
            "next_safe_stage": "source_only_uq_and_risk_selection_from_outer_pseudo_tests",
        }
    )
    _write_exclusive(stage, destination)
    return _authenticate_stage(
        root,
        destination,
        state="joint_nested_loso_stage_complete",
        authorization_commit=str(authorization["commit_sha256"]),
    )


def select_source_uq_method(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Lock one source-only UQ method, with the protocol U0 fallback."""

    gate_names = {
        "auc_at_most_0_95",
        "row_ess_at_least_25_percent",
        "date_ess_at_least_50_percent",
        "every_source_city_share_at_least_5_percent",
        "source_clip_fraction_at_most_20_percent",
        "test_clip_fraction_at_most_20_percent",
        "pooled_coverage_in_85_to_95_percent",
        "every_city_coverage_at_least_80_percent",
        "wis_improvement_at_least_2_percent",
    }
    if len(reports) != 1:
        raise M3SourceJointLosoError("UQ selection requires the one frozen clip-5 report.")
    for row in reports:
        gates = row.get("gates")
        if (
            tuple(row.get("outer_held_source_city_ids", ())) != SOURCE_CITY_IDS
            or row.get("blind_predictor_accessed") is not False
            or row.get("method") != "density_ratio_clip_5"
            or float(row.get("clip_upper", math.nan)) != 5.0
            or not isinstance(gates, Mapping)
            or set(gates) != gate_names
            or any(not isinstance(value, bool) for value in gates.values())
            or row.get("stable") is not all(gates.values())
            or any(
                not math.isfinite(float(row.get(name, math.nan)))
                for name in (
                    "weighted_wis",
                    "unweighted_wis",
                    "wis_improvement_fraction",
                    "row_ess_fraction",
                    "date_ess_fraction",
                )
            )
        ):
            raise M3SourceJointLosoError("UQ reports are not four-source pseudo-tests.")
    method = select_density_method(reports)
    return {
        "selected_method": method,
        "fallback_used": method == "unweighted_cross_conformal",
        "fallback_reason": (
            "no_density_candidate_passed_all_frozen_stability_and_benefit_gates"
            if method == "unweighted_cross_conformal"
            else None
        ),
    }


def select_source_risk_method(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Lock one source-only risk method, with deterministic accept-all fallback."""

    allowed = {"learned_error", "interval_width", "ensemble_sd"}
    if len(candidates) != len(allowed) or {row.get("method") for row in candidates} != allowed:
        raise M3SourceJointLosoError("Risk selection requires the exact three-candidate universe.")
    for row in candidates:
        improvements = row.get("per_city_improvement")
        if (
            row.get("method") not in allowed
            or tuple(row.get("outer_held_source_city_ids", ())) != SOURCE_CITY_IDS
            or row.get("blind_predictor_accessed") is not False
            or not isinstance(improvements, Mapping)
            or set(improvements) != set(SOURCE_CITY_IDS)
            or any(not math.isfinite(float(value)) for value in improvements.values())
            or any(
                not math.isfinite(float(row.get(name, math.nan)))
                for name in (
                    "equal_city_improvement",
                    "minimum_retention",
                    "accepted_equal_city_mae_c",
                )
            )
        ):
            raise M3SourceJointLosoError("Risk candidates are not frozen source pseudo-tests.")
    method = select_risk_method(candidates)
    return {
        "selected_method": method,
        "fallback_used": method == "none_accept_all",
        "fallback_reason": (
            "no_risk_candidate_passed_all_frozen_improvement_and_retention_gates"
            if method == "none_accept_all"
            else None
        ),
    }


def _validate_candidate_metric_rows(frame: pd.DataFrame, *, label: str) -> None:
    required = {
        "joint_candidate_id",
        "qa_id",
        "m3_candidate_id",
        "qa_leniency_rank",
        "m3_complexity_rank",
        "minimum_usable_dates",
        "total_usable_city_dates",
        "overall_tract_date_retention",
        "eligible",
        "equal_city_equal_date_mae_c",
    }
    if not required <= set(frame.columns) or len(frame) != len(JOINT_CONFIGURATIONS):
        raise M3SourceJointLosoError(f"{label} does not contain the exact 16 candidates.")
    expected = {item.joint_candidate_id: item for item in JOINT_CONFIGURATIONS}
    if set(frame["joint_candidate_id"].astype(str)) != set(expected):
        raise M3SourceJointLosoError(f"{label} candidate identities changed.")
    for row in frame.to_dict("records"):
        configuration = expected[str(row["joint_candidate_id"])]
        minimum_dates = float(row["minimum_usable_dates"])
        total_dates = float(row["total_usable_city_dates"])
        retention = float(row["overall_tract_date_retention"])
        eligible = bool(row["eligible"])
        mae = float(row["equal_city_equal_date_mae_c"])
        if (
            row["qa_id"] != configuration.qa_id
            or row["m3_candidate_id"] != configuration.m3_candidate.candidate_id
            or int(row["qa_leniency_rank"]) != configuration.qa_leniency_rank
            or int(row["m3_complexity_rank"]) != configuration.m3_complexity_rank
            or not minimum_dates.is_integer()
            or not total_dates.is_integer()
            or minimum_dates < 0
            or total_dates < 0
            or not math.isfinite(retention)
            or not 0 <= retention <= 1
            or eligible
            is not (
                minimum_dates >= MINIMUM_DATES_PER_INNER_CITY
                and total_dates >= MINIMUM_TOTAL_INNER_CITY_DATES
            )
            or (eligible and not math.isfinite(mae))
            or (not eligible and not math.isinf(mae))
        ):
            raise M3SourceJointLosoError(f"{label} metric contract changed.")


def _authenticate_joint_stage_tables(
    root: Path,
    payload: Mapping[str, Any],
    artifact_paths: Mapping[str, Path],
    artifact_records: Mapping[str, Mapping[str, Any]],
) -> None:
    expected_roles = {
        "final_candidate_metrics",
        "outer_inner_candidate_metrics",
        "outer_selections",
        "outer_oof_predictions",
        "selected_source_model",
        "selected_source_model_metadata",
    }
    if set(artifact_paths) != expected_roles:
        raise M3SourceJointLosoError("Joint stage artifact roles changed.")
    output_root = (root / "data/processed/multicity/m3_source_joint_nested_loso_v1/joint").resolve()
    expected_paths = {
        "final_candidate_metrics": output_root / "candidate_metrics.parquet",
        "outer_inner_candidate_metrics": output_root / "outer_inner_candidate_metrics.parquet",
        "outer_selections": output_root / "outer_selections.parquet",
        "outer_oof_predictions": output_root / "outer_oof_predictions.parquet",
        "selected_source_model": output_root / "selected_source_model.pkl",
        "selected_source_model_metadata": output_root / "selected_source_model_metadata.json",
    }
    if artifact_paths != expected_paths:
        raise M3SourceJointLosoError("Joint stage artifact paths changed.")
    final_metrics = pd.read_parquet(artifact_paths["final_candidate_metrics"])
    inner_metrics = pd.read_parquet(artifact_paths["outer_inner_candidate_metrics"])
    selections = pd.read_parquet(artifact_paths["outer_selections"])
    predictions = pd.read_parquet(artifact_paths["outer_oof_predictions"])
    model_metadata = _read_committed(
        artifact_paths["selected_source_model_metadata"],
        label="selected source model metadata",
    )
    for role, frame in (
        ("final_candidate_metrics", final_metrics),
        ("outer_inner_candidate_metrics", inner_metrics),
        ("outer_selections", selections),
        ("outer_oof_predictions", predictions),
    ):
        if artifact_records[role].get("rows") != len(frame):
            raise M3SourceJointLosoError(f"Joint artifact row count changed: {role}")
    _validate_candidate_metric_rows(final_metrics, label="Final candidate metrics")
    selected = select_joint_configuration(final_metrics.to_dict("records"))
    if selected["joint_candidate_id"] != payload.get("selected_joint_candidate_id"):
        raise M3SourceJointLosoError("Final joint winner cannot be reproduced.")
    model_record = artifact_records["selected_source_model"]
    if (
        model_metadata.get("state") != "selected_source_model_metadata"
        or model_metadata.get("authorization_commit_sha256")
        != payload.get("authorization_commit_sha256")
        or model_metadata.get("selected_joint_candidate_id")
        != payload.get("selected_joint_candidate_id")
        or model_metadata.get("selected_qa_id") != payload.get("selected_qa_id")
        or model_metadata.get("selected_m3_candidate_id") != payload.get("selected_m3_candidate_id")
        or model_metadata.get("feature_names") != list(M2_FEATURES)
        or model_metadata.get("context_features") != [CONTEXT_FEATURE]
        or tuple(model_metadata.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or model_metadata.get("model_file", {}).get("path") != model_record.get("path")
        or model_metadata.get("model_file", {}).get("bytes") != model_record.get("bytes")
        or model_metadata.get("model_file", {}).get("sha256") != model_record.get("sha256")
        or model_metadata.get("blind_test_city_accessed") is not False
    ):
        raise M3SourceJointLosoError("Selected source model metadata changed.")
    if (
        len(inner_metrics) != len(SOURCE_CITY_IDS) * len(JOINT_CONFIGURATIONS)
        or set(inner_metrics["outer_city_id"].astype(str)) != set(SOURCE_CITY_IDS)
        or len(selections) != len(SOURCE_CITY_IDS)
        or set(selections["outer_city_id"].astype(str)) != set(SOURCE_CITY_IDS)
    ):
        raise M3SourceJointLosoError("Outer-inner metric or selection universe changed.")
    for outer_city in SOURCE_CITY_IDS:
        rows = inner_metrics.loc[inner_metrics["outer_city_id"].eq(outer_city)].drop(
            columns="outer_city_id"
        )
        _validate_candidate_metric_rows(rows, label=f"{outer_city} inner candidate metrics")
        expected = select_joint_configuration(rows.to_dict("records"))
        observed = selections.loc[selections["outer_city_id"].eq(outer_city)]
        if len(observed) != 1:
            raise M3SourceJointLosoError(f"{outer_city} inner winner is missing.")
        observed_row = observed.iloc[0]
        if any(
            observed_row[key] != expected[key]
            for key in (
                "joint_candidate_id",
                "qa_id",
                "m3_candidate_id",
                "qa_leniency_rank",
                "m3_complexity_rank",
                "minimum_usable_dates",
                "total_usable_city_dates",
                "overall_tract_date_retention",
                "eligible",
                "equal_city_equal_date_mae_c",
            )
        ):
            raise M3SourceJointLosoError(f"{outer_city} inner winner cannot be reproduced.")
    required_prediction_columns = {
        *KEY_COLUMNS,
        "observed_lst_c",
        "outer_city_id",
        "selected_joint_candidate_id",
        "selected_qa_id",
        "selected_m3_candidate_id",
        "m3_prediction_c",
    }
    if (
        predictions.empty
        or not required_prediction_columns <= set(predictions.columns)
        or set(predictions["outer_city_id"].astype(str)) != set(SOURCE_CITY_IDS)
        or set(predictions["city_id"].astype(str)) != set(SOURCE_CITY_IDS)
        or not predictions["city_id"].astype(str).eq(predictions["outer_city_id"].astype(str)).all()
        or predictions.duplicated(list(KEY_COLUMNS)).any()
        or not np.isfinite(
            predictions.loc[:, ["m3_prediction_c", "observed_lst_c"]].to_numpy(dtype=float)
        ).all()
    ):
        raise M3SourceJointLosoError("Outer OOF prediction universe changed.")
    selected_by_city = selections.set_index("outer_city_id")["joint_candidate_id"].astype(str)
    expected_ids = predictions["outer_city_id"].astype(str).map(selected_by_city)
    if not predictions["selected_joint_candidate_id"].astype(str).eq(expected_ids).all():
        raise M3SourceJointLosoError("Outer OOF predictions do not bind each inner winner.")
    configuration_by_id = {item.joint_candidate_id: item for item in JOINT_CONFIGURATIONS}
    expected_qa = expected_ids.map(lambda value: configuration_by_id[str(value)].qa_id)
    expected_m3 = expected_ids.map(
        lambda value: configuration_by_id[str(value)].m3_candidate.candidate_id
    )
    if (
        not predictions["selected_qa_id"].astype(str).eq(expected_qa).all()
        or not predictions["selected_m3_candidate_id"].astype(str).eq(expected_m3).all()
    ):
        raise M3SourceJointLosoError("Outer OOF QA/M3 identities changed.")
    for outer_city in SOURCE_CITY_IDS:
        observed_mae = _equal_city_equal_date_mae(
            predictions.loc[predictions["outer_city_id"].eq(outer_city)]
        )
        recorded_mae = float(
            selections.loc[
                selections["outer_city_id"].eq(outer_city),
                "outer_equal_city_equal_date_mae_c",
            ].iloc[0]
        )
        if round(observed_mae, 12) != round(recorded_mae, 12):
            raise M3SourceJointLosoError(f"{outer_city} outer score cannot be reproduced.")


def _authenticate_stage(
    root: Path,
    path: str | Path,
    *,
    state: str,
    authorization_commit: str,
) -> dict[str, Any]:
    stage_path = _inside(root, path, label=state)
    payload = _read_committed(stage_path, label=state)
    audit = _require_mapping(payload.get("audit"), label=f"{state} audit")
    artifacts = payload.get("artifacts")
    values_marker = _authenticate_values_marker(
        root,
        {
            "values_opened_marker": VALUES_OPENED_PATH.as_posix(),
            "commit_sha256": authorization_commit,
            "source_predictors_completion_commit_sha256": payload.get(
                "source_predictors_completion_commit_sha256"
            ),
        },
    )
    if (
        payload.get("state") != state
        or payload.get("authorization_commit_sha256") != authorization_commit
        or payload.get("m3_protocol_lock_commit_sha256") != EXPECTED_PROTOCOL_COMMIT
        or payload.get("source_qa_candidates_completion_commit_sha256")
        != EXPECTED_QA_COMPLETION_COMMIT
        or values_marker.get("authorization_commit_sha256") != authorization_commit
        or payload.get("values_opened_commit_sha256") != values_marker.get("commit_sha256")
        or tuple(payload.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or not isinstance(artifacts, list)
        or not artifacts
        or audit.get("blind_test_city_accessed") is not False
        or audit.get("blind_predictor_accessed") is not False
        or audit.get("network_or_href_reads") != 0
    ):
        raise M3SourceJointLosoError(f"{state} does not bind the authorized source-only run.")
    artifact_paths: dict[str, Path] = {}
    artifact_records: dict[str, Mapping[str, Any]] = {}
    for record in artifacts:
        if not isinstance(record, Mapping):
            raise M3SourceJointLosoError(f"{state} has an invalid artifact record.")
        role = str(record.get("role", ""))
        path = _inside(root, str(record.get("path", "")), label=f"{state} artifact")
        if (
            not path.is_file()
            or path.stat().st_size != int(record.get("bytes", -1))
            or sha256_file(path) != record.get("sha256")
        ):
            raise M3SourceJointLosoError(f"{state} artifact bytes changed: {path}")
        if not role or role in artifact_paths:
            raise M3SourceJointLosoError(f"{state} artifact roles are missing or duplicated.")
        artifact_paths[role] = path
        artifact_records[role] = record
    if state == "joint_nested_loso_stage_complete":
        joint_id = str(payload.get("selected_joint_candidate_id", ""))
        configuration = {item.joint_candidate_id: item for item in JOINT_CONFIGURATIONS}.get(
            joint_id
        )
        if (
            configuration is None
            or payload.get("selected_qa_id") != configuration.qa_id
            or payload.get("selected_m3_candidate_id") != configuration.m3_candidate.candidate_id
            or payload.get("configuration_count") != 16
            or payload.get("outer_fold_count") != 4
            or tuple(payload.get("final_refit_source_city_ids", ())) != SOURCE_CITY_IDS
            or payload.get("final_source_selection_complete") is not False
        ):
            raise M3SourceJointLosoError("Joint stage selection contract changed.")
        _authenticate_joint_stage_tables(root, payload, artifact_paths, artifact_records)
    elif state == "source_uq_selection_complete":
        method = str(payload.get("selected_method", ""))
        if (
            tuple(payload.get("outer_held_source_city_ids", ())) != SOURCE_CITY_IDS
            or not (method == "unweighted_cross_conformal" or method.startswith("density_ratio_"))
            or payload.get("fallback_used") is not (method == "unweighted_cross_conformal")
            or payload.get("fallback_used") is True
            and not payload.get("fallback_reason")
        ):
            raise M3SourceJointLosoError("Source UQ stage selection contract changed.")
    elif state == "source_risk_selection_complete":
        method = str(payload.get("selected_method", ""))
        if (
            tuple(payload.get("outer_held_source_city_ids", ())) != SOURCE_CITY_IDS
            or method
            not in {
                "learned_error",
                "interval_width",
                "ensemble_sd",
                "none_accept_all",
            }
            or payload.get("fallback_used") is not (method == "none_accept_all")
            or payload.get("fallback_used") is True
            and not payload.get("fallback_reason")
        ):
            raise M3SourceJointLosoError("Source risk stage selection contract changed.")
    return payload


def build_source_nested_loso_completion(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    joint_stage_path: str | Path = JOINT_STAGE_PATH,
    uq_stage_path: str | Path = UQ_STAGE_PATH,
    risk_stage_path: str | Path = RISK_STAGE_PATH,
) -> dict[str, Any]:
    """Fail closed until executable source-only UQ and risk stages are added.

    The parameters are retained as the versioned runner interface, but a
    committed joint-model artifact alone is not a complete M3 source
    selection.  A later append-only implementation must replace this guard
    only together with reproducible pseudo-test UQ/risk runners and semantic
    stage authentication.
    """

    del (
        project_root,
        authorization_path,
        joint_stage_path,
        uq_stage_path,
        risk_stage_path,
    )
    raise M3SourceJointLosoError(
        "SOURCE_NESTED_LOSO_COMPLETE is disabled until executable source-only "
        "UQ and risk pseudo-test stages are implemented and authenticated."
    )


def create_source_nested_loso_completion(
    project_root: str | Path,
    completion_path: str | Path = COMPLETION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_source_nested_loso_completion(root)
    destination = _inside(root, completion_path, label="source nested LOSO completion")
    _write_exclusive(payload, destination)
    return authenticate_source_nested_loso_completion(root, destination)


def authenticate_source_nested_loso_completion(
    project_root: str | Path,
    completion_path: str | Path = COMPLETION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, completion_path, label="source nested LOSO completion")
    observed = _read_committed(destination, label="source nested LOSO completion")
    expected = build_source_nested_loso_completion(root)
    if observed != expected:
        raise M3SourceJointLosoError("Source nested LOSO completion drifted.")
    return observed
