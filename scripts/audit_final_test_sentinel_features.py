"""Authenticate completed target-blind 2025 Sentinel Collection 1 features."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_test_sentinel_audit import (
    DEFAULT_AUDIT_PATH,
    DEFAULT_DEVELOPMENT_CONTROL_DIRECTORY,
    DEFAULT_FORMAL_LOCK_PATH,
    DEFAULT_LANDSAT_INVENTORY_DIRECTORY,
    DEFAULT_LEGACY_CONTROL_DIRECTORY,
    DEFAULT_PREDICTOR_BASE_PATH,
    DEFAULT_PREDICTOR_BASE_PROVENANCE_PATH,
    DEFAULT_RAW_STAC_DIRECTORY,
    DEFAULT_RESEARCH_CONFIG_PATH,
    DEFAULT_SENTINEL_CONFIG_PATH,
    DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    DEFAULT_SENTINEL_OUTPUT_DIRECTORY,
    DEFAULT_STATIC_AUDIT_PATH,
    audit_final_test_sentinel_features,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sentinel-output-directory",
        type=Path,
        default=DEFAULT_SENTINEL_OUTPUT_DIRECTORY,
    )
    parser.add_argument(
        "--sentinel-inventory-directory",
        type=Path,
        default=DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    )
    parser.add_argument(
        "--raw-stac-directory", type=Path, default=DEFAULT_RAW_STAC_DIRECTORY
    )
    parser.add_argument(
        "--landsat-inventory-directory",
        type=Path,
        default=DEFAULT_LANDSAT_INVENTORY_DIRECTORY,
    )
    parser.add_argument("--formal-lock", type=Path, default=DEFAULT_FORMAL_LOCK_PATH)
    parser.add_argument(
        "--research-config", type=Path, default=DEFAULT_RESEARCH_CONFIG_PATH
    )
    parser.add_argument(
        "--sentinel-config", type=Path, default=DEFAULT_SENTINEL_CONFIG_PATH
    )
    parser.add_argument(
        "--predictor-base", type=Path, default=DEFAULT_PREDICTOR_BASE_PATH
    )
    parser.add_argument(
        "--predictor-base-provenance",
        type=Path,
        default=DEFAULT_PREDICTOR_BASE_PROVENANCE_PATH,
    )
    parser.add_argument(
        "--static-audit", type=Path, default=DEFAULT_STATIC_AUDIT_PATH
    )
    parser.add_argument(
        "--development-control-directory",
        type=Path,
        default=DEFAULT_DEVELOPMENT_CONTROL_DIRECTORY,
    )
    parser.add_argument(
        "--legacy-control-directory",
        type=Path,
        default=DEFAULT_LEGACY_CONTROL_DIRECTORY,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_AUDIT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = audit_final_test_sentinel_features(
        sentinel_output_directory=args.sentinel_output_directory,
        sentinel_inventory_directory=args.sentinel_inventory_directory,
        raw_stac_directory=args.raw_stac_directory,
        landsat_inventory_directory=args.landsat_inventory_directory,
        formal_lock_path=args.formal_lock,
        research_config_path=args.research_config,
        sentinel_config_path=args.sentinel_config,
        predictor_base_path=args.predictor_base,
        predictor_base_provenance_path=args.predictor_base_provenance,
        static_audit_path=args.static_audit,
        development_control_directory=args.development_control_directory,
        legacy_control_directory=args.legacy_control_directory,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "target_blind": payload["target_blind"],
                "safe_for_final_predictor_assembly": payload[
                    "safe_for_final_predictor_assembly"
                ],
                "calibration_classification": payload[
                    "calibration_classification"
                ]["classification"],
                "feature_available_row_count": payload["semantic_contract"][
                    "feature_available_row_count"
                ],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
