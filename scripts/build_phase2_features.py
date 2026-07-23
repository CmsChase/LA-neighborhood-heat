"""Assemble and promote the target-blind Phase 2 predictor table."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.phase2_feature_stage import (
    DEFAULT_OUTPUT_DIRECTORY,
    build_phase2_feature_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research.toml"))
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_phase2_feature_artifacts(
        args.config,
        output_directory=args.output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase2_complete": payload["phase2_complete"],
                "target_blind": payload["target_blind"],
                "row_count": payload["row_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "model_feature_count": payload["model_feature_count"],
                "complete_model_feature_rows": payload[
                    "complete_model_feature_rows"
                ],
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
