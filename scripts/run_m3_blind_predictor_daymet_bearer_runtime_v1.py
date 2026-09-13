"""Authorize, check, or run blind Daymet with an ephemeral bearer token."""

from __future__ import annotations

import argparse
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_daymet_bearer_runtime_v1 import (
    authenticate_authorization,
    create_authorization,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--create-authorization", action="store_true")
    modes.add_argument("--check-authorization", action="store_true")
    modes.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.create_authorization:
        payload = create_authorization(args.project_root)
    elif args.check_authorization:
        payload = authenticate_authorization(args.project_root)
    else:
        payload = run(args.project_root)
    print(payload)


if __name__ == "__main__":
    main()
