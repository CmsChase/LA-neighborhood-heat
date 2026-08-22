from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_build_authorization_v1 as auth


def test_live_parent_authorization_is_metadata_only_and_exact() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = auth.build_m3_blind_predictor_parent_authorization(root)

    assert payload["blind_city_ids"] == list(auth.BLIND_CITY_IDS)
    assert payload["key_universe"]["city_count"] == 4
    assert payload["key_universe"]["target_date_count"] == 143
    assert payload["key_universe"]["tract_date_row_count"] == 23_667
    assert payload["predictor_contract"]["feature_count"] == 46
    assert payload["frozen_source_selection"] == {
        "joint_candidate_id": "qa_4k__level_ridge_alpha_10__anomaly_hgb_leaves_31",
        "qa_id": "4k",
        "m3_candidate_id": "level_ridge_alpha_10__anomaly_hgb_leaves_31",
        "uq_method": "unweighted_cross_conformal",
        "risk_method": "none_accept_all",
        "retuning_after_this_parent": False,
    }
    assert payload["permissions"]["read_blind_predictor_values_under_this_parent_alone"] is False
    assert payload["permissions"]["open_blind_target_or_qa_values"] is False
    assert payload["authorization_access_audit"] == {
        "blind_predictor_parquet_opened_or_statted": 0,
        "blind_landsat_asset_href_reads": 0,
        "blind_thermal_qa_or_target_values_read": False,
        "network_requests": 0,
        "model_fit_predict_score_or_evaluate": False,
    }


def test_city_universe_order_and_counts_are_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = auth.build_m3_blind_predictor_parent_authorization(root)
    rows = payload["key_universe"]["cities"]
    assert tuple(row["city_id"] for row in rows) == auth.BLIND_CITY_IDS
    assert {
        row["city_id"]: {
            "tract_count": row["tract_count"],
            "target_date_count": row["target_date_count"],
            "row_count": row["row_count"],
        }
        for row in rows
    } == auth.EXPECTED_CITY_COUNTS
