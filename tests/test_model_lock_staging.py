from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.model_lock_staging as staging_module
from la_heat.final_model import (
    FINAL_MODEL_ALGORITHM_VERSION,
    FINAL_MODEL_SCHEMA_VERSION,
    atomic_dump_model_bundle,
    load_final_model_config,
)
from la_heat.model_lock_staging import ModelLockStagingError, stage_model_lock
from la_heat.provenance import canonical_sha256, sha256_file

ROOT = Path(__file__).resolve().parents[1]


class SyntheticPipeline:
    def __init__(self, feature_names: list[str]):
        self.feature_names = feature_names

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return frame[self.feature_names[0]].to_numpy(dtype=float)


def _write_config(tmp_path: Path, *, robustness_exists: bool) -> Path:
    source = (ROOT / "configs" / "final_model.toml").read_text(encoding="utf-8")
    robustness = [
        tmp_path / "feature_ablation_analysis_provenance.json",
        tmp_path / "stqa2_sensitivity_provenance.json",
        tmp_path / "robustness_reconciliation_provenance.json",
    ]
    source = source.replace(
        'output_root = "data/interim/final_model_staging"',
        f'output_root = "{(tmp_path / "builds").as_posix()}"',
    ).replace(
        'model_lock_staging = "manifests/model_lock/MODEL_LOCK_STAGING.json"',
        f'model_lock_staging = "{(tmp_path / "MODEL_LOCK_STAGING.json").as_posix()}"',
    )
    source = source.replace(
        '  "reports/tables/feature_ablation/feature_ablation_analysis_provenance.json",',
        f'  "{robustness[0].as_posix()}",',
    ).replace(
        '  "reports/tables/stqa2_sensitivity/stqa2_sensitivity_provenance.json",',
        f'  "{robustness[1].as_posix()}",',
    ).replace(
        '  "reports/tables/robustness_reconciliation/robustness_reconciliation_provenance.json",',
        f'  "{robustness[2].as_posix()}",',
    )
    path = tmp_path / "final_model.toml"
    path.write_text(source, encoding="utf-8")
    if robustness_exists:
        shared_compile = "e" * 64
        shared_oof = "f" * 64
        algorithms = {
            "initial_results": "initial-model-result-analysis-v1",
            "endpoint": "model-endpoint-sensor-diagnostics-v1",
            "qa": "model-qa-diagnostics-v1",
            "residual_spatial": "residual-spatial-diagnostics-v1",
            "diagnostic_figures": "model-diagnostic-figures-v1",
            "feature_ablation": "feature-ablation-analysis-v1",
            "stqa2_sensitivity": "stqa2-pixel-label-sensitivity-v2",
        }
        source_paths = {
            name: tmp_path / f"{name}_source_provenance.json" for name in algorithms
        }
        source_paths["feature_ablation"] = robustness[0]
        source_paths["stqa2_sensitivity"] = robustness[1]
        source_records = {}
        for name, algorithm in algorithms.items():
            if name in {"initial_results", "endpoint"}:
                lineage = {
                    "compile_provenance_commit_sha256": shared_compile,
                    "input_authentication": {"oof_predictions_sha256": shared_oof},
                }
            elif name in {"qa", "residual_spatial", "diagnostic_figures"}:
                lineage = {
                    "input_authentication": {
                        "compile_provenance_commit_sha256": shared_compile,
                        "oof_predictions_sha256": shared_oof,
                    }
                }
            elif name == "feature_ablation":
                lineage = {
                    "input_authentication": {
                        "canonical_model_compile_commit_sha256": shared_compile,
                        "canonical_all_feature_oof_sha256": shared_oof,
                    }
                }
            else:
                lineage = {
                    "input_authentication": {
                        "model_compile_provenance_commit_sha256": shared_compile,
                        "model_oof_predictions_sha256": shared_oof,
                    }
                }
            payload = {
                "schema_version": 1,
                "algorithm_version": algorithm,
                "state": "complete",
                "final_test_year": 2025,
                "final_test_locked": True,
                "contains_final_test_year": False,
                **lineage,
            }
            payload["commit_sha256"] = canonical_sha256(payload)
            item = source_paths[name]
            item.write_text(json.dumps(payload), encoding="utf-8")
            source_records[name] = {
                "provenance_path": item.resolve().as_posix(),
                "provenance_file_sha256": sha256_file(item),
                "provenance_commit_sha256": payload["commit_sha256"],
                "authenticated_output_count": 1,
            }
        reconciliation = {
            "schema_version": 1,
            "algorithm_version": "development-robustness-reconciliation-v1",
            "analysis_scope": "locked_2020_2024_development_robustness_reconciliation",
            "state": "complete",
            "ready_for_development_robustness_interpretation": True,
            "final_test_year": 2025,
            "final_test_locked": True,
            "contains_final_test_year": False,
            "input_authentication": {
                "shared_model_compile_commit_sha256": shared_compile,
                "shared_oof_predictions_sha256": shared_oof,
                "sources": source_records,
            },
        }
        reconciliation["commit_sha256"] = canonical_sha256(reconciliation)
        robustness[2].write_text(json.dumps(reconciliation), encoding="utf-8")
    return path


def _bundle(model_id: str) -> dict[str, object]:
    candidate = (
        "B1-ridge-a100" if model_id == "B1" else "M2-hgb-leaf15-min50-l2-1"
    )
    parameters = (
        {"ridge_alpha": 100.0}
        if model_id == "B1"
        else {
            "hgb_learning_rate": 0.05,
            "hgb_max_iter": 300,
            "hgb_max_leaf_nodes": 15,
            "hgb_min_samples_leaf": 50,
            "hgb_l2_regularization": 1.0,
        }
    )
    return {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "model_id": model_id,
        "candidate_id": candidate,
        "candidate_parameters": parameters,
        "random_state": 20260719,
        "feature_names": ["x"],
        "training_row_count": 63_403,
        "training_date_count": 65,
        "training_spatial_block_count": 71,
        "training_keys_sha256": "a" * 64,
        "pipeline": SyntheticPipeline(["x"]),
    }


def _build_provenance(tmp_path: Path, config_path: Path) -> Path:
    config = load_final_model_config(config_path)
    run = tmp_path / "run"
    run.mkdir()
    output_files = {}
    for name in ("full_development_tuning_date_scores.parquet", "full_development_selections.json"):
        path = run / name
        path.write_bytes(name.encode("utf-8"))
        output_files[name] = {
            "path": name,
            "path_base": "run_directory",
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    models = {}
    for model_id in ("B1", "M2"):
        bundle = _bundle(model_id)
        record = atomic_dump_model_bundle(bundle, run / f"{model_id}.joblib")
        models[model_id] = {
            **record,
            "selected_candidate_id": bundle["candidate_id"],
            "selected_parameters": bundle["candidate_parameters"],
            "random_state": 20260719,
            "feature_names": ["x"],
            "feature_count": 1,
            "training_row_count": 63_403,
            "training_date_count": 65,
            "training_spatial_block_count": 71,
            "training_keys_sha256": "a" * 64,
        }
    lock_names = {
        "model_dataset_provenance",
        "model_table",
        "feature_registry",
        "split_promotion",
        "row_groups",
        "fold_definitions",
        "spatial_buffer_geoids",
        "model_selection_freeze",
        "model_selection_config",
    }
    input_locks = {
        name: {"path": name, "sha256": "b" * 64, "bytes": 1}
        for name in lock_names
    }
    input_locks["context_commits"] = {
        "model_dataset_commit_sha256": "1" * 64,
        "split_promotion_commit_sha256": "2" * 64,
        "model_selection_commit_sha256": "3" * 64,
    }
    input_locks["feature_registry_semantic_sha256"] = "c" * 64
    payload = {
        "schema_version": FINAL_MODEL_SCHEMA_VERSION,
        "algorithm_version": FINAL_MODEL_ALGORITHM_VERSION,
        "state": "complete_development_only",
        "ready_for_model_lock_staging": True,
        "ready_for_formal_model_lock": False,
        "run_id": "synthetic-final-build",
        "development_years": list(range(2020, 2025)),
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "final_test_values_read": False,
        "model_ids": ["B1", "M2"],
        "model_dataset_commit_sha256": "1" * 64,
        "split_promotion_commit_sha256": "2" * 64,
        "model_selection_commit_sha256": "3" * 64,
        "selection_config_sha256": "4" * 64,
        "analysis_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_locks": input_locks,
        "models": models,
        "selections": {
            model_id: {
                "selected_candidate_id": record["selected_candidate_id"],
            }
            for model_id, record in models.items()
        },
        "hotspot_contract": config.hotspot_contract,
        "planned_figures": list(config.planned_figures),
        "output_files": output_files,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    path = run / "final_model_build_provenance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_stage_records_complete_lock_surface_but_never_writes_formal_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, robustness_exists=True)
    build = _build_provenance(tmp_path, config_path)
    monkeypatch.setattr(
        staging_module,
        "_git_state",
        lambda root: {
            "head_present": True,
            "training_code_git_commit": "d" * 40,
            "working_tree_clean": True,
            "status_entry_count": 0,
        },
    )

    result = stage_model_lock(build, config_path=config_path)

    assert result["state"] == "eligible_for_later_formal_promotion"
    assert result["ready_for_formal_model_lock"] is True
    assert result["formal_model_lock_written"] is False
    assert result["blockers"] == []
    assert set(result["models"]) == {"B1", "M2"}
    assert result["primary_metric"] == "equal_date_weighted_mae_c"
    assert result["final_test_locked"] is True
    assert result["final_test_used"] is False
    assert not (tmp_path / "MODEL_LOCK.json").exists()


def test_missing_git_and_robustness_are_explicit_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, robustness_exists=False)
    build = _build_provenance(tmp_path, config_path)
    monkeypatch.setattr(
        staging_module,
        "_git_state",
        lambda root: {
            "head_present": False,
            "training_code_git_commit": None,
            "working_tree_clean": False,
            "status_entry_count": None,
        },
    )

    result = stage_model_lock(build, config_path=config_path)

    assert result["state"] == "blocked"
    assert result["ready_for_formal_model_lock"] is False
    assert "git_head_missing" in result["blockers"]
    assert sum(value.startswith("robustness:") for value in result["blockers"]) == 3


def test_formal_model_lock_filename_is_always_forbidden(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, robustness_exists=False)
    build = _build_provenance(tmp_path, config_path)

    with pytest.raises(PermissionError, match="forbidden"):
        stage_model_lock(
            build,
            config_path=config_path,
            output_path=tmp_path / "MODEL_LOCK.json",
        )

    assert not (tmp_path / "MODEL_LOCK.json").exists()


def test_failed_restage_clears_old_marker(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, robustness_exists=False)
    output = tmp_path / "MODEL_LOCK_STAGING.json"
    output.write_text("old marker", encoding="utf-8")
    invalid_build = tmp_path / "invalid.json"
    invalid_build.write_text("{}", encoding="utf-8")

    with pytest.raises(ModelLockStagingError):
        stage_model_lock(
            invalid_build,
            config_path=config_path,
            output_path=output,
        )

    assert not output.exists()
