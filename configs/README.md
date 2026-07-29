# Configuration map

Configuration files hold versioned scientific choices and paths. Prefer a
configuration change over hard-coded script values, but never change a frozen
setting after inspecting its result.

## Groups

| Purpose | Files |
|---|---|
| Core study and sensitivity target | `research.toml`, `research_stqa2_sensitivity.toml` |
| Feature construction | `sentinel_features.toml`, `validation_splits.toml` |
| Model selection and final development fit | `model_selection.toml`, `final_model.toml` |
| Development diagnostics | `result_analysis.toml`, `model_endpoint_diagnostics.toml`, `model_qa_diagnostics.toml`, `residual_spatial_diagnostics.toml`, `model_diagnostic_figures.toml` |
| Robustness | `feature_ablation.toml`, `feature_ablation_analysis.toml`, `stqa2_sensitivity_analysis.toml`, `robustness_reconciliation.toml` |
| Reporting | `development_report.toml` |
| One-time held-out evaluation | `final_evaluation_2025.toml` |
| Cross-city continuation planning | `multicity/experiment.toml`, `multicity/cities/*.toml`, `multicity/water_distance_review_v1.toml`, and `multicity/gshhg_geometry_pilot_v{1,2}.toml` |

`final_evaluation_2025.toml` binds exact input hashes, state-marker paths,
target-cache paths, output names, metrics, bootstrap settings, and success
gates. It is frozen evidence, not a reusable template.

`research.toml` records the already-consumed final-test unlock. Do not revert
it, create a second unlock, or change either file to obtain a different final
result. Consult the [mandatory handoff](../docs/PROJECT_HANDOFF.md) before any
configuration edit.

The `multicity/` configuration is a separate draft continuation. Its initial
lock permits only boundary and public-metadata staging and currently limits
writes to the Phoenix pilot. Census TIGERweb is the authoritative first source;
fixed Esri Demographics item IDs provide a vertex-preserving pilot mirror when
the local route to TIGERweb is unavailable. The mirror is not yet a
confirmatory source freeze. Nothing here authorizes predictor construction,
model fitting, external-city target access, or a real-time forecast claim.

`multicity/water_distance_review_v1.toml` records a completed, nonbinding,
target-blind source review. It authenticates Census 2019 as the U.S.-only
benchmark and historically defined the now-completed GSHHG geometry-only
gate, while explicitly leaving the portable source, algorithm, and feature
names unfrozen.

`multicity/gshhg_geometry_pilot_v1.toml` is the immutable before-geometry
preregistration. V1 failed before distance calculation because one L1 polygon
was invalid and five named lake seeds mapped to three L2 connected-water
polygons. `multicity/gshhg_geometry_pilot_v2.toml` records the
source-structure-only amendment committed before any diagnostic distance was
opened. It leaves every V1 point, numerical threshold, access lock, and
source-freeze prohibition unchanged.
