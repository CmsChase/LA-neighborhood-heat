"""Prepare, pause/resume, or checkpoint fixed-selected M2 feature ablations."""

from __future__ import annotations

import argparse
import json

from la_heat.feature_ablation import run_feature_ablation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-tasks", type=int)
    args = parser.parse_args()
    result = run_feature_ablation(
        workers=args.workers,
        resume=args.resume,
        prepare_only=args.prepare_only,
        max_tasks=args.max_tasks,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
