# Portable water-distance source review

Status: **review and follow-on geometry pilot complete; source and algorithm
remain unfrozen**

Review record:
[`WATER_DISTANCE_REVIEW.json`](../manifests/multicity/reviews/portable_water_distance/WATER_DISTANCE_REVIEW.json)

This is a target-blind scientific review for the cross-city continuation. It
does not change the completed Los Angeles model, calculate a distance
predictor, fit a model, or open any Phoenix, Houston, or Chicago target or
target-QA value.

## Outcome

The exact-byte-audited **Census TIGER/Line 2019 National Coastline** is the
strongest reproducibility benchmark and the best conditional U.S.-only
fallback. It is not yet the final portable source.

The remaining question is semantic, not computational:

- a Census-only feature means distance to the nearest **U.S. Census L4150
  Coastal, Territorial, or Great Lakes line**;
- the scientifically preferred physical feature means distance to the nearest
  **global ocean shoreline or one of the five Great Lakes**, regardless of a
  national border.

These are not equivalent for Phoenix because the Census national file does
not contain the Mexican Gulf of California shoreline. Calling a Census-only
value “distance to the nearest ocean” would therefore be misleading. The next
safe stage at the time of this review was a source-only GSHHG geometry
comparison conducted before any predictor or target access. That pilot is now
complete: its V1 topology assumptions failed before distance access, and its
separately preregistered V2 contract passed. The candidate source remains
unfrozen pending a separate source-and-algorithm decision.

If that comparison does not support a reproducible global contract, the
Census source remains an acceptable fallback only under the explicit name and
interpretation “U.S. Census qualifying-shoreline distance, a statistical
coastline proxy.”

## Authenticated Census benchmark

The existing source was reauthenticated by an audit program that made no
source-data network or download request. Candidate facts were separately
checked on the official online documentation linked below:

| Check | Result |
|---|---:|
| File | `data/raw/static/tl_2019_us_coastline.zip` |
| Bytes | 16,631,608 |
| SHA-256 | `10c7e252a96a46552bf6045cc46f0605f645feeab70be545fab1bac869723494` |
| ZIP members | 7; expected sizes and CRC-32 values all passed |
| CRS | `EPSG:4269` |
| Rows | 4,248 |
| MTFCC | all `L4150` |
| Geometry | 4,248 valid, nonempty `LineString` records |
| Great Lakes records | 377 |
| Geometry semantic SHA-256 | `7a57e6388d3e702ab2ac6fdee4afb019ee4ee093b5f14984ca235364f6b21f71` |

The Census definition of `L4150` is the line separating land or inland water
from Coastal, Territorial, or Great Lakes water, including defined closure
lines at some river mouths. Census also states that this coastline is for
statistical display and is not a legal shoreline. See the
[2019 TIGER/Line technical documentation](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2019/TGRSHP2019_TechDoc.pdf)
and the fixed
[2019 Coastline archive](https://www2.census.gov/geo/tiger/TIGER2019/COASTLINE/).

The 2019 release is preferred over downloading a newer annual Census file for
this benchmark because its exact bytes are already audited, it predates every
study target year, and a later vintage would add a source change without
resolving the cross-border semantic problem.

## Candidate assessment

| Candidate | Strength | Disqualifying issue or next use | Decision |
|---|---|---|---|
| Census TIGER/Line 2019 Coastline | One small national file; fixed audited bytes; `L4150` directly includes U.S. coastal, territorial, and Great Lakes lines | U.S.-only coverage; variable source accuracy; statistical rather than legal shoreline | Provisional benchmark and conditional fallback |
| GSHHG 2.3.7 full resolution | Global ocean polygons include the Gulf of California; archived fixed release | Five Great Lakes require a separately frozen identity rule; lake geometry is older and unnamed; LGPL | Advance to target-blind geometry pilot |
| Natural Earth 10m | Global, named lakes, public domain, versioned | Generalized at 1:10,000,000, too coarse for a neighborhood predictor built on 30 m support | Reject as primary |
| NOAA CUSP | High local detail | NOAA says it is updated without preserving versions; dates, sources, accuracy, and coverage vary; U.S.-only | Local sensitivity check only |
| USGS 3DHP | Promising explicit “Ocean or Great Lake” class | Product and services are still evolving during the NHD transition | Defer for future migration or sensitivity |

Official primary-source references:

- [NOAA/NCEI GSHHG accession](https://www.ncei.noaa.gov/archive/archive-management-system/OAS/bin/prd/jquery/accession/details/304143)
- [GSHHG shapefile level definitions](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/master/SHAPEFILES.TXT)
- [Natural Earth 1:10m physical vectors](https://www.naturalearthdata.com/downloads/10m-physical-vectors/)
- [NOAA Continuously Updated Shoreline Product](https://shoreline.noaa.gov/cusp.html)
- [USGS 3DHP data products](https://www.usgs.gov/3d-hydrography-program/access-3dhp-data-products)

## Reviewed common algorithm

The point-level nearest-line diagnostic kernel is now implemented and audited
by the follow-on geometry pilot. The full eligible-grid calculation, tract
aggregation, and predictor pipeline remain deliberately unimplemented and
unfrozen. Whichever source contract survives the separate freeze decision
must use the same rules in all four cities:

1. Freeze exact source bytes, license, schema, CRS, and qualifying-water
   membership before target access. Do not choose source members by city. For
   the Census benchmark, select only by `MTFCC=L4150`; `NAME` counts are an
   audit inventory and may not include or exclude lines.
2. Calculate distance at every center of the frozen 30 m eligible-land
   target-grid cells. Do not substitute tract centroids.
3. Use each city's already declared projected target-grid CRS only after
   verifying that it is projected, metre-based, and produces finite source
   geometry.
4. Use `float64` metres for the exact nearest-line query, then convert to
   kilometres.
5. Use a single deterministic radius ladder of
   `64, 128, 256, 512, 1024, 2048 km`. At each step, conservatively prefilter
   source geometry in its native CRS, project the candidate subset, and accept
   only if every eligible distance is finite and the maximum is strictly
   inside the active radius. This avoids the Phase I fixed-500-km failure mode
   while avoiding invalid projection of an entire global shoreline into one
   local UTM zone.
6. Aggregate over the same fixed eligible-land cells using arithmetic mean
   and NumPy's `linear` 10th percentile.
7. Record source, support, CRS, code/runtime, and per-city completeness hashes.
   Chunk size, worker count, and search-radius expansion must not change the
   values.

The proposed global names are
`ocean_great_lakes_distance_mean_km` and
`ocean_great_lakes_distance_p10_km`. If the Census fallback is chosen, the
names must instead say
`us_census_qualifying_shoreline_distance_*`. The historical Phase I
`pacific_coast_distance_*` values and model lock remain immutable and may not
be silently aliased.

## Completed follow-on gate: GSHHG source-only geometry pilot

The completed stage was permitted to read only public shoreline geometry.
Before it could support a later source freeze, it had to:

1. pin the official GSHHG 2.3.7 full-resolution archive URL, exact bytes,
   members, hashes, CRS, schema, and license;
2. select global ocean level 1 and use five fixed named-lake seeds to audit the
   source's actual distinct connected-water topology, with river-lake records
   excluded; freeze an unambiguous rule for converting level-1 polygons to
   shoreline lines and for normalizing antimeridian-crossing geometry;
3. use fixed target-blind points to compare global and Census-only geometry,
   including a separately reported Phoenix difference;
4. prove STRtree results match brute force on fixed samples;
5. prove values are invariant to search-radius expansion, chunk size, and
   worker count;
6. audit projected distance against a geodesic reference under a
   predeclared tolerance; and
7. record zero target/QA access, zero predictor construction, and zero model
   work.

The completed pilot found that five named lake seeds resolve to three
connected-water L2 source polygons, rather than five distinct polygons. Its
pre-distance V2 amendment froze that exact topology, one deterministic L1
repair, and the unchanged numerical gates. See
[`GSHHG_GEOMETRY_PILOT_REPORT.md`](GSHHG_GEOMETRY_PILOT_REPORT.md).

Only a separate decision may now freeze one semantic definition, one source
bundle, one feature name, and one implementation. Predictor construction
remains prohibited until that later gate.
