"""Frozen zero-shot transfer models and target-blind external prediction core."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from la_heat.modeling import CompleteFeatureValidator, ObservedDynamicMedianImputer
from la_heat.multicity.predictor_readiness import validate_predictor_frame
from la_heat.provenance import canonical_sha256
from la_heat.training_contract import date_balanced_sample_weights

CONTRACT_PATH: Final = Path(
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT.json"
)
KEY_COLUMNS: Final = ("city_id", "tract_geoid", "target_date")
TRAINING_CITY: Final = "los_angeles_ca"
TRAINING_YEARS: Final = frozenset({2020, 2021, 2022, 2023})
CALIBRATION_YEAR: Final = 2024
EXTERNAL_YEAR: Final = 2025
EXTERNAL_CITIES: Final = frozenset({"phoenix_az", "houston_tx", "chicago_il"})


class TransferModelError(RuntimeError):
    """Raised when a cohort or model violates the frozen transfer contract."""


@dataclass(frozen=True, slots=True)
class FrozenEstimatorSet:
    b1: Pipeline
    m2: Pipeline
    lower: Pipeline
    upper: Pipeline
    b1_feature_order: tuple[str, ...]
    m2_feature_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FittedTransferModels:
    b1: Pipeline
    m2: Pipeline
    lower: Pipeline
    upper: Pipeline
    b1_feature_order: tuple[str, ...]
    m2_feature_order: tuple[str, ...]
    training_row_count: int
    training_date_count: int


@dataclass(frozen=True, slots=True)
class ConformalCalibration:
    nonconformity_quantile_c: float
    abstention_width_threshold_c: float
    nominal_coverage: float
    calibration_row_count: int
    calibration_date_count: int


def load_frozen_transfer_contract(project_root: str | Path) -> dict[str, Any]:
    """Load the committed predictor/model contract without reading any data values."""

    path = Path(project_root).resolve() / CONTRACT_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransferModelError(f"Cannot read frozen transfer contract: {path}") from error
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise TransferModelError("Frozen transfer contract commit is invalid.")
    locks = payload.get("semantic_locks")
    if (
        payload.get("state") != "complete_portable_predictor_contract_locked"
        or not isinstance(locks, dict)
        or locks.get("contract_sha256") != canonical_sha256(payload.get("contract"))
        or locks.get("feature_records_sha256")
        != canonical_sha256(payload.get("feature_registry", {}).get("features"))
        or locks.get("model_contract_sha256")
        != canonical_sha256(payload.get("model_contract"))
    ):
        raise TransferModelError("Frozen transfer semantic locks changed.")
    if payload.get("model_roles") != {
        "primary": "m2_transfer",
        "diagnostic_baseline": "b1_transfer",
        "b1_deployment_candidate": False,
        "reason": payload.get("model_roles", {}).get("reason"),
    }:
        raise TransferModelError("Frozen model roles changed.")
    return payload


def _feature_groups(
    contract: dict[str, Any],
    feature_order: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    registry = contract.get("feature_registry", {})
    rows = registry.get("features") if isinstance(registry, dict) else None
    if not isinstance(rows, list):
        raise TransferModelError("Frozen feature registry is missing.")
    by_name = {
        str(row.get("feature_name")): row for row in rows if isinstance(row, dict)
    }
    if set(feature_order) - set(by_name):
        raise TransferModelError("Model feature order is absent from the registry.")
    complete = tuple(
        name
        for name in feature_order
        if by_name[name].get("static") is True or by_name[name].get("family") == "calendar"
    )
    dynamic = tuple(name for name in feature_order if name not in set(complete))
    if not complete or not dynamic:
        raise TransferModelError("Frozen complete/dynamic feature groups are empty.")
    return complete, dynamic


def _preprocessor(
    complete: tuple[str, ...],
    dynamic: tuple[str, ...],
    *,
    scale: bool,
) -> ColumnTransformer:
    complete_steps: list[tuple[str, Any]] = [
        ("validate", CompleteFeatureValidator())
    ]
    dynamic_steps: list[tuple[str, Any]] = [
        ("impute", ObservedDynamicMedianImputer())
    ]
    if scale:
        complete_steps.append(("scale", StandardScaler()))
        dynamic_steps.append(("scale", StandardScaler()))
    return ColumnTransformer(
        [
            ("complete", Pipeline(complete_steps), list(complete)),
            ("dynamic", Pipeline(dynamic_steps), list(dynamic)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def _hgb(
    parameters: dict[str, Any],
    *,
    loss: str,
    quantile: float | None = None,
) -> HistGradientBoostingRegressor:
    kwargs: dict[str, Any] = {
        "loss": loss,
        "learning_rate": float(parameters["learning_rate"]),
        "max_iter": int(parameters["max_iter"]),
        "max_leaf_nodes": int(parameters["max_leaf_nodes"]),
        "min_samples_leaf": int(parameters["min_samples_leaf"]),
        "l2_regularization": float(parameters["l2_regularization"]),
        "early_stopping": bool(parameters["early_stopping"]),
        "random_state": int(parameters["random_state"]),
    }
    if quantile is not None:
        kwargs["quantile"] = quantile
    return HistGradientBoostingRegressor(**kwargs)


def build_frozen_transfer_estimators(
    contract: dict[str, Any],
) -> FrozenEstimatorSet:
    """Construct unfitted B1, point M2, and lower/upper M2 estimators."""

    model_contract = contract.get("model_contract")
    registry = contract.get("feature_registry")
    if not isinstance(model_contract, dict) or not isinstance(registry, dict):
        raise TransferModelError("Frozen model contract is incomplete.")
    b1_order = tuple(str(value) for value in model_contract.get("b1_feature_order", []))
    m2_order = tuple(str(value) for value in model_contract.get("m2_feature_order", []))
    if (
        len(b1_order) != 23
        or len(m2_order) != 46
        or list(m2_order) != registry.get("feature_order")
    ):
        raise TransferModelError("Frozen B1/M2 feature order changed.")
    b1_complete, b1_dynamic = _feature_groups(contract, b1_order)
    m2_complete, m2_dynamic = _feature_groups(contract, m2_order)
    b1_parameters = model_contract.get("b1_transfer")
    m2_parameters = model_contract.get("m2_transfer")
    uncertainty = model_contract.get("uncertainty")
    if not all(isinstance(value, dict) for value in (b1_parameters, m2_parameters, uncertainty)):
        raise TransferModelError("Frozen estimator parameters are incomplete.")
    if (
        b1_parameters.get("estimator") != "Ridge"
        or b1_parameters.get("alpha") != 10.0
        or m2_parameters.get("estimator") != "HistGradientBoostingRegressor"
        or m2_parameters.get("loss") != "absolute_error"
    ):
        raise TransferModelError("Frozen estimator identity changed.")

    b1 = Pipeline(
        [
            ("preprocess", _preprocessor(b1_complete, b1_dynamic, scale=True)),
            (
                "model",
                Ridge(
                    alpha=10.0,
                    fit_intercept=True,
                    solver="lsqr",
                    tol=0.0001,
                ),
            ),
        ]
    )
    point = Pipeline(
        [
            ("preprocess", _preprocessor(m2_complete, m2_dynamic, scale=False)),
            ("model", _hgb(m2_parameters, loss="absolute_error")),
        ]
    )
    lower_quantile = float(uncertainty["lower_quantile"])
    upper_quantile = float(uncertainty["upper_quantile"])
    lower = Pipeline(
        [
            ("preprocess", _preprocessor(m2_complete, m2_dynamic, scale=False)),
            (
                "model",
                _hgb(m2_parameters, loss="quantile", quantile=lower_quantile),
            ),
        ]
    )
    upper = Pipeline(
        [
            ("preprocess", _preprocessor(m2_complete, m2_dynamic, scale=False)),
            (
                "model",
                _hgb(m2_parameters, loss="quantile", quantile=upper_quantile),
            ),
        ]
    )
    return FrozenEstimatorSet(b1, point, lower, upper, b1_order, m2_order)


def _cohort(
    frame: pd.DataFrame,
    *,
    feature_order: tuple[str, ...],
    city_rule: str,
    years: frozenset[int],
) -> pd.Series:
    validate_predictor_frame(
        frame,
        key_columns=list(KEY_COLUMNS),
        feature_order=list(feature_order),
    )
    city = frame["city_id"].astype(str)
    dates = pd.to_datetime(frame["target_date"], errors="raise")
    observed_years = frozenset(int(value) for value in dates.dt.year.unique())
    city_ok = (
        city.eq(TRAINING_CITY).all()
        if city_rule == "source"
        else frozenset(city.unique()) == EXTERNAL_CITIES
    )
    if not city_ok or observed_years != years:
        raise TransferModelError(
            f"Cohort does not match city/year contract: cities={sorted(city.unique())}, "
            f"years={sorted(observed_years)}"
        )
    return dates


def _target(target: pd.Series, frame: pd.DataFrame) -> pd.Series:
    if not isinstance(target, pd.Series) or not target.index.equals(frame.index):
        raise TransferModelError("Target must be a Series aligned to predictor rows.")
    numeric = pd.to_numeric(target, errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise TransferModelError("Target values must be finite and complete.")
    return numeric


def fit_frozen_transfer_models(
    training_predictors: pd.DataFrame,
    training_target: pd.Series,
    contract: dict[str, Any],
) -> FittedTransferModels:
    """Fit only on Los Angeles 2020-2023 rows supplied by the caller."""

    estimators = build_frozen_transfer_estimators(contract)
    dates = _cohort(
        training_predictors,
        feature_order=estimators.m2_feature_order,
        city_rule="source",
        years=TRAINING_YEARS,
    )
    target = _target(training_target, training_predictors)
    keys = training_predictors.loc[:, ["tract_geoid", "target_date"]]
    weights = date_balanced_sample_weights(keys).to_numpy(dtype=float)
    estimators.b1.fit(
        training_predictors.loc[:, estimators.b1_feature_order],
        target,
        model__sample_weight=weights,
    )
    m2_frame = training_predictors.loc[:, estimators.m2_feature_order]
    for model in (estimators.m2, estimators.lower, estimators.upper):
        model.fit(m2_frame, target, model__sample_weight=weights)
    return FittedTransferModels(
        estimators.b1,
        estimators.m2,
        estimators.lower,
        estimators.upper,
        estimators.b1_feature_order,
        estimators.m2_feature_order,
        len(training_predictors),
        int(dates.nunique()),
    )


def _predictions(models: FittedTransferModels, frame: pd.DataFrame) -> tuple[np.ndarray, ...]:
    b1 = np.asarray(models.b1.predict(frame.loc[:, models.b1_feature_order]), dtype=float)
    m2_frame = frame.loc[:, models.m2_feature_order]
    point = np.asarray(models.m2.predict(m2_frame), dtype=float)
    lower = np.asarray(models.lower.predict(m2_frame), dtype=float)
    upper = np.asarray(models.upper.predict(m2_frame), dtype=float)
    if not np.isfinite(np.column_stack([b1, point, lower, upper])).all():
        raise TransferModelError("A frozen transfer model produced non-finite predictions.")
    return b1, point, lower, upper


def weighted_contract_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    dates: pd.Series,
    geoids: pd.Series,
    probability: float,
) -> float:
    """Contract quantile: value, date, GEOID order then first cumulative hit."""

    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if (
        values.ndim != 1
        or weights.shape != values.shape
        or len(values) != len(dates)
        or len(values) != len(geoids)
        or not np.isfinite(values).all()
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
        or not 0 < probability < 1
    ):
        raise TransferModelError("Invalid weighted contract quantile inputs.")
    ordered = pd.DataFrame(
        {
            "value": values,
            "weight": weights,
            "target_date": pd.to_datetime(dates).to_numpy(),
            "tract_geoid": geoids.astype(str).to_numpy(),
        }
    ).sort_values(["value", "target_date", "tract_geoid"], kind="stable")
    threshold = probability * float(ordered["weight"].sum())
    index = int(np.searchsorted(ordered["weight"].cumsum().to_numpy(), threshold, side="left"))
    return float(ordered["value"].iloc[min(index, len(ordered) - 1)])


def calibrate_frozen_intervals(
    models: FittedTransferModels,
    calibration_predictors: pd.DataFrame,
    calibration_target: pd.Series,
    contract: dict[str, Any],
) -> ConformalCalibration:
    """Calibrate M2 intervals only on supplied Los Angeles 2024 rows."""

    dates = _cohort(
        calibration_predictors,
        feature_order=models.m2_feature_order,
        city_rule="source",
        years=frozenset({CALIBRATION_YEAR}),
    )
    target = _target(calibration_target, calibration_predictors).to_numpy(dtype=float)
    _, _, lower, upper = _predictions(models, calibration_predictors)
    scores = np.maximum.reduce([lower - target, target - upper, np.zeros(len(target))])
    weights = date_balanced_sample_weights(
        calibration_predictors.loc[:, ["tract_geoid", "target_date"]]
    ).to_numpy(dtype=float)
    uncertainty = contract["model_contract"]["uncertainty"]
    correction = weighted_contract_quantile(
        scores,
        weights,
        calibration_predictors["target_date"],
        calibration_predictors["tract_geoid"],
        float(uncertainty["conformal_probability"]),
    )
    widths = (upper + correction) - (lower - correction)
    if not np.isfinite(widths).all() or np.any(widths < 0):
        raise TransferModelError("Corrected quantile intervals are invalid.")
    abstention = weighted_contract_quantile(
        widths,
        weights,
        calibration_predictors["target_date"],
        calibration_predictors["tract_geoid"],
        float(uncertainty["abstention_width_quantile"]),
    )
    return ConformalCalibration(
        correction,
        abstention,
        float(uncertainty["nominal_coverage"]),
        len(calibration_predictors),
        int(dates.nunique()),
    )


def predict_external_cities(
    models: FittedTransferModels,
    calibration: ConformalCalibration,
    external_predictors: pd.DataFrame,
) -> pd.DataFrame:
    """Create target-blind 2025 external predictions, intervals, and abstention flags."""

    _cohort(
        external_predictors,
        feature_order=models.m2_feature_order,
        city_rule="external",
        years=frozenset({EXTERNAL_YEAR}),
    )
    b1, point, lower_raw, upper_raw = _predictions(models, external_predictors)
    lower = lower_raw - calibration.nonconformity_quantile_c
    upper = upper_raw + calibration.nonconformity_quantile_c
    width = upper - lower
    if not np.isfinite(width).all() or np.any(width < 0):
        raise TransferModelError("External corrected intervals are invalid.")
    result = external_predictors.loc[:, KEY_COLUMNS].copy()
    result["b1_prediction_c"] = b1
    result["m2_prediction_c"] = point
    result["m2_lower_c"] = lower
    result["m2_upper_c"] = upper
    result["m2_interval_width_c"] = width
    result["m2_abstain"] = width > calibration.abstention_width_threshold_c
    result["m2_accepted"] = ~result["m2_abstain"]
    return result
