"""Preview, create, or check the blind Sentinel/static acquisition permit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_sentinel_static_acquisition_authorization_v1 import (
    authenticate_authorization,
    build_authorization,
    create_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check-only", action="store_true")
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
                "city_count": len(payload["blind_city_ids"]),
                "sentinel_physical_acquisition_count": payload["source_contract"]
                ["sentinel"]["selected_physical_acquisition_count"],
                "static_feature_count": payload["predictor_scope"][
                    "static_feature_count"
                ],
                "sentinel_feature_count": payload["predictor_scope"][
                    "sentinel_feature_count"
                ],
                "value_or_network_read_authorized_now": payload["permissions"][
                    "sentinel_and_static_value_or_network_read_now"
                ],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
