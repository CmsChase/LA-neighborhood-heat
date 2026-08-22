"""Preview, create, or authenticate the M3 blind-predictor parent permit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    AUTHORIZATION_PATH,
    authenticate_m3_blind_predictor_parent_authorization,
    build_m3_blind_predictor_parent_authorization,
    create_m3_blind_predictor_parent_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.write:
        mode = "write"
        payload = create_m3_blind_predictor_parent_authorization(
            args.project_root, args.output
        )
    elif args.check_only:
        mode = "check"
        payload = authenticate_m3_blind_predictor_parent_authorization(
            args.project_root, args.output
        )
    else:
        mode = "preview"
        payload = build_m3_blind_predictor_parent_authorization(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "city_count": payload["key_universe"]["city_count"],
                "target_date_count": payload["key_universe"]["target_date_count"],
                "tract_date_row_count": payload["key_universe"]["tract_date_row_count"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
