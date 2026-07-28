"""
Train an XGBoost model on data/processed/features.csv
and save the model + metadata for later inference.
"""
from pathlib import Path
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error

from config import DATA_PROCESSED

FEATURES_PATH = DATA_PROCESSED / "features.csv"
MODEL_DIR = DATA_PROCESSED / "model"
MODEL_PATH = MODEL_DIR / "xgb_model.json"
META_PATH = MODEL_DIR / "model_meta.json"

# Columns that must never be used as features
EXCLUDE = {
    "measured_gauge_height_ft",
    "measured_water_level",
    "measured_Water Level",
    "measured_depth",
    "measured_value",
    "phase_name", "short_forecast", "wind_direction", "station",
}

TARGET_CANDIDATES = [
    "measured_water_level",
    "measured_Water Level",
    "measured_depth",
    "measured_value",
]


def find_target(df: pd.DataFrame) -> str:
    for c in TARGET_CANDIDATES:
        if c in df.columns:
            return c
    measured = [c for c in df.columns if c.startswith("measured_")]
    if not measured:
        raise ValueError("No measured_* column found")
    return measured[0]


def prepare_xy(df: pd.DataFrame, target: str):
    """Return X, y and the ordered list of feature names."""
    # Keep only numeric columns
    numeric = df.select_dtypes(include=[np.number]).copy()

    # Drop the target and any explicitly excluded columns
    drop_cols = [c for c in numeric.columns if c == target or c in EXCLUDE]
    feature_cols = [c for c in numeric.columns if c not in drop_cols]

    X = numeric[feature_cols]
    y = numeric[target]

    # Final safety: drop rows that still have NaNs in features or target
    mask = X.notna().all(axis=1) & y.notna()
    return X.loc[mask], y.loc[mask], feature_cols


def main(
    test_days: int = 7,
    n_estimators: int = 400,
    max_depth: int = 6,
    learning_rate: float = 0.05,
):
    if not FEATURES_PATH.exists():
        raise FileNotFoundError(f"{FEATURES_PATH} not found – run build_features.py first")

    print("Loading features…")
    df = pd.read_csv(FEATURES_PATH, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()

    target = find_target(df)
    print(f"Target: {target}")

    X, y, feature_cols = prepare_xy(df, target)
    print(f"Usable rows: {len(X)} | features: {len(feature_cols)}")

    MIN_ROWS = 48
    if len(X) < MIN_ROWS:
        raise SystemExit(
            f"Not enough usable rows ({len(X)} < {MIN_ROWS}). "
            "Fetch more history or reduce lag requirements."
        )

    # Time-based split (last N days = test)
    cutoff = X.index.max() - pd.Timedelta(days=test_days)
    train_mask = X.index < cutoff
    test_mask = X.index >= cutoff

    X_train, y_train = X.loc[train_mask], y.loc[train_mask]
    X_test, y_test = X.loc[test_mask], y.loc[test_mask]

    print(f"Train: {len(X_train)} rows  |  Test: {len(X_test)} rows")

    model = xgb.XGBRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        n_jobs=-1,
        random_state=42,
    )

    print("Training…")
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Evaluation
    pred_test = model.predict(X_test)
    mae = mean_absolute_error(y_test, pred_test)
    rmse = mean_squared_error(y_test, pred_test, squared=False)
    print(f"\nHold-out performance (last {test_days} days)")
    print(f"  MAE  : {mae:.4f} ft")
    print(f"  RMSE : {rmse:.4f} ft")

    # Feature importance (top 15)
    importance = (
        pd.Series(model.feature_importances_, index=feature_cols)
        .sort_values(ascending=False)
        .head(15)
    )
    print("\nTop feature importances:")
    print(importance.to_string())

    # Persist
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "feature_cols": feature_cols,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "mae": float(mae),
        "rmse": float(rmse),
        "test_days": test_days,
        "xgb_params": {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
        },
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nModel saved → {MODEL_PATH}")
    print(f"Meta  saved → {META_PATH}")


if __name__ == "__main__":
    main()
