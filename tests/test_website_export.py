from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon

import la_heat.website_export as website_export
from la_heat.website_export import (
    WebsiteExportError,
    geometry_svg_path,
    tract_display_name,
)


def test_geometry_svg_path_preserves_polygon_holes_and_multiparts() -> None:
    polygon = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        holes=[[(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]],
    )
    multipolygon = MultiPolygon(
        [
            polygon,
            Polygon([(20, 0), (25, 0), (25, 5), (20, 5), (20, 0)]),
        ]
    )

    path = geometry_svg_path(
        multipolygon,
        min_x=0,
        min_y=0,
        scale=1,
        offset_x=0,
        offset_y=0,
        height=30,
    )

    assert path.count("M") == 3
    assert path.count("Z") == 3
    assert "M0,30L10,30L10,20L0,20L0,30Z" in path


def test_tract_display_name_combines_tiger_type_and_number() -> None:
    assert tract_display_name("1011.10", "Census Tract") == "Census Tract 1011.10"
    assert tract_display_name("1013", "") == "Census Tract 1013"
    assert tract_display_name("", "Census Tract") == "Census Tract"


def _build_minimal_valid_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    output = project / "display"
    source = project / "source.txt"
    evidence = project / "evidence.json"
    output.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    display = output / "tracts.json"
    display.write_text("{}", encoding="utf-8")
    identity = {
        "claimId": "claim",
        "completionCommitSha256": "completion",
        "evidenceZipSha256": "evidence-zip",
        "packageRepositoryGitHead": "git-head",
    }
    evidence.write_text(
        json.dumps(
            {
                "verified": True,
                "claim_id": identity["claimId"],
                "completion_commit_sha256": identity["completionCommitSha256"],
                "zip_sha256": identity["evidenceZipSha256"],
                "package_repository_git_head": identity["packageRepositoryGitHead"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        website_export,
        "SOURCE_RELATIVES",
        (Path("source.txt"), Path("evidence.json")),
    )
    monkeypatch.setattr(website_export, "EVIDENCE_RELATIVE", Path("evidence.json"))
    monkeypatch.setattr(website_export, "DISPLAY_FILES", ("tracts.json",))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
    display_hash = hashlib.sha256(display.read_bytes()).hexdigest()
    manifest = {
        "state": "verified-display-export",
        "algorithmVersion": website_export.ALGORITHM_VERSION,
        "scientificIdentity": identity,
        "displayRules": {"metricsRecomputedFromRoundedDisplayValues": False},
        "counts": {"tracts": 1096, "evaluationRows": 15116, "independentDates": 15},
        "sources": [
            {"path": "source.txt", "bytes": 6, "sha256": source_hash},
            {
                "path": "evidence.json",
                "bytes": evidence.stat().st_size,
                "sha256": evidence_hash,
            },
        ],
        "outputs": [{"path": "tracts.json", "bytes": 2, "sha256": display_hash}],
    }
    (output / "display-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project, output, display


def test_verify_export_rejects_changed_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, output, display = _build_minimal_valid_export(tmp_path, monkeypatch)
    website_export.verify_website_export(project, output)

    display.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(WebsiteExportError, match="Display output changed"):
        website_export.verify_website_export(project, output)


def test_verify_export_rejects_incomplete_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, output, _ = _build_minimal_valid_export(tmp_path, monkeypatch)
    manifest_path = output / "display-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["sources"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WebsiteExportError, match="source inventory is not exact"):
        website_export.verify_website_export(project, output)
