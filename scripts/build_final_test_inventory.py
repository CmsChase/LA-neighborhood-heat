"""Freeze the target-blind 2025 Landsat inventory and tract-date keys."""

from __future__ import annotations

import argparse
import json

from la_heat.final_test_inventory import (
    USGS_STAC_API,
    USGS_SURFACE_TEMPERATURE_COLLECTION,
    build_final_test_inventory_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    parser.add_argument(
        "--formal-lock", default="manifests/model_lock/MODEL_LOCK.json"
    )
    parser.add_argument(
        "--output-directory",
        default="manifests/final_test_2025/landsat_inventory",
    )
    parser.add_argument("--stac-api", default=USGS_STAC_API)
    parser.add_argument(
        "--stac-collection", default=USGS_SURFACE_TEMPERATURE_COLLECTION
    )
    args = parser.parse_args()
    payload = build_final_test_inventory_artifacts(
        config_path=args.config,
        formal_lock_path=args.formal_lock,
        output_directory=args.output_directory,
        stac_api=args.stac_api,
        stac_collection=args.stac_collection,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "scene_count": payload["scene_count"],
                "physical_overpass_count": payload["physical_overpass_count"],
                "primary_overpass_count": payload["primary_overpass_count"],
                "tract_count": payload["tract_count"],
                "key_count": payload["key_count"],
                "target_or_qa_values_read": payload["target_or_qa_values_read"],
                "one_time_evaluation_consumed": payload[
                    "one_time_evaluation_consumed"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
