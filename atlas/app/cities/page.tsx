import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import ComparisonPanel from "./ComparisonPanel";
import { FOUR_CITY_COMPARISON_DATA } from "./comparison-data";
import styles from "./cities.module.css";

const data = FOUR_CITY_COMPARISON_DATA;
const ASSET_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export const metadata: Metadata = {
  title:
    data.release.state === "verified"
      ? "Verified three-city confirmation · Surface Heat Atlas"
      : "Four-city comparison preview · Surface Heat Atlas",
  description:
    data.release.state === "verified"
      ? "Authenticated external confirmation results for Phoenix, Houston, and Chicago, with Los Angeles shown as the historical source reference."
      : "A target-sealed preview of the Los Angeles, Phoenix, Houston, and Chicago surface-heat transfer study interface.",
};

function roleLabel(role: (typeof data.cities)[number]["role"]) {
  return role === "source_anchor" ? "Source anchor" : "External cohort";
}

export default function CitiesPage() {
  const verified = data.release.state === "verified";
  return (
    <main className={styles.page}>
      <header className="site-header">
        <Link className="wordmark" href="/">
          <span>4C</span> Surface Heat Atlas
        </Link>
        <nav aria-label="Four-city navigation">
          <a href="#compare">Compare</a>
          <a href="#results">Result interface</a>
          {verified && <a href="#evidence">Evidence</a>}
          <a href="#protocol">Protocol</a>
          <a href="#data-interface">Data contract</a>
        </nav>
        <Link className="header-tag" href="/">
          LA evaluation
        </Link>
      </header>

      <section className={styles.hero} id="top">
        <div className={styles.heroCopy}>
          <span className={styles.releaseBadge}>
            <i aria-hidden="true" />
            {verified
              ? "Verified · external confirmation"
              : "Preview · targets sealed"}
          </span>
          <span className="eyebrow">Four-city transfer study</span>
          <h1>
            One frozen model contract.
            <br />
            <em>Four different cities.</em>
          </h1>
          <p>
            {verified
              ? "The frozen Los Angeles model has now been evaluated as one indivisible external claim in Phoenix, Houston, and Chicago. Los Angeles remains the historical source reference."
              : "This comparison frame is live before the results are. It introduces the cross-city design using target-blind inventory only; external-city performance and target values remain absent by construction."}
          </p>
          <div className={styles.heroActions}>
            <a className="primary-action" href="#compare">
              Compare the study frame <span>↓</span>
            </a>
            <Link className={styles.secondaryAction} href="/">
              View the completed LA evaluation
            </Link>
          </div>
        </div>

        <aside className={styles.cityIndex} aria-label="Cities in the comparison">
          <div className={styles.cityIndexHeader}>
            <span>Comparison window</span>
            <strong>4 / 4 cities</strong>
          </div>
          {data.cities.map((city, index) => (
            <article data-city={city.id} key={city.id}>
              <span className={styles.cityOrdinal}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className={styles.cityDot} aria-hidden="true" />
              <div>
                <strong>{city.name}</strong>
                <small>
                  {city.region} · {roleLabel(city.role)}
                </small>
              </div>
              <span className={styles.cityCode}>{city.code}</span>
            </article>
          ))}
          <div className={styles.cityIndexFooter}>
            <span>Outcome payload</span>
            <strong>{verified ? "VERIFIED · AUTHENTICATED" : "NULL · PREVIEW"}</strong>
          </div>
        </aside>
      </section>

      <section className={styles.summaryRail} aria-label="Target-blind study inventory">
        <div>
          <span>Canonical cities</span>
          <strong>{data.studyDesign.cityCount}</strong>
          <small>one source, three external</small>
        </div>
        <div>
          <span>Census tracts</span>
          <strong>{data.studyDesign.totalTracts.toLocaleString()}</strong>
          <small>new four-city support</small>
        </div>
        <div>
          <span>Planned overpasses</span>
          <strong>{data.studyDesign.plannedOverpasses}</strong>
          <small>physical target units</small>
        </div>
        <div>
          <span>Frozen predictors</span>
          <strong>{data.studyDesign.frozenPredictorCount}</strong>
          <small>same M2 contract everywhere</small>
        </div>
        <p>Protocol inventory · not outcome data</p>
      </section>

      <ComparisonPanel data={data} />

      {verified && (
        <section
          className={[styles.section, styles.evidenceSection].join(" ")}
          id="evidence"
        >
          <div className={styles.sectionHeading}>
            <div>
              <span className="eyebrow light">03 · Authenticated evidence</span>
              <h2>Six views. One frozen claim.</h2>
            </div>
            <p>
              Every figure is copied byte-for-byte from the authenticated,
              read-only evaluation report. Open any panel at full resolution or
              follow its source link to inspect the published artifact.
            </p>
          </div>

          <div className={styles.evidenceGrid}>
            {data.evidenceFigures.map((figure, index) => {
              const publicHref = `${ASSET_BASE_PATH}${figure.publicPath}`;
              return (
                <article className={styles.evidenceCard} key={figure.id}>
                  <a
                    aria-label={`Open ${figure.title} at full resolution`}
                    className={styles.evidenceImage}
                    href={publicHref}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <Image
                      alt={figure.description}
                      fill
                      sizes="(max-width: 850px) 100vw, 50vw"
                      src={publicHref}
                    />
                  </a>
                  <div className={styles.evidenceCaption}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <h3>{figure.title}</h3>
                      <p>{figure.description}</p>
                    </div>
                    <a href={figure.href} rel="noreferrer" target="_blank">
                      Source record <i aria-hidden="true">↗</i>
                    </a>
                  </div>
                </article>
              );
            })}
          </div>

          <p className={styles.evidenceBoundary}>
            Claim ID <code>{data.release.claimId}</code> · external 2025
            confirmation only · Los Angeles remains the historical source
            reference.
          </p>
        </section>
      )}

      <section
        className={[styles.section, styles.protocolSection].join(" ")}
        id="protocol"
      >
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow light">
              {verified ? "04" : "03"} · Transfer protocol
            </span>
            <h2>One route through the evidence.</h2>
          </div>
          <p>
            The sequence keeps training, calibration, and external confirmation
            separate. Phoenix, Houston, and Chicago are evaluated together, not
            as three opportunities to choose a favorable result.
          </p>
        </div>

        <div className={styles.protocolFlow}>
          <article>
            <span>01</span>
            <small>Fit</small>
            <h3>
              Los Angeles
              <br />2020–2023
            </h3>
            <p>
              {verified
                ? "The preregistered M2 pipeline was fit on source-city years only."
                : "Fit the preregistered M2 pipeline on source-city years only; no real fit has occurred yet."}
            </p>
          </article>
          <article>
            <span>02</span>
            <small>Calibrate</small>
            <h3>
              Los Angeles
              <br />2024
            </h3>
            <p>
              Freeze conformal correction and the abstention threshold without
              external labels.
            </p>
          </article>
          <article>
            <span>03</span>
            <small>Transfer</small>
            <h3>
              Three cities
              <br />2025
            </h3>
            <p>
              Apply the unchanged pipeline to one indivisible external
              confirmation cohort.
            </p>
          </article>
          <article>
            <span>04</span>
            <small>Release</small>
            <h3>
              One combined
              <br />claim
            </h3>
            <p>
              Publish only after the target transaction and result evidence are
              authenticated.
            </p>
          </article>
        </div>
      </section>

      <section className={styles.section} id="data-interface">
        <div className={styles.sectionHeading}>
          <div>
            <span className="eyebrow">
              {verified ? "05" : "04"} · Static data contract
            </span>
            <h2>Honest while empty. Ready when verified.</h2>
          </div>
          <p>
            The page consumes a versioned, runtime-validated interface. Preview
            mode rejects every result value; verified mode requires one claim ID,
            an explicit Los Angeles source reference, and complete authenticated
            metrics for all three external cities.
          </p>
        </div>

        <div className={styles.contractPanel}>
          <div className={styles.contractState}>
            <span>Current payload</span>
            <strong>{data.release.label}</strong>
            <p>{data.release.notice}</p>
            <dl>
              <div>
                <dt>schemaVersion</dt>
                <dd>{data.schemaVersion}</dd>
              </div>
              <div>
                <dt>release.state</dt>
                <dd>{data.release.state}</dd>
              </div>
              <div>
                <dt>release.claimId</dt>
                <dd>{data.release.claimId ?? "null"}</dd>
              </div>
              <div>
                <dt>cities[*].results</dt>
                <dd>{verified ? "1 source reference + 3 external" : "null × 4"}</dd>
              </div>
            </dl>
          </div>

          <div className={styles.contractSources}>
            <span>Evidence records</span>
            <p>
              Preview counts come from committed target-blind manifests. A
              verified release adds only authenticated evaluation artifacts.
            </p>
            <ul>
              {data.provenance.map((source) => (
                <li key={source.repositoryPath}>
                  <a href={source.href} rel="noreferrer" target="_blank">
                    <span>{source.label}</span>
                    <code>{source.repositoryPath}</code>
                    <i aria-hidden="true">↗</i>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className={styles.endpointNote}>
        <span>Interpretation boundary</span>
        <h2>{data.endpoint.name}</h2>
        <p>{data.endpoint.interpretation}</p>
      </section>

      <footer className={styles.footer}>
        <div>
          <strong>
            Surface Heat Atlas · {verified ? "Verified transfer study" : "Four-city preview"}
          </strong>
          <span>Los Angeles · Phoenix · Houston · Chicago</span>
        </div>
        <p>
          {verified
            ? "External results were published only after completion authentication. "
            : "No external-city target, prediction, or performance value is included. "}
          <Link href="/">Return to the completed Los Angeles atlas.</Link>
        </p>
      </footer>
    </main>
  );
}
