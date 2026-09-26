"use strict";

// The board draws run files of this schema only.
const RUN_SCHEMA = "rapira-bench-run/2";
// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
const COMMITS = "https://github.com/rapira-rs/rapira/commit/";
const INK = "#000000";
const LINE = "#2f6fdf";
const GRID = "rgba(0, 0, 0, 0.07)";
const FRAME = "#cccccc";
const CROSSHAIR = "#607d8b";
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif';
// The distance in pixels between a point and its tooltip.
const TIP_OFFSET = 10;
// Every target gets one chart per measure, in this order.
const MEASURES = [
  { key: "p99_ms", name: "p99", axis: "p99 latency (ms)", unit: "ms" },
  { key: "rss_mib", name: "RSS", axis: "RSS (MiB)", unit: "MiB" },
];

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Manifest entries to show, oldest first. Smoke runs are not shown.
function visibleRuns(entries) {
  return entries
    .filter((entry) => !entry.smoke)
    .sort((a, b) => (a.started < b.started ? -1 : a.started > b.started ? 1 : 0));
}

// The x label of a run: the pull request number, or the first 7 characters of the sha without one.
function runLabel(run) {
  return run.rapira.pr ? "#" + run.rapira.pr.number : run.rapira.sha.slice(0, 7);
}

// The page that a click in a chart opens: the pull request, or the commit on GitHub.
function runLink(run) {
  return run.rapira.pr ? run.rapira.pr.url : COMMITS + run.rapira.sha;
}

// The values of one target in one run: the medians over its ok cells, or nulls without one.
function runPoint(cells) {
  if (!cells.length) {
    return { p99_ms: null, rss_mib: null, achieved: null, rate: null, held: null, flags: null };
  }
  return {
    p99_ms: median(cells.map((cell) => cell.latency_us.p99)) / 1000,
    rss_mib: median(cells.map((cell) => cell.rss_kb)) / 1024,
    achieved: median(cells.map((cell) => cell.achieved_rps)),
    rate: cells[0].rate,
    held: cells.every((cell) => cell.held),
    flags: Array.from(new Set(cells.flatMap((cell) => Object.keys(cell.flags)))).sort(),
  };
}

// Chart data of every target over the runs, which come in started order. The targets come in name order.
function targetSeries(runs) {
  const names = Array.from(new Set(runs.flatMap((run) => run.cells.map((cell) => cell.target.name)))).sort();
  const targets = {};
  names.forEach((name) => {
    const points = runs.map((run) =>
      runPoint(run.cells.filter((cell) => cell.target.name === name && cell.status === "ok"))
    );
    targets[name] = {
      p99_ms: points.map((point) => point.p99_ms),
      rss_mib: points.map((point) => point.rss_mib),
      achieved: points.map((point) => point.achieved),
      rate: points.map((point) => point.rate),
      held: points.map((point) => point.held),
      flags: points.map((point) => point.flags),
    };
  });
  return {
    labels: runs.map(runLabel),
    // started is an ISO 8601 UTC time, for example 2026-09-26T19:10:13Z.
    dates: runs.map((run) => run.started.slice(0, 16).replace("T", " ") + " UTC"),
    links: runs.map(runLink),
    titles: runs.map((run) => (run.rapira.pr ? run.rapira.pr.title : "")),
    targets,
  };
}

// The change of values[i] in percent from the first value that is not null.
function sinceStart(values, i) {
  const start = values.find((value) => value !== null);
  return ((values[i] - start) / start) * 100;
}

// The hover lines of the point i of one target and one measure. A lower p99 and a lower RSS are better, so the
// value line has the tone "worse" after an increase since the start and "better" after a decrease.
function tooltipLines(series, name, key, unit, i) {
  const target = series.targets[name];
  const change = sinceStart(target[key], i);
  const lines = [{ text: series.dates[i] + " - " + series.labels[i], tone: "" }];
  if (series.titles[i]) {
    lines.push({ text: series.titles[i], tone: "" });
  }
  lines.push({
    text: target[key][i].toFixed(2) + " " + unit + " (" + (change > 0 ? "+" : "") + change.toFixed(2) + "% since start)",
    tone: change > 0 ? "worse" : change < 0 ? "better" : "",
  });
  lines.push({
    text: Math.round(target.achieved[i]) + " of " + target.rate[i] + " req/s, held " + (target.held[i] ? "yes" : "no"),
    tone: "",
  });
  if (target.flags[i].length) {
    lines.push({ text: target.flags[i].join(", "), tone: "" });
  }
  return lines;
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(path + ": HTTP " + response.status);
  }
  return response.json();
}

// A thin frame around the plot area, and a dashed vertical line at the point under the pointer.
const plotMarks = {
  id: "plotMarks",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: area } = chart;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = FRAME;
    ctx.strokeRect(area.left + 0.5, area.top + 0.5, area.width - 1, area.height - 1);
    const active = chart.getActiveElements();
    if (active.length) {
      const x = Math.round(active[0].element.x) + 0.5;
      ctx.strokeStyle = CROSSHAIR;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
    }
    ctx.restore();
  },
};

// One chart of one measure of one target over the runs.
function drawChart(parent, series, name, measure) {
  const box = el("div");
  const title = el("h2", name + " · " + measure.name);
  title.className = "title";
  const plot = el("div");
  plot.className = "plot";
  const canvas = el("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", measure.axis + " of " + name + " by run");
  const tip = el("div");
  tip.className = "tooltip";
  tip.hidden = true;
  plot.append(canvas, tip);
  box.append(title, plot);
  parent.appendChild(box);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: series.labels,
      datasets: [{
        data: series.targets[name][measure.key],
        borderColor: LINE,
        backgroundColor: LINE,
        borderWidth: 1,
        pointRadius: 2,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      // A touch screen has no hover. There, a tap in the plot shows the tooltip, and only a tap on a point opens the run.
      onClick: (event, elements, chart) => {
        const hits = matchMedia("(hover: none)").matches
          ? chart.getElementsAtEventForMode(event, "nearest", { intersect: true }, false)
          : elements;
        if (hits.length) {
          window.open(series.links[hits[0].index], "_blank", "noopener");
        }
      },
      scales: {
        x: { grid: { display: false }, border: { display: false } },
        y: {
          min: 0,
          grace: "20%",
          grid: { color: GRID },
          border: { display: false },
          title: { display: true, text: measure.axis, font: { weight: "bold" } },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: false,
          // Shows the text right of the point and below it, or left of the point when the text does not fit on the right.
          external: ({ chart, tooltip }) => {
            if (!tooltip.opacity) {
              tip.hidden = true;
              canvas.style.cursor = "";
              return;
            }
            const lines = tooltipLines(series, name, measure.key, measure.unit, tooltip.dataPoints[0].dataIndex);
            tip.replaceChildren(...lines.map((line) => {
              const node = el("div", line.text);
              node.className = line.tone;
              return node;
            }));
            tip.hidden = false;
            canvas.style.cursor = "pointer";
            // An absolute box shrinks to the space right of its left edge, so measure it at the left edge of the plot.
            tip.style.left = "0px";
            const width = tip.offsetWidth;
            const right = tooltip.caretX + TIP_OFFSET;
            const left = right + width > chart.width ? Math.max(0, tooltip.caretX - TIP_OFFSET - width) : right;
            tip.style.left = left + "px";
            tip.style.top = tooltip.caretY + TIP_OFFSET + "px";
          },
        },
      },
    },
    plugins: [plotMarks],
  });
}

function showError(error) {
  const node = document.getElementById("error");
  node.textContent = String(error);
  node.hidden = false;
}

async function main() {
  Chart.defaults.color = INK;
  Chart.defaults.font.family = SANS;
  const entries = visibleRuns((await fetchJson("data/index.json")).runs).slice(-HISTORY_RUNS);
  const loaded = await Promise.all(entries.map((entry) => fetchJson("data/" + entry.id + ".json")));
  const series = targetSeries(loaded.filter((run) => run.schema === RUN_SCHEMA));
  const root = document.getElementById("charts");
  Object.keys(series.targets).forEach((name) => {
    MEASURES.forEach((measure) => drawChart(root, series, name, measure));
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, runLabel, runLink, targetSeries, sinceStart, tooltipLines };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
