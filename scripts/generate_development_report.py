"""Generate the authenticated, development-only scientific report."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.development_report import DEFAULT_CONFIG, generate_development_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provenance = generate_development_report(args.config)
    print(
        json.dumps(
            {
                "state": provenance["state"],
                "analysis_scope": provenance["analysis_scope"],
                "report_sha256": provenance["output_files"]["DEVELOPMENT_REPORT.md"]["sha256"],
                "commit_sha256": provenance["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
