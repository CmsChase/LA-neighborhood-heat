# Results website

Public site: <https://cmschase.github.io/LA-neighborhood-heat/>

Target-sealed four-city preview:
<https://cmschase.github.io/LA-neighborhood-heat/cities/>

The Atlas is the presentation layer for the frozen one-time 2025 evaluation.
It does not train a model, recalculate metrics, or authorize another holdout
read. Its source now lives in `atlas/` of this repository; the former
standalone repository is an archived historical snapshot.

## What it shows

- the equal-square Los Angeles homepage heat mosaic;
- synchronized observed, M2/B1 predicted, and residual tract maps;
- button zoom, pan, reset, neighborhood/GEOID search, and tract selection;
- Mapping L.A. neighborhood names assigned by maximum tract-area overlap;
- all 15 usable held-out dates for each tract;
- observed and predicted LST, signed and absolute error, valid-pixel fraction,
  uncertainty, and Sentinel-2 availability;
- per-date performance, crossed date/spatial-block uncertainty, and hotspot
  diagnostics.

The displayed endpoint is clear-sky daytime land-surface temperature. It is not
air temperature, personal exposure, illness, or mortality. The experiment is a
historical hindcast, not an operational forecast.

## Four-city preview

`atlas/app/cities/` is a static comparison interface for Los Angeles, Phoenix,
Houston, and Chicago. While external targets are sealed it exposes only the
authenticated study design: city roles, tract counts, planned overpasses,
spatial-block counts, the fixed 46-feature contract, and the gate sequence.
Every result object and the external claim ID are `null` in preview mode.

The runtime validator in `atlas/app/cities/comparison-data.ts` rejects any
preview payload containing result values. A future verified release must supply
one authenticated claim ID and a complete result object for all four cities;
partial or selectively released city results are rejected. The browser never
fits a model, opens target data, or calculates a scientific metric.

## Source and data layout

```text
atlas/app/                 LA interface plus the target-sealed /cities route
atlas/public/data/         compact authenticated display JSON
atlas/tests/               static-export regression test
scripts/build_website_data.py
src/la_heat/website_export.py
tests/test_website_export.py
```

The compact export contains 1,096 tract geometries, 15,116 tract-date rows,
and 15 dates. Current file identities are:

| File | SHA-256 |
|---|---|
| `tracts.json` | `3712654983cee108eea2e54066b8286ff1bb3fd291e6126bace538b820ac670f` |
| `evaluation-2025.json` | `617eac416e348b4a0445a06c2d3627d1fd51421faf6c23f4c513835a06aa7938` |
| `metrics.json` | `494db653c65ba75ae2d2b312c808e80a376e75d97a177d3b8785342130f80aeb` |
| `display-manifest.json` | `675f69e8138d7a1255973b3da70928a19ee70f4ec52d580421e5d9786af18e85` |

Neighborhood labels come from the commit-pinned Los Angeles Times Mapping L.A.
dataset. They are display metadata, not predictors, targets, splits, or
evaluation strata.

## Rebuild and verify display data

From the repository root:

```powershell
.\.venv\Scripts\python scripts\stage_mapping_la_neighborhoods.py
.\.venv\Scripts\python scripts\build_website_data.py
.\.venv\Scripts\python scripts\build_website_data.py --verify-only
```

The default output is `atlas/public/data`; an explicit `--output-dir` is no
longer needed.

## Run and validate the Atlas

```powershell
Set-Location atlas
npm ci
npm run dev
```

Static Pages validation:

```powershell
$env:GITHUB_PAGES = "true"
$env:NEXT_PUBLIC_BASE_PATH = "/LA-neighborhood-heat"
$env:NEXT_PUBLIC_SITE_URL = "https://cmschase.github.io/LA-neighborhood-heat/"
npm test
npm run lint
```

`.github/workflows/deploy-atlas-pages.yml` runs the same build from `atlas/`
and deploys `atlas/out` after relevant changes reach `main`.
