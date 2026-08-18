from __future__ import annotations

from pathlib import Path

from la_heat.multicity.m3_source_predictor_sentinel_game_laptop_v1 import (
    CODE_PATHS,
    DOWNLOAD_THREADS,
)


def test_game_laptop_contract_is_four_asset_threads() -> None:
    assert DOWNLOAD_THREADS == 4
    assert CODE_PATHS[-1] == Path("START_M3_PREDICTOR_GAME_LAPTOP.cmd")
