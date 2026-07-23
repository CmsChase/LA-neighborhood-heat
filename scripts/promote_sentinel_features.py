"""Promote audited Sentinel-2 predictors to the stable Phase 2 input directory."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.sentinel_feature_stage import (
    DEFAULT_FEATURE_UNIVERSE_PATH,
    DEFAULT_INVENTORY_DIRECTORY,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_RESEARCH_CONFIG_PATH,
    DEFAULT_SENTINEL_CONFIG_PATH,
    DEFAULT_SOURCE_DIRECTORY,
    promote_sentinel_features,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, default=DEFAULT_SOURCE_DIRECTORY)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument(
        "--feature-universe", type=Path, default=DEFAULT_FEATURE_UNIVERSE_PATH
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument(
        "--inventory-directory", type=Path, default=DEFAULT_INVENTORY_DIRECTORY
    )
    parser.add_argument("--research-config", type=Path, default=DEFAULT_RESEARCH_CONFIG_PATH)
    parser.add_argument("--sentinel-config", type=Path, default=DEFAULT_SENTINEL_CONFIG_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = promote_sentinel_features(
        source_directory=args.source_directory,
        output_directory=args.output_directory,
        feature_universe_path=args.feature_universe,
        registry_path=args.registry,
        inventory_directory=args.inventory_directory,
        research_config_path=args.research_config,
        sentinel_config_path=args.sentinel_config,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "promoted_outputs_valid": payload["promoted_outputs_valid"],
                "row_count": payload["row_count"],
                "feature_available_row_count": payload[
                    "feature_available_row_count"
                ],
                "feature_missing_row_count": payload["feature_missing_row_count"],
                "scientific_processor_sha256": payload[
                    "scientific_processor_sha256"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

