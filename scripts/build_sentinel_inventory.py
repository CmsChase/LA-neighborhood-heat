"""Build the frozen Sentinel-2 inventory used by lagged optical features."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pystac_client import Client

from la_heat.sentinel_inventory import build_sentinel_inventory_artifacts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query all intersecting Sentinel-2 L2A items for the union of frozen "
            "target d-60:d-1 windows and commit a deterministic inventory."
        )
    )
    parser.add_argument(
        "--city-boundary",
        type=Path,
        default=PROJECT_ROOT / "manifests/target_inventory/city_boundary.geojson",
    )
    parser.add_argument(
        "--primary-overpass-manifest",
        type=Path,
        default=(
            PROJECT_ROOT
            / "manifests/target_inventory/primary_overpass_manifest.csv"
        ),
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=PROJECT_ROOT / "manifests/sentinel_inventory",
    )
    parser.add_argument(
        "--raw-stac-directory",
        type=Path,
        default=PROJECT_ROOT / "data/raw/sentinel/stac_items",
    )
    parser.add_argument("--stac-api", default=DEFAULT_STAC_API)
    parser.add_argument("--final-test-year", type=int, default=2025)
    parser.add_argument(
        "--unlock-final-test",
        action="store_true",
        help="Explicitly allow final-test-year target dates (disabled by default).",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    client = Client.open(args.stac_api)
    summary = build_sentinel_inventory_artifacts(
        city_boundary_path=args.city_boundary,
        primary_overpass_manifest_path=args.primary_overpass_manifest,
        output_directory=args.output_directory,
        raw_stac_directory=args.raw_stac_directory,
        client=client,
        unlock_final_test=args.unlock_final_test,
        final_test_year=args.final_test_year,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
