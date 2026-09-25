"use strict";

// The board draws run files of this schema only.
const RUN_SCHEMA = "rapira-bench-run/2";
// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
const COMMITS = "https://github.com/rapira-rs/rapira/commit/";
const PALETTE = ["#2f6fdf", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0c8599", "#c2255c", "#5c7cfa"];

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

// The page that a click on a point opens: the pull request, or the commit on GitHub.
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
    links: runs.map(runLink),
    titles: runs.map((run) => (run.rapira.pr ? run.rapira.pr.title : "")),
    targets,
  };
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

// One chart of every target over the runs. key is p99_ms or rss_mib; scale is logarithmic or linear.
function drawChart(parent, series, key, axis, unit, scale) {
  const box = el("div");
  box.className = "chart";
  const canvas = el("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", axis + " by run");
  box.appendChild(canvas);
  parent.appendChild(box);
  const names = Object.keys(series.targets);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: series.labels,
      datasets: names.map((name, i) => ({
        label: name,
        data: series.targets[name][key],
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
      })),
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      onClick: (event, elements) => {
        if (elements.length) {
          window.open(series.links[elements[0].index], "_blank", "noopener");
        }
      },
      scales: {
        x: { type: "category" },
        y: { type: scale, title: { display: true, text: axis } },
      },
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            title: (items) => {
              const i = items[0].dataIndex;
              return series.titles[i] ? series.labels[i] + " " + series.titles[i] : series.labels[i];
            },
            label: (item) => {
              const target = series.targets[item.dataset.label];
              const i = item.dataIndex;
              return (
                item.dataset.label + ": " + item.parsed.y.toFixed(2) + " " + unit + ", " +
                Math.round(target.achieved[i]) + " of " + target.rate[i] + " req/s, held " + (target.held[i] ? "yes" : "no")
              );
            },
            afterLabel: (item) => series.targets[item.dataset.label].flags[item.dataIndex].join(", "),
          },
        },
      },
    },
  });
}

function showError(error) {
  const node = document.getElementById("error");
  node.textContent = String(error);
  node.hidden = false;
}

async function main() {
  const style = getComputedStyle(document.documentElement);
  Chart.defaults.color = style.getPropertyValue("--fg").trim();
  Chart.defaults.borderColor = style.getPropertyValue("--grid").trim();
  const entries = visibleRuns((await fetchJson("data/index.json")).runs).slice(-HISTORY_RUNS);
  const loaded = await Promise.all(entries.map((entry) => fetchJson("data/" + entry.id + ".json")));
  const series = targetSeries(loaded.filter((run) => run.schema === RUN_SCHEMA));
  const root = document.getElementById("charts");
  root.appendChild(el("h2", "p99 latency"));
  drawChart(root, series, "p99_ms", "p99 ms", "ms", "logarithmic");
  root.appendChild(el("h2", "RSS"));
  drawChart(root, series, "rss_mib", "RSS MiB", "MiB", "linear");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, runLabel, runLink, targetSeries };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
