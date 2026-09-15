"""One-shot LA target measurement-support sensitivity diagnostic.

The TOML beside this file is the pre-result contract. This script reads only
authenticated local 2020--2024 LA source assets and existing OOF predictions;
it never fits a model, resolves an href, or accesses final/external targets.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

from la_heat.aligned_landsat import COVERAGE_KEY, decode_aligned_scene_arrays
from la_heat.config import load_config
from la_heat.mosaic import MosaicResult, mosaic_aligned_scenes
from la_heat.multicity.m3_source_asset_cache import authenticate_plan
from la_heat.multicity.m3_source_integrity_v2 import (
    authenticate_logical_global_cache,
    load_retained_scene_arrays,
)
from la_heat.multicity.m3_source_offline_qa import candidate_config
from la_heat.multicity.target_context import load_target_city_context
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = Path(__file__).with_name("experiment.toml")
KEYS = ["city_id", "tract_geoid", "target_date"]
WRS_RE = re.compile(r"L[A-Z0-9]{3}_L2S[A-Z]_([0-9]{6})_")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verify_inputs(config: dict) -> dict[str, dict[str, object]]:
    inputs = config["inputs"]
    records: dict[str, dict[str, object]] = {}
    for name, relative in inputs.items():
        if name.endswith("_sha256"):
            continue
        path = ROOT / relative
        observed = digest(path)
        expected = inputs[f"{name}_sha256"]
        if observed != expected:
            raise ValueError(f"Locked input changed: {relative}")
        records[name] = {
            "path": str(relative),
            "bytes": path.stat().st_size,
            "sha256": observed,
        }
    return records


def build_pairs(dates: list[pd.Timestamp], maximum_gap_days: int) -> list[tuple[str, str]]:
    by_year: dict[int, list[pd.Timestamp]] = defaultdict(list)
    for value in sorted(dates):
        by_year[value.year].append(value)
    pairs: list[tuple[str, str]] = []
    for year in sorted(by_year):
        values = by_year[year]
        index = 0
        while index + 1 < len(values):
            first, second = values[index], values[index + 1]
            if (second - first).days <= maximum_gap_days:
                pairs.append((first.strftime("%Y-%m-%d"), second.strftime("%Y-%m-%d")))
                index += 2
            else:
                index += 1
    return pairs


def wrs_signature(scene_ids: list[str]) -> tuple[str, ...]:
    signatures = []
    for scene_id in scene_ids:
        match = WRS_RE.search(scene_id)
        if match is None:
            raise ValueError(f"Cannot extract WRS path/row from {scene_id}")
        signatures.append(match.group(1))
    return tuple(sorted(signatures))


def load_locked_state(config: dict):
    authorization_path = ROOT / config["inputs"]["integrity_authorization"]
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    unsigned_authorization = dict(authorization)
    recorded_authorization_commit = unsigned_authorization.pop("commit_sha256", None)
    if (
        authorization.get("state") != "source_integrity_overlay_v2_authorized"
        or recorded_authorization_commit != canonical_sha256(unsigned_authorization)
    ):
        raise ValueError("Locked source-cache authorization commit is invalid")
    qa_completion_path = ROOT / config["inputs"]["qa_completion"]
    qa_completion = json.loads(qa_completion_path.read_text(encoding="utf-8"))
    unsigned_qa = dict(qa_completion)
    recorded_qa_commit = unsigned_qa.pop("commit_sha256", None)
    if (
        qa_completion.get("state") != "source_qa_candidates_complete"
        or recorded_qa_commit != canonical_sha256(unsigned_qa)
    ):
        raise ValueError("Locked QA completion commit is invalid")
    authenticate_logical_global_cache(ROOT, authorization)
    raw_plan = json.loads(
        (ROOT / config["inputs"]["physical_cache_plan"]).read_text(encoding="utf-8")
    )
    plan = authenticate_plan(raw_plan)
    context = load_target_city_context(ROOT, config["experiment"]["city_id"])
    base = load_config(ROOT / config["inputs"]["research_config"])
    qa_config = candidate_config(base, config["experiment"]["qa_candidate"])
    return authorization, plan, context, qa_config


def verify_authorization_unchanged(config: dict, authorization: dict) -> None:
    path = ROOT / config["inputs"]["integrity_authorization"]
    if (
        digest(path) != config["inputs"]["integrity_authorization_sha256"]
        or json.loads(path.read_text(encoding="utf-8")) != authorization
    ):
        raise ValueError("Source-cache authorization changed before pixel access")


def build_mosaic(
    *,
    city_id: str,
    scene_ids: list[str],
    authorization: dict,
    plan: dict,
    qa_config,
) -> MosaicResult:
    scenes = []
    for scene_id in scene_ids:
        arrays = load_retained_scene_arrays(
            ROOT,
            authorization,
            plan,
            city_id,
            scene_id,
            before_value_access=lambda: None,
        )
        if COVERAGE_KEY not in arrays and "source_coverage" in arrays:
            arrays[COVERAGE_KEY] = arrays.pop("source_coverage")
        scenes.append(
            decode_aligned_scene_arrays(
                scene_id=scene_id,
                arrays=arrays,
                config=qa_config,
            )
        )
    return mosaic_aligned_scenes(
        scene_ids=[scene.scene_id for scene in scenes],
        st_values=np.stack([scene.lst_c for scene in scenes]),
        qa_valid=np.stack([scene.valid for scene in scenes]),
        st_qa=np.stack([scene.st_uncertainty_k for scene in scenes]),
        cdist=np.stack([scene.cloud_distance_km for scene in scenes]),
        footprint=np.stack([scene.footprint for scene in scenes]),
    )


def zonal_median(values: np.ndarray, zones: np.ndarray, mask: np.ndarray, count: int) -> np.ndarray:
    labels = zones[mask]
    observed = values[mask]
    if not np.isfinite(observed).all():
        raise ValueError("Common support contains a nonfinite target value")
    return np.asarray(
        ndimage.median(observed, labels=labels, index=np.arange(1, count + 1)),
        dtype=float,
    )


def centroid_by_zone(mask: np.ndarray, zones: np.ndarray, transform, count: int):
    flat = np.flatnonzero(mask)
    labels = zones.ravel()[flat]
    rows, columns = np.divmod(flat, zones.shape[1])
    counts = np.bincount(labels, minlength=count + 1)[1:].astype(np.int64)
    x = transform.c + (columns + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e
    sum_x = np.bincount(labels, weights=x, minlength=count + 1)[1:]
    sum_y = np.bincount(labels, weights=y, minlength=count + 1)[1:]
    mean_x = np.divide(sum_x, counts, out=np.full(count, np.nan), where=counts > 0)
    mean_y = np.divide(sum_y, counts, out=np.full(count, np.nan), where=counts > 0)
    return counts, mean_x, mean_y


def centered(values: pd.Series) -> pd.Series:
    return values - values.median()


def equal_date_equal_block(rows: pd.DataFrame, column: str) -> float:
    blocks = rows.groupby(["target_date", "spatial_block"], observed=True)[column].mean()
    dates = blocks.groupby("target_date", observed=True).mean()
    return float(dates.mean())


def bootstrap_dates(date_values: pd.Series, replicates: int, seed: int) -> dict[str, float]:
    values = date_values.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(replicates, len(values)), replace=True).mean(axis=1)
    return {
        "lower_95": float(np.quantile(samples, 0.025)),
        "upper_95": float(np.quantile(samples, 0.975)),
    }


def main() -> None:
    with CONTRACT.open("rb") as handle:
        config = tomllib.load(handle)
    records = verify_inputs(config)
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)

    oof = pd.read_parquet(ROOT / config["inputs"]["forward_oof"])
    target = pd.read_parquet(ROOT / config["inputs"]["target_summary"])
    oof["target_date"] = pd.to_datetime(oof.target_date).dt.strftime("%Y-%m-%d")
    target["target_date"] = pd.to_datetime(target.target_date).dt.strftime("%Y-%m-%d")
    years = sorted(pd.to_datetime(oof.target_date).dt.year.unique().tolist())
    if years != config["experiment"]["development_years"] or set(oof.city_id) != {
        config["experiment"]["city_id"]
    }:
        raise ValueError("OOF cohort left the locked LA 2022--2024 development set")
    if oof.duplicated(KEYS).any() or target.duplicated(KEYS).any():
        raise ValueError("Locked inputs contain duplicated tract/date keys")
    if set(target.candidate_id) != {config["experiment"]["qa_candidate"]}:
        raise ValueError("Target QA candidate changed")

    pairs = build_pairs(
        list(pd.to_datetime(oof.target_date.unique())),
        int(config["date_pairing"]["maximum_gap_days"]),
    )
    if len(pairs) != config["date_pairing"]["expected_pairs"]:
        raise ValueError("Locked date pairing changed")

    authorization, plan, context, qa_config = load_locked_state(config)
    city_id = config["experiment"]["city_id"]
    plan_rows = [row for row in plan["scenes"] if row["city_id"] == city_id]
    scenes_by_date: dict[str, list[str]] = defaultdict(list)
    for row in plan_rows:
        scenes_by_date[str(row["target_date"])].append(str(row["scene_id"]))
    for date in scenes_by_date:
        scenes_by_date[date].sort()

    zone_count = len(context.tracts)
    eligible_counts, eligible_x, eligible_y = centroid_by_zone(
        context.eligible_land,
        context.zones,
        context.grid.transform,
        zone_count,
    )
    geoids = context.tracts.GEOID.astype(str).to_numpy()
    zone_index_by_geoid = pd.Series(
        np.arange(zone_count, dtype=np.int64), index=geoids
    )
    if not np.array_equal(
        eligible_counts,
        target.groupby("tract_geoid", sort=False).eligible_pixel_count_static.first()
        .reindex(geoids)
        .to_numpy(dtype=np.int64),
    ):
        raise ValueError("Fixed eligible-land denominator differs from target table")

    target_columns = [
        "tract_geoid",
        "target_date",
        "target_lst_c",
        "target_available",
        "valid_fraction",
        "valid_pixel_count",
        "latitude_quartile",
        "longitude_quartile",
        "spatial_block",
    ]
    target_lookup = target.loc[:, target_columns]
    prediction_column = config["analysis"]["prediction_column"]
    oof_lookup = oof.loc[:, [*KEYS, "observed", "spatial_block", prediction_column]]
    pair_frames: list[pd.DataFrame] = []
    pair_records: list[dict[str, object]] = []
    reconstruction_maximum_differences: list[float] = []

    for ordinal, (first, second) in enumerate(pairs, start=1):
        first_scenes = scenes_by_date[first]
        second_scenes = scenes_by_date[second]
        if wrs_signature(first_scenes) != wrs_signature(second_scenes):
            raise ValueError(f"WRS contributors changed inside pair {first}/{second}")
        verify_authorization_unchanged(config, authorization)
        first_mosaic = build_mosaic(
            city_id=city_id,
            scene_ids=first_scenes,
            authorization=authorization,
            plan=plan,
            qa_config=qa_config,
        )
        second_mosaic = build_mosaic(
            city_id=city_id,
            scene_ids=second_scenes,
            authorization=authorization,
            plan=plan,
            qa_config=qa_config,
        )
        first_valid = context.eligible_land & first_mosaic.selected_valid
        second_valid = context.eligible_land & second_mosaic.selected_valid
        original_rebuilt = {
            first: zonal_median(
                first_mosaic.selected_st_value,
                context.zones,
                first_valid,
                zone_count,
            ),
            second: zonal_median(
                second_mosaic.selected_st_value,
                context.zones,
                second_valid,
                zone_count,
            ),
        }
        common = first_valid & second_valid
        common_counts, common_x, common_y = centroid_by_zone(
            common, context.zones, context.grid.transform, zone_count
        )
        common_fraction = np.divide(
            common_counts,
            eligible_counts,
            out=np.zeros(zone_count, dtype=float),
            where=eligible_counts > 0,
        )
        support_pass = (
            (common_counts >= int(config["common_support"]["minimum_pixels_per_tract"]))
            & (
                common_fraction
                >= float(
                    config["common_support"]["minimum_fraction_of_fixed_eligible_land"]
                )
            )
        )
        first_median = zonal_median(
            first_mosaic.selected_st_value, context.zones, common, zone_count
        )
        second_median = zonal_median(
            second_mosaic.selected_st_value, context.zones, common, zone_count
        )
        centroid_shift = np.hypot(common_x - eligible_x, common_y - eligible_y)
        support = pd.DataFrame(
            {
                "tract_geoid": geoids,
                "common_valid_pixel_count": common_counts,
                "common_valid_fraction": common_fraction,
                "common_support_pass": support_pass,
                "common_centroid_shift_m": centroid_shift,
                f"common_target_{first}": first_median,
                f"common_target_{second}": second_median,
            }
        )
        target_pair = {
            date: target_lookup.loc[target_lookup.target_date.eq(date)].set_index(
                "tract_geoid"
            )
            for date in (first, second)
        }
        for date in (first, second):
            available = target_pair[date].loc[
                target_pair[date].target_available, "target_lst_c"
            ]
            zone_indices = zone_index_by_geoid.reindex(available.index)
            if zone_indices.isna().any():
                raise ValueError("Target summary contains a tract outside the fixed grid")
            rebuilt = original_rebuilt[date][zone_indices.to_numpy(dtype=np.int64)]
            maximum_difference = float(
                np.max(np.abs(rebuilt - available.to_numpy(dtype=float)))
            )
            if maximum_difference > 1e-9:
                raise ValueError(
                    "Pixel reconstruction differs from the authenticated original target"
                )
            reconstruction_maximum_differences.append(maximum_difference)
        oof_pair = {
            date: oof_lookup.loc[oof_lookup.target_date.eq(date)].set_index("tract_geoid")
            for date in (first, second)
        }
        paired_geoids = (
            set(oof_pair[first].index)
            & set(oof_pair[second].index)
            & set(target_pair[first].index[target_pair[first].target_available])
            & set(target_pair[second].index[target_pair[second].target_available])
        )
        support = support.loc[
            support.common_support_pass & support.tract_geoid.isin(paired_geoids)
        ].copy()
        retained_geoids = set(support.tract_geoid)
        pair_id = f"pair_{ordinal:02d}_{first}_{second}"
        support_hash = hashlib.sha256(np.packbits(common.ravel()).tobytes()).hexdigest()
        for date, other in ((first, second), (second, first)):
            frame = support.loc[
                :,
                [
                    "tract_geoid",
                    "common_valid_pixel_count",
                    "common_valid_fraction",
                    "common_centroid_shift_m",
                    f"common_target_{date}",
                ],
            ].rename(columns={f"common_target_{date}": "common_target_lst_c"})
            frame = frame.merge(
                target_pair[date].reset_index(),
                on="tract_geoid",
                how="left",
                validate="one_to_one",
            ).merge(
                oof_pair[date].reset_index().drop(columns=["city_id", "spatial_block"]),
                on=["tract_geoid", "target_date"],
                how="left",
                validate="one_to_one",
            )
            if set(frame.tract_geoid) != retained_geoids or frame.isna().any().any():
                raise ValueError("Matched pair join changed the retained support")
            if not np.allclose(frame.target_lst_c, frame.observed, rtol=0, atol=1e-9):
                raise ValueError("OOF observed target differs from authenticated QA4K target")
            frame.insert(0, "pair_id", pair_id)
            frame.insert(1, "paired_date", other)
            frame["original_relative_matched"] = centered(frame.target_lst_c)
            frame["common_relative"] = centered(frame.common_target_lst_c)
            frame["prediction_relative_matched"] = centered(frame[prediction_column])
            frame["relative_target_shift_c"] = (
                frame.common_relative - frame.original_relative_matched
            )
            frame["absolute_target_shift_c"] = (
                frame.common_target_lst_c - frame.target_lst_c
            )
            frame["original_relative_error_matched_c"] = np.abs(
                frame.prediction_relative_matched - frame.original_relative_matched
            )
            frame["common_relative_error_c"] = np.abs(
                frame.prediction_relative_matched - frame.common_relative
            )
            frame["relative_error_change_c"] = (
                frame.common_relative_error_c - frame.original_relative_error_matched_c
            )
            pair_frames.append(frame)
        pair_records.append(
            {
                "pair_id": pair_id,
                "first_date": first,
                "second_date": second,
                "gap_days": int((pd.Timestamp(second) - pd.Timestamp(first)).days),
                "wrs_path_rows": list(wrs_signature(first_scenes)),
                "common_city_grid_pixel_count": int(common.sum()),
                "common_city_grid_mask_sha256": support_hash,
                "paired_scored_tracts_before_common_gate": len(paired_geoids),
                "retained_tracts": len(retained_geoids),
            }
        )
        print(f"[support] {ordinal}/{len(pairs)} {first} + {second}", flush=True)

    rows = pd.concat(pair_frames, ignore_index=True)
    paired_dates = {date for pair in pairs for date in pair}
    paired_oof = oof.loc[oof.target_date.isin(paired_dates)]
    overall_retention = len(rows) / len(paired_oof)
    yearly_retention = {}
    for year in config["experiment"]["development_years"]:
        numerator = int(pd.to_datetime(rows.target_date).dt.year.eq(year).sum())
        denominator = int(pd.to_datetime(paired_oof.target_date).dt.year.eq(year).sum())
        yearly_retention[str(year)] = numerator / denominator
    original_blocks = int(oof.spatial_block.nunique())
    retained_blocks = int(rows.spatial_block.nunique())
    gate = config["feasibility_stop"]
    feasibility_checks = {
        "overall_retention": overall_retention
        >= float(gate["minimum_overall_retained_original_scored_fraction"]),
        "each_year_retention": min(yearly_retention.values())
        >= float(gate["minimum_each_year_retained_original_scored_fraction"]),
        "retained_dates": int(rows.target_date.nunique())
        >= int(gate["minimum_retained_dates"]),
        "spatial_blocks": retained_blocks / original_blocks
        >= float(gate["minimum_spatial_block_fraction"]),
    }
    feasible = all(feasibility_checks.values())
    base_summary = {
        "schema_version": 1,
        "experiment": config["experiment"]["name"],
        "completed_at": datetime.now(UTC).isoformat(),
        "contract": {
            "path": str(CONTRACT.relative_to(ROOT)).replace("\\", "/"),
            "sha256": digest(CONTRACT),
        },
        "input_records": records,
        "inventory": {
            "pixel_cache_sufficient": True,
            "cached_la_scenes": len(plan_rows),
            "cached_la_dates": len(scenes_by_date),
            "pixel_fields": [
                "surface-temperature DN",
                "QA_PIXEL",
                "ST_QA",
                "cloud distance",
                "QA_RADSAT",
                "georeferenced source coverage",
            ],
            "fixed_eligible_land_mask_available": True,
            "fixed_zone_raster_available": True,
            "valid_pixel_locations_reconstructed_not_in_summary_table": True,
            "maximum_original_target_reconstruction_abs_difference_c": max(
                reconstruction_maximum_differences
            ),
        },
        "pairing": {
            "original_oof_dates": int(oof.target_date.nunique()),
            "pairs": len(pairs),
            "paired_dates": len(paired_dates),
            "unpaired_dates": sorted(set(oof.target_date) - paired_dates),
            "pair_records": pair_records,
        },
        "feasibility": {
            "passed": feasible,
            "checks": feasibility_checks,
            "paired_oof_rows": len(paired_oof),
            "retained_rows": len(rows),
            "overall_retained_fraction": overall_retention,
            "year_retained_fraction": yearly_retention,
            "retained_dates": int(rows.target_date.nunique()),
            "retained_spatial_blocks": retained_blocks,
            "original_spatial_blocks": original_blocks,
        },
        "audit": {
            "new_data_acquired": False,
            "model_fit_or_selection_performed": False,
            "qa_or_eligible_denominator_changed": False,
            "la_2025_read": False,
            "external_city_target_read": False,
            "common_support_claimed_as_better_truth": False,
        },
    }
    if not feasible:
        summary = {
            **base_summary,
            "state": "stopped_common_support_not_identifiable",
            "decision": "unidentifiable_due_to_common_support_selection",
        }
        write_json(output / "summary.json", summary)
        write_json(output / "status.json", {"state": summary["state"]})
        print(json.dumps({"state": summary["state"], "feasibility": feasibility_checks}))
        return

    rows["absolute_relative_target_shift_c"] = rows.relative_target_shift_c.abs()
    rows["absolute_absolute_target_shift_c"] = rows.absolute_target_shift_c.abs()
    rows["year"] = pd.to_datetime(rows.target_date).dt.year
    date_block = (
        rows.groupby(["target_date", "spatial_block"], observed=True)
        .agg(
            absolute_relative_target_shift_c=(
                "absolute_relative_target_shift_c",
                "mean",
            ),
            absolute_absolute_target_shift_c=(
                "absolute_absolute_target_shift_c",
                "mean",
            ),
            original_relative_error_matched_c=(
                "original_relative_error_matched_c",
                "mean",
            ),
            common_relative_error_c=("common_relative_error_c", "mean"),
            relative_error_change_c=("relative_error_change_c", "mean"),
            rows=("tract_geoid", "size"),
        )
        .reset_index()
    )
    date_metrics = (
        date_block.groupby("target_date", observed=True)
        .agg(
            absolute_relative_target_shift_c=(
                "absolute_relative_target_shift_c",
                "mean",
            ),
            absolute_absolute_target_shift_c=(
                "absolute_absolute_target_shift_c",
                "mean",
            ),
            original_relative_error_matched_c=(
                "original_relative_error_matched_c",
                "mean",
            ),
            common_relative_error_c=("common_relative_error_c", "mean"),
            relative_error_change_c=("relative_error_change_c", "mean"),
            spatial_blocks=("spatial_block", "nunique"),
            rows=("rows", "sum"),
        )
        .reset_index()
    )
    date_metrics["year"] = pd.to_datetime(date_metrics.target_date).dt.year
    year_metrics = (
        date_metrics.groupby("year", observed=True)
        .agg(
            absolute_relative_target_shift_c=(
                "absolute_relative_target_shift_c",
                "mean",
            ),
            absolute_absolute_target_shift_c=(
                "absolute_absolute_target_shift_c",
                "mean",
            ),
            original_relative_error_matched_c=(
                "original_relative_error_matched_c",
                "mean",
            ),
            common_relative_error_c=("common_relative_error_c", "mean"),
            relative_error_change_c=("relative_error_change_c", "mean"),
            dates=("target_date", "nunique"),
            rows=("rows", "sum"),
        )
        .reset_index()
    )
    primary = float(date_metrics.absolute_relative_target_shift_c.mean())
    uncertainty = bootstrap_dates(
        date_metrics.set_index("target_date").absolute_relative_target_shift_c,
        int(config["analysis"]["bootstrap_replicates"]),
        int(config["analysis"]["bootstrap_seed"]),
    )
    year_threshold = float(
        config["decision"]["and_at_least_two_years_at_least_celsius"]
    )
    qualifying_years = int(
        (year_metrics.absolute_relative_target_shift_c >= year_threshold).sum()
    )
    worth_prioritizing = (
        primary >= float(config["decision"]["worth_prioritizing_if_primary_at_least_celsius"])
        and qualifying_years >= 2
    )
    quartile = (
        rows.groupby(["year", "latitude_quartile", "longitude_quartile"], observed=True)
        .agg(
            rows=("tract_geoid", "size"),
            median_common_fraction=("common_valid_fraction", "median"),
        )
        .reset_index()
    )
    rows.to_parquet(output / "matched_tract_dates.parquet", index=False)
    date_block.to_csv(output / "date_block_metrics.csv", index=False)
    date_metrics.to_csv(output / "date_metrics.csv", index=False)
    year_metrics.to_csv(output / "year_metrics.csv", index=False)
    quartile.to_csv(output / "retained_support_quartiles.csv", index=False)
    summary = {
        **base_summary,
        "state": "complete",
        "sensitivity": {
            "primary_absolute_relative_target_shift_c": primary,
            "date_bootstrap_95": uncertainty,
            "year_absolute_relative_target_shift_c": {
                str(int(row.year)): float(row.absolute_relative_target_shift_c)
                for row in year_metrics.itertuples(index=False)
            },
            "absolute_target_shift_c": equal_date_equal_block(
                rows, "absolute_absolute_target_shift_c"
            ),
            "original_model_relative_mae_on_matched_rows_c": float(
                date_metrics.original_relative_error_matched_c.mean()
            ),
            "common_support_relative_mae_on_matched_rows_c": float(
                date_metrics.common_relative_error_c.mean()
            ),
            "relative_mae_change_c": float(date_metrics.relative_error_change_c.mean()),
            "median_common_fraction": float(rows.common_valid_fraction.median()),
            "p10_common_fraction": float(rows.common_valid_fraction.quantile(0.10)),
            "median_common_centroid_shift_m": float(
                rows.common_centroid_shift_m.median()
            ),
            "p90_common_centroid_shift_m": float(
                rows.common_centroid_shift_m.quantile(0.90)
            ),
        },
        "decision": {
            "measurement_support_worth_prioritizing": worth_prioritizing,
            "primary_threshold_c": float(
                config["decision"]["worth_prioritizing_if_primary_at_least_celsius"]
            ),
            "year_threshold_c": year_threshold,
            "qualifying_years": qualifying_years,
        },
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "status.json",
        {
            "state": "complete",
            "measurement_support_worth_prioritizing": worth_prioritizing,
            "primary_absolute_relative_target_shift_c": primary,
        },
    )
    print(
        json.dumps(
            {
                "state": "complete",
                "worth_prioritizing": worth_prioritizing,
                "primary_shift_c": primary,
            }
        )
    )


if __name__ == "__main__":
    main()
