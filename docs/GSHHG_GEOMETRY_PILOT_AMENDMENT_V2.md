# GSHHG geometry-pilot structural amendment v2

Status at amendment: **v1 failed; source structure inspected; no diagnostic
distance value computed**

The v1 preregistration was committed and pushed at
`08d69c3b4ba4c2d9e4ffb45e11727840ae3341b3` before either full-resolution
geometry layer was opened. Its numerical thresholds, four fixed reference
points, five lake seeds, radius ladder, comparison definition, and access
locks remain unchanged.

## Why v1 failed

The first authenticated L1/L2 read found two structural facts that v1 had
forbidden:

1. L1 contains exactly one invalid polygon, source ID `2380`, with a ring
   self-intersection near the Maine coast.
2. The five unchanged Great Lakes seeds identify three source polygons rather
   than five. GSHHG represents Superior, Michigan, and Huron as one connected
   L2 water polygon (`180507`); Erie (`180515`) and Ontario (`180517`) are the
   other two.

V1 therefore failed before any GSHHG-to-Census distance was calculated. The
failure is retained as evidence; it is not relabeled as a pass.

## V2 source-structure rule

V2 changes only the two rules that could not be specified until the fixed
source structure was observed:

- the one exact invalid L1 geometry must match its frozen ID, reason, bounds,
  and WKB hash, then undergo a deterministic `shapely.make_valid` repair;
  polygonal components are retained and the zero-area line remnant is
  discarded. Any other invalid L1 geometry fails closed;
- all five original lake seeds must still match exactly one positive-area L2
  polygon each, but repeated source IDs are now allowed only under the exact
  frozen mapping `180507, 180507, 180507, 180515, 180517`. The three polygons
  are deduplicated before exterior extraction.

This distinguishes **five named lake identities** from the topology of a
dataset that stores connected water as fewer polygons. It does not select a
source using temperature, model, or diagnostic-distance performance.

Exact geometry hashes, bounds, reported areas, coordinate counts, and the
repair-area tolerance are frozen in
`configs/multicity/gshhg_geometry_pilot_v2.toml`.

## What did not change

No diagnostic distance had been calculated when this amendment was written.
The following v1 elements remain byte-bound through the v1 configuration hash:

- source archive, member inventory, required-member hashes, and license;
- four fixed city reference points and their projected CRSs;
- the five lake seed coordinates;
- L1 exterior-only and L2 exterior-only semantics;
- antimeridian seam removal;
- all radius, chunk, worker, STRtree, brute-force, and geodesic thresholds;
- the signed GSHHG-minus-Census comparison;
- every target, QA, predictor, model, prediction, feature-surface, and
  final-evaluation prohibition.

V2 may still finish only as `geometry_pilot_complete_source_not_frozen`. A
separate decision is required before a portable source or algorithm can be
frozen.
