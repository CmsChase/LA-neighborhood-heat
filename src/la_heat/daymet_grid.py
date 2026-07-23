"""Audited Daymet V4 R1 gridded discovery, access, and tract aggregation.

The official continental mosaics are very large annual NetCDF files.  This
module discovers the exact DOI 2129 granules through NASA CMR, asks the CMR
Service-Bridge for an authenticated OPeNDAP spatial-subset URL, and downloads
the returned NetCDF atomically.  Credentials are never persisted or included
in provenance records.

The aggregation helpers deliberately operate on fixed eligible-land weights.
Missing Daymet cells invalidate a tract-day value; weights are never
renormalized by date.  Shortwave energy is calculated at the grid-cell level
before spatial aggregation.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from getpass import getpass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse

import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.transform import array_bounds, rowcol, xy
from rasterio.warp import transform as transform_coordinates
from rasterio.warp import transform_bounds

from la_heat.weather_daymet import (
    DAYMET_VARIABLES,
    DEFAULT_DAYMET_VARIABLES,
    DERIVED_SRAD_ENERGY_COLUMN,
    build_lagged_features,
)

DAYMET_DOI = "10.3334/ORNLDAAC/2129"
DAYMET_DOI_URL = f"https://doi.org/{DAYMET_DOI}"
DAYMET_CMR_COLLECTION_ID = "C2532426483-ORNL_CLOUD"
DAYMET_CMR_GRANULES_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
DAYMET_CMR_SERVICE_BRIDGE_URL = (
    "https://cmr.earthdata.nasa.gov/service-bridge/ous/collection/"
    f"{DAYMET_CMR_COLLECTION_ID}"
)
DAYMET_REGION = "na"
DAYMET_DIRECT_DAP4_ROUTE = "direct_dap4_fixed_indices_v1"
DAYMET_FULL_GRID_SHAPE = (8_075, 7_814)
DAYMET_FULL_GRID_TRANSFORM = rasterio.Affine(
    1_000.0,
    0.0,
    -4_560_750.0,
    0.0,
    -1_000.0,
    4_984_500.0,
)
DAYMET_GRID_CRS = (
    "+proj=lcc +lat_1=25 +lat_2=60 +lat_0=42.5 +lon_0=-100 "
    "+x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
)
DAYMET_TOKEN_ENVIRONMENT_VARIABLES = (
    "EARTHDATA_TOKEN",
    "NASA_EARTHDATA_TOKEN",
    "EDL_TOKEN",
)
_GRANULE_PATTERN = re.compile(
    r"^Daymet_Daily_V4R1\.daymet_v4_daily_na_"
    r"(?P<variable>dayl|prcp|srad|swe|tmax|tmin|vp)_"
    r"(?P<year>\d{4})\.nc$"
)
_NETCDF_SIGNATURES = (b"CDF\x01", b"CDF\x02", b"CDF\x05", b"\x89HDF\r\n\x1a\n")


class DaymetGridAuditError(ValueError):
    """Raised when gridded Daymet inputs fail a scientific or provenance audit."""


class DaymetAuthenticationError(PermissionError):
    """Raised when authenticated Earthdata access is unavailable or rejected."""


@dataclass(frozen=True, slots=True)
class EarthdataBearerToken:
    """An in-memory Earthdata token whose value is suppressed from repr output."""

    value: str = field(repr=False)
    source_environment_variable: str

    def __post_init__(self) -> None:
        if not self.value or self.value != self.value.strip():
            raise ValueError("Earthdata bearer token must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class DaymetGranule:
    """CMR identity and official access links for one variable-year mosaic."""

    concept_id: str
    title: str
    variable: str
    year: int
    size_mb: float
    https_url: str
    opendap_url: str
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class DaymetNetCDFSpec:
    """Audited raster metadata for one Daymet variable-year subset."""

    path: Path
    variable: str
    year: int
    subdataset_uri: str
    shape: tuple[int, int]
    transform: rasterio.Affine
    crs_wkt: str
    dates: tuple[pd.Timestamp, ...]
    nodata: float
    scales: tuple[float, ...]
    offsets: tuple[float, ...]
    units: str


class _ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...

    def iter_content(self, chunk_size: int) -> Any: ...

    def close(self) -> None: ...


class _HttpClientLike(Protocol):
    def get(self, url: str, **kwargs: object) -> _ResponseLike: ...


def load_earthdata_bearer_token(
    *,
    environment: Mapping[str, str] | None = None,
    variable_names: Sequence[str] = DAYMET_TOKEN_ENVIRONMENT_VARIABLES,
) -> EarthdataBearerToken:
    """Load an Earthdata bearer token without logging or persisting its value."""

    source = os.environ if environment is None else environment
    present = [name for name in variable_names if source.get(name)]
    if not present:
        names = ", ".join(variable_names)
        raise DaymetAuthenticationError(
            "Daymet gridded access requires a NASA Earthdata bearer token. "
            f"Set one of these environment variables without committing it: {names}."
        )
    if len(present) > 1:
        raise DaymetAuthenticationError(
            "Multiple Earthdata token environment variables are set; retain exactly one "
            "to make credential provenance unambiguous."
        )
    name = present[0]
    return EarthdataBearerToken(
        value=str(source[name]), source_environment_variable=name
    )


def prompt_earthdata_bearer_token(
    *,
    environment: Mapping[str, str] | None = None,
    variable_names: Sequence[str] = DAYMET_TOKEN_ENVIRONMENT_VARIABLES,
    prompt_function: Callable[[str], str] | None = None,
) -> EarthdataBearerToken:
    """Read one bearer token from a hidden terminal prompt without persisting it."""

    source = os.environ if environment is None else environment
    present = [name for name in variable_names if name in source]
    if present:
        names = ", ".join(present)
        raise DaymetAuthenticationError(
            "Interactive token prompting cannot be combined with an existing "
            f"Earthdata token environment variable: {names}. Unset it first."
        )
    if prompt_function is None:
        if not sys.stdin.isatty():
            raise DaymetAuthenticationError(
                "Interactive Earthdata token input requires a real terminal."
            )
        prompt_function = getpass
    try:
        value = prompt_function("Earthdata bearer token: ")
    except EOFError as exc:
        raise DaymetAuthenticationError(
            "Interactive Earthdata token input requires a real terminal."
        ) from exc
    return EarthdataBearerToken(
        value=value,
        source_environment_variable="interactive_prompt",
    )


def _normalize_development_years(
    years: Sequence[int], *, final_test_year: int
) -> tuple[int, ...]:
    if isinstance(years, (str, bytes)) or not years:
        raise ValueError("Daymet years must be a non-empty sequence.")
    normalized: list[int] = []
    for value in years:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise TypeError("Every Daymet year must be an integer.")
        year = int(value)
        if year >= final_test_year:
            raise PermissionError(
                f"Daymet year {year} is at or beyond the locked final-test year "
                f"{final_test_year}."
            )
        normalized.append(year)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Daymet years must be unique.")
    return tuple(sorted(normalized))


def _normalize_variables(variables: Sequence[str]) -> tuple[str, ...]:
    if isinstance(variables, (str, bytes)) or not variables:
        raise ValueError("Daymet variables must be a non-empty sequence.")
    normalized = tuple(str(value).lower() for value in variables)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Daymet variables must be unique.")
    unknown = sorted(set(normalized).difference(DAYMET_VARIABLES))
    if unknown:
        raise ValueError(f"Unknown Daymet variables: {unknown}")
    return tuple(value for value in DAYMET_VARIABLES if value in normalized)


def _official_link(entry: Mapping[str, object], *, relation_suffix: str) -> str:
    links = entry.get("links")
    if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
        raise DaymetGridAuditError("CMR granule is missing its links array.")
    candidates: list[str] = []
    for raw in links:
        if not isinstance(raw, Mapping) or raw.get("inherited") is True:
            continue
        relation = str(raw.get("rel", ""))
        href = str(raw.get("href", ""))
        if relation.endswith(relation_suffix) and href:
            candidates.append(href)
    if len(candidates) != 1:
        raise DaymetGridAuditError(
            f"CMR granule must have exactly one {relation_suffix!r} link; "
            f"found {len(candidates)}."
        )
    return candidates[0]


def _audit_daymet_url(url: str, *, protected_download: bool) -> None:
    parsed = urlparse(url)
    expected_host = (
        "data.ornldaac.earthdata.nasa.gov"
        if protected_download
        else "opendap.earthdata.nasa.gov"
    )
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        raise DaymetGridAuditError(f"Unexpected Daymet access host in {url!r}.")
    if protected_download and "/Daymet_Daily_V4R1/data/" not in parsed.path:
        raise DaymetGridAuditError("CMR HTTPS link is not a Daymet V4 R1 data path.")
    if "1840" in url:
        raise DaymetGridAuditError("Legacy DOI 1840 access paths are prohibited.")


def _reject_credential_bearing_url(
    url: str, *, credential: EarthdataBearerToken
) -> None:
    """Refuse URLs that would leak an Earthdata credential into provenance."""

    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if credential.value in parsed.path or any(
        credential.value in value for _, value in query_pairs
    ):
        raise DaymetAuthenticationError(
            "Daymet response embedded the Earthdata credential in its URL; refusing "
            "to persist or download that URL."
        )
    sensitive_query_names = {
        "access_token",
        "authorization",
        "auth",
        "echo-token",
        "token",
    }
    query_names = {name.casefold() for name, _ in query_pairs}
    if query_names & sensitive_query_names:
        raise DaymetAuthenticationError(
            "Daymet response URL contains a credential-like query parameter; "
            "refusing to persist it."
        )


def discover_daymet_v4r1_granules(
    *,
    years: Sequence[int],
    variables: Sequence[str] = DEFAULT_DAYMET_VARIABLES,
    final_test_year: int = 2025,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 120.0),
    endpoint: str = DAYMET_CMR_GRANULES_URL,
) -> list[DaymetGranule]:
    """Discover and audit exact DOI 2129 continental variable-year granules."""

    normalized_years = _normalize_development_years(
        years, final_test_year=final_test_year
    )
    normalized_variables = _normalize_variables(variables)
    client: _HttpClientLike = requests if http_client is None else http_client
    response = client.get(
        endpoint,
        params={
            "collection_concept_id": DAYMET_CMR_COLLECTION_ID,
            "temporal": (
                f"{normalized_years[0]}-01-01T00:00:00Z,"
                f"{normalized_years[-1]}-12-31T23:59:59Z"
            ),
            "page_size": 2000,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise DaymetGridAuditError("CMR granule response is not valid JSON.") from error
    if not isinstance(payload, Mapping):
        raise DaymetGridAuditError("CMR granule response must be an object.")
    feed = payload.get("feed")
    entries = feed.get("entry") if isinstance(feed, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise DaymetGridAuditError("CMR granule response is missing feed.entry.")

    requested = {
        (variable, year)
        for variable in normalized_variables
        for year in normalized_years
    }
    discovered: dict[tuple[str, int], DaymetGranule] = {}
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise DaymetGridAuditError("CMR feed contains a non-object granule.")
        title = str(raw.get("title", ""))
        match = _GRANULE_PATTERN.fullmatch(title)
        if match is None:
            continue
        variable = match.group("variable")
        year = int(match.group("year"))
        key = (variable, year)
        if key not in requested:
            continue
        if key in discovered:
            raise DaymetGridAuditError(f"CMR returned duplicate Daymet granule {key}.")
        concept_id = str(raw.get("id", ""))
        if not re.fullmatch(r"G\d+-ORNL_CLOUD", concept_id):
            raise DaymetGridAuditError(
                f"CMR returned an invalid ORNL_CLOUD concept id for {title}."
            )
        https_url = _official_link(raw, relation_suffix="/data#")
        opendap_url = _official_link(raw, relation_suffix="/service#")
        _audit_daymet_url(https_url, protected_download=True)
        _audit_daymet_url(opendap_url, protected_download=False)
        try:
            size_mb = float(raw["granule_size"])
        except (KeyError, TypeError, ValueError) as error:
            raise DaymetGridAuditError(
                f"CMR granule {title} lacks a finite size."
            ) from error
        if not math.isfinite(size_mb) or size_mb <= 0:
            raise DaymetGridAuditError(f"CMR granule {title} has an invalid size.")
        discovered[key] = DaymetGranule(
            concept_id=concept_id,
            title=title,
            variable=variable,
            year=year,
            size_mb=size_mb,
            https_url=https_url,
            opendap_url=opendap_url,
            updated_at=None if raw.get("updated") is None else str(raw["updated"]),
        )

    missing = sorted(requested.difference(discovered))
    unexpected = sorted(set(discovered).difference(requested))
    if missing or unexpected:
        raise DaymetGridAuditError(
            "CMR Daymet V4 R1 granule set is incomplete; "
            f"missing={missing}, unexpected={unexpected}."
        )
    return [discovered[key] for key in sorted(discovered, key=lambda x: (x[1], x[0]))]


def _normalized_bbox(bbox_wgs84: Sequence[float]) -> tuple[float, float, float, float]:
    if isinstance(bbox_wgs84, (str, bytes)) or len(bbox_wgs84) != 4:
        raise ValueError("Daymet subset bbox must contain west, south, east, north.")
    west, south, east, north = (float(value) for value in bbox_wgs84)
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("Daymet subset bbox must be finite.")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Daymet subset bbox has invalid bounds.")
    return west, south, east, north


def _normalized_daymet_index_window(
    *,
    y_indices: Sequence[int],
    x_indices: Sequence[int],
) -> tuple[int, int, int, int]:
    if (
        isinstance(y_indices, (str, bytes))
        or isinstance(x_indices, (str, bytes))
        or len(y_indices) != 2
        or len(x_indices) != 2
    ):
        raise ValueError("Daymet x/y index windows must each contain start and stop.")
    raw = (*y_indices, *x_indices)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
        raise ValueError("Daymet x/y index windows must contain integers.")
    y_start, y_stop, x_start, x_stop = raw
    full_height, full_width = DAYMET_FULL_GRID_SHAPE
    if not (
        0 <= y_start <= y_stop < full_height
        and 0 <= x_start <= x_stop < full_width
    ):
        raise ValueError("Daymet x/y index window is outside the frozen full grid.")
    return y_start, y_stop, x_start, x_stop


def build_daymet_direct_subset_url(
    granule: DaymetGranule,
    *,
    y_indices: Sequence[int],
    x_indices: Sequence[int],
) -> str:
    """Build one audited DAP4 URL from a frozen granule and index window."""

    y_start, y_stop, x_start, x_stop = _normalized_daymet_index_window(
        y_indices=y_indices,
        x_indices=x_indices,
    )
    _audit_daymet_url(granule.opendap_url, protected_download=False)
    parsed = urlparse(granule.opendap_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise DaymetGridAuditError("Daymet OPeNDAP URL has an invalid port.") from error
    expected_path = (
        f"/collections/{DAYMET_CMR_COLLECTION_ID}/granules/{granule.title}"
    )
    expected_title = (
        "Daymet_Daily_V4R1.daymet_v4_daily_na_"
        f"{granule.variable}_{granule.year}.nc"
    )
    if (
        parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
        or granule.title != expected_title
    ):
        raise DaymetGridAuditError(
            "Frozen Daymet granule is not an exact official direct-DAP4 source."
        )
    y_slice = f"{y_start}:1:{y_stop}"
    x_slice = f"{x_start}:1:{x_stop}"
    constraint = ";".join(
        (
            f"/y[{y_slice}]",
            f"/x[{x_slice}]",
            "/time[0:1:364]",
            "/yearday[0:1:364]",
            "/time_bnds[0:1:364][0:1:1]",
            f"/lat[{y_slice}][{x_slice}]",
            f"/lon[{y_slice}][{x_slice}]",
            "/lambert_conformal_conic",
            f"/{granule.variable}[0:1:364][{y_slice}][{x_slice}]",
        )
    )
    subset_url = f"{granule.opendap_url}.dap.nc4?{urlencode({'dap4.ce': constraint})}"
    _audit_daymet_url(subset_url, protected_download=False)
    return subset_url


def validate_daymet_direct_subset_spec(
    spec: DaymetNetCDFSpec,
    *,
    y_indices: Sequence[int],
    x_indices: Sequence[int],
    bbox_wgs84: Sequence[float],
) -> DaymetNetCDFSpec:
    """Require a direct DAP4 subset to equal and cover its frozen LA window."""

    y_start, y_stop, x_start, x_stop = _normalized_daymet_index_window(
        y_indices=y_indices,
        x_indices=x_indices,
    )
    expected_shape = (y_stop - y_start + 1, x_stop - x_start + 1)
    expected_transform = DAYMET_FULL_GRID_TRANSFORM * rasterio.Affine.translation(
        x_start,
        y_start,
    )
    if spec.shape != expected_shape or not all(
        math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6)
        for actual, expected in zip(spec.transform, expected_transform, strict=True)
    ):
        raise DaymetGridAuditError(
            "Daymet direct subset shape/transform disagrees with its frozen indices."
        )
    west, south, east, north = _normalized_bbox(bbox_wgs84)
    projected = transform_bounds(
        "EPSG:4326",
        spec.crs_wkt,
        west,
        south,
        east,
        north,
        densify_pts=41,
    )
    subset = array_bounds(spec.shape[0], spec.shape[1], spec.transform)
    if not (
        subset[0] <= projected[0]
        and subset[1] <= projected[1]
        and subset[2] >= projected[2]
        and subset[3] >= projected[3]
    ):
        raise DaymetGridAuditError(
            "Daymet direct subset does not cover the frozen WGS84 study bbox."
        )
    return spec


def request_daymet_subset_url(
    granule: DaymetGranule,
    *,
    bbox_wgs84: Sequence[float],
    credential: EarthdataBearerToken,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 120.0),
    endpoint: str = DAYMET_CMR_SERVICE_BRIDGE_URL,
) -> str:
    """Request the unique matching NetCDF subset URL from CMR Service-Bridge."""

    west, south, east, north = _normalized_bbox(bbox_wgs84)
    client: _HttpClientLike = requests if http_client is None else http_client
    response = client.get(
        endpoint,
        params={
            "granules": granule.concept_id,
            "bounding-box": f"{west},{south},{east},{north}",
            "format": "nc",
            "dap-version": "4",
            "page-size": 1,
        },
        headers={
            "Echo-Token": credential.value,
            "Accept": "application/vnd.cmr-service-bridge.v3+json",
        },
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise DaymetAuthenticationError(
            "Earthdata rejected the Daymet Service-Bridge token; refresh the token "
            "and confirm ORNL DAAC application authorization."
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise DaymetGridAuditError("CMR Service-Bridge response is not JSON.") from error
    if not isinstance(payload, Mapping):
        raise DaymetGridAuditError("CMR Service-Bridge response must be an object.")
    warnings = payload.get("warnings")
    if warnings not in (None, [], ()):
        raise DaymetGridAuditError(
            "CMR Service-Bridge returned warnings, so spatial subsetting cannot be "
            "assumed to have been applied."
        )
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise DaymetGridAuditError(
            "CMR Service-Bridge returned no subset URL candidates; "
            f"hits={payload.get('hits')!r}, item_count=0."
        )
    if not all(isinstance(item, str) for item in items):
        raise DaymetGridAuditError(
            "CMR Service-Bridge subset candidates must all be text URLs."
        )
    expected = urlparse(granule.opendap_url)
    expected_subset_path = f"{expected.path}.dap.nc4"
    matches: list[str] = []
    official_host_count = 0
    exact_path_count = 0
    for item in items:
        _reject_credential_bearing_url(item, credential=credential)
        parsed = urlparse(item)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected.hostname
        ):
            continue
        official_host_count += 1
        if parsed.path != expected_subset_path:
            continue
        exact_path_count += 1
        # Service-Bridge owns the exact query-string syntax, so treat that query
        # as opaque instead of guessing a particular DAP4 expression spelling.
        # The immutable granule identity is enforced by the exact official host
        # and path above.  A non-empty query distinguishes the Service-Bridge
        # constrained response from the unconstrained path; the byte cap and
        # downloaded-NetCDF audits then fail closed on an ignored or malformed
        # subset request.  Those audits cover the requested variable, year,
        # 365-day axis, CRS, resolution, and shared grid.
        if not parsed.query:
            continue
        _audit_daymet_url(item, protected_download=False)
        matches.append(item)
    if len(matches) != 1:
        request_id = str(response.headers.get("Cmr-Request-Id", ""))
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            request_id,
        ):
            request_id = "unavailable"
        if len(items) == 1 and official_host_count == 1 and exact_path_count == 0:
            raise DaymetGridAuditError(
                "CMR Service-Bridge returned a different Daymet granule from the "
                "one frozen in the audited inventory."
            )
        raise DaymetGridAuditError(
            "CMR Service-Bridge did not return one unique constrained URL for the "
            f"frozen Daymet granule; hits={payload.get('hits')!r}, "
            f"item_count={len(items)}, official_host_count={official_host_count}, "
            f"exact_path_count={exact_path_count}, matching_count={len(matches)}, "
            f"request_id={request_id!r}."
        )
    return matches[0]


def _has_netcdf_signature(path: Path) -> bool:
    with path.open("rb") as handle:
        prefix = handle.read(8)
    return any(prefix.startswith(signature) for signature in _NETCDF_SIGNATURES)


def _authenticated_netcdf_download_once(
    url: str,
    destination: str | Path,
    *,
    credential: EarthdataBearerToken,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 900.0),
    maximum_bytes: int = 1_000_000_000,
) -> dict[str, object]:
    """Make one atomic authenticated NetCDF download attempt."""

    _audit_daymet_url(url, protected_download=False)
    _reject_credential_bearing_url(url, credential=credential)
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive.")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    client: _HttpClientLike = requests if http_client is None else http_client
    response = client.get(
        url,
        headers={"Authorization": f"Bearer {credential.value}"},
        stream=True,
        timeout=timeout,
    )
    try:
        if response.status_code in {401, 403}:
            raise DaymetAuthenticationError(
                "Earthdata rejected the authenticated Daymet subset download."
            )
        response.raise_for_status()
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None and int(declared_length) > maximum_bytes:
            raise DaymetGridAuditError(
                "Daymet subset response exceeds the configured byte limit; refusing a "
                "possible unsubsampled continental mosaic."
            )
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise DaymetGridAuditError(
                            "Daymet subset stream exceeded the configured byte limit."
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if written == 0 or not _has_netcdf_signature(temporary):
                raise DaymetGridAuditError(
                    "Authenticated Daymet response is not a NetCDF file (it may be a "
                    "login or error page)."
                )
            temporary.replace(output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "path": output.as_posix(),
            "bytes": written,
            "sha256": digest.hexdigest(),
            "source_url": url,
            "retrieved_on": date.today().isoformat(),
            "credential_source": credential.source_environment_variable,
        }
    finally:
        response.close()


def authenticated_netcdf_download(
    url: str,
    destination: str | Path,
    *,
    credential: EarthdataBearerToken,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 900.0),
    maximum_bytes: int = 1_000_000_000,
    maximum_attempts: int = 5,
    retry_backoff_seconds: float = 2.0,
    sleep_function: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Stream an authenticated NetCDF atomically with bounded transient retries."""

    if (
        isinstance(maximum_attempts, bool)
        or not isinstance(maximum_attempts, int)
        or maximum_attempts <= 0
    ):
        raise ValueError("maximum_attempts must be a positive integer.")
    if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be finite and non-negative.")
    for attempt in range(1, maximum_attempts + 1):
        try:
            return _authenticated_netcdf_download_once(
                url,
                destination,
                credential=credential,
                http_client=http_client,
                timeout=timeout,
                maximum_bytes=maximum_bytes,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt == maximum_attempts:
                raise
        except requests.HTTPError as error:
            status = None if error.response is None else error.response.status_code
            if status not in {429, 500, 502, 503, 504} or attempt == maximum_attempts:
                raise
        sleep_function(retry_backoff_seconds * (2 ** (attempt - 1)))
    raise AssertionError("Daymet retry loop exited without returning or raising.")


_DAYMET_NETCDF_UNIT_ALIASES: dict[str, frozenset[str]] = {
    "dayl": frozenset({"s", "s/day", "seconds/day", "s d-1"}),
    "prcp": frozenset({"mm", "mm/day", "mm d-1"}),
    "srad": frozenset({"w/m2", "w/m^2", "w m-2"}),
    "tmax": frozenset({"degrees c", "degree c", "deg c", "degc", "celsius"}),
    "tmin": frozenset({"degrees c", "degree c", "deg c", "degc", "celsius"}),
    "vp": frozenset({"pa", "pascal", "pascals"}),
}
_ALLOWED_CF_CALENDARS = frozenset({"standard", "gregorian", "proleptic_gregorian"})
_CF_TIME_UNITS = re.compile(
    r"^(?P<unit>days?|hours?|seconds?)\s+since\s+(?P<origin>.+)$",
    flags=re.IGNORECASE,
)


def _normalized_netcdf_unit(value: str) -> str:
    return " ".join(value.strip().lower().replace("²", "2").split())


def _tag_value(tags: Mapping[str, str], *suffixes: str) -> str | None:
    normalized = {str(key).casefold(): str(value) for key, value in tags.items()}
    for suffix in suffixes:
        folded = suffix.casefold()
        exact = normalized.get(folded)
        if exact is not None:
            return exact
        matches = [value for key, value in normalized.items() if key.endswith(folded)]
        if len(matches) == 1:
            return matches[0]
    return None


def _select_daymet_subdataset(path: Path, variable: str) -> str:
    with rasterio.open(path) as root:
        subdatasets = tuple(root.subdatasets)
        if not subdatasets:
            names = {
                value
                for value in (
                    _tag_value(root.tags(), "netcdf_varname"),
                    _tag_value(root.tags(1), "netcdf_varname") if root.count else None,
                )
                if value is not None
            }
            if names == {variable}:
                return str(path)
            raise DaymetGridAuditError(
                f"Daymet NetCDF {path} does not expose the expected variable {variable!r}."
            )
    candidates = [
        uri
        for uri in subdatasets
        if uri.rsplit(":", maxsplit=1)[-1].strip('"') == variable
    ]
    if len(candidates) != 1:
        raise DaymetGridAuditError(
            f"Daymet NetCDF must expose exactly one {variable!r} subdataset; "
            f"found {len(candidates)}."
        )
    return candidates[0]


def _parse_netcdf_dimension_values(value: str) -> tuple[float, ...]:
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text.strip():
        raise DaymetGridAuditError("Daymet NetCDF time coordinate is empty.")
    try:
        values = tuple(float(item.strip()) for item in text.split(","))
    except ValueError as error:
        raise DaymetGridAuditError(
            "Daymet NetCDF time coordinate contains a non-numeric value."
        ) from error
    if not all(math.isfinite(item) for item in values):
        raise DaymetGridAuditError("Daymet NetCDF time coordinate is non-finite.")
    return values


def _daymet_band_dates(
    source: rasterio.DatasetReader,
    *,
    year: int,
) -> tuple[pd.Timestamp, ...]:
    dataset_tags = source.tags()
    units = _tag_value(dataset_tags, "time#units", "netcdf_dim_time_units")
    if units is None:
        units = _tag_value(source.tags(1), "time#units", "netcdf_dim_time_units")
    if units is None:
        raise DaymetGridAuditError("Daymet NetCDF lacks CF time units.")
    match = _CF_TIME_UNITS.fullmatch(units.strip())
    if match is None:
        raise DaymetGridAuditError(f"Unsupported Daymet CF time units: {units!r}.")

    calendar = _tag_value(dataset_tags, "time#calendar", "netcdf_dim_time_calendar")
    if calendar is not None and calendar.strip().casefold() not in _ALLOWED_CF_CALENDARS:
        raise DaymetGridAuditError(f"Unsupported Daymet CF calendar: {calendar!r}.")

    per_band = [
        _tag_value(source.tags(index), "netcdf_dim_time")
        for index in range(1, source.count + 1)
    ]
    if all(value is not None for value in per_band):
        try:
            coordinate = tuple(float(str(value)) for value in per_band)
        except ValueError as error:
            raise DaymetGridAuditError(
                "Daymet NetCDF band time coordinate is non-numeric."
            ) from error
    else:
        raw_values = _tag_value(dataset_tags, "netcdf_dim_time_values")
        if raw_values is None:
            raise DaymetGridAuditError(
                "Daymet NetCDF lacks one time coordinate for every raster band."
            )
        coordinate = _parse_netcdf_dimension_values(raw_values)
    if not all(math.isfinite(value) for value in coordinate):
        raise DaymetGridAuditError("Daymet NetCDF time coordinate is non-finite.")
    if len(coordinate) != source.count:
        raise DaymetGridAuditError(
            "Daymet NetCDF time-coordinate length does not match its raster bands."
        )

    try:
        origin = pd.Timestamp(match.group("origin").strip())
    except (TypeError, ValueError) as error:
        raise DaymetGridAuditError("Daymet NetCDF has an invalid CF time origin.") from error
    if pd.isna(origin):
        raise DaymetGridAuditError("Daymet NetCDF has a missing CF time origin.")
    if origin.tzinfo is not None:
        origin = origin.tz_convert("UTC").tz_localize(None)
    unit = match.group("unit").casefold()
    pandas_unit = "D" if unit.startswith("day") else "h" if unit.startswith("hour") else "s"
    timestamps = pd.DatetimeIndex(
        origin + pd.to_timedelta(coordinate, unit=pandas_unit)
    )
    dates = timestamps.normalize()
    time_of_day = timestamps - dates
    allowed_time_of_day = {pd.Timedelta(0), pd.Timedelta(hours=12)}
    if (
        timestamps.has_duplicates
        or dates.has_duplicates
        or len(set(time_of_day)) != 1
        or time_of_day[0] not in allowed_time_of_day
    ):
        raise DaymetGridAuditError(
            "Daymet NetCDF time coordinates must be unique daily midnight or "
            "noon centers."
        )
    expected = pd.date_range(f"{year:04d}-01-01", periods=365, freq="D")
    if len(dates) != 365 or not dates.equals(expected):
        raise DaymetGridAuditError(
            f"Daymet year {year} must contain its exact 365-day calendar in order."
        )
    return tuple(pd.Timestamp(value) for value in dates)


def _daymet_nodata(source: rasterio.DatasetReader, *, variable: str) -> float:
    values = tuple(source.nodatavals)
    finite = [float(value) for value in values if value is not None and math.isfinite(value)]
    if len(finite) == len(values) and len(set(finite)) == 1:
        return finite[0]
    tagged = _tag_value(source.tags(), f"{variable}#_fillvalue", "_fillvalue")
    if tagged is None:
        tagged = _tag_value(source.tags(1), "_fillvalue")
    try:
        nodata = float(tagged) if tagged is not None else math.nan
    except ValueError as error:
        raise DaymetGridAuditError("Daymet NetCDF _FillValue is not numeric.") from error
    if not math.isfinite(nodata):
        raise DaymetGridAuditError(
            "Daymet NetCDF must declare one finite _FillValue across all bands."
        )
    return nodata


def _is_locked_daymet_crs(crs: rasterio.crs.CRS | None) -> bool:
    if crs is None:
        return False
    parameters = crs.to_dict()
    if str(parameters.get("proj", "")).casefold() != "lcc":
        return False
    expected = {
        "lat_1": 25.0,
        "lat_2": 60.0,
        "lat_0": 42.5,
        "lon_0": -100.0,
        "x_0": 0.0,
        "y_0": 0.0,
    }
    try:
        if any(
            not math.isclose(float(parameters[key]), value, rel_tol=0, abs_tol=1e-8)
            for key, value in expected.items()
        ):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if str(parameters.get("units", "")).casefold() not in {"m", "metre", "meter"}:
        return False
    if str(parameters.get("datum", "")).casefold() in {"wgs84", "wgs_84"}:
        return True
    if str(parameters.get("ellps", "")).casefold() in {"wgs84", "wgs_84"}:
        return True
    try:
        return math.isclose(
            float(parameters["a"]), 6_378_137.0, rel_tol=0, abs_tol=1e-3
        ) and math.isclose(
            float(parameters["rf"]), 298.257223563, rel_tol=0, abs_tol=1e-4
        )
    except (KeyError, TypeError, ValueError):
        return False


def inspect_daymet_netcdf(
    path: str | Path,
    *,
    variable: str,
    year: int,
    final_test_year: int = 2025,
) -> DaymetNetCDFSpec:
    """Fail closed on one official variable-year Daymet subset schema."""

    normalized_variable = _normalize_variables((variable,))[0]
    normalized_year = _normalize_development_years(
        (year,), final_test_year=final_test_year
    )[0]
    source_path = Path(path)
    if not source_path.is_file() or not _has_netcdf_signature(source_path):
        raise DaymetGridAuditError(f"Daymet subset is missing or not NetCDF: {source_path}")
    uri = _select_daymet_subdataset(source_path, normalized_variable)
    with rasterio.open(uri) as source:
        if source.count != 365 or min(source.width, source.height) <= 0:
            raise DaymetGridAuditError(
                "Daymet NetCDF must contain 365 non-empty raster bands."
            )
        if not _is_locked_daymet_crs(source.crs):
            raise DaymetGridAuditError(
                "Daymet NetCDF CRS is not the locked WGS84 Lambert Conformal Conic grid."
            )
        transform = source.transform
        coefficients = tuple(float(value) for value in transform)
        if not all(math.isfinite(value) for value in coefficients):
            raise DaymetGridAuditError("Daymet NetCDF transform is non-finite.")
        if (
            not math.isclose(transform.a, 1000.0, rel_tol=0, abs_tol=1e-6)
            or not math.isclose(transform.e, -1000.0, rel_tol=0, abs_tol=1e-6)
            or not math.isclose(transform.b, 0.0, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(transform.d, 0.0, rel_tol=0, abs_tol=1e-12)
        ):
            raise DaymetGridAuditError(
                "Daymet NetCDF must use a north-up native 1 km grid."
            )
        units = _tag_value(
            source.tags(), f"{normalized_variable}#units", "units"
        ) or _tag_value(source.tags(1), "units")
        if units is None or _normalized_netcdf_unit(units) not in {
            _normalized_netcdf_unit(value)
            for value in _DAYMET_NETCDF_UNIT_ALIASES[normalized_variable]
        }:
            raise DaymetGridAuditError(
                f"Daymet variable {normalized_variable} has unexpected units {units!r}."
            )
        dates = _daymet_band_dates(source, year=normalized_year)
        nodata = _daymet_nodata(source, variable=normalized_variable)
        scales = tuple(float(value) for value in source.scales)
        offsets = tuple(float(value) for value in source.offsets)
        if (
            len(scales) != source.count
            or len(offsets) != source.count
            or not all(math.isfinite(value) and value != 0 for value in scales)
            or not all(math.isfinite(value) for value in offsets)
        ):
            raise DaymetGridAuditError("Daymet NetCDF has invalid scale/offset metadata.")
        return DaymetNetCDFSpec(
            path=source_path,
            variable=normalized_variable,
            year=normalized_year,
            subdataset_uri=uri,
            shape=(source.height, source.width),
            transform=transform,
            crs_wkt=source.crs.to_wkt(),
            dates=dates,
            nodata=nodata,
            scales=scales,
            offsets=offsets,
            units=units,
        )


def validate_daymet_netcdf_grid_specs(
    specs: Sequence[DaymetNetCDFSpec],
) -> DaymetNetCDFSpec:
    """Require all variable/year subsets to share one native spatial grid."""

    if isinstance(specs, (str, bytes)) or not specs:
        raise ValueError("At least one Daymet NetCDF specification is required.")
    reference = specs[0]
    reference_crs = rasterio.crs.CRS.from_wkt(reference.crs_wkt)
    for spec in specs[1:]:
        if (
            spec.shape != reference.shape
            or tuple(spec.transform) != tuple(reference.transform)
            or rasterio.crs.CRS.from_wkt(spec.crs_wkt) != reference_crs
        ):
            raise DaymetGridAuditError(
                "Daymet variable/year subsets do not share one fixed native grid."
            )
    return reference


def read_daymet_netcdf_cells(
    spec: DaymetNetCDFSpec,
    *,
    cells: pd.DataFrame,
) -> pd.DataFrame:
    """Read one audited variable-year subset only at fixed Daymet cells."""

    required = {"daymet_cell_id", "daymet_row", "daymet_col"}
    if missing := required.difference(cells.columns):
        raise ValueError(f"Requested Daymet cells lack columns: {sorted(missing)}")
    selected = cells.loc[:, sorted(required)].drop_duplicates().copy()
    if selected.empty or selected["daymet_cell_id"].duplicated().any():
        raise DaymetGridAuditError("Requested Daymet cells must be unique and non-empty.")
    for column, size in (("daymet_row", spec.shape[0]), ("daymet_col", spec.shape[1])):
        numeric = pd.to_numeric(selected[column], errors="coerce").to_numpy(dtype=float)
        if (
            not np.isfinite(numeric).all()
            or not np.equal(numeric, np.floor(numeric)).all()
            or (numeric < 0).any()
            or (numeric >= size).any()
        ):
            raise DaymetGridAuditError(f"Requested {column} values are outside the subset.")
        selected[column] = numeric.astype(np.int64)
    if selected.duplicated(["daymet_row", "daymet_col"]).any():
        raise DaymetGridAuditError("Requested Daymet grid coordinates must be unique.")
    selected = selected.sort_values(["daymet_row", "daymet_col"], kind="stable")
    rows = selected["daymet_row"].to_numpy(dtype=np.int64)
    columns = selected["daymet_col"].to_numpy(dtype=np.int64)
    cell_x, cell_y = xy(spec.transform, rows, columns, offset="center")
    expected_ids = np.asarray(
        [
            f"x{x_value:.3f}_y{y_value:.3f}"
            for x_value, y_value in zip(cell_x, cell_y, strict=True)
        ]
    )
    if not np.array_equal(
        selected["daymet_cell_id"].astype(str).to_numpy(), expected_ids
    ):
        raise DaymetGridAuditError(
            "Requested Daymet cell IDs disagree with their native grid coordinates."
        )
    with rasterio.open(spec.subdataset_uri) as source:
        if (
            source.shape != spec.shape
            or tuple(source.transform) != tuple(spec.transform)
            or source.count != len(spec.dates)
        ):
            raise RuntimeError(f"Daymet NetCDF changed after inspection: {spec.path}")
        raw = source.read()[:, rows, columns].astype(np.float64, copy=False)
    nodata = raw == spec.nodata
    invalid = nodata | ~np.isfinite(raw)
    values = raw * np.asarray(spec.scales)[:, None] + np.asarray(spec.offsets)[:, None]
    values[invalid] = np.nan
    dates = pd.DatetimeIndex(spec.dates)
    output = pd.DataFrame(
        {
            "daymet_cell_id": np.tile(
                selected["daymet_cell_id"].astype(str).to_numpy(), len(dates)
            ),
            "date": np.repeat(dates.to_numpy(), len(selected)),
            DAYMET_VARIABLES[spec.variable].column: values.reshape(-1),
        }
    )
    if output.duplicated(["daymet_cell_id", "date"]).any():
        raise AssertionError("Decoded Daymet cell-date keys are not unique.")
    return output


def build_fixed_eligible_cell_weights(
    *,
    zone_raster: np.ndarray,
    eligible_land_mask: np.ndarray,
    tract_geoids: Sequence[str],
    target_transform: rasterio.Affine,
    target_crs: object,
    daymet_transform: rasterio.Affine,
    daymet_crs: object,
    daymet_shape: tuple[int, int],
) -> pd.DataFrame:
    """Map frozen eligible 30 m pixel centers to native Daymet grid cells.

    Pixel-center assignment preserves the already-locked eligible-land support:
    every eligible target-grid pixel contributes its fixed projected area to
    exactly one Daymet cell.  The returned weights are static by construction.
    """

    zones = np.asarray(zone_raster)
    eligible = np.asarray(eligible_land_mask, dtype=bool)
    if zones.ndim != 2 or eligible.shape != zones.shape:
        raise ValueError("Zone raster and eligible-land mask must share a 2-D shape.")
    geoids = tuple(str(value) for value in tract_geoids)
    if not geoids or len(set(geoids)) != len(geoids) or any(not value for value in geoids):
        raise ValueError("tract_geoids must be unique, non-empty strings.")
    if len(daymet_shape) != 2 or min(daymet_shape) <= 0:
        raise ValueError("daymet_shape must contain positive height and width.")
    selected = eligible & (zones > 0)
    rows, columns = np.nonzero(selected)
    if len(rows) == 0:
        raise DaymetGridAuditError("Frozen eligible-land support is empty.")
    zone_ids = zones[rows, columns].astype(np.int64)
    if zone_ids.min() < 1 or zone_ids.max() > len(geoids):
        raise DaymetGridAuditError("Zone raster contains an unmapped tract index.")
    expected_zone_ids = set(range(1, len(geoids) + 1))
    observed_zone_ids = set(zone_ids.tolist())
    if observed_zone_ids != expected_zone_ids:
        missing_geoids = [
            geoids[index - 1] for index in sorted(expected_zone_ids - observed_zone_ids)
        ]
        raise DaymetGridAuditError(
            "Frozen eligible-land support is missing tracts: "
            f"{missing_geoids[:10]}"
        )

    source_x, source_y = xy(target_transform, rows, columns, offset="center")
    daymet_x, daymet_y = transform_coordinates(
        target_crs, daymet_crs, source_x, source_y
    )
    daymet_rows, daymet_columns = rowcol(
        daymet_transform, daymet_x, daymet_y, op=np.floor
    )
    daymet_rows = np.asarray(daymet_rows, dtype=np.int64)
    daymet_columns = np.asarray(daymet_columns, dtype=np.int64)
    inside = (
        (daymet_rows >= 0)
        & (daymet_rows < daymet_shape[0])
        & (daymet_columns >= 0)
        & (daymet_columns < daymet_shape[1])
    )
    if not inside.all():
        raise DaymetGridAuditError(
            "Daymet subset does not cover every frozen eligible-land pixel."
        )
    cell_x, cell_y = xy(
        daymet_transform, daymet_rows, daymet_columns, offset="center"
    )
    pixel_area = abs(
        target_transform.a * target_transform.e
        - target_transform.b * target_transform.d
    )
    if not math.isfinite(pixel_area) or pixel_area <= 0:
        raise ValueError("Target transform must have a positive finite pixel area.")

    pixels = pd.DataFrame(
        {
            "tract_geoid": [geoids[index - 1] for index in zone_ids],
            "daymet_row": daymet_rows,
            "daymet_col": daymet_columns,
            "daymet_x_m": np.asarray(cell_x, dtype=float),
            "daymet_y_m": np.asarray(cell_y, dtype=float),
        }
    )
    pixels["daymet_cell_id"] = [
        f"x{x_value:.3f}_y{y_value:.3f}"
        for x_value, y_value in zip(
            pixels["daymet_x_m"], pixels["daymet_y_m"], strict=True
        )
    ]
    group_columns = [
        "tract_geoid",
        "daymet_cell_id",
        "daymet_row",
        "daymet_col",
        "daymet_x_m",
        "daymet_y_m",
    ]
    weights = (
        pixels.groupby(group_columns, sort=True, observed=True)
        .size()
        .rename("eligible_pixel_count")
        .reset_index()
    )
    weights["eligible_area_m2"] = weights["eligible_pixel_count"] * pixel_area
    weights["static_denominator_m2"] = weights.groupby(
        "tract_geoid", sort=False
    )["eligible_area_m2"].transform("sum")
    weights["weight"] = (
        weights["eligible_area_m2"] / weights["static_denominator_m2"]
    )
    return validate_fixed_cell_weights(weights)


def validate_fixed_cell_weights(weights: pd.DataFrame) -> pd.DataFrame:
    """Validate fixed Daymet-cell weights and return a sorted defensive copy."""

    required = {
        "tract_geoid",
        "daymet_cell_id",
        "eligible_pixel_count",
        "eligible_area_m2",
        "static_denominator_m2",
        "weight",
    }
    missing = required.difference(weights.columns)
    if missing:
        raise ValueError(f"Daymet weights are missing columns: {sorted(missing)}")
    checked = weights.copy()
    if checked.empty or checked.duplicated(["tract_geoid", "daymet_cell_id"]).any():
        raise DaymetGridAuditError("Daymet weights must have unique tract-cell rows.")
    for column in ("eligible_pixel_count", "eligible_area_m2", "static_denominator_m2", "weight"):
        values = pd.to_numeric(checked[column], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise DaymetGridAuditError(
                f"Daymet weight column {column} must be finite and positive."
            )
    pixel_counts = pd.to_numeric(
        checked["eligible_pixel_count"], errors="coerce"
    ).to_numpy(dtype=float)
    if not np.equal(pixel_counts, np.floor(pixel_counts)).all():
        raise DaymetGridAuditError(
            "Daymet eligible_pixel_count values must be positive integers."
        )
    denominator_variants = checked.groupby("tract_geoid", sort=False)[
        "static_denominator_m2"
    ].nunique()
    if (denominator_variants != 1).any():
        raise DaymetGridAuditError(
            "Static eligible-land denominator changes within a tract."
        )
    by_tract = checked.groupby("tract_geoid", sort=False)
    area_sums = by_tract["eligible_area_m2"].sum()
    denominators = by_tract["static_denominator_m2"].first()
    weight_sums = by_tract["weight"].sum()
    if not np.allclose(area_sums, denominators, rtol=0, atol=1e-6):
        raise DaymetGridAuditError(
            "Daymet eligible areas do not sum to the static denominator."
        )
    if not np.allclose(weight_sums, 1.0, rtol=0, atol=1e-12):
        raise DaymetGridAuditError("Daymet weights do not sum to one per tract.")
    expected = checked["eligible_area_m2"] / checked["static_denominator_m2"]
    if not np.allclose(checked["weight"], expected, rtol=0, atol=1e-12):
        raise DaymetGridAuditError("Daymet weight values do not match fixed areas.")
    return checked.sort_values(
        ["tract_geoid", "daymet_cell_id"], kind="stable"
    ).reset_index(drop=True)


def aggregate_daymet_cells_to_tract_daily(
    cell_daily: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    final_test_year: int = 2025,
) -> pd.DataFrame:
    """Area-weight daily grid-cell weather to tracts without renormalization."""

    checked_weights = validate_fixed_cell_weights(weights)
    required_weather = {
        DAYMET_VARIABLES[variable].column for variable in DEFAULT_DAYMET_VARIABLES
    }
    required = {"daymet_cell_id", "date", *required_weather}
    missing = required.difference(cell_daily.columns)
    if missing:
        raise ValueError(f"Daymet cell daily data are missing: {sorted(missing)}")
    forbidden = [
        column
        for column in cell_daily.columns
        if any(token in column.lower() for token in ("lst", "thermal", "target_"))
    ]
    if forbidden:
        raise DaymetGridAuditError(
            f"Target/thermal columns are prohibited in Daymet inputs: {forbidden}"
        )
    daily = cell_daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="raise")
    if daily["date"].dt.tz is not None or not daily["date"].dt.normalize().equals(
        daily["date"]
    ):
        raise ValueError("Daymet cell dates must be timezone-naive civil midnights.")
    if (daily["date"].dt.year >= final_test_year).any():
        raise PermissionError("Daymet cell data include the locked final-test year.")
    if daily.duplicated(["daymet_cell_id", "date"]).any():
        raise DaymetGridAuditError("Daymet cell-date keys must be unique.")
    for column in sorted(required_weather):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
        finite_or_missing = np.isfinite(daily[column]) | daily[column].isna()
        if not finite_or_missing.all():
            raise DaymetGridAuditError(f"Daymet cell variable {column} is non-finite.")
    if (daily["tmax_c"] < daily["tmin_c"]).fillna(False).any():
        raise DaymetGridAuditError("Daymet grid-cell tmax cannot be below tmin.")
    for column in ("prcp_mm_day", "srad_w_m2", "vp_pa", "dayl_s"):
        if (daily[column] < 0).fillna(False).any():
            raise DaymetGridAuditError(f"Daymet grid-cell {column} cannot be negative.")
    if (daily["dayl_s"] > 86_400).fillna(False).any():
        raise DaymetGridAuditError("Daymet grid-cell dayl_s exceeds one day.")
    daily[DERIVED_SRAD_ENERGY_COLUMN] = (
        daily["srad_w_m2"] * daily["dayl_s"] / 1_000_000.0
    )

    used_cells = checked_weights["daymet_cell_id"].drop_duplicates().tolist()
    dates = pd.DatetimeIndex(sorted(daily["date"].unique()))
    complete_index = pd.MultiIndex.from_product(
        [used_cells, dates], names=["daymet_cell_id", "date"]
    )
    weather_columns = [
        DAYMET_VARIABLES[variable].column for variable in DEFAULT_DAYMET_VARIABLES
    ] + [DERIVED_SRAD_ENERGY_COLUMN]
    dense = (
        daily.set_index(["daymet_cell_id", "date"])[weather_columns]
        .reindex(complete_index)
        .reset_index()
    )
    joined = checked_weights.merge(
        dense, on="daymet_cell_id", how="left", validate="many_to_many"
    )
    joined["year"] = joined["date"].dt.year.astype("int32")
    joined["yday"] = joined["date"].dt.dayofyear.astype("int16")
    group_keys = ["tract_geoid", "date", "year", "yday"]
    expected_cells = joined.groupby(group_keys, sort=True)["daymet_cell_id"].size()
    records = expected_cells.rename("daymet_grid_cells_expected").to_frame()
    complete_all = joined[weather_columns].notna().all(axis=1)
    records["daymet_grid_cells_present"] = complete_all.groupby(
        [joined[column] for column in group_keys], sort=True
    ).sum()
    denominator = joined.groupby(group_keys, sort=True)[
        "static_denominator_m2"
    ].first()
    records["daymet_static_eligible_area_m2"] = denominator
    for column in weather_columns:
        valid_count = joined[column].notna().groupby(
            [joined[key] for key in group_keys], sort=True
        ).sum()
        weighted = (joined[column] * joined["weight"]).groupby(
            [joined[key] for key in group_keys], sort=True
        ).sum(min_count=1)
        records[column] = weighted.where(valid_count == expected_cells)
    output = records.reset_index().sort_values(
        ["tract_geoid", "date"], kind="stable"
    )
    output = output.reset_index(drop=True)
    output.attrs = {
        "source": "Daymet V4 R1 gridded daily data",
        "dataset_doi": DAYMET_DOI_URL,
        "spatial_aggregation": "fixed eligible-land area weights",
        "date_specific_weight_renormalization": False,
        "srad_energy_computed_cell_first": True,
    }
    return output


def build_lagged_tract_daymet_features(
    tract_daily: pd.DataFrame,
    *,
    target_dates: Sequence[str | pd.Timestamp],
    windows: Sequence[int] = (1, 3, 7),
    final_test_year: int = 2025,
) -> pd.DataFrame:
    """Build tract-date Daymet features using complete windows ending at d-1."""

    required = {"tract_geoid", "date", "year", "yday"}
    missing = required.difference(tract_daily.columns)
    if missing:
        raise ValueError(f"Tract daily Daymet data are missing: {sorted(missing)}")
    normalized_dates = pd.DatetimeIndex(pd.to_datetime(target_dates, errors="raise"))
    if normalized_dates.empty or normalized_dates.has_duplicates:
        raise ValueError("Target dates must be non-empty and unique.")
    if normalized_dates.tz is not None or not normalized_dates.normalize().equals(
        normalized_dates
    ):
        raise ValueError("Target dates must be timezone-naive civil midnights.")
    if (normalized_dates.year >= final_test_year).any():
        raise PermissionError("Target dates include the locked final-test year.")
    maximum_window = max(int(window) for window in windows)
    if maximum_window <= 0:
        raise ValueError("Lag windows must be positive.")

    outputs: list[pd.DataFrame] = []
    for tract_geoid, group in tract_daily.groupby("tract_geoid", sort=True):
        if group["date"].duplicated().any():
            raise DaymetGridAuditError(
                f"Tract {tract_geoid} has duplicate Daymet daily dates."
            )
        lagged = build_lagged_features(group, windows=windows)
        selected = lagged.reindex(normalized_dates).copy()
        selected.insert(0, "target_date", normalized_dates)
        selected.insert(0, "tract_geoid", str(tract_geoid))
        selected["daymet_source_start_date"] = (
            selected["target_date"] - pd.to_timedelta(maximum_window, unit="D")
        )
        selected["daymet_source_end_date"] = selected["target_date"] - pd.Timedelta(
            days=1
        )
        feature_columns = [
            column
            for column in selected
            if column.startswith("daymet_") and "source_" not in column
        ]
        selected["daymet_all_primary_windows_complete"] = selected[
            feature_columns
        ].notna().all(axis=1)
        outputs.append(selected.reset_index(drop=True))
    if not outputs:
        raise ValueError("Tract daily Daymet data cannot be empty.")
    result = pd.concat(outputs, ignore_index=True).sort_values(
        ["tract_geoid", "target_date"], kind="stable"
    )
    if not (result["daymet_source_end_date"] < result["target_date"]).all():
        raise DaymetGridAuditError("Daymet dynamic lineage reaches the target date.")
    result = result.reset_index(drop=True)
    result.attrs = {
        "source": "Daymet V4 R1 gridded daily data",
        "dataset_doi": DAYMET_DOI_URL,
        "window_definition": "complete civil days d-n through d-1",
        "target_day_observations_included": False,
    }
    return result
