"""Analyze the isolated strict pixel-level ST_QA <= 2 K target rebuild."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from la_heat.stqa2_sensitivity_analysis import analyze_stqa2_sensitivity


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stqa2_sensitivity_analysis.toml")
    args = parser.parse_args(argv)
    result = analyze_stqa2_sensitivity(config_path=args.config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
