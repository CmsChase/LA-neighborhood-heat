"""Create or authenticate the narrow multicity planning transition V12."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_missing_support_calibration_transition_v12 import (
    authorize_multicity_missing_support_calibration_evidence_v12,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing canonical planning V12 record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = authorize_multicity_missing_support_calibration_evidence_v12(
        write=not args.check_only
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "evidence_authorized": payload["authorized_now"][
                    "portable_predictor_missing_support_and_calibration_evidence_staging"
                ],
                "predictor_build_authorized": payload["authorized_now"][
                    "predictor_construction"
                ],
                "target_access_authorized": payload["authorized_now"][
                    "external_target_or_qa_value_access"
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
