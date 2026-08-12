from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.multicity.model_fit_prediction as runner
from la_heat.multicity.model_fit_prediction import (
    ModelFitPredictionError,
    fit_and_predict,
    prepare_fit_data,
    run_model_fit_prediction,
)
from la_heat.multicity.transfer_model import load_frozen_transfer_contract
from la_heat.provenance import canonical_sha256, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame, dict[str, object]]:
    contract = load_frozen_transfer_contract(PROJECT_ROOT)
    features = list(contract["feature_registry"]["feature_order"])
    rng = np.random.default_rng(20260812)
    city_dates = [
        ("los_angeles_ca", "2020-06-01", 15),
        ("los_angeles_ca", "2021-06-01", 15),
        ("los_angeles_ca", "2022-06-01", 15),
        ("los_angeles_ca", "2023-06-01", 15),
        ("los_angeles_ca", "2024-06-01", 15),
        ("los_angeles_ca", "2024-08-01", 15),
        ("phoenix_az", "2025-06-01", 10),
        ("houston_tx", "2025-06-02", 10),
        ("chicago_il", "2025-06-03", 10),
    ]
    rows: list[dict[str, object]] = []
    for city_id, target_date, count in city_dates:
        for index in range(count):
            values = rng.normal(size=len(features))
            row: dict[str, object] = {
                "city_id": city_id,
                "tract_geoid": f"{city_id[:2]}{index:09d}",
                "target_date": target_date,
            }
            row.update(zip(features, values, strict=True))
            rows.append(row)
    predictors = pd.DataFrame(rows)
    la = predictors.loc[predictors["city_id"].eq("los_angeles_ca")]
    targets = la.loc[:, ["city_id", "tract_geoid", "target_date"]].copy()
    target_values = 29.0 + 1.3 * la[features[0]].to_numpy() - 0.7 * la[features[3]].to_numpy()
    targets["target_lst_c"] = target_values
    targets["target_available"] = True
    targets["date_usable"] = True
    targets["tract_exclusion_reason"] = ""
    targets["date_exclusion_reason"] = ""
    targets.loc[targets.index[0], "target_available"] = False
    targets.loc[targets.index[0], "target_lst_c"] = np.nan
    targets.loc[targets.index[0], "tract_exclusion_reason"] = "cloud"
    targets.loc[targets["target_date"].eq("2024-08-01"), "date_usable"] = False
    targets.loc[targets["target_date"].eq("2024-08-01"), "date_exclusion_reason"] = "low_retention"
    cohorts: dict[str, object] = {
        "training_city_id": "los_angeles_ca",
        "training_years": [2020, 2021, 2022, 2023],
        "training_rows": 60,
        "calibration_city_id": "los_angeles_ca",
        "calibration_year": 2024,
        "calibration_rows": 30,
        "external_city_ids": ["phoenix_az", "houston_tx", "chicago_il"],
        "external_year": 2025,
        "external_rows": 30,
        "external_city_dates": 3,
    }
    return contract, predictors, targets.reset_index(drop=True), cohorts


def test_qa_join_is_explicit_and_external_predictions_need_no_external_target() -> None:
    contract, predictors, targets, cohorts = _inputs()
    prepared = prepare_fit_data(predictors, targets, contract, cohorts)
    stages: list[str] = []
    result = fit_and_predict(
        prepared,
        contract,
        progress=lambda stage, _detail=None: stages.append(stage),
    )

    assert len(prepared.training_predictors) == 59
    assert len(prepared.calibration_predictors) == 15
    assert prepared.selection_audit["training"]["candidate_rows"] == 60
    assert prepared.selection_audit["training"]["usable_rows"] == 59
    assert prepared.selection_audit["calibration"]["candidate_rows"] == 30
    assert prepared.selection_audit["calibration"]["usable_rows"] == 15
    assert stages == ["fit_b1", "fit_m2", "fit_q05", "fit_q95", "calibrate", "predict"]
    assert len(result.predictions) == 30
    assert tuple(result.predictions.columns) == runner.PREDICTION_COLUMNS
    assert set(result.predictions["city_id"]) == {
        "phoenix_az",
        "houston_tx",
        "chicago_il",
    }
    assert "target_lst_c" not in result.predictions.columns


def test_prepare_rejects_any_external_target_row() -> None:
    contract, predictors, targets, cohorts = _inputs()
    contaminated = pd.concat(
        [
            targets,
            pd.DataFrame(
                [
                    {
                        "city_id": "phoenix_az",
                        "tract_geoid": "external",
                        "target_date": "2025-06-01",
                        "target_lst_c": 40.0,
                        "target_available": True,
                        "date_usable": True,
                        "tract_exclusion_reason": "",
                        "date_exclusion_reason": "",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    with pytest.raises(ModelFitPredictionError, match="only Los Angeles"):
        prepare_fit_data(predictors, contaminated, contract, cohorts)


def _committed(path: Path, payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def test_production_runner_commits_once_and_check_only_does_not_refit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract, predictors, targets, cohorts = _inputs()
    predictor_path = tmp_path / "predictors.parquet"
    target_directory = tmp_path / "source" / "cities" / "los_angeles_ca"
    target_path = target_directory / "targets.parquet"
    predictors.to_parquet(predictor_path, index=False)
    target_directory.mkdir(parents=True, exist_ok=True)
    targets.to_parquet(target_path, index=False)
    target_record = {
        "bytes": target_path.stat().st_size,
        "sha256": sha256_file(target_path),
        "rows": len(targets),
    }
    city_commit_path = target_directory / "CITY_TARGETS_COMPLETE.json"
    city_commit = _committed(
        city_commit_path,
        {"state": "complete", "output_files": {"targets.parquet": target_record}},
    )
    source_path = tmp_path / "LA_SOURCE_TARGETS_COMPLETE.json"
    source = _committed(
        source_path,
        {
            "state": "la_source_targets_complete",
            "city_target_commit": {
                "path": city_commit_path.relative_to(PROJECT_ROOT).as_posix(),
                "commit_sha256": city_commit["commit_sha256"],
                "output_files": {"targets.parquet": target_record},
            },
        },
    )
    model_root = tmp_path / "models"
    completion_path = tmp_path / "MODEL_FIT_COMPLETE.json"
    prediction_commit_path = tmp_path / "EXTERNAL_PREDICTIONS_COMMITTED.json"
    authorization: dict[str, object] = {
        "state": "model_fit_authorized",
        "claim_id": "fit-claim",
        "commit_sha256": "a" * 64,
        "protocol_model_lock_commit_sha256": "b" * 64,
        "source_completion_commit_sha256": source["commit_sha256"],
        "predictor_complete_commit_sha256": "c" * 64,
        "source_targets_complete": {"path": source_path.relative_to(PROJECT_ROOT).as_posix()},
        "predictor_table": {
            "path": predictor_path.relative_to(PROJECT_ROOT).as_posix(),
            "bytes": predictor_path.stat().st_size,
            "sha256": sha256_file(predictor_path),
        },
        "cohorts": cohorts,
        "output_contract": {
            "model_root": model_root.relative_to(PROJECT_ROOT).as_posix(),
            "model_artifact_path": (model_root / runner.MODEL_FILENAME)
            .relative_to(PROJECT_ROOT)
            .as_posix(),
            "fit_audit_path": (model_root / runner.FIT_AUDIT_FILENAME)
            .relative_to(PROJECT_ROOT)
            .as_posix(),
            "external_predictions_path": (model_root / runner.PREDICTION_FILENAME)
            .relative_to(PROJECT_ROOT)
            .as_posix(),
            "completion_manifest": completion_path.relative_to(PROJECT_ROOT).as_posix(),
            "external_prediction_commit_manifest": prediction_commit_path.relative_to(
                PROJECT_ROOT
            ).as_posix(),
        },
    }
    monkeypatch.setattr(
        runner,
        "authenticate_model_fit_authorization",
        lambda _root, _path: authorization,
    )
    monkeypatch.setattr(runner, "load_frozen_transfer_contract", lambda _root: contract)

    completed = run_model_fit_prediction(
        PROJECT_ROOT,
        authorization_path=tmp_path / "fake-authorization.json",
        status_path=tmp_path / "status.json",
    )
    assert completed["state"] == runner.COMPLETION_STATE
    assert completed["outputs"]["external_predictions"]["rows"] == 30
    assert completed["access_audit"]["external_target_values_read"] is False
    publication = json.loads(prediction_commit_path.read_text(encoding="utf-8"))
    assert publication["state"] == runner.PREDICTION_COMMIT_STATE
    assert publication["row_count"] == 30
    assert publication["city_date_count"] == 3
    assert publication["external_target_or_qa_values_read"] is False
    assert completed["external_prediction_commit_sha256"] == publication["commit_sha256"]
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["stage"] == "complete"

    monkeypatch.setattr(
        runner,
        "fit_and_predict",
        lambda *_args, **_kwargs: pytest.fail("completed G5 must not refit"),
    )
    checked = run_model_fit_prediction(
        PROJECT_ROOT,
        authorization_path=tmp_path / "fake-authorization.json",
        status_path=tmp_path / "status.json",
        check_only=True,
    )
    assert checked == completed

    tampered_publication = json.loads(prediction_commit_path.read_text(encoding="utf-8"))
    tampered_publication["calibration_commit_sha256"] = "f" * 64
    tampered_publication.pop("commit_sha256")
    tampered_publication["commit_sha256"] = canonical_sha256(tampered_publication)
    prediction_commit_path.write_text(json.dumps(tampered_publication), encoding="utf-8")
    with pytest.raises(ModelFitPredictionError, match="byte lock|publication binding"):
        run_model_fit_prediction(
            PROJECT_ROOT,
            authorization_path=tmp_path / "fake-authorization.json",
            status_path=tmp_path / "status.json",
            check_only=True,
        )


def test_completion_rejects_claimed_external_qa_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = {
        "commit_sha256": "a" * 64,
        "claim_id": "fit-claim",
        "protocol_model_lock_commit_sha256": "b" * 64,
        "source_completion_commit_sha256": "c" * 64,
        "predictor_complete_commit_sha256": "d" * 64,
        "cohorts": {"external_rows": 1},
    }
    completion = {
        "state": runner.COMPLETION_STATE,
        "authorization_commit_sha256": authorization["commit_sha256"],
        "claim_id": authorization["claim_id"],
        "protocol_model_lock_commit_sha256": authorization["protocol_model_lock_commit_sha256"],
        "source_completion_commit_sha256": authorization["source_completion_commit_sha256"],
        "predictor_complete_commit_sha256": authorization["predictor_complete_commit_sha256"],
        "cohorts": {"candidate": authorization["cohorts"]},
        "access_audit": {
            "la_source_target_values_read": True,
            "external_target_values_read": False,
            "external_target_or_qa_files_read": ["forbidden.parquet"],
            "external_prediction_created_before_external_target_claim": True,
            "model_selection_or_retuning_performed": False,
        },
    }
    completion["commit_sha256"] = canonical_sha256(completion)
    completion_path = tmp_path / "MODEL_FIT_COMPLETE.json"
    completion_path.write_text(json.dumps(completion), encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "authenticate_model_fit_authorization",
        lambda *_args, **_kwargs: authorization,
    )

    with pytest.raises(ModelFitPredictionError, match="identity changed"):
        runner.authenticate_model_fit_completion(
            PROJECT_ROOT,
            completion_path,
            authorization_path=tmp_path / "MODEL_FIT_AUTHORIZATION.json",
        )
