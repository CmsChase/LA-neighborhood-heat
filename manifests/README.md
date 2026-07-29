# Manifest and audit records

This directory is the machine-readable provenance layer. Many files are
byte-authenticated, and paths are part of the scientific contract. Do not
hand-edit, reformat, rename, or move them.

## Development records

- `target_inventory/`: frozen Landsat scene and physical-overpass selection.
- `sentinel_inventory/`: selected acquisitions, tiles, and legal time windows.
- `daymet_grid/`: official granule discovery and subset downloads.
- `phase2_registry/` and `phase2_readiness/`: predictor registry and
  target-blind assembly gate.
- `validation_splits/`: grouped fold definitions and promotion records.
- `model_selection/` and `model_lock/`: prescore candidate freeze and final
  B1/M2 lock.

## One-time 2025 records

`final_test_2025/` preserves the final-test source inventories, feature
provenance, authorization, and append-only evaluation transaction. Its six
canonical state records are:

1. `evaluation/EVALUATION_READINESS.json`
2. `AUTHORIZATION.json`
3. `evaluation/CONSUMPTION_CLAIM.json`
4. `evaluation/PREDICTIONS_FROZEN.json`
5. `evaluation/VALUES_OPENED.json`
6. `evaluation/EVALUATION_COMPLETE.json`

`evaluation/EVIDENCE_EXPORT.json` authenticates the separate read-only evidence
package. The single claim is complete; these records do not authorize another
evaluation.

## Cross-city continuation planning

`multicity/PLAN_READINESS.json` authenticates the draft experiment and the
unchanged Phase I anchor. Its `planning_ready` state permits only boundary and
public-metadata staging. It explicitly records that external targets,
predictor construction, model fitting, and one-time evaluation remain locked.

`multicity/cities/phoenix_az/geography/GEOGRAPHY.json` authenticates the first
metadata-only pilot: exact public source responses, one city boundary, 603
bbox tract candidates, and the 375-tract primary universe. Its state is
`pilot_complete_source_not_protocol_locked`; it is evidence of adapter
behavior, not a source freeze or target-access authorization.

`multicity/cities/phoenix_az/source_footprints/SOURCE_FOOTPRINTS.json`
authenticates the next metadata-only pilot. It records Landsat WRS contributors
`WRS2-036037`, `WRS2-037036`, and `WRS2-037037`; Sentinel MGRS tiles
`12SUB`, `12SUC`, `12SVB`, and `12SVC`; 1,461 positive-intersection Daymet
candidate cells inside halo window `y=5814..5888, x=3453..3500`; six Daymet
granules; and terrain tiles `N33W112` and `N33W113` verified by `HEAD` only.
The STAC searches returned 67 Landsat and 494 Sentinel metadata items with item
assets and item links excluded. The record contains zero STAC asset objects or
asset hrefs, signing calls, raster GET/payload requests, raster bytes,
target/QA reads, predictors, predictions, or models. Its state is
`complete_metadata_only_source_not_protocol_locked` and its source lock status
is `pilot_snapshot_not_protocol_lock`.

`multicity/reviews/portable_water_distance/WATER_DISTANCE_REVIEW.json`
authenticates the completed target-blind water-distance review. It verifies the
existing 16,631,608-byte Census 2019 coastline ZIP, all 4,248 `L4150` lines,
the candidate-source assessment, and the reviewed common algorithm. Its state
is `review_complete_source_not_frozen`: no source or algorithm lock was
created, no distance was computed, the audit program made no source-data
network or download request, and no target, QA, predictor, prediction, or
model was accessed. The candidate review separately records that official
online documentation was consulted.

`multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT_V1_FAILURE.json`
authenticates the preregistered V1 failure before distance calculation.
`GSHHG_GEOMETRY_PILOT.json` authenticates the amended V2 comparison: exact
GSHHG archive/member/license hashes, 179,837 L1 and 6,660 L2 polygons, the one
deterministic L1 repair, five named lake seeds selecting three L2
connected-water polygons, the exclusion of L3 lake-island shores, eight fixed
target-blind diagnostic distances, and every numerical invariance gate. Its
state is `geometry_pilot_complete_source_not_frozen`; source, algorithm,
predictor, model, protocol, and target permissions remain closed.

`multicity/reviews/portable_water_distance/WATER_DISTANCE_FREEZE_DECISION.json`
authenticates the completed decision to defer source-and-algorithm freeze. It
binds the prerequisite manifests, exact GSHHG archive bytes, unresolved L3
hierarchy gap, license record, all-closed locks, and no-target access ledger.

The updated planning record advances only to
`preregister_target_blind_gshhg_l3_hierarchy_audit`. It authorizes writing and
committing that preregistration, but not opening L3 geometry, constructing a
predictor, fitting a model, or reading a target.

Generated manifests are marked `-text` in `.gitattributes` because exact bytes
matter. Do not normalize line endings to silence a cosmetic diff warning. Use
[`scripts/verify_final_evaluation_evidence.py`](../scripts/verify_final_evaluation_evidence.py)
for read-only evidence verification and consult the
[handoff](../docs/PROJECT_HANDOFF.md) for canonical hashes.
