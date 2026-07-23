import math

import pandas as pd

from la_heat.targets import assign_relative_endpoints


def test_relative_hotspot_is_exact_top_k_with_geoid_tie_break() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["c", "b", "a", "d", "e"],
            "spatial_block": ["x"] * 5,
            "latitude_quartile": [0] * 5,
            "longitude_quartile": [0] * 5,
            "target_lst_c": [40.0, 40.0, 40.0, 39.0, 38.0],
        }
    )
    result, summary = assign_relative_endpoints(
        frame,
        hotspot_fraction=0.20,
        minimum_tract_fraction=0.80,
        maximum_quartile_retention_gap=0.20,
        minimum_joint_cell_tracts=1,
        minimum_joint_cell_retention_fraction=0.60,
    )
    assert summary.coverage_pass
    assert summary.hotspot_count == math.ceil(0.20 * len(frame)) == 1
    assert result.loc[result["relative_hotspot_top20"], "tract_geoid"].tolist() == ["a"]


def test_relative_endpoints_are_withheld_when_spatial_coverage_fails() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "c", "d"],
            "spatial_block": ["north", "north", "south", "south"],
            "latitude_quartile": [0, 0, 1, 1],
            "longitude_quartile": [0, 1, 0, 1],
            "target_lst_c": [40.0, 39.0, float("nan"), float("nan")],
        }
    )
    result, summary = assign_relative_endpoints(
        frame,
        hotspot_fraction=0.20,
        minimum_tract_fraction=0.50,
        maximum_quartile_retention_gap=0.20,
        minimum_joint_cell_tracts=1,
        minimum_joint_cell_retention_fraction=0.60,
    )
    assert not summary.coverage_pass
    assert result["lst_anomaly_c"].isna().all()
    assert result["relative_hotspot_top20"].isna().all()


def test_joint_cell_gate_catches_local_hole_that_margins_hide() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": list("abcdefgh"),
            "spatial_block": ["x"] * 8,
            "latitude_quartile": [0, 0, 0, 0, 1, 1, 1, 1],
            "longitude_quartile": [0, 0, 1, 1, 0, 0, 1, 1],
            # Two diagonal cells present and two diagonal cells completely absent.
            "target_lst_c": [40.0, 39.0, float("nan"), float("nan"),
                             float("nan"), float("nan"), 38.0, 37.0],
        }
    )
    result, summary = assign_relative_endpoints(
        frame,
        hotspot_fraction=0.20,
        minimum_tract_fraction=0.50,
        maximum_quartile_retention_gap=0.20,
        minimum_joint_cell_tracts=2,
        minimum_joint_cell_retention_fraction=0.60,
    )
    assert summary.latitude_quartile_retention_gap == 0.0
    assert summary.longitude_quartile_retention_gap == 0.0
    assert summary.minimum_eligible_joint_cell_retention_fraction == 0.0
    assert not summary.coverage_pass
    assert result["relative_hotspot_top20"].isna().all()
