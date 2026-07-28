"""Verify a final-evaluation evidence directory byte for byte and semantically."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

EXPECTED_CLAIM_ID = (
    "c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f"
)
EXPECTED_COMPLETION_COMMIT = (
    "4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0"
)
PRE_UNLOCK_COMMIT = "407aefcb4f54ba9fc6265ea5830b1747b7977ba4"
EXPECTED_PRE_UNLOCK = (
    5417,
    "77d8badff06aa17f3a22c3cf8669c4143f1bf5847868e77eb3372617cfa570db",
)
EXPECTED_POST_UNLOCK = (
    5416,
    "a2d4f7300d8a264c77c3ddc15a730546945a2d7f6ed253ff2f49e352102c60b9",
)
EXPECTED_TARGET_BUILD_LOCK = (
    "480a09b14d5bee2b340478e58790d5e34c4af68ede3792694dd73335d80121d6"
)
EXPECTED_TARGET_BUILD_FILE_SHA256 = (
    "381732985915bde60b8ecff91e59e38c55c34a164cc410430b239fe584d08646"
)
EXPECTED_REMOTE_ORIGIN = "https://github.com/CmsChase/LA-neighborhood-heat.git"
EXPECTED_READ_ONLY_PERMISSIONS = (
    "advisory_only; SHA-256 manifests and the external ZIP checksum "
    "are the integrity controls"
)
EXPECTED_REMOTE_ASSET_BYTE_EQUIVALENCE_SCOPE = (
    "The public-mirror audit binds 45 scene identities and 225 "
    "asset filenames; it is not a 225-raster byte mirror."
)

EXPECTED_OUTPUT_FILES = (
    "blind_predictions.parquet",
    "final_target_qa.parquet",
    "date_summary.parquet",
    "scene_contributions.parquet",
    "evaluation_rows.parquet",
    "model_metrics.csv",
    "per_date_metrics.csv",
    "paired_date_block_errors.csv",
    "crossed_bootstrap.json",
    "protocol_gates.csv",
    "hotspot_per_date.csv",
    "hotspot_summary.csv",
    "sensor_per_date_metrics.csv",
    "sensor_summary.csv",
    "sentinel_stratum_summary.csv",
    "qa_missingness_summary.csv",
    "tract_choropleth_summary.csv",
    "observed_predicted_residual_maps.pdf",
    "per_date_error_and_rank.png",
    "hotspot_precision_recall.png",
    "EVALUATION_COMMIT.json",
)

EXPECTED_CACHE_COMMITS = {
    "landsat-8_20250514T182744Z": (
        "90f3ee5ced51c064fbd8a7350a99129dae04fbe699c0c80b883db275a2b14174"
    ),
    "landsat-8_20250530T182748Z": (
        "4a85944dd8450b77049afe66ebf1da98d6b3f80465b4b78c4a250ff60be353da"
    ),
    "landsat-8_20250615T182758Z": (
        "eb22cd389253148d17ebae2c15ed3e7c3c1af686680fd79b2112fcc3f2c2521e"
    ),
    "landsat-8_20250701T182805Z": (
        "2a27c67a8d69ce0b926a75cc86e762e3c657cfd3624f4ed082a0f5949425c97d"
    ),
    "landsat-8_20250717T182810Z": (
        "7673f79165788ec2394d5ce3f665d24086736d2b00a8f6afbad909b338827733"
    ),
    "landsat-8_20250802T182815Z": (
        "5051ed56ac078322a4fb748b64ae5d1af6944914f988490d7fd8e5e47bd5a72c"
    ),
    "landsat-8_20250818T182823Z": (
        "1b6532ece95873cabc24a21f0fb57532be383fe3b4db090cb4d1a0298de4dc77"
    ),
    "landsat-8_20250903T182826Z": (
        "977148d82fe27e6f57dba83c9910c2e7d007566ea59ad7b5552d0043dc6432e7"
    ),
    "landsat-8_20250919T182833Z": (
        "a11eeb5fca1fbe6a7adfcf7a805ef1d975be8d7e4c4883c5ade63d140067103f"
    ),
    "landsat-8_20251005T182834Z": (
        "7d3bd890439a838357f211f4ecffa75cf6e09196067307e42311a8672adc706d"
    ),
    "landsat-8_20251021T182833Z": (
        "db8f200b42006a963b02f0aaa960e66b58f5372012af9f0e95fb0725512d9f28"
    ),
    "landsat-9_20250506T182800Z": (
        "cdb9c3199dafef3c3fa634eca7b1d8a48509f467a5c7d15b1031ae86d99ad701"
    ),
    "landsat-9_20250522T182750Z": (
        "5dad43ea5811e8bbb8e4b4d0bb963d52110159d7fe0f42f68965aad039f9f8d5"
    ),
    "landsat-9_20250607T182746Z": (
        "cd294c70766ae91b57c22da1637a6717b32523df36d636f849bec5f726d817c5"
    ),
    "landsat-9_20250623T182800Z": (
        "7d10ed8fa2159a263a773bb1ab65b6957eee7747e1a9a60c64f739213e6f6b19"
    ),
    "landsat-9_20250709T182803Z": (
        "f64d4e4b2a551a627ab0cd13d95ae50107a33abfb679e362b9b963dedbbf96fc"
    ),
    "landsat-9_20250725T182812Z": (
        "6024dc7795a9df1ddeeb5ad15772099b9bdfffa8ee91687e446be531745e0228"
    ),
    "landsat-9_20250810T182822Z": (
        "e58510cf2267a3d3d66b933fa5fc28c3e008adfa2fc3029ddcc72229d6b0b900"
    ),
    "landsat-9_20250826T182827Z": (
        "a5e10294889fe3fe939579d58b666024ca26984d4cbb6bb3ba740d48b02f1428"
    ),
    "landsat-9_20250911T182833Z": (
        "f22f057c466edc61a94b61b313b35b29ab3cbb997e417f9908cb98a0d037ea72"
    ),
    "landsat-9_20250927T182836Z": (
        "bf4a3c0e3abf33c06c5f8afba834023b9dda814f1e1a89aa87f316afcadbd6bc"
    ),
    "landsat-9_20251013T182842Z": (
        "c75147dd93a684afc669469f95a408946673bfaf2354e88c155ad6269962c69a"
    ),
    "landsat-9_20251029T182835Z": (
        "b3e03270a4e7033aa10e5d5f870baeaaaf438d6f376898466c078166c8ca5225"
    ),
}

EXPECTED_RECOVERY_SHA256 = {
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

STATE_PATHS = {
    "readiness": (
        "repository/manifests/final_test_2025/evaluation/"
        "EVALUATION_READINESS.json"
    ),
    "authorization": (
        "repository/manifests/final_test_2025/AUTHORIZATION.json"
    ),
    "claim": (
        "repository/manifests/final_test_2025/evaluation/"
        "CONSUMPTION_CLAIM.json"
    ),
    "predictions": (
        "repository/manifests/final_test_2025/evaluation/"
        "PREDICTIONS_FROZEN.json"
    ),
    "values": (
        "repository/manifests/final_test_2025/evaluation/VALUES_OPENED.json"
    ),
    "completion": (
        "repository/manifests/final_test_2025/evaluation/"
        "EVALUATION_COMPLETE.json"
    ),
}

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_GIT_SHA1_PATTERN = re.compile(r"[0-9a-f]{40}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object.")
    return value


def _read_object(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {label}: {path}") from error
    return _as_object(payload, label)


def _verify_commit(payload: Mapping[str, object], label: str) -> str:
    expected = payload.get("commit_sha256")
    if not isinstance(expected, str) or not _SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"{label} has no valid canonical commit.")
    content = dict(payload)
    content.pop("commit_sha256")
    if _canonical_sha256(content) != expected:
        raise RuntimeError(f"{label} canonical commit failed.")
    return expected


def _normalized_relative(relative: str) -> PurePosixPath:
    if (
        not relative
        or "\x00" in relative
        or "\\" in relative
        or ":" in relative
    ):
        raise RuntimeError(f"Unsafe evidence path: {relative!r}")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"Unsafe evidence path: {relative!r}")
    return path


def _evidence_path(root: Path, relative: str) -> Path:
    normalized = _normalized_relative(relative)
    candidate = root.joinpath(*normalized.parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"Evidence path escapes root: {relative}") from error
    return candidate


def _checksum_lines(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if (
            separator != "  "
            or not _SHA256_PATTERN.fullmatch(digest)
            or relative in records
        ):
            raise RuntimeError(f"Invalid checksum line: {line!r}")
        _normalized_relative(relative)
        records[relative] = digest
    return records


def _verify_file_record(
    path: Path,
    record_value: object,
    label: str,
    *,
    expected_record_path: str | None = None,
) -> None:
    record = _as_object(record_value, f"{label} record")
    expected_bytes = record.get("bytes")
    expected_sha256 = record.get("sha256")
    if (
        type(expected_bytes) is not int
        or expected_bytes < 0
        or not isinstance(expected_sha256, str)
        or not _SHA256_PATTERN.fullmatch(expected_sha256)
    ):
        raise RuntimeError(f"{label} has an invalid byte lock.")
    if expected_record_path is not None and record.get("path") != expected_record_path:
        raise RuntimeError(f"{label} records the wrong relative path.")
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"Missing regular evidence file: {label}")
    if path.stat().st_size != expected_bytes or _sha256(path) != expected_sha256:
        raise RuntimeError(f"{label} byte lock failed.")


def _verify_manifest_identity(root: Path) -> dict[str, object]:
    manifest = _read_object(root / "EVIDENCE_MANIFEST.json", "evidence manifest")
    required = {
        "schema_version": 1,
        "state": "read_only_final_evaluation_evidence",
        "claim_id": EXPECTED_CLAIM_ID,
        "completion_commit_sha256": EXPECTED_COMPLETION_COMMIT,
        "final_output_file_count": 21,
        "target_cache_file_count": 93,
        "target_cache_commit_count": 23,
        "contains_pre_and_post_unlock_research_config": True,
        "contains_all_recovery_attempt_logs": True,
        "exact_final_output_files": list(EXPECTED_OUTPUT_FILES),
        "recovery_files_sha256": dict(sorted(EXPECTED_RECOVERY_SHA256.items())),
        "read_only_permissions": EXPECTED_READ_ONLY_PERMISSIONS,
        "remote_asset_byte_equivalence_scope": (
            EXPECTED_REMOTE_ASSET_BYTE_EQUIVALENCE_SCOPE
        ),
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise RuntimeError(f"Evidence manifest identity failed: {key}.")
    repository_git_head = manifest.get("repository_git_head")
    if (
        not isinstance(repository_git_head, str)
        or not _GIT_SHA1_PATTERN.fullmatch(repository_git_head)
    ):
        raise RuntimeError(
            "Evidence manifest identity failed: repository_git_head."
        )
    return manifest


def _verify_git_history(
    root: Path,
    manifest: Mapping[str, object],
) -> str:
    git_state_path = _evidence_path(root, "history/GIT_STATE.json")
    bundle_path = _evidence_path(root, "history/repository.bundle")
    verify_log_path = _evidence_path(
        root,
        "history/repository_bundle_verify.txt",
    )
    required_paths = (git_state_path, bundle_path, verify_log_path)
    if any(not path.is_file() or path.is_symlink() for path in required_paths):
        raise RuntimeError("Git history evidence files are incomplete.")

    git_state = _read_object(git_state_path, "Git state")
    head = git_state.get("head")
    remote_origin = git_state.get("remote_origin")
    if (
        git_state.get("schema_version") != 1
        or git_state.get("branch") != "main"
        or not isinstance(head, str)
        or not _GIT_SHA1_PATTERN.fullmatch(head)
        or git_state.get("origin_main") != head
        or manifest.get("repository_git_head") != head
        or git_state.get("worktree_porcelain") != []
        or git_state.get("claim_id") != EXPECTED_CLAIM_ID
        or git_state.get("completion_commit_sha256")
        != EXPECTED_COMPLETION_COMMIT
        or remote_origin != EXPECTED_REMOTE_ORIGIN
    ):
        raise RuntimeError("Git state identity or clean-main tracking failed.")

    try:
        verify_log = verify_log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeError("Cannot read Git bundle verification log.") from error
    if (
        "--- independent bare-clone verification ---" not in verify_log
        or f"recovered_main={head}" not in verify_log
    ):
        raise RuntimeError("Git bundle verification log does not bind main HEAD.")

    try:
        with tempfile.TemporaryDirectory(
            prefix=".final_evidence_bundle_verify."
        ) as temporary:
            clone = Path(temporary) / "repository.git"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    os.fspath(bundle_path),
                    os.fspath(clone),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            observed_main = subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(clone),
                    "rev-parse",
                    "refs/heads/main",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            observed_head = subprocess.run(
                ["git", "-C", os.fspath(clone), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if observed_main != head or observed_head != head:
                raise RuntimeError(
                    "Independent Git bundle clone did not recover main HEAD."
                )
            if (clone / "objects" / "info" / "alternates").exists():
                raise RuntimeError(
                    "Independent Git bundle clone is not self-contained."
                )
            subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(clone),
                    "fsck",
                    "--full",
                    "--strict",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Independent Git bundle verification failed.") from error
    return head


def _verify_state_chain(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    paths = {
        key: _evidence_path(root, relative)
        for key, relative in STATE_PATHS.items()
    }
    records = {
        key: _read_object(path, f"{key} marker") for key, path in paths.items()
    }
    commits = {
        key: _verify_commit(record, f"{key} marker")
        for key, record in records.items()
    }
    readiness = records["readiness"]
    authorization = records["authorization"]
    claim = records["claim"]
    predictions = records["predictions"]
    values = records["values"]
    completion = records["completion"]

    request = _as_object(readiness.get("request"), "readiness request")
    request_sha256 = readiness.get("request_sha256")
    if (
        readiness.get("state") != "ready_target_blind"
        or readiness.get("target_blind") is not True
        or readiness.get("values_read") is not False
        or request_sha256 != _canonical_sha256(request)
    ):
        raise RuntimeError("Readiness state or request commitment failed.")

    readiness_link = _as_object(
        authorization.get("evaluation_readiness"),
        "authorization readiness link",
    )
    if (
        authorization.get("state")
        != "authorized_for_one_time_2025_evaluation"
        or authorization.get("authorized") is not True
        or authorization.get("values_read") is not False
        or readiness_link.get("commit_sha256") != commits["readiness"]
        or readiness_link.get("request_sha256") != request_sha256
        or readiness_link.get("file_sha256") != _sha256(paths["readiness"])
        or readiness_link.get("bytes") != paths["readiness"].stat().st_size
    ):
        raise RuntimeError("Authorization does not bind readiness.")

    claim_request = _as_object(claim.get("request"), "claim request")
    if (
        claim.get("state") != "claimed_for_single_evaluation"
        or claim.get("claim_id") != EXPECTED_CLAIM_ID
        or claim.get("request_sha256") != EXPECTED_CLAIM_ID
        or _canonical_sha256(claim_request) != EXPECTED_CLAIM_ID
        or claim_request.get("readiness_commit_sha256") != commits["readiness"]
        or claim_request.get("authorization_commit_sha256")
        != commits["authorization"]
    ):
        raise RuntimeError("Consumption claim commitment failed.")

    if (
        predictions.get("state") != "blind_predictions_frozen"
        or predictions.get("claim_id") != EXPECTED_CLAIM_ID
        or predictions.get("claim_commit_sha256") != commits["claim"]
        or predictions.get("target_or_qa_values_read") is not False
    ):
        raise RuntimeError("Frozen predictions do not bind the claim.")

    if (
        values.get("state") != "target_and_qa_values_opened"
        or values.get("claim_id") != EXPECTED_CLAIM_ID
        or values.get("claim_commit_sha256") != commits["claim"]
        or values.get("predictions_commit_sha256") != commits["predictions"]
        or values.get("blind_predictions_frozen") is not True
        or values.get("values_read") is not True
    ):
        raise RuntimeError("Values-opened marker does not bind the blind prediction.")

    if (
        completion.get("state") != "complete_one_time_final_evaluation"
        or completion.get("completed") is not True
        or completion.get("claim_id") != EXPECTED_CLAIM_ID
        or commits["completion"] != EXPECTED_COMPLETION_COMMIT
        or completion.get("claim_commit_sha256") != commits["claim"]
        or completion.get("predictions_commit_sha256") != commits["predictions"]
        or completion.get("values_opened_commit_sha256") != commits["values"]
    ):
        raise RuntimeError("Completion marker identity or state chain failed.")
    return records, commits


def _verify_final_outputs(
    root: Path,
    state_records: Mapping[str, dict[str, object]],
    state_commits: Mapping[str, str],
) -> None:
    output_root = _evidence_path(
        root,
        "repository/data/processed/final_test_2025/final_evaluation",
    )
    if not output_root.is_dir() or output_root.is_symlink():
        raise RuntimeError("Final output directory is missing.")
    entries = list(output_root.iterdir())
    if (
        any(not path.is_file() or path.is_symlink() for path in entries)
        or {path.name for path in entries} != set(EXPECTED_OUTPUT_FILES)
        or len(entries) != 21
    ):
        raise RuntimeError("Final output is not the exact 21-file set.")

    commit_path = output_root / "EVALUATION_COMMIT.json"
    output_commit = _read_object(commit_path, "evaluation output commit")
    output_commit_sha256 = _verify_commit(
        output_commit,
        "evaluation output commit",
    )
    output_records = _as_object(
        output_commit.get("output_files"),
        "evaluation output records",
    )
    expected_payloads = set(EXPECTED_OUTPUT_FILES) - {"EVALUATION_COMMIT.json"}
    if set(output_records) != expected_payloads:
        raise RuntimeError("Evaluation output commitment has the wrong file set.")
    for name in sorted(expected_payloads):
        _verify_file_record(
            output_root / name,
            output_records[name],
            f"final output {name}",
            expected_record_path=name,
        )

    completion = state_records["completion"]
    if tuple(completion.get("exact_output_files", ())) != EXPECTED_OUTPUT_FILES:
        raise RuntimeError("Completion marker has the wrong exact output set.")
    if (
        completion.get("output_directory")
        != "data/processed/final_test_2025/final_evaluation"
        or completion.get("output_commit_file_sha256") != _sha256(commit_path)
        or completion.get("output_commit_sha256") != output_commit_sha256
        or output_commit.get("claim_id") != EXPECTED_CLAIM_ID
        or output_commit.get("claim_commit_sha256") != state_commits["claim"]
        or output_commit.get("predictions_commit_sha256")
        != state_commits["predictions"]
        or output_commit.get("values_opened_commit_sha256")
        != state_commits["values"]
        or output_commit.get("readiness_commit_sha256")
        != state_commits["readiness"]
        or output_commit.get("authorization_commit_sha256")
        != state_commits["authorization"]
    ):
        raise RuntimeError("Evaluation output commit does not bind the state chain.")

    predictions_output = _as_object(
        state_records["predictions"].get("output"),
        "frozen prediction output",
    )
    blind_record = _as_object(
        output_records["blind_predictions.parquet"],
        "published blind prediction record",
    )
    if (
        predictions_output.get("filename") != "blind_predictions.parquet"
        or predictions_output.get("sha256") != blind_record.get("sha256")
        or predictions_output.get("bytes") != blind_record.get("bytes")
    ):
        raise RuntimeError("Published blind predictions differ from the freeze.")


def _verify_target_cache(root: Path) -> None:
    cache_root = _evidence_path(
        root,
        (
            "repository/data/interim/final_test_2025/evaluation/"
            "target_cache/targets"
        ),
    )
    lock_path = cache_root / "TARGET_BUILD_LOCK.json"
    overpass_root = cache_root / "by_overpass"
    if (
        not cache_root.is_dir()
        or cache_root.is_symlink()
        or {path.name for path in cache_root.iterdir()}
        != {"TARGET_BUILD_LOCK.json", "by_overpass"}
        or not lock_path.is_file()
        or lock_path.is_symlink()
        or not overpass_root.is_dir()
        or overpass_root.is_symlink()
    ):
        raise RuntimeError("Target cache root is not the exact expected structure.")

    target_lock = _read_object(lock_path, "target build lock")
    target_commit = _verify_commit(target_lock, "target build lock")
    target_cache_lock = _as_object(
        target_lock.get("cache_lock"),
        "target build cache lock",
    )
    if (
        target_commit != EXPECTED_TARGET_BUILD_LOCK
        or _sha256(lock_path) != EXPECTED_TARGET_BUILD_FILE_SHA256
        or target_lock.get("state") != "claim_bound_before_target_values"
        or target_cache_lock.get("claim_id") != EXPECTED_CLAIM_ID
        or target_lock.get("expected_overpass_count") != 23
    ):
        raise RuntimeError("Target build lock identity failed.")

    overpass_entries = list(overpass_root.iterdir())
    if (
        any(not path.is_dir() or path.is_symlink() for path in overpass_entries)
        or {path.name for path in overpass_entries}
        != set(EXPECTED_CACHE_COMMITS)
        or len(overpass_entries) != 23
    ):
        raise RuntimeError("Target cache does not contain the exact 23 overpasses.")

    payload_names = {
        "date_summary.parquet",
        "scene_contributions.parquet",
        "tract_date_qa.parquet",
    }
    shared_lock_keys = {
        "claim_id",
        "target_algorithm_version",
        "target_pipeline_sha256",
        "target_config_sha256",
        "research_config_file_sha256",
        "inventory_file_sha256",
        "inventory_commit_sha256",
        "key_universe_semantic_sha256",
        "tract_manifest_sha256",
        "grid_sha256",
    }
    observed_file_count = 1
    for directory in sorted(overpass_entries):
        entries = list(directory.iterdir())
        expected_names = payload_names | {"CACHE_COMMIT.json"}
        if (
            any(not path.is_file() or path.is_symlink() for path in entries)
            or {path.name for path in entries} != expected_names
            or len(entries) != 4
        ):
            raise RuntimeError(f"Cache file set changed: {directory.name}.")
        observed_file_count += len(entries)
        cache_commit = _read_object(
            directory / "CACHE_COMMIT.json",
            f"cache commit {directory.name}",
        )
        cache_commit_sha256 = _verify_commit(
            cache_commit,
            f"cache commit {directory.name}",
        )
        cache_lock = _as_object(
            cache_commit.get("cache_lock"),
            f"cache lock {directory.name}",
        )
        if (
            cache_commit_sha256 != EXPECTED_CACHE_COMMITS[directory.name]
            or cache_commit.get("state") != "complete"
            or cache_lock.get("overpass_id") != directory.name
            or any(
                cache_lock.get(key) != target_cache_lock.get(key)
                for key in shared_lock_keys
            )
        ):
            raise RuntimeError(f"Cache commitment identity failed: {directory.name}.")
        output_files = _as_object(
            cache_commit.get("output_files"),
            f"cache outputs {directory.name}",
        )
        if set(output_files) != payload_names:
            raise RuntimeError(f"Cache payload set changed: {directory.name}.")
        for name in sorted(payload_names):
            _verify_file_record(
                directory / name,
                output_files[name],
                f"cache payload {directory.name}/{name}",
            )
    if observed_file_count != 93:
        raise RuntimeError(
            f"Expected 93 target-cache files, observed {observed_file_count}."
        )


def _verify_unlock_snapshots(root: Path) -> None:
    pre_unlock = _evidence_path(
        root,
        f"snapshots/{PRE_UNLOCK_COMMIT}/configs/research.toml",
    )
    post_unlock = _evidence_path(root, "repository/configs/research.toml")
    for label, path, expected in (
        ("pre-unlock research config", pre_unlock, EXPECTED_PRE_UNLOCK),
        ("post-unlock research config", post_unlock, EXPECTED_POST_UNLOCK),
    ):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != expected[0]
            or _sha256(path) != expected[1]
        ):
            raise RuntimeError(f"{label} byte lock failed.")


def _verify_recovery(root: Path) -> None:
    recovery_root = _evidence_path(
        root,
        "repository/exports/PC_MIRROR_RESUME",
    )
    if not recovery_root.is_dir() or recovery_root.is_symlink():
        raise RuntimeError("Recovery evidence directory is missing.")
    entries = list(recovery_root.iterdir())
    if (
        any(not path.is_file() or path.is_symlink() for path in entries)
        or {path.name for path in entries} != set(EXPECTED_RECOVERY_SHA256)
        or len(entries) != 16
    ):
        raise RuntimeError("Recovery evidence is not the exact 16-file set.")
    for path in entries:
        if _sha256(path) != EXPECTED_RECOVERY_SHA256[path.name]:
            raise RuntimeError(f"Recovery evidence byte lock failed: {path.name}.")


def verify_evidence(root: Path) -> dict[str, object]:
    if root.is_symlink():
        raise RuntimeError("Evidence root must not be a symlink.")
    evidence_root = root.resolve()
    if not evidence_root.is_dir():
        raise RuntimeError("Evidence root is missing.")
    symlinks = [
        path.relative_to(evidence_root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_symlink()
    ]
    if symlinks:
        raise RuntimeError(f"Evidence tree contains symlinks: {sorted(symlinks)}")
    inventory_path = evidence_root / "integrity" / "FILES.csv"
    checksums_path = evidence_root / "integrity" / "FILES.sha256"
    checksum_anchor_path = (
        evidence_root / "integrity" / "FILES.sha256.sha256"
    )
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (inventory_path, checksums_path, checksum_anchor_path)
    ):
        raise RuntimeError("Evidence integrity files are incomplete.")

    with inventory_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = (
            "relative_path",
            "bytes",
            "sha256",
            "source_group",
        )
        if tuple(reader.fieldnames or ()) != required_columns:
            raise RuntimeError("FILES.csv schema is invalid.")
        rows = list(reader)
    if not rows:
        raise RuntimeError("FILES.csv is empty.")

    expected_paths: set[str] = set()
    total_bytes = 0
    for row in rows:
        relative = row["relative_path"]
        if relative in expected_paths:
            raise RuntimeError(f"Duplicate evidence path: {relative}")
        expected_paths.add(relative)
        path = _evidence_path(evidence_root, relative)
        try:
            expected_bytes = int(row["bytes"])
        except ValueError as error:
            raise RuntimeError(f"Invalid byte count: {relative}") from error
        if (
            expected_bytes < 0
            or not _SHA256_PATTERN.fullmatch(row["sha256"])
            or not path.is_file()
            or path.is_symlink()
        ):
            raise RuntimeError(f"Invalid evidence record: {relative}")
        size = path.stat().st_size
        digest = _sha256(path)
        if size != expected_bytes or digest != row["sha256"]:
            raise RuntimeError(f"Evidence byte lock failed: {relative}")
        total_bytes += size

    checksums = _checksum_lines(checksums_path)
    expected_checksums = {
        row["relative_path"]: row["sha256"] for row in rows
    }
    expected_checksums["integrity/FILES.csv"] = _sha256(inventory_path)
    if checksums != expected_checksums:
        raise RuntimeError("FILES.sha256 disagrees with FILES.csv.")

    anchor = _checksum_lines(checksum_anchor_path)
    if anchor != {"integrity/FILES.sha256": _sha256(checksums_path)}:
        raise RuntimeError("Checksum-manifest anchor failed.")

    actual_paths = {
        path.relative_to(evidence_root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }
    expected_actual = expected_paths | {
        "integrity/FILES.csv",
        "integrity/FILES.sha256",
        "integrity/FILES.sha256.sha256",
    }
    if actual_paths != expected_actual:
        extra = sorted(actual_paths - expected_actual)
        missing = sorted(expected_actual - actual_paths)
        raise RuntimeError(
            f"Evidence file set changed; extra={extra}, missing={missing}."
        )

    manifest = _verify_manifest_identity(evidence_root)
    repository_git_head = _verify_git_history(evidence_root, manifest)
    state_records, state_commits = _verify_state_chain(evidence_root)
    _verify_final_outputs(evidence_root, state_records, state_commits)
    _verify_target_cache(evidence_root)
    _verify_unlock_snapshots(evidence_root)
    _verify_recovery(evidence_root)
    return {
        "state": "verified",
        "claim_id": EXPECTED_CLAIM_ID,
        "completion_commit_sha256": EXPECTED_COMPLETION_COMMIT,
        "repository_git_head": repository_git_head,
        "file_count": len(actual_paths),
        "inventoried_file_count": len(rows),
        "inventoried_bytes": total_bytes,
        "files_csv_sha256": _sha256(inventory_path),
        "files_sha256_sha256": _sha256(checksums_path),
        "final_output_file_count": 21,
        "target_cache_file_count": 93,
        "target_cache_commit_count": 23,
        "recovery_file_count": 16,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(verify_evidence(args.evidence_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
