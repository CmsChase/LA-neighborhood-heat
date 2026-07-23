"""Authenticate and reconcile the complete development robustness package."""

from __future__ import annotations

import argparse

from la_heat.robustness_reconciliation import reconcile_development_robustness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/robustness_reconciliation.toml",
    )
    arguments = parser.parse_args()
    provenance = reconcile_development_robustness(arguments.config)
    print(provenance["commit_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
