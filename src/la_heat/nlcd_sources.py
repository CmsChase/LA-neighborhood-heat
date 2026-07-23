"""Pinned, anonymous MRLC WCS snapshots of the original NLCD 2016 layers.

The original CONUS bundles are roughly gigabyte-scale.  MRLC's public WCS can
return a native-grid subset without resampling, so this module pins one
EPSG:5070, 30 m window that fully contains the locked Los Angeles target grid.
Every response is checked by byte count, SHA-256, raster grid, data type, and
value domain before an atomic commit marker is promoted.

The impervious WCS response declares 0 as its TIFF NoData value even though
0 percent imperviousness is scientifically valid and the product's actual
NoData code is 127.  Downstream code must therefore ignore the TIFF mask for
that layer and apply the explicit value-domain rule ``0..100 valid; 127
NoData``.  This behavior is audited here and again at feature construction.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import rasterio
import requests
from rasterio import Affine

from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

NLCD_SOURCE_SCHEMA_VERSION = 1
NLCD_SOURCE_ALGORITHM_VERSION = "nlcd-2016-mrlc-wcs-la-subset-v1"
NLCD_SOURCE_COMMIT_MARKER = "nlcd_2016_sources_provenance.json"
MRLC_WCS_ENDPOINT = "https://www.mrlc.gov/geoserver/ows"
NLCD_2016_OFFICIAL_DOI = "https://doi.org/10.5066/P937PN4Z"
NLCD_2016_RELEASE_DATE = "2019-04-30"

# Native EPSG:5070 source-pixel edges.  This window is padded beyond the
# transformed fixed-grid extent so downstream reprojection never reads an
# artificial subset edge.
NLCD_LA_SUBSET_BOUNDS = (-2_066_055.0, 1_418_445.0, -2_004_585.0, 1_498_245.0)
NLCD_LA_SUBSET_SHAPE = (2660, 2049)
NLCD_LA_SUBSET_TRANSFORM = Affine(
    30.0,
    0.0,
    NLCD_LA_SUBSET_BOUNDS[0],
    0.0,
    -30.0,
    NLCD_LA_SUBSET_BOUNDS[3],
)


class NlcdSourceAuditError(ValueError):
    """Raised when an NLCD subset cannot be proven to match its pin."""


@dataclass(frozen=True, slots=True)
class NlcdSubsetSpec:
    source_id: str
    filename: str
    coverage_id: str
    product: Literal["land_cover", "impervious"]
    expected_bytes: int
    expected_sha256: str

    def __post_init__(self) -> None:
        if Path(self.filename).name != self.filename or Path(self.filename).is_absolute():
            raise ValueError(f"Unsafe NLCD destination filename: {self.filename!r}")
        if len(self.expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_sha256
        ):
            raise ValueError(f"Invalid NLCD SHA-256 for {self.source_id!r}.")
        if self.expected_bytes <= 0:
            raise ValueError("Expected NLCD byte count must be positive.")

    @property
    def query_parameters(self) -> tuple[tuple[str, str], ...]:
        left, bottom, right, top = NLCD_LA_SUBSET_BOUNDS
        return (
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("request", "GetCoverage"),
            ("coverageId", self.coverage_id),
            ("format", "image/tiff"),
            ("subset", f"X({int(left)},{int(right)})"),
            ("subset", f"Y({int(bottom)},{int(top)})"),
        )


NLCD_2016_LAND_COVER = NlcdSubsetSpec(
    source_id="nlcd_2016_land_cover_la_subset",
    filename="nlcd_2016_land_cover_la_subset.tif",
    coverage_id="mrlc_download__NLCD_2016_Land_Cover_L48",
    product="land_cover",
    expected_bytes=7_866_492,
    expected_sha256="d40a7cb3dfa2009afc19114175ece977390a917cc193afbc96d89a8506af99a0",
)

NLCD_2016_IMPERVIOUS = NlcdSubsetSpec(
    source_id="nlcd_2016_impervious_la_subset",
    filename="nlcd_2016_impervious_la_subset.tif",
    coverage_id="mrlc_download__NLCD_2016_Impervious_L48",
    product="impervious",
    expected_bytes=7_866_492,
    expected_sha256="c87a3a3dd5908542261d261d00e27d07bf9254cc1be190220d76761106a61532",
)

NLCD_SUBSET_SPECS = (NLCD_2016_LAND_COVER, NLCD_2016_IMPERVIOUS)
_LAND_COVER_ALLOWED = frozenset(
    {0, 11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}
)


class _ResponseLike(Protocol):
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...

    def iter_content(self, *, chunk_size: int) -> object: ...

    def close(self) -> None: ...


class NlcdHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Sequence[tuple[str, str]],
        stream: bool,
        timeout: tuple[float, float] | float,
    ) -> _ResponseLike: ...


def _same_transform(actual: Affine, expected: Affine) -> bool:
    return all(
        math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
        for left, right in zip(tuple(actual)[:6], tuple(expected)[:6], strict=True)
    )


def validate_nlcd_subset(path: str | Path, spec: NlcdSubsetSpec) -> dict[str, object]:
    """Fail closed unless one subset exactly matches its pinned bytes and schema."""

    source = Path(path)
    if not source.is_file():
        raise NlcdSourceAuditError(f"NLCD subset is missing: {source}")
    actual_bytes = source.stat().st_size
    actual_sha256 = sha256_file(source)
    if actual_bytes != spec.expected_bytes:
        raise NlcdSourceAuditError(
            f"NLCD byte-count mismatch for {spec.source_id}: "
            f"{actual_bytes} != {spec.expected_bytes}."
        )
    if actual_sha256 != spec.expected_sha256:
        raise NlcdSourceAuditError(
            f"NLCD SHA-256 mismatch for {spec.source_id}: "
            f"{actual_sha256} != {spec.expected_sha256}."
        )
    try:
        with rasterio.open(source) as dataset:
            if dataset.count != 1 or dataset.shape != NLCD_LA_SUBSET_SHAPE:
                raise NlcdSourceAuditError(
                    f"NLCD raster shape/count mismatch for {spec.source_id}."
                )
            if dataset.crs is None or dataset.crs.to_epsg() != 5070:
                raise NlcdSourceAuditError(f"NLCD raster CRS mismatch for {spec.source_id}.")
            if dataset.dtypes != ("uint8",):
                raise NlcdSourceAuditError(f"NLCD raster dtype mismatch for {spec.source_id}.")
            if not _same_transform(dataset.transform, NLCD_LA_SUBSET_TRANSFORM):
                raise NlcdSourceAuditError(
                    f"NLCD raster transform mismatch for {spec.source_id}."
                )
            if dataset.nodata != 0:
                raise NlcdSourceAuditError(
                    f"Expected the audited WCS TIFF NoData metadata value 0; got "
                    f"{dataset.nodata!r}."
                )
            if dataset.tags().get("AREA_OR_POINT") != "Area":
                raise NlcdSourceAuditError("NLCD raster must declare AREA_OR_POINT=Area.")
            values = dataset.read(1)
    except NlcdSourceAuditError:
        raise
    except Exception as error:
        raise NlcdSourceAuditError(f"Cannot inspect NLCD raster {source}: {error}") from error

    unique = set(int(value) for value in np.unique(values))
    if spec.product == "land_cover":
        unexpected = sorted(unique - _LAND_COVER_ALLOWED)
        if unexpected:
            raise NlcdSourceAuditError(f"Unexpected NLCD land-cover values: {unexpected}")
        scientific_nodata = 0
    else:
        unexpected = sorted(value for value in unique if value > 100 and value != 127)
        if unexpected or 127 not in unique or 0 not in unique:
            raise NlcdSourceAuditError(
                "NLCD impervious values must include valid 0 and NoData 127, "
                f"with no other values above 100; unexpected={unexpected}."
            )
        scientific_nodata = 127
    return {
        "bytes": actual_bytes,
        "sha256": actual_sha256,
        "shape": list(NLCD_LA_SUBSET_SHAPE),
        "crs": "EPSG:5070",
        "transform": list(tuple(NLCD_LA_SUBSET_TRANSFORM)[:6]),
        "dtype": "uint8",
        "wcs_tiff_nodata_metadata": 0,
        "scientific_nodata": scientific_nodata,
        "minimum_value": int(values.min()),
        "maximum_value": int(values.max()),
        "unique_value_count": len(unique),
    }


def _load_valid_commit(path: Path) -> dict[str, object] | None:
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


def _download_one(
    *,
    spec: NlcdSubsetSpec,
    destination: Path,
    client: NlcdHttpClient,
    timeout: tuple[float, float] | float,
) -> tuple[dict[str, object], dict[str, str]]:
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
    response: _ResponseLike | None = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        response = client.get(
            MRLC_WCS_ENDPOINT,
            params=spec.query_parameters,
            stream=True,
            timeout=timeout,
        )
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):  # type: ignore[union-attr]
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
        if byte_count != spec.expected_bytes or digest.hexdigest() != spec.expected_sha256:
            raise NlcdSourceAuditError(
                f"Downloaded NLCD pin mismatch for {spec.source_id}: "
                f"bytes={byte_count}, sha256={digest.hexdigest()}."
            )
        validation = validate_nlcd_subset(temporary, spec)
        headers = {
            str(key).lower(): str(value)
            for key, value in response.headers.items()
            if str(key).lower() in {"content-type", "content-length", "etag", "last-modified"}
        }
        os.replace(temporary, destination)
        return validation, headers
    finally:
        temporary.unlink(missing_ok=True)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def download_nlcd_2016_sources(
    output_directory: str | Path,
    *,
    http_client: NlcdHttpClient | None = None,
    timeout: tuple[float, float] | float = (30.0, 240.0),
    force: bool = False,
    specs: Sequence[NlcdSubsetSpec] = NLCD_SUBSET_SPECS,
) -> Path:
    """Download, audit, and atomically commit the two pinned NLCD subsets."""

    selected = tuple(specs)
    if not selected:
        raise ValueError("At least one NLCD subset specification is required.")
    if len({spec.source_id for spec in selected}) != len(selected):
        raise ValueError("NLCD source IDs must be unique.")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    marker = output / NLCD_SOURCE_COMMIT_MARKER
    prior = _load_valid_commit(marker)
    marker.unlink(missing_ok=True)
    client: NlcdHttpClient = requests if http_client is None else http_client
    records: dict[str, object] = {}
    for spec in selected:
        destination = output / spec.filename
        cache_hit = False
        headers: dict[str, str] = {}
        try:
            validation = validate_nlcd_subset(destination, spec) if not force else None
        except NlcdSourceAuditError:
            validation = None
        if validation is None:
            validation, headers = _download_one(
                spec=spec,
                destination=destination,
                client=client,
                timeout=timeout,
            )
        else:
            cache_hit = True
            if prior is not None:
                old_sources = prior.get("sources")
                if isinstance(old_sources, Mapping):
                    old = old_sources.get(spec.source_id)
                    if isinstance(old, Mapping) and isinstance(
                        old.get("response_headers"), Mapping
                    ):
                        headers = {
                            str(key): str(value)
                            for key, value in old["response_headers"].items()
                        }
        records[spec.source_id] = {
            "source_id": spec.source_id,
            "product": spec.product,
            "filename": spec.filename,
            "path": str(destination.resolve()),
            "wcs_endpoint": MRLC_WCS_ENDPOINT,
            "wcs_coverage_id": spec.coverage_id,
            "wcs_query_parameters": [list(pair) for pair in spec.query_parameters],
            "official_usgs_doi": NLCD_2016_OFFICIAL_DOI,
            "release_date": NLCD_2016_RELEASE_DATE,
            "publisher": "U.S. Geological Survey / MRLC Consortium",
            "license": "CC0 1.0 Universal / U.S. public domain",
            "cache_hit": cache_hit,
            "response_headers": headers,
            "validation": validation,
        }
    payload: dict[str, object] = {
        "schema_version": NLCD_SOURCE_SCHEMA_VERSION,
        "algorithm_version": NLCD_SOURCE_ALGORITHM_VERSION,
        "state": "complete",
        "promoted_outputs_valid": True,
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "output_directory": str(output.resolve()),
        "source_count": len(selected),
        "native_subset_bounds_epsg5070": list(NLCD_LA_SUBSET_BOUNDS),
        "sources": records,
        "impervious_nodata_warning": (
            "Ignore TIFF NoData=0; valid domain is 0..100 and scientific NoData is 127."
        ),
        "implementation_sha256": sha256_file(Path(__file__)),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return marker
