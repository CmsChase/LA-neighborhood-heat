# Documentation index

Use this page to choose the authoritative document for a question. Detailed
history is intentionally separated from the short project landing page.

## Start by purpose

| Question | Document |
|---|---|
| What was tested and why? | [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md) |
| How does data move through the project? | [PIPELINE_DIAGRAM.md](PIPELINE_DIAGRAM.md) |
| What is the current scientific result? | [PROJECT_STATUS.md](PROJECT_STATUS.md) and [the final report](../reports/FINAL_EVALUATION_REPORT.md) |
| What should a new contributor do next? | [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) |
| Why was a material choice made? | [DECISION_LOG.md](DECISION_LOG.md) |
| Where did each external dataset come from? | [DATA_MANIFEST.csv](DATA_MANIFEST.csv) |
| How is the public atlas authenticated? | [RESULTS_WEBSITE.md](RESULTS_WEBSITE.md) |
| Where are the paper, poster, and deck? | [PUBLICATION_MATERIALS.md](PUBLICATION_MATERIALS.md) |
| What is the active cross-city continuation? | [Active stage](../manifests/multicity/ACTIVE_STAGE.json) and [MULTICITY_GENERALIZATION_PROTOCOL.md](MULTICITY_GENERALIZATION_PROTOCOL.md) |
| How can I exercise the four-city interfaces without real targets? | [MULTICITY_SYNTHETIC_SMOKE.md](MULTICITY_SYNTHETIC_SMOKE.md) |
| How are the cross-city methods, gates, resume points, and evidence preserved? | [MULTICITY_METHODS_AND_EVIDENCE.md](MULTICITY_METHODS_AND_EVIDENCE.md) |
| What did the Phoenix geography pilot produce? | [Phoenix geography manifest](../manifests/multicity/cities/phoenix_az/geography/GEOGRAPHY.json) |
| What is the current Phoenix portable source footprint? | [Portable Phoenix source-footprint manifest](../manifests/multicity/cities/phoenix_az/source_footprints/PORTABLE_SOURCE_FOOTPRINT.json) |
| Why was the portable water-distance source initially deferred? | [Historical review](PORTABLE_WATER_DISTANCE_REVIEW.md) |
| What did the global shoreline geometry pilot find? | [GSHHG_GEOMETRY_PILOT_REPORT.md](GSHHG_GEOMETRY_PILOT_REPORT.md) |
| Why was the portable source-and-algorithm freeze deferred? | [PORTABLE_WATER_DISTANCE_FREEZE_DECISION.md](PORTABLE_WATER_DISTANCE_FREEZE_DECISION.md) |
| What exactly is fixed before the L3 hierarchy audit? | [GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.md](GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.md) |
| What did the completed L3 hierarchy audit find? | [GSHHG_L3_HIERARCHY_AUDIT_REPORT.md](GSHHG_L3_HIERARCHY_AUDIT_REPORT.md) |
| What source and water-distance algorithm are now frozen? | [WATER_DISTANCE_FREEZE_DECISION_V2.md](WATER_DISTANCE_FREEZE_DECISION_V2.md) |
| Why was the first portable predictor contract deferred? | [Historical V1 decision](PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.md) |
| What predictor contract is now authoritative? | [Locked portable predictor contract](../manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json) |

## Scientific contracts

- [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md): target, predictors, validation,
  metrics, success gates, and interpretation limits.
- [PHASE2_FEATURE_SPEC.md](PHASE2_FEATURE_SPEC.md): legal predictor families,
  timing cutoffs, units, and assembly gates.
- [MODEL_SELECTION_SPEC.md](MODEL_SELECTION_SPEC.md): frozen candidates,
  grouped tuning, objective, and tie-breaking.
- [LITERATURE_EVIDENCE.md](LITERATURE_EVIDENCE.md): source-to-claim map; not
  project-result evidence.
- [MULTICITY_GENERALIZATION_PROTOCOL.md](MULTICITY_GENERALIZATION_PROTOCOL.md):
  draft zero-shot transfer and uncertainty study; its external targets remain
  locked. The portable predictor contract and 46 feature names are frozen and
  predictor construction is authorized; protocol promotion, real fitting, and
  all target-value access remain locked.
- [MULTICITY_METHODS_AND_EVIDENCE.md](MULTICITY_METHODS_AND_EVIDENCE.md):
  implementation-facing gate sequence, fixed cohorts and metrics, synthetic
  rehearsal boundary, transfer/resume rules, and final evidence-package
  requirements.
- [PORTABLE_WATER_DISTANCE_REVIEW.md](PORTABLE_WATER_DISTANCE_REVIEW.md):
  target-blind source comparison, authenticated Census benchmark, cross-border
  semantic limitation, reviewed algorithm, and GSHHG geometry-pilot gate.
- [GSHHG_GEOMETRY_PILOT_REPORT.md](GSHHG_GEOMETRY_PILOT_REPORT.md):
  immutable V1 preregistration and failure, source-structure-only V2
  amendment, exact GSHHG archive audit, fixed-point Census comparison, and the
  remaining source/algorithm freeze gate.
- [PORTABLE_WATER_DISTANCE_FREEZE_DECISION.md](PORTABLE_WATER_DISTANCE_FREEZE_DECISION.md):
  authenticated deferred decision, the then-unresolved L3 hierarchy gap,
  license and claim boundaries, closed access ledger, and its next
  preregistration gate.
- [GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.md](GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.md):
  exact L3 direct-parent/exterior rule, deterministic probes, structural and
  numerical gates, fail-closed phase order, and still-closed permissions.
- [GSHHG_L3_HIERARCHY_AUDIT_REPORT.md](GSHHG_L3_HIERARCHY_AUDIT_REPORT.md):
  preserved V1 structural failure, one-character V2 amendment, authenticated
  structure and numerical results, scientific interpretation, and remaining
  source/algorithm freeze boundary.
- [WATER_DISTANCE_FREEZE_DECISION_V2.md](WATER_DISTANCE_FREEZE_DECISION_V2.md):
  authenticated GSHHG 2.3.7 source and point-distance-algorithm freeze,
  closed-data ledger, applicability limits, and mandatory planning-v8 gate.
- [PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.md](PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.md):
  authenticated deferred predictor-contract decision, four exact missing
  source-evidence facts, closed permissions, and the next safe staging gate.

## Execution and status

- [PROJECT_PLAN.md](PROJECT_PLAN.md): phase roadmap and completed deliverables.
- [PROJECT_STATUS.md](PROJECT_STATUS.md): readable scientific status.
- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md): concise operational state and the
  next resumable command.
- [EXPERIMENT_LOG.csv](EXPERIMENT_LOG.csv): experiment-level record.

## Authority and editing rules

When records disagree, authenticated manifests and generated provenance take
priority, followed by the evidence package, project handoff, generated reports,
and narrative summaries.

Detailed history belongs in the decision log and Git history, not in the
handoff. Do not move frozen scientific files or hand-edit generated results.
Update the handoff after a meaningful scientific or runtime change as required
by [the repository rules](../AGENTS.md).
