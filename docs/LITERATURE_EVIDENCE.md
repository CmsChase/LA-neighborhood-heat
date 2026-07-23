# Literature evidence map

Last reviewed: 2026-07-22

This file records the source-to-claim mapping used in the simulated ISEF
report. It is not model evidence. Project results must still come from the
frozen generated tables and figures.

## Data and methods

1. **USGS Landsat 8–9 Collection 2 Level-2 Science Product Guide and Surface
   Temperature documentation.**
   [Product guide](https://www.usgs.gov/media/files/landsat-8-9-collection-2-level-2-science-product-guide),
   [product page](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature),
   [data DOI 10.5066/P9OGBGM6](https://doi.org/10.5066/P9OGBGM6).
   Supports the Collection 2 L2SP surface-temperature and QA fields, the
   `DN × 0.00341802 + 149 K` conversion, and caution near clouds. It supports
   calling the response land-surface temperature, not air temperature.

2. **ESA/Copernicus Sentinel-2 Level-2A processing.**
   [Processing documentation](https://sentiwiki.copernicus.eu/web/s2-processing),
   [data DOI 10.5270/S2_-znk9xsj](https://doi.org/10.5270/S2_-znk9xsj).
   Supports use of atmospherically corrected bottom-of-atmosphere reflectance,
   Scene Classification Layer masks, and explicit handling of the 10/20/60 m
   native grids.

3. **Huete et al. (2002), EVI.**
   [DOI 10.1016/S0034-4257(02)00096-2](https://doi.org/10.1016/S0034-4257(02)00096-2).
   Supports the standard EVI form. In this project it is a lagged Sentinel-2
   vegetation-state predictor, not a heat measurement or causal variable.

4. **McFeeters (1996), NDWI.**
   [DOI 10.1080/01431169608948714](https://doi.org/10.1080/01431169608948714).
   Supports the open-water index `(Green − NIR)/(Green + NIR)`, mapped here to
   Sentinel-2 B03 and B08. This is distinct from Gao's NIR–SWIR vegetation-water
   index.

5. **Zha, Gao, and Ni (2003), NDBI.**
   [DOI 10.1080/01431160304987](https://doi.org/10.1080/01431160304987).
   Supports `(SWIR − NIR)/(SWIR + NIR)` as a built-up spectral indicator,
   mapped here to Sentinel-2 B11 and B08. It is interpreted only as a predictive
   built-environment proxy.

6. **Bonafoni and Sekertekin (2020), Sentinel-2 broadband albedo.**
   [DOI 10.1109/LGRS.2020.2967085](https://doi.org/10.1109/LGRS.2020.2967085).
   Supports the six-band coefficients used by the project. The resulting
   temporal composite remains labeled `albedo_proxy`, not an in-situ albedo
   measurement.

7. **Thornton et al., Daymet V4 R1.**
   [ORNL DAAC data DOI 10.3334/ORNLDAAC/2129](https://doi.org/10.3334/ORNLDAAC/2129).
   Supports Daymet's 1 km daily meteorological variables and its station-based
   interpolation methodology. The project uses lagged historical observations
   for a hindcast and does not claim to use an operational forecast.

8. **Roberts et al. (2017), structured cross-validation.**
   [DOI 10.1111/ecog.02881](https://doi.org/10.1111/ecog.02881).
   Supports blocking validation by the spatial and temporal dependence
   structure instead of randomly splitting dependent observations.

9. **Moran (1950), Moran's I.**
   [DOI 10.1093/biomet/37.1-2.17](https://doi.org/10.1093/biomet/37.1-2.17).
   Supports Moran's I as a global spatial-autocorrelation statistic. The
   project-specific rook adjacency, date aggregation, and permutation settings
   are defined by project code and configuration.

## Background and interpretation boundaries

10. **White-Newsome et al. (2013), satellite LST and heat exposure.**
    [DOI 10.1289/ehp.1206176](https://doi.org/10.1289/ehp.1206176),
    [CDC archived copy](https://stacks.cdc.gov/view/cdc/22579/).
    Supports using satellite LST to describe longer-term spatial heat
    differences while recognizing weaker correspondence with short-timescale
    air-temperature exposure. This project therefore treats LST only as a
    surface-heat hazard proxy, never as human exposure or a health outcome.

11. **Stewart and Oke (2012), Local Climate Zones.**
    [DOI 10.1175/BAMS-D-11-00019.1](https://doi.org/10.1175/BAMS-D-11-00019.1).
    Supports the relevance of urban form, surface cover, and human activity to
    local thermal environments. It motivates predictive land-use and geography
    features without establishing a causal effect in this study.

12. **Weng, Lu, and Schubring (2004), vegetation abundance and LST.**
    [DOI 10.1016/j.rse.2003.11.005](https://doi.org/10.1016/j.rse.2003.11.005).
    Supports an observed spatial association between urban vegetation abundance
    and Landsat LST. It does not justify a causal claim about vegetation and Los
    Angeles temperature in this project.

