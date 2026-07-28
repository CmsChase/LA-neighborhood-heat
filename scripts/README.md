# Script map

Files in this directory are thin command-line entry points. Reusable logic
belongs in [`src/la_heat`](../src/la_heat/README.md). Always read the
[current handoff](../docs/PROJECT_HANDOFF.md) before running a script because
many historical stages are already complete.

## Workflow groups

| Stage | Main entry points |
|---|---|
| Target inventory and labels | `download_static_sources.py`, `build_final_test_inventory.py`, `run_target_build_resumable.py`, `generate_target_checkpoint.py` |
| Static, Daymet, and Sentinel features | `build_static_features.py`, `stage_daymet_grid.py`, `build_daymet_features.py`, `build_sentinel_inventory.py`, `build_sentinel_features.py`, `promote_sentinel_features.py` |
| Phase 2 assembly and validation | `build_feature_universe.py`, `build_calendar_features.py`, `build_phase2_registry.py`, `audit_phase2_readiness.py`, `build_phase2_features.py`, `build_model_dataset.py`, `build_validation_splits.py`, `promote_validation_splits.py` |
| Development modeling | `audit_model_selection_freeze.py`, `run_grouped_models.py`, `import_returned_model_results.py`, `build_final_models.py`, `stage_model_lock.py`, `promote_formal_model_lock.py` |
| Development diagnostics | `analyze_model_results.py`, `analyze_model_endpoints.py`, `analyze_model_qa.py`, `analyze_residual_spatial.py`, `analyze_feature_ablation.py`, `analyze_stqa2_sensitivity.py`, `reconcile_development_robustness.py`, `generate_model_diagnostic_figures.py`, `generate_development_report.py` |
| Final-test predictor preparation | files beginning `build_final_test_`, `stage_final_test_`, `run_final_test_`, and `audit_final_test_` |
| Final evaluation and evidence | `prepare_final_evaluation.py`, `authorize_final_test_2025.py`, `execute_locked_final_evaluation.py`, `build_final_evaluation_evidence.py`, `verify_final_evaluation_evidence.py` |
| Communication | `stage_mapping_la_neighborhoods.py`, `build_website_data.py`, `build_research_paper.py` |
| Cross-city continuation planning | `audit_multicity_plan.py` |
| Transfer and local control | `create_portable_relocation.py`, `verify_portable_relocation.py`, `transfer_queue_snapshot.py`, `*_dashboard.py`, `*_watchdog.py`, `research_runner_ui.py`, and the PowerShell transfer helpers |

## Website source staging

```powershell
.\.venv\Scripts\python scripts\stage_mapping_la_neighborhoods.py
```

`stage_mapping_la_neighborhoods.py` downloads or reauthenticates the exact
commit-pinned Los Angeles Times Mapping L.A. GeoJSON used only for website
labels. It refuses an existing or downloaded file unless its frozen SHA-256
matches. `build_website_data.py` then derives display-only neighborhood
assignments and homepage pixels from that source and the already frozen
evaluation; neither command trains or retunes a model.

The current public output is `website-display-export-v3`, deployed from the
nested website commit `206c04b797b184aa3dcb3aec60ac0ffaba8775bb` by successful
GitHub Actions run `30358959707`. Exact display hashes are recorded in
[`docs/RESULTS_WEBSITE.md`](../docs/RESULTS_WEBSITE.md).

## Safe read-only checks

These commands verify existing products rather than rerun the one-time result:

```powershell
.\.venv\Scripts\python scripts\verify_final_evaluation_evidence.py
.\.venv\Scripts\python scripts\build_website_data.py --verify-only `
  --output-dir website-github-pages\public\data
```

Tests and Ruff are documented in [`tests/README.md`](../tests/README.md).

## Completed historical workflows

The 2025 preparation, authorization, single consumption claim, target-value
opening, and final evaluation are complete. Do **not** start a second
authorization or claim, alter thresholds, or treat the final-evaluation scripts
as a normal rebuild pipeline.

The old Sentinel and model dashboards record completed work. Leave them
stopped unless the handoff explicitly identifies a current resumable task and
its exact output directory. Never run two controllers against one canonical
directory.

`audit_multicity_plan.py` authenticates the completed Phase I transaction and
the draft continuation locks. Its current successful state permits only the
Phoenix boundary/public-metadata pilot. It never opens a new Landsat thermal or
target-QA value.

Development rebuild and analysis scripts can overwrite or regenerate local
products. Run them only for a defined task after checking configuration,
provenance, Git state, and output ownership.
