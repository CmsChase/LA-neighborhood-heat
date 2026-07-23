"""Promote frozen grouped-validation splits against committed development keys."""

from __future__ import annotations

import json

from la_heat.validation_split_promotion import promote_validation_splits


def main() -> int:
    payload = promote_validation_splits()
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase_complete": payload["phase_complete"],
                "ready_for_model_evaluation": payload[
                    "ready_for_model_evaluation"
                ],
                "row_count": payload["row_count"],
                "independent_date_count": payload["independent_date_count"],
                "spatial_block_count": payload["spatial_block_count"],
                "fold_counts": payload["fold_counts"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
