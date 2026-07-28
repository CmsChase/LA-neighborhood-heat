from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import scripts.verify_final_evaluation_evidence as evidence


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _committed(payload: dict[str, object]) -> dict[str, object]:
    committed = dict(payload)
    committed["commit_sha256"] = _canonical_sha256(committed)
    return committed


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_inventory_layers(
    root: Path,
    rows: list[dict[str, object]],
) -> None:
    integrity = root / "integrity"
    integrity.mkdir(exist_ok=True)
    inventory = integrity / "FILES.csv"
    with inventory.open("w", encoding="utf-8", newline="") as handle:
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
        writer.writerows(rows)
    checksums = integrity / "FILES.sha256"
    checksum_records = {
        str(row["relative_path"]): str(row["sha256"]) for row in rows
    }
    checksum_records["integrity/FILES.csv"] = _sha256(inventory)
    checksums.write_text(
        "".join(
            f"{digest}  {relative}\n"
            for relative, digest in sorted(checksum_records.items())
        ),
        encoding="utf-8",
    )
    (integrity / "FILES.sha256.sha256").write_text(
        f"{_sha256(checksums)}  integrity/FILES.sha256\n",
        encoding="utf-8",
    )


def _write_integrity(root: Path) -> None:
    integrity = root / "integrity"
    integrity.mkdir(exist_ok=True)
    excluded = {
        integrity / "FILES.csv",
        integrity / "FILES.sha256",
        integrity / "FILES.sha256.sha256",
    }
    for path in excluded:
        path.unlink(missing_ok=True)
    rows = [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "source_group": "fixture",
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path not in excluded
    ]
    _write_inventory_layers(root, rows)


def _fixture(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    pre_unlock = b"unlock_final_test = false\n"
    post_unlock = b"unlock_final_test = true\n"
    monkeypatch.setattr(
        evidence,
        "EXPECTED_PRE_UNLOCK",
        (len(pre_unlock), hashlib.sha256(pre_unlock).hexdigest()),
    )
    monkeypatch.setattr(
        evidence,
        "EXPECTED_POST_UNLOCK",
        (len(post_unlock), hashlib.sha256(post_unlock).hexdigest()),
    )
    pre_unlock_path = (
        root
        / "snapshots"
        / evidence.PRE_UNLOCK_COMMIT
        / "configs"
        / "research.toml"
    )
    pre_unlock_path.parent.mkdir(parents=True)
    pre_unlock_path.write_bytes(pre_unlock)
    post_unlock_path = root / "repository" / "configs" / "research.toml"
    post_unlock_path.parent.mkdir(parents=True)
    post_unlock_path.write_bytes(post_unlock)

    output_root = (
        root
        / "repository"
        / "data"
        / "processed"
        / "final_test_2025"
        / "final_evaluation"
    )
    output_root.mkdir(parents=True)
    for name in evidence.EXPECTED_OUTPUT_FILES:
        if name != "EVALUATION_COMMIT.json":
            (output_root / name).write_bytes(f"fixture:{name}\n".encode())

    readiness_request = {"fixture_pipeline": "locked"}
    readiness = _committed(
        {
            "schema_version": 1,
            "state": "ready_target_blind",
            "target_blind": True,
            "values_read": False,
            "request": readiness_request,
            "request_sha256": _canonical_sha256(readiness_request),
        }
    )
    readiness_path = root / evidence.STATE_PATHS["readiness"]
    _write_json(readiness_path, readiness)

    authorization = _committed(
        {
            "schema_version": 1,
            "state": "authorized_for_one_time_2025_evaluation",
            "authorized": True,
            "values_read": False,
            "evaluation_readiness": {
                "commit_sha256": readiness["commit_sha256"],
                "request_sha256": readiness["request_sha256"],
                "file_sha256": _sha256(readiness_path),
                "bytes": readiness_path.stat().st_size,
            },
        }
    )
    authorization_path = root / evidence.STATE_PATHS["authorization"]
    _write_json(authorization_path, authorization)

    claim_request = {
        "readiness_commit_sha256": readiness["commit_sha256"],
        "authorization_commit_sha256": authorization["commit_sha256"],
        "fixture": "single evaluation",
    }
    claim_id = _canonical_sha256(claim_request)
    monkeypatch.setattr(evidence, "EXPECTED_CLAIM_ID", claim_id)
    claim = _committed(
        {
            "schema_version": 1,
            "state": "claimed_for_single_evaluation",
            "claim_id": claim_id,
            "request": claim_request,
            "request_sha256": claim_id,
        }
    )
    claim_path = root / evidence.STATE_PATHS["claim"]
    _write_json(claim_path, claim)

    blind_path = output_root / "blind_predictions.parquet"
    predictions = _committed(
        {
            "schema_version": 1,
            "state": "blind_predictions_frozen",
            "claim_id": claim_id,
            "claim_commit_sha256": claim["commit_sha256"],
            "target_or_qa_values_read": False,
            "output": {
                "filename": blind_path.name,
                "bytes": blind_path.stat().st_size,
                "sha256": _sha256(blind_path),
            },
        }
    )
    predictions_path = root / evidence.STATE_PATHS["predictions"]
    _write_json(predictions_path, predictions)

    values = _committed(
        {
            "schema_version": 1,
            "state": "target_and_qa_values_opened",
            "claim_id": claim_id,
            "claim_commit_sha256": claim["commit_sha256"],
            "predictions_commit_sha256": predictions["commit_sha256"],
            "blind_predictions_frozen": True,
            "values_read": True,
        }
    )
    values_path = root / evidence.STATE_PATHS["values"]
    _write_json(values_path, values)

    cache_root = (
        root
        / "repository"
        / "data"
        / "interim"
        / "final_test_2025"
        / "evaluation"
        / "target_cache"
        / "targets"
    )
    cache_lock = {
        "claim_id": claim_id,
        "target_algorithm_version": "fixture-target-v1",
        "target_pipeline_sha256": "1" * 64,
        "target_config_sha256": "2" * 64,
        "research_config_file_sha256": "3" * 64,
        "inventory_file_sha256": "4" * 64,
        "inventory_commit_sha256": "5" * 64,
        "key_universe_semantic_sha256": "6" * 64,
        "tract_manifest_sha256": "7" * 64,
        "grid_sha256": "8" * 64,
    }
    target_lock = _committed(
        {
            "schema_version": 1,
            "state": "claim_bound_before_target_values",
            "cache_lock": cache_lock,
            "expected_overpass_count": 23,
        }
    )
    target_lock_path = cache_root / "TARGET_BUILD_LOCK.json"
    _write_json(target_lock_path, target_lock)
    monkeypatch.setattr(
        evidence,
        "EXPECTED_TARGET_BUILD_LOCK",
        target_lock["commit_sha256"],
    )
    monkeypatch.setattr(
        evidence,
        "EXPECTED_TARGET_BUILD_FILE_SHA256",
        _sha256(target_lock_path),
    )

    cache_commits: dict[str, str] = {}
    first_cache_payload: Path | None = None
    for index in range(23):
        overpass_id = f"fixture-overpass-{index:02d}"
        directory = cache_root / "by_overpass" / overpass_id
        directory.mkdir(parents=True)
        output_records: dict[str, dict[str, object]] = {}
        for name in (
            "date_summary.parquet",
            "scene_contributions.parquet",
            "tract_date_qa.parquet",
        ):
            path = directory / name
            path.write_bytes(f"{overpass_id}:{name}\n".encode())
            output_records[name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            if first_cache_payload is None and name == "tract_date_qa.parquet":
                first_cache_payload = path
        cache_commit = _committed(
            {
                "schema_version": 1,
                "state": "complete",
                "cache_lock": {
                    **cache_lock,
                    "overpass_id": overpass_id,
                    "overpass_source_sha256": f"{index:064x}",
                },
                "output_files": output_records,
            }
        )
        _write_json(directory / "CACHE_COMMIT.json", cache_commit)
        cache_commits[overpass_id] = str(cache_commit["commit_sha256"])
    monkeypatch.setattr(evidence, "EXPECTED_CACHE_COMMITS", cache_commits)
    assert first_cache_payload is not None

    output_records = {}
    for name in evidence.EXPECTED_OUTPUT_FILES:
        if name == "EVALUATION_COMMIT.json":
            continue
        path = output_root / name
        output_records[name] = {
            "path": name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    output_commit = _committed(
        {
            "schema_version": 1,
            "state": "complete_staged_evaluation",
            "claim_id": claim_id,
            "claim_commit_sha256": claim["commit_sha256"],
            "predictions_commit_sha256": predictions["commit_sha256"],
            "values_opened_commit_sha256": values["commit_sha256"],
            "readiness_commit_sha256": readiness["commit_sha256"],
            "authorization_commit_sha256": authorization["commit_sha256"],
            "output_files": output_records,
        }
    )
    output_commit_path = output_root / "EVALUATION_COMMIT.json"
    _write_json(output_commit_path, output_commit)

    completion = _committed(
        {
            "schema_version": 1,
            "state": "complete_one_time_final_evaluation",
            "completed": True,
            "claim_id": claim_id,
            "claim_commit_sha256": claim["commit_sha256"],
            "predictions_commit_sha256": predictions["commit_sha256"],
            "values_opened_commit_sha256": values["commit_sha256"],
            "output_directory": (
                "data/processed/final_test_2025/final_evaluation"
            ),
            "output_commit_file_sha256": _sha256(output_commit_path),
            "output_commit_sha256": output_commit["commit_sha256"],
            "exact_output_files": list(evidence.EXPECTED_OUTPUT_FILES),
        }
    )
    completion_path = root / evidence.STATE_PATHS["completion"]
    _write_json(completion_path, completion)
    monkeypatch.setattr(
        evidence,
        "EXPECTED_COMPLETION_COMMIT",
        completion["commit_sha256"],
    )

    recovery_root = (
        root / "repository" / "exports" / "PC_MIRROR_RESUME"
    )
    recovery_root.mkdir(parents=True)
    recovery_hashes = {}
    for name in evidence.EXPECTED_RECOVERY_SHA256:
        path = recovery_root / name
        path.write_bytes(f"fixture recovery:{name}\n".encode())
        recovery_hashes[name] = _sha256(path)
    monkeypatch.setattr(
        evidence,
        "EXPECTED_RECOVERY_SHA256",
        recovery_hashes,
    )

    git_source = root.parent / f"{root.name}-git-source"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(git_source)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(git_source, "config", "user.name", "Evidence Fixture")
    _git(git_source, "config", "user.email", "fixture@example.invalid")
    _git(git_source, "config", "commit.gpgsign", "false")
    (git_source / "README.md").write_text("fixture history\n", encoding="utf-8")
    _git(git_source, "add", "README.md")
    _git(git_source, "commit", "-m", "fixture history")
    repository_git_head = _git(git_source, "rev-parse", "HEAD")

    history_root = root / "history"
    history_root.mkdir()
    bundle_path = history_root / "repository.bundle"
    _git(git_source, "bundle", "create", str(bundle_path), "--all")
    _write_json(
        history_root / "GIT_STATE.json",
        {
            "schema_version": 1,
            "generated_at_utc": "2026-07-27T00:00:00+00:00",
            "branch": "main",
            "head": repository_git_head,
            "origin_main": repository_git_head,
            "remote_origin": evidence.EXPECTED_REMOTE_ORIGIN,
            "worktree_porcelain": [],
            "claim_id": claim_id,
            "completion_commit_sha256": completion["commit_sha256"],
        },
    )
    (history_root / "repository_bundle_verify.txt").write_text(
        (
            "fixture bundle verification\n"
            "--- independent bare-clone verification ---\n"
            f"recovered_main={repository_git_head}\n"
        ),
        encoding="utf-8",
    )

    _write_json(
        root / "EVIDENCE_MANIFEST.json",
        {
            "schema_version": 1,
            "state": "read_only_final_evaluation_evidence",
            "claim_id": claim_id,
            "completion_commit_sha256": completion["commit_sha256"],
            "repository_git_head": repository_git_head,
            "final_output_file_count": 21,
            "exact_final_output_files": list(evidence.EXPECTED_OUTPUT_FILES),
            "target_cache_file_count": 93,
            "target_cache_commit_count": 23,
            "contains_pre_and_post_unlock_research_config": True,
            "contains_all_recovery_attempt_logs": True,
            "recovery_files_sha256": dict(sorted(recovery_hashes.items())),
            "read_only_permissions": evidence.EXPECTED_READ_ONLY_PERMISSIONS,
            "remote_asset_byte_equivalence_scope": (
                evidence.EXPECTED_REMOTE_ASSET_BYTE_EQUIVALENCE_SCOPE
            ),
        },
    )
    _write_integrity(root)
    return {
        "readiness": readiness_path,
        "output_root": output_root,
        "first_cache_payload": first_cache_payload,
        "recovery_root": recovery_root,
        "bundle": bundle_path,
        "git_state": history_root / "GIT_STATE.json",
        "manifest": root / "EVIDENCE_MANIFEST.json",
    }


def test_verify_evidence_accepts_minimal_complete_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch)

    result = evidence.verify_evidence(tmp_path)

    assert result["state"] == "verified"
    assert result["claim_id"] == evidence.EXPECTED_CLAIM_ID
    assert len(result["repository_git_head"]) == 40
    assert result["final_output_file_count"] == 21
    assert result["target_cache_file_count"] == 93
    assert result["target_cache_commit_count"] == 23
    assert result["recovery_file_count"] == 16


def test_verify_evidence_rejects_payload_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    (paths["output_root"] / "model_metrics.csv").write_bytes(b"changed\n")

    with pytest.raises(RuntimeError, match="Evidence byte lock failed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_extra_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch)
    (tmp_path / "extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(RuntimeError, match="file set changed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch)
    inventory = tmp_path / "integrity" / "FILES.csv"
    with inventory.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["relative_path"] = "../outside.txt"
    _write_inventory_layers(tmp_path, rows)

    with pytest.raises(RuntimeError, match="Unsafe evidence path"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_self_consistent_manifest_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch)
    manifest_path = tmp_path / "EVIDENCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["claim_id"] = "f" * 64
    _write_json(manifest_path, manifest)
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="manifest identity failed"):
        evidence.verify_evidence(tmp_path)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repository_git_head", "not-a-git-object"),
        ("exact_final_output_files", []),
        ("recovery_files_sha256", {}),
        ("read_only_permissions", "changed"),
        ("remote_asset_byte_equivalence_scope", "changed"),
    ],
)
def test_verify_evidence_rejects_manifest_contract_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest[field] = replacement
    _write_json(paths["manifest"], manifest)
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="manifest identity failed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_manifest_head_not_bound_to_git_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    replacement = "f" * 40
    if manifest["repository_git_head"] == replacement:
        replacement = "e" * 40
    manifest["repository_git_head"] = replacement
    _write_json(paths["manifest"], manifest)
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="clean-main tracking failed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_missing_git_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    paths["bundle"].unlink()
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="Git history evidence files are incomplete"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_replaced_git_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    paths["bundle"].write_bytes(b"not a Git bundle\n")
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="Git bundle verification failed"):
        evidence.verify_evidence(tmp_path)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("branch", "development"),
        ("origin_main", "f" * 40),
        ("worktree_porcelain", [" M tracked.txt"]),
        ("remote_origin", "https://example.invalid/forged.git"),
    ],
)
def test_verify_evidence_rejects_forged_git_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    git_state = json.loads(paths["git_state"].read_text(encoding="utf-8"))
    git_state[field] = replacement
    _write_json(paths["git_state"], git_state)
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="clean-main tracking failed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_recommitted_state_chain_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    readiness = json.loads(paths["readiness"].read_text(encoding="utf-8"))
    readiness["request"]["fixture_pipeline"] = "changed"
    readiness["request_sha256"] = _canonical_sha256(readiness["request"])
    readiness.pop("commit_sha256")
    readiness["commit_sha256"] = _canonical_sha256(readiness)
    _write_json(paths["readiness"], readiness)
    _write_integrity(tmp_path)

    with pytest.raises(
        RuntimeError,
        match="Authorization does not bind readiness",
    ):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_output_tamper_with_fresh_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    (paths["output_root"] / "model_metrics.csv").write_bytes(b"changed\n")
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="final output model_metrics.csv"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_recommitted_cache_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    payload = paths["first_cache_payload"]
    payload.write_bytes(b"forged target cache\n")
    commit_path = payload.parent / "CACHE_COMMIT.json"
    cache_commit = json.loads(commit_path.read_text(encoding="utf-8"))
    record = cache_commit["output_files"][payload.name]
    record["bytes"] = payload.stat().st_size
    record["sha256"] = _sha256(payload)
    cache_commit.pop("commit_sha256")
    cache_commit["commit_sha256"] = _canonical_sha256(cache_commit)
    _write_json(commit_path, cache_commit)
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="Cache commitment identity failed"):
        evidence.verify_evidence(tmp_path)


def test_verify_evidence_rejects_missing_recovery_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path, monkeypatch)
    (paths["recovery_root"] / "status.json").unlink()
    _write_integrity(tmp_path)

    with pytest.raises(RuntimeError, match="exact 16-file set"):
        evidence.verify_evidence(tmp_path)
