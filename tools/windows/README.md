# Historical Windows launchers

These convenience launchers supported completed workstation-to-laptop and
dashboard workflows. They are retained for reproducibility, but they are not
the public API or the current experiment entry point. New users should follow
the platform-neutral commands in the repository [README](../../README.md) and
[reproduction guide](../../docs/REPRODUCING.md).

The launchers live under `legacy/` so the repository root reflects the public
project structure rather than one operator's Windows workflow. The six
relocatable launchers resolve the repository root before invoking Python.
The byte-bound `START_` launcher is an archival copy, with the separate
restoration procedure below; do not launch it directly from `legacy/`.

## Archived launchers

| Launcher | Historical purpose |
|---|---|
| `IMPORT_SENTINEL_RESULTS.cmd` | Validate and import a returned Sentinel package |
| `PACKAGE_M3_SOURCE_DEVELOPMENT.cmd` | Package a safely paused source-development runtime |
| `RUN_LA_SOURCE_TARGETS.cmd` | Open the completed LA source-target dashboard |
| `RUN_M3_PREDICTOR_GAME_LAPTOP.cmd` | Resume the completed source-predictor laptop run |
| `RUN_M3_SOURCE_DEVELOPMENT.cmd` | Open the completed M3 source-development dashboard |
| `RUN_THREE_CITY_EXTERNAL_TARGETS.cmd` | Open the completed external-target dashboard |
| `START_M3_PREDICTOR_GAME_LAPTOP.cmd` | Exact historical, path-bound four-thread laptop launcher |

The Chinese migration note is archived in
[`docs/operations/M3_PREDICTOR_GAME_LAPTOP_README.zh-CN.md`](../../docs/operations/M3_PREDICTOR_GAME_LAPTOP_README.zh-CN.md).

## Restoring the path-bound historical launcher

`legacy/START_M3_PREDICTOR_GAME_LAPTOP.cmd` preserves the original 779 bytes and
SHA-256 `cf14b4871a74d0d0012ffe88429a57db8d50c9cb135c838aacb8804fa3ef3369`.
The historical authorization and Python runner still name its original root
path; neither has been rewritten. Archiving a file is not a new runtime permit.

Only when reconstructing the old authorized environment, run this from that
checkout's root in PowerShell to restore the original layout without starting
any task:

```powershell
if (Test-Path -LiteralPath '.\START_M3_PREDICTOR_GAME_LAPTOP.cmd') {
    throw 'Original path already exists; inspect it instead of overwriting it.'
}
Copy-Item -LiteralPath '.\tools\windows\legacy\START_M3_PREDICTOR_GAME_LAPTOP.cmd' -Destination '.\START_M3_PREDICTOR_GAME_LAPTOP.cmd'
```

That optional local copy is ignored by Git. A normal public clone lacks the
bundled runtime, data, and historical queue, so restoration alone does not make
the old workflow runnable. Its existing authorization/state checks must still
pass; do not restart completed tasks or treat this launcher as the current
experiment entry point.
