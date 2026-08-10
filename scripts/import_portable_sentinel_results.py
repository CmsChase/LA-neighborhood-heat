"""Verify and import the gaming-laptop Sentinel result ZIP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.portable_sentinel_return import (
    verify_and_import_portable_sentinel_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--checksum",
        type=Path,
        help="Defaults to the .zip.sha256 file beside the ZIP.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    summary = verify_and_import_portable_sentinel_results(
        args.archive,
        args.project_root,
        checksum_path=args.checksum,
        verify_only=args.verify_only,
    )
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
