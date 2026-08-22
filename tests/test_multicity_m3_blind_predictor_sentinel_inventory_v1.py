from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_sentinel_inventory_v1 as inventory


def test_sentinel_inventory_authorization_is_raster_blind() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = inventory.build_authorization(root)
    assert payload["network_contract"]["exact_item_id_queries_only"] is True
    assert payload["network_contract"]["raster_asset_open_or_download"] is False
    assert payload["permissions"]["read_asset_href_metadata"] is True
    assert payload["permissions"]["open_or_download_sentinel_rasters"] is False
    assert payload["permissions"]["read_static_daymet_landsat_qa_or_target_values"] is False
