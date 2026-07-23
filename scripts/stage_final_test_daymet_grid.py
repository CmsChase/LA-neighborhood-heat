"""Freeze or resume the target-blind 2025 Daymet grid/subset stage."""

from __future__ import annotations

import argparse
import json

from la_heat.config import load_config
from la_heat.daymet_grid import (
    load_earthdata_bearer_token,
    prompt_earthdata_bearer_token,
)
from la_heat.final_test_daymet_grid import stage_final_test_daymet_grid


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    parser.add_argument(
        "--download-subsets",
        action="store_true",
        help="Download or resume the exact key-derived annual LA subsets.",
    )
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help=(
            "Read an Earthdata bearer token from a hidden prompt if an unfinished "
            "subset actually needs downloading; requires --download-subsets."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.prompt_token and not args.download_subsets:
        parser.error("--prompt-token requires --download-subsets")

    config = load_config(args.config)
    token_variables = tuple(
        config.raw["weather_features"]["token_environment_variables"]
    )

    def credential_provider():
        if args.prompt_token:
            return prompt_earthdata_bearer_token(variable_names=token_variables)
        return load_earthdata_bearer_token(variable_names=token_variables)

    result = stage_final_test_daymet_grid(
        config_path=args.config,
        download_subsets=args.download_subsets,
        credential_provider=(credential_provider if args.download_subsets else None),
    )
    print(
        json.dumps(
            {
                "state": result["state"],
                "target_blind": result["target_blind"],
                "target_date_count": result["target_date_count"],
                "required_weather_date_count": result[
                    "required_weather_date_count"
                ],
                "source_years": result["source_years"],
                "completed_subset_count": result["completed_subset_count"],
                "expected_subset_count": result["expected_subset_count"],
                "commit_sha256": result["commit_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

