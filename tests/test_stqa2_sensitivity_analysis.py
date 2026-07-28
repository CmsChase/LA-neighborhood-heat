from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.stqa2_sensitivity_analysis as stqa2_module
from la_heat.model_result_analysis import ModelResultAnalysisError
from la_heat.provenance import atomic_json, canonical_sha256, parquet_file_record
from la_heat.stqa2_sensitivity_analysis import (
    PROVENANCE_FILENAME,
    Stqa2SensitivityConfig,
    Stqa2SensitivityError,
    TargetStage,
    _begin_output_transaction,
    authenticate_target_stage,
    build_date_retention,
    build_frozen_primary_oof_sensitivity,
    load_stqa2_sensitivity_config,
    validate_fixed_support,
    validate_research_config_pair,
)


def _with_locked_primary_config(
    config: Stqa2SensitivityConfig,
    tmp_path: Path,
) -> Stqa2SensitivityConfig:
    locked_primary = tmp_path / "research_locked.toml"
    source = config.primary_research_config.read_bytes()
    unlocked_flag = b"unlock_final_test = true"
    assert source.count(unlocked_flag) == 1
    locked_primary.write_bytes(
        source.replace(
            unlocked_flag,
            b"unlock_final_test = false",
            1,
        )
    )
    return replace(config, primary_research_config=locked_primary)


def _stage(*, strict: bool = False, denominator: int = 100) -> TargetStage:
    dates = pd.to_datetime(["2024-07-01", "2024-07-17"])
    qa = pd.DataFrame(
        {
            "tract_geoid": ["1", "2", "1", "2"],
            "target_date": dates.repeat(2),
            "overpass_id": ["a", "a", "b", "b"],
            "platform": ["landsat-8"] * 4,
            "spatial_block": ["x+0000_y+0000", "x+0001_y+0000"] * 2,
            "rasterized_pixel_count": [120] * 4,
            "footprint_pixel_count": [100] * 4,
            "eligible_pixel_count_static": [denominator] * 4,
            "eligible_pixel_identity_sha256": ["fixed"] * 4,
            "footprint_fraction": [1.0] * 4,
            "tract_manifest_sha256": ["tracts"] * 4,
            "grid_sha256": ["grid"] * 4,
            "valid_pixel_count": [50 if strict else 80] * 4,
            "target_available": [True] * 4,
            "date_usable": [True] * 4,
            "p90_st_uncertainty_k": [1.9 if strict else 3.0] * 4,
        }
    )
    summary = pd.DataFrame(
        {
            "target_date": dates,
            "overpass_id": ["a", "b"],
            "platform": ["landsat-8"] * 2,
            "retained_tract_count": [2, 2],
            "retained_tract_fraction": [1.0, 1.0],
            "date_usable": [True, True],
            "relative_endpoint_coverage_pass": [True, True],
        }
    )
    return TargetStage({}, "progress", qa, summary, qa.copy(), {})


def _write_complete_target_stage(
    directory: Path,
    *,
    state: str,
    target_date: str = "2024-07-01",
    ready_delta_c: float = 0.0,
) -> tuple[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    qa = pd.DataFrame(
        {
            "tract_geoid": ["1"],
            "target_date": [target_date],
            "date_usable": [True],
            "target_available": [True],
            "target_lst_c": [40.0],
            "spatial_block": ["x+0000_y+0000"],
        }
    )
    dates = pd.DataFrame(
        {
            "target_date": [target_date],
            "date_usable": [True],
        }
    )
    contributions = pd.DataFrame(
        {
            "target_date": [target_date],
            "overpass_id": ["o1"],
            "scene_id": ["s1"],
            "tract_geoid": ["1"],
        }
    )
    frames = {
        "development_target_qa.parquet": qa,
        "date_summary.parquet": dates,
        "scene_contributions.parquet": contributions,
    }
    if state == "model_ready":
        ready = qa.copy()
        ready["target_lst_c"] += ready_delta_c
        frames["development_targets_model_ready.parquet"] = ready
    records = {}
    for filename, frame in frames.items():
        path = directory / filename
        frame.to_parquet(path, index=False)
        records[filename] = parquet_file_record(path, frame)
    config_payload = {"landsat": {"apply_st_uncertainty_threshold": True}}
    config_sha = canonical_sha256(config_payload)
    pipeline = {"algorithm_version": "test-pipeline"}
    pipeline_sha = canonical_sha256(pipeline)
    config_file_sha = "config-file-sha"
    atomic_json(
        {
            "target_config_payload": config_payload,
            "target_config_sha256": config_sha,
            "target_pipeline_fingerprint": pipeline,
            "target_pipeline_sha256": pipeline_sha,
            "target_grid_identity_sha256": "grid",
            "static_land_mask_sha256": "land",
            "research_config_file_sha256": config_file_sha,
        },
        directory / "fixed_grid_lock.json",
    )
    promoted = state == "model_ready"
    atomic_json(
        {
            "state": state,
            "build_complete": True,
            "partial_outputs_only": False,
            "promoted_outputs_valid": promoted,
            "expected_overpass_count": 1,
            "completed_overpass_count": 1,
            "target_config_sha256": config_sha,
            "target_pipeline_sha256": pipeline_sha,
            "research_config_file_sha256": config_file_sha,
            "grid_sha256": "grid",
            "aggregate_outputs": records,
        },
        directory / "build_progress.json",
    )
    return config_sha, config_file_sha


def test_historical_locked_research_pair_is_frozen(tmp_path: Path) -> None:
    live = load_stqa2_sensitivity_config()
    assert "unlock_final_test = true" in live.primary_research_config.read_text(
        encoding="utf-8"
    )
    config = _with_locked_primary_config(live, tmp_path)
    locks = validate_research_config_pair(config)
    assert config.final_test_year == 2025
    assert config.strict_threshold_k == 2.0
    assert locks["primary_target_config_sha256"] != locks["strict_target_config_sha256"]


def test_research_pair_rejects_any_second_semantic_change(tmp_path: Path) -> None:
    config = _with_locked_primary_config(
        load_stqa2_sensitivity_config(),
        tmp_path,
    )
    changed = tmp_path / "strict.toml"
    text = config.strict_research_config.read_text(encoding="utf-8").replace(
        "minimum_cloud_distance_km = 1.0", "minimum_cloud_distance_km = 2.0"
    )
    changed.write_text(text, encoding="utf-8")
    with pytest.raises(Stqa2SensitivityError, match="differ only"):
        validate_research_config_pair(replace(config, strict_research_config=changed))


def test_fixed_support_allows_only_valid_pixel_reduction() -> None:
    joined = validate_fixed_support(_stage(), _stage(strict=True), threshold_k=2.0)
    assert len(joined) == 4
    assert (joined["valid_pixel_count_strict"] < joined["valid_pixel_count_primary"]).all()


def test_fixed_support_rejects_denominator_drift() -> None:
    with pytest.raises(Stqa2SensitivityError, match="eligible_pixel_count_static"):
        validate_fixed_support(_stage(), _stage(strict=True, denominator=99), threshold_k=2.0)


def test_date_retention_preserves_complete_date_identity() -> None:
    result = build_date_retention(_stage(), _stage(strict=True))
    assert len(result) == 2
    assert set(result["target_date"].dt.year) == {2024}
    assert (result["retained_tract_count_change"] == 0).all()


def test_fixed_support_rejects_missing_available_stqa() -> None:
    strict = _stage(strict=True)
    strict.target_qa.loc[0, "p90_st_uncertainty_k"] = np.nan
    with pytest.raises(Stqa2SensitivityError, match="ST_QA"):
        validate_fixed_support(_stage(), strict, threshold_k=2.0)


def test_fixed_support_rejects_new_strict_usable_date() -> None:
    primary = _stage()
    strict = _stage(strict=True)
    primary.target_qa.loc[
        primary.target_qa["target_date"].eq(pd.Timestamp("2024-07-17")), "date_usable"
    ] = False
    with pytest.raises(Stqa2SensitivityError, match="usable dates"):
        validate_fixed_support(primary, strict, threshold_k=2.0)


def test_authenticate_accepts_complete_gate_failed_as_derived_only(tmp_path: Path) -> None:
    config_sha, config_file_sha = _write_complete_target_stage(
        tmp_path, state="complete_gate_failed"
    )
    stage = authenticate_target_stage(
        tmp_path,
        expected_target_config_sha256=config_sha,
        expected_overpass_count=1,
        expected_tract_count=1,
        final_test_year=2025,
        require_config_file_sha256=config_file_sha,
        allow_complete_gate_failed=True,
    )
    assert stage.state == "complete_gate_failed"
    assert not stage.model_ready_promoted
    assert stage.analysis_rows_derived_from_complete_qa
    assert len(stage.model_ready) == 1
    with pytest.raises(Stqa2SensitivityError, match="accepted complete build"):
        authenticate_target_stage(
            tmp_path,
            expected_target_config_sha256=config_sha,
            expected_overpass_count=1,
            expected_tract_count=1,
            final_test_year=2025,
            require_config_file_sha256=config_file_sha,
        )


def test_authenticate_primary_semantic_lock_does_not_require_stale_file_byte_hash(
    tmp_path: Path,
) -> None:
    config_sha, _ = _write_complete_target_stage(tmp_path, state="model_ready")
    progress_path = tmp_path / "build_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("research_config_file_sha256")
    atomic_json(progress, progress_path)
    stage = authenticate_target_stage(
        tmp_path,
        expected_target_config_sha256=config_sha,
        expected_overpass_count=1,
        expected_tract_count=1,
        final_test_year=2025,
    )
    assert stage.model_ready_promoted


def test_authenticate_rejects_locked_year_and_model_ready_drift(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    config_sha, config_file_sha = _write_complete_target_stage(
        locked, state="complete_gate_failed", target_date="2025-07-01"
    )
    with pytest.raises(Stqa2SensitivityError, match="2025"):
        authenticate_target_stage(
            locked,
            expected_target_config_sha256=config_sha,
            expected_overpass_count=1,
            expected_tract_count=1,
            final_test_year=2025,
            require_config_file_sha256=config_file_sha,
            allow_complete_gate_failed=True,
        )

    drift = tmp_path / "drift"
    config_sha, config_file_sha = _write_complete_target_stage(
        drift, state="model_ready", ready_delta_c=0.5
    )
    with pytest.raises(Stqa2SensitivityError, match="exactly equal"):
        authenticate_target_stage(
            drift,
            expected_target_config_sha256=config_sha,
            expected_overpass_count=1,
            expected_tract_count=1,
            final_test_year=2025,
            require_config_file_sha256=config_file_sha,
        )


def test_sparse_bootstrap_failure_preserves_descriptive_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_stqa2_sensitivity_config()
    keys = pd.DataFrame(
        {
            "tract_geoid": ["1", "2"],
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-17"]),
            "target_lst_c": [40.0, 42.0],
            "spatial_block": ["a", "b"],
        }
    )
    oof_rows = []
    for model_id, offset in (("B1", 2.0), ("M2", 1.0)):
        for row in keys.itertuples(index=False):
            oof_rows.append(
                {
                    "tract_geoid": row.tract_geoid,
                    "target_date": row.target_date,
                    "spatial_block": row.spatial_block,
                    "family": "joint",
                    "model_id": model_id,
                    "y_true": row.target_lst_c,
                    "y_pred": row.target_lst_c + offset,
                }
            )

    def _raise_sparse(*args: object, **kwargs: object) -> dict[str, object]:
        raise ModelResultAnalysisError("A crossed bootstrap replicate contains no observations.")

    monkeypatch.setattr(stqa2_module, "crossed_date_spatial_block_bootstrap", _raise_sparse)
    predictions, metrics, bootstrap, reason = build_frozen_primary_oof_sensitivity(
        pd.DataFrame(oof_rows),
        keys,
        keys.assign(target_lst_c=keys["target_lst_c"] + 0.25),
        config=config,
    )
    assert len(predictions) == 4
    assert len(metrics) == 2
    assert bootstrap is None
    assert reason is not None and "sparse_date_block_support" in reason


def test_output_transaction_withdraws_old_complete_marker(tmp_path: Path) -> None:
    marker = tmp_path / PROVENANCE_FILENAME
    marker.write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    _begin_output_transaction(tmp_path)
    assert not marker.exists()
