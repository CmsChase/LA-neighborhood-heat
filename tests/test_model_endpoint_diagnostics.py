from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.model_endpoint_diagnostics as diagnostics_module
from la_heat.model_endpoint_diagnostics import (
    ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION,
    AuthenticatedEndpointInputs,
    EndpointDiagnosticsConfig,
    ModelEndpointDiagnosticsError,
    authenticate_endpoint_inputs,
    build_hotspot_diagnostics,
    build_sensor_diagnostics,
    build_sentinel_stratum_diagnostics,
    continuous_average_precision,
    exact_top_k_mask,
    load_endpoint_diagnostics_config,
    validate_relative_endpoint_gate,
)
from la_heat.model_run_compile import (
    MODEL_RUN_COMPILE_ALGORITHM_VERSION,
    MODEL_RUN_COMPILE_SCHEMA_VERSION,
)
from la_heat.model_selection import MODEL_IDS
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import canonical_sha256, parquet_file_record, sha256_file
from la_heat.validation_splits import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_COLUMNS = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)


def _config(
    tmp_path: Path,
    *,
    rows: int = 4,
    dates: int = 2,
    blocks: int = 2,
    relative_dates: int = 1,
) -> EndpointDiagnosticsConfig:
    return EndpointDiagnosticsConfig(
        path=ROOT / "configs" / "model_endpoint_diagnostics.toml",
        semantic_sha256="a" * 64,
        evaluation_directory=tmp_path / "evaluation",
        target_directory=tmp_path / "targets",
        model_dataset_directory=tmp_path / "model_dataset",
        output_directory=tmp_path / "reports",
        final_test_year=2025,
        final_test_locked=True,
        families=FAMILIES,
        models=MODEL_IDS,
        focus_family="joint",
        focus_models=("B1", "M2"),
        expected_tract_date_rows=rows,
        expected_independent_dates=dates,
        expected_independent_spatial_blocks=blocks,
        expected_relative_gate_dates=relative_dates,
        sensors=("landsat-8", "landsat-9"),
        label_column="relative_hotspot_top20",
        gate_column="relative_endpoint_coverage_pass",
        positive_fraction=0.20,
        sentinel_enabled=True,
        sentinel_feature_columns=SENTINEL_COLUMNS,
        allowed_sentinel_strata=(
            "sentinel_complete",
            "sentinel_all_five_missing",
        ),
    )


def _synthetic_frames(
    *,
    include_2025: bool = False,
    partial_sentinel: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = [pd.Timestamp("2023-06-01"), pd.Timestamp("2024-06-01")]
    if include_2025:
        dates[-1] = pd.Timestamp("2025-06-01")
    target = pd.DataFrame(
        {
            "tract_geoid": ["001", "002", "001", "002"],
            "target_date": [dates[0], dates[0], dates[1], dates[1]],
            "platform": ["landsat-8", "landsat-8", "landsat-9", "landsat-9"],
            "spatial_block": ["a", "b", "a", "b"],
            "target_lst_c": [40.0, 30.0, 35.0, 25.0],
            "target_available": [True] * 4,
            "date_usable": [True] * 4,
            "relative_hotspot_top20": pd.Series(
                [True, False, pd.NA, pd.NA], dtype="boolean"
            ),
        }
    )
    date_summary = pd.DataFrame(
        {
            "target_date": dates,
            "platform": ["landsat-8", "landsat-9"],
            "retained_tract_count": [2, 2],
            "date_usable": [True, True],
            "relative_endpoint_coverage_pass": [True, False],
            "relative_hotspot_count": [1, 0],
        }
    )
    model_table = target[["tract_geoid", "target_date", "target_lst_c"]].copy()
    for column_index, column in enumerate(SENTINEL_COLUMNS):
        model_table[column] = [
            0.1 + column_index,
            0.2 + column_index,
            0.3 + column_index,
            np.nan,
        ]
    if partial_sentinel:
        model_table.loc[0, SENTINEL_COLUMNS[0]] = np.nan

    offsets = {"B0": 3.0, "B1": 2.0, "B2": 2.5, "M1": 1.5, "M2": 1.0}
    rows: list[dict[str, object]] = []
    for family in FAMILIES:
        for model_id in MODEL_IDS:
            for record in target.to_dict("records"):
                rows.append(
                    {
                        "tract_geoid": record["tract_geoid"],
                        "target_date": record["target_date"],
                        "spatial_block": record["spatial_block"],
                        "family": family,
                        "fold_id": f"{family}-fold",
                        "model_id": model_id,
                        "candidate_id": f"{model_id}-candidate",
                        "y_true": record["target_lst_c"],
                        "y_pred": float(record["target_lst_c"]) + offsets[model_id],
                    }
                )
    oof = pd.DataFrame(rows, columns=list(OUTER_PREDICTION_COLUMNS))
    return oof, target, date_summary, model_table


def _parquet_record(path: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = pd.read_parquet(path)
    return frame, {"path": str(path), **parquet_file_record(path, frame)}


def _write_authenticated_inputs(
    tmp_path: Path,
    *,
    include_2025: bool = False,
    partial_sentinel: bool = False,
) -> tuple[EndpointDiagnosticsConfig, dict[str, Path]]:
    config = _config(tmp_path)
    for directory in (
        config.evaluation_directory,
        config.target_directory,
        config.model_dataset_directory,
    ):
        directory.mkdir(parents=True)
    oof, target, date_summary, model_table = _synthetic_frames(
        include_2025=include_2025,
        partial_sentinel=partial_sentinel,
    )
    paths = {
        "oof": config.evaluation_directory / "oof_predictions.parquet",
        "target": config.target_directory / "development_targets_model_ready.parquet",
        "summary": config.target_directory / "date_summary.parquet",
        "model": config.model_dataset_directory / "development_model_table.parquet",
        "progress": config.target_directory / "build_progress.json",
        "model_provenance": config.model_dataset_directory
        / "model_dataset_provenance.json",
        "compile": config.evaluation_directory / "model_run_compile_provenance.json",
    }
    oof.to_parquet(paths["oof"], index=False)
    target.to_parquet(paths["target"], index=False)
    date_summary.to_parquet(paths["summary"], index=False)
    model_table.to_parquet(paths["model"], index=False)
    _, oof_record = _parquet_record(paths["oof"])
    _, target_record = _parquet_record(paths["target"])
    _, summary_record = _parquet_record(paths["summary"])
    _, model_record = _parquet_record(paths["model"])

    progress = {
        "state": "model_ready",
        "build_complete": True,
        "partial_outputs_only": False,
        "promoted_outputs_valid": True,
        "usable_overpass_count": 2,
        "aggregate_outputs": {
            paths["target"].name: {
                key: target_record[key]
                for key in ("sha256", "bytes", "rows", "schema_sha256")
            },
            paths["summary"].name: {
                key: summary_record[key]
                for key in ("sha256", "bytes", "rows", "schema_sha256")
            },
        },
    }
    paths["progress"].write_text(json.dumps(progress), encoding="utf-8")
    model_provenance = {
        "schema_version": 1,
        "algorithm_version": "gated-development-model-dataset-v1",
        "state": "complete",
        "ready_for_modeling": True,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "row_count": 4,
        "independent_date_count": 2,
        "complete_model_feature_rows": 3,
        "incomplete_model_feature_rows": 1,
        "inputs": {
            "target_progress": {
                "path": str(paths["progress"]),
                "sha256": sha256_file(paths["progress"]),
            },
            "model_ready_target": target_record,
        },
        "output_files": {paths["model"].name: model_record},
    }
    model_provenance["commit_sha256"] = canonical_sha256(model_provenance)
    paths["model_provenance"].write_text(
        json.dumps(model_provenance), encoding="utf-8"
    )
    compile_provenance = {
        "schema_version": MODEL_RUN_COMPILE_SCHEMA_VERSION,
        "algorithm_version": MODEL_RUN_COMPILE_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_reporting": True,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "context_row_count": 4,
        "independent_date_count": 2,
        "family_count": len(FAMILIES),
        "model_count": len(MODEL_IDS),
        "oof_prediction_row_count": 4 * len(FAMILIES) * len(MODEL_IDS),
        "model_dataset_commit_sha256": model_provenance["commit_sha256"],
        "output_files": {
            paths["oof"].name: {
                "path_base": "output_directory",
                **oof_record,
            }
        },
    }
    compile_provenance["commit_sha256"] = canonical_sha256(compile_provenance)
    paths["compile"].write_text(
        json.dumps(compile_provenance), encoding="utf-8"
    )
    return config, paths


def test_production_config_freezes_gated_continuous_score_contract() -> None:
    config = load_endpoint_diagnostics_config(
        ROOT / "configs" / "model_endpoint_diagnostics.toml"
    )

    assert config.semantic_sha256
    assert config.final_test_locked is True
    assert config.expected_relative_gate_dates == 34
    assert config.positive_fraction == 0.20
    assert config.focus_models == ("B1", "M2")
    assert ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION.endswith("v1")


def test_exact_top_k_uses_geoid_ascending_to_break_score_ties() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["003", "001", "002", "004"],
            "score": [10.0, 10.0, 10.0, 9.0],
        }
    )

    mask = exact_top_k_mask(frame, score_column="score", positive_fraction=0.50)

    assert set(frame.loc[mask, "tract_geoid"]) == {"001", "002"}
    assert int(mask.sum()) == 2


def test_average_precision_uses_full_continuous_ranking() -> None:
    value = continuous_average_precision(
        [True, False, True, False], [0.9, 0.8, 0.7, 0.1]
    )

    assert value == pytest.approx((1.0 + 2.0 / 3.0) / 2.0)


def test_label_gate_rejects_labels_on_ungated_dates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _, target, date_summary, _ = _synthetic_frames()
    target.loc[target["target_date"].eq(pd.Timestamp("2024-06-01")),
               "relative_hotspot_top20"] = False

    with pytest.raises(ModelEndpointDiagnosticsError, match="ungated date"):
        validate_relative_endpoint_gate(target, date_summary, config)


def test_label_audit_rejects_non_top_k_ground_truth(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _, target, date_summary, _ = _synthetic_frames()
    target.loc[target["target_date"].eq(pd.Timestamp("2023-06-01")),
               "relative_hotspot_top20"] = [False, True]

    with pytest.raises(ModelEndpointDiagnosticsError, match="exact top-k"):
        validate_relative_endpoint_gate(target, date_summary, config)


@pytest.mark.parametrize("locked_key", ["oof", "target"])
def test_hash_lock_is_checked_before_any_parquet_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    locked_key: str,
) -> None:
    config, paths = _write_authenticated_inputs(tmp_path)
    with paths[locked_key].open("ab") as handle:
        handle.write(b"corruption")

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        raise AssertionError("A failed byte lock must stop before Parquet reads.")

    monkeypatch.setattr(diagnostics_module.pd, "read_parquet", forbidden_read)
    with pytest.raises(ModelEndpointDiagnosticsError, match="byte lock"):
        authenticate_endpoint_inputs(config)


def test_authenticated_inputs_reject_2025_even_with_valid_hashes(tmp_path: Path) -> None:
    config, _ = _write_authenticated_inputs(tmp_path, include_2025=True)

    with pytest.raises(ModelEndpointDiagnosticsError, match="Locked final-test year 2025"):
        authenticate_endpoint_inputs(config)


def test_partial_sentinel_missingness_is_rejected(tmp_path: Path) -> None:
    config, _ = _write_authenticated_inputs(tmp_path, partial_sentinel=True)

    with pytest.raises(ModelEndpointDiagnosticsError, match="Partial Sentinel"):
        authenticate_endpoint_inputs(config)


def test_outputs_report_rows_dates_blocks_and_complete_cardinalities(
    tmp_path: Path,
) -> None:
    config, _ = _write_authenticated_inputs(tmp_path)
    authenticated: AuthenticatedEndpointInputs = authenticate_endpoint_inputs(config)

    hotspot_dates, hotspot_summary = build_hotspot_diagnostics(
        authenticated.oof, config
    )
    sensor_dates, sensor_summary = build_sensor_diagnostics(authenticated.oof, config)
    sentinel_summary = build_sentinel_stratum_diagnostics(authenticated.oof, config)

    pair_count = len(FAMILIES) * len(MODEL_IDS)
    assert len(hotspot_dates) == pair_count
    assert len(hotspot_summary) == pair_count
    assert hotspot_summary["tract_date_row_count"].eq(2).all()
    assert hotspot_summary["independent_date_count"].eq(1).all()
    assert hotspot_summary["independent_spatial_block_count"].eq(2).all()
    assert len(sensor_dates) == pair_count * 2
    assert len(sensor_summary) == pair_count * 2
    assert set(sensor_summary["platform"]) == {"landsat-8", "landsat-9"}
    assert len(sentinel_summary) == pair_count * 2
    assert set(sentinel_summary["sentinel_stratum"]) == {
        "sentinel_complete",
        "sentinel_all_five_missing",
    }
