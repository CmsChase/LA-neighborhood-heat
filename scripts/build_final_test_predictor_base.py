"""Build authenticated static and calendar predictors on the blind 2025 keys."""

from __future__ import annotations

import json

from la_heat.final_test_predictor_base import (
    build_final_test_predictor_base_artifacts,
)


def main() -> None:
    payload = build_final_test_predictor_base_artifacts()
    print(
        json.dumps(
            {
                "state": payload["state"],
                "row_count": payload["row_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "feature_count": payload["feature_count"],
                "target_values_read": payload["target_values_read"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

