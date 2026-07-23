from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.phase2_feature_stage as stage
from la_heat.phase2_feature_stage import (
    PHASE2_COVERAGE_FILENAME,
    PHASE2_FEATURE_FILENAME,
    PHASE2_PROVENANCE_FILENAME,
    build_phase2_feature_artifacts,
)
from la_heat.provenance import canonical_sha256, sha256_file

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
    keys = pd.DataFrame(
        {
            "tract_geoid": pd.Series(["06037000001", "06037000002"], dtype="string"),
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]),
        }
    )
    static = pd.DataFrame(
        {
            "tract_geoid": keys["tract_geoid"],
            "elevation_mean_m": [100.0, 200.0],
            "nlcd_developed_medium_fraction": [0.2, 0.3],
        }
    )
    calendar = keys.assign(
        calendar_doy_sin=[0.1, 0.1],
        calendar_doy_cos=[-0.9, -0.9],
    )
    sentinel = keys.assign(sentinel_ndvi_median=[0.4, None])
    daymet = keys.assign(daymet_tmax_c_mean_prev_1d=[30.0, 31.0])
    registry = _registry()
    paths = {
        "universe": tmp_path / "feature_key_universe.parquet",
        "registry": tmp_path / "registry.csv",
        "static": tmp_path / "static.parquet",
        "calendar": tmp_path / "calendar.parquet",
        "sentinel": tmp_path / "sentinel.parquet",
        "daymet": tmp_path / "daymet.parquet",
        "readiness": tmp_path / "readiness.json",
    }
    keys.to_parquet(paths["universe"], index=False)
    registry.to_csv(paths["registry"], index=False)
    static.to_parquet(paths["static"], index=False)
    calendar.to_parquet(paths["calendar"], index=False)
    sentinel.to_parquet(paths["sentinel"], index=False)
    daymet.to_parquet(paths["daymet"], index=False)

    readiness_keys = {
        "universe": "feature_key_universe",
        "registry": "phase2_registry",
        "static": "static_features",
        "calendar": "calendar_features",
        "sentinel": "sentinel_features",
        "daymet": "daymet_features",
    }
    readiness: dict[str, object] = {
        "state": "ready_for_feature_assembly",
        "audit_completed": True,
        "phase2_complete": False,
        "ready_for_feature_assembly": True,
        "blockers": [],
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "model_scores_read": False,
        "final_test_year": 2025,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "key_count": 2,
        "date_count": 1,
        "tract_count": 2,
        "registry_model_feature_count": 5,
        "family_status": [
            {
                "family": "sentinel",
                "available_row_count": 1,
                "missing_row_count": 1,
            }
        ],
        "inputs": {
            readiness_key: {
                "path": str(paths[label].resolve()),
                "sha256": sha256_file(paths[label]),
            }
            for label, readiness_key in readiness_keys.items()
        },
    }
    readiness["commit_sha256"] = canonical_sha256(readiness)
    paths["readiness"].write_text(json.dumps(readiness), encoding="utf-8")
    return paths


def _build(tmp_path: Path, paths: dict[str, Path]) -> dict[str, object]:
    return build_phase2_feature_artifacts(
        CONFIG_PATH,
        tmp_path / "output",
        readiness_path=paths["readiness"],
        universe_path=paths["universe"],
        registry_path=paths["registry"],
        static_path=paths["static"],
        calendar_path=paths["calendar"],
        sentinel_path=paths["sentinel"],
        daymet_path=paths["daymet"],
    )


def test_promotes_target_blind_table_in_registry_and_universe_order(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    payload = _build(tmp_path, paths)
    output = tmp_path / "output"
    table = pd.read_parquet(output / PHASE2_FEATURE_FILENAME)
    registry = pd.read_csv(paths["registry"])
    coverage = pd.read_csv(output / PHASE2_COVERAGE_FILENAME)
    marker = json.loads(
        (output / PHASE2_PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    commit = marker.pop("commit_sha256")

    assert payload["phase2_complete"] is True
    assert payload["target_blind"] is True
    assert payload["target_or_qa_tables_read"] == []
    assert payload["row_count"] == 2
    assert payload["column_count"] == 8
    assert payload["model_feature_count"] == 5
    assert payload["complete_model_feature_rows"] == 1
    assert table.columns.tolist() == registry["feature_name"].tolist()
    assert table["tract_geoid"].tolist() == ["06037000001", "06037000002"]
    assert "target_lst_c" not in table.columns
    assert "target_available" not in table.columns
    assert len(coverage) == 6
    assert canonical_sha256(marker) == commit


def test_blocked_readiness_fails_before_any_parquet_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_inputs(tmp_path)
    readiness = json.loads(paths["readiness"].read_text(encoding="utf-8"))
    readiness.pop("commit_sha256")
    readiness["state"] = "blocked_missing_daymet_values"
    readiness["ready_for_feature_assembly"] = False
    readiness["blockers"] = ["blocked_missing_authenticated_subsets"]
    readiness["commit_sha256"] = canonical_sha256(readiness)
    paths["readiness"].write_text(json.dumps(readiness), encoding="utf-8")
    reads: list[object] = []

    def forbidden_read(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append((args, kwargs))
        raise AssertionError("No Parquet may be read before readiness passes.")

    monkeypatch.setattr(stage.pd, "read_parquet", forbidden_read)
    with pytest.raises(stage.Phase2FeatureAssemblyError, match="not authorized"):
        _build(tmp_path, paths)
    assert reads == []


def test_partial_sentinel_row_is_rejected(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    sentinel = pd.read_parquet(paths["sentinel"])
    sentinel["sentinel_evi_extra"] = [0.2, None]
    registry = pd.read_csv(paths["registry"])
    extra = registry.loc[registry["feature_name"].eq("sentinel_ndvi_median")].copy()
    extra["feature_name"] = "sentinel_evi_extra"
    registry = pd.concat([registry, extra], ignore_index=True)
    sentinel.loc[1, "sentinel_ndvi_median"] = 0.4
    sentinel.to_parquet(paths["sentinel"], index=False)
    registry.to_csv(paths["registry"], index=False)

    readiness = json.loads(paths["readiness"].read_text(encoding="utf-8"))
    readiness.pop("commit_sha256")
    readiness["registry_model_feature_count"] = 6
    readiness["inputs"]["sentinel_features"]["sha256"] = sha256_file(
        paths["sentinel"]
    )
    readiness["inputs"]["phase2_registry"]["sha256"] = sha256_file(
        paths["registry"]
    )
    readiness["commit_sha256"] = canonical_sha256(readiness)
    paths["readiness"].write_text(json.dumps(readiness), encoding="utf-8")

    with pytest.raises(stage.Phase2FeatureAssemblyError, match="all registered indices"):
        _build(tmp_path, paths)


def test_tampered_input_fails_its_readiness_lock(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    registry = pd.read_csv(paths["registry"])
    registry.loc[registry["feature_name"].eq("elevation_mean_m"), "units"] = "km"
    registry.to_csv(paths["registry"], index=False)

    with pytest.raises(stage.Phase2FeatureAssemblyError, match="byte lock failed"):
        _build(tmp_path, paths)
