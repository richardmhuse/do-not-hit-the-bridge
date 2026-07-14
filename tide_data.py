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

# Selectable lookback/lookahead windows, surfaced as buttons in the UI.
ALLOWED_LOOKBACK_DAYS = (1, 3, 5)
ALLOWED_LOOKAHEAD_DAYS = (1, 3, 5)
DEFAULT_LOOKBACK_DAYS = 1
DEFAULT_LOOKAHEAD_DAYS = 1

# How much raw history to keep cached in memory (must cover the largest
# lookback option). Switching the lookback selector re-slices this cached
# frame instead of re-fetching from GitHub each time.
CACHE_WINDOW_DAYS = float(os.environ.get("CACHE_WINDOW_DAYS", str(max(ALLOWED_LOOKBACK_DAYS))))

RAW_URL = (
    f"https://raw.githubusercontent.com/"
    f"{GITHUB_OWNER}/{DATA_REPO}/{DATA_BRANCH}/{DATA_PATH}"
)

_cache = {"timestamp": 0.0, "df": None, "time_col": None, "value_col": None}

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


def _fetch_raw(force: bool = False):
    """Fetches + caches the raw dataframe (up to CACHE_WINDOW_DAYS of
    history). This is the only function that hits the network."""
    now = time.time()
    if not force and _cache["df"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        return _cache["df"], _cache["time_col"], _cache["value_col"]

    resp = requests.get(RAW_URL, timeout=20)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    time_col, value_col = _detect_columns(df)

    df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    df = df.dropna(subset=[time_col, value_col])
    df = df.sort_values(time_col).drop_duplicates(subset=[time_col])

    if not df.empty:
        cutoff = df[time_col].max() - pd.Timedelta(days=CACHE_WINDOW_DAYS)
        df = df[df[time_col] >= cutoff]

    if df.empty:
        raise ValueError("No usable rows found in measured.csv after filtering.")

    _cache.update(timestamp=now, df=df, time_col=time_col, value_col=value_col)
    return df, time_col, value_col


def fetch_tide_data(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    force: bool = False,
) -> dict:
    """Returns a dict with raw + smoothed series for the requested lookback
    window. lookahead_days isn't used to fetch anything yet (there's no
    forecast source wired up) — it's passed straight through so the
    frontend can reserve visual space for predictive analytics later."""
    if lookback_days not in ALLOWED_LOOKBACK_DAYS:
        lookback_days = DEFAULT_LOOKBACK_DAYS
    if lookahead_days not in ALLOWED_LOOKAHEAD_DAYS:
        lookahead_days = DEFAULT_LOOKAHEAD_DAYS

    df, time_col, value_col = _fetch_raw(force=force)

    cutoff = df[time_col].max() - pd.Timedelta(days=lookback_days)
    view = df[df[time_col] >= cutoff]
    if view.empty:
        view = df

    raw_values = view[value_col].to_numpy(dtype=float)
    smoothed_values = _smooth(raw_values)

    payload = {
        "timestamps": view[time_col].dt.strftime("%Y-%m-%dT%H:%M:%SZ").tolist(),
        "raw": raw_values.tolist(),
        "smoothed": smoothed_values.tolist(),
        "value_column": value_col,
        "time_column": time_col,
        "source_url": RAW_URL,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_value": float(smoothed_values[-1]),
        "latest_timestamp": view[time_col].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_days": lookback_days,
        "lookahead_days": lookahead_days,
        "forecast": [],  # reserved for predictive analytics
    }

    return payload
