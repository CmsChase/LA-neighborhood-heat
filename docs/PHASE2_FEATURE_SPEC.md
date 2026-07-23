# Phase 2 Predictor Specification — Complete and Target-Join Audited

Status: **all four predictor families, the target-blind combined table, and the
legal development target join are complete**. Thresholds were frozen from
predictor coverage and provenance diagnostics, never from LST values or model
scores. The 31-candidate model-selection rule and grouped validation are now
frozen before scores; recoverable nested grouped modeling is next, and 2025
remains locked.

## Common contract

- Join key: `tract_geoid × target_date`; `tract_geoid` is never a predictor.
- Prediction origin: 00:00 `America/Los_Angeles` on target date `d`.
- Dynamic source end: no later than local date `d−1`.
- Static support: the frozen City-clipped 1,096-tract manifest and the exact
  WorldCover-derived eligible-land denominator already locked by the target
  stage.
- Missing predictor values are preserved. Any imputer is fit only on a model
  training fold.
- Coverage, observation counts, source age, hashes, coordinates, spatial block,
  and QA fields are audit-only and prohibited from the primary feature matrix.
- Landsat thermal/LST products, target QA, target-derived climatology, same-scene
  optical values, future data, and 2025 rows are prohibited.

## Locked source families

| Family | Product and vintage | Primary spatial support | Time support | Access status |
|---|---|---|---|---|
| Weather | Daymet V4 R1, DOI `10.3334/ORNLDAAC/2129` | Fixed eligible-land weighted 1 km Daymet grid cells within each tract | Complete 1/3/7 civil-day windows ending `d−1` | All 30 official grid subsets downloaded and hash-audited; 21-feature production table complete |
| Land cover | Original NLCD 2016 release, published 2019-04-30 | Fixed eligible-land area-weighted 30 m fractions | Static for every 2020–2024 row | Official MRLC WCS LA subset at native 30 m EPSG:5070; USGS DOI `10.5066/P937PN4Z`; downloaded and audited |
| Imperviousness | Original NLCD 2016 release | Fixed eligible-land area-weighted 30 m statistics | Static | Official MRLC WCS LA subset at native 30 m EPSG:5070; USGS DOI `10.5066/P937PN4Z`; downloaded and audited |
| Elevation/slope | NASA SRTMGL1 v003, observations from February 2000 | Fixed eligible-land area-weighted 30 m statistics | Static | Both required GeoTIFF tiles downloaded from the OpenTopography academic distribution and byte/content audited |
| Coast distance | Census TIGER/Line 2019 U.S. Coastline, Pacific Ocean features | Distance from fixed eligible-land cell centers | Static | Official Census URL attempted first; fixed exact-byte Internet Archive memento downloaded and ZIP/CRC audited after the official endpoint returned 403 in this environment |
| Optical satellite | Microsoft Planetary Computer `sentinel-2-l2a` | Physical-overpass mosaics on a fixed 20 m grid, then valid-area aggregation to the locked 30 m grid and tracts | Acquisitions from `d−60` through `d−1` | All 226 acquisition caches and formal 98,640-row predictor output complete and promoted |

WorldCover 2020 remains the static eligible-land support mask only. It is not a
land-cover predictor because it was observed during the target period.

Implementation checkpoint (2026-07-20): the NLCD, SRTM, and coastline source
commits are complete and valid. The promoted static table contains 1,096 unique
GEOIDs, 18 model features and one audit-only reference category, with no missing
values and 100% observed coverage for all five source layers. The official
Daymet inventory contains 30 granules (2020–2024 × six variables) and zero 2025
entries. All 30 official subsets are downloaded and hash-audited. The completed
compiler produced 21 finite weather predictors for all 98,640 tract-date keys,
using invariant tract-cell weights and windows ending strictly at `d−1`.

The frozen Sentinel inventory contains 226 physical acquisitions, 449 selected
tile items, and 1,045 memberships across all 90 development target dates.
Membership lags are exactly 1–60 days, no global scene-cloud cutoff was applied,
and 2025 remains absent and locked. The processor passed a real calibration
smoke. All 226 complete full-resolution physical-acquisition caches were
independently revalidated and contain 247,696 acquisition-tract rows. Formal
promotion produced exactly 98,640 predictor/audit rows, with 97,870 available
and 770 explicit all-five-null rows, and 1,145,320 lineage rows. The stage is
`complete` with `promoted_outputs_valid=true`.

## Known-at-origin calendar features

Two deterministic seasonal terms are generated on the complete 98,640-key
predictor support:

- `calendar_doy_sin = sin(theta)`;
- `calendar_doy_cos = cos(theta)`;
- `theta = 2*pi*(dayofyear-1)/(365+is_leap_year)`.

They use only the timezone-naive Los Angeles civil target date already present
as a join key. They are known at the 00:00 prediction origin, not observed
target-day data, and therefore have null observed-source offsets. The registry
permits this exception only for the exact paired names and frozen metadata.
Weather, optical, and every other observed dynamic model feature must still end
by `d-1`.

The generated calendar table contains 90 dates × 1,096 tracts, no missing value,
and no 2025 row. Its semantic table SHA is
`9c7cfc530a696ee65b9c0568db83eebb33535e74f46e3d0328e6a29819acb4ca`.

The final combined registry has 46 model features:
18 static, 2 calendar, 21 Daymet, and 5 Sentinel. It also contains two keys and
one audit-only static reference. The target-blind 98,640 × 49 combined table and
registry were frozen under commit
`3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.

## Weather features

Daily gridded values are first aggregated to tracts with fixed eligible-land
weights. Missing grid cells never cause date-specific weight renormalization.
For target date `d`, an `n`-day window contains exactly `d−n … d−1` and requires
all `n` civil days.

- Mean over previous 1/3/7 days: `tmax_c`, `tmin_c`, `vp_pa`, `srad_w_m2`, and
  `dayl_s`.
- Sum over previous 1/3/7 days: `prcp_mm`.
- Sum over previous 1/3/7 days: daily shortwave energy computed cell-first as
  `srad_w_m2 × dayl_s / 1_000_000`, in `MJ/m²`.

The unusual Daymet leap-year calendar is mapped to the Gregorian calendar:
February 29 is retained and December 31 is an explicit missing date. No
interpolation or forward fill is allowed. This analysis is a historical
one-day-ahead hindcast, not an operational forecast.

The production compiler validates all 30 official raw subset records and
hashes, the native WGS84 Lambert conformal conic 1 km grid, variable units,
fill/scale metadata, fixed eligible-land cell weights, exact civil-day windows,
and the 2025 lock. Its completed key-plus-21-feature table contains 98,640 rows
across 90 dates and 1,096 tracts, with separate audit, weight, and provenance
artifacts. All 21 features are complete and zero rows belong to 2025. Its commit
is `a7da6a107695787f047547275669217c6bd508b12852e2d6c244078f687c0ea9`.

## Static land-use and geography features

The primary static vector is identical on every date for a GEOID.

Land-cover fractions use the frozen eligible-land area denominator:

- open water;
- developed open, low, medium, and high intensity;
- barren;
- forest;
- shrub/grass;
- agriculture;
- wetland.

The ten reported NLCD land-cover fractions are exhaustive and summed exactly to
one for every tract. To avoid exact intercept collinearity,
`nlcd_developed_medium_fraction` is retained as an audit-only reference category
and excluded from the model. This target-blind choice was made because it was
nonzero in all 1,096 tracts and had the largest median fraction. No LST value or
model score was consulted.

Continuous primary features:

- impervious mean fraction, P90 fraction, and fraction at least 50% impervious;
- elevation mean and standard deviation in metres;
- slope mean and P90 in degrees, using a fixed Horn 3×3 operator;
- Pacific-coast distance mean and P10 in kilometres.

Each source must cover at least 98% of every tract's frozen eligible-land
support or the stage fails. NLCD land-cover `0`, imperviousness `127`, and SRTM
`−32768` are NoData; imperviousness `0%` is valid. NoData is never recoded as
ocean, zero elevation, or zero imperviousness.

## Sentinel-2 features

Inventory uses physical acquisitions, not STAC items, and resolves reprocessed
duplicates deterministically before any target is read. Adjacent MGRS tiles from
one acquisition are mosaic contributors rather than independent observations.
No global tile-cloud threshold is used.

Required BOA assets are `B02`, `B03`, `B04`, `B08`, `B8A`, `B11`, `B12`, `SCL`,
and product metadata. Reflectance is decoded from each item's quantification
value and band-specific BOA offset; processing baseline 04.00 or later fails if
the required offsets are absent. SCL classes 4 and 5 are the only accepted
clear-land classes.

The processing order is locked. First validate the native 10/20 m grid phase;
then area-average native DNs onto the aligned 20 m grid while propagating any
saturated native pixel with max-mask resampling; then decode BOA offsets and
quantification values; then apply SCL classes 4/5; finally calculate all five
predictors on one joint-valid band mask. Bilinear reflectance mixing is
prohibited.

Per physical acquisition, calculate from decoded reflectance:

- `NDVI = (B08 − B04) / (B08 + B04)`;
- `EVI = 2.5 × (B08 − B04) / (B08 + 6×B04 − 7.5×B02 + 1)`;
- McFeeters `NDWI = (B03 − B08) / (B03 + B08)`;
- `NDBI = (B11 − B08) / (B11 + B08)`;
- `albedo_proxy = 0.2266·B02 + 0.1236·B03 + 0.1573·B04 + 0.3417·B08 +
  0.1170·B11 + 0.0338·B12`, with intercept 0, from Bonafoni & Sekertekin
  (2020), DOI `10.1109/LGRS.2020.2967085`.

Denominator magnitude below `1e−6` produces missing, not a clipped value.
Per-acquisition tract coverage must be at least 80% of the static denominator.
The final five primary optical predictors are tract medians across at least
three qualifying physical acquisitions in `d−60 … d−1`; otherwise all five are
missing. Observation count, coverage, processing baseline, tile IDs, and source
age remain audit-only.

Implementation artifacts are not scientific results. The real baseline-04.00
calibration smoke has semantic SHA
`819417a0ad628c98956025bd5b132e9d8a645343dcb35927eed1acf309240162`;
it read an independent B04/SCL window and is not a model feature. The stage
configuration SHA is
`094dfb394bc45740343351bfaacaf6e558b92e1dd4d40313917756974b0e62b5`
and the processor SHA is
`68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c`.
The completed source/interim composite SHA is
`1114f61188f55258e4dae95c23cbd02d79bd0b60969e1e2d595b13ad2c9c8154`.
The normalized processed feature SHA is
`aa02df3a00c51076610f442512949ade5ca70ab466b4d2d9c513826184fe82b5`,
with promotion commit
`bf3adfffcfe52df7cca7c366fa214d6cb11a5cca4bf1111454c99c87fd48e291`.
The minimum availability is 56.75% across dates and 85.56% across tracts;
31 dates and 537 tracts have at least one explicitly missing Sentinel row.
The primary analysis keeps all otherwise legal rows with training-fold-only
dynamic median imputation. It must report errors stratified by Sentinel
availability and a prediction-level complete-Sentinel sensitivity; neither may
be used to tune models or remove dates after seeing scores.

## Pre-model exit gates

1. Output keys are unique and match development target dates with zero 2025
   rows.
2. Every dynamic lineage row has `source_end_date < target_date`.
3. Perturbing target-day and future inputs changes no feature value.
4. Every static vector is byte-identical across dates for its GEOID.
5. All source versions, access dates, URLs, units, resampling rules, raw hashes,
   processing hashes, grid hashes, and tract hashes are in the data manifest or
   stage provenance.
6. A target-blind coverage report is reviewed and thresholds are frozen before
   any model or target-performance comparison.
7. The final feature registry explicitly separates `model` from `audit_only`
   columns and its SHA-256 is recorded in the decision log.

The target-blind readiness audit has closed these gates for the exact key
universe, registry, and all four predictor families. It reports
`state=ready_for_feature_assembly`, `ready_for_feature_assembly=true`, and
`blockers=[]`, with commit
`92534764be459110ff239670f320e3b947313f344ca00774c3097cff42fa3762`.
It read no target value, target-QA table, or model score.

A separate target-blind assembly then froze the 98,640 × 49 Phase 2 table under
commit `3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.
It contains two keys, 46 model features, and one audit-only field; 97,870 rows
are complete and 770 retain the allowed all-five-missing Sentinel pattern.

Only after that gate passed did the formal target join read the legal
development target table. Its 63,403 × 50 output covers 65 independent dates,
contains 46 model features and one audit-only field, has 63,235 complete
model-feature rows, and contains zero 2025 rows. Its commit is
`9c2f903993167fc2a228b3cfe60a23fe33f57f252bae6299458338cb8eb967ad`.
The grouped-validation manifest is also formally promoted over 63,403 legal
keys, 65 dates, and 71 fixed spatial blocks. It freezes 5 temporal, 71 spatial,
and 355 joint folds (431 total) under commit
`6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc`.
Promotion read only keys and split metadata—no target value, predictor value,
or model score—and preserved the 2025 lock.

These artifacts do not constitute a model fit or performance result. The next
step is to implement and run recoverable nested grouped modeling over the frozen
folds; 2025 remains locked.
