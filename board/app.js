"use strict";

// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
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

// Legend text of a ladder rate: 640000 is "640k", 1280000 is "1.28M".
function rateLabel(rate) {
  if (rate >= 1000000) {
    return rate / 1000000 + "M";
  }
  if (rate >= 1000) {
    return rate / 1000 + "k";
  }
  return String(rate);
}

// The lines of one target. okCells[i] holds the ok cells of the target in run i.
// A cell has at most one stage per rate, so a value is the median over the cells.
function targetLines(okCells) {
  const passed = okCells.map((cells) => cells.flatMap((cell) => cell.stages.filter((stage) => stage.pass)));
  const rates = Array.from(new Set(passed.flat().map((stage) => stage.rate))).sort((a, b) => a - b);
  const p99 = {};
  rates.forEach((rate) => {
    p99[rate] = passed.map((stages) => {
      const values = stages.filter((stage) => stage.rate === rate).map((stage) => stage.latency_us.p99);
      return values.length ? median(values) / 1000 : null;
    });
  });
  const flags = okCells.map((cells) =>
    cells.length ? Array.from(new Set(cells.flatMap((cell) => Object.keys(cell.flags)))).sort() : null
  );
  return { rates, p99_ms: p99, flags };
}

// Chart data per app and target over the runs, which come in started order.
// The apps and the targets come in name order.
function targetSeries(runs) {
  const names = {};
  runs.forEach((run) =>
    run.cells.forEach((cell) => {
      names[cell.target.app] = names[cell.target.app] || new Set();
      names[cell.target.app].add(cell.target.name);
    })
  );
  const apps = {};
  Object.keys(names)
    .sort()
    .forEach((app) => {
      apps[app] = {};
      Array.from(names[app])
        .sort()
        .forEach((name) => {
          apps[app][name] = targetLines(
            runs.map((run) => run.cells.filter((cell) => cell.target.name === name && cell.status === "ok"))
          );
        });
    });
  return {
    labels: runs.map((run) => run.rapira.sha.slice(0, 7)),
    versions: runs.map((run) => run.rapira.version || ""),
    apps,
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

function drawTarget(parent, series, target) {
  const box = el("div");
  box.className = "chart";
  const canvas = el("canvas");
  box.appendChild(canvas);
  parent.appendChild(box);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: series.labels,
      datasets: target.rates.map((rate, i) => ({
        label: rateLabel(rate),
        data: target.p99_ms[rate],
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
        spanGaps: false,
      })),
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { type: "category" },
        y: { type: "logarithmic", title: { display: true, text: "p99 ms" } },
      },
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            title: (items) => {
              const i = items[0].dataIndex;
              return series.versions[i] ? series.labels[i] + " " + series.versions[i] : series.labels[i];
            },
            label: (item) => item.dataset.label + " req/s: " + item.parsed.y.toFixed(2) + " ms",
            afterLabel: (item) => {
              const flags = target.flags[item.dataIndex];
              return flags && flags.length ? flags.join(", ") : "";
            },
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
  const runs = await Promise.all(entries.map((entry) => fetchJson("data/" + entry.id + ".json")));
  const series = targetSeries(runs);
  const root = document.getElementById("charts");
  Object.keys(series.apps).forEach((app) => {
    root.appendChild(el("h2", app));
    Object.keys(series.apps[app]).forEach((name) => {
      root.appendChild(el("h3", name));
      drawTarget(root, series, series.apps[app][name]);
    });
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, rateLabel, targetSeries };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
