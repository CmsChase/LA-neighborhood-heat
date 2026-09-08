from __future__ import annotations

from pathlib import Path
from typing import Any

from la_heat.multicity import (
    m3_blind_predictor_sentinel_static_acquisition_authorization_v1 as authorization,
)


def test_authorization_is_exact_and_value_free() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = authorization.build_authorization(root)
    assert payload["blind_city_ids"] == [
        "seattle_wa",
        "denver_co",
        "atlanta_ga",
        "miami_fl",
    ]
    assert payload["source_contract"]["sentinel"][
        "selected_physical_acquisition_count"
    ] == 539
    assert payload["predictor_scope"]["static_feature_count"] == 18
    assert payload["predictor_scope"]["sentinel_feature_count"] == 5
    assert payload["permissions"]["sentinel_and_static_value_or_network_read_now"] is False
    assert payload["permissions"]["daymet_value_or_network_read"] is False
    assert payload["permissions"]["landsat_asset_href_thermal_qa_or_target_access"] is False
    assert payload["authorization_audit"]["sentinel_or_static_raster_opened_or_statted"] == 0
    assert payload["authorization_audit"]["network_or_href_reads"] == 0


def test_city_source_scope_is_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = authorization.build_authorization(root)
    cities = {row["city_id"]: row for row in payload["city_scope"]}
    assert cities["seattle_wa"]["sentinel_physical_acquisitions"] == 150
    assert cities["denver_co"]["sentinel_physical_acquisitions"] == 157
    assert cities["atlanta_ga"]["sentinel_physical_acquisitions"] == 157
    assert cities["miami_fl"]["sentinel_physical_acquisitions"] == 75
    assert cities["seattle_wa"]["srtm_tile_ids"] == ["N47W123"]
    assert cities["denver_co"]["srtm_tile_ids"] == ["N39W106", "N39W105"]
    assert cities["atlanta_ga"]["srtm_tile_ids"] == ["N33W085"]
    assert cities["miami_fl"]["srtm_tile_ids"] == ["N25W081"]


def test_authorization_builder_never_opens_or_stats_value_files(monkeypatch: Any) -> None:
    root = Path(__file__).resolve().parents[1]
    value_suffixes = {".csv", ".parquet", ".tif", ".zip"}
    original_open = Path.open
    original_stat = Path.stat

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.suffix.lower() not in value_suffixes, f"value file opened: {path}"
        return original_open(path, *args, **kwargs)

    def guarded_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.suffix.lower() not in value_suffixes, f"value file statted: {path}"
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    payload = authorization.build_authorization(root)
    assert payload["authorization_audit"]["network_or_href_reads"] == 0
