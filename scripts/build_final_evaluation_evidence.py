"""Create a byte-preserving, read-only evidence export for the final evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from la_heat.final_evaluation_protocol import (
    authenticate_completed_final_evaluation,
    load_final_evaluation_config,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = ROOT / "exports" / "FINAL_EVALUATION_EVIDENCE"
FINAL_CONFIG = ROOT / "configs" / "final_evaluation_2025.toml"
PRE_UNLOCK_COMMIT = "407aefcb4f54ba9fc6265ea5830b1747b7977ba4"
EXPECTED_COMPLETION_COMMIT = (
    "4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0"
)
EXPECTED_CLAIM_ID = (
    "c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f"
)
MODEL_RUN_ID = (
    "219b7a10e4d42b1d9abf1c482b3ab355525681cac43b9e21e70831b7fb7c6b36"
)
RECOVERY_FILE_SHA256 = {
    "PC_MIRROR_AUDIT.json": (
        "f2b1ff73af92321d15c5fe3e68ac3cb1e5406ebdbe78a443ffaa05fcdbeeabe7"
    ),
    "publish_compat_attempt2_stderr.log": (
        "6aa8f85fee64bfc3a33b8e7a3e62c0cfe6ba36a45a63d9942de0c785ff7c6fc7"
    ),
    "publish_compat_attempt2_stdout.log": (
        "1aef23816d28a450f5180f6e31213581e46c54c5c2e8120f1b4380c7e42fa3d0"
    ),
    "publish_compat_attempt3_stderr.log": (
        "b6d89c9411313a80186f796ee8e16d9830fbfe64cb503f279cd7d55fcb35916f"
    ),
    "publish_compat_attempt3_stdout.log": (
        "47a7fa73777663ea6fb1e8935c2e22725f13e2815bc38ec35201ee880cb95632"
    ),
    "publish_compat_attempt4_stderr.log": (
        "4bafc695699ba7968a7f3431c4797729e57f40908c541d2ca444c79ec95be631"
    ),
    "publish_compat_attempt4_stdout.log": (
        "586d67840a6111098a3595d926ee7f6cca9f1643684ea659c6055efed8a88c16"
    ),
    "publish_compat_attempt5_stderr.log": (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ),
    "publish_compat_attempt5_stdout.log": (
        "9316773d5ae12ff00895ea89c66edb2962828e6071f75c645b19550a951562d4"
    ),
    "publish_compat_stderr.log": (
        "bf752416085c939d45dd95e396f69999f7c20a793144480ae76f238726a6ac3c"
    ),
    "publish_compat_stdout.log": (
        "cc538c68fdbb1df68d0fa1b5c030cfe9294c2a7891ad731d2ebb069405837639"
    ),
    "publish_unlock_compat.py": (
        "503399ed0961a19642fc838967d8b4d4ed11e264be8a087a192af83fc417d4df"
    ),
    "run_pc_mirror_resume.py": (
        "09a459c304975cabbcd5f0f4e54ff3341cf556693acfb88d8d8b082e3f646b40"
    ),
    "status.json": (
        "c42f3a74ec7133eef84089b97bebecf25a30cf84d0a41c0ec15786bd0ae16144"
    ),
    "stderr.log": (
        "2642cb5182d7a8fd17d18527d824dcefcefbd4460fd099cdd10a4c417b1a2fe2"
    ),
    "stdout.log": (
        "0073891a72cb5a07dc10f36a0c3422fce14a1e936782a5f2a146f8c1bc057ac5"
    ),
}


@dataclass(frozen=True)
class EvidenceRecord:
    relative_path: str
    bytes: int
    sha256: str
    source_group: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _safe_package_path(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise RuntimeError(f"Unsafe evidence-relative path: {relative!r}")
    return path


class EvidenceBuilder:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.records: dict[str, EvidenceRecord] = {}

    def _register(self, relative: str, group: str) -> None:
        normalized = _safe_package_path(relative).as_posix()
        if normalized in self.records:
            raise RuntimeError(f"Duplicate evidence path: {normalized}")
        path = self.staging.joinpath(*PurePosixPath(normalized).parts)
        self.records[normalized] = EvidenceRecord(
            relative_path=normalized,
            bytes=path.stat().st_size,
            sha256=_sha256(path),
            source_group=group,
        )

    def copy(self, source: Path, relative: str, group: str) -> None:
        package_path = _safe_package_path(relative)
        resolved = source.resolve()
        if source.is_symlink() or not resolved.is_file():
            raise FileNotFoundError(resolved)
        destination = self.staging.joinpath(*package_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, destination)
        if (
            destination.stat().st_size != resolved.stat().st_size
            or _sha256(destination) != _sha256(resolved)
        ):
            raise RuntimeError(f"Evidence copy changed bytes: {resolved}")
        self._register(relative, group)

    def write(self, content: bytes, relative: str, group: str) -> None:
        package_path = _safe_package_path(relative)
        destination = self.staging.joinpath(*package_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(content)
        self._register(relative, group)

    def copy_tree(
        self,
        source_root: Path,
        package_root: str,
        group: str,
        *,
        files: Iterable[Path] | None = None,
    ) -> None:
        if source_root.is_symlink() or not source_root.is_dir():
            raise FileNotFoundError(source_root)
        paths = (
            sorted(source_root.rglob("*"))
            if files is None
            else sorted(files)
        )
        copied = 0
        for source in paths:
            if source.is_symlink():
                raise RuntimeError(f"Evidence source contains a symlink: {source}")
            if source.is_file():
                relative = (
                    Path(package_root)
                    / source.relative_to(source_root)
                ).as_posix()
                self.copy(source, relative, group)
                copied += 1
        if copied == 0:
            raise RuntimeError(f"Evidence source tree is empty: {source_root}")


def _repository_path(path: Path) -> str:
    return (
        Path("repository") / path.resolve().relative_to(ROOT.resolve())
    ).as_posix()


def _copy_repository_file(
    builder: EvidenceBuilder,
    relative: str,
    group: str,
) -> None:
    source = ROOT / Path(relative)
    builder.copy(source, _repository_path(source), group)


def _validate_cache_set(cache_root: Path) -> list[Path]:
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise RuntimeError("Final target-cache root is absent or unsafe.")
    lock = cache_root / "TARGET_BUILD_LOCK.json"
    overpass_root = cache_root / "by_overpass"
    root_entries = list(cache_root.iterdir())
    if (
        {path.name for path in root_entries}
        != {"TARGET_BUILD_LOCK.json", "by_overpass"}
        or lock.is_symlink()
        or not lock.is_file()
        or overpass_root.is_symlink()
        or not overpass_root.is_dir()
    ):
        raise RuntimeError("Final target-cache root file set changed.")
    overpasses = sorted(path for path in overpass_root.iterdir() if path.is_dir())
    expected_names = {
        "CACHE_COMMIT.json",
        "date_summary.parquet",
        "scene_contributions.parquet",
        "tract_date_qa.parquet",
    }
    if (
        len(list(overpass_root.iterdir())) != len(overpasses)
        or any(path.is_symlink() for path in overpasses)
        or len(overpasses) != 23
    ):
        raise RuntimeError("Expected TARGET_BUILD_LOCK plus 23 overpass caches.")
    files = [lock]
    for directory in overpasses:
        entries = list(directory.iterdir())
        if (
            {path.name for path in entries} != expected_names
            or any(path.is_symlink() or not path.is_file() for path in entries)
        ):
            raise RuntimeError(f"Cache file set changed: {directory.name}")
        files.extend(sorted(entries))
    if len(files) != 93:
        raise RuntimeError(f"Expected 93 cache files, observed {len(files)}.")
    return files


def _validate_recovery_set(recovery_root: Path) -> list[Path]:
    if recovery_root.is_symlink() or not recovery_root.is_dir():
        raise RuntimeError("Public-mirror recovery evidence directory is absent.")
    entries = list(recovery_root.iterdir())
    files = {path.name: path for path in entries if path.is_file()}
    directories = {path.name for path in entries if path.is_dir()}
    if (
        set(files) != set(RECOVERY_FILE_SHA256)
        or not directories.issubset({"__pycache__"})
        or any(path.is_symlink() for path in entries)
    ):
        raise RuntimeError(
            "Public-mirror recovery evidence is not the frozen 16-file set."
        )
    for name, expected_sha256 in RECOVERY_FILE_SHA256.items():
        if _sha256(files[name]) != expected_sha256:
            raise RuntimeError(f"Recovery evidence changed: {name}")
    return [files[name] for name in sorted(files)]


def _write_integrity(builder: EvidenceBuilder) -> None:
    integrity = builder.staging / "integrity"
    integrity.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        builder.records.values(),
        key=lambda record: record.relative_path,
    )
    inventory = integrity / "FILES.csv"
    with inventory.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "relative_path",
                "bytes",
                "sha256",
                "source_group",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        for record in rows:
            writer.writerow(
                {
                    "relative_path": record.relative_path,
                    "bytes": record.bytes,
                    "sha256": record.sha256,
                    "source_group": record.source_group,
                }
            )
    checksums = {
        record.relative_path: record.sha256 for record in rows
    }
    checksums["integrity/FILES.csv"] = _sha256(inventory)
    checksums_path = integrity / "FILES.sha256"
    checksums_path.write_text(
        "".join(
            f"{digest}  {relative}\n"
            for relative, digest in sorted(checksums.items())
        ),
        encoding="utf-8",
    )
    anchor_path = integrity / "FILES.sha256.sha256"
    anchor_path.write_text(
        f"{_sha256(checksums_path)}  integrity/FILES.sha256\n",
        encoding="utf-8",
    )


def _zip_directory(
    source: Path,
    destination: Path,
    *,
    archive_root_name: str,
) -> None:
    fixed_timestamp = (2026, 7, 27, 0, 0, 0)
    with zipfile.ZipFile(
        destination,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = (
                Path(archive_root_name) / path.relative_to(source)
            ).as_posix()
            info = zipfile.ZipInfo(relative, fixed_timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            with path.open("rb") as source_handle:
                with archive.open(info, mode="w", force_zip64=True) as member:
                    shutil.copyfileobj(
                        source_handle,
                        member,
                        length=1024 * 1024,
                    )


def _verify_zip(
    zip_path: Path,
    *,
    source: Path,
    archive_root_name: str,
) -> None:
    expected_members = {
        (Path(archive_root_name) / path.relative_to(source)).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    with zipfile.ZipFile(zip_path, mode="r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (
            len(names) != len(set(names))
            or set(names) != expected_members
            or any(info.is_dir() for info in infos)
        ):
            raise RuntimeError("ZIP member set differs from the evidence directory.")
        failed_member = archive.testzip()
        if failed_member is not None:
            raise RuntimeError(f"ZIP CRC verification failed: {failed_member}")

    with tempfile.TemporaryDirectory(
        prefix=".FINAL_EVALUATION_EVIDENCE_ZIP_VERIFY.",
        dir=source.parent,
    ) as temporary:
        extraction_root = Path(temporary)
        with zipfile.ZipFile(zip_path, mode="r") as archive:
            archive.extractall(extraction_root)
        extracted = extraction_root / archive_root_name
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(extracted / "integrity" / "verify_evidence.py"),
                os.fspath(extracted),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        verification = json.loads(result.stdout)
        if verification.get("state") != "verified":
            raise RuntimeError("Extracted ZIP failed evidence verification.")
        source_records = {
            path.relative_to(source).as_posix(): (
                path.stat().st_size,
                _sha256(path),
            )
            for path in source.rglob("*")
            if path.is_file()
        }
        extracted_records = {
            path.relative_to(extracted).as_posix(): (
                path.stat().st_size,
                _sha256(path),
            )
            for path in extracted.rglob("*")
            if path.is_file()
        }
        if extracted_records != source_records:
            raise RuntimeError(
                "Extracted ZIP is not byte-identical to the evidence directory."
            )


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(stat.S_IREAD)
        elif path.is_dir():
            path.chmod(stat.S_IREAD | stat.S_IEXEC)
    root.chmod(stat.S_IREAD | stat.S_IEXEC)


def _make_writable(root: Path) -> None:
    if root.is_file():
        root.chmod(stat.S_IREAD | stat.S_IWRITE)
        return
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
            elif path.is_dir():
                path.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        root.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)


def _remove_owned_artifact(path: Path, *, parent: Path) -> None:
    if not path.exists():
        return
    resolved_parent = parent.resolve()
    if path.resolve().parent != resolved_parent:
        raise RuntimeError(f"Refusing unsafe evidence cleanup target: {path}")
    _make_writable(path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _publish_file_no_clobber(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.link(source, destination)
    source.unlink()


def _publish_directory_no_clobber(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.rename(source, destination)


def _verify_bundle_independently(bundle: Path, *, expected_head: str) -> str:
    with tempfile.TemporaryDirectory(
        prefix=".bundle_verify.",
        dir=bundle.parent,
    ) as temporary:
        clone = Path(temporary) / "repository.git"
        cloned = subprocess.run(
            ["git", "clone", "--bare", os.fspath(bundle), os.fspath(clone)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        observed_head = subprocess.run(
            ["git", "-C", os.fspath(clone), "rev-parse", "refs/heads/main"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if observed_head != expected_head:
            raise RuntimeError("Independent bundle clone did not recover main HEAD.")
        fsck = subprocess.run(
            ["git", "-C", os.fspath(clone), "fsck", "--full", "--strict"],
            check=True,
            capture_output=True,
            text=True,
        )
        return (
            cloned.stdout
            + cloned.stderr
            + f"\nrecovered_main={observed_head}\n"
            + fsck.stdout
            + fsck.stderr
        )


def build_evidence(destination: Path) -> dict[str, object]:
    output = destination.resolve()
    zip_path = Path(os.fspath(output) + ".zip")
    zip_hash_path = Path(os.fspath(zip_path) + ".sha256")
    if (
        output == output.parent
        or output.parent == output
        or output.name in {"", ".", ".."}
    ):
        raise RuntimeError("Unsafe evidence destination.")
    if any(path.exists() for path in (output, zip_path, zip_hash_path)):
        raise FileExistsError(
            "Evidence destination, ZIP, and ZIP checksum must all be absent."
        )

    config = load_final_evaluation_config(
        FINAL_CONFIG,
        project_root=ROOT,
    )
    completion = authenticate_completed_final_evaluation(config)
    if (
        completion.get("commit_sha256") != EXPECTED_COMPLETION_COMMIT
        or completion.get("claim_id") != EXPECTED_CLAIM_ID
        or completion.get("completed") is not True
    ):
        raise RuntimeError("Canonical completion identity changed.")

    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    origin_main = _git("rev-parse", "origin/main")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all")
    if branch != "main" or head != origin_main or porcelain:
        raise RuntimeError(
            "Evidence export requires a clean main branch equal to origin/main."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".FINAL_EVALUATION_EVIDENCE.",
            dir=output.parent,
        )
    ).resolve()
    if staging.parent != output.parent or not staging.name.startswith(
        ".FINAL_EVALUATION_EVIDENCE."
    ):
        raise RuntimeError("Unsafe evidence staging path.")
    zip_staging = Path(os.fspath(staging) + ".zip")
    zip_hash_staging = Path(os.fspath(zip_staging) + ".sha256")
    builder = EvidenceBuilder(staging)
    promoted_output = False
    promoted_zip = False
    promoted_zip_hash = False
    try:
        fixed_repository_files = (
            "README.md",
            "configs/final_evaluation_2025.toml",
            "configs/research.toml",
            "docs/DATA_MANIFEST.csv",
            "docs/DECISION_LOG.md",
            "docs/PROJECT_HANDOFF.md",
            "docs/PROJECT_PLAN.md",
            "docs/PROJECT_STATUS.md",
            "reports/FINAL_EVALUATION_REPORT.md",
            "scripts/build_final_evaluation_evidence.py",
            "scripts/verify_final_evaluation_evidence.py",
            "manifests/final_test_2025/AUTHORIZATION.json",
            "manifests/target_inventory/city_boundary.geojson",
            "data/interim/targets/primary_tract_manifest.parquet",
            "data/processed/final_test_2025/predictors/final_predictors.parquet",
            "data/interim/final_model_staging/latest_build.json",
        )
        for relative in fixed_repository_files:
            _copy_repository_file(builder, relative, "frozen_dependency")

        for relative_directory, group in (
            ("manifests/model_lock", "model_lock"),
            (
                "manifests/final_test_2025/evaluation",
                "state_chain",
            ),
            (
                "manifests/final_test_2025/landsat_inventory",
                "frozen_inventory",
            ),
            (
                "manifests/final_test_2025/predictors",
                "frozen_predictors",
            ),
        ):
            source_root = ROOT / relative_directory
            builder.copy_tree(
                source_root,
                f"repository/{relative_directory}",
                group,
            )

        final_root = config.paths["final_output_directory"]
        expected_final = set(completion["exact_output_files"])
        final_entries = list(final_root.iterdir())
        actual_final = {path.name for path in final_entries}
        if (
            actual_final != expected_final
            or len(expected_final) != 21
            or any(path.is_symlink() or not path.is_file() for path in final_entries)
        ):
            raise RuntimeError("Final output is not the exact 21-file set.")
        builder.copy_tree(
            final_root,
            _repository_path(final_root),
            "final_output",
            files=(final_root / name for name in sorted(expected_final)),
        )

        cache_root = (
            ROOT
            / "data"
            / "interim"
            / "final_test_2025"
            / "evaluation"
            / "target_cache"
            / "targets"
        )
        builder.copy_tree(
            cache_root,
            _repository_path(cache_root),
            "target_cache",
            files=_validate_cache_set(cache_root),
        )

        model_run_root = (
            ROOT
            / "data"
            / "interim"
            / "final_model_staging"
            / "runs"
            / MODEL_RUN_ID
        )
        builder.copy_tree(
            model_run_root,
            _repository_path(model_run_root),
            "frozen_models",
        )

        recovery_root = ROOT / "exports" / "PC_MIRROR_RESUME"
        builder.copy_tree(
            recovery_root,
            _repository_path(recovery_root),
            "recovery_audit",
            files=_validate_recovery_set(recovery_root),
        )

        pre_unlock = subprocess.run(
            [
                "git",
                "show",
                f"{PRE_UNLOCK_COMMIT}:configs/research.toml",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        if (
            len(pre_unlock) != 5417
            or hashlib.sha256(pre_unlock).hexdigest()
            != "77d8badff06aa17f3a22c3cf8669c4143f1bf5847868e77eb3372617cfa570db"
        ):
            raise RuntimeError("Pre-unlock research snapshot changed.")
        builder.write(
            pre_unlock,
            (
                f"snapshots/{PRE_UNLOCK_COMMIT}/"
                "configs/research.toml"
            ),
            "pre_unlock_snapshot",
        )

        git_state = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "branch": branch,
            "head": head,
            "origin_main": origin_main,
            "remote_origin": _git("remote", "get-url", "origin"),
            "worktree_porcelain": [],
            "claim_id": completion["claim_id"],
            "completion_commit_sha256": completion["commit_sha256"],
        }
        builder.write(
            _json_bytes(git_state),
            "history/GIT_STATE.json",
            "git_history",
        )
        bundle = staging / "history" / "repository.bundle"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "bundle", "create", str(bundle), "--all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        builder._register("history/repository.bundle", "git_history")
        verification = subprocess.run(
            ["git", "bundle", "verify", str(bundle)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        independent_verification = _verify_bundle_independently(
            bundle,
            expected_head=head,
        )
        builder.write(
            (
                verification.stdout
                + verification.stderr
                + "\n--- independent bare-clone verification ---\n"
                + independent_verification
            ).encode("utf-8"),
            "history/repository_bundle_verify.txt",
            "git_history",
        )

        verifier = ROOT / "scripts" / "verify_final_evaluation_evidence.py"
        builder.copy(
            verifier,
            "integrity/verify_evidence.py",
            "integrity_tool",
        )
        manifest = {
            "schema_version": 1,
            "state": "read_only_final_evaluation_evidence",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "claim_id": completion["claim_id"],
            "completion_commit_sha256": completion["commit_sha256"],
            "repository_git_head": head,
            "final_output_file_count": 21,
            "exact_final_output_files": list(completion["exact_output_files"]),
            "target_cache_file_count": 93,
            "target_cache_commit_count": 23,
            "contains_pre_and_post_unlock_research_config": True,
            "contains_all_recovery_attempt_logs": True,
            "recovery_files_sha256": dict(sorted(RECOVERY_FILE_SHA256.items())),
            "read_only_permissions": (
                "advisory_only; SHA-256 manifests and the external ZIP checksum "
                "are the integrity controls"
            ),
            "remote_asset_byte_equivalence_scope": (
                "The public-mirror audit binds 45 scene identities and 225 "
                "asset filenames; it is not a 225-raster byte mirror."
            ),
        }
        builder.write(
            _json_bytes(manifest),
            "EVIDENCE_MANIFEST.json",
            "evidence_manifest",
        )
        _write_integrity(builder)

        verifier_result = subprocess.run(
            [
                sys.executable,
                os.fspath(
                    staging / "integrity" / "verify_evidence.py"
                ),
                os.fspath(staging),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        _zip_directory(
            staging,
            zip_staging,
            archive_root_name=output.name,
        )
        _verify_zip(
            zip_staging,
            source=staging,
            archive_root_name=output.name,
        )
        zip_digest = _sha256(zip_staging)
        zip_hash_staging.write_text(
            f"{zip_digest}  {zip_path.name}\n",
            encoding="utf-8",
        )
        _make_read_only(staging)
        if any(path.exists() for path in (output, zip_path, zip_hash_path)):
            raise FileExistsError(
                "Evidence publication targets appeared during the build."
            )
        _publish_directory_no_clobber(staging, output)
        promoted_output = True
        _publish_file_no_clobber(zip_staging, zip_path)
        promoted_zip = True
        zip_path.chmod(stat.S_IREAD)
        _publish_file_no_clobber(zip_hash_staging, zip_hash_path)
        promoted_zip_hash = True
        zip_hash_path.chmod(stat.S_IREAD)
        return {
            "state": "complete",
            "destination": os.fspath(output),
            "zip": os.fspath(zip_path),
            "zip_sha256": zip_digest,
            "completion_commit_sha256": completion["commit_sha256"],
            "verification": json.loads(verifier_result.stdout),
        }
    except Exception:
        temporary_artifacts = (
            staging,
            zip_staging,
            zip_hash_staging,
        )
        for artifact in temporary_artifacts:
            _remove_owned_artifact(artifact, parent=output.parent)
        promoted_artifacts = (
            (output, promoted_output),
            (zip_path, promoted_zip),
            (zip_hash_path, promoted_zip_hash),
        )
        for artifact, owned in promoted_artifacts:
            if owned:
                _remove_owned_artifact(artifact, parent=output.parent)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(build_evidence(args.destination), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
