"""Run or authenticate the target-blind next-experiment city feasibility audit."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from la_heat.multicity.next_experiment_feasibility import run_feasibility_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/multicity/next_experiment_feasibility.toml"),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Authenticate the existing feasibility result without network access.",
    )
    return parser


def _city_summary(city_results: Any) -> list[dict[str, Any]]:
    if isinstance(city_results, Mapping):
        records = [dict(result, city_id=city_id) for city_id, result in city_results.items()]
    else:
        records = list(city_results)
    return [
        {
            "city_id": record.get("city_id", record.get("id")),
            "tier": record.get("tier"),
            "eligible": record.get("eligible", record.get("passes")),
            "unique_physical_dates": record.get(
                "unique_physical_dates",
                record.get(
                    "eligible_unique_physical_date_count",
                    record.get("landsat_unique_physical_dates"),
                ),
            ),
        }
        for record in records
    ]


def _selection_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    selection = result.get("selection", {})
    if not isinstance(selection, Mapping):
        selection = {}
    return {
        "decision": result.get("decision", selection.get("decision")),
        "selected_test_city_ids": result.get(
            "selected_test_city_ids",
            selection.get(
                "selected_test_city_ids",
                selection.get("selected_city_ids", selection.get("city_ids", [])),
            ),
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_feasibility_audit(
        args.project_root.resolve(),
        args.config,
        check_only=args.check_only,
    )
    selection = _selection_summary(result)
    city_results = result.get("city_results", result.get("cities", []))
    print(
        json.dumps(
            {
                "state": result["state"],
                **selection,
                "cities": _city_summary(city_results),
                "target_blind": True,
                "label_free": True,
                "commit_sha256": result["commit_sha256"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
