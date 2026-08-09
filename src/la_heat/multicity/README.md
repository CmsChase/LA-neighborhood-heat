# Multicity module map

The active continuation is intentionally small:

- `missing_support_calibration_evidence_v1.py` — orchestrates the current
  public-only evidence stage;
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
