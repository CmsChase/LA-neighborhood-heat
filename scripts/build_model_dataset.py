"""Assemble and promote the legal Phase 2 development model dataset."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.feature_assembly_stage import (
    DEFAULT_OUTPUT_DIRECTORY,
    build_model_dataset_artifacts,
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
    payload = build_model_dataset_artifacts(
        args.config,
        output_directory=args.output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase2_feature_commit_verified": payload[
                    "phase2_feature_commit_verified"
                ],
                "ready_for_modeling": payload["ready_for_modeling"],
                "row_count": payload["row_count"],
                "column_count": payload["column_count"],
                "independent_date_count": payload["independent_date_count"],
                "tract_count": payload["tract_count"],
                "model_feature_count": payload["model_feature_count"],
                "complete_model_feature_rows": payload[
                    "complete_model_feature_rows"
                ],
                "semantic_model_table_sha256": payload[
                    "semantic_model_table_sha256"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
