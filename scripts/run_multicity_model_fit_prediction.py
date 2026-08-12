"""Run or reauthenticate the authorized LA fit and three-city predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.model_fit_authorization import AUTHORIZATION_PATH
from la_heat.multicity.model_fit_prediction import (
    DEFAULT_STATUS_PATH,
    run_model_fit_prediction,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    result = run_model_fit_prediction(
        args.project_root,
        authorization_path=args.authorization,
        status_path=args.status,
        check_only=args.check_only,
    )
    print(
        json.dumps(
            {
                "state": result["state"],
                "commit_sha256": result["commit_sha256"],
                "external_prediction_rows": result["cohorts"]["external_prediction_rows"],
                "external_target_values_read": result["access_audit"][
                    "external_target_values_read"
                ],
                "next_safe_stage": result["next_safe_stage"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
