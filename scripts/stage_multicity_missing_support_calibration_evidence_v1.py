"""Run or authenticate the target-blind V12 support/calibration evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.multicity.missing_support_calibration_evidence_v1 import (
    CONFIG_PATH,
    stage_missing_support_calibration_evidence_v1,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the published fifteen-file terminal without networking.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = stage_missing_support_calibration_evidence_v1(
        args.config, check_only=args.check_only
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "publication_status": payload.get("publication_status"),
                "geography_complete": bool(
                    payload.get("evidence", {}).get("geography_commit_sha256")
                ),
                "worldcover_complete": bool(
                    payload.get("evidence", {}).get("worldcover_commit_sha256")
                ),
                "sentinel_smoke_complete": bool(
                    payload.get("evidence", {}).get("sentinel_commit_sha256")
                ),
                "predictor_build_authorized": payload["predictor_build_authorized"],
                "external_targets_unlocked": payload["external_targets_unlocked"],
                "next_gate": payload["next_gate"]["successful_evidence_next_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
