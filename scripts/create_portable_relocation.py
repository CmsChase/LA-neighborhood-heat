"""Create the audited nine-input relocation manifest in a copied bundle."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.portable_relocation import build_portable_relocation_manifest
from la_heat.provenance import sha256_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("portable_relocation.json"),
        help="Absolute path or path relative to --bundle-root.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    destination = build_portable_relocation_manifest(
        args.source_root,
        args.bundle_root,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "state": "complete",
                "path": str(destination),
                "sha256": sha256_file(destination),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
