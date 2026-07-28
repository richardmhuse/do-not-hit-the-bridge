"""
tide_data.py

Loads measured gauge data and (when available) the XGBoost forecast,
then returns the payload the dashboard expects.

Priority for measured data:
  1. Local file  data/raw/measured.csv   (written by the pipeline / Cron)
  2. Public GitHub raw URL               (fallback so the site stays up)

Priority for the prediction line:
  1. data/processed/forecast.json        (XGBoost multi-step forecast)
  2. Lightweight harmonic gap-bridge     (original behaviour, only fills
                                          lag between last reading and now)
"""

import io
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.signal import savgol_filter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (override via environment variables on Render)
# ---------------------------------------------------------------------------
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "richardmhuse")
DATA_REPO = os.environ.get("DATA_REPO", "do-not-hit-the-bridge")
DATA_BRANCH = os.environ.get("DATA_BRANCH", "main")
DATA_PATH = os.environ.get("DATA_PATH", "data/measured.csv")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
HISTORY_DAYS = float(os.environ.get("HISTORY_DAYS", "30"))

SOURCE_DATA_TIMEZONE = os.environ.get("SOURCE_DATA_TIMEZONE", "America/New_York")

# Local paths used by the new pipeline
LOCAL_MEASURED_PATH = Path(os.environ.get("LOCAL_MEASURED_PATH", "data/raw/measured.csv"))
LOCAL_FORECAST_PATH = Path(os.environ.get("LOCAL_FORECAST_PATH", "data/processed/forecast.json"))

RAW_URL = (
    f"https://raw.githubusercontent.com/"
    f"{GITHUB_OWNER}/{DATA_REPO}/{DATA_BRANCH}/{DATA_PATH}"
)

_cache = {"timestamp": 0.0, "payload": None}

TIME_COL_HINTS = ("date", "time", "timestamp")
VALUE_COL_HINTS = ("level", "gauge", "stage", "value", "measurement", "ft", "feet", "water")

# --- Gap-bridging prediction (fallback only) --------------------------------
TIDAL_PERIOD_HOURS = 12.42
PREDICTION_MAX_POINTS = 40
MIN_POINTS_FOR_FIT = 12


def _detect_columns(df: pd.DataFrame):
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


def _predict_gap(view, time_col, smoothed_values, now_ts):
    """Original short harmonic bridge – only used when no ML forecast exists."""
    last_ts = view[time_col].iloc[-1]
    gap_seconds = (now_ts - last_ts).total_seconds()

    if gap_seconds <= 60 or len(view) < MIN_POINTS_FOR_FIT:
        return [], []

    basis = view[time_col].iloc[0]
    t_seconds = (view[time_col] - basis).dt.total_seconds().to_numpy()
    omega = 2 * math.pi / (TIDAL_PERIOD_HOURS * 3600)

    design = np.column_stack(
        [
            np.sin(omega * t_seconds),
            np.cos(omega * t_seconds),
            np.ones_like(t_seconds),
            t_seconds,
        ]
    )

    try:
        coeffs, *_ = np.linalg.lstsq(design, smoothed_values, rcond=None)
    except Exception:
        logger.warning("Harmonic fit failed, skipping gap prediction", exc_info=True)
        return [], []

    def fitted(t_sec):
        a, b, c, d = coeffs
        return a * math.sin(omega * t_sec) + b * math.cos(omega * t_sec) + c + d * t_sec

    t_last = (last_ts - basis).total_seconds()
    offset = float(smoothed_values[-1]) - fitted(t_last)

    n_points = max(2, min(PREDICTION_MAX_POINTS, int(gap_seconds // 60) + 2))
    pred_timestamps = pd.date_range(start=last_ts, end=now_ts, periods=n_points)
    pred_values = [fitted((ts - basis).total_seconds()) + offset for ts in pred_timestamps]

    return (
        pred_timestamps.strftime("%Y-%m-%dT%H:%M:%SZ").tolist(),
        [float(v) for v in pred_values],
    )


def _load_measured_csv() -> pd.DataFrame:
    """Prefer local pipeline file; fall back to public GitHub raw URL."""
    if LOCAL_MEASURED_PATH.exists():
        logger.info("Loading measured data from local file: %s", LOCAL_MEASURED_PATH)
        df = pd.read_csv(LOCAL_MEASURED_PATH)
        return df

    logger.info("Local measured.csv not found – fetching from GitHub: %s", RAW_URL)
    resp = requests.get(RAW_URL, timeout=20)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text))


def _load_ml_forecast() -> dict | None:
    """Return the XGBoost forecast payload if present and fresh enough."""
    if not LOCAL_FORECAST_PATH.exists():
        return None
    try:
        with open(LOCAL_FORECAST_PATH) as f:
            fc = json.load(f)
        # Basic sanity check
        if not fc.get("predicted_timestamps") or not fc.get("predicted_values"):
            return None
        return fc
    except Exception:
        logger.warning("Failed to read forecast.json", exc_info=True)
        return None


def fetch_tide_data(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["payload"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        return _cache["payload"]

    df = _load_measured_csv()
    time_col, value_col = _detect_columns(df)

    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    if df[time_col].dt.tz is None:
        df[time_col] = df[time_col].dt.tz_localize(
            SOURCE_DATA_TIMEZONE, ambiguous="NaT", nonexistent="NaT"
        )
    df[time_col] = df[time_col].dt.tz_convert("UTC")
    df = df.dropna(subset=[time_col, value_col])
    df = df.sort_values(time_col).drop_duplicates(subset=[time_col])

    if not df.empty:
        cutoff = df[time_col].max() - pd.Timedelta(days=HISTORY_DAYS)
        df = df[df[time_col] >= cutoff]

    if df.empty:
        raise ValueError("No usable rows found in measured data after filtering.")

    raw_values = df[value_col].to_numpy(dtype=float)
    smoothed_values = _smooth(raw_values)

    now_dt = datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now_dt)
    last_ts = df[time_col].iloc[-1]
    gap_minutes = max(0.0, (now_ts - last_ts).total_seconds() / 60)

    # ------------------------------------------------------------------
    # Prediction line: prefer XGBoost forecast, else short harmonic bridge
    # ------------------------------------------------------------------
    ml_forecast = _load_ml_forecast()
    if ml_forecast is not None:
        predicted_timestamps = ml_forecast["predicted_timestamps"]
        predicted_values = ml_forecast["predicted_values"]
        logger.info(
            "Using XGBoost forecast (%d points, generated %s)",
            len(predicted_timestamps),
            ml_forecast.get("generated_at", "?"),
        )
    else:
        predicted_timestamps, predicted_values = _predict_gap(
            df, time_col, smoothed_values, now_ts
        )
        logger.info("No ML forecast found – using harmonic gap bridge (%d points)", len(predicted_timestamps))

    payload = {
        "timestamps": df[time_col].dt.strftime("%Y-%m-%dT%H:%M:%SZ").tolist(),
        "raw": raw_values.tolist(),
        "smoothed": smoothed_values.tolist(),
        "predicted_timestamps": predicted_timestamps,
        "predicted_values": predicted_values,
        "gap_minutes": gap_minutes,
        "value_column": value_col,
        "time_column": time_col,
        "source_url": str(LOCAL_MEASURED_PATH) if LOCAL_MEASURED_PATH.exists() else RAW_URL,
        "fetched_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_value": float(smoothed_values[-1]),
        "latest_timestamp": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecast_source": "xgboost" if ml_forecast is not None else "harmonic_gap",
    }

    _cache["timestamp"] = now
    _cache["payload"] = payload
    return payload
