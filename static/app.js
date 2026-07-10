const REFRESH_MS = window.__REFRESH_MS__ || 60000;

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
