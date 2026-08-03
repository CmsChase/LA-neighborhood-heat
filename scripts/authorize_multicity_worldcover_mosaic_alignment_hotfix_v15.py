"""Create or authenticate the WorldCover mosaic-alignment planning V15."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_worldcover_mosaic_alignment_hotfix_transition_v15 import (
    authorize_multicity_worldcover_mosaic_alignment_hotfix_v15,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing canonical planning V15 record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = authorize_multicity_worldcover_mosaic_alignment_hotfix_v15(
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
                "native_mosaic_bounds": transition["authorized_fix"][
                    "worldcover_native_mosaic_bounds_after"
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
