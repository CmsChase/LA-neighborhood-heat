# Target-blind GSHHG geometry pilot

Status: **V2 geometry pilot complete; source and algorithm remain unfrozen**

Primary record:
[`GSHHG_GEOMETRY_PILOT.json`](../manifests/multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT.json)

V1 failure record:
[`GSHHG_GEOMETRY_PILOT_V1_FAILURE.json`](../manifests/multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT_V1_FAILURE.json)

This pilot answers one source-design question for the cross-city continuation:
does a fixed global shoreline source materially change public-geometry
distance relative to the U.S.-only Census benchmark, especially for Phoenix?
It does not build a predictor, aggregate a tract, fit a model, or read a
temperature or target-QA value.

## Result

The two source contracts materially diverge at the fixed Phoenix diagnostic.

| Fixed target-blind reference point | GSHHG L1 + seed-selected L2 contract | Census U.S. `L4150` contract | GSHHG minus Census |
|---|---:|---:|---:|
| Los Angeles | 20.208 km | 21.988 km | -1.780 km |
| Phoenix | 262.208 km | 482.409 km | **-220.201 km** |
| Houston | 36.287 km | 36.758 km | -0.471 km |
| Chicago | 1.162 km | 1.120 km | +0.042 km |

These are source-diagnostic point distances, not tract predictors and not
model results. At the Phoenix point, the GSHHG contract is 220.201 km shorter
than the Census contract. This demonstrates that the two source definitions
are not interchangeable; it does not establish a true-distance error or show
that either source is positional ground truth. The manifest records the
nearest GSHHG location as `(-113.770306, 31.570972)` with `source_id=2`,
`component_id=2`, polygon `0`, run `0`, and chunk `77`. That source location
is consistent with the intended Gulf-of-California sensitivity check. The
Census comparison instead reaches archive row `3419` at
`(-117.136644, 32.616387)`; its audit-only `NAME` is `Pacific` and did not
control geometry inclusion. These coordinates make the semantic difference
auditable; they still do not prove which historical cartographic source is
positionally more accurate.

The GSHHG contract is specifically the exteriors of all full-resolution L1
polygons plus the exteriors of three positive-area L2 connected-water
polygons selected by five fixed seeds for Superior, Michigan, Huron, Erie, and
Ontario. The three L2 polygons are the source topology; “five Great Lakes” is
an identity shorthand, not a claim that GSHHG stores five separate lake
polygons. L3 islands-in-lakes shores are excluded. Consequently, this pilot
supports only the declared four-city study scope and does not establish an
arbitrary-city Great-Lakes-island contract.

GSHHG 2.3.7 therefore passes the V2 geometry-comparison gate as a candidate
for a later source contract within the declared four-city scope. It is **not**
frozen by this pilot. A separate decision must weigh its reproducibility and
international coverage against its age, lake quality, limited positional
authority, one deterministic geometry repair, and LGPL obligations.

## Source authentication

- archive: `gshhg-shp-2.3.7.zip`
- local reauthentication path:
  `data/raw/multicity/water_distance/gshhg-shp-2.3.7.zip`
- bytes: `149157845`
- SHA-256:
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`
- published and observed MD5: `cb82015f8533f9611b4adba2c404ba44`
- members: 402
- expanded member bytes: 416,243,458
- complete source-inventory SHA-256:
  `fa348ee1fd68c9b10251735b60f3afd83b230e660165502e493dcc6605519d05`
- full-resolution L1: 179,837 polygons
- full-resolution L2: 6,660 polygons, including 56 negative-area river-lakes
- license: GNU Lesser General Public License v3

The validator authenticated the archive bytes before ZIP parsing, rejected
unsafe member names and file types, enforced size and compression limits,
verified the exact member inventory and required-member hashes, and read every
member through its CRC. Nothing was extracted.

Official fixed-version references:

- [GSHHG 2.3.7 release](https://github.com/GenericMappingTools/gshhg-gmt/releases/tag/2.3.7)
- [2.3.7 README](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/README.md)
- [shapefile level definitions](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/SHAPEFILES.TXT)
- [official shapefile conversion code](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/src/polygon_to_shape.c)
- [LGPL v3 text](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/LICENSE)
- [NOAA/NCEI archive record and decommission notice](https://www.ncei.noaa.gov/archive/archive-management-system/OAS/bin/prd/jquery/accession/details/304143)

## V1 failure and V2 amendment

V1 was committed before the geometry layers were opened. It failed before any
distance was computed:

1. L1 contained one invalid source polygon, while V1 permitted no repair.
2. Five fixed Great Lakes seeds mapped to three source polygons rather than
   five. GSHHG stores Superior, Michigan, and Huron as one connected water
   polygon; Erie and Ontario are separate.

The failure was retained rather than relabeled. V2 was then committed before
any distance calculation and changed only these source-structure rules:

- exact L1 source ID `2380` must have the frozen self-intersection reason,
  bounds, and WKB hash; `shapely.make_valid` must return the frozen polygonal
  result with a planar area change below `1e-6` square degree;
- all five unchanged lake seeds must match exactly one positive-area L2
  polygon, under the fixed mapping
  `180507, 180507, 180507, 180515, 180517`; the three source polygons are then
  deduplicated.

All points, seeds, distance thresholds, radius values, chunk sizes, worker
counts, comparison definitions, and access locks remained byte-bound to V1.
See the
[V1 preregistration](GSHHG_GEOMETRY_PILOT_PREREGISTRATION.md) and
[V2 structural amendment](GSHHG_GEOMETRY_PILOT_AMENDMENT_V2.md).

## Geometry and numerical gates

- ocean linework used only L1 polygon exteriors;
- five fixed lake seeds selected three authenticated positive-area L2
  connected-water polygons, whose exteriors were added to the L1 exteriors;
  negative-area river-lakes and L3 islands-in-lakes shores were excluded;
- artificial segments with both endpoints on the same `+180` or `-180`
  meridian were removed; the global L1 audit found exactly 10 such segments,
  no opposite-sign dateline segments, no remaining jump at or above 180
  degrees, and a 0.149722-degree maximum retained jump;
- source polygons were conservatively filtered in WGS84 before local
  projection; the global layer was never projected into one UTM CRS;
- exact float64 STRtree distance matched brute force within `1e-6 m`;
- radius expansion, source row reversal, line chunks of 256/1024/4096
  vertices, true vector query chunks of 1/2/4 repeated without spatial
  alteration, and 1/2/4 workers changed no distance;
- independent WGS84 geodesic audits passed the preregistered
  `max(100 m, 0.5%)` tolerance.

The four city CRSs were the already declared metre-based UTM systems. The
largest projected-versus-geodesic difference was about 598 m for the
482.4-km Phoenix Census diagnostic, within its preregistered 0.5% allowance.

## Interpretation limits

- GSHHG 2.3.7 was released in 2017. NOAA/NCEI decommissioned its archive of
  the product in May 2025 with no further NCEI updates. The pinned release is
  reproducible, but it is a fixed historical cartographic source, not a
  real-time shoreline product.
- `full resolution` means the source was not Douglas-Peucker decimated. It
  does not imply modern, uniform, or 30 m positional accuracy.
- GSHHG ocean geometry largely derives from older WVS material, while lake
  geometry derives from still older and lower-quality WDBII material. The
  maintainer warns that offsets from modern GPS-aligned sources exist. The
  pilot therefore treats GSHHG as a reproducible covariate candidate, not
  shoreline positional truth.
- L1 excludes Antarctica; L5/L6 are outside this four-U.S.-city pilot. The
  result must not be advertised as a complete arbitrary-world shoreline
  contract.
- The five lake names are imposed by frozen external seed points; the
  shapefile itself does not supply reliable lake names. Those seeds select
  three connected-water L2 polygons, and the chosen exterior-only rule omits
  L3 lake-island shores.
- The four points—one per city—test source semantics and numerical stability.
  They do not validate source positional accuracy, neighborhood-level
  variation, a 30 m distance surface, or tract aggregation. All of those
  remain outside this pilot.

## Access ledger and next gate

The audit program made zero network requests. It read only the pinned GSHHG
L1/L2 geometry and the authenticated Census coastline. It computed eight
fixed-point source diagnostics. It opened no eligible-land grid, feature
surface, target, target-QA, predictor, model, prediction, or final-evaluation
output. The one canonical download and one preserved failed concurrent
download artifact are operator-recorded history, explicitly marked as not
mechanically authenticated by this audit.

Current state:

- `source_lock_created = false`
- `algorithm_lock_created = false`
- `predictor_build_authorized = false`
- `protocol_lock_created = false`

The next safe stage is a separate
`portable_water_distance_source_and_algorithm_freeze_decision`. That decision
may accept GSHHG, reject it, or request a source-only sensitivity comparison.
It may not construct the predictor in the same step. Before any freeze, it
must also prove that excluding L3 lake-island shores cannot affect the four
frozen eligible-land supports, or preregister a narrower/revised algorithm,
feature name, and applicability claim.
