"""Create or authenticate the narrow WorldCover-path hotfix planning V13."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_missing_support_calibration_hotfix_transition_v13 import (
    authorize_multicity_missing_support_calibration_hotfix_v13,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing canonical planning V13 record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = authorize_multicity_missing_support_calibration_hotfix_v13(
        write=not args.check_only
    )
    transition = payload["transition"]
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "evidence_authorized": payload["authorized_now"][
                    "portable_predictor_missing_support_and_calibration_evidence_staging"
                ],
                "worldcover_path_prefix": transition["authorized_fix"][
                    "authorized_exact_path_prefix"
                ],
                "resume_checkpoints": len(transition["resume_checkpoints"]),
                "next_safe_stage": payload["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
