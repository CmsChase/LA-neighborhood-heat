# M3 Four-City Blind-Evaluation Report

## Result in one sentence

The frozen M3 model did **not** pass its one-time Seattle–Denver–Atlanta–Miami
confirmation test: its equal-city, equal-date mean absolute error (MAE) was
5.8343 °C versus 3.8032 °C for the legal B1 baseline, a relative change of
-53.40% improvement (that is, 53.40% worse), with a prespecified 95% crossed
bootstrap interval from -75.81% to -32.61%. The protocol terminal state is
`inconclusive_sample_size` because Miami contributed only four usable dates,
but the observed point estimate and entire interval also disfavor M3.

## Immutable evaluation identity

- Blind cities: Seattle, Denver, Atlanta, and Miami.
- Evaluated cohort: 9,502 tract-date rows, 66 usable city-dates, and 68 spatial
  blocks.
- Frozen comparison: source-selected M3 versus the legal B1 baseline.
- Bootstrap: 10,000 city-stratified crossed complete-date × 5 km spatial-block
  replicates, seed `20260816`, with cities weighted equally.
- Prediction completion commit:
  `295ccca0ea0239cf6eb5633b736a7565abbac3cc47992bae6bcbc9ba4d6f74ac`.
- Target completion commit:
  `55839671f2b28e5c725c5d9601e7d0e2d6ae8a916d7b9880c94e65b2fd99e901`.
- Evaluation completion commit:
  `6574750d633b580afd9fedb4b06cf99cd78c414956450a92bb3ea9e99113559f`.
- Terminal certification commit:
  `383742cb17674c508e8a7dfe853caa163ab2bd8d0816e150e6f4d966e2334a26`.
- Predictions were committed before target access. No model was refit,
  recalibrated, retuned, or selected after blind targets were opened.

## Primary result

| Metric | B1 | M3 |
|---|---:|---:|
| Equal-city, equal-date MAE (°C) | 3.8032 | 5.8343 |
| Relative MAE improvement | — | -53.40% |

The 95% interval for relative improvement was -75.81% to -32.61%. In all
10,000 bootstrap replicates, neither improvement above zero nor improvement of
at least 10% occurred. All prespecified point-prediction gates failed:

- the total/per-city sample-size gate;
- at least 10% relative improvement;
- a bootstrap lower bound above zero;
- no city-level point degradation; and
- overall point-prediction success.

## City-level results

| City | Usable dates | Rows | B1 MAE (°C) | M3 MAE (°C) | M3 minus B1 (°C) | M3 interval coverage |
|---|---:|---:|---:|---:|---:|---:|
| Seattle | 25 | 3,601 | 3.4303 | 1.6696 | -1.7608 | 99.94% |
| Denver | 24 | 3,674 | 3.8993 | 13.2004 | +9.3011 | 30.38% |
| Atlanta | 13 | 1,873 | 4.3150 | 5.2466 | +0.9316 | 90.66% |
| Miami | 4 | 354 | 3.5683 | 3.2206 | -0.3476 | 98.31% |

M3 improved over B1 in Seattle and Miami but degraded in Denver and Atlanta.
The Denver error increase dominates the aggregate failure. Miami's four usable
dates are below the frozen minimum of eight dates per city, so the indivisible
four-city claim cannot satisfy its sample-size gate.

## Reliability and abstention

M3's overall interval coverage was 71.15%. The frozen risk rule retained 100%
of predictions, so accepted-set MAE equaled all-prediction MAE at 6.8268 °C and
accepted-set improvement was 0%. The reliability gate therefore failed. The
mean interval width was 20.8130 °C in each city, while observed coverage varied
substantially, most notably falling to 30.38% in Denver.

## Secondary comparison

The prespecified secondary M2 legacy model had an equal-city, equal-date MAE of
4.4056 °C. This secondary result cannot rescue a failed M3-versus-B1 primary
claim and is not a new confirmation test.

## Scientific interpretation

The experiment provides no support for claiming that the frozen M3 model
generalizes better than B1 across the four blind cities. Although the formal
terminal label emphasizes insufficient Miami dates, the negative point
estimate, negative bootstrap interval, and degradation in two cities are
independent reasons not to claim success.

These four cities and their opened targets must not be reused as a new blind
test or used for confirmatory retuning. Any future M4 development must be
clearly labeled post hoc, use source/development data for model choices, and
reserve a new, untouched cohort for any future confirmation claim.

Landsat land-surface temperature is a clear-sky surface-heat proxy, not air
temperature, personal exposure, illness, or mortality. The results describe
predictive performance and do not establish causal effects of any predictor.

## Canonical evidence

All reported values come from the frozen evaluation outputs and authenticated
completion records:

- `data/processed/multicity/m3_blind_evaluation_v1/summary_v2.json`
- `data/processed/multicity/m3_blind_evaluation_v1/city_metrics.parquet`
- `data/processed/multicity/m3_blind_evaluation_v1/crossed_bootstrap.json`
- `data/processed/multicity/m3_blind_evaluation_v1/risk_coverage.parquet`
- `manifests/multicity/next_experiment/blind_evaluation_v1/M3_BLIND_EVALUATION_COMPLETE.json`
- `manifests/multicity/next_experiment/blind_evaluation_v1/M3_BLIND_EVALUATION_TERMINAL_COMPLETE.json`

The terminal certification authenticated the bytes and semantics of every
parent output. `summary_v2.json` only removes two contradictory legacy
three-city compatibility fields; it does not change any metric, gate, bootstrap
result, or conclusion.
