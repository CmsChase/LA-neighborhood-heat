from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_metadata_v1 as metadata


def test_metadata_authorization_is_value_blind() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = metadata.build_runtime_authorization(root)
    assert payload["state"] == "m3_blind_predictor_metadata_runtime_authorized"
    assert payload["blind_city_ids"] == list(metadata.BLIND_CITY_IDS)
    assert payload["network_contract"]["stac_assets_and_links_excluded"] is True
    assert payload["permissions"]["read_public_sentinel_and_daymet_metadata"] is True
    assert (
        payload["permissions"][
            "read_or_download_sentinel_daymet_static_predictor_values"
        ]
        is False
    )
    assert payload["permissions"]["read_landsat_asset_hrefs_thermal_qa_or_targets"] is False
    assert payload["permissions"]["fit_predict_score_or_evaluate"] is False


def test_metadata_paths_are_new_stage_paths() -> None:
    assert "m3_blind_predictor_build_v1" in metadata.RUNTIME_ROOT.as_posix()
    assert "m3_blind_predictor_build_v1" in metadata.OUTPUT_ROOT.as_posix()
    assert metadata.AUTHORIZATION_PATH.name.startswith("M3_BLIND_PREDICTOR_METADATA")
