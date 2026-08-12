"""Build or authenticate the compact multicity evaluation evidence package."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.evidence_export import (
    DEFAULT_OUTPUT,
    authenticate_multicity_evidence_export,
    build_multicity_evidence_export,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    action = (
        authenticate_multicity_evidence_export
        if args.check_only
        else build_multicity_evidence_export
    )
    result = action(args.project_root, output_directory=args.output)
    archive = (args.project_root / args.output).resolve().with_suffix(".zip")
    print(
        json.dumps(
            {
                "state": result["state"],
                "scientific_outcome": result["scientific_outcome"],
                "file_count": len(result["files"]),
                "archive": str(archive),
                "commit_sha256": result["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
