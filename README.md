# Los Angeles Neighborhood Surface Heat

This repository contains the research pipeline, frozen evidence, and public
interactive Atlas for neighborhood-scale daytime land-surface temperature
(LST), beginning in Los Angeles and extending to target-blind cross-city tests.

[![Python CI](https://github.com/CmsChase/LA-neighborhood-heat/actions/workflows/python-ci.yml/badge.svg)](https://github.com/CmsChase/LA-neighborhood-heat/actions/workflows/python-ci.yml)

[Open the Los Angeles heat atlas](https://cmschase.github.io/LA-neighborhood-heat/)
| [Open the M3 research record and final U.S. stage](https://cmschase.github.io/LA-neighborhood-heat/m3/)
| [Open the earlier transfer study](https://cmschase.github.io/LA-neighborhood-heat/cities/)

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
| Seattle–Denver–Atlanta–Miami predictor build | Complete | All 46 predictors and the frozen M3 predictions were authenticated before target access |
| Four-city blind evaluation | Complete; not confirmed | M3 MAE 5.8343 °C versus B1 at 3.8032 °C; all primary gates failed and Miami had only four usable dates |
| LA local relative-accuracy optimization | Complete; model search paused | The existing 23-feature relative model remains the stage default; strict-forward 2022–2024 development MAE was 0.9531 °C and no tested candidate passed the fixed 5% upgrade gate |
| Colorado source addition | Complete; `no_upgrade` | Aggregate absolute MAE improved 8.39%, but Houston and Phoenix degraded beyond the fixed city guard; the original four-source model remains the default |
| U.S. model optimization | Complete; search paused | Existing defaults and limitations are frozen; Chengdu feasibility is a future, separately authorized direction and has not started |

The one-time M3 blind evaluation is complete. Its protocol state is
`inconclusive_sample_size`, and its observed point estimate and full bootstrap
interval disfavor M3. See the
[M3 four-city blind-evaluation report](reports/M3_BLIND_EVALUATION_REPORT.md).
These four cities must not be reused as a new blind test or used for
confirmatory retuning.

The local relative-accuracy work is a separate development task, not a later
point on the original LA holdout or cross-city score curve. Its retained model,
rejected candidates, absolute-output boundary, and local-only artifact inventory
are recorded in the
[LA model-optimization stage summary](docs/LA_MODEL_OPTIMIZATION_STAGE_SUMMARY.zh-CN.md).

The final Colorado source-addition comparison is also development evidence,
not a new blind confirmation. It used the same 96,061 original-source scoring
rows, 132 city-dates and 254 spatial blocks: absolute MAE changed from 4.5251°C
to 4.1454°C, but Houston and Phoenix degraded and the fixed city guard failed.
The complete U.S. stage interpretation and local-artifact inventory are in the
[U.S. model-optimization stage summary](docs/US_MODEL_OPTIMIZATION_STAGE_SUMMARY.zh-CN.md).
The public M3 page displays this result without changing the earlier blind-test
conclusion.

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
