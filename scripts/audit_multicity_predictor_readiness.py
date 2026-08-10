"""Audit the completed target-blind four-city predictor table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.predictor_readiness import (
    audit_multicity_predictor_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    report = audit_multicity_predictor_readiness(
        args.project_root,
        write_report=args.write_report,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
