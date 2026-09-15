# LA measurement-support sensitivity

This is a one-shot measurement diagnostic, not a model experiment.

The locked contract is `experiment.toml`. It uses only the existing LA 2022–2024
forward-development dates and authenticated local Landsat cache. It reconstructs
the existing 4 K pixel-validity rule, forms non-overlapping adjacent date pairs,
and recomputes each date on the exact pixel intersection within a pair.

The fixed eligible-land denominator, original QA target, original scored sample,
and current model remain unchanged. Common-support targets are sensitivity
estimands only; they are not a replacement truth and cannot promote a model.

Run with:

```powershell
.\.venv\Scripts\python.exe experiments\la_measurement_support\run.py
```
