"""Run fixed stage-2 source validation for C1/C2/C3.

The runner reads only the four authorized source cities.  It performs nested
whole-city validation, writes source-only evidence, and never touches opened
city or LA-2025 targets.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.m3_2x2.run import (  # noqa: E402
    SourceData,
    Variant,
    _level_estimator,
    _level_features,
    _normalize_keys,
    _predict_level,
    build_level_training_table,
    load_source_data,
)
from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    B1_FEATURES,
    KEY_COLUMNS,
    M3_CANDIDATES,
    build_b1_estimator,
    build_m3_estimators,
    city_date_level_weights,
    city_date_row_weights,
)

CONTRACT_PATH = Path(__file__).with_name("fixed_contract.toml")
LEGACY_CONFIG_PATH = ROOT / "experiments/m3_2x2/experiment.toml"
PREDICTOR_COMPLETION_PATH = (
    ROOT
    / "manifests/multicity/next_experiment/source_predictor_extension_v1"
    / "SOURCE_PREDICTORS_46_COMPLETE.json"
)
OUTPUT_DIR = ROOT / "exports/FOUR_CITY_ABSOLUTE_ERROR_STAGE_2"
MODEL_IDS = ("B1", "M3", "C1", "C2", "C3")
CANDIDATE_IDS = ("C1", "C2", "C3")
HOTSPOT_FRACTION = 0.20
HOTSPOT_RULE = "score_desc_geoid_asc_exact_top_ceiling_20_percent"
ORIGINAL_M3_EXPECTED_MAE_C = 4.579558805988182
B1_EXPECTED_MAE_C = 3.6578113483469457
REPRODUCTION_TOLERANCE_C = 1e-8


@dataclass
class FitBundle:
    train_cities: tuple[str, ...]
    b1_model: Any
    anomaly_model: Any
    original_level_model: Any
    c3_model: DecisionTreeRegressor
    elevation_medians: dict[str, float]
    elevation_min_c: float
    elevation_max_c: float
    c3_training_rows: int
    c3_training_dates_by_city: dict[str, int]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_contract() -> dict[str, Any]:
    contract = tomllib.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if tuple(row["id"] for row in contract["candidates"]) != CANDIDATE_IDS:
        raise ValueError("The candidate set is not the frozen C1/C2/C3 set.")
    if contract["scope"]["qa_candidate"] != "QA4K":
        raise ValueError("Stage 2 requires frozen QA4K.")
    if contract["source_selection"]["simplicity_order"] != list(CANDIDATE_IDS):
        raise ValueError("The frozen simplicity order drifted.")
    return contract


def load_data(
    contract: dict[str, Any],
) -> tuple[SourceData, list[dict[str, Any]], dict[str, Any]]:
    legacy_config = tomllib.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"))
    cities = tuple(contract["scope"]["source_cities"])
    if tuple(legacy_config["experiment"]["source_cities"]) != cities:
        raise ValueError("The existing source loader city order differs from the contract.")
    if legacy_config["experiment"]["qa_id"] != "4k":
        raise ValueError("The existing source loader is not fixed to QA4K.")
    if (
        float(legacy_config["experiment"]["level_ridge_alpha"]) != 10.0
        or int(legacy_config["experiment"]["anomaly_max_leaf_nodes"]) != 31
    ):
        raise ValueError("The original M3 specification differs from the fixed contract.")
    data, inputs = load_source_data(ROOT, legacy_config)

    target_blocks: list[pd.DataFrame] = []
    for city_id in cities:
        path = (
            ROOT
            / "data/interim/multicity/m3_source_development_v2/qa_candidates/cities"
            / city_id
            / "4k/targets.parquet"
        )
        table = _normalize_keys(pd.read_parquet(path))
        target_blocks.append(table.loc[:, [*KEY_COLUMNS, "spatial_block"]])
    blocks = pd.concat(target_blocks, ignore_index=True)
    scored = data.scored_frame.merge(
        blocks,
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
    )
    if scored["spatial_block"].isna().any():
        raise ValueError("A QA4K scoring row lacks its frozen spatial block.")
    return SourceData(data.prediction_universe, scored, data.target), inputs, legacy_config


def _candidate_spec() -> Any:
    return next(
        candidate
        for candidate in M3_CANDIDATES
        if candidate.level_alpha == 10.0 and candidate.anomaly_max_leaf_nodes == 31
    )


def _city_date_feature_medians(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.loc[:, [*KEY_COLUMNS, *B1_FEATURES]]
        .groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg({name: "median" for name in B1_FEATURES})
        .reset_index()
    )


def _target_levels(scored: pd.DataFrame, target: pd.Series) -> pd.DataFrame:
    levels = scored.loc[:, ["city_id", "target_date"]].copy()
    levels["observed_lst_c"] = target.to_numpy(float)
    return (
        levels.groupby(["city_id", "target_date"], observed=True, sort=True)
        .agg(observed_lst_c=("observed_lst_c", "median"))
        .reset_index()
    )


def fit_bundle(data: SourceData, train_cities: Iterable[str]) -> FitBundle:
    train_cities = tuple(sorted(str(city) for city in train_cities))
    scored_mask = data.scored_frame["city_id"].isin(train_cities)
    universe_mask = data.prediction_universe["city_id"].isin(train_cities)
    scored = data.scored_frame.loc[scored_mask].reset_index(drop=True)
    target = data.target.loc[scored_mask].reset_index(drop=True)
    universe = data.prediction_universe.loc[universe_mask].reset_index(drop=True)
    if set(scored["city_id"]) != set(train_cities):
        raise ValueError("A training city has no QA4K scoring rows.")

    row_weights = city_date_row_weights(scored)
    b1_model = build_b1_estimator()
    b1_model.fit(
        scored.loc[:, B1_FEATURES],
        target,
        model__sample_weight=row_weights,
    )

    _, anomaly_model = build_m3_estimators(_candidate_spec())
    target_level = target.groupby(
        [scored["city_id"], scored["target_date"]], observed=True
    ).transform("median")
    anomaly_model.fit(
        scored.loc[:, ANOMALY_FEATURES],
        target - target_level,
        model__sample_weight=row_weights,
    )

    original_variant = Variant(
        "M3",
        "original M3 level",
        True,
        "selected_training_support",
    )
    original_table = build_level_training_table(
        scored,
        target,
        universe,
        original_variant,
    )
    original_features = _level_features(True)
    original_level_model = _level_estimator(True, 10.0)
    original_level_model.fit(
        original_table.loc[:, original_features],
        original_table["observed_lst_c"],
        model__sample_weight=city_date_level_weights(original_table),
    )

    c3_features = _city_date_feature_medians(universe)
    c3_targets = _target_levels(scored, target)
    c3_table = c3_features.merge(
        c3_targets,
        on=["city_id", "target_date"],
        how="inner",
        validate="one_to_one",
    )
    if c3_table.loc[:, B1_FEATURES].isna().any().any():
        raise ValueError(
            "C3 has missing city-date medians, but the frozen contract does not fix an "
            "imputation estimator. Stop instead of choosing one post hoc."
        )
    anchor = c3_table["daymet_tmax_c_mean_prev_1d"].to_numpy(float)
    response = c3_table["observed_lst_c"].to_numpy(float) - anchor
    c3_model = DecisionTreeRegressor(
        criterion="absolute_error",
        max_depth=2,
        min_samples_leaf=10,
        random_state=20260916,
    )
    c3_model.fit(
        c3_table.loc[:, B1_FEATURES],
        response,
        sample_weight=city_date_level_weights(c3_table),
    )

    elevation_medians = {
        str(city): float(group["elevation_mean_m"].median())
        for city, group in universe.groupby("city_id", observed=True, sort=True)
    }
    return FitBundle(
        train_cities=train_cities,
        b1_model=b1_model,
        anomaly_model=anomaly_model,
        original_level_model=original_level_model,
        c3_model=c3_model,
        elevation_medians=elevation_medians,
        elevation_min_c=min(elevation_medians.values()),
        elevation_max_c=max(elevation_medians.values()),
        c3_training_rows=int(len(c3_table)),
        c3_training_dates_by_city={
            str(city): int(group["target_date"].nunique())
            for city, group in c3_table.groupby("city_id", observed=True, sort=True)
        },
    )


def _level_values(level_table: pd.DataFrame, held: pd.DataFrame) -> np.ndarray:
    lookup = level_table.set_index(["city_id", "target_date"])["level_prediction_c"]
    keys = pd.MultiIndex.from_frame(held.loc[:, ["city_id", "target_date"]])
    values = lookup.reindex(keys).to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("A held-out complete-universe row lacks its city-date level.")
    return values


def _make_model_rows(
    held: pd.DataFrame,
    model_id: str,
    prediction: np.ndarray,
    level: np.ndarray,
    anomaly: np.ndarray,
) -> pd.DataFrame:
    result = held.loc[:, KEY_COLUMNS].copy()
    result.insert(0, "model_id", model_id)
    result["prediction_c"] = prediction
    result["level_prediction_c"] = level
    result["anomaly_prediction_c"] = anomaly
    return result


def predict_bundle(
    data: SourceData,
    bundle: FitBundle,
    held_city: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if held_city in bundle.train_cities:
        raise ValueError("Held city appears in its training set.")
    held_universe = data.prediction_universe.loc[
        data.prediction_universe["city_id"].eq(held_city)
    ].reset_index(drop=True)
    held_scored = data.scored_frame.loc[
        data.scored_frame["city_id"].eq(held_city)
    ].reset_index(drop=True)
    held_target = data.target.loc[
        data.scored_frame["city_id"].eq(held_city)
    ].reset_index(drop=True)

    b1 = np.asarray(
        bundle.b1_model.predict(held_universe.loc[:, B1_FEATURES]), dtype=float
    )
    b1_frame = held_universe.loc[:, ["city_id", "target_date"]].copy()
    b1_frame["b1"] = b1
    c1_levels = b1_frame.groupby(
        ["city_id", "target_date"], observed=True, sort=True
    )["b1"].transform("median").to_numpy(float)
    b1_anomaly = b1 - c1_levels

    raw_anomaly = pd.Series(
        bundle.anomaly_model.predict(held_universe.loc[:, ANOMALY_FEATURES]),
        index=held_universe.index,
        dtype=float,
    )
    anomaly = (
        raw_anomaly
        - raw_anomaly.groupby(
            [held_universe["city_id"], held_universe["target_date"]], observed=True
        ).transform("median")
    ).to_numpy(float)

    original_level_table = _predict_level(
        bundle.original_level_model,
        held_universe,
        _level_features(True),
    )
    original_levels = _level_values(original_level_table, held_universe)

    held_elevation_median = float(held_universe["elevation_mean_m"].median())
    c2_fallback = not (
        bundle.elevation_min_c
        <= held_elevation_median
        <= bundle.elevation_max_c
    )
    c2_levels = c1_levels if c2_fallback else original_levels

    c3_table = _city_date_feature_medians(held_universe)
    if c3_table.loc[:, B1_FEATURES].isna().any().any():
        raise ValueError("Held C3 city-date medians are missing under the frozen contract.")
    c3_correction = bundle.c3_model.predict(c3_table.loc[:, B1_FEATURES])
    c3_table["level_prediction_c"] = (
        c3_table["daymet_tmax_c_mean_prev_1d"].to_numpy(float) + c3_correction
    )
    c3_levels = _level_values(
        c3_table.loc[:, ["city_id", "target_date", "level_prediction_c"]],
        held_universe,
    )

    full = pd.concat(
        [
            _make_model_rows(held_universe, "B1", b1, c1_levels, b1_anomaly),
            _make_model_rows(
                held_universe,
                "M3",
                original_levels + anomaly,
                original_levels,
                anomaly,
            ),
            _make_model_rows(
                held_universe, "C1", c1_levels + anomaly, c1_levels, anomaly
            ),
            _make_model_rows(
                held_universe, "C2", c2_levels + anomaly, c2_levels, anomaly
            ),
            _make_model_rows(
                held_universe, "C3", c3_levels + anomaly, c3_levels, anomaly
            ),
        ],
        ignore_index=True,
    )
    full.insert(0, "held_city_id", held_city)
    full.insert(0, "training_city_ids", "+".join(bundle.train_cities))

    observed = held_scored.loc[:, [*KEY_COLUMNS, "spatial_block"]].copy()
    observed["observed_lst_c"] = held_target.to_numpy(float)
    scored = full.merge(observed, on=list(KEY_COLUMNS), how="inner", validate="many_to_one")
    audit = {
        "training_city_ids": list(bundle.train_cities),
        "held_city_id": held_city,
        "training_elevation_medians_m": bundle.elevation_medians,
        "training_elevation_range_m": [bundle.elevation_min_c, bundle.elevation_max_c],
        "held_elevation_median_m": held_elevation_median,
        "c2_fallback_to_c1": c2_fallback,
        "c3_training_city_dates": bundle.c3_training_rows,
        "c3_training_dates_by_city": bundle.c3_training_dates_by_city,
        "c3_missing_city_date_feature_values": 0,
    }
    return full, scored, audit


def _exact_hotspot_set(group: pd.DataFrame) -> tuple[str, ...]:
    count = int(math.ceil(HOTSPOT_FRACTION * len(group)))
    ranked = group.sort_values(
        ["prediction_c", "tract_geoid"],
        ascending=[False, True],
        kind="stable",
    )
    return tuple(ranked.head(count)["tract_geoid"].astype(str))


def calculate_metrics(
    scored: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    date_rows: list[dict[str, Any]] = []
    for (model_id, city_id, target_date), group in scored.groupby(
        ["model_id", "city_id", "target_date"], observed=True, sort=True
    ):
        prediction = group["prediction_c"].to_numpy(float)
        observed = group["observed_lst_c"].to_numpy(float)
        error = prediction - observed
        absolute = np.abs(error)
        relative_error = np.abs(
            (prediction - np.median(prediction)) - (observed - np.median(observed))
        )
        date_rows.append(
            {
                "model_id": model_id,
                "city_id": city_id,
                "target_date": target_date,
                "rows": int(len(group)),
                "spatial_blocks": int(group["spatial_block"].nunique()),
                "absolute_mae_c": float(absolute.mean()),
                "signed_error_c": float(error.mean()),
                "p95_absolute_error_c": float(np.quantile(absolute, 0.95)),
                "fraction_absolute_error_gt_5c": float((absolute > 5.0).mean()),
                "fraction_absolute_error_gt_10c": float((absolute > 10.0).mean()),
                "relative_mae_c": float(relative_error.mean()),
            }
        )
    date_metrics = pd.DataFrame(date_rows)

    city_rows: list[dict[str, Any]] = []
    for (model_id, city_id), dates in date_metrics.groupby(
        ["model_id", "city_id"], observed=True, sort=True
    ):
        model_city_rows = scored.loc[
            scored["model_id"].eq(model_id) & scored["city_id"].eq(city_id)
        ]
        city_rows.append(
            {
                "model_id": model_id,
                "city_id": city_id,
                "dates": int(len(dates)),
                "rows": int(dates["rows"].sum()),
                "spatial_blocks": int(model_city_rows["spatial_block"].nunique()),
                "absolute_mae_c": float(dates["absolute_mae_c"].mean()),
                "signed_error_c": float(dates["signed_error_c"].mean()),
                "p95_absolute_error_c": float(dates["p95_absolute_error_c"].mean()),
                "fraction_absolute_error_gt_5c": float(
                    dates["fraction_absolute_error_gt_5c"].mean()
                ),
                "fraction_absolute_error_gt_10c": float(
                    dates["fraction_absolute_error_gt_10c"].mean()
                ),
                "relative_mae_c": float(dates["relative_mae_c"].mean()),
            }
        )
    city_metrics = pd.DataFrame(city_rows)

    overall_rows: list[dict[str, Any]] = []
    for model_id, cities in city_metrics.groupby("model_id", observed=True, sort=True):
        model_rows = scored.loc[scored["model_id"].eq(model_id)]
        overall_rows.append(
            {
                "model_id": model_id,
                "cities": int(len(cities)),
                "city_dates": int(cities["dates"].sum()),
                "rows": int(cities["rows"].sum()),
                "spatial_blocks": int(model_rows["spatial_block"].nunique()),
                "absolute_mae_c": float(cities["absolute_mae_c"].mean()),
                "signed_error_c": float(cities["signed_error_c"].mean()),
                "worst_city_absolute_mae_c": float(cities["absolute_mae_c"].max()),
                "worst_city_id": str(
                    cities.sort_values(
                        ["absolute_mae_c", "city_id"],
                        ascending=[False, True],
                        kind="stable",
                    ).iloc[0]["city_id"]
                ),
                "p95_absolute_error_c": float(cities["p95_absolute_error_c"].mean()),
                "fraction_absolute_error_gt_5c": float(
                    cities["fraction_absolute_error_gt_5c"].mean()
                ),
                "fraction_absolute_error_gt_10c": float(
                    cities["fraction_absolute_error_gt_10c"].mean()
                ),
                "relative_mae_c": float(cities["relative_mae_c"].mean()),
            }
        )
    return date_metrics, city_metrics, pd.DataFrame(overall_rows)


def select_candidate(
    city_metrics: pd.DataFrame,
    contract: dict[str, Any],
) -> dict[str, Any]:
    indexed = city_metrics.set_index(["model_id", "city_id"])
    b1 = city_metrics.loc[city_metrics["model_id"].eq("B1")]
    b1_overall = float(b1["absolute_mae_c"].mean())
    b1_worst = float(b1["absolute_mae_c"].max())
    records: list[dict[str, Any]] = []
    for candidate_id in CANDIDATE_IDS:
        rows = city_metrics.loc[city_metrics["model_id"].eq(candidate_id)]
        overall = float(rows["absolute_mae_c"].mean())
        worst = float(rows["absolute_mae_c"].max())
        records.append(
            {
                "candidate_id": candidate_id,
                "overall_absolute_mae_c": overall,
                "worst_city_absolute_mae_c": worst,
                "worst_city_id": str(
                    rows.sort_values(
                        ["absolute_mae_c", "city_id"],
                        ascending=[False, True],
                        kind="stable",
                    ).iloc[0]["city_id"]
                ),
                "overall_no_worse_than_b1": overall <= b1_overall,
                "worst_city_no_worse_than_b1": worst <= b1_worst,
                "eligible": overall <= b1_overall and worst <= b1_worst,
                "city_absolute_mae_c": {
                    city_id: float(indexed.loc[(candidate_id, city_id), "absolute_mae_c"])
                    for city_id in sorted(rows["city_id"].astype(str))
                },
            }
        )
    eligible = [record for record in records if record["eligible"]]
    if not eligible:
        selected = "B1"
    else:
        tolerance = float(contract["source_selection"]["tie_tolerance_c"])
        best_worst = min(record["worst_city_absolute_mae_c"] for record in eligible)
        shortlist = [
            record
            for record in eligible
            if record["worst_city_absolute_mae_c"] <= best_worst + tolerance
        ]
        best_overall = min(record["overall_absolute_mae_c"] for record in shortlist)
        shortlist = [
            record
            for record in shortlist
            if abs(record["overall_absolute_mae_c"] - best_overall) <= 1e-12
        ]
        simplicity = {
            candidate_id: index
            for index, candidate_id in enumerate(
                contract["source_selection"]["simplicity_order"]
            )
        }
        selected = min(shortlist, key=lambda row: simplicity[row["candidate_id"]])[
            "candidate_id"
        ]
    return {
        "baseline": {
            "overall_absolute_mae_c": b1_overall,
            "worst_city_absolute_mae_c": b1_worst,
        },
        "candidates": records,
        "selected_model_id": selected,
        "candidate_promoted": selected in CANDIDATE_IDS,
        "selection_interpretation": (
            "Worst-city values within 0.01 C enter the frozen tie band; exact minimum "
            "overall MAE then decides, with C1/C2/C3 simplicity used only for numerical "
            "equality of the secondary metric."
        ),
    }


def validate_complete_predictions(full: pd.DataFrame) -> dict[str, Any]:
    identity_error = np.abs(
        full["prediction_c"].to_numpy(float)
        - full["level_prediction_c"].to_numpy(float)
        - full["anomaly_prediction_c"].to_numpy(float)
    )
    medians = full.groupby(
        ["training_city_ids", "held_city_id", "model_id", "target_date"],
        observed=True,
    )["anomaly_prediction_c"].median()
    return {
        "rows": int(len(full)),
        "maximum_absolute_identity_error_c": float(identity_error.max()),
        "maximum_absolute_complete_support_anomaly_median_c": float(
            medians.abs().max()
        ),
        "passed": bool(identity_error.max() <= 1e-10 and medians.abs().max() <= 1e-10),
    }


def validate_relative_invariance(scored: pd.DataFrame) -> dict[str, Any]:
    comparison_models = ("M3", "C1", "C2", "C3")
    maximum_centered_difference = 0.0
    ranking_mismatches = 0
    hotspot_mismatches = 0
    dates_checked = 0
    for (_city_id, _target_date), group in scored.loc[
        scored["model_id"].isin(comparison_models)
    ].groupby(["city_id", "target_date"], observed=True, sort=True):
        by_model = {
            model_id: model.sort_values("tract_geoid", kind="stable").reset_index(drop=True)
            for model_id, model in group.groupby("model_id", observed=True)
        }
        reference = by_model["M3"]
        reference_centered = (
            reference["prediction_c"] - reference["prediction_c"].median()
        ).to_numpy(float)
        reference_rank = tuple(
            reference.sort_values(
                ["prediction_c", "tract_geoid"],
                ascending=[False, True],
                kind="stable",
            )["tract_geoid"].astype(str)
        )
        reference_hotspots = _exact_hotspot_set(reference)
        for model_id in comparison_models[1:]:
            model = by_model[model_id]
            centered = (model["prediction_c"] - model["prediction_c"].median()).to_numpy(
                float
            )
            maximum_centered_difference = max(
                maximum_centered_difference,
                float(np.max(np.abs(centered - reference_centered))),
            )
            rank = tuple(
                model.sort_values(
                    ["prediction_c", "tract_geoid"],
                    ascending=[False, True],
                    kind="stable",
                )["tract_geoid"].astype(str)
            )
            ranking_mismatches += int(rank != reference_rank)
            hotspot_mismatches += int(_exact_hotspot_set(model) != reference_hotspots)
        dates_checked += 1
    return {
        "comparison_models": list(comparison_models),
        "dates_checked": dates_checked,
        "maximum_absolute_centered_prediction_difference_c": maximum_centered_difference,
        "ranking_mismatches": ranking_mismatches,
        "hotspot_mismatches": hotspot_mismatches,
        "hotspot_rule": HOTSPOT_RULE,
        "passed": bool(
            maximum_centered_difference <= 1e-10
            and ranking_mismatches == 0
            and hotspot_mismatches == 0
        ),
    }


def _write_table(frame: pd.DataFrame, filename: str) -> dict[str, Any]:
    path = OUTPUT_DIR / filename
    frame.to_parquet(path, index=False)
    return file_record(path)


def run() -> dict[str, Any]:
    contract = load_contract()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(
        OUTPUT_DIR / "status.json",
        {"state": "running", "stage": "source_validation_only"},
    )
    data, input_records, _legacy_config = load_data(contract)
    source_cities = tuple(contract["scope"]["source_cities"])

    fit_cache: dict[tuple[str, ...], FitBundle] = {}
    c2_records: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    def get_bundle(cities: Iterable[str]) -> FitBundle:
        key = tuple(sorted(cities))
        if key not in fit_cache:
            print(f"[four-city-stage2] fit {','.join(key)}", flush=True)
            fit_cache[key] = fit_bundle(data, key)
        return fit_cache[key]

    fixed_full_parts: list[pd.DataFrame] = []
    fixed_scored_parts: list[pd.DataFrame] = []
    for held_city in source_cities:
        train_cities = tuple(city for city in source_cities if city != held_city)
        full, scored, audit = predict_bundle(data, get_bundle(train_cities), held_city)
        fixed_full_parts.append(full)
        fixed_scored_parts.append(scored)
        c2_records[(tuple(sorted(train_cities)), held_city)] = audit
    fixed_full = pd.concat(fixed_full_parts, ignore_index=True)
    fixed_scored = pd.concat(fixed_scored_parts, ignore_index=True)
    fixed_date, fixed_city, fixed_overall = calculate_metrics(fixed_scored)

    original_mae = float(
        fixed_overall.loc[fixed_overall["model_id"].eq("M3"), "absolute_mae_c"].iloc[0]
    )
    b1_mae = float(
        fixed_overall.loc[fixed_overall["model_id"].eq("B1"), "absolute_mae_c"].iloc[0]
    )
    if abs(original_mae - ORIGINAL_M3_EXPECTED_MAE_C) > REPRODUCTION_TOLERANCE_C:
        raise ValueError("The fixed-QA4K original M3 OOF baseline did not reproduce.")
    if abs(b1_mae - B1_EXPECTED_MAE_C) > REPRODUCTION_TOLERANCE_C:
        raise ValueError("The fixed-QA4K B1 OOF baseline did not reproduce.")

    final_selection = select_candidate(fixed_city, contract)
    complete_validation = validate_complete_predictions(fixed_full)
    relative_validation = validate_relative_invariance(fixed_scored)
    if not complete_validation["passed"] or not relative_validation["passed"]:
        raise ValueError("Level/anomaly or relative-invariance validation failed.")

    inner_date_parts: list[pd.DataFrame] = []
    inner_city_parts: list[pd.DataFrame] = []
    inner_overall_parts: list[pd.DataFrame] = []
    outer_selection_records: list[dict[str, Any]] = []
    nested_full_parts: list[pd.DataFrame] = []
    nested_scored_parts: list[pd.DataFrame] = []
    for outer_held in source_cities:
        outer_training = tuple(city for city in source_cities if city != outer_held)
        inner_scored_parts: list[pd.DataFrame] = []
        for inner_held in outer_training:
            inner_training = tuple(city for city in outer_training if city != inner_held)
            _full, scored, audit = predict_bundle(
                data, get_bundle(inner_training), inner_held
            )
            inner_scored_parts.append(scored)
            c2_records[(tuple(sorted(inner_training)), inner_held)] = audit
        inner_scored = pd.concat(inner_scored_parts, ignore_index=True)
        inner_date, inner_city, inner_overall = calculate_metrics(inner_scored)
        for table in (inner_date, inner_city, inner_overall):
            table.insert(0, "outer_held_city_id", outer_held)
        inner_date_parts.append(inner_date)
        inner_city_parts.append(inner_city)
        inner_overall_parts.append(inner_overall)
        selection = select_candidate(inner_city.drop(columns="outer_held_city_id"), contract)
        outer_selection_records.append(
            {
                "outer_held_city_id": outer_held,
                "inner_training_city_ids": list(outer_training),
                **selection,
            }
        )
        selected_id = selection["selected_model_id"]
        selected_full = fixed_full.loc[
            fixed_full["held_city_id"].eq(outer_held)
            & fixed_full["model_id"].eq(selected_id)
        ].copy()
        selected_scored = fixed_scored.loc[
            fixed_scored["held_city_id"].eq(outer_held)
            & fixed_scored["model_id"].eq(selected_id)
        ].copy()
        selected_full["selected_model_id"] = selected_id
        selected_scored["selected_model_id"] = selected_id
        selected_full["model_id"] = "NESTED"
        selected_scored["model_id"] = "NESTED"
        nested_full_parts.append(selected_full)
        nested_scored_parts.append(selected_scored)

    inner_date_metrics = pd.concat(inner_date_parts, ignore_index=True)
    inner_city_metrics = pd.concat(inner_city_parts, ignore_index=True)
    inner_overall_metrics = pd.concat(inner_overall_parts, ignore_index=True)
    nested_full = pd.concat(nested_full_parts, ignore_index=True)
    nested_scored = pd.concat(nested_scored_parts, ignore_index=True)
    nested_date, nested_city, nested_overall = calculate_metrics(nested_scored)

    c2_audit = list(c2_records.values())
    c3_training_audit = [
        {
            "training_city_ids": list(key),
            "training_city_dates": bundle.c3_training_rows,
            "training_dates_by_city": bundle.c3_training_dates_by_city,
            "missing_city_date_feature_values": 0,
        }
        for key, bundle in sorted(fit_cache.items())
    ]

    artifacts = {
        "fixed_oof_complete_predictions": _write_table(
            fixed_full, "fixed_oof_complete_predictions.parquet"
        ),
        "fixed_oof_scored_predictions": _write_table(
            fixed_scored, "fixed_oof_scored_predictions.parquet"
        ),
        "fixed_date_metrics": _write_table(fixed_date, "fixed_date_metrics.parquet"),
        "fixed_city_metrics": _write_table(fixed_city, "fixed_city_metrics.parquet"),
        "fixed_overall_metrics": _write_table(
            fixed_overall, "fixed_overall_metrics.parquet"
        ),
        "inner_date_metrics": _write_table(inner_date_metrics, "inner_date_metrics.parquet"),
        "inner_city_metrics": _write_table(inner_city_metrics, "inner_city_metrics.parquet"),
        "inner_overall_metrics": _write_table(
            inner_overall_metrics, "inner_overall_metrics.parquet"
        ),
        "nested_oof_complete_predictions": _write_table(
            nested_full, "nested_oof_complete_predictions.parquet"
        ),
        "nested_oof_scored_predictions": _write_table(
            nested_scored, "nested_oof_scored_predictions.parquet"
        ),
        "nested_date_metrics": _write_table(nested_date, "nested_date_metrics.parquet"),
        "nested_city_metrics": _write_table(nested_city, "nested_city_metrics.parquet"),
        "nested_overall_metrics": _write_table(
            nested_overall, "nested_overall_metrics.parquet"
        ),
    }

    input_hashes = [
        file_record(CONTRACT_PATH),
        file_record(LEGACY_CONFIG_PATH),
        file_record(PREDICTOR_COMPLETION_PATH),
        file_record(Path(__file__)),
        *[
            {
                "city_id": record["city_id"],
                "role": record["role"],
                "path": record["path"],
                "sha256": record["sha256"],
            }
            for record in input_records
        ],
    ]
    run_signature = hashlib.sha256(
        json.dumps(input_hashes, sort_keys=True).encode("utf-8")
    ).hexdigest()
    summary = {
        "schema_version": 1,
        "state": "complete",
        "stage": "four_city_absolute_error_stage_2_source_validation",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_signature": run_signature,
        "scope": {
            "source_city_ids": list(source_cities),
            "opened_city_values_read": False,
            "la_2025_read": False,
            "network_or_download_used": False,
            "predictor_build_performed": False,
            "default_model_changed": False,
        },
        "input_hashes": input_hashes,
        "artifacts": artifacts,
        "fixed_candidate_overall_metrics": fixed_overall.to_dict("records"),
        "fixed_candidate_city_metrics": fixed_city.to_dict("records"),
        "final_source_selection": final_selection,
        "outer_selections": outer_selection_records,
        "nested_selection_overall_metrics": nested_overall.to_dict("records"),
        "nested_selection_city_metrics": nested_city.to_dict("records"),
        "c2_gate_audit": c2_audit,
        "c3_training_audit": c3_training_audit,
        "complete_prediction_validation": complete_validation,
        "relative_invariance_validation": relative_validation,
        "baseline_reproduction": {
            "original_m3_expected_mae_c": ORIGINAL_M3_EXPECTED_MAE_C,
            "original_m3_observed_mae_c": original_mae,
            "b1_expected_mae_c": B1_EXPECTED_MAE_C,
            "b1_observed_mae_c": b1_mae,
            "tolerance_c": REPRODUCTION_TOLERANCE_C,
            "passed": True,
        },
        "scientific_role": "reused-source development evidence, not independent confirmation",
        "next_action": (
            "eligible_for_separately_authorized_opened_city_historical_stress_test"
            if final_selection["candidate_promoted"]
            else "stop_no_source_candidate_passed"
        ),
    }
    summary_path = OUTPUT_DIR / "summary.json"
    write_json(summary_path, summary)
    write_json(
        OUTPUT_DIR / "status.json",
        {
            "state": "complete",
            "run_signature": run_signature,
            "selected_model_id": final_selection["selected_model_id"],
            "candidate_promoted": final_selection["candidate_promoted"],
            "summary_sha256": sha256(summary_path),
        },
    )
    return summary


def main() -> None:
    try:
        summary = run()
    except Exception as error:
        write_json(
            OUTPUT_DIR / "status.json",
            {
                "state": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise
    print(
        json.dumps(
            {
                "state": summary["state"],
                "run_signature": summary["run_signature"],
                "final_source_selection": summary["final_source_selection"],
                "nested_selection_overall_metrics": summary[
                    "nested_selection_overall_metrics"
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
