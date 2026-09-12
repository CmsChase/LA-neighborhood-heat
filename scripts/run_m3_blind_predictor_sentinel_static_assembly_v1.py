"""Authorize or run blind-city Sentinel/static offline assembly."""

from __future__ import annotations

import argparse
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_sentinel_static_assembly_v1 import (
    authenticate_authorization,
    create_authorization,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create-authorization", action="store_true")
    mode.add_argument("--check-authorization", action="store_true")
    mode.add_argument("--canary", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.create_authorization:
        payload = create_authorization(args.project_root)
    elif args.check_authorization:
        payload = authenticate_authorization(args.project_root)
    else:
        payload = run(args.project_root, canary=args.canary)
    print(payload)


if __name__ == "__main__":
    main()
