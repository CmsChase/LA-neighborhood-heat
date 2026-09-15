# LA residual diagnostics

This is a bounded read-only diagnostic over the existing 2022--2024 strict-
forward OOF residuals. It does not train a model, acquire data, change QA, or
alter the scoring cohort. Reuse of the same years makes every result exploratory
development evidence rather than independent confirmation.

The six-neighbor statistic compares, separately on each date, each tract's
signed residual with the mean residual of its six nearest *other* public-geometry
tract centroids. A within-date position permutation retains the same overlapping
neighbor graph and missingness pattern, providing a structural null reference.

Quality fields and scene metadata are diagnostic-only target-scene information.
They are never proposed as predictors. Existing Daymet variables are audited for
within-city variation and same-direction residual association by year; all end
at target day minus one. No target-day/post-overpass weather is present.
