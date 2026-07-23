import pandas as pd
import pytest

from la_heat.final_test_predictor_base import (
    FinalTestPredictorBaseError,
    build_predictor_base_frame,
)


def _keys(year: int = 2025) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["a", "b"],
            "target_date": pd.to_datetime([f"{year}-07-01", f"{year}-07-01"]),
            "overpass_id": ["one", "one"],
            "platform": ["landsat-9", "landsat-9"],
            "spatial_block": ["x", "y"],
            "latitude_quartile": [1, 2],
            "longitude_quartile": [3, 4],
        }
    )


def _static() -> pd.DataFrame:
    payload: dict[str, object] = {"tract_geoid": ["a", "b"]}
    payload.update({f"static_{index}": [index, index + 1] for index in range(18)})
    return pd.DataFrame(payload)


def _model_features() -> list[str]:
    return [
        *(f"static_{index}" for index in range(18)),
        "calendar_doy_sin",
        "calendar_doy_cos",
        "daymet_placeholder",
        "sentinel_placeholder",
    ]


def test_predictor_base_is_complete_and_exact_2025() -> None:
    result, features = build_predictor_base_frame(
        _keys(), _static(), model_feature_names=_model_features()
    )
    assert len(result) == 2
    assert len(features) == 20
    assert result["target_date"].dt.year.eq(2025).all()
    assert result[features].notna().all(axis=None)
    assert "target_lst_c" not in result


def test_predictor_base_rejects_nonfinal_year_before_calendar_unlock() -> None:
    with pytest.raises(FinalTestPredictorBaseError, match="unique 2025"):
        build_predictor_base_frame(
            _keys(2026), _static(), model_feature_names=_model_features()
        )


def test_predictor_base_rejects_target_derived_column() -> None:
    keys = _keys()
    keys["target_lst_c"] = 42.0
    with pytest.raises(FinalTestPredictorBaseError, match="schema drifted"):
        build_predictor_base_frame(
            keys, _static(), model_feature_names=_model_features()
        )
