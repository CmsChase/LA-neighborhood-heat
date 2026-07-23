# Research Protocol v0.3

Status: **development protocol**. Target-QA rules were revised once after the
pre-model pilot and are now locked for full development-data construction.
Model, feature, split, and reporting details must be frozen before any 2025
target value is accessed.

## Research question

How accurately can public weather, land-use, topographic, and lagged
non-thermal satellite features predict Census-tract daytime land-surface
temperature and relative surface-heat hotspots in the City of Los Angeles on
unseen warm-season dates and in unseen spatial areas?

## Scope and estimand

- Study area: official City of Los Angeles boundary.
- Reporting unit: the City-clipped part of a fixed 2020 Census tract with at
  least 50% of its polygon area inside the city boundary. Census special-use
  tracts whose codes begin with `98` are excluded from the primary neighborhood
  manifest and retained for a sensitivity analysis.
- Frozen counts: 1,110 tracts in the mother manifest; excluding 14 special-use
  tracts leaves 1,096 primary analysis units.
- Observation: one tract on one qualifying Landsat 8/9 local overpass date.
- Development data: warm seasons (May–October) in 2020–2024.
- Frozen final test: warm season in 2025.
- Prediction origin: 00:00 America/Los_Angeles on target date `d`.
- Primary target: median valid Landsat daytime LST in °C within a tract-date.
- Neighborhood endpoint: target LST minus the same-date city median.
- Hotspot endpoint: deterministic same-date top 20% of retained tract labels.

The estimand is predictive performance for observable clear-sky daytime
surface heat. It does not estimate individual exposure, air temperature, heat
illness, mortality, or a causal effect of changing land cover.

## Landsat target contract

Only Landsat 8/9 Collection 2 Level-2 Tier-1 `L2SP` scenes may create labels.
All eligible scenes in the same physical overpass are grouped by platform and an
acquisition-time cluster of at most 15 minutes. WRS positions must form a
connected set of adjacent paths/rows. Scenes are quality-mosaicked on a fixed
30 m grid whose raster edges have the Landsat UTM phase `15 + 30k` metres; the
date-level union footprint must cover at least 98% of the City. Same-local-date
distinct overpasses fail closed.

Overlapping pixels are selected deterministically by QA-valid status, lower
`ST_QA`, greater `ST_CDIST`, and then lexicographically smaller scene ID. The
union footprint and selected valid pixels are counted once. A pixel inside the
source raster but lacking a valid ST retrieval remains covered with missing LST;
source-raster exterior is identified independently of band values and combined
with `QA_PIXEL` fill bit 0.

Scene-level metadata cloud cover is recorded but is not a primary discovery
filter because it includes land outside Los Angeles. A `<15%` scene-cloud cohort
is retained only as a sensitivity analysis. Dates are selected by prespecified
calendar and local QA rules, never by observed temperature.

Pixel inclusion requires all of the following:

1. Valid `ST_B10` digital number in the official 293–61,440 range.
2. `QA_PIXEL` does not flag fill, dilated cloud, cirrus, cloud, shadow, snow, or
   water.
3. `ST_CDIST ≥ 1.0 km` from the nearest cloud.
4. `QA_RADSAT` does not flag terrain occlusion.
5. Converted LST is finite and within the broad diagnostic range −30 to 80 °C.

`ST_QA` is a continuous measurement-uncertainty estimate, not a valid/invalid
flag. It is not hard-thresholded in the primary target. Its tract median and
90th percentile are stored as label-quality metadata and are prohibited from
the model feature matrix.

A 2020 ESA WorldCover mask, with permanent-water class 80 removed and
categorical `mode` resampling onto the locked grid, freezes the eligible-land
denominator before any target date is inspected. For every GEOID, both the
static eligible-pixel count and the hash of the exact eligible pixel identities
must be identical on every date. Dynamic `QA_PIXEL` water flags may invalidate
a date's pixel but may not change its denominator.

A tract-date label is retained only when:

- scene footprint covers at least 90% of its rasterized pixels;
- valid pixels cover at least 60% of its eligible non-water land pixels; and
- at least 20 delivered 30 m pixels remain.

The delivered 30 m pixels are not interpreted as independent 30 m thermal
measurements because the TIRS thermal information is natively coarser. Missing
targets are never imputed.

Primary QA is the grid cell `(no ST_QA hard threshold, ST_CDIST ≥ 1 km)`.
The prespecified sensitivity grid is the full Cartesian product
`ST_QA ∈ {none, 2, 3, 4, 6 K}` × `ST_CDIST ∈ {0, 0.5, 1, 2 km}`, producing
20 specifications per pilot date. QA rules may not be selected using model
scores or visually preferred temperature patterns.

Absolute tract LST may be retained whenever its tract QA passes. Same-date city
anomaly and relative hotspot labels are constructed only when at least 80% of
the fixed tract manifest has labels, the retention-rate gap across both
target-independent latitude and longitude quartiles is at most 20 percentage
points, and every eligible cell in the fixed 4 × 4 latitude-by-longitude table
containing at least 20 tracts retains at least 60%. The hotspot label contains
exactly `ceil(0.20 × n)` retained tracts, ranked by LST descending with GEOID
ascending as the deterministic tie-break. It is a relative spatial rank, not an
absolute danger or health threshold.

## Predictors and timing

- Weather: Daymet maximum/minimum air temperature, vapor pressure, shortwave
  radiation, precipitation, and prespecified 1-, 3-, and 7-day lag summaries.
- Land use: the latest prespecified public land-cover and imperviousness vintage
  whose reference year is strictly earlier than the target year; exact products
  and release dates must be frozen in the Phase 2 feature registry.
- Geography: elevation, slope, and distance to coast.
- Satellite: Sentinel-2 non-thermal NDVI, EVI, NDWI, NDBI, and an albedo proxy,
  composited only from target day `d−60` through `d−1`.

All primary dynamic predictors end no later than `d−1`. Target-day observed
Daymet values are prohibited from the primary model. A separately labeled
contemporaneous sensitivity analysis may use them, but it cannot support a
forecasting claim. Historical observed inputs make the primary analysis a
one-day-ahead hindcast, not an operational weather forecast.

Landsat thermal bands, LST, target-derived climatologies, same-date target
summaries, target QA statistics, hotspot labels, tract identifiers, and any
statistic fitted using held-out targets are forbidden predictors. Social and
demographic variables are excluded from the physical prediction model and may
be joined only after prediction for error auditing or a labeled screening
overlay.

## Baselines and models

- Calendar seasonality is the deterministic pair `calendar_doy_sin` and
  `calendar_doy_cos`, where
  `theta = 2*pi*(dayofyear-1)/(365+is_leap_year)`. These are known at the 00:00
  target-date prediction origin and are not observed target-day data.
- B0: inside each training fold, calculate one mean LST response per independent
  overpass date, then fit an unregularized intercept plus the two calendar
  harmonics with every training date weighted equally. The date means are never
  predictors or reusable climatology columns.
- B1: 21 lagged Daymet features and two calendar terms with Ridge regression.
- B2: 18 prespecified static land-use/geography features and two calendar terms
  with Ridge regression. It contains no target-derived tract climatology or
  tract ID.
- M1: Elastic Net with all 46 prespecified model features.
- M2: histogram gradient-boosted trees with all 46 features as the main
  nonlinear model. Its primary loss is absolute error and internal random-row
  early stopping is disabled.

For B1/B2/M1/M2, fold-local sample weights are `N/(D*n_d)`, giving every
training overpass date equal total loss weight. Static/calendar missingness and
an all-missing dynamic training feature are hard failures. Only observed dynamic
weather/Sentinel values are median-imputed from the training fold, with no
missingness indicators. Ridge and Elastic Net scaling is also fit only inside
the applicable training fold.

Deep learning and unrestricted model shopping are outside this protocol.

## Validation and leakage control

- Temporal validation uses five leave-one-calendar-year-out folds for
  2020–2024.
- Spatial validation uses 71 leave-one-existing-5-km-block-out folds. The
  target-independent block assignment is recomputed from fixed tract centroids
  in EPSG:3310 and must agree exactly with the tract manifest.
- Joint validation is the full 5 × 71 Cartesian set. A joint test set is the
  held-out year AND held-out block. Training uses other years and excludes every
  fixed tract whose City-clipped polygon lies within or exactly 1,000 m of the
  held-out block union in EPSG:3310. All remaining rows are explicitly purged.
- Hyperparameter tuning leaves one remaining calendar year out inside each
  outer training set. Outer test and purged rows never enter inner training or
  validation.
- Imputation, scaling, selection, dimensionality reduction, calibration, and
  tuning are fitted only on the applicable inner-training rows, then the chosen
  pipeline is refit on the complete outer-training set.
- Every model feature must pass a fail-closed feature registry and lineage audit.
- All rows from 2025 are exclusively `test_2025` and cannot enter exploration,
  preprocessing, feature selection, tuning, or threshold selection.
- Predictions are stitched into one unique out-of-fold prediction per legal
  tract-date row within each validation family. Metrics are computed on the
  stitched predictions; metrics from the 355 differently sized joint folds are
  not averaged.

Before final evaluation, `MODEL_LOCK.json` must record the Git commit,
configuration hash, split-manifest hash, feature-registry hash, fitted-pipeline
hash, model parameters, primary metric, hotspot rule, and planned figures. The
final evaluator loads the frozen model and never calls `fit`.

## Metrics and uncertainty

Primary performance is date-macro MAE: calculate tract MAE within each date,
then give every acquisition date equal weight. Mandatory secondary results are:

- pooled RMSE and pooled out-of-sample R²;
- pooled and date-macro mean signed error, defined as prediction minus
  observation;
- date-macro within-date anomaly MAE, centering observations and predictions
  separately within each date;
- median per-date Spearman correlation, with constant or otherwise undefined
  dates counted and reported rather than assigned a value of zero;
- hotspot precision-recall AUC, recall, precision, and false-negative rate;
- sensor-stratified error, missingness, residual maps, and residual spatial
  autocorrelation.

Every performance table reports tract-date row count, independent physical
overpass-date count, and independent spatial-block count. Row count is never
presented as the number of independent samples.

Confidence intervals resample complete acquisition dates and spatial blocks,
not individual tract rows.

## Success criteria

- At least 30 independent usable warm-season dates.
- At least 10% joint spatiotemporal date-macro MAE improvement over the strongest
  legal baseline, with block-aware uncertainty supporting improvement.
- Median per-date Spearman correlation at least 0.50.
- Every leakage, provenance, schema, and model-lock check passes.

Failure to meet these criteria is a valid negative result and does not authorize
changing the split or repeatedly searching models against the final test.

The minimum-date criterion counts post-mosaic, post-tract-QA unique usable
physical overpasses. Metadata scenes or pre-QA dates do not satisfy it.
