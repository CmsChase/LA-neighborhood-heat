"""Deterministic Atlas publication from authenticated external evaluation outputs."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity.external_evaluation import (
    OUTPUT_DIRECTORY as EVALUATION_OUTPUT_DIRECTORY,
)
from la_heat.multicity.external_evaluation import (
    authenticate_external_evaluation_completion,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.provenance import canonical_sha256, parquet_file_record, sha256_file

ALGORITHM_VERSION: Final = "multicity-atlas-release-v1"
ATLAS_SCHEMA_VERSION: Final = "multicity-atlas-release-v1"
ATLAS_OUTPUT_PATH: Final = Path("atlas/app/cities/generated-results.ts")
RELEASE_MANIFEST_PATH: Final = Path("manifests/multicity/releases/ATLAS_RESULTS_RELEASE.json")
CITY_METRICS_FILENAME: Final = "city_metrics.parquet"
DATE_METRICS_FILENAME: Final = "date_metrics.parquet"
SUMMARY_FILENAME: Final = "summary.json"


class AtlasReleaseError(RuntimeError):
    """Raised when authenticated evidence cannot produce the exact Atlas release."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise AtlasReleaseError(f"{label} must stay inside the project")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AtlasReleaseError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise AtlasReleaseError(f"{label} must be a JSON object")
    return payload


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    payload = _read_json(path, label=label)
    commit = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(commit, str) or commit != canonical_sha256(unsigned):
        raise AtlasReleaseError(f"{label} commit is invalid")
    return payload


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _exclusive_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise AtlasReleaseError(f"Append-only Atlas release already exists: {path}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    try:
        partial.write_text(text, encoding="utf-8", newline="\n")
        partial.replace(path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _finite(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise AtlasReleaseError(f"{label} is not numeric") from error
    if not math.isfinite(result):
        raise AtlasReleaseError(f"{label} is not finite")
    return result


def _authenticate_file(
    path: Path,
    record: object,
    *,
    frame: pd.DataFrame | None = None,
) -> None:
    if not isinstance(record, dict) or not path.is_file():
        raise AtlasReleaseError(f"Authenticated evaluation output is missing: {path.name}")
    expected = (
        parquet_file_record(path, frame)
        if frame is not None
        else {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    )
    if record != expected:
        raise AtlasReleaseError(f"Authenticated evaluation output changed: {path.name}")


def _load_authenticated_metrics(
    root: Path,
    evaluation_output: Path,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    completion = authenticate_external_evaluation_completion(
        root,
        output_directory=evaluation_output,
    )
    records = completion.get("output_files")
    if not isinstance(records, dict):
        raise AtlasReleaseError("Evaluation completion lacks output records")
    city_path = evaluation_output / CITY_METRICS_FILENAME
    date_path = evaluation_output / DATE_METRICS_FILENAME
    summary_path = evaluation_output / SUMMARY_FILENAME
    try:
        city_metrics = pd.read_parquet(city_path)
        date_metrics = pd.read_parquet(date_path)
    except Exception as error:  # noqa: BLE001 - normalize reader failures
        raise AtlasReleaseError("Authenticated Atlas metric tables cannot be read") from error
    summary = _read_json(summary_path, label="Authenticated evaluation summary")
    _authenticate_file(city_path, records.get(CITY_METRICS_FILENAME), frame=city_metrics)
    _authenticate_file(date_path, records.get(DATE_METRICS_FILENAME), frame=date_metrics)
    _authenticate_file(summary_path, records.get(SUMMARY_FILENAME))
    return completion, city_metrics, date_metrics, summary


def _external_result(
    city_id: str,
    row: pd.Series,
    dates: pd.DataFrame,
) -> dict[str, Any]:
    b1_mae = _finite(row["b1_equal_date_mae_c"], label=f"{city_id} B1 MAE")
    m2_mae = _finite(row["m2_equal_date_mae_c"], label=f"{city_id} M2 MAE")
    if b1_mae <= 0:
        raise AtlasReleaseError(f"{city_id} B1 MAE must be positive")
    city_dates = dates.loc[dates["city_id"].astype(str).eq(city_id)].copy()
    if len(city_dates) != int(row["date_count"]):
        raise AtlasReleaseError(f"{city_id} date metrics do not match city metrics")
    observed_dates = sorted(city_dates["target_date"].astype(str).tolist())
    if not observed_dates:
        raise AtlasReleaseError(f"{city_id} has no authenticated evaluation dates")
    return {
        "cityId": city_id,
        "resultState": "authenticated_external_confirmation",
        "evaluationRows": int(row["row_count"]),
        "independentDates": int(row["date_count"]),
        "independentSpatialBlocks": int(row["spatial_block_count"]),
        "evaluatedDateRange": {
            "first": observed_dates[0],
            "last": observed_dates[-1],
        },
        "primary": {
            "equalDateMaeC": m2_mae,
            "baselineEqualDateMaeC": b1_mae,
            "medianPerDateSpearman": _finite(
                row["median_per_date_m2_spearman"],
                label=f"{city_id} median Spearman",
            ),
            "relativeMaeImprovementPercent": 100.0 * (1.0 - m2_mae / b1_mae),
        },
        "uncertainty": {
            "nominalCoverage": 0.9,
            "empiricalCoverage": _finite(row["m2_interval_coverage"], label=f"{city_id} coverage"),
            "retentionFraction": _finite(
                row["m2_retention_fraction"], label=f"{city_id} retention"
            ),
            "meanIntervalWidthC": _finite(
                row["m2_mean_interval_width_c"], label=f"{city_id} interval width"
            ),
            "wis90C": _finite(row["m2_wis90_c"], label=f"{city_id} WIS"),
        },
    }


def build_atlas_release_payload(
    project_root: str | Path,
    *,
    evaluation_output_directory: str | Path = EVALUATION_OUTPUT_DIRECTORY,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a verified overlay only after authenticating the complete evaluation."""

    root = Path(project_root).resolve()
    evaluation_output = _inside(
        root, evaluation_output_directory, label="External evaluation output"
    )
    completion, city_metrics, date_metrics, summary = _load_authenticated_metrics(
        root, evaluation_output
    )
    if (
        summary.get("city_ids") != list(EXTERNAL_CITY_IDS)
        or set(city_metrics["city_id"].astype(str)) != set(EXTERNAL_CITY_IDS)
        or set(date_metrics["city_id"].astype(str)) != set(EXTERNAL_CITY_IDS)
    ):
        raise AtlasReleaseError("Authenticated external city cohort changed")
    indexed = city_metrics.set_index(city_metrics["city_id"].astype(str), drop=False)
    external_results = [
        _external_result(city_id, indexed.loc[city_id], date_metrics)
        for city_id in EXTERNAL_CITY_IDS
    ]
    payload: dict[str, Any] = {
        "schemaVersion": ATLAS_SCHEMA_VERSION,
        "release": {
            "state": "verified",
            "label": "Authenticated three-city external confirmation",
            "claimId": completion["commit_sha256"],
            "notice": (
                "Los Angeles is the historical source reference; Phoenix, Houston, "
                "and Chicago are one authenticated 2025 external confirmation claim."
            ),
        },
        "sourceReference": {
            "cityId": "los_angeles_ca",
            "resultState": "historical_source_reference",
            "label": "Completed Phase-I Los Angeles held-out evaluation",
            "href": "/",
            "comparableAsExternalConfirmation": False,
            "notice": (
                "Los Angeles supplied model fitting and calibration data in this "
                "transfer experiment, so its earlier held-out study is linked as "
                "context and is not pooled with the three external confirmations."
            ),
        },
        "externalConfirmation": {
            "cohortState": str(summary["state"]),
            "cityIds": list(EXTERNAL_CITY_IDS),
            "usableRows": int(summary["usable_row_count"]),
            "usableCityDates": int(summary["usable_city_date_count"]),
            "spatialBlocks": int(summary["spatial_block_count"]),
            "relativeMaeImprovementPercent": 100.0
            * _finite(
                summary["primary"]["relative_mae_improvement_fraction"],
                label="Three-city relative MAE improvement",
            ),
            "bootstrapCiPercent": {
                "lower": 100.0
                * _finite(
                    summary["primary"]["bootstrap_ci_lower"],
                    label="Bootstrap lower bound",
                ),
                "upper": 100.0
                * _finite(
                    summary["primary"]["bootstrap_ci_upper"],
                    label="Bootstrap upper bound",
                ),
            },
            "pointPredictionGatePassed": bool(summary["point_prediction_gates"]["success"]),
            "reliabilityGatePassed": bool(summary["reliability"]["success"]),
        },
        "externalResults": external_results,
        "provenance": [
            {
                "label": "Authenticated external evaluation completion",
                "repositoryPath": _relative(
                    root, evaluation_output / "EXTERNAL_EVALUATION_COMPLETE.json"
                ),
            },
            {
                "label": "Authenticated three-city summary",
                "repositoryPath": _relative(root, evaluation_output / SUMMARY_FILENAME),
            },
            {
                "label": "Authenticated per-city metrics",
                "repositoryPath": _relative(root, evaluation_output / CITY_METRICS_FILENAME),
            },
        ],
    }
    for source in payload["provenance"]:
        source["href"] = (
            "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/" + source["repositoryPath"]
        )
    evidence = {
        "evaluation_completion": {
            **_file_record(root, evaluation_output / "EXTERNAL_EVALUATION_COMPLETE.json"),
            "commit_sha256": completion["commit_sha256"],
        },
        "city_metrics": {
            **_file_record(root, evaluation_output / CITY_METRICS_FILENAME),
            **parquet_file_record(evaluation_output / CITY_METRICS_FILENAME, city_metrics),
        },
        "date_metrics": {
            **_file_record(root, evaluation_output / DATE_METRICS_FILENAME),
            **parquet_file_record(evaluation_output / DATE_METRICS_FILENAME, date_metrics),
        },
        "summary": _file_record(root, evaluation_output / SUMMARY_FILENAME),
    }
    return payload, evidence


def _render_typescript(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    return (
        "// Generated from authenticated external evaluation evidence.\n"
        "// Do not edit by hand; use scripts/publish_multicity_atlas_release.py.\n"
        f"export const GENERATED_VERIFIED_RELEASE: unknown = {encoded};\n"
    )


def authenticate_atlas_release(
    project_root: str | Path,
    *,
    evaluation_output_directory: str | Path = EVALUATION_OUTPUT_DIRECTORY,
    atlas_output_path: str | Path = ATLAS_OUTPUT_PATH,
    release_manifest_path: str | Path = RELEASE_MANIFEST_PATH,
) -> dict[str, Any]:
    """Rebuild and byte-authenticate an existing deterministic Atlas release."""

    root = Path(project_root).resolve()
    atlas_path = _inside(root, atlas_output_path, label="Atlas generated result")
    manifest_path = _inside(root, release_manifest_path, label="Atlas release manifest")
    manifest = _read_committed(manifest_path, label="Atlas release manifest")
    payload, evidence = build_atlas_release_payload(
        root, evaluation_output_directory=evaluation_output_directory
    )
    expected_text = _render_typescript(payload)
    try:
        observed_text = atlas_path.read_text(encoding="utf-8")
    except OSError as error:
        raise AtlasReleaseError("Atlas generated result is unavailable") from error
    expected = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "authenticated_multicity_atlas_release",
        "evaluation_completion_commit_sha256": evidence["evaluation_completion"]["commit_sha256"],
        "release_claim_id": payload["release"]["claimId"],
        "payload_semantic_sha256": canonical_sha256(payload),
        "evidence": evidence,
        "atlas_output": _file_record(root, atlas_path),
        "external_city_ids": list(EXTERNAL_CITY_IDS),
        "la_role": "historical_source_reference_not_external_confirmation",
        "next_safe_stage": "build_and_publish_verified_static_atlas",
    }
    expected["commit_sha256"] = canonical_sha256(expected)
    if observed_text != expected_text or manifest != expected:
        raise AtlasReleaseError("Atlas release no longer reproduces authenticated evidence")
    return manifest


def publish_atlas_release(
    project_root: str | Path,
    *,
    evaluation_output_directory: str | Path = EVALUATION_OUTPUT_DIRECTORY,
    atlas_output_path: str | Path = ATLAS_OUTPUT_PATH,
    release_manifest_path: str | Path = RELEASE_MANIFEST_PATH,
    check_only: bool = False,
) -> dict[str, Any]:
    """Publish once, or reauthenticate the existing release with ``check_only``."""

    root = Path(project_root).resolve()
    atlas_path = _inside(root, atlas_output_path, label="Atlas generated result")
    manifest_path = _inside(root, release_manifest_path, label="Atlas release manifest")
    if check_only or manifest_path.exists():
        return authenticate_atlas_release(
            root,
            evaluation_output_directory=evaluation_output_directory,
            atlas_output_path=atlas_path,
            release_manifest_path=manifest_path,
        )
    payload, evidence = build_atlas_release_payload(
        root, evaluation_output_directory=evaluation_output_directory
    )
    _atomic_text(_render_typescript(payload), atlas_path)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "authenticated_multicity_atlas_release",
        "evaluation_completion_commit_sha256": evidence["evaluation_completion"]["commit_sha256"],
        "release_claim_id": payload["release"]["claimId"],
        "payload_semantic_sha256": canonical_sha256(payload),
        "evidence": evidence,
        "atlas_output": _file_record(root, atlas_path),
        "external_city_ids": list(EXTERNAL_CITY_IDS),
        "la_role": "historical_source_reference_not_external_confirmation",
        "next_safe_stage": "build_and_publish_verified_static_atlas",
    }
    manifest["commit_sha256"] = canonical_sha256(manifest)
    _exclusive_json(manifest, manifest_path)
    return authenticate_atlas_release(
        root,
        evaluation_output_directory=evaluation_output_directory,
        atlas_output_path=atlas_path,
        release_manifest_path=manifest_path,
    )
