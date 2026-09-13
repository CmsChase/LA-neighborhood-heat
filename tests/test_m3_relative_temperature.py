from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments" / "m3_relative_temperature" / "run.py"
SPEC = importlib.util.spec_from_file_location("m3_relative_temperature", RUNNER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_hotspot_ties_use_geoid_order() -> None:
    ap, recall = MODULE.ranked_hotspot_metrics(
        pd.Series(["003", "001", "002", "004"]),
        np.array([4.0, 3.0, 2.0, 1.0]),
        np.array([1.0, 1.0, 1.0, 1.0]),
        0.25,
    )

    assert recall == 0.0
    assert ap == 1 / 3


def test_date_metrics_center_prediction_and_observation_on_scored_rows() -> None:
    rows = pd.DataFrame(
        {
            "evidence_role": ["source"] * 3,
            "model_id": ["M3"] * 3,
            "city_id": ["city"] * 3,
            "tract_geoid": ["1", "2", "3"],
            "target_date": ["2025-01-01"] * 3,
            "predicted_lst_c": [100.0, 101.0, 102.0],
            "observed_lst_c": [20.0, 21.0, 22.0],
        }
    )

    result = MODULE.calculate_date_metrics(rows, 0.2)

    assert result.loc[0, "anomaly_mae_c"] == 0.0
    assert result.loc[0, "spearman"] == 1.0


def test_city_groups_are_disjoint() -> None:
    config = MODULE.load_config()

    assert not set(config["experiment"]["source_cities"]) & set(
        config["experiment"]["opened_stress_cities"]
    )
