from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "experiments" / "la_tmax_gradient" / "run.py"
SPEC = importlib.util.spec_from_file_location("la_tmax_gradient_run", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
RUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN
SPEC.loader.exec_module(RUN)


def test_gradient_uses_complete_prediction_support_not_scored_subset() -> None:
    keys = ["city_id", "tract_geoid", "target_date"]
    universe = pd.DataFrame(
        {
            "city_id": ["los_angeles_ca"] * 1096,
            "tract_geoid": [f"{index:011d}" for index in range(1096)],
            "target_date": ["2024-07-01"] * 1096,
            RUN.TMAX: [0.0, 10.0, *([100.0] * 1094)],
        }
    )
    scored = universe.iloc[:2].copy()
    enriched_scored, enriched_universe, metadata = RUN.add_complete_support_gradient(
        scored, universe, keys
    )
    assert enriched_scored[RUN.GRADIENT].tolist() == [-100.0, -90.0]
    assert enriched_universe[RUN.GRADIENT].median() == 0.0
    assert metadata["scored_subset_used_for_centering"] is False
    assert metadata["target_or_qa_used_for_centering"] is False


def test_daymet_audit_proves_calendar_dminus1_but_not_publication_time() -> None:
    predictor_path = (
        ROOT
        / "data"
        / "processed"
        / "multicity"
        / "m3_source_predictor_extension_v1"
        / "los_angeles_ca"
        / "predictors_46.parquet"
    )
    if not predictor_path.exists():
        pytest.skip("requires the gitignored LA source-predictor artifact")
    with RUN.CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    prior = RUN.load_module("la_tmax_test_prior", RUN.PRIOR_RUN_PATH)
    with RUN.PRIOR_CONFIG_PATH.open("rb") as handle:
        prior_config = tomllib.load(handle)
    _, _, universe, _, _ = prior.load_inputs(prior_config)
    result = RUN.audit_daymet_timing(
        ROOT / config["inputs"]["daymet_audit"], universe, list(prior.KEYS)
    )
    assert all(result["checks"].values())
    assert result["publication_before_target_proven"] is False
    assert result["allowed_claim"] == "historical hindcast reconstruction only"


def test_contract_has_exactly_one_new_feature_and_no_search() -> None:
    with RUN.CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    prior = RUN.load_module("la_tmax_contract_prior", RUN.PRIOR_RUN_PATH)
    assert len(prior.ANOMALY_FEATURES) == 23
    assert config["candidate"]["feature_count"] == 24
    assert config["candidate"]["changed_features"] == [RUN.GRADIENT]
    assert config["candidate"]["extra_candidates_or_search"] is False
    assert config["candidate"]["weather_window_search"] is False
    assert config["candidate"]["interaction_search"] is False
    assert config["candidate"]["gradient_scale_search"] is False


def test_candidate_preprocessor_adds_gradient_and_keeps_train_fold_imputer() -> None:
    prior = RUN.load_module("la_tmax_fit_prior", RUN.PRIOR_RUN_PATH)
    rng = np.random.default_rng(42)
    rows = 120
    training = pd.DataFrame(
        {
            "city_id": ["los_angeles_ca"] * rows,
            "tract_geoid": [f"{index:011d}" for index in range(rows)],
            "target_date": [f"2020-06-{1 + index % 10:02d}" for index in range(rows)],
        }
    )
    for field in prior.ANOMALY_FEATURES:
        training[field] = rng.normal(size=rows)
    dynamic = [
        field
        for field in prior.ANOMALY_FEATURES
        if field not in prior.STATIC_FEATURES
    ]
    training.loc[0, dynamic] = np.nan
    training[RUN.GRADIENT] = rng.normal(size=rows)
    target = pd.Series(rng.normal(size=rows))
    base = prior.build_m3_estimators(prior.M3_CANDIDATES[-1])[1]
    model = RUN.fit_candidate(prior, base, training, target)
    preprocess = model.named_steps["preprocess"]
    columns = {name: list(fields) for name, _, fields in preprocess.transformers_}
    assert columns["complete"] == [*prior.STATIC_FEATURES, RUN.GRADIENT]
    assert columns["dynamic"] == dynamic
    assert preprocess.transform(training).shape == (rows, 24)
    imputer = preprocess.named_transformers_["dynamic"]
    assert imputer.named_steps["impute"].statistics_.shape == (5,)
