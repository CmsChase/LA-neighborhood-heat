import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

from la_heat.calendar_features import build_calendar_features
from la_heat.training_contract import (
    TrainingContractError,
    date_balanced_sample_weights,
    prepare_b0_date_mean_training,
)


def _unequal_date_rows() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    dates = ["2023-01-01"] + ["2023-04-01"] * 2 + ["2023-07-01"] * 5
    keys = pd.DataFrame(
        {
            "tract_geoid": [f"g{index}" for index in range(len(dates))],
            "target_date": pd.to_datetime(dates),
        }
    )
    calendar = build_calendar_features(keys)
    aligned = keys.merge(calendar, on=["tract_geoid", "target_date"], validate="one_to_one")
    features = aligned[["calendar_doy_sin", "calendar_doy_cos"]]
    target = pd.Series([10.0, 20.0, 22.0, 30.0, 32.0, 34.0, 36.0, 38.0])
    return keys, features, target


def test_date_balanced_weights_give_every_date_equal_total_mass() -> None:
    keys, _, _ = _unequal_date_rows()
    weights = date_balanced_sample_weights(keys)
    totals = pd.DataFrame(
        {"target_date": pd.to_datetime(keys["target_date"]), "weight": weights}
    ).groupby("target_date")["weight"].sum()

    assert weights.mean() == pytest.approx(1.0)
    np.testing.assert_allclose(totals.to_numpy(), np.repeat(len(keys) / 3, 3))


def test_b0_uses_one_equal_weight_training_mean_per_date() -> None:
    keys, features, target = _unequal_date_rows()

    prepared = prepare_b0_date_mean_training(features, target, keys)
    fitted = LinearRegression().fit(prepared.features, prepared.target)
    direct_date_means = (
        pd.DataFrame(
            {
                "target_date": pd.to_datetime(keys["target_date"]),
                "target": target,
            }
        )
        .groupby("target_date")["target"]
        .mean()
        .to_numpy()
    )

    assert len(prepared.features) == 3
    np.testing.assert_allclose(prepared.target, direct_date_means)
    np.testing.assert_allclose(fitted.predict(prepared.features), direct_date_means)
    assert prepared.tract_row_counts.tolist() == [1, 2, 5]


def test_b0_rejects_calendar_missingness_and_rank_deficiency() -> None:
    keys, features, target = _unequal_date_rows()
    missing = features.copy()
    missing.loc[0, "calendar_doy_sin"] = np.nan
    with pytest.raises(TrainingContractError, match="complete and finite"):
        prepare_b0_date_mean_training(missing, target, keys)

    one_date = keys.iloc[:1].copy()
    one_features = features.iloc[:1].copy()
    one_target = target.iloc[:1].copy()
    with pytest.raises(TrainingContractError, match="full-rank"):
        prepare_b0_date_mean_training(one_features, one_target, one_date)


def test_training_contract_rejects_duplicate_and_locked_keys() -> None:
    keys, _, _ = _unequal_date_rows()
    duplicated = pd.concat([keys, keys.iloc[[0]]], ignore_index=True)
    with pytest.raises(TrainingContractError, match="duplicate"):
        date_balanced_sample_weights(duplicated)

    locked = keys.iloc[[0]].copy()
    locked.loc[:, "target_date"] = "2025-07-01"
    with pytest.raises(PermissionError, match="locked rows"):
        date_balanced_sample_weights(locked)
