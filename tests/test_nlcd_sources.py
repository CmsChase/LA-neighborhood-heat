from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import requests
from rasterio.io import MemoryFile

from la_heat.nlcd_sources import (
    MRLC_WCS_ENDPOINT,
    NLCD_2016_IMPERVIOUS,
    NLCD_2016_LAND_COVER,
    NLCD_LA_SUBSET_SHAPE,
    NLCD_LA_SUBSET_TRANSFORM,
    NLCD_SOURCE_COMMIT_MARKER,
    NlcdSourceAuditError,
    NlcdSubsetSpec,
    download_nlcd_2016_sources,
    validate_nlcd_subset,
)
from la_heat.provenance import canonical_sha256


def _payload(product: str) -> bytes:
    data = np.zeros(NLCD_LA_SUBSET_SHAPE, dtype=np.uint8)
    if product == "land_cover":
        data[0, 0] = 11
    else:
        data[0, 0] = 127
        data[0, 1] = 100
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=data.shape[0],
            width=data.shape[1],
            count=1,
            dtype="uint8",
            crs="EPSG:5070",
            transform=NLCD_LA_SUBSET_TRANSFORM,
            nodata=0,
        ) as dataset:
            dataset.write(data, 1)
            dataset.update_tags(AREA_OR_POINT="Area")
        return memory.read()


def _spec(payload: bytes, product: str) -> NlcdSubsetSpec:
    return NlcdSubsetSpec(
        source_id=f"test_{product}",
        filename=f"test_{product}.tif",
        coverage_id=f"test__{product}",
        product=product,  # type: ignore[arg-type]
        expected_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"Content-Type": "image/tiff"}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *, chunk_size: int):  # type: ignore[no-untyped-def]
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, object]] = []

    def get(
        self,
        url: str,
        *,
        params: object,
        stream: bool,
        timeout: object,
    ) -> _Response:
        assert stream is True
        self.calls.append((url, params))
        return self.response


def test_production_pins_native_la_subset_and_scientific_nodata() -> None:
    assert NLCD_2016_LAND_COVER.expected_bytes == 7_866_492
    assert NLCD_2016_LAND_COVER.expected_sha256 == (
        "d40a7cb3dfa2009afc19114175ece977390a917cc193afbc96d89a8506af99a0"
    )
    assert NLCD_2016_IMPERVIOUS.expected_bytes == 7_866_492
    assert NLCD_2016_IMPERVIOUS.expected_sha256 == (
        "c87a3a3dd5908542261d261d00e27d07bf9254cc1be190220d76761106a61532"
    )
    assert NLCD_2016_LAND_COVER.query_parameters[-2:] == (
        ("subset", "X(-2066055,-2004585)"),
        ("subset", "Y(1418445,1498245)"),
    )


def test_validator_preserves_zero_impervious_and_rejects_tampering(tmp_path: Path) -> None:
    payload = _payload("impervious")
    spec = _spec(payload, "impervious")
    path = tmp_path / spec.filename
    path.write_bytes(payload)

    validation = validate_nlcd_subset(path, spec)

    assert validation["wcs_tiff_nodata_metadata"] == 0
    assert validation["scientific_nodata"] == 127
    path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(NlcdSourceAuditError, match="SHA-256 mismatch"):
        validate_nlcd_subset(path, spec)


def test_downloader_writes_commit_last_and_reuses_valid_cache(tmp_path: Path) -> None:
    payload = _payload("land_cover")
    spec = _spec(payload, "land_cover")
    response = _Response(payload)
    client = _Client(response)

    marker = download_nlcd_2016_sources(
        tmp_path,
        http_client=client,
        specs=(spec,),
    )

    assert marker == tmp_path / NLCD_SOURCE_COMMIT_MARKER
    assert client.calls[0][0] == MRLC_WCS_ENDPOINT
    assert response.closed
    committed = json.loads(marker.read_text(encoding="utf-8"))
    recorded = committed.pop("commit_sha256")
    assert canonical_sha256(committed) == recorded
    assert committed["sources"][spec.source_id]["validation"]["scientific_nodata"] == 0

    no_network = _Client(_Response(b"wrong"))
    download_nlcd_2016_sources(tmp_path, http_client=no_network, specs=(spec,))
    assert not no_network.calls


def test_http_failure_withdraws_old_commit(tmp_path: Path) -> None:
    payload = _payload("land_cover")
    spec = _spec(payload, "land_cover")
    marker = tmp_path / NLCD_SOURCE_COMMIT_MARKER
    marker.write_text("stale", encoding="utf-8")

    class _BadResponse(_Response):
        def raise_for_status(self) -> None:
            raise requests.HTTPError("unavailable")

    with pytest.raises(requests.HTTPError):
        download_nlcd_2016_sources(
            tmp_path,
            http_client=_Client(_BadResponse(payload)),
            specs=(spec,),
        )
    assert not marker.exists()
    assert not list(tmp_path.glob("*.partial"))
