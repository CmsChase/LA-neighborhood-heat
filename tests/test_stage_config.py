import copy
from pathlib import Path

from la_heat.config import ResearchConfig, load_config
from la_heat.stage_config import (
    daymet_grid_config_sha256,
    inventory_config_sha256,
    target_config_sha256,
)

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def _changed_config(section: str, key: str, value: object) -> ResearchConfig:
    loaded = load_config(CONFIG)
    raw = copy.deepcopy(loaded.raw)
    raw[section][key] = value
    return ResearchConfig(raw=raw, path=loaded.path)


def test_modeling_feature_change_does_not_invalidate_data_stages() -> None:
    loaded = load_config(CONFIG)
    changed = _changed_config("weather_features", "rolling_windows_days", [1, 3, 9])
    assert inventory_config_sha256(loaded) == inventory_config_sha256(changed)
    assert target_config_sha256(loaded) == target_config_sha256(changed)


def test_target_qa_change_does_not_change_inventory_but_changes_target() -> None:
    loaded = load_config(CONFIG)
    changed = _changed_config("landsat", "minimum_cloud_distance_km", 2.0)
    assert inventory_config_sha256(loaded) == inventory_config_sha256(changed)
    assert target_config_sha256(loaded) != target_config_sha256(changed)


def test_inventory_date_change_invalidates_inventory() -> None:
    loaded = load_config(CONFIG)
    changed = _changed_config("study", "development_end_date", "2024-09-30")
    assert inventory_config_sha256(loaded) != inventory_config_sha256(changed)


def test_weather_change_only_invalidates_daymet_stage() -> None:
    loaded = load_config(CONFIG)
    changed = _changed_config("weather_features", "rolling_windows_days", [1, 3, 9])
    assert daymet_grid_config_sha256(loaded) != daymet_grid_config_sha256(changed)
    assert inventory_config_sha256(loaded) == inventory_config_sha256(changed)
    assert target_config_sha256(loaded) == target_config_sha256(changed)


def test_fixed_land_mask_change_invalidates_daymet_weights() -> None:
    loaded = load_config(CONFIG)
    changed = _changed_config("static_land_mask", "water_classes", [80, 90])
    assert daymet_grid_config_sha256(loaded) != daymet_grid_config_sha256(changed)
