# LA Surface Heat Atlas

An interactive, read-only atlas comparing observed Landsat land-surface
temperature with two frozen model predictions across Los Angeles census tracts
in the held-out 2025 evaluation.

Live site:
<https://cmschase.github.io/LA-neighborhood-heat/>

The atlas now lives in `atlas/` of the same repository as the research
pipeline. The former standalone repository is retained only as an archived
historical snapshot.

## What the atlas shows

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

## Local verification

Requires Node.js 22 or newer.

```bash
npm ci
GITHUB_PAGES=true \
NEXT_PUBLIC_BASE_PATH=/LA-neighborhood-heat \
NEXT_PUBLIC_SITE_URL=https://cmschase.github.io/LA-neighborhood-heat/ \
npm test
npm run lint
```

Files under `public/data/` are a compact display export authenticated against
the frozen evaluation. They are presentation inputs and must not be edited by
hand.

Pushes to `main` are built, tested, and deployed automatically through GitHub
Actions.
