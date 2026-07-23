"""Build the provisional combined Phase 2 feature-registry contract.

This stage combines only metadata.  It does not read predictor values, target
values, QA tables, or dynamic audit fields, and therefore cannot promote Phase 2
to complete.  The provenance JSON is the commit marker and is written last.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.feature_registry import validate_feature_registry
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_features import INDEX_COLUMNS
from la_heat.weather_daymet import (
    DAYMET_DATASET_DOI,
    DAYMET_VARIABLES,
    DEFAULT_DAYMET_VARIABLES,
    DERIVED_SRAD_ENERGY_COLUMN,
    PRIMARY_WINDOWS_DAYS,
    build_lagged_features,
)

PHASE2_REGISTRY_SCHEMA_VERSION: Final = 1
PHASE2_REGISTRY_ALGORITHM_VERSION: Final = "phase2-combined-feature-registry-v1"
PHASE2_REGISTRY_STATUS: Final = "predeclared_draft"
PHASE2_REGISTRY_FILENAME: Final = "combined_feature_registry_draft.csv"
PHASE2_REGISTRY_PROVENANCE_FILENAME: Final = (
    "combined_feature_registry_draft_provenance.json"
)
DEFAULT_STATIC_REGISTRY_PATH: Final = Path(
    "data/processed/static_features/static_feature_registry.csv"
)
DEFAULT_OUTPUT_DIRECTORY: Final = Path("manifests/phase2_registry")
DEVELOPMENT_START: Final = "2020-05-01"
HISTORICAL_ARCHIVE_AVAILABLE_BY: Final = "historical archive queried 2026-07-18"
DAYMET_SOURCE: Final = (
    f"Daymet V4 R1 daily gridded weather; DOI {DAYMET_DATASET_DOI}"
)
SENTINEL_SOURCE: Final = (
    "Sentinel-2 L2A historical archive (Microsoft Planetary Computer)"
)

# This is the promoted, provenance-locked static registry used by this draft.
# Sorting by feature_name makes the lock invariant to harmless row reordering.
PROMOTED_STATIC_REGISTRY_SEMANTIC_SHA256: Final = (
    "562dbf03ba0ab47c498575cdd03af49091df3ed1ee4a0469fbecdf443bfb27bd"
)
STATIC_FEATURE_ORDER: Final[tuple[str, ...]] = (
    "tract_geoid",
    "target_date",
    "nlcd_open_water_fraction",
    "nlcd_developed_open_fraction",
    "nlcd_developed_low_fraction",
    "nlcd_developed_medium_fraction",
    "nlcd_developed_high_fraction",
    "nlcd_barren_fraction",
    "nlcd_forest_fraction",
    "nlcd_shrub_grass_fraction",
    "nlcd_agriculture_fraction",
    "nlcd_wetland_fraction",
    "impervious_mean_fraction",
    "impervious_p90_fraction",
    "impervious_at_least_50_fraction",
    "elevation_mean_m",
    "elevation_std_m",
    "slope_mean_degrees",
    "slope_p90_degrees",
    "pacific_coast_distance_mean_km",
    "pacific_coast_distance_p10_km",
)
LOCKED_DAYMET_VARIABLES: Final[tuple[str, ...]] = DEFAULT_DAYMET_VARIABLES
DAYMET_FEATURE_COUNT: Final = 21
EXPECTED_TOTAL_ROWS: Final = 49
EXPECTED_MODEL_ROWS: Final = 46

_REGISTRY_COLUMNS: Final[tuple[str, ...]] = (
    "feature_name",
    "family",
    "role",
    "units",
    "source",
    "static",
    "available_by",
    "source_start_offset_days",
    "source_end_offset_days",
)
_DAYMET_WINDOW_PATTERN: Final = re.compile(r"_prev_(1|3|7)d$")


class Phase2RegistryError(ValueError):
    """Raised when the provisional registry cannot be proven reproducible."""


def _static_semantic_sha256(frame: pd.DataFrame) -> str:
    return canonical_frame_sha256(frame, sort_by=["feature_name"])


def _canonical_static_registry(static_registry: pd.DataFrame) -> pd.DataFrame:
    """Validate and canonically order the exact promoted static fragment."""

    if not isinstance(static_registry, pd.DataFrame):
        raise TypeError("The static registry must be a pandas DataFrame.")
    if static_registry.columns.duplicated().any():
        raise Phase2RegistryError("The static registry contains duplicate columns.")
    if set(static_registry.columns) != set(_REGISTRY_COLUMNS):
        missing = sorted(set(_REGISTRY_COLUMNS) - set(static_registry.columns))
        extra = sorted(set(static_registry.columns) - set(_REGISTRY_COLUMNS))
        raise Phase2RegistryError(
            f"Static registry schema mismatch; missing={missing}, extra={extra}."
        )
    working = static_registry.loc[:, _REGISTRY_COLUMNS].copy()
    validate_feature_registry(working, development_start=DEVELOPMENT_START)
    semantic_sha256 = _static_semantic_sha256(working)
    if semantic_sha256 != PROMOTED_STATIC_REGISTRY_SEMANTIC_SHA256:
        raise Phase2RegistryError(
            "Static registry disagrees with the promoted semantic lock: "
            f"{semantic_sha256} != {PROMOTED_STATIC_REGISTRY_SEMANTIC_SHA256}."
        )
    names = working["feature_name"].tolist()
    if set(names) != set(STATIC_FEATURE_ORDER) or len(names) != len(STATIC_FEATURE_ORDER):
        raise Phase2RegistryError("Static registry feature names do not match the frozen order.")
    ordered = working.set_index("feature_name", drop=False).loc[list(STATIC_FEATURE_ORDER)]
    return ordered.reset_index(drop=True)


def _synthetic_daymet_contract() -> pd.DataFrame:
    """Exercise the production lag builder to obtain exact names and units."""

    dates = pd.date_range("2021-01-01", periods=8, freq="D")
    daily: dict[str, object] = {
        "date": dates,
        "year": dates.year,
        "yday": dates.dayofyear,
    }
    for variable in LOCKED_DAYMET_VARIABLES:
        daily[DAYMET_VARIABLES[variable].column] = np.ones(len(dates), dtype=float)
    daily[DERIVED_SRAD_ENERGY_COLUMN] = np.ones(len(dates), dtype=float)
    generated = build_lagged_features(
        pd.DataFrame(daily),
        windows=PRIMARY_WINDOWS_DAYS,
        start=dates[0],
        end=dates[-1],
    )
    if len(generated.columns) != DAYMET_FEATURE_COUNT:
        raise AssertionError(
            "Locked Daymet inputs must generate exactly 21 model features; "
            f"found {len(generated.columns)}."
        )
    return generated


def daymet_feature_registry_rows() -> pd.DataFrame:
    """Return the 21 exact d-n through d-1 Daymet model declarations."""

    generated = _synthetic_daymet_contract()
    units = generated.attrs.get("units")
    if not isinstance(units, Mapping):
        raise AssertionError("Daymet lag builder did not emit a unit registry.")
    rows: list[dict[str, object]] = []
    for feature_name in generated.columns:
        match = _DAYMET_WINDOW_PATTERN.search(str(feature_name))
        if match is None:
            raise AssertionError(f"Unexpected Daymet lag feature name: {feature_name}")
        window = int(match.group(1))
        rows.append(
            {
                "feature_name": str(feature_name),
                "family": "weather",
                "role": "model",
                "units": str(units[feature_name]),
                "source": DAYMET_SOURCE,
                "static": False,
                "available_by": HISTORICAL_ARCHIVE_AVAILABLE_BY,
                "source_start_offset_days": -window,
                "source_end_offset_days": -1,
            }
        )
    return pd.DataFrame(rows, columns=_REGISTRY_COLUMNS)


def sentinel_feature_registry_rows() -> pd.DataFrame:
    """Return the five exact Sentinel d-60 through d-1 model declarations."""

    return pd.DataFrame(
        [
            {
                "feature_name": feature_name,
                "family": "satellite",
                "role": "model",
                "units": "unitless",
                "source": SENTINEL_SOURCE,
                "static": False,
                "available_by": HISTORICAL_ARCHIVE_AVAILABLE_BY,
                "source_start_offset_days": -60,
                "source_end_offset_days": -1,
            }
            for feature_name in INDEX_COLUMNS
        ],
        columns=_REGISTRY_COLUMNS,
    )


def construct_phase2_registry(static_registry: pd.DataFrame) -> pd.DataFrame:
    """Combine the promoted static fragment with predeclared dynamic metadata."""

    static = _canonical_static_registry(static_registry)
    combined = pd.concat(
        [
            static,
            calendar_feature_registry_rows().loc[:, _REGISTRY_COLUMNS],
            daymet_feature_registry_rows(),
            sentinel_feature_registry_rows(),
        ],
        ignore_index=True,
    )
    validate_feature_registry(combined, development_start=DEVELOPMENT_START)
    if len(combined) != EXPECTED_TOTAL_ROWS:
        raise AssertionError(f"Combined registry must contain {EXPECTED_TOTAL_ROWS} rows.")
    counts = combined["role"].value_counts().to_dict()
    if counts != {"model": EXPECTED_MODEL_ROWS, "key": 2, "audit_only": 1}:
        raise AssertionError(f"Combined registry has unexpected role counts: {counts}")
    if combined["feature_name"].duplicated().any():
        raise AssertionError("Combined registry feature names must be unique.")
    return combined.loc[:, _REGISTRY_COLUMNS]


def _read_static_provenance(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase2RegistryError(f"Cannot read static provenance marker {path}.") from exc
    if not isinstance(payload, dict):
        raise Phase2RegistryError("Static provenance marker must contain a JSON object.")
    recorded_commit = payload.pop("commit_sha256", None)
    if not isinstance(recorded_commit, str) or canonical_sha256(payload) != recorded_commit:
        raise Phase2RegistryError("Static provenance commit hash is invalid.")
    payload["commit_sha256"] = recorded_commit
    if payload.get("state") != "complete" or payload.get("promoted_outputs_valid") is not True:
        raise Phase2RegistryError("Static feature stage is not a promoted complete input.")
    return payload


def _read_promoted_static_registry(
    path: Path,
    *,
    provenance_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    frame = pd.read_csv(path)
    after = sha256_file(path)
    if before != after:
        raise Phase2RegistryError("Static registry changed while it was being read.")
    canonical = _canonical_static_registry(frame)
    provenance_file_sha256 = sha256_file(provenance_path)
    provenance = _read_static_provenance(provenance_path)
    try:
        output_record = provenance["output_files"][path.name]
    except (KeyError, TypeError) as exc:
        raise Phase2RegistryError("Static provenance lacks the registry output record.") from exc
    if output_record.get("sha256") != before or output_record.get("rows") != len(frame):
        raise Phase2RegistryError("Static registry file disagrees with its provenance record.")
    if (
        provenance.get("feature_registry_semantic_sha256")
        != PROMOTED_STATIC_REGISTRY_SEMANTIC_SHA256
    ):
        raise Phase2RegistryError("Static provenance disagrees with the semantic registry lock.")
    return canonical, provenance, before, provenance_file_sha256


def _configuration_payload() -> dict[str, Any]:
    return {
        "development_start": DEVELOPMENT_START,
        "static_registry_semantic_sha256": PROMOTED_STATIC_REGISTRY_SEMANTIC_SHA256,
        "calendar_features": list(calendar_feature_registry_rows()["feature_name"]),
        "daymet": {
            "dataset": "Daymet V4 R1",
            "doi": DAYMET_DATASET_DOI,
            "variables": list(LOCKED_DAYMET_VARIABLES),
            "derived_variables": [DERIVED_SRAD_ENERGY_COLUMN],
            "windows_days": list(PRIMARY_WINDOWS_DAYS),
            "window_end_offset_days": -1,
        },
        "sentinel": {
            "dataset": "Sentinel-2 L2A historical archive",
            "features": list(INDEX_COLUMNS),
            "window_start_offset_days": -60,
            "window_end_offset_days": -1,
        },
        "historical_archive_available_by": HISTORICAL_ARCHIVE_AVAILABLE_BY,
    }


def build_phase2_registry(
    static_registry_path: str | Path = DEFAULT_STATIC_REGISTRY_PATH,
    output_directory: str | Path = DEFAULT_OUTPUT_DIRECTORY,
    *,
    static_provenance_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and atomically commit the provisional combined registry metadata."""

    input_path = Path(static_registry_path)
    provenance_path = (
        Path(static_provenance_path)
        if static_provenance_path is not None
        else input_path.with_name("static_features_provenance.json")
    )
    static, static_provenance, input_sha256, provenance_file_sha256 = (
        _read_promoted_static_registry(input_path, provenance_path=provenance_path)
    )
    combined = construct_phase2_registry(static)

    output = Path(output_directory)
    registry_path = output / PHASE2_REGISTRY_FILENAME
    marker = output / PHASE2_REGISTRY_PROVENANCE_FILENAME
    output.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    atomic_csv(combined, registry_path)
    frozen = pd.read_csv(registry_path)
    rebuilt = construct_phase2_registry(static)
    pd.testing.assert_frame_equal(frozen, rebuilt, check_dtype=True)

    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_phase2_registry.py",
            "src/la_heat/calendar_features.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/phase2_registry.py",
            "src/la_heat/provenance.py",
            "src/la_heat/sentinel_features.py",
            "src/la_heat/weather_daymet.py",
        ),
        algorithm_version=PHASE2_REGISTRY_ALGORITHM_VERSION,
    )
    configuration_payload = _configuration_payload()
    ordered_semantic_sha256 = canonical_sha256(frozen.to_dict("records"))
    payload: dict[str, Any] = {
        "schema_version": PHASE2_REGISTRY_SCHEMA_VERSION,
        "algorithm_version": PHASE2_REGISTRY_ALGORITHM_VERSION,
        "status": PHASE2_REGISTRY_STATUS,
        "phase2_complete": False,
        "registry_contract_valid": True,
        "dynamic_values_complete": False,
        "dynamic_coverage_complete": False,
        "target_or_qa_tables_read": [],
        "row_count": len(frozen),
        "role_counts": {
            role: int(count) for role, count in frozen["role"].value_counts().items()
        },
        "family_counts": {
            family: int(count)
            for family, count in frozen["family"].value_counts().items()
        },
        "ordered_registry_semantic_sha256": ordered_semantic_sha256,
        "registry_semantic_sha256": canonical_frame_sha256(
            frozen, sort_by=["feature_name"]
        ),
        "configuration_semantic_sha256": canonical_sha256(configuration_payload),
        "configuration_payload": configuration_payload,
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "inputs": {
            "promoted_static_registry": {
                "path": str(input_path.resolve()),
                "sha256": input_sha256,
                "bytes": input_path.stat().st_size,
                "rows": len(static),
                "semantic_sha256": _static_semantic_sha256(static),
            },
            "promoted_static_provenance": {
                "path": str(provenance_path.resolve()),
                "sha256": provenance_file_sha256,
                "commit_sha256": static_provenance["commit_sha256"],
            },
        },
        "output_files": {
            PHASE2_REGISTRY_FILENAME: {
                "sha256": sha256_file(registry_path),
                "bytes": registry_path.stat().st_size,
                "rows": len(frozen),
            }
        },
        "scientific_contract": {
            "prediction_type": "historical hindcast",
            "prediction_origin": "00:00 Los Angeles civil time on target date",
            "dynamic_observed_predictors_end_by": "target day -1",
            "daymet_window_definition": "complete civil days d-n through d-1",
            "sentinel_window_definition": "physical acquisitions d-60 through d-1",
            "operational_weather_forecast_claimed": False,
            "dynamic_audit_metadata_in_registry": False,
            "draft_reason": (
                "Dynamic predictor values and coverage audits are unfinished; this "
                "artifact freezes metadata only and cannot promote Phase 2."
            ),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, marker)
    return payload
