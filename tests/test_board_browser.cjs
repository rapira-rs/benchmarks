"use strict";

// Run with node --test. PLAYWRIGHT_CORE and CHROMIUM can select local installations.
const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "playwright-core");
const runs = require("./fixtures/board_runs.json").slice(0, 3);
const board = path.join(__dirname, "..", "board");
const prA = "https://github.com/rapira-rs/rapira/pull/101";
const commitB = "https://github.com/rapira-rs/rapira/commit/bbbbbbb0000000000000000000000000000000b2";

// Run c is the first run, so its new build ccccccc is the baseline build and its points are 0%. Runs a, b, and d
// measure ccccccc as their base build. The hello capacity deltas of runs a and b are 4.0% and -0.5%.
const history = JSON.parse(JSON.stringify([runs[0], runs[1], runs[2], runs[2]]));
history[3].id = "20260928T010000Z-ci-ddddddd";
history[3].started = "2026-09-28T01:00:00Z";
history[3].rapira.sha = "ddddddd0000000000000000000000000000000d4";
history[3].summary["hello-rapira-worker"].capacity = { pairs: 3, delta_pct: 6, min_pct: 5, max_pct: 7, base: 100000, new: 106000, flags: [] };
// 64 runs: the board loads every run, and the last 61 are copies of run d.
const longHistory = [history[0], history[1], history[2], ...Array.from({ length: 61 }, (_, i) => ({
  ...history[3], id: "later-" + i, started: new Date(Date.UTC(2026, 8, 28 + i, 1)).toISOString(),
}))];

const cases = [
  { name: "desktop hover at a zero-pair gap hides the previous tooltip", target: "grpc-rapira", index: 2, from: 1, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a zero-pair gap opens its run", target: "grpc-rapira", index: 2, action: "click", tooltip: null, links: [commitB] },
  { name: "desktop hover at a missing target hides the tooltip", target: "hello-rapira-dispatcher", index: 1, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a missing target opens its run", target: "hello-rapira-dispatcher", index: 1, action: "click", tooltip: null, links: [prA] },
  { name: "desktop hover from a gap to a point shows the measured values", target: "grpc-rapira", index: 1, from: 2, action: "hover", tooltip: ["2026-09-26 01:00 UTC - #101", "new 150750 vs base 150000 req/s"], links: [] },
  { name: "desktop hover from a missing target to a point keeps the tooltip in the plot", target: "hello-rapira-dispatcher", index: 2, from: 1, action: "hover", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "new 24000 vs base 25000 req/s"], links: [] },
  { name: "desktop click at a measurement opens its run", target: "hello-rapira-worker", index: 2, action: "click", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "new 103480 vs base 104000 req/s"], links: [commitB] },
  { name: "desktop hover at the first run shows the start", target: "hello-rapira-worker", index: 0, action: "hover", tooltip: ["2026-09-25 01:00 UTC - #100", "change vs ccccccc in run #100 (100000 req/s) 0.0% (starting point)"], links: [] },
  { name: "touch tap in the plot shows a tooltip without navigation", touch: true, target: "grpc-rapira", index: 1, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "new 150750 vs base 150000 req/s"], links: [] },
  { name: "touch tap at a gap hides the previous tooltip without navigation", touch: true, target: "grpc-rapira", index: 2, from: 1, action: "tap", tooltip: null, links: [] },
  { name: "touch tap on a point opens its run", touch: true, target: "grpc-rapira", index: 1, point: true, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "new 150750 vs base 150000 req/s"], links: [prA] },
  { name: "the chart plots the start and the paired deltas against the baseline build", runs: history, target: "hello-rapira-worker", index: 3, action: "hover", tooltip: ["2026-09-28 01:00 UTC - ddddddd", "delta vs ccccccc +6.0% (min +5.0%, max +7.0%, 3 pairs)"], links: [], values: [0, 4, -0.5, 6] },
  { name: "the chart shows every run", runs: longHistory, target: "hello-rapira-worker", index: 63, action: "hover", tooltip: ["2026-11-27 01:00 UTC - ddddddd", "delta vs ccccccc +6.0% (min +5.0%, max +7.0%, 3 pairs)"], links: [], values: [0, 4, -0.5, ...Array(61).fill(6)] },
];

let browser;
before(async () => {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM, headless: true });
});
after(async () => {
  await browser?.close();
});

for (const c of cases) {
  test(c.name, async () => {
    const page = await browser.newPage({ hasTouch: !!c.touch, isMobile: !!c.touch, viewport: c.touch ? { width: 390, height: 844 } : { width: 1280, height: 900 } });
    const errors = [];
    const data = c.runs || runs;
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("https://board-audit.invalid/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/data/index.json") {
          await route.fulfill({ json: { runs: data.map((run) => ({ id: run.id, started: run.started, schema: run.schema, smoke: run.smoke })) } });
        } else if (pathname.startsWith("/data/")) {
          await route.fulfill({ json: data.find((run) => pathname === "/data/" + run.id + ".json") });
        } else {
          await route.fulfill({ path: path.join(board, pathname === "/" ? "index.html" : pathname) });
        }
      });
      await page.addInitScript(() => {
        window.opened = [];
        window.open = (url) => window.opened.push(url);
      });
      await page.goto("https://board-audit.invalid/");
      await page.waitForFunction(() => document.querySelectorAll("canvas").length === 9);
      const canvas = page.locator(`canvas[aria-label^="capacity "][aria-label$="of ${c.target} by run"]`);
      if (c.values) {
        const datasets = await canvas.evaluate((canvas) => Chart.getChart(canvas).data.datasets);
        assert.equal(datasets.length, 1);
        assert.equal(datasets[0].data.length, c.values.length);
        datasets[0].data.forEach((value, i) => assert.ok(Math.abs(value - c.values[i]) < 1e-10));
        // +6.0% with 3 of 3 pairs and all paired deltas above zero reaches the capacity floor of 2.5%, and a higher capacity is better.
        assert.equal(datasets[0].pointBackgroundColor.at(-1), "#1a7f37");
        // The deltas of run c, +2.6% to +3.4%, would make a paired point green, but run c is the start between two rigs.
        assert.equal(datasets[0].pointBackgroundColor[0], "#8c959f");
        assert.match(await canvas.getAttribute("aria-label"), /change vs baseline build/);
        assert.equal(await page.locator("#baseline").textContent(), "Baseline build: ccccccc.");
      }
      await canvas.scrollIntoViewIfNeeded();
      const positions = await canvas.evaluate((canvas, c) => {
        const chart = Chart.getChart(canvas);
        const rect = canvas.getBoundingClientRect();
        return [c.from ?? c.index, c.index].map((index) => ({
          x: rect.left + Math.max(chart.chartArea.left + 1, Math.min(chart.chartArea.right - 1, chart.scales.x.getPixelForValue(index))),
          y: rect.top + (c.point ? chart.getDatasetMeta(0).data[index].y : chart.chartArea.top + 10),
        }));
      }, c);
      if (c.from !== undefined) {
        if (c.touch) {
          await page.touchscreen.tap(positions[0].x, positions[0].y);
        } else {
          await page.mouse.move(positions[0].x, positions[0].y);
        }
        await page.evaluate(() => new Promise(requestAnimationFrame));
      }
      const { x, y } = positions[1];
      if (c.action === "tap") {
        await page.touchscreen.tap(x, y);
      } else if (c.action === "click") {
        await page.mouse.click(x, y);
      } else {
        await page.mouse.move(x, y);
      }
      await page.evaluate(() => new Promise(requestAnimationFrame));
      assert.deepEqual(await page.evaluate(() => window.opened), c.links);
      const tooltip = canvas.locator("..").locator(".tooltip");
      assert.equal(await tooltip.isVisible(), c.tooltip !== null);
      if (c.tooltip) {
        const lines = await tooltip.locator("div").allTextContents();
        assert.equal(lines[0], c.tooltip[0]);
        assert.ok(lines.includes(c.tooltip[1]), JSON.stringify(lines));
        const plotBox = await canvas.boundingBox();
        const tipBox = await tooltip.boundingBox();
        assert.ok(tipBox.x >= plotBox.x && tipBox.x + tipBox.width <= plotBox.x + plotBox.width + 1, JSON.stringify({ plotBox, tipBox }));
      }
      assert.deepEqual(errors, []);
    } finally {
      await page.close();
    }
  });
}
