from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments" / "m3_2x2" / "run.py"
SPEC = importlib.util.spec_from_file_location("m3_source_level_2x2", RUNNER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_fixed_variant_matrix() -> None:
    variants = MODULE.variants_from_config(MODULE.load_config())

    assert [(row.variant_id, row.include_elevation, row.level_aggregation) for row in variants] == [
        ("A", True, "selected_training_support"),
        ("B", True, "complete_predictor_support"),
        ("C", False, "selected_training_support"),
        ("D", False, "complete_predictor_support"),
    ]


def test_complete_support_summary_is_invariant_to_target_selection() -> None:
    features = list(MODULE._level_features(include_elevation=True))
    rows = []
    for tract, elevation, tmax in (("1", 10.0, 20.0), ("2", 20.0, 22.0), ("3", 100.0, 30.0)):
        row = {
            "city_id": "source_city",
            "tract_geoid": tract,
            "target_date": "2024-07-01",
            "elevation_mean_m": elevation,
            "city_centroid_latitude_deg": 34.0,
            "calendar_doy_sin": 0.0,
            "calendar_doy_cos": 1.0,
        }
        for name in MODULE.WEATHER_FEATURES:
            row[name] = tmax if name == "daymet_tmax_c_mean_prev_1d" else 1.0
        rows.append(row)
    complete = pd.DataFrame(rows)
    selected = complete.iloc[:2].reset_index(drop=True)
    target = pd.Series([25.0, 26.0])
    consistent = MODULE.Variant("B", "consistent", True, "complete_predictor_support")
    original = MODULE.Variant("A", "original", True, "selected_training_support")

    complete_table = MODULE.build_level_training_table(selected, target, complete, consistent)
    original_table = MODULE.build_level_training_table(selected, target, complete, original)

    assert complete_table.loc[0, "elevation_mean_m"] == 20.0
    assert original_table.loc[0, "elevation_mean_m"] == 15.0
    assert complete_table.loc[0, "daymet_tmax_c_mean_prev_1d"] == 22.0
    assert original_table.loc[0, "daymet_tmax_c_mean_prev_1d"] == 21.0
    assert complete_table.loc[0, "observed_lst_c"] == 25.5
    assert set(features) <= set(complete_table.columns)


def test_opened_cities_are_excluded_from_source_cities() -> None:
    config = MODULE.load_config()

    assert not set(config["experiment"]["source_cities"]) & set(
        config["experiment"]["opened_cities_excluded"]
    )
