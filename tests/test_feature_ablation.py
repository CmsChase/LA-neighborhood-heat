from __future__ import annotations

import pandas as pd
import pytest

from la_heat.feature_ablation import (
    AblationSpec,
    FeatureAblationError,
    _task_payload,
    _validate_compiled_ablation_coverage,
)
from la_heat.model_task_engine import OuterFitTask
from la_heat.provenance import canonical_frame_sha256


class _Config:
    source_run_id = "source-run"


def _outer_task() -> OuterFitTask:
    return OuterFitTask(
        selection_config_sha256="selection",
        family="temporal",
        fold_index=0,
        fold_id="temporal_year_2020",
        held_out_year=2020,
        held_out_block=None,
        model_id="M2",
        expected_outer_train_row_count=8,
        expected_outer_test_row_count=2,
        expected_outer_purged_row_count=0,
        expected_outer_train_date_count=4,
        expected_outer_test_date_count=1,
        expected_inner_fold_count=4,
    )


def test_ablation_task_is_deterministic_and_carries_fixed_candidate() -> None:
    payload = _task_payload(
        ablation=AblationSpec("calendar_weather", frozenset({"calendar", "weather"})),
        outer_task=_outer_task(),
        selected_candidate_id="M2-hgb-leaf15-min50-l2-1",
        config=_Config(),  # type: ignore[arg-type]
        source_selection_lock="locked",
    )
    duplicate = _task_payload(
        ablation=AblationSpec("calendar_weather", frozenset({"weather", "calendar"})),
        outer_task=_outer_task(),
        selected_candidate_id="M2-hgb-leaf15-min50-l2-1",
        config=_Config(),  # type: ignore[arg-type]
        source_selection_lock="locked",
    )

    assert payload["task_id"] == duplicate["task_id"]
    assert payload["feature_families"] == ["calendar", "weather"]
    assert payload["selected_candidate_id"] == "M2-hgb-leaf15-min50-l2-1"
    assert payload["outer_task"]["task_id"] == _outer_task().task_id


def _compiled_rows() -> tuple[pd.DataFrame, str]:
    keys = pd.DataFrame(
        {
            "tract_geoid": ["06037000100", "06037000200"],
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]),
        }
    )
    frames = []
    for ablation_id in ("calendar_weather", "calendar_satellite"):
        for family in ("temporal", "spatial", "joint"):
            frame = keys.copy()
            frame.insert(0, "family", family)
            frame.insert(0, "ablation_id", ablation_id)
            frames.append(frame)
    return (
        pd.concat(frames, ignore_index=True),
        canonical_frame_sha256(keys, sort_by=["target_date", "tract_geoid"]),
    )


def test_compiled_coverage_requires_one_complete_face_per_family() -> None:
    combined, key_sha256 = _compiled_rows()
    _validate_compiled_ablation_coverage(
        combined,
        ablation_ids=("calendar_weather", "calendar_satellite"),
        expected_keys_sha256=key_sha256,
        expected_rows_per_family=2,
    )


def test_compiled_coverage_rejects_old_one_face_per_ablation_assumption() -> None:
    combined, key_sha256 = _compiled_rows()
    only_temporal = combined.loc[combined["family"].eq("temporal")]
    with pytest.raises(FeatureAblationError, match="row cardinality"):
        _validate_compiled_ablation_coverage(
            only_temporal,
            ablation_ids=("calendar_weather", "calendar_satellite"),
            expected_keys_sha256=key_sha256,
            expected_rows_per_family=2,
        )


def test_compiled_coverage_rejects_duplicate_within_family() -> None:
    combined, key_sha256 = _compiled_rows()
    duplicate = pd.concat([combined.iloc[:-1], combined.iloc[[-2]]], ignore_index=True)
    with pytest.raises(FeatureAblationError, match="one prediction"):
        _validate_compiled_ablation_coverage(
            duplicate,
            ablation_ids=("calendar_weather", "calendar_satellite"),
            expected_keys_sha256=key_sha256,
            expected_rows_per_family=2,
        )
