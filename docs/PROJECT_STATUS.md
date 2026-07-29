# Project Status — 2026-07-29

This file is a historical scientific summary. Before any work, read
`docs/PROJECT_HANDOFF.md` in full; that document is authoritative for the live
Git, authorization, runtime, and final-test state.

## Outcome so far

Phases 0–6, the frozen development-model run, the formal model lock, and the
one-time 2025 final evaluation are complete. The final transaction is
authenticated under claim
`c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`
and completion commit
`4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`.
All 57,800 grouped development tasks had already finished: 55,645 nested inner
fits and 2,155 selected-candidate outer refits.

On the 15-date, 15,116-row held-out cohort, M2 reduced equal-date-weighted MAE
from 3.1165 °C to 2.1650 °C (30.53%) and reached median per-date Spearman
0.8447. The crossed date-by-block 95% relative-improvement interval was
-10.13% to 58.46%. The point and rank gates passed, but the required positive
lower-bound gate failed; therefore the frozen overall protocol success flag is
false. This is a qualified result, not permission to retune or rerun.

The final read-only evidence export is also complete. Its 239-file directory
and 21,787,327-byte ZIP were independently verified, including all 23 target
cache commits and an isolated clone/fsck of the Git bundle. The external ZIP
SHA-256 is
`61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`;
the tracked attestation is
`manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json`.

## Cross-city continuation — draft planning

A scientifically separate continuation now has a target-locked planning
scaffold. It asks whether a fixed model trained only with Los Angeles
2020–2023 labels transfers to target-sealed Phoenix, Houston, and Chicago in
2025, and whether a Los Angeles 2024-calibrated uncertainty rule can identify
predictions that should be withheld.

The draft protocol is `docs/MULTICITY_GENERALIZATION_PROTOCOL.md`; the
machine-readable audit is `manifests/multicity/PLAN_READINESS.json`, internal
commit
`78cafaa41c4f45f8738e98fb8441e6efcbf0efe19d4a3f1226c874554fc3578d`.
It reauthenticated the Phase I claim, model lock, completion marker, 23 cache
commits, 21 final outputs, and evidence ZIP. It records
`external_targets_unlocked = false` and permits only boundary/public-metadata
staging.

The generic Census place/tract adapter and corrected Phoenix geography pilot
are complete. The target-independent 50% area rule plus one `98xxxx`
special-use exclusion produced 375 primary Phoenix tracts from 603 bbox
candidates. The exact raw responses, GeoParquet hashes, and all-false
target/model access flags are authenticated by
`manifests/multicity/cities/phoenix_az/geography/GEOGRAPHY.json`, internal
commit
`3891c871ab5e5710bf6abdbc8f2a22a5a62db7962ee66bf235e1caee28301fea`.
The source remains a pilot snapshot rather than a protocol lock. No
external-city LST or target-QA value was opened, and no predictor or new model
was built.

The returned archive is `D:\Downloads\FINAL _VER.zip`, SHA-256
`0a07e9e3f016b0ed67a5f00085b0ab74ebd0f5273b58f9cbadbb07aa6ac0a335`.
The earlier extracted directory was missing 2,155 outer fragments because of
Windows long-path extraction, but strict ZIP-level acceptance found and imported
all 2,155 original fragments. Zero fragments were reconstructed. The canonical
import audit commit is
`b0382cdc792d9716ebf13dcfd69256e08a0920bd8ac45a0fe554946ae6640393`.

Initial authenticated result analysis is also complete. In joint
spatiotemporal OOF evaluation, the strongest legal baseline is B1 with MAE
2.516141 °C; M2 has MAE 2.108788 °C, an absolute improvement of 0.407353 °C
and a relative improvement of 16.1896%. The 5,000-replicate paired crossed
date-by-block bootstrap gives a 95% relative-improvement interval of
4.2088%–27.6883%, `P(improvement > 0)=0.995`, and
`P(improvement > 10%)=0.8516`. Joint M2 median per-date Spearman is 0.792785.
The required development gates pass, while the stronger claim that the entire
95% interval exceeds 10% does not. The analysis provenance commit is
`d8bfaf258590d2ef42abe37a73fb92eeba3d66d61f23fd5f85b8e8c5616844c4`.

## Locked primary target

- Unit: City-clipped 2020 Census tract × physical Landsat overpass date.
- Mother universe: 1,110 tracts meeting the ≥50% City-area rule.
- Primary universe: 1,096 tracts after flagging out 14 Census special-use
  `98xxxx` tracts.
- Target: median QA-valid Landsat 8/9 Collection 2 L2SP daytime LST in °C.
- Interpretation: clear-sky surface-heat hazard proxy, not air temperature,
  individual exposure, illness, mortality, or a causal effect.
- Final test: the separate one-time 2025 transaction is complete; the
  development table itself still has zero 2025 rows.

## Verified full-target numbers

| Check | Result |
|---|---:|
| Detailed LA County TIGER features downloaded and checked | 2,498 |
| Frozen mother / primary City tracts | 1,110 / 1,096 |
| Eligible Landsat 8/9 scenes | 178 |
| Physical overpass groups / complete primary overpasses | 91 / 90 |
| Completed primary overpasses | 90 / 90 |
| Absolute-usable independent dates | 65 |
| Relative-endpoint dates | 34 |
| Complete tract-date QA rows | 98,640 |
| Rows with a tract target before date-level filtering | 67,104 |
| Legal absolute-model rows | 63,403 |
| Approximate 5 km spatial blocks represented | 71 |
| Relative anomaly labels / exact hotspot positives | 36,139 / 7,241 |
| Selected valid pixels with scene lineage | 72,709,295 |
| Duplicate tract-date keys / 2025 rows | 0 / 0 |
| Static eligible-count or pixel-identity drift | 0 GEOIDs |

The promoted output hashes are:

- target QA table: `d923b476de3d3ca7e64b13ac0d4fecce743d6684472740bfff8d04f5f77cf2b9`;
- date summary: `2c6d6a597c6b1b321c4f632291c22dfe5788a058fe82fd65114a5a59d5d0be83`;
- scene contributions: `f43aec119ef6a0a23fab25b6cb23e65efae3c89765f70eca6294a41cdcd3e16e`;
- legal model rows: `11f4fe862570895441036964a9b308e92c2ee6ba8a87b5da9009ffd5208a4bda`.

## Independent-date distribution

| Year | Physical dates | Absolute usable | Relative gate | Target-available rows | Final model rows |
|---:|---:|---:|---:|---:|---:|
| 2020 | 11 | 7 | 5 | 8,316 | 7,324 |
| 2021 | 12 | 9 | 3 | 9,811 | 8,938 |
| 2022 | 21 | 19 | 10 | 19,321 | 18,992 |
| 2023 | 23 | 14 | 7 | 13,642 | 13,155 |
| 2024 | 23 | 16 | 9 | 16,014 | 14,994 |
| **Total** | **90** | **65** | **34** | **67,104** | **63,403** |

Landsat 8 contributed 56 dates, of which 41 were absolute usable; Landsat 9
contributed 34 dates, of which 24 were usable. Adjacent WRS rows are mosaic
contributors and never counted as extra dates.

## QA, missingness, and relative-label audit

Twenty-five dates failed only the predeclared ≥50% tract-retention gate. Across
all 98,640 QA rows, 67,104 were retained at tract level, 27,447 failed minimum
valid-pixel count, and 4,089 failed valid fraction. No tract failed because of
scene footprint or a changing static denominator.

There were 3,701 locally valid tract labels on dates that failed the date-level
gate. They remain in the complete QA table for missingness analysis but are
excluded from `development_targets_model_ready.parquet`. A fail-closed
downstream selector now requires both `target_available` and `date_usable`.

Among the 65 usable dates, retained-tract fraction ranged from 54.0% to 99.8%
with a median of 94.3%. Model-row tract-median LST ranged from 20.73 to 58.57 °C;
usable-date city medians ranged from 27.10 to 53.65 °C. These broad ranges are
diagnostic checks, not climate trends or model results.

All 34 relative dates obey exact `ceil(0.20 × n)` hotspot counts. Recomputing
`target_lst_c DESC, GEOID ASC` produced exactly the stored 7,241 positives.
Using the exact stored floating-point values, 11 cutoff dates had repeated
temperatures and six had a tie group that actually crossed the top-k boundary;
all six used the frozen ascending-GEOID truncation rule correctly. Dates failing
the relative spatial-representativeness gate have neither anomaly nor hotspot
labels.

Scene-contribution pixels sum exactly to every tract's `valid_pixel_count`:
72,709,295 on both sides. Every GEOID has one and only one static eligible-pixel
count, exact pixel-identity hash, rasterized count, spatial block, and coordinate
quartile assignment across all 90 dates.

## Important uncertainty finding

`ST_QA ≤ 2 K` remains inappropriate as a primary hard validity mask, but label
uncertainty is material: 43,855 of 63,403 model rows (69.2%) have tract-median
`ST_QA > 2 K`. The primary target therefore remains unchanged, while the
predeclared 2 K analysis is mandatory as a robustness sensitivity. `ST_QA` is
audit metadata and is prohibited from every model feature matrix.

## Phase 2 implementation status

Phase 2 and grouped-split promotion are complete. The fail-closed assembler and
feature registry prohibit IDs, target-derived fields, thermal data,
target-day/future observations, and 2025. These are data and validation-design
milestones, not evidence that any model predicts well.

### Static features — complete and promoted

Official MRLC NLCD 2016 land-cover and imperviousness subsets, both required
SRTM tiles, and the Census 2019 coastline are downloaded and hash-audited. The
derived table uses the target stage's exact fixed eligible-land support.

| Static-stage check | Result |
|---|---:|
| Rows / unique GEOIDs | 1,096 / 1,096 |
| Legal model / audit-only features | 18 / 1 |
| Missing static values | 0 |
| Fixed eligible pixels | 1,166,782 |
| Minimum coverage: NLCD land cover | 1.000 |
| Minimum coverage: NLCD imperviousness | 1.000 |
| Minimum coverage: SRTM elevation / slope | 1.000 / 1.000 |
| Minimum coverage: Census coast distance | 1.000 |

The exhaustive NLCD fractions sum to one. The target-blind
`nlcd_developed_medium_fraction` reference is audit-only, removing exact
intercept collinearity. Static semantic SHA is
`e14c760614889fa3e346c0f543b92db12f1dec12b2df7efe65af1d83c85f4fe6`;
registry semantic SHA is
`562dbf03ba0ab47c498575cdd03af49091df3ed1ee4a0469fbecdf443bfb27bd`;
the promoted static commit is
`1c6ea2e0ff446a53843084e4c985f7af91124c7ad6c738a7c9bbc79530f75666`.

### Daymet weather — 30/30 subsets complete and features promoted

Official CMR discovery froze 30 Daymet V4 R1 granules: 2020–2024 × `tmax`,
`tmin`, `prcp`, `srad`, `vp`, and `dayl`, with zero duplicate keys and zero 2025
entries. All 30 official LA grid subsets were downloaded through the audited
Earthdata route and have unique file hashes; no older anonymous mirror was
substituted. The inventory semantic SHA is
`7655215698f819c24514f74cbc79866f5178032beb4a521465fac2b5aff2ac5c`.

The completed target-blind compiler validates
the official six-variable/year inventory and raw hashes, maps Daymet's 365-day
calendar without inventing December 31, constructs invariant eligible-land cell
weights, computes shortwave energy cell-first, and emits 21 windows ending at
`d−1`. Its production table has 98,640 rows across 90 dates and 1,096 tracts,
with all 21 weather features complete and zero 2025 rows. The Daymet feature
commit is
`a7da6a107695787f047547275669217c6bd508b12852e2d6c244078f687c0ea9`.

### Sentinel-2 optical — complete and promoted

The target-blind inventory contains 226 physical acquisitions, 449 selected
tile items, and 1,045 exact `d−60 … d−1` memberships covering all 90 development
dates, with no global cloud filter and zero 2025 rows. The processor enforces
native-grid phase, saturation propagation, BOA offsets, SCL 4/5, one joint-valid
five-feature mask, the fixed denominator, an 80% acquisition coverage gate, and
at least three physical acquisitions per target window.

A real baseline-04.00 calibration smoke passed. All 226 acquisition caches now
pass strict lock and schema revalidation, each with exactly 1,096 GEOIDs. The
promoted acquisition table contains 247,696 unique acquisition-tract rows. For
every tract, the static eligible-land denominator has exactly one value across
all acquisitions. There are zero duplicate keys, non-finite available feature
cells, target-day/future lineage rows, or 2025 rows.

The temporary localhost dashboard is an audit-only orchestration layer. It
prevents a second dashboard from writing the same cache tree and implements
cooperative pause: active acquisitions commit atomically before new work stops.
Transient acquisition failures now use delayed, sanitized retries without
pausing peer workers; retryable runner failures rebuild from revalidated caches,
and an external single-instance watchdog restarts a dashboard process that exits
nonzero. Persistent run/pause intent prevents an automatic restart from
overriding a user pause. Scientific integrity failures remain fail-closed.
Final compilation is sharded by target date because the same physical
acquisition legally belongs to several target windows; each shard invokes the
unchanged frozen composite function. Read-only equivalence testing against the
intended many-to-many expansion was exact across values, row order, columns,
and dtypes. These orchestration changes do not alter the scientific processor SHA
`68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c`.
The stage is now `complete` with `promoted_outputs_valid=true`. The model table
contains 98,640 rows (90 dates × 1,096 tracts); 97,870 pass the frozen minimum
of three qualifying acquisitions, while all five predictors are explicitly
missing for the remaining 770 rows. Lineage contains exactly 1,145,320 rows
(1,045 memberships × 1,096 tracts), with source ages restricted to 1–60 days.
Thirty-one of 90 dates and 537 of 1,096 tracts have at least one unavailable
row; the minimum available fraction is 56.75% by date and 85.56% by tract.
Missing rows are preserved for fold-local imputation rather than removed.

The source intermediate feature SHA is
`1114f61188f55258e4dae95c23cbd02d79bd0b60969e1e2d595b13ad2c9c8154`.
The canonical normalized processed feature SHA is
`aa02df3a00c51076610f442512949ade5ca70ab466b4d2d9c513826184fe82b5`,
and its independent promotion commit is
`bf3adfffcfe52df7cca7c366fa214d6cb11a5cca4bf1111454c99c87fd48e291`.

## Target-blind Phase 2 assembly and promoted validation

The predictor key universe uses only the frozen primary-overpass manifest and
fixed primary-tract manifest. It contains exactly 98,640 unique keys from 90
dates × 1,096 GEOIDs, covers 2020–2024, and contains no target value, target QA
field, duplicate, or 2025 row. Its semantic key SHA is
`5379959ef963f4f0506b8646d29ab95b1569a3ffeac288eaab93ecc1b139c747`.
The key universe itself remains a target-blind support artifact; the completed
feature table described below is its exact one-to-one realization.

The grouped-validation manifest is formally promoted over 63,403 legal keys, 65
independent dates, and 71 fixed spatial blocks. It contains 5 leave-one-year-out
temporal folds, 71 leave-one-fixed-block-out spatial folds, and 355 Cartesian
year × block joint folds: 431 total. Joint training excludes the held-out year
and every fixed tract within or exactly 1 km of the held-out block geometry;
the remaining non-test rows are purged. Each legal row is OOF test exactly once
in every split family. The promotion commit SHA is
`6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc`.

Reference evaluation code now fixes equal-date-weighted MAE as primary and
implements pooled RMSE/R², signed error, within-date anomaly MAE, and per-date
Spearman while reporting independent-date and block counts separately from the
tract-date row count; the full stitched development cohort has 65 dates and 71
blocks. Promotion verified exact modeling-table key agreement and fold-local
preprocessing isolation while reading no target value, predictor value, or
model score. It preserved the 2025 lock and still creates no model claim.

The deterministic calendar substage is complete on the full target-blind key
support: 98,640 rows, two finite unit-circle features, and exact key agreement.
The combined registry freezes 46 legal model features: 18 static, 2 calendar,
21 Daymet, and 5 Sentinel, plus one audit-only static reference.

The target- and score-blind readiness audit verifies exact key agreement, the
49-row registry, every predictor family, the invariant denominator, both
dynamic cutoffs, and the 2025 lock. It now reports
`state=ready_for_feature_assembly`, `ready_for_feature_assembly=true`, and
`blockers=[]`. It read no Landsat target value, target-QA table, or model score;
its commit is
`92534764be459110ff239670f320e3b947313f344ca00774c3097cff42fa3762`.

A separate target-blind assembly then produced the formal 98,640 × 49 Phase 2
table: two keys, 46 model features, and one audit-only field. It has 97,870
complete model-feature rows and 770 rows with the allowed all-five-missing
Sentinel pattern; static, calendar, and Daymet values are complete. It contains
zero 2025 rows and was built without opening target or QA tables. Its commit is
`3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.

Only after that target-blind commit passed was the legal target table opened.
The formal development modeling table has 63,403 rows × 50 columns across 65
independent dates: two keys, one LST response, 46 model features, and one
audit-only field. Exactly 63,235 rows have every model feature; the other 168
retain the all-five-missing Sentinel pattern for training-fold-only imputation.
There are no duplicate keys or 2025 rows. The model-table commit is
`9c2f903993167fc2a228b3cfe60a23fe33f57f252bae6299458338cb8eb967ad`.

Registry-driven factories and fold-local training helpers enforce the exact
B0/B1/B2/M1/M2 feature families, one response per training
date for B0, equal total training weight per date for the other models,
train-only dynamic medians, no missingness indicators, hard failure on missing
static/calendar or all-missing dynamic training columns, and no random-row HGB
early stopping. The exact pre-score selection contract is now frozen: 1 B0,
5 B1 Ridge, 5 B2 Ridge, 12 M1 Elastic Net, and 8 M2 histogram-gradient-boosting
candidates. Whole-year inner validation, stitched date-macro MAE, and the
deterministic simpler-first tie rule are fixed under commit
`4d8c2bd37be67f9f46d89d1dec8d5ed0aab196b24b43f9745ff730f040f2a6cd`.

The recoverable grouped-model runner is now implemented and production-smoked.
Its immutable plan contains 55,645 inner fits and 2,155 selected-candidate outer
refits (57,800 tasks total). Audited arbitrary-directory relocation and
run-relative result records upgraded the context, worker, runner, and compiler
contracts to v2. The authenticated context ID is
`37decc26a90832de1b9ce89d85f862f510659880f33a36f408ad89c670b29a45`;
the fresh portable execution ID is
`eb2d09ce9592d5531b51e3e507634aa25f25ef1323376b056dd79fae948876f5`.
The SQLite queue uses WAL, expiring leases and fencing generations; inner tasks
are the only claimable kind until all inner work completes. Pause stops new
claims and drains active fits. Transient failures retry automatically with
bounded backoff; exhausted tasks are quarantined without requiring a manual
continue click, and quarantine blocks scientific promotion rather than hiding
the failure.

A five-fit runtime-only calibration did not mutate the formal queue and did not
inspect or publish performance scores. Conservative samples took 2.50–3.08 s
for B0/B1/B2 and 20.72–21.03 s for M1/M2, giving a rough one-worker upper
projection of 803,071 s (about 9.3 days). The old v1 queue reached 42 completed
inner smoke tasks and was safely drained with zero active and zero quarantined.
Those results were intentionally not mixed into v2. The gaming-laptop controller
defaulted to six single-threaded workers and permitted a persisted paused-only
switch to eight. The portable v2 queue subsequently ran to completion. No smoke
result was treated as a performance comparison, and neither the smoke nor the
completed development run unlocks 2025.

## Completed development-model evaluation

The returned run is terminal at 57,800 / 57,800 complete with zero pending,
running, or quarantined tasks. Compilation produced exactly 951,045 unique OOF
predictions, 15 family-model summaries, 975 per-date metric rows, and 2,155
fold rows. Canonical import used the original ZIP members, not reconstructed
fragments, and preserved the compile hashes and provenance.

The initial result stage compares all five models within temporal, spatial, and
joint validation and selects the strongest legal baseline only from B0–B2 by
the frozen primary date-macro MAE. For the required joint M2 comparison this is
B1. Its uncertainty calculation first aggregates paired errors to 4,202
date-by-block cells, then independently resamples all 65 complete dates and all
71 complete blocks with replacement. It never resamples individual tract-date
rows. The point-improvement, Spearman, and positive-CI development gates pass;
the separately reported stronger test that the CI lower bound exceed 10% fails.
Thresholds were not changed after seeing the result.

## Completed endpoint, sensor, and missingness diagnostics

The relative-hotspot analysis is restricted to the 34 dates that passed the
existing spatial-representativeness gate, covering 36,139 tract-date rows and
71 spatial blocks. In joint OOF predictions, mean per-date average precision is
0.397683 for B1 and 0.666949 for M2. Exact top-20% precision/recall increases
from 0.420779 to 0.613917. Hotspot truth and predictions both use exact top-k
selection with the frozen GEOID tie-break. The endpoint/sensor provenance
commit is
`2ec9540ca7817dd53802e5849a35a8499b7f0755c7057b3c3b4292183475881a`.

In the development joint-OOF diagnostics, sensor-stratified date-macro MAE is
2.405914 °C for B1 versus 2.053245 °C for M2 on Landsat 8 dates, and 2.704447 °C
versus 2.203673 °C on Landsat 9 dates. The development-OOF direction of
improvement is therefore present for both sensors; the later final-test
Landsat-9 absolute-MAE result is slightly unfavorable and is reported
separately.
The all-five-Sentinel-missing group contains only 168 rows, 12 dates, and 29
blocks; its uncertainty interval is wide and it is retained only as an
exploratory descriptive result.

## Completed QA and failure-case diagnostics

The frozen QA cohorts preserve the primary result and expose an important
limitation. For rows whose tract-median ST_QA is at most 2 K, M2 improves on B1
by only 8.8608%, with a 95% crossed-cluster interval of -9.8226% to 26.4133%.
This is a tract-summary diagnostic, not the prespecified pixel-level hard-mask
sensitivity. The separate pixel-level build is now complete and is reported
below. The Sentinel-missing
group has a 95% interval of -59.5131% to 53.3188% and must not be generalized.
The QA/failure-case provenance commit is
`938a837cde8f49785e708e7960a4bca680ad851007a142189dbf4f3ac640ef8e`.

## Completed residual and spatial diagnostics

Mean Moran's I across the 65 joint dates is 0.639090 for B1 and 0.574355 for
M2; the medians are 0.669207 and 0.575018. All 65 dates are positive and have
exploratory, unadjusted permutation p-values at or below 0.05 for both models.
M2 reduces spatial residual structure but does not remove the strong residual
clustering, which remains a material limitation. The provenance commit is
`44047f7c9dd72135ab93d06bb4dc31144772ea012de98ba117768a185fdccc30`.

## Frozen development diagnostic figures

Four reproducible figures cover the joint performance overview, QA-cohort
forest, worst-date errors, and fixed pilot-date LST/prediction/residual maps.
The map dates were fixed independently of model score; 2024-10-10 is explicitly
marked as failing the relative-endpoint gate. Figure provenance commit:
`0e69b5bff544a92fb14015ebc75da5f27437420e68c83d55167749de97defda8`.

## Completed feature-set ablation

All 1,293 predeclared reduced-feature refits completed with zero quarantine.
Against full-feature M2 MAE of 2.108788 °C, calendar + weather has MAE 2.565020
°C, calendar + land-use/geography has MAE 3.352043 °C, and calendar + lagged
Sentinel-2 has MAE 3.614205 °C. The full model's relative improvements are
17.7867% (95% CI 9.6191%–25.8203%), 37.0895% (25.3337%–46.8952%), and
41.6528% (31.9740%–50.1712%), respectively. These are predictive
feature-set associations, not causal importance. Provenance commit:
`31afec41abc3448f9732567ad671d73938d5a620f0adc7d2421cb690edc76ae1`.

## Completed strict pixel-level ST_QA sensitivity

The strict `ST_QA <= 2 K` rule was applied before tract aggregation on all 90
dates using the same fixed eligible-land denominator. Only 15 dates passed the
unchanged usable-date gate, below the required 30, so the strict target was not
promoted. On 11,808 rows from those 15 dates, the frozen primary OOF comparison
shows a 17.7702% M2 improvement, but its crossed-cluster 95% interval is
-5.2068% to 39.5864% and crosses zero. This is an existing-OOF label
sensitivity, not a strict-label model refit. Provenance commit:
`71b9b5fd3b0768086852f0fa8d9fcd834d8719778a93f5d65ef6e1a8dfe46021`.

## Reconciled robustness package and generated report

All seven result families now share authenticated model-compile and OOF
lineage. The reconciliation keeps the primary interval separate from the QA
rerun, distinguishes tract-summary from pixel-level ST_QA, labels sparse groups
as exploratory, prohibits causal interpretation of ablation, and retains
spatial residual clustering as a material limitation. Reconciliation commit:
`7b7aa40a49ae5d3fb415e16ed8202205f70c8bdce795087b1d98467f453722df`.
The generated development report is `reports/DEVELOPMENT_REPORT.md`, report SHA
`d0fbdc5a598b9b4acb23ae7c3cbc7afe2596d1cb428bdbba6042cdb738574d31`.

## Immediate next work

1. Preserve the completed append-only evidence chain and build the read-only
   checksum export; never create a second claim or rerun with changed settings.
2. Use `reports/FINAL_EVALUATION_REPORT.md` and the canonical tables/figures to
   draft the paper, abstract, poster, and oral-defense materials.
3. State the qualified result exactly: strong M2 point and ranking performance,
   but a failed positive-confidence-bound gate and no overall protocol success.
4. Do not retune, retrain, alter thresholds, replace outputs, or use the 2025
   result to choose another model.

## Current verification state

The development report, reconciled robustness evidence, completed final fit,
formal model lock, and final evaluator were covered by 712 passing tests and a
clean full-repository Ruff run before authorization. The completed transaction
contains exactly 21 final files, 23 authenticated overpass-cache commits, no
remaining staging directory, and a completion commit of
`4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`.
The original unpatched execution command reauthenticated this completed state
without reopening targets.

The exact scientific contract is in `docs/RESEARCH_PROTOCOL.md`; the Phase 2
implementation contract is in `docs/PHASE2_FEATURE_SPEC.md`.
