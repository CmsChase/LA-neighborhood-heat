"""Append-only figures and evidence record for the three-city confirmation."""

from __future__ import annotations

import json
import math
import os
import platform
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from PIL import Image

from la_heat.multicity.external_evaluation import (
    COMPLETION_FILENAME as EVALUATION_COMPLETION_FILENAME,
)
from la_heat.multicity.external_evaluation import (
    OUTPUT_DIRECTORY as EVALUATION_DIRECTORY,
)
from la_heat.multicity.external_evaluation import (
    authenticate_external_evaluation_completion,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "multicity-external-evaluation-reporting-v1"
OUTPUT_DIRECTORY: Final = Path("data/processed/multicity/external_evaluation_report")
MANIFEST_FILENAME: Final = "EXTERNAL_EVALUATION_EVIDENCE.json"
RESULTS_FILENAME: Final = "RESULTS.md"
EVALUATION_YEAR: Final = 2025
FIGURE_FILES: Final = {
    "external_city_mae": "external_city_mae.png",
    "predicted_vs_observed": "predicted_vs_observed.png",
    "error_by_city_date": "error_by_city_date.png",
    "interval_calibration": "interval_calibration.png",
    "risk_coverage": "risk_coverage.png",
    "spatial_error_maps": "spatial_error_maps.png",
}
DISPLAY_NAMES: Final = {
    "phoenix_az": "Phoenix",
    "houston_tx": "Houston",
    "chicago_il": "Chicago",
}
COLORS: Final = {
    "phoenix_az": "#c34a36",
    "houston_tx": "#d88735",
    "chicago_il": "#356b8c",
    "B1": "#849097",
    "M2": "#c54d38",
}


class ExternalEvaluationReportingError(RuntimeError):
    """Raised when a report would not reproduce the authenticated evaluation."""


GeometryLoader = Callable[
    [Path], tuple[dict[str, gpd.GeoDataFrame], dict[str, dict[str, Any]]]
]


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ExternalEvaluationReportingError(f"{label} must stay inside the project")
    return path


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExternalEvaluationReportingError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise ExternalEvaluationReportingError(f"{label} is not a JSON object")
    unsigned = dict(payload)
    recorded = unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise ExternalEvaluationReportingError(f"{label} commit is invalid")
    return payload


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExternalEvaluationReportingError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise ExternalEvaluationReportingError(f"{label} is not a JSON object")
    return payload


def _record(path: Path, *, relative_to: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _verify_record(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _inside(root, record.get("path", ""), label=label)
    if (
        not path.is_file()
        or record.get("bytes") != path.stat().st_size
        or record.get("sha256") != sha256_file(path)
    ):
        raise ExternalEvaluationReportingError(f"{label} failed authentication")
    return path


def _load_locked_geometries(
    root: Path,
) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, dict[str, Any]]]:
    geometries: dict[str, gpd.GeoDataFrame] = {}
    records: dict[str, dict[str, Any]] = {}
    for city_id in EXTERNAL_CITY_IDS:
        manifest_path = root / (
            f"manifests/multicity/cities/{city_id}/geography/"
            "GEOGRAPHY_CONTRACT_V1.json"
        )
        manifest = _read_committed(manifest_path, label=f"{city_id} geography")
        if (
            manifest.get("access_contract", {}).get(
                "external_target_or_qa_values_read"
            )
            is not False
        ):
            raise ExternalEvaluationReportingError("Geometry access contract changed")
        primary = manifest.get("output_tables", {}).get("primary_tracts")
        if not isinstance(primary, dict):
            raise ExternalEvaluationReportingError("Geography lacks primary tracts")
        table_path = _verify_record(root, primary, label=f"{city_id} primary tracts")
        frame = gpd.read_parquet(table_path)
        if (
            frame.empty
            or frame.crs is None
            or "tract_geoid" not in frame
            or frame["tract_geoid"].astype(str).duplicated().any()
            or frame.geometry.isna().any()
        ):
            raise ExternalEvaluationReportingError("Canonical tract geometry changed")
        geometries[city_id] = frame.loc[:, ["tract_geoid", frame.geometry.name]].assign(
            tract_geoid=lambda value: value["tract_geoid"].astype("string")
        )
        records[city_id] = {
            "manifest": {
                **_record(manifest_path, relative_to=root),
                "commit_sha256": manifest["commit_sha256"],
            },
            "primary_tracts": dict(primary),
        }
    return geometries, records


def _load_evaluation_tables(
    evaluation: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], dict[str, Any]]:
    tables = {
        name: pd.read_parquet(evaluation / name)
        for name in (
            "scored_rows.parquet",
            "date_metrics.parquet",
            "city_metrics.parquet",
            "risk_coverage.parquet",
        )
    }
    summary = _read_json(evaluation / "summary.json", label="Evaluation summary")
    bootstrap = _read_json(evaluation / "bootstrap.json", label="Evaluation bootstrap")
    return tables, summary, bootstrap


def _counts(summary: Mapping[str, Any]) -> dict[str, int]:
    try:
        result = {
            "rows": int(summary["usable_row_count"]),
            "city_dates": int(summary["usable_city_date_count"]),
            "spatial_blocks": int(summary["spatial_block_count"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ExternalEvaluationReportingError("Evaluation summary lacks counts") from error
    if any(value <= 0 for value in result.values()):
        raise ExternalEvaluationReportingError("Evaluation counts must be positive")
    return result


def _software() -> dict[str, str]:
    return {
        "algorithm": ALGORITHM_VERSION,
        "python": platform.python_version(),
        "matplotlib": matplotlib.__version__,
        "pandas": pd.__version__,
        "geopandas": gpd.__version__,
        "numpy": np.__version__,
    }


def _footer(counts: Mapping[str, int]) -> str:
    return (
        f"Rows: {counts['rows']:,} · City-dates: {counts['city_dates']:,} · "
        f"5 km blocks: {counts['spatial_blocks']:,} · External year: {EVALUATION_YEAR} · "
        f"Software: {ALGORITHM_VERSION} / Matplotlib {matplotlib.__version__}"
    )


def _finish_figure(
    figure: plt.Figure,
    destination: Path,
    *,
    title: str,
    counts: Mapping[str, int],
) -> None:
    figure.text(0.5, 0.012, _footer(counts), ha="center", fontsize=8, color="#59636a")
    figure.savefig(
        destination,
        format="png",
        dpi=180,
        bbox_inches="tight",
        metadata={
            "Title": title,
            "Software": f"{ALGORITHM_VERSION}; Matplotlib {matplotlib.__version__}",
            "Description": _footer(counts),
        },
    )
    plt.close(figure)


def _figure_city_mae(
    city: pd.DataFrame, destination: Path, counts: Mapping[str, int]
) -> None:
    indexed = city.set_index("city_id").loc[list(EXTERNAL_CITY_IDS)]
    x = np.arange(len(indexed))
    width = 0.34
    figure, axis = plt.subplots(figsize=(8.5, 5.2))
    axis.bar(
        x - width / 2,
        indexed["b1_equal_date_mae_c"],
        width,
        color=COLORS["B1"],
        label="B1 transfer",
    )
    axis.bar(
        x + width / 2,
        indexed["m2_equal_date_mae_c"],
        width,
        color=COLORS["M2"],
        label="M2 transfer",
    )
    axis.set_xticks(x, [DISPLAY_NAMES[value] for value in indexed.index])
    axis.set_ylabel("Equal-date MAE (°C)")
    axis.set_title("External point accuracy by city")
    axis.grid(axis="y", alpha=0.22)
    axis.legend(frameon=False)
    figure.tight_layout(rect=(0, 0.07, 1, 1))
    _finish_figure(figure, destination, title="External city MAE", counts=counts)


def _figure_predicted_observed(
    rows: pd.DataFrame, destination: Path, counts: Mapping[str, int]
) -> None:
    values = rows[["target_lst_c", "b1_prediction_c", "m2_prediction_c"]].to_numpy(
        dtype=float
    )
    lower, upper = float(np.nanmin(values)), float(np.nanmax(values))
    figure, axes = plt.subplots(1, 2, figsize=(10.5, 5.0), sharex=True, sharey=True)
    for axis, column, label in zip(
        axes,
        ("b1_prediction_c", "m2_prediction_c"),
        ("B1 transfer", "M2 transfer"),
        strict=True,
    ):
        for city_id in EXTERNAL_CITY_IDS:
            selected = rows.loc[rows["city_id"].astype(str).eq(city_id)]
            axis.scatter(
                selected["target_lst_c"],
                selected[column],
                s=8,
                alpha=0.28,
                color=COLORS[city_id],
                label=DISPLAY_NAMES[city_id],
                rasterized=True,
            )
        axis.plot([lower, upper], [lower, upper], color="#263238", linewidth=1)
        axis.set_title(label)
        axis.set_xlabel("Observed daytime LST (°C)")
        axis.grid(alpha=0.15)
    axes[0].set_ylabel("Predicted daytime LST (°C)")
    axes[1].legend(frameon=False, fontsize=8)
    figure.suptitle("Predicted versus observed external LST")
    figure.tight_layout(rect=(0, 0.07, 1, 0.94))
    _finish_figure(
        figure, destination, title="Predicted versus observed", counts=counts
    )


def _figure_error_by_date(
    dates: pd.DataFrame, destination: Path, counts: Mapping[str, int]
) -> None:
    figure, axes = plt.subplots(3, 1, figsize=(10, 7.8), sharex=False, sharey=True)
    for axis, city_id in zip(axes, EXTERNAL_CITY_IDS, strict=True):
        selected = dates.loc[dates["city_id"].astype(str).eq(city_id)].sort_values(
            "target_date", kind="stable"
        )
        x = np.arange(len(selected))
        axis.plot(x, selected["b1_mae_c"], color=COLORS["B1"], label="B1", linewidth=1.5)
        axis.plot(x, selected["m2_mae_c"], color=COLORS["M2"], label="M2", linewidth=1.8)
        axis.fill_between(
            x,
            selected["b1_mae_c"].to_numpy(dtype=float),
            selected["m2_mae_c"].to_numpy(dtype=float),
            color=COLORS[city_id],
            alpha=0.12,
        )
        axis.set_title(DISPLAY_NAMES[city_id], loc="left", fontsize=10)
        axis.set_ylabel("MAE (°C)")
        axis.grid(alpha=0.16)
        if len(selected):
            step = max(1, len(selected) // 5)
            ticks = x[::step]
            axis.set_xticks(ticks, selected["target_date"].astype(str).iloc[::step], rotation=25)
    axes[0].legend(frameon=False, ncol=2)
    figure.suptitle("Error by external city and date")
    figure.tight_layout(rect=(0, 0.07, 1, 0.96))
    _finish_figure(figure, destination, title="Error by city and date", counts=counts)


def _figure_interval_calibration(
    rows: pd.DataFrame,
    city: pd.DataFrame,
    destination: Path,
    counts: Mapping[str, int],
) -> None:
    labels = [DISPLAY_NAMES[value] for value in EXTERNAL_CITY_IDS] + ["Overall"]
    values = [
        float(
            city.loc[city["city_id"].astype(str).eq(value), "m2_interval_coverage"].iloc[0]
        )
        for value in EXTERNAL_CITY_IDS
    ] + [float(rows["m2_interval_covered"].astype(bool).mean())]
    figure, axis = plt.subplots(figsize=(8.5, 5.2))
    bars = axis.bar(
        labels,
        values,
        color=[COLORS[value] for value in EXTERNAL_CITY_IDS] + ["#334e5c"],
    )
    axis.axhline(0.90, color="#20272c", linestyle="--", linewidth=1.2, label="Nominal 90%")
    axis.axhspan(0.85, 0.95, color="#6aa67a", alpha=0.13, label="Overall success band")
    axis.set_ylim(0, 1.04)
    axis.set_ylabel("Observed interval coverage")
    axis.set_title("Frozen 90% interval calibration")
    axis.bar_label(bars, labels=[f"{value:.1%}" for value in values], padding=3)
    axis.legend(frameon=False)
    axis.grid(axis="y", alpha=0.16)
    figure.tight_layout(rect=(0, 0.07, 1, 1))
    _finish_figure(figure, destination, title="Interval calibration", counts=counts)


def _figure_risk_coverage(
    risk: pd.DataFrame, destination: Path, counts: Mapping[str, int]
) -> None:
    figure, axis = plt.subplots(figsize=(8.5, 5.2))
    for cohort_id, selected in risk.groupby("cohort_id", observed=True, sort=False):
        selected = selected.sort_values("coverage_fraction", kind="stable")
        label = "All external" if cohort_id == "all_external" else DISPLAY_NAMES[str(cohort_id)]
        color = "#25323a" if cohort_id == "all_external" else COLORS[str(cohort_id)]
        axis.plot(
            selected["coverage_fraction"],
            selected["m2_mae_c"],
            marker="o",
            markersize=3.5,
            linewidth=1.7,
            color=color,
            label=label,
        )
    axis.set_xlabel("Retained prediction fraction")
    axis.set_ylabel("M2 MAE (°C)")
    axis.set_title("Risk–coverage curve from frozen interval width")
    axis.grid(alpha=0.18)
    axis.legend(frameon=False, ncol=2)
    figure.tight_layout(rect=(0, 0.07, 1, 1))
    _finish_figure(figure, destination, title="Risk coverage", counts=counts)


def _figure_spatial_errors(
    rows: pd.DataFrame,
    geometries: Mapping[str, gpd.GeoDataFrame],
    destination: Path,
    counts: Mapping[str, int],
) -> None:
    means = (
        rows.groupby(["city_id", "tract_geoid"], observed=True, sort=True)[
            "m2_absolute_error_c"
        ]
        .mean()
        .rename("mean_m2_absolute_error_c")
        .reset_index()
    )
    maximum = max(float(means["mean_m2_absolute_error_c"].quantile(0.98)), 1e-9)
    figure, axes = plt.subplots(1, 3, figsize=(13.2, 5.0))
    for axis, city_id in zip(axes, EXTERNAL_CITY_IDS, strict=True):
        geography = geometries.get(city_id)
        if geography is None:
            raise ExternalEvaluationReportingError(f"Missing geometry for {city_id}")
        selected = means.loc[means["city_id"].astype(str).eq(city_id)].copy()
        joined = geography.merge(selected, on="tract_geoid", how="left", validate="one_to_one")
        joined.plot(
            column="mean_m2_absolute_error_c",
            cmap="OrRd",
            vmin=0,
            vmax=maximum,
            missing_kwds={"color": "#e8e5df"},
            linewidth=0,
            ax=axis,
        )
        axis.set_title(DISPLAY_NAMES[city_id])
        axis.set_axis_off()
    scalar = ScalarMappable(norm=Normalize(vmin=0, vmax=maximum), cmap="OrRd")
    scalar.set_array([])
    figure.colorbar(scalar, ax=axes, fraction=0.025, pad=0.015, label="Mean M2 absolute error (°C)")
    figure.suptitle("Spatial distribution of external M2 error")
    figure.subplots_adjust(left=0.01, right=0.93, top=0.89, bottom=0.11, wspace=0.03)
    _finish_figure(figure, destination, title="Spatial error maps", counts=counts)


def _metric(value: object, *, percent: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(numeric):
        return "NA"
    return f"{numeric:.1%}" if percent else f"{numeric:.3f}"


def _markdown(
    summary: Mapping[str, Any],
    city: pd.DataFrame,
    counts: Mapping[str, int],
) -> str:
    primary = summary.get("primary", {})
    gates = summary.get("point_prediction_gates", {})
    reliability = summary.get("reliability", {})
    evidence_base = (
        f"**{counts['rows']:,} rows · {counts['city_dates']:,} city-dates · "
        f"{counts['spatial_blocks']:,} 5 km blocks**"
    )
    b1_mae = _metric(primary.get("b1_equal_city_equal_date_mae_c"))
    m2_mae = _metric(primary.get("m2_equal_city_equal_date_mae_c"))
    improvement = _metric(
        primary.get("relative_mae_improvement_fraction"), percent=True
    )
    ci_lower = _metric(primary.get("bootstrap_ci_lower"), percent=True)
    ci_upper = _metric(primary.get("bootstrap_ci_upper"), percent=True)
    point_gate = "PASS" if gates.get("success") is True else "NOT MET"
    reliability_gate = (
        "PASS" if reliability.get("success") is True else "NOT MET"
    )
    lines = [
        "# Three-City External Confirmation — Results",
        "",
        "> Generated only after authenticating the frozen external evaluation completion.",
        "",
        "## Frozen design",
        "",
        f"- External year: {EVALUATION_YEAR}",
        "- Cities: Phoenix, Houston, Chicago (one indivisible claim)",
        "- External refit or recalibration: **No**",
        f"- Evidence base: {evidence_base}",
        "",
        "## Primary result",
        "",
        "| Measure | Result |",
        "|---|---:|",
        f"| B1 equal-city/equal-date MAE (°C) | {b1_mae} |",
        f"| M2 equal-city/equal-date MAE (°C) | {m2_mae} |",
        f"| Relative MAE improvement | {improvement} |",
        f"| 95% bootstrap CI | [{ci_lower}, {ci_upper}] |",
        f"| Predeclared point gate | **{point_gate}** |",
        f"| Predeclared reliability gate | **{reliability_gate}** |",
        "",
        "## City results",
        "",
        "| City | Dates | Rows | Blocks | B1 MAE °C | M2 MAE °C |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    indexed = city.set_index("city_id")
    for city_id in EXTERNAL_CITY_IDS:
        row = indexed.loc[city_id]
        lines.append(
            f"| {DISPLAY_NAMES[city_id]} | {int(row['date_count'])} | {int(row['row_count'])} | "
            f"{int(row['spatial_block_count'])} | {_metric(row['b1_equal_date_mae_c'])} | "
            f"{_metric(row['m2_equal_date_mae_c'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation to complete after review",
            "",
            "- Main scientific interpretation: _[complete after reviewing all six figures]_",
            "- Largest limitation: _[complete without changing the frozen outcome]_",
            "- Appropriate next experiment: _[prospective forecast-time validation]_",
            "",
            "## Figure index",
            "",
        ]
    )
    for figure_id, filename in FIGURE_FILES.items():
        lines.append(f"- `{figure_id}` — `{filename}`")
    lines.extend(["", f"Software: `{ALGORITHM_VERSION}`.", ""])
    return "\n".join(lines)


def _render_all(
    tables: Mapping[str, pd.DataFrame],
    geometries: Mapping[str, gpd.GeoDataFrame],
    directory: Path,
    counts: Mapping[str, int],
) -> None:
    rows = tables["scored_rows.parquet"]
    dates = tables["date_metrics.parquet"]
    city = tables["city_metrics.parquet"]
    risk = tables["risk_coverage.parquet"]
    _figure_city_mae(city, directory / FIGURE_FILES["external_city_mae"], counts)
    _figure_predicted_observed(
        rows, directory / FIGURE_FILES["predicted_vs_observed"], counts
    )
    _figure_error_by_date(
        dates, directory / FIGURE_FILES["error_by_city_date"], counts
    )
    _figure_interval_calibration(
        rows, city, directory / FIGURE_FILES["interval_calibration"], counts
    )
    _figure_risk_coverage(risk, directory / FIGURE_FILES["risk_coverage"], counts)
    _figure_spatial_errors(
        rows, geometries, directory / FIGURE_FILES["spatial_error_maps"], counts
    )


def build_external_evaluation_report(
    project_root: str | Path,
    *,
    evaluation_directory: str | Path = EVALUATION_DIRECTORY,
    output_directory: str | Path = OUTPUT_DIRECTORY,
    geometry_loader: GeometryLoader | None = None,
) -> dict[str, Any]:
    """Authenticate first, then atomically publish all six locked figures."""

    root = Path(project_root).resolve()
    evaluation = _inside(root, evaluation_directory, label="Evaluation directory")
    output = _inside(root, output_directory, label="Reporting output")
    completion = authenticate_external_evaluation_completion(
        root, output_directory=evaluation
    )
    # No evaluation table or geometry is opened before the call above returns.
    tables, summary, bootstrap = _load_evaluation_tables(evaluation)
    del bootstrap  # The completion authenticator already verifies its full contract.
    loader = _load_locked_geometries if geometry_loader is None else geometry_loader
    geometries, geometry_records = loader(root)
    if set(geometries) != set(EXTERNAL_CITY_IDS) or set(geometry_records) != set(
        EXTERNAL_CITY_IDS
    ):
        raise ExternalEvaluationReportingError("Geometry cohort is incomplete")
    counts = _counts(summary)
    if output.exists():
        raise ExternalEvaluationReportingError("External report is append-only")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    ) as temporary_name:
        temporary = Path(temporary_name)
        _render_all(tables, geometries, temporary, counts)
        markdown = _markdown(summary, tables["city_metrics.parquet"], counts)
        (temporary / RESULTS_FILENAME).write_text(markdown, encoding="utf-8")
        figures = {
            figure_id: {
                **_record(temporary / filename, relative_to=temporary),
                "row_count": counts["rows"],
                "city_date_count": counts["city_dates"],
                "spatial_block_count": counts["spatial_blocks"],
                "external_year": EVALUATION_YEAR,
                "annotation": _footer(counts),
            }
            for figure_id, filename in FIGURE_FILES.items()
        }
        completion_path = evaluation / EVALUATION_COMPLETION_FILENAME
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "read_only_external_evaluation_evidence",
            "external_year": EVALUATION_YEAR,
            "city_ids": list(EXTERNAL_CITY_IDS),
            "evaluation_completion": {
                **_record(completion_path, relative_to=root),
                "commit_sha256": completion["commit_sha256"],
            },
            "global_counts": dict(counts),
            "software": _software(),
            "geometry_inputs": geometry_records,
            "figure_ids": list(FIGURE_FILES),
            "figures": figures,
            "results_markdown": _record(
                temporary / RESULTS_FILENAME, relative_to=temporary
            ),
            "permissions": {
                "evaluation_outputs_read_only": True,
                "external_targets_read_directly": False,
                "model_refit_or_recalibration_performed": False,
                "result_or_gate_changed": False,
            },
        }
        manifest["commit_sha256"] = canonical_sha256(manifest)
        (temporary / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.rename(temporary, output)
    return authenticate_external_evaluation_report(
        root,
        evaluation_directory=evaluation,
        output_directory=output,
        geometry_loader=loader,
    )


def authenticate_external_evaluation_report(
    project_root: str | Path,
    *,
    evaluation_directory: str | Path = EVALUATION_DIRECTORY,
    output_directory: str | Path = OUTPUT_DIRECTORY,
    geometry_loader: GeometryLoader | None = None,
) -> dict[str, Any]:
    """Check the append-only report without regenerating figures."""

    root = Path(project_root).resolve()
    evaluation = _inside(root, evaluation_directory, label="Evaluation directory")
    output = _inside(root, output_directory, label="Reporting output")
    completion = authenticate_external_evaluation_completion(
        root, output_directory=evaluation
    )
    # The completion boundary is authenticated before any metric/report input read.
    tables, summary, bootstrap = _load_evaluation_tables(evaluation)
    del bootstrap
    loader = _load_locked_geometries if geometry_loader is None else geometry_loader
    _geometries, geometry_records = loader(root)
    manifest = _read_committed(output / MANIFEST_FILENAME, label="Evidence manifest")
    counts = _counts(summary)
    completion_path = evaluation / EVALUATION_COMPLETION_FILENAME
    expected_completion = {
        **_record(completion_path, relative_to=root),
        "commit_sha256": completion["commit_sha256"],
    }
    if (
        manifest.get("algorithm_version") != ALGORITHM_VERSION
        or manifest.get("state") != "read_only_external_evaluation_evidence"
        or manifest.get("external_year") != EVALUATION_YEAR
        or manifest.get("city_ids") != list(EXTERNAL_CITY_IDS)
        or manifest.get("evaluation_completion") != expected_completion
        or manifest.get("global_counts") != counts
        or manifest.get("software") != _software()
        or manifest.get("geometry_inputs") != geometry_records
        or manifest.get("figure_ids") != list(FIGURE_FILES)
        or set(manifest.get("figures", {})) != set(FIGURE_FILES)
        or manifest.get("permissions")
        != {
            "evaluation_outputs_read_only": True,
            "external_targets_read_directly": False,
            "model_refit_or_recalibration_performed": False,
            "result_or_gate_changed": False,
        }
    ):
        raise ExternalEvaluationReportingError("Evidence manifest identity changed")
    for figure_id, filename in FIGURE_FILES.items():
        record = manifest["figures"][figure_id]
        path = output / filename
        expected = {
            **_record(path, relative_to=output),
            "row_count": counts["rows"],
            "city_date_count": counts["city_dates"],
            "spatial_block_count": counts["spatial_blocks"],
            "external_year": EVALUATION_YEAR,
            "annotation": _footer(counts),
        }
        if record != expected:
            raise ExternalEvaluationReportingError(f"Figure changed: {figure_id}")
        try:
            with Image.open(path) as image:
                if image.format != "PNG" or min(image.size) < 200:
                    raise ExternalEvaluationReportingError(
                        f"Figure is not a usable PNG: {figure_id}"
                    )
                image.verify()
        except (OSError, ValueError) as error:
            raise ExternalEvaluationReportingError(
                f"Figure cannot be decoded: {figure_id}"
            ) from error
    expected_markdown = _markdown(summary, tables["city_metrics.parquet"], counts)
    markdown_path = output / RESULTS_FILENAME
    try:
        observed_markdown = markdown_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ExternalEvaluationReportingError("Results Markdown is unavailable") from error
    if (
        observed_markdown != expected_markdown
        or manifest.get("results_markdown")
        != _record(markdown_path, relative_to=output)
    ):
        raise ExternalEvaluationReportingError("Results Markdown changed")
    return manifest
