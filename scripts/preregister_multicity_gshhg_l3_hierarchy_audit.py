"""Create or authenticate the tracked-input-only GSHHG L3 audit preregistration."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.gshhg_l3_hierarchy_preregistration import (
    DEFAULT_CONFIG,
    DEFAULT_MANIFEST,
    preregister_gshhg_l3_hierarchy_audit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing append-only preregistration manifest.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = preregister_gshhg_l3_hierarchy_audit(
        args.config,
        output_path=args.output,
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "gshhg_l3_member_opened": payload["access_contract"][
                    "gshhg_l3_member_opened"
                ],
                "distance_values_computed": payload["access_contract"][
                    "distance_values_computed"
                ],
                "source_lock_created": payload["source_lock_created"],
                "predictor_build_authorized": payload[
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
