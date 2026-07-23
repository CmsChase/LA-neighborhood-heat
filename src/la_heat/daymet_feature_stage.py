"""Compile authenticated Daymet subsets into target-blind tract-date features."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

import numpy as np
import pandas as pd
import rasterio

from la_heat.config import ResearchConfig, load_config
from la_heat.daymet_grid import (
    DAYMET_DIRECT_DAP4_ROUTE,
    DAYMET_DOI_URL,
    DAYMET_TOKEN_ENVIRONMENT_VARIABLES,
    DaymetGranule,
    DaymetGridAuditError,
    DaymetNetCDFSpec,
    aggregate_daymet_cells_to_tract_daily,
    build_daymet_direct_subset_url,
    build_fixed_eligible_cell_weights,
    build_lagged_tract_daymet_features,
    inspect_daymet_netcdf,
    read_daymet_netcdf_cells,
    validate_daymet_netcdf_grid_specs,
)
from la_heat.feature_universe import KEY_COLUMNS, validate_feature_key_universe
from la_heat.phase2_registry import (
    DAYMET_FEATURE_COUNT,
    LOCKED_DAYMET_VARIABLES,
    daymet_feature_registry_rows,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import daymet_grid_config_sha256
from la_heat.static_features import TargetSupport, load_target_support

DAYMET_FEATURE_STAGE_SCHEMA_VERSION = 2
DAYMET_FEATURE_STAGE_ALGORITHM_VERSION = "daymet-fixed-support-lagged-features-v2"
DAYMET_FEATURE_STAGE_STATUS = "complete"
DAYMET_FEATURE_FILENAME = "daymet_features.parquet"
DAYMET_AUDIT_FILENAME = "daymet_feature_audit.parquet"
DAYMET_WEIGHTS_FILENAME = "daymet_fixed_cell_weights.parquet"
DAYMET_PROVENANCE_FILENAME = "daymet_features_provenance.json"
DEFAULT_FEATURE_UNIVERSE_PATH = Path(
    "data/interim/features/feature_key_universe/feature_key_universe.parquet"
)
DEFAULT_OUTPUT_DIRECTORY = Path("data/interim/features/daymet")
EXPECTED_PRODUCTION_ROWS = 98_640
EXPECTED_PRODUCTION_DATES = 90
EXPECTED_PRODUCTION_TRACTS = 1_096
DAYMET_PRIMARY_WINDOWS = (1, 3, 7)
DAYMET_ALLOWED_CREDENTIAL_SOURCES = frozenset(
    (
        *DAYMET_TOKEN_ENVIRONMENT_VARIABLES,
        "interactive_prompt",
        "not_reloaded_from_cache",
    )
)
DAYMET_WINDOW_AUDIT_FIELDS = (
    "source_start_date",
    "source_end_date",
    "source_days_expected",
    "source_days_complete",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "authorization", "auth", "echo-token", "token"}
)


@dataclass(frozen=True, slots=True)
class DaymetCompilation:
    """In-memory feature, audit, and fixed-support products."""

    features: pd.DataFrame
    audit: pd.DataFrame
    weights: pd.DataFrame
    tract_daily: pd.DataFrame
    specs: tuple[DaymetNetCDFSpec, ...]


def _stable_parquet_keys(path: Path, *, final_test_year: int) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Feature key universe does not exist: {path}")
    before = sha256_file(path)
    try:
        keys = pd.read_parquet(path, columns=list(KEY_COLUMNS))
    except (KeyError, TypeError, ValueError) as error:
        raise DaymetGridAuditError(
            "Feature universe lacks the exact target-blind key columns."
        ) from error
    after = sha256_file(path)
    if before != after:
        raise RuntimeError("Feature key universe changed while being read.")
    validate_feature_key_universe(keys, final_test_year=final_test_year)
    return keys, before


def _validate_key_grid(
    keys: pd.DataFrame,
    *,
    tract_geoids: Sequence[str],
    final_test_year: int,
) -> tuple[pd.DataFrame, tuple[pd.Timestamp, ...]]:
    if list(keys.columns) != list(KEY_COLUMNS):
        raise DaymetGridAuditError(
            "Daymet key universe must contain only tract_geoid,target_date."
        )
    checked = keys.copy()
    checked["tract_geoid"] = checked["tract_geoid"].astype("str")
    checked["target_date"] = pd.to_datetime(checked["target_date"], errors="raise")
    if (
        checked.empty
        or checked.duplicated(list(KEY_COLUMNS)).any()
        or checked["target_date"].dt.tz is not None
        or not checked["target_date"].dt.normalize().equals(checked["target_date"])
    ):
        raise DaymetGridAuditError("Daymet feature keys are empty, duplicate, or non-civil.")
    if checked["target_date"].dt.year.ge(final_test_year).any():
        raise PermissionError(f"Daymet feature keys include locked year {final_test_year}.")
    normalized_geoids = tuple(str(value) for value in tract_geoids)
    if not normalized_geoids or len(set(normalized_geoids)) != len(normalized_geoids):
        raise ValueError("Daymet tract GEOIDs must be non-empty and unique.")
    if set(checked["tract_geoid"].astype(str)) != set(normalized_geoids):
        raise DaymetGridAuditError("Daymet keys do not match the fixed tract support.")
    dates = tuple(sorted(pd.Timestamp(value) for value in checked["target_date"].unique()))
    expected = pd.MultiIndex.from_product(
        [normalized_geoids, dates], names=list(KEY_COLUMNS)
    )
    actual = pd.MultiIndex.from_frame(checked.loc[:, list(KEY_COLUMNS)])
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise DaymetGridAuditError(
            "Daymet keys must be the complete fixed tract x target-date product."
        )
    return checked, dates


def _validate_subset_records(
    records: pd.DataFrame,
    *,
    target_dates: Sequence[pd.Timestamp],
    windows: Sequence[int],
    final_test_year: int,
) -> pd.DataFrame:
    required = {"path", "variable", "year"}
    if missing := required.difference(records.columns):
        raise ValueError(f"Daymet subset records lack columns: {sorted(missing)}")
    checked = records.loc[:, ["path", "variable", "year"]].copy()
    if checked.empty:
        raise DaymetGridAuditError("No Daymet subset files were supplied.")
    checked["variable"] = checked["variable"].astype(str).str.lower()
    raw_years = checked["year"]
    if raw_years.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise DaymetGridAuditError("Daymet subset years cannot be booleans.")
    numeric_years = pd.to_numeric(raw_years, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(numeric_years).all() or not np.equal(
        numeric_years, np.floor(numeric_years)
    ).all():
        raise DaymetGridAuditError("Daymet subset years must be finite integers.")
    checked["year"] = numeric_years.astype(int)
    if checked.duplicated(["year", "variable"]).any():
        raise DaymetGridAuditError("Daymet subset records contain duplicate variable-years.")
    if checked["year"].ge(final_test_year).any():
        raise PermissionError(f"Daymet subset records include locked year {final_test_year}.")
    if set(checked["variable"]) != set(LOCKED_DAYMET_VARIABLES):
        raise DaymetGridAuditError(
            "Daymet subset variables do not match the six predeclared weather inputs."
        )
    by_year = checked.groupby("year", sort=True)["variable"].agg(set)
    if not by_year.map(lambda values: values == set(LOCKED_DAYMET_VARIABLES)).all():
        raise DaymetGridAuditError("Every Daymet year must contain all six variables.")
    maximum_window = max(int(value) for value in windows)
    source_start = min(target_dates) - pd.Timedelta(days=maximum_window)
    source_end = max(target_dates) - pd.Timedelta(days=1)
    required_years = set(range(source_start.year, source_end.year + 1))
    if set(by_year.index.astype(int)) != required_years:
        raise DaymetGridAuditError(
            "Daymet variable-years do not exactly cover the required lag-source years: "
            f"expected={sorted(required_years)}, found={sorted(by_year.index.astype(int))}."
        )
    checked["path"] = checked["path"].map(Path)
    return checked.sort_values(["year", "variable"], kind="stable").reset_index(drop=True)


def _same_cell_date_keys(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    columns = ["daymet_cell_id", "date"]
    return left.loc[:, columns].equals(right.loc[:, columns])


def _compile_tract_daily(
    records: pd.DataFrame,
    *,
    specs: dict[tuple[int, str], DaymetNetCDFSpec],
    cells: pd.DataFrame,
    weights: pd.DataFrame,
    source_start: pd.Timestamp,
    source_end: pd.Timestamp,
    final_test_year: int,
) -> pd.DataFrame:
    annual: list[pd.DataFrame] = []
    for year, group in records.groupby("year", sort=True):
        decoded: pd.DataFrame | None = None
        for row in group.itertuples(index=False):
            spec = specs[(int(year), str(row.variable))]
            variable_frame = read_daymet_netcdf_cells(spec, cells=cells)
            if decoded is None:
                decoded = variable_frame
            else:
                if not _same_cell_date_keys(decoded, variable_frame):
                    raise DaymetGridAuditError(
                        f"Daymet variables disagree on cell-date keys for {year}."
                    )
                value_column = next(
                    column
                    for column in variable_frame.columns
                    if column not in {"daymet_cell_id", "date"}
                )
                decoded[value_column] = variable_frame[value_column].to_numpy()
        assert decoded is not None
        decoded = decoded.loc[
            decoded["date"].between(source_start, source_end, inclusive="both")
        ].reset_index(drop=True)
        if not decoded.empty:
            annual.append(
                aggregate_daymet_cells_to_tract_daily(
                    decoded,
                    weights,
                    final_test_year=final_test_year,
                )
            )
    if not annual:
        raise DaymetGridAuditError("Daymet source window contains no decoded daily values.")
    daily = pd.concat(annual, ignore_index=True).sort_values(
        ["tract_geoid", "date"], kind="stable"
    )
    if daily.duplicated(["tract_geoid", "date"]).any():
        raise AssertionError("Compiled Daymet tract-day keys are not unique.")
    return daily.reset_index(drop=True)


def _build_feature_audit(
    lagged: pd.DataFrame,
    tract_daily: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    target_dates: Sequence[pd.Timestamp],
    windows: Sequence[int],
) -> pd.DataFrame:
    normalized_windows = tuple(sorted(int(window) for window in windows))
    if normalized_windows != DAYMET_PRIMARY_WINDOWS:
        raise DaymetGridAuditError(
            "Feature-audit windows must remain locked to 1, 3, and 7 days."
        )
    maximum_window = max(normalized_windows)
    weight_summary = weights.groupby("tract_geoid", sort=True).agg(
        daymet_grid_cells_expected=("daymet_cell_id", "nunique"),
        daymet_static_eligible_area_m2=("static_denominator_m2", "first"),
    )
    records: list[dict[str, object]] = []
    for geoid, group in tract_daily.groupby("tract_geoid", sort=True):
        indexed = group.set_index("date").sort_index()
        if not indexed.index.is_unique:
            raise DaymetGridAuditError(f"Tract {geoid} has duplicate Daymet daily dates.")
        expected_cells = int(weight_summary.loc[str(geoid), "daymet_grid_cells_expected"])
        static_area = float(
            weight_summary.loc[str(geoid), "daymet_static_eligible_area_m2"]
        )
        for target_date in target_dates:
            end = target_date - pd.Timedelta(days=1)
            record: dict[str, object] = {
                "tract_geoid": str(geoid),
                "target_date": target_date,
                "daymet_grid_cells_expected": expected_cells,
                "daymet_static_eligible_area_m2": static_area,
            }
            for window_days in normalized_windows:
                start = target_date - pd.Timedelta(days=window_days)
                window = indexed.reindex(pd.date_range(start, end, freq="D"))
                present = pd.to_numeric(
                    window["daymet_grid_cells_present"], errors="coerce"
                )
                complete_days = int(present.eq(expected_cells).sum())
                suffix = f"prev_{window_days}d"
                record[f"daymet_source_start_date_{suffix}"] = start
                record[f"daymet_source_end_date_{suffix}"] = end
                record[f"daymet_source_days_expected_{suffix}"] = window_days
                record[f"daymet_source_days_complete_{suffix}"] = complete_days
                if window_days == maximum_window:
                    record["daymet_source_start_date"] = start
                    record["daymet_source_end_date"] = end
                    record["daymet_source_days_expected"] = window_days
                    record["daymet_source_days_complete"] = complete_days
                    record["daymet_grid_cells_present_min"] = (
                        int(present.min()) if present.notna().all() else 0
                    )
            records.append(record)
    audit = pd.DataFrame(records)
    availability = lagged.loc[
        :, [*KEY_COLUMNS, "daymet_all_primary_windows_complete"]
    ]
    audit = audit.merge(
        availability,
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
    )
    window_complete = pd.concat(
        [
            audit[f"daymet_source_days_complete_prev_{window_days}d"].eq(
                window_days
            )
            for window_days in normalized_windows
        ],
        axis=1,
    ).all(axis=1)
    if not window_complete.equals(audit["daymet_all_primary_windows_complete"]):
        raise AssertionError(
            "Daymet lag availability disagrees with its complete fixed-cell source window."
        )
    if not (audit["daymet_source_end_date"] < audit["target_date"]).all():
        raise AssertionError("Daymet audit lineage reaches the target day.")
    return audit.sort_values(list(KEY_COLUMNS), kind="stable").reset_index(drop=True)


def compile_daymet_feature_tables(
    subset_records: pd.DataFrame,
    key_universe: pd.DataFrame,
    *,
    zone_raster: np.ndarray,
    eligible_land_mask: np.ndarray,
    tract_geoids: Sequence[str],
    target_transform: rasterio.Affine,
    target_crs: object,
    windows: Sequence[int] = (1, 3, 7),
    final_test_year: int = 2025,
) -> DaymetCompilation:
    """Compile real Daymet subsets without reading any target or QA values."""

    if isinstance(windows, (str, bytes)) or any(
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        for value in windows
    ):
        raise TypeError("Primary Daymet windows must be integer day counts.")
    normalized_windows = tuple(sorted(int(value) for value in windows))
    if normalized_windows != DAYMET_PRIMARY_WINDOWS:
        raise DaymetGridAuditError("Primary Daymet windows are locked to 1, 3, and 7 days.")
    keys, target_dates = _validate_key_grid(
        key_universe,
        tract_geoids=tract_geoids,
        final_test_year=final_test_year,
    )
    records = _validate_subset_records(
        subset_records,
        target_dates=target_dates,
        windows=normalized_windows,
        final_test_year=final_test_year,
    )
    inspected: dict[tuple[int, str], DaymetNetCDFSpec] = {}
    for row in records.itertuples(index=False):
        spec = inspect_daymet_netcdf(
            row.path,
            variable=str(row.variable),
            year=int(row.year),
            final_test_year=final_test_year,
        )
        inspected[(int(row.year), str(row.variable))] = spec
    specs = tuple(inspected[key] for key in sorted(inspected))
    reference = validate_daymet_netcdf_grid_specs(specs)

    weights = build_fixed_eligible_cell_weights(
        zone_raster=zone_raster,
        eligible_land_mask=eligible_land_mask,
        tract_geoids=tract_geoids,
        target_transform=target_transform,
        target_crs=target_crs,
        daymet_transform=reference.transform,
        daymet_crs=reference.crs_wkt,
        daymet_shape=reference.shape,
    )
    target_pixel_area = abs(
        target_transform.a * target_transform.e
        - target_transform.b * target_transform.d
    )
    expected_counts = np.bincount(
        np.asarray(zone_raster)[
            np.asarray(eligible_land_mask, dtype=bool) & (np.asarray(zone_raster) > 0)
        ],
        minlength=len(tuple(tract_geoids)) + 1,
    )[1:]
    denominators = (
        weights.groupby("tract_geoid", sort=False)["static_denominator_m2"]
        .first()
        .reindex([str(value) for value in tract_geoids])
        .to_numpy(dtype=float)
    )
    if not np.allclose(
        denominators, expected_counts * target_pixel_area, rtol=0, atol=1e-6
    ):
        raise AssertionError("Daymet fixed weights changed the eligible-land denominator.")

    cells = weights.loc[
        :, ["daymet_cell_id", "daymet_row", "daymet_col"]
    ].drop_duplicates("daymet_cell_id")
    maximum_window = max(normalized_windows)
    source_start = min(target_dates) - pd.Timedelta(days=maximum_window)
    source_end = max(target_dates) - pd.Timedelta(days=1)
    tract_daily = _compile_tract_daily(
        records,
        specs=inspected,
        cells=cells,
        weights=weights,
        source_start=source_start,
        source_end=source_end,
        final_test_year=final_test_year,
    )
    lagged = build_lagged_tract_daymet_features(
        tract_daily,
        target_dates=target_dates,
        windows=normalized_windows,
        final_test_year=final_test_year,
    )
    feature_names = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))
    if len(feature_names) != DAYMET_FEATURE_COUNT or not set(feature_names).issubset(lagged):
        raise AssertionError("Daymet compiler did not produce the exact 21-feature contract.")
    lagged_features = lagged.loc[:, [*KEY_COLUMNS, *feature_names]]
    features = keys.merge(
        lagged_features,
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    )
    features["tract_geoid"] = features["tract_geoid"].astype("str")
    numeric = features.loc[:, feature_names].to_numpy(dtype=float)
    if np.isinf(numeric).any():
        raise DaymetGridAuditError("Daymet model features contain infinite values.")
    audit = _build_feature_audit(
        lagged,
        tract_daily,
        weights,
        target_dates=target_dates,
        windows=normalized_windows,
    )
    audit = keys.merge(
        audit,
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    )
    audit["tract_geoid"] = audit["tract_geoid"].astype("str")
    weights["tract_geoid"] = weights["tract_geoid"].astype("str")
    weights["daymet_cell_id"] = weights["daymet_cell_id"].astype("str")
    if len(features) != len(keys) or len(audit) != len(keys):
        raise AssertionError("Daymet compiler changed the frozen key count.")
    same_geoids = np.array_equal(
        features["tract_geoid"].astype(str).to_numpy(),
        keys["tract_geoid"].astype(str).to_numpy(),
    )
    same_dates = np.array_equal(
        features["target_date"].to_numpy(), keys["target_date"].to_numpy()
    )
    if not same_geoids or not same_dates:
        raise AssertionError("Daymet feature output changed the frozen key order.")
    return DaymetCompilation(features, audit, weights, tract_daily, specs)


def _safe_subset_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "opendap.earthdata.nasa.gov":
        raise DaymetGridAuditError("Daymet subset manifest contains an unofficial URL.")
    query_names = {name.casefold() for name, _ in parse_qsl(parsed.query)}
    if query_names & _SENSITIVE_QUERY_KEYS:
        raise DaymetGridAuditError("Daymet subset manifest contains credential-like data.")


def load_verified_daymet_subset_records(
    config: ResearchConfig,
    *,
    project_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load and byte-verify the authenticated downloader's committed manifest."""

    weather = config.raw["weather_features"]
    manifest_directory = project_root / Path(weather["manifest_directory"])
    summary_path = manifest_directory / "inventory_summary.json"
    inventory_path = manifest_directory / "granule_inventory.csv"
    downloads_path = manifest_directory / "subset_downloads.csv"
    for path in (summary_path, inventory_path, downloads_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required Daymet download artifact is missing: {path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("state") != "subsets_complete":
        raise DaymetGridAuditError("Daymet downloader has not committed all subsets.")
    if summary.get("daymet_grid_config_sha256") != daymet_grid_config_sha256(config):
        raise DaymetGridAuditError("Daymet download manifest disagrees with configuration.")
    if sha256_file(inventory_path) != summary.get("inventory_file_sha256"):
        raise DaymetGridAuditError("Daymet granule inventory failed its byte lock.")
    if sha256_file(downloads_path) != summary.get("download_manifest_sha256"):
        raise DaymetGridAuditError("Daymet subset manifest failed its byte lock.")

    inventory = pd.read_csv(inventory_path, dtype={"variable": "string"})
    downloads = pd.read_csv(downloads_path, dtype={"variable": "string"})
    required_inventory = {
        "concept_id",
        "title",
        "variable",
        "year",
        "size_mb",
        "https_url",
        "opendap_url",
        "updated_at",
    }
    required_downloads = {
        "access_route",
        "concept_id",
        "variable",
        "year",
        "path",
        "bytes",
        "sha256",
        "source_url",
        "credential_source",
        "subset_y_start",
        "subset_y_stop",
        "subset_x_start",
        "subset_x_stop",
    }
    if missing := required_inventory.difference(inventory.columns):
        raise DaymetGridAuditError(f"Daymet inventory lacks columns: {sorted(missing)}")
    if missing := required_downloads.difference(downloads.columns):
        raise DaymetGridAuditError(f"Daymet download manifest lacks columns: {sorted(missing)}")
    joined = downloads.merge(
        inventory.loc[:, sorted(required_inventory)],
        on=["concept_id", "variable", "year"],
        how="left",
        validate="one_to_one",
    )
    if joined["opendap_url"].isna().any() or len(joined) != len(inventory):
        raise DaymetGridAuditError("Daymet download manifest does not match the inventory.")
    if int(summary.get("subset_count", -1)) != len(joined):
        raise DaymetGridAuditError("Daymet committed subset count is inconsistent.")
    y_indices = tuple(weather["direct_subset_y_indices"])
    x_indices = tuple(weather["direct_subset_x_indices"])
    if (
        summary.get("subset_access_route") != DAYMET_DIRECT_DAP4_ROUTE
        or summary.get("direct_subset_y_indices") != list(y_indices)
        or summary.get("direct_subset_x_indices") != list(x_indices)
        or not joined["access_route"].astype(str).eq(DAYMET_DIRECT_DAP4_ROUTE).all()
        or not joined["subset_y_start"].eq(y_indices[0]).all()
        or not joined["subset_y_stop"].eq(y_indices[1]).all()
        or not joined["subset_x_start"].eq(x_indices[0]).all()
        or not joined["subset_x_stop"].eq(x_indices[1]).all()
    ):
        raise DaymetGridAuditError("Daymet direct DAP4 route provenance is invalid.")
    if not joined["credential_source"].astype(str).isin(
        DAYMET_ALLOWED_CREDENTIAL_SOURCES
    ).all():
        raise DaymetGridAuditError("Daymet credential provenance is invalid.")

    records: list[dict[str, object]] = []
    file_records: list[dict[str, object]] = []
    raw_directory = Path(weather["raw_subset_directory"])
    raw_directory = (
        raw_directory.resolve()
        if raw_directory.is_absolute()
        else (project_root / raw_directory).resolve()
    )
    for row in joined.itertuples(index=False):
        source_url = str(row.source_url)
        _safe_subset_source_url(source_url)
        expected_source_url = build_daymet_direct_subset_url(
            DaymetGranule(
                concept_id=str(row.concept_id),
                title=str(row.title),
                variable=str(row.variable),
                year=int(row.year),
                size_mb=float(row.size_mb),
                https_url=str(row.https_url),
                opendap_url=str(row.opendap_url),
                updated_at=None if pd.isna(row.updated_at) else str(row.updated_at),
            ),
            y_indices=y_indices,
            x_indices=x_indices,
        )
        if source_url != expected_source_url:
            raise DaymetGridAuditError("Daymet subset URL does not match its granule.")
        path = Path(str(row.path))
        resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
        expected_name = (
            f"daymet_v4r1_daily_na_{row.variable}_{int(row.year)}_la_subset.nc"
        )
        if resolved.parent != raw_directory or resolved.name != expected_name:
            raise DaymetGridAuditError(
                "Daymet subset manifest points outside its locked raw directory/schema."
            )
        if not resolved.is_file():
            raise FileNotFoundError(f"Daymet subset file is missing: {resolved}")
        expected_sha = str(row.sha256)
        expected_bytes = int(row.bytes)
        if _SHA256.fullmatch(expected_sha) is None or sha256_file(resolved) != expected_sha:
            raise DaymetGridAuditError(f"Daymet subset failed its SHA-256 lock: {resolved}")
        if resolved.stat().st_size != expected_bytes:
            raise DaymetGridAuditError(f"Daymet subset failed its byte-size lock: {resolved}")
        records.append(
            {"path": resolved, "variable": str(row.variable), "year": int(row.year)}
        )
        file_records.append(
            {
                "path": str(resolved),
                "variable": str(row.variable),
                "year": int(row.year),
                "bytes": expected_bytes,
                "sha256": expected_sha,
            }
        )
    locks = {
        "inventory_summary_path": str(summary_path.resolve()),
        "inventory_summary_sha256": sha256_file(summary_path),
        "granule_inventory_path": str(inventory_path.resolve()),
        "granule_inventory_sha256": sha256_file(inventory_path),
        "subset_manifest_path": str(downloads_path.resolve()),
        "subset_manifest_sha256": sha256_file(downloads_path),
        "credential_source": str(summary.get("credential_source")),
        "subset_files": file_records,
    }
    return pd.DataFrame(records), locks


def _verify_subset_files_unchanged(download_locks: dict[str, Any]) -> None:
    records = download_locks.get("subset_files")
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict):
            raise DaymetGridAuditError("Daymet subset provenance contains a non-record.")
        path = Path(str(record.get("path", "")))
        expected_sha = str(record.get("sha256", ""))
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise RuntimeError(f"Daymet subset changed during compilation: {path}")


def _target_support_from_config(
    config: ResearchConfig,
    *,
    project_root: Path,
) -> TargetSupport:
    target_directory = project_root / Path(
        config.raw["static_features"]["target_support_directory"]
    )
    return load_target_support(config, target_directory)


def build_daymet_feature_artifacts(
    config_path: str | Path = Path("configs/research.toml"),
    feature_universe_path: str | Path = DEFAULT_FEATURE_UNIVERSE_PATH,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Build committed Daymet features from downloaded subsets and frozen support."""

    project_root = Path(__file__).resolve().parents[2]
    config = load_config(config_path)
    if config.final_test_unlocked:
        raise PermissionError("Daymet development compilation requires 2025 to remain locked.")
    weather = config.raw["weather_features"]
    windows = tuple(int(value) for value in weather["rolling_windows_days"])
    if int(weather["latest_primary_source_offset_days"]) != -1 or bool(
        weather["allow_target_day_observations_primary_model"]
    ):
        raise DaymetGridAuditError("Primary Daymet predictors must end at target day d-1.")

    universe_path = Path(feature_universe_path)
    if not universe_path.is_absolute():
        universe_path = project_root / universe_path
    keys, universe_sha = _stable_parquet_keys(
        universe_path, final_test_year=config.final_test_year
    )
    date_count = int(keys["target_date"].nunique())
    tract_count = int(keys["tract_geoid"].nunique())
    if (
        len(keys) != EXPECTED_PRODUCTION_ROWS
        or date_count != EXPECTED_PRODUCTION_DATES
        or tract_count != EXPECTED_PRODUCTION_TRACTS
    ):
        raise DaymetGridAuditError(
            "Production Daymet key universe no longer matches 98,640 = 90 x 1,096."
        )
    subset_records, download_locks = load_verified_daymet_subset_records(
        config, project_root=project_root
    )
    target = _target_support_from_config(config, project_root=project_root)
    tract_geoids = tuple(target.tracts["GEOID"].astype(str))
    compilation = compile_daymet_feature_tables(
        subset_records,
        keys,
        zone_raster=target.zones,
        eligible_land_mask=target.eligible_land,
        tract_geoids=tract_geoids,
        target_transform=target.grid.transform,
        target_crs=target.grid.crs,
        windows=windows,
        final_test_year=config.final_test_year,
    )
    _verify_subset_files_unchanged(download_locks)
    features = compilation.features
    audit = compilation.audit
    weights = compilation.weights
    feature_names = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))
    output_key_sha = canonical_frame_sha256(
        features,
        sort_by=list(KEY_COLUMNS),
        columns=list(KEY_COLUMNS),
    )
    input_key_sha = canonical_frame_sha256(
        keys,
        sort_by=list(KEY_COLUMNS),
        columns=list(KEY_COLUMNS),
    )
    if output_key_sha != input_key_sha:
        raise AssertionError("Committed Daymet features changed the frozen key universe.")

    output = Path(output_directory)
    if not output.is_absolute():
        output = project_root / output
    output.mkdir(parents=True, exist_ok=True)
    feature_path = output / DAYMET_FEATURE_FILENAME
    audit_path = output / DAYMET_AUDIT_FILENAME
    weights_path = output / DAYMET_WEIGHTS_FILENAME
    provenance_path = output / DAYMET_PROVENANCE_FILENAME
    provenance_path.unlink(missing_ok=True)
    atomic_parquet(features, feature_path)
    atomic_parquet(audit, audit_path)
    atomic_parquet(weights, weights_path)
    frozen_features = pd.read_parquet(feature_path)
    frozen_audit = pd.read_parquet(audit_path)
    frozen_weights = pd.read_parquet(weights_path)
    pd.testing.assert_frame_equal(frozen_features, features, check_dtype=True)
    pd.testing.assert_frame_equal(frozen_audit, audit, check_dtype=True)
    pd.testing.assert_frame_equal(frozen_weights, weights, check_dtype=True)

    pipeline_sha, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_daymet_features.py",
            "src/la_heat/daymet_feature_stage.py",
            "src/la_heat/daymet_grid.py",
            "src/la_heat/feature_universe.py",
            "src/la_heat/phase2_registry.py",
            "src/la_heat/provenance.py",
            "src/la_heat/stage_config.py",
            "src/la_heat/static_features.py",
            "src/la_heat/weather_daymet.py",
        ),
        algorithm_version=DAYMET_FEATURE_STAGE_ALGORITHM_VERSION,
    )
    complete_rows = int(audit["daymet_all_primary_windows_complete"].sum())
    missingness = {
        name: int(features[name].isna().sum()) for name in feature_names
    }
    payload: dict[str, Any] = {
        "schema_version": DAYMET_FEATURE_STAGE_SCHEMA_VERSION,
        "algorithm_version": DAYMET_FEATURE_STAGE_ALGORITHM_VERSION,
        "status": DAYMET_FEATURE_STAGE_STATUS,
        "phase2_promoted": False,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_or_qa_value_columns_read": [],
        "source": "Daymet V4 R1 daily gridded weather",
        "dataset_doi": DAYMET_DOI_URL,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": False,
        "row_count": len(features),
        "date_count": date_count,
        "tract_count": tract_count,
        "feature_count": len(feature_names),
        "feature_names": list(feature_names),
        "complete_feature_rows": complete_rows,
        "incomplete_feature_rows": len(features) - complete_rows,
        "missing_count_by_feature": missingness,
        "window_days": list(windows),
        "source_window_definition": "complete civil days d-n through d-1",
        "source_end_offset_days": -1,
        "latest_source_offset_days": -1,
        "date_specific_weight_renormalization": False,
        "static_eligible_land_denominator_invariant": True,
        "srad_energy_computed_cell_first": True,
        "feature_universe": {
            "path": str(universe_path.resolve()),
            "sha256": universe_sha,
            "columns_read": list(KEY_COLUMNS),
            "semantic_key_sha256": input_key_sha,
        },
        "daymet_downloads": download_locks,
        "target_support_locks": target.locks,
        "daymet_grid_config_sha256": daymet_grid_config_sha256(config),
        "semantic_key_sha256": output_key_sha,
        "semantic_feature_table_sha256": canonical_frame_sha256(
            features, sort_by=list(KEY_COLUMNS)
        ),
        "semantic_audit_table_sha256": canonical_frame_sha256(
            audit, sort_by=list(KEY_COLUMNS)
        ),
        "semantic_weights_sha256": canonical_frame_sha256(
            weights, sort_by=["tract_geoid", "daymet_cell_id"]
        ),
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_payload,
        "output_files": {
            feature_path.name: parquet_file_record(feature_path, frozen_features),
            audit_path.name: parquet_file_record(audit_path, frozen_audit),
            weights_path.name: parquet_file_record(weights_path, frozen_weights),
        },
        "remaining_gate": (
            "Assemble all predictor families, freeze the combined registry and model "
            "hyperparameters, then promote grouped validation before model fitting."
        ),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, provenance_path)
    return payload
