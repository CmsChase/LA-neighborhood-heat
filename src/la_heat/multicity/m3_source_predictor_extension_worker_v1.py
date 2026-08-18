"""One-task-at-a-time worker for the source predictor extension.

The engine authenticates the append-only permit before constructing an
adapter.  Network methods are callable only in ``online_acquisition``;
predictor table reads and assembly are callable only after the acquisition
completion has been authenticated with network and href reads closed.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol
from urllib.parse import urlsplit

from la_heat.model_run_queue import LeaseLostError, ModelRunQueue
from la_heat.multicity.m3_source_development_worker import _Heartbeat
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    ALGORITHM_VERSION,
    AUTHORIZATION_PATH,
    CALENDAR_FEATURES,
    CONTEXT_FEATURES,
    DAYMET_FEATURES,
    DEFAULT_CONFIG,
    EXTENSION_CITY_IDS,
    FEATURE_NAMES,
    REQUIRED_COLUMNS,
    SENTINEL_FEATURES,
    SOURCE_CITY_IDS,
    STATIC_FEATURES,
    M3SourcePredictorExtensionError,
    PredictorExtensionSettings,
    _file_record,
    _read_committed,
    _record_path,
    _static_manifest_from_permit,
    _with_commit,
    _write_exclusive,
    authenticate_m3_source_predictor_extension_authorization,
    authenticate_source_predictor_acquisition_completion,
    authenticate_source_predictors_46_completion,
    authenticated_city_centroid_latitude,
    authenticated_static_tract_geoids,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import (
    OFFLINE_KINDS,
    OFFLINE_PHASE,
    ONLINE_KINDS,
    ONLINE_PHASE,
    PHASES,
    active_predictor_kind,
    initialize_source_predictor_runtime,
    source_predictor_run_id,
    source_predictor_runtime_status,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    atomic_text,
    canonical_sha256,
    sha256_file,
)

DAYMET_VARIABLES: Final = ("dayl", "prcp", "srad", "tmax", "tmin", "vp")
EXTENSION_COMPLETE_NAME: Final = "EXTENSION_46_COMPLETE.json"
CITY_COMPLETE_NAME: Final = "CITY_SOURCE_PREDICTORS_46_COMPLETE.json"
PLANETARY_COMPUTER_STAC_API: Final = "https://planetarycomputer.microsoft.com/api/stac/v1"


class M3SourcePredictorWorkerError(RuntimeError):
    """Raised when worker phase, concurrency, or immutable state changes."""


class M3SourcePredictorCompatibilityError(M3SourcePredictorWorkerError):
    """A permanent fail-closed blocker for an unauthenticated builder output."""


class M3SourcePredictorCredentialRequiredError(M3SourcePredictorWorkerError):
    """Anonymous official access failed and no in-memory fallback token exists."""


def _official_sentinel_url(
    value: object,
    host_patterns: tuple[str, ...],
    *,
    label: str,
) -> str:
    """Fail closed unless one HTTPS URL has an approved host and no authority tricks."""

    if not isinstance(value, str) or not value:
        raise M3SourcePredictorCompatibilityError(f"{label} URL is missing.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise M3SourcePredictorCompatibilityError(f"{label} URL authority is invalid.") from error
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path
        or parsed.fragment
    ):
        raise M3SourcePredictorCompatibilityError(
            f"{label} URL must be HTTPS without userinfo, port, or fragment."
        )
    normalized = host.rstrip(".").lower()
    allowed = any(
        normalized == pattern
        if not pattern.startswith("*.")
        else normalized.endswith("." + pattern[2:]) and normalized != pattern[2:]
        for pattern in host_patterns
    )
    if not allowed:
        raise M3SourcePredictorCompatibilityError(
            f"{label} host is outside the authorized Sentinel whitelist."
        )
    return value


class PredictorExtensionAdapter(Protocol):
    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


AdapterFactory = Callable[
    [PredictorExtensionSettings, Mapping[str, Any], str], PredictorExtensionAdapter
]


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_predictor_worker(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    try:
        _lock(handle)
    except OSError as error:
        handle.close()
        raise M3SourcePredictorWorkerError(
            "Another source predictor worker owns the lock."
        ) from error
    try:
        yield
    finally:
        try:
            _unlock(handle)
        finally:
            handle.close()


def _write_or_authenticate(payload: Mapping[str, Any], path: Path) -> dict[str, Any]:
    expected = dict(payload)
    if path.is_file():
        observed = _read_committed(path, label=path.name)
        if observed != expected:
            raise M3SourcePredictorCompatibilityError(
                f"Existing append-only artifact drifted: {path}"
            )
        return observed
    _write_exclusive(expected, path)
    return expected


def _parquet_record(root: Path, path: Path, frame: Any) -> dict[str, Any]:
    return {
        **_file_record(root, path),
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
        "semantic_sha256": canonical_sha256(
            frame.sort_values(["city_id", "target_date", "tract_geoid"], kind="stable").to_dict(
                "records"
            )
        ),
    }


class SafeExistingBuilderAdapter:
    """Concrete safe reuse/assembly plus fail-closed acquisition adapters.

    Daymet metadata and subsets use the official CMR/direct-DAP route.  The
    versioned Sentinel inventory/value adapter is intentionally accepted only
    through a committed cache under this extension root; it never redirects
    the legacy hard-coded builder into old predictor directories.
    """

    def __init__(
        self,
        settings: PredictorExtensionSettings,
        permit: Mapping[str, Any],
        phase: str,
    ) -> None:
        self.settings = settings
        self.permit = dict(permit)
        self.phase = phase
        self.run_id = source_predictor_run_id(permit)

    def _extension_dates(self, city_id: str) -> tuple[str, ...]:
        matches = [
            row
            for row in self.permit["key_universe"]["extension_cities"]
            if row["city_id"] == city_id
        ]
        if len(matches) != 1:
            raise M3SourcePredictorWorkerError("Extension city key universe changed.")
        return tuple(matches[0]["target_dates"])

    def _all_dates(self, city_id: str) -> tuple[str, ...]:
        matches = [
            row
            for row in self.permit["key_universe"]["all_source_cities"]
            if row["city_id"] == city_id
        ]
        if len(matches) != 1:
            raise M3SourcePredictorWorkerError("Source city key universe changed.")
        return tuple(matches[0]["target_dates"])

    def _source_footprint(self, city_id: str) -> dict[str, Any]:
        path = self.settings.root / (
            f"manifests/multicity/cities/{city_id}/source_footprints/SOURCE_FOOTPRINTS.json"
        )
        payload = _read_committed(path, label=f"{city_id} source footprints")
        records = self.permit.get("inputs", {}).get("extension_source_footprints")
        if not isinstance(records, list):
            raise M3SourcePredictorWorkerError("Source-footprint permit records changed.")
        matches = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise M3SourcePredictorWorkerError("Source-footprint permit record changed.")
            recorded_path = _record_path(
                self.settings.root,
                record,
                label=f"Source footprint {index}",
            )
            if recorded_path == path:
                matches.append(record)
        access = payload.get("access_contract", {})
        if (
            len(matches) != 1
            or payload.get("commit_sha256") != matches[0].get("commit_sha256")
            or payload.get("city", {}).get("id") != city_id
            or payload.get("city", {}).get("target_values_status") != "sealed"
            or access.get("landsat_thermal_values_read") is not False
            or access.get("landsat_target_qa_values_read") is not False
            or access.get("external_lst_values_read") is not False
        ):
            raise M3SourcePredictorWorkerError("Source footprint seal changed.")
        return payload

    def freeze_key_universe(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload.get("key_universe_sha256") != self.permit["key_universe"]["key_universe_sha256"]:
            raise M3SourcePredictorWorkerError("Key freeze payload changed.")
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_predictor_key_universe_frozen",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "key_universe": self.permit["key_universe"],
                "predictor_or_target_values_read": False,
                "blind_test_city_accessed": False,
            }
        )
        path = self.settings.component_root / "SOURCE_KEY_UNIVERSE.json"
        observed = _write_or_authenticate(marker, path)
        return {"state": observed["state"], "files": [_file_record(self.settings.root, path)]}

    def authenticate_static_reuse(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        city_id = str(payload.get("city_id", ""))
        if city_id not in EXTENSION_CITY_IDS:
            raise M3SourcePredictorWorkerError("Static reuse city changed.")
        geoids = authenticated_static_tract_geoids(
            self.settings.root,
            self.permit,
            city_id,
            config_path=self.settings.config_path,
        )
        return {
            "state": "authenticated_static_reuse",
            "city_id": city_id,
            "tract_count": len(geoids),
            "tract_geoid_set_sha256": canonical_sha256(geoids),
            "files": [],
        }

    def acquire_daymet_metadata(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.phase != ONLINE_PHASE:
            raise M3SourcePredictorWorkerError("Daymet metadata requires online phase.")
        city_id = str(payload.get("city_id", ""))
        year = int(payload.get("year", -1))
        if city_id not in EXTENSION_CITY_IDS or year not in range(2020, 2025):
            raise M3SourcePredictorWorkerError("Daymet metadata task changed.")
        destination = (
            self.settings.acquisition_root / "daymet" / city_id / str(year) / "GRANULES.json"
        )
        if destination.is_file():
            existing = _read_committed(destination, label="Daymet granules")
            if (
                existing.get("city_id") != city_id
                or existing.get("year") != year
                or tuple(row["variable"] for row in existing.get("granules", ()))
                != DAYMET_VARIABLES
            ):
                raise M3SourcePredictorCompatibilityError("Daymet metadata cache drifted.")
            return {
                "state": existing["state"],
                "files": [_file_record(self.settings.root, destination)],
            }
        import requests

        from la_heat.daymet_grid import (
            DAYMET_CMR_COLLECTION_ID,
            DAYMET_CMR_GRANULES_URL,
        )
        from la_heat.multicity.source_footprints import fetch_daymet_granule_metadata

        footprint = self._source_footprint(city_id)
        bbox = footprint["geography_input"]["bbox_wgs84"]
        with requests.Session() as session:
            frame, _raw, query = fetch_daymet_granule_metadata(
                session,
                endpoint=DAYMET_CMR_GRANULES_URL,
                collection_concept_id=DAYMET_CMR_COLLECTION_ID,
                year=year,
                variables=DAYMET_VARIABLES,
                bbox_wgs84=bbox,
            )
        rows = [
            {
                "concept_id": str(row.concept_id),
                "title": str(row.title),
                "variable": str(row.variable),
                "year": int(row.year),
                "size_mb": float(row.size_mb),
                "updated_at": None if row.updated_at is None else str(row.updated_at),
            }
            for row in frame.sort_values("variable", kind="stable").itertuples(index=False)
        ]
        by_variable = {row["variable"]: row for row in rows}
        ordered = [by_variable[variable] for variable in DAYMET_VARIABLES]
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "daymet_granules_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "city_id": city_id,
                "year": year,
                "granules": ordered,
                "query_sha256": query["query_sha256"],
                "official_cmr_http_status": query["http_status"],
                "urls_or_credentials_persisted": False,
                "target_or_landsat_values_read": False,
            }
        )
        _write_or_authenticate(marker, destination)
        return {"state": marker["state"], "files": [_file_record(self.settings.root, destination)]}

    def _stream_anonymous_daymet(self, url: str, destination: Path) -> bool:
        import requests

        temporary = destination.with_suffix(destination.suffix + ".partial")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=(30, 900)) as response:
            if response.status_code in {401, 403}:
                return False
            response.raise_for_status()
            maximum = 1_000_000_000
            written = 0
            try:
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > maximum:
                            raise M3SourcePredictorWorkerError(
                                "Anonymous Daymet subset exceeded 1 GB."
                            )
                        handle.write(chunk)
                if written == 0:
                    raise M3SourcePredictorWorkerError("Anonymous Daymet response is empty.")
                with temporary.open("rb") as handle:
                    prefix = handle.read(8)
                signatures = (
                    b"CDF\x01",
                    b"CDF\x02",
                    b"CDF\x05",
                    b"\x89HDF\r\n\x1a\n",
                )
                if not any(prefix.startswith(signature) for signature in signatures):
                    raise M3SourcePredictorWorkerError("Anonymous Daymet response is not NetCDF.")
                temporary.replace(destination)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        return True

    def acquire_daymet_subset(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.phase != ONLINE_PHASE:
            raise M3SourcePredictorWorkerError("Daymet subset requires online phase.")
        city_id = str(payload.get("city_id", ""))
        year = int(payload.get("year", -1))
        variable = str(payload.get("variable", ""))
        if (
            city_id not in EXTENSION_CITY_IDS
            or year not in range(2020, 2025)
            or variable not in DAYMET_VARIABLES
        ):
            raise M3SourcePredictorWorkerError("Daymet subset task changed.")
        directory = self.settings.acquisition_root / "daymet" / city_id / str(year)
        metadata = _read_committed(directory / "GRANULES.json", label="Daymet granules")
        matches = [row for row in metadata["granules"] if row["variable"] == variable]
        if len(matches) != 1:
            raise M3SourcePredictorWorkerError("Daymet granule identity changed.")
        destination = directory / f"{variable}.nc"
        footprint = self._source_footprint(city_id)
        window = footprint["source_families"]["daymet_cells"]["window"]
        y_indices = tuple(window["y_indices_inclusive"])
        x_indices = tuple(window["x_indices_inclusive"])
        bbox = footprint["geography_input"]["bbox_wgs84"]
        from la_heat.daymet_grid import (
            DAYMET_CMR_COLLECTION_ID,
            DaymetGranule,
            authenticated_netcdf_download,
            build_daymet_direct_subset_url,
            inspect_daymet_netcdf,
            load_earthdata_bearer_token,
            validate_daymet_direct_subset_spec,
        )

        row = matches[0]
        title = str(row["title"])
        opendap_url = (
            "https://opendap.earthdata.nasa.gov/collections/"
            f"{DAYMET_CMR_COLLECTION_ID}/granules/{title}"
        )
        granule = DaymetGranule(
            concept_id=str(row["concept_id"]),
            title=title,
            variable=variable,
            year=year,
            size_mb=float(row["size_mb"]),
            https_url="https://data.ornldaac.earthdata.nasa.gov/Daymet_Daily_V4R1/data/",
            opendap_url=opendap_url,
            updated_at=row.get("updated_at"),
        )
        url = build_daymet_direct_subset_url(granule, y_indices=y_indices, x_indices=x_indices)

        def validate_destination() -> None:
            spec = inspect_daymet_netcdf(
                destination,
                variable=variable,
                year=year,
                final_test_year=2025,
            )
            validate_daymet_direct_subset_spec(
                spec,
                y_indices=y_indices,
                x_indices=x_indices,
                bbox_wgs84=bbox,
            )

        if destination.is_file():
            try:
                validate_destination()
            except (OSError, ValueError):
                destination.unlink()
        downloaded = False
        if not destination.is_file():
            anonymous = self._stream_anonymous_daymet(url, destination)
            if not anonymous:
                try:
                    credential = load_earthdata_bearer_token()
                except PermissionError as error:
                    raise M3SourcePredictorCredentialRequiredError(
                        "Official anonymous Daymet DAP returned 401/403. Set exactly one "
                        "EARTHDATA_TOKEN, NASA_EARTHDATA_TOKEN, or EDL_TOKEN environment "
                        "variable for this process; it will not be persisted."
                    ) from error
                authenticated_netcdf_download(url, destination, credential=credential)
            downloaded = True
        try:
            validate_destination()
        except (OSError, ValueError):
            if downloaded:
                destination.unlink(missing_ok=True)
            raise
        record = {
            **_file_record(self.settings.root, destination),
            "kind": "daymet_subset",
            "city_id": city_id,
            "year": year,
            "variable": variable,
        }
        return {"state": "daymet_subset_complete", "files": [record]}

    def _existing_versioned_result(
        self,
        city_id: str,
        marker_name: str,
        *,
        state: str,
    ) -> Mapping[str, Any]:
        marker_path = self.settings.acquisition_root / "sentinel" / city_id / marker_name
        if not marker_path.is_file():
            raise M3SourcePredictorCompatibilityError(
                "No authenticated versioned Sentinel extension adapter output exists. "
                "The legacy builder is hard-coded to old roots and is refused."
            )
        marker = _read_committed(marker_path, label=f"{city_id} Sentinel cache")
        if (
            marker.get("state") != state
            or marker.get("authorization_commit_sha256") != self.permit["commit_sha256"]
            or marker.get("city_id") != city_id
            or marker.get("target_dates_sha256") != canonical_sha256(self._extension_dates(city_id))
            or marker.get("credentials_or_signed_urls_persisted") is not False
            or marker.get("target_or_landsat_values_read") is not False
        ):
            raise M3SourcePredictorCompatibilityError("Sentinel extension marker drifted.")
        files = marker.get("files")
        if not isinstance(files, list) or not files:
            raise M3SourcePredictorCompatibilityError("Sentinel extension files are absent.")
        for record in files:
            if not isinstance(record, Mapping):
                raise M3SourcePredictorCompatibilityError("Sentinel file record drifted.")
            path = self.settings.root / str(record.get("path", ""))
            if (
                not path.resolve().is_relative_to(self.settings.acquisition_root)
                or not path.is_file()
                or path.stat().st_size != record.get("bytes")
                or sha256_file(path) != record.get("sha256")
            ):
                raise M3SourcePredictorCompatibilityError("Sentinel file lock drifted.")
        return {
            "state": state,
            "commit_sha256": marker["commit_sha256"],
            "files": [*files, _file_record(self.settings.root, marker_path)],
        }

    def build_sentinel_inventory(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        city_id = str(payload.get("city_id", ""))
        if city_id not in EXTENSION_CITY_IDS or self.phase != ONLINE_PHASE:
            raise M3SourcePredictorWorkerError("Sentinel inventory task changed.")
        if payload.get("target_dates_sha256") != canonical_sha256(self._extension_dates(city_id)):
            raise M3SourcePredictorWorkerError("Sentinel target dates changed.")
        marker_path = (
            self.settings.acquisition_root / "sentinel" / city_id / "INVENTORY_COMPLETE.json"
        )
        if not marker_path.is_file():
            self._build_sentinel_inventory(city_id, marker_path)
        return self._existing_versioned_result(
            city_id, "INVENTORY_COMPLETE.json", state="sentinel_inventory_complete"
        )

    def _build_sentinel_inventory(self, city_id: str, marker_path: Path) -> None:
        from datetime import UTC, date, datetime, timedelta
        from datetime import time as datetime_time
        from zoneinfo import ZoneInfo

        import geopandas as gpd
        import pystac_client
        import shapely

        from la_heat.multicity.portable_sentinel_inventory import (
            _membership_frame,
            _selected_acquisitions_frame,
            _selected_items_frame,
            _snapshot_filename,
            _snapshot_text,
            build_city_window_membership,
        )
        from la_heat.sentinel_inventory import (
            SENTINEL_COLLECTION,
            canonical_stac_item_snapshot,
            query_sentinel_items,
            select_all_reprocessing_cohorts,
            sentinel_inventory_semantic_sha256,
            sentinel_record_from_item,
        )

        footprint = self._source_footprint(city_id)
        timezone = str(footprint["city"]["timezone"])
        zone = ZoneInfo(timezone)
        target_dates = tuple(date.fromisoformat(value) for value in self._extension_dates(city_id))
        windows = sorted(
            (target - timedelta(days=60), target - timedelta(days=1)) for target in target_dates
        )
        merged: list[tuple[date, date]] = []
        for start, stop in windows:
            if not merged or start > merged[-1][1] + timedelta(days=1):
                merged.append((start, stop))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], stop))

        geography_path = self.settings.root / (
            f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
        )
        geography = _read_committed(geography_path, label=f"{city_id} geography")
        boundary_record = geography.get("output_tables", {}).get("city_boundary")
        if not isinstance(boundary_record, Mapping):
            raise M3SourcePredictorWorkerError("City boundary record changed.")
        boundary_path = self.settings.root / str(boundary_record.get("path", ""))
        if (
            not boundary_path.is_file()
            or boundary_path.stat().st_size != boundary_record.get("bytes")
            or sha256_file(boundary_path) != boundary_record.get("sha256")
        ):
            raise M3SourcePredictorWorkerError("City boundary file lock changed.")
        boundary = gpd.read_parquet(boundary_path)
        if boundary.empty or boundary.crs is None:
            raise M3SourcePredictorWorkerError("City boundary geometry changed.")
        aoi = shapely.union_all(boundary.to_crs("EPSG:4326").geometry.to_numpy())
        if aoi.is_empty or not aoi.is_valid:
            raise M3SourcePredictorWorkerError("City boundary union changed.")
        context = authenticated_city_centroid_latitude(
            self.settings.root,
            self.permit,
            city_id,
            config_path=self.settings.config_path,
        )
        stac_api = _official_sentinel_url(
            PLANETARY_COMPUTER_STAC_API,
            self.settings.official_sentinel_hosts,
            label="Planetary Computer STAC API",
        )
        client = pystac_client.Client.open(stac_api)
        item_by_id: dict[str, Any] = {}
        snapshot_by_id: dict[str, dict[str, Any]] = {}
        for start, stop in merged:
            start_utc = datetime.combine(start, datetime_time.min, tzinfo=zone).astimezone(UTC)
            stop_utc = datetime.combine(
                stop + timedelta(days=1), datetime_time.min, tzinfo=zone
            ).astimezone(UTC)
            interval = (
                start_utc.isoformat().replace("+00:00", "Z")
                + "/"
                + stop_utc.isoformat().replace("+00:00", "Z")
            )
            for item in query_sentinel_items(
                client,
                intersects=aoi,
                datetime_interval=interval,
                collection=SENTINEL_COLLECTION,
            ):
                for asset_name, asset in item.assets.items():
                    _official_sentinel_url(
                        getattr(asset, "href", None),
                        self.settings.official_sentinel_hosts,
                        label=f"Sentinel item {item.id} asset {asset_name}",
                    )
                snapshot = canonical_stac_item_snapshot(item)
                item_id = str(snapshot["id"])
                text = _snapshot_text(snapshot)
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if item_id in snapshot_by_id and snapshot_by_id[item_id]["sha256"] != sha:
                    raise M3SourcePredictorWorkerError(
                        "Conflicting canonical Sentinel snapshots share an item ID."
                    )
                snapshot_by_id[item_id] = {
                    "filename": _snapshot_filename(item_id),
                    "sha256": sha,
                    "bytes": len(text.encode("utf-8")),
                    "text": text,
                }
                item_by_id[item_id] = item
        records = tuple(
            sentinel_record_from_item(item_by_id[item_id]) for item_id in sorted(item_by_id)
        )
        candidates = select_all_reprocessing_cohorts(
            records,
            aoi_geometry_wgs84=aoi,
            analysis_crs=str(context["target_grid_crs"]),
        )
        memberships = build_city_window_membership(
            target_dates,
            candidates,
            timezone=timezone,
        )
        retained = {membership.acquisition_key for membership in memberships}
        selections = tuple(
            selection for selection in candidates if selection.acquisition_key in retained
        )
        if not selections:
            raise M3SourcePredictorWorkerError(
                f"No Sentinel acquisition covers {city_id} d-60:d-1 windows."
            )
        selected_ids = {item.item_id for selection in selections for item in selection.items}
        selected_snapshots = {item_id: snapshot_by_id[item_id] for item_id in sorted(selected_ids)}
        directory = marker_path.parent
        raw_directory = directory / "stac"
        for item_id, snapshot in selected_snapshots.items():
            path = raw_directory / str(snapshot["filename"])
            if path.is_file() and sha256_file(path) != snapshot["sha256"]:
                raise M3SourcePredictorCompatibilityError(f"Sentinel snapshot drifted: {item_id}")
            if not path.is_file():
                atomic_text(str(snapshot["text"]), path)
        acquisitions = _selected_acquisitions_frame(city_id, timezone, selections)
        items = _selected_items_frame(city_id, timezone, selections, selected_snapshots)
        membership = _membership_frame(city_id, memberships)
        csv_frames = {
            "selected_acquisitions.csv": acquisitions,
            "selected_items.csv": items,
            "target_window_membership.csv": membership,
        }
        files: list[dict[str, Any]] = []
        for name, frame in csv_frames.items():
            path = directory / name
            atomic_csv(frame, path)
            files.append(_file_record(self.settings.root, path))
        for snapshot in selected_snapshots.values():
            files.append(
                _file_record(self.settings.root, raw_directory / str(snapshot["filename"]))
            )
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "sentinel_inventory_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "city_id": city_id,
                "timezone": timezone,
                "target_dates": list(self._extension_dates(city_id)),
                "target_dates_sha256": canonical_sha256(self._extension_dates(city_id)),
                "window_days_before_target": [60, 1],
                "global_cloud_cover_filter": False,
                "selected_physical_acquisition_count": len(selections),
                "selected_item_count": len(items),
                "membership_count": len(membership),
                "inventory_semantic_sha256": sentinel_inventory_semantic_sha256(selections),
                "target_membership_semantic_sha256": canonical_sha256(
                    membership.to_dict("records")
                ),
                "files": files,
                "credentials_or_signed_urls_persisted": False,
                "target_or_landsat_values_read": False,
                "blind_test_city_accessed": False,
            }
        )
        _write_exclusive(marker, marker_path)

    def acquire_sentinel_cache(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        city_id = str(payload.get("city_id", ""))
        if city_id not in EXTENSION_CITY_IDS or self.phase != ONLINE_PHASE:
            raise M3SourcePredictorWorkerError("Sentinel acquisition task changed.")
        marker_path = (
            self.settings.acquisition_root / "sentinel" / city_id / "ACQUISITION_COMPLETE.json"
        )
        if not marker_path.is_file():
            self._build_sentinel_cache(city_id, marker_path)
        return self._existing_versioned_result(
            city_id, "ACQUISITION_COMPLETE.json", state="sentinel_acquisition_complete"
        )

    def _authenticate_sentinel_inventory_for_cache(
        self,
        city_id: str,
        inventory_path: Path,
    ) -> tuple[dict[str, Any], Any, Any, Any]:
        """Lock every inventory byte, then rebuild its scientific identities."""

        from datetime import date, datetime

        import pandas as pd
        import shapely

        from la_heat.multicity.portable_sentinel_inventory import (
            _asset_column,
            _membership_frame,
            build_city_window_membership,
        )
        from la_heat.sentinel_inventory import (
            REQUIRED_SENTINEL_ASSETS,
            CohortSelection,
            PhysicalAcquisitionKey,
            SentinelItemRecord,
            sentinel_inventory_semantic_sha256,
        )

        inventory = _read_committed(inventory_path, label=f"{city_id} Sentinel inventory")
        target_dates = self._extension_dates(city_id)
        if (
            inventory.get("authorization_commit_sha256") != self.permit["commit_sha256"]
            or inventory.get("state") != "sentinel_inventory_complete"
            or inventory.get("city_id") != city_id
            or inventory.get("target_dates") != list(target_dates)
            or inventory.get("target_dates_sha256") != canonical_sha256(target_dates)
            or inventory.get("window_days_before_target") != [60, 1]
            or not isinstance(inventory.get("timezone"), str)
            or inventory.get("global_cloud_cover_filter") is not False
            or inventory.get("credentials_or_signed_urls_persisted") is not False
            or inventory.get("target_or_landsat_values_read") is not False
            or inventory.get("blind_test_city_accessed") is not False
        ):
            raise M3SourcePredictorCompatibilityError("Sentinel inventory scope drifted.")

        directory = inventory_path.parent.resolve()
        records = inventory.get("files")
        if not isinstance(records, list) or not records:
            raise M3SourcePredictorCompatibilityError("Sentinel inventory files drifted.")
        locked: dict[Path, Mapping[str, Any]] = {}
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise M3SourcePredictorCompatibilityError("Sentinel inventory file drifted.")
            try:
                path = _record_path(
                    self.settings.root,
                    record,
                    label=f"{city_id} Sentinel inventory file {index}",
                )
            except M3SourcePredictorExtensionError as error:
                raise M3SourcePredictorCompatibilityError(
                    "Sentinel inventory file byte lock drifted."
                ) from error
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(self.settings.acquisition_root)
                or not resolved.is_relative_to(directory)
                or resolved in locked
            ):
                raise M3SourcePredictorCompatibilityError(
                    "Sentinel inventory file escaped or duplicated its new acquisition root."
                )
            locked[resolved] = record

        csv_names = (
            "selected_acquisitions.csv",
            "selected_items.csv",
            "target_window_membership.csv",
        )
        csv_paths = {name: (directory / name).resolve() for name in csv_names}
        if any(path not in locked for path in csv_paths.values()):
            raise M3SourcePredictorCompatibilityError(
                "Sentinel inventory required file set changed."
            )
        snapshot_paths = set(locked) - set(csv_paths.values())
        if any(
            path.parent != directory / "stac" or path.suffix.lower() != ".json"
            for path in snapshot_paths
        ):
            raise M3SourcePredictorCompatibilityError(
                "Sentinel inventory contains an unexpected file."
            )

        # No CSV, href, or raster value is touched before every inventory file
        # above has passed its path, byte-count, and SHA-256 lock.
        acquisitions = pd.read_csv(
            csv_paths["selected_acquisitions.csv"],
            dtype={"processing_baseline": "string"},
        )
        items = pd.read_csv(
            csv_paths["selected_items.csv"],
            dtype={"processing_baseline": "string"},
        )
        membership = pd.read_csv(csv_paths["target_window_membership.csv"])
        asset_columns = tuple(_asset_column(asset) for asset in REQUIRED_SENTINEL_ASSETS)
        acquisition_columns = (
            "city_id",
            "physical_acquisition_id",
            "platform",
            "acquired_utc",
            "acquisition_local_date",
            "relative_orbit",
            "normalized_datatake_id",
            "processing_baseline",
            "generation_time",
            "union_city_coverage_fraction",
            "item_count",
            "item_ids",
            "mgrs_tiles",
        )
        item_columns = (
            "city_id",
            "physical_acquisition_id",
            "item_id",
            "platform",
            "acquired_utc",
            "acquisition_local_date",
            "relative_orbit",
            "datatake_id",
            "normalized_datatake_id",
            "mgrs_tile",
            "processing_baseline",
            "generation_time",
            "cloud_cover_percent_audit_only",
            "geometry_wkb_hex",
            "snapshot_filename",
            "snapshot_sha256",
            *asset_columns,
        )
        membership_columns = (
            "city_id",
            "target_date",
            "physical_acquisition_id",
            "acquisition_local_date",
            "lag_days",
        )
        if (
            tuple(acquisitions.columns) != acquisition_columns
            or tuple(items.columns) != item_columns
            or tuple(membership.columns) != membership_columns
            or acquisitions["physical_acquisition_id"].astype(str).duplicated().any()
            or items["item_id"].astype(str).duplicated().any()
            or membership.duplicated(["target_date", "physical_acquisition_id"]).any()
            or set(acquisitions["city_id"].astype(str)) != {city_id}
            or set(items["city_id"].astype(str)) != {city_id}
            or (not membership.empty and set(membership["city_id"].astype(str)) != {city_id})
        ):
            raise M3SourcePredictorCompatibilityError("Sentinel inventory table schema drifted.")

        def utc_datetime(value: object) -> datetime:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Sentinel UTC timestamp became naive.")
            return parsed

        reconstructed: list[Any] = []
        observed_snapshot_paths: set[Path] = set()
        for acquisition in acquisitions.itertuples(index=False):
            key = PhysicalAcquisitionKey(
                platform=str(acquisition.platform),
                acquired_utc=utc_datetime(acquisition.acquired_utc),
                relative_orbit=str(acquisition.relative_orbit),
                normalized_datatake_id=str(acquisition.normalized_datatake_id),
            )
            physical_id = str(acquisition.physical_acquisition_id)
            if key.semantic_id != physical_id:
                raise M3SourcePredictorCompatibilityError(
                    "Sentinel physical acquisition identity drifted."
                )
            selected = items.loc[items["physical_acquisition_id"].astype(str) == physical_id]
            item_records: list[Any] = []
            for item in selected.itertuples(index=False):
                assets: list[tuple[str, str]] = []
                for asset, column in zip(REQUIRED_SENTINEL_ASSETS, asset_columns, strict=True):
                    href = _official_sentinel_url(
                        getattr(item, column),
                        self.settings.official_sentinel_hosts,
                        label=f"Sentinel cached item {item.item_id} asset {asset}",
                    )
                    if urlsplit(href).query:
                        raise M3SourcePredictorCompatibilityError(
                            "A signed Sentinel URL was persisted in the inventory."
                        )
                    assets.append((asset, href))
                snapshot_name = str(item.snapshot_filename)
                if Path(snapshot_name).name != snapshot_name:
                    raise M3SourcePredictorCompatibilityError(
                        "Sentinel snapshot filename became unsafe."
                    )
                snapshot_path = (directory / "stac" / snapshot_name).resolve()
                snapshot_record = locked.get(snapshot_path)
                if snapshot_record is None or snapshot_record.get("sha256") != str(
                    item.snapshot_sha256
                ):
                    raise M3SourcePredictorCompatibilityError("Sentinel snapshot identity drifted.")
                observed_snapshot_paths.add(snapshot_path)
                cloud = item.cloud_cover_percent_audit_only
                cloud_value = None if pd.isna(cloud) else float(cloud)
                item_records.append(
                    SentinelItemRecord(
                        item_id=str(item.item_id),
                        platform=str(item.platform),
                        acquired_utc=utc_datetime(item.acquired_utc),
                        relative_orbit=str(item.relative_orbit),
                        datatake_id=str(item.datatake_id),
                        mgrs_tile=str(item.mgrs_tile),
                        processing_baseline=str(item.processing_baseline),
                        generation_time=utc_datetime(item.generation_time),
                        geometry_wgs84=shapely.from_wkb(bytes.fromhex(str(item.geometry_wkb_hex))),
                        asset_hrefs=tuple(sorted(assets)),
                        cloud_cover_percent=cloud_value,
                    )
                )
            expected_item_ids = tuple(sorted(str(acquisition.item_ids).split("|")))
            observed_item_ids = tuple(sorted(item.item_id for item in item_records))
            expected_tiles = tuple(sorted(str(acquisition.mgrs_tiles).split("|")))
            observed_tiles = tuple(sorted({item.mgrs_tile for item in item_records}))
            coverage = float(acquisition.union_city_coverage_fraction)
            if (
                expected_item_ids != observed_item_ids
                or int(acquisition.item_count) != len(item_records)
                or expected_tiles != observed_tiles
                or not 0.0 <= coverage <= 1.0
                or any(
                    item.processing_baseline != str(acquisition.processing_baseline)
                    for item in item_records
                )
            ):
                raise M3SourcePredictorCompatibilityError(
                    "Sentinel selected acquisition cohort drifted."
                )
            reconstructed.append(
                CohortSelection(
                    acquisition_key=key,
                    processing_baseline=str(acquisition.processing_baseline),
                    union_aoi_coverage_fraction=coverage,
                    generation_time=utc_datetime(acquisition.generation_time),
                    item_ids=observed_item_ids,
                    items=tuple(item_records),
                )
            )
        if observed_snapshot_paths != snapshot_paths:
            raise M3SourcePredictorCompatibilityError("Sentinel snapshot file set drifted.")
        selections = tuple(reconstructed)
        if (
            len(selections) != inventory.get("selected_physical_acquisition_count")
            or len(items) != inventory.get("selected_item_count")
            or sentinel_inventory_semantic_sha256(selections)
            != inventory.get("inventory_semantic_sha256")
        ):
            raise M3SourcePredictorCompatibilityError(
                "Sentinel inventory semantic identity drifted."
            )

        expected_membership = _membership_frame(
            city_id,
            build_city_window_membership(
                (date.fromisoformat(value) for value in target_dates),
                selections,
                timezone=str(inventory["timezone"]),
            ),
        )

        def membership_records(frame: Any) -> list[dict[str, Any]]:
            result = [
                {
                    "city_id": str(row.city_id),
                    "target_date": date.fromisoformat(str(row.target_date)).isoformat(),
                    "physical_acquisition_id": str(row.physical_acquisition_id),
                    "acquisition_local_date": date.fromisoformat(
                        str(row.acquisition_local_date)
                    ).isoformat(),
                    "lag_days": int(row.lag_days),
                }
                for row in frame.itertuples(index=False)
            ]
            return sorted(
                result,
                key=lambda row: (
                    row["target_date"],
                    row["physical_acquisition_id"],
                ),
            )

        observed_membership = membership_records(membership)
        rebuilt_membership = membership_records(expected_membership)
        membership_sha = canonical_sha256(rebuilt_membership)
        if (
            observed_membership != rebuilt_membership
            or len(observed_membership) != inventory.get("membership_count")
            or inventory.get("target_membership_semantic_sha256") != membership_sha
        ):
            raise M3SourcePredictorCompatibilityError(
                "Sentinel target membership identity drifted."
            )
        return inventory, acquisitions, items, membership

    def _build_sentinel_cache(self, city_id: str, marker_path: Path) -> None:
        from la_heat.multicity.portable_predictor_components import load_city_support
        from la_heat.multicity.portable_sentinel_build import (
            CityBuildContext,
            _fixed_spatial_support,
            _process_one,
            _stage_for_city,
            acquisition_cache_is_current,
            city_output_is_current,
            compile_city,
        )
        from la_heat.sentinel_feature_builder import FrozenSentinelInputs

        inventory_path = marker_path.parent / "INVENTORY_COMPLETE.json"
        directory = marker_path.parent
        try:
            inventory, acquisitions, items, membership = (
                self._authenticate_sentinel_inventory_for_cache(city_id, inventory_path)
            )
        except M3SourcePredictorCompatibilityError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise M3SourcePredictorCompatibilityError(
                "Sentinel inventory semantic authentication failed."
            ) from error
        frozen = FrozenSentinelInputs(
            acquisitions=acquisitions,
            items=items,
            membership=membership,
            summary=inventory,
            locks={
                "source_predictor_extension_authorization_commit_sha256": self.permit[
                    "commit_sha256"
                ],
                "sentinel_inventory_commit_sha256": inventory["commit_sha256"],
                "sentinel_inventory_semantic_sha256": inventory["inventory_semantic_sha256"],
            },
        )
        support = load_city_support(self.settings.root, city_id)
        spatial = _fixed_spatial_support(support, target_dates=self._extension_dates(city_id))
        stage = _stage_for_city(self.settings.root, city_id, str(inventory["timezone"]))
        context = CityBuildContext(
            city_id=city_id,
            inventory=frozen,
            support=support,
            spatial=spatial,
            stage=stage,
            base_lock={
                "source_predictor_extension_authorization_commit_sha256": self.permit[
                    "commit_sha256"
                ],
                "sentinel_inventory_commit_sha256": inventory["commit_sha256"],
                **spatial.locks,
            },
            runtime_directory=directory / "cache",
            output_directory=directory / "compiled",
            metadata_directory=directory / "product_metadata",
        )
        for row in acquisitions.itertuples(index=False):
            if not acquisition_cache_is_current(self.settings.root, context, row):
                _process_one(
                    self.settings.root,
                    context,
                    row,
                    download_threads=1,
                    force=False,
                )
        if not city_output_is_current(context):
            compile_city(self.settings.root, context)
        files = [
            _file_record(self.settings.root, path)
            for path in sorted(directory.rglob("*"))
            if path.is_file()
            and not path.is_symlink()
            and path != marker_path
            and not path.name.endswith(".partial")
        ]
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "sentinel_acquisition_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "city_id": city_id,
                "target_dates_sha256": canonical_sha256(self._extension_dates(city_id)),
                "inventory_commit_sha256": inventory["commit_sha256"],
                "physical_acquisition_count": len(acquisitions),
                "files": files,
                "credentials_or_signed_urls_persisted": False,
                "target_or_landsat_values_read": False,
                "blind_test_city_accessed": False,
            }
        )
        _write_exclusive(marker, marker_path)

    def finalize_acquisition(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if (
            self.phase != ONLINE_PHASE
            or tuple(payload.get("extension_city_ids", ())) != EXTENSION_CITY_IDS
        ):
            raise M3SourcePredictorWorkerError("Acquisition finalization task changed.")
        queue = ModelRunQueue(self.settings.database)
        by_kind = queue.counts_by_kind(self.run_id)
        expected = {
            "acquire_daymet_metadata": 10,
            "acquire_daymet_subset": 60,
            "build_sentinel_inventory": 2,
            "acquire_sentinel_cache": 2,
        }
        if any(by_kind.get(kind, {}).get("complete") != count for kind, count in expected.items()):
            raise M3SourcePredictorWorkerError("Online acquisition tasks are incomplete.")
        files: list[dict[str, Any]] = []
        files_by_path: dict[str, dict[str, Any]] = {}
        for task in queue.list_tasks(self.run_id, statuses=("complete",)):
            if task.kind not in expected or not isinstance(task.result, Mapping):
                continue
            records = task.result.get("files", [])
            if not isinstance(records, list):
                raise M3SourcePredictorWorkerError("Acquisition task result changed.")
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                normalized = dict(record)
                path = str(normalized.get("path", ""))
                if path in files_by_path and files_by_path[path] != normalized:
                    raise M3SourcePredictorWorkerError(
                        "Acquisition tasks disagree on one file record."
                    )
                files_by_path[path] = normalized
        files = [files_by_path[path] for path in sorted(files_by_path)]
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_predictor_acquisition_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "key_universe_sha256": self.permit["key_universe"]["key_universe_sha256"],
                "completed_task_counts": expected,
                "files": files,
                "file_set_sha256": canonical_sha256(files),
                "network_phase": "online_acquisition_closed",
                "credentials_or_signed_urls_persisted": False,
                "blind_test_city_accessed": False,
                "target_or_landsat_values_read": False,
                "model_fit_select_predict_or_score_performed": False,
                "next_safe_stage": "offline_assembly_with_network_and_href_reads_zero",
            }
        )
        observed = _write_or_authenticate(marker, self.settings.acquisition_completion)
        authenticate_source_predictor_acquisition_completion(
            self.settings.root,
            authorization_path=self.settings.authorization,
            config_path=self.settings.config_path,
        )
        return {"state": observed["state"], "commit_sha256": observed["commit_sha256"]}

    def _extension_component(self, city_id: str) -> tuple[dict[str, Any], Any]:
        import pandas as pd

        marker_path = self.settings.component_root / city_id / EXTENSION_COMPLETE_NAME
        if not marker_path.is_file():
            raise M3SourcePredictorCompatibilityError(
                f"Versioned {city_id} extension component is not yet built."
            )
        marker = _read_committed(marker_path, label=f"{city_id} extension component")
        record = marker.get("output")
        if (
            marker.get("state") != "source_predictor_extension_city_complete"
            or marker.get("authorization_commit_sha256") != self.permit["commit_sha256"]
            or marker.get("city_id") != city_id
            or marker.get("target_dates") != list(self._extension_dates(city_id))
            or marker.get("feature_names") != list(FEATURE_NAMES)
            or not isinstance(record, Mapping)
        ):
            raise M3SourcePredictorCompatibilityError("Extension component marker drifted.")
        path = self.settings.root / str(record.get("path", ""))
        if (
            not path.resolve().is_relative_to(self.settings.component_root)
            or not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise M3SourcePredictorCompatibilityError("Extension component file drifted.")
        return marker, pd.read_parquet(path)

    def build_extension_city(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        city_id = str(payload.get("city_id", ""))
        if city_id not in EXTENSION_CITY_IDS or self.phase != OFFLINE_PHASE:
            raise M3SourcePredictorWorkerError("Extension build task changed.")
        authenticate_source_predictor_acquisition_completion(
            self.settings.root,
            authorization_path=self.settings.authorization,
            config_path=self.settings.config_path,
        )
        marker_path = self.settings.component_root / city_id / EXTENSION_COMPLETE_NAME
        if not marker_path.is_file():
            self._build_extension_component(city_id, marker_path)
        marker, _frame = self._extension_component(city_id)
        return {"state": marker["state"], "commit_sha256": marker["commit_sha256"]}

    def _build_extension_component(self, city_id: str, marker_path: Path) -> None:
        import numpy as np
        import pandas as pd

        from la_heat.calendar_features import build_calendar_features
        from la_heat.daymet_feature_stage import compile_daymet_feature_tables
        from la_heat.multicity.portable_predictor_components import load_city_support

        static_manifest, static_manifest_path = _static_manifest_from_permit(
            self.settings, self.permit, city_id
        )
        static_record = static_manifest.get("output_files", {}).get("static_features.parquet")
        static_path = static_manifest_path.parent / "static_features.parquet"
        if (
            not isinstance(static_record, Mapping)
            or not static_path.is_file()
            or static_path.stat().st_size != static_record.get("bytes")
            or sha256_file(static_path) != static_record.get("sha256")
        ):
            raise M3SourcePredictorCompatibilityError("Static component lock changed.")
        static = pd.read_parquet(static_path)
        if not set(("tract_geoid", *STATIC_FEATURES)).issubset(static.columns):
            raise M3SourcePredictorCompatibilityError("Static component schema changed.")
        static = static.loc[:, ["tract_geoid", *STATIC_FEATURES]].copy()
        static["tract_geoid"] = static["tract_geoid"].astype(str)
        geoids = authenticated_static_tract_geoids(
            self.settings.root,
            self.permit,
            city_id,
            config_path=self.settings.config_path,
        )
        dates = tuple(pd.Timestamp(value) for value in self._extension_dates(city_id))
        keys = pd.MultiIndex.from_product(
            [geoids, dates], names=["tract_geoid", "target_date"]
        ).to_frame(index=False)
        calendar = build_calendar_features(
            keys.copy(), final_test_year=2025, unlock_final_test=False
        )
        support = load_city_support(self.settings.root, city_id)
        subset_rows = [
            {
                "path": self.settings.acquisition_root
                / "daymet"
                / city_id
                / str(year)
                / f"{variable}.nc",
                "year": year,
                "variable": variable,
            }
            for year in range(2020, 2025)
            for variable in DAYMET_VARIABLES
        ]
        for row in subset_rows:
            if not Path(row["path"]).is_file():
                raise M3SourcePredictorCompatibilityError(
                    f"Daymet subset missing: {row['year']}/{row['variable']}"
                )
        daymet = compile_daymet_feature_tables(
            pd.DataFrame(subset_rows),
            keys.copy(),
            zone_raster=support.zones,
            eligible_land_mask=support.eligible_land,
            tract_geoids=support.tract_geoids,
            target_transform=support.grid.transform,
            target_crs=support.grid.crs,
            windows=(1, 3, 7),
            final_test_year=2025,
        )
        sentinel_marker_path = (
            self.settings.acquisition_root / "sentinel" / city_id / "ACQUISITION_COMPLETE.json"
        )
        sentinel_marker = _read_committed(
            sentinel_marker_path, label=f"{city_id} Sentinel acquisition"
        )
        sentinel_manifest_path = sentinel_marker_path.parent / "compiled" / "SENTINEL_COMPLETE.json"
        sentinel_manifest = _read_committed(
            sentinel_manifest_path, label=f"{city_id} Sentinel features"
        )
        sentinel_record = sentinel_manifest.get("outputs", {}).get("sentinel_features.parquet")
        sentinel_path = sentinel_manifest_path.parent / "sentinel_features.parquet"
        if (
            not isinstance(sentinel_record, Mapping)
            or not sentinel_path.is_file()
            or sentinel_path.stat().st_size != sentinel_record.get("bytes")
            or sha256_file(sentinel_path) != sentinel_record.get("sha256")
        ):
            raise M3SourcePredictorCompatibilityError("Sentinel feature lock changed.")
        sentinel = pd.read_parquet(sentinel_path)
        sentinel["city_id"] = sentinel["city_id"].astype(str)
        sentinel["tract_geoid"] = sentinel["tract_geoid"].astype(str)
        sentinel["target_date"] = pd.to_datetime(sentinel["target_date"])
        if not set(("city_id", "tract_geoid", "target_date", *SENTINEL_FEATURES)).issubset(
            sentinel.columns
        ):
            raise M3SourcePredictorCompatibilityError("Sentinel feature schema changed.")
        combined = keys.merge(static, on="tract_geoid", how="left", validate="many_to_one")
        combined = combined.merge(
            calendar,
            on=["tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
        combined = combined.merge(
            daymet.features,
            on=["tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
        combined.insert(0, "city_id", city_id)
        combined = combined.merge(
            sentinel.loc[
                :,
                ["city_id", "tract_geoid", "target_date", *SENTINEL_FEATURES],
            ],
            on=["city_id", "tract_geoid", "target_date"],
            how="left",
            validate="one_to_one",
        )
        combined = (
            combined.loc[:, ["city_id", "tract_geoid", "target_date", *FEATURE_NAMES]]
            .sort_values(["city_id", "target_date", "tract_geoid"], kind="stable")
            .reset_index(drop=True)
        )
        expected_rows = len(geoids) * len(dates)
        if (
            len(combined) != expected_rows
            or combined.duplicated(["city_id", "tract_geoid", "target_date"]).any()
        ):
            raise M3SourcePredictorCompatibilityError("Extension key product changed.")
        if not np.isfinite(
            combined.loc[:, [*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES]].to_numpy(
                dtype=float
            )
        ).all():
            raise M3SourcePredictorCompatibilityError(
                "Extension base predictor contains non-finite values."
            )
        sentinel_missing = combined.loc[:, SENTINEL_FEATURES].isna()
        if not sentinel_missing.nunique(axis=1).eq(1).all():
            raise M3SourcePredictorCompatibilityError("Extension Sentinel missingness is partial.")
        output_directory = marker_path.parent
        output_path = output_directory / "predictors_extension_46.parquet"
        audit_paths = {
            "daymet_feature_audit.parquet": daymet.audit,
            "daymet_fixed_cell_weights.parquet": daymet.weights,
            "daymet_tract_daily.parquet": daymet.tract_daily,
        }
        atomic_parquet(combined, output_path)
        audit_records: dict[str, Any] = {}
        for name, frame in audit_paths.items():
            path = output_directory / name
            atomic_parquet(frame, path)
            audit_records[name] = _file_record(self.settings.root, path)
        output_record = _parquet_record(self.settings.root, output_path, combined)
        output_record["tract_geoid_set_sha256"] = canonical_sha256(geoids)
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_predictor_extension_city_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "city_id": city_id,
                "target_dates": list(self._extension_dates(city_id)),
                "tract_geoid_set_sha256": canonical_sha256(geoids),
                "feature_names": list(FEATURE_NAMES),
                "output": output_record,
                "audit_outputs": audit_records,
                "inputs": {
                    "static_provenance_commit_sha256": static_manifest["commit_sha256"],
                    "daymet_subset_file_set_sha256": canonical_sha256(
                        [_file_record(self.settings.root, Path(row["path"])) for row in subset_rows]
                    ),
                    "sentinel_acquisition_commit_sha256": sentinel_marker["commit_sha256"],
                    "sentinel_feature_commit_sha256": sentinel_manifest["commit_sha256"],
                },
                "audit": {
                    "offline_network_requests": 0,
                    "offline_href_reads": 0,
                    "blind_test_city_accessed": False,
                    "target_or_landsat_values_read": False,
                    "model_fit_select_predict_or_score_performed": False,
                },
            }
        )
        _write_exclusive(marker, marker_path)

    def _old_predictors(self) -> Any:
        import pandas as pd

        record = self.permit["inputs"]["existing_predictors_46_completion"]
        manifest_path = self.settings.root / str(record["path"])
        manifest = _read_committed(manifest_path, label="Existing predictors 46")
        output = manifest.get("output")
        if not isinstance(output, Mapping):
            raise M3SourcePredictorCompatibilityError("Existing predictor output changed.")
        path = self.settings.root / str(output.get("path", ""))
        if (
            not path.is_file()
            or path.stat().st_size != output.get("bytes")
            or sha256_file(path) != output.get("sha256")
        ):
            raise M3SourcePredictorCompatibilityError("Existing predictor file lock changed.")
        return pd.read_parquet(path)

    def compile_source_city(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        import numpy as np
        import pandas as pd

        city_id = str(payload.get("city_id", ""))
        if city_id not in SOURCE_CITY_IDS or self.phase != OFFLINE_PHASE:
            raise M3SourcePredictorWorkerError("Source city compile task changed.")
        old = self._old_predictors()
        old["city_id"] = old["city_id"].astype(str)
        old["tract_geoid"] = old["tract_geoid"].astype(str)
        old["target_date"] = pd.to_datetime(old["target_date"])
        selected = old.loc[
            old["city_id"] == city_id, ["city_id", "tract_geoid", "target_date", *FEATURE_NAMES]
        ].copy()
        if city_id in EXTENSION_CITY_IDS:
            _marker, extension = self._extension_component(city_id)
            extension["city_id"] = extension["city_id"].astype(str)
            extension["tract_geoid"] = extension["tract_geoid"].astype(str)
            extension["target_date"] = pd.to_datetime(extension["target_date"])
            if tuple(extension.columns) != (
                "city_id",
                "tract_geoid",
                "target_date",
                *FEATURE_NAMES,
            ):
                raise M3SourcePredictorCompatibilityError("Extension schema changed.")
            selected = pd.concat([selected, extension], ignore_index=True)
        selected = selected.sort_values(
            ["city_id", "target_date", "tract_geoid"], kind="stable"
        ).reset_index(drop=True)
        expected_dates = self._all_dates(city_id)
        observed_dates = tuple(sorted(selected["target_date"].dt.date.astype(str).unique()))
        canonical_geoids = authenticated_static_tract_geoids(
            self.settings.root, self.permit, city_id, config_path=self.settings.config_path
        )
        if (
            observed_dates != expected_dates
            or selected.duplicated(["city_id", "tract_geoid", "target_date"]).any()
        ):
            raise M3SourcePredictorCompatibilityError("Compiled source key set changed.")
        for _date, rows in selected.groupby("target_date", sort=True):
            if set(rows["tract_geoid"].astype(str)) != set(canonical_geoids):
                raise M3SourcePredictorCompatibilityError("Compiled canonical GEOID set changed.")
        base = selected[[*FEATURE_NAMES[:-5]]].to_numpy(dtype=float)
        if not np.isfinite(base).all():
            raise M3SourcePredictorCompatibilityError("Compiled base predictors are non-finite.")
        missing = selected[list(SENTINEL_FEATURES)].isna()
        if not missing.nunique(axis=1).eq(1).all():
            raise M3SourcePredictorCompatibilityError("Partial Sentinel missingness found.")
        context = authenticated_city_centroid_latitude(
            self.settings.root, self.permit, city_id, config_path=self.settings.config_path
        )
        selected = selected.loc[:, REQUIRED_COLUMNS]
        destination = self.settings.output_root / city_id / "predictors_46.parquet"
        marker_path = destination.parent / CITY_COMPLETE_NAME
        if marker_path.is_file():
            marker = _read_committed(marker_path, label=f"{city_id} predictors")
            output = marker.get("output", {})
            if (
                marker.get("authorization_commit_sha256") != self.permit["commit_sha256"]
                or marker.get("city_context") != context
                or not destination.is_file()
                or destination.stat().st_size != output.get("bytes")
                or sha256_file(destination) != output.get("sha256")
            ):
                raise M3SourcePredictorCompatibilityError("Existing city predictors drifted.")
            return {"state": marker["state"], "commit_sha256": marker["commit_sha256"]}
        atomic_parquet(selected, destination)
        output = {
            "city_id": city_id,
            **_parquet_record(self.settings.root, destination, selected),
            "tract_geoid_set_sha256": canonical_sha256(canonical_geoids),
        }
        marker = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_city_predictors_46_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "city_id": city_id,
                "feature_names": list(FEATURE_NAMES),
                "required_columns": list(REQUIRED_COLUMNS),
                "city_context": context,
                "output": output,
                "audit": {
                    "offline_network_requests": 0,
                    "offline_href_reads": 0,
                    "blind_test_city_accessed": False,
                    "target_or_landsat_values_read": False,
                    "model_fit_select_predict_or_score_performed": False,
                    "old_predictor_or_runtime_mutated": False,
                },
            }
        )
        _write_exclusive(marker, marker_path)
        return {"state": marker["state"], "commit_sha256": marker["commit_sha256"]}

    def finalize_predictors(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if (
            self.phase != OFFLINE_PHASE
            or tuple(payload.get("source_city_ids", ())) != SOURCE_CITY_IDS
            or payload.get("feature_count") != 46
        ):
            raise M3SourcePredictorWorkerError("Predictor finalization task changed.")
        acquisition = authenticate_source_predictor_acquisition_completion(
            self.settings.root,
            authorization_path=self.settings.authorization,
            config_path=self.settings.config_path,
        )
        tables: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        for city_id in SOURCE_CITY_IDS:
            marker_path = self.settings.output_root / city_id / CITY_COMPLETE_NAME
            marker = _read_committed(marker_path, label=f"{city_id} predictor completion")
            if (
                marker.get("state") != "source_city_predictors_46_complete"
                or marker.get("authorization_commit_sha256") != self.permit["commit_sha256"]
            ):
                raise M3SourcePredictorWorkerError("City predictor completion changed.")
            tables.append(dict(marker["output"]))
            contexts.append(dict(marker["city_context"]))
        completion = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_predictors_46_complete",
                "authorization_commit_sha256": self.permit["commit_sha256"],
                "acquisition_completion_commit_sha256": acquisition["commit_sha256"],
                "source_qa_candidates_completion_commit_sha256": self.permit[
                    "source_qa_candidates_completion_commit_sha256"
                ],
                "feature_count": 46,
                "feature_names": list(FEATURE_NAMES),
                "context_features": list(CONTEXT_FEATURES),
                "required_columns": list(REQUIRED_COLUMNS),
                "extension_key_universe": self.permit["key_universe"],
                "city_tables": tables,
                "city_context": contexts,
                "audit": {
                    "offline_network_requests": 0,
                    "offline_href_reads": 0,
                    "blind_test_city_accessed": False,
                    "target_or_landsat_values_read": False,
                    "model_fit_select_predict_or_score_performed": False,
                    "old_predictor_or_runtime_mutated": False,
                },
                "next_safe_stage": "create_independent_nested_whole_city_loso_authorization",
            }
        )
        observed = _write_or_authenticate(completion, self.settings.predictor_completion)
        authenticate_source_predictors_46_completion(
            self.settings.root,
            authorization_path=self.settings.authorization,
            config_path=self.settings.config_path,
        )
        return {"state": observed["state"], "commit_sha256": observed["commit_sha256"]}

    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = {
            "freeze_key_universe": self.freeze_key_universe,
            "authenticate_static_reuse": self.authenticate_static_reuse,
            "acquire_daymet_metadata": self.acquire_daymet_metadata,
            "acquire_daymet_subset": self.acquire_daymet_subset,
            "build_sentinel_inventory": self.build_sentinel_inventory,
            "acquire_sentinel_cache": self.acquire_sentinel_cache,
            "finalize_acquisition": self.finalize_acquisition,
            "build_extension_city": self.build_extension_city,
            "compile_source_city": self.compile_source_city,
            "finalize_predictors": self.finalize_predictors,
        }
        phase_kinds = ONLINE_KINDS if self.phase == ONLINE_PHASE else OFFLINE_KINDS
        if kind not in phase_kinds:
            raise M3SourcePredictorWorkerError(
                f"Task {kind!r} is forbidden in phase {self.phase!r}."
            )
        return allowed[kind](payload)


@dataclass(frozen=True, slots=True)
class PredictorWorkerOptions:
    phase: str
    poll_seconds: float = 0.5

    def validate(self) -> None:
        if self.phase not in PHASES or self.poll_seconds <= 0:
            raise ValueError("Invalid predictor worker options.")


def _publish_status(
    settings: PredictorExtensionSettings,
    queue: ModelRunQueue,
    run_id: str,
    phase: str,
    *,
    active_task_id: str | None = None,
    last_error_type: str | None = None,
) -> dict[str, Any]:
    payload = source_predictor_runtime_status(queue, run_id, settings=settings, phase=phase)
    payload.update(
        {
            "active_task_ids": [] if active_task_id is None else [active_task_id],
            "last_error_type": last_error_type,
            "network_request_count": 0 if phase == OFFLINE_PHASE else None,
            "href_read_count": 0 if phase == OFFLINE_PHASE else None,
        }
    )
    atomic_json(payload, settings.status)
    return payload


def _execute_unlocked(
    *,
    settings: PredictorExtensionSettings,
    permit: Mapping[str, Any],
    options: PredictorWorkerOptions,
    adapter: PredictorExtensionAdapter,
) -> dict[str, Any]:
    options.validate()
    queue = ModelRunQueue(settings.database)
    run_id = source_predictor_run_id(permit)
    owner = f"m3-source-predictor-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    active_task_id: str | None = None
    last_error_type: str | None = None
    _publish_status(settings, queue, run_id, options.phase)
    while queue.get_desired_state(run_id) == "running":
        kind = active_predictor_kind(queue, run_id, options.phase)
        if kind == "complete":
            break
        claim = queue.claim_next(
            run_id,
            owner=owner,
            lease_seconds=settings.lease_seconds,
            kinds={kind},
        )
        if claim is None:
            time.sleep(options.poll_seconds)
            continue
        active_task_id = claim.task_id
        _publish_status(settings, queue, run_id, options.phase, active_task_id=active_task_id)
        heartbeat = _Heartbeat(
            queue,
            claim,
            interval_seconds=settings.heartbeat_seconds,
            lease_seconds=settings.lease_seconds,
        )
        try:
            with heartbeat:
                current = load_m3_source_predictor_extension_runtime_permit(
                    settings.root, settings.authorization, settings.config_path
                )
                if current != permit:
                    raise M3SourcePredictorWorkerError("Runtime permit changed.")
                result = dict(adapter.execute(kind, dict(claim.payload)))
            if heartbeat.lost.is_set():
                raise LeaseLostError("Predictor task lease heartbeat was lost.")
            queue.complete(
                run_id,
                claim.task_id,
                owner=owner,
                generation=claim.claim_generation,
                result=result,
            )
            last_error_type = None
        except M3SourcePredictorCredentialRequiredError as error:
            last_error_type = type(error).__name__
            queue.retry(
                run_id,
                claim.task_id,
                owner=owner,
                generation=claim.claim_generation,
                error_type=last_error_type,
                base_delay_seconds=settings.retry_base_seconds,
                max_delay_seconds=settings.retry_base_seconds,
            )
            queue.set_desired_state(run_id, "paused")
        except M3SourcePredictorCompatibilityError as error:
            last_error_type = type(error).__name__
            queue.quarantine(
                run_id,
                claim.task_id,
                owner=owner,
                generation=claim.claim_generation,
                error_type=last_error_type,
                result={"message": str(error), "safe_to_resume_after_fix": True},
            )
            queue.set_desired_state(run_id, "paused")
        except LeaseLostError:
            last_error_type = "LeaseLostError"
        except Exception as error:
            last_error_type = type(error).__name__
            queue.retry(
                run_id,
                claim.task_id,
                owner=owner,
                generation=claim.claim_generation,
                error_type=last_error_type,
                base_delay_seconds=settings.retry_base_seconds,
                max_delay_seconds=settings.retry_max_seconds,
            )
        finally:
            active_task_id = None
            _publish_status(
                settings,
                queue,
                run_id,
                options.phase,
                last_error_type=last_error_type,
            )
    if (
        not queue.counts(run_id)["quarantined"]
        and active_predictor_kind(queue, run_id, options.phase) == "complete"
    ):
        queue.set_desired_state(run_id, "paused")
    return _publish_status(settings, queue, run_id, options.phase, last_error_type=last_error_type)


def execute_source_predictor_worker(
    project_root: str | Path,
    *,
    phase: str,
    config_path: str | Path = DEFAULT_CONFIG,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    adapter_factory: AdapterFactory = SafeExistingBuilderAdapter,
) -> dict[str, Any]:
    """Lock, authenticate, initialize paused state, then start one phase safely."""

    settings = load_predictor_extension_settings(project_root, config_path)
    if Path(authorization_path) != AUTHORIZATION_PATH:
        requested = (settings.root / authorization_path).resolve()
        if requested != settings.authorization:
            raise M3SourcePredictorWorkerError("Authorization path changed.")
    with exclusive_predictor_worker(settings.worker_lock):
        full_permit = authenticate_m3_source_predictor_extension_authorization(
            settings.root, settings.authorization, settings.config_path
        )
        permit = load_m3_source_predictor_extension_runtime_permit(
            settings.root, settings.authorization, settings.config_path
        )
        if permit != full_permit:
            raise M3SourcePredictorWorkerError("Full authorization and runtime permit disagree.")
        initialized = initialize_source_predictor_runtime(
            settings.root, config_path=settings.config_path
        )
        run_id = source_predictor_run_id(permit)
        if initialized.get("run_id") != run_id:
            raise M3SourcePredictorWorkerError("Initialized predictor run changed.")
        if phase == OFFLINE_PHASE:
            authenticate_source_predictor_acquisition_completion(
                settings.root,
                authorization_path=settings.authorization,
                config_path=settings.config_path,
            )
        adapter = adapter_factory(settings, permit, phase)
        queue = ModelRunQueue(settings.database)
        queue.set_desired_state(run_id, "running")
        try:
            return _execute_unlocked(
                settings=settings,
                permit=permit,
                options=PredictorWorkerOptions(phase=phase),
                adapter=adapter,
            )
        except Exception:
            queue.set_desired_state(run_id, "paused")
            raise
