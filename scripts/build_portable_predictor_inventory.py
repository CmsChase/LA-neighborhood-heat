"""Build or verify the four-city target-blind predictor key inventory."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.portable_predictor_inventory import (
    DEFAULT_CONFIG,
    build_portable_predictor_inventory,
    verify_portable_predictor_inventory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = (
        verify_portable_predictor_inventory(args.config)
        if args.check_only
        else build_portable_predictor_inventory(args.config)
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "cities": {
                    city_id: {
                        "dates": city["primary_date_count"],
                        "tracts": city["tract_count"],
                        "keys": city["predictor_key_count"],
                    }
                    for city_id, city in payload["cities"].items()
                },
                "combined": payload["combined"],
                "external_targets_read": False,
                "next_safe_stage": payload["decision"]["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
