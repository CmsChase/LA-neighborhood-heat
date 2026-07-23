"""Download and audit the pinned raw static-source snapshot.

The command writes three raw files under ``data/raw/static`` by default and
promotes ``static_sources_provenance.json`` only after every byte-level,
format-level, and cross-tile seam check passes.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.static_sources import download_static_sources


def _positive_timeout(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("timeout values must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically download, hash, and validate the pinned SRTM and "
            "Census coastline sources."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/static"),
        help="raw destination directory (default: data/raw/static)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="redownload even when every cached file passes a fresh audit",
    )
    parser.add_argument(
        "--connect-timeout",
        type=_positive_timeout,
        default=30.0,
        help="HTTP connection timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--read-timeout",
        type=_positive_timeout,
        default=240.0,
        help="HTTP read timeout in seconds (default: 240)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    marker = download_static_sources(
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
                "files": {
                    source_id: record["path"]
                    for source_id, record in payload["sources"].items()
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

