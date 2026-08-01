"""Create or authenticate the narrow multicity planning transition v7."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_freeze_transition_v7 import (
    authorize_multicity_water_distance_freeze,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing canonical v7 planning record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = authorize_multicity_water_distance_freeze(
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "l3_geometry_read_authorized": payload["authorized_now"][
                    "target_blind_gshhg_l3_hierarchy_geometry_read"
                ],
                "freeze_decision_authorized": payload["authorized_now"][
                    "portable_predictor_source_freeze"
                ],
                "predictor_build_authorized": payload["authorized_now"][
                    "predictor_construction"
                ],
                "next_safe_stage": payload["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
