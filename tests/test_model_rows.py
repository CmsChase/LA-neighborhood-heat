import pandas as pd
import pytest

from la_heat.model_rows import select_absolute_model_rows


def _qa_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["06037000100", "06037000200", "06037000300"],
            "target_date": ["2024-07-01", "2024-07-01", "2024-07-01"],
            "target_available": [True, True, False],
            "date_usable": [True, False, True],
            "target_lst_c": [35.0, 36.0, float("nan")],
            "valid_pixel_count": [100, 100, 0],
        },
        index=[10, 20, 30],
    )


def test_absolute_selector_excludes_target_available_rows_from_unusable_dates() -> None:
    frame = _qa_frame()
    before = frame.copy(deep=True)

    naive = frame.loc[frame["target_available"]]
    selected = select_absolute_model_rows(frame)

    assert naive.index.tolist() == [10, 20]
    assert selected.index.tolist() == [10]
    pd.testing.assert_frame_equal(selected, before.loc[[10]])
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("locked_date", ["2025-01-01", "2026-01-01"])
def test_absolute_selector_rejects_final_test_year_and_later_by_default(
    locked_date: str,
) -> None:
    frame = _qa_frame().iloc[[0]].copy()
    frame["target_date"] = locked_date

    with pytest.raises(PermissionError, match="2025 or later"):
        select_absolute_model_rows(frame, final_test_year=2025)


def test_absolute_selector_requires_explicit_final_test_unlock() -> None:
    frame = _qa_frame().iloc[[0]].copy()
    frame["target_date"] = "2025-07-01"

    selected = select_absolute_model_rows(
        frame,
        final_test_year=2025,
        unlock_final_test=True,
    )

    pd.testing.assert_frame_equal(selected, frame)


def test_absolute_selector_rejects_missing_required_columns() -> None:
    frame = _qa_frame().drop(columns="date_usable")

    with pytest.raises(ValueError, match="missing required columns.*date_usable"):
        select_absolute_model_rows(frame)


def test_absolute_selector_rejects_duplicate_tract_date_keys() -> None:
    frame = pd.concat([_qa_frame().iloc[[0]], _qa_frame().iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="Duplicate tract-date keys"):
        select_absolute_model_rows(frame)


def test_absolute_selector_rejects_available_row_without_target() -> None:
    frame = _qa_frame().iloc[[0]].copy()
    frame["target_lst_c"] = float("nan")

    with pytest.raises(ValueError, match="availability disagrees"):
        select_absolute_model_rows(frame)


@pytest.mark.parametrize("column", ["target_available", "date_usable"])
def test_absolute_selector_rejects_non_boolean_qa_flags(column: str) -> None:
    frame = _qa_frame()
    frame[column] = frame[column].astype(object)
    frame.loc[10, column] = None

    with pytest.raises(ValueError, match="non-null booleans"):
        select_absolute_model_rows(frame)
