from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import la_heat.final_test_predictor_assembler as assembler
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

BASE_FEATURES = [
    *[f"static_feature_{index:02d}" for index in range(18)],
    "calendar_doy_sin",
    "calendar_doy_cos",
]
DAYMET_FEATURES = [f"daymet_test_{index:02d}" for index in range(21)]
SENTINEL_FEATURES = list(assembler.EXPECTED_SENTINEL_FEATURES)
MODEL_FEATURES = [*BASE_FEATURES, *DAYMET_FEATURES, *SENTINEL_FEATURES]
CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


def _keys() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["06037000001", "06037000002"] * 2,
            "target_date": pd.to_datetime(
                ["2025-05-01", "2025-05-01", "2025-05-02", "2025-05-02"]
            ),
        }
    )


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    keys = _keys()
    base = keys.assign(
        overpass_id=["o1", "o1", "o2", "o2"],
        platform=["landsat-8"] * 4,
        spatial_block=["a", "b", "a", "b"],
        latitude_quartile=[1, 1, 2, 2],
        longitude_quartile=[1, 2, 1, 2],
    )
    for index, name in enumerate(BASE_FEATURES):
        base[name] = np.arange(4, dtype=float) + index
    daymet = keys.copy()
    for index, name in enumerate(DAYMET_FEATURES):
        daymet[name] = np.arange(4, dtype=float) + 100 + index
    sentinel = keys.loc[:, list(assembler.SENTINEL_SOURCE_KEY_COLUMNS)].copy()
    for index, name in enumerate(SENTINEL_FEATURES):
        sentinel[name] = [0.1 + index, 0.2 + index, np.nan, np.nan]
    return base, daymet, sentinel


def _assemble(
    base: pd.DataFrame,
    daymet: pd.DataFrame,
    sentinel: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return assembler.assemble_final_predictor_frame(
        base,
        daymet,
        sentinel,
        base_feature_names=BASE_FEATURES,
        daymet_feature_names=DAYMET_FEATURES,
        sentinel_feature_names=SENTINEL_FEATURES,
        model_feature_names=MODEL_FEATURES,
        production_shape=False,
    )


def test_exact_join_outputs_two_keys_plus_frozen_46_and_reports_missingness() -> None:
    base, daymet, sentinel = _frames()
    predictors, missingness = _assemble(
        base.sample(frac=1, random_state=1).reset_index(drop=True),
        daymet.sample(frac=1, random_state=2).reset_index(drop=True),
        sentinel.sample(frac=1, random_state=3).reset_index(drop=True),
    )

    assert predictors.columns.tolist() == [
        "tract_geoid",
        "target_date",
        *MODEL_FEATURES,
    ]
    assert len(predictors) == 4
    assert "tract_geoid" not in MODEL_FEATURES
    assert predictors[BASE_FEATURES + DAYMET_FEATURES].notna().all(axis=None)
    assert predictors[SENTINEL_FEATURES].isna().all(axis=1).sum() == 2
    satellite = missingness.loc[missingness["family"].eq("satellite")]
    assert satellite["missing_allowed"].all()
    assert satellite["missing_count"].eq(2).all()


def test_join_rejects_missing_or_duplicate_dynamic_keys() -> None:
    base, daymet, sentinel = _frames()
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError,
        match="complete date-by-tract grid|key mismatch",
    ):
        _assemble(base, daymet.iloc[:-1].copy(), sentinel)

    duplicated = pd.concat([sentinel, sentinel.iloc[[0]]], ignore_index=True)
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="unique 11-digit"
    ):
        _assemble(base, daymet, duplicated)


def test_join_rejects_partial_sentinel_missingness_and_non_2025_keys() -> None:
    base, daymet, sentinel = _frames()
    sentinel.loc[0, SENTINEL_FEATURES[0]] = np.nan
    with pytest.raises(assembler.FinalTestPredictorAssemblyError, match="all five"):
        _assemble(base, daymet, sentinel)

    sentinel = _frames()[2]
    base.loc[0, "target_date"] = pd.Timestamp("2024-05-01")
    with pytest.raises(assembler.FinalTestPredictorAssemblyError, match="2025 civil-date"):
        _assemble(base, daymet, sentinel)


def test_model_contract_rejects_geoid_as_predictor() -> None:
    formal = _formal_payload()
    formal["models"]["M2"]["feature_names"][0] = "tract_geoid"
    with pytest.raises(assembler.FinalTestPredictorAssemblyError, match="forbidden"):
        assembler._model_feature_contract(formal)


def test_upstream_pipeline_requires_exact_algorithm_and_dependency_closure() -> None:
    root = assembler._project_root()
    incomplete = {
        "algorithm_version": assembler._BASE_ALGORITHM_VERSION,
        "files": {
            "src/la_heat/provenance.py": sha256_file(
                root / "src/la_heat/provenance.py"
            )
        },
    }
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="dependency closure"
    ):
        assembler._verify_upstream_pipeline(
            incomplete,
            canonical_sha256(incomplete),
            root=root,
            label="test",
            expected_algorithm_version=assembler._BASE_ALGORITHM_VERSION,
            expected_files=assembler._BASE_PIPELINE_FILES,
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _commit(payload: dict[str, Any]) -> dict[str, Any]:
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _formal_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "algorithm_version": "test-formal-lock",
        "state": "frozen_for_one_time_2025_evaluation",
        "formal_model_lock_written": True,
        "final_test_year": 2025,
        "final_test_locked": True,
        "final_test_unlocked": False,
        "final_test_used": False,
        "final_test_values_read": False,
        "contains_final_test_year": False,
        "one_time_final_evaluation_authorized": False,
        "models": {
            "B1": {
                "feature_names": [
                    "calendar_doy_sin",
                    "calendar_doy_cos",
                    *DAYMET_FEATURES,
                ],
                "feature_count": 23,
            },
            "M2": {
                "feature_names": list(MODEL_FEATURES),
                "feature_count": 46,
            },
        },
    }


def _blind_fields() -> dict[str, Any]:
    return {
        "state": "complete_target_blind",
        "final_test_year": 2025,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
    }


def _mini_pipeline(
    root: Path,
    *,
    algorithm_version: str,
    files: tuple[str, ...],
) -> tuple[str, dict[str, Any]]:
    fingerprint = {
        "algorithm_version": algorithm_version,
        "files": {name: sha256_file(root / name) for name in files},
    }
    return canonical_sha256(fingerprint), fingerprint


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _patch_small_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assembler, "EXPECTED_ROW_COUNT", 4)
    monkeypatch.setattr(assembler, "EXPECTED_DATE_COUNT", 2)
    monkeypatch.setattr(assembler, "EXPECTED_TRACT_COUNT", 2)


def _locked_config_copy(tmp_path: Path) -> Path:
    research_config_path = tmp_path / "configs" / "research.toml"
    research_config_path.parent.mkdir(parents=True)
    payload = CONFIG.read_bytes()
    unlocked_setting = b"unlock_final_test = true"
    assert payload.count(unlocked_setting) == 1
    research_config_path.write_bytes(
        payload.replace(unlocked_setting, b"unlock_final_test = false")
    )
    return research_config_path


def _synthetic_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    _patch_small_dimensions(monkeypatch)
    research_config_path = _locked_config_copy(tmp_path)
    monkeypatch.setattr(
        assembler,
        "FINAL_TEST_AUTHORIZATION_PATH",
        tmp_path / "manifests" / "final_test_2025" / "AUTHORIZATION.json",
    )
    root = assembler._project_root()
    base_pipeline_sha256, base_pipeline = _mini_pipeline(
        root,
        algorithm_version=assembler._BASE_ALGORITHM_VERSION,
        files=assembler._BASE_PIPELINE_FILES,
    )
    daymet_pipeline_sha256, daymet_pipeline = _mini_pipeline(
        root,
        algorithm_version=assembler._DAYMET_ALGORITHM_VERSION,
        files=assembler._DAYMET_PIPELINE_FILES,
    )
    sentinel_pipeline_sha256, sentinel_pipeline = _mini_pipeline(
        root,
        algorithm_version=assembler.SENTINEL_ALGORITHM_VERSION,
        files=assembler.SENTINEL_PIPELINE_FILES,
    )
    sentinel_audit_pipeline_sha256, sentinel_audit_pipeline = _mini_pipeline(
        root,
        algorithm_version=assembler.SENTINEL_AUDIT_ALGORITHM_VERSION,
        files=assembler.SENTINEL_AUDIT_PIPELINE_FILES,
    )
    base, daymet, sentinel = _frames()

    formal_path = tmp_path / "formal" / "MODEL_LOCK.json"
    formal = _commit(_formal_payload())
    _write_json(formal_path, formal)
    formal_sha256 = sha256_file(formal_path)

    landsat_path = tmp_path / "locks" / "LANDSAT_INVENTORY.json"
    key_path = tmp_path / "locks" / "target_blind_keys.bin"
    _write_json(landsat_path, {"target_blind": True})
    key_path.write_bytes(b"target-blind-key-lock")
    landsat_record = {
        **_file_record(landsat_path),
        "commit_sha256": "landsat-commit",
    }
    key_record = _file_record(key_path)

    base_path = tmp_path / "base" / "predictor_base.parquet"
    base_path.parent.mkdir(parents=True)
    base.to_parquet(base_path, index=False)
    base_provenance_path = tmp_path / "base_manifest" / "PREDICTOR_BASE.json"
    base_payload = {
        **_blind_fields(),
        "row_count": 4,
        "date_count": 2,
        "tract_count": 2,
        "feature_count": 20,
        "feature_names": BASE_FEATURES,
        "semantic_table_sha256": canonical_frame_sha256(
            base, sort_by=["target_date", "tract_geoid"]
        ),
        "formal_model_lock": {
            "path": str(formal_path.resolve()),
            "sha256": formal_sha256,
            "commit_sha256": formal["commit_sha256"],
        },
        "inputs": {
            "landsat_inventory": landsat_record,
            "key_universe": key_record,
        },
        "pipeline_sha256": base_pipeline_sha256,
        "pipeline_fingerprint": base_pipeline,
        "output_files": {
            base_path.name: {
                "path": str(base_path.resolve()),
                **parquet_file_record(base_path, base),
            }
        },
    }
    _write_json(base_provenance_path, _commit(base_payload))

    daymet_directory = tmp_path / "daymet"
    daymet_directory.mkdir()
    daymet_path = daymet_directory / "daymet_features.parquet"
    daymet_audit_path = daymet_directory / "daymet_feature_audit.parquet"
    daymet.to_parquet(daymet_path, index=False)
    daymet_audit = _keys().assign(daymet_all_primary_windows_complete=True)
    daymet_audit.to_parquet(daymet_audit_path, index=False)
    daymet_provenance_path = tmp_path / "daymet_manifest" / "DAYMET_FEATURES.json"
    daymet_internal_path = daymet_directory / "DAYMET_FEATURES.json"
    daymet_request = {
        "formal_model_lock_path": str(formal_path.resolve()),
        "feature_output_path": str(daymet_path.resolve()),
        "external_provenance_path": str(daymet_provenance_path.resolve()),
        "internal_provenance_path": str(daymet_internal_path.resolve()),
        "final_test_year": 2025,
        "window_days": [1, 3, 7],
    }
    daymet_payload = {
        **_blind_fields(),
        "publication_protocol": "staged_directory_atomic_replace_v1",
        "row_count": 4,
        "date_count": 2,
        "tract_count": 2,
        "feature_count": 21,
        "feature_names": DAYMET_FEATURES,
        "complete_feature_rows": 4,
        "incomplete_feature_rows": 0,
        "source_end_offset_days": -1,
        "target_day_observations_included": False,
        "missing_count_by_feature": dict.fromkeys(DAYMET_FEATURES, 0),
        "semantic_feature_table_sha256": canonical_frame_sha256(
            daymet, sort_by=["target_date", "tract_geoid"]
        ),
        "request": daymet_request,
        "request_sha256": canonical_sha256(daymet_request),
        "formal_model_lock": {
            "path": str(formal_path.resolve()),
            "sha256": formal_sha256,
            "commit_sha256": formal["commit_sha256"],
        },
        "landsat_inventory": {
            "path": str(landsat_path.resolve()),
            "sha256": landsat_record["sha256"],
            "commit_sha256": landsat_record["commit_sha256"],
        },
        "key_universe": {
            "path": str(key_path.resolve()),
            "sha256": key_record["sha256"],
            "bytes": key_record["bytes"],
        },
        "immutable_input_files": [_file_record(formal_path)],
        "pipeline_sha256": daymet_pipeline_sha256,
        "pipeline_fingerprint": daymet_pipeline,
        "output_files": {
            daymet_path.name: {
                "path": str(daymet_path.resolve()),
                **parquet_file_record(daymet_path, daymet),
            },
            daymet_audit_path.name: {
                "path": str(daymet_audit_path.resolve()),
                **parquet_file_record(daymet_audit_path, daymet_audit),
            },
        },
    }
    _commit(daymet_payload)
    _write_json(daymet_provenance_path, daymet_payload)
    _write_json(daymet_internal_path, daymet_payload)

    sentinel_directory = tmp_path / "sentinel"
    sentinel_directory.mkdir()
    sentinel_path = sentinel_directory / "sentinel_features.parquet"
    sentinel.to_parquet(sentinel_path, index=False)
    aggregate_frames = {
        "acquisition_tract.parquet": pd.DataFrame({"x": [1]}),
        "sentinel_features.parquet": sentinel,
        "sentinel_feature_audit.parquet": pd.DataFrame({"x": [1]}),
        "sentinel_lineage.parquet": pd.DataFrame({"x": [1]}),
    }
    aggregate_records: dict[str, Any] = {}
    for name, frame in aggregate_frames.items():
        path = sentinel_directory / name
        if path != sentinel_path:
            frame.to_parquet(path, index=False)
        aggregate_records[name] = parquet_file_record(path, frame)
    sentinel_pipeline_path = sentinel_directory / "pipeline_fingerprint.json"
    _write_json(sentinel_pipeline_path, sentinel_pipeline)
    sentinel_progress_path = sentinel_directory / "build_progress.json"
    stage_payload = {"frozen": "test-stage"}
    research_dependency_payload = {"frozen": "test-research"}
    chain_locks = {"test_complete_sentinel_chain_sha256": "a" * 64}
    monkeypatch.setattr(
        assembler,
        "_authenticate_sentinel_chain",
        lambda **_: assembler._SentinelChainAudit(
            locks=chain_locks,
            snapshots=[],
            stage_payload=stage_payload,
            research_dependency_payload=research_dependency_payload,
        ),
    )
    sentinel_progress = {
        "state": "complete",
        "promoted_outputs_valid": True,
        "build_complete": True,
        "target_blind_predictor_access": "2025_predictors_only_no_labels",
        "requester_pays_product_xml_opened": "false",
        "public_product_xml_opened": "false",
        "sentinel_source_collection": "sentinel-2-c1-l2a",
        "sentinel_raw_dn_encoding": "native_dn_scale_offset_once",
        "sentinel_prohibited_legacy_collection": "sentinel-2-l2a",
        "sentinel_provider_parity_evidence_sha256": "b" * 64,
        "expected_physical_acquisition_count": (
            assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT
        ),
        "completed_physical_acquisition_count": (
            assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT
        ),
        "feature_row_count": 4,
        "feature_available_row_count": 2,
        "target_date_count": 2,
        "tract_count": 2,
        "formal_model_lock_sha256": formal_sha256,
        "formal_model_lock_commit_sha256": formal["commit_sha256"],
        "landsat_inventory_sha256": landsat_record["sha256"],
        "landsat_inventory_commit_sha256": landsat_record["commit_sha256"],
        "final_sentinel_inventory_provenance_sha256": "c" * 64,
        "final_sentinel_inventory_commit_sha256": "d" * 64,
        "sentinel_inventory_semantic_sha256": "e" * 64,
        "raw_stac_snapshot_set_sha256": "f" * 64,
        "static_feature_audit_sha256": "1" * 64,
        "target_grid_identity_sha256": "2" * 64,
        "final_test_sentinel_feature_pipeline_sha256": sentinel_pipeline_sha256,
        "final_test_sentinel_feature_pipeline_fingerprint_sha256": sha256_file(
            sentinel_pipeline_path
        ),
        "sentinel_stage_config_payload": stage_payload,
        "sentinel_stage_config_sha256": canonical_sha256(stage_payload),
        "sentinel_research_dependency_payload": research_dependency_payload,
        **chain_locks,
        "aggregate_outputs": aggregate_records,
    }
    _write_json(sentinel_progress_path, sentinel_progress)
    sentinel_status_path = sentinel_directory / "status.json"
    _write_json(
        sentinel_status_path,
        {
            "state": "complete",
            "algorithm_version": assembler.SENTINEL_ALGORITHM_VERSION,
            "total": assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT,
            "completed": assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT,
            "running": 0,
            "failed": 0,
            "current": [],
            "failures": [],
            "compile_state": "complete",
            "promoted_outputs_valid": True,
        },
    )
    audit_inputs = sorted(
        [
            _file_record(sentinel_status_path),
            _file_record(sentinel_progress_path),
            _file_record(sentinel_pipeline_path),
            *[
                _file_record(sentinel_directory / name)
                for name in sorted(aggregate_frames)
            ],
        ],
        key=lambda record: str(record["path"]),
    )
    sentinel_audit_path = (
        tmp_path
        / "sentinel_manifest"
        / "SENTINEL_FEATURE_AUDIT.json"
    )
    sentinel_audit_payload = {
        "schema_version": assembler.SENTINEL_AUDIT_SCHEMA_VERSION,
        "algorithm_version": assembler.SENTINEL_AUDIT_ALGORITHM_VERSION,
        "state": "passed",
        "safe_for_final_predictor_assembly": True,
        "target_blind": True,
        "target_or_qa_values_read": False,
        "target_or_qa_paths_opened": [],
        "fitted_models_loaded": False,
        "predictions_scores_or_metrics_read": False,
        "authorization_file_present": False,
        "sentinel_algorithm_version": assembler.SENTINEL_ALGORITHM_VERSION,
        "source_collection": sentinel_progress["sentinel_source_collection"],
        "raw_dn_encoding": sentinel_progress["sentinel_raw_dn_encoding"],
        "prohibited_legacy_collection": sentinel_progress[
            "sentinel_prohibited_legacy_collection"
        ],
        "provider_parity_evidence_sha256": sentinel_progress[
            "sentinel_provider_parity_evidence_sha256"
        ],
        "completion_contract": {
            "status_complete": True,
            "progress_complete": True,
            "algorithm_version": assembler.SENTINEL_ALGORITHM_VERSION,
            "completed_physical_acquisition_count": (
                assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT
            ),
            "feature_available_row_count": 2,
        },
        "semantic_contract": {
            "feature_row_count": 4,
            "audit_row_count": 4,
            "target_date_count": 2,
            "tract_count": 2,
            "feature_available_row_count": 2,
            "all_or_none_feature_missingness": True,
            "minimum_source_age_days": 1,
            "maximum_source_age_days": 60,
            "target_day_or_future_source_count": 0,
            "semantic_feature_table_sha256": canonical_frame_sha256(
                sentinel, sort_by=["target_date", "tract_geoid"]
            ),
            "acquisition": {
                "physical_acquisition_count": (
                    assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT
                ),
                "tract_count": 2,
                "fixed_denominator_invariant": True,
                "two_tile_mosaic_invariant": True,
            },
        },
        "cache_contract": {
            "cache_count": assembler.SENTINEL_EXPECTED_ACQUISITION_COUNT,
            "all_current": True,
        },
        "calibration_classification": {
            "passed": True,
            "classification": "c1_calibration_consistent",
        },
        "authenticated_input_files": audit_inputs,
        "authenticated_input_file_set_sha256": canonical_sha256(audit_inputs),
        "upstream_locks": {
            "formal_model_lock_sha256": formal_sha256,
            "formal_model_lock_commit_sha256": formal["commit_sha256"],
            "sentinel_inventory_provenance_sha256": sentinel_progress[
                "final_sentinel_inventory_provenance_sha256"
            ],
            "sentinel_inventory_commit_sha256": sentinel_progress[
                "final_sentinel_inventory_commit_sha256"
            ],
            "sentinel_inventory_semantic_sha256": sentinel_progress[
                "sentinel_inventory_semantic_sha256"
            ],
            "raw_stac_snapshot_set_sha256": sentinel_progress[
                "raw_stac_snapshot_set_sha256"
            ],
            "static_feature_audit_sha256": sentinel_progress[
                "static_feature_audit_sha256"
            ],
            "target_grid_identity_sha256": sentinel_progress[
                "target_grid_identity_sha256"
            ],
        },
        "audit_pipeline_sha256": sentinel_audit_pipeline_sha256,
        "audit_pipeline_fingerprint": sentinel_audit_pipeline,
    }
    _write_json(sentinel_audit_path, _commit(sentinel_audit_payload))
    monkeypatch.setattr(
        assembler, "DEFAULT_SENTINEL_AUDIT_PATH", sentinel_audit_path
    )

    return {
        "formal": formal_path,
        "base": base_path,
        "base_provenance": base_provenance_path,
        "daymet": daymet_path,
        "daymet_provenance": daymet_provenance_path,
        "sentinel": sentinel_path,
        "sentinel_progress": sentinel_progress_path,
        "sentinel_pipeline": sentinel_pipeline_path,
        "sentinel_audit": sentinel_audit_path,
        "research_config": research_config_path,
        "output": tmp_path / "published" / "predictors",
        "marker": tmp_path / "manifest" / "PREDICTOR_ASSEMBLY.json",
    }


def _build(paths: dict[str, Path], **kwargs: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "formal_lock_path": paths["formal"],
        "predictor_base_path": paths["base"],
        "predictor_base_provenance_path": paths["base_provenance"],
        "daymet_feature_path": paths["daymet"],
        "daymet_provenance_path": paths["daymet_provenance"],
        "sentinel_feature_path": paths["sentinel"],
        "sentinel_progress_path": paths["sentinel_progress"],
        "sentinel_pipeline_path": paths["sentinel_pipeline"],
        "sentinel_audit_path": paths.get(
            "sentinel_audit", assembler.DEFAULT_SENTINEL_AUDIT_PATH
        ),
        "research_config_path": paths["research_config"],
        "output_directory": paths["output"],
        "provenance_path": paths["marker"],
    }
    arguments.update(kwargs)
    return assembler.build_final_test_predictor_artifacts(**arguments)


def test_full_build_is_target_blind_exact_and_reauthenticates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    payload = _build(paths)
    second = _build(paths)

    assert second == payload
    assert payload["state"] == "complete_target_blind"
    assert payload["target_or_qa_tables_read"] == []
    assert payload["target_values_read"] is False
    assert payload["feature_count"] == 46
    assert payload["row_count"] == 4
    assert payload["sentinel_missing_row_count"] == 2
    output = pd.read_parquet(paths["output"] / assembler.OUTPUT_FILENAME)
    assert output.columns.tolist() == [*assembler.KEY_COLUMNS, *MODEL_FEATURES]


def test_atomic_directory_publication_recovers_after_marker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)

    def fail_marker(_: dict[str, Any], __: Path) -> None:
        raise OSError("simulated external marker failure")

    with pytest.raises(OSError, match="simulated"):
        _build(paths, marker_writer=fail_marker)
    assert paths["output"].is_dir()
    assert not paths["marker"].exists()

    recovered = _build(paths)
    assert recovered["state"] == "complete_target_blind"
    assert paths["marker"].is_file()


def test_prepublication_snapshot_detects_changed_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    original = assembler._verify_snapshot
    calls = 0

    def mutate_before_second_check(records: list[dict[str, Any]]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            with paths["base"].open("ab") as handle:
                handle.write(b"changed")
        original(records)

    monkeypatch.setattr(assembler, "_verify_snapshot", mutate_before_second_check)
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="SHA-256 lock failed"
    ):
        _build(paths)
    assert not paths["output"].exists()
    assert not paths["marker"].exists()


def test_postpublication_authentication_rechecks_provenance_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    original = assembler._validate_published_outputs
    changed = False

    def mutate_external_marker(
        predictors: pd.DataFrame,
        missingness: pd.DataFrame,
        payload: dict[str, Any],
    ) -> None:
        nonlocal changed
        original(predictors, missingness, payload)
        if paths["marker"].is_file() and not changed:
            changed = True
            _write_json(paths["marker"], {"changed": True})

    monkeypatch.setattr(
        assembler, "_validate_published_outputs", mutate_external_marker
    )
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError,
        match="provenance changed during authentication",
    ):
        _build(paths)
    assert changed


def test_missing_daymet_commit_fails_before_any_predictor_parquet_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: dict[str, Path] = {
        "research_config": _locked_config_copy(tmp_path),
    }
    for name in ("formal", "base_provenance", "sentinel_progress", "sentinel_pipeline"):
        path = tmp_path / f"{name}.json"
        _write_json(path, {})
        paths[name] = path
    paths.update(
        {
            "base": tmp_path / "base.parquet",
            "daymet": tmp_path / "daymet.parquet",
            "daymet_provenance": tmp_path / "missing_daymet.json",
            "sentinel": tmp_path / "sentinel" / "sentinel_features.parquet",
            "output": tmp_path / "out",
            "marker": tmp_path / "marker.json",
        }
    )
    paths["sentinel"].parent.mkdir()
    paths["sentinel_pipeline"] = tmp_path / "sentinel_pipeline.json"

    def forbidden_read(*_: Any, **__: Any) -> pd.DataFrame:
        raise AssertionError("No parquet may be read before the Daymet completion gate.")

    monkeypatch.setattr(pd, "read_parquet", forbidden_read)
    with pytest.raises(FileNotFoundError, match="Daymet provenance"):
        _build(paths)


def test_missing_sentinel_postrun_audit_blocks_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    paths["sentinel_audit"].unlink()

    with pytest.raises(FileNotFoundError, match="Sentinel post-run safety audit"):
        _build(paths)
    assert not paths["output"].exists()
    assert not paths["marker"].exists()


def test_tampered_sentinel_postrun_audit_input_hash_blocks_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    payload = json.loads(paths["sentinel_audit"].read_text(encoding="utf-8"))
    payload.pop("commit_sha256")
    feature_record = next(
        record
        for record in payload["authenticated_input_files"]
        if Path(record["path"]).name == "sentinel_features.parquet"
    )
    feature_record["sha256"] = "0" * 64
    payload["authenticated_input_file_set_sha256"] = canonical_sha256(
        payload["authenticated_input_files"]
    )
    _write_json(paths["sentinel_audit"], _commit(payload))

    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError,
        match="SHA-256 lock failed",
    ):
        _build(paths)
    assert not paths["output"].exists()


def test_unsafe_sentinel_postrun_audit_blocks_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    payload = json.loads(paths["sentinel_audit"].read_text(encoding="utf-8"))
    payload.pop("commit_sha256")
    payload["safe_for_final_predictor_assembly"] = False
    _write_json(paths["sentinel_audit"], _commit(payload))

    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError,
        match="absent, unsafe, or not target-blind",
    ):
        _build(paths)
    assert not paths["output"].exists()


def test_noncanonical_sentinel_audit_path_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    alternate = tmp_path / "alternate" / "SENTINEL_FEATURE_AUDIT.json"
    alternate.parent.mkdir()
    shutil.copyfile(paths["sentinel_audit"], alternate)

    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError,
        match="exact canonical production path",
    ):
        _build(paths, sentinel_audit_path=alternate)


def test_existing_commit_rejects_changed_cli_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    _build(paths)
    changed = dict(paths)
    changed["base"] = tmp_path / "other_base.parquet"
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="another or unsafe request"
    ):
        _build(changed)


def test_sentinel_stage_request_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    progress = json.loads(paths["sentinel_progress"].read_text(encoding="utf-8"))
    progress["sentinel_stage_config_payload"] = {"changed": True}
    _write_json(paths["sentinel_progress"], progress)
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="request/spec/input locks"
    ):
        _build(paths)


def test_publication_paths_cannot_overwrite_outputs_or_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _synthetic_artifacts(tmp_path, monkeypatch)
    inside = dict(paths)
    inside["marker"] = paths["output"] / assembler.OUTPUT_FILENAME
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="outside the atomic"
    ):
        _build(inside)

    colliding = dict(paths)
    colliding["marker"] = paths["base"]
    before = sha256_file(paths["base"])
    with pytest.raises(
        assembler.FinalTestPredictorAssemblyError, match="collides with an immutable"
    ):
        _build(colliding)
    assert sha256_file(paths["base"]) == before
