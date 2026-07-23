import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone

from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.modeling import (
    EXPECTED_MODEL_FEATURE_COUNTS,
    ModelingContractError,
    fit_fold_model,
    make_model_spec,
    model_matrix,
    predict_fold_model,
)


def _row(name, family, *, static, start=None, end=None):
    return {
        "feature_name": name,
        "family": family,
        "role": "model",
        "units": "unitless",
        "source": "synthetic legal source",
        "static": static,
        "available_by": "2019-01-01" if static else "historical archive",
        "source_start_offset_days": start,
        "source_end_offset_days": end,
    }


def _registry() -> pd.DataFrame:
    rows = [
        {
            "feature_name": "tract_geoid",
            "family": "key",
            "role": "key",
            "units": "identifier",
            "source": "tract manifest",
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
            "source": "join key",
            "static": False,
            "available_by": "prediction origin",
            "source_start_offset_days": None,
            "source_end_offset_days": None,
        },
    ]
    for index in range(9):
        rows.append(_row(f"land_feature_{index:02d}", "land_use", static=True))
    for index in range(9):
        rows.append(_row(f"geo_feature_{index:02d}", "geography", static=True))
    rows.extend(calendar_feature_registry_rows().to_dict("records"))
    for index in range(21):
        rows.append(
            _row(
                f"weather_feature_{index:02d}",
                "weather",
                static=False,
                start=-7,
                end=-1,
            )
        )
    for index in range(5):
        rows.append(
            _row(
                f"satellite_feature_{index:02d}",
                "satellite",
                static=False,
                start=-60,
                end=-1,
            )
        )
    rows.append(
        {
            **_row("coverage_audit", "audit", static=False),
            "role": "audit_only",
        }
    )
    return pd.DataFrame(rows)


def _training_data(registry: pd.DataFrame):
    dates = pd.to_datetime(
        ["2022-05-01"] * 3 + ["2022-07-01"] * 4 + ["2023-05-01"] * 3 + ["2023-09-01"] * 5
    )
    keys = pd.DataFrame(
        {
            "tract_geoid": [f"g{index:02d}" for index in range(len(dates))],
            "target_date": dates,
        }
    )
    model_names = registry.loc[registry["role"].eq("model"), "feature_name"].tolist()
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(rng.normal(size=(len(keys), len(model_names))), columns=model_names)
    phase = 2 * np.pi * (dates.dayofyear.to_numpy() - 1) / np.where(dates.is_leap_year, 366, 365)
    frame["calendar_doy_sin"] = np.sin(phase)
    frame["calendar_doy_cos"] = np.cos(phase)
    frame.loc[0, "weather_feature_00"] = np.nan
    frame.loc[1, "satellite_feature_00"] = np.nan
    target = pd.Series(25 + 3 * np.sin(phase) + rng.normal(scale=0.2, size=len(keys)))
    frame.index = keys.index
    target.index = keys.index
    return frame, target, keys


def test_specs_select_exact_registry_families_and_feature_counts() -> None:
    registry = _registry()
    specs = {
        model_id: make_model_spec(registry, model_id) for model_id in EXPECTED_MODEL_FEATURE_COUNTS
    }

    observed_counts = {key: len(spec.feature_names) for key, spec in specs.items()}
    assert observed_counts == EXPECTED_MODEL_FEATURE_COUNTS
    assert specs["B0"].feature_names == ("calendar_doy_sin", "calendar_doy_cos")
    assert specs["B0"].fit_contract == "one_equal_weight_training_date_mean_per_row"
    assert set(specs["B1"].imputed_feature_names) == {
        f"weather_feature_{index:02d}" for index in range(21)
    }
    assert specs["B2"].imputed_feature_names == ()
    assert "coverage_audit" not in specs["M1"].feature_names


def test_m2_feature_family_ablation_preserves_train_only_dynamic_imputation() -> None:
    registry = _registry()
    spec = make_model_spec(
        registry,
        "M2",
        feature_families=frozenset({"calendar", "weather"}),
        hgb_max_iter=20,
        hgb_min_samples_leaf=2,
    )

    assert len(spec.feature_names) == 23
    assert set(spec.imputed_feature_names) == {
        f"weather_feature_{index:02d}" for index in range(21)
    }
    assert not any("land_feature" in name or "geo_feature" in name for name in spec.feature_names)


def test_feature_family_restriction_is_m2_only_and_cannot_be_empty() -> None:
    registry = _registry()
    with pytest.raises(ModelingContractError, match="only defined for the M2"):
        make_model_spec(registry, "B1", feature_families=frozenset({"calendar"}))
    with pytest.raises(ModelingContractError, match="restriction is invalid"):
        make_model_spec(registry, "M2", feature_families=frozenset())


def test_factories_are_unfitted_cloneable_and_hgb_has_no_random_holdout() -> None:
    registry = _registry()
    m2 = make_model_spec(registry, "M2")
    copied = clone(m2.pipeline)

    assert copied is not m2.pipeline
    assert not hasattr(m2.pipeline.named_steps["preprocess"], "transformers_")
    model = m2.pipeline.named_steps["model"]
    assert model.early_stopping is False
    assert model.loss == "absolute_error"
    assert model.random_state == 20260719


@pytest.mark.parametrize("model_id", ["B0", "B1", "B2", "M1", "M2"])
def test_fold_fit_is_deterministic_and_predicts_finite(model_id: str) -> None:
    registry = _registry()
    frame, target, keys = _training_data(registry)
    spec = make_model_spec(
        registry,
        model_id,
        hgb_max_iter=20,
        hgb_min_samples_leaf=2,
    )

    first = fit_fold_model(spec, frame, target, keys)
    second = fit_fold_model(spec, frame, target, keys)
    first_predictions = predict_fold_model(first, frame)
    second_predictions = predict_fold_model(second, frame)

    np.testing.assert_allclose(first_predictions, second_predictions)
    assert np.isfinite(first_predictions).all()
    assert first.training_date_count == 4


def test_train_fitted_imputer_ignores_unpassed_holdout_extremes() -> None:
    registry = _registry()
    frame, target, keys = _training_data(registry)
    train = frame.index[:10]
    holdout = frame.index[10:]
    spec = make_model_spec(registry, "B1")

    fitted = fit_fold_model(spec, frame.loc[train], target.loc[train], keys.loc[train])
    before = predict_fold_model(fitted, frame.loc[holdout])
    changed = frame.loc[holdout].copy()
    changed.loc[:, list(spec.imputed_feature_names)] = 1e9
    after = predict_fold_model(fitted, changed)

    assert not np.allclose(before, after)
    dynamic = fitted.pipeline.named_steps["preprocess"].named_transformers_["dynamic"]
    learned = dynamic.named_steps["impute"].statistics_.copy()
    refit = fit_fold_model(spec, frame.loc[train], target.loc[train], keys.loc[train])
    learned_again = (
        refit.pipeline.named_steps["preprocess"]
        .named_transformers_["dynamic"]
        .named_steps["impute"]
        .statistics_
    )
    np.testing.assert_allclose(learned, learned_again)


def test_missing_complete_or_all_missing_dynamic_feature_fails_closed() -> None:
    registry = _registry()
    frame, target, keys = _training_data(registry)
    b2 = make_model_spec(registry, "B2")
    missing_static = frame.copy()
    missing_static.loc[0, b2.complete_feature_names[0]] = np.nan
    with pytest.raises(ModelingContractError, match="no missing"):
        fit_fold_model(b2, missing_static, target, keys)

    b1 = make_model_spec(registry, "B1")
    all_missing = frame.copy()
    all_missing.loc[:, b1.imputed_feature_names[0]] = np.nan
    with pytest.raises(ModelingContractError, match="entirely missing"):
        fit_fold_model(b1, all_missing, target, keys)


def test_model_matrix_excludes_keys_targets_and_audit_columns() -> None:
    registry = _registry()
    frame, _, keys = _training_data(registry)
    frame["tract_geoid"] = keys["tract_geoid"]
    frame["target_date"] = keys["target_date"]
    frame["target_lst_c"] = 999.0
    frame["coverage_audit"] = 1.0
    spec = make_model_spec(registry, "M1")

    selected = model_matrix(frame, spec)

    assert selected.columns.tolist() == list(spec.feature_names)
    assert not {"tract_geoid", "target_date", "target_lst_c", "coverage_audit"} & set(selected)


def test_invalid_model_id_feature_count_and_parameters_fail() -> None:
    registry = _registry()
    with pytest.raises(ModelingContractError, match="Unknown model_id"):
        make_model_spec(registry, "unknown")
    incomplete = registry.loc[~registry["feature_name"].eq("weather_feature_00")]
    with pytest.raises(ModelingContractError, match="requires exactly 23"):
        make_model_spec(incomplete, "B1")
    with pytest.raises(ValueError, match="positive and finite"):
        make_model_spec(registry, "B1", ridge_alpha=0)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        make_model_spec(registry, "M1", elastic_l1_ratio=2)
