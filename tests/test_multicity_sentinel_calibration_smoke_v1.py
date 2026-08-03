from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box

from la_heat.multicity import missing_support_calibration_evidence_v1 as evidence
from la_heat.multicity import sentinel_calibration_smoke_v1 as smoke

ROOT = Path(__file__).resolve().parents[1]


def test_mgrs_native_utm_contract_allows_houston_cross_zone_assets() -> None:
    assert smoke._mgrs_epsg("14RQT") == "EPSG:32614"
    assert smoke._mgrs_epsg("15RTN") == "EPSG:32615"
    assert smoke._mgrs_epsg("16TDM") == "EPSG:32616"


def test_candidate_selection_is_one_per_positive_native_zone_and_value_blind() -> None:
    frame = gpd.GeoDataFrame(
        {
            "item_id": ["late", "early", "zone15", "no-overlap"],
            "mgrs_tile": ["14RQT", "14RQT", "15RTN", "16TDM"],
            "acquired_utc": [
                "2025-06-02T00:00:00Z",
                "2025-06-01T00:00:00Z",
                "2025-06-03T00:00:00Z",
                "2025-06-01T00:00:00Z",
            ],
        },
        geometry=[box(0, 0, 1, 1)] * 4,
        crs="EPSG:4326",
    )
    selected = smoke._probe_candidates(
        frame,
        overlap_counts={"14RQT": 10, "15RTN": 20, "16TDM": 0},
    )
    assert [(row["native_utm_zone"], row["source_item_id"]) for row in selected] == [
        (14, "early"),
        (15, "zone15"),
    ]


def _dataset(*, crs: str, resolution: int, dtype: str) -> tuple[MemoryFile, object]:
    memory = MemoryFile()
    dataset = memory.open(
        driver="GTiff",
        width=64,
        height=64,
        count=1,
        crs=crs,
        transform=from_origin(600000, 3400000, resolution, resolution),
        dtype=dtype,
        nodata=0,
    )
    return memory, dataset


def test_native_grid_validation_uses_mgrs_crs_not_city_target_crs() -> None:
    memory, dataset = _dataset(crs="EPSG:32614", resolution=10, dtype="uint16")
    try:
        record = smoke._validate_grid(dataset, asset="B04", mgrs_tile="14RQT")
        assert record["crs"] == "EPSG:32614"
        assert record["resolution_m"] == 10.0
    finally:
        dataset.close()
        memory.close()


def test_scl_requires_native_20m_uint8() -> None:
    memory, dataset = _dataset(crs="EPSG:32615", resolution=20, dtype="uint8")
    try:
        record = smoke._validate_grid(dataset, asset="SCL", mgrs_tile="15RTN")
        assert record["dtype"] == "uint8"
        assert record["resolution_m"] == 20.0
    finally:
        dataset.close()
        memory.close()


@dataclass(frozen=True)
class _Item:
    geometry_wgs84: object


def test_probe_cell_uses_lowest_flat_eligible_cell_inside_item() -> None:
    eligible = np.array([[False, True], [True, True]])
    transform = from_origin(0, 60, 30, 30)
    item = _Item(box(0, 0, 60, 60))
    x, y, flat = smoke._probe_cell(
        item=item,
        eligible=eligible,
        transform_value=transform,
        support_crs="EPSG:4326",
        full_window_margin_m=0.0,
    )
    assert (x, y, flat) == (45.0, 45.0, 1)


class _RangeResponse:
    def __init__(
        self,
        *,
        content: bytes,
        status_code: int,
        url: str,
        headers: dict[str, str] | None = None,
        json_payload: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.history: tuple[object, ...] = ()
        self._json_payload = json_payload

    def json(self) -> dict[str, str] | None:
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size: int) -> list[bytes]:
        del chunk_size
        return [self.content]

    def close(self) -> None:
        return None


class _RangeSession:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.ranges: list[tuple[int, int]] = []

    def get(self, url: str, **kwargs: Any) -> _RangeResponse:
        if "/api/sas/v1/token/" in url:
            return _RangeResponse(
                content=b'{"token":"sig=test"}',
                status_code=200,
                url=url,
                headers={"Content-Length": "20"},
                json_payload={"token": "sig=test"},
            )
        range_header = kwargs["headers"]["Range"]
        start_text, end_text = range_header.removeprefix("bytes=").split("-")
        start, end = int(start_text), int(end_text)
        self.ranges.append((start, end))
        content = self.payload[start : end + 1]
        return _RangeResponse(
            content=content,
            status_code=206,
            url=url,
            headers={
                "Content-Length": str(len(content)),
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}",
            },
        )


def _bounded_body_client(*, maximum_total: int = 1024) -> smoke._SentinelClient:
    config = SimpleNamespace(
        raw={
            "sentinel": {
                "allowed_http_content_encodings": [
                    "identity",
                    "gzip",
                    "deflate",
                    "br",
                    "zstd",
                ],
                "limits": {
                    "maximum_requests": 16,
                    "maximum_total_download_bytes": maximum_total,
                    "maximum_product_metadata_bytes": maximum_total,
                    "maximum_range_response_bytes": maximum_total,
                    "allowed_hosts": [],
                    "allowed_stac_path": "/api/stac/v1/search",
                    "allowed_sas_path_prefix": "/api/sas/v1/token/",
                    "allowed_asset_path_prefix": "/sentinel2-l2/",
                },
            }
        }
    )
    return smoke._SentinelClient(object(), config)  # type: ignore[arg-type]


def test_compressed_http_body_bounds_encoded_and_decoded_sizes() -> None:
    content = b"x" * 100
    response = _RangeResponse(
        content=content,
        status_code=200,
        url="https://planetarycomputer.microsoft.com/api/stac/v1/search",
        headers={"Content-Length": "20", "Content-Encoding": "gzip"},
    )
    client = _bounded_body_client()

    observed = client._read_bounded_body(
        response, maximum_bytes=200, label="compressed response"
    )

    assert observed == content
    assert client.downloaded_bytes == 100


def test_identity_http_body_still_requires_exact_content_length() -> None:
    response = _RangeResponse(
        content=b"decoded",
        status_code=200,
        url="https://planetarycomputer.microsoft.com/api/stac/v1/search",
        headers={"Content-Length": "2"},
    )
    client = _bounded_body_client()

    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="disagrees with Content-Length",
    ):
        client._read_bounded_body(
            response, maximum_bytes=200, label="identity response"
        )


def test_compressed_http_body_cannot_exceed_decoded_limit() -> None:
    response = _RangeResponse(
        content=b"x" * 100,
        status_code=200,
        url="https://planetarycomputer.microsoft.com/api/stac/v1/search",
        headers={"Content-Length": "20", "Content-Encoding": "gzip"},
    )
    client = _bounded_body_client()

    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="streamed bytes exceed",
    ):
        client._read_bounded_body(
            response, maximum_bytes=50, label="compressed response"
        )


def test_rasterio_python_opener_counts_and_bounds_every_cog_range() -> None:
    array = np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=64,
            height=64,
            count=1,
            crs="EPSG:32614",
            transform=from_origin(600000, 3400000, 10, 10),
            dtype="uint16",
            nodata=0,
        ) as destination:
            destination.write(array, 1)
        payload = memory.read()

    session = _RangeSession(payload)
    config = SimpleNamespace(
        raw={
            "sentinel": {
                "allowed_http_content_encodings": [
                    "identity",
                    "gzip",
                    "deflate",
                    "br",
                    "zstd",
                ],
                "limits": {
                    "allowed_hosts": [
                        "planetarycomputer.microsoft.com",
                        "sentinel2l2a01.blob.core.windows.net",
                    ],
                    "maximum_requests": 128,
                    "maximum_total_download_bytes": 1024 * 1024,
                    "maximum_product_metadata_bytes": 1024 * 1024,
                    "maximum_range_response_bytes": 64 * 1024,
                    "allowed_stac_path": "/api/stac/v1/search",
                    "allowed_sas_path_prefix": "/api/sas/v1/token/",
                    "allowed_asset_path_prefix": "/sentinel2-l2/",
                }
            }
        }
    )
    client = smoke._SentinelClient(session, config)  # type: ignore[arg-type]
    unsigned = (
        "https://sentinel2l2a01.blob.core.windows.net/"
        "sentinel2-l2/synthetic.tif"
    )
    with rasterio.open(unsigned, opener=client.open_asset) as source:
        observed = source.read(1, window=((10, 20), (10, 20)))

    assert np.array_equal(observed, array[10:20, 10:20])
    assert session.ranges
    assert client.request_count == len(session.ranges) + 1
    assert client.downloaded_bytes == 20 + sum(
        end - start + 1 for start, end in session.ranges
    )


def test_sentinel_program_is_not_los_angeles_hardcoded_and_imports_no_results() -> None:
    source = (
        ROOT / "src/la_heat/multicity/sentinel_calibration_smoke_v1.py"
    ).read_text(encoding="utf-8")
    assert "America/Los_Angeles" not in source
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(
            (
                "la_heat.final_",
                "la_heat.model",
                "la_heat.target",
                "la_heat.feature_ablation",
            )
        )
        for name in imported_modules
    )
