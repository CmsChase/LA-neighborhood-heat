# M3 relative-temperature review

This no-refit analysis asks whether the already-fixed M3 spatial component is
useful for neighborhood differences within the same city and date, even though
M3 failed at absolute cross-city temperature.

It recomputes a common set of metrics from existing row-level predictions:

- source evidence: whole-city LOSO predictions for LA, Phoenix, Houston, Chicago;
- historical stress evidence: already-opened Seattle, Denver, Atlanta, Miami;
- model comparison: fixed M3 against B1;
- outcomes: within-date anomaly MAE, Spearman correlation, and top-20% hotspot
  average precision/recall.

The two city groups are reported separately. Their combined eight-city summary
is descriptive and is not a new blind or confirmatory result.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe experiments\m3_relative_temperature\run.py
```

The runner performs no fitting, download, or network access. Generated outputs
go to the ignored `exports/M3_RELATIVE_TEMPERATURE_REVIEW/` directory.

After the fixed contract was committed, its source-only temporal stability
audit was run with:

```powershell
.\.venv\Scripts\python.exe experiments\m3_relative_temperature\year_stability.py
```

This runner fits the single fixed M3-relative model and B1 in 18 leave-one-year-
out folds. Fold outputs are cached under the ignored
`exports/M3_RELATIVE_TEMPERATURE_YEAR_STABILITY/` directory. It performs no
network access, new-city acquisition, or model selection.

After that audit passed, the fixed development model was fitted with:

```powershell
.\.venv\Scripts\python.exe experiments\m3_relative_temperature\fit_final.py
```

The ignored model bundle and source predictions are written under
`exports/M3_RELATIVE_TEMPERATURE_MODEL_V1/`. The tracked completion record is
`manifests/multicity/reviews/m3_relative_temperature/M3_RELATIVE_DEVELOPMENT_MODEL_COMPLETE.json`.
The fit script records input and artifact hashes and verifies predictions after
reloading the serialized model; it does not report training-set performance.
