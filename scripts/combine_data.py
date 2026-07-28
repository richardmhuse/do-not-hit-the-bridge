"""
Combine tides, weather, rain, lunar (if present) and measured sensor data
into a single time-aligned dataset for modeling.
"""
import pandas as pd
from pathlib import Path

from config import DATA_RAW, DATA_PROCESSED

def load_csv(path: Path, time_col: str = "t") -> pd.DataFrame | None:
    if not path.exists():
        print(f"  ⚠ missing {path} – skipping")
        return None
    df = pd.read_csv(path, parse_dates=[time_col])
    if df[time_col].dt.tz is None:
        df[time_col] = pd.to_datetime(df[time_col], utc=True)
    else:
        df[time_col] = df[time_col].dt.tz_convert("UTC")
    return df.set_index(time_col).sort_index()


def main():
    print("Loading raw datasets...")
    tides   = load_csv(DATA_RAW / "tides.csv")
    weather = load_csv(DATA_RAW / "weather.csv")
    rain    = load_csv(DATA_RAW / "rain.csv")
    lunar   = load_csv(DATA_RAW / "lunar.csv")          # optional

    # measured.csv is long-format: timestamp, value, parameter, ...
    measured_path = DATA_RAW / "measured.csv"
    measured = None
    if measured_path.exists():
        raw = pd.read_csv(measured_path, parse_dates=["timestamp"])
        raw = raw.rename(columns={"timestamp": "t"})
        if raw["t"].dt.tz is None:
            raw["t"] = pd.to_datetime(raw["t"], utc=True)
        measured = (
            raw.pivot_table(index="t", columns="parameter", values="value", aggfunc="mean")
            .add_prefix("measured_")
            .sort_index()
        )
        print(f"  measured: {len(measured)} rows, columns = {list(measured.columns)}")
    else:
        print("  ⚠ measured.csv missing")

    # Start with tides as the backbone (regular hourly grid)
    if tides is None:
        raise FileNotFoundError("tides.csv is required as the time backbone")

    combined = tides.copy()

    for name, df in [("weather", weather), ("rain", rain), ("lunar", lunar)]:
        if df is not None:
            combined = combined.join(df, how="left")
            print(f"  joined {name}")

    # Nearest-neighbor join for measured (sensor may not be on exact hour)
    if measured is not None:
        combined = pd.merge_asof(
            combined.sort_index(),
            measured.sort_index(),
            left_index=True,
            right_index=True,
            direction="nearest",
            tolerance=pd.Timedelta("30min"),
        )
        print("  joined measured (asof 30 min)")

    # Clean-ups
    if "rain_inches" in combined.columns:
        combined["rain_inches"] = combined["rain_inches"].fillna(0.0)
        combined["rain_24h"] = combined["rain_inches"].rolling("24h", min_periods=1).sum()
        combined["rain_72h"] = combined["rain_inches"].rolling("72h", min_periods=1).sum()
        combined["rain_7d"]  = combined["rain_inches"].rolling("7d",  min_periods=1).sum()

    if lunar is not None:
        lunar_cols = [c for c in lunar.columns if c in combined.columns]
        combined[lunar_cols] = combined[lunar_cols].ffill()

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out = DATA_PROCESSED / "combined.csv"
    combined.to_csv(out)
    print(f"\nCombined dataset: {len(combined)} rows → {out}")
    print(f"Columns: {list(combined.columns)}")


if __name__ == "__main__":
    main()
