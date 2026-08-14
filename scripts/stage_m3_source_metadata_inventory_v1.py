from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_metadata_inventory_v1 import (
    AUTHORIZATION_PATH,
    INVENTORY_PATH,
    RAW_ROOT,
    authenticate_expanded_source_inventory,
    authenticate_source_metadata_inventory_authorization,
    build_expanded_source_inventory,
    build_source_metadata_inventory_authorization,
    create_source_metadata_inventory_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or create the append-only M3 metadata-only authorization, "
            "or build/authenticate its fixed expanded source inventory."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--authorization-path", type=Path, default=AUTHORIZATION_PATH
    )
    parser.add_argument("--inventory-path", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write-authorization",
        action="store_true",
        help="Explicitly create the append-only metadata-query authorization.",
    )
    mode.add_argument(
        "--check-authorization",
        action="store_true",
        help="Authenticate the existing authorization by exact reconstruction.",
    )
    mode.add_argument(
        "--build-inventory",
        action="store_true",
        help=(
            "Use the authenticated authorization to perform the fixed public "
            "metadata query and create the append-only expanded inventory."
        ),
    )
    mode.add_argument(
        "--check-inventory",
        action="store_true",
        help="Authenticate the completed expanded inventory without network access.",
    )
    args = parser.parse_args()

    if args.check_authorization:
        payload = authenticate_source_metadata_inventory_authorization(
            args.project_root, args.authorization_path
        )
        mode_name = "check_authorization"
    elif args.write_authorization:
        payload = create_source_metadata_inventory_authorization(
            args.project_root, args.authorization_path
        )
        mode_name = "write_authorization"
    elif args.build_inventory:
        payload = build_expanded_source_inventory(
            args.project_root,
            authorization_path=args.authorization_path,
            inventory_path=args.inventory_path,
            raw_root=args.raw_root,
        )
        mode_name = "build_inventory"
    elif args.check_inventory:
        payload = authenticate_expanded_source_inventory(
            args.project_root, args.inventory_path
        )
        mode_name = "check_inventory"
    else:
        payload = build_source_metadata_inventory_authorization(args.project_root)
        mode_name = "preview_authorization_only_no_file_written"

    print(
        json.dumps(
            {
                "mode": mode_name,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "metadata_query_city_ids": payload.get(
                    "metadata_query_city_ids", payload.get("queried_city_ids")
                ),
                "overpass_count": payload.get("overpass_count"),
                "blind_test_asset_or_value_accessed": payload.get(
                    "blind_test_asset_or_value_accessed", False
                ),
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
