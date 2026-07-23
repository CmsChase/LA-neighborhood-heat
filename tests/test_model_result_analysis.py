from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.model_result_analysis as analysis_module
from la_heat.model_result_analysis import (
    BOOTSTRAP_METHOD,
    BOOTSTRAP_SAMPLING_UNIT,
    FROZEN_BOOTSTRAP_REPLICATES,
    FROZEN_BOOTSTRAP_SEED,
    ModelResultAnalysisError,
    ResultAnalysisConfig,
    aggregate_paired_date_block_errors,
    authenticate_model_results,
    build_protocol_success_gates,
    crossed_date_spatial_block_bootstrap,
    load_result_analysis_config,
    select_strongest_legal_baseline,
)
from la_heat.model_run_compile import SUMMARY_METRIC_COLUMNS
from la_heat.model_selection import MODEL_IDS
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import canonical_sha256, parquet_file_record, sha256_file
from la_heat.validation_splits import FAMILIES

ROOT = Path(__file__).resolve().parents[1]


def _config(
    tmp_path: Path, *, rows: int = 4, dates: int = 2, blocks: int = 2
) -> ResultAnalysisConfig:
    return ResultAnalysisConfig(
        path=ROOT / "configs" / "result_analysis.toml",
        semantic_sha256="a" * 64,
        evaluation_directory=tmp_path / "evaluation",
        output_directory=tmp_path / "reports",
        final_test_year=2025,
        final_test_locked=True,
        target_family="joint",
        target_model_id="M2",
        legal_baseline_model_ids=("B0", "B1", "B2"),
        primary_metric_column="primary_equal_date_weighted_mae_c",
        spearman_metric_column="median_per_date_spearman",
        expected_independent_date_count=dates,
        expected_independent_spatial_block_count=blocks,
        expected_tract_date_row_count=rows,
        bootstrap_method=BOOTSTRAP_METHOD,
        bootstrap_sampling_unit=BOOTSTRAP_SAMPLING_UNIT,
        bootstrap_seed=123,
        bootstrap_replicates=200,
        confidence_level=0.95,
        minimum_relative_mae_improvement_fraction=0.10,
        minimum_median_per_date_spearman=0.50,
        uncertainty_relative_ci_lower_must_exceed=0.0,
    )


def _synthetic_oof(*, include_2025: bool = False) -> pd.DataFrame:
    dates = [pd.Timestamp("2023-06-01"), pd.Timestamp("2024-06-01")]
    if include_2025:
        dates[-1] = pd.Timestamp("2025-06-01")
    base_rows = [
        {
            "tract_geoid": f"tract-{date_index}-{block_index}",
            "target_date": target_date,
            "spatial_block": f"block-{block_index}",
            "y_true": float(30 + date_index + block_index),
        }
        for date_index, target_date in enumerate(dates)
        for block_index in range(2)
    ]
    offsets = {"B0": 3.0, "B1": 2.0, "B2": 2.5, "M1": 1.5, "M2": 1.0}
    rows: list[dict[str, object]] = []
    for family in FAMILIES:
        for model_id in MODEL_IDS:
            for base in base_rows:
                rows.append(
                    {
                        "tract_geoid": base["tract_geoid"],
                        "target_date": base["target_date"],
                        "spatial_block": base["spatial_block"],
                        "family": family,
                        "fold_id": f"{family}-fold",
                        "model_id": model_id,
                        "candidate_id": f"{model_id}-candidate",
                        "y_true": base["y_true"],
                        "y_pred": float(base["y_true"]) + offsets[model_id],
                    }
                )
    result = pd.DataFrame(rows, columns=list(OUTER_PREDICTION_COLUMNS))
    result["tract_geoid"] = result["tract_geoid"].astype("string")
    return result


def _synthetic_summary() -> pd.DataFrame:
    offsets = {"B0": 3.0, "B1": 2.0, "B2": 2.5, "M1": 1.5, "M2": 1.0}
    spearman = {"B0": 0.1, "B1": 0.3, "B2": 0.4, "M1": 0.6, "M2": 0.8}
    rows = []
    for family in FAMILIES:
        for model_id in MODEL_IDS:
            rows.append(
                {
                    "family": family,
                    "model_id": model_id,
                    "row_count": 4,
                    "independent_date_count": 2,
                    "independent_spatial_block_count": 2,
                    "primary_equal_date_weighted_mae_c": offsets[model_id],
                    "pooled_rmse_c": offsets[model_id],
                    "pooled_oos_r2": 0.5,
                    "pooled_mean_signed_error_c": offsets[model_id],
                    "equal_date_weighted_mean_signed_error_c": offsets[model_id],
                    "equal_date_weighted_within_date_anomaly_mae_c": 0.0,
                    "median_per_date_spearman": spearman[model_id],
                    "spearman_defined_date_count": 2,
                    "spearman_undefined_date_count": 0,
                }
            )
    return pd.DataFrame(rows, columns=list(SUMMARY_METRIC_COLUMNS))


def _write_authenticated_inputs(
    tmp_path: Path,
    *,
    include_2025: bool = False,
) -> tuple[ResultAnalysisConfig, Path]:
    config = _config(tmp_path)
    directory = config.evaluation_directory
    directory.mkdir(parents=True)
    oof_path = directory / "oof_predictions.parquet"
    summary_path = directory / "summary_metrics.csv"
    oof = _synthetic_oof(include_2025=include_2025)
    summary = _synthetic_summary()
    oof.to_parquet(oof_path, index=False)
    summary.to_csv(summary_path, index=False)
    round_trip_oof = pd.read_parquet(oof_path)
    oof_record = {
        "path": oof_path.name,
        "path_base": "output_directory",
        **parquet_file_record(oof_path, round_trip_oof),
    }
    summary_record = {
        "path": summary_path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(summary_path),
        "bytes": summary_path.stat().st_size,
        "rows": len(summary),
    }
    provenance = {
        "schema_version": 2,
        "algorithm_version": "grouped-model-oof-compile-v2",
        "state": "complete",
        "ready_for_reporting": True,
        "run_id": "b" * 64,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "context_row_count": 4,
        "independent_date_count": 2,
        "family_count": len(FAMILIES),
        "model_count": len(MODEL_IDS),
        "oof_prediction_row_count": len(oof),
        "summary_metric_row_count": len(summary),
        "output_files": {
            oof_path.name: oof_record,
            summary_path.name: summary_record,
        },
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    (directory / "model_run_compile_provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    return config, directory


def _constant_improvement_cells() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "target_date": date,
                "spatial_block": block,
                "row_count": count,
                "baseline_absolute_error_sum_c": 2.0 * count,
                "target_absolute_error_sum_c": 1.0 * count,
            }
            for date in pd.to_datetime(["2023-06-01", "2024-06-01", "2024-07-01"])
            for block, count in [("a", 1), ("b", 3)]
        ]
    )


def test_production_config_freezes_complete_cluster_bootstrap_and_canonical_input() -> None:
    config = load_result_analysis_config(ROOT / "configs" / "result_analysis.toml")

    assert config.evaluation_directory == (
        ROOT / "data" / "processed" / "model_evaluation"
    ).resolve()
    assert config.bootstrap_method == BOOTSTRAP_METHOD
    assert config.bootstrap_sampling_unit == BOOTSTRAP_SAMPLING_UNIT
    assert config.bootstrap_seed == FROZEN_BOOTSTRAP_SEED
    assert config.bootstrap_replicates == FROZEN_BOOTSTRAP_REPLICATES
    assert config.final_test_locked is True


def test_strongest_legal_baseline_uses_mae_then_frozen_baseline_order() -> None:
    summary = pd.DataFrame(
        {
            "family": ["joint", "joint", "joint"],
            "model_id": ["B0", "B1", "B2"],
            "primary_equal_date_weighted_mae_c": [2.0, 1.0, 1.0],
        }
    )

    selected = select_strongest_legal_baseline(summary, family="joint")

    assert selected == "B1"


def test_paired_errors_are_aggregated_once_per_date_block_before_bootstrap() -> None:
    oof = _synthetic_oof().sample(frac=1.0, random_state=42).reset_index(drop=True)

    cells = aggregate_paired_date_block_errors(
        oof,
        family="joint",
        target_model_id="M2",
        baseline_model_id="B1",
    )

    assert len(cells) == 4
    assert cells["row_count"].sum() == 4
    assert not cells.duplicated(["target_date", "spatial_block"]).any()
    assert np.allclose(cells["paired_absolute_mae_improvement_c"], 1.0)


def test_crossed_cluster_bootstrap_is_deterministic_and_paired() -> None:
    cells = _constant_improvement_cells()

    first = crossed_date_spatial_block_bootstrap(
        cells,
        seed=20260722,
        replicates=500,
        confidence_level=0.95,
    )
    second = crossed_date_spatial_block_bootstrap(
        cells,
        seed=20260722,
        replicates=500,
        confidence_level=0.95,
    )

    assert first == second
    assert first["paired_models_share_every_cluster_draw"] is True
    assert first["random_row_sampling_used"] is False
    assert first["absolute_mae_improvement_c"] == pytest.approx(1.0)
    assert first["absolute_mae_improvement_ci_lower_c"] == pytest.approx(1.0)
    assert first["absolute_mae_improvement_ci_upper_c"] == pytest.approx(1.0)
    assert first["relative_mae_improvement_fraction"] == pytest.approx(0.5)
    assert first["probability_improvement_gt_zero"] == 1.0
    assert first["probability_relative_improvement_gt_10_percent"] == 1.0


def test_random_row_or_unaggregated_bootstrap_is_rejected() -> None:
    cells = _constant_improvement_cells()
    duplicated_cell_rows = pd.concat([cells, cells.iloc[[0]]], ignore_index=True)

    with pytest.raises(ModelResultAnalysisError, match="pre-aggregated"):
        crossed_date_spatial_block_bootstrap(
            duplicated_cell_rows,
            seed=1,
            replicates=10,
            confidence_level=0.95,
        )
    with pytest.raises(ModelResultAnalysisError, match="Random-row"):
        crossed_date_spatial_block_bootstrap(
            cells,
            seed=1,
            replicates=10,
            confidence_level=0.95,
            method="random_tract_date_rows",
        )


def test_authenticated_input_hash_is_checked_before_parquet_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, directory = _write_authenticated_inputs(tmp_path)
    with (directory / "oof_predictions.parquet").open("ab") as handle:
        handle.write(b"corruption")

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        raise AssertionError("A failed byte lock must stop before Parquet reads.")

    monkeypatch.setattr(analysis_module.pd, "read_parquet", forbidden_read)
    with pytest.raises(ModelResultAnalysisError, match="byte lock"):
        authenticate_model_results(config)


def test_authenticated_oof_rejects_2025_even_when_file_hashes_are_valid(
    tmp_path: Path,
) -> None:
    config, _ = _write_authenticated_inputs(tmp_path, include_2025=True)

    with pytest.raises(ModelResultAnalysisError, match="Locked final-test year 2025"):
        authenticate_model_results(config)


def test_uncertainty_gate_requires_only_positive_ci_and_reports_ten_percent_separately(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    inputs = {
        "oof_predictions_sha256": "1" * 64,
        "summary_metrics_sha256": "2" * 64,
        "compile_provenance_file_sha256": "3" * 64,
        "compile_provenance_commit_sha256": "4" * 64,
    }
    bootstrap = {
        "relative_mae_improvement_fraction": 0.12,
        "relative_mae_improvement_ci_lower_fraction": 0.02,
    }

    table, status = build_protocol_success_gates(
        target_summary={"median_per_date_spearman": 0.60},
        bootstrap=bootstrap,
        config=config,
        input_authentication=inputs,
    )

    assert status["uncertainty_gate_pass"] is True
    assert status["ten_percent_threshold_ci_supported"] is False
    assert status["overall_protocol_success_gate_pass"] is True
    stronger = table.set_index("gate_id").loc[
        "uncertainty_supports_full_ten_percent_improvement"
    ]
    assert not bool(stronger["required_for_protocol_success"])
    assert not bool(stronger["passed"])
