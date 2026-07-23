"""Analyze authenticated development OOF results with frozen clustered uncertainty."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_result_analysis import (
    DEFAULT_RESULT_ANALYSIS_CONFIG,
    analyze_model_results,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_RESULT_ANALYSIS_CONFIG)
    parser.add_argument(
        "--evaluation-directory",
        type=Path,
        help="Override the authenticated evaluation directory (primarily for relocation/testing).",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Override the configured output directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_model_results(
        args.config,
        evaluation_directory=args.evaluation_directory,
        output_directory=args.output_directory,
    )
    gates = payload["protocol_success_gates"]
    print(
        json.dumps(
            {
                "state": payload["state"],
                "analysis_scope": payload["analysis_scope"],
                "comparison": payload["comparison"],
                "independent_date_count": payload["independent_date_count"],
                "independent_spatial_block_count": payload[
                    "independent_spatial_block_count"
                ],
                "tract_date_row_count": payload["tract_date_row_count"],
                "overall_protocol_success_gate_pass": gates[
                    "overall_protocol_success_gate_pass"
                ],
                "ten_percent_threshold_ci_supported": gates[
                    "ten_percent_threshold_ci_supported"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
