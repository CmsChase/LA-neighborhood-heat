"""Cache-preserving final compilation for frozen Sentinel acquisitions.

The frozen composite function validates one target date at a time as a
``one_to_many`` acquisition-to-tract expansion.  A full inventory reuses one
physical acquisition in several target-date windows, so invoking that function
on all dates at once incorrectly violates its validation contract before any
aggregation occurs.  This adapter executes the exact frozen scientific
function independently for each target date and concatenates those independent
outputs.  It does not alter per-acquisition values, cache locks, masks, windows,
or aggregation formulas.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from la_heat.config import ResearchConfig
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    parquet_file_record,
)
from la_heat.sentinel_feature_builder import (
    FixedSpatialSupport,
    FrozenSentinelInputs,
    SentinelStageConfig,
    _acquisition_cache_directory,
    _acquisition_cache_is_current,
    _expected_acquisition_lock,
    _research_dependency_payload,
)
from la_heat.sentinel_features import (
    CompositeArtifacts,
    build_previous_60_day_composites,
)

COMPILE_ADAPTER_VERSION = "sentinel-target-sharded-compile-v1"


def build_previous_60_day_composites_by_target(
    acquisition_tract: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    target_dates: Sequence[date | str],
    tract_geoids: Sequence[str],
    minimum_acquisition_coverage: float,
    minimum_acquisitions: int,
    final_test_year: int,
    unlock_final_test: bool,
) -> CompositeArtifacts:
    """Run the frozen composite independently for each target date.

    Target-date groups are mathematically independent: all aggregations and
    availability gates group by ``target_date`` and ``tract_geoid``.  Sharding
    therefore changes only join validation scope, not feature values.
    """

    normalized_targets = pd.to_datetime(
        pd.Series([str(value) for value in target_dates]),
        format="%Y-%m-%d",
        errors="raise",
    )
    if normalized_targets.empty:
        raise ValueError("Target dates must be non-empty.")
    if normalized_targets.isna().any() or normalized_targets.duplicated().any():
        raise ValueError("Target dates must be non-missing and unique.")
    if "target_date" not in membership or "physical_acquisition_id" not in membership:
        raise ValueError("Membership lacks target_date or physical_acquisition_id.")
    if "physical_acquisition_id" not in acquisition_tract:
        raise ValueError("Acquisition table lacks physical_acquisition_id.")

    member_dates = pd.to_datetime(
        membership["target_date"], format="%Y-%m-%d", errors="raise"
    )
    declared_dates = set(normalized_targets)
    if not set(member_dates).issubset(declared_dates):
        raise ValueError("Membership contains an undeclared target date.")

    acquisition_ids = acquisition_tract["physical_acquisition_id"].astype(str)
    artifacts: list[CompositeArtifacts] = []
    for target in sorted(normalized_targets):
        target_members = membership.loc[member_dates.eq(target)].copy()
        physical_ids = set(target_members["physical_acquisition_id"].astype(str))
        target_acquisition = acquisition_tract.loc[
            acquisition_ids.isin(physical_ids)
        ].copy()
        artifacts.append(
            build_previous_60_day_composites(
                target_acquisition,
                target_members,
                target_dates=[target.date().isoformat()],
                tract_geoids=tract_geoids,
                minimum_acquisition_coverage=minimum_acquisition_coverage,
                minimum_acquisitions=minimum_acquisitions,
                final_test_year=final_test_year,
                unlock_final_test=unlock_final_test,
            )
        )

    features = pd.concat(
        [artifact.features for artifact in artifacts], ignore_index=True
    )
    audit = pd.concat([artifact.audit for artifact in artifacts], ignore_index=True)
    lineage = pd.concat(
        [artifact.lineage for artifact in artifacts], ignore_index=True
    )
    expected_grid_rows = len(normalized_targets) * len(tuple(tract_geoids))
    if len(features) != expected_grid_rows or len(audit) != expected_grid_rows:
        raise AssertionError("Target-sharded compilation lost feature or audit rows.")
    if features.duplicated(["target_date", "tract_geoid"]).any():
        raise AssertionError("Target-sharded features contain duplicate keys.")
    if audit.duplicated(["target_date", "tract_geoid"]).any():
        raise AssertionError("Target-sharded audit contains duplicate keys.")
    expected_lineage_rows = len(membership) * len(tuple(tract_geoids))
    if len(lineage) != expected_lineage_rows:
        raise AssertionError("Target-sharded compilation lost lineage rows.")
    if lineage.duplicated(
        ["target_date", "tract_geoid", "physical_acquisition_id"]
    ).any():
        raise AssertionError("Target-sharded lineage contains duplicate keys.")
    return CompositeArtifacts(features=features, audit=audit, lineage=lineage)


def compile_outputs_from_current_caches(
    *,
    inventory: FrozenSentinelInputs,
    spatial: FixedSpatialSupport,
    stage: SentinelStageConfig,
    research: ResearchConfig,
    base_lock: dict[str, str],
    output_directory: Path,
    runner_sha256: str,
    runner_version: str,
) -> dict[str, Any]:
    """Validate frozen caches and atomically promote target-sharded outputs."""

    acquisition_frames: list[pd.DataFrame] = []
    current_ids: list[str] = []
    for row in inventory.acquisitions.itertuples(index=False):
        physical_id = str(row.physical_acquisition_id)
        item_rows = inventory.items.loc[
            inventory.items["physical_acquisition_id"] == physical_id
        ]
        directory = _acquisition_cache_directory(output_directory, physical_id)
        expected = _expected_acquisition_lock(
            base_lock=base_lock,
            physical_id=physical_id,
            item_rows=item_rows,
        )
        if _acquisition_cache_is_current(directory, expected_lock=expected):
            acquisition_frames.append(
                pd.read_parquet(directory / "acquisition_tract.parquet")
            )
            current_ids.append(physical_id)

    expected_count = len(inventory.acquisitions)
    complete = len(acquisition_frames) == expected_count
    promoted = [
        output_directory / "acquisition_tract.parquet",
        output_directory / "sentinel_features.parquet",
        output_directory / "sentinel_feature_audit.parquet",
        output_directory / "sentinel_lineage.parquet",
    ]
    partial = output_directory / "acquisition_tract_partial.parquet"
    progress: dict[str, Any] = {
        **base_lock,
        "sentinel_stage_config_payload": stage.raw,
        "sentinel_research_dependency_payload": _research_dependency_payload(research),
        "state": "building" if not complete else "compiling",
        "promoted_outputs_valid": False,
        "expected_physical_acquisition_count": expected_count,
        "completed_physical_acquisition_count": len(acquisition_frames),
        "build_complete": complete,
        "completed_physical_acquisition_ids_sha256": canonical_sha256(current_ids),
        "compile_adapter_version_audit_only": COMPILE_ADAPTER_VERSION,
        "dashboard_runner_sha256_audit_only": runner_sha256,
        "dashboard_runner_version_audit_only": runner_version,
    }
    if not acquisition_frames:
        for path in [*promoted, partial]:
            path.unlink(missing_ok=True)
        progress["state"] = "no_current_acquisition_caches"
        atomic_json(progress, output_directory / "build_progress.json")
        return progress

    acquisition_tract = pd.concat(acquisition_frames, ignore_index=True)
    if acquisition_tract.duplicated(
        ["tract_geoid", "physical_acquisition_id"]
    ).any():
        raise ValueError("Compiled acquisition table contains duplicate keys.")
    if not complete:
        for path in promoted:
            path.unlink(missing_ok=True)
        atomic_parquet(acquisition_tract, partial)
        progress["state"] = "partial_ready"
        progress["partial_output"] = parquet_file_record(partial, acquisition_tract)
        atomic_json(progress, output_directory / "build_progress.json")
        return progress

    partial.unlink(missing_ok=True)
    composites = build_previous_60_day_composites_by_target(
        acquisition_tract,
        inventory.membership,
        target_dates=spatial.target_dates,
        tract_geoids=spatial.tract_geoids,
        minimum_acquisition_coverage=stage.minimum_coverage,
        minimum_acquisitions=stage.minimum_acquisitions,
        final_test_year=research.final_test_year,
        unlock_final_test=research.final_test_unlocked,
    )
    output_frames = [
        acquisition_tract,
        composites.features,
        composites.audit,
        composites.lineage,
    ]
    for path, frame in zip(promoted, output_frames, strict=True):
        atomic_parquet(frame, path)
    progress["aggregate_outputs"] = {
        path.name: parquet_file_record(path, frame)
        for path, frame in zip(promoted, output_frames, strict=True)
    }
    progress["state"] = "complete"
    progress["promoted_outputs_valid"] = True
    progress["feature_row_count"] = len(composites.features)
    progress["feature_available_row_count"] = int(
        composites.audit["sentinel_feature_available"].sum()
    )
    progress["target_date_count"] = len(spatial.target_dates)
    progress["tract_count"] = len(spatial.tract_geoids)
    progress["lineage_row_count"] = len(composites.lineage)
    atomic_json(progress, output_directory / "build_progress.json")
    return progress
