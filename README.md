# Los Angeles Neighborhood Surface Heat

Reproducible research project testing whether public weather, land-use,
geographic, and lagged non-thermal satellite features can predict
neighborhood-scale daytime surface heat in the City of Los Angeles.

## Locked research question

> How accurately can public weather, land-use, topographic, and lagged
> non-thermal satellite features predict census-tract daytime land-surface
> temperature and relative surface-heat hotspots in the City of Los Angeles
> on unseen warm-season dates and in unseen spatial areas?

The supervised label is QA-filtered Landsat 8/9 Collection 2 Level-2 land
surface temperature (LST). LST is a **surface-heat hazard proxy**, not air
temperature, personal exposure, illness, or mortality.

## Current status

- [x] Research target and study scope selected
- [x] Scientific leakage rules written and tested
- [x] Three-date feasibility pilot completed: absolute labels were usable on all
      dates; June/August relative labels passed and October was withheld
- [x] Detailed 2020 TIGER tract geometry acquired and audited
- [x] Complete target-blind 2020–2024 Landsat overpass inventory built
- [x] Fixed-grid adjacent-scene mosaic and target builder integration-tested
- [x] Full 2020–2024 target table built and independently audited
- [x] Target-blind Sentinel-2 acquisition/window inventory frozen
- [x] Required SRTM tiles and Census 2019 coastline downloaded and audited
- [x] Official NLCD 2016 sources downloaded and audited
- [x] NLCD/SRTM/coast static feature table built and audited
- [x] Official Daymet V4 R1 development granule inventory frozen
- [x] All 226 Sentinel-2 acquisition caches, lagged composites, and formal
      predictor outputs completed and independently promoted
- [x] Target-blind 90-date × 1,096-tract predictor key universe built
- [x] Known-at-origin calendar table and 46-feature registry draft built
- [x] Temporal/spatial/joint validation and absolute-LST metric drafts audited
- [x] Target- and score-blind Phase 2 readiness audit completed with no blockers
- [x] All 31 model-selection candidates and the tie-breaking rule frozen before
      any model score
- [x] All 30 official Daymet subsets downloaded and 21 lagged weather features built
- [x] Target-blind 98,640-row Phase 2 table and final registry frozen
- [x] Formal 63,403-row development modeling table assembled and audited
- [x] Grouped temporal/spatial/joint validation formally promoted
- [x] Baselines and main model trained; authenticated development OOF analyzed
- [x] Hotspot, sensor, and Sentinel-missingness diagnostics complete
- [x] QA cohort and failure-case diagnostics complete
- [x] Residual and spatial-autocorrelation diagnostics complete
- [x] Reproducible development diagnostic figures complete
- [x] Feature-family ablation complete
- [x] Pixel-level ST_QA <= 2 K sensitivity complete (15/30 date gate failed)
- [x] Development robustness reconciled and generated report complete
- [ ] Model lock created
- [ ] 2025 final test unlocked and run once

The feasibility pilot created a 1,110-tract mother manifest and froze 1,096
City-clipped, non-special-use primary tracts. It retained 95.5%, 95.0%, and
77.8% on three representative dates. The target-blind
inventory froze 90 unambiguous physical overpasses with complete City coverage.
The corrected Landsat-aligned target builder has now completed all 90: 65 pass
the independent ≥50% date-retention gate and 34 also pass the relative-endpoint
spatial-representativeness gate. The committed QA table has 98,640 rows; the
legal absolute-LST model table has 63,403 rows across 65 independent dates and
71 spatial blocks. It contains zero duplicate keys and zero 2025 rows, and each
tract's static eligible-land count and exact pixel-identity hash are invariant
across all 90 dates. Development data only have been opened; final-test labels
remain locked.

Phase 2 has frozen 226 Sentinel-2 physical acquisitions and 1,045 legal
`d−60 … d−1` target-window memberships across all 90 development dates. The two
required SRTM tiles, Census 2019 coastline ZIP, and official MRLC NLCD 2016
subsets are preserved with exact hashes and content audits. The promoted static
table has 1,096 unique GEOIDs, 18 legal model predictors, one audit-only NLCD
reference fraction, no missing values, and 100% observed support coverage for
all five source layers.

Official CMR discovery froze 30 Daymet granules (six variables × five
development years) with zero 2025 entries. All 30 official grid subsets are now
downloaded and hash-audited. The completed compiler applies the 365-day
calendar, invariant tract-cell weights, cell-first shortwave energy, and strict
`d−1` cutoff to produce 21 complete weather predictors on all 98,640
target-blind tract-date keys.

All 226 Sentinel acquisition caches are complete, totaling 247,696 unique
acquisition-tract rows. Their formally promoted 60-day predictor table has
98,640 rows: 97,870 have all five optical predictors and 770 preserve all five
as missing because they do not meet the frozen three-acquisition rule. The
1,145,320-row lineage has no duplicate, target-day, future, or 2025 source and
uses only ages 1–60 days. The canonical processed feature file SHA-256 is
`aa02df3a00c51076610f442512949ade5ca70ab466b4d2d9c513826184fe82b5`;
its independent promotion commit is
`bf3adfffcfe52df7cca7c366fa214d6cb11a5cca4bf1111454c99c87fd48e291`.

The readiness audit now verifies the key universe and all four predictor
families with `blockers=[]` and `state=ready_for_feature_assembly`; it read no
target value, target-QA table, or model score. A separate target-blind assembly
then froze a 98,640 × 49 Phase 2 table: two keys, 46 model features (18 static,
2 calendar, 21 Daymet, and 5 Sentinel), and one audit-only static reference.
Its commit is
`3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.

After that target-blind gate passed, the legal target join produced the formal
63,403 × 50 development modeling table across 65 independent dates. It contains
46 model features, one audit-only field, the response, two keys, and zero 2025
rows. Exactly 63,235 rows have all 46 model features; the remaining 168 preserve
the frozen all-five-missing Sentinel pattern for training-fold-only imputation.
The model-table commit is
`9c2f903993167fc2a228b3cfe60a23fe33f57f252bae6299458338cb8eb967ad`.

The grouped validation manifest is now formally promoted over all 63,403 legal
keys, 65 dates, and 71 spatial blocks. It freezes 5 temporal, 71 spatial, and
355 joint folds (431 total), with every row assigned to OOF test exactly once
per family. Promotion read only keys and split metadata—no target value,
predictor value, or model score—and kept 2025 locked. Its commit is
`6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc`.
The recoverable nested grouped-modeling pipeline completed all 55,645 inner fits
and 2,155 outer refits. Strict import from the returned ZIP preserved all 2,155
original fragments and compiled 951,045 authenticated development OOF rows. In
joint validation, M2 improves date-macro MAE by 16.19% over the strongest legal
baseline B1; the paired crossed date-by-block 95% interval is 4.21%–27.69%, and
median per-date Spearman is 0.793. Required development gates pass, while the
stronger check that the full interval exceed 10% does not. This is not a 2025
final-test result.

Legal model factories exist, and the exact 31-candidate nested-selection grid,
date-macro objective, and deterministic tie rule are frozen under commit
`4d8c2bd37be67f9f46d89d1dec8d5ed0aab196b24b43f9745ff730f040f2a6cd`.
The selection contract was frozen before any fit. The completed comparison did
not change its candidates, folds, metrics, thresholds, or final-test lock after
scores were observed. Hotspot, sensor, Sentinel-missingness, QA/failure-case,
residual, and spatial-autocorrelation diagnostics are complete. The frozen
feature-family ablation and strict pixel-level ST_QA sensitivity are also
complete and reconciled. The strict build retained only 15 usable dates versus
the required 30, so it is preserved as a limitation rather than promoted. The
generated development report is `reports/DEVELOPMENT_REPORT.md`. The next
compute stage is the separate, not-yet-started full-development final fit; 2025
remains locked.

## Study design

- Primary unit: 2020 Census tract × Landsat overpass date
- Warm season: May through October
- Development period: 2020–2024
- Frozen final test: 2025
- Primary target: tract-median daytime LST in °C
- Neighborhood endpoint: within-date LST anomaly
- Hotspot endpoint: relative hottest 20% of retained tracts per date
- Validation: temporal, spatial-block, and joint spatiotemporal holdouts
- Primary metric: date-macro MAE, so each acquisition date has equal weight

See `docs/PROJECT_PLAN.md` for the execution table and
`docs/RESEARCH_PROTOCOL.md` for the scientific contract. The target-blind
predictor implementation contract is in `docs/PHASE2_FEATURE_SPEC.md`.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

Run the target-data pilot:

```powershell
.\.venv\Scripts\python -m la_heat.pilot --config configs/research.toml
```

Build the complete target-blind development overpass inventory:

```powershell
.\.venv\Scripts\python -m la_heat.inventory --config configs/research.toml
```

Resume the cache-safe full development target build (90 overpasses):

```powershell
.\.venv\Scripts\python -m la_heat.target_builder --config configs/research.toml
```

For remote-raster builds that must recover automatically from transient COG
read failures, use the bounded retry supervisor. It preserves validated
overpass caches and accepts both a promoted build and a scientifically valid
completed date-gate failure:

```powershell
.\.venv\Scripts\python scripts/run_target_build_resumable.py `
  --config configs/research_stqa2_sensitivity.toml `
  --output-directory data/interim/targets_sensitivity_stqa2 `
  --max-attempts 20 `
  --retry-delay-seconds 30
```

Rebuild Phase 2 source inventories and features:

```powershell
.\.venv\Scripts\python scripts/download_nlcd_2016_sources.py
.\.venv\Scripts\python scripts/build_static_features.py --config configs/research.toml
.\.venv\Scripts\python scripts/stage_daymet_grid.py --config configs/research.toml
.\.venv\Scripts\python scripts/build_sentinel_features.py
.\.venv\Scripts\python scripts/promote_sentinel_features.py
.\.venv\Scripts\python scripts/audit_model_selection_freeze.py
```

Build the target-blind predictor support and grouped-validation artifacts:

```powershell
.\.venv\Scripts\python scripts/build_feature_universe.py
.\.venv\Scripts\python scripts/build_calendar_features.py
.\.venv\Scripts\python scripts/build_phase2_registry.py
.\.venv\Scripts\python scripts/build_validation_splits.py
.\.venv\Scripts\python scripts/promote_validation_splits.py
```

These commands create no model result. They write the full feature-only
tract-date support, deterministic calendar table, metadata-only registry draft,
and split formulas/audits over legal development keys and fixed tract geometry.
All leave 2025 locked.

The Daymet inventory command is anonymous. For an interactive authenticated
download, including Windows PowerShell 5.1, clear any stale credential variables
and let Python read the token from a hidden terminal prompt:

```powershell
Remove-Item Env:EARTHDATA_TOKEN,Env:NASA_EARTHDATA_TOKEN,Env:EDL_TOKEN -ErrorAction SilentlyContinue
.\.venv\Scripts\python scripts\stage_daymet_grid.py --config configs/research.toml --download-subsets --prompt-token
.\.venv\Scripts\python scripts\build_daymet_features.py --config configs/research.toml
.\.venv\Scripts\python scripts\audit_phase2_readiness.py
.\.venv\Scripts\python scripts\build_phase2_features.py
.\.venv\Scripts\python scripts\build_model_dataset.py
```

Paste the token only after the Python prompt appears. Its value is neither
printed nor persisted, and prompt mode fails before networking if any configured
token environment variable still exists. Noninteractive automation may instead
set exactly one of `EARTHDATA_TOKEN`, `NASA_EARTHDATA_TOKEN`, or `EDL_TOKEN`.
The Sentinel build command is cache-safe and now serves as a reproducible
rebuild/revalidation path for the already completed 226-acquisition stage.

For a future Sentinel rebuild with visible progress, cooperative start/pause
control, and automatic process recovery, launch the dashboard through its
watchdog instead of the direct Sentinel command:

```powershell
.\.venv\Scripts\python scripts/sentinel_dashboard_watchdog.py --workers 2
```

It opens `http://127.0.0.1:8765/` and reuses strictly validated acquisition
caches. Safe Pause records a persistent paused intent, stops submitting new
work, and waits for active acquisitions to commit atomically. Transient network
and remote-raster failures retry automatically with bounded backoff while other
acquisitions continue. A retryable runner failure triggers cache revalidation
and automatic batch reconstruction; a nonzero dashboard-process exit is
restarted by the watchdog. Configuration, schema, hash, and scientific
integrity failures remain fail-closed rather than being hidden by an endless
retry loop. The watchdog restores only a persisted `running` intent, never a
user-requested `paused` state.

Reproduce the authenticated initial development-result tables from the
canonical compiled evaluation directory:

```powershell
.\.venv\Scripts\python scripts/analyze_model_results.py --config configs/result_analysis.toml
.\.venv\Scripts\python scripts/analyze_model_endpoints.py --config configs/model_endpoint_diagnostics.toml
.\.venv\Scripts\python scripts/analyze_residual_spatial.py --config configs/residual_spatial_diagnostics.toml
.\.venv\Scripts\python scripts/analyze_model_qa.py --config configs/model_qa_diagnostics.toml
.\.venv\Scripts\python scripts/generate_model_diagnostic_figures.py --config configs/model_diagnostic_figures.toml
.\.venv\Scripts\python scripts/analyze_feature_ablation.py --config configs/feature_ablation_analysis.toml
.\.venv\Scripts\python scripts/analyze_stqa2_sensitivity.py --config configs/stqa2_sensitivity_analysis.toml
.\.venv\Scripts\python scripts/reconcile_development_robustness.py --config configs/robustness_reconciliation.toml
.\.venv\Scripts\python scripts/generate_development_report.py --config configs/development_report.toml
```

The grouped queue is already terminal at 57,800 / 57,800, so do not restart the
model dashboard as a next step. Hotspot, sensor-stratified, residual, spatial-
autocorrelation, QA/missingness, failure-case, ablation, and pixel-level ST_QA
diagnostics are frozen and reconciled. Full-development final tuning/refitting
is prepared but intentionally not started automatically. Calendar year 2025
remains locked until the full model lock is approved.

When the computer is available for that separate fit, open the opt-in controller:

```powershell
.\.venv\Scripts\python scripts\final_model_runner_ui.py
```

Then visit `http://127.0.0.1:8766/`. Every newly opened controller session
forces the task into a paused, disarmed state; computation starts only after
clicking **Start / Continue** in that same session. Completed tuning fragments
are retained across pauses. A transient exit restarts with bounded backoff, but
repeated exits without new progress fail closed. The controller never unlocks
or reads the 2025 final test.

After the staging audit is committed from a clean tree, formal promotion is a
separate one-way command. It re-authenticates the fitted B1/M2 artifacts and all
frozen inputs, refuses to overwrite an existing lock, and does not authorize
final-test access:

```powershell
.\.venv\Scripts\python scripts\promote_formal_model_lock.py --approve-formal-lock
```

The pilot queries public STAC metadata, reads only the required remote COG
windows, applies the locked Landsat QA and temperature scaling, aggregates to
Los Angeles Census tracts, and writes auditable coverage, mask-waterfall, and
QA-sensitivity summaries.

## Repository layout

```text
configs/                 versioned study and pilot configuration
data/raw/                immutable downloads (untracked)
data/interim/            reproducible intermediate products (untracked)
data/processed/          model-ready tables (untracked)
docs/                    protocol, decisions, provenance, experiment log
reports/                 generated figures and tables
src/la_heat/             reusable data and modeling code
tests/                   scientific and engineering guardrails
```
