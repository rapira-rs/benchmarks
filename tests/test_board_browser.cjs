"use strict";

// Run with node --test. PLAYWRIGHT_CORE and CHROMIUM can select local installations.
const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "playwright-core");
const runs = require("./fixtures/board_runs.json").slice(0, 2);
const board = path.join(__dirname, "..", "board");
const prA = "https://github.com/rapira-rs/rapira/pull/101";
const commitB = "https://github.com/rapira-rs/rapira/commit/bbbbbbb0000000000000000000000000000000b2";

// The hello capacity of run a is 104000 req/s. The new medians 114400 and 124800 are 10% and 20% above it. The
// product of the factors 1 + delta_pct / 100, 1.1 x 1.04, would give 14.4%.
const history = JSON.parse(JSON.stringify([runs[0], runs[1], runs[1]]));
history[1].summary["hello-rapira-worker"].capacity = { pairs: 3, delta_pct: 10, min_pct: 9, max_pct: 11, base: 104000, new: 114400, flags: [] };
history[2].id = "20260928T010000Z-ci-ddddddd";
history[2].started = "2026-09-28T01:00:00Z";
history[2].rapira.sha = "ddddddd0000000000000000000000000000000d4";
history[2].base = runs[1].rapira;
history[2].summary["hello-rapira-worker"].capacity = { pairs: 3, delta_pct: 4, min_pct: 3, max_pct: 5, base: 120000, new: 124800, flags: [] };
const longHistory = [history[0], history[1], ...Array.from({ length: 61 }, (_, i) => ({
  ...history[2], id: "later-" + i, started: new Date(Date.UTC(2026, 8, 28 + i, 1)).toISOString(),
}))];

const cases = [
  { name: "desktop hover at a zero-pair gap hides the previous tooltip", target: "grpc-rapira", index: 1, from: 0, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a zero-pair gap opens its run", target: "grpc-rapira", index: 1, action: "click", tooltip: null, links: [commitB] },
  { name: "desktop hover at a missing target hides the tooltip", target: "hello-rapira-dispatcher", index: 0, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a missing target opens its run", target: "hello-rapira-dispatcher", index: 0, action: "click", tooltip: null, links: [prA] },
  { name: "desktop hover from a gap to a point shows the recorded base", target: "grpc-rapira", index: 0, from: 1, action: "hover", tooltip: ["2026-09-26 01:00 UTC - #101", "measured base ccccccc"], links: [] },
  { name: "desktop hover from a missing target to a point keeps the tooltip in the plot", target: "hello-rapira-dispatcher", index: 1, from: 0, action: "hover", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "measured base aaaaaaa"], links: [] },
  { name: "desktop click at a measurement opens its run", target: "hello-rapira-worker", index: 1, action: "click", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "measured base aaaaaaa"], links: [commitB] },
  { name: "touch tap in the plot shows a tooltip without navigation", touch: true, target: "grpc-rapira", index: 0, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "measured base ccccccc"], links: [] },
  { name: "touch tap at a gap hides the previous tooltip without navigation", touch: true, target: "grpc-rapira", index: 1, from: 0, action: "tap", tooltip: null, links: [] },
  { name: "touch tap on a point opens its run", touch: true, target: "grpc-rapira", index: 0, point: true, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "measured base ccccccc"], links: [prA] },
  { name: "the chart plots the change of the new median from the baseline run", runs: history, target: "hello-rapira-worker", index: 2, action: "hover", tooltip: ["2026-09-28 01:00 UTC - ddddddd", "change vs aaaaaaa (104000 req/s) +20.0%"], links: [], values: [0, 10, 20] },
  { name: "the chart keeps the baseline run after it leaves the display", runs: longHistory, target: "hello-rapira-worker", index: 59, action: "hover", tooltip: ["2026-11-27 01:00 UTC - ddddddd", "change vs aaaaaaa (104000 req/s) +20.0%"], links: [], values: Array(60).fill(20) },
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
        // +20% with 3 of 3 pairs is above the capacity floor of 8%, and a higher capacity is better.
        assert.equal(datasets[0].pointBackgroundColor.at(-1), "#1a7f37");
        assert.match(await canvas.getAttribute("aria-label"), /change from baseline/);
        assert.match(await page.locator("#baseline").textContent(), /aaaaaaa/);
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
