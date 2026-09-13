from __future__ import annotations

import pandas as pd
import pytest

from la_heat.multicity.m3_blind_prediction_v1 import (
    OUTPUT_COLUMNS,
    M3BlindPredictionError,
    _validate_output,
)


def _frame(rows: int = 9_558) -> pd.DataFrame:
    correction = 2.5
    frame = pd.DataFrame(
        {
            "city_id": ["seattle_wa"] * rows,
            "tract_geoid": [f"{value:011d}" for value in range(rows)],
            "target_date": ["2025-07-01"] * rows,
            "m3_level_prediction_c": [30.0] * rows,
            "m3_anomaly_prediction_c": [1.0] * rows,
            "m3_prediction_c": [31.0] * rows,
            "m3_conformal_correction_c": [correction] * rows,
            "m3_lower_c": [31.0 - correction] * rows,
            "m3_upper_c": [31.0 + correction] * rows,
            "m3_interval_width_c": [2 * correction] * rows,
            "uq_method": ["unweighted_cross_conformal"] * rows,
            "risk_method": ["none_accept_all"] * rows,
            "m3_abstain": [False] * rows,
            "m3_accepted": [True] * rows,
        }
    )
    return frame.loc[:, OUTPUT_COLUMNS]


def test_validate_target_blind_output() -> None:
    assert len(_validate_output(_frame(), "seattle_wa")) == 9_558


def test_validate_rejects_target_column() -> None:
    frame = _frame()
    frame["target_lst_c"] = 42.0
    with pytest.raises(M3BlindPredictionError):
        _validate_output(frame, "seattle_wa")
