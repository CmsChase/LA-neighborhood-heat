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

The static, calendar, and Daymet component runner was started on 2026-08-10.
Its authoritative live state is:

`data/interim/multicity/portable_predictors/runtime/status.json`

When the local dashboard process is active, open `http://127.0.0.1:8768/`.
If it is not active, restart the supervisor with:

```powershell
Set-Location "D:\HuaweiMoveData\Users\haora\Documents\ISEF"
.\.venv\Scripts\python scripts\run_portable_predictor_dashboard.py --host 127.0.0.1 --port 8768
```

The work plan resumes from completed task records rather than rebuilding prior
chunks. It first completes all token-free calendar, static, GSHHG, and Los
Angeles Daymet work. It then enters `waiting_for_earthdata_token` if the 18
external-city Daymet subsets are still missing. Paste a fresh Earthdata bearer
token into the local page and click Start / Continue. The token is held only in
the dashboard process memory and child environment.

After the component runner reaches `complete`, the next implementation stage
is the visible, resumable Sentinel predictor build. Do not fit M2 or open any
external-city Landsat target values before the protocol, prediction commit,
and target-opening gates authorize those actions.

Authenticate the completed gates with:

```powershell
Set-Location "D:\HuaweiMoveData\Users\haora\Documents\ISEF"
.\.venv\Scripts\python scripts\stage_multicity_missing_support_calibration_evidence_v1.py --check-only
.\.venv\Scripts\python scripts\stage_phoenix_source_footprint_restage.py --check-only
.\.venv\Scripts\python scripts\lock_portable_predictor_contract.py --check-only
.\.venv\Scripts\python scripts\build_portable_predictor_inventory.py --check-only
```

The current component build writes stable, purpose-named outputs and builds Los
Angeles on the new canonical support rather than copying Phase-I feature
tables. Its focused test set is:

```powershell
.\.venv\Scripts\python -m pytest -q tests/test_portable_predictor_build.py tests/test_portable_predictor_components.py tests/test_portable_predictor_dashboard.py tests/test_portable_predictor_daymet.py
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
