"""Build or verify the compact display data used by the project website."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.website_export import build_website_export, verify_website_export

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "website" / "public" / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()

    if arguments.verify_only:
        verify_website_export(ROOT, arguments.output_dir)
        print(json.dumps({"state": "verified", "output": str(arguments.output_dir)}))
        return

    manifest = build_website_export(ROOT, arguments.output_dir)
    print(
        json.dumps(
            {
                "state": manifest["state"],
                "output": str(arguments.output_dir),
                "counts": manifest["counts"],
            }
        )
    )


if __name__ == "__main__":
    main()
