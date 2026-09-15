"""Guard whole-city selection, support centering and paired uncertainty."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

SPEC = importlib.util.spec_from_file_location(
    "accuracy_development", Path(__file__).parents[1] / "experiments/accuracy_development/run.py"
)
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


def test_inner_splits_never_see_outer_city():
    cities = ["a", "b", "c", "d"]
    plan = RUN.split_plan(cities)
    assert len(plan) == len(set(plan)) == 16
    for outer in cities:
        remaining = tuple(city for city in cities if city != outer)
        for held in remaining:
            train = tuple(city for city in remaining if city != held)
            assert (train, held) in plan
            assert outer not in train and held != outer and held not in train


def test_macro_selection_balances_cities_and_dates_not_row_counts():
    rows = pd.DataFrame({"city_id": ["a"] * 100 + ["b"], "target_date": ["2000-01-01"] * 101})
    for name in RUN.CANDIDATES:
        rows[name + "_error"] = 10.0
    rows["relative_static_error"] = [0.0] * 100 + [4.0]
    rows["relative_context_error"] = 1.5
    winner, scores = RUN.choose([rows])
    assert winner == "relative_context"
    assert scores["relative_static"] == 2.0


class FakeModel:
    def __init__(self, feature, offset):
        self.feature, self.offset = feature, offset

    def predict(self, frame):
        assert not set(RUN.KEYS) & set(frame.columns)
        assert "observed" not in frame
        return frame[self.feature].to_numpy() + self.offset


def test_prediction_centering_precedes_held_support_and_labels():
    frame = pd.DataFrame({name: [0.0, 2.0, 10.0] for name in RUN.M2_FEATURES})
    frame["city_id"] = "held"
    frame["target_date"] = "2000-01-01"
    frame["tract_geoid"] = ["1", "2", "3"]
    models = {
        "B1": FakeModel(RUN.B1_FEATURES[0], 30),
        "relative_static": FakeModel(RUN.ANOMALY_FEATURES[0], 100),
        "relative_context": FakeModel(RUN.M2_FEATURES[0], -70),
    }
    predicted = RUN.predict_models(models, frame)
    np.testing.assert_allclose(predicted.relative_static_relative, [-2.0, 0.0, 8.0])
    observed = frame.loc[[0, 1], RUN.KEYS].copy()
    observed["observed"] = [20.0, 24.0]
    scored = RUN.score_rows(predicted, observed)
    # Scored-subset diagnostic recentering differs from the full-support output.
    np.testing.assert_allclose(scored.relative_static_relative, [-2.0, 0.0])
    np.testing.assert_allclose(scored.relative_static_error, [1.0, 1.0])
    np.testing.assert_allclose(scored.relative_static_support_error, [0.0, 2.0])
    observed["observed"] += 999
    scored_again = RUN.score_rows(predicted, observed)
    np.testing.assert_allclose(scored.relative_static, scored_again.relative_static)


def test_crossed_bootstrap_keeps_pairing_and_date_block_structure():
    rows = pd.DataFrame(
        {
            "city_id": ["a"] * 8,
            "target_date": ["d1"] * 4 + ["d2"] * 4,
            "spatial_block": ["b1", "b1", "b2", "b2"] * 2,
            "relative_static_error": np.arange(8) + 0.5,
            "selected_error": np.arange(8),
        }
    )
    result = RUN.crossed_bootstrap(rows, 100, 1)
    assert result["lower_c"] == result["upper_c"] == 0.5


def test_training_response_centering_is_separate_from_allowed_predictors(monkeypatch):
    records = []

    class Recorder:
        def get_params(self):
            return {"model__random_state": 20260813}

        def set_params(self, **kwargs):
            return self

        def fit(self, frame, target, **kwargs):
            records.append((frame.copy(), np.asarray(target), kwargs["model__sample_weight"]))

    monkeypatch.setattr(RUN, "build_b1_estimator", Recorder)
    monkeypatch.setattr(RUN, "build_m2_legacy_estimator", Recorder)
    monkeypatch.setattr(RUN, "build_m3_estimators", lambda candidate: (None, Recorder()))
    frame = pd.DataFrame({name: np.arange(4, dtype=float) for name in RUN.M2_FEATURES})
    frame["city_id"] = ["a", "a", "b", "b"]
    frame["target_date"] = "2000-01-01"
    frame["tract_geoid"] = ["1", "2", "3", "4"]
    RUN.fit_models(frame, pd.Series([10.0, 14.0, 50.0, 58.0]), {"models": {}})
    assert [len(record[0].columns) for record in records] == [23, 23, 46]
    np.testing.assert_allclose(records[1][1], [-2, 2, -4, 4])
    assert all(not set(RUN.KEYS) & set(record[0]) for record in records)
