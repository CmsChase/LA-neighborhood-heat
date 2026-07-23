import pandas as pd
import pytest

from la_heat.guardrails import (
    validate_disjoint_groups,
    validate_lag_windows,
    validate_no_final_year_rows,
    validate_primary_feature_names,
    validate_static_eligible_denominator,
    validate_target_qa_contract,
    validate_unique_primary_key,
)


def test_rejects_target_thermal_and_identifier_features() -> None:
    with pytest.raises(ValueError, match="Forbidden"):
        validate_primary_feature_names(["daymet_tmax", "ST_B10", "GEOID"])


def test_accepts_prespecified_physical_features() -> None:
    validate_primary_feature_names(["daymet_tmax", "nlcd_impervious", "sentinel_ndvi_lag60"])


def test_rejects_target_quality_metadata_as_predictor() -> None:
    with pytest.raises(ValueError, match="Forbidden"):
        validate_primary_feature_names(["median_st_uncertainty_k"])


def test_lag_window_must_end_before_target() -> None:
    valid = pd.DataFrame(
        {"target_date": ["2024-08-10"], "feature_window_end": ["2024-08-09"]}
    )
    validate_lag_windows(valid)

    invalid = pd.DataFrame(
        {"target_date": ["2024-08-10"], "feature_window_end": ["2024-08-10"]}
    )
    with pytest.raises(ValueError, match="temporal leakage"):
        validate_lag_windows(invalid)


def test_joint_split_rejects_shared_dates_or_blocks() -> None:
    train = pd.DataFrame({"date_group": ["2024-07-01"], "spatial_block": ["A"]})
    test = pd.DataFrame({"date_group": ["2025-07-01"], "spatial_block": ["A"]})
    with pytest.raises(ValueError, match="spatial_block"):
        validate_disjoint_groups(
            train, test, group_columns=["date_group", "spatial_block"]
        )


def test_development_frame_rejects_final_year() -> None:
    frame = pd.DataFrame({"target_date": ["2024-07-01", "2025-07-01"]})
    with pytest.raises(PermissionError, match="2025"):
        validate_no_final_year_rows(frame, final_year=2025)


def test_development_frame_rejects_years_after_final_year() -> None:
    frame = pd.DataFrame({"target_date": ["2024-07-01", "2026-07-01"]})
    with pytest.raises(PermissionError, match="2025 or later"):
        validate_no_final_year_rows(frame, final_year=2025)


def test_primary_key_must_be_unique() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["06037000100", "06037000100"],
            "target_date": ["2024-07-01", "2024-07-01"],
        }
    )
    with pytest.raises(ValueError, match="Duplicate primary keys"):
        validate_unique_primary_key(frame)


def test_target_availability_must_match_qa_gate() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["06037000100", "06037000200"],
            "target_date": ["2024-07-01", "2024-07-01"],
            "footprint_fraction": [1.0, 1.0],
            "valid_fraction": [0.80, 0.40],
            "valid_pixel_count": [100, 100],
            "target_lst_c": [35.0, float("nan")],
        }
    )
    validate_target_qa_contract(
        frame,
        minimum_footprint_fraction=0.90,
        minimum_valid_fraction=0.60,
        minimum_valid_pixels=20,
    )
    frame.loc[1, "target_lst_c"] = 34.0
    with pytest.raises(ValueError, match="Target QA contract"):
        validate_target_qa_contract(
            frame,
            minimum_footprint_fraction=0.90,
            minimum_valid_fraction=0.60,
            minimum_valid_pixels=20,
        )


def test_static_eligible_denominator_cannot_change_across_dates() -> None:
    frame = pd.DataFrame(
        {
            "tract_geoid": ["a", "a", "b", "b"],
            "target_date": ["2024-06-01", "2024-07-01"] * 2,
            "eligible_pixel_count_static": [100, 101, 80, 80],
            "eligible_pixel_identity_sha256": ["x", "y", "z", "z"],
        }
    )
    with pytest.raises(ValueError, match="denominator changed"):
        validate_static_eligible_denominator(frame)
