from __future__ import annotations

from la_heat.multicity.m3_blind_prediction_v2 import ALGORITHM_VERSION


def test_repair_is_explicitly_versioned() -> None:
    assert ALGORITHM_VERSION == "m3-blind-prediction-v2-exact-schema-repair"
