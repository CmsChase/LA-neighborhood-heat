from __future__ import annotations

from la_heat.multicity import portable_sentinel_build as engine
from la_heat.sentinel_compile_adapter import (
    build_previous_60_day_composites_by_target,
)
from scripts import build_portable_sentinel_features as launcher


def test_launcher_installs_cache_preserving_target_sharded_compile() -> None:
    original_pipeline_files = engine.PIPELINE_FILES

    launcher._install_compile_adapter()

    assert (
        engine.build_previous_60_day_composites
        is build_previous_60_day_composites_by_target
    )
    assert engine.PIPELINE_FILES == original_pipeline_files
    assert "scripts/build_portable_sentinel_features.py" not in engine.PIPELINE_FILES
    assert "src/la_heat/sentinel_compile_adapter.py" not in engine.PIPELINE_FILES
