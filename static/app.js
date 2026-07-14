const REFRESH_MS = window.__REFRESH_MS__ || 60000;
const BRIDGE_CLEARANCE_FT = window.__BRIDGE_CLEARANCE_FT__;
const WARNING_MARGIN_FT = .2; // tint the readout amber within this margin of the threshold

const dot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const readout = document.getElementById("readout");
const readoutMeta = document.getElementById("readout-meta");
const errorBanner = document.getElementById("error-banner");

const CHART_LAYOUT = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  margin: { l: 48, r: 20, t: 16, b: 40 },
  font: { family: "IBM Plex Sans, sans-serif", color: "#7c93a8", size: 12 },
  xaxis: {
    gridcolor: "rgba(124, 147, 168, 0.15)",
    zeroline: false,
    tickformat: "%b %d\n%H:%M",
  },
  yaxis: {
    gridcolor: "rgba(124, 147, 168, 0.15)",
    zeroline: false,
    title: { text: "water level (ft)", font: { size: 11 } },
  },
  showlegend: false,
  shapes: [
    {
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: BRIDGE_CLEARANCE_FT,
      y1: BRIDGE_CLEARANCE_FT,
      line: { color: "#f2a93c", width: 1.5, dash: "dash" },
    },
  ],
  annotations: [
    {
      xref: "paper",
      x: 1,
      xanchor: "right",
      yref: "y",
      y: BRIDGE_CLEARANCE_FT,
      yshift: 10,
      text: `bridge clearance \u00b7 ${BRIDGE_CLEARANCE_FT.toFixed(2)} ft`,
      showarrow: false,
      font: { color: "#f2a93c", size: 11, family: "JetBrains Mono, monospace" },
    },
  ],
};

let chartInitialized = false;

function buildTraces(data) {
  return [
    {
      x: data.timestamps,
      y: data.raw,
      mode: "markers",
      marker: { color: "rgba(124, 147, 168, 0.35)", size: 3 },
      hoverinfo: "skip",
      name: "raw",
    },
    {
      x: data.timestamps,
      y: data.smoothed,
      mode: "lines",
      line: { color: "#35c6c4", width: 2.5, shape: "spline" },
      name: "smoothed",
      hovertemplate: "%{y:.2f} ft<br>%{x}<extra></extra>",
    },
  ];
}

function setStatus(ok) {
  dot.classList.toggle("stale", !ok);
  statusText.textContent = ok
    ? "Live \u00b7 UNCW-02 gauge feed"
    : "Feed unavailable \u2014 showing last known data";
}

function formatTimestamp(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

async function refresh() {
  try {
    const res = await fetch("/api/data");
    const data = await res.json();

    if (!data.ok) {
      throw new Error(data.error || "Unknown error");
    }

    errorBanner.classList.remove("visible");
    setStatus(true);

    readout.innerHTML = `${data.latest_value.toFixed(2)}<span class="unit">ft</span>`;
    readout.classList.toggle(
      "near-limit",
      BRIDGE_CLEARANCE_FT - data.latest_value <= WARNING_MARGIN_FT
    );
    readoutMeta.textContent = `As of ${formatTimestamp(data.latest_timestamp)} \u00b7 fetched ${formatTimestamp(data.fetched_at)}`;

    const traces = buildTraces(data);
    if (!chartInitialized) {
      Plotly.newPlot("chart", traces, CHART_LAYOUT, { displayModeBar: false, responsive: true });
      chartInitialized = true;
    } else {
      Plotly.react("chart", traces, CHART_LAYOUT, { displayModeBar: false, responsive: true });
    }
  } catch (err) {
    setStatus(false);
    errorBanner.textContent = `Could not load gauge data: ${err.message}`;
    errorBanner.classList.add("visible");
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
