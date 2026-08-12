# Project Execution Plan

This table is the working roadmap for the student research project. Week
numbers are relative; scientific gates control progression more than calendar
dates.

| Phase | Target weeks | Work | Main outputs | Exit gate | Primary owner |
|---|---:|---|---|---|---|
| 0. Scope and feasibility — complete | 1–2 | Lock question, outcome, unit, leakage rules; run real Landsat pilot | Protocol, decision log, three-date target pilot, QA waterfall and sensitivity table | Absolute-label feasibility passes; June/August relative endpoints pass and October is withheld by locked gates | Research engineering implements; independent review challenges assumptions; student approves scientific claims |
| 1. Target dataset — complete | 3–4 | Build and audit all 90 primary 2020–2024 physical overpasses on the corrected fixed grid | Frozen mother/primary tract manifests, pixel-level scene lineage, target table, missingness/coverage report | Unique keys; static pixel-identity denominator; mosaic/QA contract; 65 post-QA usable dates; no 2025 rows | Research engineering |
| 2. Predictor dataset — complete | 5–7 | Build and audit static, calendar, 21 Daymet, and `d−60:d−1` Sentinel-2 features; perform a target-blind assembly before the legal target join | Frozen 98,640 × 49 Phase 2 table and 63,403 × 50 development modeling table; 46 model features plus one audit-only field | Every feature has units/version/time window; readiness blockers are empty; forbidden-feature, cutoff, key, and 2025-lock checks pass | Research engineering with independent review |
| 3. Validation design — complete and promoted | 8 | Predeclare 5 km spatial blocks, whole-year folds, Cartesian joint folds, 1 km buffer, nested year tuning, metrics, and the 31-candidate selection rule | Promoted 63,403-key manifest with 5 temporal, 71 spatial, and 355 joint folds; reference metric code and frozen selection configuration | One OOF test assignment per legal row/family; no date/block/buffer overlap; preprocessing isolation tests pass; no values or scores read at promotion | Research team |
| 4. Baselines and models — development OOF complete | 9–10 | Run the 57,800-task durable plan for B0/B1/B2, Elastic Net, and histogram gradient boosting using nested grouped validation | Authenticated OOF predictions, metrics, tuning log, and initial result analysis | Required development gates pass without split or threshold changes | Pipeline operator computes; research team interprets |
| 5. Robustness and interpretation — complete with limitations | 11 | Feature-family ablations, strict pixel-level ST_QA sensitivity, QA cohorts, sensor checks, uncertainty intervals, endpoint and residual diagnostics | Authenticated ablation/sensitivity tables, unified evidence table, generated development report, hotspot and spatial diagnostics | Complete: strict 2 K date-support gate failed and spatial clustering remains, so the claim is explicitly narrowed | Research team |
| 6. Model lock and one-time final test — complete | 12 | Freeze code/config/features/model/figures, create hashes, then unlock and evaluate 2025 once | `MODEL_LOCK.json`, append-only claim chain, one-way 2025 predictions, metrics, and figures | Evaluator never fits; every hash matches; one authenticated final transaction | Authorized operator after student approval |
| 7. Research communication — complete | 13–14 | Write paper, poster, public result atlas, limitations, and oral-defense materials | Final report, reviewed paper, print poster, defense deck, website, and reproducibility instructions | Every number traceable to a table or script; claims match estimand | Writing and QA team; student presents |
| 8. Cross-city continuation - combined external claim active | continuation | Test zero-shot transfer from LA to Phoenix, Houston, and Chicago; add calibrated uncertainty and abstention | Separate protocol/config, city manifests, target-blind predictors, frozen predictions, one-time external evidence | Complete and authenticate all 64 external overpasses plus three city compiles, then run the frozen evaluator once | Research engineering implements; independent review challenges; student approves freeze |

## Planned result package

The final project should report a result even if the model fails the success
criteria. Required artifacts are:

1. Data-flow and leakage-prevention diagram.
2. Maps of observed LST, predicted LST, within-date residuals, and uncertainty.
3. Temporal, spatial, joint, and frozen-year performance table.
4. Date-macro MAE with grouped confidence intervals.
5. Within-date Spearman and hotspot recall/precision results.
6. Feature-family ablation table: weather; land-use/geography; satellite; all.
7. QA and missingness sensitivity analyses.
8. Residual spatial autocorrelation and error-stratification diagnostics.
9. A limitations section that distinguishes LST from air temperature and human
   health risk.

## Roles and evidence boundary

| Research engineering | Scientific review and writing | Human/student decisions |
|---|---|---|
| Download/query data, create deterministic pipelines, write tests, fit models, generate exact tables/figures, record hashes and lineage | Refine hypotheses, critique design, interpret outputs, draft explanations, and rehearse defense questions | Approve scope and protocol changes, understand and verify the methods, and decide what claims to present |

Draft discussion and prose are never treated as evidence. Every reported number
must be generated from code, and every scientific claim must be supportable
from the frozen data, protocol, or cited source.

## Current execution checkpoint

Phases 0 and 1 are complete. The corrected builder ran all 90 frozen physical
overpasses end to end and committed `state=model_ready`: 98,640 complete tract
QA rows, 65 absolute-usable dates, 34 relative-endpoint dates, and 63,403 legal
absolute-model rows. There are zero duplicate keys and zero 2025 rows; exact
static eligible-pixel identity is unchanged for every GEOID across all dates.
The ≥30-date gate passed without changing its threshold.

Phase 2 is complete. The promoted fixed-support static table has 1,096
unique GEOIDs, 18 legal model features, one audit-only NLCD reference fraction,
no missing values, and full observed coverage for all five source layers.
Official MRLC NLCD subsets, both SRTM tiles, and the Census coastline ZIP are
downloaded and hash-audited.

The official Daymet CMR inventory is frozen at 30 development granules
(2020–2024 × six variables) and zero 2025 entries. All 30 official subsets are
downloaded and hash-audited, and the completed compiler produced 21 lagged
weather features on every one of the 98,640 target-blind tract-date keys.

The Sentinel inventory is frozen at 226 physical acquisitions, 449 selected
tile items, and 1,045 legal window memberships. All 226 caches are complete and
validated, totaling 247,696 acquisition-tract rows. Formal promotion produced a
98,640-row predictor table with 97,870 available and 770 explicit all-five-null
rows, plus 1,145,320 lineage rows whose ages are exactly 1–60 days. The
canonical processed feature SHA is
`aa02df3a00c51076610f442512949ade5ca70ab466b4d2d9c513826184fe82b5`.

The target-blind predictor support is an audited 90-date × 1,096-tract grid with
98,640 keys. Readiness now covers static, calendar, Daymet, and Sentinel with
`blockers=[]`, while reading no target value, target QA, or model score. A
separate target-blind assembly froze the 98,640 × 49 Phase 2 table (two keys, 46
model features, and one audit-only field) under commit
`3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.

The gated legal target join then produced the 63,403 × 50 development modeling
table across 65 independent dates. It has 46 model features, one audit-only
field, 63,235 complete model-feature rows, zero duplicate keys, and zero 2025
rows. Its commit is
`9c2f903993167fc2a228b3cfe60a23fe33f57f252bae6299458338cb8eb967ad`.

The grouped-validation manifest is now formally promoted over all 63,403 keys,
65 dates, and 71 fixed spatial blocks. It freezes 5 temporal, 71 spatial, and
355 joint outer folds (431 total), plus strict year-grouped inner CV and the
reference absolute-LST metric code. Promotion read only keys and split metadata,
not target values, predictor values, or model scores; 2025 remained locked at
that split-promotion checkpoint. Its
commit is
`6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc`.
The recoverable nested grouped run is complete: 55,645 inner fits and 2,155
outer refits, or 57,800 / 57,800 tasks, with no quarantine. Canonical import
from the returned ZIP preserved all original outer fragments and compiled
951,045 authenticated development OOF rows. Joint M2 improves date-macro MAE
by 16.1896% over B1, with a paired crossed-cluster 95% interval of
4.2088%–27.6883%; the required development gates pass without changing the
frozen split, candidates, metrics, or thresholds.

The pre-score model-selection contract is no longer pending. It freezes 31
candidates across B0/B1/B2/M1/M2, whole-year inner CV, stitched date-macro MAE,
and deterministic tie-breaking under commit
`4d8c2bd37be67f9f46d89d1dec8d5ed0aab196b24b43f9745ff730f040f2a6cd`.
The endpoint, sensor, Sentinel-missingness, QA/failure-case, residual, Moran's I,
and diagnostic-figure stages are also complete and provenance-locked. They show
useful hotspot skill and development-OOF improvement for both Landsat sensors,
while identifying
strong remaining residual spatial clustering and weak evidence in the small
tract-median-ST_QA and Sentinel-missing cohorts. All 1,293 feature-ablation
refits are complete with zero quarantine. The strict pixel-level ST_QA rebuild
is also complete, but retained only 15 usable dates versus the required 30 and
its frozen-OOF improvement interval crosses zero. These limitations are
preserved in the authenticated reconciliation and generated development report.
The one-time 2025 evaluation is complete and authenticated under claim
`c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`.
M2 reduced held-out equal-date MAE from 3.1165 °C to 2.1650 °C (30.53%), and
median per-date Spearman was 0.8447. The 95% relative-improvement interval was
-10.13% to 58.46%, so the required positive lower-bound gate and overall
protocol success flag did not pass. The result remains frozen; no retuning,
threshold change, second claim, or repeat evaluation is permitted. Phase 7
must communicate this qualified result and its 15-date uncertainty honestly.
The 239-file read-only evidence export is complete and externally anchored by
ZIP SHA-256
`61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.
Phase 7 is complete: the reviewed paper, 36 × 48 inch poster, ten-slide defense
deck, public interactive result atlas, and reproducibility documentation all
preserve the qualified held-out conclusion.

The following source-contract history is retained for provenance. The Phoenix geography and
source-footprint metadata pilots, portable water-distance review, and
target-blind GSHHG geometry pilot are authenticated. The geometry pilot
preserved its preregistered V1 failure, then passed a source-structure-only V2
whose points and numerical thresholds were unchanged before distance access.
At the fixed target-blind Phoenix point, the global-candidate contract was
220.201 km shorter than the U.S.-only Census contract, while every numerical
audit passed. The four point diagnostics establish source semantics and
numerical stability, not positional accuracy or a complete distance surface.
The subsequent decision retained GSHHG as the candidate but deferred the
source-and-algorithm freeze because the then-current contract excluded L3
lake-island shores. The source-only L3 hierarchy audit has now completed. It
preserved a V1 structure-phase failure before distance, applied one separately
committed single-character V2 amendment, then authenticated 139 direct L3
descendants and passed every structural and numerical gate. At that L3-audit
checkpoint it created no source, algorithm, feature-name, predictor, model,
external-target, or target-QA authorization, and the next safe task was the
separate portable water-distance source-and-algorithm freeze decision.

Canonical v7, published in commit
`252c01d015110336c65bb602d4c5b608708fb092`, changes exactly two authorization
leaves: it closes the consumed L3 geometry-read permission and opens only the
evidence-only V2 freeze decision. Its 20,809-byte plan has file SHA-256
`88c153b7c1da9f2f159ac550fd3156a4ffe3fd1f56c269c057288d938a2047f3`
and internal commit
`4f6ed97b64d3a1601da6af83779ec96bef87c77de72d5294475ac029f666110f`.
The completed V2 program reads seven source-only rows from the tracked L3 success
manifest, not the ignored CSV, and opens no ZIP, geometry, eligible support,
predictor, model, target, or result. Only the exact source and audited
point-distance algorithm are frozen in its 18,541-byte decision terminal,
whose file SHA-256 is
`a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b`;
predictor construction remains closed. A separate tracked-only v8 must then
consume that terminal, update the canonical plan locks, close the decision
permission, and authorize only the exact preregistered predictor-contract
freeze program. That program is staged together with v8 so the permission
cannot later be widened by substituting different code or inputs.

Current Phase 8 checkpoint (2026-08-12): all 516 Sentinel units and the
136,941-row by 46-feature predictor table authenticated. The protocol/model
specification was locked before target access. The LA source lane completed all
91 units and produced 98,640 tract-date keys. Frozen B1/M2/CQR fitting then
committed exactly 38,301 predictor-only Phoenix/Houston/Chicago predictions
before any external target was opened. One indivisible three-city claim is now
active at `http://127.0.0.1:8771/`; it contains 64 external overpasses plus
three city compiles. Partial scoring, refitting, and result inspection remain
forbidden. The next scientific gate is authentication of the complete combined
target set, followed by one frozen evaluation, evidence release, and Atlas
publication.
