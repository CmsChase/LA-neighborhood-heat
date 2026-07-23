"""Promote an eligible staging record to the immutable formal model lock."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.formal_model_lock import promote_formal_model_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staging",
        type=Path,
        default=Path("manifests/model_lock/MODEL_LOCK_STAGING.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/model_lock/MODEL_LOCK.json"),
    )
    parser.add_argument("--approve-formal-lock", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = promote_formal_model_lock(
        args.staging,
        output_path=args.output,
        approve_formal_lock=args.approve_formal_lock,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "formal_model_lock_written": payload["formal_model_lock_written"],
                "final_test_locked": payload["final_test_locked"],
                "one_time_final_evaluation_authorized": payload[
                    "one_time_final_evaluation_authorized"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
