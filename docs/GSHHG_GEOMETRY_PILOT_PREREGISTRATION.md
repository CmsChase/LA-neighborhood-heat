# GSHHG geometry-pilot preregistration

Status at registration: **source archive authenticated; geometry and diagnostic
distance values not yet opened**

Date: 2026-07-29 Asia/Shanghai

This record freezes the source-only comparison before the first read of the
GSHHG L1 or L2 geometry. It is not a source lock, an algorithm lock, a
predictor build, or permission to access any temperature target or target-QA
value.

## Fixed source

- GSHHG shapefile release: `2.3.7`
- archive: `gshhg-shp-2.3.7.zip`
- bytes: `149157845`
- SHA-256:
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`
- complete ZIP inventory: 402 members and 416,243,458 uncompressed bytes
- inventory SHA-256:
  `fa348ee1fd68c9b10251735b60f3afd83b230e660165502e493dcc6605519d05`
- canonical URL:
  `https://github.com/GenericMappingTools/gshhg-gmt/releases/download/2.3.7/gshhg-shp-2.3.7.zip`
- license: GNU Lesser General Public License v3; the archived license and
  notice bytes are part of the required-member hash contract

The archive was downloaded once to an ignored raw-data directory. The first
attempt was invalidated because two transfer processes briefly wrote the same
file; that file was never opened as a source and was preserved under
`.tmp/gshhg-download-failure`. The canonical path was then populated through
one unique partial file and atomically promoted only after exact byte count,
SHA-256, all 402 ZIP members, and all CRC values passed.

## Fixed semantic rules

1. Ocean shoreline comes only from full-resolution L1 polygon exteriors.
   Interior rings are not treated as ocean.
2. River-lakes are excluded by the official negative-area rule before any lake
   identity check.
3. Five predeclared interior points must select five different, positive-area
   L2 polygons for Superior, Michigan, Huron, Erie, and Ontario. No target
   value, polygon size ranking, or result-driven name assignment may change
   this membership.
4. Only the selected L2 exteriors are used in this four-city pilot. L3 island
   shores and Antarctica L5/L6 are explicitly outside scope.
5. Artificial shapefile closure segments with both endpoints on the same
   `+180` or `-180` meridian are removed. Any remaining segment with a
   longitude jump of 180 degrees or more fails closed.
6. Census remains the separately authenticated `MTFCC=L4150` U.S.-only
   benchmark. Its `NAME` field remains audit-only.

## Fixed numerical gates

- four source-only reference points: Los Angeles, Phoenix, Houston, and
  Chicago, with coordinates and UTM CRSs frozen in
  `configs/multicity/gshhg_geometry_pilot_v1.toml`;
- radius ladder: 64, 128, 256, 512, 1024, and 2048 km;
- line-chunk vertex counts: 256, 1024, and 4096;
- query chunk sizes: 1, 2, and 4;
- worker counts: 1, 2, and 4;
- STRtree versus brute-force tolerance: `1e-6 m`;
- chunk, query, worker, source-order, and radius invariance tolerance:
  `1e-6 m`;
- independent WGS84 geodesic reference densification: at most 50 m;
- projected/geodesic acceptance:
  `abs(projected - geodesic) <= max(100 m, 0.005 * geodesic)`.

The thresholds, coordinates, semantic choices, and archive identity above were
written before any GSHHG geometry layer or diagnostic distance result was
opened. A failed gate must stop the pilot; it may not be repaired by changing a
threshold after seeing the result.

## Access boundary

This pilot may read only the pinned GSHHG L1/L2 geometry and the already
authenticated Census coastline. It may compute distances only at the four
fixed, unlabeled source-diagnostic points. It may not open an eligible-land
grid, produce a distance feature surface, aggregate tracts, construct a
predictor, fit a model, compute a prediction, inspect final-evaluation output,
or open any external temperature or QA value.

Successful completion can authorize only a separate human-reviewed
source-and-algorithm freeze decision. It cannot directly authorize predictor
construction.
