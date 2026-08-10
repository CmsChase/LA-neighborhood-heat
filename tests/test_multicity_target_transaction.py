from __future__ import annotations

import pandas as pd
import pytest

from la_heat.multicity.target_transaction import (
    TargetTransactionError,
    authenticate_city_target_relationships,
)


def _inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    items = pd.DataFrame(
        {
            "item_id": ["scene-a", "scene-b"],
            "platform": ["landsat-9", "landsat-9"],
            "acquisition_local_date": ["2025-05-08", "2025-05-08"],
        }
    )
    overpasses = pd.DataFrame(
        {
            "overpass_id": ["overpass-a"],
            "platform": ["landsat-9"],
            "local_date": ["2025-05-08"],
            "scene_ids": ["scene-a|scene-b"],
            "scene_count": [2],
            "primary_eligible": [True],
            "source_lock_sha256": ["a" * 64],
        }
    )
    keys = pd.DataFrame(
        {
            "city_id": ["chicago_il", "chicago_il"],
            "tract_geoid": ["1", "2"],
            "target_date": pd.to_datetime(["2025-05-08", "2025-05-08"]),
            "overpass_id": ["overpass-a", "overpass-a"],
            "platform": ["landsat-9", "landsat-9"],
        }
    )
    return items, overpasses, keys


def _authenticate(
    items: pd.DataFrame, overpasses: pd.DataFrame, keys: pd.DataFrame
) -> tuple[dict[str, object], list[dict[str, object]]]:
    return authenticate_city_target_relationships(
        city_id="chicago_il",
        items=items,
        overpasses=overpasses,
        keys=keys,
        context_geoids=("1", "2"),
        target_grid_sha256="b" * 64,
    )


def test_authenticates_scene_overpass_and_key_relationships() -> None:
    summary, units = _authenticate(*_inputs())

    assert summary["overpass_target_unit_count"] == 1
    assert summary["primary_scene_count"] == 2
    assert units[0]["scene_ids"] == ["scene-a", "scene-b"]
    assert units[0]["tract_key_count"] == 2


def test_rejects_scene_overpass_date_disagreement() -> None:
    items, overpasses, keys = _inputs()
    items.loc[0, "acquisition_local_date"] = "2025-05-09"

    with pytest.raises(TargetTransactionError, match="Scene metadata disagrees"):
        _authenticate(items, overpasses, keys)


def test_rejects_key_universe_that_does_not_match_target_context() -> None:
    items, overpasses, keys = _inputs()
    keys.loc[1, "tract_geoid"] = "3"

    with pytest.raises(TargetTransactionError, match="Tract keys disagree"):
        _authenticate(items, overpasses, keys)


def test_rejects_planning_table_that_contains_asset_hrefs() -> None:
    items, overpasses, keys = _inputs()
    items["lwir11_href"] = "https://forbidden.test/value.tif"

    with pytest.raises(TargetTransactionError, match="may not contain asset hrefs"):
        _authenticate(items, overpasses, keys)
