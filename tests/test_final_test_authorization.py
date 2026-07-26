from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import la_heat.final_test_authorization as authorization_module
from la_heat.final_test_authorization import (
    FinalTestAuthorizationError,
    _atomic_create_json,
    _authenticate_formal_model_lock,
    _committed_file_record,
    _git_state,
    authorize_final_test_2025,
    preflight_final_test_2025,
)
from la_heat.final_test_state_lock import (
    DEFAULT_FINAL_TEST_STATE_LOCK_PATH,
    FinalTestStateLock,
    FinalTestStateLockBusyError,
)
from la_heat.provenance import canonical_sha256, sha256_file


def _committed_json(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _file_lock(path: Path, *, commit: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if commit is not None:
        record["commit_sha256"] = commit
    return record


def _synthetic_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    evaluator_module = tmp_path / "src/la_heat/final_test_evaluator.py"
    evaluator_config = tmp_path / "configs/final_test_2025.toml"
    evaluator_module.parent.mkdir(parents=True)
    evaluator_config.parent.mkdir(parents=True)
    evaluator_module.write_text("def evaluate():\n    return None\n", encoding="utf-8")
    evaluator_config.write_text("final_test_year = 2025\n", encoding="utf-8")

    staging_path = tmp_path / "manifests/model_lock/MODEL_LOCK_STAGING.json"
    staging = _committed_json(
        {
            "state": "eligible_for_later_formal_promotion",
            "final_test_locked": True,
            "final_test_values_read": False,
        },
        staging_path,
    )
    config_path = tmp_path / "configs/final_model.toml"
    config_path.write_text("final_test_locked = true\n", encoding="utf-8")
    configuration = {
        "path": config_path.as_posix(),
        "file_sha256": sha256_file(config_path),
        "semantic_sha256": "1" * 64,
    }

    input_locks: dict[str, Any] = {}
    input_names = sorted(authorization_module._INPUT_FILE_LOCKS)
    for index, name in enumerate(input_names):
        path = tmp_path / f"development_inputs/{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"locked-development-input-{index}".encode())
        input_locks[name] = _file_lock(path)
    input_locks["context_commits"] = {
        "model_dataset_commit_sha256": "2" * 64,
        "split_promotion_commit_sha256": "3" * 64,
        "model_selection_commit_sha256": "4" * 64,
    }
    input_locks["feature_registry_semantic_sha256"] = "5" * 64

    formal_models: dict[str, Any] = {}
    build_models: dict[str, Any] = {}
    for model_id, character in (("B1", "6"), ("M2", "7")):
        formal = {
            "artifact_path": f"{model_id}_full_development.joblib",
            "fitted_pipeline_sha256": character * 64,
            "fitted_pipeline_bytes": 10,
            "selected_candidate_id": f"{model_id}-candidate",
            "selected_parameters": {"depth": 1},
            "random_state": 9,
            "feature_names": ["feature"],
            "feature_count": 1,
            "training_row_count": 10,
            "training_date_count": 5,
            "training_spatial_block_count": 2,
            "training_keys_sha256": "8" * 64,
        }
        formal_models[model_id] = formal
        build_models[model_id] = {
            "path": formal["artifact_path"],
            "sha256": formal["fitted_pipeline_sha256"],
            "bytes": formal["fitted_pipeline_bytes"],
            **{
                key: value
                for key, value in formal.items()
                if key
                not in {
                    "artifact_path",
                    "fitted_pipeline_sha256",
                    "fitted_pipeline_bytes",
                }
            },
        }

    build_path = tmp_path / "data/interim/final_model/final_model_build_provenance.json"
    build = _committed_json(
        {
            "state": "complete_development_only",
            "run_id": "synthetic-run",
            "final_test_values_read": False,
            "analysis_config": configuration,
            "input_locks": input_locks,
            "models": build_models,
        },
        build_path,
    )
    robustness = []
    for index in range(3):
        path = tmp_path / f"reports/robustness-{index}.json"
        payload = _committed_json({"state": "complete"}, path)
        robustness.append(
            {
                **_file_lock(path, commit=payload["commit_sha256"]),
                "authenticated": True,
            }
        )

    lock_path = tmp_path / "manifests/model_lock/MODEL_LOCK.json"
    formal_lock = _committed_json(
        {
            "schema_version": 1,
            "algorithm_version": "formal-model-lock-v1",
            "state": "frozen_for_one_time_2025_evaluation",
            "formal_model_lock_written": True,
            "staging_record": _file_lock(
                staging_path,
                commit=staging["commit_sha256"],
            ),
            "development_build": {
                **_file_lock(build_path, commit=build["commit_sha256"]),
                "run_id": build["run_id"],
                "final_test_values_read": False,
            },
            "configuration": configuration,
            "input_locks": input_locks,
            "models": formal_models,
            "robustness_provenance": robustness,
            "final_test_year": 2025,
            "final_test_locked": True,
            "final_test_unlocked": False,
            "final_test_used": False,
            "final_test_values_read": False,
            "contains_final_test_year": False,
            "one_time_final_evaluation_authorized": False,
        },
        lock_path,
    )

    head = "a" * 40

    def committed_record(
        root: Path,
        path: Path,
        *,
        head: str,
        label: str,
    ) -> dict[str, Any]:
        assert root == tmp_path
        assert head == "a" * 40
        resolved = path.resolve()
        return {
            "path": resolved.relative_to(tmp_path).as_posix(),
            "sha256": sha256_file(resolved),
            "bytes": resolved.stat().st_size,
            "git_blob_oid": "b" * 40,
        }

    monkeypatch.setattr(authorization_module, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        authorization_module,
        "_git_state",
        lambda _root: {
            "head": head,
            "working_tree_clean": True,
            "status_entry_count": 0,
        },
    )
    monkeypatch.setattr(authorization_module, "_committed_file_record", committed_record)
    monkeypatch.setattr(
        authorization_module,
        "authenticate_final_build_provenance",
        lambda path, *, load_models: build,
    )
    return evaluator_module, evaluator_config, lock_path, formal_lock


def test_authorization_is_denied_by_default_without_preflight_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, config, _, _ = _synthetic_lock(tmp_path, monkeypatch)
    output = tmp_path / "manifests/final_test_2025/AUTHORIZATION.json"

    with pytest.raises(PermissionError, match="approve-one-time-2025"):
        authorize_final_test_2025(
            evaluator_module=module,
            evaluator_config=config,
        )

    assert not output.exists()


def test_preflight_reads_no_final_values_and_authorization_is_one_way(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, config, _, formal = _synthetic_lock(tmp_path, monkeypatch)
    output = tmp_path / "manifests/final_test_2025/AUTHORIZATION.json"

    preflight = preflight_final_test_2025(
        evaluator_module=module,
        evaluator_config=config,
    )
    result = authorize_final_test_2025(
        evaluator_module=module,
        evaluator_config=config,
        approve_one_time_2025=True,
    )

    assert preflight["state"] == "eligible_but_not_authorized"
    assert preflight["authorized"] is False
    assert preflight["values_read"] is False
    assert result["authorized"] is True
    assert result["values_read"] is False
    assert result["authorization_consumed"] is False
    assert result["evaluator_code_git_commit"] == "a" * 40
    assert result["working_tree_clean"] is True
    assert result["formal_model_lock"]["commit_sha256"] == formal["commit_sha256"]
    assert result["formal_model_lock"]["file_sha256"] == sha256_file(
        tmp_path / "manifests/model_lock/MODEL_LOCK.json"
    )
    assert canonical_sha256(
        {key: value for key, value in result.items() if key != "commit_sha256"}
    ) == result["commit_sha256"]
    assert json.loads(output.read_text(encoding="utf-8")) == result
    with pytest.raises(FileExistsError, match="never be overwritten"):
        authorize_final_test_2025(
            evaluator_module=module,
            evaluator_config=config,
            approve_one_time_2025=True,
        )


def test_formal_lock_canonical_commit_and_formal_state_are_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, lock_path, _ = _synthetic_lock(tmp_path, monkeypatch)
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["state"] = "draft"
    payload["commit_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "commit_sha256"}
    )
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FinalTestAuthorizationError, match="not the untouched formal"):
        _authenticate_formal_model_lock(tmp_path, lock_path)

    payload["state"] = "frozen_for_one_time_2025_evaluation"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FinalTestAuthorizationError, match="canonical commit"):
        _authenticate_formal_model_lock(tmp_path, lock_path)


def test_locked_development_input_hash_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, lock_path, formal = _synthetic_lock(tmp_path, monkeypatch)
    input_record = formal["input_locks"]["model_table"]
    Path(input_record["path"]).write_bytes(b"changed after formal lock")

    with pytest.raises(FinalTestAuthorizationError, match="input lock model_table SHA-256"):
        _authenticate_formal_model_lock(tmp_path, lock_path)


def test_evaluator_file_must_be_present_in_current_clean_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "evaluator.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    state = _git_state(tmp_path)

    record = _committed_file_record(
        tmp_path,
        tracked,
        head=state["head"],
        label="Evaluator module",
    )

    assert record["path"] == "evaluator.py"
    ignored = tmp_path / "ignored.py"
    ignored.write_text("VALUE = 2\n", encoding="utf-8")
    assert _git_state(tmp_path)["working_tree_clean"] is True
    with pytest.raises(FinalTestAuthorizationError, match="Git command failed"):
        _committed_file_record(
            tmp_path,
            ignored,
            head=state["head"],
            label="Evaluator module",
        )


def test_dirty_working_tree_is_rejected(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    path = tmp_path / "tracked.txt"
    path.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    path.write_text("dirty\n", encoding="utf-8")

    with pytest.raises(FinalTestAuthorizationError, match="completely clean"):
        _git_state(tmp_path)


def test_atomic_publication_never_replaces_an_existing_authorization(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "AUTHORIZATION.json"
    destination.write_bytes(b"original")

    with pytest.raises(FileExistsError, match="never be overwritten"):
        _atomic_create_json({"authorized": True}, destination)

    assert destination.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.partial"))


def test_authorization_rechecks_absence_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, config, _, _ = _synthetic_lock(tmp_path, monkeypatch)
    output = tmp_path / "manifests/final_test_2025/AUTHORIZATION.json"
    call_count = 0

    def racing_git_state(_root: Path) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"racing publisher")
        return {
            "head": "a" * 40,
            "working_tree_clean": True,
            "status_entry_count": 0,
        }

    monkeypatch.setattr(authorization_module, "_git_state", racing_git_state)

    with pytest.raises(FileExistsError, match="never be overwritten"):
        authorize_final_test_2025(
            evaluator_module=module,
            evaluator_config=config,
            approve_one_time_2025=True,
        )

    assert output.read_bytes() == b"racing publisher"


def test_authorization_publisher_respects_shared_state_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, config, _, _ = _synthetic_lock(tmp_path, monkeypatch)
    lock_path = tmp_path / DEFAULT_FINAL_TEST_STATE_LOCK_PATH

    with FinalTestStateLock(lock_path):
        with pytest.raises(FinalTestStateLockBusyError):
            authorize_final_test_2025(
                evaluator_module=module,
                evaluator_config=config,
                approve_one_time_2025=True,
            )

    assert not (
        tmp_path / "manifests/final_test_2025/AUTHORIZATION.json"
    ).exists()


def test_shared_state_lock_excludes_another_process(tmp_path: Path) -> None:
    lock_path = tmp_path / DEFAULT_FINAL_TEST_STATE_LOCK_PATH
    child = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from la_heat.final_test_state_lock import (",
            "    FinalTestStateLock, FinalTestStateLockBusyError,",
            ")",
            "try:",
            "    with FinalTestStateLock(Path(sys.argv[1])):",
            "        raise SystemExit(2)",
            "except FinalTestStateLockBusyError:",
            "    raise SystemExit(0)",
        )
    )

    with FinalTestStateLock(lock_path):
        result = subprocess.run(
            [sys.executable, "-c", child, str(lock_path)],
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 0, result.stderr
