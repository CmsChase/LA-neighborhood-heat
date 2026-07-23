"""Audited relocation manifest for the nine frozen model-context inputs.

The committed scientific manifests intentionally retain the absolute paths at
which they were created.  A portable relocation manifest does not rewrite or
weaken those locks.  Instead, it binds every original absolute path to one
byte-identical, project-relative copy under an explicitly supplied portable
root.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

PORTABLE_RELOCATION_SCHEMA_VERSION: Final = 1
PORTABLE_RELOCATION_ALGORITHM_VERSION: Final = "model-context-relocation-v1"
DEFAULT_PORTABLE_RELOCATION_PATH: Final = Path("portable_relocation.json")

PORTABLE_CONTEXT_PATHS: Final[dict[str, Path]] = {
    "model_provenance": Path(
        "data/processed/model_dataset/model_dataset_provenance.json"
    ),
    "model_table": Path(
        "data/processed/model_dataset/development_model_table.parquet"
    ),
    "registry": Path("data/processed/model_dataset/feature_registry.csv"),
    "split_promotion": Path("manifests/validation_splits/split_promotion.json"),
    "row_groups": Path("manifests/validation_splits/row_groups.parquet"),
    "folds": Path("manifests/validation_splits/fold_definitions.csv"),
    "buffers": Path(
        "manifests/validation_splits/spatial_buffer_geoids.parquet"
    ),
    "selection_freeze": Path(
        "manifests/model_selection/model_selection_freeze.json"
    ),
    "selection_config": Path("configs/model_selection.toml"),
}
ORIGINAL_MANIFEST_NAMES: Final = (
    "model_provenance",
    "split_promotion",
    "selection_freeze",
)


class PortableRelocationError(ValueError):
    """Raised when a portable path substitution is not fully authenticated."""


@dataclass(frozen=True, slots=True)
class PortableRelocationEntry:
    """One original absolute path and its byte-identical portable copy."""

    logical_name: str
    original_path: Path
    relative_path: str
    portable_path: Path
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PortableRelocation:
    """Authenticated relocation map rooted at one explicit project directory."""

    manifest_path: Path
    portable_root: Path
    source_project_root: Path
    commit_sha256: str
    entries: dict[str, PortableRelocationEntry]

    def entry(self, logical_name: str) -> PortableRelocationEntry:
        try:
            return self.entries[logical_name]
        except KeyError as error:
            raise PortableRelocationError(
                f"Portable relocation lacks logical input {logical_name!r}."
            ) from error


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise PortableRelocationError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before:
        raise RuntimeError(f"{label} changed while being read: {path}")
    if not isinstance(payload, dict):
        raise PortableRelocationError(f"{label} must be a JSON object: {path}")
    return payload, before


def _verified_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise PortableRelocationError(f"{label} has an invalid canonical commit hash.")
    return recorded


def _inside(root: Path, candidate: Path, *, label: str) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PortableRelocationError(f"{label} must remain inside portable root.") from error
    return resolved


def _safe_relative_path(value: object, *, logical_name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PortableRelocationError(
            f"Portable {logical_name} relative_path must use non-empty POSIX syntax."
        )
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise PortableRelocationError(
            f"Portable {logical_name} relative_path is unsafe."
        )
    expected = PORTABLE_CONTEXT_PATHS[logical_name].as_posix()
    if candidate.as_posix() != expected:
        raise PortableRelocationError(
            f"Portable {logical_name} relative_path must be {expected!r}."
        )
    return candidate.as_posix()


def _file_lock(path: Path) -> tuple[int, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = path.stat().st_size
    digest = sha256_file(path)
    if not path.is_file() or path.stat().st_size != before:
        raise RuntimeError(f"File changed while being locked: {path}")
    return before, digest


def _recorded_path(record: object, *, label: str) -> Path:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise PortableRelocationError(f"{label} lacks its original path lock.")
    path = Path(record["path"])
    if not path.is_absolute():
        raise PortableRelocationError(f"{label} original path lock is not absolute.")
    return path.resolve()


def _validate_source_cross_locks(
    entries: dict[str, PortableRelocationEntry],
    manifest_payloads: dict[str, dict[str, Any]],
    manifest_file_sha256: dict[str, str],
) -> None:
    model = manifest_payloads["model_provenance"]
    split = manifest_payloads["split_promotion"]
    selection = manifest_payloads["selection_freeze"]
    model_outputs = model.get("output_files")
    split_inputs = split.get("inputs")
    selection_config = selection.get("config")
    if not isinstance(model_outputs, dict) or not isinstance(split_inputs, dict):
        raise PortableRelocationError("Original manifests lack required file-lock sections.")
    if not isinstance(selection_config, dict):
        raise PortableRelocationError("Original selection manifest lacks its config lock.")

    path_records: dict[str, object] = {
        "model_table": model_outputs.get("development_model_table.parquet"),
        "registry": model_outputs.get("feature_registry.csv"),
        "selection_config": selection_config,
        "model_provenance": split_inputs.get("model_dataset_provenance"),
        "model_table_split": split_inputs.get("model_dataset"),
    }
    split_outputs = split_inputs.get("frozen_split_outputs")
    if not isinstance(split_outputs, dict):
        raise PortableRelocationError("Original split manifest lacks frozen outputs.")
    path_records.update(
        {
            "row_groups": split_outputs.get("row_groups.parquet"),
            "folds": split_outputs.get("fold_definitions.csv"),
            "buffers": split_outputs.get("spatial_buffer_geoids.parquet"),
        }
    )
    for logical_name in (
        "model_table",
        "registry",
        "selection_config",
        "model_provenance",
        "row_groups",
        "folds",
        "buffers",
    ):
        if _recorded_path(
            path_records[logical_name], label=f"Original {logical_name}"
        ) != entries[logical_name].original_path:
            raise PortableRelocationError(
                f"Original {logical_name} path disagrees with relocation entry."
            )
    if _recorded_path(
        path_records["model_table_split"], label="Original split model table"
    ) != entries["model_table"].original_path:
        raise PortableRelocationError(
            "Original split/model manifests disagree on model-table path."
        )

    model_lock = path_records["model_provenance"]
    assert isinstance(model_lock, dict)
    if (
        model_lock.get("sha256") != manifest_file_sha256["model_provenance"]
        or model_lock.get("commit_sha256")
        != manifest_payloads["model_provenance"].get("commit_sha256")
    ):
        raise PortableRelocationError(
            "Original split manifest does not byte/commit-lock model provenance."
        )


def load_portable_relocation(
    manifest_path: str | Path,
    portable_root: str | Path,
) -> PortableRelocation:
    """Authenticate a relocation manifest and all nine destination copies."""

    root = Path(portable_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    manifest = Path(manifest_path)
    if not manifest.is_absolute():
        manifest = root / manifest
    manifest = _inside(root, manifest, label="Portable relocation manifest")
    payload, _ = _read_json(manifest, label="portable relocation manifest")
    commit = _verified_commit(payload, label="Portable relocation manifest")
    if (
        payload.get("schema_version") != PORTABLE_RELOCATION_SCHEMA_VERSION
        or payload.get("algorithm_version")
        != PORTABLE_RELOCATION_ALGORITHM_VERSION
        or payload.get("state") != "complete"
    ):
        raise PortableRelocationError("Portable relocation header is not supported.")
    source_value = payload.get("source_project_root")
    if not isinstance(source_value, str) or not Path(source_value).is_absolute():
        raise PortableRelocationError(
            "Portable relocation source_project_root must be absolute."
        )
    source_root = Path(source_value).resolve()
    rows = payload.get("entries")
    if not isinstance(rows, list) or len(rows) != len(PORTABLE_CONTEXT_PATHS):
        raise PortableRelocationError(
            "Portable relocation must contain exactly nine context entries."
        )
    entries: dict[str, PortableRelocationEntry] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "logical_name",
            "original_path",
            "relative_path",
            "bytes",
            "sha256",
        }:
            raise PortableRelocationError("Portable relocation entry schema is invalid.")
        logical_name = row.get("logical_name")
        if not isinstance(logical_name, str) or logical_name not in PORTABLE_CONTEXT_PATHS:
            raise PortableRelocationError("Portable relocation logical_name is invalid.")
        if logical_name in entries:
            raise PortableRelocationError(
                f"Portable relocation duplicates {logical_name!r}."
            )
        original_value = row.get("original_path")
        if not isinstance(original_value, str) or not Path(original_value).is_absolute():
            raise PortableRelocationError(
                f"Portable {logical_name} original_path must be absolute."
            )
        original = Path(original_value).resolve()
        expected_original = (source_root / PORTABLE_CONTEXT_PATHS[logical_name]).resolve()
        if original != expected_original:
            raise PortableRelocationError(
                f"Portable {logical_name} original_path is inconsistent with source root."
            )
        relative = _safe_relative_path(row.get("relative_path"), logical_name=logical_name)
        portable = _inside(root, root / Path(relative), label=f"Portable {logical_name}")
        try:
            expected_bytes = int(row["bytes"])
            expected_sha256 = str(row["sha256"])
        except (TypeError, ValueError) as error:
            raise PortableRelocationError(
                f"Portable {logical_name} byte lock is incomplete."
            ) from error
        if (
            expected_bytes < 0
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or not portable.is_file()
            or portable.stat().st_size != expected_bytes
            or sha256_file(portable) != expected_sha256
        ):
            raise PortableRelocationError(
                f"Portable {logical_name} destination byte lock failed."
            )
        entries[logical_name] = PortableRelocationEntry(
            logical_name=logical_name,
            original_path=original,
            relative_path=relative,
            portable_path=portable,
            sha256=expected_sha256,
            bytes=expected_bytes,
        )
    if set(entries) != set(PORTABLE_CONTEXT_PATHS):
        raise PortableRelocationError("Portable relocation context coverage is not exact.")

    lock_rows = payload.get("original_manifest_locks")
    if not isinstance(lock_rows, dict) or set(lock_rows) != set(ORIGINAL_MANIFEST_NAMES):
        raise PortableRelocationError(
            "Portable relocation must lock all three original manifests."
        )
    manifest_payloads: dict[str, dict[str, Any]] = {}
    manifest_file_sha256: dict[str, str] = {}
    for logical_name in ORIGINAL_MANIFEST_NAMES:
        lock = lock_rows.get(logical_name)
        if not isinstance(lock, dict) or set(lock) != {
            "bytes",
            "sha256",
            "commit_sha256",
        }:
            raise PortableRelocationError(
                f"Original manifest lock {logical_name!r} is invalid."
            )
        original_payload, file_sha = _read_json(
            entries[logical_name].portable_path,
            label=f"portable copy of {logical_name}",
        )
        original_commit = _verified_commit(
            original_payload, label=f"Original manifest {logical_name}"
        )
        if (
            lock.get("bytes") != entries[logical_name].bytes
            or lock.get("sha256") != file_sha
            or lock.get("commit_sha256") != original_commit
        ):
            raise PortableRelocationError(
                f"Original manifest lock {logical_name!r} failed."
            )
        manifest_payloads[logical_name] = original_payload
        manifest_file_sha256[logical_name] = file_sha
    _validate_source_cross_locks(entries, manifest_payloads, manifest_file_sha256)
    return PortableRelocation(
        manifest_path=manifest,
        portable_root=root,
        source_project_root=source_root,
        commit_sha256=commit,
        entries=entries,
    )


def build_portable_relocation_manifest(
    source_project_root: str | Path,
    bundle_root: str | Path,
    *,
    output_path: str | Path = DEFAULT_PORTABLE_RELOCATION_PATH,
) -> Path:
    """Create and self-verify a relocation manifest for copied frozen inputs.

    The caller copies the nine files first.  This function proves that every
    destination file is byte-identical to its source, locks the canonical
    commits of the three original manifests, and writes only the new relocation
    manifest.  It never edits a committed source or destination input.
    """

    source_root = Path(source_project_root).resolve()
    portable_root = Path(bundle_root).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    if not portable_root.is_dir():
        raise FileNotFoundError(portable_root)
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = portable_root / destination
    destination = _inside(
        portable_root, destination, label="Portable relocation output"
    )
    entries: list[dict[str, Any]] = []
    for logical_name, relative_path in PORTABLE_CONTEXT_PATHS.items():
        source = (source_root / relative_path).resolve()
        portable = _inside(
            portable_root,
            portable_root / relative_path,
            label=f"Portable {logical_name}",
        )
        source_bytes, source_sha = _file_lock(source)
        portable_bytes, portable_sha = _file_lock(portable)
        if (portable_bytes, portable_sha) != (source_bytes, source_sha):
            raise PortableRelocationError(
                f"Portable {logical_name} is not byte-identical to its source."
            )
        entries.append(
            {
                "logical_name": logical_name,
                "original_path": str(source),
                "relative_path": relative_path.as_posix(),
                "bytes": source_bytes,
                "sha256": source_sha,
            }
        )
    original_manifest_locks: dict[str, dict[str, Any]] = {}
    for logical_name in ORIGINAL_MANIFEST_NAMES:
        path = source_root / PORTABLE_CONTEXT_PATHS[logical_name]
        manifest_payload, manifest_sha = _read_json(
            path, label=f"source {logical_name}"
        )
        original_manifest_locks[logical_name] = {
            "bytes": path.stat().st_size,
            "sha256": manifest_sha,
            "commit_sha256": _verified_commit(
                manifest_payload, label=f"Source manifest {logical_name}"
            ),
        }
    payload: dict[str, Any] = {
        "schema_version": PORTABLE_RELOCATION_SCHEMA_VERSION,
        "algorithm_version": PORTABLE_RELOCATION_ALGORITHM_VERSION,
        "state": "complete",
        "source_project_root": str(source_root),
        "entries": entries,
        "original_manifest_locks": original_manifest_locks,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, destination)
    load_portable_relocation(destination, portable_root)
    return destination
