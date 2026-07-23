"""Download and audit the pinned Los Angeles NLCD 2016 WCS subsets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.nlcd_sources import download_nlcd_2016_sources


def _positive(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/static"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--connect-timeout", type=_positive, default=30.0)
    parser.add_argument("--read-timeout", type=_positive, default=240.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    marker = download_nlcd_2016_sources(
        args.output_dir,
        timeout=(args.connect_timeout, args.read_timeout),
        force=args.force,
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "state": payload["state"],
                "commit_marker": str(marker.resolve()),
                "commit_sha256": payload["commit_sha256"],
                "source_count": payload["source_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
