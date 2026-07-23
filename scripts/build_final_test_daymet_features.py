"""Build frozen target-blind Daymet predictors for the 2025 test keys."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_test_daymet_features import (
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_PROVENANCE_PATH,
    build_final_test_daymet_feature_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal-lock",
        type=Path,
        default=Path("manifests/model_lock/MODEL_LOCK.json"),
    )
    parser.add_argument(
        "--landsat-inventory-directory",
        type=Path,
        default=Path("manifests/final_test_2025/landsat_inventory"),
    )
    parser.add_argument(
        "--daymet-manifest-directory",
        type=Path,
        default=Path("manifests/final_test_2025/daymet_grid"),
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_final_test_daymet_feature_artifacts(
        formal_lock_path=args.formal_lock,
        landsat_inventory_directory=args.landsat_inventory_directory,
        daymet_manifest_directory=args.daymet_manifest_directory,
        output_directory=args.output_directory,
        provenance_path=args.provenance,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "target_blind": payload["target_blind"],
                "row_count": payload["row_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "feature_count": payload["feature_count"],
                "complete_feature_rows": payload["complete_feature_rows"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
