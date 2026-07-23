"""Target-blind Daymet grid staging for the isolated 2025 final test.

The stage is deliberately separate from the development Daymet stage.  It may
read only the formal model-lock metadata, the frozen target-blind Landsat
inventory, its tract-date key universe, and public Daymet weather products.  It
never opens a Landsat target/QA table, a fitted model, or a model score.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

import pandas as pd
import rasterio
import requests

import la_heat.daymet_grid as daymet_core
from la_heat.config import ResearchConfig, load_config
from la_heat.daymet_grid import (
    DAYMET_CMR_COLLECTION_ID,
    DAYMET_CMR_GRANULES_URL,
    DAYMET_CMR_SERVICE_BRIDGE_URL,
    DAYMET_DIRECT_DAP4_ROUTE,
    DAYMET_DOI,
    DAYMET_DOI_URL,
    DaymetGranule,
    DaymetGridAuditError,
    DaymetNetCDFSpec,
    EarthdataBearerToken,
    authenticated_netcdf_download,
    build_daymet_direct_subset_url,
    validate_daymet_direct_subset_spec,
    validate_daymet_netcdf_grid_specs,
)
from la_heat.final_test_inventory import (
    FINAL_TEST_YEAR,
    KEY_UNIVERSE_FILENAME,
    authenticate_formal_model_lock,
)
from la_heat.final_test_inventory import (
    SUMMARY_FILENAME as LANDSAT_SUMMARY_FILENAME,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "final-test-daymet-grid-v1-target-blind"
PROVENANCE_FILENAME: Final = "DAYMET_GRID.json"
GRANULE_INVENTORY_FILENAME: Final = "granule_inventory.csv"
WEATHER_REQUIREMENTS_FILENAME: Final = "weather_date_requirements.csv"
SUBSET_DOWNLOADS_FILENAME: Final = "subset_downloads.csv"
DEFAULT_MANIFEST_DIRECTORY: Final = Path("manifests/final_test_2025/daymet_grid")
DEFAULT_RAW_SUBSET_DIRECTORY: Final = Path(
    "data/raw/final_test_2025/daymet/subsets"
)
KEY_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)
REQUIRED_LAG_DAYS: Final = tuple(range(1, 8))
PIPELINE_FILES: Final = (
    "scripts/stage_final_test_daymet_grid.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/final_test_daymet_grid.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/provenance.py",
)
_ALLOWED_STATES: Final = frozenset(
    {"inventory_complete", "subsets_partial", "subsets_complete"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class FinalTestDaymetGridError(RuntimeError):
    """Raised when final-test Daymet staging cannot prove its blind contract."""


class _ResponseLike(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _HttpClientLike(Protocol):
    def get(self, url: str, **kwargs: object) -> _ResponseLike: ...


@dataclass(frozen=True, slots=True)
class FinalTestDaymetRequirements:
    """Exact weather dates implied by frozen 2025 targets and d-7 through d-1."""

    target_dates: tuple[date, ...]
    weather_dates: tuple[date, ...]
    source_years: tuple[int, ...]
    membership_semantic_sha256: str


@dataclass(frozen=True, slots=True)
class _AuthenticatedInputs:
    config: ResearchConfig
    formal_lock_path: Path
    formal_lock: dict[str, Any]
    formal_lock_file_sha256: str
    inventory_path: Path
    inventory: dict[str, Any]
    inventory_commit_sha256: str
    inventory_file_sha256: str
    key_path: Path
    key_record: dict[str, Any]
    requirements: FinalTestDaymetRequirements
    requirements_frame: pd.DataFrame


Discoverer = Callable[..., list[DaymetGranule]]
Downloader = Callable[..., dict[str, object]]
Inspector = Callable[..., DaymetNetCDFSpec]
CredentialProvider = Callable[[], EarthdataBearerToken]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalTestDaymetGridError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise FinalTestDaymetGridError(f"{label} changed or is not a JSON object.")
    return payload


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if (
        not isinstance(recorded, str)
        or _SHA256.fullmatch(recorded) is None
        or canonical_sha256(working) != recorded
    ):
        raise FinalTestDaymetGridError(f"{label} canonical commit is invalid.")
    return recorded


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("commit_sha256", None)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def _validate_file_record(
    path: Path,
    record: object,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise FinalTestDaymetGridError(f"{label} file lock is invalid.")
    recorded_path = record.get("path")
    if not isinstance(recorded_path, str) or Path(recorded_path).resolve() != path.resolve():
        raise FinalTestDaymetGridError(f"{label} path lock failed.")
    if (
        not path.is_file()
        or sha256_file(path) != record.get("sha256")
        or path.stat().st_size != record.get("bytes")
    ):
        raise FinalTestDaymetGridError(f"{label} byte lock failed.")
    return dict(record)


def _weather_requirement_frame(target_dates: Sequence[date]) -> pd.DataFrame:
    rows = [
        {
            "target_date": target,
            "weather_date": target - timedelta(days=lag_days),
            "lag_days": lag_days,
        }
        for target in target_dates
        for lag_days in REQUIRED_LAG_DAYS
    ]
    return pd.DataFrame(rows).sort_values(
        ["target_date", "lag_days"], kind="stable"
    ).reset_index(drop=True)


def derive_final_test_daymet_requirements(
    keys: pd.DataFrame,
    *,
    final_test_year: int = FINAL_TEST_YEAR,
) -> tuple[FinalTestDaymetRequirements, pd.DataFrame]:
    """Validate target-blind keys and derive exact d-7 through d-1 dates."""

    if final_test_year != FINAL_TEST_YEAR:
        raise FinalTestDaymetGridError("The final-test Daymet policy is exact 2025.")
    if keys.columns.tolist() != list(KEY_COLUMNS):
        raise FinalTestDaymetGridError(
            "Final-test key universe must contain only the frozen metadata schema."
        )
    working = keys.copy()
    working["tract_geoid"] = working["tract_geoid"].astype("string")
    working["target_date"] = pd.to_datetime(working["target_date"], errors="raise")
    dates = working["target_date"]
    if (
        working.empty
        or dates.dt.tz is not None
        or not dates.dt.normalize().equals(dates)
        or not dates.dt.year.eq(FINAL_TEST_YEAR).all()
        or working.duplicated(["tract_geoid", "target_date"]).any()
    ):
        raise FinalTestDaymetGridError(
            "Final-test keys must be unique, timezone-naive 2025 civil dates."
        )
    target_dates = tuple(
        value.date() for value in sorted(dates.drop_duplicates().tolist())
    )
    if not target_dates:
        raise FinalTestDaymetGridError("At least one frozen final-test date is required.")
    reference_geoids: frozenset[str] | None = None
    for _, group in working.groupby("target_date", sort=True):
        geoids = frozenset(group["tract_geoid"].astype(str))
        if len(geoids) != len(group):
            raise FinalTestDaymetGridError("A final-test date contains duplicate tracts.")
        if reference_geoids is None:
            reference_geoids = geoids
        elif geoids != reference_geoids:
            raise FinalTestDaymetGridError(
                "Final-test key dates do not share one frozen tract universe."
            )

    membership = _weather_requirement_frame(target_dates)
    target = pd.to_datetime(membership["target_date"])
    weather = pd.to_datetime(membership["weather_date"])
    lag = (target - weather).dt.days
    if (
        not lag.equals(membership["lag_days"])
        or set(lag) != set(REQUIRED_LAG_DAYS)
        or (weather >= target).any()
    ):
        raise AssertionError("Constructed Daymet requirements violate d-7 through d-1.")
    weather_dates = tuple(
        value.date() for value in sorted(weather.drop_duplicates().tolist())
    )
    source_years = tuple(sorted({value.year for value in weather_dates}))
    requirements = FinalTestDaymetRequirements(
        target_dates=target_dates,
        weather_dates=weather_dates,
        source_years=source_years,
        membership_semantic_sha256=canonical_frame_sha256(
            membership,
            sort_by=["target_date", "lag_days"],
        ),
    )
    return requirements, membership


def _normalized_variables(variables: Sequence[str]) -> tuple[str, ...]:
    normalized = daymet_core._normalize_variables(variables)
    expected = daymet_core._normalize_variables(DEFAULT_DAYMET_VARIABLES)
    if normalized != expected:
        raise FinalTestDaymetGridError(
            "Final-test Daymet staging requires the exact six frozen variables."
        )
    return normalized


def discover_exact_final_test_daymet_granules(
    requirements: FinalTestDaymetRequirements,
    *,
    variables: Sequence[str] = DEFAULT_DAYMET_VARIABLES,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 120.0),
    endpoint: str = DAYMET_CMR_GRANULES_URL,
) -> list[DaymetGranule]:
    """Discover only annual granules implied by exact 2025 d-7:d-1 windows.

    This is intentionally not implemented by pretending that the final-test
    year is 2026.  Authorization comes from the validated 2025 key-derived
    requirements object.
    """

    if (
        not requirements.target_dates
        or any(value.year != FINAL_TEST_YEAR for value in requirements.target_dates)
        or not requirements.source_years
        or set(requirements.source_years)
        != {value.year for value in requirements.weather_dates}
    ):
        raise FinalTestDaymetGridError(
            "Daymet discovery lacks an exact key-derived 2025 requirement set."
        )
    normalized_variables = _normalized_variables(variables)
    client: _HttpClientLike = requests if http_client is None else http_client
    response = client.get(
        endpoint,
        params={
            "collection_concept_id": DAYMET_CMR_COLLECTION_ID,
            "temporal": (
                f"{requirements.source_years[0]}-01-01T00:00:00Z,"
                f"{requirements.source_years[-1]}-12-31T23:59:59Z"
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
        for year in requirements.source_years
        for variable in normalized_variables
    }
    discovered: dict[tuple[str, int], DaymetGranule] = {}
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise DaymetGridAuditError("CMR feed contains a non-object granule.")
        title = str(raw.get("title", ""))
        match = daymet_core._GRANULE_PATTERN.fullmatch(title)
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
        if re.fullmatch(r"G\d+-ORNL_CLOUD", concept_id) is None:
            raise DaymetGridAuditError(
                f"CMR returned an invalid ORNL_CLOUD concept id for {title}."
            )
        https_url = daymet_core._official_link(raw, relation_suffix="/data#")
        opendap_url = daymet_core._official_link(raw, relation_suffix="/service#")
        daymet_core._audit_daymet_url(https_url, protected_download=True)
        daymet_core._audit_daymet_url(opendap_url, protected_download=False)
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
            "CMR Daymet V4 R1 final-test granule set is incomplete; "
            f"missing={missing}, unexpected={unexpected}."
        )
    return [discovered[key] for key in sorted(discovered, key=lambda key: (key[1], key[0]))]


def inspect_exact_final_test_daymet_netcdf(
    path: str | Path,
    *,
    variable: str,
    year: int,
    requirements: FinalTestDaymetRequirements,
) -> DaymetNetCDFSpec:
    """Audit a required annual subset without weakening the 2025 lock policy."""

    normalized_variable = daymet_core._normalize_variables((variable,))[0]
    if (
        isinstance(year, bool)
        or not isinstance(year, int)
        or year not in requirements.source_years
    ):
        raise FinalTestDaymetGridError(
            "Daymet NetCDF year is not implied by frozen 2025 weather windows."
        )
    source_path = Path(path)
    if not source_path.is_file() or not daymet_core._has_netcdf_signature(source_path):
        raise DaymetGridAuditError(f"Daymet subset is missing or not NetCDF: {source_path}")
    uri = daymet_core._select_daymet_subdataset(source_path, normalized_variable)
    with rasterio.open(uri) as source:
        if source.count != 365 or min(source.width, source.height) <= 0:
            raise DaymetGridAuditError(
                "Daymet NetCDF must contain 365 non-empty raster bands."
            )
        if not daymet_core._is_locked_daymet_crs(source.crs):
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
        units = daymet_core._tag_value(
            source.tags(), f"{normalized_variable}#units", "units"
        ) or daymet_core._tag_value(source.tags(1), "units")
        aliases = daymet_core._DAYMET_NETCDF_UNIT_ALIASES[normalized_variable]
        if units is None or daymet_core._normalized_netcdf_unit(units) not in {
            daymet_core._normalized_netcdf_unit(value) for value in aliases
        }:
            raise DaymetGridAuditError(
                f"Daymet variable {normalized_variable} has unexpected units {units!r}."
            )
        dates = daymet_core._daymet_band_dates(source, year=year)
        nodata = daymet_core._daymet_nodata(source, variable=normalized_variable)
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
            year=year,
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


def _authenticate_inventory_and_keys(
    *,
    config_path: Path,
    formal_lock_path: Path,
    inventory_directory: Path,
) -> _AuthenticatedInputs:
    formal_lock, formal_sha256 = authenticate_formal_model_lock(formal_lock_path)
    config = load_config(config_path)
    if config.final_test_year != FINAL_TEST_YEAR or config.final_test_unlocked:
        raise FinalTestDaymetGridError(
            "Target-blind Daymet staging requires exact 2025 with labels still locked."
        )
    inventory_path = inventory_directory / LANDSAT_SUMMARY_FILENAME
    inventory = _read_json(inventory_path, label="final-test Landsat inventory")
    inventory_commit = _verify_commit(
        inventory, label="final-test Landsat inventory"
    )
    formal_record = inventory.get("formal_model_lock")
    if (
        inventory.get("state") != "target_blind_inventory_frozen"
        or inventory.get("final_test_year") != FINAL_TEST_YEAR
        or inventory.get("target_blind") is not True
        or inventory.get("target_assets_opened") is not False
        or inventory.get("target_or_qa_values_read") is not False
        or inventory.get("labels_created") is not False
        or inventory.get("models_loaded") is not False
        or inventory.get("model_scores_read") is not False
        or inventory.get("one_time_evaluation_consumed") is not False
        or not isinstance(formal_record, dict)
        or formal_record.get("sha256") != formal_sha256
        or formal_record.get("commit_sha256") != formal_lock.get("commit_sha256")
    ):
        raise FinalTestDaymetGridError(
            "Landsat inventory is not the untouched target-blind 2025 lock."
        )
    research_record = inventory.get("source_records", {}).get("research_config")
    if (
        not isinstance(research_record, dict)
        or research_record.get("sha256") != sha256_file(config.path)
    ):
        raise FinalTestDaymetGridError(
            "Research configuration changed after the Landsat inventory freeze."
        )

    outputs = inventory.get("output_files")
    if not isinstance(outputs, dict) or KEY_UNIVERSE_FILENAME not in outputs:
        raise FinalTestDaymetGridError("Landsat inventory lacks key-universe locks.")
    for filename, record in outputs.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise FinalTestDaymetGridError("Landsat inventory output name is invalid.")
        _validate_file_record(
            inventory_directory / filename,
            record,
            label=f"Landsat inventory output {filename}",
        )
    key_path = inventory_directory / KEY_UNIVERSE_FILENAME
    key_record = dict(outputs[KEY_UNIVERSE_FILENAME])
    before = sha256_file(key_path)
    keys = pd.read_parquet(key_path)
    if sha256_file(key_path) != before:
        raise FinalTestDaymetGridError("Final-test key universe changed while read.")
    requirements, membership = derive_final_test_daymet_requirements(keys)
    semantic = canonical_frame_sha256(
        keys,
        sort_by=["target_date", "tract_geoid"],
    )
    if (
        semantic != inventory.get("semantic_hashes", {}).get("key_universe")
        or len(keys) != inventory.get("key_count")
        or len(requirements.target_dates) != inventory.get("primary_overpass_count")
        or keys["tract_geoid"].nunique() != inventory.get("tract_count")
    ):
        raise FinalTestDaymetGridError(
            "Final-test key universe disagrees with its Landsat inventory commitment."
        )
    return _AuthenticatedInputs(
        config=config,
        formal_lock_path=formal_lock_path,
        formal_lock=formal_lock,
        formal_lock_file_sha256=formal_sha256,
        inventory_path=inventory_path,
        inventory=inventory,
        inventory_commit_sha256=inventory_commit,
        inventory_file_sha256=sha256_file(inventory_path),
        key_path=key_path,
        key_record=key_record,
        requirements=requirements,
        requirements_frame=membership,
    )


def _weather_config(config: ResearchConfig) -> dict[str, Any]:
    study = config.raw["study"]
    weather = config.raw["weather_features"]
    variables = _normalized_variables(tuple(weather["variables"]))
    if (
        weather.get("source") != "Daymet V4 R1"
        or weather.get("dataset_doi") != DAYMET_DOI
        or weather.get("cmr_collection_concept_id") != DAYMET_CMR_COLLECTION_ID
        or weather.get("cmr_granules_url") != DAYMET_CMR_GRANULES_URL
        or weather.get("cmr_service_bridge_url") != DAYMET_CMR_SERVICE_BRIDGE_URL
        or weather.get("subset_access_route") != DAYMET_DIRECT_DAP4_ROUTE
        or weather.get("region") != "na"
        or int(weather.get("latest_primary_source_offset_days", 0)) != -1
        or sorted(weather.get("rolling_windows_days", [])) != [1, 3, 7]
        or weather.get("allow_target_day_observations_primary_model") is not False
        or int(study.get("primary_dynamic_data_latest_offset_days", 0)) != -1
    ):
        raise FinalTestDaymetGridError("Frozen Daymet hindcast configuration drifted.")
    payload = {
        "bbox_wgs84": list(study["bbox_wgs84"]),
        "variables": list(variables),
        "subset_access_route": DAYMET_DIRECT_DAP4_ROUTE,
        "direct_subset_y_indices": list(weather["direct_subset_y_indices"]),
        "direct_subset_x_indices": list(weather["direct_subset_x_indices"]),
        "maximum_subset_bytes": int(weather["maximum_subset_bytes"]),
        "cmr_granules_url": str(weather["cmr_granules_url"]),
        "token_environment_variables": list(weather["token_environment_variables"]),
        "latest_source_offset_days": -1,
        "rolling_windows_days": [1, 3, 7],
    }
    payload["semantic_sha256"] = canonical_sha256(payload)
    return payload


def _pipeline_fingerprint() -> tuple[str, dict[str, Any]]:
    return code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )


def _validate_granules(
    granules: Sequence[DaymetGranule],
    *,
    requirements: FinalTestDaymetRequirements,
    variables: Sequence[str],
    y_indices: Sequence[int],
    x_indices: Sequence[int],
) -> list[DaymetGranule]:
    expected = {
        (variable, year)
        for year in requirements.source_years
        for variable in variables
    }
    observed: dict[tuple[str, int], DaymetGranule] = {}
    for granule in granules:
        key = (granule.variable, granule.year)
        if key in observed:
            raise FinalTestDaymetGridError(f"Duplicate Daymet granule: {key}")
        if key not in expected:
            raise FinalTestDaymetGridError(f"Unexpected Daymet granule: {key}")
        build_daymet_direct_subset_url(
            granule,
            y_indices=y_indices,
            x_indices=x_indices,
        )
        observed[key] = granule
    if set(observed) != expected:
        raise FinalTestDaymetGridError(
            f"Daymet granule set is incomplete: {sorted(expected - set(observed))}"
        )
    return [observed[key] for key in sorted(observed, key=lambda key: (key[1], key[0]))]


def _granules_from_csv(path: Path) -> list[DaymetGranule]:
    frame = pd.read_csv(
        path,
        dtype={
            "concept_id": "string",
            "title": "string",
            "variable": "string",
            "year": "int64",
            "https_url": "string",
            "opendap_url": "string",
            "updated_at": "string",
        },
    )
    rows: list[DaymetGranule] = []
    for row in frame.to_dict("records"):
        updated = row["updated_at"]
        rows.append(
            DaymetGranule(
                concept_id=str(row["concept_id"]),
                title=str(row["title"]),
                variable=str(row["variable"]),
                year=int(row["year"]),
                size_mb=float(row["size_mb"]),
                https_url=str(row["https_url"]),
                opendap_url=str(row["opendap_url"]),
                updated_at=None if pd.isna(updated) else str(updated),
            )
        )
    return rows


def _validate_existing_summary(
    summary: dict[str, Any],
    *,
    manifest_directory: Path,
    inputs: _AuthenticatedInputs,
    weather: dict[str, Any],
    pipeline_sha256: str,
) -> None:
    _verify_commit(summary, label="final-test Daymet grid provenance")
    formal = summary.get("formal_model_lock", {})
    landsat = summary.get("landsat_inventory", {})
    key = summary.get("key_universe", {})
    if (
        summary.get("schema_version") != SCHEMA_VERSION
        or summary.get("algorithm_version") != ALGORITHM_VERSION
        or summary.get("state") not in _ALLOWED_STATES
        or summary.get("final_test_year") != FINAL_TEST_YEAR
        or summary.get("target_blind") is not True
        or summary.get("target_or_qa_tables_read") != []
        or summary.get("target_values_read") is not False
        or summary.get("models_loaded") is not False
        or summary.get("model_scores_read") is not False
        or summary.get("one_time_evaluation_consumed") is not False
        or summary.get("final_test_unlocked") is not False
        or summary.get("pipeline_sha256") != pipeline_sha256
        or summary.get("weather_config_semantic_sha256")
        != weather["semantic_sha256"]
        or formal.get("path") != str(inputs.formal_lock_path)
        or formal.get("sha256") != inputs.formal_lock_file_sha256
        or formal.get("commit_sha256") != inputs.formal_lock.get("commit_sha256")
        or landsat.get("path") != str(inputs.inventory_path)
        or landsat.get("sha256") != inputs.inventory_file_sha256
        or landsat.get("commit_sha256") != inputs.inventory_commit_sha256
        or key.get("path") != str(inputs.key_path)
        or key.get("sha256") != inputs.key_record.get("sha256")
        or summary.get("weather_requirement_semantic_sha256")
        != inputs.requirements.membership_semantic_sha256
        or summary.get("target_date_count") != len(inputs.requirements.target_dates)
        or summary.get("required_weather_date_count")
        != len(inputs.requirements.weather_dates)
        or summary.get("source_years") != list(inputs.requirements.source_years)
    ):
        raise FinalTestDaymetGridError(
            "Existing final-test Daymet stage disagrees with frozen inputs or code."
        )
    outputs = summary.get("output_files")
    if not isinstance(outputs, dict):
        raise FinalTestDaymetGridError("Existing Daymet stage lacks output locks.")
    for filename, record in outputs.items():
        if filename not in {
            GRANULE_INVENTORY_FILENAME,
            WEATHER_REQUIREMENTS_FILENAME,
            SUBSET_DOWNLOADS_FILENAME,
        }:
            raise FinalTestDaymetGridError("Unexpected Daymet stage output file.")
        _validate_file_record(
            manifest_directory / filename,
            record,
            label=f"Daymet output {filename}",
        )


def _write_summary(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    result = _committed(payload)
    atomic_json(result, path)
    return result


def _base_summary(
    *,
    inputs: _AuthenticatedInputs,
    weather: dict[str, Any],
    manifest_directory: Path,
    raw_subset_directory: Path,
    inventory_path: Path,
    inventory_frame: pd.DataFrame,
    requirements_path: Path,
    pipeline_sha256: str,
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "inventory_complete",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "final_test_year": FINAL_TEST_YEAR,
        "final_test_unlocked": False,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "dynamic_observed_predictors_end_by": "target_day_minus_1",
        "required_lag_days": list(REQUIRED_LAG_DAYS),
        "annual_container_contains_unused_dates": True,
        "contains_final_test_predictor_source_year": (
            FINAL_TEST_YEAR in inputs.requirements.source_years
        ),
        "target_date_count": len(inputs.requirements.target_dates),
        "required_weather_date_count": len(inputs.requirements.weather_dates),
        "required_weather_date_min": inputs.requirements.weather_dates[0].isoformat(),
        "required_weather_date_max": inputs.requirements.weather_dates[-1].isoformat(),
        "source_years": list(inputs.requirements.source_years),
        "variables": weather["variables"],
        "granule_count": len(inventory_frame),
        "expected_subset_count": len(inventory_frame),
        "completed_subset_count": 0,
        "manifest_directory": str(manifest_directory),
        "raw_subset_directory": str(raw_subset_directory),
        "dataset": "Daymet: Daily Surface Weather Data on a 1-km Grid for North America, V4 R1",
        "dataset_doi": DAYMET_DOI_URL,
        "cmr_collection_concept_id": DAYMET_CMR_COLLECTION_ID,
        "cmr_granules_url": DAYMET_CMR_GRANULES_URL,
        "cmr_service_bridge_url": DAYMET_CMR_SERVICE_BRIDGE_URL,
        "subset_access_route": DAYMET_DIRECT_DAP4_ROUTE,
        "direct_subset_y_indices": weather["direct_subset_y_indices"],
        "direct_subset_x_indices": weather["direct_subset_x_indices"],
        "bbox_wgs84": weather["bbox_wgs84"],
        "weather_config_semantic_sha256": weather["semantic_sha256"],
        "weather_requirement_semantic_sha256": (
            inputs.requirements.membership_semantic_sha256
        ),
        "formal_model_lock": {
            "path": str(inputs.formal_lock_path),
            "sha256": inputs.formal_lock_file_sha256,
            "commit_sha256": inputs.formal_lock["commit_sha256"],
        },
        "landsat_inventory": {
            "path": str(inputs.inventory_path),
            "sha256": inputs.inventory_file_sha256,
            "commit_sha256": inputs.inventory_commit_sha256,
        },
        "key_universe": {
            "path": str(inputs.key_path),
            "sha256": inputs.key_record["sha256"],
            "bytes": inputs.key_record["bytes"],
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline,
        "output_files": {
            GRANULE_INVENTORY_FILENAME: _csv_record(
                inventory_path, inventory_frame
            ),
            WEATHER_REQUIREMENTS_FILENAME: _csv_record(
                requirements_path, inputs.requirements_frame
            ),
        },
    }


def _destination(
    raw_directory: Path,
    granule: DaymetGranule,
) -> Path:
    return raw_directory / (
        f"daymet_v4r1_daily_na_{granule.variable}_{granule.year}_la_subset.nc"
    )


def _download_frame(records: Mapping[tuple[str, int], dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records.values()).sort_values(
        ["year", "variable"], kind="stable"
    ).reset_index(drop=True)


def _load_download_records(
    path: Path,
    *,
    record: object,
) -> dict[tuple[str, int], dict[str, Any]]:
    if record is None:
        return {}
    _validate_file_record(path, record, label="Daymet subset download manifest")
    frame = pd.read_csv(path)
    required = {
        "concept_id",
        "variable",
        "year",
        "access_route",
        "subset_y_start",
        "subset_y_stop",
        "subset_x_start",
        "subset_x_stop",
        "path",
        "bytes",
        "sha256",
        "source_url",
        "retrieved_on",
        "credential_source",
    }
    if set(frame.columns) != required or frame.duplicated(["variable", "year"]).any():
        raise FinalTestDaymetGridError("Daymet download manifest schema drifted.")
    return {
        (str(row["variable"]), int(row["year"])): dict(row)
        for row in frame.to_dict("records")
    }


def _cached_file_record(path: Path, *, source_url: str) -> dict[str, Any]:
    if not path.is_file() or not daymet_core._has_netcdf_signature(path):
        raise FinalTestDaymetGridError(f"Cached Daymet subset is not NetCDF: {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "source_url": source_url,
        "retrieved_on": None,
        "credential_source": "recovered_valid_atomic_file",
    }


def _normalize_download_record(
    raw: Mapping[str, object],
    *,
    destination: Path,
    source_url: str,
) -> dict[str, Any]:
    if (
        Path(str(raw.get("path", ""))).resolve() != destination.resolve()
        or raw.get("source_url") != source_url
        or raw.get("bytes") != destination.stat().st_size
        or raw.get("sha256") != sha256_file(destination)
        or not isinstance(raw.get("credential_source"), str)
    ):
        raise FinalTestDaymetGridError("Downloaded Daymet subset record is inconsistent.")
    return dict(raw)


def stage_final_test_daymet_grid(
    *,
    config_path: str | Path = "configs/research.toml",
    formal_lock_path: str | Path = "manifests/model_lock/MODEL_LOCK.json",
    landsat_inventory_directory: str | Path = (
        "manifests/final_test_2025/landsat_inventory"
    ),
    manifest_directory: str | Path = DEFAULT_MANIFEST_DIRECTORY,
    raw_subset_directory: str | Path = DEFAULT_RAW_SUBSET_DIRECTORY,
    download_subsets: bool = False,
    credential: EarthdataBearerToken | None = None,
    credential_provider: CredentialProvider | None = None,
    http_client: _HttpClientLike | None = None,
    discoverer: Discoverer | None = None,
    downloader: Downloader = authenticated_netcdf_download,
    inspector: Inspector = inspect_exact_final_test_daymet_netcdf,
) -> dict[str, Any]:
    """Freeze inventory or resume authenticated exact-2025 subset downloads."""

    if not isinstance(download_subsets, bool):
        raise TypeError("download_subsets must be boolean.")
    if credential is not None and credential_provider is not None:
        raise ValueError("Provide either credential or credential_provider, not both.")
    if not download_subsets and (credential is not None or credential_provider is not None):
        raise ValueError("Credentials are prohibited for inventory-only staging.")

    root = _project_root()
    config_file = _resolve(root, config_path)
    formal_file = _resolve(root, formal_lock_path)
    landsat_directory = _resolve(root, landsat_inventory_directory)
    manifest = _resolve(root, manifest_directory)
    raw = _resolve(root, raw_subset_directory)
    if manifest == raw or manifest in raw.parents or raw in manifest.parents:
        raise FinalTestDaymetGridError(
            "Daymet manifest and raw subset directories must be isolated."
        )
    inputs = _authenticate_inventory_and_keys(
        config_path=config_file,
        formal_lock_path=formal_file,
        inventory_directory=landsat_directory,
    )
    weather = _weather_config(inputs.config)
    pipeline_sha256, pipeline = _pipeline_fingerprint()
    summary_path = manifest / PROVENANCE_FILENAME

    if summary_path.exists():
        summary = _read_json(summary_path, label="final-test Daymet grid provenance")
        _validate_existing_summary(
            summary,
            manifest_directory=manifest,
            inputs=inputs,
            weather=weather,
            pipeline_sha256=pipeline_sha256,
        )
        inventory_path = manifest / GRANULE_INVENTORY_FILENAME
        requirements_path = manifest / WEATHER_REQUIREMENTS_FILENAME
        granules = _validate_granules(
            _granules_from_csv(inventory_path),
            requirements=inputs.requirements,
            variables=weather["variables"],
            y_indices=weather["direct_subset_y_indices"],
            x_indices=weather["direct_subset_x_indices"],
        )
        frozen_requirements = pd.read_csv(requirements_path)
        if canonical_frame_sha256(
            frozen_requirements,
            sort_by=["target_date", "lag_days"],
        ) != inputs.requirements.membership_semantic_sha256:
            raise FinalTestDaymetGridError("Frozen weather-date requirements drifted.")
    else:
        if manifest.exists() and any(manifest.iterdir()):
            raise FinalTestDaymetGridError(
                "Partial Daymet manifest exists without a valid commit marker."
            )
        active_discoverer = (
            discover_exact_final_test_daymet_granules
            if discoverer is None
            else discoverer
        )
        granules = active_discoverer(
            inputs.requirements,
            variables=weather["variables"],
            http_client=http_client,
            endpoint=weather["cmr_granules_url"],
        )
        granules = _validate_granules(
            granules,
            requirements=inputs.requirements,
            variables=weather["variables"],
            y_indices=weather["direct_subset_y_indices"],
            x_indices=weather["direct_subset_x_indices"],
        )
        inventory_frame = pd.DataFrame([asdict(value) for value in granules])
        inventory_frame = inventory_frame.sort_values(
            ["year", "variable"], kind="stable"
        ).reset_index(drop=True)
        inventory_path = manifest / GRANULE_INVENTORY_FILENAME
        requirements_path = manifest / WEATHER_REQUIREMENTS_FILENAME
        atomic_csv(inventory_frame, inventory_path)
        atomic_csv(inputs.requirements_frame, requirements_path)
        summary = _base_summary(
            inputs=inputs,
            weather=weather,
            manifest_directory=manifest,
            raw_subset_directory=raw,
            inventory_path=inventory_path,
            inventory_frame=inventory_frame,
            requirements_path=requirements_path,
            pipeline_sha256=pipeline_sha256,
            pipeline=pipeline,
        )
        summary = _write_summary(summary, summary_path)

    if not download_subsets:
        return summary

    summary = dict(summary)
    summary["state"] = "subsets_partial"
    summary["download_subsets_requested"] = True
    summary["updated_at_utc"] = datetime.now(UTC).isoformat()
    summary = _write_summary(summary, summary_path)
    download_path = manifest / SUBSET_DOWNLOADS_FILENAME
    existing_record = summary.get("output_files", {}).get(SUBSET_DOWNLOADS_FILENAME)
    records = _load_download_records(download_path, record=existing_record)
    expected_keys = {(value.variable, value.year) for value in granules}
    if not set(records).issubset(expected_keys):
        raise FinalTestDaymetGridError("Download manifest contains an unexpected subset.")

    y_indices = tuple(int(value) for value in weather["direct_subset_y_indices"])
    x_indices = tuple(int(value) for value in weather["direct_subset_x_indices"])
    specs: dict[tuple[str, int], DaymetNetCDFSpec] = {}
    active_credential = credential

    for position, granule in enumerate(granules, start=1):
        key = (granule.variable, granule.year)
        subset_url = build_daymet_direct_subset_url(
            granule,
            y_indices=y_indices,
            x_indices=x_indices,
        )
        destination = _destination(raw, granule)
        if key in records:
            record = records[key]
            if (
                record.get("concept_id") != granule.concept_id
                or record.get("access_route") != DAYMET_DIRECT_DAP4_ROUTE
                or int(record.get("subset_y_start")) != y_indices[0]
                or int(record.get("subset_y_stop")) != y_indices[1]
                or int(record.get("subset_x_start")) != x_indices[0]
                or int(record.get("subset_x_stop")) != x_indices[1]
                or Path(str(record.get("path", ""))).resolve() != destination.resolve()
                or record.get("source_url") != subset_url
                or int(record.get("bytes")) != destination.stat().st_size
                or record.get("sha256") != sha256_file(destination)
            ):
                raise FinalTestDaymetGridError(
                    f"Committed Daymet subset drifted: {granule.variable} {granule.year}"
                )
            action = "verified cached"
        elif destination.exists():
            record = {
                "concept_id": granule.concept_id,
                "variable": granule.variable,
                "year": granule.year,
                "access_route": DAYMET_DIRECT_DAP4_ROUTE,
                "subset_y_start": y_indices[0],
                "subset_y_stop": y_indices[1],
                "subset_x_start": x_indices[0],
                "subset_x_stop": x_indices[1],
                **_cached_file_record(destination, source_url=subset_url),
            }
            action = "recovered validated atomic file"
        else:
            if active_credential is None:
                if credential_provider is None:
                    raise PermissionError(
                        "Missing Earthdata credential for an incomplete Daymet subset."
                    )
                active_credential = credential_provider()
                if not isinstance(active_credential, EarthdataBearerToken):
                    raise TypeError(
                        "credential_provider must return EarthdataBearerToken."
                    )
            downloaded = downloader(
                subset_url,
                destination,
                credential=active_credential,
                maximum_bytes=weather["maximum_subset_bytes"],
            )
            downloaded = _normalize_download_record(
                downloaded,
                destination=destination,
                source_url=subset_url,
            )
            record = {
                "concept_id": granule.concept_id,
                "variable": granule.variable,
                "year": granule.year,
                "access_route": DAYMET_DIRECT_DAP4_ROUTE,
                "subset_y_start": y_indices[0],
                "subset_y_stop": y_indices[1],
                "subset_x_start": x_indices[0],
                "subset_x_stop": x_indices[1],
                **downloaded,
            }
            action = "downloaded"

        spec = inspector(
            destination,
            variable=granule.variable,
            year=granule.year,
            requirements=inputs.requirements,
        )
        spec = validate_daymet_direct_subset_spec(
            spec,
            y_indices=y_indices,
            x_indices=x_indices,
            bbox_wgs84=weather["bbox_wgs84"],
        )
        specs[key] = spec
        records[key] = dict(record)
        downloads = _download_frame(records)
        atomic_csv(downloads, download_path)
        summary["output_files"][SUBSET_DOWNLOADS_FILENAME] = _csv_record(
            download_path, downloads
        )
        summary["completed_subset_count"] = len(downloads)
        summary["state"] = (
            "subsets_complete"
            if len(downloads) == len(granules)
            else "subsets_partial"
        )
        summary["updated_at_utc"] = datetime.now(UTC).isoformat()
        summary = _write_summary(summary, summary_path)
        print(
            f"Final-test Daymet [{position}/{len(granules)}] {action}: "
            f"{granule.variable} {granule.year}",
            flush=True,
        )

    reference = validate_daymet_netcdf_grid_specs(
        [specs[(value.variable, value.year)] for value in granules]
    )
    summary["state"] = "subsets_complete"
    summary["completed_subset_count"] = len(granules)
    summary["subset_grid_shape"] = list(reference.shape)
    summary["download_manifest_semantic_sha256"] = canonical_frame_sha256(
        _download_frame(records),
        sort_by=["year", "variable"],
    )
    summary["updated_at_utc"] = datetime.now(UTC).isoformat()
    return _write_summary(summary, summary_path)
