# Next Experiment Preregistration Draft

> **Status:** design draft only. This is not a protocol lock, does not authorize
> new target access or model fitting, and does not alter the completed three-city
> result.

## 1. Why a new experiment is justified

The completed transfer experiment produced a useful but inconclusive result:
M2 reduced equal-city/equal-date MAE by 28.916% overall, but only 28 city-dates
were usable, M2 degraded in Phoenix, and the Los Angeles-calibrated uncertainty
system failed. The next experiment should address those observed failure modes
instead of repeating the same pipeline on more cities.

The strongest scientific continuation is therefore not a live heat-map product.
It is a new target-blind experiment testing whether a multi-source,
weather-anchored model transfers absolute temperature and spatial pattern more
reliably to genuinely unseen cities.

## 2. Recommended research question

**Can a multi-source model that separates city-date temperature level from
neighborhood-scale spatial anomaly improve daytime land-surface-temperature
prediction in unseen cities while maintaining calibrated uncertainty under
geographic covariate shift?**

The target remains QA-filtered daytime Landsat land-surface temperature (LST).
It must still be described as surface radiative temperature—not near-surface air
temperature, personal heat exposure, or health risk. Recent urban-temperature
research likewise stresses that satellite LST and screen-height air temperature
are physically distinct, even though LST is useful for detailed intra-urban
analysis ([Zhao et al., 2026](https://www.nature.com/articles/s41467-026-73716-7)).

## 3. Proposed study roles

### Source cities and labels

- Los Angeles: 2020–2024 source observations from the completed source lane.
- Phoenix, Houston, and Chicago: their already opened 2025 observations may be
  reused only as **source/training data in this new experiment**.
- Their previous external-test outcome remains frozen and must never be relabeled
  as a new independent test.

### Candidate unseen test cities

Recommended primary set:

1. Seattle, Washington — marine climate and strong topographic/water influence.
2. Denver, Colorado — high elevation and semi-arid continental setting.
3. Atlanta, Georgia — humid, highly vegetated urban form.
4. Miami, Florida — tropical/coastal humidity and water-dominated boundaries.

Before any target href or QA/value access, a metadata-only feasibility audit
must confirm census support, non-water WorldCover support, Landsat 8/9 scene
coverage, and at least 16 candidate March–November acquisitions per city. If a
candidate fails that audit, replacements must come in fixed order from Dallas,
Minneapolis, Portland, and Baltimore. The final city set must be locked before
any new-city target is opened.

## 4. Models to compare

### B1 — public-weather/calendar baseline

Retain a simple regularized baseline using public weather, calendar, and broad
geographic variables. It remains a diagnostic comparator rather than a
deployment candidate.

### M2-L — locked legacy transfer model

Carry the completed 46-feature M2 specification forward unchanged as a legacy
comparator. Do not tune it using the completed three-city outcome.

### M3 — recommended primary model

Decompose each source target as:

`LST(city, date, tract) = city-date level + neighborhood anomaly`

- The **city-date level model** learns the absolute thermal level from public
  meteorology, season, elevation, latitude, and city-wide summaries.
- The **neighborhood anomaly model** learns the zero-centered within-city/date
  spatial pattern from land use, terrain, distance, and lagged Sentinel-2
  features.
- At prediction time, the anomaly component is centered to zero within each
  unseen city-date before it is added to the predicted city-date level.

This directly tests the main diagnosis from the first experiment: one nonlinear
model may have mixed transferable neighborhood pattern with non-transferable
city-wide temperature offsets. M3 changes both representation and training
structure, so its improvement must be attributed to the full pipeline rather
than to one individual feature group.

## 5. Target quality rules

The formal target remains the USGS Landsat Collection 2 Level-2 ST product. USGS
documents the ST scaling rule and warns of retrieval errors near clouds and
possibly cloud shadows
([USGS Surface Temperature](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature)).
The official product guide defines `ST_QA` as surface-temperature uncertainty in
Kelvin with a `0.01` scale factor
([USGS Landsat 8–9 Level-2 Product Guide](https://www.usgs.gov/media/files/landsat-8-9-collection-2-level-2-science-product-guide)).

Before opening unseen-city targets, the new protocol must freeze:

- the existing cloud, cloud-shadow, snow, saturation, cloud-distance, valid-pixel
  count, valid-fraction, and date-retention rules;
- one additional source-derived `ST_QA` eligibility threshold, selected only by
  leave-one-source-city-out validation;
- a rule that every reported table includes row, independent-date, and spatial-
  block counts;
- a separate descriptive sensitivity table for all candidate dates, without
  allowing that table to replace the primary analysis.

Increasing the number of candidate dates—not weakening QA after outcomes are
seen—is the preferred way to meet the sample-support gate.

## 6. Model selection without test leakage

All development must use only the four source cities. Use nested
leave-one-source-city-out validation:

1. hold out one entire source city;
2. train on the other three cities;
3. evaluate absolute LST, spatial anomaly, ranking, interval coverage, and
   retention on the held-out source city;
4. repeat for every source city;
5. choose one fixed M3 specification and one fixed uncertainty method;
6. refit on all source data;
7. commit predictions for every unseen-city tract-date;
8. only then authorize the single combined unseen-city target claim.

No unseen-city LST, Landsat thermal band, target QA table, derived target
statistic, or partial metric may be read before the prediction commit.

## 7. Uncertainty redesign

The previous Los Angeles-only conformal correction did not transfer. The
recommended replacement is source-city cross-conformal calibration with an
optional covariate-shift weight estimated only from unlabeled predictor rows.
Weighted conformal prediction is specifically designed for settings where train
and test covariate distributions differ, provided the density ratio can be
estimated from unlabeled test covariates
([Tibshirani et al., 2019](https://papers.neurips.cc/paper_files/paper/2019/hash/8fb21ee7a2207526da55a679f0332de2-Abstract.html)).

The exact density-ratio model, clipping range, folds, nominal coverage, interval
construction, and abstention rule must be selected on source-city validation and
locked before unseen targets open. If weighted conformal is unstable in source
validation, preregister unweighted multi-source conformal as the fallback rather
than inventing a rule after the test.

## 8. Prespecified evaluation

Primary metric:

- equal-city/equal-date MAE for absolute daytime LST;
- primary effect `R = 1 - MAE_M3 / MAE_B1`.

Primary confirmation requires every component:

1. `R ≥ 10%`;
2. crossed date × 5 km block bootstrap 95% lower bound `> 0`;
3. M3 does not degrade relative to B1 in any unseen city;
4. at least 40 usable city-dates overall and at least 8 per city.

Reliability requires every component:

1. overall nominal-90% coverage between 85% and 95%;
2. coverage at least 80% in every city;
3. retention at least 60% in every city;
4. accepted-set MAE at least 10% lower than all-prediction MAE.

Secondary outcomes are within-date Spearman correlation, hotspot average
precision/recall, spatial-anomaly MAE, signed bias, WIS, city/date error plots,
and risk-coverage curves. They cannot rescue a failed primary gate.

## 9. Ablations needed for a defensible conclusion

Run these only inside source-city validation, then freeze their reporting:

- weather/calendar only;
- weather + static land use/geography;
- weather + lagged Sentinel-2;
- full 46-feature legacy M2;
- M3 level component only;
- M3 anomaly component only;
- full M3.

This is necessary because the completed M2-versus-B1 comparison changed both
algorithm and feature set; it could not identify which feature family caused an
improvement or degradation.

## 10. Execution order and stopping rule

1. Freeze this design after city/support feasibility review.
2. Build predictors for source and candidate test cities without targets.
3. Build/reuse source targets and run source-only nested validation.
4. Lock M3, QA threshold, uncertainty method, metrics, and figures.
5. Fit once and commit unseen-city predictions.
6. Authorize all unseen cities as one indivisible target claim.
7. Evaluate once and publish pass, fail, or inconclusive exactly as observed.

Stop and request an explicit design decision before step 1 if the target changes
from LST to air temperature, the proposed test cities change, or the primary M3
decomposition is rejected. None of those choices may be inferred from the
completed target outcomes after the new protocol is locked.

## 11. Competition and product roles

For a science competition, the strongest story is the complete cycle:

`initial hypothesis → rigorous blind test → honest failure analysis → redesigned
mechanism → new independent test`.

The website should remain a communication layer. A real-time or nationwide tool
can be a later engineering extension, but it should not replace independent
scientific validation and must not translate LST directly into personal health
risk.

## 12. Recommended decision

Proceed with the M3 decomposition and the four recommended unseen cities while
keeping daytime LST as the target. First perform only the metadata/predictor
feasibility audit; do not build or open new targets until a new append-only
protocol lock exists.
