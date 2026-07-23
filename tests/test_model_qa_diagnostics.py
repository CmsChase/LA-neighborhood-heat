from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.model_qa_diagnostics import (
    ModelQADiagnosticConfig,
    ModelQADiagnosticError,
    build_failure_case_tables,
    build_qa_cohort_bootstrap,
    build_qa_cohort_improvement,
    build_qa_cohort_metrics,
    build_qa_cohorts,
    load_model_qa_diagnostic_config,
    validate_development_diagnostic_frame,
)

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, *, rows: int = 4, dates: int = 2, blocks: int = 2):
    placeholder = tmp_path / "input"
    return ModelQADiagnosticConfig(
        path=ROOT / "configs/model_qa_diagnostics.toml",
        semantic_sha256="a" * 64,
        result_analysis_config=placeholder,
        target_build_progress=placeholder,
        model_ready_targets=placeholder,
        date_summary=placeholder,
        model_dataset_provenance=placeholder,
        model_table=placeholder,
        target_inventory_summary=placeholder,
        scene_inventory=placeholder,
        output_directory=tmp_path / "output",
        final_test_year=2025,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        expected_rows=rows,
        expected_dates=dates,
        expected_blocks=blocks,
        st_uncertainty_threshold_k=2.0,
        low_scene_cloud_threshold_percent=15.0,
        valid_fraction_breaks=(0.60, 0.70, 0.80, 0.90, 1.0000001),
        cloud_distance_breaks_km=(1.0, 2.0, 5.0, 1_000_000.0),
        failure_case_limit=2,
        sentinel_feature_names=(
            "sentinel_ndvi_lag60",
            "sentinel_evi_lag60",
            "sentinel_ndwi_lag60",
            "sentinel_ndbi_lag60",
            "sentinel_albedo_proxy_lag60",
        ),
        bootstrap_method="crossed_date_spatial_block",
        bootstrap_sampling_unit="complete_clusters_only",
        bootstrap_seed=123,
        bootstrap_replicates=200,
        bootstrap_confidence_level=0.95,
    )


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "c", "d"],
            "target_date": pd.to_datetime(
                ["2023-06-01", "2024-06-01", "2024-06-01", "2024-06-01"]
            ),
            "spatial_block": ["x", "x", "y", "y"],
            "y_true": [0.0, 0.0, 1.0, 2.0],
            "b1_y_pred": [4.0, 2.0, 3.0, 4.0],
            "m2_y_pred": [10.0, 0.0, 1.0, 2.0],
            "platform": ["landsat-8", "landsat-9", "landsat-9", "landsat-9"],
            "valid_fraction": [0.60, 0.70, 0.80, 1.0],
            "median_st_uncertainty_k": [1.0, 2.0, 2.1, 3.0],
            "median_cloud_distance_km": [1.0, 2.0, 5.0, 10.0],
            "relative_hotspot_top20": [True, False, pd.NA, pd.NA],
            "retained_tract_fraction": [0.9, 0.8, 0.8, 0.8],
            "relative_endpoint_coverage_pass": [True, False, False, False],
            "sentinel_availability": [
                "complete",
                "complete",
                "all_five_missing",
                "complete",
            ],
            "low_scene_cloud_cohort": [
                "any_scene_cloud_lt_15pct",
                "no_scene_cloud_lt_15pct",
                "no_scene_cloud_lt_15pct",
                "no_scene_cloud_lt_15pct",
            ],
            "relative_endpoint_cohort": [
                "relative_label_available",
                "relative_label_available",
                "relative_label_unavailable",
                "relative_label_unavailable",
            ],
            "st_qa_2k_cohort": [
                "tract_median_le_2k",
                "tract_median_le_2k",
                "tract_median_gt_2k",
                "tract_median_gt_2k",
            ],
            "b1_residual_c": [4.0, 2.0, 2.0, 2.0],
            "m2_residual_c": [10.0, 0.0, 0.0, 0.0],
        }
    )


def test_production_config_freezes_2025_and_qa_thresholds() -> None:
    config = load_model_qa_diagnostic_config(ROOT / "configs/model_qa_diagnostics.toml")

    assert config.final_test_year == 2025
    assert config.st_uncertainty_threshold_k == 2.0
    assert config.low_scene_cloud_threshold_percent == 15.0
    assert config.expected_rows == 63_403
    assert config.bootstrap_replicates == 5_000


def test_diagnostic_frame_rejects_2025_and_nonfinite_scores(tmp_path: Path) -> None:
    config = _config(tmp_path)
    frame = _frame()
    validate_development_diagnostic_frame(frame, config)

    locked = frame.copy()
    locked.loc[0, "target_date"] = pd.Timestamp("2025-06-01")
    with pytest.raises(ModelQADiagnosticError, match="final-test lock"):
        validate_development_diagnostic_frame(locked, config)

    nonfinite = frame.copy()
    nonfinite.loc[0, "m2_y_pred"] = np.inf
    with pytest.raises(ModelQADiagnosticError, match="score"):
        validate_development_diagnostic_frame(nonfinite, config)


def test_frozen_cohorts_cover_edges_without_data_derived_bins(tmp_path: Path) -> None:
    config = _config(tmp_path)
    memberships = build_qa_cohorts(_frame(), config)

    valid = memberships.loc[memberships["cohort_dimension"].eq("valid_fraction")]
    assert valid["cohort_label"].tolist() == [
        "[0.60,0.70)",
        "[0.70,0.80)",
        "[0.80,0.90)",
        "[0.90,1.00]",
    ]
    cloud = memberships.loc[
        memberships["cohort_dimension"].eq("median_cloud_distance_km")
    ]
    assert cloud["cohort_label"].tolist() == [
        "[1.0,2.0)",
        "[2.0,5.0)",
        "[5.0,inf)",
        "[5.0,inf)",
    ]


def test_primary_cohort_metric_equal_weights_dates_not_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    metrics = build_qa_cohort_metrics(build_qa_cohorts(_frame(), config), config)
    all_m2 = metrics.loc[
        metrics["cohort_dimension"].eq("all") & metrics["model_id"].eq("M2")
    ].iloc[0]

    assert all_m2["primary_equal_date_weighted_mae_c"] == pytest.approx(5.0)
    assert all_m2["pooled_rmse_c"] == pytest.approx(5.0)
    assert all_m2["tract_date_row_count"] == 4
    assert all_m2["independent_date_count"] == 2

    improvement = build_qa_cohort_improvement(metrics, config)
    all_rows = improvement.loc[improvement["cohort_dimension"].eq("all")].iloc[0]
    assert all_rows["baseline_primary_mae_c"] == pytest.approx(3.0)
    assert all_rows["target_primary_mae_c"] == pytest.approx(5.0)
    assert all_rows["relative_mae_improvement_fraction"] == pytest.approx(-2.0 / 3.0)


def test_failure_cases_are_ranked_by_m2_error_and_keep_date_counts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    dates, tracts = build_failure_case_tables(_frame(), config)

    assert dates.iloc[0]["target_date"] == pd.Timestamp("2023-06-01")
    assert dates.iloc[0]["tract_date_row_count"] == 1
    assert tracts.iloc[0]["tract_geoid"] == "a"
    assert len(dates) == config.failure_case_limit
    assert len(tracts) == config.failure_case_limit


def test_cohort_bootstrap_uses_complete_dates_and_blocks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    bootstrap = build_qa_cohort_bootstrap(build_qa_cohorts(_frame(), config), config)
    all_rows = bootstrap.loc[bootstrap["cohort_dimension"].eq("all")].iloc[0]

    assert all_rows["bootstrap_method"] == "crossed_date_spatial_block"
    assert all_rows["bootstrap_sampling_unit"] == "complete_clusters_only"
    assert not bool(all_rows["random_row_sampling_used"])
    assert all_rows["independent_date_count"] == 2
    assert all_rows["independent_spatial_block_count"] == 2
