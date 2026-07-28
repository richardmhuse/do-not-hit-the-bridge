"""
Generate a multi-step forecast past "now" using the trained XGBoost model.
Writes data/processed/forecast.csv  (and a JSON sidecar the API can serve).
"""
from pathlib import Path
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import xgboost as xgb

from config import DATA_PROCESSED

FEATURES_PATH = DATA_PROCESSED / "features.csv"
MODEL_PATH = DATA_PROCESSED / "model" / "xgb_model.json"
META_PATH = DATA_PROCESSED / "model" / "model_meta.json"
FORECAST_CSV = DATA_PROCESSED / "forecast.csv"
FORECAST_JSON = DATA_PROCESSED / "forecast.json"

# How far ahead to forecast (hours) and the step size
HORIZON_HOURS = 12
STEP_HOURS = 1


def load_model_and_meta():
    if not MODEL_PATH.exists() or not META_PATH.exists():
        raise FileNotFoundError("Model not found – run train_xgboost.py first")
    model = xgb.XGBRegressor()
    model.load_model(MODEL_PATH)
    with open(META_PATH) as f:
        meta = json.load(f)
    return model, meta


def recursive_forecast(
    model,
    feature_cols: list[str],
    history: pd.DataFrame,
    target: str,
    horizon_hours: int = HORIZON_HOURS,
    step_hours: int = STEP_HOURS,
) -> pd.DataFrame:
    """
    Recursive multi-step forecast.
    At each step we predict the next value, append it to the history,
    recompute the lag / rolling features that depend on the target,
    and repeat.
    """
    hist = history.copy().sort_index()
    last_time = hist.index.max()
    preds = []

    for step in range(1, int(horizon_hours / step_hours) + 1):
        # Build the feature vector for the next timestamp
        next_time = last_time + pd.Timedelta(hours=step_hours * step)

        # Start from the most recent real row and update time-based features
        row = hist.iloc[[-1]].copy()
        row.index = [next_time]

        # --- time features ---
        hour = next_time.hour + next_time.minute / 60.0
        row["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        row["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        doy = next_time.dayofyear
        row["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        row["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

        # --- lag features of the target ---
        # We keep a short series of the most recent target values
        # (real + previously predicted)
        target_series = hist[target].dropna()
        for lag in (1, 2, 3, 6, 12, 24):
            col = f"{target}_lag{lag}"
            if col in feature_cols:
                if len(target_series) >= lag:
                    row[col] = target_series.iloc[-lag]
                else:
                    row[col] = target_series.iloc[-1]  # fallback

        # --- simple rolling features (approximate with recent values) ---
        for window, suffix in [(3, "roll3_mean"), (6, "roll6_mean"), (12, "roll12_mean")]:
            col = f"{target}_{suffix}"
            if col in feature_cols:
                row[col] = target_series.iloc[-window:].mean() if len(target_series) >= 1 else target_series.iloc[-1]

        if f"{target}_roll6_std" in feature_cols:
            row[f"{target}_roll6_std"] = (
                target_series.iloc[-6:].std() if len(target_series) >= 2 else 0.0
            )

        # Tide residual lags if present
        if "tide_residual" in hist.columns:
            resid_series = hist["tide_residual"].dropna()
            for lag in (1, 2, 3, 6):
                col = f"tide_residual_lag{lag}"
                if col in feature_cols:
                    row[col] = resid_series.iloc[-lag] if len(resid_series) >= lag else resid_series.iloc[-1]

        # Ensure every required feature exists (fill missing with last known)
        for col in feature_cols:
            if col not in row.columns or pd.isna(row[col].iloc[0]):
                if col in hist.columns:
                    row[col] = hist[col].iloc[-1]
                else:
                    row[col] = 0.0

        # Predict
        X_next = row[feature_cols]
        y_hat = float(model.predict(X_next)[0])

        preds.append({"t": next_time, "predicted": y_hat})

        # Append the prediction so the next iteration can use it as a lag
        new_row = row.copy()
        new_row[target] = y_hat
        if "tide_ft" in hist.columns:
            # keep the last known tide value (or you can interpolate a tide forecast)
            new_row["tide_ft"] = hist["tide_ft"].iloc[-1]
            new_row["tide_residual"] = y_hat - new_row["tide_ft"]
        hist = pd.concat([hist, new_row])

    return pd.DataFrame(preds).set_index("t")


def main():
    print("Loading model…")
    model, meta = load_model_and_meta()
    target = meta["target"]
    feature_cols = meta["feature_cols"]

    print("Loading latest features…")
    df = pd.read_csv(FEATURES_PATH, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()

    # Use the most recent 48 h of history for the recursive step
    history = df.iloc[-48 * 4 :]  # generous buffer (assumes ~15-min or hourly)

    print(f"Generating {HORIZON_HOURS}h forecast (step={STEP_HOURS}h)…")
    forecast = recursive_forecast(
        model, feature_cols, history, target,
        horizon_hours=HORIZON_HOURS,
        step_hours=STEP_HOURS,
    )

    # Also keep the last real observation so the frontend can stitch smoothly
    last_obs_time = df.index.max()
    last_obs_value = df[target].iloc[-1]

    out = pd.DataFrame({
        "t": [last_obs_time] + list(forecast.index),
        "predicted": [last_obs_value] + list(forecast["predicted"]),
        "is_forecast": [False] + [True] * len(forecast),
    })

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_csv(FORECAST_CSV, index=False)

    # JSON sidecar that matches what the frontend already expects
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_hours": HORIZON_HOURS,
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
