"""Run the deterministic four-city synthetic smoke pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.synthetic_smoke import DEFAULT_SEED, run_synthetic_smoke

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate explicitly non-evidence model/evaluation smoke artifacts from "
            "deterministic in-memory data."
        )
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=ROOT / ".tmp" / "multicity_synthetic_smoke",
        help="Caller-owned output directory (canonical project trees are rejected).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_synthetic_smoke(ROOT, args.output_directory, seed=args.seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
