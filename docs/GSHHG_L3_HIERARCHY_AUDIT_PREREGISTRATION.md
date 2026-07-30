# GSHHG L3 hierarchy-audit preregistration

Preregistration date: 2026-07-30

State: `gshhg_l3_hierarchy_audit_preregistered_geometry_unopened`

## Purpose

The previous source-and-algorithm freeze decision retained GSHHG 2.3.7 as the
candidate source but rejected the L1/L2-only shoreline contract. GSHHG level 3
represents islands in lakes, so omitting direct L3 descendants of the selected
Great Lakes polygons could give island land an incorrect shoreline distance.

This preregistration fixes the source-only audit before any L3 shapefile member
or geometry is opened. It does not perform that audit, create a source or
algorithm lock, or authorize a predictor.

The append-only manifest is:

`manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json`

- file bytes: `21,892`
- file SHA-256:
  `ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f`
- internal commit:
  `7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7`
- exact config SHA-256:
  `6fcac13640a8914543d7d057e19cd18ec7ddea74a8d3f50406f4c5dd81e2c1cd`

## Fixed source boundary

The audit remains bound to the already authenticated 149,157,845-byte GSHHG
2.3.7 archive under SHA-256
`8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`.
The preregistration program inherited that identity from the authenticated
deferred-decision record; it did not open or hash the local archive itself.

The exact L3 members to be opened only after the later authorization transition
are:

- `GSHHS_shp/f/GSHHS_f_L3.dbf`
- `GSHHS_shp/f/GSHHS_f_L3.prj`
- `GSHHS_shp/f/GSHHS_f_L3.shp`
- `GSHHS_shp/f/GSHHS_f_L3.shx`

Their individual byte counts, SHA-256 values, CRCs, row count, bounds, and
semantic hash are deliberately not predicted. The future structure phase must
observe and record them.

The upstream hierarchy and conversion semantics are bound to the tag-pinned
[hierarchy definition](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/SHAPEFILES.TXT),
[shapefile conversion source](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/src/polygon_to_shape.c),
[header semantics](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/src/wvs.h),
and [source notes](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/README.md).

## Fixed hierarchy contract

The candidate linework is:

1. the unchanged, repaired V2 L1 ocean exteriors;
2. the unchanged exteriors of L2 source IDs `180507`, `180515`, and `180517`;
3. every full-resolution L3 Polygon whose integer `parent_id` is exactly one
   of those three L2 IDs, using each selected L3 exterior only.

The inclusion rule cannot use a city, bounding box, name, source area,
distance, eligible-land support, target/QA value, model, prediction, or result.
`sibling_id` is audit metadata and cannot control selection. Canonical source
IDs allow the official `-E` and `-W` suffixes created when a polygon is split
at the antimeridian.

L4 members may not be opened in this audit. GSHHG defines L4 as ponds inside
L3 islands, so L4 boundaries are outside the selected Great-Lakes-water
shoreline contract. Any observed L3 interior ring is also excluded by the
exterior-only rule, counted separately, and not assigned an unverified
meaning.

## Structure phase

Before any probe or distance, the future auditor must authenticate:

- exact member presence and case, bytes, SHA-256, and CRC;
- exact columns, dtypes, `EPSG:4326`, level, geometry type, validity,
  non-emptiness, positive finite reported area, bounds, and source values;
- unique canonical source IDs, including any official antimeridian suffix;
- the complete L3 semantic hash;
- the selected direct-descendant subset hash and count for each parent;
- the selected exterior-linework hash and interior-ring count;
- strict child-within-parent topology, no child/parent exterior intersection,
  no selected sibling interior overlap, and no selected exterior longitude
  jump at or above 180 degrees.

No repair, decomposition, spatial fallback, or hierarchy substitution is
allowed. At least one qualifying direct L3 descendant must exist.

## Source-only probes and numerical gates

For each selected L2 parent with at least one direct child, the audit selects
one probe component by source-reported area descending and canonical source ID
ascending. It normalizes that Polygon, derives a Shapely representative point,
and chooses the northern WGS84 UTM zone using the preregistered longitude
formula. This is a deterministic source-geometry rule; no observed distance
may change the chosen component or point.

Each real probe must be strictly inside its component, have finite positive
distances, and meet all of these gates:

- the unique nearest candidate provenance is that probe's own L3 source ID;
- the indexed distance equals direct point-to-own-exterior distance within
  `0.000001 m`;
- the L3-inclusive distance is more than `0.000001 m` shorter than the
  L1/L2-only distance;
- projected/geodesic disagreement is no greater than
  `max(100 m, 0.005 × geodesic distance)`.

The audit also replays the four unchanged Los Angeles, Phoenix, Houston, and
Chicago source-only points. Their GSHHG distances must reproduce the V2 values
within `0.000001 m`; the Census layer is not reopened.

All existing numerical gates remain fixed: float64 exact point-to-line
distance, radii `64/128/256/512/1024/2048 km`, line chunks
`256/1024/4096`, query chunks `1/2/4`, workers `1/2/4`, forward/reverse source
order, STRtree/brute-force parity, and WGS84 50 m geodesic densification.

## Failure and amendment policy

The structure phase must pass completely before any probe or distance phase.
Any unexpected member, schema, hierarchy, topology, ID, validity, or geometry
fact creates an append-only V1 failure record while
`distance_values_computed=false`.

A structural amendment is allowed only for an exact observed source-structure
correction, after preserving the failure and after a separate commit and push.
It cannot change the archive or version, the three L2 IDs, the all-direct-
descendants rule, L4/exterior semantics, existing points, numerical thresholds,
or access locks.

A numerical failure cannot be converted into a pass by relaxing a tolerance,
changing a probe, or deleting a radius/chunk/worker gate. It must be preserved
and returned to an independent scientific decision.

## Access and next transition

The preregistration program was tested in a minimal project copy containing no
`data/`, `exports/`, or `reports/` directory. Forbidden ZIP, vector, Parquet,
raster, model, legacy-auditor, and network entry points were trapped by tests.
The canonical run:

- made zero network requests;
- opened no GSHHG archive or member;
- opened no L3 or L4 geometry;
- opened no eligible-land grid or other public-source geometry;
- computed no distance or feature surface;
- constructed no predictor;
- fit no model and computed no prediction; and
- opened no target, target-QA, LST, or final-evaluation output.

Planning schema v6 has now completed the separate tracked-input-only
transition. It authenticated the exact committed preregistration, historical
v5 planning blob, pilot and deferred-decision manifests, configuration, code,
and local Git blobs without opening the GSHHG ZIP, a member, geometry, support,
target, model, or result. Its internal commit is
`1789d828f212e0cd65f87c9427eb4a7fbd1697cc7170ebb98a80806659afbc86`.

The transition closes the completed preregistration permission and narrowly
sets `target_blind_gshhg_l3_hierarchy_geometry_read=true`. That grant is
limited to the exact local archive and the 12 listed full-resolution
L1/L2/L3 shapefile members, with no network/download, L4, Census, eligible
support, predictor/model/target/result, geometry-export, or redistribution
permission. Before opening the first member, the future executor must itself
be committed and pushed and v6 `--check-only` must pass from clean `main` with
`HEAD == origin/main`.

Even a passing L3 audit will authorize only a second, separate
portable-water-distance source-and-algorithm freeze decision.
