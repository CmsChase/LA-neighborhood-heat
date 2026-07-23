"""Build the target-blind predeclared development validation split draft."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.validation_splits import build_validation_split_draft


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/validation_splits.toml"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_validation_split_draft(args.config)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase_complete": payload["phase_complete"],
                "row_count": payload["row_count"],
                "independent_date_count": payload["independent_date_count"],
                "spatial_block_count": payload["spatial_block_count"],
                "fold_counts": payload["fold_counts"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
