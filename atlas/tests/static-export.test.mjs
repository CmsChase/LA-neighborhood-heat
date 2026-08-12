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

test("exports an explicitly target-sealed four-city preview", async () => {
  const html = await readFile(
    new URL("../out/cities/index.html", import.meta.url),
    "utf8",
  );
  const dataContract = await readFile(
    new URL("../app/cities/comparison-data.ts", import.meta.url),
    "utf8",
  );
  const generatedResults = await readFile(
    new URL("../app/cities/generated-results.ts", import.meta.url),
    "utf8",
  );

  assert.match(html, /One frozen model contract/);
  assert.match(html, /no real fit has occurred yet/);
  assert.match(html, /aria-label="Scrollable four-city performance table"/);
  assert.match(html, /Preview · targets sealed/);
  assert.match(html, /Result slots are intentionally empty/);
  assert.match(html, /External targets sealed/);
  assert.match(html, /No cross-city outcome values are bundled/);
  assert.match(html, /Los Angeles/);
  assert.match(html, /Phoenix/);
  assert.match(html, /Houston/);
  assert.match(html, /Chicago/);
  assert.match(html, /cities\[\*\]\.results/);
  assert.match(dataContract, /state: "preview"/);
  assert.match(dataContract, /claimId: null/);
  assert.match(dataContract, /evidenceFigures: \[\]/);
  assert.equal((dataContract.match(/^\s{6}results: null,/gm) ?? []).length, 4);
  assert.match(
    dataContract,
    /Preview releases cannot contain a claim ID or result values/,
  );
  assert.match(dataContract, /historical_source_reference/);
  assert.match(dataContract, /authenticated_external_confirmation/);
  assert.match(generatedResults, /GENERATED_VERIFIED_RELEASE: unknown = null/);
  assert.doesNotMatch(html, /Six views\. One frozen claim\./);
});

test("keeps a verified rendering branch without inventing external results", async () => {
  const page = await readFile(
    new URL("../app/cities/page.tsx", import.meta.url),
    "utf8",
  );
  const panel = await readFile(
    new URL("../app/cities/ComparisonPanel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(page, /data\.release\.state === "verified"/);
  assert.match(page, /Verified · external confirmation/);
  assert.match(page, /Los Angeles remains the historical source reference/);
  assert.match(panel, /External confirmation results are authenticated/);
  assert.match(panel, /Authenticated external result/);
  assert.match(panel, /Historical LA reference/);
  assert.match(page, /data\.evidenceFigures\.map/);
  assert.match(page, /Six views\. One frozen claim\./);
  assert.match(page, /ASSET_BASE_PATH/);
  assert.match(page, /Source record/);
});
