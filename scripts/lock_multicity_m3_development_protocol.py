from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_development_protocol_lock import (
    LOCK_PATH,
    authenticate_m3_development_protocol_lock,
    create_m3_development_protocol_lock,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or authenticate the append-only M3 development protocol lock."
    )
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=LOCK_PATH)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        payload = authenticate_m3_development_protocol_lock(args.project_root, args.output)
    else:
        payload = create_m3_development_protocol_lock(args.project_root, args.output)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "model_spec_locked": payload["model_spec_locked"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

