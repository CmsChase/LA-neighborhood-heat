# Project handoff

Last updated: 2026-08-10 Asia/Shanghai

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
13. The folder was copied to the gaming laptop and the long Sentinel build is
    running there with six asset-read threads and one acquisition at a time.
    The engine intentionally processes cities serially in the fixed order
    Chicago, Phoenix, Houston, then Los Angeles. The latest user-confirmed
    checkpoint is Chicago complete (`57 / 516` durable units); that number is
    a snapshot, not a live status feed to this repository.
14. One-click return tooling now accepts the packaged ZIP, the copied
    `GAMING_LAPTOP_SENTINEL` folder, or its extracted result folder. It first
    verifies the frozen 3,410-file source identity and all durable result
    hashes. A safe-paused partial return imports additively, opens the existing
    dashboard, and reauthenticates after the dashboard exits. A complete return
    must prove 516 / 516, four city completion commits, and the final 46-feature
    completion record; it then writes a formal return receipt and runs the
    target-blind predictor-readiness audit.
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
20. The public Atlas now includes a static `/cities/` preview. It compares the
    authenticated target-blind study frame for all four cities while enforcing
    `null` result objects and a `null` claim ID. A later verified payload must
    be complete for all four cities. The future 159-unit target runtime is also
    implemented and testable only in `paused_not_authorized`; no worker, target
    href, thermal value, QA value, model fit, or external result was opened.

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

Authorized now:

- build public predictors for the four canonical city supports;
- read the public static, weather, and non-thermal Sentinel inputs needed for
  those predictors;
- keep the build resumable and visible.

Still prohibited:

- fitting or scoring a model;
- reading external-city Landsat target or QA values;
- opening external evaluation results;
- creating the final protocol lock or external prediction commit.

The single active control record is
`manifests/multicity/ACTIVE_STAGE.json`. Historical numbered transition modules
through V18 remain for provenance and must not be extended with V19/V20 files.

## Exact resume point

The static, calendar, and Daymet component build reached `84 / 84` on
2026-08-10. Its canonical completion record and merged output are:

- `data/processed/multicity/portable_predictors/components/COMPONENTS_COMPLETE.json`
- `data/processed/multicity/portable_predictors/components/predictors_static_calendar_daymet.parquet`

The implementation and portable folder are complete, and the gaming-laptop
run is active. The last confirmed checkpoint is Chicago complete (`57 / 516`).
Leave it running or use `Safe Pause` only when the laptop must stop. The
dashboard tracks 511 acquisitions, four city compiles, and one final merge.
The remaining cities begin automatically in the fixed order. Do not rebuild
the 41 completed features, fit M2, or open any external-city Landsat target
values. Model fitting and target access remain behind later locks.

When the page reports `complete`, close it and double-click
`PACKAGE_RESULTS.cmd`. Bring back the generated ZIP and its `.sha256`, or copy
back the whole portable folder. A safe pause is also portable: request
`Safe Pause`, wait for running to reach zero, then package or copy the folder.
The returned cache can resume without repeating committed acquisitions.

For the simplest return, drag the ZIP or returned folder onto
`IMPORT_SENTINEL_RESULTS.cmd`; double-clicking it also prompts for a path. The
equivalent commands are:

```powershell
.\.venv\Scripts\python scripts\import_portable_sentinel_results.py --archive "D:\path\returned-results.zip" --audit-if-complete
.\.venv\Scripts\python scripts\import_portable_sentinel_results.py --source-directory "D:\path\returned-folder" --resume-dashboard --audit-if-complete
```

The command validates before import. A partial safe-pause return resumes through
the portable dashboard and is rechecked when the dashboard closes. A complete
return writes `manifests/multicity/returns/PORTABLE_SENTINEL_RETURN.json` and
must report `ready_for_protocol_lock_not_model_fit`. It still does not
authorize or perform model fitting.

The already completed spatial partition can be reauthenticated without target
or predictor access using:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_spatial_blocks.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_contexts.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_build_plan.py --check-only
```

After predictor readiness succeeds, the next scientific action is to bind the
existing frozen predictor/model contract into the full protocol/model lock.
That lock must then separately authorize and complete the 90-date LA
2020–2024 source-target lane on the new support before any real fit. Do not
call the transfer-model fit functions against real labels while
`ACTIVE_STAGE.json` still has `model_fit_authorized=false`; external targets
remain behind the later prediction-commit and one-claim gates.

Authenticate the completed gates with:

```powershell
Set-Location "D:\HuaweiMoveData\Users\haora\Documents\ISEF"
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
- `docs/MULTICITY_SYNTHETIC_SMOKE.md` — deterministic non-evidence rehearsal
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public-data provenance
