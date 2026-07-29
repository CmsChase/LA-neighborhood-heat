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

The updated planning record keeps `protocol_locked = false` and advances only
to review of a nationwide ocean/Great-Lakes water-distance source and
target-independent algorithm. It does not authorize predictor construction.

Generated manifests are marked `-text` in `.gitattributes` because exact bytes
matter. Do not normalize line endings to silence a cosmetic diff warning. Use
[`scripts/verify_final_evaluation_evidence.py`](../scripts/verify_final_evaluation_evidence.py)
for read-only evidence verification and consult the
[handoff](../docs/PROJECT_HANDOFF.md) for canonical hashes.
