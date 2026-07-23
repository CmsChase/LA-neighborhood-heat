import numpy as np
import pandas as pd
import pytest

from la_heat.calendar_features import (
    CALENDAR_FEATURE_REGISTRY_COLUMNS,
    CalendarFeatureError,
    build_calendar_features,
    calendar_feature_registry_rows,
)
from la_heat.feature_registry import (
    CALENDAR_FEATURE_AVAILABLE_BY,
    CALENDAR_FEATURE_SOURCE,
    CALENDAR_MODEL_FEATURE_NAMES,
)


def _keys() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["06037101110", "06037101120", "06037101110"],
            "target_date": ["2024-12-31", "2023-12-31", "2024-01-01"],
        }
    )


def test_registry_factory_emits_only_the_frozen_known_origin_pair() -> None:
    rows = calendar_feature_registry_rows()

    assert tuple(rows.columns) == CALENDAR_FEATURE_REGISTRY_COLUMNS
    assert tuple(rows["feature_name"]) == CALENDAR_MODEL_FEATURE_NAMES
    assert rows["family"].eq("calendar").all()
    assert rows["role"].eq("model").all()
    assert rows["units"].eq("unitless").all()
    assert rows["source"].eq(CALENDAR_FEATURE_SOURCE).all()
    assert rows["static"].eq(False).all()  # noqa: E712
    assert rows["available_by"].eq(CALENDAR_FEATURE_AVAILABLE_BY).all()
    assert rows[["source_start_offset_days", "source_end_offset_days"]].isna().all(
        axis=None
    )


def test_builder_is_shuffle_safe_exact_and_does_not_mutate_input() -> None:
    keys = _keys()
    before = keys.copy(deep=True)

    result = build_calendar_features(keys)
    shuffled = build_calendar_features(keys.sample(frac=1.0, random_state=29))

    pd.testing.assert_frame_equal(result, shuffled)
    pd.testing.assert_frame_equal(keys, before)
    assert result.columns.tolist() == [
        "tract_geoid",
        "target_date",
        "calendar_doy_sin",
        "calendar_doy_cos",
    ]
    assert result["target_date"].is_monotonic_increasing


def test_phase_uses_the_correct_leap_and_nonleap_year_lengths() -> None:
    result = build_calendar_features(_keys()).set_index(
        ["tract_geoid", "target_date"]
    )

    leap = result.loc[("06037101110", pd.Timestamp("2024-12-31"))]
    nonleap = result.loc[("06037101120", pd.Timestamp("2023-12-31"))]
    leap_phase = 2.0 * np.pi * 365.0 / 366.0
    nonleap_phase = 2.0 * np.pi * 364.0 / 365.0
    assert leap["calendar_doy_sin"] == pytest.approx(np.sin(leap_phase))
    assert leap["calendar_doy_cos"] == pytest.approx(np.cos(leap_phase))
    assert nonleap["calendar_doy_sin"] == pytest.approx(np.sin(nonleap_phase))
    assert nonleap["calendar_doy_cos"] == pytest.approx(np.cos(nonleap_phase))

    january_first = result.loc[("06037101110", pd.Timestamp("2024-01-01"))]
    assert january_first["calendar_doy_sin"] == pytest.approx(0.0, abs=1e-15)
    assert january_first["calendar_doy_cos"] == pytest.approx(1.0)


def test_features_are_finite_and_lie_on_the_unit_circle() -> None:
    dates = pd.date_range("2020-01-01", "2024-12-31", freq="17D")
    keys = pd.DataFrame(
        {
            "tract_geoid": [f"tract-{index:03d}" for index in range(len(dates))],
            "target_date": dates,
        }
    )

    result = build_calendar_features(keys)
    sine = result["calendar_doy_sin"].to_numpy()
    cosine = result["calendar_doy_cos"].to_numpy()
    assert np.isfinite(sine).all()
    assert np.isfinite(cosine).all()
    np.testing.assert_allclose(sine**2 + cosine**2, 1.0, atol=1e-15, rtol=0.0)


@pytest.mark.parametrize("locked_date", ["2025-01-01", "2026-07-01"])
def test_locked_final_test_dates_are_rejected(locked_date: str) -> None:
    keys = _keys().iloc[[0]].copy()
    keys.loc[:, "target_date"] = locked_date

    with pytest.raises(PermissionError, match="2025 or later"):
        build_calendar_features(keys)


def test_final_test_calendar_features_require_explicit_unlock() -> None:
    keys = pd.DataFrame(
        {"tract_geoid": ["06037101110"], "target_date": ["2025-07-01"]}
    )

    result = build_calendar_features(keys, unlock_final_test=True)

    assert result["target_date"].tolist() == [pd.Timestamp("2025-07-01")]
    assert np.isfinite(
        result.loc[:, ["calendar_doy_sin", "calendar_doy_cos"]].to_numpy()
    ).all()


@pytest.mark.parametrize(
    "invalid_date",
    ["2024-07-01 12:00:00", "2024-07-01T00:00:00Z"],
)
def test_nonmidnight_or_timezone_aware_dates_are_rejected(invalid_date: str) -> None:
    keys = _keys().iloc[[0]].copy()
    keys.loc[:, "target_date"] = invalid_date

    with pytest.raises(CalendarFeatureError, match="timezone-naive civil midnights"):
        build_calendar_features(keys)


def test_duplicate_keys_are_rejected_after_date_parsing() -> None:
    keys = pd.DataFrame(
        {
            "tract_geoid": ["06037101110", "06037101110"],
            "target_date": ["2024-07-01", pd.Timestamp("2024-07-01")],
        }
    )

    with pytest.raises(CalendarFeatureError, match="duplicate tract-date keys"):
        build_calendar_features(keys)


@pytest.mark.parametrize("extra_column", ["target_lst_c", "daymet_tmax_c", "year"])
def test_target_predictor_and_raw_calendar_inputs_are_not_accepted(
    extra_column: str,
) -> None:
    keys = _keys().assign(**{extra_column: 999.0})

    with pytest.raises(CalendarFeatureError, match="exactly tract_geoid,target_date"):
        build_calendar_features(keys)


def test_wrong_key_order_empty_input_and_noncanonical_ids_are_rejected() -> None:
    with pytest.raises(CalendarFeatureError, match="in that order"):
        build_calendar_features(_keys().loc[:, ["target_date", "tract_geoid"]])

    with pytest.raises(CalendarFeatureError, match="must not be empty"):
        build_calendar_features(_keys().iloc[0:0])

    invalid_geoid = _keys().iloc[[0]].copy()
    invalid_geoid.loc[:, "tract_geoid"] = " 06037101110"
    with pytest.raises(CalendarFeatureError, match="canonical strings"):
        build_calendar_features(invalid_geoid)
