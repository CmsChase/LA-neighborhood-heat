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
