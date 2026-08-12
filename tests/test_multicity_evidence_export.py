from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import la_heat.multicity.evidence_export as evidence


def _fixture(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    groups = {
        "aggregate": (
            "formal/summary.json",
            "formal/city_metrics.parquet",
            "figures/result.png",
        ),
        "documentation": ("docs/interpretation.md",),
    }
    monkeypatch.setattr(evidence, "SOURCE_GROUPS", groups)
    for _group, paths in groups.items():
        for relative in paths:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"safe:{relative}\n".encode())


def _auth(_root: Path) -> dict[str, str]:
    return {
        "external_evaluation_commit_sha256": "a" * 64,
        "evaluation_report_commit_sha256": "b" * 64,
        "atlas_release_commit_sha256": "c" * 64,
        "posthoc_audit_commit_sha256": "d" * 64,
    }


def test_build_and_authenticate_compact_evidence_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path, monkeypatch)
    manifest = evidence.build_multicity_evidence_export(
        tmp_path,
        output_directory="exports/evidence",
        input_authenticator=_auth,
        git_identity=lambda _root: "1" * 40,
    )
    assert manifest["state"] == "verified_read_only_multicity_evidence_bundle"
    assert manifest["scientific_outcome"] == "inconclusive_sample_size"
    assert len(manifest["files"]) == 5
    archive = tmp_path / "exports/evidence.zip"
    checksum = tmp_path / "exports/evidence.zip.sha256"
    assert checksum.read_text(encoding="ascii") == (
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  evidence.zip\n"
    )
    with zipfile.ZipFile(archive) as handle:
        assert handle.testzip() is None
        assert "MULTICITY_EVALUATION_EVIDENCE/EVIDENCE_MANIFEST.json" in handle.namelist()
    authenticated = evidence.authenticate_multicity_evidence_export(
        tmp_path,
        output_directory="exports/evidence",
        input_authenticator=_auth,
    )
    assert authenticated == manifest


def test_export_rejects_secrets_and_forbidden_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evidence, "SOURCE_GROUPS", {"bad": ("safe/token.txt",)})
    path = tmp_path / "safe/token.txt"
    path.parent.mkdir(parents=True)
    path.write_text("Bearer " + "x" * 40, encoding="utf-8")
    with pytest.raises(evidence.MulticityEvidenceExportError, match="bearer"):
        evidence.build_multicity_evidence_export(
            tmp_path,
            output_directory="exports/evidence",
            input_authenticator=_auth,
            git_identity=lambda _root: "1" * 40,
        )

    monkeypatch.setattr(
        evidence,
        "SOURCE_GROUPS",
        {"bad": ("data/scored_rows.parquet",)},
    )
    forbidden = tmp_path / "data/scored_rows.parquet"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_bytes(b"not-secret")
    with pytest.raises(evidence.MulticityEvidenceExportError, match="Forbidden"):
        evidence.build_multicity_evidence_export(
            tmp_path,
            output_directory="exports/other",
            input_authenticator=_auth,
            git_identity=lambda _root: "1" * 40,
        )


def test_authentication_rejects_bundle_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path, monkeypatch)
    evidence.build_multicity_evidence_export(
        tmp_path,
        output_directory="exports/evidence",
        input_authenticator=_auth,
        git_identity=lambda _root: "1" * 40,
    )
    summary = tmp_path / "exports/evidence/repository/formal/summary.json"
    summary.write_bytes(b"tampered\n")
    with pytest.raises(evidence.MulticityEvidenceExportError, match="hash changed"):
        evidence.authenticate_multicity_evidence_export(
            tmp_path,
            output_directory="exports/evidence",
            input_authenticator=_auth,
        )


def test_manifest_commit_is_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixture(tmp_path, monkeypatch)
    evidence.build_multicity_evidence_export(
        tmp_path,
        output_directory="exports/evidence",
        input_authenticator=_auth,
        git_identity=lambda _root: "1" * 40,
    )
    path = tmp_path / "exports/evidence/EVIDENCE_MANIFEST.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    observed = payload.pop("commit_sha256")
    assert observed == evidence.canonical_sha256(payload)
