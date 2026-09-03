# Reproducing and verifying the project

This guide separates ordinary code verification from scientific regeneration.
The public Atlas and default code tests do not require credentials or the ignored
local raster cache. Real-data evidence audits are a separate, opt-in test lane.

## Requirements

- Python 3.12–3.14
- Git
- Node.js 22 only when building the Atlas
- A platform capable of installing the geospatial Python wheels listed in
  `pyproject.toml`

## Python environment

From the repository root, create a virtual environment:

```text
python -m venv .venv
```

macOS/Linux:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c requirements-ci.txt -e ".[dev]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c requirements-ci.txt -e ".[dev]"
```

## Test and lint

Use the corresponding virtual-environment Python path for your platform:

```text
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest-ci
python -m ruff check .
```

GitHub Actions runs these checks on Ubuntu and Windows with full Git history
(some provenance tests inspect historical commits). The explicit base
temporary directory keeps security-sensitive test artifacts inside the project
boundary and under the synthetic runner's required `.tmp/` namespace on every
operating system. Create the parent first on a fresh clone. Tests use mocks where appropriate; they
must not open a blind-city target or require a private credential.

`requirements-ci.txt` fixes Rasterio at the tested 1.5.0 version. Version 1.5.1
probes a custom opener with `test/test`, which the historical HTTPS-only adapter
correctly rejects. The bound scientific adapter and its security rules are not
modified to accommodate that probe.

Tests that authenticate ignored research products are explicitly inventoried
in `tests/conftest.py` and reported as skipped by default. This is not evidence
that the scientific products passed authentication. On an authorized research
workstation with those products already present, run them explicitly:

```text
python -m pytest -q --basetemp=.tmp/pytest-ci --run-local-evidence -m local_evidence
```

With this option, missing or changed evidence fails normally; there is no
automatic download, replacement evidence, or bypass of the existing assertions.

## Atlas

The Atlas contains compact frozen public evidence:

```text
cd atlas
npm ci
npm test
npm run dev
```

From the repository root, compact website data can be regenerated or verified
with:

```text
python scripts/build_website_data.py
python scripts/build_website_data.py --verify-only
```

Replace `python` with `.venv/bin/python` or
`.\.venv\Scripts\python.exe` when the environment is not activated.

## Evidence package

After a completed multicity evaluation, create or reauthenticate the compact,
aggregate-only evidence package:

```text
python scripts/export_multicity_evidence.py --project-root .
python scripts/export_multicity_evidence.py --project-root . --check-only
```

The exporter excludes tract-level scored rows and targets, fitted models,
runtime databases, credentials, and signed URLs.

## Data and credentials

Large or sensitive generated products remain under ignored `data/` and
`exports/` paths. A clone therefore contains code, compact public evidence, and
machine-readable provenance—not the full raster cache.

Never store Earthdata tokens, bearer tokens, cookies, signed URLs, or `.env`
files in Git. Scientific regeneration that accesses remote public data must
follow the active authorization boundary in
`manifests/multicity/ACTIVE_STAGE.json`.

## Historical Windows operations

Completed workstation-transfer and dashboard launchers are preserved under
`tools/windows/legacy/`. They document earlier runs and are not required for a
new installation. `START_M3_PREDICTOR_GAME_LAPTOP.cmd` remains at the root only
because an append-only authorization binds its exact historical path and hash.
