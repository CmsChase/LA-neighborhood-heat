from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from la_heat.multicity.m3_development import (
    M3_CANDIDATES,
    SENTINEL_FEATURES,
    STATIC_FEATURES,
    build_m3_estimators,
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments" / "m3_relative_temperature" / "run.py"
CONTRACT = ROOT / "experiments" / "m3_relative_temperature" / "fixed_contract.toml"
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


def test_fixed_contract_matches_implemented_anomaly_estimator() -> None:
    with CONTRACT.open("rb") as handle:
        contract = tomllib.load(handle)
    model = build_m3_estimators(M3_CANDIDATES[-1])[1].named_steps["model"]

    assert contract["model"]["complete_features"] == list(STATIC_FEATURES)
    assert contract["model"]["median_imputed_features"] == list(SENTINEL_FEATURES)
    for name in (
        "loss",
        "learning_rate",
        "max_iter",
        "max_leaf_nodes",
        "min_samples_leaf",
        "l2_regularization",
        "early_stopping",
        "random_state",
    ):
        assert contract["model"][name] == model.get_params()[name]


def test_fixed_contract_forbids_scope_expansion() -> None:
    with CONTRACT.open("rb") as handle:
        contract = tomllib.load(handle)

    assert contract["contract"]["absolute_temperature_claim_allowed"] is False
    assert contract["contract"]["new_city_data_allowed"] is False
    assert contract["contract"]["hyperparameter_search_allowed"] is False
    assert contract["next_audit"]["opened_historical_stress_values_used"] is False
