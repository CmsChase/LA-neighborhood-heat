# Mandatory project handoff

Last material update: 2026-07-26 09:45 Asia/Shanghai

Latest required scientific code checkpoint:
`1dbb0232c9c278118875a6c4eb9af2c6e8e29720` on `main`

The mandatory handoff protocol itself was introduced at:
`6818f17732c4a72c792c564d0b3fe40153e46c0e`

Always verify the actual checkout with `git rev-parse HEAD` and
`git status --short`; this document intentionally avoids claiming its own
circular Git commit identifier.

Canonical remote: `https://github.com/CmsChase/LA-neighborhood-heat.git`

## Read this first

This is the authoritative session-state document. Every new contributor must
read it completely before running commands, editing files, starting a service,
or opening final-test data.

The scientific truth still comes from authenticated manifests and generated
provenance. If this document disagrees with those artifacts, do not guess:
fail closed, record the discrepancy here, and repair the handoff or pipeline
before proceeding.

Before ending a session, update:

1. the timestamp and Git checkpoint above;
2. the live runtime state below;
3. completed work, exact outputs, hashes, and tests;
4. failures or invalidated artifacts;
5. the first safe next command.

Never write a password, bearer token, signed URL, cookie, or other secret here.

## Research question and fixed interpretation

Question: can public weather, land-use, geography, and lagged non-thermal
satellite features predict neighborhood-level urban surface-heat risk in the
City of Los Angeles?

The primary endpoint is QA-filtered daytime Landsat land-surface temperature
(LST) at census-tract × physical-overpass-date resolution. LST is a
surface-heat hazard proxy. It is not measured human exposure, a health outcome,
or causal evidence.

The primary analysis is a historical hindcast with prediction origin at
00:00 local time on each target date. Dynamic observed inputs end on target
day minus one. It must not be described as an operational weather forecast.

## Non-negotiable scientific locks

- Never randomly split tract-date rows.
- Development uses whole dates, whole years, contiguous spatial blocks, and
  joint spatiotemporal validation.
- Calendar year 2025 is the one-time final test.
- Never use a Landsat thermal band, LST product, target-derived statistic,
  same-scene optical band, future observation, or tract ID as a predictor.
- The primary model never uses raw coordinates.
- Sentinel composites use only `d−60` through `d−1`.
- Daymet features use complete 1-, 3-, and 7-day rolling windows, each ending
  at `d−1`.
- Imputation, scaling, feature selection, climatology, and tuning are fit on
  training folds only.
- Adjacent Landsat WRS scenes and Sentinel MGRS tiles from one physical
  overpass are mosaic contributors, not independent dates.
- Every tract's WorldCover-derived eligible-land denominator is invariant
  across dates. Any change is a hard failure.
- Report independent dates and spatial blocks; do not treat tract-date rows as
  independent samples.
- Feature importance is predictive association, not causation.

## Formal final-test lock

Authoritative lock:

`manifests/model_lock/MODEL_LOCK.json`

MODEL_LOCK file SHA-256:

`bf77762bbd1838be2b67e8461c5f99aad1c2ebf36b4f3b53b25dac1801a81245`

Internal canonical `commit_sha256`:

`584ccfcb6a32a5a9c380e6e029f5205b91b21684ca6655f240eb72d49e76115b`

Required state:

- `final_test_locked = true`
- `final_test_values_read = false`
- `one_time_final_evaluation_authorized = false`

The following file must not exist before the deliberately authorized one-time
evaluation:

`manifests/final_test_2025/AUTHORIZATION.json`

As of this handoff it does not exist. No 2025 Landsat target value, QA value,
prediction, residual, score, or metric has been read or produced.

## Completed development phase

- Development years: 2020–2024.
- Landsat inventory: 90 physical overpass dates.
- Legal model table: 63,403 rows across 65 usable dates and 1,096 tracts.
- Spatial design: 71 contiguous blocks.
- Frozen model features: 46 total.
  - 18 static land-use/geography features
  - 2 calendar features
  - 21 lagged Daymet features
  - 5 lagged Sentinel features
- All 57,800 grouped-model tasks completed.
- Joint out-of-fold B1 ridge MAE: `2.516141 °C`.
- Joint out-of-fold M2 histogram-gradient-boosting MAE: `2.108788 °C`.
- M2 point-estimate improvement over B1: `16.1896%`.
- Robustness, hotspot, sensor, QA, residual/spatial, ablation, and ST_QA
  sensitivity analyses completed.
- B1 and M2 were refit on all legal development rows and frozen before any
  final-test values were opened.

Authoritative development report:

`reports/DEVELOPMENT_REPORT.md`

SHA-256:

`d0fbdc5a598b9b4acb23ae7c3cbc7afe2596d1cb428bdbba6042cdb738574d31`

## Completed target-blind 2025 preparation

### Landsat metadata and key universe

Authoritative provenance:

`manifests/final_test_2025/landsat_inventory/LANDSAT_INVENTORY.json`

Frozen structure:

- 45 Tier-1 L2SP scenes
- 23 physical overpasses on 23 dates
- 1,096 tracts
- 25,208 exact tract-date keys
- metadata only; target/QA pixels remain unopened

### Static and calendar predictor base

Output:

`data/interim/final_test_2025/predictor_base/predictor_base.parquet`

Frozen structure: 25,208 rows and 20 predictors.

### Daymet source subsets

Authoritative provenance:

`manifests/final_test_2025/daymet_grid/DAYMET_GRID.json`

Completed:

- 6/6 official Daymet V4 R1 variables
- 14,131,863 total bytes
- exact 80 × 64 native-grid subset
- 161 required weather dates
- no target-day or future weather

### Daymet final-test features

Status: complete and independently audited.

Outputs:

- `data/interim/final_test_2025/daymet_features/daymet_features.parquet`
- `data/interim/final_test_2025/daymet_features/daymet_feature_audit.parquet`
- `manifests/final_test_2025/daymet_features/DAYMET_FEATURES.json`

Authenticated facts:

- 25,208 rows = 23 dates × 1,096 tracts
- 21 features
- zero missing and zero infinite values
- all windows end on target day minus one
- feature Parquet SHA-256:
  `79275b1f1494249e34dad8400cfc3690d8651743fea77a8f30c7b19c70971d8f`
- audit Parquet SHA-256:
  `068208536e205ebaafdc3108520fd996247a3b3e016287d5cd7eabe79438bcb8`
- provenance SHA-256:
  `64731546e57dba13212e77a85969c4e9370d83340ce826e24bb17a0b5afc22f9`
- provenance commit:
  `5615c5a304e22636d7426cc12da5c3f361b631ecddc181cc0f9244697b348433`

### Canonical Sentinel Collection 1 metadata inventory

Status: complete and independently audited.

Collection: `sentinel-2-c1-l2a`

Authoritative provenance:

`manifests/final_test_2025/sentinel_inventory/FINAL_TEST_SENTINEL_INVENTORY.json`

Authenticated facts:

- 36 physical acquisitions
- 72 selected tile items
- 207 exact target-window memberships
- every acquisition is an `11SLT + 11SLU` mosaic
- 72 exact raw STAC snapshots
- no global scene-cloud cutoff
- raw DN decoding is `reflectance = DN × 0.0001 − 0.1`, exactly once
- C1 B04 equals an independent native-DN reference pixel-for-pixel
- the prohibited legacy negative control is exactly 1,000 DN lower
- semantic SHA-256:
  `8378e479094ad691a64bdb023b066f58bed86a1f19a6ea8b805799a5e17d1fe0`
- outer provenance SHA-256:
  `14ec6f28a5840c09000fb60a31fb3504b2dc660e4788bfb25311f81fd96feade`
- provenance commit:
  `ceb8fac5060e365c871a34929ec9847824911ed0a18fdc92d33da056d3d16c9f`

A PySTAC storage-extension migration warning was audited and is non-material.
The pipeline does not consume that extension, and the warning changes no
selected URL, DN value, scale, offset, collection, or calibration contract.

## Live runtime and latest completed computation

Task: build the valid 2025 C1 Sentinel lagged optical features.

Started: 2026-07-26 08:29:44 Asia/Shanghai

Dashboard URL: `http://127.0.0.1:8768/`

Dashboard PID at start: `14468`

Managed engine PID at start: `18668`

Workers: 6

Expected total: 36 physical acquisitions

Required algorithm:
`final-test-sentinel-features-v2-c1-native-dn`

Completed: 2026-07-26 09:03:59 Asia/Shanghai

Snapshot at 2026-07-26 09:32 Asia/Shanghai:

- state: `complete`
- completed: `36 / 36`
- running: `0`
- failed: `0`
- managed engine: stopped cleanly
- desired running state: `false`
- automatic restarts: `0`
- contract error: none
- one transient `WarpOperationError` affected the 2025-03-15 acquisition on
  attempt 1; the isolated acquisition succeeded on attempt 2 without human
  intervention

The dashboard may remain available for read-only inspection on this same
computer only. Its loopback API is:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8768/api/status"
```

If the API responds on this computer, do not launch a second dashboard or
engine. A different computer cannot reach this loopback service; that does not
mean it should start another process against the canonical output directory.
The completed build must not be restarted merely to reproduce the UI state.

If the API does not respond, first verify that no process owns port 8768 and
that no engine is already writing the canonical state directory. Only then
restart the dashboard:

```powershell
.\.venv\Scripts\python scripts\run_final_test_predictor_dashboard.py `
  --workers 6 --host 127.0.0.1 --port 8768 --no-browser
```

That command is foreground/blocking and should run in a dedicated terminal.
For a hidden local launch:

```powershell
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "scripts\run_final_test_predictor_dashboard.py", `
    "--workers","6","--host","127.0.0.1","--port","8768","--no-browser" `
  -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru
```

The dashboard does not automatically start computation after a fresh launch.
Do not click Start/Continue for this completed build unless a future audited
code change explicitly invalidates it.

Pause is cooperative. Active acquisitions may finish before the engine stops.
The supervisor automatically restarts unexpected process exits with backoff.
A failed individual acquisition remains retryable and does not authorize use
of partial aggregate outputs.

Canonical completed output directory:

`data/interim/final_test_2025/sentinel/`

Completed files:

- `status.json`
- `build_progress.json`
- `pipeline_fingerprint.json`
- `acquisition_tract.parquet`
- `sentinel_features.parquet`
- `sentinel_feature_audit.parquet`
- `sentinel_lineage.parquet`

Completed aggregate facts:

- acquisition-tract rows: `39,456 = 36 × 1,096`
- feature and audit rows: `25,208`
- feature-available rows: `24,633`; all-five-feature-missing rows: `575`
- lineage rows: `226,872 = 207 × 1,096`
- target dates: `23`; tracts: `1,096`
- pipeline SHA-256:
  `b25505861742768d17d4f576f124e4d2ae59b6cba6e52beac4e8cb6bee4b6178`
- `acquisition_tract.parquet` SHA-256:
  `46185f05149a5127e2552c830e19ad57c6f6a5bc3716bc7f78dd21dc4d22afb8`
- `sentinel_features.parquet` SHA-256:
  `13a048bd344087c1ebc165834f5b98bcf0074b62151d1a18493375ba336d8a36`
- `sentinel_feature_audit.parquet` SHA-256:
  `01414f6019b5d5da51f148e1e3991182f70efdaff6fe13df07b3054dc8f73f40`
- `sentinel_lineage.parquet` SHA-256:
  `7fe10c0dd06dbcc5c5ea975ab1fbbf974fb94b52e77391e3a04466ba721bf1c4`

The formal audit passed and published:

`manifests/final_test_2025/sentinel_features/SENTINEL_FEATURE_AUDIT.json`

- state: `passed`
- target blind: `true`
- safe for final predictor assembly: `true`
- authenticated input files: `178`
- calibration: `c1_calibration_consistent`
- audit file SHA-256:
  `8a19fe7b07be6caaba53798364765d3ba0c4cca89b64f2d83becfe6331a2169e`
- internal canonical commit:
  `412c818b12b31efd8203f4fe4a12b6404c948bf8e18b933bee3fb051d2198434`
- audit-pipeline SHA-256:
  `c5bf84ef1892a4a81133b06726774014cf38b50dc55a73f978961cfcb01363aa`

Predictor assembly is permitted only while this marker and every authenticated
input still match exactly.

## Invalid and superseded artifacts: never use

These paths are audit evidence only:

### Incorrect physical-overpass grouping

`data/interim/superseded/sentinel_inventory_67_20260723/`

This draft incorrectly treated adjacent tiles as separate acquisitions.

### Legacy Sentinel inventory with unsafe encoding

`data/interim/superseded/sentinel_inventory_legacy_double_offset_20260723/`

This is the legacy 34-acquisition / 67-item / 192-membership inventory. It
must never satisfy a canonical feature-build dependency.

### Completed but scientifically invalid optical features

`data/interim/superseded/sentinel_features_double_offset_20260723/`

The legacy COG had already incorporated the BOA offset, but the old run
subtracted `0.1` again. All 34 caches and aggregate outputs are invalid.
NDVI, NDWI, EVI, NDBI, and albedo are nonlinear, so the aggregate result
cannot be repaired. The required C1 recomputation is now complete and audited;
the legacy files remain permanently prohibited.

Never copy any file from these directories into a canonical output path.

## Quarantined evaluator drafts: do not run or commit

The following untracked files came from an incomplete evaluator design review:

- `configs/final_test_2025.toml`
- `scripts/run_final_test_evaluation.py`
- `src/la_heat/final_test_evaluator.py`
- `tests/test_final_test_evaluator.py`

They are not part of checkpoint `1dbb023`, are not approved, and must not be
staged, run, or treated as a final-evaluation implementation. Preserve them
unless a user explicitly authorizes their deletion. A later contributor must
redesign and audit the predict-only evaluator after the predictor matrix is
frozen.

## Exact next steps

### 1. Build the frozen 46-feature final predictor matrix

The assembler code is committed and now imports the authoritative Sentinel
expected acquisition count instead of hard-coding the old value.

The Sentinel audit gate has passed. Run:

```powershell
.\.venv\Scripts\python scripts\build_final_test_predictors.py
```

Expected result:

- 25,208 exact rows
- 46 model features in frozen order
- 20 base + 21 Daymet + 5 Sentinel
- output:
  `data/processed/final_test_2025/predictors/final_predictors.parquet`
- provenance:
  `manifests/final_test_2025/predictors/PREDICTOR_ASSEMBLY.json`
- no target/QA/model/score path opened

Audit and record the output hashes before proceeding.

### 2. Audit and freeze the predictor matrix

Authenticate the exact row-key set, 46-feature order, source hashes, missing
patterns, and target-blind flags. Update `docs/DATA_MANIFEST.csv`,
`docs/DECISION_LOG.md`, and this handoff, run the full checks, and commit/push
only the allowlisted predictor artifacts and documentation.

### 3. Redesign and freeze the independent final evaluator

Do not use the quarantined drafts. The accepted evaluator must:

- be predict-only and never fit or tune;
- authenticate both frozen B1/M2 models and the exact 46-feature matrix;
- keep target construction separate from predictor preparation;
- prevent repeated authorization or evaluation;
- emit predictions and metrics atomically with complete provenance;
- forbid threshold or model changes after any 2025 target value is opened.

### 4. One-time authorization and final evaluation

This is intentionally not authorized yet.

Authorization may occur only after:

- C1 Sentinel outputs and final predictors are frozen and committed;
- the evaluator/config/tests are committed and independently audited;
- the worktree is clean;
- full tests and Ruff pass;
- the final target builder and all output paths are frozen;
- the user explicitly approves the irreversible one-time step.

After authorization, run the final evaluation exactly once. Never retune on
2025 results.

## Verification commands

Run from the repository root:

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git fetch origin
git rev-parse origin/main
git remote -v
```

Required branch for the current linear workflow: `main`.

Before completing any code change:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
```

Latest verified results through checkpoint `1dbb023`:

- audit/authorization/assembler focused suite: `37 passed`
- full test suite after the audit-gate implementation: passed, zero failures
- full Ruff after the audit-gate implementation: passed

Generated manifests under `manifests/**` are marked `-text` in
`.gitattributes` because byte-level hashes are authoritative. On Windows,
`git diff --check` may report their intentional CRLF bytes as trailing
whitespace. Do not rewrite a frozen manifest merely to remove that warning.

## Multi-contributor coordination

- Never let two contributors mutate the same working tree concurrently.
- For parallel code review, use separate clones or worktrees and branches.
- Large canonical data under `data/**` are intentionally untracked. A Git clone
  or worktree alone cannot run data audit or assembly. Those steps must have
  exactly one writer in this canonical workspace. Another machine may take over
  only after copying the required data/manifests and authenticating every hash
  recorded here.
- Assign one owner per file group and one bounded task per branch.
- Do not run two processes against the same canonical output directory.
- Before starting, fetch the remote, read this file, inspect Git status, and
  query any live local service.
- Before pushing, rebase or merge deliberately, rerun affected tests, inspect
  the exact staged file list, and exclude quarantined drafts.
- After pushing, update this document with the new checkpoint and next command.
- If a prior contributor left uncommitted changes, treat them as user-owned.
  Do not discard, overwrite, reset, or clean them without explicit authority.

## Definition of a completed pipeline step

A step is complete only when:

1. the documented command runs successfully;
2. the output is structurally and scientifically audited;
3. provenance and hashes authenticate;
4. relevant targeted tests pass;
5. full `pytest` and Ruff pass before handoff;
6. `docs/DATA_MANIFEST.csv` and `docs/DECISION_LOG.md` are updated;
7. this handoff is updated;
8. valid code/manifests/docs are committed and pushed.

“The process ended” or “the UI says 100%” alone is never sufficient.
