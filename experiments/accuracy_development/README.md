# Relative accuracy development

The user's 2026-09-13 instruction resumes accuracy development and prioritizes
relative neighborhood temperature, retaining absolute temperature as a secondary
output. This is post-hoc source development, never a new confirmation claim.
Use only the original four source cities, fixed QA4K. Exclude LA 2025 and all four
opened stress cities. Frozen historical models and results remain unchanged.

Before any fitting, fix four candidates: existing 23-feature M3 anomaly HGB
(relative_static), B1, a 46-feature anomaly HGB adding weather/calendar
(relative_context), and their fixed half-and-half anomaly blend (relative_blend).
HGB parameters stay fixed. Training responses are centered on the eligible training
city-date median. Held targets never enter fitting. Center predictions over the
complete predictor universe; add the B1 predicted city-date median for absolute
outputs. B1 retains its original absolute output. No city/tract identifiers or
coordinates enter estimators.

Four outer leave-one-city-out folds each select the candidate by inner
leave-one-city-out on the remaining three cities. Equal-city, equal-date anomaly
MAE selects, with ties favoring the listed candidate order. There are 16 distinct
training/held-city splits, each fitting three estimators. Compare the outer selected
procedure with the existing relative model, B1, and zero anomaly. Report scored-
subset centered anomaly MAE for comparability and full-universe centered output
MAE against the observed scored-subset anomaly as an explicit support sensitivity.
Absolute error is secondary; there is no observed full-universe reference truth.

Promotion requires at least 10% relative MAE improvement versus relative_static,
no city degradation, and positive lower paired crossed date/block bootstrap bound.
The bootstrap independently resamples dates and 5 km blocks within each city and
conditions on these four cities. It cannot establish unseen-city population
uncertainty. The final all-source candidate choice uses the four outer fixed-
candidate scores; that selection score is not an independent performance claim.
Save the fitted research candidate with its gate outcome, including if the gate
fails; label unsuccessful development clearly. Do not adapt candidates after results.

Run `.venv\Scripts\python.exe experiments\accuracy_development\run.py`.
Outputs stay ignored under exports/RELATIVE_ACCURACY_DEVELOPMENT. Atomic status,
per-split prediction checkpoints, input/code/config/library signatures and checkpoint
hash verification make the run resumable without trusting stale predictions.

After completion, run `report.py` in the same directory to generate the Chinese
report and ranking diagnostics. Run `predict.py --city los_angeles_ca` to export
relative, anchored absolute, and B1 absolute historical predictions for the
complete frozen LA predictor universe. These fitted-source outputs are not
validation predictions; use `nested_oof.parquet` for validation. Saved artifact
and predictor hashes are checked before export.
