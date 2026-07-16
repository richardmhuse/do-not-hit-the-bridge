const REFRESH_MS = window.__REFRESH_MS__ || 60000;
const BRIDGE_CLEARANCE_FT =
  typeof window.__BRIDGE_CLEARANCE_FT__ === "number" ? window.__BRIDGE_CLEARANCE_FT__ : 4.81;
const MIN_WATER_DEPTH_FT =
  typeof window.__MIN_WATER_DEPTH_FT__ === "number" ? window.__MIN_WATER_DEPTH_FT__ : 1.86;
const WARNING_MARGIN_FT = .2; // tint the readout within this margin of either threshold

// On touch devices, use pinch-to-zoom + single-finger pan instead of the
// desktop rectangular drag-to-zoom (which is awkward with a finger).
const IS_TOUCH_DEVICE = "ontouchstart" in window || navigator.maxTouchPoints > 0;

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
  dragmode: IS_TOUCH_DEVICE ? "pan" : "zoom",
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
    {
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: MIN_WATER_DEPTH_FT,
      y1: MIN_WATER_DEPTH_FT,
      line: { color: "#ef6461", width: 1.5, dash: "dash" },
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
    {
      xref: "paper",
      x: 1,
      xanchor: "right",
      yref: "y",
      y: MIN_WATER_DEPTH_FT,
      yshift: -10,
      text: `min depth \u00b7 ${MIN_WATER_DEPTH_FT.toFixed(2)} ft`,
      showarrow: false,
      font: { color: "#ef6461", size: 11, family: "JetBrains Mono, monospace" },
    },
  ],
};

let chartInitialized = false;

function buildTraces(data) {
  const traces = [
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

  if (data.predicted_timestamps && data.predicted_timestamps.length > 1) {
    traces.push({
      x: data.predicted_timestamps,
      y: data.predicted_values,
      mode: "lines",
      line: { color: "#9d8cff", width: 2, dash: "dot", shape: "spline" },
      name: "predicted",
      hovertemplate: "%{y:.2f} ft (predicted)<br>%{x}<extra></extra>",
    });
  }

  return traces;
}

function setStatus(ok) {
  dot.classList.toggle("stale", !ok);
  statusText.textContent = ok
    ? "Live \u00b7 UNCW-02 gauge feed"
    : "Feed unavailable \u2014 showing last known data";
}

/**
 * Plotly's built-in scrollZoom config reliably handles mouse-wheel zoom
 * and pinch-zoom on map/3D subplots, but doesn't reliably translate a
 * two-finger pinch into a zoom on plain SVG cartesian charts (like this
 * one) across mobile browsers — it tends to fall through to treating it
 * as a single-finger pan, which is exactly the bug this works around.
 *
 * This attaches raw touch listeners directly to the chart div, in the
 * capture phase, so we see two-finger touches before Plotly's own
 * (bubble-phase) pan handler does. Single-finger touches are left
 * completely alone and continue to work exactly as before (normal pan).
 * Only once a second finger comes down do we take over: we read Plotly's
 * internal axis pixel<->data conversion (`_fullLayout.<axis>.p2d`) to
 * find the data coordinate under the pinch midpoint, then scale the
 * visible x/y range around that point as the two fingers move apart or
 * together, applying it via Plotly.relayout.
 */
function setupPinchZoom(gd) {
  let pinchState = null;

  function touchDistance(touches) {
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;
    return Math.hypot(dx, dy);
  }

  function touchMidpoint(touches) {
    return {
      x: (touches[0].clientX + touches[1].clientX) / 2,
      y: (touches[0].clientY + touches[1].clientY) / 2,
    };
  }

  // Plotly's axis.p2d() returns a plain number for linear axes, but a
  // Date object for date-type axes (like our x-axis, which is
  // timestamps). JS's +/- operators silently coerce Date objects to
  // strings rather than numbers in this kind of arithmetic, which is
  // what was producing garbage x-ranges. Normalize everything to a
  // millisecond number up front so the math is safe regardless of axis
  // type.
  function toMillis(v) {
    if (v instanceof Date) return v.getTime();
    if (typeof v === "number") return v;
    const t = new Date(v).getTime();
    return Number.isNaN(t) ? Number(v) : t;
  }

  function beginPinch(e) {
    const fullLayout = gd._fullLayout;
    const xa = fullLayout && fullLayout.xaxis;
    const ya = fullLayout && fullLayout.yaxis;
    if (!xa || !ya || typeof xa.p2d !== "function") {
      return; // chart not fully rendered yet - skip this gesture rather than throw
    }

    const rect = gd.getBoundingClientRect();
    const mid = touchMidpoint(e.touches);
    const localX = mid.x - rect.left - fullLayout._size.l;
    const localY = mid.y - rect.top - fullLayout._size.t;

    pinchState = {
      startDistance: touchDistance(e.touches),
      anchorDataX: toMillis(xa.p2d(localX)),
      anchorDataY: toMillis(ya.p2d(localY)),
      startXRange: xa.range.map(toMillis),
      startYRange: ya.range.map(toMillis),
      xIsDate: xa.type === "date",
      yIsDate: ya.type === "date",
    };
  }

  gd.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length === 2) {
        e.preventDefault();
        e.stopPropagation();
        beginPinch(e);
      }
    },
    { passive: false, capture: true }
  );

  gd.addEventListener(
    "touchmove",
    (e) => {
      if (e.touches.length === 2 && pinchState) {
        e.preventDefault();
        e.stopPropagation();

        const newDistance = touchDistance(e.touches);
        if (newDistance < 1) return;

        // fingers moving apart -> zoom in (narrower range); together -> zoom out
        const scale = pinchState.startDistance / newDistance;
        const MIN_SCALE = 0.05; // cap how far a single gesture can zoom in
        const MAX_SCALE = 20; // cap how far a single gesture can zoom out
        const clampedScale = Math.min(Math.max(scale, MIN_SCALE), MAX_SCALE);

        const newXMillis = pinchState.startXRange.map(
          (v) => pinchState.anchorDataX + (v - pinchState.anchorDataX) * clampedScale
        );
        const newYMillis = pinchState.startYRange.map(
          (v) => pinchState.anchorDataY + (v - pinchState.anchorDataY) * clampedScale
        );

        const newXRange = pinchState.xIsDate
          ? newXMillis.map((ms) => new Date(ms).toISOString())
          : newXMillis;
        const newYRange = pinchState.yIsDate
          ? newYMillis.map((ms) => new Date(ms).toISOString())
          : newYMillis;

        Plotly.relayout(gd, {
          "xaxis.range": newXRange,
          "yaxis.range": newYRange,
        });
      }
    },
    { passive: false, capture: true }
  );

  function endPinch(e) {
    if (e.touches.length < 2) {
      pinchState = null;
    }
  }

  gd.addEventListener("touchend", endPinch, { capture: true });
  gd.addEventListener("touchcancel", endPinch, { capture: true });
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
    const nearHigh = BRIDGE_CLEARANCE_FT - data.latest_value <= WARNING_MARGIN_FT;
    const nearLow = data.latest_value - MIN_WATER_DEPTH_FT <= WARNING_MARGIN_FT;
    readout.classList.toggle("near-limit-high", nearHigh && !nearLow);
    readout.classList.toggle("near-limit-low", nearLow);
    readoutMeta.textContent = `As of ${formatTimestamp(data.latest_timestamp)} \u00b7 fetched ${formatTimestamp(data.fetched_at)}`;

    const traces = buildTraces(data);
    const plotlyConfig = { displayModeBar: false, responsive: true, scrollZoom: false };
    if (!chartInitialized) {
      Plotly.newPlot("chart", traces, CHART_LAYOUT, plotlyConfig);
      chartInitialized = true;
      if (IS_TOUCH_DEVICE) {
        setupPinchZoom(document.getElementById("chart"));
      }
    } else {
      Plotly.react("chart", traces, CHART_LAYOUT, plotlyConfig);
    }
  } catch (err) {
    setStatus(false);
    errorBanner.textContent = `Could not load gauge data: ${err.message}`;
    errorBanner.classList.add("visible");
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
