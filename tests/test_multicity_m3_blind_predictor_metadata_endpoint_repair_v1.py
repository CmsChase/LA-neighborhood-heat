from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_metadata_endpoint_repair_v1 as repair


def test_endpoint_repair_is_narrow_and_parent_bound() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = repair.build_authorization(root)
    assert payload["repair"] == {
        "operation": "inject_missing_source_footprints_endpoint_constant_only",
        "name": "PLANETARY_COMPUTER_STAC_API",
        "value": repair.STAC_API,
    }
    assert payload["incident"]["failed_before_first_network_request"] is True
    assert payload["permissions"]["change_city_date_key_or_source_contract"] is False
    assert payload["permissions"]["read_predictor_landsat_qa_or_target_values"] is False
