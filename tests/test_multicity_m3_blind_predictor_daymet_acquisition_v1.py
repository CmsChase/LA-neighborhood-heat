from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_daymet_acquisition_v1 as acquisition


def test_daymet_authorization_is_narrow() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = acquisition.build_authorization(root)
    assert payload["task_count"] == 24
    assert payload["network_contract"]["direct_dap4_subsets_only"] is True
    assert payload["network_contract"]["credentials_persisted"] is False
    assert payload["permissions"]["download_daymet_2025_subsets"] is True
    assert payload["permissions"]["read_sentinel_static_landsat_qa_or_target_values"] is False
