"""Commit the frozen, pre-score model-selection contract without reading labels."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_selection import (
    DEFAULT_MODEL_SELECTION_OUTPUT_DIRECTORY,
    build_model_selection_freeze_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/model_selection.toml")
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_MODEL_SELECTION_OUTPUT_DIRECTORY,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_model_selection_freeze_manifest(
        args.config,
        args.output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "frozen_before_scores": payload["frozen_before_scores"],
                "candidate_counts": payload["candidate_counts"],
                "config_semantic_sha256": payload["config"]["semantic_sha256"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
