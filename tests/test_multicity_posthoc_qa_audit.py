from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import la_heat.multicity.posthoc_qa_audit as audit_module
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.posthoc_qa_audit import (
    REPORT_FILENAME,
    SUMMARY_FILENAME,
    authenticate_posthoc_qa_audit,
    build_posthoc_qa_audit,
)
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)


def _committed(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _fixture(root: Path) -> tuple[dict[str, object], Path, Path]:
    evaluation = root / "evaluation"
    targets_root = root / "targets"
    evaluation.mkdir(parents=True)
    city_targets: dict[str, object] = {}
    date_metric_rows: list[dict[str, object]] = []
    for city_index, city_id in enumerate(EXTERNAL_CITY_IDS):
        city_dir = targets_root / city_id
        city_dir.mkdir(parents=True)
        dates = ["2025-07-17", "2025-07-25"] if city_id == "houston_tx" else [
            "2025-07-17",
            "2025-07-25",
        ]
        target_rows: list[dict[str, object]] = []
        summary_rows: list[dict[str, object]] = []
        for target_date in dates:
            for tract_index in range(4):
                anomaly = city_id == "houston_tx" and target_date == "2025-07-25"
                target_rows.append(
                    {
                        "target_date": target_date,
                        "target_available": True,
                        "date_usable": True,
                        "target_lst_c": (
                            [-5.0, 0.0, 20.0, 45.0][tract_index]
                            if anomaly
                            else 35.0 + city_index + tract_index
                        ),
                        "median_st_uncertainty_k": 4.0 if anomaly else 2.0,
                        "p90_st_uncertainty_k": 4.5 if anomaly else 2.5,
                    }
                )
            frame = pd.DataFrame(target_rows[-4:])
            summary_rows.append(
                {
                    "target_date": target_date,
                    "overpass_id": f"landsat-9_{target_date}",
                    "platform": "landsat-9",
                    "tract_count": 4,
                    "retained_tract_count": 4,
                    "retained_tract_fraction": 1.0,
                    "date_usable": True,
                    "date_exclusion_reason": "",
                    "median_target_lst_c": float(frame["target_lst_c"].median()),
                    "p05_target_lst_c": float(frame["target_lst_c"].quantile(0.05)),
                    "p95_target_lst_c": float(frame["target_lst_c"].quantile(0.95)),
                }
            )
            b1_mae = 8.0 if anomaly else 2.0 + city_index
            m2_mae = 6.0 if anomaly else 1.0 + city_index
            date_metric_rows.append(
                {
                    "city_id": city_id,
                    "target_date": target_date,
                    "row_count": 4,
                    "b1_mae_c": b1_mae,
                    "m2_mae_c": m2_mae,
                    "m2_interval_coverage": 0.5 if anomaly else 0.9,
                    "m2_mean_interval_width_c": 10.0,
                }
            )
        targets = pd.DataFrame(target_rows)
        date_summary = pd.DataFrame(summary_rows)
        target_path = city_dir / "targets.parquet"
        summary_path = city_dir / "date_summary.parquet"
        targets.to_parquet(target_path, index=False)
        date_summary.to_parquet(summary_path, index=False)
        city_targets[city_id] = {
            "directory": city_dir.relative_to(root).as_posix(),
            "output_files": {
                "targets.parquet": parquet_file_record(target_path, targets),
                "date_summary.parquet": parquet_file_record(summary_path, date_summary),
            },
        }

    date_metrics = pd.DataFrame(date_metric_rows)
    date_metrics_path = evaluation / "date_metrics.parquet"
    date_metrics.to_parquet(date_metrics_path, index=False)
    city_points = date_metrics.groupby("city_id").agg(
        b1=("b1_mae_c", "mean"), m2=("m2_mae_c", "mean")
    )
    b1 = float(city_points["b1"].mean())
    m2 = float(city_points["m2"].mean())
    formal_summary = {
        "state": "inconclusive_sample_size",
        "primary": {
            "b1_equal_city_equal_date_mae_c": b1,
            "m2_equal_city_equal_date_mae_c": m2,
            "relative_mae_improvement_fraction": 1.0 - m2 / b1,
        },
        "point_prediction_gates": {"success": False},
        "reliability": {"success": False},
    }
    summary_path = evaluation / "summary.json"
    summary_path.write_text(json.dumps(formal_summary), encoding="utf-8")
    external_completion = _committed(
        {
            "state": "three_city_external_targets_complete",
            "city_targets": city_targets,
        }
    )
    external_path = root / "external_complete.json"
    atomic_json(external_completion, external_path)
    evaluation_completion = _committed(
        {
            "input_bindings": {
                "external_target_completion_commit_sha256": external_completion[
                    "commit_sha256"
                ]
            },
            "output_files": {
                "summary.json": {
                    "bytes": summary_path.stat().st_size,
                    "sha256": sha256_file(summary_path),
                },
                "date_metrics.parquet": parquet_file_record(
                    date_metrics_path, date_metrics
                ),
            },
        }
    )
    return evaluation_completion, evaluation, external_path


def test_posthoc_audit_is_aggregate_only_and_preserves_formal_result(
    tmp_path: Path, monkeypatch
) -> None:
    completion, evaluation, external_path = _fixture(tmp_path)
    calls: list[str] = []

    def authenticate(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls.append("formal_auth")
        return completion

    original_read_parquet = audit_module.pd.read_parquet

    def read_parquet(*args: object, **kwargs: object) -> pd.DataFrame:
        calls.append("read")
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(
        audit_module, "authenticate_external_evaluation_completion", authenticate
    )
    monkeypatch.setattr(audit_module.pd, "read_parquet", read_parquet)
    before = {
        path.name: sha256_file(path)
        for path in (evaluation / "summary.json", evaluation / "date_metrics.parquet")
    }
    output = tmp_path / "reports"
    payload = build_posthoc_qa_audit(
        tmp_path,
        evaluation_directory=evaluation,
        external_completion_path=external_path,
        output_directory=output,
    )
    after = {
        path.name: sha256_file(path)
        for path in (evaluation / "summary.json", evaluation / "date_metrics.parquet")
    }

    assert calls[0] == "formal_auth"
    assert before == after
    assert payload["analysis_class"] == "non_confirmatory_posthoc_read_only_qa"
    assert payload["formal_result_unchanged"] is True
    assert payload["leave_one_date_out_sensitivity"]["gates_recomputed"] is False
    assert payload["leave_one_date_out_sensitivity"]["bootstrap_recomputed"] is False
    assert payload["observed_anomaly"]["target_lst_below_0c_count"] == 1
    assert payload["observed_anomaly"]["median_st_uncertainty_k"] == 4.0
    assert len(payload["date_support"]) == 6
    serialized = json.dumps(payload)
    assert "tract_geoid" not in serialized

    authenticated = authenticate_posthoc_qa_audit(
        tmp_path,
        evaluation_directory=evaluation,
        external_completion_path=external_path,
        output_directory=output,
    )
    assert authenticated == payload
    assert (output / SUMMARY_FILENAME).is_file()
    for path in (output / SUMMARY_FILENAME, output / REPORT_FILENAME):
        raw = path.read_bytes()
        assert b"\r" not in raw
        assert not raw.endswith(b"\n\n")
        assert all(line == line.rstrip() for line in raw.decode("utf-8").splitlines())
    report = (output / REPORT_FILENAME).read_text(encoding="utf-8")
    assert "NON-CONFIRMATORY / POST-HOC" in report
    assert "No bootstrap interval or formal gate was recalculated" in report
