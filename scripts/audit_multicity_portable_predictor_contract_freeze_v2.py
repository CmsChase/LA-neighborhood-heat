"""Create or authenticate the deferred portable predictor-contract V2 decision."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.portable_predictor_contract_freeze_v2 import (
    audit_portable_predictor_contract_freeze_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing append-only V2 decision.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_portable_predictor_contract_freeze_v2(
        check_only=args.check_only,
    )
    decision = payload["decision"]
    print(
        json.dumps(
            {
                "state": payload["state"],
                "outcome": payload["outcome"],
                "contract_freeze_passed": decision["contract_freeze_passed"],
                "candidate_registry_bound": decision[
                    "candidate_rules_and_registry_bound_for_evidence_stage"
                ],
                "observed_blockers": decision["new_v2_blockers_observed"],
                "predictor_build_authorized": decision[
                    "predictor_build_authorized_now"
                ],
                "next_safe_stage": decision["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
