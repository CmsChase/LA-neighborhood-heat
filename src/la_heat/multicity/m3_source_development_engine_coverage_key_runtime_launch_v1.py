"""Launch-amendment wrapper around the hash-bound coverage-key repair engine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity import m3_source_development_engine as engine_v1
from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    _inside,
    _read_committed,
)
from la_heat.multicity.m3_source_coverage_key_runtime_launch_v1 import (
    ALGORITHM_VERSION as LAUNCH_ALGORITHM_VERSION,
)
from la_heat.multicity.m3_source_coverage_key_runtime_launch_v1 import (
    AUTHORIZATION_PATH as LAUNCH_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_source_coverage_key_runtime_launch_v1 import (
    authenticate_m3_source_coverage_key_runtime_launch_bundle,
    authenticate_m3_source_coverage_key_runtime_launch_value_gate,
)
from la_heat.multicity.m3_source_development_engine_coverage_key_repair_v1 import (
    M3SourceDevelopmentCoverageKeyRepairEngineV1,
)
from la_heat.multicity.m3_source_development_engine_v2 import (
    QA_PHASE,
    M3SourceDevelopmentEngineV2,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    RunnerSettingsV2,
    load_runner_settings_v2,
)
from la_heat.multicity.m3_source_development_worker_v2 import (
    WorkerOptionsV2,
    _exclusive_worker,
    _execute_phase_queue_unlocked_v2,
)
from la_heat.multicity.m3_source_offline_qa import reconstruct_overpass_candidates
from la_heat.multicity.target_context import load_target_city_context

ALGORITHM_VERSION: Final = "m3-source-development-coverage-key-runtime-launch-v1"


class M3SourceCoverageKeyRuntimeLaunchEngineError(RuntimeError):
    """Raised when the launch wrapper leaves its append-only authorization."""


class M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1(
    M3SourceDevelopmentCoverageKeyRepairEngineV1
):
    """Construct the repair engine without prepare_worker/runtime initialize."""

    __slots__ = ("coverage_key_runtime_launch_authorization",)

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        phase: str = QA_PHASE,
        launch_authorization_path: str | Path = LAUNCH_AUTHORIZATION_PATH,
        require_terminal_queue: bool = False,
        config_path: str | Path = "configs/research.toml",
        context_loader: Any = load_target_city_context,
        before_value_access: Any | None = None,
        reconstructor: Any = reconstruct_overpass_candidates,
    ) -> M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1:
        if phase != QA_PHASE:
            raise M3SourceCoverageKeyRuntimeLaunchEngineError(
                "Runtime launch authorizes only the existing offline QA phase."
            )
        root = Path(project_root).resolve()
        launch, parent_repair = authenticate_m3_source_coverage_key_runtime_launch_bundle(
            root,
            launch_authorization_path,
            require_terminal_queue=require_terminal_queue,
        )
        engine = M3SourceDevelopmentEngineV2.create.__func__(
            cls,
            root,
            phase=phase,
            config_path=config_path,
            context_loader=context_loader,
            before_value_access=before_value_access,
            reconstructor=reconstructor,
        )
        if engine.settings.root.resolve() != root:
            raise M3SourceCoverageKeyRuntimeLaunchEngineError(
                "Parent v2 engine resolved a different project root."
            )
        base_gate = engine.before_value_access

        def combined_gate() -> None:
            base_gate()
            authenticate_m3_source_coverage_key_runtime_launch_value_gate(
                root,
                launch,
                parent_repair,
                launch_authorization_path,
            )

        engine.before_value_access = combined_gate
        engine.coverage_key_repair_authorization = parent_repair
        engine.coverage_key_runtime_launch_authorization = launch
        return engine

    def _base_lock(self, city_id: str) -> dict[str, Any]:
        return {
            **super()._base_lock(city_id),
            "coverage_key_runtime_launch_authorization_commit_sha256": (
                self.coverage_key_runtime_launch_authorization["commit_sha256"]
            ),
        }

    def _build_qa_completion(self) -> dict[str, Any]:
        parent = super()._build_qa_completion()
        unsigned = dict(parent)
        unsigned.pop("commit_sha256", None)
        unsigned.update(
            {
                "coverage_key_runtime_launch_algorithm_version": (LAUNCH_ALGORITHM_VERSION),
                "coverage_key_runtime_launch_authorization_commit_sha256": (
                    self.coverage_key_runtime_launch_authorization["commit_sha256"]
                ),
                "prepare_worker_v2_or_initialize_source_runtime_v2_performed": False,
                "model_run_queue_schema_open_after_all_permits_authenticated": True,
                "task_plan_rebuilt_reset_or_rewritten": False,
                "database_hash_transition_used_as_runtime_input": False,
            }
        )
        return engine_v1._with_commit(unsigned)


def execute_coverage_key_runtime_launch_queue_locked(
    *,
    settings: RunnerSettingsV2,
    run_id: str,
    options: WorkerOptionsV2,
    executor_factory: Callable[[], M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1],
) -> dict[str, Any]:
    """Authenticate inside the lock before the ordinary queue schema open."""

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


def authenticate_source_qa_candidates_completion_coverage_key_runtime_launch_v1(
    project_root: str | Path,
    completion_path: str | Path | None = None,
    *,
    launch_authorization_path: str | Path = LAUNCH_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Read-only authentication of the launch-bound full QA completion chain."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    engine = M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1.create(
        root,
        phase=QA_PHASE,
        launch_authorization_path=launch_authorization_path,
        require_terminal_queue=True,
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
        raise M3SourceCoverageKeyRuntimeLaunchEngineError("QA completion path changed.")
    observed = _read_committed(requested, label="Launch-aware QA completion")
    expected = engine._build_qa_completion()
    if observed != expected:
        raise M3SourceCoverageKeyRuntimeLaunchEngineError(
            "Launch-aware QA completion differs from its authenticated chain."
        )
    return observed


__all__ = [
    "M3SourceDevelopmentCoverageKeyRuntimeLaunchEngineV1",
    "authenticate_source_qa_candidates_completion_coverage_key_runtime_launch_v1",
    "execute_coverage_key_runtime_launch_queue_locked",
]
