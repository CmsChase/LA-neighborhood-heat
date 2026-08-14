# M3 source-development runner

This runner prepares the source-only evidence needed by the locked follow-up
experiment. It is intentionally separate from the completed Phoenix–Houston–
Chicago evaluation and never opens Seattle, Denver, Atlanta, or Miami targets.

## Why the source inventory was expanded

Under the existing `none` ST_QA rule, Phoenix had 21 usable dates, Houston had
4, and Chicago had 3. The locked nested-LOSO gate requires at least 8 usable
dates in every inner validation city. The 3 K, 4 K, and 6 K masks only remove
pixels from `none`, so none of the 16 QA-by-model combinations could pass with
the old inventory.

The append-only acquisition amendment therefore retains Los Angeles 2020–2024
and Phoenix 2025, and expands Houston and Chicago to every qualifying warm-
season overpass from 2020 through 2025. It does not select dates by observed
temperature or QA performance.

## Two low-load phases

1. **Online predownload** uses one or two download workers. It retrieves only
   the five frozen Landsat assets for the four source cities, aligns them to
   each frozen city grid, and writes content-addressed local cache commits.
   Signed URLs and credentials remain in memory and are never saved.
2. **Offline QA rebuild** uses one compute worker and one numerical-library
   thread. It first authenticates the complete local cache, rejects any network
   URL, and reconstructs `none`, `3k`, `4k`, and `6k` from the same cached
   pixels. The current reader processes one complete city overpass at a time;
   the displayed 512 setting is a compatibility parameter, not a hard tiled-
   memory limit. Expected peak memory is roughly hundreds of MB to 1–2 GB.

The offline phase ends at `SOURCE_QA_CANDIDATES_COMPLETE`. Nested LOSO model
selection remains locked and requires a later, separate authorization.

## Start and pause

From the repository root, double-click `RUN_M3_SOURCE_DEVELOPMENT.cmd`. The
local control page opens at <http://127.0.0.1:8772/>. Nothing downloads until
**Start / Continue** is pressed.

- Choose **1 download worker** for the lightest office-laptop load.
- Choose **2 download workers** for faster network use.
- Compute workers stay fixed at **1**. The 512 value shown in the UI is a fixed
  compatibility parameter; it does not claim streaming 512×512 execution.
- **Safe pause** stops claiming new work and lets the current item finish.
- Reopening the launcher resumes from authenticated asset, scene, and queue
  commits; completed files are not downloaded again.

No Earthdata token is needed. Planetary Computer signs its own public Landsat
asset links in memory.

## Network and disk expectations

Only the online phase requires a stable internet connection. A disconnect
returns the current asset to the queue and the supervisor keeps retrying with
backoff. The offline phase makes no network requests.

The raw aligned cache is intentionally much larger than the final tract tables.
Keep substantial free space on the project drive. The launcher places temporary
files under the project data directory rather than the Windows system drive.

## Moving the paused job

After the page reports paused and zero running tasks, double-click
`PACKAGE_M3_SOURCE_DEVELOPMENT.cmd`. The migration builder checkpoints SQLite,
records file hashes, and creates a relocatable folder under
`exports/M3_SOURCE_DEVELOPMENT_OFFICE`.
Never run the same copied queue on two computers at once.

## Completion boundary

The following remain forbidden after this runner finishes:

- fitting or selecting the M3 winner;
- changing the ST_QA candidate set or source-support gate;
- reading target or QA values for Seattle, Denver, Atlanta, or Miami;
- scoring or interpreting the blind test.

The next scientific step is to authenticate the four source QA tables, extend
the corresponding public predictors where required, and issue a separate
source-only nested-LOSO authorization.
