"""Create a resumable M3 source-development transfer folder.

The packager is intentionally data-only: it mirrors authenticated inputs,
durable checkpoints, and completed cache/output files at their project-relative
paths.  It refuses to run unless every queue is paused and no task owns a lease,
checkpoints SQLite before copying, and rejects persisted credentials or signed
URLs.  The resulting folder is overlaid onto the same repository checkout on
the destination computer; it is not a standalone program or an authorization.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.m3_source_development_runtime import (
    DEFAULT_CONFIG,
    RunnerSettings,
    load_runner_settings,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-development-transfer-v1"
DEFAULT_EXPORT_ROOT: Final = Path("exports/M3_SOURCE_DEVELOPMENT_OFFICE")
MANIFEST_FILENAME: Final = "M3_SOURCE_DEVELOPMENT_TRANSFER_MANIFEST.json"

_SAFE_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SENSITIVE_QUERY = re.compile(
    rb"(?i)(?:access_token|token|sig|signature|x-amz-signature|"
    rb"x-amz-credential|x-amz-security-token|se|sp|sr|sv)="
)
_BEARER = re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~-]+")
_JWT = re.compile(rb"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")
_TEXT_SUFFIXES: Final = frozenset(
    {".json", ".toml", ".txt", ".csv", ".tsv", ".md", ".yaml", ".yml"}
)


class M3SourceMigrationError(RuntimeError):
    """Raised when a safe, resumable transfer snapshot cannot be made."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _inside(root: Path, path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise M3SourceMigrationError(f"{label} must stay inside the project.")
    return resolved


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourceMigrationError(f"{label} is unreadable: {path}") from error
    if not isinstance(payload, dict):
        raise M3SourceMigrationError(f"{label} must be a JSON object: {path}")
    return payload


def _assert_safe_bytes(value: bytes, *, label: str) -> None:
    if _SENSITIVE_QUERY.search(value) or _BEARER.search(value) or _JWT.search(value):
        raise M3SourceMigrationError(
            f"Transfer blocked because {label} contains a credential or signed URL."
        )


def _assert_safe_file(path: Path, *, relative: str) -> None:
    lowered_parts = {part.casefold() for part in Path(relative).parts}
    if any(
        part == ".env"
        or part.startswith(".env.")
        or any(word in part for word in ("earthdata_token", "access_token", "credential"))
        for part in lowered_parts
    ):
        raise M3SourceMigrationError(
            f"Transfer blocked because a sensitive path was found: {relative}"
        )
    if path.suffix.casefold() in _TEXT_SUFFIXES:
        _assert_safe_bytes(path.read_bytes(), label=relative)


def _queue_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        runs = connection.execute(
            "SELECT run_id, desired_state FROM model_runs ORDER BY run_id"
        ).fetchall()
        active = connection.execute(
            """
            SELECT COUNT(*)
            FROM model_run_tasks
            WHERE status = 'running'
               OR lease_owner IS NOT NULL
               OR lease_expires_at IS NOT NULL
            """
        ).fetchone()
    except sqlite3.DatabaseError as error:
        raise M3SourceMigrationError("The runtime SQLite schema is unreadable.") from error
    if not runs:
        raise M3SourceMigrationError("No initialized source-development run exists to move.")
    if any(str(state) != "paused" for _, state in runs):
        raise M3SourceMigrationError(
            "Safe pause is incomplete: a SQLite run still requests execution."
        )
    active_count = int(active[0]) if active is not None else 0
    if active_count:
        raise M3SourceMigrationError(
            "Safe pause is incomplete: a task is running or still owns a lease."
        )
    return {
        "run_ids": [str(run_id) for run_id, _ in runs],
        "all_desired_states": "paused",
        "running_or_leased_task_count": active_count,
    }


def _checkpoint_sqlite(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise M3SourceMigrationError(f"Runtime SQLite checkpoint is missing: {path}")
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        before = _queue_snapshot(connection)
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise M3SourceMigrationError(
                "SQLite WAL checkpoint is busy; finish safe pause and try again."
            )
        after = _queue_snapshot(connection)
    finally:
        connection.close()
    if before != after:
        raise M3SourceMigrationError("Queue state changed while SQLite was checkpointed.")
    wal = Path(f"{path}-wal")
    if wal.exists() and wal.stat().st_size:
        raise M3SourceMigrationError("SQLite WAL is still non-empty after checkpoint.")
    return {
        **after,
        "wal_checkpoint_mode": "TRUNCATE",
        "wal_busy": int(checkpoint[0]),
        "wal_frames": int(checkpoint[1]),
        "wal_checkpointed_frames": int(checkpoint[2]),
    }


def _assert_database_has_no_secrets(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT payload_json, result_json, error_type
            FROM model_run_tasks
            """
        )
        for index, row in enumerate(rows, start=1):
            for value in row:
                if value is not None:
                    _assert_safe_bytes(
                        str(value).encode("utf-8"),
                        label=f"runtime SQLite task row {index}",
                    )
    finally:
        connection.close()


def _control_is_paused(settings: RunnerSettings) -> None:
    if not settings.control.is_file():
        return
    control = _read_json(settings.control, label="Dashboard control")
    if control.get("desired_state") != "paused":
        raise M3SourceMigrationError(
            "Request Safe Pause in the dashboard and wait for zero active tasks."
        )


def _source_files(settings: RunnerSettings) -> list[tuple[str, Path]]:
    root = settings.root
    required = (
        settings.config_path,
        settings.protocol_lock,
        settings.amendment,
        settings.inventory,
        settings.authorization,
        settings.database,
    )
    for path in required:
        if not path.is_file():
            raise M3SourceMigrationError(f"Required transfer input is missing: {path}")
    files: dict[str, Path] = {}

    def add(path: Path) -> None:
        resolved = _inside(root, path, label="Transfer source")
        if resolved.is_symlink():
            raise M3SourceMigrationError(f"Transfer source may not be a symlink: {resolved}")
        if not resolved.is_file():
            return
        relative = resolved.relative_to(root).as_posix()
        if relative.endswith(("-wal", "-shm")) or "/.tmp/" in f"/{relative}/":
            return
        _assert_safe_file(resolved, relative=relative)
        files[relative] = resolved

    for path in required:
        add(path)
    for path in (settings.control, settings.status):
        add(path)
    for directory in (
        settings.cache_root,
        settings.qa_output_root,
        settings.completion_root,
    ):
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise M3SourceMigrationError(f"Transfer directory is invalid: {directory}")
        for path in sorted(directory.rglob("*")):
            add(path)
    return sorted(files.items())


def _package_name(value: str | None) -> str:
    if value is None:
        value = "M3_SOURCE_TRANSFER_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if _SAFE_PACKAGE_NAME.fullmatch(value) is None:
        raise M3SourceMigrationError("Package name contains unsafe characters.")
    return value


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _publish_directory(temporary: Path, destination: Path) -> None:
    """Rename a completed folder, tolerating short-lived Windows file locks."""

    for attempt in range(10):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def create_transfer_folder(
    project_root: str | Path,
    *,
    output_root: str | Path = DEFAULT_EXPORT_ROOT,
    package_name: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Checkpoint and mirror one paused source-development runtime."""

    settings = load_runner_settings(project_root, config_path)
    root = settings.root
    _control_is_paused(settings)
    queue = _checkpoint_sqlite(settings.database)
    _assert_database_has_no_secrets(settings.database)
    files = _source_files(settings)
    output = Path(output_root)
    output = output.resolve() if output.is_absolute() else (root / output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = _package_name(package_name)
    destination = output / name
    temporary = output / f".{name}.building-{os.getpid()}"
    if destination.exists() or temporary.exists():
        raise M3SourceMigrationError(f"Transfer destination already exists: {destination}")
    temporary.mkdir()
    records: list[dict[str, Any]] = []
    try:
        for relative, source in files:
            target = temporary / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            source_hash = sha256_file(source)
            shutil.copy2(source, target)
            copied_hash = sha256_file(target)
            if copied_hash != source_hash:
                raise M3SourceMigrationError(f"Copied file hash changed: {relative}")
            records.append(
                {
                    "relative_path": relative,
                    "bytes": target.stat().st_size,
                    "sha256": copied_hash,
                }
            )
        _control_is_paused(settings)
        post_copy_queue = _checkpoint_sqlite(settings.database)
        if post_copy_queue["run_ids"] != queue["run_ids"]:
            raise M3SourceMigrationError("Queue identity changed during packaging.")
        database_record = next(
            record
            for record in records
            if record["relative_path"]
            == settings.database.relative_to(root).as_posix()
        )
        if sha256_file(settings.database) != database_record["sha256"]:
            raise M3SourceMigrationError("Runtime SQLite changed during packaging.")
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "paused_transfer_complete",
            "created_at_utc": _utc_now(),
            "restore_mode": "overlay_exact_relative_paths_into_same_protocol_checkout",
            "execution_authorization_created_by_packager": False,
            "credentials_tokens_cookies_or_signed_urls_included": False,
            "sqlite_checkpoint": queue,
            "file_count": len(records),
            "total_bytes": sum(int(record["bytes"]) for record in records),
            "files": records,
        }
        manifest["commit_sha256"] = canonical_sha256(manifest)
        _write_manifest(temporary / MANIFEST_FILENAME, manifest)
        _publish_directory(temporary, destination)
    except Exception:
        if temporary.is_dir() and temporary.parent == output:
            shutil.rmtree(temporary)
        raise
    return {
        **manifest,
        "transfer_directory": str(destination),
        "manifest_path": str(destination / MANIFEST_FILENAME),
    }


def verify_transfer_folder(path: str | Path) -> dict[str, Any]:
    """Verify the transfer manifest and every copied file without restoring it."""

    root = Path(path).resolve()
    manifest = _read_json(root / MANIFEST_FILENAME, label="Transfer manifest")
    recorded = manifest.get("commit_sha256")
    unsigned = dict(manifest)
    unsigned.pop("commit_sha256", None)
    if recorded != canonical_sha256(unsigned):
        raise M3SourceMigrationError("Transfer manifest commit is invalid.")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("algorithm_version") != ALGORITHM_VERSION
        or manifest.get("state") != "paused_transfer_complete"
        or manifest.get("execution_authorization_created_by_packager") is not False
        or manifest.get("credentials_tokens_cookies_or_signed_urls_included") is not False
    ):
        raise M3SourceMigrationError("Transfer manifest contract changed.")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise M3SourceMigrationError("Transfer manifest has no file list.")
    seen: set[str] = set()
    for value in rows:
        if not isinstance(value, Mapping):
            raise M3SourceMigrationError("Transfer file record is invalid.")
        relative = str(value.get("relative_path", ""))
        if not relative or relative in seen:
            raise M3SourceMigrationError("Transfer file paths are empty or duplicated.")
        seen.add(relative)
        path_value = _inside(root, root / Path(relative), label="Transfer file")
        if (
            not path_value.is_file()
            or path_value.stat().st_size != value.get("bytes")
            or sha256_file(path_value) != value.get("sha256")
        ):
            raise M3SourceMigrationError(f"Transfer file failed verification: {relative}")
        _assert_safe_file(path_value, relative=relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_FILENAME
    }
    if actual != seen:
        raise M3SourceMigrationError("Transfer folder has missing or unexpected files.")
    if manifest.get("file_count") != len(rows) or manifest.get("total_bytes") != sum(
        int(row["bytes"]) for row in rows
    ):
        raise M3SourceMigrationError("Transfer manifest counts changed.")
    return manifest


def transfer_relative_paths(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the ordered project-relative paths declared by a manifest."""

    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise M3SourceMigrationError("Transfer manifest file list is invalid.")
    return tuple(str(row["relative_path"]) for row in rows if isinstance(row, Mapping))
