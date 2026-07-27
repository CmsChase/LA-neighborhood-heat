"""Authenticate and freeze the target-blind 2025 final-evaluation request."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_evaluation_protocol import (
    DEFAULT_CONFIG_PATH,
    prepare_final_evaluation_readiness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = prepare_final_evaluation_readiness(args.config)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "target_blind": payload["target_blind"],
                "values_read": payload["values_read"],
                "code_git_commit": payload["code_git_commit"],
                "request_sha256": payload["request_sha256"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
