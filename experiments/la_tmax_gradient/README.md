# LA d-1 Tmax gradient experiment

This is one adaptive, bounded development comparison over the existing LA
2020--2024 data. It compares the unchanged 23-feature relative HGB with one
24-feature clone that adds only:

`Tmax(tract, d-1) - median[Tmax(all 1096 fixed LA prediction tracts, d-1)]`.

The same-date median is computed from the complete target-free prediction
universe before the scored QA subset is joined. It is not a spatial derivative.
No weather window, scale, interaction, model, or QA search is permitted.

The local Daymet audit must show that the one-day source window starts and ends
at target day minus one and is complete. The repository does not record a
publication timestamp proving that the product was released before the target,
so this experiment supports historical hindcast reconstruction only, not an
operational real-time forecast claim.

The strict-forward outer tests remain 2022, 2023, and 2024; each fit uses only
earlier years. The feature was selected after inspecting residuals from these
same years, so even a passing result is adaptive development evidence requiring
an independent future year.
