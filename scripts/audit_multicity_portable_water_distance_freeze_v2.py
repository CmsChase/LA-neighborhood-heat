"""Create or authenticate the append-only portable water-distance V2 freeze."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.portable_water_distance_freeze_v2 import (
    DEFAULT_CONFIG,
    DEFAULT_MANIFEST,
    audit_portable_water_distance_freeze_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing append-only V2 decision.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_portable_water_distance_freeze_v2(
        args.config,
        output_path=DEFAULT_MANIFEST,
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "outcome": payload["outcome"],
                "source_lock_created": payload["locks"]["source_lock_created"],
                "algorithm_lock_created": payload["locks"][
                    "algorithm_lock_created"
                ],
                "predictor_build_authorized": payload["locks"][
                    "predictor_build_authorized"
                ],
                "next_safe_stage": payload["next_gate"]["stage_id"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
