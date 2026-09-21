import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const basePath = process.env.NEXT_PUBLIC_BASE_PATH;

if (!basePath) {
  throw new Error("NEXT_PUBLIC_BASE_PATH is required for the Pages export test.");
}

async function sha256(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const paths = await Promise.all(
    entries.map(async (entry) => {
      const path = `${directory}/${entry.name}`;
      return entry.isDirectory() ? walk(path) : [path];
    }),
  );
  return paths.flat();
}

test("exports a GitHub Pages shell with repository-prefixed assets", async () => {
  const html = await readFile(new URL("../out/index.html", import.meta.url), "utf8");

  assert.match(html, /LA Surface Heat Atlas/);
  assert.match(html, /Loading the verified 2025 evaluation/);
  assert.ok(html.includes(`${basePath}/_next/`));
});

test("ships exact authenticated display data and repository-prefixed fetches", async () => {
  const dataFiles = [
    "display-manifest.json",
    "evaluation-2025.json",
    "metrics.json",
    "tracts.json",
  ];

  for (const file of dataFiles) {
    const source = new URL(`../public/data/${file}`, import.meta.url);
    const exported = new URL(`../out/data/${file}`, import.meta.url);
    assert.equal(await sha256(source), await sha256(exported));
  }

  const scripts = (
    await walk(fileURLToPath(new URL("../out/_next/static", import.meta.url)))
  ).filter((path) => path.endsWith(".js"));
  const javascript = (
    await Promise.all(scripts.map((path) => readFile(path, "utf8")))
  ).join("\n");

  assert.ok(javascript.includes(`"${basePath}"`));
  assert.ok(javascript.includes("/data/tracts.json"));
  assert.ok(javascript.includes("/data/evaluation-2025.json"));
  assert.ok(
    javascript.includes(
      "Promising point estimate. The 95% uncertainty interval crosses zero",
    ),
  );
  assert.ok(javascript.includes("Explore every evaluated tract."));
  assert.ok(javascript.includes("Complete evaluation record for"));
  assert.ok(javascript.includes("Find neighborhood or GEOID"));
  assert.ok(javascript.includes("Mapping L.A. neighborhood"));
  assert.ok(javascript.includes("M2 predicted daytime LST"));
  assert.ok(javascript.includes("Darker color = hotter"));
  assert.ok(javascript.includes("Share of mapped area"));
  assert.ok(javascript.includes("Mapping L.A. coverage"));
  assert.ok(!javascript.includes("Scroll to zoom"));
});

test("keeps hero annotations on-screen and distinguishes tract taps from map drags", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const explorer = await readFile(
    new URL("../app/components/TractDetailExplorer.tsx", import.meta.url),
    "utf8",
  );

  assert.match(css, /\.hero-pixel-map\s*\{[^}]*inset:\s*0;/s);
  assert.match(css, /\.hero-pixel-heading,[\s\S]*?right:\s*clamp\(/);
  assert.match(css, /\.hero-pixel-map svg\s*\{[^}]*height:\s*118%;/s);
  assert.match(explorer, /const DRAG_THRESHOLD_PX = 5;/);
  assert.match(explorer, /pendingTractIndex/);
  assert.match(explorer, /data-tract-index=\{index\}/);
  assert.match(explorer, /aria-pressed=\{isSelected\}/);
  assert.match(explorer, /finishPointer\(event, true\)/);
  assert.doesNotMatch(
    explorer,
    /onClick=\{\(event\) => \{[\s\S]*?onSelectTract\(index\);/,
  );
});

test("uses one continuous story for current and legacy research routes", async () => {
  const current = await readFile(
    new URL("../out/m3/index.html", import.meta.url),
    "utf8",
  );
  const legacy = await readFile(
    new URL("../out/cities/index.html", import.meta.url),
    "utf8",
  );

  for (const html of [current, legacy]) {
    assert.match(html, /The whole research line/);
    assert.match(html, /The first transfer study/);
    assert.match(html, /The harder M3 blind test/);
    assert.match(html, /Diagnose before expanding/);
    assert.match(html, /The narrower result/);
  }
});

test("exports the complete M3 research line without rewriting the blind result", async () => {
  const html = await readFile(
    new URL("../out/m3/index.html", import.meta.url),
    "utf8",
  );
  const renderedText = html.replaceAll("<!-- -->", "");
  const sourceSummary = new URL(
    "../public/evidence/m3/m3-blind-evaluation-summary.json",
    import.meta.url,
  );
  const exportedSummary = new URL(
    "../out/evidence/m3/m3-blind-evaluation-summary.json",
    import.meta.url,
  );

  assert.match(html, /The model failed/);
  assert.match(html, /The signal narrowed/);
  assert.match(html, /A result, not a victory/);
  assert.match(renderedText, /53\.4%/);
  assert.match(renderedText, /5\.83°/);
  assert.match(renderedText, /3\.80°/);
  assert.match(html, /−75\.8%/);
  assert.match(html, /−32\.6%/);
  assert.match(html, /Seattle/);
  assert.match(html, /Denver/);
  assert.match(html, /Atlanta/);
  assert.match(html, /Miami/);
  assert.match(html, /Five honest failures/);
  assert.match(renderedText, /\+13\.20°C/);
  assert.match(renderedText, /19\.8%/);
  assert.match(renderedText, /28\.8%/);
  assert.match(renderedText, /36%/);
  assert.match(html, /Absolute-temperature route stopped/);
  assert.match(html, /development result/);
  assert.match(html, /Fixed Colorado source addition/);
  assert.match(html, /Large errors fell\. The upgrade still failed\./);
  assert.match(renderedText, /4\.53°C/);
  assert.match(renderedText, /4\.15°C/);
  assert.match(renderedText, /96,061/);
  assert.match(renderedText, /254/);
  assert.match(html, /No upgrade/);
  assert.match(html, /Chengdu pilot-area/);
  assert.equal(await sha256(sourceSummary), await sha256(exportedSummary));
});

test("ships the generated Colorado source-addition display result", async () => {
  const source = new URL(
    "../public/data/colorado-source-addition.json",
    import.meta.url,
  );
  const exported = new URL(
    "../out/data/colorado-source-addition.json",
    import.meta.url,
  );
  const payload = JSON.parse(await readFile(source, "utf8"));

  assert.equal(await sha256(source), await sha256(exported));
  assert.equal(payload.decision, "no_upgrade");
  assert.equal(payload.support.scoredRows, 96061);
  assert.equal(payload.support.independentCityDates, 132);
  assert.equal(payload.support.spatialBlocks, 254);
  assert.equal(payload.scope.independentConfirmation, false);
  assert.equal(payload.scope.defaultModelChanged, false);
  assert.deepEqual(
    payload.cities.map(({ id }) => id),
    ["chicago_il", "houston_tx", "los_angeles_ca", "phoenix_az"],
  );
});

test("keeps the LA homepage and exports the separate four-city atlas", async () => {
  const homepage = await readFile(
    new URL("../out/index.html", import.meta.url),
    "utf8",
  );
  const fourCityPage = await readFile(
    new URL("../out/four-cities/index.html", import.meta.url),
    "utf8",
  );
  const sourcePayload = new URL(
    "../public/data/four-city-atlas.json",
    import.meta.url,
  );
  const exportedPayload = new URL(
    "../out/data/four-city-atlas.json",
    import.meta.url,
  );
  const payload = JSON.parse(await readFile(sourcePayload, "utf8"));
  const scripts = (
    await walk(fileURLToPath(new URL("../out/_next/static", import.meta.url)))
  ).filter((path) => path.endsWith(".js"));
  const javascript = (
    await Promise.all(scripts.map((path) => readFile(path, "utf8")))
  ).join("\n");

  assert.match(homepage, /LA Surface Heat Atlas/);
  assert.ok(javascript.includes("Four-city atlas"));
  assert.ok(javascript.includes("/four-cities"));
  assert.match(fourCityPage, /Four City Surface Heat Atlas/);
  assert.match(fourCityPage, /Loading the four city Atlas/);
  assert.equal(await sha256(sourcePayload), await sha256(exportedPayload));
  assert.equal(payload.state, "opened-blind-evaluation-display-only");
  assert.deepEqual(
    payload.cities.map(({ id }) => id),
    ["seattle_wa", "denver_co", "atlanta_ga", "miami_fl"],
  );
  assert.equal(
    payload.cities.reduce((total, city) => total + city.metrics.rows, 0),
    9502,
  );
});
