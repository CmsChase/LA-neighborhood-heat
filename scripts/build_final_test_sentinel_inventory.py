"""Build the authenticated, metadata-only 2025 Sentinel-2 inventory."""

from __future__ import annotations

import argparse
import json

from la_heat.final_test_sentinel_inventory import (
    DEFAULT_STAC_API,
    build_final_test_sentinel_inventory_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal-lock", default="manifests/model_lock/MODEL_LOCK.json"
    )
    parser.add_argument(
        "--landsat-inventory",
        default="manifests/final_test_2025/landsat_inventory",
    )
    parser.add_argument(
        "--output-directory",
        default="manifests/final_test_2025/sentinel_inventory",
    )
    parser.add_argument(
        "--raw-stac-directory",
        default="data/raw/final_test_2025/sentinel/stac_items",
    )
    parser.add_argument("--stac-api", default=DEFAULT_STAC_API)
    args = parser.parse_args()
    payload = build_final_test_sentinel_inventory_artifacts(
        formal_lock_path=args.formal_lock,
        landsat_inventory_directory=args.landsat_inventory,
        output_directory=args.output_directory,
        raw_stac_directory=args.raw_stac_directory,
        stac_api=args.stac_api,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "target_date_count": payload["target_date_count"],
                "selected_physical_acquisition_count": payload[
                    "selected_physical_acquisition_count"
                ],
                "selected_item_count": payload["selected_item_count"],
                "target_window_membership_count": payload[
                    "target_window_membership_count"
                ],
                "target_or_qa_values_read": payload["target_or_qa_values_read"],
                "fitted_models_loaded": payload["fitted_models_loaded"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
