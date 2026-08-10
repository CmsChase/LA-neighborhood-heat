"""Deterministic non-evidence smoke run for the four-city model interfaces.

This module intentionally has no reader for target, predictor, or result files.  It
loads only the frozen predictor/model contract, creates tiny data in memory, and
writes caller-owned smoke artifacts outside canonical project output directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import clone

from la_heat.multicity.predictor_readiness import validate_predictor_frame
from la_heat.multicity.transfer_model import (
    EXTERNAL_CITIES,
    KEY_COLUMNS,
    TRAINING_CITY,
    ConformalCalibration,
    FittedTransferModels,
    build_frozen_transfer_estimators,
    calibrate_frozen_intervals,
    fit_frozen_transfer_models,
    load_frozen_transfer_contract,
    predict_external_cities,
)
from la_heat.provenance import atomic_csv, atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "multicity-synthetic-smoke-v1"
DEFAULT_SEED: Final = 20_260_810
ARTIFACT_SCOPE: Final = "synthetic_smoke_only"
NON_EVIDENCE_WARNING: Final = (
    "SYNTHETIC SMOKE ONLY - NOT SCIENTIFIC EVIDENCE OR A REAL TARGET RESULT"
)
CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
MODEL_IDS: Final = ("B1_transfer", "M2_transfer")
PROJECT_SMOKE_DIRECTORY: Final = ".tmp"


class SyntheticSmokeError(RuntimeError):
    """Raised when the synthetic smoke run could be confused with real evidence."""


@dataclass(frozen=True, slots=True)
class SyntheticInputs:
    """In-memory inputs with every synthetic target kept outside predictor frames."""

    training_predictors: pd.DataFrame
    training_target: pd.Series
    calibration_predictors: pd.DataFrame
    calibration_target: pd.Series
    external_predictors: pd.DataFrame
    external_target: pd.Series
    loco_predictors: pd.DataFrame
    loco_target: pd.Series


@dataclass(frozen=True, slots=True)
class LocoFold:
    """One mechanical leave-one-city-out split expressed as row positions."""

    held_out_city: str
    train_positions: tuple[int, ...]
    test_positions: tuple[int, ...]


def validate_synthetic_output_directory(
    project_root: str | Path,
    output_directory: str | Path,
) -> Path:
    """Reject canonical project trees before creating or fitting anything."""

    root = Path(project_root).resolve()
    output = Path(output_directory).resolve()
    if output == root:
        raise SyntheticSmokeError("Synthetic smoke output cannot be the project root.")
    if root in output.parents:
        smoke_root = (root / PROJECT_SMOKE_DIRECTORY).resolve()
        if output != smoke_root and smoke_root not in output.parents:
            raise SyntheticSmokeError(
                "Synthetic smoke output inside the project must stay under '.tmp'."
            )
    return output


def _feature_order(contract: dict[str, Any]) -> tuple[str, ...]:
    registry = contract.get("feature_registry")
    if not isinstance(registry, dict):
        raise SyntheticSmokeError("Frozen feature registry is missing.")
    order = tuple(str(value) for value in registry.get("feature_order", []))
    if len(order) != 46 or len(order) != len(set(order)):
        raise SyntheticSmokeError("Synthetic smoke requires the exact 46-feature contract.")
    return order


def _validate_predictor_only_frame(
    frame: pd.DataFrame,
    contract: dict[str, Any],
) -> None:
    feature_order = _feature_order(contract)
    expected = [*KEY_COLUMNS, *feature_order]
    if list(frame.columns) != expected:
        raise SyntheticSmokeError(
            "Smoke predictors must contain only ordered keys and frozen features; "
            "target, label, audit, and identifier features are forbidden."
        )
    forbidden = {
        "synthetic_target_c",
        "landsat_lst_c",
        "target_lst_c",
        "label",
        *KEY_COLUMNS,
    }
    if forbidden.intersection(feature_order):
        raise SyntheticSmokeError("A key or target-like column entered the feature contract.")
    validate_predictor_frame(
        frame,
        key_columns=list(KEY_COLUMNS),
        feature_order=list(feature_order),
    )


def _synthetic_cohort(
    contract: dict[str, Any],
    city_dates: dict[str, tuple[str, ...]],
    *,
    tracts_per_date: int,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.Series]:
    feature_order = _feature_order(contract)
    city_effects = {
        "los_angeles_ca": 0.0,
        "phoenix_az": 2.2,
        "houston_tx": 1.1,
        "chicago_il": -1.4,
    }
    rows: list[dict[str, object]] = []
    targets: list[float] = []
    for city_index, city_id in enumerate(CITY_IDS):
        for date_index, target_date in enumerate(city_dates.get(city_id, ())):
            for tract_index in range(tracts_per_date):
                values = rng.normal(loc=city_index * 0.08, scale=1.0, size=len(feature_order))
                row: dict[str, object] = {
                    "city_id": city_id,
                    "tract_geoid": f"{city_index + 1:02d}{tract_index:09d}",
                    "target_date": target_date,
                }
                row.update(zip(feature_order, values, strict=True))
                rows.append(row)
                nonlinear = 1.4 * np.tanh(values[-1]) + 0.55 * values[-2] ** 2
                targets.append(
                    29.0
                    + city_effects[city_id]
                    + 0.3 * date_index
                    + 2.1 * values[0]
                    - 1.3 * values[3]
                    + 0.7 * values[12]
                    + nonlinear
                    + float(rng.normal(scale=0.2))
                )
    frame = pd.DataFrame(rows, columns=[*KEY_COLUMNS, *feature_order])
    target = pd.Series(targets, index=frame.index, name="synthetic_target_c", dtype=float)
    _validate_predictor_only_frame(frame, contract)
    return frame, target


def make_synthetic_inputs(
    contract: dict[str, Any],
    *,
    seed: int = DEFAULT_SEED,
) -> SyntheticInputs:
    """Create fixed, small cohorts without opening any project data or target file."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("Synthetic seed must be an integer.")
    rng = np.random.default_rng(seed)
    training, training_target = _synthetic_cohort(
        contract,
        {
            TRAINING_CITY: (
                "2020-06-15",
                "2021-06-15",
                "2022-06-15",
                "2023-06-15",
            )
        },
        tracts_per_date=28,
        rng=rng,
    )
    calibration, calibration_target = _synthetic_cohort(
        contract,
        {TRAINING_CITY: ("2024-06-20", "2024-08-20")},
        tracts_per_date=24,
        rng=rng,
    )
    external, external_target = _synthetic_cohort(
        contract,
        {
            "phoenix_az": ("2025-06-05", "2025-08-05"),
            "houston_tx": ("2025-06-06", "2025-08-06"),
            "chicago_il": ("2025-06-07", "2025-08-07"),
        },
        tracts_per_date=12,
        rng=rng,
    )
    loco, loco_target = _synthetic_cohort(
        contract,
        {
            "los_angeles_ca": ("2030-06-01", "2030-07-01", "2030-08-01"),
            "phoenix_az": ("2030-06-02", "2030-07-02", "2030-08-02"),
            "houston_tx": ("2030-06-03", "2030-07-03", "2030-08-03"),
            "chicago_il": ("2030-06-04", "2030-07-04", "2030-08-04"),
        },
        tracts_per_date=12,
        rng=rng,
    )
    return SyntheticInputs(
        training,
        training_target,
        calibration,
        calibration_target,
        external,
        external_target,
        loco,
        loco_target,
    )


def build_loco_folds(predictors: pd.DataFrame) -> tuple[LocoFold, ...]:
    """Build four whole-city folds and fail if the city universe drifts."""

    observed = tuple(sorted(predictors["city_id"].astype(str).unique()))
    if observed != tuple(sorted(CITY_IDS)):
        raise SyntheticSmokeError(
            f"LOCO smoke requires the exact four-city universe; found {list(observed)}."
        )
    cities = predictors["city_id"].astype(str).to_numpy()
    folds = tuple(
        LocoFold(
            held_out_city=held_out,
            train_positions=tuple(np.flatnonzero(cities != held_out).tolist()),
            test_positions=tuple(np.flatnonzero(cities == held_out).tolist()),
        )
        for held_out in CITY_IDS
    )
    validate_loco_folds(predictors, folds)
    return folds


def validate_loco_folds(
    predictors: pd.DataFrame,
    folds: tuple[LocoFold, ...],
) -> None:
    """Assert non-overlap, exact held-out cities, and one test visit per row."""

    row_positions = set(range(len(predictors)))
    if {fold.held_out_city for fold in folds} != set(CITY_IDS) or len(folds) != len(
        CITY_IDS
    ):
        raise SyntheticSmokeError("LOCO folds must hold out each canonical city exactly once.")
    test_visits: list[int] = []
    cities = predictors["city_id"].astype(str).reset_index(drop=True)
    for fold in folds:
        train = set(fold.train_positions)
        test = set(fold.test_positions)
        if (
            not train
            or not test
            or train.intersection(test)
            or train.union(test) != row_positions
            or len(train) != len(fold.train_positions)
            or len(test) != len(fold.test_positions)
        ):
            raise SyntheticSmokeError(f"LOCO fold {fold.held_out_city} overlaps or drops rows.")
        train_cities = set(cities.iloc[sorted(train)])
        test_cities = set(cities.iloc[sorted(test)])
        if test_cities != {fold.held_out_city} or train_cities != set(CITY_IDS) - {
            fold.held_out_city
        }:
            raise SyntheticSmokeError(
                f"LOCO fold {fold.held_out_city} has target-city leakage or city drift."
            )
        test_visits.extend(test)
    if sorted(test_visits) != sorted(row_positions):
        raise SyntheticSmokeError("Every LOCO row must be held out exactly once.")


def _equal_city_equal_date_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = frame.loc[:, ["city_id", "target_date"]].copy()
    keys["target_date"] = pd.to_datetime(keys["target_date"], errors="raise")
    row_counts = keys.groupby(["city_id", "target_date"], observed=True)[
        "target_date"
    ].transform("size")
    dates_per_city = (
        keys.drop_duplicates()
        .groupby("city_id", observed=True)["target_date"]
        .nunique()
        .to_dict()
    )
    city_count = int(keys["city_id"].nunique())
    weights = np.array(
        [
            1.0 / (city_count * int(dates_per_city[city]) * int(count))
            for city, count in zip(keys["city_id"], row_counts, strict=True)
        ],
        dtype=float,
    )
    city_totals = pd.Series(weights).groupby(keys["city_id"].reset_index(drop=True)).sum()
    if not np.allclose(city_totals.to_numpy(), 1.0 / city_count) or not np.isclose(
        weights.sum(), 1.0
    ):
        raise SyntheticSmokeError("LOCO weights do not give every city and date equal weight.")
    return weights


def fit_predict_loco_fold(
    predictors: pd.DataFrame,
    target: pd.Series,
    contract: dict[str, Any],
    fold: LocoFold,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Fit B1/M2 on three cities and predict the fourth without reading its labels."""

    _validate_predictor_only_frame(predictors, contract)
    if not isinstance(target, pd.Series) or not target.index.equals(predictors.index):
        raise SyntheticSmokeError("Synthetic LOCO target must align to predictor rows.")
    train_positions = list(fold.train_positions)
    test_positions = list(fold.test_positions)
    train = predictors.iloc[train_positions]
    test = predictors.iloc[test_positions]
    if set(train["city_id"].astype(str)) != set(CITY_IDS) - {fold.held_out_city} or set(
        test["city_id"].astype(str)
    ) != {fold.held_out_city}:
        raise SyntheticSmokeError("LOCO fit/prediction city boundary changed.")

    # Only this sliced, three-city target is coerced or inspected before prediction.
    train_target = pd.to_numeric(target.iloc[train_positions], errors="raise")
    if not np.isfinite(train_target.to_numpy(dtype=float)).all():
        raise SyntheticSmokeError("Synthetic LOCO training labels must be finite.")
    weights = _equal_city_equal_date_weights(train)
    estimators = build_frozen_transfer_estimators(contract)
    model_specs = (
        (MODEL_IDS[0], clone(estimators.b1), estimators.b1_feature_order),
        (MODEL_IDS[1], clone(estimators.m2), estimators.m2_feature_order),
    )
    outputs: list[pd.DataFrame] = []
    for model_id, model, feature_order in model_specs:
        model.fit(
            train.loc[:, feature_order],
            train_target,
            model__sample_weight=weights,
        )
        predicted = np.asarray(model.predict(test.loc[:, feature_order]), dtype=float)
        if predicted.shape != (len(test),) or not np.isfinite(predicted).all():
            raise SyntheticSmokeError(f"Synthetic {model_id} LOCO prediction is invalid.")
        result = test.loc[:, KEY_COLUMNS].copy().reset_index(drop=True)
        result.insert(0, "synthetic_row_position", test_positions)
        result.insert(0, "held_out_city", fold.held_out_city)
        result.insert(0, "fold_id", f"loco_{fold.held_out_city}")
        result.insert(3, "model_id", model_id)
        result["prediction_c"] = predicted
        outputs.append(result)
    prediction = pd.concat(outputs, ignore_index=True)
    audit: dict[str, object] = {
        "fold_id": f"loco_{fold.held_out_city}",
        "held_out_city": fold.held_out_city,
        "training_city_ids": "|".join(sorted(set(train["city_id"].astype(str)))),
        "testing_city_ids": fold.held_out_city,
        "training_row_count": len(train),
        "testing_row_count": len(test),
        "training_date_count": int(
            train.loc[:, ["city_id", "target_date"]].drop_duplicates().shape[0]
        ),
        "model_ids": "|".join(MODEL_IDS),
        "held_out_target_read_before_prediction": False,
        "synthetic_only": True,
    }
    return prediction, audit


def build_loco_predictions(
    predictors: pd.DataFrame,
    target: pd.Series,
    contract: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every whole-city fold with a fresh preprocessing/model fit."""

    folds = build_loco_folds(predictors)
    predictions: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    for fold in folds:
        prediction, audit = fit_predict_loco_fold(predictors, target, contract, fold)
        predictions.append(prediction)
        audits.append(audit)
    combined = pd.concat(predictions, ignore_index=True)
    visits = combined.groupby(["synthetic_row_position", "model_id"], observed=True).size()
    if len(visits) != len(predictors) * len(MODEL_IDS) or not visits.eq(1).all():
        raise SyntheticSmokeError("LOCO predictions do not cover every row/model exactly once.")
    return combined, pd.DataFrame(audits)


def build_external_predictions(
    training_predictors: pd.DataFrame,
    training_target: pd.Series,
    calibration_predictors: pd.DataFrame,
    calibration_target: pd.Series,
    external_predictors: pd.DataFrame,
    contract: dict[str, Any],
) -> tuple[pd.DataFrame, FittedTransferModels, ConformalCalibration]:
    """Exercise the exact frozen external core; external labels are not an argument."""

    for frame in (
        training_predictors,
        calibration_predictors,
        external_predictors,
    ):
        _validate_predictor_only_frame(frame, contract)
    models = fit_frozen_transfer_models(
        training_predictors,
        training_target,
        contract,
    )
    calibration = calibrate_frozen_intervals(
        models,
        calibration_predictors,
        calibration_target,
        contract,
    )
    prediction = predict_external_cities(
        models,
        calibration,
        external_predictors,
    )
    prediction.insert(0, "synthetic_row_position", np.arange(len(prediction), dtype=int))
    return prediction, models, calibration


def _target_values(target: pd.Series, predictors: pd.DataFrame) -> np.ndarray:
    if not isinstance(target, pd.Series) or not target.index.equals(predictors.index):
        raise SyntheticSmokeError("Synthetic evaluation target must align to predictors.")
    values = pd.to_numeric(target, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise SyntheticSmokeError("Synthetic evaluation target must be finite.")
    return values


def evaluate_loco_predictions(
    predictions: pd.DataFrame,
    predictors: pd.DataFrame,
    target: pd.Series,
) -> pd.DataFrame:
    """Attach synthetic held-out labels only after every fold has predicted."""

    actual = _target_values(target, predictors)
    result = predictions.copy()
    positions = result["synthetic_row_position"].to_numpy(dtype=int)
    if np.any(positions < 0) or np.any(positions >= len(actual)):
        raise SyntheticSmokeError("LOCO prediction row position is outside its cohort.")
    result["synthetic_target_c"] = actual[positions]
    result["error_c"] = result["prediction_c"] - result["synthetic_target_c"]
    result["absolute_error_c"] = result["error_c"].abs()
    result.insert(0, "scientific_evidence", False)
    result.insert(0, "artifact_scope", ARTIFACT_SCOPE)
    return result


def evaluate_external_predictions(
    predictions: pd.DataFrame,
    predictors: pd.DataFrame,
    external_target: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score synthetic external labels after the target-blind prediction commit step."""

    expected_cities = set(EXTERNAL_CITIES)
    if set(predictions["city_id"].astype(str)) != expected_cities:
        raise SyntheticSmokeError("External prediction city cohort changed.")
    if not predictions.loc[:, KEY_COLUMNS].reset_index(drop=True).equals(
        predictors.loc[:, KEY_COLUMNS].reset_index(drop=True)
    ):
        raise SyntheticSmokeError("External prediction keys changed before evaluation.")
    actual = _target_values(external_target, predictors)
    result = predictions.copy().reset_index(drop=True)
    result["synthetic_target_c"] = actual
    for prefix in ("b1", "m2"):
        result[f"{prefix}_error_c"] = (
            result[f"{prefix}_prediction_c"] - result["synthetic_target_c"]
        )
        result[f"{prefix}_absolute_error_c"] = result[f"{prefix}_error_c"].abs()
    result["m2_interval_covered"] = result["synthetic_target_c"].between(
        result["m2_lower_c"], result["m2_upper_c"], inclusive="both"
    )
    result.insert(0, "scientific_evidence", False)
    result.insert(0, "artifact_scope", ARTIFACT_SCOPE)

    long_rows: list[pd.DataFrame] = []
    for model_id, prefix in zip(MODEL_IDS, ("b1", "m2"), strict=True):
        selected = result.loc[
            :,
            [
                "synthetic_row_position",
                *KEY_COLUMNS,
                "synthetic_target_c",
                f"{prefix}_prediction_c",
                f"{prefix}_error_c",
                f"{prefix}_absolute_error_c",
            ],
        ].copy()
        selected.columns = [
            "synthetic_row_position",
            *KEY_COLUMNS,
            "synthetic_target_c",
            "prediction_c",
            "error_c",
            "absolute_error_c",
        ]
        selected.insert(0, "model_id", model_id)
        long_rows.append(selected)
    return result, pd.concat(long_rows, ignore_index=True)


def _metric_tables(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    groups = ["city_id", "target_date", "model_id"]
    rows: list[dict[str, object]] = []
    for keys, frame in predictions.groupby(groups, observed=True, sort=True):
        error = frame["error_c"].to_numpy(dtype=float)
        rows.append(
            {
                "city_id": str(keys[0]),
                "target_date": str(keys[1]),
                "model_id": str(keys[2]),
                "row_count": len(frame),
                "mae_c": float(np.mean(np.abs(error))),
                "rmse_c": float(np.sqrt(np.mean(np.square(error)))),
                "mean_error_c": float(np.mean(error)),
            }
        )
    date_metrics = pd.DataFrame(rows).sort_values(groups, kind="stable").reset_index(drop=True)
    city_rows: list[dict[str, object]] = []
    for keys, frame in date_metrics.groupby(["city_id", "model_id"], observed=True):
        city_rows.append(
            {
                "city_id": str(keys[0]),
                "model_id": str(keys[1]),
                "date_count": len(frame),
                "row_count": int(frame["row_count"].sum()),
                "equal_date_mae_c": float(frame["mae_c"].mean()),
                "equal_date_rmse_c": float(frame["rmse_c"].mean()),
                "equal_date_mean_error_c": float(frame["mean_error_c"].mean()),
            }
        )
    city_metrics = (
        pd.DataFrame(city_rows)
        .sort_values(["city_id", "model_id"], kind="stable")
        .reset_index(drop=True)
    )
    for frame in (date_metrics, city_metrics):
        frame.insert(0, "scientific_evidence", False)
        frame.insert(0, "artifact_scope", ARTIFACT_SCOPE)
    return date_metrics, city_metrics


def _external_reliability(external: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for city_id, frame in external.groupby("city_id", observed=True, sort=True):
        accepted = frame["m2_accepted"].astype(bool)
        rows.append(
            {
                "city_id": str(city_id),
                "row_count": len(frame),
                "interval_coverage": float(frame["m2_interval_covered"].mean()),
                "mean_interval_width_c": float(frame["m2_interval_width_c"].mean()),
                "retention_fraction": float(accepted.mean()),
                "all_prediction_mae_c": float(frame["m2_absolute_error_c"].mean()),
                "accepted_prediction_mae_c": (
                    float(frame.loc[accepted, "m2_absolute_error_c"].mean())
                    if accepted.any()
                    else np.nan
                ),
            }
        )
    result = pd.DataFrame(rows)
    result.insert(0, "scientific_evidence", False)
    result.insert(0, "artifact_scope", ARTIFACT_SCOPE)
    return result


def _mark_non_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "scientific_evidence", False)
    result.insert(0, "artifact_scope", ARTIFACT_SCOPE)
    return result


def _render_metric_figure(
    loco_city_metrics: pd.DataFrame,
    external_city_metrics: pd.DataFrame,
    destination: Path,
) -> None:
    display = {
        "los_angeles_ca": "Los Angeles",
        "phoenix_az": "Phoenix",
        "houston_tx": "Houston",
        "chicago_il": "Chicago",
    }
    colors = {"B1_transfer": "#6b7280", "M2_transfer": "#d97706"}
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharey=True)
    panels = (
        (axes[0], loco_city_metrics, list(CITY_IDS), "Four synthetic LOCO folds"),
        (
            axes[1],
            external_city_metrics,
            ["phoenix_az", "houston_tx", "chicago_il"],
            "Frozen LA-to-external synthetic path",
        ),
    )
    for axis, metrics, city_order, title in panels:
        x = np.arange(len(city_order), dtype=float)
        width = 0.36
        for offset, model_id in zip((-width / 2, width / 2), MODEL_IDS, strict=True):
            indexed = metrics.loc[metrics["model_id"].eq(model_id)].set_index("city_id")
            values = [float(indexed.loc[city, "equal_date_mae_c"]) for city in city_order]
            axis.bar(
                x + offset,
                values,
                width,
                label=model_id.replace("_transfer", ""),
                color=colors[model_id],
            )
        axis.set_title(title)
        axis.set_xticks(x, [display[city] for city in city_order], rotation=20, ha="right")
        axis.grid(axis="y", alpha=0.25)
        axis.set_axisbelow(True)
    axes[0].set_ylabel("Equal-date synthetic MAE (degrees C)")
    axes[1].legend(frameon=False, loc="upper right")
    figure.suptitle(NON_EVIDENCE_WARNING, color="#991b1b", fontweight="bold", fontsize=12)
    figure.text(
        0.5,
        0.01,
        "Generated entirely from deterministic in-memory labels; no canonical result file read.",
        ha="center",
        fontsize=9,
        color="#4b5563",
    )
    figure.tight_layout(rect=(0, 0.05, 1, 0.92))
    temporary = destination.with_suffix(destination.suffix + ".partial")
    figure.savefig(
        temporary,
        format="png",
        dpi=160,
        bbox_inches="tight",
        metadata={"Title": NON_EVIDENCE_WARNING, "Software": ALGORITHM_VERSION},
    )
    plt.close(figure)
    temporary.replace(destination)


def _file_record(path: Path, *, rows: int | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        record["rows"] = rows
    return record


def run_synthetic_smoke(
    project_root: str | Path,
    output_directory: str | Path,
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run the full in-memory smoke path and write only labeled smoke artifacts."""

    root = Path(project_root).resolve()
    output = validate_synthetic_output_directory(root, output_directory)
    contract = load_frozen_transfer_contract(root)
    inputs = make_synthetic_inputs(contract, seed=seed)

    loco_raw, loco_audit = build_loco_predictions(
        inputs.loco_predictors,
        inputs.loco_target,
        contract,
    )
    loco_predictions = evaluate_loco_predictions(
        loco_raw,
        inputs.loco_predictors,
        inputs.loco_target,
    )
    loco_date_metrics, loco_city_metrics = _metric_tables(loco_predictions)

    external_raw, fitted, calibration = build_external_predictions(
        inputs.training_predictors,
        inputs.training_target,
        inputs.calibration_predictors,
        inputs.calibration_target,
        inputs.external_predictors,
        contract,
    )
    external_predictions, external_long = evaluate_external_predictions(
        external_raw,
        inputs.external_predictors,
        inputs.external_target,
    )
    external_date_metrics, external_city_metrics = _metric_tables(external_long)
    external_reliability = _external_reliability(external_predictions)
    loco_audit = _mark_non_evidence(loco_audit)

    output.mkdir(parents=True, exist_ok=True)
    table_outputs = {
        "loco_fold_audit": ("synthetic_loco_fold_audit.csv", loco_audit),
        "loco_predictions": ("synthetic_loco_predictions.csv", loco_predictions),
        "loco_date_metrics": ("synthetic_loco_date_metrics.csv", loco_date_metrics),
        "loco_city_metrics": ("synthetic_loco_city_metrics.csv", loco_city_metrics),
        "external_predictions": (
            "synthetic_external_predictions.csv",
            external_predictions,
        ),
        "external_date_metrics": (
            "synthetic_external_date_metrics.csv",
            external_date_metrics,
        ),
        "external_city_metrics": (
            "synthetic_external_city_metrics.csv",
            external_city_metrics,
        ),
        "external_reliability": (
            "synthetic_external_reliability.csv",
            external_reliability,
        ),
    }
    artifact_records: dict[str, dict[str, object]] = {}
    for name, (filename, frame) in table_outputs.items():
        path = output / filename
        atomic_csv(frame, path)
        artifact_records[name] = _file_record(path, rows=len(frame))

    figure_path = output / "synthetic_smoke_metrics.png"
    _render_metric_figure(loco_city_metrics, external_city_metrics, figure_path)
    artifact_records["metric_figure"] = _file_record(figure_path)

    b1_mae = float(
        external_city_metrics.loc[
            external_city_metrics["model_id"].eq("B1_transfer"), "equal_date_mae_c"
        ].mean()
    )
    m2_mae = float(
        external_city_metrics.loc[
            external_city_metrics["model_id"].eq("M2_transfer"), "equal_date_mae_c"
        ].mean()
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_synthetic_smoke_not_scientific_evidence",
        "warning": NON_EVIDENCE_WARNING,
        "artifact_scope": ARTIFACT_SCOPE,
        "synthetic_only": True,
        "scientific_evidence": False,
        "seed": seed,
        "city_ids": list(CITY_IDS),
        "external_city_ids": sorted(EXTERNAL_CITIES),
        "contract_commit_sha256": contract["commit_sha256"],
        "contract_source": {
            "path": (
                "manifests/multicity/reviews/portable_predictor_contract/"
                "PORTABLE_PREDICTOR_CONTRACT.json"
            ),
            "purpose": "read_only_schema_and_fixed_model_parameters",
            "contains_target_values": False,
        },
        "cohorts": {
            "la_2020_2023_training_rows": len(inputs.training_predictors),
            "la_2024_calibration_rows": len(inputs.calibration_predictors),
            "three_city_2025_external_rows": len(inputs.external_predictors),
            "four_city_mechanical_loco_rows": len(inputs.loco_predictors),
        },
        "model_audit": {
            "b1_feature_count": len(fitted.b1_feature_order),
            "m2_feature_count": len(fitted.m2_feature_order),
            "training_row_count": fitted.training_row_count,
            "training_date_count": fitted.training_date_count,
            "calibration_row_count": calibration.calibration_row_count,
            "calibration_date_count": calibration.calibration_date_count,
            "loco_fold_count": len(loco_audit),
            "loco_status": "mechanical_diagnostic_not_part_of_frozen_confirmation",
        },
        "synthetic_external_point_metrics": {
            "equal_city_equal_date_b1_mae_c": b1_mae,
            "equal_city_equal_date_m2_mae_c": m2_mae,
            "relative_m2_improvement": 1.0 - (m2_mae / b1_mae),
        },
        "guardrails": {
            "real_predictor_files_read": [],
            "real_target_files_read": [],
            "canonical_files_written": [],
            "external_target_passed_to_fit_or_prediction": False,
            "external_target_used_only_after_prediction": True,
            "city_or_geoid_used_as_model_feature": False,
            "fold_local_preprocessing": True,
            "every_loco_city_held_out_once": True,
        },
        "artifacts": artifact_records,
    }
    summary["content_sha256"] = canonical_sha256(summary)
    atomic_json(summary, output / "synthetic_smoke_summary.json")
    return summary
