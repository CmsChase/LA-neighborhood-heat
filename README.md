# Los Angeles Neighborhood Surface Heat

This repository tests whether public weather, land-use, topographic, and
lagged non-thermal satellite features can predict daytime land-surface
temperature (LST) across City of Los Angeles census tracts.

LST is treated as a **surface-heat hazard proxy**. It is not air temperature,
personal heat exposure, a health outcome, or evidence of a causal effect.

## Final held-out result

The primary model (M2) was evaluated once on the predeclared 2025 final test
after all modeling choices were frozen.

| Held-out 2025 result | B1 baseline | M2 model |
|---|---:|---:|
| Equal-date-weighted MAE | 3.1165 °C | 2.1650 °C |
| Relative MAE change | — | 30.53% lower |
| Median per-date Spearman | — | 0.8447 |

The point estimate is promising, but the crossed date/spatial-block 95%
interval for relative MAE improvement was **−10.13% to 58.46%**. Because that
interval crosses zero, the prespecified uncertainty gate did not pass and
`overall_protocol_success` is false. The result must not be presented as a
confirmed general improvement.

Explore the 15 usable held-out dates in the
[interactive result atlas](https://cmschase.github.io/LA-surface-heat-atlas/).
The atlas includes a zoomable tract map, GEOID search, and a complete
date-by-date record for each selected tract.

## Cross-city continuation

A separate continuation is now in draft planning. It asks whether a fixed
model trained only with Los Angeles labels transfers to Phoenix, Houston, and
Chicago, and whether calibrated uncertainty can identify predictions that
should be withheld. The three external cities remain target-sealed; no new LST
or target-QA value has been opened.

Start with the
[cross-city protocol](docs/MULTICITY_GENERALIZATION_PROTOCOL.md) and the
[planning readiness record](manifests/multicity/PLAN_READINESS.json). This
work uses isolated `multicity/` paths and cannot modify the completed 2025
transaction.

The first target-blind engineering pilot is complete: the generic Census
place/tract adapter discovered a 375-tract primary Phoenix universe from the
fixed 2020 incorporated-place boundary. The authenticated
[Phoenix geography manifest](manifests/multicity/cities/phoenix_az/geography/GEOGRAPHY.json)
records the exact source responses, geometry hashes, selection audit, and the
fact that no external LST or target-QA value was read. The source remains a
pilot snapshot rather than a locked confirmatory input.

The Phoenix source-footprint pilot is also complete at metadata-only scope.
Its authenticated
[source-footprint manifest](manifests/multicity/cities/phoenix_az/source_footprints/SOURCE_FOOTPRINTS.json)
records three Landsat WRS contributors, four Sentinel MGRS tiles, 1,461
positive-intersection Daymet candidate cells with a one-cell halo window, six
Daymet granules, and two terrain tiles verified by `HEAD` only. The STAC
queries returned 67 Landsat and 494 Sentinel items while excluding item assets
and item links. No STAC asset href, signed request, raster GET/payload,
target/QA value, predictor, prediction, or model was opened or constructed.
This remains a pilot metadata snapshot, not a source or protocol lock.

The target-blind
[portable water-distance review](docs/PORTABLE_WATER_DISTANCE_REVIEW.md) is
now complete. It reauthenticated the existing Census 2019 coastline as the
best U.S.-only reproducibility benchmark, but did not freeze it because that
source omits the Mexican Gulf of California relevant to Phoenix. The next
safe task is a source-only GSHHG geometry comparison that reads no target/QA
values and constructs no predictor. Any later source freeze remains a separate
gate.

## Six ways into the project

| If you want to… | Start here |
|---|---|
| 1. See the result | [Final evaluation report](reports/FINAL_EVALUATION_REPORT.md) and [interactive atlas](https://cmschase.github.io/LA-surface-heat-atlas/) |
| 2. Understand the study | [Research protocol](docs/RESEARCH_PROTOCOL.md) and [pipeline diagram](docs/PIPELINE_DIAGRAM.md) |
| 3. Verify the final evidence | [Evidence attestation](manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json) and [manifest guide](manifests/README.md) |
| 4. Reproduce development work | [Script map](scripts/README.md), [configuration map](configs/README.md), and [test guide](tests/README.md) |
| 5. Continue the project safely | [Mandatory handoff](docs/PROJECT_HANDOFF.md) and [repository rules](AGENTS.md) |
| 6. Use the communication materials | [Publication guide](docs/PUBLICATION_MATERIALS.md) and [website guide](docs/RESULTS_WEBSITE.md) |

The [documentation index](docs/README.md) provides the complete role-based
map.

## Locked research design

- Unit: 2020 Census tract × Landsat physical-overpass date.
- Study area: City of Los Angeles.
- Warm season: May through October.
- Development period: 2020–2024.
- One-time held-out final test: 2025.
- Target: QA-filtered tract-median Landsat 8/9 daytime LST in °C.
- Predictors: 18 static land-use/geography, 2 calendar, 21 lagged Daymet, and
  5 lagged Sentinel-2 features.
- Primary metric: equal-date-weighted MAE.
- Validation: whole-date, whole-year, contiguous spatial-block, and joint
  spatiotemporal holdouts—never random tract-date splits.
- Prediction origin: 00:00 local time on the target date; dynamic observed
  inputs end on target day minus one.

The development table contains 63,403 legal rows across 65 independent dates,
1,096 tracts, and 71 spatial blocks. The held-out display contains 15,116
evaluated tract-date rows across 15 usable 2025 dates.

## Scientific boundaries

- Do not describe this historical hindcast as an operational weather forecast.
- Do not treat tract-date rows as independent samples.
- Do not interpret feature importance as causation.
- Never use Landsat thermal values, target-derived statistics, same-scene
  optical bands, future observations, raw tract IDs, or target-day observed
  weather as primary-model predictors.
- Every Sentinel composite ends before its target date.
- Preprocessing and model selection are fit within the appropriate training
  folds only.
- The one-time 2025 evaluation is complete. Do not create another claim,
  retune after viewing results, or rerun the authorization sequence.

## Repository map

```text
configs/                 versioned configuration; see configs/README.md
data/                    ignored raw, interim, and processed data products
docs/                    protocol, provenance, decisions, and navigation
manifests/               frozen machine-readable audit and state records
reports/                 generated scientific reports, figures, and tables
scripts/                 thin command-line entry points grouped by workflow
src/la_heat/             reusable Python implementation
tests/                   leakage, schema, provenance, and engineering tests
portable_templates/      Windows transfer and remote-run templates
tools/                   static dashboard front ends
exports/                 ignored local evidence and publication packages
website-github-pages/    ignored local clone of the separate public site repo
```

Bulk data and export packages are intentionally untracked. Their authenticated
hashes and provenance are recorded in the manifests and documentation.

## Safe verification

From the repository root, install the development environment and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
```

Verify an existing unpacked final-evaluation evidence package without
recomputing the final result:

```powershell
.\.venv\Scripts\python scripts\verify_final_evaluation_evidence.py
```

Verify the existing website display export:

```powershell
.\.venv\Scripts\python scripts\build_website_data.py --verify-only `
  --output-dir website-github-pages\public\data
```

These commands are checks. The final-evaluation preparation, authorization,
claim, and value-opening workflow is a completed historical transaction and
must not be started again.

## Output authority

Use this order when records disagree:

1. authenticated manifests and generated provenance;
2. the completed final-evaluation evidence package;
3. the mandatory project handoff;
4. generated reports and communication materials;
5. narrative status summaries.

Never hand-edit generated analysis tables, figures, compact website JSON, or
byte-authenticated manifests. Regenerate only through the documented scripts
and preserve every frozen path and checksum.

## Main outputs

- [Final evaluation report](reports/FINAL_EVALUATION_REPORT.md)
- [Development report](reports/DEVELOPMENT_REPORT.md)
- [Read-only evidence package documentation](docs/PROJECT_HANDOFF.md)
- [Interactive results website documentation](docs/RESULTS_WEBSITE.md)
- [Research paper, poster, and defense-deck documentation](docs/PUBLICATION_MATERIALS.md)
- [Data-source manifest](docs/DATA_MANIFEST.csv)
- [Scientific decision log](docs/DECISION_LOG.md)

Before changing anything, read [AGENTS.md](AGENTS.md) and the complete
[project handoff](docs/PROJECT_HANDOFF.md). They contain the current Git
checkpoint, one-time-evaluation state, immutable paths, and required validation
steps.
