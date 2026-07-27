from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box

import la_heat.final_evaluation_targets as target_module
from la_heat.aligned_landsat import REQUIRED_ASSETS, AlignedScene
from la_heat.config import ResearchConfig, load_config
from la_heat.final_evaluation_targets import (
    FinalEvaluationTargetError,
    audit_final_target_artifacts,
    authenticate_final_landsat_inventory,
    build_final_targets_transaction,
)
from la_heat.final_test_inventory import build_target_blind_key_universe
from la_heat.grid import FixedGrid
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import target_config_sha256
from la_heat.target_builder import _canonical_tract_manifest_hash


@dataclass(frozen=True)
class _InventoryFixture:
    root: Path
    inventory_path: Path
    authenticate_kwargs: dict[str, object]
    scene_path: Path


def _synthetic_inventory(tmp_path: Path) -> _InventoryFixture:
    root = tmp_path.resolve()
    inventory_directory = root / "manifests/final_test_2025/landsat_inventory"
    support_directory = root / "support"
    support_directory.mkdir(parents=True)

    city_path = support_directory / "city.gpkg"
    city = gpd.GeoDataFrame(
        {"name": ["synthetic-city"]},
        geometry=[box(0, 0, 4, 2)],
        crs="EPSG:32611",
    )
    city.to_file(city_path, driver="GPKG")
    frozen_city = gpd.read_file(city_path)

    tract_path = support_directory / "primary_tracts.parquet"
    tracts = gpd.GeoDataFrame(
        {
            "GEOID": ["06037000001", "06037000002"],
            "geometry_sha256": ["geometry-a", "geometry-b"],
            "city_area_fraction": [1.0, 1.0],
            "census_land_fraction": [1.0, 1.0],
            "special_use_tract": [False, False],
            "primary_included": [True, True],
            "primary_exclusion_reason": ["", ""],
            "spatial_block": ["block-a", "block-b"],
            "longitude_quartile": [0, 1],
            "latitude_quartile": [0, 1],
        },
        geometry=[box(0, 0, 2, 2), box(2, 0, 4, 2)],
        crs="EPSG:32611",
    )
    tract_commit = _canonical_tract_manifest_hash(tracts)
    tracts["tract_manifest_sha256"] = tract_commit
    atomic_parquet(tracts, tract_path)
    frozen_tracts = gpd.read_parquet(tract_path)
    assert _canonical_tract_manifest_hash(frozen_tracts) == tract_commit

    scene_path = inventory_directory / "scene_inventory.csv"
    scene_rows: list[dict[str, object]] = []
    for index, (scene_id, local_date) in enumerate(
        (("scene-a", "2025-06-01"), ("scene-b", "2025-06-17"))
    ):
        row: dict[str, object] = {
            "item_id": scene_id,
            "platform": "landsat-9",
            "acquired_utc": f"{local_date}T18:00:00+00:00",
            "local_date": local_date,
            "wrs_path": "041",
            "wrs_row": f"03{6 + index}",
            "cloud_cover_percent": 100.0,
            "city_coverage_fraction": 0.12345678901234568 + index * 0.1,
        }
        row.update(
            {
                f"{asset}_href": f"https://example.test/{scene_id}/{asset}.tif"
                for asset in REQUIRED_ASSETS
            }
        )
        scene_rows.append(row)
    atomic_csv(pd.DataFrame(scene_rows), scene_path)

    overpass_rows = [
        {
            "overpass_id": "overpass-a",
            "platform": "landsat-9",
            "local_date": "2025-06-01",
            "acquired_utc_min": "2025-06-01T18:00:00+00:00",
            "acquired_utc_max": "2025-06-01T18:00:00+00:00",
            "scene_ids": "scene-a",
            "wrs_path_rows": "041036",
            "scene_count": 1,
            "union_city_coverage_fraction": 1.0,
            "ambiguous_local_date": False,
            "source_lock_sha256": "a" * 64,
            "primary_eligible": True,
        },
        {
            "overpass_id": "overpass-b",
            "platform": "landsat-9",
            "local_date": "2025-06-17",
            "acquired_utc_min": "2025-06-17T18:00:00+00:00",
            "acquired_utc_max": "2025-06-17T18:00:00+00:00",
            "scene_ids": "scene-b",
            "wrs_path_rows": "041037",
            "scene_count": 1,
            "union_city_coverage_fraction": 1.0,
            "ambiguous_local_date": False,
            "source_lock_sha256": "b" * 64,
            "primary_eligible": True,
        },
    ]
    overpass_path = inventory_directory / "overpass_inventory.csv"
    primary_path = inventory_directory / "primary_overpass_manifest.csv"
    atomic_csv(pd.DataFrame(overpass_rows), overpass_path)
    atomic_csv(pd.DataFrame(overpass_rows), primary_path)

    scenes = pd.read_csv(
        scene_path,
        dtype={
            "item_id": str,
            "platform": str,
            "acquired_utc": str,
            "local_date": str,
            "wrs_path": str,
            "wrs_row": str,
            **{f"{asset}_href": str for asset in REQUIRED_ASSETS},
        },
        float_precision="round_trip",
    )
    overpasses = pd.read_csv(
        overpass_path,
        dtype={
            "overpass_id": str,
            "platform": str,
            "local_date": str,
            "acquired_utc_min": str,
            "acquired_utc_max": str,
            "scene_ids": str,
            "wrs_path_rows": str,
            "source_lock_sha256": str,
        },
        float_precision="round_trip",
    )
    primary = pd.read_csv(
        primary_path,
        dtype={
            "overpass_id": str,
            "platform": str,
            "local_date": str,
            "acquired_utc_min": str,
            "acquired_utc_max": str,
            "scene_ids": str,
            "wrs_path_rows": str,
            "source_lock_sha256": str,
        },
        float_precision="round_trip",
    )
    keys = build_target_blind_key_universe(frozen_tracts, primary)
    key_path = inventory_directory / "target_blind_key_universe.parquet"
    atomic_parquet(keys, key_path)
    locked_keys = pd.read_parquet(key_path)

    outputs = {
        "scene_inventory.csv": {
            "path": str(scene_path.resolve()),
            "sha256": sha256_file(scene_path),
            "bytes": scene_path.stat().st_size,
            "rows": len(scenes),
        },
        "overpass_inventory.csv": {
            "path": str(overpass_path.resolve()),
            "sha256": sha256_file(overpass_path),
            "bytes": overpass_path.stat().st_size,
            "rows": len(overpasses),
        },
        "primary_overpass_manifest.csv": {
            "path": str(primary_path.resolve()),
            "sha256": sha256_file(primary_path),
            "bytes": primary_path.stat().st_size,
            "rows": len(primary),
        },
        "target_blind_key_universe.parquet": {
            "path": str(key_path.resolve()),
            **parquet_file_record(key_path, locked_keys),
        },
    }
    key_semantic = canonical_frame_sha256(
        locked_keys, sort_by=["target_date", "tract_geoid"]
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "algorithm_version": "final-test-landsat-inventory-v1-target-blind",
        "state": "target_blind_inventory_frozen",
        "final_test_year": 2025,
        "target_blind": True,
        "target_assets_opened": False,
        "target_or_qa_values_read": False,
        "labels_created": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "global_scene_cloud_cover_filter": False,
        "scene_count": 2,
        "physical_overpass_count": 2,
        "primary_overpass_count": 2,
        "tract_count": 2,
        "key_count": 4,
        "frozen_support": {
            "city_boundary_path": str(city_path.resolve()),
            "city_boundary_sha256": sha256_file(city_path),
            "city_boundary_geometry_sha256": geometry_semantic_sha256(frozen_city),
            "primary_tract_path": str(tract_path.resolve()),
            "primary_tract_sha256": sha256_file(tract_path),
            "primary_tract_commit_sha256": tract_commit,
            "tract_count": 2,
        },
        "semantic_hashes": {
            "scenes": canonical_frame_sha256(scenes, sort_by=["item_id"]),
            "overpasses": canonical_frame_sha256(
                overpasses, sort_by=["local_date", "overpass_id"]
            ),
            "primary_overpasses": canonical_frame_sha256(
                primary, sort_by=["local_date", "overpass_id"]
            ),
            "key_universe": key_semantic,
        },
        "output_files": outputs,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    inventory_path = inventory_directory / "LANDSAT_INVENTORY.json"
    atomic_json(payload, inventory_path)
    kwargs: dict[str, object] = {
        "expected_inventory_file_sha256": sha256_file(inventory_path),
        "expected_inventory_commit_sha256": payload["commit_sha256"],
        "expected_key_semantic_sha256": key_semantic,
        "expected_scene_count": 2,
        "expected_overpass_count": 2,
        "expected_tract_count": 2,
        "expected_key_count": 4,
        "project_root": root,
    }
    return _InventoryFixture(root, inventory_path, kwargs, scene_path)


def _authenticate(fixture: _InventoryFixture):
    return authenticate_final_landsat_inventory(
        fixture.inventory_path,
        **fixture.authenticate_kwargs,
    )


def _unlocked_test_config(tmp_path: Path) -> ResearchConfig:
    source = Path("configs/research.toml").read_text(encoding="utf-8")
    replacements = {
        "unlock_final_test = false": "unlock_final_test = true",
        "minimum_valid_pixels_per_tract = 20": (
            "minimum_valid_pixels_per_tract = 1"
        ),
        "minimum_relative_joint_cell_tracts = 20": (
            "minimum_relative_joint_cell_tracts = 1"
        ),
    }
    for old, new in replacements.items():
        assert old in source
        source = source.replace(old, new)
    path = tmp_path / "research.toml"
    path.write_text(source, encoding="utf-8")
    return load_config(path)


def _fixed_grid() -> FixedGrid:
    return FixedGrid(
        crs="EPSG:32611",
        resolution_m=1.0,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=0.0,
        bottom=0.0,
        right=4.0,
        top=2.0,
        width=4,
        height=2,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
    )


def _reader(
    events: list[str],
):
    def read(
        *,
        scene_id: str,
        asset_hrefs: dict[str, str],
        grid: FixedGrid,
        config: ResearchConfig,
    ) -> AlignedScene:
        del asset_hrefs, config
        assert events and events[0] == "values-opened"
        events.append(f"read:{scene_id}")
        offset = 0.0 if scene_id == "scene-a" else 1.0
        return AlignedScene(
            scene_id=scene_id,
            lst_c=np.array(
                [
                    [30.0, 32.0, 40.0, 42.0],
                    [34.0, 36.0, 44.0, 46.0],
                ]
            )
            + offset,
            valid=np.ones(grid.shape, dtype=bool),
            st_uncertainty_k=np.full(grid.shape, 2.0),
            cloud_distance_km=np.full(grid.shape, 5.0),
            footprint=np.ones(grid.shape, dtype=bool),
        )

    return read


def _patch_target_support(monkeypatch: pytest.MonkeyPatch) -> None:
    zones = np.array([[1, 1, 2, 2], [1, 1, 2, 2]], dtype=np.int32)
    land = np.ones((2, 4), dtype=bool)
    monkeypatch.setattr(
        "la_heat.final_evaluation_targets._fixed_grid_and_zones",
        lambda config, city, tracts: (_fixed_grid(), zones, land, "synthetic-grid"),
    )
    monkeypatch.setattr(
        "la_heat.final_evaluation_targets.final_target_pipeline_fingerprint",
        lambda project_root=None: (
            "pipeline-sha256",
            {"algorithm_version": "synthetic-pipeline"},
        ),
    )


def test_authenticates_exact_target_blind_inventory_and_detects_drift(
    tmp_path: Path,
) -> None:
    fixture = _synthetic_inventory(tmp_path)
    authenticated = _authenticate(fixture)
    assert len(authenticated.scenes) == 2
    assert len(authenticated.primary_overpasses) == 2
    assert len(authenticated.key_universe) == 4
    assert authenticated.readiness_record["state"] == (
        "authenticated_target_blind_final_landsat_inventory"
    )
    assert authenticated.readiness_record["target_dates"] == [
        "2025-06-01",
        "2025-06-17",
    ]
    assert authenticated.readiness_record["tract_crs"] == "EPSG:32611"
    assert "href" not in str(authenticated.readiness_record).lower()

    fixture.scene_path.write_text(
        fixture.scene_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(FinalEvaluationTargetError, match="byte count|SHA-256"):
        _authenticate(fixture)


def test_value_marker_precedes_remote_and_cached_target_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    expected_config = target_config_sha256(config)
    events: list[str] = []

    def mark_values_opened() -> None:
        events.append("values-opened")

    first = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=expected_config,
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=mark_values_opened,
        scene_reader=_reader(events),
    )
    assert events == ["values-opened", "read:scene-a", "read:scene-b"]
    assert len(first.target_qa) == 4
    assert len(first.date_summary) == 2
    assert first.audit["minimum_development_date_gate_applied"] is False

    events.clear()
    original_read_parquet = pd.read_parquet
    original_sha256_file = target_module.sha256_file

    def guarded_read_parquet(*args: object, **kwargs: object) -> pd.DataFrame:
        path = Path(args[0])
        if "by_overpass" in path.parts:
            assert events == ["values-opened"]
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(
        "la_heat.final_evaluation_targets.pd.read_parquet",
        guarded_read_parquet,
    )

    def guarded_sha256_file(path: Path) -> str:
        if "by_overpass" in Path(path).parts:
            assert events == ["values-opened"]
        return original_sha256_file(path)

    monkeypatch.setattr(
        "la_heat.final_evaluation_targets.sha256_file",
        guarded_sha256_file,
    )

    def must_not_read_remote(**kwargs: object) -> AlignedScene:
        del kwargs
        raise AssertionError("A same-claim resume must use authenticated caches.")

    resumed = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=expected_config,
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=mark_values_opened,
        scene_reader=must_not_read_remote,
    )
    assert events == ["values-opened"]
    pd.testing.assert_frame_equal(first.target_qa, resumed.target_qa)

    events.clear()

    def reject_cached_values() -> None:
        raise PermissionError("cached marker verification failed")

    with pytest.raises(PermissionError, match="cached marker verification failed"):
        build_final_targets_transaction(
            inventory=inventory,
            config=config,
            expected_target_config_sha256=expected_config,
            claim_id="claim-one",
            staging_directory="staging",
            values_opened_callback=reject_cached_values,
            scene_reader=must_not_read_remote,
        )
    assert events == []


def test_compiled_audit_rejects_support_and_contribution_lineage_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    artifacts = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=target_config_sha256(config),
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=lambda: None,
        scene_reader=_reader(["values-opened"]),
    )

    wrong_identity = artifacts.target_qa.copy()
    geoid = str(wrong_identity.loc[0, "tract_geoid"])
    wrong_identity.loc[
        wrong_identity["tract_geoid"].astype(str).eq(geoid),
        "eligible_pixel_identity_sha256",
    ] = "f" * 64
    with pytest.raises(
        FinalEvaluationTargetError,
        match="eligible support identity",
    ):
        audit_final_target_artifacts(
            wrong_identity,
            artifacts.date_summary,
            artifacts.scene_contributions,
            inventory=inventory,
            config=config,
            expected_target_config_sha256=target_config_sha256(config),
        )

    wrong_summary = artifacts.date_summary.copy()
    wrong_summary["zone_raster_sha256"] = "e" * 64
    with pytest.raises(
        FinalEvaluationTargetError,
        match="eligible support identity",
    ):
        audit_final_target_artifacts(
            artifacts.target_qa,
            wrong_summary,
            artifacts.scene_contributions,
            inventory=inventory,
            config=config,
            expected_target_config_sha256=target_config_sha256(config),
        )

    wrong_contributions = artifacts.scene_contributions.copy()
    wrong_contributions.loc[0, "target_date"] = wrong_contributions[
        "target_date"
    ].max()
    with pytest.raises(
        FinalEvaluationTargetError,
        match="scene/date lineage",
    ):
        audit_final_target_artifacts(
            artifacts.target_qa,
            artifacts.date_summary,
            wrong_contributions,
            inventory=inventory,
            config=config,
            expected_target_config_sha256=target_config_sha256(config),
        )


def test_callback_failure_prevents_every_target_value_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    reader_called = False

    def reject_values_opened() -> None:
        raise PermissionError("marker write failed")

    def forbidden_reader(**kwargs: object) -> AlignedScene:
        del kwargs
        nonlocal reader_called
        reader_called = True
        raise AssertionError("Target reader crossed a failed marker boundary.")

    with pytest.raises(PermissionError, match="marker write failed"):
        build_final_targets_transaction(
            inventory=inventory,
            config=config,
            expected_target_config_sha256=target_config_sha256(config),
            claim_id="claim-one",
            staging_directory="failed-staging",
            values_opened_callback=reject_values_opened,
            scene_reader=forbidden_reader,
        )
    assert reader_called is False


def test_compiled_audit_rejects_changing_static_land_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    artifacts = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=target_config_sha256(config),
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=lambda: None,
        scene_reader=_reader(["values-opened"]),
    )
    changed = artifacts.target_qa.copy()
    geoid = changed.loc[0, "tract_geoid"]
    later = changed.index[
        changed["tract_geoid"].eq(geoid)
        & changed["target_date"].eq(changed["target_date"].max())
    ][0]
    changed.loc[later, "eligible_pixel_count_static"] += 1
    with pytest.raises(
        FinalEvaluationTargetError,
        match="denominator|pixel-count",
    ):
        audit_final_target_artifacts(
            changed,
            artifacts.date_summary,
            artifacts.scene_contributions,
            inventory=inventory,
            config=config,
            expected_target_config_sha256=target_config_sha256(config),
        )


def test_compiled_audit_rejects_primitive_qa_type_and_arithmetic_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    artifacts = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=target_config_sha256(config),
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=lambda: None,
        scene_reader=_reader(["values-opened"]),
    )

    def rejected(
        targets: pd.DataFrame,
        summaries: pd.DataFrame | None = None,
    ) -> None:
        with pytest.raises(FinalEvaluationTargetError):
            audit_final_target_artifacts(
                targets,
                artifacts.date_summary if summaries is None else summaries,
                artifacts.scene_contributions,
                inventory=inventory,
                config=config,
                expected_target_config_sha256=target_config_sha256(config),
            )

    integer_booleans = artifacts.target_qa.copy()
    integer_summaries = artifacts.date_summary.copy()
    integer_booleans["date_usable"] = integer_booleans["date_usable"].astype(int)
    integer_summaries["date_usable"] = integer_summaries["date_usable"].astype(int)
    rejected(integer_booleans, integer_summaries)

    integer_available = artifacts.target_qa.copy()
    integer_available["target_available"] = integer_available[
        "target_available"
    ].astype(int)
    rejected(integer_available)

    integer_labels = artifacts.target_qa.copy()
    integer_labels["relative_hotspot_top20"] = integer_labels[
        "relative_hotspot_top20"
    ].astype(int)
    rejected(integer_labels)

    fractional_count = artifacts.target_qa.copy()
    fractional_count["eligible_pixel_count_static"] = fractional_count[
        "eligible_pixel_count_static"
    ].astype(float)
    fractional_count.loc[0, "eligible_pixel_count_static"] += 0.5
    rejected(fractional_count)

    inconsistent_count = artifacts.target_qa.copy()
    inconsistent_count.loc[0, "rasterized_pixel_count"] += 1
    rejected(inconsistent_count)

    inconsistent_fraction = artifacts.target_qa.copy()
    inconsistent_fraction.loc[0, "valid_fraction"] -= 0.1
    rejected(inconsistent_fraction)


def test_compiled_audit_rejects_derived_gate_label_and_reason_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _synthetic_inventory(tmp_path / "inventory")
    inventory = _authenticate(fixture)
    config = _unlocked_test_config(fixture.root)
    _patch_target_support(monkeypatch)
    artifacts = build_final_targets_transaction(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=target_config_sha256(config),
        claim_id="claim-one",
        staging_directory="staging",
        values_opened_callback=lambda: None,
        scene_reader=_reader(["values-opened"]),
    )

    def rejected(
        targets: pd.DataFrame,
        summaries: pd.DataFrame | None = None,
    ) -> None:
        with pytest.raises(FinalEvaluationTargetError):
            audit_final_target_artifacts(
                targets,
                artifacts.date_summary if summaries is None else summaries,
                artifacts.scene_contributions,
                inventory=inventory,
                config=config,
                expected_target_config_sha256=target_config_sha256(config),
            )

    date = artifacts.target_qa["target_date"].min()
    date_mask = artifacts.target_qa["target_date"].eq(date)
    summary_index = artifacts.date_summary.index[
        artifacts.date_summary["target_date"].eq(date)
    ][0]

    changed_date_gate = artifacts.target_qa.copy()
    changed_date_summary = artifacts.date_summary.copy()
    changed_date_gate.loc[date_mask, "date_usable"] = False
    changed_date_gate.loc[
        date_mask, "date_exclusion_reason"
    ] = "insufficient_date_tract_retention"
    changed_date_summary.loc[summary_index, "date_usable"] = False
    changed_date_summary.loc[
        summary_index, "date_exclusion_reason"
    ] = "insufficient_date_tract_retention"
    rejected(changed_date_gate, changed_date_summary)

    changed_relative_gate = artifacts.date_summary.copy()
    changed_relative_gate.loc[
        summary_index, "relative_endpoint_coverage_pass"
    ] = False
    rejected(artifacts.target_qa, changed_relative_gate)

    changed_anomaly = artifacts.target_qa.copy()
    available_index = changed_anomaly.index[
        date_mask & changed_anomaly["target_available"]
    ][0]
    changed_anomaly.loc[available_index, "lst_anomaly_c"] += 1.0
    rejected(changed_anomaly)

    changed_labels = artifacts.target_qa.copy()
    date_rows = changed_labels.loc[date_mask]
    hot_index = date_rows.index[
        date_rows["relative_hotspot_top20"].fillna(False)
    ][0]
    cold_index = date_rows.index[
        ~date_rows["relative_hotspot_top20"].fillna(False)
    ][0]
    changed_labels.loc[hot_index, "relative_hotspot_top20"] = False
    changed_labels.loc[cold_index, "relative_hotspot_top20"] = True
    rejected(changed_labels)

    changed_reason = artifacts.target_qa.copy()
    changed_reason.loc[
        available_index, "tract_exclusion_reason"
    ] = "insufficient_valid_fraction"
    rejected(changed_reason)
