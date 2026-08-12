"""Run or resume only the authorized combined three-city external lane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.external_target_authorization import AUTHORIZATION_PATH
from la_heat.multicity.external_target_worker import (
    DEFAULT_STATUS,
    ExternalWorkerSettings,
    run_authorized_external_worker,
)
from la_heat.multicity.target_runtime import DEFAULT_DATABASE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=1)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    result = run_authorized_external_worker(
        args.project_root,
        authorization_path=args.authorization,
        database_path=args.database,
        status_path=args.status,
        settings=ExternalWorkerSettings(workers=args.workers),
        start=args.start,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
