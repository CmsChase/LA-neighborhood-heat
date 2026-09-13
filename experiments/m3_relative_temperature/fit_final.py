"""Fit the fixed four-source M3-relative development model."""

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

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from la_heat.multicity.m3_development import (  # noqa: E402
    ANOMALY_FEATURES,
    KEY_COLUMNS,
    M3_CANDIDATES,
    build_m3_estimators,
    city_date_row_weights,
    validate_prediction_feature_frame,
)

CONTRACT_PATH = Path(__file__).with_name("fixed_contract.toml")
SOURCE_RUNNER_PATH = ROOT / "experiments" / "m3_2x2" / "run.py"
OUTPUT_DIR = ROOT / "exports" / "M3_RELATIVE_TEMPERATURE_MODEL_V1"
COMPLETION_PATH = (
    ROOT
    / "manifests"
    / "multicity"
    / "reviews"
    / "m3_relative_temperature"
    / "M3_RELATIVE_DEVELOPMENT_MODEL_COMPLETE.json"
)


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


def fit_fixed_model(frame: pd.DataFrame, target: pd.Series) -> Any:
    observed = pd.to_numeric(target, errors="raise").astype(float)
    anomaly = observed - observed.groupby(
        [frame["city_id"], frame["target_date"]], observed=True
    ).transform("median")
    model = build_m3_estimators(M3_CANDIDATES[-1])[1]
    model.fit(
        frame.loc[:, ANOMALY_FEATURES],
        anomaly,
        model__sample_weight=city_date_row_weights(frame),
    )
    return model


def predict_relative(
    model: Any,
    frame: pd.DataFrame,
    *,
    minimum_tracts_per_city_date: int,
) -> pd.DataFrame:
    predictors = validate_prediction_feature_frame(frame, required_features=ANOMALY_FEATURES)
    counts = predictors.groupby(["city_id", "target_date"], observed=True).size()
    if int(counts.min()) < minimum_tracts_per_city_date:
        raise ValueError("A city-date has fewer predictor tracts than the fixed contract allows.")
    raw = pd.Series(
        np.asarray(model.predict(predictors.loc[:, ANOMALY_FEATURES]), dtype=float),
        index=predictors.index,
    )
    centered = raw - raw.groupby(
        [predictors["city_id"], predictors["target_date"]], observed=True
    ).transform("median")
    result = predictors.loc[:, KEY_COLUMNS].copy()
    result["relative_lst_anomaly_c"] = centered.to_numpy(dtype=float)
    return result


def main() -> None:
    contract = load_contract()
    source = _load_module("m3_2x2_source_for_final_relative", SOURCE_RUNNER_PATH)
    data, inputs = source.load_source_data(ROOT, source.load_config())
    model = fit_fixed_model(data.scored_frame, data.target)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "m3_relative_v1.joblib"
    bundle = {
        "schema_version": 1,
        "contract_id": contract["contract"]["id"],
        "feature_names": list(ANOMALY_FEATURES),
        "model": model,
    }
    joblib.dump(bundle, model_path, compress=3)

    reloaded = joblib.load(model_path)
    predictions = predict_relative(
        reloaded["model"],
        data.prediction_universe,
        minimum_tracts_per_city_date=int(data.prediction_universe.groupby(
            ["city_id", "target_date"], observed=True
        ).size().min()),
    )
    medians = predictions.groupby(
        ["city_id", "target_date"], observed=True
    )["relative_lst_anomaly_c"].median()
    if not np.allclose(medians.to_numpy(dtype=float), 0.0, atol=1e-12):
        raise RuntimeError("Reloaded model predictions are not city-date centered.")
    predictions.to_parquet(OUTPUT_DIR / "source_predictions.parquet", index=False)

    completion = {
        "schema_version": 1,
        "state": "complete",
        "contract_id": contract["contract"]["id"],
        "completed_at": datetime.now(UTC).isoformat(),
        "model_role": "development_not_confirmation",
        "absolute_temperature_output": False,
        "new_city_data_accessed": False,
        "opened_historical_stress_values_accessed": False,
        "training_city_ids": sorted(data.scored_frame["city_id"].unique().tolist()),
        "training_city_dates": int(
            data.scored_frame.loc[:, ["city_id", "target_date"]].drop_duplicates().shape[0]
        ),
        "training_rows": int(len(data.scored_frame)),
        "feature_count": len(ANOMALY_FEATURES),
        "contract": {
            "path": CONTRACT_PATH.relative_to(ROOT).as_posix(),
            "sha256": _sha256(CONTRACT_PATH),
        },
        "inputs": inputs,
        "artifacts": [
            {
                "path": model_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(model_path),
                "bytes": model_path.stat().st_size,
            },
            {
                "path": "exports/M3_RELATIVE_TEMPERATURE_MODEL_V1/source_predictions.parquet",
                "sha256": _sha256(OUTPUT_DIR / "source_predictions.parquet"),
                "bytes": (OUTPUT_DIR / "source_predictions.parquet").stat().st_size,
            },
        ],
        "reload_prediction_check": "passed",
        "prediction_row_count": int(len(predictions)),
        "city_date_prediction_medians_zero": True,
    }
    _write_json(OUTPUT_DIR / "model_metadata.json", completion)
    _write_json(COMPLETION_PATH, completion)
    print(json.dumps(completion, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
