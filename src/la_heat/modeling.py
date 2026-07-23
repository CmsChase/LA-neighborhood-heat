"""Registry-driven, fold-local model factories for the predeclared analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LinearRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from la_heat.feature_registry import (
    CALENDAR_MODEL_FEATURE_NAMES,
    validate_feature_registry,
)
from la_heat.training_contract import (
    date_balanced_sample_weights,
    prepare_b0_date_mean_training,
)

MODEL_IDS = ("B0", "B1", "B2", "M1", "M2")
EXPECTED_MODEL_FEATURE_COUNTS = {
    "B0": 2,
    "B1": 23,
    "B2": 20,
    "M1": 46,
    "M2": 46,
}
MODEL_FAMILIES = {
    "B0": frozenset({"calendar"}),
    "B1": frozenset({"calendar", "weather"}),
    "B2": frozenset({"calendar", "land_use", "geography"}),
    "M1": None,
    "M2": None,
}


class ModelingContractError(ValueError):
    """Raised when a model or training matrix violates the frozen contract."""


class CompleteFeatureValidator(TransformerMixin, BaseEstimator):
    """Require static/calendar values to be numeric, finite, and complete."""

    def fit(self, X: Any, y: Any = None) -> CompleteFeatureValidator:
        self._validated_array(X)
        self.n_features_in_ = np.asarray(X).shape[1]
        return self

    def transform(self, X: Any) -> np.ndarray:
        values = self._validated_array(X)
        if values.shape[1] != self.n_features_in_:
            raise ModelingContractError("Complete feature width changed after fit.")
        return values

    @staticmethod
    def _validated_array(X: Any) -> np.ndarray:
        try:
            values = np.asarray(X, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ModelingContractError(
                "Static and calendar features must be numeric and complete."
            ) from exc
        if values.ndim != 2 or not np.isfinite(values).all():
            raise ModelingContractError(
                "Static and calendar features must be finite with no missing values."
            )
        return values


class ObservedDynamicMedianImputer(TransformerMixin, BaseEstimator):
    """Fit train-only medians while rejecting infinite or all-missing columns."""

    def fit(self, X: Any, y: Any = None) -> ObservedDynamicMedianImputer:
        values = self._validated_array(X)
        if np.isnan(values).all(axis=0).any():
            raise ModelingContractError(
                "An observed dynamic feature is entirely missing in the training fold."
            )
        self.imputer_ = SimpleImputer(
            strategy="median",
            add_indicator=False,
            keep_empty_features=False,
        ).fit(values)
        self.n_features_in_ = values.shape[1]
        return self

    def transform(self, X: Any) -> np.ndarray:
        values = self._validated_array(X)
        if values.shape[1] != self.n_features_in_:
            raise ModelingContractError("Dynamic feature width changed after fit.")
        transformed = self.imputer_.transform(values)
        if transformed.shape[1] != self.n_features_in_:
            raise ModelingContractError("Dynamic imputation silently dropped a feature.")
        return transformed

    @property
    def statistics_(self) -> np.ndarray:
        return self.imputer_.statistics_

    @staticmethod
    def _validated_array(X: Any) -> np.ndarray:
        try:
            values = np.asarray(X, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ModelingContractError(
                "Observed dynamic features must be numeric or missing."
            ) from exc
        if values.ndim != 2 or np.isinf(values).any():
            raise ModelingContractError(
                "Observed dynamic features may be finite or missing, not infinite."
            )
        return values


@dataclass(frozen=True)
class ModelSpec:
    """An unfitted pipeline and the exact registry columns it may inspect."""

    model_id: str
    feature_names: tuple[str, ...]
    complete_feature_names: tuple[str, ...]
    imputed_feature_names: tuple[str, ...]
    fit_contract: str
    pipeline: Pipeline


@dataclass(frozen=True)
class FittedModel:
    """A fitted fold-local pipeline with independent-unit training counts."""

    spec: ModelSpec
    pipeline: Pipeline
    training_row_count: int
    training_date_count: int


def _positive_finite(value: float, *, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric.")
    number = float(value)
    if not np.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be positive and finite.")
    return number


def _nonnegative_finite(value: float, *, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric.")
    number = float(value)
    if not np.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be nonnegative and finite.")
    return number


def _positive_integer(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if value < 1:
        raise ValueError(f"{name} must be positive.")
    return value


def _selected_registry_rows(
    registry: pd.DataFrame,
    model_id: str,
    *,
    feature_families: frozenset[str] | None = None,
) -> pd.DataFrame:
    if model_id not in MODEL_IDS:
        raise ModelingContractError(f"Unknown model_id {model_id!r}.")
    model_rows = registry.loc[registry["role"].eq("model")]
    families = MODEL_FAMILIES[model_id]
    selected = (
        model_rows if families is None else model_rows.loc[model_rows["family"].isin(families)]
    )
    if feature_families is not None:
        if model_id != "M2":
            raise ModelingContractError(
                "Feature-family restrictions are only defined for the M2 ablation."
            )
        allowed = {"calendar", "weather", "land_use", "geography", "satellite"}
        if not feature_families or not feature_families.issubset(allowed):
            raise ModelingContractError("Feature-family restriction is invalid.")
        selected = selected.loc[selected["family"].isin(feature_families)]
        if selected.empty:
            raise ModelingContractError("Feature-family restriction selected no predictors.")
    expected = EXPECTED_MODEL_FEATURE_COUNTS[model_id]
    if feature_families is None and len(selected) != expected:
        raise ModelingContractError(
            f"{model_id} requires exactly {expected} registered model features; "
            f"found {len(selected)}."
        )
    names = selected["feature_name"].tolist()
    if len(names) != len(set(names)):
        raise ModelingContractError(f"{model_id} selected duplicate feature names.")
    if model_id == "B0" and tuple(names) != CALENDAR_MODEL_FEATURE_NAMES:
        raise ModelingContractError("B0 requires the exact calendar sin/cos pair in order.")
    return selected


def _preprocessor(
    complete_names: tuple[str, ...],
    dynamic_names: tuple[str, ...],
    *,
    scale: bool,
) -> ColumnTransformer | CompleteFeatureValidator:
    if not dynamic_names:
        if not scale:
            return CompleteFeatureValidator()
        return ColumnTransformer(
            [
                (
                    "complete",
                    Pipeline(
                        [
                            ("validate", CompleteFeatureValidator()),
                            ("scale", StandardScaler()),
                        ]
                    ),
                    list(complete_names),
                )
            ],
            remainder="drop",
            verbose_feature_names_out=False,
        )

    complete_steps: list[tuple[str, Any]] = [("validate", CompleteFeatureValidator())]
    dynamic_steps: list[tuple[str, Any]] = [("impute", ObservedDynamicMedianImputer())]
    if scale:
        complete_steps.append(("scale", StandardScaler()))
        dynamic_steps.append(("scale", StandardScaler()))
    return ColumnTransformer(
        [
            ("complete", Pipeline(complete_steps), list(complete_names)),
            ("dynamic", Pipeline(dynamic_steps), list(dynamic_names)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def make_model_spec(
    registry: pd.DataFrame,
    model_id: str,
    *,
    development_start: str = "2020-05-01",
    random_state: int = 20260719,
    ridge_alpha: float = 1.0,
    elastic_alpha: float = 0.01,
    elastic_l1_ratio: float = 0.5,
    hgb_learning_rate: float = 0.05,
    hgb_max_iter: int = 300,
    hgb_max_leaf_nodes: int = 31,
    hgb_min_samples_leaf: int = 20,
    hgb_l2_regularization: float = 1.0,
    feature_families: frozenset[str] | None = None,
) -> ModelSpec:
    """Return an unfitted, registry-selected pipeline with no hidden data split."""

    validate_feature_registry(registry, development_start=development_start)
    if isinstance(random_state, bool) or not isinstance(random_state, int):
        raise TypeError("random_state must be an integer.")
    selected = _selected_registry_rows(registry, model_id, feature_families=feature_families)
    feature_names = tuple(selected["feature_name"].astype(str))
    complete = tuple(
        selected.loc[
            selected["static"].astype(bool) | selected["family"].eq("calendar"),
            "feature_name",
        ].astype(str)
    )
    dynamic = tuple(name for name in feature_names if name not in set(complete))

    if model_id == "B0":
        pipeline = Pipeline(
            [
                ("validate", CompleteFeatureValidator()),
                ("model", LinearRegression(fit_intercept=True)),
            ]
        )
        fit_contract = "one_equal_weight_training_date_mean_per_row"
    elif model_id in {"B1", "B2"}:
        pipeline = Pipeline(
            [
                ("preprocess", _preprocessor(complete, dynamic, scale=True)),
                (
                    "model",
                    Ridge(
                        alpha=_positive_finite(ridge_alpha, name="ridge_alpha"),
                        fit_intercept=True,
                        solver="lsqr",
                        tol=1e-4,
                    ),
                ),
            ]
        )
        fit_contract = "tract_date_rows_with_equal_total_weight_per_date"
    elif model_id == "M1":
        l1_ratio = float(elastic_l1_ratio)
        if not np.isfinite(l1_ratio) or not 0 <= l1_ratio <= 1:
            raise ValueError("elastic_l1_ratio must be finite and in [0, 1].")
        pipeline = Pipeline(
            [
                ("preprocess", _preprocessor(complete, dynamic, scale=True)),
                (
                    "model",
                    ElasticNet(
                        alpha=_positive_finite(elastic_alpha, name="elastic_alpha"),
                        l1_ratio=l1_ratio,
                        fit_intercept=True,
                        max_iter=20_000,
                        tol=1e-4,
                        selection="cyclic",
                        random_state=random_state,
                    ),
                ),
            ]
        )
        fit_contract = "tract_date_rows_with_equal_total_weight_per_date"
    else:
        pipeline = Pipeline(
            [
                ("preprocess", _preprocessor(complete, dynamic, scale=False)),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        loss="absolute_error",
                        learning_rate=_positive_finite(hgb_learning_rate, name="hgb_learning_rate"),
                        max_iter=_positive_integer(hgb_max_iter, name="hgb_max_iter"),
                        max_leaf_nodes=_positive_integer(
                            hgb_max_leaf_nodes, name="hgb_max_leaf_nodes"
                        ),
                        min_samples_leaf=_positive_integer(
                            hgb_min_samples_leaf, name="hgb_min_samples_leaf"
                        ),
                        l2_regularization=_nonnegative_finite(
                            hgb_l2_regularization,
                            name="hgb_l2_regularization",
                        ),
                        early_stopping=False,
                        random_state=random_state,
                    ),
                ),
            ]
        )
        fit_contract = "tract_date_rows_with_equal_total_weight_per_date"
    return ModelSpec(
        model_id=model_id,
        feature_names=feature_names,
        complete_feature_names=complete,
        imputed_feature_names=dynamic,
        fit_contract=fit_contract,
        pipeline=pipeline,
    )


def model_matrix(frame: pd.DataFrame, spec: ModelSpec) -> pd.DataFrame:
    """Select only the exact registered predictors for a model."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Model input must be a pandas DataFrame.")
    if frame.columns.duplicated().any():
        raise ModelingContractError("Model input contains duplicate columns.")
    missing = sorted(set(spec.feature_names) - set(frame.columns))
    if missing:
        raise ModelingContractError(f"Model input is missing features: {missing}")
    return frame.loc[:, list(spec.feature_names)].copy()


def fit_fold_model(
    spec: ModelSpec,
    frame: pd.DataFrame,
    target: pd.Series,
    keys: pd.DataFrame,
) -> FittedModel:
    """Fit one model using only rows already selected as a fold's training set."""

    X = model_matrix(frame, spec)
    if not isinstance(target, pd.Series):
        raise TypeError("Training target must be a pandas Series.")
    if not X.index.equals(target.index) or not X.index.equals(keys.index):
        raise ModelingContractError("Training features, target, and keys must align.")
    numeric_target = pd.to_numeric(target, errors="raise")
    target_values = numeric_target.to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(target_values).all():
        raise ModelingContractError("Training target must be finite and complete.")

    fitted = clone(spec.pipeline)
    if spec.model_id == "B0":
        prepared = prepare_b0_date_mean_training(X, numeric_target, keys)
        fitted.fit(prepared.features, prepared.target)
    else:
        weights = date_balanced_sample_weights(keys)
        fitted.fit(X, numeric_target, model__sample_weight=weights.to_numpy())
    return FittedModel(
        spec=spec,
        pipeline=fitted,
        training_row_count=len(X),
        training_date_count=int(pd.to_datetime(keys["target_date"]).nunique()),
    )


def predict_fold_model(fitted: FittedModel, frame: pd.DataFrame) -> np.ndarray:
    """Predict from an already fitted fold model without fitting any state."""

    X = model_matrix(frame, fitted.spec)
    predictions = np.asarray(fitted.pipeline.predict(X), dtype=float)
    if predictions.ndim != 1 or len(predictions) != len(X):
        raise AssertionError("Model prediction shape does not match input rows.")
    if not np.isfinite(predictions).all():
        raise ModelingContractError("Model produced a non-finite prediction.")
    return predictions
