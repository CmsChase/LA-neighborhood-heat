"""Execute or same-claim resume the authorized one-time 2025 evaluation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_evaluation_protocol import (
    DEFAULT_CONFIG_PATH,
    execute_locked_final_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = execute_locked_final_evaluation(args.config)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "final_test_year": payload["final_test_year"],
                "completed": payload["completed"],
                "claim_id": payload["claim_id"],
                "output_directory": payload["output_directory"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
