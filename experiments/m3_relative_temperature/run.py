"""Compare fixed M3 and B1 on within-date neighborhood heat using existing data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("experiment.toml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_existing_predictions(root: Path, config: dict[str, Any]) -> pd.DataFrame:
    source_path = root / config["inputs"]["source_predictions"]
    opened_path = root / config["inputs"]["opened_scored_rows"]
    source = pd.read_parquet(source_path)
    source = source.loc[
        source["model_id"].isin(["B1", config["experiment"]["source_m3_variant"]])
    ].copy()
    source["model_id"] = source["model_id"].replace(
        {config["experiment"]["source_m3_variant"]: "M3"}
    )
    source = source.rename(columns={"prediction_c": "predicted_lst_c"})
    source["evidence_role"] = "source_whole_city_loso"

    opened = pd.read_parquet(opened_path)
    opened = pd.concat(
        [
            opened.assign(model_id="B1", predicted_lst_c=opened["b1_prediction_c"]),
            opened.assign(model_id="M3", predicted_lst_c=opened["m3_prediction_c"]),
        ],
        ignore_index=True,
    )
    opened = opened.rename(columns={"target_lst_c": "observed_lst_c"})
    opened["evidence_role"] = "opened_historical_stress"
    columns = [
        "evidence_role",
        "model_id",
        "city_id",
        "tract_geoid",
        "target_date",
        "predicted_lst_c",
        "observed_lst_c",
    ]
    combined = pd.concat([source.loc[:, columns], opened.loc[:, columns]], ignore_index=True)
    combined["target_date"] = pd.to_datetime(combined["target_date"]).dt.strftime("%Y-%m-%d")
    return combined.sort_values(columns[:5], kind="stable").reset_index(drop=True)


def ranked_hotspot_metrics(
    geoids: pd.Series,
    actual: np.ndarray,
    predicted: np.ndarray,
    fraction: float,
) -> tuple[float, float]:
    count = len(actual)
    k = max(1, math.ceil(fraction * count))
    identifiers = geoids.astype(str).to_numpy()
    actual_order = np.lexsort((identifiers, -actual))
    predicted_order = np.lexsort((identifiers, -predicted))
    actual_hot = set(actual_order[:k].tolist())
    predicted_hot = set(predicted_order[:k].tolist())
    recall = len(actual_hot & predicted_hot) / k
    positives_seen = 0
    precision_sum = 0.0
    for rank, position in enumerate(predicted_order, start=1):
        if int(position) in actual_hot:
            positives_seen += 1
            precision_sum += positives_seen / rank
    return precision_sum / k, recall


def calculate_date_metrics(rows: pd.DataFrame, hotspot_fraction: float) -> pd.DataFrame:
    metrics: list[dict[str, Any]] = []
    groups = rows.groupby(
        ["evidence_role", "model_id", "city_id", "target_date"],
        observed=True,
        sort=True,
    )
    for (role, model_id, city_id, target_date), frame in groups:
        observed = frame["observed_lst_c"].to_numpy(dtype=float)
        predicted = frame["predicted_lst_c"].to_numpy(dtype=float)
        observed_anomaly = observed - np.median(observed)
        predicted_anomaly = predicted - np.median(predicted)
        average_precision, recall = ranked_hotspot_metrics(
            frame["tract_geoid"], observed, predicted, hotspot_fraction
        )
        spearman = pd.Series(predicted).corr(pd.Series(observed), method="spearman")
        metrics.append(
            {
                "evidence_role": role,
                "model_id": model_id,
                "city_id": city_id,
                "target_date": target_date,
                "rows": len(frame),
                "anomaly_mae_c": float(np.mean(np.abs(predicted_anomaly - observed_anomaly))),
                "spearman": float(spearman),
                "hotspot_average_precision": average_precision,
                "hotspot_recall": recall,
            }
        )
    return pd.DataFrame(metrics)


def aggregate_metrics(dates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    measures = {
        "dates": ("target_date", "nunique"),
        "rows": ("rows", "sum"),
        "anomaly_mae_c": ("anomaly_mae_c", "mean"),
        "median_spearman": ("spearman", "median"),
        "hotspot_average_precision": ("hotspot_average_precision", "mean"),
        "hotspot_recall": ("hotspot_recall", "mean"),
    }
    city = (
        dates.groupby(["evidence_role", "model_id", "city_id"], observed=True, sort=True)
        .agg(**measures)
        .reset_index()
    )
    role = (
        city.groupby(["evidence_role", "model_id"], observed=True, sort=True)
        .agg(
            cities=("city_id", "nunique"),
            city_dates=("dates", "sum"),
            rows=("rows", "sum"),
            anomaly_mae_c=("anomaly_mae_c", "mean"),
            median_city_spearman=("median_spearman", "median"),
            hotspot_average_precision=("hotspot_average_precision", "mean"),
            hotspot_recall=("hotspot_recall", "mean"),
        )
        .reset_index()
    )
    return city, role


def paired_hierarchical_bootstrap(
    dates: pd.DataFrame,
    *,
    iterations: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    results: dict[str, dict[str, float]] = {}
    for role, role_frame in dates.groupby("evidence_role", observed=True, sort=True):
        paired = role_frame.pivot(
            index=["city_id", "target_date"], columns="model_id", values="anomaly_mae_c"
        ).dropna()
        city_values = {
            city: frame["B1"].to_numpy() - frame["M3"].to_numpy()
            for city, frame in paired.groupby(level="city_id", observed=True)
        }
        cities = tuple(sorted(city_values))
        estimates = np.empty(iterations, dtype=float)
        for index in range(iterations):
            sampled_cities = rng.choice(cities, size=len(cities), replace=True)
            city_means = []
            for city in sampled_cities:
                values = city_values[str(city)]
                city_means.append(float(rng.choice(values, size=len(values), replace=True).mean()))
            estimates[index] = float(np.mean(city_means))
        observed = float(np.mean([values.mean() for values in city_values.values()]))
        results[str(role)] = {
            "b1_minus_m3_anomaly_mae_c": observed,
            "bootstrap_95_ci_lower_c": float(np.quantile(estimates, 0.025)),
            "bootstrap_95_ci_upper_c": float(np.quantile(estimates, 0.975)),
        }
    return results


def evaluate_gate(
    config: dict[str, Any], city: pd.DataFrame, role: pd.DataFrame
) -> dict[str, Any]:
    threshold = config["development_gate"]
    role_index = role.set_index(["evidence_role", "model_id"])
    city_index = city.set_index(["evidence_role", "model_id", "city_id"])
    role_results: dict[str, Any] = {}
    all_city_deltas: dict[str, float] = {}
    for evidence_role in sorted(role["evidence_role"].unique()):
        b1 = role_index.loc[(evidence_role, "B1")]
        m3 = role_index.loc[(evidence_role, "M3")]
        improvement = (float(b1["anomaly_mae_c"]) - float(m3["anomaly_mae_c"])) / float(
            b1["anomaly_mae_c"]
        )
        spearman_change = float(m3["median_city_spearman"] - b1["median_city_spearman"])
        role_results[evidence_role] = {
            "relative_anomaly_mae_improvement": improvement,
            "median_city_spearman_change": spearman_change,
            "improvement_gate": improvement
            >= float(threshold["minimum_relative_anomaly_mae_improvement"]),
            "spearman_gate": spearman_change > 0
            if threshold["require_positive_spearman_change_in_each_role"]
            else True,
        }
        cities = city.loc[city["evidence_role"].eq(evidence_role), "city_id"].unique()
        for city_id in cities:
            all_city_deltas[str(city_id)] = float(
                city_index.loc[(evidence_role, "M3", city_id), "anomaly_mae_c"]
                - city_index.loc[(evidence_role, "B1", city_id), "anomaly_mae_c"]
            )
    city_gate = max(all_city_deltas.values()) <= float(
        threshold["maximum_city_anomaly_mae_degradation_c"]
    )
    passed = city_gate and all(
        result[gate]
        for result in role_results.values()
        for gate in ("improvement_gate", "spearman_gate")
    )
    return {
        "role_results": role_results,
        "city_m3_minus_b1_anomaly_mae_c": all_city_deltas,
        "maximum_city_degradation_gate": city_gate,
        "passed_for_focused_existing_data_development": passed,
        "next_action": (
            "define_one_fixed_relative_temperature_model_and_applicability_contract"
            if passed
            else "stop_m3_relative_temperature_route"
        ),
    }


def run(root: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    rows = load_existing_predictions(root, config)
    dates = calculate_date_metrics(rows, float(config["experiment"]["hotspot_fraction"]))
    city, role = aggregate_metrics(dates)
    bootstrap = paired_hierarchical_bootstrap(
        dates,
        iterations=int(config["experiment"]["bootstrap_iterations"]),
        seed=int(config["experiment"]["bootstrap_seed"]),
    )
    gate = evaluate_gate(config, city, role)
    output = root / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)
    dates.to_parquet(output / "date_metrics.parquet", index=False)
    city.to_parquet(output / "city_metrics.parquet", index=False)
    role.to_parquet(output / "role_metrics.parquet", index=False)
    summary = {
        "schema_version": 1,
        "experiment": config["experiment"],
        "completed_at": datetime.now(UTC).isoformat(),
        "no_refit_or_network_access": True,
        "inputs": [
            {
                "path": config["inputs"][name],
                "sha256": _sha256(root / config["inputs"][name]),
            }
            for name in ("source_predictions", "opened_scored_rows")
        ],
        "role_metrics": role.to_dict("records"),
        "paired_hierarchical_bootstrap": bootstrap,
        "development_gate": gate,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    print(json.dumps(run(args.project_root.resolve(), args.config.resolve()), indent=2))


if __name__ == "__main__":
    main()
