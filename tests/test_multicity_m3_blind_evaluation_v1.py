from __future__ import annotations

from la_heat.multicity.m3_blind_evaluation_v1 import (
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    ENGINE_COLUMNS,
)


def test_frozen_blind_evaluation_constants() -> None:
    assert BOOTSTRAP_ITERATIONS == 10_000
    assert BOOTSTRAP_SEED == 20_260_816
    assert ENGINE_COLUMNS[3] == "b1_prediction_c"
    assert ENGINE_COLUMNS[4] == "m2_prediction_c"
