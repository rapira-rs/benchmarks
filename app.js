"use strict";

// The board draws run files of this schema only.
const RUN_SCHEMA = "rapira-bench-run/4";
// The board loads the newest runs up to this limit.
const HISTORY_RUNS = 60;
const COMMITS = "https://github.com/rapira-rs/rapira/commit/";
const INK = "#000000";
const LINE = "#2f6fdf";
const ZERO = "#999999";
const GRID = "rgba(0, 0, 0, 0.07)";
const FRAME = "#cccccc";
const CROSSHAIR = "#607d8b";
// The point color of each tone.
const TONE_COLORS = { better: "#1a7f37", worse: "#cf222e", "": "#8c959f" };
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif';
// The distance in pixels between a point and its tooltip.
const TIP_OFFSET = 10;
// The noise floor of each measure in percent: the largest |paired delta| of the measure over all targets of the A/A
// run of 2026-09-27, rounded up to 0.5%. The p99 floor leaves out grpc-rapira, which has its own floor. NOTES.md
// holds the A/A results.
const NOISE_FLOOR_PCT = { capacity: 2.5, p99: 3, rss: 2 };
// The floors of one target that replace the floor of the measure: the largest |paired delta| of the target in the
// same A/A run, rounded up to 0.5%.
const TARGET_NOISE_FLOOR_PCT = { "grpc-rapira": { p99: 19.5 } };
// Each chart shows the paired delta of the new build against the baseline build. Tooltip medians use the given scale
// and digits.
const MEASURES = [
  { key: "capacity", name: "capacity", axis: "capacity delta vs baseline build (%)", unit: "req/s", scale: 1, digits: 0, higherIsBetter: true },
  { key: "p99", name: "p99", axis: "p99 delta vs baseline build (%)", unit: "ms", scale: 1 / 1000, digits: 2, higherIsBetter: false },
  { key: "rss", name: "RSS", axis: "RSS delta vs baseline build (%)", unit: "MiB", scale: 1 / 1024, digits: 1, higherIsBetter: false },
];

// Manifest entries to show, oldest first. Smoke runs and runs of another schema are not shown.
function visibleRuns(entries) {
  return entries
    .filter((entry) => !entry.smoke && entry.schema === RUN_SCHEMA)
    .sort((a, b) => (a.started < b.started ? -1 : a.started > b.started ? 1 : 0));
}

// The values that the runs of one series share: the base build, the rounds, the connections, the stages with their
// rates, the instance types, the loader count, the php.ini text, and the app hashes.
function seriesKey(run) {
  return JSON.stringify([
    run.base.sha, run.suite.rounds, run.suite.connections, run.suite.stages, run.rig.server_type, run.rig.loader_type,
    run.rig.loader_count, run.servers.php_ini, run.apps,
  ]);
}

// The x label of a run: the pull request number, or the first 7 characters of the sha without one.
function runLabel(run) {
  return run.rapira.pr ? "#" + run.rapira.pr.number : run.rapira.sha.slice(0, 7);
}

// The page that a click in a chart opens: the pull request, or the commit on GitHub.
function runLink(run) {
  return run.rapira.pr ? run.rapira.pr.url : COMMITS + run.rapira.sha;
}

// Runs arrive oldest first. The series has the runs with the key of the newest run. A point is the summary record of
// one target and one measure in one run: the paired delta of the new build against the base build. A run without the
// target, or a measure with 0 pairs, gives null.
function targetSeries(runs) {
  const newest = runs.at(-1);
  const displayed = runs.filter((run) => seriesKey(run) === seriesKey(newest));
  const names = Array.from(new Set(displayed.flatMap((run) => Object.keys(run.summary)))).sort();
  const targets = {};
  names.forEach((name) => {
    targets[name] = {};
    MEASURES.forEach((measure) => {
      targets[name][measure.key] = displayed.map((run) => {
        const point = run.summary[name]?.[measure.key];
        return point?.pairs ? point : null;
      });
    });
  });
  return {
    labels: displayed.map(runLabel),
    // started is an ISO 8601 UTC time, for example 2026-09-26T19:10:13Z.
    dates: displayed.map((run) => run.started.slice(0, 16).replace("T", " ") + " UTC"),
    links: displayed.map(runLink),
    titles: displayed.map((run) => (run.rapira.pr ? run.rapira.pr.title : "")),
    // The base build of every run of the series.
    baseline: newest?.base.sha ?? "",
    rounds: displayed.map((run) => run.suite.rounds),
    targets,
  };
}

// A point has a tone when it has one pair per round, all its paired deltas are on one side of zero, and |delta_pct| is
// at least the noise floor: the floor of the target for the measure, or else the floor of the measure.
function tone(point, target, measure, rounds) {
  if (!point || point.pairs < rounds) {
    return "";
  }
  const up = point.min_pct > 0;
  const floor = TARGET_NOISE_FLOOR_PCT[target]?.[measure] ?? NOISE_FLOOR_PCT[measure];
  if (!(up || point.max_pct < 0) || Math.abs(point.delta_pct) < floor) {
    return "";
  }
  return up === MEASURES.find((m) => m.key === measure).higherIsBetter ? "better" : "worse";
}

// A percent value with its sign, for example +4.0%.
function percent(value) {
  return (value > 0 ? "+" : "") + value.toFixed(1) + "%";
}

// The hover lines of point i of one target and measure. The point is not null.
function tooltipLines(series, name, measure, i) {
  const spec = MEASURES.find((m) => m.key === measure);
  const point = series.targets[name][measure][i];
  const value = (number) => (number * spec.scale).toFixed(spec.digits);
  const lines = [{ text: series.dates[i] + " - " + series.labels[i], tone: "" }];
  if (series.titles[i]) {
    lines.push({ text: series.titles[i], tone: "" });
  }
  lines.push({ text: "new " + value(point.new) + " vs base " + value(point.base) + " " + spec.unit, tone: "" });
  lines.push({
    text: "delta vs " + series.baseline.slice(0, 7) + " " + percent(point.delta_pct) + " (min " + percent(point.min_pct) +
      ", max " + percent(point.max_pct) + ", " + point.pairs + (point.pairs === 1 ? " pair)" : " pairs)"),
    tone: tone(point, name, measure, series.rounds[i]),
  });
  if (point.flags.length) {
    lines.push({ text: point.flags.join(", "), tone: "" });
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

// A thin frame around the plot area, a line at zero, and a dashed vertical line at the point under the pointer.
const plotMarks = {
  id: "plotMarks",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: area } = chart;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = FRAME;
    ctx.strokeRect(area.left + 0.5, area.top + 0.5, area.width - 1, area.height - 1);
    const zero = Math.round(chart.scales.y.getPixelForValue(0)) + 0.5;
    ctx.strokeStyle = ZERO;
    ctx.beginPath();
    ctx.moveTo(area.left, zero);
    ctx.lineTo(area.right, zero);
    ctx.stroke();
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
  const points = series.targets[name][measure.key];
  const colors = points.map((point, i) => TONE_COLORS[tone(point, name, measure.key, series.rounds[i])]);
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
      datasets: [
        {
          data: points.map((point) => point?.delta_pct ?? null),
          borderColor: LINE,
          borderWidth: 1,
          pointRadius: 3,
          pointBackgroundColor: colors,
          pointBorderColor: colors,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      onHover: (event, elements, chart) => {
        const i = chart.scales.x.getValueForPixel(event.x);
        tip.style.visibility = points[i] ? "" : "hidden";
      },
      // A touch screen has no hover. There, a tap in the plot shows the tooltip, and only a tap on a point opens the run.
      onClick: (event, elements, chart) => {
        const i = matchMedia("(hover: none)").matches
          ? chart.getElementsAtEventForMode(event, "nearest", { intersect: true }, false)[0]?.index
          : chart.scales.x.getValueForPixel(event.x);
        if (i !== undefined) {
          window.open(series.links[i], "_blank", "noopener");
        }
      },
      scales: {
        x: { grid: { display: false }, border: { display: false } },
        // The suggested min and max keep zero in the plot, and the data can go below and above it.
        y: {
          suggestedMin: 0,
          suggestedMax: 0,
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
            const lines = tooltipLines(series, name, measure.key, tooltip.dataPoints[0].dataIndex);
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
  const series = targetSeries(await Promise.all(entries.map((entry) => fetchJson("data/" + entry.id + ".json"))));
  if (series.baseline) {
    const link = el("a", series.baseline.slice(0, 7));
    link.href = COMMITS + series.baseline;
    document.getElementById("baseline").replaceChildren("Baseline build: ", link, ".");
  }
  const root = document.getElementById("charts");
  Object.keys(series.targets).forEach((name) => {
    MEASURES.forEach((measure) => drawChart(root, series, name, measure));
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, runLabel, runLink, targetSeries, tone, tooltipLines };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
