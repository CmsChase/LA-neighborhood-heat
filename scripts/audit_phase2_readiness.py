"""Audit target-blind Phase 2 predictor readiness without reading Landsat labels."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.config import load_config
from la_heat.phase2_readiness import (
    DEFAULT_DAYMET_FEATURE_PATH,
    DEFAULT_DAYMET_PROVENANCE_PATH,
    DEFAULT_OUTPUT_DIRECTORY,
    audit_phase2_readiness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research.toml"))
    parser.add_argument("--daymet-features", type=Path, default=DEFAULT_DAYMET_FEATURE_PATH)
    parser.add_argument(
        "--daymet-provenance", type=Path, default=DEFAULT_DAYMET_PROVENANCE_PATH
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    payload = audit_phase2_readiness(
        daymet_feature_path=args.daymet_features,
        daymet_provenance_path=args.daymet_provenance,
        output_directory=args.output_directory,
        final_test_year=config.final_test_year,
        unlock_final_test=config.final_test_unlocked,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "phase2_complete": payload["phase2_complete"],
                "ready_for_feature_assembly": payload["ready_for_feature_assembly"],
                "blockers": payload["blockers"],
                "key_count": payload["key_count"],
                "date_count": payload["date_count"],
                "tract_count": payload["tract_count"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
