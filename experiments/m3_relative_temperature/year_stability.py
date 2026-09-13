"""Run the fixed M3-relative source leave-one-year-out stability audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    B1_FEATURES,
    KEY_COLUMNS,
    M3_CANDIDATES,
    build_b1_estimator,
    build_m3_estimators,
    city_date_row_weights,
)

CONTRACT_PATH = Path(__file__).with_name("fixed_contract.toml")
SOURCE_RUNNER_PATH = ROOT / "experiments" / "m3_2x2" / "run.py"
METRICS_RUNNER_PATH = Path(__file__).with_name("run.py")


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_contract() -> dict[str, Any]:
    with CONTRACT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def fold_ids(frame: pd.DataFrame) -> list[tuple[str, int]]:
    dates = pd.to_datetime(frame["target_date"], errors="raise")
    pairs = pd.DataFrame(
        {"city_id": frame["city_id"].astype(str), "year": dates.dt.year.astype(int)}
    ).drop_duplicates()
    return [
        (str(row.city_id), int(row.year))
        for row in pairs.sort_values(["city_id", "year"]).itertuples(index=False)
    ]


def _fit_fold(
    data: Any,
    city_id: str,
    year: int,
) -> pd.DataFrame:
    scored_year = pd.to_datetime(data.scored_frame["target_date"]).dt.year
    held_mask = data.scored_frame["city_id"].eq(city_id) & scored_year.eq(year)
    training = data.scored_frame.loc[~held_mask].reset_index(drop=True)
    training_target = data.target.loc[~held_mask].reset_index(drop=True)
    held = data.scored_frame.loc[held_mask].reset_index(drop=True)
    held_target = data.target.loc[held_mask].reset_index(drop=True)
    if held.empty or training.empty:
        raise ValueError(f"Empty train/test split for {city_id} {year}")

    universe_year = pd.to_datetime(data.prediction_universe["target_date"]).dt.year
    held_universe = data.prediction_universe.loc[
        data.prediction_universe["city_id"].eq(city_id) & universe_year.eq(year)
    ].reset_index(drop=True)

    weights = city_date_row_weights(training)
    b1 = build_b1_estimator()
    b1.fit(
        training.loc[:, B1_FEATURES],
        training_target,
        model__sample_weight=weights,
    )

    anomaly = training_target - training_target.groupby(
        [training["city_id"], training["target_date"]], observed=True
    ).transform("median")
    anomaly_model = build_m3_estimators(M3_CANDIDATES[-1])[1]
    anomaly_model.fit(
        training.loc[:, ANOMALY_FEATURES],
        anomaly,
        model__sample_weight=weights,
    )
    raw = pd.Series(
        anomaly_model.predict(held_universe.loc[:, ANOMALY_FEATURES]),
        index=held_universe.index,
        dtype=float,
    )
    centered = raw - raw.groupby(
        [held_universe["city_id"], held_universe["target_date"]], observed=True
    ).transform("median")
    m3 = held_universe.loc[:, KEY_COLUMNS].copy()
    m3["predicted_lst_c"] = centered.to_numpy(dtype=float)

    observed = held.loc[:, KEY_COLUMNS].copy()
    observed["observed_lst_c"] = held_target.to_numpy(dtype=float)
    m3 = m3.merge(observed, on=list(KEY_COLUMNS), how="inner", validate="one_to_one")
    m3.insert(0, "model_id", "M3")

    b1_rows = observed.copy()
    b1_rows.insert(
        len(KEY_COLUMNS),
        "predicted_lst_c",
        b1.predict(held.loc[:, B1_FEATURES]),
    )
    b1_rows.insert(0, "model_id", "B1")
    result = pd.concat([b1_rows, m3], ignore_index=True)
    result.insert(0, "evidence_role", "source_leave_one_year_out")
    result.insert(3, "held_year", int(year))
    return result


def _evaluate_gate(
    role_metrics: pd.DataFrame,
    city_metrics: pd.DataFrame,
    bootstrap: dict[str, float],
    contract: dict[str, Any],
) -> dict[str, Any]:
    gate = contract["development_gate"]
    indexed = role_metrics.set_index("model_id")
    b1 = indexed.loc["B1"]
    m3 = indexed.loc["M3"]
    improvement = (float(b1["anomaly_mae_c"]) - float(m3["anomaly_mae_c"])) / float(
        b1["anomaly_mae_c"]
    )
    city_pivot = city_metrics.pivot(index="city_id", columns="model_id", values="anomaly_mae_c")
    city_delta = city_pivot["M3"] - city_pivot["B1"]
    spearman_change = float(m3["median_city_spearman"] - b1["median_city_spearman"])
    checks = {
        "aggregate_improvement": improvement
        >= float(gate["minimum_aggregate_relative_anomaly_mae_improvement"]),
        "maximum_city_degradation": float(city_delta.max())
        <= float(gate["maximum_any_city_anomaly_mae_degradation_c"]),
        "positive_aggregate_spearman_change": spearman_change > 0,
        "positive_bootstrap_lower_bound": float(bootstrap["bootstrap_95_ci_lower_c"]) > 0,
    }
    return {
        "relative_anomaly_mae_improvement": improvement,
        "median_city_spearman_change": spearman_change,
        "city_m3_minus_b1_anomaly_mae_c": {
            str(key): float(value) for key, value in city_delta.items()
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> None:
    contract = load_contract()
    source = _load_module("m3_2x2_source_for_year_audit", SOURCE_RUNNER_PATH)
    metrics = _load_module("m3_relative_metrics_for_year_audit", METRICS_RUNNER_PATH)
    data, inputs = source.load_source_data(ROOT, source.load_config())
    output_dir = ROOT / "exports" / "M3_RELATIVE_TEMPERATURE_YEAR_STABILITY"
    fold_dir = output_dir / "folds"
    fold_dir.mkdir(parents=True, exist_ok=True)
    run_signature = hashlib.sha256(
        (
            _sha256(CONTRACT_PATH)
            + "".join(sorted(str(row["sha256"]) for row in inputs))
        ).encode()
    ).hexdigest()

    folds: list[pd.DataFrame] = []
    ids = fold_ids(data.scored_frame)
    for index, (city_id, year) in enumerate(ids, start=1):
        path = fold_dir / f"{city_id}_{year}.parquet"
        if path.exists():
            cached = pd.read_parquet(path)
            cache_matches = (
                cached["run_signature"].nunique() == 1
                and cached["run_signature"].iat[0] == run_signature
            )
            if cache_matches:
                folds.append(cached.drop(columns="run_signature"))
                print(f"[{index}/{len(ids)}] reused {city_id} {year}", flush=True)
                continue
        result = _fit_fold(data, city_id, year)
        stored = result.copy()
        stored["run_signature"] = run_signature
        stored.to_parquet(path, index=False)
        folds.append(result)
        print(f"[{index}/{len(ids)}] completed {city_id} {year}", flush=True)

    predictions = pd.concat(folds, ignore_index=True)
    date_metrics = metrics.calculate_date_metrics(
        predictions,
        float(contract["evaluation"]["hotspot_fraction"]),
    )
    city_metrics, role_metrics = metrics.aggregate_metrics(date_metrics)
    bootstrap = metrics.paired_hierarchical_bootstrap(
        date_metrics,
        iterations=int(contract["evaluation"]["bootstrap_iterations"]),
        seed=int(contract["evaluation"]["bootstrap_seed"]),
    )["source_leave_one_year_out"]
    gate = _evaluate_gate(role_metrics, city_metrics, bootstrap, contract)

    predictions.to_parquet(output_dir / "predictions.parquet", index=False)
    date_metrics.to_parquet(output_dir / "date_metrics.parquet", index=False)
    city_metrics.to_parquet(output_dir / "city_metrics.parquet", index=False)
    role_metrics.to_parquet(output_dir / "role_metrics.parquet", index=False)
    summary = {
        "schema_version": 1,
        "contract_id": contract["contract"]["id"],
        "completed_at": datetime.now(UTC).isoformat(),
        "run_signature": run_signature,
        "no_network_or_new_city_access": True,
        "fold_count": len(ids),
        "fold_ids": [{"city_id": city, "year": year} for city, year in ids],
        "inputs": inputs,
        "role_metrics": role_metrics.to_dict(orient="records"),
        "paired_hierarchical_bootstrap": bootstrap,
        "gate": gate,
    }
    _write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
