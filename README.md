# Los Angeles Neighborhood Surface Heat

This repository contains one complete project: the research pipeline, frozen
evidence, and the public interactive atlas for neighborhood-scale daytime
land-surface temperature (LST) in Los Angeles.

[Open the heat atlas](https://cmschase.github.io/LA-neighborhood-heat/)

## Research question

Can public weather, land-use, geography, and lagged non-thermal satellite
features predict census-tract-scale daytime surface heat?

The primary model (M2) was trained on 2020–2024 data and evaluated once on a
predeclared 2025 holdout. Its inputs contain no Landsat thermal target values,
same-scene optical data, future observations, or tract identifiers.

| Held-out 2025 result | B1 baseline | M2 model |
|---|---:|---:|
| Equal-date-weighted MAE | 3.1165 °C | 2.1650 °C |
| Relative MAE change | — | 30.53% lower |
| Median per-date Spearman | — | 0.8447 |

The point estimate favors M2, but the prespecified 95% interval for relative
MAE improvement was -10.13% to 58.46%. Because it crosses zero, the result is
promising rather than protocol-confirmed.

## Repository map

| Path | Purpose |
|---|---|
| `atlas/` | Next.js source and compact frozen data for the public website |
| `src/la_heat/` | Reusable Python data, feature, modeling, and evidence code |
| `scripts/` | Command-line entry points |
| `configs/` | Research and runtime configuration |
| `manifests/` | Machine-readable provenance and stage records |
| `docs/` | Protocols, decisions, data sources, status, and handoff |
| `reports/` | Scientific reports, tables, and figures |
| `tests/` | Scientific invariants and focused regression tests |
| `data/`, `exports/` | Local generated data and evidence packages; not tracked |

The old standalone Atlas repository has been merged into `atlas/`. It is no
longer a separate codebase. The current website is built and deployed from
this repository by GitHub Actions.

## Current continuation

The Los Angeles evaluation is complete. The active research extension tests
whether a Los-Angeles-trained predictor can transfer, without retraining, to
Phoenix, Houston, and Chicago. External-city target values remain sealed.

The active stage is recorded in
[`manifests/multicity/ACTIVE_STAGE.json`](manifests/multicity/ACTIVE_STAGE.json).
The earlier V7–V18 transition files are historical provenance; they are not the
active workflow and no new numbered hotfix file should be created.

The resumable static, calendar, and Daymet build is complete: all 84 work units
produced 41 non-Sentinel predictors for 136,941 frozen rows. Its canonical
outputs are:

- `data/processed/multicity/portable_predictors/components/COMPONENTS_COMPLETE.json`
- `data/processed/multicity/portable_predictors/components/predictors_static_calendar_daymet.parquet`

The visible, resumable four-city Sentinel-2 predictor build is currently
running on the gaming laptop, in the fixed order Chicago, Phoenix, Houston,
then Los Angeles. No model has been fit and external-city target values remain
sealed.

## Local setup

Python pipeline:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Atlas:

```powershell
Set-Location atlas
npm ci
npm run dev
```

Rebuild or verify the compact display data from the repository root:

```powershell
.\.venv\Scripts\python scripts\build_website_data.py
.\.venv\Scripts\python scripts\build_website_data.py --verify-only
```

Run focused tests for changed behavior during normal development. Run the full
suite before a scientific release or a change to targets, features, splits,
metrics, or frozen evidence:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
```

## Interpretation limits

- LST is a clear-sky surface-heat hazard proxy, not air temperature, personal
  exposure, illness, or mortality.
- This is a historical hindcast, not an operational weather forecast.
- Tract-date rows are not independent; evaluation groups dates and spatial
  blocks.
- Feature importance is predictive association, not causation.
- The completed 2025 holdout must not be retuned or presented as a second test.

Start a new work session with the concise
[`docs/PROJECT_HANDOFF.md`](docs/PROJECT_HANDOFF.md). Detailed scientific
decisions remain in [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md), and source
provenance is in [`docs/DATA_MANIFEST.csv`](docs/DATA_MANIFEST.csv).
