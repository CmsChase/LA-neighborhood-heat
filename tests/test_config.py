from pathlib import Path

import pytest

from la_heat.config import load_config

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def _locked_config_copy(tmp_path: Path) -> Path:
    config_path = tmp_path / "configs" / "research.toml"
    config_path.parent.mkdir(parents=True)
    payload = CONFIG.read_bytes()
    unlocked_setting = b"unlock_final_test = true"
    assert payload.count(unlocked_setting) == 1
    config_path.write_bytes(
        payload.replace(unlocked_setting, b"unlock_final_test = false")
    )
    return config_path


def test_completed_final_test_config_is_unlocked() -> None:
    config = load_config(CONFIG)
    assert config.final_test_year == 2025
    assert config.final_test_unlocked is True
    config.require_final_test_access()


def test_final_test_lock_still_denies_access(tmp_path: Path) -> None:
    config = load_config(_locked_config_copy(tmp_path))
    assert config.final_test_year == 2025
    assert config.final_test_unlocked is False
    with pytest.raises(PermissionError, match="Final-test labels"):
        config.require_final_test_access()


def test_primary_landsat_qa_does_not_hard_cut_st_uncertainty() -> None:
    config = load_config(CONFIG)
    landsat = config.raw["landsat"]
    assert landsat["apply_st_uncertainty_threshold"] is False
    assert landsat["minimum_cloud_distance_km"] == 1.0
