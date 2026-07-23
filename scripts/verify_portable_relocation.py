"""Fully authenticate a relocated model context before queue initialization."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_run_context import load_model_run_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    context = load_model_run_context(
        portable_manifest_path=args.manifest,
        portable_root=args.project_root,
    )
    print(
        json.dumps(
            {
                "state": "verified",
                "context_run_id": context.run_id,
                "portable_relocation_commit_sha256": (
                    context.portable_relocation_commit_sha256
                ),
                "row_count": len(context.keys),
                "fold_count": len(context.fold_definitions),
                "final_test_year": context.model_selection.final_test_year,
                "final_test_unlocked": context.model_selection.unlock_final_test,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
