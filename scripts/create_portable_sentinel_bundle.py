"""Assemble the copy-ready gaming-laptop Sentinel-2 feature bundle.

This is a file-staging operation only.  It never starts the Sentinel raster
build and it does not copy credentials, old virtual environments, or model
outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = PROJECT_ROOT / "exports/GAMING_LAPTOP_SENTINEL"


def _default_runtime_source(project_root: Path) -> Path:
    exports = project_root / "exports"
    if exports.is_dir():
        candidates = sorted(
            (path for path in exports.iterdir() if path.is_dir()),
            key=lambda path: (not path.name.endswith("_MODEL_RUNNER_8845H"), path.name),
        )
        for candidate in candidates:
            if (
                (candidate / "runtime/python/python.exe").is_file()
                and (candidate / "runtime/wheelhouse").is_dir()
                and (candidate / "portable_requirements_lock.txt").is_file()
            ):
                return candidate
    return exports / "MODEL_RUNNER_RUNTIME_SOURCE"


DEFAULT_RUNTIME_SOURCE = _default_runtime_source(PROJECT_ROOT)

EXPECTED_PROGRAM_FILES = (
    Path("scripts/run_portable_sentinel_dashboard.py"),
    Path("scripts/build_portable_sentinel_features.py"),
    Path("src/la_heat/multicity/portable_sentinel_dashboard.py"),
    Path("src/la_heat/multicity/portable_sentinel_build.py"),
)

REQUIRED_FILES = (
    Path(
        "data/processed/multicity/portable_predictors/components/"
        "predictors_static_calendar_daymet.parquet"
    ),
    Path(
        "data/processed/multicity/portable_predictors/components/"
        "COMPONENTS_COMPLETE.json"
    ),
)

DIRECTORY_COPIES = (
    (Path("src"), Path("src")),
    (Path("scripts"), Path("scripts")),
    (Path("configs"), Path("configs")),
    (Path("manifests"), Path("manifests")),
    (
        Path("data/processed/multicity/missing_support_calibration_evidence_v1"),
        Path("data/processed/multicity/missing_support_calibration_evidence_v1"),
    ),
    (
        Path("data/processed/multicity/portable_predictors/inventory"),
        Path("data/processed/multicity/portable_predictors/inventory"),
    ),
    (
        Path("data/processed/multicity/portable_predictors/sentinel_inventory"),
        Path("data/processed/multicity/portable_predictors/sentinel_inventory"),
    ),
    (
        Path("data/raw/multicity/portable_predictors/sentinel_stac"),
        Path("data/raw/multicity/portable_predictors/sentinel_stac"),
    ),
    (
        Path("data/raw/sentinel/product_metadata"),
        Path(
            "data/raw/multicity/portable_predictors/"
            "sentinel_product_metadata/los_angeles_ca"
        ),
    ),
    (
        Path("data/processed/multicity/phoenix_az/portable_source_footprint"),
        Path("data/processed/multicity/phoenix_az/portable_source_footprint"),
    ),
    (
        Path("data/processed/multicity/houston_tx/source_footprints"),
        Path("data/processed/multicity/houston_tx/source_footprints"),
    ),
    (
        Path("data/processed/multicity/chicago_il/source_footprints"),
        Path("data/processed/multicity/chicago_il/source_footprints"),
    ),
)

IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-sentinel",
    "__pycache__",
    "locks",
    "tmp",
}


class PortableBundleError(RuntimeError):
    """Raised when a complete, runnable bundle cannot be assembled."""


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ignored_transient_names(names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_DIRECTORY_NAMES
        or name.endswith((".pyc", ".pyo", ".lock", ".tmp"))
    }


def _ignore_names(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if any(secret in name.lower() for secret in ("token", "credential", "cookie"))
    }
    return ignored | _ignored_transient_names(names)


def _ignore_runtime_names(_directory: str, names: list[str]) -> set[str]:
    return _ignored_transient_names(names)


def _copy_tree(
    source: Path,
    target: Path,
    *,
    required: bool = True,
    preserve_standard_library_names: bool = False,
) -> None:
    if not source.is_dir():
        if required:
            raise PortableBundleError(f"Required directory is missing: {source}")
        return
    ignore = _ignore_runtime_names if preserve_standard_library_names else _ignore_names
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)


def _copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise PortableBundleError(f"Required file is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _file_records(root: Path) -> tuple[FileRecord, ...]:
    records: list[FileRecord] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        records.append(FileRecord(path=relative, bytes=path.stat().st_size, sha256=_sha256(path)))
    return tuple(records)


def _prepare_destination(destination: Path, *, replace: bool) -> Path | None:
    if not destination.exists():
        return None
    if not replace:
        raise PortableBundleError(
            f"Destination already exists: {destination}. Use --replace to preserve it as a backup."
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = destination.with_name(f"{destination.name}.backup-{stamp}")
    if backup.exists():
        raise PortableBundleError(f"Backup destination already exists: {backup}")
    destination.rename(backup)
    return backup


def create_bundle(
    project_root: Path,
    destination: Path,
    runtime_source: Path,
    *,
    replace: bool = False,
    allow_missing_engine: bool = False,
) -> dict[str, object]:
    """Create the bundle and return its machine-readable manifest."""

    root = project_root.resolve()
    output = destination.resolve()
    runtime = runtime_source.resolve()

    missing_program_files = [
        relative.as_posix()
        for relative in EXPECTED_PROGRAM_FILES
        if not (root / relative).is_file()
    ]
    if missing_program_files and not allow_missing_engine:
        raise PortableBundleError(
            "Sentinel engine is not ready; missing: " + ", ".join(missing_program_files)
        )
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            raise PortableBundleError(f"Required frozen input is missing: {relative.as_posix()}")

    runtime_directory = runtime / "runtime"
    requirements = runtime / "portable_requirements_lock.txt"
    python_version = runtime / "portable_python_version.txt"
    for required_runtime_path in (
        runtime_directory / "python/python.exe",
        runtime_directory / "wheelhouse",
        requirements,
        python_version,
    ):
        if not required_runtime_path.exists():
            raise PortableBundleError(f"Portable runtime input is missing: {required_runtime_path}")

    output.parent.mkdir(parents=True, exist_ok=True)
    backup = _prepare_destination(output, replace=replace)
    staging = output.with_name(f".{output.name}.building-{uuid.uuid4().hex}")
    if staging.exists():
        raise PortableBundleError(f"Unexpected staging path already exists: {staging}")
    staging.mkdir(parents=True)

    try:
        _copy_tree(
            runtime_directory,
            staging / "runtime",
            preserve_standard_library_names=True,
        )
        _copy_file(requirements, staging / "portable_requirements_lock.txt")
        _copy_file(python_version, staging / "portable_python_version.txt")
        _copy_file(root / "pyproject.toml", staging / "pyproject.toml")

        template_root = root / "portable_sentinel_templates"
        for name in (
            "START_HERE.cmd",
            "PACKAGE_RESULTS.cmd",
            "README_CN.txt",
            "setup_and_launch.ps1",
            "package_results.ps1",
        ):
            _copy_file(template_root / name, staging / "portable_sentinel_templates" / name)
        for name in ("START_HERE.cmd", "PACKAGE_RESULTS.cmd", "README_CN.txt"):
            _copy_file(template_root / name, staging / name)

        for source_relative, target_relative in DIRECTORY_COPIES:
            _copy_tree(root / source_relative, staging / target_relative)
        for relative in REQUIRED_FILES:
            _copy_file(root / relative, staging / relative)

        records = _file_records(staging)
        manifest: dict[str, object] = {
            "schema_version": 1,
            "bundle_name": "GAMING_LAPTOP_SENTINEL",
            "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "engine_ready": not missing_program_files,
            "missing_program_files": missing_program_files,
            "dashboard_url": "http://127.0.0.1:8769/",
            "long_computation_started": False,
            "file_count": len(records),
            "total_bytes": sum(record.bytes for record in records),
            "files": [
                {"path": record.path, "bytes": record.bytes, "sha256": record.sha256}
                for record in records
            ],
        }
        manifest_path = staging / "PORTABLE_BUNDLE_MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.rename(output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        if backup is not None and not output.exists() and backup.exists():
            backup.rename(output)
        raise

    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--runtime-source", type=Path, default=DEFAULT_RUNTIME_SOURCE)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Move an existing destination to a timestamped backup before rebuilding.",
    )
    parser.add_argument(
        "--allow-missing-engine",
        action="store_true",
        help="Create a non-runnable preview bundle while engine files are still being implemented.",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        manifest = create_bundle(
            arguments.project_root,
            arguments.destination,
            arguments.runtime_source,
            replace=arguments.replace,
            allow_missing_engine=arguments.allow_missing_engine,
        )
    except PortableBundleError as error:
        print(f"Bundle not created: {error}", file=sys.stderr)
        return 2
    size_mib = int(manifest["total_bytes"]) / (1024 * 1024)
    print(f"Portable folder: {arguments.destination.resolve()}")
    print(f"Files: {manifest['file_count']}; size: {size_mib:.1f} MiB")
    print(f"Engine ready: {manifest['engine_ready']}")
    print("Long Sentinel computation was not started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
