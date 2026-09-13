# M3 source-level 2 × 2 diagnostic

This is a post-hoc mechanism experiment, not a new confirmation test. It uses
only the existing LA, Phoenix, Houston, and Chicago source tables. Seattle,
Denver, Atlanta, and Miami are not opened by this runner.

The fixed comparison changes two factors in M3's city-date level component:

| Variant | Elevation | Training feature aggregation |
|---|---|---|
| A | included | QA/target-selected rows, reproducing the original implementation |
| B | included | complete source predictor universe |
| C | removed | QA/target-selected rows |
| D | removed | complete source predictor universe |

QA 4K, Ridge alpha 10, the 31-leaf anomaly model, source cities, scoring rows,
and whole-city folds stay fixed. B1 is refit on the same training rows in every
fold. Lower equal-city/equal-date MAE is better.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe experiments\m3_2x2\run.py
```

Generated checkpoints and results are written to the ignored
`exports/M3_SOURCE_LEVEL_2X2/` directory. A completed city fold is reused on
restart when its run signature matches the current config and runner.

The predeclared development gate requires at least 10% aggregate MAE improvement
over B1, no held-source city degradation, and no aggregate anomaly-MAE
degradation. Passing that gate only permits evaluation on already opened cities
as development stress tests; it is not independent confirmation.
