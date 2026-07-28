# Results website

The interactive results site is deployed at:

https://cmschase.github.io/LA-surface-heat-atlas/

This is a public GitHub Pages deployment. It is a presentation layer over the
frozen one-time 2025 evaluation; it does not train a model, recalculate the
final metrics, or authorize a second test-set read.

The currently deployed site is the verified
`website-display-export-v3` release.

## What the site shows

- a 40-column by 59-row field of equal display squares on the homepage, with
  869 Los Angeles cells retained after the frozen coverage rule; every square
  is colored from the M2 prediction for the fixed display date
  `2025-09-03`, assigned by maximum tract overlap, separated by a visible gap,
  and linked to the assigned tract's source-backed neighborhood name and
  GEOID; the field is oversized and may extend beyond the hero, while its
  date/scale and selected-place cards remain inside the viewport;
- synchronized census-tract maps for Landsat-observed LST, M2 or B1
  predictions, and prediction residuals;
- a larger, viewport-bounded tract map with button zoom, drag-to-pan, reset,
  GEOID or Mapping L.A. neighborhood search, mouse selection, and keyboard
  selection; wheel/scroll zoom is deliberately disabled so normal page
  scrolling is not captured; a short pointer tap selects the tract and a
  movement beyond five pixels becomes a pan, so the selection and the data
  below remain synchronized;
- a source-backed Mapping L.A. neighborhood label for every tract, with the
  tract GEOID retained and cross-boundary neighborhood proportions available
  rather than hiding boundary ambiguity;
- a 15-date timeline and full table for each selected tract, including
  observed and predicted LST, signed and absolute error, valid-pixel fraction,
  median ST uncertainty, and Sentinel-2 availability;
- a selector for all 15 usable held-out dates;
- observed-versus-predicted scatterplots for one date or the complete held-out
  cohort;
- per-date MAE and rank-correlation diagnostics;
- the prespecified 5,000-replicate crossed date-by-spatial-block uncertainty
  interval;
- top-20% hotspot performance, method boundaries, and evidence identities.

The shared temperature scale is fixed across the observed and prediction maps.
Residual is defined as prediction minus observation, so positive values indicate
overprediction. The main result is deliberately qualified: M2 lowered the
held-out point-estimate MAE by 30.53%, but the 95% relative-improvement interval
was -10.13% to 58.46%. The result is promising, not protocol-confirmed.

The square homepage field is a display raster, not a new unit of analysis.
Its 869 included cells do not replace the 1,096 census tracts, and the fixed
September 3 view is not a new model-selection result. Formal metrics continue
to use the frozen tract-date evaluation rows and their predeclared grouped
analysis.

LST is a clear-sky surface-heat hazard proxy, not air temperature, personal
exposure, illness, or mortality. The experiment is a historical hindcast, not
an operational weather forecast.

## Reproducible display export

The root repository owns the deterministic export code:

- `src/la_heat/website_export.py`
- `scripts/build_website_data.py`
- `tests/test_website_export.py`

The current public website is an independent nested Git project under the
ignored `website-github-pages/` directory. Its public repository and deployed
source commit are:

- `https://github.com/CmsChase/LA-surface-heat-atlas`
- `0f360543d85bdd0401cea473d57d5ddd3abcef5a`

The earlier `website/` project is retained locally as the original visual
source, but it is not the current public deployment.

Stage or authenticate the exact Mapping L.A. source snapshot:

```powershell
.\.venv\Scripts\python scripts\stage_mapping_la_neighborhoods.py
```

The frozen source is the Los Angeles Times Data and Graphics Department
`mapping-la-data` repository at commit
`5acc817cd8e9ef1800dc9641493e46efe7ce35b0`. The exact raw file is:

```text
https://raw.githubusercontent.com/datadesk/mapping-la-data/5acc817cd8e9ef1800dc9641493e46efe7ce35b0/geojson/la-county-neighborhoods-v6.geojson
```

Its SHA-256 is
`ada200f59e0d2cd7e04a212eb5510cfe570765d68b7ff29d83b97cc5abeb6ead`.
The exporter filters the authenticated countywide file to exactly 114 records
whose metadata identify the City of Los Angeles. For each tract, it computes
polygon intersections in a shared projected CRS, uses the neighborhood with
the largest intersected area as the primary display name, and preserves all
nonzero cross-boundary covered-area proportions plus total source coverage.
These names are display metadata only; they are not predictors, targets,
splits, or evaluation strata.

Build or authenticate the compact display files from the frozen outputs:

```powershell
.\.venv\Scripts\python scripts\build_website_data.py `
  --output-dir website-github-pages\public\data
.\.venv\Scripts\python scripts\build_website_data.py --verify-only `
  --output-dir website-github-pages\public\data
```

The verified display export contains 1,096 tract geometries, 15,116
tract-date rows, and 15 dates. Its manifest is
`website-github-pages/public/data/display-manifest.json`. Current display-file
hashes are:

| File | SHA-256 |
|---|---|
| `tracts.json` | `3712654983cee108eea2e54066b8286ff1bb3fd291e6126bace538b820ac670f` |
| `evaluation-2025.json` | `617eac416e348b4a0445a06c2d3627d1fd51421faf6c23f4c513835a06aa7938` |
| `metrics.json` | `494db653c65ba75ae2d2b312c808e80a376e75d97a177d3b8785342130f80aeb` |
| `display-manifest.json` | `675f69e8138d7a1255973b3da70928a19ee70f4ec52d580421e5d9786af18e85` |

The historical v2 display manifest SHA-256 is
`253435604ee2ceece0e820b8df71ab86584e3d4aac39ad7e485e1fe362b2816c`.
Version 2 changes only the presentation label from the generic Census type to
the authenticated TIGER tract number (for example, `Census Tract 1011.10`).
The evaluation and metric files are byte-identical to version 1.

Version 3 adds the Mapping L.A. labels, preserved overlap proportions, the
40-by-59 homepage grid with 869 included cells, and the fixed homepage date
and model (`2025-09-03`, M2). Its evaluation and metrics files remain
byte-identical to v2. The canonical final-evaluation outputs, model choice,
thresholds, uncertainty interval, and protocol conclusion do not change.

The display manifest binds these products to claim
`c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`,
completion commit
`4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`,
and evidence ZIP SHA-256
`61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.

## Website validation

From `website-github-pages/`, with the repository name as the Pages base path:

```powershell
$env:GITHUB_PAGES = "true"
$env:NEXT_PUBLIC_BASE_PATH = "/LA-surface-heat-atlas"
$env:NEXT_PUBLIC_SITE_URL = "https://cmschase.github.io/LA-surface-heat-atlas/"
npm test
npm run lint
```

The v3 root validation used an independent D-drive pytest base directory and
passed all 747 tests with five warnings in 529.02 seconds. Full-repository Ruff
also passed. These results supplement rather than replace the earlier v2
validation record.

GitHub Actions run `30362483324` passed installation, linting, TypeScript,
static export, exact data/hash tests, artifact upload, and deployment from
commit `0f360543d85bdd0401cea473d57d5ddd3abcef5a`. Public HTTP checks returned
200 for the page and all four JSON files and matched every deployed v3 hash.
Public browser verification confirmed that both homepage cards stayed inside
the viewport, the square map exceeded the hero height, and selecting Tujunga
tract `06037101110` changed its `aria-pressed` state, selected neighborhood,
timeline label, and complete table caption together. The interface still has
no `Scroll to zoom` control. The preceding v3 interface deployment at commit
`206c04b797b184aa3dcb3aec60ac0ffaba8775bb` and run `30358959707`, and the
earlier v2 deployment at commit
`128283948c74ad262401a3ced390e452d285e0b1` and run `30353854439` remain
historical provenance. The publication paper, poster, oral-defense deck, and
QR asset continue to use the same public URL; no scientific result changed.
