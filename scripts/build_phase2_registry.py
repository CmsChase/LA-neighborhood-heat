"""Build the provisional combined Phase 2 feature registry."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.phase2_registry import (
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_STATIC_REGISTRY_PATH,
    build_phase2_registry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--static-registry",
        type=Path,
        default=DEFAULT_STATIC_REGISTRY_PATH,
    )
    parser.add_argument("--static-provenance", type=Path)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_phase2_registry(
        args.static_registry,
        args.output_directory,
        static_provenance_path=args.static_provenance,
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "phase2_complete": payload["phase2_complete"],
                "row_count": payload["row_count"],
                "role_counts": payload["role_counts"],
                "ordered_registry_semantic_sha256": payload[
                    "ordered_registry_semantic_sha256"
                ],
                "pipeline_sha256": payload["pipeline_sha256"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

