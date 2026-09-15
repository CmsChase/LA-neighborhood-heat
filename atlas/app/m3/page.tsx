import type { Metadata } from "next";
import Link from "next/link";
import { M3_BLIND_RESULT as result } from "./results";
import styles from "./m3.module.css";

const repositoryRoot =
  "https://github.com/CmsChase/LA-neighborhood-heat/blob/main";

export const metadata: Metadata = {
  title: "The M3 research story · Surface Heat Atlas",
  description:
    "One continuous record from early transfer evidence through the failed M3 blind test, mechanism diagnosis, and the narrower relative-temperature result.",
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
        <nav aria-label="M3 research-story navigation">
          <a href="#story">Story</a>
          <a href="#earlier-transfer">First transfer</a>
          <a href="#blind-result">Blind result</a>
          <a href="#diagnosis">Diagnosis</a>
          <a href="#relative">Relative signal</a>
        </nav>
        <Link className="header-tag" href="/">
          LA interactive atlas
        </Link>
      </header>

      <section className={styles.hero} id="top">
        <div className={styles.heroGrid} aria-hidden="true" />
        <div className={styles.heroCopy}>
          <span className={styles.recordBadge}>
            <i /> One continuous research record · 2025–2026
          </span>
          <span className="eyebrow light">Eight cities · three experiments · one honest turn</span>
          <h1>
            The model failed.
            <br />
            <em>The signal narrowed.</em>
          </h1>
          <p>
            Early transfer looked promising. A harder blind test rejected M3 as
            an absolute-temperature model. Diagnosis then found a smaller,
            repeatable result: neighborhood heat patterns within the same city
            and day.
          </p>
          <div className={styles.heroActions}>
            <a className={styles.primaryAction} href="#story">
              Follow the full line <span>↓</span>
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

        <aside className={styles.verdictCard} aria-label="Current M3 research conclusion">
          <span>Current conclusion</span>
          <strong>One route stopped. One narrowed.</strong>
          <code>absolute: rejected · relative: development signal</code>
          <div className={styles.verdictRule} />
          <p>
            The blind failure remains unchanged. The later relative-temperature
            work uses existing data only and is development evidence—not a new
            confirmation claim.
          </p>
          <dl>
            <div>
              <dt>Absolute blind gates</dt>
              <dd>0 / 5</dd>
            </div>
            <div>
              <dt>Relative source improvement</dt>
              <dd>{result.relative.source.improvementPercent}%</dd>
            </div>
          </dl>
        </aside>
      </section>

      <section className={styles.storySection} id="story">
        <div className={styles.storyHeading}>
          <span className="eyebrow">The whole research line</span>
          <h2>One question became more precise.</h2>
          <p>
            Each stage keeps the previous result intact. A promising estimate
            became an inconclusive transfer, then a failed blind claim, then a
            mechanism diagnosis, and finally a narrower development target.
          </p>
        </div>
        <ol className={styles.storyLine}>
          <li>
            <span>01</span>
            <small>Los Angeles</small>
            <strong>Learn neighborhood heat</strong>
            <p>The original atlas established the local historical hindcast.</p>
          </li>
          <li>
            <span>02</span>
            <small>Phoenix · Houston · Chicago</small>
            <strong>First transfer</strong>
            <p>A positive aggregate estimate, but too little support for confirmation.</p>
          </li>
          <li>
            <span>03</span>
            <small>Source-only development</small>
            <strong>Build M3</strong>
            <p>Separate city-day level from neighborhood spatial anomaly.</p>
          </li>
          <li>
            <span>04</span>
            <small>Seattle · Denver · Atlanta · Miami</small>
            <strong>Blind test fails</strong>
            <p>Absolute error rises 53.4%; all five gates fail.</p>
          </li>
          <li>
            <span>05</span>
            <small>Failure analysis</small>
            <strong>Find the fracture</strong>
            <p>Denver exposes level and elevation extrapolation failure.</p>
          </li>
          <li>
            <span>06</span>
            <small>Existing data only</small>
            <strong>Narrow the claim</strong>
            <p>Relative neighborhood differences remain a development result; cross-city absolute transfer failed.</p>
          </li>
        </ol>
      </section>

      <section className={styles.earlierTransfer} id="earlier-transfer">
        <div>
          <span className="eyebrow light">01 · The first transfer study</span>
          <h2>Promising direction, insufficient confirmation.</h2>
          <p>
            The earlier frozen model moved from Los Angeles to Phoenix, Houston,
            and Chicago. Its aggregate point estimate improved over B1, but the
            point and reliability gates still failed. That result motivated the
            stricter M3 experiment; it did not validate cross-city use.
          </p>
        </div>
        <dl className={styles.transferNumbers}>
          <div>
            <dt>MAE improvement</dt>
            <dd>{result.earlierTransfer.relativeMaeImprovementPercent.toFixed(1)}%</dd>
          </div>
          <div>
            <dt>95% bootstrap interval</dt>
            <dd>
              {result.earlierTransfer.bootstrapLowerPercent.toFixed(1)}–
              {result.earlierTransfer.bootstrapUpperPercent.toFixed(1)}%
            </dd>
          </div>
          <div>
            <dt>Evidence support</dt>
            <dd>
              {result.earlierTransfer.cityDates} dates ·{" "}
              {result.earlierTransfer.rows.toLocaleString()} rows
            </dd>
          </div>
          <div>
            <dt>Scientific verdict</dt>
            <dd>Inconclusive</dd>
          </div>
        </dl>
      </section>

      <section className={styles.blindIntro} id="blind-result">
        <span className="eyebrow">02 · The harder M3 blind test</span>
        <h2>A result, not a victory.</h2>
        <p>
          The frozen M3 model met four genuinely unseen cities. It lost to B1,
          and that failed absolute-temperature result remains the primary answer.
        </p>
      </section>

      <section className={styles.resultBand}>
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
            <span className="eyebrow">03 · Blind result, city by city</span>
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
          <span className="eyebrow light">04 · Prespecified decision</span>
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
            <span className="eyebrow">05 · Reliability</span>
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
          <span className="eyebrow light">06 · Experimental integrity</span>
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

      <section className={styles.diagnosisSection} id="diagnosis">
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">07 · Diagnose before expanding</span>
            <h2>Denver revealed a level problem.</h2>
          </div>
          <p>
            M3 overestimated Denver on every usable date. Once each city-day&apos;s
            overall temperature was removed, much of the neighborhood pattern
            remained. That split the failed task into two distinct questions.
          </p>
        </div>
        <div className={styles.diagnosisGrid}>
          <article className={styles.diagnosisLead}>
            <span>Denver absolute bias</span>
            <strong>+{result.diagnosis.denverMeanBiasC.toFixed(2)}°C</strong>
            <p>Systematically too hot across all 24 valid dates.</p>
          </article>
          <article>
            <span>Neighborhood anomaly error</span>
            <strong>{result.diagnosis.denverAnomalyMaeC.toFixed(2)}°C</strong>
            <p>After removing the city-day level.</p>
          </article>
          <article>
            <span>Elevation extrapolation</span>
            <strong>{result.diagnosis.denverElevationM.toLocaleString()} m</strong>
            <p>
              Denver versus about {result.diagnosis.maximumTrainingCityElevationM} m
              at the top of the training city-level range.
            </p>
          </article>
          <article>
            <span>Estimated elevation term</span>
            <strong>+{result.diagnosis.denverElevationContributionC.toFixed(2)}°C</strong>
            <p>Close to the observed bias magnitude; mechanism, not a correction.</p>
          </article>
        </div>
        <div className={styles.mechanismVerdict}>
          <span>Fixed 2×2 mechanism test</span>
          <p>
            B1 reached <strong>{result.diagnosis.sourceB1MaeC.toFixed(2)}°C</strong> MAE;
            the best absolute M3 variant reached{" "}
            <strong>{result.diagnosis.bestAbsoluteVariantMaeC.toFixed(2)}°C</strong>.
            Changing elevation and aggregation support did not rescue the route.
          </p>
          <b>Absolute-temperature route stopped</b>
        </div>
      </section>

      <section className={styles.relativeSection} id="relative">
        <div className={styles.relativeHeading}>
          <span className="eyebrow light">08 · The narrower result</span>
          <h2>Predict the pattern, not the city&apos;s thermometer.</h2>
          <p>
            With absolute level removed, the same fixed spatial model estimates
            whether a neighborhood is hotter or cooler than its city on that day.
            No new city was added and no second blind claim was made.
          </p>
        </div>
        <div className={styles.relativeCards}>
          <article>
            <span>Whole-city source LOSO</span>
            <strong>{result.relative.source.improvementPercent}%</strong>
            <p>lower anomaly MAE than B1</p>
            <dl>
              <div><dt>B1</dt><dd>{result.relative.source.b1MaeC.toFixed(2)}°</dd></div>
              <div><dt>M3-relative</dt><dd>{result.relative.source.m3MaeC.toFixed(2)}°</dd></div>
            </dl>
            <small>
              95% B1−M3 CI {result.relative.source.bootstrapLowerC.toFixed(2)} to{" "}
              {result.relative.source.bootstrapUpperC.toFixed(2)}°C
            </small>
          </article>
          <article>
            <span>Opened historical stress set</span>
            <strong>{result.relative.openedStress.improvementPercent}%</strong>
            <p>lower anomaly MAE than B1</p>
            <dl>
              <div><dt>B1</dt><dd>{result.relative.openedStress.b1MaeC.toFixed(2)}°</dd></div>
              <div><dt>M3-relative</dt><dd>{result.relative.openedStress.m3MaeC.toFixed(2)}°</dd></div>
            </dl>
            <small>
              Development stress evidence only · 7 of 8 cities improved overall
            </small>
          </article>
          <article>
            <span>Fixed year stability</span>
            <strong>{result.relative.yearStability.improvementPercent}%</strong>
            <p>lower anomaly MAE across {result.relative.yearStability.folds} folds</p>
            <dl>
              <div><dt>B1</dt><dd>{result.relative.yearStability.b1MaeC.toFixed(2)}°</dd></div>
              <div><dt>M3-relative</dt><dd>{result.relative.yearStability.m3MaeC.toFixed(2)}°</dd></div>
            </dl>
            <small>Temporal stability, not unseen-city confirmation</small>
          </article>
        </div>
        <div className={styles.fixedModelStrip}>
          <div>
            <span>Fixed development model</span>
            <strong>{result.relative.fixedModel.featureCount} spatial features</strong>
          </div>
          <p>
            {result.relative.fixedModel.trainingRows.toLocaleString()} source rows ·{" "}
            {result.relative.fixedModel.cityDates} city-dates ·{" "}
            {result.relative.fixedModel.predictionKeys.toLocaleString()} verified prediction keys
          </p>
          <a
            href={repositoryRoot + "/docs/M3_RELATIVE_TEMPERATURE_REVIEW.zh-CN.md"}
            rel="noreferrer"
            target="_blank"
          >
            Read the complete review ↗
          </a>
        </div>
      </section>

      <section className={styles.finalNote}>
        <span>Where the line ends</span>
        <h2>Absolute M3 failed. Relative M3 is a development result.</h2>
        <p>
          The existing 23-feature relative model remains the stage default and
          model search is paused. Existing absolute-temperature outputs are
          retained with their original limits; this result does not rescue the
          failed cross-city blind test. Any future confirmation still requires
          a genuinely untouched cohort.
          Landsat LST is a clear-sky surface-heat proxy—not air temperature,
          exposure, illness, or causation.
        </p>
      </section>

      <footer className={styles.footer}>
        <div>
          <strong>The M3 research record</strong>
          <span>Los Angeles to eight-city development</span>
        </div>
        <p>
          One continuous line · failure preserved · narrower result labeled ·{" "}
          <Link href="/">Return to the Los Angeles atlas.</Link>
        </p>
      </footer>
    </main>
  );
}
