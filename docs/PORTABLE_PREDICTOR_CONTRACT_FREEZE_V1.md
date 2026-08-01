# Portable predictor contract freeze V1

Date: 2026-08-01

## Decision

The portable predictor source-and-calibration contract is **not frozen**.
The preregistered V1 decision is complete and deferred because all four
required evidence gaps were observed. Predictor construction remains closed.

This is a source-contract result, not a model-performance result. No predictor,
model, external target, target-QA value, prediction, or evaluation result was
opened or computed.

## Observed blockers

| Required evidence | V1 finding |
|---|---|
| Houston metadata-only source-footprint manifest | Absent from canonical v8 history |
| Chicago metadata-only source-footprint manifest | Absent from canonical v8 history |
| Phoenix NLCD land-cover/imperviousness source family | Absent from the authenticated Phoenix pilot manifest |
| Phoenix terrain content and raster schema contract | Content hash and raster schema were not frozen |

The outcome is therefore
`defer_until_cross_city_source_footprints_and_static_source_contracts_complete`.
The next safe stage is
`stage_missing_portable_predictor_source_evidence_before_v2_freeze`.

## What remains locked

- GSHHG 2.3.7 and the audited L1/L2/L3 point-distance algorithm remain frozen.
- Water-distance tract aggregation and feature names remain unfrozen.
- The complete portable predictor source/calibration contract remains
  unfrozen.
- Predictor construction, protocol promotion, model fitting, external target
  access, one-time evaluation, and operational forecasting remain closed.

## Access boundary

The decision read only exact tracked JSON/config/code blobs and local Git
history. It made zero network requests and opened no source archive or payload,
geometry, raster/vector values, eligible-land grid, predictor values, model,
target, prediction, or final result.

## Authenticated identities

- implementation commit:
  `622b03cadbc94af0ecf667ce4602913b36fb0d74`;
- canonical planning-v8 publication:
  `35b6015a3a9a410b42752d2e50a7599e18bf2563`;
- planning-v8 file: 27,837 bytes, SHA-256
  `8ad87ecdfd7d6e232d574662187dc91977bef0c177fd673cb96305469f44d948`,
  internal commit
  `d2a3d95bc4935b3aa1c46861abd2420d67959db0de18c3689b6d5994e64800dd`;
- V1 configuration: 5,794 bytes, SHA-256
  `536364a9a44b0fbf04a7880f2053cb2b5ae6d9badbe12861236773d239362c62`;
- append-only V1 terminal publication:
  `47a626f6fc0a6577148cc731bb00d21f5387f20a`;
- V1 terminal: 12,934 bytes, SHA-256
  `794e85c2ea5ad76b84c5e6e7be0999bc5939ab85d9dc7df773406f9802fe6127`,
  internal commit
  `75b368d7f71c7af5af10317f996595f629e4dacdbbf62b3dc79c3ac0c5eb3e3d`.

Both planning v8 and the V1 terminal passed publication-aware `--check-only`
authentication from clean synchronized `main`. The v8 publication changed
only `PLAN_READINESS.json`; its direct-child V1 publication added only the
canonical terminal.

## Next step

Create a separate, target-blind authorization and implementation for the
missing source evidence. Any new network request or download must first be
bound by an exact preregistration. Do not build predictors or unlock external
targets while filling these metadata/source-contract gaps.
