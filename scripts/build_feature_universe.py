"""Build the target-blind Phase 2 tract-date feature key universe."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.feature_universe import (
    DEFAULT_OUTPUT_DIRECTORY,
    build_feature_key_universe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overpass-manifest",
        type=Path,
        default=Path("manifests/target_inventory/primary_overpass_manifest.csv"),
    )
    parser.add_argument(
        "--tract-manifest",
        type=Path,
        default=Path("data/interim/targets/primary_tract_manifest.parquet"),
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    parser.add_argument("--final-test-year", type=int, default=2025)
    parser.add_argument("--expected-date-count", type=int, default=90)
    parser.add_argument("--expected-tract-count", type=int, default=1096)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_feature_key_universe(
        overpass_manifest_path=args.overpass_manifest,
        tract_manifest_path=args.tract_manifest,
        output_directory=args.output_directory,
        final_test_year=args.final_test_year,
        expected_date_count=args.expected_date_count,
        expected_tract_count=args.expected_tract_count,
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "phase2_promoted": payload["phase2_promoted"],
                "eligible_date_count": payload["eligible_date_count"],
                "primary_tract_count": payload["primary_tract_count"],
                "key_count": payload["key_count"],
                "years": payload["years"],
                "semantic_key_sha256": payload["semantic_key_sha256"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

