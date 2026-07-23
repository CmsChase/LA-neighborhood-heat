"""Fail-closed assembly of legal target rows and registered predictor tables."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from la_heat.feature_registry import validate_feature_registry
from la_heat.model_rows import select_absolute_model_rows

PRIMARY_KEYS = ("tract_geoid", "target_date")
TARGET_COLUMN = "target_lst_c"
_FORBIDDEN_PREDICTOR_TABLE_COLUMNS = frozenset(
    {
        TARGET_COLUMN,
        "target_available",
        "date_usable",
        "lst_anomaly_c",
        "relative_hotspot_top20",
    }
)


@dataclass(frozen=True)
class PredictorTable:
    """A named predictor table with an explicit static or tract-date key."""

    name: str
    frame: pd.DataFrame
    key_columns: tuple[str, ...]


def _validate_predictor_table(
    table: PredictorTable,
    *,
    expected_keys: pd.DataFrame,
    registry_static: dict[str, bool],
    final_test_year: int,
    unlock_final_test: bool,
) -> None:
    if not isinstance(table.name, str) or not table.name.strip():
        raise ValueError("Predictor table names must be non-empty strings.")
    if not isinstance(table.frame, pd.DataFrame):
        raise TypeError(f"Predictor table {table.name!r} must be a pandas DataFrame.")
    if table.frame.columns.duplicated().any():
        duplicates = table.frame.columns[table.frame.columns.duplicated()].tolist()
        raise ValueError(
            f"Predictor table {table.name!r} has duplicate columns: {duplicates}"
        )
    allowed_keys = {("tract_geoid",), PRIMARY_KEYS}
    if table.key_columns not in allowed_keys:
        raise ValueError(
            f"Predictor table {table.name!r} has invalid keys {table.key_columns}; "
            "use ('tract_geoid',) for static data or "
            "('tract_geoid', 'target_date') for dynamic data."
        )
    missing_keys = sorted(set(table.key_columns) - set(table.frame.columns))
    if missing_keys:
        raise ValueError(
            f"Predictor table {table.name!r} is missing key columns: {missing_keys}"
        )
    if table.frame.empty:
        raise ValueError(f"Predictor table {table.name!r} must not be empty.")
    if table.frame[list(table.key_columns)].isna().any(axis=None):
        raise ValueError(f"Predictor table {table.name!r} has missing key values.")
    duplicates = table.frame.duplicated(list(table.key_columns), keep=False)
    if duplicates.any():
        examples = table.frame.loc[duplicates, list(table.key_columns)].head(5)
        raise ValueError(
            f"Predictor table {table.name!r} has duplicate keys:\n"
            f"{examples.to_string(index=False)}"
        )

    feature_columns = [
        column for column in table.frame.columns if column not in table.key_columns
    ]
    forbidden = sorted(set(feature_columns) & _FORBIDDEN_PREDICTOR_TABLE_COLUMNS)
    if forbidden:
        raise ValueError(
            f"Predictor table {table.name!r} contains target-derived columns: {forbidden}"
        )

    if "target_date" in table.key_columns:
        dates = _parse_civil_midnights(
            table.frame["target_date"],
            field=f"Predictor table {table.name!r} target_date",
        )
        if not unlock_final_test:
            locked = dates.dt.year >= final_test_year
            if locked.any():
                raise PermissionError(
                    f"Predictor table {table.name!r} contains {int(locked.sum())} "
                    f"locked rows from {final_test_year} or later."
                )

    table_is_static = table.key_columns == ("tract_geoid",)
    mismatched_static = sorted(
        column
        for column in feature_columns
        if column in registry_static and registry_static[column] != table_is_static
    )
    if mismatched_static:
        raise ValueError(
            f"Predictor table {table.name!r} key type disagrees with registry static "
            f"flags for: {mismatched_static}"
        )

    expected = expected_keys[list(table.key_columns)].drop_duplicates()
    observed = table.frame.loc[:, list(table.key_columns)].drop_duplicates()
    coverage = expected.merge(
        observed,
        on=list(table.key_columns),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    missing = coverage["_merge"].eq("left_only")
    if missing.any():
        examples = coverage.loc[missing, list(table.key_columns)].head(5)
        raise ValueError(
            f"Predictor table {table.name!r} is missing {int(missing.sum())} "
            f"required keys:\n{examples.to_string(index=False)}"
        )
    unexpected = coverage["_merge"].eq("right_only")
    if unexpected.any():
        examples = coverage.loc[unexpected, list(table.key_columns)].head(5)
        raise ValueError(
            f"Predictor table {table.name!r} has {int(unexpected.sum())} keys outside "
            f"the frozen predictor universe:\n{examples.to_string(index=False)}"
        )


def _parse_civil_midnights(values: pd.Series, *, field: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain valid civil dates.") from exc
    if parsed.isna().any():
        raise ValueError(f"{field} contains missing dates.")
    try:
        timezone = parsed.dt.tz
    except AttributeError as exc:
        raise ValueError(f"{field} must use one timezone-naive date representation.") from exc
    if timezone is not None or not parsed.dt.normalize().equals(parsed):
        raise ValueError(f"{field} must contain timezone-naive civil midnights.")
    return parsed


def _validated_predictor_universe(
    universe: pd.DataFrame | None,
    *,
    legal_keys: pd.DataFrame,
    final_test_year: int,
    unlock_final_test: bool,
) -> pd.DataFrame:
    if universe is None:
        return legal_keys.copy()
    if not isinstance(universe, pd.DataFrame):
        raise TypeError("Predictor key universe must be a pandas DataFrame.")
    if set(universe.columns) != set(PRIMARY_KEYS):
        raise ValueError("Predictor key universe must contain only the two primary keys.")
    if universe.empty or universe[list(PRIMARY_KEYS)].isna().any(axis=None):
        raise ValueError("Predictor key universe must be non-empty with complete keys.")
    if universe.duplicated(list(PRIMARY_KEYS)).any():
        raise ValueError("Predictor key universe has duplicate tract-date keys.")
    result = universe.loc[:, list(PRIMARY_KEYS)].copy()
    result["target_date"] = _parse_civil_midnights(
        result["target_date"], field="Predictor key universe target_date"
    )
    if not unlock_final_test:
        locked = result["target_date"].dt.year.ge(final_test_year)
        if locked.any():
            raise PermissionError(
                f"Predictor key universe contains {int(locked.sum())} locked rows from "
                f"{final_test_year} or later."
            )
    coverage = legal_keys.merge(
        result,
        on=list(PRIMARY_KEYS),
        how="left",
        indicator=True,
        validate="one_to_one",
    )
    if coverage["_merge"].ne("both").any():
        raise ValueError("Predictor key universe does not contain every legal model key.")
    return result


def assemble_development_model_table(
    qa_frame: pd.DataFrame,
    predictor_tables: Iterable[PredictorTable],
    registry: pd.DataFrame,
    *,
    development_start: str | pd.Timestamp,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
    predictor_key_universe: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Left-join registered predictors onto the legal absolute-LST row universe.

    Predictor tables must explicitly contain every required static or dynamic key.
    Feature values may remain missing; a missing value is different from a silently
    absent row. The output contains only keys, the target, and registry-declared
    predictor/audit columns, so target QA metadata cannot accidentally enter a model.
    """

    validate_feature_registry(registry, development_start=development_start)
    legal = select_absolute_model_rows(
        qa_frame,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )
    base = legal.loc[:, [*PRIMARY_KEYS, TARGET_COLUMN]].copy()
    base["target_date"] = _parse_civil_midnights(
        base["target_date"], field="Legal model target_date"
    )
    base_keys = base.loc[:, list(PRIMARY_KEYS)].copy()
    expected_keys = _validated_predictor_universe(
        predictor_key_universe,
        legal_keys=base_keys,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )

    tables = list(predictor_tables)
    names = [table.name for table in tables]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        raise ValueError(f"Predictor table names must be unique: {duplicate_names}")

    registry_names = registry["feature_name"].tolist()
    declared_feature_names = [
        name for name in registry_names if name not in set(PRIMARY_KEYS)
    ]
    registry_static = registry.set_index("feature_name")["static"].astype(bool).to_dict()
    observed_feature_names: list[str] = []
    assembled = base
    for table in tables:
        working_frame = table.frame.copy(deep=False)
        if "target_date" in table.key_columns:
            working_frame = working_frame.copy()
            working_frame["target_date"] = _parse_civil_midnights(
                working_frame["target_date"],
                field=f"Predictor table {table.name!r} target_date",
            )
        working_table = PredictorTable(table.name, working_frame, table.key_columns)
        _validate_predictor_table(
            working_table,
            expected_keys=expected_keys,
            registry_static=registry_static,
            final_test_year=final_test_year,
            unlock_final_test=unlock_final_test,
        )
        feature_columns = [
            column
            for column in working_table.frame.columns
            if column not in working_table.key_columns
        ]
        collisions = sorted(set(feature_columns) & set(assembled.columns))
        if collisions:
            raise ValueError(
                f"Predictor table {table.name!r} has colliding columns: {collisions}"
            )
        observed_feature_names.extend(feature_columns)
        assembled = assembled.merge(
            working_table.frame,
            on=list(working_table.key_columns),
            how="left",
            sort=False,
            validate=(
                "many_to_one"
                if working_table.key_columns == ("tract_geoid",)
                else "one_to_one"
            ),
        )

    duplicate_features = sorted(
        {name for name in observed_feature_names if observed_feature_names.count(name) > 1}
    )
    if duplicate_features:
        raise ValueError(f"Predictor feature columns must be unique: {duplicate_features}")

    missing_declared = sorted(set(declared_feature_names) - set(observed_feature_names))
    undeclared = sorted(set(observed_feature_names) - set(declared_feature_names))
    if missing_declared or undeclared:
        raise ValueError(
            "Predictor tables and feature registry disagree: "
            f"missing declared={missing_declared}, undeclared={undeclared}"
        )
    if len(assembled) != len(base):
        raise AssertionError("Predictor assembly changed the legal target-row count.")
    if assembled.duplicated(list(PRIMARY_KEYS)).any():
        raise AssertionError("Predictor assembly created duplicate tract-date keys.")
    if not assembled.loc[:, list(PRIMARY_KEYS)].equals(base_keys):
        raise AssertionError("Predictor assembly changed legal key order or values.")

    for column in registry.loc[registry["role"].eq("model"), "feature_name"]:
        try:
            numeric = pd.to_numeric(assembled[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Model feature {column!r} must be numeric or missing.") from exc
        values = numeric.to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError(f"Model feature {column!r} contains infinite values.")
        assembled[column] = numeric

    return assembled.loc[:, [*PRIMARY_KEYS, TARGET_COLUMN, *declared_feature_names]]


def assemble_precomputed_development_model_table(
    qa_frame: pd.DataFrame,
    feature_table: pd.DataFrame,
    registry: pd.DataFrame,
    *,
    development_start: str | pd.Timestamp,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> pd.DataFrame:
    """Join legal targets to an already promoted target-blind feature table.

    The promoted table may contain a broader tract-date universe than the legal
    target rows, but its schema must equal the registry exactly. Only the target
    value is carried from ``qa_frame`` into the returned model table.
    """

    validate_feature_registry(registry, development_start=development_start)
    if not isinstance(feature_table, pd.DataFrame):
        raise TypeError("Precomputed feature table must be a pandas DataFrame.")
    if feature_table.empty:
        raise ValueError("Precomputed feature table must not be empty.")
    if feature_table.columns.duplicated().any():
        raise ValueError("Precomputed feature table contains duplicate columns.")

    registry_names = registry["feature_name"].tolist()
    observed_names = feature_table.columns.tolist()
    if observed_names != registry_names:
        missing = sorted(set(registry_names) - set(observed_names))
        extra = sorted(set(observed_names) - set(registry_names))
        raise ValueError(
            "Precomputed feature schema or order disagrees with the registry: "
            f"missing={missing}, extra={extra}."
        )

    feature_working = feature_table.copy(deep=False)
    feature_working = feature_working.copy()
    feature_working["tract_geoid"] = feature_working["tract_geoid"].astype("string")
    feature_working["target_date"] = _parse_civil_midnights(
        feature_working["target_date"], field="Precomputed feature target_date"
    )
    if feature_working[list(PRIMARY_KEYS)].isna().any(axis=None):
        raise ValueError("Precomputed feature table has missing tract-date keys.")
    if feature_working.duplicated(list(PRIMARY_KEYS)).any():
        raise ValueError("Precomputed feature table has duplicate tract-date keys.")
    if not unlock_final_test:
        locked = feature_working["target_date"].dt.year.ge(final_test_year)
        if locked.any():
            raise PermissionError(
                f"Precomputed feature table contains {int(locked.sum())} locked rows "
                f"from {final_test_year} or later."
            )

    nonkey_names = [name for name in registry_names if name not in PRIMARY_KEYS]
    numeric = feature_working.loc[:, nonkey_names].apply(pd.to_numeric, errors="raise")
    numeric_values = numeric.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(numeric_values).any():
        raise ValueError("Precomputed features may be finite or missing, not infinite.")
    feature_working.loc[:, nonkey_names] = numeric

    legal = select_absolute_model_rows(
        qa_frame,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )
    base = legal.loc[:, [*PRIMARY_KEYS, TARGET_COLUMN]].copy()
    base["tract_geoid"] = base["tract_geoid"].astype("string")
    base["target_date"] = _parse_civil_midnights(
        base["target_date"], field="Legal model target_date"
    )
    base_keys = base.loc[:, list(PRIMARY_KEYS)].copy()
    assembled = base.merge(
        feature_working,
        on=list(PRIMARY_KEYS),
        how="left",
        sort=False,
        indicator=True,
        validate="one_to_one",
    )
    missing = assembled["_merge"].ne("both")
    if missing.any():
        examples = assembled.loc[missing, list(PRIMARY_KEYS)].head(5)
        raise ValueError(
            f"Precomputed feature table is missing {int(missing.sum())} legal keys:\n"
            f"{examples.to_string(index=False)}"
        )
    assembled = assembled.drop(columns="_merge")
    if len(assembled) != len(base):
        raise AssertionError("Precomputed feature join changed the legal target-row count.")
    if not assembled.loc[:, list(PRIMARY_KEYS)].equals(base_keys):
        raise AssertionError("Precomputed feature join changed legal key order or values.")
    return assembled.loc[:, [*PRIMARY_KEYS, TARGET_COLUMN, *nonkey_names]]


def registered_model_columns(registry: pd.DataFrame) -> list[str]:
    """Return model-feature names in the frozen registry order."""

    return registry.loc[registry["role"].eq("model"), "feature_name"].tolist()


def extract_registered_model_data(
    assembled: pd.DataFrame,
    registry: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.DataFrame]:
    """Separate X, y, keys, and audit-only metadata from an assembled table."""

    model_columns = registered_model_columns(registry)
    audit_columns = registry.loc[
        registry["role"].eq("audit_only"), "feature_name"
    ].tolist()
    required = {*PRIMARY_KEYS, TARGET_COLUMN, *model_columns, *audit_columns}
    missing = sorted(required - set(assembled.columns))
    if missing:
        raise ValueError(f"Assembled model table is missing registered columns: {missing}")
    target = pd.to_numeric(assembled[TARGET_COLUMN], errors="raise")
    if not np.isfinite(target.to_numpy(dtype=float)).all():
        raise ValueError("Assembled model target must contain only finite numeric values.")
    features = assembled.loc[:, model_columns].apply(pd.to_numeric, errors="raise")
    feature_values = features.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(feature_values).any():
        raise ValueError("Assembled model features may be finite or missing, not infinite.")
    return (
        features.copy(),
        target.copy(),
        assembled.loc[:, list(PRIMARY_KEYS)].copy(),
        assembled.loc[:, audit_columns].copy(),
    )
