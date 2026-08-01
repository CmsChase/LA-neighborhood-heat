# Decision Log

## 2026-07-18 — Initial scope

- Selected the City of Los Angeles rather than Los Angeles County.
- Selected QA-filtered Landsat 8/9 daytime LST as the physical supervised label.
- Defined LST as surface-heat hazard, not human heat risk.
- Selected 2020 Census tracts as primary reporting units, with a later
  equal-area-grid sensitivity analysis.
- Selected 2020–2024 as development years and locked 2025 as the final test.
- Required lagged non-thermal Sentinel-2 features and grouped spatiotemporal
  validation.
- Rejected a self-constructed Human Heat Risk Index as the primary target because
  it lacks independent ground truth and would risk circular prediction.

## 2026-07-18 — Target pilot and QA revision

- Queried official public metadata and remotely read three 2024 Landsat 9 L2SP
  scenes spanning low to moderate scene cloud cover.
- Confirmed complete City footprint, official ST scale/offset, valid ST digital
  numbers, QA bit handling, and 63 candidate development-period dates.
- The provisional `ST_QA ≤ 2 K` rule retained only 23.7%, 10.4%, and 7.2% of City
  eligible pixels after the other QA checks. It retained only 21.9%, 6.7%, and
  3.1% of tracts and introduced strong spatial selection.
- The mask waterfall showed that scene footprint, ST validity, and ordinary
  cloud QA were not the cause. `ST_QA` is a continuous uncertainty estimate,
  not an official invalidity flag.
- Revised the primary rule before feature construction or modeling: no hard
  `ST_QA` cutoff; retain `ST_CDIST ≥ 1 km`; record tract median and P90 `ST_QA`
  only as label-quality metadata. The 2 K cutoff remains a strict sensitivity
  scenario.
- Under the revised locked rule, the provisional pilot retained 95.1%, 94.8%,
  and 77.5% of 1,112 generalized-boundary tracts.
- This revision used coverage and QA diagnostics only. No model was fitted and no
  QA choice used prediction performance.

## 2026-07-18 — Phase 1 boundary, cohort, and relative-label audit

- Replaced the 1:500,000 pilot geometry with detailed original 2020 TIGER fields
  from the California Department of Water Resources public mirror. The downloader
  verifies 2,498 LA County features, complete pagination, and unique 11-digit
  GEOIDs. The inaccessible Census-hosted source remains the underlying authority.
- Froze 1,096 City-clipped primary tracts using the ≥50% City-area rule and
  excluded 14 `98xxxx` special-use tracts from the neighborhood manifest. The
  special tracts remain a prespecified sensitivity universe.
- Replaced the date-varying `QA_PIXEL water` denominator with a static 2020 ESA
  WorldCover land mask. An automated check requires identical eligible-pixel
  counts for each GEOID across dates.
- Removed the `<15%` global scene-cloud filter from the primary cohort. The
  target-blind inventory found 178 eligible scenes, 91 physical overpasses, and
  90 complete, unambiguous City overpasses; 65 dates meet the old low-cloud rule.
  The 30-date success gate will count only post-QA usable overpasses.
- Locked physical-overpass grouping and adjacent-row mosaicking as the full-build
  contract. The three pilot dates can use their single full-City `041/036` scene;
  the full builder must retain and lineage adjacent `041/037` scenes.
- Added a spatial-representativeness gate for relative endpoints: ≥80% overall
  tract retention and ≤20 percentage-point retention gaps across both latitude
  and longitude quartiles. Relative hotspots are exact deterministic top-k.
- Final detailed-geometry pilot retained 1047/1096, 1041/1096, and 853/1096
  tracts. June and August passed the relative-endpoint gate; October failed it
  and therefore received no city anomaly or relative-hotspot label.

## Final-test unlock log

No unlock has occurred. `unlock_final_test` remains `false`.

## 2026-07-18 — Fixed-grid target build lock

- Retained all 1,110 area-eligible tracts in a mother manifest and marked the 14
  `98xxxx` special-use tracts as primary exclusions instead of deleting them.
- Locked the target grid to EPSG:32611 at 30 m and recorded hashes for the grid,
  tract-zone raster, resampled static land mask, and every tract's exact static
  eligible-pixel identity.
- Changed categorical WorldCover alignment from nearest-neighbor to modal
  resampling before the full build.
- Froze a primary manifest of 90 complete, unambiguous physical overpasses and
  its SHA-256; the 15-minute grouping window is measured from the first scene in
  a group, preventing chained groups longer than the limit.
- Added a 4 × 4 joint latitude/longitude retention gate for relative endpoints:
  every cell with at least 20 tracts must retain at least 60%.
- Ran the first real locked overpass (2020-05-16) through the two-scene quality
  mosaic. The build retained 1,061/1,096 tracts, produced 213 exact hotspots,
  and passed unique-key, no-2025, target-QA, and static-denominator guards.

## 2026-07-18 — Red-team correction before full target build

- Independent review found that the provisional target-grid edges were
  `0 mod 30` metres while the locked Landsat UTM source edges were
  `15 mod 30`, producing a half-pixel phase error. No full build or model had
  been run. All provisional target caches were invalidated.
- Corrected the fixed grid to prespecified 15 m x/y edge anchors and added
  fail-closed checks for source CRS, resolution, rotation, and integer-pixel
  phase alignment.
- Replaced `ST_B10 != 0` as scene footprint with independent source-raster
  extent coverage combined with `NOT QA_PIXEL.fill`. A covered pixel with no ST
  retrieval is now covered-but-invalid, not outside the scene.
- Separated explicit online inventory refresh from target construction. The
  target builder now reads frozen City boundary, scene asset URLs, and primary
  overpass artifacts only.
- Cache identity now includes algorithm/code/runtime, config, boundary, tract,
  grid/static mask, scene inventory, primary manifest, and per-overpass source
  hashes. Parquet file hashes and schemas are verified before cache reuse; the
  summary is written last as the cache commit marker.
- Incomplete builds now write `_partial` outputs only. A model-ready table can
  be promoted only after all 90 overpasses complete and the ≥30 usable-date gate
  passes.
- Corrected first-three-overpass build produced 3,288 unique QA rows and
  retained 1,061, 375, and 71 tract labels. Exact static eligible-pixel identity
  remained invariant for every GEOID; no 2025 row was present.

## 2026-07-18 — Frozen-manifest and aggregate-commit hardening

- Moved the exact City boundary, scene asset URLs, overpass audit, primary
  manifest, and inventory summary into the repository's trackable
  `manifests/target_inventory/`; a repository clone can reproduce the frozen
  cohort without silently re-querying a changed STAC catalog.
- Replaced whole-file TOML cache identity with separate canonical inventory and
  target-stage hashes. Feature/model settings and TOML comments do not alter
  either data-stage identity; date/sensor settings alter inventory identity and
  target-QA/grid settings alter target identity.
- Added an inventory code/runtime fingerprint. The target cache also binds the
  inventory fingerprint and exact per-overpass source lock.
- Made `build_progress.json` the atomic aggregate commit marker. Every run first
  writes `preparing` and withdraws old promoted/partial aggregates, then writes
  `building`; interrupted multi-file output cannot be consumed. Incomplete
  successful runs end at `partial_ready`. Only a complete 90-overpass build that
  passes the ≥30 usable-date gate may commit `state=model_ready` together with
  matching hashes for every promoted file.

## 2026-07-18 — Pre-model baseline correction

- Removed the planned tract-target climatology baseline before feature
  construction or model fitting. Even when fitted only on a training fold, a
  tract historical LST mean is a target-derived predictor and conflicts with
  the project's stricter feature prohibition.
- Replaced it with a legal Ridge baseline using only prespecified static
  land-use/geography and seasonal terms. Tract identifiers remain prohibited
  predictors in every primary model and baseline.

## 2026-07-18 — Full development target promoted

- Completed all 90 target-blind 2020–2024 physical overpasses. The aggregate
  commit marker is `state=model_ready`, `build_complete=true`, and
  `promoted_outputs_valid=true`.
- Sixty-five independent dates passed the locked ≥50% date-retention rule,
  exceeding the predeclared minimum of 30. Thirty-four dates also passed every
  relative-endpoint spatial-representativeness gate. No gate was changed after
  observing these counts.
- Promoted 98,640 complete tract-date QA rows and 63,403 legal absolute-model
  rows. The build has zero duplicate keys and zero 2025 rows; all 1,096 GEOIDs
  retain invariant static eligible-pixel counts and exact pixel-identity hashes.
- Verified that 72,709,295 selected scene-contribution pixels equal the sum of
  tract valid-pixel counts and that all 7,241 hotspot positives reproduce from
  exact `LST DESC, GEOID ASC` ranking.
- Recorded a mandatory uncertainty sensitivity: 43,855/63,403 model rows have
  tract-median `ST_QA > 2 K`. This does not retroactively change the primary QA
  rule; the 2 K cutoff remains a prespecified robustness analysis and `ST_QA`
  remains prohibited as a predictor.

## 2026-07-18 — Phase 2 source and timing contract

- Locked the primary weather product to Daymet V4 R1 / DOI
  `10.3334/ORNLDAAC/2129`, aggregated over each tract's fixed eligible-land
  support. Target-day weather remains prohibited; 1/3/7-day windows end at
  `d−1`.
- Rejected the anonymous Planetary Computer Daymet mirror because it uses the
  superseded DOI `1840` and ends in 2020. Exact V4 R1 gridded retrieval will use
  authenticated NASA Earthdata access rather than silently mixing versions.
- Locked static model sources to products available before 2020: original NLCD
  2016 land cover/imperviousness, SRTM v3 observations via a byte-verified
  OpenTopography mirror, and Census TIGER/Line 2019 coastline via official bytes
  with a fixed archive fallback. WorldCover 2020 remains a support mask only and
  is not a predictor.
- Locked Sentinel-2 membership to physical acquisitions from local dates
  `d−60 … d−1`. Adjacent MGRS tiles and reprocessed STAC items cannot count as
  independent observations; processing cohorts are selected target-blind by
  AOI coverage, numeric baseline, generation time, and lexical item ID.
- Added fail-closed registry rules: static predictor sources must predate the
  entire 2020 development start year; dynamic model windows must end no later
  than `d−1`; IDs, coordinates, blocks, coverage, observation counts, QA,
  thermal/LST, labels, and target-derived fields cannot have role `model`.

## 2026-07-18 — Independent Phase 1 closure audit and Phase 2 source commits

- An independent read-only reconstruction found zero target-artifact P0, P1,
  or P2 defects. All 90 per-overpass caches and the four promoted aggregates
  match their recorded rows, byte counts, schemas, and SHA-256 hashes; 2025
  remains absent and locked.
- Corrected a report-only counting error: exact stored temperatures produce 11
  dates with a repeated hotspot cutoff value, of which six have a tie group
  crossing the top-k boundary. All six use ascending GEOID correctly, and all
  7,241 stored positives remain unchanged.
- Froze the target-blind Sentinel-2 metadata inventory at 226 physical
  acquisitions, 449 selected tile items, and 1,045 legal `d−60 … d−1`
  memberships spanning all 90 development target dates. No global cloud cutoff
  or 2025 data was used.
- Committed exact raw-source audits for the two required SRTM GL1 v3 GeoTIFFs
  and Census TIGER/Line 2019 coastline ZIP. The Census endpoint returned 403 in
  this environment, so the locked exact-URL Internet Archive memento was used
  and its ZIP members and CRCs were verified.

## 2026-07-18 — Official Daymet discovery frozen

- Froze the official Daymet V4 R1 collection to NASA CMR concept
  `C2532426483-ORNL_CLOUD`, DOI `10.3334/ORNLDAAC/2129`.
- The target-blind inventory contains exactly 30 granules: 2020–2024 ×
  `tmax`, `tmin`, `prcp`, `srad`, `vp`, and `dayl`. It has no duplicate
  year-variable keys and zero 2025 entries.
- Recorded inventory semantic SHA
  `7655215698f819c24514f74cbc79866f5178032beb4a521465fac2b5aff2ac5c`
  and Daymet-stage configuration SHA
  `28a2860109830087443cee4e7e90584633f336eb14195a437c3ffd4319a458d8`.
- Anonymous CMR discovery is sufficient for provenance, but OPeNDAP subset
  downloads redirect to Earthdata authentication. No token is available and no
  NetCDF value or weather feature has been created. Tokens must remain in the
  process environment and are never persisted or printed.

## 2026-07-18 — NLCD and static features promoted

- Downloaded official LA subsets of original NLCD 2016 land cover and
  imperviousness from the MRLC WCS at native 30 m EPSG:5070, with USGS DOI
  `10.5066/P937PN4Z`. The source commit is
  `86df6e71853a508cfd2133fb5546686c66bed5ca3dec3cde821ce006c9be5671`.
- Rejected the impervious TIFF's misleading metadata NoData value of 0. The
  scientific product NoData is 127; 0% imperviousness remains a valid value.
- Promoted a fixed-support static table with 1,096 unique GEOIDs, 18 legal model
  features, one audit-only NLCD reference fraction, no missing values, and
  minimum observed coverage 1.0 for NLCD land cover, NLCD imperviousness, SRTM
  elevation, SRTM slope, and coast distance. The table uses the same 1,166,782
  eligible pixels locked by the target stage.
- The ten reported NLCD land-cover fractions sum exactly to one for each tract.
  To avoid intercept collinearity, retained
  `nlcd_developed_medium_fraction` as audit-only and excluded it from models.
  This target-blind choice used only the static source distribution: it is
  nonzero in all 1,096 tracts and has the largest median fraction.
- Recorded static semantic table SHA
  `e14c760614889fa3e346c0f543b92db12f1dec12b2df7efe65af1d83c85f4fe6`,
  registry semantic SHA
  `562dbf03ba0ab47c498575cdd03af49091df3ed1ee4a0469fbecdf443bfb27bd`,
  and promoted static commit
  `1c6ea2e0ff446a53843084e4c985f7af91124c7ad6c738a7c9bbc79530f75666`.

## 2026-07-18 — Sentinel optical processor and real checkpoint locked

- Locked processing order to native 10/20 m grid-phase validation; area-average
  DN resampling to an aligned 20 m grid; max-mask propagation of any native
  saturation; BOA offset/quantification decoding; SCL 4/5 clear-land gating;
  and five-index calculation on one joint-valid band mask. Bilinear mixing is
  prohibited.
- Retained the target-blind 80% per-acquisition tract-coverage gate, at least
  three qualifying physical acquisitions, and exact `d−60 … d−1` membership.
- Locked the zero-intercept Bonafoni & Sekertekin (2020) albedo proxy, DOI
  `10.1109/LGRS.2020.2967085`: `0.2266·B02 + 0.1236·B03 + 0.1573·B04 +
  0.3417·B08 + 0.1170·B11 + 0.0338·B12`.
- A real baseline-04.00 COG-window calibration smoke passed. A separate
  baseline-03.00 physical acquisition then completed at full resolution with
  1,096 unique tract rows; 1,043 passed the 80% gate and the other 53 have all
  five features jointly missing. Per-tract denominator counts and identity
  hashes match the target support exactly, with zero forbidden target columns.
- Recorded stage configuration SHA
  `094dfb394bc45740343351bfaacaf6e558b92e1dd4d40313917756974b0e62b5`,
  processor SHA
  `68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c`,
  and acquisition-cache SHA
  `04efb29a0ca916f59b6cb0cb55f06360508f33adbb0d57256f7b9b27bfa6d3c5`.
  The stage remains `partial_ready` and unpromoted; this is an implementation
  checkpoint, not a completed 60-day feature table or a model result.

## 2026-07-19 — Target-blind key universe and grouped-validation draft

- Constructed the Phase 2 predictor key universe without reading an LST value,
  target QA table, or target-availability flag. It is the exact Cartesian
  product of 90 frozen eligible overpass dates and 1,096 fixed primary tracts:
  98,640 unique keys spanning 2020-05-16 through 2024-10-26, with no 2025 row.
  Its semantic key SHA is
  `5379959ef963f4f0506b8646d29ab95b1569a3ffeac288eaab93ecc1b139c747`.
  The artifact remains `target_blind_draft`; it is not a completed predictor
  table and contains no feature values.
- Predeclared five temporal leave-one-calendar-year-out folds for 2020–2024
  and 71 spatial leave-one-existing-5-km-block-out folds. Blocks are the fixed,
  target-independent centroid assignments already stored in the tract manifest;
  they are never merged or rebalanced using labels or row counts.
- Predeclared the full 5 × 71 Cartesian set of 355 joint folds. A joint test set
  is the held-out year AND held-out block. Its training set uses other years and
  excludes every fixed tract at polygon distance less than or equal to 1,000 m
  from the held-out block union in EPSG:3310; all other rows are explicitly
  purged.
- Predeclared nested tuning as leave-one-remaining-calendar-year-out inside
  each outer training set. Outer test and purged rows are excluded from both
  inner training and validation, and preprocessing is fit only on inner-train
  rows.
- Generated a target-value-blind split draft over the 63,403 legal development
  keys: 65 independent dates, 1,096 tracts, 71 spatial blocks, and 431 outer
  folds total. Every legal key is assigned to test exactly once within each of
  the temporal, spatial, and joint families. The split provenance commit SHA is
  `94246f690429b67dfba0b4d98fbb329eca8f62d4a1f42a16621ae343232a29f4`.
- Froze date-macro MAE as the primary absolute-LST metric. Secondary absolute
  metrics are pooled RMSE and R², pooled and date-macro signed error, date-macro
  within-date anomaly MAE, and median per-date Spearman correlation. Every
  metric report must include row count, independent overpass-date count, and
  independent spatial-block count. Constant-date Spearman values are reported
  as undefined rather than silently converted to zero.
- These split artifacts remain `predeclared_draft` and are not yet ready for
  model evaluation. Promotion still requires exact one-to-one agreement with
  the completed predictor keys plus fold-local preprocessing isolation tests.

## 2026-07-19 — Known-at-origin seasonality and legal model contracts

- Added exactly two deterministic calendar predictors,
  `calendar_doy_sin` and `calendar_doy_cos`, using
  `theta = 2*pi*(dayofyear-1)/(365+is_leap_year)`. They are calculated from the
  Los Angeles civil target date, are known at the 00:00 prediction origin, and
  are not observed target-day data. Their source-window offsets are therefore
  null rather than falsely labeled `d-1`.
- The registry accepts this null-window exception only for the complete,
  exactly named sin/cos pair with frozen metadata. Every weather, satellite, or
  other observed dynamic model feature still requires a finite integer window
  ending no later than `d-1`.
- Generated the calendar table on all 98,640 target-blind predictor keys, not
  from the 63,403 QA-available target rows. It contains 90 dates and 1,096
  tracts with no missing values or 2025 rows. Its semantic table SHA is
  `9c7cfc530a696ee65b9c0568db83eebb33535e74f46e3d0328e6a29819acb4ca`.
- Generated a metadata-only combined Phase 2 registry draft with 49 rows: two
  keys, 46 model features, and one audit-only static reference. The model vector
  is exactly 18 static land-use/geography + 2 calendar + 21 Daymet + 5 Sentinel
  features. Its ordered semantic SHA is
  `0638ef9c0e8c440764efede0cff2ef694e86d8d64373e23a43f2c87623f1168e`.
  It remains `predeclared_draft`; unfinished dynamic values and coverage prevent
  Phase 2 promotion.
- Defined B0 as an equal-overpass-date-weight annual first-harmonic baseline.
  Inside each training fold only, tract LST is averaged once per date and an
  intercept plus the two calendar terms is fit to those date means. The date
  means are training responses only and are never saved or joined as features.
- Defined B1 as 21 Daymet + 2 calendar features with Ridge; B2 as 18 static + 2
  calendar features with Ridge; M1 as all 46 features with Elastic Net; and M2
  as all 46 features with histogram gradient boosting.
- For B1, B2, M1, and M2, training rows receive weights
  `N/(D*n_d)`, calculated only from the current training fold, so every physical
  overpass date has equal total loss weight. B0 already has one equal-weight row
  per training date.
- Static and calendar missingness is a hard failure. Only observed dynamic
  weather/Sentinel values may be median-imputed, using training-fold medians;
  an all-missing training feature is a hard failure and no missingness indicator
  is added. Ridge/Elastic Net scale inside the fold. M2 uses absolute-error loss,
  median-imputed dynamics, fixed randomness, and `early_stopping=false` so it
  cannot create an internal random row-level validation split.
- These are pipeline and leakage contracts, not trained models. Candidate
  hyperparameters and their selection rule are frozen separately below before
  any score is inspected.

## 2026-07-19 — Automatic Sentinel orchestration recovery

- A single transient network, STAC, or remote-raster read failure no longer
  requests a global safe pause. The failed physical acquisition enters a
  delayed retry queue while other workers continue.
- Retry events record only the frozen physical acquisition ID, exception type,
  attempt count, and delay. Exception messages and signed remote URLs are never
  written to the dashboard or recovery provenance.
- Retry exhaustion triggers an automatic runner reconstruction only when every
  quarantined error is classified as transient. Reconstruction reloads frozen
  inputs and revalidates acquisition cache locks before resuming. Configuration,
  schema, hash, permissions, and scientific-integrity failures remain
  fail-closed.
- Dashboard run/pause/complete intent is written atomically. A process restart
  resumes only a persisted `running` intent, so an explicit safe pause cannot be
  silently overridden.
- A separate single-instance watchdog restarts the dashboard after a nonzero
  process exit with capped exponential backoff. It never owns or bypasses the
  acquisition-cache lock.
- These are audit-only orchestration changes. Sentinel feature definitions,
  masks, fixed support, cache locks, and scientific processor SHA remain
  unchanged.

## 2026-07-20 — Cache-preserving Sentinel final compilation

- The 226/226 acquisition caches completed successfully, but the first final
  compile stopped before promotion with a Pandas `MergeError`. Cache audit found
  no corrupt file, schema mismatch, duplicate business key, changed denominator,
  or missing acquisition.
- The frozen composite function declared the membership-to-tract merge as
  `one_to_many`. In the complete inventory, 203 physical acquisitions legally
  belong to multiple target-date windows, while each acquisition also has 1,096
  tract rows. The intended expansion is therefore many-to-many across the join
  columns, with uniqueness enforced by the natural
  `(target_date, tract_geoid, physical_acquisition_id)` lineage key.
- Directly editing the fingerprinted composite function would have changed the
  full scientific pipeline SHA and unnecessarily invalidated all 226 valid
  acquisition caches. Instead, the audit-only compile adapter invokes the exact
  frozen composite independently for each target date and concatenates the
  independent outputs. Target-date groups share no aggregation or availability
  state, so this changes join-validation scope only.
- On the full frozen data, a read-only intended-many-to-many reference and the
  target-sharded adapter were exactly equal in values, row order, columns, and
  dtypes for features, audit, and lineage. A regression test now covers one
  acquisition reused across multiple target dates and preserves its physical ID
  and source ages.
- Final promotion passed: 247,696 acquisition-tract rows; 98,640 model/audit
  rows; 97,870 feature-available rows; and 1,145,320 lineage rows. There are no
  duplicate keys, 2025 rows, target-day/future sources, or violations of the
  invariant static denominator. Source ages are exactly within `d-60 ... d-1`.
- The scientific processor SHA remains
  `68774cc3cf9de77c55d23802d59b62a8c2a28f09c3edf79f90b8c3a4c390f34c`.
  The completed source/interim feature SHA is
  `1114f61188f55258e4dae95c23cbd02d79bd0b60969e1e2d595b13ad2c9c8154`,
  and compile adapter version is `sentinel-target-sharded-compile-v1`.
- Independently normalized and promoted the source outputs without reading any
  target value or target-QA table. The canonical processed feature SHA is
  `aa02df3a00c51076610f442512949ade5ca70ab466b4d2d9c513826184fe82b5`;
  the promotion commit is
  `bf3adfffcfe52df7cca7c366fa214d6cb11a5cca4bf1111454c99c87fd48e291`.
- Of 98,640 predictor rows, 97,870 have all five optical values and 770 retain
  all five as missing. Missingness occurs on 31 dates and in 537 tracts; minimum
  availability is 56.75% by date and 85.56% by tract. No date or tract was
  removed and no coverage threshold was changed.
- Froze the missingness reporting rule before model scores: the primary
  analysis keeps every otherwise legal row and uses training-fold-only dynamic
  medians. Reports must stratify errors by Sentinel availability and include a
  prediction-level complete-Sentinel sensitivity without using it to tune,
  select, or retrospectively drop a date.

## 2026-07-20 — Daymet post-download compiler completed without fabricating data

- Implemented and tested the production compiler that will consume the 30
  authenticated Daymet V4 R1 LA subsets. It verifies inventory/file hashes,
  native 1 km Lambert conformal conic coordinates, units and fill/scale
  metadata, and Daymet's 365-day calendar.
- Locked fixed eligible-land tract-cell weights with no date-specific
  renormalization, cell-first shortwave-energy calculation, and exact 1/3/7-day
  windows ending at `d−1`. The intended model output has exactly 21 weather
  predictors plus separate audit, fixed-weight, and provenance artifacts.
- No authenticated subset is present: `subset_downloads.csv`, raw NetCDF
  subsets, fixed weights, and Daymet feature outputs do not exist. The compiler
  was therefore not run on production inputs, no anonymous substitute was used,
  and no weather value was fabricated.
- A real downloaded-file smoke and the complete target-blind readiness audit
  remain mandatory immediately after authentication.
- A first interactive attempt under Windows PowerShell 5.1 used the unsupported
  `Read-Host -MaskInput` syntax. Because the whole block was pasted, the literal
  next line `try {` became the environment value; the user's real token was
  neither submitted nor exposed. Earthdata rejected that invalid string before
  any subset request completed. The public 30-granule inventory was refreshed
  with the same semantic SHA, and no raw, partial, manifest, or derived Daymet
  file was left behind.
- Added `--prompt-token` to the Python staging CLI. It uses a hidden terminal
  prompt, refuses non-TTY input, rejects empty or untrimmed credentials, and
  fails before networking if any configured token environment variable exists.
  Only the provenance label `interactive_prompt` may be recorded; the token
  value is never passed in argv, printed, or persisted.

## 2026-07-20 — Pre-score model-selection freeze

- Froze 1 B0, 5 B1 Ridge, 5 B2 Ridge, 12 M1 Elastic Net, and 8 M2 histogram
  gradient-boosting candidates before fitting a model or reading any target or
  performance score for selection.
- Inner validation remains leave-one-remaining-calendar-year-out and strictly
  inside each outer-training set. Preprocessing is refit on every inner-training
  partition; the selected pipeline is refit only on the complete outer train.
- Froze the objective as mean MAE across the stitched independent inner-OOF
  dates. Each overpass date has equal weight; tract-row counts and validation-year
  sizes cannot weight selection.
- Froze the choice rule as minimum stitched date-macro MAE. Only candidates
  within `1e-12 °C` of the numerical minimum are tied; ties use the predeclared
  simpler-first complexity rank and then candidate ID. This is not a
  one-standard-error rule.
- The fail-closed selector requires every candidate to match the caller-supplied
  exact validation-date set from the grouped fold manifest. It rejects
  duplicates, all-candidate joint date omissions, invalid scores, non-civil
  dates, or 2025/later dates. Calendar year 2025 remains locked throughout
  tuning and selection.
- The authoritative configuration semantic SHA-256 is
  `98f0429f3f2daa6f61f2bf260ff284f7fe08cc52487ee6f11abcab05b98fcec0`.
  The independent pre-score freeze commit is
  `4d8c2bd37be67f9f46d89d1dec8d5ed0aab196b24b43f9745ff730f040f2a6cd`;
  exact grids and complexity order are documented in
  `docs/MODEL_SELECTION_SPEC.md`.

## 2026-07-20 — Target-blind Phase 2 readiness remains blocked only by Daymet

- Ran the readiness audit without opening Landsat target values, target-QA
  tables, or model scores. It verified the exact 98,640-key universe, 49-row
  registry contract, 1,096-row static table, 98,640-row calendar table, formal
  Sentinel table and lineage, invariant denominator, legal temporal cutoffs,
  and the locked 2025 state.
- The truthful state is `blocked_missing_daymet_values`, with blocker
  `blocked_missing_authenticated_subsets`, `phase2_complete=false`, and
  `ready_for_feature_assembly=false`. No combined model-ready table was
  fabricated or promoted.
- Before production weather values arrive, strengthened the Daymet-present
  audit to require exact 1/3/7-day start/end dates, expected and complete day
  counts, nested-window consistency, finite values for each seven-feature
  window, and exact agreement between all 21 feature values and the availability
  flag.
- The canonical readiness commit is
  `7a38e450d7c3cfc4260e7c684cae84075d8e1c85d10a0904849214a42dedcc9d`.

## 2026-07-20 — Corrected CMR Service-Bridge result parsing

- A hidden-prompt request with the user's Earthdata Login token first received
  an authentication rejection, then a later request passed authorization and
  reached a successful JSON response. The remaining failure was therefore
  local response parsing, not evidence that the second token entry was wrong.
- NASA's CMR Service-Bridge contract defines `hits` as total matches and
  `items` as the returned result page; `page-size` may limit the latter.
  Removed the invalid requirement that `hits == 1` and `len(items) == 1`
  must both hold for one requested granule.
- A subsequent successful response had one item on the official host and the
  exact frozen granule path, but exposed that the generated query did not repeat
  the data-variable name in the one spelling assumed locally. The
  Service-Bridge contract does not freeze its generated query-string syntax, so
  that query is now treated as opaque rather than parsed for a variable token.
- The parser accepts only one credential-free HTTPS URL whose official host,
  exact frozen granule path, DAP4 NetCDF suffix, and non-empty subset query match
  the audited CMR inventory. Zero or multiple exact matches still fail closed;
  no URL query or token is logged. Each downloaded or cached file is immediately
  audited for the requested variable, year, 365-day axis, units, 1 km CRS/grid,
  and all 30 subsets must share one grid before the manifest is promoted.
- Added per-granule `[n/30]` progress without URLs or credentials. The direct
  index-based cloud OPeNDAP route documented by ORNL remains the fallback if the
  corrected Service-Bridge response has no unique constrained match.
- Primary references: NASA CMR Service-Bridge REST documentation
  (`https://cmr.earthdata.nasa.gov/service-bridge/docs/current/rest-api`)
  and the ORNL DAAC current cloud OPeNDAP guidance
  (`https://forum.earthdata.nasa.gov/viewtopic.php?t=7585`).

## 2026-07-20 — Daymet direct DAP4 completion and Phase 2 promotion

- The final live CMR Service-Bridge response identified the exact official
  granule but did not provide a usable constrained subset URL. Further
  query-string parsing could not make an unconstrained URL scientifically or
  operationally equivalent to the requested LA subset, so the Service-Bridge
  route was not used for production downloads.
- Adopted the official Earthdata cloud DAP4 endpoint with the frozen inclusive
  native-grid indices `y=5666:5745` and `x=2900:2963`. Every request also
  constrained the complete 365-day time axis, coordinates, grid-mapping
  variable, and exactly one weather variable. This is recorded as access route
  `direct_dap4_fixed_indices_v1`.
- Downloaded and validated all 30 Daymet V4 R1 subsets: six variables for each
  year 2020–2024, totaling 70,840,908 bytes. The download-manifest SHA-256 is
  `329dbfdac420c8ebef2a48d5a75de36c7a98cde5fa20268ccb5f3a6eec3d73b4`;
  it records a distinct content hash for every raw file. No 2025 granule was
  requested or downloaded.
- The fixed-support Daymet compiler produced 98,640 tract-date rows with 21
  lagged weather predictors and no missing values. The Parquet SHA-256 is
  `11af1670280c54637bac883c7525fbee033139d9e00d6fd42f3e91124f584590`,
  and the stage commit is
  `a7da6a107695787f047547275669217c6bd508b12852e2d6c244078f687c0ea9`.
- Re-ran the target-blind readiness audit. All five predictor families and the
  registry passed, with no blocker and no target, target-QA, prediction, or
  model-score value read. The readiness artifact SHA-256 is
  `86938c7e597be689659fa56b6246a7d81be55092af08c1c6bda8cac18f5d228e`;
  its commit is
  `92534764be459110ff239670f320e3b947313f344ca00774c3097cff42fa3762`.
- Promoted the target-blind Phase 2 table with exactly 98,640 rows and 49
  columns: two keys, 46 model predictors, and one audit-only feature. Its
  Parquet SHA-256 is
  `39b27d358a29e6104ed9573a381b16efd7fb29d5e90ac958503118d1d1235752`;
  the promotion commit is
  `3f5e4017713f90a47a4a5b1eefdb4e91bb6141bfb1f0458d9a168dd785c2a364`.
- Only after that target-blind promotion, joined the legal Landsat target rows
  to produce the development model table: 63,403 rows, 50 columns, 65
  independent overpass dates, and 1,096 tracts. Its Parquet SHA-256 is
  `3a365f5dd3e9d410585a34695bb444d67b220c4b40a9348288a540078bc27f75`;
  the gated assembly commit is
  `9c2f903993167fc2a228b3cfe60a23fe33f57f252bae6299458338cb8eb967ad`.
- Calendar year 2025 remains absent from all of these artifacts and locked as
  the final test set. `unlock_final_test` remains `false`.

## 2026-07-20 — Validation split promotion

- Promoted the predeclared grouped validation splits only after exact agreement
  with the completed model-table keys. The promotion read only
  `tract_geoid` and `target_date` from that table; it did not read the target,
  predictor values, predictions, or model scores.
- The promoted support contains 63,403 legal rows, 65 independent overpass
  dates, 1,096 tracts, and 71 fixed spatial blocks. The 431 folds comprise five
  whole-year temporal folds, 71 whole-block spatial folds, and 355 joint
  year-by-block folds with the frozen 1 km purge.
- Each legal row is an out-of-fold test row exactly once within each split
  family. The row-group SHA-256 is
  `400de375885369777f717494aae314d1fe4e7c9ec44be1c3d11c38872fff24a8`,
  the fold-definition SHA-256 is
  `73ebb7c41847fd4a9554ba2cd71fbfa9dc8e57b54b7e0a82b91ccf3703dccf6c`,
  and the promotion commit is
  `6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc`.
- The split artifacts contain no 2025 rows; the final-test lock remains in
  force.

## 2026-07-20 — Resumable grouped-model execution and runtime calibration

- Implemented the predeclared nested evaluation as an immutable 57,800-task
  plan: 55,645 candidate-by-inner-year fits followed by 2,155 fold-local
  selected-candidate outer refits. Outer tasks cannot be claimed while any
  inner task remains pending, running, or quarantined.
- Derived execution run ID
  `c81b941cf92134b3bb9b11a73ca97c1e784eb3a6784a2c39c370dd524d98fbc9`
  from the authenticated model context, upstream commits, exact task-plan SHA
  `0d662d5d75d4d8aad085f50038513e36c4c02e10d266b77d4972151ad904a103`,
  and complete runner/task/compiler runtime fingerprint. A code or plan change
  therefore cannot silently reuse old task results.
- Chose a SQLite WAL queue with expiring leases and fencing generations.
  Cooperative pause stops new claims and drains current fits; a stale worker
  cannot commit after lease reclamation. Ordinary failures retry automatically
  with bounded exponential backoff, then quarantine and allow unrelated work
  in the same phase to continue. Quarantine blocks phase promotion and final
  publication.
- A runtime-only calibration sampled one conservative inner fit per model after
  the candidate grid was frozen. It did not mutate the production queue and did
  not inspect or publish prediction scores. B0/B1/B2 took 2.50–3.08 seconds;
  M1/M2 took 20.72–21.03 seconds. The rough one-worker projection is 803,071
  seconds, so the local controller defaults to four single-threaded workers and
  exposes 1–8 workers. This operational choice did not change candidates,
  folds, metrics, or thresholds.
- Completed one formal B0 inner task as an end-to-end smoke. The durable queue
  is paused at 1 / 57,800 complete, zero running, zero quarantined. No outer OOF
  metric, candidate comparison, scientific performance claim, or 2025 result
  has been inspected. The 2025 lock remains unchanged.
- Added a localhost model dashboard on port 8766 and an external watchdog. It
  provides persistent Start/Continue and Safe Pause controls, status/ETA, and
  automatic coordinator restart without turning a scientifically blocked state
  into an endless retry loop.

## 2026-07-20 — Audited gaming-laptop handoff and portable run v2

- Chose a new clean execution queue for the Ryzen 7 8845H laptop instead of
  copying the 42 completed v1 inner-fit smoke tasks. Portable path support,
  run-relative outer fragments, and the expanded modeling-library runtime lock
  change the authenticated runner schema; mixing v1 and v2 results is forbidden.
- Kept the three upstream committed manifests byte-identical. A separate
  canonical `portable_relocation.json` maps their original absolute paths to
  nine byte-locked bundle-relative copies and is revalidated before the model
  context or any Parquet input is opened.
- Upgraded the context, grouped runner, worker result, and compiler contracts to
  v2. Outer fragment metadata is relative to its immutable run directory and
  compiled output metadata is relative to its output directory, so a returned
  run can be audited after moving the enclosing folder.
- Expanded the runtime fingerprint to include scikit-learn, SciPy, joblib, and
  threadpoolctl. The authenticated context ID is
  `37decc26a90832de1b9ce89d85f862f510659880f33a36f408ad89c670b29a45`;
  the fresh execution ID is
  `eb2d09ce9592d5531b51e3e507634aa25f25ef1323376b056dd79fae948876f5`;
  and the unchanged exact task-plan SHA is
  `0d662d5d75d4d8aad085f50038513e36c4c02e10d266b77d4972151ad904a103`.
- The portable controller defaults to six single-threaded worker processes and
  exposes six or eight in the dashboard. A change is accepted only while the
  queue is fully paused and applies to the next Start. Eight is an optional
  throughput setting; six is the conservative thermal/memory default.
- The source is handed off only after `desired_state=paused`, zero running
  tasks, full tests, Ruff, file hashing, and credential scanning. The source
  coordinator and dashboard Start API then fail closed on the `transferred_out`
  ownership marker. The laptop starts a fresh paused 55,645-inner + 2,155-outer
  queue and requires an explicit dashboard Start.
- No candidate score, outer prediction, or final-test value was read during the
  portability work. Calendar year 2025 remains absent and locked.

## 2026-07-22 — Returned archive authority and canonical result import

- Accepted `D:\Downloads\FINAL _VER.zip` as the returned-run authority only
  after strict ZIP-member, size, hash, queue, OOF, metric, and provenance
  validation. Its SHA-256 is
  `0a07e9e3f016b0ed67a5f00085b0ab74ebd0f5273b58f9cbadbb07aa6ac0a335`.
- Diagnosed the 2,155 missing outer fragments in the earlier extracted
  directory as a Windows long-path extraction failure, not a failed model run
  or missing ZIP content. The canonical import read all 2,155 original fragment
  members directly from the ZIP and reconstructed zero fragments.
- Verified the terminal queue at 57,800 / 57,800 complete: 55,645 inner fits
  and 2,155 outer refits, with no pending, running, or quarantined task. The
  canonical import audit commit is
  `b0382cdc792d9716ebf13dcfd69256e08a0920bd8ac45a0fe554946ae6640393`.
- Preserved the authenticated compiled outputs and their byte locks in the
  canonical evaluation directory. This import decision changes no model,
  candidate, fold, prediction, metric, or threshold.

## 2026-07-22 — Initial development-result analysis and gate interpretation

- Selected the strongest legal baseline separately within each validation
  family only from B0–B2 by the frozen primary date-macro MAE. For the required
  joint comparison, B1 is strongest at 2.516141 °C; joint M2 is 2.108788 °C.
- Recorded an absolute M2 improvement of 0.407353 °C and a relative improvement
  of 16.1896% over joint B1 on 63,403 development rows, 65 independent dates,
  and 71 spatial blocks. Joint M2 median per-date Spearman is 0.792785.
- Used the predeclared paired crossed bootstrap: aggregate to date-by-block
  cells first, then independently resample complete dates and complete spatial
  blocks with replacement for 5,000 fixed-seed replicates. Individual
  tract-date rows were never bootstrap units. The 95% relative-improvement
  interval is 4.2088%–27.6883%, `P(improvement > 0)=0.995`, and
  `P(improvement > 10%)=0.8516`.
- The required development gates pass: median per-date Spearman is at least
  0.50, point improvement is at least 10%, and the relative-improvement CI lower
  bound is above zero. The separately reported stronger check that the entire
  CI exceed 10% does not pass. The threshold was not weakened or reinterpreted
  after observing the result. Analysis provenance commit:
  `d8bfaf258590d2ef42abe37a73fb92eeba3d66d61f23fd5f85b8e8c5616844c4`.
- Classified this as frozen 2020–2024 development OOF evidence, not the final
  test. Calendar year 2025 remains locked. Mandatory robustness, hotspot,
  sensor-stratified, residual, spatial-autocorrelation, ablation, and failure
  analyses must precede the full model lock and any one-time 2025 unlock.

## 2026-07-22 — Endpoint, sensor, and Sentinel-missingness diagnostics

- Restricted the relative-hotspot endpoint to the 34 dates that already passed
  the frozen spatial-representativeness gate; no failed date was promoted after
  observing scores. The analysis covers 36,139 rows and uses exact top-k with
  the frozen GEOID tie-break.
- In joint OOF evaluation, B1 versus M2 mean per-date average precision is
  0.397683 versus 0.666949, and exact-top-20% recall is 0.420779 versus
  0.613917. These are predictive endpoint results, not health outcomes.
- Landsat 8 and Landsat 9 both retain the direction of M2 improvement. The
  all-five-Sentinel-missing group contains only 168 rows across 12 dates and 29
  blocks and is explicitly exploratory. Provenance commit:
  `2ec9540ca7817dd53802e5849a35a8499b7f0755c7057b3c3b4292183475881a`.

## 2026-07-22 — QA cohorts and failure cases

- Froze QA and failure-case diagnostics without changing any model, split,
  threshold, or predictor. The QA all-row bootstrap uses its own fixed seed and
  is a diagnostic rerun; it is not substituted for the primary result-analysis
  bootstrap.
- The tract-median ST_QA <= 2 K cohort shows an 8.8608% M2 improvement with a
  95% interval of -9.8226% to 26.4133%. This tract-summary cohort is not the
  prespecified pixel-level hard-mask sensitivity and cannot replace it.
- The Sentinel-missing group has only 168 rows and a wide -59.5131% to 53.3188%
  interval, so it is reported only as sparse exploratory evidence. Provenance
  commit:
  `938a837cde8f49785e708e7960a4bca680ad851007a142189dbf4f3ac640ef8e`.

## 2026-07-22 — Residual spatial structure remains a limitation

- Rook-adjacency Moran's I was computed by date from authenticated joint OOF
  residuals with 999 fixed-seed permutations. Mean Moran's I is 0.639090 for B1
  and 0.574355 for M2; medians are 0.669207 and 0.575018.
- M2 reduces but does not eliminate strong residual spatial clustering. All 65
  date-level statistics are positive and their exploratory p-values are
  unadjusted, so they are reported as diagnostics rather than confirmatory
  multiple-testing claims. Provenance commit:
  `44047f7c9dd72135ab93d06bb4dc31144772ea012de98ba117768a185fdccc30`.

## 2026-07-22 — Reproducible development diagnostic figures frozen

- Froze four generated figures: joint performance overview, QA-cohort forest,
  worst-date errors, and fixed-date LST/prediction/residual maps. The map dates
  were selected before inspecting their model scores, and the failed relative
  endpoint date is visibly labeled rather than omitted.
- Figure provenance commit:
  `0e69b5bff544a92fb14015ebc75da5f27437420e68c83d55167749de97defda8`.
  Calendar year 2025 remains locked. Feature-family ablation and the strict
  pixel-level ST_QA sensitivity remain separate unfinished decisions.

## 2026-07-22 — Feature-set ablation completed

- Completed all 1,293 reduced-feature grouped refits with zero quarantine and
  authenticated the compiled outputs against the canonical M2 OOF lineage.
- Full-feature M2 improves over calendar + weather by 17.7867% (95% CI
  9.6191%–25.8203%), over calendar + land-use/geography by 37.0895%
  (25.3337%–46.8952%), and over calendar + lagged satellite by 41.6528%
  (31.9740%–50.1712%).
- Interpreted these only as predictive associations among predeclared refitted
  feature sets. They are not leave-one-variable importance and not causal
  effects. Provenance commit:
  `31afec41abc3448f9732567ad671d73938d5a620f0adc7d2421cb690edc76ae1`.

## 2026-07-22 — Strict pixel-level ST_QA sensitivity completed but gate failed

- Applied `ST_QA <= 2 K` before aggregation across all 90 overpass dates while
  preserving exact fixed eligible-land support.
- Only 15 dates passed the unchanged usable-date gate, below the predeclared
  minimum of 30. Recorded `complete_gate_failed`; this is a scientific support
  limitation, not a software failure, and the strict target was not promoted.
- On 11,808 strict-label rows, frozen primary OOF M2 improves over B1 by
  17.7702%, but the 95% crossed date-by-block interval is -5.2068% to 39.5864%.
  The comparison is not a strict-label refit and does not provide a robust
  positive-CI claim. Provenance commit:
  `71b9b5fd3b0768086852f0fa8d9fcd834d8719778a93f5d65ef6e1a8dfe46021`.

## 2026-07-22 — Development robustness evidence reconciled

- Authenticated initial performance, endpoint/sensor, QA/failure, residual
  spatial, diagnostic figures, feature ablation, and strict pixel-level ST_QA
  under one shared compile and OOF lineage.
- Kept the predeclared primary CI distinct from the separate QA seed rerun;
  kept tract-median ST_QA distinct from pixel-level reaggregation; retained
  sparse-group and Moran results as exploratory; and prohibited causal feature
  importance claims.
- Froze reconciliation commit
  `7b7aa40a49ae5d3fb415e16ed8202205f70c8bdce795087b1d98467f453722df`
  and generated the development-only report. Calendar year 2025 remains locked
  and no automatic unlock is authorized.

## 2026-07-23 — Final-fit job prepared but deliberately not started

- Authenticated the development-only inputs and froze a resumable plan for 65
  leave-one-year-out candidate fits followed by two full-development refits.
  Preparation performed no fitting; the controller remains paused at 0/65.
- Added a localhost controller on port 8766 that begins every new UI session
  paused and disarmed. Only an explicit Start/Continue click in that same
  session can launch work. Exact project process matching, an OS-released
  single-instance lock, stop-time revalidation, bounded restart backoff, and a
  no-progress circuit breaker prevent accidental or duplicate computation.
- Preserved the frozen Sentinel pipeline fingerprint. The only detected drift
  was an unrelated dependency-manifest edit; the Sentinel scientific source
  files and runtime packages were unchanged, so the exact frozen manifest bytes
  were restored instead of invalidating all 226 authenticated caches.
- Re-ran the complete repository verification: 541 tests passed and Ruff
  passed. No final fit, 2025 read, model-lock promotion, or final-test unlock
  occurred.

## 2026-07-23 — Full-development models and formal lock frozen

- Completed the development-only final tuning plan and refit the selected B1
  ridge and M2 histogram-gradient-boosting pipelines on all legal 2020–2024
  development rows. Both serialized artifacts and their runtime fingerprints
  were authenticated before promotion.
- Promoted the immutable `MODEL_LOCK.json` from committed training code
  `1437fb8317a5bdc93b1d4587a9627ca8fa4f46f6` and committed staging record
  `dc73353f2ab8a270a862628d462915b67dc9317c`. The formal lock commit SHA-256 is
  `584ccfcb6a32a5a9c380e6e029f5205b91b21684ca6655f240eb72d49e76115b`.
- The lock records `final_test_locked=true`, `final_test_values_read=false`, and
  `one_time_final_evaluation_authorized=false`. No 2025 feature, target, score,
  or metric was read. A separate frozen predict-only evaluator and one-way
  authorization are required before the single final-test run.

## 2026-07-23 — Target-blind 2025 weather inventory frozen

- Derived the exact weather requirement from the frozen 23-date by 1,096-tract
  key universe. All dynamic weather windows end on target day minus one.
- Queried NASA CMR and froze the six Daymet V4 R1 annual granules required for
  161 weather dates from 2025-04-29 through 2025-10-28.
- This stage read metadata only. It did not download weather values, open a
  Landsat target or QA asset, load a fitted model, or consume the one-time final
  evaluation. The 2025 label lock remains closed.

## 2026-07-23 — Target-blind 2025 Sentinel inventory frozen

- Queried Element 84 Earth Search for the exact d-60 through d-1 windows of the
  23 locked target dates, without a global scene-cloud cutoff. Froze 34 physical
  acquisitions represented by 67 selected tile items and 192 exact
  target-window memberships. Thirty-three acquisitions are adjacent two-tile
  mosaics and one is a single-tile acquisition.
- Corrected the first metadata-only draft before opening any optical pixels:
  Earth Search records adjacent tiles about 14 seconds apart even though their
  shared Sentinel datatake timestamp identifies one physical overpass. Physical
  acquisition time is now derived and cross-validated from `s2:datatake_id` and
  `s2:product_uri`; the superseded 67-acquisition draft is retained under
  `data/interim/superseded/` for audit only.
- Kept the official requester-pays product-metadata S3 URI as audit lineage and
  did not synthesize an inaccessible HTTPS URL. For scientific decoding, froze
  each public COG asset's STAC Raster scale and offset; all selected reflectance
  bands specify `DN * 0.0001 - 0.1`.
- The inventory stage read metadata only and did not open optical pixels,
  Landsat target/QA assets, models, predictions, or scores. The 2025 label lock
  remains closed.

## 2026-07-23 — First 2025 Sentinel feature run superseded

- Processed all 34 frozen physical acquisitions from 67 public optical COG
  tiles, with adjacent tiles mosaicked before tract aggregation. Transient warp
  failures were retried automatically; all 34 acquisition caches completed and
  the final compile reported zero failures.
- The run produced 25,208 structurally complete tract-date rows, but the
  post-run cross-provider calibration audit invalidated every optical feature.
  Legacy Earth Search `sentinel-2-l2a` COG pixels had already incorporated the
  BOA additive offset even though their raster metadata advertised `-0.1`.
  Applying that metadata caused a second subtraction.
- For the same 2025-04-27 tile and pixel window, Planetary Computer and Earth
  Search `sentinel-2-c1-l2a` raw DNs matched exactly, while every tested legacy
  COG DN was 1,000 lower. Invalid value ranges in NDVI, NDWI, and albedo
  independently corroborated the failure. Because the indices are nonlinear,
  no aggregate correction is permitted.
- Moved the original 34 caches and aggregate outputs unchanged to
  `data/interim/superseded/sentinel_features_double_offset_20260723/`. They are
  audit evidence only and cannot satisfy any final-predictor provenance check.
  The replacement will use the C1 collection after raw-DN parity validation.
- Every source date is between target day minus 60 and target day minus one.
  Static eligible-land counts and exact pixel identities remained invariant for
  all 1,096 tracts. No Landsat target, QA, prediction, or score value was read,
  and the one-time final evaluation remains unconsumed.

## 2026-07-23 — Target-blind 2025 Daymet subsets completed

- Downloaded the six frozen Daymet V4 R1 variables through the official direct
  DAP4 fixed-index route. The files contain the exact 80 by 64 Los Angeles grid
  cells required by the previously frozen tract weights.
- Recorded every source URL, variable, array slice, byte count, and SHA-256 in
  `manifests/final_test_2025/daymet_grid/subset_downloads.csv`; total payload
  size is 14,131,863 bytes.
- This stage accessed weather predictors only. It did not read a Landsat
  target, QA value, fitted prediction, or score, and did not consume the
  one-time final evaluation.

## 2026-07-26 — Sentinel Collection 1 metadata cardinality preflight

- Requeried the exact frozen 23-date `d−60 … d−1` window against Element 84
  Earth Search `sentinel-2-c1-l2a`, without opening optical pixels or applying
  a global scene-cloud cutoff.
- The complete Collection 1 cohort contains 36 physical acquisitions,
  72 selected tile items, and 207 exact target-window memberships. All 36
  acquisitions are adjacent two-tile mosaics. Relative to the superseded
  legacy inventory, Collection 1 adds the eligible 2025-06-16 and 2025-07-06
  overpasses and supplies both required tiles for every retained acquisition.
- Updated the feature-engine and dashboard cardinality contracts to 36
  acquisitions and 72 items. Membership remains derived and authenticated as
  an exact crosswalk rather than hard-coded.
- This was a metadata-only reproducibility preflight. It did not start the
  36-acquisition pixel build, read Landsat target/QA values, load predictions
  or scores, or consume the one-time final evaluation.

## 2026-07-26 — Sentinel Collection 1 final-test inventory frozen

- Rebuilt the canonical 2025 Sentinel inventory exclusively from Element 84
  Earth Search `sentinel-2-c1-l2a`. The authenticated inventory contains 36
  physical acquisitions, 72 tile items, and 207 exact target-window
  memberships; every acquisition is an `11SLT + 11SLU` mosaic.
- Froze all 72 raw STAC snapshots and authenticated their exact filename,
  item-ID, byte-count, and SHA-256 set. Every selected raster URL is on the
  fixed Collection 1 host and path, with no query string, legacy COG URL, or
  `earthsearch:boa_offset_applied` flag.
- Locked the decoding contract to native Sen2Cor L2A integer values:
  `reflectance = DN × 0.0001 − 0.1`, applied exactly once. The independent
  256 × 256 B04 calibration window matched Planetary Computer pixel-for-pixel;
  the prohibited legacy COG negative control was exactly 1,000 DN lower at
  every pixel.
- Independent audit passed the complete date-window crosswalk, two-tile mosaic
  membership, URL/calibration contracts, snapshot exact set, current pipeline
  fingerprint, and target-blind flags. A PySTAC storage-extension migration
  warning was classified as non-material because the pipeline never consumes
  that extension and no selected asset or calibration metadata was changed.
- The legacy 34-acquisition inventory remains only under
  `data/interim/superseded/sentinel_inventory_legacy_double_offset_20260723/`.
  It is prohibited for feature generation.

## 2026-07-26 — Target-blind 2025 Daymet features completed

- Compiled the six frozen Daymet V4 R1 subsets through the invariant
  development-era 1 km cell weights into 25,208 tract-date rows
  (23 dates × 1,096 tracts) and 21 frozen lagged weather predictors.
- All `d−1`, `d−3`, and `d−7` windows end on target day minus one. All 21
  predictors have zero missing and zero infinite values; all 25,208 audit rows
  are complete.
- Independent audit authenticated all 18 immutable inputs, the 4,544-row fixed
  weight table, six NetCDF files, request and pipeline fingerprints, both
  Parquet outputs, and matching internal/external provenance.
- This stage did not read Landsat target or QA values, load a fitted model,
  produce a prediction, or consume the one-time final evaluation.

## 2026-07-26 — Collection 1 final-test Sentinel features completed and audited

- Completed all 36 authenticated Collection 1 physical acquisitions with six
  workers. The build ended with 36 completed, zero running, zero failed, and
  zero supervisor restarts. One isolated 2025-03-15 warp failed transiently on
  attempt one and completed on automatic retry two.
- Produced 39,456 acquisition-tract rows, 25,208 feature rows, 25,208 audit
  rows, and 226,872 lineage rows. There are 24,633 rows with all five lagged
  optical features available and 575 with all five missing; no row is partially
  missing.
- The formal target-blind post-run audit reaggregated all
  `207 × 1,096 = 226,872` lineage rows, authenticated all 36 cache pairs and
  178 input files, verified invariant WorldCover eligible-pixel identities,
  and proved every source age is between one and 60 days.
- The calibration classification is `c1_calibration_consistent`. Across 23,593
  finite same-date/tract pairs shared with the superseded run, the median
  albedo shift is `+0.1000000015`; its median error to the required `+0.1`
  correction is approximately `5.96e-9`, versus `0.1000000015` for the
  repeated-offset hypothesis. Final NDVI, NDWI, and albedo distributions are
  each closer to the valid development control than to the frozen invalid
  legacy control.
- Published the fail-closed audit marker at
  `manifests/final_test_2025/sentinel_features/SENTINEL_FEATURE_AUDIT.json`.
  It records `state = passed`, `target_blind = true`, and
  `safe_for_final_predictor_assembly = true`; its file SHA-256 is
  `8a19fe7b07be6caaba53798364765d3ba0c4cca89b64f2d83becfe6331a2169e`
  and internal canonical commit is
  `412c818b12b31efd8203f4fe4a12b6404c948bf8e18b933bee3fb051d2198434`.
- No Landsat target or QA path was opened, no fitted model was loaded, and no
  prediction, score, or metric was read. The one-time final evaluation remains
  unconsumed.

## 2026-07-26 — Frozen 2025 target-blind predictor matrix completed

- Assembled the authenticated final-test predictor base, final-test Daymet
  features, and audited Collection 1 Sentinel features in the exact frozen M2
  feature order. The result contains 25,208 unique tract-date rows, 23 dates,
  1,096 tracts, and 46 model predictors: 20 static/calendar, 21 weather, and
  five lagged optical features.
- The output has no duplicate keys, contains only 2025 dates, preserves
  11-digit tract GEOIDs as keys rather than predictors, and contains no target,
  QA, label, prediction, residual, score, or metric column. All numeric values
  are finite where present.
- All 41 non-Sentinel predictors have zero missing values. Exactly 575 rows
  have all five Sentinel predictors missing and 24,633 have all five present;
  partial optical-feature missingness is impossible under the frozen contract.
- The predictor Parquet SHA-256 is
  `f02b3428a1070b1d95152bb225652bc063330b3793322aeb364e8a0dd267fa0a`,
  schema SHA-256 is
  `d3c34680721b3852c0d8fbf1679743befeb619b565d3d35b7822d0cf01aa0a16`,
  and semantic predictor SHA-256 is
  `793af3c05589532887d436bbccaa0e415bc08e5106af447004f6444f766f27d0`.
- External and internal `PREDICTOR_ASSEMBLY.json` files are byte-identical.
  Their file SHA-256 is
  `4f94bd00ba5e7a6cdbd08abdb14483ea2018a5429406db3bd2f0549470f74541`
  and canonical internal commit is
  `b8ce453ecd2c4c067251d0f84bdf647b59cef9c10cbc57820286b6cb4c838d3f`.
  A second invocation reauthenticated the existing artifact and returned the
  same commit without rebuilding it.
- An independent read-only audit rebuilt the one-to-one merge from the 20
  base, 21 Daymet, and five Sentinel source features and found every value
  exactly equal with NaN-aware comparison. It also independently authenticated
  the frozen M2 feature order and all upstream commits.
- This assembly was target-blind: no Landsat target/QA table, fitted model,
  prediction, or score was opened. The authorization file is absent and the
  one-time final evaluation remains unconsumed.

## 2026-07-26 — Freeze the independent one-time final-evaluation protocol

- Rejected the four untracked evaluator drafts from the formal workflow. They
  remain quarantined and untouched. The replacement uses the distinct
  `final_evaluation_*` module, configuration, script, and test names.
- Froze the primary comparison to authenticated B1 versus M2 on all 25,208
  inventory keys. Both blind prediction surfaces must be persisted and
  committed before the first Landsat thermal or QA value is opened. No model
  fitting, tuning, selection, threshold adjustment, or target-derived
  imputation is permitted.
- Froze an append-only state sequence:
  target-blind readiness, explicit authorization, one consumption claim,
  blind-prediction commitment, values-opened commitment, atomic output
  promotion, and completion. A crash may resume only the same claim; a second
  claim or replacement of a published output is prohibited.
- The final target transaction assesses all 23 frozen physical-overpass dates
  using the unchanged tract/date QA rules. The development promotion rule of
  at least 30 independent dates is not applied to the single 2025 test year;
  the report instead gives the actual usable and excluded date counts.
- Froze the primary metric, B1/M2 paired crossed date-by-spatial-block
  bootstrap (5,000 replicates, seed 20260722, 95% interval), 10% improvement
  gate, median per-date Spearman gate, exact top-20% hotspot rule, sensor and
  Sentinel-availability strata, missingness audit, and three diagnostic
  figures before any 2025 target value is read.
- Readiness must be created while `unlock_final_test = false`. Authorization
  binds the committed evaluator and recomputes the readiness contract. The
  only permitted post-authorization Git transition changes the research
  unlock flag from false to true and records that future event in this log;
  that irreversible unlock has not occurred.
- Target-blind dry-run validation authenticated the 45-scene/23-overpass
  Landsat metadata inventory and the exact 46-feature predictor table. It
  loaded the frozen B1/M2 artifacts and produced 25,208 finite predictions per
  model in memory only. No prediction file, target/QA value, residual, metric,
  readiness marker, authorization, claim, or completion marker was produced.

## 2026-07-27 — Harden and complete the locked final-evaluation implementation

- Added deterministic replay of both blind prediction surfaces from the
  authenticated predictor table and locked fitted B1/M2 artifacts immediately
  before the target-value boundary and again before publication. The published
  blind-prediction bytes must match the append-only prediction marker.
- Moved same-claim target-cache authentication behind the values-opened gate,
  so remote thermal/QA assets and cached target bytes share the same
  fail-closed first-access boundary.
- Strengthened the independent target audit to recompute primitive QA
  booleans, counts, fractions, availability, date gates, relative endpoints,
  exact hotspot labels, tract exclusion reasons, fixed-grid and static
  eligible-pixel identities, and scene-to-overpass-date lineage.
- Froze the final output as an exact regular-file set with predeclared column
  order, canonical 2025 civil dates, unique primary keys, 11-digit GEOIDs,
  schema/semantic commitments, exact derived key sets, and cross-table
  cardinality/arithmetic checks. Same-claim recovery reruns the deep contract
  before promotion.
- Added deterministic replay of model, per-date, paired date-block,
  5,000-draw crossed-bootstrap, protocol-gate, hotspot, sensor, and
  Sentinel-availability results from the committed evaluation rows. Bootstrap
  point estimates are independently reconciled to the primary model metrics.
- Replaced the placeholder map representation with an authenticated
  census-tract choropleth summary and six-panel PDF covering observed LST, B1,
  M2, both residual surfaces, and evaluated-date support. The figure contract
  binds the claim's tract file, tract manifest, CRS, and source-table semantic
  hash; zero-support tracts remain explicit with support count/fraction zero.
  Deep authentication regenerates all three figures and requires byte-identical
  PDF/PNG output.
- Final verification results are recorded in `docs/PROJECT_HANDOFF.md`.
  Throughout this implementation `unlock_final_test` remained false. No
  readiness, authorization, claim, prediction, values-opened, completion,
  target-cache, final target/QA, metric, or figure artifact was created, and
  the one-time evaluation remains unconsumed.

## 2026-07-27 - Clear final-evaluation preparation blockers without reading targets

- After the separate draft-handling and irreversible evaluation boundaries
  were explained, the user instructed the project to perform the described
  sequence. This grants relocation authorization for the four obsolete drafts
  and one authorization for the already frozen 2025 evaluation protocol. It
  does not grant a second claim, a changed evaluator, or post-result tuning.
- Moved the four obsolete, untracked drafts without reading or executing them
  to the recoverable external quarantine directory
  `D:\HuaweiMoveData\Users\haora\Documents\ISEF_QUARANTINED_DRAFTS_20260727`.
  Their byte counts and pre/post-move SHA-256 values are recorded in
  `docs/PROJECT_HANDOFF.md`. The repository was clean after relocation.
- The first target-blind readiness attempt failed before writing a marker
  because the Daymet feature directory copied from the other laptop retained
  a protected ACL. Enabled inherited permissions only on that exact directory.
  No data bytes changed, and all three recovered files match the frozen
  predictor-manifest byte counts and SHA-256 values.
- Repaired the ignored local virtual-environment launcher by pointing it to
  the preserved portable Python 3.14.4 base runtime. The frozen package
  versions import successfully.
- A later readiness attempt stopped before marker creation because the frozen
  predictor directory retained the same protected other-laptop ACL. Enabled
  inheritance only on that directory; the predictor Parquet and provenance
  hashes remained exactly equal to their frozen locks. A subsequent read-only
  authentication verified 25,208 predictor rows and the 23-overpass inventory
  with `target_or_qa_values_read = false`.
- A readiness generated from committed target-blind code was rejected by the
  authorization preflight before authorization or unlock. The evaluator's
  extended-runtime registry included Pillow 12.3.0, but the authorization
  module's duplicate registry omitted Pillow; all pipeline hashes and other
  runtime fields matched. Preserved the invalid readiness intact outside the
  repository at
  `D:\HuaweiMoveData\Users\haora\Documents\ISEF_INVALID_READINESS_20260727_824e8d3`
  (28,960 bytes; SHA-256
  `7af7778a2fa83aa3936856c740961dafff73ad6d305b96e1056fec5595f9188a`).
  No authorization, claim, prediction, values-opened, target cache, output, or
  completion artifact was created.
- Added Pillow to the authorization runtime registry and a regression test
  requiring the evaluator and authorization registries to remain identical.
  The canonical readiness path remains absent until the repaired code and this
  audit record are committed and pushed. The authorization/evaluator/target/
  reporting suite passed all 55 tests, the complete project suite passed all
  712 tests in 244.69 seconds, and full-repository Ruff passed.
- No readiness, authorization, claim, frozen-prediction, values-opened,
  completion, target-cache, target/QA, residual, metric, or final figure
  artifact existed after these repairs. `unlock_final_test` remained false.
- The held-out test evidence will be preserved as an append-only chain of
  readiness, authorization, claim, prediction freeze, value opening, 23
  overpass cache commits, exact output commitment, completion marker, Git
  history, and SHA-256 export manifest. This is the electronic data-book
  record; a screenshot alone is not accepted as result evidence.
- A final independent audit found one pre-readiness recovery-authentication
  gap: a self-consistent replacement evaluation surface could replay its own
  reports without being reconstructed from the committed target, blind
  prediction, predictor, and date-QA source tables.
- Closed that gap before readiness. Deep staging/recovery authentication now
  reauthenticates the inventory, predictor provenance and model feature
  contract, research unlock, target/date/contribution artifacts, and target
  configuration; rebuilds the joined evaluation surface and QA summary; and
  requires exact evaluation-row and safe-count replay.
- Synthetic tamper tests cover target values, B1/M2 predictions, Sentinel
  availability, date and tract QA, target provenance, predictor/inventory
  provenance, research unlock, QA summary, and safe-count drift. The focused
  evaluator/target/reporting suite passed 44 tests. The complete project suite
  passed all 711 tests in 214.87 seconds, full-repository Ruff passed, and an
  independent read-only re-audit found no remaining P0/P1 issue. No real 2025
  target value was opened during this hardening.

## 2026-07-27 - Authorize and commit the one-time 2025 evaluation unlock

- Generated a new target-blind readiness from repaired evaluator commit
  `407aefcb4f54ba9fc6265ea5830b1747b7977ba4`. It records
  `target_blind = true`, `values_read = false`, request SHA-256
  `6cde25bcda2f52724b67754432b92b0a3aee84e5bae4aa92a4d4595e9bd434b5`,
  internal commit
  `fda4bcab8ec0963ad4824a3e863974be5887d3143c26c502081789a9a878acdb`,
  and file SHA-256
  `a2676a10d239248906dee6a42f3ecec4224b4c31556f180b682d958e7580a314`.
- The authorization preflight returned `eligible_but_not_authorized` with
  `values_read = false`. Under the user's explicit one-time authorization,
  created the canonical authorization marker. It records
  `authorized = true`, `values_read = false`, internal commit
  `3490c71100e1ea063ec8f71f0823b50715963a7cead12f62f5bdffe86c706a9a`,
  and file SHA-256
  `fb1174c01da85f8c149c1e286cba9da42de2ea0925d732caaf75b9420b3c6241`.
- This commit changes only `configs/research.toml` from
  `unlock_final_test = false` to `true` and updates this decision log and the
  mandatory handoff. It changes no model, feature, target rule, metric,
  bootstrap, threshold, output contract, or evaluator code.
- At this transition no consumption claim, frozen prediction, values-opened
  marker, target cache, target/QA table, residual, score, metric, figure,
  staging commit, final output, or completion marker exists. The next and only
  safe action after push is:

  `.\.venv\Scripts\python scripts\execute_locked_final_evaluation.py --config configs\final_evaluation_2025.toml`

## 2026-07-27 - Complete and freeze the one-time 2025 evaluation

- Created exactly one consumption claim:
  `c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`.
  Blind B1/M2 predictions were committed before the target/QA value boundary.
  The prediction commit is
  `73c6f4ef48262930626f5efc285387bdde9b3ebfe85fc2586008efd02d314df9`;
  the values-opened commit is
  `ae17a1a5b229f2b04cbfb9bbc8ff2ea9afa8f27c136a908a681b288d9d59b54f`.
- USGS LandsatLook redirected the frozen asset requests to an interactive
  EarthExplorer login. The same frozen 45 scenes and 225 exact product
  assets were therefore read through the anonymous Microsoft Planetary
  Computer public mirror. A target-blind metadata audit matched all 45 product
  directories and all 225 asset filenames before any mirror byte was read.
  The 23 same-claim overpass caches each have a committed provenance record.
- The post-claim recovery exposed three representation/control-boundary bugs,
  not scientific-result differences: the authorized `research.toml`
  `false -> true` bit was still included in a pre-authorization predictor
  fingerprint, CSV round-tripping represented nullable booleans and blank
  reasons differently, and Parquet returned `scene_ids` lists as NumPy arrays.
  A local, ignored recovery wrapper was permitted only for the existing claim.
  It verified that the research unlock was the sole pipeline difference,
  authenticated all 198 immutable predictor inputs, bound the exact predictor
  provenance/table to the claim, and normalized only the identified
  serialization representations. It did not change a target, prediction,
  model, threshold, metric, bootstrap draw, or staged output byte.
- The canonical evaluator then completed its deep reconstruction of targets,
  predictions, evaluation rows, QA summaries, all reports, safe counts, and
  figures. It atomically promoted the exact 21-file output set and removed
  staging. The output commit is
  `ee6d7bb6df917acf0340b3e91ff3189044ae8e9bec1ae32b888dbf8429a75432`;
  the completion commit is
  `4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`.
  A separate process then ran the original unpatched execution command and
  authenticated the same completed state without reopening targets.
- The frozen held-out cohort contains 15,116 tract-date rows across 15 usable
  dates and 71 spatial blocks. M2 equal-date MAE is 2.164951 °C versus
  3.116514 °C for B1, a 30.5329% point reduction. M2 median per-date Spearman
  is 0.844693. The 5,000-draw crossed date-by-block relative-improvement 95%
  interval is -10.1336% to 58.4591%.
- The point-improvement and rank-correlation gates passed. The required
  confidence-interval lower-bound-above-zero gate failed, so the frozen
  overall protocol success flag is false. This qualified result is final:
  no retuning, retraining, threshold change, second claim, or repeat evaluation
  is permitted.

## 2026-07-28 — Freeze and externally anchor the final evidence export

- Decision: preserve the completed one-time evaluation as a no-overwrite,
  read-only directory plus deterministic ZIP, with an external ZIP checksum
  recorded in the repository.
- Package Git head:
  `e077e520629a287b9e855e00162cc9729852e45f`.
- Evidence directory: `exports/FINAL_EVALUATION_EVIDENCE`; exact file count:
  239.
- Evidence ZIP: `exports/FINAL_EVALUATION_EVIDENCE.zip`; bytes: 21,787,327;
  SHA-256:
  `61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.
- Verification: all inventory hashes, the six-state chain, exact 21 final
  outputs, 23 cache commits/93 cache files, 16 recovery files, pre/post unlock
  snapshots, ZIP byte equivalence, and an isolated clone plus
  `git fsck --full --strict` of the repository bundle passed.
- The attestation is
  `manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json`, canonical commit
  `87942c00858d82aaacf3d2c14e86c5c116d6f3b7dd0b815f770a304a6a29525a`.
- This export copies existing evidence bytes; it does not rerun, refit,
  recalculate, or alter the scientific result.

## 2026-07-28 — Publish a read-only interactive results layer

- Decision: publish a compact, authenticated presentation layer over the
  already completed one-time evaluation. The site may visualize canonical
  observed LST, frozen B1/M2 predictions, residuals, and published diagnostics;
  it may not train, retune, replace a metric, or initiate a second test read.
- The display export is deterministic and binds its source hashes to claim
  `c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`,
  completion commit
  `4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`,
  and evidence ZIP SHA-256
  `61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.
- The deployed site source is isolated in its own nested Git project so that
  deployment metadata and JavaScript dependencies do not change the canonical
  scientific repository. Deployed website commit:
  `abd30c33539e93d49f8674d3b209d07afbb3ce90`.
- The visual conclusion must always pair the 30.53% lower held-out point MAE
  with the crossed-zero 95% interval and the frozen
  `overall_protocol_success = false` interpretation.

## 2026-07-28 — Produce one qualified publication package

- Decision: create a paper, large-format poster, and oral-defense presentation
  from the already frozen evaluation, using one consistent visual and
  interpretive system.
- Every format must pair the 30.53% favorable point estimate with the
  -10.13% to 58.46% crossed-cluster interval and the failed overall protocol
  flag. No communication artifact may convert the result into a confirmed,
  causal, exposure, health, or operational-forecast claim.
- The paper builder reads only authenticated local summaries and figures.
  Editable documents and print outputs are preserved together; slide sources
  are repository-relative and each slide has a `[Sources]` notes block.
- Publication generation does not train a model, modify a frozen output, open
  a target value, or authorize a second final-test claim.
- The 17-file shareable archive is
  `exports/PUBLICATION_MATERIALS.zip` (12,293,058 bytes), SHA-256
  `2a91f09e993ebb0438bd987169862d308cb5182b5b8b56293d6b4ca41aae9493`;
  isolated extraction reproduced every packaged file hash.

## 2026-07-28 — Move the public atlas to GitHub Pages

- Decision: replace the restricted-access deployment with a public static
  GitHub Pages site at
  `https://cmschase.github.io/LA-surface-heat-atlas/`.
- The Pages source is isolated in the public repository
  `https://github.com/CmsChase/LA-surface-heat-atlas` at commit
  `405990a2cf87c539fed855e035b5d6883e74a732`.
- GitHub Actions run `30340513995` passed lint, static export, exact display
  data tests, artifact upload, and deployment. Public checks returned HTTP 200
  for the page, four authenticated JSON files, and social image; browser
  checks exercised the date and model controls without console errors.
- The four display files are byte-identical to the prior verified export. This
  hosting change neither recalculates a metric nor reruns, retunes, or changes
  the completed one-time evaluation.
- The paper, poster, oral-defense deck, packaged QR asset, and shareable ZIP
  were regenerated only to replace the obsolete site address with the public
  GitHub Pages URL. Their scientific values and qualified conclusion are
  unchanged.

## 2026-07-28 — Rebuild navigation and add tract-level inspection

- Decision: keep the existing scientific directory and every frozen path
  intact. Improve first-read clarity by shortening the root README and adding
  concise indexes for documentation, scripts, configurations, source modules,
  tests, data, manifests, and reports. No canonical artifact was moved.
- The public atlas now retains its three synchronized overview maps and adds a
  larger map with zoom, pan, reset, GEOID search, mouse/keyboard tract
  selection, a 15-date observed-versus-predicted timeline, and a complete
  per-date error and QA table.
- Display-export version 2 replaces the uninformative repeated tract type with
  the authenticated TIGER `NAME` number joined to `NAMELSAD`; it does not
  change geometry, evaluation rows, predictions, residuals, or metrics.
  `evaluation-2025.json` and `metrics.json` remain byte-identical to version 1.
- Public website commit
  `128283948c74ad262401a3ced390e452d285e0b1` deployed successfully in GitHub
  Actions run `30353854439`. This is a presentation and repository-navigation
  change only; the one-time final evaluation remains untouched.

## 2026-07-28 - Add source-backed neighborhood labels and a pixel homepage

- Decision: advance the deterministic presentation export to
  `website-display-export-v3` without changing any canonical model, predictor,
  target, threshold, evaluation row, metric, uncertainty draw, or protocol
  conclusion.
- Human-readable names come from the Los Angeles Times Data and Graphics
  Department `mapping-la-data` repository, frozen at commit
  `5acc817cd8e9ef1800dc9641493e46efe7ce35b0`. The exact
  `la-county-neighborhoods-v6.geojson` snapshot has SHA-256
  `ada200f59e0d2cd7e04a212eb5510cfe570765d68b7ff29d83b97cc5abeb6ead`
  and is available under the MIT license. The exporter accepts exactly the 114
  records identified by the source metadata as City of Los Angeles
  neighborhoods.
- Each census tract receives a primary display label from the neighborhood
  with the largest polygon-intersection area in the shared projected CRS.
  The export also preserves every nonzero cross-boundary covered-area
  proportion and the total neighborhood-source coverage, so the primary label
  does not erase boundary ambiguity. Neighborhood names remain display
  metadata and are not model inputs, target values, split groups, or new
  evaluation strata.
- The homepage receives a 40-column by 59-row field of equal display squares.
  The frozen coverage and maximum-tract-overlap rules retain exactly 869
  Los Angeles cells. Their color comes from the M2 prediction on the explicitly
  fixed display date `2025-09-03`; each cell also resolves to its assigned
  tract GEOID and source-backed neighborhood label. This raster-like field is
  an interface summary, not a new analytical surface or an additional
  performance result.
- The detailed map no longer captures the mouse wheel. Zoom remains available
  through explicit plus/minus controls, alongside drag-to-pan and reset. Its
  height is reduced and bounded by the viewport, and search accepts either a
  tract GEOID or a Mapping L.A. neighborhood name.
- The verified v3 display SHA-256 values are:
  `3712654983cee108eea2e54066b8286ff1bb3fd291e6126bace538b820ac670f`
  for `tracts.json`,
  `617eac416e348b4a0445a06c2d3627d1fd51421faf6c23f4c513835a06aa7938`
  for `evaluation-2025.json`,
  `494db653c65ba75ae2d2b312c808e80a376e75d97a177d3b8785342130f80aeb`
  for `metrics.json`, and
  `675f69e8138d7a1255973b3da70928a19ee70f4ec52d580421e5d9786af18e85`
  for `display-manifest.json`.
- Public website commit
  `206c04b797b184aa3dcb3aec60ac0ffaba8775bb` deployed successfully in
  GitHub Actions run `30358959707`. Public HTTP checks returned 200 for the
  page and four JSON files, all deployed hashes matched, and the deployed
  JavaScript contained the new interface without `Scroll to zoom`.

## 2026-07-28 — Start a target-locked cross-city continuation

- Decision: preserve the completed Los Angeles study as immutable Phase I
  evidence and start a scientifically separate continuation under isolated
  `multicity/` paths. Existing `phase2_*`, `final_test_*`, and
  `final_evaluation_*` names and workflows are not repurposed.
- The new question is zero-shot external generalization and reliability, not
  merely repeating the Los Angeles analysis with more rows. Los Angeles is the
  only labeled source; Phoenix, Houston, and Chicago are fixed external cities
  representing hot-arid, hot-humid, and continental/Great-Lakes contrasts.
- Model fitting is predeclared for Los Angeles 2020–2023; Los Angeles 2024 is
  reserved for interval calibration and the abstention threshold. The already
  known Los Angeles 2025 result is excluded from all new confirmation.
  Phoenix, Houston, and Chicago 2025 target and target-QA values remain sealed.
- The sole confirmatory comparison is fixed B1-Transfer versus M2-Transfer.
  The point-model class and parameters retain the Phase I Ridge/HGB settings.
  Uncertainty uses predeclared 90% conformalized quantile intervals and a
  Los Angeles 2024 interval-width abstention threshold.
- The Phase I `Pacific coast distance` variables are not portable. Predictor
  construction remains locked until a nationwide ocean/Great-Lakes shoreline
  source and identical target-independent distance algorithm are reviewed and
  frozen.
- Daymet cannot support an operational issue-time forecast. The continuation
  is a target-blind historical external test. A true current/next-day tool
  requires a separate archived/live HRRR protocol and may not borrow the
  confirmation result.
- `manifests/multicity/PLAN_READINESS.json` reauthenticated the Phase I model
  lock, completed claim, 23 cache commits, 21 final outputs, and evidence ZIP.
  Its internal commit is
  `2eceb4fbc220d3bcffb2e071d3393722e7db0aba8e09c64a97a3dc7a62c97079`.
  It authorizes only boundary and public-metadata staging; no external target,
  predictor build, model fit, long computation, or new evaluation was started.

## 2026-07-29 — Complete the target-blind Phoenix geography pilot

- Decision: implement one reusable ArcGIS Census place/tract adapter under
  `src/la_heat/multicity/` and authorize its draft write path only for
  `phoenix_az`. Houston, Chicago, and Los Angeles continuation staging is
  rejected before any network request.
- Census TIGERweb remains the authoritative first source. The current local
  route could not complete its TLS connection, so the adapter selected the
  two fixed Esri Demographics 2020 Census items already recorded in
  configuration. Their item metadata says the boundaries came from the 2020
  TIGER/Line geodatabases and that vertices were not altered. Raw layer
  metadata, item metadata, counts, and GeoJSON pages are preserved and hashed.
  This is explicitly a pilot mirror, not a confirmatory source freeze.
- Selection is target-independent: exact incorporated-place GEOID `0455000`,
  common equal-area CRS `EPSG:5070`, at least 50% of original tract area inside
  the city, then exclusion of `98xxxx` special-use tracts. No population field,
  Landsat thermal/QA value, predictor, prediction, model fit, or score is read
  or computed.
- The first uncommitted run exposed an invalid-geometry ordering bug. The
  source Phoenix multipolygon contains nested shells; unioning before
  `make_valid` overstated total geometry area. The implementation now repairs
  each source geometry before dissolving, and a synthetic nested-shell
  regression test enforces that order. The superseded uncommitted bytes were
  moved intact to the ignored recoverable directory
  `data/interim/multicity/phoenix_az/superseded_geography_union_before_make_valid_20260729`.
- The corrected audit contains 603 bbox candidates, 389 positive overlaps,
  376 tracts meeting the area threshold, one qualifying special-use exclusion,
  and 375 primary tracts in county FIPS `013`. Every primary geometry is valid,
  nonempty, unique by GEOID, and has an overlap fraction in `[0, 1]`.
- The primary GeoParquet SHA-256 is
  `01bb492bd10ca7373d14d72a99cb076a76efaf887bbb309022da45d3044abacb`.
  The geography manifest internal commit is
  `3891c871ab5e5710bf6abdbc8f2a22a5a62db7962ee66bf235e1caee28301fea`.
  The updated planning readiness advances the next safe stage to Phoenix
  target-blind source-footprint discovery while leaving all external targets
  and computation locks closed.

## 2026-07-29 - Complete the Phoenix source-footprint metadata pilot

- Decision: complete the authorized Phoenix source-footprint stage as a
  target-blind metadata snapshot only. The authenticated record is
  `manifests/multicity/cities/phoenix_az/source_footprints/SOURCE_FOOTPRINTS.json`;
  its internal commit is
  `a6f287daa22c41d893519dc751848c426b819dcf4ae00d33012fbf11c6073ed7`.
- Boundary-derived discovery found Landsat contributors `WRS2-036037`,
  `WRS2-037036`, and `WRS2-037037`; Sentinel tiles `12SUB`, `12SUC`,
  `12SVB`, and `12SVC`; 1,461 positive-intersection Daymet candidate cells
  within halo window `y=5814..5888, x=3453..3500`; six Daymet granules; and
  terrain tiles `N33W112` and `N33W113`, verified by `HEAD` only.
- The STAC queries returned 67 Landsat and 494 Sentinel metadata items. Item
  assets and item links were excluded. No STAC asset object or asset href was
  returned or read, no asset signing or raster GET/payload request occurred,
  and zero raster bytes, Landsat thermal/target-QA values, predictors,
  predictions, scores, or models were read or constructed. Two terrain
  objects were checked by metadata-only `HEAD` requests.
- The final verifier strictly separates booleans from integer counters,
  reconstructs the STAC POST-pagination chain and item tables, parses the
  frozen CMR response, rebuilds the Daymet cells, and binds the exact terrain
  HEAD records to the derived tiles. The planning `--check-only` path also
  compares the complete recomputed readiness payload with the saved manifest.
- Verification passed 12 source-footprint tests, 20 combined multicity tests,
  all 767 project tests in 235.5 seconds, and full-repository Ruff.
- This result is a pilot metadata snapshot, not a source freeze or protocol
  lock. The planning audit remains internal and now authenticates commit
  `fe62d0ab6505d60879b12ba00b29183e7bff54944496faff2dd7adddd3366559`.
- The only safe next task is to review a nationwide ocean/Great-Lakes
  water-distance source and a single target-independent distance algorithm.
  That review does not authorize predictor construction, model fitting, or
  external target access.

## 2026-07-29 - Complete the portable water-distance source review

- Decision: complete a target-blind source and algorithm review without
  freezing a source, implementing a distance surface, or authorizing predictor
  construction. The authenticated record is
  `manifests/multicity/reviews/portable_water_distance/WATER_DISTANCE_REVIEW.json`;
  its internal commit is
  `1c3b1738b7625a74f446b2c61e8efc84255dbe99fe745a2fa101d4decfcda6a5`.
- The existing Census TIGER/Line 2019 national Coastline was reauthenticated as
  the strongest U.S.-only reproducibility benchmark: 16,631,608 bytes, SHA-256
  `10c7e252a96a46552bf6045cc46f0605f645feeab70be545fab1bac869723494`,
  seven expected ZIP members, CRS `EPSG:4269`, and 4,248 valid nonempty
  `L4150` lines including 377 `Great Lakes` records.
- Census is not frozen as the final portable source. Its national scope omits
  the Mexican Gulf of California, so a Census-only value would be distance to
  a U.S. Census qualifying shoreline rather than the globally nearest ocean.
  The review keeps those definitions and proposed feature names distinct.
- Natural Earth 10m was rejected as too generalized for neighborhood-scale
  distance; NOAA CUSP was rejected as a canonical source because versions are
  not preserved and coverage is heterogeneous; USGS 3DHP was deferred during
  its evolving NHD transition. GSHHG 2.3.7 full resolution advances to one
  source-only geometry pilot because it is global and fixed, but its five
  Great Lakes need an explicit identity audit and its lake geometry/license
  require separate review.
- The reviewed common algorithm uses frozen eligible-land cell centers,
  verified metre-based projected CRSs, float64 exact nearest-line distances,
  one fixed expanding-radius ladder, and fixed mean/linear-p10 aggregation.
  It remains a specification, not an implementation or lock.
- The audit program made zero source-data network or download requests,
  computed zero distance values, and opened no target, target-QA, predictor,
  prediction, score, or model value. The scientific comparison separately
  consulted the cited official online documentation, which the access record
  explicitly acknowledges.
  The updated planning audit has internal commit
  `5af81da4de409b60e313784ce7453df8bec997849826ad789b2418b937f6c244`
  and advances only to `target_blind_gshhg_geometry_comparison`.
- Verification passed all 7 review tests, 27 combined continuation tests, all
  774 project tests in 279.9 seconds, both review and planning `--check-only`
  authentication, and full-repository Ruff.

## 2026-07-29 - Complete the target-blind GSHHG geometry pilot

- Decision: preserve the preregistered V1 structural failure, then complete
  only the separately preregistered V2 public-geometry comparison. V1 was
  committed and pushed as `08d69c3b4ba4c2d9e4ffb45e11727840ae3341b3`
  before source geometry was read. After the two source-topology failures
  were observed—but before any diagnostic distance was computed—the
  source-structure-only V2 amendment was committed and pushed under subject
  `Amend GSHHG pilot for source topology`
  (`544f1870d669e67e655891623e973abc4980999c`). No distance point, numerical
  threshold, source comparison, or access lock changed.
- V1 failed because full-resolution L1 contained exactly one invalid polygon
  and five fixed named-lake seeds resolved to three, not five, positive-area
  L2 connected-water polygons. The failure remains authenticated separately;
  it is not relabeled as a V1 pass.
- V2 authenticates the 149,157,845-byte GSHHG 2.3.7 archive under SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`
  and published MD5 `cb82015f8533f9611b4adba2c404ba44`, all 402 ZIP
  members and CRCs, 179,837 L1 polygons, 6,660 L2 polygons, one exact
  deterministic L1 repair, and the frozen five-seed-to-three-polygon mapping.
  The four-city contract uses L1 exteriors plus those three L2 exteriors and
  excludes negative-area river-lakes and L3 lake-island shores.
- At the fixed target-blind Phoenix point, GSHHG measured 262.208 km and the
  U.S.-only Census `L4150` benchmark measured 482.409 km, so the two contracts
  differ by 220.201 km. The nearest GSHHG evidence is source/component `2` at
  `(-113.770306, 31.570972)`. This demonstrates a semantic difference between
  contracts; it does not establish positional truth or source accuracy.
- Global antimeridian, STRtree/brute-force, radius, source-order, line-chunk,
  true vector query-chunk, worker-count, and frozen point-to-vertex geodesic
  audits all passed. The canonical pilot manifest internal commit is
  `e14cbd4763489fbacdec3ac45348226e2ae677073aa592aabf9bc0e3d8256735`.
- The pilot read no target grid, thermal/QA value, feature surface, predictor,
  model, prediction, score, or final-evaluation artifact. It creates neither a
  source lock nor an algorithm lock. The only safe next stage is a separate
  portable water-distance source-and-algorithm freeze decision.
- The first complete-project regression run exposed one stale test-only
  Sentinel pipeline fingerprint. Declaring the already used `pyproj` package
  as a direct project dependency changed the current `pyproject.toml`-bound
  fingerprint from
  `dd0723206eac0acb73fadc718b2f6cf5bc8898489f75eb20e05e46ec72a564c2`
  to
  `5fecf25f6a8217b03f89021ac837132243380ddb81394c42f31364e1d3f4420a`.
  The regression expectation was updated to the recomputed value; no Sentinel
  cache, feature, model, target, or frozen result was rebuilt or changed.
- Final verification passed 30 focused GSHHG/water-review/planning tests, all
  797 project tests in about 271 seconds with five existing deprecation
  warnings, full-repository Ruff, exact GSHHG generation plus `--check-only`,
  and planning generation plus `--check-only`. Planning internal commit
  `8444a16998699e39a51b2e0bf8daa62a9bd4365dfdb926cadeb315c6952de472`
  advances only to the separate freeze decision while leaving every source,
  predictor, model, target, and evaluation permission false.

## 2026-07-29 - Defer portable water-distance freeze for an L3 audit

- Decision: retain the exact GSHHG 2.3.7 archive as the preferred candidate,
  but reject immediate source-and-algorithm freeze of the current
  L1/L2-exterior contract. The authenticated append-only decision state is
  `decision_complete_freeze_deferred`, with outcome
  `deferred_pending_gshhg_l3_hierarchy_contract`.
- The blocking scientific gap is narrow and explicit. GSHHG defines L3 as
  islands in lakes, while the completed V2 pilot excluded L3 shores. The four
  fixed city points test source semantics and numerical stability but do not
  prove that no eligible-land cell lies on a Great Lakes island. A natural
  language feature called ocean-or-Great-Lakes shoreline distance cannot
  silently omit those shores.
- The decision binds the completed source review, V1 failure, V2 pilot, every
  V2 numerical gate, the exact 149,157,845-byte archive under SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`,
  and selected L2 source IDs `180507`, `180515`, and `180517`.
- The source candidate is not rejected, but it is limited to a possible
  historical cartographic covariate for the four predeclared study cities,
  conditional on their later authenticated and frozen eligible-land supports.
  Nationwide/all-city, current-day, real-time, uniform 30 m,
  physical-ground-truth, or future-city claims are prohibited without new
  audits.
- The archive notice, historical README, and GitHub repository UI label use
  non-identical LGPL-version wording. The record therefore uses the LGPL 3.0
  common-denominator compliance baseline pending legal review and authorizes
  no archive or modified-geometry redistribution under current project
  policy. Future redistribution must preserve the copyright/permission notice
  and add the official GPLv3 text missing from the archive.
- The only next safe stage is
  `preregister_target_blind_gshhg_l3_hierarchy_audit`. That preregistration
  must be committed before opening L3 geometry. The candidate rule is to add
  every direct L3 descendant exterior of the three selected L2 polygons,
  exclude L4, and replay the source-only numerical gates. A passing audit will
  still require a second freeze decision.
- The decision program made zero network requests, authenticated the local ZIP
  bytes without opening an archive member, and opened no L3 geometry,
  eligible-land grid, target, target-QA, feature surface, predictor, model,
  prediction, or final-evaluation output. Every portable-water-distance and
  multicity-continuation source, algorithm, feature-name, predictor, model,
  protocol, and external-target permission remains closed.
- The canonical manifest internal commit is
  `00e8ed677035f8f8315b7171fa8c969ca6c50c14b0114eff9e5024bb1c7b99b5`.
  Planning schema v5 advances only to the preregistration gate under internal
  commit
  `ebe371cdb8e9dc39c086fc394ce33d4d113abc44d83e2289bef6c74988021001`.
- Verification passed all 16 deferred-decision tests, all 50 focused
  GSHHG/water-review/decision/planning tests, all 813 project tests in 175.4
  seconds with the same five existing deprecation warnings, and
  full-repository Ruff.

## 2026-07-30 - Preregister the target-blind GSHHG L3 hierarchy audit

- Decision: complete only the tracked-input preregistration and stop before
  any L3-member read. The append-only manifest is
  `manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json`;
  its file SHA-256 is
  `ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f`
  and internal commit is
  `7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7`.
- The exact contract includes every full-resolution L3 exterior whose integer
  `parent_id` is `180507`, `180515`, or `180517`. It cannot use city, bbox,
  name, area, distance, eligible support, target, model, prediction, or result
  values for hierarchy inclusion. L4 remains excluded and unopened.
- The source audit accepts the official `-E`/`-W` source-ID suffixes created by
  antimeridian splitting. It does not assume that an observed L3 interior ring
  has L4 semantics; every such ring is simply counted and excluded by the
  exterior-only contract.
- Structure must pass before distance. The audit must record member hashes and
  CRCs, schema/dtypes/CRS, complete and selected semantic hashes,
  parent/containment/overlap checks, and selected linework identity. No repair
  or decomposition is allowed.
- One deterministic real-component probe is selected for every parent that has
  direct children by source area descending and canonical ID ascending. The
  audit also replays the four existing city points and every V2 radius,
  STRtree/brute-force, source-order, line/query-chunk, worker, and geodesic
  gate. A numerical failure cannot be converted into a pass by changing a
  probe or tolerance.
- Any structure failure is append-only and must retain
  `distance_values_computed=false`. A source-structure amendment may proceed
  only after a separate commit and push and cannot change the archive,
  selected parents, hierarchy rule, L4/exterior semantics, existing points,
  numerical thresholds, or access locks.
- The preregistration generator reads only tracked experiment configuration,
  the prior planning manifest, and the deferred-decision manifest. It does not
  call the geometry-pilot, deferred-decision, or planning auditors. A minimal
  project copy with no `data/`, `exports/`, or `reports/` directory generated
  the same contract, and forbidden ZIP/vector/Parquet/raster/model/network
  readers were trapped in tests.
- `PLAN_READINESS.json` remains intentionally unchanged: preregistration is
  true and L3 geometry read is false. The next safe task is a tracked-only,
  clean-and-pushed Git transition that removes the planning auditor's deep
  geometry-pilot call before narrowly authorizing the L3 geometry audit.
- This stage made zero network requests, opened no GSHHG archive/member or
  geometry, computed no distance, and opened no support, target, predictor,
  model, prediction, or result. It creates no source, algorithm, feature-name,
  predictor, or protocol lock. A passing future L3 audit still requires a
  second independent freeze decision.
- Verification passed all 12 new preregistration tests, all 58 focused
  L3-preregistration/GSHHG/water-review/deferred-decision tests, all 825
  project tests in about 300.3 seconds with the same five existing
  deprecation warnings, exact generation plus a separate `--check-only`, and
  full-repository Ruff.

## 2026-07-30 - Authorize only the preregistered GSHHG L3 geometry audit

- Decision: replace planning schema v5's deep data reauthentication with
  schema v6's tracked-manifest-and-local-Git transition. The implementation
  and eight regression tests were committed and pushed first in
  `5515b1042ddda7d77cb71d5a6ee80370afc87d36`. Only after the repository was
  again clean `main` with `HEAD == origin/main` was the v6 planning manifest
  generated.
- v6 authenticates the exact historical v5 `PLAN_READINESS.json` blob from
  Git commit `997e86d9ab06d22c04faad6fe714eacde53c9921`, the exact append-only L3
  preregistration, exact pilot and deferred-decision manifests, current
  configuration/code hashes, and regular tracked worktree blobs. Two Git
  preflights require the same HEAD and compare every required worktree file
  to its HEAD blob, including files hidden by `skip-worktree` or
  `assume-unchanged`.
- The new state is
  `gshhg_l3_hierarchy_audit_preregistered_geometry_authorized_unopened`; the
  next safe stage is `target_blind_gshhg_l3_hierarchy_geometry_audit`.
  Preregistration authorization changes true to false, and the only false to
  true change is
  `target_blind_gshhg_l3_hierarchy_geometry_read=true`.
- The grant is not a generic geometry or feature-construction permission. It
  binds the exact local 149,157,845-byte GSHHG archive under SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`
  and exactly 12 full-resolution L1/L2/L3 shapefile members. Network/download,
  all other members, L4, Census/other geometry, eligible support, distance
  surfaces, tract aggregation, predictors, models, predictions, targets,
  results, geometry export, and redistribution remain prohibited.
- The next executor may write only one exact append-only success/failure
  manifest and the ignored preregistered diagnostic table. Even a passing
  audit creates no source, algorithm, feature-name, predictor, or protocol
  lock; it permits only a second independent freeze decision.
- The transition itself requested Git's untracked path names only to prove a
  clean worktree; it opened no untracked file content or ignored path, made
  zero network requests, and opened no GSHHG archive/member, geometry,
  eligible support, predictor, model, target, or result bytes.
- Canonical `PLAN_READINESS.json` is 13,327 bytes, file SHA-256
  `9a8f8b93ccfa89bf43354cb09d6d92fee1b436eb5edbd227b75d794dd49cac6c`,
  and internal commit
  `1789d828f212e0cd65f87c9427eb4a7fbd1697cc7170ebb98a80806659afbc86`.
- Verification before the implementation commit passed 44 focused tests, all
  833 project tests with the same five existing deprecation warnings, and
  full-repository Ruff. Final v6 `--check-only` is required after this
  manifest and documentation are committed and pushed and before any
  archive/member read.

## 2026-07-30 - Complete the amended GSHHG L3 hierarchy audit

- Decision: preserve the V1 failure, accept one exact source-identity
  transcription amendment, and publish the passing V2 audit without creating
  a source, algorithm, feature-name, predictor, model, protocol, or target
  lock.
- The V1 executor was committed in
  `ab51a9506d77b7ac0efcdfb97e494c665cd80e5b`. It failed in the structure
  phase before any probe or distance because the preregistered normalized-WKB
  hash for source ID `180515` ended in `c`, while the authenticated source and
  prior pilot evidence ended in `a`. The append-only failure was published in
  `fbf20ed7a601af8e9f77ad768f1267b8a6503a0d`.
- The V2 amendment was independently committed and pushed in
  `e07ef369ea3310ec67956b06436f793f01c89942`. Its only semantic change is the
  final `c -> a` character for source ID `180515`; all archive, hierarchy,
  probe, numerical, and access rules remain unchanged.
- The V2 executor, verifier, and tests were committed in
  `7b7c804293057458c82353f705c1d9cf28375301`. The canonical terminal was
  blindly published before result inspection in
  `0afb1f9868378f12e8fe8b66f5772fde6685ed1f` and then authenticated by
  `--check-only`.
- The audit authenticated the 149,157,845-byte GSHHG 2.3.7 archive, opened
  only the 12 allowlisted L1/L2/L3 members, and selected 139 direct L3
  descendants across parents `180507`, `180515`, and `180517` as
  118 / 8 / 13. All containment, parent-boundary, sibling-overlap, longitude,
  replay, STRtree/brute-force, radius, source-order, chunk, worker, and
  projected/geodesic gates passed.
- L3 changed none of the four fixed Los Angeles, Phoenix, Houston, and Chicago
  distances. It shortened the three deterministic real-island probe distances
  by 30,948.681 m, 1,143.027 m, and 4,895.846 m. This supports including the
  directly parented L3 exteriors in the candidate algorithm without claiming
  predictive performance or modern positional truth.
- The success manifest file SHA-256 is
  `9b206f449d71f23ff0f13d0adca436a2d433140560fef92646d48a7e5c522070`
  and its internal commit is
  `9b7f6c814bda4e97120a6768b88feae37ee73044883b2ec8cad10db8d4af0f0b`.
  The seven-row diagnostic table SHA-256 is
  `ab3be04995c70359ed7b65a59d63aec84202f7319f5484dc038e172e76949a8c`.
- `source_frozen`, `algorithm_frozen`, and
  `predictor_build_authorized` remain false. The exact next safe stage is
  `separate_portable_water_distance_source_and_algorithm_freeze_decision`.
- Verification passed 316 focused tests, two complete repository test runs,
  full-repository Ruff, and terminal authentication from clean synchronized
  `main`. Metrics were inspected only after the committed terminal
  authenticated.

## 2026-08-01 - Implement the separate water-distance freeze transition

- Decision: use three append-safe publications before any predictor-contract
  work: first code/config/tests/docs, then canonical planning v7, then the V2
  decision terminal. Every publication requires clean synchronized `main` and
  a post-publication `--check-only` authentication.
- Planning v7 changes exactly two authorization leaves:
  `target_blind_gshhg_l3_hierarchy_geometry_read` changes true to false and
  `portable_predictor_source_freeze` changes false to true. All canonical
  locks and `predictor_construction` remain false.
- V2 extracts all seven source-only diagnostics directly from the exact
  tracked `GSHHG_L3_HIERARCHY_AUDIT.json`. It does not request or open the
  ignored CSV and makes no network request or ZIP/member/geometry/
  eligible-support/predictor/model/target/result read.
- A passing V2 terminal creates only the exact GSHHG 2.3.7 source lock and
  audited L1/L2/L3 point-distance-algorithm lock. Tract aggregation, feature
  names, predictor construction, model fitting, protocol promotion, and target
  access remain closed.
- V2 cannot directly authorize predictor-contract work. Its next gate is a
  separate tracked-only planning v8 that authenticates and consumes the exact
  terminal, sets the canonical plan's source and algorithm locks true, closes
  the consumed decision permission, and authorizes only the separate
  predictor-source/calibration-contract freeze.
- The historical deferred `WATER_DISTANCE_FREEZE_DECISION.json` is immutable;
  V2 may write only append-only `WATER_DISTANCE_FREEZE_DECISION_V2.json`. At
  this implementation checkpoint canonical planning is still v6 and the V2
  terminal does not yet exist; later entries must record actual publication
  hashes and authenticated states without rewriting this decision.

## 2026-08-01 - Publish canonical planning v7

- The reviewed transition implementation was committed and pushed as
  `eefb531e99d95e8dd7069e821ff941abd68de622`, subject
  `Add water distance freeze transition`.
- The sole canonical v6-to-v7 transition is published with subject
  `Authorize water distance freeze decision`. The rendered plan is 20,809
  bytes, file SHA-256
  `88c153b7c1da9f2f159ac550fd3156a4ffe3fd1f56c269c057288d938a2047f3`,
  and internal commit
  `4f6ed97b64d3a1601da6af83779ec96bef87c77de72d5294475ac029f666110f`.
- Recursive authorization comparison found exactly two changes:
  `target_blind_gshhg_l3_hierarchy_geometry_read` true to false and
  `portable_predictor_source_freeze` false to true. Every lock and
  `predictor_construction` remain false.
- The V2 decision terminal does not exist at this checkpoint. It may be
  generated only after the v7 publication is committed, pushed, and passes
  `--check-only` authentication from clean synchronized `main`.

## 2026-08-01 - Freeze the portable water source and point-distance algorithm

- Canonical planning v7 was published in commit
  `252c01d015110336c65bb602d4c5b608708fb092` and passed its publication-aware
  `--check-only` authentication from clean synchronized `main`.
- The V2 decision completed with state
  `decision_complete_source_and_algorithm_frozen_predictor_closed` and outcome
  `freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm`.
- The append-only terminal is 18,541 bytes, file SHA-256
  `a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b`,
  and internal commit
  `2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51`.
  It is published in commit `91a31fd9e1793bbfa9c9f751459fc73d0e0bbb4c`,
  subject `Record water distance freeze v2 terminal`.
- The decision program read seven source-only rows from the exact tracked L3
  success manifest. It made zero network requests and opened no ignored CSV,
  archive/member, geometry, eligible support, target/QA, model, prediction, or
  final-result data.
- The terminal freezes the exact GSHHG 2.3.7 archive and the audited repaired
  L1 plus selected L2 and direct-L3-descendant point-distance algorithm. It
  leaves tract aggregation, feature names, predictor construction, model
  fitting, protocol promotion, and target access closed.
- The mandatory next gate is tracked-only planning v8. V8 must authenticate
  and consume this exact terminal, set the canonical source and algorithm
  locks true, close the consumed decision permission, and authorize only the
  separate predictor-source/calibration-contract freeze.

## 2026-08-01 - Stage planning v8 and bind its only authorized audit

- V8 is a tracked-only transition. Before writing canonical planning it must
  authenticate the exact v7 plan, the append-only V2 terminal, their bounded
  Git histories, unchanged frozen V2 runtimes, clean `main`, and
  `HEAD == origin/main` without opening source data, geometry, eligible
  support, predictors, models, targets, or results.
- The only canonical lock changes are
  `portable_water_distance_source_locked` false to true and
  `portable_water_distance_algorithm_locked` false to true. Water-distance
  feature names, predictor construction, protocol, model, target, evaluation,
  and operational permissions remain false.
- The consumed `portable_predictor_source_freeze` permission changes true to
  false. The only new permission is
  `portable_predictor_source_and_calibration_contract_freeze=true`.
- That permission is not open-ended. The exact future contract-audit config,
  executor, script, tracked input set, and unique append-only output path are
  committed with the v8 transition and fingerprinted by the v8 plan. New
  networking or downloads require an exact preregistration; predictor values,
  model fitting, and target access remain prohibited.
- The V1 config is 5,794 bytes with SHA-256
  `536364a9a44b0fbf04a7880f2053cb2b5ae6d9badbe12861236773d239362c62`.
  It preregisters a deferred outcome only if all four exact gaps are observed:
  absent Houston and Chicago source-footprint manifests, absent Phoenix NLCD
  source family, and unfrozen Phoenix terrain content hash/schema. A deferred
  terminal cannot freeze feature names or authorize predictor construction.
- Publication order is implementation first, canonical v8 second as its
  direct child, and the separately authenticated contract-decision terminal
  only after v8 passes `--check-only`. Staged files alone grant no authority.
