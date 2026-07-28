# Frozen 2025 Final-Evaluation Report

## Result in one sentence

On the one-time held-out 2025 test, the frozen M2 model predicted
neighborhood-scale daytime land-surface temperature with substantially lower
point-estimate error than the legal B1 baseline, but the prespecified uncertainty gate
failed because the 95% crossed-cluster interval still included no improvement.
The correct conclusion is therefore **promising held-out predictive signal,
without full protocol-level confirmation**.

## Immutable evaluation identity

- Final-test year: 2025
- Unit: Los Angeles census tract × Landsat physical-overpass date
- Consumption claim:
  `c174e0b26272dcb194a54ec4cdb468e18d0f64f8d04156681746a52361d1f01f`
- Completion commit:
  `4cc8a5536cf1055d42876577f8d9f6300c799176779a7ec89cd1d3ed819d77a0`
- Evaluated cohort: 15,116 tract-date rows, 15 independent usable dates,
  and 71 spatial blocks
- Frozen comparison: B1 legal baseline versus M2 primary model
- No model was refit, tuned, selected, or altered after 2025 values were
  opened.

## Primary held-out results

| Metric | B1 | M2 |
|---|---:|---:|
| Equal-date-weighted MAE (°C) | 3.1165 | 2.1650 |
| Pooled RMSE (°C) | 4.0574 | 2.7476 |
| Pooled out-of-sample R² | 0.2402 | 0.6516 |
| Equal-date-weighted within-date anomaly MAE (°C) | 1.8463 | 0.9783 |
| Median per-date Spearman | 0.4135 | 0.8447 |

M2 reduced equal-date-weighted MAE by 0.9516 °C, or 30.53%, relative to
B1. The 5,000-replicate paired crossed date-by-spatial-block bootstrap gave:

- absolute improvement 95% interval: -0.2407 to 2.5963 °C;
- relative improvement 95% interval: -10.13% to 58.46%;
- bootstrap fraction with improvement above zero: 0.9222.

The point-improvement and rank-correlation gates passed. The required lower
confidence-bound-above-zero gate did not pass, so the frozen overall protocol
success flag is false. Thresholds were not changed after observing this result.

## Date-to-date heterogeneity

M2 had lower absolute MAE on 12 of 15 dates, lower within-date anomaly MAE on
all 15 dates, and higher Spearman rank correlation on all 15 dates. Its absolute
MAE was worse on three dates. One unusually large B1 error on 2025-10-21
(12.0512 °C versus M2 at 1.2999 °C) contributed strongly to the aggregate point
improvement. This date heterogeneity explains why complete-date bootstrap
resampling produced a wide interval despite the favorable point estimate.

## Neighborhood hotspot endpoint

The relative hottest-20% endpoint was available on 10 independent dates
(10,672 tract-date rows):

| Metric | B1 | M2 |
|---|---:|---:|
| Mean per-date average precision | 0.3749 | 0.6954 |
| Mean per-date precision at exact k | 0.3843 | 0.6069 |
| Mean per-date recall at exact k | 0.3843 | 0.6069 |

This supports useful neighborhood ranking skill, but it does not override the
failed primary uncertainty gate.

## Prespecified subgroup checks

- Landsat 8, 9 dates: MAE improved from 3.5822 °C to 1.9768 °C.
- Landsat 9, 6 dates: MAE changed from 2.4179 °C to 2.4471 °C, so the point
  improvement was not uniform across sensors.
- Complete Sentinel support, 14,843 rows: MAE improved from 3.1132 °C to
  2.1632 °C.
- All five Sentinel features missing, 273 rows on only 2 dates: MAE changed
  from 3.3322 °C to 2.7533 °C. This stratum is too small for a strong claim.

The frozen universe contains 25,208 tract-date keys across 23 overpasses.
Targets were available on 16,378 rows; 15,116 rows on 15 dates entered the
formal evaluation. Eight dates failed the unchanged date-retention rule. The
relative-hotspot endpoint was available on only 10 of the 15 evaluated dates.

## Scientific interpretation

The frozen M2 configuration showed promising held-out predictive signal by
point estimate, without protocol-level confirmation. Its stronger
out-of-sample R², within-date anomaly accuracy, rank correlation, and hotspot
ranking all favor M2. Because B1 and M2 differ in both feature set and learning
algorithm, this comparison does not isolate the causal contribution of any one
feature family.

However, only 15 of the 23 frozen 2025 overpass dates entered the absolute-LST
evaluation cohort. Date-by-block uncertainty was therefore wide and crossed
zero improvement. The project must not claim that the prespecified final-test
success criterion was met, that every sensor benefited, or that the predictors
cause heat. Landsat LST is a clear-sky surface-heat hazard proxy, not air
temperature, personal exposure, illness, or mortality.

## Canonical evidence

All numbers above come directly from:

- `data/processed/final_test_2025/final_evaluation/model_metrics.csv`
- `data/processed/final_test_2025/final_evaluation/crossed_bootstrap.json`
- `data/processed/final_test_2025/final_evaluation/protocol_gates.csv`
- `data/processed/final_test_2025/final_evaluation/hotspot_summary.csv`
- `data/processed/final_test_2025/final_evaluation/sensor_summary.csv`
- `data/processed/final_test_2025/final_evaluation/sentinel_stratum_summary.csv`
- `manifests/final_test_2025/evaluation/EVALUATION_COMPLETE.json`
- `manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json`

The final directory contains the exact committed 21-file output set, including
the observed/predicted/residual map PDF and two diagnostic PNG figures. The
canonical evaluator was rerun after publication in completion-authentication
mode and returned the same completion commit without reopening or recomputing
the evaluation.

The separate read-only evidence ZIP has SHA-256
`61a853c3eeea3f1ae92bf7999f0fd057018797f70498fcd017d1394dbd621b51`.
