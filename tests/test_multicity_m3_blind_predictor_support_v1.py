from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_support_v1 as support


def test_runtime_authorization_is_code_bound_and_value_blind() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = support.build_m3_blind_predictor_support_runtime_authorization(root)
    assert payload["blind_city_ids"] == list(support.BLIND_CITY_IDS)
    assert payload["permissions"] == {
        "read_only_parent_bound_census_and_worldcover_support_values": True,
        "write_only_new_support_runtime_and_outputs": True,
        "network_or_href_reads": False,
        "landsat_asset_href_thermal_qa_or_target_access": False,
        "daymet_sentinel_or_other_predictor_source_access": False,
        "fit_predict_score_or_evaluate": False,
    }
    assert payload["authorization_audit"] == {
        "census_or_worldcover_value_files_opened_or_statted": 0,
        "network_or_href_reads": 0,
        "landsat_asset_href_thermal_qa_or_target_access": False,
        "model_fit_predict_score_or_evaluate": False,
    }
    paths = {record["path"] for record in payload["code_identity"]["files"]}
    assert paths == set(support.CODE_PATHS)
