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
  )
    .filter((path) => path.endsWith(".js"));
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
  assert.doesNotMatch(explorer, /onClick=\{\(event\) => \{[\s\S]*?onSelectTract\(index\);/);
});
