"""Stage or authenticate one authorized cross-city Census geography snapshot."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.geography import (
    stage_city_geography,
    verify_city_geography,
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
        "--check-only",
        action="store_true",
        help="Authenticate an existing completed snapshot without network access.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    action = verify_city_geography if args.check_only else stage_city_geography
    payload = action(args.config, args.city)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "city": payload["city"]["id"],
                "place_source": payload["sources"]["place"]["selected"]["label"],
                "tract_source": payload["sources"]["tract"]["selected"]["label"],
                "tract_candidates": payload["geography"]["tract_candidates_in_bbox"],
                "primary_tracts": payload["geography"]["primary_tract_count"],
                "county_fips": payload["geography"]["county_fips"],
                "target_or_qa_values_read": any(payload["target_access"].values()),
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

