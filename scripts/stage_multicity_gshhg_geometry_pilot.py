"""Run or reauthenticate the target-blind GSHHG geometry comparison."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.gshhg_geometry_pilot import (
    DEFAULT_CONFIG,
    DEFAULT_DIAGNOSTIC_TABLE,
    DEFAULT_MANIFEST,
    DEFAULT_V1_FAILURE_MANIFEST,
    audit_gshhg_geometry_pilot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--failure-output",
        type=Path,
        default=DEFAULT_V1_FAILURE_MANIFEST,
    )
    parser.add_argument(
        "--diagnostic-output",
        type=Path,
        default=DEFAULT_DIAGNOSTIC_TABLE,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Recompute and authenticate existing outputs without networking or "
            "replacing files."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_gshhg_geometry_pilot(
        args.config,
        output_path=args.output,
        failure_output_path=args.failure_output,
        diagnostic_output_path=args.diagnostic_output,
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "pilot_id": payload["pilot_id"],
                "source_sha256": payload["source_archive"]["sha256"],
                "v1_failure": payload["v1_failure"]["state"],
                "phoenix_comparison": payload["phoenix_comparison"],
                "source_frozen": payload["decision"]["source_frozen"],
                "predictor_build_authorized": payload[
                    "predictor_build_authorized"
                ],
                "next_safe_stage": payload["decision"]["next_safe_stage"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
