"""Freeze target-independent 5 km blocks for the four M3 blind cities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.boundaries import assign_spatial_blocks
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_prediction_v2 as prediction
from la_heat.multicity.m3_blind_predictor_sentinel_static_runtime_v1 import _city_support
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-5km-spatial-blocks-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_SPATIAL_BLOCKS_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/M3_BLIND_SPATIAL_BLOCKS_COMPLETE.json"
)
OUTPUT_PATH: Final = Path(
    "data/processed/multicity/m3_blind_evaluation_v1/tract_spatial_blocks.parquet"
)
PREDICTION_COMMIT: Final = "295ccca0ea0239cf6eb5633b736a7565abbac3cc47992bae6bcbc9ba4d6f74ac"
EXPECTED_TRACTS: Final = {
    "seattle_wa": 177,
    "denver_co": 175,
    "atlanta_ga": 173,
    "miami_fl": 128,
}
OUTPUT_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "spatial_block",
    "local_spatial_block",
    "longitude_quartile",
    "latitude_quartile",
)
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_spatial_blocks_v1.py",
    "scripts/authorize_m3_blind_spatial_blocks_v1.py",
    "scripts/run_m3_blind_spatial_blocks_v1.py",
)


class M3BlindSpatialBlockError(RuntimeError):
    """Raised when blind-city spatial-block provenance changes."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindSpatialBlockError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindSpatialBlockError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    predictions = _read(helpers._inside(root, prediction.COMPLETION_PATH))
    if predictions.get("commit_sha256") != PREDICTION_COMMIT:
        raise M3BlindSpatialBlockError("Prediction-before-target anchor changed.")
    support = _read(
        helpers._inside(
            root,
            "manifests/multicity/next_experiment/blind_predictor_build_v1/"
            "M3_BLIND_PREDICTOR_SUPPORT_COMPLETE.json",
        )
    )
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_spatial_blocks_authorized",
            "prediction_completion_commit_sha256": PREDICTION_COMMIT,
            "support_completion_commit_sha256": support["commit_sha256"],
            "city_ids": list(helpers.BLIND_CITY_IDS),
            "tract_counts": EXPECTED_TRACTS,
            "block_size_km": 5.0,
            "spatial_crs": "EPSG:5070",
            "code": code,
            "permissions": {
                "read_public_tract_geometry": True,
                "write_target_independent_spatial_blocks": True,
                "read_landsat_hrefs_thermal_qa_or_targets": False,
                "fit_predict_score_or_evaluate": False,
                "network_or_href_reads": False,
            },
            "next_safe_stage": "freeze_blind_spatial_blocks_before_target_authorization",
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_authorization(root)
    helpers._write_json_exclusive(payload, helpers._inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(helpers._inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindSpatialBlockError("Spatial-block authorization drifted.")
    return observed


def _city_blocks(city_id: str, tracts: Any) -> pd.DataFrame:
    source = tracts.loc[:, ["tract_geoid", tracts.geometry.name]].copy()
    source["GEOID"] = source["tract_geoid"].astype("string")
    blocked = assign_spatial_blocks(source, block_size_km=5.0)
    result = pd.DataFrame(
        {
            "city_id": city_id,
            "tract_geoid": blocked["tract_geoid"].astype("string"),
            "local_spatial_block": blocked["spatial_block"].astype("string"),
            "longitude_quartile": blocked["longitude_quartile"].astype("int8"),
            "latitude_quartile": blocked["latitude_quartile"].astype("int8"),
        }
    )
    result["spatial_block"] = city_id + "__" + result["local_spatial_block"]
    return result.loc[:, OUTPUT_COLUMNS].sort_values("tract_geoid").reset_index(drop=True)


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    destination = helpers._inside(root, COMPLETION_PATH)
    if destination.exists():
        return authenticate_completion(root)
    frames = []
    cities = {}
    for city_id in helpers.BLIND_CITY_IDS:
        support = _city_support(root, city_id)
        frame = _city_blocks(city_id, support.tracts)
        if len(frame) != EXPECTED_TRACTS[city_id]:
            raise M3BlindSpatialBlockError(f"{city_id} tract count changed.")
        frames.append(frame)
        cities[city_id] = {
            "tract_count": len(frame),
            "spatial_block_count": int(frame["spatial_block"].nunique()),
            "semantic_sha256": canonical_frame_sha256(frame, sort_by=["city_id", "tract_geoid"]),
        }
    combined = pd.concat(frames, ignore_index=True)
    output = helpers._inside(root, OUTPUT_PATH)
    helpers._write_parquet_exclusive(combined, output)
    record = helpers._record(root, output, rows=len(combined))
    record["semantic_sha256"] = canonical_frame_sha256(combined, sort_by=["city_id", "tract_geoid"])
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_spatial_blocks_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "prediction_completion_commit_sha256": PREDICTION_COMMIT,
            "block_size_km": 5.0,
            "cities": cities,
            "output": record,
            "audit": {
                "target_or_qa_values_read": False,
                "network_or_href_reads": 0,
                "prediction_before_target_boundary_preserved": True,
            },
            "next_safe_stage": "authorize_one_combined_four_city_target_claim",
        }
    )
    helpers._write_json_exclusive(completion, destination)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    record = payload.get("output", {})
    path = helpers._inside(root, record.get("path", ""))
    frame = pd.read_parquet(path)
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or tuple(frame.columns) != OUTPUT_COLUMNS
        or len(frame) != sum(EXPECTED_TRACTS.values())
        or not helpers._matches(root, record, OUTPUT_PATH)
        or canonical_frame_sha256(frame, sort_by=["city_id", "tract_geoid"])
        != record.get("semantic_sha256")
    ):
        raise M3BlindSpatialBlockError("Spatial-block completion changed.")
    return payload
