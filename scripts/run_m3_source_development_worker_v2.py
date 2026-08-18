"""Run one phase of the independent offline M3 integrity-v2 continuation."""

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

from la_heat.model_run_queue import ModelRunQueue  # noqa: E402
from la_heat.multicity.m3_source_development_engine_v2 import (  # noqa: E402
    M3SourceDevelopmentEngineV2,
)
from la_heat.multicity.m3_source_development_worker_v2 import (  # noqa: E402
    PHASES,
    WorkerOptionsV2,
    execute_phase_queue_v2,
    prepare_worker_v2,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--compute-workers", type=int, choices=(1,), default=1)
    parser.add_argument("--window-size", type=int, choices=(512,), default=512)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()

    settings, run_id, initial = prepare_worker_v2(args.project_root)
    if not run_id or not args.start:
        print(json.dumps(initial, indent=2, ensure_ascii=False))
        return
    engine = M3SourceDevelopmentEngineV2.create(
        settings.root,
        phase=args.phase,
    )
    queue = ModelRunQueue(settings.database)
    queue.set_desired_state(run_id, "running")
    result = execute_phase_queue_v2(
        settings=settings,
        run_id=run_id,
        options=WorkerOptionsV2(
            phase=args.phase,
            compute_workers=args.compute_workers,
            window_size=args.window_size,
        ),
        executor_factory=lambda: engine,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
