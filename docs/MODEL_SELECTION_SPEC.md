# Frozen model-selection specification

## Status and scope

The candidate hyperparameters and selection rule were frozen before any model
was fitted and before any target or performance score was read for model
selection. The authoritative configuration is `configs/model_selection.toml`.
Its canonical semantic SHA-256 is
`98f0429f3f2daa6f61f2bf260ff284f7fe08cc52487ee6f11abcab05b98fcec0`;
the loader fails closed if the semantic content changes.
The target- and score-blind command
`python scripts/audit_model_selection_freeze.py` writes the independent commit
marker `manifests/model_selection/model_selection_freeze.json`. That marker
records the exact 31 candidates, configuration/file hashes, pipeline fingerprint,
2025 lock, and empty target/score input lists. Its current canonical commit is
`4d8c2bd37be67f9f46d89d1dec8d5ed0aab196b24b43f9745ff730f040f2a6cd`.

This is a historical-hindcast development contract. Calendar year 2025 remains
locked (`unlock_final_test = false`) and cannot enter preprocessing, tuning,
selection, thresholding, or model comparison.

## Nested validation and objective

For each outer fold, inner validation leaves one remaining calendar year out at
a time and uses only that outer fold's training rows. Imputation and scaling are
fit again on each inner-training partition. Outer-test and purged rows never
enter inner training or inner validation. After selection, the complete pipeline
is refit once on the full outer-training partition.

Each candidate produces one tract-level MAE summary per independent inner-OOF
validation date. The candidate objective is the arithmetic mean of those date
MAEs after stitching all inner validation years. Thus every physical Landsat
overpass date has equal weight; tract-row counts and inner-year sizes do not
change the objective.

The selector requires the caller to supply the exact expected validation-date
set from the grouped fold manifest. Every candidate must match that set exactly;
matching one another while jointly omitting a date is a hard failure.

The selected candidate has the minimum stitched date-macro MAE. Scores within
`1e-12 °C` of the numerical minimum are treated as tied. A tie is resolved by
the frozen `complexity_rank`, then by `candidate_id`. This tiny tolerance handles
floating-point equality only; it is not a one-standard-error rule and cannot
promote a meaningfully worse score.

## Frozen candidate grids

| Model | Candidate set | Count | Simpler-first complexity order |
|---|---|---:|---|
| B0 calendar baseline | No tunable parameter | 1 | Fixed |
| B1 weather + calendar Ridge | `alpha ∈ {100, 10, 1, 0.1, 0.01}` | 5 | Larger `alpha` first |
| B2 static + calendar Ridge | `alpha ∈ {100, 10, 1, 0.1, 0.01}` | 5 | Larger `alpha` first |
| M1 all-feature Elastic Net | `alpha ∈ {1, 0.1, 0.01, 0.001}` × `l1_ratio ∈ {0.9, 0.5, 0.1}` | 12 | Larger `alpha`, then larger `l1_ratio` |
| M2 all-feature histogram gradient boosting | `max_leaf_nodes ∈ {15, 31}` × `min_samples_leaf ∈ {50, 20}` × `l2_regularization ∈ {1, 0}`; learning rate `0.05`, 300 iterations | 8 | Fewer leaves, more minimum samples, then more L2 |

The shared random seed is `20260719`. M2 retains the separate modeling
contract's absolute-error loss and `early_stopping = false`, so it does not
create an internal random row split. Candidate grids do not alter the registered
feature sets, fold-local median imputation, date-balanced training weights, or
the special one-response-per-training-date B0 design.

## Fail-closed audit conditions

Selection is rejected if a candidate is missing, an unregistered candidate is
present, candidate-date rows are duplicated, candidates cover different dates,
candidate dates differ from the exact expected fold-manifest dates, MAE values
are missing/nonfinite/negative, dates are not timezone-naive civil midnights,
the supplied inner years differ from the expected grouped folds, or any date is
from 2025 or later. The selection input is exactly one MAE value per candidate
and independent validation date; raw target rows are outside this module's
input contract.
