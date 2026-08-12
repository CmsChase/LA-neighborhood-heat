"""Run or resume only the authorized Los Angeles 2020-2024 target lane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.source_target_worker import (
    DEFAULT_AUTHORIZATION,
    DEFAULT_STATUS,
    SourceWorkerSettings,
    run_authorized_source_worker,
)
from la_heat.multicity.target_runtime import DEFAULT_DATABASE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 17))
    parser.add_argument("--lease-seconds", type=float, default=600.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=60.0)
    parser.add_argument("--retry-base-seconds", type=float, default=5.0)
    parser.add_argument("--retry-max-seconds", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument(
        "--start",
        action="store_true",
        help="Persist desired_state=running before entering the worker loop.",
    )
    args = parser.parse_args()
    result = run_authorized_source_worker(
        args.project_root,
        authorization_path=args.authorization,
        database_path=args.database,
        status_path=args.status,
        settings=SourceWorkerSettings(
            workers=args.workers,
            lease_seconds=args.lease_seconds,
            heartbeat_interval_seconds=args.heartbeat_seconds,
            retry_base_seconds=args.retry_base_seconds,
            retry_max_seconds=args.retry_max_seconds,
            poll_seconds=args.poll_seconds,
        ),
        start=args.start,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
