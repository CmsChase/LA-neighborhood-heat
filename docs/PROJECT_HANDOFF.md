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
    370,520,872 bytes. Independent verification found zero missing, extra, or
    hash-mismatched files. The manifest SHA-256 is
    `41819b6054b10a708e1644349ef31e361fd6d2db89b0ef26599c1a8cfafea449`.
    Package-local engine check returned `ready`; dashboard smoke returned
    `paused` with no engine process. The complete Windows PowerShell first-run
    path also passed after the quoting repair. The long raster build has not
    started.

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

The implementation and portable folder are now complete. The exact next task
is to copy the whole `exports/GAMING_LAPTOP_SENTINEL` directory to the gaming
laptop's local NVMe drive, double-click `START_HERE.cmd`, keep the default 6
download threads and 1 acquisition concurrency initially, and click
`Start / Continue`. The dashboard tracks 516 durable units: 511 acquisitions,
four city compiles, and one final merge. Do not rebuild the 41 completed
features, fit M2, or open any external-city Landsat target values. Model fitting
and target access remain behind later locks.

When the page reports `complete`, close it and double-click
`PACKAGE_RESULTS.cmd`. Bring back both the generated ZIP and its `.sha256`
file. A safe pause is also portable: request `Safe Pause`, wait for running to
reach zero, then package or shut down. The returned cache can resume without
repeating committed acquisitions.

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
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public-data provenance
