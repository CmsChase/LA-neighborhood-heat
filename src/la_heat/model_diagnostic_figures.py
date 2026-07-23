"""Authenticated, publication-quality figures for frozen development diagnostics."""

from __future__ import annotations

import importlib.metadata
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import matplotlib

matplotlib.use("Agg", force=True)

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from PIL import Image

from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

FIGURE_SCHEMA_VERSION: Final = 1
FIGURE_ALGORITHM_VERSION: Final = "model-diagnostic-figures-v1"
FIGURE_STATE: Final = "frozen_development_figures"
DEFAULT_FIGURE_CONFIG: Final = Path("configs/model_diagnostic_figures.toml")
OVERVIEW_FILENAME: Final = "joint_performance_overview.png"
FOREST_FILENAME: Final = "qa_cohort_improvement_forest.png"
WORST_DATES_FILENAME: Final = "worst_date_errors.png"
PILOT_MAP_FILENAME: Final = "fixed_date_lst_prediction_maps.png"
SUMMARY_FILENAME: Final = "model_diagnostic_figures_summary.json"
PROVENANCE_FILENAME: Final = "model_diagnostic_figures_provenance.json"
PIPELINE_FILES: Final = (
    "scripts/generate_model_diagnostic_figures.py",
    "src/la_heat/model_diagnostic_figures.py",
    "src/la_heat/provenance.py",
)

BASELINE_COLOR: Final = "#4E79A7"
TARGET_COLOR: Final = "#E15759"
FOCUS_COLOR: Final = "#B07AA1"
OVER_COLOR: Final = "#D55E00"
UNDER_COLOR: Final = "#0072B2"
GRID_COLOR: Final = "#D9D9D9"
TEXT_COLOR: Final = "#222222"
MUTED_COLOR: Final = "#666666"

EXPECTED_FOREST_COHORTS: Final = (
    ("all", "all_rows", "All model-ready rows", "Overall"),
    (
        "st_qa_2k",
        "tract_median_le_2k",
        "Tract median ST_QA <= 2 K (summary diagnostic)",
        "ST_QA tract summary",
    ),
    (
        "st_qa_2k",
        "tract_median_gt_2k",
        "Tract median ST_QA > 2 K",
        "ST_QA tract summary",
    ),
    (
        "valid_fraction",
        "[0.60,0.70)",
        "Valid pixels: 60-<70%",
        "Valid-pixel fraction",
    ),
    (
        "valid_fraction",
        "[0.70,0.80)",
        "Valid pixels: 70-<80%",
        "Valid-pixel fraction",
    ),
    (
        "valid_fraction",
        "[0.80,0.90)",
        "Valid pixels: 80-<90%",
        "Valid-pixel fraction",
    ),
    (
        "valid_fraction",
        "[0.90,1.00]",
        "Valid pixels: 90-100%",
        "Valid-pixel fraction",
    ),
    (
        "sentinel_availability",
        "complete",
        "Sentinel-2: all five features present",
        "Sentinel-2 availability",
    ),
    (
        "sentinel_availability",
        "all_five_missing",
        "Sentinel-2: all five features missing",
        "Sentinel-2 availability",
    ),
    (
        "scene_cloud_metadata",
        "any_scene_cloud_lt_15pct",
        "Any contributing scene cloud < 15%",
        "Scene cloud metadata",
    ),
    (
        "scene_cloud_metadata",
        "no_scene_cloud_lt_15pct",
        "No contributing scene cloud < 15%",
        "Scene cloud metadata",
    ),
)


class ModelDiagnosticFigureError(ValueError):
    """Raised when a figure input or frozen display contract is invalid."""


@dataclass(frozen=True, slots=True)
class ForestCohort:
    dimension: str
    label: str
    display_label: str
    group: str


@dataclass(frozen=True, slots=True)
class ModelDiagnosticFigureConfig:
    path: Path
    semantic_sha256: str
    initial_provenance: Path
    primary_bootstrap: Path
    endpoint_provenance: Path
    hotspot_summary: Path
    sensor_summary: Path
    qa_provenance: Path
    qa_cohort_bootstrap: Path
    worst_dates: Path
    residual_provenance: Path
    target_build_progress: Path
    oof_predictions: Path
    model_ready_targets: Path
    date_summary: Path
    tract_manifest: Path
    figure_output_directory: Path
    table_output_directory: Path
    final_test_year: int
    family: str
    baseline_model_id: str
    target_model_id: str
    sensors: tuple[str, ...]
    expected_bootstrap_replicates: int
    worst_date_limit: int
    figure_dpi: int
    forest_cohorts: tuple[ForestCohort, ...]
    pilot_dates: tuple[pd.Timestamp, ...]


@dataclass(frozen=True, slots=True)
class AuthenticatedFigureInputs:
    primary_bootstrap: pd.DataFrame
    hotspot_summary: pd.DataFrame
    sensor_summary: pd.DataFrame
    qa_cohort_bootstrap: pd.DataFrame
    worst_dates: pd.DataFrame
    pilot_map_data: gpd.GeoDataFrame
    pilot_date_summary: pd.DataFrame
    input_authentication: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ModelDiagnosticFigureError(
            f"{label} keys must be exactly {sorted(expected)}; got {observed}."
        )
    return value


def _resolve_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelDiagnosticFigureError(f"{label} must be a nonempty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def _positive_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelDiagnosticFigureError(f"{label} must be a positive integer.")
    return value


def load_model_diagnostic_figure_config(
    path: str | Path = DEFAULT_FIGURE_CONFIG,
) -> ModelDiagnosticFigureConfig:
    """Load the prespecified figure contract and fail closed on drift."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _exact_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "state",
            "paths",
            "analysis",
            "forest",
            "pilot_maps",
        },
        label="model diagnostic figure config",
    )
    if (
        raw["schema_version"] != FIGURE_SCHEMA_VERSION
        or raw["algorithm_version"] != FIGURE_ALGORITHM_VERSION
        or raw["state"] != FIGURE_STATE
    ):
        raise ModelDiagnosticFigureError("Figure config identity drifted.")
    paths = _exact_keys(
        raw["paths"],
        {
            "initial_provenance",
            "primary_bootstrap",
            "endpoint_provenance",
            "hotspot_summary",
            "sensor_summary",
            "qa_provenance",
            "qa_cohort_bootstrap",
            "worst_dates",
            "residual_provenance",
            "target_build_progress",
            "oof_predictions",
            "model_ready_targets",
            "date_summary",
            "tract_manifest",
            "figure_output_directory",
            "table_output_directory",
        },
        label="paths",
    )
    analysis = _exact_keys(
        raw["analysis"],
        {
            "final_test_year",
            "final_test_locked",
            "family",
            "baseline_model_id",
            "target_model_id",
            "sensors",
            "expected_bootstrap_replicates",
            "worst_date_limit",
            "figure_dpi",
        },
        label="analysis",
    )
    forest = _exact_keys(raw["forest"], {"cohorts"}, label="forest")
    pilot_maps = _exact_keys(
        raw["pilot_maps"],
        {"dates", "date_selection", "geometry_role"},
        label="pilot_maps",
    )
    if (
        analysis["final_test_year"] != 2025
        or analysis["final_test_locked"] is not True
        or analysis["family"] != "joint"
        or analysis["baseline_model_id"] != "B1"
        or analysis["target_model_id"] != "M2"
        or analysis["sensors"] != ["landsat-8", "landsat-9"]
    ):
        raise ModelDiagnosticFigureError("Joint B1/M2 comparison or 2025 lock drifted.")
    replicates = _positive_integer(
        analysis["expected_bootstrap_replicates"], label="bootstrap replicates"
    )
    limit = _positive_integer(analysis["worst_date_limit"], label="worst-date limit")
    dpi = _positive_integer(analysis["figure_dpi"], label="figure DPI")
    if replicates != 5_000 or limit != 10 or dpi < 180:
        raise ModelDiagnosticFigureError(
            "Frozen bootstrap, worst-date, or resolution contract drifted."
        )
    cohorts_raw = forest["cohorts"]
    if not isinstance(cohorts_raw, list):
        raise ModelDiagnosticFigureError("forest.cohorts must be an ordered list.")
    cohorts: list[ForestCohort] = []
    for index, item in enumerate(cohorts_raw):
        parsed = _exact_keys(
            item,
            {"dimension", "label", "display_label", "group"},
            label=f"forest cohort {index}",
        )
        if not all(isinstance(parsed[key], str) and parsed[key] for key in parsed):
            raise ModelDiagnosticFigureError("Forest cohort fields must be nonempty strings.")
        cohorts.append(ForestCohort(**parsed))
    observed = tuple(
        (item.dimension, item.label, item.display_label, item.group) for item in cohorts
    )
    if observed != EXPECTED_FOREST_COHORTS:
        raise ModelDiagnosticFigureError("Prespecified QA forest cohorts or labels drifted.")
    pilot_dates = tuple(pd.Timestamp(value) for value in pilot_maps["dates"])
    if (
        pilot_dates
        != (
            pd.Timestamp("2024-06-20"),
            pd.Timestamp("2024-08-23"),
            pd.Timestamp("2024-10-10"),
        )
        or pilot_maps["date_selection"] != "protocol_fixed_not_score_selected"
        or pilot_maps["geometry_role"] != "diagnostic_only_never_predictor"
    ):
        raise ModelDiagnosticFigureError("Fixed pilot-map dates or geometry role drifted.")
    return ModelDiagnosticFigureConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        initial_provenance=_resolve_path(
            paths["initial_provenance"], label="initial provenance"
        ),
        primary_bootstrap=_resolve_path(
            paths["primary_bootstrap"], label="primary bootstrap"
        ),
        endpoint_provenance=_resolve_path(
            paths["endpoint_provenance"], label="endpoint provenance"
        ),
        hotspot_summary=_resolve_path(paths["hotspot_summary"], label="hotspot summary"),
        sensor_summary=_resolve_path(paths["sensor_summary"], label="sensor summary"),
        qa_provenance=_resolve_path(paths["qa_provenance"], label="QA provenance"),
        qa_cohort_bootstrap=_resolve_path(
            paths["qa_cohort_bootstrap"], label="QA cohort bootstrap"
        ),
        worst_dates=_resolve_path(paths["worst_dates"], label="worst dates"),
        residual_provenance=_resolve_path(
            paths["residual_provenance"], label="residual spatial provenance"
        ),
        target_build_progress=_resolve_path(
            paths["target_build_progress"], label="target build progress"
        ),
        oof_predictions=_resolve_path(
            paths["oof_predictions"], label="OOF predictions"
        ),
        model_ready_targets=_resolve_path(
            paths["model_ready_targets"], label="model-ready targets"
        ),
        date_summary=_resolve_path(paths["date_summary"], label="date summary"),
        tract_manifest=_resolve_path(paths["tract_manifest"], label="tract manifest"),
        figure_output_directory=_resolve_path(
            paths["figure_output_directory"], label="figure output directory"
        ),
        table_output_directory=_resolve_path(
            paths["table_output_directory"], label="table output directory"
        ),
        final_test_year=2025,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        sensors=("landsat-8", "landsat-9"),
        expected_bootstrap_replicates=replicates,
        worst_date_limit=limit,
        figure_dpi=dpi,
        forest_cohorts=tuple(cohorts),
        pilot_dates=pilot_dates,
    )


def _read_provenance(path: Path, *, label: str) -> tuple[dict[str, Any], str, str]:
    file_sha = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelDiagnosticFigureError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != file_sha or not isinstance(payload, dict):
        raise ModelDiagnosticFigureError(f"{label} changed or is not a JSON object.")
    working = dict(payload)
    commit = working.pop("commit_sha256", None)
    if not isinstance(commit, str) or canonical_sha256(working) != commit:
        raise ModelDiagnosticFigureError(f"{label} provenance commit is invalid.")
    _validate_locked_provenance(payload, label=label)
    return payload, file_sha, commit


def _recursive_true(payload: object, keys: set[str]) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value is True:
                return True
            if _recursive_true(value, keys):
                return True
    elif isinstance(payload, list):
        return any(_recursive_true(item, keys) for item in payload)
    return False


def _validate_locked_provenance(payload: dict[str, Any], *, label: str) -> None:
    if (
        payload.get("state") != "complete"
        or payload.get("final_test_year") != 2025
        or payload.get("final_test_locked") is not True
        or payload.get("contains_final_test_year") is not False
        or _recursive_true(payload, {"final_test_unlocked", "unlock_final_test"})
    ):
        raise ModelDiagnosticFigureError(
            f"{label} contains or unlocks the 2025 final test, or is incomplete."
        )
    scope = payload.get("analysis_scope")
    if not isinstance(scope, str) or "2020_2024" not in scope or "locked" not in scope:
        raise ModelDiagnosticFigureError(f"{label} development-only scope is invalid.")


def _read_locked_csv(
    path: Path,
    provenance: dict[str, Any],
    *,
    label: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_files = provenance.get("output_files")
    if not isinstance(output_files, dict):
        raise ModelDiagnosticFigureError(f"{label} upstream output locks are missing.")
    record = output_files.get(path.name)
    if not isinstance(record, dict):
        raise ModelDiagnosticFigureError(f"{label} byte lock is missing.")
    if record.get("path") != path.name or record.get("path_base") != "output_directory":
        raise ModelDiagnosticFigureError(f"{label} locked path is inconsistent.")
    expected_sha = record.get("sha256")
    expected_bytes = record.get("bytes")
    expected_rows = record.get("rows")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or not path.is_file()
        or path.stat().st_size != expected_bytes
        or sha256_file(path) != expected_sha
    ):
        raise ModelDiagnosticFigureError(f"{label} byte lock failed before CSV read.")
    frame = pd.read_csv(path)
    if sha256_file(path) != expected_sha or len(frame) != expected_rows:
        raise ModelDiagnosticFigureError(f"{label} changed during read or row count drifted.")
    return frame, {
        "path": path.as_posix(),
        "sha256": expected_sha,
        "bytes": expected_bytes,
        "rows": expected_rows,
    }


def _read_byte_locked_json(
    path: Path,
    expected_sha256: object,
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(expected_sha256, str) or not path.is_file():
        raise ModelDiagnosticFigureError(f"{label} byte lock is missing.")
    before = sha256_file(path)
    if before != expected_sha256:
        raise ModelDiagnosticFigureError(f"{label} byte lock failed before JSON read.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelDiagnosticFigureError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise ModelDiagnosticFigureError(f"{label} changed or is not a JSON object.")
    return payload, {
        "path": path.as_posix(),
        "sha256": before,
        "bytes": path.stat().st_size,
    }


def _read_locked_parquet(
    path: Path,
    expected_sha256: object,
    *,
    label: str,
    lock_record: object | None = None,
    geospatial: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not isinstance(expected_sha256, str) or not path.is_file():
        raise ModelDiagnosticFigureError(f"{label} byte lock is missing.")
    if isinstance(lock_record, dict):
        if lock_record.get("sha256") != expected_sha256:
            raise ModelDiagnosticFigureError(f"{label} upstream hashes disagree.")
        expected_bytes = lock_record.get("bytes")
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int):
            raise ModelDiagnosticFigureError(f"{label} byte count lock is invalid.")
        if path.stat().st_size != expected_bytes:
            raise ModelDiagnosticFigureError(f"{label} byte count lock failed.")
    before = sha256_file(path)
    if before != expected_sha256:
        raise ModelDiagnosticFigureError(f"{label} byte lock failed before Parquet read.")
    frame = gpd.read_parquet(path) if geospatial else pd.read_parquet(path)
    if sha256_file(path) != before:
        raise ModelDiagnosticFigureError(f"{label} changed during Parquet read.")
    if isinstance(lock_record, dict) and len(frame) != int(lock_record.get("rows", -1)):
        raise ModelDiagnosticFigureError(f"{label} row-count lock failed.")
    return frame, {
        "path": path.as_posix(),
        "sha256": before,
        "bytes": path.stat().st_size,
        "rows": len(frame),
    }


def _bit_exact(left: pd.Series, right: pd.Series) -> bool:
    left_values = left.to_numpy(dtype=np.float64)
    right_values = right.to_numpy(dtype=np.float64)
    return np.array_equal(left_values.view(np.uint64), right_values.view(np.uint64))


def _build_pilot_map_inputs(
    config: ModelDiagnosticFigureConfig,
    *,
    initial: dict[str, Any],
    endpoint: dict[str, Any],
    residual: dict[str, Any],
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, dict[str, Any]]:
    initial_auth = initial.get("input_authentication")
    endpoint_auth = endpoint.get("input_authentication")
    residual_auth = residual.get("input_authentication")
    if not all(isinstance(item, dict) for item in (initial_auth, endpoint_auth, residual_auth)):
        raise ModelDiagnosticFigureError("Pilot-map upstream authentication records are missing.")
    assert isinstance(initial_auth, dict)
    assert isinstance(endpoint_auth, dict)
    assert isinstance(residual_auth, dict)
    oof_hashes = {
        initial_auth.get("oof_predictions_sha256"),
        endpoint_auth.get("oof_predictions_sha256"),
        residual_auth.get("oof_predictions_sha256"),
    }
    if len(oof_hashes) != 1 or None in oof_hashes:
        raise ModelDiagnosticFigureError("Pilot-map OOF hashes do not share one lineage.")

    progress, progress_record = _read_byte_locked_json(
        config.target_build_progress,
        endpoint_auth.get("target_progress_sha256"),
        label="target build progress",
    )
    if (
        progress.get("state") != "model_ready"
        or progress.get("build_complete") is not True
        or progress.get("promoted_outputs_valid") is not True
    ):
        raise ModelDiagnosticFigureError("Target build is not the frozen model-ready state.")
    locks = progress.get("aggregate_outputs")
    if not isinstance(locks, dict):
        raise ModelDiagnosticFigureError("Target aggregate output locks are missing.")
    target_lock = locks.get(config.model_ready_targets.name)
    date_lock = locks.get(config.date_summary.name)
    target_hash = endpoint_auth.get("model_ready_target_sha256")
    if target_hash != residual_auth.get("target_manifest_sha256"):
        raise ModelDiagnosticFigureError("Target hashes disagree across diagnostics.")
    targets, target_record = _read_locked_parquet(
        config.model_ready_targets,
        target_hash,
        label="model-ready target table",
        lock_record=target_lock,
    )
    dates, date_record = _read_locked_parquet(
        config.date_summary,
        endpoint_auth.get("date_summary_sha256"),
        label="date summary",
        lock_record=date_lock,
    )
    oof, oof_record = _read_locked_parquet(
        config.oof_predictions,
        next(iter(oof_hashes)),
        label="OOF predictions",
    )
    geometry_frame, geometry_record = _read_locked_parquet(
        config.tract_manifest,
        residual_auth.get("tract_manifest_sha256"),
        label="primary tract geometry",
        geospatial=True,
    )
    if not isinstance(geometry_frame, gpd.GeoDataFrame):
        raise ModelDiagnosticFigureError("Primary tract manifest is not geospatial.")

    for frame, label in ((oof, "OOF"), (targets, "target"), (dates, "date summary")):
        if "target_date" not in frame:
            raise ModelDiagnosticFigureError(f"{label} table lacks target_date.")
        parsed = pd.to_datetime(frame["target_date"], errors="raise")
        if parsed.dt.year.ge(config.final_test_year).any():
            raise ModelDiagnosticFigureError(f"{label} table contains locked 2025 rows.")

    target_required = {
        "tract_geoid",
        "target_date",
        "target_lst_c",
        "median_st_uncertainty_k",
    }
    oof_required = {"tract_geoid", "target_date", "family", "model_id", "y_true", "y_pred"}
    date_required = {"target_date", "relative_endpoint_coverage_pass"}
    geometry_required = {"GEOID", "geometry", "primary_included"}
    if not target_required.issubset(targets):
        raise ModelDiagnosticFigureError("Model-ready target map fields are incomplete.")
    if not oof_required.issubset(oof):
        raise ModelDiagnosticFigureError("OOF pilot-map fields are incomplete.")
    if not date_required.issubset(dates):
        raise ModelDiagnosticFigureError("Date-summary gate field is missing.")
    if not geometry_required.issubset(geometry_frame):
        raise ModelDiagnosticFigureError("Primary tract geometry fields are incomplete.")

    pilot_set = set(config.pilot_dates)
    oof = oof.loc[
        oof["family"].eq(config.family)
        & oof["model_id"].eq(config.target_model_id)
        & pd.to_datetime(oof["target_date"]).isin(pilot_set),
        ["tract_geoid", "target_date", "y_true", "y_pred"],
    ].copy()
    targets = targets.loc[
        pd.to_datetime(targets["target_date"]).isin(pilot_set),
        ["tract_geoid", "target_date", "target_lst_c", "median_st_uncertainty_k"],
    ].copy()
    for frame in (oof, targets):
        frame["tract_geoid"] = frame["tract_geoid"].astype(str).str.zfill(11)
        frame["target_date"] = pd.to_datetime(frame["target_date"], errors="raise")
        if frame.duplicated(["tract_geoid", "target_date"]).any():
            raise ModelDiagnosticFigureError("Pilot-map tract-date keys are duplicated.")
    paired = oof.merge(
        targets,
        on=["tract_geoid", "target_date"],
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    if not paired["_merge"].eq("both").all() or not _bit_exact(
        paired["y_true"], paired["target_lst_c"]
    ):
        raise ModelDiagnosticFigureError(
            "Pilot-map OOF and target keys/values do not match exactly."
        )
    paired = paired.drop(columns=["_merge", "target_lst_c"])
    if set(pd.to_datetime(paired["target_date"]).tolist()) != pilot_set:
        raise ModelDiagnosticFigureError("One or more protocol-fixed pilot dates are absent.")
    numeric = paired[["y_true", "y_pred", "median_st_uncertainty_k"]].apply(
        pd.to_numeric, errors="raise"
    )
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ModelDiagnosticFigureError("Pilot-map values contain nonfinite numbers.")
    paired["residual_c"] = paired["y_pred"] - paired["y_true"]

    date_gates = dates.loc[
        pd.to_datetime(dates["target_date"]).isin(pilot_set),
        ["target_date", "relative_endpoint_coverage_pass"],
    ].copy()
    date_gates["target_date"] = pd.to_datetime(date_gates["target_date"], errors="raise")
    if (
        len(date_gates) != len(config.pilot_dates)
        or date_gates["target_date"].duplicated().any()
        or set(date_gates["target_date"]) != pilot_set
    ):
        raise ModelDiagnosticFigureError("Pilot-date relative-endpoint gates are incomplete.")
    date_gates["relative_endpoint_coverage_pass"] = date_gates[
        "relative_endpoint_coverage_pass"
    ].astype(bool)

    geometry = geometry_frame.loc[
        geometry_frame["primary_included"].eq(True), ["GEOID", "geometry"]  # noqa: E712
    ].copy()
    geometry = geometry.rename(columns={"GEOID": "tract_geoid"})
    geometry["tract_geoid"] = geometry["tract_geoid"].astype(str).str.zfill(11)
    if geometry.crs is None or geometry["tract_geoid"].duplicated().any() or geometry.empty:
        raise ModelDiagnosticFigureError(
            "Primary map geometry is empty, duplicated, or unprojected."
        )
    date_frame = pd.DataFrame({"target_date": list(config.pilot_dates), "_join": 1})
    geometry["_join"] = 1
    map_frame = geometry.merge(date_frame, on="_join", validate="many_to_many").drop(
        columns="_join"
    )
    map_frame = map_frame.merge(
        paired,
        on=["tract_geoid", "target_date"],
        how="left",
        validate="one_to_one",
    )
    map_frame = gpd.GeoDataFrame(map_frame, geometry="geometry", crs=geometry.crs)
    if len(map_frame) != len(geometry) * len(config.pilot_dates):
        raise ModelDiagnosticFigureError("Pilot-map geometry expansion cardinality drifted.")
    return map_frame, date_gates, {
        "target_build_progress": progress_record,
        "oof_predictions": oof_record,
        "model_ready_targets": target_record,
        "date_summary": date_record,
        "tract_manifest": geometry_record,
    }


def _finite_columns(frame: pd.DataFrame, columns: list[str], *, label: str) -> None:
    if not set(columns).issubset(frame):
        raise ModelDiagnosticFigureError(f"{label} lacks required numeric fields.")
    values = frame[columns].apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ModelDiagnosticFigureError(f"{label} contains nonfinite values.")


def _validate_primary(frame: pd.DataFrame, config: ModelDiagnosticFigureConfig) -> pd.DataFrame:
    required = {
        "family",
        "target_model_id",
        "strongest_legal_baseline_model_id",
        "bootstrap_replicates",
        "random_row_sampling_used",
        "baseline_point_mae_c",
        "target_model_point_mae_c",
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
        "independent_date_count",
        "independent_spatial_block_count",
        "tract_date_row_count",
    }
    if not required.issubset(frame):
        raise ModelDiagnosticFigureError("Primary bootstrap schema is incomplete.")
    selected = frame.loc[
        frame["family"].eq(config.family)
        & frame["target_model_id"].eq(config.target_model_id)
        & frame["strongest_legal_baseline_model_id"].eq(config.baseline_model_id)
    ].copy()
    if len(selected) != 1:
        raise ModelDiagnosticFigureError("Primary joint B1/M2 bootstrap row is not unique.")
    row = selected.iloc[0]
    random_rows = str(row["random_row_sampling_used"]).strip().lower()
    if (
        int(row["bootstrap_replicates"]) != config.expected_bootstrap_replicates
        or random_rows not in {"false", "0"}
    ):
        raise ModelDiagnosticFigureError("Primary bootstrap contract drifted.")
    numeric = [
        "baseline_point_mae_c",
        "target_model_point_mae_c",
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
    ]
    _finite_columns(selected, numeric, label="primary bootstrap")
    lower = float(row["relative_mae_improvement_ci_lower_percent"])
    point = float(row["relative_mae_improvement_percent"])
    upper = float(row["relative_mae_improvement_ci_upper_percent"])
    if (
        float(row["baseline_point_mae_c"]) < 0
        or float(row["target_model_point_mae_c"]) < 0
        or not lower <= point <= upper
    ):
        raise ModelDiagnosticFigureError("Primary bootstrap values are incoherent.")
    return selected.reset_index(drop=True)


def _validate_hotspot(frame: pd.DataFrame, config: ModelDiagnosticFigureConfig) -> pd.DataFrame:
    required = {
        "family",
        "model_id",
        "mean_per_date_average_precision",
        "mean_per_date_recall_at_k",
        "independent_date_count",
    }
    if not required.issubset(frame):
        raise ModelDiagnosticFigureError("Hotspot summary schema is incomplete.")
    selected = frame.loc[
        frame["family"].eq(config.family)
        & frame["model_id"].isin([config.baseline_model_id, config.target_model_id])
    ].copy()
    if len(selected) != 2 or set(selected["model_id"]) != {
        config.baseline_model_id,
        config.target_model_id,
    }:
        raise ModelDiagnosticFigureError("Hotspot B1/M2 rows are not unique.")
    metrics = ["mean_per_date_average_precision", "mean_per_date_recall_at_k"]
    _finite_columns(selected, metrics, label="hotspot summary")
    if not ((selected[metrics] >= 0) & (selected[metrics] <= 1)).all().all():
        raise ModelDiagnosticFigureError("Hotspot metrics fall outside [0, 1].")
    return selected.reset_index(drop=True)


def _validate_sensor(frame: pd.DataFrame, config: ModelDiagnosticFigureConfig) -> pd.DataFrame:
    required = {
        "family",
        "model_id",
        "platform",
        "equal_date_weighted_mae_c",
        "independent_date_count",
    }
    if not required.issubset(frame):
        raise ModelDiagnosticFigureError("Sensor summary schema is incomplete.")
    selected = frame.loc[
        frame["family"].eq(config.family)
        & frame["model_id"].isin([config.baseline_model_id, config.target_model_id])
        & frame["platform"].isin(config.sensors)
    ].copy()
    expected = {
        (model, sensor)
        for model in (config.baseline_model_id, config.target_model_id)
        for sensor in config.sensors
    }
    observed = set(zip(selected["model_id"], selected["platform"], strict=False))
    if len(selected) != 4 or observed != expected:
        raise ModelDiagnosticFigureError("Sensor B1/M2 by-platform rows are incomplete.")
    _finite_columns(selected, ["equal_date_weighted_mae_c"], label="sensor summary")
    if (selected["equal_date_weighted_mae_c"] < 0).any():
        raise ModelDiagnosticFigureError("Sensor MAE cannot be negative.")
    return selected.reset_index(drop=True)


def _select_forest(
    frame: pd.DataFrame, config: ModelDiagnosticFigureConfig
) -> pd.DataFrame:
    required = {
        "cohort_dimension",
        "cohort_label",
        "baseline_model_id",
        "target_model_id",
        "bootstrap_replicates",
        "random_row_sampling_used",
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
        "tract_date_row_count",
        "independent_date_count",
        "independent_spatial_block_count",
    }
    if not required.issubset(frame):
        raise ModelDiagnosticFigureError("QA cohort bootstrap schema is incomplete.")
    records: list[pd.Series] = []
    for order, cohort in enumerate(config.forest_cohorts):
        rows = frame.loc[
            frame["cohort_dimension"].eq(cohort.dimension)
            & frame["cohort_label"].eq(cohort.label)
            & frame["baseline_model_id"].eq(config.baseline_model_id)
            & frame["target_model_id"].eq(config.target_model_id)
        ]
        if len(rows) != 1:
            raise ModelDiagnosticFigureError(
                f"Forest cohort is missing or duplicated: {cohort.dimension}/{cohort.label}."
            )
        row = rows.iloc[0].copy()
        if (
            int(row["bootstrap_replicates"]) != config.expected_bootstrap_replicates
            or str(row["random_row_sampling_used"]).strip().lower() not in {"false", "0"}
        ):
            raise ModelDiagnosticFigureError("QA crossed-bootstrap contract drifted.")
        row["display_label"] = cohort.display_label
        row["display_group"] = cohort.group
        row["display_order"] = order
        records.append(row)
    selected = pd.DataFrame(records).reset_index(drop=True)
    numeric = [
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
        "tract_date_row_count",
        "independent_date_count",
        "independent_spatial_block_count",
    ]
    _finite_columns(selected, numeric, label="selected QA cohorts")
    lower = selected["relative_mae_improvement_ci_lower_percent"].astype(float)
    point = selected["relative_mae_improvement_percent"].astype(float)
    upper = selected["relative_mae_improvement_ci_upper_percent"].astype(float)
    if not ((lower <= point) & (point <= upper)).all():
        raise ModelDiagnosticFigureError("QA cohort confidence intervals are incoherent.")
    return selected


def _validate_worst_dates(
    frame: pd.DataFrame, config: ModelDiagnosticFigureConfig
) -> pd.DataFrame:
    required = {
        "target_date",
        "platform",
        "b1_mae_c",
        "m2_mae_c",
        "m2_minus_b1_mae_c",
        "m2_bias_c",
        "m2_underprediction_fraction",
        "tract_date_row_count",
        "independent_spatial_block_count",
    }
    if not required.issubset(frame):
        raise ModelDiagnosticFigureError("Worst-date table schema is incomplete.")
    selected = frame.head(config.worst_date_limit).copy()
    if len(selected) != config.worst_date_limit:
        raise ModelDiagnosticFigureError("Worst-date table is shorter than the frozen limit.")
    selected["target_date"] = pd.to_datetime(selected["target_date"], errors="raise")
    if (
        selected["target_date"].dt.year.ge(config.final_test_year).any()
        or not selected["platform"].isin(config.sensors).all()
    ):
        raise ModelDiagnosticFigureError("Worst-date input contains 2025 or an unknown sensor.")
    numeric = [
        "b1_mae_c",
        "m2_mae_c",
        "m2_minus_b1_mae_c",
        "m2_bias_c",
        "m2_underprediction_fraction",
    ]
    _finite_columns(selected, numeric, label="worst dates")
    if (
        (selected[["b1_mae_c", "m2_mae_c"]] < 0).any().any()
        or not selected["m2_mae_c"].is_monotonic_decreasing
        or not np.allclose(
            selected["m2_minus_b1_mae_c"],
            selected["m2_mae_c"] - selected["b1_mae_c"],
            rtol=1e-10,
            atol=1e-10,
        )
        or not selected["m2_underprediction_fraction"].between(0, 1).all()
    ):
        raise ModelDiagnosticFigureError("Worst-date ranking or error values are incoherent.")
    return selected.reset_index(drop=True)


def authenticate_figure_inputs(
    config: ModelDiagnosticFigureConfig,
) -> AuthenticatedFigureInputs:
    """Authenticate every upstream provenance and byte-lock each CSV before reading."""

    initial, initial_sha, initial_commit = _read_provenance(
        config.initial_provenance, label="initial model-result"
    )
    endpoint, endpoint_sha, endpoint_commit = _read_provenance(
        config.endpoint_provenance, label="endpoint diagnostic"
    )
    qa, qa_sha, qa_commit = _read_provenance(config.qa_provenance, label="QA diagnostic")
    residual, residual_sha, residual_commit = _read_provenance(
        config.residual_provenance, label="residual spatial diagnostic"
    )

    compile_commits = {
        initial.get("compile_provenance_commit_sha256"),
        endpoint.get("compile_provenance_commit_sha256"),
        (qa.get("input_authentication") or {}).get("compile_provenance_commit_sha256")
        if isinstance(qa.get("input_authentication"), dict)
        else None,
        (residual.get("input_authentication") or {}).get(
            "compile_provenance_commit_sha256"
        )
        if isinstance(residual.get("input_authentication"), dict)
        else None,
    }
    oof_hashes = {
        (initial.get("input_authentication") or {}).get("oof_predictions_sha256")
        if isinstance(initial.get("input_authentication"), dict)
        else None,
        (endpoint.get("input_authentication") or {}).get("oof_predictions_sha256")
        if isinstance(endpoint.get("input_authentication"), dict)
        else None,
        (qa.get("input_authentication") or {}).get("oof_predictions_sha256")
        if isinstance(qa.get("input_authentication"), dict)
        else None,
        (residual.get("input_authentication") or {}).get("oof_predictions_sha256")
        if isinstance(residual.get("input_authentication"), dict)
        else None,
    }
    if (
        len(compile_commits) != 1
        or None in compile_commits
        or len(oof_hashes) != 1
        or None in oof_hashes
    ):
        raise ModelDiagnosticFigureError(
            "Upstream diagnostics do not share one frozen OOF lineage."
        )

    primary, primary_record = _read_locked_csv(
        config.primary_bootstrap, initial, label="primary bootstrap"
    )
    hotspot, hotspot_record = _read_locked_csv(
        config.hotspot_summary, endpoint, label="hotspot summary"
    )
    sensor, sensor_record = _read_locked_csv(
        config.sensor_summary, endpoint, label="sensor summary"
    )
    forest, forest_record = _read_locked_csv(
        config.qa_cohort_bootstrap, qa, label="QA cohort bootstrap"
    )
    worst, worst_record = _read_locked_csv(
        config.worst_dates, qa, label="worst dates"
    )

    primary = _validate_primary(primary, config)
    hotspot = _validate_hotspot(hotspot, config)
    sensor = _validate_sensor(sensor, config)
    forest = _select_forest(forest, config)
    worst = _validate_worst_dates(worst, config)
    pilot_map, pilot_dates, map_records = _build_pilot_map_inputs(
        config,
        initial=initial,
        endpoint=endpoint,
        residual=residual,
    )
    return AuthenticatedFigureInputs(
        primary_bootstrap=primary,
        hotspot_summary=hotspot,
        sensor_summary=sensor,
        qa_cohort_bootstrap=forest,
        worst_dates=worst,
        pilot_map_data=pilot_map,
        pilot_date_summary=pilot_dates,
        input_authentication={
            "upstream_provenance": {
                "initial": {
                    "path": config.initial_provenance.as_posix(),
                    "file_sha256": initial_sha,
                    "commit_sha256": initial_commit,
                },
                "endpoint": {
                    "path": config.endpoint_provenance.as_posix(),
                    "file_sha256": endpoint_sha,
                    "commit_sha256": endpoint_commit,
                },
                "qa": {
                    "path": config.qa_provenance.as_posix(),
                    "file_sha256": qa_sha,
                    "commit_sha256": qa_commit,
                },
                "residual_spatial": {
                    "path": config.residual_provenance.as_posix(),
                    "file_sha256": residual_sha,
                    "commit_sha256": residual_commit,
                },
            },
            "input_tables": {
                "primary_bootstrap": primary_record,
                "hotspot_summary": hotspot_record,
                "sensor_summary": sensor_record,
                "qa_cohort_bootstrap": forest_record,
                "worst_dates": worst_record,
                **map_records,
            },
            "compile_provenance_commit_sha256": next(iter(compile_commits)),
            "oof_predictions_sha256": next(iter(oof_hashes)),
        },
    )


def _style() -> dict[str, Any]:
    return {
        "font.family": "DejaVu Sans",
        "font.size": 9.0,
        "axes.titlesize": 11.0,
        "axes.labelsize": 9.0,
        "xtick.labelsize": 8.0,
        "ytick.labelsize": 8.0,
        "legend.fontsize": 8.0,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": MUTED_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "text.color": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    }


def _label_bars(axis: plt.Axes, bars: Any, *, suffix: str, decimals: int = 2) -> None:
    for bar in bars:
        height = float(bar.get_height())
        axis.annotate(
            f"{height:.{decimals}f}{suffix}",
            (bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7.5,
        )


def _render_overview(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
) -> plt.Figure:
    primary = inputs.primary_bootstrap.iloc[0]
    hot = inputs.hotspot_summary.set_index("model_id")
    sensor = inputs.sensor_summary.set_index(["model_id", "platform"])
    models = [config.baseline_model_id, config.target_model_id]
    colors = [BASELINE_COLOR, TARGET_COLOR]
    with plt.rc_context(_style()):
        fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.8))
        fig.subplots_adjust(left=0.06, right=0.985, top=0.82, bottom=0.22, wspace=0.35)
        fig.suptitle(
            "Development OOF performance for neighborhood-scale surface LST",
            x=0.06,
            ha="left",
            fontsize=14,
            fontweight="bold",
        )

        mae = [float(primary["baseline_point_mae_c"]), float(primary["target_model_point_mae_c"])]
        bars = axes[0].bar(models, mae, color=colors, width=0.62)
        _label_bars(axes[0], bars, suffix=" °C")
        axes[0].set_ylim(0, max(mae) * 1.48)
        axes[0].set_ylabel("Equal-date-weighted MAE (°C)")
        axes[0].set_title("A  Primary joint split", loc="left", fontweight="bold")
        axes[0].grid(axis="y", color=GRID_COLOR, linewidth=0.7)
        axes[0].set_axisbelow(True)
        axes[0].text(
            0.5,
            max(mae) * 1.31,
            (
                f"M2 improvement: {float(primary['relative_mae_improvement_percent']):.1f}%\n"
                "crossed date × block 95% CI: "
                f"{float(primary['relative_mae_improvement_ci_lower_percent']):.1f}% to "
                f"{float(primary['relative_mae_improvement_ci_upper_percent']):.1f}%"
            ),
            ha="center",
            va="top",
            fontsize=8,
        )

        metrics = [
            ("mean_per_date_average_precision", "Average\nprecision"),
            ("mean_per_date_recall_at_k", "Recall at exact\ntop 20%"),
        ]
        x = np.arange(len(metrics), dtype=float)
        width = 0.34
        for offset, model, color in zip((-width / 2, width / 2), models, colors, strict=True):
            values = [float(hot.loc[model, field]) for field, _ in metrics]
            bars = axes[1].bar(x + offset, values, width=width, color=color, label=model)
            _label_bars(axes[1], bars, suffix="", decimals=2)
        axes[1].set_xticks(x, [label for _, label in metrics])
        axes[1].set_ylim(0, 0.82)
        axes[1].set_ylabel("Mean across gated dates")
        axes[1].set_title("B  Relative hotspot endpoint", loc="left", fontweight="bold")
        axes[1].grid(axis="y", color=GRID_COLOR, linewidth=0.7)
        axes[1].set_axisbelow(True)

        sensor_labels = ["Landsat 8", "Landsat 9"]
        x = np.arange(len(config.sensors), dtype=float)
        for offset, model, color in zip((-width / 2, width / 2), models, colors, strict=True):
            values = [
                float(sensor.loc[(model, platform), "equal_date_weighted_mae_c"])
                for platform in config.sensors
            ]
            bars = axes[2].bar(x + offset, values, width=width, color=color, label=model)
            _label_bars(axes[2], bars, suffix="", decimals=2)
        axes[2].set_xticks(x, sensor_labels)
        axes[2].set_ylim(0, max(float(sensor["equal_date_weighted_mae_c"].max()) * 1.25, 3.0))
        axes[2].set_ylabel("Equal-date-weighted MAE (°C)")
        axes[2].set_title("C  Sensor-stratified error", loc="left", fontweight="bold")
        axes[2].grid(axis="y", color=GRID_COLOR, linewidth=0.7)
        axes[2].set_axisbelow(True)

        handles = [
            plt.Rectangle((0, 0), 1, 1, color=BASELINE_COLOR),
            plt.Rectangle((0, 0), 1, 1, color=TARGET_COLOR),
        ]
        fig.legend(
            handles,
            models,
            loc="upper right",
            bbox_to_anchor=(0.98, 0.925),
            ncol=2,
            frameon=False,
        )
        fig.text(
            0.06,
            0.075,
            (
                "2020–2024 grouped out-of-fold predictions only; 2025 remains locked. "
                "MAE is surface-temperature error, not human heat exposure. "
                "Hotspot metrics average 34 coverage-gated dates."
            ),
            ha="left",
            va="bottom",
            fontsize=8,
            color=MUTED_COLOR,
        )
        return fig


def _forest_positions(groups: list[str]) -> tuple[np.ndarray, list[float]]:
    positions: list[float] = []
    separators: list[float] = []
    cursor = 0.0
    for index, group in enumerate(groups):
        if index and group != groups[index - 1]:
            separators.append(cursor - 0.35)
            cursor += 0.45
        positions.append(cursor)
        cursor += 1.0
    return np.asarray(positions), separators


def _render_forest(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
) -> plt.Figure:
    data = inputs.qa_cohort_bootstrap
    point = data["relative_mae_improvement_percent"].to_numpy(dtype=float)
    lower = data["relative_mae_improvement_ci_lower_percent"].to_numpy(dtype=float)
    upper = data["relative_mae_improvement_ci_upper_percent"].to_numpy(dtype=float)
    positions, separators = _forest_positions(data["display_group"].astype(str).tolist())
    labels = [
        (
            f"{row.display_label}\n  n={int(row.tract_date_row_count):,}; "
            f"{int(row.independent_date_count)} dates"
        )
        for row in data.itertuples(index=False)
    ]
    with plt.rc_context(_style()):
        fig, (axis, text_axis) = plt.subplots(
            1,
            2,
            figsize=(12.8, 8.3),
            sharey=True,
            gridspec_kw={"width_ratios": [4.25, 1.55], "wspace": 0.03},
        )
        fig.subplots_adjust(left=0.37, right=0.985, top=0.78, bottom=0.19)
        fig.suptitle(
            "M2 relative MAE improvement over B1\nacross QA and missingness cohorts",
            x=0.5,
            y=0.97,
            ha="center",
            fontsize=14,
            fontweight="bold",
        )
        x_error = np.vstack([point - lower, upper - point])
        ordinary = ~data["cohort_label"].eq("tract_median_le_2k").to_numpy()
        axis.errorbar(
            point[ordinary],
            positions[ordinary],
            xerr=x_error[:, ordinary],
            fmt="o",
            color=BASELINE_COLOR,
            ecolor=BASELINE_COLOR,
            elinewidth=1.5,
            capsize=3,
            markersize=5,
            zorder=3,
        )
        focus = ~ordinary
        axis.errorbar(
            point[focus],
            positions[focus],
            xerr=x_error[:, focus],
            fmt="D",
            color=FOCUS_COLOR,
            ecolor=FOCUS_COLOR,
            elinewidth=2.0,
            capsize=4,
            markersize=6,
            zorder=4,
        )
        axis.axvline(0, color=TEXT_COLOR, linewidth=1.0)
        axis.axvline(10, color=MUTED_COLOR, linewidth=1.0, linestyle="--")
        axis.annotate(
            "No-improvement line",
            xy=(0, 1.01),
            xycoords=("data", "axes fraction"),
            xytext=(-5, 0),
            textcoords="offset points",
            ha="right",
            va="bottom",
            fontsize=7.5,
        )
        axis.annotate(
            "10% protocol point threshold",
            xy=(10, 1.01),
            xycoords=("data", "axes fraction"),
            xytext=(5, 0),
            textcoords="offset points",
            ha="left",
            va="bottom",
            fontsize=7.5,
        )
        axis.set_xlim(min(-65.0, float(lower.min()) - 4), max(58.0, float(upper.max()) + 4))
        axis.set_xlabel("Relative reduction in equal-date-weighted MAE (%)")
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        axis.grid(axis="x", color=GRID_COLOR, linewidth=0.7)
        axis.set_axisbelow(True)
        for separator in separators:
            axis.axhline(separator, color=GRID_COLOR, linewidth=0.8)
            text_axis.axhline(separator, color=GRID_COLOR, linewidth=0.8)

        text_axis.set_xlim(0, 1)
        text_axis.set_xticks([])
        text_axis.tick_params(axis="y", left=False, labelleft=False)
        for spine in text_axis.spines.values():
            spine.set_visible(False)
        text_axis.text(
            0.02,
            positions[0] - 0.72,
            "Point estimate [95% crossed CI]",
            ha="left",
            va="bottom",
            fontsize=8,
            fontweight="bold",
        )
        for y, estimate, low, high in zip(positions, point, lower, upper, strict=True):
            text_axis.text(
                0.02,
                y,
                f"{estimate:.1f}%  [{low:.1f}, {high:.1f}]",
                ha="left",
                va="center",
                fontsize=8,
            )
        fig.text(
            0.12,
            0.04,
            (
                "Intervals resample complete overpass dates and spatial blocks "
                "(5,000 paired draws); rows are not resampled.\n"
                "The <=2 K row is a tract-median ST_QA summary diagnostic, "
                "not the pixel-level ST_QA hard-mask sensitivity."
            ),
            ha="left",
            va="bottom",
            fontsize=8,
            color=MUTED_COLOR,
        )
        return fig


def _render_worst_dates(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
) -> plt.Figure:
    data = inputs.worst_dates
    y = np.arange(len(data), dtype=float)
    labels = [
        f"{date:%Y-%m-%d} · {'L8' if platform == 'landsat-8' else 'L9'}"
        for date, platform in zip(data["target_date"], data["platform"], strict=True)
    ]
    b1 = data["b1_mae_c"].to_numpy(dtype=float)
    m2 = data["m2_mae_c"].to_numpy(dtype=float)
    bias = data["m2_bias_c"].to_numpy(dtype=float)
    with plt.rc_context(_style()):
        fig, (mae_axis, bias_axis) = plt.subplots(
            1,
            2,
            figsize=(12.3, 7.1),
            sharey=True,
            gridspec_kw={"width_ratios": [1.35, 1.0], "wspace": 0.12},
        )
        fig.subplots_adjust(left=0.18, right=0.98, top=0.86, bottom=0.19)
        fig.suptitle(
            "Largest M2 date-level errors reveal whole-overpass bias failures",
            x=0.18,
            ha="left",
            fontsize=14,
            fontweight="bold",
        )
        width = 0.34
        mae_axis.barh(y - width / 2, b1, height=width, color=BASELINE_COLOR, label="B1")
        mae_axis.barh(y + width / 2, m2, height=width, color=TARGET_COLOR, label="M2")
        mae_axis.set_yticks(y, labels)
        mae_axis.invert_yaxis()
        mae_axis.set_xlabel("Equal-date MAE (°C)")
        mae_axis.set_title("A  B1 vs M2", loc="left", fontweight="bold")
        mae_axis.grid(axis="x", color=GRID_COLOR, linewidth=0.7)
        mae_axis.set_axisbelow(True)
        mae_axis.legend(frameon=False, ncol=2, loc="lower right")
        for row_y, b1_value, m2_value in zip(y, b1, m2, strict=True):
            mae_axis.text(
                b1_value + 0.07,
                row_y - width / 2,
                f"{b1_value:.2f}",
                va="center",
                fontsize=7,
            )
            mae_axis.text(
                m2_value + 0.07,
                row_y + width / 2,
                f"{m2_value:.2f}",
                va="center",
                fontsize=7,
            )
        mae_axis.set_xlim(0, max(float(max(b1.max(), m2.max())) * 1.17, 1.0))

        bias_colors = [OVER_COLOR if value >= 0 else UNDER_COLOR for value in bias]
        bias_axis.barh(y, bias, height=0.48, color=bias_colors)
        bias_axis.axvline(0, color=TEXT_COLOR, linewidth=1.0)
        bias_axis.set_xlabel("M2 mean signed error (°C)")
        bias_axis.set_title("B  Error direction", loc="left", fontweight="bold")
        bias_axis.grid(axis="x", color=GRID_COLOR, linewidth=0.7)
        bias_axis.set_axisbelow(True)
        extent = max(float(np.abs(bias).max()) * 1.55, 1.0)
        bias_axis.set_xlim(-extent, extent)
        bias_axis.tick_params(axis="y", left=False, labelleft=False)
        for row_y, value in zip(y, bias, strict=True):
            direction = "over" if value >= 0 else "under"
            alignment = "left" if value >= 0 else "right"
            offset = extent * 0.025 if value >= 0 else -extent * 0.025
            bias_axis.text(
                value + offset,
                row_y,
                f"{value:+.2f} ({direction})",
                ha=alignment,
                va="center",
                fontsize=7.5,
            )
        fig.text(
            0.18,
            0.045,
            (
                "Dates are ranked by M2 MAE in the authenticated failure-case table. "
                "Signed error = prediction − observed LST.\n"
                "Positive indicates overprediction; negative indicates underprediction. "
                "Development OOF only; 2025 remains locked."
            ),
            ha="left",
            va="bottom",
            fontsize=8,
            color=MUTED_COLOR,
        )
        return fig


def _finite_range(values: np.ndarray, *, symmetric: bool = False) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if not finite.size:
        raise ModelDiagnosticFigureError("Pilot-map color scale has no finite values.")
    if symmetric:
        bound = float(np.max(np.abs(finite)))
        bound = bound if bound > 0 else 1.0
        return -bound, bound
    lower = float(np.min(finite))
    upper = float(np.max(finite))
    if lower == upper:
        return lower - 0.5, upper + 0.5
    return lower, upper


def _pilot_map_limits(data: gpd.GeoDataFrame) -> dict[str, tuple[float, float]]:
    lst_values = np.concatenate(
        [
            data["y_true"].to_numpy(dtype=float),
            data["y_pred"].to_numpy(dtype=float),
        ]
    )
    return {
        "lst_c": _finite_range(lst_values),
        "residual_c": _finite_range(
            data["residual_c"].to_numpy(dtype=float), symmetric=True
        ),
        "st_qa_k": _finite_range(
            data["median_st_uncertainty_k"].to_numpy(dtype=float)
        ),
    }


def _render_pilot_maps(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
) -> plt.Figure:
    data = inputs.pilot_map_data
    gates = inputs.pilot_date_summary.set_index("target_date")
    limits = _pilot_map_limits(data)
    columns = (
        ("y_true", "Observed LST", "inferno", limits["lst_c"]),
        ("y_pred", "Joint M2 prediction", "inferno", limits["lst_c"]),
        ("residual_c", "Residual: prediction − observed", "RdBu_r", limits["residual_c"]),
        (
            "median_st_uncertainty_k",
            "Tract median ST_QA",
            "viridis",
            limits["st_qa_k"],
        ),
    )
    bounds = data.total_bounds
    with plt.rc_context(_style()):
        fig = plt.figure(figsize=(13.5, 10.0))
        grid = fig.add_gridspec(
            4,
            4,
            height_ratios=[1, 1, 1, 0.065],
            left=0.12,
            right=0.985,
            top=0.87,
            bottom=0.09,
            wspace=0.025,
            hspace=0.055,
        )
        axes = np.empty((3, 4), dtype=object)
        for row_index, target_date in enumerate(config.pilot_dates):
            subset = data.loc[data["target_date"].eq(target_date)].copy()
            if subset.empty:
                raise ModelDiagnosticFigureError(f"Pilot map date is empty: {target_date.date()}")
            gate_pass = bool(gates.loc[target_date, "relative_endpoint_coverage_pass"])
            for column_index, (field, title, cmap, (vmin, vmax)) in enumerate(columns):
                axis = fig.add_subplot(grid[row_index, column_index])
                axes[row_index, column_index] = axis
                subset.plot(
                    column=field,
                    ax=axis,
                    cmap=cmap,
                    vmin=vmin,
                    vmax=vmax,
                    edgecolor="white",
                    linewidth=0.10,
                    missing_kwds={"color": "#E6E6E6", "edgecolor": "white"},
                )
                axis.set_xlim(bounds[0], bounds[2])
                axis.set_ylim(bounds[1], bounds[3])
                axis.set_aspect("equal")
                axis.set_axis_off()
                if row_index == 0:
                    axis.set_title(title, fontsize=10, fontweight="bold", pad=7)
            gate_label = "relative gate passed" if gate_pass else "relative gate FAILED"
            axes[row_index, 0].text(
                -0.055,
                0.5,
                f"{target_date:%Y-%m-%d}\n{gate_label}",
                transform=axes[row_index, 0].transAxes,
                ha="right",
                va="center",
                fontsize=9,
                fontweight="bold" if not gate_pass else "normal",
            )

        lst_cax = fig.add_subplot(grid[3, 0:2])
        residual_cax = fig.add_subplot(grid[3, 2])
        qa_cax = fig.add_subplot(grid[3, 3])
        colorbars = (
            (
                lst_cax,
                ScalarMappable(norm=Normalize(*limits["lst_c"]), cmap="inferno"),
                "Observed / predicted LST (°C)",
            ),
            (
                residual_cax,
                ScalarMappable(norm=Normalize(*limits["residual_c"]), cmap="RdBu_r"),
                "Residual (°C)",
            ),
            (
                qa_cax,
                ScalarMappable(norm=Normalize(*limits["st_qa_k"]), cmap="viridis"),
                "Median ST_QA (K)",
            ),
        )
        for color_axis, mappable, label in colorbars:
            colorbar = fig.colorbar(mappable, cax=color_axis, orientation="horizontal")
            colorbar.set_label(label, fontsize=8)
            colorbar.ax.tick_params(labelsize=7)
        fig.suptitle(
            "Protocol-fixed dates: observed LST, M2 prediction, residual, and target uncertainty",
            x=0.12,
            ha="left",
            fontsize=14,
            fontweight="bold",
        )
        fig.text(
            0.12,
            0.025,
            (
                "Dates were fixed before viewing scores (not selected for performance). "
                "Gray tracts lack a model-ready target on that date. Geometry is used only "
                "for diagnostics and never as a predictor; 2025 remains locked."
            ),
            ha="left",
            va="bottom",
            fontsize=8,
            color=MUTED_COLOR,
        )
        return fig


def _save_figure(fig: plt.Figure, destination: Path, *, dpi: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    fig.savefig(
        temporary,
        format="png",
        dpi=dpi,
        metadata={
            "Software": FIGURE_ALGORITHM_VERSION,
            "Title": destination.stem,
        },
    )
    plt.close(fig)
    temporary.replace(destination)


def verify_figure(path: Path, *, minimum_dpi: int = 180) -> dict[str, Any]:
    """Verify pixel dimensions, encoded DPI, and a nonblank raster."""

    with Image.open(path) as image:
        image.load()
        width, height = image.size
        dpi_value = image.info.get("dpi", (0.0, 0.0))
        if isinstance(dpi_value, tuple):
            dpi_x, dpi_y = (float(dpi_value[0]), float(dpi_value[1]))
        else:
            dpi_x = dpi_y = float(dpi_value)
        pixels = np.asarray(image.convert("RGB"))
    background = pixels[0, 0]
    non_background = float(np.mean(np.any(pixels != background, axis=2)))
    if (
        width < 1_500
        or height < 700
        or min(dpi_x, dpi_y) < minimum_dpi - 0.5
        or non_background < 0.005
    ):
        raise ModelDiagnosticFigureError(f"Figure dimension/DPI/nonblank check failed: {path}")
    return {
        "path": path.name,
        "path_base": "figure_output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "width_px": width,
        "height_px": height,
        "dpi_x": dpi_x,
        "dpi_y": dpi_y,
        "non_background_pixel_fraction": non_background,
    }


def render_model_diagnostic_figures(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
    output_directory: Path,
) -> dict[str, dict[str, Any]]:
    """Render and mechanically verify the three prespecified figure files."""

    renderers = {
        OVERVIEW_FILENAME: _render_overview,
        FOREST_FILENAME: _render_forest,
        WORST_DATES_FILENAME: _render_worst_dates,
        PILOT_MAP_FILENAME: _render_pilot_maps,
    }
    records: dict[str, dict[str, Any]] = {}
    for filename, renderer in renderers.items():
        path = output_directory / filename
        _save_figure(renderer(inputs, config), path, dpi=config.figure_dpi)
        records[filename] = verify_figure(path, minimum_dpi=180)
    return records


def _records_for_summary(
    inputs: AuthenticatedFigureInputs,
    config: ModelDiagnosticFigureConfig,
) -> dict[str, Any]:
    primary = inputs.primary_bootstrap.iloc[0]
    hotspot = inputs.hotspot_summary.set_index("model_id")
    sensor = inputs.sensor_summary.set_index(["model_id", "platform"])
    map_limits = _pilot_map_limits(inputs.pilot_map_data)
    gate_lookup = inputs.pilot_date_summary.set_index("target_date")
    return {
        "primary_joint_comparison": {
            "baseline_mae_c": float(primary["baseline_point_mae_c"]),
            "m2_mae_c": float(primary["target_model_point_mae_c"]),
            "relative_improvement_percent": float(primary["relative_mae_improvement_percent"]),
            "relative_improvement_ci_lower_percent": float(
                primary["relative_mae_improvement_ci_lower_percent"]
            ),
            "relative_improvement_ci_upper_percent": float(
                primary["relative_mae_improvement_ci_upper_percent"]
            ),
            "bootstrap_replicates": int(primary["bootstrap_replicates"]),
            "independent_date_count": int(primary["independent_date_count"]),
            "independent_spatial_block_count": int(primary["independent_spatial_block_count"]),
        },
        "hotspot_endpoint": {
            model: {
                "mean_per_date_average_precision": float(
                    hotspot.loc[model, "mean_per_date_average_precision"]
                ),
                "mean_per_date_recall_at_exact_top20": float(
                    hotspot.loc[model, "mean_per_date_recall_at_k"]
                ),
                "independent_date_count": int(hotspot.loc[model, "independent_date_count"]),
            }
            for model in (config.baseline_model_id, config.target_model_id)
        },
        "sensor_mae_c": {
            platform: {
                model: float(sensor.loc[(model, platform), "equal_date_weighted_mae_c"])
                for model in (config.baseline_model_id, config.target_model_id)
            }
            for platform in config.sensors
        },
        "forest_cohorts": [
            {
                "dimension": str(row.cohort_dimension),
                "label": str(row.cohort_label),
                "display_label": str(row.display_label),
                "relative_improvement_percent": float(row.relative_mae_improvement_percent),
                "ci_lower_percent": float(row.relative_mae_improvement_ci_lower_percent),
                "ci_upper_percent": float(row.relative_mae_improvement_ci_upper_percent),
                "tract_date_row_count": int(row.tract_date_row_count),
                "independent_date_count": int(row.independent_date_count),
                "independent_spatial_block_count": int(row.independent_spatial_block_count),
            }
            for row in inputs.qa_cohort_bootstrap.itertuples(index=False)
        ],
        "worst_dates": [
            {
                "target_date": row.target_date.date().isoformat(),
                "platform": str(row.platform),
                "b1_mae_c": float(row.b1_mae_c),
                "m2_mae_c": float(row.m2_mae_c),
                "m2_bias_c": float(row.m2_bias_c),
                "signed_error_direction": (
                    "overprediction" if row.m2_bias_c >= 0 else "underprediction"
                ),
            }
            for row in inputs.worst_dates.itertuples(index=False)
        ],
        "protocol_fixed_pilot_maps": {
            "date_selection": "protocol_fixed_not_score_selected",
            "color_limit_method": (
                "full finite authenticated value range; no percentile clipping"
            ),
            "dates": [
                {
                    "target_date": date.date().isoformat(),
                    "model_ready_tract_count": int(
                        inputs.pilot_map_data.loc[
                            inputs.pilot_map_data["target_date"].eq(date), "y_true"
                        ].notna().sum()
                    ),
                    "relative_endpoint_coverage_pass": bool(
                        gate_lookup.loc[date, "relative_endpoint_coverage_pass"]
                    ),
                }
                for date in config.pilot_dates
            ],
            "shared_color_limits": {
                key: {"minimum": value[0], "maximum": value[1]}
                for key, value in map_limits.items()
            },
            "geometry_used_for_diagnostics_only": True,
            "coordinates_used_as_predictors": False,
        },
    }


def _runtime_fingerprint() -> tuple[str, dict[str, Any]]:
    _, payload = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=PIPELINE_FILES,
        algorithm_version=FIGURE_ALGORITHM_VERSION,
    )
    packages = payload.setdefault("packages", {})
    for package in ("matplotlib", "Pillow"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "MISSING"
    return canonical_sha256(payload), payload


def _json_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "table_output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def generate_model_diagnostic_figures(
    config_path: str | Path = DEFAULT_FIGURE_CONFIG,
    *,
    figure_output_directory: str | Path | None = None,
    table_output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Authenticate frozen outputs, render figures, and commit figure provenance."""

    config = load_model_diagnostic_figure_config(config_path)
    inputs = authenticate_figure_inputs(config)
    figure_output = (
        config.figure_output_directory
        if figure_output_directory is None
        else Path(figure_output_directory).resolve()
    )
    table_output = (
        config.table_output_directory
        if table_output_directory is None
        else Path(table_output_directory).resolve()
    )
    table_output.mkdir(parents=True, exist_ok=True)
    figure_records = render_model_diagnostic_figures(inputs, config, figure_output)
    summary: dict[str, Any] = {
        "schema_version": FIGURE_SCHEMA_VERSION,
        "algorithm_version": FIGURE_ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_diagnostics_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "absolute_lst_interpretation": "surface-heat hazard proxy, not human heat exposure",
        "displayed_values": _records_for_summary(inputs, config),
        "interpretive_caveats": {
            "st_qa_le_2k": (
                "The <=2 K cohort uses tract-median ST_QA and is not the pixel-level "
                "hard-mask sensitivity."
            ),
            "sentinel_all_missing": (
                "The all-five-missing Sentinel-2 stratum is sparse and its crossed "
                "interval is wide."
            ),
            "worst_dates": "Failure-case dates are descriptive grouped-OOF diagnostics.",
        },
        "figure_files": figure_records,
    }
    summary["commit_sha256"] = canonical_sha256(summary)
    summary_path = table_output / SUMMARY_FILENAME
    atomic_json(summary, summary_path)

    pipeline_sha, pipeline_payload = _runtime_fingerprint()
    provenance: dict[str, Any] = {
        "schema_version": FIGURE_SCHEMA_VERSION,
        "algorithm_version": FIGURE_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_visual_interpretation": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_diagnostics_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "input_authentication": inputs.input_authentication,
        "analysis_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "figure_contract": {
            "dpi": config.figure_dpi,
            "minimum_verified_dpi": 180,
            "forest_cohorts_prespecified": True,
            "pilot_map_dates_prespecified": [
                date.date().isoformat() for date in config.pilot_dates
            ],
            "pilot_map_dates_score_selected": False,
            "pilot_map_color_limit_method": (
                "full finite authenticated value range; no percentile clipping"
            ),
            "geometry_used_for_diagnostics_only": True,
            "coordinates_used_as_predictors": False,
            "values_generated_from_authenticated_tables": True,
            "manual_figure_values_used": False,
            "models_fitted": False,
            "final_test_unlocked": False,
        },
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_payload,
        "output_files": {
            **figure_records,
            SUMMARY_FILENAME: _json_record(summary_path),
        },
        "summary_commit_sha256": summary["commit_sha256"],
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, table_output / PROVENANCE_FILENAME)
    return provenance
