"""Verify and safely import one returned terminal grouped-model result."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.returned_result_import import verify_and_import_returned_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--returned-root", type=Path, required=True)
    parser.add_argument(
        "--expected-archive-sha256",
        help="Required external SHA-256 when --returned-root is a ZIP file.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Reconstruct and authenticate in temporary staging without importing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = verify_and_import_returned_results(
        args.returned_root,
        args.project_root,
        verify_only=args.verify_only,
        expected_archive_sha256=args.expected_archive_sha256,
    )
    print(json.dumps(summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
