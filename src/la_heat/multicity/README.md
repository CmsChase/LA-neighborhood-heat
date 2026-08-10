# Multicity module map

The active continuation is intentionally small:

- `portable_predictor_contract.py` — locks and authenticates the current
  46-feature contract;
- `portable_predictor_inventory.py` — freezes the target-blind city, tract, and
  Landsat-date predictor keys;
- `portable_predictor_components.py` — builds the canonical calendar, NLCD,
  terrain, and resumable GSHHG static components;
- `portable_predictor_daymet.py` — reuses Los Angeles weather subsets and
  downloads or compiles the matching Daymet inputs for the external cities;
- `portable_predictor_build.py` — owns the durable 84-unit work plan, completed
  task records, final merge, and machine-readable progress;
- `portable_predictor_dashboard.py` — supervises that builder through a local
  start, continue, safe-pause, token-entry, and progress page;
- `phoenix_source_footprint_restage.py` — refreshes Phoenix public source
  metadata against the canonical boundary;
- `four_city_geography_contract_v1.py` — common Census geography;
- `worldcover_eligible_support_evidence_v1.py` — common eligible-land support;
- `sentinel_calibration_smoke_v1.py` — real Sentinel-2 metadata and small native
  DN calibration probes;
- `config.py` and `workspace.py` — shared configuration and paths.

`plan_*transition_v7.py` through `plan_*transition_v18.py` reproduce historical
decisions and old evidence identities. They are not the active control flow.
Do not create another numbered transition for a routine implementation fix;
update `manifests/multicity/ACTIVE_STAGE.json`, the affected module, and its
focused regression test.

The completed component build can be inspected from the repository root with:

```powershell
.\.venv\Scripts\python scripts\run_portable_predictor_dashboard.py --host 127.0.0.1 --port 8768
```

The live status is written below
`data/interim/multicity/portable_predictors/runtime/`. The dashboard and build
remain target-blind and never fit or score a model.

The active next implementation is a visible, resumable Sentinel-2 predictor
runner on the same frozen four-city support.

Post-return preparation is also implemented:

- `portable_sentinel_return.py` and `portable_sentinel_directory_return.py`
  authenticate ZIP or copied-folder returns, import only durable result-owned
  files, resume partial safe-pause returns, and emit the same terminal receipt;
- `predictor_readiness.py` audits the final 46-feature table before any fit;
- `spatial_blocks.py` freezes the continuation-specific EPSG:5070 5 km
  evaluation partition;
- `target_context.py` combines each canonical tract/raster support with that
  frozen partition for later Landsat aggregation without opening target data;
- `target_transaction.py` authenticates frozen scene-overpass-key
  relationships and prepares the 159-unit, still-unauthorized target plan; and
- `transfer_model.py` contains the exact B1 diagnostic, point/quantile M2,
  equal-date weighting, LA-2024 CQR calibration, and target-blind external
  prediction core. It has synthetic tests but is not authorized to fit real
  labels during the current stage.
- `synthetic_smoke.py` runs an explicitly non-evidence, in-memory four-city
  integration check and writes only to a caller-owned noncanonical directory;
  see `docs/MULTICITY_SYNTHETIC_SMOKE.md`.
