"""Authorized exact Sentinel inventory for M3 blind predictor cities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

from la_heat.multicity import portable_sentinel_inventory as portable
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import BLIND_CITY_IDS
from la_heat.multicity.m3_blind_predictor_metadata_v1 import (
    OUTPUT_ROOT as METADATA_ROOT,
)
from la_heat.multicity.m3_blind_predictor_metadata_v1 import (
    authenticate_metadata_completion,
)
from la_heat.multicity.m3_blind_predictor_support_v1 import OUTPUT_ROOT as SUPPORT_ROOT
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-inventory-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_INVENTORY_V1_AUTHORIZATION.json"
)
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/sentinel_inventory"
)
RAW_STAC_ROOT: Final = Path(
    "data/raw/multicity/m3_blind_predictor_build_v1/sentinel_stac"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_INVENTORY_COMPLETE.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_sentinel_inventory_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_inventory_v1.py",
    "src/la_heat/multicity/portable_sentinel_inventory.py",
    "src/la_heat/sentinel_inventory.py",
    "src/la_heat/provenance.py",
)


class M3BlindPredictorSentinelInventoryError(RuntimeError):
    """Raised when Sentinel inventory authorization or identity drifts."""


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorSentinelInventoryError("Path escapes project root.")
    return path


def _record(root: Path, path: Path) -> dict[str, Any]:
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
        raise M3BlindPredictorSentinelInventoryError(f"Commit changed: {path}")
    return payload


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read(path) != dict(payload):
            raise M3BlindPredictorSentinelInventoryError(f"Artifact drifted: {path}")
        return
    atomic_json(dict(payload), path)


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    metadata = authenticate_metadata_completion(root)
    code = [_record(root, _inside(root, path)) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_inventory_authorized",
        "metadata_completion_commit_sha256": metadata["commit_sha256"],
        "blind_city_ids": list(BLIND_CITY_IDS),
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "network_contract": {
            "endpoint": portable.STAC_API,
            "collection": portable.STAC_COLLECTION,
            "exact_item_id_queries_only": True,
            "raster_asset_open_or_download": False,
            "signed_urls_or_credentials_persisted": False,
        },
        "permissions": {
            "hydrate_exact_parent_bound_sentinel_item_ids": True,
            "read_asset_href_metadata": True,
            "open_or_download_sentinel_rasters": False,
            "read_static_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "output_root": OUTPUT_ROOT.as_posix(),
        "raw_stac_root": RAW_STAC_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _committed(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    _write_exclusive(expected, _inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(_inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindPredictorSentinelInventoryError("Authorization drifted.")
    return observed


def _city_spec(root: Path, city_id: str) -> portable.CityInventorySpec:
    marker = _read(_inside(root, METADATA_ROOT / city_id / "METADATA_COMPLETE.json"))
    keys = pd.read_parquet(
        _inside(root, marker["outputs"]["predictor_keys"]["path"]),
        columns=["target_date"],
    )
    return portable.CityInventorySpec(
        city_id=city_id,
        timezone=str(marker["timezone"]),
        analysis_crs=str(marker["analysis_crs"]),
        target_dates=tuple(sorted(pd.to_datetime(keys["target_date"]).dt.date.unique())),
    )


def _support(root: Path, city_id: str) -> SimpleNamespace:
    support = _read(_inside(root, SUPPORT_ROOT / city_id / "SUPPORT_COMPLETE.json"))
    zone_path = _inside(root, support["outputs"]["tract_zones_30m"]["path"])
    mask_path = _inside(root, support["outputs"]["eligible_mask_30m"]["path"])
    with rasterio.open(zone_path) as source:
        transform = source.transform
        crs = str(source.crs)
    with rasterio.open(mask_path) as source:
        eligible = source.read(1).astype(bool)
    boundary = support["inputs"]["boundary"]
    return SimpleNamespace(
        city_id=city_id,
        crs=crs,
        transform=transform,
        eligible_land=np.asarray(eligible),
        geography_manifest={"output_tables": {"city_boundary": boundary}},
    )


def _candidate_source(
    root: Path, spec: portable.CityInventorySpec
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    marker_path = _inside(root, METADATA_ROOT / spec.city_id / "METADATA_COMPLETE.json")
    marker = _read(marker_path)
    record = marker["outputs"]["sentinel_items"]
    path = _inside(root, record["path"])
    if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
        raise M3BlindPredictorSentinelInventoryError("Candidate table changed.")
    frame = gpd.read_parquet(path)
    targets = tuple(spec.target_dates)
    local_dates = pd.to_datetime(frame["acquisition_local_date"]).dt.date
    keep = local_dates.map(
        lambda value: any(1 <= (target - value).days <= 60 for target in targets)
    )
    frame = frame.loc[keep].copy().reset_index(drop=True)
    if frame.empty or frame["item_id"].duplicated().any():
        raise M3BlindPredictorSentinelInventoryError("Candidate identities changed.")
    return frame, {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "source_manifest": marker_path.relative_to(root).as_posix(),
        "source_manifest_commit_sha256": marker["commit_sha256"],
    }


def run_inventory(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    portable.CITY_IDS = BLIND_CITY_IDS
    portable.OUTPUT_ROOT = OUTPUT_ROOT
    portable.RAW_STAC_ROOT = RAW_STAC_ROOT
    portable.PREDICTOR_KEY_ROOT = METADATA_ROOT
    portable._load_city_spec = _city_spec
    portable.load_city_support = _support
    portable._external_candidate_source = _candidate_source
    completions = []
    for city_id in BLIND_CITY_IDS:
        authenticate_authorization(root)
        payload = portable.build_portable_sentinel_inventory(root, city_id, batch_size=50)
        completions.append(payload)
        print(
            f"SENTINEL_INVENTORY_COMPLETE {city_id} "
            f"{payload['counts']['selected_physical_acquisitions']}",
            flush=True,
        )
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_sentinel_inventory_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_completions": [
                {
                    "city_id": row["city_id"],
                    "commit_sha256": row["commit_sha256"],
                    "selected_physical_acquisitions": row["counts"][
                        "selected_physical_acquisitions"
                    ],
                }
                for row in completions
            ],
            "audit": {
                "asset_href_metadata_read": True,
                "raster_assets_opened_or_downloaded": False,
                "static_daymet_landsat_qa_or_target_values_read": False,
                "model_fit_predict_score_or_evaluate": False,
            },
            "next_safe_stage": "authorize_resumable_sentinel_raster_acquisition",
        }
    )
    _write_exclusive(completion, _inside(root, COMPLETION_PATH))
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    completion = _read(_inside(root, COMPLETION_PATH))
    if (
        completion.get("authorization_commit_sha256") != permit["commit_sha256"]
        or len(completion.get("city_completions", [])) != 4
        or completion.get("audit", {}).get("raster_assets_opened_or_downloaded")
        is not False
        or completion.get("audit", {}).get(
            "static_daymet_landsat_qa_or_target_values_read"
        )
        is not False
    ):
        raise M3BlindPredictorSentinelInventoryError("Completion changed.")
    return completion
