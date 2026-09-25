"use strict";

// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
const PALETTE = ["#2f6fdf", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0c8599", "#c2255c", "#5c7cfa"];
const PERCENTILES = ["p50", "p90", "p99", "p999"];

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Index entries to show, oldest first. Smoke runs show only on request.
function visibleRuns(entries, showSmoke) {
  return entries
    .filter((entry) => showSmoke || !entry.smoke)
    .sort((a, b) => (a.started < b.started ? -1 : a.started > b.started ? 1 : 0));
}

// One point of one target in one run: the median over the ok cells.
// A target without an ok cell in the run is a gap.
function historyPoint(cells) {
  const ok = cells.filter((cell) => cell.status === "ok");
  const peaks = ok.filter((cell) => cell.peak !== null).map((cell) => cell.peak);
  const helds = ok.filter((cell) => cell.held !== null).map((cell) => cell.held.rate);
  const flags = new Set();
  ok.forEach((cell) => Object.keys(cell.flags).forEach((name) => flags.add(name)));
  return {
    peak: peaks.length ? median(peaks) : null,
    held: helds.length ? median(helds) : null,
    floor: flags.has("generator_bound"),
    flags: Array.from(flags).sort(),
  };
}

// Series per app and target over the runs, which come in started order.
function historySeries(runs) {
  const apps = {};
  runs.forEach((run) => {
    run.cells.forEach((cell) => {
      apps[cell.target.app] = apps[cell.target.app] || {};
      apps[cell.target.app][cell.target.name] = { peak: [], held: [], floor: [], flags: [] };
    });
  });
  runs.forEach((run) => {
    Object.keys(apps).forEach((app) => {
      Object.keys(apps[app]).forEach((name) => {
        const point = historyPoint(run.cells.filter((cell) => cell.target.name === name));
        const series = apps[app][name];
        series.peak.push(point.peak);
        series.held.push(point.held);
        series.floor.push(point.floor);
        series.flags.push(point.flags);
      });
    });
  });
  const labels = runs.map((run) => run.rapira.sha.slice(0, 7) + " " + run.started.slice(0, 10));
  return { labels, apps };
}

function flagText(flags) {
  return Object.keys(flags || {})
    .sort()
    .map((name) => (flags[name] === true ? name : name + "(" + JSON.stringify(flags[name]) + ")"))
    .join(", ");
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function number(value) {
  return value === null || value === undefined ? "-" : Math.round(value).toLocaleString("en-US");
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(path + ": HTTP " + response.status);
  }
  return response.json();
}

const state = { entries: [], docs: new Map(), charts: [] };

async function loadRun(id) {
  if (!state.docs.has(id)) {
    state.docs.set(id, await fetchJson("data/" + id + ".json"));
  }
  return state.docs.get(id);
}

function addChart(parent, config) {
  const box = el("div");
  box.className = "chart";
  const canvas = el("canvas");
  box.appendChild(canvas);
  parent.appendChild(box);
  state.charts.push(new Chart(canvas, config));
}

function drawHistory(history) {
  const root = document.getElementById("history-charts");
  root.replaceChildren();
  Object.keys(history.apps)
    .sort()
    .forEach((app) => {
      root.appendChild(el("h3", app));
      const datasets = [];
      Object.keys(history.apps[app])
        .sort()
        .forEach((name, i) => {
          const series = history.apps[app][name];
          const color = PALETTE[i % PALETTE.length];
          datasets.push({
            label: name + " peak",
            data: series.peak,
            borderColor: color,
            backgroundColor: color,
            pointStyle: series.floor.map((floor) => (floor ? "triangle" : "circle")),
            pointRadius: series.floor.map((floor) => (floor ? 7 : 3)),
            flags: series.flags,
          });
          datasets.push({
            label: name + " held",
            data: series.held,
            borderColor: color,
            backgroundColor: color,
            borderDash: [6, 4],
            pointRadius: 2,
            flags: series.flags,
          });
        });
      addChart(root, {
        type: "line",
        data: { labels: history.labels, datasets },
        options: {
          spanGaps: false,
          scales: { y: { title: { display: true, text: "req/s" }, beginAtZero: true } },
          plugins: {
            tooltip: {
              callbacks: {
                afterLabel: (item) => {
                  const flags = item.dataset.flags[item.dataIndex];
                  return flags.length ? "flags: " + flags.join(", ") : "";
                },
              },
            },
          },
        },
      });
    });
}

function stageTable(cell) {
  const table = el("table");
  const head = el("tr");
  ["stage", "rate", "pass", "achieved req/s", "successful req/s", "p50 us", "p99 us", "flags"].forEach((title) =>
    head.appendChild(el("th", title))
  );
  table.appendChild(head);
  cell.stages.forEach((stage, i) => {
    const row = el("tr");
    row.appendChild(el("td", String(i)));
    row.appendChild(el("td", number(stage.rate)));
    row.appendChild(el("td", stage.pass ? "pass" : "fail: " + stage.fail_reason));
    row.appendChild(el("td", number(stage.merged.achieved_rps)));
    row.appendChild(el("td", number(stage.merged.successful_rps)));
    row.appendChild(el("td", number(stage.latency_us.p50)));
    row.appendChild(el("td", number(stage.latency_us.p99)));
    row.appendChild(el("td", flagText(stage.flags)));
    if (!stage.pass) {
      row.className = "fail";
    }
    table.appendChild(row);
  });
  return table;
}

function drawRun(entry, run) {
  const meta = document.getElementById("run-meta");
  meta.textContent =
    "rapira " + entry.rapira_version + " (" + entry.rapira_sha.slice(0, 7) + "), suite " + entry.suite +
    ", " + run.rig.server_type + " server, " + run.rig.loader_count + " x " + run.rig.loader_type +
    " loaders, " + run.processes + " workers, status " + entry.status;
  const root = document.getElementById("run-cells");
  root.replaceChildren();
  run.cells.forEach((cell) => {
    const section = el("section");
    section.className = "cell";
    section.appendChild(el("h3", cell.key));
    const summary =
      cell.status === "ok"
        ? "held " + number(cell.held && cell.held.rate) + " req/s, peak " + number(cell.peak) + " req/s"
        : cell.status + (cell.reason ? ": " + cell.reason : "");
    section.appendChild(el("p", summary));
    const flags = flagText(cell.flags);
    if (flags) {
      section.appendChild(el("p", "flags: " + flags));
    }
    if (cell.stages.length) {
      addChart(section, {
        type: "line",
        data: {
          datasets: PERCENTILES.map((key, i) => ({
            label: key,
            data: cell.stages.map((stage) => ({ x: stage.rate, y: stage.latency_us[key] })),
            borderColor: PALETTE[i],
            backgroundColor: PALETTE[i],
          })),
        },
        options: {
          scales: {
            x: { type: "logarithmic", title: { display: true, text: "rate req/s" } },
            y: { type: "logarithmic", title: { display: true, text: "latency us" } },
          },
        },
      });
      section.appendChild(stageTable(cell));
    }
    root.appendChild(section);
  });
}

async function render() {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
  const showSmoke = document.getElementById("smoke").checked;
  const entries = visibleRuns(state.entries, showSmoke).slice(-HISTORY_RUNS);
  const runs = await Promise.all(entries.map((entry) => loadRun(entry.id)));
  drawHistory(historySeries(runs));
  const select = document.getElementById("run-select");
  const current = select.value;
  select.replaceChildren();
  entries
    .slice()
    .reverse()
    .forEach((entry) => {
      const option = el("option", entry.started + " " + entry.suite + " " + entry.rapira_sha.slice(0, 7));
      option.value = entry.id;
      select.appendChild(option);
    });
  if (entries.some((entry) => entry.id === current)) {
    select.value = current;
  }
  const entry = entries.find((item) => item.id === select.value);
  if (entry) {
    drawRun(entry, await loadRun(entry.id));
  }
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
  Chart.defaults.maintainAspectRatio = false;
  state.entries = (await fetchJson("data/index.json")).runs;
  document.getElementById("smoke").addEventListener("change", () => render().catch(showError));
  document.getElementById("run-select").addEventListener("change", () => render().catch(showError));
  await render();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, historySeries };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
