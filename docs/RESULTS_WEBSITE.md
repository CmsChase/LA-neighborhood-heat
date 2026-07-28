# Results website

The interactive results site is deployed at:

https://la-surface-heat-atlas.zhuzifu485.chatgpt.site

The deployment currently uses owner-authenticated access. It is a presentation
layer over the frozen one-time 2025 evaluation; it does not train a model,
recalculate the final metrics, or authorize a second test-set read.

## What the site shows

- synchronized census-tract maps for Landsat-observed LST, M2 or B1
  predictions, and prediction residuals;
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

LST is a clear-sky surface-heat hazard proxy, not air temperature, personal
exposure, illness, or mortality. The experiment is a historical hindcast, not
an operational weather forecast.

## Reproducible display export

The root repository owns the deterministic export code:

- `src/la_heat/website_export.py`
- `scripts/build_website_data.py`
- `tests/test_website_export.py`

The website is an independent nested Git project under the ignored `website/`
directory. Its deployed source commit is
`abd30c33539e93d49f8674d3b209d07afbb3ce90`.

Build or authenticate the compact display files from the frozen outputs:

```powershell
.\.venv\Scripts\python scripts\build_website_data.py
.\.venv\Scripts\python scripts\build_website_data.py --verify-only
```

The verified display export contains 1,096 tract geometries, 15,116
tract-date rows, and 15 dates. Its manifest is
`website/public/data/display-manifest.json`. Current display-file hashes are:

| File | SHA-256 |
|---|---|
| `tracts.json` | `0aef9a34d06c39d23309b1a18844fc193d0963a28ac8427c2246c77c9fd0c9d1` |
| `evaluation-2025.json` | `617eac416e348b4a0445a06c2d3627d1fd51421faf6c23f4c513835a06aa7938` |
| `metrics.json` | `494db653c65ba75ae2d2b312c808e80a376e75d97a177d3b8785342130f80aeb` |

The display manifest binds these products to claim
`c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`,
completion commit
`4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`,
and evidence ZIP SHA-256
`61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.

## Website validation

From `website/`:

```powershell
npm test
npm run lint
```

The committed version passed its production build, rendered-HTML tests,
linting, desktop visual review at 1440 x 1000, and mobile visual review at
390 x 844. The date selector, B1/M2 switch, map synchronization, and responsive
map tabs were exercised before deployment.
