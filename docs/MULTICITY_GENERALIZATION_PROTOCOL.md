# Cross-city generalization protocol

Status: **draft pre-registration, version 0.1**

Internal study name: `la_to_three_city_zero_shot_v1`

This document defines a new continuation study. It does not reopen, revise, or
reinterpret the completed Los Angeles 2025 evaluation. In particular, the
known Los Angeles 2025 result is descriptive context and cannot count as new
confirmatory evidence.

## Research question

Can a fixed public-data model trained only with Los Angeles LST labels
generalize to an unseen year in three cities whose LST labels were never used,
and can calibrated uncertainty identify predictions that should be withheld?

The target remains QA-filtered daytime Landsat land-surface temperature (LST).
It is a surface-heat hazard proxy, not air temperature, human exposure, heat
illness, or a health outcome.

## Why this is a substantive continuation

The first study asked whether public features predict tract-scale LST in Los
Angeles. This study asks whether that relationship transfers across climate
and urban form, and whether the model can recognize unreliable predictions.
It is not the original analysis repeated with a larger row count.

The scientific contribution is external generalization and reliability. A
multi-city website will remain a presentation layer until these claims have
been tested.

## City roles

| City | Role | Climate contrast | Target-label status |
|---|---|---|---|
| Los Angeles | sole training and calibration source | Mediterranean coastal | 2020–2024 available; 2025 already known |
| Phoenix | zero-shot external confirmation | hot arid | sealed |
| Houston | zero-shot external confirmation | hot humid | sealed |
| Chicago | zero-shot external confirmation | continental and Great Lakes | sealed |

City identity comes from fixed 2020 Census incorporated-place GEOIDs. The
primary unit is `city_id × 2020 Census tract × physical Landsat overpass date`.
Tracts must have at least 50% of their area inside the place boundary, and
special-use tract codes beginning with `98` remain excluded.

The three external cities are fixed case studies. Results may support claims
about those three environments; they are not a random sample proving
performance in every city.

## Time split and first-access boundary

- Model fitting: Los Angeles, warm seasons 2020–2023.
- Interval calibration and abstention threshold: Los Angeles, warm season
  2024 only.
- Confirmation: Phoenix, Houston, and Chicago, warm season 2025.
- Los Angeles 2025: known Phase I anchor only, excluded from every new test,
  threshold, and success gate.

For external cities, public boundary, scene metadata, land-use, weather, and
legally lagged Sentinel predictors may be staged while targets remain sealed.
Predictions, lower and upper bounds, and abstention flags must be committed
before the first Landsat thermal or target-QA value is opened.

Opening external targets will require a separate model lock, explicit
one-time authorization, one append-only claim, and same-claim recovery. The
draft planning audit does not grant that authorization.

## Target and support contract

The continuation retains the Phase I Landsat 8/9 Collection 2 Level-2 L2SP
mosaic, QA, valid-pixel, fixed eligible-land denominator, and
spatial-representativeness rules unless a pre-target implementation audit
demonstrates a city-independent defect. Any approved portable change must be
documented and frozen before external target access.

All city bounding boxes, Landsat WRS contributors, Sentinel MGRS tiles, Daymet
grid cells, terrain tiles, and source windows must be discovered from frozen
city boundaries and written to authenticated manifests. They may not be
copied from Los Angeles constants.

For the draft Phoenix adapter pilot, Census TIGERweb remains the authoritative
first request. If that host is operationally unreachable, the adapter may use
only the two fixed Esri Demographics 2020 Census item IDs recorded in
`configs/multicity/experiment.toml`. Their published processing notes state
that the boundaries came from the 2020 TIGER/Line geodatabases and that no
vertices were altered. Every selected response and item-metadata response must
be preserved and hashed. This fallback is a pilot snapshot, not the eventual
confirmatory source freeze; a separate parity/integrity review is required
before protocol lock.

The Phase I feature `Pacific coast distance` is not portable. Before predictor
construction, the continuation must freeze a nationwide source and algorithm
for distance to a qualifying ocean or Great Lakes shoreline. The definition
must be target-independent and identical in all four cities.

## Predictors and timing

The portable predictor set contains:

- nationwide land-cover and imperviousness summaries;
- elevation, slope, and the newly frozen portable water-distance variables;
- calendar harmonics;
- 1-, 3-, and 7-day lagged Daymet summaries;
- 60-day lagged non-thermal Sentinel-2 indices.

Dynamic inputs end at `d−1`. Prohibited predictors include Landsat thermal
values, target QA, same-scene optical bands, future inputs, GEOID, city ID,
raw coordinates, target-city LST summaries, and target-city climatology.
Imputation and every learned preprocessing step fit only on Los Angeles
2020–2023.

## Frozen confirmatory comparison

Only one model comparison is confirmatory:

- **B1-Transfer:** Ridge with the two calendar and 21 lagged Daymet features,
  `alpha = 10`.
- **M2-Transfer:** Histogram Gradient Boosting with the portable full feature
  set and the Phase I locked model-class settings: absolute-error loss,
  learning rate 0.05, 300 iterations, 31 leaves, minimum leaf 50, L2 1.0,
  random seed 20260719, and no random-row early stopping.

The fitted Phase I M2 cannot be replayed unchanged because its
Pacific-specific distance feature is not portable. The continuation refits
the same fixed model class on Los Angeles 2020–2023 after the portable feature
registry is frozen. No external-city label may select a feature or parameter.

## Uncertainty and abstention

Lower and upper models use the same HGB structure with 0.05 and 0.95 quantile
loss. Los Angeles 2024 supplies the split-conformal correction for a nominal
90% interval.

The abstention threshold is the 80th percentile of interval width under an
equal-date-weighted Los Angeles 2024 distribution. A prediction is marked
`abstain` when its frozen interval width exceeds that threshold. This rule is
applied unchanged in all three external cities.

Reliability succeeds separately from point prediction only if:

1. overall 90% interval coverage is 85%–95%;
2. coverage in every external city is at least 80%;
3. every city retains at least 60% of predictions; and
4. accepted-set MAE is at least 10% lower than all-prediction MAE.

## Metrics and success gate

For each city and date, calculate tract-level MAE. Give every date equal weight
inside a city, then give Phoenix, Houston, and Chicago equal weight.

The primary effect is:

`R = 1 - external_MAE(M2-Transfer) / external_MAE(B1-Transfer)`

Cross-city point prediction succeeds only if all conditions hold:

1. `R` is at least 10%;
2. the 95% confidence interval lower bound for `R` is above zero;
3. no external city has a worse M2 point MAE than B1; and
4. at least 30 usable city-dates exist in total and at least eight in each
   city.

If the date threshold is not met, the result is `inconclusive_sample_size`.
The window, city list, or threshold may not be changed after opening targets.

Secondary metrics are per-city date-macro MAE, RMSE, signed error,
within-date anomaly MAE, median per-date Spearman correlation, exact top-20%
hotspot average precision/precision/recall/false-negative rate, interval
coverage and width, weighted interval score, retained-set MAE, risk–coverage
curves, and predeclared sensor/Sentinel/heat-event/land-cover/spatial-block
strata.

Uncertainty uses 10,000 city-stratified crossed complete-date × 5 km
spatial-block bootstrap draws. Cities retain equal weight. Census-tract rows
are never treated as independent samples.

## Real-time boundary

Daymet is an observation-based historical product and cannot support a
prediction issued at 00:00 on the target date. Therefore this continuation is
a target-blind historical external test, not an operational forecast.

A genuine current-day or next-day tool requires a separate protocol using
archived and live forecast-time weather such as HRRR, exact issue timestamps,
input-freshness checks, and prospective prediction commitments. That product
work begins only after the external-generalization pipeline is validated; its
results must not be mixed into this confirmation.

## Failure and stopping rules

- Contract, leakage, hash, schema, or fixed-land-support failure stops the
  affected stage.
- Transient network and raster-warp errors may retry with bounded backoff.
- A failed atomic task does not invalidate completed authenticated tasks.
- Missing/clouded dates are recorded, not replaced.
- The complete fixed window is processed; there is no performance-based early
  stop.
- External results cannot trigger retuning, a new threshold, another claim, or
  city substitution.
- Later few-shot adaptation is a separately labeled exploratory study and
  cannot overwrite zero-shot results.

## Execution architecture

All continuation products live under parallel `multicity/` paths. No new code
may write to Phase I `final_test_2025`, `final_evaluation`, model-lock, report,
evidence, or website JSON paths.

The eventual controller will use one SQLite task queue and expose city × stage
progress, cooperative pause, bounded automatic retry, crash recovery, worker
selection, and clear distinction between transient errors and scientific
contract blocks. Expected atomic tasks are:

- boundary/source metadata by city;
- Landsat by physical overpass;
- Daymet by variable × year;
- Sentinel by physical acquisition;
- static features by source;
- models by fixed comparison;
- uncertainty, evaluation, and reporting as separate committed stages.

## Current authorization

At draft version 0.1, the following completed metadata-pilot actions were
authorized:

1. authenticate the completed Phase I evidence;
2. stage official Census boundary and public source metadata;
3. implement and test city-independent adapters;
4. run a Phoenix metadata-only pilot that reads no Landsat thermal or target-QA
   value.

Predictor construction, model fitting, external target access, and an
operational-forecast claim remain locked until their missing source and
protocol contracts are separately frozen.

The Phoenix geography portion of item 4 completed on 2026-07-29. The adapter
found 603 bbox candidates, 389 with positive city overlap, 376 meeting the
50% area rule, and one qualifying `98xxxx` special-use tract; the resulting
primary universe therefore contains 375 tracts, all in county FIPS `013`.
The manifest state is `pilot_complete_source_not_protocol_locked` and records
all target/model/predictor access flags as false.

The Phoenix source-footprint portion of item 4 also completed on 2026-07-29.
The authenticated metadata snapshot records:

- Landsat contributors `WRS2-036037`, `WRS2-037036`, and `WRS2-037037`;
- Sentinel tiles `12SUB`, `12SUC`, `12SVB`, and `12SVC`;
- 1,461 positive-intersection Daymet candidate cells, six granules, and the
  one-cell halo window `y=5814..5888, x=3453..3500`;
- terrain tiles `N33W112` and `N33W113`, verified by `HEAD` only.

The STAC queries returned 67 Landsat and 494 Sentinel items before strict
positive-intersection filtering. Item assets and item links were excluded from
those responses. No STAC asset object or asset href was returned or read, no
signing call or raster GET/payload request occurred, and zero raster bytes,
external target/QA values, predictors, predictions, or models were opened or
constructed. Two terrain objects were checked by metadata-only `HEAD`
requests.

This is `complete_metadata_only_source_not_protocol_locked`: a reproducible
pilot metadata snapshot, not a confirmatory source freeze or protocol lock.
The only safe next task is review of a nationwide ocean/Great-Lakes
water-distance source and an identical target-independent algorithm for all
four cities. That review may not construct predictors. Predictor construction,
source freeze, model fitting, and target access remain separately locked.

## Official source anchors

- U.S. Census Bureau 2020 TIGER/Line:
  https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.2020.html
- Census 2020 tract layer:
  https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/6
- Census 2020 incorporated-place layer:
  https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/26
- Fixed Phoenix-pilot tract mirror item:
  https://www.arcgis.com/home/item.html?id=e3a7d2d3e5834b7eb6b1c2943141ced6
- Fixed Phoenix-pilot incorporated-place mirror item:
  https://www.arcgis.com/home/item.html?id=13ea1fb24ca14842bb265e6ec6ac1d46
- Daymet:
  https://daymet.ornl.gov/
- Landsat acquisition cadence:
  https://landsat.usgs.gov/landsat_acq
- NOAA HRRR:
  https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/hrrr.php
- ISEF continuation and project rules:
  https://www.societyforscience.org/isef/international-rules/rules-for-all-projects/
