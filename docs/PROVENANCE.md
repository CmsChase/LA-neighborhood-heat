# Provenance and scientific gates

This project uses machine-readable records to prevent accidental leakage,
post-hoc tuning, and silent changes to completed scientific evidence. The
system is deliberately stricter than a typical application repository, but a
reader does not need to inspect every manifest to use the Atlas or run tests.

## Four terms used in the repository

| Term | Plain-language meaning |
|---|---|
| **Protocol lock** | Freezes the scientific question, cohorts, metrics, and decision rules before protected outcomes are used |
| **Authorization** | Defines the exact next action, inputs, code identity, and data boundary |
| **Completion** | Records what was produced and verifies counts, schemas, hashes, and safety assertions |
| **Commit fingerprint** | A SHA-256 identity for a record or artifact, used to detect later byte changes |

`manifests/multicity/ACTIVE_STAGE.json` is the single current control record.
Older manifests remain immutable historical evidence; their presence does not
mean those workflows should be rerun.

## Normal gated sequence

1. Freeze the protocol or candidate space.
2. Create a narrowly scoped authorization before opening protected values.
3. Run a resumable task inside that scope.
4. Write and authenticate a completion record.
5. Advance the active stage only after the completion checks pass.

For blind evaluation, predictions must be committed before target values are
authorized. That ordering is the central reason for the provenance layer.

## Evidence versus operational state

- `manifests/` contains compact, tracked scientific control records.
- `reports/` and `atlas/public/evidence/` contain released aggregate evidence.
- `data/` and `exports/` contain ignored generated data, caches, queues, and
  transfer packages.
- Runtime SQLite databases and status files support recovery but are not public
  scientific results.
- Historical `.cmd` launchers are operational provenance, not the public API.

## Current boundary

The source-only M3 selection is complete. Seattle, Denver, Atlanta, and Miami
predictor support and metadata are frozen, but their Landsat thermal, QA, and
target values remain sealed. Predictor acquisition and prediction must finish
before a separate one-time target authorization can exist.

For the detailed chronological record, see `docs/PROJECT_HANDOFF.md` and
`docs/DECISION_LOG.md`. For the public-facing state, start with the status table
in the repository `README.md`.
