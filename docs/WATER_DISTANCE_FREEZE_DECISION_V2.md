# Portable water-distance freeze decision V2

## Outcome

The target-blind decision passed. It freezes the exact GSHHG 2.3.7 source and
the audited L1/L2/L3 shoreline point-distance algorithm. It does not authorize
construction of a predictor, model fitting, protocol promotion, or access to
any external-city target or target-QA value.

The machine-readable authority is
`manifests/multicity/reviews/portable_water_distance/WATER_DISTANCE_FREEZE_DECISION_V2.json`:

- state: `decision_complete_source_and_algorithm_frozen_predictor_closed`;
- outcome:
  `freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm`;
- bytes: 18,541;
- file SHA-256:
  `a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b`;
- internal commit:
  `2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51`;
- publication subject: `Record water distance freeze v2 terminal`.

## What is frozen

The source lock binds the 149,157,845-byte GSHHG 2.3.7 archive under SHA-256
`8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`,
including the authenticated member inventory and semantic identities of the
full-resolution L1, L2, and L3 layers.

The algorithm lock binds nearest qualifying ocean-or-Great-Lakes shoreline
point distance using repaired L1 ocean exteriors, selected L2 exteriors, and
every directly parented L3 island exterior. It retains the audited dateline,
projection, prefilter, radius, chunking, worker-count, numerical-invariance,
and geodesic-reference rules. Production must use the authenticated kernels
or pass a separately preregistered exact-equivalence audit.

## Evidence boundary

The decision extracted seven source-only diagnostic rows from the tracked L3
success manifest. It did not open the ignored diagnostic CSV, GSHHG archive or
members, geometry, eligible-land support, predictor/model/prediction bytes,
external target or QA values, or final evaluation results. It made no network
request and computed no new distance surface.

This is a historical cartographic covariate for the four predeclared study
cities. It is not permission to claim nationwide applicability, modern
real-time coastline truth, or uniform 30 m positional truth. Any new city
requires a new applicability audit.

## What remains closed

Tract aggregation, feature names, predictor construction, model fitting,
protocol promotion, and external target access remain closed. The source and
algorithm locks exist in this append-only decision terminal, while their
canonical plan fields remain false until a separate tracked-only v8 consumes
the terminal.

The next stage is therefore exactly
`publish_tracked_only_plan_v8_after_water_distance_freeze`. V8 may authorize
only the separate predictor-source and calibration-contract freeze.

## Authentication

After the publication commit is pushed from clean synchronized `main`, run:

```powershell
.\.venv\Scripts\python `
  scripts\audit_multicity_portable_water_distance_freeze_v2.py --check-only
```

Authentication must fail closed if the terminal, its direct-child Git
lineage, the v7 plan, any frozen runtime, or an authenticated prerequisite has
changed.
