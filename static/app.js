const REFRESH_MS = window.__REFRESH_MS__ || 60000;
const BRIDGE_CLEARANCE_FT =
  typeof window.__BRIDGE_CLEARANCE_FT__ === "number" ? window.__BRIDGE_CLEARANCE_FT__ : 4.81;
const MIN_WATER_DEPTH_FT =
  typeof window.__MIN_WATER_DEPTH_FT__ === "number" ? window.__MIN_WATER_DEPTH_FT__ : 1.86;
const WARNING_MARGIN_FT = 0.2; // tint the readout within this margin of either threshold

// On touch devices, use pinch-to-zoom + single-finger pan instead of the
// desktop rectangular drag-to-zoom (which is awkward with a finger).
const IS_TOUCH_DEVICE = "ontouchstart" in window || navigator.maxTouchPoints > 0;

// Plotly's date axis has no concept of timezones — it renders whatever
// calendar values it's given, literally, with no conversion of its own.
// The API sends true UTC ("...Z") timestamps. Without this, the whole
// chart (axis ticks, the predicted line, the "now" marker) renders in
// UTC rather than the viewer's own local time — which is what was
// making the "now" marker (and everything else) look hours off.
// Auto-detects the viewer's browser/OS timezone (DST-aware).
const DISPLAY_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

const INITIAL_VIEW_DAYS =
  typeof window.__INITIAL_VIEW_DAYS__ === "number" ? window.__INITIAL_VIEW_DAYS__ : 1.5;
const INITIAL_VIEW_END_PADDING_HOURS = 3; // breathing room past "now" so the marker isn't flush against the edge

const VIEWER_PARTS_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toViewerPlotTimestamp(utcIso) {
  const d = new Date(utcIso);
  const parts = {};
  for (const part of VIEWER_PARTS_FORMATTER.formatToParts(d)) {
    parts[part.type] = part.value;
  }
  // hour12:false can render midnight as "24" in some engines — normalize it
  const hour = parts.hour === "24" ? "00" : parts.hour;
  return `${parts.year}-${parts.month}-${parts.day}T${hour}:${parts.minute}:${parts.second}`;
}

function toViewerPlotTimestamps(utcIsoArray) {
  return utcIsoArray.map(toViewerPlotTimestamp);
}

/* ------------------------------------------------------------------ */
/*  User threshold preferences (localStorage)                          */
/* ------------------------------------------------------------------ */
const PREFS_KEY = "whiskey-creek-thresholds";

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function savePrefs(prefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
}

function getEffectiveThresholds() {
  const prefs = loadPrefs();
  return {
    showMax: prefs?.showMax ?? true,
    showMin: prefs?.showMin ?? true,
    minValue: typeof prefs?.minValue === "number" ? prefs.minValue : MIN_WATER_DEPTH_FT,
    maxValue: BRIDGE_CLEARANCE_FT, // still the fixed bridge clearance
  };
}

function buildThresholdShapesAndAnnotations() {
  const { showMax, showMin, minValue, maxValue } = getEffectiveThresholds();
  const shapes = [];
  const annotations = [];

  if (showMax) {
    shapes.push({
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: maxValue,
      y1: maxValue,
      line: {
        color: "rgba(255,179,64,.82)",
        width: 1.8,
      },
    });
    annotations.push({
      xref: "paper",
      x: 1,
      xanchor: "right",
      yref: "y",
      y: maxValue,
      yshift: 14,
      showarrow: false,
      align: "right",
      text: `<b>Bridge Clearance</b><br>${maxValue.toFixed(2)} ft`,
      font: {
        family: '-apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif',
        size: 12,
        color: "#C97A00",
      },
      bgcolor: "rgba(255,255,255,.75)",
      borderpad: 4,
    });
  }

  if (showMin) {
    shapes.push({
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: minValue,
      y1: minValue,
      line: {
        color: "rgba(255,105,97,.82)",
        width: 1.8,
      },
    });
    annotations.push({
      xref: "paper",
      x: 1,
      xanchor: "right",
      yref: "y",
      y: minValue,
      yshift: -14,
      showarrow: false,
      align: "right",
      text: `<b>Minimum Depth</b><br>${minValue.toFixed(2)} ft`,
      font: {
        family: '-apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif',
        size: 12,
        color: "#D94B43",
      },
      bgcolor: "rgba(255,255,255,.75)",
      borderpad: 4,
    });
  }

  return { shapes, annotations };
}

/* ------------------------------------------------------------------ */
/*  Settings panel (created automatically)                             */
/* ------------------------------------------------------------------ */
function createThresholdPanel() {
  // Avoid duplicating the panel if the script is ever re-run
  if (document.getElementById("threshold-panel")) return;

  const panel = document.createElement("div");
  panel.id = "threshold-panel";
  panel.innerHTML = `
    <button id="threshold-toggle" type="button" aria-expanded="false" title="Threshold settings">
      ⚙ Thresholds
    </button>
    <div id="threshold-controls" hidden>
      <label class="th-row">
        <input type="checkbox" id="show-max" checked>
        <span>Show maximum (Bridge Clearance)</span>
      </label>
      <label class="th-row">
        <input type="checkbox" id="show-min" checked>
        <span>Show minimum threshold</span>
      </label>
      <label class="th-row">
        <span>Min value (ft)</span>
        <input type="number" id="min-value" step="0.01" min="0" value="${MIN_WATER_DEPTH_FT}">
      </label>
    </div>
  `;

  // Minimal styling so it looks decent on most dashboards
  const style = document.createElement("style");
  style.textContent = `
    #threshold-panel {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 1000;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
      font-size: 13px;
    }
    #threshold-toggle {
      background: rgba(255,255,255,0.92);
      border: 1px solid rgba(0,0,0,0.12);
      border-radius: 10px;
      padding: 8px 14px;
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
      font-size: 13px;
      color: #1d1d1f;
    }
    #threshold-toggle:hover {
      background: #fff;
    }
    #threshold-controls {
      margin-top: 8px;
      background: rgba(255,255,255,0.96);
      border: 1px solid rgba(0,0,0,0.1);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.1);
      min-width: 240px;
    }
    .th-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
      cursor: pointer;
      color: #1d1d1f;
    }
    .th-row:last-child {
      margin-bottom: 0;
    }
    #min-value {
      width: 72px;
      padding: 4px 6px;
      border: 1px solid rgba(0,0,0,0.15);
      border-radius: 6px;
      font-size: 13px;
    }
    #min-value:disabled {
      opacity: 0.45;
    }
  `;
  document.head.appendChild(style);
  document.body.appendChild(panel);

  // Toggle open/close
  const toggleBtn = document.getElementById("threshold-toggle");
  const controls = document.getElementById("threshold-controls");
  toggleBtn.addEventListener("click", () => {
    const isHidden = controls.hidden;
    controls.hidden = !isHidden;
    toggleBtn.setAttribute("aria-expanded", String(isHidden));
  });
}

function initThresholdControls() {
  createThresholdPanel();

  const prefs = getEffectiveThresholds();
  const showMaxEl = document.getElementById("show-max");
  const showMinEl = document.getElementById("show-min");
  const minValueEl = document.getElementById("min-value");

  showMaxEl.checked = prefs.showMax;
  showMinEl.checked = prefs.showMin;
  minValueEl.value = prefs.minValue;
  minValueEl.disabled = !prefs.showMin;

  function onChange() {
    const showMax = showMaxEl.checked;
    const showMin = showMinEl.checked;
    let minValue = parseFloat(minValueEl.value);
    if (Number.isNaN(minValue) || minValue < 0) minValue = MIN_WATER_DEPTH_FT;

    minValueEl.disabled = !showMin;
    savePrefs({ showMax, showMin, minValue });
    applyThresholds();
  }

  showMaxEl.addEventListener("change", onChange);
  showMinEl.addEventListener("change", onChange);
  minValueEl.addEventListener("change", onChange);
  minValueEl.addEventListener("input", () => {
    // live update while typing is optional; we keep it on change for less noise
  });
}

function applyThresholds() {
  if (!chartInitialized) return;
  const { shapes, annotations } = buildThresholdShapesAndAnnotations();
  Plotly.relayout("chart", { shapes, annotations });
}

/* ------------------------------------------------------------------ */
/*  Original chart plumbing (slightly adapted)                         */
/* ------------------------------------------------------------------ */
const dot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const readout = document.getElementById("readout");
const readoutMeta = document.getElementById("readout-meta");
const errorBanner = document.getElementById("error-banner");

const CHART_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  margin: {
    l: 58,
    r: 30,
    t: 28,
    b: 54,
  },
  font: {
    family: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif',
    size: 13,
    color: "#1d1d1f",
  },
  showlegend: false,
  hovermode: "x unified",
  hoverlabel: {
    bgcolor: "rgba(255,255,255,0.94)",
    bordercolor: "rgba(0,0,0,.08)",
    font: {
      family: '-apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif',
      size: 13,
      color: "#1d1d1f",
    },
  },
  dragmode: IS_TOUCH_DEVICE ? "pan" : "zoom",
  xaxis: {
    tickformat: "%b %-d\n%I %p",
    showgrid: true,
    gridcolor: "rgba(0,0,0,.03)",
    gridwidth: 1,
    zeroline: false,
    showline: false,
    ticks: "",
    ticklen: 0,
    tickfont: {
      size: 12,
      color: "#86868b",
    },
    showspikes: true,
    spikecolor: "rgba(0,122,255,.35)",
    spikemode: "across",
    spikethickness: 1,
    spikedash: "solid",
  },
  yaxis: {
    title: {
      text: "Water Level (ft)",
      font: {
        size: 13,
        color: "#6e6e73",
      },
    },
    showgrid: true,
    gridcolor: "rgba(0,0,0,.045)",
    gridwidth: 1,
    zeroline: false,
    showline: false,
    ticks: "",
    ticklen: 0,
    tickfont: {
      size: 12,
      color: "#86868b",
    },
  },
  // shapes & annotations are now supplied dynamically
  shapes: [],
  annotations: [],
  transition: {
    duration: 500,
    easing: "cubic-in-out",
  },
};

let chartInitialized = false;
let lastPayload = null;
let nowTraceIndex = null;

function computeInitialXRange(data) {
  const lastMs = Math.max(new Date(data.latest_timestamp).getTime(), Date.now());
  const endMs = lastMs + INITIAL_VIEW_END_PADDING_HOURS * 3600000;
  const startMs = endMs - INITIAL_VIEW_DAYS * 24 * 3600000;
  return [
    toViewerPlotTimestamp(new Date(startMs).toISOString()),
    toViewerPlotTimestamp(new Date(endMs).toISOString()),
  ];
}

function buildTraces(data) {
  const viewerTimestamps = toViewerPlotTimestamps(data.timestamps);
  const traces = [
    {
      x: viewerTimestamps,
      y: data.raw,
      mode: "markers",
      marker: { color: "rgba(124, 147, 168, 0.35)", size: 3 },
      hovertemplate:
        "<b>%{y:.2f} ft</b><br>" +
        "Measured<br>" +
        "%{x|%b %d, %I:%M %p}" +
        "<extra></extra>",
      name: "raw",
    },
    {
      x: viewerTimestamps,
      y: data.smoothed,
      mode: "lines",
      line: {
        color: "#0A84FF",
        width: 4,
        shape: "spline",
        smoothing: 0.65,
      },
      fill: "tozeroy",
      fillcolor: "rgba(10,132,255,.14)",
      name: "Water Level",
      hoverinfo: "skip",
      showlegend: false,
    },
  ];

  const hasPrediction = data.predicted_timestamps && data.predicted_timestamps.length > 1;
  if (hasPrediction) {
    traces.push({
      x: toViewerPlotTimestamps(data.predicted_timestamps).slice(1),
      y: data.predicted_values.slice(1),
      mode: "lines",
      line: {
        color: "#0A84FF",
        width: 4,
        dash: "dot",
        shape: "spline",
      },
      opacity: 0.55,
      name: "predicted",
      hovertemplate:
        "<b>%{y:.2f} ft</b><br>" +
        "Predicted<br>" +
        "%{x|%b %d, %I:%M %p}" +
        "<extra></extra>",
    });
  }

  // "now" marker
  const seed = interpolateNowValue(data, Date.now());
  traces.push({
    x: [toViewerPlotTimestamp(seed.utcIso)],
    y: [seed.value],
    mode: "markers+text",
    text: ["Now"],
    textposition: "top center",
    textfont: {
      family: "-apple-system",
      size: 11,
      color: "#0A84FF",
    },
    marker: {
      size: 13,
      color: "#FFFFFF",
      line: {
        color: "#0A84FF",
        width: 3,
      },
    },
    hoverinfo: "skip",
    showlegend: false,
  });

  return traces;
}

/**
 * Finds where "now" sits along the predicted segment.
 */
function interpolateNowValue(payload, nowMs) {
  const hasPrediction = payload.predicted_timestamps && payload.predicted_timestamps.length > 1;
  if (!hasPrediction) {
    return { utcIso: payload.latest_timestamp, value: payload.latest_value };
  }
  const times = payload.predicted_timestamps.map((t) => new Date(t).getTime());
  const values = payload.predicted_values;
  if (nowMs <= times[0]) {
    return { utcIso: payload.predicted_timestamps[0], value: values[0] };
  }
  if (nowMs >= times[times.length - 1]) {
    return {
      utcIso: payload.predicted_timestamps[times.length - 1],
      value: values[times.length - 1],
    };
  }
  for (let i = 0; i < times.length - 1; i++) {
    if (nowMs >= times[i] && nowMs <= times[i + 1]) {
      const span = times[i + 1] - times[i];
      const frac = span === 0 ? 0 : (nowMs - times[i]) / span;
      return {
        utcIso: new Date(nowMs).toISOString(),
        value: values[i] + (values[i + 1] - values[i]) * frac,
      };
    }
  }
  return {
    utcIso: payload.predicted_timestamps[times.length - 1],
    value: values[times.length - 1],
  };
}

function setStatus(ok) {
  if (dot) dot.classList.toggle("stale", !ok);
  if (statusText) {
    statusText.textContent = ok
      ? "Live · Whiskey Creek"
      : "Feed unavailable — showing last known data";
  }
}

/**
 * Pinch-zoom workaround for mobile.
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
    if (!xa || !ya || typeof xa.p2d !== "function") return;

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

        const scale = pinchState.startDistance / newDistance;
        const MIN_SCALE = 0.05;
        const MAX_SCALE = 20;
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
    if (e.touches.length < 2) pinchState = null;
  }
  gd.addEventListener("touchend", endPinch, { capture: true });
  gd.addEventListener("touchcancel", endPinch, { capture: true });
}

function formatTimestamp(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    timeZone: DISPLAY_TIME_ZONE,
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

    if (errorBanner) errorBanner.classList.remove("visible");
    setStatus(true);

    if (readout) {
      readout.innerHTML = `${data.latest_value.toFixed(2)}<span class="unit">ft</span>`;
    }

    // Use the user's effective thresholds for the warning tint
    const { minValue, maxValue } = getEffectiveThresholds();
    const nearHigh = maxValue - data.latest_value <= WARNING_MARGIN_FT;
    const nearLow = data.latest_value - minValue <= WARNING_MARGIN_FT;
    if (readout) {
      readout.classList.toggle("near-limit-high", nearHigh && !nearLow);
      readout.classList.toggle("near-limit-low", nearLow);
    }

    if (readoutMeta) {
      readoutMeta.textContent = `As of ${formatTimestamp(data.latest_timestamp)} · fetched ${formatTimestamp(data.fetched_at)}`;
    }

    const traces = buildTraces(data);
    lastPayload = data;
    nowTraceIndex = traces.length - 1;

    const { shapes, annotations } = buildThresholdShapesAndAnnotations();
    const plotlyConfig = { displayModeBar: false, responsive: true, scrollZoom: false };

    if (!chartInitialized) {
      const initialLayout = {
        ...CHART_LAYOUT,
        shapes,
        annotations,
        xaxis: { ...CHART_LAYOUT.xaxis, range: computeInitialXRange(data) },
      };
      Plotly.newPlot("chart", traces, initialLayout, plotlyConfig);
      chartInitialized = true;
      if (IS_TOUCH_DEVICE) {
        setupPinchZoom(document.getElementById("chart"));
      }
    } else {
      const gd = document.getElementById("chart");
      const layout = {
        ...CHART_LAYOUT,
        shapes,
        annotations,
        xaxis: { ...CHART_LAYOUT.xaxis },
      };
      // Preserve whatever view the user is currently looking at
      if (gd.layout?.xaxis?.range) {
        layout.xaxis.range = [...gd.layout.xaxis.range];
      } else {
        layout.xaxis.range = computeInitialXRange(data);
      }
      Plotly.react(gd, traces, layout, plotlyConfig);
    }
  } catch (err) {
    setStatus(false);
    if (errorBanner) {
      errorBanner.textContent = `Could not load gauge data: ${err.message}`;
      errorBanner.classList.add("visible");
    }
  }
}

/**
 * Runs every second so the "now" marker keeps moving.
 */
function tickNowMarker() {
  if (!lastPayload || nowTraceIndex === null || !chartInitialized) return;
  const nowMs = Date.now();
  const { utcIso, value } = interpolateNowValue(lastPayload, nowMs);
  const displayX = toViewerPlotTimestamp(utcIso);
  Plotly.restyle("chart", { x: [[displayX]], y: [[value]] }, [nowTraceIndex]);
}

/* ------------------------------------------------------------------ */
/*  Boot                                                               */
/* ------------------------------------------------------------------ */
initThresholdControls();
refresh();
setInterval(refresh, REFRESH_MS);
setInterval(tickNowMarker, 1000);
