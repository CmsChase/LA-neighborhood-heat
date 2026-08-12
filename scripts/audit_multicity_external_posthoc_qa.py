"""Build or authenticate the aggregate-only post-hoc external QA report."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.posthoc_qa_audit import (
    authenticate_posthoc_qa_audit,
    build_posthoc_qa_audit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output-directory")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Reproduce the aggregate audit in memory and verify existing outputs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = {}
    if args.output_directory:
        options["output_directory"] = args.output_directory
    action = authenticate_posthoc_qa_audit if args.check_only else build_posthoc_qa_audit
    result = action(args.project_root, **options)
    print(
        json.dumps(
            {
                "analysis_class": result["analysis_class"],
                "formal_result_unchanged": result["formal_result_unchanged"],
                "anomaly": {
                    "city_id": result["observed_anomaly"]["city_id"],
                    "target_date": result["observed_anomaly"]["target_date"],
                },
                "commit_sha256": result["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
