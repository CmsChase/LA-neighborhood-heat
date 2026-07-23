import pandas as pd
import pytest

from la_heat.model_dataset import (
    PredictorTable,
    assemble_development_model_table,
    extract_registered_model_data,
)


def _qa() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "a"],
            "target_date": ["2024-07-01", "2024-07-01", "2024-07-17"],
            "target_available": [True, True, True],
            "date_usable": [True, True, False],
            "target_lst_c": [35.0, 36.0, 37.0],
            "median_st_uncertainty_k": [1.0, 2.0, 3.0],
        }
    )


def _registry() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_name": "tract_geoid",
                "family": "key",
                "role": "key",
                "units": "string",
                "source": "Census",
                "static": True,
                "available_by": "2019-01-01",
                "source_start_offset_days": None,
                "source_end_offset_days": None,
            },
            {
                "feature_name": "target_date",
                "family": "key",
                "role": "key",
                "units": "date",
                "source": "target schedule",
                "static": False,
                "available_by": "2019-01-01",
                "source_start_offset_days": None,
                "source_end_offset_days": None,
            },
            {
                "feature_name": "elevation_mean_m",
                "family": "geography",
                "role": "model",
                "units": "m",
                "source": "SRTM",
                "static": True,
                "available_by": "2015-01-01",
                "source_start_offset_days": None,
                "source_end_offset_days": None,
            },
            {
                "feature_name": "daymet_tmax_mean_1d_c",
                "family": "weather",
                "role": "model",
                "units": "degC",
                "source": "Daymet V4 R1",
                "static": False,
                "available_by": "2026-01-01",
                "source_start_offset_days": -1,
                "source_end_offset_days": -1,
            },
            {
                "feature_name": "daymet_source_age_days",
                "family": "weather",
                "role": "audit_only",
                "units": "days",
                "source": "Daymet V4 R1",
                "static": False,
                "available_by": "2026-01-01",
                "source_start_offset_days": -1,
                "source_end_offset_days": -1,
            },
        ]
    )


def _tables() -> list[PredictorTable]:
    static = pd.DataFrame(
        {"tract_geoid": ["a", "b"], "elevation_mean_m": [100.0, 200.0]}
    )
    weather = pd.DataFrame(
        {
            "tract_geoid": ["a", "b"],
            "target_date": ["2024-07-01", "2024-07-01"],
            "daymet_tmax_mean_1d_c": [30.0, None],
            "daymet_source_age_days": [1, 1],
        }
    )
    return [
        PredictorTable("static", static, ("tract_geoid",)),
        PredictorTable("weather", weather, ("tract_geoid", "target_date")),
    ]


def test_assembly_preserves_legal_rows_and_registered_missing_values() -> None:
    assembled = assemble_development_model_table(
        _qa(), _tables(), _registry(), development_start="2020-05-01"
    )
    assert len(assembled) == 2
    assert assembled["tract_geoid"].tolist() == ["a", "b"]
    assert pd.isna(assembled.loc[1, "daymet_tmax_mean_1d_c"])
    assert "median_st_uncertainty_k" not in assembled.columns

    features, target, keys, audit = extract_registered_model_data(
        assembled, _registry()
    )
    assert features.columns.tolist() == [
        "elevation_mean_m",
        "daymet_tmax_mean_1d_c",
    ]
    assert target.tolist() == [35.0, 36.0]
    assert keys.columns.tolist() == ["tract_geoid", "target_date"]
    assert audit.columns.tolist() == ["daymet_source_age_days"]


def test_assembly_rejects_missing_dynamic_keys_instead_of_silent_row_missingness() -> None:
    tables = _tables()
    weather = tables[1].frame.iloc[[0]].copy()
    tables[1] = PredictorTable("weather", weather, ("tract_geoid", "target_date"))
    with pytest.raises(ValueError, match="missing 1 required keys"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_duplicate_predictor_keys() -> None:
    tables = _tables()
    duplicated = pd.concat([tables[0].frame, tables[0].frame.iloc[[0]]], ignore_index=True)
    tables[0] = PredictorTable("static", duplicated, ("tract_geoid",))
    with pytest.raises(ValueError, match="duplicate keys"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_locked_or_later_predictor_rows() -> None:
    tables = _tables()
    locked = tables[1].frame.copy()
    locked.loc[0, "target_date"] = "2026-07-01"
    tables[1] = PredictorTable("weather", locked, ("tract_geoid", "target_date"))
    with pytest.raises(PermissionError, match="2025 or later"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_target_derived_predictor_columns() -> None:
    tables = _tables()
    tables[0].frame["lst_anomaly_c"] = [0.1, -0.1]
    with pytest.raises(ValueError, match="target-derived"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_requires_registry_and_tables_to_agree() -> None:
    tables = _tables()
    tables[0].frame["unregistered_value"] = [1.0, 2.0]
    with pytest.raises(ValueError, match="registry disagree"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_static_feature_in_dynamic_table() -> None:
    tables = _tables()
    tables[0] = PredictorTable(
        "static", tables[0].frame.loc[:, ["tract_geoid"]], ("tract_geoid",)
    )
    weather = tables[1].frame.copy()
    weather["elevation_mean_m"] = [100.0, 200.0]
    tables[1] = PredictorTable(
        "weather", weather, ("tract_geoid", "target_date")
    )

    with pytest.raises(ValueError, match="key type disagrees"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_dynamic_feature_in_static_table() -> None:
    tables = _tables()
    static = tables[0].frame.copy()
    static["daymet_tmax_mean_1d_c"] = [30.0, None]
    tables[0] = PredictorTable("static", static, ("tract_geoid",))
    tables[1] = PredictorTable(
        "weather",
        tables[1].frame.drop(columns="daymet_tmax_mean_1d_c"),
        ("tract_geoid", "target_date"),
    )

    with pytest.raises(ValueError, match="key type disagrees"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_rejects_dynamic_key_outside_default_universe() -> None:
    tables = _tables()
    extra = pd.DataFrame(
        {
            "tract_geoid": ["a"],
            "target_date": ["2024-07-17"],
            "daymet_tmax_mean_1d_c": [31.0],
            "daymet_source_age_days": [1],
        }
    )
    tables[1] = PredictorTable(
        "weather",
        pd.concat([tables[1].frame, extra], ignore_index=True),
        ("tract_geoid", "target_date"),
    )

    with pytest.raises(ValueError, match="outside the frozen predictor universe"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_explicit_predictor_universe_can_include_feature_only_keys() -> None:
    tables = _tables()
    extra = pd.DataFrame(
        {
            "tract_geoid": ["a"],
            "target_date": ["2024-07-17"],
            "daymet_tmax_mean_1d_c": [31.0],
            "daymet_source_age_days": [1],
        }
    )
    tables[1] = PredictorTable(
        "weather",
        pd.concat([tables[1].frame, extra], ignore_index=True),
        ("tract_geoid", "target_date"),
    )
    universe = tables[1].frame.loc[:, ["tract_geoid", "target_date"]].copy()

    assembled = assemble_development_model_table(
        _qa(),
        tables,
        _registry(),
        development_start="2020-05-01",
        predictor_key_universe=universe,
    )

    assert len(assembled) == 2
    assert assembled[["tract_geoid", "target_date"]].equals(
        pd.DataFrame(
            {
                "tract_geoid": ["a", "b"],
                "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]),
            }
        )
    )


@pytest.mark.parametrize("location", ["qa", "table", "universe"])
def test_assembly_rejects_non_midnight_dates(location: str) -> None:
    qa = _qa()
    tables = _tables()
    universe = None
    if location == "qa":
        qa["target_date"] = pd.to_datetime(qa["target_date"])
        qa.loc[0, "target_date"] = pd.Timestamp("2024-07-01 12:00:00")
    elif location == "table":
        weather = tables[1].frame.copy()
        weather["target_date"] = pd.to_datetime(weather["target_date"])
        weather.loc[0, "target_date"] = pd.Timestamp("2024-07-01 12:00:00")
        tables[1] = PredictorTable(
            "weather", weather, ("tract_geoid", "target_date")
        )
    else:
        universe = tables[1].frame.loc[:, ["tract_geoid", "target_date"]].copy()
        universe["target_date"] = pd.to_datetime(universe["target_date"])
        universe.loc[0, "target_date"] = pd.Timestamp("2024-07-01 12:00:00")

    with pytest.raises(ValueError, match="civil midnights"):
        assemble_development_model_table(
            qa,
            tables,
            _registry(),
            development_start="2020-05-01",
            predictor_key_universe=universe,
        )


def test_assembly_rejects_duplicate_predictor_columns() -> None:
    tables = _tables()
    duplicate_columns = pd.DataFrame(
        [["a", 100.0, 101.0], ["b", 200.0, 201.0]],
        columns=["tract_geoid", "elevation_mean_m", "elevation_mean_m"],
    )
    tables[0] = PredictorTable("static", duplicate_columns, ("tract_geoid",))

    with pytest.raises(ValueError, match="duplicate columns"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )


def test_assembly_and_extraction_reject_infinite_model_features() -> None:
    tables = _tables()
    tables[0].frame.loc[0, "elevation_mean_m"] = float("inf")
    with pytest.raises(ValueError, match="infinite values"):
        assemble_development_model_table(
            _qa(), tables, _registry(), development_start="2020-05-01"
        )

    assembled = assemble_development_model_table(
        _qa(), _tables(), _registry(), development_start="2020-05-01"
    )
    assembled.loc[0, "elevation_mean_m"] = float("-inf")
    with pytest.raises(ValueError, match="not infinite"):
        extract_registered_model_data(assembled, _registry())
