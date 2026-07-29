"""Create or verify the target-blind portable water-distance source review."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.water_distance_review import (
    DEFAULT_CONFIG,
    DEFAULT_MANIFEST,
    audit_water_distance_review,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the committed review without replacing it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_water_distance_review(
        args.config,
        output_path=args.output,
        write=not args.check_only,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "review_outcome": payload["review_outcome"],
                "source_lock_created": payload["source_lock_created"],
                "predictor_build_authorized": payload[
                    "predictor_build_authorized"
                ],
                "audit_program_network_requests": payload["access_contract"][
                    "audit_program_network_requests"
                ],
                "official_documentation_web_review_performed": payload[
                    "access_contract"
                ]["official_documentation_web_review_performed"],
                "target_values_read": any(
                    payload["access_contract"][name]
                    for name in (
                        "landsat_thermal_values_read",
                        "landsat_target_qa_values_read",
                        "external_lst_values_read",
                        "external_target_files_opened",
                    )
                ),
                "next_safe_stage": "target_blind_gshhg_geometry_comparison",
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
