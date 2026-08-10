# Four-city synthetic smoke run

This is an engineering check, not a scientific result. It creates all labels and
predictors deterministically in memory and reads only the frozen 46-feature/model
contract. It never reads a real predictor table, Landsat target, target QA value,
external result, `ACTIVE_STAGE.json`, or any canonical data product.

Run it from the repository root:

```powershell
.\.venv\Scripts\python scripts\run_multicity_synthetic_smoke.py
```

The default destination is `.tmp/multicity_synthetic_smoke`; a different
caller-owned directory can be supplied with `--output-directory`. Inside this
repository the runner accepts only `.tmp/` and rejects every other project
path, including hidden control folders. A path outside the repository is also
allowed.

The smoke run exercises two paths:

1. the frozen Los Angeles 2020-2023 fit, Los Angeles 2024 conformal calibration,
   and target-blind Phoenix/Houston/Chicago 2025 prediction interfaces; and
2. four mechanical leave-one-city-out folds, each using fresh B1/M2 preprocessing
   and training on exactly the other three synthetic cities.

Every CSV contains `artifact_scope=synthetic_smoke_only` and
`scientific_evidence=false`. The PNG and JSON summary carry an equally prominent
non-evidence warning. The LOCO exercise is deliberately labeled a mechanical
diagnostic because it is not part of the frozen confirmatory design.

Focused verification:

```powershell
.\.venv\Scripts\python -m pytest -q tests\test_multicity_synthetic_smoke.py tests\test_multicity_transfer_model.py
.\.venv\Scripts\python -m ruff check src\la_heat\multicity\synthetic_smoke.py scripts\run_multicity_synthetic_smoke.py tests\test_multicity_synthetic_smoke.py
```
