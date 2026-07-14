import requests
import pandas as pd
from pathlib import Path

OUTPUT_PATH = Path("data/raw/tides.csv")

def fetch_tides(station_id, begin_date, end_date):
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    params = {
        "begin_date": begin_date,
        "end_date": end_date,
        "station": station_id,
        "product": "predictions",
        "datum": "MLLW",
        "time_zone": "lst_ldt",
        "interval": "h",
        "units": "english",
        "application": "tide_tracker",
        "format": "json"
    }
    response = requests.get(url, params=params)
    df = pd.DataFrame(response.json()["predictions"])
    df["t"] = pd.to_datetime(df["t"])
    return df

if __name__ == "__main__":
    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)

    new_data = fetch_tides(
        station_id="8658163",
        begin_date=yesterday.strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d")
    )

    if OUTPUT_PATH.exists():
        existing = pd.read_csv(OUTPUT_PATH, parse_dates=["t"])
        combined = pd.concat([existing, new_data]).drop_duplicates(subset=["t"])
    else:
        combined = new_data

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUTPUT_PATH, index=False)
    print(f"Tides saved: {len(new_data)} new rows")
