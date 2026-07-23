"""Generate authenticated figures from frozen development diagnostic outputs."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_diagnostic_figures import (
    DEFAULT_FIGURE_CONFIG,
    generate_model_diagnostic_figures,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_FIGURE_CONFIG)
    parser.add_argument("--figure-output-directory", type=Path)
    parser.add_argument("--table-output-directory", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = generate_model_diagnostic_figures(
        args.config,
        figure_output_directory=args.figure_output_directory,
        table_output_directory=args.table_output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "analysis_scope": payload["analysis_scope"],
                "figure_count": len(payload["output_files"]) - 1,
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
