"""Audited Daymet single-pixel weather retrieval and lagged feature construction.

Daymet uses an unusual 365-record calendar: leap years include February 29
but omit December 31.  This module maps ``year``/``yday`` onto the Gregorian
calendar, inserts the omitted civil date as missing, and constructs historical
hindcast features from complete calendar windows ending at target day ``d-1``.
Missing source days are never filled.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import pandas as pd
import requests

DAYMET_SINGLE_PIXEL_URL = "https://daymet.ornl.gov/single-pixel/api/data"
DAYMET_DATASET_DOI = "https://doi.org/10.3334/ORNLDAAC/2129"
DAYMET_CACHE_SCHEMA_VERSION = 1
PRIMARY_WINDOWS_DAYS = (1, 3, 7)


class DaymetAuditError(ValueError):
    """Raised when a Daymet response or cached request fails closed auditing."""


@dataclass(frozen=True, slots=True)
class DaymetVariable:
    """Official API field metadata and the canonical parsed column name."""

    response_key: str
    column: str
    units: str
    feature_stem: str
    aggregation: Literal["mean", "sum"]
    feature_units: str


DAYMET_VARIABLES: dict[str, DaymetVariable] = {
    "dayl": DaymetVariable(
        response_key="dayl (s)",
        column="dayl_s",
        units="s/day",
        feature_stem="dayl_s",
        aggregation="mean",
        feature_units="s/day",
    ),
    "prcp": DaymetVariable(
        response_key="prcp (mm/day)",
        column="prcp_mm_day",
        units="mm/day",
        feature_stem="prcp_mm",
        aggregation="sum",
        feature_units="mm",
    ),
    "srad": DaymetVariable(
        response_key="srad (W/m^2)",
        column="srad_w_m2",
        units="W/m^2",
        feature_stem="srad_w_m2",
        aggregation="mean",
        feature_units="W/m^2",
    ),
    "swe": DaymetVariable(
        response_key="swe (kg/m^2)",
        column="swe_kg_m2",
        units="kg/m^2",
        feature_stem="swe_kg_m2",
        aggregation="mean",
        feature_units="kg/m^2",
    ),
    "tmax": DaymetVariable(
        response_key="tmax (deg c)",
        column="tmax_c",
        units="degrees C",
        feature_stem="tmax_c",
        aggregation="mean",
        feature_units="degrees C",
    ),
    "tmin": DaymetVariable(
        response_key="tmin (deg c)",
        column="tmin_c",
        units="degrees C",
        feature_stem="tmin_c",
        aggregation="mean",
        feature_units="degrees C",
    ),
    "vp": DaymetVariable(
        response_key="vp (Pa)",
        column="vp_pa",
        units="Pa",
        feature_stem="vp_pa",
        aggregation="mean",
        feature_units="Pa",
    ),
}

DEFAULT_DAYMET_VARIABLES = ("tmax", "tmin", "prcp", "srad", "vp", "dayl")

DERIVED_SRAD_ENERGY_COLUMN = "srad_energy_mj_m2_day"
DERIVED_SRAD_ENERGY_UNITS = "MJ/m^2/day"


class _ResponseLike(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _HttpClientLike(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        timeout: tuple[float, float] | float,
    ) -> _ResponseLike: ...


def _normalized_years(years: Sequence[int]) -> tuple[int, ...]:
    if isinstance(years, (str, bytes)) or not years:
        raise ValueError("Daymet years must be a non-empty sequence of integers.")
    normalized: list[int] = []
    for year in years:
        if isinstance(year, bool) or not isinstance(year, (int, np.integer)):
            raise TypeError("Every Daymet year must be an integer.")
        normalized.append(int(year))
    if len(set(normalized)) != len(normalized):
        raise ValueError("Daymet years must be unique.")
    return tuple(sorted(normalized))


def _normalized_variables(variables: Sequence[str]) -> tuple[str, ...]:
    if isinstance(variables, (str, bytes)) or not variables:
        raise ValueError("Daymet variables must be a non-empty sequence.")
    requested = tuple(str(variable).lower() for variable in variables)
    if len(set(requested)) != len(requested):
        raise ValueError("Daymet variables must be unique.")
    unknown = sorted(set(requested).difference(DAYMET_VARIABLES))
    if unknown:
        raise ValueError(f"Unknown Daymet variables: {unknown}")
    return tuple(variable for variable in DAYMET_VARIABLES if variable in requested)


def _numeric_series(values: object, *, field: str, expected_length: int) -> pd.Series:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise DaymetAuditError(f"Daymet field {field!r} must be an array.")
    if len(values) != expected_length:
        raise DaymetAuditError(
            f"Daymet field {field!r} has {len(values)} rows; expected {expected_length}."
        )
    if any(isinstance(value, bool) for value in values):
        raise DaymetAuditError(f"Daymet field {field!r} contains a boolean value.")
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise DaymetAuditError(f"Daymet field {field!r} contains non-finite data.")
    return numeric


def parse_single_pixel_json(
    payload: Mapping[str, object],
    *,
    expected_years: Sequence[int],
    expected_variables: Sequence[str] = DEFAULT_DAYMET_VARIABLES,
) -> pd.DataFrame:
    """Parse and audit an official Daymet Single Pixel JSON response.

    The response must contain exactly 365 rows for every requested year, all
    ``yday`` values 1 through 365 exactly once, and exactly the requested
    weather variables.  The returned ``date`` uses the Gregorian calendar, so
    leap-year ``yday=60`` is February 29 and ``yday=365`` is December 30.
    """

    years = _normalized_years(expected_years)
    variables = _normalized_variables(expected_variables)
    if not isinstance(payload, Mapping):
        raise DaymetAuditError("Daymet JSON payload must be an object.")
    raw_data = payload.get("data")
    if not isinstance(raw_data, Mapping):
        raise DaymetAuditError("Daymet JSON payload is missing its data object.")

    expected_fields = {
        "year",
        "yday",
        *(DAYMET_VARIABLES[variable].response_key for variable in variables),
    }
    actual_fields = set(raw_data)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields.difference(actual_fields))
        unexpected = sorted(actual_fields.difference(expected_fields))
        raise DaymetAuditError(
            "Daymet response variables do not match the request; "
            f"missing={missing}, unexpected={unexpected}."
        )

    expected_length = 365 * len(years)
    parsed: dict[str, pd.Series] = {}
    for field in sorted(expected_fields):
        parsed[field] = _numeric_series(
            raw_data[field], field=field, expected_length=expected_length
        )
    frame = pd.DataFrame(parsed)

    for field in ("year", "yday"):
        values = frame[field].to_numpy(dtype=float)
        if not np.equal(values, np.floor(values)).all():
            raise DaymetAuditError(f"Daymet {field} values must be integers.")
        frame[field] = values.astype(np.int32)

    actual_years = tuple(sorted(frame["year"].unique().tolist()))
    if actual_years != years:
        raise DaymetAuditError(
            f"Daymet response years {actual_years} do not match requested years {years}."
        )
    if frame.duplicated(["year", "yday"]).any():
        raise DaymetAuditError("Daymet response has duplicate year/yday records.")

    expected_yday = np.arange(1, 366, dtype=np.int32)
    for year in years:
        observed = np.sort(frame.loc[frame["year"] == year, "yday"].to_numpy())
        if len(observed) != 365 or not np.array_equal(observed, expected_yday):
            raise DaymetAuditError(
                f"Daymet year {year} must contain yday 1 through 365 exactly once."
            )

    year_start = pd.to_datetime(frame["year"].astype(str) + "-01-01")
    frame["date"] = year_start + pd.to_timedelta(frame["yday"] - 1, unit="D")
    if frame["date"].duplicated().any():
        raise DaymetAuditError("Daymet year/yday values map to duplicate civil dates.")

    for year in years:
        year_dates = frame.loc[frame["year"] == year, "date"]
        leap = pd.Timestamp(year=year, month=1, day=1).is_leap_year
        expected_last = pd.Timestamp(year=year, month=12, day=30 if leap else 31)
        if year_dates.max() != expected_last:
            raise DaymetAuditError(f"Daymet year {year} has an invalid final civil date.")
        if leap:
            if pd.Timestamp(year=year, month=2, day=29) not in set(year_dates):
                raise DaymetAuditError(f"Daymet leap year {year} is missing February 29.")
            if pd.Timestamp(year=year, month=12, day=31) in set(year_dates):
                raise DaymetAuditError(f"Daymet leap year {year} must omit December 31.")

    canonical_columns: list[str] = []
    unit_registry: dict[str, str] = {}
    for variable in variables:
        definition = DAYMET_VARIABLES[variable]
        frame[definition.column] = frame.pop(definition.response_key).astype(float)
        canonical_columns.append(definition.column)
        unit_registry[definition.column] = definition.units

    if "srad" in variables and "dayl" in variables:
        frame[DERIVED_SRAD_ENERGY_COLUMN] = (
            frame[DAYMET_VARIABLES["srad"].column]
            * frame[DAYMET_VARIABLES["dayl"].column]
            / 1_000_000.0
        )
        canonical_columns.append(DERIVED_SRAD_ENERGY_COLUMN)
        unit_registry[DERIVED_SRAD_ENERGY_COLUMN] = DERIVED_SRAD_ENERGY_UNITS

    nonnegative_variables = {
        "dayl": ("dayl_s", 86_400.0),
        "prcp": ("prcp_mm_day", None),
        "srad": ("srad_w_m2", None),
        "swe": ("swe_kg_m2", None),
        "vp": ("vp_pa", None),
    }
    for variable, (column, maximum) in nonnegative_variables.items():
        if variable not in variables:
            continue
        if (frame[column] < 0).any():
            raise DaymetAuditError(f"Daymet variable {variable} contains negative values.")
        if maximum is not None and (frame[column] > maximum).any():
            raise DaymetAuditError(
                f"Daymet variable {variable} exceeds its physical maximum {maximum}."
            )
    if {"tmax", "tmin"}.issubset(variables) and (
        frame["tmax_c"] < frame["tmin_c"]
    ).any():
        raise DaymetAuditError("Daymet tmax cannot be lower than tmin.")

    frame = frame[["date", "year", "yday", *canonical_columns]].sort_values(
        "date", kind="stable"
    )
    frame = frame.reset_index(drop=True)
    frame.attrs = {
        "source": "Daymet V4 R1 Single Pixel Extraction Tool",
        "source_url": DAYMET_SINGLE_PIXEL_URL,
        "dataset_doi": DAYMET_DATASET_DOI,
        "requested_years": years,
        "requested_variables": variables,
        "units": unit_registry,
        "location": payload.get("loc"),
        "tile": payload.get("Tile"),
        "elevation": payload.get("Elevation"),
        "lcc": payload.get("LCC"),
        "citation": payload.get("citation"),
    }
    return frame


def _civil_timestamp(value: str | pd.Timestamp, *, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tz is not None:
        raise ValueError(f"{name} must be a timezone-naive local civil date.")
    if timestamp != timestamp.normalize():
        raise ValueError(f"{name} must be normalized to local midnight.")
    return timestamp


def reindex_complete_calendar(
    daily: pd.DataFrame,
    *,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Reindex audited Daymet rows to a dense Gregorian daily calendar.

    Leap-year December 31 and any other absent source date remain missing.
    No interpolation, forward fill, or backward fill is performed.
    """

    required = {"date", "year", "yday"}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"Daymet daily frame is missing columns: {sorted(missing)}")
    if daily.empty:
        raise ValueError("Daymet daily frame cannot be empty.")

    source = daily.copy()
    source["date"] = pd.to_datetime(source["date"], errors="raise")
    if source["date"].dt.tz is not None:
        raise ValueError("Daymet daily dates must be timezone-naive local civil dates.")
    if not source["date"].dt.normalize().equals(source["date"]):
        raise ValueError("Daymet daily dates must be normalized civil dates.")
    if source["date"].duplicated().any():
        raise ValueError("Daymet daily dates must be unique before reindexing.")

    first_year = int(source["date"].dt.year.min())
    last_year = int(source["date"].dt.year.max())
    calendar_start = (
        pd.Timestamp(year=first_year, month=1, day=1)
        if start is None
        else _civil_timestamp(start, name="Complete-calendar start")
    )
    calendar_end = (
        pd.Timestamp(year=last_year, month=12, day=31)
        if end is None
        else _civil_timestamp(end, name="Complete-calendar end")
    )
    if calendar_start > calendar_end:
        raise ValueError("Complete-calendar start must not be after end.")

    attributes = dict(daily.attrs)
    dense = source.set_index("date").sort_index().reindex(
        pd.date_range(calendar_start, calendar_end, freq="D", name="date")
    )
    dense["year"] = dense["year"].astype("Int64")
    dense["yday"] = dense["yday"].astype("Int64")
    dense.insert(2, "daymet_source_present", dense["year"].notna())
    structural_gap = (
        dense.index.is_leap_year
        & (dense.index.month == 12)
        & (dense.index.day == 31)
        & ~dense["daymet_source_present"].to_numpy()
    )
    dense.insert(3, "daymet_structural_calendar_gap", structural_gap)
    dense.attrs = attributes
    dense.attrs["calendar_start"] = calendar_start.isoformat()
    dense.attrs["calendar_end"] = calendar_end.isoformat()
    dense.attrs["missing_civil_dates"] = tuple(
        timestamp.isoformat()
        for timestamp in dense.index[~dense["daymet_source_present"]]
    )
    return dense


def _normalized_windows(windows: Sequence[int]) -> tuple[int, ...]:
    if isinstance(windows, (str, bytes)) or not windows:
        raise ValueError("Rolling windows must be a non-empty sequence.")
    normalized: list[int] = []
    for window in windows:
        if isinstance(window, bool) or not isinstance(window, (int, np.integer)):
            raise TypeError("Every rolling window must be an integer number of days.")
        if int(window) <= 0:
            raise ValueError("Rolling windows must be positive.")
        normalized.append(int(window))
    if len(set(normalized)) != len(normalized):
        raise ValueError("Rolling windows must be unique.")
    return tuple(sorted(normalized))


def build_lagged_features(
    daily: pd.DataFrame,
    *,
    windows: Sequence[int] = PRIMARY_WINDOWS_DAYS,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build fail-closed Daymet features for target dates.

    For a target date ``d``, an ``n``-day feature uses exactly the complete
    civil dates ``d-n`` through ``d-1``.  ``shift(1)`` is applied before every
    rolling operation, and ``min_periods=n`` makes any missing source day
    invalidate the entire feature window.
    """

    normalized_windows = _normalized_windows(windows)
    dense = reindex_complete_calendar(daily, start=start, end=end)

    column_definitions: dict[str, tuple[str, Literal["mean", "sum"], str]] = {}
    for definition in DAYMET_VARIABLES.values():
        if definition.column in dense.columns:
            column_definitions[definition.column] = (
                definition.feature_stem,
                definition.aggregation,
                definition.feature_units,
            )
    if DERIVED_SRAD_ENERGY_COLUMN in dense.columns:
        column_definitions[DERIVED_SRAD_ENERGY_COLUMN] = (
            "srad_energy_mj_m2",
            "sum",
            "MJ/m^2",
        )
    if not column_definitions:
        raise ValueError("Daymet daily frame has no recognized weather columns.")

    lagged = dense[list(column_definitions)].shift(1)
    features = pd.DataFrame(index=dense.index.copy())
    features.index.name = "target_date"
    feature_units: dict[str, str] = {}
    for window in normalized_windows:
        for column, (stem, aggregation, units) in column_definitions.items():
            rolling = lagged[column].rolling(window=window, min_periods=window)
            values = rolling.mean() if aggregation == "mean" else rolling.sum()
            feature_name = f"daymet_{stem}_{aggregation}_prev_{window}d"
            features[feature_name] = values.to_numpy()
            feature_units[feature_name] = units

    features.attrs = {
        "source": dense.attrs.get("source"),
        "source_url": dense.attrs.get("source_url"),
        "dataset_doi": dense.attrs.get("dataset_doi"),
        "units": feature_units,
        "windows_days": normalized_windows,
        "window_definition": "complete civil days d-n through d-1",
        "target_day_observations_included": False,
        "missing_days_filled": False,
    }
    return features


def _request_descriptor(
    *,
    latitude: float,
    longitude: float,
    years: tuple[int, ...],
    variables: tuple[str, ...],
    endpoint: str,
) -> dict[str, object]:
    if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("Latitude must be finite and between -90 and 90 degrees.")
    if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise ValueError("Longitude must be finite and between -180 and 180 degrees.")
    return {
        "endpoint": endpoint,
        "latitude": latitude,
        "longitude": longitude,
        "years": list(years),
        "variables": list(variables),
        "format": "json",
    }


def _read_cache(path: Path, *, expected_request: Mapping[str, object]) -> Mapping[str, object]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DaymetAuditError(f"Cannot read Daymet cache {path}.") from error
    if not isinstance(envelope, Mapping):
        raise DaymetAuditError("Daymet cache must contain a JSON object.")
    if envelope.get("cache_schema_version") != DAYMET_CACHE_SCHEMA_VERSION:
        raise DaymetAuditError("Daymet cache schema version does not match.")
    if envelope.get("request") != expected_request:
        raise DaymetAuditError("Daymet cache request does not match the requested query.")
    response = envelope.get("response")
    if not isinstance(response, Mapping):
        raise DaymetAuditError("Daymet cache is missing the raw response object.")
    if envelope.get("response_sha256") != _response_sha256(response):
        raise DaymetAuditError("Daymet cached response hash does not match its content.")
    return response


def _response_sha256(response: Mapping[str, object]) -> str:
    encoded = json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_cache(
    path: Path,
    *,
    request_descriptor: Mapping[str, object],
    response: Mapping[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    envelope = {
        "cache_schema_version": DAYMET_CACHE_SCHEMA_VERSION,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "request": dict(request_descriptor),
        "response_sha256": _response_sha256(response),
        "response": response,
    }
    temporary.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_single_pixel_json(
    *,
    latitude: float,
    longitude: float,
    years: Sequence[int],
    variables: Sequence[str] = DEFAULT_DAYMET_VARIABLES,
    cache_path: str | Path | None = None,
    force_refresh: bool = False,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 180.0),
    endpoint: str = DAYMET_SINGLE_PIXEL_URL,
) -> Mapping[str, object]:
    """Fetch or load an audited official Single Pixel JSON response.

    When ``cache_path`` is provided, the cache stores the unmodified response
    together with the exact normalized request.  A mismatched or malformed
    cache fails closed instead of being silently reused.
    """

    normalized_years = _normalized_years(years)
    normalized_variables = _normalized_variables(variables)
    descriptor = _request_descriptor(
        latitude=float(latitude),
        longitude=float(longitude),
        years=normalized_years,
        variables=normalized_variables,
        endpoint=endpoint,
    )
    path = None if cache_path is None else Path(cache_path)
    if path is not None and path.exists() and not force_refresh:
        payload = _read_cache(path, expected_request=descriptor)
        parse_single_pixel_json(
            payload,
            expected_years=normalized_years,
            expected_variables=normalized_variables,
        )
        return payload

    client: _HttpClientLike = requests if http_client is None else http_client
    response = client.get(
        endpoint,
        params={
            "lat": descriptor["latitude"],
            "lon": descriptor["longitude"],
            "years": ",".join(str(year) for year in normalized_years),
            "vars": ",".join(normalized_variables),
            "format": "json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        decoded = response.json()
    except ValueError as error:
        raise DaymetAuditError("Daymet API response is not valid JSON.") from error
    if not isinstance(decoded, Mapping):
        raise DaymetAuditError("Daymet API response must be a JSON object.")

    parse_single_pixel_json(
        decoded,
        expected_years=normalized_years,
        expected_variables=normalized_variables,
    )
    if path is not None:
        _write_cache(path, request_descriptor=descriptor, response=decoded)
    return decoded


def load_single_pixel_daily(
    *,
    latitude: float,
    longitude: float,
    years: Sequence[int],
    variables: Sequence[str] = DEFAULT_DAYMET_VARIABLES,
    cache_path: str | Path | None = None,
    force_refresh: bool = False,
    http_client: _HttpClientLike | None = None,
    timeout: tuple[float, float] | float = (30.0, 180.0),
) -> pd.DataFrame:
    """Fetch/cache, audit, and parse one Daymet grid cell's daily data."""

    payload = fetch_single_pixel_json(
        latitude=latitude,
        longitude=longitude,
        years=years,
        variables=variables,
        cache_path=cache_path,
        force_refresh=force_refresh,
        http_client=http_client,
        timeout=timeout,
    )
    return parse_single_pixel_json(
        payload,
        expected_years=years,
        expected_variables=variables,
    )
