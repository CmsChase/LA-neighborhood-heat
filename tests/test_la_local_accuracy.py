"""Guards for strict-forward LA local accuracy development."""

import importlib.util
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "la_local_accuracy", ROOT / "experiments/la_local_accuracy/run.py"
)
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


def test_forward_plan_never_uses_held_or_future_years():
    plan = RUN.forward_plan([2020, 2021, 2022, 2023, 2024], [2022, 2023, 2024])
    assert [row["outer_test_year"] for row in plan] == [2022, 2023, 2024]
    for row in plan:
        assert max(row["outer_train_years"]) < row["outer_test_year"]
        assert max(row["inner_train_years"]) < row["inner_validation_year"]
        assert row["inner_validation_year"] == row["outer_test_year"] - 1


def test_fixed_candidates_exclude_identifiers_and_target_history():
    with (ROOT / "experiments/la_local_accuracy/experiment.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["experiment"]["candidate_ids"] == RUN.CANDIDATES
    assert config["selection"]["tract_id_or_target_history_as_candidate_feature"] is False
    assert not set(RUN.KEYS).intersection(RUN.ANOMALY_FEATURES)
    assert "historical_tract_median" not in RUN.CANDIDATES


def test_complete_support_centering_precedes_scored_subset_centering():
    prediction = pd.DataFrame(
        {
            "city_id": ["la"] * 3,
            "tract_geoid": ["1", "2", "3"],
            "target_date": ["2022-01-01"] * 3,
        }
    )
    for name in [*RUN.CANDIDATES, *RUN.BASELINES]:
        prediction[f"{name}_relative_support"] = [-2.0, 0.0, 8.0]
        prediction[f"{name}_absolute"] = [28.0, 30.0, 38.0]
    observed = prediction.loc[:1, RUN.KEYS].copy()
    observed["observed"] = [28.0, 32.0]
    observed["spatial_block"] = ["a", "b"]
    scored = RUN.score_rows(prediction, observed)
    np.testing.assert_allclose(
        scored.relative_current_23_relative_scored.to_numpy(), [-1.0, 1.0]
    )
    np.testing.assert_allclose(
        scored.relative_current_23_relative_support.to_numpy(), [-2.0, 0.0]
    )
    assert RUN.mean_by_date(scored, "relative_current_23_error") == 1.0
    assert RUN.mean_by_date(scored, "relative_current_23_support_error") == 1.0


def test_hotspot_ties_use_geoid_and_exact_top_k():
    ap, recall = RUN.ranked_hotspot(
        ["003", "001", "002", "004", "005"],
        [9.0, 10.0, 8.0, 1.0, 0.0],
        [5.0, 5.0, 4.0, 3.0, 2.0],
        0.20,
    )
    assert ap == recall == 1.0
