"""Pinned, fail-closed retrieval of the project's raw static geospatial sources.

The public NASA LP DAAC SRTM archive requires Earthdata authentication.  For
the two Los Angeles tiles this module therefore uses the anonymous
OpenTopography SRTM GL1 version-3 bulk mirror.  OpenTopography distributes the
same dataset as GeoTIFF rather than the original LP DAAC HGT ZIP container.

The Census Bureau's 2019 coastline ZIP is attempted at its official URL first.
Some networks are blocked by the Census Cloudflare policy, so a fixed Internet
Archive memento of that exact official URL is the sole fallback.  Both routes
must produce the same pinned bytes and the same audited ZIP members.

Files are streamed to a unique partial path, checked by byte count, SHA-256,
and format-specific invariants, then atomically promoted.  A provenance JSON
file is written last and is the commit marker for the complete three-file
snapshot.  A missing marker means the directory must not be treated as a
committed input snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.windows import Window

from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

STATIC_SOURCES_SCHEMA_VERSION = 1
STATIC_SOURCES_ALGORITHM_VERSION = "static-source-download-v1"
STATIC_SOURCES_COMMIT_MARKER = "static_sources_provenance.json"
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

OPEN_TOPOGRAPHY_DATASET_URL = (
    "https://portal.opentopography.org/raster?opentopoID=OTSRTM.082015.4326.1"
)
OPEN_TOPOGRAPHY_DATASET_DOI = "https://doi.org/10.5069/G9445JDF"
OPEN_TOPOGRAPHY_SRTM_BASE_URL = (
    "https://opentopography.s3.sdsc.edu/raster/SRTM_GL1/SRTM_GL1_srtm"
)
CENSUS_2019_COASTLINE_CATALOG_URL = (
    "https://catalog.data.gov/dataset/"
    "tiger-line-shapefile-2019-nation-u-s-coastline-national-shapefile"
)
CENSUS_2019_COASTLINE_OFFICIAL_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2019/COASTLINE/"
    "tl_2019_us_coastline.zip"
)
CENSUS_2019_COASTLINE_WAYBACK_URL = (
    "https://web.archive.org/web/20201030234808id_/"
    + CENSUS_2019_COASTLINE_OFFICIAL_URL
)

SRTM_N33_SOURCE_ID = "srtm_n33w119"
SRTM_N34_SOURCE_ID = "srtm_n34w119"
CENSUS_COASTLINE_SOURCE_ID = "census_2019_coastline"
SRTM_LAT34_SEAM_PAIR = (SRTM_N33_SOURCE_ID, SRTM_N34_SOURCE_ID)


class StaticSourceAuditError(ValueError):
    """Raised when a static-source download or cached file fails closed."""


@dataclass(frozen=True, slots=True)
class RasterExpectation:
    """Audited raster schema and fixed grid for one source tile."""

    shape: tuple[int, int]
    crs: str
    dtype: str
    nodata: float
    transform: tuple[float, float, float, float, float, float]
    area_or_point: str

    def __post_init__(self) -> None:
        if len(self.shape) != 2 or min(self.shape) <= 0:
            raise ValueError("Raster shape must contain two positive dimensions.")
        if len(self.transform) != 6 or not all(
            math.isfinite(value) for value in self.transform
        ):
            raise ValueError("Raster transform must contain six finite coefficients.")
        if not math.isfinite(self.nodata):
            raise ValueError("Raster nodata must be finite.")


@dataclass(frozen=True, slots=True)
class ZipMemberExpectation:
    """Expected uncompressed member size and CRC-32 from a pinned ZIP."""

    name: str
    bytes: int
    crc32: int

    def __post_init__(self) -> None:
        if Path(self.name).is_absolute() or Path(self.name).as_posix() != self.name:
            raise ValueError(f"ZIP member name is not canonical: {self.name!r}")
        if self.bytes < 0 or not 0 <= self.crc32 <= 0xFFFFFFFF:
            raise ValueError(f"Invalid ZIP member expectation for {self.name!r}.")


@dataclass(frozen=True, slots=True)
class StaticSourceSpec:
    """Immutable retrieval, integrity, schema, and provenance specification."""

    source_id: str
    filename: str
    kind: Literal["raster", "zip"]
    urls: tuple[str, ...]
    expected_sha256: str
    expected_bytes: int
    publisher: str
    dataset: str
    version: str
    data_vintage: str
    catalog_url: str
    license_note: str
    provenance_caveats: tuple[str, ...]
    raster: RasterExpectation | None = None
    zip_members: tuple[ZipMemberExpectation, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.replace("_", "").isalnum():
            raise ValueError(f"Invalid source_id: {self.source_id!r}")
        if Path(self.filename).name != self.filename or Path(self.filename).is_absolute():
            raise ValueError(f"Static source filename is unsafe: {self.filename!r}")
        if not self.urls or any(not url.startswith("https://") for url in self.urls):
            raise ValueError(f"Static source {self.source_id!r} needs HTTPS URL(s).")
        if len(self.expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_sha256
        ):
            raise ValueError(f"Invalid SHA-256 for {self.source_id!r}.")
        if self.expected_bytes <= 0:
            raise ValueError(f"Expected bytes must be positive for {self.source_id!r}.")
        if self.kind == "raster":
            if self.raster is None or self.zip_members:
                raise ValueError("Raster specs need raster metadata and no ZIP members.")
        elif self.kind == "zip":
            if self.raster is not None or not self.zip_members:
                raise ValueError("ZIP specs need member expectations and no raster metadata.")
            names = [member.name for member in self.zip_members]
            if len(names) != len(set(names)):
                raise ValueError("ZIP member expectations must have unique names.")
        else:  # pragma: no cover - Literal prevents this for typed callers.
            raise ValueError(f"Unsupported source kind: {self.kind!r}")


_ONE_ARC_SECOND = 1.0 / 3600.0
_SRTM_WEST_EDGE = -119.0 - _ONE_ARC_SECOND / 2.0

_SRTM_COMMON_PROVENANCE = {
    "publisher": "NASA/USGS; distributed by OpenTopography (UC San Diego)",
    "dataset": "NASA Shuttle Radar Topography Mission Global 1 arc second V003",
    "version": "SRTM GL1 v3; OpenTopoID OTSRTM.082015.4326.1",
    "data_vintage": "SRTM acquisition 2000-02-11 through 2000-02-21",
    "catalog_url": OPEN_TOPOGRAPHY_DATASET_URL,
    "license_note": "Cite NASA LP DAAC and OpenTopography; see dataset landing page.",
    "provenance_caveats": (
        "OpenTopography is an academic third-party distribution endpoint.",
        "The mirror is a GeoTIFF conversion, not the byte-identical LP DAAC HGT ZIP.",
        "The object timestamp reflects later storage migration, not DEM observation date.",
        "Use SRTM_GL1 (EGM96 orthometric heights), not SRTM_GL1_Ellip.",
    ),
}

SRTM_N33W119 = StaticSourceSpec(
    source_id=SRTM_N33_SOURCE_ID,
    filename="N33W119.tif",
    kind="raster",
    urls=(f"{OPEN_TOPOGRAPHY_SRTM_BASE_URL}/N33W119.tif",),
    expected_sha256="723e181239b96318165898261885ee3fb02b296e80399a151ad479decb599435",
    expected_bytes=1_639_890,
    raster=RasterExpectation(
        shape=(3601, 3601),
        crs="EPSG:4326",
        dtype="int16",
        nodata=-32768.0,
        transform=(
            _ONE_ARC_SECOND,
            0.0,
            _SRTM_WEST_EDGE,
            0.0,
            -_ONE_ARC_SECOND,
            34.0 + _ONE_ARC_SECOND / 2.0,
        ),
        area_or_point="Point",
    ),
    **_SRTM_COMMON_PROVENANCE,
)

SRTM_N34W119 = StaticSourceSpec(
    source_id=SRTM_N34_SOURCE_ID,
    filename="N34W119.tif",
    kind="raster",
    urls=(f"{OPEN_TOPOGRAPHY_SRTM_BASE_URL}/N34W119.tif",),
    expected_sha256="b91b076ff94bd832b309a5cd8514b759e78052f5db0ea65c2122e4a68799ed00",
    expected_bytes=15_894_914,
    raster=RasterExpectation(
        shape=(3601, 3601),
        crs="EPSG:4326",
        dtype="int16",
        nodata=-32768.0,
        transform=(
            _ONE_ARC_SECOND,
            0.0,
            _SRTM_WEST_EDGE,
            0.0,
            -_ONE_ARC_SECOND,
            35.0 + _ONE_ARC_SECOND / 2.0,
        ),
        area_or_point="Point",
    ),
    **_SRTM_COMMON_PROVENANCE,
)

_CENSUS_COASTLINE_MEMBERS = (
    ZipMemberExpectation("tl_2019_us_coastline.cpg", 5, 0x0E813C50),
    ZipMemberExpectation("tl_2019_us_coastline.dbf", 450_386, 0x9F8BC1BA),
    ZipMemberExpectation("tl_2019_us_coastline.prj", 165, 0xAA147E60),
    ZipMemberExpectation("tl_2019_us_coastline.shp", 25_198_068, 0x3D5DA880),
    ZipMemberExpectation("tl_2019_us_coastline.shp.ea.iso.xml", 4_661, 0x4AB07030),
    ZipMemberExpectation("tl_2019_us_coastline.shp.iso.xml", 30_373, 0x6386AAA7),
    ZipMemberExpectation("tl_2019_us_coastline.shx", 34_084, 0xC3053098),
)

CENSUS_2019_COASTLINE = StaticSourceSpec(
    source_id=CENSUS_COASTLINE_SOURCE_ID,
    filename="tl_2019_us_coastline.zip",
    kind="zip",
    urls=(
        CENSUS_2019_COASTLINE_OFFICIAL_URL,
        CENSUS_2019_COASTLINE_WAYBACK_URL,
    ),
    expected_sha256="10c7e252a96a46552bf6045cc46f0605f645feeab70be545fab1bac869723494",
    expected_bytes=16_631_608,
    publisher="U.S. Census Bureau",
    dataset="TIGER/Line Shapefile, 2019, U.S. Coastline",
    version="TIGER/Line 2019 national coastline (MTFCC L4150)",
    data_vintage="2019 release; origin Last-Modified 2019-08-09",
    catalog_url=CENSUS_2019_COASTLINE_CATALOG_URL,
    license_note="CC0-1.0/public-domain dedication in the Data.gov catalog.",
    provenance_caveats=(
        "The official Census URL is always attempted before the fixed memento.",
        "Internet Archive is a third-party transport mirror of the official URL.",
        "Census calls this a statistical-display coastline, not a legal shoreline.",
        "Use derived distance only as a statistical coastline proxy.",
    ),
    zip_members=_CENSUS_COASTLINE_MEMBERS,
)

STATIC_SOURCE_SPECS = (SRTM_N33W119, SRTM_N34W119, CENSUS_2019_COASTLINE)


class _ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...

    def iter_content(self, *, chunk_size: int) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class StaticSourceHttpClient(Protocol):
    """Minimal injectable client used by the offline unit tests."""

    def get(
        self,
        url: str,
        *,
        stream: bool,
        timeout: tuple[float, float] | float,
    ) -> _ResponseLike: ...


def _file_integrity(path: Path) -> dict[str, int | str]:
    if not path.is_file():
        raise StaticSourceAuditError(f"Static source is missing or not a file: {path}")
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _audit_integrity(path: Path, spec: StaticSourceSpec) -> dict[str, int | str]:
    record = _file_integrity(path)
    if record["bytes"] != spec.expected_bytes:
        raise StaticSourceAuditError(
            f"Byte-count mismatch for {spec.source_id}: "
            f"{record['bytes']} != {spec.expected_bytes}."
        )
    if record["sha256"] != spec.expected_sha256:
        raise StaticSourceAuditError(
            f"SHA-256 mismatch for {spec.source_id}: "
            f"{record['sha256']} != {spec.expected_sha256}."
        )
    return record


def _close_enough(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)


def validate_raster(path: Path, expectation: RasterExpectation) -> dict[str, object]:
    """Validate one pinned raster without relying on its filename."""

    try:
        with rasterio.open(path) as dataset:
            actual_shape = (dataset.height, dataset.width)
            if actual_shape != expectation.shape:
                raise StaticSourceAuditError(
                    f"Raster shape mismatch for {path.name}: "
                    f"{actual_shape} != {expectation.shape}."
                )
            if dataset.count != 1:
                raise StaticSourceAuditError(
                    f"Raster {path.name} must have exactly one band; got {dataset.count}."
                )
            if dataset.dtypes != (expectation.dtype,):
                raise StaticSourceAuditError(
                    f"Raster dtype mismatch for {path.name}: "
                    f"{dataset.dtypes} != {(expectation.dtype,)}."
                )
            expected_crs = CRS.from_user_input(expectation.crs)
            if dataset.crs != expected_crs:
                raise StaticSourceAuditError(
                    f"Raster CRS mismatch for {path.name}: "
                    f"{dataset.crs} != {expected_crs}."
                )
            if dataset.nodata is None or not _close_enough(
                float(dataset.nodata), expectation.nodata
            ):
                raise StaticSourceAuditError(
                    f"Raster nodata mismatch for {path.name}: "
                    f"{dataset.nodata} != {expectation.nodata}."
                )
            actual_transform = tuple(float(value) for value in tuple(dataset.transform)[:6])
            if not all(
                _close_enough(actual, expected)
                for actual, expected in zip(
                    actual_transform, expectation.transform, strict=True
                )
            ):
                raise StaticSourceAuditError(
                    f"Raster grid transform mismatch for {path.name}: "
                    f"{actual_transform} != {expectation.transform}."
                )
            area_or_point = dataset.tags().get("AREA_OR_POINT")
            if area_or_point != expectation.area_or_point:
                raise StaticSourceAuditError(
                    f"Raster AREA_OR_POINT mismatch for {path.name}: "
                    f"{area_or_point!r} != {expectation.area_or_point!r}."
                )
            return {
                "kind": "raster",
                "shape": [dataset.height, dataset.width],
                "count": dataset.count,
                "dtype": dataset.dtypes[0],
                "crs": dataset.crs.to_string(),
                "nodata": float(dataset.nodata),
                "transform": list(actual_transform),
                "area_or_point": area_or_point,
                "bounds": [float(value) for value in dataset.bounds],
            }
    except StaticSourceAuditError:
        raise
    except (OSError, rasterio.errors.RasterioError) as error:
        raise StaticSourceAuditError(f"Cannot read raster {path}.") from error


def validate_zip(
    path: Path, expectations: Sequence[ZipMemberExpectation]
) -> dict[str, object]:
    """Validate exact member names, sizes, CRCs, encryption state, and payloads."""

    expected = {member.name: member for member in expectations}
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise StaticSourceAuditError(f"ZIP {path.name} has duplicate member names.")
            if set(names) != set(expected):
                missing = sorted(set(expected).difference(names))
                unexpected = sorted(set(names).difference(expected))
                raise StaticSourceAuditError(
                    f"ZIP member mismatch for {path.name}; "
                    f"missing={missing}, unexpected={unexpected}."
                )
            for member in members:
                expected_member = expected[member.filename]
                if member.flag_bits & 0x1:
                    raise StaticSourceAuditError(
                        f"ZIP member {member.filename!r} must not be encrypted."
                    )
                if member.file_size != expected_member.bytes:
                    raise StaticSourceAuditError(
                        f"ZIP member size mismatch for {member.filename!r}: "
                        f"{member.file_size} != {expected_member.bytes}."
                    )
                if member.CRC != expected_member.crc32:
                    raise StaticSourceAuditError(
                        f"ZIP member CRC mismatch for {member.filename!r}: "
                        f"{member.CRC:08x} != {expected_member.crc32:08x}."
                    )
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise StaticSourceAuditError(
                    f"ZIP payload CRC failed for member {corrupt_member!r}."
                )
            return {
                "kind": "zip",
                "member_count": len(members),
                "members": [
                    {
                        "name": member.filename,
                        "bytes": member.file_size,
                        "crc32": f"{member.CRC:08x}",
                    }
                    for member in sorted(members, key=lambda item: item.filename)
                ],
            }
    except StaticSourceAuditError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise StaticSourceAuditError(f"Cannot read ZIP archive {path}.") from error


def validate_source_file(spec: StaticSourceSpec, path: Path) -> dict[str, object]:
    """Run pinned byte-level and format-level validation for one source."""

    integrity = _audit_integrity(path, spec)
    if spec.kind == "raster":
        if spec.raster is None:  # pragma: no cover - guarded by dataclass validation.
            raise AssertionError("Raster expectation is missing.")
        content = validate_raster(path, spec.raster)
    else:
        content = validate_zip(path, spec.zip_members)
    return {"integrity": integrity, "content": content}


def validate_raster_seam(
    lower_path: Path,
    upper_path: Path,
) -> dict[str, object]:
    """Require the N33 north row to equal the N34 south row sample-for-sample."""

    try:
        with rasterio.open(lower_path) as lower, rasterio.open(upper_path) as upper:
            if lower.width != upper.width:
                raise StaticSourceAuditError(
                    "Adjacent SRTM tile widths differ; their seam cannot be audited."
                )
            lower_edge = lower.read(
                [1],
                window=Window(col_off=0, row_off=0, width=lower.width, height=1),
            )[0, 0]
            upper_edge = upper.read(
                [1],
                window=Window(
                    col_off=0,
                    row_off=upper.height - 1,
                    width=upper.width,
                    height=1,
                ),
            )[0, 0]
            if not np.array_equal(lower_edge, upper_edge):
                difference_count = int(np.count_nonzero(lower_edge != upper_edge))
                raise StaticSourceAuditError(
                    "Adjacent SRTM tiles disagree on their shared latitude row; "
                    f"different_samples={difference_count}."
                )
            lower_y = float(lower.xy(0, 0)[1])
            upper_y = float(upper.xy(upper.height - 1, 0)[1])
            if not _close_enough(lower_y, upper_y):
                raise StaticSourceAuditError(
                    f"Adjacent SRTM seam coordinates differ: {lower_y} != {upper_y}."
                )
            canonical_edge = np.asarray(lower_edge, dtype=">i2").tobytes()
            return {
                "check": "adjacent_point_grid_row_identity",
                "lower_north_row": 0,
                "upper_south_row": upper.height - 1,
                "shared_latitude": lower_y,
                "sample_count": lower.width,
                "different_samples": 0,
                "edge_sha256": hashlib.sha256(canonical_edge).hexdigest(),
            }
    except StaticSourceAuditError:
        raise
    except (OSError, rasterio.errors.RasterioError) as error:
        raise StaticSourceAuditError("Cannot audit adjacent SRTM raster seam.") from error


def _selected_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    wanted = {
        "content-type",
        "etag",
        "last-modified",
        "memento-datetime",
        "x-archive-orig-last-modified",
    }
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if str(key).lower() in wanted
    }


def _stream_response_to_path(
    response: _ResponseLike,
    path: Path,
) -> dict[str, int | str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise StaticSourceAuditError("HTTP response yielded a non-bytes chunk.")
            handle.write(chunk)
            digest.update(chunk)
            byte_count += len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    return {"bytes": byte_count, "sha256": digest.hexdigest()}


def _download_candidate(
    *,
    spec: StaticSourceSpec,
    url: str,
    destination: Path,
    client: StaticSourceHttpClient,
    timeout: tuple[float, float] | float,
) -> tuple[dict[str, object], dict[str, str]]:
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.partial"
    )
    response: _ResponseLike | None = None
    try:
        response = client.get(url, stream=True, timeout=timeout)
        response.raise_for_status()
        streamed = _stream_response_to_path(response, temporary)
        if streamed["bytes"] != spec.expected_bytes:
            raise StaticSourceAuditError(
                f"Downloaded byte-count mismatch for {spec.source_id}: "
                f"{streamed['bytes']} != {spec.expected_bytes}."
            )
        if streamed["sha256"] != spec.expected_sha256:
            raise StaticSourceAuditError(
                f"Downloaded SHA-256 mismatch for {spec.source_id}: "
                f"{streamed['sha256']} != {spec.expected_sha256}."
            )
        validation = validate_source_file(spec, temporary)
        headers = _selected_response_headers(response.headers)
        os.replace(temporary, destination)
        return validation, headers
    finally:
        temporary.unlink(missing_ok=True)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _prior_source_record(
    previous_commit: Mapping[str, object] | None,
    spec: StaticSourceSpec,
) -> Mapping[str, object] | None:
    if previous_commit is None:
        return None
    sources = previous_commit.get("sources")
    if not isinstance(sources, Mapping):
        return None
    record = sources.get(spec.source_id)
    if not isinstance(record, Mapping):
        return None
    if record.get("sha256") != spec.expected_sha256:
        return None
    source_url = record.get("source_url_used")
    if source_url is not None and source_url not in spec.urls:
        return None
    return record


def _materialize_one(
    *,
    spec: StaticSourceSpec,
    output_directory: Path,
    client: StaticSourceHttpClient,
    timeout: tuple[float, float] | float,
    force: bool,
    previous_commit: Mapping[str, object] | None,
) -> dict[str, object]:
    destination = output_directory / spec.filename
    if destination.exists() and not destination.is_file():
        raise StaticSourceAuditError(
            f"Static source destination exists but is not a file: {destination}"
        )

    cached_error: str | None = None
    prior = _prior_source_record(previous_commit, spec)
    if destination.is_file() and not force:
        try:
            validation = validate_source_file(spec, destination)
        except StaticSourceAuditError as error:
            cached_error = str(error)
        else:
            source_url_used = None if prior is None else prior.get("source_url_used")
            return _source_record(
                spec=spec,
                path=destination,
                validation=validation,
                source_url_used=(
                    str(source_url_used) if isinstance(source_url_used, str) else None
                ),
                response_headers=(
                    dict(prior.get("response_headers", {})) if prior is not None else {}
                ),
                cache_hit=True,
                attempts=[],
                cached_error=None,
            )

    attempts: list[dict[str, object]] = []
    for url in spec.urls:
        try:
            validation, headers = _download_candidate(
                spec=spec,
                url=url,
                destination=destination,
                client=client,
                timeout=timeout,
            )
        except Exception as error:
            attempts.append(
                {
                    "url": url,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
            continue
        attempts.append({"url": url, "status": "downloaded"})
        return _source_record(
            spec=spec,
            path=destination,
            validation=validation,
            source_url_used=url,
            response_headers=headers,
            cache_hit=False,
            attempts=attempts,
            cached_error=cached_error,
        )

    attempted_urls = [attempt["url"] for attempt in attempts]
    raise StaticSourceAuditError(
        f"Every candidate URL failed for {spec.source_id}; attempted={attempted_urls}."
    )


def _source_record(
    *,
    spec: StaticSourceSpec,
    path: Path,
    validation: Mapping[str, object],
    source_url_used: str | None,
    response_headers: Mapping[str, object],
    cache_hit: bool,
    attempts: Sequence[Mapping[str, object]],
    cached_error: str | None,
) -> dict[str, object]:
    return {
        "source_id": spec.source_id,
        "filename": spec.filename,
        "path": str(path.resolve()),
        "kind": spec.kind,
        "sha256": spec.expected_sha256,
        "bytes": spec.expected_bytes,
        "source_url_used": source_url_used,
        "candidate_urls": list(spec.urls),
        "cache_hit": cache_hit,
        "attempts": [dict(attempt) for attempt in attempts],
        "cached_file_rejection": cached_error,
        "response_headers": dict(response_headers),
        "publisher": spec.publisher,
        "dataset": spec.dataset,
        "version": spec.version,
        "data_vintage": spec.data_vintage,
        "catalog_url": spec.catalog_url,
        "license_note": spec.license_note,
        "provenance_caveats": list(spec.provenance_caveats),
        "validation": dict(validation),
    }


def _validated_specs(specs: Sequence[StaticSourceSpec]) -> tuple[StaticSourceSpec, ...]:
    normalized = tuple(specs)
    if not normalized:
        raise ValueError("At least one static source specification is required.")
    source_ids = [spec.source_id for spec in normalized]
    filenames = [spec.filename for spec in normalized]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Static source IDs must be unique.")
    if len(filenames) != len(set(filenames)):
        raise ValueError("Static source filenames must be unique.")
    return normalized


def _load_valid_commit(path: Path) -> Mapping[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("state") != "complete":
        return None
    recorded = payload.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(payload) != recorded:
        return None
    payload["commit_sha256"] = recorded
    return payload


def download_static_sources(
    output_directory: str | Path,
    *,
    http_client: StaticSourceHttpClient | None = None,
    timeout: tuple[float, float] | float = (30.0, 240.0),
    force: bool = False,
    specs: Sequence[StaticSourceSpec] = STATIC_SOURCE_SPECS,
    seam_pair: tuple[str, str] | None = SRTM_LAT34_SEAM_PAIR,
) -> Path:
    """Materialize and atomically commit the complete static-source snapshot.

    The network dependency and the source specifications are injectable so all
    behavior can be tested without network access.  Production callers should
    rely on the pinned defaults.
    """

    selected_specs = _validated_specs(specs)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    marker_path = output / STATIC_SOURCES_COMMIT_MARKER
    previous_commit = _load_valid_commit(marker_path)

    # Withdraw the old commit before any cache audit or network operation.  A
    # crash or failed validation cannot leave a stale readable marker behind.
    marker_path.unlink(missing_ok=True)
    client: StaticSourceHttpClient = requests if http_client is None else http_client
    source_records: dict[str, dict[str, object]] = {}
    for spec in selected_specs:
        source_records[spec.source_id] = _materialize_one(
            spec=spec,
            output_directory=output,
            client=client,
            timeout=timeout,
            force=force,
            previous_commit=previous_commit,
        )

    cross_file_checks: dict[str, object] = {}
    if seam_pair is not None:
        by_id = {spec.source_id: spec for spec in selected_specs}
        missing = [source_id for source_id in seam_pair if source_id not in by_id]
        if missing:
            raise ValueError(f"SRTM seam pair references missing source IDs: {missing}")
        lower, upper = (by_id[source_id] for source_id in seam_pair)
        if lower.kind != "raster" or upper.kind != "raster":
            raise ValueError("Every seam-pair source must be a raster.")
        cross_file_checks["srtm_lat34_seam"] = validate_raster_seam(
            output / lower.filename,
            output / upper.filename,
        )

    payload: dict[str, object] = {
        "schema_version": STATIC_SOURCES_SCHEMA_VERSION,
        "algorithm_version": STATIC_SOURCES_ALGORITHM_VERSION,
        "state": "complete",
        "promoted_outputs_valid": True,
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "output_directory": str(output.resolve()),
        "source_count": len(selected_specs),
        "required_source_ids": [spec.source_id for spec in selected_specs],
        "sources": source_records,
        "cross_file_checks": cross_file_checks,
        "implementation_sha256": sha256_file(Path(__file__)),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    return marker_path
