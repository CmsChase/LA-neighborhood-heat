from __future__ import annotations

from pathlib import Path

import pandas as pd

from la_heat.multicity import m3_blind_predictor_sentinel_lineage_city_repair_v1 as repair


def test_duplicate_city_is_validated_then_reinserted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(repair, "authenticate_authorization", lambda _root: {})
    frame = pd.DataFrame({"city_id": ["miami_fl", "miami_fl"], "x": [1, 2]})
    with repair._repair_insert(tmp_path) as applied:
        frame.insert(0, "city_id", "miami_fl")
    assert list(frame.columns) == ["city_id", "x"]
    assert frame["city_id"].tolist() == ["miami_fl", "miami_fl"]
    assert applied == [1]
