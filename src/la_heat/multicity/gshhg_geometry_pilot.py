"""Target-blind GSHHG geometry helpers and source-only pilot orchestrator.

The orchestrator contains no downloader, target-grid reader, or predictor builder.
It authenticates an already present archive and reads only public source geometry,
frozen target-blind reference points, and the Census comparison geometry.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import stat
import tomllib
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import LineString, Point, Polygon

from la_heat.multicity.config import load_multicity_plan
from la_heat.provenance import (
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.static_sources import CENSUS_2019_COASTLINE, validate_source_file

WGS84: Final = CRS.from_epsg(4326)
_SOURCE_ID_PATTERN: Final = re.compile(r"^(0|[1-9][0-9]*)(?:-([EW]))?$")
_DEFAULT_GEOD: Final = Geod(ellps="WGS84")

SCHEMA_VERSION: Final = 2
ALGORITHM_VERSION: Final = "gshhg-geometry-pilot-v2"
COMPLETE_STATE: Final = "geometry_pilot_complete_source_not_frozen"
V1_FAILURE_STATE: Final = "geometry_pilot_v1_failed_before_distance"
DEFAULT_CONFIG: Final = Path("configs/multicity/gshhg_geometry_pilot_v2.toml")
DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT.json"
)
DEFAULT_V1_FAILURE_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT_V1_FAILURE.json"
)
DEFAULT_DIAGNOSTIC_TABLE: Final = Path(
    "data/interim/multicity/water_distance/gshhg_geometry_pilot/diagnostic_distances.csv"
)
GSHHG_SOURCE_NAME: Final = "gshhg_l1_plus_three_seed_selected_l2_connected_waters"
CENSUS_SOURCE_NAME: Final = "census_us_l4150_benchmark"
EXPECTED_V1_CONFIG_SHA256: Final = (
    "691e42c914ccff4a076097d83bc9a363cd07f508847c224f8268704f308a67f4"
)
EXPECTED_V2_CONFIG_SHA256: Final = (
    "c0f41f77d0c87a5ca09de81c2ec0ca2ae489633ac3f785648fbeb5f9f67e67ff"
)
CODE_PATHS: Final = (
    "configs/multicity/experiment.toml",
    "configs/multicity/gshhg_geometry_pilot_v1.toml",
    "configs/multicity/gshhg_geometry_pilot_v2.toml",
    "configs/multicity/water_distance_review_v1.toml",
    "pyproject.toml",
    "scripts/stage_multicity_gshhg_geometry_pilot.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/gshhg_geometry_pilot.py",
    "src/la_heat/multicity/water_distance_review.py",
    "src/la_heat/provenance.py",
    "src/la_heat/static_sources.py",
)


class GshhgGeometryPilotError(ValueError):
    """Raised when a source-only geometry audit fails closed."""


@dataclass(frozen=True, slots=True)
class ZipSafetyLimits:
    """Resource limits applied before a pinned ZIP payload is opened by GDAL."""

    max_members: int = 4096
    max_member_uncompressed_bytes: int = 1_000_000_000
    max_total_uncompressed_bytes: int = 2_000_000_000
    max_compression_ratio: float = 250.0

    def __post_init__(self) -> None:
        integer_values = (
            self.max_members,
            self.max_member_uncompressed_bytes,
            self.max_total_uncompressed_bytes,
        )
        if any(isinstance(value, bool) or value <= 0 for value in integer_values):
            raise ValueError("ZIP integer limits must be positive non-boolean integers.")
        if (
            isinstance(self.max_compression_ratio, bool)
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio <= 1.0
        ):
            raise ValueError("ZIP compression-ratio limit must be finite and above one.")


DEFAULT_ZIP_SAFETY_LIMITS: Final = ZipSafetyLimits()


@dataclass(frozen=True, slots=True)
class LakeSeed:
    """A predeclared, target-independent point strictly inside one named lake."""

    name: str
    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("Lake seed names must be non-empty and canonical.")
        if not (
            math.isfinite(self.longitude)
            and math.isfinite(self.latitude)
            and -180.0 <= self.longitude <= 180.0
            and -90.0 <= self.latitude <= 90.0
        ):
            raise ValueError(f"Lake seed {self.name!r} has invalid coordinates.")


@dataclass(frozen=True, slots=True)
class RadiusDistanceResult:
    """Distances accepted by one strict step of an expanding-radius ladder."""

    distances_m: np.ndarray
    accepted_radius_km: float
    candidate_count: int


def _canonical_member_name(name: str, *, is_directory: bool) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise GshhgGeometryPilotError(f"Unsafe ZIP member name: {name!r}.")
    if name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", name):
        raise GshhgGeometryPilotError(f"Unsafe ZIP member name: {name!r}.")
    if ":" in name:
        raise GshhgGeometryPilotError(f"Unsafe ZIP member name: {name!r}.")
    if is_directory:
        if not name.endswith("/"):
            raise GshhgGeometryPilotError(
                f"ZIP directory member lacks a canonical trailing slash: {name!r}."
            )
        body = name[:-1]
    else:
        if name.endswith("/"):
            raise GshhgGeometryPilotError(f"ZIP file member has a directory suffix: {name!r}.")
        body = name
    raw_parts = body.split("/")
    if not body or any(part in {"", ".", ".."} for part in raw_parts):
        raise GshhgGeometryPilotError(f"Unsafe ZIP member name: {name!r}.")
    parsed = PurePosixPath(body)
    if parsed.is_absolute() or parsed.as_posix() != body:
        raise GshhgGeometryPilotError(f"Non-canonical ZIP member name: {name!r}.")
    return body


def _validate_expected_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Expected ZIP SHA-256 must be 64 lowercase hexadecimal characters.")


def validate_pinned_zip(
    path: str | Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_members: Sequence[str],
    allowed_members: Sequence[str] | None = None,
    limits: ZipSafetyLimits | None = None,
) -> dict[str, object]:
    """Authenticate exact bytes and reject unsafe or ambiguous ZIP structure.

    The function never extracts a member.  Exact byte authentication occurs before
    ``zipfile`` parses the central directory, so a caller may safely use a validated
    archive through GDAL's read-only ZIP virtual filesystem afterward.
    """

    archive_path = Path(path)
    active_limits = DEFAULT_ZIP_SAFETY_LIMITS if limits is None else limits
    if not isinstance(active_limits, ZipSafetyLimits):
        raise TypeError("limits must be a ZipSafetyLimits instance.")
    _validate_expected_digest(expected_sha256)
    if isinstance(expected_bytes, bool) or expected_bytes <= 0:
        raise ValueError("Expected ZIP byte count must be a positive integer.")
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    observed_bytes = archive_path.stat().st_size
    if observed_bytes != expected_bytes:
        raise GshhgGeometryPilotError(
            f"Pinned ZIP byte count changed: {observed_bytes} != {expected_bytes}."
        )
    observed_sha256 = sha256_file(archive_path)
    if observed_sha256 != expected_sha256:
        raise GshhgGeometryPilotError("Pinned ZIP SHA-256 changed.")

    required = tuple(
        _canonical_member_name(str(name), is_directory=False) for name in required_members
    )
    if len(required) != len(set(required)):
        raise ValueError("Required ZIP members must be unique.")
    allowed: set[str] | None = None
    if allowed_members is not None:
        allowed_values = tuple(
            _canonical_member_name(str(name), is_directory=False) for name in allowed_members
        )
        if len(allowed_values) != len(set(allowed_values)):
            raise ValueError("Allowed ZIP members must be unique.")
        allowed = set(allowed_values)
        if not set(required).issubset(allowed):
            raise ValueError("Every required ZIP member must also be allowed.")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise GshhgGeometryPilotError("Pinned ZIP is empty.")
            if len(members) > active_limits.max_members:
                raise GshhgGeometryPilotError(f"Pinned ZIP has too many members: {len(members)}.")

            seen_names: set[str] = set()
            collision_keys: set[str] = set()
            records: list[dict[str, object]] = []
            total_uncompressed = 0
            file_names: set[str] = set()
            for member in members:
                canonical = _canonical_member_name(
                    member.filename,
                    is_directory=member.is_dir(),
                )
                if member.filename in seen_names:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP has duplicate member {member.filename!r}."
                    )
                seen_names.add(member.filename)
                collision_key = unicodedata.normalize("NFKC", canonical).casefold()
                if collision_key in collision_keys:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP has a case-folding path collision at {canonical!r}."
                    )
                collision_keys.add(collision_key)

                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP member {member.filename!r} is a symbolic link."
                    )
                file_type = stat.S_IFMT(unix_mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP member {member.filename!r} has an unsafe file type."
                    )
                if member.flag_bits & 0x1:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP member {member.filename!r} is encrypted."
                    )
                if member.file_size < 0 or member.compress_size < 0:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP member {member.filename!r} has a negative size."
                    )
                if member.file_size > active_limits.max_member_uncompressed_bytes:
                    raise GshhgGeometryPilotError(
                        f"Pinned ZIP member {member.filename!r} exceeds its size limit."
                    )
                if member.file_size:
                    ratio = member.file_size / max(member.compress_size, 1)
                    if ratio > active_limits.max_compression_ratio:
                        raise GshhgGeometryPilotError(
                            f"Pinned ZIP member {member.filename!r} exceeds the "
                            "compression-ratio limit."
                        )
                total_uncompressed += member.file_size
                if total_uncompressed > active_limits.max_total_uncompressed_bytes:
                    raise GshhgGeometryPilotError(
                        "Pinned ZIP exceeds the total uncompressed-size limit."
                    )
                if not member.is_dir():
                    file_names.add(canonical)
                records.append(
                    {
                        "name": member.filename,
                        "directory": member.is_dir(),
                        "bytes": member.file_size,
                        "compressed_bytes": member.compress_size,
                        "crc32": f"{member.CRC:08x}",
                        "compression": member.compress_type,
                    }
                )

            missing = sorted(set(required) - file_names)
            if missing:
                raise GshhgGeometryPilotError(f"Pinned ZIP lacks required members: {missing}.")
            if allowed is not None and file_names != allowed:
                raise GshhgGeometryPilotError(
                    "Pinned ZIP file-member allow-list changed; "
                    f"missing={sorted(allowed - file_names)}, "
                    f"unexpected={sorted(file_names - allowed)}."
                )
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise GshhgGeometryPilotError(
                    f"Pinned ZIP CRC failed for member {corrupt_member!r}."
                )
    except GshhgGeometryPilotError:
        raise
    except (
        NotImplementedError,
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise GshhgGeometryPilotError(f"Cannot safely read pinned ZIP {archive_path}.") from exc

    ordered_records = sorted(records, key=lambda record: str(record["name"]))
    return {
        "path": str(archive_path),
        "bytes": observed_bytes,
        "sha256": observed_sha256,
        "member_count": len(records),
        "file_member_count": len(file_names),
        "total_uncompressed_bytes": total_uncompressed,
        "members": ordered_records,
        "member_inventory_sha256": canonical_sha256(ordered_records),
        "archive_testzip_passed": True,
        "extracted": False,
    }


def canonical_gshhg_source_id(value: object) -> str:
    """Collapse the official ``<id>-E``/``<id>-W`` split IDs to one source ID."""

    if not isinstance(value, str):
        raise GshhgGeometryPilotError("GSHHG source IDs must be strings.")
    match = _SOURCE_ID_PATTERN.fullmatch(value)
    if match is None:
        raise GshhgGeometryPilotError(f"Invalid GSHHG source ID: {value!r}.")
    return match.group(1)


def _require_wgs84(frame: gpd.GeoDataFrame, *, label: str) -> None:
    if frame.empty or frame.crs is None:
        raise GshhgGeometryPilotError(f"{label} must be non-empty and georeferenced.")
    observed = CRS.from_user_input(frame.crs)
    if not observed.equals(WGS84):
        raise GshhgGeometryPilotError(f"{label} must use WGS84 / EPSG:4326.")


def _require_valid_polygons(frame: gpd.GeoDataFrame, *, label: str) -> None:
    geometry = frame.geometry
    if (
        geometry.isna().any()
        or geometry.is_empty.any()
        or not geometry.is_valid.all()
        or not geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all()
    ):
        raise GshhgGeometryPilotError(
            f"{label} requires only non-empty, valid Polygon or MultiPolygon geometry."
        )
    coordinates = shapely.get_coordinates(geometry.to_numpy())
    if coordinates.size == 0 or not np.isfinite(coordinates).all():
        raise GshhgGeometryPilotError(f"{label} has missing or non-finite coordinates.")
    if np.abs(coordinates[:, 0]).max() > 180.0 or np.abs(coordinates[:, 1]).max() > 90.0:
        raise GshhgGeometryPilotError(f"{label} coordinates exceed longitude/latitude bounds.")


def _level_is_exact(frame: gpd.GeoDataFrame, column: str, expected: int) -> bool:
    if column not in frame:
        return False
    values = frame[column]
    if values.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        return False
    numeric = pd.to_numeric(values, errors="coerce")
    return bool(numeric.notna().all() and numeric.eq(expected).all())


def _is_dateline_seam(
    left: tuple[float, float],
    right: tuple[float, float],
    *,
    tolerance: float,
) -> bool:
    positive_seam = math.isclose(left[0], 180.0, abs_tol=tolerance, rel_tol=0.0) and math.isclose(
        right[0], 180.0, abs_tol=tolerance, rel_tol=0.0
    )
    negative_seam = math.isclose(left[0], -180.0, abs_tol=tolerance, rel_tol=0.0) and math.isclose(
        right[0], -180.0, abs_tol=tolerance, rel_tol=0.0
    )
    return bool(positive_seam or negative_seam)


def audit_l1_dateline_segments(
    frame: gpd.GeoDataFrame,
    *,
    id_column: str = "id",
    level_column: str = "level",
    tolerance: float = 1e-9,
    reject_jump_degrees: float = 180.0,
) -> dict[str, object]:
    """Audit the frozen antimeridian rule against every source polygon exterior."""

    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or not 0.0 <= tolerance <= 1e-6:
        raise ValueError("Dateline tolerance must be finite and no larger than 1e-6.")
    if (
        isinstance(reject_jump_degrees, bool)
        or not math.isfinite(reject_jump_degrees)
        or reject_jump_degrees <= 0.0
    ):
        raise ValueError("The rejected longitude jump must be finite and positive.")
    _require_wgs84(frame, label="GSHHG L1")
    _require_valid_polygons(frame, label="GSHHG L1")
    if id_column not in frame or not _level_is_exact(frame, level_column, 1):
        raise GshhgGeometryPilotError("GSHHG L1 identity or level schema changed.")

    bounds = frame.geometry.bounds
    touches_dateline = bounds["minx"].le(-180.0 + tolerance) | bounds["maxx"].ge(180.0 - tolerance)
    spans_rejected_jump = (bounds["maxx"] - bounds["minx"]).ge(reject_jump_degrees - tolerance)
    candidates = frame.loc[touches_dateline | spans_rejected_jump].sort_values(
        id_column,
        kind="stable",
    )
    same_meridian_seams = 0
    opposite_sign_dateline_segments = 0
    rejected_remaining_jumps = 0
    maximum_retained_jump = 0.0
    audited_segment_count = 0
    seam_component_ids: set[str] = set()
    for _, row in candidates.iterrows():
        geometry = row.geometry
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for polygon in polygons:
            coordinates = [
                (float(longitude), float(latitude))
                for longitude, latitude, *_ in polygon.exterior.coords
            ]
            for left, right in zip(coordinates, coordinates[1:], strict=False):
                if left == right:
                    continue
                audited_segment_count += 1
                if _is_dateline_seam(left, right, tolerance=tolerance):
                    same_meridian_seams += 1
                    seam_component_ids.add(str(row[id_column]))
                    continue
                both_on_dateline = math.isclose(
                    abs(left[0]), 180.0, abs_tol=tolerance, rel_tol=0.0
                ) and math.isclose(abs(right[0]), 180.0, abs_tol=tolerance, rel_tol=0.0)
                if both_on_dateline:
                    opposite_sign_dateline_segments += 1
                jump = abs(right[0] - left[0])
                maximum_retained_jump = max(maximum_retained_jump, jump)
                if jump >= reject_jump_degrees - tolerance:
                    rejected_remaining_jumps += 1

    if opposite_sign_dateline_segments or rejected_remaining_jumps:
        raise GshhgGeometryPilotError(
            "GSHHG contains an unapproved antimeridian segment after same-meridian "
            "seam classification."
        )
    return {
        "global_source_exteriors_audited": True,
        "source_polygon_count": len(frame),
        "dateline_candidate_polygon_count": len(candidates),
        "audited_candidate_segment_count": audited_segment_count,
        "removed_same_meridian_seam_segment_count": same_meridian_seams,
        "same_meridian_seam_component_ids": sorted(seam_component_ids),
        "opposite_sign_dateline_segment_count": opposite_sign_dateline_segments,
        "remaining_rejected_longitude_jump_count": rejected_remaining_jumps,
        "maximum_retained_longitude_jump_degrees": maximum_retained_jump,
        "reject_jump_degrees": reject_jump_degrees,
        "tolerance_degrees": tolerance,
        "all_global_source_exteriors_passed": True,
    }


def _coordinate_runs_without_dateline_seams(
    polygon: Polygon,
    *,
    tolerance: float,
) -> list[list[tuple[float, float]]]:
    coordinates = [
        (float(longitude), float(latitude)) for longitude, latitude, *_ in polygon.exterior.coords
    ]
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for left, right in zip(coordinates, coordinates[1:], strict=False):
        if left == right:
            continue
        if _is_dateline_seam(left, right, tolerance=tolerance):
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if abs(right[0] - left[0]) >= 180.0 - tolerance:
            raise GshhgGeometryPilotError(
                "A GSHHG exterior segment crosses the world after split normalization."
            )
        if not current:
            current = [left, right]
        elif current[-1] == left:
            current.append(right)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = [left, right]
    if len(current) >= 2:
        runs.append(current)
    return runs


def _chunk_coordinate_run(
    coordinates: Sequence[tuple[float, float]],
    *,
    max_vertices: int,
) -> list[LineString]:
    chunks: list[LineString] = []
    start = 0
    while start < len(coordinates) - 1:
        end = min(start + max_vertices, len(coordinates))
        chunk = LineString(coordinates[start:end])
        if not chunk.is_empty and chunk.length > 0:
            chunks.append(chunk)
        start = end - 1
    return chunks


def gshhg_l1_exterior_linework(
    frame: gpd.GeoDataFrame,
    *,
    id_column: str = "id",
    level_column: str = "level",
    max_vertices: int = 512,
    dateline_tolerance_degrees: float = 1e-9,
) -> gpd.GeoDataFrame:
    """Convert L1 polygon exteriors to bounded, antimeridian-safe line chunks.

    Interior rings are deliberately excluded.  Official E/W component suffixes are
    retained for audit but collapse to one numeric ``source_id``.
    """

    if isinstance(max_vertices, bool) or max_vertices < 2:
        raise ValueError("Line chunks require at least two vertices.")
    if (
        isinstance(dateline_tolerance_degrees, bool)
        or not math.isfinite(dateline_tolerance_degrees)
        or not 0.0 <= dateline_tolerance_degrees <= 1e-6
    ):
        raise ValueError("Dateline tolerance must be finite and no larger than 1e-6 degree.")
    _require_wgs84(frame, label="GSHHG L1")
    _require_valid_polygons(frame, label="GSHHG L1")
    if id_column not in frame or not _level_is_exact(frame, level_column, 1):
        raise GshhgGeometryPilotError("GSHHG L1 identity or level schema changed.")

    records: list[dict[str, object]] = []
    ordered = frame.assign(
        _component_id=frame[id_column].map(lambda value: value if isinstance(value, str) else "")
    ).sort_values("_component_id", kind="stable")
    for _, row in ordered.iterrows():
        component_id = row[id_column]
        source_id = canonical_gshhg_source_id(component_id)
        geometry = row.geometry
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        component_chunk_count = 0
        for polygon_index, polygon in enumerate(polygons):
            runs = _coordinate_runs_without_dateline_seams(
                polygon,
                tolerance=dateline_tolerance_degrees,
            )
            for run_index, run in enumerate(runs):
                for chunk_index, chunk in enumerate(
                    _chunk_coordinate_run(run, max_vertices=max_vertices)
                ):
                    records.append(
                        {
                            "source_id": source_id,
                            "component_id": component_id,
                            "polygon_index": polygon_index,
                            "run_index": run_index,
                            "chunk_index": chunk_index,
                            "geometry": chunk,
                        }
                    )
                    component_chunk_count += 1
        if component_chunk_count == 0:
            raise GshhgGeometryPilotError(
                f"GSHHG L1 component {component_id!r} has no physical exterior line."
            )

    result = gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)
    if result.empty or result.geometry.is_empty.any() or not result.geometry.is_valid.all():
        raise GshhgGeometryPilotError("GSHHG L1 exterior conversion produced invalid linework.")
    return result.sort_values(
        ["source_id", "component_id", "polygon_index", "run_index", "chunk_index"],
        kind="stable",
    ).reset_index(drop=True)


def select_five_great_lakes(
    frame: gpd.GeoDataFrame,
    seeds: Sequence[LakeSeed],
    *,
    id_column: str = "id",
    level_column: str = "level",
    area_column: str = "area",
) -> gpd.GeoDataFrame:
    """Select exactly five distinct positive-area L2 polygons by fixed seeds.

    GSHHG shapefile L2 records with negative ``area`` are river-lakes.  They are
    excluded before seed containment is evaluated.
    """

    if len(seeds) != 5 or len({seed.name for seed in seeds}) != 5:
        raise GshhgGeometryPilotError("Exactly five uniquely named Great Lake seeds are required.")
    _require_wgs84(frame, label="GSHHG L2")
    _require_valid_polygons(frame, label="GSHHG L2")
    required = {id_column, level_column, area_column}
    if not required.issubset(frame.columns) or not _level_is_exact(frame, level_column, 2):
        raise GshhgGeometryPilotError("GSHHG L2 identity, level, or area schema changed.")
    if frame[id_column].map(lambda value: not isinstance(value, str)).any():
        raise GshhgGeometryPilotError("GSHHG L2 source IDs must be strings.")
    areas = pd.to_numeric(frame[area_column], errors="coerce")
    if areas.isna().any() or not np.isfinite(areas.to_numpy(dtype=np.float64)).all():
        raise GshhgGeometryPilotError("GSHHG L2 areas must be finite numbers.")

    positive = frame.loc[areas > 0].copy()
    if positive.empty:
        raise GshhgGeometryPilotError("GSHHG L2 has no non-river-lake polygons.")
    positive["_source_id"] = positive[id_column].map(canonical_gshhg_source_id)

    selected_indices: list[object] = []
    selected_source_ids: list[str] = []
    selected_names: list[str] = []
    for seed in seeds:
        point = Point(seed.longitude, seed.latitude)
        matches = shapely.contains(positive.geometry.to_numpy(), point)
        indices = list(positive.index[matches])
        if len(indices) != 1:
            raise GshhgGeometryPilotError(
                f"Great Lake seed {seed.name!r} matched {len(indices)} positive-area polygons."
            )
        index = indices[0]
        selected_indices.append(index)
        selected_source_ids.append(str(positive.loc[index, "_source_id"]))
        selected_names.append(seed.name)
    if len(set(selected_source_ids)) != 5:
        raise GshhgGeometryPilotError(
            "Great Lake seeds did not identify five distinct source polygons."
        )

    selected = positive.loc[selected_indices].copy()
    selected["lake_name"] = selected_names
    selected["source_id"] = selected_source_ids
    selected["river_lake_excluded"] = True
    return selected.drop(columns=["_source_id"]).reset_index(drop=True)


def _normalized_wkb_sha256(geometry: object) -> str:
    return hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()


def _polygonal_parts(geometry: object) -> list[object]:
    geometry_type = shapely.get_type_id(geometry)
    if geometry_type in {
        shapely.GeometryType.POLYGON,
        shapely.GeometryType.MULTIPOLYGON,
    }:
        return [geometry]
    if hasattr(geometry, "geoms"):
        parts: list[object] = []
        for part in geometry.geoms:
            parts.extend(_polygonal_parts(part))
        return parts
    return []


def repair_predeclared_l1_geometry(
    frame: gpd.GeoDataFrame,
    settings: Mapping[str, Any],
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    """Apply only the v2 source-structure repair frozen before distance access."""

    _require_wgs84(frame, label="GSHHG L1")
    if set(frame.geom_type) != {"Polygon"}:
        raise GshhgGeometryPilotError("GSHHG L1 must contain only Polygon records.")
    invalid_indices = list(frame.index[~frame.geometry.is_valid])
    expected_count = int(settings["allowed_invalid_polygon_count"])
    if len(invalid_indices) != expected_count or expected_count != 1:
        raise GshhgGeometryPilotError(
            f"GSHHG L1 invalid-polygon inventory changed: {len(invalid_indices)}."
        )
    index = invalid_indices[0]
    row = frame.loc[index]
    if (
        str(row["id"]) != str(settings["source_id"])
        or int(row["level"]) != int(settings["level"])
        or str(row["source"]) != str(settings["source"])
    ):
        raise GshhgGeometryPilotError("The sole invalid GSHHG L1 identity changed.")
    original = row.geometry
    reason = shapely.is_valid_reason(original)
    if reason != settings["validity_reason"]:
        raise GshhgGeometryPilotError("The predeclared GSHHG validity reason changed.")
    observed_bounds = [float(value) for value in original.bounds]
    if not np.allclose(
        observed_bounds,
        np.asarray(settings["expected_bounds"], dtype=np.float64),
        rtol=0.0,
        atol=1e-12,
    ):
        raise GshhgGeometryPilotError("The invalid GSHHG polygon bounds changed.")
    original_hash = _normalized_wkb_sha256(original)
    if original_hash != settings["original_normalized_wkb_sha256"]:
        raise GshhgGeometryPilotError("The invalid GSHHG polygon bytes changed.")

    repaired_container = shapely.make_valid(original)
    if repaired_container.geom_type != settings["expected_make_valid_container_type"]:
        raise GshhgGeometryPilotError("The make-valid container type changed.")
    polygonal_parts = _polygonal_parts(repaired_container)
    if not polygonal_parts:
        raise GshhgGeometryPilotError("The make-valid result has no polygonal component.")
    polygonal = shapely.union_all(polygonal_parts)
    if (
        polygonal.geom_type != settings["expected_polygonal_type"]
        or not shapely.is_valid(polygonal)
        or shapely.is_empty(polygonal)
    ):
        raise GshhgGeometryPilotError("The predeclared polygonal repair failed.")
    repaired_hash = _normalized_wkb_sha256(polygonal)
    if repaired_hash != settings["expected_polygonal_normalized_wkb_sha256"]:
        raise GshhgGeometryPilotError("The repaired GSHHG polygon semantics changed.")
    area_delta = abs(float(polygonal.area) - float(original.area))
    if area_delta > float(settings["maximum_planar_area_delta_square_degrees"]):
        raise GshhgGeometryPilotError("The GSHHG polygon repair changed too much area.")

    repaired = frame.copy()
    repaired.at[index, repaired.geometry.name] = polygonal
    if not repaired.geometry.is_valid.all():
        raise GshhgGeometryPilotError("GSHHG L1 still contains invalid geometry after repair.")
    return repaired, {
        "invalid_polygon_count": 1,
        "source_id": str(row["id"]),
        "validity_reason": reason,
        "bounds": observed_bounds,
        "original_normalized_wkb_sha256": original_hash,
        "make_valid_container_type": repaired_container.geom_type,
        "discarded_nonpolygonal_types": sorted(
            {
                part.geom_type
                for part in getattr(repaired_container, "geoms", ())
                if part.geom_type not in {"Polygon", "MultiPolygon"}
            }
        ),
        "repaired_geometry_type": polygonal.geom_type,
        "repaired_normalized_wkb_sha256": repaired_hash,
        "planar_area_delta_square_degrees": area_delta,
        "all_geometry_valid_after_repair": True,
        "selection_used_city_or_distance": False,
    }


def select_connected_great_lakes(
    frame: gpd.GeoDataFrame,
    seeds: Sequence[LakeSeed],
    settings: Mapping[str, Any],
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    """Authenticate five named seeds mapped to the frozen three connected waters."""

    if len(seeds) != int(settings["named_lake_count"]):
        raise GshhgGeometryPilotError("The v2 Great Lakes seed count changed.")
    _require_wgs84(frame, label="GSHHG L2")
    _require_valid_polygons(frame, label="GSHHG L2")
    if not _level_is_exact(frame, "level", 2):
        raise GshhgGeometryPilotError("GSHHG L2 level values changed.")
    areas = pd.to_numeric(frame["area"], errors="coerce")
    if areas.isna().any() or not np.isfinite(areas.to_numpy(dtype=np.float64)).all():
        raise GshhgGeometryPilotError("GSHHG L2 areas are not finite.")
    positive = frame.loc[areas > 0].copy()
    positive["_source_id"] = positive["id"].map(canonical_gshhg_source_id)

    expected_mapping = {
        str(record["name"]): str(record["source_id"]) for record in settings["seed_mapping"]
    }
    observed_mapping: list[dict[str, object]] = []
    for seed in seeds:
        matches = shapely.contains(
            positive.geometry.to_numpy(),
            Point(seed.longitude, seed.latitude),
        )
        indices = list(positive.index[matches])
        if len(indices) != 1:
            raise GshhgGeometryPilotError(
                f"Great Lake seed {seed.name!r} matched {len(indices)} polygons."
            )
        source_id = str(positive.loc[indices[0], "_source_id"])
        if expected_mapping.get(seed.name) != source_id:
            raise GshhgGeometryPilotError(f"Great Lake seed {seed.name!r} source mapping changed.")
        observed_mapping.append(
            {
                "name": seed.name,
                "longitude": seed.longitude,
                "latitude": seed.latitude,
                "source_id": source_id,
            }
        )

    expected_ids = [str(value) for value in settings["expected_source_ids"]]
    observed_ids = list(dict.fromkeys(record["source_id"] for record in observed_mapping))
    if observed_ids != expected_ids or len(observed_ids) != int(
        settings["expected_distinct_source_polygon_count"]
    ):
        raise GshhgGeometryPilotError("The connected Great Lakes source IDs changed.")

    source_contract = {str(record["source_id"]): record for record in settings["source_polygons"]}
    selected_rows: list[pd.Series] = []
    source_audit: list[dict[str, object]] = []
    for source_id in expected_ids:
        matches = positive.loc[positive["_source_id"].eq(source_id)]
        if len(matches) != 1:
            raise GshhgGeometryPilotError(
                f"Expected exactly one L2 polygon for source {source_id!r}."
            )
        row = matches.iloc[0]
        contract = source_contract[source_id]
        bounds = [float(value) for value in row.geometry.bounds]
        semantic_hash = _normalized_wkb_sha256(row.geometry)
        coordinate_count = int(shapely.get_num_coordinates(row.geometry))
        if (
            not math.isclose(
                float(row["area"]),
                float(contract["reported_area"]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or not np.allclose(
                bounds,
                np.asarray(contract["expected_bounds"], dtype=np.float64),
                rtol=0.0,
                atol=1e-12,
            )
            or coordinate_count != int(contract["expected_coordinate_count"])
            or semantic_hash != contract["expected_normalized_wkb_sha256"]
        ):
            raise GshhgGeometryPilotError(f"Connected Great Lake polygon {source_id!r} changed.")
        selected_rows.append(row)
        source_audit.append(
            {
                "source_id": source_id,
                "reported_area": float(row["area"]),
                "bounds": bounds,
                "coordinate_count": coordinate_count,
                "normalized_wkb_sha256": semantic_hash,
            }
        )

    selected = gpd.GeoDataFrame(selected_rows, geometry="geometry", crs=frame.crs)
    selected = selected.drop(columns=["_source_id"], errors="ignore").reset_index(drop=True)
    return selected, {
        "named_seed_count": len(seeds),
        "distinct_source_polygon_count": len(expected_ids),
        "seed_mapping": observed_mapping,
        "source_polygons": source_audit,
        "negative_area_river_lake_count_excluded": int((areas < 0).sum()),
        "zero_area_lake_count": int((areas == 0).sum()),
        "l3_island_shores_included": False,
        "selection_used_target_or_distance": False,
    }


def _as_geometry_series(
    value: gpd.GeoDataFrame | gpd.GeoSeries,
    *,
    label: str,
    allowed_types: set[str],
) -> gpd.GeoSeries:
    series = value.geometry if isinstance(value, gpd.GeoDataFrame) else value
    if not isinstance(series, gpd.GeoSeries) or series.empty or series.crs is None:
        raise GshhgGeometryPilotError(f"{label} must be a non-empty GeoSeries with a CRS.")
    if (
        series.isna().any()
        or series.is_empty.any()
        or not series.is_valid.all()
        or not series.geom_type.isin(allowed_types).all()
    ):
        raise GshhgGeometryPilotError(f"{label} has invalid or unsupported geometry.")
    coordinates = shapely.get_coordinates(series.to_numpy())
    if coordinates.size == 0 or not np.isfinite(coordinates).all():
        raise GshhgGeometryPilotError(f"{label} has non-finite coordinates.")
    return series


def _projected_metre_crs(value: str | CRS) -> CRS:
    crs = CRS.from_user_input(value)
    if not crs.is_projected or len(crs.axis_info) < 2:
        raise GshhgGeometryPilotError("Distance CRS must be projected.")
    factors = [axis.unit_conversion_factor for axis in crs.axis_info[:2]]
    if any(
        factor is None
        or not math.isfinite(float(factor))
        or not math.isclose(float(factor), 1.0, abs_tol=1e-12, rel_tol=0.0)
        for factor in factors
    ):
        raise GshhgGeometryPilotError("Distance CRS axes must use metres.")
    return crs


def _project_distance_inputs(
    points: gpd.GeoDataFrame | gpd.GeoSeries,
    lines: gpd.GeoDataFrame | gpd.GeoSeries,
    projected_crs: str | CRS,
) -> tuple[np.ndarray, np.ndarray, CRS]:
    point_series = _as_geometry_series(points, label="Distance points", allowed_types={"Point"})
    line_series = _as_geometry_series(
        lines,
        label="Distance lines",
        allowed_types={"LineString", "MultiLineString"},
    )
    point_crs = CRS.from_user_input(point_series.crs)
    line_crs = CRS.from_user_input(line_series.crs)
    if not point_crs.equals(line_crs):
        raise GshhgGeometryPilotError("Distance points and lines must share one source CRS.")
    destination = _projected_metre_crs(projected_crs)
    projected_points = point_series.to_crs(destination).to_numpy()
    projected_lines = line_series.to_crs(destination).to_numpy()
    for label, values in (
        ("Projected points", projected_points),
        ("Projected lines", projected_lines),
    ):
        coordinates = shapely.get_coordinates(values)
        if (
            coordinates.size == 0
            or not np.isfinite(coordinates).all()
            or not shapely.is_valid(values).all()
            or shapely.is_empty(values).any()
        ):
            raise GshhgGeometryPilotError(f"{label} are empty, invalid, or non-finite.")
    return projected_points, projected_lines, destination


def _nearest_strtree_arrays(points: np.ndarray, lines: np.ndarray) -> np.ndarray:
    tree = shapely.STRtree(lines)
    indices, distances = tree.query_nearest(
        points,
        all_matches=False,
        return_distance=True,
    )
    if indices.ndim != 2 or indices.shape[0] != 2:
        raise GshhgGeometryPilotError("STRtree returned an unexpected nearest-query shape.")
    result = np.full(points.size, np.nan, dtype=np.float64)
    query_indices = indices[0]
    if query_indices.size != points.size or len(set(query_indices.tolist())) != points.size:
        raise GshhgGeometryPilotError("STRtree omitted or duplicated a query point.")
    result[query_indices] = np.asarray(distances, dtype=np.float64)
    if not np.isfinite(result).all() or (result < 0).any():
        raise GshhgGeometryPilotError("STRtree returned invalid distances.")
    return result


def _nearest_bruteforce_arrays(points: np.ndarray, lines: np.ndarray) -> np.ndarray:
    result = np.asarray(
        [float(np.min(shapely.distance(point, lines))) for point in points],
        dtype=np.float64,
    )
    if not np.isfinite(result).all() or (result < 0).any():
        raise GshhgGeometryPilotError("Brute-force query returned invalid distances.")
    return result


def nearest_projected_strtree(
    points: gpd.GeoDataFrame | gpd.GeoSeries,
    lines: gpd.GeoDataFrame | gpd.GeoSeries,
    projected_crs: str | CRS,
) -> np.ndarray:
    """Return float64 exact projected nearest-line distances in metres."""

    projected_points, projected_lines, _ = _project_distance_inputs(
        points,
        lines,
        projected_crs,
    )
    return _nearest_strtree_arrays(projected_points, projected_lines)


def nearest_projected_bruteforce(
    points: gpd.GeoDataFrame | gpd.GeoSeries,
    lines: gpd.GeoDataFrame | gpd.GeoSeries,
    projected_crs: str | CRS,
) -> np.ndarray:
    """Return the independent O(points x lines) projected reference distances."""

    projected_points, projected_lines, _ = _project_distance_inputs(
        points,
        lines,
        projected_crs,
    )
    return _nearest_bruteforce_arrays(projected_points, projected_lines)


def expanding_radius_distances(
    points: gpd.GeoDataFrame | gpd.GeoSeries,
    lines: gpd.GeoDataFrame | gpd.GeoSeries,
    projected_crs: str | CRS,
    *,
    radii_km: Sequence[float],
    method: Literal["strtree", "bruteforce"] = "strtree",
) -> RadiusDistanceResult:
    """Apply a strict expanding-radius gate to already supplied source linework."""

    if not radii_km:
        raise ValueError("At least one search radius is required.")
    radii = np.asarray(radii_km, dtype=np.float64)
    if not np.isfinite(radii).all() or (radii <= 0).any() or not np.all(np.diff(radii) > 0):
        raise ValueError("Search radii must be finite, positive, and strictly increasing.")
    projected_points, projected_lines, _ = _project_distance_inputs(
        points,
        lines,
        projected_crs,
    )
    query = (
        _nearest_strtree_arrays
        if method == "strtree"
        else _nearest_bruteforce_arrays
        if method == "bruteforce"
        else None
    )
    if query is None:
        raise ValueError(f"Unsupported nearest-distance method: {method!r}.")

    line_tree = shapely.STRtree(projected_lines)
    point_bounds = shapely.total_bounds(projected_points)
    for radius_km in radii:
        radius_m = float(radius_km * 1000.0)
        search_box = shapely.box(
            point_bounds[0] - radius_m,
            point_bounds[1] - radius_m,
            point_bounds[2] + radius_m,
            point_bounds[3] + radius_m,
        )
        candidate_indices = np.unique(line_tree.query(search_box))
        if candidate_indices.size == 0:
            continue
        distances = query(projected_points, projected_lines[candidate_indices])
        if float(np.max(distances)) < radius_m:
            return RadiusDistanceResult(
                distances_m=distances,
                accepted_radius_km=float(radius_km),
                candidate_count=int(candidate_indices.size),
            )
    raise GshhgGeometryPilotError(
        "Search-radius ladder was exhausted before every distance was strictly interior."
    )


def _densified_geodesic_line(
    line: LineString,
    *,
    max_step_m: float,
    geod: Geod,
) -> LineString:
    coordinates = [(float(longitude), float(latitude)) for longitude, latitude, *_ in line.coords]
    densified: list[tuple[float, float]] = [coordinates[0]]
    for left, right in zip(coordinates, coordinates[1:], strict=False):
        _, _, segment_m = geod.inv(left[0], left[1], right[0], right[1])
        if not math.isfinite(segment_m):
            raise GshhgGeometryPilotError("Geodesic source segment is non-finite.")
        subdivisions = max(1, int(math.ceil(segment_m / max_step_m)))
        if subdivisions > 1:
            densified.extend(
                geod.npts(
                    left[0],
                    left[1],
                    right[0],
                    right[1],
                    subdivisions - 1,
                )
            )
        densified.append(right)
    return LineString(densified)


def geodesic_reference_distances(
    points: gpd.GeoDataFrame | gpd.GeoSeries,
    lines: gpd.GeoDataFrame | gpd.GeoSeries,
    *,
    max_step_m: float = 100.0,
) -> np.ndarray:
    """Return the frozen point-to-vertex WGS84 geodesic reference distance.

    Each source segment is densified along WGS84 geodesics and the minimum
    ``pyproj.Geod`` distance from each query point to any resulting vertex is
    returned.  ``max_step_m`` bounds the along-segment vertex spacing.
    """

    if isinstance(max_step_m, bool) or not math.isfinite(max_step_m) or max_step_m <= 0:
        raise ValueError("Geodesic densification step must be finite and positive.")
    point_series = _as_geometry_series(points, label="Geodesic points", allowed_types={"Point"})
    line_series = _as_geometry_series(
        lines,
        label="Geodesic lines",
        allowed_types={"LineString", "MultiLineString"},
    )
    if not CRS.from_user_input(point_series.crs).equals(WGS84) or not CRS.from_user_input(
        line_series.crs
    ).equals(WGS84):
        raise GshhgGeometryPilotError("Geodesic reference inputs must use WGS84.")

    simple_lines: list[LineString] = []
    for geometry in line_series:
        if geometry.geom_type == "MultiLineString":
            simple_lines.extend(geometry.geoms)
        else:
            simple_lines.append(geometry)
    densified = [
        _densified_geodesic_line(
            line,
            max_step_m=max_step_m,
            geod=_DEFAULT_GEOD,
        )
        for line in simple_lines
    ]

    vertices = np.concatenate(
        [np.asarray(line.coords, dtype=np.float64)[:, :2] for line in densified],
        axis=0,
    )
    vertex_longitudes = vertices[:, 0]
    vertex_latitudes = vertices[:, 1]
    result = np.full(len(point_series), np.nan, dtype=np.float64)
    for index, point in enumerate(point_series):
        _, _, distances = _DEFAULT_GEOD.inv(
            np.full(vertices.shape[0], point.x, dtype=np.float64),
            np.full(vertices.shape[0], point.y, dtype=np.float64),
            vertex_longitudes,
            vertex_latitudes,
        )
        result[index] = float(np.min(np.asarray(distances, dtype=np.float64)))
    if not np.isfinite(result).all() or (result < 0).any():
        raise GshhgGeometryPilotError("Geodesic reference returned invalid distances.")
    return result


def require_projected_geodesic_parity(
    projected_m: Sequence[float] | np.ndarray,
    geodesic_m: Sequence[float] | np.ndarray,
    *,
    absolute_tolerance_m: float,
    relative_tolerance: float,
) -> dict[str, float]:
    """Fail when projected distances exceed a predeclared geodesic tolerance."""

    projected = np.asarray(projected_m, dtype=np.float64)
    geodesic = np.asarray(geodesic_m, dtype=np.float64)
    if projected.shape != geodesic.shape or projected.ndim != 1 or projected.size == 0:
        raise ValueError("Projected and geodesic distance vectors must share one non-empty shape.")
    if not np.isfinite(projected).all() or not np.isfinite(geodesic).all():
        raise ValueError("Projected and geodesic distances must be finite.")
    if (
        isinstance(absolute_tolerance_m, bool)
        or isinstance(relative_tolerance, bool)
        or not math.isfinite(absolute_tolerance_m)
        or not math.isfinite(relative_tolerance)
        or absolute_tolerance_m < 0
        or relative_tolerance < 0
    ):
        raise ValueError("Projected/geodesic tolerances must be finite and non-negative.")
    differences = np.abs(projected - geodesic)
    limits = np.maximum(absolute_tolerance_m, relative_tolerance * geodesic)
    if np.any(differences > limits):
        raise GshhgGeometryPilotError(
            "Projected distances exceed the predeclared geodesic tolerance."
        )
    return {
        "maximum_absolute_difference_m": float(np.max(differences)),
        "maximum_relative_difference": float(
            np.max(differences / np.maximum(geodesic, np.finfo(np.float64).eps))
        ),
        "absolute_tolerance_m": float(absolute_tolerance_m),
        "relative_tolerance": float(relative_tolerance),
    }


def _read_exact_configs(
    path: str | Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    amendment_path = Path(path).resolve()
    if not amendment_path.is_file():
        raise FileNotFoundError(amendment_path)
    if sha256_file(amendment_path) != EXPECTED_V2_CONFIG_SHA256:
        raise GshhgGeometryPilotError("The v2 source-structure amendment changed.")
    with amendment_path.open("rb") as handle:
        amendment = tomllib.load(handle)
    if set(amendment) != {
        "amendment",
        "v1_failure",
        "invalid_geometry_repair",
        "great_lakes_connected_water_contract",
        "amendment_access_record",
    }:
        raise GshhgGeometryPilotError("The v2 amendment schema changed.")
    identity = amendment["amendment"]
    if (
        identity.get("schema_version") != SCHEMA_VERSION
        or identity.get("algorithm_version") != ALGORITHM_VERSION
        or identity.get("completion_state") != COMPLETE_STATE
        or identity.get("all_v1_distance_points_thresholds_and_access_locks_unchanged") is not True
    ):
        raise GshhgGeometryPilotError("The v2 pilot identity or unchanged-gates claim changed.")
    project_root = amendment_path.parents[2]
    base_path = project_root / str(identity["base_config"])
    if (
        not base_path.is_file()
        or sha256_file(base_path) != EXPECTED_V1_CONFIG_SHA256
        or identity.get("base_config_sha256") != EXPECTED_V1_CONFIG_SHA256
    ):
        raise GshhgGeometryPilotError("The v1 preregistration identity changed.")
    with base_path.open("rb") as handle:
        base = tomllib.load(handle)
    expected_base_tables = {
        "pilot",
        "source",
        "archive_security",
        "required_member_sha256",
        "geometry_contract",
        "distance_audit",
        "comparison",
        "access_contract",
        "great_lake_seeds",
        "diagnostic_points",
    }
    if set(base) != expected_base_tables:
        raise GshhgGeometryPilotError("The v1 preregistration schema changed.")
    if (
        base["pilot"].get("algorithm_version") != "gshhg-geometry-pilot-v1"
        or len(base["great_lake_seeds"]) != 5
        or len(base["diagnostic_points"]) != 4
        or base["comparison"].get("source_decision_allowed") is not False
        or base["comparison"].get("predictor_construction_allowed") is not False
    ):
        raise GshhgGeometryPilotError("The v1 scientific or access contract changed.")
    return amendment_path, amendment, base_path, base


def _source_inventory_records(archive_path: Path) -> tuple[list[dict[str, object]], str]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            records = [
                {
                    "name": member.filename,
                    "bytes": member.file_size,
                    "compressed_bytes": member.compress_size,
                    "crc32": f"{member.CRC:08x}",
                    "method": member.compress_type,
                    "flags": member.flag_bits,
                    "external_attr": member.external_attr,
                }
                for member in archive.infolist()
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise GshhgGeometryPilotError("Cannot read the GSHHG member inventory.") from exc
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return records, hashlib.sha256(encoded).hexdigest()


def _required_member_hashes(
    archive_path: Path,
    required_members: Sequence[str],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for name in sorted(required_members):
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                hashes[name] = digest.hexdigest()
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise GshhgGeometryPilotError("Cannot hash a required GSHHG member.") from exc
    return hashes


def _audit_archive(
    archive_path: Path,
    base: Mapping[str, Any],
    *,
    project_root: Path,
) -> dict[str, object]:
    source = base["source"]
    security = base["archive_security"]
    limits = ZipSafetyLimits(
        max_members=int(security["maximum_member_count"]),
        max_member_uncompressed_bytes=int(security["maximum_single_member_bytes"]),
        max_total_uncompressed_bytes=int(security["maximum_total_uncompressed_bytes"]),
        max_compression_ratio=float(security["maximum_compression_ratio"]),
    )
    validation = validate_pinned_zip(
        archive_path,
        expected_sha256=str(source["expected_sha256"]),
        expected_bytes=int(source["expected_bytes"]),
        required_members=[str(value) for value in security["required_members"]],
        limits=limits,
    )
    records, inventory_hash = _source_inventory_records(archive_path)
    if (
        len(records) != int(source["expected_member_count"])
        or sum(int(record["bytes"]) for record in records)
        != int(source["expected_total_uncompressed_bytes"])
        or inventory_hash != source["expected_member_inventory_sha256"]
    ):
        raise GshhgGeometryPilotError("The complete GSHHG member inventory changed.")
    allowed_methods = {int(value) for value in security["allowed_compression_methods"]}
    observed_methods = sorted({int(record["method"]) for record in records})
    if set(observed_methods) != allowed_methods:
        raise GshhgGeometryPilotError("The GSHHG ZIP compression methods changed.")
    member_hashes = _required_member_hashes(
        archive_path,
        [str(value) for value in security["required_members"]],
    )
    if member_hashes != {
        str(name): str(value) for name, value in base["required_member_sha256"].items()
    }:
        raise GshhgGeometryPilotError("A required GSHHG member hash changed.")
    published_md5 = hashlib.md5(usedforsecurity=False)  # noqa: S324
    with archive_path.open("rb") as source_handle:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            published_md5.update(chunk)
    observed_md5 = published_md5.hexdigest()
    if observed_md5 != str(source["published_md5"]):
        raise GshhgGeometryPilotError("The archive does not match the published MD5.")
    return {
        "source_id": source["source_id"],
        "publisher": source["publisher"],
        "dataset": source["dataset"],
        "version": source["version"],
        "release_date": source["release_date"],
        "path": archive_path.relative_to(project_root).as_posix(),
        "official_url": source["official_url"],
        "maintainer_mirror": source["maintainer_mirror"],
        "release_record": source["release_record"],
        "license": source["license"],
        "bytes": int(validation["bytes"]),
        "sha256": validation["sha256"],
        "published_md5": str(source["published_md5"]),
        "observed_md5": observed_md5,
        "published_md5_matched": True,
        "member_count": len(records),
        "total_uncompressed_bytes": int(validation["total_uncompressed_bytes"]),
        "source_member_inventory_sha256": inventory_hash,
        "security_inventory_sha256": validation["member_inventory_sha256"],
        "compression_methods": observed_methods,
        "required_member_sha256": member_hashes,
        "all_member_crc_passed": validation["archive_testzip_passed"],
        "archive_extracted": validation["extracted"],
    }


def _layer_semantic_sha256(frame: gpd.GeoDataFrame) -> str:
    record_hashes: list[str] = []
    columns = ("id", "level", "source", "parent_id", "sibling_id", "area")
    ordered = frame.sort_values(["id"], kind="stable")
    for row in ordered.itertuples(index=False):
        attributes = {column: getattr(row, column) for column in columns}
        attributes["normalized_wkb_sha256"] = _normalized_wkb_sha256(
            getattr(row, frame.geometry.name)
        )
        record_hashes.append(canonical_sha256(attributes))
    return canonical_sha256(
        {
            "crs": CRS.from_user_input(frame.crs).to_string(),
            "columns": list(columns),
            "record_hashes": record_hashes,
        }
    )


def _audit_source_layer(
    frame: gpd.GeoDataFrame,
    *,
    label: str,
    expected_level: int,
    expected_rows: int,
    allow_invalid_count: int,
) -> dict[str, object]:
    expected_columns = {
        "id",
        "level",
        "source",
        "parent_id",
        "sibling_id",
        "area",
        "geometry",
    }
    if set(frame.columns) != expected_columns:
        raise GshhgGeometryPilotError(f"{label} attribute schema changed.")
    _require_wgs84(frame, label=label)
    if len(frame) != expected_rows or not _level_is_exact(frame, "level", expected_level):
        raise GshhgGeometryPilotError(f"{label} row count or level changed.")
    if frame["id"].map(lambda value: not isinstance(value, str)).any():
        raise GshhgGeometryPilotError(f"{label} IDs must be strings.")
    if frame["id"].duplicated().any():
        raise GshhgGeometryPilotError(f"{label} IDs must be unique.")
    if set(frame.geom_type) != {"Polygon"}:
        raise GshhgGeometryPilotError(f"{label} must contain only Polygon geometry.")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise GshhgGeometryPilotError(f"{label} contains missing or empty geometry.")
    invalid_count = int((~frame.geometry.is_valid).sum())
    if invalid_count != allow_invalid_count:
        raise GshhgGeometryPilotError(f"{label} invalid-geometry count changed.")
    areas = pd.to_numeric(frame["area"], errors="coerce").to_numpy(dtype=np.float64)
    if not np.isfinite(areas).all():
        raise GshhgGeometryPilotError(f"{label} area values are not finite.")
    coordinates = shapely.get_coordinates(frame.geometry.to_numpy())
    if (
        coordinates.size == 0
        or not np.isfinite(coordinates).all()
        or np.max(np.abs(coordinates[:, 0])) > 180.0
        or np.max(np.abs(coordinates[:, 1])) > 90.0
    ):
        raise GshhgGeometryPilotError(f"{label} coordinates are invalid.")
    return {
        "row_count": len(frame),
        "columns": list(frame.columns),
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "crs": CRS.from_user_input(frame.crs).to_string(),
        "bounds": [float(value) for value in frame.total_bounds],
        "geometry_types": {
            str(name): int(count) for name, count in frame.geom_type.value_counts().items()
        },
        "source_counts": {
            str(name): int(count) for name, count in frame["source"].value_counts().items()
        },
        "invalid_geometry_count": invalid_count,
        "negative_area_count": int((areas < 0).sum()),
        "zero_area_count": int((areas == 0).sum()),
        "positive_area_count": int((areas > 0).sum()),
        "attribute_geometry_semantic_sha256": _layer_semantic_sha256(frame),
    }


def _conservative_envelope(
    longitude: float,
    latitude: float,
    radius_km: float,
) -> Polygon:
    latitude_delta = radius_km / 110.0 * 1.05
    latitude_for_scale = min(89.0, abs(latitude) + latitude_delta)
    longitude_delta = (
        radius_km / (111.0 * max(math.cos(math.radians(latitude_for_scale)), 0.05)) * 1.05
    )
    west = longitude - longitude_delta
    east = longitude + longitude_delta
    south = max(-90.0, latitude - latitude_delta)
    north = min(90.0, latitude + latitude_delta)
    if west <= -180.0 or east >= 180.0:
        raise GshhgGeometryPilotError(
            "The four-city pilot does not permit an antimeridian-wrapping search."
        )
    return shapely.box(west, south, east, north)


def _read_source_layers(
    archive_path: Path,
    base: Mapping[str, Any],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    geometry_contract = base["geometry_contract"]
    frames: list[gpd.GeoDataFrame] = []
    for member in (
        geometry_contract["ocean_layer"],
        geometry_contract["lake_layer"],
    ):
        uri = f"zip://{archive_path.resolve()}!{member}"
        try:
            frames.append(gpd.read_file(uri))
        except Exception as exc:
            raise GshhgGeometryPilotError(
                f"Cannot read authenticated GSHHG layer {member!r}."
            ) from exc
    return frames[0], frames[1]


def _regional_linework_variants(
    repaired_l1: gpd.GeoDataFrame,
    selected_lakes: gpd.GeoDataFrame,
    diagnostic_points: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    geometry_contract: Mapping[str, Any],
) -> tuple[dict[int, gpd.GeoDataFrame], dict[str, object]]:
    max_radius = float(max(settings["search_radii_km"]))
    regional_indices: set[int] = set()
    for point in diagnostic_points:
        envelope = _conservative_envelope(
            float(point["longitude"]),
            float(point["latitude"]),
            max_radius,
        )
        regional_indices.update(
            int(value)
            for value in repaired_l1.sindex.query(
                envelope,
                predicate="intersects",
            )
        )
    regional_l1 = repaired_l1.iloc[sorted(regional_indices)].copy()
    if regional_l1.empty:
        raise GshhgGeometryPilotError("No regional GSHHG L1 polygons were selected.")
    lake_as_l1 = selected_lakes.copy()
    lake_as_l1["level"] = 1
    variants: dict[int, gpd.GeoDataFrame] = {}
    for chunk_size_value in settings["line_chunk_vertex_counts"]:
        chunk_size = int(chunk_size_value)
        ocean = gshhg_l1_exterior_linework(
            regional_l1,
            max_vertices=chunk_size,
            dateline_tolerance_degrees=float(geometry_contract["dateline_tolerance_degrees"]),
        )
        ocean["shoreline_class"] = "global_ocean_l1"
        lakes = gshhg_l1_exterior_linework(
            lake_as_l1,
            max_vertices=chunk_size,
            dateline_tolerance_degrees=float(geometry_contract["dateline_tolerance_degrees"]),
        )
        lakes["shoreline_class"] = "three_seed_selected_l2_connected_waters"
        variants[chunk_size] = gpd.GeoDataFrame(
            pd.concat([ocean, lakes], ignore_index=True),
            geometry="geometry",
            crs=WGS84,
        )
    return variants, {
        "maximum_search_radius_km": max_radius,
        "global_l1_polygon_count": len(repaired_l1),
        "regional_l1_polygon_count": len(regional_l1),
        "selected_l2_polygon_count": len(selected_lakes),
        "chunk_variant_line_counts": {
            str(chunk_size): len(frame) for chunk_size, frame in variants.items()
        },
        "global_layer_projected_at_once": False,
        "native_crs_prefilter_used": True,
    }


def _candidate_lines(
    linework: gpd.GeoDataFrame,
    envelope: Polygon,
) -> gpd.GeoDataFrame:
    indices = np.unique(linework.sindex.query(envelope, predicate="intersects"))
    if indices.size == 0:
        return linework.iloc[0:0].copy()
    return linework.iloc[indices].reset_index(drop=True)


def _target_blind_label(preregistered_label: str) -> str:
    """Correct an immutable v1 presentation label without rewriting history."""

    return preregistered_label.replace("fixed unlabeled ", "fixed target-blind ", 1)


def _nearest_source_evidence(
    point_geometry: gpd.GeoSeries,
    candidates: gpd.GeoDataFrame,
    projected_crs: str,
    *,
    expected_distance_m: float,
    tie_tolerance_m: float,
) -> dict[str, object]:
    """Identify and locate one stable nearest source segment for audit evidence."""

    required = {"source_id", "component_id", "shoreline_class", "geometry"}
    if not required.issubset(candidates.columns):
        raise GshhgGeometryPilotError("Nearest-source evidence columns are missing.")
    projected_point = point_geometry.to_crs(projected_crs).iloc[0]
    projected_candidates = candidates.to_crs(projected_crs)
    distances = np.asarray(
        shapely.distance(
            projected_point,
            projected_candidates.geometry.to_numpy(),
        ),
        dtype=np.float64,
    )
    if distances.size == 0 or not np.isfinite(distances).all():
        raise GshhgGeometryPilotError("Nearest-source evidence distances are invalid.")
    minimum = float(np.min(distances))
    if abs(minimum - expected_distance_m) > tie_tolerance_m:
        raise GshhgGeometryPilotError(
            "Nearest-source evidence disagrees with the canonical distance."
        )
    tie_indices = np.flatnonzero(np.abs(distances - minimum) <= tie_tolerance_m).tolist()
    identity_columns = (
        "shoreline_class",
        "source_id",
        "component_id",
        "polygon_index",
        "run_index",
        "chunk_index",
    )

    def identity(index: int) -> tuple[str, ...]:
        row = candidates.iloc[index]
        return tuple(
            "" if column not in candidates else str(row[column]) for column in identity_columns
        )

    selected_index = min(tie_indices, key=identity)
    selected = candidates.iloc[selected_index]
    projected_selected = projected_candidates.geometry.iloc[selected_index]
    connector = shapely.shortest_line(projected_point, projected_selected)
    if connector.is_empty or len(connector.coords) < 2:
        raise GshhgGeometryPilotError("Cannot locate the nearest source coordinate.")
    nearest_x, nearest_y = connector.coords[-1][:2]
    transformer = Transformer.from_crs(
        CRS.from_user_input(projected_crs),
        WGS84,
        always_xy=True,
    )
    nearest_longitude, nearest_latitude = transformer.transform(
        nearest_x,
        nearest_y,
    )
    evidence: dict[str, object] = {
        "source_id": str(selected["source_id"]),
        "component_id": str(selected["component_id"]),
        "shoreline_class": str(selected["shoreline_class"]),
        "polygon_index": (
            None if "polygon_index" not in candidates else int(selected["polygon_index"])
        ),
        "run_index": (None if "run_index" not in candidates else int(selected["run_index"])),
        "chunk_index": (None if "chunk_index" not in candidates else int(selected["chunk_index"])),
        "nearest_longitude": float(nearest_longitude),
        "nearest_latitude": float(nearest_latitude),
        "projected_distance_m": minimum,
        "equidistant_candidate_count": len(tie_indices),
        "tie_tolerance_m": tie_tolerance_m,
    }
    if "source_name" in candidates:
        value = selected["source_name"]
        evidence["source_name"] = None if pd.isna(value) else str(value)
    return evidence


def _distance_record(
    point: Mapping[str, Any],
    linework: gpd.GeoDataFrame,
    source_name: str,
    settings: Mapping[str, Any],
    *,
    include_geodesic: bool,
) -> tuple[dict[str, object], gpd.GeoDataFrame]:
    longitude = float(point["longitude"])
    latitude = float(point["latitude"])
    projected_crs = str(point["projected_crs"])
    point_geometry = gpd.GeoSeries(
        [Point(longitude, latitude)],
        crs=WGS84,
    )
    accepted_candidates: gpd.GeoDataFrame | None = None
    accepted_radius = math.nan
    indexed_distance = math.nan
    brute_distance = math.nan
    radii_audit: list[dict[str, object]] = []
    for radius_value in settings["search_radii_km"]:
        radius_km = float(radius_value)
        candidates = _candidate_lines(
            linework,
            _conservative_envelope(longitude, latitude, radius_km),
        )
        if candidates.empty:
            radii_audit.append(
                {
                    "radius_km": radius_km,
                    "candidate_count": 0,
                    "accepted": False,
                }
            )
            continue
        indexed = float(
            nearest_projected_strtree(
                point_geometry,
                candidates,
                projected_crs,
            )[0]
        )
        brute = float(
            nearest_projected_bruteforce(
                point_geometry,
                candidates,
                projected_crs,
            )[0]
        )
        parity_error = abs(indexed - brute)
        if parity_error > float(settings["strtree_bruteforce_absolute_tolerance_m"]):
            raise GshhgGeometryPilotError("STRtree and brute-force diagnostic distances disagree.")
        accepted = indexed < radius_km * 1000.0
        radii_audit.append(
            {
                "radius_km": radius_km,
                "candidate_count": len(candidates),
                "strtree_distance_m": indexed,
                "bruteforce_distance_m": brute,
                "absolute_difference_m": parity_error,
                "accepted": accepted,
            }
        )
        if accepted:
            accepted_candidates = candidates
            accepted_radius = radius_km
            indexed_distance = indexed
            brute_distance = brute
            break
    if accepted_candidates is None:
        raise GshhgGeometryPilotError(
            f"Radius ladder exhausted for {point['city_id']} and {source_name}."
        )

    invariance_values: list[dict[str, object]] = []
    for radius_value in settings["search_radii_km"]:
        radius_km = float(radius_value)
        if radius_km < accepted_radius:
            continue
        candidates = _candidate_lines(
            linework,
            _conservative_envelope(longitude, latitude, radius_km),
        )
        distance = float(
            nearest_projected_strtree(
                point_geometry,
                candidates,
                projected_crs,
            )[0]
        )
        difference = abs(distance - indexed_distance)
        if difference > float(settings["invariance_absolute_tolerance_m"]):
            raise GshhgGeometryPilotError("Diagnostic distance changed under radius expansion.")
        invariance_values.append(
            {
                "radius_km": radius_km,
                "candidate_count": len(candidates),
                "distance_m": distance,
                "absolute_difference_from_canonical_m": difference,
            }
        )
    reversed_distance = float(
        nearest_projected_strtree(
            point_geometry,
            accepted_candidates.iloc[::-1].reset_index(drop=True),
            projected_crs,
        )[0]
    )
    source_order_difference = abs(reversed_distance - indexed_distance)
    if source_order_difference > float(settings["invariance_absolute_tolerance_m"]):
        raise GshhgGeometryPilotError("Diagnostic distance changed with source order.")

    geodesic_distance: float | None = None
    geodesic_audit: dict[str, float] | None = None
    if include_geodesic:
        geodesic_distance = float(
            geodesic_reference_distances(
                point_geometry,
                accepted_candidates,
                max_step_m=float(settings["geodesic_densification_max_step_m"]),
            )[0]
        )
        geodesic_audit = require_projected_geodesic_parity(
            [indexed_distance],
            [geodesic_distance],
            absolute_tolerance_m=float(settings["geodesic_absolute_tolerance_m"]),
            relative_tolerance=float(settings["geodesic_relative_tolerance"]),
        )
    evidence = _nearest_source_evidence(
        point_geometry,
        accepted_candidates,
        projected_crs,
        expected_distance_m=indexed_distance,
        tie_tolerance_m=float(settings["invariance_absolute_tolerance_m"]),
    )
    preregistered_label = str(point["label"])
    return (
        {
            "city_id": str(point["city_id"]),
            "label": _target_blind_label(preregistered_label),
            "preregistration_label": preregistered_label,
            "longitude": longitude,
            "latitude": latitude,
            "projected_crs": projected_crs,
            "source": source_name,
            "distance_m": indexed_distance,
            "distance_km": indexed_distance / 1000.0,
            "accepted_radius_km": accepted_radius,
            "accepted_candidate_count": len(accepted_candidates),
            "bruteforce_distance_m": brute_distance,
            "strtree_bruteforce_absolute_difference_m": abs(indexed_distance - brute_distance),
            "source_order_reversed_distance_m": reversed_distance,
            "source_order_absolute_difference_m": source_order_difference,
            "radius_audit": radii_audit,
            "radius_invariance": invariance_values,
            "geodesic_distance_m": geodesic_distance,
            "projected_minus_geodesic_m": (
                None if geodesic_distance is None else indexed_distance - geodesic_distance
            ),
            "geodesic_audit": geodesic_audit,
            "nearest_source_evidence": evidence,
        },
        accepted_candidates,
    )


def _thread_and_query_chunk_audit(
    tasks: Mapping[
        tuple[str, str],
        tuple[Mapping[str, Any], gpd.GeoDataFrame, float],
    ],
    settings: Mapping[str, Any],
) -> dict[str, object]:
    tolerance = float(settings["invariance_absolute_tolerance_m"])
    chunk_sizes = [int(value) for value in settings["query_chunk_sizes"]]
    replicate_count = max(chunk_sizes)

    def query(
        task: tuple[tuple[str, str], int],
    ) -> tuple[tuple[str, str], list[float]]:
        key, query_count = task
        point, candidates, _ = tasks[key]
        point_geometry = gpd.GeoSeries(
            [
                Point(float(point["longitude"]), float(point["latitude"]))
                for _ in range(query_count)
            ],
            crs=WGS84,
        )
        values = nearest_projected_strtree(
            point_geometry,
            candidates,
            str(point["projected_crs"]),
        )
        return key, [float(value) for value in values]

    keys = sorted(tasks)
    runs: list[dict[str, object]] = []
    maximum_difference = 0.0
    for worker_value in settings["worker_counts"]:
        workers = int(worker_value)
        for query_chunk_size in chunk_sizes:
            observed: dict[tuple[str, str], list[float]] = {key: [] for key in keys}
            chunk_tasks: list[tuple[tuple[str, str], int]] = []
            for key in keys:
                remaining = replicate_count
                while remaining:
                    query_count = min(query_chunk_size, remaining)
                    chunk_tasks.append((key, query_count))
                    remaining -= query_count
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for key, values in executor.map(query, chunk_tasks):
                    observed[key].extend(values)
            differences = {
                f"{city_id}:{source}": max(
                    abs(value - tasks[(city_id, source)][2])
                    for value in observed[(city_id, source)]
                )
                for city_id, source in keys
            }
            if any(len(values) != replicate_count for values in observed.values()):
                raise GshhgGeometryPilotError(
                    "A vector query-chunk audit omitted or duplicated a query."
                )
            run_maximum = max(differences.values(), default=0.0)
            maximum_difference = max(maximum_difference, run_maximum)
            if run_maximum > tolerance:
                raise GshhgGeometryPilotError(
                    "Diagnostic distances changed with worker or query chunk count."
                )
            runs.append(
                {
                    "workers": workers,
                    "query_chunk_size": query_chunk_size,
                    "identical_query_replicates_per_task": replicate_count,
                    "vectorized_query_exercised": query_chunk_size > 1,
                    "maximum_absolute_difference_m": run_maximum,
                    "result_sha256": canonical_sha256(
                        {f"{city}:{source}": observed[(city, source)] for city, source in keys}
                    ),
                }
            )
    return {
        "runs": runs,
        "maximum_absolute_difference_m": maximum_difference,
        "tolerance_m": tolerance,
        "audit_semantics": (
            "Each fixed point-source task is repeated without spatial alteration; "
            "the repeated point vector is evaluated in chunks of the frozen sizes."
        ),
        "all_runs_invariant": True,
    }


def _diagnostic_table_bytes(frame: pd.DataFrame) -> bytes:
    columns = [
        "city_id",
        "label",
        "longitude",
        "latitude",
        "projected_crs",
        "source",
        "distance_m",
        "distance_km",
        "accepted_radius_km",
        "accepted_candidate_count",
        "bruteforce_distance_m",
        "geodesic_distance_m",
        "projected_minus_geodesic_m",
    ]
    return (
        frame.loc[:, columns]
        .to_csv(
            index=False,
            lineterminator="\n",
            float_format="%.12g",
        )
        .encode("utf-8")
    )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, indent=2).encode("utf-8")


def _atomic_bytes(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_bytes(content)
    temporary.replace(destination)


def _resolve_project_path(
    project_root: Path,
    path: str | Path,
) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root / candidate


def _recorded_output_path(project_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _build_pilot_payload(
    config_path: Path,
    amendment: dict[str, Any],
    base_path: Path,
    base: dict[str, Any],
    *,
    failure_output_path: Path,
    diagnostic_output_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    project_root = config_path.parents[2]
    from la_heat.multicity.water_distance_review import (
        _require_closed_locks,
        audit_water_distance_review,
    )

    plan = load_multicity_plan(project_root / "configs/multicity/experiment.toml")
    locks = _require_closed_locks(plan.raw)
    water_review_path = (
        project_root / "manifests/multicity/reviews/portable_water_distance/"
        "WATER_DISTANCE_REVIEW.json"
    )
    verified_review = audit_water_distance_review(
        project_root / str(base["comparison"]["census_config"]),
        output_path=water_review_path,
        write=False,
    )
    if verified_review.get("state") != "review_complete_source_not_frozen":
        raise GshhgGeometryPilotError("The prerequisite water review changed.")

    archive_path = project_root / str(base["source"]["archive_path"])
    archive_audit = _audit_archive(
        archive_path,
        base,
        project_root=project_root,
    )
    l1, l2 = _read_source_layers(archive_path, base)
    v1_failure = amendment["v1_failure"]
    l1_audit = _audit_source_layer(
        l1,
        label="GSHHG full-resolution L1",
        expected_level=1,
        expected_rows=int(v1_failure["l1_row_count"]),
        allow_invalid_count=int(v1_failure["l1_invalid_polygon_count"]),
    )
    l2_audit = _audit_source_layer(
        l2,
        label="GSHHG full-resolution L2",
        expected_level=2,
        expected_rows=int(v1_failure["l2_row_count"]),
        allow_invalid_count=0,
    )
    repaired_l1, repair_audit = repair_predeclared_l1_geometry(
        l1,
        amendment["invalid_geometry_repair"],
    )
    dateline_audit = audit_l1_dateline_segments(
        repaired_l1,
        tolerance=float(base["geometry_contract"]["dateline_tolerance_degrees"]),
        reject_jump_degrees=float(
            base["geometry_contract"]["reject_remaining_segment_longitude_jump_degrees"]
        ),
    )
    seeds = [
        LakeSeed(
            str(record["name"]),
            float(record["longitude"]),
            float(record["latitude"]),
        )
        for record in base["great_lake_seeds"]
    ]
    selected_lakes, lake_audit = select_connected_great_lakes(
        l2,
        seeds,
        amendment["great_lakes_connected_water_contract"],
    )
    if lake_audit["distinct_source_polygon_count"] == 5:
        raise GshhgGeometryPilotError(
            "V1 failure evidence unexpectedly identifies five source polygons."
        )

    v1_failure_payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "gshhg-geometry-pilot-v1-structural-failure-audit",
        "state": V1_FAILURE_STATE,
        "pilot_id": base["pilot"]["pilot_id"],
        "preregistration_config": {
            "path": base_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(base_path),
            "git_commit": amendment["amendment"]["base_preregistration_commit"],
        },
        "source_archive": archive_audit,
        "source_layers": {
            "l1": l1_audit,
            "l2": l2_audit,
        },
        "failed_gates": [
            {
                "gate": "all_l1_polygons_valid_without_predeclared_repair",
                "expected": 0,
                "observed": l1_audit["invalid_geometry_count"],
            },
            {
                "gate": "five_seeds_identify_five_distinct_l2_polygons",
                "expected": 5,
                "observed": lake_audit["distinct_source_polygon_count"],
                "seed_mapping": lake_audit["seed_mapping"],
            },
        ],
        "diagnostic_distance_values_computed": False,
        "census_comparison_distances_computed": False,
        "predictor_values_computed": False,
        "target_or_qa_values_read": False,
        "model_or_prediction_work_performed": False,
    }
    v1_failure_payload["commit_sha256"] = canonical_sha256(v1_failure_payload)

    diagnostic_points = list(base["diagnostic_points"])
    settings = base["distance_audit"]
    linework_variants, linework_audit = _regional_linework_variants(
        repaired_l1,
        selected_lakes,
        diagnostic_points,
        settings,
        base["geometry_contract"],
    )
    linework_audit["global_antimeridian_source_audit"] = dateline_audit
    canonical_chunk_size = int(settings["canonical_line_chunk_vertex_count"])
    if canonical_chunk_size not in linework_variants:
        raise GshhgGeometryPilotError("The canonical GSHHG line chunk is missing.")
    canonical_gshhg = linework_variants[canonical_chunk_size]

    census_path = project_root / str(verified_review["census_benchmark"]["path"])
    validate_source_file(CENSUS_2019_COASTLINE, census_path)
    census_uri = f"zip://{census_path.resolve()}!{verified_review['census_benchmark']['layer']}"
    try:
        census = gpd.read_file(census_uri)
    except Exception as exc:
        raise GshhgGeometryPilotError("Cannot read the Census comparison geometry.") from exc
    if (
        not census["MTFCC"].astype(str).eq(str(base["comparison"]["census_filter_value"])).all()
        or len(census) != 4248
    ):
        raise GshhgGeometryPilotError("The Census comparison geometry changed.")
    census = census.to_crs(WGS84).reset_index(drop=True)
    census["source_id"] = [f"census_l4150_archive_row_{index:04d}" for index in range(len(census))]
    census["component_id"] = census["source_id"]
    census["shoreline_class"] = "census_us_l4150"
    census["source_name"] = census["NAME"]

    diagnostic_records: list[dict[str, object]] = []
    canonical_candidates: dict[
        tuple[str, str],
        tuple[Mapping[str, Any], gpd.GeoDataFrame, float],
    ] = {}
    for point in diagnostic_points:
        for source_name, linework in (
            (GSHHG_SOURCE_NAME, canonical_gshhg),
            (CENSUS_SOURCE_NAME, census),
        ):
            record, candidates = _distance_record(
                point,
                linework,
                source_name,
                settings,
                include_geodesic=True,
            )
            diagnostic_records.append(record)
            key = (str(point["city_id"]), source_name)
            canonical_candidates[key] = (
                point,
                candidates,
                float(record["distance_m"]),
            )

    canonical_by_city = {
        (str(record["city_id"]), str(record["source"])): record for record in diagnostic_records
    }
    chunk_runs: list[dict[str, object]] = []
    maximum_chunk_difference = 0.0
    for chunk_size, linework in sorted(linework_variants.items()):
        if chunk_size == canonical_chunk_size:
            continue
        run_differences: dict[str, float] = {}
        for point in diagnostic_points:
            record, _ = _distance_record(
                point,
                linework,
                GSHHG_SOURCE_NAME,
                settings,
                include_geodesic=False,
            )
            canonical = canonical_by_city[
                (
                    str(point["city_id"]),
                    GSHHG_SOURCE_NAME,
                )
            ]
            difference = abs(float(record["distance_m"]) - float(canonical["distance_m"]))
            if difference > float(settings["invariance_absolute_tolerance_m"]) or float(
                record["accepted_radius_km"]
            ) != float(canonical["accepted_radius_km"]):
                raise GshhgGeometryPilotError("Diagnostic distance changed with line chunk size.")
            run_differences[str(point["city_id"])] = difference
        run_maximum = max(run_differences.values(), default=0.0)
        maximum_chunk_difference = max(maximum_chunk_difference, run_maximum)
        chunk_runs.append(
            {
                "line_chunk_vertex_count": chunk_size,
                "absolute_differences_m": run_differences,
                "maximum_absolute_difference_m": run_maximum,
            }
        )
    chunk_audit = {
        "canonical_line_chunk_vertex_count": canonical_chunk_size,
        "runs": chunk_runs,
        "maximum_absolute_difference_m": maximum_chunk_difference,
        "tolerance_m": float(settings["invariance_absolute_tolerance_m"]),
        "all_runs_invariant": True,
    }
    worker_audit = _thread_and_query_chunk_audit(
        canonical_candidates,
        settings,
    )

    comparisons: list[dict[str, object]] = []
    for point in diagnostic_points:
        city_id = str(point["city_id"])
        gshhg_record = canonical_by_city[(city_id, GSHHG_SOURCE_NAME)]
        census_record = canonical_by_city[(city_id, CENSUS_SOURCE_NAME)]
        signed_m = float(gshhg_record["distance_m"]) - float(census_record["distance_m"])
        comparisons.append(
            {
                "city_id": city_id,
                "gshhg_distance_km": float(gshhg_record["distance_km"]),
                "census_distance_km": float(census_record["distance_km"]),
                "gshhg_minus_census_km": signed_m / 1000.0,
                "absolute_difference_km": abs(signed_m) / 1000.0,
            }
        )
    phoenix = [record for record in comparisons if record["city_id"] == "phoenix_az"]
    if len(phoenix) != 1:
        raise GshhgGeometryPilotError("Phoenix comparison is not singular.")

    diagnostic_frame = pd.DataFrame(diagnostic_records)
    code_sha256, code_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_payload["packages"]["pyproj"] = importlib.metadata.version("pyproj")
    code_payload["relative_paths"] = list(CODE_PATHS)
    code_payload["sha256"] = canonical_sha256(code_payload)
    if code_sha256 == code_payload["sha256"]:
        raise GshhgGeometryPilotError(
            "Pilot-local pyproj binding was not added to the runtime fingerprint."
        )

    table_bytes = _diagnostic_table_bytes(diagnostic_frame)
    failure_bytes = _json_bytes(v1_failure_payload)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "pilot_id": amendment["amendment"]["pilot_id"],
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "prerequisite_water_review": {
            "path": water_review_path.relative_to(project_root).as_posix(),
            "file_sha256": sha256_file(water_review_path),
            "commit_sha256": verified_review["commit_sha256"],
            "state": verified_review["state"],
        },
        "config": {
            "base_path": base_path.relative_to(project_root).as_posix(),
            "base_sha256": sha256_file(base_path),
            "amendment_path": config_path.relative_to(project_root).as_posix(),
            "amendment_sha256": sha256_file(config_path),
            "base_preregistration_commit": amendment["amendment"]["base_preregistration_commit"],
            "all_numerical_gates_unchanged_after_source_read": True,
        },
        "source_archive": archive_audit,
        "source_layers": {
            "l1_original": l1_audit,
            "l2_original": l2_audit,
            "l1_repair": repair_audit,
            "great_lakes_identity": lake_audit,
            "linework": linework_audit,
        },
        "v1_failure": {
            "path": _recorded_output_path(project_root, failure_output_path),
            "file_sha256": hashlib.sha256(failure_bytes).hexdigest(),
            "commit_sha256": v1_failure_payload["commit_sha256"],
            "state": v1_failure_payload["state"],
            "distance_values_computed": False,
        },
        "diagnostic_distances": diagnostic_records,
        "source_comparisons": comparisons,
        "phoenix_comparison": phoenix[0],
        "numerical_gates": {
            "strtree_bruteforce_all_passed": True,
            "radius_expansion_all_passed": True,
            "source_order_all_passed": True,
            "projected_geodesic_all_passed": True,
            "line_chunk_invariance": chunk_audit,
            "worker_and_query_chunk_invariance": worker_audit,
        },
        "diagnostic_table": {
            "path": _recorded_output_path(project_root, diagnostic_output_path),
            "bytes": len(table_bytes),
            "sha256": hashlib.sha256(table_bytes).hexdigest(),
            "rows": len(diagnostic_frame),
            "semantic_sha256": canonical_sha256(
                diagnostic_frame[
                    [
                        "city_id",
                        "source",
                        "distance_m",
                        "accepted_radius_km",
                    ]
                ]
                .sort_values(["city_id", "source"], kind="stable")
                .to_dict("records")
            ),
        },
        "locks": locks,
        "access_contract": {
            "operator_recorded_canonical_source_archive_downloads": 1,
            "operator_recorded_failed_concurrent_download_artifacts_preserved": 1,
            "operator_download_history_mechanically_authenticated": False,
            "audit_program_network_requests": 0,
            "public_source_geometry_read": True,
            "fixed_target_blind_source_geometry_distances_computed": True,
            "immutable_v1_access_key_fixed_unlabeled_was_true": True,
            "eligible_land_grid_opened": False,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_lst_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
        },
        "decision": {
            "source_frozen": False,
            "algorithm_frozen": False,
            "gshhg_pilot_passed_all_v2_gates": True,
            "next_safe_stage": ("portable_water_distance_source_and_algorithm_freeze_decision"),
        },
        "code_runtime": code_payload,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload, v1_failure_payload, diagnostic_frame


def audit_gshhg_geometry_pilot(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_path: str | Path = DEFAULT_MANIFEST,
    failure_output_path: str | Path = DEFAULT_V1_FAILURE_MANIFEST,
    diagnostic_output_path: str | Path = DEFAULT_DIAGNOSTIC_TABLE,
    write: bool = True,
) -> dict[str, Any]:
    """Run or reauthenticate the source-only GSHHG geometry comparison."""

    resolved_config, amendment, base_path, base = _read_exact_configs(config_path)
    project_root = resolved_config.parents[2]
    destination = _resolve_project_path(project_root, output_path)
    failure_destination = _resolve_project_path(project_root, failure_output_path)
    table_destination = _resolve_project_path(project_root, diagnostic_output_path)
    payload, failure_payload, diagnostic_frame = _build_pilot_payload(
        resolved_config,
        amendment,
        base_path,
        base,
        failure_output_path=failure_destination,
        diagnostic_output_path=table_destination,
    )
    expected_failure_bytes = _json_bytes(failure_payload)
    expected_table_bytes = _diagnostic_table_bytes(diagnostic_frame)

    if write:
        _atomic_bytes(expected_failure_bytes, failure_destination)
        _atomic_bytes(expected_table_bytes, table_destination)
        _atomic_bytes(_json_bytes(payload), destination)
        return payload

    for path in (destination, failure_destination, table_destination):
        if not path.is_file():
            raise FileNotFoundError(path)
    if failure_destination.read_bytes() != expected_failure_bytes:
        raise GshhgGeometryPilotError("The v1 failure artifact is stale or changed.")
    if table_destination.read_bytes() != expected_table_bytes:
        raise GshhgGeometryPilotError("The diagnostic distance table is stale or changed.")
    try:
        committed = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GshhgGeometryPilotError("Cannot read the GSHHG pilot manifest.") from exc
    if not isinstance(committed, dict):
        raise GshhgGeometryPilotError("The GSHHG pilot manifest must be an object.")
    recorded = committed.get("commit_sha256")
    body = {key: value for key, value in committed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or recorded != canonical_sha256(body):
        raise GshhgGeometryPilotError("The GSHHG pilot internal commit is invalid.")
    if committed != payload:
        raise GshhgGeometryPilotError("The GSHHG pilot manifest is stale or changed.")
    return committed
