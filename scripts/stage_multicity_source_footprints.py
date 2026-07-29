"""Stage or authenticate one city's public-metadata source footprints."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.source_footprints import (
    DEFAULT_SOURCE_CONFIG,
    stage_city_source_footprints,
    verify_city_source_footprints,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/multicity/experiment.toml"),
    )
    parser.add_argument("--city", required=True)
    parser.add_argument(
        "--source-config",
        type=Path,
        default=DEFAULT_SOURCE_CONFIG,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate an existing snapshot without making network requests.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_only:
        payload = verify_city_source_footprints(
            args.config,
            args.city,
            source_config_path=args.source_config,
        )
    else:
        payload = stage_city_source_footprints(
            args.config,
            args.city,
            source_config_path=args.source_config,
        )
    families = payload["source_families"]
    print(
        json.dumps(
            {
                "state": payload["state"],
                "city": payload["city"]["id"],
                "landsat_wrs": families["landsat_wrs"]["member_ids"],
                "sentinel_mgrs": families["sentinel_mgrs"]["member_ids"],
                "daymet_intersecting_cells": families["daymet_cells"][
                    "member_count"
                ],
                "daymet_window": {
                    "y": families["daymet_cells"]["window"][
                        "y_indices_inclusive"
                    ],
                    "x": families["daymet_cells"]["window"][
                        "x_indices_inclusive"
                    ],
                },
                "terrain_tiles": families["terrain_windows"]["member_ids"],
                "target_or_asset_values_read": False,
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
