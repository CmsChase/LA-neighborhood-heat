# Historical Windows launchers

These convenience launchers supported completed workstation-to-laptop and
dashboard workflows. They are retained for reproducibility, but they are not
the public API or the current experiment entry point. New users should follow
the platform-neutral commands in the repository [README](../../README.md) and
[reproduction guide](../../docs/REPRODUCING.md).

The launchers live under `legacy/` so the repository root reflects the public
project structure rather than one operator's Windows workflow. Each launcher
resolves the repository root before invoking its Python entry point.

`START_M3_PREDICTOR_GAME_LAPTOP.cmd` intentionally remains at the repository
root. A completed append-only authorization binds that historical file's exact
path and SHA-256, so moving or editing it would invalidate provenance. It is
not expected to work in a normal clone because its bundled runtime and data
package were deliberately excluded from Git.

## Archived launchers

| Launcher | Historical purpose |
|---|---|
| `IMPORT_SENTINEL_RESULTS.cmd` | Validate and import a returned Sentinel package |
| `PACKAGE_M3_SOURCE_DEVELOPMENT.cmd` | Package a safely paused source-development runtime |
| `RUN_LA_SOURCE_TARGETS.cmd` | Open the completed LA source-target dashboard |
| `RUN_M3_PREDICTOR_GAME_LAPTOP.cmd` | Resume the completed source-predictor laptop run |
| `RUN_M3_SOURCE_DEVELOPMENT.cmd` | Open the completed M3 source-development dashboard |
| `RUN_THREE_CITY_EXTERNAL_TARGETS.cmd` | Open the completed external-target dashboard |

The Chinese migration note is archived in
[`docs/operations/M3_PREDICTOR_GAME_LAPTOP_README.zh-CN.md`](../../docs/operations/M3_PREDICTOR_GAME_LAPTOP_README.zh-CN.md).
