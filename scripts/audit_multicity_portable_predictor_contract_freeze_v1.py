"""Create or authenticate the portable predictor-contract V1 decision."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    audit_portable_predictor_contract_freeze_v1,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing append-only V1 decision.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_portable_predictor_contract_freeze_v1(
        write=not args.check_only,
    )
    decision = payload["decision"]
    print(
        json.dumps(
            {
                "state": payload["state"],
                "outcome": payload["outcome"],
                "contract_freeze_passed": decision["contract_freeze_passed"],
                "observed_blockers": payload["evidence_gaps"][
                    "observed_blockers"
                ],
                "predictor_build_authorized": decision[
                    "predictor_build_authorized"
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
