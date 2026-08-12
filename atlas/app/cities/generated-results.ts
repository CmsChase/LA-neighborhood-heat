// Generated from authenticated external evaluation evidence.
// Do not edit by hand; use scripts/publish_multicity_atlas_release.py.
export const GENERATED_VERIFIED_RELEASE: unknown = {
  "schemaVersion": "multicity-atlas-release-v1",
  "release": {
    "state": "verified",
    "label": "Authenticated three-city external confirmation",
    "claimId": "fda881ca15442257acaaff4b563dbc1d46e5c2002080a8423e8ca703e6c338de",
    "notice": "Los Angeles is the historical source reference; Phoenix, Houston, and Chicago are one authenticated 2025 external confirmation claim."
  },
  "sourceReference": {
    "cityId": "los_angeles_ca",
    "resultState": "historical_source_reference",
    "label": "Completed Phase-I Los Angeles held-out evaluation",
    "href": "/",
    "comparableAsExternalConfirmation": false,
    "notice": "Los Angeles supplied model fitting and calibration data in this transfer experiment, so its earlier held-out study is linked as context and is not pooled with the three external confirmations."
  },
  "externalConfirmation": {
    "cohortState": "inconclusive_sample_size",
    "cityIds": [
      "phoenix_az",
      "houston_tx",
      "chicago_il"
    ],
    "usableRows": 11207,
    "usableCityDates": 28,
    "spatialBlocks": 180,
    "relativeMaeImprovementPercent": 28.916092276511694,
    "bootstrapCiPercent": {
      "lower": 14.105534669836144,
      "upper": 43.51375492934664
    },
    "pointPredictionGatePassed": false,
    "reliabilityGatePassed": false
  },
  "externalResults": [
    {
      "cityId": "phoenix_az",
      "resultState": "authenticated_external_confirmation",
      "evaluationRows": 7585,
      "independentDates": 21,
      "independentSpatialBlocks": 59,
      "evaluatedDateRange": {
        "first": "2025-05-02",
        "last": "2025-10-25"
      },
      "primary": {
        "equalDateMaeC": 4.887776368276468,
        "baselineEqualDateMaeC": 3.107616029637055,
        "medianPerDateSpearman": 0.4618667831156191,
        "relativeMaeImprovementPercent": -57.28379316048646
      },
      "uncertainty": {
        "nominalCoverage": 0.9,
        "empiricalCoverage": 0.43757415952537904,
        "retentionFraction": 0.7124588002636784,
        "meanIntervalWidthC": 9.261658530614707,
        "wis90C": 2.986714797460451
      }
    },
    {
      "cityId": "houston_tx",
      "resultState": "authenticated_external_confirmation",
      "evaluationRows": 2165,
      "independentDates": 4,
      "independentSpatialBlocks": 88,
      "evaluatedDateRange": {
        "first": "2025-05-14",
        "last": "2025-10-29"
      },
      "primary": {
        "equalDateMaeC": 8.448789365703934,
        "baselineEqualDateMaeC": 16.4311920837349,
        "medianPerDateSpearman": 0.7490972100397764,
        "relativeMaeImprovementPercent": 48.58078876658425
      },
      "uncertainty": {
        "nominalCoverage": 0.9,
        "empiricalCoverage": 0.6332563510392609,
        "retentionFraction": 0.20323325635103925,
        "meanIntervalWidthC": 11.14832736395381,
        "wis90C": 5.199468991954653
      }
    },
    {
      "cityId": "chicago_il",
      "resultState": "authenticated_external_confirmation",
      "evaluationRows": 1457,
      "independentDates": 3,
      "independentSpatialBlocks": 33,
      "evaluatedDateRange": {
        "first": "2025-05-16",
        "last": "2025-09-21"
      },
      "primary": {
        "equalDateMaeC": 7.430024934025293,
        "baselineEqualDateMaeC": 9.67538583441488,
        "medianPerDateSpearman": 0.27634981624423804,
        "relativeMaeImprovementPercent": 23.20693912177587
      },
      "uncertainty": {
        "nominalCoverage": 0.9,
        "empiricalCoverage": 0.24571036376115304,
        "retentionFraction": 0.29581331503088537,
        "meanIntervalWidthC": 10.639966566839513,
        "wis90C": 4.4907424347672915
      }
    }
  ],
  "evidenceFigures": [
    {
      "id": "external_city_mae",
      "title": "Point accuracy by city",
      "description": "Frozen M2 and diagnostic B1 equal-date MAE across the three external cities.",
      "publicPath": "/evidence/multicity/external_city_mae.png",
      "repositoryPath": "atlas/public/evidence/multicity/external_city_mae.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/external_city_mae.png",
      "sha256": "3dc3d5863b47c5bc5474b6ea25aa523ca7a66e297c065268dfcd5834407f94d3"
    },
    {
      "id": "predicted_vs_observed",
      "title": "Predicted versus observed",
      "description": "Every usable external observation against its frozen-model estimate.",
      "publicPath": "/evidence/multicity/predicted_vs_observed.png",
      "repositoryPath": "atlas/public/evidence/multicity/predicted_vs_observed.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/predicted_vs_observed.png",
      "sha256": "798f48c9878632811f0b986270bfc053d6c739264c2a482264aba4bfa977135b"
    },
    {
      "id": "error_by_city_date",
      "title": "Error through the season",
      "description": "Date-level error traces show whether transfer performance is stable over time.",
      "publicPath": "/evidence/multicity/error_by_city_date.png",
      "repositoryPath": "atlas/public/evidence/multicity/error_by_city_date.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/error_by_city_date.png",
      "sha256": "415c69833f2323caf08f301940e1b766e1b9d4cc3940a861565740be5e9c32c0"
    },
    {
      "id": "interval_calibration",
      "title": "Uncertainty calibration",
      "description": "Observed coverage of the frozen 90% conformal prediction intervals.",
      "publicPath": "/evidence/multicity/interval_calibration.png",
      "repositoryPath": "atlas/public/evidence/multicity/interval_calibration.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/interval_calibration.png",
      "sha256": "f66661f19c9456de0ab485fe117027efef538e422037bf30008bc570c6f91be9"
    },
    {
      "id": "risk_coverage",
      "title": "Risk–coverage tradeoff",
      "description": "Accuracy as increasingly uncertain estimates are withheld by the frozen rule.",
      "publicPath": "/evidence/multicity/risk_coverage.png",
      "repositoryPath": "atlas/public/evidence/multicity/risk_coverage.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/risk_coverage.png",
      "sha256": "05aa55cbc85873216ae4c8abd13b806e03fd725a10a46e19a4af12d40cf7cf3b"
    },
    {
      "id": "spatial_error_maps",
      "title": "Where errors concentrate",
      "description": "Neighborhood-scale mean absolute error mapped across each external city.",
      "publicPath": "/evidence/multicity/spatial_error_maps.png",
      "repositoryPath": "atlas/public/evidence/multicity/spatial_error_maps.png",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/spatial_error_maps.png",
      "sha256": "968b66f40fe97ab5531057e90f65d781c4c12c1500a8587b20a89a18772d4af1"
    }
  ],
  "provenance": [
    {
      "label": "Authenticated external evaluation completion",
      "repositoryPath": "atlas/public/evidence/multicity/external-evaluation-completion.json",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/external-evaluation-completion.json"
    },
    {
      "label": "Authenticated three-city summary",
      "repositoryPath": "atlas/public/evidence/multicity/external-evaluation-summary.json",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/external-evaluation-summary.json"
    },
    {
      "label": "Authenticated per-city metrics",
      "repositoryPath": "atlas/public/evidence/multicity/external-city-metrics.json",
      "href": "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/atlas/public/evidence/multicity/external-city-metrics.json"
    }
  ]
};
