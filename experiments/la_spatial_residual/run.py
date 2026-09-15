"""Bounded LA coarse spatial-residual experiment with strict-forward evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.spatial.distance import cdist
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CONFIG_PATH = Path(__file__).with_name("experiment.toml")
PRIOR_RUN_PATH = ROOT / "experiments" / "la_local_accuracy" / "run.py"
PRIOR_CONFIG_PATH = PRIOR_RUN_PATH.with_name("experiment.toml")
GEOMETRY_PATH = ROOT / "data" / "interim" / "targets" / "primary_tract_manifest.parquet"
BASIS_COLUMNS = ["space_x", "space_y", "space_x2", "space_xy", "space_y2"]
COMPARATOR = "relative_current_23"
CANDIDATE = "current_plus_coarse_spatial_residual"


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


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_spatial_basis() -> tuple[pd.DataFrame, dict]:
    geometry = gpd.read_parquet(GEOMETRY_PATH)
    if geometry.crs is None or geometry.crs.to_epsg() != 3310:
        raise ValueError("LA tract geometry must remain in EPSG:3310")
    geometry = geometry.loc[geometry.primary_included].copy()
    if geometry.GEOID.astype(str).duplicated().any() or len(geometry) != 1096:
        raise ValueError("Fixed LA tract geography changed")
    centroids = geometry.geometry.centroid
    raw = np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()])
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    if not np.isfinite(raw).all() or np.any(scale <= 0):
        raise ValueError("Invalid tract centroids")
    xy = (raw - mean) / scale
    basis = np.column_stack(
        [xy[:, 0], xy[:, 1], xy[:, 0] ** 2, xy[:, 0] * xy[:, 1], xy[:, 1] ** 2]
    )
    basis -= basis.mean(axis=0)
    result = pd.DataFrame(basis, columns=BASIS_COLUMNS)
    result.insert(0, "tract_geoid", geometry.GEOID.astype(str).to_numpy())
    metadata = {
        "geometry_sha256": digest(GEOMETRY_PATH),
        "crs": "EPSG:3310",
        "tracts": len(result),
        "centroid_mean_m": mean.tolist(),
        "centroid_scale_m": scale.tolist(),
        "basis": BASIS_COLUMNS,
        "role": "explicit LA-local residual-correction contract exception",
    }
    return result, metadata


def add_basis(frame: pd.DataFrame, basis: pd.DataFrame) -> pd.DataFrame:
    result = frame.merge(basis, on="tract_geoid", how="left", validate="many_to_one")
    if result[BASIS_COLUMNS].isna().any().any():
        raise ValueError("Missing spatial basis for LA prediction row")
    return result


def forward_oof_residuals(
    prior,
    prior_config: dict,
    scored: pd.DataFrame,
    target: pd.Series,
    universe: pd.DataFrame,
    observed: pd.DataFrame,
    training_years: list[int],
) -> pd.DataFrame:
    frames = []
    for held_year in training_years[1:]:
        earlier = [year for year in training_years if year < held_year]
        rows, _ = prior.fit_predict_year(
            scored, target, universe, observed, earlier, held_year, prior_config
        )
        rows = rows.loc[
            :,
            [
                *prior.KEYS,
                "spatial_block",
                "observed_relative",
                f"{COMPARATOR}_relative_scored",
            ],
        ].copy()
        rows["oof_year"] = held_year
        rows["signed_residual"] = (
            rows.observed_relative - rows[f"{COMPARATOR}_relative_scored"]
        )
        frames.append(rows)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _safe_spearman(left: pd.Series, right: pd.Series) -> float | None:
    value = left.corr(right, method="spearman")
    return None if not np.isfinite(value) else float(value)


def diagnose_residuals(
    residuals: pd.DataFrame, basis: pd.DataFrame, config: dict
) -> dict:
    minimum_years = int(config["diagnostic"]["minimum_oof_years"])
    if residuals.empty:
        return {
            "supported": False,
            "reason": "no_forward_oof_year",
            "oof_years": [],
            "cross_year_spearman": [],
            "neighbor_spearman": [],
        }
    annual = (
        residuals.groupby(["oof_year", "tract_geoid"], observed=True)
        .signed_residual.median()
        .reset_index()
    )
    years = sorted(annual.oof_year.unique().tolist())
    cross_year = []
    for index, left_year in enumerate(years):
        left = annual.loc[annual.oof_year.eq(left_year), ["tract_geoid", "signed_residual"]]
        for right_year in years[index + 1 :]:
            right = annual.loc[
                annual.oof_year.eq(right_year), ["tract_geoid", "signed_residual"]
            ]
            pair = left.merge(right, on="tract_geoid", suffixes=("_left", "_right"))
            rho = _safe_spearman(pair.signed_residual_left, pair.signed_residual_right)
            if rho is not None:
                cross_year.append(
                    {
                        "left_year": int(left_year),
                        "right_year": int(right_year),
                        "tracts": len(pair),
                        "spearman": rho,
                        "sign_agreement": float(
                            np.mean(
                                np.sign(pair.signed_residual_left)
                                == np.sign(pair.signed_residual_right)
                            )
                        ),
                    }
                )

    neighbor_count = int(config["diagnostic"]["nearest_neighbors"])
    coordinate = basis.set_index("tract_geoid")[["space_x", "space_y"]]
    all_geoids = coordinate.index.to_numpy(dtype=str)
    all_distances = cdist(coordinate.to_numpy(), coordinate.to_numpy())
    np.fill_diagonal(all_distances, np.inf)
    all_neighbors = np.argpartition(
        all_distances, neighbor_count, axis=1
    )[:, :neighbor_count]
    neighbor_map = {
        geoid: all_geoids[indexes]
        for geoid, indexes in zip(all_geoids, all_neighbors, strict=True)
    }
    neighbor_rows = []
    for target_date, values in residuals.groupby("target_date", sort=True, observed=True):
        values = values.set_index("tract_geoid")
        residual_lookup = values.signed_residual.to_dict()
        own = []
        neighbor_mean = []
        for geoid, residual in residual_lookup.items():
            available = [
                residual_lookup[neighbor]
                for neighbor in neighbor_map[str(geoid)]
                if neighbor in residual_lookup
            ]
            if available:
                own.append(float(residual))
                neighbor_mean.append(float(np.mean(available)))
        rho = _safe_spearman(pd.Series(own), pd.Series(neighbor_mean))
        if rho is not None:
            neighbor_rows.append(
                {
                    "target_date": str(target_date),
                    "oof_year": int(pd.Timestamp(target_date).year),
                    "tracts": len(own),
                    "spearman": rho,
                    "sign_agreement": float(
                        np.mean(np.sign(own) == np.sign(neighbor_mean))
                    ),
                }
            )

    stable = residuals.groupby("tract_geoid", observed=True).signed_residual.median()
    mapped = residuals.tract_geoid.map(stable).to_numpy(dtype=float)
    total = residuals.signed_residual.to_numpy(dtype=float)
    total_variance = float(np.var(total))
    stable_variance = float(np.var(mapped))
    cross_median = (
        None if not cross_year else float(np.median([row["spearman"] for row in cross_year]))
    )
    neighbor_median = (
        None
        if not neighbor_rows
        else float(np.median([row["spearman"] for row in neighbor_rows]))
    )
    enough_years = len(years) >= minimum_years
    supported = bool(
        enough_years
        and cross_median is not None
        and neighbor_median is not None
        and cross_median
        >= float(config["diagnostic"]["minimum_cross_year_tract_bias_spearman"])
        and neighbor_median
        >= float(config["diagnostic"]["minimum_within_year_neighbor_residual_spearman"])
    )
    return {
        "supported": supported,
        "reason": "thresholds_passed" if supported else "insufficient_or_unstable_oof_residual",
        "oof_years": [int(year) for year in years],
        "oof_rows": len(residuals),
        "cross_year_spearman": cross_year,
        "median_cross_year_spearman": cross_median,
        "neighbor_spearman": neighbor_rows,
        "median_neighbor_spearman": neighbor_median,
        "total_residual_variance_c2": total_variance,
        "stable_tract_component_variance_c2": stable_variance,
        "stable_variance_fraction": (
            None if total_variance <= 0 else stable_variance / total_variance
        ),
        "total_residual_mae_c": float(np.mean(np.abs(total))),
        "dynamic_after_stable_tract_median_mae_c": float(np.mean(np.abs(total - mapped))),
    }


def fit_correction(
    residuals: pd.DataFrame, basis: pd.DataFrame, alpha: float, prior
) -> Ridge:
    training = add_basis(residuals, basis)
    weights = prior.city_date_row_weights(training)
    model = Ridge(alpha=alpha, fit_intercept=False)
    model.fit(
        training[BASIS_COLUMNS], training.signed_residual, sample_weight=weights
    )
    return model


def add_candidate(
    prior,
    base_rows: pd.DataFrame,
    base_prediction: pd.DataFrame,
    held_universe: pd.DataFrame,
    basis: pd.DataFrame,
    correction: Ridge | None,
) -> pd.DataFrame:
    support = held_universe[prior.KEYS].copy()
    if correction is None:
        correction_values = np.zeros(len(support), dtype=float)
    else:
        spatial = add_basis(held_universe[prior.KEYS], basis)
        correction_values = correction.predict(spatial[BASIS_COLUMNS])
    correction_values = prior.center(held_universe, correction_values)
    prediction = base_prediction.loc[
        :,
        [
            *prior.KEYS,
            f"{COMPARATOR}_relative_support",
            f"{COMPARATOR}_absolute",
        ],
    ].copy()
    prediction["spatial_correction_support"] = correction_values
    candidate_support = prior.center(
        held_universe,
        prediction[f"{COMPARATOR}_relative_support"].to_numpy()
        + prediction.spatial_correction_support,
    )
    # Preserve the fold-specific absolute level; only the relative component changes.
    level = (
        prediction[f"{COMPARATOR}_absolute"].to_numpy()
        - prediction[f"{COMPARATOR}_relative_support"].to_numpy()
    )
    prediction[f"{CANDIDATE}_relative_support"] = candidate_support
    prediction[f"{CANDIDATE}_absolute"] = level + candidate_support
    candidate = prediction.loc[
        :, [*prior.KEYS, f"{CANDIDATE}_relative_support", f"{CANDIDATE}_absolute"]
    ]
    result = base_rows.merge(candidate, on=prior.KEYS, validate="one_to_one")
    result[f"{CANDIDATE}_relative_scored"] = prior.center(
        result, result[f"{CANDIDATE}_relative_support"]
    )
    result[f"{CANDIDATE}_error"] = np.abs(
        result[f"{CANDIDATE}_relative_scored"] - result.observed_relative
    )
    result[f"{CANDIDATE}_support_error"] = np.abs(
        result[f"{CANDIDATE}_relative_support"] - result.observed_relative
    )
    result[f"{CANDIDATE}_absolute_error"] = np.abs(
        result[f"{CANDIDATE}_absolute"] - result.observed
    )
    return result


def promotion_checks(
    metrics: pd.DataFrame,
    bootstrap: dict,
    active_years: list[int],
    config: dict,
) -> tuple[dict, float]:
    overall = metrics.loc[metrics.held_year.eq("all")].set_index("model")
    improvement = 1.0 - float(overall.loc[CANDIDATE, "relative_mae_c"]) / float(
        overall.loc[COMPARATOR, "relative_mae_c"]
    )
    yearly = metrics.loc[metrics.held_year.ne("all")].pivot(
        index="held_year", columns="model", values="relative_mae_c"
    )
    contract = config["promotion"]
    checks = {
        "minimum_active_candidate_test_years": len(active_years)
        >= int(contract["minimum_active_candidate_test_years"]),
        "minimum_relative_mae_improvement": improvement
        >= float(contract["minimum_relative_mae_improvement"]),
        "maximum_test_year_mae_degradation": float(
            (yearly[CANDIDATE] - yearly[COMPARATOR]).max()
        )
        <= float(contract["maximum_test_year_mae_degradation_c"]),
        "support_centered_mae_not_degraded": float(
            overall.loc[CANDIDATE, "support_centered_relative_mae_c"]
            - overall.loc[COMPARATOR, "support_centered_relative_mae_c"]
        )
        <= float(contract["maximum_support_centered_mae_degradation_c"]),
        "absolute_mae_not_degraded": float(
            overall.loc[CANDIDATE, "absolute_mae_c"]
            - overall.loc[COMPARATOR, "absolute_mae_c"]
        )
        <= float(contract["maximum_absolute_mae_degradation_c"]),
        "hotspot_recall_not_degraded": float(
            overall.loc[COMPARATOR, "hotspot_recall"]
            - overall.loc[CANDIDATE, "hotspot_recall"]
        )
        <= float(contract["maximum_hotspot_recall_degradation"]),
        "positive_bootstrap_lower_bound": bootstrap["lower_c"] > 0,
    }
    return checks, improvement


def main() -> None:
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    with PRIOR_CONFIG_PATH.open("rb") as handle:
        prior_config = tomllib.load(handle)
    prior = load_module("la_local_accuracy_prior", PRIOR_RUN_PATH)
    output = ROOT / config["outputs"]["directory"]
    output.mkdir(parents=True, exist_ok=True)

    def status(state: str, **details) -> None:
        write_json(
            output / "status.json",
            {"state": state, "updated_utc": datetime.now(UTC).isoformat(), **details},
        )
        print(state, json.dumps(details), flush=True)

    status("loading")
    scored, target, universe, observed, input_records = prior.load_inputs(prior_config)
    basis, basis_metadata = load_spatial_basis()
    plan = prior.forward_plan(
        list(config["experiment"]["allowed_years"]),
        list(config["experiment"]["outer_test_years"]),
    )
    provenance = {
        "inputs": {
            "upstream_records": input_records,
            "tract_geometry": basis_metadata,
        },
        "config": config,
        "code": {
            str(path.relative_to(ROOT)): digest(path)
            for path in [Path(__file__), CONFIG_PATH, PRIOR_RUN_PATH]
        },
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    signature = hashlib.sha256(
        json.dumps(provenance, sort_keys=True).encode("utf-8")
    ).hexdigest()
    write_json(output / "provenance.json", {"signature": signature, **provenance})

    outer_frames = []
    diagnostics = []
    active_years = []
    correction_models = {}
    with threadpool_limits(limits=int(config["experiment"]["threads"])):
        for index, fold in enumerate(plan, start=1):
            status("training_window_oof_diagnostic", fold=index, total=len(plan), **fold)
            residuals = forward_oof_residuals(
                prior,
                prior_config,
                scored,
                target,
                universe,
                observed,
                fold["outer_train_years"],
            )
            diagnostic = diagnose_residuals(residuals, basis, config)
            diagnostic["outer_test_year"] = int(fold["outer_test_year"])
            diagnostics.append(diagnostic)
            correction = None
            if diagnostic["supported"]:
                correction = fit_correction(
                    residuals,
                    basis,
                    float(config["spatial_correction"]["ridge_alpha"]),
                    prior,
                )
                active_years.append(int(fold["outer_test_year"]))
                correction_models[str(fold["outer_test_year"])] = correction
            status(
                "outer_test",
                fold=index,
                correction_active=diagnostic["supported"],
                **fold,
            )
            base_rows, base_models = prior.fit_predict_year(
                scored,
                target,
                universe,
                observed,
                fold["outer_train_years"],
                fold["outer_test_year"],
                prior_config,
            )
            held_universe = prior.held_rows(universe, fold["outer_test_year"])
            base_prediction = prior.predict_models(
                base_models, held_universe, prior_config
            )
            rows = add_candidate(
                prior,
                base_rows,
                base_prediction,
                held_universe,
                basis,
                correction,
            )
            rows["held_year"] = int(fold["outer_test_year"])
            rows["spatial_correction_active"] = diagnostic["supported"]
            outer_frames.append(rows)

        rows = pd.concat(outer_frames, ignore_index=True)
        rows.to_parquet(output / "forward_oof.parquet", index=False)
        pd.DataFrame(diagnostics).to_json(
            output / "diagnostics.json", orient="records", indent=2
        )
        names = [COMPARATOR, CANDIDATE]
        metrics = [
            prior.model_metrics(subset, name, float(config["experiment"]["hotspot_fraction"]))
            | {"held_year": label}
            for label, subset in [("all", rows), *list(rows.groupby("held_year"))]
            for name in names
        ]
        metrics_table = pd.DataFrame(metrics)
        metrics_table.to_csv(output / "metrics.csv", index=False)
        bootstrap = prior.crossed_bootstrap(
            rows,
            COMPARATOR,
            CANDIDATE,
            int(config["promotion"]["bootstrap_iterations"]),
            int(config["experiment"]["seed"]),
        )
        checks, improvement = promotion_checks(
            metrics_table, bootstrap, active_years, config
        )

        status("fitting_full_history_development_artifact")
        full_residuals = forward_oof_residuals(
            prior,
            prior_config,
            scored,
            target,
            universe,
            observed,
            list(config["experiment"]["allowed_years"]),
        )
        final_diagnostic = diagnose_residuals(full_residuals, basis, config)
        final_correction = (
            fit_correction(
                full_residuals,
                basis,
                float(config["spatial_correction"]["ridge_alpha"]),
                prior,
            )
            if final_diagnostic["supported"]
            else None
        )
        final_base = prior.fit_models(scored, target, prior_config)
        bundle = {
            "base_models": final_base,
            "spatial_correction": final_correction,
            "basis_metadata": basis_metadata,
            "basis_table": basis,
            "signature": signature,
            "promotion_passed": all(checks.values()),
            "role": config["experiment"]["role"],
        }
        model_path = output / "development_model.joblib"
        joblib.dump(bundle, model_path)
        reloaded = joblib.load(model_path)
        if reloaded["signature"] != signature:
            raise ValueError("Serialized development artifact changed")

    per_year = metrics_table.loc[metrics_table.held_year.ne("all")].pivot(
        index="held_year", columns="model", values="relative_mae_c"
    )
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment"]["id"],
        "role": config["experiment"]["role"],
        "completed_at": datetime.now(UTC).isoformat(),
        "signature": signature,
        "city_id": config["experiment"]["city_id"],
        "no_la_2025_or_external_city_targets_used": True,
        "global_feature_registry_unchanged": True,
        "local_coordinate_contract_exception": basis_metadata,
        "forward_plan": plan,
        "training_window_diagnostics": diagnostics,
        "final_full_history_training_diagnostic": final_diagnostic,
        "candidate_active_test_years": active_years,
        "candidate_inactive_test_years": sorted(
            set(config["experiment"]["outer_test_years"]) - set(active_years)
        ),
        "relative_improvement": improvement,
        "bootstrap": bootstrap,
        "promotion_checks": checks,
        "promotion_passed": all(checks.values()),
        "per_year_relative_gain_c": {
            str(year): float(per_year.loc[year, COMPARATOR] - per_year.loc[year, CANDIDATE])
            for year in per_year.index
        },
        "metrics": metrics,
        "model_sha256": digest(model_path),
    }
    write_json(output / "summary.json", summary)
    status(
        "complete",
        promotion_passed=summary["promotion_passed"],
        relative_improvement=improvement,
        candidate_active_test_years=active_years,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        try:
            with CONFIG_PATH.open("rb") as handle:
                failed = tomllib.load(handle)
            write_json(
                ROOT / failed["outputs"]["directory"] / "status.json",
                {
                    "state": "failed",
                    "updated_utc": datetime.now(UTC).isoformat(),
                    "error": repr(error),
                },
            )
        finally:
            raise
