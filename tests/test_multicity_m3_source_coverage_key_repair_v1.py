import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from la_heat.aligned_landsat import (
    COVERAGE_KEY,
    REQUIRED_ASSETS,
    decode_aligned_scene_arrays,
)
from la_heat.config import load_config
from la_heat.multicity import m3_source_coverage_key_repair_v1 as repair
from la_heat.multicity import (
    m3_source_development_engine_coverage_key_repair_v1 as repair_engine,
)
from la_heat.multicity import m3_source_development_engine_v2 as engine_v2


def _legacy_arrays() -> dict[str, np.ndarray]:
    return {
        "lwir11": np.array([[30000]], dtype=np.uint16),
        "qa_pixel": np.array([[0]], dtype=np.uint16),
        "qa": np.array([[100]], dtype=np.int16),
        "cdist": np.array([[1000]], dtype=np.int16),
        "qa_radsat": np.array([[0]], dtype=np.uint16),
        repair.LEGACY_COVERAGE_KEY: np.array([[True]], dtype=bool),
    }


def test_adapter_runs_after_loader_and_preserves_coverage_object_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_arrays()
    coverage = source[repair.LEGACY_COVERAGE_KEY]
    gate_calls: list[str] = []

    def fake_loader(*args: object, **kwargs: object) -> dict[str, np.ndarray]:
        del args
        gate = kwargs["before_value_access"]
        assert callable(gate)
        gate()
        return dict(source)

    monkeypatch.setattr(repair_engine, "load_retained_scene_arrays", fake_loader)
    observed = repair_engine.load_retained_scene_arrays_with_coverage_key_repair(
        ".",
        {},
        {},
        "los_angeles_ca",
        "scene-1",
        before_value_access=lambda: gate_calls.append("authenticated"),
    )

    assert gate_calls == ["authenticated"]
    assert set(observed) == {*REQUIRED_ASSETS, COVERAGE_KEY}
    assert repair.LEGACY_COVERAGE_KEY not in observed
    assert observed[COVERAGE_KEY] is coverage


@pytest.mark.parametrize("extra", ["unexpected", COVERAGE_KEY])
def test_adapter_rejects_extra_or_dual_keys(
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
) -> None:
    arrays = _legacy_arrays()
    arrays[extra] = np.array([[True]], dtype=bool)
    monkeypatch.setattr(
        repair_engine,
        "load_retained_scene_arrays",
        lambda *args, **kwargs: dict(arrays),
    )
    with pytest.raises(
        repair_engine.M3SourceCoverageKeyRepairEngineError,
        match="exact legacy coverage key",
    ):
        repair_engine.load_retained_scene_arrays_with_coverage_key_repair(
            ".",
            {},
            {},
            "los_angeles_ca",
            "scene-1",
            before_value_access=lambda: None,
        )


def test_synthetic_canary_reproduces_value_error_then_adapter_decodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_arrays()
    config = load_config(Path("configs/research.toml"))
    with pytest.raises(
        ValueError,
        match=r"Aligned scene lacks assets: \['_source_coverage'\]",
    ):
        decode_aligned_scene_arrays(
            scene_id="synthetic-scene",
            arrays=dict(legacy),
            config=config,
        )

    coverage = legacy[repair.LEGACY_COVERAGE_KEY]
    monkeypatch.setattr(
        repair_engine,
        "load_retained_scene_arrays",
        lambda *args, **kwargs: dict(legacy),
    )
    adapted = repair_engine.load_retained_scene_arrays_with_coverage_key_repair(
        ".",
        {},
        {},
        "los_angeles_ca",
        "synthetic-scene",
        before_value_access=lambda: None,
    )
    decoded = decode_aligned_scene_arrays(
        scene_id="synthetic-scene",
        arrays=adapted,
        config=config,
    )
    assert decoded.scene_id == "synthetic-scene"
    assert adapted[COVERAGE_KEY] is coverage


def test_static_bug_evidence_binds_exact_producer_and_consumer_keys() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = repair._bug_evidence(root)

    assert evidence["producer_key"] == "source_coverage"
    assert evidence["consumer_key"] == "_source_coverage"
    assert evidence["expected_exception_type"] == "ValueError"
    assert evidence["expected_exception_message"] == (
        "Aligned scene lacks assets: ['_source_coverage']"
    )
    assert evidence["tiff_bytes_or_values_read_to_build_evidence"] is False


def test_repair_base_lock_and_completion_bind_repair_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = {
        "commit_sha256": "a" * 64,
        "adapter_contract": {"input": "source_coverage", "output": COVERAGE_KEY},
    }
    engine = object.__new__(repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1)
    engine.coverage_key_repair_authorization = authorization
    monkeypatch.setattr(
        engine_v2.M3SourceDevelopmentEngineV2,
        "_base_lock",
        lambda self, city_id: {"city_id": city_id, "base": True},
    )
    lock = engine._base_lock("los_angeles_ca")
    assert lock["coverage_key_repair_authorization_commit_sha256"] == "a" * 64
    assert lock["coverage_key_adapter_contract_sha256"] == repair.canonical_sha256(
        authorization["adapter_contract"]
    )

    parent = engine_v2.engine_v1._with_commit({"state": "source_qa_candidates_complete"})
    monkeypatch.setattr(
        engine_v2.M3SourceDevelopmentEngineV2,
        "_build_qa_completion",
        lambda self: parent,
    )
    completion = engine._build_qa_completion()
    assert completion["coverage_key_repair_authorization_commit_sha256"] == "a" * 64
    assert engine_v2.engine_v1._is_committed(completion)


def test_engine_authenticates_repair_before_parent_context_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    authorization = {
        "commit_sha256": "a" * 64,
        "adapter_contract": {},
    }

    def authenticate(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        events.append("repair_authorized")
        return authorization

    def parent_create(
        cls: type[repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1],
        project_root: Path,
        **kwargs: object,
    ) -> repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1:
        del kwargs
        events.append("parent_context_construction")
        engine = object.__new__(cls)
        engine.settings = SimpleNamespace(root=project_root)
        engine.before_value_access = lambda: None
        return engine

    monkeypatch.setattr(
        repair_engine,
        "authenticate_m3_source_coverage_key_repair_authorization",
        authenticate,
    )
    monkeypatch.setattr(
        engine_v2.M3SourceDevelopmentEngineV2,
        "create",
        classmethod(parent_create),
    )
    observed = repair_engine.M3SourceDevelopmentCoverageKeyRepairEngineV1.create(tmp_path)

    assert observed.coverage_key_repair_authorization is authorization
    assert events == ["repair_authorized", "parent_context_construction"]


def test_runner_locks_before_running_and_pauses_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextmanager
    def exclusive(path: Path) -> Any:
        assert path == (tmp_path / "control.json").with_suffix(".worker.lock")
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    class Queue:
        def __init__(self, path: Path) -> None:
            assert path == tmp_path / "tasks.sqlite"

        def set_desired_state(self, run_id: str, state: str) -> None:
            assert run_id == repair.RUN_ID
            events.append(f"state_{state}")

    def fail(**kwargs: object) -> dict[str, object]:
        del kwargs
        events.append("execute")
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(repair_engine, "_exclusive_worker", exclusive)
    monkeypatch.setattr(repair_engine, "ModelRunQueue", Queue)
    monkeypatch.setattr(repair_engine, "_execute_phase_queue_unlocked_v2", fail)
    settings = SimpleNamespace(
        control=tmp_path / "control.json",
        database=tmp_path / "tasks.sqlite",
    )
    with pytest.raises(RuntimeError, match="synthetic worker failure"):
        repair_engine.execute_coverage_key_repair_queue_locked(
            settings=settings,
            run_id=repair.RUN_ID,
            options=repair_engine.WorkerOptionsV2(phase=engine_v2.QA_PHASE),
            executor_factory=lambda: events.append("engine") or object(),
        )

    assert events == [
        "lock_enter",
        "engine",
        "state_running",
        "execute",
        "state_paused",
        "lock_exit",
    ]


def test_runtime_permit_skips_dynamic_incident_database_file_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    immutable = tmp_path / "immutable.json"
    immutable.write_text("immutable", encoding="utf-8")
    record = {
        "path": "immutable.json",
        "bytes": immutable.stat().st_size,
        "sha256": repair.sha256_file(immutable),
    }
    monkeypatch.setattr(repair, "CODE_PATHS", ("immutable.json",))
    bug_evidence = {"synthetic_bug_evidence": True}
    initial_snapshot = {"first_qa_task": {"task_id": repair.FIRST_QA_TASK_ID}}
    payload = {
        "state": "m3_source_coverage_key_repair_authorized",
        "repair_scope": "rename_one_authenticated_in_memory_mapping_key_only",
        "parent_v2_authorization_commit_sha256": (repair.V2_AUTHORIZATION_COMMIT_SHA256),
        "logical_cache_completion_commit_sha256": (repair.LOGICAL_CACHE_COMPLETION_COMMIT_SHA256),
        "logical_global_cache_commit_sha256": (repair.LOGICAL_GLOBAL_CACHE_COMMIT_SHA256),
        "v2_run_id": repair.RUN_ID,
        "v2_task_plan_sha256": repair.TASK_PLAN_SHA256,
        "initial_paused_queue_snapshot": initial_snapshot,
        "incident_evidence": {
            "first_qa_task": initial_snapshot["first_qa_task"],
            "coverage_key_mismatch": bug_evidence,
            "status": {},
        },
        "source_city_ids": list(repair.SOURCE_CITY_IDS),
        "blind_test_city_ids": list(repair.BLIND_CITY_IDS),
        "required_landsat_assets": list(REQUIRED_ASSETS),
        "qa_candidate_ids": list(repair.QA_CANDIDATES),
        "adapter_contract": {
            "call_existing_authenticated_load_retained_scene_arrays_first": True,
            "required_input_keys": [*REQUIRED_ASSETS, repair.LEGACY_COVERAGE_KEY],
            "required_output_keys": [*REQUIRED_ASSETS, COVERAGE_KEY],
            "remove_key": repair.LEGACY_COVERAGE_KEY,
            "insert_key": COVERAGE_KEY,
            "coverage_array_object_copied_or_modified": False,
            "any_raster_array_value_inspected_or_changed_by_adapter": False,
        },
        "permissions": {
            "resume_existing_v2_queue_without_rebuild_or_reset": True,
            "execute_only_existing_317_qa_four_city_compile_and_final_tasks": True,
            "rename_legacy_coverage_key_in_memory_after_authenticated_loader": True,
            "write_existing_v2_qa_output_and_completion_paths": True,
            "modify_old_v2_authorization_or_logical_cache_commits": False,
            "modify_physical_cache_or_completed_logical_tasks": False,
            "network_or_href_reads": False,
            "blind_city_asset_predictor_qa_or_target_access": False,
            "predictor_read_or_build": False,
            "fit_select_predict_or_score": False,
            "change_year_city_candidate_or_support_gate": False,
        },
        "runtime_contract": {
            "compute_workers": 1,
            "download_workers": 0,
            "network_requests_allowed": False,
            "href_reads_allowed": False,
            "existing_run_id": repair.RUN_ID,
            "existing_task_plan_sha256": repair.TASK_PLAN_SHA256,
            "initial_completed_tasks_preserved": 524,
            "initial_pending_tasks_preserved": 322,
            "queue_rebuild_reset_or_task_rewrite_allowed": False,
        },
        "access_audit": {
            "authorization_read_landsat_or_qa_raster_values": False,
            "authorization_read_predictor_or_target_values": False,
            "authorization_accessed_blind_city_data": False,
            "authorization_modified_queue_cache_or_existing_manifest": False,
            "authorization_fit_selected_predicted_or_scored": False,
        },
        "code_identity": {"immutable.json": record},
        "inputs": {
            "parent_v2_authorization": {
                **record,
                "commit_sha256": repair.V2_AUTHORIZATION_COMMIT_SHA256,
            },
            "logical_cache_completion": {
                **record,
                "commit_sha256": repair.LOGICAL_CACHE_COMPLETION_COMMIT_SHA256,
            },
            "logical_global_cache_commit": {
                **record,
                "commit_sha256": repair.LOGICAL_GLOBAL_CACHE_COMMIT_SHA256,
            },
            "incident_queue_database": {
                "path": "mutated.sqlite",
                "bytes": 1,
                "sha256": "0" * 64,
            },
        },
    }
    payload["claim_id"] = repair.canonical_sha256(payload)
    permit = repair._with_commit(payload)
    authorization_path = tmp_path / "repair.json"
    authorization_path.write_text(json.dumps(permit), encoding="utf-8")
    monkeypatch.setattr(
        repair,
        "load_runner_settings_v2",
        lambda root: SimpleNamespace(
            root=Path(root),
            authorization=tmp_path / "v2.json",
            database=tmp_path / "mutated.sqlite",
        ),
    )
    monkeypatch.setattr(
        repair,
        "authenticate_m3_source_integrity_v2_authorization",
        lambda root, path: {"commit_sha256": repair.V2_AUTHORIZATION_COMMIT_SHA256},
    )
    monkeypatch.setattr(
        repair,
        "authenticate_logical_global_cache",
        lambda root, auth: {"commit_sha256": repair.LOGICAL_GLOBAL_CACHE_COMMIT_SHA256},
    )
    monkeypatch.setattr(repair, "_bug_evidence", lambda root: bug_evidence)
    monkeypatch.setattr(repair, "_validate_initial_snapshot", lambda snapshot: None)
    monkeypatch.setattr(repair, "_validate_incident_status", lambda status: None)
    monkeypatch.setattr(repair, "_queue_snapshot", lambda path: {"dynamic": True})
    progress_calls: list[tuple[dict[str, bool], bool]] = []
    monkeypatch.setattr(
        repair,
        "_validate_safe_progress",
        lambda snapshot, terminal: progress_calls.append((snapshot, terminal)),
    )

    observed = repair.load_m3_source_coverage_key_repair_runtime_permit(
        tmp_path,
        authorization_path,
        require_terminal_queue=True,
    )
    assert observed == permit
    assert progress_calls == [({"dynamic": True}, True)]
