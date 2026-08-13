from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

import la_heat.multicity.m3_development as m3
from la_heat.multicity.m3_development import (
    ANOMALY_FEATURES,
    B1_FEATURES,
    M2_FEATURES,
    M3_CANDIDATES,
    PREDICTION_COLUMNS,
    RISK_FEATURES,
    M3DevelopmentError,
    build_b1_estimator,
    build_domain_classifier,
    build_m2_legacy_estimator,
    build_m3_estimators,
    build_risk_feature_matrix,
    city_date_row_weights,
    cross_fit_domain_probabilities,
    density_ratios_from_probabilities,
    density_stability_report,
    domain_sample_weights,
    finite_sample_test_atom_quantile,
    fit_m3_candidate,
    nested_whole_city_loso,
    predict_m3,
    risk_acceptance_flags,
    select_density_method,
    select_risk_method,
    u0_cross_conformal_correction,
    validate_prediction_feature_frame,
    validate_prediction_output,
    validate_risk_feature_matrix,
)


def _frame(*, cities: int = 4, dates: int = 3, tracts: int = 8) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(20_260_813)
    rows: list[dict[str, object]] = []
    target: list[float] = []
    for city_index in range(cities):
        for date_index in range(dates):
            for tract_index in range(tracts):
                values = rng.normal(size=len(M2_FEATURES))
                row: dict[str, object] = {
                    "city_id": f"city_{city_index}",
                    "tract_geoid": f"{city_index:02d}{tract_index:09d}",
                    "target_date": f"2025-0{date_index + 5}-15",
                    "city_centroid_latitude_deg": 28.0 + 4.0 * city_index,
                }
                row.update(dict(zip(M2_FEATURES, values, strict=True)))
                rows.append(row)
                target.append(
                    24.0
                    + 3.0 * city_index
                    + 2.0 * date_index
                    + 1.5 * float(row["daymet_tmax_c_mean_prev_1d"])
                    + 2.0 * float(row["impervious_mean_fraction"])
                )
    frame = pd.DataFrame(rows)
    return frame, pd.Series(target, index=frame.index, name="observed_lst_c")


def _fast_hgb(*, max_leaf_nodes: int, seed: int) -> DummyRegressor:
    del max_leaf_nodes, seed
    return DummyRegressor(strategy="median")


def test_fixed_feature_and_estimator_contracts() -> None:
    assert len(B1_FEATURES) == 23
    assert len(M2_FEATURES) == 46
    assert len(ANOMALY_FEATURES) == 23
    assert len(M3_CANDIDATES) == 4
    assert {candidate.level_alpha for candidate in M3_CANDIDATES} == {1.0, 10.0}
    assert {candidate.anomaly_max_leaf_nodes for candidate in M3_CANDIDATES} == {15, 31}

    assert build_b1_estimator()["model"].alpha == 10.0
    legacy = build_m2_legacy_estimator()["model"]
    assert legacy.max_leaf_nodes == 31
    assert legacy.random_state == 20_260_719
    level, anomaly = build_m3_estimators(M3_CANDIDATES[0])
    assert level["model"].alpha == M3_CANDIDATES[0].level_alpha
    assert anomaly["model"].random_state == m3.MODEL_SEED


def test_city_date_row_weights_balance_every_level() -> None:
    frame, _ = _frame(cities=2, dates=2, tracts=3)
    # Make one city-date larger; totals must still be balanced.
    extra = frame.loc[frame["city_id"].eq("city_0")].iloc[:2].copy()
    extra["tract_geoid"] = ["extra_0", "extra_1"]
    frame = pd.concat([frame, extra], ignore_index=True)
    weights = city_date_row_weights(frame)
    weighted = frame.loc[:, ["city_id", "target_date"]].assign(weight=weights)
    city_totals = weighted.groupby("city_id")["weight"].sum()
    date_totals = weighted.groupby(["city_id", "target_date"])["weight"].sum()
    assert city_totals.nunique() == 1
    assert all(
        np.allclose(group.to_numpy(), group.iloc[0])
        for _, group in date_totals.groupby(level=0)
    )
    assert weights.mean() == pytest.approx(1.0)


def test_m3_fit_predict_centers_each_city_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m3, "_hgb", _fast_hgb)
    frame, target = _frame(cities=3, dates=2, tracts=6)
    model = fit_m3_candidate(frame, target, M3_CANDIDATES[0])
    predictions = predict_m3(model, frame.sample(frac=1.0, random_state=8).reset_index(drop=True))

    medians = predictions.groupby(["city_id", "target_date"])[
        "m3_anomaly_prediction_c"
    ].median()
    assert np.allclose(medians, 0.0)
    assert np.allclose(
        predictions["m3_prediction_c"],
        predictions["m3_level_prediction_c"] + predictions["m3_anomaly_prediction_c"],
    )


def test_nested_loso_is_whole_city_and_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m3, "_hgb", _fast_hgb)
    frame, target = _frame(dates=2, tracts=5)

    first = nested_whole_city_loso(frame, target)
    second = nested_whole_city_loso(frame, target)

    assert first.selected_candidate_id == second.selected_candidate_id
    assert len(first.candidate_metrics) == 4
    assert len(first.outer_selections) == 4
    assert len(first.oof_predictions) == len(frame)
    assert first.oof_predictions[list(m3.KEY_COLUMNS)].equals(
        second.oof_predictions[list(m3.KEY_COLUMNS)]
    )
    assert first.oof_predictions["city_id"].eq(
        first.oof_predictions["outer_city_id"]
    ).all()
    assert set(first.oof_predictions["selected_candidate_id"]).issubset(
        {candidate.candidate_id for candidate in M3_CANDIDATES}
    )


def test_u0_conformal_uses_test_atom_and_stable_order() -> None:
    scores = np.arange(10, dtype=float)
    correction = finite_sample_test_atom_quantile(
        scores,
        np.ones(10),
        ["a"] * 10,
        ["2025-06-01"] * 10,
        [f"{value:011d}" for value in range(10)],
    )
    assert correction == 9.0
    with pytest.raises(M3DevelopmentError, match="unbounded"):
        finite_sample_test_atom_quantile(
            [0.0, 1.0],
            [1.0, 1.0],
            ["a", "a"],
            ["2025-06-01", "2025-06-01"],
            ["1", "2"],
        )

    frame, _ = _frame(cities=2, dates=2, tracts=5)
    keys = frame.loc[:, m3.KEY_COLUMNS]
    assert u0_cross_conformal_correction(np.linspace(0, 1, len(keys)), keys) >= 0


def test_density_ratio_stability_and_fallback_are_pure() -> None:
    ratios = density_ratios_from_probabilities(
        [0.5] * 40,
        [0.5] * 20,
        clip=(0.2, 5.0),
    )
    assert np.allclose(ratios["source_weight"], 1.0)
    assert not ratios["source_clip_hit"].any()
    city = ["a"] * 20 + ["b"] * 20
    dates = [f"2025-06-{index % 10 + 1:02d}" for index in range(40)]
    report = density_stability_report(
        ratios["source_weight"],
        city,
        dates,
        ratios["source_clip_hit"],
        ratios["test_clip_hit"],
        roc_auc=0.70,
        pooled_coverage=0.90,
        city_coverages={"held": 0.90},
        weighted_wis=8.0,
        unweighted_wis=10.0,
        clip_upper=5.0,
    )
    assert report["stable"] is True
    assert select_density_method([report]) == "density_ratio_clip_5"
    failed = dict(report, stable=False)
    assert select_density_method([failed]) == "unweighted_cross_conformal"


def test_exact_domain_classifier_and_balanced_cross_fit_probabilities() -> None:
    classifier = build_domain_classifier()
    assert classifier["impute"].strategy == "median"
    assert classifier["impute"].add_indicator is True
    assert classifier["model"].penalty == "l2"
    assert classifier["model"].C == 1.0
    assert classifier["model"].solver == "lbfgs"
    assert classifier["model"].max_iter == 2_000
    assert classifier["model"].random_state == m3.DENSITY_SEED

    frame, _ = _frame(cities=4, dates=5, tracts=2)
    domain = frame["city_id"].isin(["city_2", "city_3"]).astype(int).to_numpy()
    weights = domain_sample_weights(frame.loc[:, m3.KEY_COLUMNS], domain)
    assert weights[domain == 0].sum() == pytest.approx(0.5)
    assert weights[domain == 1].sum() == pytest.approx(0.5)

    first = cross_fit_domain_probabilities(frame, domain)
    second = cross_fit_domain_probabilities(frame, domain)
    assert set(first["density_fold"]) == set(range(5))
    assert first.equals(second)
    assert first["domain_probability"].between(0, 1, inclusive="neither").all()


def test_domain_cross_fit_rejects_a_training_fold_without_both_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, _ = _frame(cities=2, dates=5, tracts=1)
    domain = frame["city_id"].eq("city_1").astype(int).to_numpy()

    def separated_fold(city: object, target_date: object, *, folds: int = 5) -> int:
        del target_date, folds
        return 0 if str(city) == "city_0" else 1

    monkeypatch.setattr(m3, "deterministic_group_fold", separated_fold)
    with pytest.raises(M3DevelopmentError, match="folds are incomplete"):
        cross_fit_domain_probabilities(frame, domain)


def test_risk_acceptance_is_exact_and_has_honest_fallback() -> None:
    frame, _ = _frame(cities=1, dates=1, tracts=5)
    keys = frame.loc[:, m3.KEY_COLUMNS]
    result = risk_acceptance_flags(keys, [1.0, 1.0, 2.0, 3.0, 4.0])
    assert result["m3_accepted"].sum() == 4
    # Equal risks are ordered by GEOID.
    accepted_equal = result.loc[
        result["m3_predicted_absolute_error_c"].eq(1), "tract_geoid"
    ].tolist()
    assert accepted_equal == sorted(accepted_equal)
    assert result["m3_accepted"].eq(~result["m3_abstain"]).all()

    good = {
        "method": "learned_error",
        "equal_city_improvement": 0.15,
        "minimum_retention": 0.8,
        "per_city_improvement": {"a": 0.1, "b": 0.01},
        "accepted_equal_city_mae_c": 2.0,
    }
    assert select_risk_method([good]) == "learned_error"
    assert select_risk_method([{**good, "equal_city_improvement": 0.09}]) == (
        "none_accept_all"
    )


def test_exact_risk_matrix_is_target_blind_and_validated() -> None:
    frame, _ = _frame(cities=2, dates=1, tracts=3)
    diagnostics = frame.loc[:, m3.KEY_COLUMNS].copy()
    diagnostics["b1_prediction_c"] = 30.0
    diagnostics["m2_legacy_prediction_c"] = 29.0
    diagnostics["m3_prediction_c"] = 28.0
    diagnostics["m3_interval_width_c"] = 4.0
    diagnostics["m3_ensemble_point_sd_c"] = 0.5
    risk = build_risk_feature_matrix(frame, diagnostics, np.full(len(frame), 2.0))
    assert tuple(risk.columns) == RISK_FEATURES
    assert np.allclose(risk["m3_abs_b1_disagreement_c"], 2.0)
    assert np.allclose(risk["m3_abs_m2_legacy_disagreement_c"], 1.0)
    assert np.allclose(risk["uq_abs_log_density_ratio"], np.log(2.0))
    assert validate_risk_feature_matrix(risk).equals(risk.astype(float))

    changed = risk.copy()
    changed["target_lst_c"] = 1.0
    with pytest.raises(M3DevelopmentError, match="schema"):
        validate_risk_feature_matrix(changed)


def test_exact_21_column_prediction_output_contract() -> None:
    assert len(PREDICTION_COLUMNS) == 21
    assert "m3_raw_lower_c" not in PREDICTION_COLUMNS
    assert "m3_raw_upper_c" not in PREDICTION_COLUMNS
    row = {
        "city_id": "test_city",
        "tract_geoid": "00000000001",
        "target_date": "2025-07-01",
        "b1_prediction_c": 31.0,
        "m2_legacy_prediction_c": 30.0,
        "m3_level_prediction_c": 28.0,
        "m3_anomaly_prediction_c": 1.0,
        "m3_prediction_c": 29.0,
        "m3_conformal_correction_c": 2.0,
        "m3_lower_c": 27.0,
        "m3_upper_c": 31.0,
        "m3_interval_width_c": 4.0,
        "m3_ensemble_point_sd_c": 0.5,
        "uq_method": "unweighted_cross_conformal",
        "uq_density_ratio_raw": 1.0,
        "uq_density_ratio_clipped": 1.0,
        "uq_weight_clip_hit": False,
        "m3_predicted_absolute_error_c": 1.5,
        "m3_risk_percentile_within_city_date": 0.5,
        "m3_abstain": False,
        "m3_accepted": True,
    }
    output = pd.DataFrame([row], columns=PREDICTION_COLUMNS)
    assert validate_prediction_output(output).equals(output)
    changed = output.copy()
    changed["m3_interval_width_c"] = 3.0
    with pytest.raises(M3DevelopmentError, match="numeric contract"):
        validate_prediction_output(changed)


@pytest.mark.parametrize("column", ["target_lst_c", "ST_QA", "QA_PIXEL", "date_usable"])
def test_prediction_feature_frame_rejects_target_and_qa_columns(column: str) -> None:
    frame, _ = _frame(cities=1, dates=1, tracts=2)
    frame[column] = 1.0
    with pytest.raises(M3DevelopmentError, match="target/QA"):
        validate_prediction_feature_frame(frame)
