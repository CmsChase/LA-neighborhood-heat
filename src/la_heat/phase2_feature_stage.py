"""Build the frozen, target-blind Phase 2 predictor table."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import load_config
from la_heat.feature_registry import validate_feature_registry
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

PHASE2_FEATURE_SCHEMA_VERSION: Final = 1
PHASE2_FEATURE_ALGORITHM_VERSION: Final = "phase2-target-blind-feature-assembly-v1"
PHASE2_FEATURE_FILENAME: Final = "phase2_features.parquet"
PHASE2_REGISTRY_FILENAME: Final = "combined_feature_registry.csv"
PHASE2_COVERAGE_FILENAME: Final = "phase2_feature_coverage.csv"
PHASE2_PROVENANCE_FILENAME: Final = "phase2_features_provenance.json"

PRIMARY_KEYS: Final[tuple[str, str]] = ("tract_geoid", "target_date")
DEFAULT_READINESS_PATH: Final = Path(
    "manifests/phase2_readiness/phase2_readiness.json"
)
DEFAULT_UNIVERSE_PATH: Final = Path(
    "data/interim/features/feature_key_universe/feature_key_universe.parquet"
)
DEFAULT_REGISTRY_PATH: Final = Path(
    "manifests/phase2_registry/combined_feature_registry_draft.csv"
)
DEFAULT_STATIC_PATH: Final = Path(
    "data/processed/static_features/static_features.parquet"
)
DEFAULT_CALENDAR_PATH: Final = Path(
    "data/interim/features/calendar/calendar_features.parquet"
)
DEFAULT_SENTINEL_PATH: Final = Path(
    "data/processed/sentinel_features/sentinel_features.parquet"
)
DEFAULT_DAYMET_PATH: Final = Path(
    "data/interim/features/daymet/daymet_features.parquet"
)
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/processed/phase2_features")

_READINESS_INPUT_KEYS: Final[dict[str, str]] = {
    "universe": "feature_key_universe",
    "registry": "phase2_registry",
    "static": "static_features",
    "calendar": "calendar_features",
    "sentinel": "sentinel_features",
    "daymet": "daymet_features",
}
_FAMILY_TO_INPUT: Final[dict[str, str]] = {
    "calendar": "calendar",
    "weather": "daymet",
    "satellite": "sentinel",
}
_FORBIDDEN_NONKEY_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "target_lst_c",
        "target_available",
        "date_usable",
        "lst_anomaly_c",
        "relative_hotspot_top20",
        "prediction",
        "prediction_error",
        "residual",
    }
)


class Phase2FeatureAssemblyError(ValueError):
    """Raised when a frozen Phase 2 feature input fails closed."""


def _resolve(project_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Phase2FeatureAssemblyError(f"Cannot read JSON input {path}.") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"Input changed while being read: {path}")
    if not isinstance(payload, dict):
        raise Phase2FeatureAssemblyError(f"JSON input must be an object: {path}")
    return payload, before


def _verify_canonical_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise Phase2FeatureAssemblyError(f"{label} has an invalid canonical commit hash.")
    return recorded


def _locked_input_sha256(
    readiness: dict[str, Any],
    *,
    key: str,
    requested_path: Path,
) -> str:
    try:
        record = readiness["inputs"][key]
        recorded_path = Path(str(record["path"])).resolve()
        recorded_sha256 = str(record["sha256"])
    except (KeyError, TypeError) as error:
        raise Phase2FeatureAssemblyError(
            f"Readiness manifest lacks input lock {key!r}."
        ) from error
    if recorded_path != requested_path:
        raise Phase2FeatureAssemblyError(
            f"Readiness path lock failed for {key!r}: {recorded_path} != {requested_path}."
        )
    if not requested_path.is_file() or sha256_file(requested_path) != recorded_sha256:
        raise Phase2FeatureAssemblyError(f"Readiness byte lock failed for {key!r}.")
    return recorded_sha256


def _read_locked_parquet(path: Path, *, expected_sha256: str) -> pd.DataFrame:
    before = sha256_file(path)
    if before != expected_sha256:
        raise Phase2FeatureAssemblyError(f"Parquet input failed its byte lock: {path}")
    frame = pd.read_parquet(path)
    if sha256_file(path) != before:
        raise RuntimeError(f"Parquet input changed while being read: {path}")
    return frame


def _read_locked_csv(path: Path, *, expected_sha256: str) -> pd.DataFrame:
    before = sha256_file(path)
    if before != expected_sha256:
        raise Phase2FeatureAssemblyError(f"CSV input failed its byte lock: {path}")
    frame = pd.read_csv(path)
    if sha256_file(path) != before:
        raise RuntimeError(f"CSV input changed while being read: {path}")
    return frame


def _parse_keys(frame: pd.DataFrame, *, label: str, final_test_year: int) -> pd.DataFrame:
    if list(frame.columns[:2]) != list(PRIMARY_KEYS) and set(frame.columns) == set(
        PRIMARY_KEYS
    ):
        frame = frame.loc[:, list(PRIMARY_KEYS)]
    missing = sorted(set(PRIMARY_KEYS) - set(frame.columns))
    if missing:
        raise Phase2FeatureAssemblyError(f"{label} is missing keys: {missing}")
    keys = frame.loc[:, list(PRIMARY_KEYS)].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    try:
        keys["target_date"] = pd.to_datetime(keys["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise Phase2FeatureAssemblyError(f"{label} has invalid target dates.") from error
    if keys.isna().any(axis=None):
        raise Phase2FeatureAssemblyError(f"{label} has missing key values.")
    if keys["target_date"].dt.tz is not None:
        raise Phase2FeatureAssemblyError(f"{label} target dates must be timezone-naive.")
    if not keys["target_date"].dt.normalize().equals(keys["target_date"]):
        raise Phase2FeatureAssemblyError(f"{label} target dates must be civil midnights.")
    if keys.duplicated(list(PRIMARY_KEYS)).any():
        raise Phase2FeatureAssemblyError(f"{label} has duplicate tract-date keys.")
    if keys["target_date"].dt.year.ge(final_test_year).any():
        raise PermissionError(f"{label} contains locked {final_test_year}+ rows.")
    return keys


def _assert_exact_columns(
    frame: pd.DataFrame,
    *,
    expected: list[str],
    label: str,
) -> None:
    if frame.columns.duplicated().any():
        raise Phase2FeatureAssemblyError(f"{label} contains duplicate columns.")
    missing = sorted(set(expected) - set(frame.columns))
    extra = sorted(set(frame.columns) - set(expected))
    if missing or extra:
        raise Phase2FeatureAssemblyError(
            f"{label} schema mismatch: missing={missing}, extra={extra}."
        )


def _assert_exact_dynamic_keys(
    frame: pd.DataFrame,
    universe_keys: pd.DataFrame,
    *,
    label: str,
    final_test_year: int,
) -> pd.DataFrame:
    observed = _parse_keys(frame, label=label, final_test_year=final_test_year)
    comparison = universe_keys.merge(
        observed,
        on=list(PRIMARY_KEYS),
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    missing = int(comparison["_merge"].eq("left_only").sum())
    extra = int(comparison["_merge"].eq("right_only").sum())
    if missing or extra:
        raise Phase2FeatureAssemblyError(
            f"{label} key coverage mismatch: missing={missing}, extra={extra}."
        )
    return observed


def _numeric_values(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    label: str,
    allow_missing: bool,
) -> np.ndarray:
    try:
        numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as error:
        raise Phase2FeatureAssemblyError(f"{label} features must be numeric.") from error
    values = numeric.to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any():
        raise Phase2FeatureAssemblyError(f"{label} features contain infinite values.")
    if not allow_missing and np.isnan(values).any():
        raise Phase2FeatureAssemblyError(f"{label} features contain missing values.")
    return values


def _expected_sentinel_counts(readiness: dict[str, Any]) -> tuple[int, int]:
    statuses = readiness.get("family_status")
    if not isinstance(statuses, list):
        raise Phase2FeatureAssemblyError("Readiness lacks feature-family status records.")
    matches = [
        row for row in statuses if isinstance(row, dict) and row.get("family") == "sentinel"
    ]
    if len(matches) != 1:
        raise Phase2FeatureAssemblyError(
            "Readiness must contain exactly one Sentinel family status."
        )
    try:
        return int(matches[0]["available_row_count"]), int(
            matches[0]["missing_row_count"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise Phase2FeatureAssemblyError(
            "Readiness Sentinel availability counts are invalid."
        ) from error


def _validate_readiness(
    readiness: dict[str, Any],
    *,
    final_test_year: int,
    final_test_unlocked: bool,
) -> str:
    commit = _verify_canonical_commit(readiness, label="Phase 2 readiness")
    required = (
        readiness.get("state") == "ready_for_feature_assembly"
        and readiness.get("audit_completed") is True
        and readiness.get("phase2_complete") is False
        and readiness.get("ready_for_feature_assembly") is True
        and readiness.get("blockers") == []
        and readiness.get("target_blind") is True
        and readiness.get("target_or_qa_tables_read") == []
        and readiness.get("target_values_read") is False
        and readiness.get("model_scores_read") is False
    )
    if not required:
        raise Phase2FeatureAssemblyError("Phase 2 readiness has not authorized assembly.")
    if (
        int(readiness.get("final_test_year", -1)) != final_test_year
        or bool(readiness.get("final_test_unlocked")) != final_test_unlocked
        or readiness.get("contains_final_test_year") is not False
    ):
        raise Phase2FeatureAssemblyError(
            "Readiness disagrees with the locked final-test state."
        )
    if final_test_unlocked:
        raise PermissionError(
            "This target-blind development build requires the final test to remain locked."
        )
    return commit


def _feature_groups(registry: pd.DataFrame) -> dict[str, list[str]]:
    key_names = registry.loc[registry["role"].eq("key"), "feature_name"].tolist()
    if key_names != list(PRIMARY_KEYS):
        raise Phase2FeatureAssemblyError(
            f"Registry key order must be {list(PRIMARY_KEYS)}; found {key_names}."
        )
    nonkeys = registry.loc[~registry["role"].eq("key")].copy()
    forbidden = sorted(set(nonkeys["feature_name"]) & _FORBIDDEN_NONKEY_COLUMNS)
    if forbidden:
        raise Phase2FeatureAssemblyError(
            f"Registry contains target-derived or model-output fields: {forbidden}"
        )
    static = nonkeys.loc[nonkeys["static"].astype(bool), "feature_name"].tolist()
    dynamic = nonkeys.loc[~nonkeys["static"].astype(bool)].copy()
    unknown_families = sorted(set(dynamic["family"]) - set(_FAMILY_TO_INPUT))
    if unknown_families:
        raise Phase2FeatureAssemblyError(
            f"Dynamic registry contains unsupported families: {unknown_families}"
        )
    groups = {"static": static}
    for family, input_name in _FAMILY_TO_INPUT.items():
        groups[input_name] = dynamic.loc[
            dynamic["family"].eq(family), "feature_name"
        ].tolist()
    return groups


def _coverage_table(
    assembled: pd.DataFrame,
    registry: pd.DataFrame,
) -> pd.DataFrame:
    metadata = registry.set_index("feature_name")
    rows: list[dict[str, Any]] = []
    feature_names = registry.loc[~registry["role"].eq("key"), "feature_name"]
    for column in feature_names:
        non_missing = int(assembled[column].notna().sum())
        rows.append(
            {
                "feature_name": column,
                "family": metadata.at[column, "family"],
                "role": metadata.at[column, "role"],
                "static": bool(metadata.at[column, "static"]),
                "row_count": len(assembled),
                "non_missing_count": non_missing,
                "missing_count": int(len(assembled) - non_missing),
                "coverage_fraction": float(non_missing / len(assembled)),
            }
        )
    return pd.DataFrame(rows)


def build_phase2_feature_artifacts(
    config_path: str | Path = "configs/research.toml",
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    *,
    readiness_path: str | Path = DEFAULT_READINESS_PATH,
    universe_path: str | Path = DEFAULT_UNIVERSE_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    static_path: str | Path = DEFAULT_STATIC_PATH,
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
    sentinel_path: str | Path = DEFAULT_SENTINEL_PATH,
    daymet_path: str | Path = DEFAULT_DAYMET_PATH,
) -> dict[str, Any]:
    """Assemble and atomically promote the target-blind Phase 2 feature table."""

    project_root = Path(__file__).resolve().parents[2]
    config = load_config(_resolve(project_root, config_path))
    study = config.raw["study"]
    resolved = {
        "readiness": _resolve(project_root, readiness_path),
        "universe": _resolve(project_root, universe_path),
        "registry": _resolve(project_root, registry_path),
        "static": _resolve(project_root, static_path),
        "calendar": _resolve(project_root, calendar_path),
        "sentinel": _resolve(project_root, sentinel_path),
        "daymet": _resolve(project_root, daymet_path),
    }

    readiness, readiness_sha256 = _read_json_object(resolved["readiness"])
    readiness_commit = _validate_readiness(
        readiness,
        final_test_year=config.final_test_year,
        final_test_unlocked=config.final_test_unlocked,
    )
    input_hashes = {
        label: _locked_input_sha256(
            readiness,
            key=readiness_key,
            requested_path=resolved[label],
        )
        for label, readiness_key in _READINESS_INPUT_KEYS.items()
    }

    universe = _read_locked_parquet(
        resolved["universe"], expected_sha256=input_hashes["universe"]
    )
    registry = _read_locked_csv(
        resolved["registry"], expected_sha256=input_hashes["registry"]
    )
    static = _read_locked_parquet(
        resolved["static"], expected_sha256=input_hashes["static"]
    )
    calendar = _read_locked_parquet(
        resolved["calendar"], expected_sha256=input_hashes["calendar"]
    )
    sentinel = _read_locked_parquet(
        resolved["sentinel"], expected_sha256=input_hashes["sentinel"]
    )
    daymet = _read_locked_parquet(
        resolved["daymet"], expected_sha256=input_hashes["daymet"]
    )

    validate_feature_registry(registry, development_start=str(study["start_date"]))
    groups = _feature_groups(registry)
    ordered_columns = registry["feature_name"].tolist()
    if len(ordered_columns) != len(set(ordered_columns)):
        raise Phase2FeatureAssemblyError("Registry feature names are not unique.")

    _assert_exact_columns(
        universe,
        expected=list(PRIMARY_KEYS),
        label="feature key universe",
    )
    universe_keys = _parse_keys(
        universe,
        label="feature key universe",
        final_test_year=config.final_test_year,
    )
    date_count = int(universe_keys["target_date"].nunique())
    tract_count = int(universe_keys["tract_geoid"].nunique())
    if len(universe_keys) != date_count * tract_count:
        raise Phase2FeatureAssemblyError(
            "Feature key universe is not a complete date-by-tract grid."
        )
    readiness_counts = (
        int(readiness.get("key_count", -1)),
        int(readiness.get("date_count", -1)),
        int(readiness.get("tract_count", -1)),
    )
    if readiness_counts != (len(universe_keys), date_count, tract_count):
        raise Phase2FeatureAssemblyError(
            "Feature key-universe dimensions disagree with the readiness audit."
        )
    role_counts = registry["role"].value_counts().to_dict()
    expected_model_count = int(readiness.get("registry_model_feature_count", -1))
    if role_counts.get("key") != 2 or role_counts.get("model") != expected_model_count:
        raise Phase2FeatureAssemblyError(
            f"Registry role counts disagree with readiness: {role_counts}."
        )

    _assert_exact_columns(
        static,
        expected=["tract_geoid", *groups["static"]],
        label="static feature table",
    )
    static = static.copy()
    static["tract_geoid"] = static["tract_geoid"].astype("string")
    if static["tract_geoid"].isna().any() or static["tract_geoid"].duplicated().any():
        raise Phase2FeatureAssemblyError("Static features require one complete row per tract.")
    expected_tracts = set(universe_keys["tract_geoid"])
    if set(static["tract_geoid"]) != expected_tracts:
        raise Phase2FeatureAssemblyError("Static feature tracts disagree with the universe.")
    _numeric_values(
        static,
        groups["static"],
        label="static",
        allow_missing=False,
    )

    dynamic_frames = {
        "calendar": calendar,
        "sentinel": sentinel,
        "daymet": daymet,
    }
    for label, frame in dynamic_frames.items():
        _assert_exact_columns(
            frame,
            expected=[*PRIMARY_KEYS, *groups[label]],
            label=f"{label} feature table",
        )
        _assert_exact_dynamic_keys(
            frame,
            universe_keys,
            label=f"{label} feature table",
            final_test_year=config.final_test_year,
        )

    _numeric_values(
        calendar,
        groups["calendar"],
        label="calendar",
        allow_missing=False,
    )
    _numeric_values(
        daymet,
        groups["daymet"],
        label="Daymet",
        allow_missing=False,
    )
    sentinel_values = _numeric_values(
        sentinel,
        groups["sentinel"],
        label="Sentinel",
        allow_missing=True,
    )
    sentinel_missing = np.isnan(sentinel_values)
    all_present = ~sentinel_missing.any(axis=1)
    all_missing = sentinel_missing.all(axis=1)
    if not np.logical_or(all_present, all_missing).all():
        raise Phase2FeatureAssemblyError(
            "Sentinel rows must have all registered indices finite or all missing."
        )
    expected_available, expected_missing = _expected_sentinel_counts(readiness)
    if int(all_present.sum()) != expected_available or int(all_missing.sum()) != expected_missing:
        raise Phase2FeatureAssemblyError(
            "Sentinel availability counts disagree with the readiness audit."
        )

    assembled = universe_keys.copy()
    base_keys = assembled.loc[:, list(PRIMARY_KEYS)].copy()
    assembled = assembled.merge(
        static,
        on="tract_geoid",
        how="left",
        sort=False,
        validate="many_to_one",
    )
    for label in ("calendar", "daymet", "sentinel"):
        working = dynamic_frames[label].copy()
        working["tract_geoid"] = working["tract_geoid"].astype("string")
        working["target_date"] = pd.to_datetime(working["target_date"], errors="raise")
        assembled = assembled.merge(
            working,
            on=list(PRIMARY_KEYS),
            how="left",
            sort=False,
            validate="one_to_one",
        )
    if len(assembled) != len(universe_keys):
        raise AssertionError("Phase 2 assembly changed the frozen row count.")
    if not assembled.loc[:, list(PRIMARY_KEYS)].equals(base_keys):
        raise AssertionError("Phase 2 assembly changed the frozen key order or values.")
    if set(assembled.columns) != set(ordered_columns):
        raise Phase2FeatureAssemblyError("Assembled columns disagree with the registry.")
    assembled = assembled.loc[:, ordered_columns]

    nonkey_columns = [name for name in ordered_columns if name not in PRIMARY_KEYS]
    assembled[nonkey_columns] = assembled[nonkey_columns].apply(
        pd.to_numeric, errors="raise"
    )
    values = assembled[nonkey_columns].to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any():
        raise Phase2FeatureAssemblyError("Assembled predictors contain infinite values.")

    model_columns = registry.loc[registry["role"].eq("model"), "feature_name"].tolist()
    audit_columns = registry.loc[
        registry["role"].eq("audit_only"), "feature_name"
    ].tolist()
    model_missing = {
        column: int(count) for column, count in assembled[model_columns].isna().sum().items()
    }
    complete_model_rows = int(assembled[model_columns].notna().all(axis=1).sum())
    coverage = _coverage_table(assembled, registry)

    output = _resolve(project_root, output_directory)
    table_path = output / PHASE2_FEATURE_FILENAME
    registry_snapshot_path = output / PHASE2_REGISTRY_FILENAME
    coverage_path = output / PHASE2_COVERAGE_FILENAME
    marker_path = output / PHASE2_PROVENANCE_FILENAME
    output.mkdir(parents=True, exist_ok=True)
    marker_path.unlink(missing_ok=True)
    atomic_parquet(assembled, table_path)
    atomic_csv(registry, registry_snapshot_path)
    atomic_csv(coverage, coverage_path)
    frozen = pd.read_parquet(table_path)
    pd.testing.assert_frame_equal(frozen, assembled, check_dtype=True)

    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_phase2_features.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/phase2_feature_stage.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=PHASE2_FEATURE_ALGORITHM_VERSION,
    )
    payload: dict[str, Any] = {
        "schema_version": PHASE2_FEATURE_SCHEMA_VERSION,
        "algorithm_version": PHASE2_FEATURE_ALGORITHM_VERSION,
        "state": "complete",
        "phase2_complete": True,
        "ready_for_target_join": True,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "model_scores_read": False,
        "assembled_at_utc": datetime.now(UTC).isoformat(),
        "readiness_commit_sha256": readiness_commit,
        "final_test_year": config.final_test_year,
        "final_test_unlocked": config.final_test_unlocked,
        "contains_final_test_year": False,
        "row_count": len(assembled),
        "column_count": assembled.shape[1],
        "date_count": date_count,
        "tract_count": tract_count,
        "registry_row_count": len(registry),
        "model_feature_count": len(model_columns),
        "audit_only_feature_count": len(audit_columns),
        "complete_model_feature_rows": complete_model_rows,
        "incomplete_model_feature_rows": int(len(assembled) - complete_model_rows),
        "missing_count_by_model_feature": model_missing,
        "ordered_columns": ordered_columns,
        "ordered_model_feature_names": model_columns,
        "ordered_audit_only_feature_names": audit_columns,
        "semantic_feature_table_sha256": canonical_frame_sha256(
            assembled,
            sort_by=["target_date", "tract_geoid"],
            columns=ordered_columns,
        ),
        "registry_semantic_sha256": canonical_frame_sha256(
            registry, sort_by=["feature_name"]
        ),
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "inputs": {
            "phase2_readiness": {
                "path": str(resolved["readiness"]),
                "sha256": readiness_sha256,
                "commit_sha256": readiness_commit,
            },
            **{
                label: {"path": str(resolved[label]), "sha256": sha256}
                for label, sha256 in input_hashes.items()
            },
        },
        "output_files": {
            PHASE2_FEATURE_FILENAME: {
                "path": str(table_path),
                **parquet_file_record(table_path, assembled),
            },
            PHASE2_REGISTRY_FILENAME: {
                "path": str(registry_snapshot_path),
                "sha256": sha256_file(registry_snapshot_path),
                "bytes": registry_snapshot_path.stat().st_size,
                "rows": len(registry),
            },
            PHASE2_COVERAGE_FILENAME: {
                "path": str(coverage_path),
                "sha256": sha256_file(coverage_path),
                "bytes": coverage_path.stat().st_size,
                "rows": len(coverage),
            },
        },
        "scientific_contract": {
            "outcome": "QA-filtered daytime Landsat land-surface temperature",
            "outcome_interpretation": "surface-heat hazard proxy",
            "prediction_type": "historical hindcast",
            "prediction_origin": "00:00 Los Angeles civil time on target date",
            "dynamic_observed_predictors_end_by": "target day -1",
            "same_scene_or_thermal_predictors_used": False,
            "target_or_qa_data_accessed": False,
            "fold_local_preprocessing_still_required": True,
            "random_row_split_allowed": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker_path)
    return payload
