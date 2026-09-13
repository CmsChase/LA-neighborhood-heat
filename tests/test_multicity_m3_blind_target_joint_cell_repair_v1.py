from __future__ import annotations

import pandas as pd

from la_heat.multicity.m3_blind_target_joint_cell_repair_v1 import (
    safe_assign_relative_endpoints,
)


def test_empty_eligible_joint_cells_withhold_relative_endpoints() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": [f"{index:011d}" for index in range(16)],
            "spatial_block": [f"b{index}" for index in range(16)],
            "latitude_quartile": [index // 4 for index in range(16)],
            "longitude_quartile": [index % 4 for index in range(16)],
            "target_lst_c": [30.0 + index / 10 for index in range(16)],
        }
    )
    result, summary = safe_assign_relative_endpoints(
        frame,
        hotspot_fraction=0.2,
        minimum_tract_fraction=0.8,
        maximum_quartile_retention_gap=0.2,
        minimum_joint_cell_tracts=20,
        minimum_joint_cell_retention_fraction=0.6,
    )
    assert summary.coverage_pass is False
    assert result["lst_anomaly_c"].isna().all()
    assert result["relative_hotspot_top20"].isna().all()
