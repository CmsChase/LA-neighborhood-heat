"""Build or authenticate target-independent four-city 5 km spatial blocks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.spatial_blocks import build_multicity_spatial_blocks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    result = build_multicity_spatial_blocks(
        args.project_root,
        check_only=args.check_only,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
