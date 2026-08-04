"""Real Sentinel-2 product-metadata and native-DN calibration smoke evidence."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import geopandas as gpd
import numpy as np
import pystac
import rasterio
import requests
import shapely
from pyproj import Transformer
from rasterio.windows import Window
from shapely.geometry import mapping

from la_heat.multicity import portable_predictor_source_evidence_v1 as _source_evidence
from la_heat.multicity import source_footprints as _footprints
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.missing_support_calibration_evidence_v1 import (
    EXTERNAL_CITY_IDS,
    SENTINEL_GLOBAL_PATH,
    EvidenceConfig,
    MissingSupportCalibrationEvidenceV1Error,
    _city_geography_path,
    _city_sentinel_path,
    _city_worldcover_path,
    assert_no_secrets,
    canonical_unsigned_url,
    checkpoint_record,
    file_record,
    read_json_with_commit,
    write_manifest_no_clobber,
)
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import canonical_sha256
from la_heat.sentinel_features import decode_boa_reflectance, parse_boa_calibration
from la_heat.sentinel_inventory import (
    physical_acquisition_key,
    select_reprocessing_cohort,
    sentinel_record_from_item,
)

ALGORITHM_VERSION: Final = "external-city-sentinel-calibration-smoke-v1"
COMPLETE_STATE: Final = "complete_target_blind_sentinel_calibration_smoke"
NEXT_GATE: Final = (
    "publish_tracked_only_plan_v19_for_portable_predictor_contract_v3_decision"
)
MGRS = re.compile(r"^(?P<zone>\d{2})(?P<band>[C-HJ-NP-X])(?P<square>[A-Z]{2})$")
REFLECTANCE_ASSETS: Final = ("B02", "B03", "B04", "B08", "B8A", "B11", "B12")
ALL_ASSETS: Final = (*REFLECTANCE_ASSETS, "SCL", "product-metadata")


class _SentinelClient:
    def __init__(self, session: Any, config: EvidenceConfig) -> None:
        self.session = session
        self.config = config
        limits = config.raw["sentinel"]["limits"]
        self.allowed_hosts = {str(value).lower() for value in limits["allowed_hosts"]}
        self.maximum_requests = int(limits["maximum_requests"])
        self.maximum_total = int(limits["maximum_total_download_bytes"])
        self.maximum_xml = int(limits["maximum_product_metadata_bytes"])
        self.maximum_range = int(limits["maximum_range_response_bytes"])
        self.request_count = 0
        self.downloaded_bytes = 0
        self.gdal_asset_open_count = 0
        self._sas_tokens: dict[tuple[str, str], str] = {}

    def _count(self) -> None:
        self.request_count += 1
        if self.request_count > self.maximum_requests:
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel smoke request limit exceeded.")

    def _read_bounded_body(
        self,
        response: Any,
        *,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        length_text = response.headers.get("Content-Length")
        declared = int(length_text) if length_text is not None else None
        encoding_text = str(response.headers.get("Content-Encoding", "")).lower()
        encodings = {value.strip() for value in encoding_text.split(",") if value.strip()}
        if not encodings:
            encodings = {"identity"}
        allowed_encodings = set(self.config.raw["sentinel"]["allowed_http_content_encodings"])
        if not encodings.issubset(allowed_encodings):
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                f"{label} uses an unsupported Content-Encoding."
            )
        encoded = encodings != {"identity"}
        if declared is not None and (
            declared < 0
            or declared > maximum_bytes
            or self.downloaded_bytes + declared > self.maximum_total
        ):
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                f"{label} declared bytes exceed the evidence limit."
            )
        content = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if (
                    len(content) > maximum_bytes
                    or self.downloaded_bytes + len(content) > self.maximum_total
                ):
                    raise MissingSupportCalibrationEvidenceV1Error(
                        f"{label} streamed bytes exceed the evidence limit."
                    )
        finally:
            response.close()
        if declared is not None and not encoded and len(content) != declared:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"{label} body disagrees with Content-Length."
            )
        accounted = max(len(content), declared or 0)
        if self.downloaded_bytes + accounted > self.maximum_total:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"{label} accounted bytes exceed the evidence limit."
            )
        self.downloaded_bytes += accounted
        return bytes(content)

    def _validate_pc(self, url: str, *, stac: bool = False, sas: bool = False) -> None:
        parsed = urlsplit(url)
        limits = self.config.raw["sentinel"]["limits"]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "planetarycomputer.microsoft.com"
            or parsed.fragment
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel Planetary Computer URL is outside the allowlist."
            )
        if stac and parsed.path != limits["allowed_stac_path"]:
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel STAC path changed.")
        if sas and not parsed.path.startswith(limits["allowed_sas_path_prefix"]):
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel SAS path changed.")

    def post_stac(self, query: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bytes]:
        url = str(self.config.raw["sentinel"]["stac_api"]).rstrip("/") + "/search"
        self._validate_pc(url, stac=True)
        self._count()
        response = self.session.post(
            url,
            json=dict(query),
            stream=True,
            timeout=(20, 120),
            allow_redirects=False,
        )
        if 300 <= int(response.status_code) < 400:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel STAC redirect is prohibited.")
        response.raise_for_status()
        content = self._read_bounded_body(
            response,
            maximum_bytes=64 * 1024 * 1024,
            label="Sentinel STAC response",
        )
        payload = json.loads(content.decode("utf-8"))
        assert_no_secrets(payload, label="Sentinel STAC response")
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list) or not features:
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel STAC query returned no items.")
        if any(
            isinstance(link, dict) and link.get("rel") == "next"
            for link in payload.get("links", [])
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                "Unexpected Sentinel STAC pagination in a narrow smoke query."
            )
        return [dict(feature) for feature in features], content

    def signed_url(self, unsigned_url: str) -> str:
        canonical = canonical_unsigned_url(unsigned_url)
        parsed = urlsplit(canonical)
        limits = self.config.raw["sentinel"]["limits"]
        if parsed.hostname != "sentinel2l2a01.blob.core.windows.net" or not parsed.path.startswith(
            limits["allowed_asset_path_prefix"]
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel asset URL is outside the frozen PC container."
            )
        parts = parsed.path.lstrip("/").split("/", 1)
        if len(parts) != 2:
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel asset URL lacks a container.")
        account = parsed.hostname.split(".", 1)[0]
        container = parts[0]
        key = account, container
        if key not in self._sas_tokens:
            sas_url = (
                f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
            )
            self._validate_pc(sas_url, sas=True)
            self._count()
            response = self.session.get(
                sas_url,
                stream=True,
                timeout=(20, 60),
                allow_redirects=False,
            )
            if 300 <= int(response.status_code) < 400:
                response.close()
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Sentinel SAS redirect is prohibited."
                )
            response.raise_for_status()
            raw = self._read_bounded_body(
                response,
                maximum_bytes=1024 * 1024,
                label="Sentinel SAS response",
            )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Sentinel SAS endpoint did not return JSON."
                ) from exc
            token = payload.get("token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Sentinel SAS endpoint did not return a token."
                )
            self._sas_tokens[key] = token.lstrip("?")
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                self._sas_tokens[key],
                "",
            )
        )

    def get_xml(self, unsigned_url: str) -> bytes:
        signed = self.signed_url(unsigned_url)
        self._count()
        response = self.session.get(
            signed,
            stream=True,
            timeout=(20, 120),
            allow_redirects=False,
        )
        if 300 <= int(response.status_code) < 400:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel XML redirect is prohibited.")
        response.raise_for_status()
        content = self._read_bounded_body(
            response,
            maximum_bytes=self.maximum_xml,
            label="Sentinel product metadata",
        )
        if not content:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel product metadata size is invalid."
            )
        return content

    def get_range(
        self,
        *,
        signed_url: str,
        unsigned_url: str,
        start: int,
        end: int,
    ) -> tuple[bytes, int]:
        """Read one exact byte range with enforced count, size, and redirect gates."""

        if start < 0 or end < start or end - start + 1 > self.maximum_range:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG range is outside the byte contract."
            )
        self._count()
        response = self.session.get(
            signed_url,
            headers={"Range": f"bytes={start}-{end}"},
            stream=True,
            timeout=(20, 120),
            allow_redirects=False,
        )
        if int(response.status_code) != 206 or getattr(response, "history", ()):
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG server did not honor one redirect-free range."
            )
        response_url = str(getattr(response, "url", signed_url))
        if canonical_unsigned_url(response_url) != unsigned_url:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG response identity changed."
            )
        content_range = str(response.headers.get("Content-Range", ""))
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if (
            match is None
            or int(match.group(1)) != start
            or int(match.group(2)) != end
            or int(match.group(3)) <= end
        ):
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel COG Content-Range changed.")
        expected_length = end - start + 1
        if int(response.headers.get("Content-Length", -1)) != expected_length:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG range lacks its exact Content-Length."
            )
        if self.downloaded_bytes + expected_length > self.maximum_total:
            response.close()
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel smoke byte limit exceeded.")
        content = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > expected_length:
                    raise MissingSupportCalibrationEvidenceV1Error(
                        "Sentinel COG range body exceeded Content-Length."
                    )
        finally:
            response.close()
        if len(content) != expected_length:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG range body length changed."
            )
        self.downloaded_bytes += len(content)
        return bytes(content), int(match.group(3))

    def open_asset(self, path: str, mode: str = "rb") -> io.BytesIO | _BoundedRangeReader:
        if mode not in {"r", "rb"}:
            raise MissingSupportCalibrationEvidenceV1Error("Sentinel COG opener is read-only.")
        # Rasterio validates callable openers with this fixed local sentinel.
        if str(path) == "test":
            return io.BytesIO(b"test")
        unsigned = canonical_unsigned_url(str(path))
        if Path(urlsplit(unsigned).path).suffix.lower() not in {".tif", ".tiff"}:
            raise FileNotFoundError(path)
        signed = self.signed_url(unsigned)
        self.gdal_asset_open_count += 1
        return _BoundedRangeReader(
            client=self,
            signed_url=signed,
            unsigned_url=unsigned,
        )


class _BoundedRangeReader(io.RawIOBase):
    """Seekable HTTP-range reader used by Rasterio's Python opener."""

    def __init__(
        self,
        *,
        client: _SentinelClient,
        signed_url: str,
        unsigned_url: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._signed_url = signed_url
        self._unsigned_url = unsigned_url
        first, self._length = client.get_range(
            signed_url=signed_url,
            unsigned_url=unsigned_url,
            start=0,
            end=0,
        )
        self._first_byte = first
        self._position = 0
        self.name = unsigned_url
        self.mode = "rb"

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._length + offset
        else:
            raise ValueError(f"Unsupported seek mode: {whence}")
        if position < 0:
            raise ValueError("Cannot seek before the Sentinel COG start.")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed Sentinel COG reader.")
        if self._position >= self._length or size == 0:
            return b""
        remaining = self._length - self._position
        requested = remaining if size is None or size < 0 else min(size, remaining)
        if requested > self._client.maximum_range:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel COG reader requested an oversized range."
            )
        start = self._position
        end = start + requested - 1
        if start == 0 and end == 0:
            content = self._first_byte
        else:
            content, observed_length = self._client.get_range(
                signed_url=self._signed_url,
                unsigned_url=self._unsigned_url,
                start=start,
                end=end,
            )
            if observed_length != self._length:
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Sentinel COG length changed between ranges."
                )
        self._position += len(content)
        return content

    def readinto(self, buffer: Any) -> int:
        content = self.read(len(buffer))
        buffer[: len(content)] = content
        return len(content)


def _save_bytes_no_clobber(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Existing Sentinel smoke cache differs: {path}"
            )
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    temporary.write_bytes(content)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def _city_timezone_and_crs(config: EvidenceConfig, city_id: str) -> tuple[str, str]:
    import tomllib

    path = config.project_path(f"configs/multicity/cities/{city_id}.toml")
    with path.open("rb") as handle:
        raw = tomllib.load(handle)["city"]
    timezone = str(raw["timezone"])
    crs = str(raw["target_grid_crs"])
    return timezone, crs


def _verified_source_footprint(
    config: EvidenceConfig, city_id: str
) -> dict[str, Any]:
    experiment = config.project_path(str(config.raw["stage"]["experiment_config"]))
    if city_id == "phoenix_az":
        return _footprints.verify_city_source_footprints(experiment, city_id)
    if city_id not in {"houston_tx", "chicago_il"}:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"No frozen source-footprint verifier is registered for {city_id}."
        )
    source_config = _source_evidence._read_config(
        config.project_path(_source_evidence.CONFIG_PATH)
    )
    experiment = source_config.project_path(
        str(source_config.raw["stage"]["experiment_config"])
    )
    plan = load_multicity_plan(experiment)
    workspace = MulticityWorkspace.from_plan(plan)
    return _source_evidence._verify_new_source_footprint(
        source_config, workspace, city_id
    )


def _source_inputs(
    config: EvidenceConfig, city_id: str
) -> tuple[gpd.GeoDataFrame, dict[str, Any], gpd.GeoDataFrame, dict[str, Any]]:
    source = _verified_source_footprint(config, city_id)
    sentinel_record = source["output_tables"]["sentinel_items"]
    sentinel = gpd.read_parquet(config.project_path(str(sentinel_record["path"])))
    if (
        sentinel.empty
        or sentinel.crs is None
        or sentinel["item_id"].duplicated().any()
        or set(sentinel["collection"]) != {"sentinel-2-l2a"}
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Frozen Sentinel source metadata changed for {city_id}."
        )
    geography = read_json_with_commit(
        config.project_path(_city_geography_path(city_id)),
        label=f"{city_id} geography evidence",
    )
    boundary_record = geography["output_tables"]["city_boundary"]
    boundary = gpd.read_parquet(config.project_path(str(boundary_record["path"])))
    support = read_json_with_commit(
        config.project_path(_city_worldcover_path(city_id)),
        label=f"{city_id} WorldCover evidence",
    )
    return sentinel, source, boundary, support


def _load_eligible(
    config: EvidenceConfig, support: Mapping[str, Any]
) -> tuple[np.ndarray, np.ndarray, rasterio.Affine, str]:
    outputs = support["outputs"]
    with rasterio.open(config.project_path(str(outputs["eligible_mask_30m"]["path"]))) as source:
        eligible = source.read(1).astype(bool)
        transform_value = source.transform
        crs = source.crs.to_string()
    with rasterio.open(config.project_path(str(outputs["tract_zones_30m"]["path"]))) as source:
        zones = source.read(1)
        if source.transform != transform_value or source.crs.to_string() != crs:
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel smoke support rasters disagree."
            )
    if eligible.shape != zones.shape or not np.any(eligible & (zones > 0)):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel smoke received empty WorldCover support."
        )
    return eligible, zones, transform_value, crs


def _eligible_centers(
    eligible: np.ndarray, transform_value: rasterio.Affine
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows, columns = np.nonzero(eligible)
    xs = transform_value.c + (columns.astype(float) + 0.5) * transform_value.a
    ys = transform_value.f + (rows.astype(float) + 0.5) * transform_value.e
    flat = np.ravel_multi_index((rows, columns), eligible.shape).astype("<u8")
    return xs, ys, flat


def _tile_overlap_counts(
    items: gpd.GeoDataFrame,
    *,
    eligible: np.ndarray,
    transform_value: rasterio.Affine,
    support_crs: str,
) -> dict[str, int]:
    xs, ys, _ = _eligible_centers(eligible, transform_value)
    unique = items.sort_values(["mgrs_tile", "item_id"]).drop_duplicates("mgrs_tile")
    projected = unique.to_crs(support_crs)
    counts: dict[str, int] = {}
    for mgrs_tile, geometry in zip(
        projected["mgrs_tile"].astype(str), projected.geometry, strict=True
    ):
        counts[mgrs_tile] = int(shapely.contains_xy(geometry, xs, ys).sum())
    return counts


def _probe_candidates(
    items: gpd.GeoDataFrame,
    *,
    overlap_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    candidates = items.copy()
    candidates["mgrs_tile"] = candidates["mgrs_tile"].astype(str).str.upper()
    parsed = candidates["mgrs_tile"].map(MGRS.fullmatch)
    if parsed.isna().any():
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel source metadata contains invalid MGRS."
        )
    candidates["native_utm_zone"] = parsed.map(lambda value: int(value.group("zone")))
    candidates["eligible_cell_overlap"] = candidates["mgrs_tile"].map(overlap_counts)
    candidates = candidates.loc[candidates["eligible_cell_overlap"] > 0].copy()
    if candidates.empty:
        raise MissingSupportCalibrationEvidenceV1Error(
            "No Sentinel native UTM zone intersects eligible support."
        )
    selected: list[dict[str, Any]] = []
    for zone, group in candidates.groupby("native_utm_zone", sort=True):
        ordered = group.sort_values(
            [
                "eligible_cell_overlap",
                "acquired_utc",
                "mgrs_tile",
                "item_id",
            ],
            ascending=[False, True, True, True],
            kind="stable",
        )
        row = ordered.iloc[0]
        selected.append(
            {
                "native_utm_zone": int(zone),
                "source_item_id": str(row["item_id"]),
                "mgrs_tile": str(row["mgrs_tile"]),
                "acquired_utc": str(row["acquired_utc"]),
                "eligible_cell_overlap": int(row["eligible_cell_overlap"]),
            }
        )
    return selected


def _stac_snapshot_path(config: EvidenceConfig, city_id: str, label: str) -> Path:
    root = config.project_path(str(config.raw["sentinel"]["limits"]["working_directory"]))
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    return root / city_id / "stac" / f"{safe}.json"


def _query_exact_item(
    client: _SentinelClient,
    config: EvidenceConfig,
    *,
    city_id: str,
    item_id: str,
) -> tuple[pystac.Item, dict[str, Any]]:
    query = {"collections": ["sentinel-2-l2a"], "ids": [item_id], "limit": 10}
    features, raw = client.post_stac(query)
    if len(features) != 1 or str(features[0].get("id")) != item_id:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Exact Sentinel item lookup was not unique: {item_id}"
        )
    path = _stac_snapshot_path(config, city_id, f"item_{item_id}")
    _save_bytes_no_clobber(path, raw)
    item = pystac.Item.from_dict(features[0], preserve_dict=False)
    return item, {"query_sha256": canonical_sha256(query), "raw": file_record(config, path)}


def _query_cohort(
    client: _SentinelClient,
    config: EvidenceConfig,
    *,
    city_id: str,
    initial: Any,
    boundary: gpd.GeoDataFrame,
    analysis_crs: str,
) -> tuple[Any, dict[str, Any]]:
    key = physical_acquisition_key(initial)
    start = key.acquired_utc - timedelta(seconds=1)
    end = key.acquired_utc + timedelta(seconds=1)
    query = {
        "collections": ["sentinel-2-l2a"],
        "datetime": (
            f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}"
        ),
        "intersects": mapping(boundary.to_crs("EPSG:4326").geometry.union_all()),
        "limit": 100,
    }
    features, raw = client.post_stac(query)
    records = []
    for feature in features:
        if feature.get("collection") != "sentinel-2-l2a":
            raise MissingSupportCalibrationEvidenceV1Error(
                "Sentinel cohort query crossed collections."
            )
        record = sentinel_record_from_item(pystac.Item.from_dict(feature, preserve_dict=False))
        if physical_acquisition_key(record) == key:
            records.append(record)
    if not records:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel cohort query lost the selected physical acquisition."
        )
    cohort = select_reprocessing_cohort(
        records,
        aoi_geometry_wgs84=boundary.to_crs("EPSG:4326").geometry.union_all(),
        analysis_crs=analysis_crs,
    )
    path = _stac_snapshot_path(config, city_id, f"cohort_{key.semantic_id}")
    _save_bytes_no_clobber(path, raw)
    return cohort, {"query_sha256": canonical_sha256(query), "raw": file_record(config, path)}


def _mgrs_epsg(mgrs_tile: str) -> str:
    match = MGRS.fullmatch(mgrs_tile.upper())
    if match is None:
        raise MissingSupportCalibrationEvidenceV1Error(f"Invalid MGRS tile: {mgrs_tile}")
    zone = int(match.group("zone"))
    north = match.group("band") >= "N"
    return f"EPSG:{32600 + zone if north else 32700 + zone}"


def _window_for_probe(
    source: rasterio.DatasetReader,
    *,
    x: float,
    y: float,
    support_crs: str,
    size: int,
) -> Window:
    transformer = Transformer.from_crs(support_crs, source.crs, always_xy=True)
    source_x, source_y = transformer.transform(x, y)
    row, column = source.index(source_x, source_y)
    row_off = row - size // 2
    column_off = column - size // 2
    if (
        row_off < 0
        or column_off < 0
        or row_off + size > source.height
        or column_off + size > source.width
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel smoke window would require clipping."
        )
    return Window(column_off, row_off, size, size)


def _validate_grid(
    source: rasterio.DatasetReader,
    *,
    asset: str,
    mgrs_tile: str,
) -> dict[str, Any]:
    expected_crs = _mgrs_epsg(mgrs_tile)
    if source.crs is None or source.crs.to_string() != expected_crs:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} native CRS does not match MGRS {mgrs_tile}."
        )
    transform_value = source.transform
    if (
        not math.isclose(transform_value.b, 0.0, abs_tol=1e-9)
        or not math.isclose(transform_value.d, 0.0, abs_tol=1e-9)
        or transform_value.a <= 0
        or transform_value.e >= 0
        or not math.isclose(transform_value.a, -transform_value.e, abs_tol=1e-9)
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} is not a north-up square grid."
        )
    expected_resolution = 10.0 if asset in {"B02", "B03", "B04", "B08"} else 20.0
    if not math.isclose(transform_value.a, expected_resolution, abs_tol=1e-9):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} native resolution changed."
        )
    for edge in (transform_value.c, transform_value.f):
        if not math.isclose(
            edge / expected_resolution,
            round(edge / expected_resolution),
            abs_tol=1e-8,
        ):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Sentinel {asset} native grid phase changed."
            )
    expected_dtype = "uint8" if asset == "SCL" else "uint16"
    if source.count != 1 or source.dtypes[0] != expected_dtype:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} dtype or band count changed."
        )
    scales = tuple(float(value) for value in source.scales)
    offsets = tuple(float(value) for value in source.offsets)
    if (
        source.nodata is None
        or not math.isclose(float(source.nodata), 0.0, rel_tol=0.0, abs_tol=0.0)
        or scales != (1.0,)
        or offsets != (0.0,)
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} is not identity-encoded native DN storage."
        )
    return {
        "crs": source.crs.to_string(),
        "shape": [source.height, source.width],
        "transform": list(source.transform),
        "resolution_m": expected_resolution,
        "dtype": source.dtypes[0],
        "nodata": source.nodata,
        "scales": list(scales),
        "offsets": list(offsets),
        "native_dn_identity_storage": True,
    }


def _probe_cell(
    *,
    item: Any,
    eligible: np.ndarray,
    transform_value: rasterio.Affine,
    support_crs: str,
    full_window_margin_m: float,
) -> tuple[float, float, int]:
    xs, ys, flat = _eligible_centers(eligible, transform_value)
    geometry = gpd.GeoSeries([item.geometry_wgs84], crs="EPSG:4326").to_crs(support_crs).iloc[0]
    if full_window_margin_m < 0:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel full-window margin cannot be negative."
        )
    interior = geometry.buffer(-full_window_margin_m)
    if interior.is_empty:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Selected Sentinel item has no fixed full-window interior."
        )
    inside = shapely.contains_xy(interior, xs, ys)
    if not np.any(inside):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Selected Sentinel item has no eligible full-window probe cell."
        )
    candidates = np.flatnonzero(inside)
    chosen = candidates[np.argmin(flat[candidates])]
    return float(xs[chosen]), float(ys[chosen]), int(flat[chosen])


def _asset_extra_calibration(feature: Mapping[str, Any], asset: str) -> dict[str, Any]:
    assets = feature.get("assets")
    value = assets.get(asset) if isinstance(assets, Mapping) else None
    if not isinstance(value, Mapping):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel STAC item lost asset metadata: {asset}"
        )
    if "raster:bands" not in value:
        return {
            "availability": "not_published_by_provider_stac_item",
            "scale": None,
            "offset": None,
            "nodata": None,
        }
    bands = value.get("raster:bands")
    if not isinstance(bands, list) or len(bands) != 1 or not isinstance(bands[0], Mapping):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} has ambiguous raster:bands metadata."
        )
    band = bands[0]
    try:
        scale = float(band["scale"])
        offset = float(band["offset"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} raster:bands lacks finite scale and offset."
        ) from exc
    if not math.isfinite(scale) or not math.isfinite(offset):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel {asset} raster:bands lacks finite scale and offset."
        )
    return {
        "availability": "published_by_provider_stac_item",
        "scale": scale,
        "offset": offset,
        "nodata": band.get("nodata"),
    }


def _provider_encoding_evidence(
    asset_records: Mapping[str, Mapping[str, Any]], calibration: Any
) -> dict[str, Any]:
    expected_scale = 1.0 / calibration.quantification_value
    declarations: dict[str, bool] = {}
    matches: dict[str, bool | None] = {}
    for asset in REFLECTANCE_ASSETS:
        raster_band = asset_records[asset]["stac_raster_band"]
        availability = raster_band.get("availability")
        if availability == "not_published_by_provider_stac_item":
            declarations[asset] = False
            matches[asset] = None
            continue
        if availability != "published_by_provider_stac_item":
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Sentinel {asset} STAC calibration availability changed."
            )
        declarations[asset] = True
        try:
            observed_scale = float(raster_band["scale"])
            observed_offset = float(raster_band["offset"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Sentinel {asset} STAC calibration is incomplete."
            ) from exc
        if not math.isfinite(observed_scale) or not math.isfinite(observed_offset):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Sentinel {asset} STAC calibration is incomplete."
            )
        expected_offset = calibration.offset_by_band[asset] / calibration.quantification_value
        matches[asset] = math.isclose(
            observed_scale, expected_scale, rel_tol=0.0, abs_tol=1e-12
        ) and math.isclose(observed_offset, expected_offset, rel_tol=0.0, abs_tol=1e-12)
    if any(declarations.values()) and not all(declarations.values()):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel STAC publishes calibration for only part of the seven-band set."
        )
    all_declared = all(declarations.values())
    all_match = all_declared and all(value is True for value in matches.values())
    if not any(declarations.values()):
        comparison_status = "provider_stac_raster_calibration_not_published"
    elif all_match:
        comparison_status = "all_provider_stac_calibrations_match_product_xml"
    else:
        comparison_status = "provider_stac_calibration_mismatch"
    return {
        "decode_calibration_authority": "official_product_metadata_xml",
        "stac_values_synthesized_from_xml": False,
        "expected_stac_scale": expected_scale,
        "expected_stac_offset_by_band": {
            asset: (calibration.offset_by_band[asset] / calibration.quantification_value)
            for asset in REFLECTANCE_ASSETS
        },
        "stac_raster_band_declared_by_asset": declarations,
        "stac_raster_band_matches_xml_formula": matches,
        "all_seven_assets_declare_stac_calibration": all_declared,
        "all_seven_assets_match_xml_formula": all_match,
        "comparison_status": comparison_status,
        "native_dn_storage_header_identity_required": True,
        "interpretation_deferred_to_v3": True,
    }


def _stage_probe(
    config: EvidenceConfig,
    *,
    client: _SentinelClient,
    city_id: str,
    candidate: Mapping[str, Any],
    boundary: gpd.GeoDataFrame,
    eligible: np.ndarray,
    transform_value: rasterio.Affine,
    support_crs: str,
) -> dict[str, Any]:
    item, exact_query = _query_exact_item(
        client,
        config,
        city_id=city_id,
        item_id=str(candidate["source_item_id"]),
    )
    if item.collection_id != "sentinel-2-l2a":
        raise MissingSupportCalibrationEvidenceV1Error("Sentinel exact item crossed collections.")
    initial = sentinel_record_from_item(item)
    cohort, cohort_query = _query_cohort(
        client,
        config,
        city_id=city_id,
        initial=initial,
        boundary=boundary,
        analysis_crs=support_crs,
    )
    tile_items = [record for record in cohort.items if record.mgrs_tile == candidate["mgrs_tile"]]
    if len(tile_items) != 1:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Selected Sentinel cohort does not contain one probe-tile item."
        )
    selected = tile_items[0]
    selected_feature, selected_raw = _query_exact_item(
        client,
        config,
        city_id=city_id,
        item_id=selected.item_id,
    )
    selected_record = sentinel_record_from_item(selected_feature)
    if selected_record != selected:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel selected cohort item changed on exact lookup."
        )
    x, y, flat_index = _probe_cell(
        item=selected,
        eligible=eligible,
        transform_value=transform_value,
        support_crs=support_crs,
        full_window_margin_m=float(config.raw["sentinel"]["probe_cell_full_window_margin_m"]),
    )
    hrefs = dict(selected.asset_hrefs)
    xml = client.get_xml(hrefs["product-metadata"])
    calibration = parse_boa_calibration(xml, processing_baseline=selected.processing_baseline)
    root = config.project_path(str(config.raw["sentinel"]["limits"]["working_directory"]))
    probe_id = canonical_sha256(
        {
            "city_id": city_id,
            "native_utm_zone": candidate["native_utm_zone"],
            "item_id": selected.item_id,
            "flat_index": flat_index,
        }
    )
    probe_root = root / city_id / "probes" / probe_id
    xml_path = probe_root / "product_metadata.xml"
    _save_bytes_no_clobber(xml_path, xml)
    asset_records: dict[str, Any] = {}
    decoded_hashes: dict[str, str] = {}
    finite_counts: dict[str, int] = {}
    scl_histogram: dict[str, int] = {}
    window_size = int(config.raw["sentinel"]["native_window_shape"][0])
    if config.raw["sentinel"]["native_window_shape"] != [window_size, window_size]:
        raise MissingSupportCalibrationEvidenceV1Error("Sentinel window is not square.")
    feature_dict = selected_feature.to_dict()
    for asset in (*REFLECTANCE_ASSETS, "SCL"):
        unsigned = hrefs[asset]
        with rasterio.open(canonical_unsigned_url(unsigned), opener=client.open_asset) as source:
            schema = _validate_grid(source, asset=asset, mgrs_tile=selected.mgrs_tile)
            window = _window_for_probe(
                source,
                x=x,
                y=y,
                support_crs=support_crs,
                size=window_size,
            )
            array = source.read(1, window=window, masked=False)
        raw_dtype = "u1" if asset == "SCL" else "<u2"
        canonical_raw = np.asarray(array, dtype=raw_dtype, order="C")
        raw_path = probe_root / f"{asset}_raw.npy"
        _save_bytes_no_clobber(raw_path, _npy_bytes(canonical_raw))
        extra = _asset_extra_calibration(feature_dict, asset)
        asset_records[asset] = {
            "unsigned_url": canonical_unsigned_url(unsigned),
            "schema": schema,
            "stac_raster_band": extra,
            "window": [
                int(window.col_off),
                int(window.row_off),
                int(window.width),
                int(window.height),
            ],
            "raw_window": file_record(config, raw_path),
            "raw_array_sha256": hashlib.sha256(canonical_raw.tobytes(order="C")).hexdigest(),
        }
        if asset == "SCL":
            values, counts = np.unique(canonical_raw, return_counts=True)
            scl_histogram = {
                str(int(value)): int(count) for value, count in zip(values, counts, strict=True)
            }
            continue
        decoded = decode_boa_reflectance(
            canonical_raw,
            band=asset,
            calibration=calibration,
            nodata_dn=0,
            saturated_dn=65535,
        )
        finite = np.isfinite(decoded)
        if not finite.any():
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Fixed Sentinel probe has no finite decoded {asset} value."
            )
        canonical_decoded = np.asarray(decoded, dtype="<f8", order="C")
        finite_mask = np.packbits(finite.ravel(order="C"), bitorder="big")
        decoded_hashes[asset] = hashlib.sha256(
            finite_mask.tobytes() + canonical_decoded[finite].astype("<f8").tobytes()
        ).hexdigest()
        finite_counts[asset] = int(finite.sum())
    provider_encoding = _provider_encoding_evidence(asset_records, calibration)
    return {
        "probe_id": probe_id,
        "native_utm_zone": int(candidate["native_utm_zone"]),
        "source_candidate": dict(candidate),
        "exact_candidate_query": exact_query,
        "cohort_query": cohort_query,
        "selected_exact_query": selected_raw,
        "physical_acquisition_id": cohort.acquisition_key.semantic_id,
        "selected_item_id": selected.item_id,
        "mgrs_tile": selected.mgrs_tile,
        "processing_baseline": calibration.processing_baseline,
        "cohort_item_ids": list(cohort.item_ids),
        "cohort_union_aoi_coverage_fraction": cohort.union_aoi_coverage_fraction,
        "probe_cell": {
            "support_crs": support_crs,
            "x": x,
            "y": y,
            "flat_index": flat_index,
            "selection": "minimum_flat_eligible_cell_index_inside_selected_item",
            "full_window_margin_m": float(
                config.raw["sentinel"]["probe_cell_full_window_margin_m"]
            ),
        },
        "product_metadata": {
            **file_record(config, xml_path),
            "unsigned_url": canonical_unsigned_url(hrefs["product-metadata"]),
            "processing_baseline": calibration.processing_baseline,
            "quantification_value": calibration.quantification_value,
            "band_offsets_dn": calibration.offset_by_band,
            "calibration_sha256": calibration.sha256,
        },
        "assets": asset_records,
        "decoded_reflectance": {
            "formula": "(DN+BOA_ADD_OFFSET)/BOA_QUANTIFICATION_VALUE",
            "offset_applied_exactly_once": True,
            "finite_counts": finite_counts,
            "value_hashes": decoded_hashes,
            "indices_or_albedo_computed": False,
        },
        "provider_encoding_evidence": provider_encoding,
        "scl_histogram_audit_only": scl_histogram,
        "data_driven_candidate_fallback_used": False,
    }


def _city_manifest(
    config: EvidenceConfig,
    *,
    city_id: str,
    source: Mapping[str, Any],
    support: Mapping[str, Any],
    probes: Sequence[Mapping[str, Any]],
    plan_record: Mapping[str, Any],
    network_audit: Mapping[str, Any],
) -> dict[str, Any]:
    timezone, target_crs = _city_timezone_and_crs(config, city_id)
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_target_blind_city_sentinel_calibration_smoke",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "city_id": city_id,
        "timezone": timezone,
        "target_grid_crs": target_crs,
        "plan_authorization": dict(plan_record),
        "source_footprint": {
            "commit_sha256": source["commit_sha256"],
            "sentinel_table": source["output_tables"]["sentinel_items"],
        },
        "worldcover_support": {
            "path": _city_worldcover_path(city_id),
            "commit_sha256": support["commit_sha256"],
            "city_support_identity_sha256": support["support"]["city_support_identity_sha256"],
        },
        "selection_contract": {
            "group": "each_native_utm_zone_with_positive_eligible_cell_overlap",
            "candidate_order": (
                "eligible_cell_overlap_desc_acquired_utc_asc_mgrs_tile_asc_item_id_asc"
            ),
            "probe_cell_order": (
                "eligible_flat_index_ascending_inside_item_after_fixed_full_window_margin"
            ),
            "cloud_scl_or_dn_used_for_selection": False,
            "data_driven_fallback_allowed": False,
        },
        "probe_count": len(probes),
        "native_utm_zones": sorted(int(probe["native_utm_zone"]) for probe in probes),
        "probes": list(probes),
        "network_audit": dict(network_audit),
        "access_contract": {
            "sentinel_product_metadata_read": True,
            "small_native_dn_windows_read": True,
            "landsat_thermal_or_target_qa_values_read": False,
            "external_target_or_lst_values_read": False,
            "sentinel_indices_or_tract_features_computed": False,
            "predictor_construction_performed": False,
            "model_fit_or_prediction_performed": False,
            "final_evaluation_outputs_opened": False,
        },
    }


def _verify_city(config: EvidenceConfig, city_id: str) -> dict[str, Any]:
    path = config.project_path(_city_sentinel_path(city_id))
    payload = read_json_with_commit(path, label=f"{city_id} Sentinel smoke")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != "complete_target_blind_city_sentinel_calibration_smoke"
        or payload.get("city_id") != city_id
        or payload.get("probe_count") != len(payload.get("probes", []))
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel smoke city manifest changed: {city_id}"
        )
    network_audit = payload.get("network_audit")
    if (
        not isinstance(network_audit, Mapping)
        or int(network_audit.get("explicit_request_count", -1)) <= 0
        or int(network_audit.get("bounded_response_bytes", -1)) <= 0
        or int(network_audit.get("python_range_asset_open_count", -1)) <= 0
        or network_audit.get("all_cog_range_requests_counted") is not True
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel smoke network audit changed: {city_id}"
        )
    source_table = payload["source_footprint"]["sentinel_table"]
    source_path = config.project_path(str(source_table["path"]))
    if file_record(config, source_path) != {
        key: source_table[key] for key in ("path", "bytes", "sha256")
    }:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Sentinel frozen source table changed: {city_id}"
        )
    for probe in payload["probes"]:
        for query_name in (
            "exact_candidate_query",
            "cohort_query",
            "selected_exact_query",
        ):
            record = probe[query_name]["raw"]
            query_path = config.project_path(str(record["path"]))
            if file_record(config, query_path) != record:
                raise MissingSupportCalibrationEvidenceV1Error(
                    f"Sentinel STAC snapshot changed: {city_id}/{query_name}"
                )
        metadata = probe["product_metadata"]
        metadata_path = config.project_path(str(metadata["path"]))
        if file_record(config, metadata_path) != {
            key: metadata[key] for key in ("path", "bytes", "sha256")
        }:
            raise MissingSupportCalibrationEvidenceV1Error(f"Sentinel XML changed: {city_id}")
        for asset in (*REFLECTANCE_ASSETS, "SCL"):
            record = probe["assets"][asset]["raw_window"]
            raw_path = config.project_path(str(record["path"]))
            if file_record(config, raw_path) != record:
                raise MissingSupportCalibrationEvidenceV1Error(
                    f"Sentinel smoke raw window changed: {city_id}/{asset}"
                )
    return payload


def _verify_global(config: EvidenceConfig) -> dict[str, Any]:
    path = config.project_path(SENTINEL_GLOBAL_PATH)
    payload = read_json_with_commit(path, label="Sentinel smoke terminal")
    if (
        payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or set(payload.get("cities", {})) != set(EXTERNAL_CITY_IDS)
    ):
        raise MissingSupportCalibrationEvidenceV1Error("Sentinel smoke terminal changed.")
    total = 0
    requests_total = 0
    bytes_total = 0
    opens_total = 0
    for city_id in EXTERNAL_CITY_IDS:
        city = _verify_city(config, city_id)
        total += int(city["probe_count"])
        requests_total += int(city["network_audit"]["explicit_request_count"])
        bytes_total += int(city["network_audit"]["bounded_response_bytes"])
        opens_total += int(city["network_audit"]["python_range_asset_open_count"])
        if payload["cities"][city_id]["commit_sha256"] != city["commit_sha256"]:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Sentinel smoke terminal lost {city_id}."
            )
    if total != int(payload["total_probe_groups"]):
        raise MissingSupportCalibrationEvidenceV1Error("Sentinel smoke total probe count changed.")
    expected_audit = {
        "explicit_request_count": requests_total,
        "bounded_response_bytes": bytes_total,
        "python_range_asset_open_count": opens_total,
        "all_cog_range_requests_counted": True,
        "redirect_fallback_allowed": False,
        "signed_urls_persisted": False,
    }
    if payload.get("network_audit") != expected_audit:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel smoke global network audit changed."
        )
    return payload


def stage_sentinel_calibration_smoke_v1(
    config: EvidenceConfig,
    *,
    plan_record: Mapping[str, Any],
    worldcover_terminal: Mapping[str, Any],
    session: Any | None = None,
) -> dict[str, Any]:
    """Stage or authenticate all external-city real Sentinel calibration probes."""

    del worldcover_terminal
    global_path = config.project_path(SENTINEL_GLOBAL_PATH)
    if global_path.is_file():
        return _verify_global(config)
    client = _SentinelClient(requests.Session() if session is None else session, config)
    city_payloads: dict[str, dict[str, Any]] = {}
    for city_id in EXTERNAL_CITY_IDS:
        manifest_path = config.project_path(_city_sentinel_path(city_id))
        if manifest_path.is_file():
            city_payloads[city_id] = _verify_city(config, city_id)
            audit = city_payloads[city_id]["network_audit"]
            client.request_count += int(audit["explicit_request_count"])
            client.downloaded_bytes += int(audit["bounded_response_bytes"])
            client.gdal_asset_open_count += int(audit["python_range_asset_open_count"])
            if (
                client.request_count > client.maximum_requests
                or client.downloaded_bytes > client.maximum_total
            ):
                raise MissingSupportCalibrationEvidenceV1Error(
                    "Resumed Sentinel audit exceeds the stage network budget."
                )
            continue
        request_start = client.request_count
        byte_start = client.downloaded_bytes
        asset_open_start = client.gdal_asset_open_count
        items, source, boundary, support = _source_inputs(config, city_id)
        eligible, _, transform_value, support_crs = _load_eligible(config, support)
        overlaps = _tile_overlap_counts(
            items,
            eligible=eligible,
            transform_value=transform_value,
            support_crs=support_crs,
        )
        candidates = _probe_candidates(items, overlap_counts=overlaps)
        probes = [
            _stage_probe(
                config,
                client=client,
                city_id=city_id,
                candidate=candidate,
                boundary=boundary,
                eligible=eligible,
                transform_value=transform_value,
                support_crs=support_crs,
            )
            for candidate in candidates
        ]
        payload = _city_manifest(
            config,
            city_id=city_id,
            source=source,
            support=support,
            probes=probes,
            plan_record=plan_record,
            network_audit={
                "explicit_request_count": client.request_count - request_start,
                "bounded_response_bytes": client.downloaded_bytes - byte_start,
                "python_range_asset_open_count": (client.gdal_asset_open_count - asset_open_start),
                "all_cog_range_requests_counted": True,
                "redirect_fallback_allowed": False,
                "signed_urls_persisted": False,
            },
        )
        write_manifest_no_clobber(payload, manifest_path)
        city_payloads[city_id] = _verify_city(config, city_id)
    total = sum(int(payload["probe_count"]) for payload in city_payloads.values())
    limits = config.raw["sentinel"]["limits"]
    if (
        not int(limits["expected_minimum_probe_groups"])
        <= total
        <= int(limits["maximum_probe_groups"])
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel contributing native-zone count left its preregistered range."
        )
    aggregate_audit = {
        "explicit_request_count": sum(
            int(payload["network_audit"]["explicit_request_count"])
            for payload in city_payloads.values()
        ),
        "bounded_response_bytes": sum(
            int(payload["network_audit"]["bounded_response_bytes"])
            for payload in city_payloads.values()
        ),
        "python_range_asset_open_count": sum(
            int(payload["network_audit"]["python_range_asset_open_count"])
            for payload in city_payloads.values()
        ),
    }
    if aggregate_audit != {
        "explicit_request_count": client.request_count,
        "bounded_response_bytes": client.downloaded_bytes,
        "python_range_asset_open_count": client.gdal_asset_open_count,
    }:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Sentinel resumed network audit does not conserve totals."
        )
    global_payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "plan_authorization": dict(plan_record),
        "cities": {
            city_id: checkpoint_record(config, config.project_path(_city_sentinel_path(city_id)))
            for city_id in EXTERNAL_CITY_IDS
        },
        "total_probe_groups": total,
        "network_audit": {
            **aggregate_audit,
            "all_cog_range_requests_counted": True,
            "redirect_fallback_allowed": False,
            "signed_urls_persisted": False,
        },
        "all_contributing_external_native_utm_zones_tested": True,
        "all_seven_reflectance_assets_and_scl_tested_per_probe": True,
        "sentinel_indices_or_tract_features_computed": False,
        "predictor_build_authorized": False,
        "external_target_or_qa_values_read": False,
        "next_gate": NEXT_GATE,
    }
    write_manifest_no_clobber(global_payload, global_path)
    return _verify_global(config)
