"""
Generate a multi-step forecast past "now" using the trained XGBoost model.

Supports two modes (auto-detected from model_meta.json):
  - target == "tide_residual"  → residual model + blend with astronomical tide
  - otherwise                  → absolute-level model (legacy behaviour)

Writes:
  data/processed/forecast.csv
  data/processed/forecast.json
"""
from pathlib import Path
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb

from config import DATA_PROCESSED, DATA_RAW

FEATURES_PATH = DATA_PROCESSED / "features.csv"
MODEL_PATH = DATA_PROCESSED / "model" / "xgb_model.json"
META_PATH = DATA_PROCESSED / "model" / "model_meta.json"
FORECAST_CSV = DATA_PROCESSED / "forecast.csv"
FORECAST_JSON = DATA_PROCESSED / "forecast.json"
TIDES_PATH = DATA_RAW / "tides.csv"

HORIZON_HOURS = 12
STEP_HOURS = 1
BLEND_ALPHA = 0.75   # 1.0 = pure residual model, 0.0 = pure astronomical tide


def load_model_and_meta():
    if not MODEL_PATH.exists() or not META_PATH.exists():
        raise FileNotFoundError("Model not found – run train_xgboost.py first")
    model = xgb.XGBRegressor()
    model.load_model(MODEL_PATH)
    with open(META_PATH) as f:
        meta = json.load(f)
    return model, meta


def load_future_tide(start_time: pd.Timestamp, hours: int = 24) -> pd.Series:
    """Return tide_ft series covering [start_time, start_time + hours]."""
    if not TIDES_PATH.exists():
        return pd.Series(dtype=float)

    tides = pd.read_csv(TIDES_PATH, parse_dates=["t"])
    if tides["t"].dt.tz is None:
        tides["t"] = pd.to_datetime(tides["t"], utc=True)
    else:
        tides["t"] = tides["t"].dt.tz_convert("UTC")

    tides = tides.set_index("t").sort_index()
    if "tide_ft" not in tides.columns:
        # older files sometimes used "v"
        if "v" in tides.columns:
            tides = tides.rename(columns={"v": "tide_ft"})
        else:
            return pd.Series(dtype=float)

    end = start_time + pd.Timedelta(hours=hours)
    window = tides.loc[start_time - pd.Timedelta(hours=1) : end, "tide_ft"]
    return window.astype(float)


def tide_at(series: pd.Series, ts: pd.Timestamp, fallback: float) -> float:
    """Nearest / asof tide value; falls back to last known if series is empty."""
    if series.empty:
        return fallback
    s = series.copy()
    if ts not in s.index:
        s.loc[ts] = np.nan
        s = s.sort_index().ffill().bfill()
    val = s.asof(ts)
    if pd.isna(val):
        return fallback
    return float(val)


def recursive_forecast(
    model,
    feature_cols: list[str],
    history: pd.DataFrame,
    target: str,
    future_tides: pd.Series,
    horizon_hours: int = HORIZON_HOURS,
    step_hours: int = STEP_HOURS,
    blend_alpha: float = BLEND_ALPHA,
) -> pd.DataFrame:
    """
    Recursive multi-step forecast.
    If target is "tide_residual", predictions are converted to water level:
        level = tide + blend_alpha * residual_hat
    """
    hist = history.copy().sort_index()
    last_time = hist.index.max()
    last_tide_fallback = (
        float(hist["tide_ft"].dropna().iloc[-1])
        if "tide_ft" in hist.columns and hist["tide_ft"].notna().any()
        else 0.0
    )
    is_residual = target == "tide_residual"
    preds = []

    for step in range(1, int(horizon_hours / step_hours) + 1):
        next_time = last_time + pd.Timedelta(hours=step_hours * step)

        row = hist.iloc[[-1]].copy()
        row.index = [next_time]

        # --- time features ---
        hour = next_time.hour + next_time.minute / 60.0
        row["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        row["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        doy = next_time.dayofyear
        row["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        row["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

        # --- lag / rolling features of the training target ---
        target_series = hist[target].dropna()
        for lag in (1, 2, 3, 6, 12, 24, 48):
            col = f"{target}_lag{lag}"
            if col in feature_cols:
                row[col] = (
                    target_series.iloc[-lag]
                    if len(target_series) >= lag
                    else target_series.iloc[-1]
                )

        for window, suffix in [(3, "roll3_mean"), (6, "roll6_mean"), (12, "roll12_mean")]:
            col = f"{target}_{suffix}"
            if col in feature_cols:
                row[col] = (
                    target_series.iloc[-window:].mean()
                    if len(target_series) >= 1
                    else target_series.iloc[-1]
                )

        if f"{target}_roll6_std" in feature_cols:
            row[f"{target}_roll6_std"] = (
                target_series.iloc[-6:].std() if len(target_series) >= 2 else 0.0
            )

        # Extra residual lags (in case target is absolute but residual lags exist)
        if "tide_residual" in hist.columns:
            resid_series = hist["tide_residual"].dropna()
            for lag in (1, 2, 3, 6, 12, 24):
                col = f"tide_residual_lag{lag}"
                if col in feature_cols:
                    row[col] = (
                        resid_series.iloc[-lag]
                        if len(resid_series) >= lag
                        else resid_series.iloc[-1]
                    )

        # Future tide value at this step
        tide_val = tide_at(future_tides, next_time, last_tide_fallback)
        row["tide_ft"] = tide_val

        # Fill any remaining required features
        for col in feature_cols:
            if col not in row.columns or pd.isna(row[col].iloc[0]):
                if col in hist.columns and hist[col].notna().any():
                    row[col] = hist[col].dropna().iloc[-1]
                else:
                    row[col] = 0.0

        # Predict (residual or absolute, depending on how the model was trained)
        X_next = row[feature_cols]
        y_hat = float(model.predict(X_next)[0])

        if is_residual:
            # residual → water level, with blend toward pure tide
            level_hat = tide_val + blend_alpha * y_hat
            residual_hat = y_hat
        else:
            level_hat = y_hat
            residual_hat = level_hat - tide_val

        preds.append({"t": next_time, "predicted": level_hat})

        # Append to history so the next step can use updated lags
        new_row = row.copy()
        new_row[target] = residual_hat if is_residual else level_hat
        new_row["tide_ft"] = tide_val
        new_row["tide_residual"] = residual_hat
        if "measured_gauge_height_ft" in hist.columns:
            new_row["measured_gauge_height_ft"] = level_hat
        hist = pd.concat([hist, new_row])

    return pd.DataFrame(preds).set_index("t")


def main():
    print("Loading model…")
    model, meta = load_model_and_meta()
    target = meta["target"]
    feature_cols = meta["feature_cols"]
    is_residual = target == "tide_residual"
    print(f"Model target: {target}  |  residual mode: {is_residual}  |  blend α={BLEND_ALPHA}")

    print("Loading latest features…")
    df = pd.read_csv(FEATURES_PATH, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()

    # History buffer for recursive lags
    history = df.iloc[-48 * 12 :]   # ~48 h of 5-min data

    last_obs_time = df.index.max()

    # Stitch point on the chart must be absolute water level, not residual
    if "measured_gauge_height_ft" in df.columns:
        last_obs_value = float(df["measured_gauge_height_ft"].iloc[-1])
    elif not is_residual:
        last_obs_value = float(df[target].iloc[-1])
    else:
        # residual model but no absolute column — reconstruct
        last_obs_value = float(df["tide_ft"].iloc[-1] + df[target].iloc[-1])

    future_tides = load_future_tide(last_obs_time, hours=HORIZON_HOURS + 6)
    print(f"Future tide points available: {len(future_tides)}")

    print(f"Generating {HORIZON_HOURS}h forecast (step={STEP_HOURS}h)…")
    forecast = recursive_forecast(
        model,
        feature_cols,
        history,
        target,
        future_tides,
        horizon_hours=HORIZON_HOURS,
        step_hours=STEP_HOURS,
        blend_alpha=BLEND_ALPHA,
    )

    out = pd.DataFrame({
        "t": [last_obs_time] + list(forecast.index),
        "predicted": [last_obs_value] + list(forecast["predicted"]),
        "is_forecast": [False] + [True] * len(forecast),
    })

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_csv(FORECAST_CSV, index=False)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_hours": HORIZON_HOURS,
        "blend_alpha": BLEND_ALPHA,
        "model_target": target,
        "predicted_timestamps": [t.isoformat() for t in out["t"]],
        "predicted_values": [float(v) for v in out["predicted"]],
        "model_mae": meta.get("mae"),
        "model_rmse": meta.get("rmse"),
    }
    with open(FORECAST_JSON, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Forecast written → {FORECAST_CSV}")
    print(f"JSON sidecar   → {FORECAST_JSON}")
    print(f"Points: {len(out)} (1 observed + {len(forecast)} forecast)")


if __name__ == "__main__":
    main()
