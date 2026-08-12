from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.evaluation_protocol_lock import (
    LOCK_PATH,
    authenticate_protocol_model_lock,
    create_protocol_model_lock,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or authenticate the pre-fit four-city protocol/model lock."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--lock-path", default=LOCK_PATH.as_posix())
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if args.check_only:
        result = authenticate_protocol_model_lock(root, args.lock_path)
    else:
        result = create_protocol_model_lock(root, args.lock_path)
    print(json.dumps({
        "state": result["state"],
        "commit_sha256": result["commit_sha256"],
        "protocol_locked": result["protocol_locked"],
        "model_fit_authorized": result["permissions"]["model_fit_authorized"],
        "external_targets_unlocked": result["permissions"]["external_targets_unlocked"],
        "next_safe_stage": result["next_safe_stage"],
    }, indent=2))


if __name__ == "__main__":
    main()
