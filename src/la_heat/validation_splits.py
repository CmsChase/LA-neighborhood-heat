"""Target-blind, grouped validation split definitions for the development cohort.

The builder deliberately reads only tract/date/block keys from the legal development
row table.  Temperature values and all predictors are outside this stage's input
contract.  The generated files describe fold formulas rather than materializing a
large row-by-fold role table; :func:`assign_fold_roles` reconstructs those roles.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

SPLIT_SCHEMA_VERSION = 1
SPLIT_ALGORITHM_VERSION = "validation-splits-v1"
SPLIT_STATE = "predeclared_draft"
ROW_GROUP_INPUT_COLUMNS = ("tract_geoid", "target_date", "spatial_block")
TRACT_INPUT_COLUMNS = ("GEOID", "spatial_block", "geometry")
FAMILIES = ("temporal", "spatial", "joint")
PROVENANCE_FILENAME = "split_provenance.json"


class ValidationSplitAuditError(ValueError):
    """Raised when a split input or generated artifact violates the contract."""


@dataclass(frozen=True)
class ValidationSplitConfig:
    """Validated settings for the predeclared split draft."""

    path: Path
    raw: dict[str, Any]
    schema_version: int
    algorithm_version: str
    state: str
    development_years: tuple[int, ...]
    final_test_year: int
    analysis_crs: str
    spatial_block_size_km: float
    joint_buffer_m: float
    row_groups_path: Path
    tract_manifest_path: Path
    output_directory: Path


@dataclass(frozen=True)
class ValidationSplitTables:
    """In-memory target-blind split artifacts."""

    row_groups: pd.DataFrame
    fold_definitions: pd.DataFrame
    spatial_buffer_geoids: pd.DataFrame


def _resolve_project_path(project_root: Path, value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValidationSplitAuditError(f"{field} must be a non-empty path string.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def load_validation_split_config(path: str | Path) -> ValidationSplitConfig:
    """Load and fail-closed validate the standalone split configuration."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    project_root = config_path.parent.parent
    try:
        schema_version = int(raw["schema_version"])
        algorithm_version = str(raw["algorithm_version"])
        state = str(raw["state"])
        development_years = tuple(int(year) for year in raw["development_years"])
        final_test_year = int(raw["final_test_year"])
        spatial = raw["spatial"]
        inputs = raw["inputs"]
        outputs = raw["outputs"]
        schemes = raw["schemes"]
        inner_cv = raw["inner_cv"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationSplitAuditError(
            "Validation split configuration is incomplete or malformed."
        ) from error

    if schema_version != SPLIT_SCHEMA_VERSION:
        raise ValidationSplitAuditError(
            f"Unsupported split schema version: {schema_version}."
        )
    if algorithm_version != SPLIT_ALGORITHM_VERSION:
        raise ValidationSplitAuditError(
            f"Unsupported split algorithm version: {algorithm_version}."
        )
    if state != SPLIT_STATE:
        raise ValidationSplitAuditError(
            f"Split state must remain {SPLIT_STATE!r} before model/predictor freeze."
        )
    if development_years != tuple(range(2020, 2025)):
        raise ValidationSplitAuditError(
            "The development-year contract must be exactly 2020 through 2024."
        )
    if final_test_year != 2025 or any(
        year >= final_test_year for year in development_years
    ):
        raise ValidationSplitAuditError("Calendar year 2025 must remain outside development.")

    expected_schemes = {
        "temporal": "leave_one_calendar_year_out",
        "spatial": "leave_one_existing_spatial_block_out",
        "joint": "cartesian_year_x_block_with_geometry_buffer",
    }
    for family, expected in expected_schemes.items():
        try:
            configured = str(schemes[family]["strategy"])
        except (KeyError, TypeError) as error:
            raise ValidationSplitAuditError(
                f"Missing strategy for the {family} split family."
            ) from error
        if configured != expected:
            raise ValidationSplitAuditError(
                f"The {family} strategy must be {expected!r}, got {configured!r}."
            )
    expected_inner_cv = {
        "strategy": "leave_one_remaining_calendar_year_out",
        "scope": "outer_train_only",
        "preprocessing_fit_scope": "inner_train_only",
    }
    for field, expected in expected_inner_cv.items():
        configured = str(inner_cv.get(field, ""))
        if configured != expected:
            raise ValidationSplitAuditError(
                f"inner_cv.{field} must be {expected!r}, got {configured!r}."
            )

    analysis_crs = str(spatial.get("analysis_crs", ""))
    block_size_km = float(spatial.get("block_size_km", 0.0))
    joint_buffer_m = float(spatial.get("joint_buffer_m", -1.0))
    if analysis_crs != "EPSG:3310":
        raise ValidationSplitAuditError("Split geometry must remain in EPSG:3310.")
    if block_size_km != 5.0:
        raise ValidationSplitAuditError("Spatial validation blocks must remain 5 km.")
    if joint_buffer_m != 1000.0:
        raise ValidationSplitAuditError("The joint-fold geometry buffer must remain 1000 m.")

    return ValidationSplitConfig(
        path=config_path,
        raw=raw,
        schema_version=schema_version,
        algorithm_version=algorithm_version,
        state=state,
        development_years=development_years,
        final_test_year=final_test_year,
        analysis_crs=analysis_crs,
        spatial_block_size_km=block_size_km,
        joint_buffer_m=joint_buffer_m,
        row_groups_path=_resolve_project_path(
            project_root, inputs.get("row_groups"), field="inputs.row_groups"
        ),
        tract_manifest_path=_resolve_project_path(
            project_root,
            inputs.get("tract_manifest"),
            field="inputs.tract_manifest",
        ),
        output_directory=_resolve_project_path(
            project_root, outputs.get("directory"), field="outputs.directory"
        ),
    )


def _civil_midnights(values: pd.Series) -> pd.Series:
    parsed: list[pd.Timestamp] = []
    for position, value in enumerate(values.tolist()):
        if isinstance(value, (int, float, np.integer, np.floating)):
            raise ValidationSplitAuditError(
                f"target_date at row {position} is numeric, not a civil date."
            )
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValidationSplitAuditError(
                f"target_date at row {position} is not parseable."
            ) from error
        if pd.isna(timestamp):
            raise ValidationSplitAuditError(f"target_date at row {position} is missing.")
        if timestamp.tzinfo is not None:
            raise ValidationSplitAuditError("target_date must be timezone-naive civil dates.")
        if timestamp != timestamp.normalize():
            raise ValidationSplitAuditError("target_date must be at civil midnight.")
        parsed.append(timestamp)
    return pd.Series(parsed, index=values.index, dtype="datetime64[us]")


def prepare_row_groups(
    frame: pd.DataFrame,
    *,
    development_years: tuple[int, ...],
    final_test_year: int,
) -> pd.DataFrame:
    """Validate and canonicalize only the legal tract/date/block columns.

    Extra columns are intentionally ignored.  This lets tests demonstrate that
    changing target values cannot change split assignments.
    """

    missing = set(ROW_GROUP_INPUT_COLUMNS) - set(frame.columns)
    if missing:
        raise ValidationSplitAuditError(
            f"Legal row-group input is missing columns: {sorted(missing)}"
        )
    result = frame.loc[:, list(ROW_GROUP_INPUT_COLUMNS)].copy()
    if result.columns.duplicated().any():
        raise ValidationSplitAuditError("Legal row-group input has duplicate columns.")
    for column in ("tract_geoid", "spatial_block"):
        if result[column].isna().any() or not result[column].map(
            lambda value: isinstance(value, str) and value == value.strip() and bool(value)
        ).all():
            raise ValidationSplitAuditError(
                f"{column} must contain non-empty, whitespace-normalized strings."
            )
    result["target_date"] = _civil_midnights(result["target_date"])
    if result.duplicated(["tract_geoid", "target_date"]).any():
        raise ValidationSplitAuditError("Duplicate tract-date keys in legal row groups.")

    result["year"] = result["target_date"].dt.year.astype("int16")
    if (result["year"] >= final_test_year).any():
        raise PermissionError(
            f"Locked final-test year {final_test_year} or later appeared in split input."
        )
    observed_years = tuple(sorted(int(year) for year in result["year"].unique()))
    if observed_years != development_years:
        raise ValidationSplitAuditError(
            f"Development years must be exactly {development_years}; got {observed_years}."
        )
    return result.sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def read_legal_row_groups(
    path: str | Path,
    *,
    development_years: tuple[int, ...],
    final_test_year: int,
) -> pd.DataFrame:
    """Read only the three target-blind columns allowed by the split contract."""

    try:
        frame = pd.read_parquet(path, columns=list(ROW_GROUP_INPUT_COLUMNS))
    except (KeyError, ValueError) as error:
        raise ValidationSplitAuditError(
            "Could not read the required target-blind row-group columns."
        ) from error
    return prepare_row_groups(
        frame,
        development_years=development_years,
        final_test_year=final_test_year,
    )


def _recomputed_spatial_blocks(
    tracts: gpd.GeoDataFrame, *, block_size_km: float
) -> pd.Series:
    block_size_m = float(block_size_km) * 1000.0
    if not np.isfinite(block_size_m) or block_size_m <= 0:
        raise ValidationSplitAuditError("Spatial block size must be positive and finite.")
    centroids = tracts.geometry.centroid
    block_x = np.floor(centroids.x.to_numpy() / block_size_m).astype(int)
    block_y = np.floor(centroids.y.to_numpy() / block_size_m).astype(int)
    return pd.Series(
        [
            f"x{x:+05d}_y{y:+05d}"
            for x, y in zip(block_x, block_y, strict=True)
        ],
        index=tracts.index,
        dtype="string",
    )


def prepare_fixed_tracts(
    tracts: gpd.GeoDataFrame,
    *,
    analysis_crs: str,
    block_size_km: float,
) -> gpd.GeoDataFrame:
    """Validate the complete, target-independent tract geometry universe."""

    missing = set(TRACT_INPUT_COLUMNS) - set(tracts.columns)
    if missing:
        raise ValidationSplitAuditError(
            f"Fixed tract manifest is missing columns: {sorted(missing)}"
        )
    result = tracts.loc[:, list(TRACT_INPUT_COLUMNS)].copy()
    if result.empty or result.crs is None or result.crs != analysis_crs:
        raise ValidationSplitAuditError(
            f"Fixed tract geometry must be non-empty and stored in {analysis_crs}."
        )
    if result.crs.is_geographic:
        raise ValidationSplitAuditError("Fixed tract geometry must use a projected CRS.")
    if result["GEOID"].isna().any() or not result["GEOID"].map(
        lambda value: isinstance(value, str) and value == value.strip() and bool(value)
    ).all():
        raise ValidationSplitAuditError("Fixed tract GEOIDs must be normalized strings.")
    if result["spatial_block"].isna().any() or not result["spatial_block"].map(
        lambda value: isinstance(value, str) and value == value.strip() and bool(value)
    ).all():
        raise ValidationSplitAuditError(
            "Fixed tract spatial blocks must be normalized strings."
        )
    if result["GEOID"].duplicated().any():
        raise ValidationSplitAuditError("Fixed tract manifest contains duplicate GEOIDs.")
    if result.geometry.isna().any() or result.geometry.is_empty.any():
        raise ValidationSplitAuditError("Fixed tract manifest contains missing/empty geometry.")
    if not result.geometry.is_valid.all():
        raise ValidationSplitAuditError("Fixed tract manifest contains invalid geometry.")

    recomputed = _recomputed_spatial_blocks(result, block_size_km=block_size_km)
    recorded = result["spatial_block"].astype("string")
    mismatch = recorded.ne(recomputed)
    if mismatch.any():
        example = result.loc[mismatch, "GEOID"].astype(str).iloc[0]
        raise ValidationSplitAuditError(
            f"Recorded 5 km spatial block disagrees with tract centroid for GEOID {example}."
        )
    result["GEOID"] = result["GEOID"].astype(str)
    result["spatial_block"] = recorded.astype(str)
    return result.sort_values("GEOID", kind="stable").reset_index(drop=True)


def read_fixed_tracts(
    path: str | Path,
    *,
    analysis_crs: str,
    block_size_km: float,
) -> gpd.GeoDataFrame:
    """Read only geometry and grouping columns from the fixed tract manifest."""

    try:
        raw = pd.read_parquet(path, columns=list(TRACT_INPUT_COLUMNS))
        values = raw["geometry"]
        if values.map(lambda value: isinstance(value, bytes) or pd.isna(value)).all():
            decoded = shapely.from_wkb(values.to_numpy())
        else:
            decoded = values.to_numpy()
        tracts = gpd.GeoDataFrame(
            raw.drop(columns="geometry"), geometry=decoded, crs=analysis_crs
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationSplitAuditError(
            "Could not read the required fixed-tract grouping/geometry columns."
        ) from error
    return prepare_fixed_tracts(
        tracts,
        analysis_crs=analysis_crs,
        block_size_km=block_size_km,
    )


def validate_row_group_tract_lock(
    row_groups: pd.DataFrame, tracts: gpd.GeoDataFrame
) -> None:
    """Require legal rows to use the fixed tract-to-block assignment exactly."""

    mapping = tracts.set_index("GEOID")["spatial_block"]
    expected = row_groups["tract_geoid"].map(mapping)
    if expected.isna().any():
        unknown = row_groups.loc[expected.isna(), "tract_geoid"].iloc[0]
        raise ValidationSplitAuditError(
            f"Legal row references GEOID outside the fixed tract universe: {unknown}."
        )
    mismatch = expected.ne(row_groups["spatial_block"])
    if mismatch.any():
        geoid = row_groups.loc[mismatch, "tract_geoid"].iloc[0]
        raise ValidationSplitAuditError(
            f"Legal row block disagrees with fixed tract block for GEOID {geoid}."
        )
    fixed_blocks = set(tracts["spatial_block"])
    observed_blocks = set(row_groups["spatial_block"])
    if observed_blocks != fixed_blocks:
        missing = sorted(fixed_blocks - observed_blocks)
        extra = sorted(observed_blocks - fixed_blocks)
        raise ValidationSplitAuditError(
            "Every fixed spatial block must have legal development rows; "
            f"missing={missing}, extra={extra}."
        )


def build_spatial_buffer_geoids(
    tracts: gpd.GeoDataFrame, *, buffer_m: float
) -> pd.DataFrame:
    """Build the fixed geometry-based exclusion set for every held-out block."""

    if not np.isfinite(buffer_m) or buffer_m < 0:
        raise ValidationSplitAuditError("Joint-fold buffer distance must be finite/nonnegative.")
    records: list[pd.DataFrame] = []
    for block in sorted(tracts["spatial_block"].unique()):
        held_out = tracts.loc[tracts["spatial_block"].eq(block)]
        held_union = held_out.geometry.union_all()
        distance = tracts.geometry.distance(held_union).to_numpy(dtype=float)
        selected = distance <= buffer_m
        if not selected.any():
            raise ValidationSplitAuditError(f"Spatial buffer is empty for block {block}.")
        piece = pd.DataFrame(
            {
                "held_out_block": block,
                "tract_geoid": tracts.loc[selected, "GEOID"].to_numpy(),
                "exclusion_role": np.where(
                    tracts.loc[selected, "spatial_block"].eq(block),
                    "held_out_block",
                    "buffer_only",
                ),
                "distance_to_held_out_block_m": distance[selected],
            }
        )
        records.append(piece)
    result = pd.concat(records, ignore_index=True)
    if result.duplicated(["held_out_block", "tract_geoid"]).any():
        raise ValidationSplitAuditError("Duplicate GEOID in a held-out block buffer set.")
    return result.sort_values(
        ["held_out_block", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def _buffer_lookup(spatial_buffer_geoids: pd.DataFrame) -> dict[str, frozenset[str]]:
    required = {
        "held_out_block",
        "tract_geoid",
        "exclusion_role",
        "distance_to_held_out_block_m",
    }
    missing = required - set(spatial_buffer_geoids.columns)
    if missing:
        raise ValidationSplitAuditError(
            f"Spatial buffer table is missing columns: {sorted(missing)}"
        )
    return {
        str(block): frozenset(group["tract_geoid"].astype(str))
        for block, group in spatial_buffer_geoids.groupby("held_out_block", sort=True)
    }


def assign_fold_roles(
    row_groups: pd.DataFrame,
    *,
    family: str,
    held_out_year: int | None = None,
    held_out_block: str | None = None,
    buffered_geoids: frozenset[str] = frozenset(),
) -> pd.Series:
    """Reconstruct mutually exclusive ``train``/``test``/``purged`` row roles."""

    if family not in FAMILIES:
        raise ValidationSplitAuditError(f"Unknown split family: {family!r}.")
    roles = np.full(len(row_groups), "purged", dtype=object)
    years = row_groups["year"].to_numpy()
    blocks = row_groups["spatial_block"].to_numpy()

    if family == "temporal":
        if held_out_year is None or held_out_block is not None:
            raise ValidationSplitAuditError("Temporal fold requires only held_out_year.")
        is_test = years == held_out_year
        roles[~is_test] = "train"
        roles[is_test] = "test"
    elif family == "spatial":
        if held_out_block is None or held_out_year is not None:
            raise ValidationSplitAuditError("Spatial fold requires only held_out_block.")
        is_test = blocks == held_out_block
        roles[~is_test] = "train"
        roles[is_test] = "test"
    else:
        if held_out_year is None or held_out_block is None:
            raise ValidationSplitAuditError(
                "Joint fold requires held_out_year and held_out_block."
            )
        if not buffered_geoids:
            raise ValidationSplitAuditError("Joint fold requires a non-empty buffer set.")
        is_test = (years == held_out_year) & (blocks == held_out_block)
        outside_buffer = ~row_groups["tract_geoid"].isin(buffered_geoids).to_numpy()
        is_train = (years != held_out_year) & outside_buffer
        roles[is_train] = "train"
        roles[is_test] = "test"
    return pd.Series(roles, index=row_groups.index, name="split_role", dtype="string")


def build_inner_cv_roles(
    row_groups: pd.DataFrame, outer_roles: pd.Series
) -> dict[int, pd.Series]:
    """Leave one remaining year out strictly inside an outer training set.

    Rows assigned to the outer test or purge sets receive ``outer_excluded`` in
    every inner fold.  Consequently neither labels nor preprocessing statistics
    from those rows can enter hyperparameter selection.
    """

    if not outer_roles.index.equals(row_groups.index):
        raise ValidationSplitAuditError("Outer roles must align exactly with row groups.")
    if outer_roles.isna().any() or not set(outer_roles.astype(str)).issubset(
        {"train", "test", "purged"}
    ):
        raise ValidationSplitAuditError("Outer roles contain missing or unknown values.")
    outer_train = outer_roles.eq("train").to_numpy()
    remaining_years = tuple(
        sorted(int(year) for year in row_groups.loc[outer_train, "year"].unique())
    )
    if len(remaining_years) < 2:
        raise ValidationSplitAuditError(
            "Inner year CV requires at least two calendar years in the outer training set."
        )

    result: dict[int, pd.Series] = {}
    years = row_groups["year"].to_numpy()
    for validation_year in remaining_years:
        roles = np.full(len(row_groups), "outer_excluded", dtype=object)
        is_validation = outer_train & (years == validation_year)
        is_train = outer_train & (years != validation_year)
        roles[is_train] = "train"
        roles[is_validation] = "validation"
        if not is_train.any() or not is_validation.any():
            raise ValidationSplitAuditError(
                f"Inner validation year {validation_year} has an empty train/validation set."
            )
        inner = pd.Series(
            roles, index=row_groups.index, name="inner_cv_role", dtype="string"
        )
        if not inner.loc[~outer_train].eq("outer_excluded").all():
            raise ValidationSplitAuditError("Outer-excluded rows entered inner CV.")
        result[validation_year] = inner
    return result


def _fold_record(
    row_groups: pd.DataFrame,
    *,
    family: str,
    fold_index: int,
    fold_id: str,
    held_out_year: int | None,
    held_out_block: str | None,
    buffered_geoids: frozenset[str],
    held_out_block_geoid_count: int,
) -> dict[str, object]:
    roles = assign_fold_roles(
        row_groups,
        family=family,
        held_out_year=held_out_year,
        held_out_block=held_out_block,
        buffered_geoids=buffered_geoids,
    )
    train = row_groups.loc[roles.eq("train")]
    test = row_groups.loc[roles.eq("test")]
    purged = row_groups.loc[roles.eq("purged")]
    if train.empty or test.empty:
        raise ValidationSplitAuditError(f"Fold {fold_id} has an empty train or test set.")
    if len(train) + len(test) + len(purged) != len(row_groups):
        raise ValidationSplitAuditError(f"Fold {fold_id} roles are not exhaustive.")
    if family != "joint" and not purged.empty:
        raise ValidationSplitAuditError(f"Fold {fold_id} unexpectedly purged rows.")
    return {
        "split_state": SPLIT_STATE,
        "family": family,
        "fold_index": fold_index,
        "fold_id": fold_id,
        "held_out_year": held_out_year,
        "held_out_block": held_out_block,
        "train_row_count": len(train),
        "test_row_count": len(test),
        "purged_row_count": len(purged),
        "train_date_count": train["target_date"].nunique(),
        "test_date_count": test["target_date"].nunique(),
        "train_spatial_block_count": train["spatial_block"].nunique(),
        "test_spatial_block_count": test["spatial_block"].nunique(),
        "train_geoid_count": train["tract_geoid"].nunique(),
        "test_geoid_count": test["tract_geoid"].nunique(),
        "inner_cv_fold_count": train["year"].nunique(),
        "buffered_geoid_count": len(buffered_geoids),
        "held_out_block_geoid_count": held_out_block_geoid_count,
    }


def build_fold_definitions(
    row_groups: pd.DataFrame,
    tracts: gpd.GeoDataFrame,
    spatial_buffer_geoids: pd.DataFrame,
    *,
    development_years: tuple[int, ...],
) -> pd.DataFrame:
    """Create all temporal, spatial, and Cartesian joint fold definitions."""

    blocks = tuple(sorted(tracts["spatial_block"].unique()))
    block_geoid_counts = tracts.groupby("spatial_block")["GEOID"].nunique().to_dict()
    buffers = _buffer_lookup(spatial_buffer_geoids)
    if set(buffers) != set(blocks):
        raise ValidationSplitAuditError("Spatial buffer table does not cover every block.")

    records: list[dict[str, object]] = []
    for index, year in enumerate(development_years):
        records.append(
            _fold_record(
                row_groups,
                family="temporal",
                fold_index=index,
                fold_id=f"temporal_year_{year}",
                held_out_year=year,
                held_out_block=None,
                buffered_geoids=frozenset(),
                held_out_block_geoid_count=0,
            )
        )
    for index, block in enumerate(blocks):
        records.append(
            _fold_record(
                row_groups,
                family="spatial",
                fold_index=index,
                fold_id=f"spatial_block_{block}",
                held_out_year=None,
                held_out_block=block,
                buffered_geoids=frozenset(),
                held_out_block_geoid_count=int(block_geoid_counts[block]),
            )
        )
    joint_index = 0
    for year in development_years:
        for block in blocks:
            records.append(
                _fold_record(
                    row_groups,
                    family="joint",
                    fold_index=joint_index,
                    fold_id=f"joint_year_{year}__block_{block}",
                    held_out_year=year,
                    held_out_block=block,
                    buffered_geoids=buffers[block],
                    held_out_block_geoid_count=int(block_geoid_counts[block]),
                )
            )
            joint_index += 1
    result = pd.DataFrame.from_records(records)
    result["held_out_year"] = result["held_out_year"].astype("Int64")
    return result.reset_index(drop=True)


def validate_oof_coverage(
    row_groups: pd.DataFrame,
    fold_definitions: pd.DataFrame,
    spatial_buffer_geoids: pd.DataFrame,
) -> dict[str, dict[str, int]]:
    """Audit fold counts and require one test assignment per row per family."""

    buffers = _buffer_lookup(spatial_buffer_geoids)
    audit: dict[str, dict[str, int]] = {}
    for family in FAMILIES:
        test_assignments = np.zeros(len(row_groups), dtype=np.int16)
        family_folds = fold_definitions.loc[fold_definitions["family"].eq(family)]
        if family_folds.empty:
            raise ValidationSplitAuditError(f"No fold definitions for family {family}.")
        for fold in family_folds.itertuples(index=False):
            year = None if pd.isna(fold.held_out_year) else int(fold.held_out_year)
            block = None if pd.isna(fold.held_out_block) else str(fold.held_out_block)
            roles = assign_fold_roles(
                row_groups,
                family=family,
                held_out_year=year,
                held_out_block=block,
                buffered_geoids=(buffers[block] if family == "joint" and block else frozenset()),
            )
            observed = roles.value_counts().to_dict()
            expected = {
                "train": int(fold.train_row_count),
                "test": int(fold.test_row_count),
                "purged": int(fold.purged_row_count),
            }
            if any(int(observed.get(role, 0)) != count for role, count in expected.items()):
                raise ValidationSplitAuditError(
                    f"Recorded counts disagree with reconstructed roles for {fold.fold_id}."
                )
            test_assignments += roles.eq("test").to_numpy(dtype=np.int16)
        minimum = int(test_assignments.min())
        maximum = int(test_assignments.max())
        if minimum != 1 or maximum != 1:
            raise ValidationSplitAuditError(
                f"{family} OOF test coverage must equal one per row; got {minimum}..{maximum}."
            )
        audit[family] = {
            "fold_count": len(family_folds),
            "minimum_test_assignments_per_row": minimum,
            "maximum_test_assignments_per_row": maximum,
        }
    return audit


def build_validation_split_tables(
    row_input: pd.DataFrame,
    tract_input: gpd.GeoDataFrame,
    *,
    development_years: tuple[int, ...],
    final_test_year: int,
    analysis_crs: str,
    block_size_km: float,
    joint_buffer_m: float,
) -> ValidationSplitTables:
    """Build and audit all three split tables from in-memory target-blind inputs."""

    row_groups = prepare_row_groups(
        row_input,
        development_years=development_years,
        final_test_year=final_test_year,
    )
    tracts = prepare_fixed_tracts(
        tract_input,
        analysis_crs=analysis_crs,
        block_size_km=block_size_km,
    )
    validate_row_group_tract_lock(row_groups, tracts)
    buffers = build_spatial_buffer_geoids(tracts, buffer_m=joint_buffer_m)
    folds = build_fold_definitions(
        row_groups,
        tracts,
        buffers,
        development_years=development_years,
    )
    validate_oof_coverage(row_groups, folds, buffers)
    return ValidationSplitTables(row_groups, folds, buffers)


def _fixed_tract_semantic_sha256(tracts: gpd.GeoDataFrame) -> str:
    records = [
        {
            "tract_geoid": row.GEOID,
            "spatial_block": row.spatial_block,
            "geometry_wkb": shapely.to_wkb(shapely.normalize(row.geometry)).hex(),
        }
        for row in tracts.sort_values("GEOID", kind="stable").itertuples(index=False)
    ]
    return canonical_sha256({"crs": tracts.crs.to_string(), "tracts": records})


def build_validation_split_draft(
    config_path: str | Path = Path("configs/validation_splits.toml"),
) -> dict[str, Any]:
    """Build auditable draft artifacts and write provenance last as the commit marker."""

    config = load_validation_split_config(config_path)
    output_directory = config.output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    provenance_path = output_directory / PROVENANCE_FILENAME
    provenance_path.unlink(missing_ok=True)

    row_groups = read_legal_row_groups(
        config.row_groups_path,
        development_years=config.development_years,
        final_test_year=config.final_test_year,
    )
    tracts = read_fixed_tracts(
        config.tract_manifest_path,
        analysis_crs=config.analysis_crs,
        block_size_km=config.spatial_block_size_km,
    )
    validate_row_group_tract_lock(row_groups, tracts)
    buffers = build_spatial_buffer_geoids(tracts, buffer_m=config.joint_buffer_m)
    folds = build_fold_definitions(
        row_groups,
        tracts,
        buffers,
        development_years=config.development_years,
    )
    oof_audit = validate_oof_coverage(row_groups, folds, buffers)

    row_path = output_directory / "row_groups.parquet"
    fold_path = output_directory / "fold_definitions.csv"
    buffer_path = output_directory / "spatial_buffer_geoids.parquet"
    atomic_parquet(row_groups, row_path)
    atomic_csv(folds, fold_path)
    atomic_parquet(buffers, buffer_path)

    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_validation_splits.py",
            "src/la_heat/provenance.py",
            "src/la_heat/validation_splits.py",
        ),
        algorithm_version=config.algorithm_version,
    )
    fold_counts = {
        family: int(folds["family"].eq(family).sum()) for family in FAMILIES
    }
    payload: dict[str, Any] = {
        "schema_version": config.schema_version,
        "algorithm_version": config.algorithm_version,
        "state": config.state,
        "phase_complete": False,
        "ready_for_model_evaluation": False,
        "remaining_gate": (
            "Require exact predictor-key match and fold-local preprocessing tests after "
            "the dynamic feature tables are complete."
        ),
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "output_directory": str(output_directory),
        "development_years": list(config.development_years),
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "row_count": len(row_groups),
        "independent_date_count": int(row_groups["target_date"].nunique()),
        "fixed_tract_count": len(tracts),
        "legal_row_geoid_count": int(row_groups["tract_geoid"].nunique()),
        "spatial_block_count": int(tracts["spatial_block"].nunique()),
        "fold_counts": fold_counts,
        "fold_count_total": len(folds),
        "oof_coverage_audit": oof_audit,
        "input_column_contract": {
            "legal_row_table": list(ROW_GROUP_INPUT_COLUMNS),
            "fixed_tract_manifest": list(TRACT_INPUT_COLUMNS),
            "target_or_predictor_values_read": False,
        },
        "scientific_rules": {
            "temporal": "leave one complete calendar year out",
            "spatial": "leave one fixed target-independent 5 km block out",
            "joint_test": "held-out calendar year AND held-out spatial block",
            "joint_train": (
                "other calendar years AND GEOID outside the held-out block plus "
                "the <=1000 m fixed-tract geometry buffer"
            ),
            "joint_other_rows": "purged",
            "spatial_distance_crs": config.analysis_crs,
            "joint_buffer_m": config.joint_buffer_m,
            "oof_reporting": (
                "stitch one out-of-fold prediction per legal row within each family"
            ),
            "inner_cv": (
                "within each outer training set, leave one remaining calendar year out; "
                "fit preprocessing and tune only on inner-train rows"
            ),
            "outer_exclusion_from_tuning": (
                "outer-test and outer-purged keys never enter inner train or validation"
            ),
        },
        "split_config_sha256": canonical_sha256(config.raw),
        "split_config_file_sha256": sha256_file(config.path),
        "input_files": {
            "legal_row_groups": {
                "path": str(config.row_groups_path),
                "sha256": sha256_file(config.row_groups_path),
            },
            "fixed_tract_manifest": {
                "path": str(config.tract_manifest_path),
                "sha256": sha256_file(config.tract_manifest_path),
                "semantic_sha256": _fixed_tract_semantic_sha256(tracts),
            },
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "semantic_outputs": {
            "row_groups_sha256": canonical_frame_sha256(
                row_groups, sort_by=["target_date", "tract_geoid"]
            ),
            "fold_definitions_sha256": canonical_frame_sha256(
                folds, sort_by=["family", "fold_index"]
            ),
            "spatial_buffer_geoids_sha256": canonical_frame_sha256(
                buffers, sort_by=["held_out_block", "tract_geoid"]
            ),
        },
        "output_files": {
            row_path.name: parquet_file_record(row_path, row_groups),
            fold_path.name: {
                "sha256": sha256_file(fold_path),
                "bytes": fold_path.stat().st_size,
                "rows": len(folds),
            },
            buffer_path.name: parquet_file_record(buffer_path, buffers),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, provenance_path)
    return payload
