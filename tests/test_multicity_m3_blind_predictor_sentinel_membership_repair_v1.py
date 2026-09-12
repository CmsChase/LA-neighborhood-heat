from __future__ import annotations

from pathlib import Path

import pandas as pd

from la_heat.multicity import m3_blind_predictor_sentinel_membership_repair_v1 as repair


def test_repair_is_narrow_and_enables_legitimate_many_to_many(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(repair, "authenticate_authorization", lambda _root: {})
    left = pd.DataFrame(
        {
            "target_date": ["2025-01-02", "2025-01-03"],
            "physical_acquisition_id": ["a", "a"],
            "acquisition_local_date": ["2025-01-01", "2025-01-01"],
            "lag_days": [1, 2],
        }
    )
    right = pd.DataFrame(
        {
            "physical_acquisition_id": ["a", "a"],
            "acquisition_local_date": ["2025-01-01", "2025-01-01"],
            "tract_geoid": ["1", "2"],
        }
    )
    with repair._repair_merge(tmp_path) as applied:
        merged = left.merge(
            right,
            on=list(repair.JOIN_KEYS),
            validate="one_to_many",
        )
    assert len(merged) == 4
    assert applied == [1]
