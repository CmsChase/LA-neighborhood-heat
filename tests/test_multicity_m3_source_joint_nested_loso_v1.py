from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from la_heat.multicity import m3_source_joint_nested_loso_v1 as joint


def _predictors(*, dates: int = 10, tracts: int = 2) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for city_index, city_id in enumerate(joint.SOURCE_CITY_IDS):
        for date_index in range(dates):
            for tract_index in range(tracts):
                row: dict[str, Any] = {
                    "city_id": city_id,
                    "tract_geoid": f"{city_index:02d}{tract_index:09d}",
                    "target_date": f"2024-07-{date_index + 1:02d}",
                    joint.CONTEXT_FEATURE: 30.0 + city_index,
                }
                row.update(
                    {name: float(city_index + date_index / 10) for name in joint.M2_FEATURES}
                )
                rows.append(row)
    return pd.DataFrame(rows)


def _targets(
    predictors: pd.DataFrame, *, unavailable_city: str | None = None
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for qa_index, qa_id in enumerate(joint.QA_IDS):
        frame = predictors.loc[:, joint.KEY_COLUMNS].copy()
        frame["date_usable"] = True
        frame["target_available"] = True
        frame["target_lst_c"] = (
            predictors["city_id"].map(
                {city: index for index, city in enumerate(joint.SOURCE_CITY_IDS)}
            )
            + pd.to_datetime(predictors["target_date"]).dt.day / 10
            + qa_index / 100
        )
        if unavailable_city is not None:
            frame.loc[frame["city_id"].eq(unavailable_city), "target_available"] = False
            frame.loc[frame["city_id"].eq(unavailable_city), "target_lst_c"] = np.nan
        result[qa_id] = frame
    return result


def test_joint_universe_and_pre_value_complexity_order_are_exact() -> None:
    assert len(joint.JOINT_CONFIGURATIONS) == 16
    assert len({item.joint_candidate_id for item in joint.JOINT_CONFIGURATIONS}) == 16
    assert {
        (item.qa_id, item.m3_candidate.candidate_id) for item in joint.JOINT_CONFIGURATIONS
    } == {
        (qa_id, candidate_id) for qa_id in joint.QA_IDS for candidate_id in joint.M3_COMPLEXITY_RANK
    }
    assert joint.M3_COMPLEXITY_RANK == {
        "level_ridge_alpha_10__anomaly_hgb_leaves_15": 1,
        "level_ridge_alpha_1__anomaly_hgb_leaves_15": 2,
        "level_ridge_alpha_10__anomaly_hgb_leaves_31": 3,
        "level_ridge_alpha_1__anomaly_hgb_leaves_31": 4,
    }


def test_selection_key_uses_rounded_primary_then_all_six_ties() -> None:
    base = {
        "equal_city_equal_date_mae_c": 1.0000000000004,
        "minimum_usable_dates": 8,
        "total_usable_city_dates": 30,
        "overall_tract_date_retention": 0.7,
        "qa_leniency_rank": 3,
        "m3_complexity_rank": 2,
        "joint_candidate_id": "b",
        "eligible": True,
    }
    # Primary values tie after the frozen 12-decimal rounding, then every
    # listed field independently has the documented direction.
    improvements = [
        {"minimum_usable_dates": 9},
        {"total_usable_city_dates": 31},
        {"overall_tract_date_retention": 0.8},
        {"qa_leniency_rank": 4},
        {"m3_complexity_rank": 1},
        {"joint_candidate_id": "a"},
    ]
    for change in improvements:
        better = {**base, **change, "equal_city_equal_date_mae_c": 1.00000000000049}
        assert joint.joint_selection_key(better) < joint.joint_selection_key(base)


@pytest.mark.parametrize(
    ("dates", "low_support_city"),
    [(9, None), (10, joint.SOURCE_CITY_IDS[0])],
)
def test_support_gate_rejects_total_below_30_or_any_city_below_8(
    dates: int,
    low_support_city: str | None,
) -> None:
    predictors = _predictors(dates=dates)
    targets = _targets(predictors)
    if low_support_city is not None:
        for frame in targets.values():
            low_support = frame["city_id"].eq(low_support_city) & pd.to_datetime(
                frame["target_date"]
            ).dt.day.le(3)
            frame.loc[low_support, "date_usable"] = False
    with pytest.raises(joint.M3SourceJointLosoError, match="support gate"):
        joint.joint_nested_whole_city_loso(
            predictors,
            targets,
            fit_func=lambda frame, target, candidate: object(),
            predict_func=lambda model, frame: frame.loc[:, joint.KEY_COLUMNS].assign(
                m3_prediction_c=0.0
            ),
            diagnostic_func=None,
        )


def test_nested_joint_selection_never_fits_a_held_city() -> None:
    predictors = _predictors()
    targets = _targets(predictors)
    fit_events: list[frozenset[str]] = []

    def fake_fit(
        frame: pd.DataFrame,
        target: pd.Series,
        candidate: joint.M3Candidate,
    ) -> dict[str, Any]:
        cities = frozenset(frame["city_id"].astype(str))
        fit_events.append(cities)
        return {
            "cities": cities,
            "mean": float(target.mean()),
            "offset": joint.M3_COMPLEXITY_RANK[candidate.candidate_id] / 1000,
        }

    def fake_predict(model: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
        held = set(frame["city_id"].astype(str))
        assert not (set(model["cities"]) & held)
        return frame.loc[:, joint.KEY_COLUMNS].assign(
            m3_prediction_c=float(model["mean"] + model["offset"])
        )

    result = joint.joint_nested_whole_city_loso(
        predictors,
        targets,
        fit_func=fake_fit,
        predict_func=fake_predict,
        diagnostic_func=None,
    )
    assert len(result.candidate_metrics) == 16
    assert len(result.outer_inner_candidate_metrics) == 64
    assert result.outer_inner_candidate_metrics.groupby("outer_city_id").size().eq(16).all()
    assert len(result.outer_selections) == 4
    assert set(result.outer_oof_predictions["outer_city_id"]) == set(joint.SOURCE_CITY_IDS)
    assert result.selected_qa_id == "none"
    assert result.selected_m3_candidate_id == ("level_ridge_alpha_10__anomaly_hgb_leaves_15")
    assert frozenset(joint.SOURCE_CITY_IDS) in fit_events  # the final four-source refit only


def test_prediction_precedes_qa_filter_and_retention_uses_usable_universe() -> None:
    predictors = _predictors()
    target = _targets(predictors)["none"]
    first_city = joint.SOURCE_CITY_IDS[0]
    unusable_date = "2024-07-01"
    target.loc[
        target["city_id"].eq(first_city) & target["target_date"].eq(unusable_date),
        "date_usable",
    ] = False
    unavailable = target.index[
        target["city_id"].eq(joint.SOURCE_CITY_IDS[1]) & target["target_date"].eq("2024-07-02")
    ][0]
    target.loc[unavailable, "target_available"] = False
    target.loc[unavailable, "target_lst_c"] = np.nan
    prepared = joint.prepare_qa_dataset(predictors, target, qa_id="none")
    assert prepared.total_predictor_rows_on_usable_dates == 78
    assert len(prepared.frame) == 77
    seen_held_sizes: list[int] = []

    def fake_fit(
        frame: pd.DataFrame,
        observed: pd.Series,
        candidate: joint.M3Candidate,
    ) -> float:
        del frame, candidate
        return float(observed.mean())

    def fake_predict(model: float, frame: pd.DataFrame) -> pd.DataFrame:
        seen_held_sizes.append(len(frame))
        return frame.loc[:, joint.KEY_COLUMNS].assign(m3_prediction_c=model)

    row, _ = joint._candidate_loso_metrics(
        prepared,
        joint.SOURCE_CITY_IDS,
        joint.JOINT_CONFIGURATIONS[0],
        fit_func=fake_fit,
        predict_func=fake_predict,
    )
    assert seen_held_sizes == [20, 20, 20, 20]
    assert row["overall_tract_date_retention"] == pytest.approx(77 / 78)


def test_formal_predictor_context_is_added_only_from_authenticated_mapping() -> None:
    raw = _predictors().drop(columns=joint.CONTEXT_FEATURE)
    context = {city: 30.0 + index for index, city in enumerate(joint.SOURCE_CITY_IDS)}
    added = joint.add_authenticated_city_context(raw, context)
    assert added.groupby("city_id")[joint.CONTEXT_FEATURE].nunique().eq(1).all()
    assert added.groupby("city_id")[joint.CONTEXT_FEATURE].first().to_dict() == context
    with pytest.raises(joint.M3SourceJointLosoError, match="smuggle"):
        joint.add_authenticated_city_context(added, context)


def test_readiness_authenticates_metadata_without_opening_or_statting_any_parquet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("value file access is forbidden during readiness")

    monkeypatch.setattr(pd, "read_parquet", forbidden)
    monkeypatch.setattr(joint, "_authenticate_bound_parquet", forbidden)
    readiness = joint.joint_loso_readiness(root)
    assert readiness["state"] == "ready_for_independent_joint_nested_loso_authorization"
    assert readiness["ready"] is True
    assert readiness["parquet_files_opened_or_statted"] == 0
    assert readiness["model_fit_selection_prediction_or_scoring_performed"] is False


def test_source_uq_and_risk_selectors_use_frozen_fallbacks() -> None:
    pseudo_test = {
        "outer_held_source_city_ids": list(joint.SOURCE_CITY_IDS),
        "blind_predictor_accessed": False,
    }
    uq_gates = {
        "auc_at_most_0_95": False,
        "row_ess_at_least_25_percent": True,
        "date_ess_at_least_50_percent": True,
        "every_source_city_share_at_least_5_percent": True,
        "source_clip_fraction_at_most_20_percent": True,
        "test_clip_fraction_at_most_20_percent": True,
        "pooled_coverage_in_85_to_95_percent": True,
        "every_city_coverage_at_least_80_percent": True,
        "wis_improvement_at_least_2_percent": True,
    }
    uq = joint.select_source_uq_method(
        [
            {
                **pseudo_test,
                "method": "density_ratio_clip_5",
                "clip_upper": 5.0,
                "weighted_wis": 1.0,
                "unweighted_wis": 1.0,
                "wis_improvement_fraction": 0.0,
                "row_ess_fraction": 1.0,
                "date_ess_fraction": 1.0,
                "gates": uq_gates,
                "stable": False,
            }
        ]
    )
    assert uq == {
        "selected_method": "unweighted_cross_conformal",
        "fallback_used": True,
        "fallback_reason": ("no_density_candidate_passed_all_frozen_stability_and_benefit_gates"),
    }
    risk = joint.select_source_risk_method(
        [
            {
                **pseudo_test,
                "method": method,
                "equal_city_improvement": 0.09,
                "minimum_retention": 0.8,
                "per_city_improvement": {city: 0.1 for city in joint.SOURCE_CITY_IDS},
                "accepted_equal_city_mae_c": 1.0,
            }
            for method in ("learned_error", "interval_width", "ensemble_sd")
        ]
    )
    assert risk["selected_method"] == "none_accept_all"
    assert risk["fallback_used"] is True
    assert risk["fallback_reason"]


def test_completion_requires_the_independent_authorization_and_all_stages(tmp_path: Path) -> None:
    with pytest.raises(joint.M3SourceJointLosoError, match="authorization"):
        joint.build_source_nested_loso_completion(tmp_path)


def test_source_uq_and_risk_pseudo_test_runners_are_source_only() -> None:
    predictors = _predictors()
    oof = predictors.loc[:, joint.KEY_COLUMNS].copy()
    oof["outer_city_id"] = oof["city_id"]
    city_index = oof["city_id"].map(
        {city: index for index, city in enumerate(joint.SOURCE_CITY_IDS)}
    ).astype(float)
    day = pd.to_datetime(oof["target_date"]).dt.day.astype(float)
    oof["observed_lst_c"] = 25.0 + city_index + day / 10.0
    oof["m3_prediction_c"] = oof["observed_lst_c"] + ((day.astype(int) % 3) - 1) / 2
    oof["b1_prediction_c"] = oof["m3_prediction_c"] + 1.0
    oof["m2_legacy_prediction_c"] = oof["m3_prediction_c"] + 0.5
    oof["m3_ensemble_point_sd_c"] = 0.2 + day / 100.0
    uq, reports, intervals = joint.source_uq_pseudo_tests(predictors, oof)
    assert uq["selected_method"] in {
        "unweighted_cross_conformal",
        "density_ratio_clip_5",
    }
    assert reports[0]["outer_held_source_city_ids"] == list(joint.SOURCE_CITY_IDS)
    assert set(intervals["city_id"]) == set(joint.SOURCE_CITY_IDS)
    risk, candidates, ranked = joint.source_risk_pseudo_tests(predictors, oof, intervals)
    assert risk["selected_method"] in {
        "learned_error",
        "interval_width",
        "ensemble_sd",
        "none_accept_all",
    }
    assert {row["method"] for row in candidates} == {
        "learned_error",
        "interval_width",
        "ensemble_sd",
    }
    assert set(ranked["city_id"]) == set(joint.SOURCE_CITY_IDS)


def test_source_and_runner_keep_value_reads_behind_formal_authentication() -> None:
    loader = inspect.getsource(joint.load_authorized_source_inputs)
    assert loader.index("authenticate_m3_source_joint_nested_loso_authorization") < loader.index(
        "pd.read_parquet"
    )
    assert "pd.read_parquet" not in inspect.getsource(joint.joint_loso_readiness)
    runner = (
        Path(__file__).resolve().parents[1] / "scripts/run_m3_source_joint_nested_loso_v1.py"
    ).read_text(encoding="utf-8")
    assert "create_m3_source_joint_nested_loso_authorization" not in runner
    authorization = Path(__file__).resolve().parents[1] / joint.AUTHORIZATION_PATH
    if authorization.exists():
        assert joint.authenticate_m3_source_joint_nested_loso_authorization(
            authorization.parents[3]
        )["state"] == "m3_source_joint_nested_loso_v1_authorized"
