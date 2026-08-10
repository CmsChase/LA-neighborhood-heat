from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import la_heat.multicity.portable_sentinel_directory_return as directory_return
import la_heat.multicity.portable_sentinel_return as returned
import scripts.import_portable_sentinel_results as import_cli
from la_heat.provenance import canonical_sha256


def _locked(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _file_record(path: str, raw: bytes) -> dict[str, object]:
    return {
        "path": path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _build_return_zip(
    root: Path,
    *,
    status_state: str = "complete",
) -> Path:
    bundle_raw = b'{"bundle_name":"GAMING_LAPTOP_SENTINEL"}\n'
    bundle = root / "exports/GAMING_LAPTOP_SENTINEL/PORTABLE_BUNDLE_MANIFEST.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(bundle_raw)

    status = {
        "schema_version": 1,
        "algorithm_version": "portable-four-city-sentinel-v1",
        "state": status_state,
        "total": 516,
        "completed": 516,
        "pending": 0,
        "running": 0,
        "failed": 0,
        "current": [],
        "current_city": None,
        "error": None,
        "cities": {
            city_id: {
                "total": total,
                "completed": total,
                "running": 0,
                "failed": 0,
                "state": "complete",
            }
            for city_id, total in returned.EXPECTED_CITY_TOTALS.items()
        },
    }
    files: dict[str, bytes] = {
        returned.SOURCE_BUNDLE_MANIFEST: bundle_raw,
        returned.STATUS_PATH: json.dumps(status).encode(),
    }
    city_commits: dict[str, str] = {}
    component_root = "data/processed/multicity/portable_predictors/components/sentinel"
    for city_id, acquisition_count in returned.EXPECTED_ACQUISITIONS.items():
        outputs: dict[str, dict[str, object]] = {}
        directory = f"{component_root}/{city_id}"
        for name in returned.CITY_OUTPUTS:
            path = f"{directory}/{name}"
            raw = f"{city_id}-{name}".encode()
            files[path] = raw
            outputs[name] = _file_record(path, raw)
        completion = _locked(
            {
                "schema_version": 1,
                "algorithm_version": "portable-four-city-sentinel-v1",
                "state": "complete",
                "city_id": city_id,
                "base_lock": {},
                "physical_acquisition_count": acquisition_count,
                "outputs": outputs,
                "access_contract": {
                    "external_target_or_qa_values_read": False,
                    "model_fit_or_prediction_performed": False,
                },
            }
        )
        city_commits[city_id] = str(completion["commit_sha256"])
        files[f"{directory}/{returned.CITY_COMPLETE_FILENAME}"] = json.dumps(
            completion
        ).encode()

    final_raw = b"synthetic-final-predictors"
    files[returned.FINAL_OUTPUT.as_posix()] = final_raw
    final = _locked(
        {
            "schema_version": 1,
            "algorithm_version": "portable-four-city-sentinel-v1",
            "state": "complete_target_blind_46_feature_predictors",
            "city_count": 4,
            "row_count": returned.EXPECTED_ROWS,
            "feature_count": 46,
            "city_complete_commits": city_commits,
            "output": _file_record(returned.FINAL_OUTPUT.as_posix(), final_raw),
            "access_contract": {
                "external_target_or_qa_values_read": False,
                "model_fit_or_prediction_performed": False,
            },
        }
    )
    files[returned.FINAL_COMPLETE.as_posix()] = json.dumps(final).encode()
    records = [_file_record(path, raw) for path, raw in sorted(files.items())]
    result_manifest = {
        "schema_version": 1,
        "packaged_at_utc": "2026-08-10T00:00:00Z",
        "source_state": "complete",
        "source_status": returned.STATUS_PATH,
        "files": records,
    }
    archive_path = root / "GAMING_LAPTOP_SENTINEL_RESULTS_test.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path, raw in files.items():
            archive.writestr(f"{returned.RESULT_ROOT_NAME}/{path}", raw)
        archive.writestr(
            f"{returned.RESULT_ROOT_NAME}/{returned.RESULT_MANIFEST_NAME}",
            json.dumps(result_manifest).encode(),
        )
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    Path(f"{archive_path}.sha256").write_text(
        f"{archive_sha} *{archive_path.name}\n", encoding="utf-8"
    )
    return archive_path


def test_verify_completed_return_without_writing(tmp_path: Path) -> None:
    archive = _build_return_zip(tmp_path)

    summary = returned.verify_and_import_portable_sentinel_results(
        archive, tmp_path, verify_only=True
    )

    assert summary.source_state == "complete"
    assert summary.completed_work_units == 516
    assert summary.imported is False
    assert not (tmp_path / returned.RECEIPT_PATH).exists()


def test_rejects_noncomplete_runtime_status(tmp_path: Path) -> None:
    archive = _build_return_zip(tmp_path, status_state="paused")

    with pytest.raises(
        returned.PortableSentinelReturnError,
        match="not a clean 516/516 completion",
    ):
        returned.verify_and_import_portable_sentinel_results(
            archive, tmp_path, verify_only=True
        )


def test_rejects_wrong_companion_checksum(tmp_path: Path) -> None:
    archive = _build_return_zip(tmp_path)
    Path(f"{archive}.sha256").write_text(
        f"{'0' * 64} *{archive.name}\n", encoding="utf-8"
    )

    with pytest.raises(
        returned.PortableSentinelReturnError,
        match="SHA-256 does not match",
    ):
        returned.verify_and_import_portable_sentinel_results(
            archive, tmp_path, verify_only=True
        )


def test_imports_only_result_owned_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _build_return_zip(tmp_path)
    monkeypatch.setattr(returned, "_authenticate_imported_outputs", lambda _root: None)

    summary = returned.verify_and_import_portable_sentinel_results(archive, tmp_path)

    assert summary.imported is True
    assert not (tmp_path / returned.STATUS_PATH).exists()
    assert (tmp_path / returned.FINAL_OUTPUT).read_bytes() == b"synthetic-final-predictors"
    assert not (tmp_path / returned.SOURCE_BUNDLE_MANIFEST).exists()
    receipt = json.loads((tmp_path / returned.RECEIPT_PATH).read_text(encoding="utf-8"))
    recorded = receipt.pop("commit_sha256")
    assert canonical_sha256(receipt) == recorded


def _extract_result_directory(archive: Path, root: Path) -> Path:
    with ZipFile(archive) as package:
        package.extractall(root)
    return root / returned.RESULT_ROOT_NAME


def _selected_directory_records(source: Path) -> dict[str, dict[str, object]]:
    manifest = json.loads(
        (source / returned.RESULT_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    return {
        str(record["path"]): record
        for record in manifest["files"]
        if returned._should_import(str(record["path"]))
    }


def test_imports_complete_extracted_result_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "package"
    archive = _build_return_zip(package_root)
    source = _extract_result_directory(archive, tmp_path / "copied")
    project = tmp_path / "project"
    project.mkdir()
    selected = _selected_directory_records(source)
    monkeypatch.setattr(
        directory_return,
        "_validate_bundle_identity",
        lambda _source, _project: "1" * 64,
    )
    monkeypatch.setattr(
        directory_return,
        "_collect_checkpoint_records",
        lambda _source, _project: (
            selected,
            dict(returned.EXPECTED_ACQUISITIONS),
            tuple(sorted(returned.EXPECTED_CITY_TOTALS)),
            True,
        ),
    )
    component_root = "data/processed/multicity/portable_predictors/components/sentinel"
    city_commits = {
        city_id: json.loads(
            (
                source
                / component_root
                / city_id
                / returned.CITY_COMPLETE_FILENAME
            ).read_text(encoding="utf-8")
        )["commit_sha256"]
        for city_id in returned.EXPECTED_CITY_TOTALS
    }
    final_commit = json.loads(
        (source / returned.FINAL_COMPLETE).read_text(encoding="utf-8")
    )["commit_sha256"]
    monkeypatch.setattr(
        directory_return,
        "reauthenticate_canonical_portable_sentinel_completion",
        lambda _project: directory_return.CanonicalPortableSentinelCompletion(
            completed_work_units=516,
            total_work_units=516,
            resumable_acquisition_count=511,
            complete_city_count=4,
            scientifically_complete=True,
            city_complete_commits=city_commits,
            final_complete_commit_sha256=final_commit,
        ),
    )

    summary = directory_return.verify_and_import_portable_sentinel_directory(
        source, project
    )

    assert summary.scientifically_complete is True
    assert summary.completed_work_units == 516
    assert (project / returned.FINAL_OUTPUT).read_bytes() == b"synthetic-final-predictors"
    assert summary.checkpoint == returned.RECEIPT_PATH.as_posix()
    receipt = json.loads((project / returned.RECEIPT_PATH).read_text(encoding="utf-8"))
    recorded = receipt.pop("commit_sha256")
    assert canonical_sha256(receipt) == recorded
    assert receipt["state"] == "complete_verified_portable_sentinel_return"
    assert receipt["archive"] is None
    assert receipt["returned_source"]["kind"] == "result_manifest_directory"


def _partial_source(root: Path) -> tuple[Path, dict[str, dict[str, object]]]:
    source = root / "GAMING_LAPTOP_SENTINEL"
    cache = source / (
        "data/interim/multicity/portable_predictors/runtime/sentinel/"
        "chicago_il/by_acquisition/0123456789abcdef0123"
    )
    cache.mkdir(parents=True)
    (cache / "summary.json").write_text('{"state":"complete"}', encoding="utf-8")
    (cache / "acquisition_tract.parquet").write_bytes(b"checkpoint")
    status = {
        "algorithm_version": "portable-four-city-sentinel-v1",
        "state": "paused",
        "total": 516,
        "completed": 1,
        "running": 0,
        "current": [],
        "cities": {
            city_id: {
                "total": total,
                "completed": int(city_id == "chicago_il"),
                "running": 0,
            }
            for city_id, total in returned.EXPECTED_CITY_TOTALS.items()
        },
    }
    status_path = source / returned.STATUS_PATH
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status), encoding="utf-8")
    (source / returned.SOURCE_BUNDLE_MANIFEST).write_text("{}", encoding="utf-8")
    records = {
        path.relative_to(source).as_posix(): _file_record(
            path.relative_to(source).as_posix(), path.read_bytes()
        )
        for path in cache.iterdir()
    }
    return source, records


def test_partial_directory_import_is_idempotent_and_resume_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, records = _partial_source(tmp_path / "copied")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        directory_return,
        "_validate_bundle_identity",
        lambda _source, _project: "2" * 64,
    )
    monkeypatch.setattr(
        directory_return,
        "_collect_checkpoint_records",
        lambda _source, _project: (
            records,
            {
                city_id: int(city_id == "chicago_il")
                for city_id in returned.EXPECTED_CITY_TOTALS
            },
            (),
            False,
        ),
    )

    first = directory_return.verify_and_import_portable_sentinel_directory(
        source, project
    )
    second = directory_return.verify_and_import_portable_sentinel_directory(
        source, project
    )

    assert first.scientifically_complete is False
    assert first.imported_file_count == 2
    assert first.next_action.startswith("resume Sentinel dashboard")
    assert second.imported_file_count == 0
    assert second.unchanged_file_count == 2
    checkpoint = json.loads(
        (project / directory_return.IMPORT_STATUS_PATH).read_text(encoding="utf-8")
    )
    assert checkpoint["state"] == "resume_ready"


def test_directory_import_refuses_existing_different_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, records = _partial_source(tmp_path / "copied")
    project = tmp_path / "project"
    conflict = project / next(iter(records))
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b"keep-user-data")
    monkeypatch.setattr(
        directory_return,
        "_validate_bundle_identity",
        lambda _source, _project: "3" * 64,
    )
    monkeypatch.setattr(
        directory_return,
        "_collect_checkpoint_records",
        lambda _source, _project: (
            records,
            {
                city_id: int(city_id == "chicago_il")
                for city_id in returned.EXPECTED_CITY_TOTALS
            },
            (),
            False,
        ),
    )

    with pytest.raises(
        returned.PortableSentinelReturnError,
        match="would overwrite existing different data",
    ):
        directory_return.verify_and_import_portable_sentinel_directory(
            source, project
        )

    assert conflict.read_bytes() == b"keep-user-data"
    assert not (project / directory_return.IMPORT_STATUS_PATH).exists()


@pytest.mark.parametrize("kind", ["directory", "zip"])
def test_import_rejects_destination_parent_linked_outside_project(
    tmp_path: Path,
    kind: str,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    link = project / "data"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode:
            pytest.skip(f"Directory junctions are unavailable: {created.stderr}")
    else:
        link.symlink_to(outside, target_is_directory=True)
    relative = (
        "data/raw/multicity/portable_predictors/sentinel_product_metadata/"
        "chicago_il/item.xml"
    )
    records = {relative: _file_record(relative, b"metadata")}

    with pytest.raises(
        returned.PortableSentinelReturnError,
        match="parent resolves outside the project root",
    ):
        if kind == "directory":
            directory_return._preflight(project, records)
        else:
            returned._preflight_member_destinations(
                project, SimpleNamespace(records=records)
            )


def test_finalize_resumed_directory_writes_formal_receipt(tmp_path: Path) -> None:
    project = tmp_path / "project"
    status_path = project / directory_return.IMPORT_STATUS_PATH
    status_path.parent.mkdir(parents=True)
    import_status = {
        "schema_version": 1,
        "state": "resume_ready",
        "source_directory": str(tmp_path / "copied"),
        "source_kind": "copied_portable_project",
        "source_state": "paused",
        "source_bundle_manifest_sha256": "a" * 64,
        "result_manifest_sha256": None,
        "completed_work_units": 12,
        "total_work_units": 516,
        "resumable_acquisition_count": 12,
        "complete_cities": [],
        "imported_file_count": 24,
        "unchanged_file_count": 3,
        "checkpoint_set_sha256": "b" * 64,
        "scientifically_complete": False,
        "model_fit_or_prediction_performed": False,
    }
    import_status["commit_sha256"] = canonical_sha256(import_status)
    status_path.write_text(json.dumps(import_status), encoding="utf-8")
    completion = directory_return.CanonicalPortableSentinelCompletion(
        completed_work_units=516,
        total_work_units=516,
        resumable_acquisition_count=511,
        complete_city_count=4,
        scientifically_complete=True,
        city_complete_commits={
            city_id: str(index) * 64
            for index, city_id in enumerate(returned.EXPECTED_CITY_TOTALS, start=1)
        },
        final_complete_commit_sha256="f" * 64,
    )

    receipt_path = directory_return.finalize_resumed_portable_sentinel_directory_return(
        project,
        completion=completion,
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    recorded = receipt.pop("commit_sha256")
    assert canonical_sha256(receipt) == recorded
    assert receipt["state"] == "complete_verified_portable_sentinel_return"
    assert receipt["returned_source"] == {
        "kind": "copied_portable_project",
        "path": str(tmp_path / "copied"),
    }
    assert receipt["imported_file_count"] == 24
    assert receipt["completed_work_units"] == 516


@pytest.mark.parametrize("complete_after_resume", [False, True])
def test_cli_reauthenticates_after_dashboard_before_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    complete_after_resume: bool,
) -> None:
    initial = SimpleNamespace(
        scientifically_complete=False,
        to_dict=lambda: {"scientifically_complete": False},
    )
    canonical = directory_return.CanonicalPortableSentinelCompletion(
        completed_work_units=516 if complete_after_resume else 12,
        total_work_units=516,
        resumable_acquisition_count=511 if complete_after_resume else 12,
        complete_city_count=4 if complete_after_resume else 0,
        scientifically_complete=complete_after_resume,
        city_complete_commits={},
        final_complete_commit_sha256=None,
    )
    commands: list[list[str]] = []
    finalized: list[directory_return.CanonicalPortableSentinelCompletion] = []

    monkeypatch.setattr(
        import_cli,
        "verify_and_import_portable_sentinel_directory",
        lambda *_args, **_kwargs: initial,
    )
    monkeypatch.setattr(
        import_cli,
        "reauthenticate_canonical_portable_sentinel_completion",
        lambda _root: canonical,
    )
    monkeypatch.setattr(
        import_cli,
        "finalize_resumed_portable_sentinel_directory_return",
        lambda _root, *, completion: (
            finalized.append(completion) or tmp_path / returned.RECEIPT_PATH
        ),
    )

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(import_cli.subprocess, "run", fake_run)

    code = import_cli.main(
        [
            "--source-directory",
            str(tmp_path / "copied"),
            "--project-root",
            str(tmp_path),
            "--resume-dashboard",
        ]
    )

    assert code == 0
    assert "run_portable_sentinel_dashboard.py" in commands[0][1]
    if complete_after_resume:
        assert len(commands) == 2
        assert "audit_multicity_predictor_readiness.py" in commands[1][1]
        assert finalized == [canonical]
    else:
        assert len(commands) == 1
        assert finalized == []
        assert "Resume-ready, not scientifically complete" in capsys.readouterr().out
