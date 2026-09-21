"""Export the fixed Colorado source-addition result for the public Atlas.

The output is compact, deterministic display data. Scientific values are read
from authenticated local experiment artifacts; this script does not fit or
score a model and does not read LA 2025 or external-city targets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from la_heat.provenance import sha256_file

DEFAULT_RESULT = Path("exports/COLORADO_SOURCE_ADDITION_FIXED_V1/RESULTS.json")
DEFAULT_EXPERIMENT = Path("exports/COLORADO_SOURCE_ADDITION_FIXED_V1")
DEFAULT_AUTHORIZATION = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_JOINT_NESTED_LOSO_V1_AUTHORIZATION.json"
)
DEFAULT_OUTPUT = Path("atlas/public/data/colorado-source-addition.json")
CITY_NAMES = {
    "chicago_il": "Chicago",
    "houston_tx": "Houston",
    "los_angeles_ca": "Los Angeles",
    "phoenix_az": "Phoenix",
}
EXPECTED_ROWS = 96_061
EXPECTED_CITY_DATES = 132
EXPECTED_SPATIAL_BLOCKS = 254
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source_date_filter(city_id: str) -> list[tuple[str, str, str]] | None:
    if city_id == "los_angeles_ca":
        return [("target_date", "<", "2025-01-01")]
    return None


def build_payload(root: Path) -> dict:
    experiment = root / DEFAULT_EXPERIMENT
    result_path = root / DEFAULT_RESULT
    authorization_path = root / DEFAULT_AUTHORIZATION
    results = json.loads(result_path.read_text(encoding="utf-8"))
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))

    if results["state"] != "complete" or results["decision"] != "no_upgrade":
        raise RuntimeError("Colorado fixed experiment is not a completed no-upgrade result")
    if results["la_2025_accessed"] or results["external_four_city_targets_accessed"]:
        raise RuntimeError("Forbidden target access is recorded in the result")

    predictions_path = experiment / "paired_outer_predictions.parquet"
    predictions = pd.read_parquet(
        predictions_path,
        columns=["city_id", "tract_geoid", "target_date", "variant"],
    )
    by_variant = predictions.groupby("variant", observed=True).size().to_dict()
    if by_variant != {"baseline": EXPECTED_ROWS, "expanded": EXPECTED_ROWS}:
        raise RuntimeError(f"Unexpected paired prediction support: {by_variant}")
    keys = predictions.drop(columns="variant").drop_duplicates()
    keys["target_date"] = pd.to_datetime(keys["target_date"])
    if len(keys) != EXPECTED_ROWS:
        raise RuntimeError("Baseline and expanded scoring keys are not identical")
    city_dates = keys[["city_id", "target_date"]].drop_duplicates()
    if len(city_dates) != EXPECTED_CITY_DATES:
        raise RuntimeError("Unexpected independent city-date count")

    target_support = []
    for record in authorization["source_qa_target_tables"]:
        if record["qa_id"] != results["qa_id"]:
            continue
        path = root / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"Source target drifted: {record['city_id']}")
        frame = pd.read_parquet(
            path,
            columns=[
                "city_id",
                "tract_geoid",
                "target_date",
                "spatial_block",
                "date_usable",
                "target_available",
            ],
            filters=_source_date_filter(record["city_id"]),
        )
        target_support.append(
            frame.loc[
                frame["date_usable"] & frame["target_available"],
                ["city_id", "tract_geoid", "target_date", "spatial_block"],
            ]
        )
    support = pd.concat(target_support, ignore_index=True)
    support["target_date"] = pd.to_datetime(support["target_date"])
    keyed_support = keys.merge(
        support,
        on=["city_id", "tract_geoid", "target_date"],
        how="left",
        validate="one_to_one",
    )
    if keyed_support["spatial_block"].isna().any():
        raise RuntimeError("A scored key has no authenticated spatial block")
    spatial_blocks = len(
        keyed_support[["city_id", "spatial_block"]].drop_duplicates()
    )
    if spatial_blocks != EXPECTED_SPATIAL_BLOCKS:
        raise RuntimeError(f"Unexpected spatial-block count: {spatial_blocks}")

    city_metrics_path = experiment / "paired_city_metrics.parquet"
    city_metrics = pd.read_parquet(city_metrics_path).sort_values("held_city_id")
    cities = []
    for row in city_metrics.itertuples(index=False):
        cities.append(
            {
                "id": row.held_city_id,
                "name": CITY_NAMES[row.held_city_id],
                "baselineMaeC": row.baseline,
                "expandedMaeC": row.expanded,
                "deltaC": row.expanded_minus_baseline_c,
            }
        )

    return {
        "state": results["state"],
        "decision": results["decision"],
        "label": "No upgrade",
        "candidateId": results["candidate_id"],
        "qaId": results["qa_id"],
        "support": {
            "scoredRows": EXPECTED_ROWS,
            "independentCityDates": EXPECTED_CITY_DATES,
            "spatialBlocks": spatial_blocks,
            "heldCities": 4,
            "coloradoTrainingRows": results["colorado_training_rows"],
            "coloradoTrainingDates": results["colorado_training_dates"],
        },
        "baseline": results["metrics"]["baseline"],
        "expanded": results["metrics"]["expanded"],
        "relativeImprovementPercent": 100 * results["primary_relative_improvement"],
        "expandedMinusBaselineCi95C": results[
            "paired_expanded_minus_baseline_mae_ci95_c"
        ],
        "maximumCityMaeDegradationC": results["maximum_city_mae_degradation_c"],
        "fivePercentGatePassed": results["primary_five_percent_gate_passed"],
        "cityGuardPassed": results["per_city_degradation_gate_passed"],
        "cities": cities,
        "scope": {
            "evidenceRole": "source-city development validation",
            "independentConfirmation": False,
            "defaultModelChanged": False,
            "la2025Accessed": results["la_2025_accessed"],
            "externalFourCityTargetsAccessed": results[
                "external_four_city_targets_accessed"
            ],
            "networkRequests": results["network_requests"],
        },
        "sources": [
            {"path": str(DEFAULT_RESULT).replace("\\", "/"), "sha256": sha256_file(result_path)},
            {
                "path": str(DEFAULT_EXPERIMENT / "paired_city_metrics.parquet").replace("\\", "/"),
                "sha256": sha256_file(city_metrics_path),
            },
            {
                "path": str(
                    DEFAULT_EXPERIMENT / "paired_outer_predictions.parquet"
                ).replace("\\", "/"),
                "sha256": sha256_file(predictions_path),
            },
            {
                "path": str(DEFAULT_AUTHORIZATION).replace("\\", "/"),
                "sha256": sha256_file(authorization_path),
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_payload(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"COLORADO_SOURCE_ADDITION_ATLAS_COMPLETE {output}")


if __name__ == "__main__":
    main()
