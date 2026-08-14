from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_acquisition_amendment import (
    AMENDMENT_PATH,
    authenticate_m3_source_acquisition_amendment,
    build_m3_source_acquisition_amendment,
    create_m3_source_acquisition_amendment,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit authenticated source support and preview, create, or authenticate "
            "the append-only acquisition amendment."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, default=AMENDMENT_PATH)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write-amendment",
        action="store_true",
        help="Explicitly create the append-only amendment after review.",
    )
    mode.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate an amendment that already exists.",
    )
    args = parser.parse_args()
    if args.check_only:
        payload = authenticate_m3_source_acquisition_amendment(
            args.project_root, args.output
        )
        mode_name = "check_only"
    elif args.write_amendment:
        payload = create_m3_source_acquisition_amendment(
            args.project_root, args.output
        )
        mode_name = "write_amendment"
    else:
        payload = build_m3_source_acquisition_amendment(args.project_root)
        mode_name = "preview_only_no_file_written"
    print(
        json.dumps(
            {
                "mode": mode_name,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "current_support_decision": payload["historical_preflight"]["decision"],
                "current_city_support": payload["historical_preflight"][
                    "current_city_support"
                ],
                "failing_city_ids": payload["historical_preflight"][
                    "failing_city_ids"
                ],
                "execution_authorized": payload["execution_authorized"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
