"""Export the authenticated multicity result as a compact, read-only bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from la_heat.multicity.atlas_release import authenticate_atlas_release
from la_heat.multicity.external_evaluation import (
    authenticate_external_evaluation_completion,
)
from la_heat.multicity.external_evaluation_reporting import (
    authenticate_external_evaluation_report,
)
from la_heat.multicity.posthoc_qa_audit import authenticate_posthoc_qa_audit
from la_heat.provenance import canonical_sha256

ALGORITHM_VERSION = "multicity-read-only-evidence-export-v1"
DEFAULT_OUTPUT = Path("exports/MULTICITY_EVALUATION_EVIDENCE")
ARCHIVE_NAME = "MULTICITY_EVALUATION_EVIDENCE"
MANIFEST_NAME = "EVIDENCE_MANIFEST.json"
README_NAME = "README.md"

SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    "protocol_and_stage": (
        "configs/multicity/experiment.toml",
        "configs/research.toml",
        "manifests/multicity/ACTIVE_STAGE.json",
        "manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json",
        "manifests/multicity/evaluation/SPATIAL_BLOCKS.json",
        "manifests/multicity/evaluation/EXTERNAL_PREDICTIONS_COMMITTED.json",
        "manifests/multicity/model/MODEL_FIT_AUTHORIZATION.json",
        "manifests/multicity/model/MODEL_FIT_COMPLETE.json",
        "manifests/multicity/targets/SOURCE_TARGET_AUTHORIZATION.json",
        "manifests/multicity/targets/LA_SOURCE_TARGETS_COMPLETE.json",
        "manifests/multicity/targets/EXTERNAL_TARGET_AUTHORIZATION.json",
        "manifests/multicity/targets/THREE_CITY_EXTERNAL_TARGETS_COMPLETE.json",
        "manifests/multicity/releases/ATLAS_RESULTS_RELEASE.json",
    ),
    "formal_aggregate_evaluation": (
        "data/processed/multicity/external_evaluation/EXTERNAL_EVALUATION_COMPLETE.json",
        "data/processed/multicity/external_evaluation/summary.json",
        "data/processed/multicity/external_evaluation/bootstrap.json",
        "data/processed/multicity/external_evaluation/city_metrics.parquet",
        "data/processed/multicity/external_evaluation/date_metrics.parquet",
        "data/processed/multicity/external_evaluation/risk_coverage.parquet",
    ),
    "authenticated_figures": (
        "data/processed/multicity/external_evaluation_report/EXTERNAL_EVALUATION_EVIDENCE.json",
        "data/processed/multicity/external_evaluation_report/RESULTS.md",
        "data/processed/multicity/external_evaluation_report/external_city_mae.png",
        "data/processed/multicity/external_evaluation_report/predicted_vs_observed.png",
        "data/processed/multicity/external_evaluation_report/error_by_city_date.png",
        "data/processed/multicity/external_evaluation_report/interval_calibration.png",
        "data/processed/multicity/external_evaluation_report/risk_coverage.png",
        "data/processed/multicity/external_evaluation_report/spatial_error_maps.png",
    ),
    "non_confirmatory_posthoc": (
        "reports/tables/multicity_external_posthoc_qa/posthoc_qa_summary.json",
        "reports/tables/multicity_external_posthoc_qa/POSTHOC_QA_REPORT.md",
    ),
    "interpretation_and_handoff": (
        "README.md",
        "docs/COMPETITION_NARRATIVE.md",
        "docs/MULTICITY_RESULTS_INTERPRETATION.md",
        "docs/MULTICITY_METHODS_AND_EVIDENCE.md",
        "docs/PROJECT_HANDOFF.md",
    ),
    "reproduction_code": (
        "pyproject.toml",
        "atlas/app/cities/generated-results.ts",
        "scripts/run_multicity_external_evaluation.py",
        "scripts/build_multicity_external_evaluation_report.py",
        "scripts/publish_multicity_atlas_release.py",
        "scripts/audit_multicity_external_posthoc_qa.py",
        "src/la_heat/multicity/transfer_model.py",
        "src/la_heat/multicity/external_evaluation.py",
        "src/la_heat/multicity/external_evaluation_reporting.py",
        "src/la_heat/multicity/atlas_release.py",
        "src/la_heat/multicity/posthoc_qa_audit.py",
        "src/la_heat/multicity/evidence_export.py",
    ),
}

FORBIDDEN_PATH_PARTS = {
    "scored_rows.parquet",
    "external_predictions_2025.parquet",
    "fitted_transfer_models.joblib",
    "values_opened.json",
    "target_tasks.sqlite",
}
SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer": re.compile(rb"Bearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
    "github_token": re.compile(rb"gh[opusr]_[A-Za-z0-9]{20,}"),
    "aws_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
}


class MulticityEvidenceExportError(RuntimeError):
    """Raised when the evidence package cannot be authenticated or exported."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise MulticityEvidenceExportError(f"Evidence path escapes project: {value}") from error
    return path


def _source_entries() -> Iterable[tuple[str, str]]:
    for group, paths in SOURCE_GROUPS.items():
        for relative in paths:
            yield group, relative


def _authenticate_inputs(root: Path) -> dict[str, str]:
    evaluation = authenticate_external_evaluation_completion(root)
    report = authenticate_external_evaluation_report(root)
    atlas = authenticate_atlas_release(root)
    posthoc = authenticate_posthoc_qa_audit(root)
    return {
        "external_evaluation_commit_sha256": evaluation["commit_sha256"],
        "evaluation_report_commit_sha256": report["commit_sha256"],
        "atlas_release_commit_sha256": atlas["commit_sha256"],
        "posthoc_audit_commit_sha256": posthoc["commit_sha256"],
    }


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _scan_safe(relative: str, data: bytes) -> None:
    lowered_parts = {part.lower() for part in PurePosixPath(relative).parts}
    if lowered_parts & FORBIDDEN_PATH_PARTS:
        raise MulticityEvidenceExportError(f"Forbidden evidence member: {relative}")
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(data):
            raise MulticityEvidenceExportError(
                f"Potential {label} found in evidence member: {relative}"
            )


def _readme(bindings: dict[str, str], git_head: str) -> bytes:
    text = "\n".join(
        (
            "# Multicity Urban Surface-Heat Evidence Package",
            "",
            "This is a byte-verified, read-only export of the completed "
            "Phoenix-Houston-Chicago external evaluation.",
            "",
            "Scientific outcome: **inconclusive_sample_size**. M2 improved the "
            "aggregate equal-city/equal-date MAE by 28.916%, but the prespecified "
            "sample-support and no-city-degradation gates failed, and the reliability "
            "gate failed. Authentication proves evidence integrity; it does not mean "
            "the scientific claim passed.",
            "",
            "The package contains aggregate metrics, date-level metrics, uncertainty "
            "summaries, figures, protocol records, completion records, interpretation, "
            "and the exact evaluation/reporting code. It intentionally excludes "
            "tract-level scored rows, raw/aggregated target tables, predictor rows, "
            "fitted model files, runtime databases, VALUES_OPENED records, credentials, "
            "and signed asset URLs.",
            "",
            f"Repository commit: `{git_head}`",
            f"External evaluation commit: `{bindings['external_evaluation_commit_sha256']}`",
            f"Atlas release commit: `{bindings['atlas_release_commit_sha256']}`",
            f"Post-hoc audit commit: `{bindings['posthoc_audit_commit_sha256']}`",
            "",
            "Start with `repository/docs/MULTICITY_RESULTS_INTERPRETATION.md`. The "
            "post-hoc QA material is explicitly non-confirmatory and never replaces "
            "the frozen formal result.",
            "",
        )
    )
    return text.encode("utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _zip_directory(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(f"{ARCHIVE_NAME}/{relative}", (2026, 8, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(
                info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )


def _verify_archive(output: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive) as handle:
        if handle.testzip() is not None:
            raise MulticityEvidenceExportError("Evidence ZIP failed CRC validation")
        expected = {
            f"{ARCHIVE_NAME}/{path.relative_to(output).as_posix()}": path.read_bytes()
            for path in output.rglob("*")
            if path.is_file()
        }
        if set(handle.namelist()) != set(expected):
            raise MulticityEvidenceExportError("Evidence ZIP member set changed")
        for name, data in expected.items():
            if handle.read(name) != data:
                raise MulticityEvidenceExportError(f"Evidence ZIP member changed: {name}")


def authenticate_multicity_evidence_export(
    project_root: str | Path,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
    input_authenticator: Callable[[Path], dict[str, str]] = _authenticate_inputs,
) -> dict[str, Any]:
    """Authenticate the sources, exported directory, and deterministic ZIP."""

    root = Path(project_root).resolve()
    bindings = input_authenticator(root)
    output = _inside(root, output_directory)
    archive = output.with_suffix(".zip")
    checksum = archive.with_suffix(".zip.sha256")
    try:
        manifest = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MulticityEvidenceExportError("Evidence manifest is unavailable") from error
    observed_commit = manifest.pop("commit_sha256", None)
    expected_commit = canonical_sha256(manifest)
    manifest["commit_sha256"] = observed_commit
    if (
        observed_commit != expected_commit
        or manifest.get("state") != "verified_read_only_multicity_evidence_bundle"
        or manifest.get("input_bindings") != bindings
    ):
        raise MulticityEvidenceExportError("Evidence manifest identity changed")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise MulticityEvidenceExportError("Evidence file inventory is invalid")
    expected_sources = {relative for _group, relative in _source_entries()}
    observed_sources: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise MulticityEvidenceExportError("Evidence file record is invalid")
        relative = record.get("source_relative_path")
        package_path = record.get("package_path")
        if relative is not None:
            observed_sources.add(relative)
            source = _inside(root, relative)
        else:
            source = None
        bundled = output / str(package_path)
        if not bundled.is_file():
            raise MulticityEvidenceExportError(f"Evidence member is missing: {package_path}")
        data = bundled.read_bytes()
        _scan_safe(str(package_path), data)
        if record.get("bytes") != len(data) or record.get("sha256") != _sha256_bytes(data):
            raise MulticityEvidenceExportError(f"Evidence member hash changed: {package_path}")
        if source is not None and source.read_bytes() != data:
            raise MulticityEvidenceExportError(f"Evidence source changed: {relative}")
    if observed_sources != expected_sources:
        raise MulticityEvidenceExportError("Evidence source allowlist changed")
    if not archive.is_file() or not checksum.is_file():
        raise MulticityEvidenceExportError("Evidence ZIP or checksum is missing")
    _verify_archive(output, archive)
    expected_checksum = f"{_sha256_file(archive)}  {archive.name}\n"
    if checksum.read_text(encoding="ascii") != expected_checksum:
        raise MulticityEvidenceExportError("Evidence ZIP checksum changed")
    return manifest


def build_multicity_evidence_export(
    project_root: str | Path,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
    input_authenticator: Callable[[Path], dict[str, str]] = _authenticate_inputs,
    git_identity: Callable[[Path], str] = _git_head,
) -> dict[str, Any]:
    """Build the append-only export after authenticating every formal input."""

    root = Path(project_root).resolve()
    bindings = input_authenticator(root)
    head = git_identity(root)
    output = _inside(root, output_directory)
    archive = output.with_suffix(".zip")
    checksum = archive.with_suffix(".zip.sha256")
    if output.exists() or archive.exists() or checksum.exists():
        raise FileExistsError("Evidence export already exists; use --check-only")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    temporary_archive = archive.with_name(f".{archive.name}.partial")
    records: list[dict[str, Any]] = []
    try:
        for group, relative in _source_entries():
            source = _inside(root, relative)
            if not source.is_file() or source.is_symlink():
                raise MulticityEvidenceExportError(f"Evidence source is unavailable: {relative}")
            data = source.read_bytes()
            _scan_safe(relative, data)
            package_path = f"repository/{PurePosixPath(relative).as_posix()}"
            _write_bytes(temporary / package_path, data)
            records.append(
                {
                    "package_path": package_path,
                    "source_relative_path": relative,
                    "group": group,
                    "bytes": len(data),
                    "sha256": _sha256_bytes(data),
                }
            )
        readme = _readme(bindings, head)
        _write_bytes(temporary / README_NAME, readme)
        records.append(
            {
                "package_path": README_NAME,
                "source_relative_path": None,
                "group": "package_guide",
                "bytes": len(readme),
                "sha256": _sha256_bytes(readme),
            }
        )
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "verified_read_only_multicity_evidence_bundle",
            "repository_git_commit": head,
            "scientific_outcome": "inconclusive_sample_size",
            "input_bindings": bindings,
            "files": sorted(records, key=lambda item: str(item["package_path"])),
            "excluded_classes": [
                "tract-level scored rows and target values",
                "predictor tables and fitted model artifacts",
                "runtime databases, logs, and VALUES_OPENED records",
                "credentials, cookies, tokens, and signed asset URLs",
            ],
        }
        manifest["commit_sha256"] = canonical_sha256(manifest)
        _write_bytes(
            temporary / MANIFEST_NAME,
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        os.rename(temporary, output)
        _zip_directory(output, temporary_archive)
        os.rename(temporary_archive, archive)
        checksum.write_text(
            f"{_sha256_file(archive)}  {archive.name}\n",
            encoding="ascii",
            newline="\n",
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        temporary_archive.unlink(missing_ok=True)
        raise
    return authenticate_multicity_evidence_export(
        root, output_directory=output, input_authenticator=input_authenticator
    )
