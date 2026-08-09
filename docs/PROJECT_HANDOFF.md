# Project handoff

Last updated: 2026-08-09 Asia/Shanghai

## Project in one paragraph

The completed Los Angeles study predicts census-tract/date daytime Landsat
land-surface temperature from public weather, land-use, geography, and lagged
non-thermal Sentinel-2 features. M2 reached 2.1650 °C equal-date MAE versus
3.1165 °C for B1 on the one-time 2025 holdout. The 30.53% point improvement was
not protocol-confirmed because its prespecified 95% interval crossed zero. The
research code, evidence, and public atlas now live in one repository.

## Current repository state

- Canonical repository: `https://github.com/CmsChase/LA-neighborhood-heat`
- Public atlas: `https://cmschase.github.io/LA-neighborhood-heat/`
- Website source: `atlas/`
- The old `LA-surface-heat-atlas` repository is an archived historical copy.
- Use `git log -1 --oneline` and `git status --short --branch` for the live
  checkpoint; do not rely on an old commit hash written in prose.

The historical multicity transition files through V18 remain in Git for
provenance. They are not the active runtime and must not be extended with V19,
V20, and so on. The only current stage record is:

`manifests/multicity/ACTIVE_STAGE.json`

## Completed work

1. The Los Angeles development, model lock, one-time 2025 evaluation, evidence
   package, reports, and interactive atlas are complete.
2. The cross-city extension has authenticated public geography and WorldCover
   eligible support for Los Angeles, Phoenix, Houston, and Chicago.
3. The Phoenix real Sentinel-2 calibration-smoke checkpoint is complete.
4. Eleven of the fifteen public-evidence outputs exist locally and must be
   preserved. They are intentionally untracked until the stage finishes.
5. The former oversized Sentinel cohort request has been fixed: STAC discovery
   now uses the city bounding box, then keeps only records with the exact frozen
   physical acquisition key and computes coverage against the true city shape.

## Active task and resume point

No Python worker should be running at handoff.

The active public-only evidence stage previously stopped on Houston because a
full city MultiPolygon caused HTTP 413 at the Planetary Computer STAC endpoint.
The code fix and a focused regression test are now in the repository. Resume
from the existing eleven checkpoints with:

```powershell
Set-Location "D:\HuaweiMoveData\Users\haora\Documents\ISEF"
.\.venv\Scripts\python scripts\stage_multicity_missing_support_calibration_evidence_v1.py
```

Human-readable progress is written to:

`data/interim/multicity/missing_support_calibration_evidence_v1/status.json`

Expected remaining outputs are Houston Sentinel, Chicago Sentinel, the
three-city Sentinel terminal, and the overall evidence terminal. Do not delete
or recreate the existing eleven checkpoints.

When all fifteen outputs exist, commit those outputs once and run:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_missing_support_calibration_evidence_v1.py --check-only
```

The next scientific decision is `portable_predictor_contract_decision`. Model
construction, fitting, scoring, and external target access are not yet
authorized.

## Normal working style

- Make the direct implementation change; do not create a numbered transition
  module for a routine bug.
- Run focused tests and one touched-file lint pass during development.
- Run the full suite only for a scientific-contract change or release.
- Keep long computation resumable and visible. Do not start a long background
  job unless the user asked for it.
- Preserve the one-time 2025 result and all existing checkpoint files.

Focused verification for the current change:

```powershell
.\.venv\Scripts\python -m pytest `
  tests\test_multicity_missing_support_calibration_evidence_v1.py `
  tests\test_multicity_sentinel_calibration_smoke_v1.py -q
```

## Key references

- `README.md` — concise project overview and repository map
- `docs/RESEARCH_PROTOCOL.md` — original scientific design
- `reports/FINAL_EVALUATION_REPORT.md` — held-out result
- `docs/RESULTS_WEBSITE.md` — atlas source, data, and deployment
- `docs/MULTICITY_GENERALIZATION_PROTOCOL.md` — cross-city research design
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public data provenance

Never place credentials, bearer tokens, signed URLs, or cookies in this file or
any tracked artifact.
