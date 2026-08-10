"""Read-only validation and additive import of a copied Sentinel work folder."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.multicity import portable_sentinel_build as engine
from la_heat.multicity.portable_predictor_components import CITY_IDS, load_city_support
from la_heat.multicity.portable_sentinel_return import (
    EXPECTED_ACQUISITIONS,
    EXPECTED_CITY_TOTALS,
    EXPECTED_TOTAL,
    RECEIPT_PATH,
    RESULT_MANIFEST_NAME,
    SOURCE_BUNDLE_MANIFEST,
    STATUS_PATH,
    PortableSentinelReturnError,
    _manifest_records,
    _require_destination_inside_project,
    _safe_relative_path,
    _should_import,
    _validate_completed_payloads,
    _write_return_receipt,
)
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_feature_builder import _acquisition_cache_directory

IMPORT_STATUS_PATH: Final = engine.RUNTIME_ROOT / "RETURN_IMPORT_STATUS.json"
_SAFE_PARTIAL_STATES: Final = {
    "blocked",
    "failed",
    "incomplete",
    "incomplete_with_failures",
    "paused",
    "ready",
}


@dataclass(frozen=True, slots=True)
class PortableSentinelDirectoryReturnSummary:
    source_directory: str
    source_kind: str
    source_state: str
    completed_work_units: int
    total_work_units: int
    resumable_acquisition_count: int
    complete_city_count: int
    scientifically_complete: bool
    imported: bool
    imported_file_count: int
    unchanged_file_count: int
    validated_file_count: int
    validated_bytes: int
    checkpoint: str | None
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _DirectoryAudit:
    source_kind: str
    state: str
    status: dict[str, Any]
    records: dict[str, dict[str, object]]
    acquisition_counts: dict[str, int]
    complete_cities: tuple[str, ...]
    scientifically_complete: bool
    bundle_manifest_sha256: str
    result_manifest_sha256: str | None
    city_complete_commits: dict[str, str]
    final_complete_commit_sha256: str | None


@dataclass(frozen=True, slots=True)
class CanonicalPortableSentinelCompletion:
    completed_work_units: int
    total_work_units: int
    resumable_acquisition_count: int
    complete_city_count: int
    scientifically_complete: bool
    city_complete_commits: dict[str, str]
    final_complete_commit_sha256: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PortableSentinelReturnError(f"Invalid {label}: {path}") from error
    if not isinstance(payload, dict):
        raise PortableSentinelReturnError(f"{label} must be a JSON object.")
    return payload


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _source_file_record(source: Path, path: Path) -> dict[str, object]:
    try:
        relative = path.resolve(strict=True).relative_to(source)
    except (FileNotFoundError, ValueError) as error:
        raise PortableSentinelReturnError(
            f"Returned checkpoint is missing or outside the copied folder: {path}"
        ) from error
    if path.is_symlink() or not path.is_file():
        raise PortableSentinelReturnError(f"Returned checkpoint is not a regular file: {path}")
    return {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _add_record(
    records: dict[str, dict[str, object]],
    source: Path,
    path: Path,
) -> None:
    record = _source_file_record(source, path)
    relative = str(record["path"])
    previous = records.get(relative)
    if previous is not None and previous != record:
        raise PortableSentinelReturnError(f"Inconsistent returned file lock: {relative}")
    records[relative] = record


def _resolve_source(value: str | Path) -> Path:
    source = Path(value).resolve()
    if not source.is_dir():
        raise PortableSentinelReturnError(
            "--source-directory must point to a copied result or portable-project folder."
        )
    if (source / SOURCE_BUNDLE_MANIFEST).is_file():
        return source
    candidates = [
        child
        for child in source.iterdir()
        if child.is_dir() and (child / SOURCE_BUNDLE_MANIFEST).is_file()
    ]
    if len(candidates) != 1:
        raise PortableSentinelReturnError(
            "The selected folder must contain exactly one portable Sentinel result root."
        )
    return candidates[0].resolve()


def _validate_bundle_identity(source: Path, project_root: Path) -> str:
    returned = source / SOURCE_BUNDLE_MANIFEST
    expected = (
        project_root
        / "exports"
        / "GAMING_LAPTOP_SENTINEL"
        / SOURCE_BUNDLE_MANIFEST
    )
    if not returned.is_file() or not expected.is_file():
        raise PortableSentinelReturnError("Source bundle manifest is unavailable.")
    returned_sha = sha256_file(returned)
    if returned_sha != sha256_file(expected):
        raise PortableSentinelReturnError(
            "Copied results came from a different portable Sentinel bundle."
        )
    manifest = _read_json(returned, label="source bundle manifest")
    files = manifest.get("files")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("file_count") != 3410
        or manifest.get("total_bytes") != 370_521_440
        or not isinstance(files, list)
        or len(files) != 3410
        or sum(
            int(row.get("bytes", -1))
            for row in files
            if isinstance(row, dict)
        )
        != 370_521_440
    ):
        raise PortableSentinelReturnError("Source bundle manifest counts changed.")
    return returned_sha


def _validate_safe_status(source: Path) -> tuple[dict[str, Any], str]:
    status = _read_json(source / STATUS_PATH, label="Sentinel runtime status")
    state = str(status.get("state", "")).casefold()
    if state not in {*_SAFE_PARTIAL_STATES, "complete"}:
        raise PortableSentinelReturnError(
            "Copied run is not safely paused; use Safe Pause and wait for running=0."
        )
    if (
        status.get("algorithm_version") != engine.RUNNER_VERSION
        or status.get("total") != EXPECTED_TOTAL
        or not isinstance(status.get("completed"), int)
        or not 0 <= int(status["completed"]) <= EXPECTED_TOTAL
        or status.get("running") != 0
        or status.get("current") != []
    ):
        raise PortableSentinelReturnError("Returned runtime status is inconsistent.")
    cities = status.get("cities")
    if not isinstance(cities, dict) or set(cities) != set(EXPECTED_CITY_TOTALS):
        raise PortableSentinelReturnError("Returned city status set is invalid.")
    for city_id, expected_total in EXPECTED_CITY_TOTALS.items():
        city = cities[city_id]
        if (
            not isinstance(city, dict)
            or city.get("total") != expected_total
            or not isinstance(city.get("completed"), int)
            or not 0 <= int(city["completed"]) <= expected_total
            or city.get("running") != 0
        ):
            raise PortableSentinelReturnError(
                f"Returned city status is inconsistent for {city_id}."
            )
    return status, state


def _read_only_source_contexts(
    source: Path,
    project_root: Path,
) -> dict[str, engine.CityBuildContext]:
    pipeline_sha, pipeline = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=engine.PIPELINE_FILES,
        algorithm_version=engine.RUNNER_VERSION,
    )
    returned_pipeline = _read_json(
        source / engine.RUNTIME_ROOT / "pipeline_fingerprint.json",
        label="pipeline fingerprint",
    )
    if returned_pipeline != pipeline or canonical_sha256(returned_pipeline) != pipeline_sha:
        raise PortableSentinelReturnError("Returned Sentinel pipeline fingerprint changed.")
    contexts: dict[str, engine.CityBuildContext] = {}
    for city_id in engine.PROCESS_ORDER:
        inventory, target_dates_sha = engine._load_inventory(project_root, city_id)
        support = load_city_support(project_root, city_id)
        stage = engine._stage_for_city(
            project_root, city_id, str(inventory.summary["local_timezone"])
        )
        target_dates = tuple(
            sorted(inventory.membership["target_date"].astype(str).unique())
        )
        spatial = engine._fixed_spatial_support(support, target_dates=target_dates)
        contexts[city_id] = engine.CityBuildContext(
            city_id=city_id,
            inventory=inventory,
            support=support,
            spatial=spatial,
            stage=stage,
            base_lock={
                "portable_sentinel_pipeline_sha256": pipeline_sha,
                "portable_sentinel_stage_sha256": stage.sha256,
                "target_dates_sha256": target_dates_sha,
                **inventory.locks,
                **spatial.locks,
            },
            runtime_directory=source / engine.RUNTIME_ROOT / city_id,
            output_directory=source / engine.SENTINEL_COMPONENT_ROOT / city_id,
            metadata_directory=source / engine.RAW_METADATA_ROOT / city_id,
        )
    return contexts


def _collect_checkpoint_records(
    source: Path,
    project_root: Path,
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, int],
    tuple[str, ...],
    bool,
]:
    contexts = _read_only_source_contexts(source, project_root)
    records: dict[str, dict[str, object]] = {}
    acquisition_counts: dict[str, int] = {}
    complete_cities: list[str] = []
    for city_id, context in contexts.items():
        count = 0
        expected_directories: set[Path] = set()
        for row in context.inventory.acquisitions.itertuples(index=False):
            physical_id = str(row.physical_acquisition_id)
            cache = _acquisition_cache_directory(context.runtime_directory, physical_id)
            expected_directories.add(cache.resolve())
            summary_path = cache / "summary.json"
            if not summary_path.exists():
                continue
            if not engine.acquisition_cache_is_current(source, context, row):
                raise PortableSentinelReturnError(
                    "Returned acquisition checkpoint failed authentication: "
                    f"{city_id}/{physical_id}"
                )
            summary = _read_json(summary_path, label="acquisition checkpoint")
            _add_record(records, source, summary_path)
            _add_record(records, source, cache / "acquisition_tract.parquet")
            for raw_metadata in summary.get("product_metadata", []):
                if not isinstance(raw_metadata, dict):
                    raise PortableSentinelReturnError("Invalid product-metadata checkpoint.")
                relative = _safe_relative_path(
                    raw_metadata.get("product_metadata_path"), label="metadata"
                )
                if not relative.startswith(engine.RAW_METADATA_ROOT.as_posix() + "/"):
                    raise PortableSentinelReturnError("Unexpected metadata checkpoint path.")
                _add_record(
                    records,
                    source,
                    source / Path(*PurePosixPath(relative).parts),
                )
            count += 1
        by_acquisition = context.runtime_directory / "by_acquisition"
        if by_acquisition.is_dir():
            unknown = [
                path.parent
                for path in by_acquisition.glob("*/summary.json")
                if path.parent.resolve() not in expected_directories
            ]
            if unknown:
                raise PortableSentinelReturnError("Returned run has an unknown acquisition cache.")
        acquisition_counts[city_id] = count
        completion = context.output_directory / engine.CITY_COMPLETE_FILENAME
        if completion.exists():
            if not engine.city_output_is_current(context):
                raise PortableSentinelReturnError(
                    f"Returned city completion failed authentication: {city_id}"
                )
            for name in (*engine.CITY_OUTPUTS, engine.CITY_COMPLETE_FILENAME):
                _add_record(records, source, context.output_directory / name)
            complete_cities.append(city_id)
    final_manifest = source / engine.FINAL_COMPLETE
    final_complete = final_manifest.exists()
    if final_complete:
        if not engine.final_output_is_current(source, contexts):
            raise PortableSentinelReturnError("Returned final merge failed authentication.")
        _add_record(records, source, source / engine.FINAL_OUTPUT)
        _add_record(records, source, final_manifest)
    return records, acquisition_counts, tuple(sorted(complete_cities)), final_complete


def _validate_manifest_directory(
    source: Path,
) -> tuple[dict[str, dict[str, object]], str]:
    manifest_path = source / RESULT_MANIFEST_NAME
    manifest = _read_json(manifest_path, label="result manifest")
    records = _manifest_records(manifest)
    observed: dict[str, Path] = {}
    folded: set[str] = set()
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        record = _source_file_record(source, path)
        relative = str(record["path"])
        key = relative.casefold()
        if key in folded:
            raise PortableSentinelReturnError(f"Duplicate returned path: {relative}")
        folded.add(key)
        observed[relative] = path
    if set(observed) != {*records, RESULT_MANIFEST_NAME}:
        raise PortableSentinelReturnError(
            "Copied directory contents do not exactly match RESULT_MANIFEST.json."
        )
    for relative, expected in records.items():
        if _source_file_record(source, observed[relative]) != expected:
            raise PortableSentinelReturnError(
                f"Returned file failed size/SHA-256 verification: {relative}"
            )
    if manifest.get("source_status") != STATUS_PATH:
        raise PortableSentinelReturnError("Result manifest status path changed.")
    return records, sha256_file(manifest_path)


def _audit_directory(source: Path, project_root: Path) -> _DirectoryAudit:
    bundle_sha = _validate_bundle_identity(source, project_root)
    status, state = _validate_safe_status(source)
    manifest_records: dict[str, dict[str, object]] | None = None
    result_sha: str | None = None
    if (source / RESULT_MANIFEST_NAME).is_file():
        manifest_records, result_sha = _validate_manifest_directory(source)
        source_kind = "result_manifest_directory"
    else:
        source_kind = "copied_portable_project"
    records, acquisitions, cities, final_complete = _collect_checkpoint_records(
        source, project_root
    )
    authenticated_completed = sum(acquisitions.values()) + len(cities) + int(final_complete)
    if authenticated_completed != status["completed"]:
        raise PortableSentinelReturnError(
            "Status completion count does not match authenticated checkpoints."
        )
    for city_id in CITY_IDS:
        expected = acquisitions[city_id] + int(city_id in cities)
        if status["cities"][city_id]["completed"] != expected:
            raise PortableSentinelReturnError(
                f"City completion count does not match checkpoints: {city_id}."
            )
    complete = state == "complete"
    city_commits: dict[str, str] = {}
    final_commit: str | None = None
    if complete:
        if not final_complete or authenticated_completed != EXPECTED_TOTAL:
            raise PortableSentinelReturnError("Complete return lacks all 516 work units.")
        validation_records = manifest_records if manifest_records is not None else records
        _, city_commits, final_commit = _validate_completed_payloads(
            lambda relative, label: _read_json(source / relative, label=label),
            validation_records,
        )
    elif final_complete:
        raise PortableSentinelReturnError("Partial return unexpectedly has a final merge.")
    if manifest_records is not None:
        for relative, record in records.items():
            if manifest_records.get(relative) != record:
                raise PortableSentinelReturnError(
                    f"Checkpoint lock differs from result manifest: {relative}"
                )
    return _DirectoryAudit(
        source_kind=source_kind,
        state=state,
        status=status,
        records=records,
        acquisition_counts=acquisitions,
        complete_cities=cities,
        scientifically_complete=complete,
        bundle_manifest_sha256=bundle_sha,
        result_manifest_sha256=result_sha,
        city_complete_commits=city_commits,
        final_complete_commit_sha256=final_commit,
    )


def _preflight(
    project_root: Path,
    records: dict[str, dict[str, object]],
) -> tuple[list[str], int]:
    pending: list[str] = []
    unchanged = 0
    conflicts: list[str] = []
    for relative, record in sorted(records.items()):
        if not _should_import(relative):
            raise PortableSentinelReturnError(f"Unexpected checkpoint path: {relative}")
        destination = project_root / Path(*PurePosixPath(relative).parts)
        _require_destination_inside_project(project_root, destination)
        if not destination.exists():
            pending.append(relative)
        elif (
            destination.is_file()
            and destination.stat().st_size == record["bytes"]
            and sha256_file(destination) == record["sha256"]
        ):
            unchanged += 1
        else:
            conflicts.append(relative)
    if conflicts:
        raise PortableSentinelReturnError(
            "Import would overwrite existing different data: "
            + ", ".join(conflicts[:5])
        )
    return pending, unchanged


def _install(
    source: Path,
    project_root: Path,
    records: dict[str, dict[str, object]],
) -> tuple[int, int]:
    pending, unchanged = _preflight(project_root, records)
    imported = 0
    for relative in pending:
        record = records[relative]
        source_path = source / Path(*PurePosixPath(relative).parts)
        if _source_file_record(source, source_path) != record:
            raise PortableSentinelReturnError(f"Source changed after validation: {relative}")
        destination = project_root / Path(*PurePosixPath(relative).parts)
        _require_destination_inside_project(project_root, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _require_destination_inside_project(project_root, destination)
        temporary = destination.with_name(
            f".{destination.name}.return-{uuid.uuid4().hex}.tmp"
        )
        try:
            with source_path.open("rb") as source_stream, temporary.open("xb") as target:
                shutil.copyfileobj(source_stream, target, 1024 * 1024)
            if (
                temporary.stat().st_size != record["bytes"]
                or sha256_file(temporary) != record["sha256"]
            ):
                raise PortableSentinelReturnError(f"Copied file changed: {relative}")
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        imported += 1
    return imported, unchanged


def reauthenticate_canonical_portable_sentinel_completion(
    project_root: str | Path,
) -> CanonicalPortableSentinelCompletion:
    """Authenticate every canonical checkpoint after a resumed dashboard exits."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PortableSentinelReturnError(f"Project root does not exist: {root}")
    contexts, _ = engine.prepare_contexts(root)
    acquisition_count = 0
    complete_cities = 0
    city_commits: dict[str, str] = {}
    for city_id, context in contexts.items():
        expected = EXPECTED_ACQUISITIONS[city_id]
        current = sum(
            engine.acquisition_cache_is_current(root, context, row)
            for row in context.inventory.acquisitions.itertuples(index=False)
        )
        if len(context.inventory.acquisitions) != expected or current > expected:
            raise PortableSentinelReturnError(
                f"Canonical acquisition inventory changed for {city_id}."
            )
        acquisition_count += current
        if engine.city_output_is_current(context):
            completion = _read_json(
                context.output_directory / engine.CITY_COMPLETE_FILENAME,
                label=f"canonical {city_id} completion",
            )
            city_commits[city_id] = str(completion["commit_sha256"])
            complete_cities += 1
    final_commit: str | None = None
    final_complete = engine.final_output_is_current(root, contexts)
    if final_complete:
        final = _read_json(root / engine.FINAL_COMPLETE, label="canonical final completion")
        final_commit = str(final["commit_sha256"])
    completed = acquisition_count + complete_cities + int(final_complete)
    scientifically_complete = bool(
        completed == EXPECTED_TOTAL
        and acquisition_count == sum(EXPECTED_ACQUISITIONS.values())
        and complete_cities == len(EXPECTED_CITY_TOTALS)
        and final_complete
        and set(city_commits) == set(EXPECTED_CITY_TOTALS)
    )
    return CanonicalPortableSentinelCompletion(
        completed_work_units=completed,
        total_work_units=EXPECTED_TOTAL,
        resumable_acquisition_count=acquisition_count,
        complete_city_count=complete_cities,
        scientifically_complete=scientifically_complete,
        city_complete_commits=city_commits,
        final_complete_commit_sha256=final_commit,
    )


def finalize_resumed_portable_sentinel_directory_return(
    project_root: str | Path,
    *,
    completion: CanonicalPortableSentinelCompletion | None = None,
) -> Path:
    """Publish the formal receipt after local resume reaches authenticated 516/516."""

    root = Path(project_root).resolve()
    import_status = _read_json(
        root / IMPORT_STATUS_PATH,
        label="portable Sentinel directory import status",
    )
    recorded = import_status.get("commit_sha256")
    unsigned = dict(import_status)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise PortableSentinelReturnError("Directory import status commit is invalid.")
    if (
        import_status.get("state") != "resume_ready"
        or import_status.get("scientifically_complete") is not False
        or import_status.get("model_fit_or_prediction_performed") is not False
        or not import_status.get("source_directory")
        or import_status.get("source_kind")
        not in {"copied_portable_project", "result_manifest_directory"}
        or not _is_sha256(import_status.get("source_bundle_manifest_sha256"))
        or (
            import_status.get("result_manifest_sha256") is not None
            and not _is_sha256(import_status.get("result_manifest_sha256"))
        )
        or import_status.get("total_work_units") != EXPECTED_TOTAL
        or not isinstance(import_status.get("completed_work_units"), int)
        or not 0 <= int(import_status["completed_work_units"]) < EXPECTED_TOTAL
        or not isinstance(import_status.get("imported_file_count"), int)
        or int(import_status["imported_file_count"]) < 0
        or not isinstance(import_status.get("unchanged_file_count"), int)
        or int(import_status["unchanged_file_count"]) < 0
        or not _is_sha256(import_status.get("checkpoint_set_sha256"))
    ):
        raise PortableSentinelReturnError(
            "Directory import status is not an authenticated resume-ready source."
        )
    canonical = completion or reauthenticate_canonical_portable_sentinel_completion(root)
    if (
        not canonical.scientifically_complete
        or canonical.completed_work_units != EXPECTED_TOTAL
        or set(canonical.city_complete_commits) != set(EXPECTED_CITY_TOTALS)
        or canonical.final_complete_commit_sha256 is None
    ):
        raise PortableSentinelReturnError(
            "Canonical Sentinel outputs are not authenticated at 516/516."
        )
    return _write_return_receipt(
        root,
        source_kind=str(import_status["source_kind"]),
        source_path=str(import_status["source_directory"]),
        archive=None,
        archive_sha256=None,
        result_manifest_sha256=import_status.get("result_manifest_sha256"),
        source_bundle_manifest_sha256=str(
            import_status["source_bundle_manifest_sha256"]
        ),
        city_complete_commits=canonical.city_complete_commits,
        final_complete_commit_sha256=canonical.final_complete_commit_sha256,
        imported_file_count=int(import_status["imported_file_count"]),
        unchanged_file_count=int(import_status["unchanged_file_count"]),
    )


def verify_and_import_portable_sentinel_directory(
    source_directory: str | Path,
    project_root: str | Path,
    *,
    verify_only: bool = False,
) -> PortableSentinelDirectoryReturnSummary:
    """Validate first, then add authenticated checkpoints without overwriting."""

    source = _resolve_source(source_directory)
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PortableSentinelReturnError(f"Project root does not exist: {root}")
    audit = _audit_directory(source, root)  # Deliberately read-only.
    imported = 0
    unchanged = 0
    checkpoint: str | None = None
    if not verify_only:
        imported, unchanged = _install(source, root, audit.records)
        if audit.scientifically_complete:
            canonical = reauthenticate_canonical_portable_sentinel_completion(root)
            if (
                not canonical.scientifically_complete
                or canonical.city_complete_commits != audit.city_complete_commits
                or canonical.final_complete_commit_sha256
                != audit.final_complete_commit_sha256
            ):
                raise PortableSentinelReturnError(
                    "Imported complete directory failed canonical reauthentication."
                )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "state": (
                "scientifically_complete" if audit.scientifically_complete else "resume_ready"
            ),
            "source_directory": str(source),
            "source_kind": audit.source_kind,
            "source_state": audit.state,
            "source_bundle_manifest_sha256": audit.bundle_manifest_sha256,
            "result_manifest_sha256": audit.result_manifest_sha256,
            "completed_work_units": int(audit.status["completed"]),
            "total_work_units": EXPECTED_TOTAL,
            "resumable_acquisition_count": sum(audit.acquisition_counts.values()),
            "complete_cities": list(audit.complete_cities),
            "imported_file_count": imported,
            "unchanged_file_count": unchanged,
            "checkpoint_set_sha256": canonical_sha256(
                [audit.records[path] for path in sorted(audit.records)]
            ),
            "scientifically_complete": audit.scientifically_complete,
            "model_fit_or_prediction_performed": False,
        }
        payload["commit_sha256"] = canonical_sha256(payload)
        import_status_path = root / IMPORT_STATUS_PATH
        _require_destination_inside_project(root, import_status_path)
        import_status_path.parent.mkdir(parents=True, exist_ok=True)
        _require_destination_inside_project(root, import_status_path)
        atomic_json(payload, import_status_path)
        checkpoint = IMPORT_STATUS_PATH.as_posix()
        if audit.scientifically_complete:
            _write_return_receipt(
                root,
                source_kind=audit.source_kind,
                source_path=str(source),
                archive=None,
                archive_sha256=None,
                result_manifest_sha256=audit.result_manifest_sha256,
                source_bundle_manifest_sha256=audit.bundle_manifest_sha256,
                city_complete_commits=audit.city_complete_commits,
                final_complete_commit_sha256=str(
                    audit.final_complete_commit_sha256
                ),
                imported_file_count=imported,
                unchanged_file_count=unchanged,
            )
            checkpoint = RECEIPT_PATH.as_posix()
    next_action = (
        "run predictor readiness audit"
        if audit.scientifically_complete
        else "resume Sentinel dashboard; imported checkpoints are not scientifically complete"
    )
    return PortableSentinelDirectoryReturnSummary(
        source_directory=str(source),
        source_kind=audit.source_kind,
        source_state=audit.state,
        completed_work_units=int(audit.status["completed"]),
        total_work_units=EXPECTED_TOTAL,
        resumable_acquisition_count=sum(audit.acquisition_counts.values()),
        complete_city_count=len(audit.complete_cities),
        scientifically_complete=audit.scientifically_complete,
        imported=not verify_only,
        imported_file_count=imported,
        unchanged_file_count=unchanged,
        validated_file_count=len(audit.records),
        validated_bytes=sum(int(record["bytes"]) for record in audit.records.values()),
        checkpoint=checkpoint,
        next_action=next_action,
    )
