"""Authorize, inspect, canary, or run blind Sentinel/static acquisition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_sentinel_static_runtime_v1 import (
    authenticate_launch_authorization,
    build_launch_authorization,
    create_launch_authorization,
    read_status,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preview-authorization", action="store_true")
    actions.add_argument("--authorize", action="store_true")
    actions.add_argument("--check-authorization", action="store_true")
    actions.add_argument("--canary", action="store_true")
    actions.add_argument("--run", action="store_true")
    actions.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.preview_authorization:
        result = build_launch_authorization(args.project_root)
    elif args.authorize:
        result = create_launch_authorization(args.project_root)
    elif args.check_authorization:
        result = authenticate_launch_authorization(args.project_root)
    elif args.canary:
        result = run(args.project_root, canary=True)
    elif args.run:
        result = run(args.project_root)
    else:
        result = read_status(args.project_root)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

