from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from affine import Affine

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "experiments" / "la_measurement_support" / "run.py"
SPEC = importlib.util.spec_from_file_location("la_measurement_support_run", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
RUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN
SPEC.loader.exec_module(RUN)


def test_contract_locks_one_shot_read_only_scope_and_thresholds() -> None:
    with RUN.CONTRACT.open("rb") as handle:
        contract = tomllib.load(handle)
    assert contract["experiment"]["new_data_allowed"] is False
    assert contract["experiment"]["model_training_allowed"] is False
    assert contract["experiment"]["model_selection_allowed"] is False
    assert contract["experiment"]["la_2025_allowed"] is False
    assert contract["decision"]["no_rule_changes_after_results"] is True
    assert contract["decision"]["worth_prioritizing_if_primary_at_least_celsius"] == 0.20
    assert contract["decision"]["and_at_least_two_years_at_least_celsius"] == 0.15


def test_completed_result_authenticates_original_pixel_reconstruction() -> None:
    path = ROOT / "exports" / "LA_MEASUREMENT_SUPPORT_DIAGNOSTIC" / "summary.json"
    if not path.exists():
        pytest.skip("requires the gitignored LA measurement-support result")
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["state"] == "complete"
    assert summary["inventory"][
        "maximum_original_target_reconstruction_abs_difference_c"
    ] == 0.0
    assert summary["decision"]["measurement_support_worth_prioritizing"] is False


def test_pairing_is_chronological_nonoverlapping_and_year_bounded() -> None:
    dates = pd.to_datetime(
        [
            "2022-05-01",
            "2022-05-09",
            "2022-06-01",
            "2022-06-25",
            "2022-07-01",
            "2023-05-01",
            "2023-05-09",
        ]
    ).tolist()
    assert RUN.build_pairs(dates, 16) == [
        ("2022-05-01", "2022-05-09"),
        ("2022-06-25", "2022-07-01"),
        ("2023-05-01", "2023-05-09"),
    ]


def test_common_support_uses_exact_same_pixel_locations_for_both_dates() -> None:
    zones = np.array([[1, 1, 2], [1, 2, 2]])
    first_valid = np.array([[True, True, True], [False, True, True]])
    second_valid = np.array([[True, False, True], [True, True, False]])
    common = first_valid & second_valid
    counts, x, y = RUN.centroid_by_zone(
        common, zones, Affine.translation(100, 200) * Affine.scale(30, -30), 2
    )
    assert common.tolist() == [
        [True, False, True],
        [False, True, False],
    ]
    assert counts.tolist() == [1, 2]
    assert np.allclose(x, [115.0, 160.0])
    assert np.allclose(y, [185.0, 170.0])


def test_zonal_median_and_centering_are_deterministic() -> None:
    values = np.array([[1.0, 3.0, 10.0], [5.0, 12.0, 14.0]])
    zones = np.array([[1, 1, 2], [1, 2, 2]])
    mask = np.ones_like(zones, dtype=bool)
    assert RUN.zonal_median(values, zones, mask, 2).tolist() == [3.0, 12.0]
    assert RUN.centered(pd.Series([3.0, 12.0])).tolist() == [-4.5, 4.5]
