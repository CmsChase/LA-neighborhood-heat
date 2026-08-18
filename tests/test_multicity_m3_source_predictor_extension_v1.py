from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import requests

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity import m3_source_predictor_extension_authorization_v1 as auth
from la_heat.multicity import m3_source_predictor_extension_runtime_v1 as runtime
from la_heat.multicity import m3_source_predictor_extension_worker_v1 as worker
from la_heat.provenance import sha256_file


def _permit() -> dict[str, Any]:
    extension = [
        {
            "city_id": city_id,
            "target_dates": ["2020-05-01"],
        }
        for city_id in auth.EXTENSION_CITY_IDS
    ]
    source = [
        {
            "city_id": city_id,
            "target_dates": ["2020-05-01"],
            "target_date_count": 1,
            "tract_count": 1,
            "row_count": 1,
        }
        for city_id in auth.SOURCE_CITY_IDS
    ]
    return {
        "commit_sha256": "a" * 64,
        "key_universe": {
            "key_universe_sha256": "b" * 64,
            "extension_cities": extension,
            "all_source_cities": source,
        },
    }


def _settings(tmp_path: Path) -> auth.PredictorExtensionSettings:
    return auth.PredictorExtensionSettings(
        root=tmp_path,
        config_path=tmp_path / "config.toml",
        anchors={},
        scope={},
        authorization=tmp_path / "authorization.json",
        database=tmp_path / "runtime" / "tasks.sqlite",
        control=tmp_path / "runtime" / "control.json",
        status=tmp_path / "runtime" / "status.json",
        log=tmp_path / "runtime" / "worker.log",
        worker_lock=tmp_path / "runtime" / "worker.lock",
        acquisition_root=tmp_path / "acquisition",
        component_root=tmp_path / "components",
        output_root=tmp_path / "output",
        completion_root=tmp_path / "completion",
        acquisition_completion=tmp_path / "completion" / "acquisition.json",
        predictor_completion=tmp_path / "completion" / "predictors.json",
        official_sentinel_hosts=(
            "planetarycomputer.microsoft.com",
            "*.blob.core.windows.net",
        ),
        lease_seconds=60,
        heartbeat_seconds=1,
        retry_base_seconds=1,
        retry_max_seconds=2,
    )


def test_exact_feature_and_context_contract() -> None:
    assert len(auth.STATIC_FEATURES) == 18
    assert len(auth.CALENDAR_FEATURES) == 2
    assert len(auth.DAYMET_FEATURES) == 21
    assert len(auth.SENTINEL_FEATURES) == 5
    assert len(auth.FEATURE_NAMES) == 46
    assert auth.REQUIRED_COLUMNS == (
        "city_id",
        "tract_geoid",
        "target_date",
        *auth.FEATURE_NAMES,
    )
    assert "city_centroid_latitude_deg" not in auth.REQUIRED_COLUMNS
    assert auth.CONTEXT_FEATURES == ("city_centroid_latitude_deg",)
    assert auth.CITY_CENTROID_ALGORITHM == (
        "authenticate_census_place_city_boundary;project_to_locked_target_grid_crs;"
        "unary_union;centroid;transform_centroid_to_epsg4326;take_latitude"
    )


def test_task_plan_is_exact_resumable_and_has_no_blind_city() -> None:
    specs = runtime.task_specs_from_predictor_authorization(_permit())
    assert len(specs) == runtime.EXPECTED_TASK_COUNT == 85
    assert len({spec.task_id for spec in specs}) == len(specs)
    by_kind: dict[str, int] = {}
    for spec in specs:
        by_kind[spec.kind] = by_kind.get(spec.kind, 0) + 1
    assert by_kind == {
        "freeze_key_universe": 1,
        "authenticate_static_reuse": 2,
        "acquire_daymet_metadata": 10,
        "acquire_daymet_subset": 60,
        "build_sentinel_inventory": 2,
        "acquire_sentinel_cache": 2,
        "finalize_acquisition": 1,
        "build_extension_city": 2,
        "compile_source_city": 4,
        "finalize_predictors": 1,
    }
    encoded = json.dumps([spec.payload for spec in specs], sort_keys=True, ensure_ascii=False)
    assert not any(city_id in encoded for city_id in auth.BLIND_CITY_IDS)


def test_offline_phase_is_sealed_before_acquisition_completion(tmp_path: Path) -> None:
    queue = ModelRunQueue(tmp_path / "tasks.sqlite")
    permit = _permit()
    run_id = runtime.source_predictor_run_id(permit)
    queue.initialize_run(
        run_id,
        runtime.task_specs_from_predictor_authorization(permit),
        desired_state="paused",
    )
    assert runtime.active_predictor_kind(queue, run_id, runtime.ONLINE_PHASE) == (
        "freeze_key_universe"
    )
    with pytest.raises(
        runtime.M3SourcePredictorRuntimeError,
        match="sealed until acquisition completion",
    ):
        runtime.active_predictor_kind(queue, run_id, runtime.OFFLINE_PHASE)


def test_runtime_config_rejects_write_target_in_old_runtime(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / auth.DEFAULT_CONFIG).read_text(encoding="utf-8")
    text = text.replace(
        'database = "data/interim/multicity/m3_source_predictor_extension_v1/runtime/tasks.sqlite"',
        'database = "data/interim/multicity/m3_source_development_v2/runtime/evil.sqlite"',
    )
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(
        auth.M3SourcePredictorExtensionError,
        match="isolated from every prior runtime",
    ):
        auth.load_predictor_extension_settings(tmp_path, config)


def test_live_authorization_preview_is_metadata_only_and_exact() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = auth.build_m3_source_predictor_extension_authorization(root)
    universe = payload["key_universe"]
    extension = {row["city_id"]: row for row in universe["extension_cities"]}

    assert payload["authorization_access_audit"] == {
        "predictor_values_read": False,
        "qa_values_read": False,
        "target_values_read": False,
        "network_or_href_reads": 0,
        "blind_test_city_accessed": False,
        "old_artifact_modified": False,
        "model_fit_select_predict_or_score_performed": False,
    }
    assert extension["houston_tx"]["target_date_count"] == 81
    assert extension["houston_tx"]["row_count"] == 52_731
    assert extension["chicago_il"]["target_date_count"] == 82
    assert extension["chicago_il"]["row_count"] == 63_960
    assert len(universe["qa_overpass_commit_set_sha256"]) == 64
    assert len(universe["qa_city_commit_set_sha256"]) == 64
    code_paths = {record["path"] for record in payload["code_identity"]["files"]}
    assert {
        "src/la_heat/model_run_queue.py",
        "src/la_heat/multicity/m3_source_development_worker.py",
        "src/la_heat/provenance.py",
        "src/la_heat/weather_daymet.py",
        "src/la_heat/sentinel_feature_builder.py",
    } <= code_paths


def test_runner_forces_native_thread_limits_before_la_heat_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_m3_source_predictor_extension_v1.py").read_text(encoding="utf-8")
    assignment = 'os.environ[_thread_env_name] = "1"'
    assert assignment in source
    assert source.index(assignment) < source.index("from la_heat")
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "GDAL_NUM_THREADS",
    ):
        assert f'"{name}"' in source


def test_metadata_record_check_does_not_touch_predictor_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    expected = settings.output_root / "houston_tx" / "predictors_46.parquet"
    record = {
        "path": expected.relative_to(tmp_path).as_posix(),
        "bytes": 123,
        "sha256": "a" * 64,
    }
    assert not expected.exists()
    assert (
        auth._completion_metadata_record_path(
            settings,
            record,
            allowed_root=settings.output_root,
            label="synthetic predictor",
            expected_path=expected,
        )
        == expected
    )


def test_sentinel_url_gate_accepts_only_exact_or_subdomain_https_hosts() -> None:
    hosts = ("planetarycomputer.microsoft.com", "*.blob.core.windows.net")
    assert worker._official_sentinel_url(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        hosts,
        label="STAC",
    ).endswith("/api/stac/v1")
    assert worker._official_sentinel_url(
        "https://sentinel2l2a01.blob.core.windows.net/container/B04.tif",
        hosts,
        label="asset",
    ).endswith("B04.tif")
    for value in (
        "http://planetarycomputer.microsoft.com/api/stac/v1",
        "https://planetarycomputer.microsoft.com:443/api/stac/v1",
        "https://user@planetarycomputer.microsoft.com/api/stac/v1",
        "https://blob.core.windows.net/container/B04.tif",
        "https://sentinel.blob.core.windows.net.evil.test/B04.tif",
        "https://example.test/B04.tif",
    ):
        with pytest.raises(worker.M3SourcePredictorCompatibilityError):
            worker._official_sentinel_url(value, hosts, label="tampered")


def test_sentinel_inventory_byte_tamper_is_rejected_before_any_csv_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    permit = _permit()
    adapter = worker.SafeExistingBuilderAdapter(settings, permit, runtime.ONLINE_PHASE)
    directory = settings.acquisition_root / "sentinel" / "houston_tx"
    directory.mkdir(parents=True)
    paths = [
        directory / name
        for name in (
            "selected_acquisitions.csv",
            "target_window_membership.csv",
            "selected_items.csv",
        )
    ]
    for path in paths:
        path.write_text("locked\n", encoding="utf-8")
    files = [auth._file_record(tmp_path, path) for path in paths]
    marker = auth._with_commit(
        {
            "state": "sentinel_inventory_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_id": "houston_tx",
            "timezone": "America/Chicago",
            "target_dates": ["2020-05-01"],
            "target_dates_sha256": auth.canonical_sha256(("2020-05-01",)),
            "window_days_before_target": [60, 1],
            "global_cloud_cover_filter": False,
            "files": files,
            "credentials_or_signed_urls_persisted": False,
            "target_or_landsat_values_read": False,
            "blind_test_city_accessed": False,
        }
    )
    marker_path = directory / "INVENTORY_COMPLETE.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    paths[-1].write_text("tampered\n", encoding="utf-8")
    monkeypatch.setattr(
        pd,
        "read_csv",
        lambda *args, **kwargs: pytest.fail("CSV read occurred before all file locks"),
    )
    with pytest.raises(
        worker.M3SourcePredictorCompatibilityError,
        match="byte lock drifted",
    ):
        adapter._authenticate_sentinel_inventory_for_cache("houston_tx", marker_path)


def test_sentinel_membership_tamper_is_rejected_after_self_commit_rewrite(
    tmp_path: Path,
) -> None:
    from datetime import UTC, date, datetime

    import shapely

    from la_heat.multicity.portable_sentinel_inventory import (
        _membership_frame,
        _selected_acquisitions_frame,
        _selected_items_frame,
        build_city_window_membership,
    )
    from la_heat.sentinel_inventory import (
        REQUIRED_SENTINEL_ASSETS,
        CohortSelection,
        PhysicalAcquisitionKey,
        SentinelItemRecord,
        sentinel_inventory_semantic_sha256,
    )

    settings = _settings(tmp_path)
    permit = _permit()
    adapter = worker.SafeExistingBuilderAdapter(settings, permit, runtime.ONLINE_PHASE)
    directory = settings.acquisition_root / "sentinel" / "houston_tx"
    stac = directory / "stac"
    stac.mkdir(parents=True)
    snapshot = stac / "item-1.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot_sha = sha256_file(snapshot)
    acquired = datetime(2020, 4, 1, 12, tzinfo=UTC)
    key = PhysicalAcquisitionKey("sentinel-2a", acquired, "45", "DT1")
    asset_url = "https://sentinel2l2a01.blob.core.windows.net/container/value.tif"
    item = SentinelItemRecord(
        item_id="item-1",
        platform="sentinel-2a",
        acquired_utc=acquired,
        relative_orbit="45",
        datatake_id="DT1",
        mgrs_tile="15RTM",
        processing_baseline="05.09",
        generation_time=datetime(2020, 4, 2, 12, tzinfo=UTC),
        geometry_wgs84=shapely.box(-96.0, 29.0, -95.0, 30.0),
        asset_hrefs=tuple((name, asset_url) for name in REQUIRED_SENTINEL_ASSETS),
        cloud_cover_percent=10.0,
    )
    selection = CohortSelection(
        acquisition_key=key,
        processing_baseline="05.09",
        union_aoi_coverage_fraction=1.0,
        generation_time=item.generation_time,
        item_ids=(item.item_id,),
        items=(item,),
    )
    selections = (selection,)
    targets = (date(2020, 5, 1),)
    acquisitions = _selected_acquisitions_frame("houston_tx", "America/Chicago", selections)
    items = _selected_items_frame(
        "houston_tx",
        "America/Chicago",
        selections,
        {"item-1": {"filename": snapshot.name, "sha256": snapshot_sha}},
    )
    membership = _membership_frame(
        "houston_tx",
        build_city_window_membership(targets, selections, timezone="America/Chicago"),
    )
    table_paths = {
        "selected_acquisitions.csv": acquisitions,
        "selected_items.csv": items,
        "target_window_membership.csv": membership,
    }
    for name, frame in table_paths.items():
        frame.to_csv(directory / name, index=False)
    files = [
        *[auth._file_record(tmp_path, directory / name) for name in table_paths],
        auth._file_record(tmp_path, snapshot),
    ]
    marker_payload = {
        "state": "sentinel_inventory_complete",
        "authorization_commit_sha256": permit["commit_sha256"],
        "city_id": "houston_tx",
        "timezone": "America/Chicago",
        "target_dates": ["2020-05-01"],
        "target_dates_sha256": auth.canonical_sha256(("2020-05-01",)),
        "window_days_before_target": [60, 1],
        "global_cloud_cover_filter": False,
        "selected_physical_acquisition_count": 1,
        "selected_item_count": 1,
        "membership_count": len(membership),
        "inventory_semantic_sha256": sentinel_inventory_semantic_sha256(selections),
        "target_membership_semantic_sha256": auth.canonical_sha256(membership.to_dict("records")),
        "files": files,
        "credentials_or_signed_urls_persisted": False,
        "target_or_landsat_values_read": False,
        "blind_test_city_accessed": False,
    }
    marker_path = directory / "INVENTORY_COMPLETE.json"
    marker_path.write_text(json.dumps(auth._with_commit(marker_payload)), encoding="utf-8")

    tampered = membership.copy()
    tampered.loc[:, "lag_days"] = 1
    tampered.to_csv(directory / "target_window_membership.csv", index=False)
    marker_payload["files"] = [
        *[auth._file_record(tmp_path, directory / name) for name in table_paths],
        auth._file_record(tmp_path, snapshot),
    ]
    marker_payload["target_membership_semantic_sha256"] = auth.canonical_sha256(
        tampered.to_dict("records")
    )
    marker_path.write_text(json.dumps(auth._with_commit(marker_payload)), encoding="utf-8")
    with pytest.raises(
        worker.M3SourcePredictorCompatibilityError,
        match="target membership identity drifted",
    ):
        adapter._authenticate_sentinel_inventory_for_cache("houston_tx", marker_path)


def test_static_geoid_helper_authenticates_exact_synthetic_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    city_id = "houston_tx"
    directory = tmp_path / "static" / city_id
    directory.mkdir(parents=True)
    frame = pd.DataFrame({"tract_geoid": ["g2", "g1"]})
    table = directory / "static_features.parquet"
    frame.to_parquet(table, index=False)
    manifest = auth._with_commit(
        {
            "city_id": city_id,
            "output_files": {
                "static_features.parquet": {
                    "bytes": table.stat().st_size,
                    "sha256": sha256_file(table),
                }
            },
        }
    )
    manifest_path = directory / "static_features_provenance.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    permit = {"inputs": {"static_provenance": [auth._file_record(tmp_path, manifest_path)]}}
    settings = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(auth, "load_predictor_extension_settings", lambda *args, **kwargs: settings)
    monkeypatch.setitem(auth.EXPECTED_CITY_COUNTS[city_id], "tracts", 2)

    assert auth.authenticated_static_tract_geoids(tmp_path, permit, city_id) == (
        "g1",
        "g2",
    )


def test_anonymous_daymet_rejects_html_without_poisoning_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, *, chunk_size: int) -> list[bytes]:
            assert chunk_size == 1024 * 1024
            return [b"<html>login</html>"]

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    adapter = object.__new__(worker.SafeExistingBuilderAdapter)
    destination = tmp_path / "subset.nc"
    with pytest.raises(worker.M3SourcePredictorWorkerError, match="not NetCDF"):
        adapter._stream_anonymous_daymet("https://example.test/subset", destination)
    assert not destination.exists()
    assert not destination.with_suffix(".nc.partial").exists()


class _BlockedAdapter:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, kind: str, payload: Any) -> Mapping[str, Any]:
        del kind, payload
        raise self.error


def _run_one_blocked_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> tuple[ModelRunQueue, str, dict[str, Any]]:
    settings = _settings(tmp_path)
    permit = _permit()
    run_id = runtime.source_predictor_run_id(permit)
    queue = ModelRunQueue(settings.database)
    queue.initialize_run(
        run_id,
        runtime.task_specs_from_predictor_authorization(permit),
        desired_state="running",
    )
    monkeypatch.setattr(
        worker,
        "load_m3_source_predictor_extension_runtime_permit",
        lambda *args, **kwargs: permit,
    )
    monkeypatch.setattr(
        worker,
        "source_predictor_runtime_status",
        lambda *args, **kwargs: {
            "state": "running",
            "counts": queue.counts(run_id),
        },
    )
    result = worker._execute_unlocked(
        settings=settings,
        permit=permit,
        options=worker.PredictorWorkerOptions(phase=runtime.ONLINE_PHASE, poll_seconds=0.001),
        adapter=_BlockedAdapter(error),
    )
    return queue, run_id, result


def test_missing_earthdata_token_leaves_pending_paused_for_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, run_id, result = _run_one_blocked_task(
        tmp_path,
        monkeypatch,
        worker.M3SourcePredictorCredentialRequiredError("set an environment token"),
    )
    task = next(task for task in queue.list_tasks(run_id) if task.task_id == "freeze-key-universe")
    assert task.status == "pending"
    assert task.error_type == "M3SourcePredictorCredentialRequiredError"
    assert queue.get_desired_state(run_id) == "paused"
    assert queue.counts(run_id)["quarantined"] == 0
    assert result["last_error_type"] == "M3SourcePredictorCredentialRequiredError"


def test_permanent_compatibility_error_quarantines_without_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, run_id, result = _run_one_blocked_task(
        tmp_path,
        monkeypatch,
        worker.M3SourcePredictorCompatibilityError("synthetic contract drift"),
    )
    task = next(task for task in queue.list_tasks(run_id) if task.task_id == "freeze-key-universe")
    assert task.status == "quarantined"
    assert queue.get_desired_state(run_id) == "paused"
    assert queue.counts(run_id)["quarantined"] == 1
    assert result["last_error_type"] == "M3SourcePredictorCompatibilityError"
