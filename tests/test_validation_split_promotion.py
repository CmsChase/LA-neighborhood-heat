from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

import la_heat.validation_split_promotion as promotion
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.validation_split_promotion import promote_validation_splits
from la_heat.validation_splits import build_validation_split_tables, validate_oof_coverage


def _tracts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "GEOID": ["a", "b"],
            "spatial_block": ["x+0000_y+0000", "x+0001_y+0000"],
        },
        geometry=[box(100, 100, 200, 200), box(6000, 100, 6100, 200)],
        crs="EPSG:3310",
    )


def _row_input() -> pd.DataFrame:
    rows = []
    mapping = dict(zip(_tracts()["GEOID"], _tracts()["spatial_block"], strict=True))
    for year in range(2020, 2025):
        for geoid in ("a", "b"):
            rows.append(
                {
                    "tract_geoid": geoid,
                    "target_date": f"{year}-07-01",
                    "spatial_block": mapping[geoid],
                }
            )
    return pd.DataFrame(rows)


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    tables = build_validation_split_tables(
        _row_input(),
        _tracts(),
        development_years=tuple(range(2020, 2025)),
        final_test_year=2025,
        analysis_crs="EPSG:3310",
        block_size_km=5.0,
        joint_buffer_m=1000.0,
    )
    split_directory = tmp_path / "splits"
    model_directory = tmp_path / "model"
    split_directory.mkdir()
    model_directory.mkdir()
    paths = {
        "draft": split_directory / "split_provenance.json",
        "rows": split_directory / "row_groups.parquet",
        "folds": split_directory / "fold_definitions.csv",
        "buffers": split_directory / "spatial_buffer_geoids.parquet",
        "model": model_directory / "development_model_table.parquet",
        "model_provenance": model_directory / "model_dataset_provenance.json",
        "output": split_directory / "split_promotion.json",
    }
    tables.row_groups.to_parquet(paths["rows"], index=False)
    tables.fold_definitions.to_csv(paths["folds"], index=False)
    tables.spatial_buffer_geoids.to_parquet(paths["buffers"], index=False)
    output_files = {
        "row_groups.parquet": parquet_file_record(paths["rows"], tables.row_groups),
        "fold_definitions.csv": {
            "sha256": sha256_file(paths["folds"]),
            "bytes": paths["folds"].stat().st_size,
            "rows": len(tables.fold_definitions),
        },
        "spatial_buffer_geoids.parquet": parquet_file_record(
            paths["buffers"], tables.spatial_buffer_geoids
        ),
    }
    draft: dict[str, object] = {
        "state": "predeclared_draft",
        "phase_complete": False,
        "ready_for_model_evaluation": False,
        "development_years": [2020, 2021, 2022, 2023, 2024],
        "final_test_year": 2025,
        "final_test_locked": True,
        "output_directory": str(split_directory.resolve()),
        "spatial_block_count": 2,
        "input_column_contract": {"target_or_predictor_values_read": False},
        "oof_coverage_audit": validate_oof_coverage(
            tables.row_groups,
            tables.fold_definitions,
            tables.spatial_buffer_geoids,
        ),
        "semantic_outputs": {
            "row_groups_sha256": canonical_frame_sha256(
                tables.row_groups, sort_by=["target_date", "tract_geoid"]
            ),
            "fold_definitions_sha256": canonical_frame_sha256(
                tables.fold_definitions, sort_by=["family", "fold_index"]
            ),
            "spatial_buffer_geoids_sha256": canonical_frame_sha256(
                tables.spatial_buffer_geoids,
                sort_by=["held_out_block", "tract_geoid"],
            ),
        },
        "output_files": output_files,
    }
    draft["commit_sha256"] = canonical_sha256(draft)
    paths["draft"].write_text(json.dumps(draft), encoding="utf-8")

    model = tables.row_groups.loc[:, ["tract_geoid", "target_date"]].copy()
    model.to_parquet(paths["model"], index=False)
    model_provenance: dict[str, object] = {
        "state": "complete",
        "algorithm_version": "gated-development-model-dataset-v1",
        "phase2_feature_commit_verified": True,
        "ready_for_modeling": True,
        "model_scores_read": False,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "model_feature_count": 46,
        "audit_only_feature_count": 1,
        "row_count": len(model),
        "column_count": 50,
        "output_files": {
            "development_model_table.parquet": {
                "path": str(paths["model"].resolve()),
                **parquet_file_record(paths["model"], model),
            }
        },
    }
    model_provenance["commit_sha256"] = canonical_sha256(model_provenance)
    paths["model_provenance"].write_text(
        json.dumps(model_provenance), encoding="utf-8"
    )
    return paths


def _promote(paths: dict[str, Path]) -> dict[str, object]:
    return promote_validation_splits(
        draft_provenance_path=paths["draft"],
        row_groups_path=paths["rows"],
        fold_definitions_path=paths["folds"],
        buffer_path=paths["buffers"],
        model_path=paths["model"],
        model_provenance_path=paths["model_provenance"],
        output_path=paths["output"],
    )


def test_promotes_exact_model_keys_without_reading_values(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    payload = _promote(paths)
    marker = json.loads(paths["output"].read_text(encoding="utf-8"))
    commit = marker.pop("commit_sha256")

    assert payload["state"] == "promoted"
    assert payload["phase_complete"] is True
    assert payload["ready_for_model_evaluation"] is True
    assert payload["row_count"] == 10
    assert payload["independent_date_count"] == 5
    assert payload["fold_counts"] == {"temporal": 5, "spatial": 2, "joint": 10}
    assert payload["target_values_read"] is False
    assert payload["predictor_values_read"] is False
    assert payload["columns_read_from_model_dataset"] == [
        "tract_geoid",
        "target_date",
    ]
    assert canonical_sha256(marker) == commit


def test_invalid_draft_commit_prevents_all_parquet_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_inputs(tmp_path)
    draft = json.loads(paths["draft"].read_text(encoding="utf-8"))
    draft["spatial_block_count"] = 99
    paths["draft"].write_text(json.dumps(draft), encoding="utf-8")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("Invalid draft commit must fail before Parquet reads.")

    monkeypatch.setattr(promotion.pd, "read_parquet", forbidden_read)
    with pytest.raises(
        promotion.ValidationSplitPromotionError, match="canonical commit is invalid"
    ):
        _promote(paths)
    assert reads == []


def test_model_key_mismatch_fails_promotion(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    model = pd.read_parquet(paths["model"]).iloc[:-1].copy()
    model.to_parquet(paths["model"], index=False)
    provenance = json.loads(paths["model_provenance"].read_text(encoding="utf-8"))
    provenance.pop("commit_sha256")
    provenance["row_count"] = len(model)
    provenance["output_files"]["development_model_table.parquet"] = {
        "path": str(paths["model"].resolve()),
        **parquet_file_record(paths["model"], model),
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    paths["model_provenance"].write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(promotion.ValidationSplitPromotionError, match="key mismatch"):
        _promote(paths)
