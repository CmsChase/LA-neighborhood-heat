from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.multicity.synthetic_smoke import (
    ARTIFACT_SCOPE,
    CITY_IDS,
    SyntheticSmokeError,
    build_external_predictions,
    build_loco_folds,
    fit_predict_loco_fold,
    make_synthetic_inputs,
    run_synthetic_smoke,
    validate_loco_folds,
    validate_synthetic_output_directory,
)
from la_heat.multicity.transfer_model import load_frozen_transfer_contract
from la_heat.provenance import sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _contract() -> dict[str, object]:
    return load_frozen_transfer_contract(PROJECT_ROOT)


def test_end_to_end_smoke_is_deterministic_and_explicitly_non_evidence(
    tmp_path: Path,
) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first = run_synthetic_smoke(PROJECT_ROOT, first_directory)
    second = run_synthetic_smoke(PROJECT_ROOT, second_directory)

    assert first == second
    assert first["state"] == "complete_synthetic_smoke_not_scientific_evidence"
    assert first["synthetic_only"] is True
    assert first["scientific_evidence"] is False
    assert first["guardrails"] == {
        "real_predictor_files_read": [],
        "real_target_files_read": [],
        "canonical_files_written": [],
        "external_target_passed_to_fit_or_prediction": False,
        "external_target_used_only_after_prediction": True,
        "city_or_geoid_used_as_model_feature": False,
        "fold_local_preprocessing": True,
        "every_loco_city_held_out_once": True,
    }
    assert first["model_audit"]["loco_fold_count"] == 4
    assert first["model_audit"]["b1_feature_count"] == 23
    assert first["model_audit"]["m2_feature_count"] == 46

    expected_files = {
        "synthetic_loco_fold_audit.csv",
        "synthetic_loco_predictions.csv",
        "synthetic_loco_date_metrics.csv",
        "synthetic_loco_city_metrics.csv",
        "synthetic_external_predictions.csv",
        "synthetic_external_date_metrics.csv",
        "synthetic_external_city_metrics.csv",
        "synthetic_external_reliability.csv",
        "synthetic_smoke_metrics.png",
        "synthetic_smoke_summary.json",
    }
    assert {path.name for path in first_directory.iterdir()} == expected_files
    assert {path.name for path in second_directory.iterdir()} == expected_files
    for filename in expected_files:
        assert sha256_file(first_directory / filename) == sha256_file(
            second_directory / filename
        )

    audit = pd.read_csv(first_directory / "synthetic_loco_fold_audit.csv")
    assert set(audit["held_out_city"]) == set(CITY_IDS)
    assert audit["held_out_target_read_before_prediction"].eq(False).all()  # noqa: E712
    for row in audit.itertuples(index=False):
        training_cities = set(row.training_city_ids.split("|"))
        assert row.held_out_city not in training_cities
        assert training_cities == set(CITY_IDS) - {row.held_out_city}

    external = pd.read_csv(first_directory / "synthetic_external_predictions.csv")
    assert set(external["city_id"]) == {"phoenix_az", "houston_tx", "chicago_il"}
    assert external["artifact_scope"].eq(ARTIFACT_SCOPE).all()
    assert external["scientific_evidence"].eq(False).all()  # noqa: E712
    assert (external["m2_upper_c"] >= external["m2_lower_c"]).all()
    assert (first_directory / "synthetic_smoke_metrics.png").stat().st_size > 10_000


def test_loco_prediction_does_not_inspect_held_out_labels() -> None:
    contract = _contract()
    inputs = make_synthetic_inputs(contract)
    fold = next(
        candidate
        for candidate in build_loco_folds(inputs.loco_predictors)
        if candidate.held_out_city == "los_angeles_ca"
    )
    original, _ = fit_predict_loco_fold(
        inputs.loco_predictors,
        inputs.loco_target,
        contract,
        fold,
    )
    poisoned = inputs.loco_target.copy()
    poisoned.iloc[list(fold.test_positions)] = np.nan
    replay, _ = fit_predict_loco_fold(
        inputs.loco_predictors,
        poisoned,
        contract,
        fold,
    )

    pd.testing.assert_frame_equal(original, replay)
    assert "external_target" not in inspect.signature(build_external_predictions).parameters


def test_split_and_predictor_leakage_guards_fail_closed() -> None:
    contract = _contract()
    inputs = make_synthetic_inputs(contract)
    folds = build_loco_folds(inputs.loco_predictors)
    first = folds[0]
    leaked = replace(
        first,
        train_positions=(*first.train_positions, first.test_positions[0]),
    )
    with pytest.raises(SyntheticSmokeError, match="overlaps or drops rows"):
        validate_loco_folds(inputs.loco_predictors, (leaked, *folds[1:]))

    missing_city = inputs.loco_predictors.loc[
        inputs.loco_predictors["city_id"].ne("chicago_il")
    ]
    with pytest.raises(SyntheticSmokeError, match="exact four-city universe"):
        build_loco_folds(missing_city)

    predictor_leak = inputs.loco_predictors.copy()
    predictor_leak["synthetic_target_c"] = inputs.loco_target
    with pytest.raises(SyntheticSmokeError, match="target, label, audit"):
        fit_predict_loco_fold(predictor_leak, inputs.loco_target, contract, first)


@pytest.mark.parametrize(
    "directory",
    ["data", "manifests", "reports", "exports", "atlas", ".git", ".github", "tools"],
)
def test_smoke_output_rejects_canonical_project_trees(directory: str) -> None:
    with pytest.raises(SyntheticSmokeError, match="must stay under '.tmp'"):
        validate_synthetic_output_directory(PROJECT_ROOT, PROJECT_ROOT / directory / "smoke")


def test_smoke_output_allows_only_project_tmp_or_external_paths(tmp_path: Path) -> None:
    project_smoke = PROJECT_ROOT / ".tmp" / "multicity-smoke"
    assert validate_synthetic_output_directory(PROJECT_ROOT, project_smoke) == (
        project_smoke.resolve()
    )
    assert validate_synthetic_output_directory(PROJECT_ROOT, tmp_path) == tmp_path.resolve()
