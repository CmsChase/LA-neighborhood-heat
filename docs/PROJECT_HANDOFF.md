# Project handoff

Last updated: 2026-08-12 Asia/Shanghai

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

## Frozen scientific decisions

- M2 is the only primary transfer model and uses all 46 frozen predictors.
- B1 remains only as a 23-feature weather/calendar diagnostic baseline. It is
  not a deployment or model-selection candidate.
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

Still prohibited:

- refitting, recalibrating, or selecting models from external outcomes;
- replacing the frozen confirmatory result with a post-hoc sensitivity result;
- claiming successful cross-city confirmation or calibrated uncertainty.

The single active control record is
`manifests/multicity/ACTIVE_STAGE.json`. Historical numbered transition modules
through V18 remain for provenance and must not be extended with V19/V20 files.

## Exact resume point

The public predictor phase is complete: static/calendar/Daymet reached `84 / 84`,
Sentinel reached `516 / 516`, and the final table is 136,941 rows by 46 frozen
features. Do not rebuild or re-import these products.

The exact tracked stage is `external_evaluation_complete_inconclusive_sample_size`.
The complete target claim contains 64 overpasses and three city compiles. The
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
- `docs/MULTICITY_SYNTHETIC_SMOKE.md` — deterministic non-evidence rehearsal
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public-data provenance
