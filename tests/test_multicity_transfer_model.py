from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.multicity.transfer_model import (
    TransferModelError,
    build_frozen_transfer_estimators,
    calibrate_frozen_intervals,
    fit_frozen_transfer_models,
    load_frozen_transfer_contract,
    predict_external_cities,
    weighted_contract_quantile,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _contract() -> dict[str, object]:
    return load_frozen_transfer_contract(PROJECT_ROOT)


def _cohort(
    contract: dict[str, object],
    city_dates: list[tuple[str, str]],
    *,
    tracts_per_date: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    features = contract["feature_registry"]["feature_order"]
    rows: list[dict[str, object]] = []
    targets: list[float] = []
    for city_index, (city_id, target_date) in enumerate(city_dates):
        for tract_index in range(tracts_per_date):
            values = rng.normal(size=len(features))
            row: dict[str, object] = {
                "city_id": city_id,
                "tract_geoid": f"{city_index:02d}{tract_index:09d}",
                "target_date": target_date,
            }
            row.update(zip(features, values, strict=True))
            rows.append(row)
            targets.append(
                28.0
                + 1.8 * values[0]
                - 1.2 * values[3]
                + 0.8 * values[-1]
                + rng.normal(scale=0.35)
            )
    frame = pd.DataFrame(rows)
    return frame, pd.Series(targets, index=frame.index, name="synthetic_lst_c")


def test_frozen_estimator_factories_use_transfer_parameters() -> None:
    estimators = build_frozen_transfer_estimators(_contract())

    ridge = estimators.b1.named_steps["model"]
    point = estimators.m2.named_steps["model"]
    lower = estimators.lower.named_steps["model"]
    upper = estimators.upper.named_steps["model"]
    assert len(estimators.b1_feature_order) == 23
    assert len(estimators.m2_feature_order) == 46
    assert "pacific_coast_distance_mean_km" not in estimators.m2_feature_order
    assert (
        "gshhg_ocean_great_lakes_shore_distance_mean_km"
        in estimators.m2_feature_order
    )
    assert ridge.alpha == 10.0
    assert ridge.solver == "lsqr"
    assert ridge.tol == 0.0001
    assert point.loss == "absolute_error"
    assert point.min_samples_leaf == 50
    assert point.max_iter == 300
    assert point.early_stopping is False
    assert lower.loss == upper.loss == "quantile"
    assert lower.quantile == 0.05
    assert upper.quantile == 0.95


def test_weighted_step_quantile_uses_value_date_geoid_order() -> None:
    values = np.array([3.0, 1.0, 2.0, 2.0])
    weights = np.array([1.0, 1.0, 1.0, 1.0])
    dates = pd.Series(["2024-06-02", "2024-06-01", "2024-06-02", "2024-06-01"])
    geoids = pd.Series(["d", "a", "c", "b"])

    assert weighted_contract_quantile(values, weights, dates, geoids, 0.50) == 2.0
    assert weighted_contract_quantile(values, weights, dates, geoids, 0.90) == 3.0


def test_training_rejects_los_angeles_2024_rows() -> None:
    contract = _contract()
    frame, target = _cohort(
        contract,
        [("los_angeles_ca", "2024-07-01")],
        tracts_per_date=4,
        seed=6,
    )

    with pytest.raises(TransferModelError, match="city/year contract"):
        fit_frozen_transfer_models(frame, target, contract)


def test_synthetic_fit_calibration_and_external_prediction() -> None:
    contract = _contract()
    training, y_train = _cohort(
        contract,
        [
            ("los_angeles_ca", "2020-07-01"),
            ("los_angeles_ca", "2021-07-01"),
            ("los_angeles_ca", "2022-07-01"),
            ("los_angeles_ca", "2023-07-01"),
        ],
        tracts_per_date=60,
        seed=7,
    )
    calibration_frame, y_calibration = _cohort(
        contract,
        [
            ("los_angeles_ca", "2024-06-15"),
            ("los_angeles_ca", "2024-08-15"),
        ],
        tracts_per_date=60,
        seed=8,
    )
    external, _unused_external_target = _cohort(
        contract,
        [
            ("phoenix_az", "2025-06-10"),
            ("houston_tx", "2025-06-11"),
            ("chicago_il", "2025-06-12"),
        ],
        tracts_per_date=20,
        seed=9,
    )

    models = fit_frozen_transfer_models(training, y_train, contract)
    calibration = calibrate_frozen_intervals(
        models, calibration_frame, y_calibration, contract
    )
    predictions = predict_external_cities(models, calibration, external)

    assert models.training_row_count == 240
    assert models.training_date_count == 4
    assert calibration.calibration_row_count == 120
    assert calibration.calibration_date_count == 2
    assert calibration.nonconformity_quantile_c >= 0
    assert len(predictions) == 60
    assert set(predictions["city_id"]) == {"phoenix_az", "houston_tx", "chicago_il"}
    assert (predictions["m2_upper_c"] >= predictions["m2_lower_c"]).all()
    assert predictions["m2_accepted"].eq(~predictions["m2_abstain"]).all()
    assert "synthetic_lst_c" not in predictions
