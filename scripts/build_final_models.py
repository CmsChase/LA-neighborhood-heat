"""Prepare or run frozen 2020--2024 B1/M2 full-development model fitting."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_model import DEFAULT_FINAL_MODEL_CONFIG, build_final_models
from la_heat.final_model_process_lock import (
    FinalModelAlreadyRunning,
    exclusive_final_model_process,
)

PROCESS_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/interim/final_model_staging/.build_final_models.process.lock"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_FINAL_MODEL_CONFIG)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Authenticate development inputs and freeze the 65-task plan without fitting.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with exclusive_final_model_process(PROCESS_LOCK_PATH):
            payload = build_final_models(args.config, prepare_only=args.prepare_only)
    except FinalModelAlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 75
    print(
        json.dumps(
            {
                "state": payload["state"],
                "run_id": payload["run_id"],
                "final_test_year": payload["final_test_year"],
                "final_test_locked": payload["final_test_locked"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
