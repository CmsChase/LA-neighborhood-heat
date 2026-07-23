# Data flow and leakage barriers

```mermaid
flowchart LR
    subgraph public["Public source data"]
        landsat["Landsat 8/9 L2SP\nthermal + QA"]
        daymet["Daymet weather\nwindows ending d-1"]
        static["NLCD + SRTM + coastline\nstatic land/geography"]
        sentinel["Sentinel-2 non-thermal\nd-60 through d-1"]
        census["2020 Census tracts +\nCity of Los Angeles boundary"]
    end

    census --> support["Frozen 1,096-tract support\nfixed 30 m eligible-land denominator"]
    support --> target
    support --> predictors

    landsat --> target["Target-only path\nQA-filtered daytime tract LST"]
    daymet --> predictors["Target-blind predictor assembly\n46 legal features"]
    static --> predictors
    sentinel --> predictors

    target --> gate["Local tract/date QA\n65 usable development dates"]
    predictors --> registry["Feature registry + lineage audit"]
    gate --> join["One legal join after\npredictor table is committed"]
    registry --> join

    join --> splits["Frozen grouped validation\n5 years, 71 blocks, 355 joint folds"]
    splits --> fold["Within each fold only\nimputation, scaling, tuning, refit"]
    fold --> oof["One OOF prediction per\ntract-date and split family"]
    oof --> diagnostics["Date-macro metrics, crossed\ndate×block uncertainty, hotspots,\nQA/sensor/ablation/spatial diagnostics"]
    diagnostics --> lock["Model and threshold lock"]
    lock -. "explicit approval only" .-> final["One-time 2025 final evaluation"]

    forbidden["Forbidden predictors\nthermal/LST, target QA, IDs, raw coordinates,\nsame-scene optical, target-day/future observations"]
    forbidden -. "fail-closed tests" .-> registry
    final_lock["2025 inaccessible during\nexploration and model selection"]
    final_lock -. "hard guard" .-> splits
```

The Landsat target path and predictor path remain separate until the predictor
table, registry, temporal cutoffs, and provenance are frozen. Geometry is used
to define tracts, fixed spatial blocks, and diagnostic maps; it is not a primary
model predictor. The primary analysis is a historical one-day-ahead hindcast,
not an operational forecast, because lagged predictors are historical observed
data. LST is a clear-sky surface-heat hazard proxy, not air temperature or a
human health outcome.

