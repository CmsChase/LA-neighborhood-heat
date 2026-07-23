"""Generate authenticated joint M2/B1 residual spatial diagnostics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.residual_spatial_diagnostics import (
    DEFAULT_DIAGNOSTIC_CONFIG,
    analyze_residual_spatial,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_DIAGNOSTIC_CONFIG)
    parser.add_argument(
        "--table-output-directory",
        type=Path,
        help="Override the configured table directory (primarily for testing).",
    )
    parser.add_argument(
        "--figure-output-directory",
        type=Path,
        help="Override the configured generated-figure directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_residual_spatial(
        args.config,
        table_output_directory=args.table_output_directory,
        figure_output_directory=args.figure_output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "analysis_scope": payload["analysis_scope"],
                "family": payload["family"],
                "target_model_id": payload["target_model_id"],
                "baseline_model_id": payload["baseline_model_id"],
                "tract_date_row_count_per_model": payload[
                    "tract_date_row_count_per_model"
                ],
                "independent_date_count": payload["independent_date_count"],
                "independent_spatial_block_count": payload[
                    "independent_spatial_block_count"
                ],
                "date_block_cell_count_per_model": payload[
                    "date_block_cell_count_per_model"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
