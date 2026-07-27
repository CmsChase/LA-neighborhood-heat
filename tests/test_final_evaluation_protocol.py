from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

import la_heat.final_evaluation_protocol as protocol
from la_heat.final_evaluation_protocol import (
    ALGORITHM_VERSION,
    EXPECTED_OUTPUT_FILES,
    FinalEvaluationConfig,
    FinalEvaluationProtocolError,
    _create_or_authenticate_claim,
    _exclusive_json,
    _freeze_blind_predictions,
    _joined_reporting_rows,
    _values_opened_callback,
    execute_locked_final_evaluation,
    load_final_evaluation_config,
)


def _analysis() -> dict[str, Any]:
    return {
        "final_test_year": 2025,
        "expected_key_count": 25_208,
        "expected_tract_count": 1_096,
        "expected_inventory_overpass_count": 23,
        "expected_inventory_scene_count": 45,
        "expected_model_feature_count": 46,
        "model_ids": ["B1", "M2"],
        "baseline_model_id": "B1",
        "primary_model_id": "M2",
        "primary_metric": "equal_date_weighted_mae_c",
        "evaluation_cohort": "all_date_usable_and_target_available_rows",
        "minimum_usable_date_count_for_metrics": 1,
        "prediction_origin": "00:00 local time on target date",
        "interpretation": "historical_hindcast_surface_heat_hazard_proxy",
    }


def _config(tmp_path: Path) -> FinalEvaluationConfig:
    names = {
        "formal_model_lock": "manifests/model_lock/MODEL_LOCK.json",
        "predictor_table": "data/predictors.parquet",
        "predictor_provenance": "manifests/PREDICTORS.json",
        "landsat_inventory": "manifests/LANDSAT.json",
        "research_config": "configs/research.toml",
        "readiness": "manifests/evaluation/EVALUATION_READINESS.json",
        "authorization": "manifests/AUTHORIZATION.json",
        "claim": "manifests/evaluation/CONSUMPTION_CLAIM.json",
        "values_opened": "manifests/evaluation/VALUES_OPENED.json",
        "predictions_frozen": "manifests/evaluation/PREDICTIONS_FROZEN.json",
        "complete": "manifests/evaluation/EVALUATION_COMPLETE.json",
        "staging_root": "data/.final_evaluation.staging",
        "target_cache_directory": "data/target_cache",
        "final_output_directory": "data/final_evaluation",
    }
    raw = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "frozen_before_2025_target_values",
    }
    return FinalEvaluationConfig(
        root=tmp_path,
        path=tmp_path / "configs/final_evaluation_2025.toml",
        raw=raw,
        paths={name: tmp_path / value for name, value in names.items()},
        locks={},
        analysis=_analysis(),
        bootstrap={},
        success_gates={},
        hotspot={},
        publication={},
        semantic_sha256="a" * 64,
    )


def _model_contract() -> tuple[dict[str, Any], list[str], list[str]]:
    b1_features = [f"feature_{index:02d}" for index in range(23)]
    m2_features = [
        *b1_features,
        *[f"feature_{index:02d}" for index in range(23, 41)],
        *protocol.SENTINEL_FEATURES,
    ]
    models: dict[str, Any] = {}
    for model_id, features in (("B1", b1_features), ("M2", m2_features)):
        models[model_id] = {
            "fitted_pipeline_sha256": ("1" if model_id == "B1" else "2") * 64,
            "fitted_pipeline_bytes": 10,
            "selected_candidate_id": f"{model_id}-candidate",
            "selected_parameters": {"alpha": 1},
            "random_state": 7,
            "feature_names": features,
            "training_row_count": 100,
            "training_date_count": 5,
            "training_spatial_block_count": 3,
            "training_keys_sha256": "3" * 64,
        }
    return {"models": models}, b1_features, m2_features


class _PredictOnlyPipeline:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value, dtype=float)

    def fit(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("A frozen model must never be fitted.")

    def fit_transform(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("A frozen model must never be fit-transformed.")


def _bundle(
    model_id: str,
    features: list[str],
    *,
    value: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "algorithm_version": "full-development-final-model-v1",
        "model_id": model_id,
        "candidate_id": f"{model_id}-candidate",
        "candidate_parameters": {"alpha": 1},
        "random_state": 7,
        "feature_names": features,
        "training_row_count": 100,
        "training_date_count": 5,
        "training_spatial_block_count": 3,
        "training_keys_sha256": "3" * 64,
        "pipeline": _PredictOnlyPipeline(value),
    }


def _predictors(features: list[str]) -> pd.DataFrame:
    dates = pd.date_range("2025-05-01", periods=23, freq="7D")
    geoids = [f"{6_037_000_000 + index:011d}" for index in range(1_096)]
    keys = pd.MultiIndex.from_product(
        [dates, geoids],
        names=["target_date", "tract_geoid"],
    ).to_frame(index=False)
    keys = keys.loc[:, ["tract_geoid", "target_date"]]
    for index, feature in enumerate(features):
        keys[feature] = float(index)
    return keys


def _source_binding_tables() -> tuple[
    dict[str, pd.DataFrame],
    pd.DataFrame,
    pd.DataFrame,
]:
    dates = pd.to_datetime(["2025-07-01", "2025-07-01"])
    geoids = ["06037000001", "06037000002"]
    target = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            "platform": ["landsat-9", "landsat-9"],
            "spatial_block": ["block-a", "block-b"],
            "target_lst_c": [31.0, 32.0],
            "target_available": [True, True],
            "date_usable": [True, True],
            "relative_hotspot_top20": [False, True],
            "source_scene_count": [2, 2],
            "source_scene_ids": ["scene-a;scene-b", "scene-a;scene-b"],
            "rasterized_pixel_count": [20, 22],
            "footprint_pixel_count": [18, 20],
            "eligible_pixel_count_static": [17, 19],
            "valid_pixel_count": [16, 18],
            "footprint_fraction": [0.90, 0.91],
            "valid_fraction": [0.94, 0.95],
            "median_st_uncertainty_k": [0.5, 0.6],
            "p90_st_uncertainty_k": [0.8, 0.9],
            "median_cloud_distance_km": [3.0, 4.0],
            "tract_exclusion_reason": [pd.NA, pd.NA],
            "date_exclusion_reason": [pd.NA, pd.NA],
        }
    )
    date_summary = pd.DataFrame(
        {
            "target_date": [pd.Timestamp("2025-07-01")],
            "relative_endpoint_coverage_pass": [True],
            "date_exclusion_reason": [pd.NA],
            "union_city_coverage_fraction": [0.99],
            "retained_tract_count": [2],
            "retained_tract_fraction": [1.0],
            "minimum_eligible_joint_cell_retention_fraction": [0.9],
        }
    )
    blind = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            "y_pred_b1": [30.0, 30.5],
            "y_pred_m2": [31.1, 31.8],
        }
    )
    predictors = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            **{
                feature: [0.2, np.nan]
                for feature in protocol.SENTINEL_FEATURES
            },
        }
    )
    contributions = pd.DataFrame(
        {
            "target_date": dates,
            "overpass_id": ["overpass-a", "overpass-a"],
            "scene_id": ["scene-a", "scene-b"],
            "selected_valid_pixel_count": [16, 18],
            "tract_geoid": geoids,
        }
    )
    joined = protocol._joined_reporting_rows(
        target_qa=target,
        date_summary=date_summary,
        blind_predictions=blind,
        predictors=predictors,
    )
    reports = _fake_source_bound_reporting(joined, object())
    return (
        {
            "blind_predictions.parquet": blind,
            "final_target_qa.parquet": target,
            "date_summary.parquet": date_summary,
            "scene_contributions.parquet": contributions,
            "evaluation_rows.parquet": reports.evaluation_rows,
            "qa_missingness_summary.csv": reports.qa_missingness_summary,
        },
        predictors,
        reports.evaluation_rows,
    )


def _fake_source_bound_reporting(
    rows: pd.DataFrame,
    _settings: object,
) -> SimpleNamespace:
    source = rows.loc[
        rows["date_usable"].astype(bool)
        & rows["target_available"].astype(bool)
    ].copy()
    evaluation = pd.DataFrame(
        {
            "tract_geoid": source["tract_geoid"],
            "target_date": source["target_date"],
            "spatial_block": source["spatial_block"],
            "sensor": source["sensor"],
            "sentinel_available": source["sentinel_available"],
            "target_available": source["target_available"],
            "date_usable": source["date_usable"],
            "relative_endpoint_coverage_pass": source[
                "relative_endpoint_coverage_pass"
            ],
            "relative_hotspot_top20": source["relative_hotspot_top20"],
            "y_true": source["y_true"],
            "y_pred_b1": source["y_pred_b1"],
            "y_pred_m2": source["y_pred_m2"],
        }
    )
    qa_columns = (
        "source_scene_count",
        "source_scene_ids",
        "rasterized_pixel_count",
        "footprint_pixel_count",
        "eligible_pixel_count_static",
        "valid_pixel_count",
        "footprint_fraction",
        "valid_fraction",
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
        "median_cloud_distance_km",
        "tract_exclusion_reason",
        "date_exclusion_reason",
        "union_city_coverage_fraction",
        "retained_tract_count",
        "retained_tract_fraction",
        "minimum_eligible_joint_cell_retention_fraction",
    )
    for column in qa_columns:
        evaluation[column] = source[column]
    evaluation["sentinel_stratum"] = np.where(
        evaluation["sentinel_available"],
        "sentinel_complete",
        "sentinel_all_five_missing",
    )
    evaluation["b1_error_c"] = (
        evaluation["y_pred_b1"] - evaluation["y_true"]
    )
    evaluation["m2_error_c"] = (
        evaluation["y_pred_m2"] - evaluation["y_true"]
    )
    evaluation["b1_absolute_error_c"] = evaluation["b1_error_c"].abs()
    evaluation["m2_absolute_error_c"] = evaluation["m2_error_c"].abs()
    columns = protocol._output_table_column_contracts()[
        "evaluation_rows.parquet"
    ]
    qa_columns = protocol._output_table_column_contracts()[
        "qa_missingness_summary.csv"
    ]
    qa_record: dict[str, Any] = {
        column: np.nan for column in qa_columns
    }
    qa_record.update(
        {
            "summary_level": "overall",
            "sensor": "all",
            "inventory_key_count": int(len(rows)),
            "independent_date_count": int(rows["target_date"].nunique()),
            "independent_spatial_block_count": int(
                rows["spatial_block"].nunique()
            ),
            "target_available_count": int(
                rows["target_available"].astype(bool).sum()
            ),
            "target_unavailable_count": int(
                (~rows["target_available"].astype(bool)).sum()
            ),
            "target_availability_fraction": float(
                rows["target_available"].astype(bool).mean()
            ),
            "qa_rules_json": "{}",
        }
    )
    return SimpleNamespace(
        evaluation_rows=evaluation.loc[:, list(columns)].reset_index(drop=True),
        qa_missingness_summary=pd.DataFrame(
            [qa_record],
            columns=list(qa_columns),
        ),
    )


def _source_binding_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    FinalEvaluationConfig,
    dict[str, Any],
    dict[str, pd.DataFrame],
    pd.DataFrame,
    dict[str, Any],
]:
    import la_heat.final_evaluation_reporting as reporting
    import la_heat.final_evaluation_targets as targets

    config = replace(
        _config(tmp_path),
        locks={"target_config_semantic_sha256": "c" * 64},
        analysis={
            **_analysis(),
            "expected_key_count": 2,
            "expected_tract_count": 2,
            "expected_inventory_overpass_count": 1,
            "expected_inventory_scene_count": 2,
        },
    )
    config.paths["research_config"].parent.mkdir(parents=True, exist_ok=True)
    config.paths["research_config"].write_text(
        "synthetic unlocked research configuration",
        encoding="utf-8",
    )
    research_file_sha256 = protocol.sha256_file(
        config.paths["research_config"]
    )
    inventory_record = {
        "tract_count": 2,
        "tract_crs": "EPSG:3310",
        "locks": {
            "tract_manifest_sha256": "a" * 64,
            "primary_tract_file_sha256": "b" * 64,
        },
    }
    claim = {
        "claim_id": "source-bound-claim",
        "request": {
            "final_test_year": 2025,
            "unlock_transition": {
                "research_config_file_sha256": research_file_sha256,
                "target_config_semantic_sha256": "c" * 64,
                "unlock_final_test": True,
            },
            "landsat_inventory": inventory_record,
            "predictors": {"file_sha256": "d" * 64},
            "models": {"B1": {}, "M2": {}},
        },
    }
    tables, predictors, _ = _source_binding_tables()
    audit = {
        "state": "complete_all_inventory_dates_assessed",
        "target_row_count": 2,
        "inventory_date_count": 1,
        "tract_count": 2,
        "exact_key_universe": True,
        "static_eligible_denominator_invariant": True,
        "qa_contract_exact": True,
        "minimum_development_date_gate_applied": False,
    }
    safe_count_summary = {
        "target_audit": audit,
        "evaluation_row_count": 2,
        "inventory_date_count": 1,
        "usable_date_count": 1,
        "independent_spatial_block_count": 2,
        "tract_choropleth": {
            "tract_manifest_sha256": "a" * 64,
            "primary_tract_file_sha256": "b" * 64,
            "tract_count": 2,
            "crs": "EPSG:3310",
            "aggregation": (
                "unweighted_per_tract_mean_over_all_usable_matched_dates"
            ),
            "geometry_used_for_diagnostics_only": True,
            "coordinates_used_as_predictors": False,
        },
    }
    monkeypatch.setattr(
        protocol,
        "_default_inventory_authenticator",
        lambda _config: (SimpleNamespace(), inventory_record),
    )
    monkeypatch.setattr(
        protocol,
        "_predictor_readiness_record",
        lambda _config: (
            {},
            predictors.copy(),
            claim["request"]["predictors"],
        ),
    )
    monkeypatch.setattr(
        protocol,
        "_validate_predictor_frame",
        lambda frame, **_kwargs: frame.copy(),
    )
    monkeypatch.setattr(
        protocol,
        "load_config",
        lambda _path: SimpleNamespace(raw={}, final_test_year=2025),
    )
    monkeypatch.setattr(
        protocol,
        "target_config_sha256",
        lambda _config: "c" * 64,
    )
    monkeypatch.setattr(protocol, "_reporting_settings", lambda _config: object())
    monkeypatch.setattr(
        targets,
        "audit_final_target_artifacts",
        lambda *_args, **_kwargs: audit,
    )
    monkeypatch.setattr(
        reporting,
        "build_final_evaluation_reporting",
        _fake_source_bound_reporting,
    )
    return config, claim, tables, predictors, safe_count_summary


def test_default_config_freezes_exact_outputs_and_all_inventory_dates() -> None:
    loaded = load_final_evaluation_config()

    assert loaded.analysis["expected_key_count"] == 25_208
    assert loaded.analysis["expected_inventory_overpass_count"] == 23
    assert loaded.analysis["minimum_usable_date_count_for_metrics"] == 1
    assert loaded.publication["exact_output_files"] == list(
        EXPECTED_OUTPUT_FILES
    )


def test_claim_is_append_only_and_only_same_request_can_resume(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    request = {"frozen": "request"}

    first, first_commit = _create_or_authenticate_claim(
        config,
        request=request,
    )
    resumed, resumed_commit = _create_or_authenticate_claim(
        config,
        request=request,
    )

    assert first == resumed
    assert first_commit == resumed_commit
    with pytest.raises(FinalEvaluationProtocolError, match="another evaluation"):
        _create_or_authenticate_claim(
            config,
            request={"frozen": "different"},
        )


def test_exclusive_marker_never_overwrites_existing_bytes(tmp_path: Path) -> None:
    path = tmp_path / "MARKER.json"
    path.write_bytes(b"original")

    with pytest.raises(FinalEvaluationProtocolError, match="cannot be overwritten"):
        _exclusive_json({"state": "new"}, path, label="MARKER.json")

    assert path.read_bytes() == b"original"


def test_predictions_are_frozen_before_values_marker_and_models_never_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    formal, b1_features, m2_features = _model_contract()
    predictors = _predictors(m2_features)
    bundles = {
        "B1": _bundle("B1", b1_features, value=31.0),
        "M2": _bundle("M2", m2_features, value=30.0),
    }
    monkeypatch.setattr(
        protocol,
        "_load_predictors_after_claim",
        lambda *_args, **_kwargs: predictors,
    )
    monkeypatch.setattr(
        protocol,
        "_load_locked_models",
        lambda *_args, **_kwargs: bundles,
    )
    predictor_key_sha = protocol.canonical_frame_sha256(
        predictors,
        sort_by=["target_date", "tract_geoid"],
        columns=["tract_geoid", "target_date"],
    )
    predictor_record = {
        "file_sha256": "5" * 64,
        "key_semantic_sha256": predictor_key_sha,
    }
    claim = {
        "claim_id": "single-claim",
        "request": {
            "predictors": predictor_record,
            "models": formal["models"],
        },
    }
    claim_commit = "4" * 64
    readiness = {"request": {"predictors": predictor_record}}

    frozen = _freeze_blind_predictions(
        config,
        readiness=readiness,
        formal=formal,
        claim=claim,
        claim_commit=claim_commit,
    )

    assert len(frozen.frame) == 25_208
    assert frozen.frame["y_pred_b1"].eq(31.0).all()
    assert frozen.frame["y_pred_m2"].eq(30.0).all()
    assert config.paths["predictions_frozen"].is_file()
    assert not config.paths["values_opened"].exists()

    callback = _values_opened_callback(
        config,
        claim=claim,
        claim_commit=claim_commit,
        predictions=frozen,
        readiness=readiness,
        formal=formal,
    )
    callback()
    callback()

    opened = json.loads(config.paths["values_opened"].read_text("utf-8"))
    assert opened["state"] == "target_and_qa_values_opened"
    assert opened["blind_predictions_frozen"] is True
    assert opened["predictions_commit_sha256"] == frozen.marker["commit_sha256"]


def test_join_requires_exact_target_prediction_and_predictor_keys() -> None:
    dates = pd.to_datetime(["2025-07-01", "2025-07-01"])
    geoids = ["06037000001", "06037000002"]
    target = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            "platform": ["landsat-9", "landsat-9"],
            "spatial_block": ["b1", "b2"],
            "target_lst_c": [31.0, np.nan],
            "target_available": [True, False],
            "date_usable": [True, True],
            "relative_hotspot_top20": [True, pd.NA],
            "date_exclusion_reason": [pd.NA, pd.NA],
        }
    )
    summaries = pd.DataFrame(
        {
            "target_date": [pd.Timestamp("2025-07-01")],
            "relative_endpoint_coverage_pass": [True],
            "date_exclusion_reason": [pd.NA],
            "union_city_coverage_fraction": [1.0],
            "retained_tract_count": [1],
            "retained_tract_fraction": [0.5],
            "minimum_eligible_joint_cell_retention_fraction": [0.5],
        }
    )
    predictions = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            "y_pred_b1": [30.0, 30.0],
            "y_pred_m2": [31.0, 31.0],
        }
    )
    sentinel = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "target_date": dates,
            **{
                feature: [0.2, np.nan]
                for feature in protocol.SENTINEL_FEATURES
            },
        }
    )

    joined = _joined_reporting_rows(
        target_qa=target,
        date_summary=summaries,
        blind_predictions=predictions,
        predictors=sentinel,
    )

    assert joined["sentinel_available"].tolist() == [True, False]
    assert joined["sensor"].eq("landsat-9").all()
    with pytest.raises(FinalEvaluationProtocolError, match="not exactly unique"):
        _joined_reporting_rows(
            target_qa=target,
            date_summary=summaries,
            blind_predictions=predictions.iloc[:1],
            predictors=sentinel,
        )


def test_deep_source_binding_accepts_exact_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim, tables, _, safe_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )

    protocol._assert_source_bound_outputs(
        config,
        claim=claim,
        tables=tables,
        safe_count_summary=safe_summary,
    )


def test_deep_source_binding_rejects_predictor_provenance_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim, tables, predictors, safe_summary = (
        _source_binding_context(tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        protocol,
        "_predictor_readiness_record",
        lambda _config: (
            {},
            predictors.copy(),
            {"file_sha256": "e" * 64},
        ),
    )

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="predictors differ from the consumption claim",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )


def test_deep_source_binding_rejects_inventory_provenance_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim, tables, _, safe_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        protocol,
        "_default_inventory_authenticator",
        lambda _config: (
            SimpleNamespace(),
            {
                **claim["request"]["landsat_inventory"],
                "tract_crs": "EPSG:4326",
            },
        ),
    )

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="inventory differs from the consumption claim",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )


def test_deep_source_binding_rejects_unlock_configuration_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim, tables, _, safe_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )
    claim["request"]["unlock_transition"]["unlock_final_test"] = False

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="configuration or unlock differs",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )


@pytest.mark.parametrize(
    "drift",
    ("y_true", "y_pred", "sentinel", "date_qa", "tract_qa"),
)
def test_deep_source_binding_rejects_source_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    config, claim, tables, predictors, safe_summary = (
        _source_binding_context(tmp_path, monkeypatch)
    )
    if drift == "y_true":
        tables["final_target_qa.parquet"].loc[0, "target_lst_c"] = 99.0
    elif drift == "y_pred":
        tables["blind_predictions.parquet"].loc[0, "y_pred_m2"] = 99.0
    elif drift == "sentinel":
        predictors.loc[0, list(protocol.SENTINEL_FEATURES)] = np.nan
    elif drift == "date_qa":
        tables["date_summary.parquet"].loc[
            0,
            "relative_endpoint_coverage_pass",
        ] = False
    elif drift == "tract_qa":
        tables["final_target_qa.parquet"].loc[0, "source_scene_count"] = 99
    else:  # pragma: no cover - parameter registry is closed above.
        raise AssertionError(drift)

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="do not exactly replay",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )


def test_deep_source_binding_rejects_target_provenance_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import la_heat.final_evaluation_targets as targets

    config, claim, tables, _, safe_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )

    def reject_provenance(*_args: object, **_kwargs: object) -> None:
        raise targets.FinalEvaluationTargetError("provenance drift")

    monkeypatch.setattr(
        targets,
        "audit_final_target_artifacts",
        reject_provenance,
    )
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="source reconstruction failed",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )


def test_deep_source_binding_rejects_qa_summary_or_safe_count_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim, tables, _, safe_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )
    tables["qa_missingness_summary.csv"].loc[
        0,
        "inventory_key_count",
    ] = 999
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="QA missingness summary does not replay",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=tables,
            safe_count_summary=safe_summary,
        )

    _, _, clean_tables, _, clean_summary = _source_binding_context(
        tmp_path,
        monkeypatch,
    )
    clean_summary["evaluation_row_count"] = 999
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="safe-count summary does not replay",
    ):
        protocol._assert_source_bound_outputs(
            config,
            claim=claim,
            tables=clean_tables,
            safe_count_summary=clean_summary,
        )


def test_execution_stops_before_models_or_targets_without_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        protocol,
        "load_final_evaluation_config",
        lambda _path=protocol.DEFAULT_CONFIG_PATH: config,
    )
    reached_model_or_target = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal reached_model_or_target
        reached_model_or_target = True
        raise AssertionError

    monkeypatch.setattr(protocol, "_load_locked_models", forbidden)

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="final-evaluation readiness",
    ):
        execute_locked_final_evaluation()

    assert reached_model_or_target is False


def test_predictor_partial_sentinel_missingness_is_rejected(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        analysis={
            **_analysis(),
            "expected_key_count": 2,
            "expected_tract_count": 2,
            "expected_inventory_overpass_count": 1,
        },
    )
    formal, _, features = _model_contract()
    predictors = pd.DataFrame(
        {
            "tract_geoid": ["06037000001", "06037000002"],
            "target_date": pd.to_datetime(["2025-07-01", "2025-07-01"]),
            **{feature: [1.0, 1.0] for feature in features},
        }
    )
    predictors.loc[0, protocol.SENTINEL_FEATURES[0]] = np.nan

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="all-five or none",
    ):
        protocol._validate_predictor_frame(
            predictors,
            formal=formal,
            config=config,
        )


def test_claim_request_is_stable_across_documentation_only_descendant_heads(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    readiness = {
        "request_sha256": "1" * 64,
        "formal_model_lock": {"sha256": "2" * 64},
        "request": {
            "pipeline_sha256": "3" * 64,
            "models": {"B1": {"locked": True}, "M2": {"locked": True}},
            "predictors": {"key_semantic_sha256": "4" * 64},
            "landsat_inventory": {"key_semantic_sha256": "4" * 64},
            "paths": {"complete": "complete.json"},
        },
    }
    base_unlock = {
        "authorized_git_commit": "5" * 40,
        "research_config_file_sha256": "6" * 64,
        "target_config_semantic_sha256": "7" * 64,
        "unlock_final_test": True,
        "unlocked_git_commit": "8" * 40,
        "changed_paths": ["configs/research.toml"],
    }
    after_handoff_commit = {
        **base_unlock,
        "unlocked_git_commit": "9" * 40,
        "changed_paths": [
            "configs/research.toml",
            "docs/DECISION_LOG.md",
            "docs/PROJECT_HANDOFF.md",
        ],
    }

    first = protocol._claim_request(
        config,
        readiness=readiness,
        readiness_commit="a" * 64,
        authorization_commit="b" * 64,
        unlock=base_unlock,
    )
    second = protocol._claim_request(
        config,
        readiness=readiness,
        readiness_commit="a" * 64,
        authorization_commit="b" * 64,
        unlock=after_handoff_commit,
    )

    assert first == second
    assert "unlocked_git_commit" not in str(first)


def test_prediction_key_tamper_is_rejected_before_values_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    formal, b1_features, m2_features = _model_contract()
    predictors = _predictors(m2_features)
    bundles = {
        "B1": _bundle("B1", b1_features, value=31.0),
        "M2": _bundle("M2", m2_features, value=30.0),
    }
    predictor_key_sha = protocol.canonical_frame_sha256(
        predictors,
        sort_by=["target_date", "tract_geoid"],
        columns=["tract_geoid", "target_date"],
    )
    predictor_record = {
        "file_sha256": "5" * 64,
        "key_semantic_sha256": predictor_key_sha,
    }
    claim = {
        "claim_id": "single-claim",
        "request": {
            "predictors": predictor_record,
            "models": formal["models"],
        },
    }
    claim_commit = "4" * 64
    monkeypatch.setattr(
        protocol,
        "_load_predictors_after_claim",
        lambda *_args, **_kwargs: predictors,
    )
    monkeypatch.setattr(
        protocol,
        "_load_locked_models",
        lambda *_args, **_kwargs: bundles,
    )
    frozen = _freeze_blind_predictions(
        config,
        readiness={"request": {"predictors": predictor_record}},
        formal=formal,
        claim=claim,
        claim_commit=claim_commit,
    )

    wrong = frozen.frame.copy()
    wrong.loc[0, "tract_geoid"] = "06037999999"
    output_path = config.paths["staging_root"] / "blind_predictions.parquet"
    protocol.atomic_parquet(wrong, output_path)
    marker = json.loads(
        config.paths["predictions_frozen"].read_text(encoding="utf-8")
    )
    marker.pop("commit_sha256")
    marker["output"] = protocol._prediction_output_record(output_path, wrong)
    marker["commit_sha256"] = protocol.canonical_sha256(marker)
    config.paths["predictions_frozen"].write_text(
        json.dumps(marker, indent=2),
        encoding="utf-8",
    )

    callback = _values_opened_callback(
        config,
        claim=claim,
        claim_commit=claim_commit,
        predictions=frozen,
        readiness={"request": {"predictors": predictor_record}},
        formal=formal,
    )
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="exact current claim",
    ):
        callback()
    assert not config.paths["values_opened"].exists()

    wrong_values = frozen.frame.copy()
    wrong_values["y_pred_m2"] = 999.0
    protocol.atomic_parquet(wrong_values, output_path)
    marker.pop("commit_sha256")
    marker["output"] = protocol._prediction_output_record(
        output_path,
        wrong_values,
    )
    marker["commit_sha256"] = protocol.canonical_sha256(marker)
    config.paths["predictions_frozen"].write_text(
        json.dumps(marker, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="do not reproduce",
    ):
        callback()
    assert not config.paths["values_opened"].exists()


def test_completion_authenticates_value_boundary_before_output_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    claim = protocol._committed_payload(
        {
            "schema_version": 1,
            "claim_id": "single-claim",
            "request": {},
        }
    )
    protocol._exclusive_json(
        claim,
        config.paths["claim"],
        label="claim",
    )
    complete = protocol._committed_payload(
        {
            "schema_version": 1,
            "algorithm_version": protocol.COMPLETION_ALGORITHM_VERSION,
            "state": "complete_one_time_final_evaluation",
            "completed": True,
            "claim_id": "single-claim",
            "claim_commit_sha256": claim["commit_sha256"],
        }
    )
    protocol._exclusive_json(
        complete,
        config.paths["complete"],
        label="complete",
    )
    output_touched = False

    def reject_prediction_marker(*_args: object, **kwargs: object):
        assert kwargs["read_frame"] is False
        raise FinalEvaluationProtocolError("prediction boundary failed")

    def forbidden_output(*_args: object, **_kwargs: object):
        nonlocal output_touched
        output_touched = True
        raise AssertionError

    monkeypatch.setattr(
        protocol,
        "_authenticate_prediction_marker",
        reject_prediction_marker,
    )
    monkeypatch.setattr(
        protocol,
        "_authenticate_output_commit",
        forbidden_output,
    )
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="prediction boundary failed",
    ):
        protocol.authenticate_completed_final_evaluation(config)
    assert output_touched is False


def test_committed_staging_recovery_promotes_without_recomputation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    staging = config.paths["staging_root"]
    staging.mkdir(parents=True)
    committed_bytes = b'{"state":"already-committed"}'
    (staging / "EVALUATION_COMMIT.json").write_bytes(committed_bytes)
    claim = {"claim_id": "single-claim"}
    claim_commit = "c" * 64
    observed_directories: list[Path] = []

    monkeypatch.setattr(
        protocol,
        "_authenticate_prediction_marker",
        lambda *_args, **_kwargs: (
            {
                "claim_id": "single-claim",
                "output": {"filename": "blind_predictions.parquet"},
            },
            "p" * 64,
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(
        protocol,
        "_authenticate_values_opened",
        lambda *_args, **_kwargs: ({}, "v" * 64),
    )

    def authenticate_output(directory: Path, **_kwargs: object):
        observed_directories.append(directory)
        assert (directory / "EVALUATION_COMMIT.json").read_bytes() == committed_bytes
        return (
            {
                "predictions_commit_sha256": "p" * 64,
                "values_opened_commit_sha256": "v" * 64,
            },
            "o" * 64,
        )

    monkeypatch.setattr(
        protocol,
        "_authenticate_output_commit",
        authenticate_output,
    )
    monkeypatch.setattr(
        protocol,
        "_publish_completion",
        lambda *_args, **_kwargs: {"state": "recovered"},
    )

    result = protocol._recover_committed_staging(
        config,
        claim=claim,
        claim_commit=claim_commit,
    )

    assert result == {"state": "recovered"}
    assert not staging.exists()
    assert (
        config.paths["final_output_directory"] / "EVALUATION_COMMIT.json"
    ).read_bytes() == committed_bytes
    assert observed_directories == [
        staging,
        config.paths["final_output_directory"],
    ]


def test_staged_output_contract_rejects_unexpected_directory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    staging = config.paths["staging_root"]
    staging.mkdir(parents=True)
    for filename in EXPECTED_OUTPUT_FILES:
        if filename != "EVALUATION_COMMIT.json":
            (staging / filename).write_bytes(b"placeholder")
    (staging / "unexpected-directory").mkdir()

    with pytest.raises(
        FinalEvaluationProtocolError,
        match="exact regular files",
    ):
        protocol._staged_output_records(
            config,
            claim={},
            expected_prediction_output={},
            safe_count_summary={},
        )


def test_output_table_reader_preserves_geoids_and_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    filename = "tract_choropleth_summary.csv"
    columns = protocol._output_table_column_contracts()[filename]
    row: dict[str, object] = {
        column: np.nan for column in columns
    }
    row.update(
        {
            "tract_geoid": "06037000001",
            "spatial_block": "block-a",
            "evaluated_date_count": 0,
            "evaluated_date_fraction": 0.0,
        }
    )
    path = tmp_path / filename
    pd.DataFrame([row], columns=columns).to_csv(path, index=False)

    observed = protocol._read_output_table(path, filename=filename)

    assert observed.loc[0, "tract_geoid"] == "06037000001"
    assert str(observed["tract_geoid"].dtype) == "string"

    pd.DataFrame([row, row], columns=columns).to_csv(path, index=False)
    with pytest.raises(FinalEvaluationProtocolError, match="not unique"):
        protocol._read_output_table(path, filename=filename)


def test_output_table_reader_rejects_non_boolean_gate_values(
    tmp_path: Path,
) -> None:
    filename = "protocol_gates.csv"
    columns = protocol._output_table_column_contracts()[filename]
    row: dict[str, object] = {column: 0 for column in columns}
    row.update(
        {
            "gate_id": "median_per_date_spearman",
            "comparison": ">=",
            "passed": "not-a-boolean",
            "required_for_protocol_success": True,
            "overall_protocol_success_gate_pass": True,
            "interpretation": "test",
        }
    )
    path = tmp_path / filename
    pd.DataFrame([row], columns=columns).to_csv(path, index=False)

    with pytest.raises(FinalEvaluationProtocolError, match="boolean domain"):
        protocol._read_output_table(path, filename=filename)


def test_output_table_reader_requires_unique_midnight_civil_dates(
    tmp_path: Path,
) -> None:
    filename = "per_date_metrics.csv"
    columns = protocol._output_table_column_contracts()[filename]
    row: dict[str, object] = {column: 0 for column in columns}
    row.update(
        {
            "model_id": "B1",
            "model_role": "legal_baseline",
            "target_date": "2025-07-01 12:00:00",
            "spearman_defined": True,
        }
    )
    path = tmp_path / filename
    pd.DataFrame([row], columns=columns).to_csv(path, index=False)
    with pytest.raises(FinalEvaluationProtocolError, match="date domain"):
        protocol._read_output_table(path, filename=filename)

    first = {**row, "target_date": "2025-07-01"}
    second = {**row, "target_date": "2025-07-01 00:00:00"}
    pd.DataFrame([first, second], columns=columns).to_csv(path, index=False)
    with pytest.raises(FinalEvaluationProtocolError, match="not unique"):
        protocol._read_output_table(path, filename=filename)

    zoned = {**row, "target_date": "2025-07-01T00:00:00+00:00"}
    pd.DataFrame([zoned], columns=columns).to_csv(path, index=False)
    with pytest.raises(FinalEvaluationProtocolError, match="date domain"):
        protocol._read_output_table(path, filename=filename)


def test_prediction_output_record_must_match_frozen_marker() -> None:
    marker = {
        "filename": "blind_predictions.parquet",
        "sha256": "a" * 64,
        "bytes": 100,
        "rows": 2,
        "schema_sha256": "b" * 64,
        "semantic_sha256": "c" * 64,
        "key_semantic_sha256": "d" * 64,
    }
    output = {"path": "blind_predictions.parquet", **marker}
    output.pop("filename")
    protocol._assert_prediction_output_binding(output, marker)

    output["sha256"] = "e" * 64
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="PREDICTIONS_FROZEN",
    ):
        protocol._assert_prediction_output_binding(output, marker)


def test_claim_freezes_authenticated_tract_geometry_contract(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        analysis={**_analysis(), "expected_tract_count": 2},
    )
    claim = {
        "claim_id": "claim",
        "request": {
            "landsat_inventory": {
                "tract_count": 2,
                "tract_crs": "EPSG:3310",
                "locks": {
                    "tract_manifest_sha256": "a" * 64,
                    "primary_tract_file_sha256": "b" * 64,
                },
            }
        },
    }

    assert protocol._claim_tract_geometry_contract(
        claim,
        config=config,
    ) == {
        "tract_manifest_sha256": "a" * 64,
        "primary_tract_file_sha256": "b" * 64,
        "tract_count": 2,
        "crs": "EPSG:3310",
    }

    claim["request"]["landsat_inventory"]["tract_crs"] = ""
    with pytest.raises(
        FinalEvaluationProtocolError,
        match="geometry contract",
    ):
        protocol._claim_tract_geometry_contract(claim, config=config)
