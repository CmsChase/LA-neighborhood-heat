# External-result post-hoc QA audit

> **NON-CONFIRMATORY / POST-HOC.** This audit was selected after the frozen
> external result was observed. It does not replace the formal result, change
> any gate, refit or recalibrate a model, or recompute a confidence interval.

Formal result state: `inconclusive_sample_size`. The formal
evaluation files remain unchanged and authoritative.

## Houston 2025-07-25 observation

| Quantity | Observed |
|---|---:|
| Available tracts | 400 / 651 (61.4%) |
| Target LST median | 28.828 °C |
| Target LST p05–p95 | -13.406 to 47.294 °C |
| Target LST standard deviation | 18.703 °C |
| Available tracts below 0 °C | 43 (10.8%) |
| Median tract ST_QA uncertainty | 4.050 K |
| Median tract p90 ST_QA uncertainty | 4.285 K |
| M2 MAE on this date | 22.199 °C |
| M2 interval coverage / mean width | 16.2% / 10.241 °C |

Relative to the other usable Houston dates, the date-level target median was
13.046 °C lower, target dispersion
was 16.566 °C higher, and median Landsat
ST_QA uncertainty was 1.500 K higher.
`ST_QA` is observation-label uncertainty metadata; it is not the M2 prediction
interval. These diagnostics identify a data-quality concern but do not establish
its physical or processing cause.

## Per-city date support

| City | Planned dates | Usable dates | Available targets | Usable targets |
|---|---:|---:|---:|---:|
| phoenix_az | 22 | 21 | 7585 / 8250 | 7585 |
| houston_tx | 21 | 4 | 2763 / 13671 | 2165 |
| chicago_il | 21 | 3 | 2593 / 16380 | 1457 |

The machine-readable JSON contains all 64 city-date support summaries. No
tract-level target or prediction values are included in either audit artifact.

### Houston date-level support

| Date | Available | Usable | Median LST | p05–p95 LST | Median ST_QA |
|---|---:|:---:|---:|---:|---:|
| 2025-05-14 | 644 / 651 | yes | 41.905 °C | 35.922–44.862 °C | 2.320 K |
| 2025-05-22 | 2 / 651 | no | 48.013 °C | 47.726–48.300 °C | 3.530 K |
| 2025-06-07 | 34 / 651 | no | 47.168 °C | 41.756–50.296 °C | 3.665 K |
| 2025-06-15 | 38 / 651 | no | 47.312 °C | 35.939–51.569 °C | 3.915 K |
| 2025-06-23 | 20 / 651 | no | 51.165 °C | 45.157–54.481 °C | 3.845 K |
| 2025-07-01 | 158 / 651 | no | 43.880 °C | 38.463–47.407 °C | 3.540 K |
| 2025-07-09 | 17 / 651 | no | 47.127 °C | 31.615–49.644 °C | 3.830 K |
| 2025-07-17 | 0 / 651 | no | — °C | —–— °C | — K |
| 2025-07-25 | 400 / 651 | yes | 28.828 °C | -13.406–47.294 °C | 4.050 K |
| 2025-08-02 | 0 / 651 | no | — °C | —–— °C | — K |
| 2025-08-10 | 87 / 651 | no | 46.566 °C | 42.116–51.334 °C | 3.480 K |
| 2025-08-18 | 59 / 651 | no | 47.811 °C | 39.275–51.631 °C | 3.740 K |
| 2025-08-26 | 23 / 651 | no | 41.819 °C | 36.513–45.884 °C | 3.730 K |
| 2025-09-03 | 10 / 651 | no | 45.276 °C | 42.667–47.509 °C | 3.675 K |
| 2025-09-11 | 48 / 651 | no | 41.786 °C | 38.708–44.580 °C | 3.520 K |
| 2025-09-19 | 2 / 651 | no | 37.782 °C | 34.506–41.058 °C | 3.450 K |
| 2025-09-27 | 32 / 651 | no | 40.069 °C | 34.104–43.104 °C | 3.025 K |
| 2025-10-05 | 621 / 651 | yes | 41.873 °C | 37.423–44.401 °C | 2.550 K |
| 2025-10-13 | 25 / 651 | no | 39.117 °C | 35.098–40.955 °C | 3.100 K |
| 2025-10-21 | 43 / 651 | no | 38.332 °C | 36.403–41.855 °C | 3.680 K |
| 2025-10-29 | 500 / 651 | yes | 25.830 °C | 23.258–27.651 °C | 2.640 K |

## Descriptive leave-one-date-out sensitivity

| Dataset | Rows | City-dates | B1 equal-city/date MAE | M2 equal-city/date MAE | Relative improvement |
|---|---:|---:|---:|---:|---:|
| Frozen formal usable dates | 11207 | 28 | 9.738 °C | 6.922 °C | 28.92% |
| Excluding Houston 2025-07-25 (post-hoc) | 10807 | 27 | 8.523 °C | 5.394 °C | 36.70% |

The descriptive relative-improvement point estimate changes by 7.79 percentage
points. No bootstrap interval or formal gate was recalculated. The frozen
three-city result—including its sample-size and city-degradation failures—
remains the only confirmatory result.

## Reproducibility boundary

Audit algorithm: `multicity-external-posthoc-qa-v1`
Audit commit: `3b6dfc2c41bcc0b61c6667df4b5413ec1838dc0cfc5d277fba33bfaa1574fe63`
Formal evaluation commit: `fda881ca15442257acaaff4b563dbc1d46e5c2002080a8423e8ca703e6c338de`
