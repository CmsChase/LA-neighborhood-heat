from __future__ import annotations

import math
import tomllib
from pathlib import Path
from statistics import NormalDist

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs" / "la_future_date_development_confirmation_v1.toml"


def load_protocol() -> dict:
    with PROTOCOL.open("rb") as handle:
        return tomllib.load(handle)


def test_protocol_does_not_authorize_collection_target_read_or_training() -> None:
    protocol = load_protocol()
    assert protocol["protocol"]["state"] == "complete_draft_collection_not_authorized"
    assert protocol["protocol"]["new_data_download_authorized"] is False
    assert protocol["protocol"]["model_training_authorized"] is False
    assert protocol["protocol"]["target_read_authorized"] is False
    assert protocol["historical_roles"]["la_2025_remains_frozen"] is True
    assert protocol["acquisition_frame"]["performance_dependent_stopping_forbidden"]


def test_central_sample_size_is_reproducible_from_locked_assumptions() -> None:
    evidence = load_protocol()["sample_size_evidence"]
    z_sum = NormalDist().inv_cdf(1 - evidence["alpha_two_sided"] / 2) + NormalDist().inv_cdf(
        evidence["power"]
    )
    design_effect = (1 + evidence["planning_adjacent_date_correlation"]) / (
        1 - evidence["planning_adjacent_date_correlation"]
    )
    required = math.ceil(
        (z_sum * evidence["planning_daily_gain_sd_c"] / evidence["five_percent_effect_c"])
        ** 2
        * design_effect
    )
    assert required == evidence["planning_required_confirmation_dates"] == 106


def test_measurement_support_pairing_denominator_gap_is_exactly_three_dates() -> None:
    path = ROOT / "exports" / "LA_SPATIAL_RESIDUAL" / "forward_oof.parquet"
    if not path.exists():
        pytest.skip("requires the gitignored LA spatial-residual evidence artifact")
    rows = pd.read_parquet(path, columns=["target_date", "tract_geoid"])
    rows["target_date"] = pd.to_datetime(rows.target_date).dt.strftime("%Y-%m-%d")
    expected = {
        "2022-09-03": 1085,
        "2023-05-17": 787,
        "2023-10-24": 699,
    }
    observed = rows.loc[rows.target_date.isin(expected)].groupby("target_date").size()
    assert observed.to_dict() == expected
    assert len(rows) - int(observed.sum()) == 44571
    assert int(observed.sum()) == 2571
