# Project handoff

Last updated: 2026-09-14 Asia/Shanghai

## Current direction: LA model-optimization stage complete (2026-09-14)

This stage is closed and model search is paused. The existing 23-feature
relative-temperature specification remains the stage default; no candidate was
promoted and no model was retrained during closure. Existing absolute-temperature
outputs remain available under their original contracts and limitations. In
particular, the standalone 23-feature relative artifact does not contain an
absolute city-day level, while the LA strict-forward development diagnostic
formed its secondary absolute output by adding a fold-refit B1 level.

The concise, score-separated record is
`docs/LA_MODEL_OPTIMIZATION_STAGE_SUMMARY.zh-CN.md`. It distinguishes LA local
strict-forward development, four-source whole-city LOSO, the separate frozen
46-feature M2 LA 2025 holdout, and the separate full-M3 four-city blind test;
their scores must not be presented as a continuous upgrade curve. The final
relative model contract, completion record, local ignored artifact and
reproduction entry are cross-referenced there.

All permissions in `manifests/multicity/ACTIVE_STAGE.json` remain false. LA 2025
stays frozen, opened external cities cannot become new blind tests, and no new
target may be read without a new explicit authorization. Future date counts and
collection durations in the planning protocol are scenario estimates under
specific variance, serial-correlation and QA-yield assumptions. They are not a
prerequisite for completing this stage and do not prescribe a fixed waiting
period. Model optimization may restart only for a clear new research question,
new prediction-time-legal information, or genuinely new validation conditions.

Sections below are chronological evidence records, not active instructions.

## Planning record: future LA date protocol complete; collection not authorized (2026-09-14)

The next-date development and independent-confirmation protocol is documented
in `docs/LA_FUTURE_DATE_DEVELOPMENT_CONFIRMATION_PROTOCOL.zh-CN.md`, with a
machine-readable draft at
`configs/la_future_date_development_confirmation_v1.toml`. The draft does not
authorize downloading data, reading new targets, or fitting a model. Its
sample-size and elapsed-season values are planning scenarios only; the current
stage has now closed independently of any future collection.

## Completed record: LA measurement-support diagnosis (2026-09-14)

Model search is paused. The one-shot, read-only diagnostic in
`experiments/la_measurement_support/` tested whether changing valid-pixel
composition under the unchanged QA4K target contract materially changes LA
relative temperature. Its pairing, exact common-support rule, coverage gates,
0.20 C priority threshold, and stopping rule were written before pixel values
were read. It acquired no data, fitted no model, changed neither QA nor the
eligible-land denominator, and read neither LA 2025 nor external-city targets.

The local cache was sufficient: 177 georeferenced scenes over 90 dates retain
surface temperature, QA_PIXEL, ST_QA, cloud distance, QA_RADSAT, source
coverage, fixed tract zones, and fixed eligible land. Valid-pixel locations are
not present in the tract summary but were reconstructed exactly from the
authenticated rasters. Rebuilding each original tract target from its own-date
pixel mask matched the formal target table with maximum absolute difference
0.0 C.

The fixed common-support comparison retained 41,106 of 44,571 paired scored
rows (92.23%), 46 dates, and 69 of 71 spatial blocks. Annual retention was
93.40%, 94.38%, and 89.15% for 2022--2024. The equal-date/equal-block mean
absolute change in relative target was only 0.01339 C, with date-bootstrap 95%
interval [0.00897, 0.01834] C; annual values were 0.01165, 0.01288, and
0.01572 C. The unchanged model's matched-row relative MAE changed from 1.11001
to 1.11172 C under the sensitivity target. No year approached the prespecified
0.15 C annual threshold, so changing valid-pixel support is not a priority under
this fixed mechanism. This does not rule out all measurement error or establish
weather causation.

The 44,571-row pairing denominator is not the full 47,142-row OOF set. The
2,571-row difference is exactly the three dates left unpaired by the locked
greedy, nonoverlapping adjacent-date rule: 1,085 rows on 2022-09-03, 787 on
2023-05-17, and 699 on 2023-10-24. The common-support gate is the later step
from 44,571 to 41,106 rows. Consequently the diagnostic applies only to support
composition on the 46 paired same-WRS dates, not the three unpaired dates or
dates that had already failed the original date QA.

There is no untouched QA-eligible LA date in 2020--2024: the 16 usable dates in
2020--2021 entered training, and all 49 usable dates in 2022--2024 have been
reused for development. Dates that failed the locked date QA cannot be relabeled
to create an independent set. LA 2025 remains frozen. The next safe research
stage is therefore to preregister prospective, complete LA date cohorts: one
new development cohort if a single scientifically motivated dynamic candidate
is proposed, followed by a nonoverlapping one-time confirmation cohort. Each
cohort should accumulate at least 30 usable dates under unchanged QA and retain
pixel masks, observation geometry/quality, and verifiable predictor publication
times. Do not restart spatial correction, Tmax-window search, or broader model
search. See `docs/LA_MEASUREMENT_SUPPORT_DIAGNOSTIC.zh-CN.md`; machine-readable
evidence is under `exports/LA_MEASUREMENT_SUPPORT_DIAGNOSTIC/`.

## Completed record: fixed LA d-1 Tmax-gradient route stopped (2026-09-14)

The single adaptive candidate in `experiments/la_tmax_gradient/` is complete.
It added exactly one feature to the unchanged 23-feature relative HGB:
tract d-1 Daymet Tmax minus the same-date median over the complete fixed 1,096-
tract LA prediction universe. This is a city-background temperature difference,
not a spatial derivative. The median was computed before joining QA/scored rows
and used neither targets nor QA. The prior dynamic train-fold median imputation,
model algorithm, hyperparameters, weights, forward splits, target, and scoring
definitions were unchanged; there was no candidate or weather-window search.

The local Daymet audit covers 98,640 rows and 90 dates. Every prev_1d source
start and end equals target date minus one day, and the one-day windows are
complete, so there is no calendar offset. No publication/release timestamp is
available to prove pre-target product release. The feature therefore supports
historical hindcast reconstruction only, not an operational real-time forecast
claim.

The same 47,142 QA4K rows, 49 dates, and 71 blocks were tested strictly forward:
2022 trained on 2020--2021, 2023 on 2020--2022, and 2024 on 2020--2023. Relative
MAE changed from 0.95308 C to 0.93850 C, a 1.53% improvement, below the fixed 5%
gate. The paired date x block gain interval was [-0.02978, 0.06772] C. The
candidate worsened 2022 by 0.02166 C, improved 2023 by only 0.00106 C, and
improved 2024 by 0.06945 C, so the gain is not stable across years. Full-support
centered relative MAE changed from 0.97985 to 0.96735 C, hotspot recall from
0.68439 to 0.68012, and B1-level-anchored absolute MAE from 1.84858 to 1.82772 C.
Secondary non-degradation checks passed, but the 5% primary gate and positive
bootstrap lower-bound check failed.

The experiment is closed under its stopping rule. No full-history candidate
model was fitted or saved, the existing 23-feature model remains default, and
the d-1 Tmax gradient must not be replaced with another window, transformation,
or interaction using the same outer years. This failure rejects only this fixed
candidate, not all weather information. The result is adaptive development
evidence because 2022--2024 residuals selected the feature; forward fitting does
not remove that adaptation. No LA 2025 or external-city target was read, no data
was acquired, and QA was not changed. See
`docs/LA_TMAX_GRADIENT_DEVELOPMENT.zh-CN.md`; generated evidence is under
`exports/LA_TMAX_GRADIENT/`.

## Completed record: bounded LA residual diagnosis (2026-09-14)

The read-only diagnostic in `experiments/la_residual_diagnostics/` is complete.
It reused the fixed 47,142 strict-forward OOF rows from 49 dates and 71 spatial
blocks in 2022--2024. It did not fit or select a model, acquire data, change QA4K
or its scoring cohort, or read LA 2025 or external-city targets. Because these
years have already informed development, this is exploratory evidence rather
than a new independent confirmation, and tract-date rows were not treated as
independent replicates.

The six-neighbor statistic is now explicit: within each date it is the Spearman
correlation between each observed tract's signed OOF residual and the mean
residual among its six nearest other observed tracts. There are no self edges or
duplicate neighbors within a focal tract, although neighbor sets overlap. The
observed median date correlation is 0.890. In 200 within-date permutations that
reassigned residuals among observed tract positions while retaining the graph
and missingness, the median was -0.002 and the 95% range was [-0.018, 0.013].
The spatial pattern is therefore not explained by the obvious computational
artifact, but this does not identify a physical mechanism or reopen spatial
model tuning.

Signed residual decomposition does not support a fixed-neighborhood correction.
The city-date mean signed-error magnitude averages 0.240 C, while the within-date
centered residual MAE remains 0.947 C. A tract's all-date median residual maps
only 8.3% of total residual variance, and annual tract patterns have pairwise
Spearman 0.165, 0.351, and 0.158; block-level values are 0.070, 0.251, and
-0.173. This is consistent with strong date-specific spatial fields and weak
long-term persistence, and the prior fixed coarse spatial candidate remains
stopped.

Existing target-scene quality fields do not provide a cross-year-consistent
explanation of absolute errors. Median/p90 uncertainty, cloud distance, and
valid fraction all miss the prespecified signal rule; source-scene count and
footprint fraction are constant. Landsat platform is confounded with date,
each overpass/scene set is unique to a date, and view/sun geometry is absent.
These fields remain diagnostic-only and may not be used to filter the scoring
set or as predictors.

All 21 existing Daymet fields are approximately 1 km tract summaries whose
windows end at d-1. Three pass the prespecified cross-year direction rule. The
two day-length windows are redundant stable-location proxies and should not be
used after the spatial-route stop. The actionable signal is d-1 maximum
temperature: its within-date residual Spearman medians are 0.232, 0.129, and
0.325 for 2022, 2023, and 2024, its median within-date SD is 1.97 C, and it is
not in the current 18-static + 5-lagged-Sentinel relative model.

The single evidence-based recommendation is therefore A: if the user authorizes
another fit, freeze one candidate that adds only a train-fold-defined,
train-standardized within-LA `daymet_tmax_c_mean_prev_1d` gradient to the current
relative model. Keep the current model as default and the absolute level model
fixed; reuse the same forward splits, rows, weights, centering, and 5% promotion
gate, with no search over weather windows, models, or spatial scales. Do not add
day length. Any result would remain iterative development evidence requiring a
future independent year. See `docs/LA_RESIDUAL_DIAGNOSTICS.zh-CN.md`; generated
outputs are under `exports/LA_RESIDUAL_DIAGNOSTICS/`.

## Completed record: LA coarse spatial-residual experiment stopped (2026-09-14)

The bounded experiment in `experiments/la_spatial_residual/` is complete. It is
explicitly iterative development evidence, not an independent confirmation,
because the prior 2022--2024 results informed its design. The historical/global
feature registry still forbids raw coordinates. A documented LA-local exception
allowed only a five-term quadratic basis of public EPSG:3310 tract centroids in
a residual corrector; the existing 23-feature model and B1 absolute level were
unchanged. Ridge alpha 1000, six-neighbor diagnostics, thresholds, and stopping
rules were fixed before residual inspection. No LA 2025 or external-city target
was read.

Each outer training window used only strict-forward OOF residuals from years
inside that window. Per-date local residual structure was strong (median fixed
six-neighbor Spearman 0.867--0.869), but same-tract annual bias was not reliably
stable: 2021 versus 2022 Spearman was 0.0439, and the full-history median across
year pairs was 0.1631. Only the 2024 outer fold passed the diagnostic, fewer than
the two active years required for promotion. On the same 47,142 rows, 49 dates,
and 71 blocks, the candidate relative MAE was 0.95313 C versus 0.95308 C for the
existing model (-0.005% improvement). The paired date x block interval was
[-0.00556, 0.00503] C. Hotspot recall moved 0.6844 to 0.6862, full-support MAE
0.97985 to 0.97864 C, and absolute MAE 1.84858 to 1.84860 C. Promotion failed;
the existing model remains default, and this spatial-correction route stops
without further scale or regularization tuning.

The failure does not establish an accuracy ceiling. Strong within-date spatial
residual agreement argues against purely independent noise, while weak cross-
year tract persistence argues against a fixed neighborhood correction. The next
useful information is target-before-date local weather gradients and atmospheric
or acquisition context, plus an audit of existing uncertainty, cloud-distance,
and valid-support fields to distinguish a missing dynamic spatial field from
spatially correlated measurement/support error. Do not start a new model search
or reuse the same outer years to tune spatial scale. See
`docs/LA_SPATIAL_RESIDUAL_DEVELOPMENT.zh-CN.md`; generated outputs are under
`exports/LA_SPATIAL_RESIDUAL/`.

## Completed record: Los Angeles strict-forward local accuracy (2026-09-13)

The user explicitly prioritized improving relative neighborhood LST accuracy
in already observed cities, beginning with Los Angeles, while retaining an
absolute-temperature output. This does not reopen LA 2025 and does not treat
the failed cross-city absolute transfer as a local accuracy ceiling.

The fixed bounded experiment in `experiments/la_local_accuracy/` is complete.
It used only LA QA4K observations and predictors from 2020–2024. Outer tests
were the complete 2022, 2023, and 2024 years; each candidate choice used the
immediately preceding year and trained only on still earlier years, after
which the chosen candidate was refit on every year strictly before the outer
test. Fold preprocessing was train-only. No tract-date rows were randomly
split, and neither LA 2025 nor any external-city target was read.

The three candidates were fixed before fitting: the existing 23-feature
relative HGB, an 18-feature stable-spatial HGB without lagged Sentinel, and a
fixed 1:1 blend. The training-period tract median anomaly was evaluated only
as a diagnostic baseline; tract ID and target history never entered candidate
features. Across 47,142 scored rows, 49 independent dates, and 71 fixed 5 km
blocks, the forward selected procedure reached 0.94151 C relative MAE versus
0.95308 C for the refit existing model (1.21% improvement). Its paired date x
block gain interval was [-0.03009, 0.05046] C. The fixed 5% promotion threshold
and positive lower-bound checks failed. Hotspot recall, full-support centering,
and anchored absolute MAE did not materially degrade, but the model is not a
reliable upgrade and the existing relative model remains the default.

The main useful result is structural: the train-only tract-history diagnostic
reached 0.94694 C versus 2.01023 C for zero anomaly, and the descriptive median
pairwise date Spearman was 0.8185 across 2,080 date pairs. Stable neighborhood
structure is therefore strong, but simply removing/shrinking Sentinel did not
capture it reliably. The next evidence-based small experiment should add a
target-free spatial smooth or hierarchical structure to the same fixed
strict-forward LA validation, without tract ID or historical target statistics
as candidate features. Do not repeat the completed candidate set or expand to
new cities. See `docs/LA_LOCAL_FORWARD_ACCURACY.zh-CN.md`; generated outputs are
under `exports/LA_LOCAL_FORWARD_ACCURACY/`.

## Completed record: relative accuracy development (2026-09-13)

The user resumed accuracy development and explicitly prioritized relative
neighborhood temperature while retaining absolute-temperature outputs. This
supersedes the earlier explanation-only pause. The fixed, source-only experiment
is in `experiments/accuracy_development/`; runtime status is
`exports/RELATIVE_ACCURACY_DEVELOPMENT/status.json`. It compares the existing
23-feature relative HGB, B1, a 46-feature weather-context relative HGB, and a fixed
blend using nested whole-city validation. No opened stress-city values or LA 2025
enter this experiment. Old frozen results and models remain historical evidence.

Completed on 96,061 rows, 132 city-dates (254 spatial blocks). The fixed blend
has descriptive source LOSO anomaly MAE 1.34269 C versus existing relative
1.36059 C. The nested selection procedure achieves 1.35615 C (0.33% gain),
with paired crossed date/block gain interval [-0.01666, 0.02373] C. Chicago
worsens by 0.08209 C, and hotspot recall falls from 0.46065 to 0.44366.
The fixed promotion gate failed: do not call this a reliable improved model.
Anchored absolute MAE is 3.56578 C for the selected procedure versus B1
3.65776 C; absolute improvement was secondary and not independently confirmed.

Full-source relative_blend is saved as a research candidate under
`exports/RELATIVE_ACCURACY_DEVELOPMENT/development_model.joblib`, with signature
and model hash in provenance/model metadata. `predict.py --city los_angeles_ca`
exported 98,640 complete-support historical prediction rows with relative,
anchored absolute and B1 absolute columns. These fitted-source outputs are not
validation estimates. Use nested_oof.parquet for validation. Five focused tests,
touched Python lint, baseline reproduction, finite outputs, complete-support
centering and model reload checks passed.

See generated `docs/M3_RELATIVE_ACCURACY_DEVELOPMENT.zh-CN.md` for results and
hotspot diagnostics. Source experiment checkpoints are resumable, but no further
candidate changes should be made against these results. Next resolve whether
the practical priority is improved local accuracy in an observed city or transfer
to an unseen city; those require different validation. Adding existing weather
features alone has not delivered a reliable upgrade. The prior explanation review
in `docs/M3_EXPLANATORY_REASSESSMENT.zh-CN.md` remains historical analysis, not
the current instruction to stop model development.

## Current research review milestone (supersedes stale resume notes below)

The M3 four-city evaluation and public result are complete in the current
`7955c7b` checkout. See `reports/M3_BLIND_EVALUATION_REPORT.md` and terminal
completion `383742cb17674c508e8a7dfe853caa163ab2bd8d0816e150e6f4d966e2334a26`.
M3 failed its primary comparison: MAE 5.8343 C versus B1 3.8032 C over 9,502
rows, 66 city-dates, and 68 blocks. The four opened cities cannot provide
another blind confirmation or confirmatory retuning.

A read-only research review is recorded in `docs/M3_FAILURE_REVIEW.zh-CN.md`.
Its reproducible local diagnostics are in `exports/M3_FAILURE_REVIEW/` (ignored).
The frozen level model has a positive elevation coefficient of 0.0088051 C/m;
Denver's approximately 1,630 m city median is well beyond the approximately
371 m maximum QA-4K training city-date median. Its centered elevation term is
approximately +12.94 C, making level extrapolation the leading mechanism to
test, not a proven causal explanation. Source outer OOF already favored B1
(4.2059 C versus M3 5.2259 C; 96,904 rows, 134 city-dates, 254 blocks).
The review also identified training/prediction aggregation-support mismatch
and the absence of a B1 performance requirement for model promotion.

The approved fixed source-only 2-by-2 diagnostic is now implemented and
complete. It used only QA-4K data already on disk for LA, Phoenix, Houston, and
Chicago, with Ridge alpha 10 and the 31-leaf anomaly model fixed. Variant A
exactly reproduced the historical fixed-candidate MAE of 4.5795588 C. On the
same 132 city-dates and 96,061 rows, B1 achieved 3.6578 C; A/B/C/D achieved
4.5796/4.7568/5.0814/5.1595 C. No variant passed the fixed development gate.
Aggregate anomaly MAE remained informative at 1.3631 C versus B1 1.7146 C,
although Phoenix alone was slightly worse than B1 on that secondary metric.
The absolute-temperature route is paused under its predefined stop rule.
An immediate no-refit screen across all eight already available cities found
lower M3 anomaly MAE in seven of eight cities; equal-city descriptive means
were 1.1804 C for fixed-spec M3 and 1.5576 C for B1. Phoenix was the lone
exception by 0.0099 C. Source LOSO and previously opened-city results retain
their different historical roles, so this combined screen is directional,
not a new confirmation estimate.

That screen has now been replaced by a harmonized, reproducible relative-
temperature review. Without refitting or network access, it recomputed both
evidence roles from row-level predictions using the same city-date centering.
Across the four source cities, M3 reduced anomaly MAE from 1.6965 C to 1.3606 C
(19.8%); across the four opened historical stress cities it reduced anomaly MAE
from 1.4007 C to 0.9976 C (28.8%). Paired city/date hierarchical bootstrap 95%
intervals for B1 minus M3 were [0.0886, 0.5828] C and [0.1835, 0.7355] C,
respectively. Seven of eight cities improved; Phoenix degraded by 0.0097 C.
The fixed development gate passed, but LA and Phoenix still had lower M3
Spearman than B1. This is development evidence, not a new blind claim. See
`docs/M3_RELATIVE_TEMPERATURE_REVIEW.zh-CN.md` and
`experiments/m3_relative_temperature/`.

`ACTIVE_STAGE.json` now records this completed development review. The next safe
research task is now fixed in `experiments/m3_relative_temperature/fixed_contract.toml`.
It contains one anomaly-only 31-leaf HGB, no level model, no candidate search,
and a source-only leave-one-year-out stability audit. That 18-fold audit is now
complete on 132 city-dates and 96,061 rows. M3-relative achieved 1.0241 C
anomaly MAE versus B1 1.6008 C (36.0% improvement); the paired hierarchical
bootstrap interval for B1 minus M3 was [0.1832, 0.8616] C and all fixed gates
passed. This checks temporal stability, not unseen-city transfer, because most
folds retain other years from the held city. The single fixed development model
has now been fitted on all four source cities: 96,061 training rows, 132 city-
dates, and 23 fixed spatial features. Its serialized-reload check passed on all
253,632 source predictor keys with zero city-date medians. Completion is tracked
at `manifests/multicity/reviews/m3_relative_temperature/M3_RELATIVE_DEVELOPMENT_MODEL_COMPLETE.json`.
Do not resume historical queues, acquire a new city, or enlarge the model
search. The opened Seattle, Denver, Atlanta, and Miami cohort may be used only
as development stress-test evidence, never as another blind confirmation.

The public research narrative is now consolidated into one continuous page at
`/m3/`: the earlier Phoenix/Houston/Chicago transfer, the four-city blind M3
failure, the Denver mechanism diagnosis, and the relative-temperature
development result appear in chronological order without changing their
scientific roles. The legacy `/cities/` route reuses the same page so old links
remain valid instead of preserving a competing summary. The Los Angeles
interactive atlas remains at `/`. The GitHub Pages export, evidence hashes,
both routes, and the merged narrative are covered by `atlas/tests/static-export.test.mjs`.

## Project summary

This repository contains a completed Los Angeles neighborhood-scale historical
land-surface-temperature study and its public atlas. The continuation asks
whether the same public weather, land-use, geography, and lagged Sentinel-2
predictors transfer to Phoenix, Houston, and Chicago without using those
cities' target values during development.

Canonical repository: `https://github.com/CmsChase/LA-neighborhood-heat`

Public atlas: `https://cmschase.github.io/LA-neighborhood-heat/`

The website source is `atlas/`. The old standalone atlas repository is archived.

## Historical M3 blind-prediction stage (closed)

The full four-city offline Sentinel/static assembly is complete and
authenticated at
`bc56348b43c12f2d9e17cf67831190e47f1e3c49df1ad90caecd2f0b1a623f42`.
All 16 city tasks and all 12 GSHHG chunks completed. Static output rows are
177/175/173/128 and Sentinel tract-date rows are 9,558/5,425/4,844/3,840 for
Seattle/Denver/Atlanta/Miami, each with exactly 18 static and 5 Sentinel model
features. Network/href reads were zero and blind targets remained sealed.
The 24-task blind Daymet acquisition has parent authorization
`bbf7c4e1b24639d4ad4ff857c04492d296b51486cfbe0651142ac073c166c6ae`.
Its independent bearer adapter is authorized at
`71ef9df08f29cd5110af060e315645f617f7c9e00963c561d7194948217b78ea`.
All 24 frozen city-variable subsets completed and authenticate at
`e3269d965a97c88a43db82c61fa8930a19bf6aa00873efb981317817f84cba1a`.
The adapter accepted the Earthdata token only from the process environment,
sent it only to the locked HTTPS Earthdata OPeNDAP host, refused redirects,
cleared it after the run, and did not persist credentials or URLs. Sentinel,
static, Landsat, QA, targets, fitting, prediction, and scoring were not read or
performed under this permit. Independent offline compilation authorization
`378bfea1bd3bca1f9c9cf24ef23f243f01b1eb38c5b3630353b6f1f34e660484`
binds both upstream completions and the exact compiler code. It permits only
authenticated static/Sentinel/Daymet/support inputs and writes one resumable
46-feature output per city. Network/href reads, Landsat, QA, targets, fitting,
prediction, and scoring remain forbidden. The next safe action is to run
`scripts/run_m3_blind_predictor_daymet_compilation_v1.py --run`.

The first launch stopped before producing a city output because the upstream
static/Sentinel parquet records omit a path and are relative to their locked
city directories. Path-only repair authorization
`aed45c08882a122eda3fb55d724ea6092a6d84b9fb04d426520a7c3e1b5ed3da`
retains the original byte/hash checks and changes no scientific value, key,
schema, or feature. Resume only through
`scripts/run_m3_blind_predictor_daymet_path_repair_v1.py --run`.

That retry reached the semantic gate and showed Seattle has 108 complete-key
rows (two tracts across 54 dates) with all 21 Daymet features missing because
the coastal Daymet grid has no valid cell there. All other gates passed. The
frozen protocol already specifies source-fold median imputation plus a missing
indicator, so support-repair authorization
`1fcee278574e99ee14976b10d0c82bb7dbc083ff9a18d267c4b4cb0b6c2e4698`
requires preserving these NaNs without filling or dropping rows and records
their counts. Resume only through
`scripts/run_m3_blind_predictor_daymet_support_repair_v1.py --run`.

The full four-city predictor compilation is now complete and authenticated at
`efd100881992273fd17101b6687e84a9a24cb00d52726eec489ba9b2331b3661`.
It contains 23,667 unique tract-date rows with the exact 46-feature schema:
9,558 Seattle, 5,425 Denver, 4,844 Atlanta, and 3,840 Miami. The completion
records 228 rows with all 21 Daymet features missing and 1,907 rows with all
five Sentinel features missing; no row was dropped or imputed. Network/href
reads were zero, blind targets stayed sealed, and no fit, prediction, score, or
evaluation occurred.

Independent prediction authorization
`9c22ff5a3bd2c8b016c47281207da8b0779c3e1b1b48f1438a51e37399b8958d`
was created and pushed before the first predictor/model value read. The frozen
source-selected M3 model then produced all 23,667 four-city point predictions.
Review found that v1 completion
`4d90bca2ae6aa28233a05e3e0419b165e8c11844843bb93489b4c4d74837f20b`
contained 14 rather than the protocol-exact 21 columns, so it is retained only
as a superseded audit record. Append-only repair authorization
`1213ab79192bb2f22736bcc886473fc61589c6eafa8ec4dff73ba34b08bef4e8`
allowed source-only fitting of the already-frozen B1, legacy M2, and four-member
M3 diagnostic ensemble. The corrected 21-column predictions authenticate at
`295ccca0ea0239cf6eb5633b736a7565abbac3cc47992bae6bcbc9ba4d6f74ac`.
No blind Landsat thermal, QA, or target read occurred. The prediction-before-
target boundary remains intact. The next safe stage is a new independent blind
target-access and evaluation authorization; do not open target values before it
authenticates.

## Completed milestones

1. The Los Angeles development study, frozen 2025 evaluation, evidence package,
   reports, and interactive atlas are complete.
2. Public Census geography and WorldCover eligible-land support are complete for
   Los Angeles, Phoenix, Houston, and Chicago.
3. Real Sentinel-2 calibration smoke evidence is complete for all three external
   native UTM zones: Phoenix 12, Houston 15, and Chicago 16.
4. All 15 missing-support/calibration evidence outputs are tracked and
   authenticated. The terminal internal commit is
   `1251247b821093a3208b5dc0da65b5e2ae2969e14030da83666fbd65e45e6d07`.
5. Phoenix public source metadata was refreshed against the new canonical city
   boundary. It found 3 Landsat units, 4 Sentinel units, 1,462 Daymet cells, and
   2 terrain tiles without opening raster or target values.
6. The final portable predictor contract is locked in
   `manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json`.
7. The target-blind predictor key inventory is frozen: Los Angeles has 90
   dates, Phoenix 22, Houston 21, and Chicago 21. Across 2,902 city-specific
   tracts this yields 136,941 predictor rows without reading target assets.
8. A stable, target-blind component runner and localhost progress page are
   implemented and tested. Its 84 durable work units cover four calendar
   tables, four static bases, 49 GSHHG distance chunks, four static finalizers,
   Los Angeles Daymet compilation, 18 external-city Daymet downloads, three
   external-city compilations, and one final component merge.
9. That 84-unit build is complete. It produced all 41 non-Sentinel predictors
   for 136,941 frozen city-date-tract rows. The merged Parquet has 46 columns:
   five keys plus the 41 frozen static, calendar, and Daymet features. No model
   was fit and no external-city target values were opened.
10. The four-city Sentinel metadata inventory is frozen and authenticated:
    Los Angeles has 226 physical acquisitions, Phoenix 116, Houston 113, and
    Chicago 56 (511 total). Houston's UTM-14 zero-support candidates remain in
    audit lineage but are not raster contributors.
11. The resumable Sentinel engine and localhost dashboard are implemented and
    tested. The dashboard exposes 6/8 asset-read threads separately from 1/2
    complete-acquisition concurrency, defaults to 6/1, pauses only after a
    durable acquisition boundary, retries failed acquisitions, and restarts an
    unexpectedly exited engine.
12. A copy-ready gaming-laptop folder is staged at
    `exports/GAMING_LAPTOP_SENTINEL`. Its 3,410 manifest-tracked files total
    370,521,440 bytes. Independent verification found zero missing, extra, or
    hash-mismatched files. The manifest SHA-256 is
    `41819b6054b10a708e1644349ef31e361fd6d2db89b0ef26599c1a8cfafea449`.
    Package-local engine check returned `ready`; dashboard smoke returned
    `paused` with no engine process. The complete Windows PowerShell first-run
    path also passed after the quoting repair.
13. The gaming-laptop Sentinel run completed all `516 / 516` durable units:
    511 physical acquisitions, four city compiles, and one final merge. The
    copied return ZIP passed a complete CRC audit and was imported into this
    primary project without rerunning acquisitions.
14. The formal return receipt is
    `manifests/multicity/returns/PORTABLE_SENTINEL_RETURN.json`. The completed
    predictor table contains 136,941 rows and 46 features; its SHA-256 is
    `31b472b53f11a69c8a2d44dfc927ed46162db0c076ef644693233cea4e026b0f`.
    The target-blind readiness audit is `ready_for_protocol_lock_not_model_fit`
    with 73,432 LA training rows, 25,208 LA calibration rows, and 38,301 sealed
    external-city prediction rows.
15. The continuation-specific target-blind 5 km spatial partition is complete
    in EPSG:5070 for all 2,902 tracts: Los Angeles has 71 blocks, Phoenix 59,
    Houston 88, and Chicago 36 (254 globally unique city-prefixed blocks).
    Although Los Angeles again has 71 blocks, this is not the Phase-I EPSG:3310
    partition and no Phase-I block assignment is reused. The committed control
    record is `manifests/multicity/evaluation/SPATIAL_BLOCKS.json`.
16. The fixed transfer-model core is implemented and synthetic-tested. It
    builds the exact 23-feature B1 diagnostic, 46-feature point M2, and q05/q95
    M2 pipelines; enforces LA 2020-2023 training, LA 2024 calibration, and the
    complete three-city 2025 external prediction cohort; and implements the
    frozen equal-date weighting, CQR correction, and strict-greater-than
    abstention rule. No real target was read and no real model was fit.
17. The target-blind four-city aggregation contexts are complete. They attach
    the frozen 5 km blocks to all 2,902 tracts in canonical raster-zone order
    and authenticate each city's 30 m target grid, geography, WorldCover mask,
    and block assignment. The control record is
    `manifests/multicity/targets/TARGET_CONTEXTS.json`. This step opened no
    Landsat asset href, thermal band, QA band, or target table.
18. The still-unauthorized target build plan is complete and reproducible. It
    contains 159 durable units: 154 overpass targets, four city compiles, and
    one final merge. Los Angeles 2020-2024 is a separate 90-date source lane;
    Phoenix, Houston, and Chicago form one indivisible 64-date external 2025
    cohort that requires one later append-only claim. The plan freezes only
    item IDs and metadata relationships; all asset hydration and target-value
    access remain unauthorized. Its record is
    `manifests/multicity/targets/TARGET_BUILD_PLAN.json`.
19. The complete four-city software path now has a deterministic in-memory
    rehearsal. It exercises the frozen LA training/calibration interface,
    target-blind three-city prediction, mechanical leave-one-city-out folds,
    evaluation tables, uncertainty diagnostics, and a figure. Every artifact
    is explicitly synthetic and non-evidence, and project-local output is
    restricted to `.tmp/`.
20. The public Atlas includes a static `/cities/` route. It began as a sealed
    null-result preview and now publishes only the authenticated aggregate
    evaluation, gate outcomes, and six evidence figures. It explicitly labels
    the scientific outcome `inconclusive_sample_size` and distinguishes evidence
    authentication from successful confirmation.
21. The four-city pre-fit protocol/model specification is append-only locked in
    `manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json`, commit
    `c93cee9d7d05194dd75fe8dba662ae1d5b9ee2a8e1401178a1a9c0fc8675304f`.
    It freezes cohorts, exact B1/M2 feature order and parameters, LA-2024 CQR,
    the equal-city/equal-date primary metric, all success and reliability gates,
    the 10,000-replicate crossed bootstrap, output schema, planned figures, and
    code/input identities. This lock did not read any target value or fit a model.
22. The separately authorized Los Angeles lane completed all 90 overpasses and
    one compile, yielding 98,640 source tract-date keys. Frozen B1/M2/CQR fitting
    then committed 38,301 predictor-only external predictions without opening
    external targets. The indivisible Phoenix-Houston-Chicago claim subsequently
    completed all 64 overpasses and three city compiles; the frozen evaluator,
    report, post-hoc QA audit, and public Atlas release all authenticated. No
    target dashboard or worker is currently required.
23. The follow-up M3 experiment's target-blind, label-free city feasibility
    audit is complete and authenticated. Seattle, Denver, Atlanta, and Miami
    all passed, so no replacement city was used. The audit found 177/175/173/128
    primary Census tracts and 54/31/28/30 eligible independent 2025 Landsat
    dates, respectively; every selected tract had positive WorldCover valid
    non-water support. The terminal record is
    `manifests/multicity/next_experiment/METADATA_FEASIBILITY_AUDIT.json`, commit
    `450ecd604000fcec7f3958e9a15013c74f69c52fddd656e9786c703c62838922`.
    It read no new-city Landsat asset href, thermal or QA value, target table,
    model, prediction, or evaluation metric.
24. The follow-up experiment's append-only M3 development protocol is locked in
    `manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json`,
    commit `dfa2cd5231f5153ef92a100bafc6a32cd2798cb5f10c5a8b6ebbd759086bbee8`.
    It freezes the four source and four blind-test cities, B1/M2-L and four M3
    candidates, pixel-level ST_QA candidate rules, nested whole-city LOSO,
    uncertainty and abstention selection, evaluation gates, and the 21-column
    prediction schema. The candidate space is locked, but no winning M3 model
    has been selected, no source-only selection has run, and no new value was read.
25. A pre-access source-acquisition amendment proved that the old source support
    could not satisfy the locked eight-date inner-city gate, then fixed a complete
    2020-2025 warm-season expansion for Houston and Chicago without selecting on
    QA outcomes. The assets-excluded public metadata inventory is complete: 318
    source overpasses and 525 unique city-scenes (Los Angeles 90/177, Phoenix
    22/44, Houston 102/200, Chicago 104/104). The two-phase cache/offline-QA
    authorization is authenticated, and its 3,474-task durable queue is initialized
    and paused. No Landsat asset href or pixel was opened by these preparation
    steps; the four blind-test cities remain sealed.
26. The source-only QA rebuild and support gate completed successfully. The
    gaming-laptop predictor extension was returned and authenticated in the
    primary project: four source cities, 253,632 tract-date keys, and exactly
    46 predictors. The next-stage executable source-only UQ/risk pseudo-test
    path passed 34 focused tests and lint. Independent append-only joint LOSO
    authorization commit
    `358c8f63f65932c1ac17914b95015ae59194c2a42adc0e40e765016d2b68d773`
    was created without opening a predictor or QA Parquet. It permits only the
    frozen 4 QA × 4 M3 nested whole-city LOSO and source-city pseudo-tests;
    Seattle, Denver, Atlanta, and Miami remain sealed.
27. The source-only nested whole-city LOSO, UQ, and risk stages completed and
    authenticated under commit
    `207d45f8fdc7237f6347ed69b1c67733df353a3331e622707e93c4b3f21c34d3`.
    The frozen selection is QA `4k`, M3
    `level_ridge_alpha_10__anomaly_hgb_leaves_31`, unweighted cross-conformal
    UQ, and accept-all risk. Metadata-only parent authorization
    `1a704fca3848471dfba16c28bf2dd2e282343af6ac2aa24e3cbbd2ef44d790f8`
    freezes four blind cities, 143 target dates, 23,667 tract-date keys, and the
    exact 46-predictor contract. It permits implementation and review only;
    predictor values and network access remain prohibited until a reviewed
    child runtime authorization is created.
28. The blind-predictor support and public metadata substages are now complete.
    Support commit
    `aa0c35e626e84ab1e8a17e04b8c5da374c3fcd655a601c90b2e8256806d74dbd`
    reconstructs the four canonical grids with zero network access. Metadata
    commit `31dcc3f639ccd8a4af040be20dd5ced243cadf7a913b53639a9e7311b7966201`
    freezes 23,667 predictor keys and assets-excluded Sentinel plus Daymet
    granule metadata. Exact Sentinel inventory commit
    `7052a02df4da25661ea29cb9b5862bd71921ce1e57017a73db87e8f9ca4b10d7`
    selected 539 physical acquisitions (Seattle 150, Denver 157, Atlanta 157,
    Miami 75) after reading only exact-item href metadata. No Sentinel/static
    raster or Landsat/QA/target value was opened. The authorized 24-task Daymet
    acquisition remains 0/24 because NASA returned 401 and no ephemeral
    Earthdata token is present; it is not being retried automatically.
29. The public repository presentation is refreshed without changing the active
    scientific boundary. The README now shows the completed source-only M3
    selection, current blind-predictor stage, and prediction-before-target rule
    in plain language. Cross-platform setup and provenance guides live in
    `docs/REPRODUCING.md` and `docs/PROVENANCE.md`; Python CI runs public code tests
    on Ubuntu and Windows with project-local temporary files. Completed Windows
    launchers are archived under `tools/windows/legacy/`. The historical
    `START_M3_PREDICTOR_GAME_LAPTOP.cmd` was initially retained at the root because
    its exact path and SHA-256 are bound by an append-only authorization. It is
    now archived byte-for-byte under `tools/windows/legacy/` as well; the Windows
    tools guide documents optional restoration to its original, Git-ignored
    local path. Historical authorization and runner code remain unchanged.
    No root `.cmd` launchers remain tracked. Ruff
    and the complete pytest suite passed on the original workstation after the
    organization change. Fresh-clone CI then exposed a missing temporary parent,
    shallow Git history, ignored evidence dependencies, a runtime-specific test
    hash, and Rasterio 1.5.1 opener incompatibility. The CI repair uses a direct
    project-local `.tmp/pytest-ci` directory with its parent created first, full
    history, an explicit opt-in local-evidence
    test lane, an invariant-based hash test, and the tested Rasterio 1.5.0 version.
    No scientific implementation, evidence bytes, or permissions were changed.
    The clean-worktree check passed 1,624 tests with 52 explicitly skipped local
    evidence audits and zero failures. Commit `a966661` then passed both Ubuntu
    and Windows Python 3.12 jobs in GitHub Actions run `33698172511`.
30. Independent blind-city Sentinel/static acquisition scope authorization
    `2543bf7e29b6a76cc1099cd60f03644d669104c6597c0ed54db8e4cf3e83f633`
    is append-only and authenticated. It freezes Seattle, Denver, Atlanta, and
    Miami; 143 dates and 23,667 keys; 539 exact Sentinel acquisitions; the 18
    static plus 5 lagged-Sentinel features; fixed NLCD, SRTM, and GSHHG sources;
    output paths; and low-load concurrency limits. Its builder was tested under
    guards that reject opening or statting CSV, Parquet, TIFF, and ZIP files.
    Therefore this milestone performed zero predictor-value and network reads.
    It is a scope permit, not a launch permit: the next step must implement and
    review the resumable runner, then create a separate code-bound runtime-launch
    authorization before the first Sentinel/static value or network access.
31. The exact resumable Sentinel/static acquisition runtime is implemented,
    reviewed, and bound by append-only launch authorization
    `ef99c4816e0153f04185fea9a27a1ed8661b6cf2dc6fe740d31e834d121f3ef7`.
    Its frozen plan has 553 durable units: 539 exact Sentinel acquisitions and
    14 static-source units across the four blind cities. The runtime permits at
    most two asset-read threads and one active acquisition, authenticates the
    permit immediately before each value or network read, never authorizes
    Daymet, Landsat, QA, targets, fitting, prediction, scoring, or evaluation,
    and requires a one-static plus one-Sentinel canary before full acquisition.
    Authorization generation and authentication opened or statted zero value
    files and made zero network reads. At that authorization milestone, the
    canary had not started.
32. The required Sentinel/static canary completed successfully. It durably
    finished exactly one static-source unit and one Seattle Sentinel physical
    acquisition, leaving 551 of 553 work units. Its runtime completion commit is
    `f77229430416c48013368e0652c288bbdcd007896f05a3fe394a250e73e10bdc`.
    The post-canary status closes network permission and records no Daymet,
    Landsat, QA, target, fit, prediction, score, or evaluation access. The next
    safe step is to resume the same runner over the remaining 551 acquisition
    units; do not rebuild or delete the two valid canary completions.
33. Blind-city Sentinel/static acquisition is complete and independently
    reauthenticated. Completion commit
    `33cf3e03ba8bbc1c1de5abb6c976ca50cbeb2a93ea8207e24e520d7c9f30f2c2`
    binds all 553 durable units: 14/14 static tasks and 539/539 Sentinel
    physical acquisitions (Seattle 150, Denver 157, Atlanta 157, Miami 75).
    The terminal runtime has zero pending/running tasks, no worker, network
    permission closed, and no Daymet, Landsat, QA, target, fit, prediction,
    score, or evaluation access. Offline Sentinel/static assembly is not yet
    authorized and has not started.

## Frozen scientific decisions

- For the completed Phoenix-Houston-Chicago experiment, M2 is the only primary
  transfer model and uses all 46 frozen predictors.
- In the follow-up experiment, M3 is a locked candidate family awaiting
  source-only selection; it does not revise the completed M2 result.
- B1 remains a fixed 23-feature weather/calendar benchmark. It is not a
  deployment or model-selection candidate.
- All four cities use the new same-adapter Census 2020 geography and WorldCover
  2020 v100 valid non-water support.
- Los Angeles keeps the same 1,096 GEOIDs, but its new zone assignment differs
  from Phase I in 6,872 cells. Therefore Los Angeles 2020-2024 predictors and
  Landsat target aggregation must be rebuilt on the new support; Phase-I pixel,
  mask, feature, or already aggregated target tables cannot be reused directly.
- WorldCover defines the eligible support mask only; it is not a predictor.
- Sentinel reflectance calibration comes only from official product metadata
  XML using `(DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE`, exactly once.
  Missing STAC `raster:bands` scale/offset fields are not a blocker.
- City ID, tract GEOID, and raw coordinates are keys or metadata, never model
  inputs.

## Current permission boundary

Completed and authenticated:

- authenticate the completed public predictor table and its evidence;
- authenticate the append-only protocol/model specification lock;
- authenticate the completed LA 2020-2024 source-target table;
- authenticate the completed frozen model fit and committed external predictions;
- the one indivisible Phoenix-Houston-Chicago target claim;
- the frozen external evaluation, six-figure evidence package, and verified Atlas release.
- the target-blind four-city feasibility audit and append-only M3 development
  protocol lock.
- the append-only source-acquisition amendment, Houston/Chicago assets-excluded
  metadata expansion, and source-cache/offline-QA execution authorization.

Still prohibited:

- refitting, recalibrating, or selecting models from external outcomes;
- replacing the frozen confirmatory result with a post-hoc sensitivity result;
- claiming successful cross-city confirmation or calibrated uncertainty.
- reading any Seattle, Denver, Atlanta, or Miami Landsat asset href, thermal or
  QA value, or target table;
- running nested LOSO, M3 fitting, or model/QA selection; the current permit
  stops after the four source ST_QA candidate tables are rebuilt;
- building new-city predictors, fitting the final M3, or creating predictions
  until later staged permissions explicitly authorize each step.

The single active control record is
`manifests/multicity/ACTIVE_STAGE.json`. Historical numbered transition modules
through V18 remain for provenance and must not be extended with V19/V20 files.

## Historical resume point (closed)

Current resume point (supersedes the historical repair-blocker narrative
retained below): the public-repository organization milestone is complete and
does not advance scientific permissions. Blind support, keys, public metadata, and exact Sentinel
inventories are complete at the commits in milestone 28. The next safe action
is to implement, review, and independently authorize resumable Sentinel and
static predictor-value acquisition. The already-authorized Daymet acquisition
may resume only with an in-memory Earthdata token; do not persist the token or
loop on 401. No blind-city Landsat asset href, thermal/QA value, target table,
fit, prediction, or score is authorized.

Historical pre-return context follows for provenance only.

The public predictor phase is complete: static/calendar/Daymet reached `84 / 84`,
Sentinel reached `516 / 516`, and the final table is 136,941 rows by 46 frozen
features. Do not rebuild or re-import these products.

The exact tracked stage is
`m3_source_development_paused_upstream_asset_integrity_blocker`. The control
page is <http://127.0.0.1:8772/> and the durable runtime status is
`data/interim/multicity/m3_source_development/runtime/status.json`. The queue
contains 3,151 online cache tasks followed by 323 offline QA tasks. Dynamic
progress belongs only in that ignored runtime status, not in tracked documents.
The queue is deliberately paused with no active leases. Exactly three frozen
source assets are blocked because their Planetary Computer blobs contain a
persistent 12,351-byte HTML error payload rather than GeoTIFF bytes. The
append-only repair incident and authorization preserve this fact and permit
only the exact original product files whose official MD5 values match. Do not
resume repeated downloads, substitute a later processing version, or relax the
source inventory. Existing valid cache contents remain intact, and no blind
city was accessed.
Its frozen predecessor remains the completed three-city external evaluation:
the target claim contains 64 overpasses and three city compiles. The
evaluation used 11,207 QA-valid rows across 28 city-dates and 180 spatial
blocks. M2 reduced equal-city/equal-date MAE from 9.738 °C to 6.922 °C, a 28.9%
improvement with a 95% crossed-bootstrap interval of 14.1% to 43.5%.

The compact offline evidence package can be created once, then reauthenticated,
with:

```powershell
.\.venv\Scripts\python scripts\export_multicity_evidence.py --project-root .
.\.venv\Scripts\python scripts\export_multicity_evidence.py --project-root . --check-only
```

Its default location is `exports/MULTICITY_EVALUATION_EVIDENCE.zip`. It contains
only aggregate/date-level evidence, figures, protocol records, interpretation,
and relevant code; it excludes tract-level scored rows and targets, model files,
runtime state, credentials, and signed URLs.

Reauthenticate the protocol and LA-only authorization with:

```powershell
.\.venv\Scripts\python scripts\lock_multicity_evaluation_protocol.py --project-root . --check-only
.\.venv\Scripts\python scripts\authorize_multicity_source_targets.py --project-root . --check-only
```

The preregistered point gate nevertheless failed: only 28 city-dates survived
the unchanged QA rules, Houston and Chicago each had fewer than eight usable
dates, and M2 degraded in Phoenix. The reliability gate also failed: nominal
90% intervals covered only 45.0% overall and abstention increased rather than
reduced accepted-set MAE. Preserve this result. A clearly labeled, read-only
post-hoc quality audit is complete and does not replace the formal result. The
next research step is to design and preregister a separate future experiment;
it must not overwrite or be presented as the completed confirmatory test.

The already completed spatial partition can be reauthenticated without target
or predictor access using:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_spatial_blocks.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_contexts.py --check-only
.\.venv\Scripts\python scripts\stage_multicity_target_build_plan.py --check-only
```

Do not refit or recalibrate the committed model, rescore the completed external
claim, or reinterpret the post-hoc sensitivity as confirmatory. The prediction
commit preceded the single combined external claim, and all three city compiles
and the one-time evaluation have authenticated.

The selected unseen-city set is Seattle, Denver, Atlanta, and Miami. The
target-blind feasibility audit and the append-only M3 development protocol lock
can be reauthenticated without network access using:

```powershell
.\.venv\Scripts\python scripts\audit_next_experiment_city_feasibility.py --project-root . --check-only
.\.venv\Scripts\python scripts\lock_multicity_m3_development_protocol.py --project-root . --check-only
```

The next safe task is to obtain the three exact original GeoTIFFs through an
authorized USGS/AWS source, or wait for Planetary Computer to restore those
same bytes. The repair runner must verify product identity, TIFF magic, and the
official MD5 before it writes any cache content. Only after the repair
completion authenticates may the original online queue resume, finalize its
cache, and automatically hand off to the offline pixel-level ST_QA rebuild.
The runner uses only Los Angeles, Phoenix, Houston, and Chicago. It cannot start
nested LOSO or access Seattle, Denver, Atlanta, or Miami assets, predictors,
QA, or targets. The final M3 model specification remains unlocked until a
later source-only selection authorization and completion.

Reauthenticate the incident and exact-three-asset repair permit with:

```powershell
.\.venv\Scripts\python scripts\repair_m3_source_assets_v1.py --project-root . --check-incident
.\.venv\Scripts\python scripts\repair_m3_source_assets_v1.py --project-root . --check-authorization
```

Reauthenticate the current follow-up gates without downloading pixels using:

```powershell
.\.venv\Scripts\python scripts\audit_m3_source_support_and_stage_amendment.py --project-root . --check-only
.\.venv\Scripts\python scripts\stage_m3_source_metadata_inventory_v1.py --project-root . --check-authorization
.\.venv\Scripts\python scripts\stage_m3_source_metadata_inventory_v1.py --project-root . --check-inventory
.\.venv\Scripts\python scripts\authorize_m3_source_qa_execution.py --project-root . --check-only
```

Authenticate the completed gates with:

```powershell
.\.venv\Scripts\python scripts\stage_multicity_missing_support_calibration_evidence_v1.py --check-only
.\.venv\Scripts\python scripts\stage_phoenix_source_footprint_restage.py --check-only
.\.venv\Scripts\python scripts\lock_portable_predictor_contract.py --check-only
.\.venv\Scripts\python scripts\build_portable_predictor_inventory.py --check-only
.\.venv\Scripts\python scripts\build_portable_sentinel_inventory.py --check-only
.\.venv\Scripts\python scripts\build_portable_sentinel_features.py --check-only
```

The completed component build uses stable, purpose-named outputs and builds Los
Angeles on the new canonical support rather than copying Phase-I feature
tables. Its focused test set is:

```powershell
.\.venv\Scripts\python -m pytest -q tests/test_portable_predictor_build.py tests/test_portable_predictor_components.py tests/test_portable_predictor_dashboard.py tests/test_portable_predictor_daymet.py
.\.venv\Scripts\python -m pytest -q tests/test_portable_sentinel_build.py tests/test_portable_sentinel_inventory.py tests/test_portable_sentinel_dashboard.py tests/test_create_portable_sentinel_bundle.py tests/test_sentinel_features.py
```

## Working style

- Make direct implementation changes and use focused tests plus one touched-file
  lint pass.
- Avoid numbered hotfix files and redundant full-project audits.
- Preserve all completed manifests and ignored local checkpoints.
- Do not start a long computation without a visible resumable runner.
- Never place credentials, bearer tokens, signed URLs, or cookies in tracked
  artifacts.

## Key references

- `README.md` — project overview and repository map
- `docs/RESEARCH_PROTOCOL.md` — original Los Angeles design
- `reports/FINAL_EVALUATION_REPORT.md` — held-out Los Angeles result
- `docs/RESULTS_WEBSITE.md` — atlas source and deployment
- `docs/MULTICITY_GENERALIZATION_PROTOCOL.md` — cross-city design
- `docs/MULTICITY_METHODS_AND_EVIDENCE.md` — continuation gates and evidence contract
- `docs/NEXT_EXPERIMENT_PREREGISTRATION_DRAFT.md` — recommended failure-driven
  follow-up design; draft only, with no new target authorization
- `configs/multicity/m3_development_protocol_v1.toml` — locked M3 candidate,
  source-selection, uncertainty, risk, and evaluation contract
- `manifests/multicity/next_experiment/M3_DEVELOPMENT_PROTOCOL_LOCK.json` —
  append-only M3 development protocol lock
- `docs/M3_SOURCE_DEVELOPMENT_RUNNER.md` — low-load online/offline runner guide
- `manifests/multicity/next_experiment/M3_SOURCE_ACQUISITION_AMENDMENT.json` —
  fixed pre-access source-support expansion
- `manifests/multicity/next_experiment/M3_SOURCE_EXPANDED_INVENTORY.json` —
  authenticated 318-overpass source inventory
- `manifests/multicity/next_experiment/M3_SOURCE_QA_EXECUTION_AUTHORIZATION.json` —
  two-phase source-cache and offline-QA permit
- `docs/MULTICITY_SYNTHETIC_SMOKE.md` — deterministic non-evidence rehearsal
- `docs/DECISION_LOG.md` — detailed historical decisions
- `docs/DATA_MANIFEST.csv` — public-data provenance
