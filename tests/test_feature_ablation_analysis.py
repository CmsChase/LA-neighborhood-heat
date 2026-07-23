from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.feature_ablation_analysis import (
    ABLATION_COMPILE_ALGORITHM_VERSION,
    ABLATION_OOF_FILENAME,
    ABLATION_PROVENANCE_FILENAME,
    ALL_FEATURE_SCENARIO,
    BOOTSTRAP_METHOD,
    BOOTSTRAP_SAMPLING_UNIT,
    FITTED_SCENARIOS,
    FeatureAblationAnalysisConfig,
    FeatureAblationAnalysisError,
    authenticate_feature_ablation_inputs,
    build_feature_ablation_metrics,
    build_joint_feature_ablation_bootstrap,
    load_feature_ablation_analysis_config,
)
from la_heat.model_result_analysis import ResultAnalysisConfig
from la_heat.model_run_compile import SUMMARY_METRIC_COLUMNS
from la_heat.model_selection import MODEL_IDS
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import canonical_sha256, parquet_file_record, sha256_file
from la_heat.validation_splits import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN_ID = "b" * 64
ABLATION_RUN_ID = "a" * 64


def _result_config(tmp_path: Path) -> ResultAnalysisConfig:
    return ResultAnalysisConfig(
        path=ROOT / "configs" / "result_analysis.toml",
        semantic_sha256="c" * 64,
        evaluation_directory=tmp_path / "evaluation",
        output_directory=tmp_path / "unused-results",
        final_test_year=2025,
        final_test_locked=True,
        target_family="joint",
        target_model_id="M2",
        legal_baseline_model_ids=("B0", "B1", "B2"),
        primary_metric_column="primary_equal_date_weighted_mae_c",
        spearman_metric_column="median_per_date_spearman",
        expected_independent_date_count=2,
        expected_independent_spatial_block_count=2,
        expected_tract_date_row_count=4,
        bootstrap_method=BOOTSTRAP_METHOD,
        bootstrap_sampling_unit=BOOTSTRAP_SAMPLING_UNIT,
        bootstrap_seed=123,
        bootstrap_replicates=200,
        confidence_level=0.95,
        minimum_relative_mae_improvement_fraction=0.10,
        minimum_median_per_date_spearman=0.50,
        uncertainty_relative_ci_lower_must_exceed=0.0,
    )


def _analysis_config(tmp_path: Path) -> FeatureAblationAnalysisConfig:
    return FeatureAblationAnalysisConfig(
        path=ROOT / "configs" / "feature_ablation_analysis.toml",
        semantic_sha256="d" * 64,
        result_analysis_config=ROOT / "configs" / "result_analysis.toml",
        compile_directory=tmp_path / "ablation",
        output_directory=tmp_path / "reports",
        expected_run_id=ABLATION_RUN_ID,
        expected_source_run_id=SOURCE_RUN_ID,
        final_test_year=2025,
        final_test_locked=True,
        model_id="M2",
        split_families=FAMILIES,
        fitted_scenario_ids=FITTED_SCENARIOS,
        all_feature_scenario_id=ALL_FEATURE_SCENARIO,
        expected_rows_per_family=4,
        expected_dates=2,
        expected_blocks=2,
        bootstrap_method=BOOTSTRAP_METHOD,
        bootstrap_sampling_unit=BOOTSTRAP_SAMPLING_UNIT,
        bootstrap_seed=456,
        bootstrap_replicates=300,
        bootstrap_confidence_level=0.95,
        relative_improvement_threshold_fraction=0.10,
    )


def _source_oof() -> pd.DataFrame:
    dates = pd.to_datetime(["2023-06-01", "2024-06-01"])
    base = [
        {
            "tract_geoid": f"tract-{date_index}-{block_index}",
            "target_date": date,
            "spatial_block": f"block-{block_index}",
            "y_true": float(30 + 2 * date_index + block_index),
        }
        for date_index, date in enumerate(dates)
        for block_index in range(2)
    ]
    offsets = {"B0": 3.0, "B1": 2.0, "B2": 2.5, "M1": 1.5, "M2": 1.0}
    rows = [
        {
            "tract_geoid": row["tract_geoid"],
            "target_date": row["target_date"],
            "spatial_block": row["spatial_block"],
            "family": family,
            "fold_id": f"{family}-fold",
            "model_id": model_id,
            "candidate_id": f"{model_id}-candidate",
            "y_true": row["y_true"],
            "y_pred": float(row["y_true"]) + offsets[model_id],
        }
        for family in FAMILIES
        for model_id in MODEL_IDS
        for row in base
    ]
    result = pd.DataFrame(rows, columns=OUTER_PREDICTION_COLUMNS)
    result["tract_geoid"] = result["tract_geoid"].astype("string")
    return result


def _source_summary() -> pd.DataFrame:
    offsets = {"B0": 3.0, "B1": 2.0, "B2": 2.5, "M1": 1.5, "M2": 1.0}
    rows = []
    for family in FAMILIES:
        for model_id in MODEL_IDS:
            offset = offsets[model_id]
            rows.append(
                {
                    "family": family,
                    "model_id": model_id,
                    "row_count": 4,
                    "independent_date_count": 2,
                    "independent_spatial_block_count": 2,
                    "primary_equal_date_weighted_mae_c": offset,
                    "pooled_rmse_c": offset,
                    "pooled_oos_r2": 0.5,
                    "pooled_mean_signed_error_c": offset,
                    "equal_date_weighted_mean_signed_error_c": offset,
                    "equal_date_weighted_within_date_anomaly_mae_c": 0.0,
                    "median_per_date_spearman": 1.0,
                    "spearman_defined_date_count": 2,
                    "spearman_undefined_date_count": 0,
                }
            )
    return pd.DataFrame(rows, columns=SUMMARY_METRIC_COLUMNS)


def _write_source(tmp_path: Path, result_config: ResultAnalysisConfig) -> pd.DataFrame:
    directory = result_config.evaluation_directory
    directory.mkdir(parents=True)
    oof = _source_oof()
    summary = _source_summary()
    oof_path = directory / "oof_predictions.parquet"
    summary_path = directory / "summary_metrics.csv"
    oof.to_parquet(oof_path, index=False)
    summary.to_csv(summary_path, index=False)
    oof_record = {
        "path": oof_path.name,
        "path_base": "output_directory",
        **parquet_file_record(oof_path, pd.read_parquet(oof_path)),
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
        "run_id": SOURCE_RUN_ID,
        "context_run_id": "e" * 64,
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
    return oof


def _ablation_oof(source_oof: pd.DataFrame) -> pd.DataFrame:
    source = source_oof.loc[source_oof["model_id"].eq("M2")].copy()
    offsets = {
        "calendar_weather": 2.0,
        "calendar_land_use_geography": 3.0,
        "calendar_satellite": 4.0,
    }
    frames = []
    for scenario, offset in offsets.items():
        frame = source.copy()
        frame.insert(3, "ablation_id", scenario)
        frame["y_pred"] = frame["y_true"] + offset
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _write_ablation(
    config: FeatureAblationAnalysisConfig,
    source_oof: pd.DataFrame,
    *,
    transform: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> Path:
    directory = config.compile_directory
    directory.mkdir(parents=True)
    frame = _ablation_oof(source_oof)
    if transform is not None:
        frame = transform(frame.copy())
    path = directory / ABLATION_OOF_FILENAME
    frame.to_parquet(path, index=False)
    round_trip = pd.read_parquet(path)
    fingerprint = {
        "algorithm_version": ABLATION_COMPILE_ALGORITHM_VERSION,
        "python": "synthetic",
        "packages": {},
        "files": {},
    }
    provenance = {
        "schema_version": 1,
        "algorithm_version": ABLATION_COMPILE_ALGORITHM_VERSION,
        "run_id": ABLATION_RUN_ID,
        "context_run_id": "e" * 64,
        "source_run_id": SOURCE_RUN_ID,
        "source_run_manifest_commit_sha256": "f" * 64,
        "source_selection_and_all_oof_lock_sha256": "1" * 64,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "fitted_ablation_count": 3,
        "split_families": list(FAMILIES),
        "split_family_count": 3,
        "outer_folds_per_ablation": 431,
        "fitted_fragment_count": 1293,
        "fitted_oof_rows_per_ablation_family": 4,
        "fitted_oof_rows_per_ablation": 12,
        "fitted_oof_row_count": len(frame),
        "compiler_pipeline_sha256": canonical_sha256(fingerprint),
        "compiler_pipeline_fingerprint": fingerprint,
        "all_feature_reference": {
            "path": str(config.result_analysis_config),
            "sha256": sha256_file(
                config.compile_directory.parent / "evaluation" / "oof_predictions.parquet"
            ),
            "model_id": "M2",
            "refit_performed": False,
        },
        "output_files": {path.name: parquet_file_record(path, round_trip)},
        "input_fragments": [{"task_id": index} for index in range(1293)],
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    (directory / ABLATION_PROVENANCE_FILENAME).write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    return path


def _write_inputs(
    tmp_path: Path,
    *,
    transform: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> tuple[FeatureAblationAnalysisConfig, ResultAnalysisConfig, Path]:
    result_config = _result_config(tmp_path)
    source = _write_source(tmp_path, result_config)
    config = _analysis_config(tmp_path)
    path = _write_ablation(config, source, transform=transform)
    return config, result_config, path


def test_production_config_freezes_exact_surfaces_and_cluster_bootstrap() -> None:
    config = load_feature_ablation_analysis_config(
        ROOT / "configs" / "feature_ablation_analysis.toml"
    )

    assert config.fitted_scenario_ids == FITTED_SCENARIOS
    assert config.split_families == FAMILIES
    assert config.expected_fitted_rows == 3 * 3 * 63_403
    assert config.bootstrap_replicates == 5_000
    assert config.bootstrap_method == BOOTSTRAP_METHOD
    assert config.final_test_locked is True


def test_authentication_accepts_exact_three_by_three_surfaces(tmp_path: Path) -> None:
    config, result_config, _ = _write_inputs(tmp_path)

    authenticated = authenticate_feature_ablation_inputs(
        config, result_analysis_config=result_config
    )

    assert len(authenticated.fitted_oof) == 36
    assert len(authenticated.all_feature_oof) == 12
    assert set(authenticated.frame["scenario_id"]) == {
        ALL_FEATURE_SCENARIO,
        *FITTED_SCENARIOS,
    }


def test_missing_compile_provenance_fails_closed_without_outputs(tmp_path: Path) -> None:
    result_config = _result_config(tmp_path)
    _write_source(tmp_path, result_config)
    config = _analysis_config(tmp_path)

    with pytest.raises(FeatureAblationAnalysisError, match="compile provenance is absent"):
        authenticate_feature_ablation_inputs(
            config, result_analysis_config=result_config
        )

    assert not config.output_directory.exists()


def test_byte_tamper_fails_closed(tmp_path: Path) -> None:
    config, result_config, path = _write_inputs(tmp_path)
    with path.open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(FeatureAblationAnalysisError, match="byte lock failed"):
        authenticate_feature_ablation_inputs(
            config, result_analysis_config=result_config
        )


def test_locked_2025_row_is_rejected_after_valid_recommit(tmp_path: Path) -> None:
    def add_2025(frame: pd.DataFrame) -> pd.DataFrame:
        frame.loc[0, "target_date"] = pd.Timestamp("2025-06-01")
        return frame

    config, result_config, _ = _write_inputs(tmp_path, transform=add_2025)

    with pytest.raises(FeatureAblationAnalysisError, match="locked 2025"):
        authenticate_feature_ablation_inputs(
            config, result_analysis_config=result_config
        )


def test_source_key_mismatch_is_rejected(tmp_path: Path) -> None:
    def change_key(frame: pd.DataFrame) -> pd.DataFrame:
        frame.loc[0, "tract_geoid"] = "not-a-source-key"
        return frame

    config, result_config, _ = _write_inputs(tmp_path, transform=change_key)

    with pytest.raises(FeatureAblationAnalysisError, match="keys do not exactly match"):
        authenticate_feature_ablation_inputs(
            config, result_analysis_config=result_config
        )


def test_metric_formulas_are_date_macro_not_random_row_weighted() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "c", "d"],
            "target_date": pd.to_datetime(
                ["2023-01-01", "2023-01-01", "2024-01-01", "2024-01-01"]
            ),
            "spatial_block": ["x", "y", "x", "y"],
            "scenario_id": ALL_FEATURE_SCENARIO,
            "family": "joint",
            "fold_id": "fold",
            "model_id": "M2",
            "candidate_id": "candidate",
            "y_true": [10.0, 20.0, 30.0, 40.0],
            "y_pred": [11.0, 17.0, 32.0, 38.0],
        }
    )

    row = build_feature_ablation_metrics(frame).iloc[0]

    assert row["date_macro_mae_c"] == pytest.approx(2.0)
    assert row["date_macro_rmse_c"] == pytest.approx((np.sqrt(5.0) + 2.0) / 2.0)
    assert row["date_macro_bias_c"] == pytest.approx(-0.5)
    assert row["pooled_rmse_c"] == pytest.approx(np.sqrt(4.5))
    assert row["median_per_date_spearman"] == pytest.approx(1.0)
    assert row["independent_date_count"] == 2
    assert row["independent_spatial_block_count"] == 2


def test_bootstrap_resamples_complete_dates_and_blocks_with_correct_direction(
    tmp_path: Path,
) -> None:
    config = _analysis_config(tmp_path)
    keys = [
        {
            "tract_geoid": f"tract-{date_index}-{block_index}",
            "target_date": date,
            "spatial_block": f"block-{block_index}",
            "y_true": float(30 + date_index + block_index),
        }
        for date_index, date in enumerate(pd.to_datetime(["2023-01-01", "2024-01-01"]))
        for block_index in range(2)
    ]
    frames = []
    for scenario in (ALL_FEATURE_SCENARIO, *FITTED_SCENARIOS):
        scenario_rows = pd.DataFrame(keys)
        scenario_rows["scenario_id"] = scenario
        scenario_rows["family"] = "joint"
        scenario_rows["fold_id"] = "fold"
        scenario_rows["model_id"] = "M2"
        scenario_rows["candidate_id"] = "candidate"
        scenario_rows["y_pred"] = scenario_rows["y_true"] + (
            1.0 if scenario == ALL_FEATURE_SCENARIO else 2.0
        )
        frames.append(scenario_rows)
    frame = pd.concat(frames, ignore_index=True)

    first = build_joint_feature_ablation_bootstrap(frame, config)
    second = build_joint_feature_ablation_bootstrap(frame, config)

    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 3
    assert first["absolute_mae_improvement_c"].eq(1.0).all()
    assert first["relative_mae_improvement_fraction"].eq(0.5).all()
    assert first["absolute_mae_improvement_ci_lower_c"].eq(1.0).all()
    assert first["probability_improvement_gt_zero"].eq(1.0).all()
    assert first["random_row_sampling_used"].eq(False).all()  # noqa: E712
    assert first["complete_date_resampling"].eq(True).all()  # noqa: E712
    assert first["complete_spatial_block_resampling"].eq(True).all()  # noqa: E712
    assert first["independent_date_count"].eq(2).all()
    assert first["independent_spatial_block_count"].eq(2).all()
    assert first["tract_date_row_count"].eq(4).all()
