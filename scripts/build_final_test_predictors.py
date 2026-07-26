"""Assemble authenticated target-blind 2025 predictors in frozen M2 order."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.final_test_predictor_assembler import (
    DEFAULT_BASE_PATH,
    DEFAULT_BASE_PROVENANCE_PATH,
    DEFAULT_DAYMET_PATH,
    DEFAULT_DAYMET_PROVENANCE_PATH,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_PROVENANCE_PATH,
    DEFAULT_RESEARCH_CONFIG_PATH,
    DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    DEFAULT_SENTINEL_PATH,
    DEFAULT_SENTINEL_PIPELINE_PATH,
    DEFAULT_SENTINEL_PROGRESS_PATH,
    DEFAULT_SENTINEL_RAW_STAC_DIRECTORY,
    DEFAULT_SENTINEL_STAGE_CONFIG_PATH,
    build_final_test_predictor_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal-lock",
        type=Path,
        default=Path("manifests/model_lock/MODEL_LOCK.json"),
    )
    parser.add_argument("--predictor-base", type=Path, default=DEFAULT_BASE_PATH)
    parser.add_argument(
        "--predictor-base-provenance",
        type=Path,
        default=DEFAULT_BASE_PROVENANCE_PATH,
    )
    parser.add_argument("--daymet-features", type=Path, default=DEFAULT_DAYMET_PATH)
    parser.add_argument(
        "--daymet-provenance",
        type=Path,
        default=DEFAULT_DAYMET_PROVENANCE_PATH,
    )
    parser.add_argument("--sentinel-features", type=Path, default=DEFAULT_SENTINEL_PATH)
    parser.add_argument(
        "--sentinel-progress",
        type=Path,
        default=DEFAULT_SENTINEL_PROGRESS_PATH,
    )
    parser.add_argument(
        "--sentinel-pipeline",
        type=Path,
        default=DEFAULT_SENTINEL_PIPELINE_PATH,
    )
    parser.add_argument(
        "--research-config", type=Path, default=DEFAULT_RESEARCH_CONFIG_PATH
    )
    parser.add_argument(
        "--sentinel-stage-config",
        type=Path,
        default=DEFAULT_SENTINEL_STAGE_CONFIG_PATH,
    )
    parser.add_argument(
        "--sentinel-inventory-directory",
        type=Path,
        default=DEFAULT_SENTINEL_INVENTORY_DIRECTORY,
    )
    parser.add_argument(
        "--sentinel-raw-stac-directory",
        type=Path,
        default=DEFAULT_SENTINEL_RAW_STAC_DIRECTORY,
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_final_test_predictor_artifacts(
        formal_lock_path=args.formal_lock,
        predictor_base_path=args.predictor_base,
        predictor_base_provenance_path=args.predictor_base_provenance,
        daymet_feature_path=args.daymet_features,
        daymet_provenance_path=args.daymet_provenance,
        sentinel_feature_path=args.sentinel_features,
        sentinel_progress_path=args.sentinel_progress,
        sentinel_pipeline_path=args.sentinel_pipeline,
        research_config_path=args.research_config,
        sentinel_stage_config_path=args.sentinel_stage_config,
        sentinel_inventory_directory=args.sentinel_inventory_directory,
        sentinel_raw_stac_directory=args.sentinel_raw_stac_directory,
        output_directory=args.output_directory,
        provenance_path=args.provenance,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "target_blind": payload["target_blind"],
                "row_count": payload["row_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "feature_count": payload["feature_count"],
                "sentinel_missing_row_count": payload["sentinel_missing_row_count"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
