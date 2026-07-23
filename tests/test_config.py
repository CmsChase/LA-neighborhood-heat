from pathlib import Path

import pytest

from la_heat.config import load_config

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def test_final_test_is_locked_by_default() -> None:
    config = load_config(CONFIG)
    assert config.final_test_year == 2025
    assert config.final_test_unlocked is False
    with pytest.raises(PermissionError, match="Final-test labels"):
        config.require_final_test_access()


def test_primary_landsat_qa_does_not_hard_cut_st_uncertainty() -> None:
    config = load_config(CONFIG)
    landsat = config.raw["landsat"]
    assert landsat["apply_st_uncertainty_threshold"] is False
    assert landsat["minimum_cloud_distance_km"] == 1.0
