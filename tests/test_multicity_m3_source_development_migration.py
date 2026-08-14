from __future__ import annotations

import json
from pathlib import Path

import pytest

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.m3_source_development_migration import (
    MANIFEST_FILENAME,
    M3SourceMigrationError,
    create_transfer_folder,
    verify_transfer_folder,
)
from la_heat.provenance import sha256_file


def _write_runner_config(root: Path) -> None:
    path = root / "configs/multicity/m3_source_development_runner.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
schema_version = 1
algorithm_version = "m3-source-development-runtime-v1"
[inputs]
protocol_lock = "manifests/protocol.json"
source_acquisition_amendment = "manifests/amendment.json"
expanded_source_inventory = "manifests/inventory.json"
execution_authorization = "manifests/authorization.json"
[runtime]
database = "data/runtime/tasks.sqlite"
control = "data/runtime/control.json"
status = "data/runtime/status.json"
log = "data/runtime/worker.log"
cache_root = "data/cache"
qa_output_root = "data/qa"
completion_root = "manifests/completion"
[office_mode]
download_workers = 2
compute_workers = 1
raster_window_size = 512
network_timeout_seconds = 20
network_recheck_seconds = 20
lease_seconds = 900
heartbeat_seconds = 30
retry_base_seconds = 5
retry_max_seconds = 300
[limits]
allowed_download_workers = [1, 2]
compute_workers_fixed = 1
raster_window_size_fixed = 512
signed_urls_may_be_persisted = false
credentials_may_be_persisted = false
offline_phase_network_requests_allowed = false
blind_test_city_assets_allowed = false
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _paused_project(root: Path) -> ModelRunQueue:
    _write_runner_config(root)
    for name in ("protocol", "amendment", "inventory", "authorization"):
        path = root / f"manifests/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
    runtime = root / "data/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "control.json").write_text(
        json.dumps({"desired_state": "paused"}), encoding="utf-8"
    )
    (runtime / "status.json").write_text(
        json.dumps({"state": "paused", "active_task_ids": []}), encoding="utf-8"
    )
    queue = ModelRunQueue(runtime / "tasks.sqlite")
    queue.initialize_run(
        "m3-test-run",
        [TaskSpec(task_id="one", kind="download_asset", payload={"scene": "LC08"})],
        desired_state="paused",
    )
    cache = root / "data/cache/cities/los_angeles_ca"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "window.bin").write_bytes(b"safe-cache-content")
    qa = root / "data/qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "checkpoint.json").write_text(
        json.dumps({"state": "partial"}), encoding="utf-8"
    )
    return queue


def test_transfer_requires_pause_checkpoints_sqlite_and_hashes_relative_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _paused_project(root)
    result = create_transfer_folder(
        root,
        output_root="exports/transfer",
        package_name="checkpoint-one",
    )
    destination = Path(result["transfer_directory"])
    manifest = verify_transfer_folder(destination)
    rows = {row["relative_path"]: row for row in manifest["files"]}
    assert "data/runtime/tasks.sqlite" in rows
    assert "data/cache/cities/los_angeles_ca/window.bin" in rows
    assert "data/runtime/worker.log" not in rows
    copied_database = destination / "data/runtime/tasks.sqlite"
    assert rows["data/runtime/tasks.sqlite"]["sha256"] == sha256_file(copied_database)
    assert manifest["sqlite_checkpoint"]["all_desired_states"] == "paused"
    assert manifest["sqlite_checkpoint"]["running_or_leased_task_count"] == 0
    assert manifest["credentials_tokens_cookies_or_signed_urls_included"] is False
    assert (destination / MANIFEST_FILENAME).is_file()


def test_transfer_rejects_running_desired_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    queue = _paused_project(root)
    queue.set_desired_state("m3-test-run", "running")
    with pytest.raises(M3SourceMigrationError, match="still requests execution"):
        create_transfer_folder(root, package_name="must-not-exist")


def test_transfer_rejects_running_lease_even_after_pause_requested(tmp_path: Path) -> None:
    root = tmp_path / "project"
    queue = _paused_project(root)
    queue.set_desired_state("m3-test-run", "running")
    assert queue.claim_next("m3-test-run", owner="worker", lease_seconds=300) is not None
    queue.set_desired_state("m3-test-run", "paused")
    with pytest.raises(M3SourceMigrationError, match="still owns a lease"):
        create_transfer_folder(root, package_name="must-not-exist")


def test_transfer_rejects_persisted_signed_url(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _paused_project(root)
    (root / "data/qa/signed.json").write_text(
        json.dumps({"href": "https://example.test/a.tif?sig=secret"}),
        encoding="utf-8",
    )
    with pytest.raises(M3SourceMigrationError, match="credential or signed URL"):
        create_transfer_folder(root, package_name="must-not-exist")
