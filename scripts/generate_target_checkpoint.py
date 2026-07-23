"""Generate an auditable checkpoint table and figure from a committed target build.

This script reads only aggregate products whose ``build_progress.json`` commit
marker is in a readable state.  It refuses to report while the target builder
is running or when the recorded file hashes do not match the on-disk snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

READABLE_STATES = {"partial_ready", "model_ready"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    _atomic_bytes(path, frame.to_csv(index=False).encode("utf-8"))


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _input_paths(
    target_dir: Path, progress: dict[str, object]
) -> tuple[Path, Path, Path]:
    if progress["state"] == "model_ready":
        return (
            target_dir / "development_target_qa.parquet",
            target_dir / "date_summary.parquet",
            target_dir / "scene_contributions.parquet",
        )
    return (
        target_dir / "development_target_qa_partial.parquet",
        target_dir / "date_summary_partial.parquet",
        target_dir / "scene_contributions_partial.parquet",
    )


def _verify_record(path: Path, record: dict[str, object]) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Committed target product is missing: {path}")
    actual_sha = _sha256(path)
    if actual_sha != record["sha256"]:
        raise ValueError(
            f"Target product hash mismatch for {path}: {actual_sha} != {record['sha256']}"
        )


def _audit(
    date_summary: pd.DataFrame,
    rows: pd.DataFrame,
    contributions: pd.DataFrame,
    progress: dict[str, object],
    model_rows: pd.DataFrame | None = None,
) -> dict[str, object]:
    completed = int(progress["completed_overpass_count"])
    if len(date_summary) != completed or rows["target_date"].nunique() != completed:
        raise ValueError("Aggregate date counts disagree with the build commit marker.")
    if rows.duplicated(["tract_geoid", "target_date"]).any():
        raise ValueError("Duplicate tract-date primary keys found.")
    final_test_year = int(progress.get("final_test_year", 2025))
    if (pd.to_datetime(rows["target_date"]).dt.year >= final_test_year).any():
        raise ValueError(
            f"Locked {final_test_year}+ rows found in a development checkpoint."
        )
    static_count_variants = rows.groupby("tract_geoid")["eligible_pixel_count_static"].nunique(
        dropna=False
    )
    static_identity_variants = rows.groupby("tract_geoid")[
        "eligible_pixel_identity_sha256"
    ].nunique(dropna=False)
    if (static_count_variants != 1).any() or (static_identity_variants != 1).any():
        raise ValueError("Static eligible-land denominator changed across dates.")

    grouped = rows.groupby("target_date", sort=False).agg(
        row_count=("tract_geoid", "size"),
        retained_count=("target_available", "sum"),
    )
    check = date_summary.set_index("target_date").join(grouped)
    if not (check["tract_count"] == check["row_count"]).all():
        raise ValueError("Date-summary tract counts disagree with tract rows.")
    if not (check["retained_tract_count"] == check["retained_count"]).all():
        raise ValueError("Date-summary retained counts disagree with tract rows.")

    contribution_key = ["target_date", "overpass_id", "scene_id", "tract_geoid"]
    if contributions.duplicated(contribution_key).any():
        raise ValueError("Duplicate scene-contribution keys found.")
    contribution_counts = pd.to_numeric(
        contributions["selected_valid_pixel_count"], errors="raise"
    )
    if (contribution_counts < 0).any() or not np.equal(
        contribution_counts, np.floor(contribution_counts)
    ).all():
        raise ValueError("Scene-contribution pixel counts must be nonnegative integers.")
    contribution_sum = contributions.groupby(
        ["target_date", "tract_geoid"]
    )["selected_valid_pixel_count"].sum()
    row_counts = rows.set_index(["target_date", "tract_geoid"])["valid_pixel_count"]
    aligned_contributions = contribution_sum.reindex(row_counts.index, fill_value=0)
    if not np.array_equal(
        aligned_contributions.to_numpy(dtype=np.int64),
        row_counts.to_numpy(dtype=np.int64),
    ):
        raise ValueError("Scene contributions disagree with tract valid-pixel counts.")

    hotspot_errors: list[str] = []
    cutoff_repeat_dates: list[str] = []
    boundary_tie_dates: list[str] = []
    for record in date_summary.itertuples(index=False):
        date_rows = rows.loc[rows["target_date"] == record.target_date]
        expected = (
            math.ceil(0.2 * int(record.retained_tract_count))
            if bool(record.relative_endpoint_coverage_pass)
            else 0
        )
        observed = int(date_rows["relative_hotspot_top20"].fillna(False).sum())
        if expected != observed or expected != int(record.relative_hotspot_count):
            hotspot_errors.append(str(record.target_date))
            continue
        if not bool(record.relative_endpoint_coverage_pass):
            continue
        available = date_rows.loc[date_rows["target_available"]].copy()
        ranked = available.sort_values(
            ["target_lst_c", "tract_geoid"],
            ascending=[False, True],
            kind="stable",
        )
        expected_geoids = set(ranked.head(expected)["tract_geoid"].astype(str))
        observed_geoids = set(
            date_rows.loc[
                date_rows["relative_hotspot_top20"].fillna(False), "tract_geoid"
            ].astype(str)
        )
        if expected_geoids != observed_geoids:
            hotspot_errors.append(str(record.target_date))
        ranked_values = ranked["target_lst_c"].to_numpy(dtype=float)
        cutoff_value = ranked_values[expected - 1]
        if int(np.count_nonzero(ranked_values == cutoff_value)) > 1:
            cutoff_repeat_dates.append(str(record.target_date))
        if expected < len(ranked_values) and ranked_values[expected] == cutoff_value:
            boundary_tie_dates.append(str(record.target_date))
    if hotspot_errors:
        raise ValueError(f"Exact hotspot counts failed on: {hotspot_errors}")

    legal_mask = rows["target_available"].astype(bool) & rows["date_usable"].astype(bool)
    legal_rows = rows.loc[legal_mask]
    if model_rows is not None:
        if model_rows.duplicated(["tract_geoid", "target_date"]).any():
            raise ValueError("Duplicate keys found in the promoted model-ready table.")
        legal_keys = pd.MultiIndex.from_frame(legal_rows[["tract_geoid", "target_date"]])
        model_keys = pd.MultiIndex.from_frame(model_rows[["tract_geoid", "target_date"]])
        if len(model_rows) != len(legal_rows) or not legal_keys.equals(model_keys):
            if set(legal_keys) != set(model_keys):
                raise ValueError(
                    "Promoted model-ready keys do not equal target_available & date_usable."
                )
        if (
            pd.to_datetime(model_rows["target_date"]).dt.year >= final_test_year
        ).any():
            raise ValueError(f"Locked {final_test_year}+ rows found in model-ready data.")

    usable = int(date_summary["date_usable"].sum())
    relative = int(date_summary["relative_endpoint_coverage_pass"].sum())
    minimum_required = int(progress["minimum_required_usable_overpasses"])
    return {
        "state": progress["state"],
        "expected_overpass_count": int(progress["expected_overpass_count"]),
        "completed_overpass_count": completed,
        "usable_overpass_count": usable,
        "relative_endpoint_overpass_count": relative,
        "minimum_required_usable_overpasses": minimum_required,
        "usable_date_gate_pass": usable >= minimum_required,
        "tract_date_rows": int(len(rows)),
        "retained_absolute_labels": int(rows["target_available"].sum()),
        "target_available_on_unusable_dates": int(
            (rows["target_available"].astype(bool) & ~rows["date_usable"].astype(bool)).sum()
        ),
        "model_ready_rows": int(len(legal_rows)),
        "unique_tracts": int(rows["tract_geoid"].nunique()),
        "independent_spatial_blocks": int(legal_rows["spatial_block"].nunique()),
        "independent_date_block_cells": int(
            legal_rows[["target_date", "spatial_block"]].drop_duplicates().shape[0]
        ),
        "duplicate_primary_keys": 0,
        f"locked_{final_test_year}_or_later_rows": 0,
        "static_eligible_count_variants_max": int(static_count_variants.max()),
        "static_eligible_identity_variants_max": int(static_identity_variants.max()),
        "scene_contribution_rows": int(len(contributions)),
        "selected_valid_pixels": int(contribution_counts.sum()),
        "relative_label_rows": int(rows["lst_anomaly_c"].notna().sum()),
        "exact_hotspot_positives": int(
            rows["relative_hotspot_top20"].fillna(False).sum()
        ),
        "hotspot_cutoff_exact_repeat_date_count": len(cutoff_repeat_dates),
        "hotspot_cutoff_exact_repeat_dates": cutoff_repeat_dates,
        "hotspot_exact_boundary_tie_date_count": len(boundary_tie_dates),
        "hotspot_exact_boundary_tie_dates": boundary_tie_dates,
        "target_table_sha256": _sha256(Path(rows.attrs["source_path"])),
        "date_summary_sha256": _sha256(Path(date_summary.attrs["source_path"])),
        "scene_contributions_sha256": _sha256(
            Path(contributions.attrs["source_path"])
        ),
        "model_ready_table_sha256": (
            _sha256(Path(model_rows.attrs["source_path"]))
            if model_rows is not None
            else None
        ),
    }


def _make_figure(date_summary: pd.DataFrame, audit: dict[str, object], path: Path) -> None:
    dates = pd.to_datetime(date_summary["target_date"])
    usable = date_summary["date_usable"].astype(bool).to_numpy()
    relative = date_summary["relative_endpoint_coverage_pass"].astype(bool).to_numpy()
    colors = np.where(usable, "#2878B5", "#C44E52")

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    axes[0].bar(dates, date_summary["retained_tract_fraction"], width=5, color=colors)
    axes[0].axhline(
        0.50, color="#333333", linestyle="--", linewidth=1.2, label="absolute-date gate (50%)"
    )
    axes[0].axhline(
        0.80, color="#7A5195", linestyle=":", linewidth=1.2, label="relative overall gate (80%)"
    )
    axes[0].set_ylim(0, 1.03)
    axes[0].set_ylabel("Retained tract fraction")
    axes[0].set_title("QA-filtered tract retention by physical Landsat overpass")
    line_handles, line_labels = axes[0].get_legend_handles_labels()
    axes[0].legend(
        [
            Patch(facecolor="#2878B5", label="absolute usable date"),
            Patch(facecolor="#C44E52", label="date excluded by QA gate"),
            *line_handles,
        ],
        [
            "absolute usable date",
            "date excluded by QA gate",
            *line_labels,
        ],
        loc="lower right",
        frameon=True,
        facecolor="white",
        framealpha=0.92,
        edgecolor="none",
    )
    axes[0].grid(axis="y", alpha=0.2)

    x = np.arange(1, len(date_summary) + 1)
    axes[1].plot(
        x, np.cumsum(usable), color="#2878B5", linewidth=2.2, label="absolute usable dates"
    )
    axes[1].plot(
        x, np.cumsum(relative), color="#7A5195", linewidth=2.2, label="relative-endpoint dates"
    )
    axes[1].axhline(
        int(audit["minimum_required_usable_overpasses"]),
        color="#333333",
        linestyle="--",
        linewidth=1.2,
        label="minimum usable-date gate",
    )
    axes[1].set_xlim(1, len(date_summary))
    axes[1].set_ylim(0, max(32, int(np.cumsum(usable)[-1]) + 2))
    axes[1].set_xlabel("Frozen overpass sequence (chronological)")
    axes[1].set_ylabel("Cumulative independent dates")
    completed = audit["completed_overpass_count"]
    expected = audit["expected_overpass_count"]
    usable_count = audit["usable_overpass_count"]
    relative_count = audit["relative_endpoint_overpass_count"]
    axes[1].set_title(
        f"Checkpoint: {completed}/{expected} complete; "
        f"{usable_count} usable; {relative_count} relative"
    )
    axes[1].legend(
        loc="upper left",
        frameon=True,
        facecolor="white",
        framealpha=0.92,
        edgecolor="none",
    )
    axes[1].grid(alpha=0.2)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial.png")
    fig.savefig(temporary, dpi=180, bbox_inches="tight")
    plt.close(fig)
    os.replace(temporary, path)


def _year_summary(date_summary: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    dates = date_summary.assign(
        year=pd.to_datetime(date_summary["target_date"]).dt.year
    )
    summary = dates.groupby("year", as_index=False).agg(
        overpass_dates=("target_date", "size"),
        usable_dates=("date_usable", "sum"),
        relative_endpoint_dates=("relative_endpoint_coverage_pass", "sum"),
        target_available_rows=("retained_tract_count", "sum"),
    )
    model_rows = rows.loc[rows["target_available"] & rows["date_usable"]].assign(
        year=lambda frame: pd.to_datetime(frame["target_date"]).dt.year
    )
    model_counts = model_rows.groupby("year").size().rename("model_ready_rows")
    summary = summary.merge(model_counts, how="left", on="year", validate="one_to_one")
    summary["model_ready_rows"] = summary["model_ready_rows"].fillna(0).astype(int)
    return summary


def generate_checkpoint(target_dir: Path, reports_dir: Path) -> dict[str, Path]:
    progress_path = target_dir / "build_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("state") not in READABLE_STATES:
        raise RuntimeError(
            f"Target aggregate is not committed for reading; state={progress.get('state')!r}."
        )

    row_path, date_path, contribution_path = _input_paths(target_dir, progress)
    record_group = (
        "aggregate_outputs"
        if progress["state"] == "model_ready"
        else "partial_outputs"
    )
    records = progress[record_group]
    _verify_record(row_path, records[row_path.name])
    _verify_record(date_path, records[date_path.name])
    _verify_record(contribution_path, records[contribution_path.name])

    rows = pd.read_parquet(row_path)
    rows.attrs["source_path"] = str(row_path)
    date_summary = pd.read_parquet(date_path).sort_values("target_date").reset_index(drop=True)
    date_summary.attrs["source_path"] = str(date_path)
    contributions = pd.read_parquet(contribution_path)
    contributions.attrs["source_path"] = str(contribution_path)
    model_rows = None
    if progress["state"] == "model_ready":
        model_path = target_dir / "development_targets_model_ready.parquet"
        _verify_record(model_path, records[model_path.name])
        model_rows = pd.read_parquet(model_path)
        model_rows.attrs["source_path"] = str(model_path)
    audit = _audit(date_summary, rows, contributions, progress, model_rows)

    completed = int(audit["completed_overpass_count"])
    if (
        progress["state"] == "model_ready"
        and completed == int(audit["expected_overpass_count"])
    ):
        stem = f"target_build_full{completed}_checkpoint"
    else:
        stem = f"target_build_first{completed}_checkpoint"
    table_path = reports_dir / "tables" / "generated" / f"{stem}_date_summary.csv"
    year_table_path = (
        reports_dir / "tables" / "generated" / f"{stem}_year_summary.csv"
    )
    audit_path = reports_dir / "tables" / "generated" / f"{stem}_audit.json"
    figure_path = reports_dir / "figures" / "generated" / f"{stem}.png"
    _atomic_csv(date_summary, table_path)
    _atomic_csv(_year_summary(date_summary, rows), year_table_path)
    _atomic_json(audit_path, audit)
    _make_figure(date_summary, audit, figure_path)
    return {
        "date_table": table_path,
        "year_table": year_table_path,
        "audit": audit_path,
        "figure": figure_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-dir", type=Path, default=Path("data/interim/targets"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    outputs = generate_checkpoint(args.target_dir, args.reports_dir)
    print(json.dumps({key: str(value) for key, value in outputs.items()}, indent=2))


if __name__ == "__main__":
    main()
