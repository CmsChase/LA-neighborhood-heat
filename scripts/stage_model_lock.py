"""Stage MODEL_LOCK requirements without generating the formal lock."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.model_lock_staging import stage_model_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-provenance", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/final_model.toml"))
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = stage_model_lock(
        args.build_provenance,
        config_path=args.config,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "ready_for_formal_model_lock": payload["ready_for_formal_model_lock"],
                "formal_model_lock_written": payload["formal_model_lock_written"],
                "blockers": payload["blockers"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
