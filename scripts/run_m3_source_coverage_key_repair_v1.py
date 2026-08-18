"""Resume the existing v2 offline-QA queue through the authorized key adapter."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[name] = "1"
os.environ["GDAL_NUM_THREADS"] = "1"
os.environ["GDAL_PAM_ENABLED"] = "NO"

from la_heat.multicity.m3_source_coverage_key_repair_v1 import (  # noqa: E402
    AUTHORIZATION_PATH,
    RUN_ID,
)
from la_heat.multicity.m3_source_development_engine_coverage_key_repair_v1 import (  # noqa: E402
    M3SourceDevelopmentCoverageKeyRepairEngineV1,
    authenticate_source_qa_candidates_completion_coverage_key_repair_v1,
    execute_coverage_key_repair_queue_locked,
)
from la_heat.multicity.m3_source_development_engine_v2 import QA_PHASE  # noqa: E402
from la_heat.multicity.m3_source_development_worker_v2 import (  # noqa: E402
    WorkerOptionsV2,
    prepare_worker_v2,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--repair-authorization",
        type=Path,
        default=AUTHORIZATION_PATH,
    )
    parser.add_argument("--compute-workers", type=int, choices=(1,), default=1)
    parser.add_argument("--window-size", type=int, choices=(512,), default=512)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()

    settings, run_id, initial = prepare_worker_v2(args.project_root)
    if not run_id:
        print(json.dumps(initial, indent=2, ensure_ascii=False))
        return
    if run_id != RUN_ID:
        raise RuntimeError("Coverage-key repair resolved an unexpected v2 run.")
    if not args.start:
        print(json.dumps(initial, indent=2, ensure_ascii=False))
        return
    result = execute_coverage_key_repair_queue_locked(
        settings=settings,
        run_id=run_id,
        options=WorkerOptionsV2(
            phase=QA_PHASE,
            compute_workers=args.compute_workers,
            window_size=args.window_size,
        ),
        executor_factory=lambda: M3SourceDevelopmentCoverageKeyRepairEngineV1.create(
            settings.root,
            phase=QA_PHASE,
            repair_authorization_path=args.repair_authorization,
            require_initial_snapshot=True,
        ),
    )
    if result.get("state") == "qa_candidates_complete_waiting_for_loso_authorization":
        completion = authenticate_source_qa_candidates_completion_coverage_key_repair_v1(
            settings.root,
            repair_authorization_path=args.repair_authorization,
        )
        result = {**result, "authenticated_completion": completion["commit_sha256"]}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
