from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "experiments" / "la_spatial_residual" / "run.py"
SPEC = importlib.util.spec_from_file_location("la_spatial_residual_run", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
RUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN
SPEC.loader.exec_module(RUN)


def test_basis_contract_has_only_five_target_free_terms() -> None:
    support_path = ROOT / "data" / "interim" / "targets" / "primary_tract_manifest.parquet"
    if not support_path.exists():
        pytest.skip("requires the gitignored LA tract-support manifest")
    basis, metadata = RUN.load_spatial_basis()
    assert basis.shape == (1096, 6)
    assert basis.tract_geoid.nunique() == 1096
    assert list(basis.columns[1:]) == RUN.BASIS_COLUMNS
    assert np.isfinite(basis[RUN.BASIS_COLUMNS]).all().all()
    assert np.allclose(basis[RUN.BASIS_COLUMNS].mean(), 0.0, atol=1e-12)
    assert metadata["crs"] == "EPSG:3310"


def test_diagnostic_refuses_one_oof_year() -> None:
    basis = pd.DataFrame(
        {
            "tract_geoid": ["a", "b", "c"],
            "space_x": [-1.0, 0.0, 1.0],
            "space_y": [0.0, 0.0, 0.0],
            "space_x2": [1.0, 0.0, 1.0],
            "space_xy": [0.0, 0.0, 0.0],
            "space_y2": [0.0, 0.0, 0.0],
        }
    )
    residuals = pd.DataFrame(
        {
            "oof_year": [2021, 2021, 2021],
            "target_date": ["2021-01-01", "2021-01-01", "2021-01-01"],
            "tract_geoid": ["a", "b", "c"],
            "signed_residual": [-1.0, 0.0, 1.0],
        }
    )
    config = {
        "diagnostic": {
            "minimum_oof_years": 2,
            "nearest_neighbors": 1,
            "minimum_cross_year_tract_bias_spearman": 0.15,
            "minimum_within_year_neighbor_residual_spearman": 0.10,
        }
    }
    result = RUN.diagnose_residuals(residuals, basis, config)
    assert result["supported"] is False
    assert result["median_cross_year_spearman"] is None


def test_candidate_noop_exactly_matches_current_without_supported_diagnostic() -> None:
    prior = RUN.load_module("la_local_accuracy_test_prior", RUN.PRIOR_RUN_PATH)
    keys = pd.DataFrame(
        {
            "city_id": ["los_angeles_ca", "los_angeles_ca"],
            "tract_geoid": ["a", "b"],
            "target_date": ["2022-01-01", "2022-01-01"],
        }
    )
    base = keys.copy()
    base["spatial_block"] = ["one", "two"]
    base["observed"] = [9.0, 11.0]
    base["observed_relative"] = [-1.0, 1.0]
    base[f"{RUN.COMPARATOR}_relative_support"] = [-0.5, 0.5]
    base[f"{RUN.COMPARATOR}_relative_scored"] = [-0.5, 0.5]
    base[f"{RUN.COMPARATOR}_absolute"] = [9.5, 10.5]
    basis = pd.DataFrame({"tract_geoid": ["a", "b"]})
    for column in RUN.BASIS_COLUMNS:
        basis[column] = [0.0, 0.0]
    prediction = keys.copy()
    prediction[f"{RUN.COMPARATOR}_relative_support"] = [-0.5, 0.5]
    prediction[f"{RUN.COMPARATOR}_absolute"] = [9.5, 10.5]
    result = RUN.add_candidate(prior, base, prediction, keys, basis, None)
    assert np.array_equal(
        result[f"{RUN.CANDIDATE}_relative_support"],
        result[f"{RUN.COMPARATOR}_relative_support"],
    )
    assert np.array_equal(
        result[f"{RUN.CANDIDATE}_absolute"],
        result[f"{RUN.COMPARATOR}_absolute"],
    )
