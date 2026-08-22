"""Authenticated first runtime step for the M3 blind-predictor build.

The stage reconstructs the already-frozen Census/WorldCover support needed by
the public predictor builders.  It never opens Landsat assets, QA, targets, or
model artifacts and performs no network request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

from la_heat.grid import build_fixed_grid
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    AUTHORIZATION_PATH as PARENT_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    BLIND_CITY_IDS,
    EXPECTED_CITY_COUNTS,
    authenticate_m3_blind_predictor_parent_authorization,
)
from la_heat.multicity.worldcover_eligible_support_evidence_v1 import (
    _mosaic_to_grid,
    _support_table,
    _zones,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "m3-blind-predictor-support-v1"
EXPECTED_PARENT_COMMIT: Final = (
    "1a704fca3848471dfba16c28bf2dd2e282343af6ac2aa24e3cbbd2ef44d790f8"
)
RUNTIME_AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SUPPORT_V1_RUNTIME_AUTHORIZATION.json"
)
VALUES_OPENED_PATH: Final = Path(
    "data/interim/multicity/m3_blind_predictor_build_v1/support/VALUES_OPENED.json"
)
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/support"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SUPPORT_COMPLETE.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_support_v1.py",
    "src/la_heat/grid.py",
    "src/la_heat/multicity/m3_blind_predictor_build_authorization_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_support_v1.py",
    "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py",
    "src/la_heat/provenance.py",
)


class M3BlindPredictorSupportError(RuntimeError):
    """Raised when support authorization or reconstruction drifts."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorSupportError(f"{label} escapes the project root.")
    return path


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictorSupportError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictorSupportError(f"{label} is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorSupportError(f"{label} commit is invalid.")
    return payload


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        observed = _read_committed(path, label=path.name)
        if observed != payload:
            raise M3BlindPredictorSupportError(f"Append-only artifact drifted: {path}")
        return
    atomic_json(dict(payload), path)


def build_m3_blind_predictor_support_runtime_authorization(
    project_root: str | Path,
) -> dict[str, Any]:
    """Build the code-bound child permit without opening support values."""

    root = Path(project_root).resolve()
    parent = authenticate_m3_blind_predictor_parent_authorization(
        root, PARENT_AUTHORIZATION_PATH
    )
    if parent.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindPredictorSupportError("Blind predictor parent commit changed.")
    code = [_file_record(root, _inside(root, path, label="code")) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_support_runtime_authorized",
        "parent_authorization": {
            **_file_record(root, _inside(root, PARENT_AUTHORIZATION_PATH, label="parent")),
            "commit_sha256": parent["commit_sha256"],
        },
        "blind_city_ids": list(BLIND_CITY_IDS),
        "key_universe_sha256": parent["key_universe"]["universe_sha256"],
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "write_paths": {
            "values_opened": VALUES_OPENED_PATH.as_posix(),
            "output_root": OUTPUT_ROOT.as_posix(),
            "completion": COMPLETION_PATH.as_posix(),
        },
        "permissions": {
            "read_only_parent_bound_census_and_worldcover_support_values": True,
            "write_only_new_support_runtime_and_outputs": True,
            "network_or_href_reads": False,
            "landsat_asset_href_thermal_qa_or_target_access": False,
            "daymet_sentinel_or_other_predictor_source_access": False,
            "fit_predict_score_or_evaluate": False,
        },
        "authorization_audit": {
            "census_or_worldcover_value_files_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "landsat_asset_href_thermal_qa_or_target_access": False,
            "model_fit_predict_score_or_evaluate": False,
        },
        "next_safe_stage": "run_support_reconstruction_after_exact_runtime_authentication",
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def create_m3_blind_predictor_support_runtime_authorization(
    project_root: str | Path,
    output_path: str | Path = RUNTIME_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, output_path, label="runtime authorization")
    expected = _inside(root, RUNTIME_AUTHORIZATION_PATH, label="runtime authorization")
    if destination != expected:
        raise M3BlindPredictorSupportError("Runtime authorization path changed.")
    payload = build_m3_blind_predictor_support_runtime_authorization(root)
    _write_exclusive(payload, destination)
    return authenticate_m3_blind_predictor_support_runtime_authorization(root, destination)


def authenticate_m3_blind_predictor_support_runtime_authorization(
    project_root: str | Path,
    authorization_path: str | Path = RUNTIME_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="runtime authorization")
    expected_path = _inside(root, RUNTIME_AUTHORIZATION_PATH, label="runtime authorization")
    if path != expected_path:
        raise M3BlindPredictorSupportError("Runtime authorization path changed.")
    observed = _read_committed(path, label="support runtime authorization")
    expected = build_m3_blind_predictor_support_runtime_authorization(root)
    if observed != expected:
        raise M3BlindPredictorSupportError("Support runtime authorization drifted.")
    return observed


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _inside(root, str(record.get("path", "")), label=label)
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3BlindPredictorSupportError(f"{label} byte identity changed.")
    return path


def _write_raster(path: Path, values: np.ndarray, *, crs: str, transform: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with rasterio.open(
        temporary,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=str(values.dtype),
        crs=crs,
        transform=transform,
        nodata=0,
        compress="deflate",
        predictor=1,
    ) as destination:
        destination.write(values, 1)
    temporary.replace(path)


def _city_support(
    root: Path,
    permit: Mapping[str, Any],
    city: Mapping[str, Any],
) -> dict[str, Any]:
    city_id = str(city["city_id"])
    checkpoint_path = _record_path(root, city["checkpoint"], label=f"{city_id} checkpoint")
    checkpoint = _read_committed(checkpoint_path, label=f"{city_id} checkpoint")
    boundary_path = _record_path(
        root, checkpoint["census"]["outputs"]["city_boundary"], label=f"{city_id} boundary"
    )
    tracts_path = _record_path(
        root, checkpoint["census"]["outputs"]["primary_tracts"], label=f"{city_id} tracts"
    )
    asset_paths = [
        _record_path(root, item["asset"], label=f"{city_id} WorldCover asset")
        for item in checkpoint["worldcover"]["items"]
    ]
    boundary = gpd.read_parquet(boundary_path)
    tracts = gpd.read_parquet(tracts_path).sort_values("tract_geoid", kind="stable")
    tracts = tracts.reset_index(drop=True)
    grid = build_fixed_grid(
        boundary,
        target_crs=str(checkpoint["city"]["target_grid_crs"]),
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    classes = _mosaic_to_grid(asset_paths, boundary=boundary, grid=grid)
    zones = _zones(tracts, grid).astype("int32")
    eligible = ((zones > 0) & (classes != 0) & (classes != 80)).astype("uint8")
    support, identities = _support_table(
        city_id=city_id,
        geoids=tuple(tracts["tract_geoid"].astype(str)),
        zones=zones,
        classes=classes,
        eligible=eligible.astype(bool),
        grid=grid,
    )
    expected_worldcover = checkpoint["worldcover"]
    existing_support_path = _record_path(
        root, expected_worldcover["output"], label=f"{city_id} frozen support"
    )
    existing_support = pd.read_parquet(existing_support_path).sort_values(
        "tract_geoid", kind="stable"
    ).reset_index(drop=True)
    if (
        grid.sha256 != expected_worldcover["grid"]["sha256"]
        or identities != expected_worldcover["identities"]
        or support.to_dict("records") != existing_support.to_dict("records")
        or len(support) != EXPECTED_CITY_COUNTS[city_id]["tract_count"]
    ):
        raise M3BlindPredictorSupportError(f"{city_id} support replay changed.")
    directory = _inside(root, OUTPUT_ROOT / city_id, label="support output")
    zone_path = directory / "tract_zones_30m.tif"
    eligible_path = directory / "eligible_mask_30m.tif"
    support_path = directory / "tract_eligible_support.parquet"
    marker_path = directory / "SUPPORT_COMPLETE.json"
    if marker_path.is_file():
        return _read_committed(marker_path, label=f"{city_id} support completion")
    _write_raster(zone_path, zones, crs=grid.crs, transform=grid.transform)
    _write_raster(eligible_path, eligible, crs=grid.crs, transform=grid.transform)
    atomic_parquet(support, support_path)
    support_record = {
        **_file_record(root, support_path),
        **parquet_file_record(support_path, support),
        "semantic_sha256": canonical_frame_sha256(support, sort_by=["tract_geoid"]),
    }
    marker = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_city_support_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_id": city_id,
            "grid": {
                "crs": grid.crs,
                "shape": list(grid.shape),
                "resolution_m": grid.resolution_m,
                "transform": list(grid.transform),
                "sha256": grid.sha256,
            },
            "identities": identities,
            "inputs": {
                "checkpoint_commit_sha256": checkpoint["commit_sha256"],
                "boundary": _file_record(root, boundary_path),
                "primary_tracts": _file_record(root, tracts_path),
                "worldcover_assets": [_file_record(root, path) for path in asset_paths],
            },
            "outputs": {
                "tract_zones_30m": _file_record(root, zone_path),
                "eligible_mask_30m": _file_record(root, eligible_path),
                "tract_support": support_record,
            },
            "audit": {
                "network_or_href_reads": 0,
                "landsat_asset_href_thermal_qa_or_target_access": False,
                "daymet_sentinel_or_other_predictor_source_access": False,
                "model_fit_predict_score_or_evaluate": False,
            },
        }
    )
    _write_exclusive(marker, marker_path)
    return marker


def run_m3_blind_predictor_support(
    project_root: str | Path,
    authorization_path: str | Path = RUNTIME_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Run or resume the four-city support reconstruction."""

    root = Path(project_root).resolve()
    permit = authenticate_m3_blind_predictor_support_runtime_authorization(
        root, authorization_path
    )
    parent = authenticate_m3_blind_predictor_parent_authorization(root)
    values_path = _inside(root, VALUES_OPENED_PATH, label="values opened marker")
    values_marker = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_support_values_opened",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_authorization_commit_sha256": parent["commit_sha256"],
            "scope": "parent_bound_census_and_worldcover_support_only",
            "network_or_href_reads": 0,
            "landsat_asset_href_thermal_qa_or_target_access": False,
        }
    )
    _write_exclusive(values_marker, values_path)
    completions = [
        _city_support(root, permit, city) for city in parent["key_universe"]["cities"]
    ]
    completion = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_support_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_authorization_commit_sha256": parent["commit_sha256"],
            "values_opened_commit_sha256": values_marker["commit_sha256"],
            "blind_city_ids": list(BLIND_CITY_IDS),
            "city_completions": [
                {
                    "city_id": marker["city_id"],
                    "commit_sha256": marker["commit_sha256"],
                    "outputs": marker["outputs"],
                }
                for marker in completions
            ],
            "audit": {
                "network_or_href_reads": 0,
                "landsat_asset_href_thermal_qa_or_target_access": False,
                "daymet_sentinel_or_other_predictor_source_access": False,
                "model_fit_predict_score_or_evaluate": False,
                "prediction_before_target_boundary_preserved": True,
            },
            "next_safe_stage": "implement_and_authorize_public_predictor_acquisition_runtime",
        }
    )
    completion_path = _inside(root, COMPLETION_PATH, label="support completion")
    _write_exclusive(completion, completion_path)
    return authenticate_m3_blind_predictor_support_completion(root)


def authenticate_m3_blind_predictor_support_completion(
    project_root: str | Path,
    completion_path: str | Path = COMPLETION_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_m3_blind_predictor_support_runtime_authorization(root)
    path = _inside(root, completion_path, label="support completion")
    observed = _read_committed(path, label="support completion")
    if (
        observed.get("state") != "m3_blind_predictor_support_complete"
        or observed.get("authorization_commit_sha256") != permit["commit_sha256"]
        or tuple(observed.get("blind_city_ids", ())) != BLIND_CITY_IDS
        or observed.get("audit", {}).get("network_or_href_reads") != 0
        or observed.get("audit", {}).get("landsat_asset_href_thermal_qa_or_target_access")
        is not False
        or len(observed.get("city_completions", ())) != len(BLIND_CITY_IDS)
    ):
        raise M3BlindPredictorSupportError("Support completion scope changed.")
    for record, city_id in zip(observed["city_completions"], BLIND_CITY_IDS, strict=True):
        if record.get("city_id") != city_id:
            raise M3BlindPredictorSupportError("Support completion city order changed.")
        for output in record["outputs"].values():
            _record_path(root, output, label=f"{city_id} support output")
    return observed
