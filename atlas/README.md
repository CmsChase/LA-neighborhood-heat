# LA Surface Heat Atlas

An interactive, read-only atlas comparing observed Landsat land-surface
temperature with two frozen model predictions across Los Angeles census tracts
in the held-out 2025 evaluation. The original Los Angeles experience remains
the public homepage; a separate four-city view presents the opened 2025 M3
evaluation.

Live site: <https://cmschase.github.io/LA-neighborhood-heat/>

## Public routes

- `/` — the complete Los Angeles Surface Heat Atlas;
- `/four-cities/` — switchable tract maps for Seattle, Denver, Atlanta, and
  Miami;
- `/m3/` — the research story and M3 blind-test interpretation;
- `/cities/` — compatibility route for older links.

The homepage links to the four-city atlas. The four-city maps present frozen,
already-opened stress-test results; they do not recompute metrics or turn the
failed M3 absolute-temperature comparison into a new claim. Land-surface
temperature is not air temperature, exposure, illness, or causation.

## What the Los Angeles atlas shows

- synchronized observed, predicted, and residual tract maps;
- an oversized equal-square homepage mosaic of the fixed September 3 M2
  prediction, with its date, scale, and place readout pinned inside the
  viewport;
- a larger map with button zoom, pan, neighborhood/GEOID search, and keyboard
  selection; a short pointer tap selects a tract while a drag pans the map;
- Mapping L.A. neighborhood labels assigned by maximum tract-area overlap,
  with cross-neighborhood proportions retained;
- a 15-date record for every selected tract, including observed and predicted
  LST, signed and absolute error, valid-pixel fraction, uncertainty, and
  Sentinel-2 availability;
- all 15 usable held-out dates and both frozen models;
- observed-versus-predicted distributions and per-date performance;
- crossed date-by-spatial-block bootstrap uncertainty;
- hotspot ranking and evidence identities.

The point estimate favors the primary model, but the prespecified 95% interval
crosses zero. The result is promising, not protocol-confirmed. The endpoint is
daytime land-surface temperature: a surface-heat hazard proxy, not air
temperature, personal exposure, illness, or mortality. This is a historical
hindcast, not an operational forecast.

Neighborhood labels come from the commit-pinned
[Los Angeles Times Mapping L.A. dataset](https://github.com/datadesk/mapping-la-data)
under its MIT license. They are assigned to census tracts by maximum mapped-area
overlap and remain display metadata; the evaluated unit is still the tract.

## Four-city display export

Regenerate the compact display payload from the frozen local inputs:

```bash
python scripts/export_four_city_atlas.py --project-root .
```

The exporter reads the existing M3 blind-evaluation rows and fixed Census tract
geometries. Do not edit `atlas/public/data/four-city-atlas.json` by hand.

Files under `public/data/` are compact display exports authenticated against
the frozen evaluations. They are presentation inputs and must not be edited by
hand.

## Local verification

Requires Node.js 22 or newer.

```bash
cd atlas
npm ci
GITHUB_PAGES=true \
NEXT_PUBLIC_BASE_PATH=/LA-neighborhood-heat \
NEXT_PUBLIC_SITE_URL=https://cmschase.github.io/LA-neighborhood-heat/ \
npm test
npm run lint
```

Pushes to `main` are built, tested, and deployed automatically through GitHub
Actions.
