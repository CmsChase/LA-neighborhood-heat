from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

import scripts.build_final_evaluation_evidence as evidence


def _write(path: Path, content: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_safe_package_path_rejects_escape_and_absolute_paths() -> None:
    assert evidence._safe_package_path("repository/results.csv").as_posix() == (
        "repository/results.csv"
    )
    for value in (
        "",
        "../escape",
        "repository/../escape",
        "/absolute",
        r"repository\windows",
        "C:/windows",
    ):
        with pytest.raises(RuntimeError, match="Unsafe evidence-relative path"):
            evidence._safe_package_path(value)


def test_validate_cache_set_requires_exact_93_files(tmp_path: Path) -> None:
    root = tmp_path / "targets"
    _write(root / "TARGET_BUILD_LOCK.json")
    expected = {
        "CACHE_COMMIT.json",
        "date_summary.parquet",
        "scene_contributions.parquet",
        "tract_date_qa.parquet",
    }
    for index in range(23):
        directory = root / "by_overpass" / f"overpass-{index:02d}"
        for filename in expected:
            _write(directory / filename)

    assert len(evidence._validate_cache_set(root)) == 93
    _write(root / "unexpected.txt")
    with pytest.raises(RuntimeError, match="root file set changed"):
        evidence._validate_cache_set(root)


def test_validate_recovery_set_is_exact_and_excludes_pycache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "recovery"
    payload = b"audited"
    expected_hash = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        evidence,
        "RECOVERY_FILE_SHA256",
        {"audit.log": expected_hash},
    )
    _write(root / "audit.log", payload)
    _write(root / "__pycache__" / "ignored.pyc", b"cache")

    assert evidence._validate_recovery_set(root) == [root / "audit.log"]
    _write(root / "unexpected.log")
    with pytest.raises(RuntimeError, match="frozen 16-file set"):
        evidence._validate_recovery_set(root)


def test_zip_directory_streams_exact_rooted_members(tmp_path: Path) -> None:
    source = tmp_path / "staging"
    _write(source / "a.txt", b"a")
    _write(source / "nested" / "b.bin", b"\x00\x01")
    destination = tmp_path / "evidence.zip"

    evidence._zip_directory(
        source,
        destination,
        archive_root_name="FINAL_EVIDENCE",
    )

    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "FINAL_EVIDENCE/a.txt",
            "FINAL_EVIDENCE/nested/b.bin",
        }
        assert archive.read("FINAL_EVIDENCE/a.txt") == b"a"
        assert archive.read("FINAL_EVIDENCE/nested/b.bin") == b"\x00\x01"


def test_verify_zip_requires_byte_identity_with_source(tmp_path: Path) -> None:
    source = tmp_path / "staging"
    _write(
        source / "integrity" / "verify_evidence.py",
        b'import json\nprint(json.dumps({"state": "verified"}))\n',
    )
    payload = source / "payload.bin"
    _write(payload, b"original")
    destination = tmp_path / "evidence.zip"
    evidence._zip_directory(
        source,
        destination,
        archive_root_name="FINAL_EVIDENCE",
    )
    payload.write_bytes(b"changed!")

    with pytest.raises(RuntimeError, match="not byte-identical"):
        evidence._verify_zip(
            destination,
            source=source,
            archive_root_name="FINAL_EVIDENCE",
        )


def test_file_publication_is_no_clobber(tmp_path: Path) -> None:
    source = tmp_path / "source.partial"
    destination = tmp_path / "final.zip"
    _write(source, b"complete")
    _write(destination, b"foreign")
    with pytest.raises(FileExistsError):
        evidence._publish_file_no_clobber(source, destination)
    assert source.read_bytes() == b"complete"
    assert destination.read_bytes() == b"foreign"

    destination.unlink()
    evidence._publish_file_no_clobber(source, destination)
    assert not source.exists()
    assert destination.read_bytes() == b"complete"
