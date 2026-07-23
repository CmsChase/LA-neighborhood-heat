"""Fail-closed verification and import of returned grouped-model results.

The return source may be either an extracted ``ISEF_model_results`` package or
a complete portable-project directory such as ``exports/FINAL_RESULT``.  No
returned artifact is published until the terminal queue, compiled outputs, and
every outer fragment lock have been authenticated in staging.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Final
from zipfile import BadZipFile, ZipFile, ZipInfo

import numpy as np
import pandas as pd

from la_heat.model_run_compile import (
    COMPILE_PROVENANCE_FILENAME,
    FOLD_METRIC_COLUMNS,
    FOLD_METRICS_FILENAME,
    MODEL_RUN_COMPILE_ALGORITHM_VERSION,
    MODEL_RUN_COMPILE_SCHEMA_VERSION,
    OOF_PREDICTIONS_FILENAME,
    PER_DATE_METRIC_COLUMNS,
    PER_DATE_METRICS_FILENAME,
    SUMMARY_METRIC_COLUMNS,
    SUMMARY_METRICS_FILENAME,
)
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

RETURNED_RESULT_IMPORT_SCHEMA_VERSION: Final = 1
RETURNED_RESULT_IMPORT_ALGORITHM_VERSION: Final = "returned-model-result-import-v1"

EXPECTED_RUN_TASK_COUNT = 57_800
EXPECTED_INNER_TASK_COUNT = 55_645
EXPECTED_OUTER_TASK_COUNT = 2_155
EXPECTED_CONTEXT_ROW_COUNT = 63_403
EXPECTED_INDEPENDENT_DATE_COUNT = 65
EXPECTED_FAMILY_COUNT = 3
EXPECTED_MODEL_COUNT = 5
EXPECTED_OOF_ROW_COUNT = 951_045
EXPECTED_SUMMARY_ROW_COUNT = 15
EXPECTED_PER_DATE_ROW_COUNT = 975
EXPECTED_FOLD_METRIC_ROW_COUNT = 2_155

MODEL_EVALUATION_FILES: Final = (
    OOF_PREDICTIONS_FILENAME,
    SUMMARY_METRICS_FILENAME,
    PER_DATE_METRICS_FILENAME,
    FOLD_METRICS_FILENAME,
    COMPILE_PROVENANCE_FILENAME,
)
SOURCE_OWNERSHIP_PATH: Final = Path(
    "data/interim/model_runs/transfer_ownership.json"
)
SOURCE_QUEUE_PATH: Final = Path("data/interim/model_runs/model_tasks.sqlite3")
SOURCE_DISABLED_MARKER_PATH: Final = Path("RUN_DISABLED_TRANSFERRED_OUT.txt")
RETURNED_STATUS_PATH: Final = Path("data/interim/model_runs/status.json")
RETURNED_QUEUE_PATH: Final = Path("data/interim/model_runs/model_tasks.sqlite3")
RETURNED_EVALUATION_PATH: Final = Path("data/processed/model_evaluation")
MAX_RETURN_ARCHIVE_UNCOMPRESSED_BYTES: Final = 2_000_000_000


class ReturnedResultImportError(ValueError):
    """Raised when a returned result cannot be authenticated or safely imported."""


@dataclass(frozen=True, slots=True)
class ReturnedResultImportSummary:
    """Concise result of a verification-only or completed import operation."""

    state: str
    run_id: str
    returned_root: Path
    verified_existing_fragment_count: int
    reconstructed_fragment_count: int
    imported: bool
    audit_path: Path | None
    audit_commit_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "run_id": self.run_id,
            "returned_root": str(self.returned_root),
            "verified_existing_fragment_count": (
                self.verified_existing_fragment_count
            ),
            "reconstructed_fragment_count": self.reconstructed_fragment_count,
            "imported": self.imported,
            "audit_path": None if self.audit_path is None else str(self.audit_path),
            "audit_commit_sha256": self.audit_commit_sha256,
        }


@dataclass(frozen=True, slots=True)
class _QueueAudit:
    run_id: str
    desired_state: str
    task_counts: dict[str, dict[str, int]]
    fragment_locks: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _CompiledOutputs:
    provenance: dict[str, Any]
    provenance_sha256: str
    oof: pd.DataFrame
    output_records: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _ZipInventory:
    archive_root: str
    files: dict[str, ZipInfo]
    member_count: int
    uncompressed_bytes: int
    compressed_bytes: int


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _hex_sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReturnedResultImportError(f"{label} must be a lowercase SHA-256 digest.")
    return value


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReturnedResultImportError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"{label} changed while being read: {path}")
    if not isinstance(payload, dict):
        raise ReturnedResultImportError(f"{label} must be a JSON object.")
    return payload, before


def _safe_archive_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReturnedResultImportError(f"{label} must be a nonempty POSIX path.")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise ReturnedResultImportError(f"{label} is unsafe.")
    raw_parts = value.split("/")
    if any(
        not part or part in {".", ".."} or ":" in part for part in raw_parts
    ):
        raise ReturnedResultImportError(f"{label} is unsafe.")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value or PurePosixPath(value).is_absolute():
        raise ReturnedResultImportError(f"{label} is not canonical.")
    return normalized


def _inventory_zip(archive: ZipFile) -> _ZipInventory:
    roots: set[str] = set()
    files: dict[str, ZipInfo] = {}
    seen: set[str] = set()
    uncompressed_bytes = 0
    compressed_bytes = 0
    infos = archive.infolist()
    if not infos:
        raise ReturnedResultImportError("Returned ZIP is empty.")
    for info in infos:
        name = info.filename
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise ReturnedResultImportError("Returned ZIP contains an unsafe member name.")
        is_directory = info.is_dir()
        trimmed = name[:-1] if is_directory and name.endswith("/") else name
        if not trimmed or trimmed.startswith("/"):
            raise ReturnedResultImportError("Returned ZIP contains an unsafe member name.")
        parts = trimmed.split("/")
        if any(
            not part or part in {".", ".."} or ":" in part for part in parts
        ):
            raise ReturnedResultImportError("Returned ZIP contains an unsafe member name.")
        normalized = "/".join(parts)
        if PurePosixPath(normalized).as_posix() != normalized:
            raise ReturnedResultImportError(
                "Returned ZIP contains a noncanonical member name."
            )
        casefolded = normalized.casefold()
        if casefolded in seen:
            raise ReturnedResultImportError(
                "Returned ZIP contains duplicate or case-colliding members."
            )
        seen.add(casefolded)
        roots.add(parts[0])
        if info.flag_bits & 0x1:
            raise ReturnedResultImportError("Returned ZIP contains encrypted members.")
        unix_type = (info.external_attr >> 16) & 0o170000
        if unix_type not in {0, 0o040000, 0o100000}:
            raise ReturnedResultImportError(
                "Returned ZIP contains a link or special-file member."
            )
        if is_directory:
            if unix_type == 0o100000:
                raise ReturnedResultImportError(
                    "Returned ZIP directory metadata is inconsistent."
                )
        else:
            if len(parts) < 2 or unix_type == 0o040000:
                raise ReturnedResultImportError(
                    "Returned ZIP does not have one enclosing root directory."
                )
            relative = "/".join(parts[1:])
            files[relative] = info
        uncompressed_bytes += int(info.file_size)
        compressed_bytes += int(info.compress_size)
        if uncompressed_bytes > MAX_RETURN_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ReturnedResultImportError(
                "Returned ZIP exceeds the uncompressed-size safety limit."
            )
    if len(roots) != 1:
        raise ReturnedResultImportError(
            "Returned ZIP must contain exactly one enclosing root directory."
        )
    archive_root = next(iter(roots))
    if not files:
        raise ReturnedResultImportError("Returned ZIP contains no files.")
    return _ZipInventory(
        archive_root=archive_root,
        files=files,
        member_count=len(infos),
        uncompressed_bytes=uncompressed_bytes,
        compressed_bytes=compressed_bytes,
    )


def _read_zip_json(
    archive: ZipFile,
    inventory: _ZipInventory,
    relative: str,
    *,
    label: str,
) -> dict[str, Any]:
    info = inventory.files.get(relative)
    if info is None:
        raise FileNotFoundError(f"{inventory.archive_root}/{relative}")
    if info.file_size > 64 * 1024 * 1024:
        raise ReturnedResultImportError(f"{label} is unexpectedly large.")
    try:
        with archive.open(info, "r") as source:
            payload = json.loads(source.read().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReturnedResultImportError(f"Cannot read {label} from ZIP.") from error
    if not isinstance(payload, dict):
        raise ReturnedResultImportError(f"{label} must be a JSON object.")
    return payload


def _zip_member_sha256(archive: ZipFile, info: ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _binary_stream_sha256(source: BinaryIO) -> tuple[str, int]:
    source.seek(0)
    digest = hashlib.sha256()
    byte_count = 0
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        byte_count += len(chunk)
    source.seek(0)
    return digest.hexdigest(), byte_count


def _validate_zip_file_records(
    archive: ZipFile,
    inventory: _ZipInventory,
    records: object,
    *,
    label: str,
    require_nonempty: bool,
    exact_fields: bool = False,
) -> dict[str, int]:
    if not isinstance(records, list) or (require_nonempty and not records):
        raise ReturnedResultImportError(f"{label} file locks are invalid.")
    seen: set[str] = set()
    total_bytes = 0
    for index, value in enumerate(records):
        if not isinstance(value, dict):
            raise ReturnedResultImportError(f"{label} file lock {index} is invalid.")
        if exact_fields and set(value) != {"path", "bytes", "sha256"}:
            raise ReturnedResultImportError(
                f"{label} file lock {index} schema is invalid."
            )
        relative = _safe_archive_relative_path(
            value.get("path"), label=f"{label} file lock {index} path"
        )
        casefolded = relative.casefold()
        if casefolded in seen:
            raise ReturnedResultImportError(f"{label} contains duplicate file locks.")
        seen.add(casefolded)
        try:
            expected_bytes = int(value["bytes"])
            expected_sha = _hex_sha256(
                value["sha256"], label=f"{label} file lock {index} SHA-256"
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ReturnedResultImportError(
                f"{label} file lock {index} is incomplete."
            ) from error
        info = inventory.files.get(relative)
        if (
            expected_bytes < 0
            or info is None
            or int(info.file_size) != expected_bytes
            or _zip_member_sha256(archive, info) != expected_sha
        ):
            raise ReturnedResultImportError(
                f"{label} file lock failed: {relative}"
            )
        total_bytes += expected_bytes
    return {"file_count": len(records), "total_bytes": total_bytes}


def _result_manifest_records(payload: dict[str, Any]) -> object:
    if "commit_sha256" in payload:
        _verify_canonical_commit(payload, label="Returned result manifest")
    for key in ("files", "file_records", "artifacts", "immutable_files"):
        if key in payload:
            return payload[key]
    raise ReturnedResultImportError(
        "Returned result manifest does not declare a supported file-lock list."
    )


def _zip_required_files(
    archive: ZipFile,
    inventory: _ZipInventory,
) -> set[str]:
    status = _read_zip_json(
        archive,
        inventory,
        RETURNED_STATUS_PATH.as_posix(),
        label="returned model status",
    )
    run_id = _hex_sha256(status.get("run_id"), label="returned run ID")
    provenance_relative = (
        RETURNED_EVALUATION_PATH / COMPILE_PROVENANCE_FILENAME
    ).as_posix()
    provenance = _read_zip_json(
        archive,
        inventory,
        provenance_relative,
        label="model-run compile provenance",
    )
    fragment_records = provenance.get("input_fragments")
    if (
        not isinstance(fragment_records, list)
        or len(fragment_records) != EXPECTED_OUTER_TASK_COUNT
    ):
        raise ReturnedResultImportError(
            "ZIP compile provenance lacks the exact outer-fragment lock count."
        )
    run_base = f"data/interim/model_runs/runs/{run_id}"
    required = {
        "transfer_authority.json",
        "portable_bundle_manifest.json",
        "portable_relocation.json",
        RETURNED_STATUS_PATH.as_posix(),
        RETURNED_QUEUE_PATH.as_posix(),
        f"{run_base}/run_manifest.json",
        f"{run_base}/outer_selections.json",
    }
    required.update(
        (RETURNED_EVALUATION_PATH / name).as_posix()
        for name in MODEL_EVALUATION_FILES
    )
    fragment_paths: set[str] = set()
    for index, value in enumerate(fragment_records):
        if not isinstance(value, dict):
            raise ReturnedResultImportError(
                f"ZIP fragment lock {index} is not a JSON object."
            )
        fragment_relative = _safe_archive_relative_path(
            value.get("path"), label=f"ZIP fragment lock {index} path"
        )
        task_id = value.get("task_id")
        if (
            not isinstance(task_id, str)
            or fragment_relative != f"outer_fragments/{task_id}.parquet"
        ):
            raise ReturnedResultImportError(
                f"ZIP fragment lock {index} path/task identity is invalid."
            )
        fragment_paths.add(f"{run_base}/{fragment_relative}")
    if len(fragment_paths) != EXPECTED_OUTER_TASK_COUNT:
        raise ReturnedResultImportError("ZIP fragment paths are not unique and exact.")
    required.update(fragment_paths)
    if "result_manifest.json" in inventory.files:
        required.add("result_manifest.json")
    missing = sorted(required - inventory.files.keys())
    if missing:
        raise ReturnedResultImportError(
            f"Returned ZIP lacks required result files: {missing[:3]}"
        )
    return required


def _extract_zip_files(
    archive: ZipFile,
    inventory: _ZipInventory,
    relative_paths: set[str],
    destination_root: Path,
) -> None:
    for relative in sorted(relative_paths):
        info = inventory.files[relative]
        destination = _safe_child(
            destination_root, relative, label="ZIP extraction path"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, destination.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        if destination.stat().st_size != info.file_size:
            raise ReturnedResultImportError(
                f"ZIP extraction size changed: {relative}"
            )


def _materialize_returned_zip(
    archive_path: Path,
    destination_root: Path,
    *,
    expected_archive_sha256: str | None,
) -> dict[str, Any]:
    if expected_archive_sha256 is None:
        raise ReturnedResultImportError(
            "A ZIP return requires --expected-archive-sha256."
        )
    expected_sha = _hex_sha256(
        expected_archive_sha256, label="expected returned ZIP SHA-256"
    )
    try:
        with archive_path.open("rb") as archive_stream:
            actual_sha, archive_bytes = _binary_stream_sha256(archive_stream)
            if actual_sha != expected_sha:
                raise ReturnedResultImportError(
                    "Returned ZIP external SHA-256 mismatch."
                )
            with ZipFile(archive_stream, "r") as archive:
                inventory = _inventory_zip(archive)
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise ReturnedResultImportError(
                        f"Returned ZIP CRC check failed: {bad_member}"
                    )
                bundle = _read_zip_json(
                    archive,
                    inventory,
                    "portable_bundle_manifest.json",
                    label="portable bundle manifest",
                )
                if (
                    bundle.get("schema_version") != 1
                    or bundle.get("relocation_manifest")
                    != "portable_relocation.json"
                ):
                    raise ReturnedResultImportError(
                        "Portable bundle manifest header is invalid."
                    )
                portable_records = _validate_zip_file_records(
                    archive,
                    inventory,
                    bundle.get("immutable_files"),
                    label="Portable bundle manifest",
                    require_nonempty=True,
                    exact_fields=True,
                )
                result_manifest_present = "result_manifest.json" in inventory.files
                result_records: dict[str, int] | None = None
                if result_manifest_present:
                    result_manifest = _read_zip_json(
                        archive,
                        inventory,
                        "result_manifest.json",
                        label="returned result manifest",
                    )
                    result_records = _validate_zip_file_records(
                        archive,
                        inventory,
                        _result_manifest_records(result_manifest),
                        label="Returned result manifest",
                        require_nonempty=True,
                    )
                required = _zip_required_files(archive, inventory)
                destination_root.mkdir(parents=True)
                _extract_zip_files(archive, inventory, required, destination_root)
            after_sha, after_bytes = _binary_stream_sha256(archive_stream)
            if after_sha != actual_sha or after_bytes != archive_bytes:
                raise RuntimeError("Returned ZIP changed while being authenticated.")
    except (BadZipFile, NotImplementedError) as error:
        raise ReturnedResultImportError("Returned archive is not a valid ZIP.") from error
    return {
        "kind": "zip",
        "path": str(archive_path),
        "sha256": actual_sha,
        "bytes": archive_bytes,
        "crc_check": "ok",
        "member_count": inventory.member_count,
        "uncompressed_bytes": inventory.uncompressed_bytes,
        "compressed_bytes": inventory.compressed_bytes,
        "archive_root": inventory.archive_root,
        "required_files_extracted": len(required),
        "portable_immutable_files": portable_records,
        "result_manifest_present": result_manifest_present,
        "result_manifest_files": result_records,
    }


def _verify_canonical_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise ReturnedResultImportError(f"{label} canonical commit is invalid.")
    return recorded


def _safe_child(root: Path, relative: str | Path, *, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReturnedResultImportError(f"{label} relative path is unsafe.")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ReturnedResultImportError(f"{label} escapes its declared root.") from error
    return resolved


def _locked_file_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _verify_byte_lock(path: Path, record: dict[str, Any], *, label: str) -> None:
    try:
        expected_sha = _hex_sha256(record["sha256"], label=f"{label} SHA-256")
        expected_bytes = int(record["bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReturnedResultImportError(f"{label} byte lock is incomplete.") from error
    if (
        expected_bytes < 0
        or not path.is_file()
        or path.stat().st_size != expected_bytes
        or sha256_file(path) != expected_sha
    ):
        raise ReturnedResultImportError(f"{label} byte lock failed.")


def _tree_record(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_sha256": canonical_sha256(rows),
    }


def _source_guard_records(project_root: Path) -> dict[str, dict[str, Any]]:
    required = {
        "source_queue": project_root / SOURCE_QUEUE_PATH,
        "transfer_ownership": project_root / SOURCE_OWNERSHIP_PATH,
        "transferred_out_marker": project_root / SOURCE_DISABLED_MARKER_PATH,
        "research_config": project_root / "configs/research.toml",
        "model_selection_config": project_root / "configs/model_selection.toml",
    }
    return {name: _locked_file_record(path) for name, path in required.items()}


def _validate_final_test_locks(project_root: Path) -> None:
    for relative in ("configs/research.toml", "configs/model_selection.toml"):
        path = project_root / relative
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ReturnedResultImportError(f"Cannot parse final-test lock: {path}") from error
        section = payload.get("study") if relative.endswith("research.toml") else payload
        if not isinstance(section, dict):
            raise ReturnedResultImportError(f"Final-test lock section is missing: {path}")
        if section.get("final_test_year") != 2025 or section.get(
            "unlock_final_test"
        ) is not False:
            raise PermissionError(f"Final-test year 2025 is not locked in {path}.")


def _validate_transfer_authority(
    returned_root: Path,
    project_root: Path,
) -> dict[str, Any]:
    authority, _ = _read_json(
        returned_root / "transfer_authority.json",
        label="returned transfer authority",
    )
    ownership, _ = _read_json(
        project_root / SOURCE_OWNERSHIP_PATH,
        label="source transfer ownership",
    )
    bundle_path = returned_root / "portable_bundle_manifest.json"
    relocation_path = returned_root / "portable_relocation.json"
    relocation, _ = _read_json(relocation_path, label="portable relocation")
    relocation_commit = _verify_canonical_commit(
        relocation, label="Portable relocation"
    )
    bundle_sha = sha256_file(bundle_path)
    if (
        authority.get("state") != "target_active"
        or ownership.get("state") != "transferred_out"
        or not isinstance(authority.get("transfer_id"), str)
        or authority.get("transfer_id") != ownership.get("transfer_id")
        or authority.get("bundle_manifest_sha256") != bundle_sha
        or ownership.get("bundle_manifest_sha256") != bundle_sha
        or not (project_root / SOURCE_DISABLED_MARKER_PATH).is_file()
    ):
        raise ReturnedResultImportError(
            "Returned authority does not match the disabled source ownership."
        )
    return {
        "transfer_id": str(authority["transfer_id"]),
        "bundle_manifest_sha256": bundle_sha,
        "relocation_manifest_sha256": sha256_file(relocation_path),
        "relocation_commit_sha256": relocation_commit,
    }


def _validate_status(returned_root: Path) -> tuple[dict[str, Any], str]:
    status, status_sha = _read_json(
        returned_root / RETURNED_STATUS_PATH,
        label="returned model status",
    )
    counts = status.get("counts")
    by_kind = status.get("counts_by_kind")
    if not isinstance(counts, dict) or not isinstance(by_kind, dict):
        raise ReturnedResultImportError("Returned status lacks queue counts.")
    expected_total = {
        "pending": 0,
        "running": 0,
        "complete": EXPECTED_RUN_TASK_COUNT,
        "quarantined": 0,
        "total": EXPECTED_RUN_TASK_COUNT,
    }
    expected_kinds = {
        "inner_fit": {
            "pending": 0,
            "running": 0,
            "complete": EXPECTED_INNER_TASK_COUNT,
            "quarantined": 0,
            "total": EXPECTED_INNER_TASK_COUNT,
        },
        "outer_refit": {
            "pending": 0,
            "running": 0,
            "complete": EXPECTED_OUTER_TASK_COUNT,
            "quarantined": 0,
            "total": EXPECTED_OUTER_TASK_COUNT,
        },
    }
    _hex_sha256(status.get("run_id"), label="returned run ID")
    if (
        status.get("state") != "complete"
        or status.get("phase") != "complete"
        or int(status.get("total", -1)) != EXPECTED_RUN_TASK_COUNT
        or int(status.get("completed", -1)) != EXPECTED_RUN_TASK_COUNT
        or int(status.get("active", -1)) != 0
        or int(status.get("pending", -1)) != 0
        or int(status.get("quarantined", -1)) != 0
        or counts != expected_total
        or by_kind != expected_kinds
        or status.get("active_tasks") != []
        or status.get("error") is not None
    ):
        raise ReturnedResultImportError(
            "Returned status is not the exact clean 57,800-task terminal state."
        )
    return status, status_sha


def _validate_queue(
    queue_path: Path,
    *,
    expected_run_id: str,
) -> _QueueAudit:
    if not queue_path.is_file():
        raise FileNotFoundError(queue_path)
    uri = f"file:{queue_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if str(connection.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            raise ReturnedResultImportError("Returned SQLite integrity_check failed.")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if tables != {"model_runs", "model_run_tasks"}:
            raise ReturnedResultImportError("Returned SQLite table set is invalid.")
        runs = connection.execute(
            "SELECT run_id, desired_state FROM model_runs ORDER BY run_id"
        ).fetchall()
        if len(runs) != 1 or str(runs[0][0]) != expected_run_id:
            raise ReturnedResultImportError("Returned SQLite run identity is invalid.")
        task_rows = connection.execute(
            "SELECT kind, status, COUNT(*) FROM model_run_tasks "
            "GROUP BY kind, status ORDER BY kind, status"
        ).fetchall()
        expected_rows = [
            ("inner_fit", "complete", EXPECTED_INNER_TASK_COUNT),
            ("outer_refit", "complete", EXPECTED_OUTER_TASK_COUNT),
        ]
        if task_rows != expected_rows:
            raise ReturnedResultImportError(
                "Returned SQLite task counts are not exactly complete."
            )
        total, distinct = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT task_id) FROM model_run_tasks"
        ).fetchone()
        if int(total) != EXPECTED_RUN_TASK_COUNT or int(distinct) != int(total):
            raise ReturnedResultImportError("Returned SQLite task IDs are not exact.")
        fragment_locks: dict[str, dict[str, Any]] = {}
        outer_rows = connection.execute(
            "SELECT task_id, result_json FROM model_run_tasks "
            "WHERE kind = 'outer_refit' ORDER BY task_id"
        ).fetchall()
        for task_id_value, result_json in outer_rows:
            task_id = str(task_id_value)
            try:
                result = json.loads(str(result_json))
            except (TypeError, json.JSONDecodeError) as error:
                raise ReturnedResultImportError(
                    f"Outer queue result is unreadable: {task_id}"
                ) from error
            if (
                not isinstance(result, dict)
                or result.get("schema_version") != 2
                or result.get("kind") != "outer_refit"
                or not isinstance(result.get("fragment"), dict)
            ):
                raise ReturnedResultImportError(
                    f"Outer queue result schema is invalid: {task_id}"
                )
            fragment = dict(result["fragment"])
            required = {
                "path",
                "path_base",
                "sha256",
                "bytes",
                "rows",
                "schema_sha256",
                "semantic_sha256",
            }
            if set(fragment) != required:
                raise ReturnedResultImportError(
                    f"Outer queue fragment lock is invalid: {task_id}"
                )
            expected_path = f"outer_fragments/{task_id}.parquet"
            if (
                fragment.get("path_base") != "run_directory"
                or fragment.get("path") != expected_path
                or task_id in fragment_locks
            ):
                raise ReturnedResultImportError(
                    f"Outer queue fragment path is invalid: {task_id}"
                )
            for field in ("sha256", "schema_sha256", "semantic_sha256"):
                _hex_sha256(fragment.get(field), label=f"{task_id} {field}")
            try:
                fragment["bytes"] = int(fragment["bytes"])
                fragment["rows"] = int(fragment["rows"])
            except (TypeError, ValueError) as error:
                raise ReturnedResultImportError(
                    f"Outer queue fragment sizes are invalid: {task_id}"
                ) from error
            fragment["model_id"] = str(result.get("model_id"))
            fragment["selected_candidate_id"] = str(
                result.get("selected_candidate_id")
            )
            fragment_locks[task_id] = fragment
    finally:
        connection.close()
    if len(fragment_locks) != EXPECTED_OUTER_TASK_COUNT:
        raise ReturnedResultImportError("Returned queue lacks exact outer locks.")
    return _QueueAudit(
        run_id=expected_run_id,
        desired_state=str(runs[0][1]),
        task_counts={
            "inner_fit": {"complete": EXPECTED_INNER_TASK_COUNT},
            "outer_refit": {"complete": EXPECTED_OUTER_TASK_COUNT},
        },
        fragment_locks=fragment_locks,
    )


def _validate_run_manifests(
    returned_root: Path,
    *,
    run_id: str,
    relocation_commit_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    run_root = returned_root / "data/interim/model_runs/runs" / run_id
    run_manifest, _ = _read_json(
        run_root / "run_manifest.json", label="returned run manifest"
    )
    run_commit = _verify_canonical_commit(run_manifest, label="Returned run manifest")
    selections, selections_sha = _read_json(
        run_root / "outer_selections.json", label="returned outer selections"
    )
    selection_commit = _verify_canonical_commit(
        selections, label="Returned outer selections"
    )
    if (
        run_manifest.get("run_id") != run_id
        or run_manifest.get("portable_relocation_commit_sha256")
        != relocation_commit_sha256
        or run_manifest.get("inner_task_count") != EXPECTED_INNER_TASK_COUNT
        or run_manifest.get("outer_task_count") != EXPECTED_OUTER_TASK_COUNT
        or run_manifest.get("total_task_count") != EXPECTED_RUN_TASK_COUNT
        or run_manifest.get("final_test_year") != 2025
        or run_manifest.get("final_test_unlocked") is not False
        or selections.get("run_id") != run_id
        or selections.get("selection_count") != EXPECTED_OUTER_TASK_COUNT
        or not isinstance(selections.get("selections"), list)
        or len(selections["selections"]) != EXPECTED_OUTER_TASK_COUNT
    ):
        raise ReturnedResultImportError("Returned run manifests are inconsistent.")
    return run_root, {
        "run_manifest_sha256": sha256_file(run_root / "run_manifest.json"),
        "run_manifest_commit_sha256": run_commit,
        "outer_selections_sha256": selections_sha,
        "outer_selections_commit_sha256": selection_commit,
        "context_run_id": str(run_manifest.get("context_run_id")),
        "task_plan_sha256": str(run_manifest.get("task_plan_sha256")),
    }


def _csv_schema_sha256(frame: pd.DataFrame) -> str:
    return canonical_sha256(
        [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
    )


def _validate_csv_output(
    path: Path,
    record: dict[str, Any],
    *,
    expected_columns: tuple[str, ...],
    expected_rows: int,
    label: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    _verify_byte_lock(path, record, label=label)
    frame = pd.read_csv(path)
    if frame.columns.tolist() != list(expected_columns) or len(frame) != expected_rows:
        raise ReturnedResultImportError(f"{label} row/schema contract failed.")
    if int(record.get("rows", -1)) != expected_rows:
        raise ReturnedResultImportError(f"{label} provenance row lock failed.")
    return frame, {
        "path": path.name,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": _csv_schema_sha256(frame),
        "columns": frame.columns.tolist(),
    }


def _validate_compiled_outputs(
    returned_root: Path,
    *,
    status: dict[str, Any],
    run_metadata: dict[str, Any],
) -> _CompiledOutputs:
    evaluation_root = returned_root / RETURNED_EVALUATION_PATH
    provenance_path = evaluation_root / COMPILE_PROVENANCE_FILENAME
    provenance, provenance_sha = _read_json(
        provenance_path, label="model-run compile provenance"
    )
    provenance_commit = _verify_canonical_commit(
        provenance, label="Model-run compile provenance"
    )
    if (
        provenance.get("schema_version") != MODEL_RUN_COMPILE_SCHEMA_VERSION
        or provenance.get("algorithm_version")
        != MODEL_RUN_COMPILE_ALGORITHM_VERSION
        or provenance.get("state") != "complete"
        or provenance.get("ready_for_reporting") is not True
        or provenance.get("run_id") != status.get("run_id")
        or provenance.get("context_run_id") != run_metadata["context_run_id"]
        or provenance.get("task_plan_sha256") != run_metadata["task_plan_sha256"]
        or provenance.get("final_test_year") != 2025
        or provenance.get("final_test_locked") is not True
        or provenance.get("contains_final_test_year") is not False
        or provenance.get("context_row_count") != EXPECTED_CONTEXT_ROW_COUNT
        or provenance.get("independent_date_count")
        != EXPECTED_INDEPENDENT_DATE_COUNT
        or provenance.get("family_count") != EXPECTED_FAMILY_COUNT
        or provenance.get("model_count") != EXPECTED_MODEL_COUNT
        or provenance.get("outer_fragment_count") != EXPECTED_OUTER_TASK_COUNT
        or provenance.get("oof_prediction_row_count") != EXPECTED_OOF_ROW_COUNT
        or provenance.get("summary_metric_row_count") != EXPECTED_SUMMARY_ROW_COUNT
        or provenance.get("per_date_metric_row_count") != EXPECTED_PER_DATE_ROW_COUNT
        or provenance.get("fold_metric_row_count")
        != EXPECTED_FOLD_METRIC_ROW_COUNT
    ):
        raise ReturnedResultImportError(
            "Compile provenance is not the exact locked terminal contract."
        )
    output_locks = provenance.get("output_files")
    expected_output_names = {
        OOF_PREDICTIONS_FILENAME,
        SUMMARY_METRICS_FILENAME,
        PER_DATE_METRICS_FILENAME,
        FOLD_METRICS_FILENAME,
    }
    if not isinstance(output_locks, dict) or set(output_locks) != expected_output_names:
        raise ReturnedResultImportError("Compile output lock set is invalid.")
    for name, record in output_locks.items():
        if (
            not isinstance(record, dict)
            or record.get("path") != name
            or record.get("path_base") != "output_directory"
        ):
            raise ReturnedResultImportError(f"Compile output path lock failed: {name}")

    oof_path = evaluation_root / OOF_PREDICTIONS_FILENAME
    oof_lock = output_locks[OOF_PREDICTIONS_FILENAME]
    _verify_byte_lock(oof_path, oof_lock, label="OOF predictions")
    oof = pd.read_parquet(oof_path)
    actual_oof_record = parquet_file_record(oof_path, oof)
    for key in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual_oof_record.get(key) != oof_lock.get(key):
            raise ReturnedResultImportError(f"OOF prediction {key} lock failed.")
    if (
        tuple(oof.columns) != OUTER_PREDICTION_COLUMNS
        or len(oof) != EXPECTED_OOF_ROW_COUNT
    ):
        raise ReturnedResultImportError("OOF prediction row/schema contract failed.")
    dates = pd.to_datetime(oof["target_date"], errors="raise")
    numeric = oof[["y_true", "y_pred"]].apply(pd.to_numeric, errors="raise")
    if (
        dates.dt.tz is not None
        or not dates.dt.normalize().equals(dates)
        or dates.dt.year.ge(2025).any()
        or int(dates.nunique()) != EXPECTED_INDEPENDENT_DATE_COUNT
        or oof.duplicated(
            ["family", "model_id", "tract_geoid", "target_date"]
        ).any()
        or not np.isfinite(numeric.to_numpy(dtype=float)).all()
    ):
        raise ReturnedResultImportError("OOF scientific key/value audit failed.")
    oof = oof.copy()
    oof["target_date"] = dates
    oof.loc[:, ["y_true", "y_pred"]] = numeric

    output_records: dict[str, dict[str, Any]] = {
        OOF_PREDICTIONS_FILENAME: {
            "path": OOF_PREDICTIONS_FILENAME,
            **actual_oof_record,
            "columns": list(OUTER_PREDICTION_COLUMNS),
        }
    }
    _, output_records[SUMMARY_METRICS_FILENAME] = _validate_csv_output(
        evaluation_root / SUMMARY_METRICS_FILENAME,
        output_locks[SUMMARY_METRICS_FILENAME],
        expected_columns=SUMMARY_METRIC_COLUMNS,
        expected_rows=EXPECTED_SUMMARY_ROW_COUNT,
        label="Summary metrics",
    )
    _, output_records[PER_DATE_METRICS_FILENAME] = _validate_csv_output(
        evaluation_root / PER_DATE_METRICS_FILENAME,
        output_locks[PER_DATE_METRICS_FILENAME],
        expected_columns=PER_DATE_METRIC_COLUMNS,
        expected_rows=EXPECTED_PER_DATE_ROW_COUNT,
        label="Per-date metrics",
    )
    _, output_records[FOLD_METRICS_FILENAME] = _validate_csv_output(
        evaluation_root / FOLD_METRICS_FILENAME,
        output_locks[FOLD_METRICS_FILENAME],
        expected_columns=FOLD_METRIC_COLUMNS,
        expected_rows=EXPECTED_FOLD_METRIC_ROW_COUNT,
        label="Fold metrics",
    )
    output_records[COMPILE_PROVENANCE_FILENAME] = {
        "path": COMPILE_PROVENANCE_FILENAME,
        "sha256": provenance_sha,
        "bytes": provenance_path.stat().st_size,
        "rows": 1,
        "schema_version": provenance["schema_version"],
        "algorithm_version": provenance["algorithm_version"],
        "commit_sha256": provenance_commit,
    }
    return _CompiledOutputs(
        provenance=provenance,
        provenance_sha256=provenance_sha,
        oof=oof,
        output_records=output_records,
    )


def _validated_fragment_records(
    compiled: _CompiledOutputs,
    queue: _QueueAudit,
) -> list[dict[str, Any]]:
    rows = compiled.provenance.get("input_fragments")
    if not isinstance(rows, list) or len(rows) != EXPECTED_OUTER_TASK_COUNT:
        raise ReturnedResultImportError("Compile provenance lacks exact fragment locks.")
    required = {
        "task_id",
        "family",
        "fold_id",
        "model_id",
        "candidate_id",
        "path",
        "path_base",
        "sha256",
        "bytes",
        "rows",
        "schema_sha256",
    }
    result: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    group_keys: set[tuple[str, str, str]] = set()
    for value in rows:
        if not isinstance(value, dict) or set(value) != required:
            raise ReturnedResultImportError("Compile fragment record schema is invalid.")
        row = dict(value)
        task_id = str(row["task_id"])
        group_key = (str(row["family"]), str(row["fold_id"]), str(row["model_id"]))
        queue_lock = queue.fragment_locks.get(task_id)
        if queue_lock is None:
            raise ReturnedResultImportError(f"Compile fragment is absent from queue: {task_id}")
        if (
            task_id in task_ids
            or group_key in group_keys
            or row["path_base"] != "run_directory"
            or row["path"] != f"outer_fragments/{task_id}.parquet"
            or row["model_id"] != queue_lock["model_id"]
            or row["candidate_id"] != queue_lock["selected_candidate_id"]
        ):
            raise ReturnedResultImportError(f"Compile fragment identity failed: {task_id}")
        for field in ("path", "path_base", "sha256", "bytes", "rows", "schema_sha256"):
            if row[field] != queue_lock[field]:
                raise ReturnedResultImportError(
                    f"Queue/compile fragment lock mismatch for {task_id}: {field}"
                )
        _hex_sha256(row["sha256"], label=f"{task_id} fragment SHA-256")
        _hex_sha256(row["schema_sha256"], label=f"{task_id} schema SHA-256")
        if int(row["bytes"]) <= 0 or int(row["rows"]) <= 0:
            raise ReturnedResultImportError(f"Fragment size lock is invalid: {task_id}")
        row["semantic_sha256"] = queue_lock["semantic_sha256"]
        task_ids.add(task_id)
        group_keys.add(group_key)
        result.append(row)
    if task_ids != set(queue.fragment_locks):
        raise ReturnedResultImportError("Queue/compile fragment coverage is not exact.")
    return result


def _normalized_fragment_frame(
    oof: pd.DataFrame,
    indices: dict[tuple[str, str, str], np.ndarray],
    record: dict[str, Any],
) -> pd.DataFrame:
    key = (str(record["family"]), str(record["fold_id"]), str(record["model_id"]))
    positions = indices.get(key)
    if positions is None:
        raise ReturnedResultImportError(
            f"OOF lacks fragment group for {record['task_id']}."
        )
    frame = oof.iloc[positions].loc[:, list(OUTER_PREDICTION_COLUMNS)].copy()
    frame = frame.reset_index(drop=True)
    frame["target_date"] = pd.to_datetime(
        frame["target_date"], errors="raise"
    ).astype("datetime64[ns]")
    if (
        len(frame) != int(record["rows"])
        or frame["family"].nunique(dropna=False) != 1
        or frame["family"].iloc[0] != record["family"]
        or frame["fold_id"].nunique(dropna=False) != 1
        or frame["fold_id"].iloc[0] != record["fold_id"]
        or frame["model_id"].nunique(dropna=False) != 1
        or frame["model_id"].iloc[0] != record["model_id"]
        or frame["candidate_id"].nunique(dropna=False) != 1
        or frame["candidate_id"].iloc[0] != record["candidate_id"]
    ):
        raise ReturnedResultImportError(
            f"OOF fragment identity/row lock failed: {record['task_id']}"
        )
    semantic = canonical_frame_sha256(
        frame,
        sort_by=["target_date", "tract_geoid"],
    )
    if semantic != record["semantic_sha256"]:
        raise ReturnedResultImportError(
            f"OOF fragment semantic lock failed: {record['task_id']}"
        )
    return frame


def _verify_fragment_file(
    path: Path,
    record: dict[str, Any],
    expected_frame: pd.DataFrame,
) -> None:
    _verify_byte_lock(path, record, label=f"Fragment {record['task_id']}")
    observed = pd.read_parquet(path)
    if tuple(observed.columns) != OUTER_PREDICTION_COLUMNS:
        raise ReturnedResultImportError(
            f"Fragment schema/order failed: {record['task_id']}"
        )
    observed = observed.copy()
    observed["target_date"] = pd.to_datetime(
        observed["target_date"], errors="raise"
    ).astype("datetime64[ns]")
    actual = parquet_file_record(path, observed)
    for field in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual[field] != record[field]:
            raise ReturnedResultImportError(
                f"Fragment {record['task_id']} {field} lock failed."
            )
    observed_semantic = canonical_frame_sha256(
        observed,
        sort_by=["target_date", "tract_geoid"],
    )
    if (
        observed_semantic != record["semantic_sha256"]
        or canonical_frame_sha256(
            expected_frame, sort_by=["target_date", "tract_geoid"]
        )
        != observed_semantic
    ):
        raise ReturnedResultImportError(
            f"Fragment {record['task_id']} disagrees with authenticated OOF."
        )


def _verify_reconstructed_fragment(
    path: Path,
    record: dict[str, Any],
    expected_frame: pd.DataFrame,
) -> None:
    actual = parquet_file_record(path, expected_frame)
    for field in ("sha256", "bytes", "rows", "schema_sha256"):
        if actual[field] != record[field]:
            raise ReturnedResultImportError(
                f"Reconstructed fragment {record['task_id']} {field} lock failed."
            )


def _stage_run_directory(
    source_run_root: Path,
    staged_run_root: Path,
    *,
    compiled: _CompiledOutputs,
    records: list[dict[str, Any]],
    allow_reconstruction: bool,
    run_metadata: dict[str, Any],
) -> tuple[int, int]:
    staged_run_root.mkdir(parents=True)
    for name in ("run_manifest.json", "outer_selections.json"):
        source = source_run_root / name
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = staged_run_root / name
        shutil.copy2(source, destination)
        expected_sha = run_metadata[
            "run_manifest_sha256"
            if name == "run_manifest.json"
            else "outer_selections_sha256"
        ]
        if sha256_file(destination) != expected_sha:
            raise ReturnedResultImportError(
                f"Staged returned run manifest changed during copy: {name}"
            )
    source_fragments = source_run_root / "outer_fragments"
    staged_fragments = staged_run_root / "outer_fragments"
    staged_fragments.mkdir()
    expected_names = {Path(str(record["path"])).name for record in records}
    if source_fragments.is_dir():
        observed_names = {
            path.name for path in source_fragments.iterdir() if path.is_file()
        }
        unexpected = observed_names - expected_names
        if unexpected:
            raise ReturnedResultImportError(
                f"Returned fragment directory contains unexpected files: {sorted(unexpected)[:3]}"
            )
    group_indices = {
        (str(family), str(fold_id), str(model_id)): positions
        for (family, fold_id, model_id), positions in compiled.oof.groupby(
            ["family", "fold_id", "model_id"], sort=False, observed=True
        ).indices.items()
    }
    if len(group_indices) != EXPECTED_OUTER_TASK_COUNT:
        raise ReturnedResultImportError("OOF fragment-group count is not exact.")
    existing_count = 0
    reconstructed_count = 0
    for record in records:
        expected_frame = _normalized_fragment_frame(
            compiled.oof,
            group_indices,
            record,
        )
        filename = Path(str(record["path"])).name
        source = source_fragments / filename
        destination = staged_fragments / filename
        if source.is_file():
            _verify_fragment_file(source, record, expected_frame)
            shutil.copy2(source, destination)
            _verify_fragment_file(destination, record, expected_frame)
            existing_count += 1
        else:
            if not allow_reconstruction:
                raise ReturnedResultImportError(
                    "ZIP return lacks an original authenticated fragment: "
                    f"{record['task_id']}"
                )
            atomic_parquet(expected_frame, destination)
            _verify_reconstructed_fragment(destination, record, expected_frame)
            reconstructed_count += 1
    if existing_count + reconstructed_count != EXPECTED_OUTER_TASK_COUNT:
        raise AssertionError("Staged outer fragment count is not exact.")
    return existing_count, reconstructed_count


def _copy_evaluation_to_staging(
    returned_root: Path,
    staged_evaluation_root: Path,
    *,
    compiled: _CompiledOutputs,
) -> None:
    source = returned_root / RETURNED_EVALUATION_PATH
    staged_evaluation_root.mkdir(parents=True)
    for name in MODEL_EVALUATION_FILES:
        input_path = source / name
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        output_path = staged_evaluation_root / name
        shutil.copy2(input_path, output_path)
        _verify_byte_lock(
            output_path,
            compiled.output_records[name],
            label=f"Staged evaluation output {name}",
        )


def _copy_returned_queue(
    source: Path,
    destination: Path,
    *,
    run_id: str,
    expected_audit: _QueueAudit,
) -> None:
    destination.parent.mkdir(parents=True)
    if destination.exists():
        raise FileExistsError(destination)
    source_before = _locked_file_record(source)
    shutil.copy2(source, destination)
    if (
        _locked_file_record(source) != source_before
        or destination.stat().st_size != source_before["bytes"]
        or sha256_file(destination) != source_before["sha256"]
    ):
        raise RuntimeError("Returned SQLite changed during exact snapshot copy.")
    snapshot_audit = _validate_queue(destination, expected_run_id=run_id)
    if snapshot_audit != expected_audit:
        raise RuntimeError("Returned SQLite snapshot audit changed during copy.")


def _destination_paths(project_root: Path, run_id: str) -> dict[str, Path]:
    return {
        "evaluation": project_root / RETURNED_EVALUATION_PATH,
        "run": project_root / "data/interim/model_runs/runs" / run_id,
        "snapshot": project_root
        / "data/interim/model_runs/returned_snapshots"
        / run_id,
    }


def _preflight_destinations(destinations: dict[str, Path]) -> None:
    existing = [str(path) for path in destinations.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "Returned-result import refuses to overwrite existing destinations: "
            + ", ".join(existing)
        )


def _short_staging_directory(
    project_root: Path,
) -> tempfile.TemporaryDirectory[str]:
    candidates = [Path(project_root.anchor), project_root.parent]
    errors: list[OSError] = []
    for parent in candidates:
        try:
            temporary = tempfile.TemporaryDirectory(prefix=".ri-", dir=parent)
        except OSError as error:
            errors.append(error)
            continue
        if Path(temporary.name).anchor != project_root.anchor:
            temporary.cleanup()
            raise ReturnedResultImportError(
                "Returned-result staging must remain on the project volume."
            )
        return temporary
    raise OSError("Cannot create same-volume returned-result staging.") from errors[-1]


def _directory_source_record(returned_root: Path) -> dict[str, Any]:
    result_manifest = returned_root / "result_manifest.json"
    return {
        "kind": "directory",
        "path": str(returned_root),
        "result_manifest_present": result_manifest.is_file(),
    }


def _publish_staged_directories(
    staged: dict[str, Path],
    destinations: dict[str, Path],
    expected_trees: dict[str, dict[str, Any]],
) -> None:
    _preflight_destinations(destinations)
    published: list[str] = []
    try:
        for name in ("evaluation", "run", "snapshot"):
            staged[name].replace(destinations[name])
            published.append(name)
        for name in ("evaluation", "run", "snapshot"):
            if _tree_record(destinations[name]) != expected_trees[name]:
                raise RuntimeError(
                    f"Published returned-result tree changed during import: {name}"
                )
    except (OSError, RuntimeError) as error:
        rollback_errors: list[str] = []
        for name in reversed(published):
            try:
                destinations[name].replace(staged[name])
            except OSError as rollback_error:
                rollback_errors.append(f"{name}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                "Returned-result publication failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from error
        raise


def _build_import_audit(
    *,
    returned_source: dict[str, Any],
    project_root: Path,
    status: dict[str, Any],
    status_sha256: str,
    transfer: dict[str, Any],
    queue: _QueueAudit,
    queue_source_path: Path,
    queue_snapshot_path: Path,
    run_metadata: dict[str, Any],
    compiled: _CompiledOutputs,
    fragment_records: list[dict[str, Any]],
    existing_fragment_count: int,
    reconstructed_fragment_count: int,
    staged_evaluation_root: Path,
    staged_run_root: Path,
    source_guards: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fragment_lock_rows = [
        {
            field: record[field]
            for field in (
                "task_id",
                "path",
                "sha256",
                "bytes",
                "rows",
                "schema_sha256",
                "semantic_sha256",
            )
        }
        for record in sorted(fragment_records, key=lambda row: str(row["task_id"]))
    ]
    destinations = _destination_paths(project_root, queue.run_id)
    payload: dict[str, Any] = {
        "schema_version": RETURNED_RESULT_IMPORT_SCHEMA_VERSION,
        "algorithm_version": RETURNED_RESULT_IMPORT_ALGORITHM_VERSION,
        "state": "complete",
        "imported_at_utc": _utc_now(),
        "returned_source": returned_source,
        "project_root": str(project_root),
        "run_id": queue.run_id,
        "context_run_id": run_metadata["context_run_id"],
        "task_plan_sha256": run_metadata["task_plan_sha256"],
        "transfer": transfer,
        "terminal_status": {
            "path": RETURNED_STATUS_PATH.as_posix(),
            "sha256": status_sha256,
            "state": status["state"],
            "completed": status["completed"],
            "total": status["total"],
            "active": status["active"],
            "quarantined": status["quarantined"],
        },
        "returned_queue": {
            "path": RETURNED_QUEUE_PATH.as_posix(),
            "sha256": sha256_file(queue_source_path),
            "bytes": queue_source_path.stat().st_size,
            "integrity_check": "ok",
            "desired_state": queue.desired_state,
            "inner_complete": EXPECTED_INNER_TASK_COUNT,
            "outer_complete": EXPECTED_OUTER_TASK_COUNT,
            "total_complete": EXPECTED_RUN_TASK_COUNT,
        },
        "returned_run_manifests": run_metadata,
        "compiled_outputs": {
            "provenance_sha256": compiled.provenance_sha256,
            "provenance_commit_sha256": compiled.provenance["commit_sha256"],
            "files": compiled.output_records,
            "oof_row_count": len(compiled.oof),
            "independent_date_count": int(
                compiled.oof["target_date"].nunique()
            ),
            "maximum_target_year": int(
                compiled.oof["target_date"].dt.year.max()
            ),
        },
        "outer_fragments": {
            "expected_count": EXPECTED_OUTER_TASK_COUNT,
            "verified_existing_count": existing_fragment_count,
            "reconstructed_count": reconstructed_fragment_count,
            "target_date_dtype_during_reconstruction": "datetime64[ns]",
            "all_sha_bytes_rows_schema_exact": True,
            "all_semantic_locks_match_authenticated_oof": True,
            "lock_set_sha256": canonical_sha256(fragment_lock_rows),
            "total_bytes": sum(int(row["bytes"]) for row in fragment_records),
        },
        "staged_artifacts": {
            "model_evaluation": _tree_record(staged_evaluation_root),
            "run_directory": _tree_record(staged_run_root),
            "returned_queue_snapshot": {
                "sha256": sha256_file(queue_snapshot_path),
                "bytes": queue_snapshot_path.stat().st_size,
                "integrity_check": "ok",
            },
        },
        "canonical_destinations": {
            "model_evaluation": str(destinations["evaluation"]),
            "run_directory": str(destinations["run"]),
            "returned_queue_snapshot_directory": str(destinations["snapshot"]),
        },
        "publication": {
            "policy": "non_overwriting_same_volume_replace_with_rollback",
            "commit_marker": (
                "returned_queue_snapshot_directory/"
                "returned_result_import_audit.json"
            ),
            "commit_marker_published_last": True,
        },
        "source_guards": {
            "records_before_and_after": source_guards,
            "unchanged": True,
            "source_queue_overwritten": False,
            "transfer_ownership_overwritten": False,
            "transferred_out_marker_removed": False,
        },
        "final_test": {
            "year": 2025,
            "unlock_final_test": False,
            "maximum_imported_target_year": int(
                compiled.oof["target_date"].dt.year.max()
            ),
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def verify_and_import_returned_results(
    returned_root: str | Path,
    project_root: str | Path,
    *,
    verify_only: bool = False,
    expected_archive_sha256: str | None = None,
) -> ReturnedResultImportSummary:
    """Authenticate a returned terminal run and optionally publish it safely.

    Missing outer fragments are reconstructed only from the authenticated OOF
    table.  A reconstructed file is accepted only when its byte hash, byte
    count, row count, normalized schema hash, and semantic hash all equal the
    independent locks preserved in compile provenance and the returned SQLite
    queue.
    """

    returned_source_path = Path(returned_root).resolve()
    project = Path(project_root).resolve()
    if not returned_source_path.exists():
        raise FileNotFoundError(returned_source_path)
    if not returned_source_path.is_dir() and not returned_source_path.is_file():
        raise ReturnedResultImportError(
            "Returned source must be a directory or ZIP file."
        )
    if not project.is_dir():
        raise FileNotFoundError(project)
    _validate_final_test_locks(project)
    guards_before = _source_guard_records(project)
    with _short_staging_directory(project) as temporary:
        staging = Path(temporary)
        if returned_source_path.is_file():
            if returned_source_path.suffix.casefold() != ".zip":
                raise ReturnedResultImportError(
                    "Returned file source must have a .zip extension."
                )
            returned = staging / "i"
            returned_source_record = _materialize_returned_zip(
                returned_source_path,
                returned,
                expected_archive_sha256=expected_archive_sha256,
            )
            allow_reconstruction = False
        else:
            if expected_archive_sha256 is not None:
                raise ReturnedResultImportError(
                    "--expected-archive-sha256 applies only to ZIP returns."
                )
            returned = returned_source_path
            returned_source_record = _directory_source_record(returned)
            allow_reconstruction = True

        transfer = _validate_transfer_authority(returned, project)
        status, status_sha = _validate_status(returned)
        run_id = str(status["run_id"])
        queue_source = returned / RETURNED_QUEUE_PATH
        queue = _validate_queue(queue_source, expected_run_id=run_id)
        run_source, run_metadata = _validate_run_manifests(
            returned,
            run_id=run_id,
            relocation_commit_sha256=transfer["relocation_commit_sha256"],
        )
        compiled = _validate_compiled_outputs(
            returned,
            status=status,
            run_metadata=run_metadata,
        )
        fragment_records = _validated_fragment_records(compiled, queue)
        destinations = _destination_paths(project, run_id)
        if not verify_only:
            _preflight_destinations(destinations)

        staged_evaluation = staging / "e"
        staged_run = staging / "r"
        staged_snapshot = staging / "s"
        _copy_evaluation_to_staging(
            returned,
            staged_evaluation,
            compiled=compiled,
        )
        existing_count, reconstructed_count = _stage_run_directory(
            run_source,
            staged_run,
            compiled=compiled,
            records=fragment_records,
            allow_reconstruction=allow_reconstruction,
            run_metadata=run_metadata,
        )
        if not allow_reconstruction and (
            existing_count != EXPECTED_OUTER_TASK_COUNT
            or reconstructed_count != 0
        ):
            raise ReturnedResultImportError(
                "ZIP return must supply all original outer fragments."
            )
        queue_snapshot = staged_snapshot / "model_tasks.sqlite3"
        _copy_returned_queue(
            queue_source,
            queue_snapshot,
            run_id=run_id,
            expected_audit=queue,
        )
        guards_after = _source_guard_records(project)
        if guards_after != guards_before:
            raise RuntimeError(
                "Source queue/ownership/marker/config changed during returned-result audit."
            )
        _validate_final_test_locks(project)
        audit = _build_import_audit(
            returned_source=returned_source_record,
            project_root=project,
            status=status,
            status_sha256=status_sha,
            transfer=transfer,
            queue=queue,
            queue_source_path=queue_source,
            queue_snapshot_path=queue_snapshot,
            run_metadata=run_metadata,
            compiled=compiled,
            fragment_records=fragment_records,
            existing_fragment_count=existing_count,
            reconstructed_fragment_count=reconstructed_count,
            staged_evaluation_root=staged_evaluation,
            staged_run_root=staged_run,
            source_guards=guards_before,
        )
        audit_commit = str(audit["commit_sha256"])
        if verify_only:
            return ReturnedResultImportSummary(
                state="verified",
                run_id=run_id,
                returned_root=returned_source_path,
                verified_existing_fragment_count=existing_count,
                reconstructed_fragment_count=reconstructed_count,
                imported=False,
                audit_path=None,
                audit_commit_sha256=audit_commit,
            )

        audit_path = staged_snapshot / "returned_result_import_audit.json"
        atomic_json(audit, audit_path)
        expected_evaluation_tree = _tree_record(staged_evaluation)
        expected_run_tree = _tree_record(staged_run)
        expected_snapshot_tree = _tree_record(staged_snapshot)
        for destination in destinations.values():
            destination.parent.mkdir(parents=True, exist_ok=True)
        _publish_staged_directories(
            {
                "evaluation": staged_evaluation,
                "run": staged_run,
                "snapshot": staged_snapshot,
            },
            destinations,
            {
                "evaluation": expected_evaluation_tree,
                "run": expected_run_tree,
                "snapshot": expected_snapshot_tree,
            },
        )
        published_audit_path = (
            destinations["snapshot"] / "returned_result_import_audit.json"
        )
        published_audit, _ = _read_json(
            published_audit_path, label="published returned-result import audit"
        )
        if (
            _verify_canonical_commit(
                published_audit, label="Published returned-result import audit"
            )
            != audit_commit
        ):
            raise RuntimeError("Published import audit commit changed.")
        if _source_guard_records(project) != guards_before:
            raise RuntimeError("Source guards changed during result publication.")
        _validate_final_test_locks(project)
        return ReturnedResultImportSummary(
            state="imported",
            run_id=run_id,
            returned_root=returned_source_path,
            verified_existing_fragment_count=existing_count,
            reconstructed_fragment_count=reconstructed_count,
            imported=True,
            audit_path=published_audit_path,
            audit_commit_sha256=audit_commit,
        )
