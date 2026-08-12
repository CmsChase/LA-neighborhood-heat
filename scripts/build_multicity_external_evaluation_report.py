"""Build or authenticate six frozen external-evaluation evidence figures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.external_evaluation_reporting import (
    authenticate_external_evaluation_report,
    build_external_evaluation_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evaluation-directory")
    parser.add_argument("--output-directory")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    options = {}
    if args.evaluation_directory:
        options["evaluation_directory"] = args.evaluation_directory
    if args.output_directory:
        options["output_directory"] = args.output_directory
    action = (
        authenticate_external_evaluation_report
        if args.check_only
        else build_external_evaluation_report
    )
    result = action(args.project_root, **options)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
