"""Build metadata-only Sentinel inventories for the portable four-city run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.portable_sentinel_inventory import (
    CITY_IDS,
    authenticate_portable_sentinel_inventory,
    build_all_portable_sentinel_inventories,
    build_portable_sentinel_inventory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze full Planetary Computer metadata for exact Sentinel IDs; "
            "no raster assets are opened."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
    )
    parser.add_argument(
        "--city",
        choices=(*CITY_IDS, "all"),
        default="all",
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate completed outputs without querying the network.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.check_only:
        city_ids = CITY_IDS if args.city == "all" else (args.city,)
        result = {
            city_id: authenticate_portable_sentinel_inventory(
                args.project_root, city_id
            )
            for city_id in city_ids
        }
    elif args.city == "all":
        result = build_all_portable_sentinel_inventories(
            args.project_root,
            batch_size=args.batch_size,
        )
    else:
        result = {
            args.city: build_portable_sentinel_inventory(
                args.project_root,
                args.city,
                batch_size=args.batch_size,
            )
        }
    concise = {
        city_id: {
            "state": payload["state"],
            "counts": payload["counts"],
            "commit_sha256": payload["commit_sha256"],
        }
        for city_id, payload in result.items()
    }
    print(json.dumps(concise, indent=2))


if __name__ == "__main__":
    main()
