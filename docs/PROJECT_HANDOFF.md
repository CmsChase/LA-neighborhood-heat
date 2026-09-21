# Project handoff

Last updated: 2026-09-21 Asia/Shanghai

## Current resume point: U.S. model optimization formally complete

The U.S. model-optimization stage is closed and model search is paused. The
fixed Colorado source-addition comparison found an 8.39% aggregate absolute-MAE
point improvement (4.5251°C to 4.1454°C), but Houston degraded by 0.6816°C,
Phoenix by 0.9581°C, and the paired expanded-minus-baseline 95% interval
[-1.9995, 0.8773]°C crossed zero. The fixed 0.25°C city guard failed, so the
decision remains `no_upgrade` and the existing four-source model remains the
cross-city default. The existing 23-feature LA relative model remains the local
development default. This is evidence of mixed local benefit, not proof that
new data are useless or that precision has reached a ceiling.

No later work re-evaluated Seattle, Denver, Atlanta or Miami, so their opened
blind results and failure interpretation remain unchanged. LA 2025 remains
closed to retuning or a second confirmation claim. All training, target-read,
external-target and acquisition permissions are false in the single active
stage record. The next direction is recorded only as a possible separately
authorized Chengdu pilot-area surface-heat and public-space feasibility study;
no Chengdu data, model, map or outreach has begun.

The release summary is `docs/US_MODEL_OPTIMIZATION_STAGE_SUMMARY.zh-CN.md`;
the fixed experiment report is
`docs/COLORADO_SOURCE_ADDITION_FIXED_V1.zh-CN.md`. The Atlas `/m3/` page reads
the tracked compact `atlas/public/data/colorado-source-addition.json`, generated
from the ignored local machine results by
`scripts/export_colorado_source_addition_atlas.py`. GitHub does not contain the
raw science data, target rows, acquisition caches, fitted model binaries or
paired row-level predictions under ignored `data/` and `exports/`. Their local
hashes are recorded, but no independent backup of all ignored artifacts has
been confirmed; the Git repository alone is not a full local-evidence backup.

## Current resume point: fixed Colorado source-addition experiment complete; no upgrade

The user authorized one fixed comparison after Colorado predictor-input acceptance.
The preregistered contract held QA (`4k`), the selected M3 specification
(`level_ridge_alpha_10__anomaly_hgb_leaves_31`), all features, preprocessing and
scoring rows fixed. For each original source city holdout, baseline training used
the other three original sources and expanded training used those same three plus
Colorado. No candidate search was performed.

Across the same 96,061 held-out rows and 132 original-source city-dates, equal-city/
equal-date absolute MAE changed from **4.5251°C to 4.1454°C** (8.39% point
improvement), but the fixed upgrade decision is **`no_upgrade`**. Chicago improved
by 2.7786°C and LA by 0.3797°C, while Houston degraded by 0.6816°C and Phoenix by
0.9581°C. The maximum degradation exceeded the preregistered 0.25°C city guard;
the paired hierarchical bootstrap interval for expanded-minus-baseline MAE was
[-1.9995, 0.8773]°C. Relative MAE also worsened from 1.3606°C to 1.4061°C.
The existing four-source model remains the default.

Verification is complete. The focused Colorado source-addition tests and Ruff
checks pass, and the full repository test suite passes when pytest's base
temporary directory is placed under the repository `.tmp` tree, as required by
the project's path-containment and synthetic-smoke safety contracts. A default
Windows system temporary directory on `C:` causes path-policy failures because
this checkout is on `D:`; those failures are environmental rather than model or
experiment regressions. A regression test now explicitly preserves the LA 2025
lock without accidentally filtering Phoenix's authenticated 2025 source-
development cohort.

The held-Colorado development diagnostic from the original-four model was
13.4843°C absolute MAE and +13.4843°C bias, versus 0.9671°C relative MAE. This
supports the existing diagnosis that Colorado failure is primarily city-level,
but is not causal or independent-confirmation evidence. LA 2025 and all four
external-city targets remained unopened; network use was zero. All temporary
target/model permissions are closed. Results and fitted comparison artifacts are
ignored under `exports/COLORADO_SOURCE_ADDITION_FIXED_V1/`; the Chinese report is
`docs/COLORADO_SOURCE_ADDITION_FIXED_V1.zh-CN.md`. Do not tune city weights or
model parameters against these same results. A new model question or independent
confirmation condition is required before more optimization.

## Current resume point: Colorado official predictor inputs accepted; no model work started

The bounded official Sentinel build finished all **404/404** physical acquisitions
needed by the 46 calibration-cleared dates. The persistent batch checkpoint records
55,925,390,302 bytes of GET response bodies, 64,985.4 seconds (18.05 h) of
acquisition work, 1.416 GB peak process RSS, and 105.89 GB minimum observed free
disk. There were 424 attempts, including transient failures that were recovered;
403 successful attempts were made in this formal run and one previously certified
sample cache supplied the 404th acquisition.

The first offline compilation attempt failed after acquisition completion because
`build_previous_60_day_composites` incorrectly required membership keys to be
one-to-many. A physical Sentinel acquisition is legitimately shared by several
target-date d-60:d-1 windows, while also having one row per tract. The merge now
uses constrained many-to-many expansion only after checking that each physical ID
has one local acquisition date on each side and that those dates agree. Forty
focused tests and lint pass. Re-running from authenticated caches completed in
about 17 seconds and did not re-download imagery.

Formal input acceptance is complete for **46 dates × 110 tracts = 5,060 rows**.
All 46 existing model features are finite and non-missing on those rows: 18 static,
2 calendar, 21 Daymet and 5 Sentinel. They connect to **4,140** existing
source-development label keys; only keys were inspected, not temperature values.
The complete master table still preserves all **47 × 110 = 5,170 rows**.
`2021-10-18` remains `calibration_blocked`, with all 110 rows retained, 41
non-Sentinel features present, five Sentinel features uncomputed, and 95 existing
label keys recorded separately. Normal Sentinel observation-missing rows among the
46 accepted dates are zero.

Ignored local evidence is under
`exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020/colorado_springs_co/`:
`OFFICIAL_PREDICTOR_INPUT_ACCEPTANCE.json` and
`official_predictor_inputs_46_with_blocked_date.parquet`. The accepted table SHA-256
is `9c9d0ef4b22c9ffa1a1b370e99f6716e718abfe51ef3aa4dd42ae8a871e07cc2`.
`ACTIVE_STAGE.json` is closed with all temporary permissions false. The data are
ready for a separately authorized fixed-model experiment limited to the 46 accepted
dates; no model was trained or scored, and the blocked date was not silently
removed or imputed.

## Current resume point: Colorado official Sentinel batch safely paused

The batch later reached 176 authenticated new acquisitions and stopped after two `WarpOperationError` attempts on `sentinel-2a|2024-06-01T17:39:11.024000Z|R98|GS2A_20240601T173911_046713`. The checkpoint has 184 attempts, 24.462 GB GET bodies and 9.50 h cumulative acquisition time. The user requested automatic recovery. The existing resume entry now permits up to six cumulative attempts per acquisition for the frozen transient error types, with 5/15/30/60/120 s backoff and serial reads after the first attempt. Attempt counts are read from the persistent checkpoint, so restarting cannot reset the cap. It also writes a worker PID marker and automatically repairs an orphaned `running` stage only when that PID is no longer alive. Scientific errors, calibration gaps, 80 GB/24 h cumulative limits, 30 GiB disk floor and abnormal per-acquisition network volume still stop. Tests and lint pass. Run the same `official_sentinel_resume` command once; transient failures are now handled automatically within the bounded run.

After another long local run, the worker was no longer present but `ACTIVE_STAGE.json` was left in `running_colorado_official_sentinel_batch_only`, so the resume entry correctly refused to start a duplicate. The frozen checkpoint and plan still match. The orphaned state was recovered to `paused_colorado_official_sentinel_batch`, all temporary permissions were closed, and resume remains preauthorized. The checkpoint now records 153 attempts, 148 authenticated attempts, 20,629,008,416 GET bytes and 29,280.034 s cumulative acquisition time. The latest acquisition in the checkpoint authenticated successfully. Re-run the same `official_sentinel_resume` command; it will reuse these caches.

The latest resume reached 41 authenticated new acquisitions and stopped on the first attempt for `sentinel-2a|2021-04-18T17:39:01.024000Z|R98|GS2A_20210418T173901_030411` with `ProxyError`. The checkpoint now has 44 attempts: 41 authenticated and three transient failures (two Warp failures whose serial retries succeeded, plus this Proxy failure), 5.842 GB GET bodies and 2.49 h cumulative acquisition time. `ProxyError` is now included in the same persistent two-attempt rule, so this acquisition has one serial retry remaining. Focused tests and lint pass; stage remains paused with permissions closed.

The resumed official batch later reached 13 authenticated new acquisitions, then stopped safely on `sentinel-2a|2020-07-02T17:39:11.024000Z|R98|GS2A_20200702T173911_026264` with `WarpOperationError`. The checkpoint records 14 attempts, 13 authenticated, 1.822 GB GET response bodies and 1.45 h acquisition wall time. The formal batch retry list had accidentally omitted `WarpOperationError`, although the older bounded remote-read helper already classified it as retryable. The formal runner now gives this acquisition its one remaining serial retry (`download_threads=1`) and counts prior failed attempts from the persistent checkpoint, so it cannot loop. Tests and lint pass. The worker is stopped, `ACTIVE_STAGE.json` is paused, permissions are closed, and all authenticated caches remain intact. No composite or 46-feature acceptance was claimed.

The user preauthorized later local resume of this same frozen batch. From the project root, run `.\.venv\Scripts\python.exe -m experiments.source_city_predictor_trial official_sentinel_resume`. This one command checks the paused stage, frozen plan/checkpoint SHA, cumulative 80 GB network and 24 h wall caps, and 30 GiB disk floor; it sets TEMP/TMP to D:, opens only predictor permissions, resumes authenticated caches, then closes permissions on exit. `2021-10-18` remains calibration-blocked. If the batch completes, the 46-date output still needs formal acceptance and key-only label overlap before any model experiment.

## Current resume point: Colorado official Sentinel batch running

The user authorized the formal build for the 46 calibrated Colorado Springs dates on the frozen official 2020 support. `ACTIVE_STAGE.json` is open only for public predictor reads and building this batch. The frozen plan SHA-256 is `d231955e92497c894c23c4ed1a334506ec576a76386c69dbbc1ed4da464bfc22`. The command `python -m experiments.source_city_predictor_trial official_sentinel_batch` started on 2026-09-18 with TEMP/TMP on D:. It resumes authenticated acquisition caches. `exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020/colorado_springs_co/sentinel_batch_progress.json` is the authoritative cumulative checkpoint for network bytes, wall time, attempts, memory and observed temporary space. The runner enforces 80 GB new GET bodies, 24 h cumulative acquisition wall time, 30 GiB D: free floor and a per-acquisition 4× sample-tier anomaly stop. The first two new acquisitions completed with authenticated caches, 269,253,471 GET bytes and 259.4 s acquisition time. A thread heartbeat called `Colorado Sentinel batch monitor` checks the ongoing worker and resumes from the same checkpoint if needed; routine progress stays quiet. On terminal completion or safe stop, audit the 46 date outputs, key-only label overlap, close ACTIVE_STAGE and remove that heartbeat. No target value read, model fit, scoring, commit or push is authorized. The 2021-10-18 date remains calibration-blocked.

## Current resume point: Colorado official Sentinel batch authorized by user; local gate update blocked before execution

The user explicitly authorized the bounded official Colorado Springs Sentinel batch for the 46 calibration-cleared dates, with an 80 GB new-network-response cap and 24 h batch wall-clock cap. The current execution environment's automatic safety review rejected the required write that would change the existing `ACTIVE_STAGE.json` batch gate from closed to open. The repository guard therefore remained closed and the batch did not start. A dry planning invocation stopped immediately in `_official_preflight` with `Official predictor preparation stage is closed`; no Sentinel acquisition, target read, model work, commit, or push occurred in this attempt.

Pre-run checks found about 102.37 GB free on D: and 7.53 GB free on C:, while the system TEMP path is on C:. A D:-resident temporary directory was created at `data/runtime/colorado_official_sentinel/tmp`; when execution is possible, TEMP/TMP should be redirected there and a remaining-D:-space stop line should be recorded before starting. The official runtime cache currently contains only the two previously certified sample acquisitions, so the handoff's 403 remaining clean-date acquisitions has not been reduced by this attempt. The 47-date/5,170-key 41-feature table and the 2021-10-18 calibration block remain unchanged. The input is not yet ready for the next fixed-model experiment because the five Sentinel features for the 46 clear dates have not been built and accepted.

## Current resume point: Colorado official 41-feature preparation complete; full Sentinel batch closed

On the official 2020 110-tract support and 47 target-usable dates, the full **5,170 prediction keys** are frozen; only key-level correspondence was checked against **4,235 existing source-development labels**, without reading temperature values. The 18 static, 2 calendar and 21 Daymet features were rebuilt on official geometry from authenticated raw caches and are complete on all 5,170 rows/47 dates with zero missing. Mirror-derived tract aggregates were not promoted. Machine summary (ignored local artifact): `exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020/colorado_springs_co/preparation_summary.json` SHA-256 `b4d3877444ff1a3efc88db8c2d538dc2982a23ab45439d98ac7b2561501c463d`; 41-feature table is `official_non_sentinel_41.parquet` in the same directory.

The official Sentinel inventory matches the frozen 416 physical acquisitions/1,643 items. Full XML/STAC identity and BOA audit: 1,036 complete conversions, 606 pre-04.00 products not requiring a new offset, **one** N0400 missing-offset product, zero read/identity failures. Dependency table shows only **2021-10-18** blocked; 46 dates are calibration-cleared but **not yet synthesized**. Two preselected calibrated resource samples (one/four items) produced authenticated official-support caches. For clean-date work, **403 acquisitions remain** after excluding blocked-date-only work and reusing authentic caches. Item-count-stratified 0.5–2× engineering scenarios: **17.51–70.03 GB GET**, **4.42–17.68 h**, **0.05–0.19 GB persistent derived cache**; two samples observed process peak about 0.69 GB (decimal) and did not establish a general temporary-disk maximum. About 110.08 GB disk was free at planning. `sentinel_batch_plan.json` contains per-acquisition dependencies, cache status, bounds and a resumable entrypoint that remains permission-locked. No full Sentinel batch, new target read, model fit or score occurred. §8 of the Colorado formal contract records the full evidence and caveats. ACTIVE_STAGE temporary permissions are closed; a separate batch authorization is needed. Charlotte stays paused; official/mirror geometry equivalence remains failed, while official ZIPs are the authorized new support basis. No commit or push.

## Previous resume point: Colorado official-support QA/target screen complete; predictors not built

User explicitly authorized a documented support revision after the mirror-equivalence
gate failed. **Do not reinterpret the failed zero-difference gate as passing:**
the 2020 Census ZIPs are now the new official geometry basis, and the 500
official/mirror 30 m difference cells remain recorded. The old six-date mirror
labels, Daymet/static/Sentinel features and caches are exploratory only.
Charlotte remains paused.

The official Colorado support was rebuilt for the same 110 tract GEOIDs:
431,376 fixed eligible 30 m land cells, 92 changed tract denominators, no zero
denominators. The candidate catalog was independently re-queried under the
same rule; it still has 80 dates and identical contributing scenes (2020–24
counts 12/10/15/23/20). All 80 dates were recomputed on official support:
**47 passed QA, 33 failed QA, 0 remain technical** after one bounded network
retry. Only the 47 QA-pass dates had thermal values read; all 47 passed the
unchanged target support gate, yielding **4,235 development labels**, with
5/7/9/16/10 dates and 449/628/776/1431/951 labels by year. All 47 output
tables retain 110 rows and their unchanged official fixed denominator; 4,235
label keys cover 22 geometry-only 5 km blocks. These are not independent
confirmation data and not yet a complete 46-feature training table.

The ignored machine evidence is
`exports/SOURCE_CITY_QA_PILOT/official_2020/colorado_springs_co/`
(`official_catalog.json` SHA-256
`f0fe3c29effcec908f2b5859e66a0c60b22f3c3ca286fde7244f9340e38f76f2`,
`qa_summary.json` SHA-256
`268b02110d09a77efc2b855f273c5db8a2145a634cb7f45b2687566f32a79f19`),
`exports/SOURCE_CITY_TARGET_AVAILABILITY/official_2020/colorado_springs_co/summary.json`
(SHA-256 `e01e662cfc35573b459def276257082141c823b89299f9cc23eb121942d8834b`),
and `exports/SOURCE_CITY_PREDICTOR_TRIAL/official_2020/colorado_springs_co/budget.json`
(SHA-256 `1552c408a3da5f6d29abe0a0912ee44879cfafbabd6e9e87fa92d55442156e8c`).
Section 6 of `docs/COLORADO_SPRINGS_FORMAL_SOURCE_BUILD_CONTRACT.zh-CN.md`
contains the full audit and source/space-support caveats.

Only a metadata inventory was made for subsequent nonthermal work: the 47
target-usable dates require 416 unique Sentinel physical acquisitions, 1,643
items, 13,144 band assets plus 1,643 product metadata assets. One-sample
engineering scenarios give 23.22–92.9 GB GET response bodies and 4.7–18.81
hours; disk metadata/derived-state scenario is 82–329 MB, excluding temporary
COG reads. Existing raw NLCD and Daymet footprints cover official clipped
support and may be reused after identity checks; all geometry-dependent tract
aggregates must be rebuilt. The known 2021-10-05 BOA-offset-deficient product
is still in the required windows; remaining item calibration has not been
audited. **Do not start the high-cost Sentinel batch, training or scoring.**
ACTIVE_STAGE permissions are closed after this screen. No commit or push.

## Previous resume point: official Colorado geometry differs; formal build stopped

The user supplied local original Census 2020 Colorado PLACE/TRACT ZIPs and
reported Census download URLs and 2026-09-17 19:18 Beijing download time. The
offline read-only audit did not retry blocked official endpoints. The two file
SHA-256 values are `3f5f0c917a4005c8c4fa081610db03f10ac3a83af98654849b7ea6936ae0694b`
and `111978fb25abed1db139680abe21c08b61a8d6fdc4339f79b6e0b1866adbb3d7`.
The fixed comparison found the same 110 tract GEOIDs and a 1.1144 m place
Hausdorff distance, but **500 disagreeing 30 m tract-zone cells**: 102
official-only, 111 mirror-only, and 287 assigned to a different tract. The
pre-existing zero-cell-disagreement gate therefore failed. Evidence is the
ignored `exports/SOURCE_CITY_QA_PILOT/offline_colorado_20260917.json`
(SHA-256 `1f061b40370a766fb0f2e0afcd176fa24a4293a67c3a03ad89134f050093c73c`).
The planning contract has an outcome addendum. Stop at this gate: no official
WorldCover land denominator, full-date QA, new thermal target, directory
recertification or post-screening Sentinel budget was produced. Preserve all
mirror exploratory caches and their labels as development-only. A later
explicit official-support rebuild/directory-refreeze amendment is required
before downstream access; do not weaken the gate or promote the mirror data.
ACTIVE_STAGE records this stopped state with permissions closed. Charlotte
remains paused; the 2021-10-18 exact-product BOA gap is unchanged. No model
work, commit or push occurred.

## Current resume point: Colorado formal source-build contract prepared; execution closed

Exploratory six-date technical validation is ended, without retrying the single
missing Sentinel BOA offset or adding dates. The full 660-row key universe is
preserved: five dates have 550 complete 46-feature rows and the 110 rows on
2021-10-18 retain a documented Sentinel calibration block. Daymet, static,
calendar, sources, and existing caches remain intact. This is technical
feasibility evidence on mirror geometry, not formal training acceptance or a
model-benefit result.

The next-stage contract is
`docs/COLORADO_SPRINGS_FORMAL_SOURCE_BUILD_CONTRACT.zh-CN.md`. It binds the
existing metadata inventory (`exports/SOURCE_CITY_METADATA_SCREEN/summary.json`,
SHA-256 `18a5d5b899ab43a1da45b9658a780dd0490c5d7fd30c0223a911a38fb2ce0ef9`):
Colorado has 80 provisional independent candidate overpasses across 2020–24,
10 previously QA-assessed, and 70 not yet QA-assessed. Before any remaining
target read, obtain the original Census 2020 Colorado place/tract ZIPs with
source records, review their provenance, and run the offline geometry comparison
using the existing boundary-audit script. Its new optional local-ZIP mode never
calls the blocked official endpoints or reads raster/target data. A geometry
pass is not automatic official-source acceptance. If the support changes, redo
affected mask, QA, target and predictor work; otherwise reuse certified caches
with evidence. The contract then stages fixed land support, all-date QA,
QA-passing-date thermal availability, and full-set nonthermal predictors, with
per-date failure isolation and cost checks. Exact N0400 BOA evidence is still
absent for 2021-10-18. Charlotte remains paused. ACTIVE_STAGE is planning-only
with all current permissions closed; no new data, training, scoring, commit or
push occurred in this planning turn.

## Current resume point: five Sentinel dates complete; one date still blocked (2026-09-17)

The one product without defensible BOA offsets belongs only to the frozen
2021-10-18 window. The other five windows, with 120 distinct physical
acquisitions, finished from the original authenticated cache locks. An earlier
remote COG short read on 2021-04-21 B02 was handled by one bounded retry of
that same acquisition with serial asset reads; no observation was omitted or
calibration guessed. All 120/120 five-date caches were certified. The first
compile attempt stopped only because its lineage already contained `city_id`;
after correcting that duplicate-column insertion, it compiled wholly from
cache without another image read.

The ignored `sentinel_compiled_five_dates/FIVE_DATE_COMPLETE.json` has SHA-256
`a8f9181847bf9f9778edce3605c54eaf0eb9dfbe7ebcc2a108c68716d2d2c559`.
Its feature output is 550 unique keys, 110 for each of five dates, all five
Sentinel features present and finite. The audit and lineage outputs have 550
and 13,200 rows; all three recorded file hashes and byte counts match. Source
ages are 1–59 days, all strictly before target dates. The current 660-key,
46-feature table has all features finite on those 550 keys. The 110 keys for
2021-10-18 remain; only their five Sentinel fields are deliberately uncomputed
(550 missing cells), while their other 41 fields remain present. Existing
exploratory label **keys** joined 587/660, with no temperature values read.
The machine `summary.json` SHA-256 is
`772a4b8b8f625a4808ad2a5f08202cefdd82b6890e99303ab8f40109ad5cb8c8`.
No model training, prediction, or scoring occurred. This remains development
data on mirror geometry; official 2020 equivalence is not certified, Charlotte
is paused, and 2021-10-18 still requires exact-product BOA evidence. Temporary
execution permission has been closed and the `colorado-sentinel` heartbeat
removed after completion.

## Previous resume point: Daymet verified before five-date Sentinel run (2026-09-17)

The user's private `daymet_local` run completed. The frozen completion manifest,
all 30 cached 2020–2024 year-variable NetCDF subsets, fixed grid/variable checks,
and output SHA-256 agree. The actual table has 660 unique tract-date keys (110
tracts × six dates) and all 21 contracted d−1/d−3/d−7 Daymet features, with no
missing or non-finite cells. No credential was accessed in this verification and
no subset was downloaded again.

The current 46-feature schema audit has 660 complete predictor keys and 587
matching existing exploratory label **keys**. All 18 static, two calendar, and
21 Daymet fields are present; all five Sentinel fields remain absent (660 cells
each). The frozen Sentinel preflight found one required BOA offset omission
among 562 unique products. It affects the 2021-10-18 composite. The same
Planetary Computer product/granule/datastrip metadata has no offset; checked
public alternate paths yielded other processing baselines or no exact product.
No verified same-product conversion was recovered, so the 142 remaining of 144
physical acquisitions were **not** run. Do not skip the item, infer an offset,
or treat the partial table as model-ready. Section 18 of the feasibility report
records the current evidence. Official 2020 Colorado geometry is still
unverified; Charlotte is paused. This exploratory output is not formal
training data. No new target values or model operations were performed.

The user's first private Daymet probe reached the endpoint but printed
`DAYMET_ACCESS_NON_NETCDF`; that older eight-byte-only probe retained no
response headers, redirect chain, or body, so its exact cause cannot be
reconstructed and must not be called a bad token. The same `daymet_local`
entrypoint now streams only the frozen 2020/dayl probe response to a 16 MB
cap, saves no body, and prints `DAYMET_PROBE_DIAGNOSTIC` with HTTP status,
allowlisted Content-Type/Encoding, declared and actual decoded byte counts,
redirect count, redacted final host/path, Authorization *scheme only* on the
first/final request, content-range flag, and classified payload. It recognizes
CDF-1/2/5 and HDF5 (including standard user blocks); Requests decodes normal
gzip/deflate transport encoding. HTML/login, JSON/XML errors, partial/truncated
or unrecognized payloads still fail before subset downloads. No token was
read during this repair; no Daymet subset or 660×21 output exists yet. The
user may rerun the same private module command once and share only the safe
diagnostic line if it still fails. The narrow stage permission is unchanged.

The private `daymet_local` entrypoint was repaired after its preflight rejected
the later, legitimate `paused_colorado_six_date_exploratory_predictor_trial_only`
stage name. The existing narrow local approval was already true; inventory and
fixed-support SHA-256, city, six dates, 660 keys, and closed target/model
permissions all matched. The entry now recognizes that exact paused state while
retaining the frozen-input and closed-permission checks, with named failures
instead of one generic error. A no-token/no-network run against current local
files reached the hidden credential prompt and stopped there deliberately.
Daymet authentication, subset downloads, and 21 features are **not** complete;
the user may rerun `daymet_local` privately. No broader permissions were opened.
Launch it from the repository root with
`.\.venv\Scripts\python.exe -m experiments.source_city_predictor_trial daymet_local`.
Directly executing `experiments\source_city_predictor_trial.py` omits the
repository root from Python's import path and fails before the credential prompt.

## Current resume point: Sentinel metadata scope known; one exact product blocks batch (2026-09-17)

Section 17 of `docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md`
records the frozen six-date metadata-only preflight. All 562 unique selected
Sentinel products were identity-checked: 276 pre-04 products require no new BOA
offset, 285 PB>=04 products have complete per-band conversion metadata, one
PB04 product lacks required offsets, and none failed reading or identity checks.
The sole blocker is
`S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE`,
which is needed by the 2021-10-18 target composite (one product, one physical
acquisition, one of six dates). The other five dates' metadata passed; their
Sentinel feature values have **not** been built. The ignored machine result is
`exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/sentinel_metadata_preflight.json`
with SHA-256 `0ba2c695bd709b1913ddcd827ec96c04b38519abca9b6d7985ecf974a47b3243`.

Fresh Planetary Computer metadata for the exact frozen product was byte-identical
to the cached root XML and still lacked BOA offsets. Same-product granule and
datastrip XML lacked them too; an exact-name Copernicus catalog query found no
entry. No other reprocessing version was substituted, no guessed calibration
was used, and **no Sentinel batch was started**. The 2/144 authenticated
acquisitions and existing cache remain unchanged. The batch authorization's
all-products-calibrated and failed-sample-validation conditions did not pass.
Daymet's private `daymet_local` run has not occurred: no frozen subset or
660-row/21-feature result exists. Static18/calendar2 and full 660 keys remain
the only completed predictor portions. General temporary permissions are
closed again; the narrow user-initiated Daymet entry remains available.
Official Colorado 2020 geometry equivalence is still unverified; Charlotte is
paused. No new target read, model work, commit, or push occurred.

## Current resume point: private Daymet entry ready; Sentinel PB04 still blocked (2026-09-17)

The user authorized only a one-prompt local Daymet entry and a two-sample
Sentinel BOA calibration audit, not a Sentinel batch. Section 16 of
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` records the amended
scope, actual metadata evidence and stop decision. `experiments/source_city_predictor_trial.py`
now has `daymet_local`: it prompts once in a terminal, probes the frozen small
2020/dayl subset, and only after valid NetCDF proceeds through the already
frozen 30 year-variable subsets and compiles 660 keys × 21 Daymet features.
Valid subset cache survives failures. The user has **not** run the entry with
a fresh private credential; no authenticated download or feature completion
is claimed. A narrow user-initiated Daymet-only approval is recorded in
`ACTIVE_STAGE.json`; broad temporary execution permissions are closed.

The failed PB04.00 Planetary Computer product has no BOA_ADD_OFFSET anywhere
in its cached XML; its STAC band asset and actual uint16 COG tags also provide
no applicable correction. This is missing conversion evidence, not an XML
parser-path defect or proof that the offset is zero. The reader still fails
closed; the earlier PB02.12 four-tile sample and its cache remain unchanged.
Historical source and portable pipelines share this reader, so no global
calibration change or cache rewrite was attempted. The 142 pending Sentinel
acquisitions were **not** started; 73 of them have PB≥04 and require
per-product evidence. Current static18/calendar2 are complete, Daymet21 and
Sentinel5 are incomplete. The 660-row partial table remains non-model-ready.
Official Colorado geometry equivalence and Charlotte's geometry issue remain
open. No target/model activity, commit or push occurred.

## Current resume point: Colorado auth/cost check complete; no full predictor build (2026-09-17)

The user authorized only a Daymet credential diagnosis and two frozen Sentinel
physical-acquisition cost samples, not a batch run. Section 15 of
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` records the results;
ignored `exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/sentinel_cost_audit.json`
contains the machine counts and `sentinel_remaining_acquisitions.csv` is the
reusable 142-acquisition pending list. The unique ACTIVE_STAGE permissions are
closed again. Git changes from earlier phases remain untouched and uncommitted.

The project's previously successful Daymet path requires exactly one ephemeral
Earthdata bearer environment variable. None of its three configured variables
was present at process, user or machine scope here, and no project-relevant
`.netrc`/`.env` was found. No token was read or printed, no authenticated
endpoint test was possible, and no Daymet values were downloaded. The existing
trial script now has `daymet_access_probe`, which performs one frozen local
subset request and checks only the NetCDF magic after the user privately loads
a fresh token. The prior anonymous HTTP 401 does not establish token expiry.

The four-item baseline-02.12 Sentinel sample completed in 82.446 s with 120
GDAL GETs/113,082,421 downloaded body bytes plus 32 HEADs. The one-item
baseline-04.00 sample was blocked before raster reads because its official
product XML lacks required BOA offsets. There are now only **2/144**
authenticated acquisitions and 142 pending/554 item tiles. Frozen item and
asset URLs are unique, so no safe repeated-download elimination or algorithm
change was made. A one-sample linear scenario for the remaining optical work
is ~3.17 hours/~15.66 GB response bodies, not a validated forecast; the
offset blocker must be resolved before any batch. All 21 Daymet and five
Sentinel feature columns remain missing in the six-date 660-row table; static
18/calendar 2 remain complete. Official geometry equivalence is still open,
Charlotte remains paused, and no model/target work was done.

## Current stage: Colorado six-date predictor trial paused with explicit input gaps (2026-09-16)

The user authorized only nonthermal B1/M3 predictors for the same six
development-only Colorado dates, with Charlotte paused. The scope was frozen
in section 13 of `docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` and
the unique ACTIVE_STAGE before predictor reads. Section 14 records the actual
trial and its limits. The ignored machine audit is
`exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/summary.json`;
its SHA-256 is `bb6c78a9ee13181b8339b1baa39e1fad0316706bcf29b70e632865da76134638`;
the reusable single-purpose entry point is
`experiments/source_city_predictor_trial.py`.

The complete predictor key set is **110 frozen mirror tracts × six existing
dates = 660 rows**, independent of target validity. Exactly **587** existing
exploratory label keys join; target temperatures were not read for this join.
All 18 contracted NLCD 2016/SRTM/GSHHG static features were constructed once
with no missing values, and the two calendar features are complete. The four
new static raster files total 25,223,478 bytes; the GSHHG archive was reused.
The official Daymet V4R1 metadata inventory is frozen at 30 year-variable
granules on a 39×32 local grid window, but its OPeNDAP subset returned HTTP
401 and no Earthdata credential was available in the running process. Thus
all 21 weather columns remain missing. The six d−60:d−1 Sentinel windows
contain 144 selected physical acquisitions/562 item tiles. One acquisition
is durably authenticated; the batch was stopped before the other 143 because
the missing Daymet credential already prevents complete B1/M3 inputs and
full optical processing would require 4,496 item-asset window reads.
The five Sentinel features therefore also remain missing. The partial
46-column parquet is a missingness audit, **not** model-ready predictors.

The official Colorado 2020 place/tract equivalence gate is still unfinished;
all these outputs remain exploratory mirror-boundary development material.
No new target dates, target values, LA 2025, evaluation-city targets, model
fit/prediction/scoring, default-model change, commit or push occurred. The
temporary ACTIVE_STAGE permissions are closed. A separately authorized
**exploratory** resume may use the same mirror support and cached Sentinel
inventory once a valid Earthdata credential is supplied only as a local
process environment variable, never in a repo artifact; the frozen 30 local
Daymet subsets and remaining optical acquisitions then need completion.
For **formal** source-city acceptance, obtain the original complete Census
`tl_2020_08_place.zip` and `tl_2020_08_tract.zip` with verifiable source,
byte count and SHA-256 and apply the existing 15 m/exact GEOID/zero
zone-difference comparison. For a full city, approximately 70 of the 80 directory dates
have not yet had QA inspected; the six-date 6/6 result is not an 80-date yield
estimate. Charlotte remains paused at its geometry blocker.

## Previous stage: Colorado six-scene exploratory target availability complete; Charlotte geometry remains unresolved (2026-09-16)

The user separately authorized thermal `lwir11` reads for only the six
Colorado Springs overpasses previously selected by QA, plus a local-file-only
Charlotte geometry diagnosis. This amendment was recorded in section 11 of
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` and the unique
ACTIVE_STAGE **before** any thermal value read. Section 12 gives the results;
the ignored machine summary is
`exports/SOURCE_CITY_TARGET_AVAILABILITY/exploratory_mirror/summary.json`
(SHA-256 `eec87608885630d9c05bc725bbfbddedb4f489fefa04decbb22d4d4a5dbcbe85`).

All **6/6** fixed Colorado overpasses remain provisionally target-usable
after the existing thermal DN, scale, fill, QA4K and physical checks, yielding
107, 99, 95, 101, 83 and 102 tract labels respectively (587 total).
Reread QA pixel counts matched the frozen QA pilot; no thermal-invalid pixel
was added within its QA4K eligible support. The other four QA-insufficient
pilot dates were not opened for thermal values. All six target dates are now
**development material, never independent confirmation**. They remain
exploratory mirror-boundary outputs, not formally accepted training data.

Charlotte's cached raw place geometry has a `Nested shells` defect near
(-80.9691773, 35.1642059). On local copies, `make_valid` and `buffer(0)`
disagree by 9.759 km² and 10,841 cells of 30 m city support. The old
237-tract selected-GEOID list lacks locally saved tract geometries, so the
effect on selected membership and tract-specific land denominator cannot be
recomputed locally. There is **no demonstrated non-substantive repair**;
original files were not overwritten, and Charlotte WorldCover/QA/thermal
values were not read.

Next, obtain verifiable official 2020 place/tract geometry for Colorado and
apply the original 15 m/exact-GEOID/zero-zone-difference comparison. Reuse
the exploratory cache only if analysis support is unchanged, else recompute
affected parts. A full Colorado source-city build would still need separately
approved collection of the untested candidate dates and nonthermal predictors;
the 6/6 pilot is not a yield estimate for all 80 catalog dates. Charlotte
requires valid official geometry and support reconciliation before any raster
trial. Temporary permissions are closed. No LA 2025 access, model fit,
prediction or scoring, default-model change, commit or push occurred.

## Previous stage: exploratory mirror-boundary QA pilot complete; no formal boundary or target acceptance (2026-09-16)

The user explicitly amended the QA pilot contract once to permit exploratory
WorldCover 2020 v100 and four Landsat QA window reads on the *frozen Esri 2020
mirror* geometry. This did not pass or remove the original official geometry
gate. `experiments/source_city_qa_mirror_pilot.py` produced local ignored
`exports/SOURCE_CITY_QA_PILOT/mirror_exploratory/summary.json` (SHA-256
`7c1919f3d190f1f171456c6bb670da2b8a76cf6abe4753a2e162e1ea5280b3ce`).
The contract amendment and full per-date results are in sections 9–10 of
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md`.

Colorado Springs used 110 frozen tracts and a date-invariant 431,384-cell
nonwater WorldCover denominator. Of the 10 preselected overpasses, **6 met
provisional QA support**, 4 did not, and 0 had a technical raster failure;
at least one provisional date exists in every year 2020–24. This is an
exploratory QA-only upper bound, not a formally accepted date or proof of
thermal target availability. Its two adjacent WorldCover tiles had 70
mode-resampling seam cells resolved by center-containing source tile before
QA reads. Charlotte's raw mirror place polygon is topologically invalid
(`Nested shells`); the city was stopped *before* WorldCover or QA access,
leaving all 10 dates unassessed rather than scientifically failed. Its prior
237-tract metadata list does not certify the boundary.

The next decision is to obtain valid official 2020 geometry: compare Colorado
with the original 15 m/exact-GEOID/zero-zone-difference gate, and resolve
Charlotte's invalid mirror geometry. Reuse Colorado's exploratory cache only
if official geometry is shown not to alter analysis support; otherwise
recompute affected support. A later thermal availability check requires its
own authorization. Neither city is approved for training. No thermal/target
or LA 2025 values were read; no model fit, prediction, score, default change,
commit or push occurred. Temporary read permissions are closed.

## Previous stage: two-city QA pilot blocked on official geometry access (2026-09-16)

A bounded recovery pass checked the actual network failure before attempting
more scientific reads. TIGERweb has TLS handshake timeout/closure without a
usable HTTP response; local DNS resolves it to an internal 198.18.* mapping.
The official Census TIGER2020 ZIP endpoints return Cloudflare HTTP 403 even
in the browser. No full-resolution 2020 place/tract official files for either
city were found in the existing local caches. Census confirms the original
2020 TIGER/Line boundaries are January 1, 2020 vintage, but its documentation
does not supply those geometries. Section 8 of
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` records the bounded
checks and exact four ZIP filenames needed to resume. No second audit run or
QA attempt was made; previous evidence hashes and 0/0/10 unknown dates per
city remain valid. Both city gates remain B (technical access blocker), not
geometry mismatch or scientific QA failure. All permissions are closed.

The user authorized a conditional, fixed 10-overpass-per-city QA-only pilot.
Its official-versus-mirror 2020 place/tract boundary audit could not finish:
the official TIGERweb endpoint failed with timeout/TLS EOF and the official TIGER/Line
ZIP endpoint returned HTTP 403 on this host. The mirror still matches the
previously frozen 110/237 tract lists and hashes, but this does **not** prove
equivalence to official geometry. Both cities are therefore B (specific
technical boundary-access blocker), with 0 QA-passing, 0 scientifically
QA-failing, and 10 unassessable preselected overpasses each. No WorldCover,
QA, thermal, target, or LA 2025 raster was opened; scientific raster download
was 0 bytes. The comparison rule, attempts, input IDs and status are recorded
in section 7 of `docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md` and the
ignored `exports/SOURCE_CITY_QA_PILOT/boundary_audit.json` (SHA-256
`f54b75516b29c88ff33d49e3ce55401b4b3ff30d94b93963b4fd69643774135d`),
generated by `experiments/source_city_qa_boundary_audit.py`. Temporary
permissions are closed. The next smallest action is to obtain verifiable
official 2020 place and tract geometry and compare it on the fixed pixel
grid; only a city that passes may proceed to its already frozen QA windows
under an appropriate subsequent authorization. No model or default changed,
and no commit/push was made.

## Previous stage: two-city public metadata screen complete (2026-09-16)

The one-time metadata screen for Colorado Springs and Charlotte is complete.
Its addendum is in `docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md`;
reproducible, target-free query code is
`experiments/source_city_metadata_screen.py` and its local ignored output is
`exports/SOURCE_CITY_METADATA_SCREEN/summary.json` (SHA-256
`18a5d5b899ab43a1da45b9658a780dd0490c5d7fd30c0223a911a38fb2ce0ef9`).
For 2020-24 May-October, the exact Landsat T1 L2SP metadata search found
80 and 83 distinct >=98%-coverage, nonambiguous physical-overpass dates,
respectively. Those are **catalog candidates, not QA-passing or target-usable
dates**. Both cities have annual Sentinel-2 L2A catalog items and the expected
ESA WorldCover 2020 v100 tile footprints. Their status is A at the metadata
gate only: a small QA support pilot can be designed, but no sample has been
downloaded, no fixed land denominator or eligible ST pixels have been read,
and neither city has been approved as a new source training city.

The target-independent 2020 incorporated-place identities are `0816000` and
`3712000`. The local Census TIGERweb endpoint failed TLS, so the existing
Esri 2020 Census pilot mirror supplied the geometry for the 50%-overlap,
98xxxx-excluded tract rule (110 and 237 selected tracts). Before any QA-only
trial, verify the official/mirror geometry equivalence or formally approve
the source; do not silently change boundaries. The then-unexecuted trial contract
preselects each year's earliest and latest qualifying physical overpass,
10 per city (10/18 scenes). A subsequent authorization conditionally permitted only
WorldCover fixed-land classes and Landsat QA windows, not thermal values.
The boundary precondition blocked all raster reads; the fixed C1/C2/C3
no-upgrade route remains closed.

## Previous stage: source-city data expansion feasibility complete; collection not authorized (2026-09-16)

The user accepted the stage-2 **no-upgrade** decision and closed the fixed
C1/C2/C3 route. It must not proceed to the already opened four-city historical
stress test or be rescued by adjusting candidates or thresholds. C1's 0.0348 C
worst-city point deterioration is a contractual failure, not a demonstrated
statistically significant harm.

The read-only, source-expansion feasibility report is
`docs/SOURCE_CITY_DATA_EXPANSION_FEASIBILITY.zh-CN.md`. It compares only existing
non-target predictor summaries for the four source and four opened cities,
plus official public metadata. Denver's tract elevation support is separated
from all four source cities; Atlanta's city-date lagged NDVI median is above
all four source medians. These are coverage findings, not evidence that adding
cities will improve the model. Colorado Springs and Charlotte are **conditional
metadata-screen candidates only**, not authorized collection cities. The
decision is B: city-specific joint predictor support, scene inventory and
QA-yield feasibility remain unknown. A minimal two-city metadata screen would
require a new explicit scope before any scientific-data acquisition. This
round read no target or LA 2025 values, downloaded no scientific data, fitted
or scored no model, and changed no default model. Its temporary scope is closed
in the single `ACTIVE_STAGE.json`; no commit or push occurred.

## Completed record: four-city absolute transfer errors, stage 2 (2026-09-16)

The user clarified that the high-error priority is the last four cities,
especially Denver and Atlanta, rather than further LA-local relative tuning.
`docs/FOUR_CITY_ABSOLUTE_ERROR_RESEARCH_PLAN.zh-CN.md` defines the research
plan: preserve the original M3 anomaly branch, compare exactly three fixed
level alternatives (B1 anchoring, source-support fallback, bounded weather
correction), select using source cities, then use the opened four cities only
as historical stress tests. Protect Seattle from material degradation and
report Miami's four-date limitation. This supersedes the LA capacity-first
priority in `US_MODEL_IMPROVEMENT_RESEARCH.zh-CN.md`; the earlier LA tail
analysis below is a side analysis, not the active research objective.

Stage 2 is complete and stopped before opened-city stress testing. The fixed
source-only implementation is
`experiments/four_city_absolute_error/run_stage2.py`; the detailed report is
`docs/FOUR_CITY_ABSOLUTE_ERROR_STAGE_2_SOURCE_VALIDATION.zh-CN.md`; local
machine-readable evidence is under the ignored
`exports/FOUR_CITY_ABSOLUTE_ERROR_STAGE_2/` directory. Its run signature is
`b29dc1fa09577cb89e99d3fcfc758b08d2b8ec5dcbfa40cfa88b81d65cfbcf1d`.
The run used only the four source cities, performed no download or predictor
build, did not read LA 2025 or opened-city targets, did not change a default
model, and did not commit or push.

On the same 96,061 QA4K OOF rows, 132 city-dates and 254 blocks, B1 absolute
MAE is 3.6578 C and original M3 is 4.5796 C. C1 reaches 3.6094 C overall but
worsens the worst city, Chicago, from B1's 4.6346 C to 4.6694 C. C2 and C3
score 4.2414 C and 4.2727 C and also fail both source gates. Consequently no
candidate is eligible and the final source selection is B1/no upgrade. The
nested selection procedure chose C3 in three outer folds and B1 in one, but
its outer OOF MAE is 4.3094 C; this is an evaluation of the selection procedure,
not grounds to change the rule.

All 1,268,160 fixed complete-OOF model rows satisfy absolute = level + anomaly
within 3.55e-15 C. Original M3 and C1/C2/C3 retain identical centered relative
predictions, deterministic GEOID-tiebroken rankings, and exact top-20% hotspot
sets on all 132 scored city-dates. The candidate route therefore changed only
the city-date level as intended. All stage-2 source read/fit/score/select
permissions are closed. Under the frozen stop rule, do not enter the already
opened Seattle/Denver/Atlanta/Miami historical stress test for these candidates.
The result is reused-source development evidence, not independent confirmation.

### Completed prerequisite: stage 0-1 contract and audit

Stage 0-1 is complete. The machine-readable development contract is
`experiments/four_city_absolute_error/fixed_contract.toml`; the audit method is
`experiments/four_city_absolute_error/audit.py`; the concise evidence report is
`docs/FOUR_CITY_ABSOLUTE_ERROR_STAGE_0_1_AUDIT.zh-CN.md`. The audit performed no
fit, candidate prediction, candidate scoring, download, predictor build, LA
2025 access, default-model change, commit, or push. Its temporary read
permissions are closed in the single `ACTIVE_STAGE.json`.

The frozen four-city baseline reproduces at 1.6696 C Seattle, 13.2004 C Denver,
5.2466 C Atlanta, and 3.2206 C Miami; the equal-city/equal-date result is
5.8343 C. Across 23,667 complete prediction rows, every absolute prediction
equals its stored level plus anomaly, and the largest absolute city-date
complete-support anomaly median is 2.22e-16 C. The scoring universe is the
distinct 9,502-row, 66-city-date, 68-block target-available subset. Per-date
level offsets, P95 errors, and rates above 5 C/10 C are in the local ignored
`exports/FOUR_CITY_ABSOLUTE_ERROR_STAGE_0_1/` audit outputs.

Model identity is resolved: the original anomaly is a component of the saved
full M3 pickle, not the separate `M3_RELATIVE_V1` joblib. Fixed-QA4K scored
source OOF predictions (96,061 rows, 132 city-dates, 254 blocks) can reproduce
the original M3/B1 baselines, but they do not contain held-out complete-universe
predictions. The older 96,904-row nested OOF mixed QA/model selections by outer
fold and cannot substitute for the fixed stage-2 contract. Stage 2 subsequently
filled this complete-universe gap as recorded above. Original blind-test
failures remain immutable, and LA 2025 remains unavailable for retuning.

## Historical side analysis: LA regional errors (2026-09-16)

An earlier interpretation of the high-error request focused on LA-local
areas; the user subsequently clarified the four-city priority above.
A read-only recomputation of existing
LA forward OOF results is reproducible with
`exports/LA_HIGH_ERROR_REVIEW/review.py`; its report and input-hashed summary are
in the same ignored directory. No fit, new target access, or permission change
occurred. Current relative MAE is 0.95308 C, but date-macro worst-10%-row error
is 2.50397 C; error exceeds 3 C on 3.1159% of rows under equal-date weighting.
All eight errors above 10 C occurred on 2022-05-14. The seven worst baseline
blocks among 68 blocks with >=25 dates across all three years average 1.61743 C.
Some contain only 1-3 observed tracts, so their spatial representativeness is
limited. All comparisons use the same baseline-defined seven blocks.
The prior Tmax candidate reduces their descriptive MAE to 1.51726 C but does
not improve datewise worst-10% error (2.50555 C). It remains unpromoted.
These newly chosen groups/metrics are post-hoc diagnostics, not a revised
historical gate or independent confirmation. A future tail-focused experiment
must fix endpoints first and define priority regions using training-only data
or outcome-independent geography, with overall performance guardrails.

## Earlier research planning note (superseded priority; no model execution)

The user requested research into potentially larger improvements for existing
US cities. `docs/US_MODEL_IMPROVEMENT_RESEARCH.zh-CN.md` audits the actually tested
candidate space and cites primary research/data documentation. It proposes a
bounded same-feature capacity comparison first, then considers date-varying
weather/geography and morphology/illumination information if data feasibility
supports it. These are untested proposals, not expected performance gains.
No model was fitted, no scientific data downloaded, no new target read, and no
active-stage permission changed. The completed stage below remains closed;
implementation requires a separately fixed next-experiment scope. LA 2025 and
external target boundaries remain unchanged.

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
