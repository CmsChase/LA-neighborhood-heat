"""Create or authenticate the narrow planning-v10 source-stage hotfix."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.plan_source_evidence_hotfix_transition_v10 import (
    authorize_multicity_source_evidence_hotfix_resume,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    payload = authorize_multicity_source_evidence_hotfix_resume(
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "missing_source_evidence_staging_authorized": payload[
                    "authorized_now"
                ]["portable_predictor_missing_source_evidence_staging"],
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
