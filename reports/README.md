# Reports and generated results

This directory contains human-readable outputs generated from frozen inputs and
configuration. Reported values must come from these products or their
authenticated source tables, never from manual edits.

## Authoritative reports

- [`FINAL_EVALUATION_REPORT.md`](FINAL_EVALUATION_REPORT.md): the one-time
  held-out 2025 result and its required uncertainty limitation.
- [`DEVELOPMENT_REPORT.md`](DEVELOPMENT_REPORT.md): grouped development
  validation, diagnostics, robustness checks, and limitations.
- `DEVELOPMENT_REPORT_provenance.json`: inputs and hashes for the generated
  development report.

## Generated collections

- `figures/generated/`: maps and diagnostic figures.
- `tables/model_results_initial/`: baseline and model comparisons.
- `tables/model_endpoint_diagnostics/`: hotspot, sensor, and Sentinel-stratum
  diagnostics.
- `tables/model_qa_diagnostics/`: QA cohorts and worst-case summaries.
- `tables/residual_spatial/`: residual summaries and Moran's I.
- `tables/feature_ablation/`, `tables/stqa2_sensitivity/`, and
  `tables/robustness_reconciliation/`: prespecified robustness analyses.

Generated tables and figures are not scratch space. Do not hand-edit, rename,
or replace them. Rebuild only with the matching configuration and script, then
update provenance, tests, the decision log, and the
[project handoff](../docs/PROJECT_HANDOFF.md).
