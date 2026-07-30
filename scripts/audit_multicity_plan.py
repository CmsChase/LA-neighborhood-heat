"""Authenticate the tracked-only L3 planning transition."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.plan_audit import audit_multicity_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/multicity/experiment.toml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/multicity/PLAN_READINESS.json"),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate without writing or replacing the readiness record.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = audit_multicity_plan(
        args.config,
        output_path=args.output,
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "planning_stage": payload["planning_stage"],
                "experiment_id": payload["experiment_id"],
                "cities": [city["id"] for city in payload["cities"]],
                "external_targets_unlocked": payload["locks"][
                    "external_targets_unlocked"
                ],
                "authorized_now": payload["authorized_now"],
                "next_safe_stage": payload["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
