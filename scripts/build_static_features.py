"""Build the frozen one-row-per-GEOID static feature table."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.static_features import build_static_feature_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research.toml"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_static_feature_table(args.config)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "row_count": payload["row_count"],
                "model_feature_count": payload["model_feature_count"],
                "minimum_observed_coverage_by_source": payload[
                    "minimum_observed_coverage_by_source"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
