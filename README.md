# Los Angeles Neighborhood Surface Heat

This repository contains the research pipeline, frozen evidence, and public
interactive Atlas for neighborhood-scale daytime land-surface temperature
(LST), beginning in Los Angeles and extending to target-blind cross-city tests.

[![Python CI](https://github.com/CmsChase/LA-neighborhood-heat/actions/workflows/python-ci.yml/badge.svg)](https://github.com/CmsChase/LA-neighborhood-heat/actions/workflows/python-ci.yml)

[Open the Los Angeles heat atlas](https://cmschase.github.io/LA-neighborhood-heat/)
| [Open the authenticated four-city evaluation](https://cmschase.github.io/LA-neighborhood-heat/cities/)

## Research question

Can public weather, land-use, geography, and lagged non-thermal satellite
features predict census-tract-scale daytime surface heat?

In the original Los Angeles study, M2 was trained on 2020–2024 data and
evaluated once on the predeclared Los Angeles 2025 holdout. The later transfer
study used Los Angeles 2020–2023 for training, Los Angeles 2024 for calibration,
and Phoenix, Houston, and Chicago 2025 as one target-blind external claim. Model
inputs contain no Landsat thermal target values, same-scene optical data, future
observations, or tract identifiers.

| Original Los Angeles held-out 2025 result | B1 baseline | M2 model |
|---|---:|---:|
| Equal-date-weighted MAE | 3.1165 °C | 2.1650 °C |
| Relative MAE change | — | 30.53% lower |
| Median per-date Spearman | — | 0.8447 |

The point estimate favors M2, but the prespecified 95% interval for relative
MAE improvement was -10.13% to 58.46%. Because it crosses zero, the result is
promising rather than protocol-confirmed.

## Project status

| Stage | Status | Result or boundary |
|---|---|---|
| Los Angeles 2025 holdout | Complete | M2 MAE was 30.53% lower than B1; the 95% interval crossed zero |
| Phoenix–Houston–Chicago transfer | Complete | Overall MAE improved 28.9%, but the preregistered confirmation and reliability gates were not met |
| M3 source-only development | Complete | Nested whole-city LOSO selected QA `4k` and the frozen M3 specification without using blind-city targets |
| Seattle–Denver–Atlanta–Miami predictor build | In progress | Support, 23,667 keys, public metadata, and the exact 539-acquisition Sentinel inventory are complete |
| Four-city blind evaluation | Sealed | No blind-city Landsat thermal, QA, or target value may be read before predictions are committed |

The next permitted step is resumable acquisition of Sentinel-2 and static
predictor values. Daymet acquisition is separately authorized but waits for an
in-memory Earthdata token; credentials must never be committed. After all 46
predictors are assembled, the frozen model will create and commit predictions
before the one-time blind target evaluation is authorized.

The machine-readable current state is
[`manifests/multicity/ACTIVE_STAGE.json`](manifests/multicity/ACTIVE_STAGE.json).
For a plain-language explanation of authorization, completion, and commit
fingerprints, see [Provenance and scientific gates](docs/PROVENANCE.md). Older
numbered transition files are historical records, not active entry points.

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
| `tools/` | Optional operational helpers, including archived Windows launchers |
| `data/`, `exports/` | Local generated data and evidence packages; not tracked |

The old standalone Atlas repository has been merged into `atlas/`. It is no
longer a separate codebase. The current website is built and deployed from
this repository by GitHub Actions.

## Local setup

Python 3.12–3.14 is supported. From the repository root:

```text
python -m venv .venv
```

Install and verify on macOS/Linux:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c requirements-ci.txt -e ".[dev]"
.venv/bin/python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
.venv/bin/python -m pytest -q --basetemp=.tmp/pytest-ci
.venv/bin/python -m ruff check .
```

Install and verify on Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c requirements-ci.txt -e ".[dev]"
.\.venv\Scripts\python.exe -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest-ci
.\.venv\Scripts\python.exe -m ruff check .
```

The public Atlas requires Node.js 22:

```text
cd atlas
npm ci
npm test
npm run dev
```

See the cross-platform [reproduction guide](docs/REPRODUCING.md) for display-data
verification, evidence export, generated-data boundaries, and optional
historical Windows helpers.

Historical Windows launchers are archived under
[`tools/windows/`](tools/windows/); none is required for normal setup.

## Interpretation limits

- LST is a clear-sky surface-heat hazard proxy, not air temperature, personal
  exposure, illness, or mortality.
- This is a historical hindcast, not an operational weather forecast.
- Tract-date rows are not independent; evaluation groups dates and spatial
  blocks.
- Feature importance is predictive association, not causation.
- The completed 2025 holdout must not be retuned or presented as a second test.

Start a new work session with
[`docs/PROJECT_HANDOFF.md`](docs/PROJECT_HANDOFF.md). Detailed scientific
decisions remain in [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md), source
provenance is in [`docs/DATA_MANIFEST.csv`](docs/DATA_MANIFEST.csv), and the
public-facing evidence model is summarized in
[`docs/PROVENANCE.md`](docs/PROVENANCE.md).
