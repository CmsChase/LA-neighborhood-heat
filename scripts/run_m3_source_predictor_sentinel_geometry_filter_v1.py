"""Authorize, execute, or check the Houston exact-AOI STAC filter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_predictor_sentinel_geometry_filter_v1 import (
    authenticate_authorization,
    authenticate_completion,
    build_authorization,
    create_authorization,
    execute_repair,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write-authorization", action="store_true")
    actions.add_argument("--check-authorization", action="store_true")
    actions.add_argument("--execute", action="store_true")
    actions.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()
    if args.write_authorization:
        mode, payload = "write_authorization", create_authorization(args.project_root)
    elif args.check_authorization:
        mode, payload = "check_authorization", authenticate_authorization(args.project_root)
    elif args.execute:
        mode, payload = "execute", execute_repair(args.project_root)
    elif args.check_completion:
        mode, payload = "check_completion", authenticate_completion(args.project_root)
    else:
        mode, payload = "preview", build_authorization(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
