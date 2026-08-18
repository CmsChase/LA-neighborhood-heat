from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import rasterio
import requests

from la_heat.multicity import m3_source_predictor_daymet_order_repair_v1 as repair
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    DAYMET_VARIABLES as PARENT_DAYMET_VARIABLES,
)
from la_heat.provenance import canonical_sha256
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES


def _initial_snapshot() -> dict[str, Any]:
    metadata = []
    for index, (city_id, year) in enumerate(
        (city_id, year)
        for city_id in ("houston_tx", "chicago_il")
        for year in range(2020, 2025)
    ):
        attempt = 5 if index < 8 else 4
        metadata.append(
            {
                "task_id": f"daymet-metadata-{city_id}-{year}",
                "plan_index": index + 3,
                "status": "pending",
                "attempt": attempt,
                "claim_generation": attempt,
                "error_type": "SourceFootprintError",
            }
        )
    return {
        "run_id": repair.RUN_ID,
        "schema_version": 1,
        "task_plan_sha256": repair.SQLITE_TASK_PLAN_SHA256,
        "parent_canonical_task_plan_sha256": (
            repair.PARENT_CANONICAL_TASK_PLAN_SHA256
        ),
        "derived_sqlite_task_plan_sha256": repair.SQLITE_TASK_PLAN_SHA256,
        "desired_state": "paused",
        "counts": dict(repair.EXPECTED_INITIAL_COUNTS),
        "active_lease_count": 0,
        "completed_tasks": [
            {
                "task_id": task_id,
                "kind": "synthetic",
                "plan_index": index,
                "attempt": 1,
                "claim_generation": 1,
                "result": {"state": "complete"},
            }
            for index, task_id in enumerate(repair.EXPECTED_INITIAL_COMPLETE_IDS)
        ],
        "daymet_metadata_tasks": metadata,
        "running_tasks": [],
        "task_plan": [],
        "task_states": [],
    }


def _safe_progress_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    counts_by_kind = {
        "freeze_key_universe": 1,
        "authenticate_static_reuse": 2,
        "acquire_daymet_metadata": 10,
        "acquire_daymet_subset": 60,
        "build_sentinel_inventory": 2,
        "acquire_sentinel_cache": 2,
        "finalize_acquisition": 1,
        "build_extension_city": 2,
        "compile_source_city": 4,
        "finalize_predictors": 1,
    }
    task_states: list[dict[str, Any]] = []
    completed_tasks: list[dict[str, Any]] = []
    plan_index = 0
    for kind in (*repair.ONLINE_KINDS, *repair.OFFLINE_KINDS):
        for kind_index in range(counts_by_kind[kind]):
            is_initial_complete = kind in {
                "freeze_key_universe",
                "authenticate_static_reuse",
            }
            task_id = f"{kind}-{kind_index}"
            if kind == "freeze_key_universe":
                task_id = "freeze-key-universe"
            elif kind == "authenticate_static_reuse":
                task_id = ("static-houston_tx", "static-chicago_il")[kind_index]
            attempt = 1 if is_initial_complete else 0
            if kind == "acquire_daymet_metadata":
                attempt = 5 if kind_index < 8 else 4
            result = {"state": "complete"} if is_initial_complete else None
            error_type = (
                "SourceFootprintError"
                if kind == "acquire_daymet_metadata"
                else None
            )
            row = {
                "plan_index": plan_index,
                "task_id": task_id,
                "kind": kind,
                "status": "complete" if is_initial_complete else "pending",
                "attempt": attempt,
                "claim_generation": attempt,
                "error_type": error_type,
                "result": result,
            }
            task_states.append(row)
            if is_initial_complete:
                completed_tasks.append(
                    {
                        "task_id": task_id,
                        "kind": kind,
                        "plan_index": plan_index,
                        "attempt": attempt,
                        "claim_generation": attempt,
                        "result": result,
                    }
                )
            plan_index += 1
    assert len(task_states) == 85
    snapshot = {
        "run_id": repair.RUN_ID,
        "schema_version": 1,
        "task_plan_sha256": repair.SQLITE_TASK_PLAN_SHA256,
        "parent_canonical_task_plan_sha256": (
            repair.PARENT_CANONICAL_TASK_PLAN_SHA256
        ),
        "derived_sqlite_task_plan_sha256": repair.SQLITE_TASK_PLAN_SHA256,
        "task_plan": [
            {
                "plan_index": row["plan_index"],
                "task_id": row["task_id"],
                "kind": row["kind"],
                "payload": {},
            }
            for row in task_states
        ],
        "task_states": task_states,
        "desired_state": "paused",
        "counts": {
            "pending": 82,
            "running": 0,
            "complete": 3,
            "quarantined": 0,
            "total": 85,
        },
        "active_lease_count": 0,
        "completed_tasks": completed_tasks,
        "running_tasks": [],
    }
    authorization = {
        "incident": {"queue_snapshot": json.loads(json.dumps(snapshot))}
    }
    return snapshot, authorization


def _explode(label: str):
    def blocked(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail(f"authorization preview attempted {label}")

    return blocked


def test_variable_order_repair_is_exact_and_set_preserving() -> None:
    assert set(PARENT_DAYMET_VARIABLES) == set(DEFAULT_DAYMET_VARIABLES)
    assert tuple(PARENT_DAYMET_VARIABLES) != tuple(DEFAULT_DAYMET_VARIABLES)
    assert repair._repair_contract() == {
        "fetch_argument_order": list(DEFAULT_DAYMET_VARIABLES),
        "persisted_granule_order": list(PARENT_DAYMET_VARIABLES),
        "override_only_acquire_daymet_metadata": True,
        "same_database_run_id_and_task_plan": True,
        "initialize_rebuild_reset_rewrite_or_unquarantine_allowed": False,
        "maximum_active_tasks": 1,
        "download_workers": 1,
        "compute_workers": 1,
    }


def test_metadata_only_preview_uses_no_network_parquet_or_raster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _initial_snapshot()
    authorization = tmp_path / "parent.json"
    database = tmp_path / "tasks.sqlite"
    status = tmp_path / "status.json"
    authorization.write_text("{}", encoding="utf-8")
    database.write_bytes(b"queue evidence")
    status.write_text(
        json.dumps(
            {"task_plan_sha256": repair.PARENT_CANONICAL_TASK_PLAN_SHA256}
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        root=tmp_path,
        authorization=authorization,
        database=database,
        status=status,
        acquisition_root=tmp_path / "acquisition",
        config_path=tmp_path / "config.toml",
    )
    parent = {"commit_sha256": repair.PARENT_AUTHORIZATION_COMMIT_SHA256}
    monkeypatch.setattr(
        repair,
        "load_predictor_extension_settings",
        lambda *args, **kwargs: settings,
    )
    monkeypatch.setattr(
        repair,
        "authenticate_m3_source_predictor_extension_authorization",
        lambda *args, **kwargs: parent,
    )
    monkeypatch.setattr(repair, "_queue_snapshot", lambda *args: snapshot)
    monkeypatch.setattr(repair, "_code_records", lambda root: [])
    monkeypatch.setattr(
        repair,
        "INCIDENT_QUEUE_RECORD",
        repair._file_record(tmp_path, database),
    )
    monkeypatch.setattr(
        repair,
        "INCIDENT_STATUS_RECORD",
        repair._file_record(tmp_path, status),
    )
    monkeypatch.setattr(
        repair,
        "INITIAL_QUEUE_SNAPSHOT_SHA256",
        canonical_sha256(snapshot),
    )
    monkeypatch.setattr(requests.sessions.Session, "request", _explode("network"))
    monkeypatch.setattr(pd, "read_parquet", _explode("Parquet read"))
    monkeypatch.setattr(rasterio, "open", _explode("raster read"))

    payload = repair.build_m3_source_predictor_daymet_order_repair_authorization(
        tmp_path
    )

    assert payload["authorization_access_audit"] == {
        "network_or_href_reads": 0,
        "predictor_qa_or_target_values_read": False,
        "blind_test_city_accessed": False,
        "queue_or_cache_modified": False,
        "model_fit_select_predict_or_score_performed": False,
    }
    assert payload["incident"]["queue_snapshot"]["counts"] == {
        "pending": 82,
        "running": 0,
        "complete": 3,
        "quarantined": 0,
        "total": 85,
    }
    assert payload["incident"]["acquisition_file_count"] == 0


def test_initial_queue_snapshot_tamper_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _initial_snapshot()
    monkeypatch.setattr(
        repair,
        "INITIAL_QUEUE_SNAPSHOT_SHA256",
        canonical_sha256(snapshot),
    )
    repair._validate_initial_snapshot(snapshot)

    tampered = json.loads(json.dumps(snapshot))
    tampered["counts"]["pending"] = 81
    with pytest.raises(
        repair.M3SourcePredictorDaymetOrderRepairError,
        match="incident snapshot changed",
    ):
        repair._validate_initial_snapshot(tampered)


def test_runner_has_no_initialization_rebuild_or_parent_worker_call() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "scripts/run_m3_source_predictor_daymet_order_repair_v1.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_or_called = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    forbidden = {
        "initialize_source_predictor_runtime",
        "execute_source_predictor_worker",
        "prepare",
        "initialize",
        "rebuild",
        "reset",
        "unquarantine",
    }
    assert imported_or_called.isdisjoint(forbidden)
    assert "--initialize" not in source
    assert "--authorization" not in source
    assert "execute_daymet_order_repair_worker" in imported_or_called


def test_runner_forces_native_thread_limits_before_la_heat_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "scripts/run_m3_source_predictor_daymet_order_repair_v1.py"
    ).read_text(encoding="utf-8")
    assignment = 'os.environ[_thread_env_name] = "1"'
    assert assignment in source
    assert source.index(assignment) < source.index("from la_heat")
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "GDAL_NUM_THREADS",
    ):
        assert f'"{name}"' in source


def test_authorization_write_locks_before_snapshot_or_write() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "src/la_heat/multicity/m3_source_predictor_daymet_order_repair_v1.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "create_m3_source_predictor_daymet_order_repair_authorization"
    )
    with_node = next(node for node in function.body if isinstance(node, ast.With))
    context = ast.unparse(with_node.items[0].context_expr)
    body = "\n".join(ast.unparse(node) for node in with_node.body)
    assert context.startswith("exclusive_predictor_worker(")
    assert "build_m3_source_predictor_daymet_order_repair_authorization" in body
    assert "_write_exclusive" in body
    assert function.body.index(with_node) == len(function.body) - 1


def test_initial_metadata_attempt_distribution_is_frozen() -> None:
    metadata = _initial_snapshot()["daymet_metadata_tasks"]
    assert Counter(row["attempt"] for row in metadata) == Counter({5: 8, 4: 2})
    assert all(row["error_type"] == "SourceFootprintError" for row in metadata)


def test_safe_progress_rejects_schema_version_drift() -> None:
    snapshot, authorization = _safe_progress_fixture()
    repair._validate_safe_progress(snapshot, authorization, require_paused=True)
    snapshot["schema_version"] = 2
    with pytest.raises(
        repair.M3SourcePredictorDaymetOrderRepairError,
        match="progress is unsafe",
    ):
        repair._validate_safe_progress(snapshot, authorization, require_paused=True)


@pytest.mark.parametrize(
    ("attempt", "claim_generation"),
    (
        (4, 4),
        (5, 4),
        (4, 5),
    ),
)
def test_safe_progress_rejects_attempt_or_generation_regression_and_mismatch(
    attempt: int,
    claim_generation: int,
) -> None:
    snapshot, authorization = _safe_progress_fixture()
    row = next(
        item
        for item in snapshot["task_states"]
        if item["kind"] == "acquire_daymet_metadata" and item["attempt"] == 5
    )
    row["attempt"] = attempt
    row["claim_generation"] = claim_generation
    with pytest.raises(
        repair.M3SourcePredictorDaymetOrderRepairError,
        match="attempts or fencing generations regressed",
    ):
        repair._validate_safe_progress(snapshot, authorization, require_paused=True)


def test_safe_progress_rejects_active_kind_completion_with_stale_error() -> None:
    snapshot, authorization = _safe_progress_fixture()
    row = next(
        item
        for item in snapshot["task_states"]
        if item["kind"] == "acquire_daymet_metadata"
    )
    row["status"] = "complete"
    row["result"] = {"state": "daymet_granules_complete"}
    row["error_type"] = "SourceFootprintError"
    snapshot["counts"]["pending"] -= 1
    snapshot["counts"]["complete"] += 1
    with pytest.raises(
        repair.M3SourcePredictorDaymetOrderRepairError,
        match="active repair task kind has invalid state",
    ):
        repair._validate_safe_progress(snapshot, authorization, require_paused=True)


def test_daymet_marker_rejects_wrong_but_well_formed_query_sha256() -> None:
    repair_commit = "c" * 64
    year = 2022
    marker = {
        "schema_version": 1,
        "algorithm_version": "m3-source-predictor-extension-v1",
        "state": "daymet_granules_complete",
        "authorization_commit_sha256": repair.PARENT_AUTHORIZATION_COMMIT_SHA256,
        "daymet_order_repair_authorization_commit_sha256": repair_commit,
        "cmr_fetch_variable_order": list(DEFAULT_DAYMET_VARIABLES),
        "city_id": "houston_tx",
        "year": year,
        "granules": [
            {
                "concept_id": f"concept-{variable}",
                "title": f"title-{variable}",
                "variable": variable,
                "year": year,
                "size_mb": 1.0,
                "updated_at": None,
            }
            for variable in PARENT_DAYMET_VARIABLES
        ],
        "query_sha256": "a" * 64,
        "official_cmr_http_status": 200,
        "urls_or_credentials_persisted": False,
        "target_or_landsat_values_read": False,
    }
    with pytest.raises(
        repair.M3SourcePredictorDaymetOrderRepairError,
        match="marker drifted",
    ):
        repair._validate_daymet_repair_marker(
            marker,
            city_id="houston_tx",
            year=year,
            repair_commit_sha256=repair_commit,
            expected_query_sha256="b" * 64,
        )
