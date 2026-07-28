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

`final_evaluation_2025.toml` binds exact input hashes, state-marker paths,
target-cache paths, output names, metrics, bootstrap settings, and success
gates. It is frozen evidence, not a reusable template.

`research.toml` records the already-consumed final-test unlock. Do not revert
it, create a second unlock, or change either file to obtain a different final
result. Consult the [mandatory handoff](../docs/PROJECT_HANDOFF.md) before any
configuration edit.
