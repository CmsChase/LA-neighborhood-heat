# `la_heat` package map

This package contains reusable implementation. Thin command-line wrappers live
in [`scripts/`](../../scripts/README.md); do not execute package files directly
unless their module interface is explicitly documented.

## Module groups

| Area | Representative modules |
|---|---|
| Landsat targets and geography | `landsat.py`, `inventory.py`, `mosaic.py`, `grid.py`, `landmask.py`, `target_builder.py`, `target_aggregation.py`, `boundaries.py` |
| Static and weather features | `static_sources.py`, `static_features.py`, `daymet_grid.py`, `daymet_feature_stage.py`, `calendar_features.py` |
| Sentinel features | `sentinel_inventory.py`, `sentinel_features.py`, `sentinel_feature_builder.py`, `sentinel_feature_stage.py`, `sentinel_compile_adapter.py` |
| Feature assembly and validation | `feature_universe.py`, `feature_registry.py`, `phase2_readiness.py`, `phase2_feature_stage.py`, `model_dataset.py`, `validation_splits.py` |
| Modeling and locks | `modeling.py`, `model_selection.py`, `grouped_model_run.py`, `model_task_engine.py`, `final_model.py`, `formal_model_lock.py` |
| Diagnostics and reports | modules ending `_analysis.py`, `_diagnostics.py`, `development_report.py`, and `model_diagnostic_figures.py` |
| Final-test preparation | modules beginning `final_test_` for inventory, features, predictors, authorization, and state locking |
| Frozen final evaluation | `final_evaluation_protocol.py`, `final_evaluation_targets.py`, `final_evaluation_reporting.py` |
| Operations and interfaces | `execution_ownership.py`, `portable_relocation.py`, dashboard/watchdog modules, and `research_runner_ui.py` |
| Public display export | `website_export.py` |

## Boundaries

- Reusable calculations belong here; filesystem orchestration belongs in a
  script entry point.
- Scientific constants and paths should come from versioned configuration.
- Generated values and provenance must never be hand-edited.
- Final-evaluation and authorization modules are bound to the completed
  one-time transaction. Do not rename, relocate, or repurpose them.
- Every change affecting time cutoffs, QA, splits, units, schemas, or locks
  requires focused regression tests and the full project checks.

See [`../../docs/RESEARCH_PROTOCOL.md`](../../docs/RESEARCH_PROTOCOL.md) for
the scientific contract and
[`../../docs/PROJECT_HANDOFF.md`](../../docs/PROJECT_HANDOFF.md) for current
operational state.
