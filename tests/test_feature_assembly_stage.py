from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.feature_assembly_stage as stage
from la_heat.config import load_config
from la_heat.feature_assembly_stage import (
    MODEL_DATASET_FILENAME,
    MODEL_DATASET_PROVENANCE_FILENAME,
    build_model_dataset_artifacts,
)
from la_heat.model_dataset import extract_registered_model_data
from la_heat.phase2_feature_stage import (
    PHASE2_FEATURE_FILENAME,
    PHASE2_PROVENANCE_FILENAME,
    PHASE2_REGISTRY_FILENAME,
)
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import target_config_sha256

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "research.toml"


def _registry() -> pd.DataFrame:
    rows = [
        (
            "tract_geoid",
            "key",
            "key",
            "identifier",
            "Census",
            True,
            "2019-01-01",
            None,
            None,
        ),
        (
            "target_date",
            "key",
            "key",
            "date",
            "schedule",
            False,
            "target date",
            None,
            None,
        ),
        (
            "elevation_mean_m",
            "geography",
            "model",
            "m",
            "SRTM",
            True,
            "2015-01-01",
            None,
            None,
        ),
        (
            "nlcd_developed_medium_fraction",
            "land_use",
            "audit_only",
            "fraction",
            "NLCD",
            True,
            "2019-04-30",
            None,
            None,
        ),
        (
            "calendar_doy_sin",
            "calendar",
            "model",
            "unitless",
            "Deterministic target-date calendar known at prediction origin",
            False,
            "prediction origin",
            None,
            None,
        ),
        (
            "calendar_doy_cos",
            "calendar",
            "model",
            "unitless",
            "Deterministic target-date calendar known at prediction origin",
            False,
            "prediction origin",
            None,
            None,
        ),
        (
            "sentinel_ndvi_median",
            "satellite",
            "model",
            "unitless",
            "Sentinel-2",
            False,
            "historical archive",
            -60,
            -1,
        ),
        (
            "daymet_tmax_c_mean_prev_1d",
            "weather",
            "model",
            "degC",
            "Daymet",
            False,
            "historical archive",
            -1,
            -1,
        ),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "feature_name",
            "family",
            "role",
            "units",
            "source",
            "static",
            "available_by",
            "source_start_offset_days",
            "source_end_offset_days",
        ],
    )


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    registry = _registry()
    phase2 = pd.DataFrame(
        {
            "tract_geoid": pd.Series(["06037000001", "06037000002"], dtype="string"),
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]),
            "elevation_mean_m": [100.0, 200.0],
            "nlcd_developed_medium_fraction": [0.2, 0.3],
            "calendar_doy_sin": [0.1, 0.1],
            "calendar_doy_cos": [-0.9, -0.9],
            "sentinel_ndvi_median": [0.4, None],
            "daymet_tmax_c_mean_prev_1d": [30.0, 31.0],
        }
    )
    target = pd.DataFrame(
        {
            "tract_geoid": ["06037000001", "06037000002"],
            "target_date": ["2024-07-01", "2024-07-01"],
            "target_available": [True, True],
            "date_usable": [True, True],
            "target_lst_c": [35.0, 36.0],
            "spatial_block": ["q1", "q2"],
            "mean_lst_c": [35.0, 36.0],
        }
    )
    phase2_directory = tmp_path / "phase2"
    phase2_directory.mkdir()
    paths = {
        "phase2": phase2_directory / PHASE2_FEATURE_FILENAME,
        "phase2_registry": phase2_directory / PHASE2_REGISTRY_FILENAME,
        "phase2_provenance": phase2_directory / PHASE2_PROVENANCE_FILENAME,
        "target": tmp_path / "development_targets_model_ready.parquet",
        "target_progress": tmp_path / "target_progress.json",
    }
    phase2.to_parquet(paths["phase2"], index=False)
    registry.to_csv(paths["phase2_registry"], index=False)
    target.to_parquet(paths["target"], index=False)

    phase2_record = {
        "path": str(paths["phase2"].resolve()),
        **parquet_file_record(paths["phase2"], phase2),
    }
    phase2_provenance: dict[str, object] = {
        "state": "complete",
        "phase2_complete": True,
        "ready_for_target_join": True,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "model_scores_read": False,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "row_count": len(phase2),
        "column_count": phase2.shape[1],
        "ordered_columns": registry["feature_name"].tolist(),
        "semantic_feature_table_sha256": canonical_frame_sha256(
            phase2,
            sort_by=["target_date", "tract_geoid"],
            columns=registry["feature_name"].tolist(),
        ),
        "registry_semantic_sha256": canonical_frame_sha256(
            registry, sort_by=["feature_name"]
        ),
        "output_files": {
            PHASE2_FEATURE_FILENAME: phase2_record,
            PHASE2_REGISTRY_FILENAME: {
                "path": str(paths["phase2_registry"].resolve()),
                "sha256": sha256_file(paths["phase2_registry"]),
                "bytes": paths["phase2_registry"].stat().st_size,
                "rows": len(registry),
            },
        },
    }
    phase2_provenance["commit_sha256"] = canonical_sha256(phase2_provenance)
    paths["phase2_provenance"].write_text(
        json.dumps(phase2_provenance), encoding="utf-8"
    )

    target_record = parquet_file_record(paths["target"], target)
    progress = {
        "state": "model_ready",
        "build_complete": True,
        "promoted_outputs_valid": True,
        "partial_outputs_only": False,
        "expected_overpass_count": 1,
        "completed_overpass_count": 1,
        "target_config_sha256": target_config_sha256(load_config(CONFIG_PATH)),
        "aggregate_outputs": {paths["target"].name: target_record},
    }
    paths["target_progress"].write_text(json.dumps(progress), encoding="utf-8")
    return paths


def _build(tmp_path: Path, paths: dict[str, Path]) -> dict[str, object]:
    return build_model_dataset_artifacts(
        CONFIG_PATH,
        tmp_path / "output",
        phase2_path=paths["phase2"],
        phase2_provenance_path=paths["phase2_provenance"],
        phase2_registry_path=paths["phase2_registry"],
        target_progress_path=paths["target_progress"],
        target_path=paths["target"],
    )


def test_gated_join_promotes_only_target_and_registered_columns(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    payload = _build(tmp_path, paths)
    output = tmp_path / "output"
    table = pd.read_parquet(output / MODEL_DATASET_FILENAME)
    registry = pd.read_csv(paths["phase2_registry"])
    marker = json.loads(
        (output / MODEL_DATASET_PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    commit = marker.pop("commit_sha256")
    features, target, keys, audit = extract_registered_model_data(table, registry)

    assert payload["phase2_feature_commit_verified"] is True
    assert payload["ready_for_modeling"] is True
    assert payload["row_count"] == 2
    assert payload["column_count"] == 9
    assert payload["independent_date_count"] == 1
    assert payload["model_feature_count"] == 5
    assert payload["complete_model_feature_rows"] == 1
    assert table.columns.tolist() == [
        "tract_geoid",
        "target_date",
        "target_lst_c",
        *registry.loc[~registry["role"].eq("key"), "feature_name"].tolist(),
    ]
    assert "target_available" not in table.columns
    assert "date_usable" not in table.columns
    assert "spatial_block" not in table.columns
    assert "mean_lst_c" not in table.columns
    assert "nlcd_developed_medium_fraction" not in features.columns
    assert audit.columns.tolist() == ["nlcd_developed_medium_fraction"]
    assert target.tolist() == [35.0, 36.0]
    assert len(keys) == 2
    assert canonical_sha256(marker) == commit


def test_invalid_phase2_commit_fails_before_any_parquet_or_target_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_inputs(tmp_path)
    provenance = json.loads(paths["phase2_provenance"].read_text(encoding="utf-8"))
    provenance["row_count"] = 999
    paths["phase2_provenance"].write_text(json.dumps(provenance), encoding="utf-8")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("No Parquet may be read before the Phase 2 gate passes.")

    monkeypatch.setattr(stage.pd, "read_parquet", forbidden_read)
    with pytest.raises(stage.FeatureAssemblyError, match="invalid canonical commit"):
        _build(tmp_path, paths)
    assert reads == []


def test_tampered_phase2_table_fails_before_parquet_or_target_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_inputs(tmp_path)
    with paths["phase2"].open("ab") as handle:
        handle.write(b"tampered")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("A bad Phase 2 byte lock must fail before reads.")

    monkeypatch.setattr(stage.pd, "read_parquet", forbidden_read)
    with pytest.raises(stage.FeatureAssemblyError, match="output byte lock failed"):
        _build(tmp_path, paths)
    assert reads == []


def test_invalid_target_progress_does_not_open_target_parquet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_inputs(tmp_path)
    progress = json.loads(paths["target_progress"].read_text(encoding="utf-8"))
    progress["promoted_outputs_valid"] = False
    paths["target_progress"].write_text(json.dumps(progress), encoding="utf-8")
    original = stage.pd.read_parquet
    reads: list[Path] = []

    def tracked_read(path: str | Path, *args: object, **kwargs: object) -> pd.DataFrame:
        resolved = Path(path).resolve()
        reads.append(resolved)
        if resolved == paths["target"].resolve():
            raise AssertionError("Invalid target progress must prevent target access.")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(stage.pd, "read_parquet", tracked_read)
    with pytest.raises(stage.FeatureAssemblyError, match="not a locked model-ready"):
        _build(tmp_path, paths)
    assert paths["phase2"].resolve() in reads
    assert paths["target"].resolve() not in reads
