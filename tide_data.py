"""
tide_data.py

Fetches the live measured-gauge CSV from the NOAA-Data-Pipeline-V2 repo
(raw.githubusercontent.com) and produces a smoothed series suitable for
plotting a real-time tidal / bridge-clearance chart.

No GitHub Action is involved on purpose: the web app fetches the CSV
directly at request time (with a short in-memory cache) so the dashboard
always reflects whatever is currently on the data repo's default branch.
"""

import io
import logging
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from scipy.signal import savgol_filter

logger = logging.getLogger(__name__)

# --- Configuration (override via environment variables on Render) ---------
#
# This reads from the PUBLIC mirror repo (do-not-hit-the-bridge), not the
# private NOAA-Data-Pipeline-V2 repo directly. A separate scheduled Action
# in do-not-hit-the-bridge copies measured.csv over from the private repo
# (see mirror-workflow/ alongside this app). That keeps this web app
# anonymous and token-free — it only ever talks to a public
# raw.githubusercontent.com URL.

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "richardmhuse")
DATA_REPO = os.environ.get("DATA_REPO", "do-not-hit-the-bridge")
DATA_BRANCH = os.environ.get("DATA_BRANCH", "main")
DATA_PATH = os.environ.get("DATA_PATH", "data/measured.csv")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
LOOKBACK_DAYS = float(os.environ.get("LOOKBACK_DAYS", "5"))

RAW_URL = (
    f"https://raw.githubusercontent.com/"
    f"{GITHUB_OWNER}/{DATA_REPO}/{DATA_BRANCH}/{DATA_PATH}"
)

_cache = {"timestamp": 0.0, "payload": None}

TIME_COL_HINTS = ("date", "time", "timestamp")
VALUE_COL_HINTS = ("level", "gauge", "stage", "value", "measurement", "ft", "feet", "water")


def _detect_columns(df: pd.DataFrame):
    """Best-effort detection of the timestamp and water-level columns,
    since the exact header names in measured.csv may evolve upstream."""
    time_col = None
    for col in df.columns:
        if any(hint in col.lower() for hint in TIME_COL_HINTS):
            time_col = col
            break

    value_col = None
    for col in df.columns:
        if col == time_col:
            continue
        if any(hint in col.lower() for hint in VALUE_COL_HINTS) and pd.api.types.is_numeric_dtype(df[col]):
            value_col = col
            break

    if value_col is None:
        numeric_cols = [c for c in df.columns if c != time_col and pd.api.types.is_numeric_dtype(df[c])]
        if numeric_cols:
            value_col = numeric_cols[0]

    if time_col is None or value_col is None:
        raise ValueError(f"Could not detect time/value columns. Columns present: {list(df.columns)}")

    return time_col, value_col


def _smooth(values: np.ndarray) -> np.ndarray:
    """Savitzky-Golay smoothing: preserves the shape of the tidal curve
    while denoising individual gauge readings. Falls back to a rolling
    mean for very short series."""
    n = len(values)
    if n < 5:
        return values

    window = min(51, n if n % 2 == 1 else n - 1)
    window = max(window, 5)
    if window % 2 == 0:
        window -= 1
    polyorder = 3 if window > 3 else 2

    try:
        return savgol_filter(values, window_length=window, polyorder=polyorder)
    except Exception:
        logger.warning("savgol_filter failed, falling back to rolling mean", exc_info=True)
        return pd.Series(values).rolling(window=5, center=True, min_periods=1).mean().to_numpy()


def fetch_tide_data(force: bool = False) -> dict:
    """Returns a dict with raw + smoothed series, using a short-lived
    in-memory cache so a burst of dashboard visitors doesn't hammer
    raw.githubusercontent.com."""
    now = time.time()
    if not force and _cache["payload"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        return _cache["payload"]

    resp = requests.get(RAW_URL, timeout=20)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    time_col, value_col = _detect_columns(df)

    df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    df = df.dropna(subset=[time_col, value_col])
    df = df.sort_values(time_col).drop_duplicates(subset=[time_col])

    if not df.empty:
        cutoff = df[time_col].max() - pd.Timedelta(days=LOOKBACK_DAYS)
        df = df[df[time_col] >= cutoff]

    if df.empty:
        raise ValueError("No usable rows found in measured.csv after filtering.")

    raw_values = df[value_col].to_numpy(dtype=float)
    smoothed_values = _smooth(raw_values)

    payload = {
        "timestamps": df[time_col].dt.strftime("%Y-%m-%dT%H:%M:%SZ").tolist(),
        "raw": raw_values.tolist(),
        "smoothed": smoothed_values.tolist(),
        "value_column": value_col,
        "time_column": time_col,
        "source_url": RAW_URL,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_value": float(smoothed_values[-1]),
        "latest_timestamp": df[time_col].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    _cache["timestamp"] = now
    _cache["payload"] = payload
    return payload
