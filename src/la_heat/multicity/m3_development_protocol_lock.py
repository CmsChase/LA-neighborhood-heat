"""Append-only, value-blind protocol lock for M3 source-only development.

The lock freezes the development candidate space and deterministic selection
contract, not a selected model.  It reads only configuration, committed JSON
evidence, and source-code bytes.  It never discovers or opens predictor or
target tables.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_development as core
from la_heat.multicity.next_experiment_feasibility import (
    authenticate_feasibility_audit,
)
from la_heat.provenance import canonical_sha256, sha256_file

CONFIG_PATH: Final = Path("configs/multicity/m3_development_protocol_v1.toml")
CORE_PATH: Final = Path("src/la_heat/multicity/m3_development.py")
MODULE_PATH: Final = Path("src/la_heat/multicity/m3_development_protocol_lock.py")
SCRIPT_PATH: Final = Path("scripts/lock_multicity_m3_development_protocol.py")
LOCK_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json"
)
POSTHOC_QA_PATH: Final = Path(
    "reports/tables/multicity_external_posthoc_qa/posthoc_qa_summary.json"
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
HISTORICAL_STATES: Final = {
    "previous_multicity_protocol": "locked_before_source_targets_and_real_fit",
    "previous_multicity_model_fit": "model_fit_complete_external_predictions_committed",
    "previous_la_source_targets": "la_source_targets_complete",
    "previous_external_targets": "three_city_external_targets_complete",
    "previous_external_evaluation": "external_evaluation_complete",
    "previous_atlas_release": "authenticated_multicity_atlas_release",
    "phase1_model_lock": "frozen_for_one_time_2025_evaluation",
    "phase1_evaluation": "complete_one_time_final_evaluation",
}


class M3DevelopmentProtocolLockError(RuntimeError):
    """Raised when a proposed lock is non-reproducible or semantically unsafe."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3DevelopmentProtocolLockError(f"{label} must stay inside the project")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3DevelopmentProtocolLockError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise M3DevelopmentProtocolLockError(f"{label} must be a JSON object")
    return value


def _authenticate_commit(payload: Mapping[str, Any], *, label: str) -> str:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise M3DevelopmentProtocolLockError(f"{label} commit is invalid")
    return recorded


def _file_record(root: Path, value: str | Path, *, label: str) -> dict[str, Any]:
    path = _inside(root, value, label=label)
    if not path.is_file():
        raise M3DevelopmentProtocolLockError(f"{label} is unavailable: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _committed_record(
    root: Path,
    value: str | Path,
    *,
    label: str,
    expected_state: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _file_record(root, value, label=label)
    payload = _read_json(root / record["path"], label=label)
    commit = _authenticate_commit(payload, label=label)
    if expected_state is not None and payload.get("state") != expected_state:
        raise M3DevelopmentProtocolLockError(f"{label} state changed")
    return payload, {**record, "commit_sha256": commit}


def _load_config(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _file_record(root, CONFIG_PATH, label="M3 development configuration")
    try:
        config = tomllib.loads((root / record["path"]).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise M3DevelopmentProtocolLockError(
            "M3 development configuration is invalid"
        ) from error
    if not isinstance(config, dict):
        raise M3DevelopmentProtocolLockError("M3 configuration must be a table")
    return config, record


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M3DevelopmentProtocolLockError(message)


def _validate_features(config: Mapping[str, Any]) -> None:
    features = config.get("features", {})
    old_23 = tuple(features.get("old_23_dynamic", {}).get("names", ()))
    old_18 = tuple(features.get("old_18_static", {}).get("names", ()))
    old_5 = tuple(features.get("old_5_sentinel", {}).get("names", ()))
    _require(old_23 == core.B1_FEATURES, "B1 feature order differs from M3 core")
    _require(old_18 == core.STATIC_FEATURES, "Static feature order differs from M3 core")
    _require(old_5 == core.SENTINEL_FEATURES, "Sentinel feature order differs from M3 core")
    _require(
        (*old_18, *old_23, *old_5) == core.M2_FEATURES,
        "Legacy M2 feature order differs from M3 core",
    )
    _require(
        int(features.get("old_23_dynamic_feature_count", -1)) == 23
        and int(features.get("old_46_portable_feature_count", -1)) == 46,
        "Frozen feature counts changed",
    )
    for key in (
        "forbid_city_id_as_predictor",
        "forbid_tract_geoid_as_predictor",
        "forbid_landsat_thermal_or_target_qa_as_predictor",
        "forbid_same_day_or_future_dynamic_predictors",
    ):
        _require(features.get(key) is True, f"features.{key} must remain true")
    _require(
        features.get("latest_dynamic_offset_days") == -1,
        "Future or same-day dynamic predictors cannot be enabled",
    )


def _validate_estimators(config: Mapping[str, Any]) -> None:
    models = config.get("models", {})
    shared = models.get("shared", {})
    b1 = models.get("b1", {})
    m2 = models.get("m2_legacy", {})
    m3 = models.get("m3", {})
    configured = tuple(
        (
            str(row.get("candidate_id")),
            float(row.get("level_alpha")),
            int(row.get("anomaly_max_leaf_nodes")),
        )
        for row in m3.get("candidates", ())
        if isinstance(row, dict)
    )
    implemented = tuple(
        (candidate.candidate_id, candidate.level_alpha, candidate.anomaly_max_leaf_nodes)
        for candidate in core.M3_CANDIDATES
    )
    _require(
        len(configured) == 4 and configured == implemented,
        "M3 candidate set/order differs from core",
    )
    _require(m3.get("candidate_count") == 4, "M3 candidate count must be four")

    b1_model = core.build_b1_estimator().named_steps["model"]
    m2_model = core.build_m2_legacy_estimator().named_steps["model"]
    _require(
        b1.get("estimator") == "Ridge"
        and float(b1.get("alpha")) == float(b1_model.alpha)
        and bool(b1.get("fit_intercept")) is bool(b1_model.fit_intercept)
        and b1.get("solver") == b1_model.solver
        and float(b1.get("tolerance")) == float(b1_model.tol),
        "B1 configuration differs from core",
    )
    _require(
        m2.get("estimator") == "HistGradientBoostingRegressor"
        and m2.get("loss") == m2_model.loss
        and float(m2.get("learning_rate")) == float(m2_model.learning_rate)
        and int(m2.get("max_iter")) == int(m2_model.max_iter)
        and int(m2.get("max_leaf_nodes")) == int(m2_model.max_leaf_nodes)
        and int(m2.get("min_samples_leaf")) == int(m2_model.min_samples_leaf)
        and float(m2.get("l2_regularization")) == float(m2_model.l2_regularization)
        and bool(m2.get("early_stopping")) is bool(m2_model.early_stopping)
        and int(m2.get("random_state")) == int(m2_model.random_state),
        "M2-L configuration differs from core",
    )
    _require(
        int(shared.get("random_state")) == int(core.MODEL_SEED),
        "M3 shared random seed differs from core",
    )
    level_names = tuple(m3.get("level", {}).get("additional_feature_names", ()))
    _require(
        (*core.B1_FEATURES, *level_names) == core.LEVEL_FEATURES,
        "M3 level feature order differs from core",
    )
    _require(
        tuple(core.ANOMALY_FEATURES) == (*core.STATIC_FEATURES, *core.SENTINEL_FEATURES),
        "M3 anomaly feature order is invalid",
    )


def _validate_qa_support_selection(config: Mapping[str, Any]) -> None:
    qa = config.get("target_qa", {})
    support = config.get("target_support", {})
    selection = config.get("selection", {})
    candidates = qa.get("st_qa_candidates", ())
    normalized = tuple(
        (
            row.get("id"),
            row.get("apply_threshold"),
            row.get("maximum_kelvin"),
            row.get("leniency_rank"),
        )
        for row in candidates
        if isinstance(row, dict)
    )
    _require(
        normalized
        == (
            ("none", False, None, 4),
            ("3k", True, 3.0, 1),
            ("4k", True, 4.0, 2),
            ("6k", True, 6.0, 3),
        ),
        "ST_QA candidate set/order changed",
    )
    _require(
        qa.get("threshold_is_pixel_level") is True
        and qa.get("threshold_is_not_tract_summary_filter") is True
        and float(qa.get("st_qa_scale_kelvin")) == 0.01
        and qa.get("threshold_comparison")
        == "st_qa_kelvin_less_than_or_equal_to_candidate"
        and qa.get("rebuild_mosaic_and_tract_date_targets_for_every_candidate") is True
        and qa.get("never_inherit_date_usable_from_another_candidate") is True
        and qa.get("excluded_qa_pixel_bits") == [0, 1, 2, 3, 4, 5, 7]
        and qa.get("minimum_st_dn") == 293
        and qa.get("maximum_st_dn") == 61_440
        and qa.get("require_st_qa_not_fill") is True
        and qa.get("require_st_cdist_not_fill") is True
        and float(qa.get("minimum_cloud_distance_km")) == 1.0
        and qa.get("exclude_terrain_occlusion") is True,
        "Pixel-level target QA contract changed",
    )
    _require(
        support.get("worldcover_excluded_classes") == [0, 80]
        and float(support.get("minimum_tract_footprint_fraction")) == 0.90
        and support.get("minimum_valid_pixels_per_tract") == 20
        and float(support.get("minimum_valid_pixel_fraction")) == 0.60
        and float(support.get("minimum_city_union_coverage_fraction")) == 0.98
        and float(support.get("minimum_date_tract_retention_fraction")) == 0.50,
        "Target-support thresholds changed",
    )
    tie = selection.get("tie_break", {})
    _require(
        selection.get("model_and_st_qa_selected_jointly") is True
        and selection.get("joint_configuration_count") == 16
        and selection.get(
            "forbid_rows_or_dates_from_held_out_city_during_fit_imputation_and_selection"
        )
        is True
        and selection.get("primary_objective")
        == "minimum_equal_city_equal_date_m3_absolute_lst_mae"
        and selection.get("objective_rounding_decimal_places") == 12
        and selection.get("minimum_usable_dates_per_inner_validation_city") == 8
        and selection.get("minimum_total_usable_inner_validation_city_dates") == 30
        and selection.get("ineligible_if_support_gate_fails") is True
        and tie.get("order")
        == [
            "higher_minimum_usable_dates_across_inner_validation_cities",
            "higher_total_usable_city_dates",
            "higher_overall_tract_date_retention",
            "more_lenient_st_qa_none_then_6k_then_4k_then_3k",
            "lower_m3_complexity_rank",
            "candidate_id_lexicographic_ascending",
        ]
        and float(tie.get("exact_score_tolerance")) == 1e-12,
        "Joint QA/model selection or tie-break contract changed",
    )


def _validate_uncertainty_evaluation(config: Mapping[str, Any]) -> None:
    uncertainty = config.get("uncertainty", {})
    shift = uncertainty.get("covariate_shift", {})
    stability = uncertainty.get("stability", {})
    risk = uncertainty.get("risk", {})
    outputs = config.get("outputs", {})
    configured_access = config.get("access_audit", {})
    audit = config.get("audit", {})
    evaluation = config.get("evaluation", {})
    reliability = evaluation.get("reliability_gates", {})
    _require(
        float(uncertainty.get("nominal_coverage")) == 0.90
        and uncertainty.get("fallback_method") == "unweighted_cross_conformal"
        and uncertainty.get("source_residuals")
        == "nested_whole_city_out_of_fold_absolute_residuals_only"
        and uncertainty.get("point_model") == "selected_m3"
        and uncertainty.get("nonconformity_score") == "absolute_y_minus_m3_prediction_c"
        and uncertainty.get("finite_sample_correction")
        == "weighted_quantile_with_one_explicit_test_atom_at_infinity_of_weight_1",
        "Cross-conformal uncertainty contract changed",
    )
    _require(
        shift.get("uses_only_unlabeled_predictor_rows_from_each_blind_test_city") is True
        and shift.get("cross_fit_unit") == "city_date"
        and shift.get("cross_fit_folds") == 5
        and shift.get("classifier_random_state") == core.DENSITY_SEED
        and shift.get("feature_contract") == "old_46_portable_features"
        and shift.get("density_ratio")
        == "p_test_divided_by_one_minus_p_test_under_equal_classifier_priors"
        and float(shift.get("ratio_clip_lower")) == 0.20
        and float(shift.get("ratio_clip_upper")) == 5.0
        and shift.get("normalize_by_mean_clipped_source_ratio") is True
        and shift.get("apply_separately_per_blind_test_city") is True,
        "Covariate-shift contract changed",
    )
    _require(
        stability.get("require_all_values_finite") is True
        and float(stability.get("domain_classifier_roc_auc_lower")) == 0.50
        and float(stability.get("domain_classifier_roc_auc_upper")) == 0.95
        and float(stability.get("minimum_row_effective_sample_size_fraction")) == 0.25
        and float(stability.get("minimum_city_date_effective_sample_size_fraction")) == 0.50
        and float(stability.get("minimum_each_source_city_weight_share")) == 0.05
        and float(stability.get("maximum_source_ratio_clip_fraction")) == 0.20
        and float(stability.get("maximum_test_ratio_clip_fraction")) == 0.20
        and float(stability.get("source_oof_pooled_coverage_lower")) == 0.85
        and float(stability.get("source_oof_pooled_coverage_upper")) == 0.95
        and float(stability.get("minimum_source_oof_per_city_coverage")) == 0.80
        and float(stability.get("minimum_weighted_wis_improvement_over_unweighted")) == 0.02
        and stability.get("all_gates_required") is True
        and stability.get("fallback_result") == "unweighted_cross_conformal",
        "Density-ratio stability or fallback contract changed",
    )
    _require(
        risk.get("candidate_methods") == ["learned_error", "interval_width", "ensemble_sd"]
        and risk.get("learned_error_random_state") == core.RISK_SEED
        and float(risk.get("accepted_fraction_within_each_city_date")) == 0.80
        and float(risk.get("minimum_equal_city_accepted_mae_improvement")) == 0.10
        and float(risk.get("minimum_retention")) == 0.60
        and risk.get("require_no_source_city_degradation") is True
        and risk.get("fallback_result") == "none_accept_all",
        "Risk-ranking selection or fallback contract changed",
    )
    domain = core.build_domain_classifier()
    domain_imputer = domain.named_steps["impute"]
    domain_model = domain.named_steps["model"]
    _require(
        shift.get("classifier") == type(domain_model).__name__
        and shift.get("classifier_penalty") == domain_model.penalty
        and float(shift.get("classifier_c")) == float(domain_model.C)
        and shift.get("classifier_solver") == domain_model.solver
        and int(shift.get("classifier_max_iter")) == int(domain_model.max_iter)
        and shift.get("classifier_class_weight") == "none"
        and domain_model.class_weight is None
        and shift.get("classifier_role")
        == "estimate_equal_prior_probability_that_a_row_is_from_the_blind_test_city"
        and shift.get("domain_class_prior")
        == "equal_source_and_blind_test_total_weight"
        and shift.get("domain_sample_weighting")
        == "each_domain_total_weight_0_5_then_equal_city_then_equal_date_then_equal_rows"
        and shift.get("preprocess_imputer") == type(domain_imputer).__name__
        and shift.get("preprocess_imputer_strategy") == domain_imputer.strategy
        and shift.get("preprocess_imputer_add_indicator") is domain_imputer.add_indicator
        and shift.get("preprocess_scaler") == type(domain.named_steps["scale"]).__name__,
        "Exact domain-classifier pipeline differs from core",
    )
    risk_model = core.build_risk_estimator()
    risk_imputer = risk_model.named_steps["impute"]
    risk_estimator = risk_model.named_steps["model"]
    _require(
        tuple(risk.get("learned_error_feature_order", ())) == core.RISK_FEATURES
        and risk.get("learned_error_estimator") == type(risk_estimator).__name__
        and risk.get("learned_error_preprocess_imputer") == type(risk_imputer).__name__
        and risk.get("learned_error_preprocess_imputer_strategy") == risk_imputer.strategy
        and risk.get("learned_error_preprocess_imputer_add_indicator")
        is risk_imputer.add_indicator
        and risk.get("learned_error_loss") == risk_estimator.loss
        and float(risk.get("learned_error_learning_rate"))
        == float(risk_estimator.learning_rate)
        and int(risk.get("learned_error_max_iter")) == int(risk_estimator.max_iter)
        and int(risk.get("learned_error_max_leaf_nodes"))
        == int(risk_estimator.max_leaf_nodes)
        and int(risk.get("learned_error_min_samples_leaf"))
        == int(risk_estimator.min_samples_leaf)
        and float(risk.get("learned_error_l2_regularization"))
        == float(risk_estimator.l2_regularization)
        and bool(risk.get("learned_error_early_stopping"))
        is bool(risk_estimator.early_stopping)
        and int(risk.get("learned_error_random_state"))
        == int(risk_estimator.random_state),
        "Exact learned-error pipeline differs from core",
    )
    _require(
        outputs.get("prediction_column_count") == len(core.PREDICTION_COLUMNS)
        and tuple(outputs.get("prediction_columns", ())) == core.PREDICTION_COLUMNS
        and outputs.get("planned_figure_ids")
        == [
            "blind_city_mae",
            "predicted_vs_observed",
            "error_by_city_date",
            "anomaly_performance",
            "interval_calibration",
            "risk_coverage",
            "spatial_error_maps",
            "source_loso_selection",
        ]
        and outputs.get("prediction_output_must_exclude_targets_and_target_qa") is True
        and outputs.get("prediction_output_order_is_exact") is True,
        "Prediction output or planned-figure contract differs from core",
    )
    _require(
        configured_access
        == {
            "predictor_tables_read_by_this_lock": False,
            "source_target_or_qa_values_read_by_this_lock": False,
            "blind_test_landsat_asset_hrefs_read_by_this_lock": False,
            "blind_test_thermal_or_target_qa_values_read_by_this_lock": False,
            "blind_test_target_tables_read_by_this_lock": False,
            "model_fit_prediction_or_scoring_performed_by_this_lock": False,
            "values_opened_marker_created_by_this_lock": False,
        }
        and audit
        == {
            "require_configuration_and_core_exact_contract_match": True,
            "require_authenticated_feasibility_and_historical_anchors": True,
            "require_separate_source_development_authorization": True,
            "require_authenticated_source_nested_loso_completion_commit": True,
            "require_blind_test_prediction_commit_before_target_authorization": True,
            "require_authenticated_target_authorization_before_values_opened": True,
            "require_append_only_committed_manifests": True,
            "require_sha256_row_date_and_spatial_block_counts": True,
            "forbid_metric_access_before_canonical_completion_authentication": True,
        },
        "Access/audit contract changed",
    )
    _require(
        evaluation.get("primary_comparison") == "M3_vs_B1"
        and evaluation.get("legacy_comparison") == "M2_legacy_vs_B1_secondary_only"
        and float(evaluation.get("minimum_relative_mae_improvement")) == 0.10
        and evaluation.get("require_crossed_bootstrap_ci_lower_above_zero") is True
        and evaluation.get("require_no_blind_test_city_point_degradation") is True
        and evaluation.get("minimum_total_usable_city_dates") == 40
        and evaluation.get("minimum_usable_dates_per_blind_test_city") == 8
        and evaluation.get("bootstrap_iterations") == 10_000
        and evaluation.get("bootstrap_method")
        == "city_stratified_crossed_complete_date_x_5km_spatial_block"
        and evaluation.get("bootstrap_seed") == core.BOOTSTRAP_SEED
        and float(evaluation.get("confidence_level")) == 0.95
        and evaluation.get("all_tables_require_row_date_and_spatial_block_counts") is True
        and reliability
        == {
            "overall_coverage_lower": 0.85,
            "overall_coverage_upper": 0.95,
            "minimum_per_city_coverage": 0.80,
            "minimum_per_city_retention": 0.60,
            "minimum_accepted_set_mae_improvement_vs_all_predictions": 0.10,
        },
        "Evaluation or reliability gates changed",
    )


def _validate_semantics(config: Mapping[str, Any]) -> None:
    protocol = config.get("protocol", {})
    cohorts = config.get("cohorts", {})
    permissions = config.get("permissions", {})
    selection = config.get("selection", {})
    blind = config.get("blind_test", {})
    seeds = config.get("seeds", {})
    _require(protocol.get("schema_version") == 1, "Unsupported M3 protocol schema")
    _require(
        protocol.get("id") == "multicity_m3_level_anomaly_development_v1"
        and protocol.get("status") == "development_protocol_not_locked"
        and protocol.get("protocol_locked") is False,
        "M3 descriptive protocol identity changed",
    )
    _require(
        protocol.get("target_is_air_temperature") is False
        and protocol.get("target_is_human_heat_risk") is False,
        "The target interpretation boundary changed",
    )
    _require(tuple(cohorts.get("source_city_ids", ())) == SOURCE_CITY_IDS, "Source cities changed")
    _require(
        tuple(cohorts.get("blind_test_city_ids", ())) == BLIND_TEST_CITY_IDS,
        "Blind-test cities changed",
    )
    _require(
        cohorts.get("previous_phoenix_houston_chicago_results_reuse_as_independent_test")
        is False,
        "Previously opened cities cannot become an independent test again",
    )
    _require(
        isinstance(permissions, dict)
        and permissions
        and all(value is False for value in permissions.values()),
        "Every execution permission must be false at lock creation",
    )
    _require(
        selection.get("method") == "nested_whole_city_leave_one_source_city_out"
        and selection.get("outer_fold_count") == 4
        and selection.get("outer_holdout_unit") == "one_complete_source_city"
        and selection.get("inner_holdout_unit")
        == "one_complete_city_from_the_three_outer_training_cities"
        and selection.get("outer_scores_never_select_final_configuration") is True,
        "Nested whole-city LOSO contract changed",
    )
    _require(
        blind.get("prediction_commit_required_before_target_authorization") is True
        and blind.get("one_indivisible_combined_target_claim") is True
        and blind.get("retuning_after_any_blind_test_target_or_qa_access") is False,
        "Blind-test access contract changed",
    )
    _require(
        seeds
        == {
            "model": core.MODEL_SEED,
            "density": core.DENSITY_SEED,
            "risk": core.RISK_SEED,
            "bootstrap": core.BOOTSTRAP_SEED,
        },
        "Frozen M3 development seeds differ from core",
    )
    _validate_features(config)
    _validate_estimators(config)
    _validate_qa_support_selection(config)
    _validate_uncertainty_evaluation(config)


def _feasibility_anchor(
    root: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    anchor = config.get("anchors", {}).get("feasibility", {})
    payload, record = _committed_record(
        root,
        str(anchor.get("path", "")),
        label="metadata-only feasibility audit",
        expected_state=str(anchor.get("required_state", "")),
    )
    authenticated = authenticate_feasibility_audit(root)
    _require(authenticated == payload, "Feasibility check-only authentication changed")
    _require(record["commit_sha256"] == anchor.get("commit_sha256"), "Feasibility commit changed")
    selection = payload.get("selection", {})
    access = payload.get("access_contract", {})
    _require(
        selection.get("decision") == anchor.get("required_decision")
        and selection.get("selected_city_ids") == list(BLIND_TEST_CITY_IDS)
        and selection.get("all_selected_cities_passed") is True,
        "Feasibility city decision changed",
    )
    prohibited = (
        "new_candidate_landsat_asset_hrefs_read",
        "new_candidate_thermal_values_read",
        "new_candidate_target_qa_values_read",
        "new_candidate_target_tables_read",
        "new_candidate_values_opened_marker_created",
        "predictor_construction_performed",
        "model_fit_or_prediction_performed",
        "evaluation_metrics_computed",
        "target_authorization_created",
    )
    _require(
        all(access.get(name) is False for name in prohibited),
        "Feasibility access boundary changed",
    )
    return payload, record


def _historical_anchors(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    historical = config.get("anchors", {}).get("historical", {})
    records: dict[str, Any] = {}
    for name, state in HISTORICAL_STATES.items():
        _payload, record = _committed_record(
            root,
            str(historical.get(name, "")),
            label=name,
            expected_state=state,
        )
        records[name] = record
    posthoc, posthoc_record = _committed_record(
        root,
        POSTHOC_QA_PATH,
        label="posthoc QA source evidence",
    )
    _require(
        posthoc.get("analysis_class") == "non_confirmatory_posthoc_read_only_qa"
        and posthoc.get("formal_result_unchanged") is True
        and posthoc.get("model_refit_or_recalibrated") is False
        and posthoc.get("formal_evaluation_outputs_modified") is False,
        "Posthoc QA evidence changed its non-confirmatory role",
    )
    records["posthoc_qa_source_evidence"] = posthoc_record
    return records


def build_m3_development_protocol_lock(project_root: str | Path) -> dict[str, Any]:
    """Build the deterministic lock without reading any predictor or target values."""

    root = Path(project_root).resolve()
    config, config_record = _load_config(root)
    _validate_semantics(config)
    _feasibility, feasibility_record = _feasibility_anchor(root, config)
    historical_records = _historical_anchors(root, config)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "multicity-m3-development-protocol-lock-v1",
        "state": "locked_before_new_source_analysis",
        "experiment_id": config["protocol"]["id"],
        "protocol_locked": True,
        "candidate_space_locked": True,
        "model_spec_locked": False,
        "selected_model_winner_locked": False,
        "research_question": config["protocol"]["research_question"],
        "target_contract": {
            key: value
            for key, value in config["protocol"].items()
            if key
            in {
                "analysis_unit",
                "target",
                "target_is_air_temperature",
                "target_is_human_heat_risk",
            }
        },
        "cohorts": config["cohorts"],
        "development_contract": {
            name: config[name]
            for name in (
                "seeds",
                "features",
                "models",
                "target_qa",
                "target_support",
                "selection",
                "uncertainty",
                "outputs",
                "evaluation",
                "blind_test",
                "audit",
            )
        },
        "permissions": config["permissions"],
        "input_anchors": {
            "feasibility": feasibility_record,
            "historical_source_evidence": historical_records,
        },
        "code_identity": {
            "configuration": config_record,
            "m3_development_core": _file_record(root, CORE_PATH, label="M3 development core"),
            "protocol_lock_module": _file_record(root, MODULE_PATH, label="Protocol lock module"),
            "protocol_lock_script": _file_record(root, SCRIPT_PATH, label="Protocol lock script"),
        },
        "access_audit": {
            "predictor_tables_read_by_this_lock": False,
            "source_target_or_qa_values_read_by_this_lock": False,
            "blind_test_landsat_asset_hrefs_read_by_this_lock": False,
            "blind_test_thermal_or_target_qa_values_read_by_this_lock": False,
            "blind_test_target_tables_read_by_this_lock": False,
            "model_fit_prediction_or_scoring_performed_by_this_lock": False,
            "values_opened_marker_created_by_this_lock": False,
        },
        "next_safe_stage": "separately_authorize_source_only_qa_rebuild_and_nested_loso",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3DevelopmentProtocolLockError(
            f"Append-only M3 development lock already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def authenticate_m3_development_protocol_lock(
    project_root: str | Path,
    lock_path: str | Path = LOCK_PATH,
) -> dict[str, Any]:
    """Rebuild the full value-blind payload and require byte-semantic equality."""

    root = Path(project_root).resolve()
    path = _inside(root, lock_path, label="M3 development protocol lock")
    observed = _read_json(path, label="M3 development protocol lock")
    _authenticate_commit(observed, label="M3 development protocol lock")
    expected = build_m3_development_protocol_lock(root)
    if observed != expected:
        raise M3DevelopmentProtocolLockError(
            "M3 development protocol lock no longer reproduces exactly"
        )
    return observed


def create_m3_development_protocol_lock(
    project_root: str | Path,
    lock_path: str | Path = LOCK_PATH,
) -> dict[str, Any]:
    """Create the lock once with O_EXCL, then immediately re-authenticate it."""

    root = Path(project_root).resolve()
    path = _inside(root, lock_path, label="M3 development protocol lock")
    payload = build_m3_development_protocol_lock(root)
    _write_exclusive(payload, path)
    return authenticate_m3_development_protocol_lock(root, path)
