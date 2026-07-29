# Portable water-distance freeze decision

Decision date: 2026-07-29  
State: `decision_complete_freeze_deferred`  
Outcome: `deferred_pending_gshhg_l3_hierarchy_contract`

## Decision

Do not freeze the current portable water-distance source-and-algorithm
contract yet.

GSHHG 2.3.7 remains the preferred candidate source because its fixed bytes
are reproducible and its cross-border L1 ocean-land geometry contains the
Mexican Gulf-of-California shoreline segment identified by the pilot, which
the U.S.-only Census benchmark does not contain. The completed source-only
pilot also passed every predeclared numerical gate.

The current algorithm nevertheless excludes GSHHG level-3 polygons. GSHHG
defines level 3 as islands in lakes. A shoreline-distance variable described
as distance to the nearest ocean or Great Lakes shoreline therefore cannot
silently exclude those island shores without either:

1. proving, before target access, that no frozen eligible-land support is
   affected; or
2. adding the relevant level-3 island exteriors under one fixed,
   city-independent hierarchy rule.

The four diagnostic city points do not close that gap. They tested source
semantics and numerical stability, not every eligible land cell or a complete
distance surface. Immediate source-and-algorithm freeze is therefore rejected,
while the candidate source itself is retained for a narrow closure audit.

## Evidence bound by the decision

The append-only decision manifest authenticates:

- the completed portable source review;
- the preserved V1 failure before distance calculation;
- the completed non-frozen V2 GSHHG pilot and all of its numerical gates;
- the exact 149,157,845-byte GSHHG 2.3.7 archive under SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`;
- the three selected Great Lakes connected-water L2 source IDs:
  `180507`, `180515`, and `180517`; and
- the fact that the V2 contract excluded L3 island shores.

The canonical record is
`manifests/multicity/reviews/portable_water_distance/WATER_DISTANCE_FREEZE_DECISION.json`.
Its internal commit is
`00e8ed677035f8f8315b7171fa8c969ca6c50c14b0114eff9e5024bb1c7b99b5`.

## Next safe stage

The only authorized next stage is
`preregister_target_blind_gshhg_l3_hierarchy_audit`.

That preregistration must be committed before any L3 shapefile member is
opened. The fixed whole-archive SHA already binds all member bytes. The
preregistration must fix the L3 member paths, hierarchy rule, validity policy,
probe, numerical gates, failure policy, and access ledger; the audit must then
record the observed member SHA/CRC, schema, CRS, and semantic hash. If an
unexpected source fact requires an amendment, that amendment must be committed
before any probe or distance is computed.
The candidate rule to test is:

> all qualifying L1 ocean exteriors, the three already selected L2
> connected-water exteriors, and every L3 island exterior whose direct
> `parent_id` is one of those three L2 source IDs.

L4 pond shores remain outside the ocean-or-Great-Lakes definition. Selection
may not depend on city outcomes, target values, distances, or model
performance. Passing that audit will not itself freeze the source or
algorithm; it will permit a second, separate freeze decision.

The provisional, still-unfrozen feature names are:

- `gshhg_ocean_great_lakes_shoreline_distance_mean_km`
- `gshhg_ocean_great_lakes_shoreline_distance_p10_km`

The Phase I Pacific-distance variables may not be aliased, and a Census
`L4150` fallback may not be substituted under either GSHHG name.

## Claim and license boundary

If later frozen, this source can support only a claim such as:

> a GSHHG 2.3.7 historical cartographic shoreline-distance covariate for the
> four predeclared study cities, conditional on their later authenticated and
> frozen eligible-land supports.

It cannot support a nationwide or all-city validation claim, a modern or
real-time shoreline claim, uniform 30 m positional accuracy, or physical
ground truth. A future city requires a new applicability audit.

The release is old cartographic source material: the official documentation
describes level 1 as land/ocean, level 2 as lakes, level 3 as islands in
lakes, and level 4 as ponds in those islands. The project also records the
source-age, datum, and positional-offset cautions in the upstream README.
See the official
[GSHHG hierarchy definition](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/SHAPEFILES.TXT),
[GSHHG 2.3.7 source notes](https://raw.githubusercontent.com/GenericMappingTools/gshhg-gmt/2.3.7/README.md),
[fixed release](https://github.com/GenericMappingTools/gshhg-gmt/releases/tag/2.3.7),
and
[NOAA/NCEI accession record](https://www.ncei.noaa.gov/archive/archive-management-system/OAS/bin/prd/jquery/accession/details/304143).

The archive notice, historical README, and GitHub repository UI label use
non-identical LGPL-version wording. Until a legal review, future
redistribution must use the LGPL 3.0 common-denominator compliance baseline,
retain the copyright and permission notice plus source attribution, provide
the official GPLv3 text missing from this archive, and avoid using the GSHHG
name for advertising without permission. This record is a conservative
research compliance decision, not legal advice. Project policy currently
authorizes no redistribution of the archive or modified geometry. See the
official
[GNU LGPL v3 text](https://www.gnu.org/licenses/lgpl-3.0.html).

## Access ledger

This decision stage:

- made zero network requests in the audit program;
- read the prerequisite manifest bytes;
- authenticated the local GSHHG ZIP bytes without opening an archive member;
- did not open L3 geometry or any eligible-land grid;
- computed no feature surface;
- constructed no predictor;
- fit no model;
- computed no prediction; and
- opened no external target, target-QA, LST, or final-evaluation output.

All portable-water-distance and multicity-continuation source, algorithm,
feature-name, predictor, model, protocol, and external-target permissions
remain closed.
