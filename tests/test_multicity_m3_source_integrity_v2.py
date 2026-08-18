import json
import sqlite3
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from la_heat.multicity import m3_source_development_engine as engine_v1
from la_heat.multicity import m3_source_development_engine_v2 as engine_v2
from la_heat.multicity import m3_source_integrity_v2 as integrity_v2
from la_heat.multicity.m3_source_development_runtime import (
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    EXPECTED_TASK_COUNT,
    M3SourceRuntimeV2Error,
    load_runner_settings_v2,
    task_specs_from_integrity_authorization,
)
from la_heat.multicity.m3_source_integrity_v2 import (
    OLD_COMPLETE_COUNT,
    OLD_PENDING_COUNT,
    OLD_RUN_ID,
    OLD_TASK_COUNT,
    OLD_TASK_PLAN_SHA256,
    M3SourceIntegrityV2Error,
    _with_commit,
    authenticate_m3_source_integrity_v2_value_gate,
)


def _frozen_queue(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE model_runs (run_id TEXT, task_plan_sha256 TEXT, "
            "desired_state TEXT, schema_version INTEGER)"
        )
        connection.execute(
            "CREATE TABLE model_run_tasks (run_id TEXT, status TEXT, "
            "lease_owner TEXT, lease_expires_at TEXT)"
        )
        connection.execute(
            "INSERT INTO model_runs VALUES (?, ?, 'paused', 1)",
            (OLD_RUN_ID, OLD_TASK_PLAN_SHA256),
        )
        connection.executemany(
            "INSERT INTO model_run_tasks VALUES (?, ?, NULL, NULL)",
            [(OLD_RUN_ID, "complete")] * OLD_COMPLETE_COUNT
            + [(OLD_RUN_ID, "pending")] * OLD_PENDING_COUNT,
        )
        connection.commit()
    finally:
        connection.close()


def _queue_snapshot() -> dict[str, object]:
    return {
        "run_id": OLD_RUN_ID,
        "schema_version": 1,
        "task_plan_sha256": OLD_TASK_PLAN_SHA256,
        "task_count": OLD_TASK_COUNT,
        "desired_state": "paused",
        "status_counts": {
            "pending": OLD_PENDING_COUNT,
            "running": 0,
            "complete": OLD_COMPLETE_COUNT,
            "quarantined": 0,
        },
        "active_lease_count": 0,
    }


def test_lightweight_value_gate_rejects_authorization_or_old_queue_drift(
    tmp_path: Path,
) -> None:
    database = tmp_path / "old.sqlite"
    authorization_path = tmp_path / "authorization.json"
    _frozen_queue(database)
    authorization = _with_commit(
        {
            "state": "source_integrity_overlay_v2_authorized",
            "original_queue_database": "old.sqlite",
            "original_queue_snapshot": _queue_snapshot(),
        }
    )
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")

    authenticate_m3_source_integrity_v2_value_gate(
        tmp_path, authorization, authorization_path
    )

    changed = _with_commit(
        {
            key: value
            for key, value in authorization.items()
            if key != "commit_sha256"
        }
        | {"unexpected_permission": True}
    )
    authorization_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(M3SourceIntegrityV2Error, match="permit changed"):
        authenticate_m3_source_integrity_v2_value_gate(
            tmp_path, authorization, authorization_path
        )

    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE model_runs SET desired_state = 'running' WHERE run_id = ?",
            (OLD_RUN_ID,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(M3SourceIntegrityV2Error, match="frozen zero-lease"):
        authenticate_m3_source_integrity_v2_value_gate(
            tmp_path, authorization, authorization_path
        )


def test_v2_task_plan_has_only_logical_finalize_and_offline_qa_work() -> None:
    scenes = [
        {"city_id": SOURCE_CITY_IDS[index % 4], "scene_id": f"scene-{index:03d}"}
        for index in range(523)
    ]
    overpasses = [
        {
            "city_id": SOURCE_CITY_IDS[index % 4],
            "overpass_id": f"overpass-{index:03d}",
            "scene_ids": [f"scene-{index:03d}"],
            "target_date": "2024-07-01",
            "platform": "landsat-9",
        }
        for index in range(317)
    ]
    specs = task_specs_from_integrity_authorization(
        {"logical_overlay": {"retained_scenes": scenes, "overpasses": overpasses}}
    )
    counts = Counter(spec.kind for spec in specs)

    assert len(specs) == EXPECTED_TASK_COUNT
    assert counts == {
        "finalize_retained_scene": 523,
        "finalize_logical_cache": 1,
        "qa_overpass": 317,
        "compile_qa_city": 4,
        "finalize_qa_candidates": 1,
    }
    assert not {"download_asset", "finalize_scene", "finalize_download"} & set(counts)


def test_authorization_and_runtime_reject_old_write_namespaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_cache = tmp_path / "data/old/cache"
    old_database = tmp_path / "data/old/runtime/tasks.sqlite"
    settings = SimpleNamespace(cache_root=old_cache, database=old_database)
    monkeypatch.setattr(integrity_v2, "load_runner_settings", lambda root: settings)

    with pytest.raises(M3SourceIntegrityV2Error, match="isolated"):
        integrity_v2.build_m3_source_integrity_v2_authorization(
            tmp_path,
            logical_cache_completion_path=old_cache / "completion.json",
        )
    with pytest.raises(M3SourceIntegrityV2Error, match="isolated"):
        integrity_v2.create_m3_source_integrity_v2_authorization(
            tmp_path,
            old_database.parent / "authorization.json",
        )

    config = tmp_path / "runner.toml"
    config.write_text(
        """
schema_version = 2
algorithm_version = "m3-source-development-runtime-v2"
[inputs]
protocol_lock = "protocol.json"
source_acquisition_amendment = "amendment.json"
expanded_source_inventory = "inventory.json"
integrity_execution_authorization = "authorization.json"
[runtime]
original_database = "data/old/runtime/tasks.sqlite"
database = "data/v2/runtime/tasks.sqlite"
control = "data/v2/runtime/control.json"
status = "data/old/runtime/status.json"
log = "data/v2/runtime/worker.log"
physical_cache_root = "data/old/cache"
logical_cache_root = "data/v2/logical"
qa_output_root = "data/v2/qa"
completion_root = "manifests/v2"
[office_mode]
download_workers = 0
compute_workers = 1
raster_window_size = 512
lease_seconds = 900
heartbeat_seconds = 30
retry_base_seconds = 5
retry_max_seconds = 300
[limits]
network_requests_allowed = false
href_reads_allowed = false
physical_cache_mutation_allowed = false
original_queue_mutation_allowed = false
blind_test_city_access_allowed = false
predictor_read_or_build_allowed = false
fit_select_predict_or_score_allowed = false
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(M3SourceRuntimeV2Error, match="isolated"):
        load_runner_settings_v2(tmp_path, config)


def _qa_frames() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for candidate_id in QA_CANDIDATES:
        common = {
            "city_id": [SOURCE_CITY_IDS[0]],
            "candidate_id": [candidate_id],
            "target_date": ["2024-07-01"],
            "overpass_id": ["overpass-1"],
        }
        frames[f"{candidate_id}/tract_date_qa.parquet"] = pd.DataFrame(
            {**common, "tract_geoid": ["00000000001"], "date_usable": [True]}
        )
        frames[f"{candidate_id}/date_summary.parquet"] = pd.DataFrame(
            {**common, "date_usable": [True]}
        )
        frames[f"{candidate_id}/scene_contributions.parquet"] = pd.DataFrame(
            {**common, "tract_geoid": ["00000000001"], "scene_id": ["scene-1"]}
        )
    return frames


def test_v2_output_reader_authenticates_semantic_records(tmp_path: Path) -> None:
    paths = engine_v2._expected_output_paths(city_level=False)
    records = engine_v2._write_v2_frames(tmp_path, _qa_frames())
    commit = engine_v1._with_commit(
        {
            "state": "qa_overpass_complete",
            "output_files": records,
        }
    )
    commit_path = tmp_path / engine_v1.OVERPASS_COMMIT
    commit_path.write_text(json.dumps(commit), encoding="utf-8")

    observed, frames = engine_v2._read_v2_output(
        tmp_path,
        engine_v1.OVERPASS_COMMIT,
        expected_state="qa_overpass_complete",
        expected_paths=paths,
    )
    assert observed == commit
    assert tuple(frames) == paths

    changed = {**commit, "output_files": dict(commit["output_files"])}
    first = paths[0]
    changed["output_files"][first] = {
        **changed["output_files"][first],
        "semantic_sha256": "0" * 64,
    }
    changed.pop("commit_sha256")
    changed = engine_v1._with_commit(changed)
    commit_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(engine_v2.M3SourceDevelopmentV2Error, match="semantic"):
        engine_v2._read_v2_output(
            tmp_path,
            engine_v1.OVERPASS_COMMIT,
            expected_state="qa_overpass_complete",
            expected_paths=paths,
        )


def test_public_completion_authenticator_is_read_only_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization_path = tmp_path / "authorization.json"
    completion_path = tmp_path / engine_v1.FINAL_COMMIT
    authorization = {
        "source_qa_candidates_completion": engine_v1.FINAL_COMMIT,
        "commit_sha256": "a" * 64,
    }
    completion = engine_v1._with_commit(
        {"state": "source_qa_candidates_complete", "overpass_count": 317}
    )
    authorization_path.write_text("{}", encoding="utf-8")
    completion_path.write_text(json.dumps(completion), encoding="utf-8")
    original = completion_path.read_bytes()

    monkeypatch.setattr(
        engine_v2,
        "load_runner_settings_v2",
        lambda root: SimpleNamespace(root=Path(root), authorization=authorization_path),
    )
    monkeypatch.setattr(
        engine_v2,
        "authenticate_m3_source_integrity_v2_authorization",
        lambda root, path: authorization,
    )
    fake_engine = SimpleNamespace(
        authorization=authorization,
        _build_qa_completion=lambda: completion,
    )
    monkeypatch.setattr(
        engine_v2.M3SourceDevelopmentEngineV2,
        "create",
        lambda root, phase: fake_engine,
    )

    observed = engine_v2.authenticate_source_qa_candidates_completion_v2(
        tmp_path,
        completion_path,
        authorization_path=authorization_path,
    )
    assert observed == completion
    assert completion_path.read_bytes() == original

    fake_engine._build_qa_completion = lambda: engine_v1._with_commit(
        {"state": "source_qa_candidates_complete", "overpass_count": 316}
    )
    with pytest.raises(engine_v2.M3SourceDevelopmentV2Error, match="differs"):
        engine_v2.authenticate_source_qa_candidates_completion_v2(
            tmp_path,
            completion_path,
            authorization_path=authorization_path,
        )
