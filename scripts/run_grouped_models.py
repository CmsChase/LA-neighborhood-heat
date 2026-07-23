"""Prepare, calibrate, run, pause, or resume grouped development-model fits."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.grouped_model_run import (
    DEFAULT_QUEUE_PATH,
    DEFAULT_RUNS_ROOT,
    DEFAULT_STATUS_PATH,
    calibrate_grouped_runtime,
    run_grouped_coordinator,
    status_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--queue-path", type=Path, default=DEFAULT_QUEUE_PATH)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Persist desired_state=running before entering the coordinator loop.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Authenticate inputs and initialize the durable plan without fitting.",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Time one representative inner fit per model without changing the queue.",
    )
    parser.add_argument(
        "--calibration-output",
        type=Path,
        default=Path("data/interim/model_runs/calibration.json"),
    )
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--lease-seconds", type=float, default=600.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=float, default=5.0)
    parser.add_argument("--retry-max-seconds", type=float, default=300.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.calibrate:
        payload = calibrate_grouped_runtime(output_path=args.calibration_output)
        print(
            json.dumps(
                {
                    "sample_count": payload["sample_count"],
                    "projected_inner_seconds_one_worker": payload[
                        "projected_inner_seconds_one_worker"
                    ],
                    "queue_mutated": payload["queue_mutated"],
                    "commit_sha256": payload["commit_sha256"],
                },
                indent=2,
            )
        )
        return 0
    payload = run_grouped_coordinator(
        workers=args.workers,
        queue_path=args.queue_path,
        runs_root=args.runs_root,
        status_path=args.status_path,
        resume=args.resume,
        prepare_only=args.prepare_only,
        max_tasks=args.max_tasks,
        lease_seconds=args.lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        max_attempts=args.max_attempts,
        retry_base_seconds=args.retry_base_seconds,
        retry_max_seconds=args.retry_max_seconds,
    )
    print(json.dumps(status_summary(payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
