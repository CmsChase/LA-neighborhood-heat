"""Target-blind calendar and static components for the four-city build.

The functions in this module consume only the frozen predictor-key inventory,
canonical Census/WorldCover support, and public static sources.  They never
open Landsat target, target-QA, prediction, model, or evaluation artifacts.

The static build is deliberately split into three resumable operations:

``build_static_base_component``
    Reproject NLCD and SRTM to the canonical 30 m support and cache arrays.
``build_gshhg_distance_component``
    Compute exact projected nearest-line distances in atomic 100,000-cell
    chunks.  A cooperative pause is honored only after a chunk is durable.
``finalize_static_component``
    Aggregate the cached arrays and distances to the frozen tract support.
"""

from __future__ import annotations

import json
import math
import os
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import CRS
from rasterio import Affine
from rasterio.enums import Resampling

from la_heat.calendar_features import build_calendar_features
from la_heat.grid import FixedGrid
from la_heat.multicity.gshhg_geometry_pilot import (
    gshhg_l1_exterior_linework,
    repair_predeclared_l1_geometry,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.static_features import (
    StaticArrays,
    StaticSupport,
    _reproject_mosaic,
    _reproject_values,
    aggregate_static_features,
    build_static_support,
    horn_slope_degrees,
)

CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
DISTANCE_CHUNK_SIZE: Final = 100_000
COMPONENT_ROOT: Final = Path(
    "data/processed/multicity/portable_predictors/components"
)
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/portable_predictors/runtime/components"
)
INVENTORY_MANIFEST: Final = Path(
    "manifests/multicity/predictors/PORTABLE_PREDICTOR_INVENTORY.json"
)
PORTABLE_CONTRACT: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
GSHHG_FREEZE: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
GSHHG_REPAIR_CONFIG: Final = Path(
    "configs/multicity/gshhg_geometry_pilot_v2.toml"
)

ProgressCallback = Callable[[dict[str, Any]], None]
PauseCallback = Callable[[], bool]


class PortablePredictorComponentError(ValueError):
    """Raised when a portable component cannot prove its frozen inputs."""


@dataclass(frozen=True, slots=True)
class PortableCitySupport:
    """Canonical geography and WorldCover support for one city."""

    city_id: str
    grid: FixedGrid
    zones: np.ndarray
    eligible_land: np.ndarray
    tract_geoids: tuple[str, ...]
    tracts: gpd.GeoDataFrame
    static_support: StaticSupport
    worldcover_manifest: dict[str, Any]
    geography_manifest: dict[str, Any]

    @property
    def eligible_mask(self) -> np.ndarray:
        """Compatibility alias used by other portable component builders."""

        return self.eligible_land

    @property
    def transform(self) -> Affine:
        return self.grid.transform

    @property
    def crs(self) -> str:
        return self.grid.crs


@dataclass(frozen=True, slots=True)
class StaticSourcePaths:
    land_cover: Path
    impervious: Path
    terrain: tuple[Path, ...]
    records: tuple[dict[str, Any], ...]


def _project_root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise PortablePredictorComponentError(f"Path escapes project root: {value}")
    return resolved


def _read_committed_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortablePredictorComponentError(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise PortablePredictorComponentError(f"{label} commit changed: {path}")
    return payload


def _verify_record(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _resolve(root, str(record.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError(path)
    if record.get("bytes") is not None and path.stat().st_size != int(record["bytes"]):
        raise PortablePredictorComponentError(f"{label} byte count changed: {path}")
    expected_sha = record.get("sha256")
    if isinstance(expected_sha, str) and sha256_file(path) != expected_sha:
        raise PortablePredictorComponentError(f"{label} SHA-256 changed: {path}")
    return path


def _grid_from_manifest(record: Mapping[str, Any]) -> FixedGrid:
    bounds = [float(value) for value in record.get("bounds", [])]
    shape = [int(value) for value in record.get("shape", [])]
    transform_values = [float(value) for value in record.get("transform", [])]
    if len(bounds) != 4 or len(shape) != 2 or len(transform_values) < 6:
        raise PortablePredictorComponentError("WorldCover grid record is malformed.")
    grid = FixedGrid(
        crs=str(record["crs"]),
        resolution_m=float(record["resolution_m"]),
        anchor_x_m=float(record["anchor_x_m"]),
        anchor_y_m=float(record["anchor_y_m"]),
        left=bounds[0],
        bottom=bounds[1],
        right=bounds[2],
        top=bounds[3],
        width=shape[1],
        height=shape[0],
        transform=Affine(*transform_values[:6]),
    )
    if grid.sha256 != record.get("sha256"):
        raise PortablePredictorComponentError("Canonical grid definition hash changed.")
    return grid


def _read_locked_raster(
    path: Path,
    *,
    grid: FixedGrid,
    expected_dtype: str,
) -> np.ndarray:
    with rasterio.open(path) as source:
        if (
            source.shape != grid.shape
            or source.crs is None
            or not CRS.from_user_input(source.crs).equals(CRS.from_user_input(grid.crs))
            or source.transform != grid.transform
            or source.dtypes[0] != expected_dtype
        ):
            raise PortablePredictorComponentError(
                f"Canonical support raster grid changed: {path}"
            )
        return source.read(1)


def load_city_support(
    project_root: str | Path,
    city_id: str,
) -> PortableCitySupport:
    """Load and authenticate one canonical geography/WorldCover support.

    GEOIDs are sorted exactly as the zone raster was constructed.  The function
    reads no target or QA path and accepts only the four frozen city IDs.
    """

    if city_id not in CITY_IDS:
        raise PortablePredictorComponentError(f"Unknown portable city: {city_id}")
    root = _project_root(project_root)
    geography_path = root / (
        f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
    )
    worldcover_path = root / (
        f"manifests/multicity/cities/{city_id}/eligible_support/"
        "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
    )
    geography = _read_committed_json(geography_path, label=f"{city_id} geography")
    worldcover = _read_committed_json(worldcover_path, label=f"{city_id} WorldCover")
    if (
        geography.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or worldcover.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or worldcover.get("city_id") != city_id
    ):
        raise PortablePredictorComponentError(
            f"Canonical support access contract changed for {city_id}."
        )

    primary_path = _verify_record(
        root,
        geography["output_tables"]["primary_tracts"],
        label=f"{city_id} primary tracts",
    )
    support_path = _verify_record(
        root,
        worldcover["outputs"]["tract_support"],
        label=f"{city_id} tract support",
    )
    zone_path = _verify_record(
        root,
        worldcover["outputs"]["tract_zones_30m"],
        label=f"{city_id} zone raster",
    )
    eligible_path = _verify_record(
        root,
        worldcover["outputs"]["eligible_mask_30m"],
        label=f"{city_id} eligible raster",
    )

    tracts = gpd.read_parquet(primary_path)
    if tracts.empty or tracts.crs is None or "tract_geoid" not in tracts:
        raise PortablePredictorComponentError(f"Canonical tracts are invalid for {city_id}.")
    tracts = tracts.assign(
        tract_geoid=tracts["tract_geoid"].astype("string")
    ).sort_values("tract_geoid", kind="stable").reset_index(drop=True)
    if tracts["tract_geoid"].duplicated().any():
        raise PortablePredictorComponentError(f"Duplicate canonical GEOIDs for {city_id}.")
    tract_geoids = tuple(tracts["tract_geoid"].astype(str))

    support_table = pd.read_parquet(support_path).sort_values(
        "tract_geoid", kind="stable"
    )
    support_geoids = tuple(support_table["tract_geoid"].astype(str))
    if tract_geoids != support_geoids:
        raise PortablePredictorComponentError(
            f"Geography and WorldCover GEOIDs disagree for {city_id}."
        )

    grid = _grid_from_manifest(worldcover["grid"])
    zones = _read_locked_raster(zone_path, grid=grid, expected_dtype="int32")
    eligible_raw = _read_locked_raster(
        eligible_path, grid=grid, expected_dtype="uint8"
    )
    if not set(np.unique(eligible_raw)).issubset({0, 1}):
        raise PortablePredictorComponentError("Eligible support is not a binary raster.")
    eligible_land = eligible_raw.astype(bool)
    if np.any(eligible_land & (zones <= 0)):
        raise PortablePredictorComponentError("Eligible cells occur outside tract zones.")
    present_zones = np.unique(zones[zones > 0])
    if not np.array_equal(present_zones, np.arange(1, len(tract_geoids) + 1)):
        raise PortablePredictorComponentError("Canonical zone IDs changed.")

    counts = np.bincount(zones[eligible_land], minlength=len(tract_geoids) + 1)[1:]
    recorded_counts = support_table["eligible_cell_count"].to_numpy(dtype=np.int64)
    if not np.array_equal(counts, recorded_counts):
        raise PortablePredictorComponentError("Canonical eligible-cell counts changed.")
    city_support_identity = str(
        support_table["city_support_identity_sha256"].astype(str).iloc[0]
    )
    if support_table["city_support_identity_sha256"].astype(str).nunique() != 1:
        raise PortablePredictorComponentError("City support identity is not unique.")
    static_support = build_static_support(
        zones,
        eligible_land,
        geoids=tract_geoids,
        grid_identity=city_support_identity,
    )
    if not np.array_equal(static_support.counts, recorded_counts):
        raise PortablePredictorComponentError("Static support changed eligible counts.")
    return PortableCitySupport(
        city_id=city_id,
        grid=grid,
        zones=zones,
        eligible_land=eligible_land,
        tract_geoids=tract_geoids,
        tracts=tracts,
        static_support=static_support,
        worldcover_manifest=worldcover,
        geography_manifest=geography,
    )


def _city_inventory_keys(root: Path, city_id: str) -> tuple[pd.DataFrame, Path]:
    inventory = _read_committed_json(
        root / INVENTORY_MANIFEST,
        label="portable predictor inventory",
    )
    if inventory.get("decision", {}).get("predictor_keys_frozen") is not True:
        raise PortablePredictorComponentError("Predictor inventory is not frozen.")
    record = inventory["output_tables"][f"{city_id}/keys"]
    path = _verify_record(root, record, label=f"{city_id} predictor keys")
    keys = pd.read_parquet(path, columns=["city_id", "tract_geoid", "target_date"])
    if (
        keys.empty
        or not keys["city_id"].astype(str).eq(city_id).all()
        or keys.duplicated(["city_id", "tract_geoid", "target_date"]).any()
    ):
        raise PortablePredictorComponentError(f"Invalid predictor keys for {city_id}.")
    return keys, path


def build_calendar_component(
    project_root: str | Path,
    city_id: str,
) -> Path:
    """Build the deterministic calendar pair on one frozen city key table."""

    root = _project_root(project_root)
    support = load_city_support(root, city_id)
    keys, _ = _city_inventory_keys(root, city_id)
    if set(keys["tract_geoid"].astype(str)) != set(support.tract_geoids):
        raise PortablePredictorComponentError(
            f"Calendar keys do not match canonical support for {city_id}."
        )
    calendar = build_calendar_features(
        keys.loc[:, ["tract_geoid", "target_date"]],
        final_test_year=2026,
    )
    calendar.insert(0, "city_id", city_id)
    output = root / COMPONENT_ROOT / city_id / "calendar_features.parquet"
    atomic_parquet(calendar, output)
    frozen = pd.read_parquet(output)
    pd.testing.assert_frame_equal(frozen, calendar, check_dtype=True)
    return output


def _load_contract(root: Path) -> dict[str, Any]:
    contract = _read_committed_json(root / PORTABLE_CONTRACT, label="portable contract")
    if (
        contract.get("decision", {}).get("predictor_build_authorized") is not True
        or contract.get("decision", {}).get("portable_feature_names_frozen") is not True
    ):
        raise PortablePredictorComponentError("Portable predictor contract is not locked.")
    return contract


def _source_file_record(root: Path, record: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    path = _verify_record(root, record, label=label)
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _static_source_paths(root: Path, city_id: str) -> StaticSourcePaths:
    if city_id == "los_angeles_ca":
        nlcd_manifest = _read_committed_json(
            root / "data/raw/static/nlcd_2016_sources_provenance.json",
            label="Los Angeles NLCD sources",
        )
        static_manifest = _read_committed_json(
            root / "data/raw/static/static_sources_provenance.json",
            label="Los Angeles static sources",
        )
        nlcd_by_product = {
            str(record["product"]): record
            for record in nlcd_manifest["sources"].values()
        }
        land_record = nlcd_by_product["land_cover"]
        impervious_record = nlcd_by_product["impervious"]
        land_path = root / "data/raw/static" / str(land_record["filename"])
        impervious_path = root / "data/raw/static" / str(impervious_record["filename"])
        records: list[dict[str, Any]] = []
        for label, path, record in (
            ("LA NLCD land cover", land_path, land_record["validation"]),
            ("LA NLCD impervious", impervious_path, impervious_record["validation"]),
        ):
            normalized = {
                "path": path.relative_to(root).as_posix(),
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }
            records.append(_source_file_record(root, normalized, label=label))
        terrain_paths: list[Path] = []
        for source_id, record in static_manifest["sources"].items():
            filename = str(record.get("filename", ""))
            if not filename.lower().endswith(".tif"):
                continue
            path = root / "data/raw/static" / filename
            normalized = {
                "path": path.relative_to(root).as_posix(),
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }
            records.append(
                _source_file_record(root, normalized, label=f"LA terrain {source_id}")
            )
            terrain_paths.append(path)
        if len(terrain_paths) != 2:
            raise PortablePredictorComponentError("Los Angeles requires two SRTM tiles.")
        return StaticSourcePaths(
            land_path,
            impervious_path,
            tuple(sorted(terrain_paths)),
            tuple(records),
        )

    manifest = _read_committed_json(
        root
        / f"manifests/multicity/cities/{city_id}/source_evidence/"
        "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
        label=f"{city_id} static source evidence",
    )
    families = manifest["source_families"]
    nlcd_sources = families["nlcd_land_cover_and_imperviousness"]["sources"]
    by_product = {str(record["product"]): record for record in nlcd_sources}
    land_record = by_product["land_cover"]["file"]
    impervious_record = by_product["impervious"]["file"]
    land_path = _verify_record(root, land_record, label=f"{city_id} NLCD land cover")
    impervious_path = _verify_record(
        root, impervious_record, label=f"{city_id} NLCD impervious"
    )
    records = [
        _source_file_record(root, land_record, label=f"{city_id} NLCD land cover"),
        _source_file_record(root, impervious_record, label=f"{city_id} NLCD impervious"),
    ]
    terrain_paths: list[Path] = []
    for record in families["terrain_windows"]["sources"]:
        file_record = record["file"]
        terrain_paths.append(
            _verify_record(root, file_record, label=f"{city_id} SRTM")
        )
        records.append(_source_file_record(root, file_record, label=f"{city_id} SRTM"))
    if len(terrain_paths) != 2:
        raise PortablePredictorComponentError(f"{city_id} requires two SRTM tiles.")
    return StaticSourcePaths(
        land_path,
        impervious_path,
        tuple(sorted(terrain_paths)),
        tuple(records),
    )


def _runtime_static_directory(root: Path, city_id: str) -> Path:
    return root / RUNTIME_ROOT / city_id / "static"


def _atomic_npy(array: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as handle:
            np.save(handle, np.asarray(array), allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _array_record(root: Path, path: Path, array: np.ndarray) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
    }


def _valid_array_cache(root: Path, payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if (
        payload.get("state") != "complete"
        or not isinstance(recorded, str)
        or canonical_sha256(unsigned) != recorded
    ):
        return False
    arrays = payload.get("arrays")
    if not isinstance(arrays, Mapping):
        return False
    for record in arrays.values():
        if not isinstance(record, Mapping):
            return False
        try:
            path = _resolve(root, str(record["path"]))
        except (KeyError, PortablePredictorComponentError):
            return False
        if (
            not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            return False
    return True


def build_static_base_component(
    project_root: str | Path,
    city_id: str,
) -> dict[str, Any]:
    """Cache aligned NLCD, impervious, elevation, and Horn-slope arrays."""

    root = _project_root(project_root)
    support = load_city_support(root, city_id)
    contract = _load_contract(root)
    sources = _static_source_paths(root, city_id)
    static_contract = contract["contract"]["static"]
    runtime = _runtime_static_directory(root, city_id)
    marker = runtime / "static_base_manifest.json"
    if marker.is_file():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if (
            isinstance(existing, dict)
            and _valid_array_cache(root, existing)
            and existing.get("worldcover_commit_sha256")
            == support.worldcover_manifest["commit_sha256"]
            and existing.get("source_records") == list(sources.records)
        ):
            return existing

    nlcd = static_contract["nlcd"]
    terrain = static_contract["terrain"]
    land = _reproject_values(
        sources.land_cover,
        grid=support.grid,
        padding=0,
        valid_source=lambda values: values != int(nlcd["land_cover_nodata"]),
        resampling=Resampling.nearest,
    )
    land_valid = np.isfinite(land)
    land_cover = np.where(land_valid, np.rint(land), 0).astype(np.int16)
    impervious = _reproject_values(
        sources.impervious,
        grid=support.grid,
        padding=0,
        valid_source=lambda values: values != int(nlcd["impervious_nodata"]),
        resampling=Resampling.bilinear,
    )
    impervious_valid = (
        np.isfinite(impervious)
        & (impervious >= float(nlcd["impervious_valid_minimum"]))
        & (impervious <= float(nlcd["impervious_valid_maximum"]))
    )
    impervious_fraction = np.where(
        impervious_valid,
        impervious / float(nlcd["impervious_scale_divisor"]),
        np.nan,
    ).astype(np.float32)
    elevation_padded = _reproject_mosaic(
        list(sources.terrain),
        grid=support.grid,
        padding=int(terrain["slope_source_halo_cells"]),
        nodata_value=float(terrain["native_nodata"]),
        resampling=Resampling.bilinear,
    )
    padding = int(terrain["slope_source_halo_cells"])
    if terrain["slope_algorithm"] != "Horn 3x3" or padding != 1:
        raise PortablePredictorComponentError("Portable terrain contract changed.")
    elevation = elevation_padded[padding:-padding, padding:-padding]
    slope = horn_slope_degrees(
        elevation_padded,
        pixel_width_m=float(terrain["slope_pixel_size_m"]),
        pixel_height_m=float(terrain["slope_pixel_size_m"]),
    ).astype(np.float32)
    arrays = {
        "land_cover": land_cover,
        "land_cover_valid": land_valid,
        "impervious_fraction": impervious_fraction,
        "impervious_valid": impervious_valid,
        "elevation_m": elevation.astype(np.float32),
        "elevation_valid": np.isfinite(elevation),
        "slope_degrees": slope,
        "slope_valid": np.isfinite(slope),
    }
    array_directory = runtime / "base_arrays"
    records: dict[str, dict[str, Any]] = {}
    for name, array in arrays.items():
        path = array_directory / f"{name}.npy"
        _atomic_npy(array, path)
        records[name] = _array_record(root, path, array)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "portable-static-base-v1",
        "state": "complete",
        "city_id": city_id,
        "worldcover_commit_sha256": support.worldcover_manifest["commit_sha256"],
        "source_records": list(sources.records),
        "eligible_cell_count": int(support.eligible_land.sum()),
        "arrays": records,
        "target_or_qa_values_read": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload


def _gshhg_cache_paths(root: Path, city_id: str) -> tuple[Path, Path]:
    directory = _runtime_static_directory(root, city_id) / "gshhg_linework"
    return directory / "frozen_linework.parquet", directory / "linework_manifest.json"


def _expanded_city_envelope(support: PortableCitySupport, radius_km: float) -> Any:
    city_box = gpd.GeoSeries(
        [
            shapely.box(
                support.grid.left,
                support.grid.bottom,
                support.grid.right,
                support.grid.top,
            )
        ],
        crs=support.grid.crs,
    ).to_crs("EPSG:4326")
    west, south, east, north = (float(value) for value in city_box.total_bounds)
    latitude_delta = radius_km / 110.0 * 1.05
    latitude_for_scale = min(89.0, max(abs(south), abs(north)) + latitude_delta)
    longitude_delta = radius_km / (
        111.0 * max(math.cos(math.radians(latitude_for_scale)), 0.05)
    ) * 1.05
    expanded = shapely.box(
        west - longitude_delta,
        max(-90.0, south - latitude_delta),
        east + longitude_delta,
        min(90.0, north + latitude_delta),
    )
    if expanded.bounds[0] <= -180.0 or expanded.bounds[2] >= 180.0:
        raise PortablePredictorComponentError("GSHHG envelope crosses the antimeridian.")
    return expanded


def _read_gshhg_layer(
    archive: Path,
    level: int,
    *,
    bbox: tuple[float, float, float, float] | None = None,
) -> gpd.GeoDataFrame:
    member = f"GSHHS_shp/f/GSHHS_f_L{level}.shp"
    try:
        frame = gpd.read_file(
            f"zip://{archive.resolve()}!{member}",
            bbox=bbox,
        )
    except Exception as error:
        raise PortablePredictorComponentError(
            f"Cannot read frozen GSHHG L{level} layer."
        ) from error
    if frame.empty or frame.crs is None or not CRS.from_user_input(frame.crs).equals(
        CRS.from_epsg(4326)
    ):
        raise PortablePredictorComponentError(f"Frozen GSHHG L{level} layer changed.")
    return frame


def _load_frozen_gshhg_linework(
    root: Path,
    support: PortableCitySupport,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    freeze = _read_committed_json(root / GSHHG_FREEZE, label="GSHHG freeze decision")
    source_lock = freeze["source_lock"]
    algorithm = freeze["algorithm_lock"]
    archive = _resolve(root, str(source_lock["archive_path"]))
    if (
        not archive.is_file()
        or archive.stat().st_size != int(source_lock["archive_bytes"])
        or sha256_file(archive) != source_lock["archive_sha256"]
    ):
        raise PortablePredictorComponentError("Frozen local GSHHG archive changed.")
    cache_path, marker_path = _gshhg_cache_paths(root, support.city_id)
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker = {}
        unsigned = dict(marker) if isinstance(marker, dict) else {}
        recorded = unsigned.pop("commit_sha256", None)
        if (
            marker.get("state") == "complete"
            and recorded == canonical_sha256(unsigned)
            and marker.get("freeze_commit_sha256") == freeze["commit_sha256"]
            and cache_path.is_file()
            and sha256_file(cache_path) == marker.get("linework_sha256")
        ):
            return gpd.read_parquet(cache_path), freeze

    with (root / GSHHG_REPAIR_CONFIG).open("rb") as handle:
        repair_config = tomllib.load(handle)["invalid_geometry_repair"]
    maximum_radius = float(max(algorithm["search_radii_km"]))
    envelope = _expanded_city_envelope(support, maximum_radius)
    l1 = _read_gshhg_layer(archive, 1, bbox=envelope.bounds)
    l2 = _read_gshhg_layer(archive, 2)
    l3 = _read_gshhg_layer(archive, 3)
    if (
        len(l2) != int(source_lock["l2_row_count"])
        or len(l3) != int(source_lock["l3_row_count"])
    ):
        raise PortablePredictorComponentError("Frozen GSHHG L2/L3 row counts changed.")
    invalid_l1_count = int((~l1.geometry.is_valid).sum())
    if invalid_l1_count == 1:
        repaired_l1, _ = repair_predeclared_l1_geometry(l1, repair_config)
    elif invalid_l1_count == 0:
        repaired_l1 = l1
    else:
        raise PortablePredictorComponentError(
            f"Regional GSHHG L1 has {invalid_l1_count} invalid polygons."
        )
    selected_ids = tuple(str(value) for value in source_lock["selected_l2_source_ids"])
    selected_l2 = l2.loc[l2["id"].astype(str).isin(selected_ids)].copy()
    selected_l3 = l3.loc[
        pd.to_numeric(l3["parent_id"], errors="coerce").isin(
            [int(value) for value in selected_ids]
        )
    ].copy()
    if (
        len(selected_l2) != len(selected_ids)
        or set(selected_l2["id"].astype(str)) != set(selected_ids)
        or len(selected_l3) != int(source_lock["selected_l3_direct_descendant_count"])
        or not selected_l2.geometry.is_valid.all()
        or not selected_l3.geometry.is_valid.all()
    ):
        raise PortablePredictorComponentError("Frozen GSHHG L2/L3 selection changed.")

    indices = np.unique(repaired_l1.sindex.query(envelope, predicate="intersects"))
    if indices.size == 0:
        raise PortablePredictorComponentError("No regional GSHHG L1 polygon was found.")
    regional_l1 = repaired_l1.iloc[indices].copy()
    max_vertices = int(algorithm["canonical_line_chunk_vertex_count"])
    tolerance = float(algorithm["dateline_tolerance_degrees"])
    frames: list[gpd.GeoDataFrame] = []
    for source_level, shoreline_class, polygons in (
        (1, "global_ocean_l1", regional_l1),
        (2, "selected_great_lakes_l2", selected_l2),
        (3, "selected_great_lakes_direct_l3_islands", selected_l3),
    ):
        normalized = polygons.copy()
        normalized["level"] = 1
        lines = gshhg_l1_exterior_linework(
            normalized,
            max_vertices=max_vertices,
            dateline_tolerance_degrees=tolerance,
        )
        lines["source_level"] = source_level
        lines["shoreline_class"] = shoreline_class
        frames.append(lines)
    linework = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    if linework.empty or linework.geometry.is_empty.any() or not linework.geometry.is_valid.all():
        raise PortablePredictorComponentError("Frozen GSHHG linework is invalid.")
    atomic_parquet(linework, cache_path)
    marker = {
        "schema_version": 1,
        "algorithm_version": "portable-gshhg-linework-v1",
        "state": "complete",
        "city_id": support.city_id,
        "freeze_commit_sha256": freeze["commit_sha256"],
        "archive_sha256": source_lock["archive_sha256"],
        "regional_l1_polygon_count": len(regional_l1),
        "selected_l2_polygon_count": len(selected_l2),
        "selected_l3_polygon_count": len(selected_l3),
        "line_count": len(linework),
        "linework_path": cache_path.relative_to(root).as_posix(),
        "linework_sha256": sha256_file(cache_path),
    }
    marker["commit_sha256"] = canonical_sha256(marker)
    atomic_json(marker, marker_path)
    return linework, freeze


def _emit_progress(
    callback: ProgressCallback | None,
    *,
    city_id: str,
    chunk_index: int,
    chunk_count: int,
    message: str,
) -> None:
    if callback is not None:
        callback(
            {
                "city_id": city_id,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "message": message,
            }
        )


def _distance_chunk_path(directory: Path, index: int, count: int) -> Path:
    return directory / f"distance_chunk_{index:05d}_of_{count:05d}.parquet"


def _valid_distance_chunk(path: Path, expected_flat: np.ndarray) -> bool:
    if not path.is_file():
        return False
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return False
    if list(frame.columns) != ["flat_index", "tract_geoid", "distance_km"]:
        return False
    values = frame["distance_km"].to_numpy(dtype=np.float64)
    return (
        len(frame) == len(expected_flat)
        and np.array_equal(frame["flat_index"].to_numpy(dtype=np.int64), expected_flat)
        and np.isfinite(values).all()
        and (values >= 0).all()
    )


def _write_distance_progress(
    root: Path,
    support: PortableCitySupport,
    *,
    state: str,
    chunk_size: int,
    chunk_count: int,
    completed: Sequence[int],
    chunks: Mapping[str, Any],
) -> dict[str, Any]:
    marker_path = _runtime_static_directory(root, support.city_id) / (
        "gshhg_distance_progress.json"
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "portable-gshhg-distance-v1",
        "state": state,
        "city_id": support.city_id,
        "eligible_cell_count": int(support.eligible_land.sum()),
        "chunk_size": chunk_size,
        "chunk_count": chunk_count,
        "completed_chunk_indices": list(completed),
        "chunks": dict(chunks),
        "target_or_qa_values_read": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    payload["manifest_path"] = marker_path.relative_to(root).as_posix()
    return payload


def build_gshhg_distance_component(
    project_root: str | Path,
    city_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
    pause_callback: PauseCallback | None = None,
    chunk_size: int = DISTANCE_CHUNK_SIZE,
) -> dict[str, Any]:
    """Build exact eligible-cell GSHHG distances in atomic resumable chunks.

    ``progress_callback`` is called with one dictionary.  ``pause_callback`` is
    checked only after a newly computed chunk has been atomically published.  A
    requested pause returns an ``incomplete`` payload and is not a scientific
    failure.  ``chunk_index`` is one-based and ``chunk_count`` is the total.
    """

    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("Distance chunk size must be a positive integer.")
    root = _project_root(project_root)
    support = load_city_support(root, city_id)
    _load_contract(root)
    eligible_flat = np.flatnonzero(
        support.eligible_land & (support.zones > 0)
    ).astype(np.int64)
    chunk_count = math.ceil(len(eligible_flat) / chunk_size)
    chunk_directory = _runtime_static_directory(root, city_id) / "gshhg_distance_chunks"
    chunk_directory.mkdir(parents=True, exist_ok=True)
    completed: list[int] = []
    chunk_records: dict[str, Any] = {}
    missing: list[int] = []
    for index in range(1, chunk_count + 1):
        start = (index - 1) * chunk_size
        expected = eligible_flat[start : start + chunk_size]
        path = _distance_chunk_path(chunk_directory, index, chunk_count)
        if _valid_distance_chunk(path, expected):
            completed.append(index)
            chunk_records[str(index)] = {
                "path": path.relative_to(root).as_posix(),
                "rows": len(expected),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        else:
            missing.append(index)

    if not missing:
        return _write_distance_progress(
            root,
            support,
            state="complete",
            chunk_size=chunk_size,
            chunk_count=chunk_count,
            completed=completed,
            chunks=chunk_records,
        )

    linework, freeze = _load_frozen_gshhg_linework(root, support)
    projected_lines = linework.to_crs(support.grid.crs).geometry.to_numpy()
    if (
        projected_lines.size == 0
        or shapely.is_empty(projected_lines).any()
        or not shapely.is_valid(projected_lines).all()
    ):
        raise PortablePredictorComponentError("Projected GSHHG linework is invalid.")
    tree = shapely.STRtree(projected_lines)
    maximum_distance_m = float(max(freeze["algorithm_lock"]["search_radii_km"])) * 1000
    geoid_values = np.asarray(support.tract_geoids, dtype=object)
    for index in missing:
        start = (index - 1) * chunk_size
        flat = eligible_flat[start : start + chunk_size]
        rows, columns = np.unravel_index(flat, support.grid.shape)
        col_centres = columns.astype(np.float64) + 0.5
        row_centres = rows.astype(np.float64) + 0.5
        transform = support.grid.transform
        x = transform.c + col_centres * transform.a + row_centres * transform.b
        y = transform.f + col_centres * transform.d + row_centres * transform.e
        points = shapely.points(x, y)
        indices, distances = tree.query_nearest(
            points,
            all_matches=False,
            return_distance=True,
        )
        if indices.ndim != 2 or indices.shape[0] != 2:
            raise PortablePredictorComponentError("GSHHG STRtree result shape changed.")
        ordered = np.full(len(points), np.nan, dtype=np.float64)
        ordered[indices[0]] = np.asarray(distances, dtype=np.float64)
        if (
            not np.isfinite(ordered).all()
            or (ordered < 0).any()
            or float(ordered.max(initial=0.0)) >= maximum_distance_m
        ):
            raise PortablePredictorComponentError(
                "GSHHG distance is invalid or exhausted the frozen radius ladder."
            )
        zone_ids = support.zones.ravel()[flat]
        frame = pd.DataFrame(
            {
                "flat_index": flat,
                "tract_geoid": pd.Series(geoid_values[zone_ids - 1], dtype="string"),
                "distance_km": ordered / 1000.0,
            }
        )
        path = _distance_chunk_path(chunk_directory, index, chunk_count)
        atomic_parquet(frame, path)
        frozen = pd.read_parquet(path)
        pd.testing.assert_frame_equal(frozen, frame, check_dtype=True)
        completed.append(index)
        completed.sort()
        chunk_records[str(index)] = {
            "path": path.relative_to(root).as_posix(),
            "rows": len(frame),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        _write_distance_progress(
            root,
            support,
            state="incomplete",
            chunk_size=chunk_size,
            chunk_count=chunk_count,
            completed=completed,
            chunks=chunk_records,
        )
        _emit_progress(
            progress_callback,
            city_id=city_id,
            chunk_index=index,
            chunk_count=chunk_count,
            message=f"GSHHG distance chunk {index}/{chunk_count} committed",
        )
        if pause_callback is not None and pause_callback():
            return _write_distance_progress(
                root,
                support,
                state="incomplete",
                chunk_size=chunk_size,
                chunk_count=chunk_count,
                completed=completed,
                chunks=chunk_records,
            )

    payload = _write_distance_progress(
        root,
        support,
        state="complete",
        chunk_size=chunk_size,
        chunk_count=chunk_count,
        completed=completed,
        chunks=chunk_records,
    )
    _emit_progress(
        progress_callback,
        city_id=city_id,
        chunk_index=chunk_count,
        chunk_count=chunk_count,
        message="GSHHG distance component complete",
    )
    return payload


def _load_cached_array(root: Path, record: Mapping[str, Any]) -> np.ndarray:
    path = _resolve(root, str(record["path"]))
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise PortablePredictorComponentError(f"Static array cache changed: {path}")
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if list(array.shape) != record.get("shape") or str(array.dtype) != record.get("dtype"):
        raise PortablePredictorComponentError(f"Static array schema changed: {path}")
    return array


def _load_distance_surface(
    root: Path,
    support: PortableCitySupport,
) -> np.ndarray:
    marker_path = _runtime_static_directory(root, support.city_id) / (
        "gshhg_distance_progress.json"
    )
    marker = _read_committed_json(marker_path, label="GSHHG distance progress")
    if marker.get("state") != "complete":
        raise PortablePredictorComponentError("GSHHG distance component is incomplete.")
    distance = np.full(support.grid.shape, np.nan, dtype=np.float32)
    seen = np.zeros(support.grid.shape, dtype=bool)
    for index in range(1, int(marker["chunk_count"]) + 1):
        record = marker["chunks"][str(index)]
        path = _verify_record(root, record, label=f"GSHHG distance chunk {index}")
        frame = pd.read_parquet(path)
        flat = frame["flat_index"].to_numpy(dtype=np.int64)
        values = frame["distance_km"].to_numpy(dtype=np.float64)
        if (
            np.any(flat < 0)
            or np.any(flat >= distance.size)
            or len(np.unique(flat)) != len(flat)
            or seen.ravel()[flat].any()
            or not np.isfinite(values).all()
        ):
            raise PortablePredictorComponentError("GSHHG distance chunks overlap or changed.")
        distance.ravel()[flat] = values.astype(np.float32)
        seen.ravel()[flat] = True
    expected = support.eligible_land & (support.zones > 0)
    if not np.array_equal(seen, expected) or not np.isfinite(distance[expected]).all():
        raise PortablePredictorComponentError("GSHHG chunks do not cover eligible support.")
    return distance


def finalize_static_component(
    project_root: str | Path,
    city_id: str,
) -> dict[str, Any]:
    """Aggregate cached static arrays into exactly 18 frozen model features."""

    root = _project_root(project_root)
    support = load_city_support(root, city_id)
    contract = _load_contract(root)
    base_path = _runtime_static_directory(root, city_id) / "static_base_manifest.json"
    base = _read_committed_json(base_path, label="portable static base")
    if not _valid_array_cache(root, base):
        raise PortablePredictorComponentError("Portable static base is incomplete.")
    arrays = base["arrays"]
    distance = _load_distance_surface(root, support)
    static_arrays = StaticArrays(
        land_cover=_load_cached_array(root, arrays["land_cover"]),
        land_cover_valid=_load_cached_array(root, arrays["land_cover_valid"]),
        impervious_fraction=_load_cached_array(root, arrays["impervious_fraction"]),
        impervious_valid=_load_cached_array(root, arrays["impervious_valid"]),
        elevation_m=_load_cached_array(root, arrays["elevation_m"]),
        elevation_valid=_load_cached_array(root, arrays["elevation_valid"]),
        slope_degrees=_load_cached_array(root, arrays["slope_degrees"]),
        slope_valid=_load_cached_array(root, arrays["slope_valid"]),
        coast_distance_km=distance,
        coast_distance_valid=np.isfinite(distance),
    )
    static_contract = contract["contract"]["static"]
    features, audit = aggregate_static_features(
        arrays=static_arrays,
        support=support.static_support,
        land_groups={
            str(name): [int(value) for value in values]
            for name, values in static_contract["nlcd"]["groups"].items()
        },
        minimum_coverage_fraction=float(
            static_contract["minimum_valid_coverage_fraction"]
        ),
        std_ddof=int(static_contract["continuous_std_ddof"]),
        quantile_method=str(static_contract["quantile_method"]),
    )
    features = features.rename(
        columns={
            "pacific_coast_distance_mean_km": (
                "gshhg_ocean_great_lakes_shore_distance_mean_km"
            ),
            "pacific_coast_distance_p10_km": (
                "gshhg_ocean_great_lakes_shore_distance_p10_km"
            ),
        }
    )
    audit = audit.rename(
        columns={
            "census_coast_distance_valid_pixel_count": (
                "gshhg_ocean_great_lakes_shore_distance_valid_pixel_count"
            ),
            "census_coast_distance_coverage_fraction": (
                "gshhg_ocean_great_lakes_shore_distance_coverage_fraction"
            ),
        }
    )
    audit["nlcd_developed_medium_fraction"] = features[
        "nlcd_developed_medium_fraction"
    ].to_numpy()
    model_names = tuple(
        str(record["feature_name"])
        for record in contract["feature_registry"]["features"]
        if record.get("static") is True
    )
    if len(model_names) != 18 or set(model_names) != (
        set(features.columns) - {"tract_geoid", "nlcd_developed_medium_fraction"}
    ):
        raise PortablePredictorComponentError("Frozen 18-feature static schema changed.")
    if not np.allclose(
        audit["nlcd_remainder_fraction"].to_numpy(dtype=float),
        0.0,
        rtol=0.0,
        atol=0.0,
    ):
        raise PortablePredictorComponentError("NLCD remainder is not exactly zero.")
    model = features.loc[:, ["tract_geoid", *model_names]].copy()
    model.insert(0, "city_id", city_id)
    audit.insert(0, "city_id", city_id)
    if model.loc[:, model_names].isna().any().any():
        raise PortablePredictorComponentError("Static model features contain missing values.")

    output = root / COMPONENT_ROOT / city_id
    feature_path = output / "static_features.parquet"
    audit_path = output / "static_feature_audit.parquet"
    provenance_path = output / "static_features_provenance.json"
    atomic_parquet(model, feature_path)
    atomic_parquet(audit, audit_path)
    frozen_model = pd.read_parquet(feature_path)
    frozen_audit = pd.read_parquet(audit_path)
    pd.testing.assert_frame_equal(frozen_model, model, check_dtype=True)
    pd.testing.assert_frame_equal(frozen_audit, audit, check_dtype=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "portable-static-components-v1",
        "state": "complete",
        "city_id": city_id,
        "row_count": len(model),
        "model_feature_count": len(model_names),
        "model_feature_names": list(model_names),
        "audit_only_features": [
            "nlcd_developed_medium_fraction",
            "nlcd_remainder_fraction",
        ],
        "worldcover_commit_sha256": support.worldcover_manifest["commit_sha256"],
        "contract_commit_sha256": contract["commit_sha256"],
        "semantic_feature_sha256": canonical_frame_sha256(
            model, sort_by=["city_id", "tract_geoid"]
        ),
        "semantic_audit_sha256": canonical_frame_sha256(
            audit, sort_by=["city_id", "tract_geoid"]
        ),
        "output_files": {
            feature_path.name: parquet_file_record(feature_path, frozen_model),
            audit_path.name: parquet_file_record(audit_path, frozen_audit),
        },
        "target_or_qa_values_read": False,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, provenance_path)
    return payload
