from la_heat.multicity.phoenix_source_footprint_restage import _snapshot_comparison


def test_snapshot_comparison_records_new_coverage_without_rejecting_it() -> None:
    historical = {
        "landsat_wrs": {"member_count": 1, "member_ids": ["1"], "item_count": 4},
        "sentinel_mgrs": {"member_count": 1, "member_ids": ["A"], "item_count": 8},
        "daymet_cells": {"member_count": 1},
        "terrain_windows": {"member_count": 1, "member_ids": ["T1"]},
    }
    current = {
        "landsat_wrs": {
            "member_count": 2,
            "member_ids": ["1", "2"],
            "item_count": 5,
        },
        "sentinel_mgrs": {
            "member_count": 1,
            "member_ids": ["A"],
            "item_count": 9,
        },
        "daymet_cells": {"member_count": 2},
        "terrain_windows": {"member_count": 1, "member_ids": ["T1"]},
    }

    comparison = _snapshot_comparison(historical, current)

    assert comparison["landsat_wrs"]["current_only_member_ids"] == ["2"]
    assert comparison["landsat_wrs"]["current_item_count"] == 5
    assert comparison["daymet_cells"]["current_member_count"] == 2
