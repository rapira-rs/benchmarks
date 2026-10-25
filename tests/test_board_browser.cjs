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

const cases = [
  { name: "desktop hover at a zero-pair gap hides the previous tooltip", target: "grpc-rapira", index: 1, from: 0, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a zero-pair gap opens its run", target: "grpc-rapira", index: 1, action: "click", tooltip: null, links: [commitB] },
  { name: "desktop hover at a missing target hides the tooltip", target: "hello-rapira-dispatcher", index: 0, action: "hover", tooltip: null, links: [] },
  { name: "desktop click at a missing target opens its run", target: "hello-rapira-dispatcher", index: 0, action: "click", tooltip: null, links: [prA] },
  { name: "desktop hover from a gap to a point shows the recorded base", target: "grpc-rapira", index: 0, from: 1, action: "hover", tooltip: ["2026-09-26 01:00 UTC - #101", "base ccccccc"], links: [] },
  { name: "desktop hover from a missing target to a point keeps the tooltip in the plot", target: "hello-rapira-dispatcher", index: 1, from: 0, action: "hover", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "base bbbbbbb"], links: [] },
  { name: "desktop click at a measurement opens its run", target: "hello-rapira-worker", index: 1, action: "click", tooltip: ["2026-09-27 01:00 UTC - bbbbbbb", "base bbbbbbb"], links: [commitB] },
  { name: "touch tap in the plot shows a tooltip without navigation", touch: true, target: "grpc-rapira", index: 0, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "base ccccccc"], links: [] },
  { name: "touch tap at a gap hides the previous tooltip without navigation", touch: true, target: "grpc-rapira", index: 1, from: 0, action: "tap", tooltip: null, links: [] },
  { name: "touch tap on a point opens its run", touch: true, target: "grpc-rapira", index: 0, point: true, action: "tap", tooltip: ["2026-09-26 01:00 UTC - #101", "base ccccccc"], links: [prA] },
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
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("https://board-audit.invalid/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/data/index.json") {
          await route.fulfill({ json: { runs } });
        } else if (pathname.startsWith("/data/")) {
          await route.fulfill({ json: runs.find((run) => pathname === "/data/" + run.id + ".json") });
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
      const canvas = page.locator(`canvas[aria-label="capacity delta (%) of ${c.target} by run"]`);
      await canvas.scrollIntoViewIfNeeded();
      const positions = await canvas.evaluate((canvas, c) => {
        const chart = Chart.getChart(canvas);
        const rect = canvas.getBoundingClientRect();
        return [c.from ?? c.index, c.index].map((index) => ({
          x: rect.left + Math.max(chart.chartArea.left + 1, Math.min(chart.chartArea.right - 1, chart.scales.x.getPixelForValue(index))),
          y: rect.top + (c.point ? chart.getDatasetMeta(2).data[index].y : chart.chartArea.top + 10),
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
