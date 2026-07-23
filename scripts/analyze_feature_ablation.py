"""Analyze authenticated reduced-feature-set refits after compilation completes."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from la_heat.feature_ablation_analysis import DEFAULT_CONFIG, analyze_feature_ablation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-directory", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_feature_ablation(
        args.config,
        output_directory=args.output_directory,
    )
    print(
        json.dumps(
            {
                "state": payload["state"],
                "analysis_scope": payload["analysis_scope"],
                "feature_ablation_run_id": payload["feature_ablation_run_id"],
                "canonical_model_run_id": payload["canonical_model_run_id"],
                "final_test_locked": payload["final_test_locked"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
