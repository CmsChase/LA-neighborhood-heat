"use client";

import { useMemo, useState, type CSSProperties } from "react";
import type {
  AuthenticatedCityResults,
  CityDesign,
  FourCityComparisonData,
} from "./comparison-data";
import styles from "./cities.module.css";

type DesignMetricKey =
  | "tractCount"
  | "plannedOverpasses"
  | "spatialBlockCount";

const DESIGN_METRICS: Array<{
  key: DesignMetricKey;
  label: string;
  shortLabel: string;
  unit: string;
}> = [
  {
    key: "tractCount",
    label: "Canonical census tracts",
    shortLabel: "Tracts",
    unit: "tracts",
  },
  {
    key: "plannedOverpasses",
    label: "Planned physical overpasses",
    shortLabel: "Overpasses",
    unit: "dates",
  },
  {
    key: "spatialBlockCount",
    label: "Target-blind 5 km spatial blocks",
    shortLabel: "Blocks",
    unit: "blocks",
  },
];

const RESULT_COLUMNS: Array<{
  label: string;
  read: (result: AuthenticatedCityResults) => number;
  format: (value: number) => string;
}> = [
  {
    label: "Equal-date MAE",
    read: (result) => result.primary.equalDateMaeC,
    format: (value) => `${value.toFixed(2)}°C`,
  },
  {
    label: "MAE improvement vs B1",
    read: (result) => result.primary.relativeMaeImprovementPercent,
    format: (value) => `${value.toFixed(1)}%`,
  },
  {
    label: "Median rank ρ",
    read: (result) => result.primary.medianPerDateSpearman,
    format: (value) => value.toFixed(3),
  },
  {
    label: "90% interval coverage",
    read: (result) => result.uncertainty.empiricalCoverage,
    format: (value) => `${(value * 100).toFixed(1)}%`,
  },
];

function roleLabel(role: CityDesign["role"]) {
  return role === "source_anchor"
    ? "Source + calibration"
    : "External confirmation";
}

function isExternalResult(value: unknown): value is AuthenticatedCityResults {
  return (
    typeof value === "object" &&
    value !== null &&
    "resultState" in value &&
    value.resultState === "authenticated_external_confirmation"
  );
}

export default function ComparisonPanel({
  data,
}: {
  data: FourCityComparisonData;
}) {
  const [metricKey, setMetricKey] =
    useState<DesignMetricKey>("tractCount");
  const verified = data.release.state === "verified";
  const metric = DESIGN_METRICS.find((item) => item.key === metricKey)!;
  const maximum = useMemo(
    () => Math.max(...data.cities.map((city) => city[metricKey])),
    [data.cities, metricKey],
  );

  return (
    <>
      <section className={styles.section} id="compare">
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">01 · Study frame</span>
            <h2>Compare the design before the outcomes.</h2>
          </div>
          <p>
            These authenticated inventory counts describe what each city
            contributes to the protocol; they are not performance estimates.
          </p>
        </div>

        <div className={styles.comparisonCard}>
          <div className={styles.comparisonToolbar}>
            <div>
              <span>Current dimension</span>
              <strong>{metric.label}</strong>
            </div>
            <div
              aria-label="Comparison dimension"
              className={styles.metricToggle}
              role="group"
            >
              {DESIGN_METRICS.map((item) => (
                <button
                  aria-pressed={metricKey === item.key}
                  className={metricKey === item.key ? styles.active : ""}
                  key={item.key}
                  onClick={() => setMetricKey(item.key)}
                  type="button"
                >
                  {item.shortLabel}
                </button>
              ))}
            </div>
          </div>

          <div aria-live="polite" className={styles.cityBars}>
            {data.cities.map((city, index) => {
              const value = city[metricKey];
              const barStyle = {
                "--bar-width": `${Math.max(5, (value / maximum) * 100)}%`,
              } as CSSProperties;
              return (
                <article
                  className={styles.cityBarRow}
                  data-city={city.id}
                  key={city.id}
                  style={barStyle}
                >
                  <div className={styles.cityBarIdentity}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{city.name}</strong>
                      <small>{roleLabel(city.role)}</small>
                    </div>
                  </div>
                  <div className={styles.barTrack} aria-hidden="true">
                    <i />
                  </div>
                  <div className={styles.barValue}>
                    <strong>{value.toLocaleString()}</strong>
                    <span>{metric.unit}</span>
                  </div>
                  <dl className={styles.cityInventory}>
                    <div>
                      <dt>Tracts</dt>
                      <dd>{city.tractCount.toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt>Overpasses</dt>
                      <dd>{city.plannedOverpasses}</dd>
                    </div>
                    <div>
                      <dt>5 km blocks</dt>
                      <dd>{city.spatialBlockCount}</dd>
                    </div>
                  </dl>
                </article>
              );
            })}
          </div>
          <p className={styles.inventoryNote}>
            Inventory source: frozen target contexts and build plan. Overpasses
            are planned physical target units, not usable evaluation-date counts.
          </p>
        </div>
      </section>

      <section className={`${styles.section} ${styles.resultSection}`} id="results">
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">02 · Result interface</span>
            <h2>
              {verified
                ? "External confirmation results are authenticated."
                : "Result slots are intentionally empty."}
            </h2>
          </div>
          <p>
            {verified
              ? "Los Angeles remains a historical source reference; the three 2025 external cities come from one authenticated confirmation claim."
              : "A verified release can populate this same interface. In preview mode, every result object and the claim ID must remain null."}
          </p>
        </div>

        <div className={styles.resultFrame}>
          <div className={styles.resultFrameHeader}>
            <div>
              <span className={styles.lockIcon} aria-hidden="true">
                {verified ? "✓" : "×"}
              </span>
              <div>
                <strong>
                  {verified
                    ? "External evaluation verified"
                    : "External targets sealed"}
                </strong>
                <span>
                  {verified
                    ? data.release.notice
                    : "No cross-city outcome values are bundled with this page."}
                </span>
              </div>
            </div>
            <span className={styles.previewPill}>{data.release.state}</span>
          </div>

          <div
            aria-label="Scrollable four-city performance table"
            className={styles.tableScroller}
            role="region"
            tabIndex={0}
          >
            <table className={styles.resultTable}>
              <caption>
                Los Angeles historical source reference and three-city external
                confirmation interface.
              </caption>
              <thead>
                <tr>
                  <th scope="col">City</th>
                  {RESULT_COLUMNS.map((column) => (
                    <th key={column.label} scope="col">
                      {column.label}
                    </th>
                  ))}
                  <th scope="col">Release state</th>
                </tr>
              </thead>
              <tbody>
                {data.cities.map((city) => (
                  <tr key={city.id}>
                    <th scope="row">
                      <span className={styles.tableCityCode}>{city.code}</span>
                      <span>{city.name}</span>
                    </th>
                    {RESULT_COLUMNS.map((column) => (
                      <td key={column.label}>
                        {isExternalResult(city.results) ? (
                          <strong>
                            {column.format(column.read(city.results))}
                          </strong>
                        ) : city.results?.resultState ===
                          "historical_source_reference" ? (
                          <span className={styles.pendingValue}>
                            Source reference
                          </span>
                        ) : (
                          <span className={styles.pendingValue}>Not released</span>
                        )}
                      </td>
                    ))}
                    <td>
                      <span
                        className={
                          isExternalResult(city.results)
                            ? styles.verifiedState
                            : styles.pendingState
                        }
                      >
                        {isExternalResult(city.results)
                          ? "Authenticated external result"
                          : city.results?.resultState ===
                              "historical_source_reference"
                            ? "Historical LA reference"
                            : "Awaiting verified release"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </>
  );
}
