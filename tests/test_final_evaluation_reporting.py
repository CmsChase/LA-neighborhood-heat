from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from shapely.geometry import box

import la_heat.final_evaluation_protocol as protocol
import la_heat.final_evaluation_reporting as reporting
from la_heat.final_evaluation_reporting import (
    BOOTSTRAP_FILENAME,
    DATE_FIGURE_FILENAME,
    GATES_FILENAME,
    HOTSPOT_FIGURE_FILENAME,
    HOTSPOT_PER_DATE_FILENAME,
    HOTSPOT_SUMMARY_FILENAME,
    MAP_FIGURE_FILENAME,
    MODEL_METRICS_FILENAME,
    PAIRED_ERRORS_FILENAME,
    PER_DATE_METRICS_FILENAME,
    QA_MISSINGNESS_FILENAME,
    REPORT_OUTPUT_FILENAMES,
    SENSOR_PER_DATE_FILENAME,
    SENSOR_SUMMARY_FILENAME,
    SENTINEL_STRATUM_FILENAME,
    TRACT_MAP_SUMMARY_FILENAME,
    FinalEvaluationReportingError,
    FinalEvaluationReportingSettings,
    _prepare_tract_map_frame,
    build_final_evaluation_reporting,
    generate_final_evaluation_reports,
)


def _settings() -> FinalEvaluationReportingSettings:
    return FinalEvaluationReportingSettings(
        bootstrap_seed=20_260_722,
        bootstrap_replicates=128,
        confidence_level=0.95,
    )


def _synthetic_joined_rows() -> pd.DataFrame:
    dates = pd.to_datetime(
        ["2025-02-01", "2025-04-02", "2025-06-03", "2025-08-04"]
    )
    sensors = ["landsat-8", "landsat-9", "landsat-8", "landsat-9"]
    records: list[dict[str, object]] = []
    for date_index, (target_date, sensor) in enumerate(zip(dates, sensors, strict=True)):
        date_usable = date_index != 2
        coverage_gate = date_index != 1
        for tract_index in range(10):
            geoid = f"06037{tract_index + 1:06d}"
            latent_truth = 28.0 + date_index * 1.25 + tract_index * 0.45
            target_available = not (date_index == 3 and tract_index == 9)
            retained_tract_count = 9 if date_index == 3 else 10
            b1_error = 1.2 + 0.1 * (tract_index % 3)
            m2_error = 0.18 + 0.02 * (tract_index % 2)
            if not date_usable:
                b1_error = 40.0
                m2_error = -35.0
            records.append(
                {
                    "tract_geoid": geoid,
                    "target_date": target_date,
                    "spatial_block": f"block-{tract_index // 2}",
                    "sensor": sensor,
                    "sentinel_available": tract_index % 4 != 0,
                    "target_available": target_available,
                    "date_usable": date_usable,
                    "relative_endpoint_coverage_pass": coverage_gate,
                    "relative_hotspot_top20": pd.NA,
                    "y_true": latent_truth if target_available else np.nan,
                    "y_pred_b1": latent_truth + b1_error,
                    "y_pred_m2": latent_truth + m2_error,
                    "tract_exclusion_reason": (
                        "" if target_available else "insufficient_valid_fraction"
                    ),
                    "date_exclusion_reason": (
                        ""
                        if date_usable
                        else "insufficient_union_city_footprint"
                    ),
                    "source_scene_count": 1,
                    "source_scene_ids": f"scene-{date_index}",
                    "rasterized_pixel_count": 100,
                    "footprint_pixel_count": 100,
                    "eligible_pixel_count_static": 80,
                    "valid_pixel_count": 60 if target_available else 10,
                    "footprint_fraction": 1.0,
                    "valid_fraction": 0.75 if target_available else 0.125,
                    "median_st_uncertainty_k": (
                        1.5 if target_available else np.nan
                    ),
                    "p90_st_uncertainty_k": (
                        1.8 if target_available else np.nan
                    ),
                    "median_cloud_distance_km": (
                        2.5 if target_available else np.nan
                    ),
                    "union_city_coverage_fraction": (
                        1.0 if date_usable else 0.95
                    ),
                    "retained_tract_count": retained_tract_count,
                    "retained_tract_fraction": retained_tract_count / 10.0,
                    "minimum_eligible_joint_cell_retention_fraction": 0.90,
                }
            )
    frame = pd.DataFrame(records)
    for _, group in frame.groupby("target_date", sort=True):
        if not bool(group["relative_endpoint_coverage_pass"].iloc[0]):
            continue
        available = group.loc[group["target_available"]].sort_values(
            ["y_true", "tract_geoid"],
            ascending=[False, True],
            kind="stable",
        )
        k = math.ceil(0.20 * len(available))
        frame.loc[available.index, "relative_hotspot_top20"] = False
        frame.loc[available.index[:k], "relative_hotspot_top20"] = True
    frame["relative_hotspot_top20"] = frame[
        "relative_hotspot_top20"
    ].astype("boolean")
    return frame


def _synthetic_tract_geometries() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "GEOID": [f"06037{index + 1:06d}" for index in range(10)],
            "spatial_block": [f"block-{index // 2}" for index in range(10)],
        },
        geometry=[
            box(index % 5, index // 5, index % 5 + 1, index // 5 + 1)
            for index in range(10)
        ],
        crs="EPSG:3310",
    )


def test_pure_reporting_filters_cohort_and_preserves_all_date_qa() -> None:
    joined = _synthetic_joined_rows()
    before = joined.copy(deep=True)

    reports = build_final_evaluation_reporting(joined, _settings())

    assert_frame_equal(joined, before)
    assert len(reports.evaluation_rows) == 29
    assert reports.evaluation_rows["target_date"].nunique() == 3
    assert pd.Timestamp("2025-06-03") not in set(
        reports.evaluation_rows["target_date"]
    )
    assert reports.evaluation_rows["target_available"].all()
    assert reports.evaluation_rows["date_usable"].all()

    metrics = reports.model_metrics.set_index("model_id")
    assert metrics.loc["B1", "independent_date_count"] == 3
    assert metrics.loc["M2", "independent_spatial_block_count"] == 5
    assert metrics.loc["M2", "equal_date_weighted_mae_c"] < metrics.loc[
        "B1", "equal_date_weighted_mae_c"
    ]
    assert metrics.loc["B1", "equal_date_weighted_mae_c"] < 2.0
    assert len(reports.per_date_metrics) == 6

    paired = reports.paired_date_block_errors
    assert len(paired) == 15
    assert paired["row_count"].sum() == 29
    bootstrap = dict(reports.crossed_bootstrap)
    assert bootstrap["bootstrap_method"] == "crossed_date_spatial_block"
    assert bootstrap["independent_date_count"] == 3
    assert bootstrap["independent_spatial_block_count"] == 5
    assert bootstrap["tract_date_row_count"] == 29
    assert bootstrap["relative_mae_improvement_fraction"] > 0.10
    assert reports.protocol_gates[
        "overall_protocol_success_gate_pass"
    ].all()

    assert reports.hotspot_per_date["target_date"].nunique() == 2
    assert len(reports.hotspot_per_date) == 4
    assert reports.hotspot_per_date["predicted_positive_count"].equals(
        reports.hotspot_per_date["exact_top_k"]
    )
    assert reports.hotspot_summary["independent_date_count"].eq(2).all()

    qa = reports.qa_missingness_summary
    assert len(qa) == 5
    assert qa["summary_level"].value_counts().to_dict() == {
        "date": 4,
        "overall": 1,
    }
    overall = qa.loc[qa["summary_level"].eq("overall")].iloc[0]
    assert overall["inventory_key_count"] == 40
    assert overall["independent_date_count"] == 4
    assert overall["target_unavailable_count"] == 1
    assert overall["evaluation_cohort_count"] == 29

    assert len(reports.sensor_per_date_metrics) == 6
    assert set(reports.sensor_summary["sensor"]) == {"landsat-8", "landsat-9"}
    assert len(reports.sentinel_stratum_summary) == 4
    assert set(reports.sentinel_stratum_summary["sentinel_stratum"]) == {
        "sentinel_complete",
        "sentinel_all_five_missing",
    }


def test_crossed_bootstrap_is_deterministic_for_frozen_seed() -> None:
    joined = _synthetic_joined_rows()
    first = build_final_evaluation_reporting(joined, _settings())
    second = build_final_evaluation_reporting(joined, _settings())

    assert dict(first.crossed_bootstrap) == dict(second.crossed_bootstrap)
    assert_frame_equal(
        first.paired_date_block_errors,
        second.paired_date_block_errors,
    )


def test_reporting_rejects_hotspot_label_drift_and_writes_nothing(
    tmp_path: Path,
) -> None:
    joined = _synthetic_joined_rows()
    date = pd.Timestamp("2025-02-01")
    date_rows = joined.loc[joined["target_date"].eq(date)]
    true_index = date_rows.index[
        date_rows["relative_hotspot_top20"].fillna(False)
    ][0]
    false_index = date_rows.index[
        ~date_rows["relative_hotspot_top20"].fillna(False)
    ][0]
    joined.loc[true_index, "relative_hotspot_top20"] = False
    joined.loc[false_index, "relative_hotspot_top20"] = True
    staging = tmp_path / "staging"

    with pytest.raises(
        FinalEvaluationReportingError,
        match="exact target-top-k",
    ):
        generate_final_evaluation_reports(
            joined,
            _settings(),
            staging,
            tract_geometries=_synthetic_tract_geometries(),
        )

    assert not staging.exists()


def test_reporting_rejects_values_on_qa_unavailable_rows() -> None:
    joined = _synthetic_joined_rows()
    unavailable = joined.index[~joined["target_available"]][0]
    joined.loc[unavailable, "y_true"] = 99.0

    with pytest.raises(
        FinalEvaluationReportingError,
        match="target-unavailable",
    ):
        build_final_evaluation_reporting(joined, _settings())


def test_reporting_rejects_integer_hotspot_labels() -> None:
    joined = _synthetic_joined_rows()
    joined["relative_hotspot_top20"] = joined[
        "relative_hotspot_top20"
    ].astype(object)
    labeled = joined.index[joined["relative_hotspot_top20"].notna()][0]
    joined.loc[labeled, "relative_hotspot_top20"] = 1

    with pytest.raises(
        FinalEvaluationReportingError,
        match="booleans or missing",
    ):
        build_final_evaluation_reporting(joined, _settings())


def test_generate_reports_writes_exact_tables_and_figures_without_console_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "report-staging"
    artifacts = generate_final_evaluation_reports(
        _synthetic_joined_rows(),
        _settings(),
        output,
        tract_geometries=_synthetic_tract_geometries(),
    )

    assert set(path.name for path in output.iterdir()) == set(REPORT_OUTPUT_FILENAMES)
    assert set(artifacts.output_paths) == set(REPORT_OUTPUT_FILENAMES)
    assert (output / MAP_FIGURE_FILENAME).read_bytes().startswith(b"%PDF")
    assert (output / DATE_FIGURE_FILENAME).read_bytes().startswith(b"\x89PNG")
    assert (output / HOTSPOT_FIGURE_FILENAME).read_bytes().startswith(b"\x89PNG")
    for filename, contract in protocol.FIGURE_OUTPUT_CONTRACTS.items():
        protocol._inspect_figure_output(
            output / filename,
            contract=contract,
        )
    replay = tmp_path / "figure-replay"
    replay.mkdir()
    map_table = protocol._read_output_table(
        output / TRACT_MAP_SUMMARY_FILENAME,
        filename=TRACT_MAP_SUMMARY_FILENAME,
    )
    geography = _synthetic_tract_geometries().loc[:, ["GEOID", "geometry"]]
    geography["tract_geoid"] = geography["GEOID"].astype("string")
    replay_map = gpd.GeoDataFrame(
        map_table.merge(
            geography.loc[:, ["tract_geoid", "geometry"]],
            on="tract_geoid",
            how="left",
            validate="one_to_one",
        ),
        geometry="geometry",
        crs="EPSG:3310",
    )
    replay_per_date = protocol._read_output_table(
        output / PER_DATE_METRICS_FILENAME,
        filename=PER_DATE_METRICS_FILENAME,
    )
    replay_per_date["target_date"] = pd.to_datetime(
        replay_per_date["target_date"]
    )
    replay_hotspot = protocol._read_output_table(
        output / HOTSPOT_PER_DATE_FILENAME,
        filename=HOTSPOT_PER_DATE_FILENAME,
    )
    replay_hotspot["target_date"] = pd.to_datetime(
        replay_hotspot["target_date"]
    )
    reporting._write_tract_maps(
        replay_map,
        replay / MAP_FIGURE_FILENAME,
    )
    reporting._write_per_date_figure(
        replay_per_date,
        replay / DATE_FIGURE_FILENAME,
        _settings(),
    )
    reporting._write_hotspot_figure(
        replay_hotspot,
        replay / HOTSPOT_FIGURE_FILENAME,
        _settings(),
    )
    for filename in (
        MAP_FIGURE_FILENAME,
        DATE_FIGURE_FILENAME,
        HOTSPOT_FIGURE_FILENAME,
    ):
        assert (replay / filename).read_bytes() == (output / filename).read_bytes()
    assert all((output / name).stat().st_size > 100 for name in REPORT_OUTPUT_FILENAMES)

    expected_csvs = {
        MODEL_METRICS_FILENAME,
        PER_DATE_METRICS_FILENAME,
        PAIRED_ERRORS_FILENAME,
        GATES_FILENAME,
        HOTSPOT_PER_DATE_FILENAME,
        HOTSPOT_SUMMARY_FILENAME,
        SENSOR_PER_DATE_FILENAME,
        SENSOR_SUMMARY_FILENAME,
        SENTINEL_STRATUM_FILENAME,
        QA_MISSINGNESS_FILENAME,
        TRACT_MAP_SUMMARY_FILENAME,
    }
    for filename in expected_csvs:
        assert not pd.read_csv(output / filename).empty
        protocol._read_output_table(output / filename, filename=filename)
    mapped = pd.read_csv(
        output / TRACT_MAP_SUMMARY_FILENAME,
        dtype={"tract_geoid": "string"},
    )
    assert mapped["tract_geoid"].str.fullmatch(r"\d{11}").all()
    assert mapped["tract_geoid"].str.startswith("0").all()
    bootstrap = json.loads((output / BOOTSTRAP_FILENAME).read_text(encoding="utf-8"))
    assert bootstrap["bootstrap_replicates"] == 128
    assert bootstrap["random_row_sampling_used"] is False

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_tract_map_uses_all_usable_dates_and_authenticated_geography() -> None:
    reports = build_final_evaluation_reporting(
        _synthetic_joined_rows(),
        _settings(),
    )
    mapped = _prepare_tract_map_frame(
        reports.evaluation_rows,
        _synthetic_tract_geometries(),
    ).set_index("tract_geoid")
    geoid = "06037000001"
    source = reports.evaluation_rows.loc[
        reports.evaluation_rows["tract_geoid"].eq(geoid)
    ]

    assert len(mapped) == 10
    assert mapped.crs.to_string() == "EPSG:3310"
    assert mapped.loc[geoid, "evaluated_date_count"] == source[
        "target_date"
    ].nunique()
    assert mapped.loc[geoid, "evaluated_date_fraction"] == pytest.approx(
        source["target_date"].nunique()
        / reports.evaluation_rows["target_date"].nunique()
    )
    assert mapped.loc[geoid, "observed_lst_c"] == pytest.approx(
        source["y_true"].mean()
    )
    assert mapped.loc[geoid, "m2_residual_c"] == pytest.approx(
        source["m2_error_c"].mean()
    )

    extra = gpd.GeoDataFrame(
        {
            "GEOID": ["06037999999"],
            "spatial_block": ["block-extra"],
        },
        geometry=[box(6, 0, 7, 1)],
        crs="EPSG:3310",
    )
    geography = gpd.GeoDataFrame(
        pd.concat([_synthetic_tract_geometries(), extra], ignore_index=True),
        geometry="geometry",
        crs="EPSG:3310",
    )
    with_zero_support = _prepare_tract_map_frame(
        reports.evaluation_rows,
        geography,
    ).set_index("tract_geoid")
    assert with_zero_support.loc[
        "06037999999", "evaluated_date_count"
    ] == 0
    assert with_zero_support.loc[
        "06037999999", "evaluated_date_fraction"
    ] == 0.0
    assert pd.isna(with_zero_support.loc["06037999999", "observed_lst_c"])


def test_protocol_reconciles_exact_bootstrap_payload_and_paired_cells() -> None:
    reports = build_final_evaluation_reporting(
        _synthetic_joined_rows(),
        _settings(),
    )
    config = replace(
        protocol.load_final_evaluation_config(),
        bootstrap={
            "method": _settings().bootstrap_method,
            "sampling_unit": _settings().bootstrap_sampling_unit,
            "seed": _settings().bootstrap_seed,
            "replicates": _settings().bootstrap_replicates,
            "confidence_level": _settings().confidence_level,
        },
    )
    protocol._assert_bootstrap_and_paired_output(
        reports.crossed_bootstrap,
        paired=reports.paired_date_block_errors,
        evaluation=reports.evaluation_rows,
        model_metrics=reports.model_metrics,
        config=config,
    )
    protocol._assert_replayed_report_outputs(
        evaluation=reports.evaluation_rows,
        model_metrics=reports.model_metrics,
        per_date=reports.per_date_metrics,
        paired=reports.paired_date_block_errors,
        bootstrap=reports.crossed_bootstrap,
        gates=reports.protocol_gates,
        hotspot_per_date=reports.hotspot_per_date,
        hotspot_summary=reports.hotspot_summary,
        sensor_per_date=reports.sensor_per_date_metrics,
        sensor_summary=reports.sensor_summary,
        sentinel=reports.sentinel_stratum_summary,
        config=config,
    )

    tampered = dict(reports.crossed_bootstrap)
    tampered["unexpected"] = True
    with pytest.raises(
        protocol.FinalEvaluationProtocolError,
        match="exact structure",
    ):
        protocol._assert_bootstrap_and_paired_output(
            tampered,
            paired=reports.paired_date_block_errors,
            evaluation=reports.evaluation_rows,
            model_metrics=reports.model_metrics,
            config=config,
        )

    tampered_draws = dict(reports.crossed_bootstrap)
    tampered_draws["probability_improvement_gt_zero"] = 0.123
    with pytest.raises(
        protocol.FinalEvaluationProtocolError,
        match="draws do not replay",
    ):
        protocol._assert_replayed_report_outputs(
            evaluation=reports.evaluation_rows,
            model_metrics=reports.model_metrics,
            per_date=reports.per_date_metrics,
            paired=reports.paired_date_block_errors,
            bootstrap=tampered_draws,
            gates=reports.protocol_gates,
            hotspot_per_date=reports.hotspot_per_date,
            hotspot_summary=reports.hotspot_summary,
            sensor_per_date=reports.sensor_per_date_metrics,
            sensor_summary=reports.sensor_summary,
            sentinel=reports.sentinel_stratum_summary,
            config=config,
        )


def test_bootstrap_point_estimate_must_reproduce_primary_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = reporting._paired_bootstrap

    def drifted(*args: object, **kwargs: object):
        cells, payload = original(*args, **kwargs)
        payload = dict(payload)
        payload["target_model_point_mae_c"] += 0.5
        return cells, payload

    monkeypatch.setattr(reporting, "_paired_bootstrap", drifted)
    with pytest.raises(
        FinalEvaluationReportingError,
        match="does not reproduce",
    ):
        build_final_evaluation_reporting(
            _synthetic_joined_rows(),
            _settings(),
        )
