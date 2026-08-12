import { GENERATED_VERIFIED_RELEASE } from "./generated-results";

export const COMPARISON_SCHEMA_VERSION = "four-city-comparison-v1" as const;

export const CITY_IDS = [
  "los_angeles_ca",
  "phoenix_az",
  "houston_tx",
  "chicago_il",
] as const;

export const EVIDENCE_FIGURE_IDS = [
  "external_city_mae",
  "predicted_vs_observed",
  "error_by_city_date",
  "interval_calibration",
  "risk_coverage",
  "spatial_error_maps",
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
  resultState: "authenticated_external_confirmation";
  evaluationRows: number;
  independentDates: number;
  independentSpatialBlocks: number;
  evaluatedDateRange: {
    first: string;
    last: string;
  };
  primary: {
    equalDateMaeC: number;
    baselineEqualDateMaeC: number;
    medianPerDateSpearman: number;
    relativeMaeImprovementPercent: number;
  };
  uncertainty: {
    nominalCoverage: number;
    empiricalCoverage: number;
    retentionFraction: number;
    meanIntervalWidthC: number;
    wis90C: number;
  };
};

export type HistoricalSourceReference = {
  resultState: "historical_source_reference";
  label: string;
  href: string;
  comparableAsExternalConfirmation: false;
  notice: string;
};

export type EvidenceFigure = {
  id: (typeof EVIDENCE_FIGURE_IDS)[number];
  title: string;
  description: string;
  publicPath: string;
  repositoryPath: string;
  href: string;
  sha256: string;
};

export type ExternalConfirmationOutcome = {
  cohortState: "complete" | "inconclusive_sample_size";
  cityIds: Exclude<CityId, "los_angeles_ca">[];
  usableRows: number;
  usableCityDates: number;
  spatialBlocks: number;
  relativeMaeImprovementPercent: number;
  bootstrapCiPercent: {
    lower: number;
    upper: number;
  };
  pointPredictionGatePassed: boolean;
  reliabilityGatePassed: boolean;
};

type PreviewCity = CityDesign & { results: null };
type VerifiedCity = CityDesign & {
  results: AuthenticatedCityResults | HistoricalSourceReference;
};

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
  evidenceFigures: EvidenceFigure[];
};

export type PreviewComparisonData = ComparisonDataBase & {
  release: {
    state: "preview";
    label: string;
    claimId: null;
    notice: string;
  };
  externalConfirmation: null;
  cities: PreviewCity[];
};

export type VerifiedComparisonData = ComparisonDataBase & {
  release: {
    state: "verified";
    label: string;
    claimId: string;
    notice: string;
  };
  externalConfirmation: ExternalConfirmationOutcome;
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
  if (
    !isRecord(value) ||
    value.resultState !== "authenticated_external_confirmation"
  ) {
    throw new Error(`${cityId}: verified releases require authenticated results.`);
  }

  const primary = value.primary;
  const uncertainty = value.uncertainty;
  if (!isRecord(primary) || !isRecord(uncertainty)) {
    throw new Error(`${cityId}: result metric groups are incomplete.`);
  }
  const dateRange = value.evaluatedDateRange;
  if (
    !isRecord(dateRange) ||
    typeof dateRange.first !== "string" ||
    typeof dateRange.last !== "string"
  ) {
    throw new Error(`${cityId}: evaluated date range is incomplete.`);
  }

  const requiredNumbers = [
    value.evaluationRows,
    value.independentDates,
    value.independentSpatialBlocks,
    primary.equalDateMaeC,
    primary.baselineEqualDateMaeC,
    primary.medianPerDateSpearman,
    primary.relativeMaeImprovementPercent,
    uncertainty.nominalCoverage,
    uncertainty.empiricalCoverage,
    uncertainty.retentionFraction,
    uncertainty.meanIntervalWidthC,
    uncertainty.wis90C,
  ];
  if (!requiredNumbers.every(isFiniteNumber)) {
    throw new Error(`${cityId}: result metrics must all be finite numbers.`);
  }
}

function validateSourceReference(value: unknown) {
  if (
    !isRecord(value) ||
    value.resultState !== "historical_source_reference" ||
    value.comparableAsExternalConfirmation !== false ||
    typeof value.label !== "string" ||
    typeof value.href !== "string" ||
    typeof value.notice !== "string"
  ) {
    throw new Error(
      "Los Angeles must remain an explicit historical source reference.",
    );
  }
}

function validateExternalConfirmation(value: unknown) {
  if (!isRecord(value)) {
    throw new Error("Authenticated releases require a cohort-level outcome.");
  }
  if (
    value.cohortState !== "complete" &&
    value.cohortState !== "inconclusive_sample_size"
  ) {
    throw new Error("Unknown external confirmation cohort state.");
  }
  const cityIds = value.cityIds;
  const expectedCityIds = CITY_IDS.filter((id) => id !== "los_angeles_ca");
  if (
    !Array.isArray(cityIds) ||
    cityIds.join("|") !== expectedCityIds.join("|")
  ) {
    throw new Error("External confirmation cohort membership changed.");
  }
  const bootstrapCiPercent = value.bootstrapCiPercent;
  if (
    !isRecord(bootstrapCiPercent) ||
    ![
      value.usableRows,
      value.usableCityDates,
      value.spatialBlocks,
      value.relativeMaeImprovementPercent,
      bootstrapCiPercent.lower,
      bootstrapCiPercent.upper,
    ].every(isFiniteNumber) ||
    Number(bootstrapCiPercent.lower) > Number(bootstrapCiPercent.upper) ||
    typeof value.pointPredictionGatePassed !== "boolean" ||
    typeof value.reliabilityGatePassed !== "boolean"
  ) {
    throw new Error("External confirmation cohort metrics are incomplete.");
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
  const evidenceFigures = input.evidenceFigures;
  const externalConfirmation = input.externalConfirmation;
  if (
    !isRecord(release) ||
    !Array.isArray(cities) ||
    !Array.isArray(cityOrder) ||
    !Array.isArray(evidenceFigures)
  ) {
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
    if (
      release.claimId !== null ||
      externalConfirmation !== null ||
      cities.some((city) => city.results !== null) ||
      evidenceFigures.length !== 0
    ) {
      throw new Error("Preview releases cannot contain a claim ID or result values.");
    }
  } else if (release.state === "verified") {
    if (typeof release.claimId !== "string" || release.claimId.length === 0) {
      throw new Error("Verified releases require a non-empty claim ID.");
    }
    validateExternalConfirmation(externalConfirmation);
    for (const city of cities) {
      if (city.id === "los_angeles_ca") {
        validateSourceReference(city.results);
      } else {
        validateAuthenticatedResults(city.results, String(city.id));
      }
    }
    const figureIds = new Set<string>();
    if (evidenceFigures.length !== 6) {
      throw new Error("Verified releases require exactly six evidence figures.");
    }
    for (const figure of evidenceFigures) {
      if (
        !isRecord(figure) ||
        typeof figure.id !== "string" ||
        figureIds.has(figure.id) ||
        typeof figure.title !== "string" ||
        typeof figure.description !== "string" ||
        typeof figure.publicPath !== "string" ||
        !figure.publicPath.startsWith("/evidence/multicity/") ||
        typeof figure.repositoryPath !== "string" ||
        typeof figure.href !== "string" ||
        typeof figure.sha256 !== "string" ||
        !/^[0-9a-f]{64}$/.test(figure.sha256)
      ) {
        throw new Error("Verified evidence figure metadata is invalid.");
      }
      figureIds.add(figure.id);
    }
    if (
      evidenceFigures.map((figure) => figure.id).join("|") !==
      EVIDENCE_FIGURE_IDS.join("|")
    ) {
      throw new Error("Verified evidence figure order changed.");
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
  externalConfirmation: null,
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
  evidenceFigures: [],
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

function materializeGeneratedRelease(input: unknown): unknown {
  if (input === null) return previewData;
  if (
    !isRecord(input) ||
    input.schemaVersion !== "multicity-atlas-release-v1" ||
    !isRecord(input.release) ||
    !isRecord(input.sourceReference) ||
    !isRecord(input.externalConfirmation) ||
    !Array.isArray(input.externalResults) ||
    !Array.isArray(input.evidenceFigures) ||
    !Array.isArray(input.provenance)
  ) {
    throw new Error("Generated Atlas release has an unsupported schema.");
  }
  const byCity = new Map<string, unknown>();
  for (const result of input.externalResults) {
    if (!isRecord(result) || typeof result.cityId !== "string") {
      throw new Error("Generated external result lacks a city ID.");
    }
    if (byCity.has(result.cityId)) {
      throw new Error(`Generated result duplicates ${result.cityId}.`);
    }
    byCity.set(result.cityId, result);
  }
  const expectedExternal = CITY_IDS.filter((id) => id !== "los_angeles_ca");
  if (
    byCity.size !== expectedExternal.length ||
    expectedExternal.some((id) => !byCity.has(id))
  ) {
    throw new Error("Generated release must contain exactly three external cities.");
  }
  return {
    ...previewData,
    release: input.release,
    externalConfirmation: input.externalConfirmation,
    evidenceFigures: input.evidenceFigures,
    cities: previewData.cities.map((city) => ({
      ...city,
      results:
        city.id === "los_angeles_ca"
          ? input.sourceReference
          : byCity.get(city.id),
    })),
    provenance: [...previewData.provenance, ...input.provenance],
  };
}

export const FOUR_CITY_COMPARISON_DATA =
  parseFourCityComparisonData(
    materializeGeneratedRelease(GENERATED_VERIFIED_RELEASE),
  );
