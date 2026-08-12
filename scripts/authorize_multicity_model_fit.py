from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.model_fit_authorization import (
    AUTHORIZATION_PATH,
    authenticate_model_fit_authorization,
    create_model_fit_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Issue or authenticate the frozen multicity model-fit permit."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--authorization-path", default=AUTHORIZATION_PATH.as_posix())
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    payload = (
        authenticate_model_fit_authorization(root, args.authorization_path)
        if args.check_only
        else create_model_fit_authorization(root, args.authorization_path)
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "claim_id": payload["claim_id"],
                "commit_sha256": payload["commit_sha256"],
                "permissions": payload["permissions"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
