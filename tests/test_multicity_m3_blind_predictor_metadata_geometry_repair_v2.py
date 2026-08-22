from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_metadata_geometry_repair_v2 as repair


def test_geometry_repair_is_hash_only_and_parent_bound() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = repair.build_authorization(root)
    assert payload["repair"]["output_file_bytes_unchanged"] is True
    assert payload["incident"]["landsat_qa_or_target_values_read"] is False
    assert payload["permissions"]["change_query_city_date_key_or_source_contract"] is False
