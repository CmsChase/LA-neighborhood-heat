import numpy as np
import pandas as pd
import pytest

from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.feature_registry import FeatureRegistryError, validate_feature_registry


def _registry() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_name": "tract_geoid",
                "family": "key",
                "role": "key",
                "units": "identifier",
                "source": "Census TIGER/Line",
                "static": True,
                "available_by": "2019-01-01",
                "source_start_offset_days": np.nan,
                "source_end_offset_days": np.nan,
            },
            {
                "feature_name": "target_date",
                "family": "key",
                "role": "key",
                "units": "date",
                "source": "Landsat inventory",
                "static": False,
                "available_by": "target date",
                "source_start_offset_days": np.nan,
                "source_end_offset_days": np.nan,
            },
            {
                "feature_name": "daymet_tmax_7d_mean_c",
                "family": "weather",
                "role": "model",
                "units": "degC",
                "source": "Daymet V4R1",
                "static": False,
                "available_by": "target day -1",
                "source_start_offset_days": -7,
                "source_end_offset_days": -1,
            },
            {
                "feature_name": "forest_cover_fraction",
                "family": "land_use",
                "role": "model",
                "units": "fraction",
                "source": "NLCD 2016 original release",
                "static": True,
                "available_by": "2019-04-30",
                "source_start_offset_days": np.nan,
                "source_end_offset_days": np.nan,
            },
            {
                "feature_name": "valid_pixel_count",
                "family": "target_qa",
                "role": "audit_only",
                "units": "pixels",
                "source": "Landsat target QA",
                "static": False,
                "available_by": "after target construction",
                "source_start_offset_days": 0,
                "source_end_offset_days": 0,
            },
        ]
    )


def test_valid_registry_passes_without_mutation() -> None:
    registry = _registry()
    before = registry.copy(deep=True)

    assert validate_feature_registry(registry, development_start="2020-05-01") is None

    pd.testing.assert_frame_equal(registry, before)


def test_registry_requires_dataframe_and_complete_schema() -> None:
    with pytest.raises(TypeError, match="pandas DataFrame"):
        validate_feature_registry([], development_start="2020-05-01")  # type: ignore[arg-type]

    with pytest.raises(FeatureRegistryError, match="missing required columns.*units"):
        validate_feature_registry(
            _registry().drop(columns="units"), development_start="2020-05-01"
        )


def test_registry_rejects_blank_metadata_and_non_boolean_static() -> None:
    blank = _registry()
    blank.loc[2, "source"] = "  "
    with pytest.raises(FeatureRegistryError, match="'source'.*non-empty strings"):
        validate_feature_registry(blank, development_start="2020-05-01")

    non_boolean = _registry()
    non_boolean["static"] = non_boolean["static"].astype(object)
    non_boolean.loc[2, "static"] = 0
    with pytest.raises(FeatureRegistryError, match="'static'.*only booleans"):
        validate_feature_registry(non_boolean, development_start="2020-05-01")


@pytest.mark.parametrize(
    "duplicate_name",
    ["TRACT_GEOID", "tract-geoid"],
)
def test_feature_names_are_unique_after_normalization(duplicate_name: str) -> None:
    registry = pd.concat([_registry(), _registry().iloc[[0]]], ignore_index=True)
    registry.loc[len(registry) - 1, "feature_name"] = duplicate_name

    with pytest.raises(FeatureRegistryError, match="must be unique"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_registry_rejects_invalid_role() -> None:
    registry = _registry()
    registry.loc[2, "role"] = "predictor"

    with pytest.raises(FeatureRegistryError, match="invalid roles.*predictor"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_required_keys_must_exist_and_may_only_be_keys() -> None:
    missing = _registry().loc[lambda frame: frame["feature_name"] != "target_date"]
    with pytest.raises(FeatureRegistryError, match="missing required key.*target_date"):
        validate_feature_registry(missing, development_start="2020-05-01")

    model_key = _registry()
    model_key.loc[model_key["feature_name"] == "tract_geoid", "role"] = "model"
    with pytest.raises(FeatureRegistryError, match="only with role='key'"):
        validate_feature_registry(model_key, development_start="2020-05-01")

    extra_key = _registry()
    extra_key.loc[4, "role"] = "key"
    with pytest.raises(FeatureRegistryError, match="Only tract_geoid and target_date"):
        validate_feature_registry(extra_key, development_start="2020-05-01")


@pytest.mark.parametrize(
    ("feature_name", "static"), [("tract_geoid", False), ("target_date", True)]
)
def test_key_static_flags_are_exact(feature_name: str, static: bool) -> None:
    registry = _registry()
    registry.loc[registry["feature_name"].eq(feature_name), "static"] = static
    with pytest.raises(FeatureRegistryError, match="tract_geoid must be static"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_static_model_features_cannot_declare_lag_offsets() -> None:
    registry = _registry()
    registry.loc[3, "source_end_offset_days"] = -1
    with pytest.raises(FeatureRegistryError, match="Static model features"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_exact_known_at_origin_calendar_pair_is_allowed() -> None:
    registry = pd.concat(
        [_registry(), calendar_feature_registry_rows()], ignore_index=True
    )

    validate_feature_registry(registry, development_start="2020-05-01")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("feature_name", "calendar_week_sin"),
        ("family", "weather"),
        ("role", "audit_only"),
        ("units", "fraction"),
        ("source", "A different deterministic calendar"),
        ("static", True),
        ("available_by", "target date"),
        ("source_start_offset_days", -1),
        ("source_end_offset_days", 0),
    ],
)
def test_calendar_exception_metadata_is_frozen(column: str, value: object) -> None:
    calendar = calendar_feature_registry_rows()
    calendar[column] = calendar[column].astype(object)
    calendar.loc[0, column] = value
    registry = pd.concat([_registry(), calendar], ignore_index=True)

    with pytest.raises(FeatureRegistryError, match="calendar.*contract exactly"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_calendar_exception_requires_complete_sin_cos_pair() -> None:
    registry = pd.concat(
        [_registry(), calendar_feature_registry_rows().iloc[[0]]], ignore_index=True
    )

    with pytest.raises(FeatureRegistryError, match="complete sin/cos pair"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_target_day_observation_cannot_claim_known_at_prediction_origin() -> None:
    registry = _registry()
    registry.loc[2, "available_by"] = "prediction origin"
    registry.loc[2, "source_start_offset_days"] = 0
    registry.loc[2, "source_end_offset_days"] = 0

    with pytest.raises(FeatureRegistryError, match="target day -1"):
        validate_feature_registry(registry, development_start="2020-05-01")

@pytest.mark.parametrize("end_offset", [0, 1, np.nan, -1.5, "yesterday"])
def test_dynamic_model_features_require_integer_end_by_day_minus_one(end_offset: object) -> None:
    registry = _registry()
    registry["source_end_offset_days"] = registry["source_end_offset_days"].astype(object)
    registry.loc[2, "source_end_offset_days"] = end_offset

    invalid_number = pd.isna(end_offset) or end_offset in (-1.5, "yesterday")
    message = "finite integer" if invalid_number else "target day -1"
    with pytest.raises(FeatureRegistryError, match=message):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_dynamic_model_feature_rejects_reversed_window() -> None:
    registry = _registry()
    registry.loc[2, "source_start_offset_days"] = -1
    registry.loc[2, "source_end_offset_days"] = -7

    with pytest.raises(FeatureRegistryError, match="source_start_offset_days <="):
        validate_feature_registry(registry, development_start="2020-05-01")


@pytest.mark.parametrize(
    "available_by", ["2020-01-01", "2020-05-01", "2021-01-01", "not-a-date"]
)
def test_static_model_source_must_predate_development(available_by: str) -> None:
    registry = _registry()
    registry.loc[3, "available_by"] = available_by

    with pytest.raises(FeatureRegistryError, match="strictly before.*start year"):
        validate_feature_registry(registry, development_start="2020-05-01")


def test_invalid_development_start_fails_closed() -> None:
    with pytest.raises(FeatureRegistryError, match="development_start must be a valid"):
        validate_feature_registry(_registry(), development_start="not-a-date")


@pytest.mark.parametrize(
    "feature_name",
    [
        "tract_id",
        "centroid_longitude",
        "spatial_block_id",
        "footprint_fraction",
        "n_obs",
        "qa_pixel_flag",
        "landsat_lst_c",
        "thermal_band_mean",
        "hotspot_label",
        "target_date_mean_lst",
    ],
)
def test_forbidden_names_cannot_be_model_features(feature_name: str) -> None:
    registry = _registry()
    registry.loc[2, "feature_name"] = feature_name

    with pytest.raises(FeatureRegistryError, match="Forbidden primary-model"):
        validate_feature_registry(registry, development_start="2020-05-01")


@pytest.mark.parametrize(
    "feature_name",
    ["centroid_longitude", "spatial_block_id", "coverage_fraction", "landsat_lst_c"],
)
def test_forbidden_model_fields_are_allowed_in_non_model_roles(
    feature_name: str,
) -> None:
    registry = _registry()
    registry.loc[4, "feature_name"] = feature_name
    registry.loc[4, "role"] = "audit_only"

    validate_feature_registry(registry, development_start="2020-05-01")
