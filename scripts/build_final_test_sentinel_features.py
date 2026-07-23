"""Run the isolated, resumable 2025 Sentinel feature builder."""

from __future__ import annotations

import argparse
import json

from la_heat.final_test_sentinel_features import (
    build_final_test_sentinel_features,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(6, 8), default=6)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--research-config", default="configs/research.toml")
    parser.add_argument("--stage-config", default="configs/sentinel_features.toml")
    arguments = parser.parse_args()
    result = build_final_test_sentinel_features(
        research_config_path=arguments.research_config,
        stage_config_path=arguments.stage_config,
        workers=arguments.workers,
        max_attempts=arguments.max_attempts,
        force=arguments.force,
        compile_only=arguments.compile_only,
    )
    print(json.dumps(result, indent=2))
    if result["state"] == "incomplete_with_failures":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
