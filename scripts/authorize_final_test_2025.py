"""Preflight or explicitly authorize the one-time locked 2025 evaluation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_test_authorization import (
    DEFAULT_AUTHORIZATION_PATH,
    DEFAULT_EVALUATION_READINESS_PATH,
    DEFAULT_MODEL_LOCK_PATH,
    authorize_final_test_2025,
    preflight_final_test_2025,
)

DEFAULT_EVALUATOR_MODULE = Path("src/la_heat/final_evaluation_protocol.py")
DEFAULT_EVALUATOR_CONFIG = Path("configs/final_evaluation_2025.toml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluator-module",
        type=Path,
        default=DEFAULT_EVALUATOR_MODULE,
    )
    parser.add_argument(
        "--evaluator-config",
        type=Path,
        default=DEFAULT_EVALUATOR_CONFIG,
    )
    parser.add_argument("--model-lock", type=Path, default=DEFAULT_MODEL_LOCK_PATH)
    parser.add_argument(
        "--evaluation-readiness",
        type=Path,
        default=DEFAULT_EVALUATION_READINESS_PATH,
    )
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION_PATH)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--approve-one-time-2025", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common = {
        "evaluator_module": args.evaluator_module,
        "evaluator_config": args.evaluator_config,
        "model_lock_path": args.model_lock,
        "readiness_path": args.evaluation_readiness,
        "authorization_path": args.authorization,
    }
    if args.preflight_only:
        payload = preflight_final_test_2025(**common)
    else:
        payload = authorize_final_test_2025(
            **common,
            approve_one_time_2025=args.approve_one_time_2025,
        )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "final_test_year": payload["final_test_year"],
                "authorized": payload["authorized"],
                "values_read": payload["values_read"],
                "evaluator_code_git_commit": payload["evaluator_code_git_commit"],
                "commit_sha256": payload.get("commit_sha256"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
