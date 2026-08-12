from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import la_heat.multicity.external_target_authorization as external_auth
from la_heat.multicity.external_target_authorization import (
    PREDICTION_COLUMNS,
    ExternalTargetAuthorizationError,
    build_external_target_authorization,
    create_external_target_authorization,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.target_transaction import EXTERNAL_LANE
from la_heat.provenance import canonical_sha256, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_committed(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _protocol() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": "locked_before_source_targets_and_real_fit",
        "evaluation_contract": {
            "primary_metric": ("one_minus_external_equal_city_equal_date_mae_m2_divided_by_b1"),
            "bootstrap_iterations": 10_000,
            "bootstrap_method": ("city_stratified_crossed_complete_date_x_5km_spatial_block"),
            "bootstrap_seed": 20260728,
            "secondary_metrics": ["per_city_equal_date_mae", "risk_coverage"],
        },
        "prediction_output_contract": {
            "prediction_columns": list(PREDICTION_COLUMNS),
        },
        "code_identity": {
            "files": {
                "configs/research.toml": {
                    "sha256": sha256_file(PROJECT_ROOT / "configs/research.toml")
                }
            }
        },
    }


def _fixture_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setattr(external_auth, "EXPECTED_EXTERNAL_ROWS", 6)
    monkeypatch.setattr(external_auth, "EXPECTED_EXTERNAL_CITY_DATES", 3)
    protocol = _protocol()
    protocol["commit_sha256"] = canonical_sha256(protocol)
    lock_path = tmp_path / "PROTOCOL.json"
    lock_path.write_text(json.dumps(protocol), encoding="utf-8")
    monkeypatch.setattr(
        external_auth,
        "authenticate_protocol_model_lock",
        lambda *_args, **_kwargs: protocol,
    )

    prediction_path = tmp_path / "external_predictions.parquet"
    rows: list[dict[str, object]] = []
    for city_index, city_id in enumerate(EXTERNAL_CITY_IDS):
        for tract_index in range(2):
            rows.append(
                {
                    "city_id": city_id,
                    "tract_geoid": f"{city_index + 1:02d}{tract_index:09d}",
                    "target_date": "2025-07-01",
                    "b1_prediction_c": 40.0,
                    "m2_prediction_c": 39.0,
                    "m2_lower_c": 37.0,
                    "m2_upper_c": 41.0,
                    "m2_interval_width_c": 4.0,
                    "m2_abstain": False,
                    "m2_accepted": True,
                }
            )
    predictions = pd.DataFrame(rows).loc[:, PREDICTION_COLUMNS]
    predictions.to_parquet(prediction_path, index=False)
    model_path = tmp_path / "fitted_transfer_models.joblib"
    model_path.write_bytes(b"synthetic fitted model")
    calibration_commit_sha256 = "b" * 64
    fit_audit_path = tmp_path / "fit_audit.json"
    fit_audit = _write_committed(
        fit_audit_path,
        {
            "state": "complete_la_fit_and_calibration_external_predictor_only",
            "authorization_commit_sha256": "d" * 64,
            "calibration": {"commit_sha256": calibration_commit_sha256},
            "access_audit": {
                "la_source_target_table_read": True,
                "external_target_or_qa_table_read": False,
                "external_target_asset_href_read": False,
                "external_target_values_read": False,
                "model_selection_or_retuning_performed": False,
            },
        },
    )
    completion_path = tmp_path / "MODEL_FIT_COMPLETE.json"
    fit_authorization_path = tmp_path / "MODEL_FIT_AUTHORIZATION.json"
    fit_authorization = {
        "commit_sha256": "d" * 64,
        "claim_id": "fit-claim",
        "output_contract": {
            "completion_manifest": completion_path.relative_to(PROJECT_ROOT).as_posix()
        },
    }
    monkeypatch.setattr(
        external_auth,
        "authenticate_model_fit_authorization",
        lambda *_args, **_kwargs: fit_authorization,
    )
    prediction_commit_path = tmp_path / "EXTERNAL_PREDICTIONS_COMMITTED.json"
    prediction_commit = _write_committed(
        prediction_commit_path,
        {
            "schema_version": 1,
            "state": "external_predictions_committed_before_target_access",
            "authorization_commit_sha256": fit_authorization["commit_sha256"],
            "model_fit_authorization_path": fit_authorization_path.relative_to(
                PROJECT_ROOT
            ).as_posix(),
            "model_fit_claim_id": fit_authorization["claim_id"],
            "model_fit_completion_path": completion_path.relative_to(PROJECT_ROOT).as_posix(),
            "protocol_lock_commit_sha256": protocol["commit_sha256"],
            "model_fit_commit_sha256": fit_audit["commit_sha256"],
            "calibration_commit_sha256": calibration_commit_sha256,
            "external_city_ids": list(EXTERNAL_CITY_IDS),
            "external_year": 2025,
            "row_count": 6,
            "city_date_count": 3,
            "prediction_columns": list(PREDICTION_COLUMNS),
            "external_target_or_qa_values_read": False,
            "output": {
                "path": prediction_path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": prediction_path.stat().st_size,
                "sha256": sha256_file(prediction_path),
                "semantic_sha256": external_auth._prediction_semantic(predictions),
            },
            "access_audit": {
                "external_target_or_qa_files_read": [],
                "external_target_or_qa_values_read": False,
                "external_target_claim_existed_at_publication": False,
            },
        },
    )
    prediction_record = {
        **_file_record(prediction_path),
        "rows": len(predictions),
        "columns": list(PREDICTION_COLUMNS),
    }
    _write_committed(
        completion_path,
        {
            "state": "model_fit_complete_external_predictions_committed",
            "claim_id": fit_authorization["claim_id"],
            "authorization_commit_sha256": fit_authorization["commit_sha256"],
            "protocol_model_lock_commit_sha256": protocol["commit_sha256"],
            "outputs": {
                "model_artifact": _file_record(model_path),
                "fit_audit": {
                    **_file_record(fit_audit_path),
                    "commit_sha256": fit_audit["commit_sha256"],
                },
                "external_predictions": prediction_record,
            },
            "external_prediction_commit": {
                **_file_record(prediction_commit_path),
                "commit_sha256": prediction_commit["commit_sha256"],
            },
            "external_prediction_commit_sha256": prediction_commit["commit_sha256"],
            "access_audit": {
                "la_source_target_values_read": True,
                "external_target_values_read": False,
                "external_target_or_qa_files_read": [],
                "external_prediction_created_before_external_target_claim": True,
                "model_selection_or_retuning_performed": False,
            },
        },
    )
    plan_path = tmp_path / "TARGET_BUILD_PLAN.json"
    plan = _write_committed(
        plan_path,
        {
            "state": "prepared_target_blind_builder_not_authorized",
            "authorization": {
                "external_target_build_authorized": False,
                "external_claim_created": False,
            },
            "access_contract": {
                "landsat_thermal_or_target_qa_values_read": False,
                "target_tables_read": False,
            },
            "cohort_lanes": {
                EXTERNAL_LANE: {
                    "city_ids": list(EXTERNAL_CITY_IDS),
                    "years": [2025],
                    "single_append_only_claim_required": True,
                    "per_city_claims_forbidden": True,
                    "overpasses": 64,
                    "keys": 6,
                }
            },
        },
    )
    source_path = tmp_path / "LA_SOURCE_TARGETS_COMPLETE.json"
    _write_committed(
        source_path,
        {
            "state": "la_source_targets_complete",
            "plan_commit_sha256": plan["commit_sha256"],
            "external_cohort": {
                "task_count": 68,
                "tasks_claimed": False,
                "target_values_read": False,
            },
        },
    )
    return {
        "lock": lock_path,
        "prediction": prediction_commit_path,
        "prediction_table": prediction_path,
        "fit_audit": fit_audit_path,
        "fit_completion": completion_path,
        "source": source_path,
        "plan": plan_path,
        "marker": tmp_path / "VALUES_OPENED.json",
        "authorization": tmp_path / "EXTERNAL_TARGET_AUTHORIZATION.json",
    }


def _build_options(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "prediction_commit_path": paths["prediction"],
        "source_completion_path": paths["source"],
        "protocol_lock_path": paths["lock"],
        "target_plan_path": paths["plan"],
        "target_config_path": PROJECT_ROOT / "configs/research.toml",
        "values_opened_path": paths["marker"],
    }


def test_missing_prediction_commit_cannot_issue_or_open_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path, monkeypatch)
    paths["prediction"].unlink()

    with pytest.raises(ExternalTargetAuthorizationError, match="unavailable"):
        build_external_target_authorization(PROJECT_ROOT, **_build_options(paths))

    assert not paths["authorization"].exists()
    assert not paths["marker"].exists()


def test_authorization_binds_prediction_and_does_not_read_or_open_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path, monkeypatch)
    original_read = pd.read_parquet
    reads: list[Path] = []

    def tracked_read(path: str | Path, *args: object, **kwargs: object) -> pd.DataFrame:
        reads.append(Path(path).resolve())
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(external_auth.pd, "read_parquet", tracked_read)
    payload = create_external_target_authorization(
        PROJECT_ROOT,
        paths["authorization"],
        **_build_options(paths),
    )

    assert payload["city_ids"] == list(EXTERNAL_CITY_IDS)
    assert payload["single_global_claim"] is True
    assert payload["expected_overpass_count"] == 64
    assert payload["expected_city_compile_count"] == 3
    assert set(reads) == {paths["prediction_table"].resolve()}
    assert not paths["marker"].exists()


def test_prediction_commit_cannot_forge_model_fit_or_calibration_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path, monkeypatch)
    commit = json.loads(paths["prediction"].read_text(encoding="utf-8"))
    commit["calibration_commit_sha256"] = "f" * 64
    commit.pop("commit_sha256")
    commit["commit_sha256"] = canonical_sha256(commit)
    paths["prediction"].write_text(json.dumps(commit), encoding="utf-8")

    with pytest.raises(ExternalTargetAuthorizationError, match="completion chain"):
        build_external_target_authorization(PROJECT_ROOT, **_build_options(paths))

    assert not paths["marker"].exists()


def test_partial_city_claim_is_rejected_before_marker_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path, monkeypatch)
    split_plan = _write_committed(
        tmp_path / "SPLIT_PLAN.json",
        {
            "state": "prepared_target_blind_builder_not_authorized",
            "authorization": {"external_target_build_authorized": False},
            "access_contract": {"target_tables_read": False},
            "cohort_lanes": {
                EXTERNAL_LANE: {
                    "city_ids": list(EXTERNAL_CITY_IDS[:-1]),
                    "years": [2025],
                    "single_append_only_claim_required": True,
                    "per_city_claims_forbidden": True,
                    "overpasses": 64,
                    "keys": 6,
                }
            },
        },
    )
    assert split_plan["commit_sha256"]
    paths["plan"] = tmp_path / "SPLIT_PLAN.json"

    with pytest.raises(ExternalTargetAuthorizationError, match="cohort"):
        build_external_target_authorization(PROJECT_ROOT, **_build_options(paths))

    assert not paths["marker"].exists()
