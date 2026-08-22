"""Append-only geometry hashing repair for blind predictor metadata v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pandas as pd
import shapely

from la_heat.multicity import m3_blind_predictor_metadata_endpoint_repair_v1 as v1
from la_heat.multicity import m3_blind_predictor_metadata_v1 as parent
from la_heat.provenance import (
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "m3-blind-predictor-metadata-geometry-repair-v2"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_METADATA_GEOMETRY_REPAIR_V2_AUTHORIZATION.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_metadata_geometry_repair_v2.py",
    "src/la_heat/multicity/m3_blind_predictor_metadata_geometry_repair_v2.py",
)


class M3BlindPredictorMetadataGeometryRepairError(RuntimeError):
    """Raised when v2 repair identity changes."""


def _record(root: Path, value: str | Path) -> dict[str, Any]:
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindPredictorMetadataGeometryRepairError("Repair path changed.")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorMetadataGeometryRepairError("Repair commit changed.")
    return payload


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    previous = v1.authenticate_authorization(root)
    code = [_record(root, path) for path in CODE_PATHS]
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_metadata_geometry_repair_authorized",
            "previous_repair_authorization_commit_sha256": previous["commit_sha256"],
            "incident": {
                "error_type": "TypeError",
                "message_class": "polygon_not_json_serializable_in_semantic_hash",
                "public_metadata_queries_may_have_completed": True,
                "landsat_qa_or_target_values_read": False,
            },
            "repair": {
                "operation": "canonicalize_geometry_as_normalized_wkb_hex_for_hash_only",
                "output_file_bytes_unchanged": True,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "permissions": {
                "resume_previous_repair": True,
                "change_query_city_date_key_or_source_contract": False,
                "read_predictor_landsat_qa_or_target_values": False,
            },
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if _read(path) != expected:
            raise M3BlindPredictorMetadataGeometryRepairError("V2 auth drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(root / AUTHORIZATION_PATH)
    expected = build_authorization(root)
    if observed != expected:
        raise M3BlindPredictorMetadataGeometryRepairError("V2 auth drifted.")
    return observed


def _geometry_safe_frame_record(
    root: Path, path: Path, frame: pd.DataFrame
) -> dict[str, Any]:
    semantic = frame.copy()
    if "geometry" in semantic:
        semantic["geometry"] = [
            shapely.to_wkb(
                shapely.normalize(value),
                hex=True,
                output_dimension=2,
                byte_order=1,
                include_srid=False,
            )
            for value in semantic["geometry"]
        ]
    sort_by = [
        column
        for column in ("city_id", "target_date", "tract_geoid", "item_id", "variable")
        if column in semantic
    ]
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **parquet_file_record(path, frame),
        "semantic_sha256": canonical_frame_sha256(semantic, sort_by=sort_by),
    }


def run_repaired_metadata(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    parent._frame_record = _geometry_safe_frame_record
    return v1.run_repaired_metadata(root)
