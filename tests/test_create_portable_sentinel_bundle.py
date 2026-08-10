from __future__ import annotations

import json
from pathlib import Path

from scripts.create_portable_sentinel_bundle import (
    DIRECTORY_COPIES,
    EXPECTED_PROGRAM_FILES,
    REQUIRED_FILES,
    create_bundle,
)


def _write(path: Path, value: bytes = b"test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def test_create_bundle_is_runnable_and_excludes_transient_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    runtime_source = tmp_path / "runtime_source"
    destination = tmp_path / "GAMING_LAPTOP_SENTINEL"

    for relative in EXPECTED_PROGRAM_FILES + REQUIRED_FILES:
        _write(project / relative)
    for source_relative, _target_relative in DIRECTORY_COPIES:
        (project / source_relative).mkdir(parents=True, exist_ok=True)
    _write(project / "pyproject.toml")
    for name in (
        "START_HERE.cmd",
        "PACKAGE_RESULTS.cmd",
        "README_CN.txt",
        "setup_and_launch.ps1",
        "package_results.ps1",
    ):
        _write(project / "portable_sentinel_templates" / name)

    _write(runtime_source / "runtime/python/python.exe")
    _write(runtime_source / "runtime/python/Lib/tokenize.py")
    _write(runtime_source / "runtime/python/Lib/http/cookiejar.py")
    _write(runtime_source / "runtime/wheelhouse/dependency.whl")
    _write(runtime_source / "runtime/python-installer.exe")
    _write(runtime_source / "portable_requirements_lock.txt")
    _write(runtime_source / "portable_python_version.txt")
    _write(project / "src/keep.py")
    _write(project / "src/__pycache__/skip.pyc")
    _write(project / "src/access_token.txt")
    _write(project / "data/raw/sentinel/product_metadata/item.xml")

    manifest = create_bundle(project, destination, runtime_source)

    assert manifest["engine_ready"] is True
    assert (destination / "START_HERE.cmd").is_file()
    assert (destination / "runtime/python/python.exe").is_file()
    assert (destination / "runtime/python/Lib/tokenize.py").is_file()
    assert (destination / "runtime/python/Lib/http/cookiejar.py").is_file()
    assert (destination / "src/keep.py").is_file()
    assert (
        destination
        / "data/raw/multicity/portable_predictors/"
        "sentinel_product_metadata/los_angeles_ca/item.xml"
    ).is_file()
    assert not (destination / "src/__pycache__").exists()
    assert not (destination / "src/access_token.txt").exists()
    written = json.loads(
        (destination / "PORTABLE_BUNDLE_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert written["long_computation_started"] is False
    assert written["file_count"] == manifest["file_count"]
