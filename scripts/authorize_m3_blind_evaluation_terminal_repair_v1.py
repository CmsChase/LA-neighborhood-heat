"""Preview, create, or authenticate terminal M3 evaluation repair authorization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_evaluation_terminal_repair_v1 import (
    authenticate_authorization,
    build_authorization,
    create_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.write:
        mode, payload = "write", create_authorization(args.project_root)
    elif args.check_only:
        mode, payload = "check", authenticate_authorization(args.project_root)
    else:
        mode, payload = "preview", build_authorization(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
