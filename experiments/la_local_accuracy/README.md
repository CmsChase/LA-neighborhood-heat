# Los Angeles local forward accuracy experiment

This bounded post-hoc development experiment uses only Los Angeles QA4K
historical observations from 2020–2024. It does not read LA 2025 or any opened
external-city target. It asks whether the stable neighborhood pattern and the
date-varying lagged Sentinel signal should be modeled differently when the goal
is a future historical date in the same observed city.

The candidate set is fixed before fitting:

- `relative_current_23`: the existing 18 static + 5 lagged-Sentinel HGB;
- `relative_stable_18`: the same HGB using only the 18 static features;
- `relative_stable_blend`: a fixed 1:1 average of the two centered outputs.

Zero anomaly and each tract's training-period median anomaly are diagnostics,
not selectable models. The history baseline uses target values and tract IDs
only inside the training years and never exposes either to a candidate model.

Outer tests are 2022, 2023, and 2024. For each test year, candidate selection
uses the immediately preceding complete year and trains only on still earlier
years. The selected candidate is then refit on every year strictly before the
outer test. Preprocessing and dynamic-feature imputation are fit separately in
each training fold. No tract-date row is randomly split.

Promotion requires at least 5% lower equal-date relative MAE than the refit
existing model, a positive paired date × 5 km block bootstrap lower bound, no
test-year degradation over 0.05 C, no support-centered or anchored-absolute MAE
degradation over 0.05 C, and no hotspot-recall loss over 0.02. Failure leaves
the current model in place and informs one next experiment; it does not imply a
general accuracy ceiling.

Run `.venv\Scripts\python.exe experiments\la_local_accuracy\run.py`, then
`.venv\Scripts\python.exe experiments\la_local_accuracy\report.py`.
