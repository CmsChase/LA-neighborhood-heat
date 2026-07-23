from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

import la_heat.model_run_context as run_context
from la_heat.model_run_context import load_model_run_context
from la_heat.model_selection import build_model_selection_freeze_manifest
from la_heat.portable_relocation import (
    PORTABLE_CONTEXT_PATHS,
    build_portable_relocation_manifest,
)
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.validation_splits import (
    build_validation_split_tables,
    validate_oof_coverage,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_SELECTION_CONFIG = PROJECT_ROOT / "configs" / "model_selection.toml"


def _tracts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "GEOID": ["a", "b"],
            "spatial_block": ["x+0000_y+0000", "x+0001_y+0000"],
        },
        geometry=[box(100, 100, 200, 200), box(6000, 100, 6100, 200)],
        crs="EPSG:3310",
    )


def _split_tables():
    tracts = _tracts()
    block_by_geoid = dict(zip(tracts["GEOID"], tracts["spatial_block"], strict=True))
    row_input = pd.DataFrame(
        [
            {
                "tract_geoid": geoid,
                "target_date": f"{year}-07-01",
                "spatial_block": block_by_geoid[geoid],
            }
            for year in range(2020, 2025)
            for geoid in ("a", "b")
        ]
    )
    return build_validation_split_tables(
        row_input,
        tracts,
        development_years=tuple(range(2020, 2025)),
        final_test_year=2025,
        analysis_crs="EPSG:3310",
        block_size_km=5.0,
        joint_buffer_m=1000.0,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_context_inputs(
    tmp_path: Path,
    *,
    canonical_layout: bool = False,
) -> dict[str, Path]:
    tables = _split_tables()
    if canonical_layout:
        model_dir = tmp_path / "data" / "processed" / "model_dataset"
        split_dir = tmp_path / "manifests" / "validation_splits"
        selection_dir = tmp_path / "manifests" / "model_selection"
        selection_config = tmp_path / "configs" / "model_selection.toml"
    else:
        model_dir = tmp_path / "model"
        split_dir = tmp_path / "splits"
        selection_dir = tmp_path / "selection"
        selection_config = MODEL_SELECTION_CONFIG
    model_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)
    selection_dir.mkdir(parents=True)
    if canonical_layout:
        selection_config.parent.mkdir(parents=True)
        shutil.copy2(MODEL_SELECTION_CONFIG, selection_config)
    paths = {
        "model": model_dir / "development_model_table.parquet",
        "registry": model_dir / "feature_registry.csv",
        "model_provenance": model_dir / "model_dataset_provenance.json",
        "rows": split_dir / "row_groups.parquet",
        "folds": split_dir / "fold_definitions.csv",
        "buffers": split_dir / "spatial_buffer_geoids.parquet",
        "split_promotion": split_dir / "split_promotion.json",
        "selection_freeze": selection_dir / "model_selection_freeze.json",
        "selection_config": selection_config,
    }

    tables.row_groups.to_parquet(paths["rows"], index=False)
    tables.fold_definitions.to_csv(paths["folds"], index=False)
    tables.spatial_buffer_geoids.to_parquet(paths["buffers"], index=False)
    registry = pd.DataFrame(
        {
            "feature_name": ["tract_geoid", "target_date", "x", "audit_flag"],
            "role": ["key", "key", "model", "audit_only"],
        }
    )
    registry.to_csv(paths["registry"], index=False)
    model = tables.row_groups.loc[:, ["tract_geoid", "target_date"]].copy()
    model["target_lst_c"] = range(len(model))
    model["x"] = range(100, 100 + len(model))
    model["audit_flag"] = 1.0
    model = model.iloc[::-1].reset_index(drop=True)
    model.to_parquet(paths["model"], index=False)

    model_record = {
        "path": str(paths["model"].resolve()),
        **parquet_file_record(paths["model"], model),
    }
    registry_record = {
        "path": str(paths["registry"].resolve()),
        "sha256": sha256_file(paths["registry"]),
        "bytes": paths["registry"].stat().st_size,
        "rows": len(registry),
    }
    model_provenance: dict[str, object] = {
        "state": "complete",
        "phase2_feature_commit_verified": True,
        "ready_for_modeling": True,
        "model_scores_read": False,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "row_count": len(model),
        "column_count": model.shape[1],
        "model_feature_count": 1,
        "audit_only_feature_count": 1,
        "semantic_model_table_sha256": canonical_frame_sha256(
            model, sort_by=["tract_geoid", "target_date"]
        ),
        "registry_semantic_sha256": canonical_frame_sha256(
            registry, sort_by=["feature_name"]
        ),
        "output_files": {
            paths["model"].name: model_record,
            paths["registry"].name: registry_record,
        },
    }
    _write_json(paths["model_provenance"], model_provenance)

    split_records = {
        paths["rows"].name: {
            "path": str(paths["rows"].resolve()),
            **parquet_file_record(paths["rows"], tables.row_groups),
        },
        paths["folds"].name: {
            "path": str(paths["folds"].resolve()),
            "sha256": sha256_file(paths["folds"]),
            "bytes": paths["folds"].stat().st_size,
            "rows": len(tables.fold_definitions),
        },
        paths["buffers"].name: {
            "path": str(paths["buffers"].resolve()),
            **parquet_file_record(paths["buffers"], tables.spatial_buffer_geoids),
        },
    }
    fold_counts = {
        family: int(tables.fold_definitions["family"].eq(family).sum())
        for family in ("temporal", "spatial", "joint")
    }
    split_promotion: dict[str, object] = {
        "state": "promoted",
        "phase_complete": True,
        "ready_for_model_evaluation": True,
        "target_values_read": False,
        "predictor_values_read": False,
        "model_scores_read": False,
        "columns_read_from_model_dataset": ["tract_geoid", "target_date"],
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "row_count": len(model),
        "fold_counts": fold_counts,
        "fold_count_total": len(tables.fold_definitions),
        "oof_coverage_audit": validate_oof_coverage(
            tables.row_groups,
            tables.fold_definitions,
            tables.spatial_buffer_geoids,
        ),
        "semantic_model_key_sha256": canonical_frame_sha256(
            model.loc[:, ["tract_geoid", "target_date"]],
            sort_by=["target_date", "tract_geoid"],
        ),
        "inputs": {
            "model_dataset_provenance": {
                "path": str(paths["model_provenance"].resolve()),
                "sha256": sha256_file(paths["model_provenance"]),
                "commit_sha256": model_provenance["commit_sha256"],
            },
            "model_dataset": model_record,
            "frozen_split_outputs": split_records,
        },
    }
    _write_json(paths["split_promotion"], split_promotion)
    build_model_selection_freeze_manifest(
        paths["selection_config"],
        selection_dir,
    )
    return paths


def _load(paths: dict[str, Path]):
    return load_model_run_context(
        model_provenance_path=paths["model_provenance"],
        model_table_path=paths["model"],
        registry_path=paths["registry"],
        split_promotion_path=paths["split_promotion"],
        row_groups_path=paths["rows"],
        fold_definitions_path=paths["folds"],
        spatial_buffers_path=paths["buffers"],
        model_selection_freeze_path=paths["selection_freeze"],
        model_selection_config_path=paths["selection_config"],
        expected_fold_count_total=17,
    )


def test_loads_small_frozen_context_in_row_group_order(tmp_path: Path) -> None:
    paths = _write_context_inputs(tmp_path)
    context = _load(paths)

    pd.testing.assert_frame_equal(
        context.dataset.loc[:, ["tract_geoid", "target_date"]],
        context.row_groups.loc[:, ["tract_geoid", "target_date"]],
    )
    pd.testing.assert_frame_equal(context.keys, context.dataset.iloc[:, :2])
    assert context.fold_definitions["held_out_year"].dtype == pd.Int64Dtype()
    assert len(context.fold_definitions) == 17
    assert len(context.model_selection.candidates) == 31
    assert context.features.columns.tolist() == ["x"]
    assert context.audit_only.columns.tolist() == ["audit_flag"]
    assert len(context.run_id) == 64


def test_bad_commit_fails_before_any_parquet_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_context_inputs(tmp_path)
    payload = json.loads(paths["model_provenance"].read_text(encoding="utf-8"))
    payload["row_count"] = 999
    paths["model_provenance"].write_text(json.dumps(payload), encoding="utf-8")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("An invalid manifest must fail before Parquet access.")

    monkeypatch.setattr(run_context.pd, "read_parquet", forbidden_read)
    with pytest.raises(run_context.ModelRunContextError, match="canonical commit"):
        _load(paths)
    assert reads == []


def test_model_and_row_group_key_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_context_inputs(tmp_path)
    model = pd.read_parquet(paths["model"])
    model.loc[0, "tract_geoid"] = "unexpected"
    model.to_parquet(paths["model"], index=False)

    provenance = json.loads(paths["model_provenance"].read_text(encoding="utf-8"))
    provenance.pop("commit_sha256")
    model_record = {
        "path": str(paths["model"].resolve()),
        **parquet_file_record(paths["model"], model),
    }
    provenance["output_files"][paths["model"].name] = model_record
    provenance["semantic_model_table_sha256"] = canonical_frame_sha256(
        model, sort_by=["tract_geoid", "target_date"]
    )
    _write_json(paths["model_provenance"], provenance)

    promotion = json.loads(paths["split_promotion"].read_text(encoding="utf-8"))
    promotion.pop("commit_sha256")
    promotion["inputs"]["model_dataset"] = model_record
    promotion["inputs"]["model_dataset_provenance"] = {
        "path": str(paths["model_provenance"].resolve()),
        "sha256": sha256_file(paths["model_provenance"]),
        "commit_sha256": provenance["commit_sha256"],
    }
    _write_json(paths["split_promotion"], promotion)

    with pytest.raises(run_context.ModelRunContextError, match="key mismatch"):
        _load(paths)


def _copy_portable_context(source_root: Path, bundle_root: Path) -> None:
    bundle_root.mkdir(parents=True)
    for relative_path in PORTABLE_CONTEXT_PATHS.values():
        source = source_root / relative_path
        destination = bundle_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for relative_path in (
        "src/la_heat/model_dataset.py",
        "src/la_heat/model_run_context.py",
        "src/la_heat/model_runtime.py",
        "src/la_heat/model_selection.py",
        "src/la_heat/portable_relocation.py",
        "src/la_heat/provenance.py",
        "src/la_heat/validation_splits.py",
    ):
        source = PROJECT_ROOT / relative_path
        destination = bundle_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def test_explicit_relocation_loads_without_rewriting_original_manifests(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    paths = _write_context_inputs(source_root, canonical_layout=True)
    bundle_root = tmp_path / "different" / "bundle"
    _copy_portable_context(source_root, bundle_root)
    original_hashes = {
        name: sha256_file(paths[name])
        for name in ("model_provenance", "split_promotion", "selection_freeze")
    }

    relocation_path = build_portable_relocation_manifest(
        source_root,
        bundle_root,
    )
    context = load_model_run_context(
        portable_manifest_path=relocation_path,
        portable_root=bundle_root,
        expected_fold_count_total=17,
    )

    assert len(context.keys) == 10
    assert context.portable_relocation_commit_sha256 is not None
    assert {
        name: sha256_file(paths[name])
        for name in ("model_provenance", "split_promotion", "selection_freeze")
    } == original_hashes
    with pytest.raises(run_context.ModelRunContextError, match="path lock"):
        load_model_run_context(
            model_provenance_path=bundle_root
            / PORTABLE_CONTEXT_PATHS["model_provenance"],
            model_table_path=bundle_root / PORTABLE_CONTEXT_PATHS["model_table"],
            registry_path=bundle_root / PORTABLE_CONTEXT_PATHS["registry"],
            split_promotion_path=bundle_root
            / PORTABLE_CONTEXT_PATHS["split_promotion"],
            row_groups_path=bundle_root / PORTABLE_CONTEXT_PATHS["row_groups"],
            fold_definitions_path=bundle_root / PORTABLE_CONTEXT_PATHS["folds"],
            spatial_buffers_path=bundle_root / PORTABLE_CONTEXT_PATHS["buffers"],
            model_selection_freeze_path=bundle_root
            / PORTABLE_CONTEXT_PATHS["selection_freeze"],
            model_selection_config_path=bundle_root
            / PORTABLE_CONTEXT_PATHS["selection_config"],
            expected_fold_count_total=17,
        )


def test_relocated_byte_drift_fails_before_any_parquet_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_context_inputs(source_root, canonical_layout=True)
    bundle_root = tmp_path / "bundle"
    _copy_portable_context(source_root, bundle_root)
    relocation_path = build_portable_relocation_manifest(source_root, bundle_root)
    relocated_model = bundle_root / PORTABLE_CONTEXT_PATHS["model_table"]
    relocated_model.write_bytes(relocated_model.read_bytes() + b"drift")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("Relocation locks must fail before Parquet access.")

    monkeypatch.setattr(run_context.pd, "read_parquet", forbidden_read)
    with pytest.raises(run_context.ModelRunContextError, match="byte lock failed"):
        load_model_run_context(
            portable_manifest_path=relocation_path,
            portable_root=bundle_root,
            expected_fold_count_total=17,
        )
    assert reads == []
