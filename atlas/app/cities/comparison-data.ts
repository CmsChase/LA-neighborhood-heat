export const COMPARISON_SCHEMA_VERSION = "four-city-comparison-v1" as const;

export const CITY_IDS = [
  "los_angeles_ca",
  "phoenix_az",
  "houston_tx",
  "chicago_il",
] as const;

export type CityId = (typeof CITY_IDS)[number];

export type CityDesign = {
  id: CityId;
  code: "LA" | "PHX" | "HOU" | "CHI";
  name: string;
  region: string;
  role: "source_anchor" | "external_confirmation";
  studyWindow: string;
  tractCount: number;
  plannedOverpasses: number;
  spatialBlockCount: number;
};

export type AuthenticatedCityResults = {
  resultState: "authenticated";
  evaluationRows: number;
  independentDates: number;
  primary: {
    equalDateMaeC: number;
    pooledRmseC: number;
    medianPerDateSpearman: number;
    relativeMaeImprovementPercent: number;
  };
  uncertainty: {
    nominalCoverage: number;
    empiricalCoverage: number;
    abstentionRate: number;
  };
};

type PreviewCity = CityDesign & { results: null };
type VerifiedCity = CityDesign & { results: AuthenticatedCityResults };

type ComparisonDataBase = {
  schemaVersion: typeof COMPARISON_SCHEMA_VERSION;
  endpoint: {
    name: string;
    unit: "degrees_celsius";
    interpretation: string;
  };
  studyDesign: {
    cityCount: 4;
    totalTracts: number;
    plannedOverpasses: number;
    frozenPredictorCount: number;
    externalCohortRule: string;
  };
  cityOrder: CityId[];
  provenance: Array<{
    label: string;
    repositoryPath: string;
    href: string;
  }>;
};

export type PreviewComparisonData = ComparisonDataBase & {
  release: {
    state: "preview";
    label: string;
    claimId: null;
    notice: string;
  };
  cities: PreviewCity[];
};

export type VerifiedComparisonData = ComparisonDataBase & {
  release: {
    state: "verified";
    label: string;
    claimId: string;
    notice: string;
  };
  cities: VerifiedCity[];
};

export type FourCityComparisonData =
  | PreviewComparisonData
  | VerifiedComparisonData;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validateAuthenticatedResults(value: unknown, cityId: string) {
  if (!isRecord(value) || value.resultState !== "authenticated") {
    throw new Error(`${cityId}: verified releases require authenticated results.`);
  }

  const primary = value.primary;
  const uncertainty = value.uncertainty;
  if (!isRecord(primary) || !isRecord(uncertainty)) {
    throw new Error(`${cityId}: result metric groups are incomplete.`);
  }

  const requiredNumbers = [
    value.evaluationRows,
    value.independentDates,
    primary.equalDateMaeC,
    primary.pooledRmseC,
    primary.medianPerDateSpearman,
    primary.relativeMaeImprovementPercent,
    uncertainty.nominalCoverage,
    uncertainty.empiricalCoverage,
    uncertainty.abstentionRate,
  ];
  if (!requiredNumbers.every(isFiniteNumber)) {
    throw new Error(`${cityId}: result metrics must all be finite numbers.`);
  }
}

/**
 * Runtime boundary for a future generated static result bundle. Preview data is
 * required to keep every result object null; verified data is required to carry
 * a claim ID and a complete authenticated result object for all four cities.
 */
export function parseFourCityComparisonData(
  input: unknown,
): FourCityComparisonData {
  if (!isRecord(input) || input.schemaVersion !== COMPARISON_SCHEMA_VERSION) {
    throw new Error("Unsupported four-city comparison schema.");
  }

  const release = input.release;
  const cities = input.cities;
  const cityOrder = input.cityOrder;
  if (!isRecord(release) || !Array.isArray(cities) || !Array.isArray(cityOrder)) {
    throw new Error("Four-city comparison release metadata is incomplete.");
  }

  if (cities.length !== CITY_IDS.length || cityOrder.length !== CITY_IDS.length) {
    throw new Error("The comparison must contain exactly four cities.");
  }

  const orderedIds = cityOrder.join("|");
  if (orderedIds !== CITY_IDS.join("|")) {
    throw new Error("The comparison city order does not match the frozen contract.");
  }

  const observedIds = new Set<string>();
  for (const city of cities) {
    if (!isRecord(city) || typeof city.id !== "string") {
      throw new Error("Every comparison city needs a stable ID.");
    }
    if (!CITY_IDS.includes(city.id as CityId) || observedIds.has(city.id)) {
      throw new Error(`Unexpected or duplicated city ID: ${city.id}.`);
    }
    observedIds.add(city.id);
    if (
      !isFiniteNumber(city.tractCount) ||
      !isFiniteNumber(city.plannedOverpasses) ||
      !isFiniteNumber(city.spatialBlockCount)
    ) {
      throw new Error(`${city.id}: design inventory values must be finite numbers.`);
    }
  }

  if (release.state === "preview") {
    if (release.claimId !== null || cities.some((city) => city.results !== null)) {
      throw new Error("Preview releases cannot contain a claim ID or result values.");
    }
  } else if (release.state === "verified") {
    if (typeof release.claimId !== "string" || release.claimId.length === 0) {
      throw new Error("Verified releases require a non-empty claim ID.");
    }
    for (const city of cities) {
      validateAuthenticatedResults(city.results, String(city.id));
    }
  } else {
    throw new Error("Unknown four-city comparison release state.");
  }

  return input as FourCityComparisonData;
}

const previewData = {
  schemaVersion: COMPARISON_SCHEMA_VERSION,
  release: {
    state: "preview",
    label: "Interface preview — no cross-city results",
    claimId: null,
    notice:
      "This payload contains target-blind study-design inventory only. External-city performance and target values are not included.",
  },
  endpoint: {
    name: "QA-filtered daytime land-surface temperature",
    unit: "degrees_celsius",
    interpretation:
      "A surface-heat hazard proxy, not air temperature, personal exposure, or a health outcome.",
  },
  studyDesign: {
    cityCount: 4,
    totalTracts: 2902,
    plannedOverpasses: 154,
    frozenPredictorCount: 46,
    externalCohortRule:
      "Phoenix, Houston, and Chicago form one indivisible 2025 external confirmation cohort.",
  },
  cityOrder: [...CITY_IDS],
  cities: [
    {
      id: "los_angeles_ca",
      code: "LA",
      name: "Los Angeles",
      region: "Pacific basin",
      role: "source_anchor",
      studyWindow: "2020–2024 source + calibration",
      tractCount: 1096,
      plannedOverpasses: 90,
      spatialBlockCount: 71,
      results: null,
    },
    {
      id: "phoenix_az",
      code: "PHX",
      name: "Phoenix",
      region: "Sonoran Desert",
      role: "external_confirmation",
      studyWindow: "2025 external cohort",
      tractCount: 375,
      plannedOverpasses: 22,
      spatialBlockCount: 59,
      results: null,
    },
    {
      id: "houston_tx",
      code: "HOU",
      name: "Houston",
      region: "Gulf Coast",
      role: "external_confirmation",
      studyWindow: "2025 external cohort",
      tractCount: 651,
      plannedOverpasses: 21,
      spatialBlockCount: 88,
      results: null,
    },
    {
      id: "chicago_il",
      code: "CHI",
      name: "Chicago",
      region: "Great Lakes",
      role: "external_confirmation",
      studyWindow: "2025 external cohort",
      tractCount: 780,
      plannedOverpasses: 21,
      spatialBlockCount: 36,
      results: null,
    },
  ],
  provenance: [
    {
      label: "Target-blind city contexts",
      repositoryPath: "manifests/multicity/targets/TARGET_CONTEXTS.json",
      href: "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/manifests/multicity/targets/TARGET_CONTEXTS.json",
    },
    {
      label: "Frozen target build plan",
      repositoryPath: "manifests/multicity/targets/TARGET_BUILD_PLAN.json",
      href: "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/manifests/multicity/targets/TARGET_BUILD_PLAN.json",
    },
    {
      label: "Portable predictor contract",
      repositoryPath:
        "manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json",
      href: "https://github.com/CmsChase/LA-neighborhood-heat/blob/main/manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json",
    },
  ],
} satisfies PreviewComparisonData;

export const FOUR_CITY_COMPARISON_DATA =
  parseFourCityComparisonData(previewData);
