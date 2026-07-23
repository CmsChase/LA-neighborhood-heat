"""Canonical hashes and atomic writes for reproducible generated artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_frame_sha256(
    frame: pd.DataFrame,
    *,
    sort_by: list[str],
    columns: list[str] | None = None,
) -> str:
    selected = frame if columns is None else frame[columns]
    ordered = selected.sort_values(sort_by, kind="stable").reset_index(drop=True)
    return canonical_sha256(ordered.to_dict("records"))


def geometry_semantic_sha256(frame: gpd.GeoDataFrame) -> str:
    if frame.empty or frame.crs is None:
        raise ValueError("A georeferenced geometry is required for a semantic hash.")
    geometries = sorted(
        shapely.to_wkb(shapely.normalize(geometry)).hex()
        for geometry in frame.geometry
    )
    return canonical_sha256({"crs": frame.crs.to_string(), "geometries": geometries})


def atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    frame.to_csv(temporary, index=False)
    temporary.replace(destination)


def atomic_parquet(frame: pd.DataFrame | gpd.GeoDataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    frame.to_parquet(temporary, index=False)
    temporary.replace(destination)


def atomic_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(destination)


def atomic_text(text: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(destination)


def code_runtime_fingerprint(
    *,
    project_root: Path,
    relative_paths: tuple[str, ...],
    algorithm_version: str,
) -> tuple[str, dict[str, Any]]:
    package_names = (
        "geopandas",
        "numpy",
        "pandas",
        "planetary-computer",
        "pyarrow",
        "pystac",
        "pystac-client",
        "rasterio",
        "shapely",
    )
    packages: dict[str, str] = {}
    for name in package_names:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "MISSING"
    files = {
        relative: sha256_file(project_root / relative)
        for relative in sorted(relative_paths)
    }
    payload: dict[str, Any] = {
        "algorithm_version": algorithm_version,
        "python": platform.python_version(),
        "packages": packages,
        "files": files,
    }
    return canonical_sha256(payload), payload


def parquet_file_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    schema = [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
    return {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(schema),
    }
