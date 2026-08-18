"""Repair-aware wrapper for the existing M3 integrity-v2 QA engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.aligned_landsat import COVERAGE_KEY, REQUIRED_ASSETS
from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity import m3_source_development_engine as engine_v1
from la_heat.multicity import m3_source_development_engine_v2 as engine_v2
from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    ALGORITHM_VERSION as REPAIR_ALGORITHM_VERSION,
)
from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    AUTHORIZATION_PATH as REPAIR_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    LEGACY_COVERAGE_KEY,
    _inside,
    _read_committed,
    authenticate_m3_source_coverage_key_repair_authorization,
    authenticate_m3_source_coverage_key_repair_value_gate,
    load_m3_source_coverage_key_repair_runtime_permit,
)
from la_heat.multicity.m3_source_development_engine_v2 import (
    ALGORITHM_VERSION as V2_ENGINE_ALGORITHM_VERSION,
)
from la_heat.multicity.m3_source_development_engine_v2 import (
    QA_PHASE,
    M3SourceDevelopmentEngineV2,
)
from la_heat.multicity.m3_source_development_runtime import QA_CANDIDATES
from la_heat.multicity.m3_source_development_runtime_v2 import (
    RunnerSettingsV2,
    load_runner_settings_v2,
)
from la_heat.multicity.m3_source_development_worker_v2 import (
    WorkerOptionsV2,
    _exclusive_worker,
    _execute_phase_queue_unlocked_v2,
)
from la_heat.multicity.m3_source_integrity_v2 import load_retained_scene_arrays
from la_heat.multicity.m3_source_offline_qa import reconstruct_overpass_candidates
from la_heat.multicity.target_context import load_target_city_context
from la_heat.provenance import atomic_json, canonical_sha256

ALGORITHM_VERSION: Final = "m3-source-development-coverage-key-repair-v1"


class M3SourceCoverageKeyRepairEngineError(RuntimeError):
    """Raised when the wrapper attempts anything beyond the one-key adapter."""


def load_retained_scene_arrays_with_coverage_key_repair(
    project_root: str | Path,
    authorization: Mapping[str, Any],
    physical_plan: Mapping[str, Any],
    city_id: str,
    scene_id: str,
    *,
    before_value_access: Any,
) -> dict[str, np.ndarray]:
    """Authenticate through v2 first, then rename one in-memory mapping key."""

    arrays = load_retained_scene_arrays(
        project_root,
        authorization,
        physical_plan,
        city_id,
        scene_id,
        before_value_access=before_value_access,
    )
    expected_input = {*REQUIRED_ASSETS, LEGACY_COVERAGE_KEY}
    if set(arrays) != expected_input or COVERAGE_KEY in arrays:
        raise M3SourceCoverageKeyRepairEngineError(
            "Authenticated v2 loader no longer exposes the exact legacy coverage key."
        )
    coverage = arrays.pop(LEGACY_COVERAGE_KEY)
    arrays[COVERAGE_KEY] = coverage
    if set(arrays) != {*REQUIRED_ASSETS, COVERAGE_KEY} or arrays[COVERAGE_KEY] is not coverage:
        raise M3SourceCoverageKeyRepairEngineError(
            "Coverage-key adapter changed the authenticated array set or object."
        )
    return arrays


class M3SourceDevelopmentCoverageKeyRepairEngineV1(M3SourceDevelopmentEngineV2):
    """Use v2 unchanged except for the authorized post-loader key rename."""

    __slots__ = ("coverage_key_repair_authorization",)

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        phase: str = QA_PHASE,
        repair_authorization_path: str | Path = REPAIR_AUTHORIZATION_PATH,
        require_initial_snapshot: bool = True,
        config_path: str | Path = "configs/research.toml",
        context_loader: Any = load_target_city_context,
        before_value_access: Any | None = None,
        reconstructor: Any = reconstruct_overpass_candidates,
    ) -> M3SourceDevelopmentCoverageKeyRepairEngineV1:
        if phase != QA_PHASE:
            raise M3SourceCoverageKeyRepairEngineError(
                "Coverage-key repair authorizes only the existing offline QA phase."
            )
        root = Path(project_root).resolve()
        repair = (
            authenticate_m3_source_coverage_key_repair_authorization(
                root, repair_authorization_path
            )
            if require_initial_snapshot
            else load_m3_source_coverage_key_repair_runtime_permit(
                root,
                repair_authorization_path,
                require_terminal_queue=True,
            )
        )
        engine = super().create(
            root,
            phase=phase,
            config_path=config_path,
            context_loader=context_loader,
            before_value_access=before_value_access,
            reconstructor=reconstructor,
        )
        if engine.settings.root.resolve() != root:
            raise M3SourceCoverageKeyRepairEngineError(
                "Parent v2 engine resolved a different project root."
            )
        base_gate = engine.before_value_access

        def combined_gate() -> None:
            base_gate()
            authenticate_m3_source_coverage_key_repair_value_gate(
                engine.settings.root,
                repair,
                repair_authorization_path,
            )

        engine.before_value_access = combined_gate
        engine.coverage_key_repair_authorization = repair
        return engine

    def _base_lock(self, city_id: str) -> dict[str, Any]:
        return {
            **super()._base_lock(city_id),
            "coverage_key_repair_authorization_commit_sha256": (
                self.coverage_key_repair_authorization["commit_sha256"]
            ),
            "coverage_key_adapter_contract_sha256": canonical_sha256(
                self.coverage_key_repair_authorization["adapter_contract"]
            ),
        }

    def execute_qa_overpass(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_id = engine_v1._identifier(payload.get("city_id"), label="city_id")
        overpass_id = engine_v1._identifier(payload.get("overpass_id"), label="overpass_id")
        if tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES:
            raise M3SourceCoverageKeyRepairEngineError("QA candidate order or membership changed.")
        row = self._overpass(city_id, overpass_id)
        for key, expected in (
            ("scene_ids", row["scene_ids"]),
            ("target_date", engine_v1._target_date(row)),
            ("platform", engine_v1._platform(row)),
        ):
            if key in payload and payload[key] != expected:
                raise M3SourceCoverageKeyRepairEngineError(f"Overpass task changed {key}.")
        context = self.contexts[city_id]
        expected_lock = self._overpass_lock(row)
        directory = self.settings.qa_output_root / "by_overpass" / city_id / overpass_id
        if (directory / engine_v1.OVERPASS_COMMIT).is_file():
            observed, _frames = engine_v2._authenticate_overpass_output(self, row)
            if (
                observed.get("coverage_key_repair_applied_after_authenticated_loader") is not True
                or observed.get("coverage_key_repair_authorization_commit_sha256")
                != self.coverage_key_repair_authorization["commit_sha256"]
            ):
                raise M3SourceCoverageKeyRepairEngineError(
                    "Cached QA overpass is not bound to the coverage-key repair."
                )
            return {
                "state": "qa_overpass_complete",
                "cache": "hit",
                "commit_sha256": observed["commit_sha256"],
            }

        def local_loader(requested_city: str, scene_id: str) -> Mapping[str, Any]:
            if requested_city != city_id:
                raise M3SourceCoverageKeyRepairEngineError("Local loader crossed a source city.")
            return load_retained_scene_arrays_with_coverage_key_repair(
                self.settings.root,
                self.authorization,
                self.plan,
                requested_city,
                scene_id,
                before_value_access=self.before_value_access,
            )

        results = self.reconstructor(
            city_id=city_id,
            scene_ids=tuple(str(value) for value in row["scene_ids"]),
            loader=local_loader,
            context=context,
            base_config=self.config,
            target_date=engine_v1._target_date(row),
            overpass_id=overpass_id,
            platform=engine_v1._platform(row),
            union_city_coverage_fraction=engine_v1._coverage(row),
            tract_manifest_sha256=str(
                row.get("tract_manifest_sha256", engine_v2.canonical_sha256(context.locks))
            ),
        )
        if tuple(results) != QA_CANDIDATES:
            raise M3SourceCoverageKeyRepairEngineError("Offline reconstructor changed candidates.")
        frames: dict[str, pd.DataFrame] = {}
        for candidate_id, result in results.items():
            candidate_frames = {
                "tract_date_qa.parquet": result.tract_date_qa.copy(),
                "date_summary.parquet": pd.DataFrame([result.summary]),
                "scene_contributions.parquet": result.scene_contributions.copy(),
            }
            for filename, frame in candidate_frames.items():
                frame.insert(0, "candidate_id", candidate_id)
                frame.insert(0, "city_id", city_id)
                frames[f"{candidate_id}/{filename}"] = frame
        directory.mkdir(parents=True, exist_ok=True)
        output_files = engine_v2._write_v2_frames(directory, frames)
        commit = engine_v1._with_commit(
            {
                "schema_version": 2,
                "algorithm_version": V2_ENGINE_ALGORITHM_VERSION,
                "state": "qa_overpass_complete",
                "cache_lock": expected_lock,
                "candidate_ids": list(QA_CANDIDATES),
                "raw_scene_arrays_loaded_once_per_overpass": True,
                "coverage_key_repair_applied_after_authenticated_loader": True,
                "coverage_key_repair_authorization_commit_sha256": (
                    self.coverage_key_repair_authorization["commit_sha256"]
                ),
                "network_requests_performed": 0,
                "href_reads_performed": 0,
                "physical_cache_mutated": False,
                "output_files": output_files,
                "model_fit_or_selection_performed": False,
                "blind_test_city_accessed": False,
            }
        )
        atomic_json(commit, directory / engine_v1.OVERPASS_COMMIT)
        return {
            "state": "qa_overpass_complete",
            "cache": "built",
            "commit_sha256": commit["commit_sha256"],
        }

    def _build_qa_completion(self) -> dict[str, Any]:
        parent = super()._build_qa_completion()
        unsigned = dict(parent)
        unsigned.pop("commit_sha256", None)
        unsigned.update(
            {
                "coverage_key_repair_algorithm_version": REPAIR_ALGORITHM_VERSION,
                "coverage_key_repair_authorization_commit_sha256": (
                    self.coverage_key_repair_authorization["commit_sha256"]
                ),
                "coverage_key_adapter": {
                    "input_key": LEGACY_COVERAGE_KEY,
                    "output_key": COVERAGE_KEY,
                    "array_values_changed": False,
                },
            }
        )
        return engine_v1._with_commit(unsigned)


def execute_coverage_key_repair_queue_locked(
    *,
    settings: RunnerSettingsV2,
    run_id: str,
    options: WorkerOptionsV2,
    executor_factory: Callable[[], M3SourceDevelopmentCoverageKeyRepairEngineV1],
) -> dict[str, Any]:
    """Acquire the inherited worker lock before allowing the queue to run."""

    lock_path = settings.control.with_suffix(".worker.lock")
    with _exclusive_worker(lock_path):
        engine = executor_factory()
        queue = ModelRunQueue(settings.database)
        try:
            queue.set_desired_state(run_id, "running")
            return _execute_phase_queue_unlocked_v2(
                settings=settings,
                run_id=run_id,
                options=options,
                executor_factory=lambda: engine,
            )
        except BaseException:
            queue.set_desired_state(run_id, "paused")
            raise


def authenticate_source_qa_candidates_completion_coverage_key_repair_v1(
    project_root: str | Path,
    completion_path: str | Path | None = None,
    *,
    repair_authorization_path: str | Path = REPAIR_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Read-only repair-aware authentication of the full QA completion chain."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    engine = M3SourceDevelopmentCoverageKeyRepairEngineV1.create(
        root,
        phase=QA_PHASE,
        repair_authorization_path=repair_authorization_path,
        require_initial_snapshot=False,
    )
    expected_path = _inside(
        root,
        str(engine.authorization["source_qa_candidates_completion"]),
        label="QA completion",
    )
    requested = (
        expected_path
        if completion_path is None
        else _inside(root, completion_path, label="QA completion")
    )
    if (
        requested != expected_path
        or requested.parent != settings.completion_root
        or requested.name != engine_v1.FINAL_COMMIT
    ):
        raise M3SourceCoverageKeyRepairEngineError("QA completion path changed.")
    observed = _read_committed(requested, label="Repair-aware QA completion")
    expected = engine._build_qa_completion()
    if observed != expected:
        raise M3SourceCoverageKeyRepairEngineError(
            "Repair-aware QA completion differs from its authenticated chain."
        )
    return observed


__all__ = [
    "M3SourceDevelopmentCoverageKeyRepairEngineV1",
    "authenticate_source_qa_candidates_completion_coverage_key_repair_v1",
    "execute_coverage_key_repair_queue_locked",
    "load_retained_scene_arrays_with_coverage_key_repair",
]
