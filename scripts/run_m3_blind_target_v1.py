"""Run or authenticate the resumable combined blind-target build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_target_v1 import authenticate_completion, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    payload = (
        authenticate_completion(args.project_root) if args.check_only else run(args.project_root)
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "dates": payload["date_count"],
                "usable_dates": payload["usable_date_count"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
