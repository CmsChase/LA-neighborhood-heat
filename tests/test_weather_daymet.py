import json
from pathlib import Path
from typing import NoReturn

import numpy as np
import pandas as pd
import pytest

from la_heat.weather_daymet import (
    DAYMET_VARIABLES,
    DEFAULT_DAYMET_VARIABLES,
    DERIVED_SRAD_ENERGY_COLUMN,
    DaymetAuditError,
    build_lagged_features,
    fetch_single_pixel_json,
    parse_single_pixel_json,
    reindex_complete_calendar,
)


def _single_pixel_payload(
    years: tuple[int, ...] = (2020,),
    variables: tuple[str, ...] = DEFAULT_DAYMET_VARIABLES,
) -> dict[str, object]:
    year_values: list[float] = []
    yday_values: list[float] = []
    weather: dict[str, list[float]] = {
        DAYMET_VARIABLES[variable].response_key: [] for variable in variables
    }
    first_year = min(years)
    for year in years:
        for yday in range(1, 366):
            value = float((year - first_year) * 1000 + yday)
            year_values.append(float(year))
            yday_values.append(float(yday))
            for variable in variables:
                field = DAYMET_VARIABLES[variable].response_key
                if variable == "dayl":
                    weather[field].append(36_000.0)
                elif variable == "srad":
                    weather[field].append(200.0)
                elif variable == "prcp":
                    weather[field].append(float(yday))
                elif variable == "tmin":
                    weather[field].append(value - 10.0)
                elif variable == "vp":
                    weather[field].append(1_000.0 + value)
                elif variable == "swe":
                    weather[field].append(0.0)
                else:
                    weather[field].append(value)
    return {
        "loc": [34.0522, -118.2437],
        "Tile": "11191",
        "Elevation": "99 m",
        "LCC": [-1614500.0, -721000.0],
        "citation": "Daymet V4 R1 https://doi.org/10.3334/ORNLDAAC/2129",
        "data": {"year": year_values, "yday": yday_values, **weather},
    }


def _weather_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame if column.startswith("daymet_")]


def test_parser_maps_daymet_leap_calendar_and_energy_units() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2020, 2021)),
        expected_years=(2020, 2021),
    )

    assert len(parsed) == 730
    leap_dates = parsed.loc[
        (parsed["year"] == 2020) & parsed["yday"].isin([59, 60, 61, 365]),
        ["yday", "date"],
    ]
    assert leap_dates["date"].tolist() == [
        pd.Timestamp("2020-02-28"),
        pd.Timestamp("2020-02-29"),
        pd.Timestamp("2020-03-01"),
        pd.Timestamp("2020-12-30"),
    ]
    assert pd.Timestamp("2020-12-31") not in set(parsed["date"])
    assert parsed.loc[0, DERIVED_SRAD_ENERGY_COLUMN] == pytest.approx(7.2)
    assert parsed.attrs["units"]["srad_w_m2"] == "W/m^2"
    assert parsed.attrs["units"]["dayl_s"] == "s/day"
    assert parsed.attrs["units"][DERIVED_SRAD_ENERGY_COLUMN] == "MJ/m^2/day"


def test_complete_calendar_inserts_leap_december_31_without_filling() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2020, 2021)),
        expected_years=(2020, 2021),
    )
    dense = reindex_complete_calendar(parsed)

    assert len(dense) == 731
    assert dense.loc["2020-02-29", "daymet_source_present"]
    assert not dense.loc["2020-02-29", "daymet_structural_calendar_gap"]
    assert not dense.loc["2020-12-31", "daymet_source_present"]
    assert dense.loc["2020-12-31", "daymet_structural_calendar_gap"]
    assert pd.isna(dense.loc["2020-12-31", "tmax_c"])
    assert dense.attrs["missing_civil_dates"] == ("2020-12-31T00:00:00",)


def test_lagged_features_use_d_minus_one_not_target_day() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2021,)),
        expected_years=(2021,),
    )
    features = build_lagged_features(parsed)
    on_january_8 = features.loc[pd.Timestamp("2021-01-08")]

    assert on_january_8["daymet_tmax_c_mean_prev_1d"] == pytest.approx(7.0)
    assert on_january_8["daymet_tmax_c_mean_prev_3d"] == pytest.approx(6.0)
    assert on_january_8["daymet_tmax_c_mean_prev_7d"] == pytest.approx(4.0)
    assert on_january_8["daymet_prcp_mm_sum_prev_1d"] == pytest.approx(7.0)
    assert on_january_8["daymet_prcp_mm_sum_prev_3d"] == pytest.approx(18.0)
    assert on_january_8["daymet_prcp_mm_sum_prev_7d"] == pytest.approx(28.0)
    assert on_january_8[
        "daymet_srad_energy_mj_m2_sum_prev_7d"
    ] == pytest.approx(50.4)
    assert features.loc["2021-01-01"].isna().all()
    assert features.attrs["target_day_observations_included"] is False
    assert features.attrs["window_definition"] == "complete civil days d-n through d-1"
    assert features.attrs["units"]["daymet_prcp_mm_sum_prev_7d"] == "mm"


def test_february_29_is_d_minus_one_for_march_1() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2020,)),
        expected_years=(2020,),
    )
    features = build_lagged_features(parsed)

    assert features.loc[
        "2020-03-01", "daymet_tmax_c_mean_prev_1d"
    ] == pytest.approx(60.0)


def test_target_day_and_future_values_cannot_change_target_features() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2021,)),
        expected_years=(2021,),
    )
    target_date = pd.Timestamp("2021-07-15")
    baseline = build_lagged_features(parsed).loc[target_date]

    perturbed = parsed.copy()
    future = perturbed["date"] >= target_date
    perturbed.loc[future, "tmax_c"] = 9_999.0
    changed = build_lagged_features(perturbed).loc[target_date]

    pd.testing.assert_series_equal(baseline, changed)


def test_leap_year_missing_day_invalidates_then_releases_each_window() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2020, 2021)),
        expected_years=(2020, 2021),
    )
    features = build_lagged_features(parsed)

    assert features.loc["2021-01-01"].isna().all()
    assert features.loc["2021-01-02"].filter(like="prev_1d").notna().all()
    assert features.loc["2021-01-02"].filter(like="prev_3d").isna().all()
    assert features.loc["2021-01-04"].filter(like="prev_3d").notna().all()
    assert features.loc["2021-01-04"].filter(like="prev_7d").isna().all()
    assert features.loc["2021-01-08", _weather_columns(features)].notna().all()


def test_arbitrary_missing_day_is_not_filled_and_windows_fail_closed() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2021,)),
        expected_years=(2021,),
    )
    parsed = parsed.loc[parsed["date"] != pd.Timestamp("2021-06-15")].copy()
    dense = reindex_complete_calendar(parsed)
    features = build_lagged_features(parsed)

    assert not dense.loc["2021-06-15", "daymet_source_present"]
    assert not dense.loc["2021-06-15", "daymet_structural_calendar_gap"]
    assert pd.isna(dense.loc["2021-06-15", "tmax_c"])
    assert features.loc["2021-06-16"].isna().all()
    assert features.loc["2021-06-17"].filter(like="prev_1d").notna().all()
    assert features.loc["2021-06-17"].filter(like="prev_3d").isna().all()
    assert features.loc["2021-06-22"].filter(like="prev_7d").isna().all()
    assert features.loc["2021-06-23"].filter(like="prev_7d").notna().all()


def test_parser_rejects_a_year_without_365_rows() -> None:
    payload = _single_pixel_payload((2020,))
    data = payload["data"]
    assert isinstance(data, dict)
    for values in data.values():
        assert isinstance(values, list)
        values.pop()

    with pytest.raises(DaymetAuditError, match="expected 365"):
        parse_single_pixel_json(payload, expected_years=(2020,))


def test_parser_rejects_response_year_mismatch() -> None:
    with pytest.raises(DaymetAuditError, match="do not match requested years"):
        parse_single_pixel_json(
            _single_pixel_payload((2020,)),
            expected_years=(2021,),
        )


def test_parser_rejects_missing_or_unrequested_variables() -> None:
    missing = _single_pixel_payload((2020,))
    missing_data = missing["data"]
    assert isinstance(missing_data, dict)
    del missing_data[DAYMET_VARIABLES["vp"].response_key]
    with pytest.raises(DaymetAuditError, match="variables do not match"):
        parse_single_pixel_json(missing, expected_years=(2020,))

    unexpected = _single_pixel_payload(
        (2020,), (*DEFAULT_DAYMET_VARIABLES, "swe")
    )
    with pytest.raises(DaymetAuditError, match="variables do not match"):
        parse_single_pixel_json(unexpected, expected_years=(2020,))


def test_parser_rejects_duplicate_yday_and_nonfinite_weather() -> None:
    duplicate = _single_pixel_payload((2020,))
    duplicate_data = duplicate["data"]
    assert isinstance(duplicate_data, dict)
    yday = duplicate_data["yday"]
    assert isinstance(yday, list)
    yday[-1] = 364.0
    with pytest.raises(DaymetAuditError, match="duplicate year/yday"):
        parse_single_pixel_json(duplicate, expected_years=(2020,))

    nonfinite = _single_pixel_payload((2020,))
    nonfinite_data = nonfinite["data"]
    assert isinstance(nonfinite_data, dict)
    tmax = nonfinite_data[DAYMET_VARIABLES["tmax"].response_key]
    assert isinstance(tmax, list)
    tmax[0] = np.nan
    with pytest.raises(DaymetAuditError, match="non-finite"):
        parse_single_pixel_json(nonfinite, expected_years=(2020,))


def test_parser_rejects_physically_invalid_weather() -> None:
    negative = _single_pixel_payload((2020,))
    negative_data = negative["data"]
    assert isinstance(negative_data, dict)
    precipitation = negative_data[DAYMET_VARIABLES["prcp"].response_key]
    assert isinstance(precipitation, list)
    precipitation[0] = -0.1
    with pytest.raises(DaymetAuditError, match="negative values"):
        parse_single_pixel_json(negative, expected_years=(2020,))

    inverted_temperature = _single_pixel_payload((2020,))
    inverted_data = inverted_temperature["data"]
    assert isinstance(inverted_data, dict)
    tmax = inverted_data[DAYMET_VARIABLES["tmax"].response_key]
    assert isinstance(tmax, list)
    tmax[0] = -100.0
    with pytest.raises(DaymetAuditError, match="tmax cannot be lower than tmin"):
        parse_single_pixel_json(inverted_temperature, expected_years=(2020,))


def test_complete_calendar_rejects_non_civil_bounds() -> None:
    parsed = parse_single_pixel_json(
        _single_pixel_payload((2020,)),
        expected_years=(2020,),
    )

    with pytest.raises(ValueError, match="timezone-naive"):
        reindex_complete_calendar(
            parsed,
            start=pd.Timestamp("2020-01-01", tz="UTC"),
        )
    with pytest.raises(ValueError, match="local midnight"):
        reindex_complete_calendar(
            parsed,
            start=pd.Timestamp("2020-01-01 12:00:00"),
        )


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class _RecordingClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object], object]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        timeout: object,
    ) -> _FakeResponse:
        self.calls.append((url, params, timeout))
        return _FakeResponse(self.payload)


class _NoNetworkClient:
    def get(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("A valid Daymet cache must not make a network request.")


def test_raw_response_cache_is_request_bound_and_avoids_network(tmp_path: Path) -> None:
    cache = tmp_path / "daymet.json"
    payload = _single_pixel_payload((2020,))
    client = _RecordingClient(payload)

    first = fetch_single_pixel_json(
        latitude=34.0522,
        longitude=-118.2437,
        years=(2020,),
        cache_path=cache,
        http_client=client,
    )
    assert first is payload
    assert len(client.calls) == 1
    assert client.calls[0][1]["format"] == "json"
    assert client.calls[0][1]["years"] == "2020"

    envelope = json.loads(cache.read_text(encoding="utf-8"))
    assert envelope["response"]["Tile"] == "11191"
    assert len(envelope["response_sha256"]) == 64
    cached = fetch_single_pixel_json(
        latitude=34.0522,
        longitude=-118.2437,
        years=(2020,),
        cache_path=cache,
        http_client=_NoNetworkClient(),
    )
    assert cached["Tile"] == "11191"

    with pytest.raises(DaymetAuditError, match="cache request does not match"):
        fetch_single_pixel_json(
            latitude=34.0522,
            longitude=-118.0,
            years=(2020,),
            cache_path=cache,
            http_client=_NoNetworkClient(),
        )

    envelope["response"]["Tile"] = "tampered"
    cache.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(DaymetAuditError, match="cached response hash"):
        fetch_single_pixel_json(
            latitude=34.0522,
            longitude=-118.2437,
            years=(2020,),
            cache_path=cache,
            http_client=_NoNetworkClient(),
        )
