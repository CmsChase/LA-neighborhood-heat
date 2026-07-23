"""Compile downloaded Daymet V4 R1 subsets into target-blind weather features."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.daymet_feature_stage import (
    DEFAULT_FEATURE_UNIVERSE_PATH,
    DEFAULT_OUTPUT_DIRECTORY,
    build_daymet_feature_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research.toml"))
    parser.add_argument(
        "--feature-universe", type=Path, default=DEFAULT_FEATURE_UNIVERSE_PATH
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_daymet_feature_artifacts(
        args.config,
        args.feature_universe,
        args.output_directory,
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "phase2_promoted": payload["phase2_promoted"],
                "row_count": payload["row_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "feature_count": payload["feature_count"],
                "complete_feature_rows": payload["complete_feature_rows"],
                "semantic_feature_table_sha256": payload[
                    "semantic_feature_table_sha256"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
