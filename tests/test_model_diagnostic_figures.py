from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from la_heat.model_diagnostic_figures import (
    EXPECTED_FOREST_COHORTS,
    ForestCohort,
    ModelDiagnosticFigureConfig,
    ModelDiagnosticFigureError,
    authenticate_figure_inputs,
    load_model_diagnostic_figure_config,
    render_model_diagnostic_figures,
)
from la_heat.provenance import canonical_sha256, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> ModelDiagnosticFigureConfig:
    placeholder = tmp_path / "placeholder"
    return ModelDiagnosticFigureConfig(
        path=placeholder,
        semantic_sha256="a" * 64,
        initial_provenance=tmp_path / "initial_provenance.json",
        primary_bootstrap=tmp_path / "primary.csv",
        endpoint_provenance=tmp_path / "endpoint_provenance.json",
        hotspot_summary=tmp_path / "hotspot.csv",
        sensor_summary=tmp_path / "sensor.csv",
        qa_provenance=tmp_path / "qa_provenance.json",
        qa_cohort_bootstrap=tmp_path / "forest.csv",
        worst_dates=tmp_path / "worst.csv",
        residual_provenance=tmp_path / "residual_provenance.json",
        target_build_progress=tmp_path / "build_progress.json",
        oof_predictions=tmp_path / "oof.parquet",
        model_ready_targets=tmp_path / "targets.parquet",
        date_summary=tmp_path / "date_summary.parquet",
        tract_manifest=tmp_path / "tract_manifest.parquet",
        figure_output_directory=tmp_path / "figures",
        table_output_directory=tmp_path / "tables",
        final_test_year=2025,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        sensors=("landsat-8", "landsat-9"),
        expected_bootstrap_replicates=5_000,
        worst_date_limit=10,
        figure_dpi=200,
        forest_cohorts=tuple(ForestCohort(*values) for values in EXPECTED_FOREST_COHORTS),
        pilot_dates=(
            pd.Timestamp("2024-06-20"),
            pd.Timestamp("2024-08-23"),
            pd.Timestamp("2024-10-10"),
        ),
    )


def _primary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "family": "joint",
                "target_model_id": "M2",
                "strongest_legal_baseline_model_id": "B1",
                "bootstrap_replicates": 5_000,
                "random_row_sampling_used": False,
                "baseline_point_mae_c": 2.5,
                "target_model_point_mae_c": 2.1,
                "relative_mae_improvement_percent": 16.0,
                "relative_mae_improvement_ci_lower_percent": 4.0,
                "relative_mae_improvement_ci_upper_percent": 28.0,
                "independent_date_count": 4,
                "independent_spatial_block_count": 3,
                "tract_date_row_count": 40,
            }
        ]
    )


def _hotspot() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "family": ["joint", "joint"],
            "model_id": ["B1", "M2"],
            "mean_per_date_average_precision": [0.40, 0.67],
            "mean_per_date_recall_at_k": [0.42, 0.61],
            "independent_date_count": [3, 3],
        }
    )


def _sensor() -> pd.DataFrame:
    rows = []
    values = {
        ("B1", "landsat-8"): 2.4,
        ("B1", "landsat-9"): 2.7,
        ("M2", "landsat-8"): 2.05,
        ("M2", "landsat-9"): 2.2,
    }
    for (model, platform), mae in values.items():
        rows.append(
            {
                "family": "joint",
                "model_id": model,
                "platform": platform,
                "equal_date_weighted_mae_c": mae,
                "independent_date_count": 2,
            }
        )
    return pd.DataFrame(rows)


def _forest(*, display: bool = False) -> pd.DataFrame:
    rows = []
    for order, (dimension, label, display_label, group) in enumerate(
        EXPECTED_FOREST_COHORTS
    ):
        point = 8.0 if label == "tract_median_le_2k" else 16.0 + order
        low = -9.0 if label == "tract_median_le_2k" else point - 10.0
        high = 52.0 if label == "all_five_missing" else point + 12.0
        row = {
            "cohort_dimension": dimension,
            "cohort_label": label,
            "baseline_model_id": "B1",
            "target_model_id": "M2",
            "bootstrap_replicates": 5_000,
            "random_row_sampling_used": False,
            "relative_mae_improvement_percent": point,
            "relative_mae_improvement_ci_lower_percent": low,
            "relative_mae_improvement_ci_upper_percent": high,
            "tract_date_row_count": 100 + order,
            "independent_date_count": 4,
            "independent_spatial_block_count": 3,
        }
        if display:
            row.update(
                {
                    "display_label": display_label,
                    "display_group": group,
                    "display_order": order,
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _worst() -> pd.DataFrame:
    rows = []
    for index in range(10):
        m2 = 7.0 - index * 0.3
        b1 = 3.0 + index * 0.1
        bias = (m2 - 0.05) * (1 if index % 2 == 0 else -1)
        rows.append(
            {
                "target_date": pd.Timestamp("2024-09-01") - pd.Timedelta(days=index * 16),
                "platform": "landsat-8" if index % 2 == 0 else "landsat-9",
                "b1_mae_c": b1,
                "m2_mae_c": m2,
                "m2_minus_b1_mae_c": m2 - b1,
                "m2_bias_c": bias,
                "m2_underprediction_fraction": 0.01 if bias > 0 else 0.99,
                "tract_date_row_count": 100,
                "independent_spatial_block_count": 3,
            }
        )
    return pd.DataFrame(rows)


def _record(path: Path, frame: pd.DataFrame) -> dict[str, object]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
    }


def _write_provenance(
    path: Path,
    output_files: dict[str, object],
    *,
    kind: str,
    oof_sha256: str,
    extra_authentication: dict[str, object] | None = None,
) -> None:
    compile_commit = "c" * 64
    authentication: dict[str, object] = {
        "compile_provenance_commit_sha256": compile_commit,
        "oof_predictions_sha256": oof_sha256,
    }
    authentication.update(extra_authentication or {})
    payload: dict[str, object] = {
        "schema_version": 1,
        "algorithm_version": f"synthetic-{kind}",
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_oof_only",
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "input_authentication": authentication,
        "output_files": output_files,
    }
    if kind in {"initial", "endpoint"}:
        payload["compile_provenance_commit_sha256"] = compile_commit
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_pilot_sources(config: ModelDiagnosticFigureConfig) -> dict[str, str]:
    geoids = [f"0603700000{index}" for index in range(1, 5)]
    target_rows = []
    oof_rows = []
    for date_index, target_date in enumerate(config.pilot_dates):
        for tract_index, geoid in enumerate(geoids):
            observed = 35.0 + date_index * 2.0 + tract_index
            predicted = observed + (tract_index - 1.5) * 0.4
            target_rows.append(
                {
                    "tract_geoid": geoid,
                    "target_date": target_date,
                    "target_lst_c": observed,
                    "median_st_uncertainty_k": 1.0 + 0.2 * tract_index,
                }
            )
            oof_rows.append(
                {
                    "tract_geoid": geoid,
                    "target_date": target_date,
                    "family": "joint",
                    "model_id": "M2",
                    "y_true": observed,
                    "y_pred": predicted,
                }
            )
    targets = pd.DataFrame(target_rows)
    oof = pd.DataFrame(oof_rows)
    dates = pd.DataFrame(
        {
            "target_date": list(config.pilot_dates),
            "relative_endpoint_coverage_pass": [True, True, False],
        }
    )
    geometry = gpd.GeoDataFrame(
        {
            "GEOID": geoids,
            "primary_included": [True] * len(geoids),
            "geometry": [
                box(index % 2, index // 2, index % 2 + 0.9, index // 2 + 0.9)
                for index in range(len(geoids))
            ],
        },
        crs="EPSG:3310",
    )
    targets.to_parquet(config.model_ready_targets, index=False)
    oof.to_parquet(config.oof_predictions, index=False)
    dates.to_parquet(config.date_summary, index=False)
    geometry.to_parquet(config.tract_manifest, index=False)
    progress = {
        "state": "model_ready",
        "build_complete": True,
        "promoted_outputs_valid": True,
        "aggregate_outputs": {
            config.model_ready_targets.name: _record(config.model_ready_targets, targets),
            config.date_summary.name: _record(config.date_summary, dates),
        },
    }
    config.target_build_progress.write_text(json.dumps(progress), encoding="utf-8")
    return {
        "oof": sha256_file(config.oof_predictions),
        "targets": sha256_file(config.model_ready_targets),
        "dates": sha256_file(config.date_summary),
        "geometry": sha256_file(config.tract_manifest),
        "progress": sha256_file(config.target_build_progress),
    }


def _write_source_bundle(tmp_path: Path) -> ModelDiagnosticFigureConfig:
    config = _config(tmp_path)
    pilot_hashes = _write_pilot_sources(config)
    frames = {
        config.primary_bootstrap: _primary(),
        config.hotspot_summary: _hotspot(),
        config.sensor_summary: _sensor(),
        config.qa_cohort_bootstrap: _forest(),
        config.worst_dates: _worst(),
    }
    for path, frame in frames.items():
        frame.to_csv(path, index=False)
    _write_provenance(
        config.initial_provenance,
        {
            config.primary_bootstrap.name: _record(
                config.primary_bootstrap, frames[config.primary_bootstrap]
            )
        },
        kind="initial",
        oof_sha256=pilot_hashes["oof"],
    )
    _write_provenance(
        config.endpoint_provenance,
        {
            config.hotspot_summary.name: _record(
                config.hotspot_summary, frames[config.hotspot_summary]
            ),
            config.sensor_summary.name: _record(
                config.sensor_summary, frames[config.sensor_summary]
            ),
        },
        kind="endpoint",
        oof_sha256=pilot_hashes["oof"],
        extra_authentication={
            "target_progress_sha256": pilot_hashes["progress"],
            "model_ready_target_sha256": pilot_hashes["targets"],
            "date_summary_sha256": pilot_hashes["dates"],
        },
    )
    _write_provenance(
        config.qa_provenance,
        {
            config.qa_cohort_bootstrap.name: _record(
                config.qa_cohort_bootstrap, frames[config.qa_cohort_bootstrap]
            ),
            config.worst_dates.name: _record(config.worst_dates, frames[config.worst_dates]),
        },
        kind="qa",
        oof_sha256=pilot_hashes["oof"],
    )
    _write_provenance(
        config.residual_provenance,
        {},
        kind="residual",
        oof_sha256=pilot_hashes["oof"],
        extra_authentication={
            "target_manifest_sha256": pilot_hashes["targets"],
            "tract_manifest_sha256": pilot_hashes["geometry"],
        },
    )
    return config


def test_production_config_freezes_2025_cohorts_and_resolution() -> None:
    config = load_model_diagnostic_figure_config(
        ROOT / "configs/model_diagnostic_figures.toml"
    )

    assert config.final_test_year == 2025
    assert config.figure_dpi >= 180
    assert config.expected_bootstrap_replicates == 5_000
    assert len(config.forest_cohorts) == 11
    assert config.forest_cohorts[1].label == "tract_median_le_2k"
    assert "summary diagnostic" in config.forest_cohorts[1].display_label


def test_authentication_rejects_table_tamper_before_csv_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_source_bundle(tmp_path)
    with config.primary_bootstrap.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    calls = 0

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        raise AssertionError("CSV read occurred before byte-lock failure")

    monkeypatch.setattr(pd, "read_csv", forbidden_read)
    with pytest.raises(ModelDiagnosticFigureError, match="before CSV read"):
        authenticate_figure_inputs(config)
    assert calls == 0


def test_authentication_rejects_upstream_that_contains_2025(tmp_path: Path) -> None:
    config = _write_source_bundle(tmp_path)
    payload = json.loads(config.qa_provenance.read_text(encoding="utf-8"))
    payload.pop("commit_sha256")
    payload["contains_final_test_year"] = True
    payload["commit_sha256"] = canonical_sha256(payload)
    config.qa_provenance.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelDiagnosticFigureError, match="contains or unlocks"):
        authenticate_figure_inputs(config)


def test_authenticated_bundle_selects_only_prespecified_rows(tmp_path: Path) -> None:
    config = _write_source_bundle(tmp_path)
    authenticated = authenticate_figure_inputs(config)

    assert len(authenticated.qa_cohort_bootstrap) == 11
    assert authenticated.qa_cohort_bootstrap["display_order"].tolist() == list(range(11))
    assert len(authenticated.worst_dates) == 10
    assert authenticated.worst_dates["target_date"].dt.year.max() == 2024
    assert len(authenticated.input_authentication["upstream_provenance"]) == 4


def test_synthetic_figure_generation_is_high_resolution_and_nonblank(tmp_path: Path) -> None:
    config = _write_source_bundle(tmp_path)
    inputs = authenticate_figure_inputs(config)

    records = render_model_diagnostic_figures(inputs, config, config.figure_output_directory)

    assert len(records) == 4
    for record in records.values():
        assert record["width_px"] >= 1_500
        assert record["height_px"] >= 700
        assert record["dpi_x"] >= 179.5
        assert record["non_background_pixel_fraction"] > 0.005
