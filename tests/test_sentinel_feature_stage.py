from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import la_heat.sentinel_feature_stage as stage_module
from la_heat.config import load_config
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.sentinel_feature_builder import load_sentinel_stage_config
from la_heat.sentinel_feature_stage import (
    AUDIT_VALUE_COLUMNS,
    COVERAGE_BY_DATE_FILENAME,
    COVERAGE_BY_TRACT_FILENAME,
    EXPECTED_COMPILE_ADAPTER_VERSION,
    LINEAGE_REQUIRED_COLUMNS,
    PROMOTED_AUDIT_FILENAME,
    PROMOTED_FEATURE_COLUMNS,
    PROMOTED_FEATURE_FILENAME,
    PROMOTION_PROVENANCE_FILENAME,
    SentinelFeaturePromotionError,
    promote_sentinel_features,
)
from la_heat.sentinel_features import INDEX_COLUMNS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_CONFIG = PROJECT_ROOT / "configs/research.toml"
SENTINEL_CONFIG = PROJECT_ROOT / "configs/sentinel_features.toml"
REGISTRY = PROJECT_ROOT / "manifests/phase2_registry/combined_feature_registry_draft.csv"
REGISTRY_PROVENANCE = REGISTRY.with_name(
    "combined_feature_registry_draft_provenance.json"
)


@dataclass(frozen=True)
class FixturePaths:
    source: Path
    output: Path
    universe: Path
    universe_provenance: Path
    inventory: Path
    scientific_sha256: str


def _write_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_committed(payload: dict[str, object], path: Path) -> None:
    committed = dict(payload)
    committed["commit_sha256"] = canonical_sha256(committed)
    _write_json(committed, path)


def _parquet_record_after_round_trip(path: Path) -> dict[str, object]:
    return parquet_file_record(path, pd.read_parquet(path))


def _synthetic_frames() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    dates = pd.to_datetime(["2024-07-01", "2024-07-15"])
    tracts = ("06037000100", "06037000200")
    universe = pd.MultiIndex.from_product(
        [tracts, dates], names=["tract_geoid", "target_date"]
    ).to_frame(index=False)
    universe["tract_geoid"] = universe["tract_geoid"].astype("string")
    universe["target_date"] = universe["target_date"].astype("datetime64[ns]")

    memberships: list[dict[str, object]] = []
    lineage_rows: list[dict[str, object]] = []
    for date_index, target_date in enumerate(dates):
        for acquisition_index, lag in enumerate((5, 15, 25)):
            physical_id = f"physical-{date_index}-{acquisition_index}"
            acquisition_date = target_date - pd.Timedelta(days=lag)
            memberships.append(
                {
                    "target_date": target_date.strftime("%Y-%m-%d"),
                    "physical_acquisition_id": physical_id,
                    "acquisition_local_date": acquisition_date.strftime("%Y-%m-%d"),
                    "lag_days": lag,
                }
            )
            for tract_index, geoid in enumerate(tracts):
                coverage = (
                    0.7
                    if date_index == 1
                    and tract_index == 1
                    and acquisition_index == 2
                    else 0.9
                )
                row: dict[str, object] = {
                    "target_date": target_date.strftime("%Y-%m-%d"),
                    "physical_acquisition_id": physical_id,
                    "acquisition_local_date": acquisition_date.strftime("%Y-%m-%d"),
                    "lag_days": lag,
                    "tract_geoid": geoid,
                    "eligible_pixel_count_static": 100 + tract_index,
                    "acquisition_coverage_fraction": coverage,
                    "eligible_pixel_identity_sha256_audit_only": hashlib.sha256(
                        geoid.encode("utf-8")
                    ).hexdigest(),
                    "included_in_composite": coverage >= 0.8,
                    "source_end_date": acquisition_date.strftime("%Y-%m-%d"),
                    "source_age_days_audit_only": lag,
                }
                for feature_index, column in enumerate(INDEX_COLUMNS):
                    row[column] = (
                        date_index
                        + tract_index / 10
                        + acquisition_index / 100
                        + feature_index / 1000
                    )
                lineage_rows.append(row)
    membership = pd.DataFrame(memberships)
    lineage = pd.DataFrame(lineage_rows).loc[:, LINEAGE_REQUIRED_COLUMNS]

    normalized = lineage.copy()
    for column in ("target_date", "acquisition_local_date", "source_end_date"):
        normalized[column] = pd.to_datetime(normalized[column]).astype("datetime64[ns]")
    keys = ["tract_geoid", "target_date"]
    audit = (
        normalized.groupby(keys, sort=False)
        .agg(
            window_membership_count=("physical_acquisition_id", "size"),
            qualifying_acquisition_count=("included_in_composite", "sum"),
            minimum_lag_days=("lag_days", "min"),
            maximum_lag_days=("lag_days", "max"),
            median_acquisition_coverage=("acquisition_coverage_fraction", "median"),
            newest_source_end_date=("source_end_date", "max"),
            oldest_source_end_date=("source_end_date", "min"),
        )
        .reset_index()
    )
    audit["sentinel_feature_available"] = audit[
        "qualifying_acquisition_count"
    ].ge(3)
    audit = audit.loc[:, [*keys, *AUDIT_VALUE_COLUMNS]]

    medians = (
        normalized.loc[normalized["included_in_composite"]]
        .groupby(keys, sort=False)[list(INDEX_COLUMNS)]
        .median()
        .reset_index()
    )
    features = universe.merge(medians, on=keys, how="left", validate="one_to_one")
    availability = audit.set_index(keys)["sentinel_feature_available"]
    index = pd.MultiIndex.from_frame(features[keys])
    features.loc[
        ~availability.reindex(index).to_numpy(dtype=bool), list(INDEX_COLUMNS)
    ] = np.nan
    features["target_date"] = features["target_date"].dt.strftime("%Y-%m-%d")
    features = features.loc[:, ["target_date", "tract_geoid", *INDEX_COLUMNS]]
    audit["target_date"] = audit["target_date"].dt.strftime("%Y-%m-%d")
    audit = audit.loc[:, ["target_date", "tract_geoid", *AUDIT_VALUE_COLUMNS]]
    return universe, features, audit, lineage, membership


def _make_fixture(tmp_path: Path) -> FixturePaths:
    source = tmp_path / "source"
    output = tmp_path / "promoted"
    universe_path = tmp_path / "universe" / "feature_key_universe.parquet"
    universe_provenance = universe_path.with_name(
        "feature_key_universe_provenance.json"
    )
    inventory = tmp_path / "inventory"
    source.mkdir(parents=True)
    inventory.mkdir(parents=True)

    universe, features, audit, lineage, membership = _synthetic_frames()
    universe_path.parent.mkdir(parents=True)
    universe.to_parquet(universe_path, index=False)
    features.to_parquet(source / PROMOTED_FEATURE_FILENAME, index=False)
    audit.to_parquet(source / PROMOTED_AUDIT_FILENAME, index=False)
    lineage.to_parquet(source / "sentinel_lineage.parquet", index=False)
    membership.to_csv(inventory / "target_window_membership.csv", index=False)

    universe_payload: dict[str, object] = {
        "target_blind": True,
        "target_tables_read": [],
        "final_test_year": 2025,
        "semantic_key_sha256": canonical_frame_sha256(
            universe,
            sort_by=["target_date", "tract_geoid"],
            columns=["tract_geoid", "target_date"],
        ),
        "output_files": {
            universe_path.name: _parquet_record_after_round_trip(universe_path)
        },
    }
    _write_committed(universe_payload, universe_provenance)

    membership_path = inventory / "target_window_membership.csv"
    inventory_semantic = canonical_sha256({"fixture": "sentinel-inventory"})
    inventory_payload: dict[str, object] = {
        "state": "complete",
        "artifacts_valid": True,
        "final_test_year": 2025,
        "unlock_final_test": False,
        "global_scene_cloud_cover_filter": None,
        "sentinel_inventory_semantic_sha256": inventory_semantic,
        "output_files": {
            membership_path.name: {
                "sha256": sha256_file(membership_path),
                "bytes": membership_path.stat().st_size,
                "rows": len(membership),
            }
        },
    }
    inventory_summary_path = inventory / "inventory_summary.json"
    _write_json(inventory_payload, inventory_summary_path)

    fingerprint: dict[str, object] = {
        "algorithm_version": "sentinel-optical-v1-physical-mosaic-fixed-support",
        "fixture": True,
    }
    fingerprint_path = source / "pipeline_fingerprint.json"
    _write_json(fingerprint, fingerprint_path)
    scientific_sha256 = canonical_sha256(fingerprint)
    research = load_config(RESEARCH_CONFIG)
    sentinel = load_sentinel_stage_config(SENTINEL_CONFIG)
    research_dependency = {
        "study": {
            "final_test_year": research.final_test_year,
            "unlock_final_test": research.final_test_unlocked,
        },
        "static_land_mask": research.raw["static_land_mask"],
    }
    progress: dict[str, object] = {
        "state": "complete",
        "promoted_outputs_valid": True,
        "build_complete": True,
        "expected_physical_acquisition_count": membership[
            "physical_acquisition_id"
        ].nunique(),
        "completed_physical_acquisition_count": membership[
            "physical_acquisition_id"
        ].nunique(),
        "compile_adapter_version_audit_only": EXPECTED_COMPILE_ADAPTER_VERSION,
        "sentinel_feature_pipeline_sha256": scientific_sha256,
        "sentinel_feature_pipeline_fingerprint_file_sha256": sha256_file(
            fingerprint_path
        ),
        "sentinel_stage_config_sha256": sentinel.sha256,
        "sentinel_stage_config_payload": sentinel.raw,
        "research_config_file_sha256_audit_only": sha256_file(RESEARCH_CONFIG),
        "sentinel_research_dependency_payload": research_dependency,
        "sentinel_research_dependency_sha256": canonical_sha256(research_dependency),
        "sentinel_inventory_summary_sha256_audit_only": sha256_file(
            inventory_summary_path
        ),
        "sentinel_inventory_semantic_sha256": inventory_semantic,
        "sentinel_target_window_membership_csv_sha256": sha256_file(membership_path),
        "aggregate_outputs": {
            PROMOTED_FEATURE_FILENAME: _parquet_record_after_round_trip(
                source / PROMOTED_FEATURE_FILENAME
            ),
            PROMOTED_AUDIT_FILENAME: _parquet_record_after_round_trip(
                source / PROMOTED_AUDIT_FILENAME
            ),
            "sentinel_lineage.parquet": _parquet_record_after_round_trip(
                source / "sentinel_lineage.parquet"
            ),
        },
        "feature_row_count": len(features),
        "feature_available_row_count": int(
            audit["sentinel_feature_available"].sum()
        ),
        "target_date_count": universe["target_date"].nunique(),
        "tract_count": universe["tract_geoid"].nunique(),
        "lineage_row_count": len(lineage),
    }
    _write_json(progress, source / "build_progress.json")
    return FixturePaths(
        source=source,
        output=output,
        universe=universe_path,
        universe_provenance=universe_provenance,
        inventory=inventory,
        scientific_sha256=scientific_sha256,
    )


def _promote(paths: FixturePaths) -> dict[str, object]:
    return promote_sentinel_features(
        source_directory=paths.source,
        output_directory=paths.output,
        feature_universe_path=paths.universe,
        feature_universe_provenance_path=paths.universe_provenance,
        registry_path=REGISTRY,
        registry_provenance_path=REGISTRY_PROVENANCE,
        inventory_directory=paths.inventory,
        research_config_path=RESEARCH_CONFIG,
        sentinel_config_path=SENTINEL_CONFIG,
    )


def _rewrite_source_parquet(
    paths: FixturePaths,
    filename: str,
    frame: pd.DataFrame,
) -> None:
    path = paths.source / filename
    frame.to_parquet(path, index=False)
    progress_path = paths.source / "build_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress["aggregate_outputs"][filename] = _parquet_record_after_round_trip(path)
    _write_json(progress, progress_path)


def test_promotion_writes_canonical_target_blind_outputs_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )

    payload = _promote(paths)

    features = pd.read_parquet(paths.output / PROMOTED_FEATURE_FILENAME)
    audit = pd.read_parquet(paths.output / PROMOTED_AUDIT_FILENAME)
    by_date = pd.read_parquet(paths.output / COVERAGE_BY_DATE_FILENAME)
    by_tract = pd.read_parquet(paths.output / COVERAGE_BY_TRACT_FILENAME)
    assert tuple(features.columns) == PROMOTED_FEATURE_COLUMNS
    assert str(features["target_date"].dtype) == "datetime64[ns]"
    assert features.duplicated(["tract_geoid", "target_date"]).sum() == 0
    assert features[list(INDEX_COLUMNS)].isna().all(axis=1).sum() == 1
    assert audit["sentinel_feature_available"].sum() == 3
    assert len(by_date) == 2
    assert len(by_tract) == 2
    assert payload["state"] == "complete"
    assert payload["promoted_outputs_valid"] is True
    assert payload["phase2_complete"] is False
    assert payload["target_blind"] is True
    assert payload["target_or_qa_tables_read"] == []
    assert payload["feature_available_row_count"] == 3
    assert payload["feature_missing_row_count"] == 1
    assert payload["coverage_contract"]["imputation_performed"] is False
    assert payload["coverage_contract"]["rows_removed_for_sentinel_missingness"] == 0

    marker = json.loads(
        (paths.output / PROMOTION_PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    commit = marker.pop("commit_sha256")
    assert canonical_sha256(marker) == commit


def test_failure_removes_prior_commit_and_rejects_partial_feature_missingness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    _promote(paths)
    assert (paths.output / PROMOTION_PROVENANCE_FILENAME).is_file()
    features = pd.read_parquet(paths.source / PROMOTED_FEATURE_FILENAME)
    features.loc[0, INDEX_COLUMNS[0]] = np.nan
    _rewrite_source_parquet(paths, PROMOTED_FEATURE_FILENAME, features)

    with pytest.raises(SentinelFeaturePromotionError, match="all present or all missing"):
        _promote(paths)

    assert not (paths.output / PROMOTION_PROVENANCE_FILENAME).exists()


def test_promotion_rejects_availability_inconsistent_with_three_acquisitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    audit = pd.read_parquet(paths.source / PROMOTED_AUDIT_FILENAME)
    unavailable = ~audit["sentinel_feature_available"]
    audit.loc[unavailable, "sentinel_feature_available"] = True
    _rewrite_source_parquet(paths, PROMOTED_AUDIT_FILENAME, audit)

    with pytest.raises(SentinelFeaturePromotionError, match="does not reproduce"):
        _promote(paths)


def test_promotion_rejects_changed_fixed_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    lineage = pd.read_parquet(paths.source / "sentinel_lineage.parquet")
    lineage.loc[1, "eligible_pixel_count_static"] += 1
    _rewrite_source_parquet(paths, "sentinel_lineage.parquet", lineage)

    with pytest.raises(SentinelFeaturePromotionError, match="denominator changed"):
        _promote(paths)


def test_promotion_rejects_target_day_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    lineage = pd.read_parquet(paths.source / "sentinel_lineage.parquet")
    lineage.loc[0, "acquisition_local_date"] = lineage.loc[0, "target_date"]
    lineage.loc[0, "source_end_date"] = lineage.loc[0, "target_date"]
    lineage.loc[0, "lag_days"] = 0
    lineage.loc[0, "source_age_days_audit_only"] = 0
    _rewrite_source_parquet(paths, "sentinel_lineage.parquet", lineage)

    with pytest.raises(SentinelFeaturePromotionError, match="d-60:d-1"):
        _promote(paths)


def test_promotion_rejects_2025_even_when_source_hashes_are_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    features = pd.read_parquet(paths.source / PROMOTED_FEATURE_FILENAME)
    features.loc[0, "target_date"] = "2025-07-01"
    _rewrite_source_parquet(paths, PROMOTED_FEATURE_FILENAME, features)

    with pytest.raises(PermissionError, match="locked year 2025"):
        _promote(paths)


def test_promotion_rejects_recommitted_registry_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    registry_directory = tmp_path / "registry"
    registry_directory.mkdir()
    registry_path = registry_directory / REGISTRY.name
    registry = pd.read_csv(REGISTRY)
    registry.loc[registry["family"].eq("satellite"), "units"] = "tampered"
    registry.to_csv(registry_path, index=False)
    marker = json.loads(REGISTRY_PROVENANCE.read_text(encoding="utf-8"))
    marker.pop("commit_sha256")
    marker["ordered_registry_semantic_sha256"] = canonical_sha256(
        registry.to_dict("records")
    )
    marker["output_files"] = {
        registry_path.name: {
            "sha256": sha256_file(registry_path),
            "bytes": registry_path.stat().st_size,
            "rows": len(registry),
        }
    }
    marker_path = registry_directory / REGISTRY_PROVENANCE.name
    _write_committed(marker, marker_path)

    with pytest.raises(SentinelFeaturePromotionError, match="fragment changed"):
        promote_sentinel_features(
            source_directory=paths.source,
            output_directory=paths.output,
            feature_universe_path=paths.universe,
            feature_universe_provenance_path=paths.universe_provenance,
            registry_path=registry_path,
            registry_provenance_path=marker_path,
            inventory_directory=paths.inventory,
            research_config_path=RESEARCH_CONFIG,
            sentinel_config_path=SENTINEL_CONFIG,
        )


def test_promotion_rejects_invalid_feature_universe_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_fixture(tmp_path)
    monkeypatch.setattr(
        stage_module,
        "EXPECTED_SENTINEL_SCIENTIFIC_SHA256",
        paths.scientific_sha256,
    )
    marker = json.loads(paths.universe_provenance.read_text(encoding="utf-8"))
    marker["semantic_key_sha256"] = "0" * 64
    _write_json(marker, paths.universe_provenance)

    with pytest.raises(SentinelFeaturePromotionError, match="invalid commit"):
        _promote(paths)


def test_promotion_cli_help_has_no_output_side_effect(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/promote_sentinel_features.py"),
            "--help",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--source-directory" in result.stdout
    assert not (tmp_path / "data").exists()
