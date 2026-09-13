"""Run or authenticate the exact-schema M3 blind-prediction repair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_prediction_v2 import authenticate_completion, run


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
                "rows": payload["row_count"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
