"""Export complete frozen source-city historical predictions from the saved bundle."""

import argparse
import json

import joblib
import pandas as pd
from run import ROOT, digest, predict_models
from threadpoolctl import threadpool_limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--city",
        required=True,
        choices=["los_angeles_ca", "phoenix_az", "houston_tx", "chicago_il"],
    )
    args = parser.parse_args()
    output = ROOT / "exports/RELATIVE_ACCURACY_DEVELOPMENT"
    metadata = json.loads((output / "model_metadata.json").read_text())
    model_path = output / "development_model.joblib"
    if digest(model_path) != metadata["sha256"]:
        raise ValueError("Saved model hash mismatch")
    provenance = json.loads((output / "provenance.json").read_text())
    if metadata["signature"] != provenance["signature"]:
        raise ValueError("Model/provenance signature mismatch")
    record = next(
        row
        for row in provenance["inputs"]
        if row.get("city_id") == args.city and row["path"].endswith("predictors_46.parquet")
    )
    path = ROOT / record["path"]
    if digest(path) != record["sha256"]:
        raise ValueError("Frozen complete predictor support changed")
    frame = pd.read_parquet(path)
    frame["target_date"] = pd.to_datetime(frame.target_date).dt.strftime("%Y-%m-%d")
    frame["tract_geoid"] = frame.tract_geoid.astype(str).str.zfill(11)
    bundle = joblib.load(model_path)
    with threadpool_limits(limits=4):
        prediction = predict_models(bundle["models"], frame)
    candidate = bundle["candidate"]
    result = prediction[
        ["city_id", "tract_geoid", "target_date", candidate + "_relative", candidate]
    ].rename(columns={candidate: "absolute_lst_c", candidate + "_relative": "relative_lst_c"})
    result["weather_baseline_absolute_lst_c"] = prediction["B1"].to_numpy()
    result["candidate"] = candidate
    result["role"] = "in_sample_historical_reconstruction_not_validation"
    result["promotion_passed"] = bundle["promotion_passed"]
    destination = output / "predictions" / (args.city + ".parquet")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(destination, index=False)
    print(f"{len(result)} rows: {destination}")


if __name__ == "__main__":
    main()
