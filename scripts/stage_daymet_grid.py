"""Discover or authenticated-download official Daymet V4 R1 LA subsets."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from la_heat.config import load_config
from la_heat.daymet_grid import (
    DAYMET_CMR_COLLECTION_ID,
    DAYMET_CMR_GRANULES_URL,
    DAYMET_CMR_SERVICE_BRIDGE_URL,
    DAYMET_DIRECT_DAP4_ROUTE,
    DAYMET_DOI_URL,
    authenticated_netcdf_download,
    build_daymet_direct_subset_url,
    discover_daymet_v4r1_granules,
    inspect_daymet_netcdf,
    load_earthdata_bearer_token,
    prompt_earthdata_bearer_token,
    validate_daymet_direct_subset_spec,
    validate_daymet_netcdf_grid_specs,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_frame_sha256,
    sha256_file,
)
from la_heat.stage_config import daymet_grid_config_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    parser.add_argument(
        "--download-subsets",
        action="store_true",
        help="Use an Earthdata token to download all LA bbox NetCDF subsets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing subset files after a fresh authenticated request.",
    )
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help=(
            "Read the Earthdata bearer token from a hidden terminal prompt instead "
            "of an environment variable; requires --download-subsets."
        ),
    )
    return parser


def _validated_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.prompt_token and not args.download_subsets:
        parser.error("--prompt-token requires --download-subsets")
    return args


def _cached_record(path: Path, source_url: str) -> dict[str, object]:
    if path.stat().st_size <= 8:
        raise ValueError(f"Cached Daymet subset is empty: {path}")
    with path.open("rb") as handle:
        prefix = handle.read(8)
    if not (prefix.startswith(b"CDF") or prefix.startswith(b"\x89HDF\r\n\x1a\n")):
        raise ValueError(f"Cached Daymet subset is not NetCDF: {path}")
    return {
        "path": path.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "source_url": source_url,
        "retrieved_on": None,
        "credential_source": "not_reloaded_from_cache",
    }


def main() -> None:
    args = _validated_args()
    config = load_config(args.config)
    study = config.raw["study"]
    weather = config.raw["weather_features"]
    credential = None
    if args.download_subsets:
        token_environment_variables = tuple(weather["token_environment_variables"])
        if args.prompt_token:
            credential = prompt_earthdata_bearer_token(
                variable_names=token_environment_variables
            )
        else:
            credential = load_earthdata_bearer_token(
                variable_names=token_environment_variables
            )
    first_year = pd.Timestamp(study["start_date"]).year
    last_year = pd.Timestamp(study["development_end_date"]).year
    years = tuple(range(first_year, last_year + 1))
    variables = tuple(weather["variables"])
    access_route = str(weather["subset_access_route"])
    if access_route != DAYMET_DIRECT_DAP4_ROUTE:
        raise ValueError(
            "Daymet subset_access_route must equal the frozen direct DAP4 route."
        )
    y_indices = tuple(weather["direct_subset_y_indices"])
    x_indices = tuple(weather["direct_subset_x_indices"])
    granules = discover_daymet_v4r1_granules(
        years=years,
        variables=variables,
        final_test_year=config.final_test_year,
        endpoint=weather["cmr_granules_url"],
    )
    inventory = pd.DataFrame([asdict(granule) for granule in granules])
    manifest_directory = Path(weather["manifest_directory"])
    inventory_path = manifest_directory / "granule_inventory.csv"
    summary_path = manifest_directory / "inventory_summary.json"
    atomic_csv(inventory, inventory_path)
    inventory_semantic_sha256 = canonical_frame_sha256(
        inventory,
        sort_by=["year", "variable"],
    )
    summary: dict[str, object] = {
        "state": "inventory_complete",
        "queried_at_utc": datetime.now(UTC).isoformat(),
        "dataset": "Daymet: Daily Surface Weather Data on a 1-km Grid for North America, V4 R1",
        "dataset_doi": DAYMET_DOI_URL,
        "cmr_collection_concept_id": DAYMET_CMR_COLLECTION_ID,
        "cmr_granules_url": DAYMET_CMR_GRANULES_URL,
        "cmr_service_bridge_url": DAYMET_CMR_SERVICE_BRIDGE_URL,
        "subset_access_route": access_route,
        "direct_subset_y_indices": list(y_indices),
        "direct_subset_x_indices": list(x_indices),
        "years": list(years),
        "variables": list(variables),
        "region": "na",
        "bbox_wgs84": list(study["bbox_wgs84"]),
        "final_test_year": config.final_test_year,
        "final_test_unlocked": config.final_test_unlocked,
        "contains_final_test_year": False,
        "daymet_grid_config_sha256": daymet_grid_config_sha256(config),
        "granule_count": len(inventory),
        "inventory_semantic_sha256": inventory_semantic_sha256,
        "inventory_file_sha256": sha256_file(inventory_path),
        "inventory_path": inventory_path.as_posix(),
        "access_note": (
            "CMR discovery is public. Fixed-index DAP4 spatial subsetting and NetCDF "
            "download require an Earthdata bearer token. Token values are never "
            "persisted."
        ),
    }
    atomic_json(summary, summary_path)
    if not args.download_subsets:
        print(
            f"Audited {len(inventory)} Daymet V4 R1 granules; "
            f"inventory SHA-256 {inventory_semantic_sha256}."
        )
        return

    if credential is None:
        raise AssertionError("Authenticated Daymet download lacks a credential.")
    raw_directory = Path(weather["raw_subset_directory"])
    download_records: list[dict[str, object]] = []
    subset_specs = []
    for position, granule in enumerate(granules, start=1):
        print(
            f"Daymet [{position}/{len(granules)}] preparing direct DAP4 "
            f"{granule.variable} {granule.year} subset...",
            flush=True,
        )
        subset_url = build_daymet_direct_subset_url(
            granule,
            y_indices=y_indices,
            x_indices=x_indices,
        )
        destination = raw_directory / (
            f"daymet_v4r1_daily_na_{granule.variable}_{granule.year}_la_subset.nc"
        )
        if destination.exists() and not args.force:
            file_record = _cached_record(destination, subset_url)
            action = "verified cached"
        else:
            file_record = authenticated_netcdf_download(
                subset_url,
                destination,
                credential=credential,
                maximum_bytes=int(weather["maximum_subset_bytes"]),
            )
            action = "downloaded"
        subset_specs.append(
            validate_daymet_direct_subset_spec(
                inspect_daymet_netcdf(
                    destination,
                    variable=granule.variable,
                    year=granule.year,
                    final_test_year=config.final_test_year,
                ),
                y_indices=y_indices,
                x_indices=x_indices,
                bbox_wgs84=study["bbox_wgs84"],
            )
        )
        print(
            f"Daymet [{position}/{len(granules)}] {action} and validated "
            f"{int(file_record['bytes']):,} bytes.",
            flush=True,
        )
        download_records.append(
            {
                "concept_id": granule.concept_id,
                "variable": granule.variable,
                "year": granule.year,
                "access_route": access_route,
                "subset_y_start": y_indices[0],
                "subset_y_stop": y_indices[1],
                "subset_x_start": x_indices[0],
                "subset_x_stop": x_indices[1],
                **file_record,
            }
        )
    reference_spec = validate_daymet_netcdf_grid_specs(subset_specs)
    downloads = pd.DataFrame(download_records)
    downloads_path = manifest_directory / "subset_downloads.csv"
    atomic_csv(downloads, downloads_path)
    summary.update(
        {
            "state": "subsets_complete",
            "subset_count": len(downloads),
            "subset_grid_shape": list(reference_spec.shape),
            "download_manifest_path": downloads_path.as_posix(),
            "download_manifest_sha256": sha256_file(downloads_path),
            "download_semantic_sha256": canonical_frame_sha256(
                downloads,
                sort_by=["year", "variable"],
            ),
            "credential_source": credential.source_environment_variable,
        }
    )
    atomic_json(summary, summary_path)
    print(f"Downloaded or verified {len(downloads)} authenticated Daymet subsets.")


if __name__ == "__main__":
    main()
