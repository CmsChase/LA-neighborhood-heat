"""Authorized metadata-only bootstrap for the M3 blind predictor build."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import pandas as pd
import requests

from la_heat.multicity import source_footprints
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    BLIND_CITY_IDS,
    authenticate_m3_blind_predictor_parent_authorization,
)
from la_heat.multicity.m3_blind_predictor_support_v1 import (
    authenticate_m3_blind_predictor_support_completion,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

ALGORITHM_VERSION: Final = "m3-blind-predictor-metadata-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_METADATA_V1_RUNTIME_AUTHORIZATION.json"
)
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/m3_blind_predictor_build_v1/metadata"
)
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/inventory"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_METADATA_COMPLETE.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_metadata_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_build_authorization_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_metadata_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_support_v1.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/provenance.py",
)


class M3BlindPredictorMetadataError(RuntimeError):
    """Raised when metadata authorization or identities drift."""


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorMetadataError("Path escapes the project root.")
    return path


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read_committed(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictorMetadataError(f"Cannot read committed JSON: {path}") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictorMetadataError("Committed JSON is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorMetadataError(f"Commit changed: {path}")
    return payload


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read_committed(path) != dict(payload):
            raise M3BlindPredictorMetadataError(f"Append-only artifact drifted: {path}")
        return
    atomic_json(dict(payload), path)


def build_runtime_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build the exact child permit without opening predictor values or networking."""

    root = Path(project_root).resolve()
    parent = authenticate_m3_blind_predictor_parent_authorization(root)
    support = authenticate_m3_blind_predictor_support_completion(root)
    code = [_file_record(root, _inside(root, path)) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_metadata_runtime_authorized",
        "parent_authorization_commit_sha256": parent["commit_sha256"],
        "support_completion_commit_sha256": support["commit_sha256"],
        "blind_city_ids": list(BLIND_CITY_IDS),
        "key_universe_sha256": parent["key_universe"]["universe_sha256"],
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "network_contract": {
            "https_get_hosts": ["cmr.earthdata.nasa.gov"],
            "https_post_hosts": ["planetarycomputer.microsoft.com"],
            "sentinel_collection": source_footprints.SENTINEL_COLLECTION,
            "stac_assets_and_links_excluded": True,
            "daymet_year": 2025,
            "daymet_variables": list(DEFAULT_DAYMET_VARIABLES),
        },
        "write_roots": [RUNTIME_ROOT.as_posix(), OUTPUT_ROOT.as_posix()],
        "completion": COMPLETION_PATH.as_posix(),
        "permissions": {
            "read_parent_bound_census_worldcover_support": True,
            "read_public_sentinel_and_daymet_metadata": True,
            "read_or_download_sentinel_daymet_static_predictor_values": False,
            "read_landsat_asset_hrefs_thermal_qa_or_targets": False,
            "fit_predict_score_or_evaluate": False,
        },
        "next_safe_stage": "run_resumable_metadata_bootstrap",
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _with_commit(payload)


def create_runtime_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_runtime_authorization(root)
    _write_exclusive(payload, _inside(root, AUTHORIZATION_PATH))
    return authenticate_runtime_authorization(root)


def authenticate_runtime_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(_inside(root, AUTHORIZATION_PATH))
    expected = build_runtime_authorization(root)
    if observed != expected:
        raise M3BlindPredictorMetadataError("Metadata runtime authorization drifted.")
    return observed


def _parent_city(parent: Mapping[str, Any], city_id: str) -> Mapping[str, Any]:
    matches = [
        row for row in parent["key_universe"]["cities"] if row["city_id"] == city_id
    ]
    if len(matches) != 1:
        raise M3BlindPredictorMetadataError(f"Parent city identity changed: {city_id}")
    return matches[0]


def _frame_record(root: Path, path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        **_file_record(root, path),
        **parquet_file_record(path, frame),
        "semantic_sha256": canonical_frame_sha256(
            frame,
            sort_by=[
                column
                for column in (
                    "city_id",
                    "target_date",
                    "tract_geoid",
                    "item_id",
                    "variable",
                )
                if column in frame
            ],
        ),
    }


def _city_metadata(root: Path, city_id: str) -> dict[str, Any]:
    permit = authenticate_runtime_authorization(root)
    parent = authenticate_m3_blind_predictor_parent_authorization(root)
    city = _parent_city(parent, city_id)
    checkpoint = _read_committed(_inside(root, city["checkpoint"]["path"]))
    output = _inside(root, OUTPUT_ROOT / city_id)
    marker_path = output / "METADATA_COMPLETE.json"
    if marker_path.is_file():
        return _read_committed(marker_path)

    boundary_path = _inside(root, checkpoint["census"]["outputs"]["city_boundary"]["path"])
    support_path = _inside(
        root,
        f"data/processed/multicity/m3_blind_predictor_build_v1/support/{city_id}/tract_eligible_support.parquet",
    )
    boundary = gpd.read_parquet(boundary_path)
    support = pd.read_parquet(support_path)
    dates = tuple(date.fromisoformat(value) for value in city["target_dates"])
    keys = pd.MultiIndex.from_product(
        [sorted(support["tract_geoid"].astype(str)), [value.isoformat() for value in dates]],
        names=["tract_geoid", "target_date"],
    ).to_frame(index=False)
    keys.insert(0, "city_id", city_id)
    if len(keys) != int(city["row_count"]):
        raise M3BlindPredictorMetadataError(f"Key row count changed for {city_id}.")
    key_path = output / "predictor_keys.parquet"
    atomic_parquet(keys, key_path)

    bbox = tuple(float(value) for value in checkpoint["census"]["bbox_wgs84"])
    timezone = str(checkpoint["city"]["timezone"])
    analysis_crs = str(checkpoint["city"]["target_grid_crs"])
    sentinel_start = min(dates) - timedelta(days=60)
    sentinel_end = max(dates) - timedelta(days=1)
    # Re-authenticate immediately before every network family access.
    authenticate_runtime_authorization(root)
    session = requests.Session()
    features, pages, query = source_footprints.fetch_public_stac_metadata(
        session,
        api=source_footprints.PLANETARY_COMPUTER_STAC_API,
        collection=source_footprints.SENTINEL_COLLECTION,
        bbox_wgs84=bbox,
        datetime_interval=source_footprints.local_date_interval_to_utc(
            sentinel_start, sentinel_end, timezone
        ),
        fields=source_footprints.SENTINEL_FIELDS,
        properties=source_footprints.SENTINEL_PROPERTIES,
        page_limit=1000,
    )
    sentinel = source_footprints.build_optical_item_table(
        features,
        source="sentinel_mgrs",
        collection=source_footprints.SENTINEL_COLLECTION,
        expected_properties=source_footprints.SENTINEL_PROPERTIES,
        allowed_platforms=("sentinel-2a", "sentinel-2b", "sentinel-2c"),
        local_start_date=sentinel_start,
        local_end_date=sentinel_end,
        timezone=timezone,
        city_boundary=boundary,
        analysis_crs=analysis_crs,
    )
    sentinel_path = output / "sentinel_items.parquet"
    atomic_parquet(sentinel, sentinel_path)
    atomic_json({"pages": pages}, _inside(root, RUNTIME_ROOT / city_id / "sentinel_pages.json"))

    authenticate_runtime_authorization(root)
    daymet, daymet_raw, daymet_query = source_footprints.fetch_daymet_granule_metadata(
        session,
        endpoint=source_footprints.DAYMET_CMR_GRANULES_URL,
        collection_concept_id=source_footprints.DAYMET_CMR_COLLECTION_ID,
        year=2025,
        variables=DEFAULT_DAYMET_VARIABLES,
        bbox_wgs84=bbox,
    )
    daymet_path = output / "daymet_granules.parquet"
    atomic_parquet(daymet, daymet_path)
    atomic_json(daymet_raw, _inside(root, RUNTIME_ROOT / city_id / "daymet_response.json"))
    window = source_footprints.derive_daymet_index_window(bbox, halo_cells=1)

    marker = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_city_metadata_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_id": city_id,
            "timezone": timezone,
            "analysis_crs": analysis_crs,
            "bbox_wgs84": list(bbox),
            "target_date_count": len(dates),
            "outputs": {
                "predictor_keys": _frame_record(root, key_path, keys),
                "sentinel_items": _frame_record(root, sentinel_path, sentinel),
                "daymet_granules": _frame_record(root, daymet_path, daymet),
            },
            "sentinel_query": query,
            "daymet_query": daymet_query,
            "daymet_window": window,
            "audit": {
                "stac_assets_or_links_read": False,
                "predictor_raster_or_netcdf_values_read": False,
                "landsat_asset_href_thermal_qa_or_target_access": False,
                "model_fit_predict_score_or_evaluate": False,
            },
        }
    )
    _write_exclusive(marker, marker_path)
    return marker


def run_metadata_bootstrap(project_root: str | Path) -> dict[str, Any]:
    """Run/resume four independent city metadata work units."""

    root = Path(project_root).resolve()
    permit = authenticate_runtime_authorization(root)
    completions = [_city_metadata(root, city_id) for city_id in BLIND_CITY_IDS]
    parent = authenticate_m3_blind_predictor_parent_authorization(root)
    total_rows = sum(int(row["outputs"]["predictor_keys"]["rows"]) for row in completions)
    if total_rows != int(parent["key_universe"]["tract_date_row_count"]):
        raise M3BlindPredictorMetadataError("Global predictor-key count changed.")
    payload = _with_commit(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_metadata_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "parent_authorization_commit_sha256": parent["commit_sha256"],
            "city_completions": [
                {"city_id": row["city_id"], "commit_sha256": row["commit_sha256"]}
                for row in completions
            ],
            "city_count": 4,
            "target_date_count": 143,
            "predictor_key_count": total_rows,
            "audit": {
                "public_metadata_only": True,
                "stac_assets_or_links_read": False,
                "predictor_values_read": False,
                "landsat_asset_href_thermal_qa_or_target_access": False,
                "model_fit_predict_score_or_evaluate": False,
            },
            "next_safe_stage": "implement_and_authorize_public_predictor_value_acquisition",
        }
    )
    _write_exclusive(payload, _inside(root, COMPLETION_PATH))
    return authenticate_metadata_completion(root)


def authenticate_metadata_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_runtime_authorization(root)
    completion = _read_committed(_inside(root, COMPLETION_PATH))
    if (
        completion.get("authorization_commit_sha256") != permit["commit_sha256"]
        or completion.get("city_count") != 4
        or completion.get("target_date_count") != 143
        or completion.get("predictor_key_count") != 23_667
        or completion.get("audit", {}).get("stac_assets_or_links_read") is not False
        or completion.get("audit", {}).get(
            "landsat_asset_href_thermal_qa_or_target_access"
        )
        is not False
    ):
        raise M3BlindPredictorMetadataError("Metadata completion audit changed.")
    for record in completion.get("city_completions", []):
        marker = _read_committed(
            _inside(root, OUTPUT_ROOT / str(record["city_id"]) / "METADATA_COMPLETE.json")
        )
        if marker.get("commit_sha256") != record.get("commit_sha256"):
            raise M3BlindPredictorMetadataError("City metadata completion changed.")
    return completion
