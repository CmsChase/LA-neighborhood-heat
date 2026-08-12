"""Read-only, non-confirmatory QA audit of the frozen external result.

This module deliberately runs only after the external evaluation authenticates.
It summarizes target support and one observed Houston date anomaly without
refitting a model, replacing the published evaluation, or re-evaluating gates.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.multicity.external_evaluation import (
    OUTPUT_DIRECTORY as EVALUATION_DIRECTORY,
)
from la_heat.multicity.external_evaluation import (
    authenticate_external_evaluation_completion,
)
from la_heat.multicity.external_target_worker import EXTERNAL_COMPLETION
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.provenance import (
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "multicity-external-posthoc-qa-v1"
OUTPUT_DIRECTORY: Final = Path("reports/tables/multicity_external_posthoc_qa")
SUMMARY_FILENAME: Final = "posthoc_qa_summary.json"
REPORT_FILENAME: Final = "POSTHOC_QA_REPORT.md"
ANOMALY_CITY_ID: Final = "houston_tx"
ANOMALY_DATE: Final = "2025-07-25"


class PosthocQAAuditError(RuntimeError):
    """Raised when the post-hoc audit cannot reproduce its evidence boundary."""


def _atomic_lf_text(text: str, destination: Path) -> None:
    """Write UTF-8 text atomically with platform-independent LF endings."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    temporary.replace(destination)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise PosthocQAAuditError(f"{label} must stay inside the project")
    return path


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PosthocQAAuditError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise PosthocQAAuditError(f"{label} must be a JSON object")
    unsigned = dict(payload)
    recorded = unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise PosthocQAAuditError(f"{label} commit is invalid")
    return payload


def _json_number(value: object) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return int(bool(value))
    if isinstance(value, (int, np.integer)):
        return int(value)
    return float(value)


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _verified_target_tables(
    root: Path,
    evaluation_completion: Mapping[str, Any],
    external_completion_path: str | Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any], dict[str, Any]]:
    completion_path = _inside(root, external_completion_path, label="Target completion")
    external_completion = _read_committed(
        completion_path, label="External-target completion"
    )
    bound_commit = evaluation_completion.get("input_bindings", {}).get(
        "external_target_completion_commit_sha256"
    )
    if (
        external_completion.get("state") != "three_city_external_targets_complete"
        or external_completion.get("commit_sha256") != bound_commit
    ):
        raise PosthocQAAuditError(
            "External targets do not match the authenticated formal evaluation"
        )
    city_records = external_completion.get("city_targets")
    if not isinstance(city_records, dict) or set(city_records) != set(EXTERNAL_CITY_IDS):
        raise PosthocQAAuditError("External-target city cohort changed")

    target_tables: dict[str, pd.DataFrame] = {}
    date_summaries: dict[str, pd.DataFrame] = {}
    input_records: dict[str, Any] = {}
    for city_id in EXTERNAL_CITY_IDS:
        city_record = city_records[city_id]
        if not isinstance(city_record, dict):
            raise PosthocQAAuditError(f"Target record is invalid for {city_id}")
        directory = _inside(root, city_record.get("directory", ""), label="City targets")
        files = city_record.get("output_files")
        if not isinstance(files, dict):
            raise PosthocQAAuditError(f"Target output records are missing for {city_id}")
        loaded: dict[str, pd.DataFrame] = {}
        for name in ("targets.parquet", "date_summary.parquet"):
            path = directory / name
            record = files.get(name)
            if not path.is_file() or not isinstance(record, dict):
                raise PosthocQAAuditError(f"Target QA input is missing: {city_id}/{name}")
            try:
                frame = pd.read_parquet(path)
            except Exception as error:  # noqa: BLE001 - normalize parquet errors
                raise PosthocQAAuditError(
                    f"Target QA input cannot be read: {city_id}/{name}"
                ) from error
            if record != parquet_file_record(path, frame):
                raise PosthocQAAuditError(
                    f"Target QA input failed authentication: {city_id}/{name}"
                )
            loaded[name] = frame
            input_records[f"{city_id}/{name}"] = {
                "path": path.relative_to(root).as_posix(),
                **record,
            }
        target_tables[city_id] = loaded["targets.parquet"]
        date_summaries[city_id] = loaded["date_summary.parquet"]
    completion_record = {
        "path": completion_path.relative_to(root).as_posix(),
        "bytes": completion_path.stat().st_size,
        "sha256": sha256_file(completion_path),
        "commit_sha256": external_completion["commit_sha256"],
    }
    return target_tables, date_summaries, completion_record, input_records


def _date_support_rows(
    target_tables: Mapping[str, pd.DataFrame],
    date_summaries: Mapping[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    required_targets = {
        "target_date",
        "target_available",
        "date_usable",
        "target_lst_c",
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
    }
    required_summary = {
        "target_date",
        "overpass_id",
        "platform",
        "tract_count",
        "retained_tract_count",
        "retained_tract_fraction",
        "date_usable",
        "date_exclusion_reason",
        "median_target_lst_c",
        "p05_target_lst_c",
        "p95_target_lst_c",
    }
    rows: list[dict[str, Any]] = []
    for city_id in EXTERNAL_CITY_IDS:
        targets = target_tables[city_id].copy()
        summary = date_summaries[city_id].copy()
        if missing := required_targets - set(targets):
            raise PosthocQAAuditError(
                f"{city_id} target QA columns are missing: {sorted(missing)}"
            )
        if missing := required_summary - set(summary):
            raise PosthocQAAuditError(
                f"{city_id} date-summary columns are missing: {sorted(missing)}"
            )
        targets["target_date"] = pd.to_datetime(
            targets["target_date"], errors="raise"
        ).dt.strftime("%Y-%m-%d")
        summary["target_date"] = pd.to_datetime(
            summary["target_date"], errors="raise"
        ).dt.strftime("%Y-%m-%d")
        if summary["target_date"].duplicated().any():
            raise PosthocQAAuditError(f"{city_id} date summary is duplicated")
        grouped = {date: frame for date, frame in targets.groupby("target_date")}
        if set(grouped) != set(summary["target_date"]):
            raise PosthocQAAuditError(f"{city_id} target and date-summary dates differ")
        for date_row in summary.sort_values("target_date").to_dict("records"):
            target_date = str(date_row["target_date"])
            frame = grouped[target_date]
            available = frame.loc[frame["target_available"].astype(bool)].copy()
            planned_count = int(date_row["tract_count"])
            available_count = len(available)
            date_usable_values = frame["date_usable"].astype(bool).unique()
            if (
                len(frame) != planned_count
                or available_count != int(date_row["retained_tract_count"])
                or len(date_usable_values) != 1
                or bool(date_usable_values[0]) != bool(date_row["date_usable"])
            ):
                raise PosthocQAAuditError(f"{city_id} {target_date} support counts drifted")
            rows.append(
                {
                    "city_id": city_id,
                    "target_date": target_date,
                    "overpass_id": str(date_row["overpass_id"]),
                    "platform": str(date_row["platform"]),
                    "planned_tract_count": planned_count,
                    "available_target_count": available_count,
                    "available_target_fraction": float(
                        date_row["retained_tract_fraction"]
                    ),
                    "date_usable": bool(date_row["date_usable"]),
                    "date_exclusion_reason": _optional_text(
                        date_row["date_exclusion_reason"]
                    ),
                    "target_lst_mean_c": _json_number(available["target_lst_c"].mean()),
                    "target_lst_std_c": _json_number(available["target_lst_c"].std()),
                    "target_lst_median_c": _json_number(
                        date_row["median_target_lst_c"]
                    ),
                    "target_lst_p05_c": _json_number(date_row["p05_target_lst_c"]),
                    "target_lst_p95_c": _json_number(date_row["p95_target_lst_c"]),
                    "target_lst_below_0c_count": int(
                        available["target_lst_c"].lt(0).sum()
                    ),
                    "median_st_uncertainty_k": _json_number(
                        available["median_st_uncertainty_k"].median()
                    ),
                    "median_p90_st_uncertainty_k": _json_number(
                        available["p90_st_uncertainty_k"].median()
                    ),
                }
            )
    return sorted(rows, key=lambda row: (row["city_id"], row["target_date"]))


def _city_support(date_support: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for city_id in EXTERNAL_CITY_IDS:
        rows = [row for row in date_support if row["city_id"] == city_id]
        planned = sum(int(row["planned_tract_count"]) for row in rows)
        available = sum(int(row["available_target_count"]) for row in rows)
        usable_rows = [row for row in rows if row["date_usable"]]
        summaries.append(
            {
                "city_id": city_id,
                "planned_date_count": len(rows),
                "usable_date_count": len(usable_rows),
                "usable_date_fraction": len(usable_rows) / len(rows),
                "planned_tract_date_count": planned,
                "available_target_count": available,
                "available_target_fraction": available / planned,
                "usable_available_target_count": sum(
                    int(row["available_target_count"]) for row in usable_rows
                ),
            }
        )
    return summaries


def _equal_city_equal_date_point(dates: pd.DataFrame) -> dict[str, Any]:
    cities = (
        dates.groupby("city_id", observed=True)
        .agg(
            date_count=("target_date", "size"),
            row_count=("row_count", "sum"),
            b1_equal_date_mae_c=("b1_mae_c", "mean"),
            m2_equal_date_mae_c=("m2_mae_c", "mean"),
        )
        .reset_index()
        .sort_values("city_id")
    )
    b1 = float(cities["b1_equal_date_mae_c"].mean())
    m2 = float(cities["m2_equal_date_mae_c"].mean())
    return {
        "usable_row_count": int(cities["row_count"].sum()),
        "usable_city_date_count": int(cities["date_count"].sum()),
        "b1_equal_city_equal_date_mae_c": b1,
        "m2_equal_city_equal_date_mae_c": m2,
        "relative_mae_improvement_fraction": 1.0 - m2 / b1,
        "by_city": [
            {
                "city_id": str(row.city_id),
                "date_count": int(row.date_count),
                "row_count": int(row.row_count),
                "b1_equal_date_mae_c": float(row.b1_equal_date_mae_c),
                "m2_equal_date_mae_c": float(row.m2_equal_date_mae_c),
            }
            for row in cities.itertuples(index=False)
        ],
    }


def _sensitivity(
    date_metrics: pd.DataFrame,
    formal_summary: Mapping[str, Any],
    *,
    anomaly_city_id: str,
    anomaly_date: str,
) -> dict[str, Any]:
    required = {"city_id", "target_date", "row_count", "b1_mae_c", "m2_mae_c"}
    if missing := required - set(date_metrics):
        raise PosthocQAAuditError(f"Date metrics lack columns: {sorted(missing)}")
    dates = date_metrics.copy()
    dates["target_date"] = pd.to_datetime(dates["target_date"], errors="raise").dt.strftime(
        "%Y-%m-%d"
    )
    anomaly_mask = dates["city_id"].astype(str).eq(anomaly_city_id) & dates[
        "target_date"
    ].eq(anomaly_date)
    if int(anomaly_mask.sum()) != 1:
        raise PosthocQAAuditError("The requested observed city-date is not uniquely scored")
    full = _equal_city_equal_date_point(dates)
    published_primary = formal_summary.get("primary", {})
    for key in (
        "b1_equal_city_equal_date_mae_c",
        "m2_equal_city_equal_date_mae_c",
        "relative_mae_improvement_fraction",
    ):
        if not math.isclose(
            float(full[key]), float(published_primary.get(key, math.nan)), rel_tol=1e-12
        ):
            raise PosthocQAAuditError("Published primary point estimate did not reproduce")
    excluded = _equal_city_equal_date_point(dates.loc[~anomaly_mask])
    return {
        "selection_status": "observed_posthoc_date_diagnostic",
        "excluded_city_id": anomaly_city_id,
        "excluded_target_date": anomaly_date,
        "all_published_usable_dates": full,
        "excluding_observed_date": excluded,
        "relative_improvement_change_percentage_points": 100.0
        * (
            excluded["relative_mae_improvement_fraction"]
            - full["relative_mae_improvement_fraction"]
        ),
        "bootstrap_recomputed": False,
        "confidence_interval_computed": False,
        "gates_recomputed": False,
        "replaces_formal_result": False,
    }


def _anomaly_summary(
    date_support: list[dict[str, Any]],
    date_metrics: pd.DataFrame,
    *,
    anomaly_city_id: str,
    anomaly_date: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in date_support
        if row["city_id"] == anomaly_city_id and row["target_date"] == anomaly_date
    ]
    if len(matches) != 1:
        raise PosthocQAAuditError("The requested observed city-date support is unavailable")
    anomaly = dict(matches[0])
    peers = [
        row
        for row in date_support
        if row["city_id"] == anomaly_city_id
        and row["target_date"] != anomaly_date
        and row["date_usable"]
    ]
    if not peers:
        raise PosthocQAAuditError("No other usable dates exist for the anomaly comparison")

    metric_dates = date_metrics.copy()
    metric_dates["target_date"] = pd.to_datetime(
        metric_dates["target_date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    metric = metric_dates.loc[
        metric_dates["city_id"].astype(str).eq(anomaly_city_id)
        & metric_dates["target_date"].eq(anomaly_date)
    ]
    if len(metric) != 1:
        raise PosthocQAAuditError("The requested observed city-date metrics are unavailable")
    metric_row = metric.iloc[0]
    negative_count = int(anomaly["target_lst_below_0c_count"])
    available_count = int(anomaly["available_target_count"])
    return {
        **anomaly,
        "selection_was_preregistered": False,
        "selection_reason": (
            "Observed after the frozen evaluation: unusually dispersed and low "
            "Houston daytime LST labels with elevated Landsat ST_QA uncertainty."
        ),
        "target_lst_below_0c_fraction": negative_count / available_count,
        "m2_mae_c": float(metric_row["m2_mae_c"]),
        "b1_mae_c": float(metric_row["b1_mae_c"]),
        "m2_interval_coverage": float(metric_row["m2_interval_coverage"]),
        "m2_mean_interval_width_c": float(metric_row["m2_mean_interval_width_c"]),
        "comparison_with_other_usable_houston_dates": {
            "other_usable_date_count": len(peers),
            "median_target_lst_median_c": float(
                np.median([float(row["target_lst_median_c"]) for row in peers])
            ),
            "target_lst_median_difference_c": float(anomaly["target_lst_median_c"])
            - float(np.median([float(row["target_lst_median_c"]) for row in peers])),
            "median_target_lst_std_c": float(
                np.median([float(row["target_lst_std_c"]) for row in peers])
            ),
            "target_lst_std_difference_c": float(anomaly["target_lst_std_c"])
            - float(np.median([float(row["target_lst_std_c"]) for row in peers])),
            "median_st_uncertainty_k": float(
                np.median([float(row["median_st_uncertainty_k"]) for row in peers])
            ),
            "st_uncertainty_difference_k": float(anomaly["median_st_uncertainty_k"])
            - float(np.median([float(row["median_st_uncertainty_k"]) for row in peers])),
            "anomaly_has_largest_lst_std": float(anomaly["target_lst_std_c"])
            > max(float(row["target_lst_std_c"]) for row in peers),
            "anomaly_has_highest_median_st_uncertainty": float(
                anomaly["median_st_uncertainty_k"]
            )
            > max(float(row["median_st_uncertainty_k"]) for row in peers),
        },
    }


def _format_number(value: object, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}"


def _render_markdown(payload: Mapping[str, Any]) -> str:
    anomaly = payload["observed_anomaly"]
    peers = anomaly["comparison_with_other_usable_houston_dates"]
    sensitivity = payload["leave_one_date_out_sensitivity"]
    full = sensitivity["all_published_usable_dates"]
    excluded = sensitivity["excluding_observed_date"]
    formal_commit = payload["formal_result_reference"]["evaluation_commit_sha256"]
    lines = [
        "# External-result post-hoc QA audit",
        "",
        "> **NON-CONFIRMATORY / POST-HOC.** This audit was selected after the frozen ",
        "> external result was observed. It does not replace the formal result, change ",
        "> any gate, refit or recalibrate a model, or recompute a confidence interval.",
        "",
        f"Formal result state: `{payload['formal_result_reference']['state']}`. The formal ",
        "evaluation files remain unchanged and authoritative.",
        "",
        "## Houston 2025-07-25 observation",
        "",
        "| Quantity | Observed |",
        "|---|---:|",
        f"| Available tracts | {anomaly['available_target_count']} / "
        f"{anomaly['planned_tract_count']} "
        f"({100 * anomaly['available_target_fraction']:.1f}%) |",
        f"| Target LST median | {_format_number(anomaly['target_lst_median_c'])} °C |",
        f"| Target LST p05–p95 | {_format_number(anomaly['target_lst_p05_c'])} "
        f"to {_format_number(anomaly['target_lst_p95_c'])} °C |",
        f"| Target LST standard deviation | {_format_number(anomaly['target_lst_std_c'])} °C |",
        f"| Available tracts below 0 °C | {anomaly['target_lst_below_0c_count']} "
        f"({100 * anomaly['target_lst_below_0c_fraction']:.1f}%) |",
        "| Median tract ST_QA uncertainty | "
        f"{_format_number(anomaly['median_st_uncertainty_k'])} K |",
        "| Median tract p90 ST_QA uncertainty | "
        f"{_format_number(anomaly['median_p90_st_uncertainty_k'])} K |",
        f"| M2 MAE on this date | {_format_number(anomaly['m2_mae_c'])} °C |",
        "| M2 interval coverage / mean width | "
        f"{100 * anomaly['m2_interval_coverage']:.1f}% / "
        f"{_format_number(anomaly['m2_mean_interval_width_c'])} °C |",
        "",
        "Relative to the other usable Houston dates, the date-level target median was ",
        f"{abs(peers['target_lst_median_difference_c']):.3f} °C lower, target dispersion ",
        f"was {peers['target_lst_std_difference_c']:.3f} °C higher, and median Landsat ",
        f"ST_QA uncertainty was {peers['st_uncertainty_difference_k']:.3f} K higher. ",
        "`ST_QA` is observation-label uncertainty metadata; it is not the M2 prediction ",
        "interval. These diagnostics identify a data-quality concern but do not establish ",
        "its physical or processing cause.",
        "",
        "## Per-city date support",
        "",
        "| City | Planned dates | Usable dates | Available targets | Usable targets |",
        "|---|---:|---:|---:|---:|",
    ]
    for city in payload["city_support"]:
        lines.append(
            f"| {city['city_id']} | {city['planned_date_count']} | "
            f"{city['usable_date_count']} | {city['available_target_count']} / "
            f"{city['planned_tract_date_count']} | "
            f"{city['usable_available_target_count']} |"
        )
    lines.extend(
        [
            "",
            "The machine-readable JSON contains all 64 city-date support summaries. No ",
            "tract-level target or prediction values are included in either audit artifact.",
            "",
            "### Houston date-level support",
            "",
            "| Date | Available | Usable | Median LST | p05–p95 LST | Median ST_QA |",
            "|---|---:|:---:|---:|---:|---:|",
        ]
    )
    for row in payload["date_support"]:
        if row["city_id"] != ANOMALY_CITY_ID:
            continue
        lines.append(
            f"| {row['target_date']} | {row['available_target_count']} / "
            f"{row['planned_tract_count']} | {'yes' if row['date_usable'] else 'no'} | "
            f"{_format_number(row['target_lst_median_c'])} °C | "
            f"{_format_number(row['target_lst_p05_c'])}–"
            f"{_format_number(row['target_lst_p95_c'])} °C | "
            f"{_format_number(row['median_st_uncertainty_k'])} K |"
        )
    lines.extend(
        [
            "",
            "## Descriptive leave-one-date-out sensitivity",
            "",
            "| Dataset | Rows | City-dates | B1 equal-city/date MAE | "
            "M2 equal-city/date MAE | Relative improvement |",
            "|---|---:|---:|---:|---:|---:|",
            f"| Frozen formal usable dates | {full['usable_row_count']} | "
            f"{full['usable_city_date_count']} | "
            f"{full['b1_equal_city_equal_date_mae_c']:.3f} °C | "
            f"{full['m2_equal_city_equal_date_mae_c']:.3f} °C | "
            f"{100 * full['relative_mae_improvement_fraction']:.2f}% |",
            f"| Excluding Houston 2025-07-25 (post-hoc) | "
            f"{excluded['usable_row_count']} | {excluded['usable_city_date_count']} | "
            f"{excluded['b1_equal_city_equal_date_mae_c']:.3f} °C | "
            f"{excluded['m2_equal_city_equal_date_mae_c']:.3f} °C | "
            f"{100 * excluded['relative_mae_improvement_fraction']:.2f}% |",
            "",
            f"The descriptive relative-improvement point estimate changes by "
            f"{sensitivity['relative_improvement_change_percentage_points']:.2f} percentage ",
            "points. No bootstrap interval or formal gate was recalculated. The frozen ",
            "three-city result—including its sample-size and city-degradation failures—",
            "remains the only confirmatory result.",
            "",
            "## Reproducibility boundary",
            "",
            f"Audit algorithm: `{payload['algorithm_version']}`  ",
            f"Audit commit: `{payload['commit_sha256']}`  ",
            f"Formal evaluation commit: `{formal_commit}`",
            "",
        ]
    )
    return "\n".join(line.rstrip() for line in lines).rstrip("\n") + "\n"


def _compute_audit(
    project_root: str | Path,
    *,
    evaluation_directory: str | Path,
    external_completion_path: str | Path,
    anomaly_city_id: str,
    anomaly_date: str,
) -> tuple[dict[str, Any], str]:
    root = Path(project_root).resolve()
    evaluation = _inside(root, evaluation_directory, label="Evaluation directory")

    # This call must precede every metric or target read in this audit.
    evaluation_completion = authenticate_external_evaluation_completion(
        root, output_directory=evaluation
    )
    target_tables, date_summaries, target_completion_record, target_records = (
        _verified_target_tables(root, evaluation_completion, external_completion_path)
    )

    summary_path = evaluation / "summary.json"
    dates_path = evaluation / "date_metrics.parquet"
    output_files = evaluation_completion.get("output_files", {})
    try:
        formal_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        date_metrics = pd.read_parquet(dates_path)
    except Exception as error:  # noqa: BLE001 - normalize authenticated input failures
        raise PosthocQAAuditError("Authenticated evaluation QA inputs cannot be read") from error
    if (
        output_files.get("summary.json")
        != {"bytes": summary_path.stat().st_size, "sha256": sha256_file(summary_path)}
        or output_files.get("date_metrics.parquet")
        != parquet_file_record(dates_path, date_metrics)
    ):
        raise PosthocQAAuditError("Formal evaluation QA inputs failed authentication")

    date_support = _date_support_rows(target_tables, date_summaries)
    anomaly = _anomaly_summary(
        date_support,
        date_metrics,
        anomaly_city_id=anomaly_city_id,
        anomaly_date=anomaly_date,
    )
    sensitivity = _sensitivity(
        date_metrics,
        formal_summary,
        anomaly_city_id=anomaly_city_id,
        anomaly_date=anomaly_date,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "analysis_class": "non_confirmatory_posthoc_read_only_qa",
        "formal_result_unchanged": True,
        "model_refit_or_recalibrated": False,
        "formal_evaluation_outputs_modified": False,
        "tract_level_values_published": False,
        "formal_result_reference": {
            "state": formal_summary["state"],
            "evaluation_commit_sha256": evaluation_completion["commit_sha256"],
            "published_primary": formal_summary["primary"],
            "published_point_prediction_gates": formal_summary[
                "point_prediction_gates"
            ],
            "published_reliability": formal_summary["reliability"],
        },
        "observed_anomaly": anomaly,
        "city_support": _city_support(date_support),
        "date_support": date_support,
        "leave_one_date_out_sensitivity": sensitivity,
        "input_bindings": {
            "formal_evaluation_completion": {
                "path": (
                    evaluation
                    / "EXTERNAL_EVALUATION_COMPLETE.json"
                ).relative_to(root).as_posix(),
                "commit_sha256": evaluation_completion["commit_sha256"],
            },
            "formal_summary": {
                "path": summary_path.relative_to(root).as_posix(),
                **output_files["summary.json"],
            },
            "formal_date_metrics": {
                "path": dates_path.relative_to(root).as_posix(),
                **output_files["date_metrics.parquet"],
            },
            "external_target_completion": target_completion_record,
            "aggregate_target_inputs": target_records,
        },
        "interpretation_limits": [
            "The anomaly date was selected after outcomes were observed.",
            "The sensitivity is descriptive and has no confirmatory confidence interval.",
            "No success, reliability, or sample-size gate was re-evaluated.",
            "The audit does not identify the physical or processing cause of the anomaly.",
        ],
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload, _render_markdown(payload)


def build_posthoc_qa_audit(
    project_root: str | Path,
    *,
    evaluation_directory: str | Path = EVALUATION_DIRECTORY,
    external_completion_path: str | Path = EXTERNAL_COMPLETION,
    output_directory: str | Path = OUTPUT_DIRECTORY,
    anomaly_city_id: str = ANOMALY_CITY_ID,
    anomaly_date: str = ANOMALY_DATE,
) -> dict[str, Any]:
    """Build aggregate-only post-hoc QA artifacts after formal authentication."""

    root = Path(project_root).resolve()
    output = _inside(root, output_directory, label="Post-hoc QA output")
    evaluation = _inside(root, evaluation_directory, label="Evaluation directory")
    if output == evaluation:
        raise PosthocQAAuditError("Post-hoc QA output must not replace evaluation output")
    payload, markdown = _compute_audit(
        root,
        evaluation_directory=evaluation,
        external_completion_path=external_completion_path,
        anomaly_city_id=anomaly_city_id,
        anomaly_date=anomaly_date,
    )
    _atomic_lf_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        output / SUMMARY_FILENAME,
    )
    _atomic_lf_text(markdown, output / REPORT_FILENAME)
    return payload


def authenticate_posthoc_qa_audit(
    project_root: str | Path,
    *,
    evaluation_directory: str | Path = EVALUATION_DIRECTORY,
    external_completion_path: str | Path = EXTERNAL_COMPLETION,
    output_directory: str | Path = OUTPUT_DIRECTORY,
    anomaly_city_id: str = ANOMALY_CITY_ID,
    anomaly_date: str = ANOMALY_DATE,
) -> dict[str, Any]:
    """Recompute the aggregate audit in memory and verify both published artifacts."""

    root = Path(project_root).resolve()
    output = _inside(root, output_directory, label="Post-hoc QA output")
    expected, expected_markdown = _compute_audit(
        root,
        evaluation_directory=evaluation_directory,
        external_completion_path=external_completion_path,
        anomaly_city_id=anomaly_city_id,
        anomaly_date=anomaly_date,
    )
    try:
        summary_bytes = (output / SUMMARY_FILENAME).read_bytes()
        markdown_bytes = (output / REPORT_FILENAME).read_bytes()
        if b"\r" in summary_bytes or b"\r" in markdown_bytes:
            raise PosthocQAAuditError("Post-hoc QA artifacts must use LF endings")
        observed = json.loads(summary_bytes.decode("utf-8"))
        observed_markdown = markdown_bytes.decode("utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PosthocQAAuditError("Post-hoc QA artifacts are unavailable") from error
    if observed != expected or observed_markdown != expected_markdown:
        raise PosthocQAAuditError("Post-hoc QA artifacts do not reproduce")
    return observed
