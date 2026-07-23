"""Commit target-blind calendar features on the frozen Phase 2 key universe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from la_heat.calendar_features import (
    CALENDAR_KEY_COLUMNS,
    build_calendar_features,
)
from la_heat.feature_registry import CALENDAR_MODEL_FEATURE_NAMES
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

CALENDAR_STAGE_SCHEMA_VERSION = 1
CALENDAR_STAGE_ALGORITHM_VERSION = "calendar-features-known-at-origin-v1"
CALENDAR_STAGE_STATUS = "target_blind_draft"
CALENDAR_FEATURE_FILENAME = "calendar_features.parquet"
CALENDAR_PROVENANCE_FILENAME = "calendar_features_provenance.json"
DEFAULT_FEATURE_UNIVERSE_PATH = Path(
    "data/interim/features/feature_key_universe/feature_key_universe.parquet"
)
DEFAULT_CALENDAR_OUTPUT_DIRECTORY = Path("data/interim/features/calendar")


def build_calendar_feature_artifacts(
    feature_universe_path: str | Path = DEFAULT_FEATURE_UNIVERSE_PATH,
    output_directory: str | Path = DEFAULT_CALENDAR_OUTPUT_DIRECTORY,
    *,
    final_test_year: int = 2025,
    unlock_final_test: bool = False,
) -> dict[str, Any]:
    """Build the complete calendar table and write provenance last as its marker."""

    universe_path = Path(feature_universe_path)
    output = Path(output_directory)
    if not universe_path.is_file():
        raise FileNotFoundError(f"Feature key universe does not exist: {universe_path}")

    input_sha256 = sha256_file(universe_path)
    try:
        keys = pd.read_parquet(universe_path, columns=list(CALENDAR_KEY_COLUMNS))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Feature universe lacks the exact calendar key columns.") from exc
    if sha256_file(universe_path) != input_sha256:
        raise RuntimeError("Feature key universe changed while it was being read.")

    features = build_calendar_features(
        keys,
        final_test_year=final_test_year,
        unlock_final_test=unlock_final_test,
    )
    if len(features) != len(keys):
        raise AssertionError("Calendar stage changed the frozen key count.")
    input_key_sha256 = canonical_frame_sha256(
        keys,
        sort_by=["target_date", "tract_geoid"],
        columns=list(CALENDAR_KEY_COLUMNS),
    )
    output_key_sha256 = canonical_frame_sha256(
        features,
        sort_by=["target_date", "tract_geoid"],
        columns=list(CALENDAR_KEY_COLUMNS),
    )
    if output_key_sha256 != input_key_sha256:
        raise AssertionError("Calendar stage changed the frozen tract-date keys.")

    output.mkdir(parents=True, exist_ok=True)
    feature_path = output / CALENDAR_FEATURE_FILENAME
    provenance_path = output / CALENDAR_PROVENANCE_FILENAME
    provenance_path.unlink(missing_ok=True)
    atomic_parquet(features, feature_path)
    frozen = pd.read_parquet(feature_path)
    pd.testing.assert_frame_equal(frozen, features, check_dtype=True)

    project_root = Path(__file__).resolve().parents[2]
    pipeline_sha256, pipeline_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=(
            "scripts/build_calendar_features.py",
            "src/la_heat/calendar_feature_stage.py",
            "src/la_heat/calendar_features.py",
            "src/la_heat/feature_registry.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=CALENDAR_STAGE_ALGORITHM_VERSION,
    )
    payload: dict[str, Any] = {
        "schema_version": CALENDAR_STAGE_SCHEMA_VERSION,
        "algorithm_version": CALENDAR_STAGE_ALGORITHM_VERSION,
        "status": CALENDAR_STAGE_STATUS,
        "phase2_promoted": False,
        "target_blind": True,
        "final_test_year": final_test_year,
        "final_test_unlocked": unlock_final_test,
        "row_count": len(features),
        "date_count": int(features["target_date"].nunique()),
        "tract_count": int(features["tract_geoid"].nunique()),
        "feature_names": list(CALENDAR_MODEL_FEATURE_NAMES),
        "feature_units": "unitless",
        "formula": "theta=2*pi*(dayofyear-1)/(365+is_leap_year); sin(theta), cos(theta)",
        "input_file": {
            "path": str(universe_path.resolve()),
            "sha256": input_sha256,
            "semantic_key_sha256": input_key_sha256,
            "columns_read": list(CALENDAR_KEY_COLUMNS),
        },
        "semantic_key_sha256": output_key_sha256,
        "semantic_table_sha256": canonical_frame_sha256(
            features,
            sort_by=["target_date", "tract_geoid"],
        ),
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_payload,
        "output_files": {
            feature_path.name: parquet_file_record(feature_path, frozen),
        },
        "remaining_gate": (
            "Calendar features are complete, but Phase 2 still requires Daymet, "
            "Sentinel composites, coverage audit, and combined-table promotion."
        ),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, provenance_path)
    return payload
