"""Target-independent 5 km spatial blocks for four-city evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import pandas as pd

from la_heat.boundaries import assign_spatial_blocks
from la_heat.multicity.portable_predictor_components import (
    CITY_IDS,
    load_city_support,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
)

ALGORITHM_VERSION: Final = "multicity-5km-spatial-blocks-v1"
BLOCK_SIZE_KM: Final = 5.0
SPATIAL_CRS_EPSG: Final = 5070
OUTPUT_ROOT: Final = Path("data/processed/multicity/evaluation_support/spatial_blocks")
COMBINED_OUTPUT: Final = OUTPUT_ROOT / "tract_spatial_blocks.parquet"
MANIFEST_PATH: Final = Path(
    "manifests/multicity/evaluation/SPATIAL_BLOCKS.json"
)
EXPECTED_TRACTS: Final = {
    "los_angeles_ca": 1_096,
    "phoenix_az": 375,
    "houston_tx": 651,
    "chicago_il": 780,
}
OUTPUT_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "spatial_block",
    "local_spatial_block",
    "longitude_quartile",
    "latitude_quartile",
)


class MulticitySpatialBlockError(RuntimeError):
    """Raised when target-independent spatial grouping is inconsistent."""


def assign_city_spatial_blocks(
    city_id: str,
    tracts: gpd.GeoDataFrame,
    *,
    block_size_km: float = BLOCK_SIZE_KM,
) -> pd.DataFrame:
    """Assign globally unique block IDs using only projected tract geometry."""

    if city_id not in CITY_IDS:
        raise MulticitySpatialBlockError(f"Unknown city: {city_id}")
    if (
        "tract_geoid" not in tracts
        or tracts.crs is None
        or tracts.crs.to_epsg() != SPATIAL_CRS_EPSG
    ):
        raise MulticitySpatialBlockError("Canonical EPSG:5070 tracts are required.")
    if block_size_km != BLOCK_SIZE_KM:
        raise MulticitySpatialBlockError("The frozen spatial block size is exactly 5 km.")
    source = tracts.loc[:, ["tract_geoid", tracts.geometry.name]].copy()
    source["tract_geoid"] = source["tract_geoid"].astype("string")
    valid_geoids = source["tract_geoid"].map(
        lambda value: isinstance(value, str) and bool(value.strip()) and value == value.strip()
    )
    if (
        source.empty
        or source["tract_geoid"].duplicated().any()
        or not bool(valid_geoids.all())
        or source.geometry.isna().any()
        or source.geometry.is_empty.any()
        or not bool(source.geometry.is_valid.all())
    ):
        raise MulticitySpatialBlockError("Canonical tract GEOIDs are empty or duplicated.")
    source["GEOID"] = source["tract_geoid"]
    blocked = assign_spatial_blocks(source, block_size_km=block_size_km)
    result = pd.DataFrame(
        {
            "city_id": city_id,
            "tract_geoid": blocked["tract_geoid"].astype("string"),
            "local_spatial_block": blocked["spatial_block"].astype("string"),
            "longitude_quartile": blocked["longitude_quartile"].astype("int8"),
            "latitude_quartile": blocked["latitude_quartile"].astype("int8"),
        }
    )
    result["spatial_block"] = (
        result["city_id"].astype("string")
        + "__"
        + result["local_spatial_block"].astype("string")
    )
    result = result.loc[:, OUTPUT_COLUMNS].sort_values(
        ["city_id", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    if not bool(
        result["spatial_block"]
        .str.match(rf"^{city_id}__x[+-]\d{{4}}_y[+-]\d{{4}}$")
        .all()
    ):
        raise MulticitySpatialBlockError("Noncanonical spatial block ID generated.")
    return result


def _semantic_sha(frame: pd.DataFrame) -> str:
    if tuple(frame.columns) != OUTPUT_COLUMNS:
        raise MulticitySpatialBlockError("Spatial-block table schema changed.")
    return canonical_frame_sha256(
        frame,
        sort_by=["city_id", "tract_geoid"],
        columns=list(OUTPUT_COLUMNS),
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MulticitySpatialBlockError(f"Cannot read spatial-block manifest: {path}") from error
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise MulticitySpatialBlockError("Spatial-block manifest commit is invalid.")
    return payload


def build_multicity_spatial_blocks(
    project_root: str | Path,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Build or authenticate the frozen geometry-only four-city block table."""

    root = Path(project_root).resolve()
    frames: list[pd.DataFrame] = []
    source_commits: dict[str, dict[str, str]] = {}
    city_summaries: dict[str, dict[str, object]] = {}
    for city_id in CITY_IDS:
        support = load_city_support(root, city_id)
        frame = assign_city_spatial_blocks(city_id, support.tracts)
        if len(frame) != EXPECTED_TRACTS[city_id]:
            raise MulticitySpatialBlockError(f"Canonical tract count changed for {city_id}.")
        frames.append(frame)
        source_commits[city_id] = {
            "geography_commit_sha256": str(support.geography_manifest["commit_sha256"]),
            "worldcover_commit_sha256": str(support.worldcover_manifest["commit_sha256"]),
        }
        city_summaries[city_id] = {
            "tract_count": len(frame),
            "spatial_block_count": int(frame["spatial_block"].nunique()),
            "semantic_sha256": _semantic_sha(frame),
        }

    combined = pd.concat(frames, ignore_index=True).sort_values(
        ["city_id", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    if len(combined) != sum(EXPECTED_TRACTS.values()) or combined.duplicated(
        ["city_id", "tract_geoid"]
    ).any():
        raise MulticitySpatialBlockError("Combined four-city tract universe changed.")
    combined_semantic = _semantic_sha(combined)

    if check_only:
        manifest = _read_manifest(root / MANIFEST_PATH)
        output = manifest.get("output")
        if (
            manifest.get("state") != "complete_target_blind_spatial_blocks"
            or manifest.get("algorithm_version") != ALGORITHM_VERSION
            or manifest.get("block_size_km") != BLOCK_SIZE_KM
            or manifest.get("spatial_crs") != "EPSG:5070"
            or manifest.get("cities") != city_summaries
            or manifest.get("source_commits") != source_commits
            or not isinstance(output, dict)
            or output.get("path") != COMBINED_OUTPUT.as_posix()
            or output.get("semantic_sha256") != combined_semantic
            or not (root / COMBINED_OUTPUT).is_file()
        ):
            raise MulticitySpatialBlockError("Spatial-block output no longer matches its lock.")
        observed = pd.read_parquet(root / COMBINED_OUTPUT)
        observed_record = parquet_file_record(root / COMBINED_OUTPUT, observed)
        if (
            _semantic_sha(observed) != combined_semantic
            or any(output.get(key) != value for key, value in observed_record.items())
        ):
            raise MulticitySpatialBlockError("Spatial-block table contents changed.")
        return manifest

    output_path = root / COMBINED_OUTPUT
    atomic_parquet(combined, output_path)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_spatial_blocks",
        "block_size_km": BLOCK_SIZE_KM,
        "spatial_crs": "EPSG:5070",
        "grid_origin_m": [0.0, 0.0],
        "cell_interval": "half_open_floor_index",
        "assignment": "floor_projected_tract_centroid_divided_by_5000m",
        "city_prefix_prevents_cross_city_block_id_collision": True,
        "continuation_specific_partition": True,
        "phase1_spatial_block_labels_or_assignments_reused": False,
        "quartile_columns_are_metadata_only_forbidden_model_inputs": True,
        "source_commits": source_commits,
        "cities": city_summaries,
        "combined_tract_count": len(combined),
        "combined_spatial_block_count": int(combined["spatial_block"].nunique()),
        "output": {
            "path": COMBINED_OUTPUT.as_posix(),
            **parquet_file_record(output_path, combined),
            "semantic_sha256": combined_semantic,
        },
        "access_contract": {
            "public_geometry_read": True,
            "predictor_values_read": False,
            "target_or_qa_values_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "wait_for_sentinel_return_import_then_protocol_lock",
    }
    manifest["commit_sha256"] = canonical_sha256(manifest)
    atomic_json(manifest, root / MANIFEST_PATH)
    return manifest
