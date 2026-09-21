"""Audit frozen source and opened-city artifacts for absolute-error stage 0-1.

This script performs no fitting, downloading, predictor construction, or candidate
prediction.  It verifies immutable artifacts and writes descriptive audit outputs.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = Path(__file__).with_name("fixed_contract.toml")
OUTPUT_DIR = ROOT / "exports" / "FOUR_CITY_ABSOLUTE_ERROR_STAGE_0_1"
KEYS = ["city_id", "tract_geoid", "target_date"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    observed = sha256(path)
    if expected_sha256 is not None and observed != expected_sha256:
        raise ValueError(f"Hash mismatch for {path}: {observed} != {expected_sha256}")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": observed,
    }


def equal_date_metrics(group: pd.DataFrame) -> dict[str, float | int]:
    error = group["m3_prediction_c"].to_numpy(float) - group["target_lst_c"].to_numpy(float)
    absolute = np.abs(error)
    prediction_centered = (
        group["m3_prediction_c"].to_numpy(float)
        - float(group["m3_prediction_c"].median())
    )
    target_centered = (
        group["target_lst_c"].to_numpy(float) - float(group["target_lst_c"].median())
    )
    level = group["m3_level_prediction_c"].to_numpy(float)
    if not np.allclose(level, level[0], rtol=0.0, atol=1e-10):
        raise ValueError("M3 level is not constant within a city-date.")
    return {
        "full_prediction_rows": int(group["full_prediction_rows"].iloc[0]),
        "scored_rows": int(len(group)),
        "spatial_blocks": int(group["spatial_block"].nunique()),
        "m3_level_c": float(level[0]),
        "target_median_c": float(group["target_lst_c"].median()),
        "level_minus_target_median_c": float(level[0] - group["target_lst_c"].median()),
        "full_anomaly_median_c": float(group["full_anomaly_median_c"].iloc[0]),
        "scored_anomaly_median_c": float(group["m3_anomaly_prediction_c"].median()),
        "m3_mae_c": float(absolute.mean()),
        "m3_signed_error_c": float(error.mean()),
        "m3_relative_mae_c": float(np.abs(prediction_centered - target_centered).mean()),
        "m3_p95_absolute_error_c": float(np.quantile(absolute, 0.95)),
        "m3_fraction_absolute_error_gt_5c": float((absolute > 5.0).mean()),
        "m3_fraction_absolute_error_gt_10c": float((absolute > 10.0).mean()),
    }


def main() -> None:
    contract = tomllib.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if [candidate["id"] for candidate in contract["candidates"]] != ["C1", "C2", "C3"]:
        raise ValueError("The fixed candidate set must be exactly C1/C2/C3.")
    if not contract["stage_0_1"]["audit_only"]:
        raise ValueError("Stage 0-1 contract is not audit-only.")

    prediction_manifest_path = (
        ROOT
        / "manifests/multicity/next_experiment/blind_prediction_v2"
        / "M3_BLIND_PREDICTIONS_COMMITTED.json"
    )
    evaluation_manifest_path = (
        ROOT
        / "manifests/multicity/next_experiment/blind_evaluation_v1"
        / "M3_BLIND_EVALUATION_COMPLETE.json"
    )
    prediction_manifest = json.loads(prediction_manifest_path.read_text(encoding="utf-8"))
    evaluation_manifest = json.loads(evaluation_manifest_path.read_text(encoding="utf-8"))

    checked_artifacts: list[dict[str, Any]] = [
        artifact(CONTRACT_PATH),
        artifact(prediction_manifest_path),
        artifact(evaluation_manifest_path),
    ]
    full_frames: list[pd.DataFrame] = []
    full_file_rows: dict[str, int] = {}
    for entry in prediction_manifest["city_outputs"]:
        path = ROOT / entry["path"]
        checked_artifacts.append(artifact(path, expected_sha256=entry["sha256"]))
        frame = pd.read_parquet(path)
        if len(frame) != entry["rows"] or frame["city_id"].nunique() != 1:
            raise ValueError(f"Unexpected full prediction shape for {entry['city_id']}.")
        full_file_rows[entry["city_id"]] = int(len(frame))
        full_frames.append(frame)
    full = pd.concat(full_frames, ignore_index=True)
    if len(full) != prediction_manifest["row_count"] or full.duplicated(KEYS).any():
        raise ValueError("Full prediction key or row-count audit failed.")

    equation_error = np.abs(
        full["m3_prediction_c"].to_numpy(float)
        - full["m3_level_prediction_c"].to_numpy(float)
        - full["m3_anomaly_prediction_c"].to_numpy(float)
    )
    full_by_date = (
        full.groupby(["city_id", "target_date"], sort=True)
        .agg(
            full_prediction_rows=("tract_geoid", "size"),
            full_anomaly_median_c=("m3_anomaly_prediction_c", "median"),
            full_level_min_c=("m3_level_prediction_c", "min"),
            full_level_max_c=("m3_level_prediction_c", "max"),
        )
        .reset_index()
    )
    if not np.allclose(
        full_by_date["full_level_min_c"],
        full_by_date["full_level_max_c"],
        rtol=0.0,
        atol=1e-10,
    ):
        raise ValueError("Full M3 level varies within a city-date.")

    scored_entry = evaluation_manifest["outputs"]["scored_rows.parquet"]
    scored_path = ROOT / scored_entry["path"]
    checked_artifacts.append(artifact(scored_path, expected_sha256=scored_entry["sha256"]))
    scored = pd.read_parquet(scored_path)
    if len(scored) != scored_entry["rows"] or scored.duplicated(KEYS).any():
        raise ValueError("Scored prediction key or row-count audit failed.")
    components = full.loc[:, KEYS + ["m3_level_prediction_c", "m3_anomaly_prediction_c"]]
    audit_rows = scored.merge(components, on=KEYS, how="left", validate="one_to_one")
    audit_rows = audit_rows.merge(
        full_by_date.loc[:, [
            "city_id",
            "target_date",
            "full_prediction_rows",
            "full_anomaly_median_c",
        ]],
        on=["city_id", "target_date"],
        how="left",
        validate="many_to_one",
    )
    if audit_rows[["m3_level_prediction_c", "m3_anomaly_prediction_c"]].isna().any().any():
        raise ValueError("A scored key is absent from the complete prediction universe.")
    if not np.allclose(
        audit_rows["m3_prediction_c"],
        audit_rows["m3_level_prediction_c"] + audit_rows["m3_anomaly_prediction_c"],
        rtol=0.0,
        atol=1e-10,
    ):
        raise ValueError("Scored absolute = level + anomaly identity failed.")

    date_records: list[dict[str, Any]] = []
    for (city_id, target_date), group in audit_rows.groupby(
        ["city_id", "target_date"], sort=True
    ):
        date_records.append(
            {"city_id": city_id, "target_date": str(target_date), **equal_date_metrics(group)}
        )
    date_audit = pd.DataFrame(date_records)

    city_records: list[dict[str, Any]] = []
    for city_id, dates in date_audit.groupby("city_id", sort=True):
        rows = audit_rows.loc[audit_rows["city_id"].eq(city_id)]
        error = rows["m3_prediction_c"].to_numpy(float) - rows["target_lst_c"].to_numpy(float)
        city_records.append(
            {
                "city_id": city_id,
                "full_prediction_rows": full_file_rows[city_id],
                "full_dates": int(full.loc[full["city_id"].eq(city_id), "target_date"].nunique()),
                "scored_rows": int(len(rows)),
                "scored_dates": int(len(dates)),
                "scored_spatial_blocks": int(rows["spatial_block"].nunique()),
                "equal_date_absolute_mae_c": float(dates["m3_mae_c"].mean()),
                "equal_date_signed_error_c": float(dates["m3_signed_error_c"].mean()),
                "equal_date_relative_mae_c": float(dates["m3_relative_mae_c"].mean()),
                "equal_date_p95_absolute_error_c": float(
                    dates["m3_p95_absolute_error_c"].mean()
                ),
                "equal_date_fraction_absolute_error_gt_5c": float(
                    dates["m3_fraction_absolute_error_gt_5c"].mean()
                ),
                "equal_date_fraction_absolute_error_gt_10c": float(
                    dates["m3_fraction_absolute_error_gt_10c"].mean()
                ),
                "equal_date_level_minus_target_median_c": float(
                    dates["level_minus_target_median_c"].mean()
                ),
                "level_minus_target_median_min_c": float(
                    dates["level_minus_target_median_c"].min()
                ),
                "level_minus_target_median_max_c": float(
                    dates["level_minus_target_median_c"].max()
                ),
                "max_absolute_full_anomaly_median_c": float(
                    dates["full_anomaly_median_c"].abs().max()
                ),
                "mean_absolute_scored_anomaly_median_c": float(
                    dates["scored_anomaly_median_c"].abs().mean()
                ),
                "pooled_row_absolute_mae_c": float(np.abs(error).mean()),
                "pooled_row_p95_absolute_error_c": float(np.quantile(np.abs(error), 0.95)),
            }
        )
    city_audit = pd.DataFrame(city_records)

    expected = {
        "seattle_wa": contract["expected_current_baseline"]["seattle_absolute_mae_c"],
        "denver_co": contract["expected_current_baseline"]["denver_absolute_mae_c"],
        "atlanta_ga": contract["expected_current_baseline"]["atlanta_absolute_mae_c"],
        "miami_fl": contract["expected_current_baseline"]["miami_absolute_mae_c"],
    }
    tolerance = float(contract["expected_current_baseline"]["tolerance_c"])
    reproduction: dict[str, Any] = {}
    for city_id, expected_value in expected.items():
        observed = float(
            city_audit.loc[
                city_audit["city_id"].eq(city_id), "equal_date_absolute_mae_c"
            ].iloc[0]
        )
        reproduction[city_id] = {
            "expected_c": expected_value,
            "observed_c": observed,
            "difference_c": observed - expected_value,
            "passed": abs(observed - expected_value) <= tolerance,
        }
    overall_mae = float(city_audit["equal_date_absolute_mae_c"].mean())
    expected_overall = float(
        contract["expected_current_baseline"][
            "overall_equal_city_equal_date_absolute_mae_c"
        ]
    )
    reproduction["overall_equal_city_equal_date"] = {
        "expected_c": expected_overall,
        "observed_c": overall_mae,
        "difference_c": overall_mae - expected_overall,
        "passed": abs(overall_mae - expected_overall) <= tolerance,
    }
    if not all(record["passed"] for record in reproduction.values()):
        raise ValueError("A frozen baseline did not reproduce within the contract tolerance.")

    for _name, entry in evaluation_manifest["outputs"].items():
        path = ROOT / entry["path"]
        if path == scored_path:
            continue
        checked_artifacts.append(artifact(path, expected_sha256=entry["sha256"]))

    source_model_metadata_path = (
        ROOT
        / "data/processed/multicity/m3_source_joint_nested_loso_v1/joint"
        / "selected_source_model_metadata.json"
    )
    source_model_metadata = json.loads(source_model_metadata_path.read_text(encoding="utf-8"))
    checked_artifacts.append(artifact(source_model_metadata_path))
    source_model_path = ROOT / source_model_metadata["model_file"]["path"]
    checked_artifacts.append(
        artifact(source_model_path, expected_sha256=source_model_metadata["model_file"]["sha256"])
    )
    modeling_code = artifact(ROOT / "src/la_heat/multicity/m3_development.py")
    opened_prediction_code = artifact(
        ROOT / "src/la_heat/multicity/m3_blind_prediction_v2.py"
    )
    source_2x2_code = artifact(ROOT / "experiments/m3_2x2/run.py")
    checked_artifacts.extend([modeling_code, opened_prediction_code, source_2x2_code])

    relative_metadata_path = ROOT / "exports/M3_RELATIVE_TEMPERATURE_MODEL_V1/model_metadata.json"
    relative_metadata = json.loads(relative_metadata_path.read_text(encoding="utf-8"))
    checked_artifacts.append(artifact(relative_metadata_path))
    for entry in relative_metadata["artifacts"]:
        checked_artifacts.append(artifact(ROOT / entry["path"], expected_sha256=entry["sha256"]))

    source_2x2_dir = ROOT / "exports/M3_SOURCE_LEVEL_2X2"
    source_summary_path = source_2x2_dir / "summary.json"
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    for name in (
        "summary.json",
        "predictions.parquet",
        "date_metrics.parquet",
        "city_metrics.parquet",
        "metrics.parquet",
    ):
        checked_artifacts.append(artifact(source_2x2_dir / name))
    source_city = pd.read_parquet(source_2x2_dir / "city_metrics.parquet")
    source_a = source_city.loc[source_city["model_id"].eq("A")].copy()
    source_b1 = source_city.loc[source_city["model_id"].eq("B1")].copy()
    source_predictions = pd.read_parquet(
        source_2x2_dir / "predictions.parquet",
        columns=["model_id", "city_id", "tract_geoid", "target_date"],
    )
    source_prediction_keys = source_predictions.loc[
        source_predictions["model_id"].eq("A"), KEYS
    ]
    source_scored_by_city: list[dict[str, Any]] = []
    for entry in source_summary["input_files"]:
        if entry["role"] != "qa_4k_targets":
            continue
        target_path = ROOT / entry["path"]
        checked_artifacts.append(artifact(target_path, expected_sha256=entry["sha256"]))
        targets = pd.read_parquet(target_path, columns=KEYS + ["spatial_block"])
        city_keys = source_prediction_keys.loc[
            source_prediction_keys["city_id"].eq(entry["city_id"])
        ]
        joined = city_keys.merge(targets, on=KEYS, how="left", validate="one_to_one")
        if joined["spatial_block"].isna().any():
            raise ValueError(f"Missing source spatial block for {entry['city_id']}.")
        source_scored_by_city.append(
            {
                "city_id": entry["city_id"],
                "rows": int(len(joined)),
                "city_dates": int(joined["target_date"].nunique()),
                "spatial_blocks": int(joined["spatial_block"].nunique()),
            }
        )
    source_reuse = {
        "fixed_qa4k_scored_oof_available": True,
        "fixed_original_m3_model_id_in_2x2": "A",
        "rows": int(source_a["rows"].sum()),
        "city_dates": int(source_a["dates"].sum()),
        "cities": int(source_a["city_id"].nunique()),
        "by_city": sorted(source_scored_by_city, key=lambda record: record["city_id"]),
        "original_m3_equal_city_equal_date_mae_c": float(source_a["mae_c"].mean()),
        "b1_equal_city_equal_date_mae_c": float(source_b1["mae_c"].mean()),
        "complete_universe_predictions_present": False,
        "spatial_block_column_present": False,
        "limitation": (
            "The fixed QA4K 2x2 OOF file is reusable for scored-row reproduction, but "
            "stage 2 must produce fold-held complete-universe predictions before C1/C2/C3 "
            "centering and final contract scoring."
        ),
    }

    nested_oof_path = (
        ROOT
        / "data/processed/multicity/m3_source_joint_nested_loso_v1/joint"
        / "outer_oof_predictions.parquet"
    )
    nested_selection_path = nested_oof_path.with_name("outer_selections.parquet")
    checked_artifacts.extend([artifact(nested_oof_path), artifact(nested_selection_path)])
    nested_selection = pd.read_parquet(nested_selection_path)
    nested_oof = pd.read_parquet(nested_oof_path, columns=["city_id", "target_date"])
    nested_reuse = {
        "rows": int(len(nested_oof)),
        "city_dates": int(nested_oof.drop_duplicates(["city_id", "target_date"]).shape[0]),
        "outer_fold_selected_qa_ids": sorted(nested_selection["qa_id"].astype(str).unique()),
        "outer_fold_selected_m3_candidates": sorted(
            nested_selection["m3_candidate_id"].astype(str).unique()
        ),
        "directly_reusable_as_fixed_contract_oof": False,
        "reason": (
            "Outer folds selected different QA/model combinations; they are provenance "
            "evidence, not fixed-QA4K candidate predictions."
        ),
    }

    source_predictor_completion_path = (
        ROOT
        / "manifests/multicity/next_experiment/source_predictor_extension_v1"
        / "SOURCE_PREDICTORS_46_COMPLETE.json"
    )
    blind_predictor_completion_path = (
        ROOT
        / "manifests/multicity/next_experiment/blind_predictor_build_v1"
        / "M3_BLIND_PREDICTORS_46_COMPLETE.json"
    )
    checked_artifacts.extend(
        [artifact(source_predictor_completion_path), artifact(blind_predictor_completion_path)]
    )

    identities = {
        "original_full_m3": {
            "artifact": source_model_metadata["model_file"],
            "training_scope": source_model_metadata["source_city_ids"],
            "qa": source_model_metadata["selected_qa_id"],
            "features": 46,
            "context_features": source_model_metadata["context_features"],
            "role": "full absolute model containing both level and original anomaly components",
        },
        "original_m3_anomaly": {
            "artifact": source_model_metadata["model_file"],
            "standalone_file": False,
            "features": 23,
            "role": "anomaly_model component used unchanged by C1/C2/C3",
        },
        "independent_m3_relative_v1": {
            "artifact": relative_metadata["artifacts"][0],
            "prediction_artifact": relative_metadata["artifacts"][1],
            "training_scope": relative_metadata["training_city_ids"],
            "training_rows": relative_metadata["training_rows"],
            "training_city_dates": relative_metadata["training_city_dates"],
            "features": relative_metadata["feature_count"],
            "absolute_output": relative_metadata["absolute_temperature_output"],
            "role": "separate relative-only development model; not the original M3 anomaly branch",
        },
        "b1": {
            "standalone_serialized_artifact": False,
            "features": 23,
            "estimator": "training-fold Ridge(alpha=10) pipeline under the original protocol",
            "modeling_code": modeling_code,
            "opened_prediction_code": opened_prediction_code,
            "source_fixed_qa4k_oof_code": source_2x2_code,
            "opened_prediction_storage": [
                entry["path"] for entry in prediction_manifest["city_outputs"]
            ],
            "role": (
                "source-only baseline refit under each authorized training fold; frozen "
                "opened predictions are directly reusable"
            ),
        },
    }

    all_checked_unique = {entry["path"]: entry for entry in checked_artifacts}
    summary = {
        "schema_version": 1,
        "stage": "four_city_absolute_error_stage_0_1",
        "state": "complete",
        "contract": artifact(CONTRACT_PATH),
        "no_training_or_candidate_prediction_performed": True,
        "no_network_or_new_predictor_build_performed": True,
        "model_identities": identities,
        "baseline_reproduction": reproduction,
        "absolute_equals_level_plus_anomaly": {
            "rows_checked": int(len(full)),
            "maximum_absolute_difference_c": float(equation_error.max()),
            "passed": bool(equation_error.max() <= 1e-10),
        },
        "complete_prediction_universe": {
            "rows": int(len(full)),
            "city_dates": int(full_by_date.shape[0]),
            "cities": int(full["city_id"].nunique()),
            "maximum_absolute_city_date_anomaly_median_c": float(
                full_by_date["full_anomaly_median_c"].abs().max()
            ),
        },
        "scoring_universe": {
            "rows": int(len(scored)),
            "city_dates": int(date_audit.shape[0]),
            "cities": int(scored["city_id"].nunique()),
            "spatial_blocks": int(scored["spatial_block"].nunique()),
        },
        "city_audit": city_audit.to_dict(orient="records"),
        "overall_equal_city_equal_date": {
            "absolute_mae_c": overall_mae,
            "signed_error_c": float(city_audit["equal_date_signed_error_c"].mean()),
            "relative_mae_c": float(city_audit["equal_date_relative_mae_c"].mean()),
            "p95_absolute_error_c": float(
                city_audit["equal_date_p95_absolute_error_c"].mean()
            ),
            "fraction_absolute_error_gt_5c": float(
                city_audit["equal_date_fraction_absolute_error_gt_5c"].mean()
            ),
            "fraction_absolute_error_gt_10c": float(
                city_audit["equal_date_fraction_absolute_error_gt_10c"].mean()
            ),
        },
        "source_fixed_qa4k_oof_reuse": source_reuse,
        "historical_nested_oof": nested_reuse,
        "implementation_readiness": {
            "ready_for_fixed_candidate_comparison_using_local_data": True,
            "local_source_predictor_completion": artifact(source_predictor_completion_path),
            "local_opened_predictor_completion": artifact(blind_predictor_completion_path),
            "reusable": [
                "frozen original M3 model and anomaly component",
                "fixed QA4K scored source OOF predictions for reproduction checks",
                "frozen complete-universe four-city B1/M3 predictions",
                "frozen four-city scoring rows and spatial blocks",
                "existing source and opened-city 46-feature predictor completions",
            ],
            "minimum_gap": (
                "No data download is required. Stage 2 still needs authorized fold-local "
                "computation of C1/C2/C3 and complete-universe source OOF predictions; "
                "those candidate outputs do not yet exist."
            ),
        },
        "scientific_issues": [
            (
                "Opened-city thresholds are fixed recommendations, not independently "
                "calibrated guarantees."
            ),
            (
                "Opened cities have already been revealed and therefore provide historical "
                "stress tests, not new blind confirmation."
            ),
            (
                "C3 product-publication timing is not established; absent such evidence it "
                "supports historical reconstruction only."
            ),
            (
                "Hard elevation gating in C2 can be discontinuous at the training-fold range "
                "boundary; the contract freezes it and forbids post-hoc smoothing."
            ),
        ],
        "artifact_audit": list(all_checked_unique.values()),
        "source_2x2_summary_signature": source_summary["run_signature"],
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_audit.to_csv(OUTPUT_DIR / "date_audit.csv", index=False, lineterminator="\n")
    city_audit.to_csv(OUTPUT_DIR / "city_audit.csv", index=False, lineterminator="\n")
    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "summary": summary_path.relative_to(ROOT).as_posix(),
                "summary_sha256": sha256(summary_path),
                "date_rows": int(len(date_audit)),
                "city_rows": int(len(city_audit)),
                "baseline_reproduction": reproduction,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
