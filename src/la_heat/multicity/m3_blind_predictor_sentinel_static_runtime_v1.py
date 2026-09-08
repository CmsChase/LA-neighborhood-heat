"""Code-bound, resumable acquisition runtime for blind Sentinel/static inputs."""

from __future__ import annotations

import json
import os
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio import Affine

from la_heat import sentinel_feature_builder as sentinel_builder
from la_heat.grid import FixedGrid
from la_heat.multicity import portable_predictor_components as components
from la_heat.multicity import portable_predictor_source_evidence_v1 as source_evidence
from la_heat.multicity import portable_sentinel_build as sentinel_build
from la_heat.multicity import source_footprints
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import BLIND_CITY_IDS
from la_heat.multicity.m3_blind_predictor_sentinel_static_acquisition_authorization_v1 import (
    AUTHORIZATION_PATH as SCOPE_AUTHORIZATION_PATH,
)
from la_heat.multicity.m3_blind_predictor_sentinel_static_acquisition_authorization_v1 import (
    EXPECTED_SENTINEL_COUNTS,
)
from la_heat.multicity.m3_blind_predictor_sentinel_static_acquisition_authorization_v1 import (
    authenticate_authorization as authenticate_scope_authorization,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file
from la_heat.static_features import build_static_support

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-static-runtime-v1"
CONFIG_PATH: Final = Path(
    "configs/multicity/m3_blind_predictor_sentinel_static_runtime_v1.toml"
)
LAUNCH_AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_RUNTIME_LAUNCH_V1_AUTHORIZATION.json"
)
SUPPORT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/support"
)
INVENTORY_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/sentinel_inventory"
)
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/m3_blind_predictor_build_v1/"
    "sentinel_static_acquisition_v1/runtime"
)
OUTPUT_ROOT: Final = Path(
    "data/raw/multicity/m3_blind_predictor_build_v1/sentinel_static_acquisition_v1"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ACQUISITION_COMPLETE.json"
)
STATUS_PATH: Final = RUNTIME_ROOT / "status.json"
PAUSE_PATH: Final = RUNTIME_ROOT / "PAUSE_REQUESTED"
CANARY_PATH: Final = RUNTIME_ROOT / "CANARY_COMPLETE.json"
EXPECTED_SCOPE_COMMIT: Final = (
    "2543bf7e29b6a76cc1099cd60f03644d669104c6597c0ed54db8e4cf3e83f633"
)
EXPECTED_CITY_SUPPORT_COMMITS: Final = {
    "seattle_wa": "4fc9853600cd263a71998805815bec1c959def01e082366564a9ce6cad9ae84d",
    "denver_co": "aa04f0a2221a766a00ee3ab6230a379cd680f2f3bdbd4ec56dd1fce76ac439d3",
    "atlanta_ga": "c0e5424db3cb2fa601fbbcfdfdab3238c5c811dd0f884881f10bcaf0f6282ec4",
    "miami_fl": "7a2abc819e14844172951e360a821e387b11d8f9c466825ba0f974ede4e651c8",
}
EXPECTED_CITY_INVENTORY_COMMITS: Final = {
    "seattle_wa": "4035006fd9f05ec37f78f3edfa30ca05587e4526084034a8864eacaac06088ca",
    "denver_co": "4fd68413759e750c559ffa21b2110e4f72cd57d579d7328c4eabcd122feb4856",
    "atlanta_ga": "560e394fb3a154826805c02033592dc56c73571b37ed2a815e7eb7e0078aabf5",
    "miami_fl": "57f6675051a7944fbe9e084914bbddafafe4a39b032a825e5af71ddb16929aa3",
}
CODE_PATHS: Final = (
    "configs/multicity/m3_blind_predictor_sentinel_static_runtime_v1.toml",
    "scripts/run_m3_blind_predictor_sentinel_static_runtime_v1.py",
    "src/la_heat/grid.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_static_acquisition_authorization_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_static_runtime_v1.py",
    "src/la_heat/multicity/portable_predictor_components.py",
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py",
    "src/la_heat/multicity/portable_sentinel_build.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/static_features.py",
)
STATIC_TASK_COUNT: Final = 14
TOTAL_TASK_COUNT: Final = 539 + STATIC_TASK_COUNT
_VALUE_SUFFIXES: Final = {".csv", ".parquet", ".tif", ".zip"}
_ORIGINAL_SENTINEL_ASSET_READER: Final = sentinel_builder._read_asset_to_optical_grid
_ORIGINAL_SENTINEL_METADATA_READER: Final = sentinel_builder._read_product_metadata


class M3BlindPredictorSentinelStaticRuntimeError(RuntimeError):
    """Raised when the authorized runtime or a durable task drifts."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorSentinelStaticRuntimeError(f"{label} escapes project root.")
    return path


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindPredictorSentinelStaticRuntimeError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict):
        raise M3BlindPredictorSentinelStaticRuntimeError(f"{label} is not an object.")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorSentinelStaticRuntimeError(f"{label} commit is invalid.")
    return payload


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read_committed(path, label=path.name) != dict(payload):
            raise M3BlindPredictorSentinelStaticRuntimeError(
                f"Append-only artifact drifted: {path}"
            )
        return
    atomic_json(dict(payload), path)


def _load_config(root: Path) -> dict[str, Any]:
    path = _inside(root, CONFIG_PATH, label="runtime config")
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    runtime = config.get("runtime", {})
    if (
        runtime.get("schema_version") != 1
        or runtime.get("algorithm_version") != ALGORITHM_VERSION
        or runtime.get("download_threads") != 2
        or runtime.get("acquisition_concurrency") != 1
        or runtime.get("maximum_attempts") != 3
    ):
        raise M3BlindPredictorSentinelStaticRuntimeError("Runtime config changed.")
    expected_paths = {
        "scope_authorization": SCOPE_AUTHORIZATION_PATH.as_posix(),
        "launch_authorization": LAUNCH_AUTHORIZATION_PATH.as_posix(),
        "runtime_root": RUNTIME_ROOT.as_posix(),
        "output_root": OUTPUT_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
    }
    if config.get("paths") != expected_paths:
        raise M3BlindPredictorSentinelStaticRuntimeError("Runtime paths changed.")
    return config


def build_launch_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build a launch permit without opening or statting value files."""

    root = Path(project_root).resolve()
    scope = authenticate_scope_authorization(root)
    if scope.get("commit_sha256") != EXPECTED_SCOPE_COMMIT:
        raise M3BlindPredictorSentinelStaticRuntimeError("Scope permit changed.")
    config = _load_config(root)
    code = [_record(root, _inside(root, path, label="runtime code")) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_static_runtime_launch_authorized",
        "scope_authorization": {
            **_record(root, _inside(root, SCOPE_AUTHORIZATION_PATH, label="scope")),
            "commit_sha256": scope["commit_sha256"],
        },
        "blind_city_ids": list(BLIND_CITY_IDS),
        "task_plan": {
            "sentinel_acquisitions": sum(EXPECTED_SENTINEL_COUNTS.values()),
            "static_tasks": STATIC_TASK_COUNT,
            "total": TOTAL_TASK_COUNT,
            "sha256": canonical_sha256(
                {
                    "cities": scope["city_scope"],
                    "static_tasks": STATIC_TASK_COUNT,
                    "sentinel_acquisitions": sum(EXPECTED_SENTINEL_COUNTS.values()),
                }
            ),
        },
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "config": {
            **_record(root, _inside(root, CONFIG_PATH, label="config")),
            "runtime": config["runtime"],
            "canary": config["canary"],
        },
        "permissions": {
            "read_exact_scope_bound_sentinel_and_static_values": True,
            "network_exact_scope_bound_sentinel_and_static_only": True,
            "write_only_scope_bound_runtime_output_and_completion_paths": True,
            "read_daymet_values_or_network": False,
            "read_landsat_asset_href_thermal_qa_or_target": False,
            "fit_predict_score_or_evaluate": False,
        },
        "runtime_contract": {
            "authenticate_before_each_value_or_network_read": True,
            "resumable_durable_task_boundaries": True,
            "do_not_rebuild_or_delete_valid_cache": True,
            "maximum_download_threads": 2,
            "maximum_active_acquisitions": 1,
            "credentials_signed_urls_or_cookies_persisted": False,
            "canary_must_precede_full_run": True,
            "offline_assembly_not_authorized": True,
        },
        "authorization_audit": {
            "value_files_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "daymet_landsat_qa_or_target_access": False,
            "model_fit_predict_score_or_evaluate": False,
        },
        "output_root": OUTPUT_ROOT.as_posix(),
        "runtime_root": RUNTIME_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
        "next_safe_stage": "run_one_static_plus_one_sentinel_canary_then_resume_full_acquisition",
    }
    payload["run_id"] = f"m3-blind-sentinel-static-v1-{canonical_sha256(payload)[:16]}"
    payload["claim_id"] = canonical_sha256(payload)
    return _committed(payload)


def create_launch_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_launch_authorization(root)
    _write_exclusive(
        payload, _inside(root, LAUNCH_AUTHORIZATION_PATH, label="launch authorization")
    )
    return authenticate_launch_authorization(root)


def authenticate_launch_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(
        _inside(root, LAUNCH_AUTHORIZATION_PATH, label="launch authorization"),
        label="launch authorization",
    )
    if observed != build_launch_authorization(root):
        raise M3BlindPredictorSentinelStaticRuntimeError("Launch authorization drifted.")
    return observed


def _verify_record(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    authenticate_launch_authorization(root)
    path = _inside(root, str(record.get("path", "")), label=label)
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3BlindPredictorSentinelStaticRuntimeError(f"{label} changed.")
    return path


def _city_support(root: Path, city_id: str) -> components.PortableCitySupport:
    authenticate_launch_authorization(root)
    marker_path = _inside(root, SUPPORT_ROOT / city_id / "SUPPORT_COMPLETE.json", label=city_id)
    marker = _read_committed(marker_path, label=f"{city_id} support")
    if marker.get("commit_sha256") != EXPECTED_CITY_SUPPORT_COMMITS[city_id]:
        raise M3BlindPredictorSentinelStaticRuntimeError("City support changed.")
    tracts_path = _verify_record(root, marker["inputs"]["primary_tracts"], label="tracts")
    zones_path = _verify_record(root, marker["outputs"]["tract_zones_30m"], label="zones")
    eligible_path = _verify_record(
        root, marker["outputs"]["eligible_mask_30m"], label="eligible mask"
    )
    support_path = _verify_record(root, marker["outputs"]["tract_support"], label="support")
    authenticate_launch_authorization(root)
    tracts = gpd.read_parquet(tracts_path).sort_values("tract_geoid", kind="stable")
    tracts = tracts.reset_index(drop=True)
    authenticate_launch_authorization(root)
    table = pd.read_parquet(support_path).sort_values("tract_geoid", kind="stable")
    table = table.reset_index(drop=True)
    authenticate_launch_authorization(root)
    with rasterio.open(zones_path) as source:
        zones = source.read(1)
    authenticate_launch_authorization(root)
    with rasterio.open(eligible_path) as source:
        eligible = source.read(1).astype(bool)
    geoids = tuple(tracts["tract_geoid"].astype(str))
    if geoids != tuple(table["tract_geoid"].astype(str)):
        raise M3BlindPredictorSentinelStaticRuntimeError("Support GEOIDs changed.")
    grid_record = marker["grid"]
    transform = Affine(*[float(value) for value in grid_record["transform"][:6]])
    height, width = (int(value) for value in grid_record["shape"])
    grid = FixedGrid(
        crs=str(grid_record["crs"]),
        resolution_m=float(grid_record["resolution_m"]),
        anchor_x_m=15.0,
        anchor_y_m=15.0,
        left=float(transform.c),
        bottom=float(transform.f + transform.e * height),
        right=float(transform.c + transform.a * width),
        top=float(transform.f),
        width=width,
        height=height,
        transform=transform,
    )
    static = build_static_support(
        zones,
        eligible,
        geoids=geoids,
        grid_identity=str(marker["identities"]["city_support_identity_sha256"]),
    )
    if grid.sha256 != grid_record["sha256"] or not np.array_equal(
        static.counts, table["eligible_cell_count"].to_numpy(dtype=np.int64)
    ):
        raise M3BlindPredictorSentinelStaticRuntimeError("Support grid changed.")
    return components.PortableCitySupport(
        city_id=city_id,
        grid=grid,
        zones=zones,
        eligible_land=eligible,
        tract_geoids=geoids,
        tracts=tracts,
        static_support=static,
        worldcover_manifest={"commit_sha256": marker["commit_sha256"]},
        geography_manifest={
            "commit_sha256": marker["inputs"]["checkpoint_commit_sha256"]
        },
    )


def _authenticate_city_inventory(root: Path, city_id: str) -> dict[str, Any]:
    authenticate_launch_authorization(root)
    path = _inside(root, INVENTORY_ROOT / city_id / "INVENTORY_COMPLETE.json", label=city_id)
    payload = _read_committed(path, label=f"{city_id} Sentinel inventory")
    if (
        payload.get("commit_sha256") != EXPECTED_CITY_INVENTORY_COMMITS[city_id]
        or payload.get("city_id") != city_id
        or payload.get("counts", {}).get("selected_physical_acquisitions")
        != EXPECTED_SENTINEL_COUNTS[city_id]
    ):
        raise M3BlindPredictorSentinelStaticRuntimeError("Sentinel inventory changed.")
    for label in ("selected_acquisitions", "selected_items", "target_window_membership"):
        _verify_record(root, payload["outputs"][label], label=f"{city_id} {label}")
    return payload


def _configure_sentinel_adapter(root: Path) -> None:
    sentinel_build.CITY_IDS = BLIND_CITY_IDS
    sentinel_build.INVENTORY_ROOT = INVENTORY_ROOT
    sentinel_build.RUNTIME_ROOT = OUTPUT_ROOT / "sentinel"
    sentinel_build.RAW_METADATA_ROOT = OUTPUT_ROOT / "sentinel_product_metadata"
    sentinel_build.SENTINEL_COMPONENT_ROOT = OUTPUT_ROOT / "sentinel_compiled_not_used"
    sentinel_build.authenticate_portable_sentinel_inventory = (
        lambda _root, city_id: _authenticate_city_inventory(root, city_id)
    )
    sentinel_build.load_city_support = lambda _root, city_id: _city_support(root, city_id)

    def authorized_asset_reader(url: str, *args: Any, **kwargs: Any) -> Any:
        authenticate_launch_authorization(root)
        _validate_sentinel_url(url)
        return _ORIGINAL_SENTINEL_ASSET_READER(url, *args, **kwargs)

    def authorized_metadata_reader(*args: Any, **kwargs: Any) -> Any:
        url = str(kwargs.get("unsigned_url", ""))
        authenticate_launch_authorization(root)
        _validate_sentinel_url(url)
        return _ORIGINAL_SENTINEL_METADATA_READER(*args, **kwargs)

    sentinel_builder._read_asset_to_optical_grid = authorized_asset_reader
    sentinel_builder._read_product_metadata = authorized_metadata_reader


def _validate_sentinel_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "planetarycomputer.microsoft.com" or host.endswith(".blob.core.windows.net")
    ):
        raise M3BlindPredictorSentinelStaticRuntimeError("Sentinel URL escaped allowlist.")


def _sentinel_contexts(root: Path) -> dict[str, sentinel_build.CityBuildContext]:
    authenticate_launch_authorization(root)
    _configure_sentinel_adapter(root)
    contexts, _ = sentinel_build.prepare_contexts(root, BLIND_CITY_IDS)
    for context in contexts.values():
        href_columns = [column for column in context.inventory.items if column.endswith("_href")]
        for column in href_columns:
            for url in context.inventory.items[column].astype(str):
                _validate_sentinel_url(url)
    return contexts


def _sentinel_complete(
    root: Path, context: sentinel_build.CityBuildContext, row: Any
) -> bool:
    return sentinel_build.acquisition_cache_is_current(root, context, row)


def _run_sentinel_task(
    root: Path, context: sentinel_build.CityBuildContext, row: Any, *, download_threads: int
) -> dict[str, Any]:
    authenticate_launch_authorization(root)
    item_rows = sentinel_build._item_rows(context, str(row.physical_acquisition_id))
    for column in [column for column in item_rows if column.endswith("_href")]:
        for url in item_rows[column].astype(str):
            _validate_sentinel_url(url)
    return sentinel_build._process_one(
        root, context, row, download_threads=download_threads, force=False
    )


def _static_marker(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".COMPLETE.json")


def _static_complete(root: Path, path: Path) -> bool:
    marker_path = _static_marker(path)
    if not marker_path.is_file():
        return False
    try:
        marker = _read_committed(marker_path, label="static task marker")
    except M3BlindPredictorSentinelStaticRuntimeError:
        return False
    output = marker.get("output", {})
    return bool(
        marker.get("authorization_commit_sha256")
        == authenticate_launch_authorization(root)["commit_sha256"]
        and path.is_file()
        and path.stat().st_size == output.get("bytes")
        and sha256_file(path) == output.get("sha256")
    )


def _download_static(
    root: Path,
    *,
    url: str,
    destination: Path,
    params: Sequence[tuple[str, str]] | None,
    config: Mapping[str, Any],
) -> None:
    authenticate_launch_authorization(root)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.mrlc.gov",
        "opentopography.s3.sdsc.edu",
    }:
        raise M3BlindPredictorSentinelStaticRuntimeError("Static URL escaped allowlist.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    maximum = int(config["runtime"]["maximum_single_static_download_bytes"])
    total_maximum = int(config["runtime"]["maximum_total_static_download_bytes"])
    existing_total = sum(
        path.stat().st_size
        for path in _inside(root, OUTPUT_ROOT / "static", label="static root").glob(
            "**/*.tif"
        )
        if path.is_file()
    )
    written = 0
    try:
        with requests.get(
            url,
            params=params,
            stream=True,
            timeout=(
                float(config["runtime"]["request_connect_timeout_seconds"]),
                float(config["runtime"]["request_read_timeout_seconds"]),
            ),
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type not in {
                "image/tiff",
                "image/geotiff",
                "application/octet-stream",
            }:
                raise M3BlindPredictorSentinelStaticRuntimeError(
                    f"Unexpected raster content type: {content_type!r}"
                )
            with partial.open("xb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > maximum or existing_total + written > total_maximum:
                        raise M3BlindPredictorSentinelStaticRuntimeError(
                            "Static acquisition exceeded its byte limit."
                        )
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if written == 0:
            raise M3BlindPredictorSentinelStaticRuntimeError("Empty static response.")
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _boundary(root: Path, city_id: str) -> gpd.GeoDataFrame:
    authenticate_launch_authorization(root)
    marker = _read_committed(
        _inside(root, SUPPORT_ROOT / city_id / "SUPPORT_COMPLETE.json", label=city_id),
        label=f"{city_id} support",
    )
    path = _verify_record(root, marker["inputs"]["boundary"], label=f"{city_id} boundary")
    authenticate_launch_authorization(root)
    return gpd.read_parquet(path)


def _static_tasks(root: Path) -> list[dict[str, Any]]:
    authenticate_launch_authorization(root)
    scope = authenticate_scope_authorization(root)
    scope_by_city = {row["city_id"]: row for row in scope["city_scope"]}
    tasks: list[dict[str, Any]] = []
    for city_id in BLIND_CITY_IDS:
        base = _inside(root, OUTPUT_ROOT / "static" / city_id, label="static output")
        tasks.extend(
            [
                {
                    "task_id": f"static:{city_id}:nlcd_land_cover",
                    "kind": "nlcd",
                    "city_id": city_id,
                    "product": "land_cover",
                    "coverage_id": "mrlc_download__NLCD_2016_Land_Cover_L48",
                    "path": base / "nlcd_2016_land_cover.tif",
                },
                {
                    "task_id": f"static:{city_id}:nlcd_impervious",
                    "kind": "nlcd",
                    "city_id": city_id,
                    "product": "impervious",
                    "coverage_id": "mrlc_download__NLCD_2016_Impervious_L48",
                    "path": base / "nlcd_2016_impervious.tif",
                },
            ]
        )
        for tile_id in scope_by_city[city_id]["srtm_tile_ids"]:
            tasks.append(
                {
                    "task_id": f"static:{city_id}:srtm:{tile_id}",
                    "kind": "srtm",
                    "city_id": city_id,
                    "tile_id": tile_id,
                    "path": base / "terrain" / f"{tile_id}.tif",
                }
            )
    tasks.append(
        {
            "task_id": "static:global:gshhg_2_3_7",
            "kind": "gshhg",
            "path": _inside(
                root,
                OUTPUT_ROOT / "static" / "gshhg_2_3_7.AUTHENTICATED",
                label="GSHHG marker",
            ),
        }
    )
    if len(tasks) != STATIC_TASK_COUNT:
        raise M3BlindPredictorSentinelStaticRuntimeError("Static task plan changed.")
    return tasks


def _run_static_task(root: Path, task: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    permit = authenticate_launch_authorization(root)
    destination = Path(task["path"])
    kind = str(task["kind"])
    schema: dict[str, Any]
    if kind == "gshhg":
        scope = authenticate_scope_authorization(root)
        source = scope["source_contract"]["water_distance"]
        archive = _inside(root, source["archive_path"], label="GSHHG archive")
        authenticate_launch_authorization(root)
        if (
            archive.stat().st_size != source["archive_bytes"]
            or sha256_file(archive) != source["archive_sha256"]
        ):
            raise M3BlindPredictorSentinelStaticRuntimeError("GSHHG archive changed.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source["archive_sha256"] + "\n", encoding="ascii")
        schema = {"archive_sha256": source["archive_sha256"]}
    elif kind == "nlcd":
        boundary = _boundary(root, str(task["city_id"]))
        bounds = source_evidence._aligned_nlcd_bounds(
            boundary, resolution=30, edge_offset=15, halo_pixels=2
        )
        query = source_evidence._nlcd_query(str(task["coverage_id"]), bounds)
        if not destination.is_file():
            _download_static(
                root,
                url="https://www.mrlc.gov/geoserver/ows",
                destination=destination,
                params=query,
                config=config,
            )
        authenticate_launch_authorization(root)
        schema = source_evidence._inspect_nlcd(
            destination, product=str(task["product"]), bounds=bounds
        )
    elif kind == "srtm":
        tile_id = str(task["tile_id"])
        url = f"{source_footprints.OPEN_TOPOGRAPHY_SRTM_BASE_URL}/{tile_id}.tif"
        if not destination.is_file():
            _download_static(root, url=url, destination=destination, params=None, config=config)
        authenticate_launch_authorization(root)
        schema = source_evidence._inspect_srtm(destination, tile_id=tile_id)
    else:
        raise M3BlindPredictorSentinelStaticRuntimeError(f"Unknown static task: {kind}")
    marker = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "static_acquisition_task_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "task_id": task["task_id"],
            "output": _record(root, destination),
            "schema": schema,
            "audit": {
                "daymet_landsat_qa_or_target_access": False,
                "model_fit_predict_score_or_evaluate": False,
            },
        }
    )
    _write_exclusive(marker, _static_marker(destination))


def _publish_status(root: Path, payload: Mapping[str, Any]) -> None:
    path = _inside(root, STATUS_PATH, label="status")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(dict(payload), path)


def _canary_complete(root: Path, permit: Mapping[str, Any]) -> bool:
    path = _inside(root, CANARY_PATH, label="canary completion")
    if not path.is_file():
        return False
    try:
        marker = _read_committed(path, label="canary completion")
    except M3BlindPredictorSentinelStaticRuntimeError:
        return False
    return bool(
        marker.get("state") == "m3_blind_sentinel_static_canary_complete"
        and marker.get("authorization_commit_sha256") == permit["commit_sha256"]
        and marker.get("static_task_count") == 1
        and marker.get("sentinel_acquisition_count") == 1
    )


def _status(
    permit: Mapping[str, Any], *, complete: int, running: int, state: str, current: str | None
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "run_id": permit["run_id"],
        "state": state,
        "counts": {
            "pending": TOTAL_TASK_COUNT - complete - running,
            "running": running,
            "complete": complete,
            "total": TOTAL_TASK_COUNT,
        },
        "current_task_id": current,
        "network_allowed": state in {"running", "canary_running"},
        "blind_targets_sealed": True,
        "daymet_access": False,
        "landsat_qa_or_target_access": False,
        "model_fit_predict_score_or_evaluate": False,
    }


def _with_attempts(action: Callable[[], Any], *, maximum_attempts: int) -> Any:
    if maximum_attempts < 1:
        raise ValueError("maximum_attempts must be positive.")
    for attempt in range(1, maximum_attempts + 1):
        try:
            return action()
        except Exception:
            if attempt == maximum_attempts:
                raise
    raise AssertionError("unreachable")


def run(project_root: str | Path, *, canary: bool = False) -> dict[str, Any]:
    """Run one canary pair or resume the complete 553-task acquisition."""

    root = Path(project_root).resolve()
    permit = authenticate_launch_authorization(root)
    config = _load_config(root)
    pause = _inside(root, PAUSE_PATH, label="pause marker")
    if not canary and not _canary_complete(root, permit):
        raise M3BlindPredictorSentinelStaticRuntimeError(
            "Full acquisition requires an authenticated canary completion."
        )
    if pause.exists():
        previous = read_status(root)
        complete = int(previous.get("counts", {}).get("complete", 0))
        result = _status(permit, complete=complete, running=0, state="paused", current=None)
        _publish_status(root, result)
        return result
    static_tasks = _static_tasks(root)
    contexts = _sentinel_contexts(root)
    sentinel_tasks = [
        (city_id, context, row)
        for city_id, context in contexts.items()
        for row in context.inventory.acquisitions.itertuples(index=False)
    ]
    if len(sentinel_tasks) != 539:
        raise M3BlindPredictorSentinelStaticRuntimeError("Sentinel task plan changed.")
    static_done = sum(_static_complete(root, Path(task["path"])) for task in static_tasks)
    sentinel_done = sum(
        _sentinel_complete(root, context, row) for _, context, row in sentinel_tasks
    )
    complete = static_done + sentinel_done
    _publish_status(
        root,
        _status(permit, complete=complete, running=0, state="ready", current=None),
    )
    static_budget = int(config["canary"]["static_task_count"]) if canary else len(static_tasks)
    sentinel_budget = (
        int(config["canary"]["sentinel_acquisition_count"])
        if canary
        else len(sentinel_tasks)
    )
    for task in static_tasks:
        if static_budget <= 0 or pause.exists():
            break
        destination = Path(task["path"])
        if _static_complete(root, destination):
            continue
        current = str(task["task_id"])
        _publish_status(
            root,
            _status(
                permit,
                complete=complete,
                running=1,
                state="canary_running" if canary else "running",
                current=current,
            ),
        )
        try:
            _with_attempts(
                lambda current_task=task: _run_static_task(root, current_task, config),
                maximum_attempts=int(config["runtime"]["maximum_attempts"]),
            )
        except Exception as error:
            failure = _status(
                permit, complete=complete, running=0, state="failed", current=current
            )
            failure["last_error"] = {
                "type": type(error).__name__,
                "message": str(error).replace("\r", " ").replace("\n", " ")[:800],
            }
            _publish_status(root, failure)
            raise
        complete += 1
        static_budget -= 1
    for city_id, context, row in sentinel_tasks:
        if sentinel_budget <= 0 or pause.exists():
            break
        if _sentinel_complete(root, context, row):
            continue
        current = f"sentinel:{city_id}:{row.physical_acquisition_id}"
        _publish_status(
            root,
            _status(
                permit,
                complete=complete,
                running=1,
                state="canary_running" if canary else "running",
                current=current,
            ),
        )
        try:
            _with_attempts(
                lambda current_context=context, current_row=row: _run_sentinel_task(
                    root,
                    current_context,
                    current_row,
                    download_threads=int(config["runtime"]["download_threads"]),
                ),
                maximum_attempts=int(config["runtime"]["maximum_attempts"]),
            )
        except Exception as error:
            failure = _status(
                permit, complete=complete, running=0, state="failed", current=current
            )
            failure["last_error"] = {
                "type": type(error).__name__,
                "message": str(error).replace("\r", " ").replace("\n", " ")[:800],
            }
            _publish_status(root, failure)
            raise
        complete += 1
        sentinel_budget -= 1
    if pause.exists():
        state = "paused"
    elif canary:
        state = "canary_complete"
    elif complete == TOTAL_TASK_COUNT:
        state = "complete"
    else:
        state = "incomplete"
    result = _status(permit, complete=complete, running=0, state=state, current=None)
    _publish_status(root, result)
    if state == "canary_complete":
        _write_exclusive(
            _committed(
                {
                    "schema_version": 1,
                    "algorithm_version": ALGORITHM_VERSION,
                    "state": "m3_blind_sentinel_static_canary_complete",
                    "authorization_commit_sha256": permit["commit_sha256"],
                    "static_task_count": int(config["canary"]["static_task_count"]),
                    "sentinel_acquisition_count": int(
                        config["canary"]["sentinel_acquisition_count"]
                    ),
                    "audit": {
                        "daymet_landsat_qa_or_target_access": False,
                        "model_fit_predict_score_or_evaluate": False,
                    },
                }
            ),
            _inside(root, CANARY_PATH, label="canary completion"),
        )
    if state == "complete":
        completion = _committed(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "m3_blind_predictor_sentinel_static_acquisition_complete",
                "authorization_commit_sha256": permit["commit_sha256"],
                "task_count": TOTAL_TASK_COUNT,
                "counts": result["counts"],
                "audit": {
                    "blind_targets_sealed": True,
                    "daymet_landsat_qa_or_target_access": False,
                    "model_fit_predict_score_or_evaluate": False,
                    "credentials_signed_urls_or_cookies_persisted": False,
                },
                "next_safe_stage": "authorize_offline_sentinel_static_assembly",
            }
        )
        _write_exclusive(completion, _inside(root, COMPLETION_PATH, label="completion"))
    return result


def read_status(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, STATUS_PATH, label="status")
    if not path.is_file():
        return {"state": "not_started", "counts": {"complete": 0, "total": TOTAL_TASK_COUNT}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise M3BlindPredictorSentinelStaticRuntimeError("Status is malformed.")
    return payload
