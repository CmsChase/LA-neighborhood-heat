from __future__ import annotations

import pandas as pd
import pytest

from la_heat.sentinel_compile_adapter import (
    build_previous_60_day_composites_by_target,
)
from la_heat.sentinel_features import INDEX_COLUMNS


def _acquisition_rows() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for geoid, offset in (("0001", 0.0), ("0002", 1.0)):
        row: dict[str, object] = {
            "tract_geoid": geoid,
            "physical_acquisition_id": "physical-a",
            "acquisition_local_date": "2024-01-01",
            "acquisition_coverage_fraction": 1.0,
        }
        for index, column in enumerate(INDEX_COLUMNS):
            row[column] = offset + index / 10
        rows.append(row)
    return pd.DataFrame(rows)


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_date": ["2024-01-10", "2024-01-20"],
            "physical_acquisition_id": ["physical-a", "physical-a"],
            "acquisition_local_date": ["2024-01-01", "2024-01-01"],
            "lag_days": [9, 19],
        }
    )


def test_target_sharding_supports_one_acquisition_reused_across_dates() -> None:
    artifacts = build_previous_60_day_composites_by_target(
        _acquisition_rows(),
        _membership(),
        target_dates=["2024-01-10", "2024-01-20"],
        tract_geoids=["0001", "0002"],
        minimum_acquisition_coverage=0.8,
        minimum_acquisitions=1,
        final_test_year=2025,
        unlock_final_test=False,
    )

    assert len(artifacts.features) == 4
    assert len(artifacts.audit) == 4
    assert len(artifacts.lineage) == 4
    assert not artifacts.features.duplicated(["target_date", "tract_geoid"]).any()
    assert not artifacts.lineage.duplicated(
        ["target_date", "tract_geoid", "physical_acquisition_id"]
    ).any()
    assert set(artifacts.lineage["physical_acquisition_id"]) == {"physical-a"}
    assert set(artifacts.lineage["source_age_days_audit_only"]) == {9, 19}
    assert artifacts.audit["sentinel_feature_available"].all()
    for target_date in ("2024-01-10", "2024-01-20"):
        target = artifacts.features.loc[
            artifacts.features["target_date"] == target_date
        ].set_index("tract_geoid")
        assert target.loc["0001", INDEX_COLUMNS[0]] == pytest.approx(0.0)
        assert target.loc["0002", INDEX_COLUMNS[0]] == pytest.approx(1.0)


def test_target_sharding_rejects_undeclared_membership_date() -> None:
    with pytest.raises(ValueError, match="undeclared target date"):
        build_previous_60_day_composites_by_target(
            _acquisition_rows(),
            _membership(),
            target_dates=["2024-01-10"],
            tract_geoids=["0001", "0002"],
            minimum_acquisition_coverage=0.8,
            minimum_acquisitions=1,
            final_test_year=2025,
            unlock_final_test=False,
        )
