from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from PIL import Image
from shapely.geometry import box

import la_heat.multicity.external_evaluation_reporting as reporting
from la_heat.multicity.external_evaluation import (
    evaluate_external_frames,
    publish_external_evaluation,
)
from la_heat.multicity.external_evaluation_reporting import (
    FIGURE_FILES,
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    ExternalEvaluationReportingError,
    authenticate_external_evaluation_report,
    build_external_evaluation_report,
)
from la_heat.multicity.external_target_authorization import PREDICTION_COLUMNS
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predictions: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    for city_index, city_id in enumerate(EXTERNAL_CITY_IDS):
        for tract_index in range(4):
            tract = f"{city_index + 1:02d}{tract_index:09d}"
            blocks.append(
                {
                    "city_id": city_id,
                    "tract_geoid": tract,
                    "spatial_block": f"{city_id}-block-{tract_index // 2}",
                }
            )
        for date_index in range(10):
            target_date = f"2025-07-{date_index + 1:02d}"
            for tract_index in range(4):
                tract = f"{city_index + 1:02d}{tract_index:09d}"
                actual = 35.0 + city_index + date_index / 10 + tract_index / 4
                predictions.append(
                    {
                        "city_id": city_id,
                        "tract_geoid": tract,
                        "target_date": target_date,
                        "b1_prediction_c": actual + 2.0,
                        "m2_prediction_c": actual + 0.5,
                        "m2_lower_c": actual - 0.5,
                        "m2_upper_c": actual + 1.5,
                        "m2_interval_width_c": 2.0,
                        "m2_abstain": False,
                        "m2_accepted": True,
                    }
                )
                targets.append(
                    {
                        "city_id": city_id,
                        "tract_geoid": tract,
                        "target_date": target_date,
                        "target_lst_c": actual,
                        "target_available": True,
                        "date_usable": True,
                    }
                )
    return (
        pd.DataFrame(predictions).loc[:, PREDICTION_COLUMNS],
        pd.DataFrame(targets),
        pd.DataFrame(blocks),
    )


def _geometry_loader(
    _root: Path,
) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, dict[str, object]]]:
    geometries: dict[str, gpd.GeoDataFrame] = {}
    records: dict[str, dict[str, object]] = {}
    for city_index, city_id in enumerate(EXTERNAL_CITY_IDS):
        geometries[city_id] = gpd.GeoDataFrame(
            {
                "tract_geoid": [
                    f"{city_index + 1:02d}{tract_index:09d}"
                    for tract_index in range(4)
                ],
                "geometry": [
                    box(tract_index * 1000, 0, (tract_index + 1) * 1000, 1000)
                    for tract_index in range(4)
                ],
            },
            crs="EPSG:5070",
        )
        records[city_id] = {
            "manifest": {
                "path": f"synthetic/{city_id}/GEOGRAPHY.json",
                "bytes": city_index + 1,
                "sha256": str(city_index + 1) * 64,
                "commit_sha256": str(city_index + 4) * 64,
            },
            "primary_tracts": {
                "path": f"synthetic/{city_id}/tracts.parquet",
                "bytes": city_index + 10,
                "sha256": str(city_index + 7) * 64,
            },
        }
    return geometries, records


def _publish_synthetic_evaluation(directory: Path) -> dict[str, object]:
    protocol = json.loads(
        (PROJECT_ROOT / "manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json").read_text(
            encoding="utf-8"
        )
    )
    predictions, targets, blocks = _frames()
    result = evaluate_external_frames(predictions, targets, blocks, protocol)
    return publish_external_evaluation(
        PROJECT_ROOT,
        result,
        input_bindings={"synthetic_fixture": "f" * 64},
        output_directory=directory,
    )


def test_report_authenticates_before_any_metric_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation = tmp_path / "evaluation"
    output = tmp_path / "report"
    called: list[str] = []

    def stop_after_auth(*_args: object, **_kwargs: object) -> dict[str, object]:
        called.append("auth")
        raise RuntimeError("stop")

    monkeypatch.setattr(
        reporting, "authenticate_external_evaluation_completion", stop_after_auth
    )
    monkeypatch.setattr(
        reporting.pd,
        "read_parquet",
        lambda *_args, **_kwargs: called.append("read"),
    )

    with pytest.raises(RuntimeError, match="stop"):
        build_external_evaluation_report(
            PROJECT_ROOT,
            evaluation_directory=evaluation,
            output_directory=output,
            geometry_loader=_geometry_loader,
        )

    assert called == ["auth"]
    assert not output.exists()


def test_six_figure_report_is_append_only_and_checkable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation = tmp_path / "evaluation"
    output = tmp_path / "report"
    completion = _publish_synthetic_evaluation(evaluation)
    auth_calls: list[str] = []

    def authenticate(*_args: object, **_kwargs: object) -> dict[str, object]:
        auth_calls.append("auth")
        return completion

    monkeypatch.setattr(
        reporting, "authenticate_external_evaluation_completion", authenticate
    )

    manifest = build_external_evaluation_report(
        PROJECT_ROOT,
        evaluation_directory=evaluation,
        output_directory=output,
        geometry_loader=_geometry_loader,
    )

    assert manifest["state"] == "read_only_external_evaluation_evidence"
    assert manifest["figure_ids"] == list(FIGURE_FILES)
    assert set(manifest["figures"]) == set(FIGURE_FILES)
    assert manifest["global_counts"] == {
        "rows": 120,
        "city_dates": 30,
        "spatial_blocks": 6,
    }
    assert (output / MANIFEST_FILENAME).is_file()
    markdown = (output / RESULTS_FILENAME).read_text(encoding="utf-8")
    assert "External refit or recalibration: **No**" in markdown
    assert "120 rows · 30 city-dates · 6 5 km blocks" in markdown
    for figure_id, filename in FIGURE_FILES.items():
        path = output / filename
        assert path.is_file()
        assert manifest["figures"][figure_id]["row_count"] == 120
        assert manifest["figures"][figure_id]["city_date_count"] == 30
        assert manifest["figures"][figure_id]["spatial_block_count"] == 6
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert "Rows: 120" in image.info["Description"]
            assert "External year: 2025" in image.info["Description"]
            assert "Software:" in image.info["Description"]

    checked = authenticate_external_evaluation_report(
        PROJECT_ROOT,
        evaluation_directory=evaluation,
        output_directory=output,
        geometry_loader=_geometry_loader,
    )
    assert checked == manifest
    assert auth_calls[0] == "auth" and len(auth_calls) >= 2
    with pytest.raises(ExternalEvaluationReportingError, match="append-only"):
        build_external_evaluation_report(
            PROJECT_ROOT,
            evaluation_directory=evaluation,
            output_directory=output,
            geometry_loader=_geometry_loader,
        )

    tampered = output / FIGURE_FILES["external_city_mae"]
    tampered.write_bytes(tampered.read_bytes() + b"tamper")
    before = len(auth_calls)
    with pytest.raises(ExternalEvaluationReportingError, match="Figure changed"):
        authenticate_external_evaluation_report(
            PROJECT_ROOT,
            evaluation_directory=evaluation,
            output_directory=output,
            geometry_loader=_geometry_loader,
        )
    assert len(auth_calls) == before + 1

