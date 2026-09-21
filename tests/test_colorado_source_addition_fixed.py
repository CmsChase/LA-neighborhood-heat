from __future__ import annotations

import pandas as pd

from experiments.colorado_source_addition_fixed import (
    _source_date_filters,
    _summarize,
    _top20_recall,
)


def test_source_date_filter_only_locks_la_2025() -> None:
    assert _source_date_filters("los_angeles_ca", parquet_timestamp=False) == [
        ("target_date", "<", "2025-01-01")
    ]
    assert _source_date_filters("phoenix_az", parquet_timestamp=False) is None


def test_top20_recall_uses_deterministic_tract_tie_break() -> None:
    frame = pd.DataFrame(
        {
            "city_id": ["a"] * 5,
            "target_date": ["2024-01-01"] * 5,
            "tract_geoid": ["1", "2", "3", "4", "5"],
            "observed_lst_c": [5, 4, 3, 2, 1],
            "prediction_c": [5, 1, 2, 3, 4],
        }
    )
    assert _top20_recall(frame) == 1.0


def test_summary_equal_city_equal_date_weighting() -> None:
    frame = pd.DataFrame(
        {
            "city_id": ["a", "a", "b", "b"],
            "target_date": ["2024-01-01"] * 2 + ["2024-01-02"] * 2,
            "tract_geoid": ["1", "2", "3", "4"],
            "observed_lst_c": [10.0, 12.0, 20.0, 22.0],
            "prediction_c": [11.0, 13.0, 18.0, 20.0],
            "predicted_relative_c": [-1.0, 1.0, -1.0, 1.0],
        }
    )
    result = _summarize(frame)
    assert result["equal_city_equal_date_mae_c"] == 1.5
    assert result["equal_city_equal_date_relative_mae_c"] == 0.0
