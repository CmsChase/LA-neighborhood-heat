# LA coarse spatial-residual development

This bounded experiment follows the completed LA strict-forward accuracy study.
It is iterative development evidence because the 2022--2024 results informed
the design; it is not a new independent confirmation.

For each outer training window, the experiment first constructs residuals only
from strict-forward out-of-fold predictions inside that window. It checks
whether tract bias persists across OOF years and whether each OOF date has
same-direction local residual structure under a fixed six-nearest-neighbor diagnostic.
Outer-test targets never select a spatial scale or regularization value.

The sole candidate keeps the existing 23-feature relative model unchanged and
adds a five-term, city-scale quadratic basis of public LA tract centroids. A
fixed ridge correction is trained on the forward OOF residuals. Raw coordinates
remain forbidden in the historical/global model contract; this explicit local
development exception applies only to the residual correction and makes no
cross-city transfer claim. Tract IDs, historical target summaries, neighbors'
observed temperatures, LA 2025, and external-city targets are not predictors.

Run with:

```powershell
python experiments/la_spatial_residual/run.py
python experiments/la_spatial_residual/report.py
```
