"""Analyze authenticated development OOF endpoint and sensor diagnostics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_endpoint_diagnostics import (
    DEFAULT_ENDPOINT_DIAGNOSTICS_CONFIG,
    analyze_model_endpoints,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_ENDPOINT_DIAGNOSTICS_CONFIG
    )
    parser.add_argument(
        "--evaluation-directory",
        type=Path,
        help="Override the authenticated compiled-OOF directory.",
    )
    parser.add_argument(
        "--target-directory",
        type=Path,
        help="Override the authenticated target directory.",
    )
    parser.add_argument(
        "--model-dataset-directory",
        type=Path,
        help="Override the authenticated model-dataset directory.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Override the configured report output directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_model_endpoints(
        args.config,
        evaluation_directory=args.evaluation_directory,
        target_directory=args.target_directory,
        model_dataset_directory=args.model_dataset_directory,
        output_directory=args.output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "analysis_scope": payload["analysis_scope"],
                "tract_date_row_count": payload["tract_date_row_count"],
                "independent_date_count": payload["independent_date_count"],
                "independent_spatial_block_count": payload[
                    "independent_spatial_block_count"
                ],
                "relative_endpoint_gate_date_count": payload[
                    "relative_endpoint_gate_date_count"
                ],
                "focus_comparison": payload["focus_comparison"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
