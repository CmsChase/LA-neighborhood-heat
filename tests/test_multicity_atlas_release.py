from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.multicity.atlas_release as atlas_release
from la_heat.multicity.atlas_release import (
    AtlasReleaseError,
    authenticate_atlas_release,
    publish_atlas_release,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.provenance import canonical_sha256, parquet_file_record, sha256_file


def _evaluation_fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, object]]:
    output = root / "evaluation"
    output.mkdir(parents=True)
    city_rows: list[dict[str, object]] = []
    date_rows: list[dict[str, object]] = []
    for index, city_id in enumerate(EXTERNAL_CITY_IDS):
        city_rows.append(
            {
                "city_id": city_id,
                "date_count": 2,
                "row_count": 100 + index,
                "spatial_block_count": 10 + index,
                "b1_equal_date_mae_c": 4.0 + index,
                "m2_equal_date_mae_c": 3.0 + index,
                "m2_interval_coverage": 0.88 + index / 100,
                "m2_mean_interval_width_c": 5.0 + index,
                "m2_wis90_c": 1.5 + index / 10,
                "m2_retention_fraction": 0.91 - index / 100,
                "median_per_date_m2_spearman": 0.70 + index / 100,
            }
        )
        for month in (6, 7):
            date_rows.append(
                {
                    "city_id": city_id,
                    "target_date": f"2025-{month:02d}-01",
                    "date_count": 1,
                    "row_count": 50,
                    "spatial_block_count": 10,
                }
            )
    cities = pd.DataFrame(city_rows)
    dates = pd.DataFrame(date_rows)
    city_path = output / atlas_release.CITY_METRICS_FILENAME
    date_path = output / atlas_release.DATE_METRICS_FILENAME
    summary_path = output / atlas_release.SUMMARY_FILENAME
    cities.to_parquet(city_path, index=False)
    dates.to_parquet(date_path, index=False)
    summary = {
        "state": "complete",
        "city_ids": list(EXTERNAL_CITY_IDS),
        "usable_row_count": int(cities["row_count"].sum()),
        "usable_city_date_count": len(dates),
        "spatial_block_count": 33,
        "primary": {
            "relative_mae_improvement_fraction": 0.2,
            "bootstrap_ci_lower": 0.04,
            "bootstrap_ci_upper": 0.31,
        },
        "point_prediction_gates": {"success": True},
        "reliability": {"success": False},
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    records = {
        atlas_release.CITY_METRICS_FILENAME: parquet_file_record(city_path, cities),
        atlas_release.DATE_METRICS_FILENAME: parquet_file_record(date_path, dates),
        atlas_release.SUMMARY_FILENAME: {
            "bytes": summary_path.stat().st_size,
            "sha256": sha256_file(summary_path),
        },
    }
    completion: dict[str, object] = {
        "state": "external_evaluation_complete",
        "output_files": records,
    }
    completion["commit_sha256"] = canonical_sha256(completion)
    completion_path = output / "EXTERNAL_EVALUATION_COMPLETE.json"
    completion_path.write_text(json.dumps(completion), encoding="utf-8")
    monkeypatch.setattr(
        atlas_release,
        "authenticate_external_evaluation_completion",
        lambda *_args, **_kwargs: completion,
    )
    report = root / "report"
    report.mkdir()
    figures: dict[str, dict[str, object]] = {}
    for index, (figure_id, filename) in enumerate(
        atlas_release.EVALUATION_FIGURE_FILES.items()
    ):
        path = report / filename
        content = b"\x89PNG\r\n\x1a\n" + bytes([index]) + figure_id.encode("ascii")
        path.write_bytes(content)
        figures[figure_id] = {
            "path": filename,
            "bytes": len(content),
            "sha256": sha256_file(path),
        }
    report_manifest: dict[str, object] = {
        "state": "read_only_external_evaluation_evidence",
        "figures": figures,
    }
    report_manifest["commit_sha256"] = canonical_sha256(report_manifest)
    (report / atlas_release.EVALUATION_REPORT_MANIFEST_FILENAME).write_text(
        json.dumps(report_manifest), encoding="utf-8"
    )
    monkeypatch.setattr(
        atlas_release,
        "authenticate_external_evaluation_report",
        lambda *_args, **_kwargs: report_manifest,
    )
    return output, report, completion


def test_publish_maps_only_three_external_results_and_keeps_la_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation, report, completion = _evaluation_fixture(tmp_path, monkeypatch)
    atlas_output = tmp_path / "atlas/app/cities/generated-results.ts"
    manifest_path = tmp_path / "ATLAS_RESULTS_RELEASE.json"

    manifest = publish_atlas_release(
        tmp_path,
        evaluation_output_directory=evaluation,
        evaluation_report_directory=report,
        atlas_output_path=atlas_output,
        release_manifest_path=manifest_path,
    )

    text = atlas_output.read_text(encoding="utf-8")
    payload = json.loads(text.split(" = ", maxsplit=1)[1].removesuffix(";\n"))
    assert manifest["evaluation_completion_commit_sha256"] == completion["commit_sha256"]
    assert payload["release"]["state"] == "verified"
    assert payload["sourceReference"]["resultState"] == "historical_source_reference"
    assert payload["sourceReference"]["comparableAsExternalConfirmation"] is False
    assert [item["cityId"] for item in payload["externalResults"]] == list(EXTERNAL_CITY_IDS)
    assert all(
        item["resultState"] == "authenticated_external_confirmation"
        for item in payload["externalResults"]
    )
    assert payload["externalResults"][0]["primary"]["equalDateMaeC"] == 3.0
    assert payload["externalResults"][0]["primary"]["relativeMaeImprovementPercent"] == 25.0
    expected_provenance_paths = {
        "atlas/public/evidence/multicity/external-evaluation-completion.json",
        "atlas/public/evidence/multicity/external-evaluation-summary.json",
        "atlas/public/evidence/multicity/external-city-metrics.json",
    }
    expected_public_paths = {
        *expected_provenance_paths,
        *{
            f"atlas/public/evidence/multicity/{filename}"
            for filename in atlas_release.EVALUATION_FIGURE_FILES.values()
        },
    }
    assert {item["repositoryPath"] for item in payload["provenance"]} == (
        expected_provenance_paths
    )
    for item in payload["provenance"]:
        assert item["href"] == atlas_release.GITHUB_BLOB_BASE + item["repositoryPath"]
        assert (tmp_path / item["repositoryPath"]).is_file()
    assert {
        record["path"] for record in manifest["evidence"]["public_evidence"].values()
    } == expected_public_paths
    assert len(payload["evidenceFigures"]) == 6
    for figure in payload["evidenceFigures"]:
        public_path = tmp_path / figure["repositoryPath"]
        source_path = report / atlas_release.EVALUATION_FIGURE_FILES[figure["id"]]
        assert public_path.read_bytes() == source_path.read_bytes()
        assert figure["publicPath"] == (
            f"/evidence/multicity/{atlas_release.EVALUATION_FIGURE_FILES[figure['id']]}"
        )
        assert figure["sha256"] == sha256_file(source_path)
    projection = json.loads(
        (tmp_path / "atlas/public/evidence/multicity/external-city-metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert projection["state"] == "authenticated_external_city_metrics_projection"
    assert projection["evaluation_completion_commit_sha256"] == completion["commit_sha256"]
    assert [item["cityId"] for item in projection["results"]] == list(EXTERNAL_CITY_IDS)
    unsigned_projection = dict(projection)
    unsigned_projection.pop("commit_sha256")
    assert projection["commit_sha256"] == canonical_sha256(unsigned_projection)

    assert (
        authenticate_atlas_release(
            tmp_path,
            evaluation_output_directory=evaluation,
            evaluation_report_directory=report,
            atlas_output_path=atlas_output,
            release_manifest_path=manifest_path,
        )
        == manifest
    )


def test_check_only_rejects_generated_file_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation, report, _completion = _evaluation_fixture(tmp_path, monkeypatch)
    atlas_output = tmp_path / "generated-results.ts"
    manifest_path = tmp_path / "ATLAS_RESULTS_RELEASE.json"
    publish_atlas_release(
        tmp_path,
        evaluation_output_directory=evaluation,
        evaluation_report_directory=report,
        atlas_output_path=atlas_output,
        release_manifest_path=manifest_path,
    )
    atlas_output.write_text("export const forged = true;\n", encoding="utf-8")

    with pytest.raises(AtlasReleaseError, match="no longer reproduces"):
        publish_atlas_release(
            tmp_path,
            evaluation_output_directory=evaluation,
            evaluation_report_directory=report,
            atlas_output_path=atlas_output,
            release_manifest_path=manifest_path,
            check_only=True,
        )


def test_check_only_rejects_public_evidence_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation, report, _completion = _evaluation_fixture(tmp_path, monkeypatch)
    atlas_output = tmp_path / "generated-results.ts"
    manifest_path = tmp_path / "ATLAS_RESULTS_RELEASE.json"
    publish_atlas_release(
        tmp_path,
        evaluation_output_directory=evaluation,
        evaluation_report_directory=report,
        atlas_output_path=atlas_output,
        release_manifest_path=manifest_path,
    )
    public_figure = (
        tmp_path
        / "atlas/public/evidence/multicity"
        / atlas_release.EVALUATION_FIGURE_FILES["external_city_mae"]
    )
    public_figure.write_bytes(b"forged")

    with pytest.raises(AtlasReleaseError, match="Public Atlas evidence changed"):
        publish_atlas_release(
            tmp_path,
            evaluation_output_directory=evaluation,
            evaluation_report_directory=report,
            atlas_output_path=atlas_output,
            release_manifest_path=manifest_path,
            check_only=True,
        )


def test_failed_evaluation_authentication_does_not_touch_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    atlas_output = tmp_path / "generated-results.ts"
    atlas_output.write_text("export const preview = null;\n", encoding="utf-8")
    manifest_path = tmp_path / "ATLAS_RESULTS_RELEASE.json"

    def reject(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("not authenticated")

    monkeypatch.setattr(atlas_release, "authenticate_external_evaluation_completion", reject)
    monkeypatch.setattr(atlas_release, "authenticate_external_evaluation_report", reject)
    with pytest.raises(RuntimeError, match="not authenticated"):
        publish_atlas_release(
            tmp_path,
            evaluation_output_directory=tmp_path / "evaluation",
            atlas_output_path=atlas_output,
            release_manifest_path=manifest_path,
        )

    assert atlas_output.read_text(encoding="utf-8") == "export const preview = null;\n"
    assert not manifest_path.exists()
