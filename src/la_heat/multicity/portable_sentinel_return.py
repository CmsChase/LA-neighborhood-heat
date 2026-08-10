"""Verify and import a completed portable four-city Sentinel result package."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final
from zipfile import BadZipFile, ZipFile, ZipInfo

from la_heat.multicity.portable_sentinel_build import (
    CITY_COMPLETE_FILENAME,
    CITY_OUTPUTS,
    FINAL_COMPLETE,
    FINAL_OUTPUT,
    city_output_is_current,
    final_output_is_current,
    prepare_contexts,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

RESULT_ROOT_NAME: Final = "GAMING_LAPTOP_SENTINEL_RESULTS"
RESULT_MANIFEST_NAME: Final = "RESULT_MANIFEST.json"
SOURCE_BUNDLE_MANIFEST: Final = "PORTABLE_BUNDLE_MANIFEST.json"
STATUS_PATH: Final = (
    "data/interim/multicity/portable_predictors/runtime/sentinel/status.json"
)
EXPECTED_CITY_TOTALS: Final = {
    "chicago_il": 57,
    "phoenix_az": 117,
    "houston_tx": 114,
    "los_angeles_ca": 227,
}
EXPECTED_ACQUISITIONS: Final = {
    city_id: total - 1 for city_id, total in EXPECTED_CITY_TOTALS.items()
}
EXPECTED_TOTAL: Final = 516
EXPECTED_ROWS: Final = 136_941
RECEIPT_PATH: Final = Path(
    "manifests/multicity/returns/PORTABLE_SENTINEL_RETURN.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMPORT_PREFIXES: Final = (
    "data/interim/multicity/portable_predictors/runtime/sentinel/",
    "data/raw/multicity/portable_predictors/sentinel_product_metadata/",
    "data/raw/sentinel/product_metadata/",
    "data/processed/multicity/portable_predictors/components/sentinel/",
)
_IMPORT_FILES: Final = {
    FINAL_OUTPUT.as_posix(),
    FINAL_COMPLETE.as_posix(),
}


class PortableSentinelReturnError(RuntimeError):
    """Raised when a returned package is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class PortableSentinelReturnSummary:
    archive: str
    archive_sha256: str
    source_state: str
    packaged_file_count: int
    packaged_bytes: int
    completed_work_units: int
    imported: bool
    imported_file_count: int
    unchanged_file_count: int
    receipt: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _VerifiedPackage:
    archive_sha256: str
    source_state: str
    records: dict[str, dict[str, object]]
    members: dict[str, ZipInfo]
    packaged_bytes: int
    status: dict[str, Any]
    city_commits: dict[str, str]
    final_commit: str
    result_manifest_sha256: str
    bundle_manifest_sha256: str


def _read_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PortableSentinelReturnError(f"Invalid {label} JSON.") from error
    if not isinstance(payload, dict):
        raise PortableSentinelReturnError(f"{label} must be a JSON object.")
    return payload


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_member(archive: ZipFile, info: ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PortableSentinelReturnError(f"Invalid {label} path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PortableSentinelReturnError(f"Unsafe {label} path: {value!r}")
    return path.as_posix()


def _archive_members(archive: ZipFile) -> dict[str, ZipInfo]:
    members: dict[str, ZipInfo] = {}
    folded: set[str] = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        full = _safe_relative_path(info.filename, label="ZIP member")
        parts = PurePosixPath(full).parts
        if len(parts) < 2 or parts[0] != RESULT_ROOT_NAME:
            raise PortableSentinelReturnError(
                f"ZIP member is outside {RESULT_ROOT_NAME}: {info.filename!r}"
            )
        relative = PurePosixPath(*parts[1:]).as_posix()
        key = relative.casefold()
        if relative in members or key in folded:
            raise PortableSentinelReturnError(f"Duplicate ZIP member: {relative}")
        if info.flag_bits & 0x1:
            raise PortableSentinelReturnError(f"Encrypted ZIP member: {relative}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise PortableSentinelReturnError(f"Symbolic-link ZIP member: {relative}")
        members[relative] = info
        folded.add(key)
    return members


def _manifest_records(payload: dict[str, Any]) -> dict[str, dict[str, object]]:
    if payload.get("schema_version") != 1:
        raise PortableSentinelReturnError("Unsupported result-manifest schema.")
    raw_records = payload.get("files")
    if not isinstance(raw_records, list) or not raw_records:
        raise PortableSentinelReturnError("Result manifest has no file records.")
    records: dict[str, dict[str, object]] = {}
    folded: set[str] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise PortableSentinelReturnError(f"Invalid file record {index}.")
        path = _safe_relative_path(raw.get("path"), label="manifest")
        size = raw.get("bytes")
        digest = raw.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise PortableSentinelReturnError(f"Invalid file lock for {path}.")
        key = path.casefold()
        if path in records or key in folded:
            raise PortableSentinelReturnError(f"Duplicate manifest path: {path}")
        records[path] = {"path": path, "bytes": size, "sha256": digest}
        folded.add(key)
    return records


def _read_member_json(
    archive: ZipFile,
    members: dict[str, ZipInfo],
    path: str,
    *,
    label: str,
) -> dict[str, Any]:
    info = members.get(path)
    if info is None:
        raise PortableSentinelReturnError(f"Missing {label}: {path}")
    if info.file_size > 50 * 1024 * 1024:
        raise PortableSentinelReturnError(f"Unexpectedly large {label}.")
    return _read_json_bytes(archive.read(info), label=label)


def _committed(payload: dict[str, Any], *, label: str) -> str:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if (
        not isinstance(recorded, str)
        or _SHA256.fullmatch(recorded) is None
        or canonical_sha256(unsigned) != recorded
    ):
        raise PortableSentinelReturnError(f"Invalid {label} commit.")
    return recorded


def _record_matches(
    archive_records: dict[str, dict[str, object]],
    path: str,
    output: object,
) -> bool:
    if not isinstance(output, dict) or path not in archive_records:
        return False
    locked = archive_records[path]
    return (
        output.get("bytes") == locked["bytes"]
        and output.get("sha256") == locked["sha256"]
    )


def _validate_completed_payloads(
    read_json: Callable[[str, str], dict[str, Any]],
    records: dict[str, dict[str, object]],
) -> tuple[dict[str, Any], dict[str, str], str]:
    status = read_json(STATUS_PATH, "Sentinel runtime status")
    if (
        status.get("state") != "complete"
        or status.get("algorithm_version") != "portable-four-city-sentinel-v1"
        or status.get("total") != EXPECTED_TOTAL
        or status.get("completed") != EXPECTED_TOTAL
        or status.get("pending") != 0
        or status.get("running") != 0
        or status.get("failed") != 0
        or status.get("current") != []
        or status.get("current_city") is not None
        or status.get("error") is not None
    ):
        raise PortableSentinelReturnError(
            "Sentinel runtime status is not a clean 516/516 completion."
        )
    cities = status.get("cities")
    if not isinstance(cities, dict) or set(cities) != set(EXPECTED_CITY_TOTALS):
        raise PortableSentinelReturnError("Sentinel city status set is invalid.")
    for city_id, expected_total in EXPECTED_CITY_TOTALS.items():
        city = cities[city_id]
        if (
            not isinstance(city, dict)
            or city.get("state") != "complete"
            or city.get("total") != expected_total
            or city.get("completed") != expected_total
            or city.get("running") != 0
            or city.get("failed") != 0
        ):
            raise PortableSentinelReturnError(
                f"Sentinel city completion is invalid for {city_id}."
            )

    city_commits: dict[str, str] = {}
    component_root = "data/processed/multicity/portable_predictors/components/sentinel"
    for city_id, acquisition_count in EXPECTED_ACQUISITIONS.items():
        directory = f"{component_root}/{city_id}"
        complete_path = f"{directory}/{CITY_COMPLETE_FILENAME}"
        complete = read_json(complete_path, f"{city_id} completion record")
        commit = _committed(complete, label=f"{city_id} completion")
        if (
            complete.get("state") != "complete"
            or complete.get("algorithm_version")
            != "portable-four-city-sentinel-v1"
            or complete.get("city_id") != city_id
            or complete.get("physical_acquisition_count") != acquisition_count
            or complete.get("access_contract", {}).get(
                "external_target_or_qa_values_read"
            )
            is not False
            or complete.get("access_contract", {}).get(
                "model_fit_or_prediction_performed"
            )
            is not False
        ):
            raise PortableSentinelReturnError(
                f"Invalid scientific completion record for {city_id}."
            )
        outputs = complete.get("outputs")
        if not isinstance(outputs, dict) or set(outputs) != set(CITY_OUTPUTS):
            raise PortableSentinelReturnError(f"Missing output locks for {city_id}.")
        for name in CITY_OUTPUTS:
            path = f"{directory}/{name}"
            if not _record_matches(records, path, outputs.get(name)):
                raise PortableSentinelReturnError(
                    f"Returned output lock does not match {path}."
                )
        city_commits[city_id] = commit

    final_path = FINAL_COMPLETE.as_posix()
    final = read_json(final_path, "final 46-feature completion record")
    final_commit = _committed(final, label="final 46-feature completion")
    if (
        final.get("state") != "complete_target_blind_46_feature_predictors"
        or final.get("algorithm_version") != "portable-four-city-sentinel-v1"
        or final.get("city_count") != 4
        or final.get("row_count") != EXPECTED_ROWS
        or final.get("feature_count") != 46
        or final.get("city_complete_commits") != city_commits
        or final.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or final.get("access_contract", {}).get("model_fit_or_prediction_performed")
        is not False
        or not _record_matches(records, FINAL_OUTPUT.as_posix(), final.get("output"))
    ):
        raise PortableSentinelReturnError("Final 46-feature completion record is invalid.")
    return status, city_commits, final_commit


def _validate_completed_outputs(
    archive: ZipFile,
    members: dict[str, ZipInfo],
    records: dict[str, dict[str, object]],
) -> tuple[dict[str, Any], dict[str, str], str]:
    return _validate_completed_payloads(
        lambda path, label: _read_member_json(
            archive, members, path, label=label
        ),
        records,
    )


def _read_checksum(checksum_path: Path, archive_name: str) -> str:
    if not checksum_path.is_file():
        raise PortableSentinelReturnError(
            f"Companion checksum file is missing: {checksum_path}"
        )
    fields = checksum_path.read_text(encoding="utf-8-sig").strip().split()
    if not fields or _SHA256.fullmatch(fields[0].lower()) is None:
        raise PortableSentinelReturnError("Invalid companion checksum file.")
    if len(fields) > 1 and fields[1].lstrip("*") != archive_name:
        raise PortableSentinelReturnError("Checksum filename does not match the ZIP.")
    return fields[0].lower()


def _verify_archive(
    archive_path: Path,
    checksum_path: Path,
    project_root: Path,
) -> _VerifiedPackage:
    expected_archive_sha = _read_checksum(checksum_path, archive_path.name)
    observed_archive_sha = sha256_file(archive_path)
    if observed_archive_sha != expected_archive_sha:
        raise PortableSentinelReturnError("Returned ZIP SHA-256 does not match.")
    try:
        with ZipFile(archive_path, "r") as archive:
            members = _archive_members(archive)
            result_info = members.get(RESULT_MANIFEST_NAME)
            if result_info is None:
                raise PortableSentinelReturnError("Returned ZIP lacks RESULT_MANIFEST.json.")
            result_raw = archive.read(result_info)
            result_manifest = _read_json_bytes(result_raw, label="result manifest")
            records = _manifest_records(result_manifest)
            if set(members) != {*records, RESULT_MANIFEST_NAME}:
                raise PortableSentinelReturnError(
                    "ZIP contents do not exactly match RESULT_MANIFEST.json."
                )
            for path, record in records.items():
                info = members[path]
                if (
                    info.file_size != record["bytes"]
                    or _sha256_member(archive, info) != record["sha256"]
                ):
                    raise PortableSentinelReturnError(
                        f"Returned file failed size/SHA-256 verification: {path}"
                    )
            if (
                result_manifest.get("source_state") != "complete"
                or result_manifest.get("source_status") != STATUS_PATH
            ):
                raise PortableSentinelReturnError(
                    "Result package was not created from a completed Sentinel run."
                )
            bundle_info = members.get(SOURCE_BUNDLE_MANIFEST)
            local_bundle = (
                project_root
                / "exports/GAMING_LAPTOP_SENTINEL"
                / SOURCE_BUNDLE_MANIFEST
            )
            if bundle_info is None or not local_bundle.is_file():
                raise PortableSentinelReturnError("Source bundle manifest is unavailable.")
            bundle_sha = _sha256_member(archive, bundle_info)
            if bundle_sha != sha256_file(local_bundle):
                raise PortableSentinelReturnError(
                    "Returned result came from a different portable bundle."
                )
            status, city_commits, final_commit = _validate_completed_outputs(
                archive, members, records
            )
            return _VerifiedPackage(
                archive_sha256=observed_archive_sha,
                source_state="complete",
                records=records,
                members=members,
                packaged_bytes=sum(int(row["bytes"]) for row in records.values()),
                status=status,
                city_commits=city_commits,
                final_commit=final_commit,
                result_manifest_sha256=_sha256_bytes(result_raw),
                bundle_manifest_sha256=bundle_sha,
            )
    except BadZipFile as error:
        raise PortableSentinelReturnError("Returned file is not a valid ZIP archive.") from error


def _should_import(path: str) -> bool:
    parts = PurePosixPath(path).parts
    runtime = PurePosixPath(_IMPORT_PREFIXES[0]).parts
    is_acquisition_checkpoint = (
        len(parts) == len(runtime) + 4
        and parts[: len(runtime)] == runtime
        and parts[len(runtime)] in EXPECTED_CITY_TOTALS
        and parts[len(runtime) + 1] == "by_acquisition"
        and parts[-1] in {"summary.json", "acquisition_tract.parquet"}
    )
    return bool(
        path in _IMPORT_FILES
        or is_acquisition_checkpoint
        or path.startswith(_IMPORT_PREFIXES[1:])
        or path.startswith(_IMPORT_PREFIXES[3])
    )


def _require_destination_inside_project(project_root: Path, destination: Path) -> None:
    """Resolve existing links/junctions before any destination-side write."""

    root = project_root.resolve(strict=True)
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise PortableSentinelReturnError(
            f"Import destination is outside the project root: {destination}"
        ) from error

    cursor = destination.parent
    while not cursor.exists() and not cursor.is_symlink():
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    try:
        resolved_parent = cursor.resolve(strict=cursor.exists())
        resolved_parent.relative_to(root)
    except (OSError, ValueError) as error:
        raise PortableSentinelReturnError(
            "Import destination parent resolves outside the project root: "
            f"{destination.parent}"
        ) from error

    if destination.exists() or destination.is_symlink():
        try:
            resolved_destination = destination.resolve(strict=destination.exists())
            resolved_destination.relative_to(root)
        except (OSError, ValueError) as error:
            raise PortableSentinelReturnError(
                "Existing import destination resolves outside the project root: "
                f"{destination}"
            ) from error


def _preflight_member_destinations(
    project_root: Path,
    package: _VerifiedPackage,
) -> tuple[list[str], int]:
    pending: list[str] = []
    unchanged = 0
    conflicts: list[str] = []
    for path in sorted(package.records):
        if not _should_import(path):
            continue
        destination = project_root / Path(*PurePosixPath(path).parts)
        _require_destination_inside_project(project_root, destination)
        record = package.records[path]
        if not destination.exists():
            pending.append(path)
        elif (
            destination.is_file()
            and destination.stat().st_size == record["bytes"]
            and sha256_file(destination) == record["sha256"]
        ):
            unchanged += 1
        else:
            conflicts.append(path)
    if conflicts:
        shown = ", ".join(conflicts[:5])
        suffix = " ..." if len(conflicts) > 5 else ""
        raise PortableSentinelReturnError(
            "Import would overwrite existing different data: " + shown + suffix
        )
    return pending, unchanged


def _install_members(
    archive_path: Path,
    project_root: Path,
    package: _VerifiedPackage,
) -> tuple[int, int]:
    imported = 0
    pending, unchanged = _preflight_member_destinations(project_root, package)
    with ZipFile(archive_path, "r") as archive:
        for path in pending:
            destination = project_root / Path(*PurePosixPath(path).parts)
            record = package.records[path]
            _require_destination_inside_project(project_root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _require_destination_inside_project(project_root, destination)
            temporary = destination.with_name(
                f".{destination.name}.return-{uuid.uuid4().hex}.tmp"
            )
            try:
                with archive.open(package.members[path], "r") as source:
                    with temporary.open("wb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                if (
                    temporary.stat().st_size != record["bytes"]
                    or sha256_file(temporary) != record["sha256"]
                ):
                    raise PortableSentinelReturnError(
                        f"Copied file changed during import: {path}"
                    )
                # The complete preflight above guarantees this is a new path.
                # os.link is an atomic create-if-absent operation, so a concurrent
                # writer can never be overwritten between preflight and publish.
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            imported += 1
    return imported, unchanged


def _authenticate_imported_outputs(project_root: Path) -> None:
    contexts, _ = prepare_contexts(project_root)
    incomplete = [
        city_id
        for city_id, context in contexts.items()
        if not city_output_is_current(context)
    ]
    if incomplete or not final_output_is_current(project_root, contexts):
        detail = ", ".join(incomplete) if incomplete else "final merge"
        raise PortableSentinelReturnError(
            f"Imported outputs failed canonical authentication: {detail}."
        )


def _write_return_receipt(
    project_root: Path,
    *,
    source_kind: str,
    source_path: str,
    archive: str | None,
    archive_sha256: str | None,
    result_manifest_sha256: str | None,
    source_bundle_manifest_sha256: str,
    city_complete_commits: dict[str, str],
    final_complete_commit_sha256: str,
    imported_file_count: int,
    unchanged_file_count: int,
) -> Path:
    """Publish the common complete-return contract for ZIP and directory inputs."""

    receipt_path = project_root / RECEIPT_PATH
    _require_destination_inside_project(project_root, receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _require_destination_inside_project(project_root, receipt_path)
    receipt_payload: dict[str, Any] = {
        "schema_version": 1,
        "state": "complete_verified_portable_sentinel_return",
        "returned_source": {
            "kind": source_kind,
            "path": source_path,
        },
        "archive": archive,
        "archive_sha256": archive_sha256,
        "result_manifest_sha256": result_manifest_sha256,
        "source_bundle_manifest_sha256": source_bundle_manifest_sha256,
        "completed_work_units": EXPECTED_TOTAL,
        "city_complete_commits": city_complete_commits,
        "final_complete_commit_sha256": final_complete_commit_sha256,
        "final_predictor_path": FINAL_OUTPUT.as_posix(),
        "imported_file_count": imported_file_count,
        "unchanged_file_count": unchanged_file_count,
        "access_contract": {
            "external_target_or_qa_values_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "lock_multicity_evaluation_protocol",
    }
    receipt_payload["commit_sha256"] = canonical_sha256(receipt_payload)
    atomic_json(receipt_payload, receipt_path)
    return receipt_path


def verify_and_import_portable_sentinel_results(
    archive_path: str | Path,
    project_root: str | Path,
    *,
    checksum_path: str | Path | None = None,
    verify_only: bool = False,
) -> PortableSentinelReturnSummary:
    """Verify the returned ZIP and optionally install its resumable outputs."""

    archive = Path(archive_path).resolve()
    root = Path(project_root).resolve()
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise PortableSentinelReturnError("--archive must point to the returned ZIP.")
    if not root.is_dir():
        raise PortableSentinelReturnError(f"Project root does not exist: {root}")
    checksum = (
        Path(checksum_path).resolve()
        if checksum_path is not None
        else Path(f"{archive}.sha256")
    )
    package = _verify_archive(archive, checksum, root)
    imported_count = 0
    unchanged_count = 0
    receipt: str | None = None
    if not verify_only:
        imported_count, unchanged_count = _install_members(archive, root, package)
        _authenticate_imported_outputs(root)
        _write_return_receipt(
            root,
            source_kind="zip_archive",
            source_path=str(archive),
            archive=archive.name,
            archive_sha256=package.archive_sha256,
            result_manifest_sha256=package.result_manifest_sha256,
            source_bundle_manifest_sha256=package.bundle_manifest_sha256,
            city_complete_commits=package.city_commits,
            final_complete_commit_sha256=package.final_commit,
            imported_file_count=imported_count,
            unchanged_file_count=unchanged_count,
        )
        receipt = RECEIPT_PATH.as_posix()
    return PortableSentinelReturnSummary(
        archive=str(archive),
        archive_sha256=package.archive_sha256,
        source_state=package.source_state,
        packaged_file_count=len(package.records),
        packaged_bytes=package.packaged_bytes,
        completed_work_units=int(package.status["completed"]),
        imported=not verify_only,
        imported_file_count=imported_count,
        unchanged_file_count=unchanged_count,
        receipt=receipt,
    )
