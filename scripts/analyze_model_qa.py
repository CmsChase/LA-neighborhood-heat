"""Generate authenticated development OOF QA and missingness diagnostics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_qa_diagnostics import (
    DEFAULT_QA_DIAGNOSTIC_CONFIG,
    analyze_model_qa,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_QA_DIAGNOSTIC_CONFIG)
    parser.add_argument("--output-directory", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_model_qa(
        args.config,
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
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
