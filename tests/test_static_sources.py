from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import numpy as np
import pytest
import requests
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from la_heat.provenance import canonical_sha256
from la_heat.static_sources import (
    CENSUS_2019_COASTLINE,
    CENSUS_2019_COASTLINE_OFFICIAL_URL,
    CENSUS_2019_COASTLINE_WAYBACK_URL,
    CENSUS_COASTLINE_SOURCE_ID,
    OPEN_TOPOGRAPHY_SRTM_BASE_URL,
    SRTM_LAT34_SEAM_PAIR,
    SRTM_N33_SOURCE_ID,
    SRTM_N33W119,
    SRTM_N34_SOURCE_ID,
    SRTM_N34W119,
    STATIC_SOURCES_COMMIT_MARKER,
    RasterExpectation,
    StaticSourceAuditError,
    StaticSourceSpec,
    ZipMemberExpectation,
    download_static_sources,
    validate_raster,
    validate_raster_seam,
    validate_zip,
)


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, *, chunk_size: int):  # type: ignore[no-untyped-def]
        step = max(1, min(chunk_size, 37))
        for start in range(0, len(self.payload), step):
            yield self.payload[start : start + step]

    def close(self) -> None:
        self.closed = True


class _FakeClient:
    def __init__(self, responses: Mapping[str, _FakeResponse | Exception]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, bool, object]] = []

    def get(
        self,
        url: str,
        *,
        stream: bool,
        timeout: object,
    ) -> _FakeResponse:
        self.calls.append((url, stream, timeout))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class _NoNetworkClient:
    def get(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("A valid audited cache must not use the network.")


def _raster_payload(transform: Affine, data: np.ndarray) -> bytes:
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=data.shape[0],
            width=data.shape[1],
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=transform,
            nodata=-32768,
            compress="deflate",
        ) as dataset:
            dataset.write(data.astype(np.int16), 1)
            dataset.update_tags(AREA_OR_POINT="Area")
        return memory.read()


def _raster_expectation(transform: Affine, shape: tuple[int, int]) -> RasterExpectation:
    return RasterExpectation(
        shape=shape,
        crs="EPSG:4326",
        dtype="int16",
        nodata=-32768.0,
        transform=tuple(float(value) for value in tuple(transform)[:6]),
        area_or_point="Area",
    )


def _zip_payload(contents: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            info = zipfile.ZipInfo(name, date_time=(2019, 8, 9, 4, 8, 50))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def _common_spec_fields() -> dict[str, object]:
    return {
        "publisher": "test publisher",
        "dataset": "test dataset",
        "version": "test version",
        "data_vintage": "test vintage",
        "catalog_url": "https://example.test/catalog",
        "license_note": "test license",
        "provenance_caveats": ("test-only fixture",),
    }


def _raster_spec(
    *,
    source_id: str,
    filename: str,
    url: str,
    payload: bytes,
    expectation: RasterExpectation,
) -> StaticSourceSpec:
    return StaticSourceSpec(
        source_id=source_id,
        filename=filename,
        kind="raster",
        urls=(url,),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_bytes=len(payload),
        raster=expectation,
        **_common_spec_fields(),
    )


def _zip_spec(
    *,
    official_url: str,
    fallback_url: str,
    payload: bytes,
    contents: Mapping[str, bytes],
) -> StaticSourceSpec:
    members = tuple(
        ZipMemberExpectation(
            name=name,
            bytes=len(content),
            crc32=zlib.crc32(content) & 0xFFFFFFFF,
        )
        for name, content in contents.items()
    )
    return StaticSourceSpec(
        source_id=CENSUS_COASTLINE_SOURCE_ID,
        filename="coastline.zip",
        kind="zip",
        urls=(official_url, fallback_url),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_bytes=len(payload),
        zip_members=members,
        **_common_spec_fields(),
    )


def _offline_fixture() -> tuple[
    tuple[StaticSourceSpec, ...], dict[str, _FakeResponse | Exception]
]:
    lower_transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 2.0)
    upper_transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 3.0)
    shared = np.array([9, 8, 7], dtype=np.int16)
    lower_payload = _raster_payload(
        lower_transform,
        np.vstack([shared, np.array([1, 2, 3], dtype=np.int16)]),
    )
    upper_payload = _raster_payload(
        upper_transform,
        np.vstack([np.array([4, 5, 6], dtype=np.int16), shared]),
    )
    lower_url = "https://example.test/N33.tif"
    upper_url = "https://example.test/N34.tif"
    lower = _raster_spec(
        source_id=SRTM_N33_SOURCE_ID,
        filename="N33.tif",
        url=lower_url,
        payload=lower_payload,
        expectation=_raster_expectation(lower_transform, (2, 3)),
    )
    upper = _raster_spec(
        source_id=SRTM_N34_SOURCE_ID,
        filename="N34.tif",
        url=upper_url,
        payload=upper_payload,
        expectation=_raster_expectation(upper_transform, (2, 3)),
    )
    contents = {
        "coastline.shp": b"shape payload",
        "coastline.dbf": b"attribute payload",
        "coastline.shx": b"index payload",
    }
    coastline_payload = _zip_payload(contents)
    official_url = "https://example.test/official-coastline.zip"
    fallback_url = "https://example.test/memento-coastline.zip"
    coastline = _zip_spec(
        official_url=official_url,
        fallback_url=fallback_url,
        payload=coastline_payload,
        contents=contents,
    )
    responses: dict[str, _FakeResponse | Exception] = {
        lower_url: _FakeResponse(
            lower_payload,
            headers={"Last-Modified": "Mon, 01 Jan 2018 00:00:00 GMT"},
        ),
        upper_url: _FakeResponse(upper_payload),
        official_url: _FakeResponse(b"blocked", status_code=403),
        fallback_url: _FakeResponse(
            coastline_payload,
            headers={
                "Memento-Datetime": "Fri, 30 Oct 2020 23:48:08 GMT",
                "X-Archive-Orig-Last-Modified": "Fri, 09 Aug 2019 04:08:51 GMT",
            },
        ),
    }
    return (lower, upper, coastline), responses


def test_production_sources_pin_urls_hashes_and_bytes() -> None:
    assert SRTM_N33W119.urls == (
        f"{OPEN_TOPOGRAPHY_SRTM_BASE_URL}/N33W119.tif",
    )
    assert SRTM_N33W119.expected_bytes == 1_639_890
    assert SRTM_N33W119.expected_sha256 == (
        "723e181239b96318165898261885ee3fb02b296e80399a151ad479decb599435"
    )
    assert SRTM_N34W119.urls == (
        f"{OPEN_TOPOGRAPHY_SRTM_BASE_URL}/N34W119.tif",
    )
    assert SRTM_N34W119.expected_bytes == 15_894_914
    assert SRTM_N34W119.expected_sha256 == (
        "b91b076ff94bd832b309a5cd8514b759e78052f5db0ea65c2122e4a68799ed00"
    )
    assert CENSUS_2019_COASTLINE.urls == (
        CENSUS_2019_COASTLINE_OFFICIAL_URL,
        CENSUS_2019_COASTLINE_WAYBACK_URL,
    )
    assert CENSUS_2019_COASTLINE.expected_bytes == 16_631_608
    assert CENSUS_2019_COASTLINE.expected_sha256 == (
        "10c7e252a96a46552bf6045cc46f0605f645feeab70be545fab1bac869723494"
    )
    assert {member.name for member in CENSUS_2019_COASTLINE.zip_members} == {
        "tl_2019_us_coastline.cpg",
        "tl_2019_us_coastline.dbf",
        "tl_2019_us_coastline.prj",
        "tl_2019_us_coastline.shp",
        "tl_2019_us_coastline.shp.ea.iso.xml",
        "tl_2019_us_coastline.shp.iso.xml",
        "tl_2019_us_coastline.shx",
    }


def test_offline_download_falls_back_and_writes_commit_last(tmp_path: Path) -> None:
    specs, responses = _offline_fixture()
    client = _FakeClient(responses)

    marker = download_static_sources(
        tmp_path,
        http_client=client,
        specs=specs,
        seam_pair=SRTM_LAT34_SEAM_PAIR,
    )

    assert marker == tmp_path / STATIC_SOURCES_COMMIT_MARKER
    assert [call[0] for call in client.calls] == [
        specs[0].urls[0],
        specs[1].urls[0],
        specs[2].urls[0],
        specs[2].urls[1],
    ]
    assert all(call[1] is True for call in client.calls)
    assert all((tmp_path / spec.filename).is_file() for spec in specs)
    assert not list(tmp_path.glob("*.partial"))
    assert not list(tmp_path.glob(".*.partial"))

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["state"] == "complete"
    assert payload["promoted_outputs_valid"] is True
    assert payload["source_count"] == 3
    checksum = payload.pop("commit_sha256")
    assert canonical_sha256(payload) == checksum
    coastline = payload["sources"][CENSUS_COASTLINE_SOURCE_ID]
    assert coastline["source_url_used"] == specs[2].urls[1]
    assert [attempt["status"] for attempt in coastline["attempts"]] == [
        "failed",
        "downloaded",
    ]
    assert coastline["response_headers"]["x-archive-orig-last-modified"].startswith(
        "Fri, 09 Aug 2019"
    )
    seam = payload["cross_file_checks"]["srtm_lat34_seam"]
    assert seam["different_samples"] == 0
    assert seam["sample_count"] == 3


def test_valid_cache_is_reaudited_without_network(tmp_path: Path) -> None:
    specs, responses = _offline_fixture()
    download_static_sources(
        tmp_path,
        http_client=_FakeClient(responses),
        specs=specs,
        seam_pair=SRTM_LAT34_SEAM_PAIR,
    )

    marker = download_static_sources(
        tmp_path,
        http_client=_NoNetworkClient(),
        specs=specs,
        seam_pair=SRTM_LAT34_SEAM_PAIR,
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert all(record["cache_hit"] for record in payload["sources"].values())
    assert payload["sources"][CENSUS_COASTLINE_SOURCE_ID]["source_url_used"] == (
        specs[2].urls[1]
    )


def test_bad_cache_is_replaced_only_after_valid_fallback(tmp_path: Path) -> None:
    specs, responses = _offline_fixture()
    download_static_sources(
        tmp_path,
        http_client=_FakeClient(responses),
        specs=specs,
        seam_pair=SRTM_LAT34_SEAM_PAIR,
    )
    coastline_path = tmp_path / specs[2].filename
    coastline_path.write_bytes(b"tampered")

    retry_client = _FakeClient(
        {
            specs[2].urls[0]: _FakeResponse(b"blocked", status_code=403),
            specs[2].urls[1]: responses[specs[2].urls[1]],
        }
    )
    marker = download_static_sources(
        tmp_path,
        http_client=retry_client,
        specs=specs,
        seam_pair=SRTM_LAT34_SEAM_PAIR,
    )

    assert hashlib.sha256(coastline_path.read_bytes()).hexdigest() == (
        specs[2].expected_sha256
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    record = payload["sources"][CENSUS_COASTLINE_SOURCE_ID]
    assert "Byte-count mismatch" in record["cached_file_rejection"]
    assert record["source_url_used"] == specs[2].urls[1]


def test_failed_download_withdraws_marker_and_partial_file(tmp_path: Path) -> None:
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 2.0)
    valid_payload = _raster_payload(transform, np.zeros((2, 3), dtype=np.int16))
    spec = _raster_spec(
        source_id="test_raster",
        filename="test.tif",
        url="https://example.test/test.tif",
        payload=valid_payload,
        expectation=_raster_expectation(transform, (2, 3)),
    )
    marker = tmp_path / STATIC_SOURCES_COMMIT_MARKER
    marker.write_text("stale marker", encoding="utf-8")

    with pytest.raises(StaticSourceAuditError, match="Every candidate URL failed"):
        download_static_sources(
            tmp_path,
            http_client=_FakeClient({spec.urls[0]: _FakeResponse(b"wrong bytes")}),
            specs=(spec,),
            seam_pair=None,
        )

    assert not marker.exists()
    assert not (tmp_path / spec.filename).exists()
    assert not list(tmp_path.glob(".*.partial"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("shape", (3, 3), "shape mismatch"),
        ("crs", "EPSG:3857", "CRS mismatch"),
        ("dtype", "float32", "dtype mismatch"),
        ("nodata", -9999.0, "nodata mismatch"),
        (
            "transform",
            (1.0, 0.0, 0.25, 0.0, -1.0, 2.0),
            "grid transform mismatch",
        ),
        ("area_or_point", "Point", "AREA_OR_POINT mismatch"),
    ],
)
def test_raster_validator_fails_closed_on_each_grid_invariant(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 2.0)
    path = tmp_path / "tile.tif"
    path.write_bytes(_raster_payload(transform, np.zeros((2, 3), dtype=np.int16)))
    expectation = _raster_expectation(transform, (2, 3))
    assert validate_raster(path, expectation)["shape"] == [2, 3]

    bad_expectation = replace(expectation, **{field: value})
    with pytest.raises(StaticSourceAuditError, match=message):
        validate_raster(path, bad_expectation)


def test_raster_seam_mismatch_fails_closed(tmp_path: Path) -> None:
    lower_transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 2.0)
    upper_transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 3.0)
    lower = tmp_path / "lower.tif"
    upper = tmp_path / "upper.tif"
    lower.write_bytes(
        _raster_payload(
            lower_transform,
            np.array([[1, 2, 3], [0, 0, 0]], dtype=np.int16),
        )
    )
    upper.write_bytes(
        _raster_payload(
            upper_transform,
            np.array([[0, 0, 0], [1, 9, 3]], dtype=np.int16),
        )
    )

    with pytest.raises(StaticSourceAuditError, match="shared latitude row"):
        validate_raster_seam(lower, upper)


def test_zip_validator_checks_exact_members_and_crc(tmp_path: Path) -> None:
    contents = {"coastline.shp": b"shape", "coastline.dbf": b"attributes"}
    path = tmp_path / "coastline.zip"
    path.write_bytes(_zip_payload(contents))
    expected = tuple(
        ZipMemberExpectation(
            name=name,
            bytes=len(content),
            crc32=zlib.crc32(content) & 0xFFFFFFFF,
        )
        for name, content in contents.items()
    )
    assert validate_zip(path, expected)["member_count"] == 2

    with pytest.raises(StaticSourceAuditError, match="ZIP member mismatch"):
        validate_zip(path, expected[:1])
    wrong_crc = (replace(expected[0], crc32=0), expected[1])
    with pytest.raises(StaticSourceAuditError, match="CRC mismatch"):
        validate_zip(path, wrong_crc)


def test_cli_help_never_uses_network() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "download_static_sources.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "data/raw/static" in result.stdout

