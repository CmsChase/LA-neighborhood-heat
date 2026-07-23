import math

import numpy as np
import pandas as pd
import pytest

from la_heat.metrics import (
    MetricAuditError,
    evaluate_absolute_lst_predictions,
    prepare_absolute_lst_predictions,
)


def _predictions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "a", "b", "c", "d"],
            "target_date": [
                "2020-07-01",
                "2020-07-01",
                "2021-07-01",
                "2021-07-01",
                "2021-07-01",
                "2021-07-01",
            ],
            "spatial_block": ["west", "east", "west", "east", "east", "north"],
            "y_true": [10.0, 20.0, 30.0, 31.0, 32.0, 33.0],
            "y_pred": [10.0, 20.0, 34.0, 35.0, 36.0, 37.0],
        }
    )


def test_primary_mae_equal_weights_dates_with_unequal_row_counts() -> None:
    result = evaluate_absolute_lst_predictions(_predictions())

    assert result.per_date["mae_c"].tolist() == pytest.approx([0.0, 4.0])
    assert result.summary.primary_equal_date_weighted_mae_c == pytest.approx(2.0)
    pooled_row_mae = np.mean(np.abs(_predictions()["y_pred"] - _predictions()["y_true"]))
    assert pooled_row_mae == pytest.approx(8.0 / 3.0)
    assert result.summary.primary_equal_date_weighted_mae_c != pytest.approx(
        pooled_row_mae
    )
    assert result.summary.pooled_mean_signed_error_c == pytest.approx(8.0 / 3.0)
    assert result.summary.equal_date_weighted_mean_signed_error_c == pytest.approx(2.0)


def test_anomaly_metric_centers_truth_and_prediction_separately_by_date() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "a", "b"],
            "target_date": ["2020-07-01"] * 2 + ["2021-07-01"] * 2,
            "spatial_block": ["one", "two", "one", "two"],
            "y_true": [10.0, 20.0, 5.0, 9.0],
            # Pure +100 offset on date one; reversed spatial pattern on date two.
            "y_pred": [110.0, 120.0, 19.0, 15.0],
        }
    )
    result = evaluate_absolute_lst_predictions(frame)

    assert result.per_date["within_date_anomaly_mae_c"].tolist() == pytest.approx(
        [0.0, 4.0]
    )
    assert (
        result.summary.equal_date_weighted_within_date_anomaly_mae_c
        == pytest.approx(2.0)
    )


def test_per_date_spearman_excludes_constant_dates_and_records_accounting() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "c", "a", "b", "c", "a"],
            "target_date": ["2020-07-01"] * 3
            + ["2021-07-01"] * 3
            + ["2022-07-01"],
            "spatial_block": ["one", "one", "two", "one", "one", "two", "one"],
            "y_true": [1.0, 2.0, 3.0, 4.0, 4.0, 4.0, 8.0],
            "y_pred": [3.0, 2.0, 1.0, 1.0, 2.0, 3.0, 9.0],
        }
    )
    result = evaluate_absolute_lst_predictions(frame)

    assert result.per_date["spearman_defined"].tolist() == [True, False, False]
    assert result.per_date.loc[0, "spearman_rho"] == pytest.approx(-1.0)
    assert math.isnan(result.per_date.loc[1, "spearman_rho"])
    assert result.summary.median_per_date_spearman == pytest.approx(-1.0)
    assert result.summary.spearman_defined_date_count == 1
    assert result.summary.spearman_undefined_date_count == 2


def test_summary_has_exact_row_date_block_counts_rmse_and_r2() -> None:
    result = evaluate_absolute_lst_predictions(_predictions())
    summary = result.summary

    assert summary.row_count == 6
    assert summary.independent_date_count == 2
    assert summary.independent_spatial_block_count == 3
    assert summary.pooled_rmse_c == pytest.approx(math.sqrt(64.0 / 6.0))
    expected_r2 = 1.0 - 64.0 / np.sum(
        np.square(_predictions()["y_true"] - _predictions()["y_true"].mean())
    )
    assert summary.pooled_oos_r2 == pytest.approx(expected_r2)
    assert result.per_date["spatial_block_count"].tolist() == [2, 3]


def test_results_are_deterministic_under_row_shuffle_and_ignore_extra_columns() -> None:
    original = evaluate_absolute_lst_predictions(_predictions())
    shuffled = _predictions().sample(frac=1, random_state=44).reset_index(drop=True)
    shuffled["model_feature_that_must_not_affect_metrics"] = np.arange(len(shuffled))
    changed_order = evaluate_absolute_lst_predictions(shuffled)

    assert original.summary == changed_order.summary
    pd.testing.assert_frame_equal(original.per_date, changed_order.per_date)


@pytest.mark.parametrize("column", ["y_true", "y_pred"])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_truth_or_prediction_is_rejected(column: str, invalid: float) -> None:
    frame = _predictions()
    frame.loc[0, column] = invalid

    with pytest.raises(MetricAuditError, match="finite"):
        evaluate_absolute_lst_predictions(frame)


def test_duplicate_keys_are_rejected() -> None:
    frame = pd.concat([_predictions(), _predictions().iloc[[0]]], ignore_index=True)

    with pytest.raises(MetricAuditError, match="duplicate tract-date"):
        evaluate_absolute_lst_predictions(frame)


@pytest.mark.parametrize("target_date", ["2020-07-01 12:00", "2020-07-01T00:00:00Z"])
def test_non_civil_midnight_dates_are_rejected(target_date: str) -> None:
    frame = _predictions()
    frame.loc[0, "target_date"] = target_date

    with pytest.raises(MetricAuditError, match="civil|timezone"):
        evaluate_absolute_lst_predictions(frame)


def test_locked_2025_is_rejected_by_default_but_requires_explicit_unlock() -> None:
    frame = _predictions()
    frame.loc[0, "target_date"] = "2025-07-01"

    with pytest.raises(PermissionError, match="locked rows"):
        evaluate_absolute_lst_predictions(frame)
    unlocked = evaluate_absolute_lst_predictions(frame, unlock_final_test=True)
    assert unlocked.summary.row_count == len(frame)


def test_constant_global_truth_makes_pooled_r2_undefined() -> None:
    frame = _predictions()
    frame["y_true"] = 7.0

    result = evaluate_absolute_lst_predictions(frame)

    assert result.summary.pooled_oos_r2 is None


def test_inconsistent_spatial_block_metadata_is_rejected() -> None:
    frame = _predictions()
    frame.loc[2, "spatial_block"] = "wrong-block"

    with pytest.raises(MetricAuditError, match="exactly one spatial_block"):
        prepare_absolute_lst_predictions(frame)
