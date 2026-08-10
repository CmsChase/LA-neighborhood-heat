from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from la_heat.multicity.predictor_readiness import (
    PredictorReadinessError,
    validate_predictor_frame,
)

KEYS = ["city_id", "tract_geoid", "target_date"]
FEATURES = ["static_value", "calendar_value", "daymet_value", "sentinel_a", "sentinel_b"]


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city_id": ["a", "a", "b"],
            "tract_geoid": ["1", "2", "3"],
            "target_date": ["2025-01-01", "2025-01-01", "2025-01-02"],
            "static_value": [1.0, 2.0, 3.0],
            "calendar_value": [0.1, 0.1, 0.2],
            "daymet_value": [4.0, np.nan, 6.0],
            "sentinel_a": [0.2, np.nan, 0.4],
            "sentinel_b": [0.3, np.nan, 0.5],
            "display_only": ["x", "y", "z"],
        }
    )


def test_predictor_frame_allows_dynamic_missing_and_metadata() -> None:
    result = validate_predictor_frame(
        _frame(), key_columns=KEYS, feature_order=FEATURES
    )

    assert result["row_count"] == 3
    assert result["sentinel_available_row_count"] == 2
    assert result["metadata_columns"] == ["display_only"]


def test_predictor_frame_rejects_partial_sentinel_missingness() -> None:
    frame = _frame()
    frame.loc[0, "sentinel_b"] = np.nan

    with pytest.raises(PredictorReadinessError, match="all present or all missing"):
        validate_predictor_frame(frame, key_columns=KEYS, feature_order=FEATURES)


def test_predictor_frame_rejects_duplicate_keys() -> None:
    frame = _frame()
    frame.loc[1, KEYS] = frame.loc[0, KEYS]

    with pytest.raises(PredictorReadinessError, match="duplicated"):
        validate_predictor_frame(frame, key_columns=KEYS, feature_order=FEATURES)


def test_predictor_frame_rejects_missing_static_value() -> None:
    frame = _frame()
    frame.loc[0, "static_value"] = np.nan

    with pytest.raises(PredictorReadinessError, match="Static/calendar"):
        validate_predictor_frame(frame, key_columns=KEYS, feature_order=FEATURES)
