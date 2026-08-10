from __future__ import annotations

import pandas as pd

from la_heat.multicity import portable_sentinel_build as engine
from la_heat.sentinel_features import INDEX_COLUMNS
from scripts import build_portable_sentinel_features as launcher


def test_launcher_installs_cache_preserving_target_sharded_compile() -> None:
    original_pipeline_files = engine.PIPELINE_FILES

    launcher._install_compile_adapter()

    assert engine.build_previous_60_day_composites is not None
    assert engine.PIPELINE_FILES == original_pipeline_files
    assert "scripts/build_portable_sentinel_features.py" not in engine.PIPELINE_FILES
    assert "src/la_heat/sentinel_compile_adapter.py" not in engine.PIPELINE_FILES


def test_launcher_adapter_removes_existing_city_id_before_portable_insert() -> None:
    launcher._install_compile_adapter()
    rows: list[dict[str, object]] = []
    for geoid in ("0001", "0002"):
        row: dict[str, object] = {
            "city_id": "chicago_il",
            "tract_geoid": geoid,
            "physical_acquisition_id": "physical-a",
            "acquisition_local_date": "2025-05-01",
            "acquisition_coverage_fraction": 1.0,
        }
        for column in INDEX_COLUMNS:
            row[column] = 1.0
        rows.append(row)
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-10", "2025-05-20"],
            "physical_acquisition_id": ["physical-a", "physical-a"],
            "acquisition_local_date": ["2025-05-01", "2025-05-01"],
            "lag_days": [9, 19],
        }
    )

    artifacts = engine.build_previous_60_day_composites(
        pd.DataFrame(rows),
        membership,
        target_dates=["2025-05-10", "2025-05-20"],
        tract_geoids=["0001", "0002"],
        minimum_acquisition_coverage=0.8,
        minimum_acquisitions=1,
        final_test_year=2025,
        unlock_final_test=True,
    )

    assert "city_id" not in artifacts.features
    assert "city_id" not in artifacts.audit
    assert "city_id" not in artifacts.lineage
