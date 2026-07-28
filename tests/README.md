# Test guide

The test suite enforces both engineering behavior and scientific guardrails.
Tests mirror modules under [`src/la_heat`](../src/la_heat/README.md).

## Main coverage areas

- Landsat QA, scaling, mosaics, tract aggregation, and fixed-grid identity.
- Static, Daymet, calendar, and lagged Sentinel feature timing.
- Forbidden-feature, target-year, split-leakage, and preprocessing isolation.
- Grouped model queues, result compilation, diagnostics, and model locks.
- One-time authorization, prediction freezing, target opening, atomic
  publication, recovery, and evidence verification.
- Website display export, dashboards, transfer helpers, and publication code.

Run the complete checks from the repository root:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
```

For a code change, run focused affected tests first and the full suite before
handoff. A passing unit test does not authorize rerunning the completed final
evaluation or modifying frozen artifacts.

When adding behavior, add regression coverage for its schema, time boundary,
failure mode, and provenance invariant—not only the successful path.
