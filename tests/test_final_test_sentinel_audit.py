from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import la_heat.final_test_sentinel_audit as audit_module
from la_heat.final_test_sentinel_audit import (
    AGGREGATE_FILENAMES,
    ALBEDO_FEATURE,
    CALIBRATION_ENCODING,
    EXPECTED_ACQUISITION_COUNT,
    EXPECTED_FEATURE_ROW_COUNT,
    EXPECTED_LINEAGE_ROW_COUNT,
    EXPECTED_TARGET_DATE_COUNT,
    EXPECTED_TRACT_COUNT,
    SENTINEL_ALGORITHM_VERSION,
    FinalTestSentinelAuditError,
    _authorization_absent,
    _publish_report,
    _snapshot_record,
    _verify_exact_raw_stac_set,
    _verify_snapshots,
    audit_final_test_sentinel_features,
    classify_calibration,
    validate_acquisition_table,
    validate_completion_contract,
    validate_semantic_outputs,
)
from la_heat.final_test_sentinel_inventory import (
    PROHIBITED_LEGACY_COLLECTION,
    PROVIDER_PARITY_EVIDENCE_SHA256,
    STAC_COLLECTION,
)
from la_heat.final_test_state_lock import (
    DEFAULT_FINAL_TEST_STATE_LOCK_PATH,
    FinalTestStateLock,
    FinalTestStateLockBusyError,
)
from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.sentinel_features import INDEX_COLUMNS


def _completion_payloads() -> tuple[dict, dict, dict, str]:
    pipeline = {"algorithm_version": SENTINEL_ALGORITHM_VERSION}
    pipeline_sha = canonical_sha256(pipeline)
    status = {
        "state": "complete",
        "algorithm_version": SENTINEL_ALGORITHM_VERSION,
        "total": EXPECTED_ACQUISITION_COUNT,
        "completed": EXPECTED_ACQUISITION_COUNT,
        "running": 0,
        "failed": 0,
        "current": [],
        "failures": [],
        "compile_state": "complete",
        "promoted_outputs_valid": True,
    }
    progress = {
        "state": "complete",
        "promoted_outputs_valid": True,
        "build_complete": True,
        "expected_physical_acquisition_count": EXPECTED_ACQUISITION_COUNT,
        "completed_physical_acquisition_count": EXPECTED_ACQUISITION_COUNT,
        "feature_row_count": EXPECTED_FEATURE_ROW_COUNT,
        "feature_available_row_count": EXPECTED_FEATURE_ROW_COUNT - 10,
        "target_date_count": EXPECTED_TARGET_DATE_COUNT,
        "tract_count": EXPECTED_TRACT_COUNT,
        "lineage_row_count": EXPECTED_LINEAGE_ROW_COUNT,
        "target_blind_predictor_access": "2025_predictors_only_no_labels",
        "requester_pays_product_xml_opened": "false",
        "public_product_xml_opened": "false",
        "sentinel_source_collection": STAC_COLLECTION,
        "sentinel_raw_dn_encoding": CALIBRATION_ENCODING,
        "sentinel_prohibited_legacy_collection": PROHIBITED_LEGACY_COLLECTION,
        "sentinel_provider_parity_evidence_sha256": (
            PROVIDER_PARITY_EVIDENCE_SHA256
        ),
        "final_test_sentinel_feature_pipeline_sha256": pipeline_sha,
        "final_test_sentinel_feature_pipeline_fingerprint_sha256": "f" * 64,
        "aggregate_outputs": {name: {} for name in AGGREGATE_FILENAMES},
    }
    return status, progress, pipeline, pipeline_sha


def test_completion_contract_requires_clean_36_of_36() -> None:
    status, progress, pipeline, pipeline_sha = _completion_payloads()
    result = validate_completion_contract(
        status,
        progress,
        pipeline,
        pipeline_file_sha256="f" * 64,
        expected_pipeline_sha256=pipeline_sha,
    )
    assert result["status_complete"] is True
    assert result["completed_physical_acquisition_count"] == 36

    status["completed"] = 35
    with pytest.raises(FinalTestSentinelAuditError, match="36/36"):
        validate_completion_contract(
            status,
            progress,
            pipeline,
            pipeline_file_sha256="f" * 64,
            expected_pipeline_sha256=pipeline_sha,
        )


def _feature_control(
    *,
    ndvi: list[float],
    ndwi: list[float],
    albedo: list[float],
) -> pd.DataFrame:
    rows = len(ndvi)
    return pd.DataFrame(
        {
            "target_date": [
                "2025-05-06" if index < rows // 2 else "2025-05-14"
                for index in range(rows)
            ],
            "tract_geoid": [f"0603710{index:04d}" for index in range(rows)],
            "sentinel_ndvi_lag60": ndvi,
            "sentinel_evi_lag60": [0.14] * rows,
            "sentinel_ndwi_lag60": ndwi,
            "sentinel_ndbi_lag60": [-0.02] * rows,
            "sentinel_albedo_proxy_lag60": albedo,
        }
    )


def _calibration_acquisitions(*, corrected: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    legacy_rows: list[dict[str, object]] = []
    final_rows: list[dict[str, object]] = []
    for date_index, date in enumerate(("2025-04-01", "2025-04-11")):
        for tract_index, geoid in enumerate(("06037101110", "06037101122")):
            legacy_value = 0.075 + 0.005 * (date_index + tract_index)
            legacy_rows.append(
                {
                    "acquisition_local_date": date,
                    "tract_geoid": geoid,
                    ALBEDO_FEATURE: legacy_value,
                }
            )
            final_rows.append(
                {
                    "acquisition_local_date": date,
                    "tract_geoid": geoid,
                    ALBEDO_FEATURE: legacy_value + (0.1 if corrected else 0.0),
                }
            )
    for geoid in ("06037101110", "06037101122"):
        final_rows.append(
            {
                "acquisition_local_date": "2025-04-21",
                "tract_geoid": geoid,
                ALBEDO_FEATURE: 0.18,
            }
        )
    return pd.DataFrame(final_rows), pd.DataFrame(legacy_rows)


def test_calibration_classifier_transparently_selects_valid_c1() -> None:
    development = _feature_control(
        ndvi=[0.18, 0.19, 0.20, 0.21],
        ndwi=[-0.26, -0.25, -0.24, -0.23],
        albedo=[0.17, 0.18, 0.18, 0.19],
    )
    legacy = _feature_control(
        ndvi=[0.40, 0.45, 1.20, 1.30],
        ndwi=[-0.50, -0.55, -1.20, -1.30],
        albedo=[0.07, 0.08, 0.08, 0.09],
    )
    final = _feature_control(
        ndvi=[0.18, 0.20, 0.21, 0.22],
        ndwi=[-0.27, -0.25, -0.24, -0.22],
        albedo=[0.17, 0.18, 0.18, 0.19],
    )
    final_acquisition, legacy_acquisition = _calibration_acquisitions(
        corrected=True
    )
    result = classify_calibration(
        final_features=final,
        development_features=development,
        legacy_features=legacy,
        final_acquisition=final_acquisition,
        legacy_acquisition=legacy_acquisition,
        expected_final_acquisition_count=3,
        expected_legacy_acquisition_count=2,
        expected_tract_count=2,
    )
    assert result["passed"] is True
    assert result["classification"] == "c1_calibration_consistent"
    assert all(
        result["diagnostic_medians"][feature]["closer_to_development_valid"]
        for feature in (
            "sentinel_ndvi_lag60",
            "sentinel_ndwi_lag60",
            ALBEDO_FEATURE,
        )
    )
    shift = result["paired_albedo_shift_diagnostic"]
    assert shift["supports_corrected_c1"] is True
    assert shift["observed_median_shift"] == pytest.approx(0.1)


def test_calibration_classifier_fails_closed_on_replayed_legacy_offset() -> None:
    development = _feature_control(
        ndvi=[0.18, 0.19, 0.20, 0.21],
        ndwi=[-0.26, -0.25, -0.24, -0.23],
        albedo=[0.17, 0.18, 0.18, 0.19],
    )
    legacy = _feature_control(
        ndvi=[0.40, 0.45, 1.20, 1.30],
        ndwi=[-0.50, -0.55, -1.20, -1.30],
        albedo=[0.07, 0.08, 0.08, 0.09],
    )
    final_acquisition, legacy_acquisition = _calibration_acquisitions(
        corrected=False
    )
    result = classify_calibration(
        final_features=legacy.copy(),
        development_features=development,
        legacy_features=legacy,
        final_acquisition=final_acquisition,
        legacy_acquisition=legacy_acquisition,
        expected_final_acquisition_count=3,
        expected_legacy_acquisition_count=2,
        expected_tract_count=2,
    )
    assert result["passed"] is False
    assert result["classification"] == "legacy_double_offset_or_ambiguous"
    assert (
        result["paired_albedo_shift_diagnostic"]["supports_corrected_c1"]
        is False
    )


def _inventory(acquisition_ids: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    acquisitions = pd.DataFrame(
        {
            "physical_acquisition_id": acquisition_ids,
            "acquisition_local_date": [
                f"2025-01-{9 - index:02d}" for index in range(len(acquisition_ids))
            ],
            "platform": ["sentinel-2a"] * len(acquisition_ids),
            "processing_baseline": ["05.11"] * len(acquisition_ids),
        }
    )
    items: list[dict[str, str]] = []
    for acquisition_id in acquisition_ids:
        for tile in ("11SLT", "11SLU"):
            items.append(
                {
                    "physical_acquisition_id": acquisition_id,
                    "item_id": f"{acquisition_id}-{tile}",
                    "mgrs_tile": tile,
                }
            )
    return acquisitions, pd.DataFrame(items)


def _acquisition_table(acquisition_ids: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory_acquisitions, inventory_items = _inventory(acquisition_ids)
    rows: list[dict[str, object]] = []
    for acquisition in inventory_acquisitions.itertuples(index=False):
        source = inventory_items.loc[
            inventory_items["physical_acquisition_id"]
            == acquisition.physical_acquisition_id
        ].sort_values(["mgrs_tile", "item_id"])
        for geoid, eligible_count, identity in (
            ("06037101110", 100, "a" * 64),
            ("06037101122", 200, "b" * 64),
        ):
            row: dict[str, object] = {
                "tract_geoid": geoid,
                "physical_acquisition_id": acquisition.physical_acquisition_id,
                "acquisition_local_date": acquisition.acquisition_local_date,
                "platform": acquisition.platform,
                "processing_baseline": acquisition.processing_baseline,
                "eligible_pixel_count_static": eligible_count,
                "valid_area_equivalent_pixels": eligible_count * 0.9,
                "acquisition_coverage_fraction": 0.9,
                "acquisition_qualifies_coverage": True,
                "source_item_ids_audit_only": "|".join(source["item_id"]),
                "source_mgrs_tiles_audit_only": "|".join(source["mgrs_tile"]),
                "calibration_sha256_audit_only": "c" * 64,
                "optical_grid_sha256_audit_only": "d" * 64,
                "static_land_mask_sha256_audit_only": "e" * 64,
                "eligible_pixel_identity_sha256_audit_only": identity,
            }
            row.update(
                {
                    feature: value
                    for feature, value in zip(
                        INDEX_COLUMNS,
                        (0.2, 0.14, -0.24, -0.02, 0.18),
                        strict=True,
                    )
                }
            )
            rows.append(row)
    return pd.DataFrame(rows), inventory_items


def _static_audit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["06037101110", "06037101122"],
            "eligible_pixel_count_static": [100, 200],
            "eligible_pixel_identity_sha256": ["a" * 64, "b" * 64],
        }
    )


def test_fixed_denominator_change_fails_closed() -> None:
    acquisition_ids = ("a", "b")
    acquisitions, items = _acquisition_table(acquisition_ids)
    inventory_acquisitions, _ = _inventory(acquisition_ids)
    metrics = validate_acquisition_table(
        acquisitions,
        inventory_acquisitions=inventory_acquisitions,
        inventory_items=items,
        tract_geoids=("06037101110", "06037101122"),
        static_audit=_static_audit(),
        minimum_coverage=0.8,
    )
    assert metrics["fixed_denominator_invariant"] is True

    changed = acquisitions.copy()
    mask = (changed["physical_acquisition_id"] == "b") & (
        changed["tract_geoid"] == "06037101122"
    )
    changed.loc[mask, "eligible_pixel_count_static"] = 201
    changed.loc[mask, "valid_area_equivalent_pixels"] = 201 * 0.9
    with pytest.raises(FinalTestSentinelAuditError, match="denominator"):
        validate_acquisition_table(
            changed,
            inventory_acquisitions=inventory_acquisitions,
            inventory_items=items,
            tract_geoids=("06037101110", "06037101122"),
            static_audit=_static_audit(),
            minimum_coverage=0.8,
        )


def _semantic_fixture() -> dict[str, object]:
    acquisition, items = _acquisition_table(("a",))
    inventory_acquisitions, _ = _inventory(("a",))
    target_date = "2025-01-10"
    feature_rows = acquisition[["tract_geoid", *INDEX_COLUMNS]].copy()
    feature_rows.insert(0, "target_date", target_date)
    audit = pd.DataFrame(
        {
            "target_date": [target_date, target_date],
            "tract_geoid": ["06037101110", "06037101122"],
            "window_membership_count": [1, 1],
            "qualifying_acquisition_count": [1, 1],
            "minimum_lag_days": [1, 1],
            "maximum_lag_days": [1, 1],
            "median_acquisition_coverage": [0.9, 0.9],
            "newest_source_end_date": ["2025-01-09", "2025-01-09"],
            "oldest_source_end_date": ["2025-01-09", "2025-01-09"],
            "sentinel_feature_available": [True, True],
        }
    )
    membership = pd.DataFrame(
        {
            "target_date": [target_date],
            "physical_acquisition_id": ["a"],
            "acquisition_local_date": ["2025-01-09"],
            "lag_days": [1],
        }
    )
    lineage = membership.merge(
        acquisition,
        on=["physical_acquisition_id", "acquisition_local_date"],
        how="left",
    )
    lineage["included_in_composite"] = True
    lineage["source_end_date"] = lineage["acquisition_local_date"]
    lineage["source_age_days_audit_only"] = 1
    base_keys = pd.DataFrame(
        {
            "tract_geoid": ["06037101110", "06037101122"],
            "target_date": [target_date, target_date],
        }
    )
    return {
        "features": feature_rows,
        "audit": audit,
        "lineage": lineage,
        "acquisition": acquisition,
        "membership": membership,
        "predictor_base_keys": base_keys,
        "static_audit": _static_audit(),
        "inventory_acquisitions": inventory_acquisitions,
        "inventory_items": items,
        "research": SimpleNamespace(
            final_test_year=2025,
            final_test_unlocked=True,
        ),
        "minimum_coverage": 0.8,
        "minimum_acquisitions": 1,
        "expected_tract_count": 2,
        "expected_target_date_count": 1,
        "expected_acquisition_count": 1,
        "expected_membership_count": 1,
    }


def test_semantic_audit_reconstructs_features_and_rejects_target_day() -> None:
    inputs = _semantic_fixture()
    result = validate_semantic_outputs(**inputs)
    assert result["feature_row_count"] == 2
    assert result["minimum_source_age_days"] == 1
    assert result["target_day_or_future_source_count"] == 0

    leaked = deepcopy(inputs)
    leaked["membership"].loc[0, "acquisition_local_date"] = "2025-01-10"
    leaked["membership"].loc[0, "lag_days"] = 0
    leaked["lineage"]["acquisition_local_date"] = "2025-01-10"
    leaked["lineage"]["source_end_date"] = "2025-01-10"
    leaked["lineage"]["lag_days"] = 0
    leaked["lineage"]["source_age_days_audit_only"] = 0
    with pytest.raises(Exception, match="d-60"):
        validate_semantic_outputs(**leaked)


def test_audit_publication_is_isolated_atomic_and_idempotent(tmp_path: Path) -> None:
    output = (
        tmp_path
        / "manifests/final_test_2025/sentinel_features/SENTINEL_FEATURE_AUDIT.json"
    )
    report = {"schema_version": 1, "state": "passed"}
    first = _publish_report(report, root=tmp_path, output_path=output)
    second = _publish_report(report, root=tmp_path, output_path=output)
    assert first == second
    assert first["commit_sha256"]
    assert list(output.parent.iterdir()) == [output]

    with pytest.raises(FinalTestSentinelAuditError, match="isolated canonical"):
        _publish_report(
            report,
            root=tmp_path,
            output_path=tmp_path / "elsewhere.json",
        )


def test_upstream_snapshot_mutation_fails_final_recheck(tmp_path: Path) -> None:
    upstream = tmp_path / "manifests/model_lock/MODEL_LOCK.json"
    upstream.parent.mkdir(parents=True)
    upstream.write_bytes(b"frozen")
    snapshot = _snapshot_record(tmp_path, upstream)

    upstream.write_bytes(b"changed")

    with pytest.raises(FinalTestSentinelAuditError, match="changed during"):
        _verify_snapshots(tmp_path, [snapshot])


def test_extra_raw_stac_json_fails_exact_set_recheck(tmp_path: Path) -> None:
    raw = tmp_path / "stac_items"
    raw.mkdir()
    records = []
    for name, payload in (("one.json", b"one"), ("two.json", b"two")):
        path = raw / name
        path.write_bytes(payload)
        records.append(
            {
                "filename": name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    _verify_exact_raw_stac_set(raw, records, expected_count=2)

    (raw / "undeclared.json").write_bytes(b"extra")

    with pytest.raises(FinalTestSentinelAuditError, match="missing or extra"):
        _verify_exact_raw_stac_set(raw, records, expected_count=2)


def test_authorization_appearance_fails_final_recheck(tmp_path: Path) -> None:
    authorization = (
        tmp_path / "manifests/final_test_2025/AUTHORIZATION.json"
    )
    _authorization_absent(authorization)
    authorization.parent.mkdir(parents=True)
    authorization.write_bytes(b"racing authorization")

    with pytest.raises(FinalTestSentinelAuditError, match="authorization exists"):
        _authorization_absent(authorization)


def test_audit_entrypoint_holds_shared_lock_for_complete_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / DEFAULT_FINAL_TEST_STATE_LOCK_PATH

    def locked_operation(**_kwargs: object) -> dict[str, str]:
        with pytest.raises(FinalTestStateLockBusyError):
            with FinalTestStateLock(lock_path):
                pass
        return {"state": "passed"}

    monkeypatch.setattr(audit_module, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        audit_module,
        "_audit_final_test_sentinel_features_locked",
        locked_operation,
    )

    assert audit_final_test_sentinel_features() == {"state": "passed"}
    with FinalTestStateLock(lock_path):
        pass
