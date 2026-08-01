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

The target-blind source review completed on 2026-07-29 without freezing that
contract. It authenticated the existing Census TIGER/Line 2019 Coastline as
the reproducibility benchmark, but found that its U.S.-only coverage is not
equivalent to unrestricted global-ocean distance for Phoenix. The
scientifically preferred definition is therefore the nearest global ocean
shoreline or one of the five Great Lakes.

The subsequent source-only GSHHG geometry pilot is complete. Its immutable V1
failed before distance calculation on one invalid L1 polygon and the source's
five lake seeds resolving to three L2 connected-water polygons. A
source-structure-only V2 amendment was committed before distance access and
retained all V1 points, thresholds, and locks. Its candidate contract uses L1
exteriors plus those three seed-selected L2 exteriors and excludes L3
lake-island shores. V2 passed every geometry and numerical gate. At the fixed
target-blind Phoenix point, the GSHHG contract gave 262.208 km and the Census
contract gave 482.409 km, a -220.201 km difference. This establishes that the
contracts are not interchangeable, not that either source is positional
ground truth. The Census source remains only a conditional fallback, in which
case the feature must be named and interpreted as distance to a U.S. Census
qualifying shoreline rather than an unrestricted nearest ocean.

At that historical geometry-pilot checkpoint, neither the GSHHG source nor
the algorithm was frozen. See
[`PORTABLE_WATER_DISTANCE_REVIEW.md`](PORTABLE_WATER_DISTANCE_REVIEW.md) and
[`GSHHG_GEOMETRY_PILOT_REPORT.md`](GSHHG_GEOMETRY_PILOT_REPORT.md). The later
L3 audit and V2 decision now freeze the exact source and audited algorithm as
documented in
[`WATER_DISTANCE_FREEZE_DECISION_V2.md`](WATER_DISTANCE_FREEZE_DECISION_V2.md).

## Predictors and timing

The portable predictor set contains:

- nationwide land-cover and imperviousness summaries;
- elevation, slope, and portable water-distance variables only after their
  separate source-and-algorithm freeze;
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

Canonical planning v7 then authorized only the completed water-distance V2
decision. Its append-only terminal is published in commit
`91a31fd9e1793bbfa9c9f751459fc73d0e0bbb4c` and freezes the exact GSHHG
source and point-distance algorithm. A staged v8 transition may copy only
those two locks into canonical planning, close the consumed permission, and
authorize the exact preregistered predictor-source/calibration-contract audit.
The staged implementation has no authority before v8 is committed, pushed,
and authenticated.

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

The subsequent portable water-distance review is also complete and records
state `review_complete_source_not_frozen`. Its audit program made zero
source-data network or download requests, reauthenticated the existing
16,631,608-byte Census 2019 coastline ZIP and all 4,248 `L4150` lines, and
computed no distance values. Candidate documentation was reviewed on the
official websites cited in the review. Its conclusion is conditional because
Census is a reproducible U.S. benchmark but does not represent the globally
nearest ocean for Phoenix.

The target-blind GSHHG geometry comparison is now complete under state
`geometry_pilot_complete_source_not_frozen`. It authenticated GSHHG 2.3.7,
preserved the V1 structural failure, passed the amended V2 gates, and
quantified the Phoenix source-contract difference without reading any target
or target-QA value or producing a feature surface. Its four fixed points test
source semantics and numerical stability only; they do not validate
positional accuracy, neighborhood variation, or a complete distance grid.

The historical first portable water-distance freeze decision completed under
state `decision_complete_freeze_deferred`. It retained GSHHG 2.3.7 as the
candidate source but rejected immediate freeze of the L1/L2-only algorithm
because the four diagnostic points did not close the L3 lake-island shoreline
gap.

The target-blind GSHHG L3 hierarchy audit is now complete. It preserved a V1
structure-phase failure before distance, applied one separately committed
single-character V2 source-identity amendment, selected all 139 direct L3
descendants of the three fixed L2 parents, and passed every structural and
numerical gate. The four fixed city points replayed exactly, while all three
deterministic real-island probes showed material L3 distance reductions. At
that audit checkpoint, this closed the narrow hierarchy question but
created no source or algorithm lock. Its only next safe stage was a separate
portable water-distance source-and-algorithm freeze decision; predictor
construction, model fitting, protocol promotion, and target access remained
separately locked.

Canonical planning v7, published in commit
`252c01d015110336c65bb602d4c5b608708fb092`, only closes the consumed L3
geometry-read permission and authorizes the evidence-only V2 freeze decision.
Its 20,809-byte plan has file SHA-256
`88c153b7c1da9f2f159ac550fd3156a4ffe3fd1f56c269c057288d938a2047f3`
and internal commit
`4f6ed97b64d3a1601da6af83779ec96bef87c77de72d5294475ac029f666110f`.
Completed V2 extracts the seven source-only rows from the exact tracked L3 success
manifest; it does not request or open the ignored diagnostic CSV and reopens
no ZIP, archive member, geometry, eligible support, predictor/model, external
target/QA, or result. Its 18,541-byte terminal, file SHA-256
`a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b`,
freezes only the exact source and audited point-distance algorithm. It does
not freeze tract aggregation or feature names and does not authorize predictor
construction. A separate tracked-only planning v8 must authenticate and
consume the exact V2 terminal, update the canonical plan locks, close the
consumed decision permission, and authorize only the exact preregistered
predictor-source/calibration-contract freeze. Its configuration, runtime
paths, tracked read set, and unique append-only output must be bound by v8
before that audit runs.

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
