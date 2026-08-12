from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows startup contract")
def test_windows_powershell_preserves_python_self_check_quotes() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "portable_sentinel_templates/setup_and_launch.ps1"
    ).read_text(encoding="utf-8")
    command_line = next(
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith("& $venvPython -c")
        and "import geopandas" in line
    )

    assert "-c \"" in command_line
    assert "print('Environment ready.')" in command_line
    python_path = str(Path(sys.executable).resolve()).replace("'", "''")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            f"$venvPython = '{python_path}'; " + command_line,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Environment ready." in result.stdout


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows packaging contract")
def test_result_packager_relative_paths_work_in_windows_powershell_5_1() -> None:
    script_path = (
        Path(__file__).resolve().parents[1]
        / "portable_sentinel_templates/package_results.ps1"
    )
    escaped_script_path = str(script_path).replace("'", "''")
    command = f"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '{escaped_script_path}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$functionAst = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-RelativePath'
}}, $true)
Invoke-Expression $functionAst.Extent.Text
$observed = Get-RelativePath `
    -Root 'C:/fixture/root' `
    -Path 'C:/fixture/root/nested/file.txt'
$normalized = $observed.Replace([IO.Path]::DirectorySeparatorChar, '/')
if ($normalized -ne 'nested/file.txt') {{
    throw "Unexpected relative path: $observed"
}}
Write-Output $observed
"""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "nested\\file.txt" in result.stdout
