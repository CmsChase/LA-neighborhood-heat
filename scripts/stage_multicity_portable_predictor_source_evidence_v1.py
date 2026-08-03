"""Stage or authenticate the preregistered multicity source-evidence snapshot."""

from __future__ import annotations

import argparse

from la_heat.multicity.portable_predictor_source_evidence_v1 import (
    CONFIG_PATH,
    stage_portable_predictor_source_evidence_v1,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()
    payload = stage_portable_predictor_source_evidence_v1(
        args.config,
        check_only=args.check_only,
        timeout_seconds=args.timeout_seconds,
    )
    print(payload["state"])
    print(payload["publication_status"])
    print(payload["commit_sha256"])


if __name__ == "__main__":
    main()
