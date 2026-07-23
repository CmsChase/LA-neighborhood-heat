from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import la_heat.final_model as final_module
from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.final_model import (
    FINAL_MODEL_ALGORITHM_VERSION,
    FINAL_MODEL_SCHEMA_VERSION,
    FinalModelError,
    atomic_dump_model_bundle,
    authenticate_final_build_provenance,
    load_final_model_config,
    load_hashed_model_bundle,
    prepare_final_model_build,
    run_final_model_build,
)
from la_heat.model_run_context import ModelRunContext
from la_heat.model_selection import load_model_selection_config

ROOT = Path(__file__).resolve().parents[1]
YEARS = tuple(range(2020, 2025))


class SyntheticPipeline:
    def __init__(self, feature_names: list[str], offset: float = 0.0):
        self.feature_names = feature_names
        self.offset = offset

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return frame[self.feature_names[0]].to_numpy(dtype=float) + self.offset


def _registry_row(name: str, family: str, *, static: bool, start=None, end=None):
    return {
        "feature_name": name,
        "family": family,
        "role": "model",
        "units": "unitless",
        "source": "synthetic legal source",
        "static": static,
        "available_by": "2019-01-01" if static else "historical archive",
        "source_start_offset_days": start,
        "source_end_offset_days": end,
    }


def _registry() -> pd.DataFrame:
    rows = [
        {
            "feature_name": "tract_geoid",
            "family": "key",
            "role": "key",
            "units": "identifier",
            "source": "tract manifest",
            "static": True,
            "available_by": "2019-01-01",
            "source_start_offset_days": None,
            "source_end_offset_days": None,
        },
        {
            "feature_name": "target_date",
            "family": "key",
            "role": "key",
            "units": "date",
            "source": "join key",
            "static": False,
            "available_by": "prediction origin",
            "source_start_offset_days": None,
            "source_end_offset_days": None,
        },
    ]
    rows.extend(
        _registry_row(f"land_feature_{index:02d}", "land_use", static=True)
        for index in range(9)
    )
    rows.extend(
        _registry_row(f"geo_feature_{index:02d}", "geography", static=True)
        for index in range(9)
    )
    rows.extend(calendar_feature_registry_rows().to_dict("records"))
    rows.extend(
        _registry_row(
            f"weather_feature_{index:02d}",
            "weather",
            static=False,
            start=-7,
            end=-1,
        )
        for index in range(21)
    )
    rows.extend(
        _registry_row(
            f"satellite_feature_{index:02d}",
            "satellite",
            static=False,
            start=-60,
            end=-1,
        )
        for index in range(5)
    )
    rows.append({**_registry_row("coverage_audit", "audit", static=False), "role": "audit_only"})
    return pd.DataFrame(rows)


def _context() -> ModelRunContext:
    registry = _registry()
    records = []
    blocks = {"g0": "A", "g1": "A", "g2": "B", "g3": "B"}
    for year in YEARS:
        for geoid, block in blocks.items():
            records.append(
                {
                    "tract_geoid": geoid,
                    "target_date": pd.Timestamp(year=year, month=7, day=15),
                    "spatial_block": block,
                    "year": year,
                }
            )
    groups = pd.DataFrame(records)
    keys = groups[["tract_geoid", "target_date"]].copy()
    names = registry.loc[registry["role"].eq("model"), "feature_name"].tolist()
    rng = np.random.default_rng(77)
    features = pd.DataFrame(rng.normal(size=(len(groups), len(names))), columns=names)
    features["calendar_doy_sin"] = np.sin(np.linspace(0.1, 2.0, len(groups)))
    features["calendar_doy_cos"] = np.cos(np.linspace(0.1, 2.0, len(groups)))
    target = pd.Series(features[names[0]].to_numpy() + 0.1, name="target_lst_c")
    selection = load_model_selection_config(ROOT / "configs" / "model_selection.toml")
    return ModelRunContext(
        run_id="synthetic-context",
        model_dataset_commit_sha256="1" * 64,
        split_promotion_commit_sha256="2" * 64,
        model_selection_commit_sha256="3" * 64,
        runtime_fingerprint_sha256="4" * 64,
        dataset=pd.DataFrame(index=groups.index),
        registry=registry,
        row_groups=groups,
        fold_definitions=pd.DataFrame(),
        spatial_buffer_geoids=pd.DataFrame(),
        features=features,
        target=target,
        keys=keys,
        audit_only=pd.DataFrame(index=groups.index),
        model_selection=selection,
    )


def _config(tmp_path: Path):
    production = load_final_model_config(ROOT / "configs" / "final_model.toml")
    return replace(
        production,
        output_root=tmp_path / "builds",
        model_lock_staging_path=tmp_path / "MODEL_LOCK_STAGING.json",
        expected_tract_date_rows=20,
        expected_independent_dates=5,
        expected_independent_spatial_blocks=2,
    )


def _fake_factories(monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, int]]):
    def make_spec(registry: pd.DataFrame, model_id: str, **kwargs):
        rows = registry.loc[registry["role"].eq("model")]
        if model_id == "B1":
            rows = rows.loc[rows["family"].isin(["calendar", "weather"])]
        names = rows["feature_name"].tolist()
        numeric = [float(value) for key, value in kwargs.items() if key != "random_state"]
        offset = sum(numeric) * 1.0e-6
        return SimpleNamespace(
            model_id=model_id,
            feature_names=tuple(names),
            pipeline=SyntheticPipeline(names, offset),
        )

    def fit_model(spec, frame, target, keys):
        calls.append((spec.model_id, len(frame)))
        return SimpleNamespace(
            spec=spec,
            pipeline=spec.pipeline,
            training_row_count=len(frame),
            training_date_count=int(pd.to_datetime(keys["target_date"]).nunique()),
        )

    def predict_model(fitted, frame):
        return fitted.pipeline.predict(frame.loc[:, list(fitted.spec.feature_names)])

    monkeypatch.setattr(final_module, "make_model_spec", make_spec)
    monkeypatch.setattr(final_module, "fit_fold_model", fit_model)
    monkeypatch.setattr(final_module, "predict_fold_model", predict_model)


def test_production_config_freezes_development_only_b1_m2_contract() -> None:
    config = load_final_model_config(ROOT / "configs" / "final_model.toml")

    assert config.development_years == YEARS
    assert config.final_test_year == 2025
    assert config.model_ids == ("B1", "M2")
    assert config.expected_tract_date_rows == 63_403
    assert config.expected_independent_dates == 65
    assert config.expected_independent_spatial_blocks == 71
    assert config.expected_model_feature_count == 46


def test_plan_is_exact_65_candidate_year_tasks_and_contains_no_2025(tmp_path: Path) -> None:
    prepared = prepare_final_model_build(_context(), _config(tmp_path))

    assert len(prepared.tasks) == 65
    assert len({task.task_id for task in prepared.tasks}) == 65
    assert {task.model_id for task in prepared.tasks} == {"B1", "M2"}
    assert {task.validation_year for task in prepared.tasks} == set(YEARS)
    assert sum(task.model_id == "B1" for task in prepared.tasks) == 25
    assert sum(task.model_id == "M2" for task in prepared.tasks) == 40
    assert prepared.manifest["contains_final_test_year"] is False


def test_context_rejects_2025_before_any_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    context.row_groups.loc[0, "target_date"] = pd.Timestamp("2025-07-15")
    context.row_groups.loc[0, "year"] = 2025
    context.keys.loc[0, "target_date"] = pd.Timestamp("2025-07-15")
    monkeypatch.setattr(
        final_module,
        "fit_fold_model",
        lambda *args, **kwargs: pytest.fail("fit must not be reached"),
    )

    with pytest.raises(PermissionError, match="2020--2024"):
        prepare_final_model_build(context, _config(tmp_path))


def test_synthetic_full_build_selects_refits_serializes_and_authenticates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    _fake_factories(monkeypatch, calls)
    context = _context()
    config = _config(tmp_path)
    prepared = prepare_final_model_build(context, config)
    stale = prepared.run_directory / "final_model_build_provenance.json"
    stale.write_text("{}", encoding="utf-8")
    config.output_root.mkdir(parents=True, exist_ok=True)
    (config.output_root / "latest_build.json").write_text("stale", encoding="utf-8")

    result = run_final_model_build(
        context,
        config,
        input_locks={"feature_registry_semantic_sha256": "5" * 64},
    )

    assert result["state"] == "complete_development_only"
    assert result["contains_final_test_year"] is False
    assert result["final_test_values_read"] is False
    assert result["tuning_fit_count"] == 65
    assert result["tuning_score_row_count"] == 65
    assert len(calls) == 67
    assert sum(count == 16 for _, count in calls) == 65
    assert sum(count == 20 for _, count in calls) == 2
    assert result["models"]["B1"]["feature_count"] == 23
    assert result["models"]["M2"]["feature_count"] == 46
    for model_id in ("B1", "M2"):
        record = result["models"][model_id]
        bundle = load_hashed_model_bundle(
            prepared.run_directory / record["path"],
            expected_sha256=record["sha256"],
            expected_bytes=record["bytes"],
            expected_model_id=model_id,
            expected_candidate_id=record["selected_candidate_id"],
        )
        assert bundle["training_row_count"] == 20
        assert bundle["training_date_count"] == 5
    authenticated = authenticate_final_build_provenance(
        stale,
        load_models=True,
    )
    assert authenticated["commit_sha256"] == result["commit_sha256"]
    assert (config.output_root / "latest_build.json").is_file()


def _bundle() -> dict[str, object]:
    return {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "model_id": "M2",
        "candidate_id": "M2-hgb-leaf15-min50-l2-1",
        "candidate_parameters": {
            "hgb_learning_rate": 0.05,
            "hgb_max_iter": 300,
            "hgb_max_leaf_nodes": 15,
            "hgb_min_samples_leaf": 50,
            "hgb_l2_regularization": 1.0,
        },
        "random_state": 20260719,
        "feature_names": ["x"],
        "training_row_count": 10,
        "training_date_count": 5,
        "training_spatial_block_count": 2,
        "training_keys_sha256": "a" * 64,
        "pipeline": SyntheticPipeline(["x"]),
    }


def test_model_hash_is_checked_before_joblib_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "model.joblib"
    record = atomic_dump_model_bundle(_bundle(), path)
    with path.open("ab") as handle:
        handle.write(b"tamper")

    monkeypatch.setattr(
        final_module.joblib,
        "load",
        lambda *args, **kwargs: pytest.fail("joblib.load must not be called"),
    )
    with pytest.raises(FinalModelError, match="byte lock"):
        load_hashed_model_bundle(
            path,
            expected_sha256=record["sha256"],
            expected_bytes=record["bytes"],
        )


def test_failed_atomic_joblib_write_cleans_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "model.joblib"

    def broken_dump(bundle, temporary, compress):
        Path(temporary).write_bytes(b"partial")
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(final_module.joblib, "dump", broken_dump)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        atomic_dump_model_bundle(_bundle(), path)

    assert not path.exists()
    assert not path.with_suffix(".joblib.partial").exists()
