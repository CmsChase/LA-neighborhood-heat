from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import la_heat.formal_model_lock as lock_module
from la_heat.formal_model_lock import FormalModelLockError, promote_formal_model_lock
from la_heat.provenance import canonical_sha256, sha256_file


def _committed(payload: dict[str, object], path: Path) -> dict[str, object]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config_path = tmp_path / "configs/final_model.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("frozen = true\n", encoding="utf-8")
    config_record = {
        "path": config_path.as_posix(),
        "file_sha256": sha256_file(config_path),
        "semantic_sha256": "1" * 64,
    }
    robustness = []
    for index in range(3):
        path = tmp_path / f"reports/robustness-{index}.json"
        payload = _committed({"state": "complete"}, path)
        robustness.append(
            {
                "path": path.as_posix(),
                "authenticated": True,
                "sha256": sha256_file(path),
                "commit_sha256": payload["commit_sha256"],
            }
        )
    models = {}
    build_models = {}
    for model_id in ("B1", "M2"):
        staged = {
            "artifact_path": f"{model_id}.joblib",
            "fitted_pipeline_sha256": model_id.lower() * 32,
            "fitted_pipeline_bytes": 100,
            "selected_candidate_id": f"{model_id}-candidate",
            "selected_parameters": {"x": 1},
            "random_state": 7,
            "feature_names": ["x"],
            "feature_count": 1,
            "training_row_count": 10,
            "training_date_count": 5,
            "training_spatial_block_count": 2,
            "training_keys_sha256": "2" * 64,
        }
        models[model_id] = staged
        build_models[model_id] = {
            "sha256": staged["fitted_pipeline_sha256"],
            "bytes": staged["fitted_pipeline_bytes"],
            **{
                key: value
                for key, value in staged.items()
                if key not in {"artifact_path", "fitted_pipeline_sha256", "fitted_pipeline_bytes"}
            },
        }
    build_path = tmp_path / "run/final_model_build_provenance.json"
    build_path.parent.mkdir(parents=True)
    build_path.write_text("{}", encoding="utf-8")
    build = {
        "commit_sha256": "3" * 64,
        "run_id": "run",
        "final_test_values_read": False,
        "analysis_config": config_record,
        "input_locks": {"feature_registry_semantic_sha256": "4" * 64},
        "models": build_models,
    }
    staging_path = tmp_path / "manifests/model_lock/MODEL_LOCK_STAGING.json"
    staging = {
        "state": "eligible_for_later_formal_promotion",
        "ready_for_formal_model_lock": True,
        "blockers": [],
        "formal_model_lock_written": False,
        "git": {
            "training_code_git_commit": "a" * 40,
            "working_tree_clean": True,
            "status_entry_count": 0,
        },
        "development_build": {
            "path": build_path.as_posix(),
            "sha256": sha256_file(build_path),
            "commit_sha256": build["commit_sha256"],
            "run_id": build["run_id"],
            "final_test_values_read": False,
        },
        "configuration": config_record,
        "input_locks": build["input_locks"],
        "model_dataset_commit_sha256": "5" * 64,
        "split_promotion_commit_sha256": "6" * 64,
        "model_selection_commit_sha256": "7" * 64,
        "selection_config_sha256": "8" * 64,
        "models": models,
        "primary_metric": "equal_date_weighted_mae_c",
        "hotspot_rule": {"exact_top_k": True},
        "planned_figures": ["performance"],
        "robustness_provenance": robustness,
        "final_test_year": 2025,
        "final_test_locked": True,
        "final_test_unlocked": False,
        "final_test_used": False,
        "contains_final_test_year": False,
    }
    _committed(staging, staging_path)
    monkeypatch.setattr(lock_module, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        lock_module,
        "_git_state",
        lambda _root: {
            "head": "b" * 40,
            "working_tree_clean": True,
            "status_entry_count": 0,
        },
    )
    monkeypatch.setattr(lock_module, "_git_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(
        lock_module,
        "_git_changed_paths",
        lambda *_args: ("manifests/model_lock/MODEL_LOCK_STAGING.json",),
    )
    monkeypatch.setattr(
        lock_module,
        "authenticate_final_build_provenance",
        lambda _path, load_models: build,
    )
    monkeypatch.setattr(
        lock_module,
        "load_final_model_config",
        lambda _path: SimpleNamespace(
            path=config_path,
            semantic_sha256=config_record["semantic_sha256"],
        ),
    )
    return staging_path, tmp_path / "manifests/model_lock/MODEL_LOCK.json"


def test_explicit_approval_is_required(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="approve-formal-lock"):
        promote_formal_model_lock(tmp_path / "MODEL_LOCK_STAGING.json")


def test_formal_lock_is_one_way_and_keeps_final_test_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging, output = _inputs(tmp_path, monkeypatch)

    result = promote_formal_model_lock(
        staging,
        output_path=output,
        approve_formal_lock=True,
    )

    assert result["formal_model_lock_written"] is True
    assert result["final_test_locked"] is True
    assert result["one_time_final_evaluation_authorized"] is False
    assert set(result["models"]) == {"B1", "M2"}
    assert output.is_file()
    with pytest.raises(FileExistsError, match="never be overwritten"):
        promote_formal_model_lock(
            staging,
            output_path=output,
            approve_formal_lock=True,
        )


def test_ineligible_staging_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging, output = _inputs(tmp_path, monkeypatch)
    payload = json.loads(staging.read_text(encoding="utf-8"))
    payload["blockers"] = ["x"]
    payload.pop("commit_sha256")
    _committed(payload, staging)

    with pytest.raises(FormalModelLockError, match="not an eligible"):
        promote_formal_model_lock(
            staging,
            output_path=output,
            approve_formal_lock=True,
        )
