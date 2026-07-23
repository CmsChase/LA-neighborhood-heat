import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

import la_heat.target_builder as target_builder
from la_heat.config import load_config
from la_heat.provenance import atomic_parquet, parquet_file_record, sha256_file
from la_heat.stage_config import inventory_config_sha256, target_config_sha256
from la_heat.target_builder import (
    _begin_build_transaction,
    _cache_is_current,
    _compile_completed_outputs,
)

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"
STRICT_STQA_CONFIG = (
    Path(__file__).parents[1] / "configs" / "research_stqa2_sensitivity.toml"
)


def _write_cache(
    root: Path,
    *,
    overpass_id: str,
    cache_lock: dict[str, str],
) -> Path:
    directory = root / "by_overpass" / overpass_id
    target = pd.DataFrame(
        {
            "tract_geoid": ["a"],
            "target_date": ["2024-07-01"],
            "footprint_fraction": [1.0],
            "valid_fraction": [1.0],
            "valid_pixel_count": [20],
            "eligible_pixel_count_static": [20],
            "eligible_pixel_identity_sha256": ["eligible"],
            "target_lst_c": [35.0],
            "target_available": [True],
            "date_usable": [True],
        }
    )
    contributions = pd.DataFrame(
        {
            "tract_geoid": ["a"],
            "scene_id": ["scene"],
            "selected_valid_pixel_count": [20],
        }
    )
    target_path = directory / "tract_date_qa.parquet"
    contribution_path = directory / "scene_contributions.parquet"
    atomic_parquet(target, target_path)
    atomic_parquet(contributions, contribution_path)
    summary = {
        "target_date": "2024-07-01",
        "overpass_id": overpass_id,
        "date_usable": True,
        "cache_lock": cache_lock,
        "output_files": {
            target_path.name: parquet_file_record(target_path, target),
            contribution_path.name: parquet_file_record(
                contribution_path, contributions
            ),
        },
    }
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return directory


def test_cache_requires_lock_and_output_file_integrity(tmp_path: Path) -> None:
    lock = {"pipeline": "v2", "overpass_id": "one"}
    directory = _write_cache(tmp_path, overpass_id="one", cache_lock=lock)
    assert _cache_is_current(directory, lock)
    assert not _cache_is_current(directory, {**lock, "pipeline": "v3"})
    with (directory / "tract_date_qa.parquet").open("ab") as handle:
        handle.write(b"changed")
    assert not _cache_is_current(directory, lock)


def test_incomplete_build_writes_partial_and_removes_model_ready(tmp_path: Path) -> None:
    base_lock = {
        "config_sha256": "config",
        "tract_manifest_sha256": "tracts",
        "grid_sha256": "grid",
    }
    first_row_lock = {
        **base_lock,
        "overpass_id": "one",
        "overpass_source_sha256": "source-one",
    }
    _write_cache(tmp_path, overpass_id="one", cache_lock=first_row_lock)
    manifest = pd.DataFrame(
        {
            "overpass_id": ["one", "two"],
            "source_lock_sha256": ["source-one", "source-two"],
        }
    )
    stale_model_ready = tmp_path / "development_targets_model_ready.parquet"
    atomic_parquet(pd.DataFrame({"stale": [1]}), stale_model_ready)
    progress = _compile_completed_outputs(
        manifest=manifest,
        output_directory=tmp_path,
        config=load_config(CONFIG),
        base_cache_lock=base_lock,
    )
    assert progress["completed_overpass_count"] == 1
    assert not progress["build_complete"]
    assert progress["partial_outputs_only"]
    assert (tmp_path / "development_target_qa_partial.parquet").exists()
    assert not stale_model_ready.exists()
    assert progress["state"] == "partial_ready"
    assert not progress["promoted_outputs_valid"]


def test_build_transaction_withdraws_stale_promotion_before_remote_work(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "development_targets_model_ready.parquet"
    atomic_parquet(pd.DataFrame({"stale": [1]}), stale)
    _begin_build_transaction(
        tmp_path,
        target_config_sha256_value="target",
        research_config_file_sha256="file",
    )
    progress = json.loads((tmp_path / "build_progress.json").read_text())
    assert progress["state"] == "preparing"
    assert not progress["promoted_outputs_valid"]
    assert not stale.exists()


def test_strict_stqa_config_changes_only_the_prespecified_pixel_mask() -> None:
    primary = load_config(CONFIG)
    strict = load_config(STRICT_STQA_CONFIG)
    expected = deepcopy(primary.raw)
    expected["landsat"]["apply_st_uncertainty_threshold"] = True

    assert strict.raw == expected
    assert strict.raw["landsat"]["maximum_st_uncertainty_kelvin"] == 2.0
    assert strict.raw["landsat"]["apply_st_uncertainty_threshold"] is True
    assert strict.final_test_year == 2025
    assert strict.final_test_unlocked is False
    assert inventory_config_sha256(strict) == inventory_config_sha256(primary)
    assert target_config_sha256(strict) != target_config_sha256(primary)


def test_target_build_can_write_strict_sensitivity_to_isolated_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    canonical = tmp_path / "data" / "interim" / "targets"
    canonical.mkdir(parents=True)
    canonical_sentinel = canonical / "canonical-output.must-not-change"
    canonical_sentinel.write_text("primary target is immutable", encoding="utf-8")
    sensitivity = tmp_path / "data" / "interim" / "targets_sensitivity_stqa2"
    observed_output_directories: list[Path] = []

    frozen = SimpleNamespace(
        city=object(),
        primary_overpasses=pd.DataFrame({"overpass_id": pd.Series(dtype=str)}),
        scenes=pd.DataFrame(),
        locks={"inventory_test_lock": "frozen"},
    )
    grid = SimpleNamespace(
        crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
        left=15.0,
        bottom=15.0,
        right=45.0,
        top=45.0,
        width=1,
        height=1,
        sha256="fixed-grid",
    )

    monkeypatch.setattr(target_builder, "_load_frozen_inventory", lambda _: frozen)

    def prepare_tracts(_config, _city, output_directory):
        observed_output_directories.append(output_directory)
        return pd.DataFrame(), "tract-manifest"

    monkeypatch.setattr(target_builder, "_prepare_primary_tracts", prepare_tracts)
    monkeypatch.setattr(
        target_builder,
        "_fixed_grid_and_zones",
        lambda *_: (
            grid,
            np.array([[0]], dtype=np.int32),
            np.array([[True]], dtype=bool),
            "grid-identity",
        ),
    )
    monkeypatch.setattr(
        target_builder,
        "_pipeline_fingerprint",
        lambda: ("pipeline", {"algorithm_version": "test"}),
    )

    def compile_outputs(*, manifest, output_directory, config, base_cache_lock):
        assert manifest.empty
        assert config.path == STRICT_STQA_CONFIG.resolve()
        assert base_cache_lock["research_config_file_sha256"] == sha256_file(
            STRICT_STQA_CONFIG
        )
        observed_output_directories.append(output_directory)
        return {"state": "test-complete"}

    monkeypatch.setattr(
        target_builder, "_compile_completed_outputs", compile_outputs
    )

    progress = target_builder.run_target_build(
        STRICT_STQA_CONFIG,
        output_directory=sensitivity,
    )

    assert progress == {"state": "test-complete"}
    assert observed_output_directories == [sensitivity, sensitivity]
    assert canonical_sentinel.read_text(encoding="utf-8") == (
        "primary target is immutable"
    )
    assert sorted(canonical.iterdir()) == [canonical_sentinel]
    build_progress = json.loads(
        (sensitivity / "build_progress.json").read_text(encoding="utf-8")
    )
    grid_lock = json.loads(
        (sensitivity / "fixed_grid_lock.json").read_text(encoding="utf-8")
    )
    expected_file_sha256 = sha256_file(STRICT_STQA_CONFIG)
    expected_target_sha256 = target_config_sha256(load_config(STRICT_STQA_CONFIG))
    assert build_progress["research_config_file_sha256"] == expected_file_sha256
    assert grid_lock["research_config_file_sha256"] == expected_file_sha256
    assert build_progress["target_config_sha256"] == expected_target_sha256
    assert grid_lock["target_config_sha256"] == expected_target_sha256
