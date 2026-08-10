# Multicity methods and evidence runbook

Status: **implementation rehearsal; external-city targets remain sealed**

This runbook connects the scientific design to the programs, durable
checkpoints, and evidence that must exist before a cross-city result can be
reported. It supplements the preregistration in
`MULTICITY_GENERALIZATION_PROTOCOL.md`; it does not replace or loosen that
protocol.

## Question and estimand

The continuation asks whether one fixed model trained only with Los Angeles
land-surface-temperature labels transfers to Phoenix, Houston, and Chicago.
The target is QA-filtered clear-sky daytime Landsat land-surface temperature
(LST) at the `city × Census tract × physical overpass date` level. LST is a
surface-heat hazard proxy, not air temperature, personal exposure, illness, or
mortality.

The analysis is a historical external hindcast. Daymet is observation based,
so the project must not describe the continuation as a live forecast or
real-time warning system.

## Fixed experimental roles

| Cohort | Role | Permitted use |
|---|---|---|
| Los Angeles, 2020–2023 | Training | Fit the fixed B1 diagnostic and M2 primary pipelines |
| Los Angeles, 2024 | Calibration | Calibrate the 90% interval and freeze the abstention-width threshold |
| Los Angeles, 2025 | Prior Phase-I anchor | Descriptive context only; never reused for selection or a new claim |
| Phoenix, Houston, Chicago, 2025 | Zero-shot external confirmation | Predict first; open labels once only after the prediction commitment |

The external cities are three fixed case studies, not a random sample of all
U.S. cities. A successful result would support transfer to these three
contrasting settings, not universal deployment.

## Fixed models and predictors

- **M2-Transfer is primary:** histogram gradient boosting with the frozen 46
  public predictor features.
- **B1-Transfer is diagnostic only:** Ridge regression with two calendar and 21
  lagged Daymet features. It is the legal comparison baseline, not a deployment
  candidate.
- City ID, tract GEOID, raw coordinates, Landsat thermal values, target QA,
  same-scene optical data, future observations, target-city summaries, and
  target-derived climatology are prohibited model inputs.
- Every learned imputer, scaler, estimator, conformal correction, and
  abstention threshold is fitted without external-city labels.
- Los Angeles training rows are weighted so each training date has total
  weight one, then the complete weight vector is normalized. The locked
  predictor/model contract is the normative source for all estimator details.
- Sentinel composites end before the target date. The eligible-land support
  and its pixel identities remain fixed across dates.

## Stage and gate sequence

| Gate | Action | Durable output | What remains forbidden |
|---|---|---|---|
| G0 Predictor contract | Freeze geography, support, feature names, dates, and target-blind predictor keys | Contract, inventories, 41-feature component and Sentinel work plan | Model fit and all external targets |
| G1 Returned Sentinel acceptance | Verify the returned ZIP/checksum or directory manifest, every packaged hash, 516/516 status, four city commits, and final 46-feature commit before terminal acceptance | Return receipt and predictor-readiness report | Model fit and all target values |
| G2 Parallel synthetic rehearsal | Run the complete four-city fit, prediction, evaluation, and chart path on deterministic invented values; this may occur while G1 is still running | Explicitly synthetic smoke artifacts under `.tmp/` or outside the repository | Any scientific authorization or interpretation as evidence |
| G3 Protocol/model lock | Authenticate and bind the existing frozen feature/model contract, then lock code identity, cohorts, metrics, bootstrap, planned figures, and prediction schema | Append-only protocol and model-lock records | Real fitting and all target values |
| G4 Los Angeles source-target build | Separately authorize and build the 90-date LA 2020–2024 source lane on the new canonical support | Authenticated source-target table and completion record | External-city target access |
| G5 Source fit and prediction commitment | Fit on LA 2020–2023, calibrate on LA 2024, create all three-city 2025 predictions, intervals, and abstention flags | Authenticated model and external-prediction commit | External target access until the commit is complete |
| G6 One-time external claim | Authorize the indivisible three-city cohort under one claim and create `VALUES_OPENED` immediately before first target/href access | Claim-bound marker and resumable per-overpass cache commits | Retuning, city substitution, or a second claim |
| G7 External evaluation | Compile fixed targets, join committed predictions, calculate preregistered metrics and crossed date/block uncertainty | Completion record, tables, figures, and read-only evidence package | Any result-driven parameter or threshold change |
| G8 Public display | Export compact display JSON from authenticated result tables | Display manifest and static website | Recalculation, training, or scientific authorization in the browser |

If a gate fails, retain its diagnostics and stop at that boundary. A transient
download or raster error may resume from its last committed work unit; it does
not authorize changing the cohort or scientific rule.

## Primary analysis

Within each external city, calculate tract-level absolute error for each date
and then give every usable date equal weight. Give Phoenix, Houston, and Chicago
equal weight in the pooled external comparison.

The primary effect is:

`R = 1 - external_date_macro_MAE(M2) / external_date_macro_MAE(B1)`

Point prediction succeeds only when all preregistered conditions hold:

1. `R ≥ 10%`;
2. the 95% confidence-interval lower bound for `R` is above zero;
3. M2 point MAE is no worse than B1 in every external city; and
4. at least 30 usable city-dates exist in total and at least eight exist in
   each external city.

If the date gate fails, the result is `inconclusive_sample_size`; the date
window, cities, or threshold do not change.

The uncertainty analysis is separate. The nominal interval is 90%, and the
abstention threshold is the equal-date-weighted 80th percentile of calibrated
interval width in Los Angeles 2024. Reliability succeeds only if overall
coverage is 85%–95%, every external city has at least 80% coverage, every city
retains at least 60% of predictions, and accepted-set MAE is at least 10% below
all-prediction MAE.

Secondary outputs include per-city date-macro MAE, RMSE, signed error,
within-date anomaly MAE, median per-date Spearman correlation, exact top-20%
hotspot metrics, interval coverage and width, weighted interval score,
risk–coverage curves, and the preregistered strata. Every table reports row,
independent-date, and spatial-block counts.

## Synthetic rehearsal policy

Synthetic data exist only to test software wiring. The rehearsal must:

- use deterministic seeds and all four canonical city IDs;
- exercise fitting, Los Angeles calibration, target-blind external prediction,
  leave-one-city-out splits, evaluation, and figure/data generation;
- prove that a held-out city never appears in its training fold;
- prove that changing synthetic external targets cannot change already-created
  predictions;
- reject target, QA, key, and identifier leakage into the feature matrix; and
- write only to a caller-selected temporary or ignored output directory.

Synthetic metrics are not appended to the scientific experiment log, copied
to the public result JSON, cited in a report, or used to alter a frozen model.
Every synthetic table must carry `artifact_scope=synthetic_smoke_only` and
`scientific_evidence=false`; the figure and summary must carry the full
non-evidence warning.

## Evidence retention contract

### Tracked control records

Track protocols, configurations, code, tests, schema definitions, compact
manifests, hashes, claim identifiers, and human-readable decisions. A completion
record must include its state, algorithm version, input locks, output paths,
byte counts, SHA-256 values, access flags, and a canonical internal commit.

### Generated scientific data

Keep large rasters, Parquet tables, caches, ZIP files, and figures under the
ignored `data/`, `exports/`, or designated runtime directories. Never hand-edit
them. Their identities are carried by tracked manifests or a read-only evidence
package.

### Transfer and resume evidence

For a terminal ZIP return preserve the original ZIP, adjacent `.sha256`,
package-local manifest, formal return receipt, all per-acquisition commits, and
terminal completion record. For a terminal directory return preserve the
directory manifest/hashes, the same formal receipt, all commits, and the
completion record. A safe-paused copied directory may be staged only to resume;
it receives a resume checkpoint, cannot pass G1, and receives the formal
receipt only after canonical 516/516 reauthentication.

Import is additive and performs a full-package preflight followed by atomic
per-file publication; it is not an all-files rollback transaction. A same-hash
destination is reused, a conflicting destination stops the import, and an
incomplete transfer never deletes a valid local cache.

### One-time target evidence

Before the first external target value is opened, preserve the frozen external
prediction file and its commit. The authorization, claim ID, prediction commit,
target configuration, and `VALUES_OPENED` marker must agree on every resume.
Each successful overpass has its own cache commit; a failed unit leaves earlier
commits intact. No target asset URL or credential belongs in a tracked file.

### Final evidence package

The external evaluation package must be read-only and independently
verifiable. At minimum it contains the protocol/model locks, code identity,
input/output manifests, committed predictions, target lineage and QA summaries,
metric tables, bootstrap output, figures, completion record, environment
description, and a package-level SHA-256. The website export is derived from
this package but is not a substitute for it.

## Current implementation checkpoint

- The 41 non-Sentinel features and target-blind four-city predictor universe
  are complete.
- The resumable Sentinel build is running on the gaming laptop; Chicago has
  completed and the remaining cities continue in the fixed order.
- The one-click copy-back workflow accepts the packaged ZIP, the copied
  portable folder, or its extracted result folder. It validates before import,
  resumes partial safe-pause returns, and writes a formal receipt plus the
  predictor-readiness result after terminal acceptance.
- The fixed transfer-model core and full four-city synthetic smoke path pass
  deterministic integration tests. The `/cities/` target-sealed preview is
  implemented and contains no released result payload; both remain
  implementation artifacts only.
- A 159-unit future target queue can be initialized in
  `paused_not_authorized` state. Initialization does not open an asset href,
  thermal value, QA value, or model result.

The next scientific action after a successful Sentinel return is predictor
readiness followed by a separately reviewed protocol/model lock. It is not
external-target access.
