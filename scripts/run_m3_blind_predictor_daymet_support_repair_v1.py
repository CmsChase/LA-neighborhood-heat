"""Authorize, run, or check preserved blind Daymet support gaps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_daymet_support_repair_v1 import (
    authenticate_authorization,
    authenticate_completion,
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
    modes.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()
    if args.create_authorization:
        payload = create_authorization(args.project_root)
    elif args.check_authorization:
        payload = authenticate_authorization(args.project_root)
    elif args.run:
        payload = run(args.project_root)
    else:
        payload = authenticate_completion(args.project_root)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
