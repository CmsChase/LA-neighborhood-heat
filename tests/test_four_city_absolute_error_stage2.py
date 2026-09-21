from __future__ import annotations

import tomllib
from pathlib import Path

import pandas as pd

from experiments.four_city_absolute_error.run_stage2 import (
    CONTRACT_PATH,
    select_candidate,
    validate_relative_invariance,
)


def _contract() -> dict:
    return tomllib.loads(Path(CONTRACT_PATH).read_text(encoding="utf-8"))


def test_contract_keeps_exact_candidate_set_and_forbids_stage_0_1_computation() -> None:
    contract = _contract()
    assert [row["id"] for row in contract["candidates"]] == ["C1", "C2", "C3"]
    assert contract["source_selection"]["simplicity_order"] == ["C1", "C2", "C3"]
    assert contract["stage_0_1"]["allow_training"] is False
    assert contract["opened_city_stress_test"]["may_change_candidate_choice"] is False


def test_selection_retains_b1_when_worst_city_gate_fails() -> None:
    rows = []
    values = {
        "B1": {"a": 2.0, "b": 4.0},
        "C1": {"a": 1.0, "b": 4.1},
        "C2": {"a": 3.0, "b": 5.0},
        "C3": {"a": 2.5, "b": 4.5},
    }
    for model_id, cities in values.items():
        for city_id, mae in cities.items():
            rows.append(
                {"model_id": model_id, "city_id": city_id, "absolute_mae_c": mae}
            )
    selected = select_candidate(pd.DataFrame(rows), _contract())
    assert selected["selected_model_id"] == "B1"
    assert selected["candidate_promoted"] is False
    assert selected["candidates"][0]["overall_no_worse_than_b1"] is True
    assert selected["candidates"][0]["worst_city_no_worse_than_b1"] is False


def test_constant_level_changes_preserve_ranking_and_exact_hotspots() -> None:
    rows = []
    for model_id, shift in {"M3": 0.0, "C1": 1.0, "C2": -2.0, "C3": 5.0}.items():
        for index, value in enumerate([1.0, 1.0, 3.0, 2.0, 0.0]):
            rows.append(
                {
                    "model_id": model_id,
                    "city_id": "city",
                    "target_date": "2026-01-01",
                    "tract_geoid": f"{index:011d}",
                    "prediction_c": value + shift,
                }
            )
    result = validate_relative_invariance(pd.DataFrame(rows))
    assert result["passed"] is True
    assert result["ranking_mismatches"] == 0
    assert result["hotspot_mismatches"] == 0
