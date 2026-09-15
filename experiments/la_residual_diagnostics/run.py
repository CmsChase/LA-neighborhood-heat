"""Read-only LA strict-forward residual diagnostics; no fitting or acquisition."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("experiment.toml")
KEYS = ["city_id", "tract_geoid", "target_date"]
SIGNED = "signed_relative_residual"
ABSOLUTE = "absolute_relative_residual"


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


def safe_spearman(left: pd.Series, right: pd.Series) -> float | None:
    if left.nunique(dropna=True) < 2 or right.nunique(dropna=True) < 2:
        return None
    value = left.corr(right, method="spearman")
    return None if not np.isfinite(value) else float(value)


def load_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    paths = {name: ROOT / value for name, value in config["inputs"].items()}
    oof = pd.read_parquet(paths["oof"])
    target = pd.read_parquet(paths["target_quality"])
    predictors = pd.read_parquet(paths["predictors"])
    expected_years = list(config["experiment"]["years"])
    oof["target_date"] = pd.to_datetime(oof.target_date).dt.strftime("%Y-%m-%d")
    target["target_date"] = pd.to_datetime(target.target_date).dt.strftime("%Y-%m-%d")
    predictors["target_date"] = pd.to_datetime(predictors.target_date).dt.strftime(
        "%Y-%m-%d"
    )
    observed_years = sorted(pd.to_datetime(oof.target_date).dt.year.unique())
    if oof.duplicated(KEYS).any() or observed_years != expected_years:
        raise ValueError("OOF key/year contract changed")
    if set(oof.city_id) != {config["experiment"]["city_id"]}:
        raise ValueError("Residual diagnostics must remain LA-only")
    if set(target.candidate_id) != {"4k"}:
        raise ValueError("QA4K target table changed")
    target_columns = [
        *KEYS,
        "eligible_pixel_count_static",
        "eligible_pixel_identity_sha256",
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
        "median_cloud_distance_km",
        "valid_fraction",
        "footprint_fraction",
        "valid_pixel_count",
        "source_scene_count",
        "platform",
        "overpass_id",
        "source_scene_ids",
    ]
    quality = target.loc[:, target_columns]
    if quality.duplicated(KEYS).any() or predictors.duplicated(KEYS).any():
        raise ValueError("Local diagnostic input contains duplicate keys")
    result = oof.merge(quality, on=KEYS, how="left", validate="one_to_one")
    weather_columns = [
        column
        for column in predictors
        if column.startswith(config["weather"]["prefix"])
    ]
    result = result.merge(
        predictors.loc[:, [*KEYS, *weather_columns]],
        on=KEYS,
        how="left",
        validate="one_to_one",
    )
    if len(result) != len(oof) or result[weather_columns].isna().any().any():
        raise ValueError("Diagnostic joins changed the fixed OOF scoring cohort")
    result[SIGNED] = (
        result.observed_relative - result.relative_current_23_relative_scored
    )
    result[ABSOLUTE] = result[SIGNED].abs()
    records = {
        name: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(path)}
        for name, path in paths.items()
    }
    return result, predictors, records


def load_neighbors(config: dict) -> tuple[list[str], np.ndarray, dict]:
    path = ROOT / config["inputs"]["tract_geometry"]
    geometry = gpd.read_parquet(path)
    geometry = geometry.loc[geometry.primary_included].copy()
    if geometry.crs is None or geometry.crs.to_epsg() != 3310:
        raise ValueError("Neighbor geometry must be EPSG:3310")
    geoids = geometry.GEOID.astype(str).tolist()
    if len(geoids) != len(set(geoids)):
        raise ValueError("Duplicate tract in neighbor geometry")
    centroids = geometry.geometry.centroid
    distance = cdist(
        np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()]),
        np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()]),
    )
    np.fill_diagonal(distance, np.inf)
    count = int(config["neighbor_check"]["neighbors"])
    neighbors = np.argpartition(distance, count, axis=1)[:, :count]
    self_memberships = int(
        sum(index in neighbor for index, neighbor in enumerate(neighbors))
    )
    duplicate_memberships = int(
        sum(len(set(row.tolist())) != count for row in neighbors)
    )
    inbound = np.bincount(neighbors.ravel(), minlength=len(geoids))
    undirected = {
        tuple(sorted((index, int(neighbor))))
        for index, row in enumerate(neighbors)
        for neighbor in row
    }
    metadata = {
        "tracts": len(geoids),
        "neighbors_per_tract": count,
        "directed_neighbor_memberships": int(neighbors.size),
        "unique_undirected_pairs": len(undirected),
        "self_memberships": self_memberships,
        "duplicate_memberships_within_focal_tract": duplicate_memberships,
        "inbound_memberships_mean": float(inbound.mean()),
        "inbound_memberships_max": int(inbound.max()),
        "neighbor_sets_overlap": True,
        "geometry_sha256": digest(path),
    }
    return geoids, neighbors, metadata


def date_neighbor_statistic(
    rows: pd.DataFrame, geoids: list[str], neighbors: np.ndarray
) -> tuple[float, list[dict], list[np.ndarray]]:
    positions = {geoid: index for index, geoid in enumerate(geoids)}
    detail = []
    arrays = []
    for target_date, date in rows.groupby("target_date", sort=True, observed=True):
        values = np.full(len(geoids), np.nan)
        indexes = np.asarray([positions[value] for value in date.tract_geoid], dtype=int)
        values[indexes] = date[SIGNED].to_numpy(dtype=float)
        neighbor_values = values[neighbors]
        represented = np.isfinite(neighbor_values).sum(axis=1)
        neighbor_mean = np.divide(
            np.nansum(neighbor_values, axis=1),
            represented,
            out=np.full(len(values), np.nan),
            where=represented > 0,
        )
        valid = np.isfinite(values) & np.isfinite(neighbor_mean)
        rho = safe_spearman(pd.Series(values[valid]), pd.Series(neighbor_mean[valid]))
        if rho is None:
            raise ValueError("Neighbor statistic unavailable on an OOF date")
        detail.append(
            {
                "target_date": target_date,
                "year": int(target_date[:4]),
                "tracts": int(valid.sum()),
                "spearman": rho,
                "sign_agreement": float(
                    np.mean(np.sign(values[valid]) == np.sign(neighbor_mean[valid]))
                ),
            }
        )
        arrays.append(values)
    return float(np.median([row["spearman"] for row in detail])), detail, arrays


def permutation_reference(
    arrays: list[np.ndarray], neighbors: np.ndarray, repetitions: int, seed: int
) -> dict:
    rng = np.random.default_rng(seed)
    replicates = []
    for _ in range(repetitions):
        per_date = []
        for original in arrays:
            values = original.copy()
            valid_positions = np.flatnonzero(np.isfinite(values))
            values[valid_positions] = rng.permutation(values[valid_positions])
            neighbor_values = values[neighbors]
            represented = np.isfinite(neighbor_values).sum(axis=1)
            neighbor_mean = np.divide(
                np.nansum(neighbor_values, axis=1),
                represented,
                out=np.full(len(values), np.nan),
                where=represented > 0,
            )
            valid = np.isfinite(values) & np.isfinite(neighbor_mean)
            rho = safe_spearman(
                pd.Series(values[valid]), pd.Series(neighbor_mean[valid])
            )
            if rho is not None:
                per_date.append(rho)
        replicates.append(float(np.median(per_date)))
    return {
        "repetitions": repetitions,
        "median": float(np.median(replicates)),
        "lower_2_5": float(np.quantile(replicates, 0.025)),
        "upper_97_5": float(np.quantile(replicates, 0.975)),
        "replicates": replicates,
    }


def cross_year_pattern(rows: pd.DataFrame, unit: str) -> list[dict]:
    annual = (
        rows.groupby(["held_year", unit], observed=True)[SIGNED].median().reset_index()
    )
    years = sorted(annual.held_year.unique())
    result = []
    for index, left_year in enumerate(years):
        left = annual.loc[annual.held_year.eq(left_year), [unit, SIGNED]]
        for right_year in years[index + 1 :]:
            right = annual.loc[annual.held_year.eq(right_year), [unit, SIGNED]]
            pair = left.merge(right, on=unit, suffixes=("_left", "_right"))
            result.append(
                {
                    "left_year": int(left_year),
                    "right_year": int(right_year),
                    "units": len(pair),
                    "spearman": safe_spearman(pair[f"{SIGNED}_left"], pair[f"{SIGNED}_right"]),
                    "sign_agreement": float(
                        np.mean(
                            np.sign(pair[f"{SIGNED}_left"])
                            == np.sign(pair[f"{SIGNED}_right"])
                        )
                    ),
                }
            )
    return result


def signed_residual_summary(rows: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    tract_median = rows.groupby("tract_geoid", observed=True)[SIGNED].median()
    mapped = rows.tract_geoid.map(tract_median).to_numpy(dtype=float)
    residual = rows[SIGNED].to_numpy(dtype=float)
    date_means = rows.groupby("target_date", observed=True)[SIGNED].mean()
    mapped_date_mean = rows.target_date.map(date_means).to_numpy(dtype=float)
    within_date = residual - mapped_date_mean
    tract_temporal_mad = rows.groupby("tract_geoid", observed=True)[SIGNED].agg(
        lambda values: float(np.median(np.abs(values - np.median(values))))
    )
    date_dispersion = rows.groupby("target_date", observed=True)[SIGNED].agg(
        lambda values: float(np.median(np.abs(values - np.median(values))))
    )
    date_block = (
        rows.groupby(["target_date", "held_year", "spatial_block"], observed=True)[SIGNED]
        .mean()
        .reset_index()
    )
    blocks = (
        date_block.groupby("spatial_block", observed=True)
        .agg(
            mean_signed_residual_c=(SIGNED, "mean"),
            median_signed_residual_c=(SIGNED, "median"),
            dates=("target_date", "nunique"),
            years=("held_year", "nunique"),
            positive_date_fraction=(SIGNED, lambda values: float(np.mean(values > 0))),
        )
        .reset_index()
    )
    blocks["absolute_mean_signed_residual_c"] = blocks.mean_signed_residual_c.abs()
    blocks = blocks.sort_values(
        "absolute_mean_signed_residual_c", ascending=False
    ).reset_index(drop=True)
    total_variance = float(np.var(residual))
    stable_variance = float(np.var(mapped))
    summary = {
        "rows": len(rows),
        "dates": int(rows.target_date.nunique()),
        "spatial_blocks": int(rows.spatial_block.nunique()),
        "tracts": int(rows.tract_geoid.nunique()),
        "total_residual_mae_c": float(np.mean(np.abs(residual))),
        "date_level_mean_signed_residual_mae_c": float(date_means.abs().mean()),
        "within_date_centered_residual_mae_c": float(np.mean(np.abs(within_date))),
        "median_date_spatial_mad_c": float(date_dispersion.median()),
        "median_tract_temporal_mad_c": float(tract_temporal_mad.median()),
        "within_date_centered_variance_c2": float(np.var(within_date)),
        "stable_tract_component_variance_c2": stable_variance,
        "total_residual_variance_c2": total_variance,
        "stable_variance_fraction": stable_variance / total_variance,
        "after_tract_median_absolute_deviation_c": float(
            np.mean(np.abs(residual - mapped))
        ),
        "tract_cross_year": cross_year_pattern(rows, "tract_geoid"),
        "block_cross_year": cross_year_pattern(rows, "spatial_block"),
    }
    return summary, blocks


def date_associations(rows: pd.DataFrame, response: str, field: str) -> pd.DataFrame:
    records = []
    for target_date, date in rows.groupby("target_date", sort=True, observed=True):
        pair = date[[response, field]].dropna()
        rho = safe_spearman(pair[response], pair[field])
        if rho is None:
            continue
        low = pair.loc[pair[field].le(pair[field].quantile(0.25)), response]
        high = pair.loc[pair[field].ge(pair[field].quantile(0.75)), response]
        records.append(
            {
                "target_date": target_date,
                "year": int(target_date[:4]),
                "rows": len(pair),
                "spearman": rho,
                "top_minus_bottom_c": float(high.mean() - low.mean()),
                "within_date_sd": float(pair[field].std()),
                "within_date_range": float(pair[field].max() - pair[field].min()),
            }
        )
    return pd.DataFrame(records)


def summarize_association(
    detail: pd.DataFrame, field: str, rows: pd.DataFrame, config: dict
) -> dict:
    years = list(config["experiment"]["years"])
    minimum_dates = int(config["signal_rule"]["minimum_variable_dates_per_year"])
    year_rows = []
    for year in years:
        subset = detail.loc[detail.year.eq(year)] if not detail.empty else detail
        year_rows.append(
            {
                "year": year,
                "dates": len(subset),
                "median_spearman": (
                    None if subset.empty else float(subset.spearman.median())
                ),
                "median_top_minus_bottom_c": (
                    None if subset.empty else float(subset.top_minus_bottom_c.median())
                ),
            }
        )
    valid = all(
        row["dates"] >= minimum_dates and row["median_spearman"] is not None
        for row in year_rows
    )
    medians = [
        row["median_spearman"]
        for row in year_rows
        if row["median_spearman"] is not None
    ]
    same_direction = valid and (
        all(value > 0 for value in medians) or all(value < 0 for value in medians)
    )
    overall = None if detail.empty else float(detail.spearman.median())
    passes = bool(
        same_direction
        and overall is not None
        and abs(overall)
        >= float(config["signal_rule"]["minimum_absolute_overall_median_spearman"])
        and min(abs(value) for value in medians)
        >= float(config["signal_rule"]["minimum_absolute_year_median_spearman"])
    )
    return {
        "field": field,
        "available_rows": int(rows[field].notna().sum()),
        "missing_rows": int(rows[field].isna().sum()),
        "variable_dates": len(detail),
        "overall_median_date_spearman": overall,
        "overall_median_top_minus_bottom_c": (
            None if detail.empty else float(detail.top_minus_bottom_c.median())
        ),
        "median_within_date_sd": (
            None if detail.empty else float(detail.within_date_sd.median())
        ),
        "median_within_date_range": (
            None if detail.empty else float(detail.within_date_range.median())
        ),
        "year_summaries": year_rows,
        "same_direction_signal": passes,
    }


def quality_diagnostics(rows: pd.DataFrame, config: dict) -> tuple[list[dict], dict]:
    summaries = []
    for field in config["quality"]["numeric_fields"]:
        detail = date_associations(rows, ABSOLUTE, field)
        summaries.append(summarize_association(detail, field, rows, config))
    eligible = rows.groupby("tract_geoid", observed=True).agg(
        eligible_counts=("eligible_pixel_count_static", "nunique"),
        eligible_hashes=("eligible_pixel_identity_sha256", "nunique"),
    )
    scene_signatures = rows.source_scene_ids.map(scene_signature)
    platform_dates = rows[["target_date", "platform"]].drop_duplicates()
    date_quality = (
        rows.groupby(["target_date", "platform"], observed=True)[ABSOLUTE]
        .mean()
        .reset_index()
    )
    source = {
        "platform_date_counts": platform_dates.platform.value_counts().sort_index().to_dict(),
        "platform_date_mean_absolute_residual_c": (
            date_quality.groupby("platform", observed=True)[ABSOLUTE]
            .mean()
            .sort_index()
            .to_dict()
        ),
        "overpass_ids": int(rows.overpass_id.nunique()),
        "dates": int(rows.target_date.nunique()),
        "source_scene_sets": int(rows.source_scene_ids.nunique()),
        "scene_signatures": scene_signatures.value_counts().sort_index().to_dict(),
        "source_scene_count_values": sorted(rows.source_scene_count.unique().tolist()),
        "footprint_fraction_values": sorted(rows.footprint_fraction.unique().tolist()),
        "view_or_sun_angle_fields_available": False,
        "eligible_land_denominator_invariant": bool(
            eligible.eligible_counts.eq(1).all() and eligible.eligible_hashes.eq(1).all()
        ),
    }
    return summaries, source


def scene_signature(value: str) -> str:
    units = sorted(set(re.findall(r"L[A-Z0-9]{3}_L2S[A-Z]_([0-9]{6})_", str(value))))
    return "+".join(units) if units else "unparsed"


def weather_diagnostics(rows: pd.DataFrame, config: dict) -> list[dict]:
    fields = [column for column in rows if column.startswith(config["weather"]["prefix"])]
    summaries = []
    for field in fields:
        detail = date_associations(rows, SIGNED, field)
        summary = summarize_association(detail, field, rows, config)
        summary["prediction_time_available"] = True
        summary["source_window_ends"] = "target day minus 1"
        summaries.append(summary)
    return summaries


def build_evidence(
    neighbor: dict,
    signed: dict,
    quality: list[dict],
    weather: list[dict],
    rows: pd.DataFrame,
) -> tuple[list[dict], str, str]:
    quality_signals = [row for row in quality if row["same_direction_signal"]]
    weather_signals = [row for row in weather if row["same_direction_signal"]]
    cross = signed["tract_cross_year"]
    median_cross = float(np.median([row["spearman"] for row in cross]))
    common = {
        "independent_dates": int(rows.target_date.nunique()),
        "spatial_blocks": int(rows.spatial_block.nunique()),
    }
    evidence = [
        {
            "hypothesis": "The six-neighbor statistic is a self/duplicate/overlap artifact",
            "support": "Neighbor sets overlap, so focal statistics are not independent.",
            "counterexample": (
                f"Self memberships={neighbor['topology']['self_memberships']}; within-focal "
                f"duplicates={neighbor['topology']['duplicate_memberships_within_focal_tract']}; "
                f"observed median rho={neighbor['observed_median_spearman']:.3f} versus "
                f"position-permutation 97.5%={neighbor['permutation']['upper_97_5']:.3f}."
            ),
            "prediction_time_available": "public geometry is available, but diagnostic-only",
            "minimum_next_validation": "none; computation artifact explanation is not supported",
            **common,
        },
        {
            "hypothesis": "A fixed long-term neighborhood bias is missing",
            "support": (
                "Mapped tract-median residual variance fraction="
                f"{signed['stable_variance_fraction']:.3f}."
            ),
            "counterexample": (
                f"Median tract-pattern cross-year rho={median_cross:.3f}; the fixed coarse "
                "spatial correction already failed its stopping rule."
            ),
            "prediction_time_available": (
                "geometry available; target history unavailable for prediction"
            ),
            "minimum_next_validation": "stop fixed spatial correction; do not tune another scale",
            **common,
        },
        {
            "hypothesis": "Existing observation-quality variation explains large errors",
            "support": (
                "Consistent fields: "
                + (", ".join(row["field"] for row in quality_signals) or "none")
            ),
            "counterexample": (
                "Scene/overpass identity is date-confounded; view and sun angles are "
                "absent."
            ),
            "prediction_time_available": "target-scene QA is post-observation and diagnostic-only",
            "minimum_next_validation": "measurement/support audit on repeated observations",
            **common,
        },
        {
            "hypothesis": (
                "Existing target-before Daymet spatial gradients contain a missing "
                "dynamic signal"
            ),
            "support": (
                "Consistent fields: "
                + (", ".join(row["field"] for row in weather_signals) or "none")
            ),
            "counterexample": (
                "Associations reuse three development years and do not prove predictive "
                "gain or causality."
            ),
            "prediction_time_available": "yes; all audited windows end at d-1",
            "minimum_next_validation": (
                "one fixed weather-gradient experiment"
                if weather_signals
                else "additional dynamic observations"
            ),
            **common,
        },
    ]
    if weather_signals:
        decision = "A"
        recommendation = (
            "Freeze one small experiment using only the cross-year-consistent d-1 Daymet "
            "spatial-gradient fields listed in the evidence; keep the existing model and "
            "absolute level fixed and do not search windows or models."
        )
    elif quality_signals:
        decision = "B"
        recommendation = (
            "Perform a measurement audit using repeated observations and existing QA/support "
            "fields; do not claim an accuracy ceiling or filter difficult rows."
        )
    else:
        decision = "C"
        recommendation = (
            "Stop searching the existing years. Add repeated observations with acquisition "
            "geometry and target-before within-city atmospheric/weather gradients before "
            "another model experiment."
        )
    return evidence, decision, recommendation


def main() -> None:
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)
    rows, predictors, records = load_inputs(config)
    geoids, neighbors, topology = load_neighbors(config)
    observed, per_date, arrays = date_neighbor_statistic(rows, geoids, neighbors)
    permutation = permutation_reference(
        arrays,
        neighbors,
        int(config["neighbor_check"]["permutation_repetitions"]),
        int(config["experiment"]["seed"]),
    )
    permutation["one_sided_fraction_at_least_observed"] = float(
        (1 + sum(value >= observed for value in permutation["replicates"]))
        / (1 + len(permutation["replicates"]))
    )
    del permutation["replicates"]
    neighbor = {
        "definition": config["neighbor_check"]["statistic"],
        "own_residual_vector": (
            "one signed strict-forward OOF residual per observed tract on a date"
        ),
        "neighbor_vector": (
            "mean signed residual of six nearest other tracts with OOF observations on "
            "that date"
        ),
        "observed_median_spearman": observed,
        "per_date": per_date,
        "topology": topology,
        "permutation": permutation,
    }
    signed, blocks = signed_residual_summary(rows)
    quality, scene = quality_diagnostics(rows, config)
    weather = weather_diagnostics(rows, config)
    evidence, decision, recommendation = build_evidence(
        neighbor, signed, quality, weather, rows
    )
    blocks.to_csv(output / "signed_residual_blocks.csv", index=False)
    pd.DataFrame(quality).to_json(
        output / "quality_associations.json", orient="records", indent=2
    )
    pd.DataFrame(weather).to_json(
        output / "weather_associations.json", orient="records", indent=2
    )
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment"]["id"],
        "role": config["experiment"]["role"],
        "completed_at": datetime.now(UTC).isoformat(),
        "no_model_fit_or_selection": True,
        "no_data_acquisition": True,
        "qa_threshold_and_scoring_cohort_unchanged": True,
        "no_la_2025_or_external_city_targets_used": True,
        "input_records": records,
        "rows": len(rows),
        "dates": int(rows.target_date.nunique()),
        "blocks": int(rows.spatial_block.nunique()),
        "neighbor_check": neighbor,
        "signed_residual": signed,
        "quality": quality,
        "scene_and_geometry_availability": scene,
        "weather": weather,
        "weather_source_rows": len(predictors),
        "evidence_table": evidence,
        "decision": decision,
        "recommendation": recommendation,
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "status.json",
        {
            "state": "complete",
            "updated_utc": datetime.now(UTC).isoformat(),
            "decision": decision,
            "rows": len(rows),
            "dates": int(rows.target_date.nunique()),
            "blocks": int(rows.spatial_block.nunique()),
        },
    )
    print(json.dumps({"decision": decision, "recommendation": recommendation}))


if __name__ == "__main__":
    main()
