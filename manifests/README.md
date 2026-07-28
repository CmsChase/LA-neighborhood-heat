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

Generated manifests are marked `-text` in `.gitattributes` because exact bytes
matter. Do not normalize line endings to silence a cosmetic diff warning. Use
[`scripts/verify_final_evaluation_evidence.py`](../scripts/verify_final_evaluation_evidence.py)
for read-only evidence verification and consult the
[handoff](../docs/PROJECT_HANDOFF.md) for canonical hashes.
