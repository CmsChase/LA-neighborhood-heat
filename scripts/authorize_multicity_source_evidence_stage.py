"""Create or authenticate the narrow multicity planning transition v9."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_source_evidence_transition_v9 import (
    authorize_multicity_source_evidence_stage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing canonical planning-v9 record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = authorize_multicity_source_evidence_stage(
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "boundary_metadata_staging_authorized": payload["authorized_now"][
                    "boundary_and_public_metadata_staging"
                ],
                "predictor_contract_freeze_authorized": payload["authorized_now"][
                    "portable_predictor_source_and_calibration_contract_freeze"
                ],
                "missing_source_evidence_staging_authorized": payload["authorized_now"][
                    "portable_predictor_missing_source_evidence_staging"
                ],
                "predictor_build_authorized": payload["authorized_now"]["predictor_construction"],
                "next_safe_stage": payload["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
