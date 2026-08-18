"""Filter envelope-query results back to the exact Houston AOI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    _file_record,
    _read_committed,
    _with_commit,
    _write_exclusive,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_sentinel_bbox_repair_v1 import (
    COMPLETION_PATH as BBOX_COMPLETION_PATH,
)
from la_heat.multicity.m3_source_predictor_sentinel_bbox_repair_v1 import (
    _queue_snapshot,
    _validate_initial_snapshot,
)
from la_heat.multicity.m3_source_predictor_sentinel_bbox_repair_v1 import (
    authenticate_authorization as authenticate_bbox_authorization,
)
from la_heat.multicity.m3_source_predictor_sentinel_bbox_repair_v1 import (
    execute_repair as execute_bbox_repair,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-sentinel-geometry-filter-v1"
BBOX_AUTHORIZATION_COMMIT_SHA256: Final = (
    "e375ecb33138b5c56dbd46c5841379fc3f3aaea0709d1155ed14db10dccf3c46"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_PREDICTOR_SENTINEL_GEOMETRY_FILTER_V1_AUTHORIZATION.json"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development_v2/"
    "SOURCE_PREDICTOR_SENTINEL_GEOMETRY_FILTER_V1_COMPLETE.json"
)
CODE_PATHS: Final = (
    Path("src/la_heat/multicity/m3_source_predictor_sentinel_geometry_filter_v1.py"),
    Path("scripts/run_m3_source_predictor_sentinel_geometry_filter_v1.py"),
)


class SentinelGeometryFilterError(RuntimeError):
    """Raised when the exact-AOI filtering repair drifts."""


def filter_exact_aoi_items(items: Iterable[Any], exact_aoi: Any) -> tuple[Any, ...]:
    """Keep only valid STAC geometries intersecting the exact authenticated AOI."""

    import shapely

    retained = []
    for item in items:
        geometry = getattr(item, "geometry", None)
        if not isinstance(geometry, Mapping):
            continue
        candidate = shapely.geometry.shape(geometry)
        if candidate.is_valid and not candidate.is_empty and candidate.intersects(exact_aoi):
            retained.append(item)
    return tuple(retained)


def _root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _code_records(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root, root / path) for path in CODE_PATHS]


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    bbox = authenticate_bbox_authorization(root)
    if bbox.get("commit_sha256") != BBOX_AUTHORIZATION_COMMIT_SHA256:
        raise SentinelGeometryFilterError("BBox repair authorization changed.")
    parent = _read_committed(
        root / "manifests/multicity/next_experiment/"
        "M3_SOURCE_PREDICTOR_EXTENSION_V1_AUTHORIZATION.json",
        label="Predictor extension authorization",
    )
    snapshot = _queue_snapshot(root, parent)
    _validate_initial_snapshot(snapshot)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    if (settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json").exists():
        raise SentinelGeometryFilterError("Houston inventory already exists.")
    code = _code_records(root)
    return _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_source_predictor_sentinel_geometry_filter_authorized",
            "bbox_repair_authorization_commit_sha256": bbox["commit_sha256"],
            "incident": {
                "city_id": "houston_tx",
                "classification": "bbox_only_stac_item_has_invalid_geometry",
                "item_id": "S2B_MSIL2A_20230720T165849_R069_T15RTN_20230721T000753",
                "observed_error_type": "ValueError",
                "queue_snapshot": snapshot,
            },
            "repair_contract": {
                "server_discovery_geometry": "exact_authenticated_aoi_envelope",
                "local_prefilter_geometry": "exact_authenticated_aoi",
                "invalid_or_nonintersecting_bbox_only_items_retained": False,
                "parent_scientific_selection_unchanged": True,
            },
            "permissions": {
                "houston_source_sentinel_metadata_read": True,
                "blind_city_access": False,
                "target_or_landsat_value_read": False,
                "model_fit_select_predict_or_score": False,
            },
            "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
            "next_safe_stage": "execute_filtered_houston_inventory_once",
        }
    )


def _load_static(root: Path) -> dict[str, Any]:
    payload = _read_committed(root / AUTHORIZATION_PATH, label=AUTHORIZATION_PATH.name)
    code = _code_records(root)
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != "m3_source_predictor_sentinel_geometry_filter_authorized"
        or payload.get("bbox_repair_authorization_commit_sha256")
        != BBOX_AUTHORIZATION_COMMIT_SHA256
        or payload.get("code_identity") != {"files": code, "set_sha256": canonical_sha256(code)}
    ):
        raise SentinelGeometryFilterError("Geometry-filter authorization drifted.")
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    payload = build_authorization(root)
    _write_exclusive(payload, root / AUTHORIZATION_PATH)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    observed = _load_static(root)
    expected = build_authorization(root)
    if observed != expected:
        raise SentinelGeometryFilterError("Geometry-filter authorization mismatch.")
    return observed


def _exact_houston_aoi(root: Path) -> Any:
    import geopandas as gpd
    import shapely

    geography = _read_committed(
        root / "manifests/multicity/cities/houston_tx/geography/GEOGRAPHY_CONTRACT_V1.json",
        label="Houston geography",
    )
    record = geography.get("output_tables", {}).get("city_boundary")
    if not isinstance(record, Mapping):
        raise SentinelGeometryFilterError("Houston boundary record changed.")
    path = root / str(record.get("path", ""))
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise SentinelGeometryFilterError("Houston boundary bytes changed.")
    frame = gpd.read_parquet(path)
    aoi = shapely.union_all(frame.to_crs("EPSG:4326").geometry.to_numpy())
    if aoi.is_empty or not aoi.is_valid:
        raise SentinelGeometryFilterError("Houston exact AOI is invalid.")
    return aoi


def execute_repair(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    authorization = authenticate_authorization(root)
    exact_aoi = _exact_houston_aoi(root)
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    marker_path = settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json"

    import la_heat.multicity.m3_source_predictor_extension_worker_v1 as worker_module
    import la_heat.sentinel_inventory as sentinel_module

    original_query = sentinel_module.query_sentinel_items
    original_write = worker_module._write_exclusive

    def filtered_query(client: Any, **kwargs: Any) -> tuple[Any, ...]:
        return filter_exact_aoi_items(original_query(client, **kwargs), exact_aoi)

    def lineage_write(payload: Mapping[str, Any], destination: Path) -> None:
        if destination.resolve() == marker_path.resolve():
            unsigned = dict(payload)
            unsigned.pop("commit_sha256", None)
            unsigned.update(
                {
                    "sentinel_geometry_filter_authorization_commit_sha256": authorization[
                        "commit_sha256"
                    ],
                    "bbox_results_prefiltered_to_exact_aoi": True,
                    "invalid_bbox_only_item_count": 1,
                }
            )
            payload = _with_commit(unsigned)
        original_write(payload, destination)

    sentinel_module.query_sentinel_items = filtered_query
    worker_module._write_exclusive = lineage_write
    try:
        bbox_completion = execute_bbox_repair(root)
    finally:
        sentinel_module.query_sentinel_items = original_query
        worker_module._write_exclusive = original_write

    marker = _read_committed(marker_path, label="Houston Sentinel inventory")
    if (
        marker.get("sentinel_geometry_filter_authorization_commit_sha256")
        != authorization["commit_sha256"]
        or marker.get("bbox_results_prefiltered_to_exact_aoi") is not True
    ):
        raise SentinelGeometryFilterError("Filtered inventory lineage is absent.")
    completion = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_sentinel_geometry_filter_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "bbox_repair_completion_commit_sha256": bbox_completion["commit_sha256"],
            "bbox_repair_completion": _file_record(
                root,
                root / BBOX_COMPLETION_PATH,
                commit_sha256=bbox_completion["commit_sha256"],
            ),
            "sentinel_inventory_commit_sha256": marker["commit_sha256"],
            "sentinel_inventory": _file_record(
                root, marker_path, commit_sha256=marker["commit_sha256"]
            ),
            "queue_mutated": False,
            "blind_city_accessed": False,
            "target_or_landsat_values_read": False,
            "next_safe_stage": "resume_same_daymet_repair_runner_online_acquisition",
        }
    )
    _write_exclusive(completion, root / COMPLETION_PATH)
    return completion


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    authorization = _load_static(root)
    bbox_completion = _read_committed(root / BBOX_COMPLETION_PATH, label="BBox completion")
    settings = load_predictor_extension_settings(root, DEFAULT_CONFIG)
    marker_path = settings.acquisition_root / "sentinel/houston_tx/INVENTORY_COMPLETE.json"
    marker = _read_committed(marker_path, label="Houston Sentinel inventory")
    expected = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "source_predictor_sentinel_geometry_filter_complete",
            "authorization_commit_sha256": authorization["commit_sha256"],
            "bbox_repair_completion_commit_sha256": bbox_completion["commit_sha256"],
            "bbox_repair_completion": _file_record(
                root,
                root / BBOX_COMPLETION_PATH,
                commit_sha256=bbox_completion["commit_sha256"],
            ),
            "sentinel_inventory_commit_sha256": marker["commit_sha256"],
            "sentinel_inventory": _file_record(
                root, marker_path, commit_sha256=marker["commit_sha256"]
            ),
            "queue_mutated": False,
            "blind_city_accessed": False,
            "target_or_landsat_values_read": False,
            "next_safe_stage": "resume_same_daymet_repair_runner_online_acquisition",
        }
    )
    observed = _read_committed(root / COMPLETION_PATH, label=COMPLETION_PATH.name)
    if observed != expected:
        raise SentinelGeometryFilterError("Geometry-filter completion mismatch.")
    return observed
