from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
os.environ["GDAL_NUM_THREADS"] = "1"

from la_heat.model_run_queue import ModelRunQueue  # noqa: E402
from la_heat.multicity.m3_source_development_engine import (  # noqa: E402
    M3SourceDevelopmentEngine,
)
from la_heat.multicity.m3_source_development_worker import (  # noqa: E402
    OFFLINE_PHASE,
    ONLINE_PHASE,
    WorkerOptions,
    execute_phase_queue,
    prepare_worker,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the resumable low-load M3 source download or offline QA phase."
    )
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=(ONLINE_PHASE, OFFLINE_PHASE), required=True)
    parser.add_argument("--download-workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--compute-workers", type=int, choices=(1,), default=1)
    parser.add_argument("--window-size", type=int, choices=(512,), default=512)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    settings, run_id, initial = prepare_worker(args.project_root)
    if not run_id:
        print(json.dumps(initial, indent=2, ensure_ascii=False))
        return
    queue = ModelRunQueue(settings.database)
    queue.set_desired_state(run_id, "running" if args.start else "paused")
    if not args.start:
        print(json.dumps(initial, indent=2, ensure_ascii=False))
        return
    options = WorkerOptions(
        phase=args.phase,
        download_workers=args.download_workers,
        compute_workers=args.compute_workers,
        window_size=args.window_size,
    )
    engine = M3SourceDevelopmentEngine.create(
        settings.root,
        phase=args.phase,
    )
    # The executor is thread-safe. Sharing it prevents each of the two download
    # threads from loading a second copy of all four frozen city contexts.
    engine_factory = lambda: engine  # noqa: E731
    result = execute_phase_queue(
        settings=settings,
        run_id=run_id,
        options=options,
        executor_factory=engine_factory,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
