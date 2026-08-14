"""Portable, resumable cache for aligned Landsat source-city asset windows.

The cache stores raw integer arrays, not decoded temperatures or QA decisions.  A
single cache can therefore support every preregistered pixel-level ST_QA rule.
Remote and signed URLs are accepted only as call arguments and never serialized.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final
from urllib.parse import urlsplit

import numpy as np
import rasterio
from rasterio import Affine

from la_heat.aligned_landsat import COVERAGE_KEY, REQUIRED_ASSETS, _read_asset_to_grid
from la_heat.grid import FixedGrid
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-asset-cache-v1"
PLAN_FILENAME: Final = "SCENE_PLAN.json"
GLOBAL_COMMIT_FILENAME: Final = "GLOBAL_CACHE_COMMIT.json"
SCENE_COMMIT_FILENAME: Final = "SCENE_COMMIT.json"
COVERAGE_FILENAME: Final = "source_coverage.tif"
COVERAGE_COMMIT_FILENAME: Final = "COVERAGE_COMMIT.json"
CONTENT_COMMIT_SUFFIX: Final = ".CONTENT_COMMIT.json"
PLAN_STATE: Final = "prepared_source_asset_cache"
COMPLETE_STATE: Final = "complete"

FALLBACK_NODATA: Final = {
    "lwir11": 0,
    "qa_pixel": 0,
    "qa": -9999,
    "cdist": -9999,
    "qa_radsat": 0,
}
RAW_VALUE_CONTRACT: Final = {
    "lwir11": {
        "meaning": "surface_temperature_digital_number",
        "scale_kelvin": 0.00341802,
        "offset_kelvin": 149.0,
        "kelvin_to_celsius": 273.15,
    },
    "qa_pixel": {"meaning": "raw_collection_2_qa_pixel_bits"},
    "qa": {"meaning": "surface_temperature_uncertainty", "scale_kelvin": 0.01},
    "cdist": {"meaning": "cloud_distance", "scale_km": 0.01},
    "qa_radsat": {"meaning": "raw_collection_2_radiometric_saturation_bits"},
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCENE_REQUIRED_FIELDS: Final = (
    "city_id",
    "scene_id",
    "overpass_id",
    "target_date",
    "platform",
)
_SCENE_OPTIONAL_FIELDS: Final = (
    "acquired_utc",
    "wrs_path",
    "wrs_row",
    "scene_order",
)
_LOCK_GUARD = threading.Lock()
_SCENE_LOCKS: dict[str, threading.Lock] = {}


class M3SourceAssetCacheError(RuntimeError):
    """Raised when a portable source cache is incomplete or has drifted."""


def _require_identifier(value: object, *, label: str) -> str:
    text = str(value)
    if not _IDENTIFIER.fullmatch(text):
        raise M3SourceAssetCacheError(f"{label} is not a safe identifier: {text!r}")
    return text


def _committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _atomic_json(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceAssetCacheError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict) or not _committed(payload):
        raise M3SourceAssetCacheError(f"{label} commit is invalid: {path}")
    return payload


def _grid_record(grid: FixedGrid) -> dict[str, Any]:
    return {
        "crs": grid.crs,
        "resolution_m": grid.resolution_m,
        "anchor_x_m": grid.anchor_x_m,
        "anchor_y_m": grid.anchor_y_m,
        "left": grid.left,
        "bottom": grid.bottom,
        "right": grid.right,
        "top": grid.top,
        "width": grid.width,
        "height": grid.height,
        "transform": [
            grid.transform.a,
            grid.transform.b,
            grid.transform.c,
            grid.transform.d,
            grid.transform.e,
            grid.transform.f,
        ],
        "sha256": grid.sha256,
    }


def _grid_from_record(record: Mapping[str, Any]) -> FixedGrid:
    transform_values = record.get("transform")
    if not isinstance(transform_values, list) or len(transform_values) != 6:
        raise M3SourceAssetCacheError("Grid transform must contain six coefficients.")
    grid = FixedGrid(
        crs=str(record["crs"]),
        resolution_m=float(record["resolution_m"]),
        anchor_x_m=float(record["anchor_x_m"]),
        anchor_y_m=float(record["anchor_y_m"]),
        left=float(record["left"]),
        bottom=float(record["bottom"]),
        right=float(record["right"]),
        top=float(record["top"]),
        width=int(record["width"]),
        height=int(record["height"]),
        transform=Affine(*(float(value) for value in transform_values)),
    )
    if grid.sha256 != record.get("sha256"):
        raise M3SourceAssetCacheError("Grid definition does not reproduce its SHA-256.")
    if grid.width <= 0 or grid.height <= 0 or grid.shape != (
        int(record["height"]),
        int(record["width"]),
    ):
        raise M3SourceAssetCacheError("Grid dimensions are invalid.")
    return grid


def _normalize_scene_record(value: Mapping[str, Any], *, ordinal: int) -> dict[str, Any]:
    missing = set(_SCENE_REQUIRED_FIELDS) - set(value)
    if missing:
        raise M3SourceAssetCacheError(f"Scene record lacks fields: {sorted(missing)}")
    record: dict[str, Any] = {
        "ordinal": ordinal,
        "city_id": _require_identifier(value["city_id"], label="city_id"),
        "scene_id": _require_identifier(value["scene_id"], label="scene_id"),
        "overpass_id": _require_identifier(value["overpass_id"], label="overpass_id"),
        "target_date": str(value["target_date"]),
        "platform": _require_identifier(value["platform"], label="platform"),
    }
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["target_date"]):
        raise M3SourceAssetCacheError("target_date must use YYYY-MM-DD.")
    for key in _SCENE_OPTIONAL_FIELDS:
        if key in value and value[key] is not None:
            if "://" in str(value[key]):
                raise M3SourceAssetCacheError("Scene metadata may not contain a URL.")
            record[key] = value[key]
    return record


def build_scene_plan(
    scene_records: Sequence[Mapping[str, Any]],
    *,
    grids: Mapping[str, FixedGrid],
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    """Build a deterministic metadata-only plan for selected source scenes."""

    if not scene_records or not grids or not bindings:
        raise M3SourceAssetCacheError("Scenes, city grids, and bindings are required.")
    normalized = [
        _normalize_scene_record(value, ordinal=index)
        for index, value in enumerate(scene_records, start=1)
    ]
    identities = [record["scene_id"] for record in normalized]
    if len(identities) != len(set(identities)):
        raise M3SourceAssetCacheError("Scene IDs must be globally unique in one plan.")
    normalized_grids = {
        _require_identifier(city_id, label="grid city_id"): _grid_record(grid)
        for city_id, grid in sorted(grids.items())
    }
    if set(record["city_id"] for record in normalized) != set(normalized_grids):
        raise M3SourceAssetCacheError("Scene cities and grid cities must match exactly.")
    normalized_bindings: dict[str, str] = {}
    for key, value in sorted(bindings.items()):
        digest = str(value)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise M3SourceAssetCacheError(f"Binding {key!r} must be a lowercase SHA-256.")
        normalized_bindings[str(key)] = digest
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": PLAN_STATE,
        "required_assets": list(REQUIRED_ASSETS),
        "alignment": {
            "resampling": "nearest",
            "stored_values": "raw_integer_arrays_before_scientific_scaling_or_qa",
            "source_coverage_stored_separately": True,
        },
        "raw_value_contract": RAW_VALUE_CONTRACT,
        "bindings": normalized_bindings,
        "grids": normalized_grids,
        "scene_count": len(normalized),
        "content_task_count": len(normalized) * len(REQUIRED_ASSETS),
        "scenes": normalized,
        "remote_hrefs_signed_urls_tokens_or_cookies_persisted": False,
    }
    return _with_commit(payload)


def authenticate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Authenticate a plan without opening any raster value."""

    result = dict(plan)
    if not _committed(result):
        raise M3SourceAssetCacheError("Scene plan commit is invalid.")
    if (
        result.get("schema_version") != SCHEMA_VERSION
        or result.get("algorithm_version") != ALGORITHM_VERSION
        or result.get("state") != PLAN_STATE
        or tuple(result.get("required_assets", ())) != REQUIRED_ASSETS
        or result.get("remote_hrefs_signed_urls_tokens_or_cookies_persisted") is not False
        or result.get("raw_value_contract") != RAW_VALUE_CONTRACT
        or result.get("alignment")
        != {
            "resampling": "nearest",
            "stored_values": "raw_integer_arrays_before_scientific_scaling_or_qa",
            "source_coverage_stored_separately": True,
        }
    ):
        raise M3SourceAssetCacheError("Scene plan contract changed.")
    scenes = result.get("scenes")
    grids = result.get("grids")
    if not isinstance(scenes, list) or not isinstance(grids, dict) or not scenes:
        raise M3SourceAssetCacheError("Scene plan has no scenes or grids.")
    bindings = result.get("bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise M3SourceAssetCacheError("Scene plan has no immutable input bindings.")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in bindings.values()
    ):
        raise M3SourceAssetCacheError("Scene plan binding is not a lowercase SHA-256.")
    for city_id, grid_record in grids.items():
        _require_identifier(city_id, label="grid city_id")
        if not isinstance(grid_record, dict):
            raise M3SourceAssetCacheError("Grid records must be mappings.")
        _grid_from_record(grid_record)
    expected = [
        _normalize_scene_record(value, ordinal=index)
        for index, value in enumerate(scenes, start=1)
    ]
    if scenes != expected:
        raise M3SourceAssetCacheError("Scene records or their order changed.")
    if len({record["scene_id"] for record in scenes}) != len(scenes):
        raise M3SourceAssetCacheError("Scene plan contains duplicate scene IDs.")
    if set(record["city_id"] for record in scenes) != set(grids):
        raise M3SourceAssetCacheError("Scene/grid city membership changed.")
    if result.get("scene_count") != len(scenes) or result.get("content_task_count") != (
        len(scenes) * len(REQUIRED_ASSETS)
    ):
        raise M3SourceAssetCacheError("Scene plan counts changed.")
    return result


def write_scene_plan(cache_root: str | Path, plan: Mapping[str, Any]) -> Path:
    authenticated = authenticate_plan(plan)
    root = Path(cache_root).resolve()
    path = root / PLAN_FILENAME
    if path.exists():
        observed = authenticate_plan(json.loads(path.read_text(encoding="utf-8")))
        if observed != authenticated:
            raise M3SourceAssetCacheError("Existing scene plan differs from requested plan.")
        return path
    _atomic_json(authenticated, path)
    return path


def load_scene_plan(cache_root: str | Path) -> dict[str, Any]:
    root = Path(cache_root).resolve()
    try:
        payload = json.loads((root / PLAN_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceAssetCacheError("Cannot read the local scene plan.") from error
    if not isinstance(payload, dict):
        raise M3SourceAssetCacheError("Local scene plan is not a mapping.")
    return authenticate_plan(payload)


def resolve_local_cache_path(cache_root: str | Path, value: object) -> Path:
    """Resolve a manifest path while rejecting every URL and absolute path."""

    text = str(value)
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc or "\\" in text:
        raise M3SourceAssetCacheError("Offline cache records may contain only local paths.")
    pure = PurePosixPath(text)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourceAssetCacheError("Offline cache path is absolute or escapes its root.")
    root = Path(cache_root).resolve()
    resolved = (root / Path(*pure.parts)).resolve()
    if not resolved.is_relative_to(root):
        raise M3SourceAssetCacheError("Offline cache path escapes its root.")
    return resolved


def _scene_lookup(plan: Mapping[str, Any], scene_id: str) -> tuple[dict[str, Any], FixedGrid]:
    authenticated = authenticate_plan(plan)
    matches = [row for row in authenticated["scenes"] if row["scene_id"] == scene_id]
    if len(matches) != 1:
        raise M3SourceAssetCacheError(f"Unknown or duplicated scene: {scene_id!r}")
    scene = dict(matches[0])
    grid = _grid_from_record(authenticated["grids"][scene["city_id"]])
    return scene, grid


def _scene_directory(cache_root: Path, scene: Mapping[str, Any]) -> Path:
    return cache_root / "cities" / str(scene["city_id"]) / "scenes" / str(scene["scene_id"])


def _relative(cache_root: Path, path: Path) -> str:
    return path.resolve().relative_to(cache_root.resolve()).as_posix()


def _scene_lock(scene_directory: Path) -> threading.Lock:
    key = str(scene_directory.resolve())
    with _LOCK_GUARD:
        return _SCENE_LOCKS.setdefault(key, threading.Lock())


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    if contiguous.dtype.itemsize > 1:
        contiguous = contiguous.astype(contiguous.dtype.newbyteorder("<"), copy=False)
    header = json.dumps(
        {"dtype": contiguous.dtype.str, "shape": list(contiguous.shape)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(b"\0")
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _write_tiff(path: Path, array: np.ndarray, *, grid: FixedGrid, nodata: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in path.parent.glob(f".{path.name}.*.part"):
        stale.unlink(missing_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": array.dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "compress": "DEFLATE",
        "predictor": 2,
    }
    if min(grid.shape) >= 16:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    try:
        with rasterio.open(temporary, "w", **profile) as target:
            target.write(array, 1)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _tiff_record(
    cache_root: Path,
    path: Path,
    array: np.ndarray,
    grid: FixedGrid,
) -> dict[str, Any]:
    with rasterio.open(path) as source:
        nodata = source.nodata
    return {
        "path": _relative(cache_root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "array_semantic_sha256": _array_sha256(array),
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "crs": grid.crs,
        "nodata": nodata,
        "transform": [
            grid.transform.a,
            grid.transform.b,
            grid.transform.c,
            grid.transform.d,
            grid.transform.e,
            grid.transform.f,
        ],
    }


def _read_tiff_record(cache_root: Path, record: Mapping[str, Any], grid: FixedGrid) -> np.ndarray:
    path = resolve_local_cache_path(cache_root, record.get("path"))
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourceAssetCacheError(f"Cached raster failed its file lock: {path}")
    with rasterio.open(path) as source:
        if (
            record.get("crs") != grid.crs
            or record.get("transform")
            != [
                grid.transform.a,
                grid.transform.b,
                grid.transform.c,
                grid.transform.d,
                grid.transform.e,
                grid.transform.f,
            ]
            or source.count != 1
            or source.crs is None
            or source.crs.to_string() != grid.crs
            or source.transform != grid.transform
            or (source.height, source.width) != grid.shape
            or source.nodata != record.get("nodata")
        ):
            raise M3SourceAssetCacheError(f"Cached raster grid changed: {path}")
        array = source.read(1)
    if (
        str(array.dtype) != record.get("dtype")
        or list(array.shape) != record.get("shape")
        or _array_sha256(array) != record.get("array_semantic_sha256")
    ):
        raise M3SourceAssetCacheError(f"Cached raster semantic content changed: {path}")
    return array


def _coverage_commit_path(scene_directory: Path) -> Path:
    return scene_directory / COVERAGE_COMMIT_FILENAME


def _content_commit_path(scene_directory: Path, asset: str) -> Path:
    return scene_directory / "assets" / f"{asset}{CONTENT_COMMIT_SUFFIX}"


def _authenticate_coverage(
    cache_root: Path,
    scene_directory: Path,
    *,
    plan_commit_sha256: str,
    scene_id: str,
    grid: FixedGrid,
) -> tuple[dict[str, Any], np.ndarray]:
    commit = _read_json(_coverage_commit_path(scene_directory), label="coverage")
    if (
        commit.get("plan_commit_sha256") != plan_commit_sha256
        or commit.get("scene_id") != scene_id
        or commit.get("grid_sha256") != grid.sha256
    ):
        raise M3SourceAssetCacheError("Coverage belongs to another scene, grid, or plan.")
    record = commit.get("output_file")
    if not isinstance(record, dict):
        raise M3SourceAssetCacheError("Coverage commit lacks its output record.")
    array = _read_tiff_record(cache_root, record, grid)
    if array.dtype != np.uint8 or not np.isin(array, (0, 1)).all():
        raise M3SourceAssetCacheError("Coverage cache must be a binary uint8 raster.")
    return commit, array.astype(bool)


def _ensure_coverage(
    cache_root: Path,
    scene_directory: Path,
    *,
    plan_commit_sha256: str,
    scene_id: str,
    grid: FixedGrid,
    coverage: np.ndarray,
) -> dict[str, Any]:
    path = _coverage_commit_path(scene_directory)
    if path.exists():
        commit, observed = _authenticate_coverage(
            cache_root,
            scene_directory,
            plan_commit_sha256=plan_commit_sha256,
            scene_id=scene_id,
            grid=grid,
        )
        if not np.array_equal(observed, coverage):
            raise M3SourceAssetCacheError("Landsat assets do not share one source coverage.")
        return commit
    coverage_array = np.asarray(coverage, dtype=np.uint8)
    coverage_path = scene_directory / COVERAGE_FILENAME
    _write_tiff(coverage_path, coverage_array, grid=grid, nodata=0)
    commit = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": COMPLETE_STATE,
            "plan_commit_sha256": plan_commit_sha256,
            "scene_id": scene_id,
            "grid_sha256": grid.sha256,
            "output_file": _tiff_record(cache_root, coverage_path, coverage_array, grid),
        }
    )
    _atomic_json(commit, path)
    return commit


def _authenticate_content(
    cache_root: Path,
    scene_directory: Path,
    *,
    plan_commit_sha256: str,
    scene_id: str,
    asset: str,
    grid: FixedGrid,
) -> tuple[dict[str, Any], np.ndarray]:
    commit = _read_json(_content_commit_path(scene_directory, asset), label=asset)
    if (
        commit.get("plan_commit_sha256") != plan_commit_sha256
        or commit.get("scene_id") != scene_id
        or commit.get("asset") != asset
        or commit.get("grid_sha256") != grid.sha256
    ):
        raise M3SourceAssetCacheError("Asset content belongs to another scene, grid, or plan.")
    record = commit.get("output_file")
    if not isinstance(record, dict):
        raise M3SourceAssetCacheError("Asset content commit lacks its output record.")
    return commit, _read_tiff_record(cache_root, record, grid)


def _authenticate_scene(
    cache_root: Path,
    plan: Mapping[str, Any],
    scene: Mapping[str, Any],
    grid: FixedGrid,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    scene_directory = _scene_directory(cache_root, scene)
    commit = _read_json(scene_directory / SCENE_COMMIT_FILENAME, label="scene")
    if (
        commit.get("plan_commit_sha256") != plan["commit_sha256"]
        or commit.get("scene") != dict(scene)
        or commit.get("grid_sha256") != grid.sha256
        or tuple(commit.get("required_assets", ())) != REQUIRED_ASSETS
    ):
        raise M3SourceAssetCacheError("Scene cache belongs to another plan or grid.")
    coverage_commit, coverage = _authenticate_coverage(
        cache_root,
        scene_directory,
        plan_commit_sha256=str(plan["commit_sha256"]),
        scene_id=str(scene["scene_id"]),
        grid=grid,
    )
    if commit.get("coverage_commit_sha256") != coverage_commit["commit_sha256"]:
        raise M3SourceAssetCacheError("Scene coverage commit changed.")
    arrays: dict[str, np.ndarray] = {}
    content_commits: list[str] = []
    for asset in REQUIRED_ASSETS:
        content, array = _authenticate_content(
            cache_root,
            scene_directory,
            plan_commit_sha256=str(plan["commit_sha256"]),
            scene_id=str(scene["scene_id"]),
            asset=asset,
            grid=grid,
        )
        if content.get("coverage_commit_sha256") != coverage_commit["commit_sha256"]:
            raise M3SourceAssetCacheError("Asset content references another coverage cache.")
        content_commits.append(str(content["commit_sha256"]))
        arrays[asset] = array
    if commit.get("content_commit_sha256s") != content_commits:
        raise M3SourceAssetCacheError("Scene content commit order or identity changed.")
    arrays[COVERAGE_KEY] = coverage
    return commit, arrays


def cache_scene_from_hrefs(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    scene_id: str,
    asset_hrefs: Mapping[str, str],
    *,
    before_value_access: Callable[[], None],
    signer: Callable[[str], str] = lambda value: value,
) -> dict[str, Any]:
    """Cache one scene; URL strings and signed query parameters remain in memory."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    scene, grid = _scene_lookup(authenticated, scene_id)
    if set(asset_hrefs) != set(REQUIRED_ASSETS):
        raise M3SourceAssetCacheError("Scene href mapping must contain the exact five assets.")
    root = Path(cache_root).resolve()
    scene_directory = _scene_directory(root, scene)
    scene_commit_path = scene_directory / SCENE_COMMIT_FILENAME
    if scene_commit_path.exists():
        commit, _ = _authenticate_scene(root, authenticated, scene, grid)
        return commit
    for asset in REQUIRED_ASSETS:
        cache_asset_from_href(
            root,
            authenticated,
            scene_id,
            asset,
            str(asset_hrefs[asset]),
            before_value_access=lambda: None,
            signer=signer,
        )
    return finalize_scene_cache(
        root,
        authenticated,
        scene_id,
        before_value_access=lambda: None,
    )


def cache_asset_from_href(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    scene_id: str,
    asset: str,
    href: str,
    *,
    before_value_access: Callable[[], None],
    signer: Callable[[str], str] = lambda value: value,
) -> dict[str, Any]:
    """Build or authenticate one durable scene/asset content task."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    if asset not in REQUIRED_ASSETS:
        raise M3SourceAssetCacheError(f"Unknown Landsat source asset: {asset!r}")
    scene, grid = _scene_lookup(authenticated, scene_id)
    root = Path(cache_root).resolve()
    scene_directory = _scene_directory(root, scene)
    with _scene_lock(scene_directory):
        content_path = _content_commit_path(scene_directory, asset)
        if content_path.exists():
            content, _ = _authenticate_content(
                root,
                scene_directory,
                plan_commit_sha256=str(authenticated["commit_sha256"]),
                scene_id=scene_id,
                asset=asset,
                grid=grid,
            )
            coverage, _ = _authenticate_coverage(
                root,
                scene_directory,
                plan_commit_sha256=str(authenticated["commit_sha256"]),
                scene_id=scene_id,
                grid=grid,
            )
            if content.get("coverage_commit_sha256") != coverage["commit_sha256"]:
                raise M3SourceAssetCacheError("Asset content coverage binding changed.")
            return content

        signed_href = signer(str(href))
        if not isinstance(signed_href, str) or not signed_href:
            raise M3SourceAssetCacheError("Signer did not return a usable in-memory href.")
        array, coverage_array = _read_asset_to_grid(
            signed_href,
            grid=grid,
            fallback_nodata=FALLBACK_NODATA[asset],
        )
        if array.shape != grid.shape or not np.issubdtype(array.dtype, np.integer):
            raise M3SourceAssetCacheError("Aligned Landsat content must be a 2D integer grid.")
        coverage = _ensure_coverage(
            root,
            scene_directory,
            plan_commit_sha256=str(authenticated["commit_sha256"]),
            scene_id=scene_id,
            grid=grid,
            coverage=coverage_array,
        )
        output_path = scene_directory / "assets" / f"{asset}.tif"
        _write_tiff(
            output_path,
            array,
            grid=grid,
            nodata=FALLBACK_NODATA[asset],
        )
        content = _with_commit(
            {
                "schema_version": SCHEMA_VERSION,
                "algorithm_version": ALGORITHM_VERSION,
                "state": COMPLETE_STATE,
                "plan_commit_sha256": authenticated["commit_sha256"],
                "scene_id": scene_id,
                "asset": asset,
                "grid_sha256": grid.sha256,
                "alignment": "nearest_to_frozen_city_grid",
                "scientific_scaling_applied": False,
                "coverage_commit_sha256": coverage["commit_sha256"],
                "output_file": _tiff_record(root, output_path, array, grid),
                "remote_href_or_signed_url_persisted": False,
            }
        )
        _atomic_json(content, content_path)
        return content


def finalize_scene_cache(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    scene_id: str,
    *,
    before_value_access: Callable[[], None],
) -> dict[str, Any]:
    """Commit a scene only after all five content tasks authenticate."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    scene, grid = _scene_lookup(authenticated, scene_id)
    root = Path(cache_root).resolve()
    scene_directory = _scene_directory(root, scene)
    with _scene_lock(scene_directory):
        scene_commit_path = scene_directory / SCENE_COMMIT_FILENAME
        if scene_commit_path.exists():
            commit, _ = _authenticate_scene(root, authenticated, scene, grid)
            return commit
        coverage, _ = _authenticate_coverage(
            root,
            scene_directory,
            plan_commit_sha256=str(authenticated["commit_sha256"]),
            scene_id=scene_id,
            grid=grid,
        )
        content_commits: list[str] = []
        for asset in REQUIRED_ASSETS:
            content, _ = _authenticate_content(
                root,
                scene_directory,
                plan_commit_sha256=str(authenticated["commit_sha256"]),
                scene_id=scene_id,
                asset=asset,
                grid=grid,
            )
            if content.get("coverage_commit_sha256") != coverage["commit_sha256"]:
                raise M3SourceAssetCacheError("Asset content coverage binding changed.")
            content_commits.append(str(content["commit_sha256"]))
        commit = _with_commit(
            {
                "schema_version": SCHEMA_VERSION,
                "algorithm_version": ALGORITHM_VERSION,
                "state": COMPLETE_STATE,
                "plan_commit_sha256": authenticated["commit_sha256"],
                "scene": scene,
                "grid_sha256": grid.sha256,
                "required_assets": list(REQUIRED_ASSETS),
                "content_commit_sha256s": content_commits,
                "coverage_commit_sha256": coverage["commit_sha256"],
                "remote_hrefs_signed_urls_tokens_or_cookies_persisted": False,
            }
        )
        _atomic_json(commit, scene_commit_path)
        return commit


def load_local_scene_arrays(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    scene_id: str,
    *,
    before_value_access: Callable[[], None],
) -> dict[str, np.ndarray]:
    """Load an authenticated local scene without resolving or signing any URL."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    scene, grid = _scene_lookup(authenticated, scene_id)
    _, arrays = _authenticate_scene(Path(cache_root).resolve(), authenticated, scene, grid)
    return arrays


def finalize_global_cache(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    *,
    before_value_access: Callable[[], None],
) -> dict[str, Any]:
    """Commit the exact ordered set of authenticated scene caches."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    root = Path(cache_root).resolve()
    scene_commits: list[dict[str, str]] = []
    for scene in authenticated["scenes"]:
        grid = _grid_from_record(authenticated["grids"][scene["city_id"]])
        commit, _ = _authenticate_scene(root, authenticated, scene, grid)
        scene_commits.append(
            {
                "city_id": str(scene["city_id"]),
                "scene_id": str(scene["scene_id"]),
                "commit_sha256": str(commit["commit_sha256"]),
            }
        )
    payload = _with_commit(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": COMPLETE_STATE,
            "plan": {
                "path": PLAN_FILENAME,
                "commit_sha256": authenticated["commit_sha256"],
            },
            "scene_count": len(scene_commits),
            "content_count": len(scene_commits) * len(REQUIRED_ASSETS),
            "scene_commits": scene_commits,
            "local_only": True,
            "remote_hrefs_signed_urls_tokens_or_cookies_persisted": False,
        }
    )
    destination = root / GLOBAL_COMMIT_FILENAME
    if destination.exists():
        observed = authenticate_global_cache(
            root,
            authenticated,
            before_value_access=lambda: None,
        )
        if observed != payload:
            raise M3SourceAssetCacheError("Existing global cache commit differs.")
        return observed
    _atomic_json(payload, destination)
    return authenticate_global_cache(
        root,
        authenticated,
        before_value_access=lambda: None,
    )


def authenticate_global_cache(
    cache_root: str | Path,
    plan: Mapping[str, Any],
    *,
    before_value_access: Callable[[], None],
) -> dict[str, Any]:
    """Authenticate every local file and commit in one portable cache."""

    before_value_access()
    authenticated = authenticate_plan(plan)
    root = Path(cache_root).resolve()
    commit = _read_json(root / GLOBAL_COMMIT_FILENAME, label="global cache")
    if (
        commit.get("state") != COMPLETE_STATE
        or commit.get("local_only") is not True
        or commit.get("remote_hrefs_signed_urls_tokens_or_cookies_persisted") is not False
        or commit.get("plan")
        != {"path": PLAN_FILENAME, "commit_sha256": authenticated["commit_sha256"]}
    ):
        raise M3SourceAssetCacheError("Global cache contract or plan binding changed.")
    plan_path = resolve_local_cache_path(root, commit["plan"]["path"])
    try:
        stored_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceAssetCacheError("Global cache cannot read its local plan.") from error
    if not isinstance(stored_plan, dict) or authenticate_plan(stored_plan) != authenticated:
        raise M3SourceAssetCacheError("Global cache local plan changed.")
    expected: list[dict[str, str]] = []
    for scene in authenticated["scenes"]:
        grid = _grid_from_record(authenticated["grids"][scene["city_id"]])
        scene_commit, _ = _authenticate_scene(root, authenticated, scene, grid)
        expected.append(
            {
                "city_id": str(scene["city_id"]),
                "scene_id": str(scene["scene_id"]),
                "commit_sha256": str(scene_commit["commit_sha256"]),
            }
        )
    if (
        commit.get("scene_commits") != expected
        or commit.get("scene_count") != len(expected)
        or commit.get("content_count") != len(expected) * len(REQUIRED_ASSETS)
    ):
        raise M3SourceAssetCacheError("Global cache scene set or counts changed.")
    return commit
