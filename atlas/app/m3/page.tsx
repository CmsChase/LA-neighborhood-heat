import type { Metadata } from "next";
import Link from "next/link";
import { M3_BLIND_RESULT as result } from "./results";
import styles from "./m3.module.css";

const repositoryRoot =
  "https://github.com/CmsChase/LA-neighborhood-heat/blob/main";

export const metadata: Metadata = {
  title: "M3 four-city blind result · Surface Heat Atlas",
  description:
    "The authenticated Seattle, Denver, Atlanta, and Miami M3 blind evaluation: not confirmed, with every prespecified gate reported.",
};

function signed(value: number, digits = 2) {
  return (value < 0 ? "−" : "+") + Math.abs(value).toFixed(digits);
}

export default function M3ResultPage() {
  const maximumMae = Math.max(
    result.primary.b1MaeC,
    result.primary.m3MaeC,
  );

  return (
    <main className={styles.page}>
      <header className="site-header">
        <Link className="wordmark" href="/">
          <span>M3</span> Surface Heat Atlas
        </Link>
        <nav aria-label="M3 result navigation">
          <a href="#result">Result</a>
          <a href="#cities">Cities</a>
          <a href="#gates">Gates</a>
          <a href="#integrity">Integrity</a>
        </nav>
        <Link className="header-tag" href="/cities">
          Earlier transfer study
        </Link>
      </header>

      <section className={styles.hero} id="top">
        <div className={styles.heroGrid} aria-hidden="true" />
        <div className={styles.heroCopy}>
          <span className={styles.recordBadge}>
            <i /> Authenticated · one-time blind evaluation
          </span>
          <span className="eyebrow light">Seattle · Denver · Atlanta · Miami</span>
          <h1>
            A result,
            <br />
            <em>not a victory.</em>
          </h1>
          <p>
            The frozen M3 model met four cities it had never seen. It did not
            outperform the legal baseline—and the experiment reports that
            answer without moving the goalposts.
          </p>
          <div className={styles.heroActions}>
            <a className={styles.primaryAction} href="#result">
              Read the result <span>↓</span>
            </a>
            <a
              className={styles.textAction}
              href={repositoryRoot + "/reports/M3_BLIND_EVALUATION_REPORT.md"}
              rel="noreferrer"
              target="_blank"
            >
              Open scientific report ↗
            </a>
          </div>
        </div>

        <aside className={styles.verdictCard} aria-label="M3 blind-test verdict">
          <span>Protocol verdict</span>
          <strong>{result.label}</strong>
          <code>{result.state}</code>
          <div className={styles.verdictRule} />
          <p>
            M3 recorded higher error than B1. Miami also fell below the frozen
            minimum number of usable dates.
          </p>
          <dl>
            <div>
              <dt>Primary gates passed</dt>
              <dd>0 / 5</dd>
            </div>
            <div>
              <dt>Models changed after opening</dt>
              <dd>None</dd>
            </div>
          </dl>
        </aside>
      </section>

      <section className={styles.resultBand} id="result">
        <div className={styles.resultLead}>
          <span>Primary comparison</span>
          <strong>53.4%</strong>
          <p>worse MAE than B1</p>
        </div>
        <div className={styles.maeComparison}>
          <div className={styles.comparisonHeading}>
            <span>Equal-city · equal-date MAE</span>
            <small>Lower is better</small>
          </div>
          <div className={styles.maeRow}>
            <span>B1</span>
            <div>
              <i
                style={{
                  width:
                    (result.primary.b1MaeC / maximumMae) * 100 + "%",
                }}
              />
            </div>
            <strong>{result.primary.b1MaeC.toFixed(2)}°</strong>
          </div>
          <div className={[styles.maeRow, styles.m3Row].join(" ")}>
            <span>M3</span>
            <div>
              <i
                style={{
                  width:
                    (result.primary.m3MaeC / maximumMae) * 100 + "%",
                }}
              />
            </div>
            <strong>{result.primary.m3MaeC.toFixed(2)}°</strong>
          </div>
        </div>
        <div className={styles.intervalBlock}>
          <span>95% crossed-bootstrap interval</span>
          <strong>
            −75.8% <i>to</i> −32.6%
          </strong>
          <p>
            All {result.primary.bootstrapReplicates.toLocaleString()} replicates
            stayed below zero improvement.
          </p>
        </div>
      </section>

      <section className={styles.supportRail} aria-label="Evaluation support">
        <div>
          <strong>{result.support.rows.toLocaleString()}</strong>
          <span>tract-date rows</span>
        </div>
        <div>
          <strong>{result.support.cityDates}</strong>
          <span>usable city-dates</span>
        </div>
        <div>
          <strong>{result.support.spatialBlocks}</strong>
          <span>5 km spatial blocks</span>
        </div>
        <p>Frozen evaluation support</p>
      </section>

      <section className={styles.section} id="cities">
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">01 · City by city</span>
            <h2>The aggregate hides a fracture.</h2>
          </div>
          <p>
            Seattle and Miami improved. Atlanta slipped. Denver failed sharply.
            The four cities remain one indivisible claim—not four chances to
            select a favorable story.
          </p>
        </div>

        <div className={styles.cityGrid}>
          {result.cities.map((city, index) => {
            const improved = city.deltaC < 0;
            const insufficient = city.dates < 8;
            return (
              <article
                className={[
                  styles.cityCard,
                  city.id === "denver_co" ? styles.denverCard : "",
                ].join(" ")}
                key={city.id}
              >
                <div className={styles.cityTopline}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <code>{city.code}</code>
                </div>
                <h3>{city.name}</h3>
                <div className={styles.cityDelta} data-improved={improved}>
                  <strong>{signed(city.deltaC)}°C</strong>
                  <span>M3 minus B1</span>
                </div>
                <div className={styles.cityMaePair}>
                  <div>
                    <span>B1</span>
                    <strong>{city.b1MaeC.toFixed(2)}°</strong>
                  </div>
                  <div>
                    <span>M3</span>
                    <strong>{city.m3MaeC.toFixed(2)}°</strong>
                  </div>
                </div>
                <dl>
                  <div>
                    <dt>Usable dates</dt>
                    <dd>{city.dates}</dd>
                  </div>
                  <div>
                    <dt>Rows</dt>
                    <dd>{city.rows.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Coverage</dt>
                    <dd>{city.coveragePercent.toFixed(1)}%</dd>
                  </div>
                </dl>
                <p className={styles.cityNote}>
                  {insufficient
                    ? "Below the frozen minimum of 8 usable dates."
                    : city.id === "denver_co"
                      ? "The central generalization failure in this cohort."
                      : improved
                        ? "Lower point error than the legal baseline."
                        : "Higher point error than the legal baseline."}
                </p>
              </article>
            );
          })}
        </div>
      </section>

      <section className={styles.gateSection} id="gates">
        <div className={styles.gateIntro}>
          <span className="eyebrow light">02 · Prespecified decision</span>
          <h2>
            Five gates.
            <br />
            Five honest failures.
          </h2>
          <p>
            “Authenticated” means the files and lineage are genuine. It does not
            mean the scientific hypothesis passed.
          </p>
        </div>
        <ol className={styles.gateList}>
          {result.gates.map((gate, index) => (
            <li key={gate.name}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h3>{gate.name}</h3>
                <p>{gate.detail}</p>
              </div>
              <strong>Not passed</strong>
            </li>
          ))}
        </ol>
      </section>

      <section className={styles.reliabilitySection}>
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">03 · Reliability</span>
            <h2>Uncertainty did not travel cleanly.</h2>
          </div>
          <p>
            The frozen intervals retained every prediction, yet overall
            coverage reached only 71.15%. In Denver, coverage fell to 30.38%.
          </p>
        </div>
        <div className={styles.coverageGraphic}>
          <div className={styles.coverageScale}>
            <span>0%</span>
            <span>Nominal 90%</span>
            <span>100%</span>
            <i className={styles.nominalMarker} />
            <i
              className={styles.observedMarker}
              style={{
                left: result.reliability.intervalCoveragePercent + "%",
              }}
            />
          </div>
          <div className={styles.coverageReadout}>
            <span>Observed coverage</span>
            <strong>
              {result.reliability.intervalCoveragePercent.toFixed(1)}%
            </strong>
          </div>
          <dl>
            <div>
              <dt>Retention</dt>
              <dd>{result.reliability.retentionPercent}%</dd>
            </div>
            <div>
              <dt>Accepted MAE</dt>
              <dd>{result.reliability.acceptedMaeC.toFixed(2)}°C</dd>
            </div>
            <div>
              <dt>Mean interval width</dt>
              <dd>{result.reliability.intervalWidthC.toFixed(2)}°C</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className={styles.integritySection} id="integrity">
        <div className={styles.integrityHeading}>
          <span className="eyebrow light">04 · Experimental integrity</span>
          <h2>The answer arrived after the prediction was sealed.</h2>
          <p>
            This ordering is the experiment. The targets cannot flow backward
            into the model, and these four opened cities cannot become a second
            blind test.
          </p>
        </div>
        <div className={styles.timeline}>
          <article>
            <span>01</span>
            <small>Source only</small>
            <h3>Select M3</h3>
            <p>QA and model choices used source-city nested validation only.</p>
          </article>
          <article>
            <span>02</span>
            <small>Targets sealed</small>
            <h3>Commit predictions</h3>
            <p>All four-city predictions received an immutable fingerprint.</p>
          </article>
          <article>
            <span>03</span>
            <small>One-time opening</small>
            <h3>Read targets</h3>
            <p>Blind thermal and QA values were opened only after prediction.</p>
          </article>
          <article>
            <span>04</span>
            <small>No rescue</small>
            <h3>Publish the failure</h3>
            <p>No refit, recalibration, retuning, or favorable-city selection.</p>
          </article>
        </div>
        <div className={styles.commitStrip}>
          <span>Terminal certification</span>
          <code>{result.commits.terminal}</code>
          <a
            href={
              repositoryRoot +
              "/manifests/multicity/next_experiment/blind_evaluation_v1/" +
              "M3_BLIND_EVALUATION_TERMINAL_COMPLETE.json"
            }
            rel="noreferrer"
            target="_blank"
          >
            Inspect record ↗
          </a>
        </div>
      </section>

      <section className={styles.finalNote}>
        <span>What this means</span>
        <h2>M3 is not confirmed for cross-city use.</h2>
        <p>
          Future development may learn from this result, but any new
          confirmation claim requires a new model contract and a genuinely
          untouched cohort. Landsat surface temperature remains a clear-sky
          surface-heat proxy—not air temperature, exposure, illness, or
          causation.
        </p>
      </section>

      <footer className={styles.footer}>
        <div>
          <strong>M3 four-city blind evaluation</strong>
          <span>Seattle · Denver · Atlanta · Miami</span>
        </div>
        <p>
          Authenticated evidence · unsuccessful scientific confirmation ·{" "}
          <Link href="/">Return to the Los Angeles atlas.</Link>
        </p>
      </footer>
    </main>
  );
}
