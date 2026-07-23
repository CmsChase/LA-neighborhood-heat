"""Fail-closed selection of absolute-LST rows for model development."""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_ABSOLUTE_MODEL_COLUMNS = frozenset(
    {
        "tract_geoid",
        "target_date",
        "target_available",
        "date_usable",
        "target_lst_c",
    }
)


def select_absolute_model_rows(
    qa_frame: pd.DataFrame,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> pd.DataFrame:
    """Return untouched QA rows that are legal absolute-LST modeling observations.

    The selector validates the complete input before taking a row subset. By default,
    the presence of any row from ``final_test_year`` or later is an error rather than a
    silent filter. Passing ``unlock_final_test=True`` is therefore an explicit access
    decision that should only be sourced from the versioned research configuration.
    """

    if not isinstance(qa_frame, pd.DataFrame):
        raise TypeError("Absolute-model row selection requires a pandas DataFrame.")
    if isinstance(final_test_year, bool) or not isinstance(final_test_year, int):
        raise TypeError("final_test_year must be an integer calendar year.")
    if final_test_year < 1:
        raise ValueError("final_test_year must be a positive calendar year.")
    if not isinstance(unlock_final_test, bool):
        raise TypeError("unlock_final_test must be a boolean.")

    missing = sorted(REQUIRED_ABSOLUTE_MODEL_COLUMNS - set(qa_frame.columns))
    if missing:
        raise ValueError(f"Absolute-model QA table is missing required columns: {missing}")

    key_columns = ["tract_geoid", "target_date"]
    missing_key = qa_frame[key_columns].isna().any(axis=1)
    if missing_key.any():
        examples = qa_frame.loc[missing_key, key_columns].head(5)
        raise ValueError(
            "Absolute-model QA table has missing tract-date keys:\n"
            f"{examples.to_string(index=False)}"
        )
    duplicate = qa_frame.duplicated(key_columns, keep=False)
    if duplicate.any():
        examples = qa_frame.loc[duplicate, key_columns].head(5)
        raise ValueError(
            "Duplicate tract-date keys in absolute-model QA table:\n"
            f"{examples.to_string(index=False)}"
        )

    try:
        target_dates = pd.to_datetime(qa_frame["target_date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("Absolute-model QA table contains invalid target dates.") from exc
    if target_dates.isna().any():
        raise ValueError("Absolute-model QA table contains missing target dates.")

    if not unlock_final_test:
        locked = target_dates.dt.year >= final_test_year
        if locked.any():
            examples = qa_frame.loc[locked, key_columns].head(5)
            raise PermissionError(
                f"Found {int(locked.sum())} locked rows from {final_test_year} or later:\n"
                f"{examples.to_string(index=False)}"
            )

    for column in ("target_available", "date_usable"):
        values = qa_frame[column]
        valid_boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
        if not valid_boolean.all():
            examples = qa_frame.loc[~valid_boolean, key_columns + [column]].head(5)
            raise ValueError(
                f"Absolute-model QA flag {column!r} must contain only non-null booleans:\n"
                f"{examples.to_string(index=False)}"
            )

    target_available = qa_frame["target_available"].astype(bool)
    target_present = qa_frame["target_lst_c"].notna()
    availability_mismatch = target_available != target_present
    if availability_mismatch.any():
        examples = qa_frame.loc[
            availability_mismatch,
            key_columns + ["target_available", "target_lst_c"],
        ].head(5)
        raise ValueError(
            "Absolute-model target availability disagrees with target_lst_c presence:\n"
            f"{examples.to_string(index=False)}"
        )

    selected = target_available & qa_frame["date_usable"].astype(bool) & target_present
    if selected.any():
        try:
            numeric_target = pd.to_numeric(
                qa_frame.loc[selected, "target_lst_c"], errors="raise"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Selected target_lst_c values must be numeric.") from exc
        if not np.isfinite(numeric_target.to_numpy(dtype=float)).all():
            raise ValueError("Selected target_lst_c values must be finite.")

    return qa_frame.loc[selected].copy(deep=True)
