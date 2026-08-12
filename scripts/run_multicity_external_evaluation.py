"""Run the frozen one-time external evaluator after all three city compiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.external_evaluation import (
    authenticate_external_evaluation_completion,
    run_and_publish_external_evaluation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-directory")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the completed evaluation without recalculating it.",
    )
    args = parser.parse_args()
    options = {}
    if args.output_directory:
        options["output_directory"] = args.output_directory
    action = (
        authenticate_external_evaluation_completion
        if args.check_only
        else run_and_publish_external_evaluation
    )
    result = action(args.project_root, **options)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
