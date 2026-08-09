# Repository guidance

This repository contains the surface-heat research pipeline and its public
atlas. These rules apply to the whole repository.

## Start and handoff

- Read `docs/PROJECT_HANDOFF.md` before changing the project.
- Check `git status` and the active runtime status named in the handoff.
- Update the handoff at a meaningful milestone, failure, or session transfer.
  Do not append a diary entry for every command or repeat unchanged history.
- Never place credentials, bearer tokens, signed URLs, or cookies in tracked
  files.

## Delivery principle

Prefer the shortest correct path that advances the research.

- Keep one active workflow in `manifests/multicity/ACTIVE_STAGE.json`.
- Do not create a new V19/V20-style transition module for each technical fix.
  Historical V7–V18 files remain provenance only.
- Avoid repeated authentication, ancestry checks, direct-child commit rules,
  and duplicate validation when they do not protect a scientific result.
- During normal implementation, run focused tests for the behavior changed and
  one lint pass over touched files.
- Run the full suite only for scientific-contract changes, broad refactors, or
  release checkpoints.
- Prefer resumable programs with visible status for genuinely long computation;
  do not build a controller for a short task.

This simplification does not relax the scientific boundaries below.

## Scientific objective

Predict QA-filtered daytime Landsat land-surface temperature at census-tract ×
overpass-date resolution from public weather, land-use, geography, and lagged
non-thermal satellite features. LST is a surface-heat hazard proxy, not human
heat exposure or a health outcome.

## Scientific boundaries

- Never randomly split tract-date rows. Use whole-date, whole-year, contiguous
  spatial-block, or joint spatiotemporal splits.
- Do not use Landsat thermal values, target-derived statistics, same-scene
  optical bands, future observations, or tract identifiers as predictors.
- Every satellite composite must end before its target date.
- Fit preprocessing and model selection only on the training fold.
- Count independent dates and spatial blocks in every performance report.
- Keep each tract's eligible-land denominator invariant across dates.
- Treat adjacent WRS contributors from one physical overpass as one date.
- Interpret feature importance as association, not causation.
- Call the primary analysis a historical hindcast, not an operational forecast.
- The one-time 2025 evaluation is complete. Do not retune against it or create a
  second claim from the same holdout.
- External-city targets remain sealed until the active stage explicitly changes
  that permission.

## Project structure

- Reusable Python code: `src/la_heat/`
- Command-line entry points: `scripts/`
- Public website: `atlas/`
- Configuration: `configs/`
- Machine-readable provenance: `manifests/`
- Human documentation: `docs/`
- Tests: `tests/`

Generated data stays under ignored `data/`, `exports/`, or runtime directories.
Do not hand-edit generated scientific tables, figures, compact website JSON, or
authenticated evidence manifests.

## Definition of done

A normal code change is done when the intended behavior works, its focused
regression test passes, touched files lint, and the handoff states the new
resume point. A scientific release additionally requires the relevant full
verification and generated provenance.
