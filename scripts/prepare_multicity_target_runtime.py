"""Initialize the frozen 159-unit target queue without authorizing execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.target_runtime import initialize_target_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    status = initialize_target_runtime(
        args.project_root,
        database_path=args.database,
    )
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
