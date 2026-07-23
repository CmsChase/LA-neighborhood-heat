from __future__ import annotations

from pathlib import Path

import pytest

from la_heat.development_report import (
    DevelopmentReportConfig,
    _reject_final_test,
    _render_report,
    load_development_report_config,
)
from la_heat.robustness_reconciliation import AuthenticatedSource

ROOT = Path(__file__).resolve().parents[1]


def _source(
    name: str,
    *,
    json_outputs=None,
    outputs=None,
) -> AuthenticatedSource:
    return AuthenticatedSource(
        name=name,
        provenance_path=Path(f"{name}.json"),
        provenance_file_sha256="a" * 64,
        provenance_commit_sha256="b" * 64,
        provenance={},
        outputs=outputs or {},
        json_outputs=json_outputs or {},
        csv_outputs={},
        compile_commit_sha256="c" * 64,
        oof_predictions_sha256="d" * 64,
    )


def test_production_config_keeps_2025_locked() -> None:
    config = load_development_report_config(ROOT / "configs/development_report.toml")

    assert config.development_years == (2020, 2021, 2022, 2023, 2024)
    assert config.final_test_year == 2025
    assert config.expected_rows == 63_403
    assert config.expected_dates == 65
    assert config.expected_blocks == 71


def test_recursive_final_test_unlock_is_rejected() -> None:
    with pytest.raises(PermissionError, match="unlock"):
        _reject_final_test({"nested": {"final_test_unlocked": True}})


def test_report_narrative_preserves_result_and_interpretation_boundaries(
    tmp_path: Path,
) -> None:
    images = {}
    for name in (
        "joint_performance_overview.png",
        "qa_cohort_improvement_forest.png",
        "worst_date_errors.png",
        "fixed_date_lst_prediction_maps.png",
    ):
        path = tmp_path / name
        path.write_bytes(b"png")
        images[name] = path
    residual_map = tmp_path / "joint_m2_b1_residual_diagnostics_map.png"
    residual_map.write_bytes(b"png")
    initial = {
        "primary_comparison": {
            "tract_date_row_count": 63_403,
            "independent_date_count": 65,
            "independent_spatial_block_count": 71,
            "baseline_point_mae_c": 2.5,
            "target_model_point_mae_c": 2.1,
            "absolute_mae_improvement_c": 0.4,
            "relative_mae_improvement_percent": 16.0,
            "relative_mae_improvement_ci_lower_percent": 4.0,
            "relative_mae_improvement_ci_upper_percent": 28.0,
            "probability_improvement_gt_zero": 0.995,
        },
        "protocol_success_gates": {"observed_median_per_date_spearman": 0.79},
    }
    endpoint = {
        "relative_endpoint": {
            "gated_independent_date_count": 34,
            "focus_joint_models": {
                "B1": {
                    "mean_per_date_average_precision": 0.40,
                    "mean_per_date_recall_at_k": 0.42,
                },
                "M2": {
                    "mean_per_date_average_precision": 0.67,
                    "mean_per_date_recall_at_k": 0.61,
                },
            },
        }
    }
    qa = {"qa_interpretation": {"st_qa_2k_cohort_is_tract_summary_filter": True}}
    sources = {
        "initial_results": _source(
            "initial_results",
            json_outputs={"model_results_initial_summary.json": initial},
        ),
        "endpoint": _source(
            "endpoint",
            json_outputs={"model_endpoint_diagnostics_summary.json": endpoint},
        ),
        "qa": _source(
            "qa", json_outputs={"model_qa_diagnostics_summary.json": qa}
        ),
        "diagnostic_figures": _source(
            "diagnostic_figures",
            json_outputs={"model_diagnostic_figures_summary.json": {}},
            outputs=images,
        ),
        "residual_spatial": _source(
            "residual_spatial",
            outputs={"joint_m2_b1_residual_diagnostics_map.png": residual_map},
        ),
    }
    config = DevelopmentReportConfig(
        path=tmp_path / "config.toml",
        semantic_sha256="e" * 64,
        paths={"output_report": tmp_path / "report.md"},
        final_test_year=2025,
        development_years=(2020, 2021, 2022, 2023, 2024),
        expected_rows=63_403,
        expected_dates=65,
        expected_blocks=71,
        expected_relative_dates=34,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        prediction_origin="00:00:00",
        latest_dynamic_offset_days=-1,
    )
    reconciliation = {
        "strict_pixel_stqa2_sensitivity": {
            "strict_usable_date_count": 15,
            "minimum_required_usable_date_count": 30,
            "frozen_primary_oof_sensitivity_estimable": True,
            "frozen_primary_oof_bootstrap": {
                "independent_date_count": 15,
                "relative_mae_improvement_percent": 18.0,
                "relative_mae_improvement_ci_lower_percent": -5.0,
                "relative_mae_improvement_ci_upper_percent": 40.0,
            },
        },
        "feature_ablation_joint_comparisons": [
            {
                "reduced_feature_set_description": "calendar + weather",
                "reduced_date_macro_mae_c": 2.6,
                "all_features_date_macro_mae_c": 2.1,
                "relative_mae_improvement_fraction": 0.18,
                "relative_mae_improvement_ci_lower_fraction": 0.10,
                "relative_mae_improvement_ci_upper_fraction": 0.26,
            }
        ],
        "residual_spatial": {
            "B1": {"mean_morans_i_across_dates": 0.64},
            "M2": {"mean_morans_i_across_dates": 0.57},
        },
    }

    report = _render_report(config, sources, reconciliation)

    normalized = " ".join(report.split())
    assert "2025 remains untouched and locked" in normalized
    assert "historical hindcast" in normalized
    assert "predictive association" in normalized
    assert "not air temperature" in normalized
    assert "15 passed" in report
    assert "below the required 30" in report
