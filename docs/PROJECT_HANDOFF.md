# Project handoff

Last updated: 2026-09-03 Asia/Shanghai

## Project summary

This repository contains a completed Los Angeles neighborhood-scale historical
land-surface-temperature study and its public atlas. The continuation asks
whether the same public weather, land-use, geography, and lagged Sentinel-2
predictors transfer to Phoenix, Houston, and Chicago without using those
cities' target values during development.

Canonical repository: `https://github.com/CmsChase/LA-neighborhood-heat`

Public atlas: `https://cmschase.github.io/LA-neighborhood-heat/`

The website source is `atlas/`. The old standalone atlas repository is archived.

## Completed milestones

1. The Los Angeles development study, frozen 2025 evaluation, evidence package,
   reports, and interactive atlas are complete.
2. Public Census geography and WorldCover eligible-land support are complete for
   Los Angeles, Phoenix, Houston, and Chicago.
3. Real Sentinel-2 calibration smoke evidence is complete for all three external
   native UTM zones: Phoenix 12, Houston 15, and Chicago 16.
4. All 15 missing-support/calibration evidence outputs are tracked and
   authenticated. The terminal internal commit is
   `1251247b821093a3208b5dc0da65b5e2ae2969e14030da83666fbd65e45e6d07`.
5. Phoenix public source metadata was refreshed against the new canonical city
   boundary. It found 3 Landsat units, 4 Sentinel units, 1,462 Daymet cells, and
   2 terrain tiles without opening raster or target values.
6. The final portable predictor contract is locked in
   `manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json`.
7. The target-blind predictor key inventory is frozen: Los Angeles has 90
   dates, Phoenix 22, Houston 21, and Chicago 21. Across 2,902 city-specific
   tracts this yields 136,941 predictor rows without reading target assets.
8. A stable, target-blind component runner and localhost progress page are
   implemented and tested. Its 84 durable work units cover four calendar
   tables, four static bases, 49 GSHHG distance chunks, four static finalizers,
   Los Angeles Daymet compilation, 18 external-city Daymet downloads, three
   external-city compilations, and one final component merge.
9. That 84-unit build is complete. It produced all 41 non-Sentinel predictors
   for 136,941 frozen city-date-tract rows. The merged Parquet has 46 columns:
   five keys plus the 41 frozen static, calendar, and Daymet features. No model
   was fit and no external-city target values were opened.
10. The four-city Sentinel metadata inventory is frozen and authenticated:
    Los Angeles has 226 physical acquisitions, Phoenix 116, Houston 113, and
    Chicago 56 (511 total). Houston's UTM-14 zero-support candidates remain in
    audit lineage but are not raster contributors.
11. The resumable Sentinel engine and localhost dashboard are implemented and
    tested. The dashboard exposes 6/8 asset-read threads separately from 1/2
    complete-acquisition concurrency, defaults to 6/1, pauses only after a
    durable acquisition boundary, retries failed acquisitions, and restarts an
    unexpectedly exited engine.
12. A copy-ready gaming-laptop folder is staged at
    `exports/GAMING_LAPTOP_SENTINEL`. Its 3,410 manifest-tracked files total
    370,521,440 bytes. Independent verification found zero missing, extra, or
    hash-mismatched files. The manifest SHA-256 is
    `41819b6054b10a708e1644349ef31e361fd6d2db89b0ef26599c1a8cfafea449`.
    Package-local engine check returned `ready`; dashboard smoke returned
    `paused` with no engine process. The complete Windows PowerShell first-run
    path also passed after the quoting repair.
13. The gaming-laptop Sentinel run completed all `516 / 516` durable units:
    511 physical acquisitions, four city compiles, and one final merge. The
    copied return ZIP passed a complete CRC audit and was imported into this
    primary project without rerunning acquisitions.
14. The formal return receipt is
    `manifests/multicity/returns/PORTABLE_SENTINEL_RETURN.json`. The completed
    predictor table contains 136,941 rows and 46 features; its SHA-256 is
    `31b472b53f11a69c8a2d44dfc927ed46162db0c076ef644693233cea4e026b0f`.
    The target-blind readiness audit is `ready_for_protocol_lock_not_model_fit`
    with 73,432 LA training rows, 25,208 LA calibration rows, and 38,301 sealed
    external-city prediction rows.
15. The continuation-specific target-blind 5 km spatial partition is complete
    in EPSG:5070 for all 2,902 tracts: Los Angeles has 71 blocks, Phoenix 59,
    Houston 88, and Chicago 36 (254 globally unique city-prefixed blocks).
    Although Los Angeles again has 71 blocks, this is not the Phase-I EPSG:3310
    partition and no Phase-I block assignment is reused. The committed control
    record is `manifests/multicity/evaluation/SPATIAL_BLOCKS.json`.
16. The fixed transfer-model core is implemented and synthetic-tested. It
    builds the exact 23-feature B1 diagnostic, 46-feature point M2, and q05/q95
    M2 pipelines; enforces LA 2020-2023 training, LA 2024 calibration, and the
    complete three-city 2025 external prediction cohort; and implements the
    frozen equal-date weighting, CQR correction, and strict-greater-than
    abstention rule. No real target was read and no real model was fit.
17. The target-blind four-city aggregation contexts are complete. They attach
    the frozen 5 km blocks to all 2,902 tracts in canonical raster-zone order
    and authenticate each city's 30 m target grid, geography, WorldCover mask,
    and block assignment. The control record is
    `manifests/multicity/targets/TARGET_CONTEXTS.json`. This step opened no
    Landsat asset href, thermal band, QA band, or target table.
18. The still-unauthorized target build plan is complete and reproducible. It
    contains 159 durable units: 154 overpass targets, four city compiles, and
    one final merge. Los Angeles 2020-2024 is a separate 90-date source lane;
    Phoenix, Houston, and Chicago form one indivisible 64-date external 2025
    cohort that requires one later append-only claim. The plan freezes only
    item IDs and metadata relationships; all asset hydration and target-value
    access remain unauthorized. Its record is
    `manifests/multicity/targets/TARGET_BUILD_PLAN.json`.
19. The complete four-city software path now has a deterministic in-memory
    rehearsal. It exercises the frozen LA training/calibration interface,
    target-blind three-city prediction, mechanical leave-one-city-out folds,
    evaluation tables, uncertainty diagnostics, and a figure. Every artifact
    is explicitly synthetic and non-evidence, and project-local output is
    restricted to `.tmp/`.
20. The public Atlas includes a static `/cities/` route. It began as a sealed
    null-result preview and now publishes only the authenticated aggregate
    evaluation, gate outcomes, and six evidence figures. It explicitly labels
    the scientific outcome `inconclusive_sample_size` and distinguishes evidence
    authentication from successful confirmation.
21. The four-city pre-fit protocol/model specification is append-only locked in
    `manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json`, commit
    `c93cee9d7d05194dd75fe8dba662ae1d5b9ee2a8e1401178a1a9c0fc8675304f`.
    It freezes cohorts, exact B1/M2 feature order and parameters, LA-2024 CQR,
    the equal-city/equal-date primary metric, all success and reliability gates,
    the 10,000-replicate crossed bootstrap, output schema, planned figures, and
    code/input identities. This lock did not read any target value or fit a model.
22. The separately authorized Los Angeles lane completed all 90 overpasses and
    one compile, yielding 98,640 source tract-date keys. Frozen B1/M2/CQR fitting
    then committed 38,301 predictor-only external predictions without opening
    external targets. The indivisible Phoenix-Houston-Chicago claim subsequently
    completed all 64 overpasses and three city compiles; the frozen evaluator,
    report, post-hoc QA audit, and public Atlas release all authenticated. No
    target dashboard or worker is currently required.
23. The follow-up M3 experiment's target-blind, label-free city feasibility
    audit is complete and authenticated. Seattle, Denver, Atlanta, and Miami
    all passed, so no replacement city was used. The audit found 177/175/173/128
    primary Census tracts and 54/31/28/30 eligible independent 2025 Landsat
    dates, respectively; every selected tract had positive WorldCover valid
    non-water support. The terminal record is
    `manifests/multicity/next_experiment/METADATA_FEASIBILITY_AUDIT.json`, commit
    `450ecd604000fcec7f3958e9a15013c74f69c52fddd656e9786c703c62838922`.
    It read no new-city Landsat asset href, thermal or QA value, target table,
    model, prediction, or evaluation metric.
24. The follow-up experiment's append-only M3 development protocol is locked in
    `manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json`,
    commit `dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8`.
    It freezes the four source and four blind-test cities, B1/M2-L and four M3
    candidates, pixel-level ST_QA candidate rules, nested whole-city LOSO,
    uncertainty and abstention selection, evaluation gates, and the 21-column
    prediction schema. The candidate space is locked, but no winning M3 model
    has been selected, no source-only selection has run, and no new value was read.
25. A pre-access source-acquisition amendment proved that the old source support
    could not satisfy the locked eight-date inner-city gate, then fixed a complete
    2020-2025 warm-season expansion for Houston and Chicago without selecting on
    QA outcomes. The assets-excluded public metadata inventory is complete: 318
    source overpasses and 525 unique city-scenes (Los Angeles 90/177, Phoenix
    22/44, Houston 102/200, Chicago 104/104). The two-phase cache/offline-QA
    authorization is authenticated, and its 3,474-task durable queue is initialized
    and paused. No Landsat asset href or pixel was opened by these preparation
    steps; the four blind-test cities remain sealed.
26. The source-only QA rebuild and support gate completed successfully. The
    gaming-laptop predictor extension was returned and authenticated in the
    primary project: four source cities, 253,632 tract-date keys, and exactly
    46 predictors. The next-stage executable source-only UQ/risk pseudo-test
    path passed 34 focused tests and lint. Independent append-only joint LOSO
    authorization commit
    `358c8f63f65932c1ac17914b95015ae59194c2a42adc0e40e765016d2b68d773`
    was created without opening a predictor or QA Parquet. It permits only the
    frozen 4 QA × 4 M3 nested whole-city LOSO and source-city pseudo-tests;
    Seattle, Denver, Atlanta, and Miami remain sealed.
27. The source-only nested whole-city LOSO, UQ, and risk stages completed and
    authenticated under commit
    `207d45f8fdc7237f6347ed69b1c67733df353a3331e622707e93c4b3f21c34d3`.
    The frozen selection is QA `4k`, M3
    `level_ridge_alpha_10__anomaly_hgb_leaves_31`, unweighted cross-conformal
    UQ, and accept-all risk. Metadata-only parent authorization
    `1a704fca3848471dfba16c28bf2dd2e282343af6ac2aa24e3cbbd2ef44d790f8`
    freezes four blind cities, 143 target dates, 23,667 tract-date keys, and the
    exact 46-predictor contract. It permits implementation and review only;
    predictor values and network access remain prohibited until a reviewed
    child runtime authorization is created.
28. The blind-predictor support and public metadata substages are now complete.
    Support commit
    `aa0c35e626e84ab1e8a17e04b8c5da374c3fcd655a601c90b2e8256806d74dbd`
    reconstructs the four canonical grids with zero network access. Metadata
    commit `31dcc3f639ccd8a4af040be20dd5ced243cadf7a913b53639a9e7311b7966201`
    freezes 23,667 predictor keys and assets-excluded Sentinel plus Daymet
    granule metadata. Exact Sentinel inventory commit
    `7052a02df4da25661ea29cb9b5862bd71921ce1e57017a73db87e8f9ca4b10d7`
    selected 539 physical acquisitions (Seattle 150, Denver 157, Atlanta 157,
    Miami 75) after reading only exact-item href metadata. No Sentinel/static
    raster or Landsat/QA/target value was opened. The authorized 24-task Daymet
    acquisition remains 0/24 because NASA returned 401 and no ephemeral
    Earthdata token is present; it is not being retried automatically.
29. The public repository presentation is refreshed without changing the active
    scientific boundary. The README now shows the completed source-only M3
    selection, current blind-predictor stage, and prediction-before-target rule
    in plain language. Cross-platform setup and provenance guides live in
    `docs/REPRODUCING.md` and `docs/PROVENANCE.md`; Python CI runs public code tests
    on Ubuntu and Windows with project-local temporary files. Completed Windows
    launchers are archived under `tools/windows/legacy/`. The historical
    `START_M3_PREDICTOR_GAME_LAPTOP.cmd` remains unchanged at the root because
    its exact path and SHA-256 are bound by an append-only authorization. Ruff
    and the complete pytest suite passed on the original workstation after the
    organization change. Fresh-clone CI then exposed a missing temporary parent,
    shallow Git history, ignored evidence dependencies, a runtime-specific test
    hash, and Rasterio 1.5.1 opener incompatibility. The CI repair uses a direct
    project-local `.tmp/pytest-ci` directory with its parent created first, full
    history, an explicit opt-in local-evidence
    test lane, an invariant-based hash test, and the tested Rasterio 1.5.0 version.
    No scientific implementation, evidence bytes, or permissions were changed.

## Frozen scientific decisions

- For the completed Phoenix-Houston-Chicago experiment, M2 is the only primary
  transfer model and uses all 46 frozen predictors.
- In the follow-up experiment, M3 is a locked candidate family awaiting
  source-only selection; it does not revise the completed M2 result.
- B1 remains a fixed 23-feature weather/calendar benchmark. It is not a
  deployment or model-selection candidate.
- All four cities use the new same-adapter Census 2020 geography and WorldCover
  2020 v100 valid non-water support.
- Los Angeles keeps the same 1,096 GEOIDs, but its new zone assignment differs
  from Phase I in 6,872 cells. Therefore Los Angeles 2020-2024 predictors and
  Landsat target aggregation must be rebuilt on the new support; Phase-I pixel,
  mask, feature, or already aggregated target tables cannot be reused directly.
- WorldCover defines the eligible support mask only; it is not a predictor.
- Sentinel reflectance calibration comes only from official product metadata
  XML using `(DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE`, exactly once.
  Missing STAC `raster:bands` scale/offset fields are not a blocker.
- City ID, tract GEOID, and raw coordinates are keys or metadata, never model
  inputs.

## Current permission boundary

Completed and authenticated:

- authenticate the completed public predictor table and its evidence;
- authenticate the append-only protocol/model specification lock;
- authenticate the completed LA 2020-2024 source-target table;
- authenticate the completed frozen model fit and committed external predictions;
- the one indivisible Phoenix-Houston-Chicago target claim;
- the frozen external evaluation, six-figure evidence package, and verified Atlas release.
- the target-blind four-city feasibility audit and append-only M3 development
  protocol lock.
- the append-only source-acquisition amendment, Houston/Chicago assets-excluded
  metadata expansion, and source-cache/offline-QA execution authorization.

Still prohibited:

- refitting, recalibrating, or selecting models from external outcomes;
- replacing the frozen confirmatory result with a post-hoc sensitivity result;
- claiming successful cross-city confirmation or calibrated uncertainty.
- reading any Seattle, Denver, Atlanta, or Miami Landsat asset href, thermal or
  QA value, or target table;
- running nested LOSO, M3 fitting, or model/QA selection; the current permit
  stops after the four source ST_QA candidate tables are rebuilt;
- building new-city predictors, fitting the final M3, or creating predictions
  until later staged permissions explicitly authorize each step.

The single active control record is
`manifests/multicity/ACTIVE_STAGE.json`. Historical numbered transition modules
through V18 remain for provenance and must not be extended with V19/V20 files.

## Exact resume point

Current resume point (supersedes the historical repair-blocker narrative
retained below): the public-repository organization milestone is complete and
does not advance scientific permissions. Blind support, keys, public metadata, and exact Sentinel
inventories are complete at the commits in milestone 28. The next safe action
is to implement, review, and independently authorize resumable Sentinel and
static predictor-value acquisition. The already-authorized Daymet acquisition
may resume only with an in-memory Earthdata token; do not persist the token or
loop on 401. No blind-city Landsat asset href, thermal/QA value, target table,
fit, prediction, or score is authorized.

Historical pre-return context follows for provenance only.

The public predictor phase is complete: static/calendar/Daymet reached `84 / 84`,
Sentinel reached `516 / 516`, and the final table is 136,941 rows by 46 frozen
features. Do not rebuild or re-import these products.

The exact tracked stage is
`m3_source_development_paused_upstream_asset_integrity_blocker`. The control
page is <http://127.0.0.1:8772/> and the durable runtime status is
`data/interim/multicity/m3_source_development/runtime/status.json`. The queue
contains 3,151 online cache tasks followed by 323 offline QA tasks. Dynamic
progress belongs only in that ignored runtime status, not in tracked documents.
The queue is deliberately paused with no active leases. Exactly three frozen
source assets are blocked because their Planetary Computer blobs contain a
persistent 12,351-byte HTML error payload rather than GeoTIFF bytes. The
append-only repair incident and authorization preserve this fact and permit
only the exact original product files whose official MD5 values match. Do not
resume repeated downloads, substitute a later processing version, or relax the
source inventory. Existing valid cache contents remain intact, and no blind
city was accessed.
Its frozen predecessor remains the completed three-city external evaluation:
the target claim contains 64 overpasses and three city compiles. The
evaluation used 11,207 QA-valid rows across 28 city-dates and 180 spatial
blocks. M2 reduced equal-city/equal-date MAE from 9.738 °C to 6.922 °C, a 28.9%
improvement with a 95% crossed-bootstrap interval of 14.1% to 43.5%.

The compact offline evidence package can be created once, then reauthenticated,
with:

```powershell
.\.venv\Scripts\python scripts\export_multicity_evidence.py --project-root .
.\.venv\Scripts\python scripts\export_multicity_evidence.py --project-root . --check-only
```

Its default location is `exports/MULTICITY_EVALUATION_EVIDENCE.zip`. It contains
only aggregate/date-level evidence, figures, protocol records, interpretation,
and relevant code; it excludes tract-level scored rows and targets, model files,
runtime state, credentials, and signed URLs.

Reauthenticate the protocol and LA-only authorization with:

```powershell
.\.venv\Scripts\python scripts\lock_multicity_evaluation_protocol.py --project-root . --check-only
.\.venv\Scripts\python scripts\authorize_multicity_source_targets.py --project-root . --check-only
```

The preregistered point gate nevertheless failed: only 28 city-dates survived
the unchanged QA rules, Houston and Chicago each had fewer than eight usable
dates, and M2 degraded in Phoenix. The reliability gate also failed: nominal
90% intervals covered only 45.0% overall and abstention increased rather than
reduced accepted-set MAE. Preserve this result. A clearly labeled, read-only
post-hoc quality audit is complete and does not replace the formal result. The
next research step is to design and preregister a separate future experiment;
it must not overwrite or be presented as the completed confirmatory test.

The already completed spatial partition can be reauthenticated without target
or predictor access using:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_spatial_blocks.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_contexts.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_build_plan.py --check-only
```

Do not refit or recalibrate the committed model, rescore the completed external
claim, or reinterpret the post-hoc sensitivity as confirmatory. The prediction
commit preceded the single combined external claim, and all three city compiles
and the one-time evaluation have authenticated.

The selected unseen-city set is Seattle, Denver, Atlanta, and Miami. The
target-blind feasibility audit and the append-only M3 development protocol lock
can be reauthenticated without network access using:

```powershell
.\.venv\Scripts\python scripts\audit_next_experiment_city_feasibility.py --project-root . --check-only
.\.venv\Scripts\python scripts\lock_multicity_m3_development_protocol.py --project-root . --check-only
```

The next safe task is to obtain the three exact original GeoTIFFs through an
authorized USGS/AWS source, or wait for Planetary Computer to restore those
same bytes. The repair runner must verify product identity, TIFF magic, and the
official MD5 before it writes any cache content. Only after the repair
completion authenticates may the original online queue resume, finalize its
cache, and automatically hand off to the offline pixel-level ST_QA rebuild.
The runner uses only Los Angeles, Phoenix, Houston, and Chicago. It cannot start
nested LOSO or access Seattle, Denver, Atlanta, or Miami assets, predictors,
QA, or targets. The final M3 model specification remains unlocked until a
later source-only selection authorization and completion.

Reauthenticate the incident and exact-three-asset repair permit with:

```powershell
.\.venv\Scripts\python scripts\repair_m3_source_assets_v1.py --project-root . --check-incident
.\.venv\Scripts\python scripts\repair_m3_source_assets_v1.py --project-root . --check-authorization
```

Reauthenticate the current follow-up gates without downloading pixels using:

```powershell
.\.venv\Scripts\python scripts\audit_m3_source_support_and_stage_amendment.py --project-root . --check-only
.\.venv\Scripts\python scripts\stage_m3_source_metadata_inventory_v1.py --project-root . --check-authorization
.\.venv\Scripts\python scripts\stage_m3_source_metadata_inventory_v1.py --project-root . --check-inventory
.\.venv\Scripts\python scripts\authorize_m3_source_qa_execution.py --project-root . --check-only
```

Authenticate the completed gates with:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_missing_support_calibration_evidence_v1.py --check-only
.\.venv\Scripts\python scripts\stage_phoenix_source_footprint_restage.py --check-only
.\.venv\Scripts\python scripts\lock_portable_predictor_contract.py --check-only
.\.venv\Scripts\python scripts\build_portable_predictor_inventory.py --check-only
.\.venv\Scripts\python scripts\build_portable_sentinel_inventory.py --check-only
.\.venv\Scripts\python scripts\build_portable_sentinel_features.py --check-only
```

The completed component build uses stable, purpose-named outputs and builds Los
Angeles on the new canonical support rather than copying Phase-I feature
tables. Its focused test set is:

```powershell
.\.venv\Scripts\python -m pytest -q tests/test_portable_predictor_build.py tests/test_portable_predictor_components.py tests/test_portable_predictor_dashboard.py tests/test_portable_predictor_daymet.py
.\.venv\Scripts\python -m pytest -q tests/test_portable_sentinel_build.py tests/test_portable_sentinel_inventory.py tests/test_portable_sentinel_dashboard.py tests/test_create_portable_sentinel_bundle.py tests/test_sentinel_features.py
```

## Working style

- Make direct implementation changes and use focused tests plus one touched-file
  lint pass.
- Avoid numbered hotfix files and redundant full-project audits.
- Preserve all completed manifests and ignored local checkpoints.
- Do not start a long computation without a visible resumable runner.
- Never place credentials, bearer tokens, signed URLs, or cookies in tracked
  artifacts.

## Key references

- `README.md` — project overview and repository map
- `docs/RESEARCH_PROTOCOL.md` — original Los Angeles design
- `reports/FINAL_EVALUATION_REPORT.md` — held-out Los Angeles result
- `docs/RESULTS_WEBSITE.md` — atlas source and deployment
- `docs/MULTICITY_GENERALIZATION_PROTOCOL.md` — cross-city design
- `docs/MULTICITY_METHODS_AND_EVIDENCE.md` — continuation gates and evidence contract
- `docs/NEXT_EXPERIMENT_PREREGISTRATION_DRAFT.md` — recommended failure-driven
  follow-up design; draft only, with no new target authorization
- `configs/multicity/m3_development_protocol_v1.toml` — locked M3 candidate,
  source-selection, uncertainty, risk, and evaluation contract
- `manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json` —
  append-only M3 development protocol lock
- `docs/M3_SOURCE_DEVELOPMENT_RUNNER.md` — low-load online/offline runner guide
- `manifests/multicity/next_experiment/M3_SOURCE_ACQUISITION_AMENDMENT.json` —
  fixed pre-access source-support expansion
- `manifests/multicity/next_experiment/M3_SOURCE_EXPANDED_INVENTORY.json` —
  authenticated 318-overpass source inventory
- `manifests/multicity/next_experiment/M3_SOURCE_QA_EXECUTION_AUTHORIZATION.json` —
  two-phase source-cache and offline-QA permit
- `docs/MULTICITY_SYNTHETIC_SMOKE.md` — deterministic non-evidence rehearsal
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public-data provenance
