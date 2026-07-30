"""Run or authenticate the preregistered target-blind GSHHG L3 audit."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from la_heat.multicity.gshhg_l3_hierarchy_audit import (
    COMPLETE_STATE,
    DEFAULT_CONFIG,
    audit_gshhg_l3_hierarchy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate an existing append-only terminal without reopening data.",
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Suppress JSONL progress events; the terminal summary is still printed.",
    )
    return parser


def _print_progress(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_gshhg_l3_hierarchy(
        args.config,
        write=not args.check_only,
        progress=None if args.quiet_progress or args.check_only else _print_progress,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase": payload.get("phase"),
                "gate": payload.get("gate"),
                "source_frozen": payload.get("decision", {}).get(
                    "source_frozen",
                    False,
                ),
                "predictor_build_authorized": payload.get(
                    "decision",
                    payload.get("locks", {}),
                ).get("predictor_build_authorized", False),
                "next_safe_stage": payload.get("decision", {}).get("next_safe_stage"),
                "commit_sha256": payload["commit_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if payload["state"] == COMPLETE_STATE else 2


if __name__ == "__main__":
    raise SystemExit(main())
