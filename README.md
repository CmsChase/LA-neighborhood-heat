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

The only active runtime state is
[`manifests/multicity/ACTIVE_STAGE.json`](manifests/multicity/ACTIVE_STAGE.json).
It authorizes public geography, WorldCover support, and small Sentinel-2
calibration checks. The earlier V7–V18 transition files are historical
provenance; they are not the active workflow and no new numbered hotfix file
should be created.

The static, calendar, and Daymet component build now runs through a local,
resumable progress page:

```powershell
.\.venv\Scripts\python scripts\run_portable_predictor_dashboard.py --host 127.0.0.1 --port 8768
```

Open `http://127.0.0.1:8768/` to see the active city and phase, completed work
units, estimated time, and recent events. Safe pause finishes the current
atomic file or geography chunk and preserves all completed work. When the
three external cities reach their weather downloads, enter an Earthdata bearer
token in the page; it is passed only to the child process and is not written to
the repository or runtime logs.

The machine-readable live state is
`data/interim/multicity/portable_predictors/runtime/status.json`. The current
runner builds only public predictor components. It does not fit a model or
read external-city target values.

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
