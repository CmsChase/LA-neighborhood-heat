"""Fixed original-four versus plus-Colorado M3 source-development experiment."""

from __future__ import annotations

import argparse
import json
import math
import pickle
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from la_heat.multicity.m3_development import (
    KEY_COLUMNS,
    M2_FEATURES,
    M3_CANDIDATES,
    fit_m3_candidate,
    predict_m3,
)
from la_heat.provenance import atomic_json, atomic_parquet, sha256_file

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "experiments/colorado_source_addition/fixed_contract.toml"
ACTIVE = ROOT / "manifests/multicity/ACTIVE_STAGE.json"
OUTPUT = ROOT / "exports/COLORADO_SOURCE_ADDITION_FIXED_V1"
ORIGINAL_CITIES = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
COLORADO = "colorado_springs_co"
BLOCKED_DATE = "2021-10-18"
CONTEXT = "city_centroid_latitude_deg"
BOOTSTRAP_SEED = 20260921


def _source_date_filters(city_id: str, *, parquet_timestamp: bool) -> list[tuple] | None:
    """Keep the LA 2025 lock without altering the authenticated Phoenix cohort."""
    if city_id != "los_angeles_ca":
        return None
    cutoff = pd.Timestamp("2025-01-01") if parquet_timestamp else "2025-01-01"
    return [("target_date", "<", cutoff)]


def _contract() -> dict:
    with CONTRACT.open("rb") as stream:
        return tomllib.load(stream)


def authorize() -> dict:
    contract = _contract()
    for key in (
        "original_authorization",
        "colorado_predictor_acceptance",
        "colorado_predictor_table",
        "colorado_target_summary",
    ):
        path = ROOT / contract["inputs"][key]
        expected = contract["inputs"][f"{key}_sha256"]
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"Frozen input drifted before authorization: {key}")
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    if stage["state"] not in {
        "complete_colorado_official_predictor_inputs_46_dates",
        "running_colorado_source_addition_fixed_v1",
    }:
        raise RuntimeError(f"Cannot authorize from active state: {stage['state']}")
    stage["revision"] = "2026-09-colorado-source-addition-fixed-v1-running"
    stage["state"] = "running_colorado_source_addition_fixed_v1"
    stage["next_safe_stage"] = "complete_fixed_comparison_then_close_permissions"
    stage["summary"] = (
        "Fixed original-four versus plus-Colorado source-development comparison is running; "
        "LA 2025 and external-city targets remain closed."
    )
    for name in stage["permissions"]:
        stage["permissions"][name] = False
    stage["permissions"]["read_source_targets"] = True
    stage["permissions"]["fit_model"] = True
    stage["permissions"]["score_model"] = True
    stage["colorado_source_addition_fixed_v1"] = {
        "contract": str(CONTRACT.relative_to(ROOT)).replace("\\", "/"),
        "contract_sha256": sha256_file(CONTRACT),
        "candidate_count": 1,
        "qa_id": "4k",
        "original_holdout_city_count": 4,
        "la_2025_access_allowed": False,
        "external_target_access_allowed": False,
        "network_allowed": False,
    }
    atomic_json(stage, ACTIVE)
    return stage["colorado_source_addition_fixed_v1"]


def _preflight() -> dict:
    contract = _contract()
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    scope = stage.get("colorado_source_addition_fixed_v1", {})
    if (
        stage["state"] != "running_colorado_source_addition_fixed_v1"
        or scope.get("contract_sha256") != sha256_file(CONTRACT)
        or not stage["permissions"].get("read_source_targets")
        or not stage["permissions"].get("fit_model")
        or not stage["permissions"].get("score_model")
        or stage["permissions"].get("read_external_targets")
    ):
        raise RuntimeError("Fixed Colorado source-addition authorization is not active")
    if contract["cohorts"]["la_2025_access"] is not False:
        raise RuntimeError("LA 2025 must remain inaccessible")
    return contract


def _load_original_inputs(contract: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    authorization_path = ROOT / contract["inputs"]["original_authorization"]
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    predictor_frames = []
    for record in authorization["source_predictor_tables"]:
        path = ROOT / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"Original predictor drifted: {record['city_id']}")
        # The scientific lock is specifically LA 2025.  Phoenix's authenticated
        # source-development dates are in 2025 and were part of the original
        # four-city model, so filtering the whole cohort by calendar year would
        # silently remove that city from the fixed comparison.
        filters = _source_date_filters(record["city_id"], parquet_timestamp=True)
        frame = pd.read_parquet(path, filters=filters)
        predictor_frames.append(frame[[*KEY_COLUMNS, *M2_FEATURES]])
    target_frames = []
    for record in authorization["source_qa_target_tables"]:
        if record["qa_id"] != "4k":
            continue
        path = ROOT / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"Original QA4k target drifted: {record['city_id']}")
        filters = _source_date_filters(record["city_id"], parquet_timestamp=False)
        frame = pd.read_parquet(
            path,
            columns=[*KEY_COLUMNS, "date_usable", "target_available", "target_lst_c"],
            filters=filters,
        )
        target_frames.append(frame)
    context = {
        str(row["city_id"]): float(row[CONTEXT])
        for row in authorization["source_city_context"]
    }
    return (
        pd.concat(predictor_frames, ignore_index=True),
        pd.concat(target_frames, ignore_index=True),
        context,
    )


def _load_colorado_inputs(contract: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictor_path = ROOT / contract["inputs"]["colorado_predictor_table"]
    predictors = pd.read_parquet(predictor_path)
    predictors["target_date"] = pd.to_datetime(predictors["target_date"])
    predictors = predictors.loc[
        predictors["input_status"].eq("accepted_46_features"),
        [*KEY_COLUMNS, *M2_FEATURES],
    ].reset_index(drop=True)
    if len(predictors) != 5060 or predictors[list(M2_FEATURES)].isna().any(axis=None):
        raise RuntimeError("Colorado accepted predictor table drifted")
    rows = []
    dates = sorted(predictors.target_date.dt.strftime("%Y-%m-%d").unique())
    if len(dates) != 46 or BLOCKED_DATE in dates:
        raise RuntimeError("Colorado accepted date partition drifted")
    target_root = (
        ROOT
        / "exports/SOURCE_CITY_TARGET_AVAILABILITY/official_2020/colorado_springs_co/dates"
    )
    for day in dates:
        path = target_root / f"{day}_source_development_labels.csv"
        frame = pd.read_csv(
            path,
            usecols=[
                "tract_geoid",
                "target_available_source_development",
                "target_lst_c_source_development",
            ],
            dtype={"tract_geoid": str},
        )
        frame.insert(0, "city_id", COLORADO)
        frame["target_date"] = day
        frame["date_usable"] = True
        frame = frame.rename(
            columns={
                "target_available_source_development": "target_available",
                "target_lst_c_source_development": "target_lst_c",
            }
        )
        rows.append(frame[[*KEY_COLUMNS, "date_usable", "target_available", "target_lst_c"]])
    targets = pd.concat(rows, ignore_index=True)
    return predictors, targets


def _normalize(predictors: pd.DataFrame, targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    x = predictors.copy()
    y_frame = targets.copy()
    for frame in (x, y_frame):
        frame["city_id"] = frame["city_id"].astype(str)
        frame["tract_geoid"] = frame["tract_geoid"].astype(str)
        frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.strftime("%Y-%m-%d")
    merged = x.merge(
        y_frame[[*KEY_COLUMNS, "date_usable", "target_available", "target_lst_c"]],
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
    )
    numeric = pd.to_numeric(merged["target_lst_c"], errors="coerce")
    keep = (
        merged["date_usable"].fillna(False).astype(bool)
        & merged["target_available"].fillna(False).astype(bool)
        & np.isfinite(numeric)
    )
    selected = merged.loc[keep, [*KEY_COLUMNS, *M2_FEATURES, CONTEXT]].reset_index(drop=True)
    target = numeric.loc[keep].astype(float).reset_index(drop=True)
    if selected.empty or selected.duplicated(list(KEY_COLUMNS)).any():
        raise RuntimeError("A source cohort has no unique finite scoring rows")
    return selected, target


def _top20_recall(frame: pd.DataFrame) -> float:
    values = []
    for _, group in frame.groupby(["city_id", "target_date"], sort=True, observed=True):
        count = max(1, math.ceil(0.2 * len(group)))
        observed = set(
            group.sort_values(
                ["observed_lst_c", "tract_geoid"], ascending=[False, True], kind="stable"
            ).head(count).tract_geoid
        )
        predicted = set(
            group.sort_values(
                ["prediction_c", "tract_geoid"], ascending=[False, True], kind="stable"
            ).head(count).tract_geoid
        )
        values.append(len(observed & predicted) / count)
    return float(np.mean(values))


def _summarize(frame: pd.DataFrame) -> dict:
    work = frame.copy()
    work["error_c"] = work["prediction_c"] - work["observed_lst_c"]
    work["absolute_error_c"] = work.error_c.abs()
    work["observed_relative_c"] = work.observed_lst_c - work.groupby(
        ["city_id", "target_date"], observed=True
    ).observed_lst_c.transform("median")
    work["relative_absolute_error_c"] = (
        work.predicted_relative_c - work.observed_relative_c
    ).abs()
    date_metrics = work.groupby(["city_id", "target_date"], observed=True).agg(
        mae_c=("absolute_error_c", "mean"),
        bias_c=("error_c", "mean"),
        relative_mae_c=("relative_absolute_error_c", "mean"),
    )
    city_metrics = date_metrics.groupby(level="city_id").mean()
    return {
        "equal_city_equal_date_mae_c": float(city_metrics.mae_c.mean()),
        "equal_city_equal_date_bias_c": float(city_metrics.bias_c.mean()),
        "equal_city_equal_date_relative_mae_c": float(city_metrics.relative_mae_c.mean()),
        "worst_city_mae_c": float(city_metrics.mae_c.max()),
        "p95_absolute_error_c": float(work.absolute_error_c.quantile(0.95)),
        "fraction_absolute_error_over_5c": float((work.absolute_error_c > 5).mean()),
        "fraction_absolute_error_over_10c": float((work.absolute_error_c > 10).mean()),
        "top20_hotspot_recall": _top20_recall(work),
        "scored_rows": len(work),
        "independent_dates": int(work[["city_id", "target_date"]].drop_duplicates().shape[0]),
        "cities": int(work.city_id.nunique()),
    }


def _paired_bootstrap(predictions: pd.DataFrame, replicates: int = 2000) -> list[float]:
    date_scores = (
        predictions.assign(
            absolute_error_c=lambda x: (x.prediction_c - x.observed_lst_c).abs()
        )
        .groupby(["variant", "city_id", "target_date"], observed=True)
        .absolute_error_c.mean()
        .unstack("variant")
        .reset_index()
    )
    date_scores["difference_c"] = date_scores["expanded"] - date_scores["baseline"]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    cities = np.asarray(ORIGINAL_CITIES)
    draws = []
    for _ in range(replicates):
        sampled_cities = rng.choice(cities, size=len(cities), replace=True)
        city_values = []
        for city in sampled_cities:
            values = date_scores.loc[date_scores.city_id.eq(city), "difference_c"].to_numpy()
            city_values.append(float(rng.choice(values, size=len(values), replace=True).mean()))
        draws.append(float(np.mean(city_values)))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def run() -> dict:
    contract = _preflight()
    original_x, original_targets, context = _load_original_inputs(contract)
    colorado_x, colorado_targets = _load_colorado_inputs(contract)
    context[COLORADO] = float(contract["inputs"]["colorado_city_centroid_latitude_deg"])
    original_x[CONTEXT] = original_x.city_id.map(context)
    colorado_x[CONTEXT] = context[COLORADO]
    original, original_y = _normalize(original_x, original_targets)
    colorado, colorado_y = _normalize(colorado_x, colorado_targets)
    candidate = next(
        item for item in M3_CANDIDATES if item.candidate_id == contract["model"]["candidate_id"]
    )
    outputs = []
    fold_records = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for index, held_city in enumerate(ORIGINAL_CITIES, start=1):
        train_mask = original.city_id.ne(held_city)
        held = original.loc[~train_mask].reset_index(drop=True)
        observed = original_y.loc[~train_mask].reset_index(drop=True)
        baseline_x = original.loc[train_mask].reset_index(drop=True)
        baseline_y = original_y.loc[train_mask].reset_index(drop=True)
        expanded_x = pd.concat([baseline_x, colorado], ignore_index=True)
        expanded_y = pd.concat([baseline_y, colorado_y], ignore_index=True)
        for variant, train_x, train_y in (
            ("baseline", baseline_x, baseline_y),
            ("expanded", expanded_x, expanded_y),
        ):
            fitted = fit_m3_candidate(train_x, train_y, candidate)
            predicted = predict_m3(fitted, held)
            predicted = predicted.rename(
                columns={
                    "m3_prediction_c": "prediction_c",
                    "m3_anomaly_prediction_c": "predicted_relative_c",
                }
            )
            predicted["observed_lst_c"] = observed.to_numpy(dtype=float)
            predicted["variant"] = variant
            predicted["held_city_id"] = held_city
            outputs.append(predicted)
            fold_records.append(
                {
                    "held_city_id": held_city,
                    "variant": variant,
                    "training_cities": sorted(train_x.city_id.unique()),
                    "training_rows": len(train_x),
                    **_summarize(predicted),
                }
            )
        atomic_json(
            {"state": "running", "completed_folds": index, "total_folds": 4},
            OUTPUT / "status.json",
        )
        print(f"COLORADO_SOURCE_ADDITION_FOLD {index}/4 {held_city}", flush=True)
    predictions = pd.concat(outputs, ignore_index=True)
    metrics = {variant: _summarize(group) for variant, group in predictions.groupby("variant")}
    baseline = metrics["baseline"]["equal_city_equal_date_mae_c"]
    expanded = metrics["expanded"]["equal_city_equal_date_mae_c"]
    relative_improvement = (baseline - expanded) / baseline
    folds = pd.DataFrame(fold_records)
    paired = folds.pivot(
        index="held_city_id",
        columns="variant",
        values="equal_city_equal_date_mae_c",
    )
    paired["expanded_minus_baseline_c"] = paired.expanded - paired.baseline
    gate_primary = relative_improvement >= float(
        contract["decision"]["minimum_primary_relative_improvement"]
    )
    gate_city = paired.expanded_minus_baseline_c.max() <= float(
        contract["decision"]["maximum_allowed_any_city_mae_degradation_c"]
    )
    # Colorado is diagnostic only: train the unchanged model on all original cities.
    colorado_model = fit_m3_candidate(original, original_y, candidate)
    colorado_predicted = predict_m3(colorado_model, colorado).rename(
        columns={
            "m3_prediction_c": "prediction_c",
            "m3_anomaly_prediction_c": "predicted_relative_c",
        }
    )
    colorado_predicted["observed_lst_c"] = colorado_y.to_numpy(dtype=float)
    colorado_diagnostic = _summarize(colorado_predicted)
    baseline_final = fit_m3_candidate(original, original_y, candidate)
    expanded_final = fit_m3_candidate(
        pd.concat([original, colorado], ignore_index=True),
        pd.concat([original_y, colorado_y], ignore_index=True),
        candidate,
    )
    atomic_parquet(predictions, OUTPUT / "paired_outer_predictions.parquet")
    atomic_parquet(folds, OUTPUT / "fold_metrics.parquet")
    atomic_parquet(paired.reset_index(), OUTPUT / "paired_city_metrics.parquet")
    (OUTPUT / "baseline_four_source_model.pkl").write_bytes(
        pickle.dumps(baseline_final, protocol=pickle.HIGHEST_PROTOCOL)
    )
    (OUTPUT / "expanded_five_source_model.pkl").write_bytes(
        pickle.dumps(expanded_final, protocol=pickle.HIGHEST_PROTOCOL)
    )
    result = {
        "state": "complete",
        "decision": "expanded_candidate_passed" if gate_primary and gate_city else "no_upgrade",
        "contract_sha256": sha256_file(CONTRACT),
        "candidate_id": candidate.candidate_id,
        "qa_id": "4k",
        "metrics": metrics,
        "primary_relative_improvement": float(relative_improvement),
        "paired_expanded_minus_baseline_mae_ci95_c": _paired_bootstrap(predictions),
        "maximum_city_mae_degradation_c": float(paired.expanded_minus_baseline_c.max()),
        "primary_five_percent_gate_passed": bool(gate_primary),
        "per_city_degradation_gate_passed": bool(gate_city),
        "colorado_held_city_development_diagnostic": colorado_diagnostic,
        "colorado_training_rows": len(colorado),
        "colorado_training_dates": int(colorado.target_date.nunique()),
        "la_2025_accessed": False,
        "external_four_city_targets_accessed": False,
        "network_requests": 0,
        "model_files": {
            name: sha256_file(OUTPUT / name)
            for name in ("baseline_four_source_model.pkl", "expanded_five_source_model.pkl")
        },
    }
    atomic_json(result, OUTPUT / "RESULTS.json")
    atomic_json({"state": "complete", "decision": result["decision"]}, OUTPUT / "status.json")
    stage = json.loads(ACTIVE.read_text(encoding="utf-8"))
    for name in stage["permissions"]:
        stage["permissions"][name] = False
    stage["revision"] = "2026-09-colorado-source-addition-fixed-v1-complete"
    stage["state"] = "complete_colorado_source_addition_fixed_v1"
    stage["next_safe_stage"] = "review_fixed_comparison_before_any_new_model_work"
    stage["summary"] = (
        f"Fixed original-four versus plus-Colorado comparison completed with decision "
        f"{result['decision']}; all temporary target/model permissions are closed."
    )
    stage["colorado_source_addition_fixed_v1"].update(
        {
            "state": "complete",
            "decision": result["decision"],
            "results": str((OUTPUT / "RESULTS.json").relative_to(ROOT)).replace("\\", "/"),
            "results_sha256": sha256_file(OUTPUT / "RESULTS.json"),
        }
    )
    atomic_json(stage, ACTIVE)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("authorize", "run"))
    args = parser.parse_args()
    result = authorize() if args.mode == "authorize" else run()
    print("COLORADO_SOURCE_ADDITION_FIXED", result.get("state", "authorized"), flush=True)


if __name__ == "__main__":
    main()
