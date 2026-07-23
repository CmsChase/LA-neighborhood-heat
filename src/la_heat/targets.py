"""Target-label and relative-endpoint construction."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RelativeEndpointSummary:
    coverage_pass: bool
    retained_tract_count: int
    retained_tract_fraction: float
    minimum_block_retention_fraction: float
    latitude_quartile_retention_gap: float
    longitude_quartile_retention_gap: float
    minimum_eligible_joint_cell_retention_fraction: float
    hotspot_count: int
    hotspot_threshold_c: float | None


def assign_relative_endpoints(
    frame: pd.DataFrame,
    *,
    hotspot_fraction: float,
    minimum_tract_fraction: float,
    maximum_quartile_retention_gap: float,
    minimum_joint_cell_tracts: int,
    minimum_joint_cell_retention_fraction: float,
) -> tuple[pd.DataFrame, RelativeEndpointSummary]:
    """Create anomaly and exact top-k labels only for spatially representative dates."""

    if not 0 < hotspot_fraction < 1:
        raise ValueError("Hotspot fraction must lie strictly between zero and one.")
    required = {
        "tract_geoid",
        "spatial_block",
        "latitude_quartile",
        "longitude_quartile",
        "target_lst_c",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Relative endpoint input is missing columns: {sorted(missing)}")

    result = frame.copy()
    retained = result["target_lst_c"].notna()
    retained_count = int(retained.sum())
    retained_fraction = float(retained.mean())
    block_rates = retained.groupby(result["spatial_block"], sort=True).mean()
    minimum_observed_block_fraction = float(block_rates.min())
    latitude_rates = retained.groupby(result["latitude_quartile"], sort=True).mean()
    longitude_rates = retained.groupby(result["longitude_quartile"], sort=True).mean()
    latitude_gap = float(latitude_rates.max() - latitude_rates.min())
    longitude_gap = float(longitude_rates.max() - longitude_rates.min())
    joint_key = pd.MultiIndex.from_arrays(
        [result["latitude_quartile"], result["longitude_quartile"]]
    )
    joint_frame = pd.DataFrame({"retained": retained.to_numpy()}, index=joint_key)
    joint_summary = joint_frame.groupby(level=[0, 1], sort=True)["retained"].agg(
        ["size", "mean"]
    )
    eligible_joint_cells = joint_summary.loc[
        joint_summary["size"] >= minimum_joint_cell_tracts
    ]
    if eligible_joint_cells.empty:
        raise ValueError("No joint geographic cell meets the minimum tract count.")
    minimum_joint_retention = float(eligible_joint_cells["mean"].min())
    coverage_pass = bool(
        retained_fraction >= minimum_tract_fraction
        and latitude_gap <= maximum_quartile_retention_gap
        and longitude_gap <= maximum_quartile_retention_gap
        and minimum_joint_retention >= minimum_joint_cell_retention_fraction
    )

    result["lst_anomaly_c"] = pd.Series(float("nan"), index=result.index, dtype=float)
    result["relative_hotspot_top20"] = pd.Series(
        pd.NA, index=result.index, dtype="boolean"
    )
    hotspot_count = 0
    hotspot_threshold: float | None = None
    if coverage_pass and retained_count:
        city_median = float(result.loc[retained, "target_lst_c"].median())
        result.loc[retained, "lst_anomaly_c"] = (
            result.loc[retained, "target_lst_c"] - city_median
        )

        hotspot_count = math.ceil(hotspot_fraction * retained_count)
        ranked = result.loc[retained].sort_values(
            ["target_lst_c", "tract_geoid"],
            ascending=[False, True],
            kind="mergesort",
        )
        hotspot_index = ranked.index[:hotspot_count]
        result.loc[retained, "relative_hotspot_top20"] = False
        result.loc[hotspot_index, "relative_hotspot_top20"] = True
        hotspot_threshold = float(result.loc[hotspot_index, "target_lst_c"].min())

    summary = RelativeEndpointSummary(
        coverage_pass=coverage_pass,
        retained_tract_count=retained_count,
        retained_tract_fraction=retained_fraction,
        minimum_block_retention_fraction=minimum_observed_block_fraction,
        latitude_quartile_retention_gap=latitude_gap,
        longitude_quartile_retention_gap=longitude_gap,
        minimum_eligible_joint_cell_retention_fraction=minimum_joint_retention,
        hotspot_count=hotspot_count,
        hotspot_threshold_c=hotspot_threshold,
    )
    return result, summary
