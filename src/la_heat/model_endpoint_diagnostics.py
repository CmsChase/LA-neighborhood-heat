"""Authenticated relative-endpoint, sensor, and Sentinel-missingness diagnostics.

The analysis is deliberately downstream of the frozen development OOF compile.
It never refits a model, never derives a label on an ineligible date, and refuses
calendar year 2025 or later.  Every Parquet byte lock is checked before the first
Parquet read.
"""

from __future__ import annotations

import json
import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.model_run_compile import (
    MODEL_RUN_COMPILE_ALGORITHM_VERSION,
    MODEL_RUN_COMPILE_SCHEMA_VERSION,
    OOF_PREDICTIONS_FILENAME,
)
from la_heat.model_selection import MODEL_IDS
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.validation_splits import FAMILIES

ENDPOINT_DIAGNOSTICS_SCHEMA_VERSION: Final = 1
ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION: Final = (
    "model-endpoint-sensor-diagnostics-v1"
)
ENDPOINT_DIAGNOSTICS_STATE: Final = "frozen_development_endpoint_diagnostics"
DEFAULT_ENDPOINT_DIAGNOSTICS_CONFIG: Final = Path(
    "configs/model_endpoint_diagnostics.toml"
)

COMPILE_PROVENANCE_FILENAME: Final = "model_run_compile_provenance.json"
MODEL_DATASET_PROVENANCE_FILENAME: Final = "model_dataset_provenance.json"
MODEL_TABLE_FILENAME: Final = "development_model_table.parquet"
TARGET_PROGRESS_FILENAME: Final = "build_progress.json"
MODEL_READY_TARGET_FILENAME: Final = "development_targets_model_ready.parquet"
DATE_SUMMARY_FILENAME: Final = "date_summary.parquet"

HOTSPOT_PER_DATE_FILENAME: Final = "hotspot_per_date.csv"
HOTSPOT_SUMMARY_FILENAME: Final = "hotspot_summary.csv"
SENSOR_PER_DATE_FILENAME: Final = "sensor_per_date_metrics.csv"
SENSOR_SUMMARY_FILENAME: Final = "sensor_summary.csv"
SENTINEL_STRATUM_FILENAME: Final = "sentinel_stratum_summary.csv"
SUMMARY_FILENAME: Final = "model_endpoint_diagnostics_summary.json"
PROVENANCE_FILENAME: Final = "model_endpoint_diagnostics_provenance.json"

_PIPELINE_PATHS: Final = (
    "scripts/analyze_model_endpoints.py",
    "src/la_heat/model_endpoint_diagnostics.py",
    "src/la_heat/provenance.py",
)
_MODEL_DATASET_ALGORITHM_VERSION: Final = "gated-development-model-dataset-v1"
_SENTINEL_COMPLETE: Final = "sentinel_complete"
_SENTINEL_ALL_MISSING: Final = "sentinel_all_five_missing"


class ModelEndpointDiagnosticsError(ValueError):
    """Raised when the endpoint analysis would violate its frozen contract."""


@dataclass(frozen=True)
class EndpointDiagnosticsConfig:
    """Validated settings for the frozen development endpoint diagnostics."""

    path: Path
    semantic_sha256: str
    evaluation_directory: Path
    target_directory: Path
    model_dataset_directory: Path
    output_directory: Path
    final_test_year: int
    final_test_locked: bool
    families: tuple[str, ...]
    models: tuple[str, ...]
    focus_family: str
    focus_models: tuple[str, ...]
    expected_tract_date_rows: int
    expected_independent_dates: int
    expected_independent_spatial_blocks: int
    expected_relative_gate_dates: int
    sensors: tuple[str, ...]
    label_column: str
    gate_column: str
    positive_fraction: float
    sentinel_enabled: bool
    sentinel_feature_columns: tuple[str, ...]
    allowed_sentinel_strata: tuple[str, ...]


@dataclass(frozen=True)
class AuthenticatedEndpointInputs:
    """Authenticated and cross-validated development inputs."""

    oof: pd.DataFrame
    target: pd.DataFrame
    date_summary: pd.DataFrame
    model_table: pd.DataFrame
    compile_provenance: dict[str, Any]
    model_dataset_provenance: dict[str, Any]
    target_progress: dict[str, Any]
    input_authentication: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(payload: Mapping[str, Any], expected: set[str], *, name: str) -> None:
    observed = set(payload)
    if observed != expected:
        raise ModelEndpointDiagnosticsError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(observed)}."
        )


def _integer(value: object, *, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ModelEndpointDiagnosticsError(
            f"{name} must be an integer >= {minimum}."
        )
    return value


def _resolved_project_path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelEndpointDiagnosticsError(f"{name} must be a non-empty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def load_endpoint_diagnostics_config(
    path: str | Path = DEFAULT_ENDPOINT_DIAGNOSTICS_CONFIG,
) -> EndpointDiagnosticsConfig:
    """Load the predeclared endpoint diagnostics and reject scientific drift."""

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
            "hotspot",
            "sentinel_strata",
        },
        name="endpoint-diagnostics configuration",
    )
    if raw["schema_version"] != ENDPOINT_DIAGNOSTICS_SCHEMA_VERSION:
        raise ModelEndpointDiagnosticsError("Unsupported endpoint schema version.")
    if raw["algorithm_version"] != ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION:
        raise ModelEndpointDiagnosticsError("Endpoint algorithm version drifted.")
    if raw["state"] != ENDPOINT_DIAGNOSTICS_STATE:
        raise ModelEndpointDiagnosticsError("Endpoint configuration is not frozen.")

    paths = raw["paths"]
    analysis = raw["analysis"]
    hotspot = raw["hotspot"]
    sentinel = raw["sentinel_strata"]
    if not all(isinstance(value, dict) for value in (paths, analysis, hotspot, sentinel)):
        raise ModelEndpointDiagnosticsError("Configuration sections must be TOML tables.")
    _exact_keys(
        paths,
        {
            "evaluation_directory",
            "target_directory",
            "model_dataset_directory",
            "output_directory",
        },
        name="paths",
    )
    _exact_keys(
        analysis,
        {
            "final_test_year",
            "final_test_locked",
            "families",
            "models",
            "focus_family",
            "focus_models",
            "expected_tract_date_rows",
            "expected_independent_dates",
            "expected_independent_spatial_blocks",
            "expected_relative_gate_dates",
            "sensors",
        },
        name="analysis",
    )
    _exact_keys(
        hotspot,
        {
            "label_column",
            "gate_column",
            "positive_fraction",
            "average_precision_input",
            "rank_order",
            "exact_top_k",
        },
        name="hotspot",
    )
    _exact_keys(
        sentinel,
        {"enabled", "feature_columns", "allowed_strata"},
        name="sentinel_strata",
    )

    final_test_year = _integer(analysis["final_test_year"], name="final_test_year")
    if final_test_year != 2025 or analysis["final_test_locked"] is not True:
        raise ModelEndpointDiagnosticsError("The 2025 final test must remain locked.")
    if analysis["families"] != list(FAMILIES):
        raise ModelEndpointDiagnosticsError("Family order drifted from the OOF contract.")
    if analysis["models"] != list(MODEL_IDS):
        raise ModelEndpointDiagnosticsError("Model order drifted from the OOF contract.")
    if analysis["focus_family"] != "joint" or analysis["focus_models"] != [
        "B1",
        "M2",
    ]:
        raise ModelEndpointDiagnosticsError("The focus comparison must remain joint B1/M2.")
    if analysis["sensors"] != ["landsat-8", "landsat-9"]:
        raise ModelEndpointDiagnosticsError("Sensor strata must remain Landsat-8 and -9.")
    counts = {
        "rows": _integer(
            analysis["expected_tract_date_rows"], name="expected_tract_date_rows"
        ),
        "dates": _integer(
            analysis["expected_independent_dates"],
            name="expected_independent_dates",
        ),
        "blocks": _integer(
            analysis["expected_independent_spatial_blocks"],
            name="expected_independent_spatial_blocks",
        ),
        "relative_dates": _integer(
            analysis["expected_relative_gate_dates"],
            name="expected_relative_gate_dates",
        ),
    }
    if counts != {"rows": 63_403, "dates": 65, "blocks": 71, "relative_dates": 34}:
        raise ModelEndpointDiagnosticsError("Frozen development cardinalities drifted.")
    if (
        hotspot["label_column"] != "relative_hotspot_top20"
        or hotspot["gate_column"] != "relative_endpoint_coverage_pass"
        or hotspot["positive_fraction"] != 0.20
        or hotspot["average_precision_input"] != "continuous_y_pred"
        or hotspot["rank_order"] != "score_desc_geoid_asc"
        or hotspot["exact_top_k"] is not True
    ):
        raise ModelEndpointDiagnosticsError("Frozen hotspot scoring contract drifted.")
    feature_columns = sentinel["feature_columns"]
    expected_features = [
        "sentinel_ndvi_lag60",
        "sentinel_evi_lag60",
        "sentinel_ndwi_lag60",
        "sentinel_ndbi_lag60",
        "sentinel_albedo_proxy_lag60",
    ]
    if sentinel["enabled"] is not True or feature_columns != expected_features:
        raise ModelEndpointDiagnosticsError("Sentinel missingness contract drifted.")
    if sentinel["allowed_strata"] != [_SENTINEL_COMPLETE, _SENTINEL_ALL_MISSING]:
        raise ModelEndpointDiagnosticsError("Sentinel strata drifted.")

    return EndpointDiagnosticsConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        evaluation_directory=_resolved_project_path(
            paths["evaluation_directory"], name="paths.evaluation_directory"
        ),
        target_directory=_resolved_project_path(
            paths["target_directory"], name="paths.target_directory"
        ),
        model_dataset_directory=_resolved_project_path(
            paths["model_dataset_directory"], name="paths.model_dataset_directory"
        ),
        output_directory=_resolved_project_path(
            paths["output_directory"], name="paths.output_directory"
        ),
        final_test_year=final_test_year,
        final_test_locked=True,
        families=tuple(analysis["families"]),
        models=tuple(analysis["models"]),
        focus_family=str(analysis["focus_family"]),
        focus_models=tuple(analysis["focus_models"]),
        expected_tract_date_rows=counts["rows"],
        expected_independent_dates=counts["dates"],
        expected_independent_spatial_blocks=counts["blocks"],
        expected_relative_gate_dates=counts["relative_dates"],
        sensors=tuple(analysis["sensors"]),
        label_column=str(hotspot["label_column"]),
        gate_column=str(hotspot["gate_column"]),
        positive_fraction=float(hotspot["positive_fraction"]),
        sentinel_enabled=True,
        sentinel_feature_columns=tuple(feature_columns),
        allowed_sentinel_strata=tuple(sentinel["allowed_strata"]),
    )


def _json_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelEndpointDiagnosticsError(f"Cannot read valid {name}: {path}") from error
    if not isinstance(payload, dict):
        raise ModelEndpointDiagnosticsError(f"{name} must be a JSON object.")
    return payload


def _verify_json_commit(payload: Mapping[str, Any], *, name: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if (
        not isinstance(recorded, str)
        or len(recorded) != 64
        or canonical_sha256(working) != recorded
    ):
        raise ModelEndpointDiagnosticsError(f"{name} commit is invalid.")
    return recorded


def _hex_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ModelEndpointDiagnosticsError(f"{name} must be a lowercase SHA-256.")
    return value


def _record(payload: object, filename: str, *, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(filename), dict):
        raise ModelEndpointDiagnosticsError(f"{name} is missing {filename!r}.")
    return dict(payload[filename])


def _verify_file_lock(
    path: Path,
    record: Mapping[str, Any],
    *,
    name: str,
    require_bytes: bool = True,
) -> dict[str, Any]:
    """Verify one byte lock without opening a Parquet reader."""

    recorded_path = record.get("path")
    if recorded_path is not None and Path(str(recorded_path)).name != path.name:
        raise ModelEndpointDiagnosticsError(f"{name} path does not name {path.name!r}.")
    expected_hash = _hex_sha256(record.get("sha256"), name=f"{name}.sha256")
    if not path.is_file():
        raise ModelEndpointDiagnosticsError(f"Authenticated input is missing: {path}")
    if require_bytes:
        expected_bytes = _integer(record.get("bytes"), name=f"{name}.bytes", minimum=0)
        if path.stat().st_size != expected_bytes:
            raise ModelEndpointDiagnosticsError(f"{name} byte lock does not match provenance.")
    if sha256_file(path) != expected_hash:
        raise ModelEndpointDiagnosticsError(f"{name} byte lock does not match provenance.")
    return dict(record)


def _validate_parquet_record(
    frame: pd.DataFrame, record: Mapping[str, Any], *, name: str
) -> None:
    expected_rows = _integer(record.get("rows"), name=f"{name}.rows", minimum=0)
    if len(frame) != expected_rows:
        raise ModelEndpointDiagnosticsError(f"{name} row count disagrees with provenance.")
    schema_sha = canonical_sha256(
        [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
    )
    if record.get("schema_sha256") != schema_sha:
        raise ModelEndpointDiagnosticsError(f"{name} schema hash disagrees with provenance.")


def _parse_civil_dates(values: pd.Series, *, name: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, format="mixed", errors="raise")
    except (TypeError, ValueError) as error:
        raise ModelEndpointDiagnosticsError(f"{name} contains invalid dates.") from error
    if parsed.isna().any() or parsed.dt.tz is not None:
        raise ModelEndpointDiagnosticsError(f"{name} must be timezone-naive civil dates.")
    if not parsed.dt.normalize().equals(parsed):
        raise ModelEndpointDiagnosticsError(f"{name} must contain civil midnights.")
    return parsed.astype("datetime64[us]")


def _normalized_strings(values: pd.Series, *, name: str) -> pd.Series:
    valid = values.map(
        lambda value: isinstance(value, str)
        and bool(value)
        and value == value.strip()
    )
    if values.isna().any() or not valid.all():
        raise ModelEndpointDiagnosticsError(
            f"{name} must contain normalized non-empty strings."
        )
    return values.astype("string")


def _reject_locked_dates(
    frame: pd.DataFrame, *, name: str, final_test_year: int
) -> None:
    locked = frame["target_date"].dt.year.ge(final_test_year)
    if locked.any():
        raise ModelEndpointDiagnosticsError(
            f"Locked final-test year {final_test_year} or later appears in {name}."
        )


def exact_top_k_mask(
    frame: pd.DataFrame,
    *,
    score_column: str,
    positive_fraction: float,
) -> pd.Series:
    """Return exact top-k predictions using score-descending/GEOID-ascending rank."""

    if not 0.0 < positive_fraction < 1.0:
        raise ModelEndpointDiagnosticsError("positive_fraction must lie between 0 and 1.")
    required = {"tract_geoid", score_column}
    missing = sorted(required - set(frame.columns))
    if missing or frame.empty:
        raise ModelEndpointDiagnosticsError(
            f"Exact top-k input is empty or missing columns: {missing}."
        )
    geoids = _normalized_strings(frame["tract_geoid"], name="tract_geoid")
    if geoids.duplicated().any():
        raise ModelEndpointDiagnosticsError("Exact top-k GEOIDs must be unique per date.")
    try:
        scores = pd.to_numeric(frame[score_column], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ModelEndpointDiagnosticsError("Exact top-k scores must be numeric.") from error
    if not np.isfinite(scores).all():
        raise ModelEndpointDiagnosticsError("Exact top-k scores must be finite.")
    ranked = pd.DataFrame(
        {"tract_geoid": geoids.to_numpy(), "score": scores}, index=frame.index
    ).sort_values(
        ["score", "tract_geoid"],
        ascending=[False, True],
        kind="mergesort",
    )
    k = math.ceil(positive_fraction * len(frame))
    result = pd.Series(False, index=frame.index, dtype=bool)
    result.loc[ranked.index[:k]] = True
    return result


def continuous_average_precision(labels: Sequence[object], scores: Sequence[object]) -> float:
    """Average precision from continuous scores, with score ties kept together."""

    y = np.asarray(labels)
    score = np.asarray(scores, dtype=float)
    if y.ndim != 1 or score.ndim != 1 or len(y) != len(score) or len(y) == 0:
        raise ModelEndpointDiagnosticsError("AP labels and scores must be equal nonempty vectors.")
    if not np.isfinite(score).all():
        raise ModelEndpointDiagnosticsError("AP scores must be finite.")
    if not all(isinstance(value, (bool, np.bool_, int, np.integer)) for value in y):
        raise ModelEndpointDiagnosticsError("AP labels must be binary.")
    numeric_y = y.astype(int)
    if not np.isin(numeric_y, [0, 1]).all():
        raise ModelEndpointDiagnosticsError("AP labels must be binary.")
    positive_count = int(numeric_y.sum())
    if positive_count == 0:
        raise ModelEndpointDiagnosticsError("AP requires at least one positive label.")
    order = np.argsort(-score, kind="stable")
    ordered_score = score[order]
    ordered_y = numeric_y[order]
    cumulative_true = np.cumsum(ordered_y)
    tie_ends = np.r_[np.flatnonzero(ordered_score[1:] != ordered_score[:-1]), len(y) - 1]
    true_at_threshold = cumulative_true[tie_ends]
    precision = true_at_threshold / (tie_ends + 1)
    previous_true = np.r_[0, true_at_threshold[:-1]]
    recall_increment = (true_at_threshold - previous_true) / positive_count
    return float(np.sum(recall_increment * precision))


def _spearman_or_none(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    if (
        y_true.size < 2
        or np.unique(y_true).size < 2
        or np.unique(y_pred).size < 2
    ):
        return None
    true_rank = pd.Series(y_true).rank(method="average").to_numpy(dtype=float)
    pred_rank = pd.Series(y_pred).rank(method="average").to_numpy(dtype=float)
    value = float(np.corrcoef(true_rank, pred_rank)[0, 1])
    return value if math.isfinite(value) else None


def validate_relative_endpoint_gate(
    target: pd.DataFrame,
    date_summary: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Authenticate gate placement and every frozen exact-top-k target label."""

    target_required = {
        "tract_geoid",
        "target_date",
        "platform",
        "spatial_block",
        "target_lst_c",
        "target_available",
        "date_usable",
        config.label_column,
    }
    summary_required = {
        "target_date",
        "platform",
        "retained_tract_count",
        "date_usable",
        config.gate_column,
        "relative_hotspot_count",
    }
    missing_target = sorted(target_required - set(target.columns))
    missing_summary = sorted(summary_required - set(date_summary.columns))
    if missing_target or missing_summary:
        raise ModelEndpointDiagnosticsError(
            "Relative endpoint inputs are missing "
            f"target={missing_target}, summary={missing_summary}."
        )
    prepared_target = target.copy()
    prepared_summary = date_summary.copy()
    prepared_target["target_date"] = _parse_civil_dates(
        prepared_target["target_date"], name="target.target_date"
    )
    prepared_summary["target_date"] = _parse_civil_dates(
        prepared_summary["target_date"], name="date_summary.target_date"
    )
    for frame, name in (
        (prepared_target, "target"),
        (prepared_summary, "date_summary"),
    ):
        _reject_locked_dates(frame, name=name, final_test_year=config.final_test_year)
    prepared_target["tract_geoid"] = _normalized_strings(
        prepared_target["tract_geoid"], name="target.tract_geoid"
    )
    prepared_target["spatial_block"] = _normalized_strings(
        prepared_target["spatial_block"], name="target.spatial_block"
    )
    prepared_target["platform"] = _normalized_strings(
        prepared_target["platform"], name="target.platform"
    )
    prepared_summary["platform"] = _normalized_strings(
        prepared_summary["platform"], name="date_summary.platform"
    )
    if prepared_target.duplicated(["tract_geoid", "target_date"]).any():
        raise ModelEndpointDiagnosticsError("Target duplicates tract-date keys.")
    if prepared_summary.duplicated("target_date").any():
        raise ModelEndpointDiagnosticsError("Date summary duplicates target dates.")
    if len(prepared_target) != config.expected_tract_date_rows:
        raise ModelEndpointDiagnosticsError("Target row cardinality drifted.")
    if prepared_target["target_date"].nunique() != config.expected_independent_dates:
        raise ModelEndpointDiagnosticsError("Target independent-date cardinality drifted.")
    if prepared_target["spatial_block"].nunique() != config.expected_independent_spatial_blocks:
        raise ModelEndpointDiagnosticsError("Target independent-block cardinality drifted.")
    if not prepared_target["target_available"].eq(True).all() or not prepared_target[
        "date_usable"
    ].eq(True).all():
        raise ModelEndpointDiagnosticsError("Model-ready target contains unavailable rows/dates.")
    values = pd.to_numeric(prepared_target["target_lst_c"], errors="raise").to_numpy(
        dtype=float
    )
    if not np.isfinite(values).all():
        raise ModelEndpointDiagnosticsError("Target LST must be finite.")
    prepared_target["target_lst_c"] = values
    for column in ("date_usable", config.gate_column):
        if prepared_summary[column].isna().any():
            raise ModelEndpointDiagnosticsError(f"Date summary {column} contains missing values.")
        prepared_summary[column] = prepared_summary[column].astype(bool)
    usable_dates = set(
        prepared_summary.loc[prepared_summary["date_usable"], "target_date"].tolist()
    )
    target_dates = set(prepared_target["target_date"].tolist())
    if usable_dates != target_dates:
        raise ModelEndpointDiagnosticsError("Model-ready target dates disagree with usable dates.")
    gated_summary = prepared_summary.loc[prepared_summary[config.gate_column]].copy()
    if len(gated_summary) != config.expected_relative_gate_dates:
        raise ModelEndpointDiagnosticsError("Relative-endpoint gate date count drifted.")
    if not gated_summary["date_usable"].all():
        raise ModelEndpointDiagnosticsError("Relative gate passed on an unusable date.")
    gate_lookup = prepared_summary.set_index("target_date")[config.gate_column]
    prepared_target["_relative_gate"] = prepared_target["target_date"].map(gate_lookup)
    labels = prepared_target[config.label_column]
    if labels.loc[prepared_target["_relative_gate"]].isna().any():
        raise ModelEndpointDiagnosticsError("A gated date has missing hotspot labels.")
    if labels.loc[~prepared_target["_relative_gate"]].notna().any():
        raise ModelEndpointDiagnosticsError("An ungated date has hotspot labels.")
    nonmissing = labels.dropna()
    if not nonmissing.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ModelEndpointDiagnosticsError("Hotspot labels must be boolean when present.")

    summary_index = prepared_summary.set_index("target_date")
    for target_date, group in prepared_target.loc[
        prepared_target["_relative_gate"]
    ].groupby("target_date", sort=True):
        expected_k = math.ceil(config.positive_fraction * len(group))
        observed = group[config.label_column].astype(bool)
        if int(observed.sum()) != expected_k:
            raise ModelEndpointDiagnosticsError("A gated date does not contain exact top-k labels.")
        exact = exact_top_k_mask(
            group,
            score_column="target_lst_c",
            positive_fraction=config.positive_fraction,
        )
        if not observed.equals(exact):
            raise ModelEndpointDiagnosticsError(
                "Hotspot labels disagree with target-descending/GEOID-ascending exact top-k."
            )
        summary_row = summary_index.loc[target_date]
        if (
            int(summary_row["retained_tract_count"]) != len(group)
            or int(summary_row["relative_hotspot_count"]) != expected_k
            or str(summary_row["platform"]) != str(group["platform"].iloc[0])
            or group["platform"].nunique() != 1
        ):
            raise ModelEndpointDiagnosticsError(
                "Date-summary hotspot, sensor, or retained-row count disagrees with targets."
            )
    return prepared_target, prepared_summary


def _validate_oof(
    oof: pd.DataFrame,
    target: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
) -> pd.DataFrame:
    if list(oof.columns) != list(OUTER_PREDICTION_COLUMNS):
        raise ModelEndpointDiagnosticsError("OOF columns drifted from the compile contract.")
    result = oof.copy()
    result["target_date"] = _parse_civil_dates(
        result["target_date"], name="oof.target_date"
    )
    _reject_locked_dates(result, name="OOF predictions", final_test_year=config.final_test_year)
    for column in ("tract_geoid", "spatial_block", "family", "model_id"):
        result[column] = _normalized_strings(result[column], name=f"oof.{column}")
    if result.isna().any().any():
        raise ModelEndpointDiagnosticsError("OOF predictions contain missing values.")
    numeric = result[["y_true", "y_pred"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ModelEndpointDiagnosticsError("OOF truth or predictions are non-finite.")
    result.loc[:, ["y_true", "y_pred"]] = numeric
    identity = ["family", "model_id", "tract_geoid", "target_date"]
    if result.duplicated(identity).any():
        raise ModelEndpointDiagnosticsError("OOF duplicates a family/model/tract/date row.")
    expected_pairs = {(family, model) for family in config.families for model in config.models}
    observed_pairs = set(
        result[["family", "model_id"]].itertuples(index=False, name=None)
    )
    if observed_pairs != expected_pairs:
        raise ModelEndpointDiagnosticsError("OOF family/model coverage is incomplete.")
    sizes = result.groupby(["family", "model_id"], observed=True).size()
    if not sizes.eq(config.expected_tract_date_rows).all():
        raise ModelEndpointDiagnosticsError("OOF family/model row cardinality drifted.")

    target_columns = [
        "tract_geoid",
        "target_date",
        "spatial_block",
        "platform",
        "target_lst_c",
        config.label_column,
        "_relative_gate",
    ]
    enriched = result.merge(
        target[target_columns],
        on=["tract_geoid", "target_date"],
        how="left",
        suffixes=("", "_target"),
        validate="many_to_one",
        indicator=True,
    )
    if not enriched["_merge"].eq("both").all() or len(enriched) != len(result):
        raise ModelEndpointDiagnosticsError("OOF keys do not exactly cover model-ready targets.")
    if not enriched["spatial_block"].astype(str).equals(
        enriched["spatial_block_target"].astype(str)
    ):
        raise ModelEndpointDiagnosticsError("OOF spatial blocks disagree with targets.")
    if not np.array_equal(
        enriched["y_true"].to_numpy(dtype=float),
        enriched["target_lst_c"].to_numpy(dtype=float),
    ):
        raise ModelEndpointDiagnosticsError("OOF y_true is not bit-exact to target provenance.")
    base_rows = enriched[
        ["tract_geoid", "target_date", "spatial_block"]
    ].drop_duplicates()
    observed_counts = {
        "rows": len(base_rows),
        "dates": int(base_rows["target_date"].nunique()),
        "blocks": int(base_rows["spatial_block"].nunique()),
    }
    expected_counts = {
        "rows": config.expected_tract_date_rows,
        "dates": config.expected_independent_dates,
        "blocks": config.expected_independent_spatial_blocks,
    }
    if observed_counts != expected_counts:
        raise ModelEndpointDiagnosticsError(
            f"OOF independent-unit counts drifted: {observed_counts}."
        )
    return enriched.drop(columns=["_merge", "spatial_block_target"])


def _validate_model_table(
    model_table: pd.DataFrame,
    target: pd.DataFrame,
    provenance: Mapping[str, Any],
    config: EndpointDiagnosticsConfig,
) -> pd.DataFrame:
    required = {
        "tract_geoid",
        "target_date",
        "target_lst_c",
        *config.sentinel_feature_columns,
    }
    missing = sorted(required - set(model_table.columns))
    if missing:
        raise ModelEndpointDiagnosticsError(f"Model table is missing columns: {missing}")
    result = model_table.copy()
    result["target_date"] = _parse_civil_dates(
        result["target_date"], name="model_table.target_date"
    )
    _reject_locked_dates(result, name="model table", final_test_year=config.final_test_year)
    result["tract_geoid"] = _normalized_strings(
        result["tract_geoid"], name="model_table.tract_geoid"
    )
    if result.duplicated(["tract_geoid", "target_date"]).any():
        raise ModelEndpointDiagnosticsError("Model table duplicates tract-date keys.")
    if len(result) != config.expected_tract_date_rows:
        raise ModelEndpointDiagnosticsError("Model table row cardinality drifted.")
    joined = result.merge(
        target[["tract_geoid", "target_date", "target_lst_c"]],
        on=["tract_geoid", "target_date"],
        how="outer",
        suffixes=("", "_target"),
        validate="one_to_one",
        indicator=True,
    )
    if not joined["_merge"].eq("both").all() or len(joined) != len(target):
        raise ModelEndpointDiagnosticsError("Model table keys disagree with targets.")
    if not np.array_equal(
        joined["target_lst_c"].to_numpy(dtype=float),
        joined["target_lst_c_target"].to_numpy(dtype=float),
    ):
        raise ModelEndpointDiagnosticsError(
            "Model-table target is not bit-exact to target provenance."
        )
    missing_count = result[list(config.sentinel_feature_columns)].isna().sum(axis=1)
    feature_count = len(config.sentinel_feature_columns)
    partial = ~missing_count.isin([0, feature_count])
    if partial.any():
        raise ModelEndpointDiagnosticsError(
            "Partial Sentinel missingness is not an allowed stratum."
        )
    result["sentinel_stratum"] = np.where(
        missing_count.eq(0), _SENTINEL_COMPLETE, _SENTINEL_ALL_MISSING
    )
    observed = result["sentinel_stratum"].value_counts().to_dict()
    expected = {
        _SENTINEL_COMPLETE: int(provenance.get("complete_model_feature_rows", -1)),
        _SENTINEL_ALL_MISSING: int(provenance.get("incomplete_model_feature_rows", -1)),
    }
    if observed != expected or set(observed) != set(config.allowed_sentinel_strata):
        raise ModelEndpointDiagnosticsError(
            f"Sentinel stratum counts disagree with model provenance: {observed}."
        )
    return result


def authenticate_endpoint_inputs(
    config: EndpointDiagnosticsConfig,
    *,
    evaluation_directory: str | Path | None = None,
    target_directory: str | Path | None = None,
    model_dataset_directory: str | Path | None = None,
) -> AuthenticatedEndpointInputs:
    """Authenticate both provenance chains and all hashes before reading Parquet."""

    evaluation = (
        config.evaluation_directory
        if evaluation_directory is None
        else Path(evaluation_directory).resolve()
    )
    target_dir = (
        config.target_directory
        if target_directory is None
        else Path(target_directory).resolve()
    )
    model_dir = (
        config.model_dataset_directory
        if model_dataset_directory is None
        else Path(model_dataset_directory).resolve()
    )
    compile_path = evaluation / COMPILE_PROVENANCE_FILENAME
    model_provenance_path = model_dir / MODEL_DATASET_PROVENANCE_FILENAME
    compile_provenance = _json_object(compile_path, name="compile provenance")
    model_provenance = _json_object(model_provenance_path, name="model-dataset provenance")
    compile_commit = _verify_json_commit(compile_provenance, name="Compile provenance")
    model_commit = _verify_json_commit(model_provenance, name="Model-dataset provenance")

    expected_compile = {
        "schema_version": MODEL_RUN_COMPILE_SCHEMA_VERSION,
        "algorithm_version": MODEL_RUN_COMPILE_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_reporting": True,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "context_row_count": config.expected_tract_date_rows,
        "independent_date_count": config.expected_independent_dates,
        "family_count": len(config.families),
        "model_count": len(config.models),
        "oof_prediction_row_count": (
            config.expected_tract_date_rows * len(config.families) * len(config.models)
        ),
        "model_dataset_commit_sha256": model_commit,
    }
    if any(compile_provenance.get(key) != value for key, value in expected_compile.items()):
        raise ModelEndpointDiagnosticsError(
            "Compile provenance is not the canonical locked OOF compile."
        )
    expected_model = {
        "schema_version": 1,
        "algorithm_version": _MODEL_DATASET_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_modeling": True,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": False,
        "contains_final_test_year": False,
        "row_count": config.expected_tract_date_rows,
        "independent_date_count": config.expected_independent_dates,
    }
    if any(model_provenance.get(key) != value for key, value in expected_model.items()):
        raise ModelEndpointDiagnosticsError("Model-dataset provenance is not canonical and locked.")

    oof_record = _record(
        compile_provenance.get("output_files"),
        OOF_PREDICTIONS_FILENAME,
        name="compile output_files",
    )
    model_inputs = model_provenance.get("inputs")
    if not isinstance(model_inputs, dict):
        raise ModelEndpointDiagnosticsError("Model provenance has no input locks.")
    progress_record = model_inputs.get("target_progress")
    target_model_record = model_inputs.get("model_ready_target")
    if not isinstance(progress_record, dict) or not isinstance(target_model_record, dict):
        raise ModelEndpointDiagnosticsError("Model provenance lacks target provenance/hash locks.")
    model_record = _record(
        model_provenance.get("output_files"), MODEL_TABLE_FILENAME, name="model output_files"
    )

    oof_path = evaluation / OOF_PREDICTIONS_FILENAME
    progress_path = target_dir / TARGET_PROGRESS_FILENAME
    target_path = target_dir / MODEL_READY_TARGET_FILENAME
    summary_path = target_dir / DATE_SUMMARY_FILENAME
    model_table_path = model_dir / MODEL_TABLE_FILENAME
    _verify_file_lock(oof_path, oof_record, name="OOF predictions")
    _verify_file_lock(
        progress_path,
        progress_record,
        name="target progress",
        require_bytes=False,
    )
    target_progress = _json_object(progress_path, name="authenticated target progress")
    expected_progress = {
        "state": "model_ready",
        "build_complete": True,
        "partial_outputs_only": False,
        "promoted_outputs_valid": True,
        "usable_overpass_count": config.expected_independent_dates,
    }
    if any(target_progress.get(key) != value for key, value in expected_progress.items()):
        raise ModelEndpointDiagnosticsError("Target progress is not a promoted complete build.")
    aggregate_outputs = target_progress.get("aggregate_outputs")
    target_progress_record = _record(
        aggregate_outputs, MODEL_READY_TARGET_FILENAME, name="target aggregate_outputs"
    )
    date_summary_record = _record(
        aggregate_outputs, DATE_SUMMARY_FILENAME, name="target aggregate_outputs"
    )
    lock_fields = ("sha256", "bytes", "rows", "schema_sha256")
    if any(
        target_model_record.get(field) != target_progress_record.get(field)
        for field in lock_fields
    ):
        raise ModelEndpointDiagnosticsError(
            "Target locks disagree across canonical provenance files."
        )
    _verify_file_lock(target_path, target_progress_record, name="model-ready target")
    _verify_file_lock(summary_path, date_summary_record, name="date summary")
    _verify_file_lock(model_table_path, model_record, name="development model table")

    # All byte locks above must pass before this first Parquet read.
    oof = pd.read_parquet(oof_path)
    target = pd.read_parquet(target_path)
    date_summary = pd.read_parquet(summary_path)
    model_table = pd.read_parquet(model_table_path)
    _validate_parquet_record(oof, oof_record, name="OOF predictions")
    _validate_parquet_record(target, target_progress_record, name="model-ready target")
    _validate_parquet_record(date_summary, date_summary_record, name="date summary")
    _validate_parquet_record(model_table, model_record, name="development model table")

    validated_target, validated_date_summary = validate_relative_endpoint_gate(
        target, date_summary, config
    )
    validated_oof = _validate_oof(oof, validated_target, config)
    validated_model_table = _validate_model_table(
        model_table, validated_target, model_provenance, config
    )
    stratum = validated_model_table[
        ["tract_geoid", "target_date", "sentinel_stratum"]
    ]
    validated_oof = validated_oof.merge(
        stratum,
        on=["tract_geoid", "target_date"],
        how="left",
        validate="many_to_one",
    )
    if validated_oof["sentinel_stratum"].isna().any():
        raise ModelEndpointDiagnosticsError("OOF rows lack an authenticated Sentinel stratum.")

    authentication = {
        "compile_provenance_file_sha256": sha256_file(compile_path),
        "compile_provenance_commit_sha256": compile_commit,
        "oof_predictions_sha256": str(oof_record["sha256"]),
        "model_dataset_provenance_file_sha256": sha256_file(model_provenance_path),
        "model_dataset_commit_sha256": model_commit,
        "target_progress_sha256": str(progress_record["sha256"]),
        "model_ready_target_sha256": str(target_progress_record["sha256"]),
        "date_summary_sha256": str(date_summary_record["sha256"]),
        "development_model_table_sha256": str(model_record["sha256"]),
    }
    return AuthenticatedEndpointInputs(
        oof=validated_oof,
        target=validated_target,
        date_summary=validated_date_summary,
        model_table=validated_model_table,
        compile_provenance=compile_provenance,
        model_dataset_provenance=model_provenance,
        target_progress=target_progress,
        input_authentication=authentication,
    )


def _focus(family: str, model_id: str, config: EndpointDiagnosticsConfig) -> bool:
    return family == config.focus_family and model_id in config.focus_models


def _ordered(
    frame: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
    *,
    extra: Sequence[str],
) -> pd.DataFrame:
    family_order = {value: position for position, value in enumerate(config.families)}
    model_order = {value: position for position, value in enumerate(config.models)}
    result = frame.copy()
    result["__family"] = result["family"].map(family_order)
    result["__model"] = result["model_id"].map(model_order)
    return (
        result.sort_values(["__family", "__model", *extra], kind="stable")
        .drop(columns=["__family", "__model"])
        .reset_index(drop=True)
    )


def build_hotspot_diagnostics(
    oof: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate gated per-date AP and deterministic exact-top-k diagnostics."""

    gated = oof.loc[oof["_relative_gate"]].copy()
    if gated["target_date"].nunique() != config.expected_relative_gate_dates:
        raise ModelEndpointDiagnosticsError("OOF relative-gate date count drifted.")
    rows: list[dict[str, Any]] = []
    grouped = gated.groupby(["family", "model_id", "target_date"], observed=True)
    for (family, model_id, target_date), group in grouped:
        label = group[config.label_column].astype(bool)
        expected_k = math.ceil(config.positive_fraction * len(group))
        if int(label.sum()) != expected_k:
            raise ModelEndpointDiagnosticsError("OOF gated labels are not exact top-k.")
        predicted = exact_top_k_mask(
            group,
            score_column="y_pred",
            positive_fraction=config.positive_fraction,
        )
        true_positive = int((predicted & label).sum())
        false_positive = int((predicted & ~label).sum())
        false_negative = int((~predicted & label).sum())
        rows.append(
            {
                "family": str(family),
                "model_id": str(model_id),
                "focus_joint_b1_or_m2": _focus(str(family), str(model_id), config),
                "target_date": pd.Timestamp(target_date).date().isoformat(),
                "platform": str(group["platform"].iloc[0]),
                "tract_date_row_count": len(group),
                "independent_spatial_block_count": int(group["spatial_block"].nunique()),
                "exact_top_k": expected_k,
                "observed_positive_count": int(label.sum()),
                "predicted_positive_count": int(predicted.sum()),
                "true_positive_count": true_positive,
                "false_positive_count": false_positive,
                "false_negative_count": false_negative,
                "average_precision": continuous_average_precision(
                    label.to_numpy(), group["y_pred"].to_numpy(dtype=float)
                ),
                "precision_at_k": true_positive / expected_k,
                "recall_at_k": true_positive / expected_k,
                "false_negative_rate": false_negative / expected_k,
            }
        )
    per_date = _ordered(pd.DataFrame(rows), config, extra=["target_date"])
    expected_rows = (
        config.expected_relative_gate_dates * len(config.families) * len(config.models)
    )
    if len(per_date) != expected_rows:
        raise ModelEndpointDiagnosticsError("Hotspot per-date output cardinality is incomplete.")

    summary_rows: list[dict[str, Any]] = []
    for (family, model_id), metrics in per_date.groupby(
        ["family", "model_id"], observed=True, sort=False
    ):
        source = gated.loc[gated["family"].eq(family) & gated["model_id"].eq(model_id)]
        summary_rows.append(
            {
                "family": family,
                "model_id": model_id,
                "focus_joint_b1_or_m2": _focus(str(family), str(model_id), config),
                "tract_date_row_count": int(len(source)),
                "independent_date_count": int(source["target_date"].nunique()),
                "independent_spatial_block_count": int(source["spatial_block"].nunique()),
                "mean_per_date_average_precision": float(metrics["average_precision"].mean()),
                "mean_per_date_precision_at_k": float(metrics["precision_at_k"].mean()),
                "mean_per_date_recall_at_k": float(metrics["recall_at_k"].mean()),
                "mean_per_date_false_negative_rate": float(
                    metrics["false_negative_rate"].mean()
                ),
            }
        )
    summary = _ordered(pd.DataFrame(summary_rows), config, extra=[])
    if (
        len(summary) != len(config.families) * len(config.models)
        or not summary["independent_date_count"].eq(
            config.expected_relative_gate_dates
        ).all()
    ):
        raise ModelEndpointDiagnosticsError("Hotspot summary counts are incomplete.")
    return per_date, summary


def _one_date_absolute_metrics(group: pd.DataFrame) -> dict[str, Any]:
    y_true = group["y_true"].to_numpy(dtype=float)
    y_pred = group["y_pred"].to_numpy(dtype=float)
    error = y_pred - y_true
    spearman = _spearman_or_none(y_true, y_pred)
    return {
        "tract_date_row_count": len(group),
        "independent_spatial_block_count": int(group["spatial_block"].nunique()),
        "mae_c": float(np.mean(np.abs(error))),
        "rmse_c": float(np.sqrt(np.mean(np.square(error)))),
        "mean_signed_error_c": float(np.mean(error)),
        "spearman_rho": np.nan if spearman is None else spearman,
        "spearman_defined": spearman is not None,
    }


def _summarize_date_metrics(
    metrics: pd.DataFrame,
    source: pd.DataFrame,
) -> dict[str, Any]:
    defined = metrics.loc[metrics["spearman_defined"], "spearman_rho"]
    return {
        "tract_date_row_count": int(len(source)),
        "independent_date_count": int(source["target_date"].nunique()),
        "independent_spatial_block_count": int(source["spatial_block"].nunique()),
        "equal_date_weighted_mae_c": float(metrics["mae_c"].mean()),
        "equal_date_weighted_rmse_c": float(metrics["rmse_c"].mean()),
        "equal_date_weighted_mean_signed_error_c": float(
            metrics["mean_signed_error_c"].mean()
        ),
        "mean_per_date_spearman": np.nan if defined.empty else float(defined.mean()),
        "median_per_date_spearman": np.nan if defined.empty else float(defined.median()),
        "spearman_defined_date_count": int(metrics["spearman_defined"].sum()),
        "spearman_undefined_date_count": int((~metrics["spearman_defined"]).sum()),
    }


def build_sensor_diagnostics(
    oof: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate per-date metrics and date-macro summaries by Landsat sensor."""

    rows: list[dict[str, Any]] = []
    grouped = oof.groupby(["family", "model_id", "target_date"], observed=True)
    for (family, model_id, target_date), group in grouped:
        if group["platform"].nunique() != 1:
            raise ModelEndpointDiagnosticsError(
                "A physical overpass date maps to multiple sensors."
            )
        rows.append(
            {
                "family": str(family),
                "model_id": str(model_id),
                "focus_joint_b1_or_m2": _focus(str(family), str(model_id), config),
                "target_date": pd.Timestamp(target_date).date().isoformat(),
                "platform": str(group["platform"].iloc[0]),
                **_one_date_absolute_metrics(group),
            }
        )
    per_date = _ordered(pd.DataFrame(rows), config, extra=["target_date"])
    expected_rows = config.expected_independent_dates * len(config.families) * len(config.models)
    if len(per_date) != expected_rows or set(per_date["platform"]) != set(config.sensors):
        raise ModelEndpointDiagnosticsError("Sensor per-date cardinality is incomplete.")

    summary_rows: list[dict[str, Any]] = []
    for (family, model_id, platform), metrics in per_date.groupby(
        ["family", "model_id", "platform"], observed=True, sort=False
    ):
        source = oof.loc[
            oof["family"].eq(family)
            & oof["model_id"].eq(model_id)
            & oof["platform"].eq(platform)
        ]
        summary_rows.append(
            {
                "family": family,
                "model_id": model_id,
                "focus_joint_b1_or_m2": _focus(str(family), str(model_id), config),
                "platform": platform,
                **_summarize_date_metrics(metrics, source),
            }
        )
    summary = _ordered(pd.DataFrame(summary_rows), config, extra=["platform"])
    expected_summary = len(config.families) * len(config.models) * len(config.sensors)
    if len(summary) != expected_summary:
        raise ModelEndpointDiagnosticsError("Sensor summary cardinality is incomplete.")
    return per_date, summary


def build_sentinel_stratum_diagnostics(
    oof: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
) -> pd.DataFrame:
    """Compare OOF errors for complete versus all-five-missing Sentinel rows."""

    rows: list[dict[str, Any]] = []
    grouped = oof.groupby(["family", "model_id", "sentinel_stratum"], observed=True)
    for (family, model_id, stratum), source in grouped:
        date_rows = []
        for _, date_group in source.groupby("target_date", observed=True):
            date_rows.append(_one_date_absolute_metrics(date_group))
        metrics = pd.DataFrame(date_rows)
        error = source["y_pred"].to_numpy(dtype=float) - source["y_true"].to_numpy(
            dtype=float
        )
        rows.append(
            {
                "family": str(family),
                "model_id": str(model_id),
                "focus_joint_b1_or_m2": _focus(str(family), str(model_id), config),
                "sentinel_stratum": str(stratum),
                **_summarize_date_metrics(metrics, source),
                "pooled_rmse_c": float(np.sqrt(np.mean(np.square(error)))),
                "pooled_mean_signed_error_c": float(np.mean(error)),
            }
        )
    summary = _ordered(pd.DataFrame(rows), config, extra=["sentinel_stratum"])
    expected = len(config.families) * len(config.models) * len(
        config.allowed_sentinel_strata
    )
    if len(summary) != expected or set(summary["sentinel_stratum"]) != set(
        config.allowed_sentinel_strata
    ):
        raise ModelEndpointDiagnosticsError("Sentinel stratum summary is incomplete.")
    return summary


def _audit_columns(
    config: EndpointDiagnosticsConfig, authentication: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "analysis_config_semantic_sha256": config.semantic_sha256,
        **dict(authentication),
    }


def _append_audit_columns(
    frame: pd.DataFrame,
    config: EndpointDiagnosticsConfig,
    authentication: Mapping[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    for column, value in _audit_columns(config, authentication).items():
        result[column] = value
    return result


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def _json_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _summary_payload(
    *,
    config: EndpointDiagnosticsConfig,
    authenticated: AuthenticatedEndpointInputs,
    hotspot_summary: pd.DataFrame,
    sensor_summary: pd.DataFrame,
    sentinel_summary: pd.DataFrame,
) -> dict[str, Any]:
    focus_hotspot = hotspot_summary.loc[
        hotspot_summary["family"].eq(config.focus_family)
        & hotspot_summary["model_id"].isin(config.focus_models)
    ].set_index("model_id")
    focus_sensors = sensor_summary.loc[
        sensor_summary["family"].eq(config.focus_family)
        & sensor_summary["model_id"].isin(config.focus_models)
    ]
    focus_sentinel = sentinel_summary.loc[
        sentinel_summary["family"].eq(config.focus_family)
        & sentinel_summary["model_id"].isin(config.focus_models)
    ]
    hotspot_fields = [
        "tract_date_row_count",
        "independent_date_count",
        "independent_spatial_block_count",
        "mean_per_date_average_precision",
        "mean_per_date_precision_at_k",
        "mean_per_date_recall_at_k",
        "mean_per_date_false_negative_rate",
    ]
    b1_ap = float(focus_hotspot.loc["B1", "mean_per_date_average_precision"])
    m2_ap = float(focus_hotspot.loc["M2", "mean_per_date_average_precision"])
    return {
        "schema_version": ENDPOINT_DIAGNOSTICS_SCHEMA_VERSION,
        "algorithm_version": ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "absolute_lst_interpretation": "surface-heat hazard proxy, not human exposure",
        "relative_endpoint": {
            "gate": config.gate_column,
            "gated_independent_date_count": config.expected_relative_gate_dates,
            "label": config.label_column,
            "positive_fraction": config.positive_fraction,
            "prediction_rank_rule": "continuous y_pred descending, tract GEOID ascending",
            "average_precision_uses_continuous_score": True,
            "focus_joint_models": {
                model: {
                    field: (
                        int(focus_hotspot.loc[model, field])
                        if field.endswith("count")
                        else float(focus_hotspot.loc[model, field])
                    )
                    for field in hotspot_fields
                }
                for model in config.focus_models
            },
            "joint_m2_minus_b1_mean_per_date_average_precision": m2_ap - b1_ap,
        },
        "sensor_diagnostics": focus_sensors.to_dict("records"),
        "sentinel_missingness_diagnostics": focus_sentinel.to_dict("records"),
        "input_authentication": dict(authenticated.input_authentication),
    }


def analyze_model_endpoints(
    config_path: str | Path = DEFAULT_ENDPOINT_DIAGNOSTICS_CONFIG,
    *,
    evaluation_directory: str | Path | None = None,
    target_directory: str | Path | None = None,
    model_dataset_directory: str | Path | None = None,
    output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Run and commit all frozen development endpoint diagnostics."""

    config = load_endpoint_diagnostics_config(config_path)
    authenticated = authenticate_endpoint_inputs(
        config,
        evaluation_directory=evaluation_directory,
        target_directory=target_directory,
        model_dataset_directory=model_dataset_directory,
    )
    hotspot_per_date, hotspot_summary = build_hotspot_diagnostics(
        authenticated.oof, config
    )
    sensor_per_date, sensor_summary = build_sensor_diagnostics(
        authenticated.oof, config
    )
    sentinel_summary = build_sentinel_stratum_diagnostics(authenticated.oof, config)
    hotspot_per_date = _append_audit_columns(
        hotspot_per_date, config, authenticated.input_authentication
    )
    hotspot_summary = _append_audit_columns(
        hotspot_summary, config, authenticated.input_authentication
    )
    sensor_per_date = _append_audit_columns(
        sensor_per_date, config, authenticated.input_authentication
    )
    sensor_summary = _append_audit_columns(
        sensor_summary, config, authenticated.input_authentication
    )
    sentinel_summary = _append_audit_columns(
        sentinel_summary, config, authenticated.input_authentication
    )
    summary_payload = _summary_payload(
        config=config,
        authenticated=authenticated,
        hotspot_summary=hotspot_summary,
        sensor_summary=sensor_summary,
        sentinel_summary=sentinel_summary,
    )

    output = (
        config.output_directory
        if output_directory is None
        else Path(output_directory).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        HOTSPOT_PER_DATE_FILENAME: output / HOTSPOT_PER_DATE_FILENAME,
        HOTSPOT_SUMMARY_FILENAME: output / HOTSPOT_SUMMARY_FILENAME,
        SENSOR_PER_DATE_FILENAME: output / SENSOR_PER_DATE_FILENAME,
        SENSOR_SUMMARY_FILENAME: output / SENSOR_SUMMARY_FILENAME,
        SENTINEL_STRATUM_FILENAME: output / SENTINEL_STRATUM_FILENAME,
        SUMMARY_FILENAME: output / SUMMARY_FILENAME,
    }
    frames = {
        HOTSPOT_PER_DATE_FILENAME: hotspot_per_date,
        HOTSPOT_SUMMARY_FILENAME: hotspot_summary,
        SENSOR_PER_DATE_FILENAME: sensor_per_date,
        SENSOR_SUMMARY_FILENAME: sensor_summary,
        SENTINEL_STRATUM_FILENAME: sentinel_summary,
    }
    for filename, frame in frames.items():
        atomic_csv(frame, paths[filename])
    atomic_json(summary_payload, paths[SUMMARY_FILENAME])
    output_files = {
        filename: _csv_record(paths[filename], frame)
        for filename, frame in frames.items()
    }
    output_files[SUMMARY_FILENAME] = _json_record(paths[SUMMARY_FILENAME])
    runtime_sha, runtime_payload = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=_PIPELINE_PATHS,
        algorithm_version=ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION,
    )
    provenance: dict[str, Any] = {
        "schema_version": ENDPOINT_DIAGNOSTICS_SCHEMA_VERSION,
        "algorithm_version": ENDPOINT_DIAGNOSTICS_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_endpoint_interpretation": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_tract_date_rows,
        "independent_date_count": config.expected_independent_dates,
        "independent_spatial_block_count": config.expected_independent_spatial_blocks,
        "relative_endpoint_gate_date_count": config.expected_relative_gate_dates,
        "family_count": len(config.families),
        "model_count": len(config.models),
        "focus_comparison": {
            "family": config.focus_family,
            "model_ids": list(config.focus_models),
        },
        "hotspot_contract": {
            "gate_column": config.gate_column,
            "label_column": config.label_column,
            "positive_fraction": config.positive_fraction,
            "continuous_score_column": "y_pred",
            "exact_top_k": True,
            "tie_break": "tract_geoid_ascending",
            "random_row_resampling_used": False,
        },
        "sensor_contract": {
            "sensors": list(config.sensors),
            "mae_rmse_bias_aggregation": "arithmetic_mean_of_per_date_metrics",
            "spearman_aggregation": "mean_and_median_of_defined_per_date_coefficients",
        },
        "sentinel_strata_contract": {
            "feature_columns": list(config.sentinel_feature_columns),
            "allowed_strata": list(config.allowed_sentinel_strata),
            "partial_missingness_allowed": False,
        },
        "input_authentication": dict(authenticated.input_authentication),
        "compile_provenance_commit_sha256": authenticated.input_authentication[
            "compile_provenance_commit_sha256"
        ],
        "model_dataset_commit_sha256": authenticated.input_authentication[
            "model_dataset_commit_sha256"
        ],
        "analysis_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "pipeline_sha256": runtime_sha,
        "pipeline_fingerprint": runtime_payload,
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / PROVENANCE_FILENAME)
    return provenance
