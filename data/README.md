# Local data layout

Bulk data are intentionally ignored by Git. The directory structure separates
source bytes from reproducible transformations:

| Directory | Meaning | Editing rule |
|---|---|---|
| `raw/` | Original external downloads | Preserve immutable source bytes |
| `interim/` | Inventories, caches, aligned rasters, and staged features | Regenerate through documented code; never hand-edit |
| `processed/` | Model-ready tables, fitted artifacts, and final outputs | Treat authenticated outputs as frozen |

The tracked `.gitkeep` files preserve this layout in a clone. A clone alone
does not contain the large inputs required for a full rebuild.

## Frozen 2025 paths

- `interim/final_test_2025/evaluation/target_cache/`: 23 committed target-cache
  records from the single final evaluation.
- `processed/final_test_2025/predictors/final_predictors.parquet`: frozen
  target-blind predictor matrix.
- `processed/final_test_2025/final_evaluation/`: exact committed 21-file final
  output set.

Do not move, delete, overwrite, or recompute these paths. Their hashes and
lineage are recorded in [`../manifests/final_test_2025`](../manifests/final_test_2025)
and the [mandatory handoff](../docs/PROJECT_HANDOFF.md).

Invalid historical Sentinel artifacts under `interim/superseded/` are retained
as audit evidence and must never be copied into canonical output paths.
