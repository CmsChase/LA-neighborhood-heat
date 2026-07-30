# GSHHG L3 hierarchy audit report

Date: 2026-07-30

## Result

The preregistered, target-blind GSHHG L3 hierarchy audit passed every
structural and numerical gate. The canonical terminal state is
`gshhg_l3_hierarchy_audit_v2_complete_source_not_frozen`.

This result supports a separate decision about freezing the portable
water-distance source and algorithm. It does not itself create a source lock,
algorithm lock, feature-name lock, predictor, model, protocol lock, or
external-target authorization.

## Append-only lineage

The audit preserved the complete failure and amendment history:

1. The V1 executor was committed and pushed in
   `ab51a9506d77b7ac0efcdfb97e494c665cd80e5b`.
2. V1 failed in the structure phase, before any probe or distance was
   computed. Source ID `180515` had one preregistered normalized-WKB hash
   character transcribed as final `c` instead of the authenticated final `a`.
   The immutable failure was published in
   `fbf20ed7a601af8e9f77ad768f1267b8a6503a0d`.
3. A separately committed V2 amendment changed only that one character. Its
   publication commit is
   `e07ef369ea3310ec67956b06436f793f01c89942`.
4. The V2 executor, verifier, and tests were committed in
   `7b7c804293057458c82353f705c1d9cf28375301`.
5. The successful canonical V2 terminal was blindly committed before result
   inspection in
   `0afb1f9868378f12e8fe8b66f5772fde6685ed1f`, then authenticated with the
   terminal `--check-only` path.

The amendment changed no archive identity, selected parent, hierarchy rule,
probe, numerical threshold, access rule, or scientific decision boundary.

## Canonical evidence

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT.json` | 109,139 | `9b206f449d71f23ff0f13d0adca436a2d433140560fef92646d48a7e5c522070` |
| `manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT_V1_FAILURE.json` | 9,954 | `b5eb32e3de1702250e36a7eb81b2ea0c78551930a7f92abe5278d21c05a0ea9e` |
| `configs/multicity/gshhg_l3_hierarchy_audit_amendment_v2.toml` | 4,840 | `c60c2d699e94bca832a78b4959db9a5333b2aa3ae37bfdd72d9c0eb6f37ff127` |
| `data/interim/multicity/water_distance/gshhg_l3_hierarchy_audit/diagnostic_distances.csv` | 2,113 | `ab3be04995c70359ed7b65a59d63aec84202f7319f5484dc038e172e76949a8c` |

The success manifest internal commit is
`9b7f6c814bda4e97120a6768b88feae37ee73044883b2ec8cad10db8d4af0f0b`.
The V1 failure internal commit is
`e5b8e1e242276bcb530990ee070739f84e48177c431e556cfebb4819c92ea067`.
No V2 failure file exists, as required for the successful terminal state.

## Source and access audit

- GSHHG release: 2.3.7 full-resolution shapefile archive.
- Archive bytes: 149,157,845.
- Archive SHA-256:
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`.
- Archive inventory: 402 members, bound by the full archive hash and central
  directory inventory.
- Opened members: exactly the 12 allowlisted L1, L2, and L3 `.shp`, `.shx`,
  `.dbf`, and `.prj` members.
- All 12 opened members passed their individual CRC and hash checks.
- Unauthorized and L4 member opens: zero.
- Geometry export or redistribution: none.
- Isolated temporary extraction: deleted after the audit.

The archive-wide integrity statement rests on the whole-ZIP SHA-256 and
central-directory inventory. The audit did not stream every unauthorized
member merely to call `testzip`.

## Structural result

- L1: 179,837 polygons. The one predeclared invalid polygon was repaired by
  the already fixed deterministic rule; all geometries were valid afterward.
- L2: 6,660 polygons. The 56 negative-area river-lake records were excluded by
  the frozen rule.
- L3: 1,437 polygons; all were valid and had positive area.
- Selected L2 parents: `180507`, `180515`, and `180517`.
- Direct L3 descendants: 139 total:
  - parent `180507`: 118;
  - parent `180515`: 8;
  - parent `180517`: 13.
- All 139 selected children were strictly within their parent and disjoint
  from the parent exterior boundary.
- All 7,009 sibling pairs had zero positive-area overlap.
- The selected L3 exteriors contained 6,068 segments. Their maximum longitude
  jump was 0.010833 degrees, far below the fixed 180-degree rejection gate.
- No city, bounding box, name, area, distance, target, model, prediction, or
  result value influenced hierarchy inclusion.

## Distance diagnostics

| Point | L1/L2/L3 inclusive | L1/L2 only | L3 reduction |
|---|---:|---:|---:|
| Chicago | 1.162383 km | 1.162383 km | 0 km |
| Houston | 36.286503 km | 36.286503 km | 0 km |
| Los Angeles | 20.208299 km | 20.208299 km | 0 km |
| Phoenix | 262.207756 km | 262.207756 km | 0 km |
| L3 probe, parent `180507` | 9.369538 km | 40.318219 km | 30.948681 km (76.761%) |
| L3 probe, parent `180515` | 2.883989 km | 4.027016 km | 1.143027 km (28.384%) |
| L3 probe, parent `180517` | 2.639197 km | 7.535044 km | 4.895846 km (64.974%) |

The four previously fixed city points replayed exactly, with a maximum
absolute error of 0 m. L3 therefore does not change those four reference
distances. It materially changes all three deterministic real-island probes,
showing why the L3 exteriors are needed for a faithful Great Lakes hierarchy
contract.

## Numerical result

- STRtree and brute-force results were identical at all seven points.
- Forward and reverse source order produced identical results.
- All accepted search-radius expansions produced identical results.
- Line chunk sizes 256, 1,024, and 4,096 differed by at most 0 m.
- Every combination of 1, 2, or 4 workers and query chunks of 1, 2, or 4
  produced the same result hash and at most 0 m difference.
- All projected-versus-geodesic gates passed. The largest absolute difference
  was 8.660850 m, and the largest relative difference was 0.04784%, below the
  fixed 100 m and 0.5% limits.
- Probe identities were never reselected after a distance was observed.

## Interpretation and limits

The evidence closes the narrow structural question that blocked the earlier
L1/L2-only freeze: directly parented L3 island exteriors are topologically
coherent, numerically stable, backward-compatible at the four fixed city
points, and materially relevant at real Great Lakes probes.

This is not a predictive-performance evaluation. It does not show that GSHHG
is modern positional truth, a real-time shoreline, or uniform 30 m data.
GSHHG 2.3.7 is a reproducible 2017 cartographic release based largely on older
WVS and WDBII sources. The audit also does not authorize redistribution.

The next safe stage is exactly
`separate_portable_water_distance_source_and_algorithm_freeze_decision`.
Until that separate decision and a later planning transition pass:

- `source_frozen = false`;
- `algorithm_frozen = false`;
- `predictor_build_authorized = false`;
- all external target and target-QA access remains closed.

## Read-only authentication

From a clean synchronized `main` checkout:

```powershell
.\.venv\Scripts\python `
  scripts\audit_multicity_gshhg_l3_hierarchy.py --check-only
```

This authenticates the committed terminal. It must not be replaced by a new
geometry run or by manual edits to any manifest or diagnostic value.
