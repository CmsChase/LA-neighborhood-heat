from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.source_target_authorization import (
    AUTHORIZATION_PATH,
    authenticate_source_target_authorization,
    create_source_target_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Issue or authenticate the LA 2020-2024 source-target permit."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--authorization-path", default=AUTHORIZATION_PATH.as_posix())
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if args.check_only:
        payload = authenticate_source_target_authorization(root, args.authorization_path)
    else:
        payload = create_source_target_authorization(root, args.authorization_path)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "lane": payload["lane"],
                "city_ids": payload["city_ids"],
                "years": payload["years"],
                "claim_id": payload["claim_id"],
                "commit_sha256": payload["commit_sha256"],
                "values_opened_marker_created": payload["access_audit"][
                    "values_opened_marker_created_by_authorization"
                ],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
