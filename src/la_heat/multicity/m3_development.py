"""Pure source-city development algorithms for the proposed M3 experiment.

This module has no filesystem or network entry point.  Targets are always
passed as a separate Series, and prediction feature frames are rejected if
they expose target or QA-shaped columns.  The intended caller may therefore
exercise model selection and uncertainty logic on source cities without
creating a protocol lock or touching a future test-city target.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from la_heat.modeling import CompleteFeatureValidator, ObservedDynamicMedianImputer

KEY_COLUMNS: Final = ("city_id", "tract_geoid", "target_date")
MODEL_SEED: Final = 20_260_813
DENSITY_SEED: Final = 20_260_814
RISK_SEED: Final = 20_260_815
BOOTSTRAP_SEED: Final = 20_260_816

STATIC_FEATURES: Final = (
    "nlcd_open_water_fraction",
    "nlcd_developed_open_fraction",
    "nlcd_developed_low_fraction",
    "nlcd_developed_high_fraction",
    "nlcd_barren_fraction",
    "nlcd_forest_fraction",
    "nlcd_shrub_grass_fraction",
    "nlcd_agriculture_fraction",
    "nlcd_wetland_fraction",
    "impervious_mean_fraction",
    "impervious_p90_fraction",
    "impervious_at_least_50_fraction",
    "elevation_mean_m",
    "elevation_std_m",
    "slope_mean_degrees",
    "slope_p90_degrees",
    "gshhg_ocean_great_lakes_shore_distance_mean_km",
    "gshhg_ocean_great_lakes_shore_distance_p10_km",
)
CALENDAR_FEATURES: Final = ("calendar_doy_sin", "calendar_doy_cos")
WEATHER_FEATURES: Final = tuple(
    f"daymet_{variable}_{summary}_prev_{days}d"
    for days in (1, 3, 7)
    for variable, summary in (
        ("dayl_s", "mean"),
        ("prcp_mm", "sum"),
        ("srad_w_m2", "mean"),
        ("tmax_c", "mean"),
        ("tmin_c", "mean"),
        ("vp_pa", "mean"),
        ("srad_energy_mj_m2", "sum"),
    )
)
SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
B1_FEATURES: Final = (*CALENDAR_FEATURES, *WEATHER_FEATURES)
M2_FEATURES: Final = (*STATIC_FEATURES, *B1_FEATURES, *SENTINEL_FEATURES)
ANOMALY_FEATURES: Final = (*STATIC_FEATURES, *SENTINEL_FEATURES)
LEVEL_FEATURES: Final = (
    *B1_FEATURES,
    "elevation_mean_m",
    "city_centroid_latitude_deg",
)
RISK_DERIVED_FEATURES: Final = (
    "m3_interval_width_c",
    "m3_ensemble_point_sd_c",
    "m3_abs_b1_disagreement_c",
    "m3_abs_m2_legacy_disagreement_c",
    "uq_abs_log_density_ratio",
    "predictor_missing_count",
)
RISK_FEATURES: Final = (*M2_FEATURES, *RISK_DERIVED_FEATURES)
PREDICTION_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "target_date",
    "b1_prediction_c",
    "m2_legacy_prediction_c",
    "m3_level_prediction_c",
    "m3_anomaly_prediction_c",
    "m3_prediction_c",
    "m3_conformal_correction_c",
    "m3_lower_c",
    "m3_upper_c",
    "m3_interval_width_c",
    "m3_ensemble_point_sd_c",
    "uq_method",
    "uq_density_ratio_raw",
    "uq_density_ratio_clipped",
    "uq_weight_clip_hit",
    "m3_predicted_absolute_error_c",
    "m3_risk_percentile_within_city_date",
    "m3_abstain",
    "m3_accepted",
)


class M3DevelopmentError(ValueError):
    """Raised when source-only development violates its algorithm contract."""


@dataclass(frozen=True, slots=True)
class M3Candidate:
    candidate_id: str
    level_alpha: float
    anomaly_max_leaf_nodes: int


@dataclass(frozen=True, slots=True)
class FittedM3:
    candidate: M3Candidate
    level_model: Pipeline
    anomaly_model: Pipeline


@dataclass(frozen=True, slots=True)
class NestedLosoResult:
    selected_candidate_id: str
    candidate_metrics: pd.DataFrame
    outer_selections: pd.DataFrame
    oof_predictions: pd.DataFrame


M3_CANDIDATES: Final = tuple(
    M3Candidate(
        f"level_ridge_alpha_{alpha:g}__anomaly_hgb_leaves_{leaves}",
        float(alpha),
        leaves,
    )
    for alpha in (1, 10)
    for leaves in (15, 31)
)


def _forbidden_prediction_columns(columns: Sequence[object]) -> list[str]:
    forbidden: list[str] = []
    exact = {
        "date_usable",
        "target_available",
        "target_lst_c",
        "st_qa",
        "qa_pixel",
        "qa_radsat",
        "st_b10",
    }
    for value in columns:
        name = str(value)
        lowered = name.casefold()
        if name in KEY_COLUMNS:
            continue
        if (
            lowered in exact
            or lowered.startswith("target_")
            or lowered.startswith("qa_")
            or lowered.endswith("_qa")
            or "landsat_thermal" in lowered
        ):
            forbidden.append(name)
    return sorted(forbidden)


def validate_prediction_feature_frame(
    frame: pd.DataFrame,
    *,
    required_features: Sequence[str] = M2_FEATURES,
) -> pd.DataFrame:
    """Validate a target-blind predictor frame without reading target values."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise M3DevelopmentError("Prediction feature frame must be nonempty.")
    forbidden = _forbidden_prediction_columns(frame.columns)
    if forbidden:
        raise M3DevelopmentError(f"Prediction feature frame exposes target/QA columns: {forbidden}")
    missing = sorted((set(KEY_COLUMNS) | set(required_features)) - set(frame.columns))
    if missing:
        raise M3DevelopmentError(f"Prediction feature frame lacks columns: {missing}")
    result = frame.copy()
    if result.loc[:, KEY_COLUMNS].isna().any(axis=None):
        raise M3DevelopmentError("Prediction keys must be complete.")
    result["city_id"] = result["city_id"].astype(str)
    result["tract_geoid"] = result["tract_geoid"].astype(str)
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise").dt.strftime(
        "%Y-%m-%d"
    )
    if result.duplicated(list(KEY_COLUMNS)).any():
        raise M3DevelopmentError("Prediction keys must be unique.")
    return result


def city_date_row_weights(frame: pd.DataFrame) -> np.ndarray:
    """Give cities equal total weight, dates equal weight within city, then rows."""

    keys = validate_prediction_feature_frame(frame, required_features=())
    city_dates = keys.groupby("city_id", observed=True)["target_date"].nunique()
    row_counts = keys.groupby(["city_id", "target_date"], observed=True).size()
    city_count = int(keys["city_id"].nunique())
    weights = np.asarray(
        [
            1.0
            / (
                city_count
                * int(city_dates.loc[row.city_id])
                * int(row_counts.loc[(row.city_id, row.target_date)])
            )
            for row in keys.loc[:, ["city_id", "target_date"]].itertuples(index=False)
        ],
        dtype=float,
    )
    return weights * len(weights) / float(weights.sum())


def city_date_level_weights(level_frame: pd.DataFrame) -> np.ndarray:
    """Give every city equal total weight and every date equal weight within city."""

    city = level_frame["city_id"].astype(str)
    date_counts = level_frame.assign(city_id=city).groupby("city_id")["target_date"].nunique()
    city_count = int(city.nunique())
    weights = np.asarray(
        [1.0 / (city_count * int(date_counts.loc[value])) for value in city],
        dtype=float,
    )
    return weights * len(weights) / float(weights.sum())


def _preprocessor(
    complete: Sequence[str],
    dynamic: Sequence[str],
    *,
    scale: bool,
) -> ColumnTransformer:
    complete_steps: list[tuple[str, Any]] = [("validate", CompleteFeatureValidator())]
    dynamic_steps: list[tuple[str, Any]] = [("impute", ObservedDynamicMedianImputer())]
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


def build_b1_estimator() -> Pipeline:
    return Pipeline(
        [
            (
                "preprocess",
                _preprocessor(CALENDAR_FEATURES, WEATHER_FEATURES, scale=True),
            ),
            ("model", Ridge(alpha=10.0, fit_intercept=True, solver="lsqr", tol=0.0001)),
        ]
    )


def _hgb(*, max_leaf_nodes: int, seed: int) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.05,
        max_iter=300,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=50,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=seed,
    )


def build_m2_legacy_estimator() -> Pipeline:
    return Pipeline(
        [
            (
                "preprocess",
                _preprocessor(
                    (*STATIC_FEATURES, *CALENDAR_FEATURES),
                    (*WEATHER_FEATURES, *SENTINEL_FEATURES),
                    scale=False,
                ),
            ),
            ("model", _hgb(max_leaf_nodes=31, seed=20_260_719)),
        ]
    )


def build_m3_estimators(candidate: M3Candidate) -> tuple[Pipeline, Pipeline]:
    if candidate not in M3_CANDIDATES:
        raise M3DevelopmentError("M3 candidate is outside the fixed four-candidate set.")
    level = Pipeline(
        [
            (
                "preprocess",
                _preprocessor(
                    (*CALENDAR_FEATURES, "elevation_mean_m", "city_centroid_latitude_deg"),
                    WEATHER_FEATURES,
                    scale=True,
                ),
            ),
            (
                "model",
                Ridge(
                    alpha=candidate.level_alpha,
                    fit_intercept=True,
                    solver="lsqr",
                    tol=0.0001,
                ),
            ),
        ]
    )
    anomaly = Pipeline(
        [
            (
                "preprocess",
                _preprocessor(STATIC_FEATURES, SENTINEL_FEATURES, scale=False),
            ),
            (
                "model",
                _hgb(
                    max_leaf_nodes=candidate.anomaly_max_leaf_nodes,
                    seed=MODEL_SEED,
                ),
            ),
        ]
    )
    return level, anomaly


def _aligned_target(frame: pd.DataFrame, target: pd.Series) -> pd.Series:
    if not isinstance(target, pd.Series) or not target.index.equals(frame.index):
        raise M3DevelopmentError("Target must be a Series aligned to the feature frame.")
    numeric = pd.to_numeric(target, errors="raise").astype(float)
    if not np.isfinite(numeric.to_numpy()).all():
        raise M3DevelopmentError("Source development targets must be finite.")
    return numeric


def _level_table(frame: pd.DataFrame, target: pd.Series | None = None) -> pd.DataFrame:
    columns = [*KEY_COLUMNS, *LEVEL_FEATURES]
    working = frame.loc[:, columns].copy()
    if target is not None:
        working["observed_lst_c"] = target.to_numpy(dtype=float)
    aggregations: dict[str, str] = {name: "median" for name in LEVEL_FEATURES}
    if target is not None:
        aggregations["observed_lst_c"] = "median"
    return (
        working.groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg(aggregations)
        .reset_index()
    )


def fit_m3_candidate(
    frame: pd.DataFrame,
    target: pd.Series,
    candidate: M3Candidate,
) -> FittedM3:
    predictors = validate_prediction_feature_frame(
        frame,
        required_features=(*M2_FEATURES, "city_centroid_latitude_deg"),
    )
    y = _aligned_target(frame, target)
    level_frame = _level_table(predictors, y)
    level_model, anomaly_model = build_m3_estimators(candidate)
    level_model.fit(
        level_frame.loc[:, LEVEL_FEATURES],
        level_frame["observed_lst_c"],
        model__sample_weight=city_date_level_weights(level_frame),
    )
    level_by_key = y.groupby(
        [predictors["city_id"], predictors["target_date"]], observed=True
    ).transform("median")
    anomaly = y - level_by_key
    anomaly_model.fit(
        predictors.loc[:, ANOMALY_FEATURES],
        anomaly,
        model__sample_weight=city_date_row_weights(predictors),
    )
    return FittedM3(candidate, level_model, anomaly_model)


def predict_m3(model: FittedM3, frame: pd.DataFrame) -> pd.DataFrame:
    predictors = validate_prediction_feature_frame(
        frame,
        required_features=(*M2_FEATURES, "city_centroid_latitude_deg"),
    )
    level_frame = _level_table(predictors)
    level_frame["m3_level_prediction_c"] = model.level_model.predict(
        level_frame.loc[:, LEVEL_FEATURES]
    )
    level_lookup = level_frame.set_index(["city_id", "target_date"])[
        "m3_level_prediction_c"
    ]
    keys = pd.MultiIndex.from_frame(predictors.loc[:, ["city_id", "target_date"]])
    levels = level_lookup.reindex(keys).to_numpy(dtype=float)
    raw_anomaly = np.asarray(
        model.anomaly_model.predict(predictors.loc[:, ANOMALY_FEATURES]), dtype=float
    )
    raw = pd.Series(raw_anomaly, index=predictors.index)
    centered = raw - raw.groupby(
        [predictors["city_id"], predictors["target_date"]], observed=True
    ).transform("median")
    result = predictors.loc[:, KEY_COLUMNS].copy()
    result["m3_level_prediction_c"] = levels
    result["m3_anomaly_prediction_c"] = centered.to_numpy(dtype=float)
    result["m3_prediction_c"] = levels + centered.to_numpy(dtype=float)
    if not np.isfinite(result.iloc[:, 3:].to_numpy(dtype=float)).all():
        raise M3DevelopmentError("M3 produced a non-finite prediction.")
    return result


def _fit_comparator(
    estimator: Pipeline,
    features: Sequence[str],
    frame: pd.DataFrame,
    target: pd.Series,
) -> Pipeline:
    fitted = clone(estimator)
    fitted.fit(
        frame.loc[:, features],
        target,
        model__sample_weight=city_date_row_weights(frame),
    )
    return fitted


def _metrics(predictions: pd.DataFrame) -> dict[str, float]:
    frame = predictions.copy()
    frame["absolute_error_c"] = (
        frame["m3_prediction_c"] - frame["observed_lst_c"]
    ).abs()
    frame["observed_anomaly_c"] = frame["observed_lst_c"] - frame.groupby(
        ["city_id", "target_date"], observed=True
    )["observed_lst_c"].transform("median")
    frame["anomaly_absolute_error_c"] = (
        frame["m3_anomaly_prediction_c"] - frame["observed_anomaly_c"]
    ).abs()
    dates: list[dict[str, float | str]] = []
    for (city_id, target_date), date_frame in frame.groupby(
        ["city_id", "target_date"], observed=True, sort=True
    ):
        predicted_values = date_frame["m3_prediction_c"]
        observed_values = date_frame["observed_lst_c"]
        spearman = (
            -1.0
            if predicted_values.nunique() < 2 or observed_values.nunique() < 2
            else predicted_values.corr(observed_values, method="spearman")
        )
        dates.append(
            {
                "city_id": str(city_id),
                "target_date": str(target_date),
                "mae": float(date_frame["absolute_error_c"].mean()),
                "anomaly_mae": float(date_frame["anomaly_absolute_error_c"].mean()),
                "spearman": -1.0 if not math.isfinite(float(spearman)) else float(spearman),
            }
        )
    date_metrics = pd.DataFrame(dates)
    city_metrics = date_metrics.groupby("city_id", observed=True).agg(
        mae=("mae", "mean"), anomaly_mae=("anomaly_mae", "mean")
    )
    return {
        "equal_city_equal_date_mae_c": float(city_metrics["mae"].mean()),
        "equal_city_equal_date_anomaly_mae_c": float(
            city_metrics["anomaly_mae"].mean()
        ),
        "median_per_date_spearman": float(date_metrics["spearman"].median()),
    }


def _selection_key(row: Mapping[str, Any]) -> tuple[float, float, float, str]:
    return (
        round(float(row["equal_city_equal_date_mae_c"]), 12),
        round(float(row["equal_city_equal_date_anomaly_mae_c"]), 12),
        -round(float(row["median_per_date_spearman"]), 12),
        str(row["candidate_id"]),
    )


def _candidate_loso_metrics(
    frame: pd.DataFrame,
    target: pd.Series,
    cities: Sequence[str],
    candidate: M3Candidate,
) -> dict[str, Any]:
    predictions: list[pd.DataFrame] = []
    for held_city in sorted(cities):
        train_mask = frame["city_id"].astype(str).ne(held_city)
        fitted = fit_m3_candidate(
            frame.loc[train_mask].reset_index(drop=True),
            target.loc[train_mask].reset_index(drop=True),
            candidate,
        )
        held = frame.loc[~train_mask].reset_index(drop=True)
        predicted = predict_m3(fitted, held)
        predicted["observed_lst_c"] = target.loc[~train_mask].to_numpy(dtype=float)
        predictions.append(predicted)
    metrics = _metrics(pd.concat(predictions, ignore_index=True))
    return {"candidate_id": candidate.candidate_id, **metrics}


def nested_whole_city_loso(
    frame: pd.DataFrame,
    target: pd.Series,
) -> NestedLosoResult:
    """Select M3 deterministically inside each whole-city outer fold."""

    predictors = validate_prediction_feature_frame(
        frame,
        required_features=(*M2_FEATURES, "city_centroid_latitude_deg"),
    )
    y = _aligned_target(frame, target)
    cities = sorted(predictors["city_id"].unique())
    if len(cities) != 4:
        raise M3DevelopmentError("Nested M3 development requires exactly four source cities.")

    candidate_rows = [
        _candidate_loso_metrics(predictors, y, cities, candidate)
        for candidate in M3_CANDIDATES
    ]
    candidate_metrics = pd.DataFrame(candidate_rows).sort_values(
        "candidate_id", kind="stable"
    ).reset_index(drop=True)
    selected = min(candidate_rows, key=_selection_key)
    outer_rows: list[dict[str, Any]] = []
    outer_predictions: list[pd.DataFrame] = []
    candidate_by_id = {value.candidate_id: value for value in M3_CANDIDATES}
    for outer_city in cities:
        outer_train_mask = predictors["city_id"].ne(outer_city)
        development = predictors.loc[outer_train_mask].reset_index(drop=True)
        development_y = y.loc[outer_train_mask].reset_index(drop=True)
        inner_cities = sorted(development["city_id"].unique())
        inner_rows = [
            _candidate_loso_metrics(development, development_y, inner_cities, candidate)
            for candidate in M3_CANDIDATES
        ]
        inner_selected = min(inner_rows, key=_selection_key)
        candidate = candidate_by_id[str(inner_selected["candidate_id"])]
        fitted = fit_m3_candidate(development, development_y, candidate)
        held = predictors.loc[~outer_train_mask].reset_index(drop=True)
        predicted = predict_m3(fitted, held)
        b1 = _fit_comparator(build_b1_estimator(), B1_FEATURES, development, development_y)
        m2 = _fit_comparator(build_m2_legacy_estimator(), M2_FEATURES, development, development_y)
        predicted["b1_prediction_c"] = b1.predict(held.loc[:, B1_FEATURES])
        predicted["m2_legacy_prediction_c"] = m2.predict(held.loc[:, M2_FEATURES])
        predicted["observed_lst_c"] = y.loc[~outer_train_mask].to_numpy(dtype=float)
        predicted["outer_city_id"] = outer_city
        predicted["selected_candidate_id"] = candidate.candidate_id
        outer_predictions.append(predicted)
        outer_rows.append(
            {
                "outer_city_id": outer_city,
                "selected_candidate_id": candidate.candidate_id,
                **{key: inner_selected[key] for key in inner_selected if key != "candidate_id"},
            }
        )
    return NestedLosoResult(
        str(selected["candidate_id"]),
        candidate_metrics,
        pd.DataFrame(outer_rows).sort_values("outer_city_id").reset_index(drop=True),
        pd.concat(outer_predictions, ignore_index=True).sort_values(
            list(KEY_COLUMNS), kind="stable"
        ).reset_index(drop=True),
    )


def finite_sample_test_atom_quantile(
    scores: Sequence[float],
    weights: Sequence[float],
    order_city: Sequence[object],
    order_date: Sequence[object],
    order_geoid: Sequence[object],
    *,
    alpha: float = 0.10,
    test_weight: float = 1.0,
) -> float:
    """Return the CQR correction with one explicit test atom at infinity."""

    values = np.asarray(scores, dtype=float)
    weight = np.asarray(weights, dtype=float)
    if (
        values.ndim != 1
        or weight.shape != values.shape
        or not len(values)
        or not np.isfinite(values).all()
        or not np.isfinite(weight).all()
        or np.any(values < 0)
        or np.any(weight <= 0)
        or not 0 < alpha < 1
        or not math.isfinite(test_weight)
        or test_weight <= 0
        or not all(len(values) == len(part) for part in (order_city, order_date, order_geoid))
    ):
        raise M3DevelopmentError("Invalid finite-sample conformal inputs.")
    ordered = pd.DataFrame(
        {
            "score": values,
            "weight": weight,
            "city_id": pd.Series(order_city, dtype="string"),
            "target_date": pd.to_datetime(order_date),
            "tract_geoid": pd.Series(order_geoid, dtype="string"),
        }
    ).sort_values(
        ["score", "city_id", "target_date", "tract_geoid"], kind="stable"
    )
    threshold = (1.0 - alpha) * (float(weight.sum()) + test_weight)
    if threshold > float(weight.sum()) + 1e-12:
        raise M3DevelopmentError("Finite-sample conformal correction is unbounded.")
    hit = int(
        np.searchsorted(
            ordered["weight"].cumsum().to_numpy(dtype=float), threshold, side="left"
        )
    )
    return float(ordered["score"].iloc[min(hit, len(ordered) - 1)])


def u0_cross_conformal_correction(
    scores: Sequence[float],
    source_keys: pd.DataFrame,
    *,
    alpha: float = 0.10,
) -> float:
    """Unweighted multi-source U0 correction with city/date-balanced rows."""

    key_frame = validate_prediction_feature_frame(source_keys, required_features=())
    return finite_sample_test_atom_quantile(
        scores,
        city_date_row_weights(key_frame),
        key_frame["city_id"],
        key_frame["target_date"],
        key_frame["tract_geoid"],
        alpha=alpha,
        test_weight=1.0,
    )


def domain_sample_weights(keys: pd.DataFrame, domain: Sequence[int]) -> np.ndarray:
    """Balance domains, then cities, dates, and rows within each domain."""

    key_frame = validate_prediction_feature_frame(keys, required_features=())
    labels = np.asarray(domain)
    if labels.ndim != 1 or len(labels) != len(key_frame) or set(labels.tolist()) != {0, 1}:
        raise M3DevelopmentError("Domain labels must contain binary source/test values.")
    weights = np.zeros(len(key_frame), dtype=float)
    for value in (0, 1):
        positions = np.flatnonzero(labels == value)
        subset = key_frame.iloc[positions]
        city_dates = subset.groupby("city_id", observed=True)["target_date"].nunique()
        row_counts = subset.groupby(["city_id", "target_date"], observed=True).size()
        city_count = int(subset["city_id"].nunique())
        weights[positions] = [
            0.5
            / (
                city_count
                * int(city_dates.loc[row.city_id])
                * int(row_counts.loc[(row.city_id, row.target_date)])
            )
            for row in subset.loc[:, ["city_id", "target_date"]].itertuples(index=False)
        ]
    if not np.isclose(weights[labels == 0].sum(), 0.5) or not np.isclose(
        weights[labels == 1].sum(), 0.5
    ):
        raise M3DevelopmentError("Domain balancing failed conservation.")
    return weights


def build_domain_classifier() -> Pipeline:
    """Build the exact unlabeled covariate-shift domain classifier."""

    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    penalty="l2",
                    C=1.0,
                    solver="lbfgs",
                    max_iter=2_000,
                    random_state=DENSITY_SEED,
                ),
            ),
        ]
    )


def _numeric_matrix(frame: pd.DataFrame, columns: Sequence[str], *, label: str) -> pd.DataFrame:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise M3DevelopmentError(f"{label} lacks columns: {missing}")
    matrix = frame.loc[:, columns].apply(pd.to_numeric, errors="raise").astype(float)
    if np.isinf(matrix.to_numpy()).any():
        raise M3DevelopmentError(f"{label} contains infinite values.")
    if matrix.isna().all(axis=0).any():
        raise M3DevelopmentError(f"{label} contains an all-missing feature.")
    return matrix


def cross_fit_domain_probabilities(
    frame: pd.DataFrame,
    domain: Sequence[int],
    *,
    feature_order: Sequence[str] = M2_FEATURES,
    folds: int = 5,
) -> pd.DataFrame:
    """Cross-fit P(test-domain|X) by deterministic complete city-date groups."""

    predictors = validate_prediction_feature_frame(frame, required_features=feature_order)
    matrix = _numeric_matrix(predictors, feature_order, label="Domain feature matrix")
    labels = np.asarray(domain, dtype=int)
    if labels.ndim != 1 or len(labels) != len(predictors) or set(labels.tolist()) != {0, 1}:
        raise M3DevelopmentError("Domain labels must contain binary source/test values.")
    assignment = np.asarray(
        [
            deterministic_group_fold(row.city_id, row.target_date, folds=folds)
            for row in predictors.loc[:, ["city_id", "target_date"]].itertuples(index=False)
        ],
        dtype=int,
    )
    if set(assignment.tolist()) != set(range(folds)):
        raise M3DevelopmentError("Deterministic domain folds are incomplete.")
    probabilities = np.full(len(predictors), np.nan, dtype=float)
    for fold in range(folds):
        validation = assignment == fold
        training = ~validation
        if not validation.any() or set(labels[training].tolist()) != {0, 1}:
            raise M3DevelopmentError(
                f"Domain fold {fold} training lacks source or test observations."
            )
        estimator = build_domain_classifier()
        estimator.fit(
            matrix.loc[training],
            labels[training],
            model__sample_weight=domain_sample_weights(
                predictors.loc[training].reset_index(drop=True), labels[training]
            ),
        )
        class_index = list(estimator["model"].classes_).index(1)
        probabilities[validation] = estimator.predict_proba(matrix.loc[validation])[
            :, class_index
        ]
    if (
        not np.isfinite(probabilities).all()
        or np.any(probabilities <= 0)
        or np.any(probabilities >= 1)
    ):
        raise M3DevelopmentError("Cross-fit domain probabilities are invalid.")
    return pd.DataFrame(
        {
            "domain_probability": probabilities,
            "density_fold": assignment,
        },
        index=frame.index,
    )


def density_ratios_from_probabilities(
    source_probability: Sequence[float],
    test_probability: Sequence[float],
    *,
    clip: tuple[float, float] = (0.2, 5.0),
) -> dict[str, np.ndarray]:
    """Convert equal-prior domain probabilities to normalized density ratios."""

    source = np.asarray(source_probability, dtype=float)
    test = np.asarray(test_probability, dtype=float)
    lower, upper = map(float, clip)
    if (
        source.ndim != 1
        or test.ndim != 1
        or not len(source)
        or not len(test)
        or not np.isfinite(source).all()
        or not np.isfinite(test).all()
        or np.any((source <= 0) | (source >= 1))
        or np.any((test <= 0) | (test >= 1))
        or not 0 < lower < 1 < upper
    ):
        raise M3DevelopmentError("Invalid density-ratio inputs.")
    source_raw = source / (1.0 - source)
    test_raw = test / (1.0 - test)
    source_clipped = np.clip(source_raw, lower, upper)
    test_clipped = np.clip(test_raw, lower, upper)
    normalizer = float(source_clipped.mean())
    return {
        "source_raw": source_raw,
        "test_raw": test_raw,
        "source_weight": source_clipped / normalizer,
        "test_weight": test_clipped / normalizer,
        "source_clip_hit": (source_raw < lower) | (source_raw > upper),
        "test_clip_hit": (test_raw < lower) | (test_raw > upper),
    }


def _effective_sample_size(weights: np.ndarray) -> float:
    return float(weights.sum() ** 2 / np.square(weights).sum())


def density_stability_report(
    source_weights: Sequence[float],
    source_city: Sequence[object],
    source_date: Sequence[object],
    source_clip_hit: Sequence[bool],
    test_clip_hit: Sequence[bool],
    *,
    roc_auc: float,
    pooled_coverage: float,
    city_coverages: Mapping[str, float],
    weighted_wis: float,
    unweighted_wis: float,
    clip_upper: float,
) -> dict[str, Any]:
    """Apply the fixed source-only stability and benefit gates for weighted UQ."""

    weights = np.asarray(source_weights, dtype=float)
    city = pd.Series(source_city, dtype="string")
    dates = pd.to_datetime(pd.Series(source_date))
    source_hits = np.asarray(source_clip_hit, dtype=bool)
    test_hits = np.asarray(test_clip_hit, dtype=bool)
    if (
        len(weights) != len(city)
        or len(weights) != len(dates)
        or len(weights) != len(source_hits)
        or not len(test_hits)
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
        or unweighted_wis <= 0
    ):
        raise M3DevelopmentError("Invalid density stability inputs.")
    grouped = pd.DataFrame({"city": city, "date": dates, "weight": weights}).groupby(
        ["city", "date"], observed=True
    )["weight"].sum()
    city_share = (
        pd.DataFrame({"city": city, "weight": weights}).groupby("city")["weight"].sum()
        / weights.sum()
    )
    row_ess_fraction = _effective_sample_size(weights) / len(weights)
    date_ess_fraction = _effective_sample_size(grouped.to_numpy()) / len(grouped)
    wis_improvement = 1.0 - float(weighted_wis) / float(unweighted_wis)
    gates = {
        "auc_at_most_0_95": 0.5 <= float(roc_auc) <= 0.95,
        "row_ess_at_least_25_percent": row_ess_fraction >= 0.25,
        "date_ess_at_least_50_percent": date_ess_fraction >= 0.50,
        "every_source_city_share_at_least_5_percent": bool(city_share.ge(0.05).all()),
        "source_clip_fraction_at_most_20_percent": float(source_hits.mean()) <= 0.20,
        "test_clip_fraction_at_most_20_percent": float(test_hits.mean()) <= 0.20,
        "pooled_coverage_in_85_to_95_percent": 0.85 <= float(pooled_coverage) <= 0.95,
        "every_city_coverage_at_least_80_percent": bool(city_coverages)
        and all(float(value) >= 0.80 for value in city_coverages.values()),
        "wis_improvement_at_least_2_percent": wis_improvement >= 0.02,
    }
    return {
        "method": f"density_ratio_clip_{clip_upper:g}",
        "clip_upper": float(clip_upper),
        "weighted_wis": float(weighted_wis),
        "unweighted_wis": float(unweighted_wis),
        "wis_improvement_fraction": wis_improvement,
        "row_ess_fraction": row_ess_fraction,
        "date_ess_fraction": date_ess_fraction,
        "gates": gates,
        "stable": all(gates.values()),
    }


def select_density_method(reports: Sequence[Mapping[str, Any]]) -> str:
    stable = [row for row in reports if row.get("stable") is True]
    if not stable:
        return "unweighted_cross_conformal"
    selected = min(
        stable,
        key=lambda row: (
            round(float(row["weighted_wis"]), 12),
            float(row["clip_upper"]),
            str(row["method"]),
        ),
    )
    return str(selected["method"])


def build_risk_feature_matrix(
    predictors: pd.DataFrame,
    prediction_diagnostics: pd.DataFrame,
    clipped_density_ratio: Sequence[float],
) -> pd.DataFrame:
    """Build the exact target-blind matrix used to learn OOF absolute error."""

    features = validate_prediction_feature_frame(predictors, required_features=M2_FEATURES)
    required = (
        *KEY_COLUMNS,
        "b1_prediction_c",
        "m2_legacy_prediction_c",
        "m3_prediction_c",
        "m3_interval_width_c",
        "m3_ensemble_point_sd_c",
    )
    missing = sorted(set(required) - set(prediction_diagnostics.columns))
    forbidden = _forbidden_prediction_columns(prediction_diagnostics.columns)
    if missing or forbidden:
        raise M3DevelopmentError(
            f"Risk diagnostics are invalid: missing={missing}, forbidden={forbidden}"
        )
    diagnostics = prediction_diagnostics.loc[:, required].copy()
    diagnostics["city_id"] = diagnostics["city_id"].astype(str)
    diagnostics["tract_geoid"] = diagnostics["tract_geoid"].astype(str)
    diagnostics["target_date"] = pd.to_datetime(
        diagnostics["target_date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    if not diagnostics.loc[:, KEY_COLUMNS].equals(features.loc[:, KEY_COLUMNS]):
        raise M3DevelopmentError("Risk predictor and diagnostic key order differs.")
    numeric_names = required[len(KEY_COLUMNS) :]
    numeric = _numeric_matrix(diagnostics, numeric_names, label="Risk diagnostics")
    if numeric.isna().any(axis=None):
        raise M3DevelopmentError("Risk diagnostics must be finite and complete.")
    ratio = np.asarray(clipped_density_ratio, dtype=float)
    if len(ratio) != len(features) or not np.isfinite(ratio).all() or np.any(ratio <= 0):
        raise M3DevelopmentError("Clipped density ratios must be positive and aligned.")
    result = features.loc[:, M2_FEATURES].apply(pd.to_numeric, errors="raise").astype(float)
    if np.isinf(result.to_numpy()).any():
        raise M3DevelopmentError("Risk public predictors contain infinity.")
    result["m3_interval_width_c"] = numeric["m3_interval_width_c"].to_numpy()
    result["m3_ensemble_point_sd_c"] = numeric["m3_ensemble_point_sd_c"].to_numpy()
    result["m3_abs_b1_disagreement_c"] = np.abs(
        numeric["m3_prediction_c"].to_numpy()
        - numeric["b1_prediction_c"].to_numpy()
    )
    result["m3_abs_m2_legacy_disagreement_c"] = np.abs(
        numeric["m3_prediction_c"].to_numpy()
        - numeric["m2_legacy_prediction_c"].to_numpy()
    )
    result["uq_abs_log_density_ratio"] = np.abs(np.log(ratio))
    result["predictor_missing_count"] = features.loc[:, M2_FEATURES].isna().sum(axis=1)
    return validate_risk_feature_matrix(result)


def validate_risk_feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Require the exact risk-feature schema while permitting imputable NaNs."""

    if tuple(frame.columns) != RISK_FEATURES:
        raise M3DevelopmentError("Risk feature schema changed.")
    forbidden = _forbidden_prediction_columns(frame.columns)
    if forbidden:
        raise M3DevelopmentError(f"Risk features expose target/QA columns: {forbidden}")
    numeric = frame.apply(pd.to_numeric, errors="raise").astype(float)
    if np.isinf(numeric.to_numpy()).any() or numeric.isna().all(axis=0).any():
        raise M3DevelopmentError("Risk features contain infinity or an all-missing column.")
    nonnegative = [*RISK_DERIVED_FEATURES]
    if numeric.loc[:, nonnegative].lt(0).any(axis=None):
        raise M3DevelopmentError("Derived risk features must be nonnegative.")
    return numeric


def build_risk_estimator() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            (
                "model",
                HistGradientBoostingRegressor(
                    loss="absolute_error",
                    learning_rate=0.05,
                    max_iter=200,
                    max_leaf_nodes=15,
                    min_samples_leaf=50,
                    l2_regularization=1.0,
                    early_stopping=False,
                    random_state=RISK_SEED,
                ),
            ),
        ]
    )


def risk_acceptance_flags(
    keys: pd.DataFrame,
    risk_score: Sequence[float],
    *,
    retention: float = 0.80,
) -> pd.DataFrame:
    """Accept the lowest-risk 80% within each target-blind city-date cohort."""

    key_frame = validate_prediction_feature_frame(keys, required_features=())
    score = np.asarray(risk_score, dtype=float)
    if len(score) != len(key_frame) or not np.isfinite(score).all() or not 0 < retention <= 1:
        raise M3DevelopmentError("Invalid risk-acceptance inputs.")
    working = key_frame.loc[:, KEY_COLUMNS].copy()
    working["m3_predicted_absolute_error_c"] = score
    working["_row"] = np.arange(len(working))
    records: list[pd.DataFrame] = []
    for _, group in working.groupby(["city_id", "target_date"], observed=True, sort=True):
        ordered = group.sort_values(
            ["m3_predicted_absolute_error_c", "tract_geoid"], kind="stable"
        ).copy()
        accepted_count = math.ceil(retention * len(ordered))
        ordered["m3_risk_percentile_within_city_date"] = (
            np.arange(1, len(ordered) + 1, dtype=float) / len(ordered)
        )
        ordered["m3_accepted"] = np.arange(len(ordered)) < accepted_count
        ordered["m3_abstain"] = ~ordered["m3_accepted"]
        records.append(ordered)
    result = pd.concat(records).sort_values("_row").drop(columns="_row").reset_index(drop=True)
    return result


def select_risk_method(candidates: Sequence[Mapping[str, Any]]) -> str:
    """Select a source-validated risk ranker or deterministically accept all."""

    eligible: list[Mapping[str, Any]] = []
    for row in candidates:
        improvements = row.get("per_city_improvement", {})
        if (
            float(row.get("equal_city_improvement", -math.inf)) >= 0.10
            and float(row.get("minimum_retention", -math.inf)) >= 0.60
            and isinstance(improvements, Mapping)
            and bool(improvements)
            and all(float(value) >= 0 for value in improvements.values())
        ):
            eligible.append(row)
    if not eligible:
        return "none_accept_all"
    preference = {"learned_error": 0, "interval_width": 1, "ensemble_sd": 2}
    return str(
        min(
            eligible,
            key=lambda row: (
                round(float(row["accepted_equal_city_mae_c"]), 12),
                preference.get(str(row["method"]), 99),
                str(row["method"]),
            ),
        )["method"]
    )


def deterministic_group_fold(city: object, target_date: object, *, folds: int = 5) -> int:
    """Stable city-date fold used by a future density-classifier implementation."""

    if folds < 2:
        raise M3DevelopmentError("Density cross-fitting requires at least two folds.")
    token = f"{DENSITY_SEED}|{city}|{pd.Timestamp(target_date).date().isoformat()}"
    return int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big") % folds


def validate_prediction_output(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate the exact target-blind 21-column M3 publication schema."""

    if tuple(frame.columns) != PREDICTION_COLUMNS:
        raise M3DevelopmentError("M3 prediction output schema changed.")
    if frame.empty or frame.loc[:, KEY_COLUMNS].isna().any(axis=None):
        raise M3DevelopmentError("M3 prediction output keys are empty or missing.")
    result = frame.copy()
    result["city_id"] = result["city_id"].astype(str)
    result["tract_geoid"] = result["tract_geoid"].astype(str)
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise").dt.strftime(
        "%Y-%m-%d"
    )
    if result.duplicated(list(KEY_COLUMNS)).any():
        raise M3DevelopmentError("M3 prediction output keys are duplicated.")
    numeric_columns = (
        "b1_prediction_c",
        "m2_legacy_prediction_c",
        "m3_level_prediction_c",
        "m3_anomaly_prediction_c",
        "m3_prediction_c",
        "m3_conformal_correction_c",
        "m3_lower_c",
        "m3_upper_c",
        "m3_interval_width_c",
        "m3_ensemble_point_sd_c",
        "uq_density_ratio_raw",
        "uq_density_ratio_clipped",
        "m3_predicted_absolute_error_c",
        "m3_risk_percentile_within_city_date",
    )
    numeric = result.loc[:, numeric_columns].apply(pd.to_numeric, errors="raise").astype(float)
    if not np.isfinite(numeric.to_numpy()).all():
        raise M3DevelopmentError("M3 prediction output contains non-finite values.")
    tolerance = 1e-10
    if (
        numeric["m3_conformal_correction_c"].lt(0).any()
        or numeric["m3_interval_width_c"].lt(0).any()
        or numeric["m3_ensemble_point_sd_c"].lt(0).any()
        or numeric["uq_density_ratio_raw"].le(0).any()
        or numeric["uq_density_ratio_clipped"].le(0).any()
        or numeric["m3_predicted_absolute_error_c"].lt(0).any()
        or not numeric["m3_risk_percentile_within_city_date"].between(
            0, 1, inclusive="right"
        ).all()
        or not np.allclose(
            numeric["m3_prediction_c"],
            numeric["m3_level_prediction_c"] + numeric["m3_anomaly_prediction_c"],
            atol=tolerance,
            rtol=0,
        )
        or not np.allclose(
            numeric["m3_lower_c"],
            numeric["m3_prediction_c"] - numeric["m3_conformal_correction_c"],
            atol=tolerance,
            rtol=0,
        )
        or not np.allclose(
            numeric["m3_upper_c"],
            numeric["m3_prediction_c"] + numeric["m3_conformal_correction_c"],
            atol=tolerance,
            rtol=0,
        )
        or not np.allclose(
            numeric["m3_interval_width_c"],
            numeric["m3_upper_c"] - numeric["m3_lower_c"],
            atol=tolerance,
            rtol=0,
        )
    ):
        raise M3DevelopmentError("M3 prediction numeric contract changed.")
    for column in ("uq_weight_clip_hit", "m3_abstain", "m3_accepted"):
        if result[column].dtype != bool:
            raise M3DevelopmentError(f"{column} must be boolean.")
    if not result["m3_accepted"].eq(~result["m3_abstain"]).all():
        raise M3DevelopmentError("M3 accepted and abstain flags disagree.")
    if result["uq_method"].isna().any() or result["uq_method"].astype(str).str.len().eq(0).any():
        raise M3DevelopmentError("M3 UQ method must be explicit.")
    return result
