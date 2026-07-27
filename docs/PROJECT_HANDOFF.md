# Mandatory project handoff

Last material update: 2026-07-27 Asia/Shanghai

Latest required scientific checkpoints on `main`:

- frozen target-blind predictor baseline:
  `e0fb4878ac6ecec53a44a0fe027ca46c5a9d2196`
- locked final-evaluation implementation and this handoff are in the commit
  with subject `Add locked final evaluation protocol`; obtain its exact hash
  with
  `git log --oneline --grep="Add locked final evaluation protocol" -1`
- earlier audit/authorization/assembler checkpoint:
  `1dbb0232c9c278118875a6c4eb9af2c6e8e29720`

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

## Current safe state

Branch: `main`.

The project is still target-blind and locked:

- `configs/research.toml`: `unlock_final_test = false`
- MODEL_LOCK: `final_test_locked = true`
- MODEL_LOCK: `final_test_values_read = false`
- MODEL_LOCK: `one_time_final_evaluation_authorized = false`

These six canonical state markers do not exist:

- `manifests/final_test_2025/evaluation/EVALUATION_READINESS.json`
- `manifests/final_test_2025/AUTHORIZATION.json`
- `manifests/final_test_2025/evaluation/CONSUMPTION_CLAIM.json`
- `manifests/final_test_2025/evaluation/PREDICTIONS_FROZEN.json`
- `manifests/final_test_2025/evaluation/VALUES_OPENED.json`
- `manifests/final_test_2025/evaluation/EVALUATION_COMPLETE.json`

These canonical runtime/output directories also do not exist:

- `data/processed/final_test_2025/.final_evaluation.staging`
- `data/interim/final_test_2025/evaluation/target_cache`
- `data/processed/final_test_2025/final_evaluation`

No canonical 2025 Landsat thermal value, QA value, target table, residual,
score, metric, or final figure has been opened or produced. Synthetic unit
tests and target-blind in-memory prediction dry runs are not final-test value
access and do not consume the evaluation.

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
residual, score, or metric has been read or produced. A target-blind interface
dry run produced 25,208 finite B1 predictions and 25,208 finite M2 predictions
in memory only; it wrote no prediction artifact and did not consume the final
evaluation.

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

## Completed frozen 2025 predictor matrix

Command:

```powershell
.\.venv\Scripts\python scripts\build_final_test_predictors.py
```

The command completed and a second invocation authenticated the existing
result without rebuilding it.

Canonical output:

`data/processed/final_test_2025/predictors/final_predictors.parquet`

Tracked external provenance:

`manifests/final_test_2025/predictors/PREDICTOR_ASSEMBLY.json`

Audited facts:

- state: `complete_target_blind`
- rows: `25,208`; dates: `23`; tracts: `1,096`
- columns: two keys plus `46` features in frozen order
- family counts: `20` static/calendar + `21` Daymet + `5` Sentinel
- unique keys: yes; 11-digit GEOIDs are keys, not predictors
- non-Sentinel missing values: `0`
- Sentinel available rows: `24,633`
- Sentinel all-five-missing rows: `575`; partial missing rows: `0`
- infinite numeric values: `0`
- target/QA tables or values read: none
- models/scores/predictions read: none
- one-time evaluation consumed: `false`
- predictor file SHA-256:
  `f02b3428a1070b1d95152bb225652bc063330b3793322aeb364e8a0dd267fa0a`
- predictor schema SHA-256:
  `d3c34680721b3852c0d8fbf1679743befeb619b565d3d35b7822d0cf01aa0a16`
- semantic predictor SHA-256:
  `793af3c05589532887d436bbccaa0e415bc08e5106af447004f6444f766f27d0`
- semantic key SHA-256:
  `cd6ddcd4800e6d41b7f4b89d6be007be80e509dd6567dfaf416cfb6903e3b870`
- missingness CSV SHA-256:
  `63eb4710a01bb5f9b61ddb689fc98a4f49cfce87e683fddce808fe38bc0d7b34`
- external provenance file SHA-256:
  `4f94bd00ba5e7a6cdbd08abdb14483ea2018a5429406db3bd2f0549470f74541`
- provenance canonical commit:
  `b8ce453ecd2c4c067251d0f84bdf647b59cef9c10cbc57820286b6cb4c838d3f`

An independent read-only audit reconstructed the exact one-to-one merge from
the 20 base, 21 Daymet, and five Sentinel source columns and found every value
equal with NaN-aware comparison. It independently matched all 46 columns to the
formal M2 lock and reauthenticated the full existing-output chain.

The large Parquet and internal provenance remain intentionally untracked under
`data/processed/**`; their hashes above are the transfer/authentication
contract.

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

## Quarantined evaluator drafts: relocated outside the repository

The following files came from an incomplete evaluator design review:

- `configs/final_test_2025.toml`
- `scripts/run_final_test_evaluation.py`
- `src/la_heat/final_test_evaluator.py`
- `tests/test_final_test_evaluator.py`

They are not part of the approved workflow and must not be read, staged, run,
or treated as a final-evaluation implementation. On 2026-07-27 the user
explicitly authorized the previously described safe handling step. The four
files were moved, without opening or executing them, to the recoverable
external quarantine directory:

`D:\HuaweiMoveData\Users\haora\Documents\ISEF_QUARANTINED_DRAFTS_20260727`

Their relative paths, byte counts, and pre/post-move SHA-256 values are:

- `configs/final_test_2025.toml`: 1,004 bytes,
  `15a483d863bc6736e10c3e884869dd86731c2a174a9c2cabac076c261d30c103`
- `scripts/run_final_test_evaluation.py`: 1,314 bytes,
  `e109f945a17d0819bc83b8a1a586a9c30936fe89769f5caf301ec8c04384b1a7`
- `src/la_heat/final_test_evaluator.py`: 39,062 bytes,
  `380ef5f83181ec216a1221a7312a858a86a62ac3dcef0bdb49462ed78737fc5f`
- `tests/test_final_test_evaluator.py`: 13,812 bytes,
  `742fe7ad1bfe61fd3a8cd241aa628ab1e94ff89840a479cf27b1f6d8f06b9b33`

The four source paths no longer exist in the repository and the worktree was
clean immediately after relocation. Authorization A is satisfied. Never copy
these drafts back into the repository or use `git add .`.

## Exact next steps

### Independent final evaluator implemented and audited

The new, independent protocol uses names that do not overlap the four
quarantined drafts. Its frozen configuration is:

`configs/final_evaluation_2025.toml`

New implementation files:

- `src/la_heat/final_evaluation_protocol.py`
- `src/la_heat/final_evaluation_targets.py`
- `src/la_heat/final_evaluation_reporting.py`
- `scripts/prepare_final_evaluation.py`
- `scripts/execute_locked_final_evaluation.py`
- `tests/test_final_evaluation_protocol.py`
- `tests/test_final_evaluation_targets.py`
- `tests/test_final_evaluation_reporting.py`
- `src/la_heat/final_test_authorization.py`
- `scripts/authorize_final_test_2025.py`
- `tests/test_final_test_authorization.py`
- `docs/DECISION_LOG.md`
- `docs/PROJECT_HANDOFF.md`

The authorization module and script now require and bind the readiness marker.
No readiness, authorization, claim, values-opened, completion, target, QA,
residual, score, or metric artifact exists. The new protocol freezes:

- exact B1/M2, predictor, Landsat inventory, target-grid/QA, and feature locks;
- a target-blind, no-clobber readiness marker generated while the research
  lock is still false and reauthenticated during authorization;
- append-only authorization, consumption-claim, values-opened, frozen-
  prediction, and completion markers;
- predictions frozen before the first target/QA asset is opened;
- same-claim crash recovery but no second authorization or second claim;
- all 23 inventory dates assessed by the unchanged per-date QA rules, without
  reusing the development-only minimum of 30 dates;
- exact output paths, metrics, crossed date/spatial-block bootstrap, hotspot
  rules, and planned figures before any 2025 value is read.
- deterministic B1/M2 replay from the locked predictors and fitted models
  immediately before the value-opening boundary and again before publication;
- the value marker before any remote asset or cached target bytes are read;
- independent primitive QA/count/fraction/date/tract/hotspot recomputation,
  fixed-grid/eligible-pixel identities, and scene-to-overpass-date lineage;
- exact regular-file output set, predeclared columns, canonical civil dates,
  11-digit GEOIDs, primary keys, semantic hashes, cross-table key/cardinality
  checks, and same-claim committed-staging recovery;
- deep publication/recovery authentication that reauthenticates the inventory,
  predictor provenance, model feature contract, research unlock, target
  artifacts, date/tract QA, and scene contributions, then reconstructs the
  canonical evaluation rows from source tables and compares them exactly;
- deterministic replay of model, per-date, paired-cell, bootstrap, gate,
  hotspot, sensor, and Sentinel-stratum reports from published evaluation rows;
- a real authenticated-tract six-panel map PDF plus
  `tract_choropleth_summary.csv`; zero-support tracts retain support count and
  fraction zero, and the figure binds the claim's tract file, manifest, CRS,
  and source-table semantics. Deep recovery regenerates all three figures and
  requires byte-identical PDF/PNG output.

Target-blind validation completed without opening any Landsat asset:

- authenticated frozen predictor rows/dates: `25,208 / 23`
- authenticated Landsat metadata: `45 scenes / 23 overpasses / 25,208 keys`
- inventory reports `target_or_qa_values_read = false`
- authenticated B1 and M2 artifacts each produced `25,208` finite in-memory
  predictions; no predictions were persisted
- evaluator/target/reporting suite after source-binding hardening:
  `44 passed`
- final full project suite: `711 passed` in `214.87 s`
- full-repository Ruff: passed

The implementation is committed under the subject
`Add locked final evaluation protocol`; use the checkpoint command at the top
to obtain its non-circular hash. The four old drafts are recoverably
quarantined outside the repository as recorded above.

### 1. Current preparation state

Run only these read-only commands first:

```powershell
git rev-parse HEAD
git status --short
```

The expected status is now completely empty. If it is not empty, fail closed
and investigate without deleting anything.

The first readiness attempt on 2026-07-27 stopped before creating any marker:
the copied
`data/interim/final_test_2025/daymet_features` directory retained a protected
ACL owned by the other laptop account. Inheritance was enabled only on that
exact directory; no data bytes were edited. The three recovered files now
match the frozen predictor manifest exactly:

- `DAYMET_FEATURES.json`: 21,049 bytes,
  `64731546e57dba13212e77a85969c4e9370d83340ce826e24bb17a0b5afc22f9`
- `daymet_features.parquet`: 4,019,117 bytes,
  `79275b1f1494249e34dad8400cfc3690d8651743fea77a8f30c7b19c70971d8f`
- `daymet_feature_audit.parquet`: 46,324 bytes,
  `068208536e205ebaafdc3108520fd996247a3b3e016287d5cd7eabe79438bcb8`

The original Python 3.14.4 installation used by `.venv` was no longer present.
The ignored local `.venv/pyvenv.cfg` was repointed to the exact portable
Python 3.14.4 runtime already preserved under
`exports/FINAL_RESULT/runtime/python`. Imports authenticate the same frozen
package versions, including NumPy 2.5.1, pandas 3.0.3, GeoPandas 1.1.4,
rasterio 1.5.0, scikit-learn 1.9.0, PyArrow 25.0.0, and Matplotlib 3.11.1.

After the source-binding commit, a second readiness attempt also stopped
before creating a marker because
`data/processed/final_test_2025/predictors` retained the same protected
other-laptop ACL. Inheritance was enabled only on that exact directory; no
data bytes were edited. The recovered predictor Parquet is 4,945,661 bytes
with SHA-256
`f02b3428a1070b1d95152bb225652bc063330b3793322aeb364e8a0dd267fa0a`,
and the internal predictor provenance is 71,020 bytes with SHA-256
`4f94bd00ba5e7a6cdbd08abdb14483ea2018a5429406db3bd2f0549470f74541`;
both exactly match the frozen evaluation configuration.

A read-only target-blind authentication then succeeded for the formal model
lock, the 25,208-row predictor surface, and the 45-scene/23-overpass/25,208-key
Landsat metadata inventory. The inventory again reported
`target_or_qa_values_read = false`.

The first safe state-changing command, after this handoff correction and the
final source-binding hardening are committed and pushed to a clean `main`, is
the target-blind readiness command in step 1 below.

### 2. One-time authorization and final evaluation

On 2026-07-27, after the two distinct authorizations and the irreversible
one-time boundary were explained, the user instructed the project to perform
the described sequence. Authorization A (draft relocation) and Authorization
B (one one-time 2025 evaluation) are therefore explicit for this run. No
authorization marker, unlock, claim, target read, prediction file, metric, or
figure exists at this handoff checkpoint.

Authorization may occur only after:

- C1 Sentinel outputs and final predictors are frozen and committed;
- the evaluator/config/tests are committed and independently audited;
- the worktree is clean;
- full tests and Ruff pass;
- the final target builder and all output paths are frozen;
- the user explicitly approves the irreversible one-time step.

That approval is authorization B and is separate from authorization A. It has
now been granted once for the frozen protocol. It does not authorize any
second claim, rerun with changed code/settings, or post-result tuning.

The locked sequence is:

1. while `configs/research.toml` still has `unlock_final_test = false`, run:

   ```powershell
   .\.venv\Scripts\python scripts\prepare_final_evaluation.py `
     --config configs\final_evaluation_2025.toml
   ```

2. run the preflight and, only under authorization B, the explicit one-time
   authorization:

   ```powershell
   .\.venv\Scripts\python scripts\authorize_final_test_2025.py --preflight-only
   .\.venv\Scripts\python scripts\authorize_final_test_2025.py `
     --approve-one-time-2025
   ```

3. commit the separately documented `false -> true` research unlock and its
   decision-log entry, changing no evaluator/scientific setting;
4. execute exactly once or resume the same append-only claim after a crash:

   ```powershell
   .\.venv\Scripts\python scripts\execute_locked_final_evaluation.py `
     --config configs\final_evaluation_2025.toml
   ```

The commands above are now the authorized gated sequence. Every gate must
still pass exactly; a failure must remain fail-closed.

Never retune or change thresholds after target values are opened.

### 3. Preserve the test-set result as auditable evidence

The canonical evidence is not a screenshot. Preserve the append-only chain:

1. target-blind readiness and explicit authorization;
2. the one consumption claim and frozen blind-prediction commitment;
3. the values-opened boundary marker;
4. all 23 per-overpass target-cache commits;
5. the exact 21-file final output directory and its
   `EVALUATION_COMMIT.json`;
6. `EVALUATION_COMPLETE.json`, the Git commits, decision log, handoff, and
   final checksums.

After completion, rerun the same execution command once in authentication
mode. It must authenticate the completed transaction without opening target
tables. Only after that succeeds may metrics and figures be inspected. Then
create a separate, read-only export that copies existing evidence bytes and
writes a SHA-256 manifest; do not recalculate or hand-edit any result. The
repository has no approved final-evaluation ZIP exporter yet, so the old
portable development-run packagers must not be reused.

The official science-fair rules do not require a project data book or research
paper, but strongly recommend them for judging. The official judging criteria
also score systematic data collection, reproducibility, and supporting
documentation. This append-only evidence chain is the project's electronic
data-book record for the held-out test evaluation. Official references:

- `https://www.societyforscience.org/isef/international-rules/rules-for-all-projects/`
- `https://www.societyforscience.org/isef/grand-award/criteria/`

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

Latest verified results during evaluator implementation:

- evaluator/target/reporting focused suite:
  `44 passed`, zero failures
- full project suite: `711 passed`, zero failures (`214.87 s`)
- full-repository Ruff: passed
- no canonical readiness, authorization, claim, predictions-frozen,
  values-opened, completion, target-cache, staging, or final-output artifact
  existed during validation

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
