from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import la_heat.multicity.portable_sentinel_return as returned
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
    assert (tmp_path / returned.STATUS_PATH).is_file()
    assert (tmp_path / returned.FINAL_OUTPUT).read_bytes() == b"synthetic-final-predictors"
    assert not (tmp_path / returned.SOURCE_BUNDLE_MANIFEST).exists()
    receipt = json.loads((tmp_path / returned.RECEIPT_PATH).read_text(encoding="utf-8"))
    recorded = receipt.pop("commit_sha256")
    assert canonical_sha256(receipt) == recorded
