from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

import la_heat.multicity.model_fit_authorization as authorization_module
from la_heat.multicity.model_fit_authorization import (
    ModelFitAuthorizationError,
    authenticate_model_fit_authorization,
    build_model_fit_authorization,
    create_model_fit_authorization,
)
from la_heat.provenance import canonical_sha256, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_committed(path: Path, payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _source_completion(root: Path, *, total: int = 91) -> Path:
    city_directory = root / "city"
    city_directory.mkdir(parents=True, exist_ok=True)
    target = city_directory / "targets.parquet"
    target.write_bytes(b"source-target-table")
    target_record = {
        "rows": 98_640,
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }
    city_path = city_directory / "CITY_TARGETS_COMPLETE.json"
    city_commit = _write_committed(
        city_path,
        {
            "state": "complete",
            "output_files": {"targets.parquet": target_record},
        },
    )
    marker_path = root / "VALUES_OPENED.json"
    marker = _write_committed(marker_path, {"state": "target_values_opened"})
    authorization_path = (
        PROJECT_ROOT
        / "manifests/multicity/targets/SOURCE_TARGET_AUTHORIZATION.json"
    )
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    path = root / "LA_SOURCE_TARGETS_COMPLETE.json"
    _write_committed(
        path,
        {
            "state": "la_source_targets_complete",
            "lane": "los_angeles_2020_2024_source",
            "city_ids": ["los_angeles_ca"],
            "plan_commit_sha256": authorization["plan_commit_sha256"],
            "source_work_units": {
                "overpass": 90,
                "compile": 1,
                "total": total,
                "result_commits_sha256": "a" * 64,
            },
            "city_target_commit": {
                "path": city_path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": city_path.stat().st_size,
                "sha256": sha256_file(city_path),
                "commit_sha256": city_commit["commit_sha256"],
                "output_files": {"targets.parquet": target_record},
            },
            "values_opened_marker": {
                "path": marker_path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": marker_path.stat().st_size,
                "sha256": sha256_file(marker_path),
                "commit_sha256": marker["commit_sha256"],
            },
            "authorization": {
                "path": authorization_path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": authorization_path.stat().st_size,
                "sha256": sha256_file(authorization_path),
                "commit_sha256": authorization["commit_sha256"],
            },
            "external_cohort": {
                "task_count": 68,
                "tasks_claimed": False,
                "target_values_read": False,
            },
        },
    )
    return path


@pytest.fixture
def local_workspace() -> Path:
    root = PROJECT_ROOT / ".tmp" / "model-fit-authorization-tests" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_incomplete_source_cannot_authorize_fit(local_workspace: Path) -> None:
    source = _source_completion(local_workspace, total=90)

    with pytest.raises(ModelFitAuthorizationError, match="91/91"):
        build_model_fit_authorization(PROJECT_ROOT, source_completion_path=source)


def test_build_opens_only_fit_and_predictor_only_prediction(
    local_workspace: Path,
) -> None:
    source = _source_completion(local_workspace)
    payload = build_model_fit_authorization(PROJECT_ROOT, source_completion_path=source)

    assert payload["state"] == "model_fit_authorized"
    assert payload["cohorts"]["training_rows"] == 73_432
    assert payload["cohorts"]["calibration_rows"] == 25_208
    assert payload["cohorts"]["external_rows"] == 38_301
    assert payload["cohorts"]["external_city_dates"] == 64
    assert payload["permissions"] == {
        "read_la_source_targets": True,
        "fit_frozen_models": True,
        "calibrate_la_2024_cqr": True,
        "create_external_predictor_only_predictions": True,
        "read_external_targets": False,
        "score_external_targets": False,
        "retune_or_select_models": False,
        "external_target_claim_authorized": False,
    }
    assert all(value is False for value in payload["access_audit"].values())
    assert payload["output_contract"] == {
        "model_root": "data/processed/multicity/models/frozen_transfer",
        "model_artifact_path": (
            "data/processed/multicity/models/frozen_transfer/"
            "fitted_transfer_models.joblib"
        ),
        "fit_audit_path": (
            "data/processed/multicity/models/frozen_transfer/fit_audit.json"
        ),
        "external_predictions_path": (
            "data/processed/multicity/models/frozen_transfer/"
            "external_predictions_2025.parquet"
        ),
        "external_prediction_commit_manifest": (
            "manifests/multicity/evaluation/"
            "EXTERNAL_PREDICTIONS_COMMITTED.json"
        ),
        "completion_manifest": "manifests/multicity/model/MODEL_FIT_COMPLETE.json",
    }
    assert {
        "src/la_heat/multicity/model_fit_prediction.py",
        "scripts/run_multicity_model_fit_prediction.py",
    }.issubset(payload["code_identity"]["files"])


def test_create_is_append_only_and_check_only_reauthenticates(
    local_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_completion(local_workspace)
    destination = local_workspace / "MODEL_FIT_AUTHORIZATION.json"
    real_build = authorization_module.build_model_fit_authorization

    def build(root: str | Path, **kwargs: object) -> dict[str, object]:
        kwargs["source_completion_path"] = source
        return real_build(root, **kwargs)

    monkeypatch.setattr(authorization_module, "build_model_fit_authorization", build)
    created = create_model_fit_authorization(PROJECT_ROOT, destination)
    checked = authenticate_model_fit_authorization(PROJECT_ROOT, destination)

    assert checked == created
    with pytest.raises(ModelFitAuthorizationError, match="already exists"):
        create_model_fit_authorization(PROJECT_ROOT, destination)


def test_authentication_rejects_tampering(
    local_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_completion(local_workspace)
    destination = local_workspace / "MODEL_FIT_AUTHORIZATION.json"
    real_build = authorization_module.build_model_fit_authorization

    def build(root: str | Path, **kwargs: object) -> dict[str, object]:
        kwargs["source_completion_path"] = source
        return real_build(root, **kwargs)

    monkeypatch.setattr(authorization_module, "build_model_fit_authorization", build)
    create_model_fit_authorization(PROJECT_ROOT, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["permissions"]["read_external_targets"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelFitAuthorizationError, match="commit is invalid"):
        authenticate_model_fit_authorization(PROJECT_ROOT, destination)
